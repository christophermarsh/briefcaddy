"""Fictional reference audits must not recreate records during legal lifecycle holds."""
# ruff: noqa: F811 -- canonical isolated fixture
import json
from types import SimpleNamespace

import pytest
from communication_fixture import STAFF
from test_correction_provenance import (
    authorize,
    observation,  # noqa: F401
)

import correction_provenance
import engagement
import evaluation_candidates
import purge
import reader_examples
from factgraph import FactGraph


def reference(case):
    graph=FactGraph.load(case/'fact_graph.json')
    src=graph.get('applicant.i94_number').sources[0]
    result={'form':'fictional-lifecycle-reference','boxes':[{'key':'applicant.i94_number','field':'fictional-i94',
            'kind':'different','cause':'reader','ours':src.normalized_value,'reference':'33333333333'}]}
    return SimpleNamespace(case=case.name,graph=graph),result


@pytest.mark.parametrize('state',['waiting','running','purged','destroyed'])
def test_reference_lifecycle_hold_prevents_any_new_examples(observation,monkeypatch,state):
    _,case,_,_,_=observation
    ref,result=reference(case)
    before={path.name:path.read_bytes() for path in reader_examples.files_of(case)}
    if state=='destroyed':
        real=engagement.read
        monkeypatch.setattr(engagement,'read',lambda folder:dict(real(folder),destroyed={'by':'Fictional Attorney','at':'2026-10-05'}))
    else:monkeypatch.setattr(purge,'entry',lambda *args:{'state':state})
    reader_examples.from_reference(case.parent,ref,result)
    assert {path.name:path.read_bytes() for path in reader_examples.files_of(case)}==before


def test_cleanup_completing_during_reference_preparation_cannot_resurrect_examples(observation,monkeypatch):
    _,case,_,_,_=observation
    ref,result=reference(case)
    original=correction_provenance.retain
    def cleanup_after_prepare(*args,**kwargs):
        proof=original(*args,**kwargs)
        # Controlled legal lifecycle tombstone represents completed cleanup.
        # No source, input, case-folder or real record is deleted in this test.
        reader_examples.remove_case(case)
        ended=engagement.read(case);ended['destroyed']={'by':'Fictional Attorney','at':'2026-10-05'}
        (case/engagement.FILE).write_text(json.dumps(ended))
        return proof
    monkeypatch.setattr(correction_provenance,'retain',cleanup_after_prepare)
    reader_examples.from_reference(case.parent,ref,result)
    assert not reader_examples.case_folder(case).exists()
    assert reader_examples.files_of(case)==[]


def test_missing_case_reference_preparation_creates_no_examples(observation):
    _,case,_,_,_=observation
    ref,result=reference(case)
    ref.case='fictional-missing-case'
    reader_examples.from_reference(case.parent,ref,result)
    assert not reader_examples.case_folder(case.with_name(ref.case)).exists()


@pytest.mark.parametrize('state',['waiting','destroyed'])
def test_candidate_lifecycle_hold_refuses_initial_and_final_export(observation,monkeypatch,state):
    home,_case,_,_,record=observation
    root,grant,out=authorize(home,record)
    if state=='waiting':monkeypatch.setattr(purge,'entry',lambda *args:{'state':'waiting'})
    else:
        real=engagement.read
        monkeypatch.setattr(engagement,'read',lambda folder:dict(real(folder),destroyed={'by':'Fictional Attorney','at':'2026-10-05'}))
    with pytest.raises(ValueError):evaluation_candidates.export_candidates(home,root,grant,STAFF,[record['case']],out)
    assert not out.exists()


@pytest.mark.parametrize('state',['waiting','destroyed'])
def test_candidate_lifecycle_transition_during_preparation_refuses_final_export(observation,monkeypatch,state):
    home,case,_,_,record=observation
    root,grant,out=authorize(home,record)
    real=evaluation_candidates._authorization;calls=0
    def transition_before_final(*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==2:
            if state=='waiting':monkeypatch.setattr(purge,'entry',lambda *unused:{'state':'waiting'})
            else:
                ended=engagement.read(case);ended['destroyed']={'by':'Fictional Attorney','at':'2026-10-05'}
                (case/engagement.FILE).write_text(json.dumps(ended))
        return real(*args,**kwargs)
    monkeypatch.setattr(evaluation_candidates,'_authorization',transition_before_final)
    with pytest.raises(ValueError):evaluation_candidates.export_candidates(home,root,grant,STAFF,[record['case']],out)
    assert not out.exists()
