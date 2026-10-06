"""The U visa: the victim of a qualifying crime petitions for U nonimmigrant
status on Form I-918 (edition 01/20/25) -- INA 101(a)(15)(U) (8 U.S.C.
1101(a)(15)(U), U.S. Code 2024 edition, govinfo.gov, read 10/02/2026), 8 CFR
214.14 and 212.17 (eCFR as of 09/30/2026), the Form I-918 Instructions and the
Supplement B Instructions (both 01/20/25) and uscis.gov/i-918 (updated
06/05/2026):

  Who (214.14(b); INA 101(a)(15)(U)(i)): the victim of qualifying criminal
    activity -- the list in (U)(iii), or any similar activity in violation of
    criminal law -- who suffered substantial physical or mental abuse from it,
    has information about it, has been, is or is likely to be helpful to its
    investigation or prosecution, and it occurred in the U.S. (Indian country
    and military installations included) or violated U.S. law.
  The certification (214.14(c)(2)(i)): Form I-918 Supplement B "signed by a
    certifying official within the six months immediately preceding the filing
    of Form I-918". The Supplement B Instructions: "valid for six months from
    the date of signature"; the original, never "a photocopy of the signature
    page". The I-918 Instructions: without it "USCIS will deny your Form I-918".
  Initial evidence (214.14(c)(2)(ii)-(iv); the Instructions' "Required Initial
    Evidence" 1-8): the evidence of each element, the signed personal
    statement, and Form I-192 if the petitioner is inadmissible (8 CFR 212.17).
  Family (214.14(f); INA 101(a)(15)(U)(ii)): one Supplement A per qualifying
    family member, with the I-918 or later. Under 21 when filing: the spouse,
    children, parents and unmarried siblings under 18; 21 or older: the spouse
    and children (214.14(a)(10); "unmarried children under 21 years of age",
    I-918 Instructions, Who May File 2). Never a family member who committed
    the crime in a family violence or trafficking context (214.14(f)(1)).
  Where: the lockbox for the client's state, "Attn: 1367"
    (schemas/law/uscis_lockboxes_i918.json). Fees (G-1055 10/01/26): $0 for the
    I-918 and Supplements A and B, $0 for a U petitioner's I-192; the G-1055
    lists no biometric services fee for the I-918.

The request to the agency for the certification is its own filing
(src/u_certification.py); the case path is the "u_visa" track (src/journey.py).
The questions, checks and wording are DRAFT for the attorney.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from filing_questions import DATE, LINES, TEXT, YES_NO, lockbox, money, putter, state_of, us, value
from filing_questions import iso as _d
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

TITLE = "U visa petition (I-918)"
CHART = "uscis_lockboxes_i918"
# INA 101(a)(15)(U)(iii), in the order of the Form I-918 Instructions (01/20/25, page 1) and Supplement B's Part 3
CRIMES = ["Abduction", "Abusive sexual contact", "Attempt to commit any of the named crimes", "Being held hostage", "Blackmail",
          "Conspiracy to commit any of the named crimes", "Domestic violence", "Extortion", "False imprisonment", "Felonious assault",
          "Female genital mutilation", "Fraud in foreign labor contracting", "Incest", "Involuntary servitude", "Kidnapping", "Manslaughter", "Murder",
          "Obstruction of justice", "Peonage", "Perjury", "Prostitution", "Rape", "Sexual assault", "Sexual exploitation", "Slave trade",
          "Solicitation to commit any of the named crimes", "Stalking", "Torture", "Trafficking", "Unlawful criminal restraint", "Witness tampering"]
SIMILAR = "A similar activity in violation of criminal law (name it below)"
# 8 CFR 214.14(c)(2)(i): signed "within the six months immediately preceding the filing"; Supplement B Instructions: "valid for six months from the date of signature"
SUPB_VALID_MONTHS = 6
SAFE = ["In care of the attorney, at the office", "The client's own mailing address", "None: USCIS writes to the home address"]
RELATIONSHIPS = ["Spouse", "Child", "Parent", "Unmarried sibling under 18"]
SLOTS = 3  # Supplement A slots on the packet (i918a_1 .. i918a_3 in schemas/packets/companion_forms.json)
PROCEEDINGS = ["Removal", "Exclusion", "Deportation", "Rescission", "Other judicial proceedings"]


def _member(n: int) -> tuple:
    p = f"uvisa.m{n}_"
    return (f"Family member {n} (Supplement A)", "the attorney", [
        (p + "relationship", "Supplement A, Part 1: the family member is the client's", {"type": "choice", "options": RELATIONSHIPS}, True),
        (p + "family_name", "Part 3, 1.A: family name", TEXT, True),
        (p + "given_name", "Part 3, 1.B: given name", TEXT, True),
        (p + "middle_name", "Part 3, 1.C: middle name", TEXT, False),
        (p + "dob", "Part 3, 8: date of birth", DATE, True),
        (p + "sex", "Part 3, 12: sex", {"type": "choice", "options": ["Female", "Male"]}, True),
        (p + "marital_status", "Part 3, 11: marital status", {"type": "choice", "options": ["Single", "Married", "Divorced", "Widowed"]}, True),
        (p + "country_of_birth", "Part 3, 9: country of birth", TEXT, True),
        (p + "citizenship", "Part 3, 10: country of citizenship or nationality", TEXT, True),
        (p + "a_number", "Part 3, 5: A-Number (if any)", TEXT, False),
        (p + "in_us", "Lives in the United States now?", YES_NO, True),
        (p + "same_address", "Part 3, 3: lives at the client's home address?", YES_NO, False),
        (p + "ead", "Part 4, 8: wants a work permit (Form I-765, filed separately)", YES_NO, False),
        (p + "perpetrator", "Did this family member commit the crime, in a family violence or trafficking context?", YES_NO, True),
    ], lambda g, n=n: _members(g) >= n)


SECTIONS = [
    ("The crime and the certification (Supplement B)", "the attorney", [
        ("uvisa.crime", "The qualifying criminal activity, as the certification names it (INA 101(a)(15)(U)(iii))",
         {"type": "choice", "options": CRIMES + [SIMILAR]}, True),
        ("uvisa.crime_other", "If a similar activity: the offense, as the agency charged or described it", TEXT, False),
        ("uvisa.crime_date", "When it happened (the first date, if more than one)", DATE, True),
        ("uvisa.crime_place", "Where it happened (city and state)", TEXT, True),
        ("uvisa.us_crime", "Part 2, 5: it happened in the United States (Indian country and military installations included) or violated U.S. law", YES_NO, True),
        ("uvisa.p2_abuse", "Part 2, 2: the client suffered substantial physical or mental abuse because of it", YES_NO, True),
        ("uvisa.p2_information", "Part 2, 3: the client has information about the crime", YES_NO, True),
        ("uvisa.agency_name", "The certifying agency (the police department, prosecutor, court or other authority)", TEXT, True),
        ("uvisa.supb_signed", "Supplement B: the date the certifying official signed it (its Part 6, item 2)", DATE, True),
        ("uvisa.supb_original", "The office holds the ORIGINAL signed Supplement B (the official's ink signature, not a copy)", YES_NO, True),
    ]),
    # The personal statement's topics (I-918 Instructions, Required Initial Evidence 7: notes() below), in the client's own
    # words: the Declaration card assembles the statement from these answers, word for word (src/drafting.py).
    ("The client's account, in their own words (for the personal statement)", "the client", [
        ("uvisa.account_crime", "In the client's own words: the crime, when and where it happened, and who was responsible", LINES, False),
        ("uvisa.account_events", "In the client's own words: what happened before and after it", LINES, False),
        ("uvisa.account_investigation", "In the client's own words: how the police or prosecutor came to investigate it, and how the client helped", LINES, False),
        ("uvisa.account_abuse", "In the client's own words: the physical or mental harm they suffered because of it", LINES, False),
    ]),
    ("Where USCIS writes to the client (Part 1, 4)", "the attorney", [
        ("uvisa.safe_mailing", "The safe mailing address", {"type": "choice", "options": SAFE}, True),
    ]),
    ("Immigration proceedings (Part 2, 7)", "the attorney", [
        ("uvisa.in_proceedings", "Part 2, 7.A: was the client ever, or is the client now, in immigration proceedings?", YES_NO, True),
        ("uvisa.proceedings_type", "Part 2, 7.B-7.F: which proceedings", {"type": "choice", "options": PROCEEDINGS}, False),
    ]),
    ("Admissibility (Part 3) and the waiver", "the attorney", [
        ("uvisa.inadmissible", "With the Part 3 answers: is the client inadmissible on any ground? (Form I-192, the waiver, then goes with the petition)", YES_NO, True),
    ]),
    ("The waiver (Form I-192)", "the attorney", [
        # the same I-192 questions as the T visa's (src/t_visa.py): one I-192 map for both
        ("i192.grounds", "Form I-192, item 26: the grounds of inadmissibility that may apply, explained", LINES, True),
        ("i192.prior_request", "I-192, item 27: ever applied before for advance permission to enter as a nonimmigrant?", YES_NO, True),
        ("i192.six_months", "I-192, item 30: ever in the U.S. for six months or more?", YES_NO, True),
        ("i192.prior_applications", "I-192, item 31: ever filed (or had filed for them) an application or petition for immigration benefits?", YES_NO, True),
        ("i192.denied", "I-192, item 35: ever denied an immigration benefit, or had one revoked?", YES_NO, True),
        ("i192.arrested", "I-192, item 36: ever arrested, cited, charged, fined, convicted or imprisoned (not minor traffic)?", YES_NO, True),
    ], lambda g: value(g, "uvisa.inadmissible") == "Yes"),
    ("Family members (Supplement A, one each)", "the attorney", [
        ("uvisa.members", "Qualifying family members petitioned for with this petition", {"type": "choice", "options": ["None", "1", "2", "3"]}, True),
    ]),
    *[_member(n) for n in range(1, SLOTS + 1)],
    ("After filing", "the paralegal", [
        ("uvisa.bfd_date", "Date of USCIS's bona fide determination notice (work permit and deferred action), if one came", DATE, False),
        ("uvisa.waitlist_date", "Date of USCIS's waiting-list notice (8 CFR 214.14(d)(2)), if one came", DATE, False),
    ]),
]
MORE_QUESTIONS = "The waiver's question appears when the client is inadmissible; each family member's questions when the number of family members says so."


def _members(graph) -> int:
    raw = value(graph, "uvisa.members")
    return int(raw) if str(raw or "").isdigit() else 0


def _plus_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, min(d.day, calendar.monthrange(d.year + y, m + 1)[1]))


def supb_last_day(signed: date) -> date:
    """The last day USCIS can receive the I-918 on a Supplement B signed that day: the last day whose six months back
    still reach past the signature ('within the six months immediately preceding the filing', 8 CFR 214.14(c)(2)(i);
    'valid for six months from the date of signature', Supplement B Instructions). The six-month anniversary itself is
    left out, the cautious reading: signed 04/01 -> 09/30; signed 08/31 -> 02/28 (there is no 02/31)."""
    mark = _plus_months(signed, SUPB_VALID_MONTHS)
    return mark - timedelta(days=1) if mark.day == signed.day else mark


def _age(dob: date | None, on: date) -> int | None:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day)) if dob else None


def _filed(graph) -> bool:
    """The I-918 is already with USCIS (a notice for it in the case)."""
    import journey

    return any(n["form"] == "I-918" and n["kind"] not in ("rejection",) for n in journey.notices(graph))


def mail_to(graph) -> tuple[list[str] | None, str | None]:
    return lockbox(CHART, state_of(graph))


def derive(graph, today: date):
    import journey

    put = putter(graph, "u_visa.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    if v("uvisa.crime"):
        put("uvisa.p2_victim", "Yes", "the qualifying criminal activity named in the case")
    if v("uvisa.supb_signed"):
        put("uvisa.p2_supb", "Yes", "the signed Supplement B recorded in the case")
    age = _age(_d(v("applicant.dob")), today)
    if age is not None:
        put("uvisa.p2_under16", "Yes" if age < 16 else "No", "the client's date of birth")
    members = _members(graph)
    if v("uvisa.members"):
        put("uvisa.p4_family", "Yes" if members else "No", "the family members petitioned for")
    put("uvisa.g28_forms", ", ".join(["I-918"] + (["I-918 Supplement A"] if members else []) + (["I-192"] if v("uvisa.inadmissible") == "Yes" else [])),
        "the forms in the U petition")
    if v("uvisa.inadmissible") == "Yes":
        put("i192.application_type", "T or U nonimmigrant status", "a U petitioner's waiver (Form I-192, Part 1)")
        put("i192.physical_country", "USA" if v("applicant.physical_street") else None, "a U.S. address")
    approved = any(n["form"] == "I-918" and n["kind"] == "approval" for n in journey.notices(graph))
    put("uvisa.i918_status", "Approved" if approved else "Pending", "the I-918's notices in the case")
    safe = v("uvisa.safe_mailing")
    if safe == SAFE[0]:  # the office (the I-918 Instructions name an attorney's address as a safe mailing address)
        put("uvisa.safe_in_care_of", v("firm.business_name"), "the office: the safe mailing address")
        put("i192.mailing_in_care_of", v("firm.business_name"), "the office: the safe mailing address")
        for part in ("street", "city", "state", "zip"):
            put(f"uvisa.safe_{part}", v(f"firm.{part}"), "the office: the safe mailing address")
            put(f"i192.mailing_{part}", v(f"firm.{part}"), "the office: the safe mailing address")
    elif safe == SAFE[1]:
        put("uvisa.safe_in_care_of", v("applicant.mailing_in_care_of"), "the client's mailing address")
        put("i192.mailing_in_care_of", v("applicant.mailing_in_care_of"), "the client's mailing address")
        for part in ("street", "unit_type", "apt", "city", "state", "zip"):
            put(f"uvisa.safe_{part}", v(f"applicant.mailing_{part}"), "the client's mailing address")
            put(f"i192.mailing_{part}", v(f"applicant.mailing_{part}"), "the client's mailing address")
    for n in range(1, members + 1):
        p = f"uvisa.m{n}_"
        if v(p + "same_address") == "Yes":
            for part in ("street", "unit_type", "apt", "city", "state", "zip"):
                put(p + part, v(f"applicant.physical_{part}"), "the client's home address (the family member lives there)")
    return graph


def forms_for(graph, forms: list[str]) -> list[str]:
    """The forms this case files: a Supplement A for each family member petitioned for, the I-192 only when inadmissible."""
    members = _members(graph)
    return [f for f in forms if not (f.startswith("i918a_") and int(f.rsplit("_", 1)[1]) > members)
            and not (f == "i192" and value(graph, "uvisa.inadmissible") != "Yes")]


def fee(graph, today: date) -> tuple[int | None, str]:
    import fees

    paper = fees.load(today).get("paper") or {}
    return paper.get("i918"), "no fee for the I-918, its Supplements A and B, or a U petitioner's I-192 (G-1055)"


def notes(graph, today: date) -> list[dict[str, str]]:
    import fees

    v = lambda k: value(graph, k)  # noqa: E731
    amount, why = fee(graph, today)
    paper = fees.load(today).get("paper") or {}
    lines, box = mail_to(graph)
    where = (" / ".join(lines) + f" (the {box} lockbox for the client's state, uscis.gov/i-918)") if lines else "the lockbox for the client's state (uscis.gov/i-918): the client's state isn't known yet"
    out = [{"level": "info", "title": "Fee and where", "text": f"{money(amount)}: {why}; the I-192 is {money(paper.get('i192_u'))} for a U petitioner. Mailed to {where}."}]
    signed = _d(v("uvisa.supb_signed"))
    if signed:
        last = supb_last_day(signed)
        days = (last - today).days
        level = "warn" if days <= 14 else "info"
        out.append({"level": level, "title": "The certification", "text": f"Supplement B signed {us(signed)}: USCIS must receive the petition by {us(last)} "
                    f"({'in ' + str(days) + ' days' if days >= 0 else 'past'}), six months from the signature (8 CFR 214.14(c)(2)(i))."
                    + (" Send by overnight courier with tracking." if 0 <= days <= 14 else "")})
    else:
        out.append({"level": "info", "title": "The certification", "text": "No signed Supplement B recorded yet. Ask the agency first: More…, U visa: certification request."})
    out.append({"level": "info", "title": "The personal statement", "text": "Signed by the client. It describes the crime, when it happened, who was responsible, "
                "the events around it, how it came to be investigated or prosecuted, and the physical or mental abuse suffered (I-918 Instructions, Required Initial Evidence 7)."})
    out.append({"level": "info", "title": "Work permit while waiting", "text": "USCIS recommends filing Form I-765 with the I-918, for the bona fide determination; "
                "there is no fee for it (uscis.gov/i-918, questions 8 and 10)."})
    if v("uvisa.in_proceedings") == "Yes":
        out.append({"level": "info", "title": "In proceedings", "text": "The I-918 is still filed with USCIS. ICE counsel may agree to a joint motion to terminate "
                    "the proceedings while USCIS decides it (8 CFR 214.14(c)(1)(i))."})
    return out


@producer(ATTORNEY)
def _member_problems(graph, n: int, principal_age: int | None, today: date) -> list[str]:
    """INA 101(a)(15)(U)(ii), 8 CFR 214.14(a)(10) and (f)(1): who can be a qualifying family member, as the petition is filed."""
    p = f"uvisa.m{n}_"
    v = lambda k: value(graph, k)  # noqa: E731
    name = " ".join(x for x in (v(p + "given_name"), v(p + "family_name")) if x) or f"Family member {n}"
    rel, age = v(p + "relationship"), _age(_d(v(p + "dob")), today)
    out = []
    if v(p + "perpetrator") == "Yes":
        out.append(f"{name} committed the crime in a family violence or trafficking context: that family member can't receive derivative U status "
                   "(8 CFR 214.14(f)(1)).")
    if rel in ("Parent", "Unmarried sibling under 18") and principal_age is not None and principal_age >= 21:
        out.append(f"{name}: a {rel.lower().replace(' under 18', '')} qualifies only when the client is under 21 when the petition is filed "
                   "(INA 101(a)(15)(U)(ii)(I)); the client is 21 or older.")
    if rel == "Child" and (v(p + "marital_status") == "Married" or (age is not None and age >= 21)):
        out.append(f"{name}: a child must be unmarried and under 21 (I-918 Instructions, Who May File 2; 8 CFR 214.14(a)(10)).")
    if rel == "Unmarried sibling under 18" and (v(p + "marital_status") == "Married" or (age is not None and age >= 18)):
        out.append(f"{name}: a sibling must be unmarried and under 18 when the client applies (INA 101(a)(15)(U)(ii)(I); 8 CFR 214.14(a)(10)).")
    return out


@producer(CLIENT)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    signed = _d(v("uvisa.supb_signed"))
    if not signed:
        out.append("No signed Supplement B yet: the petition needs the original, signed by a certifying official within the six months before filing "
                   "(8 CFR 214.14(c)(2)(i)); without it USCIS denies the I-918 (I-918 Instructions). Ask the agency: More…, U visa: certification request.")
    elif signed > today:
        out.append(held(OFFICE, f"The Supplement B's signature date ({us(signed)}) is after today: check its Part 6, item 2."))
    elif supb_last_day(signed) < today and not _filed(graph):
        out.append(f"The Supplement B signed {us(signed)} expired after {us(supb_last_day(signed))}: it is valid six months from the signature "
                   "(8 CFR 214.14(c)(2)(i); Supplement B Instructions). Ask the agency to sign a new one.")
    if v("uvisa.supb_original") == "No":
        out.append("USCIS needs the original Supplement B with the official's handwritten signature, not a photocopy of the signature page (Supplement B Instructions).")
    if v("uvisa.crime") == SIMILAR and not v("uvisa.crime_other"):
        out.append("A similar activity: name the offense as the agency charged or described it (INA 101(a)(15)(U)(iii)).")
    if v("uvisa.us_crime") == "No":
        out.append(held(ATTORNEY, "The crime must have occurred in the United States (Indian country and military installations included) or violated U.S. law "
                   "(INA 101(a)(15)(U)(i)(IV); 8 CFR 214.14(b)(4))."))
    if v("uvisa.p2_abuse") == "No":
        out.append(held(ATTORNEY, "The client must have suffered substantial physical or mental abuse from the crime (INA 101(a)(15)(U)(i)(I); 8 CFR 214.14(b)(1))."))
    if v("uvisa.p2_information") == "No":
        out.append(held(ATTORNEY, "The client (or, under 16, a parent, guardian or next friend) must have information about the crime (INA 101(a)(15)(U)(i)(II); 8 CFR 214.14(b)(2))."))
    if v("uvisa.in_proceedings") == "Yes" and not v("uvisa.proceedings_type"):
        out.append("Part 2, 7: say which proceedings (removal, exclusion, deportation, rescission or other).")
    if not mail_to(graph)[0]:
        out.append(held(OFFICE, "The client's state isn't on USCIS's I-918 chart: check 'Where to File' on uscis.gov/i-918 before mailing."))
    if not _filed(graph):  # the ages that decide who qualifies are counted as the petition is filed
        principal = _age(_d(v("applicant.dob")), today)
        for n in range(1, _members(graph) + 1):
            out += _member_problems(graph, n, principal, today)
    return out


def letter(graph, today: date) -> dict[str, Any]:
    import fees

    v = lambda k: value(graph, k)  # noqa: E731
    lines, _box = mail_to(graph)
    edition = fees.load(today).get("edition") or "current"
    signed = _d(v("uvisa.supb_signed"))
    agency = v("uvisa.agency_name") or "[the certifying agency]"
    return {"re_lines": ["Petition: I-918 Petition for U Nonimmigrant Status"]
            + (["with I-918 Supplement A for each qualifying family member"] if _members(graph) else [])
            + (["and I-192 Application for Advance Permission to Enter as a Nonimmigrant"] if v("uvisa.inadmissible") == "Yes" else []),
            "mail_to": lines or ["[the lockbox for the client's state: uscis.gov/i-918]"],
            "always_forms": ["i918b_original"],
            "forms": {**_LETTER_FORMS, "i918b_original": f"Original I-918 Supplement B - U Nonimmigrant Status Certification, signed by {agency}"
                                                          f"{' on ' + us(signed) if signed else ''}"},
            "fees": f"No filing fee is required: Form G-1055 (edition {edition}) lists $0 for Form I-918, Supplement A and Supplement B, and for Form I-192 "
                    "filed by a petitioner for U nonimmigrant status.",
            "no_payment": True}


_LETTER_FORMS = {"g28_i918": "Petitioner’s G-28 - Notice of Entry of Appearance as Attorney or Accredited Representative",
                 "i918": "Petitioner’s I-918 - Petition for U Nonimmigrant Status",
                 **{f"i918a_{n}": f"I-918 Supplement A - Petition for Qualifying Family Member of U-1 Recipient (family member {n})" for n in range(1, SLOTS + 1)},
                 "i192": "Petitioner’s I-192 - Application for Advance Permission to Enter as a Nonimmigrant"}


def document_notes(client_dir: Path, graph) -> list[dict[str, str]]:
    """Each Supplement A family member's documents in the folder, by the document record's person (src/documents.py)."""
    import documents

    return documents.relatives_note(client_dir, [value(graph, f"uvisa.m{n}_relationship") for n in range(1, _members(graph) + 1)])
