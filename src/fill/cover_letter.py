"""Application helper with evidence-bound inputs."""

from __future__ import annotations

import io
import json
import re
from datetime import date
from pathlib import Path
from typing import Any
import schema_path
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

SCHEMA = schema_path.path("cover_letter", "i485")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
PARTICLES = {"da", "de", "do", "das", "dos", "e", "del", "la", "y"}


def load_config(path: Path = SCHEMA) -> dict[str, Any]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if config.get("base"):  # the firm's letterhead, signer and closing, from the I-485 letter's settings
        base = json.loads(schema_path.named(config["base"]).read_text(encoding="utf-8"))
        config = {k: v for k, v in base.items() if k not in ("visa_bulletin", "chargeability", "forms", "documents", "fees", "mail_to")} | config
    if "visa_bulletin" in config:  # the month's EB-4 cut-offs, set on the Settings page (src/settings.py)
        import settings

        config["visa_bulletin"] = settings.overlay("visa_bulletin_eb4", config.get("visa_bulletin") or {})
    return config


LOCKBOXES = schema_path.path("law", "uscis_lockboxes")


@producer(OFFICE, part=1)
def mail_to(config: dict[str, Any], facts: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(the USCIS address lines, problems): a fixed address from the settings,
    or -- "by_state" -- the lockbox for the client's state on USCIS's chart."""
    if config.get("mail_to") != "by_state":
        return list(config["mail_to"]), []
    path = schema_path.named(config["lockbox_chart"]) if config.get("lockbox_chart") else LOCKBOXES  # the N-400 has its own chart
    chart = json.loads(path.read_text(encoding="utf-8"))["lockboxes"]
    state = str(facts.get("physical_state") or facts.get("mailing_state") or "").upper()
    for box in chart.values():
        if state in box["states"]:
            return box["usps"], []
    return chart["Chicago"]["usps"], [f"The client's state ({state or 'unknown'}) isn't on USCIS's lockbox chart for this form: "
                                      "check the filing address on uscis.gov before mailing."]


def case_facts(client_dir: Path) -> dict[str, Any]:
    """The resolved facts the letter needs, from the reviewed case."""
    path = client_dir / "fact_graph_reviewed.json"
    path = path if path.exists() else client_dir / "fact_graph.json"
    facts = json.loads(path.read_text(encoding="utf-8")).get("facts", {}) if path.exists() else {}
    value = lambda k: (facts.get(k) or {}).get("value") if (facts.get(k) or {}).get("status") == "resolved" else None  # noqa: E731
    out = {(k.replace(".", "_") if k.startswith("petitioner.") else k.split(".", 1)[1]): value(k) for k in ("applicant.given_name", "applicant.middle_name", "applicant.family_name", "applicant.a_number",
                                                  "applicant.sex", "applicant.country_of_birth", "applicant.i360_priority_date",
                                                  "applicant.physical_state", "applicant.mailing_state", "petitioner.given_name",
                                                  "petitioner.middle_name", "petitioner.family_name", "n400.fee_reduction", "asylum.ms_l")}
    if not out["petitioner_family_name"]:  # a spouse petition: the petitioner is the client's spouse (src/family.py)
        out["petitioner_given_name"], out["petitioner_family_name"] = value("applicant.spouse_given_name"), value("applicant.spouse_family_name")
    return out


def full_name(f: dict[str, Any]) -> str:
    words = " ".join(x for x in (f.get("given_name"), f.get("middle_name"), f.get("family_name")) if x).split()
    return " ".join(w.lower() if i and w.lower() in PARTICLES else w.capitalize() for i, w in enumerate(words))


def a_number(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return ""
    digits = digits.zfill(9)
    return f"A# {digits[:3]} {digits[3:6]} {digits[6:]}"


def long_date(d: date) -> str:
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


# Said beside each thing the paragraph needs, so the letter's missing paragraph is never silent (render() leaves it out, never a gap).
LEFT_OUT = "Until then the cover letter leaves out its priority-date paragraph."


@producer(OFFICE, part=1)
def priority(facts: dict[str, Any], config: dict[str, Any], today: date) -> tuple[dict[str, Any], list[str]]:
    """The priority-date paragraph's values, and what stops the packet from
    being final (an unset or out-of-date Visa Bulletin, a date not current)."""
    if "visa_bulletin" not in config:  # a petition filed alone: no priority date to be current
        return {"month": None, "chart": None, "cutoff": None, "area": None, "pd": None, "current": None}, []
    vb = config.get("visa_bulletin") or {}
    area = (config.get("chargeability") or {}).get(str(facts.get("country_of_birth") or "").upper(), "ALL CHARGEABILITY")
    cutoff = (vb.get("eb4_cutoff") or {}).get(area)
    pd = facts.get("i360_priority_date")
    this_month = f"{MONTHS[today.month - 1]} {today.year}"
    problems = []
    if not pd:
        problems.append(held(CLIENT, "No I-360 priority date in the case: check the I-797 approval notice. " + LEFT_OUT))
    if not vb.get("month") or not cutoff:
        problems.append(f"Set this month's Visa Bulletin (EB-4, {area.title()}) on the Settings page. " + LEFT_OUT)
    elif vb["month"] != this_month:
        problems.append(f"The Visa Bulletin (EB-4) is set for {vb['month']}: update it for {this_month} on the Settings page.")
    current = None
    if pd and cutoff:
        current = cutoff == "C" or date.fromisoformat(pd) < date.fromisoformat(cutoff)
        if not current:
            problems.append(held(ATTORNEY, f"Priority date {_us(pd)} is not before the {vb.get('month')} EB-4 cut-off ({_us(cutoff)}, {area}): "
                                           "the I-485 can't be filed this month. Ask the attorney."))
    return {"month": vb.get("month"), "chart": vb.get("chart") or "Dates for Filing", "cutoff": cutoff, "area": area, "pd": pd, "current": current}, problems


@producer(OFFICE)
def gaps(plan: dict[str, Any], facts: dict[str, Any], config: dict[str, Any]) -> list[str]:
    """What the letter would have to print as a bracketed gap, as packet problems (the priority-date
    paragraph is left out instead; priority() names what it needs). A letter is never mailed with a gap."""
    out = []
    if not full_name(facts):
        out.append("The client's name is not in the case, so the cover letter has a gap where it goes: check the answers first.")
    if config.get("also_name") == "petitioner" and not full_name({k.split("_", 1)[1]: v for k, v in facts.items() if k.startswith("petitioner_")}):
        out.append("The petitioner's name is not in the case, so the cover letter has a gap where it goes: check the answers first.")
    forms, docs = enclosures(plan, fees_for(config, facts))
    text = [fee_text(fees_for(config, facts)), *mail_to(config, facts)[0], *forms, *docs]
    if any("[" in t for t in text):
        out.append("The cover letter has a gap in its address or fee paragraph: check the filing address and the filing fees on the Settings page.")
    return out


def fees_for(config: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    """The letter's fee wording for this client: the first "fees_when" rule whose fact matches
    (the N-400's reduced fee, an asylum applicant in the Ms. L. class), else "fees"."""
    for rule in config.get("fees_when") or []:
        if facts.get(rule["fact"]) == rule["eq"]:
            return config | {k: v for k, v in rule.items() if k not in ("fact", "eq")}  # "fees", and "no_payment" when nothing is enclosed
    return config


def fee_text(config: dict[str, Any]) -> str:
    """The fees paragraph, its amounts from schemas/law/fees.json (the current Form G-1055; [fee] when unset)."""
    text = config["fees"]
    if "{" not in text:
        return text
    import fees

    data = fees.load()  # today's fees, with any announced change already in effect
    paper, extra = data.get("paper") or {}, data.get("pl_119_21") or {}
    money = lambda v: f"${v:,}" if isinstance(v, (int, float)) else "[fee]"  # noqa: E731
    family = [paper.get("i130"), paper.get("i485"), paper.get("i765_with_pending_i485_paid")]
    return text.format(i130=money(family[0]), i485=money(family[1]), i765=money(family[2]),
                       total=money(sum(family)) if all(isinstance(x, (int, float)) for x in family) else "[total]",
                       i360_sij_pl=money(extra.get("i360_sij")), n400=money(paper.get("n400")), n400_reduced=money(paper.get("n400_reduced")),
                       i589_pl=money(extra.get("i589_asylum")), annual_asylum=money(extra.get("annual_asylum")),
                       schedule=data.get("source") or "the current USCIS fee schedule")


def _us(iso: str) -> str:
    return f"{iso[5:7]}/{iso[8:10]}/{iso[:4]}" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(iso)) else str(iso)


def enclosures(plan: dict[str, Any], config: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(forms, supporting documents) as the letter lists them: only what the
    packet actually holds, in the firm's order."""
    form_ids = [f["id"] for f in plan.get("forms", [])]
    forms = [config["forms"][fid] for fid in config.get("form_order", ["g28", "i485", "i765"]) if fid in form_ids]
    forms += [config["forms"][fid] for fid in config.get("always_forms", ["i693"])]
    files = [f for ex in plan.get("exhibits", []) for f in ex["files"]]
    types = {f["type"] for f in files}
    translated = any(f["type"] == "translation_certification" or re.search(r"transl|tradu", f["doc"], re.I) for f in files)
    have = types | {"photos", "visa_bulletin_printout", "fee_calculator_printout"}
    docs = []
    for kind, text in config["documents"]:
        if kind not in have or (kind == "fee_calculator_printout" and config.get("no_payment")):
            continue
        if kind == "photos":
            text = text.format(photos=4 if "i765" in form_ids else 2)
        elif "{" in text:
            text = fee_text({"fees": text})
        if kind == "birth_certificate" and not translated:
            text = text.replace(" and Translation", "")
        docs.append(text)
    return forms, docs


def _letter_doc(config: dict[str, Any], draft: bool, title: str = "Cover letter"):
    """The firm's letter: letterhead on the first page, DRAFT across every page
    of a draft. Returns (buffer, document, styles) -- the cover letter and the
    RFE response letter (src/rfe.py) write their own story into it."""
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph
    from xml.sax.saxutils import escape

    W, H = letter
    head = config["letterhead"]
    body = ParagraphStyle("body", fontName="Times-Roman", fontSize=12, leading=15, alignment=TA_JUSTIFY)
    left = ParagraphStyle("left", parent=body, alignment=0)
    item = ParagraphStyle("item", parent=left, leftIndent=0.95 * inch, firstLineIndent=-0.32 * inch, spaceAfter=3)
    small = ParagraphStyle("small", fontName="Helvetica", fontSize=8, leading=10)

    def letterhead(canvas, doc):
        canvas.saveState()
        x, y = 0.75 * inch, H - 0.85 * inch
        room = W - 1.5 * inch
        size = 32  # a long name is set smaller so it stays on the page
        full = stringWidth(head["name_light"], "Helvetica", 32) + stringWidth(head["name_bold"], "Helvetica-Bold", 32)
        if full > room:
            size = max(14, 32 * room / full)
        canvas.setFont("Helvetica", size)
        canvas.drawString(x, y, head["name_light"])
        x2 = x + stringWidth(head["name_light"], "Helvetica", size)
        canvas.setFont("Helvetica-Bold", size)
        canvas.drawString(x2, y, head["name_bold"])
        after = x2 + stringWidth(head["name_bold"], "Helvetica-Bold", size) + 12
        canvas.setFont("Helvetica", 7)
        drop = 0
        if head["address"]:
            if after + stringWidth(head["address"], "Helvetica", 7) <= W - 0.5 * inch:
                canvas.drawString(after, y + 2, head["address"])
            else:  # no room beside the name: under it
                canvas.drawString(x, y - 12, head["address"])
                drop = 10
        if head["tagline"]:
            canvas.setFont("Helvetica-Bold", 8)
            canvas.drawString(x, y - 16 - drop, head["tagline"])
        if head["attorneys"]:
            names = Paragraph(escape(head["attorneys"]), small)
            _, h = names.wrap(room, 40)
            names.drawOn(canvas, x, y - 22 - drop - h)
        canvas.restoreState()
        watermark(canvas, doc)

    def watermark(canvas, doc):
        if not draft:
            return
        canvas.saveState()
        canvas.setFillColorRGB(0.82, 0.82, 0.82)
        canvas.setFont("Helvetica-Bold", 54)
        canvas.translate(W / 2, H / 2)
        canvas.rotate(40)
        canvas.drawCentredString(0, 0, "DRAFT - NOT FOR FILING")
        canvas.restoreState()

    out = io.BytesIO()
    doc = BaseDocTemplate(out, pagesize=letter, title=title, leftMargin=inch, rightMargin=inch)
    first = Frame(inch, inch, W - 2 * inch, H - 2.55 * inch, id="first")
    later = Frame(inch, inch, W - 2 * inch, H - 2 * inch, id="later")
    doc.addPageTemplates([PageTemplate("first", [first], onPage=letterhead, autoNextPageTemplate="later"),
                          PageTemplate("later", [later], onPage=watermark)])
    return out, doc, {"body": body, "left": left, "item": item}


def _signature_block(config: dict[str, Any], styles: dict[str, Any]) -> list:
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import KeepTogether, Paragraph, Spacer
    from xml.sax.saxutils import escape

    body, left, signer = styles["body"], styles["left"], config["signer"]
    return [Spacer(1, 12), Paragraph(escape(config["closing"]), ParagraphStyle("close", parent=body, firstLineIndent=0.4 * inch)), Spacer(1, 16),
            KeepTogether([Paragraph("Sincerely,", left), Spacer(1, 42), Paragraph(escape(signer["name"]), left),
                          Paragraph("<br/>".join(escape(x) for x in signer["lines"]), left)])]


def intro(config: dict[str, Any], name: str, pron: str) -> str:
    """The opening paragraph (name already escaped). The firm's fixed wording, or -- where the filing's letter has the
    {case_paragraph} slot in its "intro" (the I-589, I-918, I-914 and VAWA I-360 letters) -- the same wording with the case's
    own facts in the slot (src/drafting.py cover_paragraph, set by packet._letter_config; nothing when it has none)."""
    from xml.sax.saxutils import escape

    noun = escape(config.get("noun", "application"))
    template = config.get("intro") or ("Please be advised that this law office has been retained to represent {name} with respect to {pron} "
                                       "immigration matters. In support of {pron} {noun}, we have provided the following documents:")
    text = template.format(name=f"<b>{name}</b>", pron=pron, noun=noun, case_paragraph=escape(config.get("case_paragraph") or ""))
    return re.sub(r"\s{2,}", " ", text)


def render(plan: dict[str, Any], facts: dict[str, Any], config: dict[str, Any], today: date, draft: bool) -> bytes:
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import KeepTogether, Paragraph, Spacer
    from xml.sax.saxutils import escape

    out, doc, styles = _letter_doc(config, draft)
    body, left, item = styles["body"], styles["left"], styles["item"]

    name = escape(full_name(facts) or "[applicant's name]")
    anum = a_number(facts.get("a_number"))
    pron = {"F": "her", "M": "his"}.get(str(facts.get("sex") or "").upper(), "their")
    form_ids = [f["id"] for f in plan.get("forms", [])]
    forms, docs = enclosures(plan, fees_for(config, facts))
    pd, _ = priority(facts, config, today)
    gap = lambda n=10: Spacer(1, n)  # noqa: E731

    re_lines = config.get("re_lines") or (["Application: I-485 Application to Register Permanent Residence or Adjust Status"]
                                          + (["I-765 Application for Employment Authorization"] if "i765" in form_ids else []))
    petitioner = escape(full_name({k.split("_", 1)[1]: v for k, v in facts.items() if k.startswith("petitioner_")}) or "[petitioner]")
    story = [Paragraph(long_date(today), left), gap(14), Paragraph(f"<u><b>{escape(config['delivery'])}</b></u>", left), gap(12),
             Paragraph("<br/>".join(escape(x) for x in mail_to(config, facts)[0]), left), gap(14),
             Paragraph("<b>RE: " + "<br/>".join(escape(x) for x in re_lines) + "</b>", left), gap(10),
             Paragraph(f"<b>{escape(config.get('who', 'Applicant'))}: {name}{f' ({anum})' if anum else ''}</b>"
                       + (f"<br/><b>Petitioner: {petitioner}</b>" if config.get("also_name") == "petitioner" else ""), left), gap(12),
             Paragraph("Dear Sir/Madam:", left), gap(10),
             Paragraph(intro(config, name, pron), ParagraphStyle("lead", parent=body, firstLineIndent=0.4 * inch)), gap(12),
             Paragraph("<u><b>FORMS</b></u>", left), gap(6)]
    # The firm numbers its forms from (2) because the cover letter is enclosure (1) (its own filed letter); the letter now lists
    # itself as (1) so nothing looks missing.
    story += [Paragraph(f"({i})&nbsp;&nbsp;{escape(text)}{';' if i < len(forms) + 1 else '.'}", item)
              for i, text in enumerate(["This cover letter", *forms], start=1)]
    story += [gap(12), Paragraph("<u><b>SUPPORTING DOCUMENTS</b></u>", left), gap(6)]
    story += [Paragraph(f"{i}.&nbsp;&nbsp;{escape(text)}{';' if i < len(docs) else '.'}", item) for i, text in enumerate(docs, start=1)]

    month = escape(pd["month"] or "")
    chart = escape(pd["chart"] or "")
    cutoff = ("is current" if pd["cutoff"] == "C" else f"has a cut-off date of {long_date(date.fromisoformat(pd['cutoff']))}") if pd["cutoff"] else ""
    pdate = long_date(date.fromisoformat(pd["pd"])) if pd["pd"] else ""
    # Without the bulletin's month, the cut-off or the priority date the paragraph says nothing true, so it is left out
    # (never a bracketed gap in a letter to USCIS); priority() names what is missing and the packet stays DRAFT.
    facts_known = bool(month and cutoff and pdate)
    story += [] if "visa_bulletin" not in config or not facts_known else [gap(14), KeepTogether([Paragraph("<b>Priority Date Eligibility</b>", left), gap(6), Paragraph(
        f"According to the U.S. Citizenship and Immigration Services (USCIS) website, for the month of {month}, USCIS has confirmed that it "
        f"will use the “{chart}” chart from the Department of State’s Visa Bulletin to determine eligibility for filing adjustment of status "
        "applications. The applicant’s approved Form I-360, Petition for Amerasian, Widow(er), or Special Immigrant (Special Immigrant Juvenile "
        "classification), falls under the Employment-Based Fourth Preference (EB-4) category. "
        f"As reflected in the {month} Visa Bulletin, the “{chart}” chart for the EB-4 category {cutoff}. "
        f"Since the applicant’s priority date of {pdate} is earlier than the published cut-off date, the priority date is current, and the "
        "applicant is eligible to file Form I-485, Application to Register Permanent Residence or Adjust Status, during this filing period. "
        "A printed copy of the relevant USCIS and Department of State Visa Bulletin pages is enclosed for reference.", body)])]
    story += [gap(12), KeepTogether([Paragraph("<b>Filing Fees</b>", left), gap(6), Paragraph(escape(fee_text(fees_for(config, facts))), body)])]
    story += _signature_block(config, styles)
    doc.build(story)
    return out.getvalue()


def render_response(config: dict[str, Any], facts: dict[str, Any], today: date, draft: bool, address: list[str], re_lines: list[str],
                    intro: str, items: list[dict[str, Any]]) -> bytes:
    """A response letter (src/rfe.py): to the address on USCIS's notice, each
    requested item numbered with the exhibit that answers it."""
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Spacer
    from xml.sax.saxutils import escape

    out, doc, styles = _letter_doc(config, draft, "Response to USCIS")
    body, left, item = styles["body"], styles["left"], styles["item"]
    name = escape(full_name(facts) or "[applicant's name]")
    anum = a_number(facts.get("a_number"))
    story = [Paragraph(long_date(today), left), Spacer(1, 14), Paragraph(f"<u><b>{escape(config['delivery'])}</b></u>", left), Spacer(1, 12),
             Paragraph("<br/>".join(escape(x) for x in address) or "[the address on the notice]", left), Spacer(1, 14),
             Paragraph("<b>RE: " + "<br/>".join(escape(x) for x in re_lines) + "</b>", left), Spacer(1, 10),
             Paragraph(f"<b>{escape(config.get('who', 'Applicant'))}: {name}{f' ({anum})' if anum else ''}</b>", left), Spacer(1, 12),
             Paragraph("Dear Sir/Madam:", left), Spacer(1, 10),
             Paragraph(escape(intro), ParagraphStyle("lead", parent=body, firstLineIndent=0.4 * inch)), Spacer(1, 12)]
    for n, it in enumerate(items, start=1):
        story.append(Paragraph(f"({n})&nbsp;&nbsp;<b>{escape(it['text'])}</b>", item))
        if it.get("exhibit"):
            story.append(Paragraph(f"Enclosed: Exhibit {escape(it['exhibit'])}: " + escape("; ".join(it.get("titles") or [])), item))
        if it.get("note"):
            story.append(Paragraph(escape(it["note"]), item))
        story.append(Spacer(1, 6))
    story += _signature_block(config, styles)
    doc.build(story)
    return out.getvalue()
