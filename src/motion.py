"""After USCIS denies something: an appeal to the AAO or a motion to reopen or
reconsider, on Form I-290B (edition 05/31/24) -- from USCIS's pages
(data/reference/i-290b-page.txt, i-290b-when.txt, i-290b-addresses.txt):

  When: within 30 calendar days of the decision, 33 when it was mailed (the
    date of service is the date USCIS mailed it, 8 CFR 103.8(b)). A late
    appeal is rejected unless it meets a motion's requirements; a late motion
    to reopen only if the delay was reasonable and beyond the client's control.
  Which: USCIS's "When to Use Form I-290B" chart (APPEAL below) -- an I-485,
    I-765, I-589 or I-751 decision can't be appealed, only reopened or
    reconsidered; an I-130's appeal is Form EOIR-29; an N-400's hearing is
    Form N-336 (src/hearing_request.py); an I-601A has neither.
  Where: an SIJ I-360 or I-485 to the Chicago lockbox (P.O. Box 5510); any
    other USCIS decision to the Phoenix lockbox (P.O. Box 21100). Never to
    the AAO directly.
  Fee (G-1055 10/01/26): $800; $0 for a person seeking or granted SIJ (a
    benefit request filed before adjusting, or a motion on the I-485).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from filing_questions import DATE, LINES, TEXT, YES_NO, addresses, money, putter, sij, us, value
from filing_questions import iso as _d
from holders import ATTORNEY, OFFICE, held, producer

TITLE = "Motion or appeal after a USCIS denial (I-290B)"
KINDS = ["Appeal to the AAO: brief or evidence attached", "Appeal to the AAO: brief or evidence to the AAO within 30 days",
         "Appeal to the AAO: no brief or evidence", "Motion to reopen", "Motion to reconsider", "Motion to reopen and reconsider"]
# the office drop-down on the form: its label -> the code the form stores (Part 2, item 7)
OFFICES = {"AAO": "2", "Agana (AGA)": "WRO AGA", "Albany (ALB)": "NER ALB", "Albuquerque (ABQ)": "CRO DET", "Anchorage (ANC)": "WRO ANC",
           "Atlanta (ATL)": "SER ATL", "Baltimore (BAL)": "NER BAL", "Boise (BOI)": "CRO BOI", "Boston (BOS)": "NER BOS",
           "Brooklyn Field Office (BNY)": "8", "Buffalo (BUF)": "NER BUF", "California Service Center (WAC)": "WAC ", "Charleston (CHL)": "SER CHL",
           "Charlotte (CLT)": "SER CLT", "Charlotte Amelie (CHA)": "SER CHA", "Chicago (CHI)": "CRO CHI", "Chula Vista (CVC)": "WRO CVC",
           "Cincinnati (CIN)": "CRO CIN", "Cleveland (CLE)": "CRO CLE", "Columbus (CLM)": "CRO CLM", "Dallas (DAL)": "CRO DAL", "Denver (DEN)": "CRO DEN",
           "Des Moines (DSM)": "CRO DSM", "Detroit (DET)": "CRO DET", "El Paso (ELP)": "CRO ELP", "Fort Myers Field Office (OFM)": "9",
           "Fresno (FRE)": "WRO FRE", "Ft. Smith, AR (FSA)": "SER FSA", "Greer, SC (GRR)": "SER GRR", "Harlingen (HLG)": "CRO HLG",
           "Hartford (HAR)": "NER HAR", "Helena (HEL)": "CRO HEL", "Hialeah (HIA)": "SER HIA", "Honolulu (HHW)": "WRO HHW", "Houston (HOU)": "CRO HOU",
           "Humanitarian Affairs Branch (RIH)": "15", "Immigrant Investor Program (IIP)": "1", "Imperial Field Office (IMP)": "10",
           "Indianapolis (INP)": "CRO INP", "Jacksonville (JAC)": "SER JAC", "Kansas City (KAN)": "CRO KAN", "Kendall (KND)": "SER KND",
           "Las Vegas (LVG)": "WRO LVG", "Lawrence (LAW)": "NER LAW", "Long Island (LNY)": "NER LNY", "Los Angeles (LAC)": "WRO LAC",
           "Los Angeles (LOS)": "WRO LOS", "Los Angeles (SFV)": "WRO SFV", "Louisville (LOU)": "CRO LOU", "Manchester (MAN)": "NER MAN ",
           "Memphis (MEM)": "SER MEM", "Miami (MIA)": "SER MIA", "Milwaukee (MIL)": "CRO MIL", "Montgomery Field Office (MGA)": "11",
           "Mount Laurel (MTL)": "NER MTL", "Nashville Field Office (NTN)": "19", "National Benefits Center (NBC)": "4",
           "Nebraska Service Center (LIN)": "NSC", "New Jersey Central (NJC)": "NER NEW", "New Orleans (NOL)": "SER NOL",
           "New York City (NYC)": "NER NYC", "Newark (NEW)": "12", "Norfolk (NOR)": "NER NOR", "Oakland Park (OKL)": "SER OKL",
           "Oklahoma City (OKC)": "CRO OKC", "Omaha (OMA)": "CRO OMA", "Orlando (ORL)": "SER ORL", "Other": "3", "Philadelphia (PHI)": "NER PHI",
           "Phoenix (PHO)": "WRO PHO", "Pittsburgh (PIT)": "NER PIT", "Portland (POM)": "NER POM", "Portland (POO)": "WRO POO",
           "Potomac Service Center (YSC)": "13", "Providence (PRO)": "NER PRO", "Queens (QNS)": "NER QNS", "Raleigh (RAL)": "SER RAL ",
           "Refugee and International Operations (RIH)": "17", "Reno (REN)": "WRO REN", "Sacramento (SAC)": "WRO SAC",
           "Salt Lake City (SLC)": "CRO SLC", "San Antonio (SNA)": "CRO SNA", "San Bernardino (SBD)": "WRO SBD", "San Diego (SND)": "WRO SND",
           "San Fernando Field Office (SFV)": "14", "San Francisco (SFR)": "WRO SFR", "San Jose (SNJ)": "WRO SNJ", "San Juan (SAJ)": "SER SAJ",
           "Santa Ana (SAA)": "WRO SAA", "Seattle (SEA)": "WRO SEA", "Spokane (SPO)": "WRO SPO", "St. Albans (STA)": "NER STA",
           "St. Louis (STL)": "CRO STL", "St. Paul (SPM)": "CRO SPM", "Tampa (TAM)": "SER TAM", "Texas Service Center (SRC)": "TSC",
           "Tucson (TUC)": "WRO TUC", "Vermont Service Center (EAC)": "5", "Washington (WAS)": "6", "West Palm Beach (WPB)": "7", "Yakima (YAK)": "WRO YAK"}
# USCIS's chart: can the decision be appealed? (a motion is available for every form the firm files except the I-601A)
APPEAL = {"I-90": False, "I-130": "EOIR-29", "I-131": None, "I-360": True, "I-485": False, "I-589": False, "I-601": True, "I-601A": False,
          "I-602": False, "I-212": True, "I-730": False, "I-751": False, "I-765": False, "N-400": "N-336", "N-600": True, "N-565": True}
NO_MOTION = {"I-601A"}
SIJ_LINES = ["USCIS", "ATTN: I-290B", "P.O. BOX 5510", "CHICAGO, IL 60680-5510"]
OTHER_LINES = ["USCIS", "ATTN: I-290B", "P.O. BOX 21100", "PHOENIX, AZ 85036-1100"]

SECTIONS = [
    ("The decision", "the attorney", [
        ("motion.form", "The form USCIS decided (for example I-485, I-360, I-765)", TEXT, True),
        ("motion.receipt", "Its receipt number", TEXT, True),
        ("motion.decision_date", "Date of the decision (on the notice)", DATE, True),
        ("motion.mailed", "USCIS mailed the decision? (then 33 days instead of 30)", YES_NO, True),
        ("motion.office", "Office that decided (on the notice)", {"type": "choice", "options": list(OFFICES)}, True),
    ]),
    ("The request", "the attorney", [
        ("motion.kind", "Appeal or motion (Part 2: exactly one)", {"type": "choice", "options": KINDS}, True),
        ("motion.basis", "Part 3: the basis, in brief (the full argument goes in the brief)", LINES, True),
        ("motion.classification", "Requested classification, if the form asks for one (Part 2, item 5)", TEXT, False),
    ]),
]


def _form(graph) -> str:
    return str(value(graph, "motion.form") or "").upper().replace("FORM", "").strip()


def denial(graph) -> dict[str, Any] | None:
    """The latest USCIS denial in the case that nothing came after."""
    import journey

    ns = journey.notices(graph)
    return next((n for n in reversed(ns) if n["kind"] == "denial" and n["form"] != "N-400"
                 and not any(m["receipt"] == n["receipt"] and (m["date"] or "") > (n["date"] or "") for m in ns)), None)


def derive(graph, today: date):
    put = putter(graph, "motion.derive")
    d = denial(graph)
    if d:
        put("motion.form", d["form"], f"the denial notice of {us(_d(d['date']))}")
        put("motion.receipt", d["receipt"], "the denial notice")
        put("motion.decision_date", (_d(d["date"]) or today).isoformat(), "the denial notice's date")
    addresses(graph, put, "motion")
    office = value(graph, "motion.office")
    put("motion.office_code", OFFICES.get(office), "the office answered above, as the form's drop-down stores it")
    return graph


def due(graph) -> date | None:
    decided = _d(value(graph, "motion.decision_date"))
    if not decided:
        return None
    return decided + timedelta(days=33 if value(graph, "motion.mailed") == "Yes" else 30)  # unknown: the earlier day


def fee(graph, today: date) -> tuple[int | None, str]:
    import fees

    paper = fees.load(today).get("paper") or {}
    if sij(graph):
        return paper.get("i290b_sij"), "SIJ: no fee for a motion or appeal on a benefit request filed before adjusting, or a motion on the I-485 (G-1055)"
    return paper.get("i290b"), "the I-290B fee (G-1055)"


def mail_to(graph) -> tuple[list[str], str]:
    if sij(graph) and _form(graph) in ("I-360", "I-485"):
        return SIJ_LINES, "an SIJ I-360 or I-485: USCIS Chicago lockbox (USCIS's I-290B filing addresses)"
    return OTHER_LINES, "any other USCIS decision: USCIS Phoenix lockbox (USCIS's I-290B filing addresses)"


def notes(graph, today: date) -> list[dict[str, str]]:
    out = []
    last = due(graph)
    if last:
        out.append({"level": "warn" if today > last - timedelta(days=7) else "info", "title": "When",
                    "text": f"USCIS must receive it by {us(last)} ({'33 days: the decision was mailed' if value(graph, 'motion.mailed') == 'Yes' else '30 days'}; "
                            "8 CFR 103.8(b): the date of service is the date USCIS mailed it)."})
    amount, why = fee(graph, today)
    out.append({"level": "info", "title": "Fee", "text": f"{money(amount)}: {why}. One I-290B, and one fee, for each decision."})
    lines, why = mail_to(graph)
    out.append({"level": "info", "title": "Where", "text": " / ".join(lines) + f": {why}. Never to the AAO directly."})
    if sij(graph) and _form(graph) in ("I-360", "I-485"):
        out.append({"level": "info", "title": "SIJ", "text": "Don't include a copy of the original I-360 or I-485 (USCIS's I-290B filing addresses)."})
    return out


@producer(ATTORNEY)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    out = []
    form, kind = _form(graph), str(value(graph, "motion.kind") or "")
    rule = APPEAL.get(form)
    if form == "N-400":
        out.append(held(OFFICE, "A denied N-400 gets a hearing on Form N-336, not an I-290B: use More… → Hearing on a denied N-400 (N-336)."))
    if form in NO_MOTION:
        out.append(f"USCIS's chart: an {form} decision can be neither appealed nor reopened on Form I-290B.")
    if kind.startswith("Appeal"):
        if rule is False:
            out.append(held(OFFICE, f"USCIS's chart: an {form} decision can't be appealed: a motion to reopen or reconsider only."))
        elif rule == "EOIR-29":
            out.append(held(OFFICE, "An I-130's appeal goes to the Board of Immigration Appeals on Form EOIR-29, not the I-290B, or choose a motion."))
        elif rule is None and form == "I-131":
            out.append("An I-131 can be appealed only for a reentry permit or a refugee travel document (USCIS's chart): check the decision.")
    last = due(graph)
    if last and today > last:
        out.append(f"Past the deadline ({us(last)}): USCIS rejects a late appeal unless it meets a motion's requirements, and denies a late motion "
                   "unless a late motion to reopen's delay was reasonable and beyond the client's control.")
    return out


def letter(graph, today: date) -> dict[str, Any]:
    amount, _why = fee(graph, today)
    lines, _ = mail_to(graph)
    import fees

    edition = fees.load(today).get("edition") or "current"
    kind = str(value(graph, "motion.kind") or "Appeal or Motion")
    text = (f"No filing fee is required for this Form I-290B: the applicant is seeking or was granted Special Immigrant Juvenile classification "
            f"(Form G-1055, Fee Schedule, edition {edition})." if amount == 0 else
            f"Enclosed is the filing fee of {money(amount)} for Form I-290B, paid by the enclosed Form G-1450 (Form G-1055, edition {edition})."
            if amount else "Filing fee: [the attorney sets it. See the packet's problems].")
    return {"re_lines": [f"Form I-290B, Notice of Appeal or Motion: {kind.split(': ')[0]}",
                         f"Decision on Form {_form(graph) or '[form]'}, Receipt Number {value(graph, 'motion.receipt') or '[receipt]'}"],
            "mail_to": lines, "fees": text, "no_payment": amount == 0}
