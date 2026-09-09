from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


UMLAUT_TABLE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
FAMILY_PREFIXES = tuple(f"B{index:02d}" for index in range(1, 11))


@dataclass(frozen=True)
class ScoreResult:
    score: int
    raw_score: int
    band: str
    reason: str | None = None
    drop_stage: str = ""
    matched_term: str = ""
    employment_format: str = ""
    primary_cv_family: str = ""
    secondary_cv_family: str = ""
    family_scores: dict[str, int] = field(default_factory=dict)
    matched_terms: dict[str, tuple[str, ...]] = field(default_factory=dict)
    language_flags: tuple[str, ...] = ()
    eligibility_flags: tuple[str, ...] = ()
    other_flags: tuple[str, ...] = ()
    penalties: tuple[str, ...] = ()
    bonuses: tuple[str, ...] = ()

    @property
    def flags(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.language_flags, *self.eligibility_flags, *self.other_flags)))

    @property
    def language_flag(self) -> str:
        return " | ".join(self.language_flags)

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "raw_score": self.raw_score,
            "band": self.band,
            "reason": self.reason,
            "drop_stage": self.drop_stage,
            "matched_term": self.matched_term,
            "employment_format": self.employment_format,
            "primary_cv_family": self.primary_cv_family,
            "secondary_cv_family": self.secondary_cv_family,
            "family_scores": self.family_scores,
            "matched_terms": {key: list(value) for key, value in self.matched_terms.items()},
            "language_flags": list(self.language_flags),
            "eligibility_flags": list(self.eligibility_flags),
            "other_flags": list(self.other_flags),
            "penalties": list(self.penalties),
            "bonuses": list(self.bonuses),
        }


def load_keywords(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        data = json.load(handle)
    required = {
        "_meta", "employment_gate", "hard_exclusion", "marketing_talking_exclusion",
        "relevance_gate", "cv_families", "analysis_task",
        "methods", "tools_strong", "tools_basic_or_adjacent", "context_bonus",
        "title_boost", "kill_conditional", "penalty", "bonus", "eligibility_flags", "regex",
    }
    missing = required - data.keys()
    if missing:
        raise ValueError(f"Keyword dictionary is missing: {', '.join(sorted(missing))}")
    if len(data["cv_families"]) != 10:
        raise ValueError("Keyword dictionary must define exactly ten CV families (B01-B10)")
    return data


def normalize(value: str) -> str:
    value = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", value or "")
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value).lower().translate(UMLAUT_TABLE)
    return re.sub(r"\s+", " ", value).strip()


def _terms(group: Mapping[str, Any] | list[str]) -> tuple[str, ...]:
    values = group if isinstance(group, list) else group.get("terms", ())
    return tuple(value for value in (normalize(str(term)) for term in values) if value)


def _hits(text: str, group: Mapping[str, Any] | list[str]) -> tuple[str, ...]:
    hits: list[str] = []
    for term in _terms(group):
        # Very short tool/acronym terms must be complete tokens. Substring
        # matching made e.g. "git" match "digital" and "sem" match
        # unrelated German words.
        if len(term) <= 3 and re.fullmatch(r"[a-z0-9]+", term):
            matched = re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) is not None
        else:
            matched = term in text
        if matched:
            hits.append(term)
    return tuple(dict.fromkeys(hits))


def _regex_hit(text: str, pattern: str | None) -> str:
    if not pattern:
        return ""
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return normalize(match.group(0)) if match else ""


def _group_points(hits: tuple[str, ...], weight: int) -> int:
    return min(weight + len(hits) - 1, weight * 2) if hits else 0


def _looks_like_english_advertisement(text: str) -> bool:
    """Conservatively identify a substantially English job description."""
    tokens = re.findall(r"\b[a-z]+\b", text)
    if len(tokens) < 30:
        return False
    english_markers = {
        "and", "the", "you", "your", "with", "for", "our", "we", "are",
        "will", "this", "that", "from", "to", "of", "in", "as", "on",
        "team", "work", "skills", "experience", "responsibilities",
    }
    german_markers = {
        "und", "der", "die", "das", "mit", "fuer", "wir", "sie", "du",
        "deine", "ihre", "unser", "eine", "einer", "von", "zu", "im",
        "auf", "als", "kenntnisse", "aufgaben", "anforderungen",
    }
    english_count = sum(token in english_markers for token in tokens)
    german_count = sum(token in german_markers for token in tokens)
    return english_count >= 8 and english_count >= (german_count * 3 // 2 + 1)


def _english_environment_hits(text: str, group: Mapping[str, Any]) -> tuple[str, ...]:
    explicit_terms = tuple(
        term for term in _terms(group)
        if term not in {"fluent english", "sehr gute englischkenntnisse", "international team", "internationales team"}
    )
    hits = list(_hits(text, list(explicit_terms)))
    if _looks_like_english_advertisement(text):
        hits.append("english-language advertisement")
    return tuple(dict.fromkeys(hits))


def _drop(
    stage: str,
    reason: str,
    matched_term: str = "",
    matched: dict[str, tuple[str, ...]] | None = None,
    employment_format: str = "",
) -> ScoreResult:
    return ScoreResult(
        0,
        0,
        "DROP",
        reason,
        stage,
        matched_term,
        employment_format=employment_format,
        matched_terms=matched or {},
    )


def _special(
    band: str,
    reason: str,
    *,
    matched_term: str = "",
    matched: dict[str, tuple[str, ...]] | None = None,
    employment_format: str = "",
    primary_cv_family: str = "",
    secondary_cv_family: str = "",
    family_scores: dict[str, int] | None = None,
) -> ScoreResult:
    return ScoreResult(
        0,
        0,
        band,
        reason,
        "",
        matched_term,
        employment_format=employment_format,
        primary_cv_family=primary_cv_family,
        secondary_cv_family=secondary_cv_family,
        family_scores=family_scores or {},
        matched_terms=matched or {},
    )


def _family_id(key: str) -> str:
    return key.split("_", 1)[0]


def _employment_gate(title: str, body: str, structured: str, keywords: Mapping[str, Any]) -> tuple[str, tuple[str, ...]] | None:
    gate = keywords["employment_gate"]
    structured_n = normalize(structured)
    allowed_structured = _hits(structured_n, gate["structured_types_allowed"])
    title_hits = _hits(title, gate["required_title_terms"])
    regex_hit = _regex_hit(title, keywords["regex"].get("employment_title"))
    title_not_target_hits = _hits(title, gate["structured_types_not_target"])
    if title_not_target_hits and not title_hits and not regex_hit:
        return None
    body_hits = _hits(body, gate["required_body_strong_terms"])
    evidence = allowed_structured or title_hits or ((regex_hit,) if regex_hit else ()) or body_hits
    if not evidence:
        return None
    evidence_text = " ".join((*allowed_structured, *title_hits, regex_hit, *body_hits))
    title_evidence = " ".join((*title_hits, regex_hit))
    all_format_text = f"{title} {structured_n} {body}".strip()
    if any(term in title_evidence for term in ("werkstudent", "working student", "working-student")):
        employment_format = "WERKSTUDENT / WORKING STUDENT"
    elif any(term in title_evidence for term in ("praktikum", "praktikant", "praktikkant", "internship", "intern")):
        employment_format = "PRAKTIKUM / INTERNSHIP"
    elif any(term in title_evidence for term in ("bachelorarbeit", "masterarbeit", "abschlussarbeit", "thesis")):
        employment_format = "THESIS / STUDENT PROJECT"
    elif any(term in title_evidence for term in (
        "studentische hilfskraft", "wissenschaftliche hilfskraft", "student assistant",
        "student research assistant", "student employee", "hiwi",
    )):
        employment_format = "STUDENT ASSISTANT"
    elif any(term in title_evidence for term in ("graduate", "trainee", "berufseinsteiger", "premaster")):
        employment_format = "GRADUATE / TRAINEE"
    elif any(term in title for term in (
        "freiwilligendienst", "freiwilliges soziales jahr", "bundesfreiwilligendienst",
        " fsj", "fsj ", " bfd", "bfd ", "referendar", "anerkennungsjahr",
    )):
        employment_format = "OTHER / EARLY CAREER"
    elif any(term in allowed_structured for term in ("vollzeit", "full time", "full-time")):
        employment_format = "FULL-TIME / VOLLZEIT"
    elif any(term in allowed_structured for term in ("teilzeit", "part time", "part-time")):
        employment_format = "PART-TIME / TEILZEIT"
    elif any(term in evidence_text for term in ("werkstudent", "working student", "working-student")):
        employment_format = "WERKSTUDENT / WORKING STUDENT"
    elif any(term in evidence_text for term in ("praktikum", "praktikant", "internship", " intern")):
        employment_format = "PRAKTIKUM / INTERNSHIP"
    elif any(term in all_format_text for term in ("vollzeit", "full time", "full-time")):
        employment_format = "FULL-TIME / VOLLZEIT"
    elif any(term in all_format_text for term in ("teilzeit", "part time", "part-time")):
        employment_format = "PART-TIME / TEILZEIT"
    else:
        employment_format = "OTHER / EARLY CAREER"
    primary_format_terms = {
        "PRAKTIKUM / INTERNSHIP": ("praktikum", "praktikant", "praktikkant", "internship", "intern"),
        "WERKSTUDENT / WORKING STUDENT": ("werkstudent", "working student", "working-student"),
        "GRADUATE / TRAINEE": ("graduate", "trainee", "berufseinsteiger", "premaster"),
        "FULL-TIME / VOLLZEIT": ("vollzeit", "full time", "full-time"),
        "PART-TIME / TEILZEIT": ("teilzeit", "part time", "part-time"),
        "THESIS / STUDENT PROJECT": ("bachelorarbeit", "masterarbeit", "abschlussarbeit", "thesis"),
        "STUDENT ASSISTANT": (
            "studentische hilfskraft", "wissenschaftliche hilfskraft", "student assistant",
            "student research assistant", "student employee", "hiwi",
        ),
    }.get(employment_format, ())
    format_flags = _hits(all_format_text, gate.get("format_flag_terms", ()))
    not_target_flags = _hits(all_format_text, gate["structured_types_not_target"])
    other_formats = tuple(
        dict.fromkeys(
            term for term in (*format_flags, *not_target_flags)
            if not any(primary_term in term or term in primary_term for primary_term in primary_format_terms)
        )
    )
    return employment_format, other_formats


def _family_rank(family_points: dict[str, int], title: str, full_text: str) -> list[str]:
    priority: list[str] = []
    rules = [
        ("B08", title, ("consulting", "consultant", "beratung", "advisory")),
        ("B04", full_text, ("venture capital", "deal flow", "due diligence", "investment", "portfolio company")),
        ("B03", full_text, ("incubator", "inkubator", "founder support", "gruenderzentrum", "technologietransfer", "startup center")),
        ("B05", full_text, ("forecast", "demand planning", "inventory", "time series", "absatzplanung")),
        ("B09", full_text, ("web analytics", "ga4", "funnel", "conversion", "attribution")),
        ("B07", full_text, ("market research", "marktforschung", "competitive intelligence", "consumer insights")),
        ("B10", full_text, ("digital transformation", "digitale transformation", "process digitalization", "prozessdigitalisierung")),
    ]
    for family, text, needles in rules:
        if family_points.get(family, 0) and any(normalize(term) in text for term in needles):
            priority.append(family)
    ranked = sorted(family_points, key=lambda family: (-family_points[family], family))
    if ranked:
        best_points = family_points[ranked[0]]
        preferred = next((family for family in priority if best_points - family_points[family] <= 2), None)
        if preferred:
            ranked.remove(preferred)
            ranked.insert(0, preferred)
    return ranked


def _collect_flags(text: str, groups: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for name, group in groups.items():
        if name.startswith("_"):
            continue
        if _hits(text, group):
            values.append(str(group["flag"]))
    return tuple(dict.fromkeys(values))


def score_text(
    title: str,
    body: str,
    keywords: Mapping[str, Any],
    thresholds: Mapping[str, int] | None = None,
    *,
    employment_type: str = "",
) -> ScoreResult:
    thresholds = thresholds or {"priority": 12, "review": 6}
    normalized_title = normalize(title)
    normalized_body = normalize(body)
    full_text = f"{normalized_title} {normalized_body}".strip()
    gate = _employment_gate(normalized_title, normalized_body, employment_type, keywords)
    if gate is None:
        return _drop("employment_gate", "employment_format_not_target")
    employment_format, other_formats = gate

    academic_group = keywords["hard_exclusion"].get("academic_title_roles", {})
    academic_hits = _hits(normalized_title, academic_group)
    if academic_hits:
        return _special(
            "ACADEMISCHE",
            str(academic_group["reason"]),
            matched_term=academic_hits[0],
            matched={"hard_exclusion.academic_title_roles": academic_hits},
            employment_format=employment_format,
        )

    german_group = keywords["hard_exclusion"]["language_de_high"]
    german_hits = list(_hits(full_text, german_group))
    german_regex = _regex_hit(full_text, keywords["regex"].get("penalty_language_de_high"))
    if german_regex and german_regex not in german_hits:
        german_hits.append(german_regex)
    german_consulting_group = keywords["hard_exclusion"].get("german_consulting_title_roles", {})
    german_consulting_hits = _hits(normalized_title, german_consulting_group)
    if german_hits and german_consulting_hits:
        return _drop(
            "language_gate",
            str(german_consulting_group["reason"]),
            german_hits[0],
            {
                "hard_exclusion.language_de_high": tuple(german_hits),
                "hard_exclusion.german_consulting_title_roles": german_consulting_hits,
            },
            employment_format,
        )
    commercial_group = keywords["hard_exclusion"]["commercial_title_roles"]
    commercial_hits = _hits(normalized_title, commercial_group)
    if commercial_hits:
        return _drop("commercial_role_gate", str(commercial_group["reason"]), commercial_hits[0], {"hard_exclusion.commercial_title_roles": commercial_hits}, employment_format)

    body_strong: dict[str, tuple[str, ...]] = {}
    for key, family in keywords["cv_families"].items():
        body_strong[_family_id(key)] = _hits(normalized_body, family["strong_terms"])
    body_analysis = _hits(normalized_body, keywords["analysis_task"])
    wrong_hits: list[str] = []
    for name, group in keywords["kill_conditional"].items():
        if name.startswith("_") or name == "rule":
            continue
        wrong_hits.extend(_hits(normalized_title, group))
    if wrong_hits and not any(body_strong.values()) and not body_analysis:
        return _drop("wrong_profession", "wrong_profession_without_analytical_body", wrong_hits[0], {"kill_conditional": tuple(dict.fromkeys(wrong_hits))}, employment_format)

    matched: dict[str, tuple[str, ...]] = {}
    family_points: dict[str, int] = {}
    family_contributions: dict[str, int] = {}
    any_strong = False
    any_weak = False
    for key, family in keywords["cv_families"].items():
        family_id = _family_id(key)
        strong = _hits(full_text, family["strong_terms"])
        weak = _hits(full_text, family["weak_terms"])
        matched[f"{family_id}.strong"] = strong
        matched[f"{family_id}.weak"] = weak
        any_strong = any_strong or bool(strong)
        any_weak = any_weak or bool(weak)
        family_points[family_id] = len(strong) * 2 + len(weak)
        family_contributions[family_id] = _group_points(tuple(dict.fromkeys((*strong, *weak))), int(family["weight"]))

    analysis_hits = _hits(full_text, keywords["analysis_task"])
    method_hits = _hits(full_text, keywords["methods"])
    strong_tool_hits = _hits(full_text, keywords["tools_strong"])
    basic_tool_hits = _hits(full_text, keywords["tools_basic_or_adjacent"])
    matched.update({
        "analysis_task": analysis_hits, "methods": method_hits,
        "tools_strong": strong_tool_hits, "tools_basic_or_adjacent": basic_tool_hits,
    })
    english_hits = _english_environment_hits(normalized_body, keywords["bonus"]["english_environment"])
    marketing = keywords["marketing_talking_exclusion"]
    marketing_title_hits = _hits(normalized_title, marketing["title_terms"])
    marketing_talking_hits = _hits(full_text, marketing["talking_terms"])
    marketing_research = bool(
        matched.get("B07.strong") or matched.get("B09.strong")
    ) and bool(analysis_hits)
    if marketing_title_hits and marketing_talking_hits and not marketing_research:
        return _drop(
            "marketing_talking_gate",
            str(marketing["reason"]),
            marketing_talking_hits[0],
            {
                "marketing_title": marketing_title_hits,
                "marketing_talking": marketing_talking_hits,
            },
            employment_format,
        )

    ranked = _family_rank(family_points, normalized_title, full_text)
    ranked = [family for family in ranked if family_points[family] > 0]
    primary = ranked[0] if ranked else ""
    secondary = ""
    if len(ranked) > 1 and family_points[ranked[0]] - family_points[ranked[1]] <= 2:
        secondary = ranked[1]
    family_labels = {_family_id(key): str(value["label"]) for key, value in keywords["cv_families"].items()}
    primary_label = family_labels.get(primary, primary)
    secondary_label = family_labels.get(secondary, secondary)

    # Recall-first gate: a strong family signal is enough on its own. Generic
    # (weak) family wording still needs either an analytical task or a clearly
    # English advertisement. The user explicitly prefers reviewing extra jobs
    # over losing a potentially suitable one.
    relevance = any_strong or (any_weak and bool(analysis_hits or english_hits))
    if not relevance:
        first = next(
            iter(
                (
                    *analysis_hits,
                    *strong_tool_hits,
                    *basic_tool_hits,
                    *(term for name, terms in matched.items() if name.endswith(".strong") for term in terms),
                )
            ),
            "",
        )
        academic_research_terms = (
            "wissenschaftlicher mitarbeiter",
            "wissenschaftliche mitarbeiterin",
            "research associate",
            "research fellow",
            "postdoc",
            "postdoctoral researcher",
        )
        if any(term in normalized_title for term in academic_research_terms):
            return _special(
                "ACADEMISCHE",
                "academic_research_requires_manual_review",
                matched_term=next(term for term in academic_research_terms if term in normalized_title),
                matched=matched,
                employment_format=employment_format,
                primary_cv_family=primary_label,
                secondary_cv_family=secondary_label,
                family_scores=family_points,
            )
        if german_hits:
            return _drop(
                "language_gate",
                str(german_group["reason"]),
                german_hits[0],
                matched | {"hard_exclusion.language_de_high": tuple(german_hits)},
                employment_format,
            )
        return _special(
            "REVIEW",
            "uncertain_relevance_requires_manual_review",
            matched_term=first,
            matched=matched,
            employment_format=employment_format,
            primary_cv_family=primary_label,
            secondary_cv_family=secondary_label,
            family_scores=family_points,
        )

    # Classification tie-breakers may prefer a specific close family (for
    # example B09 over generic B06), but the numerical contribution remains
    # the single highest family score.
    raw_score = max(family_contributions.values(), default=0)
    for name in ("analysis_task", "methods", "tools_strong", "tools_basic_or_adjacent", "context_bonus"):
        hits = _hits(full_text, keywords[name])
        matched[name] = hits
        raw_score += _group_points(hits, int(keywords[name]["weight"]))
    title_hits = _hits(normalized_title, keywords["title_boost"])
    matched["title_boost"] = title_hits
    if title_hits:
        raw_score += int(keywords["title_boost"]["weight"])

    score = raw_score
    penalties: list[str] = []
    language_flags: list[str] = []
    high_language = False
    for name, group in keywords["penalty"].items():
        if name.startswith("_"):
            continue
        hits = list(_hits(full_text, group))
        if name == "language_de_high":
            regex_hit = _regex_hit(full_text, keywords["regex"].get("penalty_language_de_high"))
            if regex_hit and regex_hit not in hits:
                hits.append(regex_hit)
        if name == "language_de_medium" and high_language:
            hits = []
        matched[f"penalty.{name}"] = tuple(hits)
        if hits:
            score += int(group["value"])
            penalties.append(f"{group['flag']} ({int(group['value']):+d})")
            if name.startswith("language_de_"):
                language_flags.append(str(group["flag"]))
                high_language = high_language or name == "language_de_high"

    bonuses: list[str] = []
    for name, group in keywords["bonus"].items():
        if name.startswith("_"):
            continue
        if name == "english_environment":
            hits = english_hits
        else:
            hits = _hits(normalized_title if name == "exact_student_format_in_title" else full_text, group)
        matched[f"bonus.{name}"] = hits
        if hits:
            score += int(group["value"])
            bonuses.append(f"{group['flag']} ({int(group['value']):+d})")
            if name == "english_environment":
                language_flags.append(str(group["flag"]))

    eligibility = _collect_flags(full_text, keywords["eligibility_flags"])
    other_flags = [f"ALSO MENTIONS: {term}" for term in other_formats]
    soft_hits = _hits(full_text, keywords.get("soft_flag", {}))
    other_flags.extend(soft_hits)
    for name, pattern in keywords["regex"].items():
        if not name.startswith("flag_"):
            continue
        for match in re.finditer(pattern, full_text, flags=re.IGNORECASE):
            captured = next((value for value in reversed(match.groups()) if value), match.group(0))
            other_flags.append(f"{name.removeprefix('flag_').upper()}: {captured.strip()}")

    priority_at = int(thresholds.get("priority", thresholds.get("apply", 12)))
    review_at = int(thresholds.get("review", 6))
    if german_hits:
        band, reason = "DE REQUIRED", str(german_group["reason"])
    elif score >= priority_at:
        band, reason = "PRIORITY", None
    elif score >= review_at or (language_flags and raw_score >= review_at):
        band, reason = "REVIEW", None
    else:
        band, reason = "REVIEW", "score_below_review_requires_manual_review"
    audit_term = next(
        (term for group, terms in matched.items() if not group.startswith(("penalty.", "bonus.")) for term in terms),
        "",
    )
    return ScoreResult(
        score=score, raw_score=raw_score, band=band, reason=reason,
        drop_stage="",
        matched_term=audit_term if reason else "",
        employment_format=employment_format,
        primary_cv_family=primary_label, secondary_cv_family=secondary_label,
        family_scores=family_points, matched_terms=matched,
        language_flags=tuple(dict.fromkeys(language_flags)), eligibility_flags=eligibility,
        other_flags=tuple(dict.fromkeys(other_flags)), penalties=tuple(penalties), bonuses=tuple(bonuses),
    )
