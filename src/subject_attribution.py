"""EV2: source roles and named, case-scoped subject attribution.

A document's catalog owner is a suggestion. It does not establish whose
individual facts may fill a form. Raw evidence stays in the graph; the
effective graph uses only sources with a current subject assignment.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import document_instances
import read_scope

VERSION = 1
ROLE_VERSION = "subject-roles-1"
_READER_VERSIONS: dict[str, tuple[tuple, str]] = {}

# This is an engineering reader-role inventory, not a legal field rule or a
# calibrated accuracy claim. Unsupported roles remain explicit.
HOLDER_TYPES = frozenset({"i94", "passport", "ssn_card", "drivers_license", "visa", "work_permit",
                          "name_change_order", "us_passport", "citizenship_certificate", "green_card", "us_birth_certificate"})
CONTEXT_TYPES = {"notice_to_appear": "respondent", "i213": "record_subject", "sij_order": "child_subject",
                 "criminal_record": "defendant", "intake_questionnaire": "respondent"}
NOTICE_TYPES = frozenset({"uscis_notice", "i360_approval", "i765_approval", "i130_approval", "i485_receipt",
                          "i526_approval", "i590_approval", "i730_approval"})
BIRTH_SUBJECT_KEYS = frozenset({"applicant.birth_certificate_name", "applicant.dob", "applicant.sex",
                               "applicant.country_of_birth", "applicant.birth_city", "applicant.birth_state",
                               "applicant.birth_cert.registration_city", "applicant.birth_cert.naturalidade"})


def digest(value: Any) -> str:
    return document_instances.digest(value)


def role_for(doc_type: str, key: str) -> str:
    """The role the reader actually printed, before any case-person match."""
    if doc_type == "birth_certificate":
        if key == "applicant.birth_cert.grandparents":
            return "grandparents"
        match = re.match(r"applicant\.birth_cert\.(mother|father|parent_a|parent_b)_", key)
        if match:
            return match[1]
        return "birth_subject" if key in BIRTH_SUBJECT_KEYS else "unmapped"
    if doc_type == "marriage_certificate":
        match = re.match(r"marriage\.(party_[ab])\.(.+)", key)
        if match:
            parent = re.match(r"parent([12])_", match[2])
            return f"{match[1]}_parent{parent[1]}" if parent else match[1]
        return "marriage_event" if key in {"applicant.marital_status", "applicant.marriage_date", "applicant.marriage_place",
                                           "applicant.marriage_party_names"} else "unmapped"
    if doc_type in NOTICE_TYPES:
        if doc_type == "i360_approval" and key in {"applicant.given_name", "applicant.family_name"}:
            return "beneficiary"  # this reader explicitly reads the Beneficiary label
        if key == "applicant.a_number" or key.endswith((".name", ".addressed_to")):
            return "unmapped"  # first matching number / recipient is not proof of beneficiary identity
        return "case_subject"
    if doc_type == "intake_questionnaire":
        match = re.match(r"(?:applicant|questionnaire)\.(mother|father|spouse)_", key)
        if match:
            return match[1]
        if "child" in key:
            return "unmapped"
    if doc_type == "sij_order" and key == "sij.parent_name":
        return "parent_mentioned"
    if doc_type in CONTEXT_TYPES:
        return CONTEXT_TYPES[doc_type]
    if doc_type in HOLDER_TYPES:
        return "holder"
    return "unmapped"


def required_slots(doc_type: str, source_role: str) -> tuple[str, ...]:
    if source_role == "unmapped":
        return ()
    if doc_type == "birth_certificate":
        return ("birth_subject",) if source_role in {"birth_subject", "grandparents"} else ("birth_subject", source_role)
    if doc_type == "marriage_certificate":
        return ("party_a", "party_b") if source_role == "marriage_event" else (
            (source_role.split("_parent", 1)[0], source_role) if "_parent" in source_role else (source_role,))
    if doc_type == "intake_questionnaire" and source_role != "respondent":
        return ("respondent", source_role)
    if doc_type == "sij_order" and source_role == "parent_mentioned":
        return ("child_subject", source_role)
    return (source_role,)


def reader_version(doc_type: str) -> str:
    return read_scope.once(("subject-reader-version", doc_type), lambda: _reader_version(doc_type))


def _reader_version(doc_type: str) -> str:
    """Bounded source-code identity for the reader and shared normalization.

    No timestamps or process identity. EV3 will extend the field confirmation
    contract to its full declared configuration/normalization inventory.
    """
    from extract import EXTRACTORS
    root = Path(__file__).parent
    reader = EXTRACTORS.get(doc_type)
    modules = {"extract/base.py", "extract/names.py", "extract/places.py"}
    if reader:
        modules.add(reader.__module__.replace(".", "/") + ".py")
    if doc_type == "i360_approval":
        modules.update({"extract/i360_approval.py", "extract/uscis_notice.py"})
    if doc_type == "us_passport":
        modules.add("extract/passport.py")
    files = [name for name in sorted(modules) if (root / name).is_file()]
    stamp = (ROLE_VERSION, tuple((name, (root / name).stat().st_mtime_ns, (root / name).stat().st_size) for name in files))
    cached = _READER_VERSIONS.get(doc_type)
    if cached and cached[0] == stamp:
        return cached[1]
    value = digest({"role_version": ROLE_VERSION, "type": doc_type,
                    "files": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}})
    _READER_VERSIONS[doc_type] = stamp, value
    return value


def provenance(instance: dict, doc_type: str, field) -> dict:
    role = role_for(doc_type, field.fact_key)
    return {"instance_id": instance["instance_id"], "subject_role": role,
            "evidence_version": digest({"instance": instance["instance_id"],
                                        "boundary_evidence": instance["evidence_fingerprint"],
                                        "key": field.fact_key, "raw": field.raw_value, "value": field.normalized_value,
                                        "page": field.page, "role": role, "reader": reader_version(doc_type)})}


def original_source(source) -> bool:
    """Only retained-document edges, including a scanned questionnaire."""
    return bool(source.instance_id) or (source.doc_id not in {
        "portal questionnaire", "office question", "firm_profile.json", "paralegal_review"}
        and source.doc_type in HOLDER_TYPES | NOTICE_TYPES | set(CONTEXT_TYPES) | {"birth_certificate", "marriage_certificate", "tax_return"})


def edge_versions(graph) -> dict[str, dict]:
    """Current original edges, keyed by their exact evidence version."""
    return {s.evidence_version: {"key": key, "source": s}
            for key, fact in graph.all_facts().items() for s in fact.sources
            if s.evidence_version and not s.from_facts and not s.input_evidence}


def ensure_catalog(data: dict, case_id: str) -> dict:
    """Seed a stable case-client identity, never an accepted source mapping."""
    import uuid
    catalog = data.setdefault("case_subjects", {"version": VERSION, "case_id": case_id, "people": []})
    if catalog.get("case_id") != case_id:
        raise ValueError("Subject identities belong to a different case.")
    if not any(p.get("case_role") == "applicant" and p.get("active", True) for p in catalog["people"]):
        catalog["people"].append({"id": uuid.uuid4().hex, "case_role": "applicant", "label": f"Client in case {case_id}",
                                  "revision": 1, "active": True})
    data.setdefault("subject_assignments", {})
    return data


def _read(case_dir: Path) -> dict:
    import documents
    return documents.read(case_dir) or {}


def _raw(case_dir: Path):
    from factgraph import FactGraph
    path = case_dir / "fact_graph_raw.json"
    if not path.exists():
        path = case_dir / "fact_graph.json"
    return FactGraph.load(path) if path.exists() else FactGraph(case_dir.name)


def _people(data: dict) -> dict:
    return {p["id"]: p for p in data.get("case_subjects", {}).get("people", []) if p.get("active", True)}


def _person_version(person: dict) -> str:
    return digest({key: person.get(key) for key in ("id", "revision", "label", "case_role", "active")})


def _parent_suggestions(graph, facts, people):
    """Exact matches to typed parent answers are suggestions, never mappings."""
    from extract.names import fold_name
    names = {}
    for role in ("mother", "father"):
        names[role] = set()
        for suffix in ("name", "birth_name"):
            fact = graph.get(f"questionnaire.{role}_{suffix}")
            if fact and fact.status == "resolved" and isinstance(fact.value, str) and any(
                    s.doc_id == "portal questionnaire" and not s.instance_id and s.normalized_value == fact.value for s in fact.sources):
                names[role].add(fold_name(fact.value))
    suggestions = {}
    for field in facts:
        slot = field["role"]
        if slot not in {"mother", "father", "parent_a", "parent_b"} or not field["key"].endswith("_name"):
            continue
        roles = [role for role, values in names.items() if isinstance(field["value"], str) and fold_name(field["value"]) in values]
        if len(roles) == 1 and (slot not in {"mother", "father"} or slot == roles[0]):
            role = roles[0]
            person = next((p for p in people.values() if p["case_role"] == role and fold_name(p["label"]) in names[role]), None)
            suggestions[slot] = {"role": role, "subject_id": person["id"] if person else None}
    return {slot: hint for slot, hint in suggestions.items() if sum(h["role"] == hint["role"] for h in suggestions.values()) == 1}


def _valid_assignment(entry, *, fingerprint, part, plan, edges, slots, people) -> bool:
    """A digest alone is not a named decision, including a reference exclusion."""
    import clock
    if not isinstance(entry, dict) or entry.get("version") != VERSION or entry.get("undone"):
        return False
    if (not isinstance(entry.get("who"), str) or not entry["who"].strip()
            or entry.get("role") not in {"paralegal", "attorney"}
            or not isinstance(entry.get("at"), str) or "T" not in entry["at"] or not clock.parse(entry["at"])):
        return False
    versions = sorted(s.evidence_version for _, s in edges)
    refs, roles = entry.get("reference_edges"), entry.get("roles")
    if (entry.get("fingerprint") != fingerprint or entry.get("source_sha256") != plan.get("source_sha256")
            or entry.get("boundary_evidence_fingerprint") != part.get("evidence_fingerprint")
            or entry.get("evidence_versions") != versions or type(entry.get("reference_only")) is not bool
            or not isinstance(refs, list) or any(not isinstance(v, str) for v in refs)
            or set(refs) - set(versions) or not isinstance(roles, dict) or set(roles) - set(slots)):
        return False
    if (refs or entry["reference_only"]) and not str(entry.get("note") or "").strip():
        return False
    for target in roles.values():
        if not isinstance(target, dict) or not isinstance(target.get("subject_id"), str):
            return False
        person = people.get(target["subject_id"])
        if not person or person["revision"] != target.get("subject_revision") or _person_version(person) != target.get("subject_version"):
            return False
    return True


def views(case_dir: Path, graph=None) -> list[dict]:
    """Per-instance attribution and exact edge inventory for staff review."""
    case_dir, data = Path(case_dir), _read(Path(case_dir))
    graph = graph if graph is not None else _raw(case_dir)
    by_alias: dict[str, list] = {}
    for key, fact in graph.all_facts().items():
        for source in fact.sources:
            if original_source(source) and not source.from_facts and not source.input_evidence:
                by_alias.setdefault(source.doc_id, []).append((key, source))
    people = _people(data)
    scoped = data.get("case_subjects", {}).get("case_id") == case_dir.name and graph.client_id == case_dir.name
    rows = []
    for plan in document_instances.views(case_dir):
        for part in plan["instances"]:
            edges = [(key, s) for alias in part.get("doc_ids", []) for key, s in by_alias.get(alias, [])]
            if not edges:
                continue
            from extract.base import ExtractedField
            def valid_proof(key, source):
                expected = provenance(part, source.doc_type,
                                      ExtractedField(key, source.raw_value, source.normalized_value, source.confidence, source.page))
                return (source.instance_id == expected["instance_id"] and source.subject_role == expected["subject_role"]
                        and source.evidence_version == expected["evidence_version"])
            bound = bool(scoped and plan.get("source_sha256") and part.get("instance_id") and not plan["stale"]
                         and not plan.get("processing_incomplete") and part["state"] != "unresolved"
                         and all(valid_proof(key, s) for key, s in edges))
            fingerprint = digest({"version": ROLE_VERSION, "instance": part.get("instance_id"),
                                  "boundary": part.get("evidence_fingerprint"),
                                  "edges": sorted(s.evidence_version or f"legacy:{key}:{s.doc_id}" for key, s in edges)})
            assignment = data.get("subject_assignments", {}).get(part.get("instance_id"), {})
            slots = sorted({slot for key, s in edges for slot in required_slots(s.doc_type, s.subject_role or "unmapped")})
            current = bool(bound and _valid_assignment(assignment, fingerprint=fingerprint, part=part, plan=plan,
                                                       edges=edges, slots=slots, people=people))
            row = {"instance_id": part.get("instance_id"), "file": plan["file"], "doc_ids": part.get("doc_ids", []),
                   "first": part["first"], "last": part["last"], "type": part["type"], "fingerprint": fingerprint,
                   "boundary_evidence_fingerprint": part.get("evidence_fingerprint"), "source_sha256": plan.get("source_sha256"),
                   "bound": bound, "slots": slots, "assignment": assignment or None, "current": current,
                   "people": list(people.values()), "facts": []}
            for key, source in edges:
                state, reason = _state(key, source, row, people)
                row["facts"].append({"key": key, "doc": source.doc_id, "page": source.page,
                                      "raw": source.raw_value, "value": source.normalized_value,
                                      "role": source.subject_role or "unmapped", "evidence_version": source.evidence_version,
                                      "state": state, "reason": reason})
            row["held"] = any(f["state"] == "pending" for f in row["facts"])
            if row["type"] == "birth_certificate":
                row["parent_suggestions"] = _parent_suggestions(graph, row["facts"], people)
            rows.append(row)
    return rows


def _state(key, source, row, people) -> tuple[str, str]:
    if not row["bound"]:
        return "pending", "Read the current retained original and resolve its boundaries before assigning a subject."
    if not row["current"]:
        return "pending", "A named subject review is required for this evidence version."
    entry = row["assignment"]
    if entry.get("reference_only") or source.evidence_version in entry.get("reference_edges", []):
        return "reference", "Kept as reference; this read does not supply a form value."
    source_role = source.subject_role or "unmapped"
    slots = required_slots(source.doc_type, source_role)
    if not slots:
        return "pending", "The reader does not establish this person's role; keep the read as reference or supply supported evidence."
    roles = {slot: people[target["subject_id"]]["case_role"] for slot, target in entry.get("roles", {}).items()}
    if any(slot not in roles for slot in slots):
        return "pending", "Confirm the person for each required document role."
    # A contextual parent/party field is not the holder's own identity.
    if source.doc_type == "birth_certificate":
        if roles["birth_subject"] != "applicant":
            return "reference", "This certificate's birth subject is another person; retain it as family evidence."
        if source_role == "grandparents":
            return "accepted", "Family surname context on the applicant's birth record; no individual parent identity is assigned."
        if source_role in {"parent_a", "parent_b"}:
            return ("accepted", "Named parent-slot mapping.") if roles[source_role] in {"mother", "father"} else (
                "pending", "Map this parent slot to the case's mother or father, or keep this read as reference.")
        if source_role in {"mother", "father"} and roles[source_role] != source_role:
            return "pending", "The printed parent role and selected case person differ."
        return "accepted", "Named birth-certificate role mapping."
    if source.doc_type == "marriage_certificate":
        if not all(slot in roles for slot in ("party_a", "party_b")):
            return "pending", "Confirm both parties so this certificate's context is explicit."
        if "applicant" not in (roles["party_a"], roles["party_b"]):
            return "reference", "Neither party is the applicant; retain this certificate as family evidence."
        if source_role == "marriage_event":
            return "accepted", "Named parties identify whose marriage the event describes."
        party = source_role.split("_parent", 1)[0]
        if roles.get(party) not in {"applicant", "spouse"}:
            return "reference", "This party is not a person used by the current applicant/spouse fields."
        return "accepted", "Named marriage-party or parent mapping."
    if source.doc_type == "intake_questionnaire" and roles.get("respondent") != "applicant":
        return "reference", "These are another person's questionnaire answers."
    if source.doc_type == "sij_order" and roles.get("child_subject") != "applicant":
        return "reference", "This order's child subject is another person."
    expected = "petitioner" if key.startswith(("petitioner.", "i864.")) else "applicant"
    if source_role in {"mother", "father", "spouse"}:
        expected = source_role
    if source_role == "parent_mentioned":
        return "accepted", "Named person mentioned by the order; legal findings still require attorney review."
    return ("accepted", "Named subject mapping.") if roles[source_role] == expected else (
        "reference", f"This evidence belongs to another person and does not supply {expected} fields.")


def problems(case_dir: Path) -> list[str]:
    return [f"Review whose facts these are in Documents: {r['file']} (pages {r['first'] + 1}–{r['last'] + 1})."
            for r in views(case_dir) if r["held"]]


def pending_notices(case_dir: Path) -> list[dict]:
    """Staff-only printed dates awaiting subjects, never accepted deadlines.

    Only current retained bytes with resolved boundaries qualify. No deadline
    arithmetic, inferred dates or client-event promotion occurs here.
    """
    labels = {"date": "Notice date", "due": "Printed response date", "appointment": "Printed appointment",
              "valid_from": "Printed validity start", "valid_to": "Printed validity end", "priority_date": "Printed priority date"}
    out = []
    for row in views(case_dir):
        if not row["bound"] or row["type"] not in NOTICE_TYPES:
            continue
        dates = [{"label": labels[f["key"].rsplit(".", 1)[-1]], "value": f["value"], "page": f["page"]}
                 for f in row["facts"] if f["state"] == "pending" and f["key"].startswith("folder.notice.")
                 and f["key"].rsplit(".", 1)[-1] in labels]
        if dates:
            out.append({"instance_id": row["instance_id"], "file": row["file"], "first": row["first"], "last": row["last"],
                        "dates": dates, "state": "subject_unconfirmed"})
    return out


def filter_graph(graph, case_dir: Path) -> set[str]:
    """Remove only ineligible source edges and recompute affected derivations.

    The caller owns this working graph. The raw graph and exhibits on disk
    remain intact. A retained source is never restored by catalog ownership.
    """
    from factgraph import FactGraph
    rows = views(case_dir, graph)
    state = {(f["doc"], f["key"], f["evidence_version"]): f["state"] for r in rows for f in r["facts"]}
    bad_versions = {f["evidence_version"] for r in rows for f in r["facts"] if f["state"] != "accepted" and f["evidence_version"]}
    def excluded(key, source):
        return (state.get((source.doc_id, key, source.evidence_version)) in {"pending", "reference"}
                or bool(set(source.input_evidence) & bad_versions))
    affected = {key for key, fact in graph.all_facts().items() if any(excluded(key, source) for source in fact.sources)}
    while True:
        expanded = affected | {key for key, fact in graph.all_facts().items() if set(fact.derived_from) & affected or
                               any(set(source.from_facts) & affected for source in fact.sources)}
        if expanded == affected:
            break
        affected = expanded
    fresh = FactGraph(graph.client_id)
    for key, fact in graph.all_facts().items():
        if key not in affected:
            fresh._facts[key] = fact
            continue
        for source in fact.sources:
            if not excluded(key, source) and not fact.derived_by and not set(source.from_facts) & affected:
                from factgraph.graph import TYPED_BY_A_PERSON
                tier = 3 if source.doc_type in TYPED_BY_A_PERSON else fact.tier
                fresh.add_source(key, source.doc_id, source.doc_type, source.raw_value, source.normalized_value, source.confidence,
                                 tier=tier, page=source.page, from_facts=source.from_facts,
                                 instance_id=source.instance_id, subject_role=source.subject_role,
                                 evidence_version=source.evidence_version, input_evidence=source.input_evidence,
                                 read_manifest=source.read_manifest, reading_issues=source.reading_issues)
    graph._facts = fresh._facts
    # Only the reviewed graph receives these role contexts. Pure low-level
    # extraction keeps its existing suggestion behavior and makes no acceptance
    # claim. Assemblers must not fall back to name matching in this mode.
    graph._subject_roles = {}
    people = _people(_read(Path(case_dir)))
    for row in rows:
        if row["current"] and not row["assignment"].get("reference_only"):
            roles = {slot: people[target["subject_id"]]["case_role"] for slot, target in row["assignment"]["roles"].items()}
            for alias in row["doc_ids"]:
                graph._subject_roles[alias] = roles
    return affected


def affected_keys(case_dir: Path) -> set[str]:
    """Only unresolved attribution suppresses decisions on every read.

    A named assignment change durably invalidates old approvals. A source
    explicitly retained as another person's/reference evidence must not keep
    invalidating a later review of the remaining legitimate sources.
    """
    from factgraph import FactGraph
    path = Path(case_dir) / "fact_graph_raw.json"
    if not path.exists():
        path = Path(case_dir) / "fact_graph.json"
    if not path.exists():
        return set()
    graph = FactGraph.load(path)
    keys = {f["key"] for row in views(Path(case_dir), graph) for f in row["facts"] if f["state"] == "pending"}
    while True:
        more = keys | {k for k, fact in graph.all_facts().items() if set(fact.derived_from) & keys or
                       any(set(s.from_facts) & keys for s in fact.sources)}
        if more == keys:
            return keys
        keys = more


def _actor(who: str, role: str | None) -> None:
    if not isinstance(who, str) or not who.strip() or role not in {"paralegal", "attorney"}:
        raise ValueError("A named staff reviewer and role are required for a subject decision.")


def add_person(case_dir: Path, label: str, case_role: str, who: str, role: str | None) -> dict:
    """Create an explicit person in this case; this grants no portal access."""
    import clock
    import documents
    import jobs
    import uuid
    _actor(who, role)
    label = " ".join(label.split())
    if not 2 <= len(label) <= 160:
        raise ValueError("Enter a name that identifies this person in the case.")
    if case_role not in {"spouse", "mother", "father", "petitioner", "other"} and not re.fullmatch(r"child_[1-9]\d?", case_role):
        raise ValueError("Choose this person's role in the case.")
    case_dir = Path(case_dir)
    with jobs.case_lock(jobs.folder_for(case_dir.parent), case_dir.name):
        data = ensure_catalog(_read(case_dir), case_dir.name)
        if case_role != "other" and any(p["case_role"] == case_role for p in _people(data).values()):
            raise ValueError("That case role already has a person. Select the existing person or correct their identity first.")
        person = {"id": uuid.uuid4().hex, "label": label, "case_role": case_role, "revision": 1, "active": True,
                  "who": who, "role": role, "at": clock.stamp()}
        data["case_subjects"]["people"].append(person)
        documents.save(case_dir, data)
        return person


def rename_person(case_dir: Path, subject_id: str, label: str, who: str, role: str | None) -> dict:
    """Correct the named identity; every assignment using it needs review again."""
    import clock
    import documents
    import jobs
    _actor(who, role)
    label = " ".join(label.split())
    if not 2 <= len(label) <= 160:
        raise ValueError("Enter a name that identifies this person in the case.")
    case_dir = Path(case_dir)
    with jobs.case_lock(jobs.folder_for(case_dir.parent), case_dir.name):
        data = _read(case_dir)
        person = _people(data).get(subject_id)
        if not person:
            raise ValueError("Select a person from this case.")
        if person["label"] == label:
            return person
        keys = {f["key"] for row in views(case_dir) if any(t.get("subject_id") == subject_id for t in
                (row.get("assignment") or {}).get("roles", {}).values()) for f in row["facts"]}
        _invalidate_field_reviews(case_dir, keys, who, role)
        person.setdefault("history", []).append({"label": person["label"], "revision": person["revision"],
                                                 "changed_to": label, "who": who, "role": role, "at": clock.stamp()})
        person.update(label=label, revision=person["revision"] + 1, who=who, role=role, at=clock.stamp())
        documents.save(case_dir, data)
        return person


def _invalidate_field_reviews(case_dir: Path, keys: set[str], who: str, role: str | None) -> None:
    from review.state import load_decision_log, undo_decision
    from factgraph import FactGraph
    path = case_dir / "fact_graph.json"
    graph = FactGraph.load(path) if path.exists() else _raw(case_dir)
    changed = True
    while changed:
        more = {k for k, f in graph.all_facts().items() if set(f.derived_from) & keys or
                any(set(s.from_facts) & keys for s in f.sources)}
        changed = not more <= keys
        keys |= more
    for iid, entry in load_decision_log(case_dir).items():
        if not entry.get("undone") and not document_instances.independent_absence(iid, entry) and set(entry.get("item", {}).get("facts", [])) & keys:
            undo_decision(case_dir, iid, who, role, "Subject attribution changed; review the affected field against its current evidence.")


def assign(case_dir: Path, instance_id: str, expected: str, mappings: dict, who: str, role: str | None,
           *, reference_only: bool = False, reference_edges: list[str] | None = None, note: str = "", undo: bool = False) -> dict:
    import clock
    import documents
    import jobs
    _actor(who, role)
    if not isinstance(mappings, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mappings.items()):
        raise ValueError("Choose each document role and its case person.")
    if (reference_edges is not None and (not isinstance(reference_edges, list) or any(not isinstance(v, str) for v in reference_edges))) or type(reference_only) is not bool:
        raise ValueError("Choose current evidence reads to keep as reference.")
    case_dir = Path(case_dir)
    with jobs.case_lock(jobs.folder_for(case_dir.parent), case_dir.name):
        data = _read(case_dir)
        row = next((r for r in views(case_dir) if r["instance_id"] == instance_id), None)
        if not row or row["fingerprint"] != expected or not row["bound"]:
            raise ValueError("The retained evidence changed or is unbound. Read it again and refresh the subject review.")
        people = _people(data)
        if set(mappings) - set(row["slots"]):
            raise ValueError("That role is not supported by this document's current reader.")
        mappings = dict(mappings)
        # An explicit staff choice can add a missing parent using the name
        # printed in this exact retained certificate. No gender or order guess,
        # questionnaire access, or automatic field approval is made here.
        for slot, subject_id in list(mappings.items()):
            if not subject_id.startswith("new:"):
                continue
            parent_role = subject_id[4:]
            child = people.get(mappings.get("birth_subject"), {})
            if (undo or reference_only or row["type"] != "birth_certificate"
                    or slot not in {"parent_a", "parent_b", "mother", "father"}
                    or parent_role not in {"mother", "father"} or child.get("case_role") != "applicant"
                    or slot in {"mother", "father"} and slot != parent_role):
                raise ValueError("Choose the applicant and the parent's printed role before adding a birth-certificate parent.")
            if any(p["case_role"] == parent_role for p in people.values()):
                raise ValueError("That parent already has a case person. Select the existing person.")
            names = {f["value"] for f in row["facts"] if f["key"] == f"applicant.birth_cert.{slot}_name" and isinstance(f["value"], str)}
            from extract.names import looks_like_name
            if len(names) != 1 or not looks_like_name(next(iter(names))) or len(next(iter(names))) > 160:
                raise ValueError("The parent's name could not be read reliably. Add the case person using the original.")
            import uuid
            person = {"id": uuid.uuid4().hex, "label": next(iter(names)), "case_role": parent_role,
                      "revision": 1, "active": True, "who": who, "role": role, "at": clock.stamp()}
            data["case_subjects"]["people"].append(person)
            people[person["id"]] = person
            mappings[slot] = person["id"]
        if any(subject_id not in people for subject_id in mappings.values()):
            raise ValueError("Select an existing person from this case; another case's identity cannot be used.")
        if row["type"] == "marriage_certificate" and mappings.get("party_a") and mappings.get("party_a") == mappings.get("party_b"):
            raise ValueError("The two parties need distinct case-person identities. Review the source or keep it as reference.")
        references = sorted(set(reference_edges or []))
        if set(references) - {f["evidence_version"] for f in row["facts"]}:
            raise ValueError("A referenced read changed. Refresh the evidence review.")
        if (reference_only or references) and not note.strip():
            raise ValueError("Record why these reads are being kept as reference only.")
        if not mappings and not reference_only and not references and not undo:
            raise ValueError("Choose whose facts these are, or keep them explicitly as reference.")
        old = data.setdefault("subject_assignments", {}).get(instance_id) or {}
        entry = {"version": VERSION, "fingerprint": expected,
                 "boundary_evidence_fingerprint": row["boundary_evidence_fingerprint"], "source_sha256": row["source_sha256"],
                 "roles": {slot: {"subject_id": sid, "subject_revision": people[sid]["revision"], "subject_version": _person_version(people[sid])}
                           for slot, sid in mappings.items()},
                 "evidence_versions": sorted(f["evidence_version"] for f in row["facts"]),
                 "reference_only": bool(reference_only), "reference_edges": references, "note": note.strip(),
                 "who": who, "role": role, "at": clock.stamp(), "undone": bool(undo)}
        entry["history"] = old.get("history", []) + [dict(entry)]
        data["subject_assignments"][instance_id] = entry
        # Invalidate before publishing the new assignment. A failed save can
        # reopen a review, but can never resurrect approval over new evidence.
        _invalidate_field_reviews(case_dir, {f["key"] for f in row["facts"]}, who, role)
        documents.save(case_dir, data)
        return entry


def owner_changed(case_dir: Path, doc_id: str, who: str, role: str | None) -> None:
    """A catalog correction reopens corresponding subject/field decisions."""
    import clock
    import documents
    case_dir = Path(case_dir)
    data = _read(case_dir)
    record = next((r for r in data.get("documents", []) if r["id"] == doc_id), {})
    aliases = set(record.get("doc_ids", []))
    changed = False
    keys = set()
    for row in views(case_dir):
        if not aliases.intersection(row["doc_ids"]):
            continue
        keys.update(f["key"] for f in row["facts"])
        entry = data.get("subject_assignments", {}).get(row["instance_id"])
        if entry and not entry.get("undone"):
            mark = {"who": who, "role": role, "at": clock.stamp(), "reason": "Document ownership changed; reconfirm fact subjects."}
            entry["undone"] = mark
            entry.setdefault("history", []).append({"undone": mark})
            changed = True
    _invalidate_field_reviews(case_dir, keys, who, role)
    if changed:
        documents.save(case_dir, data)
