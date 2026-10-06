"""Fictional adverse cases for parent boundaries and recognition selection."""
from types import SimpleNamespace
from extract.birth_certificate import _filiation_entries

PAIR = "ANA FICCAOO DOS FICCAOK, natural de Contagem - MG\nROSA FICCAOG DA FICCAOB, natural de Belo Horizonte - MG"

def test_adjacent_reordered_pair_requires_actual_parent_evidence():
    assert _filiation_entries(PAIR + "\nFILIACAO\nREGISTRO CIVIL") == []


def test_witness_pair_immediately_before_empty_filiation_is_not_parents():
    assert _filiation_entries("TESTEMUNHAS\n" + PAIR + "\nFILIACAO\nAVOS") == []


def test_parent_versions_reject_many_to_one_fuzzy_match():
    from extract.birth_certificate import _merge_versions
    # Both original spellings match MATEUS SILVA; the unrelated second name
    # must not supply a missing second parent via the same candidate.
    assert _merge_versions([
        [("MATEUS SILVA", ""), ("MATEUS SULVA", "")],
        [("MATEUS SILVA", "CONTAGEM, MINAS GERAIS"), ("MARIA FICCAOB", "")],
    ]) == []


def test_parent_versions_reject_multiple_complete_fuzzy_pairings():
    from extract.birth_certificate import _merge_versions
    assert _merge_versions([
        [("MATEUS SILVA", ""), ("MATEUS SULVA", "")],
        [("MATEUS SILVA", ""), ("MATEUS SELVA", "")],
    ]) == []


def test_parent_versions_pair_reordered_translation_one_to_one():
    from extract.birth_certificate import _merge_versions
    assert _merge_versions([
        [("MATEUS SILVA", ""), ("MARIA FICCAOB", "")],
        [("MARIA FICCAOB", "CURITIBA, PARANA"), ("MATEUS SILVA", "CONTAGEM, MINAS GERAIS")],
    ]) == [("MATEUS SILVA", "CONTAGEM, MINAS GERAIS"), ("MARIA FICCAOB", "CURITIBA, PARANA")]


def test_unmatched_noisy_parent_version_stays_unresolved():
    from extract.birth_certificate import _merge_versions
    assert _merge_versions([
        [("FICCAOL FICCAOG DA FICCAOB", ""), ("ANA FICCAOO DOS FICCAOK", "")],
        [("LROSA FICCAOG DA FICCAOB", ""), ("ANA FICCAOO DOS FICCAOK", "")],
    ]) == []

def test_unrelated_witness_rows_do_not_override_parent_block():
    text = "TESTEMUNHAS\n" + PAIR + "\nDATA DE NASCIMENTO\nFILIACAO\nJOAO SILVA, natural de Londrina - PR\nMARIA SANTOS, natural de Curitiba - PR\nAVOS"
    assert [name for name, _ in _filiation_entries(text)] == ["MATEUS SILVA", "MARIA SANTOS"]

def test_distant_rows_do_not_become_parent_evidence():
    assert _filiation_entries(PAIR + "\nDATA DE NASCIMENTO\nFILIACAO\nREGISTRO CIVIL") == []

def test_conflicting_parent_versions_are_not_silently_selected():
    text = "FILIACAO\n" + PAIR + "\nAVOS\nFILIATION\nJOAO SILVA, natural de Londrina - PR\nMARIA SANTOS, natural de Curitiba - PR\nGRANDPARENTS"
    assert _filiation_entries(text) == []

def test_matching_translation_preserves_original_parent_order():
    text = "FILIACAO\n" + PAIR + "\nAVOS\nFILIATION\n" + PAIR + "\nGRANDPARENTS"
    assert [name for name, _ in _filiation_entries(text)] == ["ANA FICCAOO DOS FICCAOK", "FICCAOL FICCAOG DA FICCAOB"]

def test_rotation_continues_past_readable_unrecognized_text(monkeypatch):
    from PIL import Image
    from classify import classifier, ocr
    garbage = "DATE PLACE NAME THE AND OF " * 10
    assert classifier.readability(garbage) >= 8
    assert classifier.classify_text(garbage).doc_type == "unclassified"
    results = iter([garbage, garbage, "YOUR SOCIAL SECURITY CARD\nVALID FOR WORK ONLY WITH DHS AUTHORIZATION"])
    calls = []
    def read(image):
        calls.append(image)
        return next(results)
    monkeypatch.setattr(ocr, "ocr_image", read)
    monkeypatch.setattr(classifier, "_page_images", lambda _: [SimpleNamespace(image=Image.new("RGB", (40, 60)))])
    assert classifier.classify_text(classifier._ocr_page_images(object())).doc_type == "ssn_card"
    assert len(calls) == 3


def test_pixel_parent_recovery_requires_actual_filiation_label(tmp_path, monkeypatch):
    from pathlib import Path
    from PIL import Image
    from classify import ocr
    from classify.ocr import OcrWord
    image = Image.new("RGB", (1200, 1600), "white")
    rows = []
    for line, y, name in ((1, 400, "ANA FICCAOO DOS FICCAOK"), (2, 450, "FICCAOL FICCAOG DA FICCAOB")):
        for index, token in enumerate((name + ", natural de Contagem - MG").split()):
            rows.append(OcrWord(token, 50 + index * 70, y, 65, 20, 95, (1, 1, line)))
    monkeypatch.setattr(ocr, "ocr_words", lambda *a, **k: rows)
    def label(command, **kwargs):
        Path(command[2]).with_suffix(".txt").write_text("FILIACAO", encoding="utf-8")
    monkeypatch.setattr(ocr.subprocess, "run", label)
    block = ocr._filiation_pixel_block(image, "tesseract", tmp_path)
    assert block and len(_filiation_entries(block)) == 2
    def not_parent(command, **kwargs):
        Path(command[2]).with_suffix(".txt").write_text("TESTEMUNHAS", encoding="utf-8")
    monkeypatch.setattr(ocr.subprocess, "run", not_parent)
    assert ocr._filiation_pixel_block(image, "tesseract", tmp_path) is None


def test_pixel_parent_recovery_rejects_intervening_section(tmp_path, monkeypatch):
    from pathlib import Path
    from PIL import Image
    from classify import ocr
    from classify.ocr import OcrWord
    rows = []
    for line, y, name in ((1, 400, "MATEUS SILVA"), (2, 450, "MARIA SANTOS")):
        for index, token in enumerate((name + ", natural de Contagem - MG").split()):
            rows.append(OcrWord(token, 50 + index * 70, y, 65, 20, 95, (1, 1, line)))
    monkeypatch.setattr(ocr, "ocr_words", lambda *a, **k: rows)
    def label(command, **kwargs):
        Path(command[2]).with_suffix(".txt").write_text("FILIACAO\nTESTEMUNHAS", encoding="utf-8")
    monkeypatch.setattr(ocr.subprocess, "run", label)
    assert ocr._filiation_pixel_block(Image.new("RGB", (1200, 1600)), "tesseract", tmp_path) is None


def test_pixel_parent_recovery_handles_tilted_overlapping_line_boxes(tmp_path, monkeypatch):
    from pathlib import Path
    from PIL import Image
    from classify import ocr
    from classify.ocr import OcrWord
    rows = []
    for line, y, name in ((1, 400, "MATEUS SILVA"), (2, 415, "MARIA SANTOS")):
        for index, token in enumerate((name + ", natural de Contagem - MG").split()):
            rows.append(OcrWord(token, 50 + index * 70, y, 65, 28, 95, (1, 1, line)))
    monkeypatch.setattr(ocr, "ocr_words", lambda *a, **k: rows)
    def label(command, **kwargs):
        Path(command[2]).with_suffix(".txt").write_text("FILIACAO", encoding="utf-8")
    monkeypatch.setattr(ocr.subprocess, "run", label)
    assert ocr._filiation_pixel_block(Image.new("RGB", (1200, 1600)), "tesseract", tmp_path)


def test_parent_state_only_birthplace_does_not_borrow_residence_city():
    from extract.birth_certificate import _birthplace_of
    assert _birthplace_of("MATEUS SILVA, natural de Minas Gerais, residente em Contagem - MG") == ""
    assert _birthplace_of("MATEUS SILVA, natural de Contagem - MG, residente em Curitiba - PR") == "CONTAGEM, MINAS GERAIS"
