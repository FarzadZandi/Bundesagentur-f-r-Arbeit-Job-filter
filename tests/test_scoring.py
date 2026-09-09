import json
from pathlib import Path

import pytest

from jobfilter.scoring import load_keywords, normalize, score_text


ROOT = Path(__file__).parents[1]
KEYWORDS = load_keywords(ROOT / "keywords_jobseeking_student_v1.json")
CASES = json.loads((ROOT / "tests/fixtures/student_scoring_cases.json").read_text(encoding="utf-8"))


def test_normalize_strips_html_decodes_and_transliterates_once():
    assert normalize("<p>Grüße&nbsp; &amp; Ökonomie</p><script>wrong</script>") == "gruesse & oekonomie"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_student_scoring_fixtures(case):
    result = score_text(case["title"], case["body"], KEYWORDS, employment_type=case.get("employment_type", ""))
    assert result.band == case["band"]
    if "drop_stage" in case:
        assert result.drop_stage == case["drop_stage"]
    if "reason" in case:
        assert result.reason == case["reason"]
    if "primary" in case:
        assert result.primary_cv_family.startswith(case["primary"])
    for flag in case.get("flags", []):
        assert flag in result.flags


def test_all_ten_families_are_defined_and_exercised():
    assert len(KEYWORDS["cv_families"]) == 10
    exercised = {case["primary"] for case in CASES if "primary" in case}
    assert exercised == {f"B{index:02d}" for index in range(1, 11)}


def test_structured_metadata_passes_without_title_format():
    result = score_text("Data Analytics", "KPI reporting and data analysis using Power BI.", KEYWORDS, employment_type="Praktikum")
    assert result.band in {"PRIORITY", "REVIEW"}
    assert result.employment_format == "PRAKTIKUM / INTERNSHIP"


def test_structured_full_time_is_scored_and_classified():
    result = score_text(
        "Data Analyst",
        "Business intelligence and KPI reporting using Power BI.",
        KEYWORDS,
        employment_type="Vollzeit",
    )
    assert result.band in {"PRIORITY", "REVIEW"}
    assert result.employment_format == "FULL-TIME / VOLLZEIT"


def test_explicit_doctoral_candidate_titles_are_separated():
    for title in ("Doktorand Data Analytics", "PhD Candidate Market Research"):
        result = score_text(
            title,
            "Data analytics, market research and KPI reporting.",
            KEYWORDS,
            employment_type="Vollzeit",
        )
        assert result.band == "ACADEMISCHE"
        assert result.drop_stage == ""
        assert result.reason == "excluded_doctoral_candidate_role"


def test_relevant_non_doctoral_university_position_can_pass():
    result = score_text(
        "Wissenschaftlicher Mitarbeiter Market Research",
        "Market research, consumer insights and data analysis at a university institute.",
        KEYWORDS,
        employment_type="Vollzeit",
    )
    assert result.band in {"PRIORITY", "REVIEW"}
    assert result.primary_cv_family.startswith(("B01", "B07"))
    assert result.employment_format == "FULL-TIME / VOLLZEIT"


def test_body_only_employment_evidence_must_be_strong():
    weak = score_text("Data Analytics", "Student role with KPI reporting and Power BI.", KEYWORDS)
    strong = score_text("Data Analytics", "Join us as a working student for KPI reporting and data analysis with Power BI.", KEYWORDS)
    assert weak.reason == "employment_format_not_target"
    assert strong.band != "DROP"


def test_mixed_employment_format_keeps_and_flags_other_format():
    result = score_text("Werkstudent Data Analytics", "Vollzeit team; KPI reporting and Power BI data analysis.", KEYWORDS)
    assert result.band != "DROP"
    assert result.employment_format == "WERKSTUDENT / WORKING STUDENT"
    assert any("vollzeit" in flag.lower() for flag in result.other_flags)


def test_family_overlap_uses_only_best_family_contribution():
    result = score_text("Werkstudent Digital Analytics Data Analytics", "GA4 funnel conversion attribution plus KPI reporting and Power BI data analysis.", KEYWORDS)
    assert result.primary_cv_family.startswith("B09")
    assert result.family_scores["B06"] > 0 and result.family_scores["B09"] > 0
    assert result.score < 50


def test_keyword_dictionary_is_dynamic_not_hardcoded(tmp_path):
    modified = json.loads((ROOT / "keywords_jobseeking_student_v1.json").read_text(encoding="utf-8"))
    modified["cv_families"]["B01_forschung_unternehmen_institute"]["strong_terms"].append("quuxresearch")
    path = tmp_path / "keywords.json"
    path.write_text(json.dumps(modified), encoding="utf-8")
    result = score_text("Werkstudent Spezialrolle", "quuxresearch and data analysis", load_keywords(path))
    assert result.band != "DROP"
    assert result.primary_cv_family.startswith("B01")


def test_relevant_high_german_goes_to_de_required():
    for phrase in (
        "sehr gute Deutschkenntnisse",
        "Deutsch C1",
        "Deutschkenntnisse C2",
        "verhandlungssicheres Deutsch",
        "fließende Deutsch- und Englischkenntnisse",
        "hervorragenden Deutsch- und Englischkenntnissen",
        "sehr gut auf Deutsch",
        "fluent in German",
        "Deutsch und Englisch fließend",
        "German and English fluency",
    ):
        result = score_text("Werkstudent Market Research", f"Market research and data analysis. {phrase}.", KEYWORDS)
        assert result.band == "DE REQUIRED"
        assert result.drop_stage == ""
        assert result.reason == "german_c1_c2_or_native_required"


def test_strong_family_passes_without_analysis_in_recall_first_mode():
    german_context = score_text("Werkstudent Corporate Strategy", "Corporate strategy projects.", KEYWORDS)
    english_context = score_text(
        "Werkstudent Corporate Strategy",
        "Corporate strategy projects. English is the working language.",
        KEYWORDS,
    )
    assert german_context.band != "DROP"
    assert english_context.band != "DROP"


def test_isolated_english_requirement_does_not_rescue_a_german_ad():
    result = score_text(
        "Werkstudent Spezialrolle",
        "Du arbeitest in unserem Team an Strategie und unterstuetzt die Kollegen. "
        "Wir erwarten sehr gute Englischkenntnisse und bieten dir spannende Aufgaben im Unternehmen.",
        KEYWORDS,
    )
    assert result.band == "REVIEW"
    assert result.reason == "uncertain_relevance_requires_manual_review"


def test_uncertain_academic_research_goes_to_academische():
    result = score_text(
        "Research Associate Spezialrolle",
        "Administrative Unterstützung ohne eindeutige B01-B10-Aufgaben.",
        KEYWORDS,
        employment_type="Vollzeit",
    )
    assert result.band == "ACADEMISCHE"
    assert result.reason == "academic_research_requires_manual_review"


def test_substantially_english_ad_can_pass_without_analytical_task_phrase():
    result = score_text(
        "Working Student Corporate Strategy",
        "You will work with our international strategy team and support projects for our business. "
        "The role gives you experience with senior colleagues and our products. You are part of the team "
        "and will help with planning for the company. Your skills and experience are important for this role.",
        KEYWORDS,
    )
    assert result.band != "DROP"
    assert "ENGLISH-FRIENDLY" in result.flags


def test_weak_family_plus_analysis_passes_in_recall_first_mode():
    result = score_text("Werkstudent Spezialrolle", "KPI analysis and reporting.", KEYWORDS)
    assert result.band != "DROP"


def test_relevant_hr_strategy_and_people_analytics_are_maintained():
    result = score_text(
        "Werkstudent HR Strategy",
        "People strategy and workforce planning with KPI analysis.",
        KEYWORDS,
    )
    assert result.band != "DROP"
    assert result.primary_cv_family.startswith(("B02", "B06"))


@pytest.mark.parametrize(
    ("title", "body", "family"),
    [
        ("Praktikum Personalentwicklung", "HR analytics und Auswertung von Personalkennzahlen.", "B02"),
        ("Werkstudent Controlling", "Financial controlling, Reporting und Abweichungsanalyse.", "B06"),
        ("Werkstudent Risk Management", "Risikomanagement und Bewertung von Risiken.", "B06"),
        ("Werkstudent Operational Excellence", "Operational Excellence und Prozessanalyse.", "B10"),
        ("Student Research Assistant", "Survey methodology and literature research for questionnaire evaluation.", "B01"),
    ],
)
def test_semantic_analytics_equivalents_pass(title, body, family):
    result = score_text(title, body, KEYWORDS)
    assert result.band != "DROP"
    assert result.primary_cv_family.startswith(family)


def test_short_terms_use_word_boundaries():
    result = score_text(
        "Werkstudent Softwareentwickler",
        "Digital product implementation and semiconductors with Python.",
        KEYWORDS,
    )
    assert "git" not in result.matched_terms.get("tools_strong", ())
    assert "sem" not in result.matched_terms.get("B09.weak", ())


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Bachelorarbeit Innovationsmanagement", "THESIS / STUDENT PROJECT"),
        ("Student Research Assistant Survey Methods", "STUDENT ASSISTANT"),
        ("Praktikantin Data Analytics", "PRAKTIKUM / INTERNSHIP"),
    ],
)
def test_student_titles_override_structured_full_time_routing(title, expected):
    result = score_text(title, "Data analytics and data analysis.", KEYWORDS, employment_type="Vollzeit")
    assert result.employment_format == expected


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("Praktikant Personalentwicklung", "HR-Auswertungen, Reports und Analyse relevanter KPIs."),
        ("Praktikant Immobilien", "Erstellung von Markt- und Standortanalysen."),
        ("Werkstudent Customer Experience", "Datenaufbereitung für Kundenbefragungen."),
        ("Praktikant Strategic Design", "Monatliche HR-Auswertungen und strategische Insights."),
        ("Praktikant Intralogistikplanung", "Analyse und Optimierung logistischer Prozesse."),
        ("Praktikant Logistic Engineering", "Analyse technischer Anforderungen und digitale Prozessunterstützung."),
        ("Praktikant Marketing", "Auswertung von Marketingmaßnahmen und datenbasiertes Marketing."),
        ("Bachelorarbeit Innovationsmanagement", "Analyse von Belieferungsprozessen und Automatisierungskonzepte."),
        ("Praktikant Online-Marketing SEM", "Fundierte Recherchen und Analysen für strategische Entscheidungen."),
        ("Werkstudent Qualitätsmanagement", "Aufbau von Datenbanken und Dashboards im Verbesserungsprozess."),
        ("Working Student Controlling", "Cloud revenue and backlog controlling."),
        ("Praktikant Projektleitung Qualität", "Qualitätsanalyse und Qualitätsreporting."),
        ("Praktikant Qualität und Logistik", "Implementierung neuer Prozesse und Analyse von Reklamationen."),
        ("Pflichtpraktikum Projektmanagement", "Prozessoptimierungen und Überwachung von Projektfortschritten."),
        ("Werkstudent Product Compliance Datenmanagement", "Definition von Datenstrukturen und Überwachung von Datenqualitätskennzahlen."),
    ],
)
def test_recall_first_cases_from_latest_workbook_audit(title, body):
    result = score_text(title, body, KEYWORDS, employment_type="Praktikum")
    assert result.band != "DROP"


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("Working Student - Strategic Finance", "Support the finance team with recurring reports."),
        ("Working Student - S4/HANA Public Cloud Revenue Recognition", "Role details are hosted externally."),
        ("Werkstudent After Sales und Business Applica", "Role details are hosted externally."),
        ("Werkstudent AI Engineering", "Du evaluierst LLMs und automatisierst Entwicklungsaufgaben."),
        ("Praktikant Leben Geschäftsprozesse", "Du entwickelst Daten, Prozesse und Schnittstellen weiter."),
        ("Praktikant Operations Customer Products & Implementation", "Transformation mit Data-diven-Mindset."),
        ("Werkstudent Personalentwicklung", "Sie werten Feedbacks zu Entwicklungsmaßnahmen aus."),
    ],
)
def test_false_negative_cases_from_2026_09_06_audit(title, body):
    result = score_text(title, body, KEYWORDS, employment_type="Praktikum")
    assert result.band != "DROP"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("PreMaster Programm – Data Engineer", "GRADUATE / TRAINEE"),
        ("Praktikkant*in Personalentwicklung", "PRAKTIKUM / INTERNSHIP"),
        ("Freiwilligendienst im FSJ / BFD", "OTHER / EARLY CAREER"),
        ("Rechtsreferendar Wahlstation", "OTHER / EARLY CAREER"),
    ],
)
def test_early_career_titles_are_routed_out_of_vollzeit(title, expected):
    result = score_text(title, "Data analytics and reporting.", KEYWORDS, employment_type="Vollzeit")
    assert result.employment_format == expected


def test_einkauf_seller_and_sales_titles_are_hard_excluded():
    for title in (
        "Werkstudent Einkauf",
        "Praktikum Verkäufer",
        "Werkstudent Vertrieb",
        "Account Manager",
        "Sales Manger",
        "Manager Energiebeschaffung",
        "Outbound Kampagnenmanagement",
    ):
        result = score_text(title, "Market research and data analysis.", KEYWORDS, employment_type="Vollzeit")
        assert result.drop_stage == "commercial_role_gate"


def test_talking_marketing_drops_but_research_marketing_passes():
    talking = score_text(
        "Praktikum Marketing",
        "Du führst Kundengespräche und übernimmst telefonische Ansprache.",
        KEYWORDS,
    )
    research = score_text(
        "Praktikum Marketing Analytics",
        "Market research, consumer insights and campaign data analysis.",
        KEYWORDS,
    )
    assert talking.drop_stage == "marketing_talking_gate"
    assert research.band != "DROP"


def test_marketing_events_without_research_or_analytics_is_dropped():
    result = score_text(
        "Praktikant Marketing und Events",
        "Unterstützung bei Events, Gästebetreuung und organisatorischen Aufgaben.",
        KEYWORDS,
    )
    assert result.drop_stage == "marketing_talking_gate"


def test_english_sap_consulting_is_eligible_but_strong_german_consulting_drops():
    english = score_text(
        "SAP Consultant Customer Service",
        "You will work with our team on SAP consulting projects and process transformation. "
        "English is the working language and German is not required.",
        KEYWORDS,
        employment_type="Vollzeit",
    )
    german = score_text(
        "SAP Consultant Customer Service",
        "SAP consulting and process transformation. Sehr gute Deutschkenntnisse erforderlich.",
        KEYWORDS,
        employment_type="Vollzeit",
    )
    assert english.band != "DROP"
    assert english.primary_cv_family.startswith("B08")
    assert german.drop_stage == "language_gate"
    assert german.reason == "german_c1_c2_or_native_required_for_consulting"


@pytest.mark.parametrize(
    "title",
    [
        "Kindheitspädagoge (m/w/d)",
        "Praxisanleiter Pflege (m/w/d)",
        "Callcenter Kundenbetreuer (m/w/d)",
        "IT Support / Systemadministrator (m/w/d)",
        "Operations Manager Logistik (m/w/d)",
        "Projektingenieur Verkehrsanlagen (m/w/d)",
        "Personalsachbearbeiter (m/w/d)",
    ],
)
def test_obvious_non_target_titles_drop_without_analytical_rescue(title):
    result = score_text(title, "Allgemeine operative Aufgaben und Teamarbeit.", KEYWORDS, employment_type="Vollzeit")
    assert result.drop_stage == "wrong_profession"


def test_title_exclusion_is_rescued_by_strong_analytics_body():
    result = score_text(
        "Projektingenieur Qualitätsmanagement",
        "Business intelligence, quality data analytics, root cause analysis and KPI dashboards.",
        KEYWORDS,
        employment_type="Vollzeit",
    )
    assert result.band != "DROP"
    assert result.primary_cv_family.startswith("B06")


def test_plain_ausbildung_title_is_not_accepted_via_structured_full_time():
    result = score_text(
        "Ausbildung zum Fachinformatiker",
        "Allgemeine Ausbildungsinhalte.",
        KEYWORDS,
        employment_type="Vollzeit",
    )
    assert result.drop_stage == "employment_gate"


def test_structured_full_time_is_not_misrouted_by_body_internship_wording():
    result = score_text(
        "Data Analyst",
        "Data analytics and KPI reporting. We also cooperate with internship programs.",
        KEYWORDS,
        employment_type="Vollzeit",
    )
    assert result.employment_format == "FULL-TIME / VOLLZEIT"
