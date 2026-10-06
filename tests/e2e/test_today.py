"""Today, in the browser (src/day_plan.py): the paralegal opens Today from My work, sees the groups (the cases ready to build and sign first, the cases the office holds, the
cases that wait only on the client last), presses Ask the client on the bottom group, finds the review card's own request form with the client's item written in, adds it and
sends the list, and the portal holds the request the card would have sent. The review app is the real one, started on the world's cases with one change: a launcher gives
two cases the packet lines named below and nothing for an attorney (what holds a packet is the packet's own tests' business; the demo client's real packet is held by
hundreds of cards, none of which this test should have to settle). Everyone here is made up (the demo client, cloned). E2E_SHOTS keeps a screenshot of each step."""

from __future__ import annotations

import json
import subprocess
import sys

from conftest import REPO, Screen, _port, _wait

LAUNCH = """
import json, os, runpy, sys
sys.path.insert(0, "src")
import approvals, packet, holders
real = packet.plan
real_items = approvals.case_items
plans = json.loads(os.environ["TODAY_PLANS"])


def plan(client_dir, row, schema=None, _building=False, light=False):
    lines = plans.get(client_dir.name if hasattr(client_dir, "name") else str(client_dir).rsplit("/", 1)[-1])
    if lines is None:
        return real(client_dir, row, schema, _building, light)
    problems = [holders.held(h, t) for h, t in lines]
    return {"summary": row.get("summary") or {}, "ready": not problems, "problems": problems, "filing": "i485",
            "held": [{"text": str(p), "holder": holders.holder_of(p)} for p in problems]}


def items(client_dir, *args, **kw):  # the two cases have nothing waiting for an attorney either
    return [] if client_dir.name in plans else real_items(client_dir, *args, **kw)


packet.plan = plan
approvals.case_items = items
sys.argv = ["src/review/server.py"] + sys.argv[1:]
runpy.run_path("src/review/server.py", run_name="__main__")
"""

READY, CLIENT_ONLY = "case-today-ready", "demo-ana"  # the demo client is in the portal, so the office can reach them
ITEM = "Missing: Passport."
SENT = {"delivery", "sent_at", "sent_by", "status", "draft"}  # what sending the list adds to a request


def test_the_paralegal_opens_today_and_asks_the_client_for_what_only_the_client_holds(world, browser, tmp_path):
    import world as w
    from portal.store import PortalStore

    w.clone(world["root"], "demo-ana", READY)
    port = _port()
    base = f"http://127.0.0.1:{port}/"
    plans = {READY: [], CLIENT_ONLY: [["client", ITEM]]}
    env = world["env"] | {"I485_ROSTER": str(tmp_path / "roster.json"), "TODAY_PLANS": json.dumps(plans)}
    log = open(tmp_path / "review.log", "w")
    review = subprocess.Popen([sys.executable, "-c", LAUNCH, "--data", str(world["clients"]), "--port", str(port), "--users", str(world["users"]), "--portal", str(world["portal"])],
                              cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    paralegal = None
    try:
        _wait(base)
        world.setdefault("last_steps", {})
        world.setdefault("devices", {})
        paralegal = Screen(browser, world | {"review": base}, w.PARALEGAL)
        page = paralegal.page
        page.goto("about:blank")
        page.goto(base)
        page.wait_for_selector("#client-rows")
        paralegal.settle()

        # My work says how many cases can reach a signed packet today, with a button to Today
        page.get_by_role("button", name="My work").first.click()
        page.wait_for_selector("#today-line")
        assert "can reach a signed packet today" in page.locator("#today-line").inner_text()
        paralegal.check("today-0-my-work")
        page.locator("#today-open").click()
        page.wait_for_selector("#today-rows")
        paralegal.settle()
        text = paralegal.check("today-1-the-groups")
        assert "Today" in text and "can reach a signed packet today" in text
        for words in ("Ready to build and sign", "Waiting on the office or the attorney", "Waiting only on the client"):
            assert words.lower() in text.lower(), words  # (a group's heading is set in capitals)
        groups = page.locator("tr.today-group").evaluate_all("(rows) => rows.map((r) => r.dataset.group)")
        assert groups == ["ready", "work", "client"], groups  # in their order: the ready ones first, the cases that wait only on the client last
        ready = page.locator(f'tr[data-today="{READY}"]')
        assert ready.get_attribute("data-group") == "ready" and "ready to build and sign" in ready.inner_text().lower()
        assert ".pdf" not in text and ".json" not in text and "—" not in text
        row = page.locator(f'tr[data-today="{CLIENT_ONLY}"]')
        assert row.get_attribute("data-group") == "client"
        said = row.inner_text()
        assert "1 step to a signed packet" in said and "Client 1, office 0, attorney 0" in said and ITEM in said and "Client" in said
        assert page.locator('tr[data-group="work"] button[data-ask-client]').count() == 0  # only the cases that wait on the client alone have the button

        # Ask the client: the review card's own request form, with the client's item written in
        row.locator("button[data-ask-client]").click()
        form = page.locator(".ask")
        form.wait_for()
        assert form.get_by_label("Your question, in English").input_value() == f"Please tell us: {ITEM}"
        paralegal.check("today-2-the-card-form")
        before = [r["id"] for r in PortalStore(world["portal"]).requests(CLIENT_ONLY)]
        form.get_by_role("button", name="Add to the client's list").click()
        paralegal.toast()
        page.get_by_role("button", name="Send the list to the client").click()
        paralegal.toast()
        paralegal.settle()
        mine = [r for r in PortalStore(world["portal"]).requests(CLIENT_ONLY) if r["id"] not in before]
        assert len(mine) == 1
        request = mine[0]
        # the request the card would send: the card's own route, asked as the card asks it (draft, then the list sent once), by this person, in the client's language
        assert request["text"] == f"Please tell us: {ITEM}" and request["status"] == "open" and request["by"] == w.PARALEGAL[1]
        assert not request.get("facts") and request.get("type", "text") == "text" and request.get("delivery")
        # the same words through the card's own route (/api/ask, queued) make a request with the same fields: Today made nothing of its own
        card = paralegal.page.request.post(base + "api/ask", headers={"Content-Type": "application/json", "X-Review-App": "1"}, data=json.dumps(
            {"client": CLIENT_ONLY, "text": request["text"], "type": "text", "options": [], "doc_id": "", "reviewer": w.PARALEGAL[1], "queue": True, "facts": []}))
        assert card.status == 200, card.text()
        twin = card.json()["request"]
        assert set(twin) - SENT == set(request) - SENT and twin["text"] == request["text"] and twin["by"] == request["by"]
        assert {k: twin.get(k) for k in ("type", "doc_id", "language")} == {k: request.get(k) for k in ("type", "doc_id", "language")}
        shown = paralegal.page.request.get(base + f"api/requests?client={CLIENT_ONLY}").json()  # and it is on the case page's own list of what was asked
        assert any(r["id"] == request["id"] for r in shown)
        paralegal.check("today-3-sent")
    finally:
        if paralegal is not None:
            paralegal.close()
        review.terminate()
        log.close()
    errors = [b[:1500] for b in (tmp_path / "review.log").read_text(errors="replace").split("Traceback (most recent call last):")[1:] if "BrokenPipeError" not in b]
    assert not errors, errors
