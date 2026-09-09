from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests

from .config import Config
from .store import canonicalize_url

LOGGER = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """A request could not be completed within the configured safety policy."""


class RobotsDisallowed(FetchError):
    pass


class SiteBlocked(FetchError):
    pass


@dataclass(frozen=True)
class FetchResult:
    url: str
    text: str
    status_code: int
    content_type: str
    from_cache: bool = False


class Fetcher:
    def __init__(self, config: Config, session: requests.Session | None = None):
        if "replace-with" in config.contact_email.lower():
            raise ValueError("Set contact_email in config.yaml to your real email before making network requests")
        self.config = config
        self.session = session or requests.Session()
        # Select once per run: stable identification, not per-request disguise.
        browser_ua = random.choice(config.user_agents)
        self.user_agent = f"{browser_ua} jobfilter/{__import__('jobfilter').__version__} (contact: {config.contact_email})"
        self.session.headers.update({"User-Agent": self.user_agent, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})
        self._robots: dict[str, RobotFileParser] = {}
        self._last_request_at: dict[str, float] = {}
        self._request_counts: dict[str, int] = {}
        self._blocked_hosts: dict[str, int] = {}

    def _origin(self, url: str) -> str:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise FetchError(f"Unsupported URL: {url}")
        return f"{parts.scheme.lower()}://{parts.netloc.lower()}"

    def _wait(self, host: str) -> None:
        count = self._request_counts.get(host, 0)
        if count and count % self.config.long_pause_every == 0:
            pause = random.uniform(self.config.long_pause_min, self.config.long_pause_max)
            LOGGER.info("Polite pause after %s requests to %s: %.1f seconds", count, host, pause)
            time.sleep(pause)
        last = self._last_request_at.get(host)
        if last is not None:
            desired = random.uniform(self.config.min_delay, self.config.max_delay)
            remaining = desired - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)

    def _send(self, url: str, *, robots_request: bool = False, **kwargs: object) -> requests.Response:
        host = urlsplit(url).netloc.lower()
        if not robots_request and host in self._blocked_hosts:
            status = self._blocked_hosts[host]
            raise SiteBlocked(
                f"{host} previously returned HTTP {status}; skipping this host for the remainder of the run"
            )
        attempts = self.config.max_retries + 1
        for attempt in range(attempts):
            self._wait(host)
            try:
                response = self.session.get(url, timeout=self.config.request_timeout, **kwargs)
            except requests.RequestException as exc:
                self._last_request_at[host] = time.monotonic()
                self._request_counts[host] = self._request_counts.get(host, 0) + 1
                if attempt + 1 >= attempts:
                    raise FetchError(f"Request failed for {url}: {exc}") from exc
                time.sleep((2**attempt) + random.uniform(0.25, 1.25))
                continue
            self._last_request_at[host] = time.monotonic()
            self._request_counts[host] = self._request_counts.get(host, 0) + 1
            # RFC 9309 section 2.3.1.3 defines 400-499 specifically for
            # /robots.txt as "Unavailable"; it does not imply a Disallow.
            # This exception applies only to the robots fetch. The same status
            # from an API/detail request remains a hard stop below.
            if robots_request and 400 <= response.status_code <= 499:
                return response
            if response.status_code in {429, 500, 502, 503, 504}:
                if attempt + 1 >= attempts:
                    raise FetchError(f"HTTP {response.status_code} after {attempts} attempts: {url}")
                retry_after = response.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.isdigit() else (2**attempt) + random.uniform(0.25, 1.25)
                time.sleep(delay)
                continue
            if response.status_code in {401, 403}:
                self._blocked_hosts[host] = response.status_code
                raise SiteBlocked(f"{urlsplit(url).netloc} returned HTTP {response.status_code}; stopping without bypass attempts")
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise FetchError(f"HTTP {response.status_code}: {url}") from exc
            return response
        raise AssertionError("unreachable")

    def _robot_parser(self, url: str) -> RobotFileParser:
        origin = self._origin(url)
        if origin in self._robots:
            return self._robots[origin]
        robots_url = f"{origin}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            response = self._send(robots_url, robots_request=True)
        except FetchError as exc:
            # Network and 5xx failures make robots.txt unreachable. RFC 9309
            # section 2.3.1.4 requires a conservative complete disallow.
            LOGGER.warning("Could not reach %s (%s); treating the host as disallowed", robots_url, exc)
            parser.disallow_all = True
        else:
            if 400 <= response.status_code <= 499:
                LOGGER.info("%s returned HTTP %s; robots.txt is unavailable", robots_url, response.status_code)
                parser.allow_all = True
            else:
                parser.parse(response.text.splitlines())
        self._robots[origin] = parser
        return parser

    def check_allowed(self, url: str) -> None:
        parser = self._robot_parser(url)
        if not parser.can_fetch(self.user_agent, url):
            raise RobotsDisallowed(f"robots.txt disallows jobfilter from fetching {url}")

    def _cache_paths(self, url: str, suffix: str) -> tuple[Path, Path]:
        canonical = canonicalize_url(url)
        host = urlsplit(canonical).hostname or "unknown-host"
        digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()
        directory = self.config.cache_dir / host
        return directory / f"{digest}{suffix}", directory / f"{digest}.meta.json"

    def get(self, url: str, *, cache_detail: bool = False, expect_json: bool = False, **kwargs: object) -> FetchResult:
        self.check_allowed(url)
        suffix = ".json" if expect_json else ".html"
        content_path, meta_path = self._cache_paths(url, suffix)
        if cache_detail and content_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
            return FetchResult(
                meta.get("final_url", url),
                content_path.read_text(encoding="utf-8"),
                int(meta.get("status_code", 200)),
                meta.get("content_type", "application/json" if expect_json else "text/html"),
                True,
            )
        response = self._send(url, **kwargs)
        result = FetchResult(
            response.url,
            response.text,
            response.status_code,
            response.headers.get("Content-Type", ""),
        )
        if cache_detail:
            content_path.parent.mkdir(parents=True, exist_ok=True)
            content_path.write_text(response.text, encoding="utf-8")
            meta_path.write_text(
                json.dumps(
                    {
                        "requested_url": url,
                        "final_url": response.url,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "status_code": response.status_code,
                        "content_type": result.content_type,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        return result
