"""Humanitarian parole for someone outside the United States: Form I-131 (edition 01/20/25), Part 1, item 7, with a Form I-134 (Declaration
of Financial Support, edition 01/20/25) for each person. Read on 10/02/2026 from uscis.gov/i-131 (updated 08/14/2026), uscis.gov/i-134
(updated 06/03/2026), "Humanitarian or Significant Public Benefit Parole for Aliens Outside the United States" (updated 12/15/2025),
"Direct Filing Addresses for Form I-131" (updated 08/11/2026), the I-131 Instructions (01/20/25) and Form G-1055 (10/01/26):

  What it is: INA 212(d)(5)(A) parole "for urgent humanitarian reasons or significant public benefit", at USCIS's discretion, case by case,
    usually for no more than a year; it is "not intended to be used solely to avoid normal visa processing procedures". The petitioner
    (the client, in the U.S., for someone else; or the client, outside the U.S., for themself) must show the reason and that the person
    merits discretion. "A petitioner does not have to be a resident of the United States or related to the beneficiary."
  What goes in (the I-131 Instructions, "Parole Document for Aliens Outside the United States", and the humanitarian parole page): the
    I-131 (completed and signed); an I-134 for each beneficiary from the financial supporter, with the supporter's ID and proof of status;
    the filing fee for each beneficiary (or a fee waiver); a detailed explanation of the reason and the length of parole; a statement why
    a visa can't be obtained (when and where tried, a denial letter) and why a waiver can't be (if applicable); any decision on an
    immigrant or nonimmigrant petition for the person; a copy of the biographical page of the beneficiary's passport (or why none);
    the petitioner's ID and proof of citizenship or status; a G-28 when represented. "Lack of evidence of financial support ... is a strong
    negative factor" (the humanitarian parole page).
  Fee (G-1055, Appendix B, page 41): initial parole for someone outside the U.S., not under a specific program (item 7): Paper $630, online
    $580, for each person; $0 when the person is a current or former U.S. armed forces service member, seeking or granted SIJ, T or U status,
    a VAWA self-petitioner, or an Afghan or Iraqi special immigrant (the General Fee Exemptions: when filing for someone else the exemption
    turns on THAT person). The I-134 is $0 (page 9). A fee waiver (Form I-912) is possible for the USCIS fee (not built here). CBP charges
    the Pub. L. 119-21 Immigration Parole Fee at the port of entry, not with the filing: $1,020 ($1,050 from 10/16/2026, USCIS alert of
    09/30/2026).
  Where: the USCIS Dallas Lockbox, Attn: HP, P.O. Box 660865, Dallas, TX 75266-0865 (couriers: 2501 S. State Hwy. 121 Business, Suite 400,
    Lewisville, TX 75067-8003): the I-131 addresses page, "Part 1, Item 7". Or online (the item 7 request may be filed online unless a fee
    waiver is asked for: the humanitarian parole page). The G-1145 goes on top of a mailed package.
  Not built: parole under a specific program (the Filipino World War II Veterans Parole Program, IMMVI, the Family Reunification Task
    Force, CAM re-parole), re-parole from inside the U.S., parole in place, and more than one supporter for a person (each files an I-134).

Every client-facing sentence here is DRAFT for the attorney.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from filing_questions import DATE, LINES, TEXT, YES_NO, addresses, has_doc, money, putter, value
from filing_questions import iso as _d
from holders import ATTORNEY, CLIENT, OFFICE, held, producer


def _us(iso_date: str) -> str:
    """2026-10-16 as 10/16/2026."""
    y, m, d = iso_date.split("-")
    return f"{m}/{d}/{y}"

TITLE = "Humanitarian parole (I-131 and I-134)"
MAIL_TO = ["USCIS", "Attn: HP", "P.O. Box 660865", "Dallas, TX 75266-0865"]
COURIER = ["USCIS", "Attn: HP (Box 660865)", "2501 S. State Hwy. 121 Business", "Suite 400", "Lewisville, TX 75067-8003"]
SOURCE = "uscis.gov/i-131-addresses, updated 08/11/2026"
TYPE = "Initial parole for someone outside the U.S., not under a specific program (Part 1, item 7)"
SELF, OTHER = "The client, for themself (the client is outside the U.S.)", "The client, for someone else who is outside the U.S."
MAX_PEOPLE = 4
NO_EXEMPTION = "None of these"
EXEMPT = [NO_EXEMPTION, "A current or former U.S. armed forces service member", "Seeking or granted Special Immigrant Juvenile classification",
          "Seeking or granted T nonimmigrant status", "Seeking or granted U nonimmigrant status", "A VAWA self-petitioner or derivative",
          "An Afghan or Iraqi special immigrant (translator, interpreter, or employed by the U.S. Government)"]
SUPPORTER_IS = ["The client (who lives in the U.S.)", "Another person in the U.S."]
STATUSES = ["U.S. citizen", "Lawful permanent resident", "U.S. national", "Nonimmigrant", "Asylee", "Refugee", "Parolee", "TPS holder",
            "Beneficiary of deferred action (including DACA) or Deferred Enforced Departure", "Other"]
EMPLOYMENT = ["Employed", "Self-employed", "Unemployed or not employed", "Retired", "Other"]
ASSETS = ["Checking - Bank Account", "Savings - Bank Account", "Annuities", "Stocks, Bonds, Certificates of Deposit", "Retirement or Educational Account",
          "Real Estate Holdings", "Personal Property (net value)"]
RACES = ["White", "Asian", "Black or African American", "American Indian or Alaska Native", "Native Hawaiian or Other Pacific Islander"]  # the portal's own list
EYES = ["Black", "Blue", "Brown", "Gray", "Green", "Hazel", "Maroon", "Pink", "Unknown/Other"]  # the portal's list, and the form's last box
HAIR = ["Bald", "Black", "Blond", "Brown", "Gray", "Red", "Sandy", "White", "Unknown/Other"]
HEIGHT = {"type": "text", "placeholder": "5'7\"", "pattern": r"^[3-8]'(\d|1[01])\"$"}  # the form's shape (the portal keeps it as 5'7")
WEIGHT = {"type": "text", "digits": True, "maxlen": 3}
MARITAL = ["Single", "Married", "Divorced", "Widowed", "Legally Separated", "Marriage Annulled", "Other"]


def _who(g) -> Any:
    return value(g, "parole.for_whom")


def people(graph) -> list[int]:
    """The people the parole is for: one (the client) when they file for themself, else the number the attorney set."""
    if _who(graph) == SELF:
        return [1]
    count = str(value(graph, "parole.count") or "")
    return list(range(1, min(int(count), MAX_PEOPLE) + 1)) if count.isdigit() else []


def _person(n: int) -> list[tuple[str, str, dict[str, Any], bool]]:
    who = f"Person {n}"
    k = f"parole.b{n}_"
    return [
        (k + "relationship", f"{who}: their relationship to the client (the sentence USCIS reads: 'sister', 'father', 'a friend')", TEXT, True),
        (k + "family_name", f"{who}: family name (Part 2, 16)", TEXT, True),
        (k + "given_name", f"{who}: given name", TEXT, True),
        (k + "middle_name", f"{who}: middle name", TEXT, False),
        (k + "dob", f"{who}: date of birth (Part 2, 18)", DATE, True),
        (k + "sex", f"{who}: sex", {"type": "choice", "options": ["M", "F"]}, True),
        (k + "marital_status", f"{who}: marital status (I-134, Part 3, 8)", {"type": "choice", "options": MARITAL}, True),
        (k + "city_of_birth", f"{who}: city or town of birth", TEXT, True),
        (k + "country_of_birth", f"{who}: country of birth (Part 2, 19)", TEXT, True),
        (k + "citizenship", f"{who}: country of citizenship or nationality (Part 2, 20)", TEXT, True),
        (k + "a_number", f"{who}: A-Number, if any (Part 2, 23)", TEXT, False),
        (k + "phone", f"{who}: telephone, if any (Part 2, 21)", TEXT, False),
        (k + "email", f"{who}: email, if any (Part 2, 22)", TEXT, False),
        (k + "street", f"{who}: where they live now: street (Part 2, 24)", TEXT, True),
        (k + "city", f"{who}: where they live now: city or town", TEXT, True),
        (k + "province", f"{who}: where they live now: province or state", TEXT, False),
        (k + "postal_code", f"{who}: where they live now: postal code", TEXT, False),
        (k + "country", f"{who}: where they live now: country", TEXT, True),
        (k + "ethnicity", f"{who}: Hispanic or Latino? (Part 3, 1)", {"type": "choice", "options": ["Hispanic or Latino", "Not Hispanic or Latino"]}, False),
        (k + "race", f"{who}: race (Part 3, 2; the form has a box for each, this asks for the main one)", {"type": "choice", "options": RACES}, False),
        (k + "height", f"{who}: height in feet and inches, like 5'7\" (Part 3, 3)", HEIGHT, False),
        (k + "weight_lbs", f"{who}: weight in pounds, numbers only (Part 3, 4)", WEIGHT, False),
        (k + "eye_color", f"{who}: eye color (Part 3, 5)", {"type": "choice", "options": EYES}, False),
        (k + "hair_color", f"{who}: hair color (Part 3, 6)", {"type": "choice", "options": HAIR}, False),
        (k + "in_proceedings", f"{who}: ever in exclusion, deportation, removal or rescission proceedings? (Part 4, 1)", YES_NO, True),
        (k + "prior_reentry_or_rtd", f"{who}: ever been issued a reentry permit or a Refugee Travel Document? (Part 4, 2.a)", YES_NO, True),
        (k + "prior_reentry_date", f"{who}: if yes, the date it was issued (Part 4, 2.b)", DATE, False),
        (k + "prior_reentry_disposition", f"{who}: if yes, what became of it: for example, still in use, lost, returned (Part 4, 2.c)", TEXT, False),
        (k + "prior_advance_parole", f"{who}: ever been issued an Advance Parole Document? (Part 4, 3.a)", YES_NO, True),
        (k + "prior_ap_date", f"{who}: if yes, the date it was issued (Part 4, 3.b)", DATE, False),
        (k + "prior_ap_disposition", f"{who}: if yes, what became of it: for example, used, lost, returned (Part 4, 3.c)", TEXT, False),
        (k + "exemption", f"{who}: no I-131 fee when they are one of these (Form G-1055's I-131 General Fee Exemptions)", {"type": "choice", "options": EXEMPT}, False),
    ]


SECTIONS = [
    ("The request (Part 1, item 7, and Part 8)", "the attorney", [
        ("parole.for_whom", "Part 1, 7 · Who the request is for", {"type": "choice", "options": [OTHER, SELF]}, True),
        ("parole.count", "How many people (one I-131 and one I-134 for each)", {"type": "choice", "options": [str(n) for n in range(1, MAX_PEOPLE + 1)]}, True),
        ("parole.reason", "Part 8, 1 · How the person qualifies for parole: the urgent humanitarian reason or significant public benefit, in brief "
                          "(the detailed statement and the evidence go with it)", LINES, True),
        ("parole.stay", "Part 8, 2 · How long they expect to stay in the U.S.", TEXT, True),
        ("parole.arrival_date", "Part 8, 3.a · The date they intend to arrive in the U.S.", DATE, False),
        ("parole.embassy_city", "Part 8, 3.b · The U.S. embassy, consulate or USCIS office abroad USCIS should notify: city", TEXT, True),
        ("parole.embassy_country", "Part 8, 3.b · That office: country", TEXT, True),
        ("parole.stay_until", "I-134, Part 3, 12 · The date the stay is expected to end (blank: no end date)", DATE, False),
        ("parole.visa_why", "Why the person can't get a U.S. visa (and any waiver): when and where they tried, or why they didn't try (the I-131 Instructions ask for this statement)",
         LINES, True),
        ("parole.refugee_status", "Part 1, 13 · Does the person who files hold refugee status, or was paroled as a refugee? (left blank on the form until someone answers)",
         YES_NO, False),
    ]),
    *[(f"Person {n}", "the attorney", _person(n), (lambda n: lambda g: n in people(g))(n)) for n in range(1, MAX_PEOPLE + 1)],
    ("The financial supporter (Form I-134, Part 2)", "the attorney", [
        ("parole.sponsor_is", "Who agrees to support them financially (the I-134 is signed by this person)", {"type": "choice", "options": SUPPORTER_IS}, True),
        ("parole.sponsor_family_name", "Part 2, 1 · The supporter's family name", TEXT, True),
        ("parole.sponsor_given_name", "Part 2, 1 · Given name", TEXT, True),
        ("parole.sponsor_middle_name", "Part 2, 1 · Middle name", TEXT, False),
        ("parole.sponsor_dob", "Part 2, 6 · Date of birth", DATE, True),
        ("parole.sponsor_birth_city", "Part 2, 7 · City or town of birth", TEXT, True),
        ("parole.sponsor_birth_country", "Part 2, 7 · Country of birth", TEXT, True),
        ("parole.sponsor_a_number", "Part 2, 8 · A-Number, if any", TEXT, False),
        ("parole.sponsor_status", "Part 2, 10 · The supporter's immigration status now", {"type": "choice", "options": STATUSES}, True),
        ("parole.sponsor_status_other", "Part 2, 10 · If Other: explain", TEXT, False),
        ("parole.sponsor_relationship", "Part 2, 11 · The supporter's relationship to the person (or each person)", TEXT, True),
        ("parole.sponsor_mailing_street", "Part 2, 3 · Mailing address: street", TEXT, True),
        ("parole.sponsor_mailing_apt", "Part 2, 3 · Apartment, suite or floor number", TEXT, False),
        ("parole.sponsor_mailing_city", "Part 2, 3 · City or town", TEXT, True),
        ("parole.sponsor_mailing_state", "Part 2, 3 · State (two letters)", TEXT, True),
        ("parole.sponsor_mailing_zip", "Part 2, 3 · ZIP code", TEXT, True),
        ("parole.sponsor_mailing_same", "Part 2, 4 · Is the mailing address the same as where the supporter lives?", YES_NO, True),
        ("parole.sponsor_physical_street", "Part 2, 5 · Where the supporter lives, if different: street", TEXT, False),
        ("parole.sponsor_physical_city", "Part 2, 5 · City or town", TEXT, False),
        ("parole.sponsor_physical_state", "Part 2, 5 · State (two letters)", TEXT, False),
        ("parole.sponsor_physical_zip", "Part 2, 5 · ZIP code", TEXT, False),
        ("parole.sponsor_employment", "Part 2, 12 · Employment status", {"type": "choice", "options": EMPLOYMENT}, True),
        ("parole.sponsor_job", "Part 2, 12 · Employed as (the job)", TEXT, False),
        ("parole.sponsor_employer", "Part 2, 12 · Name of the employer", TEXT, False),
        ("parole.sponsor_selfemployed_as", "Part 2, 12 · Self-employed as", TEXT, False),
        ("parole.sponsor_employment_other", "Part 2, 12 · Other: explain", TEXT, False),
        ("parole.sponsor_other_i134s", "Part 2, 13 · How many other I-134, I-134A, I-864, I-864EZ and I-864A the supporter has submitted whose obligation hasn't ended", TEXT, True),
        ("parole.sponsor_dependents", "Part 2, 14 · How many other dependents the supporter supports (including themself), not counting the person or those in 13", TEXT, True),
        ("parole.sponsor_income", "Part 2, 16 · The supporter's current annual income (US$, numbers only)", TEXT, True),
        ("parole.sponsor_asset1_type", "Part 2, 17 · Asset 1: the kind", {"type": "choice", "options": ASSETS}, False),
        ("parole.sponsor_asset1_amount", "Part 2, 17 · Asset 1: the cash value (US$)", TEXT, False),
        ("parole.sponsor_asset2_type", "Part 2, 17 · Asset 2: the kind", {"type": "choice", "options": ASSETS}, False),
        ("parole.sponsor_asset2_amount", "Part 2, 17 · Asset 2: the cash value (US$)", TEXT, False),
        ("parole.sponsor_asset3_type", "Part 2, 17 · Asset 3: the kind", {"type": "choice", "options": ASSETS}, False),
        ("parole.sponsor_asset3_amount", "Part 2, 17 · Asset 3: the cash value (US$)", TEXT, False),
        ("parole.sponsor_assets_total", "Part 2, 17 · The total of the assets (US$)", TEXT, False),
        ("parole.sponsor_contributions", "Part 2, 18 · Will the supporter also make specific contributions to the person's basic living needs (housing, school, work)?", YES_NO, True),
        ("parole.sponsor_contributions_text", "Part 2, 19 · Describe them: the housing (the address where the person will live), school, work", LINES, False),
        ("parole.sponsor_phone", "Part 5, 3 · The supporter's daytime telephone", TEXT, True),
        ("parole.sponsor_mobile", "Part 5, 4 · The supporter's mobile telephone", TEXT, False),
        ("parole.sponsor_email", "Part 5, 5 · The supporter's email", TEXT, False),
    ]),
]
MORE_QUESTIONS = "Choose who the request is for and how many people first: one set of questions for each person appears then."


def more_to_come(graph) -> bool:
    """Only until the number of people is known: after that the sets below are the whole list."""
    return not people(graph)


def _put_person(graph, put, n: int) -> None:
    """When the client files for themself, person 1 is the client."""
    v = lambda k: value(graph, k)  # noqa: E731
    k = f"parole.b{n}_"
    for mine, theirs in (("family_name", "family_name"), ("given_name", "given_name"), ("middle_name", "middle_name"), ("dob", "dob"), ("sex", "sex"),
                         ("country_of_birth", "country_of_birth"), ("citizenship", "citizenship"), ("a_number", "a_number"), ("phone", "daytime_phone"),
                         ("email", "email"), ("city_of_birth", "birth_city")):
        put(k + mine, v(f"applicant.{theirs}"), "the client in the case")
    for mine, theirs in (("ethnicity", "ethnicity"), ("race", "race"), ("height", "height"), ("weight_lbs", "weight_lbs"), ("eye_color", "eye_color"), ("hair_color", "hair_color")):
        put(k + mine, v(f"applicant.{theirs}"), "the client in the case")
    put(k + "relationship", "the client (applying for themself)", "the client files for themself")
    put(k + "marital_status", v("applicant.marital_status") if v("applicant.marital_status") in MARITAL else None, "the client's marital status")


def _track_exemption(graph) -> str | None:
    """The G-1055 fee exemption the client's own case shows: a VAWA self-petition, T or U status, or Special Immigrant Juvenile classification."""
    import journey
    import t_visa
    from filing_questions import sij

    if value(graph, "vawa.classification"):
        return EXEMPT[5]
    if t_visa.seeking(graph):
        return EXEMPT[3]
    if any(n["form"] == "I-918" for n in journey.notices(graph)) or any(k.startswith("uvisa.") and value(graph, k) for k in graph.all_facts()):
        return EXEMPT[4]
    if sij(graph):
        return EXEMPT[2]
    return None


def derive(graph, today: date):
    put = putter(graph, "parole.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    put("parole.type", TYPE, "the only parole request built here: Part 1, item 7")
    if _who(graph) == SELF:
        put("parole.count", "1", "the client files for themself")
        _put_person(graph, put, 1)
    addresses(graph, put, "parole")  # Part 2, 3-4: the client's own addresses
    put("parole.mailing_country", "USA" if v("parole.mailing_street") else None, "a U.S. address")
    put("parole.physical_country", "USA" if v("parole.physical_street") else None, "a U.S. address")
    for n in people(graph):  # for someone else the exemption turns on THAT person: only the attorney sets it. For the client themself, the case's own track says
        track = _track_exemption(graph) if _who(graph) == SELF else None
        put(f"parole.b{n}_exemption", track or NO_EXEMPTION, f"the case is on a track the fee exemptions name ({track})" if track else
            "none of the fee exemptions is on the case: change it if the person is one of them")
        put(f"parole.b{n}_in_proceedings", "Yes" if (n == 1 and _who(graph) == SELF and v("applicant.nta_present")) else None, "a Notice to Appear is in the case")
    if v("parole.sponsor_is") == SUPPORTER_IS[0]:  # the client is the supporter: the case already holds the facts
        for mine, theirs in (("family_name", "family_name"), ("given_name", "given_name"), ("middle_name", "middle_name"), ("dob", "dob"),
                             ("birth_city", "birth_city"), ("birth_country", "country_of_birth"), ("a_number", "a_number"), ("phone", "daytime_phone"),
                             ("mobile", "mobile_phone"), ("email", "email")):
            put(f"parole.sponsor_{mine}", v(f"applicant.{theirs}"), "the client in the case, who is the supporter")
        different = v("applicant.mailing_same_as_physical") == "No"
        for part in ("street", "apt", "city", "state", "zip"):
            put(f"parole.sponsor_mailing_{part}", v(f"applicant.mailing_{part}") if different else v(f"applicant.physical_{part}"),
                "the client's mailing address" if different else "the client's home address")
            if different:
                put(f"parole.sponsor_physical_{part}", v(f"applicant.physical_{part}"), "the client's home address")
        put("parole.sponsor_mailing_same", "No" if different else "Yes", "the client's addresses")
        put("parole.sponsor_mailing_country", "USA", "the client lives in the U.S.")
        put("parole.sponsor_physical_country", "USA" if different else None, "the client lives in the U.S.")
    put("parole.sponsor_mailing_country", "USA" if v("parole.sponsor_mailing_street") else None, "a U.S. address")
    put("parole.sponsor_assets_total", _total(graph), "the three assets added up")
    put("companion.preparer_full_name", " ".join(x for x in (v("firm.preparer_given_name"), v("firm.preparer_family_name")) if x) or None, "the firm")
    return graph


def _total(graph) -> str | None:
    amounts = []
    for n in (1, 2, 3):
        raw = str(value(graph, f"parole.sponsor_asset{n}_amount") or "").replace(",", "").replace("$", "").strip()
        try:
            amounts.append(float(raw)) if raw else None
        except ValueError:
            return None
    return f"{int(sum(amounts)):d}" if amounts else None


def case_schema(schema: dict[str, Any], graph, today: date) -> dict[str, Any]:
    """One G-28, one I-131 and one I-134 for each person the parole is for ('You must file a separate Form I-134 for each beneficiary': uscis.gov/i-134)."""
    forms = [f for n in (people(graph) or [1]) for f in (f"g28_parole_{n}", f"i131_parole_{n}", f"i134_{n}")]
    return schema | {"forms": forms}


def person_graph(graph, base: str, n: int):
    """Person n's forms (src/packet.py fills one set per person): the case, with that person's answers under the forms' own keys."""
    from factgraph import FactGraph

    g = FactGraph.from_dict(graph.to_dict())
    v = lambda k: value(graph, f"parole.b{n}_{k}")  # noqa: E731
    own = _who(graph) == SELF
    values: dict[str, Any] = {"parole.number": str(n), "parole.total": str(len(people(graph)) or 1), "parole.i134_basis": "Another individual who is the beneficiary"}
    for mine, key in (("family_name", "family_name"), ("given_name", "given_name"), ("middle_name", "middle_name"), ("dob", "dob"), ("sex", "sex"), ("a_number", "a_number"),
                      ("marital_status", "marital_status"), ("citizenship", "citizenship")):
        values[f"parole.ben.{mine}"] = v(key)
    values |= {"parole.ben.birth_city": v("city_of_birth"), "parole.ben.birth_country": v("country_of_birth"),
               "parole.ben.i134_mailing_street": v("street"), "parole.ben.i134_mailing_city": v("city"), "parole.ben.i134_mailing_country": v("country"),
               "parole.ben.i134_mailing_province": v("province"), "parole.ben.i134_mailing_postal_code": v("postal_code"), "parole.ben.i134_mailing_same": "Yes",
               "parole.bio_in_proceedings": v("in_proceedings"), "parole.bio.ethnicity": v("ethnicity"), "parole.bio.race": v("race"),
               "parole.bio.height": v("height"), "parole.bio.weight_lbs": v("weight_lbs"), "parole.bio.eye_color": v("eye_color"),
               "parole.bio.hair_color": v("hair_color")}
    for key in ("prior_reentry_or_rtd", "prior_reentry_date", "prior_reentry_disposition", "prior_advance_parole", "prior_ap_date", "prior_ap_disposition"):
        values[f"parole.bio_{key}"] = v(key)
    arrival, until = value(graph, "parole.arrival_date"), value(graph, "parole.stay_until")
    values |= {"parole.ben.stay_from": arrival, "parole.ben.stay_to": until, "parole.ben.stay_end": "Date" if until else "No"}
    if not own:  # Part 2, items 16-27: only when someone else files; the client's own Part 2 holds items 1-15
        values |= {"parole.them.family_name": v("family_name"), "parole.them.given_name": v("given_name"), "parole.them.middle_name": v("middle_name"),
                   "parole.them.dob": v("dob"), "parole.them.country_of_birth": v("country_of_birth"), "parole.them.citizenship": v("citizenship"),
                   "parole.them.phone": v("phone"), "parole.them.email": v("email"), "parole.them.a_number": v("a_number"),
                   "parole.them.mailing_street": v("street"), "parole.them.mailing_city": v("city"), "parole.them.mailing_province": v("province"),
                   "parole.them.mailing_postal_code": v("postal_code"), "parole.them.mailing_country": v("country")}
    for key, val in values.items():
        if val not in (None, "") and value(graph, key) is None:
            g.add_source(key, f"person {n}", "derived", str(val), val, 1.0)
    return g


# -- fees, address ------------------------------------------------------------------------------------

def fee(graph, today: date, n: int = 1) -> tuple[int | None, str]:
    """(the I-131 fee for person n, why): Form G-1055 (10/01/26), Appendix B, item 7: $630 paper; $0 for the General Fee Exemptions."""
    import fees

    exempt = value(graph, f"parole.b{n}_exemption")
    if exempt and exempt != NO_EXEMPTION:
        return 0, f"no fee: {exempt[0].lower() + exempt[1:]} (Form G-1055, I-131 General Fee Exemptions)"
    return (fees.load(today).get("paper") or {}).get("i131_parole"), "Form I-131, initial parole for someone outside the U.S., paper filing (Form G-1055)"


def payments(graph, today: date, forms: list[str]) -> list[tuple[str, str, Any, str]]:
    """(form id, form, amount, what): one payment for each person's I-131 (src/payment.py); the I-134 has no fee."""
    out = []
    for n in people(graph):
        amount, _why = fee(graph, today, n)
        name = " ".join(x for x in (value(graph, f"parole.b{n}_given_name"), value(graph, f"parole.b{n}_family_name")) if x) or f"person {n}"
        out.append((f"i131_parole_{n}", "I-131", amount, f"Form I-131 filing fee for {name}"))
    return out


def address(graph) -> tuple[list[str], str]:
    return list(MAIL_TO), f"the USCIS Dallas Lockbox, whatever the state ({SOURCE}: Part 1, Item 7)"


# -- notes, problems, letter ----------------------------------------------------------------------------

def notes(graph, today: date) -> list[dict[str, str]]:
    import fees

    data = fees.load(today)
    out = []
    people_ = people(graph) or [1]
    amounts = [fee(graph, today, n) for n in people_]
    total = sum(a for a, _ in amounts if isinstance(a, int))
    if len({a for a, _ in amounts}) == 1:
        per = f"{money(amounts[0][0])} for each person (Form G-1055)"
    else:  # one is exempt: say whose fee is what, and why
        per = "; ".join(f"person {n}: {money(a)} ({why})" for n, (a, why) in zip(people_, amounts))
    base = (fees.load(date(2000, 1, 1)).get("pl_119_21") or {}).get("immigration_parole_poe")
    later = next((c for c in data.get("scheduled") or [] if c["fee"] == "pl_119_21.immigration_parole_poe"), None)
    cbp = (f"{money(base)} for a person who enters before {_us(later['effective'])} and {money(later['amount'])} from then on (USCIS alert of 09/30/2026)" if later else
           f"{money((data.get('pl_119_21') or {}).get('immigration_parole_poe'))} (USCIS alert of 09/30/2026)")
    out.append({"level": "info", "title": "Fee", "text": f"{per}, each paid by its own card payment; {len(people_)} "
                f"{'person' if len(people_) == 1 else 'people'}: {money(total)} in all. The I-134 is $0. Online it is {money((data.get('online') or {}).get('i131_parole'))} each, "
                f"unless a fee waiver is asked for. CBP charges the Pub. L. 119-21 Immigration Parole Fee at the port of entry, not with the filing: {cbp}. "
                "It turns on the date the person enters, and it is not waivable."})
    lines, why = address(graph)
    out.append({"level": "info", "title": "Where it is filed", "text": " / ".join(lines) + f" (couriers: {' / '.join(COURIER)}; {why}). Or online, in a USCIS online account "
                "(not when a fee waiver is asked for): the humanitarian parole page."})
    out.append({"level": "info", "title": "What USCIS decides", "text": "Parole is discretionary and case by case, for urgent humanitarian reasons or significant public benefit; "
                "it is usually for no more than a year and is not meant to avoid normal visa processing. USCIS is receiving a very high number of requests and warns of delays; "
                "a particularly urgent case can ask for expedited processing: EXPEDITE in black ink at the top right of a paper I-131, with the reason, contact details and "
                "any evidence (the humanitarian parole page)."})
    out.append({"level": "info", "title": "The financial supporter", "text": "USCIS weighs the supporter's means against the HHS poverty guidelines. 'Lack of evidence of financial "
                "support while in the United States is a strong negative factor that may lead us to deny parole.' Several supporters may share it, each with their own I-134 and "
                "evidence; this system builds one supporter's I-134 for each person (the page: 'Requirements for Financial Supporters')."})
    out.append({"level": "info", "title": "Not built here", "text": "Parole under a specific program (the Filipino World War II Veterans Parole Program, the Immigrant Military Members and "
                "Veterans Initiative, the Family Reunification Task Force), re-parole from inside the U.S., and parole in place."})
    for n in people_:
        sex_dob = _d(value(graph, f"parole.b{n}_dob"))
        if sex_dob and 14 <= (today.year - sex_dob.year - ((today.month, today.day) < (sex_dob.month, sex_dob.day))) <= 79:
            out.append({"level": "info", "title": f"Person {n}: biometrics", "text": "Between 14 and 79: biometrics are taken at a U.S. embassy or consulate, or USCIS "
                        "or the State Department says where (I-131 Instructions, Biometrics Service Requirement)."})
        if value(graph, f"parole.b{n}_in_proceedings") == "Yes":
            out.append({"level": "warn", "title": f"Person {n}: proceedings", "text": "A person outside the U.S. who was ordered excluded, deported or removed: USCIS sends the "
                        "I-131 to ICE to decide if necessary (I-131 Instructions). The attorney reviews."})
    return out


@producer(CLIENT)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    n_people = people(graph)
    if _who(graph) == OTHER and not n_people:
        out.append("How many people the request is for (one I-131 and one I-134 for each).")
    if _who(graph) == OTHER and not (has_doc(client_dir, "us_passport", "citizenship_certificate", "green_card", "passport", "drivers_license", "national_id")):
        out.append("The petitioner's own identity document and proof of U.S. citizenship or status: none in the folder.")
    if not has_doc(client_dir, "passport", "national_id", "birth_certificate"):
        out.append("A copy of the biographical page of each beneficiary's passport (or an explanation why none is available and another government ID showing citizenship): none in the folder.")
    sponsor_docs = has_doc(client_dir, "us_passport", "citizenship_certificate", "green_card", "passport", "drivers_license", "national_id", "work_permit")
    if not sponsor_docs:
        out.append("The supporter's identity document and proof of U.S. citizenship or status (a U.S. passport, green card, naturalization certificate or birth certificate): none in the folder.")
    if not has_doc(client_dir, "tax_return", "pay_stub", "w2", "bank_statement"):
        out.append("Evidence of the supporter's income and assets (the last tax return, pay stubs, an employer's letter, bank statements): none in the folder. "
                   "USCIS calls missing evidence of support 'a strong negative factor'.")
    income = str(v("parole.sponsor_income") or "").replace(",", "").replace("$", "").strip()
    if income and not income.replace(".", "", 1).isdigit():
        out.append(held(OFFICE, "The supporter's annual income (Part 2, 16): numbers only (e.g. 42000)."))
    for n in n_people:
        dob = _d(v(f"parole.b{n}_dob"))
        if dob and dob > today:
            out.append(held(OFFICE, f"Person {n}: the date of birth is in the future."))
        if v(f"parole.b{n}_exemption") not in (None, NO_EXEMPTION) and v("parole.for_whom") == OTHER:
            out.append(held(ATTORNEY, f"Person {n}: a fee exemption when filing for someone else applies only if THAT person qualifies (Form G-1055): the attorney confirms it with the evidence."))
    if v("parole.sponsor_contributions") == "Yes" and not v("parole.sponsor_contributions_text"):
        out.append("I-134, Part 2, 19: describe the specific contributions, and the address where the person will live (the supporter said Yes to 18).")
    if v("parole.sponsor_employment") == "Employed" and not (v("parole.sponsor_job") and v("parole.sponsor_employer")):
        out.append("I-134, Part 2, 12: the supporter is employed: the job and the employer's name.")
    if v("parole.sponsor_status") == "Other" and not v("parole.sponsor_status_other"):
        out.append("I-134, Part 2, 10: explain the supporter's 'Other' immigration status.")
    for n in n_people:
        amount, why = fee(graph, today, n)
        if amount is None:
            out.append(held(OFFICE, f"Person {n}: the I-131 fee can't be set yet: {why}."))
    return out


def letter(graph, today: date) -> dict[str, Any]:
    import fees

    edition = fees.load(today).get("edition") or "current"
    people_ = people(graph) or [1]
    names = {n: " ".join(x for x in (value(graph, f"parole.b{n}_given_name"), value(graph, f"parole.b{n}_family_name")) if x) or f"person {n}" for n in people_}
    forms = {}
    for n in people_:
        forms[f"g28_parole_{n}"] = f"Petitioner’s G-28 - Notice of Entry of Appearance as Attorney or Accredited Representative ({names[n]})"
        forms[f"i131_parole_{n}"] = f"Petitioner’s I-131 - Request for Humanitarian Parole for {names[n]}"
        forms[f"i134_{n}"] = f"Supporter’s I-134 - Declaration of Financial Support for {names[n]}"
    paid = [(n, *fee(graph, today, n)) for n in people_]
    due = [f"{money(a)} for {names[n]}" for n, a, _why in paid if a]
    exempt = [f"no fee is due for {names[n]}: {why.removeprefix('no fee: ')}" for n, a, why in paid if a == 0]
    text = (f"Enclosed are the filing fees of Form I-131 ({'; '.join(due)}), each paid by its own enclosed Form G-1450, per Form G-1055, edition {edition}." if due else
            "No filing fee is due for this application: each person is exempt (Form G-1055).")
    if exempt and due:
        joined = "; ".join(exempt)
        text += f" {joined[0].upper()}{joined[1:]}."
    elif exempt:
        text = "No filing fee is due for this application: " + "; ".join(exempt) + "."
    return {"re_lines": ["Request: I-131 Request for Humanitarian Parole (INA 212(d)(5)(A)), initial parole for persons outside the United States",
                         "Beneficiaries: " + "; ".join(names.values())],
            "mail_to": MAIL_TO, "fees": text, "no_payment": not due, "forms": forms, "form_order": list(forms)}
