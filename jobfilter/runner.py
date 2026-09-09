from __future__ import annotations

import logging
import random
import re
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from .adapters import adapter_for_url
from .adapters.base import JobAd, JobStub
from .adapters.arbeitsagentur import extract_external_job_text
from .config import Config
from .export import ScoredJob, write_outputs
from .fetch import FetchError, Fetcher
from .scoring import load_keywords, score_text
from .store import SeenStore, canonicalize_url, identity_hash


LOGGER = logging.getLogger(__name__)


@dataclass
class BoardStats:
    pages_walked: int = 0
    advertised_total: int | None = None
    listings_seen: int = 0
    details_considered: int = 0
    new_advertisements: int = 0
    previously_seen: int = 0
    in_run_duplicates: int = 0
    priority: int = 0
    review: int = 0
    de_required: int = 0
    academische: int = 0
    failures: int = 0
    external_details_used: int = 0
    external_detail_failures: int = 0
    full_time_results: int = 0
    other_results: int = 0
    dropped: Counter[str] = field(default_factory=Counter)
    families: Counter[str] = field(default_factory=Counter)
    elapsed: float = 0.0


def _since_cutoff(value: str | None) -> date | None:
    if not value:
        return None
    match = re.fullmatch(r"(\d+)d", value.strip().lower())
    if not match:
        raise ValueError("--since must look like 3d or 14d")
    return date.today() - timedelta(days=int(match.group(1)))


def _is_recent(stub: JobStub, cutoff: date | None) -> bool:
    if cutoff is None or not stub.posted_date:
        return True
    try:
        posted = date.fromisoformat(stub.posted_date) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stub.posted_date) else date(*map(int, reversed(stub.posted_date.split("."))))
        return posted >= cutoff
    except (ValueError, TypeError):
        return True


def enrich_external_description(ad: JobAd, fetcher: Fetcher) -> tuple[JobAd, bool]:
    external_url = str(ad.raw_meta.get("external_url") or "")
    if not external_url:
        return ad, False
    external = fetcher.get(external_url, cache_detail=True)
    external_text = extract_external_job_text(external.text)
    if len(external_text) < 200:
        raise ValueError(f"External description was too short: {external_url}")
    return replace(
        ad,
        full_text=f"{ad.full_text}\n{external_text}".strip(),
        raw_meta=ad.raw_meta | {
            "external_final_url": external.url,
            "external_description_used": True,
        },
    ), True


def _pause_before_position(completed: int, next_position: int, config: Config, *, batch_pause: bool = False) -> float:
    """Pause between advertisements even when their detail pages come from cache."""
    if completed <= 0:
        return 0.0
    if batch_pause:
        pause = random.uniform(config.position_batch_pause_min, config.position_batch_pause_max)
        LOGGER.info(
            "Batch pause after %s positions before detail %s: %.1f seconds",
            completed,
            next_position,
            pause,
        )
    else:
        pause = random.uniform(config.position_delay_min, config.position_delay_max)
        LOGGER.info("Short pause before detail %s: %.1f seconds", next_position, pause)
    time.sleep(pause)
    return pause


def run(
    urls: Iterable[str], config: Config, *, max_pages: int | None = None, max_ads: int | None = None,
    since: str | None = None, output_dir: str | Path | None = None,
) -> tuple[Path, Path, Path, dict[str, BoardStats]]:
    urls = [url.strip() for url in urls if url.strip()]
    if not urls:
        raise ValueError("At least one --url or a non-empty --urls-file is required")
    configured_max_pages = config.max_pages if max_pages is None else max_pages
    configured_max_ads = config.max_ads if max_ads is None else max_ads
    if configured_max_pages < 0:
        raise ValueError("--max-pages must be zero (unlimited) or positive")
    if configured_max_ads < 0:
        raise ValueError("--max-ads must be zero (unlimited) or positive")
    page_cap = configured_max_pages or None
    ad_cap = configured_max_ads or None
    keywords, cutoff = load_keywords(config.keywords_path), _since_cutoff(since)
    fetcher, store = Fetcher(config), SeenStore(config.database_path)
    run_id, started_all = store.start_run(), time.monotonic()
    scored_jobs: list[ScoredJob] = []
    pending_seen: list[tuple[str, str, str, str]] = []
    pending_urls: set[str] = set()
    pending_identities: set[str] = set()
    stats: dict[str, BoardStats] = {}
    details_attempted, status = 0, "complete"
    next_batch_pause_at = random.randint(config.position_batch_size_min, config.position_batch_size_max)
    try:
        for search_url in urls:
            adapter = adapter_for_url(search_url)
            board_stats = stats.setdefault(adapter.name, BoardStats())
            started = time.monotonic()
            try:
                for stub in adapter.iter_listing(search_url, fetcher, page_cap):
                    board_stats.listings_seen += 1
                    if not _is_recent(stub, cutoff):
                        continue
                    stub_url_key = canonicalize_url(stub.url)
                    stub_identity_key = identity_hash(stub.title, stub.employer)
                    if stub_url_key in pending_urls or stub_identity_key in pending_identities:
                        board_stats.in_run_duplicates += 1
                        continue
                    if store.is_seen(stub.url, stub.title, stub.employer):
                        board_stats.previously_seen += 1
                        continue
                    if ad_cap is not None and details_attempted >= ad_cap:
                        LOGGER.warning("Reached the configured detail-page cap of %s", ad_cap)
                        break
                    is_batch_pause = details_attempted >= next_batch_pause_at
                    _pause_before_position(
                        details_attempted,
                        details_attempted + 1,
                        config,
                        batch_pause=is_batch_pause,
                    )
                    if is_batch_pause:
                        next_batch_pause_at = details_attempted + random.randint(
                            config.position_batch_size_min,
                            config.position_batch_size_max,
                        )
                    board_stats.new_advertisements += 1
                    board_stats.details_considered += 1
                    details_attempted += 1
                    LOGGER.info("Detail %s/%s: %s", details_attempted, ad_cap or "all", stub.title)
                    try:
                        detail = fetcher.get(stub.url, cache_detail=True)
                        ad = adapter.parse_detail(detail.text, stub.url)
                        external_url = str(ad.raw_meta.get("external_url") or "")
                        if external_url:
                            try:
                                ad, used = enrich_external_description(ad, fetcher)
                                if used:
                                    board_stats.external_details_used += 1
                            except (FetchError, ValueError) as exc:
                                board_stats.external_detail_failures += 1
                                LOGGER.warning("Could not retrieve external description %s: %s", external_url, exc)
                        result = score_text(ad.title, ad.full_text, keywords, config.thresholds, employment_type=ad.employment_type)
                        scored_jobs.append(ScoredJob(ad, adapter.name, result))
                        if result.employment_format == "FULL-TIME / VOLLZEIT":
                            board_stats.full_time_results += 1
                        else:
                            board_stats.other_results += 1
                        pending_seen.append((ad.url, ad.title, ad.employer, adapter.name))
                        pending_urls.update((stub_url_key, canonicalize_url(ad.url)))
                        pending_identities.update((stub_identity_key, identity_hash(ad.title, ad.employer)))
                        if result.band == "PRIORITY":
                            board_stats.priority += 1
                        elif result.band == "REVIEW":
                            board_stats.review += 1
                        elif result.band == "DE REQUIRED":
                            board_stats.de_required += 1
                        elif result.band == "ACADEMISCHE":
                            board_stats.academische += 1
                        else:
                            board_stats.dropped[result.drop_stage or "ranking"] += 1
                        if result.primary_cv_family:
                            board_stats.families[result.primary_cv_family.split()[0]] += 1
                    except (FetchError, ValueError, KeyError) as exc:
                        board_stats.failures += 1
                        LOGGER.warning("Skipping %s after detail failure: %s", stub.url, exc)
                board_stats.pages_walked += getattr(adapter, "pages_walked", 0)
                board_stats.advertised_total = getattr(adapter, "last_total_results", None)
            except Exception:
                board_stats.failures += 1
                raise
            finally:
                board_stats.elapsed += time.monotonic() - started
            if ad_cap is not None and details_attempted >= ad_cap:
                break
        paths = write_outputs(scored_jobs, output_dir or config.output_dir)
        for seen_args in pending_seen:
            store.mark_seen(*seen_args)
        summary = {name: vars(value) | {"dropped": dict(value.dropped), "families": dict(value.families)} for name, value in stats.items()}
        summary["elapsed_total"] = time.monotonic() - started_all
        store.finish_run(run_id, status, summary)
        return paths[0], paths[1], paths[2], stats
    except BaseException:
        status = "interrupted" if isinstance(__import__('sys').exc_info()[1], KeyboardInterrupt) else "failed"
        store.finish_run(run_id, status, {})
        raise


def format_summary(stats: dict[str, BoardStats]) -> str:
    lines = ["Run summary"]
    totals = Counter()
    families = Counter()
    elapsed = 0.0
    for value in stats.values():
        for key in (
            "listings_seen", "details_considered", "new_advertisements", "previously_seen", "in_run_duplicates",
            "priority", "review", "failures", "full_time_results", "other_results",
            "de_required", "academische",
            "external_details_used", "external_detail_failures",
        ):
            totals[key] += getattr(value, key)
        families.update(value.families)
        elapsed += value.elapsed
        totals["dropped_total"] += sum(value.dropped.values())
        for stage, count in value.dropped.items():
            totals[f"drop_{stage}"] += count
    lines.append(
        "listings seen={listings_seen}, detail pages considered={details_considered}, new advertisements={new_advertisements}, "
        "previously seen={previously_seen}, in-run duplicates={in_run_duplicates}, dropped={dropped_total}, PRIORITY={priority}, REVIEW={review}, "
        "DE Required={de_required}, Academische={academische}, "
        "failures={failures}, external descriptions used={external_details_used}, "
        "external description failures={external_detail_failures}, elapsed={elapsed:.1f}s".format(**totals, elapsed=elapsed)
    )
    drop_stages = sorted(
        (key.removeprefix("drop_"), count)
        for key, count in totals.items()
        if key.startswith("drop_") and count
    )
    lines.append("Drops by stage: " + (", ".join(f"{stage}={count}" for stage, count in drop_stages) or "none"))
    lines.append("Output groups: Vollzeit={full_time_results}, Other={other_results}".format(**totals))
    lines.append("Primary CV families: " + ", ".join(f"B{i:02d}={families[f'B{i:02d}']}" for i in range(1, 11)))
    return "\n".join(lines)
