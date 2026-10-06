"""Firm-reference additions use fictional facts and preserve old submissions."""
# ruff: noqa: F811 -- shared pytest fixture imported by name
import copy
import io
import json
from pathlib import Path

import pytest
from pypdf import PdfReader
from test_intake_monthly_money import H, intake  # noqa: F401

from portal.bank import (
    all_questions,
    answers_to_facts,
    clean,
    load_bank,
    localized,
    missing_required,
    visible,
)
from portal.demo import answers
from portal.questionnaire_pdf import answer_lines, render


def added():
    return {key: q for key, q in all_questions(load_bank()).items() if q.get('added_in')}


@pytest.mark.parametrize('language', ['en', 'pt', 'es', 'ht'])
def test_draft_wording_and_optional_visibility(language):
    bank = load_bank()
    assert 'review' in bank['_reference_gap_wording_status'][language]
    assert len(added()) == 17
    assert all(not q['required'] and q['label'][language] for q in added().values())
    assert not set(added()).intersection(missing_required(bank, {}))
    state = {'ssn_applied': 'Yes', 'ead_applied': 'Yes', 'had_unemployment': 'Yes',
                 'used_passport_last_entry': 'Yes', 'public_benefits': 'Yes', 'mother_deceased': 'No',
                 'father_deceased': 'No', 'self_paid_us_travel': 'Yes'}
    shown = {q['id']: q for section in localized(bank, language, state) for q in section['questions']}
    assert all(shown[key]['label'] and shown[key]['tip'] for key in added())
    for qid, trigger, enabled in [('ssn_application_approved', 'ssn_applied', 'Yes'),
                                 ('ead_application_approved', 'ead_applied', 'Yes'),
                                 ('unemployment_support', 'had_unemployment', 'Yes'),
                                 ('last_entry_passport_number', 'used_passport_last_entry', 'Yes'),
                                 ('public_benefits_details', 'public_benefits', 'Yes'),
                                 ('mother_residence_city', 'mother_deceased', 'No'),
                                 ('self_paid_us_travel_details', 'self_paid_us_travel', 'Yes')]:
        q = added()[qid]
        assert visible(q, {trigger: enabled})
        for disabled in [None, 'Unsure', 'No' if enabled == 'Yes' else 'Yes']:
            assert not visible(q, {trigger: disabled})


def test_unknown_no_unanswered_and_number_provenance():
    bank = load_bank()
    q = added()['ead_applied']
    assert clean(q, None) == (None, '')
    assert clean(q, 'No') == ('No', '')
    assert clean(q, 'Unsure') == ('Unsure', '')
    facts = answers_to_facts({'ead_applied': 'Unsure'}, bank)
    assert [(f.fact_key, f.raw_value) for f in facts] == [('questionnaire.unsure.questionnaire.ead_applied', 'Unsure')]
    assert not answers_to_facts({'ead_applied': None}, bank)
    assert [(f.fact_key, f.normalized_value) for f in answers_to_facts({'ead_applied': 'No'}, bank)] == [('questionnaire.ead_applied', 'No')]
    repeat = all_questions(bank)['prior_marriages']
    field = next(f for f in repeat['fields'] if f['id'] == 'a_number')
    assert clean(field, 'A12345678') == ('012345678', '')
    assert clean(field, 'invalid') == (None, 'invalid_a_number')
    assert clean(field, 'Unsure') == ('Unsure', '')
    values = {'marital_status': 'Divorced', 'prior_marriages': [{'name': 'Fictional Former', 'a_number': 'Unsure'}]}
    facts = {f.fact_key: f for f in answers_to_facts(values, bank)}
    assert 'questionnaire.prior_spouse1_a_number' not in facts
    assert facts['questionnaire.unsure.questionnaire.prior_spouse1_a_number'].raw_value == 'Unsure'


def test_api_saves_clears_without_reopening_submitted_client(intake):
    client, store, _ = intake
    response = client.put('/api/answers', headers=H, json={'ead_applied': 'Unsure', 'last_entry_passport_number': 'FICTIONAL-PASSPORT'})
    assert response.status_code == 200
    assert response.json()['answers']['ead_applied'] == 'Unsure'
    response = client.put('/api/answers', headers=H, json={'ead_applied': None})
    assert response.status_code == 200 and 'ead_applied' not in store.answers('fictional-income')
    store.update_profile('fictional-income', status='submitted')
    before = store.answers('fictional-income')
    assert client.put('/api/answers', headers=H, json={'ead_applied': 'Yes'}).status_code == 409
    assert store.answers('fictional-income') == before


@pytest.mark.parametrize('language', ['en', 'pt', 'es', 'ht'])
def test_old_and_new_pdf_rendering_with_long_conditional_details(language):
    load_bank()
    profile = {'id': 'fictional-gap', 'name': 'Fictional Reference Example', 'status': 'submitted',
                   'submitted_at': '2026-10-06T12:00:00+00:00', 'language': language}
    old = answers()
    old_text = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(render(profile, old))).pages)
    for q in added().values():
        assert q['label'][language] not in old_text
    values = old | {'ead_applied': 'Unsure', 'used_passport_last_entry': 'Yes', 'last_entry_passport_number': 'FICTIONAL-PASSPORT',
                        'public_benefits': 'Yes', 'public_benefits_details': ('Fictional benefit dates and details. ' * 300) + 'FINAL BENEFIT DETAIL',
                        'had_unemployment': 'No', 'unemployment_support': 'HIDDEN SUPPORT', 'mother_deceased': 'Yes', 'mother_residence_city': 'HIDDEN CITY',
                        'marital_status': 'Divorced', 'prior_marriages': [{'name': 'Fictional Former', 'a_number': 'Unsure'}]}
    content = ' '.join(' '.join(p.extract_text() for p in PdfReader(io.BytesIO(render(profile, values))).pages).split())
    assert 'FICTIONAL-PASSPORT' in content and 'FINAL BENEFIT DETAIL' in content and 'Fictional Former' in content
    assert 'HIDDEN SUPPORT' not in content and 'HIDDEN CITY' not in content
    assert answer_lines({'type': 'weight'}, {'form': '121', 'kg': 55}, language) == ['121 lb (55 kg)']


@pytest.mark.parametrize('qid', [key for key, q in added().items() if q.get('show_if')])
def test_every_added_conditional_hides_stale_answers_from_pdf_and_facts(qid):
    bank = load_bank()
    q = added()[qid]
    trigger, enabled = q['show_if']['q'], q['show_if']['eq']
    for disabled in (None, 'Unsure', 'No' if enabled == 'Yes' else 'Yes'):
        values = {trigger: disabled, qid: 'STALE FICTIONAL ANSWER'}
        original = copy.deepcopy(values)
        assert not visible(q, values)
        assert q['fact'] not in {f.fact_key for f in answers_to_facts(values, bank)}
        profile = {'id': 'fictional-hidden', 'status': 'submitted', 'submitted_at': '2026-10-05T16:00:00+00:00', 'language': 'en'}
        content = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(render(profile, values, bank))).pages)
        assert q['label']['en'] not in content and 'STALE FICTIONAL ANSWER' not in content
        assert values == original  # rendering/filtering never clears saved answers


@pytest.mark.parametrize('language', ['en', 'pt', 'es', 'ht'])
def test_all_reference_additions_export_saved_values_and_unknown_provenance(language):
    bank = load_bank()
    fixture = Path(__file__).parent / 'fixtures/questionnaire_preview_fictional.json'
    values = answers() | json.loads(fixture.read_text(encoding='utf-8'))
    original = copy.deepcopy(values)
    profile = {'id': 'fictional-complete', 'name': 'Fictional Preview Example', 'status': 'submitted',
                   'submitted_at': '2026-10-05T16:00:00+00:00', 'language': language}
    content = ' '.join(' '.join(p.extract_text() for p in PdfReader(io.BytesIO(render(profile, values, bank))).pages).split())
    facts = {f.fact_key: f for f in answers_to_facts(values, bank)}
    for qid, q in added().items():
        assert visible(q, values), qid
        assert ' '.join(q['label'][language].split()) in content, qid
        fact_key = ('questionnaire.unsure.' if values[qid] == 'Unsure' else '') + q['fact']
        assert facts[fact_key].raw_value == values[qid], qid
        for line in answer_lines(q, values[qid], language):
            assert ' '.join(line.split()) in content, qid
    assert 'Fictional Former Spouse' in content
    assert 'questionnaire.unsure.questionnaire.prior_spouse1_a_number' in facts
    assert values == original


def test_preview_fixture_uses_real_question_ids_and_valid_fictional_saved_values():
    fixture = json.loads((Path(__file__).parent / 'fixtures/questionnaire_preview_fictional.json').read_text(encoding='utf-8'))
    questions = all_questions(load_bank())
    assert set(fixture) <= set(questions)
    for qid, value in fixture.items():
        assert not clean(questions[qid], value)[1], qid
        if questions[qid]['type'] == 'repeat':
            fields = {f['id'] for f in questions[qid]['fields']}
            assert all(set(row) <= fields for row in value)
    assert not missing_required(load_bank(), answers() | fixture)
