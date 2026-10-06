"""The waiver of grounds of inadmissibility, Form I-601 (edition 01/20/25; uscis.gov/i-601, updated 06/01/2026, read 10/02/2026;
the Instructions of the same edition):

  Who: an applicant for an immigrant visa or a K or V visa found inadmissible by a consular officer at the interview, or for
    adjustment of status, who is inadmissible on a ground the form lists (Part 4, Section A); a T nonimmigrant or a Special
    Immigrant Juvenile adjusting (Section B). The provisional waiver of the unlawful presence bar alone is Form I-601A, not this
    form (Instructions, page 1). The waiver covers only the grounds and the events listed in it (Instructions, page 2).
  The standard, by ground (Instructions, "Reasons for Inadmissibility", pages 9-16): criminal grounds (INA 212(h)): extreme
    hardship to a U.S. citizen or permanent resident spouse, parent, son or daughter (or the K visa petitioner), or 15 years since
    the events with rehabilitation, or prostitution only with rehabilitation, or an approved VAWA self-petitioner; fraud or
    misrepresentation (212(i)): extreme hardship to a U.S. citizen or permanent resident spouse or parent (or the K visa
    petitioner); the 3- or 10-year unlawful presence bar (212(a)(9)(B)(v)): the same relatives; an SIJ adjusting (245(h)): no
    qualifying relative and no hardship, humanitarian purposes, family unity or the public interest. Always a matter of
    discretion: favorable factors outweighing the unfavorable (Instructions, page 17).
  Where ("Direct Filing Addresses for Form I-601", uscis.gov/i-601-addresses, updated 10/23/2025, read 10/02/2026):
    an immigrant visa, K or V visa applicant found inadmissible after the interview: USCIS Phoenix Lockbox, Attn: I-601 Foreign
      Filers, P.O. Box 21600, Phoenix, AZ 85036-1600 (couriers: Attn: I-601 Foreign Filers (Box 21600), 2108 E. Elliot Rd., Tempe,
      AZ 85284-1806);
    the I-485 pending: a receipt number beginning MSC or IOE, or with no 3-letter code: USCIS Chicago Lockbox, Attn: AOS, P.O. Box
      805887, Chicago, IL 60680 (couriers: Attn: AOS (Box 805887), 131 S. Dearborn-3rd Floor, Chicago, IL 60603-5517); EAC, LIN,
      SRC or WAC: USCIS Dallas Lockbox, Attn: NFB, P.O. Box 660867, Dallas, TX 75266-0867 (couriers: Attn: NFB (Box 660867), 2501
      S. State Hwy. 121 Business, Suite 400, Lewisville, TX 75067-8003);
    filed together with the I-485: "Follow the Form I-485 Instructions" (the I-485's own package: not built here);
    an approved VAWA self-petitioner seeking an immigrant visa, and a T nonimmigrant filing it "together with or based on a pending
      Form I-485": the "Attn: 1367" lockboxes by state (USCIS's VAWA, T and U filing addresses, updated 02/05/2026, which list the
      I-601);
    with Form I-821 (TPS): as the Federal Register notice for the country's designation says (no address on the page);
    in removal proceedings: the immigration court, as it instructs.
    With a Form I-212 in the same envelope (an immigrant visa applicant outside the U.S.): USCIS's I-212 page sends the pair to
    Phoenix, Attn: I-212 Foreign Filers, P.O. Box 21600 (src/reapply.py): the same box, its own Attn line. DRAFT: the attorney
    confirms the Attn line for the pair.
  Fee (Form G-1055 10/01/26, page 20): $1,050; $0 for a person seeking or granted SIJ, T or U status, an abused spouse or child
    adjusting under the Cuban Adjustment Act or HRIFA, NACARA benefits, a VAWA self-petitioner (including derivatives), an Afghan
    or Iraqi special immigrant (or a derivative), an Afghan diplomat or family member filing with an I-485 under Pub. L. 85-316
    section 13, or an Indochinese refugee adjusting under Pub. L. 95-145 (SIJ, VAWA, T and U are read from the case, the
    others are the attorney's choice). No biometric services fee at filing (Instructions, page 3).

DRAFT for the attorney.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import reapply
from filing_questions import DATE, LINES, TEXT, YES_NO, money, putter, sij, state_of, value
from holders import ATTORNEY, OFFICE, held, producer

TITLE = "Waiver of inadmissibility (I-601)"
SOURCE = "https://www.uscis.gov/i-601-addresses"
CONSULAR = "Immigrant visa: found inadmissible after the consular interview"
KV = "K or V visa: found inadmissible after the consular interview"
PENDING = "Adjustment: the client's I-485 is pending"
WITH_I485 = "Adjustment: filed together with the I-485"
VAWA = "An approved VAWA self-petitioner seeking an immigrant visa"
T_I485 = "A T nonimmigrant, with or on a pending I-485"  # the page's row: "filing Form I-601 together with or based on a pending Form I-485"
TPS = "With Form I-821 (Temporary Protected Status)"
COURT = "In removal proceedings (immigration court)"
SITUATIONS = [CONSULAR, KV, PENDING, WITH_I485, VAWA, T_I485, TPS, COURT]
CONFIDENTIAL = (VAWA, T_I485)  # filed with the "Attn: 1367" lockboxes (8 U.S.C. 1367): src/enotice.py
# uscis.gov/i-601-addresses (updated 10/23/2025, read 10/02/2026)
PHOENIX = ["USCIS", "ATTN: I-601 FOREIGN FILERS", "P.O. BOX 21600", "PHOENIX, AZ 85036-1600"]
PHOENIX_COURIER = ["USCIS", "ATTN: I-601 FOREIGN FILERS (BOX 21600)", "2108 E. ELLIOT RD.", "TEMPE, AZ 85284-1806"]
CHICAGO = ["USCIS", "ATTN: AOS", "P.O. BOX 805887", "CHICAGO, IL 60680"]
CHICAGO_COURIER = ["USCIS", "ATTN: AOS (BOX 805887)", "131 S. DEARBORN-3RD FLOOR", "CHICAGO, IL 60603-5517"]
DALLAS = ["USCIS", "ATTN: NFB", "P.O. BOX 660867", "DALLAS, TX 75266-0867"]
DALLAS_COURIER = ["USCIS", "ATTN: NFB (BOX 660867)", "2501 S. STATE HWY. 121 BUSINESS", "SUITE 400", "LEWISVILLE, TX 75067-8003"]
CHICAGO_PREFIXES, DALLAS_PREFIXES = ("MSC", "IOE"), ("EAC", "LIN", "SRC", "WAC")
NO_FEE = "None of these"
SIJ_EXEMPT, T_EXEMPT, VAWA_EXEMPT = "Seeking or granted SIJ classification", "Seeking or granted T nonimmigrant status", "A VAWA self-petitioner (including derivatives)"
U_EXEMPT = "Seeking or granted U nonimmigrant status"
# Form G-1055 (10/01/26), the I-601 rows, page 20: each $0 category (the choice) and the cover letter's words for it
FEE_EXEMPT = {
    SIJ_EXEMPT: "the applicant is seeking or was granted Special Immigrant Juvenile classification",
    T_EXEMPT: "the applicant is seeking or was granted T nonimmigrant status",
    U_EXEMPT: "the applicant is seeking or was granted U nonimmigrant status",
    "An abused spouse or child adjusting under the Cuban Adjustment Act or HRIFA":
        "the applicant is seeking or was granted adjustment of status as an abused spouse or child under the Cuban Adjustment Act or the "
        "Haitian Refugee Immigration Fairness Act",
    "Seeking or granted benefits under NACARA": "the applicant is seeking or was granted benefits under the Nicaraguan Adjustment and Central "
                                                "American Relief Act",
    VAWA_EXEMPT: "the applicant is seeking or was granted immigrant classification as a VAWA self-petitioner",
    "An Afghan or Iraqi special immigrant (translator or interpreter, U.S. Government or ISAF employee), or a derivative":
        "the applicant is seeking or was granted a special immigrant visa or adjustment of status as an Afghan or Iraqi national the Form "
        "G-1055 names, or a derivative beneficiary",
    "An Afghan diplomat or family member with valid A or G status on 07/14/2021, filing with an I-485 (Pub. L. 85-316, section 13)":
        "the applicant files it in connection with a Form I-485 under section 13 of Pub. L. 85-316",
    "An Indochinese refugee adjusting under Pub. L. 95-145": "the applicant is adjusting status as an Indochinese refugee under Pub. L. 95-145",
}
# Part 4, Section A: one box each (the form's own words, shortened)
GROUNDS = [("i601.g01_disease", "1. A communicable disease of public health significance"),
           ("i601.g02_vaccination", "2. An exemption from the vaccination requirement (religious beliefs or moral convictions)"),
           ("i601.g03_disorder", "3. A physical or mental disorder with harmful behavior associated with it"),
           ("i601.g04_cimt", "4. A crime involving moral turpitude (not a purely political offense)"),
           ("i601.g05_marijuana", "5. A single offense of simple possession of 30 grams or less of marijuana"),
           ("i601.g06_two_convictions", "6. Two or more convictions with combined sentences of 5 years or more"),
           ("i601.g07_prostitution", "7. Prostitution (coming to engage in it, or in the past 10 years)"),
           ("i601.g08_procuring", "8. Procuring prostitutes or persons for prostitution (in the past 10 years)"),
           ("i601.g09_vice", "9. Any other unlawful commercialized vice"),
           ("i601.g10_immunity", "10. Serious criminal activity, with immunity from prosecution asserted"),
           ("i601.g11_totalitarian", "11. Membership in or affiliation with a Communist or other totalitarian party"),
           ("i601.g12_fraud", "12. Immigration fraud or misrepresentation of a material fact"),
           ("i601.g13_smuggling", "13. Alien smuggling"),
           ("i601.g14_civil_penalty", "14. A civil penalty: a final order under INA 274C"),
           ("i601.g15_unlawful_presence", "15. The 3-year or 10-year bar for unlawful presence"),
           ("i601.g16_removed", "16. Previously removed (NACARA and HRIFA only: everyone else files Form I-212)"),
           ("i601.g17_reentry", "17. Ordered removed or unlawfully present over 1 year, then reentered without admission (NACARA, HRIFA, "
                                "approved VAWA only: everyone else files Form I-212)")]
HARDSHIP_GROUNDS = ("i601.g12_fraud", "i601.g15_unlawful_presence")  # 212(i), 212(a)(9)(B)(v): extreme hardship to a spouse or parent
CRIMINAL_GROUNDS = ("i601.g04_cimt", "i601.g05_marijuana", "i601.g06_two_convictions", "i601.g07_prostitution", "i601.g08_procuring",
                    "i601.g09_vice", "i601.g10_immunity")  # 212(h)
SPOUSE_OR_PARENT = ("Spouse", "Parent")
OWN_SECTIONS = [
    ("Where it is filed", "the attorney", [
        ("i601.situation", "Where the client stands (it decides where the I-601 is filed)", {"type": "choice", "options": SITUATIONS}, True),
        (reapply.PAIR_KEY, "Part 1, 19: Form I-212 filed with it, in the same envelope?", YES_NO, True),
        ("i601.fee_exempt", "No fee (Form G-1055): the client files as", {"type": "choice", "options": [NO_FEE, *FEE_EXEMPT]}, False),
    ]),
    ("The visa or the I-485 (Part 1, 15-18)", "the attorney", [
        ("i601.consular_case_number", "Part 1, 15.A: the Department of State case number (the NVC case number, unless the refusal shows another)", TEXT, False),
        ("i601.consulate_city", "Part 1, 15.B: the embassy or consulate of the interview: city", TEXT, False),
        ("i601.consulate_country", "Part 1, 15.B: the embassy or consulate of the interview: country", TEXT, False),
        ("i601.i485_filed", "Part 1, 16.A: filed after the client's I-485?", YES_NO, False),
        ("i601.i485_receipt", "Part 1, 16.B: the I-485 receipt number (it decides the lockbox)", TEXT, False),
        ("i601.prior_i212", "Part 1, 18.A: a Form I-212 filed before?", YES_NO, False),
        ("i601.prior_i212_receipt", "Part 1, 18.B: its receipt number", TEXT, False),
        ("i601.prior_i212_where", "Part 1, 18.C: where it was filed", TEXT, False),
        ("i601.prior_i212_date", "Part 1, 18.D: the date it was filed", DATE, False),
    ]),
    ("Grounds of inadmissibility (Part 4): every ground that applies or that the client was told applies", "the attorney",
     [(key, label, YES_NO, False) for key, label in GROUNDS] + [
        ("i601.g18_other", "18. Another ground: specify it", TEXT, False),
        ("i601.section_b", "Section B, 19 (only a T nonimmigrant or a Special Immigrant Juvenile adjusting): the grounds, specified", TEXT, False),
        ("i601.inadmissibility_statement", "40. The statement: the acts, convictions or diagnoses and their dates (or 'See attached letter')", LINES, True),
    ]),
    ("The qualifying relative (Part 5)", "the attorney", [
        ("i601.relative_family_name", "1.A: the qualifying relative's family name", TEXT, False),
        ("i601.relative_given_name", "1.B: their given name", TEXT, False),
        ("i601.relative_middle_name", "1.C: their middle name", TEXT, False),
        ("i601.relative_street", "2.A: where they live: street", TEXT, False),
        ("i601.relative_unit_type", "2.B: an apartment, a suite or a floor", {"type": "choice", "options": ["APT", "STE", "FLR"]}, False),
        ("i601.relative_apt", "2.B: its number", TEXT, False),
        ("i601.relative_city", "2.C: city or town", TEXT, False),
        ("i601.relative_state", "2.D: state (two letters)", TEXT, False),
        ("i601.relative_zip", "2.E: ZIP code", TEXT, False),
        ("i601.relative_phone", "3: their daytime telephone", TEXT, False),
        ("i601.relative_email", "4: their email", TEXT, False),
        ("i601.relative_relationship", "5: their relationship to the client (for example Spouse, Parent)", TEXT, False),
        ("i601.relative_status", "6: their immigration status", {"type": "choice", "options": ["U.S. citizen", "Lawful permanent resident"]}, False),
        ("i601.relative_a_number", "7: their A-Number, if any", TEXT, False),
        ("i601.relative_dob", "8: their date of birth", DATE, False),
        ("i601.vawa_self", "A VAWA self-petitioner claiming extreme hardship to themself?", YES_NO, False),
        ("i601.more_relatives", "More qualifying relatives (listed in Part 10)?", YES_NO, False),
        ("i601.hardship_statement", "9. The extreme hardship statement, in brief (or 'See attached letter')", LINES, False),
    ]),
    ("Discretion (Part 6)", "the attorney", [
        ("i601.discretion_statement", "9. Why the waiver should be granted as a matter of discretion (or 'See attached letter')", LINES, True),
    ]),
    ("The client's own account (asked in the portal)", "the client", [
        ("i601.client_what_happened", "In the client's words: what happened that may make them inadmissible, with dates and places", LINES, False),
        ("i601.client_hardship", "In the client's words: how their U.S. citizen or permanent resident family would suffer", LINES, False),
        ("i601.client_life", "In the client's words: their life, family, work and community, and what they regret", LINES, False),
    ]),
]
_own = {key for _s, _w, items in OWN_SECTIONS for key, *_ in items}
# with an I-212 in the same envelope, its questions here too: one panel, one packet (src/reapply.py)
SECTIONS = OWN_SECTIONS + [(f"Form I-212: {title}", who, [q for q in items if q[0] not in _own], reapply.pair)
                           for title, who, items in reapply.OWN_SECTIONS if [q for q in items if q[0] not in _own]]
MORE_QUESTIONS = "The Form I-212's questions appear here when Part 1, 19 says it goes in the same envelope."
# The long answers, asked in the client's portal in the language they read it in (filing_questions.client_questions). DRAFT: the
# attorney approves the wording and a certified translator the Portuguese and Spanish; the Haitian Creole is a machine draft.
CLIENT_QUESTIONS = {
    "i601.client_what_happened": {
        "en": "In your own words: what happened that may keep you from getting your green card or visa (for example an arrest, a false "
              "document, time without papers in the U.S., an illness)? Give the dates and places you remember.",
        "pt": "Com suas palavras: o que aconteceu que pode impedir você de receber o green card ou o visto (por exemplo, uma prisão, um "
              "documento falso, tempo sem documentos nos EUA, uma doença)? Diga as datas e os lugares de que você se lembra.",
        "es": "Con sus propias palabras: ¿qué pasó que podría impedirle obtener la green card o la visa (por ejemplo, un arresto, un documento "
              "falso, tiempo sin papeles en EE. UU., una enfermedad)? Indique las fechas y los lugares que recuerde.",
        "ht": "Avèk pwòp mo pa ou: kisa ki te pase ki ka anpeche ou jwenn green card oswa viza a (pa egzanp yon arestasyon, yon fo dokiman, "
              "tan ou te pase san papye Ozetazini, yon maladi)? Bay dat ak kote ou sonje yo."},
    "i601.client_hardship": {
        "en": "If you cannot live in the U.S., how would it hurt your husband or wife, your parents or your children who are U.S. citizens or "
              "permanent residents? Tell us about their health, money, work, school and anything else.",
        "pt": "Se você não puder morar nos EUA, como isso prejudicaria seu marido ou sua esposa, seus pais ou seus filhos que são cidadãos "
              "americanos ou residentes permanentes? Conte sobre a saúde, o dinheiro, o trabalho, a escola deles e qualquer outra coisa.",
        "es": "Si usted no pudiera vivir en EE. UU., ¿cómo afectaría eso a su esposo o esposa, a sus padres o a sus hijos que son ciudadanos "
              "estadounidenses o residentes permanentes? Cuéntenos sobre su salud, su dinero, su trabajo, la escuela y cualquier otra cosa.",
        "ht": "Si ou pa ka viv Ozetazini, ki jan sa t ap fè mari ou oswa madanm ou, paran ou oswa pitit ou ki sitwayen ameriken oswa rezidan "
              "pèmanan soufri? Pale nou sou sante yo, lajan, travay, lekòl ak nenpòt lòt bagay."},
    "i601.client_life": {
        "en": "Tell us about your life: your family in the U.S., your work, your community, your church, and the good you have done. Also "
              "anything you regret, and what you have done since.",
        "pt": "Conte sobre a sua vida: sua família nos EUA, seu trabalho, sua comunidade, sua igreja e o bem que você fez. Conte também o que "
              "você lamenta e o que fez desde então.",
        "es": "Cuéntenos sobre su vida: su familia en EE. UU., su trabajo, su comunidad, su iglesia y el bien que ha hecho. También lo que "
              "lamenta y lo que ha hecho desde entonces.",
        "ht": "Pale nou sou lavi ou: fanmi ou Ozetazini, travay ou, kominote ou, legliz ou, ak byen ou te fè. Pale nou tou sou sa ou regrèt, "
              "ak sa ou fè depi lè sa a."},
} | reapply.CLIENT_QUESTIONS


def client_questions(graph) -> dict[str, dict[str, str]]:
    """The portal questions this case asks: the I-212's only when it goes in the same envelope."""
    return {k: q for k, q in CLIENT_QUESTIONS.items() if not k.startswith("i212.") or reapply.pair(graph)}


def _relative(graph) -> tuple[str | None, str | None]:
    """(the petitioner's relationship to the client, their status) when the petitioner is the client's spouse, parent, son or daughter."""
    rel, status = value(graph, "family.relationship"), value(graph, "petitioner.status")
    relationship = {"Spouse": "Spouse", "Child": "Parent", "Parent": "Son or daughter"}.get(rel)
    return relationship, {"USC": "U.S. citizen", "LPR": "Lawful permanent resident"}.get(status)


def _receipt(graph) -> str | None:
    from filing_questions import pending

    found = pending(graph, "I-485")
    return value(graph, "i601.i485_receipt") or (found["receipt"] if found else None)


def derive(graph, today: date):
    from filing_questions import addresses

    put = putter(graph, "i601.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    addresses(graph, put, "i601")
    put("i601.situation", CONSULAR if v("visa.result") == "Refused" else None, "the consulate refused the visa (the immigrant visa page)")
    situation = v("i601.situation")
    put("i601.consular_case_number", v("visa.nvc_case_number"), "the NVC case number (the immigrant visa case)")
    put("i601.consulate_city", v("visa.consulate"), "the consulate of the visa interview")
    receipt = _receipt(graph)
    put("i601.i485_receipt", receipt, "the I-485 receipt notice")
    put("i601.i485_filed", "Yes" if receipt or situation == PENDING else "No" if situation in (CONSULAR, KV, VAWA) else None,
        "the case's I-485 receipt notice" if receipt else "where the client stands")
    relationship, status = _relative(graph)
    if relationship and status:  # the petitioner: a qualifying relative for some grounds (the attorney decides which apply)
        put("i601.relative_family_name", v("petitioner.family_name"), "the petitioner")
        put("i601.relative_given_name", v("petitioner.given_name"), "the petitioner")
        put("i601.relative_middle_name", v("petitioner.middle_name"), "the petitioner")
        put("i601.relative_relationship", relationship.upper(), "the petitioner: the client's " + relationship.lower())
        put("i601.relative_status", status, "the petitioner's status")
        put("i601.relative_dob", v("petitioner.dob"), "the petitioner")
        put("i601.relative_a_number", v("petitioner.a_number"), "the petitioner")
        same = v("petitioner.mailing_same_as_physical") == "Yes"
        for part in ("street", "unit_type", "apt", "city", "state", "zip"):  # Part 5 asks where they live
            put(f"i601.relative_{part}", v(f"petitioner.physical_{part}") or (v(f"petitioner.mailing_{part}") if same else None),
                "the petitioner's home address")
        put("i601.relative_phone", v("petitioner.daytime_phone") or v("petitioner.mobile_phone"), "the petitioner")
        put("i601.relative_email", v("petitioner.email"), "the petitioner")
    for key, box in (("i601.g18_other", "i601.g18_box"), ("i601.section_b", "i601.section_b_box")):  # a ground written in ticks its box
        put(box, "Yes" if v(key) else None, "a ground is written in")
    if reapply.pair(graph):  # the I-212 in the same envelope: its facts too, and the G-28 names both forms
        put("i212.situation", reapply.IV_WITH_601 if situation == CONSULAR else None, "filed with the I-601 from abroad")
        reapply.derive(graph, today)
        put("companion.g28_forms_i601", "I-601, I-212", "the I-601 and the I-212 in one envelope")
    return graph


def _situation_where(graph) -> dict[str, Any]:
    from filing_questions import lockbox

    situation = value(graph, "i601.situation")
    if situation in (CONSULAR, KV):
        return {"mail_to": PHOENIX, "lockbox": True, "text": " / ".join(PHOENIX) + " (couriers: " + " / ".join(PHOENIX_COURIER) + ")"}
    if situation == PENDING:
        receipt = re.sub(r"[^A-Z0-9]", "", str(_receipt(graph) or "").upper())
        if not receipt:
            return {"mail_to": None, "lockbox": True, "text": "the Chicago or Dallas lockbox, by the I-485's receipt number: it isn't in the case yet"}
        if receipt.startswith(CHICAGO_PREFIXES) or receipt[:3].isdigit():
            return {"mail_to": CHICAGO, "lockbox": True, "text": " / ".join(CHICAGO) + " (couriers: " + " / ".join(CHICAGO_COURIER) + "), "
                                                                 f"by the I-485 receipt number {receipt}"}
        if receipt.startswith(DALLAS_PREFIXES):
            return {"mail_to": DALLAS, "lockbox": True, "text": " / ".join(DALLAS) + " (couriers: " + " / ".join(DALLAS_COURIER) + "), "
                                                               f"by the I-485 receipt number {receipt}"}
        return {"mail_to": None, "lockbox": True, "text": f"the lockbox for an I-485 receipt beginning {receipt[:3]}: USCIS's page names only MSC, IOE "
                                                          "and no code (Chicago) and EAC, LIN, SRC and WAC (Dallas). The attorney sets the address"}
    if situation == WITH_I485:
        return {"mail_to": None, "lockbox": False, "text": "the I-485's own package and address ('Follow the Form I-485 Instructions'): this packet "
                                                            "doesn't build that envelope. Print the I-601 from here and put it in the I-485 packet"}
    if situation in CONFIDENTIAL:  # the page sends both rows to the VAWA, T and U filing addresses, by the client's state
        lines, name = lockbox("uscis_lockboxes_vawa", state_of(graph))
        return {"mail_to": lines, "lockbox": True,
                "text": (" / ".join(lines) + f" (the {name} lockbox, by the client's state: USCIS's VAWA, T and U filing addresses)") if lines else
                        "the VAWA, T and U lockbox for the client's state: the client's state isn't in the case or isn't on USCIS's list"}
    if situation == TPS:
        return {"mail_to": None, "lockbox": False, "text": "the address in the Federal Register notice for the client's country's TPS designation "
                                                            "(USCIS's I-601 page): the attorney sets the address"}
    if situation == COURT:
        return {"mail_to": None, "lockbox": False, "text": "the immigration court, as the court instructs: not mailed to USCIS"}
    return {"mail_to": None, "lockbox": False, "text": "not known until the attorney says where the client stands"}


def where(graph) -> dict[str, Any]:
    """{"mail_to", "lockbox", "text"}: the I-601's own address, or the pair's (the I-212 page's row for filing them together)."""
    if reapply.pair(graph):
        if value(graph, "i601.situation") == CONSULAR and value(graph, "i212.situation") in (None, reapply.IV_WITH_601):
            return reapply.where(graph) | {"pair": True}
        return {"mail_to": None, "lockbox": False, "pair": True,
                "text": "the I-601 and the I-212 together: USCIS's I-212 page gives one address for the pair, for an immigrant visa applicant "
                        "outside the U.S. (both forms' 'where the client stands' say so). For anything else the attorney sets where they go"}
    return _situation_where(graph)


def t_case(graph) -> bool:
    """A T visa case, as the case path reads it (src/journey.py track_of): an I-914 notice or the I-914's own answers."""
    import journey

    return "I-914" in {n["form"] for n in journey.notices(graph)} or any(k.startswith("tvisa.") and value(graph, k) for k in graph.all_facts())


def u_case(graph) -> bool:
    """A U visa case, as the case path reads it: an I-918 notice or the U filings' own answers."""
    import journey

    return "I-918" in {n["form"] for n in journey.notices(graph)} or any(k.startswith("uvisa.") and value(graph, k) for k in graph.all_facts())


def vawa_case(graph) -> bool:
    return bool(value(graph, "vawa.classification")) or value(graph, "i601.situation") == VAWA or value(graph, "i212.situation") == reapply.VAWA


def confidential(graph) -> bool:
    """A VAWA, T or U case (8 U.S.C. 1367): the receipt e-mail goes to the office, never the client's own (src/enotice.py)."""
    return vawa_case(graph) or value(graph, "i601.situation") in CONFIDENTIAL or t_case(graph) or u_case(graph)


def exempt(graph) -> str | None:
    """The Form G-1055 $0 category this case files under: the case's own evidence (SIJ, a VAWA self-petition, a T or U case),
    else the attorney's choice."""
    if sij(graph):
        return SIJ_EXEMPT
    if vawa_case(graph):
        return VAWA_EXEMPT
    if t_case(graph) or value(graph, "i601.situation") == T_I485:
        return T_EXEMPT
    if u_case(graph):
        return U_EXEMPT
    chosen = value(graph, "i601.fee_exempt")
    return chosen if chosen in FEE_EXEMPT else None


def fee(graph, today: date) -> tuple[int | None, str]:
    import fees

    paper = fees.load(today).get("paper") or {}
    category = exempt(graph)
    if category:
        return paper.get("i601_exempt"), f"{category}: no fee (G-1055)"
    return paper.get("i601"), "the I-601 fee (G-1055)"


def payments(graph, today: date, forms: list[str]) -> list[tuple[str, str, Any, str]]:
    """One payment per form (src/payment.py: one G-1450 for each benefit request); none when the court collects it."""
    out = [] if value(graph, "i601.situation") == COURT else [("i601", "I-601", fee(graph, today)[0], "Form I-601 filing fee")]
    if "i212" in forms:
        out += reapply.payments(graph, today, forms)
    return out


def notes(graph, today: date) -> list[dict[str, str]]:
    v = lambda k: value(graph, k)  # noqa: E731
    amount, why = fee(graph, today)
    place = where(graph)
    fee_text = "Paid as the immigration court instructs" if v("i601.situation") == COURT else f"{money(amount)}: {why}"
    if reapply.pair(graph):
        fee_text += "; the I-212 " + (lambda a, w: f"{money(a)}: {w}")(*reapply.fee(graph, today)) + ". Each fee is its own payment"
    out = [{"level": "info", "title": "What it waives",
            "text": "Only the grounds, and only the events, listed in it: list every ground and every event (the I-601 instructions, page 2). The "
                    "provisional waiver of the unlawful presence bar alone, before the consular interview, is the I-601A."},
           {"level": "info", "title": "Fee and where",
            "text": f"{fee_text}. Filed with {place['text']} ("
                    + ("USCIS's I-212 filing addresses, the row for filing it with an I-601" if place.get("pair") else "USCIS's I-601 filing addresses") + ")."},
           {"level": "info", "title": "The standard, by ground",
            "text": "Criminal grounds (INA 212(h)): extreme hardship to a U.S. citizen or permanent resident spouse, parent, son or daughter, or 15 "
                    "years since the events with rehabilitation. Fraud or misrepresentation (212(i)) and the unlawful presence bar "
                    "(212(a)(9)(B)(v)): extreme hardship to a U.S. citizen or permanent resident spouse or parent. An SIJ adjusting (245(h)): "
                    "no qualifying relative needed. Always discretionary (the I-601 instructions, pages 9-17)."}]
    if any(v(k) == "Yes" for k in ("i601.g16_removed", "i601.g17_reentry")):
        out.append({"level": "warn", "title": "Items 16 and 17",
                    "text": "On the I-601 only for NACARA and HRIFA applicants (and, for 17, an approved VAWA self-petitioner). Everyone else asks "
                            "for consent to reapply on Form I-212 (the form's own words)."})
    if not reapply.pair(graph) and v("i601.situation") == CONSULAR and (v("waiver.q29_final_order") == "Yes" or v("visa.result") == "Refused"):
        out.append({"level": "info", "title": "A Form I-212 too?",
                    "text": "If the client was removed, or reentered without admission after a removal or after more than a year of unlawful "
                            "presence, the consulate's refusal may name INA 212(a)(9)(A) or (C) as well: then the I-212 goes in the same envelope "
                            "(Part 1, 19)."})
    if place.get("pair"):
        out.append({"level": "warn", "title": "The pair's address",
                    "text": "USCIS's I-601 page sends the I-601 to Attn: I-601 Foreign Filers; its I-212 page sends the I-212 'together with Form "
                            "I-601' to Attn: I-212 Foreign Filers, the same P.O. Box 21600. The cover letter uses the I-212 page's line: the "
                            "attorney confirms."})
    # the I-212's own notes after its first two (what it is; its fee and address, said above), but not its pointer to this packet
    return out + ([n for n in reapply.notes(graph, today)[2:] if n["title"] != "With the I-601"] if reapply.pair(graph) else [])


@producer(ATTORNEY)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    situation = v("i601.situation")
    grounds = [k for k, _label in GROUNDS if v(k) == "Yes"] + [k for k in ("i601.g18_other", "i601.section_b") if v(k)]
    if not grounds:
        out.append(held(OFFICE, "Part 4: no ground of inadmissibility chosen. The waiver covers only the grounds listed in it (the I-601 instructions, page 2)."))
    if v("i601.section_b") and exempt(graph) not in (SIJ_EXEMPT, T_EXEMPT):
        out.append("Part 4, Section B is only for a T nonimmigrant or a Special Immigrant Juvenile adjusting status: the case shows neither.")
    needs_relative = any(v(k) == "Yes" for k in HARDSHIP_GROUNDS) and not (v("vawa.classification") or situation == VAWA)
    relationship = v("i601.relative_relationship")
    if needs_relative and not v("i601.hardship_statement"):
        out.append("Part 5, 9: fraud or misrepresentation and the unlawful presence bar need extreme hardship to a U.S. citizen or permanent "
                   "resident spouse or parent (the I-601 instructions, pages 11-12): write the statement, or 'See attached letter'.")
    if needs_relative and not (v("i601.relative_family_name") and relationship):
        out.append("Part 5: no qualifying relative. Fraud or misrepresentation and the unlawful presence bar need a U.S. citizen or permanent "
                   "resident spouse or parent.")
    elif needs_relative and relationship and str(relationship).strip().capitalize() not in SPOUSE_OR_PARENT \
            and not any(v(k) == "Yes" for k in CRIMINAL_GROUNDS):
        out.append(f"Part 5: a {str(relationship).lower()} isn't a qualifying relative for fraud or misrepresentation or the unlawful presence "
                   "bar: only a spouse or parent (the I-601 instructions, pages 11-12). The attorney names one, or explains.")
    if situation in (PENDING, WITH_I485) and t_case(graph):
        out.append(held(OFFICE, "A T nonimmigrant's I-601 filed with or on a pending I-485 goes to the VAWA, T and U lockbox for the client's state "
                        "(USCIS's I-601 filing addresses): choose that row under where the client stands."))
    place = where(graph)
    if situation and not place["mail_to"]:
        out.append(held(OFFICE, f"Filed with {place['text']}."))
    if reapply.pair(graph):
        if situation and situation != CONSULAR:
            out.append("The I-601 and the I-212 in one envelope: USCIS's I-212 page gives that filing for an immigrant visa applicant outside the "
                       "U.S. For this case the attorney confirms they go together, and where.")
        out += reapply.problems(client_dir, graph, today, in_pair=True)  # its unanswered questions are this panel's (SECTIONS)
    return out


RE_LINE = "Application: I-601 Application for Waiver of Grounds of Inadmissibility"


def letter(graph, today: date) -> dict[str, Any]:
    import fees

    edition = fees.load(today).get("edition") or "current"
    amount, _why = fee(graph, today)
    if value(graph, "i601.situation") == COURT:
        text = "The filing fee for Form I-601 is paid as the immigration court instructs."
    elif amount == 0:
        text = f"No filing fee is required for this Form I-601: {FEE_EXEMPT[exempt(graph)]} (Form G-1055, edition {edition})."
    elif amount:
        text = f"Enclosed is the filing fee of {money(amount)} for Form I-601, paid by the enclosed Form G-1450 (Form G-1055, edition {edition})."
    else:
        text = "Filing fee: [the attorney sets it. See the packet's problems]."
    pays = [p for p in payments(graph, today, ["i212"] if reapply.pair(graph) else []) if p[2]]
    re_lines = [RE_LINE]
    if reapply.pair(graph):
        re_lines = [RE_LINE, "and " + reapply.RE_LINE.removeprefix("Application: ")]
        text += " " + reapply.letter(graph, today)["fees"] + (" Each fee is paid by its own Form G-1450." if len(pays) > 1 else "")
    case = (f"Department of State Case Number {value(graph, 'i601.consular_case_number')}" if value(graph, "i601.consular_case_number") else
            f"Form I-485 Receipt Number {value(graph, 'i601.i485_receipt')}" if value(graph, "i601.i485_receipt") else None)
    place = where(graph)
    return {"re_lines": re_lines + ([case] if case else []),
            "mail_to": place["mail_to"] or ["[the attorney sets the address: see the packet's problems]"],
            "fees": text, "no_payment": not pays}


def case_schema(schema: dict[str, Any], graph, today: date) -> dict[str, Any]:
    """The filing as this case files it: with the I-212 in the same envelope (its form after the I-601, its exhibits, its hand
    work), and no Form G-1145 when it isn't mailed to a lockbox (src/enotice.py)."""
    out = schema | {"lockbox": bool(where(graph)["lockbox"])}
    if reapply.pair(graph) and not schema.get("pair") and "i212" not in schema.get("forms", []):  # once: packet.build and plan each lay the case over the schema
        extra = schema.get("with_i212") or {}
        out |= {"forms": [*schema.get("forms", []), "i212"], "exhibits": [*schema.get("exhibits", []), *extra.get("exhibits", [])],
                "handwork": [*schema.get("handwork", []), *extra.get("handwork", [])], "pair": True}
    return out
