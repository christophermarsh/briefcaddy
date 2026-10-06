"""Document-processing helper."""

from __future__ import annotations

import difflib
import json
import re
from functools import lru_cache
from typing import Any

from .names import fold_name
import schema_path


# Supporting implementation.
# Supporting implementation.
DOMINANT_POPULATION, DOMINANT_RATIO = 20_000, 10


@lru_cache(maxsize=None)
def _index() -> dict[str, str]:
    """Document-processing helper."""
    out = {}
    for path in (p for p in schema_path.glob("geo") if len(p.stem) == 2):  # Supporting implementation.
        head = json.loads(path.read_text(encoding="utf-8"))
        for key in (head["country"], head["iso2"], head["iso3"]):
            out[key] = head["iso2"]
    return out


@lru_cache(maxsize=None)
def _data(iso2: str) -> dict[str, Any]:
    d = json.loads(schema_path.path("geo", iso2).read_text(encoding="utf-8"))
    names = {}
    for name, aliases in d["regions"].items():
        names[name] = name
        for a in aliases:
            names.setdefault(a, name)
    if iso2 == "BR":
        from .places import BR_UF

        names.update(BR_UF)  # Supporting implementation.
    d["_names"] = names
    return d


def code(country: str | None) -> str | None:
    """Document-processing helper."""
    if not country:
        return None
    from .places import country_name

    folded = fold_name(country)
    return _index().get(country.strip().upper()) or _index().get(folded) or _index().get(country_name(country) or "")


def countries() -> list[str]:
    return sorted({iso for iso in _index().values()})


def country_name_of(country: str) -> str | None:
    iso = code(country)
    return _data(iso)["country"] if iso else None


def region_word(country: str) -> str:
    iso = code(country)
    return _data(iso)["region_word"] if iso else "province"


_AROUND = re.compile(r"^(?:DEPARTAMENTO|DEPTO|DPTO|PROVINCIA|PROV|PCIA|REGION|ESTADO|EDO|DEPARTEMENT|DEPARTMENT|DISTRICT|DISTRITO|PROVINCE|"
                     r"STATE)\s+(?:DE\s+L'|DEL\s+|DE\s+|DU\s+|D'|OF\s+)?|\s+(?:DEPARTMENT|PROVINCE|REGION|STATE|DEPARTAMENTO|PROVINCIA)$")


def region(country: str | None, text: str | None, fuzzy: bool = True) -> str | None:
    """Document-processing helper."""
    iso = code(country)
    if not iso or not text:
        return None
    names = _data(iso)["_names"]
    f = fold_name(text).strip()
    for candidate in (f, _AROUND.sub("", f).strip(), _AROUND.sub("", _AROUND.sub("", f).strip()).strip()):
        if candidate in names:
            return names[candidate]
    if fuzzy and len(f) >= 6:  # Supporting implementation.
        close = difflib.get_close_matches(f, list(names), n=2, cutoff=0.8)
        if close and (len(close) == 1 or names[close[0]] == names[close[1]]
                      or difflib.SequenceMatcher(None, f, close[1]).ratio() < difflib.SequenceMatcher(None, f, close[0]).ratio() - 0.05):
            return names[close[0]]
    return None


def is_region(country: str | None, text: str | None) -> bool:
    return region(country, text, fuzzy=False) is not None


def regions_for_place(country: str | None, place: str | None) -> list[str]:
    """Document-processing helper."""
    iso = code(country)
    if not iso or not place:
        return []
    if iso == "BR":
        from .places import br_states_for_city

        return br_states_for_city(place)
    return [r for r, _ in _data(iso)["places"].get(fold_name(place), [])]


def likely_region(country: str | None, place: str | None) -> tuple[str, str] | None:
    """Document-processing helper."""
    iso = code(country)
    if not iso or not place:
        return None
    name = fold_name(place)
    if iso == "BR":
        states = regions_for_place(iso, name)
        return (states[0], f"{name} is a municipality only in {states[0]} (IBGE list of Brazilian municipalities)") if len(states) == 1 else None
    found = _data(iso)["places"].get(name, [])
    country_name = _data(iso)["country"]
    if len(found) == 1:
        return found[0][0], f"{name} is in {found[0][0]} -- the only place of that name in {country_name} (GeoNames)"
    if len(found) > 1:
        (top, pop), (_, second) = found[0], found[1]
        if pop >= DOMINANT_POPULATION and pop >= DOMINANT_RATIO * max(second, 1):
            return top, (f"{name} is in {top} -- the {country_name} city of that name (population {pop:,}); "
                         f"smaller places called {name} exist in {len(found) - 1} other {_plural(_data(iso)['region_word'])} (GeoNames)")
    return None


def _plural(word: str) -> str:
    return word[:-1] + "ies" if word.endswith("y") else word + "s"


def postal(country: str | None) -> dict[str, Any] | None:
    """Document-processing helper."""
    iso = code(country)
    return _data(iso).get("postal") if iso else None


def postal_format(country: str | None) -> str | None:
    """Document-processing helper."""
    info = postal(country)
    if not info:
        return None
    m = re.fullmatch(r"\\d\{(\d+)\}", info["pattern"])  # Supporting implementation.
    return {"AR": "a letter, 4 digits and 3 letters (like C1425ABC), or 4 digits", "HT": "HT and 4 digits (like HT6110)",
            "GF": "5 digits starting 973"}.get(code(country), f"{m.group(1)} digits" if m else None)


def normalize_postal(country: str | None, text: str) -> str:
    """Document-processing helper."""
    iso = code(country)
    t = re.sub(r"[\s.-]", "", (text or "").upper())
    if iso == "AR" and re.fullmatch(r"[A-Z]\d{4}[A-Z]{3}", t):
        return t
    if iso == "HT":
        digits = re.sub(r"\D", "", t)
        return f"HT{digits}" if len(digits) == 4 else t
    return re.sub(r"\D", "", t) if re.search(r"\d", t) else t


def postal_ok(country: str | None, text: str) -> bool | None:
    """Document-processing helper."""
    info = postal(country)
    if not info or not text:
        return None
    return bool(re.fullmatch(info["pattern"], normalize_postal(country, text)))


def postal_search(country: str | None, text: str) -> str | None:
    """Document-processing helper."""
    info = postal(country)
    if not info or not text:
        return None
    if code(country) == "BR":
        from .places import CEP

        m = CEP.search(text)
        return m.group(1) + m.group(2) if m else None
    m = re.search(rf"(?<![A-Z0-9])(?:{info['pattern']})(?![A-Z0-9])", text.upper().replace("-", ""))
    return normalize_postal(country, m.group(0)) if m else None


def region_from_postal(country: str | None, text: str | None) -> str | None:
    """Document-processing helper."""
    iso = code(country)
    info = postal(iso)
    if not iso or not info or not text:
        return None
    if iso == "BR":
        from .places import br_state_from_cep

        return br_state_from_cep(text)
    t = normalize_postal(iso, text)
    if iso == "AR" and re.fullmatch(r"[A-Z]\d{4}[A-Z]{3}", t):
        return info.get("cpa_letters", {}).get(t[0])
    digits = re.sub(r"\D", "", t)
    prefixes = info.get("prefixes", {})
    for n in range(len(digits), 0, -1):
        if digits[:n] in prefixes:
            return prefixes[digits[:n]]
    return None


def split_place(country: str | None, text: str | None) -> tuple[str, str] | None:
    """Document-processing helper."""
    if not text or not code(country):
        return None
    parts = [p.strip() for p in re.split(r"\s*[,/]\s*|\s+[-–]\s+|(?<=[a-zà-ÿ])-(?=[A-Z]{2}\b)", text) if p.strip()]
    if len(parts) < 2:
        m = re.fullmatch(r"(.+?)\s*-\s*([A-Za-z]{2})\.?", text.strip())
        parts = [m.group(1), m.group(2)] if m else parts
    if len(parts) < 2:
        return None
    from .places import country_name

    if country_name(parts[-1]) and len(parts) >= 3:  # Supporting implementation.
        parts = parts[:-1]
    reg = region(country, parts[-1], fuzzy=False)
    city = fold_name(parts[-2]) if reg else None
    return (city, reg) if reg and city and city != reg else ((city, reg) if reg and city else None)


def which_country(place: str | None) -> list[str]:
    """Document-processing helper."""
    if not place:
        return []
    name = fold_name(place)
    out = []
    for iso in countries():
        d = _data(iso)
        if name in d["_names"] or any(pop >= DOMINANT_POPULATION for _, pop in d["places"].get(name, [])):
            out.append(d["country"])
    return out
