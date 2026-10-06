"""Independently fictional questionnaire page/answer fixture."""
from pathlib import Path
import pytest
from PIL import Image, ImageDraw
from classify.ocr import OcrWord, find_tesseract, parse_tsv
from questionnaire import load_questionnaire_map, read_questionnaire
import schema_path
_REPO_ROOT = Path(__file__).resolve().parent.parent
_QMAP = schema_path.path('paper_map', 'map')
H = 23
SIM = '(?<![A-Za-z])S[i1l]m(?![a-z])'
NAO = '(?<![A-Za-z])N\\S{1,3}?(?=[\\s,._|;:)]|$)'
YES_NO = [['Yes', SIM], ['No', NAO]]

class Page:
    """Independently fictional questionnaire page/answer fixture."""

    def __init__(self, width=1000, height=400):
        self.image = Image.new('L', (width, height), 255)
        self.draw = ImageDraw.Draw(self.image)
        self.words: list[OcrWord] = []
        self._line = 0

    def text(self, *tokens_and_x, top):
        self._line += 1
        for token, left in tokens_and_x:
            self.words.append(OcrWord(token, left, top, 12 * len(token), H, 90.0, (1, 1, self._line)))

    def box(self, label_left, top, marked):
        """Independently fictional questionnaire page/answer fixture."""
        x0, x1 = (label_left - 70, label_left - 10)
        self.draw.arc((x0, top - 4, x0 + 12, top + H + 4), 90, 270, fill=0, width=2)
        self.draw.arc((x1 - 12, top - 4, x1, top + H + 4), 270, 90, fill=0, width=2)
        if marked:
            self.draw.line((x0 + 16, top, x1 - 16, top + H), fill=0, width=4)
            self.draw.line((x0 + 16, top + H, x1 - 16, top), fill=0, width=4)

    def yes_no(self, top, marked):
        """Independently fictional questionnaire page/answer fixture."""
        self.box(200, top, marked in ('Yes', 'both'))
        self.box(400, top, marked in ('No', 'both'))
        self.text(('Sim', 200), ('Não', 400), top=top)

    def pages(self):
        return [(self.image, self.words)]

def _q(qid='q1', anchor='trabalhou\\s+sem', **extra):
    return {'id': qid, 'anchor': anchor, 'fact_key': f'applicant.part9.{qid}', 'options': YES_NO, **extra}

def test_marked_no_is_read_as_no():
    page = Page()
    page.text(('Você', 50), ('trabalhou', 120), ('sem', 250), ('autorização?', 300), top=40)
    page.yes_no(top=90, marked='No')
    result = read_questionnaire(page.pages(), [_q()])
    assert result.unread == {}
    assert [(a.fact_key, a.reading.chosen) for a in result.answers] == [('applicant.part9.q1', 'No')]
    fields = result.extracted_fields()
    assert fields[0].normalized_value == 'No'
    assert fields[0].confidence > 0.5

def test_marked_yes_is_read_as_yes():
    page = Page()
    page.text(('trabalhou', 120), ('sem', 250), top=40)
    page.yes_no(top=90, marked='Yes')
    result = read_questionnaire(page.pages(), [_q()])
    assert result.answers[0].reading.chosen == 'Yes'

@pytest.mark.parametrize('marked', [None, 'both'])
def test_blank_or_double_marked_is_unread_never_guessed(marked):
    page = Page()
    page.text(('trabalhou', 120), ('sem', 250), top=40)
    page.yes_no(top=90, marked=marked)
    result = read_questionnaire(page.pages(), [_q()])
    assert result.answers == []
    assert 'q1' in result.unread

def test_nao_inside_question_text_is_not_taken_for_the_answer_label():
    page = Page()
    page.box(300, 40, marked=True)
    page.text(('Visitante', 50), ('J', 250), ('Não', 300), ('Imigrante', 360), top=40)
    page.yes_no(top=90, marked='Yes')
    result = read_questionnaire(page.pages(), [_q(anchor='Visitante')])
    assert result.answers[0].reading.chosen == 'Yes'
    no_option = result.answers[0].reading.options[1]
    assert no_option.hit.word.top == 90

def test_question_not_found_is_reported_unread():
    page = Page()
    page.yes_no(top=90, marked='No')
    result = read_questionnaire(page.pages(), [_q(anchor='nothing\\s+like\\s+this')])
    assert result.unread == {'q1': 'question text not found on any page'}

def test_label_search_stops_at_the_next_question():
    page = Page()
    page.text(('primeira', 50), ('pergunta', 200), top=40)
    page.text(('segunda', 50), ('pergunta', 200), top=90)
    page.yes_no(top=140, marked='No')
    questions = [_q('q1', anchor='primeira'), _q('q2', anchor='segunda')]
    result = read_questionnaire(page.pages(), questions)
    assert 'q1' in result.unread
    assert [(a.question_id, a.reading.chosen) for a in result.answers] == [('q2', 'No')]

def test_box_glued_to_label_in_one_ocr_token_is_located_by_char_offset():
    page = Page()
    page.text(('trabalhou', 120), ('sem', 250), top=40)
    page.box(200, 90, marked=False)
    page.box(400, 90, marked=True)
    page.text(('Sim', 200), top=90)
    page.words.append(OcrWord('(><)Não', 352, 90, 12 * 7, H, 60.0, (1, 1, 99)))
    result = read_questionnaire(page.pages(), [_q()])
    assert result.answers[0].reading.chosen == 'No'

def test_multi_choice_returns_every_marked_option():
    page = Page(height=400)
    page.text(('Raça:', 50), top=20)
    for i, (label, marked) in enumerate([('Branco', True), ('Asiatico', False), ('Preto', True)]):
        top = 70 + i * 50
        page.box(120, top, marked)
        page.text((label, 120), top=top)
    question = {'id': 'race', 'anchor': 'Ra\\S{1,2}:', 'fact_key': 'applicant.race', 'kind': 'multi', 'options': [['White', 'Branco'], ['Asian', 'Asi\\S*tico'], ['Black', 'Preto']]}
    result = read_questionnaire(page.pages(), [question])
    assert sorted((a.reading.chosen for a in result.answers)) == ['Black', 'White']

def test_options_can_span_a_page_break():
    first, second = (Page(), Page())
    first.text(('Estado', 50), ('civil', 150), top=40)
    first.box(120, 90, marked=False)
    first.text(('Solteiro', 120), top=90)
    second.box(120, 60, marked=True)
    second.text(('Casamento', 120), ('Anulado', 250), top=60)
    question = {'id': 'marital_status', 'anchor': 'Estado\\s+civil', 'fact_key': 'applicant.marital_status', 'options': [['Single', 'Solteiro'], ['Marriage Annulled', 'Casamento\\s+Anulado']]}
    result = read_questionnaire(first.pages() + second.pages(), [question])
    assert result.answers[0].reading.chosen == 'Marriage Annulled'
    assert result.answers[0].reading.options[1].hit.page == 1

def test_parse_tsv_keeps_word_rows_only():
    tsv = 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n4\t1\t1\t1\t1\t0\t10\t20\t300\t25\t-1\t\n5\t1\t1\t1\t1\t1\t10\t20\t50\t25\t96.2\t(><)Não\n5\t1\t1\t1\t1\t2\t70\t20\t10\t25\t95.0\t \n'
    words = parse_tsv(tsv)
    assert words == [OcrWord('(><)Não', 10, 20, 50, 25, 96.2, (1, 1, 1))]

def test_questionnaire_map_is_well_formed():
    import re
    questions = load_questionnaire_map(_QMAP)
    ids = [q['id'] for q in questions]
    assert len(ids) == len(set(ids))
    for q in questions:
        re.compile(q['anchor'])
        assert len(q['options']) >= 2
        for _, label in q['options']:
            re.compile(label)

def test_fictional_questionnaire_matches_verified_answers():
    page = Page()
    page.text(('Exemplo', 120), ('de teste', 250), top=40)
    page.yes_no(top=90, marked='Yes')
    result = read_questionnaire(page.pages(), [_q(qid='fictional_demo', anchor='Exemplo')])
    assert result.unread == {}
    assert {answer.question_id: answer.reading.chosen for answer in result.answers} == {'fictional_demo': 'Yes'}

def _handwriting_into_the_no_box():
    """Independently fictional questionnaire page/answer fixture."""
    page = Page()
    page.text(('trabalhou', 120), ('sem', 250), top=40)
    page.box(200, 90, marked=True)
    page.box(400, 90, marked=False)
    for x in range(330, 400, 6):
        page.draw.line((x, 88, x + 4, 116), fill=0, width=3)
    page.text(('Sim', 200), ('Não', 400), top=90)
    return page

def test_readers_disagreeing_goes_to_a_human():
    page = _handwriting_into_the_no_box()
    result = read_questionnaire(page.pages(), [_q()], model_call=lambda *a: {'marked': [1]})
    assert result.answers == []
    assert 'disagree' in result.unread['q1']
    assert result.evidence['q1']['suggest'] == ['Yes']

def test_readers_agreeing_is_accepted():
    page = Page()
    page.text(('trabalhou', 120), ('sem', 250), top=40)
    page.yes_no(top=90, marked='No')
    result = read_questionnaire(page.pages(), [_q()], model_call=lambda *a: {'marked': [2]})
    assert [a.reading.chosen for a in result.answers] == ['No']
    assert result.evidence['q1']['check'] == 'both readers agree'

def test_both_readers_see_nothing_means_left_blank():
    page = Page()
    page.text(('trabalhou', 120), ('sem', 250), top=40)
    page.yes_no(top=90, marked=None)
    result = read_questionnaire(page.pages(), [_q()], model_call=lambda *a: {'marked': []})
    assert 'left it blank' in result.unread['q1']

def test_vision_failure_keeps_the_ink_reading_and_says_so():
    page = Page()
    page.text(('trabalhou', 120), ('sem', 250), top=40)
    page.yes_no(top=90, marked='No')

    def broken(*a):
        raise RuntimeError('ollama down')
    result = read_questionnaire(page.pages(), [_q()], model_call=broken)
    assert [a.reading.chosen for a in result.answers] == ['No']
    assert 'box darkness only' in result.evidence['q1']['check']
