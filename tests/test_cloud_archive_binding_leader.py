"""Independent exact-byte handover probes, with retained fictional originals."""
import json
import zipfile

import pytest

import client_file
import client_file_policy as policy
import engagement
from test_cloud_policy_core_leader import ACTOR
from test_cloud_file_inventory_leader import own_case


def prepared_case(tmp_path, monkeypatch):
    case, portal, _ = own_case(tmp_path, monkeypatch,
        holds=[{'kind': 'preservation', 'description': 'Fictional active preservation hold', 'active': True}])
    (case / 'notes.json').write_text(json.dumps({'version': 1, 'notes': [{
        'id': 'n.1', 'text': 'Fictional client work-product note selected for handover.',
        'by': ACTOR['who'], 'at': '2026-10-05T12:00:00-04:00',
        'carried_from_prospect': 'SECRET-UNRELATED-CASE-POINTER', 'access': 'SECRET-INTERNAL-ACL'}]}))
    current = policy.view(case, portal)
    policy.save_recipient(case, current['revision'], current['snapshot_sha256'], {
        'name': 'Fictional Client Recipient', 'authority': 'client',
        'authority_evidence': 'Fictional identity checked in person',
        'method': 'in_person', 'destination': '', 'authority_reviewed': True}, portal_root=portal, **ACTOR)
    current = policy.view(case, portal)
    inventory = client_file.inventory(case, portal)
    assert not inventory['warnings']
    choices = [{'path': row['path'], 'sha256': row['sha256'], 'input_sha256': row['input_sha256'],
        'include': row['reviewable'], 'reason': 'Fictional explicit review: include own readable material' if row['reviewable'] else 'Unsupported format withheld for a reviewed alternative'}
        for row in inventory['entries']]
    policy.approve_inventory(case, current['revision'], current['snapshot_sha256'], inventory['snapshot_sha256'], choices,
        portal_root=portal, **ACTOR)
    binding = policy.handover_binding(case, portal)
    engagement.export_file(case, case.parent, portal_root=portal, expected_binding_sha256=policy.digest(binding), **ACTOR)
    archive = engagement.read(case)['file']
    engagement.approve_file(case, archive['sha256'], portal_root=portal,
        expected_binding_sha256=archive['binding_sha256'], **ACTOR)
    return case, portal, engagement.read(case)['file']


def receipt(case, portal, archive, day):
    return engagement.file_returned(case, day, 'in_person', portal_root=portal,
        sha256=archive['sha256'], receipt_reference='Fictional signed acknowledgement reference',
        expected_binding_sha256=archive['binding_sha256'], recipient_sha256=archive['binding']['recipient_sha256'], **ACTOR)


def test_protective_handover_contains_reviewed_notes_without_internal_pointer(tmp_path, monkeypatch):
    case, portal, archive = prepared_case(tmp_path, monkeypatch)
    assert policy.view(case, portal)['destruction']['state'] == 'held'
    path = engagement.file_path(case, case.parent, portal)
    with zipfile.ZipFile(path) as bundle:
        names = bundle.namelist()
        notes = bundle.read(next(name for name in names if name.endswith('/CASE-NOTES.txt')))
        assert b'Fictional client work-product note' in notes
        assert b'SECRET-UNRELATED-CASE-POINTER' not in notes and b'SECRET-INTERNAL-ACL' not in notes
        assert not any(name.endswith('/notes.json') or name.endswith('/client_file_policy.json') for name in names)
        manifest = bundle.read('manifest.json')
        assert b'SECRET-' not in manifest and b'staff-upload-receipts' not in manifest
    receipt(case, portal, archive, '2026-10-05')
    assert engagement.read(case)['file']['returned']['sha256'] == archive['sha256']
    assert policy.view(case, portal)['destruction']['state'] == 'held'


def test_changed_original_bytes_hold_download_and_receipt_without_erasing_approval(tmp_path, monkeypatch):
    case, portal, archive = prepared_case(tmp_path, monkeypatch)
    source = case / 'source' / 'certidao.pdf'
    source.write_bytes(source.read_bytes() + b'\n% fictional changed source generation\n')
    with pytest.raises(ValueError):
        engagement.file_path(case, case.parent, portal)
    with pytest.raises(ValueError):
        receipt(case, portal, archive, '2026-10-05')
    current = engagement.read(case)['file']
    assert current['approved'] == archive['approved'] and current['returned'] is None


def test_changed_recipient_holds_prior_archive_and_preserves_old_approval(tmp_path, monkeypatch):
    case, portal, archive = prepared_case(tmp_path, monkeypatch)
    current = policy.view(case, portal)
    policy.save_recipient(case, current['revision'], current['snapshot_sha256'], {
        'name': 'Different Fictional Successor', 'authority': 'successor_counsel',
        'authority_evidence': 'Different fictional client authorization', 'method': 'secure_transfer',
        'destination': 'Fictional separately arranged secure destination', 'authority_reviewed': True}, portal_root=portal, **ACTOR)
    with pytest.raises(ValueError):
        engagement.file_path(case, case.parent, portal)
    with pytest.raises(ValueError):
        receipt(case, portal, archive, '2026-10-05')
    assert engagement.read(case)['file']['approved'] == archive['approved']


@pytest.mark.parametrize('day', ['2026-10-04', '2026-10-06'])
def test_receipt_cannot_predate_current_approval_or_claim_future_delivery(tmp_path, monkeypatch, day):
    case, portal, archive = prepared_case(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        receipt(case, portal, archive, day)
    assert engagement.read(case)['file']['returned'] is None
