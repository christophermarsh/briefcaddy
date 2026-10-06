"""Independently fictional parser/model-read fixtures, with no client history."""
import pytest
from questionnaire.handwriting import normalize, parse_date, reads_agree, judge_reads

@pytest.mark.parametrize("raw,expected", [
    ("04/08/1990", "1990-08-04"), ("1990-04-08", None),
    ("April 8, 1990", "1990-04-08"), ("not a date", None),
])
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected

def test_reads_disagree_on_any_date_difference():
    assert not reads_agree("date", {"value":"1990-04-08"}, {"value":"1990-04-09"})

def test_independent_employment_fixture_retains_date_and_name_relations():
    first = {"employer":"Fictional Workshop", "occupation":"Teacher", "street":"700 Example Lane",
             "city":"Pittsburgh", "state":"PA", "zip":"15213", "country":"United States",
             "date_from":"2017-04-08", "date_to":"2019-07-19"}
    assert reads_agree("employer", first, dict(first))
    changed = dict(first, date_from="2018-04-08")
    assert not reads_agree("employer", first, changed)

def test_person_name_normalization_uses_an_independent_fictional_value():
    values, error = normalize("person_name", {"value":"Rafaela Demonstra Marfim"})
    assert not error
    assert values["value"] == "RAFAELA DEMONSTRA MARFIM"

def test_two_agreeing_reads_are_accepted():
    spec = {"id":"fictional_weight", "kind":"weight", "facts":{"value":"applicant.weight_lbs"}}
    reading = judge_reads(spec, [{"value":"150","unit":"lbs"}, {"value":"150","unit":"lbs"}])
    assert reading.status == "ok"
    assert reading.values["value"] == "150"

def test_two_disagreeing_reads_go_to_a_human():
    spec = {"id":"fictional_weight", "kind":"weight", "facts":{"value":"applicant.weight_lbs"}}
    reading = judge_reads(spec, [{"value":"150","unit":"lbs"}, {"value":"180","unit":"lbs"}])
    assert reading.status == "disagree"
