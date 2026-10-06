"""Permission to reapply for admission after deportation or removal, Form I-212 (edition 01/20/25; uscis.gov/i-212, updated
06/01/2026, read 10/02/2026; the Instructions of the same edition):

  What it is: consent to reapply, for a person inadmissible under INA 212(a)(9)(A) (removed, or departed while a removal order was
    outstanding) or 212(a)(9)(C) (entered or tried to enter without admission or parole after more than 1 year of unlawful
    presence in total, or after a removal). Returning without it when it is needed can mean reinstatement of the order (INA
    241(a)(5)), prosecution (INA 276) and a permanent bar (Instructions, page 1).
  When it can't be filed: inadmissible under 212(a)(9)(C) and in the U.S., or not yet 10 years outside the U.S. since the last
    departure (Instructions, pages 2-3 and 5). Not needed once the whole 212(a)(9)(A) period was spent outside the U.S. and there
    is no aggravated felony (Instructions, pages 5-6).
  Discretionary: favorable factors against unfavorable ones; unsupported assertions are not enough (Instructions, pages 14-15).
  Where ("Direct Filing Addresses for Form I-212", uscis.gov, updated 11/07/2025, read 10/02/2026), by where the client stands:
    an immigrant visa applicant outside the U.S. who needs a Form I-601 too: USCIS Phoenix Lockbox, Attn: I-212 Foreign Filers,
      P.O. Box 21600, Phoenix, AZ 85036-1600 (couriers: Attn: I-212 Foreign Filers (Box 21600), 2108 E. Elliot Rd., Tempe, AZ
      85284-1806), filed together with the I-601 (8 CFR 212.2(d));
    a K or V visa applicant found inadmissible after the interview: Phoenix, Attn: I-212, P.O. Box 21600 (couriers: Attn: I-212
      (Box 21600), same street) (8 CFR 212.2);
    an immigrant visa on an approved VAWA self-petition: the "Attn: 1367" lockboxes by state (uscis.gov/Certain-VAWA-T-U-Filing-
      Locations, updated 02/05/2026, which lists the I-212);
    an immigrant visa with no I-601 needed: the USCIS field office with jurisdiction over where the removal proceedings were held;
    adjustment in the U.S., inadmissible only under 212(a)(9)(A): "at the filing location specified for Form I-485" (8 CFR
      212.2(e));
    conditional advance permission before leaving (8 CFR 212.2(j)): the field office where the client lives;
    in removal proceedings: the immigration court, as it instructs.
    The system writes an address only where the page gives one; the others are the attorney's to set.
  Fee (Form G-1055 10/01/26, pages 11-12): $1,175; $0 for a VAWA self-petitioner (including derivatives), an abused spouse or child
    adjusting under the Cuban Adjustment Act or HRIFA, and an Afghan or Iraqi special immigrant (or a derivative). With the
    immigration court, paid as the court instructs.

With an I-601 in the same envelope, the I-601's packet (src/inadmissibility_waiver.py) carries both forms, one cover letter and both
fees; this module supplies the I-212's questions, facts and fee to it. DRAFT for the attorney.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from filing_questions import DATE, LINES, TEXT, YES_NO, money, plus_years, putter, state_of, us, value
from filing_questions import iso as _d
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

TITLE = "Permission to reapply after removal (I-212)"
PAIR_KEY = "i601.with_i212"  # Form I-601 Part 1, 19 and Form I-212 Part 1, 19: the two forms in one envelope (the same fact)
SOURCE = "https://www.uscis.gov/forms/all-forms/direct-filing-addresses-for-form-i-212-application-for-permission-to-reapply-for-admission-into-the"
IV_WITH_601 = "Immigrant visa, outside the U.S., with a Form I-601 (the consulate found the client inadmissible)"
KV = "K or V visa: found inadmissible after the consular interview"
VAWA = "Immigrant visa on an approved VAWA self-petition"
IV_NO_601 = "Immigrant visa, no Form I-601 needed"
AOS = "Adjustment in the U.S., inadmissible only under INA 212(a)(9)(A): with the I-485 or while it is pending"
ADVANCE = "In the U.S., asking for conditional approval before leaving (8 CFR 212.2(j))"
COURT = "In removal proceedings (immigration court)"
SITUATIONS = [IV_WITH_601, KV, VAWA, IV_NO_601, AOS, ADVANCE, COURT]
IN_US = (AOS, ADVANCE, COURT)
# uscis.gov "Direct Filing Addresses for Form I-212" (updated 11/07/2025, read 10/02/2026): the rows that name a lockbox
PHOENIX_WITH_601 = ["USCIS", "ATTN: I-212 FOREIGN FILERS", "P.O. BOX 21600", "PHOENIX, AZ 85036-1600"]
PHOENIX_WITH_601_COURIER = ["USCIS", "ATTN: I-212 FOREIGN FILERS (BOX 21600)", "2108 E. ELLIOT RD.", "TEMPE, AZ 85284-1806"]
PHOENIX_KV = ["USCIS", "ATTN: I-212", "P.O. BOX 21600", "PHOENIX, AZ 85036-1600"]
PHOENIX_KV_COURIER = ["USCIS", "ATTN: I-212 (BOX 21600)", "2108 E. ELLIOT RD.", "TEMPE, AZ 85284-1806"]
NO_FEE = "None of these"
VAWA_EXEMPT = "A VAWA self-petitioner (including derivatives)"
# Form G-1055 (10/01/26), the I-212 rows, page 12 ("If you are filing with USCIS as a person seeking or granted..."): each $0
# category (the choice) and the cover letter's words for it
FEE_EXEMPT = {
    "An Afghan or Iraqi special immigrant (translator or interpreter, U.S. Government or ISAF employee), or a derivative":
        "the applicant is seeking or was granted a special immigrant visa or status as an Afghan or Iraqi national the Form G-1055 names, "
        "or a derivative beneficiary",
    "An abused spouse or child adjusting under the Cuban Adjustment Act or HRIFA":
        "the applicant is seeking or was granted adjustment of status as an abused spouse or child under the Cuban Adjustment Act or the "
        "Haitian Refugee Immigration Fairness Act",
    VAWA_EXEMPT: "the applicant is seeking or was granted immigrant classification as a VAWA self-petitioner",
}
ARRIVING = ["Removed once, the last removal less than 5 years ago", "Removed two or more times, the last less than 20 years ago",
            "Convicted of an aggravated felony, in the U.S. or abroad, before or after removal"]
DEPORTABLE = ["Removed once, less than 10 years ago", "Removed two or more times, the last less than 20 years ago",
              "Convicted of an aggravated felony, in the U.S. or abroad, before or after removal"]
STATUS = ["Permanent resident", "Visitor", "Student", "Other"]
RELATIVE_IS = ["A U.S. citizen", "A lawful permanent resident"]
OWN_SECTIONS = [
    ("Where it is filed", "the attorney", [
        ("i212.situation", "Where the client stands (it decides where the I-212 is filed)", {"type": "choice", "options": SITUATIONS}, True),
        (PAIR_KEY, "Part 1, 19: Form I-601 filed with it, in the same envelope?", YES_NO, True),
        ("i212.fee_exempt", "No fee (Form G-1055): the client files with USCIS as", {"type": "choice", "options": [NO_FEE, *FEE_EXEMPT]}, False),
    ]),
    ("The visa or the I-485 (Part 1, 16-20)", "the attorney", [
        ("i212.consular_case_number", "Part 1, 16: the Department of State case number (the NVC case number, unless the refusal shows another)", TEXT, False),
        ("i212.consulate_city", "Part 1, 17.A: the embassy or consulate: city", TEXT, False),
        ("i212.consulate_country", "Part 1, 17.B: the embassy or consulate: country", TEXT, False),
        ("i212.i485_receipt", "Part 1, 18.A: the I-485 receipt number (an adjustment)", TEXT, False),
        ("i212.i485_office", "Part 1, 18.B: where the I-485 was filed (for example, the lockbox)", TEXT, False),
        ("i212.i485_date", "Part 1, 18.C: the date the I-485 was filed", DATE, False),
        ("i212.prior_i601_receipt", "Part 1, 20.A: an earlier I-601's receipt number (only if 19 is No)", TEXT, False),
        ("i212.prior_i601_office", "Part 1, 20.B: where that I-601 was filed", TEXT, False),
        ("i212.prior_i601_date", "Part 1, 20.C: the date that I-601 was filed", DATE, False),
    ]),
    ("Removal as an arriving alien (Part 2, 1-4: INA 212(a)(9)(A)(i))", "the attorney", [
        ("i212.arriving", "1.A: removed as an arriving alien (expedited removal, or at the end of section 240 proceedings as an arriving alien)?", YES_NO, True),
        ("i212.arriving_which", "1.B-1.D: which applies", {"type": "choice", "options": ARRIVING}, False),
        ("i212.arriving_date", "2: the date removed", DATE, False),
        ("i212.arriving_city", "3: removed from: city or town", TEXT, False),
        ("i212.arriving_state", "4: removed from: state (two letters)", TEXT, False),
    ]),
    ("Removal as a deportable alien (Part 2, 5-7: INA 212(a)(9)(A)(ii))", "the attorney", [
        ("i212.deportable", "5.A: removed as a deportable alien, or left the U.S. while a removal order was outstanding?", YES_NO, True),
        ("i212.deportable_which", "5.B-5.D: which applies", {"type": "choice", "options": DEPORTABLE}, False),
        ("i212.deportable_date", "6: the date excluded, deported or removed", DATE, False),
        ("i212.deportable_city", "7.A: removed from: city or town", TEXT, False),
        ("i212.deportable_state", "7.B: removed from: state (two letters)", TEXT, False),
    ]),
    ("Entry after more than 1 year of unlawful presence (Part 2, 8-13: INA 212(a)(9)(C)(i)(I))", "the attorney", [
        ("i212.reentry_after_presence", "8: entered or tried to enter without admission or parole after more than 1 year of unlawful presence in total, "
                                        "on or after 04/01/1997?", YES_NO, True),
        ("i212.presence_from", "9.A: the most recent period of unlawful presence: from", DATE, False),
        ("i212.presence_to", "9.B: to", DATE, False),
        ("i212.presence_departed", "10: the date the client departed after it", DATE, False),
        ("i212.presence_departed_city", "11.A: departed from: city or town", TEXT, False),
        ("i212.presence_departed_state", "11.B: departed from: state (two letters)", TEXT, False),
        ("i212.presence_reentry_city", "12.A: reentered or tried to: city or town", TEXT, False),
        ("i212.presence_reentry_state", "12.B: reentered or tried to: state (two letters)", TEXT, False),
        ("i212.presence_reentry_date", "13: the date of that entry or attempt", DATE, False),
    ]),
    ("Entry after removal (Part 2, 14-17: INA 212(a)(9)(C)(i)(II))", "the attorney", [
        ("i212.reentry_after_removal", "14: entered or tried to enter without admission or parole after being excluded, deported or removed?", YES_NO, True),
        ("i212.removal_date", "15: the date excluded, deported or removed", DATE, False),
        ("i212.removal_reentry_city", "16.A: reentered or tried to: city or town", TEXT, False),
        ("i212.removal_reentry_state", "16.B: reentered or tried to: state (two letters)", TEXT, False),
        ("i212.removal_reentry_date", "17: the date of that entry or attempt", DATE, False),
    ]),
    ("Why the client asks (Part 3)", "the attorney", [
        ("i212.status_sought", "1: the status the client will seek", {"type": "choice", "options": STATUS}, True),
        ("i212.status_other", "1.D: other: explain", TEXT, False),
        ("i212.why", "2: why the client wants to reenter (in brief: the full statement and the evidence go with it)", LINES, True),
        ("i212.relative_family_name", "3.A: a U.S. citizen or permanent resident family member: family name", TEXT, False),
        ("i212.relative_given_name", "3.B: their given name", TEXT, False),
        ("i212.relative_middle_name", "3.C: their middle name", TEXT, False),
        ("i212.relative_relationship", "3.D: their relationship to the client", TEXT, False),
        ("i212.relative_is", "4: that relative is", {"type": "choice", "options": RELATIVE_IS}, False),
    ]),
    ("The client's own account (asked in the portal)", "the client", [
        ("i212.client_removal", "In the client's words: each removal or return at the border, and how they came back", LINES, False),
        ("i212.client_why", "In the client's words: why they want to come back, and their family there", LINES, False),
    ]),
]
SECTIONS = OWN_SECTIONS
MORE_QUESTIONS = None
# The long answers, asked in the client's portal in the language they read it in (filing_questions.client_questions). DRAFT: the
# attorney approves the wording and a certified translator the Portuguese and Spanish; the Haitian Creole is a machine draft.
CLIENT_QUESTIONS = {
    "i212.client_removal": {
        "en": "Tell us about each time you were deported, removed or sent back at the border, or left the U.S. after a judge's order: the dates, "
              "the places, and how you came back, if you did.",
        "pt": "Conte sobre cada vez que você foi deportado, removido ou mandado de volta na fronteira, ou saiu dos EUA depois da ordem de um juiz: "
              "as datas, os lugares e como você voltou, se voltou.",
        "es": "Cuéntenos sobre cada vez que fue deportado, expulsado o devuelto en la frontera, o salió de EE. UU. después de la orden de un juez: "
              "las fechas, los lugares y cómo regresó, si regresó.",
        "ht": "Pale nou sou chak fwa yo te depòte ou, retire ou oswa voye ou tounen sou fwontyè a, oswa ou te kite Ozetazini apre lòd yon jij: "
              "dat yo, kote yo, ak ki jan ou te retounen, si ou te retounen."},
    "i212.client_why": {
        "en": "Why do you want to come back to the U.S.? Who are your family members there, and what would your return mean for them?",
        "pt": "Por que você quer voltar para os EUA? Quem são os seus familiares lá e o que a sua volta significaria para eles?",
        "es": "¿Por qué quiere regresar a EE. UU.? ¿Quiénes son sus familiares allí y qué significaría su regreso para ellos?",
        "ht": "Poukisa ou vle retounen Ozetazini? Ki moun ki fanmi ou la a, e kisa retou ou t ap vle di pou yo?"},
}


def pair(graph) -> bool:
    """The I-601 and the I-212 in one envelope (Part 1, 19 of either form)."""
    return value(graph, PAIR_KEY) == "Yes"


def _relative(graph) -> tuple[str | None, str | None]:
    """(relationship to the client, U.S. citizen or permanent resident) of the petitioner -- a family member with ties to the U.S."""
    rel, status = value(graph, "family.relationship"), value(graph, "petitioner.status")
    relationship = {"Spouse": "Spouse", "Child": "Parent", "Parent": "Son or daughter", "Sibling": "Brother or sister"}.get(rel)
    return relationship, {"USC": RELATIVE_IS[0], "LPR": RELATIVE_IS[1]}.get(status)


def derive(graph, today: date):
    from filing_questions import addresses, pending

    put = putter(graph, "i212.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    addresses(graph, put, "i212")
    put("i212.consular_case_number", v("visa.nvc_case_number"), "the NVC case number (the immigrant visa case)")
    put("i212.consulate_city", v("visa.consulate"), "the consulate of the visa interview")
    receipt = pending(graph, "I-485")
    if receipt:
        put("i212.i485_receipt", receipt["receipt"], "the I-485 receipt notice")
        put("i212.i485_date", receipt.get("date"), "the I-485 receipt notice's date")
    if v("i212.situation") in (IV_WITH_601, VAWA, IV_NO_601, AOS, ADVANCE):
        put("i212.status_sought", "Permanent resident", "an immigrant visa or adjustment: the client seeks permanent residence")
    relationship, status = _relative(graph)
    if relationship and status:  # the petitioner: the family member with ties to the U.S. (Part 3, 3-4)
        put("i212.relative_family_name", v("petitioner.family_name"), "the petitioner")
        put("i212.relative_given_name", v("petitioner.given_name"), "the petitioner")
        put("i212.relative_middle_name", v("petitioner.middle_name"), "the petitioner")
        put("i212.relative_relationship", relationship.upper(), "the petitioner: the client's " + relationship.lower())
        put("i212.relative_is", status, "the petitioner's status")
    return graph


def where(graph) -> dict[str, Any]:
    """{"mail_to": the USPS lines or None, "lockbox": mailed to a USCIS lockbox, "text": where it goes, in words}."""
    from filing_questions import lockbox

    situation = value(graph, "i212.situation")
    if situation == IV_WITH_601:
        return {"mail_to": PHOENIX_WITH_601, "lockbox": True,
                "text": " / ".join(PHOENIX_WITH_601) + " (couriers: " + " / ".join(PHOENIX_WITH_601_COURIER) + "), together with the Form I-601"}
    if situation == KV:
        return {"mail_to": PHOENIX_KV, "lockbox": True, "text": " / ".join(PHOENIX_KV) + " (couriers: " + " / ".join(PHOENIX_KV_COURIER) + ")"}
    if situation == VAWA:
        lines, name = lockbox("uscis_lockboxes_vawa", state_of(graph))
        return {"mail_to": lines, "lockbox": True,
                "text": (" / ".join(lines) + f" (the {name} lockbox, by the client's state: USCIS's VAWA, T and U filing addresses)") if lines else
                        "the VAWA, T and U lockbox for the client's state: the client's state isn't in the case or isn't on USCIS's list"}
    if situation == IV_NO_601:
        return {"mail_to": None, "lockbox": False, "text": "the USCIS field office with jurisdiction over the place where the removal proceedings were "
                                                            "held (without proceedings: over the intended U.S. residence). The attorney sets the address"}
    if situation == AOS:
        return {"mail_to": None, "lockbox": False, "text": "the filing location for the client's I-485 (with it, or while it is pending): the attorney sets the address"}
    if situation == ADVANCE:
        return {"mail_to": None, "lockbox": False, "text": "the USCIS field office with jurisdiction over where the client lives: the attorney sets the address"}
    if situation == COURT:
        return {"mail_to": None, "lockbox": False, "text": "the immigration court, as the court instructs: not mailed to USCIS"}
    return {"mail_to": None, "lockbox": False, "text": "not known until the attorney says where the client stands"}


def exempt(graph) -> str | None:
    """The Form G-1055 $0 category: a VAWA self-petition the case shows, else the attorney's choice. (No $0 for an SIJ, T or U: the
    G-1055's I-212 rows don't list them.)"""
    if value(graph, "vawa.classification") or value(graph, "i212.situation") == VAWA:
        return VAWA_EXEMPT
    chosen = value(graph, "i212.fee_exempt")
    return chosen if chosen in FEE_EXEMPT else None


def fee(graph, today: date) -> tuple[int | None, str]:
    import fees

    paper = fees.load(today).get("paper") or {}
    category = exempt(graph)
    if category:
        return paper.get("i212_exempt"), f"{category}: no fee (G-1055)"
    return paper.get("i212"), "the I-212 fee (G-1055)"


def payments(graph, today: date, forms: list[str]) -> list[tuple[str, str, Any, str]]:
    """One payment, unless the court collects it (Form G-1055: 'as instructed by the immigration court')."""
    if value(graph, "i212.situation") == COURT:
        return []
    return [("i212", "I-212", fee(graph, today)[0], "Form I-212 filing fee")]


def _years_after(on: Any, years: int) -> date | None:
    d = _d(on)
    return plus_years(d, years) if d else None


def notes(graph, today: date) -> list[dict[str, str]]:
    v = lambda k: value(graph, k)  # noqa: E731
    amount, why = fee(graph, today)
    place = where(graph)
    out = [{"level": "info", "title": "What it is",
            "text": "Consent to reapply, for a client inadmissible under INA 212(a)(9)(A) (removed, or left under a removal order) or 212(a)(9)(C) "
                    "(entered or tried to enter without admission after more than 1 year of unlawful presence, or after a removal). Coming back "
                    "without it when it is needed can mean reinstatement of the order, prosecution and a permanent bar (the I-212 instructions, page 1)."},
           {"level": "info", "title": "Fee and where",
            "text": ("Paid as the immigration court instructs (Form G-1055)" if v("i212.situation") == COURT else f"{money(amount)}: {why}")
                    + f". Filed with {place['text']} (USCIS's I-212 filing addresses)."},
           {"level": "info", "title": "A discretionary decision",
            "text": "USCIS weighs the favorable factors (family ties, hardship, rehabilitation, lawful presence, good moral character) against the "
                    "unfavorable ones. Statements need evidence behind them (the I-212 instructions, pages 14-15)."}]
    # 212(a)(9)(A): the period may be over (Instructions, pages 5-6)
    for which, when, years, what in ((v("i212.arriving_which"), v("i212.arriving_date"), 5, "as an arriving alien"),
                                     (v("i212.deportable_which"), v("i212.deportable_date"), 10, "as a deportable alien")):
        ends = _years_after(when, years)
        if which and which.startswith("Removed once") and ends and ends <= today:
            out.append({"level": "warn", "title": "The period may be over",
                        "text": f"Removed once {what} on {us(_d(when))}: the {years}-year period ended on {us(ends)}. If the client stayed outside the "
                                "U.S. all that time and has no aggravated felony, consent to reapply may no longer be needed (the I-212 instructions, "
                                "pages 5-6). The attorney decides."})
    if v("i212.reentry_after_presence") == "Yes" or v("i212.reentry_after_removal") == "Yes":
        out.append({"level": "warn", "title": "INA 212(a)(9)(C)",
                    "text": "The I-212 can be filed only from outside the U.S., after 10 years outside since the last departure: the attorney "
                            "confirms the date and the evidence of the 10 years (passport stamps, residence, work, bills: the I-212 instructions, "
                            "pages 3, 5 and 13)."})
    if pair(graph):
        out.append({"level": "info", "title": "With the I-601",
                    "text": "Both forms go in one envelope: USCIS's I-212 address page says an immigrant visa applicant outside the U.S. who needs "
                            "a Form I-601 'must file Form I-212 together with Form I-601'. The I-601's packet carries both."})
    return out


@producer(OFFICE)
def problems(client_dir: Path, graph, today: date, in_pair: bool = False) -> list[str]:
    """What stops the I-212 from being final. in_pair: checked as part of the I-601's packet."""
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    situation = v("i212.situation")
    if pair(graph) and not in_pair:
        out.append("Filed with Form I-601 in one envelope: build the I-601's packet (More..., Waiver of inadmissibility (I-601)). It carries both "
                   "forms, one cover letter and both fees. Don't mail this one alone.")
    if situation == IV_WITH_601 and v(PAIR_KEY) == "No":
        out.append("Where it is filed says 'with a Form I-601', but Part 1, 19 says No: correct one of them.")
    if not any(v(k) == "Yes" for k in ("i212.arriving", "i212.deportable", "i212.reentry_after_presence", "i212.reentry_after_removal")) \
            and all(v(k) for k in ("i212.arriving", "i212.deportable", "i212.reentry_after_presence", "i212.reentry_after_removal")):
        out.append(held(ATTORNEY, "Part 2: no reason chosen. The I-212 is for a client inadmissible under INA 212(a)(9)(A) or (C) (the I-212 instructions, page 1): "
                        "if neither applies, the client may not need it."))
    for yes, which, when, label in (("i212.arriving", "i212.arriving_which", "i212.arriving_date", "1.A"),
                                    ("i212.deportable", "i212.deportable_which", "i212.deportable_date", "5.A")):
        if v(yes) == "Yes" and not (v(which) and v(when)):
            out.append(held(CLIENT, f"Part 2, {label} is Yes: say which applies and the date removed."))
    nine_c = v("i212.reentry_after_presence") == "Yes" or v("i212.reentry_after_removal") == "Yes"
    if nine_c and situation in IN_US:
        out.append(held(ATTORNEY, "Inadmissible under INA 212(a)(9)(C): consent to reapply can't be asked for from inside the U.S. (the I-212 instructions, "
                        "pages 3 and 5)."))
    last = max((d for d in (_d(v("i212.presence_departed")), _d(v("i212.removal_date"))) if d), default=None)
    if nine_c and last and plus_years(last, 10) > today:
        out.append(f"Inadmissible under INA 212(a)(9)(C), and the departure on {us(last)} was less than 10 years ago: the I-212 can be filed from "
                   f"{us(plus_years(last, 10))} at the earliest (the I-212 instructions, pages 3 and 5).")
    place = where(graph)
    if situation and not place["mail_to"] and not in_pair:
        out.append(f"Filed with {place['text']}.")
    if situation == VAWA and not place["mail_to"]:
        out.append("A VAWA filing goes to the lockbox for the client's state: the client's state isn't in the case or isn't on USCIS's list.")
    if v("i212.status_sought") == "Other" and not v("i212.status_other"):
        out.append("Part 3, 1: 'Other' needs the status explained (1.D).")
    return out


def letter(graph, today: date) -> dict[str, Any]:
    import fees

    edition = fees.load(today).get("edition") or "current"
    amount, _why = fee(graph, today)
    if value(graph, "i212.situation") == COURT:
        text = "The filing fee for Form I-212 is paid as the immigration court instructs."
    elif amount == 0:
        text = f"No filing fee is required for this Form I-212: {FEE_EXEMPT[exempt(graph)]} (Form G-1055, edition {edition})."
    elif amount:
        text = f"Enclosed is the filing fee of {money(amount)} for Form I-212, paid by the enclosed Form G-1450 (Form G-1055, edition {edition})."
    else:
        text = "Filing fee: [the attorney sets it. See the packet's problems]."
    place = where(graph)
    return {"re_lines": [RE_LINE] + _case_line(graph),
            "mail_to": place["mail_to"] or ["[the attorney sets the address: see the packet's problems]"],
            "fees": text, "no_payment": not amount or value(graph, "i212.situation") == COURT}


RE_LINE = "Application: I-212 Application for Permission to Reapply for Admission into the United States After Deportation or Removal"


def _case_line(graph) -> list[str]:
    if value(graph, "i212.consular_case_number"):
        return [f"Department of State Case Number {value(graph, 'i212.consular_case_number')}"]
    if value(graph, "i212.i485_receipt"):
        return [f"Form I-485 Receipt Number {value(graph, 'i212.i485_receipt')}"]
    return []


def confidential(graph) -> bool:
    """A VAWA, T or U case (8 U.S.C. 1367): the I-601's test (src/inadmissibility_waiver.py), which reads this form's situation too."""
    import inadmissibility_waiver

    return inadmissibility_waiver.confidential(graph)


def client_questions(graph) -> dict[str, dict[str, str]]:
    return CLIENT_QUESTIONS


def case_schema(schema: dict[str, Any], graph, today: date) -> dict[str, Any]:
    """Not mailed to a lockbox (a field office, the court): no Form G-1145 on top (src/enotice.py)."""
    return schema | {"lockbox": bool(where(graph)["lockbox"])}
