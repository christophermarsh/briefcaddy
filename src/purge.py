"""A case purged at the end of its keeping period, under the attorney's control, and proven gone (brief Q1).

CURRENT CASE AUTHORITY. Operational closure and legacy office periods are planning proposals, not legal termination or authority to destroy.
The attorney must separately select and approve the applicable jurisdiction policy, determine actual representation completion/minor protection,
approve an inclusive keeping date, and approve destruction after that date with current protections resolved (client_file_policy.py).
The policy's case incarnation, facts, source version and canonical case-evidence hashes must still match at ask, confirmation, submission and worker.
Changed holds or evidence require renewed review; a typed reason cannot override them. Protective handover is independent of retention eligibility.
An interrupted physical purge whose own identity/policy evidence was removed remains an incident hold for operator review; absence never authorizes
resume or deletion of a recreated same-ID case. Original central operation history remains on file.

TWO STEPS BEFORE THE WAIT, FOR EVERY OFFICE (the case's purge.json): (a) a recorded attempt to reach the client about their file (how, the
date, a note), and (b) a review of the originals the office holds for this client: every document of the case is listed and the attorney says,
for each, that it is no original the office holds, that the original was returned, or that it is kept and where (or that the office holds no
originals at all). What is kept becomes the originals index the firm keeps after the purge (data/originals_kept.json: the case id, the
client's name, the kind of document and where it is stored, nothing else). Neither step can be skipped; each is a ledger row.

THE PURGE, IN STEPS A PERSON TAKES. An attorney asks only with current explicit case destruction authority; the product shows every
store that holds the case and what the ledger keeps; the purge waits the days the attorney set (Settings; never fewer than 7), during which
any attorney can cancel and the case shows "to be purged on MM/DD/YYYY" to attorneys; when the firm has more than one attorney account a
second attorney confirms. When the day comes the purge runs as a job (src/jobs.py) with a ledger row before and after (run()). A paralegal
can never start, confirm or see a purge.

WHAT IS LEFT. The firm's record of the purge (data/purges.json and the ledger's "purge" rows): the case id, the kind of case, who asked, who
confirmed, when, and the reason (with any name or number of the case masked out of it). The originals index. Nothing else of the case
anywhere the product writes: every store in src/records.py that can hold a case is emptied of it (STORES; empty_stores() does it, and says
what it did per store), the view log and the access log lose the case's rows, the event ledger's rows of the case are blanked in place (their time
and their place in the ledger's chain stay, so src/ledger_seal.py's links and daily seals still hold, and ledger_redactions.jsonl accounts for them), the SQLite copies (index.db, query.db,
find.db, learning.db) lose its rows and are compacted so no deleted page keeps a byte of it. Older backups and the firm's own exports are
the firm's to delete (the screen names those that may hold the case); Clio's own record is the firm's to delete in Clio.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import clock
import events

RETENTION_FILE = "retention.json"  # the offices' confirmed rules and the waiting period (beside the case folders)
PURGES_FILE = "purges.json"  # every purge asked for, cancelled or done: the firm's record (no name, no number)
ORIGINALS_FILE = "originals_kept.json"  # the originals the office keeps after a purge
CASE_FILE = "purge.json"  # in the case folder: the two steps before the wait (gone with the case)
WAIT_MIN, WAIT_DEFAULT = 7, 14  # days between asking and the purge
MAJORITY = 18
VERSION = 1
CONTACT_HOW = {"phone": "Called the client", "letter": "Wrote to the client by mail", "email": "Wrote to the client by e-mail",
               "portal": "Sent the client a message through the portal", "in_person": "Spoke to the client in person"}
ORIGINAL_CHOICES = {"none": "Not an original the office holds", "returned": "Original returned to the client", "kept": "Original kept by the office"}
_LOCK = threading.RLock()

# What the product proposes per state, from summaries only (docs/research/ai_ethics_sources_read.md, read 10/05/2026). Never shown as a rule:
# always "proposed, to be confirmed by the attorney against the rule's own text".
PROPOSALS: dict[str, dict[str, Any]] = {
    "MA": {"years": 6, "from_majority": True,
           "words": "Proposed from a summary of Massachusetts Rule 1.15A read on 10/05/2026 (the official text not yet read): six years after the "
                    "representation ends, or six years after a minor client reaches majority, whichever is later. To be confirmed by the attorney "
                    "against the rule's own text.",
           "screen": "For a Massachusetts office the figure is the attorney's reading of Rule 1.15A."},
    "FL": {"years": 6, "from_majority": False,
           "words": "Proposed from summaries read on 10/05/2026 (the official texts not yet read): the Florida Bar sets no single period for keeping a "
                    "client's file, so six years is offered as the firm's own policy figure; Ethics Opinion 81-8 asks for a diligent attempt to "
                    "contact the client before destruction and a review for original client property. To be confirmed by the attorney.",
           "screen": "For a Florida office the Bar sets no fixed period: the figure is the firm's own policy."},
}


class PurgeError(ValueError):
    """Something the attorney must do first, said in words."""


# -- files ---------------------------------------------------------------------------------------------------------------------------------


def _read(path: Path, default: Any) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=1, ensure_ascii=False))
    os.replace(tmp, path)


def _home(data_root: Path) -> Path:
    return Path(data_root).resolve().parent


def _attorney(role: str | None, what: str) -> None:
    if role not in ("attorney", None):
        raise PermissionError(f"{what} is an attorney's to do.")


def _need(who: str) -> str:
    who = " ".join(str(who or "").split())
    if not who:
        raise PurgeError("Enter your name first: every step of a purge records who took it.")
    return who


def _us(value: Any) -> str:
    return clock.us_date(value) if value else ""


def _day(value: Any, what: str) -> date:
    from name_match import parse_date

    d = parse_date(value)
    if d is None:
        raise PurgeError(f"{what}: a date like 10/05/2026 is needed.")
    return d


# -- the offices' rules ---------------------------------------------------------------------------------------------------------------------


def _rules_file(data_root: Path) -> dict[str, Any]:
    return {"version": VERSION, "offices": {}, "wait_days": None, "history": []} | _read(_home(data_root) / RETENTION_FILE, {})


def wait_days(data_root: Path) -> int:
    try:
        return max(WAIT_MIN, int(_rules_file(data_root).get("wait_days") or WAIT_DEFAULT))
    except (TypeError, ValueError):
        return WAIT_DEFAULT


def _office_years(office: dict[str, Any]) -> int | None:
    try:
        n = int(str((office.get("values") or {}).get("office.retention_years") or "").strip())
        return n if n > 0 else None
    except ValueError:
        return None


def _state(office: dict[str, Any]) -> str:
    return str((office.get("values") or {}).get("firm.state") or (office.get("states") or [""])[0] or "").upper()


def rule_for(data_root: Path, office: dict[str, Any]) -> dict[str, Any]:
    """The office's rule as it stands: {office, name, state, proposal (or None), confirmed (the attorney's record, or None), valid (confirmed and the
    office's years unchanged since), years, from_majority, words, screen}."""
    rec = (_rules_file(data_root)["offices"] or {}).get(office["id"])
    proposal = PROPOSALS.get(_state(office))
    current = _office_years(office)
    valid = bool(rec) and rec.get("years") == current
    if valid:
        words = (f"Planning proposal only: the office period entered on {_us(rec['at'])}: {rec['years']} years after the case ends"
                 + (", or after the client turns 18 when that is later." if rec.get("from_majority") else "."))
    elif rec:
        words = (f"The office's years were changed after the attorney confirmed the rule on {_us(rec['at'])} (now {current or 'none'}, then "
                 f"{rec['years']}): the attorney confirms it again before anything is due.")
    elif proposal:
        words = "Proposed, not confirmed. " + proposal["words"]
    else:
        words = "No rule yet: the attorney enters the years this office keeps a closed file and confirms the rule it rests on."
    return {"office": office["id"], "name": office["name"], "state": _state(office), "proposal": proposal, "confirmed": rec, "valid": valid,
            "years": rec["years"] if valid else (current or (proposal or {}).get("years")),
            "from_majority": bool(rec.get("from_majority")) if valid else bool((proposal or {}).get("from_majority")),
            "words": words + " Operational closure and this office period do not authorize legal termination or destruction.", "screen": (proposal or {}).get("screen") or "The figure is the firm's own policy, as the attorney set it."}


def rules(data_root: Path) -> dict[str, Any]:
    """Settings, Keeping closed files: every office's rule and the waiting period."""
    import offices

    data = _rules_file(data_root)
    return {"offices": [rule_for(data_root, o) for o in offices.offices()], "wait_days": wait_days(data_root), "wait_min": WAIT_MIN,
            "wait_set": {"by": data.get("wait_by"), "at": data.get("wait_at")} if data.get("wait_by") else None}


def confirm_rule(data_root: Path, office_id: str, years: Any, from_majority: bool, who: str, role: str | None) -> dict[str, Any]:
    """The attorney confirms the office's rule: the years (also saved as the office's own years, so the letters and Keeping current agree) and
    whether the clock counts from the 18th birthday when that is later."""
    import offices
    import settings

    _attorney(role, "Confirming an office's retention rule")
    who = _need(who)
    office = offices.by_id(office_id)
    if office is None:
        raise LookupError("That office is not in Settings.")
    try:
        years = int(str(years).strip())
    except ValueError:
        raise PurgeError("The years: a whole number, like 6.") from None
    if not 1 <= years <= 100:
        raise PurgeError("The years: between 1 and 100.")
    with _LOCK:
        if _office_years(office) != years:
            settings.save(office["section"], {"office.retention_years": str(years)}, who)
        data = _rules_file(data_root)
        before = data["offices"].get(office_id)
        data["offices"][office_id] = {"years": years, "from_majority": bool(from_majority), "state": _state(office), "by": who, "role": role, "at": clock.stamp()}
        data["history"] = (data.get("history") or []) + [{"office": office_id, "before": before, "by": who, "at": clock.stamp()}]
        _write(_home(data_root) / RETENTION_FILE, data)
    events.record("settings", "changed", f"Confirmed the retention rule of {office['name']}: {years} years" + (", from majority when later" if from_majority else ""),
                  home=_home(data_root), who=who, role=role)
    return rules(data_root)


def set_wait(data_root: Path, days: Any, who: str, role: str | None) -> dict[str, Any]:
    """The days between asking for a purge and the purge (never fewer than 7)."""
    _attorney(role, "Setting the waiting period before a purge")
    who = _need(who)
    try:
        days = int(str(days).strip())
    except ValueError:
        raise PurgeError("The waiting period: a whole number of days.") from None
    if days < WAIT_MIN:
        raise PurgeError(f"The waiting period is at least {WAIT_MIN} days.")
    with _LOCK:
        data = _rules_file(data_root)
        data.update(wait_days=days, wait_by=who, wait_at=clock.stamp())
        _write(_home(data_root) / RETENTION_FILE, data)
    events.record("settings", "changed", f"Set the waiting period before a purge: {days} days", home=_home(data_root), who=who, role=role)
    return rules(data_root)


# -- the clock -----------------------------------------------------------------------------------------------------------------------------


def birth_date(client_dir: Path) -> date | None:
    from name_match import parse_date

    graph = _read(Path(client_dir) / "fact_graph.json", {})
    f = ((graph.get("facts") or {}).get("applicant.date_of_birth") or {}) if isinstance(graph.get("facts"), dict) else {}
    return parse_date(f.get("value")) if f.get("status") == "resolved" else None


def _plus_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


def retention_clock(client_dir: Path) -> dict[str, Any]:
    """The case's clock: {state ("open", "proposed", "no_birth_date", "running", "due"), ended_on, starts, until, words, rule}. Nothing is due
    unless the office's rule is confirmed and unchanged."""
    import engagement
    import offices

    client_dir = Path(client_dir)
    end = (engagement.read(client_dir) if (client_dir / engagement.FILE).exists() else {}).get("end") or {}
    rule = rule_for(client_dir.parent, offices.for_case(client_dir))
    base = {"rule": rule, "ended_on": end.get("on"), "starts": None, "until": None}
    if end.get("state") not in engagement.ENDED:
        return base | {"state": "open", "words": "The case has not ended (closed, withdrawn, declined or transferred): no retention clock runs."}
    if not rule["valid"]:
        return base | {"state": "proposed", "words": rule["words"] + " Nothing is due until the attorney confirms it (Settings, Keeping closed files)."}
    ended = date.fromisoformat(end["on"])
    starts = ended
    if rule["from_majority"]:
        born = birth_date(client_dir)
        if born is None:
            return base | {"state": "no_birth_date", "words": "The office's rule counts from the client's 18th birthday when that is later, and the case "
                                                             "holds no date of birth: the clock cannot run. Review the actual completion/minor facts; no reason bypasses current case destruction authority."}
        starts = max(ended, _plus_years(born, MAJORITY))
    until = _plus_years(starts, rule["years"])
    due = until <= clock.today()
    words = (f"{'Past its keeping date' if due else 'Kept until'} {_us(until.isoformat())} under {rule['words'][0].lower() + rule['words'][1:]}"
             + (f" Counted from {_us(starts.isoformat())}, the client's 18th birthday." if starts != ended else f" Counted from {_us(ended.isoformat())}, the day the case ended."))
    return base | {"state": "due" if due else "running", "starts": starts.isoformat(), "until": until.isoformat(), "words": words}


# -- the two steps before the wait ---------------------------------------------------------------------------------------------------------


def _case_rec(client_dir: Path) -> dict[str, Any]:
    return {"version": VERSION, "contact": [], "originals": None} | _read(Path(client_dir) / CASE_FILE, {})


def record_contact(client_dir: Path, how: str, on: Any, note: str, who: str, role: str | None) -> dict[str, Any]:
    """(a) The attempt to reach the client about their file: how, the date, a note (kept until the purge; the firm's record keeps how and when)."""
    _attorney(role, "Recording the attempt to reach the client")
    who = _need(who)
    if how not in CONTACT_HOW:
        raise PurgeError("Say how the office tried to reach the client: " + ", ".join(v.lower() for v in CONTACT_HOW.values()) + ".")
    day = _day(on, "The day the office tried to reach the client")
    if day > clock.today():
        raise PurgeError("The day the office tried to reach the client cannot be in the future.")
    note = " ".join(str(note or "").split())[:1000]
    with _LOCK:
        rec = _case_rec(client_dir)
        rec["contact"] = list(rec.get("contact") or []) + [{"how": how, "on": day.isoformat(), "note": note, "by": who, "role": role, "at": clock.stamp()}]
        _write(Path(client_dir) / CASE_FILE, rec)
    events.record("purge", "contact", f"Recorded an attempt to reach the client about their file ({CONTACT_HOW[how].lower()})", case_dir=client_dir, who=who, role=role)
    return rec


def originals_list(client_dir: Path) -> list[dict[str, Any]]:
    """Every document of the case, for the attorney to say whether the office holds its original paper (a portal photo never is one)."""
    import index

    try:
        import documents

        records = documents.load(client_dir)["documents"]
    except Exception:  # noqa: BLE001
        records = _read(Path(client_dir) / "documents.json", {}).get("documents") or []
    done = {i["doc"]: i for i in ((_case_rec(client_dir).get("originals") or {}).get("items") or [])}
    out = []
    for r in records:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        portal = str(r.get("source") or "").startswith("portal")
        out.append({"doc": r["id"], "kind": index.type_name(r.get("type")), "file": (r.get("files") or [None])[0], "portal": portal,
                    "choice": (done.get(r["id"]) or {}).get("choice") or ("none" if portal else None), "where": (done.get(r["id"]) or {}).get("where") or ""})
    return out


def review_originals(client_dir: Path, items: list[dict[str, Any]] | None, none_held: bool, who: str, role: str | None) -> dict[str, Any]:
    """(b) The review of the originals: for each document "none", "returned" or "kept" (with where it is stored), or none held at all."""
    _attorney(role, "Reviewing the client's originals")
    who = _need(who)
    listed = {i["doc"]: i for i in originals_list(client_dir)}
    picked: list[dict[str, Any]] = []
    if not none_held:
        given = {str(i.get("doc")): i for i in items or [] if isinstance(i, dict)}
        missing = [d for d in listed if d not in given]
        if missing:
            raise PurgeError(f"Say for every document whether the office holds its original ({len(missing)} not answered).")
        for doc, row in listed.items():
            choice, where = str(given[doc].get("choice") or ""), " ".join(str(given[doc].get("where") or "").split())[:200]
            if choice not in ORIGINAL_CHOICES:
                raise PurgeError(f"{row['kind']}: choose none, returned or kept.")
            if choice == "kept" and not where:
                raise PurgeError(f"{row['kind']}: say where the kept original is stored.")
            picked.append({"doc": doc, "kind": row["kind"], "choice": choice, "where": where if choice == "kept" else ""})
    with _LOCK:
        rec = _case_rec(client_dir)
        rec["originals"] = {"none_held": bool(none_held), "items": picked, "by": who, "role": role, "at": clock.stamp()}
        _write(Path(client_dir) / CASE_FILE, rec)
    kept = sum(1 for i in picked if i["choice"] == "kept")
    returned = sum(1 for i in picked if i["choice"] == "returned")
    events.record("purge", "originals", "Reviewed the client's originals: the office holds none" if none_held or not (kept or returned)
                  else f"Reviewed the client's originals: {returned} returned, {kept} kept", case_dir=client_dir, who=who, role=role)
    return rec


def steps_done(client_dir: Path) -> dict[str, bool]:
    rec = _case_rec(client_dir)
    return {"contact": bool(rec.get("contact")), "originals": rec.get("originals") is not None}


# -- the firm's record of purges -----------------------------------------------------------------------------------------------------------


def _purges(data_root: Path) -> dict[str, Any]:
    return {"version": VERSION, "cases": {}} | _read(_home(data_root) / PURGES_FILE, {})


def _save_purges(data_root: Path, data: dict[str, Any]) -> None:
    _write(_home(data_root) / PURGES_FILE, data)


def entry(data_root: Path, case: str) -> dict[str, Any] | None:
    """The case's purge as the firm's record has it (the latest asked), or None."""
    return (_purges(data_root)["cases"] or {}).get(case)


def waiting(data_root: Path) -> dict[str, dict[str, Any]]:
    """{case id: its waiting purge} for every purge asked and not yet run or cancelled (the lists mark them for attorneys)."""
    return {c: e for c, e in (_purges(data_root)["cases"] or {}).items() if e.get("state") == "waiting"}


def _kind(client_dir: Path) -> str:
    try:
        from review.overview import name_and_kind

        return name_and_kind(client_dir)[1] or ""
    except Exception:  # noqa: BLE001
        return ""


def _masked(client_dir: Path, text: str) -> str:
    """A reason as the firm's record keeps it: any name, number, date or address of the case masked out (src/find.py's masking)."""
    try:
        import find

        return find.masker_for(client_dir)(text)
    except Exception:  # noqa: BLE001 -- the patterns alone
        import find

        return find.Masker()(text)


@contextmanager
def _policy_case(data_root, case):
    from portal.communication_consent import data_gate
    import jobs
    with data_gate(_home(Path(data_root))), jobs.case_lock(jobs.folder_for(data_root), case, timeout=30):
        yield


def ask(client_dir: Path, reason: str, who: str, role: str | None, attorneys: int = 1) -> dict[str, Any]:
    d = Path(client_dir)
    with _policy_case(d.parent, d.name):
        return _ask(d, reason, who, role, attorneys)


def _ask(client_dir: Path, reason: str, who: str, role: str | None, attorneys: int = 1) -> dict[str, Any]:
    """An attorney asks with current case destruction authority; contact and originals review must also be complete. The purge waits
    the firm's days; with more than one attorney account a second attorney confirms. A ledger row."""
    _attorney(role, "Asking to purge a case")
    who = _need(who)
    client_dir = Path(client_dir)
    data_root, case = client_dir.parent, client_dir.name
    import client_file_policy
    authority = client_file_policy.require_destruction(client_dir)
    _ended(client_dir)
    current = entry(data_root, case)
    if current and current.get("state") == "waiting":
        raise PurgeError(f"A purge of this case is already waiting (to be purged on {_us(current['purge_on'])}).")
    steps = steps_done(client_dir)
    if not steps["contact"]:
        raise PurgeError("First record the attempt to reach the client about their file.")
    if not steps["originals"]:
        raise PurgeError("First review the originals the office holds for this client.")
    reason = " ".join(str(reason or "").split())[:500]
    on = clock.today() + timedelta(days=wait_days(data_root))
    rec = {"id": os.urandom(6).hex(), "case": case, "kind": _kind(client_dir), "state": "waiting", "reason": _masked(client_dir, reason) if reason else "The keeping date had passed.",
           "early": False, "policy_authority": authority, "asked_by": who, "asked_at": clock.stamp(), "purge_on": on.isoformat(),
           "needs_confirm": attorneys > 1, "confirmed_by": None, "confirmed_at": None}
    with _LOCK:
        data = _purges(data_root)
        data["cases"][case] = rec
        _save_purges(data_root, data)
    events.record("purge", "asked", f"Asked to purge the case (to be purged on {_us(on.isoformat())})", case_dir=client_dir, who=who, role=role)
    return rec


def confirm(data_root: Path, case: str, who: str, role: str | None) -> dict[str, Any]:
    with _policy_case(data_root, case):
        return _confirm(data_root, case, who, role)


def _confirm(data_root: Path, case: str, who: str, role: str | None) -> dict[str, Any]:
    """The second attorney confirms (never the one who asked)."""
    _attorney(role, "Confirming a purge")
    who = _need(who)
    with _LOCK:
        data = _purges(data_root)
        rec = data["cases"].get(case)
        if not rec or rec.get("state") != "waiting":
            raise LookupError("No purge of this case is waiting.")
        _current_authority(data_root, case, rec)
        if who == rec["asked_by"]:
            raise PurgeError("A second attorney confirms: the attorney who asked cannot.")
        rec.update(confirmed_by=who, confirmed_at=clock.stamp())
        _save_purges(data_root, data)
    events.record("purge", "confirmed", "Confirmed the purge of the case", case=case, home=_home(data_root), who=who, role=role)
    return rec


def cancel(data_root: Path, case: str, who: str, role: str | None, why: str = "") -> dict[str, Any]:
    """Any attorney cancels a waiting purge."""
    _attorney(role, "Cancelling a purge")
    who = _need(who)
    with _LOCK:
        data = _purges(data_root)
        rec = data["cases"].get(case)
        if not rec or rec.get("state") != "waiting":
            raise LookupError("No purge of this case is waiting.")
        rec.update(state="cancelled", cancelled_by=who, cancelled_at=clock.stamp())
        _save_purges(data_root, data)
    events.record("purge", "cancelled", "Cancelled the purge of the case", case=case, home=_home(data_root), who=who, role=role)
    return rec


def ready(rec: dict[str, Any] | None, today: date | None = None) -> bool:
    """A waiting purge whose day has come and that is confirmed when it must be."""
    return bool(rec) and rec.get("state") == "waiting" and date.fromisoformat(rec["purge_on"]) <= (today or clock.today()) \
        and (not rec.get("needs_confirm") or bool(rec.get("confirmed_by")))


def due_now(data_root: Path) -> list[str]:
    return sorted(c for c, e in waiting(data_root).items() if ready(e))


# -- what the case is known by, read before anything is removed --------------------------------------------------------------------------


def _fold(text: Any) -> str:
    import unicodedata

    return " ".join("".join(c for c in unicodedata.normalize("NFD", str(text or "")) if unicodedata.category(c) != "Mn").lower().split())


class Identity:
    """What a row in a shared file can name the case by: its id (and its prospect's), every full name of its people, the client's e-mail and phone,
    its A-Numbers. mentions(text) says whether a row names it."""

    def __init__(self, case: str, prospects: list[str], names: set[str], contacts: set[str], numbers: set[str]):
        self.case, self.prospects = case, prospects
        self.ids = {case} | {f"prospect:{p}" for p in prospects}
        self.names = {n for n in (_fold(x) for x in names) if len(n) >= 5 and " " in n}
        self.contacts = {c for c in contacts if len(c) >= 6}
        self.numbers = {n for n in numbers if len(n) >= 7}
        idp = "|".join(re.escape(x) for x in sorted({case, *prospects}, key=len, reverse=True))
        self._id = re.compile(rf"(?<![\w-])(?:{idp})(?![\w])")

    def mentions(self, text: Any) -> bool:
        raw = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
        if self._id.search(raw):
            return True
        folded = _fold(raw)
        if any(n in folded for n in self.names):
            return True
        low = raw.lower()
        if any(c in low for c in self.contacts if "@" in c):
            return True
        digits = re.sub(r"\D", "", raw)
        return any(n in digits for n in self.numbers | {c for c in self.contacts if "@" not in c})


def _portal_profile(portal_root: Path | None, case: str) -> dict[str, Any]:
    if portal_root is None:
        return {}
    return _read(Path(portal_root) / "clients" / case / "profile.json", {})


def identity(data_root: Path, case: str, portal_root: Path | None = None) -> Identity:
    d = Path(data_root) / case
    names: set[str] = set()
    contacts: set[str] = set()
    numbers: set[str] = set()
    try:
        import people

        for p in people.rows(d):
            names |= {str(n.get("name") or "") for n in p.get("names") or [] if isinstance(n, dict)}
            if p.get("role") == "client":
                numbers |= {re.sub(r"\D", "", str(a.get("value") or "")) for a in p.get("a_numbers") or [] if isinstance(a, dict)}
    except Exception:  # noqa: BLE001
        pass
    facts = (_read(d / "fact_graph.json", {}).get("facts") or {})
    if isinstance(facts, dict):
        given = (facts.get("applicant.given_name") or {}).get("value")
        family = (facts.get("applicant.family_name") or {}).get("value")
        if given and family:
            names.add(f"{given} {family}")
        for key, f in facts.items():
            if isinstance(f, dict) and re.search(r"(email|phone)$", key) and f.get("value"):
                v = str(f["value"]).strip()
                contacts.add(v.lower() if "@" in v else re.sub(r"\D", "", v))
            if isinstance(f, dict) and key == "applicant.a_number" and f.get("value"):
                numbers.add(re.sub(r"\D", "", str(f["value"])))
    profile = _portal_profile(portal_root, case)
    if profile.get("name"):
        names.add(str(profile["name"]))
    for k in ("email", "phone"):
        if profile.get(k):
            v = str(profile[k]).strip()
            contacts.add(v.lower() if "@" in v else re.sub(r"\D", "", v))
    try:
        from review.state import display_name

        names.add(display_name(d))
    except Exception:  # noqa: BLE001
        pass
    prospects = [str(profile["prospect"])] if profile.get("prospect") else []
    try:
        import prospects as pr

        prospects += [pid for pid in pr.ids(data_root) if (pr.read(pr.folder(data_root) / pid).get("became_client") or {}).get("id") == case]
    except Exception:  # noqa: BLE001
        pass
    if Path(data_root).resolve() == (_home(Path(data_root)) / "clients").resolve() and _home(Path(data_root)).name.casefold() == "data":
        from portal.communication_consent import Scope
        from portal.contact_transitions import _composition
        from portal.promotion import associated_prospects
        home = _home(Path(data_root))
        scope = Scope(home.parent, home / "portal", home / "clients")
        _, store = _composition(scope, "client", readonly=True)
        prospects = associated_prospects(scope, store, case, prospects)
    return Identity(case, sorted(set(prospects)), names, contacts, numbers)


# -- emptying the stores ---------------------------------------------------------------------------------------------------------------------

# Every store in src/records.py (RECORDS, DATABASES, WORKING_FILES) that can hold a case, in the words the purge screen uses. empty_stores()
# empties each and reports it by this id.
STORES: list[tuple[str, str]] = [
    ("case_folder", "The case's own folder: facts, documents and their scans, decisions, notes, tasks, deadlines, filled forms and packets, letters, "
                    "the agreement, translations, the restriction record, the questions asked about it, and every other record of the case"),
    ("source", "The folder of the client's original scans, when it is kept apart from the case's folder"),
    ("portal", "The client's portal: their profile and consent, answers, uploads, messages, requests, the portal's log, their sign-in links and "
               "sessions, the messages waiting to be sent to them"),
    ("prospect", "The first call the client began as: its record, answers, notes and tasks, and the portal's copy"),
    ("ledger", "The event ledger: every row about the case, blanked in place so the ledger's chain and daily seals still hold (the purge's own rows are kept)"),
    ("views", "The view log: every opening of the case, its documents and forms"),
    ("access", "The staff access log: every opening of the restricted case's record"),
    ("conflicts", "The conflict log: the search made when the client was added, and the case's people in every other search's hits"),
    ("find_questions", "The questions asked of Find across the firm that name the client"),
    ("wordings", "The firm's wordings: the case among the cases a wording was used on (the wording itself, slots only, stays)"),
    ("reader_examples", "The reader's labelled examples taken from the case"),
    ("evaluation_authorizations", "The case's explicit evaluation data-use authorizations and approval evidence"),
    ("evaluation_candidates", "The case's local evaluation candidate references, awaiting adjudication"),
    ("learning", "The learning store: the document reader's answers and the labels for the case's documents"),
    ("audit_fill", "The boxes the office changes: the case's rows and its values"),
    ("overnight", "The overnight run's files: the case's state, progress and lines in the morning report, and its rows in the accuracy comparison"),
    ("inbox", "The notice inbox: notices waiting for the case and their log rows"),
    ("clio", "The link to the practice-management system kept here (the system's own record is the firm's to delete there)"),
    ("drive_settings", "The installation-owned Drive mappings and attributable interrupted writes"),
    ("sync", "The documents mirrored from another system: the client's entry"),
    ("reference", "The hand-filled reference forms for the case and their marks"),
    ("roster", "The lists' saved copy of every case"),
    ("jobs", "The job queue: the readings that named the case"),
    ("index", "The search index (index.db): the case's documents and their text"),
    ("query", "The query layer (query.db): the case's rows, its people and its ledger rows"),
    ("find", "Find across the firm's index (find.db): the case's passages and their embeddings"),
    ("outbox", "Messages to the client or about the client not yet sent"),
    ("communication", "Service-message consent/STOP associations and attributed interrupted writes; opaque retired STOP identities prevent replay"),
    ("exports", "The client's file made to hand over (the zip in the exports folder)"),
]
TITLES = dict(STORES)
LEDGER_KEEPS = ("The ledger keeps only the purge's own rows: the case id, the kind of case, who asked, who confirmed, when, the reason, and that the client "
                "was contacted and the originals reviewed (how and when, never what was said).")


def _jsonl_filter(path: Path, drop: Callable[[dict[str, Any]], bool], edit: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> int:
    """Rewrites a JSON Lines file without the rows `drop` says, every other row through `edit`; a row that is not JSON is dropped if it names the case
    (drop gets {"_raw": line}). Returns the rows removed or changed. The file keeps its permission; an append that lands while it is rewritten is kept."""
    if not path.is_file():
        return 0
    for _ in range(5):
        before = path.stat().st_size
        lines = path.read_bytes().split(b"\n")
        out, n = [], 0
        for raw in lines:
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                row = {"_raw": raw.decode("utf-8", "replace")}
            if not isinstance(row, dict):
                row = {"_raw": raw.decode("utf-8", "replace")}
            if drop(row):
                n += 1
                continue
            if edit is not None and "_raw" not in row:
                changed = edit(row)
                if changed != row:
                    n += 1
                    out.append(json.dumps(changed, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                    continue
            out.append(raw)
        if not n:
            return 0
        mode = path.stat().st_mode & 0o777
        tmp = path.with_name(path.name + f".{os.getpid()}.purge.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode or 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(b"\n".join(out) + (b"\n" if out else b""))
        if path.stat().st_size != before:  # a row was appended meanwhile: read again
            tmp.unlink(missing_ok=True)
            continue
        os.replace(tmp, path)
        return n
    raise OSError(f"{path.name} kept changing while it was rewritten")


def _json_edit(path: Path, fn: Callable[[Any], tuple[Any, int]]) -> int:
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    data, n = fn(data)
    if n:
        _write(path, data)
    return n


def _rmtree(path: Path) -> int:
    if not path.exists() or path.is_symlink():
        return 0
    if path.is_file():
        path.unlink()
        return 1
    n = sum(1 for p in path.rglob("*") if p.is_file())
    shutil.rmtree(path)
    return n


def _vacuum(path: Path) -> None:
    """Compacts a SQLite file so no freed page keeps a deleted row's bytes, and empties its write-ahead log."""
    if not path.is_file():
        return
    with closing(sqlite3.connect(path, timeout=60)) as db:
        db.execute("pragma wal_checkpoint(TRUNCATE)")
        db.execute("vacuum")
        db.execute("pragma wal_checkpoint(TRUNCATE)")


def _documents_root(home: Path) -> Path:
    try:
        from connectors import clio

        root = clio.state(home).get("clients_root")
    except Exception:  # noqa: BLE001
        root = None
    return Path(root or os.environ.get("I485_CLIENTS_ROOT") or home.parent / "clients")


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except (OSError, ValueError):
        return False


def _portal_staging(root: Path, ids, kind: str) -> int:
    """Only new portal durable-write residue with an exact case-digest filename."""
    import hashlib
    import stat
    from portal.queue_bridge import _safe
    if kind not in {"job", "queue"}:
        raise ValueError("Invalid portal staging store")
    root = _safe(root)
    roots = [root, _safe(root / "done")] if kind == "job" else [root]
    digests = {hashlib.sha256(case.encode()).hexdigest() for case in ids}
    pattern = re.compile(r"portal-" + kind + r"-([0-9a-f]{64})-[0-9a-f]{16}\.tmp")
    removed = 0
    for folder in roots:
        if not folder.exists():
            continue
        if not folder.is_dir():
            raise ValueError("Portal staging folder is unavailable")
        for path in folder.iterdir():
            match = pattern.fullmatch(path.name)
            if not match or match[1] not in digests:
                continue
            _safe(path)  # includes all ancestors; never traverse a link/junction
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError("Portal staging record is unavailable")
            path.unlink()
            removed += 1
    return removed


def empty_stores(data_root: Path, case: str, portal_root: Path | None = None, views_log: Path | None = None, access_log: Path | None = None,
                 who: Identity | None = None) -> dict[str, Any]:
    """Installed caller holds case lock; gate spans cleanup and profile removal."""
    from portal.communication_consent import Scope, data_gate
    data_root = Path(data_root)
    home = _home(data_root)
    with data_gate(home):
        communication = {"removed": 0, "left": []}
        if home.name.casefold() == "data":
            kind = "prospect" if data_root.resolve() == (home / "prospects").resolve() else "client"
            expected = home / ("prospects" if kind == "prospect" else "clients")
            if data_root.resolve() != expected.resolve():
                raise ValueError("Communication purge cases belong to another installation.")
            current_portal = home / "portal" / "prospects" if kind == "prospect" else home / "portal"
            if portal_root is not None and Path(portal_root).resolve() != current_portal.resolve():
                raise ValueError("Communication purge portal belongs to another installation.")
            scope = Scope(home.parent, current_portal, expected)
            who = who or identity(data_root, case, portal_root)
            if kind == "client":
                from portal.contact_transitions import _composition
                from portal.promotion import associated_prospects
                _, store = _composition(scope, "client", readonly=True)
                related = associated_prospects(scope, store, case, who.prospects)
                who = Identity(who.case, related, who.names, who.contacts, who.numbers)
            selected = {(kind, case)} | ({("prospect", pid) for pid in who.prospects} if kind == "client" else set())
            from portal.contact_transitions import preflight_purge
            preflight_purge(scope, selected)  # exception before ANY destructive store cleanup
            if (home / "communication-stop").exists():
                try:
                    from portal.communication_lifecycle import purge_members
                    communication = purge_members(scope, selected)
                except (ValueError, OSError, TypeError, KeyError):
                    communication["left"].append("Communication STOP receipt/profile inventory is damaged or unavailable; scoped cleanup remains unresolved.")
        result = _empty_stores(data_root, case, portal_root, views_log, access_log, who)
        next(row for row in result["stores"] if row["id"] == "communication")["removed"] = communication["removed"]
        result["left"].extend(communication["left"])
        return result


def _empty_stores(data_root: Path, case: str, portal_root: Path | None = None, views_log: Path | None = None, access_log: Path | None = None,
                  who: Identity | None = None) -> dict[str, Any]:
    """Takes the case out of every store the product writes. Returns {"stores": [{id, title, removed}], "left": [words for what the product could
    not remove], "copies": [older backups and exports that may hold the case]}. Nothing of the case is in the result."""
    data_root = Path(data_root)
    home = _home(data_root)
    d = data_root / case
    who = who or identity(data_root, case, portal_root)
    ids = who.ids
    out: dict[str, int] = {k: 0 for k, _ in STORES}
    left: list[str] = []

    meta = _read(d / "meta.json", {})
    engagement_rec = _read(d / "engagement.json", {})

    # These optional immutable artifacts are namespaced by one canonical case.
    # Existing legal destruction authority and the surrounding data gate apply;
    # no other case's authorization or candidate history is rewritten/deleted.
    try:
        import evaluation_artifacts
        out.update(evaluation_artifacts.remove_case(home, case))
    except (OSError, ValueError):
        left.append("The case's evaluation authorization/candidate artifacts could not be removed safely; cleanup remains unresolved.")

    # the reader's examples, read from the case's folder's name, before the folder goes
    try:
        import reader_examples

        out["reader_examples"] = reader_examples.remove_case(d)
    except Exception as exc:  # noqa: BLE001
        left.append(f"The reader's labelled examples could not be removed ({type(exc).__name__}).")

    # the client's file made to hand over, named on the case
    try:
        import engagement

        name = (engagement_rec.get("file") or {}).get("name")
        if name and engagement.EXPORT_NAME.fullmatch(str(name)):
            out["exports"] += _rmtree(engagement.exports_folder(data_root) / name)
    except Exception:  # noqa: BLE001
        pass

    # the source scans kept apart: only inside the documents root, and only a folder no other case uses
    try:
        from source_association import purge_pending
        out["source"] += purge_pending(data_root, case, _documents_root(home))
    except (ValueError, OSError):
        left.append("Source setup originals or interrupted copies could not be assigned safely; canonical cleanup remains unresolved.")
    source = Path(str(meta.get("source_folder") or "")) if meta.get("source_folder") else None
    if source is not None and source.exists() and not _inside(source, d) and not (portal_root and _inside(source, Path(portal_root))):
        docs_root = _documents_root(home)
        shared = False
        for other in (p for p in data_root.iterdir() if p.is_dir() and p.name != case) if data_root.is_dir() else ():
            o = _read(other / "meta.json", {}).get("source_folder")
            if o and (Path(o).resolve() == source.resolve() or _inside(Path(o), source.parent if source.name == "source" else source)):
                shared = True
        if shared:
            left.append("The folder of the client's original scans is also another case's: it was left in place for the firm's IT to sort.")
        elif _inside(source, docs_root):
            top = source.parent if source.name == "source" and source.parent.parent.resolve() == docs_root.resolve() else source
            out["source"] = _rmtree(top)
        else:
            left.append("The folder of the client's original scans is outside the folders the product keeps: the firm's IT removes it.")
    try:
        docs_root = _documents_root(home)
        from connectors import sync
        import oslock
        for path in (docs_root, docs_root / "sync_state.json", docs_root / "sync_state.lock", docs_root / "sync_state.tmp"):
            sync._plain(path)
        if docs_root.is_dir():
            with oslock.locked(docs_root / "sync_state.lock", timeout=60):
                paths = [docs_root / "sync_state.json", docs_root / "sync_state.tmp"]
                paths += [p for p in docs_root.iterdir() if re.fullmatch(r"sync_state\.(json|tmp)\.\d+\.tmp", p.name)]
                for path in paths:
                    sync._plain(path)
                    if path.exists():
                        # A corrupt global record cannot prove whose metadata
                        # remains; do not silently report successful cleanup.
                        value = json.loads(path.read_text(encoding="utf-8"))
                        if not isinstance(value, dict) or not isinstance(value.get("clients"), dict):
                            raise ValueError("Invalid mirror state")
                        out["sync"] += _json_edit(path, lambda s: _pop_keys(s, "clients", ids))
    except Exception:  # noqa: BLE001
        left.append("The mirrored-document state or interrupted state write could not be cleaned.")

    # the case's own folder
    out["case_folder"] = _rmtree(d)

    # the portal
    if portal_root is not None:
        portal_root = Path(portal_root)
        try:
            import oslock
            from portal.queue_bridge import _safe

            # A bridge/enqueue validates the profile and publishes under this
            # same lock. Remove the profile before releasing it, so an earlier
            # producer finishes before cleanup and a later producer refuses.
            # The installed purge handler already holds the case lock; no
            # pipeline or ledger work runs while this queue lock is held.
            with oslock.locked(_safe(portal_root / "queue" / ".lock"), timeout=10):
                out["portal"] += _rmtree(_safe(portal_root / "clients" / case))
                out["portal"] += _portal_staging(portal_root / "queue", ids, "queue")
                out["portal"] += _rmtree(_safe(portal_root / "queue" / case))
        except (ValueError, OSError):
            left.append("Portal profile and queue cleanup could not finish; check the configured queue folder and retry.")
        out["portal"] += _json_edit(portal_root / "auth.json", lambda a: _auth_without(a, {case}))
        out["outbox"] = _jsonl_filter(portal_root / "outbox.jsonl", lambda r: who.mentions(r))
        for pid in who.prospects:
            proot = portal_root / "prospects"
            try:
                # Separate explicit prospect store: never nest store locks.
                with oslock.locked(_safe(proot / "queue" / ".lock"), timeout=10):
                    out["prospect"] += _rmtree(_safe(proot / "clients" / pid))
                    out["prospect"] += _portal_staging(proot / "queue", {pid}, "queue")
                    out["prospect"] += _rmtree(_safe(proot / "queue" / pid))
            except (ValueError, OSError):
                left.append("Prospect portal profile and queue cleanup could not finish; check the configured queue folder and retry.")
            out["prospect"] += _json_edit(proot / "auth.json", lambda a: _auth_without(a, {pid}))

    # the first call it began as
    try:
        import prospects as pr

        for pid in who.prospects:
            out["prospect"] += _rmtree(pr.folder(data_root) / pid) if pid not in (".", "..") and "/" not in pid else 0
    except Exception:  # noqa: BLE001
        pass

    # the ledger, but the purge's own rows: each of the case's rows is blanked in place (events.tombstone: its time and its place in the chain stay, so the
    # links and the daily seals of src/ledger_seal.py still hold), under the ledger's own lock so no row is appended while a month is rewritten; the purge's
    # line in ledger_redactions.jsonl says how many rows it blanked and the digest of their hashes, which is what the ledger's check accepts them by
    rewritten: list[str] = []
    base = events.base_path(home)
    pid = str((entry(data_root, case) or {}).get("id") or "")

    def blank(r: dict[str, Any]) -> dict[str, Any]:
        return events.tombstone(r, pid) if r.get("case") in ids and r.get("kind") not in ("purge", events.REDACTED) else r

    import oslock

    with oslock.locked(events.lock_path(base), timeout=events.LOCK_WAIT):
        for f in events.files(base):
            n = _jsonl_filter(f, lambda r: "_raw" in r and who.mentions(r["_raw"]), blank)
            if n:
                out["ledger"] += n
                rewritten.append(f.name)
        blanked = [r for r in events.rows(base) if r.get("kind") == events.REDACTED and r.get("purge") == pid]
        if blanked:
            line = {"purge": pid, "rows": len(blanked), "digest": events.redaction_digest([str(r.get("hash") or "") for r in blanked]), "at": clock.stamp()}
            fd = os.open(events.redactions_path(base), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, (json.dumps(line, separators=(",", ":")) + "\n").encode("utf-8"))
            finally:
                os.close(fd)

    # the view log and the access log
    views_log = Path(views_log) if views_log else home / "review_views.jsonl"
    out["views"] = _jsonl_filter(views_log, lambda r: r.get("client") in ids or who.mentions(r))
    access_logs = [Path(access_log)] if access_log else sorted(home.glob("*_access.jsonl"))
    for p in access_logs:
        out["access"] += _jsonl_filter(p, lambda r: r.get("client") in ids or who.mentions(r))

    # the conflict log
    try:
        import conflicts

        dropped: set[str] = set()
        clog = conflicts.log_path(data_root)

        def conflict_drop(r: dict[str, Any]) -> bool:
            if r.get("case") in ids or r.get("search") in dropped:
                dropped.add(str(r.get("id")))
                return True
            if r.get("kind") == "search" and not r.get("case") and who.mentions(r.get("query") or {}):
                dropped.add(str(r.get("id")))
                return True
            return "_raw" in r and who.mentions(r["_raw"])

        def conflict_edit(r: dict[str, Any]) -> dict[str, Any]:
            hits = [h for h in r.get("hits") or [] if not (isinstance(h, dict) and (h.get("case") in ids or who.mentions(h)))]
            matched = {k: [c for c in v if c not in ids] if isinstance(v, list) else v for k, v in (r.get("matched") or {}).items()}
            if hits == (r.get("hits") or []) and matched == (r.get("matched") or {}):
                return r
            return r | ({"hits": hits} if "hits" in r else {}) | ({"matched": matched} if "matched" in r else {})

        out["conflicts"] = _jsonl_filter(clog, conflict_drop, conflict_edit)
    except Exception as exc:  # noqa: BLE001
        left.append(f"The conflict log could not be cleaned ({type(exc).__name__}).")

    # Find across the firm: the questions that name the client, and the index
    out["find_questions"] = _jsonl_filter(home / "find_questions.jsonl", lambda r: False,
                                          lambda r: r | {"question": "[removed when a case was purged]"} if who.mentions(r.get("question") or "") else r)
    try:
        import find

        fpath = find.default_path(data_root)
        if fpath.exists():
            with closing(sqlite3.connect(fpath)) as db:
                out["find"] = db.execute(f"select count(*) from passages where case_id in ({','.join('?' * len(ids))})", sorted(ids)).fetchone()[0]
            find.forget(data_root, case, fpath)
            _vacuum(fpath)
    except Exception as exc:  # noqa: BLE001
        left.append(f"Find across the firm's index could not be cleaned ({type(exc).__name__}).")

    # the firm's wordings
    try:
        import wordings

        wroot = wordings.root(data_root)
        for p in sorted(wroot.glob("*/*/*.json")) if wroot.is_dir() else []:
            out["wordings"] += _json_edit(p, lambda w: _wording_without(w, ids))
        wordings._cache.clear()
    except Exception as exc:  # noqa: BLE001
        left.append(f"The firm's wordings could not be cleaned ({type(exc).__name__}).")

    # the learning store
    lpath = home / "learning.db"
    if lpath.is_file():
        with closing(sqlite3.connect(lpath, timeout=60)) as db:
            for table in ("model_runs", "labels"):
                try:
                    out["learning"] += db.execute(f"delete from {table} where client in ({','.join('?' * len(ids))})", sorted(ids)).rowcount
                except sqlite3.OperationalError:
                    pass
            db.commit()
        _vacuum(lpath)

    # the boxes the office changes
    try:
        import audit_fill

        for p in (audit_fill.path(data_root), audit_fill.partial_path(data_root)):
            out["audit_fill"] += _json_edit(p, lambda a: _audit_without(a, ids, case))
    except Exception as exc:  # noqa: BLE001
        left.append(f"The boxes the office changes could not be cleaned ({type(exc).__name__}).")

    # the overnight run's files
    out["overnight"] += _json_edit(home / "batch_state.json", lambda s: _pop_top(s, ids))
    out["overnight"] += _json_edit(home / "batch_progress.json", lambda s: _list_without(s, "running", ids))
    report = home / "batch_report.txt"
    if report.is_file():
        lines = report.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        kept = [x for x in lines if not who.mentions(x)]
        if len(kept) != len(lines):
            report.write_text("".join(kept), encoding="utf-8")
            out["overnight"] += len(lines) - len(kept)
    try:
        import accuracy

        latest = Path(os.environ.get("I485_ACCURACY_LATEST") or home / "accuracy_latest.json")
        out["overnight"] += _json_edit(latest, lambda a: _accuracy_without(a, ids, case))
        ref = accuracy.reference_dir(data_root)
        for p in sorted(ref.glob(f"{glob_escape(case)}.*")) if ref.is_dir() else []:
            if p.name.split(".")[0] == case:
                out["reference"] += _rmtree(p)
    except Exception as exc:  # noqa: BLE001
        left.append(f"The accuracy comparison's files could not be cleaned ({type(exc).__name__}).")

    # the notice inbox
    try:
        import inbox

        ibox = inbox.default_path(data_root)

        def queue_edit(q: Any) -> tuple[Any, int]:
            if not isinstance(q, list):
                return q, 0
            keep, n = [], 0
            for item in q:
                if isinstance(item, dict) and (any(isinstance(c, dict) and c.get("case") in ids for c in item.get("candidates") or [])
                                               or who.mentions(item.get("read") or {})):
                    for f in (ibox / "waiting" / str(item.get("file") or ""),):
                        if item.get("file") and f.is_file():
                            f.unlink()
                    n += 1
                    continue
                keep.append(item)
            return keep, n

        out["inbox"] += _json_edit(ibox / "queue.json", queue_edit)
        out["inbox"] += _jsonl_filter(ibox / "inbox_log.jsonl", lambda r: r.get("case") in ids or who.mentions(r))
    except Exception as exc:  # noqa: BLE001
        left.append(f"The notice inbox could not be cleaned ({type(exc).__name__}).")

    # the practice-management system's link kept here
    clio_dir = home / "clio"
    out["clio"] += _json_edit(clio_dir / "state.json", lambda s: _clio_without(s, ids))
    out["clio"] += _json_edit(clio_dir / "webhook.json", lambda s: _touched_without(s, ids))

    # Installation-owned configuration: preserve provider and all surviving bindings.
    try:
        from connectors.drive_settings import purge_settings
        removed, unresolved = purge_settings(home, ids)
        out["drive_settings"] += removed
        if unresolved:
            left.append("Some installation Drive settings or interrupted writes could not be resolved safely; check the configured data folder.")
    except Exception:  # noqa: BLE001
        left.append("The installation Drive settings could not be cleaned safely; check the configured data folder.")

    # the lists' saved copy, the job queue
    rpath = Path(os.environ["I485_ROSTER"]) if os.environ.get("I485_ROSTER") else home / "roster.json"
    out["roster"] = _json_edit(rpath, lambda r: _pop_keys(r, "entries", ids))
    try:
        import jobs

        jroot = jobs.folder_for(data_root)
        from review.staff_upload_recovery import purge_job_staging
        staff_removed, staff_unresolved = purge_job_staging(jroot, ids)
        out["jobs"] += staff_removed
        if staff_unresolved:
            left.append("Some staff upload interrupted job writes could not be assigned safely; check the configured jobs folder.")
        from connectors.drive_intake import purge_receipts
        removed, unresolved = purge_receipts(jroot, ids)
        out["jobs"] += removed
        if unresolved:
            left.append("Some Drive intake receipts or interrupted writes could not be assigned safely; check the configured jobs folder.")
        out["jobs"] += _portal_staging(jroot, ids, "job")
        for p in sorted(jroot.glob("*.json")) + sorted(jroot.glob("done/*.json")) if jroot.is_dir() else []:
            j = _read(p, {})
            if j.get("client") in ids or who.mentions(j.get("args") or {}):
                p.unlink(missing_ok=True)
                out["jobs"] += 1
    except Exception:  # noqa: BLE001
        left.append("The job records or interrupted portal job writes could not be removed; check the configured jobs folder.")

    # the search index and the query layer: the case's rows out, the files compacted
    try:
        import index

        ipath = index.default_path(data_root)
        if ipath.exists():
            with closing(index.connect(ipath)) as db:
                out["index"] = db.execute("select count(*) from documents where case_id = ?", (case,)).fetchone()[0]
                with db:
                    db.execute("delete from text_fts where rowid in (select id from documents where case_id = ?)", (case,))
                    db.execute("delete from documents where case_id = ?", (case,))
                    db.execute("delete from cases where case_id = ?", (case,))
                    db.execute("insert into text_fts(text_fts) values('optimize')")  # the full-text index's old segments hold the words until merged
            _vacuum(ipath)
    except Exception as exc:  # noqa: BLE001
        left.append(f"The search index could not be cleaned ({type(exc).__name__}).")
    try:
        import query

        qpath = query.default_path(data_root)
        if qpath.exists():
            with closing(sqlite3.connect(qpath, timeout=60)) as db:
                marks = ",".join("?" * len(ids))
                for table in ("cases", "documents", "facts", "decisions", "deadlines", "filings", "people", "events"):
                    try:
                        out["query"] += db.execute(f"delete from {table} where case_id in ({marks})", sorted(ids)).rowcount
                    except sqlite3.OperationalError:
                        pass
                for name in rewritten:  # those month files were rewritten: read again from their start
                    db.execute("delete from events where file = ?", (name,))
                    db.execute("delete from ledger_files where file = ?", (name,))
                db.commit()
            _vacuum(qpath)
    except Exception as exc:  # noqa: BLE001
        left.append(f"The query layer could not be cleaned ({type(exc).__name__}).")

    return {"stores": [{"id": k, "title": t, "removed": out[k]} for k, t in STORES], "left": left, "copies": copies_left(data_root)}


def glob_escape(text: str) -> str:
    import glob

    return glob.escape(text)


def _pop_keys(data: Any, key: str, ids: set[str]) -> tuple[Any, int]:
    if not isinstance(data, dict) or not isinstance(data.get(key), dict):
        return data, 0
    n = sum(1 for k in list(data[key]) if k in ids)
    for k in ids:
        data[key].pop(k, None)
    return data, n


def _pop_top(data: Any, ids: set[str]) -> tuple[Any, int]:
    if not isinstance(data, dict):
        return data, 0
    n = sum(1 for k in ids if k in data)
    for k in ids:
        data.pop(k, None)
    return data, n


def _list_without(data: Any, key: str, ids: set[str]) -> tuple[Any, int]:
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        return data, 0
    before = len(data[key])
    data[key] = [x for x in data[key] if x not in ids]
    return data, before - len(data[key])


def _auth_without(data: Any, clients: set[str]) -> tuple[Any, int]:
    if not isinstance(data, dict):
        return data, 0
    n = 0
    for table in ("links", "sessions"):
        rows = data.get(table)
        if isinstance(rows, dict):
            for k in [k for k, v in rows.items() if isinstance(v, dict) and v.get("client") in clients]:
                rows.pop(k)
                n += 1
    return data, n


def _wording_without(w: Any, ids: set[str]) -> tuple[Any, int]:
    if not isinstance(w, dict):
        return w, 0
    n = 0
    for key in ("uses", "edits"):
        if isinstance(w.get(key), list):
            kept = [u for u in w[key] if not (isinstance(u, dict) and u.get("case") in ids)]
            n += len(w[key]) - len(kept)
            w[key] = kept
    if w.get("from_file") and str(w["from_file"]).split("/")[-1].split(".")[0] in ids:
        w["from_file"] = ""
        n += 1
    return w, n


def _audit_without(a: Any, ids: set[str], case: str) -> tuple[Any, int]:
    if not isinstance(a, dict):
        return a, 0
    n = 0
    if isinstance(a.get("rows"), list):
        kept = [r for r in a["rows"] if not (isinstance(r, dict) and r.get("case") in ids)]
        n += len(a["rows"]) - len(kept)
        a["rows"] = kept
    if isinstance(a.get("skipped"), list):
        kept = [s for s in a["skipped"] if not str(s).startswith(case + " ") and not str(s).startswith(case + ":")]
        n += len(a["skipped"]) - len(kept)
        a["skipped"] = kept
    for key in ("done", "cases"):
        if isinstance(a.get(key), list):
            kept = [c for c in a[key] if c not in ids]
            n += len(a[key]) - len(kept)
            a[key] = kept
    return a, n


def _accuracy_without(a: Any, ids: set[str], case: str) -> tuple[Any, int]:
    if not isinstance(a, dict):
        return a, 0
    n = 0
    if isinstance(a.get("results"), list):
        kept = [r for r in a["results"] if not (isinstance(r, dict) and r.get("case") in ids)]
        n += len(a["results"]) - len(kept)
        a["results"] = kept
    if isinstance(a.get("skipped"), list):
        kept = [s for s in a["skipped"] if not str(s).startswith(case + ":") and not str(s).startswith(case + " ")]
        n += len(a["skipped"]) - len(kept)
        a["skipped"] = kept
    return a, n


def _clio_without(s: Any, ids: set[str]) -> tuple[Any, int]:
    if not isinstance(s, dict):
        return s, 0
    n = 0
    matters = s.get("matters")
    if isinstance(matters, dict):
        for k in [k for k, v in matters.items() if isinstance(v, dict) and v.get("case") in ids]:
            matters.pop(k)
            n += 1
    for key in ("out", "held", "failed", "allowed"):
        if isinstance(s.get(key), dict):
            for k in ids:
                if k in s[key]:
                    s[key].pop(k)
                    n += 1
    for key in ("allowed_history", "errors"):
        if isinstance(s.get(key), list):
            kept = [r for r in s[key] if not (isinstance(r, dict) and r.get("case") in ids)]
            n += len(s[key]) - len(kept)
            s[key] = kept
    return s, n


def _touched_without(s: Any, ids: set[str]) -> tuple[Any, int]:
    if not isinstance(s, dict) or not isinstance(s.get("touched"), dict):
        return s, 0
    gone = [k for k, v in s["touched"].items() if isinstance(v, dict) and v.get("case") in ids]
    for k in gone:
        s["touched"].pop(k)
    return s, len(gone)


def copies_left(data_root: Path) -> list[dict[str, str]]:
    """The copies the product does not delete that may still hold the case: every backup made so far (the firm deletes them on its own schedule) and
    the firm's own exports of all its data."""
    out: list[dict[str, str]] = []
    try:
        import backups

        folder = (backups.read_log().get("last_backup") or {}).get("folder")
        for p in backups.archives(Path(folder)) if folder and Path(folder).is_dir() else []:
            out.append({"what": "backup", "name": p.name, "where": str(p.parent)})
    except Exception:  # noqa: BLE001
        pass
    try:
        import engagement

        ex = engagement.exports_folder(data_root)
        for p in sorted(ex.glob("i485-firm-data-*.zip")) if ex.is_dir() else []:
            out.append({"what": "export", "name": p.name, "where": str(p.parent)})
    except Exception:  # noqa: BLE001
        pass
    return out


# -- the run -----------------------------------------------------------------------------------------------------------------------------------


def run(data_root: Path, case: str, portal_root: Path | None = None, views_log: Path | None = None, access_log: Path | None = None) -> dict[str, Any]:
    from portal.communication_consent import Scope, data_gate
    data_root = Path(data_root)
    home = _home(data_root)
    import jobs
    with data_gate(home), jobs.case_lock(jobs.folder_for(data_root), case, timeout=30):
        if home.name.casefold() == "data":
            kind = "prospect" if data_root.resolve() == (home / "prospects").resolve() else "client"
            expected = home / ("prospects" if kind == "prospect" else "clients")
            current_portal = home / "portal" / "prospects" if kind == "prospect" else home / "portal"
            if data_root.resolve() != expected.resolve() or (portal_root is not None and Path(portal_root).resolve() != current_portal.resolve()):
                raise ValueError("Contact purge scope belongs to another installation.")
            who = identity(data_root, case, portal_root)
            selected = {(kind, case)} | ({("prospect", pid) for pid in who.prospects} if kind == "client" else set())
            from portal.contact_transitions import preflight_purge
            preflight_purge(Scope(home.parent, current_portal, expected), selected)
        return _run(data_root, case, portal_root, views_log, access_log)


def _run(data_root: Path, case: str, portal_root: Path | None = None, views_log: Path | None = None, access_log: Path | None = None) -> dict[str, Any]:
    """The purge itself (the job's handler calls it): a ledger row, every store emptied, the originals kept indexed, the firm's record updated, a
    ledger row. Refused unless the purge is waiting, its day has come and it is confirmed when it must be."""
    data_root = Path(data_root)
    home = _home(data_root)
    rec = entry(data_root, case)
    if not ready(rec):
        raise PurgeError("This purge is not ready: it is not waiting, its day has not come, or a second attorney has not confirmed it.")
    _current_authority(data_root, case, rec, portal_root)
    d = data_root / case
    who = identity(data_root, case, portal_root)
    steps = _case_rec(d)
    try:  # the client's name as the case shows it, for the originals index (the one place it stays)
        import engagement

        name = engagement.client_name(d, portal_root)
    except Exception:  # noqa: BLE001
        name = sorted(who.names, key=len, reverse=True)[0].title() if who.names else ""
    events.record("purge", "started", "Started purging the case", case=case, home=home, default_who=("The purge", "system", "system"))
    kept = [i for i in ((steps.get("originals") or {}).get("items") or []) if i.get("choice") == "kept"]
    if kept:
        with _LOCK:
            idx = _read(home / ORIGINALS_FILE, {"version": VERSION, "originals": []})
            idx.setdefault("originals", [])
            idx["originals"] += [{"case": case, "client": name, "kind": i["kind"], "where": i["where"], "purged_on": clock.today().isoformat()} for i in kept]
            _write(home / ORIGINALS_FILE, idx)
    result = empty_stores(data_root, case, portal_root, views_log, access_log, who)
    contact = [{"how": c["how"], "on": c["on"], "by": c["by"]} for c in steps.get("contact") or []]
    originals = steps.get("originals") or {}
    with _LOCK:
        data = _purges(data_root)
        rec = data["cases"][case]
        rec.update(state="done", done_at=clock.stamp(), contact=contact,
                   originals={"none_held": bool(originals.get("none_held")), "kept": len(kept),
                              "returned": sum(1 for i in originals.get("items") or [] if i.get("choice") == "returned"), "by": originals.get("by"),
                              "at": originals.get("at")},
                   stores=[{"id": s["id"], "removed": s["removed"]} for s in result["stores"]], left=result["left"], copies=result["copies"])
        _save_purges(data_root, data)
    emptied = sum(1 for s in result["stores"] if s["removed"])
    events.record("purge", "purged", f"Purged the case: {emptied} stores held it and were emptied", case=case, home=home,
                  default_who=("The purge", "system", "system"))
    return {"stores": emptied, "left": len(result["left"])}


def run_due(data_root: Path, portal_root: Path | None = None, views_log: Path | None = None, access_log: Path | None = None) -> list[str]:
    """Every purge whose day has come (the overnight run's step): returns the cases purged."""
    done = []
    for case in due_now(data_root):
        run(data_root, case, portal_root, views_log, access_log)
        done.append(case)
    return done


def case_of(data_root: Path, purge_id: str) -> str | None:
    """The case a purge's id names (the job carries the id, never the case)."""
    return next((c for c, e in (_purges(data_root)["cases"] or {}).items() if e.get("id") == purge_id), None)


def submit(data_root: Path, case: str, by: str, views_log: Path | None = None, access_log: Path | None = None) -> str:
    with _policy_case(data_root, case):
        return _submit(data_root, case, by, views_log, access_log)


def _submit(data_root: Path, case: str, by: str, views_log: Path | None = None, access_log: Path | None = None) -> str:
    """The purge as a job (src/jobs.py): the job names the purge's id and no case, so its file and its ledger rows keep nothing of the case."""
    import jobs

    rec = entry(data_root, case)
    if not ready(rec):
        raise PurgeError("This purge is not ready: its day has not come, or a second attorney has not confirmed it.")
    _current_authority(data_root, case, rec)
    job = jobs.submit(jobs.folder_for(data_root), "purge", client=None, by=by,
                      args={"purge": rec["id"], "views": str(views_log) if views_log else "", "access": str(access_log) if access_log else ""})
    return job["id"]


def purged(data_root: Path) -> set[str]:
    return {c for c, e in (_purges(data_root)["cases"] or {}).items() if e.get("state") == "done"}


def originals_kept(data_root: Path) -> list[dict[str, Any]]:
    return _read(_home(data_root) / ORIGINALS_FILE, {"originals": []}).get("originals") or []


def _ended(case_dir):
    import engagement
    if (engagement.read(case_dir).get("end") or {}).get("state") not in engagement.ENDED:
        raise PurgeError("Operationally close this case before scheduling destruction; closure does not determine legal representation completion.")


def _current_authority(data_root, case, rec, portal_root=None):
    import client_file_policy
    try:
        current = client_file_policy.require_destruction(Path(data_root) / case, portal_root)
        _ended(Path(data_root) / case)
    except (ValueError, OSError, PermissionError, TypeError, KeyError) as exc:
        raise PurgeError("Current case-specific destruction authority is unavailable or changed. A partial purge requires incident recovery; missing identity or policy is never approval.") from exc
    if not isinstance(rec, dict) or rec.get("policy_authority") != current:
        raise PurgeError("The waiting purge's legal approval is stale or legacy. Cancel it and obtain current case-specific approval before scheduling again.")
    return current


def current_ready(data_root, case):
    rec = entry(data_root, case)
    if not ready(rec):
        return False
    try:
        _current_authority(data_root, case, rec)
        return True
    except (ValueError, OSError, PermissionError, TypeError, KeyError):
        return False


def destruction_status(case_dir, portal_root=None):
    """Authoritative own-case destructive readiness, distinct from planning clocks."""
    import client_file_policy
    d = Path(case_dir)
    holds = []
    try:
        _ended(d)
        client_file_policy.require_destruction(d, portal_root)
    except (ValueError, OSError, PermissionError, TypeError, KeyError) as exc:
        holds.append(str(exc))
    rec = entry(d.parent, d.name)
    recovery_hold = None
    if rec and rec.get("state") == "waiting":
        try:
            _current_authority(d.parent, d.name, rec, portal_root)
        except (ValueError, OSError, PermissionError, TypeError, KeyError):
            recovery_hold = "The waiting operation no longer has current case destruction authority. Cancel and review again; partial destruction requires incident recovery."
    return {"destruction_ready": not holds, "destruction_holds": holds, "recovery_hold": recovery_hold}
