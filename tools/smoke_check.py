"""A quick self-check of an installation (the update scripts run it after replacing the product; anyone can run it any time).

    python tools/smoke_check.py [--install FOLDER]

It never reads or writes the firm's data. It checks, in about ten seconds, that the product opens: the main modules load,
every schema file is readable JSON, the review app starts on an empty made-up folder and answers, and a backup of a tiny
made-up folder can be made, tested and restored. Exit code 0 and the word OK when all of that works; 1 and a list when not.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

MODULES = ("clock", "settings", "deployment", "maintenance", "backups", "getting_started", "journey", "packet", "filing_questions", "fees",
           "overnight", "review.auth", "review.server", "review.state", "portal.app")


def check(root: Path) -> list[str]:
    tmp = Path(tempfile.mkdtemp(prefix="i485-smoke-"))
    try:
        return _check(root, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # the scratch folder is never left behind


def _check(root: Path, tmp: Path) -> list[str]:
    problems: list[str] = []
    sys.path.insert(0, str(root / "src"))
    import schema_path
    # nothing of the firm's: every file the app reads or writes is pointed at the scratch folder for this check
    for env in ("I485_SETTINGS", "I485_MAINTENANCE_LOG", "I485_BACKUP_LOG", "I485_LIVE_STATUS", "I485_RULES_APPROVED", "I485_POLICIES_FIRM", "I485_INDEX",
                "I485_DEPLOYMENT", "I485_EVENTS", "I485_QUERY_DB", "I485_POSTURE"):
        os.environ[env] = str(tmp / f"{env.lower()}.json")
    os.environ["PORTAL_DATA"] = str(tmp / "portal")  # the portal app opens its store when it loads
    os.environ["I485_CASES"] = str(tmp / "clients")
    os.environ["I485_LIVE_CHECKS"] = "0"
    os.environ["I485_SHADOW"] = "0"
    for name in MODULES:
        try:
            __import__(name)
        except Exception as exc:  # noqa: BLE001 -- every failure is a line, not a stop
            problems.append(f"{name} does not load ({type(exc).__name__}: {exc})")
    for path in schema_path.all_files(".json", schema_path.schemas_in(root)):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"{path.relative_to(root).as_posix()} is not readable ({type(exc).__name__})")
    if problems:
        return problems
    try:
        from review.auth import Accounts
        from review.server import ReviewApp, make_handler, serve

        clients = tmp / "data" / "clients"
        clients.mkdir(parents=True)
        accounts = Accounts(tmp / "data" / "users.json")
        accounts.new_setup_code()
        app = ReviewApp(clients, schema_path.path("field_map", "i485", schema_path.schemas_in(root)), schema_path.path("template", "i485", schema_path.schemas_in(root)), None, accounts=accounts)
        httpd = serve(app, 0)
        port = httpd.server_address[1]
        httpd.RequestHandlerClass = make_handler(app, port)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/me", timeout=20) as r:
                me = json.loads(r.read())
            if me.get("accounts") is not True:
                problems.append("the review app answered, but not as expected")
        finally:
            httpd.shutdown()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"the review app does not start ({type(exc).__name__}: {exc})")
    try:
        import backups

        data = tmp / "fake" / "data"
        (data / "clients" / "case-a").mkdir(parents=True)
        (data / "clients" / "case-a" / "meta.json").write_text("{}", encoding="utf-8")
        made = backups.make_backup(tmp / "out", data, log_path=tmp / "log.json", deployment=tmp / "none.json")
        report = backups.check_restore(made["archive"], None, tmp, tmp / "log.json")
        if made["problems"] or not report["ok"]:
            problems.append("a test backup could not be restored: " + "; ".join(made["problems"] + report["problems"]))
    except Exception as exc:  # noqa: BLE001
        problems.append(f"backups do not work ({type(exc).__name__}: {exc})")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A quick self-check of an installation.")
    ap.add_argument("--install", type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args(argv)
    problems = check(args.install.resolve())
    if problems:
        print("The self-check found problems:")
        for p in problems:
            print("  " + p)
        return 1
    print("OK: the product opens, the review app answers, and a test backup restores.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
