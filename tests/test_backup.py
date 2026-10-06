"""Backup, test restore and restore (src/backups.py, tools/backup.py, tools/restore.py), on a made-up data folder."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import backups as backup
import clock
import schema_path

REPO = Path(__file__).resolve().parents[1]

PASSPHRASE = "a long enough passphrase"
SECRET_WORD = "Exemplo-Souza-Sorocaba"  # appears in one case file: an encrypted backup must not show it


@pytest.fixture(autouse=True)
def _no_outside_paths(monkeypatch):
    """The test run points I485_INDEX and friends at its own scratch folder; a backup of this made-up folder must not pick them up."""
    for env, _ in backup.EXTRA_ENV:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("I485_DEPLOYMENT", "/nonexistent/deployment.json")


def _db(path: Path, rows: int = 5) -> None:
    con = sqlite3.connect(path)
    con.execute("create table t (a text)")
    con.executemany("insert into t values (?)", [(f"row {i}",) for i in range(rows)])
    con.commit()
    con.close()


@pytest.fixture
def world(tmp_path):
    """data/ with two cases, a portal client, settings, accounts, two databases, a Clio vault and its key; clients/ with a scan."""
    from cryptography.fernet import Fernet

    root = tmp_path / "firm"
    data = root / "data"
    (data / "clients" / "case-ana").mkdir(parents=True)
    (data / "clients" / "case-bia").mkdir(parents=True)
    (data / "portal" / "clients" / "ana").mkdir(parents=True)
    (data / "clients" / "case-ana" / "meta.json").write_text(json.dumps({"name": SECRET_WORD}), encoding="utf-8")
    (data / "clients" / "case-ana" / "decisions.json").write_text("{}", encoding="utf-8")
    (data / "clients" / "case-ana" / "packet.pdf").write_bytes(b"%PDF-1.4 made up\n" + os.urandom(3000))
    (data / "clients" / "case-bia" / "meta.json").write_text("{}", encoding="utf-8")
    (data / "portal" / "clients" / "ana" / "profile.json").write_text('{"language": "pt"}', encoding="utf-8")
    (data / "portal" / "clients" / "ana" / "events.jsonl").write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")
    (data / "settings.json").write_text('{"firm": {}}', encoding="utf-8")
    (data / "review_users.json").write_text('{"users": {"sam@firm.example": {"hash": "x"}}, "sessions": {}}', encoding="utf-8")
    _db(data / "index.db")
    _db(data / "learning.db")
    (data / "clio").mkdir()
    key = Fernet.generate_key()
    (data / "clio" / "vault.key").write_bytes(key)
    (data / "review_users_totp.key").write_bytes(Fernet.generate_key())  # the authenticator secrets' key, beside the accounts file (review/auth.py)
    (data / "clio" / "secrets.enc").write_bytes(Fernet(key).encrypt(b'{"client_secret": "made-up"}'))
    (data / "batch.lock").write_text("123", encoding="utf-8")  # left out: a lock
    (data / "half.json.tmp").write_text("{", encoding="utf-8")  # left out: a write in progress
    (data / "backup_log.json").write_text('{"last_backup": {"at": "2020-01-01T00:00:00+00:00"}}', encoding="utf-8")  # left out
    (root / "clients" / "case-ana").mkdir(parents=True)
    (root / "clients" / "case-ana" / "scan1.pdf").write_bytes(b"%PDF scan")
    return {"root": root, "data": data, "documents": root / "clients", "out": tmp_path / "backups", "log": tmp_path / "log.json"}


def _make(w, **kw):
    return backup.make_backup(w["out"], w["data"], documents=w["documents"], log_path=w["log"], **kw)


def _zip_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as zf:
        return set(zf.namelist())


# -- the backup --------------------------------------------------------------------------------------------------------

def test_a_backup_holds_every_file_with_its_sha256_and_leaves_out_what_it_should(world):
    made = _make(world)
    archive, m = made["archive"], made["manifest"]
    assert archive.parent == world["out"] and archive.name.startswith("i485-backup-") and archive.suffix == ".zip" and not made["problems"]
    names = _zip_names(archive)
    assert {"manifest.json", "data/clients/case-ana/meta.json", "data/portal/clients/ana/profile.json", "data/settings.json", "data/learning.db",
            "documents/case-ana/scan1.pdf", "data/index.db", "data/clio/secrets.enc"} <= names
    assert not any(n.endswith((".lock", ".tmp")) or "backup_log" in n for n in names)  # a lock, a write in progress, the log itself
    assert m["counts"]["cases"] == 2 and m["counts"]["portal_clients"] == 1
    with zipfile.ZipFile(archive) as zf:
        for row in m["files"]:  # every ordinary file: the manifest's sha256 is the sha256 of what is in the zip
            assert hashlib.sha256(zf.read(row["path"])).hexdigest() == row["sha256"] and len(zf.read(row["path"])) == row["bytes"]
        assert json.loads(zf.read("manifest.json")) == m
    # the index, the vault and its key are listed apart from the ordinary files
    listed = {row["path"] for row in m["files"]}
    assert "data/index.db" not in listed and "data/clio/secrets.enc" not in listed and "data/clio/vault.key" not in listed
    assert m["separate"]["index"]["path"] == "data/index.db" and m["separate"]["vault"]["stored"] == "in the archive"


def test_without_a_passphrase_the_key_file_is_beside_the_archive_never_inside(world):
    made = _make(world)
    assert "data/clio/vault.key" not in _zip_names(made["archive"])
    key = made["key_file"]
    assert key and key.parent == made["archive"].parent and key.read_bytes() == (world["data"] / "clio" / "vault.key").read_bytes()
    assert made["manifest"]["separate"]["vault_key"]["stored"] == "beside the archive"
    if os.name == "posix":
        assert key.stat().st_mode & 0o077 == 0  # owner only


def test_the_authenticator_key_is_listed_apart_and_never_inside_a_plain_zip_next_to_the_accounts_file(world):
    totp_key = (world["data"] / "review_users_totp.key").read_bytes().strip()
    made = _make(world, keep=1)
    raw_names = _zip_names(made["archive"])
    assert "data/review_users.json" in raw_names and "data/review_users_totp.key" not in raw_names  # the sealed secrets are in; their key is not
    with zipfile.ZipFile(made["archive"]) as zf:
        assert not any(totp_key in zf.read(n) for n in zf.namelist())  # not a byte of it anywhere in the zip
    assert totp_key not in made["archive"].read_bytes()
    row = made["manifest"]["separate"]["totp_key"]
    assert row["path"] == "data/review_users_totp.key" and row["stored"] == "beside the archive" and "data/review_users_totp.key" not in {f["path"] for f in made["manifest"]["files"]}
    beside = made["key_files"]["totp_key"]
    assert beside.name == made["archive"].name + ".totp-key" and beside.read_bytes().strip() == totp_key
    assert hashlib.sha256(beside.read_bytes()).hexdigest() == row["sha256"]
    if os.name == "posix":
        assert beside.stat().st_mode & 0o077 == 0
    second = _make(world, keep=1)  # prune removes the old one's key files with it
    assert not made["archive"].exists() and not beside.exists() and second["key_files"]["totp_key"].exists()
    report = backup.check_restore(second["archive"], None, world["out"], world["log"])
    assert report["ok"], report["problems"]


def test_in_an_encrypted_backup_the_authenticator_key_is_inside_and_still_listed_apart(world, tmp_path):
    made = _make(world, passphrase=PASSPHRASE)
    assert made["key_files"] == {} and made["manifest"]["separate"]["totp_key"]["stored"] == "in the archive"
    assert backup.restore_into(made["archive"], PASSPHRASE, tmp_path / "back")["ok"] and (tmp_path / "back" / "data" / "review_users_totp.key").exists()


def test_a_key_file_that_lists_several_keys_after_a_rotation_still_opens_the_vault(world):
    from cryptography.fernet import Fernet

    old = (world["data"] / "clio" / "vault.key").read_bytes().strip()
    new = Fernet.generate_key()
    (world["data"] / "clio" / "vault.key").write_bytes(new + b"\n" + old + b"\n")  # the new key first (it encrypts), the old one still reads
    made = _make(world, passphrase=PASSPHRASE)
    assert backup.check_restore(made["archive"], PASSPHRASE, world["out"], world["log"])["vault"] == "opens"
    (world["data"] / "clio" / "vault.key").write_bytes(Fernet.generate_key())  # a key that is not the vault's: said, not hidden
    made = _make(world, passphrase=PASSPHRASE)
    assert backup.check_restore(made["archive"], PASSPHRASE, world["out"], world["log"])["vault"] == "does not open with its key"


def test_the_backup_tool_says_which_keys_it_cannot_hold(world, tmp_path, capsys, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("backup_tool", REPO / "tools" / "backup.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    monkeypatch.setattr(backup, "LOG", world["log"])
    monkeypatch.setenv("I485_TOTP_KEY", "made-up-key-in-the-environment")
    assert tool.main(["--out", str(world["out"]), "--data-folder", str(world["data"]), "--documents", str(world["documents"])]) == 0
    out = capsys.readouterr().out
    assert "authenticator key file is kept apart" in out and "Clio key file is kept apart" in out
    assert "Not in any backup: the key(s) kept in this server's environment (I485_TOTP_KEY)" in out and "made-up-key" not in out


def test_the_log_and_summary_record_the_backup_and_the_key_is_never_in_them(world):
    made = _make(world)
    log = json.loads(world["log"].read_text(encoding="utf-8"))
    assert log["last_backup"]["file"] == made["archive"].name and log["last_backup"]["counts"]["cases"] == 2 and log["last_backup"]["complete"]
    summary = json.loads((made["archive"].with_name(made["archive"].name + ".summary.json")).read_text(encoding="utf-8"))
    assert summary["sha256"] == hashlib.sha256(made["archive"].read_bytes()).hexdigest()
    assert "case-ana" not in json.dumps(summary)  # no client name in the plain summary
    assert world["data"].joinpath("clio", "vault.key").read_text() not in world["log"].read_text()


def test_a_backup_folder_inside_the_data_folder_is_not_backed_up_into_itself(world):
    out = world["data"] / "backups"
    made = backup.make_backup(out, world["data"], documents=world["documents"], log_path=world["log"])
    assert not any("backups/" in n for n in _zip_names(made["archive"]))
    second = backup.make_backup(out, world["data"], documents=world["documents"], log_path=world["log"])  # nor the one before it
    assert not any("backups/" in n for n in _zip_names(second["archive"])) or second["archive"] == made["archive"]


def test_a_database_is_copied_whole_while_it_is_open(world):
    con = sqlite3.connect(world["data"] / "index.db")  # held open, as the app holds it
    con.execute("insert into t values ('written while open')")
    made = _make(world)
    con.close()
    report = backup.check_restore(made["archive"], None, world["out"], world["log"])
    assert report["ok"] and report["checked"]["databases"] == 2


def test_a_file_that_cannot_be_read_means_the_backup_is_not_recorded(world):
    (world["data"] / "broken.db").write_bytes(b"SQLite format 3\x00" + b"\xff" * 200)  # says it is a database; it is not
    made = _make(world)
    assert made["problems"] and "broken.db" in made["problems"][0]
    assert not world["log"].exists() or "last_backup" not in json.loads(world["log"].read_text(encoding="utf-8"))
    # what was written is kept to look at under a name no list of backups and no prune counts, key file included
    names = sorted(p.name for p in world["out"].iterdir())
    assert made["archive"].name.endswith(".zip.failed") and made["key_file"].name.endswith(".vault-key.failed")
    assert made["key_files"]["totp_key"].name.endswith(".totp-key.failed")
    assert [n for n in names if not n.endswith(".failed")] == [] and len(names) == 4  # the zip, its summary, the Clio key, the authenticator key
    assert backup.archives(world["out"]) == []
    (world["data"] / "broken.db").unlink()
    for _ in range(3):
        _make(world, keep=2)
    assert len(backup.archives(world["out"])) == 2 and len([n for n in os.listdir(world["out"]) if n.endswith(".failed")]) == 4  # pruning neither counts nor removes it


def test_a_missing_data_folder_or_a_short_passphrase_is_refused_in_plain_words(world, tmp_path):
    with pytest.raises(backup.BackupError, match="no data folder"):
        backup.make_backup(tmp_path / "out", tmp_path / "nothing-here")
    with pytest.raises(backup.BackupError, match="at least 12 characters"):
        _make(world, passphrase="short")


def test_old_backups_are_pruned_to_the_newest_few(world, monkeypatch):
    made = []
    for i in range(4):
        monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 1 + i, 2, 0, tzinfo=timezone.utc))
        made.append(_make(world, keep=2)["archive"].name)
    left = sorted(p.name for p in backup.archives(world["out"]))
    assert left == sorted(made)[-2:]
    assert not [p for p in world["out"].iterdir() if p.name.startswith(made[0])]  # its summary and key file went with it


# -- the test restore --------------------------------------------------------------------------------------------------

def test_a_test_restore_opens_everything_counts_the_cases_and_deletes_itself(world):
    made = _make(world)
    before = set(world["out"].iterdir())
    report = backup.check_restore(made["archive"], None, world["out"], world["log"])
    assert report["ok"], report["problems"]
    assert report["cases"] == {"cases": 2, "portal_clients": 1}
    assert report["checked"]["json"] >= 6 and report["checked"]["databases"] == 2
    assert report["vault"] and "kept apart" in report["vault"]  # the key was not in the archive: said, not guessed
    assert set(world["out"].iterdir()) == before  # the temporary folder is gone
    log = json.loads(world["log"].read_text(encoding="utf-8"))
    assert log["last_test_restore"]["ok"] and log["last_test_restore"]["backup"] == made["archive"].name


def test_a_damaged_file_is_found_and_the_test_restore_is_not_recorded(world):
    made = _make(world)
    broken = world["out"] / "broken.zip"
    with zipfile.ZipFile(made["archive"]) as src, zipfile.ZipFile(broken, "w") as out:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "data/clients/case-ana/packet.pdf":
                data = data[:-1] + bytes([data[-1] ^ 1])  # one bit flipped
            out.writestr(info.filename, data)
    log_before = world["log"].read_text(encoding="utf-8")
    report = backup.check_restore(broken, None, world["out"], world["log"])
    assert not report["ok"] and any("packet.pdf" in p and "does not match" in p for p in report["problems"])
    assert "last_test_restore" not in json.loads(world["log"].read_text(encoding="utf-8")) and "last_backup" in json.loads(log_before)


def test_a_missing_file_an_extra_file_and_a_wrong_case_count_are_found(world):
    made = _make(world)
    broken = world["out"] / "broken.zip"
    with zipfile.ZipFile(made["archive"]) as src, zipfile.ZipFile(broken, "w") as out:
        for info in src.infolist():
            if info.filename.startswith("data/portal/clients/ana/"):
                continue  # gone
            out.writestr(info.filename, src.read(info.filename))
        out.writestr("data/clients/case-new/meta.json", "{}")  # a case the list does not know
    problems = backup.check_restore(broken, None, world["out"], world["log"])["problems"]
    assert any("profile.json" in p and "missing" in p for p in problems) and any("events.jsonl" in p and "missing" in p for p in problems)
    assert any("case-new" in p and "not on its list" in p for p in problems)
    assert any("2 case folders" in p and "3 came back" in p for p in problems)
    assert any("1 portal clients" in p and "0 came back" in p for p in problems)


def test_a_json_file_that_was_already_broken_in_the_data_is_reported(world):
    (world["data"] / "clients" / "case-bia" / "decisions.json").write_text('{"half": ', encoding="utf-8")
    made = _make(world)
    problems = backup.check_restore(made["archive"], None, world["out"], world["log"])["problems"]
    assert problems == ["data/clients/case-bia/decisions.json: is not readable JSON (JSONDecodeError)"]


def test_a_path_that_leaves_the_folder_is_never_written(world, tmp_path):
    made = _make(world)
    evil = world["out"] / "evil.zip"
    with zipfile.ZipFile(made["archive"]) as src, zipfile.ZipFile(evil, "w") as out:
        for info in src.infolist():
            out.writestr(info.filename, src.read(info.filename))
        out.writestr("../escaped.txt", "x")
    into = tmp_path / "into"
    report = backup.restore_into(evil, None, into)
    assert any("leaves the backup folder" in p for p in report["problems"]) and not (tmp_path / "escaped.txt").exists()


# -- a real restore ----------------------------------------------------------------------------------------------------

def test_a_restore_puts_every_file_back_byte_for_byte_and_never_over_anything(world, tmp_path):
    made = _make(world)
    into = tmp_path / "restored"
    report = backup.restore_into(made["archive"], None, into)
    assert report["ok"], report["problems"]
    original = world["data"]
    for path in sorted(original.rglob("*")):
        rel = path.relative_to(original)
        if not path.is_file() or path.suffix in (".tmp", ".lock") or path.name in ("backup_log.json", "vault.key", "review_users_totp.key"):
            continue
        if path.suffix == ".db":  # a database is a fresh copy: same rows, not the same bytes
            a, b = sqlite3.connect(path), sqlite3.connect(into / "data" / rel)
            assert a.execute("select * from t").fetchall() == b.execute("select * from t").fetchall()
            a.close()
            b.close()
        else:
            assert (into / "data" / rel).read_bytes() == path.read_bytes(), rel
    assert (into / "documents" / "case-ana" / "scan1.pdf").read_bytes() == b"%PDF scan"
    if os.name == "posix":  # client data: the owner only, whatever the umask
        assert all(p.stat().st_mode & 0o077 == 0 for p in [into, *into.rglob("*")]), [str(p) for p in into.rglob("*") if p.stat().st_mode & 0o077]
    assert not (into / "data" / "clio" / "vault.key").exists() and not (into / "data" / "review_users_totp.key").exists()  # beside the archive, not in it
    with pytest.raises(backup.BackupError, match="not empty"):
        backup.restore_into(made["archive"], None, into)
    (tmp_path / "afile").write_text("x")
    with pytest.raises(backup.BackupError, match="not empty"):
        backup.restore_into(made["archive"], None, tmp_path / "afile")


# -- encryption --------------------------------------------------------------------------------------------------------

def test_an_encrypted_backup_shows_nothing_and_opens_only_with_the_passphrase(world, tmp_path):
    made = _make(world, passphrase=PASSPHRASE)
    archive = made["archive"]
    assert archive.name.endswith(".zip.enc") and made["key_file"] is None
    raw = archive.read_bytes()
    assert SECRET_WORD.encode() not in raw and b"PK\x03\x04" not in raw[:64] and b"case-ana" not in raw
    assert not any(p.name.endswith(".zip") for p in world["out"].iterdir())  # no plain copy left behind
    assert made["manifest"]["separate"]["vault_key"]["stored"] == "in the archive" and made["manifest"]["encrypted"]
    report = backup.check_restore(archive, PASSPHRASE, world["out"], world["log"])
    assert report["ok"] and report["vault"] == "opens" and report["encrypted"]  # the vault opens with the key that came with it
    for wrong in ("another long passphrase", ""):
        with pytest.raises(backup.BackupError, match="passphrase"):
            backup.check_restore(archive, wrong or None, world["out"], world["log"])
    report = backup.restore_into(archive, PASSPHRASE, tmp_path / "back")
    assert report["ok"] and (tmp_path / "back" / "data" / "clio" / "vault.key").exists()


def test_a_cut_short_or_rearranged_encrypted_backup_is_noticed(world, tmp_path):
    (world["data"] / "big.bin").write_bytes(os.urandom(int(backup.CHUNK * 2.5)))  # several pieces
    archive = _make(world, passphrase=PASSPHRASE)["archive"]
    raw = archive.read_bytes()
    pieces, i, head = [], len(backup.MAGIC) + 19, raw[:len(backup.MAGIC) + 19]
    while i < len(raw):
        n = int.from_bytes(raw[i:i + 4], "big")
        pieces.append(raw[i:i + 4 + n])
        i += 4 + n
    assert len(pieces) >= 3
    cut = tmp_path / "cut.zip.enc"
    cut.write_bytes(head + b"".join(pieces[:-1]))
    with pytest.raises(backup.BackupError, match="cut short"):
        backup.check_restore(cut, PASSPHRASE, world["out"], world["log"])
    swapped = tmp_path / "swapped.zip.enc"
    swapped.write_bytes(head + pieces[1] + pieces[0] + b"".join(pieces[2:]))
    with pytest.raises(backup.BackupError, match="damaged"):
        backup.check_restore(swapped, PASSPHRASE, world["out"], world["log"])
    flipped = bytearray(raw)
    flipped[len(raw) // 2] ^= 1
    (tmp_path / "flipped.zip.enc").write_bytes(bytes(flipped))
    with pytest.raises(backup.BackupError, match="damaged"):
        backup.check_restore(tmp_path / "flipped.zip.enc", PASSPHRASE, world["out"], world["log"])
    plain = tmp_path / "notours.zip.enc"
    plain.write_bytes(backup.MAGIC + b"x")
    with pytest.raises(backup.BackupError):
        backup.check_restore(plain, PASSPHRASE, world["out"], world["log"])


# -- what Keeping current shows ------------------------------------------------------------------------------------------

def _log(path: Path, backup_at: str | None, restore_at: str | None) -> None:
    path.write_text(json.dumps({**({"last_backup": {"at": backup_at}} if backup_at else {}),
                                **({"last_test_restore": {"at": restore_at}} if restore_at else {})}), encoding="utf-8")


def test_keeping_current_says_when_the_last_backup_and_test_restore_were_and_which_is_overdue(tmp_path):
    log = tmp_path / "log.json"
    today = date(2026, 10, 3)
    s = backup.status(today, tmp_path / "none.json")
    assert s["overdue"] and s["line"] == "No backup has been made yet; no test restore yet: overdue."
    _log(log, "2026-10-03T02:00:00-04:00", "2026-09-28T09:00:00-04:00")
    s = backup.status(today, log)
    assert s["line"] == "Last backup 10/03/2026; last test restore 09/28/2026, 5 days ago." and not s["overdue"] and s["findings"] == []
    _log(log, "2026-10-03T02:00:00-04:00", "2026-08-30T09:00:00-04:00")
    s = backup.status(today, log)
    assert s["line"] == "Last backup 10/03/2026; last test restore 08/30/2026, 34 days ago: overdue." and s["overdue"]
    assert s["findings"] == ["The last test restore was 34 days ago; do one every month."]
    _log(log, "2026-09-28T02:00:00-04:00", "2026-10-03T09:00:00-04:00")
    s = backup.status(today, log)
    assert s["line"] == "Last backup 09/28/2026, 5 days ago: overdue; last test restore 10/03/2026, today." and s["overdue"]
    assert "backup should run every night" in s["findings"][0]
    _log(log, "2026-10-02T02:00:00-04:00", None)  # last night's: not overdue
    assert backup.status(today, log)["line"] == "Last backup 10/02/2026; no test restore yet: overdue."


def test_a_log_nobody_can_read_is_no_log(tmp_path):
    bad = tmp_path / "log.json"
    bad.write_text("{not json", encoding="utf-8")
    assert backup.read_log(bad) == {} and backup.status(date(2026, 10, 3), bad)["overdue"]


# -- the command line --------------------------------------------------------------------------------------------------

def test_the_command_line_tools_exit_with_the_codes_they_promise(world, tmp_path, capsys, monkeypatch):
    import backups as tool_lib  # noqa: F401
    import importlib.util

    def load(name):
        spec = importlib.util.spec_from_file_location(name, REPO / "tools" / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    cli_backup, cli_restore = load("backup"), load("restore")
    monkeypatch.setattr(backup, "LOG", world["log"])
    phrase = tmp_path / "phrase.txt"
    phrase.write_text(PASSPHRASE + "\n", encoding="utf-8")
    code = cli_backup.main(["--out", str(world["out"]), "--data-folder", str(world["data"]), "--documents", str(world["documents"]), "--passphrase-file", str(phrase)])
    out = capsys.readouterr().out
    assert code == 0 and "Backup written" in out and "2 case folders" in out and "encrypted" in out
    archive = backup.archives(world["out"])[-1]
    assert cli_restore.main([str(archive), "--check", "--passphrase-file", str(phrase), "--work-folder", str(tmp_path)]) == 0
    assert "Nothing is wrong. Recorded as the last test restore." in capsys.readouterr().out
    wrong = tmp_path / "wrong.txt"
    wrong.write_text("not the passphrase at all", encoding="utf-8")
    assert cli_restore.main([str(archive), "--check", "--passphrase-file", str(wrong)]) == 2
    assert "passphrase" in capsys.readouterr().err
    assert cli_backup.main(["--out", str(tmp_path / "o2"), "--data-folder", str(tmp_path / "nope")]) == 2
    assert "Not backed up" in capsys.readouterr().err
    # run by cron or Task Scheduler: an encrypted backup, no passphrase given and nobody to ask: plain words and exit 2, no traceback
    monkeypatch.delenv("I485_BACKUP_PASSPHRASE", raising=False)
    import getpass

    def no_terminal(prompt=""):
        raise EOFError

    monkeypatch.setattr(getpass, "getpass", no_terminal)
    assert cli_restore.main([str(archive), "--check"]) == 2
    err = capsys.readouterr().err
    assert "no terminal to ask for its passphrase" in err and "--passphrase-file" in err and "Traceback" not in err
    monkeypatch.setenv("I485_BACKUP_PASSPHRASE", PASSPHRASE)  # the environment variable is the other unattended way
    assert cli_restore.main([str(archive), "--check", "--work-folder", str(tmp_path)]) == 0


# -- the upkeep register's backup item ------------------------------------------------------------------------------------

def test_the_backup_item_in_keeping_current_shows_both_dates_and_is_due_only_when_the_log_says_so(tmp_path, monkeypatch):
    import deployment
    import maintenance
    from review.server import ReviewApp

    monkeypatch.setattr(backup, "LOG", tmp_path / "backup_log.json")
    monkeypatch.setattr(maintenance, "FIRM_LOG", tmp_path / "firm_log.json")
    monkeypatch.setattr(maintenance, "LAST_LIVE", tmp_path / "live.json")
    monkeypatch.setattr(deployment, "PATH", tmp_path / "deployment.json")
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 3, 15, 0, tzinfo=timezone.utc))
    clients = tmp_path / "clients"
    clients.mkdir()

    def shown(hosted: bool = False) -> dict:
        (tmp_path / "deployment.json").write_text(json.dumps({"mode": "hosted" if hosted else "on_premises", "provider": {"name": "Acme"}}), encoding="utf-8")
        app = ReviewApp(clients, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None)
        m = app.maintenance()
        return next(i for i in m["items"] + m["provider_items"] if i["id"] == "backups")

    item = next(i for i in maintenance.status(date(2026, 10, 3)) if i["id"] == "backups")
    assert item["due"] and item["findings"][0].startswith("No backup has been made yet") and item["last_checked"] is None
    assert shown()["note"] == "No backup has been made yet; no test restore yet: overdue."
    _log(tmp_path / "backup_log.json", "2026-10-03T02:00:00-04:00", "2026-09-28T09:00:00-04:00")
    item = next(i for i in maintenance.status(date(2026, 10, 3)) if i["id"] == "backups")
    assert not item["due"] and item["findings"] == [] and item["last_checked"] == "2026-09-28"  # the monthly check is the test restore
    row = shown()
    assert row["note"] == "Last backup 10/03/2026; last test restore 09/28/2026, 5 days ago." and not row["due"] and row["last_checked"] == "2026-09-28"
    _log(tmp_path / "backup_log.json", "2026-10-03T02:00:00-04:00", "2026-08-30T09:00:00-04:00")
    row = shown()
    assert row["due"] and "34 days ago: overdue" in row["note"] and row["findings"] == ["The last test restore was 34 days ago; do one every month."]
    assert all("tools/" not in s and ".py" not in s for s in row["steps"])  # the firm's steps name no file
    hosted = shown(hosted=True)  # hosted: the provider's item, shown to the firm as a status with the same dates
    assert hosted["status"] == "Being updated" and "34 days ago: overdue" in hosted["note"] and "steps" not in hosted


# -- a backup made before the schemas folder was put in order (release 2026.10.9) -----------------------------------------

def test_a_backup_made_before_the_schemas_folders_restores_as_it_was_and_maps_nothing(world, tmp_path):
    """Until 2026.10.9 the schemas folder was one flat list; it is one folder per kind now (docs/ARCHITECTURE.md). A backup holds the firm's
    data and never the product's own schemas, so there is nothing in an old backup to map and nothing to refuse: it restores byte for byte,
    even a copy of the old flat folder the firm kept among its documents (it comes back where it was; no schema of the product is touched),
    and the case records in it, which never named a schema file, are read by the new layout as they were."""
    import packet

    case = world["data"] / "clients" / "case-ana"
    old = {"packet.json": {"filing": "i485", "forms": [], "version": "2026.9.30"}, "packet_i360.json": {"filing": "i360", "forms": [], "version": "2026.9.30"},
           "packet_choices.json": {"files": {}}}
    for name, body in old.items():  # the records a case holds, named as they have always been
        (case / name).write_text(json.dumps(body), encoding="utf-8")
    flat = world["documents"] / "old-install" / schema_path.ROOT.name  # a copy of the product as it was, the old flat layout, kept among the documents
    flat.mkdir(parents=True)
    for name in ("fees.json", "cover_letter.json", "i485_template.pdf"):
        (flat / name).write_bytes(b"%PDF-1.4 old" if name.endswith(".pdf") else json.dumps({"old": name}).encode())
    shipped = {p: p.read_bytes() for p in schema_path.all_files(".json")}
    made = _make(world)
    back = tmp_path / "back"
    report = backup.restore_into(made["archive"], None, back)
    assert report["ok"] and not report["problems"]
    for name, body in old.items():
        assert json.loads((back / "data" / "clients" / "case-ana" / name).read_text(encoding="utf-8")) == body
        assert (back / "data" / "clients" / "case-ana" / name).read_bytes() == (case / name).read_bytes()
    for name in ("fees.json", "cover_letter.json", "i485_template.pdf"):  # nothing was mapped to a new place, and nothing was refused
        assert (back / "documents" / "old-install" / schema_path.ROOT.name / name).read_bytes() == (flat / name).read_bytes()
    assert {p: p.read_bytes() for p in schema_path.all_files(".json")} == shipped  # the product's own schemas are not touched by a restore
    for filing in ("i485", "i360"):  # the restored records work with the new layout
        manifest = json.loads((back / "data" / "clients" / "case-ana" / packet.FILINGS[filing]).read_text(encoding="utf-8"))
        assert packet.load_filing(manifest["filing"]).get("filing", "i485") == filing
