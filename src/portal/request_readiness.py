"""Current request wording proof, within existing requests and R2 history."""
from datetime import datetime
import hashlib
import json

import clock
from client_language_readiness import readiness
from .communication_consent import _open, _safe, evidence, gate, staff
from .queue_bridge import _atomic


def scope_for(store):
    root = store.root.absolute()
    canonical = root.parent.name == "data" or (root.name == "prospects" and root.parent.name == "portal" and root.parent.parent.name == "data")
    return store.communication_scope() if canonical else None  # non-authenticating legacy utility stores


def _content(request, language):
    text = request.get("text_en") or request.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 600 or request.get("type", "text") not in ("text", "date", "yes_no", "choice"):
        raise ValueError("The complete English question is required.")
    options = request.get("options") or []
    if not isinstance(options, list) or len(options) > 8:
        raise ValueError("Complete choice labels are required.")
    if request.get("type") == "choice":
        if len(options) < 2 or any(not isinstance(o, dict) or not isinstance(o.get("value"), str) or not o["value"].strip()
                                 or not isinstance(o.get("en"), str) or not o["en"].strip() for o in options):
            raise ValueError("Complete choice values and English labels are required.")
        if len({o["value"] for o in options}) != len(options):
            raise ValueError("Choice values must be unique.")
    elif options:
        raise ValueError("Only a choice request carries choice labels.")
    return {"id": request.get("id"), "text": text, "type": request.get("type", "text"), "doc_id": request.get("doc_id"),
            "language": language, "request_language": request.get("language"), "text_client": request.get("text_client"),
            "options": options}


def _digest(content, bundle):
    return hashlib.sha256(json.dumps({"content": content, "bundle": bundle}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _fixed_bank(scope, profile, content):
    from .bank import load_bank
    bank = load_bank(_safe(scope.root / "schemas/questions/intake.json"), filing=profile.get("filing"))
    doc = next((d for d in bank["documents"] if d["id"] == content["doc_id"]), None)
    labels = doc.get("label") or {} if doc else {}
    return bool(doc and content["type"] == "text" and not content["options"] and content["text"] == labels.get("en")
                and content["request_language"] == content["language"] and content["text_client"] == labels.get(content["language"]))


def check(scope, store, client, request):
    """Pure view: malformed/changed evidence is a visible hold, never a grant."""
    try:
        _, profile, _ = _open(scope, store, client)
        current = readiness(scope, profile.get("language"))
        if not current["ready"]:
            return {"ready": False, "reason": "current_language_bundle_review_required"}
        content = _content(request, profile["language"])
        digest = _digest(content, current["digest"])
        if _fixed_bank(scope, profile, content):
            return {"ready": True, "reason": "exact_reviewed_bank_content", "digest": digest}
        row = request.get("language_review")
        history = request.get("language_reviews")
        if (not isinstance(row, dict) or not isinstance(history, list) or not history or history[-1] != row
                or row.get("version") != 1 or row.get("digest") != digest or row.get("language") != profile["language"]
                or row.get("mode") not in ("as_written", "translated", "english_fallback")
                or row.get("actor_role") not in ("attorney", "paralegal") or not isinstance(row.get("actor"), str) or "@" not in row["actor"]
                or not isinstance(row.get("at"), str)):
            return {"ready": False, "reason": "request_language_review_required"}
        when = datetime.fromisoformat(row["at"])
        if when.tzinfo is None or when > clock.utcnow():
            raise ValueError("Request review time is unavailable.")
        _mode(content, row)
        ref = row.get("evidence")
        if not isinstance(ref, dict) or ref.get("kind") != "request_wording":
            raise ValueError("Own request wording evidence is required.")
        evidence(scope, ref, client=client)
        return {"ready": True, "reason": row["mode"], "digest": digest, "fallback_reason": row.get("fallback_reason")}
    except (ValueError, PermissionError, OSError, LookupError, TypeError, KeyError, AttributeError):
        return {"ready": False, "reason": "request_language_evidence_unavailable"}


def _mode(content, row):
    mode, lang = row.get("mode"), content["language"]
    if mode == "as_written":
        if lang != "en" or content["request_language"] != "en" or content["text_client"] != content["text"]:
            raise ValueError("As-written review is only for the current complete English question.")
        if any(o.get("client") not in ("", o["en"]) for o in content["options"]):
            raise ValueError("English choices cannot use mixed translated labels.")
    elif mode == "translated":
        if lang == "en" or content["request_language"] != lang or not isinstance(content["text_client"], str) or not content["text_client"].strip():
            raise ValueError("The complete current client-language question is required.")
        if any(not isinstance(o.get("client"), str) or not o["client"].strip() for o in content["options"]):
            raise ValueError("Every translated choice label is required.")
        for name in ("reviewer_name", "qualification"):
            if not isinstance(row.get(name), str) or not row[name].strip() or len(row[name]) > 2000:
                raise ValueError("Actual qualified review provenance is required.")
    elif mode == "english_fallback":
        if lang == "en" or not isinstance(row.get("fallback_reason"), str) or not row["fallback_reason"].strip() or len(row["fallback_reason"]) > 2000:
            raise ValueError("Record the explicit evidenced choice of whole English fallback.")
    else:
        raise ValueError("Choose an explicit wording review mode.")


def review_request(scope, store, client, request_id, *, actor_email, evidence_ref, mode,
                   reviewer_name=None, qualification=None, fallback_reason=None, publish=False):
    """Current protected actor records actual review/choice, never a body flag."""
    with gate(scope):
        _, profile, case = _open(scope, store, client)
        actor = staff(scope, actor_email, case=case)
        if not isinstance(evidence_ref, dict) or evidence_ref.get("kind") != "request_wording":
            raise ValueError("Own request wording evidence is required.")
        evidence(scope, evidence_ref, client=client)
        current = readiness(scope, profile.get("language"))
        if not current["ready"]:
            raise ValueError("The current global language bundle review is required.")
        with store._lock:
            requests = store.requests(client)
            request = next((r for r in requests if r.get("id") == request_id), None)
            if request is None or request.get("status") not in ("draft", "open"):
                raise ValueError("This own draft/open request is unavailable for review.")
            content = _content(request, profile["language"])
            row = {"version": 1, "mode": mode, "language": profile["language"], "digest": _digest(content, current["digest"]),
                   "actor": actor["email"], "actor_role": actor["role"], "at": clock.stamp(), "evidence": evidence_ref,
                   "reviewer_name": reviewer_name, "qualification": qualification, "fallback_reason": fallback_reason}
            _mode(content, row)
            history = request.setdefault("language_reviews", [])
            if not isinstance(history, list):
                raise ValueError("Request review history is damaged.")
            history.append(row)
            request.update(language_review=row, language_hold=None)
            if publish:
                request.update(status="open", asked_at=clock.stamp(), sent_by=actor_email)
            staff(scope, actor_email, case=case)
            _atomic(scope.check(store, client) / "requests.json", requests)
        import events
        kind, case_id, home = store._ledger_where(client, "portal")
        audit = events.record(kind, "request_language_reviewed", "Recorded current client request language review or explicit English choice.",
                              case=case_id, home=home, who=actor_email, role=actor["role"])
        return {"request": request, "ready": True, "published": bool(publish), "notified": False,
                "audit_status": "recorded" if audit else "unavailable"}


def notification_hold(scope, store, client):
    with gate(scope):
        for request in store.requests(client):
            if request.get("status") == "draft":
                return "Request remains a draft; review/publish it before notifying the client."
            if request.get("status") == "open" and not check(scope, store, client, request)["ready"]:
                return "Client request language review is incomplete or changed; no request notification was sent."
        return None
