"""Brazilian Portuguese for Brazilian clients (src/pt_br.py): the offline translator's European words are rewritten in every machine draft a
client could read, names and places are left alone, and the product's own written Portuguese holds no European-only word."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import pt_br
from portal import questions

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("european,brazilian", [
    ("Por favor carregue uma foto do seu passaporte do seu telemóvel.", "Por favor envie uma foto do seu passaporte do seu celular."),
    ("Assine com a senha que o escritório lhe enviou.", "Entre com a senha que o escritório lhe enviou."),
    ("O arquivo foi recebido. A equipa vai contactá-lo.", "O arquivo foi recebido. A equipe vai entrar em contato com você."),
    ("Tem de levar a sua certidão de nascimento à nomeação.", "Precisa levar a sua certidão de nascimento ao agendamento."),
    ("O autocarro parte às 8 da manhã.", "O ônibus parte às 8 da manhã."),
    ("Precisamos de uma cópia da sua carta de condução e do seu cartão da Segurança Social.", "Precisamos de uma cópia da sua carteira de motorista e do seu cartão da Seguro Social."),
    ("Escreva o seu apelido e a sua morada.", "Escreva o seu sobrenome e o seu endereço."),
    ("Qual é o seu código postal?", "Qual é o seu código postal?"),  # Brazil says CEP for its own codes and código postal for another country's: left alone
])
def test_the_translators_european_words_become_brazilian(european, brazilian):
    assert pt_br.brazilian(european) == brazilian


def test_a_name_or_a_place_in_the_middle_of_a_sentence_is_left_alone():
    assert pt_br.brazilian("Ela mora em Morada Nova com a família Equipa.") == "Ela mora em Morada Nova com a família Equipa."
    assert pt_br.brazilian("Morada: Rua das Flores.") == "Endereço: Rua das Flores."  # the first word of a sentence is a word
    assert pt_br.brazilian("") == "" and pt_br.brazilian("Já é brasileiro: celular, ônibus, equipe.") == "Já é brasileiro: celular, ônibus, equipe."


def test_the_office_question_draft_in_portuguese_is_brazilian_and_spanish_is_untouched(monkeypatch):
    seen = {}

    def fake(text, lang):
        seen[(text, lang)] = True
        return {"pt": "Envie uma foto da sua carta de condução pelo telemóvel.", "es": "Envíe una foto de su licencia de conducir desde su teléfono celular."}[lang]

    monkeypatch.setattr(questions, "machine_translate", fake)
    pt = questions.draft("Send a photo of your driver's license from your cell phone.", "pt")
    assert pt["text_client"] == "Envie uma foto da sua carteira de motorista pelo celular." and not pt["needs_translator"]
    es = questions.draft("Send a photo of your driver's license from your cell phone.", "es")
    assert es["text_client"] == "Envíe una foto de su licencia de conducir desde su teléfono celular."
    choices = questions.draft("Which one?", "pt", ["The bus", "The train"])
    assert choices["options_client"] == ["Envie uma foto da sua carteira de motorista pelo celular."] * 2  # the fake answers every call the same way: both rewritten


def test_the_products_own_portuguese_holds_no_european_only_word():
    """The strings a client or a paralegal reads: the question bank and its help, the journey, the portal page, the Part 14 wordings, the EOIR-26A text,
    the office's case questions, the client's case words."""
    files = [p for pattern in ("src/case_questions.py", "src/portal/bank.py", "src/portal/static/portal.html", "schemas/questions/*.json",
                               "schemas/questions/help/*.json", "schemas/registers/journey.json", "schemas/firm/part14_wordings.json", "schemas/forms/*/text.json",
                               "schemas/registers/client_case.json", "schemas/registers/client_next_steps.json") for p in REPO.glob(pattern)]
    assert len(files) >= 6, [str(f) for f in files]
    found = {}
    for f in files:
        text = f.read_text(encoding="utf-8")
        words = pt_br.european_words_in(text)
        if words:
            found[f.name] = words
    assert found == {}, f"European Portuguese in the product's own strings: {found}"


def test_every_term_is_lower_case_and_phrases_come_before_their_words():
    seen = []
    for eu, br in pt_br.TERMS:
        assert eu == eu.lower() and eu.strip() and br.strip()
        for earlier in seen:
            assert not re.search(r"(?<![\w-])" + re.escape(earlier) + r"(?![\w-])", eu) or earlier == eu, f"'{earlier}' would rewrite inside '{eu}' first"
        seen.append(eu)
    assert json.dumps(pt_br.TERMS, ensure_ascii=False)  # plain data, printable
