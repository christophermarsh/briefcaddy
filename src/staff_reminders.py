"""E-mail reminders to staff about a deadline: the week before and the morning of.

A reminder goes to the person responsible for a deadline (src/deadlines_set.py: named on a deadline a person set, or assigned on one the product
worked out) and only if that person turned reminders on, for themselves, on Settings, My calendar. Off by default: consent is each staff
member's own. A deadline nobody is responsible for sends nothing (the product does not guess who).

    data/staff_reminders.json     {"version": 1, "consent": {email: {"on", "at"}}, "sent": {"<email>|<kind>|<day>": "YYYY-MM-DD"}}

One e-mail per person and kind (the week before, the morning of) per day, listing every deadline of that kind, in plain words. A restricted case
the person may not open is left out of it altogether (the calendar feed's rule: src/calendar_feed.py); a case they may open is named.
The e-mail goes through the same mail server or outbox as every other message (src/portal/notify.py staff_email: with no mail server it waits in
the outbox, said in the run's report line). The overnight run calls nightly() once; a second run the same night sends nothing twice.

The reminders are for the morning that is coming: a run after noon is working for tomorrow's morning, one before noon for today's.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import clock
import events

FILE = "staff_reminders.json"
VERSION = 1
KEEP_DAYS = 45


def _path(data_root: Path) -> Path:
    return Path(data_root) / FILE


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    return {"version": VERSION, "consent": dict(data.get("consent") or {}), "sent": dict(data.get("sent") or {})}


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def wants(path: str | Path, email: str) -> bool:
    return bool((_load(Path(path))["consent"].get(str(email).strip().lower()) or {}).get("on"))


def set_consent(path: str | Path, email: str, on: bool, name: str = "") -> None:
    """A staff member's own choice. Off until they switch it on; every change is one row in the ledger (the sentence says who, never the address)."""
    path, email = Path(path), str(email or "").strip().lower()
    data = _load(path)
    data["consent"][email] = {"on": bool(on), "at": clock.stamp()}
    _save(path, data)
    events.record("accounts", "reminders_on" if on else "reminders_off", f"{name or 'A staff member'} turned deadline reminders {'on' if on else 'off'} for themselves",
                  home=path.parent, who=name or None)


def target_day(now=None) -> date:
    """The morning the run is working for: today before noon (the firm's clock), tomorrow after."""
    now = now or clock.now()
    return now.date() if now.hour < 12 else now.date() + timedelta(days=1)


def _line(d: dict[str, Any], name: str) -> str:
    what = " ".join(str(d.get("what") or "").split())
    what = what if len(what) <= 150 else what[:147].rstrip() + "..."
    return f"{what} ({name})"


def nightly(out_root: Path, data_root: Path, now=None) -> str:
    """The overnight run's reminders. Returns the report line (never names a case)."""
    import restricted
    from portal.notify import Notifier, firm_name
    from review.auth import Accounts
    from review.overview import journey_row

    accounts_path = Path(data_root) / "review_users.json"
    if not accounts_path.exists():
        return "Staff reminders: no staff accounts here."
    users = {u["email"]: u for u in Accounts(accounts_path).users() if u.get("active")}
    path = _path(data_root)
    state = _load(path)
    people = {e: users[e] for e, c in state["consent"].items() if c.get("on") and e in users}
    if not people:
        return "Staff reminders: nobody has turned them on."
    target = target_day(now)
    week = target + timedelta(days=7)
    # what is due that day or that week, per person responsible: {(email, kind): [(case id, deadline)]}
    due: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = {}
    from calendar_feed import is_open
    from review import roster

    copy = roster.saved(out_root)  # the timelines the overnight run has just worked out and saved for every case: not read again, one case's files at a time

    def cases():
        if copy is not None:
            for case, e in sorted(copy.items()):
                if e.get("has_case") and e.get("row") and not e["row"].get("end"):  # a case that has ended sends no reminder
                    yield Path(out_root) / case, e["row"].get("journey")
            return
        for d in sorted(p for p in Path(out_root).iterdir() if (p / "fact_graph.json").exists() and is_open(p)):
            try:
                yield d, journey_row(d)
            except Exception:  # noqa: BLE001 -- one broken case must not stop the others
                continue

    for d, row in cases():
        for item in (row or {}).get("deadlines") or []:
            who = item.get("who")
            if who not in people:
                continue
            day = str(item.get("date") or "")[:10]
            kind = "today" if day == target.isoformat() else "week" if day == week.isoformat() else None
            if kind and (copy is None or is_open(d)):  # a copy saved a few minutes ago may not know a case ended since: asked of the folder, only for the few with something due
                due.setdefault((who, kind), []).append((d.name, item))
    # the tasks no case timeline holds (src/case_notes.py): an open prospect's, and a client's who has no case file yet; each is named as the lists name it
    names: dict[str, str] = {}
    try:
        import case_notes
        import journey
        import prospects

        cfg = journey.settings()["deadlines"]
        extra = case_notes.unprocessed_rows(out_root, Path(data_root) / "portal", target, cfg, lambda d: True)
        extra += prospects.work_rows(out_root, target, cfg, lambda d: True) if prospects.folder(out_root).is_dir() else []
    except Exception:  # noqa: BLE001 -- the cases' reminders go out without them
        extra = []
    for row in extra:
        names[row["id"]] = str((row.get("summary") or {}).get("name") or row["id"])
        for item in (row.get("journey") or {}).get("deadlines") or []:
            who = item.get("who")
            day = str(item.get("date") or "")[:10]
            kind = "today" if day == target.isoformat() else "week" if day == week.isoformat() else None
            if who in people and kind:
                due.setdefault((who, kind), []).append((row["id"], item))
    sent, queued, failed, count = 0, 0, 0, 0
    notifier = Notifier(Path(data_root) / "portal" / "outbox.jsonl", cases_root=out_root)
    firm = firm_name()
    for (email, kind), items in sorted(due.items()):
        key = f"{email}|{kind}|{target.isoformat()}"
        if key in state["sent"]:
            continue
        person = people[email]
        lines = []
        for case, item in sorted(items, key=lambda x: (x[1].get("date") or "", x[0])):
            who = {"email": email, "role": person.get("role")}
            folder = (prospects_folder(out_root) / case[len("prospect:"):]) if case.startswith("prospect:") else Path(out_root) / case
            if not restricted.visible_to(who, folder) or (
                    (item.get("expiry") or {}).get("confidential") and not restricted.sees_confidential(who, folder)):
                continue  # a case this person may not open is left out, as on the calendar feed and every list: nothing says it exists
            lines.append("- " + _line(item, names.get(case) or _client_name(Path(out_root) / case)))
        if not lines:
            continue
        day_text = target.strftime("%m/%d/%Y") if kind == "today" else week.strftime("%m/%d/%Y")
        subject = f"{firm}: your deadlines today ({day_text})" if kind == "today" else f"{firm}: your deadlines in a week ({day_text})"
        body = (f"Hello {person.get('name') or ''},\n\n".replace(" ,", ",")
                + ("These deadlines are due today:" if kind == "today" else f"These deadlines are due in a week, on {day_text}:") + "\n\n"
                + "\n".join(lines) + "\n\nYou are named as the person responsible. Open the review app to see each case. "
                "You can turn these reminders off under Settings, My calendar.\n")
        try:
            result = notifier.staff_email(email, subject, body)
        except Exception:  # noqa: BLE001 -- one failed e-mail must not stop the run
            failed += 1
            continue
        state["sent"][key] = target.isoformat()
        count += len(lines)
        if result == "sent":
            sent += 1
        else:
            queued += 1
    cutoff = (target - timedelta(days=KEEP_DAYS)).isoformat()
    state["sent"] = {k: v for k, v in state["sent"].items() if v >= cutoff}
    _save(path, state)
    if not (sent or queued or failed):
        return "Staff reminders: nothing to send."
    return (f"Staff reminders: {sent + queued} e-mail(s) for {count} deadline(s)" + (f", {queued} waiting in the outbox (no mail server)" if queued else "")
            + (f", {failed} could not be sent" if failed else "") + ".")


def prospects_folder(out_root: Path) -> Path:
    import prospects

    return prospects.folder(out_root)


def _client_name(case_dir: Path) -> str:
    """The client's name as the case shows it (a case with no name read yet: its folder name)."""
    try:
        from factgraph import FactGraph
        from review.state import case_summary

        return str(case_summary(FactGraph.load(case_dir / "fact_graph.json")).get("name") or case_dir.name).title()
    except Exception:  # noqa: BLE001
        return case_dir.name
