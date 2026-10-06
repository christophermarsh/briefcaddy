"""Fictional closing-letter drafts and current wording approval, no legal/language signoff."""
from copy import deepcopy
import json
import os

import pytest

import engagement
from rules import approval
from test_case_end import firm  # noqa: F401 -- canonical fixture

# Imported fixture names deliberately match arguments.
# ruff: noqa: F811

EXPECTED = {
    'en': 'only after separate, current attorney approval',
    'pt': 'somente depois de uma aprovação atual e separada do advogado',
    'es': 'solo después de una aprobación actual y separada del abogado',
    'ht': 'sèlman apre yon avoka bay yon apwobasyon separe ki valab kounye a',
}
OLD = {
    'en': 'What we keep: we keep a copy of your file [kept until]. After that, the file may be destroyed without further notice to you.',
    'pt': 'O que guardamos: guardamos uma cópia do seu arquivo [kept until]. Depois disso, o arquivo pode ser destruído sem novo aviso a você.',
    'es': 'Lo que guardamos: guardamos una copia de su expediente [kept until]. Después, el expediente puede ser destruido sin otro aviso.',
    'ht': 'Sa nou kenbe: nou kenbe yon kopi dosye ou [kept until]. Apre sa, yo ka detwi dosye a san yo pa avèti ou ankò.',
}


NEW = {'en': 'What we keep: we keep a copy of your file [kept until]. The file may be destroyed only after separate, current attorney approval and resolution of preservation duties, original-document protections, court orders and other applicable requirements.', 'pt': 'O que guardamos: guardamos uma cópia do seu arquivo [kept until]. O arquivo pode ser destruído somente depois de uma aprovação atual e separada do advogado e da resolução de deveres de preservação, proteções de documentos originais, ordens judiciais e outros requisitos aplicáveis.', 'es': 'Lo que guardamos: guardamos una copia de su expediente [kept until]. El expediente puede destruirse solo después de una aprobación actual y separada del abogado y de resolver los deberes de conservación, las protecciones de documentos originales, las órdenes judiciales y otros requisitos aplicables.', 'ht': 'Sa nou kenbe: nou kenbe yon kopi dosye ou [kept until]. Dosye a kapab detwi sèlman apre yon avoka bay yon apwobasyon separe ki valab kounye a, epi apre obligasyon pou prezève dosye a, pwoteksyon dokiman orijinal yo, lòd tribinal ak lòt kondisyon ki aplikab yo fin rezoud.'}

@pytest.mark.parametrize('language', engagement.LANGS)
def test_actual_closing_letter_requires_separate_current_destruction_review(firm, language):
    firm.store.update_profile('case-ana', language=language)
    result = engagement.end(firm.case, 'closed', 'Fictional Attorney', 'attorney', reason='Fictional operational close.', portal_root=firm.portal)
    letter = next(row for row in engagement.read(firm.case)['letters'] if row['kind'] == 'closing')
    text = ' '.join(letter['texts'][language])
    assert EXPECTED[language] in text
    assert OLD[language].split('[kept until]. ')[1] not in text
    assert result['file_policy']['destruction']['state'] == 'held'


def test_shipped_template_change_requires_new_approval_and_preserves_old_letter(firm, tmp_path, monkeypatch):
    # A reload of the exact shipped source simulates a controlled draft release.
    path = tmp_path / 'fictional-shipped-letter-source.json'
    revised = engagement.shipped()
    revised["version"] = 1
    for lg in engagement.LANGS:
        revised["documents"]["closing"]["texts"][lg][2] = NEW[lg]
    previous = deepcopy(revised)
    previous['version'] = 0
    previous['documents']['closing']['texts'] = {lg: [OLD[lg] if i == 2 else text for i, text in enumerate(paras)]
                                                for lg, paras in previous['documents']['closing']['texts'].items()}
    previous_bytes = json.dumps(previous, ensure_ascii=False).encode()
    revised_bytes = json.dumps(revised, ensure_ascii=False).encode()
    size = max(len(previous_bytes), len(revised_bytes)) + 32
    path.write_bytes(previous_bytes + b" " * (size - len(previous_bytes)))
    original_stat = path.stat()
    monkeypatch.setattr(engagement, 'SHIPPED', path)
    approval._catalog.cache_clear()  # fixture begins a fresh previous-source runtime
    approval.approve(engagement.PRACTICE_ID, 'Fictional Previous Wording Reviewer', 'attorney')
    engagement.end(firm.case, 'closed', 'Fictional Attorney', 'attorney', reason='Fictional earlier close.', portal_root=firm.portal)
    old = next(row for row in engagement.read(firm.case)['letters'] if row['kind'] == 'closing')
    old_copy = deepcopy(old)
    pdf = engagement.pdf_path(firm.case, old)
    old_pdf = pdf.read_bytes()
    history = deepcopy(approval.history()[engagement.PRACTICE_ID])
    engagement.reopen(firm.case, 'Fictional resumed matter', 'Fictional Attorney', 'attorney', portal_root=firm.portal)
    path.write_bytes(revised_bytes + b" " * (size - len(revised_bytes)))
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert path.stat().st_size == original_stat.st_size and path.stat().st_mtime_ns == original_stat.st_mtime_ns
    # No cache clear here: current shipped source itself must invalidate approval.
    assert not engagement.approved()
    assert approval.history()[engagement.PRACTICE_ID] == history
    assert next(row for row in engagement.read(firm.case)['letters'] if row['id'] == old['id']) == old_copy
    assert pdf.read_bytes() == old_pdf
    with pytest.raises(ValueError, match='approv'):
        engagement.end(firm.case, 'closed', 'Fictional Attorney', 'attorney', reason='Fictional new close.', portal_root=firm.portal)
    approval.approve(engagement.PRACTICE_ID, 'Fictional Current Wording Reviewer', 'attorney')
    engagement.end(firm.case, 'closed', 'Fictional Attorney', 'attorney', reason='Fictional new close.', portal_root=firm.portal)
    new = engagement.read(firm.case)['letters'][-1]
    assert new['wording_hash'] != old['wording_hash'] and EXPECTED['en'] in ' '.join(new['texts']['en'])
    assert approval.history()[engagement.PRACTICE_ID][:-1] == history
    assert pdf.read_bytes() == old_pdf


@pytest.mark.parametrize('fault', ['missing', 'malformed'])
def test_unavailable_shipped_source_never_reuses_old_wording_approval(firm, tmp_path, monkeypatch, fault):
    path = tmp_path / 'fictional-shipped-source.json'
    path.write_text(json.dumps(engagement.shipped(), ensure_ascii=False))
    monkeypatch.setattr(engagement, 'SHIPPED', path)
    approval._catalog.cache_clear()
    approval.approve(engagement.PRACTICE_ID, 'Fictional Current Wording Reviewer', 'attorney')
    result = engagement.end(firm.case, 'closed', 'Fictional Attorney', 'attorney', reason='Fictional earlier close.', portal_root=firm.portal)
    old = result['letters'][-1]
    pdf = engagement.pdf_path(firm.case, old)
    old_pdf = pdf.read_bytes()
    engagement.reopen(firm.case, 'Fictional resumed matter', 'Fictional Attorney', 'attorney', portal_root=firm.portal)
    before = (firm.case / engagement.FILE).read_bytes()
    history = deepcopy(approval.history()[engagement.PRACTICE_ID])
    if fault == 'missing':
        path.unlink()
    else:
        path.write_text('{fictional malformed shipped source')
    with pytest.raises((ValueError, OSError)):
        engagement.approved()
    with pytest.raises((ValueError, OSError)):
        engagement.end(firm.case, 'closed', 'Fictional Attorney', 'attorney', reason='Fictional new close.', portal_root=firm.portal)
    assert (firm.case / engagement.FILE).read_bytes() == before and pdf.read_bytes() == old_pdf
    assert approval.history()[engagement.PRACTICE_ID] == history
