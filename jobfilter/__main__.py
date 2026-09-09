from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .adapters import adapter_for_url
from .config import Config
from .fetch import FetchError, Fetcher
from .runner import enrich_external_description, format_summary, run
from .scoring import load_keywords, score_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobfilter")
    parser.add_argument("--config", default="config.yaml")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="scrape, score, and export BA results")
    run_parser.add_argument("--url", action="append", default=[])
    run_parser.add_argument("--urls-file", type=Path)
    run_parser.add_argument("--max-pages", type=int, help="listing-page cap; 0 means all pages")
    run_parser.add_argument("--max-ads", type=int, help="advertisement cap; 0 means all advertisements")
    run_parser.add_argument("--since")
    run_parser.add_argument("--out", type=Path)
    score = commands.add_parser("score-text", help="score one saved student job")
    score.add_argument("--file", required=True, type=Path)
    score.add_argument("--title", default="")
    score.add_argument("--employment-type", default="")
    explain = commands.add_parser("explain", help="fetch and explain one BA job")
    explain.add_argument("--url", required=True)
    return parser


def _urls(args: argparse.Namespace) -> list[str]:
    values = list(args.url)
    if args.urls_file:
        values.extend(line.strip() for line in args.urls_file.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#"))
    return values


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config.load(args.config)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if args.command == "score-text":
        result = score_text(args.title, args.file.read_text(encoding="utf-8"), load_keywords(config.keywords_path), config.thresholds, employment_type=args.employment_type)
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "run":
        full_time_xlsx, other_xlsx, digest, stats = run(
            _urls(args), config, max_pages=args.max_pages, max_ads=args.max_ads,
            since=args.since, output_dir=args.out,
        )
        print(format_summary(stats))
        print(f"Vollzeit Excel: {full_time_xlsx}")
        print(f"Other Excel: {other_xlsx}")
        print(f"Digest: {digest}")
        return 0
    adapter = adapter_for_url(args.url)
    fetcher = Fetcher(config)
    ad = adapter.parse_detail(fetcher.get(args.url, cache_detail=True).text, args.url)
    try:
        ad, _ = enrich_external_description(ad, fetcher)
    except (FetchError, ValueError) as exc:
        # Explain remains useful when a partner page is unavailable; the run
        # command logs and counts the same condition.
        logging.warning("Could not use external description: %s", exc)
    result = score_text(ad.title, ad.full_text, load_keywords(config.keywords_path), config.thresholds, employment_type=ad.employment_type)
    print(json.dumps({"job": {"title": ad.title, "employer": ad.employer, "url": ad.url}, "score": result.as_dict()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
