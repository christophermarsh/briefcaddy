"""Reminders to the client about an appointment: the week before and the day before a fingerprint appointment, an interview or a court hearing.

The overnight run calls nightly() once. Each night, for every open case that has a portal client and holds an appointment of those three kinds still to come:

    the day before   the appointment is tomorrow
    the week before  the appointment is two to seven days away and no week reminder was sent for it yet (a notice that arrives late still gets one)

Each goes by the channels the client agreed to, in the client's language (src/portal/notify.py: the outbox's rules, so no provider means the message waits in the
outbox; no consent means none for that channel). A RESTRICTED case (VAWA, T, U, asylum, or one an attorney restricted) gets none at all, whether or not an attorney
switched automatic messages on for it: that switch was made for messages that carry only a link, and a reminder carries the day, the time and the place, so it is
refused here before anything else and the office reaches that client by hand (Reports counts it as "left to the office"). The text says the day, the time, the place
(only when a person entered or checked it: a hearing's court, an address confirmed or typed on the case; else "the address is on your letter"), a sentence telling
the client to check the address against their letter, "bring your notice and your passport" and the office's phone (when Settings holds one). DRAFT for the attorney
and a certified translator (docs/attorney_review.md); the Haitian Creole is a machine draft.

What was done is kept on the case, one entry for each appointment and kind, with the result of each channel, and in the event ledger (one row for each attempt that sent
something or failed, none for a night when nothing was sent and nothing failed):

    data/clients/<case id>/client_reminders.json   {"version": 1, "sent": {"<appointment>|week|day": {"kind", "appointment", "for", "at", "status", "channels": {"email": "sent"}}}}

A second run the same night sends nothing twice, and a channel that already sent is never sent again: when one channel failed (a text service that is down) the next
night tries only that channel. An entry is settled (never tried again) once its status is sent, queued in the outbox, left to the office by hand or held; "failed"
(a channel that failed) and "none" (no channel the client agreed to) are tried again the next night, while the appointment is still within the window; a day that moved
starts the entry again. Only the newest notice of a receipt and a kind counts (a rescheduled appointment's old date is never reminded: src/client_case.py upcoming).
Reports counts the entries for the firm. A case that has ended (src/engagement.py) is left alone.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import clock
import events

FILE = "client_reminders.json"
VERSION = 1
SETTLED = ("sent", "queued", "hand", "held")  # the run has dealt with it; "failed" and "none" are tried again
STATUS_NAMES = {"sent": "Sent", "queued": "Waiting in the outbox (no provider)", "hand": "Left to the office (protected case)", "held": "Held",
                "failed": "Could not be sent", "none": "No channel the client agreed to"}  # Reports, in English


def read(case_dir: Path) -> dict[str, Any]:
    path = Path(case_dir) / FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    return {"version": VERSION, "sent": dict(data.get("sent") or {})}


def _save(case_dir: Path, data: dict[str, Any]) -> None:
    path = Path(case_dir) / FILE
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def kind_of(days: int) -> str | None:
    """"day" for tomorrow, "week" for two to seven days away, else None."""
    return "day" if days == 1 else "week" if 2 <= days <= 7 else None


def channel_results(results: list[dict[str, str]]) -> dict[str, str]:
    """What each channel did, from the notifier's rows: sent, queued (waiting in the outbox: no provider), skipped (no consent), failed; "all" for a whole-message
    answer: hand (a restricted case), held (a conflict check or an ended case) or failed (the case folders were not found)."""
    from portal.notify import CONFLICT, NO_CASES, RESTRICTED

    out = {}
    for r in results:
        result, why = str(r.get("result") or ""), r.get("why")
        out[r.get("channel", "all")] = ("hand" if why == RESTRICTED else "held" if why == CONFLICT else "failed" if why == NO_CASES else "skipped" if result == "skipped"
                                        else "sent" if result == "sent" else "queued" if result.startswith("dry-run") else "failed")
    return out


def status_of(channels: dict[str, str]) -> str:
    """One word for an entry from its channels: hand, held, failed (any channel failed), sent, queued, or none (no channel the client agreed to)."""
    values = set(channels.values())
    for word in ("hand", "held", "failed", "sent", "queued"):
        if word in values:
            return word
    return "none"


def fields(a: dict[str, Any], lang: str, phone: str | None) -> dict[str, str]:
    """The words a reminder fills in, in the client's language. The place goes only when a person entered or checked it (a['where_confirmed']); the client is told in
    every case to check the address against their letter."""
    import client_case
    from portal.notify import APPOINTMENT_WORDS as W

    lang = lang if lang in client_case.LANGS else "en"
    time = client_case.clock_words(a.get("time"), lang)
    where = " ".join(str(a.get("where") or "").split()).rstrip(" .")
    if where and a.get("where_confirmed"):
        place = W["place"][lang].format(where=where) + W["check_court" if a["kind"] == "hearing" else "check"][lang]
    else:
        place = W["noplace"][lang]  # a place read from the notice by a program nobody has checked is not sent
    return {"what": W["what"][a["kind"]][lang], "date": client_case.day_words(a["date"], lang, True),
            "time": W["time"][lang].format(time=time) if time else "", "place": place, "phone": W["phone"][lang].format(phone=phone) if phone else ""}


def nightly(out_root: Path, data_root: Path, today: date | None = None) -> str:
    """The overnight run's client reminders. Returns the report line (never names a case)."""
    import calendar_feed
    import client_case
    import journey
    import restricted
    from portal.notify import Notifier
    from portal.store import PortalStore
    from review.overview import journey_row

    portal_root = Path(data_root) / "portal"
    if not (portal_root / "clients").exists():
        return "Client reminders: no client portal here."
    today = today or clock.today()
    store = PortalStore(portal_root)
    notifier = Notifier(portal_root / "outbox.jsonl", cases_root=out_root, store=store)
    counts: dict[str, int] = {}
    with events.acting("The overnight run", "system", "overnight", everywhere=True):
        for case in sorted(p for p in Path(out_root).iterdir() if (p / "fact_graph.json").exists() and calendar_feed.is_open(p)):
            try:
                closed = restricted.is_restricted(case)  # first of all: a restricted case is never sent a reminder, whatever its messages switch says
                profile = None
                if not closed:
                    try:
                        profile = store.profile(case.name)
                    except LookupError:
                        continue  # no portal for this client: nothing to send to
                row = journey_row(case) or {}
                near = [d for d in row.get("deadlines") or [] if (d.get("appt") or {}).get("kind") in ("biometrics", "interview", "hearing")
                        and kind_of((date.fromisoformat(str(d["date"])[:10]) - today).days)]
                if not near:
                    continue  # nothing within the week: the full timeline is not worked out for this case
                j = journey.journey(case, today)
                due = [(a, kind_of((date.fromisoformat(a["date"]) - today).days)) for a in client_case.upcoming(j)]
                due = [(a, k) for a, k in due if k]
                if not due:
                    continue
                state = read(case)
                phone = j.get("office_phone")
                changed = False
                for a, kind in due:
                    key = f"{a['id']}|{kind}"
                    last = state["sent"].get(key) or {}
                    if last.get("for") != a["date"]:
                        last = {}  # the appointment's day moved: this is a new reminder
                    channels = dict(last.get("channels") or {})
                    if channels and status_of(channels) in SETTLED:
                        continue
                    if closed:
                        channels = {"all": "hand"}  # the office reaches this client by hand: settled, nothing sent
                        sent_now, failed_now = False, False
                    else:
                        done = {c for c, r in channels.items() if r in ("sent", "queued")}  # a channel that sent is never sent again
                        results = channel_results(notifier.send(profile, f"appointment_{kind}", "", skip_channels=done, **fields(a, profile.get("language", "pt"), phone)))
                        channels |= {c: r for c, r in results.items() if c not in done}
                        sent_now = any(r in ("sent", "queued") for r in results.values())
                        failed_now = any(r == "failed" for r in results.values())
                    status = status_of(channels)
                    state["sent"][key] = {"kind": kind, "appointment": a["kind"], "for": a["date"], "at": clock.stamp(), "status": status, "channels": channels}
                    changed = True
                    counts[status] = counts.get(status, 0) + 1
                    if sent_now:
                        store.log(case.name, "appointment_reminded", {"kind": kind, "status": status})  # one row for each attempt that sent something, in the portal's log and the ledger
                    if failed_now:
                        events.record("portal", "failed", "Tried to send an appointment reminder: a channel could not be sent", case_dir=case)
                if changed:
                    _save(case, state)
            except Exception:  # noqa: BLE001 -- one broken case must not stop the others
                counts["error"] = counts.get("error", 0) + 1
    if not counts:
        return "Client reminders: nothing to send."
    worded = [f"{counts[s]} {n}" for s, n in (("sent", "sent"), ("queued", "waiting in the outbox (no provider)"), ("hand", "left to the office (protected case)"),
                                               ("held", "held"), ("none", "with no channel the client agreed to"), ("failed", "could not be sent"),
                                               ("error", "cases could not be read")) if counts.get(s)]
    return "Client reminders: " + ", ".join(worded) + "."


def summary(rows: list[dict[str, Any]], data_root: Path, today: date | None = None, days: int = 60) -> list[dict[str, Any]]:
    """Reports: for each kind of reminder (the week before, the day before) how many were sent, waiting in the outbox, left to the office by hand, held, not sent
    and tried again. Only the cases in rows (the cases the reader may open); an appointment older than `days` is history. Counts only: no case is named."""
    today = today or clock.today()
    table: dict[str, dict[str, int]] = {"week": {}, "day": {}}
    for row in rows:
        for entry in (row["sent"] if row.get("sent") is not None else read(Path(data_root) / row["id"])["sent"]).values():  # (a row may carry them already: the review app's lists do)
            when = client_day(entry.get("for"))
            if when is None or (today - when).days > days or entry.get("kind") not in table:
                continue
            table[entry["kind"]][entry.get("status", "none")] = table[entry["kind"]].get(entry.get("status", "none"), 0) + 1
    out = []
    for kind, name in (("week", "The week before"), ("day", "The day before")):
        c = table[kind]
        out.append({"reminder": name, "total": sum(c.values()), **{s: c.get(s, 0) for s in ("sent", "queued", "hand", "held", "none", "failed")}})
    return out


def client_day(iso: Any) -> date | None:
    try:
        return date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None
