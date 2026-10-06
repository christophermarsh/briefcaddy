"""The first call: a prospect is a person who has called and is not a client yet (data/prospects/<id>/).

A prospect is kept apart from the clients on purpose. The record is in a folder of its own beside the case folders, so no list of cases, no count of clients, no
report and no table of the query layer ever holds one: All clients, Reports and the counts are the firm's clients, and a prospect is not one. The prospects have their own
list (Prospects, on All clients), with a stage, a page at a time and a CSV file.

    data/prospects/<id>/prospect.json    the call: who, how to reach them, language, office, how they heard, the date, who took the call, the kind of case if known
                                         (one of the front desk's tracks: a protected kind restricts the prospect from the first minute, src/restricted.py protect_new),
                                         and where it stands (the questions sent, waiting for the attorney, became a client, declined) with who and when
    data/prospects/<id>/answers.json     the answers to the first-contact questions: a copy of what the portal holds (sync), kept with the prospect
    data/prospects/<id>/notes.json       free-form notes, never edited (src/case_notes.py)
    data/prospects/<id>/deadlines_set.json   tasks, never deleted (src/case_notes.py, src/deadlines_set.py)
    data/prospects/<id>/apply_for.json   what could this person apply for: the questions and the answers (src/apply_for.py)
    data/prospects/<id>/access.json      the restriction (src/restricted.py): the same record and the same gate as a case's
    data/prospects/<id>/engagement.json, engagement/*.pdf   the non-engagement letter when the firm declines (src/engagement.py)
    data/portal/prospects/clients/<id>/  what the portal needs to ask the questions: the same store, sign-in links and sessions as a client's, in a folder of its own, so a
                                         prospect is never among the portal's clients

The stages: new (called, nothing asked yet), answering (the questions were sent, or some answers are in), waiting for the attorney (the prospect sent the
questions, or someone marked it ready), became a client, declined. Becoming a client is Add a client, filled in from the call: the conflict search (src/conflicts.py) runs there,
first, as for any client; the answers are carried over as the client's portal answers where the client's questionnaire asks the same question; the notes, the tasks, the
answers to what-could-apply-for and a restriction go onto the case. Declining is the attorney's, with the non-engagement letter (src/engagement.py): the first-contact
link stops working that day.

Every change is one row of the event ledger (kind "prospects", the row's case reads "prospect:<id>"), who and when, never a name, a phone number or an answer. Nothing
here is deleted: a prospect who never became a client stays as it was (the firm's own rule for how long, docs/attorney_review.md).
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

import clock
import events
import restricted
from portal.store import PortalStore

FILE = "prospect.json"
ANSWERS = "answers.json"
FOLDER = "prospects"
PREFIX = "prospect-"  # every prospect's id starts with it (new_id); the ledger names a prospect "prospect:<id>"
VERSION = 1
CHANNELS = ("email", "sms", "whatsapp")
STAGES = {"new": "New", "answering": "Answering the questions", "waiting": "Waiting for the attorney", "client": "Became a client", "declined": "Declined"}
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]{2,}")
MAX_TEXT = 200
PER_PAGE = 50
# what a code from the portal's own checks (portal/bank.py clean) says, in words for staff
ERRORS = {"invalid_date": "that is not a date", "invalid_choice": "that is not one of the choices", "invalid_number": "that is not a number",
          "invalid_email": "that email address does not look right", "invalid_phone": "that phone number does not look right (it needs the area code)",
          "invalid": "that answer does not look right"}
WAITING_WORDS = "Marked ready for the attorney"


class ProspectStore(PortalStore):
    """The portal's records for people who are not clients yet (data/portal/prospects/clients/<id>/): the same store, the same sign-in links and sessions as a client's, in a
    folder of its own. Its ledger rows name the prospect ("prospect:<id>"), are of kind "prospects", and go to the firm's ledger beside data/."""

    _LEDGER = PortalStore._LEDGER | {"client_added": ("added", "Put the prospect in the portal so they can answer the first questions", False),
                                     "invited": ("invited", "Sent the prospect the link to the first questions", False),
                                     "language_changed": ("changed", "The prospect chose another language for the questions", True),
                                     "answers_saved": ("answered", "Saved answers to {n} first-contact question(s)", True)}

    def _ledger_where(self, client_id: str, kind: str) -> tuple[str, str, Path]:
        return "prospects", f"prospect:{client_id}", self.root.parent.parent


def store(portal_root: str | Path) -> ProspectStore:
    """The portal's store for prospects, in a folder of the portal's own (portal_root/prospects)."""
    return ProspectStore(Path(portal_root) / FOLDER)


def store_root(portal_root: str | Path) -> Path:
    return Path(portal_root) / FOLDER


def folder(data_root: str | Path) -> Path:
    """The prospects' folder: data/prospects, beside the case folders (I485_PROSPECTS names another)."""
    env = os.environ.get("I485_PROSPECTS")
    return Path(env) if env else Path(data_root).parent / FOLDER


def dir_of(data_root: str | Path, prospect_id: str) -> Path | None:
    """The prospect's folder by its exact name (never a path, never another folder's), else None."""
    pid = str(prospect_id or "")
    if not pid or pid in (".", "..") or "/" in pid or "\\" in pid or "\0" in pid or len(pid) > 100:
        return None
    root = folder(data_root)
    d = root / pid
    if not (d / FILE).is_file() or pid not in {p.name for p in root.iterdir()}:
        return None
    return d


def ids(data_root: str | Path) -> list[str]:
    root = folder(data_root)
    return sorted(p.name for p in root.iterdir() if (p / FILE).is_file()) if root.is_dir() else []


# -- the record --------------------------------------------------------------------------------------------------------------


def read(d: Path) -> dict[str, Any]:
    try:
        data = json.loads((Path(d) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _write(d: Path, rec: dict[str, Any]) -> None:
    rec["version"] = VERSION
    path = Path(d) / FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _ledger(d: Path, action: str, what: str, who: str, role: str | None = None) -> None:
    events.record("prospects", action, what, case=f"prospect:{d.name}", home=d.parent.parent, who=who, role=role)


def _save(d: Path, rec: dict[str, Any], action: str, what: str, who: str, role: str | None = None) -> None:
    """Writes the record with a line in its own history and one row in the ledger."""
    rec.setdefault("history", []).append({"at": clock.stamp(), "by": who, "what": what})
    _write(d, rec)
    _ledger(d, action, what, who, role)


def _need(who: str) -> str:
    who = " ".join(str(who or "").split())
    if not who:
        raise ValueError("Enter your name first: every change records who made it.")
    return who


def _text(value: Any, what: str, limit: int = MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        raise ValueError(f"{what} is too long ({limit} characters at most).")
    return text


def new_id(data_root: str | Path, name: str, portal_root: str | Path | None = None) -> str:
    """"Ana Clara Exemplo Souza" -> prospect-ana-clara-exemplo-souza; -2, -3 when taken. A prospect's id has a form of its own ("prospect-" and the name), and is refused when any
    folder or record of a client has it: a case folder, a client held before its case file (the conflict check or the restriction record alone), a client in the portal. Nothing written
    for a prospect is then ever keyed by an id a client could share. The portal's own rule for an id (letters, digits, - and _) holds."""
    from portal.store import CLIENT_ID
    if portal_root is not None or (Path(data_root).name == "clients" and Path(data_root).parent.name == "data"):
        from portal.communication_consent import Scope, gate
        from portal.contact_transitions import require_complete_enrollments
        main = Path(portal_root).absolute() if portal_root is not None else Path(data_root).absolute().parent / "portal"
        if main.name == "portal" and main.parent.name == "data":
            scope = Scope(main.parent.parent, main / "prospects", main.parent / "prospects")
            if Path(data_root).resolve() != (scope.data / "clients"):
                raise ValueError("Prospect allocation belongs to another installation.")
            with gate(scope):
                require_complete_enrollments(scope)

    from review.front_desk import _slug

    base = _slug(name)
    if not base or not CLIENT_ID.fullmatch(PREFIX + base):
        raise ValueError("Enter the person's full name.")
    base = PREFIX + base

    def taken(candidate: str) -> bool:
        if candidate in ids(data_root) or (folder(data_root) / candidate).exists() or (Path(data_root) / candidate).exists():
            return True
        return portal_root is not None and (Path(portal_root) / "clients" / candidate).exists()

    out, n = base, 2
    while taken(out):
        out, n = f"{base}-{n}", n + 1
    return out


def create(data_root: str | Path, body: dict[str, Any], by: str, role: str | None = None, portal_root: str | Path | None = None) -> dict[str, Any]:
    """One installation gate spans allocation through the first-call record."""
    from contextlib import nullcontext
    from portal.communication_consent import Scope, gate
    root = Path(data_root).absolute()
    lock = nullcontext()
    if root.name == "clients" and root.parent.name == "data":
        main = Path(portal_root).absolute() if portal_root is not None else root.parent / "portal"
        scope = Scope(root.parent.parent, main / "prospects", root.parent / "prospects")
        lock = gate(scope)
    with lock:
        if root.name == "clients" and root.parent.name == "data":
            from portal.contact_transitions import require_complete_enrollments
            require_complete_enrollments(scope)
        return _create(data_root, body, by, role, portal_root)


def _create(data_root: str | Path, body: dict[str, Any], by: str, role: str | None = None, portal_root: str | Path | None = None) -> dict[str, Any]:
    """A new prospect, from "New prospect" on All clients: name, phone or email, language, office, how they heard, the date of the call, who took it, notes, and the kind
    of case if it is known. A kind the law protects (VAWA, T, U, asylum: src/restricted.py) restricts the prospect from the start, before anything else of them exists."""
    import case_notes
    import offices
    from portal.bank import language_code, languages
    from review.front_desk import TRACKS

    by = _need(by)
    name = _text(body.get("name"), "The name", 120)
    if len(name) < 2:
        raise ValueError("Enter the person's full name.")
    from portal.contact_access import enrollment_contacts
    from portal.communication_consent import Scope
    root = Path(data_root).absolute()
    scope = None
    if root.name == "clients" and root.parent.name == "data":
        main = Path(portal_root).absolute() if portal_root is not None else root.parent / "portal"
        scope = Scope(root.parent.parent, main / "prospects", root.parent / "prospects")
    contact = enrollment_contacts(scope, body.get("email") or "", body.get("phone") or "")
    email, phone = contact["email"], contact["phone"]
    if not phone and not email:
        raise ValueError("Enter a phone number or an email address: the office has to be able to reach the person.")
    language = language_code(str(body.get("language") or "pt"))
    if language is None:
        raise ValueError(f"Choose the person's language: {', '.join(languages())}.")
    office = str(body.get("office") or "").strip()
    if office and offices.by_id(office) is None:
        raise ValueError("Choose the office from the list.")
    heard = _text(body.get("how_heard"), "How they heard about the firm")
    called_on = str(body.get("called_on") or "").strip() or clock.today().isoformat()
    try:
        day = date.fromisoformat(called_on)
    except ValueError:
        raise ValueError("Choose the day of the call from the calendar.") from None
    if day > clock.today():
        raise ValueError("The day of the call can't be in the future.")
    taken_by = _text(body.get("taken_by"), "Who took the call", 80) or by
    track = str(body.get("kind") or "").strip()
    names = dict(TRACKS)
    if track and track not in names:
        raise ValueError("Choose the kind of case from the list.")
    consent = {c: bool((body.get("consent") or {}).get(c)) for c in CHANNELS}
    for channel, address in (("email", email), ("sms", phone), ("whatsapp", phone)):
        if consent[channel] and not address:
            raise ValueError(f"The person agreed to {'emails' if channel == 'email' else 'text messages' if channel == 'sms' else 'WhatsApp messages'}, "
                             f"but there is no {'email address' if channel == 'email' else 'phone number'} to send to.")
    pid = new_id(data_root, name, portal_root)
    d = folder(data_root) / pid
    found = restricted.kind_law(track) if track else None
    if found:  # the restriction first: if writing stops part way, the folder that names the person is already closed
        restricted.protect_new(d, found, names[track], "Added as", by, ledger_case=f"prospect:{pid}")
    d.mkdir(parents=True, exist_ok=True)
    rec = {"id": pid, "name": name, "phone": phone, "email": email, "language": language, "office": office or None, "how_heard": heard or None, "called_on": day.isoformat(),
           "taken_by": taken_by, "kind": track or None, "kind_name": names.get(track), "consent": consent, "created_by": by, "created_at": clock.stamp(),
           "questions_sent": None, "waiting": None, "declined": None, "became_client": None, "portal": None, "typed": {}, "history": []}
    if office:
        (d / "office.json").write_text(json.dumps({"office": office, "by": by, "at": clock.stamp()}, indent=1), encoding="utf-8")  # offices.chosen's record
    _save(d, rec, "created", "Recorded a first call" + (", restricted from the start" if found else ""), by, role)
    if " ".join(str(body.get("note") or "").split()):
        case_notes.add_note(d, str(body.get("note")), by, role, prospect=True)
    return rec | {k: contact[k] for k in ("phone_access", "phone_note") if k in contact} | ({"restricted": True, "law": restricted.law_words(found), "note": restricted.INVITE_HELD} if found else {})


def stage(rec: dict[str, Any], portal: dict[str, Any] | None = None) -> str:
    """Where the prospect stands (STAGES): from the record, and the portal's own state of the prospect when it is on hand."""
    if rec.get("declined"):
        return "declined"
    if rec.get("became_client"):
        return "client"
    portal = portal if portal is not None else (rec.get("portal") or {})
    if rec.get("waiting") or portal.get("status") == "submitted":
        return "waiting"
    if rec.get("questions_sent") or rec.get("typed") or portal.get("status") == "started":
        return "answering"
    return "new"


def portal_state(portal_root: str | Path | None, pid: str) -> dict[str, Any]:
    """The prospect's side of the portal, as it is now: {status, submitted_at, invited_at, last_invite, language}; {} when the prospect has no place there yet."""
    if portal_root is None:
        return {}
    try:  # the file itself: a list of every prospect asks for each one's state, and must not make folders
        p = json.loads((store_root(portal_root) / "clients" / pid / "profile.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: p.get(k) for k in ("status", "submitted_at", "invited_at", "last_invite", "language")} if isinstance(p, dict) else {}


def ensure_portal(st, rec: dict[str, Any], by: str) -> dict[str, Any]:
    """The prospect's profile in the portal's store (made once): the questions to ask are first contact's (portal/bank.py)."""
    try:
        return st.profile(rec["id"])
    except LookupError:
        return st.add_client(rec["id"], rec["name"], phone=rec.get("phone") or "", email=rec.get("email") or "", language=rec.get("language") or "pt",
                      consent=rec.get("consent") or {}, by=by,
                      initial_profile={"filing": "first_contact", "prospect": True, "added_by": by, "added_at": clock.stamp(),
                                       **({"track": rec["kind"]} if rec.get("kind") else {})})


def sync(data_root: str | Path, portal_root: str | Path | None, pid: str) -> dict[str, Any]:
    """The portal's answers and state onto the prospect: answers.json (a copy kept with the prospect) and the record's portal part. Nothing when the portal is not on this
    machine or the prospect has no place there. Returns the record."""
    d = dir_of(data_root, pid)
    if d is None:
        raise LookupError("unknown client")
    rec = read(d)
    if portal_root is None:
        return rec
    state = portal_state(portal_root, pid)
    if not state:
        return rec
    changed = False
    if rec.get("portal") != state:
        rec["portal"], changed = state, True
    answers = store(portal_root).answers(pid)
    current = _answers_file(d)
    if answers != current:
        tmp = (d / ANSWERS).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(answers, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, d / ANSWERS)
    typed = {k: v for k, v in (rec.get("typed") or {}).items() if answers.get(k) == v.get("value")}  # a person typed it, and the prospect has not changed it since
    if typed != (rec.get("typed") or {}):
        rec["typed"], changed = typed, True
    if changed:
        _write(d, rec)
    return rec


def _answers_file(d: Path) -> dict[str, Any]:
    try:
        data = json.loads((Path(d) / ANSWERS).read_text(encoding="utf-8")) if (Path(d) / ANSWERS).exists() else {}
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def answers_of(data_root: str | Path, portal_root: str | Path | None, pid: str) -> dict[str, Any]:
    """What the prospect answered (the portal's answers, else the copy kept with the prospect)."""
    d = dir_of(data_root, pid)
    if d is None:
        raise LookupError("unknown client")
    if portal_root is not None and portal_state(portal_root, pid):
        return store(portal_root).answers(pid)
    return _answers_file(d)


# -- the questions: sending them, showing the link, typing the answers in ----------------------------------------------------------


def ensure_open(rec: dict[str, Any]) -> None:
    if rec.get("declined"):
        raise ValueError("The firm did not take this case: nothing is sent to the person, and no link is made. An attorney decides otherwise.")
    if rec.get("became_client"):
        raise ValueError("This person is a client now: their questions are the client's own (All clients).")


def _notifier(data_root: Path, portal_root: Path):
    from portal.notify import Notifier

    return Notifier(Path(portal_root) / "outbox.jsonl", cases_root=folder(data_root), store=store(portal_root))


def send_questions(data_root: str | Path, portal_root: str | Path, pid: str, by: str, role: str | None = None, again: bool = False) -> dict[str, Any]:
    """The portal link to the first questions, by every channel the person agreed to (the same send as a client's invitation: the message carries the firm's name and the
    link, nothing else). A restricted prospect gets nothing by itself: the office hands the link over (show_link). Returns {sent, delivery}."""
    from portal.admin import send
    from portal.notify import delivery

    by = _need(by)
    d = dir_of(data_root, pid)
    if d is None:
        raise LookupError("unknown client")
    rec = read(d)
    ensure_open(rec)
    st = store(portal_root)
    ensure_portal(st, rec, by)
    sent = send(st, _notifier(Path(data_root), Path(portal_root)), pid, "invite", by=by, again=again)
    result = delivery(sent)
    attempt = {"by": by, "at": clock.stamp(), "result": result}
    rec["questions_attempt"] = attempt
    st.update_profile(pid, last_invite_attempt=attempt)
    # Provider acceptance, not an outbox/dry run or aggregate display label,
    # establishes a successful invitation. A later hold preserves that history.
    accepted = any(row.get("result") == "sent" for row in sent)
    if accepted:
        st.update_profile(pid, last_invite_at=attempt["at"], last_invite_by=by, last_invite=result)
        st.log(pid, "invited", {"by": by})
        rec["questions_sent"] = attempt
    rec["portal"] = portal_state(portal_root, pid)
    _save(d, rec, "questions_sent" if accepted else "questions_attempt",
          "Sent the prospect the link to the first questions" if accepted else "Attempted the first-question invitation: " + result["text"], by, role)
    return {"sent": sent, "delivery": result}


def show_link(data_root: str | Path, portal_root: str | Path, pid: str, by: str, base_url: str, role: str | None = None, *, actor_email: str | None = None) -> dict[str, Any]:
    """An attorney hands over 72-hour consent-only access; it is neither general portal access nor proof of contact control."""
    from portal.store import LINK_TTL

    by = _need(by)
    if role == "paralegal":
        raise PermissionError("Showing a sign-in link is the attorney's. Send the link instead: it goes by the channels the person agreed to.")
    d = dir_of(data_root, pid)
    if d is None:
        raise LookupError("unknown client")
    rec = read(d)
    ensure_open(rec)
    st = store(portal_root)
    ensure_portal(st, rec, by)
    from portal.communication_consent import manual_bootstrap
    if not actor_email:
        raise PermissionError("A signed-in staff principal is required for assisted consent access.")
    token = manual_bootstrap(st.communication_scope(), st, pid, actor_email=actor_email)
    st.log(pid, "link_shown", {"by": by})
    _ledger(d, "link_shown", "Showed assisted consent-only signoff access on the screen", by, role)
    return {"url": f"{base_url.rstrip('/')}/l/{token}", "hours": int(LINK_TTL.total_seconds() // 3600)}


def save_answers(data_root: str | Path, portal_root: str | Path, pid: str, answers: dict[str, Any], by: str, role: str | None = None) -> dict[str, Any]:
    """The office types in what the person said on the phone. Each answer is checked as the portal checks it (portal/bank.py clean), kept in the same place the portal keeps the
    prospect's own, and marked as typed by this person, with the time. An empty value takes an answer out. Returns {saved: [question ids]}."""
    from portal.bank import all_questions, bank_for, clean

    by = _need(by)
    d = dir_of(data_root, pid)
    if d is None:
        raise LookupError("unknown client")
    rec = read(d)
    ensure_open(rec)
    st = store(portal_root)
    profile = ensure_portal(st, rec, by)
    questions = all_questions(bank_for(profile))
    changes: dict[str, Any] = {}
    for qid, value in (answers or {}).items():
        if qid not in questions:
            continue
        cleaned, error = clean(questions[qid], value)
        if error:
            raise ValueError(f"Check the answer to “{questions[qid]['label']['en']}”: {ERRORS.get(error, ERRORS['invalid'])}.")
        changes[qid] = cleaned
    if not changes:
        raise ValueError("Type at least one answer first.")
    st.save_answers(pid, changes)
    if st.profile(pid).get("status") == "invited":
        st.update_profile(pid, status="started")
    typed = dict(rec.get("typed") or {})
    for qid, value in changes.items():
        if value is None:
            typed.pop(qid, None)
        else:
            typed[qid] = {"by": by, "at": clock.stamp(), "value": value}
    rec["typed"] = typed
    rec["portal"] = portal_state(portal_root, pid)
    _save(d, rec, "answers_typed", f"Typed in {len(changes)} first-contact answer(s) from the call", by, role)
    sync(data_root, portal_root, pid)
    return {"saved": sorted(changes)}


# -- the stage changes -------------------------------------------------------------------------------------------------------


def mark_waiting(data_root: str | Path, pid: str, by: str, role: str | None = None) -> dict[str, Any]:
    """Anyone on staff marks the prospect ready for the attorney's decision (the prospect's own "send" does it too)."""
    by = _need(by)
    d = dir_of(data_root, pid)
    if d is None:
        raise LookupError("unknown client")
    rec = read(d)
    ensure_open(rec)
    if not rec.get("waiting"):
        rec["waiting"] = {"by": by, "at": clock.stamp()}
        _save(d, rec, "waiting", WAITING_WORDS, by, role)
    return rec


def decline(data_root: str | Path, portal_root: str | Path, pid: str, by: str, role: str | None, reason: str, additions: str = "") -> dict[str, Any]:
    """The attorney declines the prospect: the non-engagement letter (src/engagement.py, from the firm's own wording once the attorney approved it), the reason kept for
    the firm, the date and who; the first-contact link stops working today."""
    import engagement

    by = _need(by)
    d = dir_of(data_root, pid)
    if d is None:
        raise LookupError("unknown client")
    rec = read(d)
    ensure_open(rec)
    st = store(portal_root)
    ensure_portal(st, rec, by)  # the letter is written to the person by name, in their language (it reads them from the portal's profile)
    engagement.end(d, "declined", by, role, reason=reason, additions=additions, portal_root=store_root(portal_root), store=st)
    end = (engagement.read(d).get("end") or {})
    rec = read(d)
    rec["declined"] = {"by": by, "at": clock.stamp(), "on": end.get("on") or clock.today().isoformat(), "letter": end.get("letter")}
    _save(d, rec, "declined", "Declined the prospect: the firm did not take the case, and the non-engagement letter was made", by, role)
    return rec


def became_client(data_root: str | Path, portal_root: str | Path, pid: str, client_id: str, client_store, by: str, role: str | None = None, *, actor_email: str | None = None) -> dict[str, Any]:
    """Explicit current staff promotion; display name/role cannot authorize it."""
    from portal.promotion import promote
    scope = client_store.communication_scope()
    if Path(data_root).resolve() != scope.cases or Path(portal_root).resolve() != scope.portal:
        raise ValueError("Promotion belongs to another installation.")
    if not actor_email:
        raise PermissionError("Promotion requires the current signed-in staff account.")
    return promote(scope, client_store, client_id, pid, actor_email=actor_email)


def _carry_into_client(data_root: str | Path, portal_root: str | Path, pid: str, client_id: str, client_store, by: str, role: str | None = None) -> dict[str, Any]:
    """The prospect became a client (Add a client made client_id): the answers go to the client's portal answers where the client's questionnaire asks the same question, the
    notes, the tasks, the answers to what-could-apply-for and the restriction go onto the case, and the prospect's own link stops. Returns what was carried."""
    import apply_for
    import case_notes
    from portal.bank import all_questions, bank_for, clean

    by = _need(by)
    d = dir_of(data_root, pid)
    if d is None:
        raise LookupError("unknown client")
    read(d)  # Validate the retained source record before carrying anything.
    case =Path(data_root) / client_id
    case.mkdir(parents=True, exist_ok=True)
    # The promotion coordinator checks current confidentiality before every
    # copy. Carrying data must never restore a staff member removed from either
    # side's ACL. New-target restriction publication is a separate owned effect.
    closed = restricted.is_restricted(case)
    sync(data_root, portal_root, pid)
    given = answers_of(data_root, portal_root, pid)
    questions = all_questions(bank_for(client_store.profile(client_id)))
    have = client_store.answers(client_id)
    carried = {}
    for k, v in given.items():  # only an answer the client's own questionnaire accepts for that question
        if k in questions and k not in have and v not in (None, "", [], {}):
            value, error = clean(questions[k], v)
            if value is not None and not error:
                carried[k] = value
    if carried:
        client_store.save_answers(client_id, carried)
    client_store.update_profile(client_id, prospect=pid)
    moved = case_notes.carry(d, case, pid)
    answered = apply_for.carry(d, case)
    return {"answers": len(carried), "notes": moved["notes"], "tasks": moved["tasks"], "apply_for": answered, "restricted": closed}


# -- the page, the list, the lists of work ------------------------------------------------------------------------------------------


def _row(d: Path, rec: dict[str, Any], portal: dict[str, Any]) -> dict[str, Any]:
    from portal.bank import language_names

    s = stage(rec, portal)
    return {"id": rec.get("id") or d.name, "name": rec.get("name"), "stage": s, "stage_name": STAGES[s], "called_on": rec.get("called_on"), "taken_by": rec.get("taken_by"),
            "office": rec.get("office"), "kind_name": rec.get("kind_name"), "language": rec.get("language"), "language_name": language_names().get(rec.get("language"), rec.get("language")),
            "phone": rec.get("phone"), "email": rec.get("email"), "how_heard": rec.get("how_heard"), "became_client": (rec.get("became_client") or {}).get("id"),
            "declined_on": (rec.get("declined") or {}).get("on")}


def listing(data_root: str | Path, portal_root: str | Path | None, visible, q: str = "", stage_filter: str = "", page: int = 1) -> dict[str, Any]:
    """The Prospects list: the ones this person may open (visible(folder): the same gate as a case's), newest call first, a page at a time. counts: by stage, over everyone
    they may see (the stage filter does not change them)."""
    needle = " ".join(str(q or "").lower().split())
    rows, counts = [], {k: 0 for k in STAGES}
    for pid in ids(data_root):
        d = folder(data_root) / pid
        if not visible(d):
            continue
        rec = read(d)
        row = _row(d, rec, portal_state(portal_root, pid) or rec.get("portal") or {})
        counts[row["stage"]] += 1
        if stage_filter and row["stage"] != stage_filter:
            continue
        if needle and needle not in " ".join(str(row.get(k) or "") for k in ("id", "name", "phone", "email", "taken_by", "how_heard")).lower():
            continue
        rows.append(row)
    rows.sort(key=lambda r: (r["called_on"] or "", r["id"]), reverse=True)
    pages = max(1, -(-len(rows) // PER_PAGE))
    page = min(max(1, int(page or 1)), pages)
    return {"rows": rows[(page - 1) * PER_PAGE: page * PER_PAGE], "total": len(rows), "page": page, "pages": pages, "per_page": PER_PAGE,
            "stages": [{"id": k, "name": v, "count": counts[k]} for k, v in STAGES.items()], "counts": counts}


def csv_text(data_root: str | Path, portal_root: str | Path | None, visible, q: str = "", stage_filter: str = "") -> bytes:
    """All the rows of the list (not a page) as a CSV file."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Name", "Phone", "Email", "Language", "Office", "How they heard", "Day of the call", "Who took the call", "Kind of case", "Stage", "Became a client as", "Declined on"])
    page = listing(data_root, portal_root, visible, q, stage_filter, 1)
    n = 1
    while True:
        for r in page["rows"]:
            w.writerow([_safe(x) for x in (r["name"], r["phone"], r["email"], r["language_name"], r["office"], r["how_heard"], _us(r["called_on"]), r["taken_by"], r["kind_name"],
                                           r["stage_name"], r["became_client"], _us(r["declined_on"]))])
        if n >= page["pages"]:
            break
        n += 1
        page = listing(data_root, portal_root, visible, q, stage_filter, n)
    return out.getvalue().encode("utf-8-sig")


def _safe(value: Any) -> str:
    """A cell a spreadsheet will not run as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _us(day: Any) -> str:
    return f"{str(day)[5:7]}/{str(day)[8:10]}/{str(day)[:4]}" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day or "")) else ""


def view(data_root: str | Path, portal_root: str | Path | None, pid: str) -> dict[str, Any]:
    """The prospect's page: the call, the stage, the questions sent, the answers (each with who gave it), what was done to the prospect. Brings the portal's side up to date first."""
    from portal.bank import bank_for, localized

    d = dir_of(data_root, pid)
    if d is None:
        raise LookupError("unknown client")
    rec = sync(data_root, portal_root, pid)
    portal = portal_state(portal_root, pid) or rec.get("portal") or {}
    answers = answers_of(data_root, portal_root, pid)
    bank = bank_for({"filing": "first_contact"})
    typed = rec.get("typed") or {}
    sections, total, done = [], 0, 0
    for sec in localized(bank, "en", answers, help={"sections": {}, "questions": {}, "faq": []}):
        qs = []
        for x in sec["questions"]:
            value = answers.get(x["id"])
            total, done = total + 1, done + (1 if value not in (None, "", [], {}) else 0)
            who = typed.get(x["id"])
            qs.append({k: x[k] for k in ("id", "label", "type") if k in x} | {"options": x.get("options") or [], "required": x.get("required", True), "help": x.get("help"),
                                                                              "answer": value, "shown": _shown(x, value),
                                                                              "by": (who or {}).get("by") if value not in (None, "", [], {}) else None,
                                                                              "at": (who or {}).get("at"),
                                                                              "who": "typed in by " + who["by"] if who else "the prospect" if value not in (None, "", [], {}) else None})
        sections.append({"id": sec["id"], "title": sec["title"], "questions": qs})
    row = _row(d, rec, portal)
    state = restricted.state(d)
    out = row | {"phone": rec.get("phone"), "email": rec.get("email"), "kind": rec.get("kind"), "consent": rec.get("consent"), "created_by": rec.get("created_by"),
                 "created_at": rec.get("created_at"), "restricted": state["restricted"], "law_phrase": state.get("law_phrase"),
                 "questions_sent": rec.get("questions_sent"), "questions_attempt": rec.get("questions_attempt"), "portal_status": portal.get("status"), "submitted_at": portal.get("submitted_at"), "waiting": rec.get("waiting"),
                 "declined": rec.get("declined"), "became": rec.get("became_client"), "history": list(reversed(rec.get("history") or []))[:40],
                 "sections": sections, "answered": done, "total": total,
                 "held_note": restricted.INVITE_HELD if state["restricted"] and not (state.get("automatic_messages")) else None}
    return out


def _shown(question: dict[str, Any], value: Any) -> str:
    """An answer in words for the page."""
    if value in (None, "", [], {}):
        return ""
    if question.get("options"):
        return next((o["label"] for o in question["options"] if o["value"] == value), str(value))
    return str(value)


def prefill(data_root: str | Path, portal_root: str | Path | None, pid: str) -> dict[str, Any]:
    """What Add a client is filled in with for this prospect: the name, phone, email, language, office, the kind of case, and the date of birth from the answers (the conflict
    search reads it), and the prospect's id (the server finishes the prospect's side once the client is added)."""
    d = dir_of(data_root, pid)
    if d is None:
        raise LookupError("unknown client")
    rec = read(d)
    ensure_open(rec)
    answers = answers_of(data_root, portal_root, pid)
    return {"prospect": pid, "name": rec.get("name"), "phone": rec.get("phone"), "email": rec.get("email"), "language": rec.get("language"), "office": rec.get("office") or "",
            "track": rec.get("kind") or "", "dob": answers.get("dob") or "", "consent": rec.get("consent") or {}}


def first_call(data_root: str | Path, portal_root: str | Path | None, client_id: str, visible=None) -> dict[str, Any] | None:
    """For a client's case page: the first call it began with (when the client was a prospect): the day, who took it, how they heard, what they asked about and the answers given
    on the call or in the first questions, each with who gave it. None for a client who never was a prospect, or whose prospect this person may not open."""
    from portal.store import CLIENT_ID

    if portal_root is None or not CLIENT_ID.fullmatch(str(client_id or "")):
        return None
    try:
        profile = json.loads((Path(portal_root) / "clients" / client_id / "profile.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = profile.get("prospect") if isinstance(profile, dict) else None
    root = Path(data_root).resolve()
    if pid and root == root.parent / "clients" and root.parent.name.casefold() == "data":
        from portal.communication_consent import Scope
        from portal.contact_transitions import _composition
        from portal.promotion import associated_prospects
        scope = Scope(root.parent.parent, Path(portal_root), root)
        _, current = _composition(scope, "client", readonly=True)
        if pid not in associated_prospects(scope, current, client_id, [pid]):
            return None
    d = dir_of(data_root, pid) if pid else None
    if d is None or (visible is not None and not visible(d)):
        return None
    shown = view(data_root, portal_root, d.name)
    return {"id": d.name, "called_on": shown["called_on"], "taken_by": shown["taken_by"], "how_heard": shown["how_heard"], "became": shown.get("became"),
            "answers": [{"label": q["label"], "shown": q["shown"], "who": q["who"]} for s in shown["sections"] for q in s["questions"] if q["answer"] not in (None, "", [], {})]}


def work_rows(data_root: str | Path, today: date, cfg: dict[str, Any], visible) -> list[dict[str, Any]]:
    """The open prospects' tasks as rows the lists of deadlines read (a row has an id, a summary name and journey.deadlines, as an overview row of a case does, and "prospect": its
    id): so My work, What's due, the month view and the calendar feed show a prospect's tasks beside the cases' deadlines, and nothing else of a prospect. A prospect that became a
    client or was declined has left the lists (its tasks went onto the case, or the matter ended)."""
    import case_notes
    import deadlines_set

    rows = []
    for pid in ids(data_root):
        d = folder(data_root) / pid
        rec = read(d)
        if rec.get("declined") or rec.get("became_client") or not visible(d) or not (d / deadlines_set.FILE).exists():
            continue
        row = case_notes.task_row(f"prospect:{pid}", rec.get("name"), d, today, cfg)
        if row:
            rows.append(row | {"prospect": pid})
    return rows


def waiting_rows(data_root: str | Path, portal_root: str | Path | None, visible) -> list[dict[str, Any]]:
    """The prospects waiting for the attorney, oldest first: for the attorney's My work."""
    out = []
    for pid in ids(data_root):
        d = folder(data_root) / pid
        if not visible(d):
            continue
        rec = read(d)
        portal = portal_state(portal_root, pid) or rec.get("portal") or {}
        if stage(rec, portal) == "waiting":
            since = (rec.get("waiting") or {}).get("at") or portal.get("submitted_at") or rec.get("created_at")
            out.append({"prospect": pid, "name": rec.get("name"), "since": since, "called_on": rec.get("called_on")})
    return sorted(out, key=lambda r: clock.key(r["since"]))
