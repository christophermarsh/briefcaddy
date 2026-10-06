"""The firm's own version of its standard answers: what the attorney changed in Settings, kept apart from the shipped schema.

The shipped policies (schemas/law/policy_sijs.json) are the product's starting point: each has a name, a plain text, the answers it
sets and the conditions under which it fires. An attorney may change, for the whole firm, three things about a policy and nothing
else: its plain text (the words every sign-off card and the review bundle show), the answer each of its boxes gets (only a value
the box can take: Yes or No for a Yes/No box, one of its options for a choice box, a short text for a text box), and whether it
applies at all (switched off, it sets nothing). Its conditions, and the built-in rules (src/rules/definitions.py, whose text is
checked against their code), are not editable.

The edits are the firm's own file, data/policies_firm.json (I485_POLICIES_FIRM points elsewhere, as tests do; never in the repo,
never the shipped schema). Like an approval (src/rules/approval.py) every edit is kept: who, their role, when, the text and answers
before and after, and the hash of the policy as it then read. The latest record per policy is the firm's version; the engine reads
it over the shipped one (rules.policy.load_policy_profile), and an approval of the old text then shows as "changed since approval"
until an attorney approves the new wording again. An edit is not an approval.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import clock
import events
import schema_path

REPO = Path(__file__).resolve().parents[2]
SHIPPED = schema_path.path("law", "policy_sijs")
MAX_TEXT = 1000  # characters of plain text: a sentence or two, not a document
MAX_ANSWER = 100  # characters of a typed answer (a text box on the form)
_LOCK = threading.Lock()


def path() -> Path:
    """Read at call time, so a test (or a test world's server) keeps its own file."""
    return Path(os.environ.get("I485_POLICIES_FIRM") or REPO / "data" / "policies_firm.json")


def stamp() -> tuple:
    """Changes whenever the firm's file does: the key for every cache that depends on the engine's policies."""
    p = path()
    st = p.stat() if p.exists() else None
    return (str(p), st.st_mtime_ns if st else 0, st.st_size if st else 0)


@lru_cache(maxsize=4)
def _read(name: str, _stamp: tuple) -> dict[str, list[dict[str, Any]]]:
    return json.loads(Path(name).read_text(encoding="utf-8")).get("edits") or {}


def edits() -> dict[str, list[dict[str, Any]]]:
    """{policy id: every edit, oldest first}."""
    p = path()
    if not p.exists():
        return {}
    st = p.stat()
    return _read(str(p), (st.st_mtime_ns, st.st_size))  # the size too: two edits in one clock tick (a coarse file system) still differ


def latest(policy_id: str, log: dict | None = None) -> dict[str, Any] | None:
    records = (log if log is not None else edits()).get(policy_id) or []
    return records[-1] if records else None


def policy_hash(policy: dict[str, Any]) -> str:
    """The hash an approval holds for (rules.approval): the policy as the engine reads it."""
    return hashlib.sha256(json.dumps(policy, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _merged(policy: dict[str, Any], record: dict[str, Any] | None) -> dict[str, Any]:
    """The policy with the firm's latest wording and answers over the shipped ones (switched off or not: see apply)."""
    if not record:
        return policy
    out = dict(policy)
    if record.get("plain_text"):
        out["plain_text"] = record["plain_text"]
    if record.get("set"):
        out["set"] = {key: record["set"].get(key, value) for key, value in policy.get("set", {}).items()}
    return out


def apply(policies: list[dict[str, Any]], log: dict | None = None) -> list[dict[str, Any]]:
    """The policies the engine uses: the firm's version of each over the shipped one, a switched-off policy left out."""
    log = edits() if log is None else log
    out = []
    for policy in policies:
        record = latest(policy["id"], log)
        if record is not None and record.get("enabled") is False:
            continue
        out.append(_merged(policy, record))
    return out


def shipped() -> list[dict[str, Any]]:
    return json.loads(SHIPPED.read_text(encoding="utf-8"))["policies"]


def effective(policy: dict[str, Any], log: dict | None = None) -> dict[str, Any]:
    """The policy as the firm has it now, including a switched-off one (it still shows in Settings and in the catalog)."""
    return _merged(policy, latest(policy["id"], log))


def enabled(policy_id: str, log: dict | None = None) -> bool:
    record = latest(policy_id, log)
    return not (record is not None and record.get("enabled") is False)


def note(policy: dict[str, Any], log: dict | None = None) -> dict[str, Any] | None:
    """How the firm's version differs from the shipped one, for the sign-off card and the review bundle:
    {by, role, at, wording, answer, off, text}, text being "The firm's wording, edited by Ana Attorney on 10/02/2026". None when the
    firm's version is the shipped one (never edited, or edited back)."""
    record = latest(policy["id"], log)
    if record is None:
        return None
    mine = _merged(policy, record)
    wording, answer = mine.get("plain_text") != policy.get("plain_text"), mine.get("set") != policy.get("set")
    off = record.get("enabled") is False
    if not (wording or answer or off):
        return None
    what = "the firm's wording and answer" if wording and answer else "the firm's wording" if wording else "the firm's answer" if answer else None
    when = clock.us_date(record.get("at"))
    parts = ([f"{what[0].upper()}{what[1:]}, edited by {record.get('by')} on {when}"] if what else []) + ([f"switched off by {record.get('by')} on {when}"] if off else [])
    text = "; ".join(parts)
    text = text[0].upper() + text[1:]
    # the last edit that changed the words or an answer (not a switch on or off): the one an approval of this text was given for
    edited_at = next((r.get("at") for r in reversed((log if log is not None else edits()).get(policy["id"]) or [])
                      if r.get("plain_text") != r.get("old_plain_text") or r.get("set") != r.get("old_set")), record.get("at"))
    return {"by": record.get("by"), "role": record.get("role"), "at": record.get("at"), "edited_at": edited_at, "wording": wording, "answer": answer, "off": off, "text": text}


# -- editing -------------------------------------------------------------------------------------


def _check_text(text: Any) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        raise ValueError("Write what the policy says: every sign-off card shows these words.")
    if len(text) > MAX_TEXT:
        raise ValueError(f"The policy's words are {len(text)} characters: keep them under {MAX_TEXT}.")
    return text


def check_answer(key: str, value: Any, spec: dict[str, Any] | None, label: str = "") -> str:
    """The answer for one box, only if the box can take it. spec: the box's input (review.state Catalog.input): choice (Yes/No or the
    form's own options) or text with an optional length. A box the form doesn't have (a value the review app derives) takes a short text."""
    value = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    what = f"“{label}”" if label else "this box"
    if not value:
        raise ValueError(f"Give an answer for {what}.")
    spec = spec or {"type": "text"}
    if spec.get("type") == "choice":
        options = [str(o) for o in spec.get("options") or []]
        if value not in options:
            raise ValueError(f"The answer for {what} is one of: {', '.join(options)}.")
        return value
    if spec.get("type") == "date":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(f"The answer for {what} is a date.")
        return value
    limit = min(MAX_ANSWER, spec.get("maxlen") or MAX_ANSWER)
    if len(value) > limit:
        raise ValueError(f"The answer for {what} has room for {limit} characters.")
    if spec.get("digits") and not value.isdigit():
        raise ValueError(f"The answer for {what} is digits only.")
    return value.upper()  # words go in capitals, as every typed answer on the form does (portal/questions.py)


def edit(policy_id: str, by: str, role: str | None = None, *, plain_text: Any = None, answers: dict[str, Any] | None = None,
         enabled_: bool | None = None, input_for: Callable[[str], dict[str, Any]] | None = None,
         label_for: Callable[[str], str] | None = None) -> dict[str, Any]:
    """Records the attorney's change to one policy for the whole firm; returns the record. Nothing given: nothing to change.
    answers: {fact key: value} for boxes the policy sets, each checked against input_for(key); a key the policy does not set is refused.
    Raises ValueError/LookupError with a reviewer-facing message."""
    by = (by or "").strip()
    if not by:
        raise ValueError("Enter your name first: every edit records who made it.")
    policy = next((p for p in shipped() if p["id"] == policy_id), None)
    if policy is None:
        raise LookupError("No such firm policy. The built-in rules can't be edited.")
    with _LOCK:
        log = json.loads(path().read_text(encoding="utf-8")) if path().exists() else {}
        history = log.setdefault("edits", {}).setdefault(policy_id, [])
        before = _merged(policy, history[-1] if history else None)
        was_on = not (history and history[-1].get("enabled") is False)
        text = _check_text(plain_text) if plain_text is not None else before["plain_text"]
        values = dict(before.get("set", {}))
        for key, value in (answers or {}).items():
            if key not in policy.get("set", {}):
                raise ValueError("That policy doesn't set that answer.")
            values[key] = check_answer(key, value, (input_for or (lambda k: None))(key), (label_for or (lambda k: ""))(key))
        on = was_on if enabled_ is None else bool(enabled_)
        if text == before["plain_text"] and values == before.get("set") and on == was_on:
            raise ValueError("Nothing changed.")
        record = {"by": by, **({"role": role} if role else {}), "at": clock.stamp("seconds"), "plain_text": text, "set": values, "enabled": on,
                  "old_plain_text": before["plain_text"], "old_set": before.get("set", {}), "old_enabled": was_on,
                  "hash": policy_hash(_merged(policy, {"plain_text": text, "set": values}))}
        history.append(record)
        _save(log)
    parts = [w for w, same in (("the words", text == before["plain_text"]), ("the answers", values == before.get("set")), ("whether it applies", on == was_on)) if not same]
    events.record("policies", "edited", f"Edited the firm policy {policy.get('name') or policy_id}: " + " and ".join(parts), home=path().parent, who=by, role=role)
    return record


def _save(log: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(log, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def revert(policy_id: str, by: str, role: str | None = None) -> dict[str, Any]:
    """The attorney puts one policy's words and answers back to what the product shipped with. Recorded like any edit (who, when, the text
    and answers before and after, and the hash of the policy as it then reads), so the history keeps what was undone; the shipped schema is
    never written. Whether the policy applies is not touched (that is its own switch). An approval given to the firm's wording then no longer
    matches, so the policy shows "changed since approval" until an attorney approves it again."""
    by = (by or "").strip()
    if not by:
        raise ValueError("Enter your name first: every change records who made it.")
    policy = next((p for p in shipped() if p["id"] == policy_id), None)
    if policy is None:
        raise LookupError("No such firm policy. The built-in rules can't be edited.")
    with _LOCK:
        log = json.loads(path().read_text(encoding="utf-8")) if path().exists() else {}
        history = log.setdefault("edits", {}).setdefault(policy_id, [])
        before = _merged(policy, history[-1] if history else None)
        was_on = not (history and history[-1].get("enabled") is False)
        if before.get("plain_text") == policy.get("plain_text") and before.get("set") == policy.get("set"):
            raise ValueError("This policy already says what the product shipped with.")
        values = dict(policy.get("set", {}))
        record = {"by": by, **({"role": role} if role else {}), "at": clock.stamp("seconds"), "plain_text": policy["plain_text"], "set": values,
                  "enabled": was_on, "old_plain_text": before["plain_text"], "old_set": before.get("set", {}), "old_enabled": was_on, "reverted": True,
                  "hash": policy_hash(_merged(policy, {"plain_text": policy["plain_text"], "set": values}))}
        history.append(record)
        _save(log)
    events.record("policies", "reverted", f"Put the firm policy {policy.get('name') or policy_id} back to the shipped wording", home=path().parent, who=by, role=role)
    return record


def _shown(value: Any) -> str:
    return "" if value is None else str(value)


def changes(label_for: Callable[[str], str] | None = None, policy_id: str | None = None) -> list[dict[str, Any]]:
    """Every edit to the firm's policies (one policy's, or all), newest first, as the attorney reads them: {policy, name, by, role, at,
    what (a sentence), parts: [{what, before, after, labels}]}. An answer changed the same way on several boxes is one part naming the
    boxes. The words and answers before and after are in the record itself (edit, revert)."""
    label_for = label_for or (lambda key: key)
    names = {p["id"]: p.get("name") or p["id"] for p in shipped()}
    out = []
    for pid, records in edits().items():
        if policy_id is not None and pid != policy_id:
            continue
        for record in records:
            parts, said, switch = [], [], ""
            if record.get("plain_text") != record.get("old_plain_text"):
                parts.append({"what": "Words", "before": _shown(record.get("old_plain_text")), "after": _shown(record.get("plain_text")), "labels": []})
                said.append("the words")
            moved: dict[tuple[str, str], list[str]] = {}
            for key, value in (record.get("set") or {}).items():
                old = (record.get("old_set") or {}).get(key)
                if old != value:
                    moved.setdefault((_shown(old), _shown(value)), []).append(label_for(key))
            for (old, value), labels in moved.items():
                parts.append({"what": "Answer", "before": old, "after": value, "labels": labels})
            if moved:
                said.append("an answer" if sum(len(v) for v in moved.values()) == 1 else "the answers")
            if record.get("enabled", True) != record.get("old_enabled", True):
                on = record.get("enabled", True)
                parts.append({"what": "Applies", "before": "Switched off" if on else "Applies", "after": "Applies" if on else "Switched off", "labels": []})
                switch = "switched on" if on else "switched off"
            words = ("changed " + " and ".join(said)) if said else ""
            what = "Went back to the shipped wording" if record.get("reverted") else ", ".join(x for x in (words, switch) if x).capitalize()
            out.append({"policy": pid, "name": names.get(pid, pid), "by": record.get("by"), "role": record.get("role"), "at": record.get("at"), "what": what, "parts": parts})
    # newest first; two edits in the same second keep the later one first (the list is oldest first within a policy)
    return [r for _, r in sorted(enumerate(out), key=lambda nr: (clock.key(nr[1]["at"]), nr[0]), reverse=True)]


def listing(policies: list[dict[str, Any]] | None = None, input_for: Callable[[str], dict[str, Any]] | None = None,
            label_for: Callable[[str], str] | None = None) -> list[dict[str, Any]]:
    """Every shipped policy as Settings shows it: name, plain text (the firm's, with the shipped one beside it when changed), each answer it sets with
    the values its box can take, whether it applies, and who last edited it. The fact key is the handle the edit comes back with; the screen shows the label."""
    log = edits()
    out = []
    for policy in policies if policies is not None else shipped():
        mine = effective(policy, log)
        record = latest(policy["id"], log)
        out.append({"id": policy["id"], "name": policy.get("name") or policy["id"], "plain_text": mine.get("plain_text") or policy.get("why", ""),
                    "shipped_text": policy.get("plain_text") or policy.get("why", ""), "source": policy.get("source", ""), "enabled": enabled(policy["id"], log),
                    "answers": [{"key": key, "label": (label_for or (lambda k: k))(key), "value": value, "shipped": policy["set"][key],
                                 "input": (input_for or (lambda k: {"type": "text"}))(key)} for key, value in mine.get("set", {}).items()],
                    "edit": note(policy, log), "history": len((log.get(policy["id"]) or [])), "edited_at": (record or {}).get("at"), "edited_by": (record or {}).get("by")})
    return out
