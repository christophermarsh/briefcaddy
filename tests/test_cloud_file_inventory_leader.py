"""Independent archive-boundary probes; all bytes and identities are fictional."""
import json
import shutil
from pathlib import Path

import pytest

import client_file
from test_cloud_policy_core_leader import selected_case


def own_case(tmp_path, monkeypatch, **facts):
    case, portal, current = selected_case(tmp_path, monkeypatch, **facts)
    # Migrate only this synthetic legacy source layout to a valid own-case
    # source directory. Preserve every retained original byte and source read.
    meta_path = case / 'meta.json'
    meta = json.loads(meta_path.read_text())
    original = Path(meta['source_folder'])
    target = case / 'source'
    shutil.copytree(original, target)
    meta['source_folder'] = str(target)
    meta_path.write_text(json.dumps(meta))
    selected, _, _ = client_file.gather(case, portal)
    assert any(entry.source == target / 'certidao.pdf' for entry in selected), 'Valid retained source must remain available'
    return case, portal, current


@pytest.mark.parametrize('relative', [
    'fact_graph_reviewed.json',
    'staff-upload-receipts/fictional-attempt.json',
    'staff-upload-receipts/fictional-internal.txt',
])
def test_registered_internal_record_never_becomes_client_source(tmp_path, monkeypatch, relative):
    case, portal, _ = own_case(tmp_path, monkeypatch)
    path = case / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'fictional_internal_secret': 'DO-NOT-RELEASE', 'related_case': 'other-fictional-case'}))
    catalog_path = case / 'documents.json'
    catalog = json.loads(catalog_path.read_text())
    catalog['documents'].append({'files': [relative], 'type': 'other', 'owner': 'applicant'})
    catalog_path.write_text(json.dumps(catalog))
    selected, _, _ = client_file.gather(case, portal)
    assert not any(entry.source == path for entry in selected), 'Internal record released through malformed source registration'


def test_source_pointer_cannot_export_sibling_case_with_no_source_metadata(tmp_path, monkeypatch):
    case, portal, _ = own_case(tmp_path, monkeypatch)
    other = case.parent / 'unrelated-fictional-case'
    other.mkdir()
    secret = other / 'unrelated-client-letter.txt'
    secret.write_text('FICTIONAL OTHER CASE; must never become this client archive material')
    meta_path = case / 'meta.json'
    meta = json.loads(meta_path.read_text())
    meta['source_folder'] = str(other)
    meta_path.write_text(json.dumps(meta))
    try:
        selected, _, _ = client_file.gather(case, portal)
    except (ValueError, PermissionError):
        return
    assert not any(entry.source == secret for entry in selected), 'Sibling case bytes became current-case sources'


@pytest.mark.parametrize('point_to_case_root', [False, True])
def test_source_pointer_cannot_relabel_internal_receipt_as_document(tmp_path, monkeypatch, point_to_case_root):
    case, portal, _ = own_case(tmp_path, monkeypatch)
    internal = case / 'staff-upload-receipts'
    internal.mkdir()
    secret = internal / 'fictional-internal.txt'
    secret.write_text('FICTIONAL INTERNAL RECEIPT; never client material')
    meta_path = case / 'meta.json'
    meta = json.loads(meta_path.read_text())
    meta['source_folder'] = str(case if point_to_case_root else internal)
    meta_path.write_text(json.dumps(meta))
    try:
        selected, _, _ = client_file.gather(case, portal)
    except (ValueError, PermissionError):
        return
    assert not any(entry.source == secret for entry in selected), 'Source pointer relabeled internal record as a document'
