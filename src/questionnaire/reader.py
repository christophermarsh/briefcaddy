"""Application parser or workflow helper."""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from classify.ocr import OcrWord, ocr_words
from extract.base import ExtractedField
from .checkboxes import ChoiceReading, LabelHit, OptionInk, box_region, ink_fraction, judge_multi_choice, judge_single_choice, typical_word_height
import schema_path
DEFAULT_WINDOW = 500

@dataclass
class QuestionAnswer:
    question_id: str
    fact_key: str
    reading: ChoiceReading

@dataclass
class QuestionnaireReading:
    answers: list[QuestionAnswer] = field(default_factory=list)
    unread: dict[str, str] = field(default_factory=dict)
    text_fields: list[ExtractedField] = field(default_factory=list)
    blank_template: bool = False
    language: str = 'pt'
    unsupported_language: str | None = None
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)

    def extracted_fields(self) -> list[ExtractedField]:
        return [ExtractedField(fact_key=a.fact_key, raw_value=a.reading.chosen, normalized_value=a.reading.chosen, confidence=a.reading.confidence) for a in self.answers] + self.text_fields

def load_questionnaire_map(path: str | Path) -> list[dict[str, Any]]:
    return json.loads(Path(path).read_text(encoding='utf-8'))['questions']

@dataclass
class _Stream:
    text: str
    owners: list[tuple[int, OcrWord, int] | None]

    @classmethod
    def build(cls, pages: list[list[OcrWord]]) -> '_Stream':
        parts: list[str] = []
        owners: list[tuple[int, OcrWord, int] | None] = []
        for page_index, words in enumerate(pages):
            for word in words:
                for i, ch in enumerate(word.text):
                    parts.append(ch)
                    owners.append((page_index, word, i))
                parts.append(' ')
                owners.append(None)
            parts.append('\n')
            owners.append(None)
        return cls(''.join(parts), owners)

    def hit_at(self, offset: int) -> LabelHit | None:
        owner = self.owners[offset] if offset < len(self.owners) else None
        if owner is None:
            return None
        page, word, char_index = owner
        return LabelHit(page=page, word=word, char_index=char_index)
_TYPED_MARK = {'X', 'x', '✓', '✔', '✗', '✘'}

def _typed_marks_in(typed, option: OptionInk, pad: int=20) -> bool:
    x0, y0, x1, y1 = option.box
    for t in typed:
        if t.page != option.hit.page or t.text.strip() not in _TYPED_MARK:
            continue
        cx, cy = ((t.box[0] + t.box[2]) / 2, (t.box[1] + t.box[3]) / 2)
        if x0 - pad <= cx <= x1 + pad and y0 - pad <= cy <= y1 + pad:
            return True
    return False

def judge_typed(options: list[OptionInk], typed, multi: bool) -> list[ChoiceReading]:
    """Application parser or workflow helper."""
    marked_values = set()
    for t in typed:
        if t.text.strip() not in _TYPED_MARK:
            continue
        near = [o for o in options if o.hit.page == t.page and _typed_marks_in([t], o)]
        if near:
            cx, cy = ((t.box[0] + t.box[2]) / 2, (t.box[1] + t.box[3]) / 2)
            best = min(near, key=lambda o: abs((o.box[0] + o.box[2]) / 2 - cx) + abs((o.box[1] + o.box[3]) / 2 - cy))
            marked_values.add(best.value)
    marked = [o for o in options if o.value in marked_values]
    if not marked:
        return [ChoiceReading(None, options, 'no typed X in any box (client left it blank)')]
    if len(marked) > 1 and (not multi):
        return [ChoiceReading(None, options, f"typed X in more than one box: {', '.join((o.value for o in marked))}")]
    return [ChoiceReading(o.value, options, 'typed X in this box') for o in marked]

def read_questionnaire(pages: list[tuple[Any, list[OcrWord]]], questions: list[dict[str, Any]], typed: list | None=None, model_call=None) -> QuestionnaireReading:
    """Application parser or workflow helper."""
    typed = typed or []
    digital_pages = {t.page for t in typed}
    stream = _Stream.build([words for _, words in pages])
    heights = [typical_word_height(words) for _, words in pages]
    result = QuestionnaireReading()
    anchors: dict[str, int] = {}
    for q in questions:
        match = re.search(q['anchor'], stream.text)
        if match:
            anchors[q['id']] = match.end()
        else:
            result.unread[q['id']] = 'question text not found on any page'
    anchor_positions = sorted(anchors.values())
    for q in questions:
        start = anchors.get(q['id'])
        if start is None:
            continue
        later = [p for p in anchor_positions if p > start]
        end = min(start + q.get('window', DEFAULT_WINDOW), later[0] if later else len(stream.text))
        options: list[OptionInk] = []
        printed: list[str] = []
        cursor = start
        missing = []
        for value, label_regex in q['options']:
            match = re.compile(label_regex).search(stream.text, cursor, end)
            hit = stream.hit_at(match.start()) if match else None
            if hit is None:
                missing.append(value)
                continue
            cursor = match.end()
            printed.append(stream.text[match.start():match.end()])
            gray, _ = pages[hit.page]
            box = box_region(hit, heights[hit.page])
            options.append(OptionInk(value=value, hit=hit, ink=ink_fraction(gray, box), box=box))
        if options:
            page = options[0].hit.page
            on_page = [o for o in options if o.hit.page == page]
            h = heights[page]
            result.evidence[q['id']] = {'kind': 'choice', 'fact_key': q['fact_key'], 'options': [value for value, _ in q['options']], 'multi': q.get('kind') == 'multi', 'page': page, 'box': [0, max(0, int(min((o.box[1] for o in on_page)) - 3 * h)), pages[page][0].width, min(pages[page][0].height, int(max((o.box[3] for o in on_page)) + h))], 'ink': {o.value: round(o.ink, 3) for o in options}}
        if missing:
            note = f"option label(s) not found: {', '.join(missing)}"
            if q.get('kind') == 'multi' and len(options) >= q.get('min_options', len(q['options'])):
                result.unread[q['id']] = note + ' -- judged on the rest; verify the missing option(s) by eye'
            else:
                result.unread[q['id']] = note
                continue
        if options and options[0].hit.page in digital_pages:
            readings = judge_typed(options, typed, q.get('kind') == 'multi')
        elif q.get('kind') == 'multi':
            readings = judge_multi_choice(options)
        else:
            readings = [judge_single_choice(options)]
        if model_call is not None and options and (options[0].hit.page not in digital_pages) and (q['id'] in result.evidence):
            readings = _cross_check_with_vision(q, options, printed, readings, pages, result.evidence[q['id']], model_call)
        for reading in readings:
            if reading.chosen is None:
                result.unread[q['id']] = reading.reason
            else:
                result.answers.append(QuestionAnswer(q['id'], q['fact_key'], reading))
    return result

def _cross_check_with_vision(q, options, printed, readings, pages, evidence, model_call):
    """Application parser or workflow helper."""
    from .verify import vision_marks
    multi = q.get('kind') == 'multi'
    image = pages[evidence['page']][0]
    marked = vision_marks(image, evidence['box'], printed, multi, model_call)
    ink_values = sorted((r.chosen for r in readings if r.chosen))
    if marked is None:
        evidence['vision'] = None
        evidence['check'] = 'vision reader gave no usable answer -- box darkness only'
        return readings
    vision_values = sorted((options[n - 1].value for n in marked))
    evidence['vision'] = vision_values
    if ink_values == vision_values:
        evidence['check'] = 'both readers agree'
        if not ink_values:
            return [ChoiceReading(None, options, 'client left it blank (both readers see no mark)')]
        return readings
    evidence['check'] = 'readers disagree'
    evidence['suggest'] = vision_values
    said = lambda values: ', '.join(values) or 'no mark'
    reason = f'the two readers disagree: box darkness says {said(ink_values)}, the vision model says {said(vision_values)}'
    return [ChoiceReading(None, options, reason)]

def load_questionnaire_maps(schemas_dir: str | Path) -> dict[str, tuple[list, list]]:
    """Application parser or workflow helper."""
    schemas_dir = Path(schemas_dir)
    maps = {}
    for name in schema_path.names('paper_map', schemas_dir):
        if name != 'map' and (not name.startswith('map.')):
            continue
        checkbox_file = schema_path.path('paper_map', name, schemas_dir)
        data = json.loads(checkbox_file.read_text(encoding='utf-8'))
        suffix = name[len('map'):]
        language = data.get('language') or (suffix[1:] if suffix else 'pt')
        text_file = schema_path.path('paper_map', 'text_map' + suffix, schemas_dir)
        text_fields = json.loads(text_file.read_text(encoding='utf-8'))['fields'] if text_file.exists() else []
        maps[language] = (data['questions'], text_fields)
    return maps

def read_questionnaire_pdf(pdf_path: str | Path, questions: list[dict[str, Any]] | None=None, work_dir: str | Path='.ocr_tmp', text_fields: list[dict[str, Any]] | None=None, model_call=None, maps: dict[str, tuple[list, list]] | None=None, use_vision: bool | None=None) -> QuestionnaireReading:
    """Application parser or workflow helper."""
    from .languages import LANGUAGE_NAMES, detect_language
    from .pages import page_images, typed_annotations
    from .template import is_blank_template
    if maps is None:
        maps = {'pt': (questions or [], text_fields or [])}
    if is_blank_template(pdf_path):
        return QuestionnaireReading(blank_template=True)
    typed = typed_annotations(pdf_path)
    images = page_images(pdf_path)
    label_images = page_images(pdf_path, annotations=False) if typed else images
    pages: list[tuple[Any, list[OcrWord]]] = [(gray, ocr_words(bare, work_dir=work_dir)) for gray, bare in zip(images, label_images)]
    language = detect_language(' '.join((w.text for _, words in pages for w in words))) or 'pt'
    if language not in maps:
        return QuestionnaireReading(language=language, unsupported_language=LANGUAGE_NAMES.get(language, language))
    questions, text_fields = maps[language]
    if use_vision is None:
        use_vision = bool(text_fields)
    checker = None
    if use_vision:
        from .handwriting import ollama_model_call
        checker = model_call or ollama_model_call
    result = read_questionnaire(pages, questions, typed=typed, model_call=checker)
    result.language = language
    if use_vision and text_fields:
        from .handwriting import read_text_fields
        fields, unread = read_text_fields(pages, text_fields, checker, evidence=result.evidence, typed=typed, language=language)
        result.text_fields = fields
        result.unread.update(unread)
    return result
