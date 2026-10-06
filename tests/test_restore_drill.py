# ruff: noqa: F811  (the fixtures imported from the other test files are used as arguments)
"""The restore drill (src/backups.py drill, tools/restore_drill.py): the latest backup restored into a scratch folder and compared, record by record, with the live install.

On the made-up firm of tests/test_backup.py: a drill that passes; a record changed after the backup (expected, passes, named as "since"); a record changed in the archive (fails and names
the record and the field); a document whose bytes differ; a file lost; a file the backup missed; a file the catalog says a backup leaves out; the vault with the wrong key; the live install
untouched and the scratch folder gone; the log, the register's entry, the Settings line and the morning report saying one thing; the time box; the button, a job. Everyone here is made up."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import backups as backup
import clock
import maintenance
import records

sys.path.insert(0, str(Path(__file__).resolve().parent))
from restore_drill_helpers import tamper  # noqa: E402
from test_backup import PASSPHRASE, _no_outside_paths, world  # noqa: E402,F401

HOUR = 3600


@pytest.fixture(autouse=True)
def firm_logs(tmp_path, monkeypatch):
    """The register's and the backup log's own files for this test (never the repo's)."""
    monkeypatch.setenv("I485_MAINTENANCE_LOG", str(tmp_path / "maintenance_log.json"))
    monkeypatch.setattr(maintenance, "FIRM_LOG", tmp_path / "maintenance_log.json")
    monkeypatch.setenv("I485_BACKUP_LOG", str(tmp_path / "log.json"))
    monkeypatch.setattr(backup, "LOG", tmp_path / "log.json")
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "events.jsonl"))


def old(path: Path, hours: int = 5) -> None:
    """A file as it was some hours before the backup (a drill tells what changed since by the live copy's time)."""
    t = datetime.now().timestamp() - hours * HOUR
    os.utime(path, (t, t))


@pytest.fixture
def firm(world):
    """The test_backup world with a month of the event ledger and every file, and its folders, made some hours old: nothing in it is newer than the backup yet."""
    (world["data"] / "events-2026-10.jsonl").write_text('{"at": "2026-10-01T09:00:00-04:00", "kind": "facts", "what": "read"}\n{"at": "2026-10-02T09:00:00-04:00", "kind": "decisions", "what": "confirm"}\n', encoding="utf-8")
    (world["data"] / "clients" / "case-ana" / "decisions.json").write_text(json.dumps({"item-1": {"action": "confirm", "note": "made-up", "history": [{"reviewer": "Jane Doe"}]}}), encoding="utf-8")
    for path in sorted([*world["data"].rglob("*"), *world["documents"].rglob("*")], key=lambda p: -len(p.parts)):
        old(path)
    return world


def make(w, **kw):
    return backup.make_backup(w["out"], w["data"], documents=w["documents"], log_path=w["log"], **kw)


def run(w, **kw):
    return backup.drill(kw.pop("archive", None), kw.pop("passphrase", None), log_path=w["log"], register=kw.pop("register", False), work_parent=w["work"], **kw)


@pytest.fixture
def drilled(firm, tmp_path):
    firm["work"] = tmp_path / "scratch"
    firm["work"].mkdir()
    return firm


def tree(*roots: Path) -> dict[str, str]:
    """Every file under these folders: its path and sha256."""
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for r in roots for p in sorted(r.rglob("*")) if p.is_file()}


# -- the drill ------------------------------------------------------------------------------------------------------------------


def test_a_drill_of_an_untouched_install_passes_and_checks_every_kind_of_file(drilled):
    made = make(drilled)
    got = run(drilled)
    assert got["ran"] and got["ok"] and not got["stopped"] and got["failures"] == 0 and got["expected"] == 0 and got["backup"] == made["archive"].name
    c = got["checked"]
    assert c["files"] >= 14 and c["json"] >= 6 and c["ledger_months"] == 1 and c["databases"] == 2 and c["documents"] >= 2  # the records, the month of the ledger, index and learning and the databases, the scans
    log = backup.read_log(drilled["log"])
    assert log["last_restore_drill"]["ok"] and log["last_restore_drill"]["backup"] == made["archive"].name and log["last_restore_drill"]["seconds"] >= 0


def test_a_record_changed_after_the_backup_is_expected_and_named_as_since(drilled):
    make(drilled)
    d = drilled["data"] / "clients" / "case-ana" / "decisions.json"
    d.write_text(json.dumps({"item-1": {"action": "confirm", "note": "changed after", "history": [{"reviewer": "Jane Doe"}]}}), encoding="utf-8")  # the install went on working
    (drilled["data"] / "events-2026-10.jsonl").open("a", encoding="utf-8").write('{"at": "2026-10-03T09:00:00-04:00", "kind": "journey", "what": "mark"}\n')  # the ledger grew
    (drilled["data"] / "clients" / "case-new").mkdir()
    (drilled["data"] / "clients" / "case-new" / "meta.json").write_text("{}", encoding="utf-8")  # a case added since
    got = run(drilled)
    assert got["ok"] and got["failures"] == 0 and got["expected"] >= 3
    since = backup.read_log(drilled["log"])["last_restore_drill"]["since"]
    one = next(x for x in since if x["file"].endswith("case-ana/decisions.json"))
    assert one["field"] == "item-1.note" and one["backup_time"] and one["live_time"] and one["live_time"] >= one["backup_time"]  # the record, the field and the two times
    assert any(x["file"].endswith("case-new/meta.json") for x in since)
    assert "changed after" not in json.dumps(backup.read_log(drilled["log"]))  # never a value: a value is a client's


def test_a_record_that_differs_in_the_archive_fails_and_names_the_record_and_the_field(drilled):
    made = make(drilled)
    name = "data/clients/case-ana/decisions.json"
    tamper(made["archive"], name, json.dumps({"item-1": {"action": "confirm", "note": "made-up", "history": [{"reviewer": "Someone Else"}]}}).encode())
    got = run(drilled, archive=made["archive"])
    assert not got["ok"] and got["failures"] == 1
    d = got["differences"][0]
    assert d["file"] == name and d["field"] == "item-1.history[0].reviewer" and d["kind"] == "differs" and d["backup_time"] and d["live_time"] and d["live_time"] < d["backup_time"]
    assert "has not changed it since" in d["plain"] and "case-ana" not in d["plain"] and "decisions.json" not in d["plain"]  # the line a screen shows names neither the case nor the file
    assert backup.read_log(drilled["log"])["last_restore_drill"]["first"] == d["plain"]


def test_a_document_whose_bytes_differ_fails_unless_it_changed_since(drilled):
    make(drilled)
    scan = drilled["documents"] / "case-ana" / "scan1.pdf"
    scan.write_bytes(b"%PDF a different scan")
    old(scan)  # the live copy is older than the backup: the backup and the install disagree
    got = run(drilled)
    assert not got["ok"] and got["differences"][0]["kind"] == "bytes" and got["differences"][0]["file"] == "documents/case-ana/scan1.pdf"
    scan.write_bytes(b"%PDF scanned again")  # now: changed after the backup
    got = run(drilled)
    assert got["ok"] and got["expected"] == 1


def test_a_file_lost_from_the_install_fails_and_one_taken_out_since_is_expected(drilled):
    make(drilled)
    gone = drilled["data"] / "clients" / "case-bia" / "meta.json"
    gone.unlink()  # taken out now: its folder changed after the backup
    assert run(drilled)["ok"]
    old(gone.parent)  # the folder says nothing changed since the backup, and the file is not there: lost
    got = run(drilled)
    assert not got["ok"] and got["differences"][0]["kind"] == "lost" and got["differences"][0]["file"].endswith("case-bia/meta.json")


def test_a_file_the_backup_missed_fails_but_not_one_the_catalog_says_a_backup_leaves_out(drilled):
    made = make(drilled)
    extra = drilled["data"] / "clients" / "case-ana" / "forgotten.json"
    extra.write_text("{}", encoding="utf-8")
    old(extra)  # older than the backup and not in it
    left_out = [drilled["data"] / "backup_log.json", drilled["data"] / "second.lock", drilled["data"] / "exports" / "i485-firm-data-2026-10-01.zip", drilled["data"] / "index.db-wal"]
    (drilled["data"] / "exports").mkdir()
    for p in left_out:
        p.write_bytes(b"never in a backup")
        old(p)
    got = run(drilled, archive=made["archive"])
    assert not got["ok"] and got["failures"] == 1 and got["differences"][0]["kind"] == "not backed up" and got["differences"][0]["file"].endswith("forgotten.json")
    extra.unlink()
    assert run(drilled, archive=made["archive"])["ok"]  # what the catalog leaves out is not an extra file
    added = drilled["data"] / "clients" / "case-ana" / "added-after.json"
    added.write_text("{}", encoding="utf-8")
    assert run(drilled)["ok"]  # a file made since the backup is expected


def test_the_catalogs_list_of_what_a_backup_leaves_out_is_what_a_backup_leaves_out(drilled):
    made = make(drilled)
    with zipfile.ZipFile(made["archive"]) as zf:
        names = set(zf.namelist())
    for rel in ("backup_log.json", "batch.lock", "half.json.tmp", "exports/a.zip", "x.part", "index.db-wal", "index.db-shm"):
        assert records.not_backed_up(rel), rel
    for rel in ("clients/case-ana/meta.json", "settings.json", "index.db", "clio/secrets.enc", "events-2026-10.jsonl"):
        assert not records.not_backed_up(rel), rel
    assert not any(records.not_backed_up(n.split("/", 1)[1]) for n in names if "/" in n)  # nothing the catalog leaves out is in the archive


def test_the_vault_with_the_wrong_key_fails(drilled):
    from cryptography.fernet import Fernet

    (drilled["data"] / "clio" / "vault.key").write_bytes(Fernet.generate_key())  # not the key the vault was written with
    old(drilled["data"] / "clio" / "vault.key")
    made = make(drilled, passphrase=PASSPHRASE)  # the key is inside an encrypted backup
    got = run(drilled, archive=made["archive"], passphrase=PASSPHRASE)
    assert not got["ok"] and any(d["kind"] == "vault" for d in got["differences"])
    assert "Clio connection" in got["differences"][0]["plain"]
    assert not run(drilled, archive=made["archive"], passphrase="not the passphrase at all")["ok"]  # a backup that cannot be opened is a drill that failed


def test_the_vault_key_kept_beside_the_archive_is_tried_there(drilled):
    made = make(drilled)  # plain: the key is beside the archive
    assert run(drilled, archive=made["archive"])["ok"]
    from cryptography.fernet import Fernet

    made["key_file"].write_bytes(Fernet.generate_key())
    got = run(drilled, archive=made["archive"])
    assert not got["ok"] and any(d["kind"] == "vault" for d in got["differences"])


def test_a_database_with_other_row_counts_fails_unless_it_changed_since(drilled):
    import sqlite3

    make(drilled)
    con = sqlite3.connect(drilled["data"] / "learning.db")
    con.execute("insert into t values ('after the backup')")
    con.commit()
    con.close()
    got = run(drilled)
    assert got["ok"] and got["expected"] == 1  # changed now: expected
    old(drilled["data"] / "learning.db")
    got = run(drilled)
    assert not got["ok"] and got["differences"][0]["kind"] == "database" and got["differences"][0]["field"] == "t"


def test_the_ledger_only_grows_so_a_month_with_fewer_rows_fails_whenever(drilled):
    made = make(drilled)
    ledger = drilled["data"] / "events-2026-10.jsonl"
    ledger.write_text('{"at": "2026-10-01T09:00:00-04:00", "kind": "facts", "what": "read"}\n', encoding="utf-8")  # a row gone, and the file changed now
    got = run(drilled, archive=made["archive"])
    assert not got["ok"] and got["differences"][0]["kind"] == "ledger" and "fewer rows" in got["differences"][0]["plain"]


def test_the_install_is_untouched_and_the_scratch_folder_is_gone_pass_or_fail(drilled):
    made = make(drilled)
    before = tree(drilled["data"], drilled["documents"])
    times = {p: p.stat().st_mtime_ns for p in [*drilled["data"].rglob("*"), *drilled["documents"].rglob("*")]}
    assert run(drilled)["ok"]
    assert not list(drilled["work"].iterdir())
    tamper(made["archive"], "data/settings.json", b'{"firm": {"other": 1}}')
    assert not run(drilled, archive=made["archive"])["ok"]
    assert not list(drilled["work"].iterdir())
    assert tree(drilled["data"], drilled["documents"]) == before  # byte for byte what it was
    assert {p: p.stat().st_mtime_ns for p in [*drilled["data"].rglob("*"), *drilled["documents"].rglob("*")]} == times  # not even a time (the logs are outside the install here)


def test_a_drill_without_a_backup_says_so_and_a_second_at_once_is_refused(drilled, monkeypatch):
    assert backup.drill(log_path=drilled["log"], register=False, work_parent=drilled["work"]) == {"ran": False, "ok": False, "stopped": False, "line": "No backup has been made yet, so there is nothing to restore."}
    make(drilled)
    import oslock

    with oslock.locked(drilled["log"].with_name(backup.DRILL_LOCK)):
        got = run(drilled)
    assert got["line"] == "A restore drill is already running." and not got["ran"]


# -- the log, the register, Settings and the morning report ------------------------------------------------------------------------


def test_the_log_the_register_the_settings_line_and_the_morning_report_say_one_thing(drilled, tmp_path, monkeypatch):
    from review.server import ReviewApp

    import schema_path

    monkeypatch.setattr(clock, "_now_override", None)
    make(drilled)
    assert backup.drill_status(log_path=drilled["log"])["line"] == "Last restore drill: never run."
    got = backup.drill(None, None, log_path=drilled["log"], work_parent=drilled["work"])  # register=True: the firm's check of the monthly item, by the drill
    assert got["ok"]
    line = backup.drill_status(log_path=drilled["log"])["line"]
    today = clock.today()
    assert line == f"Last restore drill: {today.month:02d}/{today.day:02d}/{today.year}, passed in less than a minute." == got["line"]
    log = json.loads((tmp_path / "maintenance_log.json").read_text(encoding="utf-8"))["restore_drill"]  # the register's entry
    assert log["last_checked"] == today.isoformat() and log["log"][-1] == {"on": today.isoformat(), "by": "The restore drill"}
    item = next(i for i in maintenance.status() if i["id"] == "restore_drill")
    assert item["last_checked"] == today.isoformat() and not item["due"] and item["findings"] == []
    app = ReviewApp(drilled["data"] / "clients", schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None)
    shown = next(i for i in app.maintenance()["items"] if i["id"] == "restore_drill")
    assert shown["note"] == line and not shown["note_bad"]  # the line under Settings
    import overnight

    assert overnight.restore_drill_night() == ""  # not the first of the month, not overdue: the report says nothing today
    next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    monkeypatch.setattr(clock, "_now_override", datetime(next_month.year, next_month.month, 1, 3, 0))
    morning = overnight.restore_drill_night()  # one real monthly drill, then inspect its report
    assert morning.startswith("Last restore drill: ") and "passed in" in morning  # the morning after a drill: the same words
    current = json.loads((tmp_path / "maintenance_log.json").read_text(encoding="utf-8"))["restore_drill"]
    assert current["log"][:-1] == log["log"] and current["last_checked"] == next_month.isoformat()
    assert current["log"][-1] == {"on": next_month.isoformat(), "by": "The restore drill"}
    with pytest.raises(PermissionError):
        app.maintenance_mark({"id": "restore_drill", "reviewer": "Jane Doe"})  # nobody ticks it by hand: the drill records itself


def test_a_failed_drill_is_red_on_settings_due_in_the_register_and_said_every_morning(drilled, tmp_path, monkeypatch):
    from review.server import ReviewApp

    import schema_path

    monkeypatch.setattr(clock, "_now_override", None)
    made = make(drilled)
    tamper(made["archive"], "data/settings.json", b'{"firm": {"other": 1}}')
    got = backup.drill(made["archive"], None, log_path=drilled["log"], work_parent=drilled["work"])
    assert not got["ok"]
    status = backup.drill_status(log_path=drilled["log"])
    assert "failed: " in status["line"] and "settings" in status["line"] and status["overdue"] and status["failed_lately"]
    assert not (tmp_path / "maintenance_log.json").exists() or "restore_drill" not in json.loads((tmp_path / "maintenance_log.json").read_text(encoding="utf-8"))  # no check recorded
    item = next(i for i in maintenance.status() if i["id"] == "restore_drill")
    assert item["due"] and "failed" in " ".join(item["findings"])
    app = ReviewApp(drilled["data"] / "clients", schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None)
    shown = next(i for i in app.maintenance()["items"] if i["id"] == "restore_drill")
    assert shown["note"] == status["line"] and shown["note_bad"]  # red
    import overnight

    runs = []
    monkeypatch.setattr(backup, "drill", lambda *a, **k: runs.append(1) or {"line": "ran"})
    assert overnight.restore_drill_night() == status["line"] and not runs  # not run again by an ordinary night (it would fail the same way), but said
    monkeypatch.setattr(clock, "_now_override", datetime(clock.today().year, clock.today().month, 1, 3, 0))
    assert overnight.restore_drill_night() == "ran" and runs  # the first night of the month runs it


def test_a_drill_that_is_never_run_or_a_month_old_is_overdue_and_the_night_runs_it(drilled, monkeypatch):
    import overnight

    make(drilled)
    ran = []
    monkeypatch.setattr(backup, "drill", lambda *a, **k: ran.append(k.get("deadline")) or {"line": "Last restore drill: ran."})
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 14, 3, 0))
    assert overnight.restore_drill_night(None) == "Last restore drill: ran." and len(ran) == 1  # never run: overdue
    ran.clear()
    backup._record("last_restore_drill", {"at": "2026-09-01T12:00:00+00:00", "ok": True, "seconds": 90.0}, drilled["log"])
    monkeypatch.setattr(backup, "LOG", drilled["log"])
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 9, 20, 3, 0))
    assert overnight.restore_drill_night() == "" and not ran  # nineteen days ago and passed: not due
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 3, 0))
    assert overnight.restore_drill_night() == "Last restore drill: ran." and ran  # more than a month
    status = backup.drill_status(__import__("datetime").date(2026, 10, 5), drilled["log"])
    assert status["overdue"] and "34 days ago" in status["findings"][0] and status["line"] == "Last restore drill: 09/01/2026, passed in 2 minutes."


def test_the_time_box_stops_a_drill_at_the_hour_the_night_ends_and_says_so(drilled, monkeypatch):
    import overnight

    made = make(drilled)
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 1, 6, 31))
    got = run(drilled, archive=made["archive"], deadline=clock.now() - timedelta(minutes=1))
    assert got["stopped"] and not got["ok"] and got["line"] == "The restore drill was stopped at 06:31, the hour the overnight run ends, before it finished. It runs again tonight."
    log = backup.read_log(drilled["log"])
    assert "last_restore_drill" not in log and log["last_restore_drill_stopped"]["stopped_at"] == "06:31"  # it counts for nothing
    assert not list(drilled["work"].iterdir())
    status = backup.drill_status(__import__("datetime").date(2026, 10, 1), drilled["log"])
    assert status["unfinished"] and "was stopped at 06:31" in status["line"] and status["line"].startswith("Last restore drill: never run.")
    runs = []
    monkeypatch.setattr(backup, "LOG", drilled["log"])
    monkeypatch.setattr(backup, "drill", lambda *a, **k: runs.append(k["deadline"]) or {"line": "ran again"})
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 2, 3, 0))  # not the first of the month: a stopped drill runs the next night
    assert overnight.restore_drill_night(clock.now() + timedelta(hours=3)) == "ran again" and runs


def test_the_command_line_drill_exits_with_the_codes_it_promises(drilled, capsys, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import restore_drill

    monkeypatch.setattr(backup, "LOG", drilled["log"])
    assert restore_drill.main([]) == 2 and "no backup has been made yet" in capsys.readouterr().err
    made = make(drilled)
    assert restore_drill.main(["--work-folder", str(drilled["work"])]) == 0
    out = capsys.readouterr().out
    assert "difference(s) since the backup was made" in out and "passed in" in out
    tamper(made["archive"], "data/settings.json", b'{"firm": {"other": 1}}')
    assert restore_drill.main([str(made["archive"]), "--work-folder", str(drilled["work"])]) == 1
    assert "FAILED data/settings.json: field firm.other" in capsys.readouterr().err
    assert not list(drilled["work"].iterdir())


# -- the button: a job, an attorney's ------------------------------------------------------------------------------------------------


def test_the_button_is_an_attorneys_and_a_job_the_worker_runs_never_the_request(tmp_path, monkeypatch):
    import jobs
    import settings
    import test_approvals_queue as aq

    firm = aq.make_firm(tmp_path / "f", monkeypatch)
    # The real worker's installation gate requires every mutable authority
    # path to belong to this same installation, rather than the queue fixture's
    # older root-level settings/rules/events layout.
    data = firm.root / "data"
    for key, relative in {"I485_RULES_APPROVED": "rules_approved.json", "I485_MAINTENANCE_LOG": "maintenance_log.json",
                          "I485_EVENTS": "events.jsonl", "I485_SETTINGS": "settings.json", "I485_CASES": "clients",
                          "I485_PROSPECTS": "prospects", "PORTAL_DATA": "portal", "I485_JOBS": "jobs"}.items():
        monkeypatch.setenv(key, str(data / relative))
    monkeypatch.setattr(settings, "PATH", data / "settings.json")
    monkeypatch.setattr(maintenance, "FIRM_LOG", data / "maintenance_log.json")
    monkeypatch.setenv("I485_BACKUP_LOG", str(tmp_path / "log.json"))
    monkeypatch.setattr(backup, "LOG", tmp_path / "log.json")
    monkeypatch.delenv("I485_JOBS_WORKER", raising=False)
    aq.write_case(firm.clients, "case-ana", "Ana Exemplo")
    srv = aq.serve_app(firm)
    try:
        backup.make_backup(tmp_path / "bk", firm.root / "data")
        jane, sam = aq.sign_in(srv.base, aq.PARALEGAL), aq.sign_in(srv.base, aq.ATTORNEY)
        assert aq.call(srv.base + "/api/restore-drill", jane, {})[0] == 403  # an attorney's: the drill opens the whole backup
        status, started = aq.call(srv.base + "/api/restore-drill", sam, {})
        assert status == 200 and started["job"]["kind"] == "restore_drill" and started["job"]["label"] == "Waiting to start"
        assert backup.read_log().get("last_restore_drill") is None  # the request ran nothing: a job waits for the worker
        again = aq.call(srv.base + "/api/restore-drill", sam, {})[1]
        assert again["job"]["id"] == started["job"]["id"]  # one waiting is the one that answers
        jobs.work(srv.app.data_root, srv.app.portal_root, once=True)
        status, done = aq.call(srv.base + f"/api/jobs/firm?id={started['job']['id']}", sam)
        assert status == 200 and done["job"]["state"] == "done" and done["job"]["result"]["ok"] and "passed in" in done["job"]["result"]["text"]
        line = backup.drill_status()["line"]
        assert line == done["job"]["result"]["text"]
        note = next(i for i in aq.call(srv.base + "/api/maintenance", sam)[1]["items"] if i["id"] == "restore_drill")["note"]
        assert note == line  # the line under Settings
        assert aq.call(srv.base + "/api/jobs/firm", sam)[1]["inbox"] == []  # the drill is no reading: the inbox's list and My work's counts leave it out
        assert jobs.counts(srv.app.jobs_root) == {"waiting": 0, "running": 0}
    finally:
        srv.httpd.shutdown()
