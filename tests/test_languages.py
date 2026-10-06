"""Portuguese / Spanish / French support for questionnaire answers
(src/questionnaire/languages.py) and per-language question maps."""

import pytest

from questionnaire.handwriting import normalize, parse_date, to_english
from questionnaire.languages import detect_language, fold, translate_occupation
from questionnaire.reader import load_questionnaire_maps
import schema_path

PT = "Você já teve um visto negado para os Estados Unidos? ( ) Sim ( ) Não Nome Completo Data de Nascimento Endereço Qual seu nome Alguma vez"
ES = "¿Alguna vez le han negado una visa para los Estados Unidos? ( ) Sí ( ) No Nombre completo Fecha de nacimiento Dirección Cuál es su nombre usted ha"
FR = "Avez-vous déjà été refusé un visa pour les États-Unis ? ( ) Oui ( ) Non Nom complet Date de naissance Adresse Quel est votre nom vous"


@pytest.mark.parametrize("text, lang", [(PT, "pt"), (ES, "es"), (FR, "fr"), ("hello world", None)])
def test_detect_language(text, lang):
    assert detect_language(text * 2) == lang


@pytest.mark.parametrize(
    "raw, iso",
    [
        ("23 de novembro de 2016", "2016-11-23"),
        ("3 de marzo de 2020", "2020-03-03"),
        ("12 del febrero de 1999", "1999-02-12"),
        ("le 3 mars 2020", "2020-03-03"),
        ("14 février 2001", "2001-02-14"),
        ("5 août 2019", "2019-08-05"),
    ],
)
def test_long_form_dates_in_three_languages(raw, iso):
    assert parse_date(raw) == iso


@pytest.mark.parametrize("answer, expected", [("Sim", "Yes"), ("Não, nunca", "No"), ("Sí", "Yes"), ("No", "No"), ("Oui", "Yes"), ("Non", "No")])
def test_written_yes_no(answer, expected):
    assert normalize("yes_no_text", {"value": answer})[0]["value"] == expected


@pytest.mark.parametrize("word, color", [("preta", "Black"), ("castaño", "Brown"), ("rubia", "Blond"), ("châtain", "Brown"), ("noir", "Black")])
def test_hair_colors(word, color):
    assert normalize("hair_color", {"value": word})[0]["value"] == color


@pytest.mark.parametrize("word, country", [("Brasil", "BRAZIL"), ("México", "MEXICO"), ("Haïti", "HAITI"), ("République Dominicaine", "DOMINICAN REPUBLIC"), ("España", "SPAIN")])
def test_countries_by_table(word, country):
    assert to_english("applicant.mother_country_of_birth", word) == country


def test_occupation_glossary_comes_before_machine_translation():
    # Machine translation of the single word "Albañil" (bricklayer) gave "Meatballs".
    assert translate_occupation("Albañil", "es") == "BRICKLAYER"
    assert translate_occupation("PEDREIRO", "pt") == "MASON"
    assert translate_occupation("Femme de ménage", "fr") == "HOUSE CLEANER"
    assert fold("Açaí Ñandú Élève") == "ACAI NANDU ELEVE"


def test_maps_are_found_per_language(tmp_path):
    import json

    root = schema_path.schemas_in(tmp_path)
    schema_path.folder("paper_map", root).mkdir(parents=True)
    schema_path.path("paper_map", "map", root).write_text(json.dumps({"language": "pt", "questions": [{"id": "a"}]}))
    schema_path.path("paper_map", "text_map", root).write_text(json.dumps({"fields": [{"id": "t"}]}))
    schema_path.path("paper_map", "map.es", root).write_text(json.dumps({"questions": [{"id": "b"}]}))
    maps = load_questionnaire_maps(root)
    assert set(maps) == {"pt", "es"}
    assert maps["pt"] == ([{"id": "a"}], [{"id": "t"}])
    assert maps["es"] == ([{"id": "b"}], [])  # no Spanish text map yet: checkboxes only


def test_questionnaire_in_a_language_without_a_map_is_one_clear_flag():
    from batch import process_documents
    from questionnaire import QuestionnaireReading

    result = process_documents(
        "t", [("q.pdf", "Questionário para Ajuste de Status\nI485 - SIJS\n")],
        questionnaire_reader=lambda doc_id: QuestionnaireReading(language="es", unsupported_language="Spanish"),
    )
    reading = [f for f in result.review_flags if f.kind != "missing"]
    assert [(f.level, f.kind) for f in reading] == [("blocking", "unsupported_language")]
    assert "Spanish" in reading[0].message
