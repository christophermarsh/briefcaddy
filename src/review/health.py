"""Attorney health summary. Reads kept status; never runs a check or repair.

No case identifiers, log contents, posture command output, or ledger problem
details are returned. A timestamp describes an observation, not a live guarantee.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import clock


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("status must be an object")
    return value


def _mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def summary(app) -> dict:
    import backups
    import events
    import ledger_seal
    import maintenance
    import posture
    import version

    now = clock.utcnow()
    home = app.firm_data
    signals = []

    def add(key, title, state, note, at=None, max_hours=None):
        try:
            parsed = clock.parse(at) if at else None
        except (ValueError, TypeError):
            parsed = None
        if (at and parsed is None) or (max_hours and state == "healthy" and not parsed):
            state = state if state == "failed" else "unavailable"
            note += " The recorded observation time is missing or cannot be read."
        elif parsed and parsed > now:
            state = state if state == "failed" else "unavailable"
            note += " The observation time is in the future; ask IT to check the clock."
        elif state == "healthy" and max_hours and parsed and (now - parsed).total_seconds() > max_hours * 3600:
            state = "stale"
            note += " This observation is older than the page's freshness allowance."
        signals.append({"id": key, "title": title, "status": state, "at": parsed.isoformat() if parsed else None, "note": note})

    roster = getattr(app, "roster", None)
    add("warm", "Lists after startup", "healthy" if roster and roster.ready else "unavailable",
        "The roster is loaded; other caches are not independently observed here." if roster and roster.ready else "The roster has not finished loading.")
    try:
        data = _read(Path(roster.path)) if roster else {}
        if roster and not roster._ours(data):
            raise ValueError("roster belongs to another configuration")
        saved = data.get("saved")
        add("roster", "Saved roster", "healthy" if saved else "never-run", "Last saved list observation; a loaded list may be newer.", saved, 24)
    except (OSError, ValueError, TypeError):
        add("roster", "Saved roster", "never-run" if roster and not Path(roster.path).exists() else "unavailable", "The saved list record could not be read.")
    walked = getattr(roster, "last_walk", 0) if roster else 0
    if walked:
        from datetime import timedelta
        at = (now - timedelta(seconds=max(0, time.monotonic() - walked))).isoformat()
        add("follower", "Roster follower's last walk", "healthy", "This process's last roster reconciliation, not a check of every other index.", at, 24)
    else:
        add("follower", "Roster follower's last walk", "never-run", "No roster walk is observed in this process.")
    try:
        log_path = Path(os.environ.get("I485_BACKUP_LOG") or home / "backup_log.json")
        log = _read(log_path)
        for key, title, record_key, days in (("backup", "Last backup", "last_backup", backups.BACKUP_MAX_DAYS),
                                             ("restore", "Last test restore", "last_test_restore", backups.RESTORE_MAX_DAYS),
                                             ("drill", "Restore drill", "last_restore_drill", backups.DRILL_MAX_DAYS)):
            record = log.get(record_key) or {}
            if not isinstance(record, dict):
                raise ValueError("invalid backup record")
            at = record.get("at")
            day = clock.local_date(at) if at else None
            passed = record.get("complete") is True if key == "backup" else record.get("ok") is True
            state = "never-run" if not record else "unavailable" if day is None else "failed" if not passed else "overdue" if (clock.today() - day).days > days else "healthy"
            add(key, title, state, "Recorded completion status; use the operator recovery procedure for details.", at)
        stopped = log.get("last_restore_drill_stopped") or {}
        last = log.get("last_restore_drill") or {}
        if not isinstance(stopped, dict) or not isinstance(last, dict):
            raise ValueError("invalid drill record")
        if stopped.get("at") and clock.key(stopped["at"]) > clock.key(last.get("at")):
            add("drill_attempt", "Latest restore drill attempt", "failed", "The latest attempt stopped before completion.", stopped.get("at"))
    except (OSError, ValueError, TypeError, KeyError):
        for key, title in (("backup", "Last backup"), ("restore", "Last test restore"), ("drill", "Restore drill")):
            if not any(s["id"] == key for s in signals):
                add(key, title, "never-run" if not log_path.exists() else "unavailable", "No readable backup status is available.")
        if not any(s["id"] == "drill_attempt" for s in signals):
            add("drill_attempt", "Latest restore drill attempt", "unavailable", "The latest attempt cannot be established from this record.")
    try:
        # Existing maintenance.status has module-global nested sources. Refuse
        # rather than read a different firm's defaults from an alternate home.
        if home.resolve() != (maintenance.REPO / "data").resolve() or maintenance.FIRM_LOG.resolve().parent != home.resolve() or maintenance.LAST_LIVE.resolve().parent != home.resolve() or backups.LOG.resolve().parent != home.resolve():
            raise ValueError("maintenance sources are not scoped to this installation")
        due = sum(bool(row["due"]) for row in maintenance.status())
        add("maintenance", "Maintenance register", "overdue" if due else "healthy", f"{due} register items are due. Open Keeping current for the recorded responsibilities.")
    except (OSError, ValueError, TypeError, KeyError):
        add("maintenance", "Maintenance register", "unavailable", "The maintenance register could not be read.")
    try:
        path = posture.path_for(home)
        kept = posture.read(path)
        checks = (kept or {}).get("checks") or []
        if any(not isinstance(c, dict) for c in checks):
            raise ValueError("invalid duty record")
        complete = len(checks) == len(posture.DUTIES) and {c.get("id") for c in checks} == set(posture.DUTIES)
        state = ("unavailable" if path.exists() else "never-run") if not kept else "failed" if any(c.get("result") == "off" for c in checks) else "unavailable" if not complete or any(c.get("result") != "on" for c in checks) else "healthy"
        add("posture", "This computer", state, "Stored machine-duty checks only. Open This computer for individual duties and IT actions.", (kept or {}).get("at"), 36)
    except (OSError, ValueError, TypeError):
        add("posture", "This computer", "unavailable", "The stored machine checks could not be read.")
    try:
        base = events.base_path(home)
        kept = ledger_seal.kept(base)
        anchors = ledger_seal.read_anchors(base)
        anchor_path = ledger_seal.anchors_path(base)
        if anchor_path.exists():
            with anchor_path.open(encoding="utf-8") as stream:
                rows = [json.loads(line) for line in stream]
            if any(not isinstance(a, dict) or not isinstance(a.get("day"), str) or not isinstance(a.get("hash"), str) or not isinstance(a.get("rows"), int) for a in rows):
                raise ValueError("invalid anchor record")
        add("ledger", "Ledger check and anchor", ("unavailable" if ledger_seal.check_path(base).exists() else "never-run") if not kept else "failed" if kept.get("ok") is not True else "unavailable" if not anchors else "healthy",
            "A stored check and daily anchor are both required; this page does not reverify the chain.", (kept or {}).get("at"), 36)
        if anchors:
            add("anchor", "Last recorded anchor", "healthy", "Date of the latest recorded daily anchor; retain the seal separately.", str(anchors[-1]["day"]) + "T00:00:00+00:00", 72)
    except (OSError, ValueError, TypeError, KeyError):
        add("ledger", "Ledger check and anchor", "unavailable", "Ledger observation records could not be read.")
    add("installed", "Installed version", "healthy", version.VERSION)
    add("latest", "Latest available release", "unavailable", "No trusted release feed is configured. Shipped release notes do not prove the latest available release.")
    report = home / "batch_report.txt"
    try:
        with report.open(encoding="utf-8") as stream:
            first = stream.readline(512).strip()
        if not re.fullmatch(r"Overnight run \d{2}/\d{2}/\d{4} \d{2}:\d{2}: (?:\d+ h )?\d+ min", first):
            add("morning", "Morning report", "unavailable", "The report header was not recognized; open the report through the operator guide.", _mtime(report))
        else:
            add("morning", "Morning report", "healthy", first, _mtime(report), 36)
    except (OSError, UnicodeError):
        add("morning", "Morning report", "never-run" if not report.exists() else "unavailable", "No readable morning report is available.")
    return {"at": now.isoformat(), "status": "healthy" if all(s["status"] == "healthy" for s in signals) else "attention",
            "note": "Recorded observations only. Loading this page runs no checks or repairs; missing evidence is not success.", "signals": signals}
