"""Document-processing helper."""

from __future__ import annotations

import re
from itertools import permutations

from .base import ExtractedField, find_after_label, find_value_after_label, normalize_date
from .names import fold_name, looks_like_name, same_person_name
from .places import BR_UF, br_city_uf

_COUNTRY_HEADERS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"FEDERATIVE REPUBLIC OF BRAZIL|FEDERAL REPUBLIC OF BRAZIL", re.I), "BRAZIL"),
    (re.compile(r"REP[ÚU]BLICA FEDERATIVA DO BRASIL", re.I), "BRAZIL"),
]

_GENDER_WORDS = {"FEMALE": "F", "MALE": "M", "FEMININO": "F", "MASCULINO": "M"}

# Supporting implementation.
_BLOCK_END = re.compile(r"(?i)^\W*(AV[OÓ]S|GRANDPARENTS|G[EÊ]MEO|TWIN|DATA D[OE] REGISTRO|DATE OF REGISTRATION|"
                        r"TESTEMUNHAS?\b|WITNESS(?:ES)?\b|DECLARANTE\b|DECLARANT\b|TRADUTOR\b|TRANSLATOR\b|"
                        r"OBSERVA|N[UÚ]MERO DA|REGISTRATION NUMBER|REGISTRO CIVIL|NOME E MATR)")


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()]


def _clean_person(raw: str) -> str:
    """Document-processing helper."""
    raw = re.sub(r"^[^A-Za-zÀ-ÿ]+", "", raw)
    raw = re.split(r",|\bfrom\b|\bnatural de\b|\bNACIONALIDADE\b", raw, maxsplit=1, flags=re.I)[0]
    return fold_name(raw)


def _birthplace_of(raw: str) -> str:
    # Supporting implementation.
    # Supporting implementation.
    birthplace = re.split(r"(?i)\bresidente\b|\bresiding\b|\bresident of\b", raw, maxsplit=1)[0]
    place = br_city_uf(birthplace)
    return f"{place[0]}, {place[1]}" if place else ""


def _filiation_entries(text: str) -> list[tuple[str, str]]:
    """Document-processing helper."""
    lines = _lines(text)
    found: list[list[tuple[str, str]]] = []
    for i, line in enumerate(lines):
        if not re.search(r"(?i)\bFILIA[CÇG]+[AÃ]O\b|\bFILIATION\b", line):
            continue
        block: list[str] = []
        for nxt in lines[i + 1:i + 8]:
            if _BLOCK_END.search(nxt):
                break
            if nxt:
                block.append(nxt)
        joined = " ".join(block)
        # Supporting implementation.
        parts = re.split(r"(?<=[A-Z]{2})\.?,?\s+e\s+(?=[A-Z])", joined) if "NATURALIDADE" in joined.upper() else block
        entries = []
        for part in parts:
            name = _clean_person(part)
            if looks_like_name(name):
                entries.append((name, _birthplace_of(part)))
            if len(entries) == 2:
                break
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        if entries:
            found.append(entries)
    return _merge_versions(found)


def _merge_versions(versions: list[list[tuple[str, str]]]) -> list[tuple[str, str]]:
    """Document-processing helper."""
    if not versions:
        return []
    merged = list(versions[0])
    for other in versions[1:]:
        pairings = [order for order in permutations(range(len(other)))
                    if len(other) == len(merged) and all(
                        same_person_name(name, other[order[j]][0])
                        for j, (name, _) in enumerate(merged))]
        if len(pairings) != 1:
            return []  # Supporting implementation.
        for j, (name, place) in enumerate(merged):
            twin = other[pairings[0][j]]
            if not place and twin[1]:
                merged[j] = (name, twin[1])
    return merged


def _labeled_parents(text: str) -> dict[str, tuple[str, str]]:
    out = {}
    for role, labels in (("father", (r"Father'?s Name", r"\bPAI:")), ("mother", (r"Mother'?s Name", r"\bM[ÃA]E:"))):
        raw = find_after_label(text, *labels)
        if raw and looks_like_name(_clean_person(raw)):
            out[role] = (_clean_person(raw), _birthplace_of(raw))
    return out


def _grandparents(text: str) -> list[str]:
    """Document-processing helper."""
    lines = _lines(text)
    names: list[str] = []
    for i, line in enumerate(lines):
        if not re.search(r"(?i)^\W*(AV[OÓ]S\b|GRANDPARENTS|PATERNAL GRANDPARENTS|MATERNAL GRANDPARENTS)", line):
            continue
        tail = re.sub(r"(?i)^\W*(PATERNAL |MATERNAL )?(AV[OÓ]S|GRANDPARENTS):?", "", line)
        # Supporting implementation.
        # Supporting implementation.
        one_per_line = "Grandparents" in line  # Supporting implementation.
        for chunk in [tail] + lines[i + 1:i + (2 if one_per_line else 9)]:
            if re.search(r"(?i)GRANDPARENTS|G[EÊ]MEO|TWIN|DATA|DATE|NUMERO|REGISTR|MATR", chunk) and chunk is not tail:
                continue
            if chunk is not tail and not re.search(r"&|\se\s*[A-Z]|\bE [A-Z]", chunk) and "Grandparents" not in line:
                continue  # Supporting implementation.
            for piece in re.split(r"\s*&\s*|\s+e\s*(?=[A-Z])|\be(?=[A-Z]{3})", chunk):
                name = fold_name(piece)
                if looks_like_name(name):
                    names.append(name)
    return list(dict.fromkeys(names))


def _birth_city(text: str) -> tuple[str, str] | None:
    """Document-processing helper."""
    raw = find_after_label(text, "City and State of Birth")
    if raw and "-" in raw:
        city, state = (p.strip() for p in raw.split("-", 1))
        return fold_name(city), fold_name(state)
    lines = _lines(text)
    for i, line in enumerate(lines):
        if re.search(r"(?i)CITY OF BIRTH|MUNIC[IÍ]PIO DE\s*NASCIMENTO", line):
            place = br_city_uf(" ".join(lines[i + 1:i + 3]))
            if place:
                return place
    return None


def _naturalidade(text: str) -> tuple[str, str] | None:
    lines = _lines(text)
    for i, line in enumerate(lines):
        if re.search(r"(?i)\bNATURALIDADE\b|\bBIRTHPLACE\b", line) and not re.search(r"(?i)FILIA", line):
            place = br_city_uf(" ".join(lines[i + 1:i + 2]))
            if place:
                return place
    return None


def _registrant_name(text: str) -> str | None:
    for i, line in enumerate(_lines(text)):
        if re.fullmatch(r"(?i)\W*(NOME|NAME):?\W*", line) or re.match(r"(?i)^\W*(NOME|NAME):?\s+\S", line):
            tail = re.sub(r"(?i)^\W*(NOME|NAME):?", "", line).strip()
            candidates = [tail] + _lines(text)[i + 1:i + 3]
            for candidate in candidates:
                name = fold_name(re.sub(r"[^A-Za-zÀ-ÿ '.-]", " ", candidate))
                if looks_like_name(name) and "CPF" not in name:
                    return name
    return None


def extract(text: str) -> list[ExtractedField]:
    fields: list[ExtractedField] = []
    brazilian = any(p.search(text) for p, _ in _COUNTRY_HEADERS)

    # Supporting implementation.
    # Supporting implementation.
    dob_raw = find_value_after_label(text, "Date and Time of Birth", r"\d{1,2}/\d{1,2}/\d{4}", window=100)
    dob = normalize_date(dob_raw) if dob_raw else None
    if dob is None:
        m = re.search(r"(?is)DAY\s+MONTH\s+YEAR.{0,120}?\b(\d{2})\s+(\d{2})\s+(\d{4})\b", text)
        if m and 1 <= int(m.group(2)) <= 12:
            dob_raw, dob = " ".join(m.groups()), f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    if dob:
        fields.append(ExtractedField("applicant.dob", dob_raw, dob, 0.9))

    name = _registrant_name(text)
    if name:
        fields.append(ExtractedField("applicant.birth_certificate_name", name, name, 0.8))

    place = _birth_city(text)
    if place:
        city, state = place
        fields.append(ExtractedField("applicant.birth_city", f"{city} - {state}", city, 0.9))
        fields.append(ExtractedField("applicant.birth_state", f"{city} - {state}", state, 0.9))
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    registered = find_after_label(text, "Place of Registration", "COUNTY OF REGISTRATION[^\\n]*\\n")
    registered_place = br_city_uf(registered or "")
    if registered_place:
        fields.append(ExtractedField("applicant.birth_cert.registration_city", registered or "", registered_place[0], 0.8))
    natural = _naturalidade(text)
    if natural:
        fields.append(ExtractedField("applicant.birth_cert.naturalidade", f"{natural[0]} - {natural[1]}", natural[0], 0.8))

    labeled = _labeled_parents(text)
    if labeled:
        for role, (pname, pplace) in labeled.items():
            fields.append(ExtractedField(f"applicant.birth_cert.{role}_name", pname, pname, 0.9))
            if pplace:
                fields.append(ExtractedField(f"applicant.birth_cert.{role}_birthplace", pplace, pplace, 0.85))
    else:
        for slot, (pname, pplace) in zip(("parent_a", "parent_b"), _filiation_entries(text)):
            fields.append(ExtractedField(f"applicant.birth_cert.{slot}_name", pname, pname, 0.85))
            if pplace:
                fields.append(ExtractedField(f"applicant.birth_cert.{slot}_birthplace", pplace, pplace, 0.85))

    grandparents = _grandparents(text)
    if grandparents:
        joined = "; ".join(grandparents)
        fields.append(ExtractedField("applicant.birth_cert.grandparents", joined, joined, 0.7))

    for pattern, country in _COUNTRY_HEADERS:
        if pattern.search(text):
            fields.append(ExtractedField("applicant.country_of_birth", pattern.pattern, country, 0.9))
            break

    gender_raw = find_after_label(text, "Gender")
    if not gender_raw:
        m = re.search(r"(?im)\b(FEMALE|MALE|FEMININO|MASCULINO)\b", text)
        gender_raw = m.group(1) if m else None
    if gender_raw:
        normalized = _GENDER_WORDS.get(fold_name(gender_raw).split(" ")[0] if gender_raw.strip() else "")
        if normalized:
            fields.append(ExtractedField("applicant.sex", gender_raw.strip(), normalized, 0.9))

    if not brazilian:
        # Supporting implementation.
        fields = [f for f in fields if not f.fact_key.endswith(("birthplace", "naturalidade", "birth_state"))]
    return fields


__all__ = ["extract", "BR_UF"]
