"""USCIS fees as of a date: schemas/law/fees.json (copied from Form G-1055) with
its dated changes applied -- USCIS announces some increases weeks ahead
("postmarked on or after Oct. 16, 2026 ... must include the new fee"), so a
packet built today for mailing next month must state next month's amount.

Every place that states a fee (cover letters, the family and N-400 checks,
the asylum packet) reads it here.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import clock
import schema_path

PATH = schema_path.path("law", "fees")


def load(on: date | None = None, path: Path = PATH) -> dict[str, Any]:
    """fees.json with every scheduled change whose effective date is on or before `on` (default today)."""
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if path == PATH:  # the amounts the attorney set on the Settings page (src/settings.py)
        import settings

        data = settings.overlay("fees", data)
    on = on or clock.today()
    data = json.loads(json.dumps(data))  # a copy: the scheduled changes are applied to it, never to the file
    applied = []
    for change in data.get("scheduled") or []:
        if date.fromisoformat(change["effective"]) <= on:
            table, key = change["fee"].split(".", 1)
            data.setdefault(table, {})[key] = change["amount"]
            applied.append(change)
    data["applied_changes"] = applied
    data["upcoming_changes"] = [c for c in data.get("scheduled") or [] if c not in applied]
    return data


def upcoming(fee_keys: list[str], within_days: int = 30, on: date | None = None) -> list[dict[str, Any]]:
    """Scheduled changes to these fees ("pl_119_21.annual_asylum") in the coming days -- the pre-mailing check warns."""
    on = on or clock.today()
    return [c for c in load(on)["upcoming_changes"] if c["fee"] in fee_keys and (date.fromisoformat(c["effective"]) - on).days <= within_days]
