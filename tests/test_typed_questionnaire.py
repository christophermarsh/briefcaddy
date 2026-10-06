"""Fictional fixture helper."""
from pathlib import Path
from pypdf import PdfWriter
from pypdf.annotations import FreeText
from pypdf.generic import NameObject
from classify.ocr import OcrWord
from questionnaire.checkboxes import LabelHit, OptionInk
from questionnaire.handwriting import FieldReading, _typed_in, check_against_typed, date_order, read_typed
from questionnaire.pages import TypedAnnotation, page_images, typed_annotations
from questionnaire.reader import judge_typed
from questionnaire.template import is_blank_template

def _blank_template(path: Path, typed: list[tuple[str, tuple]] | None=None) -> Path:
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    content = 'BT /F1 10 Tf 50 750 Td ' + ' '.join((f'(Label {i}: ______________________) Tj 0 -20 Td' for i in range(25))) + ' ET'
    from pypdf.generic import DecodedStreamObject, DictionaryObject
    stream = DecodedStreamObject()
    stream.set_data(content.encode())
    page = writer.pages[0]
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    page[NameObject('/Contents')] = writer._add_object(stream)
    for text, rect in typed or []:
        annot = FreeText(text=text, rect=rect)
        writer.add_annotation(page_number=0, annotation=annot)
    with open(path, 'wb') as fh:
        writer.write(fh)
    return path

def test_template_with_typed_answers_is_not_blank(tmp_path):
    assert is_blank_template(_blank_template(tmp_path / 'blank.pdf')) is True
    filled = _blank_template(tmp_path / 'typed.pdf', [('X', (100, 700, 120, 715)), ('Trabalhar', (200, 600, 300, 615))])
    assert is_blank_template(filled) is False
    typed = typed_annotations(filled)
    assert [t.text for t in typed] == ['X', 'Trabalhar']
    assert typed[0].box[1] < typed[1].box[1]

def test_typed_page_renders_with_its_annotations(tmp_path):
    pdf = _blank_template(tmp_path / 'typed.pdf', [('XXXXXXXX', (100, 700, 300, 740))])
    image = page_images(pdf)[0]
    assert image.width == 1700
    box = typed_annotations(pdf)[0].box
    region = image.crop(box)
    assert sum(region.histogram()[:128]) > 0

def _option(value, x, y, page=0):
    word = OcrWord('Sim', x, y, 40, 23, 90.0, (1, 1, 1))
    return OptionInk(value, LabelHit(page, word, 0), 0.0, (x - 70, y - 5, x - 5, y + 28))

def test_one_typed_x_counts_for_the_nearest_box_only():
    single, married = (_option('Single', 200, 1300), _option('Married', 200, 1340))
    x_on_single = TypedAnnotation(0, (140, 1300, 170, 1325), 'X')
    readings = judge_typed([single, married], [x_on_single], multi=False)
    assert [r.chosen for r in readings] == ['Single']
    assert readings[0].confidence == 0.99

def test_no_typed_x_means_blank_not_a_guess():
    readings = judge_typed([_option('Yes', 200, 500), _option('No', 400, 500)], [], multi=False)
    assert readings[0].chosen is None and 'left it blank' in readings[0].reason

def test_typed_answer_goes_to_the_nearest_printed_line():
    words = [OcrWord('Cor', 119, 1600, 40, 22, 90, (1, 1, 1)), OcrWord('Olhos:', 222, 1600, 80, 22, 90, (1, 1, 1)), OcrWord('Cor', 119, 1640, 40, 22, 90, (1, 1, 2)), OcrWord('Cabelo:', 211, 1640, 90, 22, 90, (1, 1, 2))]
    typed = [TypedAnnotation(0, (300, 1596, 487, 1630), 'Castanho'), TypedAnnotation(0, (304, 1637, 489, 1671), 'Preto')]
    hair_crop = (301, 1560, 1700, 1670)
    assert _typed_in(typed, 0, hair_crop, words=words, anchor_center=1651, single_line=True) == ['Preto']

def test_model_values_must_be_made_of_typed_tokens():
    typed = ['12', 'Março', '1970']
    ok = FieldReading('mother_dob', 'ok', {'value': '1970-03-12'}, [{'value': '12 de Março de 1970'}], '')
    assert check_against_typed(ok, typed).status == 'ok'
    wrong = FieldReading('hair_color', 'ok', {'value': 'Brown'}, [{'value': 'Castanho'}], '')
    assert check_against_typed(wrong, ['Preto']).status == 'disagree'

def test_single_value_typed_answers_need_no_model():
    spec_date = {'id': 'mother_dob', 'kind': 'date'}
    assert read_typed(spec_date, ['12', 'Março', '1970']).values == {'value': '1970-03-12'}
    spec_weight = {'id': 'weight', 'kind': 'weight'}
    assert read_typed(spec_weight, ['70 kl']).values == {'value': '154'}
    spec_hair = {'id': 'hair_color', 'kind': 'hair_color'}
    assert read_typed(spec_hair, ['Preto']).values == {'value': 'Black'}

def test_client_who_writes_month_first_is_detected_from_their_own_dates():
    readings = [FieldReading('a', 'ok', reads=[{'date_from': '04 / 23 / 2018', 'date_to': '09 / 19 / 2020'}]), FieldReading('b', 'ok', reads=[{'date_from': '04 / 08 / 2019'}])]
    specs = {'a': {'kind': 'foreign_address'}, 'b': {'kind': 'address_history'}}
    assert date_order(readings, specs) == ('mdy', 0, 2)
    br = [FieldReading('a', 'ok', reads=[{'date_from': '23 / 04 / 2018'}])]
    assert date_order(br, {'a': {'kind': 'address_history'}})[0] == 'dmy'

def test_rendering_is_safe_from_many_threads_at_once(tmp_path):
    import threading
    pdf = _blank_template(tmp_path / 'typed.pdf', [('X', (100, 700, 120, 715))])
    results, errors = ([], [])

    def render():
        try:
            results.append(page_images(pdf)[0].size)
        except Exception as exc:
            errors.append(exc)
    threads = [threading.Thread(target=render) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and results == [(1700, 2200)] * 8
