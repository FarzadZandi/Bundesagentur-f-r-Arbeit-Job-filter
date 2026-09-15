from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from countrystatecity_countries import get_cities_of_country
from geopy.distance import geodesic


HEILBRONN_COORDINATES = (49.13995, 9.22054)

GERMAN_STATE_NAMES = {
    "BW": "Baden-Württemberg",
    "BY": "Bayern",
    "BE": "Berlin",
    "BB": "Brandenburg",
    "HB": "Bremen",
    "HH": "Hamburg",
    "HE": "Hessen",
    "NI": "Niedersachsen",
    "MV": "Mecklenburg-Vorpommern",
    "NW": "Nordrhein-Westfalen",
    "RP": "Rheinland-Pfalz",
    "SL": "Saarland",
    "SN": "Sachsen",
    "ST": "Sachsen-Anhalt",
    "SH": "Schleswig-Holstein",
    "TH": "Thüringen",
}

STATE_HINTS = {
    "baden wurttemberg": "BW",
    "wurttemberg": "BW",
    "baden": "BW",
    "bayern": "BY",
    "oberbayern": "BY",
    "niederbayern": "BY",
    "oberfranken": "BY",
    "mittelfranken": "BY",
    "oberpfalz": "BY",
    "schwaben": "BY",
    "hessen": "HE",
    "niedersachsen": "NI",
    "thuringen": "TH",
    "mecklenburg": "MV",
    "sachsen anhalt": "ST",
    "sachsen": "SN",
    "westfalen": "NW",
    "rheinland": "NW",
    "niederrhein": "NW",
    "ruhr": "NW",
    "pfalz": "RP",
    "saar": "SL",
    "holstein": "SH",
    "breisgau": "BW",
    "saale": "ST",
    "spree": "BB",
    "wumme": "NI",
}

CITY_ALIASES = {
    "munchen": "munich",
}

GENERIC_LOCATIONS = {
    "",
    "deutschland",
    "germany",
    "verschiedene arbeitsorte",
    "mehrere arbeitsorte",
    "bundesweit",
    "remote",
    "homeoffice",
}


@dataclass(frozen=True)
class LocationInfo:
    federal_state: str = ""
    distance_from_heilbronn_km: float | None = None


@dataclass(frozen=True)
class _CityPoint:
    name: str
    state_code: str
    latitude: float
    longitude: float


def _normalize(value: str) -> str:
    value = value.casefold().replace("ß", "ss")
    value = "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


def _name_variants(value: str) -> list[str]:
    raw_variants = [value.strip(), value.split(",", 1)[0].strip()]
    without_parentheses = re.sub(r"\s*\([^)]*\)\s*", " ", value).strip()
    raw_variants.extend((without_parentheses, without_parentheses.split(",", 1)[0].strip()))
    normalized: list[str] = []
    for raw in raw_variants:
        candidate = _normalize(raw)
        if not candidate:
            continue
        normalized.append(candidate)
        normalized.append(re.sub(r"^lutherstadt\s+", "", candidate))
        normalized.append(re.sub(r"^st\s+", "sankt ", candidate))
        normalized.append(
            re.sub(
                r"\s+(?:bei|am|an der|an den|im|in der|auf der|auf dem)\s+.*$",
                "",
                candidate,
            )
        )
    return list(dict.fromkeys(CITY_ALIASES.get(item, item) for item in normalized if item))


def _state_hint(value: str) -> str | None:
    normalized = _normalize(value)
    for hint, state_code in STATE_HINTS.items():
        if re.search(rf"\b{re.escape(hint)}\b", normalized):
            return state_code
    return None


@lru_cache(maxsize=1)
def _city_index() -> dict[str, tuple[_CityPoint, ...]]:
    index: dict[str, list[_CityPoint]] = {}
    for city in get_cities_of_country("DE"):
        try:
            point = _CityPoint(
                name=city.name,
                state_code=city.state_code,
                latitude=float(city.latitude),
                longitude=float(city.longitude),
            )
        except (TypeError, ValueError):
            continue
        for variant in _name_variants(city.name):
            index.setdefault(variant, []).append(point)
    return {key: tuple(values) for key, values in index.items()}


def _resolve_city(value: str) -> _CityPoint | None:
    normalized = _normalize(value)
    if normalized in GENERIC_LOCATIONS:
        return None
    hint = _state_hint(value)
    for variant in _name_variants(value):
        candidates = list(_city_index().get(variant, ()))
        if hint:
            candidates = [candidate for candidate in candidates if candidate.state_code == hint]
        if not candidates:
            continue
        states = {candidate.state_code for candidate in candidates}
        if len(states) == 1:
            exact = [candidate for candidate in candidates if _normalize(candidate.name) == variant]
            return exact[0] if exact else candidates[0]
    return None


@lru_cache(maxsize=4096)
def enrich_location(location: str) -> LocationInfo:
    normalized = _normalize(location)
    if normalized in GENERIC_LOCATIONS:
        return LocationInfo()

    points: list[_CityPoint] = []
    for part in (item.strip() for item in location.split(";") if item.strip()):
        point = _resolve_city(part)
        if point and point not in points:
            points.append(point)
    if not points:
        hint = _state_hint(location)
        if not hint:
            hint = next(
                (
                    code
                    for code, name in GERMAN_STATE_NAMES.items()
                    if _normalize(name) == normalized
                ),
                None,
            )
        return LocationInfo(federal_state=GERMAN_STATE_NAMES.get(hint, "") if hint else "")

    state_names = list(
        dict.fromkeys(
            GERMAN_STATE_NAMES.get(point.state_code, point.state_code)
            for point in points
            if point.state_code
        )
    )
    distance = min(
        geodesic(HEILBRONN_COORDINATES, (point.latitude, point.longitude)).km
        for point in points
    )
    return LocationInfo(
        federal_state="; ".join(state_names),
        distance_from_heilbronn_km=round(distance, 1),
    )
