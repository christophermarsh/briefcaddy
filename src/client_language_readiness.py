"""Current client wording reviews in the existing approval/upkeep records.

No language is certified by being in the picker, roster or maintenance date.
The firm must supply a notice and actual attorney/qualified-review evidence.
File digests identify the reviewed bundle; they are not a legal approval.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import clock
import events
import schema_path
from portal.queue_bridge import _atomic, _safe

LANGUAGES = ("en", "pt", "es", "ht")
ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "schemas/questions/intake.json", "schemas/questions/n400.json",
    "schemas/questions/parole.json", "schemas/questions/first_contact.json",
    "schemas/questions/help/portal.json", "schemas/registers/journey.json",
    "src/portal/static/portal.html", "src/portal/static/consent.html", "src/portal/notify.py", "src/portal/engine.py",
    "src/portal/questions.py", "src/signing_evidence.py", "src/engagement.py",
    "schemas/firm/firm_documents.json",
    schema_path.rel("packet", "companion_forms"), schema_path.rel("firm", "firm_profile"),
    schema_path.rel("cover_letter", "i485"),
)


def _notice(root):
    from portal.communication_consent import _read
    value = _read(_safe(Path(root) / "data" / "communication_notice.json"))
    if (not value or value.get("schema_version") != 1 or not isinstance(value.get("version"), str)
            or not value["version"].strip() or len(value["version"]) > 128
            or not isinstance(value.get("texts"), dict)):
        raise ValueError("The firm has not configured a consent notice for review.")
    return value


def bundle(root, language):
    import read_scope
    return read_scope.once(("client-wording", str(Path(root).absolute()), language), lambda: _bundle(root, language), copy=True)


def _bundle(root, language):
    if language not in LANGUAGES:
        raise ValueError("This language requires an explicit reviewed bundle.")
    root = _safe(root)
    notice = _notice(root)
    text = notice["texts"].get(language)
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 65536:
        raise ValueError("A notice in this language is required.")
    files = []
    for name in SOURCES:
        path = _safe(root / name)
        if not path.is_file():
            raise ValueError("The current client wording bundle is incomplete.")
        files.append([name, hashlib.sha256(path.read_bytes()).hexdigest()])
    value = {"version": 1, "language": language, "notice_version": notice["version"],
             "notice_sha256": hashlib.sha256(text.encode()).hexdigest(), "files": files}
    from portal.communication_consent import _read
    settings = _read(root / "data" / "settings.json", {})
    # Bind actual effective rendered inputs, not administrative settings or
    # history timestamps. An idle-timeout edit is not a wording change.
    office_ids = settings.get("_offices", [])
    if not isinstance(office_ids, list) or any(not isinstance(i, str) for i in office_ids):
        raise ValueError("Effective office configuration is damaged.")
    effective = {}
    for name in ("firm", "fees", *office_ids):
        section = settings.get(name, {})
        if not isinstance(section, dict) or not isinstance(section.get("values", {}), dict):
            raise ValueError("Effective client wording configuration is damaged.")
        effective[name] = section.get("values", {})
    translators = settings.get("_translators", [])
    if not isinstance(translators, list) or any(not isinstance(t, dict) for t in translators):
        raise ValueError("Effective translator configuration is damaged.")
    effective["translators"] = [{k: t.get(k) for k in ("id", "name", "languages", "kind", "competence", "organization", "address")} for t in translators]
    value["firm_wording_sha256"] = hashlib.sha256(json.dumps(effective, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    documents = _safe(root / "data" / "firm_documents.json")
    value["firm_documents_sha256"] = hashlib.sha256(documents.read_bytes() if documents.exists() else b"").hexdigest()
    return {**value, "digest": hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def practice_id(language):
    return "PRACTICE:CLIENT-WORDING-" + language.upper()


def practice_entries(root=ROOT):
    """Fresh digests are included in the existing catalog cache key."""
    entries = []
    for language in LANGUAGES:
        try:
            current = bundle(root, language)
        except (ValueError, OSError):
            continue
        entries.append({"id": practice_id(language), "code": "CLIENT-WORDING-" + language.upper(),
                        "kind": "practice", "name": "Client wording: " + language,
                        "plain_text": "Review the current client wording bundle and exact service-message consent notice in " + language + ".",
                        "source": "Firm client_wording maintenance item and retained review evidence",
                        "hash": current["digest"], "language": language, "bundle": current,
                        "requires_review_evidence": True})
    return entries


def stamp(root=ROOT):
    return tuple((entry["id"], entry["hash"]) for entry in practice_entries(root))


def review_attorney(scope, language, *, actor_email, evidence_ref):
    from portal.communication_consent import evidence, gate, staff
    from rules import approval
    with gate(scope):
        actor = staff(scope, actor_email, attorney=True)
        evidence(scope, evidence_ref)
        bundle(scope.root, language)  # Report missing notice/bundle as a useful 400, not StopIteration.
        entry = next((e for e in practice_entries(scope.root) if e["language"] == language), None)
        if entry is None:
            raise ValueError("Configure the current client consent notice before recording a wording review.")
        record = {"by": actor.get("name") or actor_email, "actor_email": actor_email, "role": "attorney",
                  "at": clock.stamp(), "hash": entry["hash"], "plain_text": entry["plain_text"],
                  "source": entry["source"], "evidence": evidence_ref, "language": language,
                  "bundle": entry["bundle"], "review_type": "actual_attorney_wording_review"}
        approval.record_review(entry["id"], record, target=scope.data / "rules_approved.json")
        audited = events.record("policies", "client_wording_reviewed", "Attorney reviewed current client wording.", home=scope.data, who=actor_email, role="attorney")
        return dict(record, audit_status="recorded" if audited else "unavailable")


def review_translation(scope, language, *, actor_email, reviewer_name, qualification, evidence_ref):
    """Attorney records an actual qualified review, not a roster inference."""
    from portal.communication_consent import evidence, gate, staff, _read
    if language not in {"pt", "es", "ht"}:
        raise ValueError("Choose the actual translation reviewed.")
    for value in (reviewer_name, qualification):
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError("Actual reviewer and qualification evidence are required.")
    with gate(scope):
        staff(scope, actor_email, attorney=True)
        evidence(scope, evidence_ref)
        current = bundle(scope.root, language)
        path = _safe(scope.data / "maintenance_log.json")
        log = _read(path, {})
        item = log.setdefault("client_wording", {"log": []})
        if not isinstance(item, dict) or not isinstance(item.get("log"), list):
            raise ValueError("Client wording upkeep record is damaged.")
        row = {"on": clock.stamp(), "by": actor_email, "actor_email": actor_email, "role": "attorney",
               "review_type": "actual_qualified_translation_review", "language": language,
               "reviewer": reviewer_name.strip(), "qualification": qualification.strip(),
               "hash": current["digest"], "evidence": evidence_ref}
        item["log"].append(row)
        _atomic(path, log)
        audited = events.record("upkeep", "translation_reviewed", "Recorded qualified client wording review.", home=scope.data, who=actor_email, role="attorney")
        return dict(row, audit_status="recorded" if audited else "unavailable")


def readiness(scope, language, *, notice_version=None):
    from portal.communication_consent import evidence, gate, staff, _read
    with gate(scope):
        try:
            current = bundle(scope.root, language)
            if notice_version is not None and notice_version != current["notice_version"]:
                return {"ready": False, "reason": "notice_changed", **current}
            log = _read(scope.data / "rules_approved.json", {})
            rows = log.get(practice_id(language), [])
            if not isinstance(rows, list) or not rows:
                return {"ready": False, "reason": "attorney_review_required", **current}
            row = rows[-1]
            if (not isinstance(row, dict) or row.get("hash") != current["digest"] or row.get("role") != "attorney"
                    or row.get("review_type") != "actual_attorney_wording_review" or row.get("language") != language):
                return {"ready": False, "reason": "attorney_review_required", **current}
            staff(scope, row.get("actor_email"), attorney=True)
            evidence(scope, row.get("evidence"))
            if language != "en":
                upkeep = _read(scope.data / "maintenance_log.json", {}).get("client_wording", {})
                translations = [r for r in upkeep.get("log", []) if isinstance(r, dict)
                                and r.get("review_type") == "actual_qualified_translation_review" and r.get("language") == language]
                translated = translations[-1] if translations else {}
                if translated.get("hash") != current["digest"] or translated.get("role") != "attorney" or not translated.get("reviewer") or not translated.get("qualification"):
                    return {"ready": False, "reason": "qualified_translation_review_required", **current}
                staff(scope, translated.get("actor_email"), attorney=True)
                evidence(scope, translated.get("evidence"))
            return {"ready": True, "reason": "reviewed", **current}
        except (ValueError, OSError, PermissionError, LookupError, TypeError, KeyError, AttributeError):
            return {"ready": False, "reason": "review_evidence_unavailable"}
