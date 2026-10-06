"""A green card under the Cuban Adjustment Act (CAA) -- Form I-485, Part 2
item 3.f, first box -- and, in the same panel, as a dependent under the
Haitian Refugee Immigration Fairness Act (HRIFA), the third box. Read on
10/02/2026 from:

  The Act of November 2, 1966 (Pub. L. 89-732), section 1 (govinfo.gov,
    80 Stat. 1161): a native or citizen of Cuba, inspected and admitted or
    paroled after January 1, 1959, physically present in the U.S.; the spouse
    and child "regardless of their citizenship and place of birth, who are
    residing with such alien in the United States". The two years of the
    original text are one year since the Refugee Act of 1980 (8 CFR 245.2).
  8 CFR 245.2(a)(2)(ii) (eCFR as of 09/30/2026): not properly filed unless
    inspected and admitted or paroled after January 1, 1959; ineligible
    unless physically present for one year.
  Form I-485 Instructions, edition 09/18/26, "Cuban Adjustment Act (CAA)"
    (pp. 30-31): the evidence (Cuban nativity or citizenship, one year of
    physical presence, the inspection and admission or parole); the year may
    come before the parole; present without inspection, not eligible unless
    DHS paroled the client under INA 212(d)(5)(A); no Supplement A, Form
    I-643 or police clearances. A spouse or child files as a derivative,
    whatever their nationality, and must reside with the principal.
  USCIS, "Green Card for a Cuban Native or Citizen" (updated 07/08/2025): the
    same, the family member's three timings (with the principal, while the
    principal's I-485 is pending, or after it was approved), "unmarried child
    under 21", and the grounds that don't apply (public charge, labor
    certification, documentation).
  HRIFA dependents: 8 CFR 245.15(d) (eCFR as of 09/30/2026) and the I-485
    Instructions (p. 33): a Haitian national, the spouse, child or unmarried
    son or daughter of a principal HRIFA beneficiary; the relationship
    existed when the principal was adjusted and still exists; an unmarried
    son or daughter physically present continuously since December 31, 1995;
    not eligible under any other provision of law. The principals' filing
    period closed on March 31, 2000 (245.15(c)(2)(i)): no principal here.
  The abused spouse or child (CAA or HRIFA): a different box, the 8 U.S.C.
    1367 protections and the VAWA/T/U filing address: NOT built here.

The I-485 boxes (schemas/forms/i485/template.pdf, 09/18/26, by position): Part 2,
3.f /3f0 (CAA) and /3f3 (HRIFA dependent); Part 2, 2 principal or derivative
with the principal's name and A-Number; Part 3, 1.e (no Affidavit of Support:
INA 213A covers family and some employment immigrants, USCIS Policy Manual
Vol. 8, Part G, Ch. 3); Part 9, 56 /7 (CAA) and /11 (HRIFA dependent), both
exempt from public charge (Policy Manual 8 G.3 C; 8 CFR 245.15(e)(1)).

Fees (G-1055, edition 10/01/26): the I-485's general fee; the lower fee under
14 filing with a parent's I-485; the I-765 with a pending I-485 filed with its
fee. Where (USCIS's "Direct Filing Addresses for Form I-485", updated
12/01/2025): CAA to the family-based lockbox chart, HRIFA dependents with
asylum and refugees to the non-family chart.

case_facts() runs on every reviewed case (src/review/state.py), so the I-485
itself carries the category and the exemptions.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from filing_questions import DATE, TEXT, YES_NO, lockbox, money, plus_years, putter, sij, state_of, us, value
from filing_questions import iso as _d
import clock
from holders import ATTORNEY, OFFICE, held, producer

TITLE = "Green card under the Cuban Adjustment Act or HRIFA (I-485)"
CAA, HRIFA = "Cuban Adjustment Act", "HRIFA dependent"  # the I-485 field map's options (Part 2, 3.f; Part 9, 56)
NATIVE, CITIZEN = "Principal: a native of Cuba (born in Cuba)", "Principal: a citizen of Cuba born outside Cuba"
SPOUSE, CHILD = "Spouse of a Cuban principal", "Child of a Cuban principal (unmarried, under 21)"
ADMITTED, PAROLED = "Admitted at a port of entry (inspected)", "Paroled by DHS (INA 212(d)(5)(A))"
I220A, EWI = "Released on Form I-220A (order of release on recognizance)", "Entered without inspection, not paroled"
SON_DAUGHTER = "Unmarried son or daughter (21 or older)"
SINCE = date(1959, 1, 1)  # "subsequent to January 1, 1959" (Pub. L. 89-732 section 1; 8 CFR 245.2(a)(2)(ii))
HRIFA_PRESENCE = "12/31/1995"  # 8 CFR 245.15(d)(5)
CUBA = {"CUBA", "CUB", "REPUBLICA DE CUBA", "REPÚBLICA DE CUBA", "REPUBLIC OF CUBA"}


def _category(graph) -> Any:
    return value(graph, "applicant.filing_category")


def _caa(g) -> bool:
    return _category(g) != HRIFA


def _derivative(g) -> bool:
    return _caa(g) and value(g, "caa.basis") in (SPOUSE, CHILD)


def _under_14(g, today: date | None = None) -> bool:
    born = _d(value(g, "applicant.dob"))
    return bool(born and plus_years(born, 14) > (today or clock.today()))


SECTIONS = [
    ("The category (Part 2)", "the attorney", [
        ("applicant.filing_category", "Part 2, 3.f: the category", {"type": "choice", "options": [CAA, HRIFA]}, True),
    ]),
    ("Cuban Adjustment Act: eligibility (8 CFR 245.2(a)(2)(ii))", "the attorney", [
        ("caa.basis", "Who the client is under the Act (Part 2, 2: principal or derivative)",
         {"type": "choice", "options": [NATIVE, CITIZEN, SPOUSE, CHILD]}, True),
        ("caa.entry", "How the client was let in (the Act needs an inspection and admission, or a parole)",
         {"type": "choice", "options": [ADMITTED, PAROLED, I220A, EWI]}, True),
        ("caa.entry_date", "Date of that admission or parole (after 01/01/1959)", DATE, True),
        ("caa.present_since", "Physically present in the U.S. since (the year may begin before the parole)", DATE, True),
        ("caa.present_one_year", "Physically present in the U.S. for at least one year when the I-485 is filed?", YES_NO, True),
        ("caa.abused", "Applying as an abused spouse or child of the Cuban principal?", YES_NO, False),
    ], _caa),
    ("The Cuban principal (Part 2, 2)", "the attorney", [
        ("applicant.principal_family_name", "Part 2, 2: the principal's family name", TEXT, True),
        ("applicant.principal_given_name", "Part 2, 2: the principal's given name", TEXT, True),
        ("applicant.principal_a_number", "Part 2, 2: the principal's A-Number", TEXT, False),
        ("caa.principal_i485", "The principal's own green card application",
         {"type": "choice", "options": ["Filed in the same envelope", "Pending with USCIS", "Approved: a permanent resident"]}, True),
        ("caa.resides_with_principal", "The client lives with the Cuban spouse or parent?", YES_NO, True),
    ], _derivative),
    ("HRIFA dependent (8 CFR 245.15(d))", "the attorney", [
        ("hrifa.relationship", "The client is the principal HRIFA beneficiary's", {"type": "choice", "options": ["Spouse", "Child", SON_DAUGHTER]}, True),
        ("applicant.principal_family_name", "Part 2, 2: the principal's family name", TEXT, True),
        ("applicant.principal_given_name", "Part 2, 2: the principal's given name", TEXT, True),
        ("applicant.principal_a_number", "Part 2, 2: the principal's A-Number", TEXT, False),
        ("hrifa.haitian_national", "The client is a national of Haiti?", YES_NO, True),
        ("hrifa.relationship_at_grant", "The relationship existed when the principal was granted adjustment, and still exists?", YES_NO, True),
        ("hrifa.present_since_1995", "An unmarried son or daughter: physically present in the U.S. continuously since 12/31/1995?", YES_NO, False),
        ("hrifa.other_basis", "Eligible for adjustment under any other provision of law?", YES_NO, True),
    ], lambda g: _category(g) == HRIFA),
    ("The packet", "the attorney", [
        ("caa.with_i765", "Also ask for a work permit (I-765, category (c)(9)) in the same envelope?", YES_NO, True),
    ]),
    ("A child under 14 (the fee)", "the attorney", [
        ("caa.with_parent", "Filed together with a parent's I-485 (Form G-1055: the lower fee under 14)?", YES_NO, True),
    ], _under_14),
]


def _upper(graph, key: str) -> str:
    return " ".join(str(value(graph, key) or "").upper().replace(".", " ").split())


def cuban(graph) -> str | None:
    """"native" (born in Cuba), "citizen" (a Cuban citizenship the case shows: the passport, the I-94, the questionnaire), or None."""
    if _upper(graph, "applicant.country_of_birth") in CUBA:
        return "native"
    if any(_upper(graph, k) in CUBA for k in ("applicant.citizenship", "applicant.travel_document_country")):
        return "citizen"
    return None


def _status(client_dir: Path | None) -> dict[str, Any]:
    path = client_dir / "status.json" if client_dir else None
    return json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}


def on_track(graph, client_dir: Path | None) -> bool:
    """The case is a CAA or HRIFA case as the case page sees it (src/journey.py track_of; a track a person set wins)."""
    import journey

    chosen = ((_status(client_dir).get("journey") or {}).get("track") or {}).get("value")
    if chosen:
        return chosen == "caa"
    docs = journey._docs(client_dir) if client_dir and (client_dir / "meta.json").exists() else {}
    return journey.track_of(graph, journey.notices(graph), docs) == "caa"


def present_since(graph) -> date | None:
    """The day the client's one year in the U.S. began: the answer, else the earliest arrival the case shows."""
    answered = _d(value(graph, "caa.present_since"))
    if answered:
        return answered
    found = [d for d in (_d(value(graph, k)) for k in ("applicant.i94_arrival_date", "applicant.last_arrival_date_self_reported")) if d]
    return min(found) if found else None


def eligible_on(graph) -> date | None:
    """One year after the client's physical presence began (Pub. L. 89-732 section 1; 8 CFR 245.2(a)(2)(ii))."""
    since = present_since(graph)
    return plus_years(since, 1) if since else None


def _entry(graph) -> str | None:
    """How the client was let in, from the I-485's Part 1 item 11 (src/assemble.py) and the portal's release paper question."""
    if value(graph, "questionnaire.release_document") == "i220a":
        return I220A
    return {"ADMITTED": ADMITTED, "PAROLED": PAROLED, "WITHOUT ADMISSION OR PAROLE": EWI}.get(str(value(graph, "applicant.last_arrival_manner") or "").upper())


def case_facts(graph, client_dir: Path | None = None):
    """The I-485's CAA or HRIFA answers (each derived, for the attorney to confirm): the category for a Cuban case on its own
    track, and for a CAA or HRIFA category the exemptions and Part 2 item 2. Nothing for an SIJ case or any other category."""
    if sij(graph):
        return graph
    put = putter(graph, "cuban_adjustment.derive")
    category = _category(graph)
    if category is None and cuban(graph) and on_track(graph, client_dir):
        put("applicant.filing_category", CAA, f"a {cuban(graph)} of Cuba in the U.S. (Cuban Adjustment Act, Part 2, 3.f)")
        category = _category(graph)
    if category not in (CAA, HRIFA):
        return graph
    if category == CAA:
        basis = value(graph, "caa.basis")
        if basis in (SPOUSE, CHILD):
            put("applicant.filing_as", "Derivative", "the spouse or child of a Cuban principal")
        elif basis or cuban(graph):
            put("applicant.filing_as", "Principal", "a native or citizen of Cuba")
        put("applicant.public_charge_exemption", CAA, "Part 9, 56: the Cuban Adjustment Act is exempt from public charge (USCIS Policy Manual Vol. 8, Part G, Ch. 3)")
        why = "the Cuban Adjustment Act"
    else:
        put("applicant.filing_as", "Derivative", "a dependent of a principal HRIFA beneficiary")
        put("applicant.public_charge_exemption", HRIFA, "Part 9, 56: INA 212(a)(4) doesn't apply to HRIFA dependents (8 CFR 245.15(e)(1))")
        why = "HRIFA section 902"
    put("applicant.affidavit_of_support_exemption", "Not required", f"adjusting under {why}: no Affidavit of Support (Part 3, 1.e)")
    put("applicant.part2.adjusting_under_245i", "No", f"adjusting under {why}, not INA 245(i)")
    return graph


def derive(graph, today: date):
    """The panel's suggestions from the case (case_facts already ran on the reviewed case)."""
    put = putter(graph, "cuban_adjustment.derive")
    born = cuban(graph)
    if born and not sij(graph):
        put("applicant.filing_category", CAA, f"a {born} of Cuba")
    put("caa.basis", NATIVE if born == "native" else CITIZEN if born == "citizen" else None,
        "born in Cuba (the case's country of birth)" if born == "native" else "a Cuban citizenship in the case (passport, I-94 or questionnaire)")
    put("caa.entry", _entry(graph), "the I-485's Part 1, item 11 (the I-94 and the client's answers)")
    arrived = _d(value(graph, "applicant.last_arrival_date")) or _d(value(graph, "applicant.i94_arrival_date")) or _d(value(graph, "applicant.last_arrival_date_self_reported"))
    put("caa.entry_date", arrived.isoformat() if arrived else None, "the date of the last arrival (the I-94, else the client's answer)")
    since = present_since(graph)
    put("caa.present_since", since.isoformat() if since else None, "the earliest arrival the case shows")
    if since and plus_years(since, 1) <= today:
        put("caa.present_one_year", "Yes", f"in the U.S. since {us(since)}")
    if _upper(graph, "applicant.citizenship") == "HAITI":
        put("hrifa.haitian_national", "Yes", "a citizen of Haiti (the case's citizenship)")
    return graph


def fee(graph, today: date) -> tuple[int | None, str]:
    """(the I-485 fee, why) from schemas/law/fees.json (G-1055 10/01/26): the general fee, or the lower one under 14 with a parent."""
    import fees

    paper = fees.load(today).get("paper") or {}
    if _under_14(graph, today) and value(graph, "caa.with_parent") == "Yes":
        return paper.get("i485_under_14_with_parent"), "under 14, filed with a parent's I-485 (G-1055)"
    return paper.get("i485"), "the I-485's general fee (G-1055)"


def payments(graph, today: date, forms: list[str]) -> list[tuple[str, str, Any, str]]:
    """(form id, form, amount, what) for each payment: the I-485, and the I-765 filed with it (G-1055 Appendix C: an I-485 filed
    with its fee on or after 04/01/2024 and pending)."""
    import fees

    out = [("i485", "I-485", fee(graph, today)[0], "Form I-485 filing fee")]
    if "i765" in forms:
        out.append(("i765", "I-765", (fees.load(today).get("paper") or {}).get("i765_with_pending_i485_paid"), "Form I-765 filing fee"))
    return out


def mail_to(graph) -> tuple[list[str] | None, str | None]:
    """USCIS's I-485 filing addresses: CAA (not abused) to the family-based chart; HRIFA dependents (not abused) with asylum and
    refugees, the non-family chart. The abused spouse or child goes to the VAWA, T and U page: not here."""
    if value(graph, "caa.abused") == "Yes" and _caa(graph):
        return None, None
    return lockbox("uscis_lockboxes_nfb" if _category(graph) == HRIFA else "uscis_lockboxes", state_of(graph))


def notes(graph, today: date) -> list[dict[str, str]]:
    out = []
    hrifa = _category(graph) == HRIFA
    opens = eligible_on(graph)
    if not hrifa and opens:
        out.append({"level": "warn" if today < opens else "info", "title": "When",
                    "text": f"From {us(opens)}: one year of physical presence in the U.S. (8 CFR 245.2(a)(2)(ii)). The year may come before the "
                            "parole (I-485 Instructions, CAA)." + (" Not yet." if today < opens else "")})
    amount, why = fee(graph, today)
    out.append({"level": "info", "title": "Fee", "text": f"{money(amount)}: {why}." + (" The I-765 in the same envelope is paid separately."
                                                                                       if value(graph, "caa.with_i765") != "No" else "")})
    lines, name = mail_to(graph)
    chart = "non-family" if hrifa else "family-based"
    out.append({"level": "info" if lines else "warn", "title": "Where",
                "text": f"USCIS {name} lockbox ({chart} chart): " + " / ".join(lines) if lines else
                "Not on USCIS's chart for this case: the attorney sets the address by hand."})
    out.append({"level": "info", "title": "Medical exam", "text": "Form I-693 in the civil surgeon's sealed envelope, with the I-485 (I-485 Instructions, item 9)."})
    if hrifa:
        out.append({"level": "info", "title": "HRIFA evidence",
                    "text": "Haitian nationality; the relationship when the principal was adjusted and today; an unmarried son or daughter also "
                            f"proves continuous physical presence since {HRIFA_PRESENCE} with a statement of every departure and arrival since then "
                            "(I-485 Instructions; 8 CFR 245.15(d))."})
        return out
    if value(graph, "caa.basis") == CITIZEN:
        out.append({"level": "warn", "title": "Cuban citizenship",
                    "text": "Born outside Cuba: prove it with an unexpired Cuban passport, a nationality certificate or a citizenship letter. A Cuban "
                            "birth certificate of a birth abroad, or a Cuban consular birth record, is not enough (I-485 Instructions, CAA)."})
    out.append({"level": "info", "title": "Not needed",
                "text": "No Supplement A, Form I-643 or police clearances: the I-485 and USCIS's background checks satisfy 8 CFR 245.2(a)(3)(iv) "
                        "(I-485 Instructions, CAA). Public charge, labor certification and documentation grounds don't apply (USCIS's CAA page)."})
    return out


@producer(ATTORNEY)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    import journey

    out = []
    category = _category(graph)
    if category and category not in (CAA, HRIFA):
        return [held(OFFICE, f"The case's I-485 category is \"{category}\", not the Cuban Adjustment Act or HRIFA: this is the wrong packet, or the category is wrong.")]
    if category == HRIFA:
        return out + _hrifa_problems(graph) + journey.i485_court_problem(client_dir, graph)
    basis, born = value(graph, "caa.basis"), cuban(graph)
    if basis in (NATIVE, CITIZEN) and not born and (value(graph, "applicant.country_of_birth") or value(graph, "applicant.citizenship")):
        out.append(held(OFFICE, "The case shows no birth in Cuba or Cuban citizenship, but the client is answered as the Cuban principal: correct the "
                   "country of birth or citizenship, or choose the spouse or child of a Cuban principal."))
    entry = value(graph, "caa.entry")
    if entry == EWI:
        out.append("Entered without inspection and not paroled: the Act needs an inspection and admission or a parole after 01/01/1959 "
                   "(8 CFR 245.2(a)(2)(ii)); without one the client is not eligible unless DHS paroles them under INA 212(d)(5)(A) (I-485 Instructions, CAA).")
    if entry == I220A:
        out.append("Released on Form I-220A: the attorney confirms the client was paroled under INA 212(d)(5)(A) (I-485 Instructions, CAA) "
                   "and changes the answer before filing.")
    when = _d(value(graph, "caa.entry_date"))
    if when and when <= SINCE:
        out.append(f"The admission or parole on {us(when)} is not after 01/01/1959 (8 CFR 245.2(a)(2)(ii)).")
    opens = eligible_on(graph)
    if opens and today < opens:
        out.append(held(OFFICE, f"Too early: the client can apply from {us(opens)}, after one year of physical presence in the U.S. (8 CFR 245.2(a)(2)(ii))."))
    if value(graph, "caa.present_one_year") == "No":
        out.append("Not physically present in the U.S. for one year: not eligible yet (8 CFR 245.2(a)(2)(ii)).")
    if value(graph, "caa.abused") == "Yes":
        out.append("An abused spouse or child: a different I-485 (Part 2, 3.f's second box), protected by 8 U.S.C. 1367, with no I-485 fee and filed "
                   "at USCIS's VAWA, T and U address: this packet doesn't build it. The attorney prepares it.")
    if basis in (SPOUSE, CHILD):
        if value(graph, "caa.resides_with_principal") == "No":
            out.append("The spouse or child must reside with the Cuban principal in the U.S. (Pub. L. 89-732 section 1; USCIS's CAA page).")
        born_on = _d(value(graph, "applicant.dob"))
        if basis == CHILD and born_on and plus_years(born_on, 21) <= today:
            out.append(f"A child under the Act is unmarried and under 21 (USCIS's CAA page): the client turned 21 on {us(plus_years(born_on, 21))}.")
        if basis == CHILD and str(value(graph, "applicant.marital_status") or "").lower() in ("married", "legally separated"):
            out.append("A child under the Act is unmarried (USCIS's CAA page): the case says the client is married.")
    return out + journey.i485_court_problem(client_dir, graph)


@producer(ATTORNEY)
def _hrifa_problems(graph) -> list[str]:
    out = []
    if value(graph, "hrifa.haitian_national") == "No":
        out.append("A HRIFA dependent is a national of Haiti (8 CFR 245.15(d)).")
    if value(graph, "hrifa.relationship_at_grant") == "No":
        out.append("The relationship must have existed when the principal was granted adjustment and must still exist (8 CFR 245.15(d)(4)).")
    if value(graph, "hrifa.relationship") == SON_DAUGHTER and value(graph, "hrifa.present_since_1995") != "Yes":
        out.append(f"An unmarried son or daughter must have been physically present in the U.S. continuously since {HRIFA_PRESENCE} "
                   "(8 CFR 245.15(d)(5)): answer the question, with the evidence and the statement of departures and arrivals.")
    if value(graph, "hrifa.other_basis") == "Yes":
        out.append("Eligible under another provision of law: the client may not file as a HRIFA dependent (I-485 Instructions, HRIFA).")
    return out


def packet_variant(graph) -> str | None:
    """"no_ead" when the client doesn't ask for the work permit in the same envelope (schemas/packets/caa.json)."""
    return "no_ead" if value(graph, "caa.with_i765") == "No" else None


def letter(graph, today: date) -> dict[str, Any]:
    """The cover letter's address, subject and fees for this client."""
    import fees

    hrifa = _category(graph) == HRIFA
    amount, _why = fee(graph, today)
    with_i765 = value(graph, "caa.with_i765") != "No"
    lines, _ = mail_to(graph)
    edition = fees.load(today).get("edition") or "current"
    basis = ("Basis: Haitian Refugee Immigration Fairness Act of 1998, Section 902, Dependent of a Principal Beneficiary" if hrifa else
             "Basis: Cuban Adjustment Act of November 2, 1966 (Pub. L. 89-732), Section 1"
             + (", Spouse or Child of a Qualifying Cuban" if _derivative(graph) else ""))
    i765 = (fees.load(today).get("paper") or {}).get("i765_with_pending_i485_paid")
    text = ("Filing fee: [the attorney sets it. See the packet's problems]." if amount is None else
            f"Enclosed are the filing fees, each paid by its own Form G-1450: Form I-485, {money(amount)}"
            + (f"; Form I-765, {money(i765)}" if with_i765 else "") + f" (Form G-1055, edition {edition}).")
    return {"re_lines": ["Application: I-485 Application to Register Permanent Residence or Adjust Status", basis]
            + (["I-765 Application for Employment Authorization, Category (c)(9)"] if with_i765 else []),
            "mail_to": lines or ["[USCIS address: see the packet's problems]"], "fees": text, "no_payment": False}
