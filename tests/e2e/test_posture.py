"""Settings, This computer, in the browser (src/posture.py): the attorney finds one line for each duty of the firm's own machine (the disk encrypted, the screen locked, a recent backup on
another device, the system updated, the firewall on), red when off, grey when not known, with the source command and the line it read under "How this was checked", and the words that the
product only reads and never changes the computer; the paralegal has no such section and is refused when she asks. The app's own reading is switched off here (a test world is not a firm's
computer): the test keeps a reading made from recorded Windows outputs, written from the documentation and not run on Windows (tests/posture_fixtures.py says so), where the app keeps its own.
Everyone here is made up. E2E_SHOTS keeps a screenshot of each step."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_the_attorney_sees_the_computers_duties_with_how_each_was_checked_and_the_paralegal_does_not(world, attorney, paralegal):
    import clock
    import posture
    from posture_fixtures import (
        DOC_WIN_BITLOCKER_OFF,
        DOC_WIN_DRIVE_FIXED,
        DOC_WIN_FIREWALL_PUBLIC_OFF,
        DOC_WIN_SCREEN_NOTHING,
        Out,
        table,
    )

    today = clock.today()
    fresh = (today - timedelta(days=9)).isoformat()
    log = {"last_backup": {"at": clock.stamp("seconds"), "file": "i485-backup-made-up.zip", "folder": "D:\\i485-backups"}}
    reading = posture.run_all(Path("C:\\firm\\data"), run=table(win_bitlocker=DOC_WIN_BITLOCKER_OFF, win_screen=DOC_WIN_SCREEN_NOTHING, win_drive=DOC_WIN_DRIVE_FIXED,
                                                                win_hotfix=Out(0, f"KB5031356 {fresh}\r\n"), win_firewall=DOC_WIN_FIREWALL_PUBLIC_OFF),
                              system="windows", today=today, log=log)
    posture.keep(reading, Path(world["env"]["I485_POSTURE"]))
    page = attorney.page
    page.goto("about:blank")
    page.goto(world["review"] + "#settings")
    page.wait_for_selector("#set-computer .computer-line")
    box = page.locator("#set-computer")
    text = box.inner_text()
    assert "This computer" in text and "The product only reads these. It never changes a setting of the computer" in text and "Last read" in text and "Windows" in text
    lines = {el: box.locator(f'.computer-line[data-check="{el}"]') for el in ("disk", "screen", "backup", "updates", "firewall")}
    assert [lines[k].get_attribute("data-result") for k in lines] == ["off", "not-known", "on", "on", "off"]
    assert "Disk encryption: off." in lines["disk"].inner_text() and "Screen lock: not known." in lines["screen"].inner_text() and "Firewall: off." in lines["firewall"].inner_text()
    assert "Public profile" in lines["firewall"].inner_text() and "another device" in lines["backup"].inner_text() and "9 days ago" in lines["updates"].inner_text()
    # red when off, grey when not known
    assert "err" in (lines["disk"].locator("div").first.get_attribute("class") or "").split() and "err" in (lines["firewall"].locator("div").first.get_attribute("class") or "").split()
    assert "sub" in (lines["screen"].locator("div").first.get_attribute("class") or "").split() and "err" not in (lines["screen"].locator("div").first.get_attribute("class") or "").split()
    assert "err" not in (lines["updates"].locator("div").first.get_attribute("class") or "").split()
    # the fold: closed, then the source command and the line it read, what "on" means, and what the IT person does
    fold = lines["disk"].locator("details")
    assert fold.get_attribute("open") is None and not lines["disk"].locator("code").first.is_visible()
    attorney.check("posture-1-lines")
    fold.locator("summary").click()
    shown = lines["disk"].inner_text()
    assert "How this was checked" in shown and "Answered by: Windows" in shown and "Command: powershell -NoProfile -NonInteractive -Command" in shown and "System.Volume.BitLockerProtection" in shown
    assert "The line it read: 0" in shown and '"On" means: The disk that holds the firm\'s data is encrypted' in shown and "What your IT person does: Turn on BitLocker" in shown
    lines["firewall"].locator("summary").click()
    assert "Get-NetFirewallProfile" in lines["firewall"].inner_text() and "The line it read: Domain=True; Private=True; Public=False" in lines["firewall"].inner_text()
    attorney.check("posture-2-fold")
    assert ".json" not in text and "—" not in text

    # the paralegal: no section, and the route refuses her
    para = paralegal.page
    para.goto("about:blank")
    para.goto(world["review"] + "#settings")
    para.wait_for_selector("#set-calendar")
    assert para.locator("#set-computer").count() == 0
    assert para.evaluate("fetch('/api/posture').then((r) => r.status)") == 403
