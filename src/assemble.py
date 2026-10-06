"""Application helper with evidence-bound inputs."""

from __future__ import annotations

import re
from typing import Any

import arrival
from extract.names import fold_name, same_person_name, shares_given_name, split_name, surname_tokens
from extract.places import city_country, country_name
from factgraph import FactGraph
import clock

NOT_APPLICABLE = "NOT APPLICABLE"
_US_STATE_CODES = None


def _us_codes() -> set[str]:
    global _US_STATE_CODES
    if _US_STATE_CODES is None:
        from questionnaire.handwriting import US_STATES

        _US_STATE_CODES = set(US_STATES.values())
    return _US_STATE_CODES


def _value(graph: FactGraph, key: str) -> Any:
    fact = graph.get(key)
    return fact.value if fact is not None and fact.status == "resolved" and fact.value not in (None, "") else None


def _src(graph: FactGraph, key: str):
    """Application helper with evidence-bound inputs."""
    fact = graph.get(key)
    if fact is None or not fact.sources:
        return None
    s = fact.sources[0]
    return s.doc_id, s.doc_type, key


def _put(graph: FactGraph, key: str, value: str, origin, raw: str, tier: int, confidence: float = 0.85) -> None:
    if origin is None or value in (None, ""):
        return
    graph.add_source(key, doc_id=origin[0], doc_type=origin[1], raw_value=raw, normalized_value=value,
                     confidence=confidence, tier=tier, from_facts=[origin[2]] if len(origin) > 2 and origin[2] != key else None)


def _needs_check(graph: FactGraph, *keys: str) -> None:
    """Application helper with evidence-bound inputs."""
    for key in keys:
        fact = graph.get(key)
        if fact is not None and fact.derived_by is None:
            fact.tier = 3


# Generic implementation note.


def _birth_cert_parents(graph: FactGraph) -> dict[str, tuple[str, Any, str]]:
    """Application helper with evidence-bound inputs."""
    out: dict[str, tuple[str, Any, str]] = {}
    if hasattr(graph, "_subject_roles"):
        # Generic implementation note.
        # Generic implementation note.
        candidates: dict[str, list] = {}
        for slot in ("mother", "father", "parent_a", "parent_b"):
            key = f"applicant.birth_cert.{slot}_name"
            fact = graph.get(key)
            for source in fact.sources if fact else []:
                role = graph._subject_roles.get(source.doc_id, {}).get(slot)
                if role not in {"mother", "father"}:
                    continue
                place = graph.get(f"applicant.birth_cert.{slot}_birthplace")
                places = {s.normalized_value for s in place.sources if s.doc_id == source.doc_id} if place else set()
                candidates.setdefault(role, []).append((source.normalized_value, (source.doc_id, source.doc_type, key),
                                                        next(iter(places)) if len(places) == 1 else ""))
        for role, entries in candidates.items():
            if len({entry[0] for entry in entries}) == 1:
                out[role] = entries[0]
        return out
    for role in ("mother", "father"):
        name = _value(graph, f"applicant.birth_cert.{role}_name")
        if name:
            out[role] = (name, _src(graph, f"applicant.birth_cert.{role}_name"), _value(graph, f"applicant.birth_cert.{role}_birthplace") or "")
    slots = {}
    for slot in ("parent_a", "parent_b"):
        name = _value(graph, f"applicant.birth_cert.{slot}_name")
        if name:
            slots[slot] = (name, _src(graph, f"applicant.birth_cert.{slot}_name"), _value(graph, f"applicant.birth_cert.{slot}_birthplace") or "")
    said = {role: [v for v in (_value(graph, f"questionnaire.{role}_name"), _value(graph, f"questionnaire.{role}_birth_name")) if v]
            for role in ("mother", "father")}
    for slot, entry in slots.items():
        roles = [role for role in ("mother", "father") if any(shares_given_name(entry[0], s) for s in said[role])]
        if len(roles) == 1 and roles[0] not in out:
            out[roles[0]] = entry
    unassigned = [e for s, e in slots.items() if all(e is not v for v in out.values())]
    missing = [r for r in ("mother", "father") if r not in out]
    if len(unassigned) == 1 and len(missing) == 1 and slots:
        out[missing[0]] = unassigned[0]  # Generic implementation note.
    return out


def _marriage_parents_of_applicant(graph: FactGraph, party: str | None, role: str | None = None) -> list[tuple[str, Any]]:
    if hasattr(graph, "_subject_roles"):
        out = []
        for doc, slots in graph._subject_roles.items():
            for column in ("party_a", "party_b"):
                if slots.get(column) != "applicant":
                    continue
                for n in (1, 2):
                    if slots.get(f"{column}_parent{n}") != role:
                        continue
                    key = f"marriage.{column}.parent{n}_name"
                    fact = graph.get(key)
                    out.extend((s.normalized_value, (doc, s.doc_type, key)) for s in fact.sources if s.doc_id == doc) if fact else None
        return out
    if party is None:
        return []
    out = []
    for n in (1, 2):
        key = f"marriage.{party}.parent{n}_name"
        if _value(graph, key):
            out.append((_value(graph, key), _src(graph, key)))
    return out


def _family_hints(graph: FactGraph) -> set[str]:
    hints = surname_tokens(*(_value(graph, "applicant.birth_cert.grandparents") or "").split(";"))
    family = _value(graph, "applicant.family_name") or ""
    hints |= set(fold_name(family).split())
    return hints


def _record_name(graph: FactGraph, prefix: str, full: str, supports: list[tuple[Any, str, int]], hints: set[str]) -> None:
    """Application helper with evidence-bound inputs."""
    split = split_name(full, hints)
    for origin, raw, tier in supports:
        note = f"{raw}. Split: {split.why}"
        _put(graph, f"{prefix}_given_name", split.given, origin, note, tier)
        _put(graph, f"{prefix}_family_name", split.family, origin, note, tier)
    if not split.proven:
        _needs_check(graph, f"{prefix}_given_name", f"{prefix}_family_name")


def assemble_parents(graph: FactGraph) -> None:
    hints = _family_hints(graph)
    bc = _birth_cert_parents(graph)
    party = applicant_party(graph)
    married_parents = _marriage_parents_of_applicant(graph, party)
    for role in ("mother", "father"):
        said_legal = _value(graph, f"questionnaire.{role}_name")
        said_birth = _value(graph, f"questionnaire.{role}_birth_name")
        q_origin = _src(graph, f"questionnaire.{role}_name") or _src(graph, f"questionnaire.{role}_birth_name")
        bc_name, bc_origin, bc_place = bc.get(role, (None, None, ""))
        if hasattr(graph, "_subject_roles"):
            named = _marriage_parents_of_applicant(graph, party, role)
            mc = named[0] if named and len({n for n, _ in named}) == 1 else None
        else:
            mc = next(((n, o) for n, o in married_parents
                       if any(shares_given_name(n, x) for x in (said_legal, said_birth, bc_name) if x)), None)

        # Generic implementation note.
        supports: list[tuple[Any, str, int]] = []
        legal = fold_name(said_legal) if said_legal else None
        if legal:
            supports.append((q_origin, f"client wrote {said_legal!r}", 3))
            if mc and same_person_name(mc[0], legal):
                supports.append((mc[1], f"marriage certificate lists this parent as {mc[0]!r}", 1))
            if bc_name and same_person_name(bc_name, legal):
                supports.append((bc_origin, f"birth certificate lists this parent as {bc_name!r}", 1))
        elif mc:
            # Generic implementation note.
            # Generic implementation note.
            # Generic implementation note.
            # Generic implementation note.
            legal = next((fold_name(x) for x in (bc_name, said_birth) if x and same_person_name(x, mc[0])), mc[0])
            agreeing = [(o, f"{what} lists this parent as {n!r}") for n, o, what in (
                (mc[0], mc[1], "marriage certificate"), (bc_name, bc_origin, "birth certificate"))
                if n and same_person_name(n, legal)]
            if said_birth and same_person_name(said_birth, legal):
                agreeing.append((_src(graph, f"questionnaire.{role}_birth_name"), f"client wrote {said_birth!r} (name at birth, same)"))
            # Generic implementation note.
            tier = 1 if sum(1 for _, why in agreeing if "certificate" in why) >= 2 else 3
            supports += [(o, why + ("" if tier == 1 else " (no current name from the client: confirm)"), tier) for o, why in agreeing]
        elif bc_name:
            legal = bc_name
            supports.append((bc_origin, f"name at the client's birth, from the birth certificate ({bc_name!r}): confirm it is "
                                        "still this parent's legal name", 3))
        if legal:
            _record_name(graph, f"applicant.{role}", legal, supports, hints)

        # Generic implementation note.
        birth = fold_name(said_birth) if said_birth else None
        if birth and legal:
            if same_person_name(birth, legal):
                note = f"The client gave the same name at birth ({said_birth}), so the firm writes NOT APPLICABLE"
                for key in ("birth_given_name", "birth_family_name", "birth_middle_name"):
                    _put(graph, f"applicant.{role}_{key}", NOT_APPLICABLE, q_origin, note, 3)
            else:
                birth_supports = [(q_origin, f"client wrote {said_birth!r} as the name at birth", 3)]
                if bc_name and same_person_name(bc_name, birth):
                    birth_supports.append((bc_origin, f"birth certificate lists this parent as {bc_name!r}", 1))
                _record_name(graph, f"applicant.{role}_birth", birth, birth_supports, hints)
        elif legal and _value(graph, f"questionnaire.{role}_name_changed") == "No":
            # Generic implementation note.
            note = "The client said this parent's name never changed, so the firm writes NOT APPLICABLE"
            for key in ("birth_given_name", "birth_family_name", "birth_middle_name"):
                _put(graph, f"applicant.{role}_{key}", NOT_APPLICABLE, _src(graph, f"questionnaire.{role}_name_changed"), note, 3)

        # Generic implementation note.
        if bc_place and bc_origin:
            _put(graph, f"applicant.{role}_country_of_birth", "BRAZIL", bc_origin,
                 f"birth certificate: born in {bc_place}", 1)
            _put(graph, f"applicant.{role}_birth_city", bc_place.split(",")[0], bc_origin, f"birth certificate: {bc_place}", 1)


# Generic implementation note.


def applicant_party(graph: FactGraph) -> str | None:
    """Application helper with evidence-bound inputs."""
    found = applicant_marriages(graph)
    parties = {party for _doc, party, proven in found if proven} or {party for _doc, party, _proven in found}
    return parties.pop() if len(parties) == 1 else None


def applicant_marriages(graph: FactGraph) -> list[tuple[str, str, bool]]:
    """Application helper with evidence-bound inputs."""
    if hasattr(graph, "_subject_roles"):
        confirmed = []
        for doc, roles in graph._subject_roles.items():
            for party in ("party_a", "party_b"):
                if roles.get(party) != "applicant":
                    continue
                fact = graph.get(f"marriage.{party}.name")
                if fact and any(source.doc_id == doc for source in fact.sources):
                    confirmed.append((doc, party, True))
        return confirmed
    names = [n for n in (
        " ".join(x for x in (_value(graph, "applicant.given_name"), _value(graph, "applicant.family_name")) if x),
        _value(graph, "applicant.birth_certificate_name"),
    ) if n] + _names_each_document_gives(graph)
    dob = _value(graph, "applicant.dob")
    columns: dict[str, dict[tuple[str, str], Any]] = {}
    for party in ("party_a", "party_b"):
        for part in ("name", "dob"):
            fact = graph.get(f"marriage.{party}.{part}")
            for s in fact.sources if fact is not None else []:
                if s.normalized_value not in (None, ""):
                    columns.setdefault(s.doc_id, {}).setdefault((party, part), s.normalized_value)
    out = []
    for doc, read in columns.items():
        scores = {}
        for party in ("party_a", "party_b"):
            name = read.get((party, "name"))
            if not name:
                continue
            printed_dob = read.get((party, "dob"))
            if dob is not None and printed_dob and printed_dob != dob:
                scores[party] = 0  # Generic implementation note.
                continue
            score = 2 * any(same_person_name(str(name), n) for n in names)
            score += dob is not None and printed_dob == dob
            scores[party] = score
        if len(scores) != 2:
            continue
        a, b = scores["party_a"], scores["party_b"]
        party = "party_a" if a > b and a >= 2 else "party_b" if b > a and b >= 2 else None
        if party:
            out.append((doc, party, scores[party] == 3))
    return out


def _names_each_document_gives(graph: FactGraph) -> list[str]:
    """Application helper with evidence-bound inputs."""
    parts: dict[str, dict[str, str]] = {}
    for part in ("given_name", "family_name"):
        fact = graph.get(f"applicant.{part}")
        for s in fact.sources if fact is not None else []:
            if s.normalized_value not in (None, ""):
                parts.setdefault(s.doc_id, {}).setdefault(part, str(s.normalized_value))
    return [f"{p['given_name']} {p['family_name']}" for p in parts.values() if p.get("given_name") and p.get("family_name")]


_RESIDENCE = re.compile(r"^(?P<street>.+?),\s*(?:(?:#|APT\.?|APARTMENT|UNIT)\s*(?P<apt>[A-Z0-9-]+),\s*)?(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})$")


def _residence_tie_break(graph: FactGraph, party: str, origin) -> None:
    """Application helper with evidence-bound inputs."""
    from questionnaire.handwriting import usps_street

    m = _RESIDENCE.match(_value(graph, f"marriage.{party}.residence") or "")
    if not m or origin is None:
        return
    certificate = {"street": usps_street(m["street"]), "apt": m["apt"], "city": fold_name(m["city"]), "state": m["state"]}
    raw = f"marriage certificate ({_value(graph, 'applicant.marriage_date') or 'date unknown'}): residence {m.group(0)}"
    for part, value in certificate.items():
        fact = graph.get(f"applicant.physical_{part}")
        if not value or fact is None:
            continue
        said = {str(s.normalized_value) for s in fact.sources}
        if value not in said:
            continue  # Generic implementation note.
        was_conflict = fact.status == "conflict"
        _put(graph, f"applicant.physical_{part}", value, origin, raw, 1)
        if was_conflict:
            graph.resolve_conflict(f"applicant.physical_{part}", value,
                                   f"the client's answers disagree ({' / '.join(sorted(said))}); the marriage certificate shows {value}",
                                   "marriage certificate")


def assemble_marriage(graph: FactGraph) -> None:
    from questionnaire.handwriting import usps_street

    place = _value(graph, "applicant.marriage_place")
    place_origin = _src(graph, "applicant.marriage_place")
    if place and "," in place:
        city, _, state = (p.strip() for p in place.rpartition(","))
        _put(graph, "applicant.marriage_city", city, place_origin, place, 1)
        if state in _us_codes():
            _put(graph, "applicant.marriage_state", state, place_origin, place, 1)
            _put(graph, "applicant.marriage_country", "USA", place_origin, f"{place} (a U.S. state)", 1)

    party = applicant_party(graph)
    if party is None:
        return
    spouse = "party_b" if party == "party_a" else "party_a"
    origin = _src(graph, f"marriage.{spouse}.name")
    _residence_tie_break(graph, party, _src(graph, f"marriage.{party}.residence"))

    # Generic implementation note.
    own_dob = _value(graph, f"marriage.{party}.dob")
    if own_dob:
        _put(graph, "applicant.dob", own_dob, _src(graph, f"marriage.{party}.dob"), "marriage certificate", 1, 0.85)
    number = _value(graph, f"marriage.{party}.marriage_number")
    if number:
        _put(graph, "applicant.times_married", number, _src(graph, f"marriage.{party}.marriage_number"), f"marriage certificate: this is marriage number {number}", 1)
    own_place = _value(graph, f"marriage.{party}.birthplace")
    if own_place:
        _put(graph, "applicant.marriage_cert_birthplace", own_place, _src(graph, f"marriage.{party}.birthplace"), own_place, 1)

    name = _value(graph, f"marriage.{spouse}.name")
    if name:
        explicit = [_value(graph, f"marriage.{spouse}.parent{n}_surname") or "" for n in (1, 2)]
        parents = [_value(graph, f"marriage.{spouse}.parent{n}_name") or "" for n in (1, 2)]
        hints = surname_tokens(*parents) | {t for s in explicit for t in fold_name(s).split()}
        split = split_name(name, hints)
        note = f"marriage certificate: {name}. Split: {split.why}"
        _put(graph, "applicant.spouse_given_name", split.given, origin, note, 1)
        _put(graph, "applicant.spouse_family_name", split.family, origin, note, 1)
        if not split.proven:
            _needs_check(graph, "applicant.spouse_given_name", "applicant.spouse_family_name")
    dob = _value(graph, f"marriage.{spouse}.dob")
    if dob:
        _put(graph, "applicant.spouse_dob", dob, _src(graph, f"marriage.{spouse}.dob"), "marriage certificate", 1)
    birthplace = _value(graph, f"marriage.{spouse}.birthplace")
    parsed = city_country(birthplace) if birthplace else None
    if parsed:
        _put(graph, "applicant.spouse_country_of_birth", parsed[1], _src(graph, f"marriage.{spouse}.birthplace"), f"marriage certificate: born in {birthplace}", 1)
        _put(graph, "applicant.spouse_birth_city", parsed[0], _src(graph, f"marriage.{spouse}.birthplace"), f"marriage certificate: born in {birthplace}", 1)

    residence = _value(graph, f"marriage.{spouse}.residence")
    m = _RESIDENCE.match(residence or "")
    if not m:
        return
    street, apt, city, state = usps_street(m["street"]), m["apt"], fold_name(m["city"]), m["state"]
    raw = f"marriage certificate ({_value(graph, 'applicant.marriage_date') or 'date unknown'}): residence {residence}"
    _put(graph, "applicant.spouse_street", street, origin, raw, 1)
    if apt:
        _put(graph, "applicant.spouse_apt", apt, origin, raw, 1)
    _put(graph, "applicant.spouse_city", city, origin, raw, 1)
    _put(graph, "applicant.spouse_state", state, origin, raw, 1)
    if state in _us_codes():
        _put(graph, "applicant.spouse_address_country", "USA", origin, raw, 1)
    same_as_applicant = (
        usps_street(_value(graph, "applicant.physical_street") or "") == street
        and fold_name(_value(graph, "applicant.physical_city") or "") == city
        and (_value(graph, "applicant.physical_state") or "") == state
        and (_value(graph, "applicant.physical_apt") or None) == apt
    )
    if same_as_applicant:
        why = "same residence as the client on the marriage certificate: copied from the client's current address"
        for part in ("zip", "unit_type"):
            value = _value(graph, f"applicant.physical_{part}")
            if value:
                _put(graph, f"applicant.spouse_{part}", value, _src(graph, f"applicant.physical_{part}"), why, 3)


# Generic implementation note.


def _fill_from_client(graph: FactGraph) -> None:
    """Application helper with evidence-bound inputs."""
    dob = _value(graph, "questionnaire.dob")
    if dob and graph.get("applicant.dob") is None:
        _put(graph, "applicant.dob", dob, _src(graph, "questionnaire.dob"), f"client wrote {dob}", 3)
    said = _value(graph, "questionnaire.marriage_date")
    if said and graph.get("applicant.marriage_date") is None:
        _put(graph, "applicant.marriage_date", said, _src(graph, "questionnaire.marriage_date"), f"client wrote {said}", 3)
    place = _value(graph, "questionnaire.marriage_place")
    if place and graph.get("applicant.marriage_city") is None and "," in place:
        city, _, rest = place.partition(",")
        origin = _src(graph, "questionnaire.marriage_place")
        _put(graph, "applicant.marriage_city", fold_name(city), origin, f"client wrote {place!r}", 3)
        country = country_name(rest)
        if country:
            _put(graph, "applicant.marriage_country", country, origin, f"client wrote {place!r}", 3)
    birth = _value(graph, "questionnaire.birth_place")
    if birth and graph.get("applicant.birth_city") is None:
        _put(graph, "applicant.birth_city", fold_name(birth.split(",")[0]), _src(graph, "questionnaire.birth_place"),
             f"client wrote {birth!r}", 3)


def assemble_entry_passport(graph: FactGraph) -> None:
    """Application helper with evidence-bound inputs."""
    number = _value(graph, "applicant.travel_document_number")
    if not number:
        return
    fact = graph.get(f"folder.passport.{re.sub(r'[^A-Z0-9]', '', str(number).upper())}")
    if fact is None or fact.status != "resolved" or not fact.sources:
        return
    issuer, _, expiry = str(fact.value).partition("|")
    origin = (fact.sources[0].doc_id, fact.sources[0].doc_type)
    raw = f"passport {number} (the number on the I-94)"
    _put(graph, "applicant.travel_document_country", issuer, origin, raw, 1)
    _put(graph, "applicant.travel_document_expiry", expiry, origin, raw, 1)


def mark_earlier_i485(graph: FactGraph) -> None:
    """Application helper with evidence-bound inputs."""
    for key, fact in list(graph.all_facts().items()):
        earlier = (key.startswith("folder.uscis_case.") and str(fact.value).startswith("I-485")) or \
                  (key == "folder.uscis_forms" and any(str(s.normalized_value) == "I485" for s in fact.sources))
        if earlier and fact.sources:
            s = fact.sources[0]
            graph.add_source("folder.i485_filed_before", s.doc_id, s.doc_type, str(fact.value), "Yes", 0.9, from_facts=[key])


def _us_date(iso: str | None) -> str:
    return f"{iso[5:7]}/{iso[8:10]}/{iso[:4]}" if iso and re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso) else (iso or "?")


def _entries(graph: FactGraph, prefix: str, count: int, parts: tuple[str, ...]) -> list[dict[str, Any]]:
    """Application helper with evidence-bound inputs."""
    out = []
    for k in range(1, count + 1):
        values = {part: _value(graph, f"{prefix}{k}_{part}") for part in parts}
        if any(values.values()):
            origin = next((_src(graph, f"{prefix}{k}_{part}") for part in parts if _src(graph, f"{prefix}{k}_{part}")), None)
            # Generic implementation note.
            # Generic implementation note.
            # Generic implementation note.
            out.append(values | {"_k": k, "_origin": origin and (*origin[:2], f"{prefix}{k}")})
    return out


def _address_line(a: dict[str, Any]) -> str:
    place = ", ".join(x for x in (a.get("street"), a.get("apt") and f"APT {a['apt']}", a.get("city"),
                                   a.get("state") or a.get("province"), a.get("zip") or a.get("postal_code"),
                                   a.get("country")) if x)
    return f"{place} (from {_us_date(a.get('date_from'))} to {_us_date(a.get('date_to'))})"


def assemble_history(graph: FactGraph) -> None:
    """Application helper with evidence-bound inputs."""
    from datetime import date

    from extract.names import split_name

    # Generic implementation note.
    since = _value(graph, "applicant.physical_address_since")
    if since and re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
        today = clock.today()
        five_years_ago = today.replace(year=today.year - 5)
        answer = "Yes" if date.fromisoformat(since) <= five_years_ago else "No"
        _put(graph, "applicant.lived_at_address_5yrs", answer, _src(graph, "applicant.physical_address_since"),
             f"living here since {_us_date(since)}", 3)

    priors = _entries(graph, "questionnaire.prior_address", 7,
                      ("street", "apt", "city", "state", "zip", "province", "postal_code", "country", "date_from", "date_to"))
    if priors:
        first = priors[0]
        raw = f"questionnaire address history, line {first['_k']}: {_address_line(first)}"
        for part in ("street", "apt", "city", "state", "zip", "province", "postal_code", "country", "date_from", "date_to"):
            if first.get(part):
                _put(graph, f"applicant.prior_address_{part}", first[part], first["_origin"], raw, 3)
        if first.get("apt"):
            _put(graph, "applicant.prior_address_unit_type", "APT", first["_origin"], raw, 3)

    children = _entries(graph, "questionnaire.child", 4, ("name", "a_number", "dob", "country"))
    hints = set(fold_name(_value(graph, "applicant.family_name") or "").split())
    for slot, child in enumerate(children[:2], start=1):
        raw = f"questionnaire, child {child['_k']}: {child['name']}"
        if child.get("name"):
            split = split_name(child["name"], hints)
            _put(graph, f"applicant.child{slot}_given_name", split.given, child["_origin"], f"{raw}. Split: {split.why}", 3)
            _put(graph, f"applicant.child{slot}_family_name", split.family, child["_origin"], f"{raw}. Split: {split.why}", 3)
        for part in ("a_number", "dob", "country"):
            if child.get(part):
                _put(graph, f"applicant.child{slot}_{part}", child[part], child["_origin"], raw, 3)

    # Generic implementation note.
    # Generic implementation note.
    # Generic implementation note.
    # Generic implementation note.
    # Generic implementation note.
    employers = _entries(graph, "questionnaire.prior_employer", 9,
                         ("employer", "occupation", "street", "city", "state", "zip", "country", "date_from", "date_to"))
    # Generic implementation note.
    # Generic implementation note.
    blocks = [(*_p14_spot("applicant.prior_address_street"), _p14_text("PRIOR ADDRESS (CONTINUED)", [*_address_lines(a), _dates_line(a)]), a["_origin"])
              for a in _chronological(priors[1:])]
    blocks += [(*_p14_spot("applicant.employer1_name"), _p14_text("EMPLOYMENT AND EDUCATIONAL HISTORY (CONTINUED)", [
                   _employer_line(e), *_address_lines(e), _dates_line(e)]), e["_origin"])
               for e in _chronological(employers)]
    more_numbers = _other_a_numbers(graph)[1:]
    if more_numbers:
        origin = _src(graph, "questionnaire.other_a_numbers")
        blocks.append((*_p14_spot("applicant.other_a_numbers"), _p14_text("OTHER A-NUMBERS (CONTINUED)", [", ".join(f"A-{n}" for n in more_numbers)]),
                       origin and (*origin[:2], "questionnaire.other_a_numbers")))
    spouses = _prior_spouses(graph)
    blocks += [(*_p14_spot("applicant.prior_spouse_family_name"), _p14_text("PRIOR MARRIAGE (CONTINUED)", [
                   sp.get("name") or "", ", ".join(x for x in (sp.get("date_married") and f"MARRIED {_us_date(sp['date_married'])}",
                                                              sp.get("date_ended") and f"ENDED {_us_date(sp['date_ended'])}",
                                                              sp.get("how_ended") and _HOW_ENDED.get(str(sp["how_ended"]).upper(), "OTHER")) if x)]), sp["_origin"])
               for sp in spouses[1:]]
    orgs = _entries(graph, "questionnaire.organization", 9, ("name", "city", "state", "country", "nature", "involvement", "date_from", "date_to"))
    blocks += [(*_p14_spot("applicant.part9.org1_name"), _p14_text("ORGANIZATIONS (CONTINUED)", [
                   o.get("name") or "", _place_line(o), " - ".join(x for x in (o.get("nature"), o.get("involvement")) if x), _dates_line(o)]), o["_origin"])
               for o in orgs[2:]]
    blocks += [(*_p14_spot("applicant.total_children"), _p14_text("OTHER CHILDREN", [
                   c.get("name") or "", ", ".join(x for x in (c.get("dob") and f"BORN {_us_date(c['dob'])}", c.get("country") and f"IN {c['country']}",
                                                             c.get("a_number") and f"A-{c['a_number']}") if x)]), c["_origin"])
               for c in children[2:]]
    for n, (page, part, item, text, origin) in enumerate(blocks, start=1):
        for key, value in (("page", page), ("part", part), ("item", item), ("text", text)):
            if value:
                _put(graph, f"applicant.p14_block{n}_{key}", value, origin,
                     f"composed from {origin[2]}" if origin and len(origin) > 2 else "composed from the questionnaire", 3)


P14_LINE = 80  # Generic implementation note.


def _p14_spot(field_key: str) -> tuple[str, str, str]:
    """Application helper with evidence-bound inputs."""
    from fill.where import NotFound, edition_of, where_is

    try:
        return where_is(edition_of(), field_key)
    except NotFound:
        return "", "", ""


def _chronological(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Application helper with evidence-bound inputs."""
    return sorted(entries, key=lambda e: (e.get("date_from") or "9999", e["_k"]))


def _street_line(a: dict[str, Any]) -> str:
    return " ".join(x for x in (a.get("street"), a.get("apt") and f"APT {a['apt']}") if x)


def _place_line(a: dict[str, Any]) -> str:
    region = " ".join(x for x in (a.get("state") or a.get("province"), a.get("zip") or a.get("postal_code")) if x)
    return ", ".join(x for x in (a.get("city"), region, a.get("country")) if x)


def _employer_line(e: dict[str, Any]) -> str:
    """Application helper with evidence-bound inputs."""
    employer, occupation = e.get("employer"), e.get("occupation")
    if employer and occupation and fold_name(employer) == fold_name(occupation):
        return employer
    return " - ".join(x for x in (employer, occupation) if x)


def _address_lines(a: dict[str, Any]) -> list[str]:
    """Application helper with evidence-bound inputs."""
    street, place = _street_line(a), _place_line(a)
    folded = fold_name(street)
    if street and all(fold_name(x) in folded for x in (a.get("city"), a.get("zip") or a.get("postal_code")) if x):
        country = a.get("country") or ""
        place = country if country and fold_name(country) not in folded else ""
    together = ", ".join(x for x in (street, place) if x)
    return [together] if len(together) <= P14_LINE else [street, place]


def _dates_line(a: dict[str, Any]) -> str:
    return "  ".join(x for x in (a.get("date_from") and f"FROM: {_us_date(a['date_from'])}", a.get("date_to") and f"TO: {_us_date(a['date_to'])}") if x)


def _p14_text(title: str, lines: list[str]) -> str:
    """Application helper with evidence-bound inputs."""
    import textwrap

    out = [title]
    for line in lines:
        if line:
            out += textwrap.wrap(line, P14_LINE) or [line]
    return "\n".join(out)


_HOW_ENDED = {"DIVORCED": "DIVORCED", "WIDOWED": "SPOUSE DECEASED", "ANNULLED": "ANNULLED", "OTHER": "OTHER"}


def _other_a_numbers(graph: FactGraph) -> list[str]:
    said = _value(graph, "questionnaire.other_a_numbers") or ""
    return [n.zfill(9) for n in re.findall(r"\d{7,9}", re.sub(r"[\s-]", "", said))]


def _prior_spouses(graph: FactGraph) -> list[dict[str, Any]]:
    """Application helper with evidence-bound inputs."""
    entries = _entries(graph, "questionnaire.prior_spouse", 5, ("name", "dob", "birth_country", "citizenship", "date_married", "date_ended",
                                                                "ended_city", "ended_state", "ended_country", "how_ended"))
    return sorted(entries, key=lambda e: e.get("date_ended") or e.get("date_married") or "", reverse=True)


def assemble_answers(graph: FactGraph) -> None:
    """Application helper with evidence-bound inputs."""
    from extract.names import split_name

    dobs = [d for d in (_value(graph, f"questionnaire.other_dob{n}_date") for n in range(1, 4)) if d]
    if dobs:
        _put(graph, "applicant.other_dobs", ", ".join(_us_date(d) for d in dobs), _src(graph, "questionnaire.other_dob1_date"),
             "questionnaire: other dates of birth", 3)
    numbers = _other_a_numbers(graph)
    if numbers:  # Generic implementation note.
        _put(graph, "applicant.other_a_numbers", numbers[0], _src(graph, "questionnaire.other_a_numbers"),
             f"questionnaire: {_value(graph, 'questionnaire.other_a_numbers')}", 3)

    # Generic implementation note.
    # Generic implementation note.
    how = _value(graph, "questionnaire.entry_how")
    border = _value(graph, "questionnaire.entered_via_border")
    origin = _src(graph, "questionnaire.entry_how") or _src(graph, "questionnaire.entered_via_border")
    i94_class = _value(graph, "applicant.i94_class_of_admission")
    if how == "border" or (how is None and border == "Yes"):
        _put(graph, "applicant.last_arrival_manner", "WITHOUT ADMISSION OR PAROLE", origin, "the client: entered across the border, not inspected", 3)
        # Generic implementation note.
        _put(graph, "applicant.part9.pt9line75", "Yes", origin, "the client: entered across the border, not inspected", 3)
    elif how == "parole":
        _put(graph, "applicant.last_arrival_manner", "PAROLED", origin, "the client: paroled", 3)
        if i94_class:
            _put(graph, "applicant.last_arrival_paroled_as", i94_class, _src(graph, "applicant.i94_class_of_admission"), "class of admission on the I-94", 1)
    elif how == "inspected" or (how is None and border == "No" and i94_class):
        _put(graph, "applicant.last_arrival_manner", "ADMITTED", origin or _src(graph, "applicant.i94_class_of_admission"), "admitted at the last arrival", 3)
        said = _value(graph, "questionnaire.visa_type")
        if i94_class:
            _put(graph, "applicant.last_arrival_admitted_as", str(i94_class).upper(), _src(graph, "applicant.i94_class_of_admission"), "class of admission on the I-94", 1)
        elif said:
            _put(graph, "applicant.last_arrival_admitted_as", str(said).upper(), _src(graph, "questionnaire.visa_type"), "the client: visa used", 3)
    elif how == "other":
        _put(graph, "applicant.last_arrival_manner", "OTHER", origin, "the client: other", 3)
        explained = _value(graph, "questionnaire.entry_other")
        if explained:
            _put(graph, "applicant.last_arrival_other", explained.upper()[:120], _src(graph, "questionnaire.entry_other"), "the client's explanation", 3)
    if _value(graph, "applicant.entered_without_inspection") == "Yes":  # Generic implementation note.
        _put(graph, "applicant.part9.pt9line75", "Yes", _src(graph, "applicant.entered_without_inspection") or origin,
             "Notice to Appear: not admitted or paroled", 2)

    # Generic implementation note.
    spouses = _prior_spouses(graph)
    if spouses:
        sp, raw = spouses[0], "questionnaire: previous marriage"
        if sp.get("name"):
            split = split_name(sp["name"])
            _put(graph, "applicant.prior_spouse_given_name", split.given, sp["_origin"], f"{raw}. Split: {split.why}", 3)
            _put(graph, "applicant.prior_spouse_family_name", split.family, sp["_origin"], f"{raw}. Split: {split.why}", 3)
        for part, key in (("dob", "dob"), ("birth_country", "country_of_birth"), ("citizenship", "citizenship"), ("date_married", "marriage_date"),
                          ("date_ended", "ended_date"), ("ended_city", "ended_city"), ("ended_state", "ended_state"), ("ended_country", "ended_country")):
            if sp.get(part):
                _put(graph, f"applicant.prior_spouse_{key}", sp[part], sp["_origin"], raw, 3)
        if sp.get("how_ended"):
            _put(graph, "applicant.prior_spouse_how_ended", _HOW_ENDED.get(str(sp["how_ended"]).upper(), "OTHER"), sp["_origin"], raw, 3)

    # Generic implementation note.
    orgs = _entries(graph, "questionnaire.organization", 9, ("name", "city", "state", "country", "nature", "involvement", "date_from", "date_to"))
    for n, org in enumerate(orgs[:2], start=1):
        for part in ("name", "city", "state", "country", "nature", "involvement", "date_from", "date_to"):
            if org.get(part):
                _put(graph, f"applicant.part9.org{n}_{part}", org[part], org["_origin"], "questionnaire: organization", 3)

    # Generic implementation note.
    phone = re.sub(r"\D", "", _value(graph, "questionnaire.phone") or "")
    if len(phone) == 11 and phone.startswith("1"):
        phone = phone[1:]
    if len(phone) == 10:
        for key in ("applicant.daytime_phone", "applicant.mobile_phone"):
            _put(graph, key, phone, _src(graph, "questionnaire.phone"), "the client's mobile number", 3)
    if _value(graph, "questionnaire.email"):
        _put(graph, "applicant.email", _value(graph, "questionnaire.email").lower(), _src(graph, "questionnaire.email"), "the client's email", 3)


def assemble(graph: FactGraph) -> None:
    mark_earlier_i485(graph)
    assemble_answers(graph)
    arrival.settle(graph)  # Generic implementation note.
    assemble_history(graph)
    assemble_entry_passport(graph)
    assemble_marriage(graph)
    _fill_from_client(graph)
    assemble_parents(graph)
    settle_names(graph)


def settle_names(graph: FactGraph) -> None:
    """Application helper with evidence-bound inputs."""
    from name_events import settle

    settle(graph)


# Generic implementation note.

# Generic implementation note.
_ALWAYS = [
    ("applicant.birth_city", "City or town of birth: on the birth certificate, or ask the client."),
    ("applicant.last_arrival_manner", "Part 1 item 11: how the client last arrived (admitted, paroled, or without inspection). The I-94 shows "
                                      "an admission; otherwise ask the client."),
    ("applicant.daytime_phone", "Part 10: the client's daytime phone number. Ask the client (the scanned questionnaire doesn't have it)."),
    ("applicant.mother_given_name", "Parent 1 (mother): legal given name. Ask the client."),
    ("applicant.mother_family_name", "Parent 1 (mother): legal family name. Ask the client."),
    ("applicant.father_given_name", "Parent 2 (father): legal given name. Ask the client (or enter UNKNOWN if not known)."),
    ("applicant.father_family_name", "Parent 2 (father): legal family name. Ask the client (or enter UNKNOWN if not known)."),
    ("applicant.mother_birth_family_name", "Parent 1 (mother): family name at birth. Ask the client; enter NOT APPLICABLE if it never changed."),
    ("applicant.father_birth_family_name", "Parent 2 (father): family name at birth. Ask the client; enter NOT APPLICABLE if it never changed."),
    ("applicant.eye_color", "Eye color, not found on the questionnaire or an ID; ask the client."),
    ("applicant.physical_street", "Current home address (street): ask the client."),
    ("applicant.physical_city", "Current home address (city): ask the client."),
    ("applicant.physical_state", "Current home address (state): the client didn't write it; ask the client."),
    ("applicant.physical_zip", "Current home address (ZIP code): the client didn't write it; ask the client."),
    ("applicant.mother_dob", "Parent 1 (mother): date of birth. Ask the client."),
    ("applicant.father_dob", "Parent 2 (father): date of birth. Ask the client."),
]
_IF_MARRIED = [
    ("applicant.spouse_family_name", "Current spouse's family name: from the marriage certificate, or ask the client."),
    ("applicant.spouse_given_name", "Current spouse's given name: from the marriage certificate, or ask the client."),
    ("applicant.spouse_dob", "Current spouse's date of birth: ask the client."),
    ("applicant.spouse_country_of_birth", "Current spouse's country of birth: ask the client."),
    ("applicant.spouse_street", "Current spouse's current address: ask the client."),
    ("applicant.spouse_zip", "Current spouse's ZIP code: ask the client."),
    ("applicant.marriage_date", "Date of the current marriage: from the marriage certificate, or ask the client."),
    ("applicant.marriage_city", "Place of the current marriage: from the marriage certificate, or ask the client."),
    ("applicant.spouse_in_military", "Is the spouse a current member of the U.S. armed forces or Coast Guard? The questionnaire "
                                     "doesn't ask: ask the client."),
    ("applicant.spouse_a_number", "Spouse's A-Number, if any: ask the client. Choose 'Leave blank' if the spouse has none."),
]


# Generic implementation note.
# Generic implementation note.
PART9_FOLLOWUPS = {
    "applicant.part9.pt8line29": ("applicant.part9.pt8line28", "item 29 (knew of the benefit from item 28's trafficking)"),
    "applicant.part9.pt8line35b": ("applicant.part9.pt8line35a", "item 35.b (responsible for religious-freedom violations)"),
    "applicant.part9.pt8line40": ("applicant.part9.pt9line39", "item 40 (knew the benefit came from item 39's trafficking)"),
    "applicant.part9.pt9line77": ("applicant.part9.unlawfully_present_since_1997",
                                  "item 75 (was trafficking a central reason for the unlawful presence in item 74?)"),
}


def completeness_findings(graph: FactGraph) -> list[tuple[str, str]]:
    def present(key: str) -> bool:
        fact = graph.get(key)
        return fact is not None and (fact.review is not None or (fact.status != "missing" and fact.sources)
                                     or fact.derived_by is not None)

    status = graph.get("applicant.marital_status")
    married = status is not None and (status.value == "Married" if status.status == "resolved"
                                      else any(s.normalized_value == "Married" for s in status.sources))
    wanted = _ALWAYS + (_IF_MARRIED if married else [])
    for follow, (base, words) in PART9_FOLLOWUPS.items():
        if _value(graph, base) == "Yes":
            fact = graph.get(base)
            said = next((s for s in fact.sources if s.normalized_value == "Yes"), None) if fact is not None else None
            where = (f" ({'a reviewer' if said.doc_type == 'paralegal_review' else said.doc_id}: {said.raw_value})" if said and said.raw_value else "")
            wanted.append((follow, f"Part 9 {words}: the main question is answered Yes{where}, so this follow-up needs the attorney's answer."))
    for slot in (1, 2):
        if present(f"applicant.child{slot}_family_name"):
            wanted.append((f"applicant.child{slot}_relationship", f"Child {slot}: relationship to the client (e.g. SON, DAUGHTER, "
                                                                  "STEPCHILD): the questionnaire doesn't say; ask the client."))
    if _value(graph, "applicant.lived_at_address_5yrs") == "No":
        wanted.append(("applicant.prior_address_street", "Prior address: the client has lived at the current address less than 5 "
                                                         "years, so Part 1 item 18 needs the previous address: ask the client."))
    if present("applicant.last_foreign_city"):  # Generic implementation note.
        wanted.append(("applicant.last_foreign_street", "Last address outside the U.S.: street. The client gave only the "
                                                        "city; ask the client for the street and number."))
    return [(key, ask) for key, ask in wanted if not present(key)]


# Generic implementation note.


def known_cities(graph: FactGraph) -> list[str]:
    """Application helper with evidence-bound inputs."""
    keys = ("applicant.birth_city", "applicant.birth_cert.naturalidade", "applicant.birth_cert.registration_city", "applicant.mother_birth_city",
            "applicant.father_birth_city", "applicant.foreign_employer_city")
    return list(dict.fromkeys(fold_name(v) for k in keys if (v := _value(graph, k))))


def _swap(iso: str) -> str | None:
    y, m, d = iso.split("-")
    return f"{y}-{d}-{m}" if int(d) <= 12 and d != m else None


def _foreign_address_findings(graph: FactGraph) -> list[tuple[str, str]]:
    """Application helper with evidence-bound inputs."""
    import difflib
    from datetime import date

    from extract.places import BR_STATE_NAMES, br_state_from_cep

    out: list[tuple[str, str]] = []
    city, province = _value(graph, "applicant.last_foreign_city"), _value(graph, "applicant.last_foreign_province")
    postal, country = _value(graph, "applicant.last_foreign_postal_code"), _value(graph, "applicant.last_foreign_country")
    from extract.places import is_us_country
    if is_us_country(country):
        out.append(("applicant.last_foreign_country", "This answer is for the last home address outside the U.S., but Country says United States. "
                    "Check with the client and enter the address in the country where they last lived outside the U.S.; do not substitute their U.S. address."))
    left = _value(graph, "applicant.last_foreign_date_to")
    arrived = _value(graph, "applicant.last_arrival_date") or _value(graph, "applicant.i94_arrival_date") or _value(graph, "applicant.last_arrival_date_self_reported")

    if left and arrived and re.fullmatch(r"\d{4}-\d{2}-\d{2}", left) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", arrived):
        gap = abs((date.fromisoformat(left) - date.fromisoformat(arrived)).days)
        swapped = _swap(left)
        if gap > 45:
            hint = (f" With day and month swapped it would be {swapped[5:7]}/{swapped[8:]}/{swapped[:4]}, which fits: the client "
                    "probably wrote the date month-first." if swapped and abs((date.fromisoformat(swapped) - date.fromisoformat(arrived)).days) <= 45 else "")
            out.append(("applicant.last_foreign_date_to",
                        f"The client left the last foreign address on {left[5:7]}/{left[8:]}/{left[:4]}, but entered the U.S. on "
                        f"{arrived[5:7]}/{arrived[8:]}/{arrived[:4]}: {gap} days apart.{hint} Check the dates on this address."))
    from extract import geo

    elsewhere = geo.code(country) not in (None, "BR")  # Generic implementation note.
    if city:
        parts = [p.strip() for p in city.split(",")]
        region_not_city = elsewhere and geo.is_region(country, city) and not geo.regions_for_place(country, city)
        if len(parts) > 1 or (not elsewhere and fold_name(city) in BR_STATE_NAMES) or region_not_city:
            out.append(("applicant.last_foreign_city", f"The City box holds {city!r}: only the city goes there; a state such as "
                                                       f"{parts[-1]!r} goes in Province."))
        known = known_cities(graph)
        close = difflib.get_close_matches(fold_name(parts[0]), known, n=1, cutoff=0.6)
        if close and close[0] != fold_name(parts[0]):
            out.append(("applicant.last_foreign_city", f"The city {parts[0]!r} isn't on any document, but {close[0]} is (e.g. the birth "
                                                       f"certificate): is it {close[0]}? Check the scan."))
    if province and re.search(r"\d", province):
        out.append(("applicant.last_foreign_province", f"The Province box holds {province!r}, which looks like a postal code: "
                                                       "Province is the state (e.g. SAO PAULO)."))
    if postal and elsewhere:
        label, shape = (geo.postal(country) or {}).get("label", "postal code"), geo.postal_format(country)
        if geo.postal(country) is None:
            pass  # Generic implementation note.
        elif geo.postal_ok(country, postal) is False:
            out.append(("applicant.last_foreign_postal_code", f"A {country.title()} postal code ({label}) is {shape}; {postal!r} isn't. "
                                                              "Check the scan."))
        else:
            named, written = geo.region_from_postal(country, postal), geo.region(country, province or "")
            if named and written and named != written:
                out.append(("applicant.last_foreign_postal_code", f"Postal code {postal} is in {named}, but Province says {province}."))
    if city and province and elsewhere and not re.search(r"\d", province):
        written, places = geo.region(country, province), geo.regions_for_place(country, parts[0])
        likely = geo.likely_region(country, parts[0])
        if written and places and written not in places and likely:
            out.append(("applicant.last_foreign_province", f"{parts[0]} is in {likely[0]}, not {province} ({likely[1]}). Check the address."))
    if postal and not elsewhere and (country or "").upper() in ("BRAZIL", "BRASIL", ""):
        digits = re.sub(r"\D", "", postal)
        if len(digits) != 8:
            out.append(("applicant.last_foreign_postal_code", f"A Brazilian postal code (CEP) has 8 digits; {postal!r} has {len(digits)}. "
                                                              "Check the scan."))
        else:
            from extract.places import BR_UF

            state = br_state_from_cep(digits)
            written = BR_UF.get(fold_name(province or "").strip(), fold_name(province or ""))  # Generic implementation note.
            if state and province and not re.search(r"\d", province) and written != state:
                out.append(("applicant.last_foreign_postal_code", f"Postal code {postal} belongs to {state}, but Province says {province}."))
    return out


def consistency_findings(graph: FactGraph) -> list[tuple[str, str]]:
    """Application helper with evidence-bound inputs."""
    out: list[tuple[str, str]] = []

    # Generic implementation note.
    for prefix, what in (("questionnaire.prior_address", "previous address"), ("questionnaire.prior_employer", "previous job or school")):
        for n in range(1, 10):
            start, end = _value(graph, f"{prefix}{n}_date_from"), _value(graph, f"{prefix}{n}_date_to")
            if start and end and re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", end) and end < start:
                out.append((f"{prefix}{n}_date_to", f"The client's {what} line {n} runs backwards: from {_us_date(start)} to {_us_date(end)}. "
                                                    "One of the dates is misread or miswritten (day and month swapped?): check the "
                                                    "questionnaire and correct it before it goes on Part 14."))
    city, state = _value(graph, "applicant.birth_city"), _value(graph, "applicant.birth_state")

    natural = _value(graph, "applicant.birth_cert.naturalidade")
    if city and natural and fold_name(natural) != fold_name(city):
        out.append(("applicant.birth_city", f"The birth certificate's city of birth is {city} but its 'naturalidade' is {natural}. "
                                            "Since 2017 a Brazilian registry may record the mother's home town as naturalidade; "
                                            "the I-485 asks where the client was born, so the city of birth is used. Confirm with the client."))

    said = _value(graph, "questionnaire.birth_place")
    if city and said and fold_name(city) not in fold_name(said):
        out.append(("applicant.birth_city", f"The client wrote {said!r} as their place of birth; the birth certificate says {city}"
                                            + (f", {state}" if state else "") + ". The certificate is used."))

    married_place = _value(graph, "applicant.marriage_cert_birthplace")
    if married_place and city:
        where = fold_name(married_place.split(",")[0])
        if where == fold_name(city):
            pass
        elif state and where == fold_name(state):
            out.append(("applicant.marriage_cert_birthplace", f"The marriage certificate gives the client's place of birth as "
                                                              f"{married_place}: the state, not the city ({city}, {state}) on the birth "
                                                              "certificate. Not a contradiction, but the two documents will be filed together."))
        else:
            out.append(("applicant.marriage_cert_birthplace", f"The marriage certificate gives the client's place of birth as "
                                                              f"{married_place}; the birth certificate says {city}"
                                                              + (f", {state}" if state else "") + ". One of them is wrong."))

    legal = " ".join(x for x in (_value(graph, "applicant.given_name"), _value(graph, "applicant.family_name")) if x)
    on_birth_cert = _value(graph, "applicant.birth_certificate_name")
    if legal and on_birth_cert and fold_name(legal) != fold_name(on_birth_cert) and graph.get("applicant.name_events") is None:
        # Generic implementation note.
        out.append(("applicant.birth_certificate_name", f"The birth certificate reads {on_birth_cert}; the USCIS documents read {fold_name(legal)}. "
                                                         "Decide which is the legal name and whether the other goes in Part 1 item 2 (other names)."))

    said_dob, doc_dob = _value(graph, "questionnaire.dob"), _value(graph, "applicant.dob")
    if said_dob and doc_dob and said_dob != doc_dob:
        out.append(("applicant.dob", f"The client wrote {said_dob} as their date of birth; the documents say {doc_dob}. "
                                     "The documents are used: confirm with the client."))

    out += _foreign_address_findings(graph)
    out += arrival.findings(graph)  # Generic implementation note.

    for role in ("mother", "father"):
        # Generic implementation note.
        # Generic implementation note.
        # Generic implementation note.
        now = " ".join(x for x in (_value(graph, f"applicant.{role}_given_name"), _value(graph, f"applicant.{role}_family_name")) if x)
        then = " ".join(x for x in (_value(graph, f"applicant.{role}_birth_given_name"), _value(graph, f"applicant.{role}_birth_family_name")) if x)
        if now and then and NOT_APPLICABLE not in then and fold_name(now) == fold_name(then):
            out.append((f"applicant.{role}_birth_family_name",
                        f"The {role}'s name at birth is the same as the current name ({fold_name(now)}). The I-485 asks for it "
                        "only if different: the firm writes NOT APPLICABLE in all three boxes."))

    listed = [k for k in range(1, 5) if _value(graph, f"questionnaire.child{k}_name")]
    count = _value(graph, "applicant.total_children")
    if count is not None and str(count).isdigit() and listed and int(count) != len(listed):
        out.append(("applicant.total_children", f"The client wrote {count} as the number of children but listed {len(listed)}. "
                                                "Part 7 asks for ALL living children: ask the client."))

    mother_dob, father_dob, own_dob = (_value(graph, f"applicant.{k}") for k in ("mother_dob", "father_dob", "dob"))
    if mother_dob and mother_dob == father_dob:
        out.append(("applicant.mother_dob", f"Both parents have the same date of birth ({mother_dob}): usually one of them was read "
                                            "from the wrong line. Check both against the questionnaire."))
    for role, parent_dob in (("mother", mother_dob), ("father", father_dob)):
        if parent_dob and own_dob and int(own_dob[:4]) - int(parent_dob[:4]) < 12:
            out.append((f"applicant.{role}_dob", f"The {role}'s date of birth ({parent_dob}) is less than 12 years before the "
                                                 f"client's ({own_dob}): check it."))

    if _value(graph, "questionnaire.has_i94_or_parole") == "No" and _value(graph, "applicant.i94_number"):
        out.append(("applicant.i94_number", f"The client answered No to 'do you have an I-94 or parole?', but the folder has "
                                            f"I-94 {_value(graph, 'applicant.i94_number')} (class {_value(graph, 'applicant.i94_class_of_admission') or '?'}, "
                                            f"arrived {_us_date(_value(graph, 'applicant.i94_arrival_date'))}). The I-94 is used: Part 1 items 10-12 "
                                            "and the unlawful-presence answers depend on it."))

    import absence

    for paper, spec in absence.papers().items():  # Generic implementation note.
        marker = _value(graph, absence.MARKER + paper)
        if not marker or absence.reasons().get(str(marker), {}).get("boxes") is False:
            continue
        for box in spec["boxes"]:
            held = _value(graph, box["key"])
            if held:
                article = "an" if box["label"][:1].lower() in "aeiou" else "a"
                out.append((box["key"], f"The office recorded that the client has no {absence.plain(paper)}, but the case holds {article} {box['label']} ({held}). "
                                        f"The values the case holds are used, and none of this paper's boxes read as absent: check which is right, and take the mark off "
                                        "the Documents tab if the client does have the paper."))

    said_date, doc_date = _value(graph, "questionnaire.marriage_date"), _value(graph, "applicant.marriage_date")
    if said_date and doc_date and said_date != doc_date and (_src(graph, "applicant.marriage_date") or ())[:2] != (_src(graph, "questionnaire.marriage_date") or ())[:2]:
        out.append(("applicant.marriage_date", f"The client wrote {said_date} as the marriage date; the marriage certificate says {doc_date}. "
                                               "The certificate is used."))

    for role, word in (("mother", "mother"), ("father", "father")):
        bc_name = _value(graph, f"applicant.birth_cert.{role}_name")
        if bc_name is None:
            bc = _birth_cert_parents(graph).get(role)
            bc_name = bc[0] if bc else None
        known = [fold_name(v) for v in (
            " ".join(x for x in (_value(graph, f"applicant.{role}_given_name"), _value(graph, f"applicant.{role}_family_name")) if x),
            " ".join(x for x in (_value(graph, f"applicant.{role}_birth_given_name"), _value(graph, f"applicant.{role}_birth_family_name")) if x),
        ) if v and NOT_APPLICABLE not in v]
        if bc_name and known and not any(same_person_name(bc_name, k) or fold_name(bc_name).startswith(k) or k.startswith(fold_name(bc_name))
                                         for k in known):
            out.append((f"applicant.{role}_family_name", f"The birth certificate names the {word} {bc_name}, which matches neither "
                                                         f"the legal name nor the name at birth on this form ({' / '.join(known)}). "
                                                         "Ask the client whether the name changed, and how."))
    return out
