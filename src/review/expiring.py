"""The firm-wide list behind What's due, "Expiring documents": every case's expiring
documents (src/expiry.py, carried in each case's journey summary), soonest first,
filtered by office (src/offices.py) and by the person responsible.

The person responsible is the case's reviewer of record: whoever made the case's
most recent review decision (review/state.py; decisions.json). A case nobody has
decided anything on yet has none, and is listed under "Nobody yet": the app has no
other assignment of a case to a person.

Documents in a protected case (8 U.S.C. 1367: VAWA, T and U; 8 CFR 208.6: asylum)
are listed for an attorney only, as the Search page does (src/index.py): a paralegal
is told how many more there are.
"""

from __future__ import annotations

from datetime import datetime

import clock
from pathlib import Path
from typing import Any

from .state import load_decisions

NOBODY = "none"  # the reviewer filter's value for a case nobody has decided anything on
HORIZONS = (30, 60, 90, 180, 365)
DEFAULT_HORIZON = 90


def reviewer_of_record(client_dir: Path) -> str | None:
    """The person who made the case's latest review decision, else None."""
    latest = max(load_decisions(client_dir).values(), key=lambda d: d.get("at") or "", default=None)
    return (latest or {}).get("reviewer") or None


def restricted(deadline: dict[str, Any]) -> bool:
    """A deadline about a document in a protected case (the case's own flag, carried on the document)."""
    return bool((deadline.get("expiry") or {}).get("confidential"))


def expiring(rows, data_root: Path, *, today: datetime | None = None, horizon_days: int = DEFAULT_HORIZON, office: str | None = None,
             reviewer: str | None = None, role: str | None = None, confidential_ok=None) -> dict[str, Any]:
    """The list, and the choices for its two filters. rows: the all-clients rows (review/overview.py), each with
    its office's name (the review app adds it) and its journey summary's deadlines; the review app passes only the
    cases the person may see (src/restricted.py). confidential_ok(case id): may they see that case's confidential
    documents (an attorney, or staff named on the case); without it, a paralegal may not."""
    hide = (lambda case: not confidential_ok(case)) if confidential_ok else (lambda case: role == "paralegal")
    import journey

    import query

    cfg = journey.settings()["deadlines"]
    today = today.date() if isinstance(today, datetime) else (today or clock.today())  # the office's day (src/clock.py)
    rows = list(rows)
    items, offices, reviewers, hidden = [], set(), set(), 0
    known: dict | None = None  # every case's reviewer of record, read once from the query layer (src/query.py) when a case needs it
    for row in rows:
        due = [d for d in ((row.get("journey") or {}).get("deadlines") or []) if d.get("expiry")]
        if not due:
            continue
        if known is None:
            known = query.reviewers(data_root) or {}
        who = (known[row["id"]][0] if row["id"] in known else reviewer_of_record(Path(data_root) / row["id"]) if (Path(data_root) / row["id"]).is_dir() else None)
        offices.add(row.get("office") or "")
        reviewers.add(who or NOBODY)
        if (office and row.get("office") != office) or (reviewer and (who or NOBODY) != reviewer):
            continue
        name = (row.get("summary") or {}).get("name") or row["id"]
        for d in due:
            days = (datetime.fromisoformat(d["date"]).date() - today).days
            if days > horizon_days:
                continue
            if restricted(d) and hide(row["id"]):
                hidden += 1
                continue
            e = d["expiry"]
            items.append({"client": row["id"], "name": name, "office": row.get("office") or "", "reviewer": who, "document": e["document"]["type_name"], "document_short": e["document"].get("type_short") or e["document"]["type_name"],
                          "whose": e["document"]["person_name"], "date": d["date"], "ends": e["ends"], "days_left": days, "what": d["what"], "rule": e["rule"],
                          "source": e["source"], "filing": e["filing"], "filing_name": e["filing_name"], "tracks": e["tracks"],
                          "level": journey._level(days, cfg)})
    items.sort(key=lambda i: (i["date"], i["name"] or ""))
    import expiry

    return {"items": items, "restricted": hidden, "horizon_days": horizon_days, "horizons": list(HORIZONS), "watched": expiry.watched(),
            "cases_checked": len(rows),
            "offices": sorted(o for o in offices if o), "reviewers": sorted(r for r in reviewers if r != NOBODY) + ([NOBODY] if NOBODY in reviewers else [])}
