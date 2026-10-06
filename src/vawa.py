"""The VAWA self-petition: Form I-360 (edition 01/20/25) filed by the abused
spouse, child or parent of a U.S. citizen or permanent resident, without the
abuser's knowledge or consent -- the second I-360 classification the system
fills, beside the Special Immigrant Juvenile (src/i360.py, untouched). Its own
filing ("vawa"), its own map of the same template (schemas/packets/companion_forms.json
"i360_vawa": Part 2 I/J/K, Part 1 item 7, Part 10), never Part 8.

Who (INA 204(a)(1)(A)(iii), (iv), (vii) and (B)(ii), (iii), 8 U.S.C. 1154,
govinfo 2023 edition read 2026-10-02; 8 CFR 204.2(c) and (e), eCFR as of
09/30/2026; USCIS Policy Manual Vol. 3 Part D, chapters 2-4, current as of
09/23/2026; Form I-360 Instructions 01/20/25, pages 6-7):
  - the abuser is a U.S. citizen or permanent resident (a parent: only of a
    U.S. citizen son or daughter 21 or older, INA 204(a)(1)(A)(vii); PM 3.D.2.B.4);
  - the client was battered or subjected to extreme cruelty by the abuser during
    the relationship (a spouse: also abuse of the spouse's child);
  - the client lives or lived with the abuser (no length, anywhere; PM 3.D.2.F);
  - good moral character (presumed under 14; police clearances for each place
    lived 6 months or more in the 3 years before filing; PM 3.D.2.G);
  - a spouse married in good faith (INA 204(a)(1)(A)(iii)(I)(aa); PM 3.D.2.C);
  - the 2-year rules: a spouse within 2 years of a divorce connected to the
    abuse, or of a U.S. citizen spouse's death; a parent within 2 years of the
    citizen son's or daughter's death; anyone within 2 years of the abuser's loss
    of status related to domestic violence (INA 204(a)(1)(A)(iii)(II)(aa)(CC),
    (iv), (vii)(I), (B)(ii)(II)(aa)(CC), (iii)); no tolling (PM 3.D.3);
  - a child unmarried and under 21, or under 25 when the abuse was at least one
    central reason for the delay (INA 204(a)(1)(D)(v); PM 3.D.3.G);
  - living abroad only when the abuser is a U.S. government employee, in the
    uniformed services, or abused the client in the U.S. (INA 204(a)(1)(A)(v),
    (B)(iv); Instructions page 6).

Confidentiality (8 U.S.C. 1367): USCIS mails the safe address in Part 1, item 7
(Instructions page 6: a P.O. box, a friend, the attorney, an organization); the
G-28 gives it as the client's mailing address (G-28 Instructions, item 13); an
I-485 gives it as the mailing address (I-485 Instructions 09/18/26, item 7).
Here the packet uses the attorney's safe address, or the case's office, and never
asks for the abuser's address at all (only the last address shared, which the
form itself asks in Part 10, item 10).

Where (uscis.gov "Direct Filing Addresses for Form I-360", updated 11/18/2025,
and "Filing Addresses for Certain Forms Filed in Connection With a VAWA, T, or U
Visa Application/Petition", updated 02/05/2026, both read 2026-10-02): the
"Attn: 1367" lockbox for the client's state (schemas/law/uscis_lockboxes_vawa.json);
the I-485 goes to the same address (uscis.gov/i-485-addresses, updated 12/01/2025).
Fee (G-1055 10/01/26): $0 for the VAWA I-360 and $0 for a VAWA self-petitioner's
I-485. The I-485 in the same envelope: a citizen's spouse, child or parent at any
time (immediate relatives), a permanent resident's spouse or child when a visa
is available (PM 3.D.4.A; uscis.gov "Green Card for VAWA Self-Petitioner",
updated 06/26/2026). All wording here is DRAFT for the attorney.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from filing_questions import DATE, LINES, TEXT, YES_NO, lockbox, plus_years, putter, state_of, us, value
from filing_questions import iso as _d
import clock
import schema_path
from holders import ATTORNEY, CLIENT, held, producer
from holders import OFFICE as OFFICE_HOLDS  # this module's own OFFICE is the safe-address option

TITLE = "VAWA self-petition (I-360)"
CHART = "uscis_lockboxes_vawa"
SPOUSE, CHILD, PARENT = "Spouse", "Child", "Parent"
USC_BORN, USC_ABROAD, USC_NATURALIZED = ("U.S. citizen born in the United States", "U.S. citizen born abroad to U.S. citizen parents",
                                         "U.S. citizen through naturalization")
LPR, OTHER = "Lawful permanent resident", "Other"
CITIZEN = (USC_BORN, USC_ABROAD, USC_NATURALIZED)
STILL_MARRIED, DIVORCED, DIED = "Still married", "Divorced or annulled", "The abuser died"
OFFICE, ELSEWHERE = "The office's address", "Another safe address"
ABROAD = ["The abuser is a U.S. government employee", "The abuser is a member of the U.S. uniformed services",
          "The abuse happened in the United States", "None of these"]
SAFE_PARTS = ("in_care_of", "street", "unit_type", "apt", "city", "state", "zip")


def _is(graph, key: str, *values: str) -> bool:
    return value(graph, key) in values


def filed_already(graph) -> bool:
    """An I-360 already filed for this client (a receipt or approval in the folder): what is left is the I-485 on it."""
    import journey

    return any(n["form"] == "I-360" and n["kind"] in ("receipt", "approval", "transfer", "biometrics", "rfe", "notice")
               for n in journey.notices(graph))


def concurrent(graph) -> bool:
    return value(graph, "vawa.concurrent_i485") == "Yes" and not filed_already(graph)


def variant(graph) -> str | None:
    """Which packet (schemas/packets/vawa.json variants): the I-485 alone on an I-360 already filed ("i485_only"),
    the I-360 with the I-485 in the same envelope ("concurrent"), or the I-360 alone (None)."""
    if filed_already(graph):
        return "i485_only"
    return "concurrent" if concurrent(graph) else None


def _petition(graph) -> bool:
    return not filed_already(graph)


SECTIONS = [
    ("The classification and the abuser (Part 2, Part 10)", "the attorney", [
        ("vawa.classification", "Part 2: the client is the abuser's (I. spouse, J. child, K. parent)",
         {"type": "choice", "options": [SPOUSE, CHILD, PARENT]}, True),
        ("vawa.abuser_status", "Part 10, 5: the abuser is now, or was, a", {"type": "choice", "options": [*CITIZEN, LPR, OTHER]}, True),
        ("vawa.abuser_status_explain", "Part 10, 5.E: if other, the explanation", TEXT, False),
        ("vawa.abuser_a_number", "Part 10, 5.C or 5.D: the abuser's A-Number (if known)", TEXT, False),
        ("vawa.abuser_family_name", "Part 10, 1: the abuser's family name", TEXT, True),
        ("vawa.abuser_given_name", "Part 10, 1: the abuser's given name", TEXT, True),
        ("vawa.abuser_middle_name", "Part 10, 1: the abuser's middle name", TEXT, False),
        ("vawa.abuser_dob", "Part 10, 2: the abuser's date of birth", DATE, False),
        ("vawa.abuser_country_of_birth", "Part 10, 3: the abuser's country of birth", TEXT, False),
        ("vawa.abuser_died_on", "Part 10, 4: the abuser's date of death (only if the abuser died)", DATE, False),
        ("vawa.abuser_lost_status_on", "The date the abuser lost or gave up citizenship or permanent residence (only if that happened)", DATE, False),
        ("vawa.abuser_lost_status_dv", "That loss of status was related to an incident of domestic violence?", YES_NO, False),
        ("vawa.times_married", "Part 10, 6: how many times the client has been married (None if never)", TEXT, True),
        ("vawa.abuser_times_married", "Part 10, 7: how many times the abuser was married (if known)", TEXT, False),
    ], _petition),
    ("The marriage (a spouse)", "the attorney", [
        ("vawa.marriage_date", "Part 10, 8.A: the date the client married the abuser", DATE, True),
        ("vawa.marriage_place", "Part 10, 8.B: where they married (city, state or country)", TEXT, True),
        ("vawa.marriage_status", "The marriage now", {"type": "choice", "options": [STILL_MARRIED, DIVORCED, DIED]}, True),
        ("vawa.marriage_ended_on", "The date of the divorce, annulment or death", DATE, False),
        ("vawa.divorce_connected", "A divorce or annulment: connected to the battery or extreme cruelty?", YES_NO, False),
        ("vawa.good_faith", "The client entered the marriage in good faith (not to get an immigration benefit)?", YES_NO, True),
        ("vawa.remarried", "Has the client married someone else since?", YES_NO, True),
        ("vawa.married_in_proceedings", "Did they marry while the client was in removal proceedings?", YES_NO, True),
    ], lambda g: _petition(g) and _is(g, "vawa.classification", SPOUSE)),
    ("A child 21 or older", "the attorney", [
        ("vawa.delay_central_reason", "Under 25: was the abuse at least one central reason the petition wasn't filed before 21?", YES_NO, True),
    ], lambda g: _petition(g) and _is(g, "vawa.classification", CHILD) and (age(value(g, "applicant.dob"), clock.today()) or 0) >= 21),
    ("Living with the abuser (Part 10, 9 to 11)", "the client", [
        ("vawa.lived_from", "Part 10, 9: lived with the abuser from", DATE, True),
        ("vawa.lived_to", "Part 10, 9: to (blank while they still live together)", DATE, False),
        ("vawa.lives_with_abuser", "Does the client still live with the abuser?", YES_NO, True),
        ("vawa.last_street", "Part 10, 10: the last address they lived at together: street", TEXT, True),
        ("vawa.last_apt", "Part 10, 10: apartment number", TEXT, False),
        ("vawa.last_city", "Part 10, 10: city or town", TEXT, True),
        ("vawa.last_state", "Part 10, 10: state (two letters)", TEXT, False),
        ("vawa.last_zip", "Part 10, 10: ZIP code", TEXT, False),
        ("vawa.last_country", "Part 10, 10: country (only outside the U.S.)", TEXT, False),
        ("vawa.last_from", "Part 10, 11: lived together at that address from", DATE, True),
        ("vawa.last_to", "Part 10, 11: to (blank while they still live there together)", DATE, False),
    ], _petition),
    ("The abuse and good moral character", "the attorney", [
        ("vawa.abused", "Battered or subjected to extreme cruelty by the abuser during the relationship?", YES_NO, True),
        ("vawa.abused_who", "Who the abuser battered or subjected to extreme cruelty",
         {"type": "choice", "options": ["The client", "The client's child", "The client and the client's child"]}, True),
        ("vawa.gmc_concern", "Anything that could bar good moral character: an arrest, a charge, a conviction, false testimony?", YES_NO, True),
    ], _petition),
    # The declaration's topics (Form I-360 Instructions, page 7: schemas/packets/vawa.json), in the client's own words: the
    # Declaration card assembles the declaration from these answers, word for word (src/drafting.py).
    ("The client's account, in their own words (for the declaration)", "the client", [
        ("vawa.account_relationship", "In the client's own words: how they met the abuser, and their relationship", LINES, False),
        ("vawa.account_residence", "In the client's own words: living with the abuser (where, and for how long)", LINES, False),
        ("vawa.account_abuse", "In the client's own words: the abuse (what happened, when, and how it affected them)", LINES, False),
        ("vawa.account_good_faith", "In the client's own words, a spouse only: why they married the abuser", LINES, False),
        ("vawa.account_character", "In the client's own words: their life and conduct (for good moral character)", LINES, False),
    ], _petition),
    ("Where the client lives now (Part 4)", "the attorney", [
        ("vawa.in_us", "Does the client live in the United States now?", YES_NO, True),
        ("vawa.abroad_basis", "Living abroad: which applies", {"type": "choice", "options": ABROAD}, False),
        ("applicant.part9.in_removal_proceedings", "Part 4, 5: in removal proceedings (immigration court)?", YES_NO, True),
        ("applicant.part9.worked_without_authorization", "Part 4, 6: ever worked in the U.S. without permission?", YES_NO, True),
    ]),
    ("The safe mailing address (Part 1, 7)", "the attorney", [
        ("vawa.safe_address", "Where USCIS mails the client: the safe address", {"type": "choice", "options": [OFFICE, ELSEWHERE]}, True),
    ]),
    ("The other safe address", "the attorney", [
        ("vawa.safe_in_care_of", "Safe address: in care of (a person or an organization)", TEXT, False),
        ("vawa.safe_street", "Safe address: street number and name, or P.O. box", TEXT, True),
        ("vawa.safe_unit_type", "Safe address: apartment, suite or floor", {"type": "choice", "options": ["APT", "STE", "FLR"]}, False),
        ("vawa.safe_apt", "Safe address: its number", TEXT, False),
        ("vawa.safe_city", "Safe address: city or town", TEXT, True),
        ("vawa.safe_state", "Safe address: state (two letters)", TEXT, True),
        ("vawa.safe_zip", "Safe address: ZIP code", TEXT, True),
    ], lambda g: _is(g, "vawa.safe_address", ELSEWHERE)),
    ("Work permit and the green card application", "the attorney", [
        ("vawa.request_ead", "Part 10, 12: living in the U.S. and wants the work permit (c)(31) when the petition is approved?", YES_NO, True),
        ("vawa.concurrent_i485", "File the green card application (I-485) in the same envelope?", YES_NO, True),
    ], _petition),
    ("A permanent resident abuser and the I-485", "the attorney", [
        ("vawa.visa_available", "A visa is available now: the F2A date is current this month (the attorney checked the Visa Bulletin)?", YES_NO, True),
    ], lambda g: _is(g, "vawa.abuser_status", LPR) and (concurrent(g) or filed_already(g))),
]
MORE_QUESTIONS = "More questions appear once the classification, the safe address and the I-485 choice are answered."


def age(dob: Any, today: date) -> int | None:
    born = _d(dob)
    return None if born is None else today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _the_day_before(d: date) -> date:
    return d - timedelta(days=1)


def deadline(graph, today: date) -> dict[str, Any] | None:
    """The last day the self-petition can be filed, when the law sets one -- the earliest of:
    2 years after a divorce connected to the abuse, after a citizen spouse's or son's/daughter's death, or after the abuser's
    loss of status (INA 204(a)(1)(A)(iii)(II)(aa)(CC), (iv), (vii)(I), (B)(ii)(II)(aa)(CC), (iii)); a child: the day before
    the 21st birthday, or the 25th when the abuse was one central reason for the delay (INA 204(a)(1)(D)(v)).
    The 2-year periods can't be tolled (USCIS Policy Manual 3.D.3). The day before each anniversary, to be safe.
    {"date", "why", "cite"} or None."""
    if filed_already(graph):
        return None
    v = lambda k: value(graph, k)  # noqa: E731
    klass, citizen = v("vawa.classification"), v("vawa.abuser_status") in CITIZEN
    found = []
    ended = _d(v("vawa.marriage_ended_on"))
    if klass == SPOUSE and ended and v("vawa.marriage_status") == DIVORCED:
        found.append((_the_day_before(plus_years(ended, 2)), f"2 years after the divorce or annulment of {us(ended)}", "INA 204(a)(1)(A)(iii)(II)(aa)(CC)(ccc), (B)(ii)(II)(aa)(CC)(bbb)"))
    died = _d(v("vawa.abuser_died_on")) or (ended if klass == SPOUSE and v("vawa.marriage_status") == DIED else None)
    if died and citizen and klass in (SPOUSE, PARENT):
        found.append((_the_day_before(plus_years(died, 2)), f"2 years after the U.S. citizen {'spouse' if klass == SPOUSE else 'son or daughter'} died on {us(died)}",
                      "INA 204(a)(1)(A)(iii)(II)(aa)(CC)(aaa), (vii)(I)"))
    lost = _d(v("vawa.abuser_lost_status_on"))
    if lost:
        found.append((_the_day_before(plus_years(lost, 2)), f"2 years after the abuser lost status on {us(lost)}",
                      "INA 204(a)(1)(A)(iii)(II)(aa)(CC)(bbb), (iv), (vii)(I), (B)(ii)(II)(aa)(CC)(aaa), (iii)"))
    dob = _d(v("applicant.dob"))
    if klass == CHILD and dob:
        if today < plus_years(dob, 21):
            found.append((_the_day_before(plus_years(dob, 21)), f"the day before the client turns 21 ({us(plus_years(dob, 21))})", "INA 204(a)(1)(A)(iv), (B)(iii)"))
        else:
            found.append((_the_day_before(plus_years(dob, 25)), f"before 25 ({us(plus_years(dob, 25))}), and only if the abuse was one central reason for the delay",
                          "INA 204(a)(1)(D)(v)"))
    if not found:
        return None
    when, why, cite = min(found)
    return {"date": when, "why": why, "cite": cite}


def safe_address(graph) -> dict[str, Any] | None:
    """The address USCIS mails ({part: value}): the office's, or the one the attorney entered; None when not complete."""
    choice = value(graph, "vawa.safe_address")
    if choice == OFFICE:
        parts = {"in_care_of": value(graph, "firm.business_name"), "street": value(graph, "firm.street"), "city": value(graph, "firm.city"),
                 "state": value(graph, "firm.state"), "zip": value(graph, "firm.zip")}
    elif choice == ELSEWHERE:
        parts = {p: value(graph, f"vawa.safe_{p}") for p in SAFE_PARTS}
    else:
        return None
    return parts if all(parts.get(p) for p in ("street", "city", "state", "zip")) else None


def _norm(x: Any) -> str:
    return "".join(ch for ch in str(x or "").upper() if ch.isalnum())


def same_as_shared(graph) -> bool:
    """The safe address is the last address the client shared with the abuser (street and city, or street and ZIP)."""
    safe = safe_address(graph)
    if not safe or not value(graph, "vawa.last_street"):
        return False
    street = _norm(safe["street"]) == _norm(value(graph, "vawa.last_street"))
    return street and (_norm(safe["city"]) == _norm(value(graph, "vawa.last_city")) or (_norm(safe["zip"])[:5] == _norm(value(graph, "vawa.last_zip"))[:5] != ""))


def derive(graph, today: date):
    """The form's own facts: the mailing address (the safe one), Part 10's boxes as the form asks them, Part 4's I-485 answers."""
    put = putter(graph, "vawa.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    safe = safe_address(graph)
    if safe:
        why = "the office's address (the safe mailing address chosen)" if v("vawa.safe_address") == OFFICE else "the safe mailing address the attorney entered"
        for part in SAFE_PARTS:
            put(f"vawa.mail_{part}", safe.get(part), why)
    klass = v("vawa.classification")
    spouse = klass == SPOUSE
    put("vawa.form_marriage_date", us(_d(v("vawa.marriage_date"))) if spouse and _d(v("vawa.marriage_date")) else ("N/A" if klass in (CHILD, PARENT) else None),
        "Part 10, 8.A: the marriage date (N/A for a child or a parent, as the form says)")
    put("vawa.form_marriage_place", v("vawa.marriage_place") if spouse else ("N/A" if klass in (CHILD, PARENT) else None),
        "Part 10, 8.B: where they married (N/A for a child or a parent)")
    put("vawa.last_unit_type", "APT" if v("vawa.last_apt") else None, "Part 10, 10: an apartment number was given")
    status = v("vawa.abuser_status")
    put("vawa.abuser_a_number_naturalized" if status == USC_NATURALIZED else "vawa.abuser_a_number_lpr" if status == LPR else "vawa.abuser_a_number_other",
        v("vawa.abuser_a_number"), "the abuser's A-Number, on the line of the status chosen")
    if not filed_already(graph):
        both = concurrent(graph)
        put("vawa.other_petitions", "Yes" if both else "No", "Part 4, 4.A: an I-485 goes with it" if both else "Part 4, 4.A: the I-360 goes alone")
        put("vawa.other_petitions_count", "1" if both else None, "Part 4, 4.B: the I-485")
        put("vawa.i485_attached", "Yes" if both else "No", "Part 4, 7: the I-485 in the same envelope" if both else "Part 4, 7: no I-485 with it")
    forms = "I-485" if filed_already(graph) else ("I-360, I-485" if concurrent(graph) else "I-360")
    put("companion.g28_forms_vawa", forms, "the forms in this packet")
    return graph


def fee(graph, today: date) -> tuple[int | None, str]:
    """No fee: Form G-1055 (10/01/26) lists $0 for a VAWA I-360 and $0 for a VAWA self-petitioner's I-485."""
    import fees

    paper = fees.load(today).get("paper") or {}
    if filed_already(graph):
        return paper.get("i485_vawa"), "a VAWA self-petitioner's I-485: no fee (G-1055)"
    return paper.get("i360_vawa"), "a VAWA I-360: no fee" + ("; its I-485 has no fee either" if concurrent(graph) else "") + " (G-1055)"


def mail_to(graph) -> tuple[list[str] | None, str | None]:
    return lockbox(CHART, state_of(graph))


def notes(graph, today: date) -> list[dict[str, str]]:
    lines, box = mail_to(graph)
    amount, why = fee(graph, today)
    out = [{"level": "info", "title": "Confidential", "text": "USCIS may not use information from the abuser alone, and keeps the case confidential (8 U.S.C. 1367). "
                                                              "Every notice goes to the safe address; nothing in this packet asks for the abuser's address."},
           {"level": "info", "title": "Fee and where", "text": f"${amount or 0}: {why}. Mailed to " + (" / ".join(lines) + f" ({box} lockbox, by the client's state)" if lines
                                                                                       else "the lockbox for the client's state (no state in the case yet)") + "."}]
    d = deadline(graph, today)
    if d:
        out.append({"level": "warn" if (d["date"] - today).days <= 60 else "info", "title": "File by",
                    "text": f"{us(d['date'])}: {d['why']} ({d['cite']}). No extension is possible (USCIS Policy Manual 3.D.3)."})
    if value(graph, "vawa.married_in_proceedings") == "Yes":
        out.append({"level": "warn", "title": "Married during removal proceedings",
                    "text": "Ask for the exemption in writing with the I-360, with clear and convincing evidence of a good faith marriage (INA 204(g); "
                            "USCIS Policy Manual 3.D.3.C)."})
    if value(graph, "vawa.gmc_concern") == "Yes":
        out.append({"level": "warn", "title": "Good moral character",
                    "text": "Add the arrest reports and certified court dispositions. A waivable act connected to the abuse may not bar good moral character "
                            "(INA 204(a)(1)(C)); the attorney decides."})
    if concurrent(graph) or filed_already(graph):
        out.append({"level": "info", "title": "The I-485", "text": "Part 2, 3.a (VAWA), Part 3, 1.d (no Affidavit of Support) and Part 9, 56 (no public charge) are "
                                                                   "checked; its mailing address is the safe address. Add the medical exam (Form I-693) and photos."})
    return out


@producer(OFFICE_HOLDS)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    """What stops the packet: no safe address, a ground the sources say makes the client ineligible, the I-485's conditions."""
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    if v("vawa.safe_address") and not safe_address(graph):
        out.append("No complete safe mailing address yet: enter the street, city, state and ZIP code (or choose the office's address), or check the "
                   "office's address in Settings. Without it USCIS would write to the client's home (Form I-360 Instructions, page 6).")
    elif not v("vawa.safe_address"):
        out.append("No safe mailing address chosen: choose the office's address or enter another safe address (Part 1, 7).")
    if same_as_shared(graph):
        out.append("The safe address is the address the client shared with the abuser: choose a different safe address.")
    if mail_to(graph)[0] is None:
        out.append(f"The client's state ({state_of(graph) or 'none in the case'}) isn't on USCIS's chart for VAWA filings: check the address on uscis.gov "
                   "(Filing Addresses for Certain Forms Filed in Connection With a VAWA, T, or U Visa Application/Petition).")
    if not filed_already(graph):
        out += _eligibility(graph, today)
    if concurrent(graph) or filed_already(graph):
        if v("vawa.in_us") == "No":
            out.append(held(ATTORNEY, "Living outside the U.S.: no I-485 can be filed; the approved petition goes to the consulate (uscis.gov, I-360 filing addresses)."))
        if v("vawa.abuser_status") == LPR and v("vawa.visa_available") != "Yes":
            out.append("The abuser is a permanent resident: the I-485 waits until a visa is available (the F2A date current). File the I-360 alone "
                       "for now (USCIS Policy Manual 3.D.4.A).")
        import journey

        out += journey.i485_court_problem(client_dir, graph)
    return out


@producer(ATTORNEY)
def _eligibility(graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    klass, status = v("vawa.classification"), v("vawa.abuser_status")
    citizen = status in CITIZEN
    if klass == PARENT and status in (LPR, OTHER):
        out.append("A parent can self-petition only against a U.S. citizen son or daughter (INA 204(a)(1)(A)(vii); USCIS Policy Manual 3.D.2.B.4).")
    if klass == PARENT and _d(v("vawa.abuser_dob")) and (age(v("vawa.abuser_dob"), today) or 0) < 21:
        out.append("The abusive son or daughter must be 21 or older when the petition is filed (INA 204(a)(1)(A)(vii); Form I-360 Instructions, page 6).")
    if klass == PARENT and not v("vawa.abuser_dob"):
        out.append(held(CLIENT, "A parent's petition: enter the son's or daughter's date of birth (Part 10, 2). They must be 21 or older."))
    if status == OTHER and not v("vawa.abuser_lost_status_on"):
        out.append(held(CLIENT, "The abuser must be a U.S. citizen or permanent resident, or have lost that status within 2 years for a reason related to domestic "
                   "violence: enter the date it was lost (INA 204(a)(1)(A)(iii)(II)(aa)(CC)(bbb), (B)(ii)(II)(aa)(CC)(aaa))."))
    if v("vawa.abuser_lost_status_on") and v("vawa.abuser_lost_status_dv") == "No":
        out.append("The abuser lost status for a reason not related to domestic violence: the client can't self-petition on that relationship "
                   "(USCIS Policy Manual 3.D.3.E).")
    if v("vawa.abused") == "No":
        out.append("No battery or extreme cruelty by the abuser: the petition can't be approved (INA 204(a)(1)(A)(iii)(I)(bb), (iv), (vii)(V)).")
    if klass in (CHILD, PARENT) and v("vawa.abused_who") in ("The client's child",):
        out.append("A child or a parent self-petitions on abuse of the client; the abuse of a child counts only for a spouse (8 CFR 204.2(e)(1)(i)(E); "
                   "USCIS Policy Manual 3.D.2.E).")
    if klass == SPOUSE:
        if v("vawa.good_faith") == "No":
            out.append("The marriage wasn't entered in good faith: the petition can't be approved (INA 204(a)(1)(A)(iii)(I)(aa); 8 CFR 204.2(c)(1)(ix)).")
        if v("vawa.remarried") == "Yes":
            out.append("The client married someone else: a spouse's petition is denied for a remarriage before the decision (8 CFR 204.2(c)(1)(ii); "
                       "USCIS Policy Manual 3.D.3.B).")
        ended, how = _d(v("vawa.marriage_ended_on")), v("vawa.marriage_status")
        if how in (DIVORCED, DIED) and not ended:
            out.append(held(CLIENT, "The marriage ended: enter the date of the divorce, annulment or death. A petition must be filed within 2 years of it."))
        if how == DIVORCED and v("vawa.divorce_connected") == "No":
            out.append("A divorce not connected to the abuse: a spouse can't self-petition after the marriage ended (8 CFR 204.2(c)(1)(ii); "
                       "INA 204(a)(1)(A)(iii)(II)(aa)(CC)(ccc)).")
        if how == DIVORCED and not v("vawa.divorce_connected"):
            out.append(held(CLIENT, "A divorce: answer whether it was connected to the battery or extreme cruelty (it must be)."))
        if how == DIED and not citizen:
            out.append("A permanent resident spouse died before the petition was filed: the client can't self-petition (USCIS Policy Manual 3.D.3.D.2).")
    if klass == CHILD:
        if v("applicant.marital_status") in ("Married",):
            out.append("A self-petitioning child must be unmarried when the petition is filed and approved (8 CFR 204.2(e)(1)(ii); USCIS Policy Manual 3.D.3.B.1).")
        if _d(v("vawa.abuser_died_on")):
            out.append("The abusive parent died before the petition was filed: a child can't self-petition (USCIS Policy Manual 3.D.3.D).")
        years = age(v("applicant.dob"), today)
        if years is not None and years >= 25:
            out.append("25 or older: too late to self-petition as a child (INA 204(a)(1)(D)(v)).")
        elif years is not None and years >= 21 and v("vawa.delay_central_reason") == "No":
            out.append("21 or older: a child can self-petition until 25 only when the abuse was at least one central reason for the delay "
                       "(INA 204(a)(1)(D)(v); USCIS Policy Manual 3.D.3.G).")
    if klass == PARENT and _d(v("vawa.abuser_died_on")) and not citizen:
        out.append("A parent self-petitions only against a U.S. citizen son or daughter (INA 204(a)(1)(A)(vii)).")
    if v("vawa.in_us") == "No" and v("vawa.abroad_basis") in (None, "None of these"):
        out.append("Living abroad: a self-petition can be filed from outside the U.S. only when the abuser is a U.S. government employee, in the U.S. "
                   "uniformed services, or abused the client in the U.S. (INA 204(a)(1)(A)(v), (B)(iv); Form I-360 Instructions, page 6).")
    d = deadline(graph, today)
    if d and today > d["date"]:
        out.append(f"Too late: the self-petition had to be filed by {us(d['date'])} ({d['why']}; {d['cite']}). No extension is possible "
                   "(USCIS Policy Manual 3.D.3). Ask the attorney.")
    return out


def letter(graph, today: date) -> dict[str, Any]:
    """The cover letter: the classification, the lockbox for the client's state, no fee."""
    import fees

    edition = fees.load(today).get("edition") or "current"
    klass = (value(graph, "vawa.classification") or "").lower() or "[spouse, child or parent]"
    lines, _box = mail_to(graph)
    if filed_already(graph):
        re_lines = ["Application: I-485 Application to Register Permanent Residence or Adjust Status",
                    f"Based on an I-360 VAWA self-petition ({klass}). Protected under 8 U.S.C. 1367"]
        text = f"No filing fee is required for this Form I-485 filed by a VAWA self-petitioner (Form G-1055, edition {edition})."
    else:
        both = concurrent(graph)
        re_lines = ["Petition: I-360 Petition for Amerasian, Widow(er), or Special Immigrant", f"Classification: VAWA self-petitioning {klass}"]
        re_lines += ["Filed concurrently: I-485 Application to Register Permanent Residence or Adjust Status"] if both else []
        re_lines += ["Protected under 8 U.S.C. 1367"]
        text = ("No filing fee is required for this Form I-360 filed as a VAWA self-petition" + (" or for the Form I-485 filed with it" if both else "")
                + f" (Form G-1055, edition {edition}).")
    return {"re_lines": re_lines, "mail_to": lines or ["USCIS", "ATTN: 1367", "[the lockbox for the client's state]"], "fees": text, "no_payment": True}


# -- the I-485, when it goes with the petition or after it ------------------------

I485_OUTPUT = "i485_vawa_filled.pdf"
CATEGORY = {SPOUSE: "VAWA self-petitioning spouse (I-360)", CHILD: "VAWA self-petitioning child (I-360)", PARENT: "VAWA self-petitioning parent (I-360)"}


def i485_graph(graph):
    """The case as the VAWA I-485 sees it: Part 2, 3.a's VAWA box, Part 3, 1.d (VAWA: no Affidavit of Support), Part 9, 56 (VAWA: no
    public charge), and the safe address as the mailing address (Part 1, 18; I-485 Instructions 09/18/26, item 7). A copy: the case
    itself, and its own I-485, keep the client's own answers."""
    from factgraph import FactGraph

    g = FactGraph.from_dict(graph.to_dict())
    who = "VAWA self-petition (src/vawa.py)"
    klass = value(graph, "vawa.classification")
    if klass in CATEGORY:
        g.set_by_review("applicant.filing_category", CATEGORY[klass], who, "Part 2, 3.a: VAWA self-petitioner, Form I-360")
    g.set_by_review("applicant.affidavit_of_support_exemption", "VAWA self-petitioner", who, "Part 3, 1.d: applying as a VAWA self-petitioner")
    g.set_by_review("applicant.public_charge_exemption", "VAWA", who, "Part 9, 56: VAWA self-petitioner (Form I-360)")
    safe = safe_address(graph)
    if safe:
        for part in SAFE_PARTS:
            if safe.get(part):
                g.set_by_review(f"applicant.mailing_{part}", safe[part], who, "the safe mailing address")
            elif g.get(f"applicant.mailing_{part}") is not None:
                g.blank_by_review(f"applicant.mailing_{part}", who, "the safe mailing address has no such part")
        g.set_by_review("applicant.mailing_same_as_physical", "No", who, "Part 1, 18: the safe mailing address, not the home")
    return g


def render(client_dir: Path, graph, today: date) -> None:
    """Fills the VAWA I-485 (i485_vawa_filled.pdf) when this packet carries one: the same map and template as every I-485
    (schemas/forms/i485/field_map.json), from i485_graph(). A value too long for its box is left out (the paralegal enters it), and
    Part 14 entries beyond the form go on continuation sheets, as for the main I-485 (src/batch.py)."""
    if variant(graph) is None:
        return
    from batch import _part14_continuation
    from fill.field_map import load_field_map, map_facts_to_fields
    from fill.fill_pdf import field_max_lengths, fill_pdf

    schemas = schema_path.ROOT
    field_map = load_field_map(schema_path.path("field_map", "i485", schemas))
    template = schema_path.path("template", "i485", schemas)
    g = i485_graph(graph)
    values = map_facts_to_fields(g, field_map).values
    limits = field_max_lengths(template)
    values = {k: x for k, x in values.items() if not (isinstance(x, str) and len(x) > limits.get(k, 10**6))}
    out = Path(client_dir) / I485_OUTPUT
    fill_pdf(template, values, out)
    _part14_continuation(g, field_map, out, template)


# The abuser's proof of status, as the packet's "abuser_status" exhibit takes it (schemas/packets/vawa.json).
STATUS_PROOF = ("us_passport", "citizenship_certificate", "green_card", "us_birth_certificate")


def document_notes(client_dir: Path, graph) -> list[dict[str, str]]:
    """Whose documents are in the folder (src/documents.py person): the abuser's proof of citizenship or residence --
    the record's person first, a card nobody has placed counting as the abuser's by its type, as the packet does --
    and the client's own. A card a reviewer marked as the client's is never shown as the abuser's."""
    import documents

    abuser = {SPOUSE: "spouse", CHILD: "parent", PARENT: "child"}.get(value(graph, "vawa.classification"))
    return documents.person_note(client_dir, "Whose documents", [
        ("The abuser's proof of citizenship or residence", ["petitioner", *([abuser] if abuser else [])], STATUS_PROOF, True),
        ("The client's documents", ["applicant"], None, False),
    ])
