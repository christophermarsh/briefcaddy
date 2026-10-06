"""A written request to an immigration judge for a custody redetermination
(bond) hearing for a detained client. The court has no form for it: the
office writes it (src/court_pleading.py), DRAFT for the attorney. Sources,
each read 10/02/2026:

  8 CFR 1003.19 and 1236.1(d) (eCFR, current as of 10/01/2026):
    The judge reviews the custody and bond ICE set (1003.19(a)); a first
      request may be oral, written or, at the judge's discretion, by phone
      (1003.19(b)); a later one is written and considered "only upon a
      showing that the alien's circumstances have changed materially since
      the prior bond redetermination" (1003.19(e)).
    Where, in this order: the court with jurisdiction over the place of
      detention; the court with administrative control over the case; the
      Office of the Chief Immigration Judge (1003.19(c)). Matter of Vizcaino
      Aybar, 29 I&N Dec. 736 (BIA 2026): the place of detention's court is
      the proper venue.
    Separate from the removal case; "any information that is available" may
      be used (1003.19(d)). The decision is appealable to the BIA (1003.19(f));
      an order releasing a client ICE held without bond, or on $10,000 or
      more, is stayed when ICE files Form EOIR-43 within one business day
      (1003.19(i)(2)).
    Who a judge can't release: arriving aliens (paroled after arrival
      included), 237(a)(4), INA 236(c)(1) (1003.19(h)(2)(i)); a client may
      still ask the judge to rule that they are not properly in (C)-(E)
      ((h)(2)(ii)).
    When: at any time before the removal order becomes final; a client ICE
      already released asks within 7 days of release (1236.1(d)(1)), later
      the district director (1236.1(d)(2)).
  The BIA (justice.gov/eoir, Volume 24 and Volume 29 precedent decisions):
    Matter of Guerra, 24 I&N Dec. 37, 40 (BIA 2006): the judge considers
      whether the client is a threat to national security, a danger to the
      community, likely to abscond or otherwise a poor bail risk, and "may
      look to" nine factors -- copied word for word into the request.
    Matter of Yajure Hurtado, 29 I&N Dec. 216 (BIA 2025): no bond hearing
      for a person present without admission; Matter of N-A-G-C-, 29 I&N
      Dec. 662 (BIA 2026): an SIJ approval or a UAC designation doesn't
      change that; Matter of W-F-D-, 29 I&N Dec. 854 (BIA 2026): generally
      none after an administratively final removal order. The panel quotes
      the Yajure Hurtado and W-F-D- headnotes. Warned, never decided: the
      attorney decides.
  Who bears the burden, and by what standard, depends on the circuit: Matter
    of Vizcaino Aybar cites Hernandez-Lara v. Lyons, 10 F.4th 19 (1st Cir.
    2021) for DHS's burden in a First Circuit (Massachusetts) case, while
    Guerra puts it on the respondent. The request leaves that paragraph to the
    attorney.
  No fee: 8 CFR 1103.7(b) lists none, nor does EOIR's fee page (updated
    10/01/2026); "There is no filing fee to request a bond hearing" (Practice
    Manual 9.3(c)(ii), version of 02/20/2020).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from filing_questions import DATE, LINES, TEXT, YES_NO, putter, sij, us, value
from filing_questions import iso as _d
import court_pleading as cp
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

TITLE = "Bond redetermination request (immigration court)"
PLEADING = "Respondent's Written Request for a Custody Redetermination (Bond) Hearing"
ENTRY = ["Admitted (inspected and admitted, with a visa or otherwise)", "A permanent resident", "Paroled (not admitted)",
         "Entered without inspection (never admitted)", "An arriving alien in removal proceedings", "Other"]
# Matter of Guerra, 24 I&N Dec. 37, 40 (BIA 2006): the factors, in the Board's words (justice.gov/eoir/vll/intdec/vol24/3544.pdf, read 10/02/2026)
FACTORS = [("bond.fixed_address", "whether the alien has a fixed address in the United States"),
           ("bond.residence", "the alien's length of residence in the United States"),
           ("bond.family_ties", "the alien's family ties in the United States, and whether they may entitle the alien to reside permanently in the "
                                "United States in the future"),
           ("bond.employment", "the alien's employment history"),
           ("bond.appearances", "the alien's record of appearance in court"),
           ("bond.criminal", "the alien's criminal record, including the extensiveness of criminal activity, the recency of such activity, and the "
                             "seriousness of the offenses"),
           ("bond.violations", "the alien's history of immigration violations"),
           ("bond.flight", "any attempts by the alien to flee prosecution or otherwise escape from authorities"),
           ("bond.manner_of_entry", "the alien's manner of entry to the United States")]
RELEASE_DAYS = 7  # 8 CFR 1236.1(d)(1): "within 7 days of release"
PACKET_PDF = "packet_court_bond.pdf"  # schemas/packets/court_bond.json: the PDF uploaded in ECAS
FILES = {"cover": "bond_cover.pdf", "request": "bond_request.pdf", "order": "bond_order.pdf", "service": "bond_service.pdf"}

SECTIONS = [
    ("Custody", "the attorney", [
        ("bond.custody_location", "Where the client is detained: the facility, its city and state (from ICE's detainee locator or the client; never guessed)",
         TEXT, True),
        ("bond.detained_since", "Detained by ICE since", DATE, False),
        ("bond.dhs_bond", "The bond ICE set, in dollars (or 'No bond' when ICE set none)", TEXT, True),
        ("bond.amount_asked", "The bond the client asks the judge to set, in dollars (blank: an amount the judge finds appropriate)", TEXT, False),
        ("bond.released", "Already released by ICE on the bond ICE set? (then the request is due within 7 days of release: 8 CFR 1236.1(d)(1))", YES_NO, False),
        ("bond.released_on", "Released by ICE on", DATE, False),
        ("bond.prior_ruling", "Has a judge or the BIA already ruled on the client's custody? (a new request needs a material change: 8 CFR 1003.19(e))",
         YES_NO, True),
        ("bond.changed", "The material change in the client's circumstances since that ruling", LINES, False),
    ]),
    ("Who the judge can't release (8 CFR 1003.19(h); the BIA's decisions)", "the attorney", [
        ("bond.entry", "How the client came to be in the U.S.", {"type": "choice", "options": ENTRY}, True),
        ("bond.mandatory", "Does ICE hold the client as subject to mandatory detention (INA 236(c)) or as deportable on security grounds (237(a)(4))?",
         YES_NO, True),
        ("bond.final_order", "Is there an administratively final removal order?", YES_NO, True),
    ]),
    ("The court and service", "the attorney", cp.court_section("bond")),
    ("The facts for the judge (Matter of Guerra's factors)", "the attorney", [
        ("bond.fixed_address", "(1) The fixed address where the client will live if released", TEXT, True),
        ("bond.residence", "(2) How long the client has lived in the U.S.", TEXT, True),
        ("bond.family_ties", "(3) Family in the U.S., and whether they may let the client stay permanently", LINES, True),
        ("bond.employment", "(4) Employment history", LINES, True),
        ("bond.appearances", "(5) Record of appearance in court (hearings and ICE check-ins attended)", LINES, True),
        ("bond.criminal", "(6) Criminal record: each arrest, charge or conviction, how recent and how serious (or 'None')", LINES, True),
        ("bond.violations", "(7) History of immigration violations (or 'None')", LINES, True),
        ("bond.flight", "(8) Any attempt to flee prosecution or escape from authorities (or 'None')", LINES, True),
        ("bond.manner_of_entry", "(9) The manner of entry to the United States", TEXT, True),
        ("bond.relief", "The relief the client is pursuing, and where it stands", LINES, False),
    ]),
    ("The attorney's sections", "the attorney", [
        ("bond.burden", "Who bears the burden, and by what standard, in this court's circuit (the attorney writes this paragraph)", LINES, True),
        ("bond.argument", "Anything else the attorney argues", LINES, False),
    ]),
]
MORE_QUESTIONS = "Another court's address appears when another court is chosen."


def from_record(status: dict[str, Any], graph) -> None:
    """The court, the judge and the next hearing (the case page's hearings); an earlier bond hearing with a result is a prior ruling."""
    import clock

    cp.from_hearings(status, graph, "bond", clock.today())
    ruled = [h for h in (status.get("journey") or {}).get("hearings") or [] if h.get("kind") == "Bond" and h.get("result")]
    if ruled:
        putter(graph, "case page")("bond.prior_ruling", "Yes", f"the bond hearing of {ruled[-1].get('date')} has a result on the case page")


def _years(start: date, today: date) -> int:
    return today.year - start.year - ((today.month, today.day) < (start.month, start.day))


def derive(graph, today: date):
    import court

    court.derive(graph, today)  # the EOIR-28's facts and the client's address
    put = putter(graph, "bond.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    home = ", ".join(x for x in (" ".join(y for y in (v("applicant.physical_street"), v("applicant.physical_unit_type"), v("applicant.physical_apt")) if y),
                                 v("applicant.physical_city"), " ".join(y for y in (v("applicant.physical_state"), v("applicant.physical_zip")) if y)) if x)
    put("bond.fixed_address", home or None, "the client's home address in the case: confirm it is where the client will live")
    manner = str(v("applicant.last_arrival_manner") or "").upper()
    put("bond.entry", {"ADMITTED": ENTRY[0], "PAROLED": ENTRY[2], "WITHOUT ADMISSION OR PAROLE": ENTRY[3]}.get(manner), "how the client last arrived, in the case")
    arrived = _d(v("applicant.last_arrival_date") or v("applicant.i94_arrival_date") or v("applicant.last_arrival_date_self_reported"))
    place = ", ".join(x for x in (v("applicant.last_arrival_city"), v("applicant.last_arrival_state")) if x)
    if manner == "ADMITTED" and arrived:
        klass = v("applicant.i94_class_of_admission")
        put("bond.manner_of_entry", f"Admitted{' at ' + place if place else ''} on {us(arrived)}{' as a ' + klass + ' nonimmigrant' if klass else ''} (Form I-94)",
            "the client's last arrival (the I-94)")
    elif manner == "WITHOUT ADMISSION OR PAROLE" and arrived:
        put("bond.manner_of_entry", f"Entered without inspection{' near ' + place if place else ''} on or about {us(arrived)}", "the client's account of the arrival")
    if arrived and v("applicant.first_time_in_us") == "Yes":
        n = _years(arrived, today)
        put("bond.residence", f"Since {us(arrived)} ({n} year{'s' if n != 1 else ''})", "the client's only arrival in the U.S.")
    return graph


def released_due(graph) -> date | None:
    """A client ICE released: the last day to ask the judge (8 CFR 1236.1(d)(1): within 7 days of release)."""
    out = _d(value(graph, "bond.released_on"))
    return out + timedelta(days=RELEASE_DAYS) if out and value(graph, "bond.released") == "Yes" else None


def detained(graph) -> bool:
    return value(graph, "bond.released") != "Yes"


def fee(graph, today: date) -> tuple[int, str]:
    return 0, ("no fee for a bond redetermination request (8 CFR 1103.7(b) and EOIR's fee page list none; Practice Manual 9.3(c)(ii), "
               "2020 version)")


def notes(graph, today: date) -> list[dict[str, str]]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = [{"level": "info", "title": "What it is",
            "text": "A written request that the judge review the custody and bond ICE set (8 CFR 1003.19(a), 1236.1(d)(1)). The court has no form for it: "
                    "the office's request, with a cover page, a proposed order and a proof of service, DRAFT until the attorney approves. It is kept "
                    "separate from the removal case (1003.19(d))."},
           {"level": "info", "title": "Where",
            "text": "To the court with jurisdiction over the place of detention; if not detained, the court with administrative control over the case; "
                    "else the Office of the Chief Immigration Judge (8 CFR 1003.19(c); Matter of Vizcaino Aybar, 29 I&N Dec. 736 (BIA 2026)). "
                    + cp.how_filed(graph, "bond", duplicate=False)},
           {"level": "info", "title": "Fee", "text": "None: " + fee(graph, today)[1] + "."}]
    due = released_due(graph)
    out.append({"level": "warn" if due and today > due else "info", "title": "When",
                "text": (f"ICE released the client on {us(_d(v('bond.released_on')))}: the request is due by {us(due)}, within 7 days of release; after "
                         "that, the client asks ICE (the district director) instead (8 CFR 1236.1(d)(1), (2))." if due else
                         "At any time before the removal order becomes final (8 CFR 1236.1(d)(1)). The court sets the hearing for the earliest possible "
                         "date (Practice Manual 9.3(d), 2020 version).")})
    entry = v("bond.entry")
    if entry in (ENTRY[2], ENTRY[4]):
        out.append({"level": "warn", "title": "An arriving alien",
                    "text": "An immigration judge may not redetermine the custody of \"arriving aliens in removal proceedings, including aliens paroled "
                            "after arrival pursuant to section 212(d)(5) of the Act\" (8 CFR 1003.19(h)(2)(i)(B)); ICE decides their custody "
                            "((h)(2)(ii)). The attorney decides whether the client is one."})
    if entry in (ENTRY[2], ENTRY[3]):
        out.append({"level": "warn", "title": "Not admitted",
                    "text": "Matter of Yajure Hurtado, 29 I&N Dec. 216 (BIA 2025), its headnote: \"Immigration Judges lack authority to hear bond requests or to grant "
                            "bond to aliens who are present in the United States without admission.\""
                            + (" An approved SIJ petition or a UAC designation doesn't change that (Matter of N-A-G-C-, 29 I&N Dec. 662 (BIA 2026))."
                               if sij(graph) else "")
                            + " The attorney decides how to proceed."})
    if v("bond.mandatory") == "Yes":
        out.append({"level": "warn", "title": "Mandatory detention",
                    "text": "The judge may not redetermine the custody of a person described in INA 237(a)(4) or subject to INA 236(c)(1) (8 CFR "
                            "1003.19(h)(2)(i)(C), (D)), but the client may ask the judge to decide that they are \"not properly included\" "
                            "((h)(2)(ii)). The request then says so."})
    if v("bond.final_order") == "Yes":
        out.append({"level": "warn", "title": "A final order",
                    "text": "\"Immigration Judges generally lack jurisdiction to redetermine custody conditions once an alien becomes subject to an "
                            "administratively final removal order\" (Matter of W-F-D-, 29 I&N Dec. 854 (BIA 2026), its headnote; 8 CFR 1236.1(d)(1))."})
    out.append({"level": "info", "title": "The standard",
                "text": "The judge considers whether the client is a threat to national security, a danger to the community, likely to abscond or "
                        "otherwise a poor bail risk, and may look to nine factors: a fixed address, length of residence, family ties, employment, "
                        "appearances in court, criminal record, immigration violations, attempts to flee, manner of entry (Matter of Guerra, 24 I&N "
                        "Dec. 37, 40 (BIA 2006)). Who bears the burden depends on the circuit: the attorney writes that paragraph."})
    out.append({"level": "info", "title": "After the hearing",
                "text": "Either side can appeal the judge's bond decision to the BIA on Form EOIR-26, no fee (8 CFR 1003.19(f), 1003.38; record the "
                        "decision on the case page). When ICE set no bond, or $10,000 or more, an order releasing the client is stayed if ICE files Form "
                        "EOIR-43 within one business day (8 CFR 1003.19(i)(2))."})
    return out


@producer(OFFICE)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = cp.court_problems(graph, "bond") + cp.upload_problems(client_dir, graph, PACKET_PDF)
    if not v("firm.licensing_authority"):
        out.append("The attorney's bar admission isn't set: add it on the Settings page (The firm and the attorney).")
    if v("bond.prior_ruling") == "Yes" and not v("bond.changed"):
        out.append(held(ATTORNEY, "A judge or the BIA already ruled on custody: a new request is considered only on a material change in the client's circumstances "
                        "(8 CFR 1003.19(e)). Describe it."))
    due = released_due(graph)
    if due and today > due:
        out.append(held(ATTORNEY, f"Released on {us(_d(v('bond.released_on')))}: the 7 days to ask the judge ended {us(due)} (8 CFR 1236.1(d)(1)). Ask ICE (the "
                        "district director) instead (1236.1(d)(2))."))
    if v("bond.released") == "Yes" and not _d(v("bond.released_on")):
        out.append(held(CLIENT, "The date ICE released the client: the request is due within 7 days of it (8 CFR 1236.1(d)(1))."))
    return out


# -- the request ----------------------------------------------------------------------------------------------------


def _money(raw: Any) -> str | None:
    import re

    digits = re.sub(r"[^\d.]", "", str(raw or ""))
    try:
        return f"${float(digits):,.0f}" if digits else None
    except ValueError:
        return None


def render(client_dir: Path, graph, today: date) -> None:
    """The cover page, the request, the proposed order and the proof of service (src/court_pleading.py)."""
    v = lambda k: value(graph, k)  # noqa: E731
    esc = cp.esc
    draft = bool(problems(client_dir, graph, today)) or not all(v(k) for k, *_rest in (q for _s, _w, items, *_ in SECTIONS for q in items if q[3]))
    held = detained(graph)
    cp.cover(client_dir / FILES["cover"], graph, "bond", PLEADING, "bond", ["DETAINED"] if held else [], draft)

    paper = cp.Paper(client_dir / FILES["request"], PLEADING, draft)
    name, anum = esc(cp.respondent(graph)), esc(cp._a(v("applicant.a_number")))
    asked = _money(v("bond.amount_asked"))
    release = f"on a bond of {asked}" if asked else "on a bond in an amount the Court finds appropriate"
    paper.p(f"<b>{esc(PLEADING.upper())}</b>", paper.center)
    paper.gap(10)
    paper.p(f"The respondent, {name} ({anum}), through undersigned counsel, respectfully requests that the Immigration Judge redetermine the "
            f"custody and bond conditions set by the Department of Homeland Security (DHS) and order the respondent released {release}. "
            "8 C.F.R. &sect;&sect; 1003.19(a), 1236.1(d)(1).")
    paper.p("I. Custody", paper.heading)
    location, since, set_by_dhs = v("bond.custody_location"), _d(v("bond.detained_since")), _money(v("bond.dhs_bond"))
    if held:
        paper.p(f"The respondent is detained by DHS at {esc(location or '[the place of detention]')}"
                + (f", and has been detained since {esc(us(since))}" if since else "") + ". "
                + (f"DHS set a bond of {set_by_dhs}." if set_by_dhs else "DHS has not set a bond."))
    else:
        paper.p(f"DHS released the respondent on {esc(us(_d(v('bond.released_on'))))}" + (f" on a bond of {set_by_dhs}" if set_by_dhs else "")
                + ". This request for amelioration of the terms of release is made within 7 days of release. 8 C.F.R. &sect; 1236.1(d)(1).")
    paper.p("II. Jurisdiction and venue", paper.heading)
    paper.p("Under 8 C.F.R. &sect; 1003.19(c), an application for review of a bond determination is made, in the designated order:")
    paper.p("\"(1) If the respondent is detained, to the Immigration Court having jurisdiction over the place of detention; (2) To the Immigration "
            "Court having administrative control over the case; or (3) To the Office of the Chief Immigration Judge for designation of an appropriate "
            "Immigration Court.\"", paper.quote)
    paper.p("See Matter of Vizcaino Aybar, 29 I&amp;N Dec. 736 (BIA 2026)."
            + (f" The respondent is detained at {esc(location or '[the place of detention]')}, and this request is made to this Court." if held else
               " This request is made to this Court."))
    paper.p("Consideration of this request \"shall be separate and apart from, and shall form no part of, any deportation or removal hearing or "
            "proceeding,\" and the determination \"may be based upon any information that is available to the Immigration Judge or that is presented "
            "to him or her by the alien or the Service.\" 8 C.F.R. &sect; 1003.19(d).")
    if v("bond.mandatory") == "Yes":
        paper.p("To the extent DHS treats the respondent as subject to mandatory detention, the respondent asks the Immigration Judge to determine "
                "that the respondent is not properly included within that class. 8 C.F.R. &sect; 1003.19(h)(2)(ii).")
    if v("bond.prior_ruling") == "Yes":
        paper.p("This is a subsequent request. It is made in writing, and it \"shall be considered only upon a showing that the alien's circumstances "
                "have changed materially since the prior bond redetermination.\" 8 C.F.R. &sect; 1003.19(e). The respondent's circumstances have "
                f"changed materially: {esc(v('bond.changed') or '[the material change]')}")
    paper.p("III. The standard", paper.heading)
    paper.p("In a custody redetermination, \"an Immigration Judge must consider whether an alien who seeks a change in custody status is a threat to "
            "national security, a danger to the community at large, likely to abscond, or otherwise a poor bail risk.\" Matter of Guerra, 24 I&amp;N "
            "Dec. 37, 40 (BIA 2006) (citing Matter of Patel, 15 I&amp;N Dec. 666 (BIA 1976)).")
    paper.p(esc(v("bond.burden") or "[ATTORNEY: who bears the burden, and by what standard, in this circuit.]"))
    paper.p("IV. The factors", paper.heading)
    paper.p("Immigration Judges \"may look to a number of factors in determining whether an alien merits release from bond, as well as the amount of "
            "bond that is appropriate.\" Id. The respondent addresses each of them:")
    for n, (key, words) in enumerate(FACTORS, start=1):
        paper.p(f"{n}.&nbsp;&nbsp;<i>{esc(words[0].upper() + words[1:])}.</i> {esc(v(key) or '[ATTORNEY]')}", paper.item)
    if v("bond.relief"):
        paper.p(f"The relief the respondent is pursuing: {esc(v('bond.relief'))}", paper.item)
    if v("bond.argument"):
        paper.p("V. Further argument", paper.heading)
        paper.p(esc(v("bond.argument")))
    paper.p("Conclusion", paper.heading)
    paper.p("For these reasons, the respondent respectfully asks the Immigration Judge to schedule a custody redetermination hearing at the earliest "
            f"possible date and to order the respondent released {release}.")
    cp.signature(paper, graph, today)
    paper.write()

    cp.proposed_order(client_dir / FILES["order"], graph, "bond", "the respondent's request for a custody redetermination",
                      ["DHS does not oppose the request.", "Good cause has been established.", "The court agrees with the reasons stated in DHS's opposition.",
                       "Other: ________________________________________"],
                      ["The respondent shall be released from custody upon posting a bond of $______________.",
                       "The respondent shall be released on the respondent's own recognizance.",
                       "The respondent shall remain in custody: ________________________________________"], draft, noun="request")
    cp.proof_of_service(client_dir / FILES["service"], graph, "the Respondent's Written Request for a Custody Redetermination (Bond) Hearing, "
                        "the Form EOIR-28, the proposed order", draft)
