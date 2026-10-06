"""Support access in the browser (brief R3, src/support.py): the attorney lets support in for one hour, masked; support signs in with the one-time code,
sees the cases as numbers and no name, opens a case and sees counts and shapes and no value, is refused a scan and a Save; the attorney ends the
session and support's next request is signed out; the ledger holds every step under the support person's name. Everyone here is made up."""

from __future__ import annotations

import json
import re
from pathlib import Path

SUPPORT = ("help@provider.example", "Sol Supportexemplo")


def client_values(world) -> set[str]:
    """The world's client names, A-Numbers and case folder names: none may reach a masked session."""
    out = set()
    for d in Path(world["clients"]).iterdir():
        if not d.is_dir():
            continue
        out.add(d.name)
        graph = json.loads((d / "fact_graph.json").read_text(encoding="utf-8")) if (d / "fact_graph.json").exists() else {"facts": {}}
        for key in ("applicant.given_name", "applicant.family_name", "applicant.a_number"):
            value = str((graph["facts"].get(key) or {}).get("value") or "")
            if len(value) >= 4:
                out.add(value)
    for p in Path(world["portal"]).glob("clients/*/profile.json"):
        name = json.loads(p.read_text(encoding="utf-8")).get("name") or ""
        if name:
            out.add(name)
    return out


def test_the_attorney_lets_support_in_masked_support_reads_shapes_and_is_signed_out_when_it_ends(world, browser, attorney):
    page = attorney.page
    page.goto("about:blank")
    page.goto(world["review"] + "#settings:support")
    page.wait_for_selector("#set-support #sup-form")
    page.fill("#sup-name", SUPPORT[1])
    page.fill("#sup-email", SUPPORT[0])
    page.select_option("#sup-hours", "1")
    assert not page.locator("#sup-plain").is_checked()  # masked unless ticked
    page.click("#sup-let-in")
    page.wait_for_selector("#sup-code")
    code = page.locator("#sup-code").input_value()
    assert len(code) >= 12 and "Masked" in page.locator("#sup-current").inner_text()

    ctx = browser.new_context()
    s = ctx.new_page()
    errors: list[str] = []
    s.on("pageerror", lambda e: errors.append(str(e)))
    s.goto(world["review"])
    s.wait_for_selector("input[name=email]")
    s.fill("input[name=email]", SUPPORT[0])
    s.fill("input[name=password]", code)
    s.get_by_role("button", name="Sign in").click()
    s.wait_for_selector("#sup-health")
    assert "Client values are masked" in s.locator("#sup-banner").inner_text()
    secret = client_values(world)

    s.click("#sup-tab-cases")
    s.wait_for_selector("#sup-cases tr[data-case]")
    cases = s.locator("#sup-cases tr[data-case]").evaluate_all("rows => rows.map(r => r.dataset.case)")
    assert cases and all(re.fullmatch(r"Case \d+", c) for c in cases)
    body = s.locator("body").inner_text()
    assert [v for v in secret if v.lower() in body.lower()] == []

    s.locator("#sup-cases tr[data-case='Case 1'] button").click()
    s.wait_for_selector("#sup-case #sup-counts")
    counts = s.locator("#sup-counts").inner_text()
    assert re.search(r"\d+ documents, \d+ decisions", counts) and s.locator("#sup-case table tr").count() > 2
    body = s.locator("body").inner_text()
    assert [v for v in secret if v.lower() in body.lower()] == []

    base = world["review"].rstrip("/")
    scan = s.request.get(base + "/api/crop?client=Case%201&doc=d1.pdf&page=0&box=0,0,1,1")
    assert scan.status == 403 and "masked" in scan.json()["error"]
    save = s.request.post(base + "/api/decide", data=json.dumps({"client": "Case 1", "item_id": "x", "action": "confirm"}),
                          headers={"Content-Type": "application/json", "X-Review-App": "1"})
    assert save.status == 403 and "read-only" in save.json()["error"]

    page.click("#sup-end")
    page.wait_for_selector("#sup-none")
    s.click("#sup-tab-health")
    s.wait_for_selector("input[name=email]")  # signed out: the next request was refused
    assert not errors and not attorney.errors

    events = Path(world["env"]["I485_EVENTS"]).parent
    rows = [json.loads(x) for p in sorted(events.glob("events*.jsonl")) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    ours = [r for r in rows if r.get("kind") == "support"]
    assert any(r["action"] == "let_in" and r["who"] == "Ana Attorney" and SUPPORT[1] in r["what"] and "masked" in r["what"] for r in ours)
    asked = [r for r in ours if r["action"] == "request"]
    assert all(r["who"] == SUPPORT[1] and r["via"] == "support" for r in asked)
    said = " | ".join(r["what"] for r in asked)
    for step in ("/api/support/health", "/api/support/cases", "/api/support/case", "/api/crop", "/api/decide (POST): refused"):
        assert step in said, step
    assert any(r["action"] == "ended" and r["who"] == "Ana Attorney" for r in ours)
    ctx.close()
