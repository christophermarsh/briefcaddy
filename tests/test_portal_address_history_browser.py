"""Phone address-history editing against the real autosave API."""
# ruff: noqa: F811 -- shared pytest fixtures
import os

import pytest
from playwright.sync_api import expect

from test_prospects import firm, server  # noqa: F401
from test_staff_consent_browser import staff_chromium  # noqa: F401
from test_staff_consent_http import client
from test_staff_questionnaire_access import access
from test_client_consent_browser import local_server

pytestmark = pytest.mark.skipif(not os.environ.get("E2E"), reason="E2E=1 enables Chromium")


def test_multiple_addresses_survive_autosave_language_change_and_reload(staff_chromium, server, firm, monkeypatch):
    cid = client(server, language="pt")
    firm["store"].save_answers(cid, {"address_history": [{"street": "10 Fictional Street", "city": "Boston"}]})
    with local_server(firm["portal"]) as origin:
        monkeypatch.setenv("PORTAL_BASE_URL", origin)
        token = access(server, cid)["url"].rsplit("/", 1)[1]
        context = staff_chromium.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        try:
            assert context.request.post(origin + "/api/access-link", data={"token": token}, headers={"X-Portal": "1"}).status == 200
            page.goto(origin + "/#addresses")
            history = page.locator("#q_address_history")
            expect(history.locator(".entry")).to_have_count(1)
            with page.expect_response(lambda r: r.url.endswith("/api/answers")) as added:
                history.locator("button.add").click()
            assert added.value.status == 200
            expect(history.locator(".entry")).to_have_count(2)
            # The server stores meaningful answers, not the unfilled new row.
            assert len(firm["store"].answers(cid)["address_history"]) == 1
            second = history.locator(".entry").nth(1)
            with page.expect_response(lambda r: r.url.endswith("/api/answers")) as saved:
                second.locator("input").first.fill("20 Fictional Avenue")
            assert saved.value.status == 200
            with page.expect_response(lambda r: r.url.endswith("/api/answers")):
                history.locator("button.add").click()
            expect(history.locator(".entry")).to_have_count(3)
            with page.expect_response(lambda r: r.url.endswith("/api/language")):
                page.locator("#lang").select_option("en")
            expect(history.locator(".entry")).to_have_count(3)
            with page.expect_response(lambda r: r.url.endswith("/api/answers")):
                history.locator(".entry").nth(2).locator("input").first.fill("30 Fictional Road")
            assert len(firm["store"].answers(cid)["address_history"]) == 3
            page.reload()
            expect(history.locator(".entry")).to_have_count(3)
            expect(history.locator(".entry").nth(1).locator("input").first).to_have_value("20 Fictional Avenue")
            with page.expect_response(lambda r: r.url.endswith("/api/answers")):
                history.locator(".entry").nth(1).get_by_role("button", name="Remove", exact=True).click()
            expect(history.locator(".entry")).to_have_count(2)
            assert [row["street"] for row in firm["store"].answers(cid)["address_history"]] == ["10 Fictional Street", "30 Fictional Road"]
        finally:
            context.close()


def test_foreign_country_warning_appears_and_clears_without_losing_fields(staff_chromium, server, firm, monkeypatch):
    cid = client(server, language="pt")
    firm["store"].save_answers(cid, {"last_foreign_address": {"street": "10 Fictional Street", "country": "United States"}})
    with local_server(firm["portal"]) as origin:
        monkeypatch.setenv("PORTAL_BASE_URL", origin)
        token = access(server, cid)["url"].rsplit("/", 1)[1]
        context = staff_chromium.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        try:
            assert context.request.post(origin + "/api/access-link", data={"token": token}, headers={"X-Portal": "1"}).status == 200
            page.goto(origin + "/#addresses")
            field = page.locator('[data-question="last_foreign_address"]')
            expect(field.get_by_role("alert")).to_contain_text("fora dos Estados Unidos")
            expect(field.locator(".hint")).to_contain_text("Não coloque um endereço dos EUA aqui")
            with page.expect_response(lambda r: r.url.endswith("/api/answers")):
                field.get_by_label("País", exact=True).fill("EUA")
            expect(field.get_by_role("alert")).to_contain_text("Confira esta resposta")
            with page.expect_response(lambda r: r.url.endswith("/api/answers")):
                field.get_by_label("País", exact=True).fill("Brasil")
            expect(field.get_by_role("alert")).to_have_count(0)
            expect(field.get_by_label("Rua e número", exact=True)).to_have_value("10 Fictional Street")
            assert firm["store"].answers(cid)["last_foreign_address"]["country"] == "Brasil"
        finally:
            context.close()


def test_corrected_second_job_dates_clear_warning_and_save(staff_chromium, server, firm, monkeypatch):
    cid = client(server, language="en")
    firm["store"].save_answers(cid, {"job_history": [
        {"employer": "Fictional First Job", "date_from": "2022-03-05", "date_to": "2024-03-31"},
        {"employer": "Fictional Second Job", "date_from": "2025-06-02", "date_to": "2026-03-31"}]})
    with local_server(firm["portal"]) as origin:
        monkeypatch.setenv("PORTAL_BASE_URL", origin)
        token = access(server, cid)["url"].rsplit("/", 1)[1]
        context = staff_chromium.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        try:
            assert context.request.post(origin + "/api/access-link", data={"token": token}, headers={"X-Portal": "1"}).status == 200
            page.goto(origin + "/#work")
            field = page.locator('[data-question="job_history"]')
            expect(field.locator(".entry")).to_have_count(2)
            dates = field.locator(".entry").nth(1).locator('input[type="date"]')
            with page.expect_response(lambda r: r.url.endswith("/api/answers")) as invalid:
                dates.first.fill("2027-06-02")
            assert invalid.value.json()["errors"]["job_history"] == "dates_backwards"
            expect(field.get_by_role("alert")).to_contain_text("end date is before")
            with page.expect_response(lambda r: r.url.endswith("/api/answers")) as corrected:
                dates.first.fill("2025-06-02")
            assert "job_history" in corrected.value.json()["accepted"]
            expect(field.get_by_role("alert")).to_have_count(0)
            expect(dates.last).to_have_value("2026-03-31")
            stored = firm["store"].answers(cid)["job_history"][1]
            assert (stored["date_from"], stored["date_to"]) == ("2025-06-02", "2026-03-31")
            page.reload()
            expect(field.get_by_role("alert")).to_have_count(0)
            expect(field.locator(".entry").nth(1).locator('input[type="date"]').first).to_have_value("2025-06-02")
        finally:
            context.close()
