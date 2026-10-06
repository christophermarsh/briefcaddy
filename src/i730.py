"""Form I-730, Refugee/Asylee Relative Petition (edition 01/20/25; uscis.gov/i-730, updated 09/09/2026): a principal asylee or
refugee brings their spouse and unmarried children under 21 -- abroad, or in the U.S. without the asylee's status:

  Who: the PRINCIPAL asylee or refugee, not a spouse or child who derived the status (I-730 Instructions, "Who May Not File";
    8 CFR 207.7(d)). One petition for each relative (8 CFR 208.21(c)-(d), 207.7(d)).
  When: within 2 years of the grant of asylum (8 CFR 208.21(c)-(d)) or of the admission as a refugee (8 CFR 207.7(d)), "unless
    USCIS determines that the filing period should be extended for humanitarian reasons": explained in Part 3 (I-730 Instructions,
    Note 1). eCFR as of 09/30/2026.
  The relationship: it must have existed on the day asylum was granted (a refugee: before admission) and still exist; a child
    conceived but not yet born then counts; that child's mother only if married to the client then (208.21(b), 207.7(c)). A child
    born in the U.S. is a citizen: no I-730.
  Fee: none (Form G-1055 10/01/26: I-730, general filing, $0; 8 CFR 207.7(d): "There is no fee for this benefit request").
  Where: by mail, USCIS, Attn: I-730, P.O. Box 20018, Phoenix, AZ 85036-0018; couriers to Attn: I-730 (Box 20018), 2108 E. Elliot
    Rd., Tempe, AZ 85284-1806 (uscis.gov/i-730, "Where to File", read 10/02/2026).
  A refugee's relative: USCIS asks for a Form I-590 with the I-730 (the I-730 page, "Following-to-Join Refugee Petitions Only");
    not filled here.

The deadline goes on the case's timeline from the asylee stage (src/journey.py), counted as the day before the 2nd anniversary of
the grant: the conservative reading of "within two years" (docs/decisions.md). DRAFT for the attorney.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from filing_questions import DATE, LINES, TEXT, YES_NO, money, plus_years, putter, us, value
from filing_questions import iso as _d
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

TITLE = "Relative petition for a spouse or child (I-730)"
MAIL_TO = ["USCIS", "Attn: I-730", "P.O. Box 20018", "Phoenix, AZ 85036-0018"]
MAX_RELATIVES = 4
ASYLEE, REFUGEE = "Asylee", "Refugee"
LPR_ASYLEE, LPR_REFUGEE = "Permanent resident, previously an asylee", "Permanent resident, previously a refugee"
SPOUSE = "Spouse"
CHILDREN = ["Unmarried child: biological", "Unmarried child: stepchild", "Unmarried child: adopted"]
IN_US, ABROAD = "In the United States", "Outside the United States"


def _relative(n: int) -> list[tuple[str, str, dict[str, Any], bool]]:
    who = f"Relative {n}"
    return [
        (f"i730.r{n}_relationship", f"{who}: the client's (page 1)", {"type": "choice", "options": [SPOUSE, *CHILDREN]}, True),
        (f"i730.r{n}_family_name", f"{who}: family name (Part 2, 1)", TEXT, True),
        (f"i730.r{n}_given_name", f"{who}: given name", TEXT, True),
        (f"i730.r{n}_middle_name", f"{who}: middle name", TEXT, False),
        (f"i730.r{n}_dob", f"{who}: date of birth (Part 2, 6)", DATE, True),
        (f"i730.r{n}_sex", f"{who}: sex (Part 2, 5)", {"type": "choice", "options": ["M", "F"]}, True),
        (f"i730.r{n}_country_of_birth", f"{who}: country of birth (Part 2, 7)", TEXT, True),
        (f"i730.r{n}_citizenship", f"{who}: country of citizenship (Part 2, 8)", TEXT, True),
        (f"i730.r{n}_a_number", f"{who}: A-Number, if any (Part 2, 9)", TEXT, False),
        (f"i730.r{n}_location", f"{who}: where they are now (Part 2, 21)", {"type": "choice", "options": [IN_US, ABROAD]}, True),
        (f"i730.r{n}_street", f"{who}: where they live: street (Part 2, 2)", TEXT, True),
        (f"i730.r{n}_city", f"{who}: where they live: city or town", TEXT, True),
        (f"i730.r{n}_region", f"{who}: where they live: state (in the U.S.) or province", TEXT, False),
        (f"i730.r{n}_postal_code", f"{who}: where they live: ZIP or postal code", TEXT, False),
        (f"i730.r{n}_country", f"{who}: where they live: country", TEXT, True),
        (f"i730.r{n}_consulate", f"{who}: if outside the U.S., the U.S. embassy or consulate where they will apply for travel (city, country)", TEXT, False),
        (f"i730.r{n}_native_language", f"{who}: native language (Part 2, 24)", TEXT, False),
    ]


def relatives(graph) -> list[int]:
    """The relatives this packet petitions for: one I-730 each (1..n, from the count the attorney set)."""
    count = str(value(graph, "i730.count") or "")
    return list(range(1, min(int(count), MAX_RELATIVES) + 1)) if count.isdigit() else []


SECTIONS = [
    ("The client's status (Part 1)", "the attorney", [
        ("i730.status", "My status (page 1): the client is", {"type": "choice", "options": [ASYLEE, REFUGEE, LPR_ASYLEE, LPR_REFUGEE]}, True),
        ("i730.principal", "The client was the principal applicant (not a spouse or child who received the status with them)?", YES_NO, True),
        ("asylee.granted_on", "Part 1, 21: the date asylum was granted", DATE, False),
        ("i730.granted_city", "Part 1, 22: where asylum was granted: city (the asylum office or the court)", TEXT, False),
        ("i730.granted_state", "Part 1, 22: state", TEXT, False),
        ("asylee.refugee_admitted_on", "Part 1, 25: the date admitted to the U.S. as a refugee", DATE, False),
        ("i730.admitted_city", "Part 1, 26: where admitted as a refugee: city", TEXT, False),
        ("i730.admitted_state", "Part 1, 26: state", TEXT, False),
        ("i730.late_reason", "Part 3: more than 2 years after the grant or admission: why it is late (USCIS may extend the period for humanitarian "
                             "reasons)", LINES, False),
        ("i730.count", "How many relatives (one Form I-730 each)?", {"type": "choice", "options": [str(n) for n in range(1, MAX_RELATIVES + 1)]}, True),
    ]),
    *[(f"Relative {n}", "the attorney", _relative(n), (lambda n: lambda g: n in relatives(g))(n)) for n in range(1, MAX_RELATIVES + 1)],
]
MORE_QUESTIONS = "One set of questions for each relative, as many as the number of relatives set below."


def start(graph) -> tuple[date | None, str]:
    """(the day the 2 years run from, what it is): the grant of asylum, or the admission as a refugee."""
    if value(graph, "i730.status") in (REFUGEE, LPR_REFUGEE) or value(graph, "applicant.filing_category") == "Refugee (INA 207)":
        return _d(value(graph, "asylee.refugee_admitted_on")), "the admission as a refugee (8 CFR 207.7(d))"
    return _d(value(graph, "asylee.granted_on")), "the grant of asylum (8 CFR 208.21(d))"


def last_day(granted: date | None) -> date | None:
    """The last day to file: the day before the 2nd anniversary of the grant (or the admission) -- "within two years" read conservatively."""
    return plus_years(granted, 2) - timedelta(days=1) if granted else None


def derive(graph, today: date):
    import journey

    put = putter(graph, "i730.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    category = v("applicant.filing_category")
    resident = any(n["form"] == "I-485" and n["kind"] == "approval" for n in journey.notices(graph))
    if category == "Asylee (INA 208)":
        put("i730.status", LPR_ASYLEE if resident else ASYLEE, "the case's grant of asylum" + (" and the I-485 approval" if resident else ""))
    elif category == "Refugee (INA 207)":
        put("i730.status", LPR_REFUGEE if resident else REFUGEE, "admitted as a refugee" + ("; the I-485 approval" if resident else ""))
    for n in relatives(graph):  # the client's spouse, as the case already holds them
        if v(f"i730.r{n}_relationship") == SPOUSE:
            for mine, theirs in (("family_name", "spouse_family_name"), ("given_name", "spouse_given_name"), ("dob", "spouse_dob"),
                                 ("country_of_birth", "spouse_country_of_birth")):
                put(f"i730.r{n}_{mine}", v(f"applicant.{theirs}"), "the client's spouse in the case")
    spouse = next((n for n in relatives(graph) if v(f"i730.r{n}_relationship") == SPOUSE), None)
    put("i730.spouse_family_name", v(f"i730.r{spouse}_family_name") if spouse else v("applicant.spouse_family_name"), "the client's spouse")
    put("i730.spouse_given_name", v(f"i730.r{spouse}_given_name") if spouse else v("applicant.spouse_given_name"), "the client's spouse")
    put("i730.marriage_date", v("applicant.marriage_date"), "the client's marriage")
    if v("applicant.mailing_same_as_physical") == "No":  # Part 1, 3: the mailing address only when it differs from the home
        for part in ("in_care_of", "street", "apt", "city", "state", "zip"):
            put(f"i730.mailing_{part}", v(f"applicant.mailing_{part}"), "the client's mailing address")
    if v("applicant.physical_state"):
        put("i730.petitioner_country", "USA", "the client lives in the U.S.")
    begun, _what = start(graph)
    if begun:
        put("i730.late", "Yes" if today > last_day(begun) else "No", f"2 years from {us(begun)}")
    preparer = " ".join(x for x in (v("firm.preparer_given_name"), v("firm.preparer_family_name")) if x)
    put("companion.preparer_full_name", preparer or None, "the firm")
    return graph


def case_schema(schema: dict[str, Any], graph, today: date) -> dict[str, Any]:
    """One G-28 and one I-730 for each relative (8 CFR 208.21(c)-(d): "a separate Request ... for each qualifying family member"); the
    hand work for each relative who signs too or needs a Form I-590."""
    people = relatives(graph) or [1]
    forms = [f for n in people for f in (f"g28_i730_{n}", f"i730_{n}")]
    handwork = list(schema.get("handwork") or [])
    for n in relatives(graph):
        age = _age(value(graph, f"i730.r{n}_dob"), today)
        if value(graph, f"i730.r{n}_location") != IN_US:
            continue
        handwork.append({"kind": "attach", "text": f"I-730 ({n}): a copy of both sides of the relative's Form I-94, if any (I-730 Instructions)."})
        if age is None or age >= 14:
            handwork.append({"kind": "sign", "text": f"I-730 ({n}): the relative is in the U.S. and 14 or older: they read the petition and sign Part 6 "
                                                     "too (I-730 Instructions)."})
    if value(graph, "i730.status") in (REFUGEE, LPR_REFUGEE):
        handwork.append({"kind": "attach", "text": "A refugee's relative: USCIS asks for a Form I-590 for each relative with the I-730 (parts 5 and 8 "
                                                   "and the signature left blank): prepared by hand (uscis.gov/i-730)."})
    return schema | {"forms": forms, "handwork": handwork}


def person_graph(graph, base: str, n: int):
    """Relative n's I-730 (src/packet.py fills one per relative): the case, with that relative's answers under the form's own keys."""
    from factgraph import FactGraph

    g = FactGraph.from_dict(graph.to_dict())
    if base != "i730":
        return g
    v = lambda k: value(graph, f"i730.r{n}_{k}")  # noqa: E731
    abroad = v("location") == ABROAD or str(v("country") or "").upper() not in ("", "USA", "UNITED STATES")
    spouse = v("relationship") == SPOUSE
    values = {f"i730.ben.{k}": v(k) for k in ("family_name", "given_name", "middle_name", "dob", "sex", "country_of_birth", "citizenship",
                                              "a_number", "street", "city", "country", "consulate", "native_language", "relationship", "location")}
    values |= {"i730.ben.province" if abroad else "i730.ben.state": v("region"), "i730.ben.postal_code" if abroad else "i730.ben.zip": v("postal_code"),
               "i730.number": str(n), "i730.total": str(len(relatives(graph)) or 1)}
    if spouse:  # Part 2, 12-13: a spouse's current spouse is the client
        values |= {"i730.ben.spouse_family_name": value(graph, "applicant.family_name"), "i730.ben.spouse_given_name": value(graph, "applicant.given_name"),
                   "i730.ben.marriage_date": value(graph, "applicant.marriage_date")}
    for key, val in values.items():
        if val not in (None, ""):
            g.add_source(key, f"relative {n}", "derived", str(val), val, 1.0)
    return g


def _age(dob: Any, on: date) -> int | None:
    born = _d(dob)
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day)) if born else None


def fee(graph, today: date) -> tuple[int | None, str]:
    import fees

    return (fees.load(today).get("paper") or {}).get("i730"), "no fee for an I-730 (Form G-1055)"


def notes(graph, today: date) -> list[dict[str, str]]:
    begun, what = start(graph)
    last = last_day(begun)
    out = [{"level": "info" if not last or today <= last else "warn", "title": "When",
            "text": (f"USCIS must receive each I-730 by {us(last)}: within 2 years of {what}, counted to the day before the 2nd anniversary. "
                     "Later only if USCIS extends the period for humanitarian reasons, explained in Part 3." if last else
                     "Within 2 years of the grant of asylum or the admission as a refugee (8 CFR 208.21(d), 207.7(d)): enter the date.")
            + (" That day has passed." if last and today > last else "")}]
    amount, why = fee(graph, today)
    out.append({"level": "info", "title": "Fee and where", "text": f"{money(amount)}: {why}. Mailed to " + " / ".join(MAIL_TO) + " (uscis.gov/i-730)."})
    out.append({"level": "info", "title": "One for each relative",
                "text": "A spouse and each unmarried child under 21, the relationship existing on the day of the grant (a child then conceived counts) "
                        "and still existing (8 CFR 208.21(b), 207.7(c)). A child born in the U.S. is a citizen: no I-730."})
    for n in relatives(graph):
        age = _age(value(graph, f"i730.r{n}_dob"), today)
        if value(graph, f"i730.r{n}_relationship") in CHILDREN and age is not None and age >= 21:
            out.append({"level": "warn", "title": f"Relative {n}",
                        "text": "21 or older: a child stays a child only if under 21 when the I-589 was filed (the asylum application) or, for a "
                                "refugee, at the first USCIS interview, and listed on it (I-730 Instructions). The attorney confirms."})
        born = _d(value(graph, f"i730.r{n}_dob"))
        if value(graph, f"i730.r{n}_relationship") in CHILDREN and born and begun and born > begun:
            out.append({"level": "warn", "title": f"Relative {n}",
                        "text": f"Born {us(born)}, after the grant: eligible only if conceived before it (8 CFR 208.21(b), 207.7(c)). The attorney confirms."})
    return out


@producer(CLIENT)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    out = []
    status = value(graph, "i730.status")
    if value(graph, "i730.principal") == "No":
        out.append(held(ATTORNEY, "A spouse or child who received asylum or refugee status with the principal can't file an I-730 (I-730 Instructions, 'Who May Not "
                   "File'; 8 CFR 207.7(d))."))
    begun, _what = start(graph)
    if status and not begun:
        out.append("The date asylum was granted (Part 1, 21)." if status in (ASYLEE, LPR_ASYLEE) else "The date admitted as a refugee (Part 1, 25).")
    if status in (ASYLEE, LPR_ASYLEE) and not (value(graph, "i730.granted_city") and value(graph, "i730.granted_state")):
        out.append("Where asylum was granted (Part 1, 22): the city and state.")
    if begun and today > last_day(begun) and not value(graph, "i730.late_reason"):
        out.append(f"More than 2 years since {us(begun)}: explain in Part 3 why the I-730 is late. USCIS may extend the period for humanitarian "
                   "reasons (8 CFR 208.21(d), 207.7(d)).")
    married = _d(value(graph, "applicant.marriage_date"))
    for n in relatives(graph):
        rel = value(graph, f"i730.r{n}_relationship")
        if rel == SPOUSE and married and begun and married > begun:
            out.append(held(ATTORNEY, f"Relative {n}: married {us(married)}, after the grant on {us(begun)}: the marriage must have existed then "
                       "(8 CFR 208.21(b), 207.7(c)). Another path (Form I-130) once the client is a resident or citizen: ask the attorney."))
        if value(graph, f"i730.r{n}_location") == ABROAD and not value(graph, f"i730.r{n}_consulate"):
            out.append(f"Relative {n} is outside the U.S.: the U.S. embassy or consulate where they will apply for travel (Part 2, 21).")
    if sum(1 for n in relatives(graph) if value(graph, f"i730.r{n}_relationship") == SPOUSE) > 1:
        out.append(held(OFFICE, "More than one spouse: only one I-730 for a spouse."))
    return out


def letter(graph, today: date) -> dict[str, Any]:
    import fees

    edition = fees.load(today).get("edition") or "current"
    people = relatives(graph) or [1]
    names = {n: " ".join(x for x in (value(graph, f"i730.r{n}_given_name"), value(graph, f"i730.r{n}_family_name")) if x) or f"relative {n}"
             for n in people}
    kinds = {n: (value(graph, f"i730.r{n}_relationship") or "relative").split(":")[0] for n in people}
    forms = {}
    for n in people:
        forms[f"g28_i730_{n}"] = f"Petitioner’s G-28 - Notice of Entry of Appearance as Attorney or Accredited Representative ({names[n]})"
        forms[f"i730_{n}"] = f"Petitioner’s I-730 - Refugee/Asylee Relative Petition for {names[n]} ({kinds[n]})"
    return {"re_lines": ["Petition: I-730 Refugee/Asylee Relative Petition", "Beneficiaries: " + "; ".join(names.values())],
            "mail_to": MAIL_TO, "fees": f"No filing fee is required for Form I-730 (Form G-1055, edition {edition}).", "no_payment": True,
            "forms": forms, "form_order": list(forms)}


def document_notes(client_dir: Path, graph) -> list[dict[str, str]]:
    """Each relative's documents in the folder, by the document record's person (src/documents.py): set on the
    Documents page, or read off the document (a child's birth certificate names the client as a parent)."""
    import documents

    return documents.relatives_note(client_dir, [value(graph, f"i730.r{n}_relationship") for n in relatives(graph)])
