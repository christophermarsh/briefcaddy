"""Actual staff neutral verification controls and separate client phone access."""
# ruff: noqa: F811 -- canonical fixtures are intentionally injected by name
from datetime import timedelta
import os
from pathlib import Path
import re

import pytest
from playwright.sync_api import expect

from portal.notify import Notifier
from portal.store import _now
from test_prospects import firm, server, ok  # noqa: F401 -- fixtures
from test_staff_consent_http import client, wording, retain
from test_staff_consent_browser import staff_chromium, staff_page, panel_for  # noqa: F401 -- fixture
from test_client_consent_browser import local_server

pytestmark = pytest.mark.skipif(not os.environ.get("E2E"), reason="E2E=1 requests real current phone staff controls")
SHOTS = Path(__file__).resolve().parents[1] / "docs/research/cloud_family_evidence/shots/contact22"


def phone_grant(srv, firm, cid, channel):
    wording(srv)
    profile = firm["store"].profile(cid)
    ok(srv, "jane", "/api/communication", {"client": cid, "action": "contact", "contacts": {"email": profile["email"], "phone": "+15550109876"}})
    evidence = retain(srv, "/api/communication", {"client": cid, "kind": "consent"})
    ok(srv, "jane", "/api/communication", {"client": cid, "action": "grant", "channel": channel, "evidence": evidence,
        "client_approved_at": (_now() - timedelta(minutes=1)).isoformat(), "notice_version": "fictional-caller-notice-v1", "language": "en",
        "source_kind": "in_person", "approval_description": "Fictional actual own phone-channel client approval"})


def capture(page, panel, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    expect(page.locator("#toast")).to_be_hidden(timeout=15000)
    panel.evaluate("el => el.scrollIntoView({block:'start'})")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(SHOTS / (name + "-viewport.png")))
    panel.screenshot(path=str(SHOTS / (name + "-panel.png")))


@pytest.mark.parametrize("channel,width", [("sms", 1000), ("whatsapp", 1400)])
def test_staff_neutral_phone_then_limited_confirmation_separate_invite_and_revocation(staff_chromium, server, firm, monkeypatch, channel, width):
    cid = client(server)
    phone_grant(server, firm, cid, channel)
    calls = []
    monkeypatch.setattr(Notifier, "_" + channel, lambda self, to, subject, body, kind: calls.append((body, kind)) or "sent")
    context, page, errors = staff_page(staff_chromium, server, width)
    phone_context = staff_chromium.new_context(viewport={"width": 390, "height": 844})
    phone = phone_context.new_page()
    try:
        with local_server(firm["portal"]) as origin:
            monkeypatch.setenv("PORTAL_BASE_URL", origin)
            panel = panel_for(page, cid)
            label = "Send neutral text verification" if channel == "sms" else "Send neutral WhatsApp verification"
            expect(panel.get_by_role("button", name=label, exact=True)).to_be_enabled()
            expect(panel.get_by_role("button", name="Send separate access invitation", exact=True)).to_have_count(0)
            with page.expect_response(lambda r: r.url.endswith("/api/communication") and r.request.method == "POST") as sent:
                panel.get_by_role("button", name=label, exact=True).click()
            assert sent.value.status == 200 and sent.value.json()["verification"]["status"] == "sent"
            expect(panel.get_by_role("status")).to_contain_text("Dispatch alone does not verify")
            assert len(calls) == 1 and calls[0][1] == "verify_contact"
            token = re.search(r"/l/([A-Za-z0-9_-]+)", calls[0][0]).group(1)
            phone.goto(origin + "/l/" + token)
            expect(phone.locator("#verifiedHeading")).to_have_text("Phone verified")
            assert phone.request.get(origin + "/api/me").status == 401
            assert not phone.locator("#choiceForm").is_visible() and not phone.locator("#portalLink").is_visible()
            panel.get_by_role("button", name="Check current permission", exact=True).click()
            expect(panel.locator("[data-phone-control]")).to_contain_text("verified for the current phone")
            expect(panel.get_by_role("button", name="Send separate access invitation", exact=True)).to_be_visible()
            capture(page, panel, "staff-current-verified-" + channel + "-" + str(width))
            panel.get_by_role("button", name="Send separate access invitation", exact=True).click()
            expect(page.locator("#toast")).to_contain_text("Invitation", timeout=60000)
            assert len(calls) == 2 and calls[-1][1] == "invite"
            normal = re.search(r"/l/([A-Za-z0-9_-]+)", calls[-1][0]).group(1)
            phone.goto(origin + "/l/" + normal)
            expect(phone.locator("#main h1")).to_be_visible(timeout=60000)
            assert phone.request.get(origin + "/api/me").status == 200
            panel = panel_for(page, cid)
            panel.get_by_role("button", name="Revoke communication permission", exact=True).click()
            expect(panel.get_by_role("button", name=label, exact=True)).to_be_disabled(timeout=60000)
            assert phone.request.get(origin + "/api/me").status == 401
            capture(page, panel, "staff-revoked-phone-held-" + channel + "-" + str(width))
        assert not errors
    finally:
        phone_context.close(); context.close()


def test_local_unapproved_and_shared_phone_controls_are_held(staff_chromium, server, firm):
    cid = client(server)
    wording(server)
    own = firm["store"].profile(cid)
    ok(server, "jane", "/api/communication", {"client": cid, "action": "contact", "contacts": {"email": own["email"], "phone": "(555) 010-9876"}})
    context, page, errors = staff_page(staff_chromium, server)
    try:
        panel = panel_for(page, cid)
        expect(panel.get_by_role("button", name="Send neutral text verification", exact=True)).to_be_disabled()
        expect(panel.locator("[data-phone-control]")).to_contain_text("Office/manual follow-up")
        ok(server, "jane", "/api/communication", {"client": cid, "action": "contact", "contacts": {"email": own["email"], "phone": "+15550109876"}})
        other = client(server, name="Other Fictional Phone Person")
        second = firm["store"].profile(other)
        ok(server, "jane", "/api/communication", {"client": other, "action": "contact", "contacts": {"email": second["email"], "phone": "+15550109876"}})
        panel.get_by_role("button", name="Check current permission", exact=True).click()
        expect(panel.get_by_role("button", name="Send neutral text verification", exact=True)).to_be_disabled()
        view = ok(server, "sam", "/api/communication?client=" + cid)
        assert view["phone_access"] == "office_only" and not view["contact_control"]["sms"]["verified"]
        capture(page, panel, "staff-shared-phone-office-only")
        assert not errors
    finally:
        context.close()
