"""The immigration court's paper, for a filing the court has no form for -- a
written bond request (src/bond.py), a motion to reopen or reconsider
(src/court_motion.py): the cover page, the signature, the proposed order and
the proof of service, and the court it goes to.

Where each piece comes from:

  The cover page and caption: Immigration Court Practice Manual 3.3(c)(vi) and
    Appendix F (version released 02/20/2020, data/reference/ic_practice_manual.pdf;
    justice.gov refuses automated reading of the current online manual, so the
    attorney compares): the filer's name and address top left, DETAINED top
    right when the client is detained, the court's city and state, the
    respondent's name and A-Number, the kind of proceeding, the judge and the
    next hearing, the title.
  The proposed order: "all motions must be accompanied by the appropriate
    proposed order for the Immigration Judge's signature" (Practice Manual
    5.2(b), Appendix Q, 2020 version).
  The proof of service: 8 CFR 1003.32(c) (eCFR, current as of 10/01/2026): a
    paper filing is served on DHS with "a certificate of service"; the judge
    "will not consider any documents or applications that do not contain a
    certificate of service unless service is made on the record". When every
    party files in ECAS, EOIR serves it and no service is needed (1003.32(a)).
    Its contents: Practice Manual 3.2(e)(i), Appendix G (2020 version): the
    party served, the complete address, the date, the means, the document,
    the server's name and signature. The date and the means are the actual
    ones (3.2(d)): left for the person who serves it.
  Where a paper filing goes: schemas/law/immigration_courts.json (each court's
    own page on justice.gov, read 10/02/2026); another court is typed by the
    attorney with where its address was read.

Every sentence here is DRAFT for the attorney (docs/attorney_review.md).
"""

from __future__ import annotations

import io
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from filing_questions import TEXT, YES_NO, putter, us, value
import schema_path
from holders import CLIENT, OFFICE, held, producer

COURTS_FILE = schema_path.path("law", "immigration_courts")
OTHER = "Another court (type its address below)"
ECAS_URL = "https://www.justice.gov/eoir/ecas-attorneys-and-accredited-representatives"
# EOIR's ECAS page (read 10/02/2026), "Are there any file format or file size limits for uploading documents?"
ECAS_LIMIT_MB = 25
ECAS_RULE = "PDF (or JPG), 25 MB or less per document, at least 300 DPI"
OPLA = "ICE Office of the Principal Legal Advisor"  # 8 CFR 1003.23(b)(1)(ii): who a respondent's motion is served on


def courts() -> dict[str, dict[str, Any]]:
    return json.loads(COURTS_FILE.read_text(encoding="utf-8"))["courts"]


def court_names() -> list[str]:
    return [*courts(), OTHER]


def court_section(p: str) -> list[tuple[str, str, dict, bool]]:
    """The questions every court filing asks about where it goes and how ICE is served."""
    return [(f"{p}.court", "The immigration court it is filed with", {"type": "choice", "options": court_names()}, True),
            (f"{p}.court_other", "Another court: its name", TEXT, False),
            (f"{p}.court_other_street", "Another court: its street address, with the suite", TEXT, False),
            (f"{p}.court_other_city", "Another court: city, state and ZIP code", TEXT, False),
            (f"{p}.court_other_source", "Another court: where its address was read (EOIR's page for the court) and the date", TEXT, False),
            ("eoir.electronic_service", "Filed in ECAS, with ICE taking part (EOIR serves ICE electronically)?", YES_NO, True),
            ("eoir.dhs_address", "On paper: the ICE (OPLA) office served, its full address", TEXT, False),
            (f"{p}.judge", "The immigration judge, for the cover page (if known)", TEXT, False)]


def match(text: Any) -> str | None:
    """The court a hearing's free-text court names ("Boston", "Krome"), if it is one of ours."""
    t = str(text or "").lower()
    if not t:
        return None
    for word, name in (("krome", "Miami Krome (Detained) Immigration Court"), ("chelmsford", "Chelmsford Immigration Court"),
                       ("boston", "Boston Immigration Court"), ("orlando", "Orlando Immigration Court"), ("miami", "Miami Immigration Court")):
        if word in t:
            return name
    return None


def from_hearings(status: dict[str, Any], graph, p: str, today: date) -> None:
    """The court, the judge and the next hearing, from the hearings entered on the case page (src/journey.py)."""
    hearings = sorted((status.get("journey") or {}).get("hearings") or [], key=lambda h: h.get("date") or "")
    if not hearings:
        return
    put = putter(graph, "case page")
    latest = hearings[-1]
    put(f"{p}.court", match(latest.get("court")), f"the court of the {latest.get('date')} hearing on the case page")
    put(f"{p}.judge", latest.get("judge"), f"the judge of the {latest.get('date')} hearing on the case page")
    put(f"{p}.detained", "Yes" if latest.get("detained") else None, f"the {latest.get('date')} hearing on the case page is marked detained")
    upcoming = [h for h in hearings if not h.get("result") and (h.get("date") or "") >= today.isoformat()]
    if upcoming:
        h = upcoming[0]
        put(f"{p}.next_hearing", h["date"], "the next hearing on the case page")
        put(f"{p}.next_hearing_time", h.get("time"), "the next hearing on the case page")


def court_of(graph, p: str) -> dict[str, Any] | None:
    """The chosen court: {name, address lines, city (for the caption), hours, phone, source}; None until it is known."""
    v = lambda k: value(graph, k)  # noqa: E731
    name = v(f"{p}.court")
    if name in courts():
        c = courts()[name]
        return {"name": name, "address": list(c["address"]), "city": c["city"], "hours": c.get("window_filing_hours"), "phone": c.get("phone"),
                "source": f"{c['page']} (updated {c['page_updated']})", "detained": bool(c.get("detained"))}
    if name == OTHER and v(f"{p}.court_other") and v(f"{p}.court_other_street") and v(f"{p}.court_other_city") and v(f"{p}.court_other_source"):
        city = re.sub(r"\s*\d{5}(-\d{4})?\s*$", "", str(v(f"{p}.court_other_city"))).strip().rstrip(",")
        return {"name": v(f"{p}.court_other"), "address": [v(f"{p}.court_other_street"), v(f"{p}.court_other_city")], "city": city,
                "hours": None, "phone": None, "source": v(f"{p}.court_other_source"), "detained": False}
    return None


def ecas(graph) -> bool:
    return value(graph, "eoir.electronic_service") == "Yes"


@producer(OFFICE)
def court_problems(graph, p: str) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    if v(f"{p}.court") == OTHER and not court_of(graph, p):
        out.append("Another court: type its name, street address, city, state and ZIP code, and where the address was read (EOIR's page for the court).")
    if not ecas(graph) and not v("eoir.dhs_address"):
        out.append("On paper: the ICE (OPLA) office served and its address (the proof of service), or answer that it is filed in ECAS with ICE taking part.")
    if not v("firm.eoir_id"):
        out.append("The attorney's EOIR ID isn't set: add it on the Settings page (The firm and the attorney).")
    if not v("applicant.a_number"):
        out.append(held(CLIENT, "The client's A-Number (on the Notice to Appear): the court files everything by it."))
    return out


def how_filed(graph, p: str, duplicate: bool, joint: bool = False) -> str:
    """Where and how it is filed, for the panel: ECAS, or on paper to the court (8 CFR 1003.32). joint: a motion agreed and filed by
    both parties needs no proof of service (Practice Manual 3.2(a), (e), 2020 version)."""
    c = court_of(graph, p)
    if ecas(graph):
        return (f"Upload it in the ECAS Case Portal ({ECAS_RULE}, EOIR's ECAS page). Attorneys must e-file in ECAS in every case eligible for it. "
                "EOIR serves ICE electronically, so no paper service (8 CFR 1003.32(a)). A case whose record is still on paper is not eligible: file it on paper.")
    where = (f"{c['name']}, {', '.join(c['address'])}" + (f" (window filing {c['hours']}" + (f"; {c['phone']}" if c.get("phone") else "") + ")" if c.get("hours") else "")
             if c else "the immigration court (choose it above)")
    served = ("A joint motion, agreed upon by all parties, needs no proof of service (Practice Manual 3.2(a), (e), 2020 version): none is in the packet."
              if joint else "A copy is served on ICE (OPLA) with the signed proof of service (8 CFR 1003.32(c)).")
    return (f"On paper{', in duplicate (8 CFR 1003.23(b)(1)(ii))' if duplicate else ''}, to {where}. The court counts it filed when it receives it, "
            f"not when it is mailed (Practice Manual 3.1(a)(iii), 2020 version). {served} The court takes no faxes or e-mail unless it asked for them "
            "(the court's page).")


@producer(OFFICE)
def upload_problems(client_dir: Path, graph, packet_pdf: str) -> list[str]:
    """ECAS takes documents of 25 MB or less (EOIR's ECAS page): the last built packet, when it is filed there."""
    path = Path(client_dir) / packet_pdf
    if not ecas(graph) or not path.exists():
        return []
    size = path.stat().st_size / 1_000_000
    return [f"The packet is {size:.0f} MB: ECAS takes documents of {ECAS_LIMIT_MB} MB or less. Scan the evidence lighter (black and white, 300 DPI) "
            "or upload the exhibits as separate documents."] if size > ECAS_LIMIT_MB else []


# -- the paper ------------------------------------------------------------------------------------------------------


def _a(value_: Any) -> str:
    digits = re.sub(r"\D", "", str(value_ or ""))
    if not digits:
        return "[A-Number]"
    digits = digits.zfill(9)
    return f"A {digits[:3]} {digits[3:6]} {digits[6:]}"


def respondent(graph) -> str:
    v = lambda k: value(graph, k)  # noqa: E731
    return " ".join(x for x in (v("applicant.given_name"), v("applicant.middle_name"), v("applicant.family_name")) if x).upper() or "[RESPONDENT]"


def attorney_lines(graph) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    from offices import _phone

    name = " ".join(x for x in (v("firm.preparer_given_name"), v("firm.preparer_family_name")) if x)
    place = ", ".join(x for x in (v("firm.city"), " ".join(y for y in (v("firm.state"), v("firm.zip")) if y)) if x)
    email = str(v("firm.email") or "")
    return [x for x in (name.title() if name.isupper() else name or "[attorney]", v("firm.business_name"), v("firm.street"), place or None,
                        f"Tel. {_phone(v('firm.phone'))}" if v("firm.phone") else None, f"Email: {email.lower() if email.isupper() else email}" if email else None,
                        f"EOIR ID: {v('firm.eoir_id')}" if v("firm.eoir_id") else "EOIR ID: [not set]") if x]


class Paper:
    """One PDF on court paper: 8.5 x 11, Times 12 (Practice Manual 3.3(c)(v), (vii)), DRAFT across every page of a draft."""

    def __init__(self, path: Path, title: str, draft: bool):
        from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import inch

        self.path, self.title, self.draft, self.inch = path, title, draft, inch
        self.body = ParagraphStyle("body", fontName="Times-Roman", fontSize=12, leading=22, alignment=TA_JUSTIFY, firstLineIndent=0.5 * inch, spaceAfter=4)
        self.plain = ParagraphStyle("plain", fontName="Times-Roman", fontSize=12, leading=15)
        self.quote = ParagraphStyle("quote", parent=self.plain, leftIndent=0.6 * inch, rightIndent=0.6 * inch, alignment=TA_JUSTIFY, spaceAfter=8)
        self.item = ParagraphStyle("item", parent=self.plain, leftIndent=0.5 * inch, firstLineIndent=-0.3 * inch, spaceAfter=6, alignment=TA_JUSTIFY)
        self.center = ParagraphStyle("center", parent=self.plain, alignment=TA_CENTER)
        self.heading = ParagraphStyle("heading", parent=self.plain, fontName="Times-Bold", spaceBefore=8, spaceAfter=4, keepWithNext=1)
        self.story: list = []

    def p(self, text: str, style=None) -> None:
        from reportlab.platypus import Paragraph

        self.story.append(Paragraph(text, style or self.body))

    def gap(self, n: float = 12) -> None:
        from reportlab.platypus import Spacer

        self.story.append(Spacer(1, n))

    def write(self) -> None:
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate

        width, height = letter
        draft = self.draft

        def mark(canvas, doc):
            if not draft:
                return
            canvas.saveState()
            canvas.setFillColorRGB(0.82, 0.82, 0.82)
            canvas.setFont("Helvetica-Bold", 54)
            canvas.translate(width / 2, height / 2)
            canvas.rotate(40)
            canvas.drawCentredString(0, 0, "DRAFT - NOT FOR FILING")
            canvas.restoreState()

        out = io.BytesIO()
        doc = SimpleDocTemplate(out, pagesize=letter, title=self.title, leftMargin=self.inch, rightMargin=self.inch, topMargin=self.inch, bottomMargin=self.inch)
        doc.build(self.story, onFirstPage=mark, onLaterPages=mark)
        self.path.write_bytes(out.getvalue())


def esc(text: Any) -> str:
    from xml.sax.saxutils import escape

    return escape(str(text)).replace("\n", "<br/>")


def court_heading(paper: Paper, court: dict[str, Any] | None) -> None:
    for line in ("UNITED STATES DEPARTMENT OF JUSTICE", "EXECUTIVE OFFICE FOR IMMIGRATION REVIEW", "IMMIGRATION COURT",
                 (court["city"] if court else "[THE COURT'S CITY AND STATE]").upper()):
        paper.p(f"<b>{esc(line)}</b>", paper.center)


def caption(paper: Paper, graph, proceeding: str) -> None:
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Table, TableStyle

    left = Paragraph(f"In the Matter of:<br/><br/><b>{esc(respondent(graph))}</b>,<br/><br/>Respondent.", paper.plain)
    right = Paragraph(f"File No.: {esc(_a(value(graph, 'applicant.a_number')))}<br/><br/>In {esc(proceeding)} proceedings", paper.plain)
    bar = Paragraph(")<br/>)<br/>)<br/>)<br/>)<br/>)", paper.plain)
    t = Table([[left, bar, right]], colWidths=[3.1 * inch, 0.3 * inch, 3.1 * inch])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    paper.story.append(t)


def detained(graph, p: str) -> bool:
    """The client is detained: the case page's latest hearing says so (or the attorney's answer), or the court is a detained court (Krome)."""
    c = court_of(graph, p)
    return value(graph, f"{p}.detained") == "Yes" or bool(c and c.get("detained"))


def cover(path: Path, graph, p: str, title: str, proceeding: str, flags: list[str], draft: bool) -> None:
    """The cover page (Practice Manual 3.3(c)(vi), Appendix F, 2020 version). flags: the special circumstances shown top right,
    "DETAINED", "JOINT MOTION" ("should appear prominently on the cover page, preferably in the top right corner and highlighted")."""
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Table, TableStyle

    paper = Paper(path, title, draft)
    who = Paragraph("<br/>".join(esc(x) for x in attorney_lines(graph)), paper.plain)
    flag = ""
    if flags:
        flag = Table([[Paragraph(f"<b>{esc(f)}</b>", paper.center)] for f in flags], colWidths=[1.5 * inch])
        flag.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1.2, "black"), ("BACKGROUND", (0, 0), (-1, -1), "#fff3a0")]))
    top = Table([[who, flag]], colWidths=[4.6 * inch, 1.9 * inch])
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    paper.story.append(top)
    paper.gap(36)
    court_heading(paper, court_of(graph, p))
    paper.gap(24)
    caption(paper, graph, proceeding)
    paper.gap(18)
    judge = value(graph, f"{p}.judge")
    when = value(graph, f"{p}.next_hearing")
    from filing_questions import iso

    hearing = f"{us(iso(when))}{' at ' + str(value(graph, f'{p}.next_hearing_time')) if value(graph, f'{p}.next_hearing_time') else ''}" if iso(when) else "None scheduled"
    paper.p(f"Immigration Judge: {esc(judge or '[as assigned]')}&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Next Hearing: {esc(hearing)}", paper.plain)
    paper.gap(150)
    paper.p(f"<b>{esc(title.upper())}</b>", paper.center)
    paper.write()


def signature(paper: Paper, graph, today: date) -> None:
    from reportlab.platypus import KeepTogether, Paragraph, Spacer

    from fill.cover_letter import long_date

    lines = attorney_lines(graph)
    block = [Paragraph("Respectfully submitted,", paper.plain), Spacer(1, 36), Paragraph("______________________________", paper.plain),
             Paragraph(esc(lines[0]), paper.plain), Paragraph("Attorney for the Respondent", paper.plain),
             Paragraph("<br/>".join(esc(x) for x in lines[1:]), paper.plain), Spacer(1, 8), Paragraph(f"Dated: {esc(long_date(today))}", paper.plain)]
    paper.gap(16)
    paper.story.append(KeepTogether(block))


def proposed_order(path: Path, graph, p: str, what: str, outcomes: list[str], extra: list[str], draft: bool, noun: str = "motion") -> None:
    """The proposed order for the judge's signature (Practice Manual 5.2(b), Appendix Q, 2020 version).
    what: "the respondent's Motion to Reopen"; outcomes: the boxes under "because:"; extra: the boxes for what follows;
    noun: what is granted or denied ("motion", or "request" for a bond request)."""
    paper = Paper(path, "Proposed order", draft)
    court_heading(paper, court_of(graph, p))
    paper.gap(18)
    paper.p(f"In the Matter of: <b>{esc(respondent(graph))}</b>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;A Number: {esc(_a(value(graph, 'applicant.a_number')))}", paper.plain)
    paper.gap(18)
    paper.p("<b>ORDER OF THE IMMIGRATION JUDGE</b>", paper.center)
    paper.gap(14)
    paper.p(f"Upon consideration of {esc(what)}, it is HEREBY ORDERED that the {esc(noun)} be "
            "&nbsp;[&nbsp;&nbsp;] GRANTED&nbsp;&nbsp;&nbsp;[&nbsp;&nbsp;] DENIED&nbsp;&nbsp;because:", paper.plain)
    paper.gap(8)
    for line in outcomes:
        paper.p(f"[&nbsp;&nbsp;]&nbsp;&nbsp;{esc(line)}", paper.item)
    if extra:
        paper.gap(6)
        for line in extra:
            paper.p(f"[&nbsp;&nbsp;]&nbsp;&nbsp;{esc(line)}", paper.item)
    paper.gap(40)
    paper.p("______________________&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;______________________________", paper.plain)
    paper.p("Date&nbsp;" + "&nbsp;" * 66 + "Immigration Judge", paper.plain)
    paper.gap(36)
    paper.p("<b>Certificate of Service</b>", paper.plain)
    paper.p("This document was served by: [&nbsp;&nbsp;] Mail&nbsp;&nbsp;[&nbsp;&nbsp;] Personal Service<br/>"
            "To: [&nbsp;&nbsp;] Alien&nbsp;&nbsp;[&nbsp;&nbsp;] Alien c/o Custodial Officer&nbsp;&nbsp;[&nbsp;&nbsp;] Alien's Atty/Rep&nbsp;&nbsp;[&nbsp;&nbsp;] DHS<br/>"
            "Date: ____________&nbsp;&nbsp;&nbsp;&nbsp;By: Court Staff ____________", paper.plain)
    paper.write()


def proof_of_service(path: Path, graph, documents: str, draft: bool) -> None:
    """The proof of service (8 CFR 1003.32; Practice Manual 3.2(e), Appendix G, 2020 version): on paper, the party and the address
    typed; the date, the means and the signature by the person who serves it. In ECAS with ICE taking part: no service needed."""
    paper = Paper(path, "Proof of service", draft)
    paper.p(f"<b>{esc(respondent(graph))}</b>", paper.plain)
    paper.p(esc(_a(value(graph, "applicant.a_number"))), paper.plain)
    paper.gap(24)
    paper.p("<b>PROOF OF SERVICE</b>", paper.center)
    paper.gap(18)
    if ecas(graph):
        paper.p(f"No service needed: I electronically filed {esc(documents)} through the EOIR Courts &amp; Appeals System (ECAS), and the opposing party, "
                "the Department of Homeland Security, is participating in ECAS. 8 C.F.R. &sect; 1003.32(a).", paper.body)
        paper.gap(40)
        paper.p("______________________________&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;______________", paper.plain)
        paper.p(f"{esc(attorney_lines(graph)[0])}" + "&nbsp;" * 40 + "Date", paper.plain)
    else:
        dhs = value(graph, "eoir.dhs_address") or "[the ICE (OPLA) office's full address]"
        paper.p(f"On ______________ (date), I, ______________________________ (printed name of the person signing below), served a copy of "
                f"{esc(documents)} and any attached pages on the Department of Homeland Security, {esc(OPLA)}, at the following address: "
                f"{esc(dhs)}, by ______________________________ (method of service: for example, overnight courier, hand delivery, first-class mail).",
                paper.body)
        paper.gap(40)
        paper.p("______________________________&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;______________", paper.plain)
        paper.p("(signature)" + "&nbsp;" * 52 + "(date)", paper.plain)
    paper.write()
