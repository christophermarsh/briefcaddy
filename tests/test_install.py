"""Install, update and the first-run helpers (install.sh, update.sh, install.ps1, update.ps1, tools/install_support.py,
tools/smoke_check.py), run in temporary copies of the product. Nothing here is registered with systemd, cron or Task Scheduler:
the scripts run with --dry-run or --prepare-only, and the update scripts only ever touch the temporary copy."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import schema_path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import install_support as helper  # noqa: E402

BASH = shutil.which("bash")
PRODUCT_FILES = ("install.sh", "install.ps1", "update.sh", "update.ps1", "requirements.txt", "requirements.lock", "deployment.example.json", ".gitattributes")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


@pytest.fixture(scope="session")
def template(tmp_path_factory):
    """A copy of the product as it would be shipped (no tests, no .git, only the one PDF template the self-check needs)."""
    root = tmp_path_factory.mktemp("product") / "p"
    root.mkdir()
    for name in ("src", "tools", "docs"):
        shutil.copytree(REPO / name, root / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "demo_video"))
    for p in schema_path.all_files():
        if p.name == "template.pdf" and p != schema_path.path("template", "i485"):
            continue  # the other forms' blanks are large: the self-check needs only the I-485's
        dest = schema_path.schemas_in(root) / p.relative_to(schema_path.ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
    for name in PRODUCT_FILES:
        if (REPO / name).exists():
            shutil.copy2(REPO / name, root / name)
    return root


def _copy(template: Path, dest: Path, version: str | None = None) -> Path:
    shutil.copytree(template, dest, symlinks=True)
    if version:
        v = dest / "src" / "version.py"
        v.write_text(re.sub(r'VERSION = "[^"]*"', f'VERSION = "{version}"', v.read_text(encoding="utf-8")), encoding="utf-8")
    return dest


def _env(tmp_path: Path, **extra) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("I485_", "PORTAL_")) and k not in ("PYTHON",)}
    env |= {"PYTHON": sys.executable, "HOME": str(tmp_path / "home")}
    (tmp_path / "home").mkdir(exist_ok=True)
    return env | extra


def _run(args: list[str], cwd: Path, env: dict[str, str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def _tree(folder: Path) -> dict[str, tuple[str, int]]:
    return {p.relative_to(folder).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), int(p.stat().st_mtime)) for p in sorted(folder.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


ANSWERS = ["--yes", "--firm-name", "Exemplo & Associates LLP", "--provider-name", "Exemplo Support Co", "--provider-email", "security@exemplo.example",
           "--provider-phone", "555-0100", "--time-zone", "America/Chicago"]


# -- the packages -------------------------------------------------------------------------------------------------------

def test_the_lock_is_filtered_for_a_firm_without_changing_the_pins():
    lock = (REPO / "requirements.lock").read_text(encoding="utf-8")
    linux = helper.filtered_lock(REPO / "requirements.lock", "linux")
    windows = helper.filtered_lock(REPO / "requirements.lock", "windows")
    names = lambda text: {l.split("==")[0].lower() for l in text.splitlines()}  # noqa: E731
    assert not names(linux) & helper.DEV_ONLY and not names(windows) & helper.DEV_ONLY  # no test tools, no browser driver
    assert "torch" in names(linux) and any(n.startswith("nvidia-") for n in names(linux)) and "triton" in names(linux)  # Linux: what was tested
    assert not any(n.startswith(("nvidia-", "cuda-")) or n == "triton" for n in names(windows)) and "tzdata" in names(windows)
    pinned = {l.strip() for l in lock.splitlines() if l.strip() and not l.startswith("#")}
    assert {l for l in linux.splitlines()} <= pinned and {l for l in windows.splitlines() if l != "tzdata"} <= pinned  # every pin is the lock's own
    assert all("==" in l for l in linux.splitlines())


# -- prepare ----------------------------------------------------------------------------------------------------------

def _answers(**kw):
    return {"firm_name": "Exemplo & Associates LLP", "mode": "on_premises", "provider_name": "Exemplo Support Co", "provider_email": "security@exemplo.example",
            "provider_phone": "555-0100", "time_zone": "America/Chicago", "port": 8485, "network": False, "encrypt": True} | kw


def test_prepare_makes_the_folders_the_deployment_the_first_settings_and_a_setup_code(tmp_path):
    out = helper.prepare(tmp_path, _answers(backup_folder=str(tmp_path / "usb" / "backups")))
    text = "\n".join(out)
    for folder in ("data/clients", "data/portal", "clients", "install", "usb/backups"):
        assert (tmp_path / folder).is_dir(), folder
    if os.name == "posix":
        assert (tmp_path / "data").stat().st_mode & 0o077 == 0 and (tmp_path / "usb" / "backups").stat().st_mode & 0o077 == 0
    dep = json.loads((tmp_path / "deployment.json").read_text(encoding="utf-8"))
    assert dep == {"mode": "on_premises", "provider": {"name": "Exemplo Support Co", "email": "security@exemplo.example", "phone": "555-0100", "url": ""}}
    saved = json.loads((tmp_path / "data" / "settings.json").read_text(encoding="utf-8"))["firm"]
    assert saved["values"] == {"firm.business_name": "Exemplo & Associates LLP", "office.time_zone": "America/Chicago"} and saved["updated_by"] == "Installer"
    sys.path.insert(0, str(REPO / "src"))
    from review.auth import Accounts

    users = Accounts(tmp_path / "data" / "review_users.json")
    code = next(line.split("?setup=")[1] for line in out if "?setup=" in line)
    assert users.needs_setup() and users.setup_code_ok(code)
    assert "Sign-in address: http://127.0.0.1:8485/" in text and "First visit: open this address, which carries the one-time setup code" in text
    assert "works once and for 14 days" in text
    phrase = (tmp_path / "install" / "backup_passphrase.txt").read_text(encoding="utf-8").strip()
    assert len(phrase) >= 24 and f"BACKUP PASSPHRASE (shown once): {phrase}" in text
    config = json.loads((tmp_path / "install" / "install.json").read_text(encoding="utf-8"))
    assert config["backup_folder"] == str((tmp_path / "usb" / "backups").resolve()) and config["encrypted"] and config["port"] == 8485


def test_running_prepare_again_keeps_everything_there(tmp_path):
    helper.prepare(tmp_path, _answers())
    phrase = (tmp_path / "install" / "backup_passphrase.txt").read_text(encoding="utf-8")
    (tmp_path / "deployment.json").write_text('{"mode": "hosted", "provider": {"name": "Kept"}}\n', encoding="utf-8")
    settings = json.loads((tmp_path / "data" / "settings.json").read_text(encoding="utf-8"))
    settings["firm"]["updated_by"] = "Sam Exemplo"
    settings["firm"]["values"]["firm.business_name"] = "Changed by the attorney"
    (tmp_path / "data" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    sys.path.insert(0, str(REPO / "src"))
    from review.auth import Accounts

    users = Accounts(tmp_path / "data" / "review_users.json")
    users.create_first("sam@firm.example", "Sam Exemplo", "a long enough passphrase", users.new_setup_code())
    out = "\n".join(helper.prepare(tmp_path, _answers(firm_name="Another Name", mode="on_premises")))
    assert (tmp_path / "deployment.json").read_text(encoding="utf-8") == '{"mode": "hosted", "provider": {"name": "Kept"}}\n' and "deployment.json: already there" in out
    assert json.loads((tmp_path / "data" / "settings.json").read_text(encoding="utf-8"))["firm"]["values"]["firm.business_name"] == "Changed by the attorney"
    assert (tmp_path / "install" / "backup_passphrase.txt").read_text(encoding="utf-8") == phrase and "BACKUP PASSPHRASE" not in out
    assert "?setup=" not in out and "no setup code was made" in out


def test_a_second_run_before_the_first_attorney_makes_a_new_code_and_says_so(tmp_path):
    sys.path.insert(0, str(REPO / "src"))
    from review.auth import Accounts

    first = helper.prepare(tmp_path, _answers())
    second = helper.prepare(tmp_path, _answers())
    code = lambda out: next(line.split("?setup=")[1] for line in out if "?setup=" in line)  # noqa: E731
    users = Accounts(tmp_path / "data" / "review_users.json")
    assert code(first) != code(second) and not users.setup_code_ok(code(first)) and users.setup_code_ok(code(second))
    assert "Running the installer again makes a new code, and the one printed before then stops working." in "\n".join(first)


def test_prepare_refuses_answers_that_cannot_work_in_plain_words(tmp_path):
    for bad, words in ((_answers(firm_name="  "), "firm's name"), (_answers(mode="cloud"), "on_premises"), (_answers(mode="hosted", provider_email=""), "security contact"),
                       (_answers(mode="hosted", provider_name=""), "provider's name"), (_answers(time_zone="Mars/Olympus"), "time zone")):
        with pytest.raises(helper.Problem, match=words):
            helper.prepare(tmp_path, bad)
    assert not (tmp_path / "deployment.json").exists()  # nothing was written for any of them


def test_the_network_choice_changes_the_address_and_how_the_app_listens(tmp_path):
    out = "\n".join(helper.prepare(tmp_path, _answers(network=True, hostname="review.exemplo.lan", port=9000)))
    assert "Sign-in address: http://review.exemplo.lan:9000/" in out
    assert "http://review.exemplo.lan:9000/?setup=" in out and "ON THIS COMPUTER" not in out  # over the network only the code opens the setup page
    config = json.loads((tmp_path / "install" / "install.json").read_text(encoding="utf-8"))
    cmds = helper._commands(tmp_path, "/venv/python", config)
    assert cmds["review"][-4:] == ["--root", str(tmp_path), "--service", "review"]
    import run_install
    actual = run_install.command(tmp_path, "/venv/python", "review")
    assert actual[-4:] == ["--host", "0.0.0.0", "--hostname", "review.exemplo.lan"] and "9000" in actual


def test_the_scheduled_jobs_say_what_they_run_when_and_how_to_find_them_again(tmp_path):
    helper.prepare(tmp_path / "with space", _answers())
    root = tmp_path / "with space"
    config = json.loads((root / "install" / "install.json").read_text(encoding="utf-8"))
    files = helper.unit_files(root, "/opt/py 3.12/bin/python", config)
    prefix = "i485-" + config["instance"]
    assert set(files) == {prefix + suffix for suffix in ("-review.service", "-jobs.service", "-nightly.service", "-nightly.timer", "-backup.service", "-backup.timer")} | {"crontab.txt"}
    review, nightly, backup = [files[prefix + "-" + name + ".service"] for name in ("review", "nightly", "backup")]
    worker = files[prefix + "-jobs.service"]
    assert "run_install.py" in worker and '"--service" "worker"' in worker and "Restart=on-failure" in worker and "WantedBy=default.target" in worker and "OnCalendar" not in worker
    assert 'ExecStart="/opt/py 3.12/bin/python" "' in review and "Restart=on-failure" in review and f"WorkingDirectory={root}\n" in review  # systemd takes this path as it is: quotes would make it "not absolute"
    assert '"--service" "overnight"' in nightly and "Restart" not in nightly
    assert '"--service" "backup"' in backup
    assert "OnCalendar=*-*-* 19:00:00" in files[prefix + "-nightly.timer"] and "OnCalendar=*-*-* 07:00:00" in files[prefix + "-backup.timer"]
    assert "Persistent=true" in files[prefix + "-backup.timer"]
    lines = files["crontab.txt"].splitlines()
    assert len(lines) == 4 and all(l.endswith("# i485-pipeline:" + config["instance"]) for l in lines)
    assert lines[0].startswith("@reboot ") and lines[1].startswith("@reboot ") and "--service worker" in lines[1] and lines[2].startswith("0 19 * * * ") and lines[3].startswith("0 7 * * * ")
    assert "'" + str(root) + "'" in lines[2]  # the folder with a space is quoted for the shell
    assert phrase_not_in(files, root)


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="needs systemd-analyze")
def test_systemd_itself_accepts_the_unit_files(tmp_path):
    """systemd-analyze verify only reads the files (nothing is started or registered). It caught a quoted WorkingDirectory once."""
    root = tmp_path / "with space"
    helper.prepare(root, _answers())
    config = json.loads((root / "install" / "install.json").read_text(encoding="utf-8"))
    files = helper.unit_files(root, sys.executable, config)
    for name, text in files.items():
        if name != "crontab.txt":
            (tmp_path / name).write_text(text, encoding="utf-8")
    done = subprocess.run(["systemd-analyze", "verify", *[str(tmp_path / n) for n in files if n.endswith(("-nightly.service", "-nightly.timer", "-backup.service", "-backup.timer"))]],
                          capture_output=True, text=True)
    assert "bad unit file" not in done.stdout + done.stderr and "fatal" not in done.stdout + done.stderr, done.stdout + done.stderr


def phrase_not_in(files: dict[str, str], root: Path) -> bool:
    phrase = (root / "install" / "backup_passphrase.txt").read_text(encoding="utf-8").strip()
    return not any(phrase in text for text in files.values())


# -- install.sh ---------------------------------------------------------------------------------------------------------

def test_a_dry_run_says_what_it_would_do_and_changes_nothing(template, tmp_path):
    root = _copy(template, tmp_path / "install")
    before = _tree(root)
    done = _run([BASH, "install.sh", "--dry-run", *ANSWERS], root, _env(tmp_path))
    assert done.returncode == 0, done.stderr
    for line in ("Dry run: separate firm folders", "pinned Python 3.12 environment", "review port 8485", "No machine changes or downloads performed"):
        assert line in done.stdout, line
    assert _tree(root) == before and not (root / "data").exists() and not (root / ".venv").exists() and not (tmp_path / "home" / ".config").exists()


def test_install_prepare_only_makes_everything_but_the_environment_and_registers_nothing(template, tmp_path):
    root = _copy(template, tmp_path / "install")
    done = _run([BASH, "install.sh", "--prepare-only", *ANSWERS], root, _env(tmp_path))
    assert done.returncode == 0, done.stderr
    out = done.stdout
    assert "Nothing was scheduled" in out and "Sign-in address: http://127.0.0.1:8485/" in out and "BACKUP PASSPHRASE (shown once):" in out
    assert "Self-check: not run (prepare only)" in out
    config = json.loads((root / "install/install.json").read_text())
    prefix = "install/i485-" + config["instance"]
    for path in ("data/clients", "data/portal", "clients", "backups", "deployment.json", "data/settings.json", "data/review_users.json", "install/install.json",
                 prefix + "-review.service", prefix + "-nightly.timer", prefix + "-backup.timer", "install/crontab.txt"):
        assert (root / path).exists(), path
    assert not (root / ".venv").exists()
    assert not (tmp_path / "home" / ".config" / "systemd").exists()  # nothing was put where systemd looks
    assert json.loads((root / "deployment.json").read_text(encoding="utf-8"))["provider"]["name"] == "Exemplo Support Co"
    assert 'ExecStart="' + str(root / ".venv" / "bin" / "python") + '"' in (root / (prefix + "-review.service")).read_text(encoding="utf-8")
    again = _run([BASH, "install.sh", "--prepare-only", "--scheduler", "systemd", *ANSWERS], root, _env(tmp_path))  # prepare-only wins over a scheduler
    assert again.returncode == 0 and "Nothing was scheduled" in again.stdout and not (tmp_path / "home" / ".config" / "systemd").exists()


def test_install_asks_for_the_firm_name_and_refuses_a_wrong_option(template, tmp_path):
    root = _copy(template, tmp_path / "install")
    done = _run([BASH, "install.sh", "--yes", "--prepare-only"], root, _env(tmp_path))
    assert done.returncode == 2 and "firm's name is needed" in done.stderr and not (root / "data").exists()
    done = _run([BASH, "install.sh", "--bogus"], root, _env(tmp_path))
    assert done.returncode == 2 and "do not know the option --bogus" in done.stderr
    done = _run([BASH, "install.sh", "--yes", "--prepare-only", "--firm-name", "X", "--mode", "hosted"], root, _env(tmp_path))
    assert done.returncode == 1 and "security contact" in done.stderr and not (root / "deployment.json").exists()


def test_the_scripts_are_plain_text_the_shell_and_powershell_can_run():
    for name in ("install.sh", "update.sh"):
        raw = (REPO / name).read_bytes()
        assert b"\r" not in raw and raw.startswith(b"#!/usr/bin/env bash")
        assert subprocess.run([BASH, "-n", name], cwd=REPO, capture_output=True).returncode == 0, name
    for name in ("install.ps1", "update.ps1"):
        raw = (REPO / name).read_bytes()
        assert raw.isascii(), name  # PowerShell 5.1 reads a file with no byte-order mark as ANSI: only plain ASCII is safe


# -- update -----------------------------------------------------------------------------------------------------------

def _firm(root: Path) -> dict[str, str]:
    """The firm's side of an installation: data, documents, deployment.json, backups and install, with made-up files."""
    (root / "data" / "clients" / "case-ana").mkdir(parents=True)
    (root / "data" / "clients" / "case-ana" / "meta.json").write_text('{"name": "Ana Clara Exemplo Souza"}', encoding="utf-8")
    (root / "data" / "settings.json").write_text('{"firm": {"values": {"firm.business_name": "Exemplo"}, "updated_by": "Sam Exemplo"}}', encoding="utf-8")
    (root / "data" / "review_users.json").write_text('{"users": {}, "sessions": {}}', encoding="utf-8")
    (root / "clients" / "case-ana").mkdir(parents=True)
    (root / "clients" / "case-ana" / "scan.pdf").write_bytes(b"%PDF made up")
    (root / "deployment.json").write_text('{"mode": "on_premises", "provider": {"name": "Exemplo Support Co"}}\n', encoding="utf-8")
    (root / "backups").mkdir()
    (root / "install").mkdir(exist_ok=True)
    (root / "install" / "install.json").write_text(json.dumps({"backup_folder": str(root / "backups"), "encrypted": False, "port": 8485}), encoding="utf-8")
    (root / "src" / "old_only.py").write_text("# only in the old version\n", encoding="utf-8")
    return {}


def _added(root: Path) -> Path:
    """A schema file that only a newer release holds (an update must bring it, and a rollback must take it away)."""
    return schema_path.path("law", "added_in_new", schema_path.schemas_in(root))


def _new_version(template: Path, dest: Path, version: str, notes: str | None = None) -> Path:
    new = _copy(template, dest, version)
    _added(new).write_text("{}", encoding="utf-8")
    (new / "docs" / "releases.md").write_text(notes or (f"# Release notes\n\n## {version} (10/09/2026)\n- A change a firm would notice.\n\n"
                                                        "## 2026.10.3 (10/02/2026)\n- Something already installed.\n"), encoding="utf-8")
    return new


def test_unsigned_update_helpers_and_wrappers_fail_before_mutation(template, tmp_path):
    root = _copy(template, tmp_path / "install", "2026.10.3")
    _firm(root)
    new = _new_version(template, tmp_path / "new", "2026.10.9")
    before = _tree(root)
    with pytest.raises(helper.Problem, match="Unsigned"):
        helper.update(root, new, sys.executable, skip_pip=True)
    done = _run([sys.executable, "tools/install_support.py", "update", str(new), "--python", sys.executable], root, _env(tmp_path))
    assert done.returncode == 1 and "Unsigned" in done.stderr
    done = _run([BASH, "update.sh", str(new), "--skip-pip"], root, _env(tmp_path))
    assert done.returncode == 2 and "signed archive" in done.stderr
    assert _tree(root) == before


def test_signed_upgrade_requires_services_stopped(tmp_path):
    import release_support as release
    with pytest.raises(release.Problem, match="Stop review"):
        release.upgrade(tmp_path, tmp_path / "release.zip", tmp_path / "public.pem", sys.executable, False, True)
    assert not list(tmp_path.iterdir())


def test_recovery_requires_services_stopped_and_owned_generation(tmp_path):
    import release_support as release
    root = tmp_path / "install"
    root.mkdir()
    foreign = tmp_path / "other"
    foreign.mkdir()
    with pytest.raises(release.Problem, match="Stop all"):
        release.recovery(root, foreign, None, False)
    with pytest.raises(release.Problem, match="retained update generation"):
        release.recovery(root, foreign, None, True)
    assert not list(root.iterdir())

def test_the_release_notes_shown_are_the_versions_after_the_installed_one(tmp_path):
    notes = tmp_path / "releases.md"
    notes.write_text("# Release notes\n\n## 2026.10.4 (10/09/2026)\n- four\n\n## 2026.10.3\n- three\n\n## 2026.9.20\n- old\n", encoding="utf-8")
    assert helper.changes(notes, "2026.10.3") == ["## 2026.10.4 (10/09/2026)", "- four", ""]
    assert helper.changes(notes, "2026.9.1")[0] == "## 2026.10.4 (10/09/2026)" and "- old" in helper.changes(notes, "2026.9.1") and "- three" in helper.changes(notes, "2026.9.1")
    assert helper.changes(notes, "2026.10.4") == [] and helper.changes(tmp_path / "missing.md", "1.0") == []


# -- the self-check -----------------------------------------------------------------------------------------------------

def test_the_self_check_passes_on_a_good_copy_and_names_what_is_wrong_on_a_broken_one(template, tmp_path):
    root = _copy(template, tmp_path / "install")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    done = _run([sys.executable, "tools/smoke_check.py", "--install", str(root)], root, _env(tmp_path, TMPDIR=str(scratch)))
    assert done.returncode == 0 and done.stdout.startswith("OK: ") and not (root / "data").exists()  # it never reads or writes a data folder
    assert list(scratch.iterdir()) == []  # and leaves no scratch folder of its own behind
    (schema_path.path("law", "fees", schema_path.schemas_in(root))).write_text("{not json", encoding="utf-8")
    (root / "src" / "getting_started.py").write_text("raise RuntimeError('broken')\n", encoding="utf-8")
    done = _run([sys.executable, "tools/smoke_check.py", "--install", str(root)], root, _env(tmp_path))
    assert done.returncode == 1 and "getting_started does not load" in done.stdout and "RuntimeError" in done.stdout
    (root / "src" / "getting_started.py").write_text((REPO / "src" / "getting_started.py").read_text(encoding="utf-8"), encoding="utf-8")
    done = _run([sys.executable, "tools/smoke_check.py", "--install", str(root)], root, _env(tmp_path))
    assert done.returncode == 1 and "schemas/law/fees.json is not readable" in done.stdout



@pytest.mark.parametrize("failure", [None, "backup", "package-lock", "corrupt-record"])
def test_signed_upgrade_retains_code_and_stages_verified_data(template, tmp_path, monkeypatch, failure):
    """Synthetic encrypted upgrade/recovery drill; no services or packages installed."""
    import release_support as release
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    for name in list(os.environ):
        if name.upper().startswith(release.PREFIXES):
            monkeypatch.delenv(name)
    root = _copy(template, tmp_path / "installed")
    (root / "data").mkdir()
    (root / "data" / "synthetic.txt").write_text("Synthetic confidential original")
    (root / "clients").mkdir()
    (root / "clients" / "original.pdf").write_bytes(b"%PDF synthetic original")
    (root / "install").mkdir()
    (root / "install/install.json").write_text(json.dumps({"encrypted":True}))
    (root / "install/backup_passphrase.txt").write_text("Synthetic test passphrase only\n")
    (root / "deployment.json").write_text('{}')
    new = _copy(template, tmp_path / "new")
    (new / "src/version.py").write_text('VERSION = "2026.10.11"\nRELEASED = "2026-10-04"\n')
    (new / "docs/releases.md").write_text('## 2026.10.11 (2026-10-04)\n')
    private = Ed25519PrivateKey.generate()
    secret, public = tmp_path / "private.pem", tmp_path / "public.pem"
    secret.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    archive = tmp_path / "release.zip"
    if failure == "package-lock":
        (new / "requirements.lock").write_text((new / "requirements.lock").read_text() + "synthetic-new-package==1.0\n")
    release.build(new, archive, secret)
    if failure is None:
        # Use the existing test runtime in separate processes, without installing
        # services or claiming machine startup/browser acceptance.
        import second_firm
        from run_install import environment
        drill = tmp_path / "two-firms"
        second_firm.plan(archive, public, drill)
        firms = [drill / "firm-a", drill / "firm-b"]
        for firm in firms:
            info = second_firm.fixture(firm)
            helper.prepare(firm, {"firm_name":info["name"], "port":info["port"], "portal_port":info["portal_port"],
                                  "with_portal":True, "encrypt":True})
            result = _run([sys.executable, str(firm / "tools/second_firm.py"), "_seed", "--root", str(firm)], firm, environment(firm))
            assert result.returncode == 0, result.stderr
        for firm in firms:
            result = _run([sys.executable, str(firm / "tools/second_firm.py"), "_artifacts", "--root", str(firm)], firm, environment(firm))
            assert result.returncode == 0, result.stderr
        for firm, other in (firms, firms[::-1]):
            result = _run([sys.executable, str(firm / "tools/second_firm.py"), "_probe", "--root", str(firm), "--other", str(other)], firm, environment(firm))
            assert result.returncode == 0, result.stderr
            report = json.loads(result.stdout)
            assert report["api_probes_passed"], report["checks"]
    before = (root / "clients/original.pdf").read_bytes()
    if failure is not None:
        if failure == "backup":
            def refuse_backup(*args):
                raise release.Problem("Synthetic backup failure")
            monkeypatch.setattr(release, "backup_verified", refuse_backup)
        elif failure == "corrupt-record":
            (root / "data/broken.json").write_text("{broken")
        old_version = release.version(root)[0]
        with pytest.raises(release.Problem):
            release.upgrade(root, archive, public, sys.executable, True, True)
        assert release.version(root)[0] == old_version
        assert (root / "data/synthetic.txt").read_text() == "Synthetic confidential original"
        assert (root / "clients/original.pdf").read_bytes() == before
        assert not (root / "install/active_environment.json").exists()
        return
    result = release.upgrade(root, archive, public, sys.executable, True, True)
    assert result["backup_verified"] and not result["startup_verified"]
    assert release.version(root)[0] == "2026.10.11"
    assert (root / "clients/original.pdf").read_bytes() == before
    generation = Path(result["generation"])
    (root / "data/synthetic.txt").write_text("Work created after update")
    rollback = release.recovery(root, generation, None, True)
    assert rollback["code_rolled_back"] and not rollback["data_restored"]
    assert release.version(root)[0] == release.version(template)[0]
    assert (root / "data/synthetic.txt").read_text() == "Work created after update"
    staged = tmp_path / "recovered"
    report = release.recovery(root, generation, staged, True)
    assert report["verified"] and not report["live_data_replaced"]
    assert (staged / "data/synthetic.txt").read_text() == "Synthetic confidential original"
    assert (root / "data/synthetic.txt").read_text() == "Work created after update"
