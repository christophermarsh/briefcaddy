"""Current attorney-selected client-file authority, separate from case closure.

The MA profile is a manual-review draft, not a legal opinion. This module records
no delivery, exports nothing and schedules no deletion. Route composition must
supply the current authorized actor and exact case; historical approvals remain.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from datetime import date, datetime
from functools import wraps
from pathlib import Path

import client_file
import clock
import oslock
import schema_path

FILE = "client_file_policy.json"
TEMP = ".client-file-policy.part"
MAX_BYTES = 1024 * 1024
HEX = re.compile(r"[a-f0-9]{32}")
SHA = re.compile(r"[a-f0-9]{64}")
ENUMS = {
    "choice_of_law_basis": ("unknown", "tribunal", "principal_office", "predominant_effect"),
    "matter_type": ("unknown", "civil", "criminal", "delinquency", "cpcs", "other"),
    "client_age_status": ("unknown", "adult", "minor"),
    "originals_status": ("unknown", "none_held", "returned", "retained_for_client"),
    "other_requirements": ("unknown", "reviewed_none"),
}
TEXT_FIELDS = ("principal_office", "principal_office_jurisdiction", "tribunal", "tribunal_jurisdiction",
               "predominant_effect_jurisdiction", "applicability_reason", "special_milestones")
DEFAULT_FACTS = {"profile_id": None, **{key: values[0] for key, values in ENUMS.items()},
                 **{key: "" for key in TEXT_FIELDS}, "attorney_admissions": [], "holds": [],
                 "representation_completed_on": None, "date_of_majority": None}
AUTHORITY = ("client", "authorized_representative", "successor_counsel")
METHODS = ("in_person", "mail", "secure_transfer", "email")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def profile():
    path = schema_path.path("law", "client_file_policy_ma")
    raw = path.read_bytes()
    out = json.loads(raw)
    return out | {"source_digest": hashlib.sha256(raw).hexdigest()}


def _text(value, label, required=False):
    if not isinstance(value, str) or len(value) > 4000 or (required and not value.strip()):
        raise ValueError(f"Enter valid {label}.")
    return value.strip()


class Conflict(ValueError):
    """A stale current snapshot requires a new read before mutation."""


def _date(value, label, *, future=False):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"Enter an ISO date for {label}.")
    day = date.fromisoformat(value)
    if not future and day > clock.today():
        raise ValueError(f"{label} cannot be in the future.")
    return day.isoformat()


def facts(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_FACTS):
        raise ValueError("Read the complete current file-policy facts before saving.")
    out = copy.deepcopy(value)
    if out["profile_id"] not in (None, profile()["id"]):
        raise ValueError("No reviewed profile is available for that jurisdiction.")
    for key, options in ENUMS.items():
        if not isinstance(out[key], str) or out[key] not in options:
            raise ValueError(f"Invalid {key}.")
    for key in TEXT_FIELDS:
        out[key] = _text(out[key], key)
    admissions = out["attorney_admissions"]
    if not isinstance(admissions, list) or len(admissions) > 30:
        raise ValueError("Enter the attorney's admissions.")
    out["attorney_admissions"] = [_text(item, "admission", True) for item in admissions]
    for key in ("representation_completed_on", "date_of_majority"):
        out[key] = _date(out[key], key, future=key == "date_of_majority")
    if not isinstance(out["holds"], list) or len(out["holds"]) > 100:
        raise ValueError("Review the preservation holds.")
    for row in out["holds"]:
        if (not isinstance(row, dict) or set(row) != {"kind", "description", "active"}
                or row["kind"] not in ("court_order", "preservation", "litigation", "other")
                or type(row["active"]) is not bool):
            raise ValueError("Invalid preservation hold.")
        row["description"] = _text(row["description"], "hold description", True)
    return out


def _json_bytes(path):
    try:
        raw = client_file.capture(path, path.parent, MAX_BYTES)
        out = json.loads(raw)
    except (ValueError, OSError) as exc:
        raise ValueError("File-policy identity or record is damaged.") from exc
    if not isinstance(out, dict):
        raise ValueError("File-policy identity or record is damaged.")
    return out, raw


def _json(path):
    return _json_bytes(path)[0]


def identity(case_dir, portal_root=None):
    """Reuse original enrollment, or the existing offline case-person marker.

    Never creates a marker. An existing portal profile must have its original
    enrollment proof; failure must not fall back to a different offline identity.
    """
    d = Path(case_dir)
    if not d.is_dir() or not client_file.directory_safe(d):
        raise ValueError("A canonical case directory is required.")
    import engagement
    root = engagement._portal_root(portal_root)
    p = root / "clients" / d.name
    if (p / "profile.json").exists():
        from portal.communication_consent import Scope
        from portal.contact_control import enrollment
        from portal.store import PortalStore
        scope = Scope(d.parent.parent.parent, root, d.parent)
        store = PortalStore(root)
        return digest({"case": str(d.resolve()), "kind": "enrollment", "value": enrollment(scope, store, d.name)})
    catalog = _json(d / "documents.json").get("case_subjects", {})
    people = catalog.get("people")
    if (type(catalog.get("version")) is not int or catalog["version"] != 1 or catalog.get("case_id") != d.name
            or not isinstance(people, list) or any(not isinstance(row, dict) for row in people)):
        raise ValueError("Existing case-person identity is unavailable; review the canonical intake first.")
    applicants = [row for row in people if row.get("case_role") == "applicant" and row.get("active", True)]
    if len(applicants) != 1 or not isinstance(applicants[0].get("id"), str) or not HEX.fullmatch(applicants[0]["id"]):
        raise ValueError("A unique original case-person identity is required.")
    return digest({"case": str(d.resolve()), "kind": "case_subject", "value": applicants[0]["id"]})


def read(case_dir, portal_root=None):
    d = Path(case_dir)
    binding = identity(d, portal_root)
    path = d / FILE
    if (d / TEMP).exists() or (d / TEMP).is_symlink():
        raise ValueError("An interrupted policy publication needs review; it cannot be replaced silently.")
    if not path.exists():
        if path.is_symlink():
            raise ValueError("File-policy record cannot be a link.")
        return {"version": 1, "case": d.name, "identity": binding, "revision": 0, "facts_revision": 0,
                "facts": copy.deepcopy(DEFAULT_FACTS), "applicability_approval": None,
                "retention_approval": None, "destruction_approval": None, "recipient": None, "inventory_approval": None, "history": []}
    out = _json(path)
    # Existing core records keep every historic approval; an absent inventory
    # approval supplies no authority and must be explicitly reviewed.
    out.setdefault("inventory_approval", None)
    expected = {"version", "case", "identity", "revision", "facts_revision", "facts", "applicability_approval",
                "retention_approval", "destruction_approval", "recipient", "inventory_approval", "history"}
    if (set(out) != expected or type(out["version"]) is not int or out["version"] != 1
            or out["case"] != d.name or out["identity"] != binding
            or type(out["revision"]) is not int or out["revision"] < 1
            or type(out["facts_revision"]) is not int or not 1 <= out["facts_revision"] <= out["revision"]
            or not isinstance(out["history"], list) or len(out["history"]) != out["revision"]
            or any(not isinstance(row, dict) or row.get("revision") != n for n, row in enumerate(out["history"], 1))):
        raise ValueError("File-policy record or case identity changed; do not reuse its approval.")
    facts(out["facts"])
    for key in ("applicability_approval", "retention_approval", "destruction_approval", "recipient", "inventory_approval"):
        _validate_approval(out[key], key)
    return out


def _validate_approval(row, kind):
    if row is None:
        return
    base = {"by", "role", "at", "snapshot_sha256", "facts_revision", "profile"}
    extra = {"applicability_approval": {"source_reviewed"}, "retention_approval": {"keep_until", "determination"},
             "destruction_approval": {"retention_sha256", "protections_reviewed", "determination"},
             "recipient": {"name", "authority", "authority_evidence", "method", "destination", "authority_reviewed"},
             "inventory_approval": {"inventory_sha256", "decisions"}}[kind]
    if (not isinstance(row, dict) or set(row) != base | extra or row["role"] != "attorney"
            or type(row["facts_revision"]) is not int or row["facts_revision"] < 1
            or not isinstance(row["snapshot_sha256"], str) or not SHA.fullmatch(row["snapshot_sha256"])
            or not isinstance(row["profile"], dict) or set(row["profile"]) != {"id", "version", "source_digest"}
            or not all(isinstance(row["profile"][key], str) and row["profile"][key] for key in ("id", "version", "source_digest"))
            or not SHA.fullmatch(row["profile"]["source_digest"])):
        raise ValueError("File-policy approval record is damaged.")
    _text(row["by"], "approval actor", True)
    try:
        at = datetime.fromisoformat(row["at"])
    except (TypeError, ValueError) as exc:
        raise ValueError("File-policy approval time is damaged.") from exc
    if at.tzinfo is None:
        raise ValueError("File-policy approval time needs a time zone.")
    if kind == "applicability_approval" and row["source_reviewed"] is not True:
        raise ValueError("File-policy source approval is damaged.")
    if kind == "retention_approval":
        if _date(row["keep_until"], "keeping date", future=True) is None:
            raise ValueError("File-policy keeping date is missing.")
        _text(row["determination"], "retention determination", True)
    if kind == "destruction_approval":
        if (row["protections_reviewed"] is not True or not isinstance(row["retention_sha256"], str)
                or not SHA.fullmatch(row["retention_sha256"])):
            raise ValueError("File-policy destruction approval is damaged.")
        _text(row["determination"], "destruction determination", True)
    if kind == "recipient":
        if row["authority"] not in AUTHORITY or row["method"] not in METHODS or row["authority_reviewed"] is not True:
            raise ValueError("File-policy recipient approval is damaged.")
        for key in ("name", "authority_evidence", "destination"):
            _text(row[key], key, key != "destination" or row["method"] != "in_person")
    if kind == "inventory_approval":
        if (not isinstance(row["inventory_sha256"], str) or not SHA.fullmatch(row["inventory_sha256"])
                or not isinstance(row["decisions"], list) or len(row["decisions"]) > 10000):
            raise ValueError("File inventory approval is damaged.")
        seen = set()
        for choice in row["decisions"]:
            if (not isinstance(choice, dict) or set(choice) != {"path", "sha256", "input_sha256", "include", "reason"}
                    or not isinstance(choice["path"], str) or choice["path"] in seen
                    or type(choice["include"]) is not bool
                    or any(not isinstance(choice[key], str) or not SHA.fullmatch(choice[key]) for key in ("sha256", "input_sha256"))):
                raise ValueError("File inventory decision is damaged.")
            _text(choice["reason"], "inclusion or exclusion reason", True)
            seen.add(choice["path"])


def snapshot(case_dir, record=None, portal_root=None):
    import engagement
    import settings
    rec = record if record is not None else read(case_dir, portal_root)
    # Operational state changes invalidate approval, but never establish a legal
    # completion date. Exclude evolving letters, file/archive and policy history.
    lifecycle = engagement.read(Path(case_dir))
    p = profile()
    return digest({"identity": identity(case_dir, portal_root), "facts_revision": rec["facts_revision"],
                   "facts": rec["facts"], "profile": {key: p[key] for key in ("id", "version", "source_digest")},
                   "end": lifecycle.get("end"), "destroyed": lifecycle.get("destroyed"),
                   "office": engagement._office(Path(case_dir)), "firm_state": settings.values("firm").get("firm.state"),
                   "case_evidence": _case_evidence(case_dir)})


def _case_evidence(case_dir):
    out = {}
    for name in ("documents.json", "fact_graph.json", "fact_graph_raw.json", "fact_graph_reviewed.json"):
        path = Path(case_dir) / name
        if not path.exists() and not path.is_symlink():
            out[name] = None  # genuine pre-processing absence, not invented evidence
            continue
        _, raw = _json_bytes(path)
        out[name] = hashlib.sha256(raw).hexdigest()
    return out


def _current(row, bound, facts_revision):
    at = clock.parse(row.get("at")) if isinstance(row, dict) else None
    p = profile()
    return bool(isinstance(row, dict) and row.get("snapshot_sha256") == bound and row.get("role") == "attorney"
                and row.get("facts_revision") == facts_revision
                and row.get("profile") == {key: p[key] for key in ("id", "version", "source_digest")}
                and isinstance(row.get("by"), str) and row["by"].strip() and at is not None and at <= clock.now())


def applicability_holds(value):
    p = profile()
    held = []
    if value["profile_id"] != p["id"]:
        held.append("An attorney must select an available jurisdiction profile.")
    basis = value["choice_of_law_basis"]
    jurisdiction = {"tribunal": "tribunal_jurisdiction", "principal_office": "principal_office_jurisdiction",
                    "predominant_effect": "predominant_effect_jurisdiction"}.get(basis)
    if jurisdiction is None or value[jurisdiction].strip().upper() != p["jurisdiction"]:
        held.append("The attorney must establish the selected jurisdiction under the choice-of-law rule.")
    if not value["attorney_admissions"] or not value["principal_office"] or not value["principal_office_jurisdiction"]:
        held.append("Record attorney admissions and principal office.")
    if basis == "tribunal" and not value["tribunal"]:
        held.append("Record the tribunal for this choice of law.")
    if not value["applicability_reason"]:
        held.append("Record the attorney's applicability determination.")
    return held


def retention_holds(value):
    held = []
    if value["matter_type"] != "civil":
        held.append("Special or unknown matters require a separately reviewed retention and destruction policy.")
    if value["client_age_status"] == "unknown":
        held.append("Confirm adult or minor status for retention review.")
    if not value["representation_completed_on"]:
        held.append("An attorney must determine representation completion; case closure alone is insufficient.")
    if value["client_age_status"] == "minor" and not value["date_of_majority"]:
        held.append("Record the minor's date of majority.")
    if (value["client_age_status"] == "adult" and value["date_of_majority"] and value["representation_completed_on"]
            and value["date_of_majority"] > value["representation_completed_on"]):
        held.append("Age at representation completion conflicts with the recorded date of majority.")
    if any(row["active"] for row in value["holds"]):
        held.append("An active preservation hold prevents destruction approval.")
    if value["originals_status"] not in ("none_held", "returned"):
        held.append("Resolve retained or unreviewed originals before destruction approval.")
    if value["other_requirements"] != "reviewed_none":
        held.append("Review other retention requirements and protective orders.")
    return held


def _minimum(value):
    start = max(filter(None, (value["representation_completed_on"],
                              value["date_of_majority"] if value["client_age_status"] == "minor" else None)))
    start = date.fromisoformat(start)
    try:
        return start.replace(year=start.year + 6)
    except ValueError:
        # February 29 in a non-leap anniversary year; no other invalid date is
        # accepted by the facts validator. An unsupported far-future year holds.
        if start.month == 2 and start.day == 29 and start.year + 6 <= 9999:
            return start.replace(year=start.year + 6, day=28)
        raise ValueError("The retention minimum cannot be established.") from None


def view(case_dir, portal_root=None):
    rec = read(case_dir, portal_root)
    bound = snapshot(case_dir, rec, portal_root)
    app_holds = applicability_holds(rec["facts"])
    approved = _current(rec["applicability_approval"], bound, rec["facts_revision"]) and not app_holds
    held = retention_holds(rec["facts"])
    retention = rec["retention_approval"]
    retained = bool(approved and not held and _current(retention, bound, rec["facts_revision"])
                    and date.fromisoformat(retention["keep_until"]) >= _minimum(rec["facts"]))
    destruction = rec["destruction_approval"]
    eligible = bool(retained and retention.get("keep_until") and retention["keep_until"] < clock.today().isoformat()
                    and _current(destruction, bound, rec["facts_revision"]) and destruction.get("retention_sha256") == digest(retention))
    def approval(row):
        return None if row is None else copy.deepcopy(row) | {"current": _current(row, bound, rec["facts_revision"])}
    return {"revision": rec["revision"], "facts_revision": rec["facts_revision"], "snapshot_sha256": bound,
            "state": "approved" if approved else "review_required", "profiles": [profile()],
            "schema": {"enums": ENUMS, "defaults": copy.deepcopy(DEFAULT_FACTS), "authority": AUTHORITY, "methods": METHODS,
                       "guidance": {"client_age_status": "Age at representation completion, including applicable minor protection; not age today.",
                                    "date_of_majority": "Attorney-determined majority date for minor protections; the minimum uses the later of completion and majority."}},
            "facts": copy.deepcopy(rec["facts"]), "applicability_approval": approval(rec["applicability_approval"]),
            "handover": {"policy_ready": approved, "holds": app_holds if not approved else []},
            "retention": {"state": "approved" if retained else "held", "holds": held, "approval": approval(retention)},
            "destruction": {"state": "approved" if eligible else "held", "holds": held, "approval": approval(destruction)},
            "recipient": approval(rec["recipient"]), "history": copy.deepcopy(rec["history"])}


def _mutation(fn):
    @wraps(fn)
    def change(case_dir, *args, who, role, portal_root=None, **kwargs):
        from portal.communication_consent import data_gate
        import jobs
        if role != "attorney":
            raise PermissionError("A currently authorized attorney must make this file-policy determination.")
        _text(who, "attorney's name", True)
        identity(case_dir, portal_root)
        # Same installation gate as purge.run, before the existing file lock.
        # A waiting purge does not prevent protective facts/hold updates; its
        # worker must recheck their binding before beginning destructive work.
        with data_gate(Path(case_dir).parent.parent):
            with jobs.case_lock(jobs.folder_for(Path(case_dir).parent), Path(case_dir).name, timeout=30):
                return locked_change(case_dir, *args, who=who, role=role, portal_root=portal_root, **kwargs)

    def locked_change(case_dir, *args, who, role, portal_root=None, **kwargs):
        if role != "attorney":
            raise PermissionError("A currently authorized attorney must make this file-policy determination.")
        actor = _text(who, "attorney's name", True)
        d = Path(case_dir)
        identity(d, portal_root)  # no directory or lock creation on invalid identity
        with oslock.locked(d / "client-file.lock", timeout=30, poll=0.02):
            rec = read(d, portal_root)
            if rec["revision"] == 0 and fn.__name__ != "save_facts":
                raise ValueError("Save the current jurisdiction facts before recording an approval.")
            result = fn(d, rec, *args, who=actor, portal_root=portal_root, **kwargs)
            rec["revision"] += 1
            rec["history"].append({"revision": rec["revision"], "action": fn.__name__, "by": actor, "role": role,
                                   "at": clock.stamp(), "detail": copy.deepcopy(result)})
            raw = json.dumps(rec, ensure_ascii=False, indent=2).encode()
            if len(raw) > MAX_BYTES:
                raise ValueError("Policy history requires maintenance; no history was discarded.")
            if (d / TEMP).exists():
                raise ValueError("An interrupted policy publication needs review before another write.")
            fd = os.open(d / TEMP, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(d / TEMP, d / FILE)
            if os.name != "nt":
                fd = os.open(d, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        return view(d, portal_root)
    return change


def _expected(rec, revision):
    if type(revision) is not int or revision != rec["revision"]:
        raise Conflict("The file-policy record changed; reread before saving.")


def _approval(d, rec, expected, who, portal_root):
    bound = snapshot(d, rec, portal_root)
    if expected != bound:
        raise Conflict("The case, facts or policy source changed; reread before approval.")
    return {"by": who, "role": "attorney", "at": clock.stamp(), "snapshot_sha256": bound,
            "facts_revision": rec["facts_revision"],
            "profile": {key: profile()[key] for key in ("id", "version", "source_digest")}}


@_mutation
def save_facts(d, rec, expected_revision, value, *, who, portal_root):
    _expected(rec, expected_revision)
    rec["facts"] = facts(value)
    rec["facts_revision"] += 1
    return {"facts_revision": rec["facts_revision"], "facts": copy.deepcopy(rec["facts"])}


@_mutation
def approve(d, rec, expected_revision, expected_snapshot_sha256, source_reviewed, *, who, portal_root):
    _expected(rec, expected_revision)
    if source_reviewed is not True or applicability_holds(rec["facts"]):
        raise ValueError("Review the official policy sources and this matter's jurisdiction before approving.")
    rec["applicability_approval"] = _approval(d, rec, expected_snapshot_sha256, who, portal_root) | {"source_reviewed": True}
    return copy.deepcopy(rec["applicability_approval"])


def require_handover(case_dir, portal_root=None):
    out = view(case_dir, portal_root)
    if not out["handover"]["policy_ready"]:
        raise ValueError("Current attorney-approved jurisdiction policy is required before client-file preparation or handover.")
    return out["snapshot_sha256"]


@_mutation
def approve_retention(d, rec, expected_revision, expected_snapshot_sha256, keep_until, determination, *, who, portal_root):
    _expected(rec, expected_revision)
    require_handover(d, portal_root)
    if retention_holds(rec["facts"]):
        raise ValueError("Resolve the matter's retention protections; unsupported cases remain held.")
    day = _date(keep_until, "keeping date", future=True)
    if day is None:
        raise ValueError("Record an explicit attorney-determined keeping date.")
    if date.fromisoformat(day) < _minimum(rec["facts"]):
        raise ValueError("The keeping date is below the draft profile's conservative minimum; no override is supported.")
    rec["retention_approval"] = _approval(d, rec, expected_snapshot_sha256, who, portal_root) | {
        "keep_until": day, "determination": _text(determination, "retention determination", True)}
    return copy.deepcopy(rec["retention_approval"])


@_mutation
def approve_destruction(d, rec, expected_revision, expected_snapshot_sha256, protections_reviewed, determination, *, who, portal_root):
    _expected(rec, expected_revision)
    out = view(d, portal_root)
    row = rec["retention_approval"]
    if (protections_reviewed is not True or out["retention"]["state"] != "approved"
            or row["keep_until"] >= clock.today().isoformat()):
        raise ValueError("Current retention review, elapsed keeping date and resolved protections are required; a reason cannot override them.")
    rec["destruction_approval"] = _approval(d, rec, expected_snapshot_sha256, who, portal_root) | {
        "retention_sha256": digest(row), "protections_reviewed": True,
        "determination": _text(determination, "destruction determination", True)}
    return copy.deepcopy(rec["destruction_approval"])


def require_destruction(case_dir, portal_root=None):
    out = view(case_dir, portal_root)
    if out["destruction"]["state"] != "approved":
        raise ValueError("Current case-specific legal destruction approval is required; closure and an office keeping period are insufficient.")
    return {"snapshot_sha256": out["snapshot_sha256"], "approval_sha256": digest(out["destruction"]["approval"])}


@_mutation
def save_recipient(d, rec, expected_revision, expected_snapshot_sha256, value, *, who, portal_root):
    _expected(rec, expected_revision)
    require_handover(d, portal_root)
    if (not isinstance(value, dict) or set(value) != {"name", "authority", "authority_evidence", "method", "destination", "authority_reviewed"}
            or value["authority"] not in AUTHORITY or value["method"] not in METHODS or value["authority_reviewed"] is not True):
        raise ValueError("Review recipient authority and the selected delivery method.")
    clean = {key: _text(value[key], key, key != "destination" or value["method"] != "in_person")
             for key in ("name", "authority_evidence", "destination")}
    rec["recipient"] = _approval(d, rec, expected_snapshot_sha256, who, portal_root) | {
        **clean, "authority": value["authority"], "method": value["method"], "authority_reviewed": True}
    return copy.deepcopy(rec["recipient"])


@_mutation
def approve_inventory(d, rec, expected_revision, expected_snapshot_sha256, expected_inventory_sha256, decisions, *, who, portal_root):
    _expected(rec, expected_revision)
    require_handover(d, portal_root)
    current = client_file.inventory(d, _portal(portal_root))
    if current["snapshot_sha256"] != expected_inventory_sha256 or current["warnings"]:
        raise Conflict("The current file inventory changed or has unresolved evidence warnings; reread before review.")
    row = _approval(d, rec, expected_snapshot_sha256, who, portal_root) | {
        "inventory_sha256": expected_inventory_sha256, "decisions": copy.deepcopy(decisions)}
    _validate_approval(row, "inventory_approval")
    choices = {choice["path"]: choice for choice in decisions}
    if set(choices) != {entry["path"] for entry in current["entries"]}:
        raise ValueError("Review every current inventory entry explicitly.")
    for entry in current["entries"]:
        choice = choices[entry["path"]]
        if (choice["sha256"] != entry["sha256"] or choice["input_sha256"] != entry["input_sha256"]
                or entry["required"] and not choice["include"] or choice["include"] and not entry["reviewable"]):
            raise ValueError("Review the exact current candidate bytes and supply a safe alternative for unsupported material.")
    rec["inventory_approval"] = row
    return copy.deepcopy(row)


def _portal(portal_root):
    import engagement
    return engagement._portal_root(portal_root)


def handover_binding(case_dir, portal_root=None):
    out = view(case_dir, portal_root)
    require_handover(case_dir, portal_root)
    rec = read(case_dir, portal_root)
    row = rec["inventory_approval"]
    recipient = rec["recipient"]
    if (not _current(row, out["snapshot_sha256"], rec["facts_revision"])
            or not _current(recipient, out["snapshot_sha256"], rec["facts_revision"])):
        raise ValueError("Current explicit inventory and recipient-authority reviews are required before handover preparation.")
    current = client_file.inventory(case_dir, _portal(portal_root))
    if row["inventory_sha256"] != current["snapshot_sha256"] or current["warnings"]:
        raise ValueError("The current client-file bytes or evidence changed; review the inventory again.")
    return {"policy_snapshot_sha256": out["snapshot_sha256"], "inventory_sha256": row["inventory_sha256"],
            "inventory_approval_sha256": digest(row), "recipient_sha256": digest(recipient)}


def display(case_dir, portal_root=None):
    """Own-case DTO: a legacy identity hold must not break the Agreement tab."""
    try:
        out = view(case_dir, portal_root)
    except (ValueError, OSError, PermissionError, TypeError, KeyError):
        return {"state": "unavailable", "reason": "Current case identity or policy evidence is unavailable; review the canonical intake and policy record.",
                "revision": None, "facts_revision": None, "snapshot_sha256": None, "profiles": [profile()],
                "schema": {"enums": ENUMS, "defaults": copy.deepcopy(DEFAULT_FACTS), "authority": AUTHORITY, "methods": METHODS,
                           "guidance": {"client_age_status": "Age at representation completion, including minor protection; not age today."}},
                "facts": copy.deepcopy(DEFAULT_FACTS), "applicability_approval": None, "recipient": None, "history": [],
                "handover": {"policy_ready": False, "holds": ["Review current case identity and policy evidence."],
                             "binding": None, "binding_sha256": None, "recipient_sha256": None},
                "retention": {"state": "held", "holds": [], "approval": None}, "destruction": {"state": "held", "holds": [], "approval": None},
                "inventory": {"state": "unavailable", "snapshot_sha256": None, "entries": [], "excluded": [], "warnings": [], "approval": None}}
    out["handover"].update(binding=None, binding_sha256=None, recipient_sha256=None)
    try:
        inv = client_file.inventory(case_dir, _portal(portal_root))
        rec = read(case_dir, portal_root)
        approved = rec["inventory_approval"]
        current = bool(_current(approved, out["snapshot_sha256"], rec["facts_revision"])
                       and approved["inventory_sha256"] == inv["snapshot_sha256"] and not inv["warnings"])
        if current:
            choices = {row["path"]: row for row in approved["decisions"]}
            for entry in inv["entries"]:
                choice = choices[entry["path"]]
                entry.update(inclusion="include" if choice["include"] else "exclude", reason=choice["reason"])
        inv.update(state="approved" if current else "review_required", approval=None if approved is None else approved | {"current": current})
        out["inventory"] = inv
        recipient = rec["recipient"]
        binding = None
        if out["handover"]["policy_ready"] and current and _current(recipient, out["snapshot_sha256"], rec["facts_revision"]):
            binding = {"policy_snapshot_sha256": out["snapshot_sha256"], "inventory_sha256": approved["inventory_sha256"],
                       "inventory_approval_sha256": digest(approved), "recipient_sha256": digest(recipient)}
        out["handover"].update(binding_sha256=None if binding is None else digest(binding),
                               binding=binding, recipient_sha256=None if binding is None else binding["recipient_sha256"])
    except (ValueError, OSError, PermissionError, TypeError, KeyError):
        out["inventory"] = {"state": "unavailable", "snapshot_sha256": None, "entries": [], "excluded": [],
                            "warnings": ["The current file inventory is unavailable; resolve its evidence or pending purge before preparation."], "approval": None}
    return out
