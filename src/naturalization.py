"""Naturalization: Form N-400 for a client who is a permanent resident --
whether the firm got them the green card (an SIJ or family case reaching
its last stage) or they came to the firm for citizenship alone.
schemas/packets/n400.json for the packet, schemas/packets/companion_forms.json
("n400") for the form, schemas/law/naturalization.json for the rules.

What a lawyer asks first, answered from the case:

  - WHEN can the client file? The green-card date plus 5 years (3 for the
    spouse of a U.S. citizen) less 90 days, after 3 months in the state,
    at 18 or older -- and only once the trips abroad leave enough days in
    the U.S. (913 of the last 5 years; 548 of 3);
  - which trips need explaining (more than 6 months) and which broke
    continuous residence (a year or more);
  - English/civics exemptions (50/20, 55/15, 65/20), Selective Service,
    a fee reduction (income at or below 400% of the poverty guidelines);
  - what the folder says that the answers must match: a Notice to Appear
    (item 20, and no naturalization while removal proceedings are
    pending), a criminal record (item 15).

The form itself is filled from the same reviewed case as every other
filing: names, A-Number, addresses, jobs, children and biographic facts
are already in the case; the 5-year histories come from the client's
questionnaire; trips and crimes are typed one per line in the review app.
Part 9 is the client's to answer and the attorney's to review -- nothing
here answers it for them.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import clock
import schema_path
from holders import ATTORNEY, CLIENT, OFFICE, held, of_first, producer

SETTINGS = schema_path.path("law", "naturalization")
YES_NO = {"type": "choice", "options": ["Yes", "No"]}
TEXT = {"type": "text"}
DATE = {"type": "date"}
LINES = {"type": "text", "multiline": True}
BASES = ["General", "Spouse of U.S. citizen", "VAWA", "Spouse of U.S. citizen working abroad", "Military (hostilities)", "Military (one year)", "Other"]

# Part 9: (item, the question as the form asks it, short enough for a review card)
PART9 = [
    ("1", "EVER claimed to be a U.S. citizen (in writing or any other way)?"),
    ("2", "EVER registered to vote or voted in a Federal, state or local election? (a lawful vote in a local election open to non-citizens: No)"),
    ("3", "Owe any overdue Federal, state or local taxes now?"),
    ("4", "Since becoming a resident, called themself a 'nonresident alien' on a tax return, or not filed because they considered themself a nonresident?"),
    ("5a", "EVER a member of, involved in or associated with a Communist or totalitarian party?"),
    ("5b", "EVER advocated (or been associated with a group that advocated) overthrowing the government, world communism, killing officials, sabotage...?"),
    ("6a", "EVER part of or supported a group that used a weapon or explosive to harm people or property?"),
    ("6b", "... that engaged in kidnapping, assassination, or hijacking or sabotage of a plane, ship or vehicle?"),
    ("6c", "... threatened, planned or incited others to do 6.a or 6.b?"),
    ("7a", "EVER took part in torture?"), ("7b", "... genocide?"), ("7c", "... killing or trying to kill anyone?"),
    ("7d", "... intentionally and severely injuring anyone?"), ("7e", "... sexual contact with anyone who did not or could not consent, or was forced?"),
    ("7f", "... not letting someone practice their religion?"),
    ("7g", "... harming anyone because of race, religion, national origin, social group or political opinion?"),
    ("8a", "EVER served in, been a member of, helped or participated in any military or police unit?"),
    ("8b", "EVER served in or helped any armed group (paramilitary, self-defense, vigilante, rebel or guerrilla group)?"),
    ("9", "EVER worked or served where people were detained (prison, jail, camp, detention facility), or took part in detaining people?"),
    ("10a", "EVER part of or helped a group that used or threatened to use a weapon against anyone?"),
    ("10b", "If yes to 10.a: ever used a weapon against another person?"),
    ("10c", "If yes to 10.a: ever threatened to use a weapon against another person?"),
    ("11", "EVER sold, provided or transported weapons knowing they would be used against people?"),
    ("12", "EVER received weapons, paramilitary or other military-type training?"),
    ("13", "EVER recruited or used anyone under 15 to serve in or help an armed group?"),
    ("14", "EVER used anyone under 15 to take part in hostilities?"),
    ("15a", "EVER committed (or agreed or tried to commit) a crime or offense they were NOT arrested for?"),
    ("15b", "EVER arrested, cited, detained or confined by any police, military or immigration official, or charged with a crime: anywhere, even if sealed or expunged?"),
    ("16", "If they had a suspended sentence, probation or parole: completed it?"),
    ("17a", "EVER engaged in prostitution or procured prostitutes?"), ("17b", "EVER made, sold or trafficked controlled substances or illegal drugs?"),
    ("17c", "EVER married to more than one person at the same time?"), ("17d", "EVER married someone to get an immigration benefit?"),
    ("17e", "EVER helped anyone enter, or try to enter, the U.S. illegally?"), ("17f", "EVER gambled illegally or received income from illegal gambling?"),
    ("17g", "EVER failed to support dependents (child support) or pay alimony?"), ("17h", "EVER made a misrepresentation to get a public benefit?"),
    ("18", "EVER given U.S. officials false, fraudulent or misleading information or documents?"),
    ("19", "EVER lied to U.S. officials to enter the U.S. or to get an immigration benefit?"),
    ("20", "EVER placed in removal, rescission or deportation proceedings?"), ("21", "EVER removed or deported from the U.S.?"),
    ("22a", "A man who lived in the U.S. at any time between his 18th and 26th birthdays (not as a lawful nonimmigrant)?"),
    ("22b", "If yes to 22.a: registered with the Selective Service?"),
    ("23", "EVER left the U.S. to avoid being drafted?"), ("24", "EVER applied for an exemption from U.S. military service?"),
    ("25", "EVER served in the U.S. armed forces?"), ("26a", "Now a member of the U.S. armed forces?"),
    ("26b", "If 26.a yes: deploying outside the U.S. in the next 3 months?"), ("26c", "If 26.a yes: stationed outside the U.S. now?"),
    ("26d", "If 26.a no: a former service member living outside the U.S.?"),
    ("27", "EVER court-martialed, or discharged other than honorably?"), ("28", "EVER discharged from the U.S. armed forces because they were an alien?"),
    ("29", "EVER deserted from the U.S. armed forces?"), ("30a", "Now have, or EVER had, a hereditary title or order of nobility abroad?"),
    ("30b", "If yes to 30.a: willing to give it up at the ceremony?"),
    ("31", "Support the Constitution and form of government of the United States?"),
    ("32", "Understand the full Oath of Allegiance?"),
    ("33", "Unable to take the Oath because of a physical or developmental disability or mental impairment?"),
    ("34", "Willing to take the full Oath of Allegiance?"), ("35", "If the law requires it, willing to bear arms for the United States?"),
    ("36", "If the law requires it, willing to perform noncombatant services in the U.S. armed forces?"),
    ("37", "If the law requires it, willing to perform work of national importance under civilian direction?"),
]
# Part 9's own opening words, as the form prints them, for the items that continue one lead-in (5.a-5.b, 6.a-6.c, 7.a-7.g, 17.a-17.h):
# a card for 7.d alone ("... intentionally and severely injuring anyone?") starts with the sentence it finishes. Copied from the
# official Form N-400, edition 01/20/25 (schemas/forms/n400/template.pdf, pages 6, 7 and 9, Part 9), read 10/03/2026.
PART9_INTRO = ("When a question includes the word “EVER,” you must provide information about any of your actions or conduct that occurred "
               "anywhere in the world at any time, unless the question specifies otherwise.")
PART9_LEADS = {
    "5": "Have you EVER:",
    "6": "Have you EVER been a member of, involved in, or in any way associated with, or have you EVER provided money, a thing of value, "
         "services or labor, or any other assistance or support to a group that:",
    "7": "Have you EVER ordered, incited, called for, committed, assisted, helped with, or otherwise participated in any of the following:",
    "17": "Have you EVER:",
}


def lead(key: str) -> str | None:
    """The form's opening words for a Part 9 item that continues one ("n400.p9_7d" -> item 7's), else None."""
    m = re.fullmatch(r"n400\.p9_(\d+)[a-z]", key)
    return PART9_LEADS.get(m.group(1)) if m else None


_P9_REQUIRED = {"1", "2", "3", "4", "5a", "5b", "6a", "6b", "6c", "7a", "7b", "7c", "7d", "7e", "7f", "7g", "8a", "8b", "9", "10a", "11", "12", "13", "14",
                "15a", "15b", "17a", "17b", "17c", "17d", "17e", "17f", "17g", "17h", "18", "19", "20", "21", "22a", "23", "24", "25", "30a",
                "31", "32", "33", "34", "35", "36", "37"}

SECTIONS: list[tuple[str, list[tuple[str, str, dict[str, Any], bool]]]] = [
    ("Eligibility", [
        ("n400.basis", "Part 1 · Basis of eligibility", {"type": "choice", "options": BASES}, True),
        ("n400.basis_other", "Part 1, G · If 'Other': the basis", TEXT, False),
        ("n400.lpr_date", "Part 2, 7 · Permanent resident since (the date on the green card)", DATE, True),
        ("n400.trips", "Part 8 · Trips outside the U.S. longer than 24 hours in the last 5 years: one per line, "
                       "'MM/DD/YYYY - MM/DD/YYYY COUNTRY, COUNTRY', most recent first, or NONE", LINES, True),
    ]),
    ("About the client", [
        ("n400.parent_citizen_before_18", "Part 2, 10 · Was a parent (incl. adoptive) a U.S. citizen before the client's 18th birthday? (Yes: maybe already a citizen; N-600)", YES_NO, True),
        ("n400.disability_exception", "Part 2, 11 · A disability that prevents the English/civics tests? (Yes: file Form N-648)", YES_NO, True),
        ("n400.name_change", "Part 2, 3 · Legally change their name at naturalization?", YES_NO, True),
        ("n400.new_family_name", "Part 2, 3 · New family name", TEXT, False),
        ("n400.new_given_name", "Part 2, 3 · New given name", TEXT, False),
        ("n400.new_middle_name", "Part 2, 3 · New middle name", TEXT, False),
        ("n400.other_name1_family", "Part 2, 2 · Other name used since birth: family name", TEXT, False),
        ("n400.other_name1_given", "Part 2, 2 · Other name used since birth: given name", TEXT, False),
        ("n400.ssa_card", "Part 2, 12.a · Should the SSA issue a Social Security card / update status at naturalization?", YES_NO, True),
        ("n400.ssa_consent", "Part 2, 12.c · Consent to share the information with the SSA (needed for 12.a Yes)", YES_NO, False),
    ]),
    ("The spouse (a spouse-based filing)", [
        ("n400.spouse_citizen_how", "Part 5, 5.a · The spouse became a U.S. citizen", {"type": "choice", "options": ["By birth", "Other"]}, False),
        ("n400.spouse_citizen_date", "Part 5, 5.b · If 'Other': date the spouse became a citizen", DATE, False),
        ("n400.spouse_same_address", "Part 5, 4.d · Does the spouse live at the client's address?", YES_NO, False),
        ("n400.spouse_times_married", "Part 5, 7 · How many times has the spouse been married?", TEXT, False),
    ]),
    ("Part 9 · the client's answers (the attorney reviews every Yes; each needs a Part 14 explanation)",
     [(f"n400.p9_{item}", f"Part 9, {item}: {q}", YES_NO, item in _P9_REQUIRED) for item, q in PART9]
     + [("n400.p9_22c_date", "Part 9, 22.c · Selective Service: date registered", DATE, False),
        ("n400.p9_22c_number", "Part 9, 22.c · Selective Service number", TEXT, False),
        ("n400.p9_30b_titles", "Part 9, 30.b · The titles", TEXT, False),
        ("n400.crimes", "Part 9, 15 · Each crime, arrest, citation or charge, one per line: "
                        "'offense | date | conviction date | place | outcome | sentence' (even if sealed or expunged)", LINES, False)]),
    ("Part 10 · fee reduction", [
        ("n400.fee_reduction", "Part 10, 1 · Household income at or below 400% of the poverty guidelines (apply for the reduced fee)?", YES_NO, True),
        ("n400.household_income", "Part 10, 2 · Total household income (dollars a year)", TEXT, False),
        ("n400.household_size", "Part 10, 3 · Household size", TEXT, False),
        ("n400.household_earners", "Part 10, 4 · Household members earning income (including the client)", TEXT, False),
        ("n400.head_of_household", "Part 10, 5.a · Is the client the head of household?", YES_NO, False),
        ("n400.head_of_household_name", "Part 10, 5.b · If not: the head of household's name", TEXT, False),
    ]),
]
QUESTIONS = [(key, label, section, spec, required) for section, items in SECTIONS for key, label, spec, required in items]
_TRIP = re.compile(r"^\s*(\d{1,2}/\d{1,2}/\d{4})\s*(?:-|–|to)\s*(\d{1,2}/\d{1,2}/\d{4})\s*[,:;-]?\s*(.*?)\s*$", re.I)


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


def _us_to_date(text: str) -> date | None:
    try:
        month, day, year = (int(x) for x in text.split("/"))
        return date(year, month, day)
    except ValueError:
        return None


def _plus_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # February 29
        return date(d.year + years, 3, 1)


def _plus_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return date(year, month, 28)


def us(d: date | None) -> str:
    return d.strftime("%m/%d/%Y") if d else "?"


# -- trips --------------------------------------------------------------------

def parse_trips(text: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """'MM/DD/YYYY - MM/DD/YYYY COUNTRIES' per line -> trips (most recent
    first) and the lines that couldn't be read. NONE (or blank) = no trips."""
    trips, bad = [], []
    for line in str(text or "").splitlines():
        if not line.strip() or line.strip().upper() in ("NONE", "NO TRIPS", "N/A"):
            continue
        m = _TRIP.match(line)
        left, back = (_us_to_date(m.group(1)), _us_to_date(m.group(2))) if m else (None, None)
        if not (left and back) or back < left:
            bad.append(line.strip())
            continue
        # days outside: the days of leaving and of returning count as days in the U.S.
        trips.append({"left": left, "returned": back, "countries": m.group(3).upper().strip(" ,"), "days": max(0, (back - left).days - 1)})
    return sorted(trips, key=lambda t: t["left"], reverse=True), bad


def _days_outside(trips: list[dict], start: date, end: date) -> int:
    """Whole days outside the U.S. between start and end (the trip's own departure and return days excluded)."""
    total = 0
    for t in trips:
        first, last = max(t["left"] + timedelta(days=1), start), min(t["returned"] - timedelta(days=1), end)
        if last >= first:
            total += (last - first).days + 1
    return total


# -- eligibility ----------------------------------------------------------------

def eligibility(graph, today: date | None = None) -> dict[str, Any]:
    """When the client can file, and what stands in the way -- arithmetic on
    dates read from the case, with the rule each number comes from."""
    today = today or clock.today()
    s = settings()
    basis = _value(graph, "n400.basis") or "General"
    rule = s["bases"].get(basis) or s["bases"]["General"]
    years, need = rule["years"], rule["physical_presence_days"]
    lpr, dob = _d(_value(graph, "n400.lpr_date")), _d(_value(graph, "applicant.dob"))
    since = _d(_value(graph, "applicant.physical_address_since"))
    trips, bad = parse_trips(_value(graph, "n400.trips"))
    notes, blockers = [], []
    if basis not in s["bases"]:
        notes.append(f"Basis '{basis}': the residence and presence rules differ (military, VAWA, spouse abroad). The attorney checks them; "
                     "the dates below use the 5-year rule.")
    if bad:
        blockers.append("Trips the system couldn't read (write 'MM/DD/YYYY - MM/DD/YYYY COUNTRY'): " + "; ".join(bad))
    if not lpr:
        return {"basis": basis, "years": years, "earliest": None, "ready": False, "trips": trips, "notes": notes, "english_exemptions": [],
                "selective_service": None, "file_from": None,
                "blockers": blockers + ["No permanent-resident date yet (Part 2, item 7: the date on the green card)."], "presence": None}
    anniversary = _plus_years(lpr, years)
    candidates = {"residence": anniversary - timedelta(days=s["early_filing_days"])}
    if since:
        candidates["state"] = _plus_months(since, s["state_residence_months"])
    if dob:
        candidates["age"] = _plus_years(dob, s["minimum_age"])
    earliest = max(candidates.values())
    # continuous residence: absences of 6 months to a year must be explained; a year or more breaks it
    window_start = _plus_years(earliest, -years)
    long_trips = [t for t in trips if t["returned"] >= window_start and t["days"] >= s["absence_rebuttable_days"] - 1]
    breaks = [t for t in long_trips if t["days"] >= s["absence_break_days"]]
    for t in long_trips:
        if t in breaks:
            blockers.append(f"Trip {us(t['left'])} - {us(t['returned'])} ({t['days']} days outside) is a year or more: continuous residence was broken "
                            "(unless an N-470 was approved). The attorney decides when the client may file.")
        else:
            notes.append(f"Trip {us(t['left'])} - {us(t['returned'])} ({t['days']} days) was over 6 months: the client must show residence wasn't broken "
                         "(tax transcripts, a kept job, a home and family here...).")
    # physical presence: the first date on or after `earliest` with enough days in the U.S. in the period before it
    start, presence, ready_on = max(earliest, today), None, None
    for offset in range(0, 366 * years):
        day = start + timedelta(days=offset)
        period_start = _plus_years(day, -years)
        out = _days_outside(trips, period_start, day - timedelta(days=1))
        present = (day - period_start).days - out
        if offset == 0:
            presence = {"on": day.isoformat(), "days_in_us": present, "needed": need, "days_outside": out}
        if present >= need:
            ready_on = day
            break
    if ready_on is None:
        blockers.append(f"Physical presence: fewer than {need} days in the U.S. in any {years}-year period the trips allow: check the trips.")
    elif ready_on > start:
        notes.append(f"Physical presence: {presence['days_in_us']} days in the U.S. on {us(start)} ({need} needed): enough from {us(ready_on)}.")
    file_from = ready_on if ready_on and not breaks else None
    why = {"residence": f"{years} years as a resident ({us(anniversary)}) less {s['early_filing_days']} days",
           "state": f"3 months in the state (living at the current address since {us(since)})" if since else None,
           "age": "the 18th birthday"}
    limiting = max(candidates, key=lambda k: candidates[k])
    if not since:
        notes.append("No 'living here since' date: confirm 3 months in the state or USCIS district before filing.")
    # tests: 50/20, 55/15, 65/20 at the filing date
    exemptions = []
    when = file_from or earliest
    if dob and when:
        age = (when - dob).days // 365.2425
        lpr_years = (when - lpr).days / 365.2425
        exemptions = [e["text"] for e in s["english_exemptions"] if age >= e["age"] and lpr_years >= e["lpr_years"]]
    # Selective Service: a man who lived here between 18 and 26
    ss = None
    if _value(graph, "applicant.sex") == "M" and dob:
        ss_from, ss_to = _plus_years(dob, s["selective_service"]["from_age"]), _plus_years(dob, s["selective_service"]["to_age"])
        entered = _d(_value(graph, "applicant.last_arrival_date") or _value(graph, "applicant.i94_arrival_date") or _value(graph, "applicant.last_arrival_date_self_reported"))
        if not entered or entered < ss_to:
            ss = (f"Selective Service: a man in the U.S. between {us(ss_from)} and {us(ss_to)} had to register (Part 9, 22). "
                  + ("He can still register now (under 26)." if today < ss_to else "If he didn't: a status information letter from sss.gov and an explanation."))
    return {"basis": basis, "years": years, "lpr_date": lpr.isoformat(), "anniversary": anniversary.isoformat(), "earliest": earliest.isoformat(),
            "earliest_why": why[limiting], "file_from": file_from.isoformat() if file_from else None,
            "ready": bool(file_from and file_from <= today and not blockers), "presence": presence, "trips": [
                {**t, "left": t["left"].isoformat(), "returned": t["returned"].isoformat()} for t in trips],
            "english_exemptions": exemptions, "selective_service": ss, "notes": notes, "blockers": blockers}


def fee_reduction_limit(graph) -> int | None:
    """400% of the poverty guideline for the household size (the I-864P's 100% table), or None."""
    from family import settings as family_settings

    size = re.sub(r"\D", "", str(_value(graph, "n400.household_size") or ""))
    if not size:
        return None
    guides = family_settings().get("poverty_guidelines") or {}
    region = {"AK": "alaska", "HI": "hawaii"}.get(str(_value(graph, "applicant.physical_state") or "").upper(), "contiguous")
    table = (guides.get(region) or {}).get("p100") or {}
    n = int(size)
    if str(n) in table:
        base = table[str(n)]
    elif n > 8 and "8" in table and "each_additional" in table:
        base = table["8"] + (n - 8) * table["each_additional"]
    else:
        return None  # a household of 1 isn't on the I-864P table: the attorney checks the HHS guideline
    return base * settings()["fee_reduction_percent"] // 100


# -- the form's facts -----------------------------------------------------------

def _follow(graph, key: str, value: Any) -> None:
    """The box takes the name timeline's value (src/name_events.py): an earlier derived or typed value gives way, a reviewer's
    decision does not. Nothing to follow (no other name): the box is left as it is."""
    fact = graph.get(key)
    if value in (None, "") or (fact is not None and fact.review is not None):
        return
    if fact is None or not any(s.normalized_value == value for s in fact.sources):
        graph.add_source(key, "naturalization.derive", "derived", "the client's earlier names (the name timeline)", value, 0.85, tier=3)
    fact = graph.get(key)
    if fact.status == "conflict" or fact.value != value:
        fact.status = "conflict"
        graph.resolve_conflict(key, value, "the client's earlier names (the name timeline)", "the name timeline")


def derive(graph, today: date | None = None):
    """The N-400's own facts (n400.*) from what the case already holds --
    each recorded as a derived source, so it still traces to its document or
    answer; anything a person set wins."""
    from assemble import _chronological, _entries

    today = today or clock.today()

    def put(key: str, value: Any, why: str) -> None:
        if value not in (None, "") and _value(graph, key) is None:
            graph.add_source(key, "naturalization.derive", "derived", why, value, 0.85, tier=3)

    # the green-card date: the I-485 approval in the folder, else a green card's "resident since"
    from journey import notices

    approval = next((n for n in reversed(notices(graph)) if n["form"] == "I-485" and n["kind"] == "approval" and n["date"]), None)
    if approval:
        put("n400.lpr_date", approval["date"], f"the I-485 approval notice of {approval['date']}")
    elif _value(graph, "petitioner.lpr_date") and _value(graph, "petitioner.status") is None:
        put("n400.lpr_date", _value(graph, "petitioner.lpr_date"), "the green card in the folder (check that it is the client's)")
    if _value(graph, "applicant.marital_status") in ("Single", "Divorced", "Widowed", "Marriage Annulled"):
        put("n400.basis", "General", "not married: 5 years as a resident")
    basis = _value(graph, "n400.basis") or "General"
    years = (settings()["bases"].get(basis) or {}).get("years", 5)
    if _value(graph, "n400.ssa_card") == "Yes":
        put("n400.ssn", _value(graph, "applicant.ssn"), "the client's Social Security number")
    # Part 2, item 2: the other names the case settled (src/name_events.py), the same way the I-485's item 2 gets them: the timeline's
    # value wins over an earlier derived or typed one; only a reviewer's own decision on the box stands
    for n in (1, 2):
        for part in ("family", "given"):
            _follow(graph, f"n400.other_name{n}_{part}", _value(graph, f"applicant.other_name{n}_{part}"))
    # Part 5, items 4-8: only for a spouse-based filing
    if basis.startswith("Spouse"):
        for mine, theirs in (("family_name", "spouse_family_name"), ("given_name", "spouse_given_name"), ("dob", "spouse_dob"), ("a_number", "spouse_a_number")):
            put(f"n400.spouse_{mine}", _value(graph, f"applicant.{theirs}"), f"the client's spouse ({theirs})")
        put("n400.marriage_date", _value(graph, "applicant.marriage_date"), "the marriage certificate")
    # Part 4: addresses in the period, most recent first (the current one is Part 4, item 1's own block)
    window = _plus_years(today, -years)
    priors = [a for a in _entries(graph, "questionnaire.prior_address", 7, ("street", "apt", "city", "state", "zip", "province", "postal_code", "country",
                                                                             "date_from", "date_to"))
              if not a.get("date_to") or str(a["date_to"]) >= window.isoformat()]
    priors = list(reversed(_chronological(priors)))
    for n, a in enumerate(priors[:3], start=1):
        put(f"n400.address{n}_street", " ".join(x for x in (a.get("street"), a.get("apt") and f"APT {a['apt']}") if x), "the questionnaire's address history")
        put(f"n400.address{n}_city", a.get("city"), "the questionnaire's address history")
        put(f"n400.address{n}_state", a.get("state") or a.get("province"), "the questionnaire's address history")
        put(f"n400.address{n}_zip", a.get("zip") or a.get("postal_code"), "the questionnaire's address history")
        put(f"n400.address{n}_country", a.get("country") or "USA", "the questionnaire's address history")
        put(f"n400.address{n}_from", a.get("date_from"), "the questionnaire's address history")
        put(f"n400.address{n}_to", a.get("date_to"), "the questionnaire's address history")
    # Part 7: jobs and schools, most recent first -- the current employer, then the questionnaire's history
    jobs = []
    if _value(graph, "applicant.employer1_name"):
        jobs.append({p: _value(graph, f"applicant.employer1_{p}") for p in ("name", "city", "state", "zip", "country", "date_from", "occupation")})
    jobs += [{"name": e.get("employer"), **e} for e in reversed(_chronological(_entries(
        graph, "questionnaire.prior_employer", 9, ("employer", "occupation", "street", "city", "state", "zip", "country", "date_from", "date_to"))))
             if not e.get("date_to") or str(e["date_to"]) >= window.isoformat()]
    for n, j in enumerate(jobs[:3], start=1):
        for part, key in (("name", "name"), ("city", "city"), ("state", "state"), ("zip", "zip"), ("country", "country"),
                          ("from", "date_from"), ("to", "date_to"), ("occupation", "occupation")):
            if part == "to" and n == 1:
                continue
            put(f"n400.job{n}_{part}", j.get(key) or ("USA" if part == "country" and j.get("state") else None), "the employment history")
    # Part 6: children under 18
    kids = [c for c in _entries(graph, "questionnaire.child", 9, ("name", "a_number", "dob", "country"))
            if _d(c.get("dob")) and _plus_years(_d(c["dob"]), 18) > today]
    if _value(graph, "applicant.total_children") is not None or kids:
        put("n400.total_children", str(len(kids)), "children under 18 in the questionnaire")
    for n, c in enumerate(kids[:3], start=1):
        put(f"n400.child{n}_name", c.get("name"), "the questionnaire")
        put(f"n400.child{n}_dob", c.get("dob"), "the questionnaire")
    # the client's portal answers (src/portal, schemas/questions/n400.json): trips and arrests as lists
    portal_trips = _entries(graph, "questionnaire.trip", 30, ("left", "returned", "countries"))
    if portal_trips:
        put("n400.trips", "\n".join(f"{us(_d(t.get('left')))} - {us(_d(t.get('returned')))} {str(t.get('countries') or '').upper()}"
                                     for t in sorted(portal_trips, key=lambda t: str(t.get("left") or ""), reverse=True)), "the client's trips (portal)")
    elif _value(graph, "questionnaire.n400_trips_any") == "No":
        put("n400.trips", "NONE", "the client: no trips (portal)")
    portal_crimes = _entries(graph, "questionnaire.crime", 10, ("what", "date", "place", "outcome"))
    if portal_crimes:
        put("n400.crimes", "\n".join(" | ".join([str(c.get("what") or ""), us(_d(c.get("date"))) if _d(c.get("date")) else "", "", str(c.get("place") or ""),
                                                  str(c.get("outcome") or ""), ""]) for c in portal_crimes), "the client's list (portal)")
    # the reduced fee: the household's income against 400% of the guideline
    limit, income = fee_reduction_limit(graph), re.sub(r"\D", "", str(_value(graph, "n400.household_income") or ""))
    if limit and income:
        put("n400.fee_reduction", "Yes" if int(income) <= limit else "No", f"household income ${int(income):,} vs ${limit:,} (400% of the guideline)")
    elif _value(graph, "questionnaire.n400_want_fee_reduction") == "No":
        put("n400.fee_reduction", "No", "the client didn't ask for the reduced fee")
    # Part 8: trips
    trips, _bad = parse_trips(_value(graph, "n400.trips"))
    for n, t in enumerate(trips[:6], start=1):
        put(f"n400.trip{n}_left", t["left"].isoformat(), "the trips list")
        put(f"n400.trip{n}_returned", t["returned"].isoformat(), "the trips list")
        put(f"n400.trip{n}_countries", t["countries"], "the trips list")
    # Part 9, item 15's table
    crimes = [[x.strip() for x in line.split("|")] for line in str(_value(graph, "n400.crimes") or "").splitlines() if line.strip()]
    for n, row in enumerate(crimes[:5], start=1):
        row += [""] * (6 - len(row))
        for part, value in zip(("what", "date", "conviction_date", "place", "outcome", "sentence"), row):
            if part.endswith("date") and value:
                when = _us_to_date(value) or _d(value)
                value = when.isoformat() if when else None
            put(f"n400.crime{n}_{part}", value or None, "the crimes list")
    # Part 14: what the form has no room for
    blocks = [("3", "4", "1", "ADDRESS (CONTINUED): " + ", ".join(x for x in (a.get("street"), a.get("city"), a.get("state") or a.get("province"),
                                                                                a.get("zip") or a.get("postal_code"), a.get("country")) if x)
               + f" FROM {us(_d(a.get('date_from')))} TO {us(_d(a.get('date_to')))}") for a in priors[3:]]
    blocks += [("5", "7", "1", f"EMPLOYMENT (CONTINUED): {j.get('name')}, {j.get('city') or ''} {j.get('state') or ''}, "
                               f"{j.get('occupation') or ''} FROM {us(_d(j.get('date_from')))} TO {us(_d(j.get('date_to')))}") for j in jobs[3:]]
    blocks += [("6", "8", "1", f"TRIP (CONTINUED): LEFT {us(t['left'])}, RETURNED {us(t['returned'])}, {t['countries']}") for t in trips[6:]]
    blocks += [("5", "6", "2", f"CHILD (CONTINUED): {c.get('name')}, BORN {us(_d(c.get('dob')))}") for c in kids[3:]]
    blocks += [("8", "9", "15", "CRIME OR OFFENSE (CONTINUED): " + " | ".join(r)) for r in crimes[5:]]
    for n, (page, part, item, text) in enumerate(blocks, start=1):
        for key, value in (("page", page), ("part", part), ("item", item), ("text", text)):
            put(f"n400.p14_block{n}_{key}", value, "composed for Part 14")
    # what the folder itself shows (a suggestion the client confirms: never overrides an answer)
    if _value(graph, "applicant.nta_present"):
        put("n400.p9_20", "Yes", "a Notice to Appear is in the folder")
    return graph


# -- the review app ---------------------------------------------------------------

def _graph(client_dir: Path, today: date | None = None):
    from review.state import reviewed_graph

    return derive(reviewed_graph(client_dir), today)


def _has_doc(client_dir: Path, *doc_types: str) -> list[str]:
    meta = json.loads((client_dir / "meta.json").read_text(encoding="utf-8")) if (client_dir / "meta.json").exists() else {}
    return [doc for doc, kind in (meta.get("classifications") or {}).items() if kind in doc_types]


# Who answers each question, by what the question is: the attorney decides the basis of eligibility; the office reads a date off
# a document it holds; everything else is something the client knows (their trips, names, family, the Social Security card, the
# spouse's details, every Part 9 and Part 10 question), so the card says "Ask: the client" and can ask them in the portal.
# Implementation note.
ATTORNEY_DECIDES = {"n400.basis", "n400.basis_other"}
FROM_DOCUMENT = {"n400.lpr_date": "the green card", "n400.spouse_citizen_date": "the spouse's certificate of naturalization"}
SPOUSE_SECTION = "The spouse (a spouse-based filing)"


def who_answers(key: str) -> tuple[str, str]:
    """("client" | "attorney" | "document", the words the card shows when nothing answers it yet)."""
    if key in ATTORNEY_DECIDES:
        return "attorney", "The attorney decides"
    if key in FROM_DOCUMENT:
        return "document", f"From a document: {FROM_DOCUMENT[key]}"
    return "client", "Ask: the client"


def spouse_based(basis: str | None) -> bool:
    """The spouse group (Part 5, Items 4.a to 8) is asked only on the two spouse bases. Form N-400 (edition 01/20/25, page 4,
    Part 5): "If you are filing under one of the categories below, answer Item Numbers 4.a. - 8.: Spouse of U.S. Citizen, Part 1.,
    Item Number 1.b.; or; Spouse of U.S. Citizen in Qualified Employment Outside the United States, Part 1., Item Number 1.d.",
    and "If you are not filing under one of the categories above, skip to Part 6." VAWA (Part 1, C), General, military and Other
    skip it. Read from schemas/forms/n400/template.pdf on 10/03/2026."""
    return str(basis or "").startswith("Spouse")


def status(client_dir: Path, today: date | None = None) -> dict[str, Any]:
    graph = _graph(client_dir, today)
    e = eligibility(graph, today)
    asks_spouse = spouse_based(e["basis"])
    questions = []
    for key, label, section, spec, required in QUESTIONS:
        if section == SPOUSE_SECTION and not asks_spouse:
            continue  # said once, below, instead of four rows nobody answers
        fact = graph.get(key)
        sources = [{"doc": s.doc_id, "type": s.doc_type, "raw": s.raw_value} for s in (fact.sources if fact is not None else [])][:3]
        who, ask = who_answers(key)
        questions.append({"key": key, "label": label, "section": section, "who": "the client" if who == "client" else "the attorney" if who == "attorney" else "the office",
                          "asks": who, "ask_text": ask,
                          "input": spec, "required": required, "value": _value(graph, key), "sources": sources, "answered_by": getattr(getattr(fact, "review", None), "resolved_by", None),
                          "lead": lead(key), **({"intro": PART9_INTRO} if section.startswith("Part 9") else {})})
    years = e.get("years")
    not_asked = [] if asks_spouse else [{"section": SPOUSE_SECTION, "why": f"Not asked: basis is {e['basis']}"
                                         + (f" ({years} year{'' if years == 1 else 's'})" if years else "")}]
    return {"questions": questions, "not_asked": not_asked, "eligibility": e, "problems": problems(client_dir, today, graph=graph, questions=questions),
            "fee_limit": fee_reduction_limit(graph)}


@producer(OFFICE)
def problems(client_dir: Path, today: date | None = None, graph=None, questions: list | None = None) -> list[str]:
    """What stops the N-400 packet from being final (src/packet.py adds these)."""
    if questions is None:
        return status(client_dir, today)["problems"]
    today = today or clock.today()
    out = []
    missing = [re.split(r" · | -- |: ", q["label"], maxsplit=1)[0] for q in questions if q["required"] and q["value"] is None]
    if missing:
        by_part: dict[str, list[str]] = {}
        for ref in missing:  # "Part 9, 15b" -> {"Part 9": ["15b"]}
            part, _, item = ref.partition(", ")
            by_part.setdefault(part, []).append(item)
        out.append(held(of_first(questions), f"N-400 questions not answered yet ({len(missing)}): "
                        + "; ".join(f"{part} {', '.join(i for i in items if i)}".strip() for part, items in by_part.items()) + "."))
    e = eligibility(graph, today)
    out += e["blockers"]
    if e.get("file_from") and _d(e["file_from"]) > today:
        out.append(f"Too early: the client can file from {us(_d(e['file_from']))} ({e['earliest_why']}).")
    value = lambda k: _value(graph, k)  # noqa: E731
    if value("applicant.nta_present") or _has_doc(client_dir, "notice_to_appear"):
        out.append(held(ATTORNEY, "A Notice to Appear is in the folder: USCIS can't naturalize anyone while removal proceedings are pending (INA 318). "
                                  "the attorney confirms the court case is closed, and Part 9, item 20 is Yes."))
    if _has_doc(client_dir, "criminal_record") and value("n400.p9_15b") != "Yes":
        out.append("A criminal record is in the folder but Part 9, item 15.b isn't Yes: every arrest, citation or charge must be listed, "
                   "even if dismissed, sealed or expunged.")
    if value("n400.parent_citizen_before_18") == "Yes":
        out.append(held(ATTORNEY, "A parent was a U.S. citizen before the client turned 18: the client may already be a citizen (Form N-600). The attorney decides."))
    if value("n400.disability_exception") == "Yes" and not _has_doc(client_dir, "n648"):
        out.append(held(CLIENT, "Part 2, item 11 is Yes: file Form N-648 (signed by a doctor) with the N-400."))
    if value("n400.ssa_card") == "Yes" and value("n400.ssa_consent") != "Yes":
        out.append("Part 2, 12.a is Yes: 12.c (consent for disclosure) must be Yes too, or the SSA can't issue the card.")
    # what the client ticked or wasn't sure of in the portal: left blank for the attorney to ask about (src/portal/bank.py)
    ticked = sorted({k.split("n400.p9_")[1] for k in graph.all_facts() if k.startswith("questionnaire.yes.n400.p9_") and value(k)})
    unsure = sorted({k.split("n400.p9_")[1] for k in graph.all_facts() if k.startswith("questionnaire.unsure.n400.p9_") and value(k)})
    if ticked:
        out.append(f"In the portal the client ticked Part 9 item(s) {', '.join(ticked)}: talk with the client, then answer each (and explain it in Part 14).")
    if unsure:
        out.append(f"In the portal the client wasn't sure about Part 9 item(s) {', '.join(unsure)}: ask, then answer each.")
    yes = [item for item, _ in PART9 if value(f"n400.p9_{item}") == "Yes" and item not in ("16", "22a", "22b", "30b", "31", "32", "34", "35", "36", "37")]
    if yes:
        out.append(held(ATTORNEY, f"Part 9 answered Yes ({', '.join(yes)}): each needs an explanation in Part 14 and evidence. The attorney reviews."))
    no_oath = [item for item in ("31", "32", "34", "35", "36", "37") if value(f"n400.p9_{item}") == "No"]
    if no_oath:
        out.append(held(ATTORNEY, f"Part 9 answered No to {', '.join(no_oath)} (the Constitution and the Oath): the attorney reviews before filing."))
    if (value("n400.basis") or "").startswith("Spouse") and not _has_doc(client_dir, "marriage_certificate"):
        out.append(held(CLIENT, "A spouse-based filing needs the marriage certificate and proof the spouse has been a citizen 3 years, not in the folder."))
    if value("n400.fee_reduction") == "Yes":
        limit, income = fee_reduction_limit(graph), re.sub(r"\D", "", str(value("n400.household_income") or ""))
        if limit and income and int(income) > limit:
            out.append(f"Fee reduction: household income ${int(income):,} is above 400% of the guideline (${limit:,} for the household): the full fee applies.")
        elif limit is None:
            out.append("Fee reduction: check 400% of the HHS poverty guideline for this household size (a household of 1 isn't on the I-864P table).")
    from family import fees

    paper = fees().get("paper") or {}
    if paper.get("n400") is None:
        out.append("The N-400 fee isn't set: add it on the Settings page (Filing fees), from the current Form G-1055.")
    return out


def answer(client_dir: Path, values: dict[str, Any], reviewer: str, role: str | None = None) -> dict[str, Any]:
    from review.state import record_decision

    specs = {key: (label, spec) for key, label, _section, spec, _req in QUESTIONS}
    for key, raw in values.items():
        if key not in specs:
            raise ValueError(f"{key} is not an N-400 question.")
        label, spec = specs[key]
        if key == "n400.trips" and raw not in ("", None):
            _trips, bad = parse_trips(raw)
            if bad:
                raise ValueError("Couldn't read these trips. Write 'MM/DD/YYYY - MM/DD/YYYY COUNTRY': " + "; ".join(bad))
        item = {"id": f"n400:{key}", "kind": "n400", "level": "review", "title": label, "group": "attorney", "actions": ["set", "blank"],
                "facts": [{"key": key, "input": spec}]}
        decision = {"action": "blank" if raw in ("", None) else "set", "values": {} if raw in ("", None) else {key: raw},
                    "reviewer": reviewer, **({"role": role} if role else {}), "note": "N-400 question"}
        record_decision(client_dir, item, decision)
    return status(client_dir)
