"""Family-based adjustment of status, filed together: the relative's Form
I-130 petition (+ I-130A for a spouse, + the I-864 affidavit of support) with
the client's I-485 (+ I-765). schemas/packets/family.json for the packet;
schemas/packets/companion_forms.json for the forms.

Two people. The client is the beneficiary (applicant.*, the same facts as
the I-485). The petitioner -- the U.S. citizen or permanent resident
relative -- is petitioner.*: from their own documents (U.S. passport,
naturalization certificate, green card, U.S. birth certificate), from the
spouse the pipeline already reads off the marriage certificate and the
questionnaire (derive), and from the questions below, answered in the
review app and saved as review decisions like every other fix.

Spouse of a U.S. citizen first; the relationship and the petitioner's
status choose the I-485's category (Part 2), so a parent, child or sibling
petition -- or a permanent-resident petitioner -- uses the same path.

Three forms join the packet when the case needs them (case_schema):

  - Form I-485 Supplement A (edition 09/18/26; uscis.gov/i-485supa, updated
    09/18/2026): a client barred from adjusting under INA 245(a) -- an entry
    without inspection, unauthorized work, no lawful status... (Supplement A
    Instructions, "Bars to Adjustment") -- who is a grandfathered alien: the
    beneficiary of a petition or labor certification filed on or before
    04/30/2001 and approvable when filed, present in the U.S. on 12/21/2000
    when filed after 01/14/1998, or the spouse or child of one (8 CFR
    245.10(a), eCFR as of 09/30/2026). The attorney marks it. Its sum (Form
    G-1055 10/01/26): $1,000 on paper; $0 for an unmarried child under 17;
    $0 for the spouse or unmarried child under 21 of a legalized alien with a
    copy of the Form I-817 receipt or approval notice (also 8 CFR 245.10(c)).
    Its own payment (src/payment.py).
  - Form I-864A (edition 08/24/26; uscis.gov/i-864a): one for each household
    member whose income the sponsor counts, signed by the sponsor and the
    member; the intending immigrant signs one only with accompanying
    dependents (I-864A Instructions, 08/24/26).
  - Form I-864EZ (edition 08/24/26; uscis.gov/i-864ez) in place of the I-864
    when the petitioner sponsors only the relative on this I-130 with income
    only from a salary or pension on Forms W-2 (I-864EZ Instructions,
    08/24/26, "Who May Use Form I-864EZ"). The attorney confirms; the panel
    says what in the case speaks for or against it.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import clock
import schema_path
from holders import ATTORNEY, CLIENT, OFFICE, held, of_first, producer

YES_NO = {"type": "choice", "options": ["Yes", "No"]}
TEXT = {"type": "text"}
DATE = {"type": "date"}

# Supplement A, Part 2, item 1: the basis of 245(i) eligibility, in the printed order (boxes /A to /E; 8 CFR 245.10(a)(1))
SUPA_BASES = ["Principal beneficiary, filed on or before 01/14/1998",
              "Principal beneficiary, filed 01/15/1998 to 04/30/2001, in the U.S. on 12/21/2000",
              "Derivative beneficiary, filed on or before 01/14/1998",
              "Derivative beneficiary, filed 01/15/1998 to 04/30/2001, the principal in the U.S. on 12/21/2000",
              "Spouse or unmarried child under 21 of one of the above, accompanying or following to join"]
# Supplement A, Part 3, items 1.a-1.j: the bars to adjusting under INA 245(a) (boxes /A to /J)
SUPA_BARS = [("a", "Last entered without being admitted or paroled after inspection"),
             ("b", "Last entered as a nonimmigrant crewman"),
             ("c", "Employed in the U.S. without authorization, now or ever"),
             ("d", "Not in lawful immigration status on the day the I-485 is filed"),
             ("e", "Ever failed to keep a lawful status since entry (not through no fault of their own or for technical reasons)"),
             ("f", "Last admitted in transit without a visa"),
             ("g", "Last admitted without a visa under the Guam and Northern Mariana Islands program (not a Canadian citizen)"),
             ("h", "Last admitted without a visa under the Visa Waiver Program"),
             ("i", "Seeking employment-based adjustment without a lawful nonimmigrant status on the filing day"),
             ("j", "Ever violated the terms of a nonimmigrant status")]
# the sum's exemptions (Form G-1055 10/01/26, I-485 Supplement A; 8 CFR 245.10(c))
SUPA_PAYS = "None: the sum is paid"
SUPA_UNDER_17 = "Unmarried and under 17"
SUPA_FAMILY_UNITY = "Spouse, or unmarried child under 21, of a legalized alien, with the Form I-817 receipt or approval notice"
# who a household member is (Form I-864A, Part 2): the intending immigrant (items 1-2), or the sponsor's relative or dependent (item 3)
HM_IMMIGRANT_SPOUSE = "The client (the intending immigrant), the sponsor's spouse"
HM_IMMIGRANT_HOUSEHOLD = "The client (the intending immigrant), living with the sponsor"
HM_SPOUSE, HM_CHILD, HM_PARENT = "The sponsor's spouse", "The sponsor's son or daughter (18 or older)", "The sponsor's parent"
HM_SIBLING, HM_DEPENDENT = "The sponsor's brother or sister", "Another dependent on the sponsor's tax return"
HM_IMMIGRANT = (HM_IMMIGRANT_SPOUSE, HM_IMMIGRANT_HOUSEHOLD)
HM_ROW = {HM_IMMIGRANT_SPOUSE: "SPOUSE (INTENDING IMMIGRANT)", HM_IMMIGRANT_HOUSEHOLD: "INTENDING IMMIGRANT", HM_SPOUSE: "SPOUSE",
          HM_CHILD: "SON/DAUGHTER", HM_PARENT: "PARENT", HM_SIBLING: "SIBLING", HM_DEPENDENT: "DEPENDENT"}  # the I-864's Part 6 rows
MAX_MEMBERS = 4  # the I-864 lists four household members' incomes (Part 6, items 8-11)


def _member(n: int) -> list[tuple[str, str, dict[str, Any], bool]]:
    """One household member's questions (Form I-864A, Parts 1-4)."""
    who = f"Household member {n}"
    return [
        (f"i864a{n}.relationship", f"{who}: who they are (I-864A Part 2)", {"type": "choice", "options": [HM_SPOUSE, HM_CHILD, HM_PARENT, HM_SIBLING, HM_DEPENDENT, *HM_IMMIGRANT]}, True),
        (f"i864a{n}.dependent_relationship", f"{who}: if another dependent, their relationship to the sponsor", TEXT, False),
        (f"i864a{n}.with_dependents", f"{who}: if the client, do they have accompanying dependents (then they sign an I-864A)?", YES_NO, False),
        (f"i864a{n}.family_name", f"{who}: family name", TEXT, True), (f"i864a{n}.given_name", f"{who}: given name", TEXT, True),
        (f"i864a{n}.middle_name", f"{who}: middle name", TEXT, False),
        (f"i864a{n}.dob", f"{who}: date of birth", DATE, True), (f"i864a{n}.country_of_birth", f"{who}: country of birth", TEXT, True),
        (f"i864a{n}.ssn", f"{who}: Social Security number (if any)", {"type": "text", "pattern": r"^\d{3}-?\d{2}-?\d{4}$", "placeholder": "123-45-6789"}, False),
        (f"i864a{n}.a_number", f"{who}: A-Number (if any)", TEXT, False),
        (f"i864a{n}.street", f"{who}: home address, street", TEXT, True), (f"i864a{n}.apt", f"{who}: apartment", TEXT, False),
        (f"i864a{n}.city", f"{who}: city", TEXT, True), (f"i864a{n}.state", f"{who}: state", TEXT, True), (f"i864a{n}.zip", f"{who}: ZIP code", TEXT, True),
        (f"i864a{n}.employment", f"{who}: currently (I-864A Part 3)", {"type": "choice", "options": ["Employed", "Self-employed", "Retired", "Unemployed"]}, True),
        (f"i864a{n}.occupation", f"{who}: occupation", TEXT, False), (f"i864a{n}.employer", f"{who}: employer", TEXT, False),
        (f"i864a{n}.income", f"{who}: current individual annual income (US$)", TEXT, True),
        (f"i864a{n}.tax_year1", f"{who}: most recent tax year (I-864A Part 4)", TEXT, True),
        (f"i864a{n}.tax_income1", f"{who}: total income that year (US$)", TEXT, True),
    ]


SECTIONS: list[tuple[str, list[tuple[str, str, dict[str, Any], bool]]]] = [
    ("The relationship", [
        ("family.relationship", "I-130 Part 1: The beneficiary (the client) is the petitioner's", {"type": "choice", "options": ["Spouse", "Parent", "Child", "Sibling"]}, True),
        ("petitioner.status", "The petitioner is a U.S. citizen (USC) or a lawful permanent resident (LPR)", {"type": "choice", "options": ["USC", "LPR"]}, True),
        ("applicant.filing_category", "I-485 Part 2: the client's category (from the two answers above)",
         {"type": "choice", "options": ["Spouse of U.S. citizen", "Child under 21 of U.S. citizen", "Parent of U.S. citizen", "Unmarried son/daughter 21+ of U.S. citizen",
                                        "Married son/daughter of U.S. citizen", "Sibling of U.S. citizen", "Spouse of LPR", "Child under 21 of LPR",
                                        "Unmarried son/daughter 21+ of LPR"]}, True),
        ("family.previous_petition", "I-130 Part 5, 1: Has the petitioner EVER filed a petition for this or any other beneficiary?", YES_NO, True),
        ("family.lived_together_date_from", "I-130 Part 4, 60: Date the spouses began living together at their last shared address", DATE, False),
        ("family.adjust_city", "I-130 Part 4, 61: City of the USCIS office where the client will adjust status", TEXT, True),
        ("family.adjust_state", "I-130 Part 4, 61: State of that office", TEXT, True),
    ]),
    ("The petitioner", [
        ("petitioner.family_name", "Petitioner's family name", TEXT, True),
        ("petitioner.given_name", "Petitioner's given name", TEXT, True),
        ("petitioner.dob", "Petitioner's date of birth", DATE, True),
        ("petitioner.sex", "Petitioner's sex", {"type": "choice", "options": ["M", "F"]}, True),
        ("petitioner.birth_city", "Petitioner's city of birth", TEXT, True),
        ("petitioner.country_of_birth", "Petitioner's country of birth", TEXT, True),
        ("petitioner.ssn", "Petitioner's Social Security number (required on the I-864)", {"type": "text", "pattern": r"^\d{3}-?\d{2}-?\d{4}$", "placeholder": "123-45-6789"}, True),
        ("petitioner.citizenship_how", "If a U.S. citizen: citizenship through", {"type": "choice", "options": ["birth", "naturalization", "parents"]}, False),
        ("petitioner.certificate_number", "Naturalization / citizenship certificate number", TEXT, False),
        ("petitioner.certificate_place", "Certificate place of issuance", TEXT, False),
        ("petitioner.certificate_date", "Certificate date of issuance", DATE, False),
        ("petitioner.times_married", "How many times has the petitioner been married?", TEXT, True),
        ("petitioner.prior_spouse1_family_name", "Petitioner's prior spouse: family name", TEXT, False),
        ("petitioner.prior_spouse1_given_name", "Petitioner's prior spouse: given name", TEXT, False),
        ("petitioner.prior_spouse1_date_ended", "Petitioner's prior marriage ended on", DATE, False),
        ("petitioner.daytime_phone", "Petitioner's daytime phone", TEXT, True),
        ("petitioner.email", "Petitioner's email", TEXT, False),
    ]),
    ("The petitioner's parents", [
        ("petitioner.parent1_family_name", "Parent 1 family name", TEXT, True), ("petitioner.parent1_given_name", "Parent 1 given name", TEXT, True),
        ("petitioner.parent1_sex", "Parent 1 sex", {"type": "choice", "options": ["M", "F"]}, True),
        ("petitioner.parent1_country_of_birth", "Parent 1 country of birth", TEXT, True),
        ("petitioner.parent1_city_of_residence", "Parent 1 city of residence (or DECEASED)", TEXT, True),
        ("petitioner.parent1_country_of_residence", "Parent 1 country of residence", TEXT, False),
        ("petitioner.parent2_family_name", "Parent 2 family name", TEXT, True), ("petitioner.parent2_given_name", "Parent 2 given name", TEXT, True),
        ("petitioner.parent2_sex", "Parent 2 sex", {"type": "choice", "options": ["M", "F"]}, True),
        ("petitioner.parent2_country_of_birth", "Parent 2 country of birth", TEXT, True),
        ("petitioner.parent2_city_of_residence", "Parent 2 city of residence (or DECEASED)", TEXT, True),
        ("petitioner.parent2_country_of_residence", "Parent 2 country of residence", TEXT, False),
    ]),
    ("The petitioner's address and work (last 5 years)", [
        ("petitioner.address1_date_from", "Living at the current address since", DATE, True),
        ("petitioner.address2_street", "Previous address: street (if moved in the last 5 years)", TEXT, False),
        ("petitioner.address2_city", "Previous address: city", TEXT, False), ("petitioner.address2_state", "Previous address: state", TEXT, False),
        ("petitioner.address2_date_from", "Previous address: from", DATE, False), ("petitioner.address2_date_to", "Previous address: to", DATE, False),
        ("petitioner.employer1_name", "Current employer (or UNEMPLOYED / SELF-EMPLOYED)", TEXT, True),
        ("petitioner.employer1_occupation", "Occupation", TEXT, True), ("petitioner.employer1_date_from", "Working there since", DATE, True),
        ("petitioner.employer1_city", "Employer's city", TEXT, False), ("petitioner.employer1_state", "Employer's state", TEXT, False),
    ]),
    ("The affidavit of support (I-864)", [
        ("i864.household_size", "Part 5 · Household size (the petitioner, the client, dependents and anyone else counted)", TEXT, True),
        ("i864.current_income", "Part 6, 7 · Petitioner's current annual income (US$)", TEXT, True),
        ("i864.tax_year1", "Part 6, 16 · Most recent tax year", TEXT, True), ("i864.tax_income1", "Total income that year (US$)", TEXT, True),
        ("i864.tax_year2", "2nd most recent tax year", TEXT, False), ("i864.tax_income2", "Total income that year (US$)", TEXT, False),
        ("i864.tax_year3", "3rd most recent tax year", TEXT, False), ("i864.tax_income3", "Total income that year (US$)", TEXT, False),
        ("petitioner.military", "Part 2, 12 · Is the petitioner on active duty in the U.S. armed forces?", YES_NO, True),
        ("i864a.count", "Household members whose income the sponsor counts (one Form I-864A each)", {"type": "choice", "options": ["None", *map(str, range(1, MAX_MEMBERS + 1))]}, False),
        ("i864.use_ez", "Use Form I-864EZ instead of the I-864? The attorney confirms it fits (see the note above)", YES_NO, False),
    ]),
    *[(f"Household member {n} (Form I-864A)", _member(n)) for n in range(1, MAX_MEMBERS + 1)],
    ("Adjustment under INA 245(i) (Supplement A)", [
        ("family.adjust_245i", "Is the client adjusting under INA 245(i) (Supplement A)? Only for a client barred from 245(a), e.g. an entry without "
                               "inspection, who is grandfathered (8 CFR 245.10)", YES_NO, False),
    ]),
    ("Supplement A: the qualifying petition", [
        ("supa.basis", "Supplement A Part 2, 1: the client is", {"type": "choice", "options": SUPA_BASES}, True),
        ("supa.filed_on", "The qualifying petition or labor certification was filed (or postmarked) on", DATE, True),
        ("supa.present_2000_12_21", "The principal beneficiary was in the U.S. on 12/21/2000 (needed when filed after 01/14/1998)", YES_NO, False),
        ("supa.receipt_number", "Part 2, 2: the petition's receipt number (none for a labor certification)", TEXT, False),
        ("supa.principal_family_name", "Part 2, 3: the principal beneficiary of that petition: family name", TEXT, True),
        ("supa.principal_given_name", "Part 2, 3: the principal beneficiary: given name", TEXT, True),
        ("supa.principal_a_number", "Part 2, 4: the principal applicant's A-Number (if any)", TEXT, False),
        ("supa.category", "Part 2, 5: the I-485 category, as written on the supplement (34 characters at most)", TEXT, True),
        ("supa.fee_exemption", "Supplement A sum: an exemption (Form G-1055)?", {"type": "choice", "options": [SUPA_PAYS, SUPA_UNDER_17, SUPA_FAMILY_UNITY]}, True),
    ]),
    ("Supplement A: the bars to adjusting under 245(a) (Part 3)", [(f"supa.bar_{x}", f"Part 3, 1.{x}: {text}", YES_NO, False) for x, text in SUPA_BARS]),
]
QUESTIONS = [(key, label, section, spec, required) for section, items in SECTIONS for key, label, spec, required in items]


def household_members(graph) -> list[int]:
    """The household members whose income the sponsor counts (1..n, from the count the attorney set)."""
    count = str(_value(graph, "i864a.count") or "")
    return list(range(1, min(int(count), MAX_MEMBERS) + 1)) if count.isdigit() else []


def contracts(graph) -> list[int]:
    """The members who sign a Form I-864A: every one counted, except the client without accompanying dependents -- "you need
    to complete this contract only if you have accompanying dependents" (I-864A Instructions, 08/24/26)."""
    return [n for n in household_members(graph)
            if not (_value(graph, f"i864a{n}.relationship") in HM_IMMIGRANT and _value(graph, f"i864a{n}.with_dependents") != "Yes")]


# sections shown only when an answer above says they apply
SHOWN_WHEN = {**{f"Household member {n} (Form I-864A)": (lambda n: lambda g: n in household_members(g))(n) for n in range(1, MAX_MEMBERS + 1)},
              "Supplement A: the qualifying petition": lambda g: _value(g, "family.adjust_245i") == "Yes",
              "Supplement A: the bars to adjusting under 245(a) (Part 3)": lambda g: _value(g, "family.adjust_245i") == "Yes"}
CATEGORY = {("Spouse", "USC"): "Spouse of U.S. citizen", ("Parent", "USC"): "Parent of U.S. citizen", ("Sibling", "USC"): "Sibling of U.S. citizen",
            ("Spouse", "LPR"): "Spouse of LPR"}
SETTINGS = schema_path.path("law", "family_settings")


def _value(graph, key: str) -> Any:
    fact = graph.get(key)
    return fact.value if fact is not None and fact.status == "resolved" and fact.value not in (None, "") else None


def derive(graph):
    """Facts the family forms need that the case already holds under other
    names -- recorded as derived sources, so each still traces to its document."""
    def put(key: str, value: Any, why: str) -> None:
        if value not in (None, "") and _value(graph, key) is None:
            graph.add_source(key, "family.derive", "derived", why, value, 0.85, tier=3)

    married = bool(_value(graph, "applicant.marriage_date") or _value(graph, "applicant.spouse_family_name"))
    if married:
        put("family.relationship", "Spouse", "a marriage certificate / spouse in the case")
    # in a spouse petition the petitioner IS the client's spouse
    if _value(graph, "family.relationship") == "Spouse":
        for mine, theirs in (("family_name", "spouse_family_name"), ("given_name", "spouse_given_name"), ("dob", "spouse_dob"),
                             ("country_of_birth", "spouse_country_of_birth"), ("birth_city", "spouse_birth_city"), ("a_number", "spouse_a_number")):
            put(f"petitioner.{mine}", _value(graph, f"applicant.{theirs}"), f"the client's spouse ({theirs})")
        put("petitioner.marital_status", "Married", "married to the beneficiary")
        put("applicant.marital_status", "Married", "married to the petitioner")
        # living together: the client's own address is the petitioner's too (the reviewer corrects it if not)
        for part in ("street", "unit_type", "apt", "city", "state", "zip"):
            put(f"petitioner.mailing_{part}", _value(graph, f"applicant.spouse_{part}") or _value(graph, f"applicant.physical_{part}"), "the couple's address")
            put(f"petitioner.address1_{part}", _value(graph, f"petitioner.mailing_{part}"), "the petitioner's mailing address")
            put(f"family.lived_together_{part}", _value(graph, f"applicant.physical_{part}"), "the couple's current address")
        put("petitioner.mailing_same_as_physical", "Yes", "one address given")
        put("petitioner.family_name_as_spouse", _value(graph, "petitioner.family_name"), "the beneficiary's spouse is the petitioner")
        put("petitioner.given_name_as_spouse", _value(graph, "petitioner.given_name"), "the beneficiary's spouse is the petitioner")
    category = CATEGORY.get((_value(graph, "family.relationship"), _value(graph, "petitioner.status")))
    put("applicant.filing_category", category, "relationship + the petitioner's status")
    if _value(graph, "family.relationship") == "Child":  # a son or daughter: the age and marriage decide (src/preference.py)
        from preference import child_category

        put("applicant.filing_category", child_category(graph, clock.today()),
            "the petitioner's status, the client's age and marital status (the attorney confirms the age under the Child Status Protection Act)")
    if _value(graph, "petitioner.status") == "USC":
        put("petitioner.domicile_country", "USA", "a U.S. citizen petitioner")
    if _value(graph, "applicant.father_family_name"):
        put("applicant.parent1_sex", "M", "father")
    if _value(graph, "applicant.mother_family_name"):
        put("applicant.parent2_sex", "F", "mother")
    if _value(graph, "applicant.i94_arrival_date") or _value(graph, "applicant.i94_number"):
        put("applicant.ever_in_us", "Yes", "an I-94 in the case")
    put("applicant.ever_in_proceedings", _value(graph, "applicant.part9.in_removal_proceedings"), "Part 9 removal proceedings answer")
    # tax returns read from the folder (i864.tax_return_<year>) -> the I-864's three most recent years
    years = sorted(((k.rsplit("_", 1)[1], _value(graph, k)) for k in graph.all_facts() if k.startswith("i864.tax_return_") and _value(graph, k)), reverse=True)
    for n, (year, income) in enumerate(years[:3], start=1):
        put(f"i864.tax_year{n}", year, "a tax return in the folder")
        put(f"i864.tax_income{n}", income, f"the {year} tax return")
    _household(graph, put)
    if _value(graph, "i864.current_income"):
        put("i864.employed", "Yes" if _value(graph, "petitioner.employer1_name") not in ("UNEMPLOYED", None) else None, "an employer given")
        put("i864.occupation", _value(graph, "petitioner.employer1_occupation"), "the petitioner's occupation")
        put("i864.employer1", _value(graph, "petitioner.employer1_name"), "the petitioner's employer")
        members = household_members(graph)
        total = household_income(graph)
        put("i864.household_income", f"{total:.0f}" if members and total is not None else _value(graph, "i864.current_income"),
            "the sponsor's income and the household members' (Form I-864A)" if members else "the sponsor's own income only (no household member's counted)")
        put("i864.filed_taxes", "Yes" if _value(graph, "i864.tax_year1") else None, "a tax year given")
        put("i864.household_sponsored", "1", "the client")
    _supplement_a(graph, put)
    preparer = " ".join(x for x in (_value(graph, "firm.preparer_given_name"), _value(graph, "firm.preparer_family_name")) if x)
    put("companion.preparer_full_name", preparer or None, "the firm")
    if _value(graph, "i864.use_ez") == "Yes":  # Form I-864EZ, Part 1: the three statements the attorney confirmed by choosing it
        for key in ("i864ez.q1_petitioner", "i864ez.q2_w2_income", "i864ez.q3_only_person"):
            put(key, "Yes", "the attorney chose Form I-864EZ (I-864EZ Instructions, Part 1)")
    # each G-28 names the forms it covers (30 characters): the supplements this packet adds. "I-485A" is Supplement A's number in Form G-1055.
    if _value(graph, "family.adjust_245i") == "Yes":
        put("companion.g28_forms", "I-485, I-485A, I-765", "the beneficiary's forms, with Supplement A")
    sponsor = ["I-864EZ" if _value(graph, "i864.use_ez") == "Yes" else "I-864"] + (["I-864A"] if contracts(graph) else [])
    if sponsor != ["I-864"]:
        put("companion.g28_forms_i130", "I-130, I-130A, " + ", ".join(sponsor), "the petitioner's forms")
    return graph


def _full(graph, prefix: str) -> str | None:
    return " ".join(x for x in (_value(graph, f"{prefix}given_name"), _value(graph, f"{prefix}middle_name"), _value(graph, f"{prefix}family_name")) if x) or None


def _amount(raw: Any) -> float | None:
    try:
        return float(str(raw).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def household_income(graph) -> float | None:
    """The sponsor's current income plus every counted household member's (I-864 Part 6, items 7-12)."""
    amounts = [_amount(_value(graph, "i864.current_income"))] + [_amount(_value(graph, f"i864a{n}.income")) for n in household_members(graph)]
    return sum(a for a in amounts if a is not None) if amounts[0] is not None else None


def _household(graph, put) -> None:
    """The household members' facts the case already holds -- the client's own, the sponsor's address -- and their rows on the I-864."""
    for n in household_members(graph):
        rel = _value(graph, f"i864a{n}.relationship")
        if rel in HM_IMMIGRANT:  # the intending immigrant is the client
            for part in ("family_name", "given_name", "middle_name", "dob", "country_of_birth", "ssn", "a_number"):
                put(f"i864a{n}.{part}", _value(graph, f"applicant.{part}"), "the client (the intending immigrant)")
        for part in ("street", "apt", "city", "state", "zip"):  # a household member lives with the sponsor (I-864A Instructions, Part 2)
            put(f"i864a{n}.{part}", _value(graph, f"petitioner.mailing_{part}") or _value(graph, f"petitioner.address1_{part}"),
                "the sponsor's address (a household member shares it; the attorney corrects it for a dependent living elsewhere)")
    for row, n in enumerate(household_members(graph), start=1):  # the I-864's Part 6, items 8-11
        rel = _value(graph, f"i864a{n}.relationship")
        put(f"i864.person{row}_name", _full(graph, f"i864a{n}."), f"household member {n}")
        put(f"i864.person{row}_relationship", HM_ROW.get(rel), f"household member {n}")
        put(f"i864.person{row}_income", _value(graph, f"i864a{n}.income"), f"household member {n}")
    if contracts(graph):
        put("i864.members_i864a", "Yes", "a Form I-864A for each household member (I-864 Part 6, item 13)")
    free = [n for n in household_members(graph) if n not in contracts(graph)]
    if free:  # the client counted without an I-864A: no accompanying dependents (I-864 Part 6, item 14)
        put("i864.immigrant_no_i864a", "Yes", "the intending immigrant has no accompanying dependents (I-864A Instructions)")
        put("i864.immigrant_no_i864a_name", _full(graph, f"i864a{free[0]}."), "the intending immigrant")


def _age(dob: Any, on: date) -> int | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(dob or ""))
    if not m:
        return None
    born = date(int(m[1]), int(m[2]), int(m[3]))
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def _supplement_a(graph, put) -> None:
    """Supplement A's facts from the case, once the attorney marks INA 245(i); and the I-485's own Part 2, item 4."""
    adjust = _value(graph, "family.adjust_245i")
    put("applicant.part2.adjusting_under_245i", adjust, "the attorney's INA 245(i) answer (Supplement A)")
    if adjust != "Yes":
        return
    src = "mailing" if _value(graph, "applicant.mailing_street") else "physical"
    for part in ("street", "unit_type", "apt", "city", "state", "zip"):
        put(f"supa.mailing_{part}", _value(graph, f"applicant.{src}_{part}"), f"the client's {src} address")
    if src == "mailing":
        put("supa.mailing_in_care_of", _value(graph, "applicant.mailing_in_care_of"), "the client's mailing address")
    if _value(graph, "supa.basis") in SUPA_BASES[:2]:  # the client is the principal beneficiary
        put("supa.principal_family_name", _value(graph, "applicant.family_name"), "the client is the principal beneficiary")
        put("supa.principal_given_name", _value(graph, "applicant.given_name"), "the client is the principal beneficiary")
        put("supa.principal_a_number", _value(graph, "applicant.a_number"), "the client is the principal beneficiary")
    category = _value(graph, "applicant.filing_category")
    put("supa.category", str(category).upper() if category else None, "the I-485's Part 2 category")
    if not (_value(graph, "applicant.i94_arrival_date") or _value(graph, "applicant.i94_number")):
        put("supa.bar_a", "Yes", "no I-94 or inspected entry in the case")
    age = _age(_value(graph, "applicant.dob"), clock.today())
    if age is not None and age < 17 and str(_value(graph, "applicant.marital_status") or "").lower() != "married":
        put("supa.fee_exemption", SUPA_UNDER_17, "the client's date of birth: under 17 and unmarried (the attorney confirms on the filing day)")


def settings() -> dict[str, Any]:
    import settings as firm_settings

    data = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
    data["poverty_guidelines"] = firm_settings.overlay("poverty", data.get("poverty_guidelines") or {})  # set on the Settings page
    return data


def fees() -> dict[str, Any]:
    import fees as fee_schedule

    return fee_schedule.load()


def minimum_income(guides: dict[str, Any], state: str | None, size: int, active_duty_for_spouse: bool) -> int | None:
    """The I-864 minimum for a household: the region by the sponsor's state, 125% of
    the guideline (100% for a sponsor on active duty petitioning for a spouse or child)."""
    region = {"AK": "alaska", "HI": "hawaii"}.get(str(state or "").upper(), "contiguous")
    table = (guides.get(region) or {}).get("p100" if active_duty_for_spouse else "p125") or {}
    sizes = {int(k): v for k, v in table.items() if k.isdigit()}
    if not sizes or size < 1:
        return None
    if size in sizes:
        return sizes[size]
    if size < min(sizes):
        return sizes[min(sizes)]
    return sizes[max(sizes)] + (size - max(sizes)) * int(table.get("each_additional") or 0)


def income_check(graph) -> str | None:
    """The sponsor's income against the I-864P guidelines (schemas/law/family_settings.json,
    copied from USCIS each year -- never from memory)."""
    guides = settings().get("poverty_guidelines") or {}
    if not guides.get("effective"):
        return held(OFFICE, "Set this year's poverty guidelines (Form I-864P) on the Settings page: the packet checks the sponsor's income against them.")
    low = _below_minimum(graph, guides)
    if low:
        amount, need, pct, n = low
        whose = "the sponsor's and household members' income" if household_members(graph) else "the sponsor's income"
        return held(ATTORNEY, f"I-864: {whose} (${amount:,.0f}) is below {pct} of the poverty guidelines effective {guides['effective']} for a household "
                              f"of {n} (${need:,.0f}). Add assets (Part 7), a household member's income (I-864A) or a joint sponsor: ask the attorney.")
    return None


def _below_minimum(graph, guides: dict[str, Any]) -> tuple[float, int, str, int] | None:
    """(income, the minimum, 125% or 100%, household size) when the household income counted is below the I-864P minimum."""
    size, amount = _value(graph, "i864.household_size"), household_income(graph)
    try:
        n = int(str(size or "").strip())
    except ValueError:
        return None
    if amount is None:
        return None
    active = _value(graph, "petitioner.military") == "Yes" and _value(graph, "family.relationship") in ("Spouse", "Child")
    state = _value(graph, "petitioner.address1_state") or _value(graph, "petitioner.mailing_state")
    need = minimum_income(guides, state, n, active)
    return (amount, need, "100%" if active else "125%", n) if need and amount < need else None


def ez_fit(graph) -> list[str]:
    """What in the case rules out Form I-864EZ (I-864EZ Instructions 08/24/26, "Who May Use" and "When Not To Use Form I-864EZ");
    empty when nothing does. Whether the income is only a salary or pension on Forms W-2, and whether anyone else immigrates
    on this I-130, the attorney confirms."""
    out = []
    if household_members(graph):
        out.append("a household member's income is counted, and the I-864EZ takes only the sponsor's own salary or pension")
    employer = re.sub(r"[\s-]", "", str(_value(graph, "petitioner.employer1_name") or "")).upper()
    if employer == "SELFEMPLOYED":
        out.append("the sponsor is self-employed (Part 1, item 2)")
    elif employer == "UNEMPLOYED":
        out.append("the sponsor's income isn't a salary or pension (Part 1, item 2)")
    if _below_minimum(graph, settings().get("poverty_guidelines") or {}):
        out.append("the sponsor's salary or pension is below the poverty guidelines' minimum")
    return out


def supa_fee(graph, today: date | None = None) -> tuple[int | None, str]:
    """(the Supplement A sum, why) from schemas/law/fees.json: Form G-1055 10/01/26 (8 CFR 245.10(c) says the same)."""
    import fees as fee_schedule

    paper = fee_schedule.load(today).get("paper") or {}
    exemption = _value(graph, "supa.fee_exemption")
    if exemption == SUPA_UNDER_17:
        return paper.get("i485_supa_under_17"), "an unmarried child under 17 (Form G-1055)"
    if exemption == SUPA_FAMILY_UNITY:
        return paper.get("i485_supa_family_unity"), "the spouse or unmarried child under 21 of a legalized alien, with the Form I-817 receipt or approval notice (Form G-1055)"
    return paper.get("i485_supa"), "the Supplement A sum (Form G-1055)"


def _money(n: Any) -> str:
    return f"${n:,}" if isinstance(n, (int, float)) else "[fee]"


def notes(graph, today: date | None = None) -> list[dict[str, str]]:
    """What the family panel says above its questions: the affidavit of support's forms, and Supplement A's sum."""
    out = []
    against = ez_fit(graph)
    if _value(graph, "i864.use_ez") == "Yes" and not against:
        out.append({"level": "info", "title": "Form I-864EZ", "text": "Filed in place of the I-864, as the attorney chose."})
    elif against:
        out.append({"level": "info", "title": "Form I-864EZ", "text": "Not for this case: " + "; ".join(against)
                    + " (I-864EZ Instructions, 'When Not To Use Form I-864EZ'). The I-864 is filed."})
    else:
        out.append({"level": "info", "title": "Form I-864EZ", "text": "May fit: the petitioner sponsors only this relative, with no household member's income counted. "
                    "It also needs income only from a salary or pension shown on Forms W-2, and no one else immigrating on this I-130 "
                    "(I-864EZ Instructions, 'Who May Use Form I-864EZ'). The attorney confirms by answering Yes below."})
    members, signing = household_members(graph), contracts(graph)
    if members:
        text = (f"{len(signing)} Form I-864A in the packet, one for each household member counted, signed by the sponsor and the member "
                "(I-864A Instructions).") if signing else "No Form I-864A in the packet."
        if len(signing) < len(members):
            text += (" The client's own income is counted without one: the intending immigrant signs an I-864A only with accompanying dependents "
                     "(I-864A Instructions, 'If the Intending Immigrant Is a Household Member').")
        out.append({"level": "info", "title": "Household members", "text": text})
    if _value(graph, "family.adjust_245i") == "Yes":
        amount, why = supa_fee(graph, today)
        out.append({"level": "info", "title": "Supplement A (INA 245(i))",
                    "text": f"{_money(amount)}: {why}." + (" Its own payment, by its own Form G-1450." if amount else "")
                    + " With it: the qualifying petition's receipt or approval notice (or the labor certification), and proof of presence in the U.S. "
                      "on 12/21/2000 when it was filed after 01/14/1998 (Supplement A Instructions)."})
    return out


def _supa_problems(graph, today: date) -> list[str]:
    """What stops Supplement A (8 CFR 245.10; the Supplement A Instructions, 09/18/26)."""
    from filing_questions import iso, us

    out = []
    basis, filed = _value(graph, "supa.basis"), iso(_value(graph, "supa.filed_on"))
    early, late = date(1998, 1, 14), date(2001, 4, 30)
    if filed and filed > late:
        out.append(f"Supplement A: a petition or labor certification filed {us(filed)} doesn't grandfather the client: it must have been filed on or "
                   "before 04/30/2001 (8 CFR 245.10(a)(1)).")
    elif filed and basis in (SUPA_BASES[0], SUPA_BASES[2]) and filed > early:
        out.append(f"Supplement A, Part 2, 1: filed {us(filed)}, after 01/14/1998: choose the basis for 01/15/1998 to 04/30/2001 (1.b or 1.d).")
    elif filed and basis in (SUPA_BASES[1], SUPA_BASES[3]) and filed <= early:
        out.append(f"Supplement A, Part 2, 1: filed {us(filed)}, on or before 01/14/1998: choose the basis for those dates (1.a or 1.c).")
    if basis in (SUPA_BASES[1], SUPA_BASES[3]) and _value(graph, "supa.present_2000_12_21") != "Yes":
        out.append("Supplement A: filed after 01/14/1998, so the principal beneficiary must have been in the U.S. on 12/21/2000 "
                   "(8 CFR 245.10(a)(1)(ii)): answer it, with the evidence.")
    if len(str(_value(graph, "supa.category") or "")) > 34:
        out.append("Supplement A, Part 2, 5: the category is longer than the box (34 characters): shorten it.")
    if not any(_value(graph, f"supa.bar_{x}") == "Yes" for x, _ in SUPA_BARS):
        out.append("Supplement A, Part 3: no bar to adjusting under INA 245(a) is marked. If none applies, the client adjusts under 245(a) and files "
                   "no Supplement A (Supplement A Instructions, 'Bars to Adjustment').")
    age = _age(_value(graph, "applicant.dob"), today)
    married = str(_value(graph, "applicant.marital_status") or "").lower() == "married"
    if _value(graph, "supa.fee_exemption") == SUPA_UNDER_17 and ((age is not None and age >= 17) or married):
        out.append("Supplement A: no sum only for a client unmarried and under 17 when the I-485 is filed (8 CFR 245.10(c)(1)); "
                   + (f"the client is {age}." if age is not None and age >= 17 else "the client is married."))
    if _value(graph, "applicant.nta_present"):
        out.append(held(ATTORNEY, "Supplement A: a client in immigration court files it with the court, and a copy with USCIS (Supplement A Instructions, "
                                  "'Where to File'): the attorney decides."))
    return out


def case_schema(schema: dict[str, Any], graph, today: date) -> dict[str, Any]:
    """The family packet as this case files it (src/packet.py for_case): Supplement A after the I-485 when the attorney marks INA 245(i),
    the I-864EZ in place of the I-864 when chosen, and one I-864A per household member after the affidavit of support."""
    forms = list(schema.get("forms") or [])
    handwork = list(schema.get("handwork") or [])
    if "i485" in forms and _value(graph, "family.adjust_245i") == "Yes":
        forms.insert(forms.index("i485") + 1, "i485supa")
        handwork.append({"kind": "attach", "text": "Supplement A: the qualifying petition's receipt or approval notice (Form I-797) or the labor "
                                                   "certification (Form ETA-750), and proof the principal beneficiary was in the U.S. on 12/21/2000 "
                                                   "when it was filed after 01/14/1998 (Supplement A Instructions)."})
        if _value(graph, "supa.fee_exemption") == SUPA_FAMILY_UNITY:
            handwork.append({"kind": "attach", "text": "Supplement A: a copy of the Form I-817 receipt or approval notice (no sum is paid with it, "
                                                       "Form G-1055)."})
    if "i864" in forms:
        at = forms.index("i864")
        if _value(graph, "i864.use_ez") == "Yes":
            forms[at] = "i864ez"
        forms[at + 1:at + 1] = [f"i864a_{n}" for n in contracts(graph)]
        if contracts(graph):
            handwork.append({"kind": "attach", "text": "Each household member's Form I-864A evidence: their most recent federal tax return or IRS "
                                                       "transcript, and proof of the relationship and the shared home (I-864A Instructions)."})
    return schema | {"forms": forms, "handwork": handwork}


def person_graph(graph, base: str, n: int):
    """One household member's Form I-864A (src/packet.py fills one per member): the case, with that member's answers under the
    form's own keys (i864a.m.*)."""
    from factgraph import FactGraph

    g = FactGraph.from_dict(graph.to_dict())
    if base != "i864a":
        return g
    rel = _value(graph, f"i864a{n}.relationship")
    job = _value(graph, f"i864a{n}.employment")
    taxes = _value(graph, f"i864a{n}.tax_year1")
    values = {f"i864a.m.{k}": _value(graph, f"i864a{n}.{k}") for k in ("family_name", "given_name", "middle_name", "dob", "country_of_birth", "ssn",
                                                                       "a_number", "street", "apt", "city", "state", "zip", "employment",
                                                                       "employer", "income", "tax_year1", "tax_income1", "dependent_relationship")}
    values |= {"i864a.m.relationship": rel, "i864a.m.full_name": _full(graph, f"i864a{n}."), "i864a.m.same_address": "Yes",
               "i864a.m.occupation": _value(graph, f"i864a{n}.occupation") if job == "Employed" else None,
               "i864a.m.self_employed_as": _value(graph, f"i864a{n}.occupation") if job == "Self-employed" else None,
               "i864a.m.filed_taxes": "Yes" if taxes else None, "i864a.sponsor_full_name": _full(graph, "petitioner.")}
    for key, value in values.items():
        if value not in (None, ""):
            g.add_source(key, f"household member {n}", "derived", str(value), value, 1.0)
    return g


def letter(config: dict[str, Any], graph, schema: dict[str, Any], today: date) -> dict[str, Any]:
    """The cover letter with this packet's supplements: their lines among the forms, and the fees paragraph with Supplement A's sum."""
    forms = schema.get("forms") or []
    members = [f for f in forms if f.startswith("i864a_")]
    if not ({"i485supa", "i864ez"} & set(forms) or members):
        return config
    names = {"i485supa": "Beneficiary’s I-485 Supplement A - Adjustment of Status Under Section 245(i)",
             "i864ez": "Petitioner’s I-864EZ - Affidavit of Support Under Section 213A of the INA"}
    names |= {f: f"Household Member’s I-864A - Contract Between Sponsor and Household Member ({_full(graph, 'i864a' + f.split('_')[1] + '.') or 'household member'})"
              for f in members}
    order = []
    for fid in config.get("form_order") or []:
        order.append(fid)
        if fid == "i485":
            order.append("i485supa")
        if fid == "i864":
            order += ["i864ez", *members]
    import fees as fee_schedule

    data = fee_schedule.load(today)
    paper = data.get("paper") or {}
    supa, why = supa_fee(graph, today)
    paid = [(label, amount) for fid, label, amount in (("i130", "Form I-130", paper.get("i130")), ("i485", "Form I-485", paper.get("i485")),
                                                       ("i485supa", "Form I-485 Supplement A", supa),
                                                       ("i765", "Form I-765", paper.get("i765_with_pending_i485_paid"))) if fid in forms and amount != 0]
    text = "Enclosed are the filing fees, each paid by its own Form G-1450: " + "; ".join(f"{label}, {_money(amount)}" for label, amount in paid) + "."
    if len(paid) > 1:
        text += f" A total of {_money(sum(a for _, a in paid)) if all(isinstance(a, (int, float)) for _, a in paid) else '[total]'}."
    free = [name for fid, name in (("i130a", "I-130A"), ("i864", "I-864"), ("i864ez", "I-864EZ")) if fid in forms] + (["I-864A"] if members else [])
    if free:
        text += (f" Form {free[0]} has" if len(free) == 1 else " Forms " + ", ".join(free[:-1]) + f" and {free[-1]} have") + " no filing fee."
    if "i485supa" in forms and supa == 0:
        text += f" No Supplement A sum is due: {why}, edition {data.get('edition') or 'current'}."
    return config | {"forms": {**config.get("forms", {}), **names}, "form_order": order, "fees": text}


def _graph(client_dir: Path):
    from review.state import reviewed_graph

    return derive(reviewed_graph(client_dir))


def _has_doc(client_dir: Path, *doc_types: str) -> list[str]:
    meta = json.loads((client_dir / "meta.json").read_text(encoding="utf-8")) if (client_dir / "meta.json").exists() else {}
    return [doc for doc, kind in (meta.get("classifications") or {}).items() if kind in doc_types]


PETITIONER_PROOF = ("us_passport", "citizenship_certificate", "green_card", "us_birth_certificate")
# The petitioner as the client's document record names them (src/documents.py person): the
# beneficiary (the client) is the petitioner's spouse, parent or child (I-130 Part 1).
_PETITIONER_IS = {"Spouse": ["spouse"], "Parent": ["child"], "Child": ["parent"]}


def petitioner_documents(client_dir: Path, graph=None, doc_types: tuple[str, ...] = PETITIONER_PROOF) -> list[str]:
    """The petitioner's proof of status in the folder: the document record's person first (the card's own
    reader, or a reviewer), so the client's own green card or passport never stands in for the petitioner's;
    a document nobody has placed counts by its type, as before."""
    import documents

    rel = _value(graph, "family.relationship") if graph is not None else None
    return documents.documents_for(client_dir, doc_types, ["petitioner", *_PETITIONER_IS.get(rel, [])])


def status(client_dir: Path) -> dict[str, Any]:
    graph = _graph(client_dir)
    questions = []
    hidden = {section for section, shown in SHOWN_WHEN.items() if not shown(graph)}
    for key, label, section, spec, required in QUESTIONS:
        if section in hidden:
            continue
        fact = graph.get(key)
        sources = [{"doc": s.doc_id, "type": s.doc_type, "raw": s.raw_value} for s in (fact.sources if fact is not None else [])][:3]
        questions.append({"key": key, "label": label, "section": section, "who": "the petitioner" if key.startswith(("petitioner.", "i864.")) else "the attorney",
                          "input": spec, "required": required, "value": _value(graph, key), "sources": sources, "answered_by": getattr(getattr(fact, "review", None), "resolved_by", None)})
    return {"questions": questions, "problems": problems(client_dir, graph=graph, questions=questions), "notes": notes(graph),
            "petitioner_documents": petitioner_documents(client_dir, graph)}


@producer(OFFICE)
def problems(client_dir: Path, graph=None, questions: list | None = None) -> list[str]:
    if questions is None:
        return status(client_dir)["problems"]
    out = []
    missing = [q["label"] if q["key"].startswith(("i864a", "supa.")) else re.split(r" · | -- |: ", q["label"], maxsplit=1)[-1]
               for q in questions if q["required"] and q["value"] is None]
    if missing:
        out.append(held(of_first(questions), f"Family petition questions not answered yet ({len(missing)}): {', '.join(missing[:8])}{' ...' if len(missing) > 8 else ''}."))
    rel, st = _value(graph, "family.relationship"), _value(graph, "petitioner.status")
    if rel == "Spouse" and not _has_doc(client_dir, "marriage_certificate"):
        out.append(held(CLIENT, "A spouse petition needs the marriage certificate: none found in the client's folder."))
    if st == "USC" and not petitioner_documents(client_dir, graph, ("us_passport", "citizenship_certificate", "us_birth_certificate")):
        out.append(held(CLIENT, "Proof of the petitioner's U.S. citizenship (U.S. passport, birth certificate or naturalization certificate) isn't in the folder."))
    if st == "LPR" and not petitioner_documents(client_dir, graph, ("green_card",)):
        out.append(held(CLIENT, "Proof of the petitioner's permanent residence (the green card) isn't in the folder."))
    import preference

    if rel == "Child" and st == "LPR" and str(_value(graph, "applicant.marital_status") or "").lower() == "married":
        out.append(held(ATTORNEY, "A permanent resident can't petition for a married son or daughter: no family category. Ask the attorney."))
    pref = preference.status(graph, clock.today())
    if pref["class"] not in (None, "IR") and preference.petition_on_file(graph):  # the I-485 waits for a current priority date
        out += pref["problems"]
        if pref["current"] is False:
            out.append(f"{pref['text']}: not current. The I-485 can't be filed this month.")
    adjust_245i = _value(graph, "family.adjust_245i") == "Yes"
    if not adjust_245i and not (_value(graph, "applicant.i94_arrival_date") or _value(graph, "applicant.i94_number")):
        out.append(held(ATTORNEY, "No I-94 or lawful entry in the case: adjustment under INA 245(a) needs an inspected entry. Ask the attorney (245(i)?)."))
    if adjust_245i:
        out += _supa_problems(graph, clock.today())
    low = income_check(graph)
    if low:
        out.append(low)
    against = ez_fit(graph)
    if _value(graph, "i864.use_ez") == "Yes" and against:
        out.append("Form I-864EZ can't be used: " + "; ".join(against) + " (I-864EZ Instructions, 'When Not To Use Form I-864EZ'). "
                   "Answer No to file the I-864.")
    for n in household_members(graph):
        age = _age(_value(graph, f"i864a{n}.dob"), clock.today())
        if age is not None and age < 18:
            out.append(f"Household member {n} is under 18: only someone 18 or older signs an I-864A (I-864A Instructions, 'Who May Be Considered "
                       "a Household Member').")
    paper = fees().get("paper") or {}
    if not fees().get("checked") or any(paper.get(f) is None for f in ("i130", "i485", "i765_with_pending_i485_paid", *(("i485_supa",) if adjust_245i else ()))):
        out.append("Set the current USCIS filing fees on the Settings page (Filing fees), from the latest Form G-1055: the cover letter states the amounts enclosed.")
    return out


def use_person(client_dir: Path, person_id: str, reviewer: str, role: str | None = None) -> list[str]:
    """The petitioner's name, phone and email on the petition from a Person record on the case (journey "people": someone who is not a
    client). Only the questions with no answer yet are filled, and each is recorded as a typed answer by whoever pressed the button,
    like every other fix. Returns the questions' words that were filled."""
    import journey

    person = next((p for p in (journey._status(client_dir).get("journey") or {}).get("people") or [] if p["id"] == person_id), None)
    if person is None:
        raise ValueError("No such person.")
    if person["person"] != "petitioner":
        raise ValueError("Only the petitioner's details go on the family petition.")
    have = {q["key"]: q["value"] for q in status(client_dir)["questions"]}
    wanted = {"petitioner.family_name": person["family_name"], "petitioner.given_name": person["given_name"],
              "petitioner.daytime_phone": person["phone"], "petitioner.email": person["email"]}
    values = {k: v for k, v in wanted.items() if v and have.get(k) in (None, "")}
    if not values:
        raise ValueError("The petition already has these answers (or this person has none to give): nothing was changed.")
    answer(client_dir, values, reviewer, role, note=f"from {person['name']}'s record on the case")
    labels = {key: label for key, label, *_ in QUESTIONS}
    return [labels[k] for k in values]


def answer(client_dir: Path, values: dict[str, Any], reviewer: str, role: str | None = None, note: str = "family petition question") -> dict[str, Any]:
    from review.state import record_decision

    specs = {key: (label, spec) for key, label, _section, spec, _req in QUESTIONS}
    for key, raw in values.items():
        if key not in specs:
            raise ValueError(f"{key} is not a family petition question.")
        label, spec = specs[key]
        item = {"id": f"family:{key}", "kind": "family", "level": "review", "title": label, "group": "attorney", "actions": ["set", "blank"],
                "facts": [{"key": key, "input": spec}]}
        decision = {"action": "blank" if raw in ("", None) else "set", "values": {} if raw in ("", None) else {key: raw},
                    "reviewer": reviewer, **({"role": role} if role else {}), "note": note}
        record_decision(client_dir, item, decision)
    return status(client_dir)
