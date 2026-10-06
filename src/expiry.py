"""The expiry radar: one deadline for every expiring document a case still needs
(docs/design_plan.md Part 2).

src/journey.py already works out three of them from the case's facts: the
work permit (id "ead"), the green card ("i90", or "i751" for a conditional
one) and DACA ("daca"). This reads the case's document record
(documents.json, src/documents.py) and fills the gaps, so the firm never
learns of an expiring document from the client:

    passport          consular cases (a valid passport at the interview) and
                      travel cases (advance parole is not a passport)
    work permit       one journey.py doesn't cover: a renewal still far off, a
                      spouse's or a child's
    advance parole, I-94 admit-until, TPS end date, green card, DACA
    driver's license  where the case's packet lists it as an identity exhibit
    police clearance  consular cases: two years from the day it was issued

Each deadline carries the document (its type's name, whose it is), the date,
the rule it comes from with the official source and the day it was read
(schemas/law/expiry_rules.json: nothing here is a guessed window; a window no
official source gives is left out and the document's own end date is shown),
and the filing it opens (a filing id from packet.FILINGS, so the case page
can offer it).

  - A document the case no longer needs is not listed: a newer one of the same
    kind for the same person replaces an older one (a renewed passport), and a
    kind stops mattering when the case moves past it (the work permit once the
    client is a resident, the I-94 once a green card application is filed).
  - Where journey.py already has the deadline, journey.py's wins: its deadline
    ids come in as case["ids"] and the radar drops its own for that kind.
  - Every deadline here is still actionable, never history: the "passed" display
    rule (review/overview.py HISTORY_IDS) never greys one out. A kind whose date
    has no clock after it (I-94, TPS, advance parole) is dropped once it has
    passed instead (after_end in the rules file).

deadlines(client_dir, today, case) is called by journey.journey(), so What's due,
My work and the overnight run's late count see them with no second code path.
"""

from __future__ import annotations

import calendar
import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import journey
import schema_path

RULES = schema_path.path("law", "expiry_rules")
OWNER = "paralegal"
PREFIX = "expiry."
# journey.py's own deadline ids that stand for an expiry kind: when it has one, the radar adds nothing for that kind
COVERED_BY_JOURNEY = {"ead": {"ead"}, "green_card": {"i90", "i751"}, "daca": {"daca"}}
# the case's own people; a petitioner's or a parent's document is evidence, not something the case waits on
OUT_OF_SCOPE = ("petitioner", "parent")
PEOPLE = {"applicant": "Applicant", "spouse": "Spouse", "petitioner": "Petitioner", "parent": "Parent", "unknown": "Not sure whose"}  # as the Documents page says it


@lru_cache(maxsize=2)
def _rules(path: str, mtime: float) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rules(path: Path = RULES) -> dict[str, Any]:
    """schemas/law/expiry_rules.json: the kinds, each with its rule and its official source."""
    return _rules(str(path), path.stat().st_mtime)


def person_name(person: str) -> str:
    return PEOPLE.get(person) or (f"Child {person.split('_')[1]}" if person.startswith("child_") else person.replace("_", " "))


def _us(d: date) -> str:
    return d.strftime("%m/%d/%Y")


def _plus_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # Feb. 29
        return date(d.year + years, 3, 1)


def _months_before(d: date, months: int) -> date:
    """The same day `months` earlier (or that month's last day): when a 10-year card can be renewed."""
    y, m = d.year - (d.month <= months), (d.month - months - 1) % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _type_name(doc_type: str) -> str:
    import documents

    return documents.name(doc_type)


def _type_short(doc_type: str) -> str:
    import documents

    return documents.short_name(doc_type)  # the name for a column (the long name stays on hover)


def _filing_name(filing: str | None) -> str | None:
    """The filing's name as the packet says it ("I-765 (work permit)"), never its id."""
    if not filing:
        return None
    try:
        import packet

        return packet.load_filing(filing)["title"]
    except Exception:  # noqa: BLE001 -- a name that can't be read must not hide the deadline
        return None


def _in_packet(filing: str | None, doc_type: str) -> bool:
    """The case's packet (the filing it is on: the I-485 unless it says otherwise) lists this type as an exhibit."""
    try:
        import packet

        return any(doc_type in ex.get("types", []) for ex in packet.load_filing(filing)["exhibits"])
    except Exception:  # noqa: BLE001 -- no packet schema, no exhibit
        return False


def _case(case: dict[str, Any] | None) -> dict[str, Any]:
    """The case's standing, as journey.journey() knows it. Standing alone (no case), every kind is still needed."""
    base = {"stage": None, "resident": False, "daca": False, "daca_pending": False, "consular": False, "travel": False, "filed": None,
            "packet_filing": None, "ids": set(), "notices": [], "graph": None}
    return base | (case or {})


def _needed(kind: str, case: dict[str, Any]) -> bool:
    """Whether the case still needs this kind of document dated (the firm's rule for what is worth a deadline)."""
    if case["stage"] == "citizen":
        return False
    resident = case["resident"]
    if kind == "ead":
        return not resident and not case["daca"]
    if kind == "daca":
        return case["daca"] and not resident and not case["daca_pending"]
    if kind == "green_card":
        return resident
    if kind in ("tps", "advance_parole"):
        return not resident
    if kind == "i94":
        return not resident and not case["daca"] and not case["filed"]
    if kind == "passport":
        return not resident and (case["consular"] or case["travel"])
    if kind == "drivers_license":
        return not resident and not case["filed"] and _in_packet(case["packet_filing"], "drivers_license")
    if kind == "police_clearance":
        return case["consular"] and not resident
    return False


# For the screen: which cases each kind is watched for, in the words of _needed() above (tests/test_expiry.py keeps the two in step: every kind in
# schemas/law/expiry_rules.json has a line here). An empty Expiring documents list says this, so it does not look broken (buyer visit 4).
WATCHED_FOR = {
    "ead": "every case that is not a permanent resident yet and not a DACA case",
    "green_card": "permanent residents (the renewal, or taking off the conditions)",
    "daca": "DACA cases, once the client has the approval",
    "tps": "every case until the client is a permanent resident",
    "advance_parole": "every case until the client is a permanent resident",
    "i94": "every case that is not a permanent resident or a DACA case, until a green card application is filed",
    "passport": "consular processing cases and travel cases only",
    "drivers_license": "cases whose packet lists it as an identity document, until the packet is filed",
    "police_clearance": "consular processing cases only",
}


def watched() -> list[dict[str, str]]:
    """What the watch looks at, for the screen: each kind of document, the cases it is watched for, the rule and where the rule comes from."""
    out = []
    for kind, spec in rules()["kinds"].items():
        tracks = spec.get("tracks")
        out.append({"id": kind, "name": spec["name"], "for": WATCHED_FOR[kind],
                    "rule": " ".join(t["rule"] for t in tracks.values()) if tracks else spec["rule"],
                    "source": " ".join(t["source"] for t in tracks.values()) if tracks else spec["source"]})
    return out


def _kind_of(doc_type: str, case: dict[str, Any]) -> str | None:
    for kind, spec in rules()["kinds"].items():
        if doc_type in spec["types"]:
            return "daca" if kind == "ead" and case["daca"] else kind  # a (c)(33) work permit carries DACA's date
    return None


def _ends(record: dict[str, Any], kind: str) -> date | None:
    """The date the document ends: its own end date, or for a police clearance two years after it was issued."""
    if kind == "police_clearance":
        issued = journey._d(record.get("issued"))
        return _plus_years(issued, rules()["kinds"]["police_clearance"]["valid_years"]) if issued else None
    return journey._d(record.get("expires"))


def _compose(kind: str, record: dict[str, Any], end: date, today: date, case: dict[str, Any], cfg: dict[str, Any], newer: bool) -> dict[str, Any] | None:
    """The deadline's date, owner, sentence, window and filing for one kind of document (None: nothing to show)."""
    spec = rules()["kinds"][kind]
    person = record.get("person") or "unknown"
    name = spec["name"] if kind == "daca" else _type_name(record["type"])
    subject = name if person == "applicant" else f"{name} ({person_name(person)})"
    out = {"date": end, "owner": OWNER, "rule": spec.get("rule", ""), "source": spec.get("source", ""), "filing": spec.get("filing"), "opens": None,
           "tracks": [], "end": end}
    since = lambda opens: f"since {_us(opens)}" if today >= opens else f"from {_us(opens)}"  # noqa: E731
    if kind == "ead":
        out["opens"] = end - timedelta(days=cfg["ead_renewal_days_before"])
        what = f"{subject} expires {_us(end)}: renewal (Form I-765) can be filed {since(out['opens'])}"
    elif kind == "green_card":
        issued = journey._d(record.get("issued"))
        if issued and end <= _plus_years(issued, 2):  # valid two years or less: taken as a conditional card (an inference from the validity, see the rules file)
            out["filing"], out["opens"] = spec["filing_conditional"], end - timedelta(days=90)
            what = f"{subject} expires {_us(end)}: it is a 2-year conditional card, so remove the conditions (Form I-751) {since(out['opens'])}"
        elif issued:
            out["opens"] = _months_before(end, cfg.get("card_renewal_months_before", 6))
            what = f"{subject} expires {_us(end)}: renew with Form I-90 (can be filed {since(out['opens'])})"
        else:  # the issue date isn't known: a 2-year card and a 10-year card are renewed differently
            out["filing"] = None
            what = (f"{subject} expires {_us(end)}: check whether it is a 2-year conditional card (remove the conditions with Form I-751) "
                    "or a 10-year card (renew with Form I-90)")
    elif kind == "daca":
        import daca

        r = daca._rules()
        opens, by, until = end - timedelta(days=r["earliest"]), end - timedelta(days=r["by"]), _plus_years(end, r["grace_years"])
        out["opens"] = opens
        if today <= end:
            out["date"] = by if today <= by else end
            what = (f"{subject}: DACA ends {_us(end)}: file the renewal (I-821D and I-765) by {_us(by)}; "
                    + (f"it can be filed from {_us(opens)}" if today < opens else "USCIS asks for it 150 to 120 days before"))
        elif today <= until:
            out["date"], out["owner"] = until, "attorney"
            what = f"{subject}: DACA ended {_us(end)}: the last day to file it as a renewal is {_us(until)} (one year after it ended)"
        else:
            return None  # more than a year after it ended is a new request: journey.py raises that as a step for the attorney
    elif kind == "tps":
        what = f"{subject} ends {_us(end)}: re-register in the period the Federal Register notice for the client's country sets"
    elif kind == "advance_parole":
        what = f"{subject} ends {_us(end)}: a renewal is a new Form I-131 (USCIS gives no earlier filing window for it)"
    elif kind == "i94":
        what = f"Admitted until {_us(end)}: to stay longer, file Form I-539 before that day"
    elif kind == "passport":
        tracks = spec["tracks"]
        out["tracks"] = [t for t in tracks if case[t]]
        out["rule"] = " ".join(tracks[t]["rule"] for t in out["tracks"])
        out["source"] = " ".join(tracks[t]["source"] for t in out["tracks"])
        what = f"{subject} expires {_us(end)}: needed for {' and '.join(tracks[t]['name'] for t in out['tracks'])}"
    elif kind == "drivers_license":
        what = f"{subject} expires {_us(end)}: it is an identity document in the packet, so ask for a current one before the packet is filed"
    elif kind == "police_clearance":
        what = f"{subject} issued {_us(journey._d(record['issued']))} is valid until {_us(end)} (two years): ask for a new one if the visa won't be issued by then"
    else:  # pragma: no cover -- every kind in the rules file is handled above
        return None
    out["what"] = what + (". A newer one was added after it but its date was not read: check it" if newer else "")
    return out


def deadlines(client_dir: str | Path, today: date, case: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The case's expiring documents as journey.py deadlines (id "expiry.<kind>.<document id>"), soonest first.

    case: how journey.journey() sees the case: stage, resident (a resident or later), daca (a DACA case),
    daca_pending, consular, travel, filed (the filing id of the last mailing, else None), packet_filing,
    ids (the deadline ids journey.py already has), notices, graph. None stands for a case with nothing known."""
    import documents

    # through load: whose each document is as the whole case says it now (an assumed "the client's" goes when a relative's is placed)
    data = documents.load(Path(client_dir)) if (Path(client_dir) / documents.FILE).exists() else None
    if not data or not data.get("documents"):
        return []
    case = _case(case)
    cfg = journey.settings()["deadlines"]
    # the latest document of each kind for each person: an older one a newer one replaced is not listed
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in data["documents"]:
        kind = _kind_of(record.get("type") or "", case)
        person = record.get("person") or "unknown"
        if kind and person not in OUT_OF_SCOPE:
            groups.setdefault((kind, person), []).append(record)
    case = case | {"travel": case["travel"] or any(kind == "advance_parole" for kind, _ in groups)}  # an advance parole document: a travel case
    out = []
    for (kind, person), records in groups.items():
        if not _needed(kind, case):
            continue
        dated = [(d, r) for r in records if (d := _ends(r, kind))]
        if not dated:
            continue
        end, record = max(dated, key=lambda x: (x[0], x[1].get("added") or ""))
        if rules()["kinds"][kind].get("after_end") == "drop" and end < today:
            continue
        if COVERED_BY_JOURNEY.get(kind, set()) & case["ids"] and person in ("applicant", "unknown"):
            continue  # journey.py has this one
        if kind == "green_card" and any(n.get("form") == "I-90" and (n.get("date") or "") >= (end - timedelta(days=365)).isoformat() for n in case["notices"]):
            continue  # a renewal is already filed (journey.py's own rule)
        newer = any(not _ends(r, kind) and (r.get("added") or "") > (record.get("added") or "") for r in records)
        item = _compose(kind, record, end, today, case, cfg, newer)
        if not item:
            continue
        days = (item["date"] - today).days
        out.append({
            "id": f"{PREFIX}{kind}.{record['id']}", "date": item["date"].isoformat(), "days_left": days, "level": journey._level(days, cfg),
            "what": item["what"], "owner": item["owner"], "source": item["source"], "kind": "expiry",
            "expiry": {
                "kind": kind, "ends": item["end"].isoformat(), "opens": item["opens"].isoformat() if item["opens"] else None,
                "document": {"id": record["id"], "type": record["type"], "type_name": _type_name(record["type"]), "type_short": _type_short(record["type"]), "person": person,
                             "person_name": person_name(person), "file": (record.get("files") or [None])[0]},
                "rule": item["rule"], "source": item["source"], "tracks": item["tracks"], "filing": item["filing"],
                "filing_name": _filing_name(item["filing"]), "confidential": record.get("confidential"),
            },
        })
    if out:  # a protected case protects every document in it, whatever each record says (documents.case_confidentiality, as the Search index asks it)
        protected = documents.case_confidentiality(client_dir, case["graph"]) if case["graph"] is not None else documents.case_confidentiality(client_dir)
        for d in out:
            d["expiry"]["confidential"] = protected or d["expiry"]["confidential"]
    return sorted(out, key=lambda d: d["date"])
