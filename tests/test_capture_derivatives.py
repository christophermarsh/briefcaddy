"""Fictional images and isolated records; notifications and readers disabled."""
import copy
import hashlib
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from communication_fixture import accepted_link, approve_client, installation
from fastapi.testclient import TestClient
from PIL import Image

from portal import capture_derivatives as capture
from portal import upload_recovery as recovery
from portal.app import create_app
from portal.store import PortalStore


def image(size=(1000, 1200), color='white'):
    out = io.BytesIO()
    Image.new('RGB', size, color).save(out, 'JPEG')
    return out.getvalue()


ORIGINAL = image()
DERIVATIVE = image((800, 960))
META = {'policy': capture.POLICY, 'source_dimensions': [1000, 1200],
        'corners': [[.1,.1],[.9,.1],[.9,.9],[.1,.9]], 'selection': 'corrected',
        'flags': ['blur'], 'override': True, 'sharpness': 5, 'saturated': .95}


@pytest.fixture
def store(tmp_path, monkeypatch):
    data = installation(tmp_path, monkeypatch)
    monkeypatch.setenv('PORTAL_READ_AT_ONCE', '0')
    monkeypatch.setenv('PORTAL_NOTIFY', '0')
    s = PortalStore(data / 'portal')
    (data / 'clients' / 'fictional-a').mkdir()
    s.add_client('fictional-a', 'Fictional Capture', email='capture@fictional.example', language='en')
    approve_client(s, 'fictional-a')
    return s


def accept(store, meta=META, original=ORIGINAL, derivative=DERIVATIVE):
    prepared = capture.prepare(original, json.dumps(meta), derivative)
    return recovery.accept(store, 'fictional-a', 'c'*32, 'passport', 'fictional.jpg', original, 'image/jpeg', lambda *args: 'tonight', capture=prepared, derivative=derivative)


def test_original_derivative_hashes_and_pipeline_evidence_remain_separate(store):
    assert accept(store)['status'] == 'complete'
    record = store.uploads('fictional-a')[0]
    evidence = record['capture']
    root = store.client_dir('fictional-a')
    assert (root / evidence['original_stored']).read_bytes() == ORIGINAL
    assert (root / evidence['derivative_stored']).read_bytes() == DERIVATIVE
    assert evidence['original_sha256'] == record['source_sha256'] == hashlib.sha256(ORIGINAL).hexdigest()
    assert evidence['derivative_sha256'] == hashlib.sha256(DERIVATIVE).hexdigest()
    from portal.store import image_to_pdf
    assert (root/'uploads'/record['stored']).read_bytes() == image_to_pdf(ORIGINAL)
    assert evidence['quality_review_required'] is True
    assert evidence['transform'] == 'client_reported_projective_crop'
    assert list((root/'uploads').iterdir()) == [root/'uploads'/record['stored']]
    assert accept(store)['id'] == record['id']
    assert len(store.uploads('fictional-a')) == 1


def test_metadata_and_derivative_bound_to_attempt(store):
    accept(store)
    changed = dict(META, sharpness=12)
    with pytest.raises(ValueError, match='upload_attempt_conflict'):
        accept(store, changed)
    with pytest.raises(ValueError, match='upload_attempt_conflict'):
        accept(store, derivative=image((800,960),'gray'))


@pytest.mark.parametrize('field,bad', [
    ('source_dimensions',[999,1200]), ('source_dimensions',[1000.0,1200]), ('source_dimensions',[True,1200]),
    ('policy','unknown'), ('selection','../../escape'), ('selection',[]), ('selection',{}), ('override',False), ('override',1),
    ('flags',['readable']), ('flags',['blur','blur']), ('flags',[{}]),
    ('sharpness',float('nan')), ('sharpness',float('inf')), ('sharpness',True), ('saturated',1.1),
    ('corners',[[0,0],[1,1],[1,0],[0,1]]), ('corners',[[0,0]]*4),
    ('corners',[[0,0],[1,0],[1,1],[-.1,1]]), ('corners',[[0,0],[1,0],[1,float('nan')],[0,1]]),
])
def test_malformed_metadata_is_rejected(field,bad):
    meta = copy.deepcopy(META);meta[field]=bad
    with pytest.raises(ValueError):
        capture.prepare(ORIGINAL,json.dumps(meta),DERIVATIVE)


@pytest.mark.parametrize('payload', ['[]', '{}', '{broken', 'x'*4097, '{"sha256":"invented"}'])
def test_unknown_or_oversized_metadata_rejected(payload):
    with pytest.raises(ValueError):capture.prepare(ORIGINAL,payload,DERIVATIVE)


def test_wrong_derivative_dimensions_format_and_missing_attachment_rejected():
    for derivative in (None,b'not-an-image',image((50,50)),image((1601,1100)),image((3000,3000))):
        with pytest.raises(ValueError):capture.prepare(ORIGINAL,json.dumps(META),derivative)
    with pytest.raises(ValueError):capture.prepare(b'broken',json.dumps(META),DERIVATIVE)
    with pytest.raises(ValueError):capture.prepare(image((4000,4000)),json.dumps(META),DERIVATIVE)
    with pytest.raises(ValueError):capture.prepare(ORIGINAL,json.dumps(dict(META,selection='original')),DERIVATIVE)
    pixel_meta=dict(META,source_dimensions=[1,1],corners=[[.1,.1],[.4,.1],[.4,.9],[.1,.9]])
    with pytest.raises(ValueError,match='invalid_capture_dimensions'):
        capture.prepare(image((1,1)),json.dumps(pixel_meta),image((1,1)))


@pytest.mark.parametrize('stage', ['original','derivative','uploads.json','queue'])
def test_interruption_retries_preserve_originals_and_one_upload(store,monkeypatch,stage):
    real_replace=capture.os.replace;real_write=store._write;real_enqueue=store.enqueue
    def replace(source,target):
        if str(target).endswith(f'-{stage if stage=="original" else "corrected"}.image') and stage in {'original','derivative'}:
            raise OSError('fictional interrupted capture')
        return real_replace(source,target)
    def write(path,data):
        if path.name==stage:raise OSError('fictional record interruption')
        return real_write(path,data)
    monkeypatch.setattr(capture.os,'replace',replace);monkeypatch.setattr(store,'_write',write)
    if stage=='queue':monkeypatch.setattr(store,'enqueue',lambda *args: (_ for _ in ()).throw(OSError('fictional queue interruption')))
    with pytest.raises(OSError):accept(store)
    monkeypatch.setattr(capture.os,'replace',real_replace);monkeypatch.setattr(store,'_write',real_write);monkeypatch.setattr(store,'enqueue',real_enqueue)
    assert accept(store)['status']=='complete'
    assert len(store.uploads('fictional-a'))==1
    assert capture.verify(store,'fictional-a',store.uploads('fictional-a')[0]['capture'])


@pytest.mark.parametrize('field', ['original','derivative'])
def test_damaged_retained_evidence_never_repaired_by_overwrite(store,field):
    accept(store);record=store.uploads('fictional-a')[0]
    path=store.client_dir('fictional-a')/record['capture'][field+'_stored'];path.write_bytes(b'fictional tampering')
    with pytest.raises(ValueError,match='upload_receipt_damaged'):accept(store)
    with pytest.raises(ValueError,match='upload_receipt_damaged'):recovery.outcome(store,'fictional-a','c'*32,lambda *args:'tonight')
    assert path.read_bytes()==b'fictional tampering'


def test_ordinary_file_fallback_and_direct_images_retain_raw_original(store):
    recovery.accept(store,'fictional-a','d'*32,'passport','ordinary.jpg',ORIGINAL,'image/jpeg',lambda *args:'tonight')
    direct=store.add_upload('fictional-a','passport','direct.jpg',ORIGINAL,'image/jpeg')
    for record in store.uploads('fictional-a'):
        evidence=record['capture'];assert evidence['policy']=='original-file-v1'
        assert evidence['client_reported_quality']=={'assessment':'not_run'}
        assert (store.client_dir('fictional-a')/evidence['original_stored']).read_bytes()==ORIGINAL
        assert evidence['derivative_stored'] is None
    assert direct['capture']['quality_review_required']


def test_http_authenticated_capture_and_invalid_metadata_without_side_effects(store):
    app=create_app(store.root,secure_cookies=False)
    files={'file':('fictional.jpg',ORIGINAL,'image/jpeg'),'derivative':('fictional-crop.jpg',DERIVATIVE,'image/jpeg')}
    data={'doc_id':'passport','attempt':'c'*32,'capture_metadata':json.dumps(META)}
    with TestClient(app) as browser:
        assert browser.post('/api/upload',headers={'X-Portal':'1'},data=data,files=files).status_code==401
        browser.get('/l/'+accepted_link(store,'fictional-a'))
        bad=browser.post('/api/upload',headers={'X-Portal':'1'},data=dict(data,capture_metadata='{"bad":true}'),files=files)
        assert bad.status_code==415 and store.uploads('fictional-a')==[]
        assert not (store.client_dir('fictional-a')/'capture-evidence').exists()
        good=browser.post('/api/upload',headers={'X-Portal':'1'},data=data,files=files)
        assert good.status_code==200,good.text
        assert len(store.uploads('fictional-a'))==1
        assert browser.post('/api/upload',headers={'X-Portal':'1'},data=data,files=files).status_code==200
        store.end_sessions('fictional-a')
        assert browser.post('/api/upload',headers={'X-Portal':'1'},data=dict(data,attempt='e'*32),files=files).status_code==401
        assert len(store.uploads('fictional-a'))==1


def test_legacy_image_receipt_replay_through_protected_http(store):
    recovery.accept(store,'fictional-a','f'*32,'passport','ordinary.jpg',ORIGINAL,'image/jpeg',lambda *args:'tonight')
    row=store.uploads('fictional-a')[0]
    historical=dict(row);historical.pop('capture')
    store.update_uploads('fictional-a',[historical])
    receipt_path=recovery._path(store,'fictional-a','f'*32)
    receipt=store._read(receipt_path,None);receipt.pop('capture_binding');receipt['record']=historical;store._write(receipt_path,receipt)
    app=create_app(store.root,secure_cookies=False)
    with TestClient(app) as browser:
        browser.get('/l/'+accepted_link(store,'fictional-a'))
        result=browser.post('/api/upload',headers={'X-Portal':'1'},data={'doc_id':'passport','attempt':'f'*32},files={'file':('ordinary.jpg',ORIGINAL,'image/jpeg')})
        assert result.status_code==200,result.text
        assert store.uploads('fictional-a')==[historical]
        explicit=browser.post('/api/upload',headers={'X-Portal':'1'},data={'doc_id':'passport','attempt':'f'*32,'capture_metadata':json.dumps(META)},files={'file':('ordinary.jpg',ORIGINAL,'image/jpeg'),'derivative':('crop.jpg',DERIVATIVE,'image/jpeg')})
        assert explicit.status_code==409


def test_case_closure_during_capture_decode_prevents_durable_evidence(store,monkeypatch):
    original_prepare=capture.prepare
    def close_after_prepare(*args,**kwargs):
        result=original_prepare(*args,**kwargs)
        store.save_engagement('fictional-a',{'ended':{'at':'2026-10-05T00:00:00+00:00','reason':'fictional closure'}})
        return result
    monkeypatch.setattr(capture,'prepare',close_after_prepare)
    with TestClient(create_app(store.root,secure_cookies=False)) as browser:
        browser.get('/l/'+accepted_link(store,'fictional-a'))
        result=browser.post('/api/upload',headers={'X-Portal':'1'},data={'doc_id':'passport','attempt':'e'*32,'capture_metadata':json.dumps(META)},files={'file':('fictional.jpg',ORIGINAL,'image/jpeg'),'derivative':('crop.jpg',DERIVATIVE,'image/jpeg')})
        assert result.status_code in {401,409}
    assert store.uploads('fictional-a')==[]
    assert not (store.client_dir('fictional-a')/'capture-evidence').exists()


def test_upload_outcome_repairs_retakes_under_authority_gate_then_revocation_denies(store,monkeypatch):
    store.save_tasks('fictional-a',[{'id':'fictional-retake','kind':'retake','doc_id':'passport','text':'Fictional retake'}])
    real_write=store._write
    def fail_record(path,data):
        if path.name=='uploads.json':raise OSError('fictional interruption before reconciliation')
        return real_write(path,data)
    monkeypatch.setattr(store,'_write',fail_record)
    with pytest.raises(OSError):accept(store)
    monkeypatch.setattr(store,'_write',real_write)
    assert not store.tasks('fictional-a')[0].get('received_at')
    entered=threading.Event();release=threading.Event();revoking=threading.Event()
    real_outcome=recovery.outcome
    def paused_outcome(*args,**kwargs):
        entered.set()
        assert release.wait(5)
        return real_outcome(*args,**kwargs)
    monkeypatch.setattr(recovery,'outcome',paused_outcome)
    with TestClient(create_app(store.root,secure_cookies=False)) as browser:
        browser.get('/l/'+accepted_link(store,'fictional-a'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            response=pool.submit(browser.get,'/api/upload-outcome',params={'attempt':'c'*32})
            assert entered.wait(5)
            def revoke():
                revoking.set();store.end_sessions('fictional-a')
            revoked=pool.submit(revoke)
            assert revoking.wait(5)
            assert not revoked.done()
            release.set()
            assert response.result(timeout=10).status_code==200
            revoked.result(timeout=10)
        assert store.tasks('fictional-a')[0].get('received_at')
        assert len(store.uploads('fictional-a'))==1
        denied=browser.get('/api/upload-outcome',params={'attempt':'c'*32})
        assert denied.status_code==401


def test_common_twelve_megapixel_file_fallback_preserves_immutable_original_http(store):
    phone_original=image((4032,3024),'#dddddd')
    with TestClient(create_app(store.root,secure_cookies=False)) as browser:
        browser.get('/l/'+accepted_link(store,'fictional-a'))
        result=browser.post('/api/upload',headers={'X-Portal':'1'},data={'doc_id':'passport','attempt':'a'*32},files={'file':('fictional-phone.jpg',phone_original,'image/jpeg')})
        assert result.status_code==200,result.text
    row=store.uploads('fictional-a')[0]
    assert row['capture']['source_dimensions']==[4032,3024]
    assert (store.client_dir('fictional-a')/row['capture']['original_stored']).read_bytes()==phone_original
    assert row['source_sha256']==hashlib.sha256(phone_original).hexdigest()
    with pytest.raises(ValueError,match='capture_image_too_large'):
        capture.original_attachment(image((5000,5000)))


@pytest.mark.parametrize('selection',[[],{}])
def test_malformed_selector_is_http_refusal_without_any_evidence_write(store,selection):
    with TestClient(create_app(store.root,secure_cookies=False)) as browser:
        browser.get('/l/'+accepted_link(store,'fictional-a'))
        result=browser.post('/api/upload',headers={'X-Portal':'1'},data={'doc_id':'passport','attempt':'a'*32,'capture_metadata':json.dumps(dict(META,selection=selection))},files={'file':('fictional.jpg',ORIGINAL,'image/jpeg'),'derivative':('crop.jpg',DERIVATIVE,'image/jpeg')})
        assert result.status_code==415
    assert store.uploads('fictional-a')==[]
    assert not (store.client_dir('fictional-a')/'capture-evidence').exists()
