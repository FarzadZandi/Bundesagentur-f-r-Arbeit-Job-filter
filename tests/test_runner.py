from datetime import date, timedelta

import pytest

from jobfilter.adapters.base import JobAd, JobStub
from jobfilter.fetch import FetchResult
from jobfilter.config import Config
from jobfilter.runner import _is_recent, _pause_before_position, _since_cutoff, enrich_external_description
from jobfilter.runner import BoardStats, format_summary


def test_since_parser_and_recent_filter():
    cutoff = _since_cutoff("3d")
    assert cutoff == date.today() - timedelta(days=3)
    assert _is_recent(JobStub("url", "title", posted_date=date.today().isoformat()), cutoff)
    assert not _is_recent(JobStub("url", "title", posted_date="2000-01-01"), cutoff)


def test_since_rejects_unclear_values():
    with pytest.raises(ValueError, match="3d"):
        _since_cutoff("last week")


def test_external_description_is_appended_before_scoring():
    ad = JobAd(
        "https://www.arbeitsagentur.de/jobsuche/jobdetail/123",
        "Working Student Strategy",
        full_text="Short BA shell",
        raw_meta={"external_url": "https://careers.example.com/123"},
    )

    class ExternalFetcher:
        def get(self, url, **kwargs):
            assert url == "https://careers.example.com/123"
            assert kwargs == {"cache_detail": True}
            body = "You will work with our corporate strategy team and analyze business data. " * 6
            return FetchResult(url, f"<main>{body}</main>", 200, "text/html")

    enriched, used = enrich_external_description(ad, ExternalFetcher())
    assert used
    assert enriched.full_text.startswith("Short BA shell")
    assert "analyze business data" in enriched.full_text
    assert enriched.raw_meta["external_description_used"] is True


def test_position_pause_is_short_between_ads_and_long_at_batch_boundary(monkeypatch):
    config = Config(
        position_delay_min=3.0,
        position_delay_max=5.0,
        position_batch_size_min=20,
        position_batch_size_max=30,
        position_batch_pause_min=20.0,
        position_batch_pause_max=40.0,
    )
    uniform_calls = []
    sleeps = []

    def fake_uniform(low, high):
        uniform_calls.append((low, high))
        return (low + high) / 2

    monkeypatch.setattr("jobfilter.runner.random.uniform", fake_uniform)
    monkeypatch.setattr("jobfilter.runner.time.sleep", sleeps.append)

    assert _pause_before_position(0, 1, config) == 0.0
    assert _pause_before_position(1, 2, config) == 4.0
    assert _pause_before_position(25, 26, config, batch_pause=True) == 30.0
    assert uniform_calls == [(3.0, 5.0), (20.0, 40.0)]
    assert sleeps == [4.0, 30.0]


def test_operational_defaults_use_resumable_500_ad_batches():
    config = Config()
    assert config.max_ads == 500
    assert config.position_delay_min == 0.5
    assert config.position_delay_max == 2.0


def test_summary_contains_required_counts_and_b01_b10():
    stats = BoardStats(
        listings_seen=20,
        details_considered=12,
        new_advertisements=12,
        previously_seen=8,
        in_run_duplicates=2,
        priority=3,
        review=4,
        de_required=2,
        academische=1,
        failures=1,
        full_time_results=7,
        other_results=5,
    )
    stats.dropped.update({
        "employment_gate": 2,
        "wrong_profession": 1,
        "relevance_gate": 2,
        "language_gate": 3,
        "academic_role_gate": 4,
        "commercial_role_gate": 5,
    })
    stats.families.update({"B01": 1, "B10": 2})
    text = format_summary({"arbeitsagentur": stats})
    for phrase in (
        "listings seen=20", "detail pages considered=12", "in-run duplicates=2", "dropped=17", "PRIORITY=3", "REVIEW=4",
        "DE Required=2", "Academische=1",
        "academic_role_gate=4", "commercial_role_gate=5", "employment_gate=2", "language_gate=3",
        "relevance_gate=2", "wrong_profession=1",
        "Vollzeit=7", "Other=5", "B01=1", "B10=2",
    ):
        assert phrase in text
