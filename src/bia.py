"""An appeal to the Board of Immigration Appeals from an immigration judge's
decision: Form EOIR-26 (Rev. Mar. 2026) with the attorney's Form EOIR-27
(Rev. Oct. 2023) -- 8 CFR 1003.38 (eCFR as of 09/30/2026), the EOIR-26's
instructions and EOIR's fee page (data/reference/eoir-26instr.pdf,
eoir-fees.txt, updated 10/01/2026):

  When: RECEIVED by the Board within 10 calendar days of the decision -- 30
    when the judge adjudicated an asylum application and didn't deny it under
    INA 208(a)(2)(A)-(C) -- counted from the oral decision, or the mailing or
    electronic notice of a written one; a weekend or holiday moves it to the
    next business day (1003.38(b), (c)). The EOIR-26's own instructions
    (Rev. Mar. 2026) still say 30 days for every appeal: the earlier
    regulation date is used, and the attorney confirms.
  How: attorneys e-file in ECAS in eligible cases; each represented party
    needs an EOIR-27 for the appeal (1003.38(g)(1)); served on ICE unless
    ICE takes part in ECAS.
  Fee (EOIR, 10/01/2026): $1,060 (no fee for a bond appeal), paid only
    through the EOIR Payment Portal -- its receipt goes with the appeal -- or
    a fee waiver request, Form EOIR-26A.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from filing_questions import DATE, LINES, TEXT, YES_NO, money, putter, us, value
from filing_questions import iso as _d
from holders import ATTORNEY, OFFICE, held, producer

TITLE = "Appeal to the BIA (EOIR-26 and EOIR-27)"
TYPES = ["Merits (removal, asylum, etc.)", "Bond", "Denial of a motion to reopen or reconsider", "Interlocutory"]
DATE_KEY = dict(zip(TYPES, ("bia.date_merits", "bia.date_bond", "bia.date_motion", "bia.date_interlocutory")))
BOARD_BY_MAIL = ["Board of Immigration Appeals", "Clerk's Office", "5107 Leesburg Pike, Suite 2000", "Falls Church, VA 22041"]  # the EOIR-26 instructions, B
SECTIONS = [
    ("The judge's decision", "the attorney", [
        ("bia.decision_type", "What is appealed (item 5: one decision per EOIR-26)", {"type": "choice", "options": TYPES}, True),
        ("bia.decision_date", "Date of the decision (oral), or the date a written one was mailed or sent", DATE, True),
        ("bia.asylum", "Did the judge adjudicate an asylum application, without denying it under INA 208(a)(2)(A)-(C)? (then 30 days)", YES_NO, True),
        ("bia.detained", "The client is (item 3)", {"type": "choice", "options": ["Not detained", "Detained"]}, True),
        ("bia.last_hearing", "Where the last hearing was (item 4: court, city, state)", TEXT, True),
    ]),
    ("The appeal", "the attorney", [
        ("bia.reasons", "Item 6: the reasons. The specific findings of fact and conclusions of law challenged, with authority", LINES, True),
        ("bia.oral_argument", "Item 7: ask for oral argument?", YES_NO, True),
        ("bia.brief", "Item 8: file a separate brief after the briefing schedule?", YES_NO, True),
        ("bia.fee_waiver", "Ask the Board to waive the appeal fee (Form EOIR-26A, signed by the client)?", YES_NO, False),
    ]),
    ("Service and appearance", "the attorney", [
        ("eoir.electronic_service", "An ECAS case with ICE taking part (no paper service needed)?", YES_NO, False),
        ("eoir.dhs_address", "The ICE (OPLA) office served, its address", TEXT, True),
        ("eoir.primary", "EOIR-27: primary attorney?", {"type": "choice", "options": ["Primary", "Non-primary"]}, True),
        ("eoir.pro_bono", "EOIR-27: pro bono?", YES_NO, True),
    ]),
]
MORE_QUESTIONS = "The decision's details come from the hearing result recorded on the case page."


def from_record(status: dict[str, Any], graph) -> None:
    """The latest decision recorded on the case page (src/journey.py, a hearing's result)."""
    hearings = [h for h in (status.get("journey") or {}).get("hearings") or [] if (h.get("result") or {}).get("decision_date")
                and h["result"].get("outcome") == "Decision: removal ordered or relief denied"]  # the client lost: theirs to appeal
    if not hearings:
        return
    h = max(hearings, key=lambda x: x["result"]["decision_date"])
    r = h["result"]
    put = putter(graph, "case page")
    put("bia.decision_date", r["decision_date"], f"the hearing result recorded on the case page ({r.get('outcome')})")
    put("bia.decision_type", TYPES[0], "a decision at a hearing")
    put("bia.asylum", "Yes" if r.get("asylum") else None, "the hearing result: an asylum decision")
    put("bia.last_hearing", h.get("court"), "the hearing on the case page")


def from_case(client_dir: Path, graph) -> None:
    """The fee waiver request's figures, totals, signature and attestation, when the attorney asks the Board to waive the fee (src/eoir26a.py)."""
    import eoir26a

    if eoir26a.asked_by(graph):
        eoir26a.from_case(client_dir, graph)


def forms_for(graph, forms: list[str]) -> list[str]:
    """Form EOIR-26A (the client's fee waiver request) after the EOIR-26, only when the attorney asks the Board to waive the fee (8 CFR 1003.24(d))."""
    return [*forms, "eoir26a"] if value(graph, "bia.fee_waiver") == "Yes" and "eoir26a" not in forms else list(forms)


def derive(graph, today: date):
    import court

    court.derive(graph, today)
    put = putter(graph, "bia.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    name = " ".join(x for x in (v("applicant.given_name"), v("applicant.middle_name"), v("applicant.family_name")) if x)
    anum = str(v("applicant.a_number") or "").upper().replace("A", "").replace("-", "")
    put("bia.parties", f"{name}: A{anum}" if name and anum else None, "the client's name and A-Number")
    put("bia.client_name", name or None, "the client's name")
    put("bia.city_state_zip", ", ".join(x for x in (v("eoir.city"), " ".join(y for y in (v("eoir.state"), v("eoir.zip")) if y)) if x) or None,
        "the client's home address")
    put("bia.firm_city_state_zip", ", ".join(x for x in (v("firm.city"), " ".join(y for y in (v("firm.state"), v("firm.zip")) if y)) if x) or None, "the firm")
    put("bia.served_on", "Assistant Chief Counsel, DHS-ICE (OPLA)", "the opposing party (the EOIR-26 instructions, C)")
    if v("bia.decision_type") in DATE_KEY:
        put(DATE_KEY[v("bia.decision_type")], v("bia.decision_date"), "the decision's date, beside the box checked in item 5")
    if v("eoir.dhs_address") and v("eoir.electronic_service") != "Yes":
        put("bia.serve_ice", "Yes", "served on ICE on paper")
    return graph


def due(graph) -> date | None:
    import journey

    decided = _d(value(graph, "bia.decision_date"))
    if not decided:
        return None
    rules = journey.settings().get("court_decisions") or {}
    days = rules.get("bia_appeal_days_asylum", 30) if value(graph, "bia.asylum") == "Yes" else rules.get("bia_appeal_days", 10)
    return journey._next_business_day(decided + timedelta(days=days))


def fee(graph, today: date) -> tuple[int | None, str]:
    import fees

    eoir = fees.load(today).get("eoir") or {}
    if value(graph, "bia.decision_type") == "Bond":
        return eoir.get("eoir26_bond"), "a bond appeal: no fee (EOIR)"
    return eoir.get("eoir26"), "an appeal from a judge's decision (EOIR's fee page)"


def notes(graph, today: date) -> list[dict[str, str]]:
    out = []
    last = due(graph)
    if last:
        days = "30" if value(graph, "bia.asylum") == "Yes" else "10"
        out.append({"level": "warn", "title": "When",
                    "text": f"The Board must RECEIVE it by {us(last)} ({days} days, 8 CFR 1003.38(b); a weekend or holiday moves it to the next business day). "
                            "No mailbox rule. The EOIR-26's own instructions (Rev. Mar. 2026) still say 30 days for every appeal: the regulation's "
                            "earlier date is shown; the attorney confirms."})
    amount, why = fee(graph, today)
    waiver = value(graph, "bia.fee_waiver") == "Yes"
    out.append({"level": "info", "title": "Fee", "text": f"{money(amount)}: {why}. Paid only through the EOIR Payment Portal (epay.eoir.justice.gov): "
                                                          "attach its receipt, or Form EOIR-26A (fee waiver). An unpaid appeal leaves the judge's decision final."
                                                          + (" The fee waiver request, Form EOIR-26A, is in this packet in the receipt's place: the client's own figures and "
                                                             "signature, the attorney's attestation (the Fee waiver tab)." if waiver else "")})
    out.append({"level": "info", "title": "How it is filed",
                "text": "Attorneys e-file in ECAS (BIA Practice Manual 2.1) with an EOIR-27 for each represented party. On paper: "
                        + ", ".join(BOARD_BY_MAIL) + "; served on ICE with the proof of service in item 12."})
    return out


@producer(OFFICE)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    out = []
    last = due(graph)
    if last and today > last:
        out.append(held(ATTORNEY, f"Past the deadline ({us(last)}): a late appeal is dismissed. Consider a motion to reopen or reconsider with the judge."))
    if not value(graph, "firm.eoir_id"):
        out.append("The attorney's EOIR ID isn't set: add it on the Settings page (The firm and the attorney).")
    if not value(graph, "firm.licensing_authority"):
        out.append("The attorney's bar admission isn't set: add it on the Settings page (The firm and the attorney).")
    if value(graph, "bia.oral_argument") == "Yes" and value(graph, "bia.brief") == "No":
        out.append(held(ATTORNEY, "Oral argument without a brief: the Board ordinarily won't grant it (the EOIR-26, item 7), and item 6 must say why a three-member panel is warranted."))
    if value(graph, "bia.fee_waiver") == "Yes":
        import eoir26a

        out += [p for p in eoir26a.problems(client_dir, graph) if p != eoir26a.EOIR_ID_FIRST]  # the EOIR ID is already said above
    return out
