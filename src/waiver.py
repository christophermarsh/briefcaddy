"""The provisional unlawful presence waiver, Form I-601A (edition 01/20/25):
for a client in the U.S. who will leave for an immigrant visa interview abroad
and would then be barred for the unlawful presence (INA 212(a)(9)(B)) --
8 CFR 212.7(e) (eCFR as of 09/30/2026, data/reference/8cfr212.7.xml) and
uscis.gov/i-601a (updated 06/01/2026):

  Who (212.7(e)(3), (4)): present in the U.S.; 17 or older; an immigrant visa
    case with the Department of State on an APPROVED petition, its immigrant
    visa fee PAID; would be inadmissible only for unlawful presence; extreme
    hardship to a U.S. citizen or permanent resident spouse or parent. Not:
    in removal proceedings unless administratively closed and not
    recalendared; a final order (unless an I-212 was already approved); a
    reinstated order; a pending I-485.
  It doesn't support a work permit or advance parole (212.7(e)(2)(ii)).
  Where: the Chicago lockbox, P.O. Box 4599. Fee (G-1055 10/01/26): $795; $0
    for a person seeking or granted SIJ, or a VAWA self-petitioner.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from filing_questions import LINES, TEXT, YES_NO, money, putter, sij, value
from filing_questions import iso as _d
from holders import ATTORNEY, CLIENT, held, producer

TITLE = "Provisional unlawful presence waiver (I-601A)"
MAIL_TO = ["USCIS", "ATTN: I-601A", "P.O. BOX 4599", "CHICAGO, IL 60680-4599"]
BASES = ["Immediate relative (I-130)", "Family preference (I-130)", "Employment (I-140)", "Special immigrant (I-360)", "Diversity visa"]
RELATIVES = ["U.S. citizen spouse", "U.S. citizen parent", "LPR spouse", "LPR parent"]
HISTORY = [("waiver.q32_false_information", "32. Ever knowingly given false or misleading information to a U.S. official for a benefit or entry?"),
           ("waiver.q33_smuggling", "33. Ever engaged in alien smuggling?"),
           ("waiver.q34_arrested", "34. Ever arrested, cited or detained by any officer (other than traffic)?"),
           ("waiver.q35_charged_convicted", "35. Ever charged, indicted, convicted, imprisoned or jailed for any crime?"),
           ("waiver.q36_drug_trafficking", "36. Ever trafficked in a controlled substance?"),
           ("waiver.q37_assisted_trafficking", "37. Ever assisted others in trafficking a controlled substance?"),
           ("waiver.q38_prostitution", "38. Ever engaged in prostitution?"),
           ("waiver.q39a_torture_genocide", "39.A. Ever taken part in torture or genocide?"),
           ("waiver.q39b_killing", "39.B. Ever taken part in killing any person?"),
           ("waiver.q39c_injuring", "39.C. Ever taken part in intentionally and severely injuring any person?"),
           ("waiver.q39d_forced_sexual_contact", "39.D. Ever taken part in sexual contact with a person forced or threatened?"),
           ("waiver.q39e_religious_freedom", "39.E. Ever taken part in limiting a person's religious beliefs?"),
           ("waiver.q40a_armed_group", "40.A. Ever served in or helped any military, police, rebel or other armed group?"),
           ("waiver.q40b_detention_facility", "40.B. Ever served in any prison, jail, camp or place detaining persons?"),
           ("waiver.q41_weapons_group", "41. Ever been in a group that used or threatened weapons against any person?"),
           ("waiver.q42_weapons_supply", "42. Ever sold, provided or transported weapons to someone who used them against a person?"),
           ("waiver.q43_weapons_training", "43. Ever received military, paramilitary or weapons training?"),
           ("waiver.q44_child_soldiers_recruited", "44. Ever recruited or used a person under 15 to serve an armed group?"),
           ("waiver.q45_child_soldiers_used", "45. Ever used a person under 15 in hostilities?")]
SECTIONS = [
    ("The immigrant visa case", "the attorney", [
        ("waiver.basis", "Part 3, 1: the basis of the immigrant visa", {"type": "choice", "options": BASES}, True),
        ("waiver.petition_receipt", "Part 3, 3.A: the approved petition's receipt number", TEXT, True),
        ("visa.nvc_case_number", "Part 3, 3.B: the NVC case number", TEXT, True),
        ("waiver.iv_fee_paid", "The Department of State immigrant visa fee receipt says PAID (not 'In Process')?", YES_NO, True),
    ]),
    ("The qualifying relative (extreme hardship)", "the attorney", [
        ("waiver.relative_is", "Part 4, 2: the relative who would suffer extreme hardship is the client's", {"type": "choice", "options": RELATIVES}, True),
        ("waiver.relative_family_name", "Part 4, 1.A: their family name", TEXT, True),
        ("waiver.relative_given_name", "Part 4, 1.B: their given name", TEXT, True),
        ("waiver.more_relatives", "Part 4, 3: more than one qualifying relative?", YES_NO, False),
    ]),
    ("Proceedings and orders", "the attorney", [
        ("waiver.q27_in_proceedings", "27. In removal, exclusion or deportation proceedings with no final order?", YES_NO, True),
        ("waiver.q28_proceedings", "28. Those proceedings are", {"type": "choice", "options": ["Administratively closed, not recalendared",
                                                                                              "Not administratively closed (or recalendared)"]}, False),
        ("waiver.q29_final_order", "29.A. Ever subject to a final order of removal, exclusion or deportation?", YES_NO, True),
        ("waiver.q29_i212_receipt", "29.B. The approved I-212's receipt number (if 29.A is Yes)", TEXT, False),
        ("waiver.q30a_reinstatement_notice", "30.A. Served a Form I-871 (notice of intent to reinstate a prior order)?", YES_NO, True),
        ("waiver.q30b_reinstated", "30.B. Served a final decision reinstating a prior order?", YES_NO, False),
        ("waiver.q31_voluntary_departure", "31. Granted voluntary departure by a judge or the Board that expired?", YES_NO, True),
    ]),
    ("Immigration and criminal history (Part 1, 32-45)", "the attorney", [(key, label, YES_NO, True) for key, label in HISTORY]),
    ("The statement (Part 5)", "the attorney", [
        ("waiver.statement", "Why the client qualifies and merits the waiver: the extreme hardship, in brief (the full evidence goes with it)", LINES, True),
    ]),
]


def derive(graph, today: date):
    import journey
    import preference

    put = putter(graph, "waiver.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    klass = preference.visa_class(graph)
    put("waiver.basis", "Immediate relative (I-130)" if klass == "IR" else "Family preference (I-130)" if klass else None, "the case's family category")
    approved = next((n for n in reversed(journey.notices(graph)) if n["form"] in ("I-130", "I-140", "I-360") and n["kind"] == "approval"), None)
    put("waiver.petition_receipt", approved["receipt"] if approved else None, "the approved petition's notice")
    rel, status = v("family.relationship"), v("petitioner.status")
    relative = {("Spouse", "USC"): "U.S. citizen spouse", ("Spouse", "LPR"): "LPR spouse", ("Child", "USC"): "U.S. citizen parent",
                ("Child", "LPR"): "LPR parent"}.get((rel, status))
    if relative:  # the petitioner is a qualifying relative
        put("waiver.relative_is", relative, "the petitioner: the client's " + relative.split(" ", 2)[-1])
        put("waiver.relative_family_name", v("petitioner.family_name"), "the petitioner")
        put("waiver.relative_given_name", v("petitioner.given_name"), "the petitioner")
    different = v("applicant.mailing_same_as_physical") == "No"
    for part in ("street", "apt", "city", "state", "zip"):  # the form asks for the U.S. physical address always
        put(f"waiver.physical_{part}", v(f"applicant.physical_{part}"), "the client's home address")
        put(f"waiver.mailing_{part}", v(f"applicant.mailing_{part}") if different else v(f"applicant.physical_{part}"),
            "the client's mailing address" if different else "the client's home address (also their mailing address)")
    return graph


def fee(graph, today: date) -> tuple[int | None, str]:
    import fees

    paper = fees.load(today).get("paper") or {}
    if sij(graph):
        return paper.get("i601a_sij"), "a person seeking or granted SIJ: no fee (G-1055)"
    if value(graph, "vawa.classification"):  # the G-1055 (10/01/26) also exempts a VAWA self-petitioner, including derivatives
        return paper.get("i601a_sij"), "a VAWA self-petitioner: no fee (G-1055)"
    return paper.get("i601a"), "the I-601A fee (G-1055)"


def notes(graph, today: date) -> list[dict[str, str]]:
    amount, why = fee(graph, today)
    return [{"level": "info", "title": "What it waives", "text": "Only the unlawful presence bar (INA 212(a)(9)(B)), and only once the client leaves for the immigrant "
                                                                "visa interview: it is filed after the visa fee is paid and before the interview (8 CFR 212.7(e))."},
            {"level": "info", "title": "Fee and where", "text": f"{money(amount)}: {why}. Mailed to " + " / ".join(MAIL_TO) + " (uscis.gov/i-601a)."},
            {"level": "info", "title": "No work permit or travel with it", "text": "A pending or approved I-601A supports neither; one filed with it is rejected (8 CFR 212.7(e)(2)(ii))."}]


@producer(ATTORNEY)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    import journey

    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    dob = _d(v("applicant.dob"))
    if dob and (today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))) < 17:
        out.append("Under 17: not eligible for a provisional waiver (8 CFR 212.7(e)(4)(i)).")
    if v("waiver.iv_fee_paid") == "No":
        out.append(held(CLIENT, "The immigrant visa fee isn't paid yet: the client has no Department of State case to support the waiver (8 CFR 212.7(e)(4)(ii)). "
                   "pay it at NVC and wait for the receipt to say PAID."))
    if not any(n["form"] in ("I-130", "I-140", "I-360") and n["kind"] == "approval" for n in journey.notices(graph)):
        out.append("No approved petition in the case: the waiver needs an approved I-130, I-140 or I-360 (8 CFR 212.7(e)(3)(iv)).")
    if v("waiver.q28_proceedings") == "Not administratively closed (or recalendared)":
        out.append("In removal proceedings that aren't administratively closed: not eligible (8 CFR 212.7(e)(4)(iii)).")
    if v("waiver.q29_final_order") == "Yes" and not v("waiver.q29_i212_receipt"):
        out.append("A final order: not eligible unless an I-212 (permission to reapply) was already approved (8 CFR 212.7(e)(4)(iv)).")
    if v("waiver.q30b_reinstated") == "Yes":
        out.append("A reinstated prior order: not eligible (8 CFR 212.7(e)(4)(v)).")
    if any(n["form"] == "I-485" and n["kind"] in ("receipt", "transfer", "biometrics", "interview") for n in journey.notices(graph)) \
            and not any(n["form"] == "I-485" and n["kind"] in ("approval", "denial", "rejection") for n in journey.notices(graph)):
        out.append("A pending I-485: not eligible while it is pending (8 CFR 212.7(e)(4)(vi)).")
    if v("family.relationship") == "Sibling" and not v("waiver.relative_is"):
        out.append("A sibling petition: the petitioner isn't a qualifying relative. The client needs a U.S. citizen or permanent resident spouse or parent.")
    return out


def letter(graph, today: date) -> dict[str, Any]:
    amount, _why = fee(graph, today)
    import fees

    edition = fees.load(today).get("edition") or "current"
    text = (f"No filing fee is required for this Form I-601A: the applicant is seeking or was granted Special Immigrant Juvenile classification "
            f"(Form G-1055, edition {edition})." if amount == 0 else
            f"Enclosed is the filing fee of {money(amount)} for Form I-601A, paid by the enclosed Form G-1450 (Form G-1055, edition {edition})."
            if amount else "Filing fee: [the attorney sets it. See the packet's problems].")
    return {"re_lines": ["Application: I-601A Application for Provisional Unlawful Presence Waiver",
                         f"NVC Case Number {value(graph, 'visa.nvc_case_number') or '[NVC case]'}; Petition {value(graph, 'waiver.petition_receipt') or '[receipt]'}"],
            "mail_to": MAIL_TO, "fees": text, "no_payment": amount == 0}

