"""Every client at a glance: where each case stands, from invitation to
filing, for the firm's all-clients dashboard.

A client's row comes from its review bundle (data/clients/<id>) and, when
the client was invited through the portal, from the portal's own folder
(data/portal/clients/<id>). Building a client's review items takes a
fraction of a second; for 1,800 clients that is minutes, so each row is
cached in the bundle (overview.json) and rebuilt only when one of the
files it was built from has changed.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .state import _read, build_items, load_decisions
import clock
import events
import schema_path

# The order a case moves through; the dashboard counts and filters by it.
STAGES = ["not_invited", "invited", "answering", "processing", "review", "attorney", "ready", "filed"]
STAGE_NAMES = {"not_invited": "Not invited yet", "invited": "Invited", "answering": "Answering questions", "processing": "Being processed", "review": "Paralegal review",
               "attorney": "With the attorney", "ready": "Ready to file", "filed": "Filed"}
SOURCES = ("fact_graph.json", "fact_graph_raw.json", "decisions.json", "meta.json", "evidence.json", "status.json", "office.json")
ROW_VERSION = 5.2  # 5.2 refreshes portal question labels; 5.1 added the foreign-country check.


def _signature(client_dir: Path) -> list[float]:
    from rules import firm_policies

    # the firm's edits to its policies (Settings) change what a case's cards say: the last edit's time is part of every row's signature
    return [ROW_VERSION] + [(client_dir / name).stat().st_mtime if (client_dir / name).exists() else 0.0 for name in SOURCES] + [firm_policies.stamp()[1] / 1e9]


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, clock.zone()).isoformat() if ts else None


def review_row(client_dir: Path, field_map: dict, template: str | Path, catalog) -> dict[str, Any]:
    """The review side of one client's row (cached in overview.json)."""
    signature = _signature(client_dir)
    cached = _read(client_dir / "overview.json", None)
    if cached and cached.get("signature") == signature:
        return cached["row"]
    data = build_items(client_dir, field_map, template, catalog, pending=False)
    cards = data["cards"]
    count = lambda tab: sum(1 for c in cards if c["tab"] == tab)  # noqa: E731
    decisions = load_decisions(client_dir)
    status = _read(client_dir / "status.json", {})
    open_items = sum(len(c["item_ids"]) for c in cards)
    row = {
        "id": client_dir.name, "summary": data.get("summary") or {},
        "blocking": sum(1 for c in cards if c["level"] == "blocking"),
        "fix": count("fix"), "check": count("check"), "attorney": count("attorney"),
        "open_items": open_items, "decided": len(decisions),
        "names_open": [c["title"] for c in cards if c["kind"] in ("names", "names_uscis")],  # the name cards hold the packet (src/name_events.py)
        "last_decision": max((d.get("at") for d in decisions.values()), key=clock.key, default=None),
        "processed_at": _iso((client_dir / "meta.json").stat().st_mtime) if (client_dir / "meta.json").exists() else None,
        "filed_at": status.get("filed_at"),
    }
    try:
        (client_dir / "overview.json").write_text(json.dumps({"signature": signature, "row": row}, default=str), encoding="utf-8")
    except OSError:
        pass  # a read-only copy still shows the row, just uncached
    return row


_JOURNEY_INPUTS = (schema_path.path("register", "journey"), schema_path.path("cover_letter", "i485"), schema_path.path("firm", "firm_profile"))  # the stage depends on these too (Visa Bulletin, the firm's name)


def journey_row(client_dir: Path, today: str | None = None) -> dict[str, Any] | None:
    """Where the case stands with USCIS (src/journey.py), for the dashboard.
    Cached in journey_summary.json against the bundle's files, the settings
    it reads and the date (deadlines move with the calendar) -- the overnight
    run warms it, so the morning's first look is instant."""
    import expiry
    import journey
    import settings

    today = today or clock.today().isoformat()
    signature = _signature(client_dir) + [max((p.stat().st_mtime for p in client_dir.glob("packet*.json")), default=0.0)] \
        + [n.stat().st_mtime if n.exists() else 0.0 for n in _JOURNEY_INPUTS] + [today, settings.mtime()] \
        + [Path(journey.__file__).stat().st_mtime] \
        + [(client_dir / "case_status.json").stat().st_mtime if (client_dir / "case_status.json").exists() else 0.0]  # USCIS's status, checked nightly (src/case_status.py)
    import deadlines_set

    signature += [deadlines_set.mtime(client_dir), Path(deadlines_set.__file__).stat().st_mtime]  # the deadlines a person set and who is responsible (src/deadlines_set.py)
    signature += [(client_dir / "documents.json").stat().st_mtime if (client_dir / "documents.json").exists() else 0.0,  # the expiring documents (src/expiry.py)
                  (schema_path.path("law", "expiry_rules")).stat().st_mtime, Path(expiry.__file__).stat().st_mtime]
    cached = _read(client_dir / "journey_summary.json", None)
    if cached and cached.get("signature") == signature:
        return cached["row"]
    row = journey.summary(journey.journey(client_dir, date.fromisoformat(today[:10])))
    try:
        (client_dir / "journey_summary.json").write_text(json.dumps({"signature": signature, "row": row}, default=str), encoding="utf-8")
    except OSError:
        pass
    return row


def name_and_kind(d: Path) -> tuple[str | None, str | None]:
    """The client's name and kind of case for the "Choose a client" list, from the rows the dashboard already cached in the case folder (overview.json,
    journey_summary.json): nothing is rebuilt for 1,800 cases. None until the dashboard has built the row."""
    from review.front_desk import TRACKS

    built = (_read(d / "overview.json", {}) or {}).get("row")
    name = ((built or {}).get("summary") or {}).get("name")
    row = (_read(d / "journey_summary.json", {}) or {}).get("row") or {}
    if built:  # Explicit confirmed corrections also override a previously cached name.
        from review.state import display_name

        name = display_name(d, name)
    return name, row.get("track_name") or dict(TRACKS).get(row.get("track"))


def stage_of(row: dict[str, Any]) -> str:
    """Where the firm's work on the case is. "Filed" lasts only while nothing else is due now: once USCIS
    approves and the next filing is due (the I-485 after the I-360, say), the case is back in the work list."""
    due_now = any(f.get("now") for f in ((row.get("journey") or {}).get("next_filings") or []))
    if row.get("filed_at") and not due_now:
        return "filed"
    if not row.get("processed_at"):
        portal = row.get("portal_status")
        sent = (row.get("last_invite") or {}).get("status") in ("sent", "queued") if isinstance(row.get("last_invite"), dict) else False  # a send that went nowhere ("none", "failed", "hand") is not an invitation
        if portal == "invited" and not (row.get("invited_at") or sent or row.get("reminded_at") or row.get("questionnaire_link_created_at")):
            return "not_invited"  # added to the portal, nothing sent yet: not "Invited" (buyer visit 4)
        return "invited" if portal in (None, "invited") else "processing" if portal == "submitted" else "answering"
    if row.get("fix") or row.get("check"):
        return "review"
    if row.get("attorney") or row.get("blocking"):
        return "attorney"
    return "ready"


def _answered(folder: Path, profile: dict[str, Any]) -> dict[str, int] | None:
    from portal.bank import bank_for
    from review.answers_page import progress

    answers = _read(folder / "answers.json", {})
    try:
        return progress(answers, bank_for(profile)) if answers else {"answered": 0, "total": 0}
    except Exception:  # noqa: BLE001 -- a progress count must never hide the client's row
        return None


def _new_photos(portal_folder: Path) -> list[dict[str, Any]]:
    """What this client sent that nobody has read yet (portal/engine.arrived): a retake or a new document. My work says "Photos that
    arrived" until the reader has run on it."""
    from portal.engine import arrived

    return [{"document": a["document"], "at": a["at"], "retake": a["retake"], "reading": a["reading"]} for a in arrived(_read(portal_folder / "uploads.json", []))]


def case_row(client_dir: Path, field_map: dict, template: str | Path, catalog) -> dict[str, Any]:
    """One case's row on All clients: its review side (review_row), its timeline (journey_row) and its packet. A bundle that cannot be read is a row that says so
    (one broken case must not hide the other 1,799)."""
    name = client_dir.name
    try:
        row = review_row(client_dir, field_map, template, catalog)
    except Exception as exc:  # noqa: BLE001 -- one broken bundle must not hide the other 1,799
        row = {"id": name, "error": type(exc).__name__}
    try:
        row["journey"] = journey_row(client_dir)
    except Exception as exc:  # noqa: BLE001 -- shown on the dashboard, never hidden
        row["journey"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    built = _read(client_dir / "packet.json", None)  # src/packet.py; outside the cache, it changes on its own
    if built:
        row["packet"] = {"built_at": built.get("built_at"), "draft": built.get("draft"), "pages": built.get("pages")}
    return row


def add_portal(rows: dict[str, dict[str, Any]], folder: Path) -> None:
    """What the client's portal folder adds to the client's row (or makes it, for a client with no case yet): the portal's status, the language, who invited
    them and when, the messages and photos waiting, how far along the answers are."""
    profile = _read(folder / "profile.json", {})
    events = folder / "events.jsonl"
    row = rows.setdefault(folder.name, {"id": folder.name, "summary": {"name": profile.get("name")}})
    row["portal_status"] = profile.get("status")
    row["portal_activity"] = _iso(events.stat().st_mtime) if events.exists() else None
    row["language"] = profile.get("language")
    row["filing"] = profile.get("filing") or "i485"  # the questionnaire the client answers (review/front_desk.py)
    row["invited_at"], row["last_invite"] = profile.get("invited_at"), profile.get("last_invite")
    permission = _read(folder / "communication_consent.json", {})
    handovers = [event for event in permission.get("history", []) if isinstance(event, dict)
                 and event.get("action") == "questionnaire_access" and event.get("evidence") == permission.get("questionnaire_access")]
    row["questionnaire_link_created_at"] = handovers[-1].get("at") if handovers else None
    row["reminded_at"] = profile.get("last_reminder_at")
    row["messages_waiting"] = [{"id": m["id"], "at": m["at"], "text": m["text"][:160]} for m in _read(folder / "messages.json", [])
                               if m["from"] == "client" and m.get("status") == "new"]  # the client wrote; nobody has answered yet
    row["retakes"] = [{"task": t["id"], "document": t.get("doc_en") or t.get("doc_id"), "why": t.get("why_en") or "the photo could not be read",
                       "asked_at": t.get("asked_at")} for t in _read(folder / "tasks.json", []) if t.get("kind") == "retake" and not t.get("received_at")]  # a new photo in is "arrived", not "waiting"
    row["new_photos"] = _new_photos(folder)
    import client_case

    row["answers_week"] = client_case.answers_waiting(folder)  # answers with no review card behind them that nobody has marked done for seven days
    if profile.get("status") != "submitted":  # how far along a client still answering is
        row["answers_so_far"] = _answered(folder, profile)
    if not (row.get("summary") or {}).get("name"):
        row.setdefault("summary", {})["name"] = profile.get("name")


def fill_name(row: dict[str, Any], data_root: Path) -> None:
    """A case made before anyone's name was read (documents added to a new client): never the folder's id as its name."""
    from review.state import display_name

    if "error" not in row and (data_root / row["id"] / "fact_graph.json").exists():
        row.setdefault("summary", {})["name"] = display_name(data_root / row["id"], (row.get("summary") or {}).get("name"))


def settle(row: dict[str, Any], now=None) -> None:
    """The row's stage, and when anything last happened on it and how many days ago (the office's clock)."""
    now = now or clock.now()
    row["stage"] = stage_of(row)
    last = max((x for x in (row.get("last_decision"), row.get("processed_at"), row.get("portal_activity")) if x), key=clock.key, default=None)
    row["last_activity"] = last
    row["idle_days"] = (now - clock.parse(last)).days if last else None


def overview(data_root: Path, field_map: dict, template: str | Path, catalog, portal_root: Path | None = None) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    for client_dir in sorted(p for p in data_root.iterdir() if (p / "fact_graph.json").exists()):
        rows[client_dir.name] = case_row(client_dir, field_map, template, catalog)
    if portal_root and (portal_root / "clients").exists():
        for folder in sorted(p for p in (portal_root / "clients").iterdir() if (p / "profile.json").exists()):
            add_portal(rows, folder)
    for row in rows.values():
        fill_name(row, data_root)
    now = clock.now()
    for row in rows.values():
        settle(row, now)
    counts = {s: sum(1 for r in rows.values() if r["stage"] == s) for s in STAGES}
    return {"stages": [{"id": s, "name": STAGE_NAMES[s], "count": counts[s]} for s in STAGES], "clients": list(rows.values()),
            "deadlines": deadlines(rows.values())}


def my_work(rows, owner: str | None = None, horizon_days: int = 30, person: str | None = None) -> dict[str, Any]:
    """One person's list across every case, most pressing first: late and
    urgent deadlines, the review cards waiting for them, then each case's
    open next steps (src/journey.py). owner: "attorney", "paralegal" or None (everything).
    person: the signed-in person's email: a deadline someone else is responsible for (src/deadlines_set.py) is not on their list,
    and one they are responsible for is, whatever its owner."""
    rows = list(rows)
    due = [d for d in deadlines(rows, horizon_days)
           if (person and d.get("who") == person)
           or ((owner is None or d["owner"] == owner or (owner == "paralegal" and d["owner"] == "client")) and not (person and d.get("who") and d["who"] != person))]
    review = []
    for row in rows:
        name = (row.get("summary") or {}).get("name") or row["id"]
        if owner in (None, "paralegal") and (row.get("fix") or row.get("check")):
            review.append({"client": row["id"], "name": name, "owner": "paralegal", "count": (row.get("fix") or 0) + (row.get("check") or 0),
                           "text": f"{(row.get('fix') or 0) + (row.get('check') or 0)} review card(s) to fix or check"})
        if owner in (None, "attorney") and (row.get("attorney") or row.get("blocking")):
            review.append({"client": row["id"], "name": name, "owner": "attorney", "count": row.get("attorney") or 0, "blocking": row.get("blocking") or 0,
                           "text": f"{row.get('attorney') or 0} sign-off(s)" + (f", {row['blocking']} blocking" if row.get("blocking") else "")})
    messages, waiting, photos, week = [], [], [], []
    for row in rows:
        name = (row.get("summary") or {}).get("name") or row["id"]
        if owner in (None, "paralegal"):
            messages += [m | {"client": row["id"], "name": name, "language": row.get("language")} for m in row.get("messages_waiting") or []]
            waiting += [r | {"client": row["id"], "name": name} for r in row.get("retakes") or []]
            photos += [p | {"client": row["id"], "name": name} for p in row.get("new_photos") or []]
            week += [a | {"client": row["id"], "name": name} for a in row.get("answers_week") or []]  # "Answers waiting a week": a person looks
    week.sort(key=lambda a: -a["days"])  # the longest waiting first
    messages.sort(key=lambda m: clock.key(m["at"]))  # the oldest unanswered first
    steps = []
    for row in rows:
        j = row.get("journey") or {}
        for st in j.get("steps") or []:
            if owner is None or st["owner"] == owner:
                steps.append(st | {"client": row["id"], "name": (row.get("summary") or {}).get("name") or row["id"], "stage_name": j.get("stage_name")})
    steps.sort(key=lambda s: (not s["urgent"], s["name"] or ""))
    review.sort(key=lambda r: (-(r.get("blocking") or 0), -r["count"]))
    return {"owner": owner, "deadlines": due, "review": review, "steps": steps, "messages": messages, "retakes": waiting, "new_photos": photos, "answers_week": week,
            "counts": {"late": sum(1 for d in due if d["level"] == "overdue"), "urgent": sum(1 for d in due if d["level"] == "urgent") + sum(1 for s in steps if s["urgent"]),
                       "review": len(review), "steps": len(steps), "messages": len(messages), "retakes": len(waiting), "new_photos": len(photos), "answers_week": len(week)}}


PASSED_DAYS = 60  # a deadline this long past is history, not an alarm (journey.py's "next deadline" looks back the same 60 days)
# Deadline kinds (journey.py ids) that are history once missed: nothing is left to do on the clock itself, so a long-past one is
# shown grey. This is a DISPLAY rule, not a legal one (a late filing may still be argued): the attorney decides what a missed
# deadline means. Everything else that is past stays red because it is still actionable: answers to RFEs and NOIDs, work permit,
# I-751, I-90, T status end, DACA expiry and renewal windows, U Supplement B, AR-11 and EOIR-33, the age-21 and state-order deadlines.
HISTORY_IDS = {"one_year", "vawa_deadline", "i730"}  # the asylum one-year bar, the VAWA and I-730 filing deadlines
HISTORY_SUFFIXES = (".appeal", ".reopen", ".reconsider")  # BIA appeal, motions to reopen or reconsider after a court decision
HISTORY_MARKS = (".denial.",)  # a USCIS denial's N-336 hearing request or I-290B motion or appeal (id "<receipt>.denial.<date>")


def history_once_missed(deadline_id: str) -> bool:
    return deadline_id in HISTORY_IDS or deadline_id.endswith(HISTORY_SUFFIXES) or any(m in deadline_id for m in HISTORY_MARKS)


def deadlines(rows, horizon_days: int = 60) -> list[dict[str, Any]]:
    """Every case's deadlines, soonest first: overdue ones, then the next
    horizon_days -- the paralegal's "what's due" list across all clients."""
    import journey

    cfg = journey.settings()["deadlines"]
    today = clock.today()  # "late" and "passed" are the office's dates (src/clock.py)
    out = []
    for row in rows:
        for d in ((row.get("journey") or {}).get("deadlines") or []):
            days = (date.fromisoformat(d["date"][:10]) - today).days
            if d["owner"] == "client" and days < 0:
                continue  # an appointment that has passed is history, not a deadline
            if days <= horizon_days:
                out.append(d | {"client": row["id"], "name": (row.get("summary") or {}).get("name"), "days_left": days,
                                "level": "passed" if days < -PASSED_DAYS and history_once_missed(d["id"]) else journey._level(days, cfg)})
    return sorted(out, key=lambda d: d["date"])


def mark_filed(client_dir: Path, filed: bool, who: str) -> dict[str, Any]:
    """Records (or clears) that the client's packet was filed -- the last stage."""
    status = _read(client_dir / "status.json", {})
    if filed:
        status.update(filed_at=clock.stamp(), filed_by=who)
    else:
        status.pop("filed_at", None)
        status.pop("filed_by", None)
    (client_dir / "status.json").write_text(json.dumps(status, indent=1), encoding="utf-8")
    events.record("filings", "marked_filed" if filed else "unmarked_filed", "Marked the case as filed" if filed else "Took the filed mark off the case", case_dir=client_dir, who=who)
    return status
