"""Fictional protected HTTP lock timeout responses; no host concurrency claim."""
from contextlib import contextmanager
import json

import pytest

from test_prospects import firm, server, call  # noqa: F401
from test_staff_consent_http import client

# Imported pytest fixture names deliberately match injected arguments.
# ruff: noqa: F811


@pytest.mark.parametrize('route,mutation', [("/api/items", False), ("/api/engagement", True)])
def test_lock_timeout_is_generic_busy_not_a_missing_client_or_success(server, firm, monkeypatch, route, mutation):
    import portal.communication_consent as consent
    cid = client(server, name="Fictional Busy Case")
    folder = firm['clients'] / cid
    before = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    @contextmanager
    def busy(*args, **kwargs):
        raise TimeoutError('/private/fictional/data/gate.lock internal lock detail')
        yield
    monkeypatch.setattr(consent, 'data_gate', busy)
    body = {'client': cid, 'action': 'file_policy_facts', 'expected_revision': 0, 'facts': {}} if mutation else None
    status, response = call(server, 'sam', route if mutation else route + '?client=' + cid, body)
    response = json.loads(response)
    assert status == 503 and response['busy'] is True
    assert 'trying again' in response['error'].lower() and 'reread' in response['error'].lower()
    assert not any(value in str(response) for value in ['private', 'gate.lock', cid])
    assert {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob('*') if p.is_file()} == before
    # Ordinary read-only polls retain their own authoritative response.
    assert call(server, 'sam', '/api/case-list?q=Fictional')[0] == 200


def test_existing_case_reader_busy_keeps_409_contract(server, firm, monkeypatch):
    import jobs
    cid = client(server, name="Fictional Reader Busy Case")
    @contextmanager
    def busy(*args, **kwargs):
        raise jobs.CaseBusy("The case is being read; try again after the reader finishes.")
        yield
    monkeypatch.setattr(server['app'], 'case_write', busy)
    status, response = call(server, 'sam', '/api/engagement', {'client': cid, 'action': 'file'})
    assert status == 409 and 'being read' in json.loads(response)['error']


def test_signed_out_actor_is_denied_before_busy_state(server, firm, monkeypatch):
    import portal.communication_consent as consent
    cid = client(server, name="Fictional Signed Out Busy Case")
    called = []
    @contextmanager
    def busy(*args, **kwargs):
        called.append(True)
        raise TimeoutError('fictional gate')
        yield
    monkeypatch.setattr(consent, 'data_gate', busy)
    server['accounts'].sign_out(server['sam'].split('=', 1)[1])
    status, _ = call(server, 'sam', '/api/engagement', {'client': cid, 'action': 'file'})
    assert status == 401 and called == []


def test_body_timeout_does_not_claim_rollback_or_automatically_retry(server, firm, monkeypatch):
    cid = client(server, name="Fictional Partially Written Timeout Case")
    partial = firm['clients'] / cid / 'fictional-partial-effect.txt'
    calls = []
    def interrupted(*args, **kwargs):
        calls.append(True)
        partial.write_text('Fictional partial effect requiring current reread')
        raise TimeoutError('fictional body timeout after partial effect')
    monkeypatch.setattr(server['app'], 'engagement_change', interrupted)
    status, response = call(server, 'sam', '/api/engagement', {'client': cid, 'action': 'file'})
    value = json.loads(response)
    assert status == 503 and value['busy'] is True
    assert value['error'] == 'The operation timed out. Reread current state before trying again.'
    assert partial.read_text() == 'Fictional partial effect requiring current reread' and calls == [True]
