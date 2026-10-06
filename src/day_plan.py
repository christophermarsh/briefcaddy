"""The paralegal's day plan: which cases can be got to a signed packet today, and the three things each needs.

Two parts, both pure (no case folder is read on a request):

  - summary(): one case's plan, read when the roster reads the case (src/review/roster.py keeps it in the case's entry): what holds its packet (src/packet.py plan),
    who holds each thing (src/holders.py: the client, the office, the attorney), the attorney's own items from the approvals registry (src/approvals.py: counted
    there and not a second time) and the three to do first. A case's plan is made for the filing its stage leads to, as the Filing packet tab opens it.
  - listing(): "Today", one screen over the entries a reader may open: the cases ready to build and sign first, then the cases the office can work, then the cases
    waiting only on the client; each group by next deadline, then fewest steps; 50 a page.

A case is "ready to build and sign" when nothing holds its packet and no attorney item is open. A case "waits only on the client" when the client holds every
step left. "N cases can reach a signed packet today" counts the ready cases and the cases only the office holds: the office can finish those today without waiting
for anyone.

Nothing here writes. The one button on the screen, Ask the client, is the review card's own request form.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import clock
import holders
from holders import ATTORNEY, CLIENT, OFFICE

PAGE = 50
FIRST = 3  # the things to do first
ASK_MAX = 10  # the client's items a request form is filled with
GROUPS = ("ready", "work", "client")
GROUP_NAMES = {"ready": "Ready to build and sign", "work": "Waiting on the office or the attorney", "client": "Waiting only on the client"}
ORDER = (OFFICE, ATTORNEY, CLIENT)  # what the paralegal can do herself first, then what waits for the attorney, then what waits for the client
MINE = {"paralegal": OFFICE, "attorney": ATTORNEY}


def filing_for(client_dir: Path, row: dict[str, Any]) -> str:
    """The filing the case's packet is read for: the one its stage leads to, else the packet last built, else the I-485 (the Filing packet tab's own choice)."""
    import packet

    for f in (row.get("journey") or {}).get("next_filings") or []:
        if f.get("now") and f.get("filing") in packet.FILINGS:
            return f["filing"]
    built = [(p.stat().st_mtime, f) for f, name in packet.FILINGS.items() if (p := client_dir / name).exists()]
    return max(built)[1] if built else "i485"


def items_of(plan: dict[str, Any], approvals: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """What stands between the case and a signed packet, one entry per thing to do: {"holder", "text", "n"}. The plan's lines come first; a line the approvals
    registry already lists (its `via`) is left to the registry, whose items are the attorney's, in the words of the card or the item."""
    out = []
    for line in plan["problems"]:
        n = getattr(line, "n", 1)
        if getattr(line, "via", None) or not n:
            continue
        out.append({"holder": holders.holder_of(line), "text": getattr(line, "say", None) or str(line), "n": n})
    out += [{"holder": ATTORNEY, "text": item["what"], "n": 1} for item in approvals or []]
    return out


def summary(client_dir: Path, row: dict[str, Any], approvals: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """One case's plan for the roster's entry: {"filing", "ready", "counts", "steps", "first", "client"}."""
    import packet

    filing = filing_for(client_dir, row)
    plan = packet.plan(client_dir, row, packet.load_filing(filing), light=True)
    items = items_of(plan, approvals)
    counts = {h: sum(i["n"] for i in items if i["holder"] == h) for h in holders.HOLDERS}
    steps = sum(counts.values())
    first = [i for h in ORDER for i in items if i["holder"] == h][:FIRST]
    return {"filing": filing, "ready": steps == 0, "counts": counts, "steps": steps,
            "first": [{"holder": i["holder"], "text": i["text"]} for i in first],
            "client": [i["text"] for i in items if i["holder"] == CLIENT][:ASK_MAX]}


def group_of(day: dict[str, Any]) -> str:
    c = day["counts"]
    return "ready" if day["ready"] else "client" if c[CLIENT] and not c[OFFICE] and not c[ATTORNEY] else "work"


def can_reach(day: dict[str, Any]) -> bool:
    """Nothing the client or the attorney holds stands in the way: the office can finish this case today."""
    return day["counts"][CLIENT] == 0 and day["counts"][ATTORNEY] == 0


def line(n: int) -> str:
    """"3 cases can reach a signed packet today": the same words on My work and in the morning report."""
    return f"{n} case{'s' if n != 1 else ''} can reach a signed packet today"


def _next_deadlines(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each case's next deadline (the soonest not long past), from the timelines the entries hold."""
    from review.overview import deadlines

    out: dict[str, dict[str, Any]] = {}
    for d in deadlines(rows, horizon_days=36500):
        if d["level"] != "passed":
            out.setdefault(d["client"], d)
    return out


def listing(cases: list[tuple[str, str | None, dict[str, Any], dict[str, Any]]], role: str | None, q: dict[str, Any] | None = None) -> dict[str, Any]:
    """Today for one reader. cases: (case id, the client's name, the entry's day plan, its row) for the cases this reader may be told of, and only those.
    role: "paralegal" or "attorney": the holder whose lines are marked as the reader's own."""
    q = q or {}
    mine = MINE.get(role or "")
    rows = [{"id": case, "journey": (row or {}).get("journey")} for case, _name, _day, row in cases]
    due = _next_deadlines(rows)
    out = []
    for case, name, day, _row in cases:
        d = due.get(case)
        out.append({"case": case, "name": name or case, "group": group_of(day), "steps": day["steps"], "counts": day["counts"], "filing": day["filing"],
                    "deadline": {"date": d["date"][:10], "date_text": clock.us_date(d["date"]), "what": d["what"], "days_left": d["days_left"], "level": d["level"]} if d else None,
                    "lines": [{"holder": x["holder"], "text": x["text"], "mine": x["holder"] == mine} for x in day["first"]],
                    "client": day["client"] if group_of(day) == "client" else []})
    out.sort(key=lambda r: (GROUPS.index(r["group"]), r["deadline"] is None, r["deadline"]["date"] if r["deadline"] else "", r["steps"], r["name"].casefold(), r["case"]))
    try:
        size = max(1, min(int(q.get("size") or PAGE), 200))
        page = max(1, int(q.get("page") or 1))
    except (TypeError, ValueError):
        raise ValueError("Page and size must be numbers.") from None
    pages = max(1, -(-len(out) // size))
    page = min(page, pages)
    by_group = {g: sum(1 for r in out if r["group"] == g) for g in GROUPS}
    reach = sum(1 for _c, _n, day, _r in cases if can_reach(day))
    return {"total": len(out), "groups": [{"id": g, "name": GROUP_NAMES[g], "count": by_group[g]} for g in GROUPS], "can_reach": reach, "line": line(reach),
            "rows": out[(page - 1) * size: page * size], "page": page, "pages": pages, "size": size, "today": clock.today().isoformat(), "date": clock.us_date(clock.today().isoformat())}


def night_line(entries: dict[str, dict[str, Any]], closed: set[str]) -> str:
    """The morning report's line, from the saved entries: a restricted case is not counted (the report is read by everyone)."""
    n = sum(1 for case, e in entries.items() if case not in closed and e.get("day") and e.get("row") is not None and not e["row"].get("end") and can_reach(e["day"])
            and not e["row"].get("filed_at"))
    return line(n) + "."
