"""DACA renewals: Form I-821D, Consideration of Deferred Action for Childhood
Arrivals (edition 01/20/25), with its Form I-765 under category (c)(33) and
the Form I-765WS worksheet (edition 08/21/25). Read on 10/02/2026 from:

  What USCIS takes today: uscis.gov/DACA (last updated 01/24/2025), its
    "Important Update" on the Fifth Circuit's decision of 01/17/2025, also on
    uscis.gov/i-821d (updated 06/01/2026): USCIS "will continue to accept and
    process DACA renewal requests and accompanying applications for employment
    authorization under the DACA regulations at 8 CFR 236.22 and 236.23" and
    "will continue to accept initial requests but will not process initial
    DACA requests at this time." So the system builds renewals; an initial
    request stops the packet with that sentence.
  Renewal or initial: the DACA litigation FAQ (updated 12/31/2024), Q5, Q6
    and Q17-Q18, and the I-821D Instructions' NOTE: a renewal is a request by
    someone with a current DACA grant, or filed within one year after it
    expired; more than a year after it expired, or after a termination, it
    is an initial request.
  When: the alert on both pages: USCIS "strongly encourage[s]" filing "between
    120 and 150 days (4 to 5 months) before the expiration date located on
    your current Form I-797 DACA approval notice"; and the Instructions'
    CAUTION: filed more than 150 days before, USCIS "may reject your
    submission and return it". The expiration date is also the work permit's
    (Filing Tips for DACA, updated 04/30/2025, Part 1 items 1-2). An expired
    grant leaves a gap with no DACA until the renewal is approved (FAQ A19).
  What goes in: the I-821D, the I-765 with (c)(33) in Part 2, 27 and the
    I-765WS, with the fees, in one package: without them USCIS rejects it
    (uscis.gov/DACA; the I-765 Instructions 08/21/25, item 7). A renewal sends
    only new documents about removal proceedings or arrests not sent before
    (the Instructions' "Evidence for Renewal Requests Only"); the I-765's
    renewal box asks for a copy of the previous work permit.
  Rules: 8 CFR 236.21-236.25 (eCFR, current as of 09/30/2026): 236.22(b)
    the threshold criteria (unauthorized travel on or after 08/15/2012 breaks
    continuous residence; the criminal history bars); 236.23(a) filed with
    the (c)(33) work permit request, two-year grants, no approval while in
    detention; 236.23(c)(3) no appeal or motion.
  Fees (Form G-1055, edition 10/01/26): the I-821D "General filing $85"
    (p. 24); the I-765 (c)(33) "Paper Filing: $520, Online Filing: $470"
    (Appendix C, p. 50). Each is its own payment. No fee waiver (uscis.gov/DACA).
  Where: USCIS "Direct Filing Addresses for Form I-821D" (updated 09/24/2024):
    by state, schemas/law/uscis_lockboxes_daca.json. Massachusetts files at the
    Chicago lockbox, Florida at Dallas. A lockbox: the G-1145 goes on top.
  Online: the Filing Tips page says DACA renewals can also be filed online
    (Forms I-821D and I-765 in a USCIS online account). This packet is the
    paper filing; its filing id is "daca".

Every client-facing sentence here is DRAFT for the attorney.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from filing_questions import (DATE, LINES, TEXT, YES_NO, addresses, has_doc, latest_notice, lockbox, money, pending, plus_years, putter,
                              state_of, us, value)
from filing_questions import iso as _d
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

TITLE = "DACA renewal (I-821D, I-765 and I-765WS)"
RENEWAL, INITIAL = "Renewal", "Initial request"
NOT_DETAINED, DETAINED = "Not in immigration detention", "In immigration detention"
PROCEEDINGS = ["Currently in proceedings (active)", "Currently in proceedings (administratively closed)", "Terminated", "Subject to a final order",
               "Other (explained in Part 8)"]
CHART = "uscis_lockboxes_daca"
STATUS = ("USCIS accepts and processes DACA renewal requests and their work permits; it accepts initial requests but does not process them "
          "(uscis.gov/DACA, updated 01/24/2025).")
NOT_PROCESSED = ("An initial DACA request: USCIS accepts it but does not process it now (the Texas court order; uscis.gov/DACA, updated "
                 "01/24/2025). The system builds renewals only. The attorney decides with the client.")
C33 = ("c", "33", "")  # Form I-765, Part 2, 27: the three boxes
PART4 = [("daca.p4_q1_arrested_us", "1. Ever arrested for, charged with or convicted of a felony or misdemeanor in the U.S. (juvenile court included; "
                                    "not minor traffic unless alcohol or drugs)?"),
         ("daca.p4_q2_arrested_abroad", "2. Ever arrested for, charged with or convicted of a crime in any other country?"),
         ("daca.p4_q3_terrorism", "3. Ever engaged in, or plan to engage in, terrorist activities?"),
         ("daca.p4_q4_gang", "4. Now or ever a member of a gang?"),
         ("daca.p4_q5a_torture_genocide_trafficking", "5.A. Ever took part in torture, genocide or human trafficking?"),
         ("daca.p4_q5b_killing", "5.B. Ever took part in killing any person?"),
         ("daca.p4_q5c_injuring", "5.C. Ever took part in severely injuring any person?"),
         ("daca.p4_q5d_forced_sexual_contact", "5.D. Ever took part in sexual contact with a person forced or threatened?"),
         ("daca.p4_q6_child_soldiers_recruited", "6. Ever recruited or used a person under 15 to serve an armed force or group?"),
         ("daca.p4_q7_child_soldiers_used", "7. Ever used a person under 15 in hostilities or to help people in combat?")]


def _moved(g) -> bool:
    return value(g, "daca.moved") == "Yes"


def _left(g) -> bool:
    return value(g, "daca.left_us") == "Yes"


def _in_proceedings(g) -> bool:
    return value(g, "daca.q5_proceedings") == "Yes"


def _arrested(g) -> bool:
    return "Yes" in (value(g, "daca.p4_q1_arrested_us"), value(g, "daca.p4_q2_arrested_abroad"))


SECTIONS = [
    ("The request (Part 1)", "the attorney", [
        ("daca.request_type", "Part 1, 1-2: initial request or renewal", {"type": "choice", "options": [RENEWAL, INITIAL]}, True),
        ("daca.expires", "Part 1, 2: the current DACA expires on (the I-797 approval notice; the work permit's date too)", DATE, True),
        ("daca.terminated", "Did USCIS ever terminate the client's DACA?", YES_NO, True),
        ("daca.detention", "Part 1: in immigration detention now?", {"type": "choice", "options": [NOT_DETAINED, DETAINED]}, True),
        ("daca.q5_proceedings", "Part 1, 5: now or ever in removal proceedings, or a removal order from any other context (the border, an officer)?",
         YES_NO, True),
    ]),
    ("Removal proceedings (Part 1, 6)", "the attorney", [
        ("daca.q6_status", "6.A-6.E: their status or outcome", {"type": "choice", "options": PROCEEDINGS}, True),
        ("daca.q6_date", "6.F: the most recent date of the proceedings", DATE, True),
        ("daca.q6_location", "6.G: where they were", TEXT, True),
    ], _in_proceedings),
    ("Residence and travel since the last approved DACA request (Part 2)", "the attorney", [
        ("daca.continuous_residence", "Part 2, 1: continuously residing in the U.S. since at least 06/15/2007, up to now?", YES_NO, True),
        ("daca.present_since", "Part 2, 2.A: at the present address since", DATE, True),
        ("daca.moved", "Lived at any other address since the last approved DACA request?", YES_NO, True),
        ("daca.left_us", "Left the U.S. since the last approved DACA request?", YES_NO, True),
        ("daca.q8_without_advance_parole", "Part 2, 8: left the U.S. without advance parole on or after 08/15/2012?", YES_NO, True),
    ]),
    ("The other address (Part 2, 3)", "the paralegal", [
        ("daca.prev_street", "3.B: street number and name", TEXT, True),
        ("daca.prev_apt", "3.C: apartment, suite or floor number", TEXT, False),
        ("daca.prev_city", "3.D: city or town", TEXT, True),
        ("daca.prev_state", "3.E: state (two letters)", TEXT, True),
        ("daca.prev_zip", "3.F: ZIP code", TEXT, True),
        ("daca.prev_from", "3.A: lived there from", DATE, True),
        ("daca.prev_to", "3.A: lived there until", DATE, True),
    ], _moved),
    ("The trip (Part 2, 6)", "the paralegal", [
        ("daca.trip_departed", "6.A: left the U.S. on", DATE, True),
        ("daca.trip_returned", "6.B: came back on", DATE, True),
        ("daca.trip_reason", "6.C: the reason for the trip", TEXT, True),
    ], _left),
    ("Criminal, national security and public safety (Part 4)", "the attorney", [(key, label, YES_NO, True) for key, label in PART4]),
    ("Court records (Part 4, 1-2)", "the attorney", [
        ("daca.records_sent_before", "The arrest and court records were already sent with an earlier DACA request?", YES_NO, True),
    ], _arrested),
    ("Economic need (Form I-765WS)", "the paralegal", [
        ("daca.ws_income", "1. The client's current annual income, in dollars (numbers only)", TEXT, True),
        ("daca.ws_expenses", "2. The client's current annual expenses, in dollars (numbers only)", TEXT, True),
        ("daca.ws_assets", "3. The total current value of the client's assets, in dollars (numbers only)", TEXT, True),
        ("daca.ws_explanation", "Part 3: an explanation of the client's economic need (optional)", LINES, False),
    ]),
]
MORE_QUESTIONS = "Answer the Yes/No questions first: the proceedings, the other address, the trip and the court records appear when they apply."


# -- the DACA grant and its renewal window -------------------------------------------

def _rules() -> dict[str, Any]:
    """The renewal window and the one year after expiry, from schemas/registers/journey.json (sources there and above)."""
    import journey

    cfg = journey.settings()["deadlines"]
    return {"earliest": cfg["daca_renewal_earliest_days"], "by": cfg["daca_renewal_by_days"], "grace_years": cfg["daca_renewal_grace_years"]}


def _c33(graph) -> bool:
    return re.sub(r"[^A-Z0-9]", "", str(value(graph, "applicant.ead_category") or "").upper()) == "C33"


def recipient(graph) -> bool:
    """A DACA case: a (c)(33) work permit, an I-821D notice, a DACA answer, or the category says so."""
    import journey

    category = str(value(graph, "applicant.filing_category") or "").lower()
    return bool(_c33(graph) or any(n["form"] == "I-821D" for n in journey.notices(graph)) or value(graph, "daca.expires")
                or "childhood arrivals" in category or "daca" in category)


def expires(graph) -> tuple[date | None, str]:
    """(the current DACA's last day, where it was read): the answer; else the (c)(33) work permit, whose date is the DACA
    period's (Filing Tips for DACA); else, with an I-821D approval in the case, the latest I-765 approval's validity."""
    answered = _d(value(graph, "daca.expires"))
    if answered:
        return answered, "the case"
    if _c33(graph) and _d(value(graph, "applicant.ead_expiration_date")):
        return _d(value(graph, "applicant.ead_expiration_date")), "the (c)(33) work permit in the folder"
    if latest_notice(graph, "I-821D", "approval"):
        card = latest_notice(graph, "I-765", "approval")
        if card and _d(card.get("valid_to")):
            return _d(card["valid_to"]), f"the work permit approval {card['receipt']}"
    return None, ""


def window(graph, today: date) -> dict[str, Any] | None:
    """The renewal window for the current grant: opens 150 days before it expires, USCIS asks for it by 120 days before; a renewal
    until one year after it expired. kind: early, open, late, expired (still a renewal), initial (past the year)."""
    end, where = expires(graph)
    if not end:
        return None
    r = _rules()
    opens, by, until = end - timedelta(days=r["earliest"]), end - timedelta(days=r["by"]), plus_years(end, r["grace_years"])
    kind = ("early" if today < opens else "open" if today <= by else "late" if today <= end else "expired" if today <= until else "initial")
    return {"expires": end, "from": where, "opens": opens, "file_by": by, "renewal_until": until, "kind": kind}


def renewal_pending(graph) -> dict[str, Any] | None:
    """An I-821D USCIS has receipted and not decided."""
    return pending(graph, "I-821D")


def window_text(w: dict[str, Any], today: date) -> str:
    """The window in one sentence (the panel, the case page)."""
    end = us(w["expires"])
    return {"early": f"DACA expires {end}: the renewal can be filed from {us(w['opens'])} (150 days before), ideally by {us(w['file_by'])} "
                     f"(120 days before). Not earlier: USCIS may reject it and send it back.",
            "open": f"DACA expires {end}: file the renewal now, by {us(w['file_by'])} (USCIS asks for 120 to 150 days before).",
            "late": f"DACA expires {end}: past USCIS's 120-day mark ({us(w['file_by'])}). File now: a decision may not come before it expires.",
            "expired": f"DACA expired {end}: still a renewal if filed by {us(w['renewal_until'])} (one year after). Until the renewal is approved "
                       f"the client has no DACA and no work permit (USCIS DACA FAQ, A19).",
            "initial": f"DACA expired {end}, more than one year ago: a new request is an initial request."}[w["kind"]]


# -- the filing's facts -----------------------------------------------------------------

def derive(graph, today: date):
    put = putter(graph, "daca.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    end, where = expires(graph)
    put("daca.expires", end.isoformat() if end else None, where)
    w = window(graph, today)
    if w and v("daca.terminated") != "Yes":
        put("daca.request_type", INITIAL if w["kind"] == "initial" else RENEWAL, f"DACA expires {us(w['expires'])} ({w['from']})"
            if w["kind"] != "initial" else f"DACA expired {us(w['expires'])}, more than one year ago")
    elif v("daca.terminated") == "Yes":
        put("daca.request_type", INITIAL, "USCIS terminated the client's DACA")
    renewal = v("daca.request_type") == RENEWAL
    if renewal:
        put("daca.renewal_expires", v("daca.expires"), "a renewal: Part 1, 2")
    if v("applicant.nta_present"):
        put("daca.q5_proceedings", "Yes", "a Notice to Appear is in the case")
    import filing_questions

    if state_of(graph) in filing_questions.STATES.values():
        put("daca.country_of_residence", "UNITED STATES", "the client's U.S. home address")
    addresses(graph, put, "daca")  # Part 1, 4: the U.S. mailing address
    for part in ("street", "unit_type", "apt", "city", "state", "zip"):  # Part 2, 2: the present (home) address
        put(f"daca.present_{part}", v(f"applicant.physical_{part}"), "the client's home address")
    put("daca.present_since", v("applicant.physical_address_since"), "the client's home address")
    # the I-765 (c)(33): the work permit's own map with daca_ead.* keys (schemas/packets/companion_forms.json "i765_daca")
    addresses(graph, put, "daca_ead")
    put("daca_ead.mailing_same", "No" if v("applicant.mailing_same_as_physical") == "No" else "Yes", "the client's addresses")
    reason = v("daca.request_type")
    put("daca_ead.reason", "Renewal" if reason == RENEWAL else "Initial" if reason == INITIAL else None, "the DACA request (Part 1)")
    put("daca_ead.previous_filed", "Yes" if reason == RENEWAL else None, "a DACA renewal: the client filed a (c)(33) I-765 before")
    for n, box in enumerate(C33, start=1):
        if box:
            put(f"daca_ead.category_{n}", box, "DACA: category (c)(33)")
    put("daca_ead.current_status", "Deferred action (DACA)", "DACA (the I-765's own example: 'deferred action')")
    put("companion.preparer_full_name", " ".join(x for x in (v("firm.preparer_given_name"), v("firm.preparer_family_name")) if x) or None, "the firm")
    return graph


def fees(today: date) -> tuple[int | None, int | None]:
    """(the I-821D fee, the I-765 (c)(33) paper fee): Form G-1055, edition 10/01/26."""
    import fees as fee_schedule

    paper = fee_schedule.load(today).get("paper") or {}
    return paper.get("i821d"), paper.get("i765_c33")


def payments(graph, today: date, forms: list[str]) -> list[tuple[str, str, Any, str]]:
    """(form id, form, amount, what): one payment for each request (src/payment.py)."""
    i821d, i765 = fees(today)
    out = [("i821d", "I-821D", i821d, "Form I-821D filing fee")]
    if "i765_daca" in forms:
        out.append(("i765_daca", "I-765", i765, "Form I-765 filing fee, category (c)(33)"))
    return out


def address(graph) -> tuple[list[str] | None, str]:
    state = state_of(graph)
    lines, box = lockbox(CHART, state)
    return lines, (f"the {box} lockbox for {state} (USCIS 'Direct Filing Addresses for Form I-821D', updated 09/24/2024)" if box else
                   f"the client's state ({state or 'unknown'}) isn't on USCIS's DACA filing-address chart: the attorney confirms where it goes")


def _number(raw: Any) -> bool:
    return bool(re.fullmatch(r"\$?\s*\d[\d,]*(\.\d{1,2})?", str(raw or "").strip()))


def notes(graph, today: date) -> list[dict[str, str]]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = [{"level": "info", "title": "What USCIS takes now", "text": STATUS}]
    w = window(graph, today)
    if w:
        out.append({"level": "info" if w["kind"] == "open" else "warn", "title": "When to file", "text": window_text(w, today)})
    i821d, i765 = fees(today)
    out.append({"level": "info", "title": "Fees", "text": f"{money(i821d)} for the I-821D and {money(i765)} for the I-765 (paper; $470 online), each on "
                                                         "its own payment (Form G-1055, edition 10/01/26). No fee waiver; a fee exemption must be "
                                                         "approved by USCIS before filing (uscis.gov/DACA)."})
    lines, where = address(graph)
    out.append({"level": "info" if lines else "warn", "title": "Where it is filed", "text": (" / ".join(lines) + f" ({where})") if lines else where})
    out.append({"level": "info", "title": "Online instead", "text": "USCIS also takes DACA renewals online (the I-821D and the I-765 in a USCIS "
                                                                    "online account: Filing Tips for DACA). This packet is the paper filing."})
    if v("daca.detention") == DETAINED:
        out.append({"level": "warn", "title": "Detention", "text": "USCIS will not approve DACA while the client is detained (8 CFR 236.23(a)(2)): "
                                                                   "tell the deportation officer."})
    yes = [label.split(" ", 1)[0] for key, label in PART4 if v(key) == "Yes"]
    if yes:
        out.append({"level": "warn", "title": "Part 4", "text": f"Yes to {', '.join(yes)}: explain each in Part 8. The attorney weighs the bars of "
                                                                "8 CFR 236.22(b)(6) (a felony, a significant misdemeanor, three or more other "
                                                                "misdemeanors, a threat to national security or public safety) before filing."})
    if v("daca.q8_without_advance_parole") == "Yes":
        out.append({"level": "warn", "title": "Travel without advance parole", "text": "Travel outside the U.S. without advance parole on or after "
                    "08/15/2012 breaks continuous residence (8 CFR 236.22(b)(2)); a departure without advance parole and an entry without "
                    "inspection can end DACA (236.23(d)(2)). The attorney decides before filing."})
    if v("daca.continuous_residence") == "No":
        out.append({"level": "warn", "title": "Continuous residence", "text": "Part 2, 1 is No: a renewal needs continuous residence since the last "
                                                                              "approved request (the I-821D Instructions). The attorney reviews it."})
    return out


@producer(OFFICE)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    w = window(graph, today)
    if v("daca.request_type") == INITIAL or (w and w["kind"] == "initial") or v("daca.terminated") == "Yes":
        out.append(held(ATTORNEY, NOT_PROCESSED + (f" ({window_text(w, today)})" if w and w["kind"] == "initial" else
                                    " (A request after USCIS terminated DACA is an initial request.)" if v("daca.terminated") == "Yes" else "")))
    if not w:
        out.append(held(CLIENT, "The current DACA's expiration date isn't in the case: USCIS rejects a renewal without it (the I-821D's Filing Tips). "
                   "Take it from the I-797 approval notice or the work permit."))
    elif w["kind"] == "early":
        out.append(f"Too early: a renewal filed before {us(w['opens'])} (150 days before DACA expires on {us(w['expires'])}) may be rejected and "
                   "sent back (I-821D Instructions). Build it again from that date.")
    if not v("applicant.a_number"):
        out.append(held(CLIENT, "No A-Number in the case: USCIS rejects a renewal without it (the I-821D's Filing Tips)."))
    if renewal_pending(graph):
        out.append(held(ATTORNEY, f"An I-821D is already pending (receipt {renewal_pending(graph)['receipt']}): don't file another."))
    if _arrested(graph) and v("daca.records_sent_before") == "No" and not has_doc(client_dir, "criminal_record", "police_report"):
        out.append(held(CLIENT, "Part 4, 1 or 2 is Yes: the certified arrest and court records for each incident (not sent with an earlier DACA request) "
                   "go with the renewal, and none is in the folder."))
    bad = [label.split(".", 1)[0] for key, label, *_ in SECTIONS[-1][2][:3] if v(key) is not None and not _number(v(key))]
    if bad:
        out.append(f"Form I-765WS, item {', '.join(bad)}: a dollar amount, numbers only (e.g. 24000).")
    lines, where = address(graph)
    if lines is None:
        out.append(f"The filing address can't be set: {where}.")
    return out


def letter(graph, today: date) -> dict[str, Any]:
    i821d, i765 = fees(today)
    lines, _ = address(graph)
    renewal = value(graph, "daca.request_type") != INITIAL
    text = (f"Enclosed are the filing fees of {money(i821d)} for Form I-821D and {money(i765)} for Form I-765, each paid by its own enclosed "
            "Form G-1450, per Form G-1055, edition 10/01/26.")
    return {"re_lines": [f"Request: I-821D Consideration of Deferred Action for Childhood Arrivals{' (Renewal)' if renewal else ''}",
                         "With: I-765 Application for Employment Authorization, category (c)(33), and I-765WS Worksheet"],
            "mail_to": lines or ["[USCIS address: see the packet's problems]"], "fees": text, "no_payment": False}
