"""The line under Settings says whether the record of every change is as it was written (src/ledger_seal.py): green words from the night's kept check, the first
row that does not match in red when it failed, and a plain sentence before any check. Everything is invented."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

import ledger_seal  # noqa: E402


def look(attorney, world) -> dict:
    attorney.page.goto("about:blank")
    attorney.page.goto(world["review"] + "#settings")
    attorney.settle()
    attorney.page.wait_for_selector("#record-line", state="attached", timeout=20000)
    return attorney.page.evaluate("() => { const e = document.querySelector('#record-line'); return { text: e.textContent, ok: e.dataset.ok, red: e.classList.contains('err') }; }")


def test_the_settings_line_says_intact_then_names_the_row_in_red_then_intact_again(world, attorney):
    base = Path(world["env"]["I485_EVENTS"])
    check = ledger_seal.check_path(base)
    check.unlink(missing_ok=True)
    first = look(attorney, world)
    assert first["ok"] == "null" and not first["red"] and "has not been checked yet" in first["text"]
    ledger_seal.nightly(base)  # the overnight run's own step
    kept = ledger_seal.kept(base)
    second = look(attorney, world)
    assert kept["ok"] and second["ok"] == "true" and not second["red"]
    assert second["text"].startswith("The record is intact as of ") and second["text"].endswith(f"({kept['rows']} rows)")
    line = "does not match: the row written at 10/03/2026 09:15 by Jane Paralegal was changed (events-2026-10.jsonl, line 7)."
    check.write_text(json.dumps({"at": kept["at"], "ok": False, "rows": kept["rows"], "before": 0, "line": line}), encoding="utf-8")
    third = look(attorney, world)
    assert third["ok"] == "false" and third["red"] and third["text"] == line
    ledger_seal.nightly(base)  # the world's ledger is whole: the next night's check says so again
    assert look(attorney, world)["ok"] == "true"
