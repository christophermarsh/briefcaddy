"""The case's own path in the browser (brief S3, src/path.py): the paralegal drags a step into place, removes one, sets a date, leaves out a filing, gives a
reason and saves; nothing is derived yet. The attorney sees the change on My approvals and approves it; the case's papers and deadlines follow, and the
client's page shows the new path in Portuguese, in the templates' own words. Everyone here is made up."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

CASE = "case-path"


@pytest.fixture
def path_case(world):
    world["world"].clone(world["root"], "demo-ana", CASE)
    folder = Path(world["portal"]) / "clients" / CASE
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "profile.json").write_text(json.dumps({"id": CASE, "name": "Ana Clara Exemplo Souza", "email": "ana-path@example.com", "language": "pt", "status": "started"}),
                                         encoding="utf-8")
    yield CASE


def order(page) -> list[str]:
    return page.locator("#path-steps li[data-step]").evaluate_all("els => els.map(e => e.dataset.step)")


def test_the_paralegal_shapes_the_path_the_attorney_approves_and_the_case_follows_it(world, path_case, attorney, paralegal):
    import journey

    page = paralegal.page
    paralegal.open(CASE, "path")
    page.wait_for_selector("#path-steps li[data-step='state_court']")
    before = order(page)
    assert before[:4] == ["intake", "state_court", "i360_ready", "i360_pending"] and "visa_wait" in before
    page.locator("#path-steps li[data-step='state_court']").drag_to(page.locator("#path-steps li[data-step='i360_pending']"),
                                                                   source_position={"x": 8, "y": 8}, target_position={"x": 8, "y": 8})  # by the step's badge, not its date box
    assert order(page)[1:4] == ["i360_ready", "i360_pending", "state_court"]  # dragged into place
    page.locator("#path-steps li[data-step='visa_wait'] button", has_text="Remove").click()
    page.locator("#path-steps li[data-step='i485_ready'] input[type=date]").fill("2026-12-01")
    page.locator("#path-steps li[data-step='i485_ready'] input[type=date]").dispatch_event("change")
    page.locator("#path-leave input[data-filing='ead']").check()
    page.fill("#path-reason", "The court order comes after the I-360 for this client, and no work permit.")
    page.click("#path-save")
    page.wait_for_selector("#path-waiting")
    waiting = page.locator("#path-waiting").inner_text()
    assert "waits for an attorney" in waiting and "removed" in waiting and "I-765 left out" in waiting
    assert "12/01/2026" not in page.locator("#path-deadlines").inner_text()  # nothing derived before the approval
    assert page.locator("#path-approve").count() == 0
    assert not paralegal.errors

    a = attorney.page
    attorney.open(CASE, "journey")
    a.locator("#client").select_option("")
    a.locator("#approvals-btn").click()
    a.wait_for_selector("#approval-rows")
    attorney.settle()
    a.locator('button[data-kind-filter="path"]').click()  # the kind comes from the server's registry: "Changes to a case's path"
    attorney.settle()
    row = a.locator(f'tr[data-approval^="path|{CASE}|"]').first
    row.wait_for()
    assert "A change to the case's path" in row.inner_text() and "I-765 left out" in row.inner_text()
    row.locator('button[data-action="approve"]').click()
    attorney.toast()
    a.wait_for_function("(s) => !document.querySelector(s)", arg=f'tr[data-approval^="path|{CASE}|"]', timeout=30000)

    attorney.open(CASE, "path")
    a.wait_for_selector("#path-derived")
    assert "approved by Ana Attorney" in a.locator("#path-source").inner_text()
    assert order(a)[1:4] == ["i360_ready", "i360_pending", "state_court"] and "visa_wait" not in order(a)
    label = next(lbl for f, lbl, _ in journey.settings()["next_filings"]["i485_ready"] if f == "i485")
    assert "12/01/2026" in a.locator("#path-deadlines").inner_text() and label in a.locator("#path-deadlines").inner_text()
    docs = a.locator("#path-documents").inner_text()
    assert "I-360" in docs and "I-485" in docs
    pt = journey.settings()["client"]
    shown = a.locator("#path-client li").all_inner_texts()
    assert a.locator("#path-client").get_attribute("lang") == "pt" and shown == [pt[x]["pt"][0] for x in order(a) if x in pt]
    # the case's own page: the deadline is on Where the case stands, and the client's view in Portuguese follows the new path
    j = a.request.get(world["review"].rstrip("/") + f"/api/journey?client={CASE}").json()
    assert any(d["id"] == "path.i485_ready.i485" and d["date"] == "2026-12-01" for d in j["deadlines"])
    assert j["client_language"] == "pt" and [x["name"] for x in j["client_view"]["path"]] == shown
    assert not attorney.errors

    events = Path(world["env"]["I485_EVENTS"]).parent
    rows = [json.loads(x) for p in sorted(events.glob("events*.jsonl")) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    ours = [r for r in rows if r.get("kind") == "path" and r.get("case") == CASE]
    assert [r["action"] for r in ours] == ["proposed", "approved"] and ours[0]["who"] == "Paulo Paralegal" and ours[1]["who"] == "Ana Attorney"
