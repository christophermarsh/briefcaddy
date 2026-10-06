"""The restore drill, in the browser (src/backups.py drill): under Keeping current the attorney finds "Last restore drill: never run.", presses Run it now, and the line changes to the
result ("passed in N minutes"); after the newest backup is made different from the install it says "failed" in red and names what differed without naming a case or a file; the
paralegal has no button and is refused when she asks. The drill is a job the app's own worker runs; the backup is made from the world's data folder here, the way the nightly one is.
Everyone here is made up (the demo client, cloned). E2E_SHOTS keeps a screenshot of each step."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))


def _line(screen):
    return screen.page.locator("#drill-line")


def test_the_attorney_runs_the_restore_drill_from_keeping_current_and_the_line_shows_the_result(world, attorney, paralegal, tmp_path):
    import backups

    from restore_drill_helpers import tamper

    log = Path(world["env"]["I485_BACKUP_LOG"])
    made = backups.make_backup(tmp_path / "backups", world["root"], log_path=log)  # the nightly backup, made from the world's own data folder
    assert not made["problems"]
    page = attorney.page
    page.goto("about:blank")
    page.goto(world["review"])
    page.wait_for_selector("#client-rows")
    page.get_by_role("button", name="Keeping current").click()
    page.wait_for_selector("#drill-line")
    assert _line(attorney).inner_text() == "Last restore drill: never run."
    assert page.locator("#drill-run").is_visible()
    attorney.check("restore-drill-1-never-run")

    page.locator("#drill-run").click()  # a job for the worker: the screen answers at once and watches it
    page.wait_for_function("() => /passed in/.test((document.getElementById('drill-line') || {}).innerText || '')", timeout=180000)
    said = _line(attorney).inner_text()
    assert said.startswith("Last restore drill: ") and said.endswith(".") and "passed in" in said and "never run" not in said and "err" not in (_line(attorney).get_attribute("class") or "").split()
    assert ".json" not in said and "—" not in said
    attorney.check("restore-drill-2-passed")
    assert backups.read_log(log)["last_restore_drill"]["ok"]  # the log, the line and (below) the register say the same
    register = json.loads(Path(world["env"]["I485_MAINTENANCE_LOG"]).read_text(encoding="utf-8"))["restore_drill"]
    assert register["log"][-1]["by"] == "The restore drill"

    # the newest backup no longer matches the install (a record in it differs, the install's copy untouched since): the line says failed, in red, and names no case and no file
    tamper(made["archive"], "data/clients/demo-ana/meta.json", b'{"client_id": "not-the-install"}')
    page.locator("#drill-run").click()
    page.wait_for_function("() => /failed:/.test((document.getElementById('drill-line') || {}).innerText || '')", timeout=180000)
    failed = _line(attorney).inner_text()
    assert failed.startswith("Last restore drill: ") and "failed: " in failed and "demo-ana" not in failed and "meta.json" not in failed
    assert "err" in (_line(attorney).get_attribute("class") or "").split()  # red
    assert "failed" in page.locator("#main").inner_text()
    attorney.check("restore-drill-3-failed")

    # the paralegal has no button, and the route refuses her
    para = paralegal.page
    para.goto("about:blank")
    para.goto(world["review"])
    para.wait_for_selector("#client-rows")
    para.get_by_role("button", name="Keeping current").click()
    para.wait_for_selector("#drill-line")
    assert para.locator("#drill-run").count() == 0
    assert "failed: " in para.locator("#drill-line").inner_text()  # she is told how it stands, not given the button
    status = para.evaluate("fetch('/api/restore-drill', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Review-App': '1'}, body: '{}'}).then((r) => r.status)")
    assert status == 403
