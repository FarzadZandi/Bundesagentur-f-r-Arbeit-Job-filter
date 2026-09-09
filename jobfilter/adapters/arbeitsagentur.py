from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from jobfilter.fetch import FetchError, Fetcher, RobotsDisallowed, SiteBlocked

from .base import Adapter, JobAd, JobStub


LOGGER = logging.getLogger(__name__)
API_ORIGIN = "https://rest.arbeitsagentur.de"
API_PATH = "/jobboerse/jobsuche-service/pc/v6/jobs"
API_KEY = "jobboerse-jobsuche"  # Shared key used by the public Jobsuche website.
PAGE_SIZE = 25
DETAIL_BASE = "https://www.arbeitsagentur.de/jobsuche/jobdetail"
DATE_PATTERN = (
    r"\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{4}-\d{2}-\d{2}|"
    r"\d{1,2}\.\s*(?:Januar|Februar|März|April|Mai|Juni|Juli|August|"
    r"September|Oktober|November|Dezember)\s+\d{4}"
)


class AdapterParseError(ValueError):
    pass


class ArbeitsagenturAdapter(Adapter):
    name = "arbeitsagentur"
    host_patterns = ["arbeitsagentur.de"]

    def __init__(self) -> None:
        self.pages_walked = 0
        self.last_total_results: int | None = None
        self.used_html_fallback = False

    @staticmethod
    def _snapshot_time() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _api_url(self, search_url: str, page: int, snapshot: str) -> str:
        parts = urlsplit(search_url)
        if not parts.hostname or not parts.hostname.endswith("arbeitsagentur.de"):
            raise ValueError(f"Not an Arbeitsagentur URL: {search_url}")
        incoming = dict(parse_qsl(parts.query, keep_blank_values=True))
        ignored = {"page", "size", "aktualisiertVor", "facetten", "as", "pav"}
        params = {key: value for key, value in incoming.items() if key not in ignored}

        # The SPA's `zeitarbeit=true` URL toggle means "exclude temporary-work
        # agencies"; the backend expresses that selection as zeitarbeit=false.
        if incoming.get("zeitarbeit", "").lower() == "true":
            params["zeitarbeit"] = "false"
        params.update(
            {
                "page": str(page),
                "size": str(PAGE_SIZE),
                "aktualisiertVor": snapshot,
                "as": "true",
            }
        )
        return urlunsplit(("https", "rest.arbeitsagentur.de", API_PATH, urlencode(params), ""))

    @staticmethod
    def _location(item: Mapping[str, Any]) -> str:
        locations = item.get("stellenlokationen") or []
        names: list[str] = []
        for location in locations:
            address = location.get("adresse") or {}
            value = str(address.get("ort") or "").strip()
            if value and value not in names:
                names.append(value)
        return "; ".join(names)

    @classmethod
    def parse_listing_payload(cls, payload: str | Mapping[str, Any]) -> tuple[list[JobStub], int, int, int]:
        data = json.loads(payload) if isinstance(payload, str) else dict(payload)
        raw_items = data.get("ergebnisliste")
        if raw_items is None:
            # Defensive compatibility with older observed API variants.
            raw_items = data.get("stellenangebote")
        if not isinstance(raw_items, list):
            raise AdapterParseError("Arbeitsagentur JSON has no ergebnisliste array")
        stubs: list[JobStub] = []
        for item in raw_items:
            try:
                reference = str(item["referenznummer"]).strip()
                title = str(item["stellenangebotsTitel"]).strip()
                if not reference or not title:
                    raise KeyError("empty reference/title")
                posted = (
                    (item.get("veroeffentlichungszeitraum") or {}).get("von")
                    or item.get("datumErsteVeroeffentlichung")
                    or ""
                )
                stubs.append(
                    JobStub(
                        url=f"{DETAIL_BASE}/{reference}",
                        title=title,
                        employer=str(item.get("firma") or "").strip(),
                        location=cls._location(item),
                        posted_date=str(posted),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                LOGGER.warning("Skipping malformed Arbeitsagentur result: %s", exc)
        return (
            stubs,
            int(data.get("maxErgebnisse") or len(stubs)),
            int(data.get("page") or 1),
            int(data.get("size") or PAGE_SIZE),
        )

    def iter_listing(self, url: str, fetch: Fetcher, max_pages: int | None) -> Iterator[JobStub]:
        use_api = getattr(getattr(fetch, "config", None), "arbeitsagentur_use_api", True)
        if not use_api:
            LOGGER.info("Arbeitsagentur: using public HTML result pages")
            self.used_html_fallback = True
            yield from self._iter_html_fallback(url, fetch, max_pages)
            return

        snapshot = self._snapshot_time()
        seen_references: set[str] = set()
        try:
            requested_page = 1
            while max_pages is None or requested_page <= max_pages:
                api_url = self._api_url(url, requested_page, snapshot)
                response = fetch.get(
                    api_url,
                    expect_json=True,
                    headers={"X-API-Key": API_KEY, "Accept": "application/json"},
                )
                stubs, total, returned_page, returned_size = self.parse_listing_payload(response.text)
                self.pages_walked += 1
                self.last_total_results = total
                if not stubs:
                    break
                for stub in stubs:
                    reference = stub.url.rsplit("/", 1)[-1]
                    if reference not in seen_references:
                        seen_references.add(reference)
                        yield stub
                page_count = math.ceil(total / max(returned_size, 1))
                if returned_page >= page_count:
                    break
                requested_page += 1
        except RobotsDisallowed:
            raise
        except (FetchError, SiteBlocked, AdapterParseError, json.JSONDecodeError) as exc:
            LOGGER.warning("Arbeitsagentur API unavailable (%s); trying rendered HTML fallback", exc)
            self.used_html_fallback = True
            yield from self._iter_html_fallback(url, fetch, max_pages)

    @staticmethod
    def _text(container: Tag, selector: str) -> str:
        node = container.select_one(selector)
        return node.get_text(" ", strip=True) if node else ""

    @staticmethod
    def _html_page_url(url: str, page: int) -> str:
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["page"] = str(page)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))

    @staticmethod
    def _html_total(soup: BeautifulSoup) -> int | None:
        match = re.search(r"(\d{1,3}(?:\.\d{3})*)\s+Jobs?\s+für", soup.get_text(" ", strip=True), re.IGNORECASE)
        return int(match.group(1).replace(".", "")) if match else None

    def _iter_html_fallback(self, url: str, fetch: Fetcher, max_pages: int | None = 1) -> Iterator[JobStub]:
        seen_references: set[str] = set()
        page = 1
        while max_pages is None or page <= max_pages:
            response = fetch.get(self._html_page_url(url, page))
            soup = BeautifulSoup(response.text, "lxml")
            anchors = soup.select('a[id^="ergebnisliste-item-"][href*="/jobsuche/jobdetail/"]')
            if not anchors:
                if page == 1:
                    raise AdapterParseError("Rendered HTML contains no Arbeitsagentur result entries")
                break
            self.pages_walked += 1
            total = self._html_total(soup)
            if total is not None:
                self.last_total_results = total
            total_pages = math.ceil(total / PAGE_SIZE) if total is not None else None
            LOGGER.info(
                "Arbeitsagentur listing page %s/%s: %s jobs found on this page (live total: %s)",
                page,
                min(total_pages, max_pages) if total_pages is not None and max_pages is not None else (total_pages or "all"),
                len(anchors),
                total if total is not None else "unknown",
            )
            new_on_page = 0
            for anchor in anchors:
                container = anchor.find_parent("jb-job-listen-eintrag") or anchor.find_parent("li") or anchor.parent
                title = self._text(container, '[id^="eintrag-"][id$="-titel"]')
                if not title:
                    spans = anchor.select("h2 span")
                    title = spans[-1].get_text(" ", strip=True) if spans else ""
                    if " bei " in title:
                        title = title.rsplit(" bei ", 1)[0]
                title = re.sub(r"^\d+[.]\s*", "", title)
                employer = self._text(container, '[id^="eintrag-"][id$="-firma"]')
                location = self._text(container, '[id^="eintrag-"][id$="-arbeitsort"]')
                location = location.removeprefix("Arbeitsort:").strip()
                posted_node = container.select_one('[id^="eintrag-"][id$="-veroeffentlichungsdatum"]')
                posted = ""
                if posted_node:
                    posted = str(posted_node.get("title") or posted_node.get_text(" ", strip=True))
                    posted = posted.removeprefix("Veröffentlichungsdatum:").strip()
                href = str(anchor.get("href") or "").strip()
                reference = href.rstrip("/").rsplit("/", 1)[-1]
                if href and title and reference not in seen_references:
                    seen_references.add(reference)
                    new_on_page += 1
                    yield JobStub(href, title, employer, location, posted)
            if not new_on_page:
                break
            if total is not None and page >= math.ceil(total / PAGE_SIZE):
                break
            if total is None and len(anchors) < PAGE_SIZE:
                break
            page += 1

    def parse_detail(self, html_or_json: str, url: str) -> JobAd:
        soup = BeautifulSoup(html_or_json, "lxml")
        title = self._text(soup, "#detail-kopfbereich-titel")
        if not title:
            title = self._text(soup, "#detail-h1-heading")
        if not title:
            raise AdapterParseError(f"Arbeitsagentur detail has no title: {url}")
        employer = self._text(soup, "#detail-kopfbereich-firma")
        location = self._text(soup, "#detail-kopfbereich-arbeitsort")
        posted_node = soup.select_one("#detail-kopfbereich-veroeffentlichungsdatum")
        posted = ""
        if posted_node:
            posted = str(posted_node.get("title") or posted_node.get_text(" ", strip=True))
            posted = posted.removeprefix("Veröffentlichungsdatum:").strip()
        description_node = soup.select_one("#detail-beschreibung-text-container")
        full_text = description_node.get_text("\n", strip=True) if description_node else soup.get_text("\n", strip=True)
        external_node = soup.select_one("#detail-beschreibung-externe-url-btn[href]")
        external_url = urljoin(url, str(external_node.get("href") or "").strip()) if external_node else ""
        if external_url and urlsplit(external_url).scheme not in {"http", "https"}:
            external_url = ""
        deadline_match = re.search(
            rf"(?is)(?:bewerbungsfrist|bewerbungsschluss|bewerbungen|application\s+deadline|apply\s+by).{{0,180}}?({DATE_PATTERN})",
            full_text,
        )
        reference = self._text(soup, "#detail-footer-referenznummer") or url.rstrip("/").rsplit("/", 1)[-1]
        raw_meta = {
            "reference": reference,
            "employment_type": self._text(soup, "#detail-kopfbereich-anstellungsart"),
            "contract": self._text(soup, "#detail-kopfbereich-befristung"),
            "start": self._text(soup, "#detail-kopfbereich-eintrittsdatum-mit-datum"),
            "external_url": external_url,
        }
        return JobAd(
            url=url,
            title=title,
            employer=employer,
            location=location,
            posted_date=posted,
            deadline=deadline_match.group(1) if deadline_match else "",
            full_text=full_text,
            employment_type=raw_meta["employment_type"],
            raw_meta=raw_meta,
        )


def extract_external_job_text(html_or_json: str) -> str:
    """Extract the substantive description from an employer/partner job page."""
    soup = BeautifulSoup(html_or_json, "lxml")

    # Job boards commonly include the complete, clean description in JobPosting
    # JSON-LD even when the visible page is heavily scripted.
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(node.string or node.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        pending = payload if isinstance(payload, list) else [payload]
        while pending:
            item = pending.pop()
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                pending.extend(graph)
            item_type = item.get("@type", "")
            types = item_type if isinstance(item_type, list) else [item_type]
            description = item.get("description")
            if "JobPosting" in types and isinstance(description, str):
                text = BeautifulSoup(description, "lxml").get_text("\n", strip=True)
                if len(text) >= 200:
                    return text

    for node in soup.select("script, style, noscript, nav, header, footer, form, svg"):
        node.decompose()
    container = soup.select_one("main, article, [itemprop='description'], body") or soup
    return container.get_text("\n", strip=True)
