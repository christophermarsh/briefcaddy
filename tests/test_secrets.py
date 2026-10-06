# ruff: noqa: F811  (the made-up firm's fixtures are imported from tests/test_restricted.py and used as arguments)
"""Keys and secrets on the firm's machine (src/firmsecrets.py, tools/rotate_secret.py; brief R4): one reader by name, the environment wins, the vault is owner-only, nothing is
ever shown or logged, a secret is rotated with a record of the change, and the page says when. Every secret here is made up (built, never written as a literal of the shape the scan
looks for)."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import clock
import events
import firmsecrets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import rotate_secret  # noqa: E402
from test_restricted import LISTING, NEITHER, QUERY, app, call, server, sign_in, world  # noqa: E402, F401

REPO = Path(__file__).resolve().parent.parent


def made_up(name: str) -> str:
    return f"made-up-{name.replace('.', '-')}-q7x2"  # built, so no literal in this file has the shape of a key


@pytest.fixture
def data(tmp_path, monkeypatch):
    folder = tmp_path / "data"
    folder.mkdir()
    monkeypatch.setenv("I485_EVENTS", str(folder / "events.jsonl"))
    for s in firmsecrets.SECRETS:
        for n in s.env + ((s.file_env,) if s.file_env else ()):
            monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(firmsecrets, "DATA", folder)
    monkeypatch.setattr(firmsecrets, "REPO", tmp_path)  # the installer's folder is tmp_path/install
    return folder


def modes(path: Path) -> str:
    return oct(path.stat().st_mode & 0o777)


# -- one reader, by name --------------------------------------------------------------------------------------------------------------------------


def test_a_secret_is_read_by_name_the_environment_wins_then_the_vault_and_nothing_set_is_none(data):
    name = "smtp.password"
    assert firmsecrets.get(name, data_root=data) is None and firmsecrets.where(name, data_root=data) is None
    firmsecrets.put(name, made_up("in-vault"), "Sam Attorney", data_root=data)
    assert firmsecrets.get(name, data_root=data) == made_up("in-vault") and firmsecrets.where(name, data_root=data) == "vault"
    env = {"SMTP_PASSWORD": made_up("in-environment")}
    assert firmsecrets.get(name, env=env, data_root=data) == made_up("in-environment") and firmsecrets.where(name, env=env, data_root=data) == "environment"
    assert firmsecrets.get(name, env={"SMTP_PASSWORD": "  "}, data_root=data) == made_up("in-vault")  # a blank setting is not a setting


def test_every_name_is_known_and_every_kind_has_a_register_item(data):
    import maintenance

    assert len(firmsecrets.BY_NAME) == len(firmsecrets.SECRETS) and {s.kind for s in firmsecrets.SECRETS} == set(firmsecrets.KINDS)
    items = {i["id"]: i for i in maintenance.registry()["items"]}
    for kind in firmsecrets.KINDS:
        item = items["secret_" + kind]
        assert item["cadence"] == "not set" and item["check"] == {"type": "secret_rotation", "kind": kind} and item["firm_steps"] and item["firm_what"] and item["steps"]
        assert "choose how often" in " ".join(item["firm_steps"]) and "never" in " ".join(item["firm_steps"]).lower()
    with pytest.raises(KeyError):
        firmsecrets.get("nobody.knows")


def test_the_vault_and_its_key_and_the_record_are_owner_only(data):
    firmsecrets.put("smtp.password", made_up("a"), "Sam Attorney", data_root=data)
    firmsecrets.set_cadence("mail", "yearly", "Sam Attorney", data_root=data)
    assert modes(data / "clio" / "secrets.enc") == "0o600" and modes(data / "clio" / "vault.key") == "0o600" and modes(data / "secrets_log.json") == "0o600"
    assert made_up("a").encode() not in (data / "clio" / "secrets.enc").read_bytes()  # encrypted at rest


def test_the_staff_key_the_backups_passphrase_and_a_key_file_are_read_by_name_too(data, tmp_path):
    key = tmp_path / "users_totp.key"
    one = firmsecrets.get("accounts.totp_key", path=key, create=True)
    assert one and modes(key) == "0o600" and firmsecrets.where("accounts.totp_key", path=key) == "file"
    assert firmsecrets.get("accounts.totp_key", env={"I485_TOTP_KEY": "x"}, path=key) == "x" and firmsecrets.where("accounts.totp_key", env={"I485_TOTP_KEY": "x"}, path=key) == "environment"
    install = tmp_path / "install"
    install.mkdir()
    (install / "backup_passphrase.txt").write_text(made_up("first") + "\n" + made_up("older") + "\n", encoding="utf-8")
    assert firmsecrets.get("backups.passphrase") == made_up("first") and firmsecrets.get_all("backups.passphrase") == [made_up("first"), made_up("older")]
    assert firmsecrets.get("backups.passphrase", env={"I485_BACKUP_PASSPHRASE": made_up("env")}) == made_up("env")
    named = tmp_path / "elsewhere.txt"
    named.write_text(made_up("named") + "\n", encoding="utf-8")
    assert firmsecrets.get("backups.passphrase", env={"I485_BACKUP_PASSPHRASE_FILE": str(named)}) == made_up("named")
    assert firmsecrets.get("backups.passphrase", path=named, explicit=True, env={"I485_BACKUP_PASSPHRASE": made_up("env")}) == made_up("named")  # --passphrase-file beats the environment


# -- the deployment record keeps no key -------------------------------------------------------------------------------------------------------------


def test_a_key_found_in_the_deployment_record_moves_into_the_vault_and_the_ledger_says_so(data, tmp_path):
    record = tmp_path / "deployment.json"
    record.write_text(json.dumps({"mode": "on_premises", "case_status": {"client_id": "an-id", "client_secret": made_up("uscis"), "environment": "sandbox"}}), encoding="utf-8")
    said = firmsecrets.migrate_deployment(data_root=data, deployment_path=record, env={})
    assert said and "moved out of deployment.json into the vault" in said[0]
    assert firmsecrets.get("uscis.client_secret", env={}, data_root=data) == made_up("uscis") and firmsecrets.where("uscis.client_secret", env={}, data_root=data) == "vault"
    left = json.loads(record.read_text(encoding="utf-8"))
    assert left["case_status"] == {"client_id": "an-id", "environment": "sandbox"} and modes(record) == "0o600" and made_up("uscis") not in record.read_text(encoding="utf-8")
    rows = [r for r in events.rows(data / "events.jsonl") if r["kind"] == "secrets"]
    assert len(rows) == 1 and rows[0]["action"] == "migrated" and rows[0]["what"] == "Moved into the vault USCIS case status: the client secret"
    assert firmsecrets.migrate_deployment(data_root=data, deployment_path=record, env={}) == []  # nothing left to move
    assert made_up("uscis") not in json.dumps(rows) + (data / "secrets_log.json").read_text(encoding="utf-8")


def test_startup_composition_migrates_only_selected_fictional_record_and_vault(data, tmp_path, monkeypatch):
    import deployment
    from law_app.bootstrap.config import staff_arguments
    from law_app.bootstrap.composition import build_staff_app
    from review import server as srv

    unrelated = tmp_path / "unrelated-deployment.json"
    original = json.dumps({"case_status": {"client_secret": made_up("unrelated")}})
    unrelated.write_text(original, encoding="utf-8")
    monkeypatch.setattr(deployment, "PATH", unrelated)
    stop = RuntimeError("stop before unrelated startup work")
    def index(*_):
        raise stop
    monkeypatch.setattr(srv.oversight, "Views", index)
    migrate = firmsecrets.migrate_deployment
    calls = []
    def migration(**kwargs):
        calls.append(kwargs)
        return migrate(**kwargs, env={})
    monkeypatch.setattr(firmsecrets, "migrate_deployment", migration)

    for name in ("first", "second"):
        install = tmp_path / name
        clients = install / "data" / "clients"
        clients.mkdir(parents=True)
        record = install / "selected-deployment.json"
        record.write_text(json.dumps({"mode": "on_premises", "case_status": {
            "client_id": name, "client_secret": made_up(name)}}), encoding="utf-8")
        args = staff_arguments(["--deployment", str(record)], repo=install, environ={})
        with pytest.raises(RuntimeError) as caught:
            build_staff_app(args, accounts_factory=lambda *_: pytest.fail("No account file"),
                            app_factory=srv.ReviewApp, local_setup=False)
        assert caught.value is stop
        assert calls[-1] == {"data_root": clients.parent.resolve(), "deployment_path": record}
        assert json.loads(record.read_text())["case_status"] == {"client_id": name}
        assert firmsecrets.get("uscis.client_secret", data_root=clients.parent, env={}) == made_up(name)
        assert unrelated.read_text() == original
    assert len(calls) == 2
    assert firmsecrets.get("uscis.client_secret", data_root=tmp_path / "first" / "data", env={}) == made_up("first")
    assert not (data / "clio").exists(), "Neither selected migration may fall back to the default vault"


def test_the_case_status_secret_is_never_read_from_the_deployment_record(tmp_path):
    import case_status

    record = tmp_path / "deployment.json"
    record.write_text(json.dumps({"case_status": {"client_id": "an-id", "client_secret": made_up("uscis")}}), encoding="utf-8")
    assert not case_status.config({}, deployment_path=record)["ready"]
    assert case_status.config({"USCIS_CASE_STATUS_CLIENT_SECRET": made_up("env")}, deployment_path=record)["ready"]


# -- rotation ----------------------------------------------------------------------------------------------------------------------------------------


def test_rotating_a_vault_secret_saves_it_and_records_the_name_the_day_and_who_never_the_value(data):
    said = rotate_secret.rotate("smtp.password", "Pat IT", data_root=data, env={}, value=made_up("new"))
    assert said == "smtp.password was changed in the vault."
    assert firmsecrets.get("smtp.password", env={}, data_root=data) == made_up("new")
    log = firmsecrets.read_log(data)
    assert [(c["name"], c["by"], c["how"]) for c in log["changes"]] == [("smtp.password", "Pat IT", "rotated")] and log["changes"][0]["at"].startswith(str(clock.today().year))
    ledger = [r for r in events.rows(data / "events.jsonl") if r["kind"] == "secrets"]
    assert len(ledger) == 1 and ledger[0]["who"] == "Pat IT" and ledger[0]["what"] == "Changed the mail server's password"
    assert made_up("new") not in json.dumps(ledger) + json.dumps(log)


def test_a_secret_the_environment_holds_is_not_written_over_it_only_the_change_is_recorded(data):
    env = {"TWILIO_AUTH_TOKEN": made_up("env")}
    with pytest.raises(ValueError, match="held in the server's environment, which wins"):
        rotate_secret.rotate("twilio.auth_token", "Pat IT", data_root=data, env=env, value=made_up("new"))
    assert not (data / "clio" / "secrets.enc").exists()
    assert "Recorded that twilio.auth_token was changed" in rotate_secret.rotate("twilio.auth_token", "Pat IT", data_root=data, env=env, env_changed=True)
    assert [(c["name"], c["how"]) for c in firmsecrets.read_log(data)["changes"]] == [("twilio.auth_token", "recorded")]
    with pytest.raises(ValueError, match="not held in the server's environment here"):
        rotate_secret.rotate("smtp.password", "Pat IT", data_root=data, env={}, env_changed=True)


def test_clios_tokens_are_clios_and_an_empty_value_or_a_made_up_name_changes_nothing(data):
    with pytest.raises(ValueError, match="Clio's, not the firm's"):
        rotate_secret.rotate("clio.refresh_token", "Pat IT", data_root=data, env={}, value=made_up("t"))
    with pytest.raises(ValueError, match="No new value was given"):
        rotate_secret.rotate("smtp.password", "Pat IT", data_root=data, env={}, value="  ")
    with pytest.raises(ValueError, match="There is no secret called"):
        rotate_secret.rotate("no.such", "Pat IT", data_root=data, env={}, value="x")
    with pytest.raises(ValueError, match="given by the provider"):
        rotate_secret.rotate("smtp.password", "Pat IT", data_root=data, env={}, generate=True)
    assert firmsecrets.read_log(data)["changes"] == []


def test_rotating_the_vaults_key_saves_every_secret_again_under_a_new_key_and_drops_the_old(data):
    from connectors import clio

    firmsecrets.put("smtp.password", made_up("mail"), "Sam", data_root=data)
    firmsecrets.put("clio.client_secret", made_up("clio"), "Sam", data_root=data)
    key = data / "clio" / "vault.key"
    before, vault_before = key.read_bytes(), (data / "clio" / "secrets.enc").read_bytes()
    with pytest.raises(ValueError, match="--generate"):
        rotate_secret.rotate("clio.vault_key", "Pat IT", data_root=data, env={})
    rotate_secret.rotate("clio.vault_key", "Pat IT", data_root=data, env={}, generate=True)
    assert key.read_bytes() != before and len(key.read_bytes().split()) == 1 and modes(key) == "0o600"  # one key, the new one
    assert (data / "clio" / "secrets.enc").read_bytes() != vault_before
    assert firmsecrets.get("smtp.password", env={}, data_root=data) == made_up("mail") and clio.credentials(data, {})[1] == made_up("clio")
    from cryptography.fernet import Fernet, InvalidToken

    with pytest.raises(InvalidToken):
        Fernet(before.strip()).decrypt((data / "clio" / "secrets.enc").read_bytes())  # the old key no longer opens it
    assert [(c["name"], c["how"]) for c in firmsecrets.read_log(data)["changes"] if c["name"] == "clio.vault_key"] == [("clio.vault_key", "rotated")]


def test_a_vault_key_in_the_environment_is_rotated_by_putting_the_new_one_first_then_recording(data):
    from cryptography.fernet import Fernet

    from connectors import clio

    old = Fernet.generate_key().decode()
    env = {"CLIO_TOKEN_KEY": old}
    clio.Vault(data, env).update(client_secret=made_up("clio"))
    with pytest.raises(ValueError, match="held in the server's environment"):
        rotate_secret.rotate("clio.vault_key", "Pat IT", data_root=data, env=env, generate=True)
    new = Fernet.generate_key().decode()
    env["CLIO_TOKEN_KEY"] = f"{new},{old}"  # IT puts the new key first; both read
    said = rotate_secret.rotate("clio.vault_key", "Pat IT", data_root=data, env=env, env_changed=True)  # the vault is written again under the first, and the change recorded
    assert "Recorded that clio.vault_key was changed" in said and "1 saved value(s)" in said and "Remove the old key" in said
    env["CLIO_TOKEN_KEY"] = new  # and the old one removed
    assert clio.Vault(data, env).read() == {"client_secret": made_up("clio")}
    from cryptography.fernet import InvalidToken

    with pytest.raises(InvalidToken):
        Fernet(old.encode()).decrypt((data / "clio" / "secrets.enc").read_bytes())


def test_rotating_the_staff_key_saves_every_authenticator_secret_again(data):
    import second_factor
    from review.auth import Accounts

    accounts = Accounts(data / "review_users.json")
    accounts.change_password("sam@firm.example", accounts.add("sam@firm.example", "Sam Attorney", "attorney"), "a-made-up-passphrase-1")
    second_factor.set_up(accounts, "sam@firm.example", "a-made-up-passphrase-1")
    key = data / "review_users_totp.key"
    before = key.read_bytes()
    sealed = accounts._load()["users"]["sam@firm.example"]["totp"]["secret"]
    plain = accounts._open(sealed)
    rotate_secret.rotate("accounts.totp_key", "Pat IT", data_root=data, env={}, generate=True, users=data / "review_users.json")
    assert key.read_bytes() != before and len(key.read_bytes().split()) == 1 and modes(key) == "0o600"
    again = Accounts(data / "review_users.json")
    resealed = again._load()["users"]["sam@firm.example"]["totp"]["secret"]
    assert resealed != sealed and again._open(resealed) == plain  # the same authenticator secret, saved under the new key alone
    assert [c["name"] for c in firmsecrets.read_log(data)["changes"]] == ["accounts.totp_key"]


def test_the_backups_passphrase_goes_first_and_the_older_ones_stay_so_an_older_backup_still_opens(data, tmp_path):
    import backups

    folder = tmp_path / "work"
    (folder / "data").mkdir(parents=True)
    (folder / "data" / "settings.json").write_text(json.dumps({"firm": {}}), encoding="utf-8")
    (folder / "data" / "clients").mkdir()
    old, new = made_up("old-passphrase-one"), made_up("new-passphrase-two")
    made = backups.make_backup(tmp_path / "bak", folder / "data", passphrase=old)
    rotate_secret.rotate("backups.passphrase", "Pat IT", data_root=data, env={}, value=old)
    rotate_secret.rotate("backups.passphrase", "Pat IT", data_root=data, env={}, value=new)
    file = tmp_path / "install" / "backup_passphrase.txt"
    assert file.read_text(encoding="utf-8").split() == [new, old] and modes(file) == "0o600"
    assert firmsecrets.get("backups.passphrase") == new and backups.drill_passphrase() == [new, old]
    out = tmp_path / "restored"
    backups.restore_into(made["archive"], backups.drill_passphrase(), out)  # the older backup opens: restore tries each
    assert (out / "data" / "settings.json").exists()
    with pytest.raises(backups.BackupError):
        backups.restore_into(made["archive"], [new], tmp_path / "restored-2")
    with pytest.raises(ValueError, match="at least"):
        rotate_secret.rotate("backups.passphrase", "Pat IT", data_root=data, env={}, value="short")


def test_the_new_value_is_taken_on_standard_input_never_as_an_argument(data):
    env = {**os.environ, "I485_EVENTS": str(data / "events.jsonl"), "PYTHONPATH": str(REPO / "src")}
    for n in ("SMTP_PASSWORD",):
        env.pop(n, None)
    run = subprocess.run([sys.executable, str(REPO / "tools" / "rotate_secret.py"), "smtp.password", "--data", str(data), "--by", "Pat IT"], input=made_up("stdin") + "\n", env=env,
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and made_up("stdin") not in run.stdout + run.stderr, run.stderr
    assert firmsecrets.get("smtp.password", env={}, data_root=data) == made_up("stdin")
    help_text = subprocess.run([sys.executable, str(REPO / "tools" / "rotate_secret.py"), "--help"], env=env, capture_output=True, text=True, timeout=60).stdout
    assert "standard input" in help_text and "--value" not in help_text and "--password" not in help_text
    listing = subprocess.run([sys.executable, str(REPO / "tools" / "rotate_secret.py"), "--list", "--data", str(data)], env=env, capture_output=True, text=True, timeout=60).stdout
    assert "smtp.password" in listing and "vault" in listing and made_up("stdin") not in listing


# -- the page and the morning report -----------------------------------------------------------------------------------------------------------------


def test_the_page_says_where_each_secret_lives_when_it_was_changed_by_whom_and_when_it_is_due_never_a_value(data, monkeypatch):
    monkeypatch.setattr(clock, "_now_override", __import__("datetime").datetime(2026, 10, 4, 10, 0))
    firmsecrets.put("smtp.password", made_up("mail"), "Pat IT", data_root=data, how="rotated")
    rows = {r["kind"]: r for r in firmsecrets.status(data_root=data, env={"TWILIO_AUTH_TOKEN": made_up("text")})}
    mail, text, clio_row = rows["mail"], rows["text"], rows["clio"]
    assert mail["names"][0]["where"] == "vault" and mail["last_changed"] == "10/04/2026" and mail["by"] == "Pat IT" and mail["cadence"] == "not set" and not mail["overdue"] and mail["due_on"] is None
    assert text["names"][0]["where"] == "environment" and text["last_changed"] is None and clio_row["in_use"] is False and not clio_row["overdue"]
    firmsecrets.set_cadence("mail", "quarterly", "Sam Attorney", data_root=data)
    monkeypatch.setattr(clock, "_now_override", __import__("datetime").datetime(2027, 1, 10, 10, 0))
    late = {r["kind"]: r for r in firmsecrets.status(data_root=data, env={})}["mail"]
    assert late["cadence"] == "quarterly" and str(late["due_on"]) == "2027-01-04" and late["overdue"]  # 92 days after the day it was last changed
    assert "Mail (due 01/04/2027)" in firmsecrets.morning_line(data_root=data, env={})
    assert firmsecrets.morning_line(data_root=data.parent / "none", env={}) == "Keys and secrets: none overdue for a change."
    dump = json.dumps(rows, default=str) + json.dumps(late, default=str)
    assert made_up("mail") not in dump and made_up("text") not in dump


def test_a_cadence_with_nothing_changed_since_counts_from_the_day_it_was_chosen_and_not_set_is_never_due(data, monkeypatch):
    monkeypatch.setattr(clock, "_now_override", __import__("datetime").datetime(2026, 10, 4, 10, 0))
    firmsecrets.put("smtp.password", made_up("mail"), "Pat IT", data_root=data)  # (this install has not changed it since, say, a year ago: set by hand)
    log = firmsecrets.read_log(data)
    log["changes"][0]["at"] = "2025-01-01T09:00:00-05:00"
    (data / "secrets_log.json").write_text(json.dumps(log), encoding="utf-8")
    assert not firmsecrets.status(data_root=data, env={})[2]["overdue"]  # not set: never overdue, however old
    firmsecrets.set_cadence("mail", "monthly", "Sam Attorney", data_root=data)
    assert not {r["kind"]: r for r in firmsecrets.status(data_root=data, env={})}["mail"]["overdue"]  # counted from today, not from the old change
    with pytest.raises(ValueError):
        firmsecrets.set_cadence("mail", "every other tuesday", "Sam", data_root=data)
    with pytest.raises(ValueError):
        firmsecrets.set_cadence("no-such-kind", "monthly", "Sam", data_root=data)


def test_the_register_item_turns_due_when_the_chosen_cadence_passes_and_never_before(data, monkeypatch):
    import maintenance

    monkeypatch.setattr(maintenance, "FIRM_LOG", data / "maintenance_log.json")
    monkeypatch.setattr(clock, "_now_override", __import__("datetime").datetime(2026, 10, 4, 10, 0))
    firmsecrets.put("smtp.password", made_up("mail"), "Pat IT", data_root=data)
    item = next(i for i in maintenance.status() if i["id"] == "secret_mail")
    assert item["cadence"] == "not set" and not item["due"] and item["findings"] == []
    firmsecrets.set_cadence("mail", "monthly", "Sam Attorney", data_root=data)
    monkeypatch.setattr(clock, "_now_override", __import__("datetime").datetime(2026, 12, 10, 10, 0))
    item = next(i for i in maintenance.status() if i["id"] == "secret_mail")
    assert item["due"] and item["cadence"] == "monthly" and "Due since 11/04/2026" in item["findings"][0] and item["last_checked"] == "2026-10-04"


def test_the_morning_report_names_an_overdue_kind_and_the_run_moves_a_key_out_of_the_deployment_record(data, tmp_path, monkeypatch):
    import deployment
    import overnight

    record = tmp_path / "deployment.json"
    record.write_text(json.dumps({"case_status": {"client_id": "an-id", "client_secret": made_up("uscis")}}), encoding="utf-8")
    monkeypatch.setattr(deployment, "PATH", record)
    monkeypatch.setattr(clock, "_now_override", __import__("datetime").datetime(2026, 10, 4, 10, 0))
    text = overnight.secrets_night(data)
    assert "moved out of deployment.json into the vault" in text and "none overdue" in text and made_up("uscis") not in text
    firmsecrets.set_cadence("uscis", "monthly", "Sam Attorney", data_root=data)
    monkeypatch.setattr(clock, "_now_override", __import__("datetime").datetime(2026, 12, 10, 10, 0))
    assert overnight.secrets_night(data) == "Keys and secrets overdue for a change: USCIS case status (due 11/04/2026)."


# -- no other way in --------------------------------------------------------------------------------------------------------------------------------


# where a literal is allowed: the one module, and the few that must name a file by its name (the vault's class, the backup's roles, the installer, this tool's listing)
FILE_LITERALS = {"secrets.enc": {"connectors/clio.py", "backups.py", "records.py"}, "vault.key": {"connectors/clio.py", "backups.py", "records.py"},
                 "_totp.key": {"review/auth.py", "backups.py", "records.py"}, "backup_passphrase.txt": {"install_support.py", "leave.py"}}


def _installation_path_declarations(tree):
    """Only run_install.environment's write-only canonical backup path key.

    A matching read or altered value in the same module remains forbidden.
    This is a path passed to firmsecrets, never access to the secret itself.
    """
    wanted = ast.dump(ast.parse('str(root / "install/backup_passphrase.txt")', mode="eval").body)
    allowed = set()
    for function in tree.body:
        if not isinstance(function, ast.FunctionDef) or function.name != "environment":
            continue
        for statement in function.body:
            if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
                continue
            call = statement.value
            if (not isinstance(call.func, ast.Attribute) or not isinstance(call.func.value, ast.Name)
                    or call.func.value.id != "env" or call.func.attr != "update" or len(call.args) != 1
                    or call.keywords or not isinstance(call.args[0], ast.Dict)):
                continue
            for key, value in zip(call.args[0].keys, call.args[0].values):
                if (isinstance(key, ast.Constant) and key.value == "I485_BACKUP_PASSPHRASE_FILE"
                        and ast.dump(value) == wanted):
                    allowed.add(id(key))
    return allowed


def _literals(path: Path, *, canonical_installation_binding=False) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
                  and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    declarations = _installation_path_declarations(tree) if canonical_installation_binding else set()
    return [(n.lineno, n.value) for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings | declarations]


def test_no_module_reads_a_secret_any_other_way_than_by_name():
    names = {n for s in firmsecrets.SECRETS for n in s.env + ((s.file_env,) if s.file_env else ())}
    found = []
    for path in sorted(list((REPO / "src").rglob("*.py")) + list((REPO / "tools").glob("*.py"))):
        rel = path.relative_to(REPO / "src").as_posix() if "src" in path.parts and path.is_relative_to(REPO / "src") else path.name
        if rel == "firmsecrets.py":
            continue
        for line, value in _literals(path, canonical_installation_binding=path == REPO / "tools/run_install.py"):
            if value in names:
                found.append(f"{path.relative_to(REPO)}:{line} names the environment variable {value}")
            for literal, allowed in FILE_LITERALS.items():
                if value == literal and rel not in allowed and path.name not in allowed:
                    found.append(f"{path.relative_to(REPO)}:{line} names the file {literal}")
    assert not found, "read a secret through src/firmsecrets.py:\n" + "\n".join(found)


def test_the_old_ways_are_found_by_that_test(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text('"""SMTP_PASSWORD in a docstring is words"""\nimport os\nvalue = os.environ.get("SMTP_PASSWORD")\nopen("secrets.enc")\n', encoding="utf-8")
    assert [v for _l, v in _literals(bad)] == ["SMTP_PASSWORD", "secrets.enc"]


@pytest.mark.parametrize("statement", [
    'value = os.environ.get("I485_BACKUP_PASSPHRASE_FILE")',
    'value = os.environ["I485_BACKUP_PASSPHRASE_FILE"]',
    'env.update({"I485_BACKUP_PASSPHRASE_FILE": str(root / "foreign/passphrase.txt")})',
    'env.update({"I485_BACKUP_PASSPHRASE_FILE": os.environ.get("I485_BACKUP_PASSPHRASE_FILE")})',
])
def test_installation_path_declaration_does_not_exempt_reads_or_changed_bindings(tmp_path, statement):
    path = tmp_path / "run_install.py"
    canonical = 'env.update({"I485_BACKUP_PASSPHRASE_FILE": str(root / "install/backup_passphrase.txt")})'
    path.write_text("def environment(root):\n    " + canonical + "\n    " + statement + "\n", encoding="utf-8")
    values = [value for _, value in _literals(path, canonical_installation_binding=True)]
    assert "I485_BACKUP_PASSPHRASE_FILE" in values
    path.write_text("def environment(root):\n    " + canonical + "\n", encoding="utf-8")
    assert "I485_BACKUP_PASSPHRASE_FILE" not in [value for _, value in _literals(path, canonical_installation_binding=True)]
    # Identical text outside the exact installation scope/function is not exempt.
    assert "I485_BACKUP_PASSPHRASE_FILE" in [value for _, value in _literals(path)]
    path.write_text("def another_function(root):\n    " + canonical + "\n", encoding="utf-8")
    assert "I485_BACKUP_PASSPHRASE_FILE" in [value for _, value in _literals(path, canonical_installation_binding=True)]


def test_every_secret_env_variable_the_catalog_names_is_documented_where_a_firm_would_look():
    text = (REPO / "docs" / "deployment.md").read_text(encoding="utf-8")
    for s in firmsecrets.SECRETS:
        assert s.name in text, f"docs/deployment.md does not say where {s.name} goes"


def test_the_secret_scan_still_finds_a_leaked_secret_and_the_tree_is_clean(tmp_path):
    leak = tmp_path / "leak.py"
    leak.write_text("smtp_password = " + chr(34) + "a" * 24 + chr(34) + "\n", encoding="utf-8")  # a password assigned in code: the scan's own shape
    scan = REPO / "tools" / "secret_scan.py"
    assert subprocess.run([sys.executable, str(scan), str(tmp_path)], capture_output=True, text=True, timeout=120).returncode == 1
    leak.write_text("value = firmsecrets.get(" + chr(34) + "smtp.password" + chr(34) + ")\n", encoding="utf-8")
    assert subprocess.run([sys.executable, str(scan), str(tmp_path)], capture_output=True, text=True, timeout=120).returncode == 0


# -- never shown, never logged ------------------------------------------------------------------------------------------------------------------------


def test_no_secret_is_in_any_answer_of_any_get_route_as_an_attorney_nor_in_the_ledger_or_the_logs(server, app, capsys):
    from review import server as srv

    secrets_set = {s.name: made_up(s.name) for s in firmsecrets.SECRETS if not s.app}
    data_folder = app.data_root.parent
    for name, value in secrets_set.items():
        spec = firmsecrets.BY_NAME[name]
        if spec.keys or spec.name == "gdrive.service_account":
            continue
        if spec.env and spec.name in ("twilio.auth_token", "uscis.client_secret", "google.signin_secret"):
            os.environ[spec.env[0]] = value  # these three from the environment
        elif spec.store == "vault":
            firmsecrets.put(name, value, "Pat IT", data_root=data_folder)
    try:
        sam = sign_in(server, "sam@firm.example")
        values = [v for n, v in secrets_set.items() if not firmsecrets.BY_NAME[n].keys and n != "gdrive.service_account"]
        keys_text = [(data_folder / "clio" / "vault.key").read_text(encoding="utf-8").strip()]
        seen = 0
        for route, query in list(LISTING.items()) + [(r, "") for r in sorted(NEITHER - {"/auth/start", "/auth/callback"})] + [(r, "?client=case-ana" + QUERY) for r in sorted(srv.CASE_GET)]:
            status, text = call(server + route + query, sam)
            seen += 1
            for v in values + keys_text:
                assert v not in text, f"{route} showed a secret"
        assert seen > 60
        status, text = call(server + "/api/settings", sam)
        page = json.loads(text)
        assert status == 200 and {r["kind"] for r in page["secrets"]} == set(firmsecrets.KINDS)
        by = {n["name"]: n for r in page["secrets"] for n in r["names"]}
        assert by["smtp.password"] == {"name": "smtp.password", "label": "The mail server's password", "set": True, "where": "vault", "by_firm": True}
        assert by["twilio.auth_token"]["where"] == "environment" and by["uscis.client_secret"]["where"] == "environment"
        paralegal = json.loads(call(server + "/api/settings", sign_in(server, "jane@firm.example"))[1])
        assert "secrets" not in paralegal  # the attorney's page
    finally:
        for s in firmsecrets.SECRETS:
            for n in s.env:
                os.environ.pop(n, None)
    # the ledger, the access log, the rotation record and the console hold none
    held = "".join(p.read_text(encoding="utf-8", errors="replace") for p in list(data_folder.glob("events-*.jsonl")) + list(data_folder.glob("*.jsonl")) + [data_folder / "secrets_log.json"] if p.exists())
    held += "".join(p.read_text(encoding="utf-8", errors="replace") for p in app.data_root.parent.glob("*access*.jsonl"))
    out = capsys.readouterr()
    held += out.out + out.err
    for v in values + keys_text:
        assert v not in held


def test_the_cadence_is_chosen_through_the_settings_route_by_an_attorney_only_and_recorded(server, app):
    sam, jane = sign_in(server, "sam@firm.example"), sign_in(server, "jane@firm.example")
    status, _ = call(server + "/api/settings", jane, {"action": "secret_cadence", "kind": "mail", "cadence": "yearly", "reviewer": "Jane Doe"})
    assert status == 403
    status, text = call(server + "/api/settings", sam, {"action": "secret_cadence", "kind": "mail", "cadence": "yearly", "reviewer": "Sam Attorney"})
    assert status == 200
    row = next(r for r in json.loads(text)["secrets"] if r["kind"] == "mail")
    assert row["cadence"] == "yearly" and firmsecrets.read_log(app.data_root.parent)["cadence"]["mail"]["by"] == "Sam Attorney"
    status, _ = call(server + "/api/settings", sam, {"action": "secret_cadence", "kind": "mail", "cadence": "whenever", "reviewer": "Sam Attorney"})
    assert status >= 400


def test_the_vault_still_holds_the_firms_secrets_when_clio_is_disconnected(data):
    from connectors import clio

    firmsecrets.put("smtp.password", made_up("mail"), "Pat IT", data_root=data)
    clio.Vault(data, {}).update(client_secret=made_up("clio"), access_token="a", refresh_token="b", expires_at=1)
    kept = {k: v for k, v in clio.Vault(data, {}).read().items() if k not in clio.CONNECTION_KEYS}
    assert kept == {"smtp.password": made_up("mail"), "client_secret": made_up("clio")}


def test_public_scanner_has_no_embedded_private_fingerprints():
    import importlib.util
    spec = importlib.util.spec_from_file_location("fictional_secret_scanner", REPO / "tools" / "secret_scan.py")
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    assert scanner.BANNED == [] and scanner.KNOWN == {}

def test_generic_scanner_detects_only_caller_supplied_synthetic_names(tmp_path):
    import hashlib
    import importlib.util
    spec = importlib.util.spec_from_file_location("fictional_secret_scanner", REPO / "tools" / "secret_scan.py")
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    synthetic = "fictionalmarker"
    (tmp_path / "sample.txt").write_text(synthetic, encoding="utf-8")
    digest = hashlib.sha256(synthetic.encode()).hexdigest()
    hits = scanner.scan(tmp_path, banned=[(len(synthetic), digest)], known={}, pattern_list=[])
    assert len(hits) == 1 and hits[0]["seen"] == ""
    assert scanner.scan(tmp_path, banned=[], known={}, pattern_list=[]) == []
