"""A green card for an asylee or a refugee -- Form I-485, Part 2 item 3.d --
from 8 CFR 209.1 and 209.2 (eCFR as of 09/30/2026, data/reference/8cfr209.*.xml):

  An asylee (INA 209(b), 8 CFR 209.2): physically present in the U.S. for at
    least one year after asylum was granted; still a refugee (or the spouse or
    child of one); not firmly resettled in another country; a full medical
    exam (Form I-693). In removal proceedings, only the immigration judge can
    take the application (209.2(c)). Approved, the green card dates from one
    year before the approval (209.2(f)).
  A refugee (INA 209(a), 8 CFR 209.1): must apply one year after entry; no new
    medical exam unless there were medical grounds at admission -- the
    vaccination record only (209.1(c)). Approved, the green card dates from
    the day of arrival (209.1(e)).

Fees (G-1055, edition 10/01/26): an asylee pays the I-485's general fee; a
refugee pays nothing. Where it is mailed (USCIS's I-485 page, "Asylum,
Refugee, or HRIFA"): the non-family lockbox for the client's state
(schemas/law/uscis_lockboxes_nfb.json).

case_facts() runs on every reviewed case (src/review/state.py), so the
I-485 itself is filled with the category, the grant date and the exemptions.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from filing_questions import DATE, YES_NO, lockbox, money, plus_years, putter, sij, state_of, us, value
from filing_questions import iso as _d
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

TITLE = "Green card as an asylee or refugee (I-485)"
ASYLEE, REFUGEE = "Asylee (INA 208)", "Refugee (INA 207)"
SECTIONS = [
    ("The grant", "the attorney", [
        ("applicant.filing_category", "Part 2, 3.d: asylee or refugee", {"type": "choice", "options": [ASYLEE, REFUGEE]}, True),
        ("asylee.granted_on", "Date asylum was granted (Part 2, 3.d)", DATE, False),
        ("asylee.refugee_admitted_on", "Date first admitted as a refugee (Part 2, 3.d)", DATE, False),
    ]),
    ("Eligibility (8 CFR 209)", "the attorney", [
        ("asylee.present_one_year", "Physically present in the U.S. for at least one year since the grant (or the admission as a refugee)?", YES_NO, True),
        ("asylee.still_refugee", "Asylee: still a refugee, or the spouse or child of one. The grant not terminated?", YES_NO, False),
        ("asylee.firmly_resettled", "Asylee: firmly resettled in any other country?", YES_NO, False),
    ]),
]


def _status(client_dir: Path | None) -> dict[str, Any]:
    path = client_dir / "status.json" if client_dir else None
    return json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}


def court_grant(status: dict[str, Any]) -> date | None:
    """The day an immigration judge granted asylum (a hearing's result on the case page, src/journey.py)."""
    found = [_d((h.get("result") or {}).get("decision_date")) for h in (status.get("journey") or {}).get("hearings") or []
             if (h.get("result") or {}).get("outcome") == "Decision: relief granted" and (h.get("result") or {}).get("asylum")]
    found = [d for d in found if d]
    return max(found) if found else None


def grant(graph, status: dict[str, Any]) -> dict[str, Any] | None:
    """{category, date, how}: what the case shows -- an answer first, then an I-589 approval, a judge's grant, an I-94's class."""
    import journey

    answered = value(graph, "applicant.filing_category")
    i589 = next((n for n in reversed(journey.notices(graph)) if n["form"] == "I-589" and n["kind"] == "approval"), None)
    judge = court_grant(status)
    klass = str(value(graph, "applicant.i94_class_of_admission") or "").upper().replace("-", "")
    category = answered if answered in (ASYLEE, REFUGEE) else ASYLEE if (i589 or judge or klass.startswith("AS")) else \
        REFUGEE if klass.startswith("RE") else None
    if category == ASYLEE:
        when = _d(value(graph, "asylee.granted_on")) or (_d(i589["date"]) if i589 else None) or judge
        how = "asylum granted by USCIS" if i589 else "asylum granted by the immigration judge" if judge else "asylum granted"
        return {"category": category, "date": when, "how": how}
    if category == REFUGEE:
        return {"category": category, "date": _d(value(graph, "asylee.refugee_admitted_on")) or _d(value(graph, "applicant.i94_arrival_date")),
                "how": "admitted as a refugee"}
    return None


def eligible_on(g: dict[str, Any] | None) -> date | None:
    """One year after the grant (or the admission as a refugee): 8 CFR 209.1(a)(1), 209.2(a)(1)(ii)."""
    return plus_years(g["date"], 1) if g and g.get("date") else None


def case_facts(graph, client_dir: Path | None = None):
    """The I-485's asylee or refugee answers, from what the case shows (each derived, for the attorney to confirm).
    Nothing for an SIJ case, or a case without a grant."""
    if sij(graph):
        return graph
    g = grant(graph, _status(client_dir))
    if not g:
        return graph
    put = putter(graph, "asylee.derive")
    asylee = g["category"] == ASYLEE
    put("applicant.filing_category", g["category"], g["how"])
    if g["date"]:
        put("asylee.granted_on" if asylee else "asylee.refugee_admitted_on", g["date"].isoformat(), g["how"])
    put("applicant.current_status_text", "ASYLEE" if asylee else "REFUGEE", g["how"])
    put("applicant.affidavit_of_support_exemption", "Not required", "an asylee or refugee adjusts under INA 209: no Affidavit of Support (Part 3, 1.e)")
    put("applicant.part2.adjusting_under_245i", "No", "adjusting under INA 209, not 245(i)")
    if asylee:
        put("applicant.public_charge_exemption", "Asylee", "Part 9, item 56: the asylee box (Form I-589 or I-730)")
    return graph


def derive(graph, today: date):
    return graph  # case_facts already ran on the reviewed case (src/review/state.py)


def fee(graph, today: date) -> tuple[int | None, str]:
    """(the I-485 fee, why) from schemas/law/fees.json (G-1055)."""
    import fees

    paper = fees.load(today).get("paper") or {}
    if value(graph, "applicant.filing_category") == REFUGEE:
        return paper.get("i485_refugee"), "a refugee's I-485 (G-1055: no fee)"
    return paper.get("i485"), "an asylee's I-485 (G-1055: the general fee; a child under 14 filing with a parent's I-485 pays the lower fee. The attorney checks)"


def mail_to(graph) -> tuple[list[str] | None, str | None]:
    return lockbox("uscis_lockboxes_nfb", state_of(graph))


def notes(graph, today: date) -> list[dict[str, str]]:
    out = []
    when = _d(value(graph, "asylee.granted_on")) or _d(value(graph, "asylee.refugee_admitted_on"))
    refugee = value(graph, "applicant.filing_category") == REFUGEE
    if when:
        opens = plus_years(when, 1)
        out.append({"level": "warn" if today < opens else "info", "title": "When",
                    "text": (f"From {us(opens)}: one year after " + ("the admission as a refugee: a refugee MUST apply then (8 CFR 209.1(a))."
                                                                       if refugee else "asylum was granted, physically present in the U.S. (8 CFR 209.2(a)(1)(ii)).")
                             + (" Not yet." if today < opens else ""))})
    amount, why = fee(graph, today)
    out.append({"level": "info", "title": "Fee", "text": f"{money(amount)}: {why}."})
    lines, name = mail_to(graph)
    out.append({"level": "info" if lines else "warn", "title": "Where",
                "text": f"USCIS {name} lockbox: " + " / ".join(lines) if lines else
                "The client's state isn't on USCIS's non-family lockbox chart : the attorney sets the address by hand."})
    out.append({"level": "info", "title": "Medical exam",
                "text": "A refugee: the vaccination record only, unless there were medical grounds at admission (8 CFR 209.1(c))." if refugee else
                "An asylee: the full medical exam, Form I-693, in the civil surgeon's sealed envelope (8 CFR 209.2(d))."})
    out.append({"level": "info", "title": "Work permit",
                "text": "An asylee's or refugee's own work permit (I-765, category (a)(5) or (a)(3)) is mailed separately, never in the same envelope as the I-485 "
                        "(USCIS's I-765 filing addresses)."})
    if refugee:
        out.append({"level": "warn", "title": "Part 9, item 56",
                    "text": "The I-485 has no refugee box among the public charge exemptions: the attorney answers Part 9's public charge items as the "
                              "I-485 Instructions say for a refugee."})
    return out


@producer(ATTORNEY)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    out = []
    category = value(graph, "applicant.filing_category")
    if category and category not in (ASYLEE, REFUGEE):
        out.append(held(OFFICE, f"The case's I-485 category is \"{category}\", not asylee or refugee: this is the wrong packet, or the category is wrong."))
        return out
    asylee = category == ASYLEE
    when = _d(value(graph, "asylee.granted_on" if asylee else "asylee.refugee_admitted_on"))
    if category and not when:
        out.append(held(CLIENT, "The date asylum was granted (Part 2, 3.d)." if asylee else "The date the client was first admitted as a refugee (Part 2, 3.d)."))
    if when and today < plus_years(when, 1):
        out.append(held(OFFICE, f"Too early: the client can apply from {us(plus_years(when, 1))}, one year after "
                   + ("the grant (8 CFR 209.2(a)(1)(ii))." if asylee else "the admission as a refugee (8 CFR 209.1(a)(1)).")))
    if value(graph, "asylee.present_one_year") == "No":
        out.append("Not physically present in the U.S. for a year since the grant: the client isn't eligible yet (8 CFR 209).")
    if asylee and value(graph, "asylee.still_refugee") == "No":
        out.append("The attorney answered that the client is no longer a refugee: an asylee must still be one to adjust (8 CFR 209.2(a)(1)(iii)).")
    if asylee and value(graph, "asylee.firmly_resettled") == "Yes":
        out.append("Firmly resettled in another country: an asylee can't adjust (8 CFR 209.2(a)(1)(iv)).")
    if asylee and value(graph, "applicant.nta_present") and not court_grant(_status(client_dir)):
        out.append("A Notice to Appear is in the case: while the client is in removal proceedings, only the immigration judge can take an asylee's I-485 "
                   "(8 CFR 209.2(c)): the attorney confirms the proceedings ended.")
    return out


def letter(graph, today: date) -> dict[str, Any]:
    """The cover letter's address, subject and fee for this client."""
    amount, _why = fee(graph, today)
    lines, _ = mail_to(graph)
    refugee = value(graph, "applicant.filing_category") == REFUGEE
    import fees

    edition = fees.load(today).get("edition") or "current"
    if amount is None:
        text = "Filing fee: [the attorney sets it. See the packet's problems]."
    elif amount == 0:
        text = f"No filing fee is required for Form I-485 when filed by a refugee (Form G-1055, Fee Schedule, edition {edition})."
    else:
        text = f"Enclosed is the filing fee of {money(amount)} for Form I-485, paid by the enclosed Form G-1450 (Form G-1055, edition {edition})."
    return {"re_lines": ["Application: I-485 Application to Register Permanent Residence or Adjust Status",
                         "Basis: " + ("Refugee Status, INA Section 209(a)" if refugee else "Asylee Status, INA Section 209(b)")],
            "mail_to": lines or ["[USCIS address: see the packet's problems]"], "fees": text, "no_payment": amount == 0}
