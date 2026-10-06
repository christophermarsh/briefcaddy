"""Actual Chromium neutral confirmation and separate sign-in; fictional only."""
import os
import re
from pathlib import Path

import pytest

from portal import app, communication_consent as consent, contact_control as control
from portal.notify import Notifier
from test_client_consent_browser import local_server, chromium as consent_chromium, pytestmark as browser_mark
from test_communication_consent import firm as consent_firm, reviewed, STAFF

pytestmark = browser_mark


@pytest.fixture(scope="module")
def chromium():
    yield from consent_chromium.__wrapped__()


@pytest.fixture
def firm(tmp_path, monkeypatch):
    monkeypatch.setenv("I485_PROSPECTS", str(tmp_path / "fictional-consent-firm/data/prospects"))
    return consent_firm.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("channel,width", [("sms", 390), ("whatsapp", 390), ("sms", 1280), ("whatsapp", 1280)])
def test_real_browser_first_phone_signoff_neutral_confirmation_then_separate_signin(firm, monkeypatch, chromium, channel, width):
    scope, store, client = firm
    reviewed(firm)
    token = consent.manual_bootstrap(*firm, actor_email=STAFF)
    calls = []
    def provider(self, destination, subject, body, kind):
        calls.append({"destination": destination, "body": body, "kind": kind})
        return "sent"
    monkeypatch.setattr(Notifier, "_" + channel, provider)
    context = chromium.new_context(viewport={"width": width, "height": 844})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        with local_server(scope.portal) as origin:
            page.goto(origin + "/l/" + token)
            page.wait_for_function("() => document.querySelector('#content')?.hidden === false")
            assert page.locator('input[name=channel]:checked').count() == 0
            assert page.locator(f'input[name=channel][value={channel}]').is_enabled()
            page.locator(f'input[name=channel][value={channel}]').check()
            page.locator('#typedName').fill("Fictional Client A")
            page.locator('#agree').check()
            page.locator('#save').click()
            page.wait_for_function("() => !document.querySelector('#save').disabled && !document.querySelector('#agree').checked")
            assert consent._record(*firm)["channels"][channel]["method"] == "client_portal"
            assert calls == [] and not control.status(*firm, channel)["verified"]
            notifier = Notifier(scope.portal / "outbox.jsonl", env={"PORTAL_BASE_URL": origin}, store=store, cases_root=scope.cases)
            result = notifier.verify_contact(store.profile(client), channel, actor_email=STAFF)
            assert result["credential_active"] and len(calls) == 1 and calls[0]["kind"] == "verify_contact"
            verify_token = re.search(r"/l/([A-Za-z0-9_-]+)", calls[0]["body"]).group(1)
            before = (store.client_dir(client) / consent.FILE).read_bytes()
            page.goto(origin + "/l/" + verify_token)
            page.wait_for_function("() => document.querySelector('#verification')?.hidden === false")
            assert page.url == origin + "/consent"
            assert page.locator('#verifiedHeading').inner_text() == "Phone verified"
            assert store.profile(client)['phone'] in page.locator('#verifiedResult').inner_text()
            assert not page.locator('#choiceForm').is_visible() and not page.locator('#portalLink').is_visible()
            assert "do not need to save" in page.locator('#verificationNext').inner_text()
            assert len(calls) == 1 and (store.client_dir(client) / consent.FILE).read_bytes() == before
            cookie = next(row['value'] for row in context.cookies() if row['name'] == app.COOKIE)
            assert store.session_client(cookie) is None and store.session_client(cookie, consent_only=True) == client
            assert page.request.get(origin + "/api/me").status == 401
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            if os.environ.get("E2E_SHOTS"):
                shots = Path(os.environ["E2E_SHOTS"])
                shots.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(shots / f"phone-verified-{channel}-{width}.png"), full_page=True)
            page.locator('#requestSignIn').focus()
            page.keyboard.press('Enter')
            page.wait_for_function("() => document.querySelector('#status').textContent.startsWith('Request received')")
            assert len(calls) == 2 and calls[-1]['kind'] == 'invite'
            assert (store.client_dir(client) / consent.FILE).read_bytes() == before
            normal_token = re.search(r"/l/([A-Za-z0-9_-]+)", calls[-1]["body"]).group(1)
            page.goto(origin + "/l/" + normal_token)
            page.locator('#main h1').wait_for(state="visible")
            assert not page.locator('.signin').count()
            signed_cookie = next(row['value'] for row in context.cookies() if row['name'] == app.COOKIE)
            assert signed_cookie != cookie and store.session_client(signed_cookie) == client
            assert page.request.get(origin + "/api/me").status == 200
            assert page.evaluate("() => localStorage.length === 0 && sessionStorage.length === 0")
            if os.environ.get("E2E_SHOTS"):
                shots = Path(os.environ["E2E_SHOTS"])
                shots.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(shots / f"phone-{channel}-{width}.png"), full_page=True)
            assert page.request.post(origin + '/api/logout', headers={'X-Portal': '1'}).status == 200
            page.goto(origin + '/')
            page.locator('.signin input').fill(store.profile(client)['phone'])
            assert page.locator('#phone-channel').is_visible()
            page.locator('#phone-channel').select_option(channel)
            page.locator('.signin button').click()
            page.wait_for_function("() => document.querySelector('.signin .note')?.hidden === false")
            assert len(calls) == 3 and calls[-1]['kind'] == 'invite'
            assert (store.client_dir(client) / consent.FILE).read_bytes() == before
            assert not errors
    finally:
        context.close()
