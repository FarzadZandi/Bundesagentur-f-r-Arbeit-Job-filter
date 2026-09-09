from pathlib import Path

import pytest

from jobfilter.config import Config
from jobfilter.fetch import Fetcher, RobotsDisallowed, SiteBlocked


class Response:
    def __init__(self, url, text, status=200, content_type="text/html"):
        self.url = url
        self.text = text
        self.status_code = status
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class Session:
    def __init__(self, responses):
        self.headers = {}
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return next(self.responses)


def config(tmp_path: Path) -> Config:
    return Config(
        contact_email="owner@example.com",
        cache_dir=tmp_path / "cache",
        database_path=tmp_path / "seen.sqlite",
        min_delay=2.5,
        max_delay=2.5,
        max_retries=0,
    )


def test_robots_checked_then_detail_cached(tmp_path, monkeypatch):
    monkeypatch.setattr("jobfilter.fetch.time.sleep", lambda _: None)
    session = Session(
        [
            Response("https://example.com/robots.txt", "User-agent: *\nAllow: /jobs/"),
            Response("https://example.com/jobs/1", "<html>job</html>"),
        ]
    )
    fetcher = Fetcher(config(tmp_path), session)
    first = fetcher.get("https://example.com/jobs/1", cache_detail=True)
    second = fetcher.get("https://example.com/jobs/1", cache_detail=True)
    assert not first.from_cache
    assert second.from_cache
    assert session.calls == ["https://example.com/robots.txt", "https://example.com/jobs/1"]
    assert list((tmp_path / "cache/example.com").glob("*.html"))
    assert list((tmp_path / "cache/example.com").glob("*.meta.json"))


def test_robots_disallow_aborts_before_page_request(tmp_path, monkeypatch):
    monkeypatch.setattr("jobfilter.fetch.time.sleep", lambda _: None)
    session = Session([Response("https://example.com/robots.txt", "User-agent: *\nDisallow: /private")])
    fetcher = Fetcher(config(tmp_path), session)
    with pytest.raises(RobotsDisallowed, match="disallows"):
        fetcher.get("https://example.com/private/job")
    assert session.calls == ["https://example.com/robots.txt"]


def test_robots_403_is_unavailable_but_page_403_would_still_block(tmp_path, monkeypatch):
    monkeypatch.setattr("jobfilter.fetch.time.sleep", lambda _: None)
    session = Session(
        [
            Response("https://example.com/robots.txt", "", status=403),
            Response("https://example.com/jobs/1", "job"),
        ]
    )
    fetcher = Fetcher(config(tmp_path), session)
    result = fetcher.get("https://example.com/jobs/1")
    assert result.text == "job"
    assert session.calls == ["https://example.com/robots.txt", "https://example.com/jobs/1"]


def test_unreachable_robots_conservatively_disallows(tmp_path, monkeypatch):
    monkeypatch.setattr("jobfilter.fetch.time.sleep", lambda _: None)
    session = Session([Response("https://example.com/robots.txt", "error", status=500)])
    fetcher = Fetcher(config(tmp_path), session)
    with pytest.raises(RobotsDisallowed):
        fetcher.get("https://example.com/jobs/1")


def test_blocked_external_host_is_not_requested_again_during_run(tmp_path, monkeypatch):
    monkeypatch.setattr("jobfilter.fetch.time.sleep", lambda _: None)
    session = Session(
        [
            Response("https://example.com/robots.txt", "", status=403),
            Response("https://example.com/jobs/1", "", status=403),
        ]
    )
    fetcher = Fetcher(config(tmp_path), session)
    with pytest.raises(SiteBlocked, match="returned HTTP 403"):
        fetcher.get("https://example.com/jobs/1")
    with pytest.raises(SiteBlocked, match="previously returned HTTP 403"):
        fetcher.get("https://example.com/jobs/2")
    assert session.calls == ["https://example.com/robots.txt", "https://example.com/jobs/1"]
