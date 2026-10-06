"""Form I-131 for the firm's clients: advance parole while an I-485 or I-589
is pending, a refugee travel document for an asylee (or a resident who was
one), and a reentry permit for a resident planning a long trip.

What the case already decides: which document (a pending I-485 or I-589 in
the folder), the receipt number Part 1 asks for, the fee (Form G-1055: none
at all for a person seeking or granted SIJ classification), and the filing
address -- USCIS's I-131 chart sends a reentry permit, a refugee travel
document and advance parole for an I-589 to the non-family lockbox by state,
and advance parole filed alone for an I-485 by the receipt's first letters
(MSC, IOE or none: the family-based chart; EAC, SRC, LIN, WAC: non-family).

The client must not leave the U.S. before the document is approved (and
for a reentry permit or refugee travel document, before biometrics):
leaving with an I-485 pending and no advance parole abandons it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from filing_questions import (DATE, LINES, TEXT, YES_NO, has_doc, iso, latest_notice, lockbox, money, pending, plus_years, putter, sij,
                              state_of, us, value, addresses)
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

TITLE = "I-131 (travel document)"
REENTRY, RTD, RTD_LPR = "Reentry permit", "Refugee travel document (refugee or asylee)", "Refugee travel document (resident through refugee or asylee status)"
AP_I485, AP_I589 = "Advance parole (pending I-485)", "Advance parole (pending I-589)"
TYPES = [AP_I485, AP_I589, RTD, RTD_LPR, REENTRY]
TIME_OUTSIDE = ["Less than 6 months", "6 months to 1 year", "1 to 2 years", "2 to 3 years", "3 to 4 years", "More than 4 years"]
def _ap(kind: Any) -> bool:
    return str(kind or "").startswith("Advance parole")


def _rtd(kind: Any) -> bool:
    return str(kind or "").startswith("Refugee travel document")


def _type(graph) -> Any:
    return value(graph, "i131.type")


MORE_QUESTIONS = "Choose the document first (Part 1): its own questions (the trip, where it is sent, Part 6) appear then."
# Each section's last element: when it is shown (src/filing_questions.py) -- a document's own questions appear once it is chosen.
SECTIONS = [
    ("The document", "the attorney", [
        ("i131.type", "Part 1 · Which document", {"type": "choice", "options": TYPES}, True),
        ("i131.sij", "No fee: is the client seeking or granted Special Immigrant Juvenile classification?", YES_NO, False),
        ("i131.in_proceedings", "Part 4, 1 · Ever in exclusion, deportation, removal or rescission proceedings?", YES_NO, True),
        ("i131.prior_reentry_or_rtd", "Part 4, 2.a · Ever issued a reentry permit or refugee travel document?", YES_NO, True),
        ("i131.prior_reentry_or_rtd_date", "Part 4, 2.b · Date the last one was issued", DATE, False),
        ("i131.prior_reentry_or_rtd_disposition", "Part 4, 2.c · What happened to it (attached, lost, stolen, still with the client...)", TEXT, False),
        ("i131.prior_advance_parole", "Part 4, 3.a · Ever issued an advance parole document?", YES_NO, True),
        ("i131.prior_advance_parole_date", "Part 4, 3.b · Date the last one was issued", DATE, False),
        ("i131.prior_advance_parole_disposition", "Part 4, 3.c · What happened to it", TEXT, False),
        ("i131.replacement", "Part 4, 4 · Replacing a document that was lost, stolen, damaged, never received or wrong?", YES_NO, True),
    ]),
    ("Advance parole: the pending application and the trip", "the attorney", [
        ("i131.filed_with_i485", "Mailed together with the I-485 (same envelope)?", YES_NO, False),
        ("i131.i485_receipt", "Part 1, 5.A · The I-485 receipt number (advance parole filed after the I-485)", TEXT, False),
        ("i131.i485_fee_paid", "Was the I-485 filed with its fee (not waived)?", YES_NO, False),
        ("i131.i589_receipt", "Part 1, 5.B · The I-589 receipt number", TEXT, False),
        ("i131.refugee_status", "Part 1, 13 · Is the client a refugee, paroled as a refugee, or a resident as a direct result of being a refugee?", YES_NO, True),
        ("i131.departure_date", "Part 7, 1 · Date the client plans to leave", DATE, False),
        ("i131.trip_purpose", "Part 7, 2 · Purpose of the trip", LINES, False),
        ("i131.trip_countries", "Part 7, 3 · Countries the client will visit", TEXT, False),
        ("i131.trips", "Part 7, 4 · One trip or more than one", {"type": "choice", "options": ["One trip", "More than one trip"]}, False),
        ("i131.trip_days", "Part 7, 5 · Expected length of the trip (days)", TEXT, False),
    ], lambda g: _ap(_type(g))),
    ("Reentry permit or refugee travel document: where it is sent", "the attorney", [
        ("i131.time_outside", "Part 5 · Reentry permit: total time outside the U.S. since becoming a resident (or in the last 5 years)",
         {"type": "choice", "options": TIME_OUTSIDE}, False),
        ("i131.delivery", "Part 4, 7 · Where the document is sent",
         {"type": "choice", "options": ["To the U.S. address in Part 2", "To a U.S. embassy or consulate abroad"]}, False),
        ("i131.delivery_city", "Part 4, 7.b · The embassy or consulate: city", TEXT, False),
        ("i131.delivery_country", "Part 4, 7.b · The embassy or consulate: country", TEXT, False),
    ], lambda g: _type(g) == REENTRY or _rtd(_type(g))),
    ("Refugee travel document", "the attorney", [
        ("i131.status_basis", "The client is a", {"type": "choice", "options": ["Refugee", "Asylee"]}, False),
        ("i131.refugee_country", "Part 6, 1 · Country the client is a refugee or asylee from", TEXT, False),
        ("i131.plan_travel_to_that_country", "Part 6, 2 · Plans to travel to that country?", YES_NO, False),
        ("i131.returned_to_that_country", "Part 6, 3.a · Since becoming a refugee or asylee, ever returned to that country?", YES_NO, False),
        ("i131.passport_from_that_country", "Part 6, 3.b · Applied for or obtained that country's passport, renewal or entry permit?", YES_NO, False),
        ("i131.benefit_from_that_country", "Part 6, 3.c · Applied for or received any benefit from that country (e.g. health insurance)?", YES_NO, False),
        ("i131.reacquired_nationality", "Part 6, 4.a · Reacquired that country's nationality?", YES_NO, False),
        ("i131.new_nationality", "Part 6, 4.b · Acquired a new nationality?", YES_NO, False),
        ("i131.status_elsewhere", "Part 6, 4.c · Granted refugee or asylee status in any other country?", YES_NO, False),
        ("i131.filing_before_departure", "Part 6, 5 · Filing before leaving the U.S.?", YES_NO, False),
        ("i131.outside_us_now", "Part 6, 6.a · Outside the U.S. now?", YES_NO, False),
    ], lambda g: _rtd(_type(g))),
]
REFUGEE_ITEMS = [key for key, *_ in SECTIONS[3][2] if key != "i131.status_basis"]



def derive(graph, today: date):
    put = putter(graph, "travel.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    addresses(graph, put, "i131")
    put("i131.mailing_country", "USA" if v("i131.mailing_street") else None, "a U.S. address")
    put("i131.physical_country", "USA" if v("i131.physical_street") else None, "a U.S. address")
    i485, i589 = pending(graph, "I-485"), pending(graph, "I-589")
    asylum_granted = latest_notice(graph, "I-589", "approval")
    resident = latest_notice(graph, "I-485", "approval")
    if i485:
        put("i131.type", AP_I485, f"the I-485 is pending (receipt {i485['receipt']})")
    elif i589:
        put("i131.type", AP_I589, f"the I-589 is pending (receipt {i589['receipt']})")
    elif asylum_granted and not resident:
        put("i131.type", RTD, "asylum was granted (the I-589 approval)")
    if i485:
        put("i131.i485_receipt", i485["receipt"], "the I-485 receipt notice")
    if i589:
        put("i131.i589_receipt", i589["receipt"], "the I-589 receipt notice")
    kind = v("i131.type")
    if _ap(kind):
        put("i131.refugee_status", "No", "advance parole for a pending application: change it if the client was admitted or paroled as a refugee")
    if _rtd(kind) and asylum_granted:
        put("i131.status_basis", "Asylee", "the I-589 approval")
        put("i131.refugee_status", "No", "an asylee, not a refugee (Part 1, 13 asks about refugees only)")
        put("i131.refugee_country", v("applicant.citizenship"), "the client's nationality: check it is the country of the asylum claim")
    if _rtd(kind) or kind == REENTRY:
        put("i131.delivery", "To the U.S. address in Part 2", "sent to the client's U.S. address (the usual choice)")
        put("i131.filing_before_departure", "Yes" if _rtd(kind) else None, "filed from the U.S. before the trip")
        put("i131.outside_us_now", "No" if _rtd(kind) else None, "filed from the U.S.")
    put("i131.in_proceedings", "Yes" if v("applicant.nta_present") or v("applicant.part9.in_removal_proceedings") == "Yes" else None,
        "a Notice to Appear in the case")
    put("i131.replacement", "No", "a new document, not a replacement (change it if needed)")
    put("i131.sij", "Yes" if sij(graph) else None, "the Special Immigrant Juvenile case (G-1055: no I-131 fee)")
    return graph


def fee(graph, today: date) -> tuple[int | None, str]:
    """(the fee in dollars, why) from Form G-1055 -- None when it can't be told yet."""
    import fees

    paper = fees.load(today)["paper"]
    kind = value(graph, "i131.type")
    if value(graph, "i131.sij") == "Yes":
        return paper.get("i131_sij", 0), "no fee: the client is seeking or has been granted Special Immigrant Juvenile classification (Form G-1055, I-131 general fee exemptions)"
    import t_visa

    if t_visa.seeking(graph):  # the same G-1055 exemption for a person seeking or granted T status (src/t_visa.py)
        return paper.get("i131_t", 0), "no fee: the client is seeking or has been granted T nonimmigrant status (Form G-1055, I-131 general fee exemptions)"
    if value(graph, "i131.replacement") == "Yes":
        return None, "a replacement: no fee only when USCIS made the error or the document never arrived. The attorney sets the fee"
    if kind == REENTRY:
        return paper.get("i131_reentry"), "reentry permit"
    if _ap(kind):
        filed = iso((latest_notice(graph, "I-485", "receipt") or {}).get("date")) if kind == AP_I485 else None
        if filed and date(2007, 7, 30) <= filed < date(2024, 4, 1) and value(graph, "i131.i485_fee_paid") != "No":
            return paper.get("i131_i485_paid_before_2024_04_01", 0), f"no fee: the I-485 was filed on {us(filed)}, before April 1, 2024, with its fee, and is still pending (Form G-1055)"
        return paper.get("i131_advance_parole"), "advance parole"
    if _rtd(kind):
        if value(graph, "i131.status_basis") == "Refugee":
            return paper.get("i131_rtd_refugee", 0), "no fee: a refugee travel document for a refugee (Form G-1055)"
        if value(graph, "i131.status_basis") == "Asylee":
            dob = iso(value(graph, "applicant.dob"))
            if not dob:
                return None, "asylee: $135 under 16, $165 at 16 and over. The date of birth is missing"
            sixteen = plus_years(dob, 16) <= today
            return paper.get("i131_rtd_asylee_16plus" if sixteen else "i131_rtd_asylee_under16"), f"refugee travel document, asylee {'16 or older' if sixteen else 'under 16'}"
        return None, "refugee travel document: set whether the client is a refugee or an asylee"
    return None, "choose the document first"


def address(graph) -> tuple[list[str] | None, str]:
    """(the USCIS address lines, which chart and why) -- None when it can't be told."""
    kind, state = value(graph, "i131.type"), state_of(graph)
    if not kind:
        return None, "choose the document first"
    if kind == AP_I485 and value(graph, "i131.filed_with_i485") == "Yes":
        lines, box = lockbox("uscis_lockboxes", state)
        return lines, f"filed with the I-485: the I-485's address ({box or 'state not on the chart'})"
    if kind == AP_I485:
        receipt = str(value(graph, "i131.i485_receipt") or "").upper().replace(" ", "")
        if not receipt:
            return None, "advance parole filed alone: the I-485 receipt number decides the address (MSC/IOE: family-based chart; EAC/SRC/LIN/WAC: non-family chart)"
        chart = "uscis_lockboxes_nfb" if receipt[:3] in ("EAC", "SRC", "LIN", "WAC") else "uscis_lockboxes"
        lines, box = lockbox(chart, state)
        return lines, (f"the I-485 receipt starts {receipt[:3]}: the {'non-family' if 'nfb' in chart else 'family-based'} lockbox chart"
                       f" ({box or 'state not on the chart'})")
    if _rtd(kind) and value(graph, "i131.outside_us_now") == "Yes":
        return (["USCIS REFUGEE AND INTERNATIONAL OPERATIONS", "ATTN: RTD", "999 NORTH CAPITOL ST. NE", "MAIL STOP 2292", "WASHINGTON, DC 20529-2295"],
                "a refugee travel document filed from outside the U.S. (less than 1 year away)")
    lines, box = lockbox("uscis_lockboxes_nfb", state)
    return lines, f"the non-family lockbox chart, by state ({box or 'state not on the chart'})"


def notes(graph, today: date) -> list[dict[str, str]]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    amount, why = fee(graph, today)
    out.append({"level": "info" if amount is not None else "warn", "title": "Fee", "text": (f"{money(amount)} (paper): {why}." if amount is not None else why + ".")
                + (" CBP may also charge the Pub. L. 119-21 parole fee at the port of entry." if _ap(v("i131.type")) else "")})
    lines, why = address(graph)
    out.append({"level": "info" if lines else "warn", "title": "Where it is filed", "text": (" / ".join(lines) + f" ({why})") if lines else why})
    if _ap(v("i131.type")):
        out.append({"level": "warn", "title": "Before the client travels",
                    "text": "The client must not leave the U.S. until the advance parole document is approved and in hand. "
                            "Leaving while the I-485 is pending without it abandons the I-485; with an I-589 pending, travel to the country of feared "
                            "persecution can end the asylum claim. A client who has been unlawfully present needs the attorney's advice before any trip."})
    elif v("i131.type") == REENTRY:
        out.append({"level": "info", "title": "Before the client travels",
                    "text": "File while the client is in the U.S. and stay for the biometrics appointment; the permit can be sent to a U.S. embassy or consulate to pick up."})
    elif _rtd(v("i131.type")):
        out.append({"level": "warn", "title": "Before the client travels",
                    "text": "Stay for the biometrics appointment. Travel to the country the client fled (or using its passport) can end asylee or refugee status."})
    departure = iso(v("i131.departure_date"))
    if departure:
        days = (departure - today).days
        out.append({"level": "warn" if days < 120 else "info", "title": "The trip",
                    "text": f"Leaves {us(departure)} ({days} days from today). USCIS often takes months to decide an I-131: an urgent trip needs an expedite request or an emergency appointment."})
    return out


@producer(CLIENT)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    kind, out = v("i131.type"), []
    if kind == AP_I485 and not pending(graph, "I-485") and v("i131.filed_with_i485") != "Yes":
        if latest_notice(graph, "I-485", "approval"):
            out.append(held(OFFICE, "The I-485 is approved: the client is a resident and travels with the green card. Advance parole is no longer the document."))
        elif not v("i131.i485_receipt"):
            out.append("Advance parole filed after the I-485 needs its receipt number (Part 1, 5.A) and a copy of the receipt notice.")
    if kind == AP_I589 and not v("i131.i589_receipt"):
        out.append("Advance parole for a pending I-589 needs the I-589 receipt number (Part 1, 5.B) and a copy of the receipt notice.")
    if _ap(kind):
        missing = [label for key, label in (("i131.departure_date", "the departure date"), ("i131.trip_purpose", "the purpose"),
                                             ("i131.trip_countries", "the countries"), ("i131.trips", "one trip or more"),
                                             ("i131.trip_days", "the length of the trip")) if not v(key)]
        if missing:
            out.append("Part 7 (the trip) is not complete: " + ", ".join(missing) + ".")
    if _rtd(kind):
        unanswered = [key.split(".")[1].replace("_", " ") for key in REFUGEE_ITEMS if not v(key)]
        if unanswered:
            out.append(f"Part 6 (refugee travel document) has {len(unanswered)} unanswered question{'s' if len(unanswered) != 1 else ''}.")
        if not v("i131.status_basis"):
            out.append("Refugee travel document: is the client a refugee or an asylee? It sets the fee.")
        for key in ("i131.returned_to_that_country", "i131.passport_from_that_country", "i131.reacquired_nationality", "i131.plan_travel_to_that_country"):
            if v(key) == "Yes":
                out.append(held(ATTORNEY, f"Part 6: \"Yes\" to {key.split('.')[1].replace('_', ' ')}: can end refugee or asylee status. The attorney reviews and explains in Part 13 before filing."))
        if kind == RTD_LPR and not latest_notice(graph, "I-485", "approval") and not has_doc(client_dir, "green_card"):
            out.append("A resident through refugee or asylee status: a copy of the green card goes with the I-131, not in the folder.")
    if kind == REENTRY:
        if not (latest_notice(graph, "I-485", "approval") or has_doc(client_dir, "green_card")):
            out.append("A reentry permit is for a permanent resident: no green card or I-485 approval in the folder.")
        if not v("i131.time_outside"):
            out.append("Part 5: the client's total time outside the U.S. (reentry permit).")
    if v("i131.prior_reentry_or_rtd") == "Yes" and not v("i131.prior_reentry_or_rtd_date"):
        out.append("Part 4, 2.b: the date the last reentry permit or refugee travel document was issued.")
    if v("i131.prior_advance_parole") == "Yes" and not v("i131.prior_advance_parole_date"):
        out.append("Part 4, 3.b: the date the last advance parole document was issued.")
    if v("i131.delivery") == "To a U.S. embassy or consulate abroad" and not (v("i131.delivery_city") and v("i131.delivery_country")):
        out.append("Part 4, 7.b: the city and country of the embassy or consulate where the document will be picked up.")
    if v("i131.in_proceedings") == "Yes" and _ap(kind):
        out.append(held(ATTORNEY, "The client is or was in removal proceedings: advance parole and travel need the attorney's review (travel after a removal order can execute it)."))
    if v("i131.replacement") == "Yes":
        out.append(held(OFFICE, "A replacement document: Part 4, 5-6 (why, what is wrong, the old receipt number) are filled by hand."))
    if not has_doc(client_dir, "passport", "drivers_license", "state_id", "green_card", "work_permit"):
        out.append("Every I-131 needs a copy of a government photo ID (passport, driver's license, green card or work permit): none in the folder.")
    amount, why = fee(graph, today)
    if amount is None:
        out.append(held(OFFICE, f"The fee can't be set yet: {why}."))
    lines, why = address(graph)
    if lines is None:
        out.append(held(OFFICE, f"The filing address can't be set yet: {why}."))
    return out


def letter(graph, today: date) -> dict[str, Any]:
    """The cover letter's subject, address and fee for this client."""
    kind = value(graph, "i131.type") or "Travel document"
    amount, why = fee(graph, today)
    lines, _ = address(graph)
    fee_text = ("No filing fee is due for this application: " + why.removeprefix("no fee: ").replace("the client", "the applicant") + "." if amount == 0 else
                f"Enclosed is the filing fee of {money(amount)} for Form I-131 ({kind.lower()}), per Form G-1055, edition 10/01/26." if amount else
                "Filing fee: [the attorney sets it. See the packet's problems].")
    return {"re_lines": [f"Application: I-131 Application for Travel Documents, Parole Documents, and Arrival/Departure Records ({kind})"],
            "mail_to": lines or ["[USCIS address: see the packet's problems]"], "fees": fee_text, "no_payment": amount == 0,
            "drop_documents": [] if photos(graph) else ["photos"]}


def photos(graph) -> bool:
    """Two passport photos: advance parole, or a refugee travel document filed from outside the U.S. (I-131 Instructions)."""
    kind = value(graph, "i131.type")
    return _ap(kind) or (_rtd(kind) and value(graph, "i131.outside_us_now") == "Yes")
