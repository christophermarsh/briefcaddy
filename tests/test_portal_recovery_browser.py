"""390px Chromium fault probes with fictional API responses; E2E=1 opts in."""
import copy
import os
from pathlib import Path

from communication_fixture import installation, approve_client, accepted_link

import pytest
from fastapi.testclient import TestClient
from portal.app import create_app

pytestmark = pytest.mark.skipif(not os.environ.get("E2E"), reason="E2E=1 requests Chromium probes")


@pytest.fixture
def phone(tmp_path, monkeypatch, request):
    playwright = pytest.importorskip("playwright.sync_api")
    data = installation(tmp_path, monkeypatch)
    (data / "clients" / "fictional-a").mkdir()
    app = create_app(data / "portal", secure_cookies=False)
    store = app.state.store
    store.add_client("fictional-a", "Fictional Alpha", email="alpha@fictional.example", language="en")
    approve_client(store, "fictional-a")
    with TestClient(app) as client:
        client.get("/l/" + accepted_link(store, "fictional-a"))
        response = client.get("/api/me")
        assert response.status_code == 200
        state = response.json()
        html = client.get("/").text
    state["status"] = "started"
    with playwright.sync_playwright() as driver:
        browser = driver.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        faults = {"offline": False, "hold": [], "drop_upload": False, "upload_ids": set(), "submit": 0, "ended": False, "reject_upload": False, "ackless": False, "signatures": 0}
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        def route(route):
            request = route.request
            path = request.url.split("portal.test")[-1]
            if path == "/":
                return route.fulfill(status=200, content_type="text/html", body=html)
            if faults["ended"] and path != "/api/logout":
                return route.fulfill(status=401, json={"detail": "sign in again"})
            if faults["offline"]:
                return route.abort("internetdisconnected")
            if path == "/api/answers":
                changes = request.post_data_json
                if faults["hold"] is not None:
                    faults["hold"].append((route, copy.deepcopy(changes)))
                    return
                state["answers"].update(changes)
                if state.get("money") and "fw_income_work" in changes:
                    state["money"]["totals"]["income"] = changes["fw_income_work"]
                    state["money"]["figures"][0]["amount"] = changes["fw_income_work"]
            if path == "/api/language":
                state["language"] = request.post_data_json["language"]
            if path == "/api/submit":
                faults["submit"] += 1
            if path == "/api/fee-waiver-sign":
                faults["signatures"] += 1
            result = copy.deepcopy(state)
            if path == "/api/answers":
                result["accepted"] = [] if faults["ackless"] else list(request.post_data_json)
            if path == "/api/upload":
                if faults["reject_upload"]:
                    return route.fulfill(status=415, json={"detail": "PDF, JPG or PNG only"})
                faults["upload_ids"].add("one-logical-upload")
                result["upload_outcome"] = {"status": "complete", "received": True, "id": "one-logical-upload"}
                if faults["drop_upload"]:
                    return route.abort("connectionreset")  # commit before browser response loss
            if path.startswith("/api/upload-outcome"):
                result["upload_outcome"] = {"status": "complete", "received": True, "id": "one-logical-upload"}
            return route.fulfill(status=200, json=result)
        page.route("https://portal.test/**", route)
        page.goto("https://portal.test/")
        page.wait_for_function("() => S.data !== null")
        shots = Path(os.environ["E2E_SHOTS"]) if os.environ.get("E2E_SHOTS") else None
        if shots:
            shots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shots / (request.node.name + "-initial.png")), full_page=True)
        yield page, state, faults
        if shots:
            page.screenshot(path=str(shots / (request.node.name + "-final.png")), full_page=True)
        assert errors == []
        context.close(); browser.close()


@pytest.mark.parametrize("language", ["en", "pt", "es", "ht"])
def test_pending_survives_offline_navigation_language_and_session_cleanup(phone, language):
    page, state, faults = phone
    faults["hold"] = None
    page.evaluate("() => { S.lang = 'en'; S.welcomed = true; S.step = 0; render(); }")
    qid = page.evaluate("() => S.data.sections[0].questions.find(q => q.type === 'text').id")
    faults["offline"] = True
    page.locator("#q_" + qid).fill("Fictional pending answer")
    page.wait_for_function("() => recovery.failed")
    assert page.evaluate("() => recovery.pending.size") == 1
    page.evaluate("() => { go(1); }")
    page.wait_for_function("() => S.step === 1")
    page.evaluate("language => { S.lang = language; render(); }", language)
    assert page.evaluate("qid => S.data.answers[qid]", qid) == "Fictional pending answer"
    assert page.locator("#recovery").is_visible()
    assert page.locator("#recovery button").count() == 2
    assert page.evaluate("() => Object.keys(localStorage).every(k => k === 'lang')")
    faults["offline"] = False
    page.locator("#recovery button").first.click()
    page.wait_for_function("() => recovery.pending.size === 0")
    assert state["answers"][qid] == "Fictional pending answer"
    page.evaluate("() => { rememberAnswer('dob', '2000-01-01', false); clearRecovery(); }")
    assert page.evaluate("() => recovery.pending.size + recovery.uploads.size") == 0


def test_second_edit_waits_for_first_reply_and_keeps_latest(phone):
    page, state, faults = phone
    page.evaluate("() => { rememberAnswer('dob', '2000-01-01', false); flushAnswers().catch(() => {}); }")
    page.wait_for_function("() => recovery.running !== null")
    page.wait_for_timeout(40)
    page.evaluate("() => rememberAnswer('dob', '2001-02-03', false)")
    assert len(faults["hold"]) == 1  # reversed answer replies cannot exist: one request at a time
    first, changes = faults["hold"].pop(0)
    state["answers"].update(changes)
    first.fulfill(status=200, json={**copy.deepcopy(state), "accepted": list(changes)})
    page.wait_for_timeout(80)
    assert page.evaluate("() => S.data.answers.dob") == "2001-02-03"
    second, changes = faults["hold"].pop(0)
    state["answers"].update(changes)
    second.fulfill(status=200, json={**copy.deepcopy(state), "accepted": list(changes)})
    page.wait_for_function("() => recovery.pending.size === 0")
    assert page.evaluate("() => S.data.answers.dob") == "2001-02-03"


def test_late_snapshot_cannot_replace_newer_answer(phone):
    page, state, faults = phone
    faults["hold"] = None
    held = []
    page.route("https://portal.test/api/stale", lambda route: held.append(route))
    page.evaluate("() => { api('GET', '/api/stale').then(d => { S.data = d; }); }")
    page.wait_for_timeout(40)
    stale = copy.deepcopy(state)
    page.evaluate("() => { rememberAnswer('dob', '2002-03-04', false); flushAnswers().catch(() => {}); }")
    page.wait_for_function("() => recovery.pending.size === 0")
    held.pop().fulfill(status=200, json=stale)
    page.wait_for_timeout(40)
    assert page.evaluate("() => S.data.answers.dob") == "2002-03-04"


def test_lost_upload_response_retains_file_and_cancel_reports_commit(phone):
    page, state, faults = phone
    faults["drop_upload"] = True
    page.on("dialog", lambda dialog: dialog.accept())
    page.evaluate("() => { const file = new File(['%PDF-fictional'], 'fictional.pdf', {type:'application/pdf'}); const input = {files:[file]}; sendPhoto('passport', input, null); }")
    page.wait_for_function("() => recovery.uploads.size === 1 && ![...recovery.uploads.values()][0].busy")
    assert page.evaluate("() => [...recovery.uploads.values()][0].file.name") == "fictional.pdf"
    page.evaluate("() => { cancelUpload([...recovery.uploads.keys()][0]); }")
    page.wait_for_function("() => recovery.uploads.size === 0")
    assert len(faults["upload_ids"]) == 1


def test_401_clears_answer_and_file_memory(phone):
    page, state, faults = phone
    faults["ended"] = True
    page.evaluate("() => { rememberAnswer('dob', '2000-01-01', false); flushAnswers().catch(() => {}); }")
    page.wait_for_function("() => S.data === null")
    assert page.evaluate("() => recovery.pending.size + recovery.uploads.size") == 0


def test_refused_file_can_be_reselected_without_stale_blocker(phone):
    page, state, faults = phone
    page.on("dialog", lambda dialog: dialog.accept())
    faults["reject_upload"] = True
    page.evaluate("() => { const file = new File(['fictional-heic'], 'fictional.heic', {type:'image/heic'}); sendPhoto('passport', {files:[file]}, null); }")
    page.wait_for_function("() => recovery.uploads.size === 0")
    faults["reject_upload"] = False
    page.evaluate("() => { const file = new File(['%PDF-fictional'], 'fictional.pdf', {type:'application/pdf'}); sendPhoto('passport', {files:[file]}, null); }")
    page.wait_for_function("() => recovery.uploads.size === 0")
    assert len(faults["upload_ids"]) == 1


def test_unacknowledged_question_remains_unsaved(phone):
    page, state, faults = phone
    faults["hold"] = None; faults["ackless"] = True
    page.evaluate("() => { rememberAnswer('removed_question', 'fictional pending', false); flushAnswers().catch(() => {}); }")
    page.wait_for_function("() => recovery.failed")
    assert page.evaluate("() => recovery.pending.get('removed_question').value") == "fictional pending"
    assert not page.locator("#saved").evaluate("node => node.classList.contains('on')")


def test_pending_money_edit_requires_updated_preview_and_second_explicit_signature(phone):
    page, state, faults = phone
    faults["hold"] = None
    state["money"] = {"id": "fictional-waiver", "open": True, "locked": False, "complete": True, "drawn": False, "missing": [],
                      "totals": {"income": "100.00", "expense": "20.00", "difference": "80.00"},
                      "figures": [{"group": "income", "label": "Fictional income", "amount": "100.00"}],
                      "declaration": {"own": None, "en": "Fictional declaration for this transport probe."}, "sentence": {"en": "", "own": None},
                      "section": {"title": "Fictional waiver", "intro": "Fictional test only", "example": "Fictional example", "questions": [{"id": "fw_income_work", "label": "Fictional income"}]}}
    page.evaluate("money => { S.data.money = money; S.moneyViewed = true; openMoney(); }", copy.deepcopy(state["money"]))
    page.locator("#money-name").fill("Fictional Alpha")
    page.locator("#money-agree").check()
    page.locator("#q_fw_income_work").fill("200.00")
    page.locator("#money-sign-go").click()
    page.wait_for_function("() => recovery.pending.size === 0 && document.querySelector('#money-body').innerText.includes('figures or wording changed')")
    assert faults["signatures"] == 0
    assert "200.00" in page.locator("#money-totals").inner_text()
    assert not page.locator("#money-agree").is_checked()
    page.locator("#money-name").fill("Fictional Alpha")
    page.locator("#money-agree").check()
    page.locator("#money-sign-go").click()
    page.wait_for_timeout(50)
    assert faults["signatures"] == 1  # one explicitly authorized POST, never generic retry
