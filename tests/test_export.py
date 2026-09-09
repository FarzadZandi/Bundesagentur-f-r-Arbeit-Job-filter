from datetime import date

from openpyxl import load_workbook

from jobfilter.adapters.base import JobAd
from jobfilter.export import DROPPED_COLUMNS, KEPT_COLUMNS, ScoredJob, write_outputs
from jobfilter.scoring import ScoreResult


def item(
    band, score, title, *, reason=None, stage="",
    primary="B06 Data Analytics und Business Intelligence",
    employment_format="WERKSTUDENT / WORKING STUDENT",
):
    ad = JobAd(
        url=f"https://example.com/{title.lower().replace(' ', '-')}",
        title=title,
        employer="Example GmbH",
        location="Heilbronn",
        posted_date="2026-08-25",
        deadline="2026-09-30",
        full_text="KPI reporting and data analysis using Power BI.",
        employment_type="Werkstudent",
    )
    result = ScoreResult(
        score=score,
        raw_score=score,
        band=band,
        reason=reason,
        drop_stage=stage,
        matched_term="python" if band == "DROP" else "",
        employment_format=employment_format,
        primary_cv_family=primary,
        matched_terms={
            "B06.strong": ("data analytics",),
            "analysis_task": ("kpi reporting",),
            "methods": (),
            "tools_strong": ("power bi",),
        },
        language_flags=("GERMAN C1+ / NATIVE-LEVEL",) if title == "Review Job" else (),
        eligibility_flags=("IMMATRIKULATION REQUIRED",),
        penalties=("GERMAN C1+ / NATIVE-LEVEL (-5)",) if title == "Review Job" else (),
        bonuses=("EXACT STUDENT FORMAT (+2)",),
    )
    return ScoredJob(ad, "arbeitsagentur", result)


def test_writes_required_workbook_and_priority_digest(tmp_path):
    jobs = [
        item("REVIEW", 8, "Review Job"),
        item("PRIORITY", 18, "Priority Job"),
        item("DE REQUIRED", 16, "German Required Job", reason="german_c1_c2_or_native_required"),
        item("ACADEMISCHE", 0, "Academic Job", reason="excluded_doctoral_candidate_role"),
        item("DROP", 0, "Dropped Job", reason="no_target_family_or_analysis_match", stage="relevance_gate", primary=""),
    ]
    full_time_xlsx, other_xlsx, digest = write_outputs(jobs, tmp_path, date(2026, 8, 26))
    assert full_time_xlsx.name == "jobs_vollzeit_2026-08-26.xlsx"
    assert other_xlsx.name == "jobs_other_2026-08-26.xlsx"
    workbook = load_workbook(other_xlsx)
    assert workbook.sheetnames == ["PRIORITY", "REVIEW", "DE Required", "Academische", "DROPPED"]
    assert [cell.value for cell in workbook["PRIORITY"][1]] == KEPT_COLUMNS
    assert [cell.value for cell in workbook["DROPPED"][1]] == DROPPED_COLUMNS
    assert workbook["PRIORITY"].freeze_panes == "A2"
    assert workbook["PRIORITY"].auto_filter.ref == "A1:W2"
    assert workbook["PRIORITY"]["L2"].hyperlink.target.endswith("priority-job")
    assert workbook["REVIEW"]["Q2"].value == "GERMAN C1+ / NATIVE-LEVEL"
    assert workbook["DE Required"]["F2"].value == "German Required Job"
    assert workbook["Academische"]["F2"].value == "Academic Job"
    assert workbook["DROPPED"]["E2"].value == "relevance_gate"
    assert workbook["DROPPED"]["F2"].value == "no_target_family_or_analysis_match"
    assert workbook["DROPPED"]["G2"].value == "python"
    text = digest.read_text(encoding="utf-8")
    assert "18 | B06 Data Analytics" in text
    assert "Priority Job" in text
    assert "Review Job" not in text


def test_rows_are_sorted_descending(tmp_path):
    _, other_xlsx, _ = write_outputs(
        [item("PRIORITY", 12, "Lower"), item("PRIORITY", 22, "Higher")],
        tmp_path,
        date(2026, 8, 26),
    )
    sheet = load_workbook(other_xlsx)["PRIORITY"]
    assert [sheet["A2"].value, sheet["A3"].value] == [22, 12]


def test_full_time_and_other_jobs_are_written_to_separate_workbooks(tmp_path):
    full_time_xlsx, other_xlsx, _ = write_outputs(
        [
            item("PRIORITY", 20, "Full-time Analyst", employment_format="FULL-TIME / VOLLZEIT"),
            item("PRIORITY", 18, "Working Student Analyst"),
        ],
        tmp_path,
        date(2026, 8, 26),
    )
    full_time = load_workbook(full_time_xlsx)
    other = load_workbook(other_xlsx)
    assert full_time["PRIORITY"]["F2"].value == "Full-time Analyst"
    assert full_time["PRIORITY"].max_row == 2
    assert other["PRIORITY"]["F2"].value == "Working Student Analyst"
    assert other["PRIORITY"].max_row == 2


def test_repeated_same_day_runs_do_not_overwrite_prior_outputs(tmp_path):
    first = write_outputs([item("PRIORITY", 18, "First Batch")], tmp_path, date(2026, 8, 26))
    second = write_outputs([item("PRIORITY", 18, "Second Batch")], tmp_path, date(2026, 8, 26))
    assert first[0].name == "jobs_vollzeit_2026-08-26.xlsx"
    assert second[0].name == "jobs_vollzeit_2026-08-26_2.xlsx"
    assert all(path.exists() for path in (*first, *second))
