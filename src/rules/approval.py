"""The attorney's approval of a rule or firm policy for volume use: who, when,
and a hash of the rule's text as it read when they approved it.

Every rule (src/rules/definitions.py) and firm policy
(schemas/law/policy_sijs.json) is drafted and unapproved until an attorney
approves it here (docs/decisions.md, "Rule sign-off"). An approval holds
only for the text the attorney read: when the rule's plain text, source,
conditions or code change, its hash no longer matches and the rule shows as
"changed since approval" until an attorney approves it again. Approving a
rule here does not decide any one client's answers; those are still
confirmed on each case's Attorney sign-off tab.

The record is the firm's own file, data/rules_approved.json (never shipped
in the repo; I485_RULES_APPROVED points elsewhere, as tests do). Every
approval is kept: the latest one per rule is the one that counts.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import clock
import events
import schema_path
from portal.communication_consent import data_mutation

REPO = Path(__file__).resolve().parents[2]
POLICY_PROFILE = schema_path.path("law", "policy_sijs")
_LOCK = threading.Lock()


def path() -> Path:
    """Read at call time, so a test (or a test world's server) keeps its own file."""
    return Path(os.environ.get("I485_RULES_APPROVED") or REPO / "data" / "rules_approved.json")


def _policies() -> tuple:
    """The shipped policies, each as the firm has it now (the attorney's edits in Settings, src/rules/firm_policies.py): an approval
    holds for these words, so an edited policy shows "changed since approval" until an attorney approves it again."""
    from rules import firm_policies

    log = firm_policies.edits()
    return tuple(firm_policies.effective(p, log) for p in json.loads(POLICY_PROFILE.read_text(encoding="utf-8"))["policies"])


def _rule_hash(rule) -> str:
    try:
        code = inspect.getsource(rule.apply)
    except (OSError, TypeError):  # a rule built in a test, without a source file
        code = getattr(rule.apply, "__qualname__", "")
    text = {"plain_text": rule.plain_text, "source": rule.source, "inputs": rule.inputs, "outputs": rule.outputs,
            "requires_absent": rule.requires_absent, "code": code}
    return hashlib.sha256(json.dumps(text, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _policy_hash(policy: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(policy, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def catalog() -> list[dict[str, Any]]:
    """Every rule and firm policy: {id (as facts carry it: "OVERSTAY-01", "POLICY:NO-CREWMAN"),
    kind, code, plain_text, source, hash; a policy also name, enabled and edit (how the firm's version differs from the shipped one)}.
    Read again only when the policy profile or the firm's edits change."""
    import engagement
    import g28
    from rules import firm_policies

    import part14_voice
    import client_language_readiness

    return [dict(r) for r in _catalog(POLICY_PROFILE.stat().st_mtime, firm_policies.stamp(), engagement.stamp(), g28.stamp(), part14_voice.stamp(), client_language_readiness.stamp())]


@lru_cache(maxsize=4)
def _catalog(_stamp: float, _firm: tuple, _letters: tuple = (), _g28: tuple = (), _voice: tuple = (), _client_wording: tuple = ()) -> tuple:
    from rules import ALL_RULES, firm_policies

    log = firm_policies.edits()
    shipped = {p["id"]: p for p in json.loads(POLICY_PROFILE.read_text(encoding="utf-8"))["policies"]}

    out = [{"id": r.rule_id, "kind": "rule", "code": r.rule_id, "plain_text": r.plain_text, "source": r.source, "hash": _rule_hash(r)}
           for r in ALL_RULES]
    out += [{"id": f"POLICY:{p['id']}", "kind": "policy", "code": p["id"], "name": p.get("name") or "",
             "plain_text": p.get("plain_text") or p.get("why", ""),
             "source": p.get("source", ""), "hash": _policy_hash(p),
             "enabled": firm_policies.enabled(p["id"], log), "edit": firm_policies.note(shipped[p["id"]], log)} for p in _policies()]
    # a firm practice approved the same way: drafting the client's declaration (src/drafting.py, docs/design_plan.md Part 6)
    import drafting

    practice = {"plain_text": drafting.PRACTICE, "source": drafting.PRACTICE_SOURCE}
    out.append({"id": drafting.PRACTICE_ID, "kind": "practice", "code": drafting.PRACTICE_ID.split(":", 1)[1], "name": "Drafting the client's declaration",
                **practice, "hash": hashlib.sha256(json.dumps(practice, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()})
    # and the firm's letters to clients (src/engagement.py): the approval holds for every office's wording as it stands
    import engagement

    out.append(engagement.practice_entry())
    # and the G-28's choices, office by office (src/g28.py): the approval holds for every office's choices as they stand
    import g28

    out.append(g28.practice_entry())
    # and the questions about a case: its text is the instruction the local model receives, word for word (src/case_questions.py)
    import case_questions

    practice = {"plain_text": case_questions.PRACTICE, "source": case_questions.PRACTICE_SOURCE}
    out.append({"id": case_questions.PRACTICE_ID, "kind": "practice", "code": case_questions.PRACTICE_ID.split(":", 1)[1], "name": case_questions.PRACTICE_NAME,
                **practice, "hash": hashlib.sha256(json.dumps(practice, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()})
    # and the voice Part 14 entries are written in: each office's choice is what the approval holds for (src/part14_voice.py)
    import part14_voice

    out.append(part14_voice.practice_entry())
    # and the local model's one job on the firm's wording library: choosing a number among the firm's own approved wordings (src/wordings.py)
    import wordings

    out.append(wordings.practice_entry())
    import client_language_readiness
    out.extend(client_language_readiness.practice_entries())
    return tuple(out)


def _load() -> dict[str, list[dict[str, Any]]]:
    p = path()
    if not p.exists():
        return {}
    st = p.stat()
    return json.loads(_read_cached(str(p), (st.st_mtime_ns, st.st_size)))  # the size too: two writes in one clock tick (a coarse file system) still differ


@lru_cache(maxsize=4)
def _read_cached(name: str, _stamp: tuple) -> str:
    """Every review card asks; the file is read again only when it changes."""
    return Path(name).read_text(encoding="utf-8")


def history() -> dict[str, list[dict[str, Any]]]:
    """{rule id: every approval, oldest first}: who, when and the hash of the text approved (the attorney's "What staff did" lists them)."""
    return _load()


def status(rule_id: str, current_hash: str | None = None, log: dict | None = None) -> dict[str, Any]:
    """{state: approved | changed | not_approved, by, role, at} for one rule or policy id."""
    if current_hash is None:
        current_hash = next((r["hash"] for r in catalog() if r["id"] == rule_id), None)
    records = (log if log is not None else _load()).get(rule_id) or []
    if not records:
        return {"state": "not_approved"}
    last = records[-1]
    return {"state": "approved" if last.get("hash") == current_hash else "changed",
            "by": last.get("by"), "role": last.get("role"), "at": last.get("at")}


def statuses() -> dict[str, dict[str, Any]]:
    """Every rule's and policy's approval state, read once."""
    log = _load()
    return {r["id"]: status(r["id"], r["hash"], log) for r in catalog()}


@data_mutation(lambda: path().parent)
def approve(rule_id: str, by: str, role: str | None = None) -> dict[str, Any]:
    """Records the attorney's approval of the rule's current text. Raises ValueError/LookupError with a reviewer-facing message."""
    by = (by or "").strip()
    if not by:
        raise ValueError("Enter your name first: every approval records who gave it.")
    rule = next((r for r in catalog() if r["id"] == rule_id), None)
    if rule is None:
        raise LookupError("No such rule or firm policy.")
    if rule.get("requires_review_evidence"):
        raise ValueError("Use the client wording review action with actual review evidence.")
    record = {"by": by, **({"role": role} if role else {}), "at": clock.stamp("seconds"),
              "hash": rule["hash"], "plain_text": rule["plain_text"], "source": rule["source"]}
    with _LOCK:
        from portal.communication_consent import _read
        from portal.queue_bridge import _atomic
        log = _read(path(), {})
        log.setdefault(rule_id, []).append(record)
        p = path()
        _atomic(p, log)
    events.record("policies", "approved", f"Approved a rule or firm policy for every case: {rule.get('name') or events.words(rule_id)}", home=p.parent, who=by, role=role)
    return status(rule_id, rule["hash"], log)


def record_review(rule_id: str, record: dict, *, target: Path) -> None:
    """Append a protected client-wording review to the existing approval history.

    The caller authenticates/reloads the attorney under the communication gate.
    Explicit target avoids silently selecting another installation's history.
    """
    from portal.queue_bridge import _atomic, _safe
    from portal.communication_consent import _read
    if not rule_id.startswith("PRACTICE:CLIENT-WORDING-") or record.get("role") != "attorney" or record.get("review_type") != "actual_attorney_wording_review":
        raise ValueError("Invalid client wording review.")
    with _LOCK:
        target = _safe(target)
        log = _read(target, {})
        rows = log.setdefault(rule_id, [])
        if not isinstance(rows, list):
            raise ValueError("Approval history is damaged.")
        rows.append(record)
        _atomic(target, log)
