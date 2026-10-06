"""An immigrant visa through the National Visa Center and a U.S. consulate --
for a family-based client abroad, after USCIS approves the I-130.

There is no paper form to fill: the DS-260 and the document upload are
online only (the NVC's Consular Electronic Application Center). What the
firm needs instead, and what this builds (visa_answer_sheet.pdf):

  - the DS-260 answer sheet: each answer the case already holds, where it
    came from, and what the client still has to tell us -- so the client
    (or the paralegal, sitting with the client) types the online form from
    one checked page;
  - the document checklist: the documents 22 CFR 42.65 requires (police
    certificates, birth, prison, military and relationship records), the
    sponsor's financial evidence (Form I-864), each marked found in the
    folder or missing;
  - tracking: the NVC case number and every date (welcome letter, fees,
    DS-260, documents, documentarily qualified, interview, visa, entry),
    which put the case's steps on its timeline (src/journey.py) and the
    interview in the client's portal.

The fees are on the NVC invoice in the client's account: this never states
an amount (travel.state.gov blocks automated reading, so nothing here can be
checked against it automatically).
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from typing import Any

from filing_questions import DATE, LINES, TEXT, YES_NO, has_doc, us, value
from filing_questions import iso as _d
from holders import ATTORNEY, CLIENT, held, producer

TITLE = "Immigrant visa (NVC and the consulate)"
RESULTS = ["Issued", "Refused under 221(g): more documents asked for", "Refused", "Administrative processing"]
SECTIONS = [
    ("Tracking", "the paralegal", [
        ("visa.nvc_case_number", "NVC case number (on the welcome letter, e.g. RIO2026123456)", TEXT, True),
        ("visa.invoice_id", "Invoice ID number (on the welcome letter)", TEXT, False),
        ("visa.welcome_letter_date", "Date of the NVC welcome letter", DATE, False),
        ("visa.fees_paid_date", "Date the NVC fees were paid", DATE, False),
        ("visa.ds260_submitted", "Date the DS-260 was submitted", DATE, False),
        ("visa.ds260_confirmation", "DS-260 confirmation number", TEXT, False),
        ("visa.documents_submitted", "Date the civil documents and financial evidence were submitted", DATE, False),
        ("visa.dq_date", "Date documentarily qualified (NVC's notice)", DATE, False),
        ("visa.consulate", "Embassy or consulate of the interview (e.g. Rio de Janeiro)", TEXT, False),
        ("visa.interview_date", "Interview date", DATE, False),
        ("visa.interview_time", "Interview time (as on the letter)", TEXT, False),
        ("visa.medical_date", "Medical exam with the panel physician (date)", DATE, False),
        ("visa.result", "Result of the interview", {"type": "choice", "options": RESULTS}, False),
        ("visa.issued_date", "Date the visa was issued", DATE, False),
        ("visa.entry_date", "Date the client entered the U.S. on the visa", DATE, False),
    ]),
    ("For the DS-260", "the client", [
        ("visa.residences", "Every country the client has lived in for 6 months or more, with the dates (police certificates follow from this)", LINES, True),
        ("visa.other_nationalities", "Any other nationality, now or before", TEXT, False),
        ("visa.national_id", "National identity number (e.g. Brazil's CPF)", TEXT, False),
        ("visa.passport_issued", "Passport issue date", DATE, False),
        ("visa.education", "Schools attended after primary school (name, city, dates)", LINES, False),
        ("visa.military", "Ever served in any military?", YES_NO, True),
        ("visa.arrested", "Ever arrested, charged or convicted anywhere?", YES_NO, True),
        ("visa.prior_us_visits", "Earlier trips to the U.S. (dates, length, visa type)", LINES, False),
        ("visa.prior_refusal", "Ever refused a U.S. visa, or entry at the border?", YES_NO, True),
        ("visa.social_media", "Social media accounts used in the last 5 years (platform and name)", LINES, False),
    ]),
]
SHEET = [  # (DS-260 section, question, fact key)
    ("Personal 1", "Surnames", "applicant.family_name"), ("Personal 1", "Given names", "applicant.given_name"),
    ("Personal 1", "Middle name", "applicant.middle_name"), ("Personal 1", "Sex", "applicant.sex"),
    ("Personal 1", "Marital status", "applicant.marital_status"), ("Personal 1", "Date of birth", "applicant.dob"),
    ("Personal 1", "City of birth", "applicant.birth_city"), ("Personal 1", "State/province of birth", "applicant.birth_state"),
    ("Personal 1", "Country of birth", "applicant.country_of_birth"),
    ("Personal 2", "Nationality", "applicant.citizenship"), ("Personal 2", "Other nationalities", "visa.other_nationalities"),
    ("Personal 2", "National identification number", "visa.national_id"), ("Personal 2", "U.S. Social Security number", "applicant.ssn"),
    ("Address and phone", "Street", "applicant.physical_street"), ("Address and phone", "City", "applicant.physical_city"),
    ("Address and phone", "State/province", "applicant.physical_state"), ("Address and phone", "Postal code", "applicant.physical_zip"),
    ("Address and phone", "Primary phone", "applicant.daytime_phone"), ("Address and phone", "Mobile phone", "applicant.mobile_phone"),
    ("Address and phone", "Email", "applicant.email"), ("Address and phone", "Social media (last 5 years)", "visa.social_media"),
    ("Passport", "Passport number", "applicant.travel_document_number"), ("Passport", "Issuing country", "applicant.travel_document_country"),
    ("Passport", "Issue date", "visa.passport_issued"), ("Passport", "Expiration date", "applicant.travel_document_expiry"),
    ("Family: father", "Surnames", "applicant.father_family_name"), ("Family: father", "Given names", "applicant.father_given_name"),
    ("Family: father", "Date of birth", "applicant.father_dob"), ("Family: father", "Country of residence", "applicant.father_country_of_residence"),
    ("Family: mother", "Surnames", "applicant.mother_family_name"), ("Family: mother", "Given names", "applicant.mother_given_name"),
    ("Family: mother", "Date of birth", "applicant.mother_dob"), ("Family: mother", "Country of residence", "applicant.mother_country_of_residence"),
    ("Family: spouse", "Surnames", "applicant.spouse_family_name"), ("Family: spouse", "Given names", "applicant.spouse_given_name"),
    ("Family: spouse", "Date of birth", "applicant.spouse_dob"), ("Family: spouse", "Date of marriage", "applicant.marriage_date"),
    ("Family: spouse", "City of marriage", "applicant.marriage_city"), ("Family: spouse", "Country of marriage", "applicant.marriage_country"),
    ("Family: prior spouse", "Surnames", "applicant.prior_spouse_family_name"), ("Family: prior spouse", "Given names", "applicant.prior_spouse_given_name"),
    ("Family: prior spouse", "Marriage ended", "applicant.prior_spouse_ended_date"),
    ("U.S. petitioner", "Surnames", "petitioner.family_name"), ("U.S. petitioner", "Given names", "petitioner.given_name"),
    ("U.S. petitioner", "Date of birth", "petitioner.dob"), ("U.S. petitioner", "Status (citizen or resident)", "petitioner.status"),
    ("U.S. petitioner", "Phone", "petitioner.daytime_phone"), ("U.S. petitioner", "Email", "petitioner.email"),
    ("Work and education", "Present employer", "applicant.employer1_name"), ("Work and education", "Occupation", "applicant.employer1_occupation"),
    ("Work and education", "Since", "applicant.employer1_date_from"), ("Work and education", "Earlier employer abroad", "applicant.foreign_employer_name"),
    ("Work and education", "Education", "visa.education"),
    ("Previous U.S. travel", "Earlier trips", "visa.prior_us_visits"), ("Previous U.S. travel", "Last arrival in the U.S.", "applicant.last_arrival_date"),
    ("Previous U.S. travel", "Ever refused a visa or entry", "visa.prior_refusal"),
    ("Residence", "Countries lived in 6+ months", "visa.residences"),
    ("Security and background", "Ever served in a military", "visa.military"), ("Security and background", "Ever arrested, charged or convicted", "visa.arrested"),
]


DOC_NAMES = {"i94": "I-94", "passport": "Passport", "birth_certificate": "Birth certificate", "uscis_notice": "USCIS notice", "i360_approval": "I-360 approval",
             "intake_questionnaire": "Client's questionnaire", "ssn_card": "Social Security card", "review": "A reviewer", "decision": "A reviewer",
             "firm_profile": "Firm", "derived": "Worked out from the case", "marriage_certificate": "Marriage certificate", "green_card": "Green card"}


def derive(graph, today: date):
    return graph  # every answer is the client's or the paralegal's own: nothing to suggest


def checklist(client_dir: Path, graph) -> list[dict[str, Any]]:
    """The documents for NVC (22 CFR 42.65 and Form I-864), each with what the folder holds."""
    v = lambda k: value(graph, k)  # noqa: E731
    married = v("applicant.marital_status") in ("Married", "Divorced", "Widowed") or v("applicant.marriage_date")
    items = [
        ("Passport biographic page", ["passport"], True),
        ("Birth certificate: a certified copy from the civil registry showing the date and place of birth and both parents (22 CFR 42.65(c)(4)), with a certified translation",
         ["birth_certificate"], True),
        ("Police certificate from the country of nationality and the country where the client lives now (each if lived there 6 months or more), and from any other "
         "country lived in for 1 year or more (22 CFR 42.65(c)(1)); the State Department's reciprocity schedule says how each country issues them",
         ["police_certificate"], True),
        ("Court and prison records for any arrest or conviction (22 CFR 42.65(b))", ["criminal_record"], v("visa.arrested") == "Yes"),
        ("Military record of the complete service (22 CFR 42.65(c)(3))", ["military_record"], v("visa.military") == "Yes"),
        ("Marriage certificate, and the divorce or death certificate ending each earlier marriage (42.65(c)(5))", ["marriage_certificate", "divorce_decree"], bool(married)),
        ("Affidavit of Support (Form I-864) signed by the petitioner, with the evidence its instructions ask for (the most recent federal tax return or IRS "
         "transcript; proof of U.S. domicile for a petitioner living abroad)", ["i864", "tax_return"], True),
        ("Medical exam with the consulate's panel physician, before the interview", ["medical_record"], True),
    ]
    out = []
    for text, kinds, needed in items:
        if not needed:
            continue
        found = has_doc(client_dir, *kinds)
        out.append({"text": text, "found": found})
    return out


def notes(graph, today: date) -> list[dict[str, str]]:
    v = lambda k: value(graph, k)  # noqa: E731
    order = [("visa.welcome_letter_date", "NVC welcome letter"), ("visa.fees_paid_date", "fees paid"), ("visa.ds260_submitted", "DS-260 submitted"),
             ("visa.documents_submitted", "documents submitted"), ("visa.dq_date", "documentarily qualified"), ("visa.interview_date", "interview"),
             ("visa.issued_date", "visa issued"), ("visa.entry_date", "entered the U.S.")]
    done = [f"{label} {us(_d(v(key)))}" for key, label in order if _d(v(key))]
    nxt = next((label for key, label in order if not _d(v(key))), None)
    out = [{"level": "info", "title": "Where it stands", "text": ("; ".join(done) + "." if done else "Nothing recorded yet.")
            + (f" Next: {nxt}." if nxt else "")}]
    out.append({"level": "info", "title": "Online only", "text": "The DS-260 and the documents are submitted in the NVC's Consular Electronic Application Center (ceac.state.gov): "
                "this page builds the answer sheet and the checklist to do it from. The fees are on the NVC invoice."})
    if v("visa.result") and v("visa.result").startswith("Refused under 221(g)"):
        out.append({"level": "warn", "title": "221(g)", "text": "The consulate asked for more: send exactly what the refusal sheet lists, the way it says, and record the date."})
    return out


@producer(CLIENT)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    missing = [c["text"].split(" (")[0].split(":")[0] for c in checklist(client_dir, graph) if not c["found"]]
    if missing:
        out.append("Documents for NVC not in the folder yet: " + "; ".join(missing) + ".")
    if v("visa.arrested") == "Yes" or v("visa.prior_refusal") == "Yes":
        out.append(held(ATTORNEY, "An arrest, conviction or earlier refusal: the attorney reviews before the DS-260 is submitted."))
    passport_expiry = _d(v("applicant.travel_document_expiry"))
    interview = _d(v("visa.interview_date"))
    if passport_expiry and (passport_expiry - (interview or today)).days < 183:
        out.append(f"The passport expires {us(passport_expiry)}: check the consulate's interview instructions for how long it must still be valid, and renew it in time.")
    return out


def render(client_dir: Path, graph, today: date) -> None:
    """visa_answer_sheet.pdf: the DS-260 answer sheet, then the document checklist."""
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter as page
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    body = ParagraphStyle("b", fontName="Helvetica", fontSize=9, leading=11)
    head = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=13, leading=16)
    sub = ParagraphStyle("s", fontName="Helvetica", fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
    v = lambda k: value(graph, k)  # noqa: E731
    name = " ".join(x for x in (v("applicant.given_name"), v("applicant.family_name")) if x) or "[client]"
    story = [Paragraph(f"DS-260 answer sheet: {escape(name)}", head),
             Paragraph(escape(f"NVC case {v('visa.nvc_case_number') or '[not recorded]'} · prepared {us(today)} · DRAFT for the attorney's review. "
                              "Check every answer with the client before typing it into the online DS-260; nothing here is submitted by itself."), sub), Spacer(1, 8)]
    rows = [[Paragraph("<b>Section</b>", body), Paragraph("<b>Question</b>", body), Paragraph("<b>Answer</b>", body), Paragraph("<b>From</b>", body)]]
    for section, question, key in SHEET:
        fact = graph.get(key)
        answer = v(key)
        if isinstance(answer, str) and len(answer) == 10 and _d(answer):
            answer = us(_d(answer))
        if key.endswith(".sex"):
            answer = {"F": "Female", "M": "Male"}.get(str(answer), answer)
        kinds = sorted({DOC_NAMES.get(s.doc_type, str(s.doc_type or "").replace("_", " ").capitalize()) for s in (fact.sources if fact is not None else [])})
        source = ", ".join(kinds) if answer else ""
        rows.append([Paragraph(escape(section), body), Paragraph(escape(question), body),
                     Paragraph(escape(str(answer)) if answer else "<font color='#b00020'>ask the client</font>", body), Paragraph(escape(source), sub)])
    table = Table(rows, colWidths=[1.25 * inch, 1.75 * inch, 2.6 * inch, 1.4 * inch], repeatRows=1)
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee"))]))
    story += [table, Spacer(1, 10),
              Paragraph("Security and background: the client answers every question there with the attorney. Any \"Yes\" waits for the attorney. "
                        "The photo is uploaded with the application, to the State Department's specifications (22 CFR 42.65(f)).", body),
              Spacer(1, 16), Paragraph("Documents for NVC", head),
              Paragraph("22 CFR 42.65 (documents required for an immigrant visa) and Form I-864. Upload each in the Consular Electronic Application Center; bring the originals to the interview.", sub),
              Spacer(1, 6)]
    rows = [[Paragraph("<b>Document</b>", body), Paragraph("<b>In the folder</b>", body)]]
    for c in checklist(client_dir, graph):
        rows.append([Paragraph(escape(c["text"]), body),
                     Paragraph(escape(", ".join(c["found"])[:80]) if c["found"] else "<font color='#b00020'>missing</font>", body)])
    table = Table(rows, colWidths=[5.0 * inch, 2.0 * inch], repeatRows=1)
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee"))]))
    story.append(table)
    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=page, leftMargin=0.6 * inch, rightMargin=0.6 * inch, topMargin=0.6 * inch, bottomMargin=0.6 * inch,
                      title="DS-260 answer sheet").build(story)
    (client_dir / "visa_answer_sheet.pdf").write_bytes(out.getvalue())
