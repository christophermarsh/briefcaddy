"""A motion to reopen or to reconsider before the immigration judge, and a
motion to reopen an in absentia order -- after a judge's decision (src/journey.py
puts the deadlines on the timeline). Not the I-290B (src/motion.py: a motion
to USCIS) and not a motion to the BIA. The court has no form for it: the office
writes it (src/court_pleading.py), DRAFT for the attorney. Sources, each read
10/02/2026:

  8 CFR 1003.23(b) (eCFR, current as of 10/01/2026; as amended 91 FR 35374,
    06/11/2026):
    One motion to reconsider and one to reopen; reconsider within 30 days and
      reopen within 90 days of the final order; none after the client leaves
      the U.S.; not while jurisdiction is with the BIA ((b)(1)).
    Filed with the court that has administrative control over the record;
      served on ICE's Office of the Principal Legal Advisor with a certificate
      of service; a represented party files an EOIR-28 with it; proof of the
      fee or a fee waiver request; "If filed in paper, the motion must be
      filed in duplicate" ((b)(1)(ii)).
    It states whether the order is the subject of a judicial proceeding, and
      whether the client is the subject of a pending criminal proceeding under
      the Act ((b)(1)(i)). No stay except an in absentia motion ((b)(1)(v)).
    Reconsider: the errors of fact or law, with authority ((b)(2)). Reopen: the
      new facts, affidavits and evidence; the application for relief with it;
      material evidence not available before ((b)(3)).
    No time or number limit: changed country conditions for asylum,
      withholding or CAT ((b)(4)(i)); "a motion to reopen agreed upon by all
      parties and jointly filed" ((b)(4)(iv): only a motion to reopen, and
      only (b)(1)'s limits). An in absentia order: 180 days for exceptional
      circumstances; at any time for lack of notice or custody; the motion
      stays removal; only one ((b)(4)(ii): its own limits, which (b)(4)(iv)
      doesn't name, so a joint in absentia motion keeps them; the attorney
      decides). The day counts are schemas/registers/journey.json court_decisions, as
      on the case's timeline.
  When an order becomes final: 8 CFR 1241.1 -- on waiver of appeal, when the
    appeal time runs out with no appeal, on the BIA's dismissal; an in
    absentia order at once.
  The fee (8 CFR 1003.24, 1103.7(b)(2); EOIR's "Types of Appeals, Motions, and
    Required Fees", updated 10/01/2026): $1,095 (schemas/law/fees.json eoir
    ij_motion); $950 when based only on a no-fee application
    (motion_no_fee_relief); none for an in absentia motion under INA
    240(b)(5)(C)(ii) or a joint motion (1003.24(b)(2)(iii), (v)). Paid only
    through the EOIR Payment Portal (1103.7(a)(1)). A waiver: the judge's,
    on an affidavit or declaration of inability to pay (1003.24(d)); EOIR's
    Form EOIR-26A (Rev. Aug. 2022, "Fee waiver (appeals or motions)", EOIR's
    forms page updated 10/01/2026) is that declaration.
  Matter of M-M-L-J-, 29 I&N Dec. 843 (BIA 2026): one motion to reopen,
    whether with the court or the BIA.
  The package's order: Practice Manual 3.3(c)(i)(D), (E) (version of
    02/20/2020): EOIR-28, cover page, fee receipt or waiver request, the motion,
    the judge's decision, the application, the evidence, the proposed order,
    the proof of service.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from filing_questions import DATE, LINES, TEXT, YES_NO, money, putter, us, value
from filing_questions import iso as _d
import court_pleading as cp
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

TITLE = "Motion to reopen or reconsider (immigration court)"
KINDS = ["Motion to reopen", "Motion to reconsider", "Motion to reopen an in absentia order"]
ABSENTIA = ["Exceptional circumstances", "No notice of the hearing", "In federal or state custody, through no fault of the client"]
_DAY_KEYS = {KINDS[0]: "reopen_days", KINDS[1]: "reconsider_days", KINDS[2]: "in_absentia_reopen_days"}


def days(k: str) -> int:
    """The motion's time limit: 90 to reopen, 30 to reconsider (8 CFR 1003.23(b)(1)), 180 for an in absentia order on exceptional
    circumstances ((b)(4)(ii)), read from schemas/registers/journey.json court_decisions, the same numbers the case's timeline uses."""
    import journey

    return int((journey.settings().get("court_decisions") or {})[_DAY_KEYS[k]])


def joint_reopen(graph) -> bool:
    """A joint motion to reopen: (b)(4)(iv) lifts only (b)(1)'s limits, and only for "a motion to reopen agreed upon by all parties and
    jointly filed". Not a motion to reconsider, and not the in absentia motion, whose 180 days and one motion are (b)(4)(ii)'s own."""
    return value(graph, "ijmotion.joint") == "Yes" and _reopen(graph)
PACKET_PDF = "packet_court_motion.pdf"  # schemas/packets/court_motion.json: the PDF uploaded in ECAS
FILES = {"cover": "ijmotion_cover.pdf", "motion": "ijmotion_motion.pdf", "order": "ijmotion_order.pdf", "service": "ijmotion_service.pdf"}


def kind(graph) -> str | None:
    return value(graph, "ijmotion.kind")


def _absentia(graph) -> bool:
    return kind(graph) == KINDS[2]


def _reopen(graph) -> bool:
    return kind(graph) == KINDS[0]


def _reconsider(graph) -> bool:
    return kind(graph) == KINDS[1]


SECTIONS = [
    ("The order and the motion", "the attorney", [
        ("ijmotion.kind", "Which motion", {"type": "choice", "options": KINDS}, True),
        ("ijmotion.order_date", "The date of the judge's order (the oral decision, or the day a written one was mailed or sent)", DATE, True),
        ("ijmotion.final_on", "The date the order became final (8 CFR 1241.1: appeal waived, the appeal time ran out with no appeal, the BIA dismissed "
                              "the appeal; an in absentia order at once)", DATE, False),
        ("ijmotion.appeal_pending", "Is an appeal of this order pending at the BIA?", YES_NO, True),
        ("ijmotion.departed", "Has the client left the U.S. since the order?", YES_NO, True),
        ("ijmotion.prior_motion", "Has the client already filed this kind of motion in this case, with the court or the BIA?", YES_NO, True),
        ("ijmotion.joint", "Agreed with ICE and filed jointly?", YES_NO, False),
    ]),
    ("Why the client did not appear (in absentia)", "the attorney", [
        ("ijmotion.absentia_ground", "The ground", {"type": "choice", "options": ABSENTIA}, True),
        ("ijmotion.absentia_facts", "What happened, in detail (the client's declaration and the evidence go with it)", LINES, True),
    ], _absentia),
    ("The new facts (motion to reopen)", "the attorney", [
        ("ijmotion.new_facts", "The new facts that will be proven at a reopened hearing (8 CFR 1003.23(b)(3))", LINES, True),
        ("ijmotion.why_new", "Why the evidence is material, and why it was not available and could not have been discovered or presented at the former "
                             "hearing", LINES, True),
        ("ijmotion.relief", "The application for relief filed with the motion, if any (for example Form I-485, EOIR-42B, I-589)", TEXT, False),
        ("ijmotion.country_conditions", "To apply for asylum, withholding or CAT protection on changed country conditions? (no time or number limit: "
                                        "8 CFR 1003.23(b)(4)(i))", YES_NO, False),
    ], _reopen),
    ("The errors (motion to reconsider)", "the attorney", [
        ("ijmotion.errors", "The errors of fact or law in the judge's decision, with authority (8 CFR 1003.23(b)(2))", LINES, True),
    ], _reconsider),
    ("The statements the motion must make (8 CFR 1003.23(b)(1)(i))", "the attorney", [
        ("ijmotion.judicial", "Has the order's validity been, or is it now, the subject of a court case (a petition for review, habeas)?", YES_NO, True),
        ("ijmotion.judicial_details", "If so: its nature and date, the court, and its result or status", LINES, False),
        ("ijmotion.criminal", "Is the client the subject of a pending criminal proceeding under the Immigration and Nationality Act?", YES_NO, True),
        ("ijmotion.criminal_details", "If so: its current status", LINES, False),
    ]),
    ("Fee and stay", "the attorney", [
        ("ijmotion.no_fee_relief", "Based only on an application for relief that has no fee (to reconsider: only on a prior one that had none)?", YES_NO, False),
        ("ijmotion.fee_waiver", "Ask the judge to waive the fee (Form EOIR-26A, signed by the client)?", YES_NO, False),
        ("ijmotion.stay", "Ask the judge for a stay of removal while the motion is pending?", YES_NO, False),
    ]),
    ("The court and service", "the attorney", cp.court_section("ijmotion") + [
        ("ijmotion.detained", "Is the client detained? (the cover page then says DETAINED)", YES_NO, False)]),
    ("The attorney's section", "the attorney", [
        ("ijmotion.argument", "The argument: why the judge should grant the motion, with authority (the attorney writes it)", LINES, True),
    ]),
]
MORE_QUESTIONS = "Each kind of motion has its own questions: those of the motion chosen in \"Which motion\" are shown, the others are not."


def _decisions(status: dict[str, Any]) -> list[tuple[dict, dict]]:
    import journey

    return sorted(((h, h["result"]) for h in (status.get("journey") or {}).get("hearings") or []
                   if (h.get("result") or {}).get("outcome") in journey.DECISIONS[1:] and (h.get("result") or {}).get("decision_date")),
                  key=lambda x: x[1]["decision_date"])


def from_record(status: dict[str, Any], graph) -> None:
    """The latest decision against the client on the case page: the order, its date, whether it is final yet (8 CFR 1241.1), the court."""
    import clock
    import journey

    today = clock.today()
    cp.from_hearings(status, graph, "ijmotion", today)
    found = _decisions(status)
    put = putter(graph, "case page")
    if any(r.get("filing") == "bia" and r.get("mailed_on") for r in status.get("filings") or []):
        put("ijmotion.appeal_pending", "Yes", "an appeal to the BIA is recorded as filed on the case page")
    if not found:
        return
    h, r = found[-1]
    decided = _d(r["decision_date"])
    put("ijmotion.order_date", decided.isoformat(), f"the {r['outcome'].lower()} recorded on the case page")
    put("ijmotion.court", cp.match(h.get("court")), "the court of the hearing that decided the case")
    if r["outcome"] == "Removal ordered in absentia":
        put("ijmotion.kind", KINDS[2], "an in absentia order recorded on the case page")
        put("ijmotion.final_on", decided.isoformat(), "an in absentia order is final at once (8 CFR 1241.1(e))")
    elif r.get("appeal_waived"):
        put("ijmotion.final_on", decided.isoformat(), "appeal waived: final the day of the decision (8 CFR 1241.1(b))")
    else:
        rules = journey.settings().get("court_decisions") or {}
        ends = journey._next_business_day(decided + timedelta(days=rules["bia_appeal_days_asylum"] if r.get("asylum") else rules["bia_appeal_days"]))
        if ends < today and not any(x.get("filing") == "bia" for x in status.get("filings") or []):
            put("ijmotion.final_on", ends.isoformat(), "the appeal time ran out with no appeal recorded (8 CFR 1241.1(c)): confirm none was filed")


def from_case(client_dir: Path, graph) -> None:
    """The fee waiver request's figures, totals, signature and attestation, when the attorney asks the judge to waive the fee (src/eoir26a.py)."""
    import eoir26a

    if eoir26a.asked_by(graph):
        eoir26a.from_case(client_dir, graph)


def derive(graph, today: date):
    import court

    court.derive(graph, today)  # the EOIR-28's facts (8 CFR 1003.23(b)(1)(ii): filed with the motion)
    put = putter(graph, "court_motion.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    put("ijmotion.respondent_name", ", ".join(x for x in (v("applicant.family_name"), " ".join(y for y in (v("applicant.given_name"), v("applicant.middle_name")) if y))
                                              if x) or None, "the client's name (Form EOIR-26A)")
    put("ijmotion.print_name", " ".join(x for x in (v("applicant.given_name"), v("applicant.middle_name"), v("applicant.family_name")) if x) or None,
        "the client's name (Form EOIR-26A)")
    return graph


# -- the deadline and the fee -----------------------------------------------------------------------------------------


def deadline(graph) -> tuple[date | None, str]:
    """(the last day, why): 8 CFR 1003.23(b); a weekend or holiday moves it to the next business day, as on the case's timeline (src/journey.py)."""
    import journey

    v = lambda k: value(graph, k)  # noqa: E731
    k = kind(graph)
    if joint_reopen(graph):
        return None, "A motion to reopen agreed upon by all parties and jointly filed has no time or number limit (8 CFR 1003.23(b)(4)(iv))."
    if k == KINDS[2]:
        ground, order, n = v("ijmotion.absentia_ground"), _d(v("ijmotion.order_date")), days(k)
        if ground in ABSENTIA[1:]:
            return None, "For lack of notice, or federal or state custody through no fault of the client: at any time (8 CFR 1003.23(b)(4)(ii))."
        if not order:
            return None, f"Enter the date of the in absentia order: the {n} days count from it (8 CFR 1003.23(b)(4)(ii))."
        return (journey._next_business_day(order + timedelta(days=n)),
                f"{n} days after the in absentia order of {us(order)}, for exceptional circumstances (8 CFR 1003.23(b)(4)(ii))")
    if k == KINDS[0] and v("ijmotion.country_conditions") == "Yes":
        return None, "To apply for asylum, withholding or CAT protection on changed country conditions: no time or number limit (8 CFR 1003.23(b)(4)(i))."
    if k not in KINDS:
        return None, "Choose the motion: the time limit depends on it."
    final, n = _d(v("ijmotion.final_on")), days(k)
    if not final:
        return None, f"Enter the date the order became final: the {n} days count from it (8 CFR 1003.23(b)(1), 1241.1)."
    return journey._next_business_day(final + timedelta(days=n)), f"{n} days after the order became final on {us(final)} (8 CFR 1003.23(b)(1))"


def fee(graph, today: date) -> tuple[int | None, str]:
    import fees

    v = lambda k: value(graph, k)  # noqa: E731
    eoir = fees.load(today).get("eoir") or {}
    if _absentia(graph) and v("ijmotion.absentia_ground") in ABSENTIA[1:]:
        return 0, "no fee for a motion to reopen an in absentia order for lack of notice or custody, INA 240(b)(5)(C)(ii) (8 CFR 1003.24(b)(2)(iii); EOIR's fee page)"
    if v("ijmotion.joint") == "Yes":
        return 0, "no fee for a motion agreed upon by all parties and jointly filed (8 CFR 1003.24(b)(2)(v))"
    if v("ijmotion.no_fee_relief") == "Yes" and not _absentia(graph):
        return eoir.get("motion_no_fee_relief"), "a motion based only on an application for relief that has no fee (EOIR's fee page; 8 CFR 1103.7(b)(2))"
    return eoir.get("ij_motion"), "a motion to reopen or reconsider before the judge (EOIR's fee page; 8 CFR 1103.7(b)(2))"


def joint(graph) -> bool:
    """Agreed upon by all parties and jointly filed (any kind of motion: no fee, 1003.24(b)(2)(v); no proof of service)."""
    return value(graph, "ijmotion.joint") == "Yes"


def forms_for(graph, forms: list[str]) -> list[str]:
    """Form EOIR-26A only when the attorney asks the judge to waive the fee; no proof of service for a joint motion."""
    return [f for f in forms if (f != "eoir26a" or value(graph, "ijmotion.fee_waiver") == "Yes") and (f != "ijmotion_service" or not joint(graph))]


def title(graph) -> str:
    k = kind(graph) or KINDS[0]
    words = {KINDS[0]: "Motion to Reopen", KINDS[1]: "Motion to Reconsider", KINDS[2]: "Motion to Reopen an In Absentia Order"}[k]
    return f"Respondent's {words}" + (" and Request for a Stay of Removal" if value(graph, "ijmotion.stay") == "Yes" and k != KINDS[2] else "")


def notes(graph, today: date) -> list[dict[str, str]]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = [{"level": "info", "title": "What it is",
            "text": "A written motion to the judge who decided the case (8 CFR 1003.23(b)). The court has no form for it: the office's motion, with a "
                    "cover page, a proposed order and a proof of service (Practice Manual 5.2(b), 2020 version), DRAFT until the attorney approves. "
                    "A represented client files an EOIR-28 with it (8 CFR 1003.23(b)(1)(ii)): it is in the packet."}]
    due, why = deadline(graph)
    out.append({"level": "warn" if due and today > due else "info", "title": "When",
                "text": (f"Filed by {us(due)}: {why}. The court counts it filed when it receives it." if due else why)
                + " A motion doesn't stop or extend the time to appeal to the BIA (Practice Manual 5.7(g), 5.8(g), 2020 version)."})
    amount, label = fee(graph, today)
    waiver = v("ijmotion.fee_waiver") == "Yes" and amount
    out.append({"level": "info", "title": "Fee",
                "text": (f"{money(amount)}: {label}. " if amount else f"None: {label}. ")
                + ("Paid only through the EOIR Payment Portal (epay.eoir.justice.gov); its receipt goes behind the cover page (8 CFR 1003.23(b)(1)(ii), "
                   "1103.7(a)(1)). No Form G-1450. " if amount and not waiver else "")
                + ("Fee waiver: Form EOIR-26A, the client's declaration of inability to pay, goes in its place. If the judge denies it, the motion isn't "
                   "properly filed; the judge gives 15 days to file again with the fee, and the deadline waits (8 CFR 1003.24(d))." if waiver else
                   "A client who can't pay can ask the judge to waive it on Form EOIR-26A (8 CFR 1003.24(d))." if amount else "")})
    out.append({"level": "info", "title": "How it is filed", "text": cp.how_filed(graph, "ijmotion", duplicate=True, joint=joint(graph))})
    if _absentia(graph):
        out.append({"level": "info", "title": "A stay", "text": "Filing a motion to reopen an in absentia order stays removal until the judge decides it; "
                                                                 "only one such motion (8 CFR 1003.23(b)(4)(ii))."})
    elif v("ijmotion.stay") == "Yes":
        out.append({"level": "info", "title": "A stay", "text": "The motion doesn't stop removal unless the judge, the BIA or DHS grants a stay (8 CFR "
                                                                 "1003.23(b)(1)(v)). The BIA expects a stay request to go to DHS first (Matter of "
                                                                 "Herrera-Nunez, 29 I&N Dec. 691 (BIA 2026)): the attorney decides."})
    return out


@producer(ATTORNEY)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = cp.court_problems(graph, "ijmotion") + cp.upload_problems(client_dir, graph, PACKET_PDF)
    if not v("firm.licensing_authority"):
        out.append(held(OFFICE, "The attorney's bar admission isn't set: add it on the Settings page (The firm and the attorney)."))
    if v("ijmotion.appeal_pending") == "Yes":
        out.append("An appeal is pending at the BIA: the judge can reopen or reconsider only \"unless jurisdiction is vested with the Board of Immigration "
                   "Appeals\" (8 CFR 1003.23(b)(1)). File the motion with the BIA instead.")
    if v("ijmotion.departed") == "Yes":
        out.append("The client left the U.S.: a motion to reopen or reconsider \"shall not be made\" after the person's departure, and a departure after "
                   "filing withdraws it (8 CFR 1003.23(b)(1)). The attorney decides.")
    if v("ijmotion.prior_motion") == "Yes" and not joint_reopen(graph) and not (_reopen(graph) and v("ijmotion.country_conditions") == "Yes"):
        out.append("Only one motion to reconsider and one motion to reopen (8 CFR 1003.23(b)(1)); one motion to reopen whether with the court or the "
                   "BIA (Matter of M-M-L-J-, 29 I&N Dec. 843 (BIA 2026)); one in absentia motion ((b)(4)(ii)). The attorney decides whether an exception "
                   "applies.")
    due, _why = deadline(graph)
    if due and today > due:
        out.append(f"Past the deadline ({us(due)}): a late motion is denied unless an exception applies (8 CFR 1003.23(b)(4)). The attorney decides.")
    if v("ijmotion.judicial") == "Yes" and not v("ijmotion.judicial_details"):
        out.append(held(CLIENT, "The court case about the order: its nature and date, the court, and its result or status (8 CFR 1003.23(b)(1)(i))."))
    if v("ijmotion.criminal") == "Yes" and not v("ijmotion.criminal_details"):
        out.append(held(CLIENT, "The pending criminal proceeding: its current status (8 CFR 1003.23(b)(1)(i))."))
    if v("ijmotion.fee_waiver") == "Yes" and fee(graph, today)[0] == 0:
        out.append(held(OFFICE, "No fee for this motion: no fee waiver request is needed. Answer No to the fee waiver."))
    elif v("ijmotion.fee_waiver") == "Yes":
        import eoir26a

        out += eoir26a.problems(client_dir, graph)  # the figures, item 4, the client's signature and the attorney's attestation (src/eoir26a.py)
    return out


# -- the motion -----------------------------------------------------------------------------------------------------


def render(client_dir: Path, graph, today: date) -> None:
    """The cover page, the motion, the proposed order and the proof of service (src/court_pleading.py)."""
    v = lambda k: value(graph, k)  # noqa: E731
    esc = cp.esc
    hidden = {section for section, _w, _items, *when in SECTIONS if when and not when[0](graph)}  # filing_questions.hidden_sections
    required = [key for section, _w, items, *_ in SECTIONS if section not in hidden for key, _l, _s, req in items if req]
    draft = bool(problems(client_dir, graph, today)) or not all(v(k) for k in required)
    k, name = kind(graph) or KINDS[0], esc(cp.respondent(graph))
    head = title(graph)
    flags = ["DETAINED"] * cp.detained(graph, "ijmotion") + ["JOINT MOTION"] * joint(graph)  # Practice Manual 3.3(c)(vi), 2020 version
    cp.cover(client_dir / FILES["cover"], graph, "ijmotion", head, "removal", flags, draft)

    paper = cp.Paper(client_dir / FILES["motion"], head, draft)
    order = _d(v("ijmotion.order_date"))
    when = esc(us(order)) if order else "[the date of the order]"
    paper.p(f"<b>{esc(head.upper())}</b>", paper.center)
    paper.gap(10)
    ask = {KINDS[0]: "reopen these proceedings", KINDS[1]: f"reconsider the decision of {when}",
           KINDS[2]: f"reopen these proceedings and rescind the order of removal entered in absentia on {when}"}[k]
    cite = {KINDS[0]: "1003.23(b)(3)", KINDS[1]: "1003.23(b)(2)", KINDS[2]: "1003.23(b)(4)(ii)"}[k]
    paper.p(f"The respondent, {name} ({esc(cp._a(v('applicant.a_number')))}), through undersigned counsel, moves the Immigration Judge to {ask}. "
            f"8 C.F.R. &sect; {cite}.")
    paper.p("I. The order", paper.heading)
    final = _d(v("ijmotion.final_on"))
    paper.p((f"On {when}, the Immigration Judge ordered the respondent removed in absentia." if k == KINDS[2] else
             f"On {when}, the Immigration Judge issued the decision this motion addresses.")
            + (f" The order became final on {esc(us(final))}. 8 C.F.R. &sect; 1241.1." if final else ""))
    paper.p("II. Timeliness and number", paper.heading)
    due, _why = deadline(graph)
    on_time = due is not None and today <= due
    if joint_reopen(graph):
        paper.p("This motion is agreed upon by all parties and jointly filed; the time and numerical limitations of 8 C.F.R. &sect; 1003.23(b)(1) "
                "\"shall not apply to a motion to reopen agreed upon by all parties and jointly filed.\" 8 C.F.R. &sect; 1003.23(b)(4)(iv).")
    elif k == KINDS[2]:
        if v("ijmotion.absentia_ground") == ABSENTIA[0]:
            paper.p("An order entered in absentia \"may be rescinded only upon a motion to reopen filed within 180 days after the date of the order of "
                    "removal, if the alien demonstrates that the failure to appear was because of exceptional circumstances as defined in section "
                    "240(e)(1) of the Act.\" 8 C.F.R. &sect; 1003.23(b)(4)(ii)."
                    + (f" This motion is filed within {days(k)} days of the order." if on_time else
                       f" [ATTORNEY: the motion is filed after {days(k)} days: state the basis.]"))
        else:
            paper.p("An order entered in absentia \"may be rescinded upon a motion to reopen filed at any time upon the alien's demonstration of lack of "
                    "notice in accordance with section 239(a)(1) or (2) of the Act, or upon the alien's demonstration of the alien's Federal or State "
                    "custody and the failure to appear was through no fault of the alien.\" 8 C.F.R. &sect; 1003.23(b)(4)(ii).")
    elif k == KINDS[0] and v("ijmotion.country_conditions") == "Yes":
        paper.p("The time and numerical limitations of 8 C.F.R. &sect; 1003.23(b)(1) \"shall not apply if the basis of the motion is to apply for "
                "asylum under section 208 of the Act or withholding of removal under section 241(b)(3) of the Act or withholding of removal under the "
                "Convention Against Torture, and is based on changed country conditions arising in the country of nationality or the country to which "
                "removal has been ordered, if such evidence is material and was not available and could not have been discovered or presented at the "
                "previous proceeding.\" 8 C.F.R. &sect; 1003.23(b)(4)(i).")
    else:
        n = days(k)
        paper.p(f"A motion to {'reopen' if k == KINDS[0] else 'reconsider'} \"must be filed within {n} days of the date of entry of a final "
                "administrative order of removal, deportation, or exclusion.\" 8 C.F.R. &sect; 1003.23(b)(1)."
                + (f" This motion is filed within {n} days of the final order." if on_time else
                   f" [ATTORNEY: the motion is not within {n} days of the final order: state the basis.]"))
    if v("ijmotion.prior_motion") == "No":
        paper.p(f"The respondent has not previously filed a {'motion to reconsider' if k == KINDS[1] else 'motion to reopen'} in these proceedings.")
    paper.p("III. Grounds", paper.heading)
    if k == KINDS[1]:
        paper.p("A motion to reconsider \"shall state the reasons for the motion by specifying the errors of fact or law in the immigration judge's "
                "prior decision and shall be supported by pertinent authority.\" 8 C.F.R. &sect; 1003.23(b)(2). The Immigration Judge's decision "
                f"contains these errors: {esc(v('ijmotion.errors') or '[ATTORNEY: the errors of fact or law, with authority]')}")
    elif k == KINDS[0]:
        paper.p("A motion to reopen \"shall state the new facts that will be proven at a hearing to be held if the motion is granted and shall be "
                "supported by affidavits and other evidentiary material.\" 8 C.F.R. &sect; 1003.23(b)(3). The new facts are: "
                f"{esc(v('ijmotion.new_facts') or '[ATTORNEY: the new facts]')}")
        paper.p(f"{esc(v('ijmotion.why_new') or '[ATTORNEY: why the evidence is material and was not available before]')} The evidence is material "
                "and was not available and could not have been discovered or presented at the former hearing. Id.")
        if v("ijmotion.relief"):
            paper.p(f"The respondent's application for relief, {esc(v('ijmotion.relief'))}, and all supporting documents accompany this motion. Id.")
    else:
        ground = v("ijmotion.absentia_ground")
        reason = {ABSENTIA[0]: "because of exceptional circumstances", ABSENTIA[1]: "because the respondent did not receive notice of the hearing",
                  ABSENTIA[2]: "because the respondent was in federal or state custody, through no fault of the respondent"}.get(ground, "[ATTORNEY: the ground]")
        paper.p(f"The respondent did not appear {reason}: {esc(v('ijmotion.absentia_facts') or '[ATTORNEY: what happened]')}")
    paper.p("IV. Argument", paper.heading)
    paper.p(esc(v("ijmotion.argument") or "[ATTORNEY: the argument, with authority.]"))
    paper.p("V. Statements required by 8 C.F.R. &sect; 1003.23(b)(1)(i)", paper.heading)
    paper.p("The validity of the order " + (f"has been or is the subject of a judicial proceeding: {esc(v('ijmotion.judicial_details') or '[ATTORNEY]')}"
                                            if v("ijmotion.judicial") == "Yes" else
                                            "has not been and is not the subject of any judicial proceeding" if v("ijmotion.judicial") == "No" else
                                            "[ATTORNEY: whether it has been or is the subject of a judicial proceeding]") + ".")
    paper.p("The respondent " + (f"is the subject of a pending criminal proceeding under the Act: {esc(v('ijmotion.criminal_details') or '[ATTORNEY]')}"
                                 if v("ijmotion.criminal") == "Yes" else
                                 "is not the subject of any pending criminal proceeding under the Act" if v("ijmotion.criminal") == "No" else
                                 "[ATTORNEY: whether the respondent is the subject of a pending criminal proceeding under the Act]") + ".")
    if k == KINDS[2]:
        paper.p("VI. Stay of removal", paper.heading)
        paper.p("\"The filing of a motion under this paragraph (b)(4)(ii) shall stay the removal of the alien pending disposition of the motion by the "
                "immigration judge.\" 8 C.F.R. &sect; 1003.23(b)(4)(ii).")
    elif v("ijmotion.stay") == "Yes":
        paper.p("VI. Request for a stay of removal", paper.heading)
        paper.p("The respondent asks the Immigration Judge to stay removal while this motion is pending. The filing of this motion does not itself stay "
                "removal, and a stay may be \"specifically granted by the immigration judge.\" 8 C.F.R. &sect; 1003.23(b)(1)(v).")
    amount, _label = fee(graph, today)
    paper.p("Fee", paper.heading)
    paper.p("No filing fee is required. 8 C.F.R. &sect; 1003.24(b)(2)." if amount == 0 else
            "A request for a fee waiver, Form EOIR-26A, accompanies this motion. 8 C.F.R. &sect; 1003.24(d)." if v("ijmotion.fee_waiver") == "Yes" else
            "Proof of payment of the filing fee through the EOIR Payment Portal accompanies this motion. 8 C.F.R. &sect;&sect; 1003.23(b)(1)(ii), 1103.7(a).")
    paper.p("Conclusion", paper.heading)
    paper.p("For these reasons, the respondent respectfully asks the Immigration Judge to grant this motion"
            + (" and to stay removal while it is pending." if v("ijmotion.stay") == "Yes" and k != KINDS[2] else "."))
    cp.signature(paper, graph, today)
    paper.write()

    granted = {KINDS[0]: ["The proceedings are reopened."], KINDS[1]: ["The decision of " + (us(order) if order else "__________") + " is reconsidered."],
               KINDS[2]: ["The in absentia order of " + (us(order) if order else "__________") + " is rescinded and the proceedings are reopened."]}[k]
    cp.proposed_order(client_dir / FILES["order"], graph, "ijmotion", f"the respondent's {head.split(' ', 1)[1]}",
                      ["DHS does not oppose the motion.", "A response to the motion has not been filed with the court.",
                       "Good cause has been established for the motion.", "The court agrees with the reasons stated in the opposition to the motion.",
                       "The motion is untimely per ______________________.", "Other: ________________________________________"],
                      granted + (["Removal is stayed while the motion is pending."] if v("ijmotion.stay") == "Yes" and k != KINDS[2] else [])
                      + (["The application(s) for relief must be filed by ______________.", "The respondent must comply with DHS biometrics instructions "
                          "by ______________."] if k != KINDS[1] else []), draft)
    if not joint(graph):  # Practice Manual 3.2(a), (e) (2020 version): no proof of service for a motion agreed and filed by all parties
        waiver = ", the Form EOIR-26A (fee waiver request)" if "eoir26a" in forms_for(graph, ["eoir26a"]) else ""
        cp.proof_of_service(client_dir / FILES["service"], graph, f"the {head}, the Form EOIR-28{waiver}, the proposed order", draft)
