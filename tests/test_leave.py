"""Leaving the machine (tools/leave.py; brief Q3): what of the firm is on it, deleting what the firm did not keep, the refusals, the receipt, and the check that nothing is left.
Everything runs on a made-up install under a temporary folder; nothing of this checkout, the user's home or the machine's temp folder is touched. Everyone here is made up."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

import oslock
import records

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import export_firm  # noqa: E402
import firm_world  # noqa: E402
import leave  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
NOT_RUNNING = lambda port: False  # noqa: E731


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A made-up install: the firm's data, the scans' folder, the installer's folder with a passphrase, deployment.json, a Python environment, work folders, a model folder, a
    systemd unit, backups, and one export. env is what leave.py is told (it never reads this machine's own settings)."""
    root = tmp_path / "install"
    monkeypatch.setenv("I485_EVENTS", str(root / "data" / "events.jsonl"))
    for name in ("I485_SETTINGS", "I485_POLICIES_FIRM", "I485_RULES_APPROVED", "I485_MAINTENANCE_LOG"):
        monkeypatch.delenv(name, raising=False)
    f = firm_world.make_firm(root, cases=2)
    import events

    events.record("decisions", "confirmed", "Confirmed: applicant date of birth", case="ana-exemplo", who="Jane Paralegal", role="paralegal", version=1)
    (root / "clients" / "ana-exemplo" / "source").mkdir(parents=True)
    (root / "clients" / "ana-exemplo" / "source" / "passport.pdf").write_bytes(b"%PDF-1.4 made up scan")
    (root / "clients" / ".work").mkdir()
    (root / "clients" / ".work" / "page.png").write_bytes(b"png")
    (root / "install").mkdir()
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "i485-backup-2026-10-03-070000.zip.enc").write_bytes(b"encrypted made up backup")
    (root / "install" / "install.json").write_text(json.dumps({"port": 48485, "backup_folder": str(backups), "encrypted": True}), encoding="utf-8")
    (root / "install" / "backup_passphrase.txt").write_text("made-up-passphrase\n", encoding="utf-8")
    (root / "install" / "review.log").write_text("a log line\n", encoding="utf-8")
    (root / "deployment.json").write_text(json.dumps({"mode": "on_premises"}), encoding="utf-8")
    (root / ".venv" / "lib").mkdir(parents=True)
    (root / ".venv" / "lib" / "site.py").write_text("# made up", encoding="utf-8")
    (root / ".ocr_tmp" / "tmpabc").mkdir(parents=True)
    (root / ".ocr_tmp" / "tmpabc" / "page.tsv").write_text("tsv", encoding="utf-8")
    models = tmp_path / "models"
    (models / "packages").mkdir(parents=True)
    (models / "packages" / "translate-pt_en.argosmodel").write_bytes(b"made up model")
    units = tmp_path / "units"
    units.mkdir()
    (units / "i485-review.service").write_text("[Unit]\n", encoding="utf-8")
    (units / "other.service").write_text("[Unit]\n", encoding="utf-8")  # not ours: left alone
    temp = tmp_path / "temp"
    (temp / "i485-backup-abc").mkdir(parents=True)
    (temp / "i485-backup-abc" / "part").write_bytes(b"x")
    (temp / "someone-elses").mkdir()
    (root / "data" / "index.db").write_bytes(b"made up index")  # a copy the catalog names, not exported
    env = {"I485_EVENTS": str(root / "data" / "events.jsonl"), "I485_MODEL_FOLDERS": str(models), "I485_LEAVE_TEMP": str(temp), "I485_SYSTEMD_DIR": str(units),
           "I485_CRONTAB": str(tmp_path / "no-crontab-here"), "HOME": str(tmp_path / "home")}
    return {"root": root, "data": root / "data", "env": env, "backups": backups, "models": models, "units": units, "temp": temp, "firm": f, "tmp": tmp_path}


def make_export(install) -> Path:
    f = install["firm"]
    done = export_firm.everything(export_firm.default_where(f["clients"], f["portal"], f["users"]), who="Sam Attorney", role="attorney", via="staff")
    return Path(done["path"])


def run(install, *, keep=("exports", "backups"), remove=(), name="Exemplo Law", **kw):
    said: list[str] = []
    path = leave.leave(install["root"], keep=list(keep), remove=list(remove), confirm=lambda _p: name, by="Sam Attorney", env=install["env"], probe=NOT_RUNNING, say=said.append, **kw)
    return path, said


def left(install) -> set[str]:
    root = install["root"]
    return {p.relative_to(root).as_posix() for p in root.rglob("*")} if root.exists() else set()


# -- what is on the machine -------------------------------------------------------------------------------------------------------------------


def test_the_list_names_every_place_with_what_the_dictionary_calls_it_and_changes_nothing(install, capsys):
    before = left(install)
    items = leave.plan(install["root"], install["env"])
    keys = {i.key for i in items}
    for want in ("data/clients", "data/portal", "data/settings.json", "data/review_users.json", "data/index.db", "data/events-", "documents", "deployment", "install/install.json",
                 "install/backup_passphrase.txt", ".venv", ".ocr_tmp", "clients/.work", "backups", f"models:{install['models']}", "service:systemd:i485-review.service", "temp:i485-backup-abc"):
        assert want in keys or any(k.startswith(want) for k in keys), (want, sorted(keys))
    labels = {i.key: i.label for i in items}
    assert labels["data/index.db"] == "The search index (index.db)" and labels["data/settings.json"] == "The firm's settings" and labels["data/clients"] == "The cases"
    assert "temp:someone-elses" not in keys and "service:systemd:other.service" not in keys  # only what is the product's
    assert not any(i.path and (i.path == REPO or REPO in i.path.parents) for i in items)  # never this checkout
    assert leave.main(["--root", str(install["root"])]) == 0
    out = capsys.readouterr().out
    assert "Of the firm, on this machine:" in out and "The cases" in out and "in all" in out and left(install) == before


def test_sizes_are_counted_and_a_link_is_counted_as_itself(install, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"x" * 5000)
    (install["root"] / "clients" / "link-to-outside").symlink_to(outside)
    files, size = leave.measure(install["root"] / "clients")
    assert files >= 3 and 5000 > size - 0 or size < 5000  # the link's own size, not its target's
    assert leave.human(1536) == "1.5 KB" and leave.human(12) == "12 bytes"


# -- the refusals -----------------------------------------------------------------------------------------------------------------------------


def test_it_refuses_while_a_lock_is_held_or_the_review_app_answers(install):
    make_export(install)
    lock = install["data"] / "batch.lock"
    fd = oslock.open_lock_file(lock)
    try:
        assert oslock.try_lock(fd)
        with pytest.raises(leave.LeaveError, match=r"batch.lock is held by a running process"):
            run(install)
    finally:
        oslock.unlock(fd)
        os.close(fd)
    with pytest.raises(leave.LeaveError, match="something answers on port 48485"):
        leave.leave(install["root"], keep=["exports", "backups"], remove=[], confirm=lambda _p: "Exemplo Law", by="x", env=install["env"], probe=lambda port: port == 48485, say=lambda _s: None)
    assert (install["data"] / "clients").is_dir()  # nothing was deleted


def test_it_will_not_guess_what_happens_to_the_exports_and_the_backups(install):
    make_export(install)
    with pytest.raises(leave.LeaveError, match="The attorney decides what happens to the backups and the exports"):
        run(install, keep=())
    with pytest.raises(leave.LeaveError, match="say --keep exports or --remove exports"):
        run(install, keep=("backups",))
    assert (install["data"] / "clients").is_dir()


def test_it_refuses_without_an_export_that_matches_its_own_manifest(install, tmp_path):
    with pytest.raises(leave.LeaveError, match="There is no export of the firm's data to leave with"):
        run(install)
    path = make_export(install)
    import zipfile

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(bad, "w") as out:
        for info in src.infolist():
            data = src.read(info.filename)
            out.writestr(info, data.replace(b"MADE UP", b"MADE-UP") if info.filename.endswith("fact_graph.json") else data)
    with pytest.raises(leave.LeaveError, match="does not match its own manifest"):
        run(install, export=bad)
    assert (install["data"] / "clients").is_dir()
    receipt, said = run(install, skip_export_check=True)  # the firm's choice, on the record
    assert "skipped by the firm's choice" in receipt.read_text(encoding="utf-8")


def test_it_refuses_until_the_firms_name_is_typed_back(install):
    make_export(install)
    for typed in ("", "Exemplo Law Group", "someone else"):
        with pytest.raises(leave.LeaveError, match="does not match the firm's name"):
            run(install, name=typed)
    assert (install["data"] / "clients").is_dir() and not list(install["root"].glob("leaving-receipt-*"))
    run(install, name="  exemplo   LAW ")  # spacing and capitals are not the point


def test_it_never_deletes_a_folder_that_holds_the_install_or_the_users_home(install, tmp_path, monkeypatch):
    make_export(install)
    env = dict(install["env"], I485_REFERENCE=str(install["root"].parent))  # a setting that names the folder above the install
    with pytest.raises(leave.LeaveError, match="Refusing to delete"):
        leave.leave(install["root"], keep=["exports", "backups"], remove=[], confirm=lambda _p: "Exemplo Law", by="x", env=env, probe=NOT_RUNNING, say=lambda _s: None)
    assert (install["data"] / "clients").is_dir()


# -- deleting, keeping, the receipt -------------------------------------------------------------------------------------------------------------


def test_it_deletes_everything_not_kept_and_keeps_the_exports_the_backups_and_the_passphrase(install):
    export = make_export(install)
    path, said = run(install)
    remaining = left(install)
    assert remaining == {path.name, "data", "data/exports", f"data/exports/{export.name}", "install", "install/backup_passphrase.txt"}, sorted(remaining)
    assert (install["backups"] / "i485-backup-2026-10-03-070000.zip.enc").exists()  # kept, outside the install
    assert not install["models"].exists() and not (install["units"] / "i485-review.service").exists() and (install["units"] / "other.service").exists()
    assert not (install["temp"] / "i485-backup-abc").exists() and (install["temp"] / "someone-elses").is_dir()
    assert export_firm.verify_zip(export) == []  # the export is as it was
    assert any(s.startswith("Deleted ") for s in said) and not any("Could not delete" in s for s in said)


def test_the_attorney_can_remove_the_exports_and_the_backups_and_keep_the_models(install):
    make_export(install)
    path, _ = run(install, keep=("models",), remove=("exports", "backups"))
    assert not install["backups"].exists() and (install["models"] / "packages").is_dir()
    assert left(install) == {path.name}  # a folder the firm emptied is not left behind; the passphrase went with the backups it opened


def test_the_receipt_says_what_was_deleted_when_by_whom_the_sizes_and_what_the_firm_must_still_do(install):
    export = make_export(install)
    before = {i.key: leave.measure(i.path) for i in leave.plan(install["root"], install["env"])}
    path, _ = run(install)
    text = path.read_text(encoding="utf-8")
    assert re.fullmatch(r"leaving-receipt-\d{4}-\d{2}-\d{2}\.txt", path.name) and oct(path.stat().st_mode & 0o777) == "0o600"
    assert "Firm: Exemplo Law" in text and "Deleted by: Sam Attorney" in text and re.search(r"Started: \d\d/\d\d/\d{4} \d\d:\d\d", text) and "Product version:" in text
    receipt = json.loads(text.split("-- the same, for a program --", 1)[1])
    deleted = {d["key"]: d for d in receipt["deleted"]}
    for key, (files, size) in before.items():
        if key in ("exports", "backups", "install/backup_passphrase.txt"):
            continue
        if key == "documents":  # the work folder inside it was deleted, and counted, first
            files, size = files - before["clients/.work"][0], size - before["clients/.work"][1]
        assert deleted[key]["files"] == files and deleted[key]["bytes"] == size and deleted[key]["problem"] is None, key
    assert receipt["deleted_files"] == sum(d["files"] for d in receipt["deleted"]) and receipt["not_deleted"] == []
    assert {k["key"] for k in receipt["kept"]} == {"exports", "backups", "install/backup_passphrase.txt"} and receipt["decided"] == {"keep": ["backups", "exports"], "remove": []}
    assert receipt["export_checked"]["path"] == str(export) and receipt["export_checked"]["problems"] == []
    for line in leave.STILL_YOURS:
        assert line in text
    for word in ("Older backups", "Clio", "mail provider", "disk"):
        assert word in text


def test_a_link_is_removed_and_never_what_it_points_to(install, tmp_path):
    make_export(install)
    outside = tmp_path / "outside-folder"
    outside.mkdir()
    (outside / "precious.txt").write_text("not the firm's", encoding="utf-8")
    (install["data"] / "clients" / "link-to-outside").symlink_to(outside, target_is_directory=True)
    (install["data"] / "stray-link").symlink_to(outside / "precious.txt")
    run(install)
    assert (outside / "precious.txt").read_text(encoding="utf-8") == "not the firm's"


def test_cron_lines_and_scheduled_tasks_are_taken_out_by_their_mark(install, tmp_path, monkeypatch):
    make_export(install)
    table = tmp_path / "crontab.txt"
    table.write_text("0 7 * * * echo mine # i485-pipeline\n30 3 * * * echo theirs\n", encoding="utf-8")
    fake = tmp_path / "fake-crontab"
    fake.write_text(f"#!/bin/sh\nif [ \"$1\" = \"-l\" ]; then cat {table}; else cat > {table}; fi\n", encoding="utf-8")
    fake.chmod(0o755)
    install["env"]["I485_CRONTAB"] = str(fake)
    assert any(i.key == "service:cron" for i in leave.plan(install["root"], install["env"]))
    run(install)
    assert table.read_text(encoding="utf-8") == "30 3 * * * echo theirs\n"


# -- the check ----------------------------------------------------------------------------------------------------------------------------------


def test_verify_says_nothing_remains_except_the_receipt_and_what_was_kept_and_names_what_is_left(install, capsys, monkeypatch):
    make_export(install)
    ok, text = leave.verify(install["root"], install["env"])
    assert not ok and "Still on this machine:" in text  # before leaving
    path, _ = run(install)
    ok, text = leave.verify(install["root"], install["env"])
    assert ok and text.startswith("nothing of the firm remains except: ") and path.name in text and "data/exports" in text
    (install["root"] / ".ocr_tmp" / "late").mkdir(parents=True)  # a reader wrote a work file after the deletion
    (install["data"] / "settings.json").write_text("{}", encoding="utf-8")  # the firm's settings came back
    ok, text = leave.verify(install["root"], install["env"])
    assert not ok and ".ocr_tmp" in text and "settings.json" in text
    monkeypatch.setattr(sys, "argv", ["leave.py"])
    monkeypatch.setattr(os, "environ", dict(os.environ, **install["env"]))
    assert leave.main(["--root", str(install["root"]), "--verify"]) == 1
    assert "Still on this machine" in capsys.readouterr().out


def test_nothing_the_catalog_names_remains_in_the_data_folder_after_leaving(install):
    make_export(install)
    run(install, remove=("exports",), keep=("backups",))
    data = install["data"]
    named = records.patterns("firm", exported_only=False) + records.patterns("logs", exported_only=False)
    assert not data.exists() or not [p for p in data.rglob("*") if p.is_file()], sorted(p.name for p in data.rglob("*"))
    assert not (install["root"] / "clients").exists() and not [n for n in named if (data / n.split("/")[0]).exists()]


# -- the exit procedure and the tool agree ------------------------------------------------------------------------------------------------------


def steps() -> list[tuple[int, str]]:
    text = (REPO / "docs" / "security" / "exit_procedure.md").read_text(encoding="utf-8")
    section = text.split("## The procedure", 1)[1].split("\n## ", 1)[0]
    return [(int(n), body) for n, body in re.findall(r"^(\d+)\. (.*(?:\n   .*)*)", section, flags=re.M)]


def test_the_exit_procedures_numbered_steps_each_name_a_tool_or_a_command_that_exists():
    found = steps()
    assert [n for n, _ in found] == list(range(1, len(found) + 1)) and len(found) >= 9
    for n, body in found:
        commands = re.findall(r"`([^`]+)`", body)
        assert any(c.split()[0] in ("python", "systemctl", "Unregister-ScheduledTask", "sha256sum") for c in commands), f"step {n} names no command: {body[:60]}"
        for c in commands:
            m = re.match(r"python (tools/\w+\.py)((?: .*)?)", c)
            if m:
                tool = REPO / m.group(1)
                assert tool.is_file(), f"step {n}: {m.group(1)} is not a tool of the product"
                for flag in re.findall(r"(--[a-z-]+)", m.group(2)):
                    assert flag in tool.read_text(encoding="utf-8"), f"step {n}: {m.group(1)} has no {flag}"


def test_what_the_document_says_leave_py_refuses_and_keeps_is_what_it_does():
    text = (REPO / "docs" / "security" / "exit_procedure.md").read_text(encoding="utf-8")
    section = text.split("## What `tools/leave.py` deletes, keeps and refuses", 1)[1].split("\n## ", 1)[0]
    for must in ("`--keep`", "`--remove`", "`--export`", "`--skip-export-check`", "lock file", "typed back", "receipt"):
        assert must in section, must
    still = text.split("## What the firm must still do itself", 1)[1].split("\n## ", 1)[0]
    for line in leave.STILL_YOURS[:4]:
        assert line.split(":")[0].split(" ")[0] in still  # the document and the receipt name the same things the firm must still do
