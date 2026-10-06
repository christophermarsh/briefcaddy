"""Asking the certifying agency for the U visa certification, Form I-918
Supplement B (edition 01/20/25): the step before the U petition
(src/u_visa.py). Its only sources are the Supplement B Instructions
(01/20/25) and 8 CFR 214.14 (eCFR as of 09/30/2026):

  Who certifies (214.14(a)(2), (3)): a Federal, State or local law
    enforcement agency, prosecutor, judge or other authority responsible for
    investigating or prosecuting the crime; signed by its head, a supervisor
    the head specifically designated for U certifications, or a judge.
  Discretion: "The decision whether to complete Supplement B is at the
    discretion of the certifying agency" (Instructions, page 1).
  What the firm fills: Part 1, the victim (Instructions, Part 1). Parts 2-6
    are the certifying official's.
  The original: "USCIS will not accept a photocopy of the signature page"
    (Instructions, General Instructions 4); "valid for six months from the
    date of signature" (Instructions, page 2; 8 CFR 214.14(c)(2)(i)).

What this builds: the firm's letter to the agency (u_cert_letter.pdf) and
the Supplement B with Part 1 filled. The agency's address is the attorney's
answer, with where it was read: never a guessed address, and no agency is
named by the system. Mailing it ("Record the mailing") records the request;
the date the signed form comes back is an answer here and on the petition.
The letter's wording is DRAFT for the attorney.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from filing_questions import DATE, TEXT, putter, state_of, us, value
from filing_questions import iso as _d
from holders import CLIENT, OFFICE, held, producer
from u_visa import CRIMES, SIMILAR, supb_last_day

TITLE = "U visa: certification request (Supplement B)"
SECTIONS = [
    ("The crime", "the attorney", [
        ("uvisa.crime", "The qualifying criminal activity (INA 101(a)(15)(U)(iii))", {"type": "choice", "options": CRIMES + [SIMILAR]}, True),
        ("uvisa.crime_other", "If a similar activity: the offense, as the agency charged or described it", TEXT, False),
        ("uvisa.crime_date", "When it happened (the first date, if more than one)", DATE, True),
        ("uvisa.crime_place", "Where it happened (city and state)", TEXT, True),
        ("uvisa.agency_case_number", "The agency's case or report number (on the police report or court record)", TEXT, False),
    ]),
    ("The certifying agency", "the attorney", [
        ("uvisa.agency_name", "The certifying agency (the police department, prosecutor, court or other authority)", TEXT, True),
        ("uvisa.agency_attention", "To the attention of (the unit or official the agency's own instructions name), if any", TEXT, False),
        ("uvisa.agency_street", "The agency's address for certification requests: street", TEXT, True),
        ("uvisa.agency_city", "The agency's city", TEXT, True),
        ("uvisa.agency_state", "The agency's state", TEXT, True),
        ("uvisa.agency_zip", "The agency's ZIP code", TEXT, True),
        ("uvisa.agency_source", "Where that address comes from (the agency's web page or letter, and the date it was read)", TEXT, True),
    ]),
    ("Tracking", "the paralegal", [
        ("uvisa.cert_requested", "The date the request was sent (recorded with the mailing)", DATE, False),
        ("uvisa.supb_signed", "The date the certifying official signed the Supplement B (its Part 6, item 2)", DATE, False),
        ("uvisa.supb_received", "The date the signed Supplement B reached the office", DATE, False),
    ]),
]
LETTER = "u_cert_letter.pdf"


def from_record(status: dict[str, Any], graph) -> None:
    """The request's mailing, recorded on the case (src/prefile.py): the date it was sent."""
    sent = [r for r in status.get("filings") or [] if r.get("filing") == "u_cert" and r.get("mailed_on")]
    if sent:
        putter(graph, "u_certification.record")("uvisa.cert_requested", sent[-1]["mailed_on"], f"the request's mailing, recorded by {sent[-1].get('by') or 'the office'}")


def derive(graph, today: date):
    return graph


def notes(graph, today: date) -> list[dict[str, str]]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = [{"level": "info", "title": "What goes to the agency", "text": "The office's letter and Form I-918 Supplement B with Part 1 (the client) filled in. "
            "Parts 2 to 6 are the certifying official's: the agency's head, a supervisor the head designated, or a judge (8 CFR 214.14(a)(3)). "
            "Signing it is the agency's choice (Supplement B Instructions)."},
           {"level": "info", "title": "The agency's address", "text": "From the agency's own instructions for U visa certification requests (some have a unit, "
            "a form or an online portal of their own). Write where it was read; never a guessed address."}]
    signed, requested = _d(v("uvisa.supb_signed")), _d(v("uvisa.cert_requested"))
    if signed:
        last = supb_last_day(signed)
        out.append({"level": "warn" if last < today else "info", "title": "Signed", "text": f"Signed {us(signed)}: the U petition must reach USCIS by {us(last)}, "
                    "six months from the signature (8 CFR 214.14(c)(2)(i)). Build it: More…, U visa petition (I-918)."
                    + (" It has expired: ask for a new one." if last < today else "")})
    elif requested:
        out.append({"level": "info", "title": "Requested", "text": f"Sent {us(requested)}. When the signed form comes back, record the date it was signed: "
                    "the six months count from then."})
    return out


@producer(CLIENT)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    if v("uvisa.crime") == SIMILAR and not v("uvisa.crime_other"):
        out.append("A similar activity: name the offense as the agency charged or described it (INA 101(a)(15)(U)(iii)).")
    signed = _d(v("uvisa.supb_signed"))
    if signed and signed > today:
        out.append(held(OFFICE, f"The Supplement B's signature date ({us(signed)}) is after today: check its Part 6, item 2."))
    return out


def render(client_dir: Path, graph, today: date) -> None:
    """u_cert_letter.pdf: the firm's letter to the certifying agency, on the case's office letterhead (src/offices.py)."""
    from xml.sax.saxutils import escape

    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Spacer

    import offices
    from fill.cover_letter import _letter_doc, _signature_block, full_name, load_config, long_date

    v = lambda k: value(graph, k)  # noqa: E731
    config = offices.letter(load_config(), client_dir, state_of(graph) or None)
    draft = bool(problems(client_dir, graph, today)) or not all(v(k) for k in ("uvisa.agency_name", "uvisa.agency_street", "uvisa.agency_city",
                                                                               "uvisa.agency_state", "uvisa.agency_zip", "uvisa.crime", "uvisa.crime_date", "uvisa.crime_place"))
    out, doc, styles = _letter_doc(config, draft, "Request for U nonimmigrant status certification")
    body, left = styles["body"], styles["left"]
    para = ParagraphStyle("para", parent=body, firstLineIndent=0.4 * inch)
    name = full_name({"given_name": v("applicant.given_name"), "middle_name": v("applicant.middle_name"), "family_name": v("applicant.family_name")}) or "[the client]"
    crime = (v("uvisa.crime_other") if v("uvisa.crime") == SIMILAR else v("uvisa.crime")) or "[the criminal activity]"
    when = us(_d(v("uvisa.crime_date"))) if _d(v("uvisa.crime_date")) else "[date]"
    case = v("uvisa.agency_case_number")
    to = [x for x in (v("uvisa.agency_name"), f"Attn: {v('uvisa.agency_attention')}" if v("uvisa.agency_attention") else None, v("uvisa.agency_street"),
                      " ".join(x for x in (f"{v('uvisa.agency_city')}," if v("uvisa.agency_city") else None, v("uvisa.agency_state"), v("uvisa.agency_zip")) if x))
          if x] or ["[the certifying agency's address]"]
    gap = lambda n=10: Spacer(1, n)  # noqa: E731
    story = [Paragraph(long_date(today), left), gap(14), Paragraph(f"<u><b>{escape(config['delivery'])}</b></u>", left), gap(12),
             Paragraph("<br/>".join(escape(x) for x in to), left), gap(14),
             Paragraph("<b>RE: Request for Form I-918, Supplement B, U Nonimmigrant Status Certification</b>", left),
             Paragraph(f"<b>Victim: {escape(name)}{', born ' + escape(us(_d(v('applicant.dob')))) if _d(v('applicant.dob')) else ''}</b>", left)]
    if case:
        story.append(Paragraph(f"<b>Your case or report number: {escape(case)}</b>", left))
    story += [gap(12), Paragraph("Dear Certifying Official:", left), gap(10),
              Paragraph(f"This office represents {escape(name)}, the victim of {escape(crime.lower())} on {escape(when)} in "
                        f"{escape(v('uvisa.crime_place') or '[place]')}, a crime your agency investigated or prosecuted"
                        f"{' under the case number above' if case else ''}. We respectfully ask that a certifying official of your agency complete "
                        f"and sign the enclosed Form I-918, Supplement B, U Nonimmigrant Status Certification, so that {escape(name)} may petition U.S. "
                        "Citizenship and Immigration Services for U nonimmigrant status (Immigration and Nationality Act section 101(a)(15)(U); "
                        "8 CFR 214.14).", para), gap(8),
              Paragraph("Part 1, the victim's information, is completed. Parts 2 through 6 are for your agency: its information, the criminal acts, "
                        "the victim's helpfulness, any family members involved, and the certification. Under 8 CFR 214.14(a)(3), the certifying official is "
                        "the head of the agency, a person in a supervisory role whom the head has specifically designated to issue U nonimmigrant status "
                        "certifications, or a Federal, State, or local judge.", para), gap(8),
              Paragraph("USCIS requires the original Supplement B with the official's handwritten signature and does not accept a photocopy of the "
                        "signature page. A signed Supplement B is valid for six months from the date of signature (Supplement B Instructions). We ask "
                        "that the signed original be returned to this office at the address above.", para), gap(8),
              Paragraph("Enclosed: Form I-918, Supplement B (edition 01/20/25), with Part 1 completed.", left)]
    story += _signature_block(config, styles)
    doc.build(story)
    (client_dir / LETTER).write_bytes(out.getvalue())
