"""Cancellation of removal before an immigration judge: Form EOIR-42B (certain
non-permanent residents) and Form EOIR-42A (certain permanent residents), both
Rev. Feb. 2025 (justice.gov/eoir/eoir-forms, downloaded 10/02/2026). Filed with
the immigration court, never with USCIS. The attorney decides eligibility;
these checks only put the statute's own tests next to the case's dates.

  Who (INA 240A, 8 U.S.C. 1229b; govinfo, United States Code 2024 edition, read
  10/02/2026 -- uscode.house.gov was down for maintenance that day):
    EOIR-42A, 240A(a): a permanent resident for at least 5 years; 7 years of
      continuous residence after being admitted in any status; no aggravated
      felony conviction.
    EOIR-42B, 240A(b)(1): 10 years of continuous physical presence immediately
      before the application; good moral character during them; no conviction
      under 212(a)(2), 237(a)(2) or 237(a)(3) (subject to (b)(5)); exceptional
      and extremely unusual hardship to a U.S. citizen or permanent resident
      spouse, parent or child ("child": unmarried and under 21, INA 101(b)(1)).
      240A(b)(2) (a battered spouse or child) is a different test: the
      attorney's.
    Never (240A(c)): a crewman after 06/30/1964; a J exchange visitor for
      graduate medical training, or one subject to the 2-year rule neither met
      nor waived; 212(a)(3) or 237(a)(4); a persecutor (241(b)(3)(B)(i));
      removal already cancelled, or suspension or 212(c) relief before.
  The clock (240A(d)): residence or presence ends when the Notice to Appear is
    served (not for (b)(2)), or at a 212(a)(2) offense that makes the client
    inadmissible, or removable under 237(a)(2) or (a)(4), whichever is first;
    a trip over 90 days, or trips over 180 in all, break physical presence;
    24 months of honorable active duty, enlisted in the U.S., excuse
    continuity.
  The cap (240A(e)(1); 8 CFR 1240.21, eCFR as of 09/30/2026): 4,000 grants of
    240A(b) cancellation in a fiscal year. Once they are used up the decision
    to grant is reserved to a later year (1240.21(c)(1)); no more conditional
    grants (1240.21(a)(2)); a grant of asylum or adjustment while it is pending
    means a discretionary denial (1240.21(c)(2)).
  Where (8 CFR 1240.20): only with the immigration court that has the record,
    after jurisdiction vested (1003.14), with the fee or a fee waiver request.
  Fees: EOIR-42A $730, EOIR-42B $1,690 (EOIR's forms page, updated 10/01/2026;
    8 CFR 1103.7(b)(4) as amended by 91 FR 54211, FY 2027, effective
    10/01/2026), paid only through the EOIR Payment Portal (1103.7(a)(1)); and
    the DHS biometrics fee, $30 a person (EOIR's forms page; Form G-1055
    10/01/26), paid on pay.gov. The judge can't waive the biometrics fee
    (Immigration Court Practice Manual 3.4(e)(iii)).
  How (DHS's "Instructions for Submitting Certain Applications in Immigration
    Court...", revised 06/08/2026, part B; data/reference/defa-pre-order.txt):
    filed with the court, a complete copy served on ICE (OPLA); and to the
    USCIS lockbox for the client's state: both payment confirmations, a copy
    of the G-28 and a copy of the instructions. USCIS then sends the receipt
    and the biometrics appointment; missing biometrics is abandonment
    (8 CFR 1003.47(c), (d)).
  When (Practice Manual, version of 02/20/2020, 3.1(b) and 4.15(j)): not
    detained, at least 15 days before the individual calendar hearing, unless
    the judge sets another date (schemas/registers/journey.json court_filing_days_before).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from filing_questions import DATE, LINES, TEXT, YES_NO, has_doc, lockbox, money, putter, state_of, us, value
from filing_questions import iso as _d
from filing_questions import plus_years as _plus
import clock
import schema_path
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

TITLE = "Cancellation of removal (EOIR-42A or EOIR-42B)"
FORMS = ["EOIR-42B (not a permanent resident)", "EOIR-42A (permanent resident)"]
RELATIVES = ["U.S. citizen spouse", "Permanent resident spouse", "U.S. citizen father", "Permanent resident father", "U.S. citizen mother",
             "Permanent resident mother", "U.S. citizen child", "Permanent resident child"]
ENTRY = ["Inspected and admitted with a visa", "Inspected and admitted with a green card", "Entered without inspection", "Entered without documents", "Other"]
CHART = "uscis_lockboxes_court"
INSTRUCTIONS_URL = "https://www.uscis.gov/sites/default/files/document/legal-docs/DEFA-pre-order-instructions.pdf"
EOIR_PAY = "epay.eoir.justice.gov"
PAY_GOV = "pay.gov (the USCIS biometric services fee form)"
HISTORY = [("cancel.ordered_removed", "Ever ordered deported, excluded or removed? (42B item 61, 42A item 56)"),
           ("cancel.overstayed_vd", "Ever overstayed a grant of voluntary departure? (42B item 61, 42A item 56)"),
           ("cancel.failed_to_appear", "Ever failed to appear for removal or deportation? (42B item 61, 42A item 56)"),
           ("cancel.drunkard", "Ever a habitual drunkard? (42B item 62, 42A item 57)"),
           ("cancel.gambling", "Income ever derived principally from illegal gambling? (42B item 62, 42A item 57)"),
           ("cancel.false_testimony", "Ever given false testimony to obtain an immigration benefit? (42B item 62, 42A item 57)"),
           ("cancel.prostitution", "Ever engaged in prostitution or unlawful commercialized vice? (42B item 62, 42A item 57)"),
           ("cancel.immunity", "Ever involved in a serious criminal offense and asserted immunity from prosecution? (42B item 62, 42A item 57)"),
           ("cancel.polygamist", "Ever a polygamist? (42B item 62, 42A item 57)"),
           ("cancel.smuggling", "Ever brought, or tried to bring, someone into the U.S. illegally? (42B item 62, 42A item 57)"),
           ("cancel.drug_trafficker", "Ever a trafficker of a controlled substance, or helped one? (42B item 62, 42A item 57)"),
           ("cancel.security", "Inadmissible or deportable on security grounds, INA 212(a)(3) or 237(a)(4)? (bars both: INA 240A(c)(4))"),
           ("cancel.persecutor", "Ever ordered, incited, assisted or otherwise took part in persecution? (bars both: INA 240A(c)(5))"),
           ("cancel.prior_relief", "Ever granted 212(c) relief or suspension of deportation, or had removal cancelled? (bars both: INA 240A(c)(6))")]
MILITARY = [("cancel.armed_forces", "Ever served in the U.S. Armed Forces? (42B item 55, 42A item 50)"),
            ("cancel.draft_evasion", "Ever left the U.S. or the draft district to avoid the draft? (42B item 56, 42A item 51)"),
            ("cancel.deserted", "Ever deserted from the U.S. military while the U.S. was at war? (42B item 57, 42A item 52)"),
            ("cancel.selective_service", "If male, registered with the Selective Service? (42B item 58, 42A item 53)"),
            ("cancel.exempted", "Ever exempted from service as a conscientious objector, as an alien, or otherwise? (42B item 59, 42A item 54)")]


def is_42a(graph) -> bool:
    return value(graph, "cancel.form") == FORMS[1]


def _is_42b(graph) -> bool:
    return not is_42a(graph)


SECTIONS = [
    ("Which application", "the attorney", [
        ("cancel.form", "The client applies on", {"type": "choice", "options": FORMS}, True),
        ("cancel.nta_served", "The date the Notice to Appear was served (it stops the clock: INA 240A(d)(1))", DATE, True),
        ("cancel.offense_date", "The date of an offense that stops the clock earlier, if any (INA 240A(d)(1)(B))", DATE, False),
    ]),
    ("Permanent residence (EOIR-42A)", "the attorney", [
        ("cancel.lpr_date", "Item 17: became a permanent resident on", DATE, True),
        ("cancel.lpr_place", "Item 17: where (the port of entry, or the office that approved the adjustment)", TEXT, True),
        ("cancel.admitted_on", "First admitted to the U.S. in any status on (the 7 years count from here: INA 240A(a)(2))", DATE, True),
        ("cancel.aggravated_felony", "Ever convicted of an aggravated felony? (bars the EOIR-42A: INA 240A(a)(3))", YES_NO, True),
    ], is_42a),
    ("Ten years in the U.S. (EOIR-42B)", "the attorney", [
        ("cancel.presence_since", "Item 17: continuously in the U.S. since (apart from the trips in item 23)", DATE, True),
        ("cancel.conviction_bar", "Ever convicted of an offense under INA 212(a)(2), 237(a)(2) or 237(a)(3)? (bars the EOIR-42B: INA 240A(b)(1)(C))", YES_NO, True),
        ("cancel.good_moral_character", "A person of good moral character for the whole 10 years? (INA 240A(b)(1)(B) and 101(f): the attorney's judgment)",
         YES_NO, True),
        ("cancel.visa_petition", "Item 63: the beneficiary of an approved visa petition?", YES_NO, True),
    ], _is_42b),
    ("The qualifying relatives (EOIR-42B)", "the attorney", [
        ("cancel.relative1_is", "Item 17: the first relative who would suffer exceptional and extremely unusual hardship is the client's",
         {"type": "choice", "options": RELATIVES}, True),
        ("cancel.relative1_name", "Their name (Last, First, Middle)", TEXT, True),
        ("cancel.relative1_dob", "Their date of birth", DATE, False),
        ("cancel.relative2_is", "A second qualifying relative, if any: the client's", {"type": "choice", "options": RELATIVES}, False),
        ("cancel.relative2_name", "The second relative's name (Last, First, Middle)", TEXT, False),
        ("cancel.relative2_dob", "The second relative's date of birth", DATE, False),
        ("cancel.relative3_is", "A third qualifying relative, if any: the client's", {"type": "choice", "options": RELATIVES}, False),
        ("cancel.relative3_name", "The third relative's name (Last, First, Middle)", TEXT, False),
        ("cancel.relative3_dob", "The third relative's date of birth", DATE, False),
        ("cancel.battered", "Item 17: the client, or their child, was battered or subjected to extreme cruelty by a U.S. citizen or permanent resident "
                            "spouse or parent? (INA 240A(b)(2): a different test, the attorney decides)", YES_NO, False),
    ], _is_42b),
    ("Arrival and trips (items 18 to 24)", "the attorney", [
        ("cancel.first_arrival_name", "Item 18: the name used at the first arrival (Last, First, Middle)", TEXT, True),
        ("cancel.first_arrival_date", "Item 19: first arrived in the U.S. on", DATE, True),
        ("cancel.first_arrival_place", "Item 20: the place or port of first arrival (city and state)", TEXT, True),
        ("cancel.entry_manner", "Item 21: how the client first entered", {"type": "choice", "options": ENTRY}, True),
        ("cancel.visa_type", "Item 21: the kind of visa (e.g. B-2), if admitted with one", TEXT, False),
        ("cancel.ewi_explain", "Item 21: the explanation, if entered without inspection", TEXT, False),
        ("cancel.never_departed", "Item 23: never left the U.S. since first arriving?", YES_NO, True),
        ("cancel.long_absence", "A single trip over 90 days, or trips over 180 days in all, during the period? (INA 240A(d)(2))", YES_NO, False),
        ("cancel.departed_under_order", "Item 24.a: ever left the U.S. under an order of deportation, exclusion or removal?", YES_NO, True),
        ("cancel.departed_vd", "Item 24.b: ever left the U.S. under a grant of voluntary departure?", YES_NO, True),
    ]),
    ("Who can't apply at all (INA 240A(c))", "the attorney", [
        ("cancel.crewman", "Entered the U.S. as a crewman after 06/30/1964? (42B item 51, 42A item 46)", YES_NO, True),
        ("cancel.exchange", "Admitted as, or later became, a J exchange visitor? (42B item 52, 42A item 47)", YES_NO, True),
        *[(k, label, YES_NO, True) for k, label in HISTORY[-3:]],
    ]),
    ("Arrests and history (items 54, 61 and 62; 42A items 49, 56 and 57)", "the attorney", [
        ("cancel.arrested", "Ever arrested, summoned to court as a defendant, convicted, fined, jailed or on probation, anywhere (traffic and alcohol incidents included)?",
         YES_NO, True),
        ("cancel.arrest_details", "Each one: the offense, where, the date, the sentence and the time served (the records go in the exhibits)", LINES, False),
        *[(k, label, YES_NO, True) for k, label in HISTORY[:-3]],
        ("cancel.history_explain", "The explanation of any Yes above", LINES, False),
    ]),
    ("Military service", "the attorney", [(k, label, YES_NO, True) for k, label in MILITARY] + [
        ("cancel.military_24_months", "At least 24 months of active duty, enlisted while in the U.S., and honorably separated if no longer serving? "
                                      "(then continuity isn't required: INA 240A(d)(3))", YES_NO, False)]),
    ("Family, money and reports", "the attorney", [
        ("cancel.child_support", "Ordered or obliged to pay child support or spousal maintenance after a separation or divorce? (42B item 37, 42A item 36)",
         YES_NO, True),
        ("cancel.public_assistance", "Ever received public or private relief or assistance (welfare, unemployment, Medicaid, TANF)? (42B item 41, 42A item 40)",
         YES_NO, True),
        ("cancel.family_assistance", "Has the spouse or a child received such assistance? (42B item 45, 42A item 44)", YES_NO, True),
        ("cancel.tax_years", "Each year the client filed a federal income tax return (42B item 42, 42A item 41)", TEXT, True),
        ("cancel.address_reports", "Submitted the address reports INA 265 requires? (42B item 53, 42A item 48)", YES_NO, True),
    ]),
    ("Service on ICE (Part 10)", "the attorney", [
        ("eoir.electronic_service", "An ECAS case with ICE taking part (no paper service needed)?", YES_NO, False),
        ("eoir.dhs_address", "The ICE (OPLA) office served, its address", TEXT, False),
    ]),
]
MORE_QUESTIONS = "The questions for the other form appear when the application above is changed."


# -- the case's record and dates --------------------------------------------------------------------------------


def resident_since(graph) -> date | None:
    """When the client became a permanent resident, from the case: an I-485 approval, the green card's date, an immigrant visa entry."""
    import journey

    approved = next((n for n in reversed(journey.notices(graph)) if n["form"] == "I-485" and n["kind"] == "approval"), None)
    return (_d(value(graph, "cancel.lpr_date")) or (_d(approved["date"]) if approved else None) or _d(value(graph, "n400.lpr_date"))
            or _d(value(graph, "visa.entry_date")))


def from_record(status: dict[str, Any], graph) -> None:
    """The next individual (merits) hearing entered on the case page: the court filing date is counted from it."""
    today = clock.today()
    hearings = [h for h in (status.get("journey") or {}).get("hearings") or [] if not h.get("result") and (_d(h.get("date")) or date.min) >= today]
    merits = sorted((h for h in hearings if str(h.get("kind") or "").startswith("Individual")), key=lambda h: h["date"])
    if merits:
        put = putter(graph, "case page")
        put("cancel.next_hearing", merits[0]["date"], "the individual hearing entered on the case page")
        put("cancel.next_hearing_detained", "Yes" if merits[0].get("detained") else "No", "the hearing entered on the case page")
    if any(r.get("filing") == "eoir28" for r in status.get("filings") or []):
        putter(graph, "case page")("cancel.eoir28_filed", "Yes", "the EOIR-28 recorded as filed on the case page")


def _name(family: Any, given: Any, middle: Any = None) -> str | None:
    family, rest = str(family or "").strip(), " ".join(x for x in (str(given or "").strip(), str(middle or "").strip()) if x)
    return f"{family}, {rest}" if family and rest else None




def _room(template: str, field: str) -> int:
    """How many capitals fit in a one-line box: its width (the widget's /Rect) over a Helvetica capital at the box's own font
    size (its /DA, about 0.72 of the size), measured from the template as src/asylum.py measures its boxes. The EOIR-42's
    "Name 2" has no /MaxLen, so nothing else stops a long line from running past the box."""
    import re as _re

    from pypdf import PdfReader

    reader = PdfReader(str(schema_path.path("template", template)))
    for page in reader.pages:
        for a in page.get("/Annots") or []:
            w = a.get_object()
            if str(w.get("/T") or "") == field:
                x0, _y0, x1, _y1 = [float(v) for v in w["/Rect"]]
                size = _re.search(r"([\d.]+)\s+Tf", str(w.get("/DA") or ""))
                pts = float(size.group(1)) if size and float(size.group(1)) > 0 else 9.0
                return max(10, int(abs(x1 - x0) / (0.72 * pts)))
    return 40


_ROOM: dict[str, int] = {}


def other_names(graph) -> tuple[str | None, str | None, list[str]]:
    """The other names the case settled (src/name_events.py), as the EOIR-42A/B takes them: the first in "Name 1", as many more as
    fit in "Name 2" (whole names, "; " between them), and the ones left off, which a note lists for the attorney to add by hand."""
    v = lambda k: value(graph, k)  # noqa: E731
    names = [x for n in range(1, 10) if (x := _name(v(f"applicant.other_name{n}_family"), v(f"applicant.other_name{n}_given")))]
    if not names:
        return None, None, []
    template = "eoir42a" if is_42a(graph) else "eoir42b"
    room = _ROOM.setdefault(template, _room(template, "Name 2"))
    fit: list[str] = []
    for i, name in enumerate(names[1:]):
        if len("; ".join(fit + [name])) > room:
            return names[0], "; ".join(fit) or None, names[1 + i:]
        fit.append(name)
    return names[0], "; ".join(fit) or None, []


def _line(*parts: Any) -> str:
    return ", ".join(str(p).strip() for p in parts if str(p or "").strip())


def _state_zip(state: Any, zip_code: Any) -> str:
    return " ".join(str(x).strip() for x in (state, zip_code) if str(x or "").strip())


def relatives(graph) -> list[dict[str, Any]]:
    """The qualifying relatives the attorney named (EOIR-42B, item 17): who, their name, birth date, and kind (spouse, father, mother, child)."""
    out = []
    for n in (1, 2, 3):
        who = value(graph, f"cancel.relative{n}_is")
        if who in RELATIVES:
            out.append({"n": n, "is": who, "kind": who.rsplit(" ", 1)[-1], "usc": who.startswith("U.S."),
                        "name": value(graph, f"cancel.relative{n}_name"), "dob": _d(value(graph, f"cancel.relative{n}_dob"))})
    return out


def derive(graph, today: date):
    import court

    court.derive(graph, today)  # the service on ICE and the preparer's name, as on the EOIR-28
    put = putter(graph, "cancellation.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    since = resident_since(graph)
    put("cancel.form", FORMS[1] if since else FORMS[0], "the case shows the client is a permanent resident" if since else
        "nothing in the case shows the client is a permanent resident")
    put("cancel.lpr_date", since.isoformat() if since else None, "the case's permanent residence date")
    full = _name(v("applicant.family_name"), v("applicant.given_name"), v("applicant.middle_name"))
    put("cancel.full_name", full, "the client's name")
    birth = " ".join(str(v("applicant.birth_certificate_name") or "").split()).upper()
    if full and birth and birth == " ".join(x for x in (v("applicant.given_name"), v("applicant.middle_name"), v("applicant.family_name")) if x).upper():
        put("cancel.birth_name", full, "the birth certificate shows the same name")
    put("cancel.birth_place", _line(v("applicant.birth_city"), v("applicant.country_of_birth")) or None, "the client's place of birth")
    unit = " ".join(x for x in (v("applicant.physical_unit_type"), v("applicant.physical_apt")) if x)
    put("cancel.street", v("applicant.physical_street"), "the client's home address")
    put("cancel.apt", unit or None, "the client's home address")
    put("cancel.city_state_zip", _line(v("applicant.physical_city"), _state_zip(v("applicant.physical_state"), v("applicant.physical_zip"))) or None,
        "the client's home address")
    put("cancel.address1", _line(v("applicant.physical_street"), unit, v("applicant.physical_city"), _state_zip(v("applicant.physical_state"),
                                                                                                                v("applicant.physical_zip"))) or None,
        "the client's home address (item 16 lists it first)")
    put("cancel.address2", _line(v("applicant.prior_address_street"), v("applicant.prior_address_apt"), v("applicant.prior_address_city"),
                                 _state_zip(v("applicant.prior_address_state"), v("applicant.prior_address_zip"))) or None, "the client's previous address")
    # every other name the case settled (src/name_events.py): the first in Name 1, what fits in Name 2 (the form's only other row);
    # the names that do not fit are listed in the filing's notes for the attorney to add by hand (notes below)
    first, second, _left = other_names(graph)
    put("cancel.other_name1", first or ("NONE" if v("applicant.na.other_names") == "NOT APPLICABLE" else None),
        "the other names the client has used")
    put("cancel.other_name2", second, "the other names the client has used")
    # the first arrival, when the case says the last arrival was the first one
    if v("applicant.first_time_in_us") == "Yes":
        arrived = v("applicant.last_arrival_date") or v("applicant.i94_arrival_date") or v("applicant.last_arrival_date_self_reported")
        put("cancel.first_arrival_date", arrived, "the only arrival in the case (the I-94)")
        put("cancel.first_arrival_name", _name(v("applicant.i94_family_name"), v("applicant.i94_given_name")) or full, "the name on the I-94")
        put("cancel.first_arrival_place", _line(v("applicant.last_arrival_city"), v("applicant.last_arrival_state")) or None, "the place of arrival")
        if str(v("applicant.last_arrival_manner") or "").upper() == "ADMITTED" and v("applicant.i94_class_of_admission"):
            put("cancel.entry_manner", ENTRY[0], "admitted on the I-94's class of admission")
            put("cancel.visa_type", v("applicant.i94_class_of_admission"), "the I-94's class of admission")
        if since is None and arrived:
            put("cancel.presence_since", arrived, "the client's only arrival in the U.S.")
        if arrived and str(v("applicant.last_arrival_manner") or "").upper() == "ADMITTED":
            put("cancel.admitted_on", arrived, "admitted at the only arrival (the I-94)")
    put("cancel.crewman", "No" if v("applicant.arrived_as_crewman") == "No" else None, "the client's answer: not a crewman")
    married = {"Married": "Yes", "Single": "No", "Divorced": "No", "Widowed": "No"}.get(str(v("applicant.marital_status") or ""))
    put("cancel.married", married, "the client's marital status")
    put("cancel.spouse_name", _name(v("applicant.spouse_family_name"), v("applicant.spouse_given_name"), v("applicant.spouse_middle_name")), "the spouse")
    # the present job; the first organization
    put("cancel.employer1", v("applicant.employer1_name"), "the client's present employer")
    put("cancel.employer1_address", _line(v("applicant.employer1_street"), v("applicant.employer1_city"),
                                          _state_zip(v("applicant.employer1_state"), v("applicant.employer1_zip"))) or None, "the employer's address")
    put("cancel.org1_name", v("applicant.part9.org1_name"), "the organization the client named")
    put("cancel.org1_location", _line(v("applicant.part9.org1_city"), v("applicant.part9.org1_state"), v("applicant.part9.org1_country")) or None, "the organization")
    put("cancel.org1_nature", v("applicant.part9.org1_nature"), "the organization")
    put("cancel.org1_from", v("applicant.part9.org1_date_from"), "the organization")
    put("cancel.org1_to", v("applicant.part9.org1_date_to"), "the organization")
    # an approved petition for the client (42B item 63)
    import journey

    if any(n["form"] in ("I-130", "I-140", "I-360") and n["kind"] == "approval" for n in journey.notices(graph)):
        put("cancel.visa_petition", "Yes", "an approved petition's notice in the folder")
    # the petitioner, as the first qualifying relative (EOIR-42B)
    rel, status = str(v("family.relationship") or "").lower(), str(v("petitioner.status") or "").upper()
    usc = status in ("USC", "U.S. CITIZEN", "CITIZEN")
    lpr = status in ("LPR", "PERMANENT RESIDENT")
    if rel == "spouse" and (usc or lpr):
        put("cancel.relative1_is", RELATIVES[0] if usc else RELATIVES[1], "the petitioner, the client's spouse")
        put("cancel.relative1_name", _name(v("petitioner.family_name"), v("petitioner.given_name")), "the petitioner")
        put("cancel.relative1_dob", v("petitioner.dob"), "the petitioner")
    # item 17's boxes and item 43's children, from the relatives named
    rels = relatives(graph)
    for r in rels:
        put("cancel.box_hardship", "Yes", "a qualifying relative is named")
        put(f"cancel.box_{r['kind']}", "Yes", f"the client's {r['kind']}")
        put(f"cancel.box_{r['kind']}_{'usc' if r['usc'] else 'lpr'}", "Yes", f"the client's {r['kind']}'s status")
    kids = [r for r in rels if r["kind"] == "child"]
    for i, r in enumerate(kids, start=1):
        put(f"cancel.child{i}_name", r["name"], "a qualifying child")
        put(f"cancel.child{i}_dob", r["dob"].isoformat() if r["dob"] else None, "a qualifying child")
        put(f"cancel.child{i}_status", "U.S. CITIZEN" if r["usc"] else "PERMANENT RESIDENT", "a qualifying child")
    put("cancel.children_count", v("applicant.total_children") or (str(len(kids)) if kids else None), "the number of children in the case")
    put("cancel.preparer_address", _line(v("firm.street"), v("firm.city"), _state_zip(v("firm.state"), v("firm.zip"))) or None, "the firm")
    put("cancel.g28_forms", "EOIR-42A" if is_42a(graph) else "EOIR-42B", "the application")
    return graph


# -- the statute's tests ------------------------------------------------------------------------------------------


def stop_date(graph) -> date | None:
    """The day the clock stopped (INA 240A(d)(1)): the Notice to Appear's service, or an earlier offense."""
    dates = [d for d in (_d(value(graph, "cancel.nta_served")), _d(value(graph, "cancel.offense_date"))) if d]
    return min(dates) if dates else None


def _years(start: date, end: date) -> str:
    years = end.year - start.year - ((end.month, end.day) < (start.month, start.day))
    days = (end - _plus(start, years)).days
    return f"{years} year{'s' if years != 1 else ''} and {days} day{'s' if days != 1 else ''}"


def _age(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def deadline(graph) -> tuple[date | None, str]:
    """The filing date the Practice Manual sets from the next individual hearing (not detained): (date, why)."""
    import journey

    hearing = _d(value(graph, "cancel.next_hearing"))
    if not hearing:
        return None, "No individual hearing is on the case page yet: the judge sets the date at the master calendar hearing."
    if value(graph, "cancel.next_hearing_detained") == "Yes":
        return None, f"Detained: the court sets the filing date for the {us(hearing)} hearing (Practice Manual 3.1(b))."
    cfg = journey.settings()["deadlines"]
    days = (cfg.get("court_filing_days_before") or {}).get("individual", 15)
    due = journey._next_business_day(hearing - timedelta(days=days))
    return due, (f"{days} days before the {us(hearing)} individual hearing, unless the judge set another date "
                 f"({cfg.get('court_filing_source') or 'Immigration Court Practice Manual 3.1(b)'}; 4.15(j))")


def fee(graph, today: date) -> tuple[int | None, str]:
    import fees

    eoir = fees.load(today).get("eoir") or {}
    if is_42a(graph):
        return eoir.get("eoir42a"), "the EOIR-42A fee (EOIR's forms page)"
    return eoir.get("eoir42b"), "the EOIR-42B fee (EOIR's forms page)"


def biometrics_fee(today: date) -> int | None:
    import fees

    return (fees.load(today).get("eoir") or {}).get("dhs_biometrics")


def where(graph) -> tuple[list[str] | None, str | None]:
    """The USCIS lockbox for the client's state that gets the fee confirmations (DHS's instructions, part B)."""
    return lockbox(CHART, state_of(graph))


def notes(graph, today: date) -> list[dict[str, str]]:
    v = lambda k: value(graph, k)  # noqa: E731
    a = is_42a(graph)
    out = [{"level": "info", "title": "Which form",
            "text": "A permanent resident applies on the EOIR-42A (INA 240A(a): 5 years a resident, 7 years of continuous residence after any admission, "
                    "no aggravated felony). Anyone else on the EOIR-42B (INA 240A(b)(1): 10 years of continuous physical presence, good moral character, "
                    "no disqualifying conviction, and exceptional and extremely unusual hardship to a U.S. citizen or permanent resident spouse, parent or "
                    "child). The attorney decides."}]
    stop = stop_date(graph)
    start = _d(v("cancel.admitted_on") if a else v("cancel.presence_since"))
    if start:
        need = 7 if a else 10
        reached = _plus(start, need)
        text = (f"From {us(start)}, {need} years are reached on {us(reached)}. "
                + (f"The clock stopped on {us(stop)}: {_years(start, stop)} by then." if stop else
                   "Enter the date the Notice to Appear was served: the clock stops there (INA 240A(d)(1)).")
                + (" A trip over 90 days, or 180 in all, breaks it (240A(d)(2))." if not a else ""))
        out.append({"level": "warn" if stop and stop < reached else "info", "title": "The clock (INA 240A(d))", "text": text})
    if not a:
        out.append({"level": "info", "title": "The annual cap",
                    "text": "No more than 4,000 grants of this cancellation a fiscal year (INA 240A(e)(1)). When they are used up, the judge reserves the "
                            "grant to a later year; there are no conditional grants (8 CFR 1240.21(a)(2), (c)(1)). A grant of asylum or adjustment while "
                            "it is pending means it is denied (1240.21(c)(2))."})
        for r in relatives(graph):
            if r["kind"] == "child" and r["dob"] and _age(r["dob"], today) < 21:
                out.append({"level": "info", "title": "A qualifying child",
                            "text": f"{r['name'] or 'The child'} turns 21 on {us(_plus(r['dob'], 21))}: a 'child' is unmarried and under 21 (INA 101(b)(1)). "
                                    "The attorney decides what that means for the case."})
    due, why = deadline(graph)
    out.append({"level": "warn" if due and today > due else "info", "title": "When",
                "text": (f"File it with the court by {us(due)}: {why}." if due else why)
                + " Pay both fees first: the court takes no application without proof of its fee (8 CFR 1103.7(a)(3))."})
    amount, label = fee(graph, today)
    bio = biometrics_fee(today)
    out.append({"level": "info", "title": "Fees",
                "text": f"{money(amount)}: {label}, paid only through the EOIR Payment Portal ({EOIR_PAY}) before filing. Plus the DHS biometrics fee, "
                        f"{money(bio)} a person, on {PAY_GOV}. A fee waiver request goes to the judge (8 CFR 1240.20(a)); the biometrics fee can't be "
                        "waived. No Form G-1450 for either."})
    box, name = where(graph)
    out.append({"level": "info", "title": "Where it goes",
                "text": "The application and its evidence: to the immigration court (e-filed in ECAS in an eligible case, else as the court directs), with a "
                        "complete copy served on ICE (OPLA) unless ICE takes part in ECAS. And to USCIS"
                        + (f" ({name}: {', '.join(box)})" if box else " (the lockbox for the client's state, in DHS's instructions)")
                        + ": the EOIR payment confirmation, the biometrics fee confirmation, a copy of the G-28 and a copy of DHS's instructions. "
                          "USCIS mails a receipt (file it with the court) and a biometrics appointment; missing the appointment can mean the "
                          "application is abandoned (8 CFR 1003.47(c), (d))."})
    if v("cancel.military_24_months") == "Yes":
        out.append({"level": "info", "title": "Military service", "text": "With 24 months of honorable active duty, enlisted in the U.S., continuous "
                                                                         "residence or presence isn't required (INA 240A(d)(3))."})
    if v("cancel.exchange") == "Yes":
        out.append({"level": "warn", "title": "A J exchange visitor", "text": "Barred if admitted for graduate medical training, or if subject to the 2-year "
                                                                             "home residence rule and it was neither met nor waived (INA 240A(c)(2), (3)). "
                                                                             "The attorney decides."})
    _first, _second, left = other_names(graph)
    if left:
        out.append({"level": "warn", "title": "Other names that do not fit",
                    "text": f"The form has two boxes for other names, and {len(left)} more did not fit: {'; '.join(left)}. Add "
                            f"{'it' if len(left) == 1 else 'them'} by hand on a page attached to the application, pointing to the other-names item."})
    if v("cancel.eoir28_filed") != "Yes":
        out.append({"level": "info", "title": "The attorney's appearance", "text": "No EOIR-28 is recorded as filed for this case: file it before, "
                                                                                    "or with, the application."})
    return out


@producer(ATTORNEY)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    a = is_42a(graph)
    if not has_doc(client_dir, "notice_to_appear") and v("applicant.nta_present") != "Yes" and not v("cancel.next_hearing"):
        out.append(held(CLIENT, "No Notice to Appear in the folder: cancellation is filed only with the immigration court that has the case, after the Notice to "
                   "Appear is filed there (8 CFR 1240.20(b), 1003.14). Confirm the case (EOIR case status: 1-800-898-7180)."))
    if not v("applicant.a_number"):
        out.append(held(CLIENT, "The client's A-Number (on the Notice to Appear): the court files the application by it."))
    stop = stop_date(graph)
    if v("cancel.crewman") == "Yes":
        out.append("A crewman who entered after 06/30/1964 can't get cancellation (INA 240A(c)(1)).")
    for key, cite in (("cancel.security", "240A(c)(4)"), ("cancel.persecutor", "240A(c)(5)"), ("cancel.prior_relief", "240A(c)(6)")):
        if v(key) == "Yes":
            out.append(f"Answered Yes to: \"{dict(HISTORY)[key].split(' (')[0]}\": that bars cancellation (INA {cite}). The attorney decides.")
    continuity = v("cancel.military_24_months") != "Yes"  # INA 240A(d)(3)
    if a:
        since = _d(v("cancel.lpr_date"))
        if since and today < _plus(since, 5):
            out.append(f"A permanent resident since {us(since)}: 5 years are reached only on {us(_plus(since, 5))} (INA 240A(a)(1)).")
        admitted = _d(v("cancel.admitted_on"))
        if continuity and admitted and stop and stop < _plus(admitted, 7):
            out.append(f"Admitted {us(admitted)}, and the clock stopped on {us(stop)}: {_years(admitted, stop)} of continuous residence; 7 are required "
                       "(INA 240A(a)(2), (d)(1)).")
        if v("cancel.aggravated_felony") == "Yes":
            out.append("An aggravated felony conviction bars the EOIR-42A (INA 240A(a)(3)).")
        if not has_doc(client_dir, "green_card") and not resident_since(graph):
            out.append(held(CLIENT, "The EOIR-42A is for a permanent resident, and nothing in the case shows the client is one: add the green card or the approval."))
    else:
        since = _d(v("cancel.presence_since"))
        if continuity and since and stop and stop < _plus(since, 10):
            out.append(f"In the U.S. since {us(since)}, and the clock stopped on {us(stop)}: {_years(since, stop)} of continuous physical presence; "
                       "10 are required (INA 240A(b)(1)(A), (d)(1)).")
        if continuity and v("cancel.long_absence") == "Yes":
            out.append("A trip over 90 days, or trips over 180 days in all, breaks continuous physical presence (INA 240A(d)(2)).")
        if v("cancel.conviction_bar") == "Yes":
            out.append("A conviction under INA 212(a)(2), 237(a)(2) or 237(a)(3) bars the EOIR-42B (INA 240A(b)(1)(C); the waiver in (b)(5) is the "
                       "attorney's call).")
        if v("cancel.good_moral_character") == "No":
            out.append("Without good moral character for the 10 years the EOIR-42B can't be granted (INA 240A(b)(1)(B)).")
        rels = relatives(graph)
        if not rels and v("cancel.battered") != "Yes":
            out.append("No qualifying relative: the EOIR-42B needs exceptional and extremely unusual hardship to a U.S. citizen or permanent resident "
                       "spouse, parent or child (INA 240A(b)(1)(D)).")
        for r in rels:
            if r["kind"] == "child" and r["dob"] and _age(r["dob"], today) >= 21:
                out.append(f"{r['name'] or 'The child named'} is {_age(r['dob'], today)}: a 'child' is unmarried and under 21 (INA 101(b)(1)). "
                           "The attorney decides whether they still count.")
        if resident_since(graph):
            out.append(held(OFFICE, "The case shows the client is a permanent resident: the EOIR-42A is the form for a permanent resident (INA 240A(a))."))
    if v("eoir.electronic_service") != "Yes" and not v("eoir.dhs_address"):
        out.append(held(OFFICE, "Part 10: the ICE (OPLA) office served, or answer that this is an ECAS case where ICE takes part."))
    due, _why = deadline(graph)
    if due and today > due:
        out.append(f"Past the filing date the Practice Manual sets ({us(due)}): file only with the judge's leave, or by the date the judge set.")
    if not where(graph)[0]:
        out.append(held(OFFICE, "The client's state isn't on DHS's list of USCIS lockboxes for court filings: enter the home address."))
    return out
