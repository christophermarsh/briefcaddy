"""Fictional printed-surname compositions retain actual same-party parents."""
from factgraph import FactGraph
import name_events
import subject_attribution
from review.state import reviewed_graph, source_prerequisites, undo_decision
from test_name_cards import (_retained_name_case, _items, _set, CURRENT, MARRIED,
                             EARLIER, _value, _independent_name_source_checks)
from test_name_events import MA_AFTER


def test_printed_surname_composition_has_two_current_same_party_parents(tmp_path):
    case = _retained_name_case(tmp_path, MA_AFTER)
    assert all(row['bound'] and row['current'] for row in subject_attribution.views(case))
    assert not source_prerequisites(case).get('applicant.family_name')
    graph = reviewed_graph(case)
    source = next(s for s in graph.get('applicant.family_name').sources if s.doc_id == 'casamento.pdf')
    assert source.from_facts == ['marriage.party_a.surname_after', 'marriage.party_a.name']
    originals = [s for key in source.from_facts for s in graph.get(key).sources if s.doc_id == source.doc_id]
    assert all(s.instance_id and s.subject_role == 'party_a' and s.evidence_version for s in originals)
    assert not source.instance_id and not source.evidence_version  # composed, never a fabricated original read
    assert 'applicant.given_name' not in source.from_facts and 'applicant.family_name' not in source.from_facts
    event = next(e for e in name_events.view(graph)['events'] if e.get('parent_key') == source.from_facts[0])
    assert event['name'] == MARRIED and event['printed'] == 'Surname after marriage: EXEMPLO SOUZA TESTE'
    assert event['given_parent_key'] == 'marriage.party_a.name'
    assert event['composition'] == 'given_name_and_printed_surname'
    basis = event['given_basis']
    assert basis['kind'] == 'notice_split' and basis['doc'] == 'i360.pdf' and basis['key'] == 'applicant.given_name'
    assert basis['instance'] and basis['evidence'] and basis['value'] == 'ANA CLARA'
    assert set(source.input_evidence) == {s.evidence_version for s in originals} | {basis['evidence']}
    assert 'Name after marriage' not in event['printed']
    _independent_name_source_checks(case)


def test_surname_save_and_undo_keep_raw_sources_and_current_review_hold(tmp_path):
    case = _retained_name_case(tmp_path, MA_AFTER)
    before = (case / 'fact_graph_raw.json').read_bytes()
    _independent_name_source_checks(case)
    _set(case, CURRENT, MARRIED, note='Reviewed printed surname and same-party prior name.')
    graph = reviewed_graph(case)
    assert _value(graph, 'applicant.family_name') == 'EXEMPLO SOUZA TESTE'
    assert (_value(graph, 'applicant.other_name1_given'), _value(graph, 'applicant.other_name1_family')) == ('ANA CLARA', 'EXEMPLO SOUZA')
    _independent_name_source_checks(case)
    undo_decision(case, CURRENT, 'Pat Paralegal')
    assert CURRENT in _items(case) and EARLIER in _items(case)[CURRENT]['facts'][0]['input']['options']
    assert (case / 'fact_graph_raw.json').read_bytes() == before


def test_missing_same_party_name_does_not_forge_a_surname_composition(tmp_path):
    case = _retained_name_case(tmp_path, MA_AFTER)
    graph = FactGraph.load(case / 'fact_graph_raw.json')
    graph._facts.pop('marriage.party_a.name')
    assert not any(e.get('parent_key') == 'marriage.party_a.surname_after' for e in name_events.events(graph))


def test_removing_current_notice_split_basis_reopens_name_review(tmp_path):
    import critical_review
    import pytest
    from review.state import record_decision, load_decisions
    from test_critical_review import item, decision
    case = _retained_name_case(tmp_path, MA_AFTER)
    _set(case, CURRENT, MARRIED, note='Reviewed original notice split and printed surname.')
    before = name_events.marriage_evidence(reviewed_graph(case))
    current = critical_review.context(case)['applicant.family_name']['fingerprint']
    # Confirm replays a raw field before derivation; choosing the current
    # source-backed derived family name explicitly keeps the reviewed value.
    old_confirmation = decision(case, action='set', key='applicant.family_name', value='EXEMPLO SOUZA TESTE')
    stored = record_decision(case, item('applicant.family_name'), old_confirmation)
    assert stored['evidence_confirmation']['basis'] == 'manual_retained_source_review'
    assert 'fact:applicant.family_name' in load_decisions(case)
    notice = next(row for row in subject_attribution.views(case) if row['file'] == 'i360.pdf')
    subject_attribution.assign(case, notice['instance_id'], notice['fingerprint'], {}, 'Pat Paralegal', 'paralegal',
                               reference_only=True, note='This notice is retained as reference and cannot supply the split.')
    graph = reviewed_graph(case)
    event = next(e for e in name_events.events(graph) if e.get('composition'))
    assert event['given_basis']['kind'] == 'party_name_split'
    assert event['given_basis']['given'] == 'ANA CLARA'
    assert name_events.marriage_evidence(graph) != before
    assert critical_review.context(case)['applicant.family_name']['fingerprint'] != current
    assert CURRENT in _items(case)
    assert any('applicant.family_name' in problem for problem in critical_review.problems(case))
    assert 'fact:applicant.family_name' not in load_decisions(case)
    with pytest.raises(ValueError, match='changed|refresh|review|source'):
        record_decision(case, item('applicant.family_name'), old_confirmation)


def test_changed_notice_given_split_preserves_saved_name_as_review_context():
    """Synthetic graph reread; not a retained PDF/model or source-approval claim."""
    import json
    from test_name_events import _graph
    graph = _graph()
    snapshot = name_events.marriage_evidence(graph)
    graph.set_by_review(name_events.CURRENT_KEY, MARRIED, 'Pat Paralegal')
    graph.set_by_review(name_events.EVIDENCE_KEY, snapshot, 'Pat Paralegal')
    graph.set_by_review(name_events.OVER_KEY, json.dumps(['casamento.pdf', 'certidao.pdf']), 'Pat Paralegal')
    source = next(s for s in graph.get('applicant.given_name').sources if s.doc_id == 'i360.pdf')
    source.normalized_value = 'ANA'
    result = name_events.settle(graph)
    assert result['current']['name'] == MARRIED and result['stale'] and not result['decided']
    assert any(e.get('composition') and e['name'] == 'ANA EXEMPLO SOUZA TESTE' for e in result['events'])
    assert result['marriage_evidence'] != snapshot
