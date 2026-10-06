"""Asylum: Form I-589, filed affirmatively with USCIS -- schemas/packets/i589.json
for the packet, schemas/packets/companion_forms.json ("i589") for the form,
schemas/law/asylum.json for the rules.

What a lawyer watches on an asylum case, from the case:

  - the 1-YEAR DEADLINE: within one year of the client's last arrival,
    counted to the day USCIS receives the application (INA 208(a)(2)(B)),
    unless changed or extraordinary circumstances explain the delay -- and
    not for an unaccompanied child (208(a)(2)(E));
  - where it is filed: with USCIS (by mail, by state: the Dallas or Chicago
    lockbox) -- unless the client is in immigration court, where it goes to
    the court (except an unaccompanied child, who files with USCIS);
  - what makes USCIS reject it outright: the required boxes left blank, a
    Yes without its explanation, a missing statement of why the client is
    applying, an unsigned form;
  - the fees: none to file, but the $100 Pub. L. 119-21 asylum fee at
    filing and the Annual Asylum Fee later (none for Ms. L. class members);
  - after filing: the 150 days before a work permit can be asked for, the
    Annual Asylum Fee notice, the interview.

The form is filled from the same reviewed case as every filing: who the
client is, their address, entry, passport, parents, spouse, children and
history come from the case; the claim itself -- Parts B and C -- is the
client's account, written with the attorney. Long answers go on supplement
sheets (name, A-Number, signature, date on each, as the instructions ask);
the box then says where. Nothing here writes the client's story.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import clock
import schema_path
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

SETTINGS = schema_path.path("law", "asylum")
YES_NO = {"type": "choice", "options": ["Yes", "No"]}
TEXT = {"type": "text"}
DATE = {"type": "date"}
LINES = {"type": "text", "multiline": True}
YES = {"type": "choice", "options": ["Yes"]}

# (key, Part/Item and question as the form asks it, input, required)
_CLAIM = [
    ("asylum.basis_race", "Part B, 1 · Basis: race", YES, False),
    ("asylum.basis_religion", "Part B, 1 · Basis: religion", YES, False),
    ("asylum.basis_nationality", "Part B, 1 · Basis: nationality", YES, False),
    ("asylum.basis_political", "Part B, 1 · Basis: political opinion", YES, False),
    ("asylum.basis_social_group", "Part B, 1 · Basis: membership in a particular social group", YES, False),
    ("asylum.basis_torture", "Part B, 1 · Basis: Torture Convention", YES, False),
    ("asylum.cat", "Page 1 · Also apply for withholding of removal under the Convention Against Torture", YES, False),
    ("asylum.b1a", "Part B, 1.A · Have you, your family, or close friends or colleagues ever experienced harm, mistreatment or threats in the past by anyone?", YES_NO, True),
    ("asylum.b1a_explain", "Part B, 1.A · If yes: what happened, when, who caused it, and why you believe it occurred", LINES, False),
    ("asylum.b1b", "Part B, 1.B · Do you fear harm or mistreatment if you return to your home country?", YES_NO, True),
    ("asylum.b1b_explain", "Part B, 1.B · If yes: what harm you fear, who would harm you, and why", LINES, False),
    ("asylum.b2", "Part B, 2 · Have you or your family members ever been accused, charged, arrested, detained, interrogated, convicted or imprisoned in any country other than the U.S.?", YES_NO, True),
    ("asylum.b2_explain", "Part B, 2 · If yes: the circumstances and reasons", LINES, False),
    ("asylum.b3a", "Part B, 3.A · Have you or your family members ever belonged to or been associated with any organizations or groups in your home country?", YES_NO, True),
    ("asylum.b3a_explain", "Part B, 3.A · If yes: each person's participation, positions held, and for how long", LINES, False),
    ("asylum.b3b", "Part B, 3.B · Do you or your family members continue to participate in these organizations or groups?", YES_NO, True),
    ("asylum.b3b_explain", "Part B, 3.B · If yes: current participation, positions, and for how long", LINES, False),
    ("asylum.b4", "Part B, 4 · Are you afraid of being subjected to torture in your home country or any other country to which you may be returned?", YES_NO, True),
    ("asylum.b4_explain", "Part B, 4 · If yes: why, the torture you fear, by whom, and why", LINES, False),
]
_PART_C = [
    ("asylum.c1", "Part C, 1 · Have you, your spouse, children, parents or siblings ever applied to the U.S. Government for refugee status, asylum, or withholding of removal?", YES_NO, True),
    ("asylum.c1_explain", "Part C, 1 · If yes: the decision and what happened to any status received (with A-Numbers)", LINES, False),
    ("asylum.c2a", "Part C, 2.A · After leaving the country you claim asylum from, did you (or your spouse or children now in the U.S.) travel through or reside in any other country before entering the U.S.?", YES_NO, True),
    ("asylum.c2b", "Part C, 2.B · Have you or your family ever applied for or received any lawful status in any country other than the one you are claiming asylum from?", YES_NO, True),
    ("asylum.c2b_explain", "Part C, 2.A/2.B · If yes to either: each country, length of stay, status, why you left, whether you can return, and whether you sought asylum there", LINES, False),
    ("asylum.c3", "Part C, 3 · Have you, your spouse or children ever ordered, incited, assisted or otherwise participated in causing harm or suffering to any person because of race, religion, nationality, social group or political opinion?", YES_NO, True),
    ("asylum.c3_explain", "Part C, 3 · If yes: each incident and your involvement", LINES, False),
    ("asylum.c4", "Part C, 4 · After you left the country where you were harmed or fear harm, did you return to that country?", YES_NO, True),
    ("asylum.c4_explain", "Part C, 4 · If yes: dates, purpose and length of each visit", LINES, False),
    ("asylum.c5", "Part C, 5 · Are you filing more than 1 year after your last arrival in the U.S.? (computed from the entry date)", YES_NO, True),
    ("asylum.c5_explain", "Part C, 5 · If yes: why you did not file within the first year (changed or extraordinary circumstances)", LINES, False),
    ("asylum.c6", "Part C, 6 · Have you or a family member included in the application ever committed a crime or been arrested, charged, convicted or sentenced in the U.S. (including for an immigration violation)?", YES_NO, True),
    ("asylum.c6_explain", "Part C, 6 · If yes: each instance; what, when, where, sentence, detention, charges, release", LINES, False),
]
SECTIONS: list[tuple[str, list[tuple[str, str, dict[str, Any], bool]]]] = [
    ("Filing", [
        ("asylum.uac", "Was the client ever determined to be an unaccompanied alien child (UAC)? (no 1-year limit; files with USCIS by mail even in court)", YES_NO, False),
        ("asylum.ms_l", "Is the client a Ms. L. Settlement Class member or QAFM? (no Pub. L. 119-21 fees; written on page 1; paper filing only)", YES_NO, False),
        ("asylum.court", "Part A.I, 18 · Immigration court proceedings", {"type": "choice", "options": ["never", "now", "past"]}, True),
    ]),
    ("Part B · the claim (the client's account, written with the attorney)", _CLAIM),
    ("Part C · additional information", _PART_C),
    ("About the client (Part A.I)", [
        ("asylum.other_names", "Part A.I, 7 · Other names used (maiden name, aliases)", TEXT, False),
        ("asylum.nationality_at_birth", "Part A.I, 15 · Nationality at birth", TEXT, True),
        ("asylum.ethnic_group", "Part A.I, 16 · Race, ethnic or tribal group", TEXT, True),
        ("asylum.religion", "Part A.I, 17 · Religion", TEXT, True),
        ("asylum.left_country_date", "Part A.I, 19.a · When did you last leave your country?", DATE, True),
        ("asylum.native_language", "Part A.I, 23 · Native language (with dialect)", TEXT, True),
        ("asylum.fluent_english", "Part A.I, 24 · Fluent in English?", YES_NO, True),
        ("asylum.other_languages", "Part A.I, 25 · Other languages spoken fluently", TEXT, False),
        ("asylum.native_alphabet_name", "Part D · The client's name in their native alphabet (if not the Latin alphabet)", TEXT, False),
    ]),
    ("Family and background (Parts A.II, A.III)", [
        ("asylum.spouse_included", "Part A.II, 24 · Include the spouse in this application? (spouse in the U.S.)", YES_NO, False),
        ("asylum.siblings", "Part A.III, 5 · Brothers and sisters, one per line: 'name | city and country of birth | where they live now (or DECEASED)'", LINES, False),
        ("asylum.mother_location", "Part A.III, 5 · Where the mother lives now (or DECEASED)", TEXT, False),
        ("asylum.father_location", "Part A.III, 5 · Where the father lives now (or DECEASED)", TEXT, False),
        ("asylum.schools", "Part A.III, 3 · Schools, most recent first, one per line: 'name | type | location | from MM/YYYY | to MM/YYYY'", LINES, False),
    ]),
    ("Part D · signing", [
        ("asylum.family_assisted", "Part D · Did the client's spouse, parent or child help complete this application?", YES_NO, True),
        ("asylum.given_counsel_list", "Part D · Was the client given a list of persons who may help with their claim at little or no cost?", YES_NO, True),
    ]),
]
QUESTIONS = [(key, label, section, spec, required) for section, items in SECTIONS for key, label, spec, required in items]
EXPLAINED = {"b1a", "b1b", "b2", "b3a", "b3b", "b4", "c1", "c3", "c4", "c5", "c6"}  # a Yes needs its explanation ("c2a" and "c2b" share c2b_explain)
_BOX = {"b1a": "TextField14[0]", "b1b": "TextField15[0]", "b2": "PBL2_TextField[0]", "b3a": "PBL3A_TextField[0]", "b3b": "PBL3B_TextField[0]",
        "b4": "PB4_TextField[0]", "c1": "PCL1_TextField[0]", "c2b": "PCL2B_TextField[0]", "c3": "PCL3_TextField[0]", "c4": "PCL4_TextField[0]",
        "c5": "PCL5_TextField[0]", "c6": "PCL6_TextField[0]"}


def spot(key: str, template: Path | None = None) -> tuple[str, str, str]:
    """(page, part, question) of an explanation's box on the form ("5", "B", "1.A"), read from the form's own template (fill/where.py):
    the page the box is on and the part and question the form's tooltip for it names; never typed here. ("", "", "") when the
    template can't vouch for it (the supplement then names no question and the paralegal adds it)."""
    from fill.where import NotFound, where_is_in

    try:
        return where_is_in(template or schema_path.path("template", "i589"), {"fields": [_BOX[key]]}, key, "i589")
    except NotFound:
        return "", "", ""


def settings(path: Path = SETTINGS) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _value(graph, key: str) -> Any:
    fact = graph.get(key)
    return fact.value if fact is not None and fact.status == "resolved" and fact.value not in (None, "") else None


def _d(value: Any) -> date | None:
    m = re.search(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    try:
        return date.fromisoformat(m.group(0)) if m else None
    except ValueError:
        return None


def us(d: date | None) -> str:
    return d.strftime("%m/%d/%Y") if d else "?"


def _plus_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return date(d.year + years, 3, 1)


def last_arrival(graph) -> date | None:
    return _d(_value(graph, "applicant.last_arrival_date") or _value(graph, "applicant.i94_arrival_date") or _value(graph, "applicant.last_arrival_date_self_reported"))


def one_year(graph, today: date | None = None) -> dict[str, Any]:
    """The 1-year filing deadline from the last arrival (or why it can't be computed / may not apply)."""
    today = today or clock.today()
    s = settings()["one_year"]
    arrived, dob = last_arrival(graph), _d(_value(graph, "applicant.dob"))
    if arrived is None:
        return {"deadline": None, "level": "check", "text": "No date of last arrival in the case: the 1-year filing deadline can't be computed. Add the I-94 or the entry date."}
    last_day = _plus_years(arrived, 1) - timedelta(days=1)
    mail_by = last_day - timedelta(days=s["mail_days_before"])
    minor = dob is not None and _plus_years(dob, 18) > arrived
    uac = _value(graph, "asylum.uac") == "Yes"
    days = (last_day - today).days
    note = (" " + s["uac_note"]) if minor and not uac else ""
    if uac:
        return {"deadline": last_day.isoformat(), "mail_by": mail_by.isoformat(), "days_left": days, "level": "ok",
                "text": f"Determined an unaccompanied child: the 1-year limit doesn't apply (it would have been {us(last_day)})."}
    if days < 0:
        return {"deadline": last_day.isoformat(), "mail_by": mail_by.isoformat(), "days_left": days, "level": "late", "minor_at_arrival": minor,
                "text": f"More than 1 year since the last arrival ({us(arrived)}): the 1-year deadline was {us(last_day)}. Part C, 5 is Yes and needs the "
                        "changed or extraordinary circumstances that explain the delay: the attorney decides." + note}
    level = "urgent" if today > mail_by else "ok"
    return {"deadline": last_day.isoformat(), "mail_by": mail_by.isoformat(), "days_left": days, "level": level, "minor_at_arrival": minor,
            "text": f"Last arrival {us(arrived)}: USCIS must receive the application by {us(last_day)} ({days} days): mail it by {us(mail_by)}." + note}


def where_to_file(graph) -> dict[str, Any]:
    """USCIS by mail (the lockbox for the client's state), or the immigration court."""
    court, uac = _value(graph, "asylum.court"), _value(graph, "asylum.uac") == "Yes"
    nta = bool(_value(graph, "applicant.nta_present"))
    if court == "now" and not uac:
        return {"with": "court", "text": "In immigration court proceedings: the I-589 is filed with the immigration court that has the case, not USCIS "
                                         "(check the court on the EOIR system, acis.eoir.justice.gov, or 1-800-898-7180)."}
    if court is None and nta and not uac:
        return {"with": "unknown", "text": "A Notice to Appear is in the folder: check whether it was filed with the court (EOIR system or 1-800-898-7180). "
                                           "if the case is in court, the I-589 goes to the court, not USCIS."}
    return {"with": "uscis", "text": "Filed with USCIS by mail" + (" (an unaccompanied child must file on paper)" if uac else "") + "."}


def explanation_capacity(field: str, template: Path | None = None) -> int:
    """How many characters fit an explanation box, from the box's own size on the form."""
    return _capacity(field, str(template or schema_path.path("template", "i589")))


@lru_cache(maxsize=64)
def _capacity(field: str, template: str) -> int:
    from pypdf import PdfReader

    s = settings()
    reader = PdfReader(template)
    if reader.is_encrypted:
        reader.decrypt("")
    for page in reader.pages:
        for a in page.get("/Annots") or []:
            w = a.get_object()
            if str(w.get("/T") or "") == field:
                x0, y0, x1, y1 = [float(v) for v in w["/Rect"]]
                return int(s["box_chars_per_line"] * (x1 - x0) / 510) * max(1, int((y1 - y0) / s["box_line_points"]))
    return 600


def derive(graph, today: date | None = None):
    """The I-589's own facts (asylum.*) from what the case already holds -- each
    a derived source, so it traces to its document; anything a person set wins."""
    from assemble import _chronological, _entries

    today = today or clock.today()

    def put(key: str, value: Any, why: str) -> None:
        if value not in (None, "") and _value(graph, key) is None:
            graph.add_source(key, "asylum.derive", "derived", why, value, 0.85, tier=3)

    v = lambda k: _value(graph, k)  # noqa: E731
    put("asylum.residence_street", v("applicant.physical_street"), "the client's address")
    others = [" ".join(x for x in (v(f"applicant.other_name{n}_given"), v(f"applicant.other_name{n}_family")) if x) for n in (1, 2)]
    put("asylum.other_names", "; ".join(x for x in others if x) or None, "the client's earlier names (the name timeline)")
    phone = re.sub(r"\D", "", str(v("applicant.daytime_phone") or v("applicant.mobile_phone") or ""))[-10:]
    if len(phone) == 10:
        put("asylum.phone_area", phone[:3], "the client's phone")
        put("asylum.phone_local", f"{phone[3:6]}-{phone[6:]}", "the client's phone")
    if v("applicant.mailing_same_as_physical") == "No":
        for part in ("in_care_of", "street", "apt", "city", "state", "zip"):
            put(f"asylum.mailing_{part}", v(f"applicant.mailing_{part}"), "the mailing address")
    status = {"Single": "Single", "Married": "Married", "Divorced": "Divorced", "Widowed": "Widowed"}.get(v("applicant.marital_status"))
    put("asylum.marital_status", status, "the client's marital status")
    if status in ("Single", "Divorced", "Widowed"):
        put("asylum.not_married", "Yes", "not married")
    put("asylum.birth_place", ", ".join(x for x in (v("applicant.birth_city"), v("applicant.country_of_birth")) if x) or None, "the birth certificate / passport")
    put("asylum.nationality_at_birth", v("applicant.citizenship") or v("applicant.country_of_birth"), "the client's citizenship (check: nationality AT BIRTH)")
    if not v("applicant.nta_present"):
        put("asylum.court", "never", "no Notice to Appear in the folder (the attorney confirms on the EOIR system)")
    # entry
    arrived = last_arrival(graph)
    if arrived:
        put("asylum.entry1_date", arrived.isoformat(), "the last entry")
        place = ", ".join(x for x in (v("applicant.last_arrival_city"), v("applicant.last_arrival_state")) if x)
        put("asylum.entry1_place", place or None, "the last entry")
        put("asylum.entry1_status", v("applicant.i94_class_of_admission") or ("EWI" if "WITHOUT" in str(v("applicant.last_arrival_manner") or "").upper() else None),
            "the I-94 / how the client entered")
        put("asylum.entry1_status_expires", v("applicant.i94_admit_until_date"), "the I-94")
        put("asylum.c5", "Yes" if today > _plus_years(arrived, 1) - timedelta(days=1) else "No", f"last arrival {us(arrived)}")
    put("asylum.passport_number", v("applicant.travel_document_number"), "the passport")
    # the spouse
    for mine, theirs in (("family_name", "spouse_family_name"), ("given_name", "spouse_given_name"), ("dob", "spouse_dob"),
                         ("a_number", "spouse_a_number"), ("nationality", "spouse_country_of_birth")):
        put(f"asylum.spouse_{mine}", v(f"applicant.{theirs}"), "the client's spouse")
    put("asylum.marriage_date", v("applicant.marriage_date"), "the marriage certificate")
    put("asylum.marriage_place", ", ".join(x for x in (v("applicant.marriage_city"), v("applicant.marriage_country")) if x) or None, "the marriage certificate")
    # children: ALL of them, any age
    kids = _entries(graph, "questionnaire.child", 9, ("name", "a_number", "dob", "country"))
    if kids or v("applicant.total_children") is not None:
        put("asylum.has_children", "Yes" if kids else "No", "the questionnaire's children")
        put("asylum.total_children", str(len(kids)) if kids else None, "the questionnaire's children")
    from extract.names import split_name

    for n, c in enumerate(kids[:4], start=1):
        if c.get("name"):
            split = split_name(c["name"], set(str(v("applicant.family_name") or "").split()))
            put(f"asylum.child{n}_given_name", split.given, "the questionnaire")
            put(f"asylum.child{n}_family_name", split.family, "the questionnaire")
        put(f"asylum.child{n}_dob", c.get("dob"), "the questionnaire")
        put(f"asylum.child{n}_a_number", c.get("a_number"), "the questionnaire")
        put(f"asylum.child{n}_birth_place", c.get("country"), "the questionnaire")
    # parents
    for who in ("mother", "father"):
        put(f"asylum.{who}_name", " ".join(x for x in (v(f"applicant.{who}_given_name"), v(f"applicant.{who}_family_name")) if x) or None, "the birth certificate")
        put(f"asylum.{who}_birth_place", v(f"applicant.{who}_country_of_birth"), "the questionnaire")
        loc = str(v(f"asylum.{who}_location") or "")
        if loc.upper() == "DECEASED":
            put(f"asylum.{who}_deceased", "Yes", "the client")
    for n, line in enumerate([ln for ln in str(v("asylum.siblings") or "").splitlines() if ln.strip()][:4], start=1):
        name, birth, where = (re.split(r"\s*\|\s*", line) + ["", "", ""])[:3]
        put(f"asylum.sibling{n}_name", name.upper(), "the client's list")
        put(f"asylum.sibling{n}_birth_place", birth.upper(), "the client's list")
        if where.upper() == "DECEASED":
            put(f"asylum.sibling{n}_deceased", "Yes", "the client's list")
        else:
            put(f"asylum.sibling{n}_location", where.upper(), "the client's list")
    # background: the last foreign address, residences and jobs in the last 5 years (present first), schools
    put("asylum.before_us1_street", v("applicant.last_foreign_street"), "the last address abroad")
    put("asylum.before_us1_city", v("applicant.last_foreign_city"), "the last address abroad")
    put("asylum.before_us1_province", v("applicant.last_foreign_province"), "the last address abroad")
    put("asylum.before_us1_country", v("applicant.last_foreign_country"), "the last address abroad")
    put("asylum.before_us1_from", v("applicant.last_foreign_date_from"), "the last address abroad")
    put("asylum.before_us1_to", v("applicant.last_foreign_date_to"), "the last address abroad")
    window = _plus_years(today, -5).isoformat()
    homes = [{"street": " ".join(x for x in (v("applicant.physical_street"), v("applicant.physical_apt") and f"APT {v('applicant.physical_apt')}") if x),
              "city": v("applicant.physical_city"), "state": v("applicant.physical_state"), "country": "USA", "date_from": v("applicant.physical_address_since")}]
    homes += list(reversed(_chronological([a for a in _entries(graph, "questionnaire.prior_address", 7, ("street", "city", "state", "province", "country",
                                                                                                         "date_from", "date_to"))
                                           if not a.get("date_to") or str(a["date_to"]) >= window])))
    for n, h in enumerate([h for h in homes if h.get("street") or h.get("city")][:5], start=1):
        put(f"asylum.residence{n}_street", h.get("street"), "the address history")
        put(f"asylum.residence{n}_city", h.get("city"), "the address history")
        put(f"asylum.residence{n}_province", h.get("state") or h.get("province"), "the address history")
        put(f"asylum.residence{n}_country", h.get("country") or ("USA" if h.get("state") else None), "the address history")
        put(f"asylum.residence{n}_from", h.get("date_from"), "the address history")
        put(f"asylum.residence{n}_to", h.get("date_to"), "the address history")
    jobs = []
    if v("applicant.employer1_name"):
        jobs.append({"name": v("applicant.employer1_name"), "where": ", ".join(x for x in (v("applicant.employer1_city"), v("applicant.employer1_state")) if x),
                     "occupation": v("applicant.employer1_occupation"), "date_from": v("applicant.employer1_date_from")})
    jobs += [{"name": e.get("employer"), "where": ", ".join(x for x in (e.get("city"), e.get("state"), e.get("country")) if x), "occupation": e.get("occupation"),
              "date_from": e.get("date_from"), "date_to": e.get("date_to")}
             for e in reversed(_chronological(_entries(graph, "questionnaire.prior_employer", 9,
                                                       ("employer", "occupation", "city", "state", "country", "date_from", "date_to"))))
             if not e.get("date_to") or str(e["date_to"]) >= window]
    for n, j in enumerate(jobs[:3], start=1):
        put(f"asylum.job{n}_employer", ", ".join(x for x in (j.get("name"), j.get("where")) if x), "the employment history")
        put(f"asylum.job{n}_occupation", j.get("occupation"), "the employment history")
        put(f"asylum.job{n}_from", j.get("date_from"), "the employment history")
        put(f"asylum.job{n}_to", j.get("date_to"), "the employment history")
    for n, line in enumerate([ln for ln in str(v("asylum.schools") or "").splitlines() if ln.strip()][:4], start=1):
        name, kind, where, frm, to = (re.split(r"\s*\|\s*", line) + ["", "", "", "", ""])[:5]
        put(f"asylum.school{n}_name", name.upper(), "the schools list")
        put(f"asylum.school{n}_type", kind.upper(), "the schools list")
        put(f"asylum.school{n}_location", where.upper(), "the schools list")
        for part, mmyyyy in (("from", frm), ("to", to)):
            m = re.fullmatch(r"(\d{1,2})/(\d{4})", mmyyyy.strip())
            put(f"asylum.school{n}_{part}", f"{m.group(2)}-{int(m.group(1)):02d}-01" if m else None, "the schools list")
    # Part C, 6: a criminal record in the folder (a suggestion: the client and attorney answer)
    if v("questionnaire.yes.n400.p9_15b") or v("applicant.part9.arrested"):
        put("asylum.c6", "Yes", "the client said they were arrested")
    # Part D / E: the firm prepares it
    put("asylum.print_name", " ".join(x for x in (v("applicant.given_name"), v("applicant.middle_name"), v("applicant.family_name")) if x) or None, "the client's name")
    put("asylum.other_preparer", "Yes", "the firm prepared it")
    put("asylum.preparer_name", " ".join(x for x in (v("firm.preparer_given_name"), v("firm.preparer_family_name")) if x) or None, "the firm")
    fphone = re.sub(r"\D", "", str(v("firm.phone") or ""))
    if len(fphone) == 10:
        put("asylum.preparer_phone_area", fphone[:3], "the firm")
        put("asylum.preparer_phone_local", fphone[3:], "the firm")
    _supplements(graph, put)
    return graph


def _supplements(graph, put) -> None:
    """An explanation longer than its box: the box points to the supplement sheet, which carries it all."""
    for key, box in _BOX.items():
        text = str(_value(graph, f"asylum.{key}_explain") or "")
        if not text:
            continue
        if len(text) > explanation_capacity(box):
            _page, part, item = spot(key)
            graph.add_source(f"asylum.{key}_explain_full", "asylum.derive", "derived", "the explanation", text, 1.0, tier=3)
            fact = graph.get(f"asylum.{key}_explain")
            fact.value = "SEE SUPPLEMENT B / ATTACHED SHEET" + (f": PART {part}, QUESTION {item}." if part and item else ".")


def supplement_blocks(graph) -> list:
    """The Supplement B entries: each long explanation, whole. The form's own Supplement B page takes the first and copies of
    that page the rest; an entry longer than the page's box continues in the next box ("(continued)"), by fill/continuation.py."""
    from fill.continuation import Block

    blocks = []
    for key in _BOX:
        full = str(_value(graph, f"asylum.{key}_explain_full") or "")
        if not full:
            continue
        page, part, item = spot(key)
        blocks.append(Block(page, part, item, full, source=f"the explanation for question {item}" if item else "the explanation"))
    return blocks


def attach_supplements(client_dir: Path, graph) -> int:
    """Puts the long explanations on the filled I-589's own Supplement B page, and on copies of it after the form when they need more
    room; returns how many copies were added."""
    from fill.continuation import finish_part14

    pdf = client_dir / "i589_filled.pdf"
    blocks = supplement_blocks(graph)
    if not blocks or not pdf.exists():
        return 0
    v = lambda k: str(_value(graph, k) or "")  # noqa: E731
    layout = finish_part14(pdf, blocks, schema_path.path("template", "i589"), family=v("applicant.family_name"), given=v("applicant.given_name"),
                           middle=v("applicant.middle_name"), a_number=v("applicant.a_number"))
    return layout.copies


# -- the review app ---------------------------------------------------------------

def _graph(client_dir: Path, today: date | None = None):
    from review.state import reviewed_graph

    return derive(reviewed_graph(client_dir), today)


def _has_doc(client_dir: Path, *doc_types: str) -> list[str]:
    meta = json.loads((client_dir / "meta.json").read_text(encoding="utf-8")) if (client_dir / "meta.json").exists() else {}
    return [doc for doc, kind in (meta.get("classifications") or {}).items() if kind in doc_types]


def status(client_dir: Path, today: date | None = None) -> dict[str, Any]:
    graph = _graph(client_dir, today)
    questions = []
    for key, label, section, spec, required in QUESTIONS:
        fact = graph.get(key)
        full = graph.get(f"{key}_full")  # an explanation moved to the supplement: show the whole text
        sources = [{"doc": s.doc_id, "type": s.doc_type, "raw": s.raw_value} for s in (fact.sources if fact is not None else [])][:3]
        value = _value(graph, f"{key}_full") if full is not None else _value(graph, key)
        questions.append({"key": key, "label": label, "section": section, "input": spec, "required": required, "value": value, "sources": sources, "answered_by": getattr(getattr(fact, "review", None), "resolved_by", None),
                          "who": "the client" if section.startswith(("Part B", "Part C", "About", "Family")) else "the attorney"})
    return {"questions": questions, "one_year": one_year(graph, today), "where": where_to_file(graph),
            "problems": problems(client_dir, today, graph=graph, questions=questions)}


@producer(OFFICE)
def problems(client_dir: Path, today: date | None = None, graph=None, questions: list | None = None) -> list[str]:
    """What stops the I-589 packet from being final -- starting with what USCIS rejects outright."""
    if questions is None:
        return status(client_dir, today)["problems"]
    today = today or clock.today()
    v = lambda k: _value(graph, k)  # noqa: E731
    s = settings()
    out = []
    blank = [k for k in s["required_for_acceptance"] if v(k) is None]
    if blank:
        out.append("USCIS rejects an I-589 with these blank: " + ", ".join(_label(k) for k in blank) + ".")
    unexplained = [k for k in EXPLAINED if v(f"asylum.{k}") == "Yes" and not (v(f"asylum.{k}_explain") or v(f"asylum.{k}_explain_full"))]
    if (v("asylum.c2a") == "Yes" or v("asylum.c2b") == "Yes") and not (v("asylum.c2b_explain") or v("asylum.c2b_explain_full")):
        unexplained.append("c2a/c2b")
    if unexplained:
        out.append("A Yes without its explanation (USCIS rejects it): " + ", ".join(f"Part {k[0].upper()}, {k[1:].upper()}" for k in sorted(unexplained)) + ".")
    if not any(v(f"asylum.basis_{b}") for b in ("race", "religion", "nationality", "political", "social_group", "torture")) and not v("asylum.b1a_explain"):
        out.append("Part B, 1: no basis checked and no explanation. USCIS rejects an application missing why the client is applying.")
    if v("asylum.b1a") == "No" and v("asylum.b1b") == "No" and v("asylum.b4") == "No":
        out.append(held(ATTORNEY, "No past harm, no fear of harm and no fear of torture: there is no claim on the form as answered. The attorney reviews."))
    deadline = one_year(graph, today)
    if deadline["level"] == "late" and not (v("asylum.c5_explain") or v("asylum.c5_explain_full")):
        out.append(deadline["text"])
    elif deadline["level"] == "check":
        out.append(held(CLIENT, deadline["text"]))
    where = where_to_file(graph)
    if where["with"] == "unknown":
        out.append(where["text"])
    if v("asylum.ms_l") == "Yes":
        out.append("Ms. L. class member: write \"Ms. L Settlement Class Member\" (or \"Ms. L. Settlement QAFM\") at the top of page 1, pay no Pub. L. 119-21 "
                   "fee, and mail it: don't file online.")
    kids = int(re.sub(r"\D", "", str(v("asylum.total_children") or "0")) or 0)
    if kids > 4:
        out.append(f"{kids} children: the form has room for four. Add Form I-589 Supplement A for the others.")
    if v("asylum.spouse_included") == "Yes" and not _has_doc(client_dir, "marriage_certificate"):
        out.append(held(CLIENT, "The spouse is included: add the marriage certificate (proof of the relationship)."))
    if v("asylum.has_children") == "Yes" and not _has_doc(client_dir, "birth_certificate"):
        out.append(held(CLIENT, "Children are listed: add each included child's birth certificate (proof of the relationship)."))
    return out


def _label(key: str) -> str:
    return {"applicant.family_name": "last name (A.I, 4)", "asylum.residence_street": "street (A.I, 8)", "applicant.physical_city": "city (A.I, 8)",
            "applicant.physical_state": "state (A.I, 8)", "applicant.physical_zip": "ZIP (A.I, 8)", "applicant.dob": "date of birth (A.I, 12)",
            "asylum.birth_place": "city and country of birth (A.I, 13)"}.get(key, "Part " + key.split(".")[-1][0].upper() + ", " + key.split(".")[-1][1:].upper())


def answer(client_dir: Path, values: dict[str, Any], reviewer: str, role: str | None = None) -> dict[str, Any]:
    from review.state import record_decision

    specs = {key: (label, spec) for key, label, _section, spec, _req in QUESTIONS}
    for key, raw in values.items():
        if key not in specs:
            raise ValueError(f"{key} is not an I-589 question.")
        label, spec = specs[key]
        item = {"id": f"i589:{key}", "kind": "i589", "level": "review", "title": label, "group": "attorney", "actions": ["set", "blank"],
                "facts": [{"key": key, "input": spec}]}
        decision = {"action": "blank" if raw in ("", None) else "set", "values": {} if raw in ("", None) else {key: raw},
                    "reviewer": reviewer, **({"role": role} if role else {}), "note": "I-589 question"}
        record_decision(client_dir, item, decision)
    return status(client_dir)
