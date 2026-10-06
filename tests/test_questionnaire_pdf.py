"""Submitted client copies: readable answers and protected downloads."""
# ruff: noqa: F811 -- shared fixtures
import io

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from test_prospects import call, firm, server  # noqa: F401
from test_staff_consent_http import client
from test_staff_questionnaire_access import access

from portal.app import create_app
from portal.bank import load_bank
from portal.demo import answers
from portal.questionnaire_pdf import answer_lines, render


def pdf_text(raw):
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(raw)).pages)


def test_staff_and_client_download_only_their_submitted_questionnaire(server, firm):
    cid = client(server, language="pt", name="Fictional Joao Teste")
    firm["store"].update_profile(cid, name="Fictional João Teste")
    token = access(server, cid)["url"].rsplit("/", 1)[1]
    with TestClient(create_app(firm["portal"])) as phone:
        assert phone.get("/api/questionnaire.pdf").status_code == 401
        assert phone.post("/api/access-link", json={"token": token}, headers={"X-Portal": "1"}).status_code == 200
        assert phone.get("/api/questionnaire.pdf").status_code == 409
        assert call(server, "jane", "/api/questionnaire.pdf?client=" + cid)[0] == 409
        assert phone.put("/api/answers", json=answers(), headers={"X-Portal": "1"}).status_code == 200
        assert phone.post("/api/submit", json={"agree": True, "signature": "Fictional João Teste"}, headers={"X-Portal": "1"}).status_code == 200
        result = phone.get("/api/questionnaire.pdf?client=case-rosa")  # query cannot select a different person
        assert result.status_code == 200
        assert result.headers["content-type"] == "application/pdf"
        assert "attachment" in result.headers["content-disposition"]
        assert result.headers["cache-control"] == "no-store"
        content = pdf_text(result.content)
        assert "Questionário enviado" in content and "Fictional João Teste" in content
        assert "Ana Clara" in content and "Sorocaba" in content
        assert "05/10/2026" in content and "Confirmado por" in content
        assert "127.0.0.1" not in content and "case-rosa" not in content
        code, staff_pdf = call(server, "jane", "/api/questionnaire.pdf?client=" + cid)
        assert code == 200 and pdf_text(staff_pdf) == content
        assert b"Download questionnaire PDF" in call(server, "jane", "/api/answers?client=" + cid)[1]
        assert call(server, "jane", "/api/questionnaire.pdf?client=case-rosa")[0] == 404
        assert call(server, "jane", "/api/questionnaire.pdf?client=nonexistent")[0] == 404


def test_pdf_wraps_long_answers_and_excludes_hidden_questions():
    bank = {"sections": [{"title": {"en": "Details", "pt": "Informações"}, "questions": [
        {"id": "story", "type": "textarea", "label": {"pt": "Conte sua história", "en": "Your story"}},
        {"id": "hidden", "type": "text", "label": {"en": "HIDDEN QUESTION"}, "show_if": {"q": "flag", "eq": "Yes"}},
    ]}]}
    profile = {"id": "fictional", "name": "Fictional João <Teste>", "status": "submitted", "submitted_at": "2026-10-05T10:30:00+00:00",
               "language": "en", "attestation": {"language": "pt", "typed_name": "Fictional João <Teste>"}}
    raw = render(profile, {"story": ("Uma explicação com café & pão. " * 500) + "FINAL ANSWER", "hidden": "HIDDEN VALUE"}, bank)
    reader = PdfReader(io.BytesIO(raw))
    assert len(reader.pages) > 2
    content = pdf_text(raw)
    assert "Conte sua história" in content and "FINAL ANSWER" in content
    assert "HIDDEN" not in content and "Fictional João <Teste>" in content
    for n, page in enumerate(reader.pages, 1):
        assert f"Página {n}" in page.extract_text()


def test_localized_nested_answers_and_submission_required():
    q = {"type": "repeat", "fields": [{"id": "role", "label": {"pt": "Cargo"}, "options": [{"value": "student", "label": {"pt": "Estudante"}}]},
                                      {"id": "date", "type": "date", "label": {"pt": "Data"}}]}
    assert answer_lines(q, [{"role": "student", "date": "2026-10-05"}], "pt") == ["1.", "Cargo: Estudante", "Data: 05/10/2026"]
    assert answer_lines({"type": "checklist", "options": []}, {"none": True}, "pt") == ["Nenhuma destas opções"]
    assert answer_lines({"type": "foreign_address"}, {"street": "Rua Flores", "date_from": "2026-10-05"}, "pt") == ["Rua e número: Rua Flores", "Morei lá desde: 05/10/2026"]
    with pytest.raises(ValueError, match="after"):
        render({"status": "started"}, {}, load_bank())


@pytest.mark.parametrize("language", ["en", "pt", "es", "ht"])
def test_pdf_has_consistent_headers_page_totals_and_full_repeated_answers(language):
    from portal.questionnaire_pdf import COPY_WORDS, WORDS
    labels = {lang: "Fictional details" for lang in COPY_WORDS}
    bank = {"sections": [{"title": labels, "questions": [
        {"id": "zero", "type": "money", "required": False, "label": labels},
        {"id": "unknown", "type": "money", "label": labels},
        {"id": "blank", "type": "money", "label": labels},
        {"id": "history", "type": "repeat", "label": labels, "fields": [
            {"id": "role", "type": "text", "label": labels},
            {"id": "explanation", "type": "textarea", "label": labels}]},
        {"id": "hidden", "type": "text", "label": {"en": "HIDDEN QUESTION"}, "show_if": {"q": "flag", "eq": "Yes"}},
    ]}]}
    profile = {"id": "fictional-layout", "name": "Fictional Álpha <Test>", "status": "submitted",
               "submitted_at": "2026-10-05T12:00:00+00:00", "language": language,
               "attestation": {"language": language, "typed_name": "Fictional Álpha <Test>"}}
    values = {"zero": "0.00", "unknown": "Unsure", "history": [
        {"role": "FICTIONAL FIRST ROLE", "explanation": ("Fictional long explanation & details. " * 300) + "FIRST FINAL SENTENCE"},
        {"role": "FICTIONAL SECOND ROLE", "explanation": "SECOND FINAL SENTENCE"}], "hidden": "HIDDEN VALUE"}
    reader = PdfReader(io.BytesIO(render(profile, values, bank)))
    assert len(reader.pages) >= 3
    content = "\n".join(page.extract_text() for page in reader.pages)
    for expected in ("FIRST FINAL SENTENCE", "SECOND FINAL SENTENCE", "FICTIONAL FIRST ROLE", "FICTIONAL SECOND ROLE",
                     "Fictional Álpha <Test>", WORDS[language][3], WORDS[language][4], COPY_WORDS[language]["source"]):
        assert expected in content
    assert "HIDDEN" not in content
    assert COPY_WORDS[language]["monthly"] in content
    for n, page in enumerate(reader.pages, 1):
        page_text = page.extract_text()
        assert WORDS[language][0] in page_text
        assert f"{WORDS[language][8]} {n} {COPY_WORDS[language]['of']} {len(reader.pages)}" in page_text
    assert values["unknown"] == "Unsure" and values["zero"] == "0.00"


def test_pdf_preserves_explicit_money_zero_and_unknown_as_different_answers():
    from portal.questionnaire_pdf import COPY_WORDS
    for language in COPY_WORDS:
        zero = answer_lines({"type": "money"}, "0.00", language)
        blank = answer_lines({"type": "money"}, None, language)
        unknown = answer_lines({"type": "money"}, "Unsure", language)
        assert zero != blank and blank != unknown and zero != unknown
        assert COPY_WORDS[language]["monthly"] in zero[0]


def test_old_submitted_copy_does_not_gain_nine_unasked_money_rows():
    profile = {"id": "fictional-old", "name": "Fictional Old Example", "status": "submitted",
               "submitted_at": "2026-10-05T12:00:00+00:00", "language": "en"}
    values = answers()
    old = pdf_text(render(profile, values))
    assert "Your monthly money" not in old and "per month" not in old
    current = pdf_text(render(profile, values | {"fw_income_work": "0.00"}))
    assert "Your monthly money" in current and "$0.00 per month" in current


def fictional_profile():
    return {"id": "fictional-weight", "name": "Fictional Weight Example", "status": "submitted",
            "submitted_at": "2026-10-05T12:00:00+00:00", "language": "en"}


def pdf_runs(raw):
    runs = []
    for page in PdfReader(io.BytesIO(raw)).pages:
        page.extract_text(visitor_text=lambda text, cm, tm, font, size:
                          runs.append((text, str((font or {}).get('/BaseFont', '')))))
    return runs


def test_pdf_uses_bold_labels_and_regular_values_in_groups_addresses_and_repeats():
    fields = [{"id": "state", "type": "text", "label": "State"},
              {"id": "country", "type": "country", "label": "Country"},
              {"id": "note", "type": "textarea", "label": "Details"}]
    bank = {"sections": [{"title": "Addresses", "questions": [
        {"id": "home", "type": "us_address", "label": "Current address"},
        {"id": "foreign", "type": "foreign_address", "label": "Foreign address"},
        {"id": "group", "type": "group", "label": "Grouped details", "fields": fields},
        {"id": "repeat", "type": "repeat", "label": "Prior details", "fields": fields},
        {"id": "checklist", "type": "checklist", "label": "Selected details",
         "options": [{"value": "example", "label": "FICTIONAL CHECKED OPTION"}]},
    ]}]}
    values = {"home": {"state": "MA", "city": "Fictional City"},
              "foreign": {"province": "SP", "country": "BR"},
              "group": {"state": "FL", "country": "US", "note": "State: <bold> & answer"},
              "repeat": [{"state": "NY", "country": "USA", "note": "Country: still an answer"}],
              "checklist": {"selected": ["example"], "explain": "Explanation: is part of this answer"}}
    runs = pdf_runs(render(fictional_profile(), values, bank))
    for label in ("State:", "City:", "State / province:", "Country:", "Details:", "Selected:", "Explanation:"):
        matching = [(text, font) for text, font in runs if text.strip() == label]
        assert matching and all('Bold' in font for _, font in matching)
    for value in ("MA", "SP", "BR", "FL", "US", "NY", "USA", "Fictional City",
                  "State: <bold> & answer", "Country: still an answer", "FICTIONAL CHECKED OPTION",
                  "Explanation: is part of this answer"):
        matching = [(text, font) for text, font in runs if value in text]
        assert matching and all('Roman' in font for _, font in matching)


@pytest.mark.parametrize('lines', range(13, 39))
def test_addresses_heading_stays_with_first_answer_at_page_boundaries(lines):
    bank = {"sections": [
        {"title": "Details", "questions": [{"id": "intro", "type": "textarea", "label": "Earlier answers"}]},
        {"title": "Addresses", "questions": [{"id": "home", "type": "us_address", "label": "Current address"}]},
    ]}
    raw = render(fictional_profile(), {"intro": '\n'.join(['Fictional earlier line'] * lines),
                                      "home": {"street": "123 Fictional Heading Street", "apt": "2", "city": "Fictional City",
                                               "state": "MA", "zip": "00000", "country": "USA"}}, bank)
    found = False
    for page in PdfReader(io.BytesIO(raw)).pages:
        content = ' '.join(page.extract_text().split())
        if '02 Addresses' in content:
            found = True
            assert '123 Fictional Heading Street' in content
    assert found
