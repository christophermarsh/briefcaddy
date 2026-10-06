"""The T visa, for a victim of a severe form of trafficking in persons: Form
I-914 (edition 01/20/25), a Supplement A for each family member applied for,
Form I-192 when the client may be inadmissible, and -- optional -- the law
enforcement declaration, Supplement B (src/t_visa_declaration.py asks the
agency for it). Sources, read 10/02/2026:

  INA 101(a)(15)(T) (8 U.S.C. 1101(a)(15)(T), govinfo.gov, 2024 edition);
  8 CFR 214.200-214.216 (the T visa rule, 89 FR 34931, 04/30/2024) and
    212.16, 245.23 (eCFR, current as of 09/30/2026);
  the Form I-914 Instructions (edition 01/20/25) and the Supplement B
    Instructions (edition 01/20/25), uscis.gov/i-914 (updated 06/05/2026);
  USCIS Policy Manual, Volume 3, Part B (current as of 09/23/2026);
  Form G-1055, edition 10/01/26 (data/reference/g-1055.pdf).

  Who (101(a)(15)(T)(i); 214.202): (a) is or has been a victim of a severe
    form of trafficking in persons (214.206); (b) is physically present in
    the U.S., American Samoa, the CNMI or at a port of entry on account of
    it (214.207); (c) has complied with any reasonable request for
    assistance from law enforcement (214.208) -- unless under 18 at an act
    of trafficking (214.202(c)(1), 214.208(e)(2)) or unable to because of
    physical or psychological trauma (214.202(c)(2), 214.208(e)(1)); at a
    minimum the crime was reported to an agency (214.208(b)); (d) would
    suffer extreme hardship involving unusual and severe harm on removal
    (214.209). Not someone who committed an act of trafficking (214.202(e)).
  Initial evidence (214.204(c)): a detailed, signed personal statement in
    the client's own words, and any credible evidence of each requirement.
    Inadmissible: Form I-192 with it (214.204(d), 212.16); USCIS can't waive
    INA 212(a)(3), (a)(10)(C) or (a)(10)(E) (212.16(b)); public charge
    doesn't apply. The law enforcement declaration (Supplement B) is
    OPTIONAL evidence with no special weight (214.204(e)): nothing here
    waits for it.
  Family (101(a)(15)(T)(ii); 214.211; the instructions): a principal under
    21 at filing may apply for a spouse, unmarried children under 21,
    parents and unmarried siblings under 18; 21 or older, a spouse and
    unmarried children under 21; at any age, a parent, an unmarried sibling
    under 18, or an adult or minor child of a derivative who faces a present
    danger of retaliation. One Supplement A each; a family member in the
    U.S. signs it or it is rejected (instructions).
  Fee (G-1055 10/01/26): $0 for the I-914, Supplement A and Supplement B;
    $0 for the I-192 of a T applicant (including derivatives). The G-1055
    lists no separate biometric services fee for them; biometrics are still
    required (214.204(k)): USCIS sends the appointment.
  Where (uscis.gov/i-914, "Where to File", updated 06/05/2026): the Elgin or
    the Phoenix lockbox by the client's state (schemas/law/uscis_lockboxes_i914.json);
    an I-192 filed with the I-914 goes in the same package (uscis.gov/i-192,
    updated 07/15/2026).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from filing_questions import DATE, LINES, TEXT, YES_NO, has_doc, in_exhibit, iso, latest_notice, lockbox, putter, state_of, us, value
import schema_path
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

TITLE = "T visa application (I-914)"
CHART = "uscis_lockboxes_i914"
MAX_MEMBERS = 4
SAFE = ["No: notices go to the home address", "The firm's office", "The mailing address in the case"]
EXCEPTIONS = ["Under 18 at an act of trafficking", "Physical or psychological trauma"]
SPOUSE, CHILD, PARENT, SIBLING = "Spouse", "Child", "Parent", "Unmarried sibling under 18"
# Supplement A, Part 1, item 2: a derivative's child who faces a present danger of retaliation (T-6)
T6 = ["Child of my spouse", "Child of my child (my grandchild)", "Child of my parent (my sibling over 18)",
      "Child of my unmarried sibling under 18 (my niece or nephew)"]
RELATIONSHIPS = [SPOUSE, CHILD, PARENT, SIBLING, *T6]
MARITAL = ["Single", "Married", "Divorced", "Widowed", "Annulled"]

# Part 4 (Processing Information), in the form's order: (key suffix, the question). The same 21 items on Supplement A.
PART4 = [("1a", "1.A: ever committed a crime or offense for which they were not arrested?"),
         ("1b", "1.B: ever arrested, cited or detained by any law enforcement officer (including DHS, INS and military officers)?"),
         ("1c", "1.C: ever charged with committing any crime or offense?"),
         ("1d", "1.D: ever convicted of a crime or offense (even if expunged or pardoned)?"),
         ("1e", "1.E: ever placed in alternative sentencing or a rehabilitative program (diversion, deferred prosecution or adjudication)?"),
         ("1f", "1.F: ever received a suspended sentence, probation or parole?"),
         ("1g", "1.G: ever in jail or prison?"),
         ("1h", "1.H: ever the beneficiary of a pardon, amnesty, rehabilitation or other clemency?"),
         ("1i", "1.I: ever used diplomatic immunity to avoid prosecution in the U.S.?"),
         ("2a", "2.A: engaged in prostitution or procurement of prostitution, or intends to?"),
         ("2b", "2.B: ever engaged in unlawful commercialized vice, such as illegal gambling?"),
         ("2c", "2.C: ever knowingly helped anyone try to enter the U.S. illegally?"),
         ("2d", "2.D: ever trafficked in a controlled substance, or knowingly helped anyone do so?"),
         ("3a", "3.A: ever committed, planned, threatened, attempted or funded hijacking or sabotage of any conveyance?"),
         ("3b", "3.B: ever committed, planned, threatened, attempted or funded seizing or detaining a person to compel a third person?"),
         ("3c", "3.C: ever committed, planned, threatened, attempted or funded an assassination?"),
         ("3d", "3.D: ever committed, planned, threatened, attempted or funded the use of a firearm to endanger people or property?"),
         ("3e", "3.E: ever committed, planned, threatened, attempted or funded the use of a biological, chemical, nuclear, explosive or other dangerous weapon?"),
         ("4a", "4.A: ever a member of, supporter of, or associated with an organization designated as terrorist (INA 219)?"),
         ("4b1", "4.B(1): ever a member of, supporter of, or associated with a group that engaged in hijacking or sabotage?"),
         ("4b2", "4.B(2): ... a group that engaged in seizing or detaining a person to compel a third person?"),
         ("4b3", "4.B(3): ... a group that engaged in assassination?"),
         ("4b4", "4.B(4): ... a group that engaged in the use of a firearm to endanger people or property?"),
         ("4b5", "4.B(5): ... a group that engaged in soliciting money or members, or material support, for a terrorist organization?"),
         ("4b6", "4.B(6): ... a group that engaged in the use of a biological, chemical, nuclear, explosive or other dangerous weapon?"),
         ("5a", "5.A: intends to engage in espionage in the U.S.?"),
         ("5b", "5.B: intends any unlawful activity, or activity to oppose, control or overthrow the U.S. government?"),
         ("5c", "5.C: intends any activity related to espionage or sabotage, or to violate export laws?"),
         ("6", "6: ever a member of the Communist or another totalitarian party (other than involuntarily)?"),
         ("7", "7: took part in Nazi persecution between 03/23/1933 and 05/08/1945?"),
         ("8a", "8.A: ever present or nearby when a person was intentionally killed, tortured, beaten or injured?"),
         ("8b", "8.B: ever present or nearby when a person was displaced from their home by force, compulsion or duress?"),
         ("8c", "8.C: ever present or nearby when a person was forced into any sexual contact or relations?"),
         ("9a", "9.A: removal, exclusion, rescission or deportation proceedings pending now?"),
         ("9b", "9.B: such proceedings ever begun?"),
         ("9c", "9.C: ever removed, excluded or deported from the U.S.?"),
         ("9d", "9.D: ever ordered removed, excluded or deported?"),
         ("9e", "9.E: ever denied a visa or denied admission to the U.S.?"),
         ("9f", "9.F: ever granted voluntary departure and failed to leave in time?"),
         ("10a", "10.A: ever ordered, incited, committed, assisted or taken part in acts of torture or genocide?"),
         ("10b", "10.B: ... in killing any person?"),
         ("10c", "10.C: ... in intentionally and severely injuring any person?"),
         ("10d", "10.D: ... in sexual contact with a person who was forced or threatened?"),
         ("10e", "10.E: ... in limiting or denying anyone's religious beliefs?"),
         ("11a", "11.A: ever served in or helped any military, police, self-defense, vigilante, rebel, guerrilla, militia or insurgent group?"),
         ("11b", "11.B: ever served in any prison, jail, camp or other place where people were detained?"),
         ("12", "12: ever in any group in which anyone used or threatened to use a weapon against a person?"),
         ("13", "13: ever sold, provided or transported weapons to someone who used them against a person?"),
         ("14", "14: ever received military, paramilitary or weapons training?"),
         ("15", "15: under a final order or civil penalty for document fraud (INA 274C)?"),
         ("16", "16: ever sought or obtained a visa, documents or an immigration benefit by fraud or willful misrepresentation?"),
         ("17", "17: ever left the U.S. to avoid the draft?"),
         ("18", "18: ever kept a U.S. citizen child outside the U.S. from a U.S. citizen granted custody?"),
         ("19", "19: plans to practice polygamy in the U.S.?"),
         ("20", "20: entered the U.S. as a stowaway?"),
         ("21a", "21.A: has a communicable disease of public health significance?"),
         ("21b", "21.B: has or had a physical or mental disorder with behavior that threatens anyone's property, safety or welfare?"),
         ("21c", "21.C: is or has been a drug abuser or drug addict?")]
# Items about security and related grounds (INA 212(a)(3)), which USCIS can't waive for a T applicant (8 CFR 212.16(b)): the attorney reviews any Yes.
SECURITY = {"3a", "3b", "3c", "3d", "3e", "4a", "4b1", "4b2", "4b3", "4b4", "4b5", "4b6", "5a", "5b", "5c", "6", "7", "10a"}


def count(graph) -> int:
    """How many family members the client applies for now (Part 3, item 11)."""
    raw = str(value(graph, "tvisa.family_count") or "0")
    return int(raw) if raw.isdigit() else 0


def _member_section(n: int):
    m = f"tvisa.m{n}."
    return (f"Family member {n} (Supplement A)", "the attorney", [
        (m + "relationship", f"Supplement A, Part 1: family member {n} is the client's", {"type": "choice", "options": RELATIONSHIPS}, True),
        (m + "family_name", "Part 3, 1: their family name", TEXT, True),
        (m + "given_name", "Part 3, 1: their given name", TEXT, True),
        (m + "middle_name", "Part 3, 1: their middle name", TEXT, False),
        (m + "dob", "Part 3, 11: their date of birth", DATE, True),
        (m + "sex", "Part 3, 8: their sex", {"type": "choice", "options": ["Male", "Female"]}, True),
        (m + "marital_status", "Part 3, 9: their marital status", {"type": "choice", "options": MARITAL}, True),
        (m + "birth_city", "Part 3, 12: their city or town of birth", TEXT, False),
        (m + "country_of_birth", "Part 3, 12: their country of birth", TEXT, True),
        (m + "citizenship", "Part 3, 13: their country of citizenship", TEXT, True),
        (m + "a_number", "Part 3, 5: their A-Number, if any", TEXT, False),
        (m + "in_us", "Part 3, 19: living in the United States now?", YES_NO, True),
        (m + "lives_with_client", "Part 3, 3: lives at the client's home address?", YES_NO, False),
        (m + "current_status", "Part 3, 18: their current status, as the form's list codes it (for example B2 or EWI)", TEXT, False),
        (m + "ever_in_court", "Part 3, 23: ever in immigration court proceedings?", YES_NO, True),
        (m + "ead", "Part 3, 25: requesting a work permit (only if in the U.S.: a Form I-765 goes with it)", YES_NO, False),
        (m + "retaliation", "Faces a present danger of retaliation for the client's escape or cooperation with law enforcement?", YES_NO, False),
        (m + "p4_all_no", "Part 4, 1-21: every answer is No for this family member?", YES_NO, True),
    ], lambda g, n=n: count(g) >= n)


MORE_QUESTIONS = ("More questions appear as the answers call for them: a section for each family member (Part 3, 11), every Part 4 question "
                  "when not all are No, and the waiver (Form I-192) when one is Yes.")
SECTIONS = [
    ("The claim (Part 3, 1-4)", "the attorney", [
        ("tvisa.victim", "Part 3, 1: the client is or has been a victim of a severe form of trafficking in persons (sex or labor trafficking)?", YES_NO, True),
        ("tvisa.cooperated", "Part 3, 2.A: has cooperated with reasonable requests for assistance from law enforcement?", YES_NO, True),
        ("tvisa.exempt", "Part 3, 2.B: exempt from cooperating because of their age or the trauma suffered?", YES_NO, True),
        ("tvisa.exception", "If exempt: which exception", {"type": "choice", "options": EXCEPTIONS}, False),
        ("tvisa.present", "Part 3, 3: physically present on account of the trafficking (or allowed in for the investigation or a court case)?", YES_NO, True),
        ("tvisa.hardship", "Part 3, 4: fears extreme hardship involving unusual and severe harm on removal?", YES_NO, True),
    ]),
    ("Law enforcement (Part 3, 5-7)", "the attorney", [
        ("tvisa.reported", "Part 3, 5: reported the trafficking crime to law enforcement?", YES_NO, True),
        ("tvisa.report_agency", "Part 3, 5: the agency and office it was reported to (the form has no box for the name: it goes in Part 9)", TEXT, False),
        ("tvisa.report_street", "Part 3, 5: that office's street address", TEXT, False),
        ("tvisa.report_city", "Part 3, 5: city", TEXT, False),
        ("tvisa.report_state", "Part 3, 5: state (two letters)", TEXT, False),
        ("tvisa.report_zip", "Part 3, 5: ZIP code", TEXT, False),
        ("tvisa.report_phone", "Part 3, 5: the office's daytime phone", TEXT, False),
        ("tvisa.report_case_number", "Part 3, 5: the case number, if any", TEXT, False),
        ("tvisa.report_circumstances", "Part 3, 5: if not reported, the circumstances", LINES, False),
        ("tvisa.trafficking_began", "When the trafficking began (on or about): for item 6", DATE, False),
        ("tvisa.under_18", "Part 3, 6: under 18 at the time of at least one act of trafficking?", YES_NO, True),
        ("tvisa.complied", "Part 3, 7: complied with reasonable requests from law enforcement, or unable to because of trauma?", YES_NO, True),
    ]),
    ("Entries and requests (Part 3, 8-11)", "the attorney", [
        ("tvisa.first_entry", "Part 3, 8: this is the client's first entry into the United States?", YES_NO, True),
        ("tvisa.entry_on_account", "Part 3, 9: the most recent entry was on account of the trafficking?", YES_NO, True),
        ("tvisa.ead", "Part 3, 10: a work permit (EAD) when T status is granted?", YES_NO, True),
        ("tvisa.family_count", "Part 3, 11: how many family members the client applies for now (one Supplement A each)",
         {"type": "choice", "options": [str(n) for n in range(MAX_MEMBERS + 1)]}, True),
    ]),
    ("The client's details (Part 2)", "the attorney", [
        ("tvisa.safe_mailing", "Part 2, 4: a safe mailing address for USCIS's notices", {"type": "choice", "options": SAFE}, True),
        ("tvisa.current_status", "Part 2, 20: the client's current status, as the form's list codes it (for example B2 or EWI)", TEXT, True),
        ("tvisa.continued_presence", "Granted Continued Presence by DHS (28 CFR 1100.35)?", YES_NO, False),
    ]),
    # The personal statement's topics (8 CFR 214.204(c)(1); schemas/packets/i914.json "statement"), in the client's own words:
    # the Declaration card assembles the statement from these answers, word for word (src/drafting.py).
    ("The client's account, in their own words (for the personal statement)", "the client", [
        ("tvisa.account_trafficking", "In the client's own words: what happened to them (the trafficking: who, what, when and where)", LINES, False),
        ("tvisa.account_presence", "In the client's own words: how they came to be in the United States, and how that relates to the trafficking", LINES, False),
        ("tvisa.account_cooperation", "In the client's own words: their contact with the police or other authorities about it, or why they could not help", LINES, False),
        ("tvisa.account_hardship", "In the client's own words: the harm they fear if they had to leave the United States", LINES, False),
    ]),
    ("The evidence", "the attorney", [
        ("tvisa.statement_signed", "The client's detailed personal statement, in their own words, is signed?", YES_NO, True),
        ("tvisa.supb_wanted", "Ask a law enforcement agency for Supplement B (optional evidence)?", YES_NO, True),
    ]),
    ("Processing information (Part 4)", "the attorney", [
        ("tvisa.p4_all_no", "Part 4, 1-21: every answer is No (crimes, immigration violations, security, health)?", YES_NO, True),
    ]),
    ("Part 4, each question", "the attorney", [(f"tvisa.p4_{k}", "Part 4, " + label, YES_NO, True) for k, label in PART4],
     lambda g: value(g, "tvisa.p4_all_no") == "No"),
    ("Waiver of inadmissibility (Form I-192)", "the attorney", [
        ("tvisa.i192", "File Form I-192 with the I-914 to waive the grounds that may apply?", YES_NO, True),
        ("i192.grounds", "I-192, item 26: the grounds of inadmissibility that may apply, and how they relate to the trafficking", LINES, True),
        ("i192.prior_request", "I-192, item 27: ever applied before for advance permission to enter as a nonimmigrant?", YES_NO, True),
        ("i192.six_months", "I-192, item 30: ever in the U.S. for six months or more?", YES_NO, True),
        ("i192.prior_applications", "I-192, item 31: ever filed (or had filed for them) an application or petition for immigration benefits?", YES_NO, True),
        ("i192.denied", "I-192, item 35: ever denied an immigration benefit, or had one revoked?", YES_NO, True),
        ("i192.arrested", "I-192, item 36: ever arrested, cited, charged, fined, convicted or imprisoned (not minor traffic)?", YES_NO, True),
    ], lambda g: any(value(g, f"tvisa.p4_{k}") == "Yes" for k, _ in PART4)),
    *[_member_section(n) for n in range(1, MAX_MEMBERS + 1)],
]


def _age(dob: date | None, on: date) -> int | None:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day)) if dob else None


def filed_on(graph, today: date) -> date:
    """The day the I-914 was (or is being) filed: its receipt notice, else today -- ages for family members count from it (8 CFR 214.211(e))."""
    receipt = latest_notice(graph, "I-914", "receipt")
    return iso((receipt or {}).get("date")) or today


def address(graph) -> tuple[list[str] | None, str | None]:
    """The lockbox for the client's state (uscis.gov/i-914, 'Where to File')."""
    return lockbox(CHART, state_of(graph))


def seeking(graph) -> bool:
    """Seeking or granted T nonimmigrant status: an I-914 notice in the folder, or the I-914's own answers (G-1055: no I-131 fee then)."""
    import journey

    return any(n["form"] == "I-914" for n in journey.notices(graph)) or any(value(graph, k) for k in ("tvisa.victim", "tvisa.family_count"))


def part4_yes(graph, prefix: str = "tvisa.") -> list[str]:
    return [k for k, _ in PART4 if value(graph, f"{prefix}p4_{k}") == "Yes"]


def derive(graph, today: date):
    put = putter(graph, "t_visa.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    dob = iso(v("applicant.dob"))
    began = iso(v("tvisa.trafficking_began"))
    if dob and began:  # Part 3, 6: at least one act before the 18th birthday (8 CFR 214.208(e)(2)); it began after it, then none was
        young = _age(dob, began) < 18
        put("tvisa.under_18", "Yes" if young else "No", f"the trafficking began {us(began)}, {'before' if young else 'after'} the 18th birthday")
    if v("tvisa.exempt") == "Yes" and v("tvisa.under_18") == "Yes":
        put("tvisa.exception", EXCEPTIONS[0], "under 18 at an act of trafficking (8 CFR 214.208(e)(2))")
    # Part 2, 20: the current status, from an I-94 that hasn't expired
    until = iso(v("applicant.i94_admit_until_date"))
    if until and until >= today and v("applicant.i94_class_of_admission"):
        put("tvisa.current_status", str(v("applicant.i94_class_of_admission")).upper(), f"the I-94 (admitted until {us(until)})")
    put("tvisa.last_entry_date", v("applicant.last_arrival_date") or v("applicant.i94_arrival_date") or v("applicant.last_arrival_date_self_reported"), "the last entry in the case")
    if v("tvisa.first_entry") == "No":  # Part 3, 8: the most recent entry; earlier ones in the last five years go in Part 9 by hand
        put("tvisa.prior_entry_date", v("tvisa.last_entry_date"), "the last entry in the case")
        put("tvisa.prior_entry_city", v("applicant.last_arrival_city"), "the last entry in the case")
        put("tvisa.prior_entry_state", v("applicant.last_arrival_state"), "the last entry in the case")
        put("tvisa.prior_entry_status", v("applicant.last_arrival_admitted_as") or v("applicant.i94_class_of_admission"), "the last entry in the case")
    # Part 3, 11 and the Supplement A count
    put("tvisa.applying_for_family", "Yes" if count(graph) else "No" if v("tvisa.family_count") else None, "the number of family members applied for")
    # Part 4: every answer No, when the attorney said so
    if v("tvisa.p4_all_no") == "Yes":
        for k, _ in PART4:
            put(f"tvisa.p4_{k}", "No", "the attorney: every Part 4 answer is No")
    # Part 1: filed before? (an earlier I-914 notice in the folder)
    earlier = latest_notice(graph, "I-914", "receipt", "denial", "rejection")
    put("tvisa.part1", "Previously filed" if earlier else "Not previously filed", "the folder's I-914 notices")
    if earlier and str(earlier["receipt"]).startswith("EAC"):
        put("tvisa.prior_receipt", str(earlier["receipt"])[3:], f"the earlier I-914 ({earlier['receipt']})")
    # Part 2, 4: the safe mailing address
    safe = v("tvisa.safe_mailing")
    if safe == SAFE[1]:
        source = {"in_care_of": "firm.business_name", "street": "firm.street", "city": "firm.city", "state": "firm.state", "zip": "firm.zip"}
        for part, key in source.items():
            put(f"tvisa.safe_{part}", v(key), "the firm's office: the client's safe mailing address")
    elif safe == SAFE[2]:
        for part in ("in_care_of", "street", "unit_type", "apt", "city", "state", "zip"):
            put(f"tvisa.safe_{part}", v(f"applicant.mailing_{part}"), "the client's mailing address")
    # Part 9: the agency's name (the form's item 5 has no box for it)
    if v("tvisa.reported") == "Yes" and v("tvisa.report_agency"):
        put("tvisa.p9_page", "3", "Part 3, item 5")
        put("tvisa.p9_part", "3", "Part 3, item 5")
        put("tvisa.p9_item", "5", "Part 3, item 5")
        put("tvisa.p9_text", f"Law enforcement agency and office: {v('tvisa.report_agency')}", "the agency the crime was reported to")
    # Supplement B: by default the agency the crime was reported to (src/t_visa_declaration.py)
    for part in ("street", "city", "state", "zip", "phone", "case_number"):
        put(f"tvisa.lea_{part}", v(f"tvisa.report_{part}"), "the agency the crime was reported to (Part 3, 5)")
    put("tvisa.lea_name", v("tvisa.report_agency"), "the agency the crime was reported to (Part 3, 5)")
    # Form I-192 (filed with the I-914): items 1-10, then 26 on (the I-192's own note for a T applicant)
    put("i192.application_type", "T or U nonimmigrant status", "filed with the I-914")
    safe_used = bool(v("tvisa.safe_street"))  # item 9: the safe address, if any, else home
    for part in ("in_care_of", "street", "unit_type", "apt", "city", "state", "zip"):
        source = f"tvisa.safe_{part}" if safe_used else f"applicant.physical_{part}" if part != "in_care_of" else None
        put(f"i192.mailing_{part}", v(source) if source else None, "the safe mailing address" if safe_used else "the client's home address")
    put("i192.physical_country", "USA" if v("applicant.physical_street") else None, "a U.S. address")
    # Form G-28: the forms the attorney appears on (the I-914 instructions: list the I-192 too). "I-914A" is USCIS's own short
    # name for Supplement A (uscis.gov/i-192); the G-28's box holds 30 characters.
    forms = ["I-914"] + (["I-914A"] if count(graph) else []) + (["I-192"] if v("tvisa.i192") == "Yes" else [])
    put("tvisa.g28_forms", ", ".join(forms), "the forms in this filing")
    # Supplement A: the I-914's status, and each family member
    approved, pending_ = latest_notice(graph, "I-914", "approval"), latest_notice(graph, "I-914", "receipt")
    put("tvisa.i914_status", "Approved" if approved else "Pending" if pending_ else "Filing together", "the folder's I-914 notices")
    children = 0
    for n in range(1, count(graph) + 1):
        m = f"tvisa.m{n}."
        if v(m + "lives_with_client") == "Yes":
            for part in ("street", "unit_type", "apt", "city", "state", "zip"):
                put(m + part, v(f"applicant.physical_{part}"), "lives at the client's home address")
            for part in ("in_care_of", "street", "unit_type", "apt", "city", "state", "zip"):
                put(m + "safe_" + part, v(f"tvisa.safe_{part}"), "the client's safe mailing address")
        if v(m + "relationship") == CHILD and children < 3:  # Part 5: the client's children (every child, even one not applied for)
            children += 1
            for part in ("family_name", "given_name", "middle_name", "dob", "country_of_birth"):
                put(f"tvisa.child{children}_{part}", v(m + part), f"family member {n}, the client's child")
        if v(m + "p4_all_no") == "Yes":
            for k, _ in PART4:
                put(f"{m}p4_{k}", "No", "the attorney: every Part 4 answer is No for this family member")
    return graph


def member_facts(graph, n: int) -> dict[str, Any]:
    """Family member n's answers as Supplement A's own keys (tvisa_a.*): one map, one form per member (render)."""
    prefix = f"tvisa.m{n}."
    out = {}
    for key, fact in graph.all_facts().items():
        if key.startswith(prefix) and fact.status == "resolved" and fact.value not in (None, ""):
            out["tvisa_a." + key[len(prefix):]] = fact.value
    sex = {"Male": "M", "Female": "F"}.get(out.get("tvisa_a.sex"))
    if sex:
        out["tvisa_a.sex"] = sex
    return out


def members(graph) -> list[dict[str, Any]]:
    return [{"n": n, "name": " ".join(x for x in (value(graph, f"tvisa.m{n}.given_name"), value(graph, f"tvisa.m{n}.family_name")) if x)
             or f"family member {n}", "relationship": value(graph, f"tvisa.m{n}.relationship")} for n in range(1, count(graph) + 1)]


def case_forms(forms: list[str], graph) -> list[str]:
    """The forms in this case's package, in order: the G-28, the I-914, a Supplement A per family member, the I-192 when the attorney files one."""
    base = [f for f in forms if not f.startswith("i914a_") and f != "i192"]
    at = base.index("i914") + 1 if "i914" in base else len(base)
    extra = [f"i914a_{n}" for n in range(1, count(graph) + 1)] + (["i192"] if value(graph, "tvisa.i192") == "Yes" else [])
    return base[:at] + extra + base[at:]


def render(client_dir: Path, graph, today: date) -> None:
    """i914a_<n>_filled.pdf: one Supplement A per family member, from one map (companion_forms.json "i914a")."""
    import json

    from factgraph import FactGraph
    from fill.companion import fill_companions, load_profile

    profile = load_profile()
    base = profile["forms"]["i914a"]
    done = json.loads((client_dir / "companions.json").read_text(encoding="utf-8")) if (client_dir / "companions.json").exists() else {}
    for m in members(graph):
        g = FactGraph(f"i914a_{m['n']}")
        for key, fact in graph.all_facts().items():
            if fact.status == "resolved" and fact.value not in (None, "") and not key.startswith("tvisa.m"):
                g.add_source(key, "case", "derived", fact.value, fact.value, 1.0)
        for key, val in member_facts(graph, m["n"]).items():
            g.add_source(key, f"family member {m['n']}", "derived", val, val, 1.0)
        fid = f"i914a_{m['n']}"
        result = fill_companions(g, client_dir, {**profile, "forms": {fid: {**base, "output": f"{fid}_filled.pdf"}}})[fid]
        # the blanks as the panel asks them: tvisa_a.x -> this member's tvisa.m<n>.x
        result["left_blank"] = [k.replace("tvisa_a.", f"tvisa.m{m['n']}.") for k in result["left_blank"]]
        done[fid] = result
    (client_dir / "companions.json").write_text(json.dumps(done, indent=1), encoding="utf-8")


def fee(graph, today: date) -> tuple[int | None, str]:
    import fees

    paper = fees.load(today).get("paper") or {}
    return paper.get("i914"), "no fee for Form I-914, its Supplements A and B, or a T applicant's Form I-192 (Form G-1055)"


def notes(graph, today: date) -> list[dict[str, str]]:
    import fees

    v = lambda k: value(graph, k)  # noqa: E731
    lines, box = address(graph)
    edition = fees.load(today).get("edition") or "current"
    out = [{"level": "info", "title": "Who qualifies",
            "text": "A victim of a severe form of trafficking in persons, physically present in the U.S. on account of it, who complied with any reasonable "
                    "request from law enforcement (unless under 18 at an act of trafficking, or unable to because of trauma), and who would suffer extreme "
                    "hardship involving unusual and severe harm on removal (INA 101(a)(15)(T)(i); 8 CFR 214.202)."},
           {"level": "info", "title": "Fee and where",
            "text": f"No fee for the I-914, Supplement A, Supplement B or a T applicant's I-192 (Form G-1055, edition {edition}); no separate biometrics "
                    "fee is listed. Mailed to " + (" / ".join(lines) + f" (the {box} lockbox for the client's state" if lines else
                                                   "[the client's state isn't on USCIS's I-914 chart") + ", uscis.gov/i-914)."},
           {"level": "info", "title": "Supplement B is optional",
            "text": "A law enforcement declaration helps but is not required, and USCIS gives it no special weight (8 CFR 214.204(e)). Without it, other "
                    "credible evidence shows the victimization and the cooperation, or the age or trauma exception. Ask for one with "
                    "More filings, T visa: law enforcement declaration request."}]
    if v("tvisa.supb_wanted") == "Yes":
        back = v("tvisa.supb_returned")
        asked = iso(v("tvisa.supb_requested_on"))
        out.append({"level": "info", "title": "Supplement B",
                    "text": ("Returned signed: put the original in the law enforcement exhibit." if back == "Returned, signed" else
                             "The agency declined: file with the other evidence of cooperation (8 CFR 214.208(d))." if back == "The agency declined" else
                             f"Requested {us(asked)}, not back yet: the I-914 can be filed without it, and it can be sent to USCIS later." if asked else
                             "Not requested yet: prepare the request from More filings.")})
    out.append({"level": "info", "title": "Work permit while it is pending",
                "text": "Once USCIS finds the application bona fide it may grant deferred action and a work permit; that needs Form I-765 under (c)(40) "
                        "($0, Form G-1055), prepared by hand for now (8 CFR 214.205; the I-914 instructions)."})
    out.append({"level": "info", "title": "Travel and confidentiality",
                "text": "Leaving the U.S. while the I-914 is pending can cost the physical presence requirement (the I-914 instructions; 8 CFR 214.207(b)). "
                        "The client's information is protected by 8 U.S.C. 1367 (8 CFR 214.216)."})
    by_hand = [m["name"] for m in members(graph) if v(f"tvisa.m{m['n']}.p4_all_no") == "No"]
    if by_hand:
        out.append({"level": "warn", "title": "Supplement A, Part 4",
                    "text": f"Not every answer is No for {', '.join(by_hand)}: answer Part 4 on their Supplement A by hand and, if they may be "
                            "inadmissible, file a Form I-192 for them with it (8 CFR 214.211(c)(4))."})
    if part4_yes(graph) and any(k in SECURITY for k in part4_yes(graph)):
        out.append({"level": "warn", "title": "Security grounds",
                    "text": "A Yes in Part 4, items 3-7 or 10.A may be a ground under INA 212(a)(3), which USCIS can't waive for a T applicant (8 CFR "
                            "212.16(b)): the attorney reviews before filing."})
    return out


@producer(ATTORNEY)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    if v("tvisa.victim") == "No":
        out.append("Not a victim of a severe form of trafficking in persons: not eligible (INA 101(a)(15)(T)(i)(I); 8 CFR 214.202(a), 214.206).")
    if v("tvisa.present") == "No":
        out.append("Not physically present on account of the trafficking: not eligible (INA 101(a)(15)(T)(i)(II); 8 CFR 214.202(b), 214.207).")
    if v("tvisa.hardship") == "No":
        out.append("No extreme hardship involving unusual and severe harm on removal: not eligible (INA 101(a)(15)(T)(i)(IV); 8 CFR 214.202(d), 214.209).")
    exempt = v("tvisa.exempt") == "Yes"
    if v("tvisa.cooperated") == "No" and v("tvisa.complied") == "No" and not exempt:
        out.append("Hasn't complied with reasonable requests from law enforcement and no exception applies: not eligible "
                   "(INA 101(a)(15)(T)(i)(III); 8 CFR 214.202(c), 214.208).")
    if v("tvisa.reported") == "No" and not exempt:
        out.append("The crime was never reported to law enforcement: at a minimum the client must contact an agency, unless the age or trauma exception "
                   "applies (8 CFR 214.208(b)).")
    if exempt and not v("tvisa.exception"):
        out.append(held(OFFICE, "Exempt from cooperating: choose the exception (under 18 at an act of trafficking, or trauma): each needs its own evidence (8 CFR 214.208(e))."))
    if exempt and v("tvisa.exception") == EXCEPTIONS[0] and v("tvisa.under_18") == "No":
        out.append("The age exception needs the client to have been under 18 at an act of trafficking (Part 3, 6 says No) (8 CFR 214.208(e)(2)).")
    if exempt and v("tvisa.exception") == EXCEPTIONS[1] and not in_exhibit(client_dir, "i914", "trauma") and not has_doc(client_dir, "medical_record"):
        out.append(held(CLIENT, "The trauma exception needs credible evidence of the trauma (a professional's statement, medical or psychological records): put it "
                   "in the trauma exhibit (8 CFR 214.208(e)(1))."))
    if v("tvisa.statement_signed") == "No":
        out.append(held(CLIENT, "The personal statement isn't signed: a detailed, signed statement in the client's own words is required initial evidence "
                   "(8 CFR 214.204(c)(1)), and a bona fide determination needs it (214.205(a)(2)(ii))."))
    if v("tvisa.reported") == "Yes" and not (v("tvisa.report_street") and v("tvisa.report_city")):
        out.append(held(CLIENT, "Part 3, 5: the crime was reported. Enter the agency's office and address (and the case number, if any)."))
    if v("tvisa.reported") == "No" and not v("tvisa.report_circumstances"):
        out.append(held(CLIENT, "Part 3, 5: not reported. Explain the circumstances."))
    if v("tvisa.continued_presence") == "Yes" and not in_exhibit(client_dir, "i914", "law_enforcement"):
        out.append(held(CLIENT, "Continued Presence was granted: include its documentation (8 CFR 214.204(i)) in the law enforcement exhibit."))
    if v("tvisa.supb_returned") == "Returned, signed" and not in_exhibit(client_dir, "i914", "law_enforcement"):
        out.append(held(OFFICE, "The signed Supplement B came back: put the original in the law enforcement exhibit."))
    if part4_yes(graph) and v("tvisa.i192") == "No":
        out.append("Part 4 has a Yes and no Form I-192: the attorney confirms the client isn't inadmissible on that ground (8 CFR 214.204(d), 212.16).")
    if not address(graph)[0]:
        out.append(held(OFFICE, f"The client's state ({state_of(graph) or 'unknown'}) isn't on USCIS's I-914 filing chart: the attorney sets the address (uscis.gov/i-914)."))
    if any(n["form"] == "I-914" and n["kind"] == "approval" for n in __import__("journey").notices(graph)):
        out.append("This client already has an approved I-914: a family member is added with a Supplement A on its own, while in T-1 status "
                   "(8 CFR 214.211(b)(1)): ask the attorney.")
    out += member_problems(graph, today)
    return out


@producer(ATTORNEY)
def member_problems(graph, today: date) -> list[str]:
    """Each family member against 8 CFR 214.211 (who is an eligible family member, at what age)."""
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    filing = filed_on(graph, today)
    principal = _age(iso(v("applicant.dob")), filing)
    for m in members(graph):
        p = f"tvisa.m{m['n']}."
        rel, name = m["relationship"], m["name"]
        age = _age(iso(v(p + "dob")), filing)
        danger = v(p + "retaliation") == "Yes"
        unmarried = v(p + "marital_status") in (None, "Single", "Divorced", "Widowed", "Annulled")
        if rel == CHILD and age is not None and age >= 21:
            out.append(f"{name}: a child must be under 21 when the I-914 is filed ({age} on {us(filing)}) (8 CFR 214.201, 214.211(e)(3)).")
        if rel in (CHILD, SIBLING) and not unmarried:
            out.append(f"{name}: a child or sibling must be unmarried (8 CFR 214.211(e)(4)).")
        if rel == SIBLING and age is not None and age >= 18:
            out.append(f"{name}: a sibling must be under 18 when the I-914 is filed ({age} on {us(filing)}) (8 CFR 214.211(e)(2)(iii)).")
        if rel in (PARENT, SIBLING) and principal is not None and principal >= 21 and not danger:
            out.append(f"{name}: the client was 21 or older when the I-914 was filed, so a {rel.lower()} qualifies only if they face a present danger "
                       "of retaliation (8 CFR 214.211(a)(1), (a)(3)).")
        if rel in T6 and not danger:
            out.append(f"{name}: a derivative's child qualifies only with a present danger of retaliation (8 CFR 214.211(a)(3)).")
        if v(p + "in_us") == "No" and v(p + "ead") == "Yes":
            out.append(held(OFFICE, f"{name}: lives outside the U.S.: no work permit until admitted, and no I-765 for them (the I-914 instructions)."))
    return out


def letter(graph, today: date) -> dict[str, Any]:
    import fees

    lines, _box = address(graph)
    edition = fees.load(today).get("edition") or "current"
    family = members(graph)
    i192 = value(graph, "tvisa.i192") == "Yes"
    named = (["its Supplement A"] if family else []) + (["Form I-192"] if i192 else [])
    re_lines = ["Application: I-914 Application for T Nonimmigrant Status"]
    if family:
        re_lines.append(f"With {len(family)} Form I-914, Supplement A, Application for Derivative T Nonimmigrant Status")
    if i192:
        re_lines.append("With I-192 Application for Advance Permission to Enter as a Nonimmigrant")
    forms = {f"i914a_{m['n']}": f"Applicant’s I-914, Supplement A - Application for Derivative T Nonimmigrant Status ({m['name'].upper()})" for m in family}
    import json


    config = json.loads((schema_path.path("cover_letter", "i914")).read_text(encoding="utf-8"))
    return {"re_lines": re_lines, "mail_to": lines or ["[USCIS address: see the packet's problems]"], "no_payment": True,
            "forms": {**config["forms"], **forms},
            "fees": "No filing fee is required for Form I-914" + (", " + " or ".join(named) if named else "")
                    + f" filed by an applicant for T nonimmigrant status (Form G-1055, edition {edition})."}


def document_notes(client_dir: Path, graph) -> list[dict[str, str]]:
    """Each Supplement A family member's documents in the folder, by the document record's person (src/documents.py)."""
    import documents

    return documents.relatives_note(client_dir, [m["relationship"] for m in members(graph)])
