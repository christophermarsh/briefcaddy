"""Independent actual HTTP delivery-byte integrity probe, fictional files only."""
import hashlib
from pathlib import Path

import pytest

import client_file
import engagement
from test_cloud_file_policy_adapters import prepare
from test_prospects import firm, server, call  # noqa: F401
from test_staff_consent_http import client

# ruff: noqa: F811


@pytest.mark.parametrize('only_after_path_validation', [False, True])
def test_download_rejects_archive_replaced_after_path_validation(server, firm, monkeypatch, only_after_path_validation):
    cid = client(server, name='Fictional Exact Download Client')
    case = firm['clients'] / cid
    prepared = prepare(case)['file']
    target = engagement.exports_folder(case.parent) / prepared['name']
    original = client_file.capture
    changed = []
    validated = []
    original_case_file = server['app'].case_file

    def validated_path(*args, **kwargs):
        result = original_case_file(*args, **kwargs)
        validated.append(True)
        return result

    def replaced_before_capture(path, root, *args, **kwargs):
        if Path(path) == target and (not only_after_path_validation or validated):
            target.write_bytes(b'Fictional replacement: these bytes were never prepared or reviewed.')
            changed.append(True)
        return original(path, root, *args, **kwargs)

    monkeypatch.setattr(client_file, 'capture', replaced_before_capture)
    monkeypatch.setattr(server['app'], 'case_file', validated_path)
    status, raw = call(server, 'sam', '/api/case-file.zip?client=' + cid)
    assert changed, 'The actual protected download must reach byte capture'
    assert status != 200, 'Actual HTTP sent stable replacement bytes after validating a different archive'
    assert hashlib.sha256(target.read_bytes()).hexdigest() != prepared['sha256']
    assert engagement.read(case)['file']['approved']['sha256'] == prepared['sha256']
