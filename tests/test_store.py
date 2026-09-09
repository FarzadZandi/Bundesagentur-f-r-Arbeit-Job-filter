from jobfilter.store import SeenStore, canonicalize_url


def test_canonicalize_removes_tracking_and_fragment():
    url = "HTTPS://Example.COM/job/1/?utm_source=x&b=2&a=1#details"
    assert canonicalize_url(url) == "https://example.com/job/1?a=1&b=2"


def test_seen_by_url_or_title_employer_identity(tmp_path):
    store = SeenStore(tmp_path / "seen.sqlite")
    store.mark_seen("https://example.com/job/1", "Data Analyst", "Example GmbH", "test")
    assert store.is_seen("https://example.com/job/1?utm_source=daily")
    assert store.is_seen("https://example.com/job/new-url", " Data Analyst ", "example gmbh")
    assert not store.is_seen("https://example.com/job/2", "Other", "Example GmbH")

