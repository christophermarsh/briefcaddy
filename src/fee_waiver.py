"""A fee waiver request, Form I-912 (edition 07/22/25) -- uscis.gov/i-912
(updated 06/05/2026) and the Form I-912 Instructions (data/reference/):

  Which forms (of those the firm files): the I-90, I-751, I-765 (not DACA),
    N-336, N-400, N-600, an I-485 based on asylum status, and an I-290B whose
    underlying application was fee exempt, waived or waivable. Fees created by
    Pub. L. 119-21 can never be waived: they are paid separately even with an
    I-912.
  On what basis (one or more, each with its evidence): a means-tested benefit
    the client (or their spouse, or the parent of a child) receives now;
    household income at or below 150% of the Federal Poverty Guidelines; or a
    financial hardship.
  How: attached to the application it waives -- never alone, and never after
    the application. So the I-912 isn't a separate mailing here: choose the
    form it waives, and that form's packet carries the I-912, drops the card
    authorization for the waived fee, and its cover letter says so.

The 150% figure is computed from the federal poverty guidelines the firm keeps
for the I-864 (schemas/law/family_settings.json, the 100% column: the HHS
guidelines themselves). USCIS's own I-912P page still shows the 2024 figures
and is marked archived -- not used.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from filing_questions import DATE, LINES, TEXT, YES_NO, putter, value
from holders import ATTORNEY, OFFICE, held, producer

TITLE = "Fee waiver request (I-912)"
# filing code -> what the I-912 lists in Part 3
ELIGIBLE = {"n400": "N-400", "i90": "I-90", "i751": "I-751", "n600": "N-600", "n336": "N-336", "ead": "I-765", "asylee": "I-485",
            "i290b": "I-290B"}
LABELS = {"n400": "N-400 (citizenship)", "i90": "I-90 (green card renewal)", "i751": "I-751 (removing conditions)", "n600": "N-600 (certificate of citizenship)",
          "n336": "N-336 (hearing on a denied N-400)", "ead": "I-765 (work permit)", "asylee": "I-485 (an asylee's green card)",
          "i290b": "I-290B (motion or appeal)"}
STATUS = {"n400": "LAWFUL PERMANENT RESIDENT", "i90": "LAWFUL PERMANENT RESIDENT", "i751": "CONDITIONAL PERMANENT RESIDENT",
          "n600": "LAWFUL PERMANENT RESIDENT", "n336": "LAWFUL PERMANENT RESIDENT", "asylee": "ASYLEE"}
SECTIONS = [
    ("The request", "the attorney", [
        ("feewaiver.filing", "Which application the I-912 goes with", {"type": "choice", "options": list(LABELS.values())}, True),
        ("feewaiver.basis_benefit", "Part 1, 1.A: the client (or spouse, or a child's parent) receives a means-tested benefit now?", YES_NO, False),
        ("feewaiver.basis_income", "Part 1, 1.B: household income at or below 150% of the poverty guidelines?", YES_NO, False),
        ("feewaiver.basis_hardship", "Part 1, 1.C: a financial hardship?", YES_NO, False),
        ("feewaiver.current_status", "Part 1, 2: the client's current immigration status", TEXT, False),
    ]),
    ("The means-tested benefit (Part 4)", "the paralegal", [
        ("feewaiver.benefit_person", "Who receives it (full name)", TEXT, True),
        ("feewaiver.benefit_relationship", "Their relationship to the client (SELF, SPOUSE, PARENT)", TEXT, True),
        ("feewaiver.benefit_agency", "The agency that awards it", TEXT, True),
        ("feewaiver.benefit_type", "The benefit (e.g. MEDICAID, SNAP)", TEXT, True),
        ("feewaiver.benefit_awarded", "Date it was awarded", DATE, False),
        ("feewaiver.benefit_expires", "Date it expires or must be renewed", DATE, False),
    ], lambda g: value(g, "feewaiver.basis_benefit") == "Yes"),
    ("Household income (Part 5)", "the paralegal", [
        ("feewaiver.employment", "Employment status", {"type": "choice", "options": ["Employed", "Unemployed", "Retired", "Other"]}, True),
        ("feewaiver.household_size", "Household size (the client and everyone counted in the household)", TEXT, True),
        ("feewaiver.earners", "How many in the household earn income (including the client)", TEXT, True),
        ("feewaiver.own_income", "The client's annual income (US$)", TEXT, True),
        ("feewaiver.others_income", "The other household members' annual income (US$)", TEXT, False),
        ("feewaiver.changed_since_taxes", "Has anything changed since the last federal tax return (marriage, income, dependents)?", YES_NO, True),
        ("feewaiver.income_explanation", "If no tax return, or something changed: the explanation", LINES, False),
    ], lambda g: value(g, "feewaiver.basis_income") == "Yes"),
    ("Financial hardship (Part 6)", "the attorney", [
        ("feewaiver.hardship", "The situation: the expenses, debts and income losses, in detail", LINES, True),
        ("feewaiver.monthly_expenses", "Total monthly expenses and liabilities (US$)", TEXT, True),
    ], lambda g: value(g, "feewaiver.basis_hardship") == "Yes"),
]
MORE_QUESTIONS = "Each basis answered Yes above adds its own questions (Parts 4, 5 or 6)."


def filing_of(graph) -> str | None:
    """The filing code the I-912 goes with (the answer above)."""
    chosen = value(graph, "feewaiver.filing")
    return next((code for code, label in LABELS.items() if label == chosen), None)


def applies(filing: str | None, graph) -> bool:
    return bool(filing) and filing in ELIGIBLE and filing_of(graph) == filing


def _dollars(v: Any) -> int | None:
    digits = re.sub(r"[^\d]", "", str(v or "").split(".")[0])
    return int(digits) if digits else None


def threshold(graph) -> tuple[int | None, str]:
    """150% of the federal poverty guideline for the household size (the client's state picks the table)."""
    size = _dollars(value(graph, "feewaiver.household_size"))
    import family

    guides = family.settings().get("poverty_guidelines") or {}
    state = str(value(graph, "applicant.physical_state") or "").upper()
    table = guides.get({"AK": "alaska", "HI": "hawaii"}.get(state, "contiguous")) or {}
    p100 = table.get("p100") or {}
    if not size or not p100:
        return None, "household size or guidelines missing"
    step = p100.get("each_additional")
    if str(size) in p100:
        base = p100[str(size)]
    elif size == 1 and "2" in p100 and step:
        base = p100["2"] - step  # the guidelines grow by the same amount per person: a household of 1 is 2's less one step
    elif step and "8" in p100:
        base = p100["8"] + (size - 8) * step
    else:
        return None, "household size outside the table"
    return base * 3 // 2, f"150% of the {guides.get('effective', '')[:4]} federal poverty guideline for {size} ({guides.get('source', 'I-864P')})"


def from_case(client_dir: Path, graph) -> None:
    """The client's monthly money from their page (src/eoir26a.py), so the I-912 asks nothing the client has already answered for the EOIR-26A."""
    import eoir26a

    eoir26a.figures_into(graph, client_dir)


def monthly(graph) -> dict[str, Any] | None:
    """The client's own monthly totals from the EOIR-26A's lines (src/eoir26a.py), with the annual figure worked out and the arithmetic written out:
    {"income", "expense": Decimal or None, "annual": Decimal or None, "why": the sentence}. None when none of the four income lines is answered."""
    import eoir26a

    t = eoir26a.totals(graph)
    if t["income"] is None and t["expense"] is None:
        return None
    annual = t["income"] * 12 if t["income"] is not None else None
    return {"income": t["income"], "expense": t["expense"], "annual": annual,
            "why": (f"the client's own monthly income {eoir26a.usd(t['income'])} (their EOIR-26A answers) times 12 months = {eoir26a.usd(annual)} a year" if annual is not None else ""),
            "why_expenses": f"the client's monthly expenses {eoir26a.usd(t['expense'])} (their EOIR-26A answers)" if t["expense"] is not None else ""}


def derive(graph, today: date):
    put = putter(graph, "feewaiver.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    code = filing_of(graph)
    own = monthly(graph)  # the client's monthly answers first: asked once, and more their own than the N-400's household figure below
    if own:
        put("feewaiver.own_income", f"{own['annual']:,.2f}" if own["annual"] is not None else None, own["why"])
        put("feewaiver.monthly_expenses", f"{own['expense']:,.2f}" if own["expense"] is not None else None, own["why_expenses"])
    put("feewaiver.full_name", " ".join(x for x in (v("applicant.given_name"), v("applicant.middle_name"), v("applicant.family_name")) if x) or None,
        "the client's name")
    put("feewaiver.a_number", v("applicant.a_number"), "the client's A-Number")
    put("feewaiver.dob", v("applicant.dob"), "the client's date of birth")
    put("feewaiver.forms", ELIGIBLE.get(code), "the application it goes with")
    put("feewaiver.total_forms", "1" if code else None, "one form")
    put("feewaiver.current_status", STATUS.get(code), "the case's track")
    put("feewaiver.household_size", v("n400.household_size"), "the N-400's household size")
    put("feewaiver.own_income", v("n400.household_income"), "the N-400's household income")
    own, others = _dollars(v("feewaiver.own_income")), _dollars(v("feewaiver.others_income")) or 0
    put("feewaiver.total_income", f"{own + others:,}" if own is not None else None, "the client's income plus the household's")
    return graph


def fee(graph, today: date) -> tuple[int, str]:
    return 0, "no fee for the I-912"


def notes(graph, today: date) -> list[dict[str, str]]:
    code = filing_of(graph)
    out = [{"level": "info", "title": "Where it goes",
            "text": (f"Inside the {ELIGIBLE[code]} packet: building that packet now adds this I-912, drops the card authorization for the waived fee, "
                     "and the cover letter says a waiver is requested. Never mailed alone, never after the application."
                     if code else "Choose the application it goes with: the I-912 is filed inside it, never alone.")}]
    own = monthly(graph)
    if own and (own["why"] or own["why_expenses"]):
        fact = graph.get("feewaiver.own_income")
        shown = f"{own['annual']:,.2f}" if own["annual"] is not None else None
        review = getattr(fact, "review", None) if fact is not None else None
        if fact is not None and shown is not None and fact.status == "resolved" and fact.value not in (None, "", shown):  # a person's own figure won
            text = (f"The annual income on this form is the figure {review.resolved_by if review is not None and review.resolved_by else 'a person'} typed: ${fact.value}. "
                    f"The client's monthly answers give: {own['why']}.")
        else:
            text = "Asked once: " + "; ".join(x for x in (own["why"], own["why_expenses"]) if x) + ". The same answers fill the EOIR-26A when there is one."
        out.append({"level": "info", "title": "From the client's monthly answers", "text": text})
    limit, why = threshold(graph)
    if limit:
        out.append({"level": "info", "title": "150% of the poverty guidelines", "text": f"${limit:,}: {why}."})
    if code == "i290b":
        out.append({"level": "warn", "title": "An I-290B", "text": "Waivable only when the application it challenges was fee exempt, waived, or eligible for a "
                                                                  "waiver (uscis.gov/i-912): the attorney confirms."})
    if code == "ead":
        out.append({"level": "warn", "title": "Not waivable", "text": "A Pub. L. 119-21 work-permit fee can't be waived: it is still paid, on its own G-1450."})
    return out


@producer(OFFICE)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    if not any(v(k) == "Yes" for k in ("feewaiver.basis_benefit", "feewaiver.basis_income", "feewaiver.basis_hardship")):
        out.append("Choose at least one basis (Part 1): a means-tested benefit, income at or below 150%, or a financial hardship.")
    limit, _why = threshold(graph)
    total = _dollars(v("feewaiver.total_income"))
    if v("feewaiver.basis_income") == "Yes" and limit and total is not None and total > limit:
        out.append(held(ATTORNEY, f"Household income ${total:,} is above 150% of the poverty guidelines (${limit:,}): the income basis doesn't apply."))
    if filing_of(graph) == "asylee" and v("applicant.filing_category") == "Refugee (INA 207)":
        out.append("A refugee's I-485 has no fee: no waiver is needed.")
    return out
