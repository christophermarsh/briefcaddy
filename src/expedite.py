"""Asking USCIS to expedite a pending case -- uscis.gov/forms/filing-guidance/
how-to-make-an-expedite-request (data/reference/expedite-page.txt):

  When: after the receipt notice; after anything pending with the client is
    done (biometrics, a request for evidence); only once (a second request
    slows it down). A travel document: at least 45 days before leaving --
    within 15 days, the Emergency Travel page instead.
  Why (case by case, USCIS's sole discretion, with evidence): severe financial
    loss (not caused by a late filing or late RFE answer; needing a work permit
    alone isn't enough); an emergency or urgent humanitarian situation; a
    nonprofit's cultural or social interest; a government interest; a clear
    USCIS error.
  How: the USCIS Contact Center (800-375-5283) or the online account's secure
    message ("expedite"), with the receipt number; the evidence uploaded to
    the online account. An appeal or motion at the AAO: a letter marked
    "EXPEDITE REQUEST", mailed or faxed to the AAO. An asylum interview: the
    Contact Center. A BIA appeal: the BIA's own procedure.

There is no form: this builds a one-page request sheet -- the case, the
criterion, why, the evidence -- for the call or the message, and to keep.
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from filing_questions import DATE, LINES, TEXT, putter, us, value
from filing_questions import iso as _d
from holders import CLIENT, OFFICE, held, producer

TITLE = "Expedite request (USCIS Contact Center or online account)"
CRITERIA = ["Severe financial loss to a company or person", "Emergency or urgent humanitarian situation", "Pressing or critical need to travel (I-131)",
            "Nonprofit organization's cultural or social interest", "Government interest", "Clear USCIS error"]
SECTIONS = [
    ("The case", "the attorney", [
        ("expedite.receipt", "The pending case's receipt number", TEXT, True),
        ("expedite.form", "Its form (e.g. I-485, I-765, I-131)", TEXT, True),
        ("expedite.criterion", "USCIS's criterion it meets", {"type": "choice", "options": CRITERIA}, True),
        ("expedite.needed_by", "Needed by (a travel date, a job start, a benefit cut-off)", DATE, False),
    ]),
    ("The request", "the attorney", [
        ("expedite.reason", "Why it is urgent: specific, with dates", LINES, True),
        ("expedite.evidence", "The evidence, one item per line (a doctor's letter, a death certificate, an employer's letter...)", LINES, True),
    ]),
]


def derive(graph, today: date):
    """The case still pending: the latest receipt with no decision after it."""
    import journey

    put = putter(graph, "expedite.derive")
    ns = journey.notices(graph)
    pending = [n for n in ns if n["kind"] == "receipt" and not any(m["receipt"] == n["receipt"] and m["kind"] in journey.CLOSED for m in ns)]
    if pending:
        put("expedite.receipt", pending[-1]["receipt"], f"the {pending[-1]['form'] or 'USCIS'} receipt notice of {us(_d(pending[-1]['date']))}, still pending")
        put("expedite.form", pending[-1]["form"], "the receipt notice")
    return graph


def notes(graph, today: date) -> list[dict[str, str]]:
    form, crit = str(value(graph, "expedite.form") or "").upper(), value(graph, "expedite.criterion")
    out = [{"level": "info", "title": "How",
            "text": "Call the USCIS Contact Center (800-375-5283) or send a secure message from the client's USCIS online account (reason: expedite), "
                    "with the receipt number; upload the evidence to the online account. Ask once: a second request slows it down."}]
    if form == "I-290B":
        out.append({"level": "info", "title": "An appeal or motion", "text": "Not the Contact Center: mail or fax a letter marked \"EXPEDITE REQUEST\", with the "
                                                                             "evidence, to the AAO (its Processing Requests page)."})
    if form == "I-589":
        out.append({"level": "info", "title": "Asylum", "text": "An asylum office director may schedule the interview early: ask through the Contact Center."})
    if crit == "Pressing or critical need to travel (I-131)":
        out.append({"level": "info", "title": "Travel", "text": "Ask at least 45 days before leaving; within 15 days, use USCIS's Emergency Travel page instead. "
                                                               "Vacation isn't a pressing need."})
    if crit == "Severe financial loss to a company or person":
        out.append({"level": "info", "title": "Financial loss", "text": "Not if the urgency comes from a late filing or a late answer to a request for evidence; "
                                                                       "needing a work permit alone, without other compelling factors, isn't enough."})
    return out


@producer(OFFICE)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    import journey

    out = []
    ns = journey.notices(graph)
    receipt = value(graph, "expedite.receipt")
    if receipt and not any(n["receipt"] == receipt and n["kind"] == "receipt" for n in ns):
        out.append(held(CLIENT, f"No receipt notice for {receipt} in the folder: USCIS takes an expedite request only after the receipt notice."))
    open_rfe = [n for n in ns if n["kind"] in ("rfe", "noid") and n["receipt"] == receipt
                and not any(m["receipt"] == n["receipt"] and (m["date"] or "") > (n["date"] or "") for m in ns)]
    if open_rfe:
        out.append("A request for evidence is open on this case: answer it first (USCIS's expedite page).")
    soon = [n for n in ns if n["kind"] == "biometrics" and n["receipt"] == receipt and _d(n.get("appointment")) and _d(n["appointment"]) >= today]
    if soon:
        out.append(f"Biometrics are still to come ({us(_d(soon[-1]['appointment']))}): attend first, then ask.")
    needed = _d(value(graph, "expedite.needed_by"))
    if value(graph, "expedite.criterion") == "Pressing or critical need to travel (I-131)" and needed and needed <= today + timedelta(days=15):
        out.append(f"Travel on {us(needed)} is within 15 days: use USCIS's Emergency Travel page (an appointment), not an expedite request.")
    return out


def render(client_dir: Path, graph, today: date) -> None:
    """expedite_request.pdf: the one-page request -- for the call or the message, and for the file."""
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter as page
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    v = lambda k: value(graph, k)  # noqa: E731
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=10, leading=13)
    head = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=14, leading=18)
    sub = ParagraphStyle("s", fontName="Helvetica", fontSize=8.5, leading=11, textColor=colors.HexColor("#555555"))
    name = " ".join(x for x in (v("applicant.given_name"), v("applicant.middle_name"), v("applicant.family_name")) if x) or "[client]"
    needed = _d(v("expedite.needed_by"))
    rows: list[list[Any]] = [["Client", name], ["A-Number", v("applicant.a_number") or "[none]"], ["Date of birth", us(_d(v("applicant.dob")))],
                             ["Receipt number", v("expedite.receipt") or "[receipt]"], ["Form", v("expedite.form") or "[form]"],
                             ["Criterion (USCIS)", v("expedite.criterion") or "[criterion]"], ["Needed by", us(needed) if needed else "—"]]
    table = Table([[Paragraph(f"<b>{escape(a)}</b>", body), Paragraph(escape(str(b)), body)] for a, b in rows], colWidths=[1.7 * inch, 5.1 * inch])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    evidence = [x.strip() for x in str(v("expedite.evidence") or "").splitlines() if x.strip()]
    story = [Paragraph("EXPEDITE REQUEST", head), Paragraph(escape(f"Prepared {us(today)}. DRAFT for the attorney's review."), sub), Spacer(1, 8), table,
             Spacer(1, 12), Paragraph("<b>Why it is urgent</b>", body), Spacer(1, 4),
             Paragraph(escape(str(v("expedite.reason") or "[the reason]")).replace("\n", "<br/>"), body), Spacer(1, 12),
             Paragraph("<b>Evidence (uploaded to the client's USCIS online account)</b>", body), Spacer(1, 4),
             Paragraph("<br/>".join(f"{i}. {escape(x)}" for i, x in enumerate(evidence, start=1)) or "[the evidence]", body), Spacer(1, 12),
             Paragraph("<b>Steps</b>", body), Spacer(1, 4),
             Paragraph("1. Call the USCIS Contact Center (800-375-5283), or send a secure message from the online account, reason \"expedite\"; with the "
                       "receipt number, and read the reason above.<br/>2. Upload the evidence to the online account.<br/>3. Write down the date, the "
                       "reference number and who you spoke to; record it on the case page. Ask once.", body)]
    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=page, leftMargin=0.7 * inch, rightMargin=0.7 * inch, topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                      title="Expedite request").build(story)
    (client_dir / "expedite_request.pdf").write_bytes(out.getvalue())
