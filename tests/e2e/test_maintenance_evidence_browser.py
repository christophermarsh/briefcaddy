"""Recorded fictional maintenance evidence, never live checks or update jobs."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from playwright.sync_api import expect

import clock

SHOTS = Path(__file__).resolve().parents[2] / "docs/research/cloud_maintenance_ui_evidence/shots" / os.environ.get("MAINTENANCE_UI_RUN", "development")


def capture(page, node, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    node.evaluate("node => scrollTo(0, Math.max(0, scrollY + node.getBoundingClientRect().top - document.querySelector('.topbar').getBoundingClientRect().height - 16))")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(SHOTS / (name + "-viewport.png")))
    node.screenshot(path=str(SHOTS / (name + "-element.png")))


@pytest.mark.parametrize("width,mode", [(1000, "on_premises"), (1400, "hosted")])
def test_recorded_states_responsibility_and_existing_sidebar_order(world, attorney, width, mode):
    """Saved observations exercise the protected DTO; no check producer is run."""
    page = attorney.page
    page.set_viewport_size({"width": width, "height": 900})
    deployment = world["register"].parent / "deployment.json"
    originals = {path: path.read_bytes() if path.exists() else None for path in (world["register"], world["live"], deployment)}
    try:
        register = json.loads(world["register"].read_text())
        dates = {"policy_watch": None, "shadow_report": (clock.today() - timedelta(days=100)).isoformat(),
                 "local_models": clock.today().isoformat()}
        for row in register["items"]:
            if row["id"] in dates:
                row["last_checked"] = dates[row["id"]]
        world["register"].write_text(json.dumps(register))
        deployment.write_text(json.dumps({"mode": mode, "provider": {"name": "Fictional Named Provider"}}))
        world["live"].write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "results": {
            "form_i765": {"ok": False, "finding": "Fictional retained source observation differs; review the replacement edition."},
            "form_i485": {"ok": True}}}))
        # Confirm the requested order through the real case navigation. It was
        # already implemented; this is verification, not a Path loading repair.
        attorney.open("demo-ana", "journey")
        queues = page.get_by_role("navigation", name="Review queues")
        buttons = queues.get_by_role("button")
        assert [buttons.nth(n).inner_text().strip().split("\n")[0] for n in range(3)] == [
            "Where the case stands", "Notes and tasks", "Path"]
        buttons.nth(0).focus()
        page.keyboard.press("Tab")
        expect(buttons.nth(1)).to_be_focused()
        page.keyboard.press("Tab")
        expect(buttons.nth(2)).to_be_focused()
        capture(page, queues, "existing-sidebar-order-" + str(width))
        page.locator("#all").click()
        expect(page.get_by_role("heading", name="All clients", exact=True)).to_be_visible()
        page.get_by_role("button", name=re.compile("Keeping current")).click()
        expect(page.get_by_role("heading", name="Provider responsibilities", exact=True)).to_be_visible()
        page.locator("details").filter(has=page.get_by_text(re.compile(r"^Show all \d+$"))).locator("summary").click()
        body = page.locator("#main").inner_text()
        for false_claim in ("Kept current by", "being updated now", "All up to date", "nothing for the firm to do", "re-checked every night", "Your provider is updating it"):
            assert false_claim not in body
        for private in ("schemas/", "src/", "tools/", "data/", "install/"):
            assert private not in body
        assert "Installed software version" in body and "configured overnight process runs" in body
        assert "Last recorded source check:" in body
        assert ("live rollout status is not shown here" if mode == "hosted" else "Firm IT is responsible for installing approved signed releases") in body
        expected = {"policy_watch": "Not checked", "shadow_report": "Due for review",
                    "local_models": "Reviewed within cadence", "form_i765": "Needs attention"}
        for item, state in expected.items():
            row = page.locator('#provider-responsibilities [data-maintenance-item="' + item + '"]')
            expect(row.locator(".badge")).to_have_text(state)
            if item != "form_i765":
                expect(row).to_contain_text("manual review")
                expect(row).to_contain_text("No source check recorded.")
            else:
                expect(row).to_contain_text("Recorded source check:")
                expect(row).to_contain_text("Fictional retained source observation differs")
        heading = page.locator("#provider-responsibility-heading")
        capture(page, heading, "provider-responsibilities-" + mode + "-" + str(width))
        capture(page, page.locator('#provider-responsibilities [data-maintenance-item="policy_watch"]'), "manual-policy-review-" + str(width))
        capture(page, page.locator('#provider-responsibilities [data-maintenance-item="local_models"]'), "recorded-model-review-" + str(width))
        dto = page.request.get(world["review"].rstrip("/") + "/api/maintenance").json()
        (SHOTS / ("recorded-maintenance-dto-" + str(width) + ".json")).write_text(json.dumps(dto, indent=2) + "\n")
        # Removing recorded observations must not leave a claimed run time.
        world["live"].unlink()
        page.get_by_role("button", name="Back to every case", exact=True).click()
        page.get_by_role("button", name=re.compile("Keeping current")).click()
        expect(page.locator("#main")).to_contain_text("No source check recorded.")
        assert "Last recorded source check:" not in page.locator("#main").inner_text()
        capture(page, page.locator("#main .pagehead").first, "no-source-check-recorded-" + str(width))
    except Exception:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / ("failure-" + str(width) + ".png")))
        raise
    finally:
        for path, data in originals.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(data)
