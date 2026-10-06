"""Opt-in actual Chromium + real consent HTTP handlers on fictional stores.

An ephemeral loopback server serves the real app; no provider, production data,
installed service or external network is used. This does not test deployed TLS.
"""
# ruff: noqa: F811 -- imported pytest fixture is intentionally injected by name
from contextlib import contextmanager
import os
from pathlib import Path
import socket
import threading
import time

import pytest

from portal import app, communication_consent as consent
from test_communication_consent import firm, reviewed, STAFF  # noqa: F401 -- pytest fixture

pytestmark = pytest.mark.skipif(not os.environ.get("E2E"), reason="E2E=1 requests real Chromium consent checks")


@pytest.fixture(scope="module")
def chromium():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as driver:
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        browser = driver.chromium.launch(**({"executable_path": executable} if executable else {}))
        yield browser
        browser.close()


def prepare(firm, kind, language):
    import prospects
    original, _, _ = firm
    if kind == "prospect":
        scope = consent.Scope(original.root, original.data / "portal/prospects", original.data / "prospects")
        store = prospects.store(original.portal)
        client = "prospect-browser"
        (scope.cases / client).mkdir(parents=True)
        store.add_client(client, "Fictional Browser Prospect", email="browser-prospect@fictional.example", language=language)
    else:
        scope, store, client = firm
        store.update_profile(client, language=language)
    reviewed((scope, store, client), language)
    token = consent.manual_bootstrap(scope, store, client, actor_email=STAFF)
    return scope, store, client, token


@contextmanager
def local_server(root):
    import uvicorn
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = "http://127.0.0.1:" + str(sock.getsockname()[1])
    application = app.create_app(root, base_url=origin, secure_cookies=False)
    server = uvicorn.Server(uvicorn.Config(application, log_level="warning", lifespan="off"))
    worker = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started and worker.is_alive(), "Fictional loopback server did not start"
        yield origin
    finally:
        server.should_exit = True
        worker.join(10)
        sock.close()
        assert not worker.is_alive(), "Fictional loopback server did not stop"


def transport_fault(page, origin):
    requests, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    def deliver(route):
        request = route.request
        if request.method != "POST":
            return route.continue_()
        requests.append(request.post_data_json)
        if len(requests) == 1:
            response = route.fetch()
            assert response.status == 200  # Actual handler commits before browser reply loss.
            return route.abort("connectionreset")
        route.continue_()
    page.route(origin + "/api/communication/consent", deliver)
    return requests, errors


COHORT = [(kind, width, "en") for kind in ("main", "prospect") for width in (390, 1280)]
COHORT += [(kind, width, language) for kind, width in (("main", 390), ("prospect", 1280)) for language in ("pt", "es", "ht")]


@pytest.mark.parametrize("kind,width,language", COHORT)
def test_real_browser_own_notice_choice_retry_withdraw_no_upgrade(firm, chromium, kind, width, language):
    scope, store, client, token = prepare(firm, kind, language)
    context = chromium.new_context(viewport={"width": width, "height": 844})
    page = context.new_page()
    try:
        with local_server(firm[0].portal) as origin:
            requests, errors = transport_fault(page, origin)
            page.goto(origin + "/l/" + token)
            page.wait_for_function("() => document.querySelector('#content')?.hidden === false")
            assert page.url == origin + "/consent"
            assert page.locator("html").get_attribute("lang") == language
            assert page.locator("#notice").inner_text() == "FICTIONAL TEST NOTICE ONLY: " + language
            assert not page.locator("#portalLink").is_visible()
            assert not page.locator("#agree").is_checked()
            assert page.locator("input[name=channel]:checked").count() == 0
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.locator("#typedName").fill("李明")
            page.locator('input[name=channel][value=email]').check()
            page.locator("#save").click()  # HTML required checkbox: no API effect.
            assert requests == [] and not (store.client_dir(client) / consent.FILE).exists()
            page.locator("#agree").check()
            page.locator("#save").click()
            page.wait_for_function("() => !document.querySelector('#save').disabled && document.querySelector('#status').textContent.length > 0")
            assert len(requests) == 1 and len(consent._record(scope, store, client)["history"]) == 1
            page.locator("#save").click()  # Exact action/body recovery after committed response loss.
            page.wait_for_function("() => !document.querySelector('#save').disabled && !document.querySelector('#agree').checked")
            assert len(requests) == 2 and requests[0] == requests[1]
            assert len(consent._record(scope, store, client)["history"]) == 1
            session = next(cookie["value"] for cookie in context.cookies() if cookie["name"] == app.COOKIE)
            assert store.session_client(session) is None
            assert store.session_client(session, consent_only=True) == client
            assert page.evaluate("() => localStorage.length === 0 && sessionStorage.length === 0")
            if os.environ.get("E2E_SHOTS"):
                shots = Path(os.environ["E2E_SHOTS"])
                shots.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(shots / f"consent-{kind}-{width}-{language}.png"), full_page=True)
            page.locator("#withdraw").click()
            page.wait_for_function("() => document.querySelector('#content').hidden")
            assert store.session_client(session, consent_only=True) is None
            assert consent._record(scope, store, client)["channels"]["email"]["state"] == "revoked"
            assert errors == []
    finally:
        context.close()
