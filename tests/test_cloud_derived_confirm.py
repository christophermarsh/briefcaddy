"""Actual named source confirmations must replay the displayed derived value."""
import pytest

import critical_review
from review.state import (build_items, record_decision, load_decisions, reviewed_graph,
                          apply_decisions, load_decision_log)
from test_name_cards import (_retained_name_case, _set, CURRENT, MARRIED, _value,
                             FIELD_MAP, TEMPLATE, CATALOG)
from test_name_events import MA_AFTER, MA_FULL_NAME
from test_critical_review import item, decision


@pytest.mark.parametrize('certificate', [MA_AFTER, MA_FULL_NAME])
def test_confirm_current_derived_family_survives_refresh(certificate, tmp_path):
    case = _retained_name_case(tmp_path, certificate)
    _set(case, CURRENT, MARRIED, note='Actual source-backed chosen name.')
    before = critical_review.context(case)['applicant.family_name']
    stored = record_decision(case, item('applicant.family_name'), decision(case, key='applicant.family_name'))
    assert stored['evidence_confirmation']['keys']['applicant.family_name']['chosen_value'] == 'EXEMPLO SOUZA TESTE'
    assert 'fact:applicant.family_name' in load_decisions(case)
    graph = reviewed_graph(case)
    assert _value(graph, 'applicant.family_name') == 'EXEMPLO SOUZA TESTE'
    assert graph.get('applicant.family_name').review.chosen_value == 'EXEMPLO SOUZA TESTE'
    assert critical_review.context(case)['applicant.family_name']['fingerprint'] == before['fingerprint']
    assert 'applicant.family_name' in graph._critical_reviewed


def test_grouped_ui_name_confirm_keeps_both_current_source_proofs(tmp_path):
    case = _retained_name_case(tmp_path, MA_AFTER)
    _set(case, CURRENT, MARRIED, note='Actual source-backed chosen name.')
    cards = build_items(case, FIELD_MAP, TEMPLATE, CATALOG, pending=False)['cards']
    card = next(c for c in cards if c['id'] == 'portal:1')
    keys = {f['key'] for f in card['facts']}
    assert {'applicant.given_name', 'applicant.family_name'} <= keys
    ctx = critical_review.context(case)
    stored = record_decision(case, card, {'action': 'confirm', 'reviewer': 'Fictional Reviewer', 'role': 'paralegal',
        'evidence_fingerprints': {key: ctx[key]['fingerprint'] for key in keys if key in ctx}})
    assert set(stored['evidence_confirmation']['keys']) == keys & set(ctx)
    assert card['id'] in load_decisions(case)
    graph = reviewed_graph(case)
    assert {'applicant.given_name', 'applicant.family_name'} <= graph._critical_reviewed
    assert _value(graph, 'applicant.family_name') == 'EXEMPLO SOUZA TESTE'


def test_nonmatching_raw_confirm_never_injects_proof_value(tmp_path):
    case = _retained_name_case(tmp_path, MA_AFTER)
    _set(case, CURRENT, MARRIED, note='Actual source-backed chosen name.')
    stored = record_decision(case, item('applicant.family_name'), decision(case, key='applicant.family_name'))
    from factgraph import FactGraph
    raw = FactGraph.load(case / 'fact_graph_raw.json')
    before = _value(raw, 'applicant.family_name')
    apply_decisions(raw, {'fact:applicant.family_name': stored})
    assert _value(raw, 'applicant.family_name') == before == 'EXEMPLO SOUZA'
    assert raw.get('applicant.family_name').review is None


def test_source_change_invalidates_stable_confirmation_without_deleting_history(tmp_path):
    import subject_attribution
    case = _retained_name_case(tmp_path, MA_AFTER)
    _set(case, CURRENT, MARRIED, note='Actual source-backed chosen name.')
    body = decision(case, key='applicant.family_name')
    stored = record_decision(case, item('applicant.family_name'), body)
    assert 'fact:applicant.family_name' in load_decisions(case)
    row = next(r for r in subject_attribution.views(case) if r['file'] == 'casamento.pdf')
    subject_attribution.assign(case, row['instance_id'], row['fingerprint'], {}, 'Fictional Reviewer', 'paralegal',
                               reference_only=True, note='No longer attributed to this client.')
    assert 'fact:applicant.family_name' not in load_decisions(case)
    assert stored['at'] == load_decision_log(case)['fact:applicant.family_name']['at']
    assert load_decision_log(case)['fact:applicant.family_name']['history']
    assert 'applicant.family_name' not in reviewed_graph(case)._critical_reviewed
    with pytest.raises(ValueError, match='changed|review|source|boundaries'):
        record_decision(case, item('applicant.family_name'), body)
