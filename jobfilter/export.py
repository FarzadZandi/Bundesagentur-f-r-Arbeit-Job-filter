from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .adapters.base import JobAd
from .scoring import ScoreResult


KEPT_COLUMNS = [
    "score", "band", "employment_format", "primary_cv_family", "secondary_cv_family",
    "title", "employer", "location", "posted", "deadline", "board", "url",
    "family_hits", "analysis_task_hits", "method_hits", "tool_hits", "language_flags",
    "eligibility_flags", "other_flags", "penalties", "bonuses", "kill_reason", "snippet",
]
DROPPED_COLUMNS = ["title", "employer", "location", "url", "drop_stage", "drop_reason", "matched_term"]
WIDTHS = [8, 11, 25, 40, 40, 48, 32, 23, 13, 20, 18, 48, 45, 40, 35, 42, 32, 36, 40, 32, 28, 32, 60]
HEADER_FILL = PatternFill("solid", fgColor="174A6E")
HEADER_FONT = Font(color="FFFFFF", bold=True)
PRIORITY_FILL = PatternFill("solid", fgColor="D9EAF7")
AMBER_FILL = PatternFill("solid", fgColor="FCE4A6")


@dataclass(frozen=True)
class ScoredJob:
    ad: JobAd
    board: str
    result: ScoreResult


def _join(values: Iterable[str]) -> str:
    return "; ".join(dict.fromkeys(value for value in values if value))


def _hits(result: ScoreResult, predicate) -> str:
    values: list[str] = []
    for group, hits in result.matched_terms.items():
        if predicate(group):
            values.extend(hits)
    return _join(values)


def _kept_row(item: ScoredJob) -> list[object]:
    ad, result = item.ad, item.result
    family_hits = _hits(result, lambda group: group.startswith("B") and group.endswith((".strong", ".weak")))
    return [
        result.score, result.band, result.employment_format, result.primary_cv_family,
        result.secondary_cv_family, ad.title, ad.employer, ad.location, ad.posted_date,
        ad.deadline, item.board, ad.url, family_hits,
        _join(result.matched_terms.get("analysis_task", ())),
        _join(result.matched_terms.get("methods", ())),
        _join((*result.matched_terms.get("tools_strong", ()), *result.matched_terms.get("tools_basic_or_adjacent", ()))),
        _join(result.language_flags), _join(result.eligibility_flags), _join(result.other_flags),
        _join(result.penalties), _join(result.bonuses), result.reason or "",
        " ".join(ad.full_text.split())[:500],
    ]


def _style_sheet(sheet, widths: list[int]) -> None:
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    sheet.row_dimensions[1].height = 28
    for cell in sheet[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=cell.column >= 3)


def _write_workbook(items: list[ScoredJob], xlsx_path: Path) -> None:
    by_band = {
        band: sorted((item for item in items if item.result.band == band), key=lambda item: item.result.score, reverse=True)
        for band in ("PRIORITY", "REVIEW", "DE REQUIRED", "ACADEMISCHE", "DROP")
    }
    workbook = Workbook()
    workbook.remove(workbook.active)
    for band, sheet_name in (
        ("PRIORITY", "PRIORITY"),
        ("REVIEW", "REVIEW"),
        ("DE REQUIRED", "DE Required"),
        ("ACADEMISCHE", "Academische"),
    ):
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(KEPT_COLUMNS)
        for item in by_band[band]:
            sheet.append(_kept_row(item))
            row = sheet.max_row
            sheet.cell(row, 12).hyperlink = item.ad.url
            sheet.cell(row, 12).style = "Hyperlink"
            if band == "PRIORITY":
                sheet.cell(row, 2).fill = PRIORITY_FILL
            if band == "DE REQUIRED" or any("GERMAN C1" in value for value in item.result.language_flags):
                sheet.cell(row, 17).fill = AMBER_FILL
        _style_sheet(sheet, WIDTHS)

    dropped = workbook.create_sheet("DROPPED")
    dropped.append(DROPPED_COLUMNS)
    for item in by_band["DROP"]:
        result = item.result
        dropped.append([
            item.ad.title, item.ad.employer, item.ad.location, item.ad.url,
            result.drop_stage, result.reason or "score_below_review", result.matched_term,
        ])
        dropped.cell(dropped.max_row, 4).hyperlink = item.ad.url
        dropped.cell(dropped.max_row, 4).style = "Hyperlink"
    _style_sheet(dropped, [48, 32, 23, 48, 22, 40, 30])

    workbook.save(xlsx_path)


def write_outputs(jobs: Iterable[ScoredJob], output_dir: str | Path, run_date: date | None = None) -> tuple[Path, Path, Path]:
    run_date = run_date or date.today()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    items = list(jobs)
    full_time = [item for item in items if item.result.employment_format == "FULL-TIME / VOLLZEIT"]
    other = [item for item in items if item.result.employment_format != "FULL-TIME / VOLLZEIT"]
    base_stem = run_date.isoformat()
    stem = base_stem
    sequence = 2
    while any(
        (output_dir / name).exists()
        for name in (
            f"jobs_vollzeit_{stem}.xlsx",
            f"jobs_other_{stem}.xlsx",
            f"jobs_{stem}.txt",
        )
    ):
        stem = f"{base_stem}_{sequence}"
        sequence += 1
    full_time_path = output_dir / f"jobs_vollzeit_{stem}.xlsx"
    other_path = output_dir / f"jobs_other_{stem}.xlsx"
    text_path = output_dir / f"jobs_{stem}.txt"
    _write_workbook(full_time, full_time_path)
    _write_workbook(other, other_path)

    blocks: list[str] = []
    priority_items = sorted(
        (item for item in items if item.result.band == "PRIORITY"),
        key=lambda item: item.result.score,
        reverse=True,
    )
    for item in priority_items:
        result, ad = item.result, item.ad
        strongest = _hits(result, lambda group: group.endswith(".strong") or group in {"analysis_task", "methods"})
        flags = _join((*result.language_flags, *result.eligibility_flags)) or "none"
        blocks.append(
            f"{result.score} | {result.primary_cv_family} | {ad.title} | {ad.employer} | {ad.location}\n"
            f"- Format: {result.employment_format}\n"
            f"- Deadline: {ad.deadline or 'not stated'}\n"
            f"- Strongest matches: {strongest or 'none'}\n"
            f"- Language/eligibility: {flags}\n"
            f"- {ad.url}"
        )
    text_path.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")
    return full_time_path, other_path, text_path
