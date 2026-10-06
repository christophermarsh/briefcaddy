"""Fictional correction observations; no training, notification or provider call."""
import copy
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from communication_fixture import ATTORNEY, STAFF, installation
from evaluation.corpus import digest
from test_critical_review import KEY, decision, item, saved

import clock
import correction_provenance
import evaluation_candidates as candidates
import purge
import reader_examples
import restricted
from learning import labels, report
from learning.store import connect, record_run
from review.state import record_decision


@pytest.fixture
def observation(tmp_path,monkeypatch):
    data=installation(tmp_path,monkeypatch)
    monkeypatch.setenv('PORTAL_NOTIFY','0')
    monkeypatch.setenv('I485_READER_EXAMPLES',str(data/'reader_examples'))
    case=saved(data)
    stored=record_decision(case,item(),decision(case,action='set',value='22222222222'))
    path=reader_examples.files_of(case)[0]
    record=json.loads(path.read_text())
    return tmp_path,case,stored,path,record


def authorize(home,record):
    root=home/'data'/'evaluation-candidates'
    approval_root=home/'data'/'evaluation-authorizations'/record['case']
    evidence_root=approval_root/'evidence';evidence_root.mkdir(parents=True,mode=0o700)
    allowed=[{'case':record['case'],'id':record['id'],'binding_sha256':candidates.binding(record)}]
    body={'version':1,'firm_root':str(home),'corpus_root':str(root),'cases':[record['case']],
          'approved':True,'approved_by':ATTORNEY,'approved_at':clock.stamp(),'purpose':'evaluation',
          'allowed_examples':allowed,'example_bindings_sha256':digest(allowed),'revoked':False}
    evidence={'version':1,'purpose':'evaluation','approved':True,'approved_by':ATTORNEY,
              'approved_at':body['approved_at'],'authorization_sha256':digest(body)}
    proof_path=evidence_root/'approval.json';proof_path.write_text(json.dumps(evidence))
    grant=body|{'approval_evidence':{'file':'evidence/approval.json','sha256':hashlib.sha256(proof_path.read_bytes()).hexdigest()}}
    path=approval_root/'grant.json';path.write_text(json.dumps(grant))
    return root,path,root/record['case']/'candidate.json'


def export(home,record,**kwargs):
    root,grant,out=authorize(home,record)
    return candidates.export_candidates(home,root,grant,kwargs.get('actor',STAFF),[record['case']],out),out


def test_real_read_time_source_bound_correction_is_weak_and_immutable(observation):
    home,case,stored,path,record=observation
    provenance=record['source_provenance']
    source=home/'data'/'source'/'i94.pdf'
    assert provenance['state']=='verified'
    assert provenance['source_sha256']==hashlib.sha256(source.read_bytes()).hexdigest()
    assert provenance['reader_manifest_sha256']==digest(provenance['reader_manifest'])
    assert provenance['source_version'] and provenance['instance_id'] and provenance['boundary_fingerprint']
    assert provenance['original_range']==[0,0]
    assert record['read_normalized']=='11111111111' and record['final_value']=='22222222222'
    assert record['observation']=={'kind':'workflow_correction','adjudication':'pending','training_authorized':False,'evaluation_reference':False,'staff_seconds':None}
    original_record=path.read_bytes();source.write_bytes(source.read_bytes()+b'\nfictional source replacement')
    reader_examples.from_decision(case,stored)
    assert path.read_bytes()==original_record


def test_legacy_and_future_reads_never_receive_current_hash_or_manifest(observation):
    _,case,stored,_,_=observation
    src=json.loads((case/'fact_graph.json').read_text())['facts'][KEY]['sources'][0]
    legacy=copy.deepcopy(src);legacy['instance_id']=None;legacy['evidence_version']=None
    result=correction_provenance.retain(case,legacy,stored['at'],field_key=KEY)
    assert result['state']=='legacy_unverified' and result['source_sha256'] is None and result['reader_manifest'] is None
    newer=copy.deepcopy(src);newer['extracted_at']='2999-01-01T00:00:00+00:00'
    result=correction_provenance.retain(case,newer,stored['at'],field_key=KEY)
    assert result['state']=='stale' and result['source_sha256'] is None
    proof=copy.deepcopy(stored['evidence_confirmation']['keys'][KEY]);proof['proof']['evidence'][0]['source']['raw_value']='different read'
    assert correction_provenance.retain(case,src,stored['at'],proof,field_key=KEY)['state']=='stale'


def test_descriptive_intervention_rate_has_explicit_denominator_and_no_human_time(observation):
    *_,record=observation
    result=reader_examples.figures([record,dict(record,outcome='kept')])
    assert result['intervention']['numerator']==1 and result['intervention']['denominator']==2
    assert result['intervention']['rate']==.5 and result['intervention']['staff_seconds'] is None
    assert 'human_observed' in result['intervention']['human_time_input']
    assert result['independently_adjudicated_error_rate'] is None
    assert 'awaiting independent adjudication' in '\n'.join(reader_examples.render(result))


def test_many_weak_labels_never_propose_threshold_or_promotion(tmp_path):
    database=tmp_path/'learning.db';db=connect(database)
    db.executescript(labels.LABELS_SCHEMA)
    for i in range(30):
        doc=f'fictional-{i}.pdf'
        labels._write(db,'fictional',doc,['i94'],'filed','Fictional Reviewer')
        record_run(db,client='fictional',doc=doc,task='document_type',model='fictional-model',wording='fictional-v1',rules='i94',answer='i94',probability=.99,ms=250)
    db.close()
    result=report.document_type_report(database,threshold=.9)
    assert result['label_strength']=='workflow_observation' and result['independently_adjudicated'] is False
    assert result['threshold_promotion_allowed'] is False
    [model]=result['models']
    assert model['labelled']==30 and model['model_right']==30
    assert model['suggested_threshold'] is None and model['threshold_promotion_allowed'] is False
    assert model['model_workflow_agreement']==1
    assert 'tools/evaluate_corpus.py' in result['release_gate']


def test_explicit_export_is_private_own_case_references_only(observation):
    home,_,_,_,record=observation
    result,out=export(home,record)
    assert result['approved'] is False and result['training_authorized'] is False
    assert result['independently_adjudicated'] is False and result['measurement']['labor'] is None
    [row]=result['candidates']
    assert row['example_id']==record['id'] and row['source_provenance']==record['source_provenance']
    assert 'read_raw' not in row and 'final_value' not in row
    assert row['reviewer']=={k:record[k] for k in ('by','role','at')}
    assert list(out.parent.iterdir())==[out]
    with pytest.raises(FileExistsError):
        candidates.export_candidates(home,out.parent.parent,home/'data'/'evaluation-authorizations'/record['case']/'grant.json',STAFF,[record['case']],out)


def test_export_requests_private_modes_only_for_new_artifacts(observation,monkeypatch):
    home,_,_,_,record=observation
    real_open=candidates.os.open;real_mkdir=candidates.Path.mkdir;opens=[];folders=[]
    def opened(path,flags,mode=0o777,*args,**kwargs):
        if str(path).endswith('candidate.json'):opens.append((flags,mode))
        return real_open(path,flags,mode,*args,**kwargs)
    def mkdir(path,*args,**kwargs):
        if path.name=='evaluation-candidates' or path.parent.name=='evaluation-candidates':
            folders.append(kwargs.get('mode',args[0] if args else 0o777))
        return real_mkdir(path,*args,**kwargs)
    monkeypatch.setattr(candidates.os,'open',opened);monkeypatch.setattr(candidates.Path,'mkdir',mkdir)
    export(home,record)
    assert opens==[(os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)]
    assert folders==[0o700,0o700]


def test_effective_private_modes_on_supported_filesystem(observation):
    home,_,_,_,record=observation
    probe=home/'private-mode-probe';probe.mkdir(mode=0o700)
    fd=os.open(probe/'probe',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    file_mode=stat.S_IMODE((probe/'probe').stat().st_mode);directory_mode=stat.S_IMODE(probe.stat().st_mode)
    if os.name!='posix' or (file_mode,directory_mode)!=(0o600,0o700):
        pytest.skip(f'Workspace filesystem does not enforce Unix creation modes: file={file_mode:o}, directory={directory_mode:o}; Windows ACL isolation is unverified')
    _,out=export(home,record)
    assert stat.S_IMODE(out.stat().st_mode)==0o600
    assert stat.S_IMODE(out.parent.stat().st_mode)==0o700


@pytest.mark.parametrize('bad',['no_authorization','unapproved','revoked','wrong_firm','wrong_scope','multi_case','fake_evidence','unbound_examples','inactive_actor','inactive_attorney','denied_case'])
def test_export_requires_actual_structured_own_scope_and_current_permission(observation,monkeypatch,bad):
    home,case,_,_,record=observation
    root,grant_path,out=authorize(home,record)
    grant=json.loads(grant_path.read_text())
    cases=[record['case']]
    if bad=='no_authorization':grant_path=grant_path.with_name('missing.json')
    if bad=='unapproved':grant['approved']=False
    if bad=='revoked':grant['revoked']=True
    if bad=='wrong_firm':grant['firm_root']=str(home/'other-firm')
    if bad=='wrong_scope':grant['corpus_root']=str(home/'outside')
    if bad=='multi_case':cases.append('fictional-b')
    if bad=='fake_evidence':grant['approval_evidence']['sha256']='a'*64
    if bad=='unbound_examples':grant['allowed_examples'][0]['binding_sha256']='b'*64
    if bad in {'inactive_actor','inactive_attorney'}:
        users_path=home/'data'/'review_users.json';users=json.loads(users_path.read_text());users['users'][STAFF if bad=='inactive_actor' else ATTORNEY]['active']=False;users_path.write_text(json.dumps(users))
    if bad=='denied_case':
        (case/restricted.FILE).write_text(json.dumps({'marked':{'on':True},'people':[]}))
    if bad!='no_authorization':grant_path.write_text(json.dumps(grant))
    with pytest.raises((ValueError,PermissionError,OSError)):
        candidates.export_candidates(home,root,grant_path,STAFF,cases,out)
    assert not out.exists()


@pytest.mark.parametrize('bad',['missing_manifest','legacy','withdrawn','different_value','different_outcome','different_how','replaced_source'])
def test_candidate_held_when_observation_or_source_binding_is_incomplete(observation,bad):
    home,_,_,path,record=observation
    if bad=='missing_manifest':record['source_provenance']['reader_manifest']=None;record['source_provenance']['reader_manifest_sha256']=None
    if bad=='legacy':record['source_provenance']['state']='legacy_unverified'
    if bad=='withdrawn':record['undone']={'by':'Fictional Reviewer','at':clock.stamp()}
    path.write_text(json.dumps(record))
    root,grant,out=authorize(home,record)
    if bad=='different_value':record['final_value']='33333333333';path.write_text(json.dumps(record))
    if bad=='different_outcome':record['outcome']='kept';path.write_text(json.dumps(record))
    if bad=='different_how':record['how']='confirm';path.write_text(json.dumps(record))
    if bad=='replaced_source':
        original=home/'data'/'source'/'i94.pdf';original.write_bytes(original.read_bytes()+b'fictional replacement')
    with pytest.raises((ValueError,LookupError)):
        candidates.export_candidates(home,root,grant,STAFF,[record['case']],out)
    assert not out.exists()


@pytest.mark.parametrize('race',['same_size_mtime_source','boundary','symlink_example','revoked_actor'])
def test_final_gate_revalidates_sha_boundary_and_permissions(observation,monkeypatch,race):
    home,case,_,path,record=observation
    root,grant,out=authorize(home,record)
    real=candidates._authorization;calls=0
    def change_before_final(*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==2:
            if race=='same_size_mtime_source':
                original=home/'data'/'source'/'i94.pdf';before=original.stat();data=original.read_bytes();original.write_bytes(data[:-1]+bytes([data[-1]^1]));os.utime(original,ns=(before.st_atime_ns,before.st_mtime_ns))
            if race=='boundary':
                docs=json.loads((case/'documents.json').read_text());docs['boundary_plans']['i94.pdf']['instances'][0]['evidence_fingerprint']='changed';(case/'documents.json').write_text(json.dumps(docs))
            if race=='symlink_example':
                replacement=home/'different-observation.json';replacement.write_bytes(path.read_bytes());path.unlink();path.symlink_to(replacement)
            if race=='revoked_actor':
                users_path=home/'data'/'review_users.json';users=json.loads(users_path.read_text());users['users'][STAFF]['active']=False;users_path.write_text(json.dumps(users))
        return real(*args,**kwargs)
    monkeypatch.setattr(candidates,'_authorization',change_before_final)
    with pytest.raises((ValueError,LookupError,PermissionError)):
        candidates.export_candidates(home,root,grant,STAFF,[record['case']],out)
    assert not out.exists()


def test_case_scoped_purge_removes_own_candidate_approvals_and_keeps_other_bytes(observation):
    home,_case,_,_,record=observation
    export(home,record)
    other=[]
    for namespace in ('evaluation-authorizations','evaluation-candidates'):
        folder=home/'data'/namespace/'fictional-b';folder.mkdir(parents=True)
        path=folder/'other.json';path.write_bytes(b'fictional-other-client-history');other.append(path)
    (home/'data'/'clients'/'fictional-b').mkdir()
    result=purge.empty_stores(home/'data'/'clients',record['case'],who=purge.Identity(record['case'],[],set(),set(),set()))
    assert not (home/'data'/'evaluation-authorizations'/record['case']).exists()
    assert not (home/'data'/'evaluation-candidates'/record['case']).exists()
    assert all(path.read_bytes()==b'fictional-other-client-history' for path in other)
    counts={row['id']:row['removed'] for row in result['stores']}
    assert counts['evaluation_authorizations']==2 and counts['evaluation_candidates']==1


def test_cli_stale_source_refuses_cleanly_without_artifact(observation,capsys):
    from export_evaluation_candidates import main
    home,_,_,_,record=observation
    root,grant,out=authorize(home,record)
    original=home/'data'/'source'/'i94.pdf';original.write_bytes(original.read_bytes()+b'fictional source change')
    assert main(['--firm-root',str(home),'--corpus-root',str(root),'--authorization',str(grant),
                 '--actor',STAFF,'--cases',record['case'],'--out',str(out)])==2
    assert 'Candidate export refused' in capsys.readouterr().err
    assert not out.exists()


def test_runtime_cleanup_handles_legacy_basename_without_new_artifacts(tmp_path):
    import evaluation_artifacts
    data=tmp_path/'data';data.mkdir()
    assert evaluation_artifacts.remove_case(data,'Monte, Ficção')=={'evaluation_authorizations':0,'evaluation_candidates':0}
    with pytest.raises(ValueError):evaluation_artifacts.remove_case(data,'../other-client')


def test_cleanup_preflights_both_namespaces_and_refuses_symlink_without_other_deletion(observation):
    import evaluation_artifacts
    home,_,_,_,record=observation
    _,out=export(home,record)
    original=home/'data'/'evaluation-authorizations'/record['case']/'grant.json'
    outside=home/'other-client-artifact';outside.write_bytes(b'fictional other-client bytes')
    (out.parent/'unsafe-link').symlink_to(outside)
    with pytest.raises(ValueError):evaluation_artifacts.remove_case(home/'data',record['case'])
    assert original.exists() and out.exists() and outside.read_bytes()==b'fictional other-client bytes'


def test_cleanup_runtime_needs_only_product_src(tmp_path):
    data=tmp_path/'data';data.mkdir()
    script="import sys; import evaluation_artifacts; assert 'evaluation.corpus' not in sys.modules; assert evaluation_artifacts.remove_case(sys.argv[1], 'Legacy, Fictional') == {'evaluation_authorizations': 0, 'evaluation_candidates': 0}; assert 'evaluation.corpus' not in sys.modules"
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'),PYTHONDONTWRITEBYTECODE='1')
    result=subprocess.run([sys.executable,'-c',script,str(data)],cwd=tmp_path,env=env,text=True,capture_output=True,timeout=20,check=False)
    assert result.returncode==0,result.stderr


def retained_source(observation):
    _,case,stored,_,_=observation
    src=json.loads((case/'fact_graph.json').read_text())['facts'][KEY]['sources'][0]
    return case,stored,src


def test_healthy_field_version_and_decision_proof_remain_verified_without_current_manifest(observation,monkeypatch):
    import reader_manifest
    case,stored,src=retained_source(observation)
    monkeypatch.setattr(reader_manifest,'current',lambda *args: (_ for _ in ()).throw(AssertionError('No historical manifest backfill')))
    proof=stored['evidence_confirmation']['keys'][KEY]
    result=correction_provenance.retain(case,src,stored['at'],proof,field_key=KEY)
    assert result['state']=='verified' and result['reader_manifest']==src['read_manifest']
    assert result['source_version']==src['evidence_version'] and result['instance_id']==src['instance_id']


@pytest.mark.parametrize('binding',['evidence_version','subject_role','field_key'])
def test_stale_field_reader_or_role_binding_cannot_borrow_current_identity(observation,binding):
    case,stored,src=retained_source(observation)
    field_key=KEY
    if binding=='field_key':field_key='applicant.different_field'
    elif binding=='evidence_version':src[binding]=digest({'fictional':'older reader version'})
    else:src[binding]='parent'
    result=correction_provenance.retain(case,src,stored['at'],field_key=field_key)
    assert result['state']=='stale'
    assert result['source_sha256'] is None and result['boundary_fingerprint'] is None and result['reader_manifest'] is None


def test_changed_reader_code_version_holds_old_source_without_replacing_recorded_manifest(observation,monkeypatch):
    import subject_attribution
    case,stored,src=retained_source(observation)
    monkeypatch.setattr(subject_attribution,'reader_version',lambda dtype:digest({'fictional':'new reader implementation','type':dtype}))
    result=correction_provenance.retain(case,src,stored['at'],field_key=KEY)
    assert result['state']=='stale' and result['source_version']==src['evidence_version']
    assert result['reader_manifest'] is None and result['reader_manifest_sha256'] is None


def test_same_bytes_instance_and_range_cannot_backfill_changed_boundary(observation):
    import documents
    case,stored,src=retained_source(observation)
    data=documents.read(case);part=data['boundary_plans']['i94.pdf']['instances'][0]
    original_instance=part['instance_id'];part['evidence_fingerprint']=digest({'fictional':'later boundary evidence'})
    documents.save(case,data)
    assert part['instance_id']==original_instance==src['instance_id']
    for proof in (None,stored['evidence_confirmation']['keys'][KEY]):
        result=correction_provenance.retain(case,src,stored['at'],proof,field_key=KEY)
        assert result['state']=='stale' and result['boundary_fingerprint'] is None and result['source_sha256'] is None


@pytest.mark.parametrize('binding',['boundary_fingerprint','original_zero_based_inclusive_range','missing_boundary','wrong_instance','wrong_role'])
def test_decision_boundary_range_and_instance_proof_must_match_retained_read(observation,binding):
    case,stored,src=retained_source(observation)
    proof=copy.deepcopy(stored['evidence_confirmation']['keys'][KEY]);entry=proof['proof']['evidence'][0]
    if binding=='missing_boundary':entry.pop('boundary_fingerprint')
    elif binding=='wrong_instance':entry['source']['instance_id']='wrong-instance'
    elif binding=='wrong_role':entry['source']['subject_role']='parent'
    elif binding=='original_zero_based_inclusive_range':entry[binding]=[0,1]
    else:entry[binding]=digest({'fictional':'different decision boundary'})
    result=correction_provenance.retain(case,src,stored['at'],proof,field_key=KEY)
    assert result['state']=='stale' and result['boundary_fingerprint'] is None and result['original_range'] is None


def test_missing_stored_manifest_keeps_source_only_verification(observation):
    case,stored,src=retained_source(observation)
    src['read_manifest']=None
    result=correction_provenance.retain(case,src,stored['at'],field_key=KEY)
    assert result['state']=='verified' and result['source_sha256']
    assert result['reader_manifest'] is None and result['reader_manifest_sha256'] is None
    assert 'not recorded' in result['reason']


def test_healthy_reference_keeps_stored_role_and_verified_version(observation):
    from types import SimpleNamespace
    _,case,_,_,_=observation
    graph=__import__('factgraph').FactGraph.load(case/'fact_graph.json')
    old=graph.get(KEY).sources[0]
    comparison={'form':'fictional-reference','boxes':[{'key':KEY,'field':'fictional-i94','kind':'different','cause':'reader','ours':old.normalized_value,'reference':'33333333333'}]}
    [path]=reader_examples.from_reference(case.parent,SimpleNamespace(case=case.name,graph=graph),comparison)
    record=json.loads(path.read_text());provenance=record['source_provenance']
    assert provenance['state']=='verified' and provenance['source_version']==old.evidence_version
    assert provenance['instance_id']==old.instance_id and provenance['boundary_fingerprint']
    assert record['observation']['adjudication']=='pending'
