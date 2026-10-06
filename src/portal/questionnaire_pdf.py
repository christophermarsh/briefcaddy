"""A readable client copy of submitted portal answers, generated on demand."""
from __future__ import annotations

import io
import threading
from pathlib import Path
from xml.sax.saxutils import escape

import clock

from .bank import UNSURE, bank_for, visible

WORDS = {
    "en": ("Submitted questionnaire", "Submitted", "Confirmed by", "No answer", "I'm not sure", "None of these", "Selected", "Explanation", "Page"),
    "pt": ("Questionário enviado", "Enviado em", "Confirmado por", "Sem resposta", "Não tenho certeza", "Nenhuma destas opções", "Selecionado", "Explicação", "Página"),
    "es": ("Cuestionario enviado", "Enviado el", "Confirmado por", "Sin respuesta", "No estoy seguro", "Ninguna de estas opciones", "Seleccionado", "Explicación", "Página"),
    "ht": ("Kesyonè ki voye", "Voye nan", "Konfime pa", "Pa gen repons", "Mwen pa sèten", "Okenn nan sa yo", "Chwazi", "Eksplikasyon", "Paj"),
}
ADDRESS_FIELDS = {
    "en": ("Street and number", "Apt (if any)", "City", "State", "ZIP code", "State / province", "Postal code", "Country", "Lived there from", "Until"),
    "pt": ("Rua e número", "Apto (se tiver)", "Cidade", "Estado", "Código ZIP", "Estado / província", "CEP / código postal", "País", "Morei lá desde", "Até"),
    "es": ("Calle y número", "Apto (si tiene)", "Ciudad", "Estado", "Código ZIP", "Estado / provincia", "Código postal", "País", "Viví allí desde", "Hasta"),
    "ht": ("Ri ak nimewo", "Apatman (si genyen)", "Vil", "Eta", "Kòd ZIP", "Eta / pwovens / depatman", "Kòd postal", "Peyi", "Mwen te rete la depi", "Jiska"),
}
_FONT_LOCK = threading.Lock()
# A single style boundary for a future approved firm-header configuration.
# No firm name or logo is inferred from client files.
PAGE_ACCENT = "#263f59"
PAGE_MARGIN = 48
COPY_WORDS = {
    "en": {"copy": "Client answer copy", "source": "Source: saved client questionnaire answers.", "entry": "Entry", "of": "of", "optional": "Optional", "monthly": "per month", "sections": "Sections", "conditional": "Only questions shown for these answers are included."},
    "pt": {"copy": "Cópia das respostas do cliente", "source": "Fonte: respostas salvas do questionário do cliente.", "entry": "Registro", "of": "de", "optional": "Opcional", "monthly": "por mês", "sections": "Seções", "conditional": "Inclui apenas as perguntas exibidas para estas respostas."},
    "es": {"copy": "Copia de las respuestas del cliente", "source": "Fuente: respuestas guardadas del cuestionario del cliente.", "entry": "Registro", "of": "de", "optional": "Opcional", "monthly": "por mes", "sections": "Secciones", "conditional": "Incluye solo las preguntas mostradas para estas respuestas."},
    "ht": {"copy": "Kopi repons kliyan an", "source": "Sous: repons kesyonè kliyan an ki anrejistre.", "entry": "Antre", "of": "sou", "optional": "Pa obligatwa", "monthly": "pa mwa", "sections": "Seksyon", "conditional": "Se sèlman kesyon ki parèt pou repons sa yo ki ladan."},
}


def text(value, language):
    if isinstance(value, dict):
        return str(value.get(language) or value.get("en") or next(iter(value.values()), ""))
    return str(value or "")


def answer_fields(question, language):
    if question.get("type") in ("us_address", "foreign_address"):
        return {key: {"label": label, "type": "date" if key.startswith("date_") else "text"}
                for key, label in zip(("street", "apt", "city", "state", "zip", "province", "postal_code", "country", "date_from", "date_to"), ADDRESS_FIELDS[language])}
    return {f["id"]: f for f in question.get("fields", [])}


def answer_lines(question, value, language):
    words = WORDS[language]
    if value in (None, "", [], {}):
        return [words[3]]
    if value == UNSURE:
        return [words[4]]
    kind = question.get("type")
    options = {o["value"]: text(o["label"], language) for o in question.get("options", [])}
    if kind == "checklist" and isinstance(value, dict):
        if value.get("none"):
            return [words[5]]
        rows = [f"{words[6]}: {options.get(v, v)}" for v in value.get("selected") or []]
        return rows + ([f"{words[7]}: {value['explain']}"] if value.get("explain") else [])
    if kind in ("height", "weight") and isinstance(value, dict):
        metric = f"{value['cm']} cm" if value.get("cm") else f"{value['kg']} kg" if value.get("kg") else ""
        displayed = str(value.get("form") or "")
        if kind == 'weight' and displayed:
            displayed += ' lb'
        return [displayed + (f" ({metric})" if metric else "")]
    fields = answer_fields(question, language)
    if isinstance(value, list):
        rows = []
        for n, entry in enumerate(value, 1):
            rows.append(f"{n}.")
            rows.extend(answer_lines(question, entry, language))
        return rows
    if isinstance(value, dict):
        rows = []
        for key, part in value.items():
            if part in (None, "", [], {}):
                continue
            field = fields.get(key, {})
            label = text(field.get("label") or key.replace("_", " "), language)
            rows.extend(f"{label}: {line}" for line in answer_lines(field, part, language))
        return rows or [words[3]]
    if isinstance(value, str) and value in options:
        return [options[value]]
    if kind == "date" and isinstance(value, str) and len(value) == 10 and value[4] == "-":
        return [f"{value[5:7]}/{value[8:10]}/{value[:4]}" if language == "en" else f"{value[8:10]}/{value[5:7]}/{value[:4]}"]
    if kind == "money":
        import eoir26a
        amount = eoir26a.parse_money(value)
        if amount is not None:
            return [f"{eoir26a.usd_for(amount, language)} {COPY_WORDS[language]['monthly']}"]
    return [str(value)]


def render(profile, answers, bank=None):
    if profile.get("status") != "submitted" or not profile.get("submitted_at"):
        raise ValueError("The questionnaire PDF is available after the client submits it.")
    from reportlab import __file__ as reportlab_file
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import (
        CondPageBreak,
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    with _FONT_LOCK:
        if "Questionnaire" not in pdfmetrics.getRegisteredFontNames():
            fonts = Path(reportlab_file).parent / "fonts"
            pdfmetrics.registerFont(TTFont("Questionnaire", str(fonts / "Vera.ttf")))
            pdfmetrics.registerFont(TTFont("QuestionnaireBold", str(fonts / "VeraBd.ttf")))
    attestation = profile.get("attestation") or {}
    language = attestation.get("language") or profile.get("language") or "en"
    language = language if language in WORDS else "en"
    words = WORDS[language]
    copy = COPY_WORDS[language]
    bank = bank if bank is not None else bank_for(profile)
    ink, muted, accent = colors.HexColor("#172033"), colors.HexColor("#566175"), colors.HexColor(PAGE_ACCENT)
    line, wash = colors.HexColor("#dbe2e9"), colors.HexColor("#f2f5f8")
    width = letter[0] - 2 * PAGE_MARGIN
    body = ParagraphStyle("answer", fontName="Questionnaire", fontSize=10.5, leading=15, textColor=ink,
                          leftIndent=14, spaceAfter=8, splitLongWords=True)
    question = ParagraphStyle("question", parent=body, fontName="QuestionnaireBold", fontSize=9.5, leading=14,
                              textColor=accent, leftIndent=0, spaceBefore=6, spaceAfter=4, keepWithNext=True)
    section = ParagraphStyle("section", parent=question, fontSize=12, leading=17, textColor=colors.white,
                             spaceBefore=0, spaceAfter=0)
    heading = ParagraphStyle("title", parent=question, fontSize=23, leading=29, textColor=ink, spaceBefore=0, spaceAfter=7)
    name_style = ParagraphStyle("name", parent=body, leftIndent=0, fontSize=15, leading=21, spaceAfter=8)
    metadata = ParagraphStyle("metadata", parent=body, leftIndent=0, fontSize=8.5, leading=12, textColor=muted, spaceAfter=4)
    empty_style = ParagraphStyle("empty", parent=body, textColor=muted)
    entry_style = ParagraphStyle("entry", parent=question, leftIndent=14, spaceBefore=3, spaceAfter=4)

    def paragraph(value, style):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), style)

    def field_answer(label, q, value):
        # Apply weight to the structural label, never by parsing answer text.
        # Explicitly restore the regular face, including short state/country codes.
        lines = "<br/>".join(escape(item).replace("\n", "<br/>") for item in answer_lines(q, value, language))
        return Paragraph(f'<font name="QuestionnaireBold" color="{PAGE_ACCENT}">{escape(label)}:</font> '
                         f'<font name="Questionnaire">{lines}</font>', body)

    def answer_story(q, value, prefix=None):
        """Split every substantive answer as paragraphs, never an unsplittable table."""
        prefix = prefix or []
        if q.get("type") == "repeat" and isinstance(value, list) and value:
            result = []
            fields = {f["id"]: f for f in q.get("fields", [])}
            for n, entry in enumerate(value, 1):
                entry_blocks = (prefix if n == 1 else []) + [paragraph(f"{copy['entry']} {n}", entry_style)]
                if isinstance(entry, dict):
                    for key, part in entry.items():
                        f = fields.get(key, {"type": "text", "label": key.replace("_", " ")})
                        if isinstance(part, (dict, list)) and part:
                            entry_blocks.append(paragraph(text(f.get("label", key), language), entry_style))
                            entry_blocks.extend(answer_story(f, part))
                        else:
                            entry_blocks.append(field_answer(text(f.get("label", key), language), f, part))
                else:
                    entry_blocks.extend(answer_story({"type": "text"}, entry))
                if len(str(entry)) < 800 and len(entry_blocks) - len(prefix if n == 1 else []) <= 13:
                    result.append(KeepTogether(entry_blocks))
                else:
                    if n > 1:
                        result.append(CondPageBreak(52))
                    result.extend(entry_blocks)
            return result
        # Each line remains separate so long explanations and nested histories can
        # continue across pages without clipping or losing their final sentences.
        style = empty_style if value in (None, "", [], {}) else body
        result = []
        if isinstance(value, dict) and q.get("type") in ("group", "us_address", "foreign_address"):
            fields = answer_fields(q, language)
            for key, part in value.items():
                if part in (None, "", [], {}):
                    continue
                f = fields.get(key, {"type": "text", "label": key.replace("_", " ")})
                label = text(f.get("label", key), language)
                if isinstance(part, (dict, list)) and part:
                    result.append(paragraph(label, entry_style))
                    result.extend(answer_story(f, part))
                else:
                    result.append(field_answer(label, f, part))
        elif isinstance(value, dict) and q.get("type") == "checklist" and not value.get("none"):
            options = {o["value"]: text(o["label"], language) for o in q.get("options", [])}
            result = [field_answer(words[6], {"type": "text"}, options.get(v, v)) for v in value.get("selected") or []]
            if value.get("explain"):
                result.append(field_answer(words[7], {"type": "text"}, value["explain"]))
        if not result:
            result = [paragraph(item, style) for item in answer_lines(q, value, language)]
        result = prefix + result
        if q.get("type") in ("group", "us_address", "foreign_address") and len(str(value)) < 800 and len(result) - len(prefix) <= 12:
            return [KeepTogether(result)]
        return result

    name = profile.get("name") or profile["id"]
    day = clock.day(profile["submitted_at"])
    date = f"{day[5:7]}/{day[8:10]}/{day[:4]}" if language == "en" else f"{day[8:10]}/{day[5:7]}/{day[:4]}"
    story = [paragraph(copy["copy"].upper(), metadata), paragraph(words[0], heading)]
    identity = [paragraph(name, name_style), paragraph(f"{words[1]}: {date}", metadata)]
    if attestation.get("typed_name"):
        identity.append(paragraph(f"{words[2]}: {attestation['typed_name']}", metadata))
    identity.append(paragraph(copy["source"], metadata))
    card = Table([[identity]], colWidths=[width])
    card.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), wash), ("BOX", (0, 0), (-1, -1), .5, line),
                              ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                              ("TOPPADDING", (0, 0), (-1, -1), 12), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
    story += [card, Spacer(1, 10), paragraph(copy["conditional"], metadata)]
    # Older submitted copies must not retroactively present the newly added
    # optional section as nine unanswered questions. No saved answers are changed.
    groups = [(group, [q for q in group["questions"] if visible(q, answers)
                       and (not q.get('added_in') or q['id'] in answers)]) for group in bank["sections"]
              if group.get("id") != "monthly_money" or any(q["id"] in answers for q in group["questions"])]
    groups = [(group, shown) for group, shown in groups if shown]
    number = 0
    for section_number, (group, shown) in enumerate(groups, 1):
        title = f"{section_number:02d}  {text(group['title'], language)}"
        bar = Table([[paragraph(title, section)]], colWidths=[width])
        bar.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), accent), ("LEFTPADDING", (0, 0), (-1, -1), 12),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 12), ("TOPPADDING", (0, 0), (-1, -1), 8),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
        bar.keepWithNext = True
        gap = Spacer(1, 7)
        gap.keepWithNext = True
        story += [Spacer(1, 10), CondPageBreak(88)]
        for question_index, q in enumerate(shown):
            number += 1
            label = f"{number:02d}.  {text(q['label'], language)}"
            if q.get("required") is False:
                label += f" ({copy['optional']})"
            # Include the section bar in the first answer's keep block. A table's
            # keepWithNext alone cannot keep it with a following KeepTogether.
            prefix = ([bar, gap] if question_index == 0 else []) + [paragraph(label, question)]
            story += answer_story(q, answers.get(q["id"]), prefix)

    class NumberedPages(Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.saved_pages = []

        def showPage(self):
            self.saved_pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self.saved_pages)
            for page in self.saved_pages:
                self.__dict__.update(page)
                self.saveState()
                self.setStrokeColor(line)
                self.setLineWidth(.5)
                self.line(PAGE_MARGIN, letter[1] - 39, letter[0] - PAGE_MARGIN, letter[1] - 39)
                self.setFont("QuestionnaireBold", 8)
                self.setFillColor(accent)
                self.drawString(PAGE_MARGIN, letter[1] - 29, words[0])
                self.setFont("Questionnaire", 8)
                self.setFillColor(muted)
                self.line(PAGE_MARGIN, 43, letter[0] - PAGE_MARGIN, 43)
                self.drawString(PAGE_MARGIN, 29, copy["copy"])
                self.drawRightString(letter[0] - PAGE_MARGIN, 29, f"{words[8]} {self._pageNumber} {copy['of']} {total}")
                self.restoreState()
                super().showPage()
            super().save()

    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=letter, leftMargin=PAGE_MARGIN, rightMargin=PAGE_MARGIN, topMargin=54, bottomMargin=58,
                      title=words[0], author="", subject="Saved client questionnaire answers").build(story, canvasmaker=NumberedPages)
    return out.getvalue()
