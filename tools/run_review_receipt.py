"""Run local fictional pytest with retained, source-bound review receipts.

Example: project-python tools/run_review_receipt.py --label fixtures -- tests/test_portal.py
Use --browser for sandboxed Chromium checks. Never accepts an existing basetemp.
No deployment, provider configuration, source archive or real-case processing.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREES = ("src", "schemas", "tests", "tools")
EXCLUDE = {"__pycache__", ".pytest_cache", ".ocr_test_tmp", "node_modules", ".venv"}
DEPENDENCIES = ("requirements.txt", "requirements.lock", "requirements-dev.txt")


def source_hashes():
    paths = {ROOT / name for name in DEPENDENCIES if (ROOT / name).is_file()}
    paths.update(p for name in TREES for p in (ROOT / name).rglob("*")
                 if p.is_file() and not EXCLUDE.intersection(p.relative_to(ROOT).parts))
    paths.update(p for p in (ROOT / "docs").rglob("*")
                 if p.is_file() and p.suffix in {".md", ".json", ".txt", ".py", ".yaml", ".yml"})
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise ValueError(f"Source manifest refuses a redirected file: {path.relative_to(ROOT)}")
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


PLUGIN = '''import json
import os
from pathlib import Path

RUN = Path(os.environ["REVIEW_RECEIPT_RUN"])

def record(filename, value):
    with (RUN / filename).open("a", encoding="utf-8") as output:
        output.write(json.dumps(value) + "\\n")

def pytest_configure(config):
    try:
        from playwright.sync_api import BrowserType
        from playwright.async_api import BrowserType as AsyncBrowserType
    except ImportError:
        return
    def secure(browser, kwargs):
        if browser.name != "chromium":
            return
        if kwargs.get("chromium_sandbox") is False or any("no-sandbox" in str(a).lower() or "disable-setuid-sandbox" in str(a).lower() for a in kwargs.get("args") or []):
            raise ValueError("This review requires Chrome sandbox enabled; no bypass allowed")
        kwargs["chromium_sandbox"] = True
        record("browser-launch.jsonl", {"browser": browser.name, "chromium_sandbox": True,
            "executable_path": kwargs.get("executable_path"), "args": kwargs.get("args") or []})
    for name in ("launch", "launch_persistent_context"):
        original = getattr(BrowserType, name)
        def sync_launch(self, *args, _original=original, **kwargs):
            secure(self, kwargs)
            return _original(self, *args, **kwargs)
        setattr(BrowserType, name, sync_launch)
        original_async = getattr(AsyncBrowserType, name)
        async def async_launch(self, *args, _original=original_async, **kwargs):
            secure(self, kwargs)
            return await _original(self, *args, **kwargs)
        setattr(AsyncBrowserType, name, async_launch)

def pytest_collection_finish(session):
    (RUN / "collected.json").write_text(json.dumps([item.nodeid for item in session.items]))

def pytest_runtest_logreport(report):
    record("test-phases.jsonl", {"nodeid": report.nodeid, "when": report.when,
        "outcome": report.outcome, "duration": report.duration})

def pytest_collectreport(report):
    if report.failed:
        record("collection-failures.jsonl", {"nodeid": report.nodeid, "outcome": report.outcome})
'''


def write(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", args.label) or not 1 <= args.timeout <= 10800:
        parser.error("Use a bounded label and timeout from 1 to 10800 seconds")
    selection = args.pytest_args
    if selection[:1] == ["--"]:
        selection = selection[1:]
    if not selection or any(a.startswith(("--basetemp", "--junitxml")) for a in selection):
        parser.error("Provide tests; the runner owns basetemp and JUnit output")
    run = ROOT / "tmp" / ("review-" + args.label + "-" + uuid.uuid4().hex)
    run.mkdir(parents=True, exist_ok=False)
    scratch = run / "auxiliary-temp"
    scratch.mkdir()
    base = run / "basetemp"
    assert not base.exists()
    (run / "review_runtime_plugin.py").write_text(PLUGIN, encoding="utf-8")
    env = os.environ.copy()
    removed_provider_keys = sorted(key for key in env if key.startswith(("SMTP_", "TWILIO_")) or key in {"MAIL_FROM", "PORTAL_OUTBOX_FULL_LINKS"})
    for key in removed_provider_keys:
        env.pop(key, None)
    env.update(TMPDIR=str(scratch), TMP=str(scratch), TEMP=str(scratch), I485_SHADOW="0", I485_LIVE_CHECKS="0",
               PORTAL_NOTIFY="0", I485_JOBS_WORKER="0",
               REVIEW_RECEIPT_RUN=str(run), E2E="1" if args.browser else "", E2E_SHOTS=str(run / "shots"))
    env["PYTHONPATH"] = os.pathsep.join(str(ROOT / p) for p in ("src", "tests", "tests/e2e", "tools")) + os.pathsep + str(run)
    if args.browser:
        env["DEBUG"] = "pw:browser"  # actual launch arguments in pytest.log; synthetic browser only
    command = [sys.executable, "-u", "-m", "pytest", "-q", "--capture=sys", "-p", "no:cacheprovider",
               "-p", "review_runtime_plugin", "--basetemp=" + str(base), "--junitxml=" + str(run / "results.xml"), *selection]
    versions = {d.metadata["Name"]: d.version for d in importlib.metadata.distributions() if d.metadata.get("Name")}
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    write(run / "command.json", {"command": command, "python": sys.version, "platform": platform.platform(), "head": head,
        "packages": dict(sorted(versions.items())), "timeout_seconds": args.timeout, "data": "fictional tests only",
        "provider_activation_keys_disabled": ["SMTP_HOST", "TWILIO_ACCOUNT_SID"],
        "other_provider_keys_removed": removed_provider_keys, "chrome_sandbox_required": True,
        "notification_control_note": "SMTP_HOST and TWILIO_ACCOUNT_SID activate all notifier transports; both absent. PORTAL_NOTIFY is only a legacy marker and is not relied upon. Controlled mocks/dry runs only.",
        "env": {k: env[k] for k in ("TMPDIR", "TMP", "TEMP", "I485_SHADOW", "I485_LIVE_CHECKS", "PORTAL_NOTIFY", "I485_JOBS_WORKER", "PYTHONPATH", "E2E", "E2E_SHOTS")}})
    before = source_hashes()
    write(run / "source-before.json", before)
    print(run.relative_to(ROOT).as_posix(), flush=True)
    started = time.monotonic()
    with (run / "pytest.log").open("w", encoding="utf-8") as log:
        try:
            code = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout, check=False).returncode
        except subprocess.TimeoutExpired:
            code = 124
            log.write("\nHARNESS TIMEOUT: no completed acceptance result.\n")
    after = source_hashes()
    write(run / "source-after.json", after)
    changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
    counts = None
    if (run / "results.xml").exists():
        suites = ET.parse(run / "results.xml").getroot().findall("testsuite")
        counts = {k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
        counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    phases = [json.loads(line) for line in (run / "test-phases.jsonl").read_text().splitlines()] if (run / "test-phases.jsonl").exists() else []
    failures = {when: [r["nodeid"] for r in phases if r["when"] == when and r["outcome"] == "failed"] for when in ("setup", "call", "teardown")}
    collected = json.loads((run / "collected.json").read_text()) if (run / "collected.json").exists() else []
    attempted = {r["nodeid"] for r in phases}
    status = {"exit_code": code, "seconds": round(time.monotonic() - started, 3), "counts": counts,
        "failed_by_phase": failures, "unrun_nodes": [n for n in collected if n not in attempted],
        "source_files_changed": changed, "source_stable_during_run": not changed,
        "source_manifest_sha256": hashlib.sha256((run / "source-before.json").read_bytes()).hexdigest(),
        "chrome_launch_receipt": "browser-launch.jsonl" if (run / "browser-launch.jsonl").exists() else None}
    write(run / "status.json", status)
    print(json.dumps(status, indent=2), flush=True)
    print("\n".join((run / "pytest.log").read_text(encoding="utf-8").splitlines()[-25:]))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
