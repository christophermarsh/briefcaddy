"""Paralegal-to-phone handover with no provider or notice-review setup."""
# ruff: noqa: F811 -- shared pytest fixtures
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from test_prospects import firm, server  # noqa: F401
from test_staff_consent_browser import staff_chromium  # noqa: F401
from test_staff_consent_http import client
from test_client_consent_browser import local_server

pytestmark = pytest.mark.skipif(not os.environ.get("E2E"), reason="E2E=1 enables Chromium")


def test_paralegal_prepares_questionnaire_and_records_permission(staff_chromium, server, firm, monkeypatch):
    cid = client(server, language="en", name="Fictional Phone Tester")
    (firm["data"] / "communication_notice.json").unlink()
    context = staff_chromium.new_context(viewport={"width": 1280, "height": 900})
    name, value = server["jane"].split("=", 1)
    context.add_cookies([{"name": name, "value": value, "url": server["base"]}])
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        with local_server(firm["portal"]) as origin:
            monkeypatch.setenv("PORTAL_BASE_URL", origin)
            page.goto(server["base"] + "/#all")
            row = page.locator(f'#client-rows tr.row[data-client="{cid}"]')
            row.locator("summary").click()
            row.get_by_role("button", name="Communication choices", exact=True).click()
            panel = page.locator(f'[data-communication="{cid}"]').first
            make = panel.get_by_role("button", name="Create questionnaire link", exact=True)
            expect(make).to_be_disabled()
            expect(panel.get_by_role("button", name="Review current client wording", exact=True)).to_be_hidden()
            panel.get_by_label("Client requested questionnaire access and recipient checked", exact=True).check()
            make.click()
            link = panel.get_by_label("Questionnaire access link", exact=True)
            expect(link).to_be_visible(timeout=15000)
            url = link.input_value()
            assert url.startswith(origin + "/l/")
            page.get_by_role("button", name="Refresh progress", exact=True).click()
            row = page.locator(f'#client-rows tr.row[data-client="{cid}"]')
            expect(row.locator(".stage")).to_have_text("Invited")
            expect(row.get_by_text("Personal link created", exact=True)).to_be_visible()
            row.locator("summary").click()
            row.get_by_role("button", name="Communication choices", exact=True).click()
            panel = page.locator(f'[data-communication="{cid}"]').first
            shot = Path(__file__).resolve().parents[1] / "data/phone_test_runtime/qa-staff-access.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shot))
            phone_context = staff_chromium.new_context(viewport={"width": 390, "height": 844})
            phone = phone_context.new_page()
            try:
                phone.goto(url)
                expect(phone.get_by_role("button", name="Open questionnaire", exact=True)).to_be_visible()
                assert phone.request.get(origin + "/api/me").status == 401
                phone.get_by_role("button", name="Open questionnaire", exact=True).click()
                phone.wait_for_url(origin + "/")
                assert phone.url == origin + "/"
                assert phone.request.get(origin + "/api/me").status == 200
                expect(phone.get_by_role("button", name="Let's start", exact=True)).to_be_visible()
                expect(phone.locator("#lang")).to_have_value("en")
                with phone.expect_response(lambda response: response.url.endswith("/api/language")) as switched:
                    phone.locator("#lang").select_option("pt")
                assert switched.value.status == 200
                expect(phone.locator("#lang")).to_have_value("pt")
                expect(phone.locator("html")).to_have_attribute("lang", "pt")
                assert phone.request.get(origin + "/api/me").json()["language"] == "pt"
                phone.reload()
                expect(phone.locator("#lang")).to_have_value("pt")
                assert phone.request.get(origin + "/api/me").status == 200
                expect(phone.locator("body")).not_to_contain_text("undefined")
                from portal.demo import answers
                assert phone.request.put(origin + "/api/answers", data=answers(), headers={"X-Portal": "1"}).status == 200
                assert phone.request.post(origin + "/api/submit", data={"agree": True, "signature": "Fictional Phone Tester"}, headers={"X-Portal": "1"}).status == 200
                phone.reload()
                download = phone.get_by_role("link", name="Baixar meu questionário (PDF)", exact=True)
                expect(download).to_be_visible()
                phone.route("**/api/questionnaire.pdf", lambda route: route.fulfill(status=401, content_type="application/json", body='{"detail":"sign in again"}'), times=1)
                download.click()
                expect(phone.get_by_role("alert")).to_contain_text("Não foi possível baixar o PDF")
                assert phone.url == origin + "/"
                with phone.expect_download() as downloaded:
                    with phone.expect_request(lambda request: request.url.endswith("/api/questionnaire.pdf")) as pdf_request:
                        download.click()
                assert downloaded.value.suggested_filename == "submitted-questionnaire.pdf"
                assert Path(downloaded.value.path()).read_bytes().startswith(b"%PDF-")
                assert pdf_request.value.resource_type == "fetch"
                assert phone.url == origin + "/"
                assert phone.request.get(origin + "/api/me").status == 200
                phone.get_by_text("Adicionar mais documentos", exact=True).click()
                expect(phone.get_by_text("Carteira de motorista ou ID estadual — frente", exact=True)).to_be_visible()
                expect(phone.get_by_text("Carteira de motorista ou ID estadual — verso", exact=True)).to_be_visible()
                phone.screenshot(path=str(shot.with_name("qa-phone-questionnaire.png")))
            finally:
                phone_context.close()
            panel.get_by_text("Permission for office messages", exact=True).click()
            panel.get_by_label("Permission for email", exact=True).check()
            save = panel.get_by_role("button", name="Save client permission", exact=True)
            expect(save).to_be_disabled()
            panel.get_by_label("Client agreed to messages through selected channels", exact=True).check()
            save.click()
            expect(panel.get_by_text("Client permission saved. Nothing was sent.", exact=True)).to_be_visible()
            assert not errors
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.reload()
            expect(page.locator(f'#client-rows tr.row[data-client="{cid}"]').get_by_role("link", name="Questionnaire PDF", exact=True)).to_be_visible()
    finally:
        context.close()
