"""Fictional submitted-copy authority during the final mutation/read gate."""
from contextlib import contextmanager

import pytest
from communication_fixture import accepted_link
from fastapi.testclient import TestClient
from test_capture_derivatives import store  # noqa: F401

from portal import questionnaire_pdf
from portal.app import create_app
from portal.store import PortalStore


@pytest.mark.parametrize('action',['revoke','close'])
def test_questionnaire_copy_revalidates_after_waiting_for_gate(store,monkeypatch,action):  # noqa: F811
    store.update_profile('fictional-a',status='submitted',submitted_at='2026-10-05T00:00:00+00:00')
    real_gate=PortalStore._communication_gate
    intercepted=False
    @contextmanager
    def interrupted_gate(self):
        nonlocal intercepted
        with real_gate(self):
            if not intercepted:
                intercepted=True
                if action=='revoke':store.end_sessions('fictional-a')
                else:store.save_engagement('fictional-a',{'ended':{'at':'2026-10-05T00:00:00+00:00'}})
            yield
    with TestClient(create_app(store.root,secure_cookies=False)) as browser:
        browser.get('/l/'+accepted_link(store,'fictional-a'))
        monkeypatch.setattr(PortalStore,'_communication_gate',interrupted_gate)
        result=browser.get('/api/questionnaire.pdf')
        assert result.status_code in {401,409}
        assert result.headers['content-type'].startswith('application/json')
    rows=(store.client_dir('fictional-a')/'events.jsonl').read_text()
    assert 'questionnaire_copy_downloaded' not in rows


def test_questionnaire_copy_revalidates_after_renderer_revokes_session(store,monkeypatch):  # noqa: F811
    store.update_profile('fictional-a',status='submitted',submitted_at='2026-10-05T00:00:00+00:00')
    def revoke_in_render(*args):
        store.end_sessions('fictional-a')
        return b'%PDF-fictional-private-copy'
    monkeypatch.setattr(questionnaire_pdf,'render',revoke_in_render)
    with TestClient(create_app(store.root,secure_cookies=False)) as browser:
        browser.get('/l/'+accepted_link(store,'fictional-a'))
        result=browser.get('/api/questionnaire.pdf')
        assert result.status_code==401
        assert b'fictional-private-copy' not in result.content
    assert 'questionnaire_copy_downloaded' not in (store.client_dir('fictional-a')/'events.jsonl').read_text()
