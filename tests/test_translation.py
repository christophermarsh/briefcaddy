"""Foreign-language documents in a filing (src/translation.py, docs/design_plan.md Part 5): the side-by-side translation
and the certificate of translation, the Settings translators, signing, the packet accepting the signed copy, Haitian
Creole routed to a human, a missing language model said in words, and the summary built from the extractors' fields.

A made-up Portuguese birth certificate (Ana Clara Exemplo Souza). The offline translator is replaced by a fake: the real
one is the same single call (portal/questions.py translate_to_english) and its output is not what is under test.
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

import documents
import packet
import settings
import translation
from fill import fill_pdf
from portal.demo import document_pdf
import schema_path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = schema_path.path("template", "i485")
FULL_SCHEMA = packet.load_schema()
ROW_DONE = {"summary": {"name": "ANA CLARA EXEMPLO SOUZA", "a_number": "A099000001", "dob": "2006-01-02"}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}

CERTIDAO = ["REPUBLICA FEDERATIVA DO BRASIL", "CERTIDAO DE NASCIMENTO", "EXEMPLO: DEMONSTRATION DOCUMENT", "NOME: ANA CLARA EXEMPLO SOUZA",
            "DATA DE NASCIMENTO: 14 de marco de 2006", "FILIACAO: JOSE EXEMPLO SOUZA e MARIA EXEMPLO LIMA", "NATURALIDADE: SOROCABA - SP",
            "CARTORIO DO REGISTRO CIVIL, MATRICULA 123456"]
ENGLISH = {"REPUBLICA FEDERATIVA DO BRASIL": "FEDERATIVE REPUBLIC OF BRAZIL", "CERTIDAO DE NASCIMENTO": "BIRTH CERTIFICATE",
           "EXEMPLO: DEMONSTRATION DOCUMENT": "EXEMPLO: DEMONSTRATION DOCUMENT", "NOME: ANA CLARA EXEMPLO SOUZA": "NAME: ANA CLARA EXEMPLO SOUZA",
           "DATA DE NASCIMENTO: 14 de marco de 2006": "DATE OF BIRTH: 14 March 2006",
           "FILIACAO: JOSE EXEMPLO SOUZA e MARIA EXEMPLO LIMA": "PARENTAGE: JOSE EXEMPLO SOUZA and MARIA EXEMPLO LIMA",
           "NATURALIDADE: SOROCABA - SP": "PLACE OF ORIGIN: SOROCABA - SP", "CARTORIO DO REGISTRO CIVIL, MATRICULA 123456": "CIVIL REGISTRY OFFICE, REGISTRATION 123456"}


@pytest.fixture
def firm(tmp_path, monkeypatch):
    """This test's own settings file, and a fake offline translator that counts its calls."""
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    calls = []

    def fake(text, lang):
        calls.append((text, lang))
        return ENGLISH.get(text, f"[en] {text}")

    import portal.questions as questions
    from classify import translate

    monkeypatch.setattr(questions, "translate_to_english", fake)
    monkeypatch.setattr(translate, "installed_language_codes", lambda: {"pt", "es", "fr"})
    return SimpleNamespace(calls=calls)


@pytest.fixture(scope="module")
def filled(tmp_path_factory):
    path = tmp_path_factory.mktemp("form") / "i485_filled.pdf"
    fill_pdf(TEMPLATE, {}, path)
    return path


def _case(tmp_path, filled, lines=CERTIDAO, language="pt", fields=True):
    """A processed case: the birth certificate as a PDF with a text layer, its record, the fact graph the reader filled."""
    from factgraph import FactGraph

    source, d = tmp_path / "source", tmp_path / "case"
    source.mkdir()
    d.mkdir()
    (source / "certidao.pdf").write_bytes(document_pdf(lines))
    (d / "meta.json").write_text(json.dumps({"client_id": "t-case", "source_folder": str(source), "classifications": {"certidao.pdf": "birth_certificate"}}))
    (d / "i485_filled.pdf").write_bytes(filled.read_bytes())
    text = "\n".join(lines)
    built = documents.build(source, {"certidao.pdf": SimpleNamespace(doc_type="birth_certificate", confidence=0.9)}, {}, texts={"certidao.pdf": text})
    built["documents"][0]["language"] = language
    documents.save(d, built)
    documents.set_person(d, built["documents"][0]["id"], "applicant", "Paulo Paralegal", "paralegal")  # whose it is: a reviewer's word
    graph = FactGraph("t-case")
    if fields:
        graph.add_source("applicant.birth_certificate_name", "certidao.pdf", "birth_certificate", "ANA CLARA EXEMPLO SOUZA", "ANA CLARA EXEMPLO SOUZA", 0.8)
        graph.add_source("applicant.dob", "certidao.pdf", "birth_certificate", "14/03/2006", "2006-03-14", 0.9)
        graph.add_source("applicant.birth_city", "certidao.pdf", "birth_certificate", "SOROCABA - SP", "SOROCABA", 0.9)
        graph.add_source("applicant.birth_cert.parent_a_name", "certidao.pdf", "birth_certificate", "JOSE EXEMPLO SOUZA", "JOSE EXEMPLO SOUZA", 0.85)
        graph.add_source("applicant.birth_cert.parent_b_name", "certidao.pdf", "birth_certificate", "MARIA EXEMPLO LIMA", "MARIA EXEMPLO LIMA", 0.85)
    graph.save(d / "fact_graph.json")
    return d


def _record(d) -> dict:
    return documents.load(d)["documents"][0]


def _text(pdf: Path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _translator(languages=("pt",), name="Marta Tradutora Exemplo", kind="outside", **extra):
    return settings.add_translator("Ana Attorney", name, list(languages), kind, **extra)


# --- the regulation -----------------------------------------------------------------------------------------------------


def test_the_certificate_says_both_things_the_regulation_asks():
    # 8 CFR 103.2(b)(3), copied from eCFR on 10/02/2026: complete and accurate, and competent to translate into English
    assert translation.REGULATION.startswith("Any document containing foreign language submitted to USCIS shall be accompanied by a full English")
    for words in ("certified as complete and accurate", "competent to translate from the foreign language into English"):
        assert words in translation.REGULATION
    sentence = translation.CERTIFICATE_WORDING
    assert "competent to translate from {language} into English" in sentence and "is complete and accurate" in sentence
    assert "8 CFR 103.2(b)(3)" in translation.SOURCE and "10/02/2026" in translation.SOURCE


# --- the Settings translators ---------------------------------------------------------------------------------------------


def test_a_translator_is_added_with_who_and_when_and_listed_on_the_settings_page(firm):
    row = _translator(("pt", "es"), kind="firm", competence="Native Portuguese speaker; ten years translating civil records.")
    assert row["id"] == "t1" and row["added_by"] == "Ana Attorney" and re.match(r"\d{4}-\d{2}-\d{2}T", row["added_at"])
    section = next(s for s in settings.specs() if s["id"] == "translators")
    assert [t["name"] for t in section["translators"]] == ["Marta Tradutora Exemplo"]
    assert section["translators"][0]["language_names"] == ["Portuguese", "Spanish"] and section["translators"][0]["kind_name"] == "Works for the firm"
    assert section["fields"] == [] and ["pt", "Portuguese"] in section["translator_languages"]
    second = _translator(("ht",), name="Jean Traducteur Exemple")
    assert second["id"] == "t2"
    settings.remove_translator("t1", "Ana Attorney")
    assert [t["name"] for t in settings.translators()] == ["Jean Traducteur Exemple"]
    assert json.loads(settings.PATH.read_text(encoding="utf-8"))["_removed"][0]["by"] == "Ana Attorney"


def test_a_translator_needs_a_name_a_language_a_kind_and_who(firm):
    with pytest.raises(ValueError, match="Enter your name first"):
        settings.add_translator("", "Marta", ["pt"], "firm")
    with pytest.raises(ValueError, match="full name"):
        settings.add_translator("Ana", "  ", ["pt"], "firm")
    with pytest.raises(ValueError, match="at least one language"):
        settings.add_translator("Ana", "Marta", ["klingon"], "firm")
    with pytest.raises(ValueError, match="firm or is an outside"):
        settings.add_translator("Ana", "Marta", ["pt"], "friend")
    assert settings.translators() == []


# --- the translation and the side-by-side PDF --------------------------------------------------------------------------------


def test_the_machine_translates_once_and_the_side_by_side_pdf_is_a_draft(tmp_path, filled, firm):
    d = _case(tmp_path, filled)
    rid = _record(d)["id"]
    e = translation.make(d, rid, "Paulo Paralegal")
    assert e["state"] == "draft" and e["how"] == "machine" and e["made_by"]["who"] == "Paulo Paralegal" and "Argos Translate" in e["engine"]
    record = _record(d)
    assert record["translated"].startswith("FEDERATIVE REPUBLIC OF BRAZIL\nBIRTH CERTIFICATE") and "DATE OF BIRTH: 14 March 2006" in record["translated"]
    done = len(firm.calls)
    assert done == len(CERTIDAO) and {lang for _t, lang in firm.calls} == {"pt"}
    translation.make(d, rid, "Paulo Paralegal")  # done once: not translated again
    assert len(firm.calls) == done

    pdf = translation.pdf_path(d, rid)
    reader = PdfReader(str(pdf))
    assert len(reader.pages) == 2  # one side-by-side sheet, then the certificate
    text = _text(pdf)
    flat = _flat(text)
    assert "English translation" in flat and "Document: Birth certificate, in Portuguese" in flat
    assert "Person: ANA CLARA EXEMPLO SOUZA (the client)" in flat and "Translated from Portuguese into English" in flat
    assert "Original, page 1 of 1" in flat and "BIRTH CERTIFICATE" in flat and "DATE OF BIRTH: 14 March 2006" in flat
    assert translation.DRAFT_MACHINE in flat  # "DRAFT machine translation, not for filing until the translator signs"
    assert "DRAFT" in text and translation.DRAFT_CERTIFICATE in flat and "Page 1 of 2" in flat and "Page 2 of 2" in flat
    xobjects = reader.pages[0]["/Resources"].get("/XObject") or {}
    assert len(xobjects) == 1  # the original page, drawn on the left
    kept = json.loads((d / "translations.json").read_text(encoding="utf-8"))["documents"][rid]
    assert kept["status"] == "machine" and kept["made_by"]["who"] == "Paulo Paralegal" and re.match(r"\d{4}-\d{2}-\d{2}T", kept["made_by"]["at"])


def test_names_and_places_the_extractors_found_are_left_as_written(tmp_path, filled, monkeypatch):
    """The small model turns a surname into a word ("EXEMPLO" -> "EXAMPLE"): the names and places the readers found never reach it."""
    import portal.questions as questions
    from classify import translate

    sent = []

    def mangling(text, lang):
        sent.append(text)
        return text.replace("EXEMPLO", "EXAMPLE").replace("DATA DE NASCIMENTO", "DATE OF BIRTH")

    monkeypatch.setattr(questions, "translate_to_english", mangling)
    monkeypatch.setattr(translate, "installed_language_codes", lambda: {"pt"})
    d = _case(tmp_path, filled)
    e = translation.make(d, _record(d)["id"], "Paulo Paralegal")
    assert "NAME: ANA CLARA EXEMPLO SOUZA" not in e["text"] and "ANA CLARA EXEMPLO SOUZA" in e["text"]
    assert "PARENTAGE" not in e["text"] and "JOSE EXEMPLO SOUZA e MARIA EXEMPLO LIMA" in e["text"]  # both parents, as written
    assert "DATE OF BIRTH: 14 de marco de 2006" in e["text"]
    assert not any("ANA CLARA" in line for line in sent if line.startswith("NOME"))  # the model saw a token, never the name
    assert any(re.fullmatch(r"NOME: Zq\d", line) for line in sent)
    # a model that loses a name token: the line stays as written (never retranslated with the name exposed) and is counted
    monkeypatch.setattr(questions, "translate_to_english", lambda text, lang: "gibberish" if "Zq" in text else text.replace("EXEMPLO", "EXAMPLE"))
    (tmp_path / "again").mkdir()
    d2 = _case(tmp_path / "again", filled)
    e = translation.make(d2, _record(d2)["id"], "Paulo Paralegal")
    assert "NOME: ANA CLARA EXEMPLO SOUZA" in e["text"] and "EXAMPLE" not in e["text"] and "gibberish" not in e["text"]
    assert re.match(r"\d+ lines could not be translated and are shown as written", e["problem"])


def test_ten_or_more_names_in_a_line_come_back_each_as_written(monkeypatch):
    names = [f"NOME{chr(65 + i)}{chr(66 + i)}X" for i in range(12)]  # twelve tokens: Zq10 must not become Zq1 and a 0
    line = " ".join(names)
    masked, tokens = translation._masked(line, names)
    assert len(tokens) == 12 and "Zq10" in masked
    import portal.questions as questions

    monkeypatch.setattr(questions, "translate_to_english", lambda text, lang: text.replace("NOME", "NAME"))  # passes the tokens through
    assert translation._translate_line(line, "pt", names) == line


def test_a_longer_document_gets_a_sheet_for_each_page_of_text(tmp_path, filled, firm):
    lines = ["CERTIDAO DE NASCIMENTO", *[f"LINHA {n}: NOME DO REGISTRADO" for n in range(1, 120)]]
    d = _case(tmp_path, filled, lines=lines, fields=False)
    translation.make(d, _record(d)["id"], "Paulo Paralegal")
    reader = PdfReader(str(translation.pdf_path(d, _record(d)["id"])))
    assert len(reader.pages) > 3  # the text runs over several sheets, the one-page original beside only the first
    assert "(continued)" in reader.pages[1].extract_text() and reader.pages[1].extract_text().count("Original") == 1


def test_the_translators_own_words_replace_the_drafts_and_are_kept_with_who(tmp_path, filled, firm):
    d = _case(tmp_path, filled)
    rid = _record(d)["id"]
    translation.make(d, rid, "Paulo Paralegal")
    with pytest.raises(ValueError, match="Type the English translation"):
        translation.set_text(d, rid, "  ", "Marta")
    e = translation.set_text(d, rid, "BIRTH CERTIFICATE\nName: Ana Clara Exemplo Souza", "Marta Tradutora Exemplo")
    assert e["how"] == "typed" and e["corrected_by"]["who"] == "Marta Tradutora Exemplo" and e["typed_by"] is None and e["text"].endswith("Souza")
    e = translation.set_text(d, rid, "BIRTH CERTIFICATE\nName: Ana Clara Exemplo Souza.", "Second Translator")  # the first corrector is not replaced by a typist
    assert e["corrected_by"]["who"] == "Second Translator" and e["typed_by"] is None
    assert _record(d)["translated"].startswith("BIRTH CERTIFICATE")
    assert translation.DRAFT_TYPED in _flat(_text(translation.pdf_path(d, rid)))  # not "machine" any more
    with pytest.raises(ValueError, match="never overwritten|edit it instead"):
        translation.make(d, rid, "Paulo", again=True)
    assert len(firm.calls) == len(CERTIDAO)


def test_the_translation_survives_a_reprocessing(tmp_path, filled, firm):
    d = _case(tmp_path, filled)
    rid = _record(d)["id"]
    translation.make(d, rid, "Paulo Paralegal")
    before = _record(d)["translated"]
    source = Path(json.loads((d / "meta.json").read_text())["source_folder"])
    built = documents.build(source, {"certidao.pdf": SimpleNamespace(doc_type="birth_certificate", confidence=0.9)}, {}, texts={"certidao.pdf": "\n".join(CERTIDAO)})
    assert built["documents"][0]["translated"] is None  # a new run knows nothing of it ...
    documents.save_run(d, built)
    assert _record(d)["translated"] == before  # ... until merged with what was made


# --- the certificate and signing ------------------------------------------------------------------------------------------------


def test_the_certificate_names_the_translator_and_says_what_the_regulation_asks(tmp_path, filled, firm):
    d = _case(tmp_path, filled)
    rid = _record(d)["id"]
    _translator(("pt",), competence="Native Portuguese speaker; ten years translating civil records.", organization="Exemplo Translations", address="1 Example Street, Boston, MA")
    translation.make(d, rid, "Paulo Paralegal")
    with pytest.raises(ValueError, match="Choose a translator"):
        translation.set_translator(d, rid, "t9", "Ana Attorney")
    e = translation.set_translator(d, rid, "t1", "Ana Attorney")
    assert e["translator"]["name"] == "Marta Tradutora Exemplo" and e["translator_set_by"]["who"] == "Ana Attorney"
    reader = PdfReader(str(translation.pdf_path(d, rid)))
    page = _flat(reader.pages[-1].extract_text())
    assert "CERTIFICATE OF TRANSLATION" in page and "8 CFR 103.2(b)(3)" in page
    assert ("I, Marta Tradutora Exemplo, certify that I am competent to translate from Portuguese into English, and that the attached English "
            "translation of the birth certificate of ANA CLARA EXEMPLO SOUZA (the client) is complete and accurate.") in page
    for words in ("Document translated Birth certificate", "Language of the original Portuguese", "Exemplo Translations", "1 Example Street, Boston, MA",
                  "Native Portuguese speaker; ten years translating civil records.", "Translator's signature", "Date signed (MM/DD/YYYY)"):
        assert words in page, words
    assert "DRAFT" in page and "Signed" not in page


def test_the_translator_must_know_the_documents_language(tmp_path, filled, firm):
    d = _case(tmp_path, filled)
    _translator(("es",), name="Sofia Tradutora Exemplo")
    with pytest.raises(ValueError, match="not set up for Portuguese"):
        translation.set_translator(d, _record(d)["id"], "t1", "Ana Attorney")


def test_signing_takes_the_draft_marks_off_and_records_who_and_when(tmp_path, filled, firm):
    d = _case(tmp_path, filled)
    rid = _record(d)["id"]
    with pytest.raises(ValueError, match="no English translation yet"):
        translation.sign(d, rid, "Paulo")
    translation.make(d, rid, "Paulo Paralegal")
    with pytest.raises(ValueError, match="chooses the translator first"):
        translation.sign(d, rid, "Paulo")
    with pytest.raises(ValueError, match="Enter your name"):
        translation.sign(d, rid, "")
    _translator()
    translation.set_translator(d, rid, "t1", "Ana Attorney")
    e = translation.sign(d, rid, "Paulo Paralegal", today=__import__("datetime").date(2026, 10, 2))
    assert e["state"] == "signed" and e["signed"]["by"] == "Paulo Paralegal" and e["signed"]["date"] == "10/02/2026" and e["signed"]["translator"] == "Marta Tradutora Exemplo"
    assert re.match(r"\d{4}-\d{2}-\d{2}T", e["signed"]["at"])
    text = _text(translation.pdf_path(d, rid))
    assert "DRAFT" not in text and "not for filing" not in text
    assert "Signed 10/02/2026" in text and "Signed 10/02/2026 by Marta Tradutora Exemplo" in _flat(text)
    # the translator's correction after signing takes the signature back: they sign the text as it now stands
    e = translation.set_text(d, rid, "BIRTH CERTIFICATE (corrected)", "Marta Tradutora Exemplo")
    assert e["state"] == "draft" and e["signed"] is None and "DRAFT" in _text(translation.pdf_path(d, rid))
    translation.sign(d, rid, "Paulo Paralegal")
    e = translation.reopen(d, rid, "Ana Attorney")
    assert e["state"] == "draft" and "DRAFT" in _text(translation.pdf_path(d, rid))


def test_a_document_nobody_has_placed_cannot_be_signed(tmp_path, filled, firm):
    d = _case(tmp_path, filled, fields=False)
    rid = _record(d)["id"]
    _translator()
    documents.set_person(d, rid, "unknown", "Paulo Paralegal", "paralegal")
    translation.make(d, rid, "Paulo Paralegal")
    translation.set_translator(d, rid, "t1", "Ana Attorney")
    with pytest.raises(ValueError, match="Set whose document this is first"):
        translation.sign(d, rid, "Paulo Paralegal")
    assert "not set yet" in _flat(_text(translation.pdf_path(d, rid))) and "a person not yet set" not in _flat(_text(translation.pdf_path(d, rid)))


def test_changing_whose_document_it_is_takes_the_signature_back_and_keeps_it_in_the_history(tmp_path, filled, firm):
    d = _case(tmp_path, filled)
    rid = _record(d)["id"]
    _translator()
    translation.make(d, rid, "Paulo Paralegal")
    translation.set_translator(d, rid, "t1", "Ana Attorney")
    translation.sign(d, rid, "Paulo Paralegal")
    documents.set_person(d, rid, "spouse", "Paulo Paralegal", "paralegal")
    translation.person_changed(d, rid, "Paulo Paralegal")
    e = translation.entry(d, rid)
    assert e["state"] == "draft" and e["signed"] is None and "DRAFT" in _text(translation.pdf_path(d, rid))
    assert len(e["history"]) == 1 and e["history"][0]["by"] == "Paulo Paralegal" and e["history"][0]["taken_back"]["who"] == "Paulo Paralegal"
    assert e["history"][0]["why"] == "the person the document is about was changed"
    assert "the client's spouse" in _flat(_text(translation.pdf_path(d, rid)))
    translation.sign(d, rid, "Paulo Paralegal")
    translation.reopen(d, rid, "Ana Attorney")
    assert [h["taken_back"]["who"] for h in translation.entry(d, rid)["history"]] == ["Paulo Paralegal", "Ana Attorney"]


def test_a_signature_for_another_person_never_stands(tmp_path, filled, firm):
    d = _case(tmp_path, filled)
    rid = _record(d)["id"]
    _translator()
    translation.make(d, rid, "Paulo Paralegal")
    translation.set_translator(d, rid, "t1", "Ana Attorney")
    translation.sign(d, rid, "Paulo Paralegal")
    documents.set_person(d, rid, "parent", "Paulo Paralegal", "paralegal")  # without the hook: the guard still holds
    assert translation.entry(d, rid)["state"] == "draft" and translation.signed_entries(d, [{"doc": "certidao.pdf", "type": "birth_certificate"}], documents.by_doc(d)) == []


# --- the packet -----------------------------------------------------------------------------------------------------------------


@pytest.fixture
def packet_case(tmp_path, filled, firm, monkeypatch):
    monkeypatch.setattr(packet, "load_schema", lambda: FULL_SCHEMA | {"forms": ["i485"], "cover_letter": False, "index_sheet": True})
    d = _case(tmp_path, filled)
    return d, _record(d)["id"]


def _birth(plan: dict) -> dict:
    return next(ex for ex in plan["exhibits"] if ex["id"] == "birth")


def test_the_packet_asks_for_the_translation_until_it_is_signed_and_then_takes_it_as_the_exhibit(packet_case, firm):
    d, rid = packet_case
    plan = packet.plan(d, ROW_DONE)
    missing = [c["text"] for c in plan["checklist"] if c["kind"] == "missing"]
    assert any("No English translation of the birth certificate of ANA CLARA EXEMPLO SOUZA (Portuguese) yet" in t for t in missing)
    assert not any("No English translation of the birth certificate was found" in t for t in missing)  # the generic line gives way
    assert [t["state"] for t in plan["translations"]] == ["needed"] and plan["translations"][0]["translators"] == []

    _translator()
    translation.make(d, rid, "Paulo Paralegal")
    translation.set_translator(d, rid, "t1", "Ana Attorney")
    plan = packet.plan(d, ROW_DONE)
    assert [f["doc"] for f in _birth(plan)["files"]] == ["certidao.pdf"]  # a DRAFT never goes in
    assert any("is a DRAFT" in c["text"] and c["kind"] == "missing" for c in plan["checklist"])
    assert plan["translations"][0]["state"] == "draft"
    assert plan["translations"][0]["translators"] == [{"id": "t1", "name": "Marta Tradutora Exemplo", "kind_name": "Outside translator"}]

    translation.sign(d, rid, "Paulo Paralegal")
    plan = packet.plan(d, ROW_DONE)
    files = _birth(plan)["files"]
    assert [f["doc"] for f in files] == ["certidao.pdf", f"translation-{rid}.pdf"]  # the original, then its translation and certificate
    assert files[1]["generated"] and files[1]["translation_of"] == "certidao.pdf" and files[1]["label"].startswith("English translation and translator's certificate")
    texts = [c["text"] for c in plan["checklist"]]
    assert not any("No English translation" in t or "is a DRAFT" in t for t in texts)
    assert any(c["kind"] == "sign" and "translator signs the certificate of translation" in c["text"] for c in plan["checklist"])
    assert plan["translations"][0]["state"] == "signed" and plan["translations"][0]["pdf"]


def test_the_built_packet_prints_the_signed_translation_after_the_original(packet_case, firm):
    d, rid = packet_case
    _translator()
    translation.make(d, rid, "Paulo Paralegal")
    translation.set_translator(d, rid, "t1", "Ana Attorney")
    before = packet.build(d, ROW_DONE, "Jane")
    translation.sign(d, rid, "Paulo Paralegal")
    after = packet.build(d, ROW_DONE, "Jane")
    pages = len(PdfReader(str(translation.pdf_path(d, rid))).pages)
    assert after["pages"] == before["pages"] + pages == len(PdfReader(str(d / "packet.pdf")).pages)
    sections = {s["tab"]: s for s in after["sections"]}
    assert [f["doc"] for f in sections["Exhibit A"]["files"]] == ["certidao.pdf", f"translation-{rid}.pdf"]
    packet_text = _text(d / "packet.pdf")
    assert "CERTIFICATE OF TRANSLATION" in packet_text and "Signed" in packet_text
    assert translation.DRAFT_MACHINE not in _flat(packet_text) and translation.DRAFT_CERTIFICATE not in _flat(packet_text)


def test_a_translation_the_folder_already_has_is_enough(tmp_path, filled, firm, monkeypatch):
    monkeypatch.setattr(packet, "load_schema", lambda: FULL_SCHEMA | {"forms": ["i485"], "cover_letter": False, "index_sheet": True})
    d = _case(tmp_path, filled)
    meta = json.loads((d / "meta.json").read_text())
    source = Path(meta["source_folder"])
    (source / "translator certificate.pdf").write_bytes(document_pdf(["CERTIFICATE OF TRANSLATION", "I certify that the translation is complete and accurate."]))
    meta["classifications"]["translator certificate.pdf"] = "translation_certification"
    (d / "meta.json").write_text(json.dumps(meta))
    plan = packet.plan(d, ROW_DONE)
    assert not any("translation" in c["text"].lower() and c["kind"] in ("missing", "sign") for c in plan["checklist"])
    assert [t["state"] for t in plan["translations"]] == ["covered"]


def test_an_english_document_or_another_kind_is_not_asked_for(tmp_path, filled, firm):
    d = _case(tmp_path, filled, language="en")
    assert not translation.needs(_record(d))
    with pytest.raises(ValueError, match="doesn't need a translation"):
        translation.make(d, _record(d)["id"], "Paulo")
    assert translation.status(d, [{"id": "birth", "files": [{"doc": "certidao.pdf", "type": "birth_certificate"}]}], documents.by_doc(d)) == []


# --- Haitian Creole and a missing model -------------------------------------------------------------------------------------------


def test_haitian_creole_goes_to_a_human_translator_who_types_it(packet_case, firm):
    d, rid = packet_case
    meta = documents.read(d)
    meta["documents"][0]["language"] = "ht"
    documents.save(d, meta)
    e = translation.make(d, rid, "Paulo Paralegal")
    assert e["state"] == "human" and "human translator" in e["problem"] and "Haitian Creole" in e["problem"] and not e["pdf"]
    assert firm.calls == []  # nothing was asked of a model that doesn't exist
    assert json.loads((d / "translations.json").read_text(encoding="utf-8"))["documents"][rid]["status"] == "needs_translator"
    assert _record(d)["translated"] is None
    plan = packet.plan(d, ROW_DONE)
    assert any(c["kind"] == "missing" and "needs a human translator" in c["text"] and "Haitian Creole" in c["text"] for c in plan["checklist"])
    # the certificate can still be made, for a translation the translator typed
    _translator(("ht",), name="Jean Traducteur Exemple")
    translation.set_translator(d, rid, "t1", "Ana Attorney")
    translation.set_text(d, rid, "BIRTH CERTIFICATE\nName: Ana Clara Exemplo Souza", "Jean Traducteur Exemple")
    e = translation.sign(d, rid, "Paulo Paralegal")
    assert e["state"] == "signed" and "Haitian Creole into English" in _flat(_text(translation.pdf_path(d, rid)))
    assert [f["doc"] for f in _birth(packet.plan(d, ROW_DONE))["files"]] == ["certidao.pdf", f"translation-{rid}.pdf"]


def test_a_missing_language_model_is_said_in_the_record_and_on_the_page(tmp_path, filled, firm, monkeypatch):
    d = _case(tmp_path, filled, language="fr")
    rid = _record(d)["id"]
    from classify import translate

    shown = translation.entry(d, rid)  # before anyone presses anything, the page already says so
    assert shown["state"] == "needed"
    monkeypatch.setattr(translate, "installed_language_codes", lambda: {"pt", "es"})  # French to English isn't installed here
    shown = translation.entry(d, rid)
    assert shown["state"] == "human" and "no French to English model" in shown["problem"]
    e = translation.make(d, rid, "Paulo Paralegal")
    assert e["state"] == "human" and "install it" in e["problem"] and not e["pdf"] and firm.calls == []
    state = json.loads((d / "translations.json").read_text(encoding="utf-8"))["documents"][rid]
    assert state["status"] == "missing_pair" and "French to English" in state["problem"]


def test_a_line_the_model_cannot_translate_is_shown_as_written_and_said(tmp_path, filled, monkeypatch, firm):
    import portal.questions as questions

    monkeypatch.setattr(questions, "translate_to_english", lambda text, lang: "" if "FILIACAO" in text else ENGLISH.get(text, text))
    d = _case(tmp_path, filled)
    e = translation.make(d, _record(d)["id"], "Paulo Paralegal")
    assert e["state"] == "draft" and e["problem"].startswith("1 line could not be translated and is shown as written")
    assert "FILIACAO: JOSE EXEMPLO SOUZA e MARIA EXEMPLO LIMA" in e["text"]
    monkeypatch.setattr(questions, "translate_to_english", lambda text, lang: "")
    (tmp_path / "again").mkdir()
    d2 = _case(tmp_path / "again", filled)
    e = translation.make(d2, _record(d2)["id"], "Paulo Paralegal")
    assert e["state"] == "human" and "gave nothing back" in e["problem"]


# --- the summary --------------------------------------------------------------------------------------------------------------------


def test_the_summary_is_built_from_the_extractors_fields_only(tmp_path, filled, firm):
    d = _case(tmp_path, filled)
    summary = translation.summaries(d)["certidao.pdf"]
    assert summary["language_name"] == "Portuguese"
    text = summary["summary"]
    assert text.startswith("Birth certificate in Portuguese, the client.")
    for words in ("Name on it: ANA CLARA EXEMPLO SOUZA", "Born: 03/14/2006", "Place of birth: SOROCABA", "Parents named: JOSE EXEMPLO SOUZA; MARIA EXEMPLO LIMA",
                  "the translator checks every name, date and number against the original"):
        assert words in text, words
    assert "applicant." not in text and " -- " not in text


def test_a_foreign_document_no_reader_covers_says_so(tmp_path, filled, firm):
    d = _case(tmp_path, filled, fields=False)
    text = translation.summaries(d)["certidao.pdf"]["summary"]
    assert text == "Birth certificate in Portuguese, the client. No reader for this type of document: nothing was read from it, so the translator reads the original."
    assert translation.summaries(tmp_path / "nothing here") == {}


def test_an_english_document_has_no_summary(tmp_path, filled, firm):
    assert translation.summaries(_case(tmp_path, filled, language="en")) == {}


# --- the one translation call -----------------------------------------------------------------------------------------------------------


def test_translate_to_english_is_the_one_call_beside_machine_translate(monkeypatch):
    from classify import translate
    from portal import questions

    seen = []
    monkeypatch.setattr(translate, "translate_text", lambda text, a, b="en": seen.append((text, a, b)) or (text if text == "Maria" else "Hello"))
    assert questions.translate_to_english("Ola", "pt") == "Hello" and seen == [("Ola", "pt", "en")]
    assert questions.translate_to_english("Maria", "pt") == "Maria"  # a name that comes back unchanged is a translation
    assert questions.translate_to_english("   ", "pt") == "" and len(seen) == 2
    monkeypatch.setattr(translate, "translate_text", lambda *a, **k: None)  # no model for the pair
    assert questions.translate_to_english("Ola", "ht") == ""


def test_typing_a_translation_is_one_row_in_the_event_ledger(tmp_path, filled, firm, monkeypatch):
    import events

    base = tmp_path / "ledger" / "events.jsonl"
    monkeypatch.setenv("I485_EVENTS", str(base))
    d = _case(tmp_path, filled)
    rid = _record(d)["id"]
    before = len(list(events.rows(base)))
    translation.set_text(d, rid, "BIRTH CERTIFICATE\nName: Ana Clara Exemplo Souza", "Marta Tradutora Exemplo")
    new = list(events.rows(base))[before:]
    assert [(r["kind"], r["action"]) for r in new] == [("translations", "saved")], "the translation's own row, not the document's as well"
