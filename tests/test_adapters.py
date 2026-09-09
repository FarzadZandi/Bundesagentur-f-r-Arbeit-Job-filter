import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from jobfilter.adapters import ArbeitsagenturAdapter, adapter_for_url
from jobfilter.adapters.arbeitsagentur import API_KEY, AdapterParseError, extract_external_job_text
from jobfilter.fetch import FetchResult


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests/fixtures"
SEARCH_URL = (
    "https://www.arbeitsagentur.de/jobsuche/suche?suchbereich=jobs"
    "&was=Data%20Analyst&wo=Heilbronn"
    "&pav=true&angebotsart=1&zeitarbeit=true"
)
NATIONWIDE_URL = (
    "https://www.arbeitsagentur.de/jobsuche/suche?suchbereich=jobs"
    "&angebotsart=1;34&arbeitszeit=vz;tz&pav=true&zeitarbeit=true&wo=Deutschland"
)


class FakeFetcher:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FetchResult(url, next(self.payloads), 200, "application/json")


def test_routes_arbeitsagentur_url():
    adapter = adapter_for_url(SEARCH_URL)
    assert isinstance(adapter, ArbeitsagenturAdapter)


def test_unknown_host_lists_supported_board():
    with pytest.raises(ValueError, match="arbeitsagentur.de"):
        adapter_for_url("https://example.com/jobs")


def test_api_url_translates_spa_filters_and_paginates():
    adapter = ArbeitsagenturAdapter()
    url = adapter._api_url(SEARCH_URL, 11, "2026-08-26T14:50:13.029Z")
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert parts.hostname == "rest.arbeitsagentur.de"
    assert query["page"] == ["11"]
    assert query["size"] == ["25"]
    assert query["zeitarbeit"] == ["false"]
    assert "pav" not in query
    assert query["was"] == ["Data Analyst"]


def test_api_url_preserves_nationwide_work_and_student_filters():
    adapter = ArbeitsagenturAdapter()
    url = adapter._api_url(NATIONWIDE_URL, 1, "2026-09-06T12:00:00.000Z")
    query = parse_qs(urlsplit(url).query)
    assert query["angebotsart"] == ["1;34"]
    assert query["arbeitszeit"] == ["vz;tz"]
    assert query["wo"] == ["Deutschland"]
    assert query["zeitarbeit"] == ["false"]


def test_parse_captured_listing_payload():
    payload = (FIXTURES / "arbeitsagentur_search_page_2.json").read_text(encoding="utf-8")
    jobs, total, page, size = ArbeitsagenturAdapter.parse_listing_payload(payload)
    assert (total, page, size) == (496, 2, 25)
    assert len(jobs) == 2
    assert jobs[0].title == "Data Analyst (m/w/d)"
    assert jobs[0].employer == "Beispiel Analytics GmbH"
    assert jobs[0].location == "Heilbronn"
    assert jobs[0].posted_date == "2026-08-06"
    assert jobs[0].url.endswith("/14357-5116-1788549-0-S")


def test_api_iteration_walks_all_returned_pages_with_shared_key():
    base = json.loads((FIXTURES / "arbeitsagentur_search_page_2.json").read_text(encoding="utf-8"))
    base["maxErgebnisse"] = 50
    base["page"] = 1
    second = json.loads(json.dumps(base))
    second["page"] = 2
    for index, item in enumerate(second["ergebnisliste"]):
        item["referenznummer"] = f"SECOND-{index}-S"
    fetcher = FakeFetcher([json.dumps(base), json.dumps(second)])
    adapter = ArbeitsagenturAdapter()
    jobs = list(adapter.iter_listing(SEARCH_URL, fetcher, max_pages=20))
    assert len(jobs) == 4
    assert adapter.pages_walked == 2
    assert adapter.last_total_results == 50
    assert len(fetcher.calls) == 2
    assert all(call[1]["headers"]["X-API-Key"] == API_KEY for call in fetcher.calls)
    assert parse_qs(urlsplit(fetcher.calls[1][0]).query)["page"] == ["2"]


def test_api_iteration_accepts_unlimited_page_setting():
    base = json.loads((FIXTURES / "arbeitsagentur_search_page_2.json").read_text(encoding="utf-8"))
    base["maxErgebnisse"] = 50
    base["page"] = 1
    second = json.loads(json.dumps(base))
    second["page"] = 2
    for index, item in enumerate(second["ergebnisliste"]):
        item["referenznummer"] = f"UNLIMITED-{index}-S"
    fetcher = FakeFetcher([json.dumps(base), json.dumps(second)])
    jobs = list(ArbeitsagenturAdapter().iter_listing(NATIONWIDE_URL, fetcher, max_pages=None))
    assert len(jobs) == 4
    assert len(fetcher.calls) == 2


def test_parse_captured_detail_fragment():
    html = (FIXTURES / "arbeitsagentur_detail_1.html").read_text(encoding="utf-8")
    url = "https://www.arbeitsagentur.de/jobsuche/jobdetail/10000-1207649365-S"
    ad = ArbeitsagenturAdapter().parse_detail(html, url)
    assert ad.title == "Werkstudent Data Analytics (w/m/d)"
    assert ad.employer == "Beispiel Analytics GmbH"
    assert ad.location == "Heilbronn"
    assert ad.posted_date == "25.08.2026"
    assert ad.deadline == "30. September 2026"
    assert "Beispiel Analytics GmbH" in ad.full_text
    assert ad.raw_meta["reference"] == "10000-1207649365-S"
    assert ad.raw_meta["contract"] == "befristet für 12 Monate"
    assert ad.employment_type == ad.raw_meta["employment_type"]


def test_html_fallback_parses_rendered_result_and_detects_button(caplog):
    html = (FIXTURES / "arbeitsagentur_listing_1.html").read_text(encoding="utf-8")
    fetcher = FakeFetcher([html])
    adapter = ArbeitsagenturAdapter()
    jobs = list(adapter._iter_html_fallback(SEARCH_URL, fetcher, max_pages=1))
    assert len(jobs) == 1
    assert jobs[0].employer == "Beispiel Analytics GmbH"
    assert jobs[0].posted_date == "25.08.2026"
    assert parse_qs(urlsplit(fetcher.calls[0][0]).query)["page"] == ["1"]


def test_html_total_and_page_url_are_parsed():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup("<span>495 Jobs für Data Analyst</span>", "lxml")
    assert ArbeitsagenturAdapter._html_total(soup) == 495
    page_url = ArbeitsagenturAdapter._html_page_url(SEARCH_URL, 11)
    assert parse_qs(urlsplit(page_url).query)["page"] == ["11"]


def test_detail_without_title_fails_clearly():
    with pytest.raises(AdapterParseError, match="no title"):
        ArbeitsagenturAdapter().parse_detail("<html></html>", "https://example.com/job")


def test_detail_captures_external_description_link():
    html = (FIXTURES / "arbeitsagentur_detail_1.html").read_text(encoding="utf-8").replace(
        "</section>",
        '<a id="detail-beschreibung-externe-url-btn" href="https://careers.example.com/job/123">Open</a></section>',
    )
    ad = ArbeitsagenturAdapter().parse_detail(html, "https://www.arbeitsagentur.de/jobsuche/jobdetail/123")
    assert ad.raw_meta["external_url"] == "https://careers.example.com/job/123"


def test_extract_external_job_text_prefers_jobposting_json_ld():
    description = "<p>You will analyze workforce data and build dashboards for our international team.</p>" * 4
    html = '<html><script type="application/ld+json">' + json.dumps({
        "@context": "https://schema.org", "@type": "JobPosting", "description": description,
    }) + '</script><body>Javascript shell</body></html>'
    text = extract_external_job_text(html)
    assert "analyze workforce data" in text
    assert "Javascript shell" not in text
