"""A client's new address -- the law's deadlines, and what each office needs:

  USCIS: within 10 days (8 CFR 265.1). The change of address tool in the
    client's USCIS online account is what USCIS recommends (it updates every
    case); the paper Form AR-11 goes to the address printed on it. Each
    pending USCIS case needs the new address too.
  The immigration court, when the client has a case there: within 5 days
    (8 CFR 1003.15(d)(2); EOIR's page says five working days), Form EOIR-33,
    in the EOIR Case Portal for an ECAS case, served on DHS (ICE).

The move itself is recorded on the case page (src/journey.py, "moved"),
which puts both deadlines on the timeline. This builds the AR-11 and a sheet
with the EOIR-33's answers and the list of pending cases.
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from pathlib import Path

from filing_questions import DATE, TEXT, YES_NO, putter, us, value
from filing_questions import iso as _d
from holders import CLIENT, OFFICE, held, producer

TITLE = "Change of address (AR-11, EOIR-33)"
AR11_MAIL_TO = ["U.S. DEPARTMENT OF HOMELAND SECURITY", "CITIZENSHIP AND IMMIGRATION SERVICES", "ATTN: CHANGE OF ADDRESS", "1344 PLEASANTS DRIVE",
                "HARRISONBURG, VA 22801"]  # printed on Form AR-11 (edition 11/02/22), page 2
SECTIONS = [
    ("The move", "the paralegal", [
        ("address.moved_on", "Date the client moved", DATE, True),
        ("applicant.physical_street", "The new home address: street (update it in the case too)", TEXT, True),
        ("applicant.physical_city", "The new home address: city", TEXT, True),
        ("applicant.physical_state", "The new home address: state (2 letters)", TEXT, True),
        ("applicant.physical_zip", "The new home address: ZIP code", TEXT, True),
        ("address.previous_street", "The previous home address: street", TEXT, False),
        ("address.previous_city", "The previous home address: city", TEXT, False),
        ("address.previous_state", "The previous home address: state", TEXT, False),
        ("address.previous_zip", "The previous home address: ZIP code", TEXT, False),
        ("address.online", "Reported in the client's USCIS online account instead of the paper AR-11?", YES_NO, False),
    ]),
    ("The immigration court (EOIR-33)", "the attorney", [
        ("address.court", "The immigration court that has the case", TEXT, False),
        ("address.dhs_office", "The ICE (OPLA) office served", TEXT, False),
    ], lambda g: _in_court(g)),
]
MORE_QUESTIONS = "The EOIR-33's questions appear for a client with a court case (a Notice to Appear in the case)."


def _in_court(graph) -> bool:
    return bool(value(graph, "applicant.nta_present"))


def from_record(status: dict, graph) -> None:
    """The latest move recorded on the case page (src/journey.py, "moved"): its date, and the new address as typed there."""
    moves = (status.get("journey") or {}).get("moves") or []
    if moves:
        put = putter(graph, "case page")
        put("address.moved_on", moves[-1]["date"], f"recorded on the case page by {moves[-1].get('by')}")
        put("address.recorded_as", moves[-1].get("address"), f"typed on the case page by {moves[-1].get('by')}")


def _before(graph, key: str) -> str | None:
    """The value the case held before a person answered it (the reader's or the questionnaire's), if the answer changed it."""
    fact = graph.get(key)
    if fact is None or getattr(fact, "review", None) is None:
        return None
    original = next((s.normalized_value for s in fact.sources if s.doc_type != "paralegal_review" and s.normalized_value not in (None, "")), None)
    return original if original is not None and str(original) != str(fact.value) else None


def confirmed(graph) -> bool:
    """A person entered the new home address since the move: the street was answered, and matches what was typed on the case page."""
    fact = graph.get("applicant.physical_street")
    if fact is None or getattr(fact, "review", None) is None or not value(graph, "applicant.physical_street"):
        return False
    typed = str(value(graph, "address.recorded_as") or "").upper()
    return not typed or str(value(graph, "applicant.physical_street")).upper() in typed


def derive(graph, today: date):
    put = putter(graph, "address_change.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    moved = bool(v("address.moved_on"))
    for part in ("street", "unit_type", "apt", "city", "state", "zip"):  # the address before: the one the client just left
        key = f"applicant.physical_{part}"
        if moved and confirmed(graph):
            put(f"address.previous_{part}", _before(graph, key), "the home address the case held before the new one was entered")
        elif moved:
            put(f"address.previous_{part}", v(key), "the home address the case still holds (the one the client left)")
        put(f"address.previous_{part}", v(f"applicant.prior_address_{part}"), "the client's previous address in the case")
    if v("applicant.mailing_same_as_physical") == "No":
        for part in ("street", "unit_type", "apt", "city", "state", "zip"):
            put(f"address.mailing_{part}", v(f"applicant.mailing_{part}"), "the client's mailing address")
    return graph


def notes(graph, today: date) -> list[dict[str, str]]:
    moved = _d(value(graph, "address.moved_on"))
    out = []
    if value(graph, "address.recorded_as"):
        out.append({"level": "info" if confirmed(graph) else "warn", "title": "The new address",
                    "text": f"Typed on the case page: \"{value(graph, 'address.recorded_as')}\"."
                            + ("" if confirmed(graph) else " Enter it line by line in the questions below: that updates the case, and the AR-11 takes it from there.")})
    if moved:
        out.append({"level": "warn" if today > moved + timedelta(days=10) else "info", "title": "USCIS",
                    "text": f"Due {us(moved + timedelta(days=10))} (10 days, 8 CFR 265.1): the change of address tool in the client's USCIS online account "
                            "(USCIS's recommendation: it updates every case), or the paper AR-11 to " + " / ".join(AR11_MAIL_TO) + "."})
        if _in_court(graph):
            out.append({"level": "warn", "title": "The immigration court",
                        "text": f"Due {us(moved + timedelta(days=5))} (5 days, 8 CFR 1003.15(d)(2)): Form EOIR-33 in the EOIR Case Portal (ECAS), served on DHS. "
                                "A notice sent to the old address can lead to an order made without the client."})
    else:
        out.append({"level": "info", "title": "The move", "text": "Enter the date the client moved: the deadlines count from it (10 days for USCIS, 5 for a court)."})
    return out


@producer(CLIENT)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    out = []
    if value(graph, "address.moved_on") and not confirmed(graph):
        out.append(f"The new home address isn't entered yet: the case still has {value(graph, 'applicant.physical_street') or 'no street'}, "
                   f"{value(graph, 'applicant.physical_city') or ''}: the address before the move. Answer the new address's questions above.")
    if _in_court(graph) and not value(graph, "address.court"):
        out.append(held(OFFICE, "The immigration court that has the case (for the EOIR-33)."))
    return out


def render(client_dir: Path, graph, today: date) -> None:
    """address_change_sheet.pdf: the new and old addresses, the EOIR-33's answers, and every pending USCIS case to update."""
    import journey
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter as page
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    v = lambda k: value(graph, k)  # noqa: E731
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=10, leading=13)
    head = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=13, leading=16)
    sub = ParagraphStyle("s", fontName="Helvetica", fontSize=8.5, leading=11, textColor=colors.HexColor("#555555"))

    def addr(prefix: str) -> str:
        parts = [v(f"{prefix}street"), " ".join(x for x in (v(f"{prefix}unit_type"), v(f"{prefix}apt")) if x), v(f"{prefix}city"),
                 " ".join(x for x in (v(f"{prefix}state"), v(f"{prefix}zip")) if x)]
        return ", ".join(x for x in parts if x) or "[not in the case]"

    name = " ".join(x for x in (v("applicant.given_name"), v("applicant.middle_name"), v("applicant.family_name")) if x) or "[client]"
    moved = _d(v("address.moved_on"))
    rows = [["Client", name], ["A-Number", v("applicant.a_number") or "[none]"], ["Date of birth", us(_d(v("applicant.dob")))],
            ["Moved on", us(moved) if moved else "[enter the date]"], ["New home address", addr("applicant.physical_")],
            ["Previous home address", addr("address.previous_")], ["Phone", v("applicant.daytime_phone") or "[none]"]]
    pend = [n for n in journey.notices(graph) if n["kind"] == "receipt"
            and not any(m["receipt"] == n["receipt"] and m["kind"] in journey.CLOSED for m in journey.notices(graph))]
    story = [Paragraph(f"Change of address: {escape(name)}", head),
             Paragraph(escape(f"Prepared {us(today)}. DRAFT for the attorney's review."), sub), Spacer(1, 8)]
    table = Table([[Paragraph(f"<b>{escape(a)}</b>", body), Paragraph(escape(str(b)), body)] for a, b in rows], colWidths=[1.8 * inch, 5.0 * inch])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [table, Spacer(1, 12), Paragraph("USCIS (within 10 days, 8 CFR 265.1)", head),
              Paragraph(escape("The change of address tool in the client's USCIS online account (it updates every case), or the enclosed paper AR-11 mailed to "
                               + ", ".join(AR11_MAIL_TO).title() + ". Then the new address on each pending case:"), body), Spacer(1, 4)]
    story.append(Paragraph("<br/>".join(escape(f"{n['form'] or 'USCIS'}: receipt {n['receipt']} (received {us(_d(n['date']))})") for n in pend)
                           or "No pending USCIS case in the folder.", body))
    if _in_court(graph):
        story += [Spacer(1, 12), Paragraph("The immigration court (within 5 days, 8 CFR 1003.15(d)(2))", head),
                  Paragraph(escape("Form EOIR-33 in the EOIR Case Portal (ECAS) (the answers above) for the court: "
                                   + (v("address.court") or "[the court]") + "; served on DHS: " + (v("address.dhs_office") or "[the ICE (OPLA) office]") + "."), body)]
    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=page, leftMargin=0.7 * inch, rightMargin=0.7 * inch, topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                      title="Change of address").build(story)
    (client_dir / "address_change_sheet.pdf").write_bytes(out.getvalue())
