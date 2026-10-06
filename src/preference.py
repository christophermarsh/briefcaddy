"""The family preference wait: which visa class the relationship gives the
client, the priority date, and whether it is current this month.

  An immediate relative of a U.S. citizen -- a spouse, an unmarried child
    under 21, a parent -- never waits: a visa is always available.
  The preference categories wait for the priority date (the day USCIS
    received the I-130) to come before the cut-off in the month's Visa
    Bulletin, for the client's chargeability area:
      F1  unmarried son or daughter (21+) of a U.S. citizen
      F2A spouse or unmarried child under 21 of a permanent resident
      F2B unmarried son or daughter (21+) of a permanent resident
      F3  married son or daughter of a U.S. citizen
      F4  brother or sister of a U.S. citizen

The month's cut-offs are the attorney's setting in
schemas/law/visa_bulletin_family.json, from USCIS's "Adjustment of Status Filing
Charts from the Visa Bulletin" page (which chart USCIS accepts this month)
and the Department of State's Visa Bulletin -- never guessed: unset, the case
says so and waits. Chargeability is the country of birth (the attorney
checks cross-chargeability); a child's age for the category is the attorney's
call under the Child Status Protection Act.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from filing_questions import us, value
from filing_questions import iso as _d
import schema_path

SETTINGS = schema_path.path("law", "visa_bulletin_family")
CLASS = {"Spouse of U.S. citizen": "IR", "Child under 21 of U.S. citizen": "IR", "Parent of U.S. citizen": "IR",
         "Unmarried son/daughter 21+ of U.S. citizen": "F1", "Spouse of LPR": "F2A", "Child under 21 of LPR": "F2A",
         "Unmarried son/daughter 21+ of LPR": "F2B", "Married son/daughter of U.S. citizen": "F3", "Sibling of U.S. citizen": "F4"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def settings() -> dict[str, Any]:
    """The family Visa Bulletin: the default file, with the month the attorney set on the Settings page (src/settings.py)."""
    import settings as firm_settings

    return firm_settings.overlay("visa_bulletin_family", json.loads(SETTINGS.read_text(encoding="utf-8")))


def child_category(graph, today: date) -> str | None:
    """A child's category from their age and marital status (the attorney confirms the age under the CSPA)."""
    dob, married = _d(value(graph, "applicant.dob")), str(value(graph, "applicant.marital_status") or "").lower()
    status = value(graph, "petitioner.status")
    if not dob or status not in ("USC", "LPR"):
        return None
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    is_married = married in ("married",)
    if status == "USC":
        return "Married son/daughter of U.S. citizen" if is_married else "Child under 21 of U.S. citizen" if age < 21 else "Unmarried son/daughter 21+ of U.S. citizen"
    if is_married:
        return None  # a permanent resident can't petition for a married son or daughter
    return "Child under 21 of LPR" if age < 21 else "Unmarried son/daughter 21+ of LPR"


def visa_class(graph) -> str | None:
    return CLASS.get(value(graph, "applicant.filing_category"))


def priority_date(graph) -> str | None:
    """The answer first, else the Priority Date printed on the client's I-130 notice."""
    import journey

    answered = _d(value(graph, "family.priority_date"))
    if answered:
        return answered.isoformat()
    found = [_d(n.get("priority_date")) for n in journey.notices(graph) if n["form"] == "I-130" and n.get("priority_date")]
    found = [d for d in found if d]
    return min(found).isoformat() if found else None


def status(graph, today: date) -> dict[str, Any]:
    """{class, area, month, chart, cutoff, pd, current, problems} -- current is None when it can't be known (unset, no date)."""
    klass = visa_class(graph)
    if klass in (None, "IR"):
        return {"class": klass, "current": True if klass == "IR" else None, "problems": []}
    vb = settings()
    area = (vb.get("chargeability") or {}).get(str(value(graph, "applicant.country_of_birth") or "").upper(), "ALL CHARGEABILITY")
    cutoff = ((vb.get("cutoff") or {}).get(klass) or {}).get(area)
    pd, this_month = priority_date(graph), f"{MONTHS[today.month - 1]} {today.year}"
    problems = []
    if not pd:
        problems.append(f"No priority date for this {klass} case: the I-130 isn't filed yet, or its receipt notice isn't read. The Priority Date is printed on it "
                        "(answer \"family.priority_date\" on the family questions).")
    if not vb.get("month") or not cutoff:
        problems.append(f"Set this month's family Visa Bulletin ({klass}, {area.title()}) on the Settings page.")
    elif vb["month"] != this_month:
        problems.append(f"The family Visa Bulletin is set for {vb['month']}: update it for {this_month} on the Settings page.")
    current = None
    if pd and cutoff and vb.get("month") == this_month:
        current = cutoff == "C" or date.fromisoformat(pd) < date.fromisoformat(cutoff)
    return {"class": klass, "area": area, "month": vb.get("month"), "chart": vb.get("chart"), "cutoff": cutoff, "pd": pd, "current": current,
            "problems": problems, "text": (f"{klass}: priority date {us(_d(pd))}" if pd else f"{klass}: no priority date yet")
            + (f"; {vb.get('chart')} cut-off {('current' if cutoff == 'C' else us(_d(cutoff)))} ({area}, {vb['month']})" if cutoff and vb.get("month") else "")}


def petition_on_file(graph) -> bool:
    """An I-130 for the client already with USCIS (a receipt or an approval; not rejected or denied)."""
    import journey

    i130 = [n for n in journey.notices(graph) if n["form"] == "I-130"]
    return bool(i130) and i130[-1]["kind"] not in ("rejection", "denial")


def packet_variant(graph, today: date) -> str | None:
    """Which family packet this case files now: the I-485 alone once an I-130 is on file; the I-130 alone for a
    preference case whose date isn't current (it sets the priority date); else both together (None)."""
    if petition_on_file(graph):
        return "i485_only"
    if visa_class(graph) not in (None, "IR") and status(graph, today)["current"] is not True:
        return "petition_only"
    return None
