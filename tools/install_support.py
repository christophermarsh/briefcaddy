"""What install.sh, install.ps1, update.sh and update.ps1 do that is not one shell line (so it is the same on both and has tests).

    python tools/install_support.py requirements --platform windows|linux --out FILE
    python tools/install_support.py prepare --firm-name ... [--mode on_premises|hosted] ...
    python tools/install_support.py units --python PYTHON --out FOLDER
    python tools/install_support.py update NEW_FOLDER --python PYTHON [--skip-pip]
    python tools/install_support.py changes --from VERSION [--notes docs/releases.md]

It is written for "the man they call": plain words, no stack traces. Nothing here touches data/ except `prepare`, which only
creates what is missing (folders, the first settings, the accounts file, a setup code), and never over what is there.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

MODES = ("on_premises", "hosted")
PORT = 8485
INSTALLER = "Installer"  # what the first settings say made them (src/getting_started.py: it does not count as the attorney confirming them)
# the development and test tools the lock carries; a firm's installation needs none of them (and no browser: Playwright is not run)
DEV_ONLY = {"pytest", "pytest-xdist", "pytest-playwright", "pytest-base-url", "playwright", "pyee", "execnet", "iniconfig", "pluggy", "ruff"}
# Linux-only wheels in the lock (the GPU libraries torch pulled in): there are none for Windows
LINUX_ONLY = re.compile(r"^(nvidia-|cuda-|triton\b)")
# what an update replaces: the product. Everything else in the install folder (data, clients, backups, install, the virtual
# environment, deployment.json) is the firm's or the machine's, and is never touched.
PRODUCT = ("src", "schemas", "tools", "docs", "requirements.txt", "requirements.lock", "requirements-dev.txt", "deployment.example.json", "README.md",
           "install.sh", "install.ps1", "update.sh", "update.ps1", ".gitattributes")
BACKUP_AT, OVERNIGHT_AT, OVERNIGHT_UNTIL = "07:00", "19:00", "06:30"


class Problem(Exception):
    """Something the person running the installer should read."""


def say(*lines: str) -> None:
    for line in lines:
        print(line)


# -- the packages ------------------------------------------------------------------------------------------------------

def filtered_lock(lock: Path, platform: str, *, development: bool = False) -> str:
    """Filter the installation lock, dropping test tools and Windows-incompatible GPU wheels.

    Customer Windows installs add tzdata for zoneinfo. Development keeps test tools
    and supplies a pinned platform supplement. Install with pip --no-deps -r FILE.
    """
    out = []
    for line in lock.read_text(encoding="utf-8").splitlines():
        name = re.split(r"[=<>~ ;#]", line.strip(), maxsplit=1)[0].lower()
        if not name or line.strip().startswith("#"):
            continue
        if (not development and name in DEV_ONLY) or (platform == "windows" and LINUX_ONLY.match(name)):
            continue
        out.append(line.strip())
    # Development supplies its explicitly pinned platform supplement separately.
    if platform == "windows" and not development and not any(x.lower().startswith("tzdata") for x in out):
        out.append("tzdata")
    return "\n".join(out) + "\n"


# -- prepare: folders, deployment.json, first settings, the accounts file ----------------------------------------------

def _restrict(path: Path) -> str | None:
    """Only the account that runs the app (and, on Windows, the system and administrators) may open this folder or file."""
    try:
        if sys.platform == "win32":
            who = os.environ.get("USERNAME") or "CURRENT_USER"
            grants = [f"{who}:(OI)(CI)F" if path.is_dir() else f"{who}:F", "SYSTEM:(OI)(CI)F" if path.is_dir() else "SYSTEM:F",
                      "Administrators:(OI)(CI)F" if path.is_dir() else "Administrators:F"]
            subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", *grants], check=True, capture_output=True)
        else:
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"Could not restrict who can open {path} ({type(exc).__name__}): set it by hand so that only the account that runs the app can."
    return None


def prepare(root: Path, a: dict[str, Any]) -> list[str]:
    """Creates what is missing under root and returns what to tell the person, in order. a: firm_name, mode, provider_name,
    provider_email, provider_phone, time_zone, backup_folder, port, network (bool), hostname, encrypt (bool)."""
    root = Path(root).absolute()
    # Reject links/reparse ancestors before resolving or writing owned folders.
    import stat
    for path in (root, *root.parents, root / "data", root / "clients", root / "install", root / "deployment.json"):
        if path.exists() or path.is_symlink():
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise Problem("The installation contains a link/reparse path; choose a plain owned folder.")
    root = root.resolve()
    # Cron/systemd treat percent/newline as syntax even inside ordinary quoting.
    if any(c in str(root) for c in ("%", "\n", "\r")):
        raise Problem("The installation folder may not contain percent signs or line breaks.")
    existing_path = root / "install/install.json"
    existing = json.loads(existing_path.read_text(encoding="utf-8")) if existing_path.exists() else None
    if existing is not None:
        a = dict(a)
        a.update({"port": existing["port"], "portal_port": existing.get("portal_port", 8600),
                  "instance": existing.get("instance"), "encrypt": existing["encrypted"],
                  "backup_folder": existing["backup_folder"], "with_portal": existing.get("portal_enabled", False),
                  "network": existing.get("host") != "127.0.0.1", "hostname": existing.get("hostname", "")})
    lines: list[str] = []
    firm = " ".join(str(a.get("firm_name") or "").split())
    mode = a.get("mode") or "on_premises"
    if mode not in MODES:
        raise Problem("The installation must be 'on_premises' (the firm runs it) or 'hosted' (we run it for the firm).")
    if not firm:
        raise Problem("The firm's name is needed (it is printed on the sign-in screen's first settings).")
    provider = {"name": str(a.get("provider_name") or "").strip() or "the software provider", "email": str(a.get("provider_email") or "").strip(),
                "phone": str(a.get("provider_phone") or "").strip(), "url": ""}
    if mode == "hosted" and (provider["name"] == "the software provider" or not provider["email"]):
        raise Problem("A hosted installation needs the provider's name and a security contact email: the firm is told to ask them.")
    zone = str(a.get("time_zone") or "America/New_York").strip()
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(zone)
    except Exception:  # noqa: BLE001 -- unknown, or no zone database on this machine (Windows without tzdata)
        if zone != "America/New_York":
            raise Problem(f"'{zone}' is not a time zone name this computer knows (for example America/New_York, America/Chicago, America/Los_Angeles).") from None
    port = int(a.get("port") or PORT)
    portal_port = int(a.get("portal_port") or 8600)
    if not 1024 <= port <= 65535 or not 1024 <= portal_port <= 65535 or port == portal_port:
        raise Problem("Review and portal ports must be different numbers from 1024 to 65535.")
    instance = str(a.get("instance") or ("firm-" + hashlib.sha256(str(root).encode()).hexdigest()[:10]))
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", instance):
        raise Problem("The installation name must use lowercase letters, digits and dashes (start with a letter).")
    data = root / "data"
    backup_folder = Path(a.get("backup_folder") or root / "backups").expanduser().resolve()
    if any(c in str(backup_folder) for c in ("%", "\n", "\r")):
        raise Problem("The backup folder may not contain percent signs or line breaks.")

    # folders
    made = []
    for folder in (data, data / "clients", data / "portal", data / "inbox", root / "clients", root / "install", backup_folder):
        if not folder.exists():
            folder.mkdir(parents=True)
            made.append(folder)
    for folder in (data, root / "clients", root / "install", backup_folder):
        warn = _restrict(folder)
        if warn:
            lines.append(warn)
    lines.append("Folders: " + (f"created {len(made)} (data, clients, backups)." if made else "all there already, left as they were.")
                 + " Only the account that runs the app can open them.")

    # deployment.json: the installation's identity; never rewritten if it is there
    dep = root / "deployment.json"
    if dep.exists():
        lines.append("deployment.json: already there, kept as it is.")
    else:
        dep.write_text(json.dumps({"mode": mode, "provider": provider}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        _restrict(dep)
        lines.append(f"deployment.json: written ({'hosted by ' + provider['name'] if mode == 'hosted' else 'on the firm\'s own machine'}).")

    # the first settings: the firm's name and time zone, as the installer; an attorney confirms them on Getting started
    settings_path = data / "settings.json"  # where the app reads it from unless I485_SETTINGS says otherwise (never set by the installer)
    saved = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    if (saved.get("firm") or {}).get("updated_by"):
        lines.append("Firm name and time zone: already set in Settings, kept.")
    else:
        saved["firm"] = {"values": {**((saved.get("firm") or {}).get("values") or {}), "firm.business_name": firm, "office.time_zone": zone},
                         "updated_by": INSTALLER, "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "history": []}
        settings_path.write_text(json.dumps(saved, indent=1, ensure_ascii=False), encoding="utf-8")
        lines.append(f"Settings: firm name \"{firm}\" and time zone {zone} recorded; an attorney confirms them on the Getting started page.")

    # the accounts file, empty, and a one-time code for the first attorney
    from review.auth import Accounts

    users = Accounts(data / "review_users.json")
    host = "0.0.0.0" if a.get("network") else "127.0.0.1"
    hostname = str(a.get("hostname") or "").strip() or (socket.getfqdn() if a.get("network") else "")
    where = f"http://{hostname if a.get('network') else '127.0.0.1'}:{port}/"
    if users.needs_setup():
        code = users.new_setup_code()
        _restrict(users.path)
        if a.get("network"):  # reached over the network (and usually through a proxy): only the code opens the setup page
            lines += ["", "Sign-in address: " + where,
                      f"First visit: open this address, which carries the one-time setup code: {where}?setup={code}",
                      "It asks you to set up the first attorney: their name, work email and a password. Because the app serves the office network, "
                      "the page is not offered to 'this computer' without the code."]
        else:
            lines += ["", "Sign-in address: " + where,
                      f"First visit: open this address, which carries the one-time setup code: {where}?setup={code}",
                      "It asks you to set up the first attorney: their name, work email and a password. On this computer the page also asks for the code "
                      "when the address does not carry it. Lost it? Run: python src/review/users.py setup-code (a new code; the old one stops working)."]
        lines.append("That code works once and for 14 days, and nothing else can create the first account. After the first attorney exists the page never comes back. "
                     "Running the installer again makes a new code, and the one printed before then stops working.")
    else:
        lines += ["", "Sign-in address: " + where, "Staff accounts already exist, so no setup code was made."]

    # the backup passphrase
    note = root / "install" / "backup_passphrase.txt"
    if a.get("encrypt", True):
        if note.exists():
            lines.append("Backups are encrypted; the passphrase file from the earlier install is kept.")
        else:
            note.write_text(secrets.token_urlsafe(24) + "\n", encoding="utf-8")
            _restrict(note)
            lines += ["", "BACKUP PASSPHRASE (shown once): " + note.read_text(encoding="utf-8").strip(),
                      "Copy it to the firm's password manager or safe now. Without it no backup can be opened. It is also kept in the install folder, "
                      "for the nightly backup to use."]
    config = {"instance": instance, "portal_enabled": bool(a.get("with_portal")), "portal_port": portal_port,
              "mode": mode, "port": port, "host": host, "hostname": hostname, "backup_folder": str(backup_folder), "encrypted": bool(a.get("encrypt", True)),
              "overnight_at": OVERNIGHT_AT, "backup_at": BACKUP_AT}
    config_path = root / "install/install.json"
    if config_path.exists():
        lines.append("Service settings: the existing installation's ports, scheduler name and backup policy are kept.")
    else:
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    runtime = root / "install/runtime.json"
    if not runtime.exists():
        runtime.write_text(json.dumps({"PORTAL_BASE_URL": f"http://127.0.0.1:{portal_port}"}, indent=2) + "\n", encoding="utf-8")
        _restrict(runtime)
    return lines


# -- the scheduled jobs on Linux (the files install.sh registers, or leaves for IT to register by hand) -----------------------

def _commands(root: Path, python: str, config: dict[str, Any]) -> dict[str, list[str]]:
    """The commands the installer schedules, as argument lists: the review app, the job worker (the reading of scans and photos, src/jobs.py), the overnight run, the backup."""
    services = ["review", "worker", "overnight", "backup"] + (["portal"] if config.get("portal_enabled") else [])
    return {s: [python, str(root / "tools/run_install.py"), "--root", str(root), "--service", s] for s in services}


def unit_files(root: Path, python: str, config: dict[str, Any]) -> dict[str, str]:
    """{file name: text}: systemd user units (the review app, the job worker, the nightly run, the nightly backup, with their timers) and a crontab
    for machines without systemd. Every cron line ends with the mark # i485-pipeline, which is how removal finds them."""
    import shlex

    root = Path(root)
    cmds = _commands(root, python, config)
    q = lambda v: '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'  # noqa: E731 -- systemd's quoting
    line = lambda cmd: " ".join(q(c) for c in cmd)  # noqa: E731

    def service(what: str, cmd: list[str], restart: bool = False) -> str:
        return (f"[Unit]\nDescription={what}\n\n[Service]\n" + ("Restart=on-failure\nRestartSec=5\n" if restart else "")
                + f"WorkingDirectory={root}\nExecStart={line(cmd)}\n" + ("\n[Install]\nWantedBy=default.target\n" if restart else ""))

    def timer(what: str, at: str) -> str:
        return f"[Unit]\nDescription={what}\n\n[Timer]\nOnCalendar=*-*-* {at}:00\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n"

    prefix = "i485" + ("-" + config["instance"] if config.get("instance") else "")
    marker = "# i485-pipeline" + (":" + config["instance"] if config.get("instance") else "")
    def cron(when: str, cmd: list[str], log: str) -> str:
        hour, minute = when.split(":")
        return f"{int(minute)} {int(hour)} * * * cd {shlex.quote(str(root))} && {shlex.join(cmd)} >> {shlex.quote(str(root / 'install' / log))} 2>&1 {marker}"

    units = {
        "i485-review.service": service("I-485 review app", cmds["review"], restart=True),
        "i485-jobs.service": service("I-485 job worker (reads the scans and photos the review app and the portal write down)", cmds["worker"], restart=True),
        "i485-nightly.service": service("I-485 overnight run", cmds["overnight"]),
        "i485-nightly.timer": timer("I-485 overnight run, every evening", OVERNIGHT_AT),
        "i485-backup.service": service("I-485 nightly backup", cmds["backup"]),
        "i485-backup.timer": timer("I-485 backup, every morning", BACKUP_AT),
        "crontab.txt": "\n".join([f"@reboot cd {shlex.quote(str(root))} && {shlex.join(cmds['review'])} >> {shlex.quote(str(root / 'install/review.log'))} 2>&1 {marker}",
                                  f"@reboot cd {shlex.quote(str(root))} && {shlex.join(cmds['worker'])} >> {shlex.quote(str(root / 'install/jobs.log'))} 2>&1 {marker}",
                                  cron(OVERNIGHT_AT, cmds["overnight"], "overnight.log"), cron(BACKUP_AT, cmds["backup"], "backup.log")]) + "\n",
    }
    if "portal" in cmds:
        units["i485-portal.service"] = service("Client portal (local listener; HTTPS proxy required for clients)", cmds["portal"], restart=True)
        units["crontab.txt"] += f"@reboot cd {shlex.quote(str(root))} && {shlex.join(cmds['portal'])} >> {shlex.quote(str(root / 'install/portal.log'))} 2>&1 {marker}\n"
    return {name.replace("i485-", prefix + "-", 1): text for name, text in units.items()}


def merge_cron(root: Path, existing: str) -> str:
    """Replace only this exact marker, after checking its recorded working folder."""
    import shlex

    root = root.resolve()
    marker = json.loads((root / "install/schedule.json").read_text(encoding="utf-8"))["marker"]
    owned = "cd " + shlex.quote(str(root)) + " && "
    kept = []
    for line in existing.splitlines():
        if line.rstrip().endswith(" " + marker):
            if owned not in line:
                raise Problem("This cron marker belongs to another installation. Preserve its jobs and choose a different installation name.")
        else:
            kept.append(line)
    additions = (root / "install/crontab.txt").read_text(encoding="utf-8").splitlines()
    return "\n".join(kept + additions) + "\n"


# -- update ------------------------------------------------------------------------------------------------------------

def _version(root: Path) -> str:
    text = (root / "src" / "version.py").read_text(encoding="utf-8") if (root / "src" / "version.py").exists() else ""
    m = re.search(r'VERSION\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else "unknown"


def _vkey(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def changes(notes: Path, old: str, new: str | None = None) -> list[str]:
    """The lines of docs/releases.md for every version after `old` (and up to `new`): a heading with a version number starts a version."""
    if not notes.exists():
        return []
    out: list[str] = []
    take = False
    for line in notes.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^#{1,4}\s*.*?(\d{4}\.\d+\.\d+)", line)
        if m:
            v = m.group(1)
            take = _vkey(v) > _vkey(old) and (new is None or _vkey(v) <= _vkey(new))
        if take:
            out.append(line.rstrip())
    return out


def _tree_digest(folder: Path) -> dict[str, tuple[int, int]]:
    return {p.relative_to(folder).as_posix(): (p.stat().st_size, int(p.stat().st_mtime)) for p in sorted(folder.rglob("*")) if p.is_file()}


def swap_in(root: Path, new: Path) -> Path:
    """Replaces the product items in root with the ones in new. The old ones are moved (not deleted) into root/.update-old, which
    is returned: rollback() puts them back. Nothing outside PRODUCT is looked at."""
    old = root / ".update-old"
    if old.exists():
        shutil.rmtree(old)
    old.mkdir()
    for name in PRODUCT:
        src = new / name
        if not src.exists():
            continue
        if (root / name).exists():
            shutil.move(str(root / name), str(old / name))
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, root / name, **({"ignore": shutil.ignore_patterns("__pycache__", "*.pyc")} if src.is_dir() else {}))
    return old


def rollback(root: Path) -> None:
    old = root / ".update-old"
    for name in PRODUCT:
        if (old / name).exists():
            if (root / name).exists():
                shutil.rmtree(root / name) if (root / name).is_dir() else (root / name).unlink()
            shutil.move(str(old / name), str(root / name))
    shutil.rmtree(old, ignore_errors=True)


def _unpack(archive: Path) -> tuple[Path, Path]:
    """A .zip of the new version unpacked into a temporary folder: (the folder that holds src/, the temporary folder to remove)."""
    import tempfile
    import zipfile

    tmp = Path(tempfile.mkdtemp(prefix="i485-update-"))
    try:
        with zipfile.ZipFile(archive) as zf:
            for name in zf.namelist():
                parts = name.split("/")
                if name.startswith("/") or ".." in parts or ":" in parts[0]:
                    raise Problem("That zip has a path that leaves its folder; it is not a copy of the product. Nothing was changed.")
            zf.extractall(tmp)
    except zipfile.BadZipFile:
        shutil.rmtree(tmp, ignore_errors=True)
        raise Problem(f"{archive} is not a zip file that can be opened. Nothing was changed.") from None
    except Problem:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    for candidate in [tmp, *sorted(p for p in tmp.iterdir() if p.is_dir())]:
        if (candidate / "src" / "version.py").exists():
            return candidate, tmp
    return tmp, tmp


def update(root: Path, new: Path, python: str, skip_pip: bool = False, run=subprocess.run) -> list[str]:
    """Back up, replace the product, install the packages, run the smoke check; on any failure put the old product back.
    new is the folder the new version was unpacked into, or its .zip. Returns the lines to print. Raises Problem with words for
    the person, after the rollback."""
    raise Problem("Unsigned folder/zip updates are no longer accepted. Use update.ps1 or update.sh with the signed release, trusted public key and services stopped (docs/onboarding.md).")


def _packages_back(root: Path, python: str, old_lock: str | None, req: Path, pip_started: bool, run) -> str:
    """After a rollback: the packages in the environment go back to the old version's pins (what pip changed for the new lock is undone).
    Packages only the new version had are left installed, which does no harm. Returns the sentence that says how it went."""
    if not pip_started or old_lock is None:
        return "The packages were not changed."
    req.write_text(old_lock, encoding="utf-8")
    done = run([python, "-m", "pip", "install", "--no-deps", "--quiet", "-r", str(req)], capture_output=True, text=True, cwd=root)
    if done.returncode == 0:
        return "The packages were put back to the old version's versions."
    return ("The packages could NOT be put back to the old version's versions, so the environment may hold the new version's. "
            f"To put them back, run: {python} -m pip install --no-deps -r {req}")


def _update(root: Path, new: Path, python: str, skip_pip: bool, run) -> list[str]:
    root, new = Path(root).resolve(), Path(new).resolve()
    if not (new / "src" / "version.py").exists() or not (new / "tools" / "install_support.py").exists():
        raise Problem(f"{new} is not a copy of the product (no src and tools folders): nothing was changed.")
    if new == root:
        raise Problem("The new version is the folder being updated: give the folder you unpacked the new version into. Nothing was changed.")
    before, after = _version(root), _version(new)
    config = json.loads((root / "install" / "install.json").read_text(encoding="utf-8")) if (root / "install" / "install.json").exists() else {}
    lines = [f"Updating from version {before} to {after}."]

    # 1. a backup first; a backup that fails stops the update before anything is touched
    folder = config.get("backup_folder") or str(root / "backups")
    cmd = [python, str(root / "tools" / "backup.py"), "--out", folder, "--data-folder", str(root / "data"), "--documents", str(root / "clients")]
    phrase = root / "install" / "backup_passphrase.txt"
    if config.get("encrypted") and phrase.exists():
        cmd += ["--passphrase-file", str(phrase)]
    done = run(cmd, capture_output=True, text=True, cwd=root)
    if done.returncode != 0:
        raise Problem("The backup before the update did not work, so nothing was changed.\n" + (done.stderr or done.stdout).strip())
    lines.append("Backed up first: " + next((x.strip() for x in done.stdout.splitlines() if x.startswith("Backup written")), "done") + ".")

    # 2. the data stays exactly as it is: remember what it looked like, to say so afterwards
    kept = {n: _tree_digest(root / n) for n in ("data", "clients") if (root / n).is_dir()}
    platform = "windows" if sys.platform == "win32" else "linux"
    old_lock = filtered_lock(root / "requirements.lock", platform) if (root / "requirements.lock").exists() else None  # what the environment holds now
    req = root / "install" / "requirements.installed.txt"
    pip_started = False
    try:
        swap_in(root, new)
        if not skip_pip:
            lock = filtered_lock(root / "requirements.lock", platform)
            req.write_text(lock, encoding="utf-8")
            pip_started = True
            done = run([python, "-m", "pip", "install", "--no-deps", "--quiet", "-r", str(req)], capture_output=True, text=True, cwd=root)
            if done.returncode != 0:
                raise Problem("The new version's packages could not be installed:\n" + (done.stderr or done.stdout).strip()[-800:])
        done = run([python, str(root / "tools" / "smoke_check.py"), "--install", str(root)], capture_output=True, text=True, cwd=root)
        if done.returncode != 0:
            raise Problem("The new version failed its self-check:\n" + (done.stdout + done.stderr).strip()[-1200:])
    except Problem as exc:
        rollback(root)
        raise Problem(f"{exc}\n\nThe old version was put back, and the data was not touched (a backup was made first). "
                      + _packages_back(root, python, old_lock, req, pip_started, run)) from None
    except Exception as exc:  # noqa: BLE001 -- any surprise puts the old product back too
        rollback(root)
        raise Problem(f"{type(exc).__name__}: {exc}\n\nThe old version was put back, and the data was not touched. "
                      + _packages_back(root, python, old_lock, req, pip_started, run)) from None
    if any(_tree_digest(root / n) != d for n, d in kept.items()):
        raise Problem("The data folder changed while the update ran (the review app or the overnight run was writing). The update itself is in place; "
                      "check that nothing was lost by restoring the backup just made into a spare folder.")
    shutil.rmtree(root / ".update-old", ignore_errors=True)
    lines += ["Product files replaced; data, clients, deployment.json and the backups were not touched.", "The self-check passed.", ""]
    notes = changes(root / "docs" / "releases.md", before, after)
    lines += (["What changed:"] + notes) if notes else [f"Version {after} is installed. This version's release notes list nothing newer than {before}."]
    lines += ["", "Restart the review app now (and the client portal, if it runs here) so it runs the new version."]
    return lines


# -- command line ------------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("requirements")
    r.add_argument("--platform", choices=("windows", "linux"), required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--lock", type=Path, default=REPO / "requirements.lock")
    p = sub.add_parser("prepare")
    for flag in ("firm-name", "mode", "provider-name", "provider-email", "provider-phone", "time-zone", "backup-folder", "hostname"):
        p.add_argument(f"--{flag}")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--portal-port", type=int, default=8600)
    p.add_argument("--with-portal", action="store_true")
    p.add_argument("--instance")
    p.add_argument("--network", action="store_true", help="staff open it from other computers (it then listens on the office network)")
    p.add_argument("--no-backup-encryption", action="store_true")
    p.add_argument("--root", type=Path, default=REPO)
    u = sub.add_parser("units")
    u.add_argument("--python", required=True)
    u.add_argument("--out", type=Path, required=True)
    u.add_argument("--root", type=Path, default=REPO)
    cm = sub.add_parser("commands")
    cm.add_argument("--python", required=True)
    cm.add_argument("--root", type=Path, default=REPO)
    cr = sub.add_parser("cron")
    cr.add_argument("--root", type=Path, required=True)
    up = sub.add_parser("update")
    up.add_argument("new", type=Path)
    up.add_argument("--python", required=True)
    up.add_argument("--skip-pip", action="store_true")
    up.add_argument("--root", type=Path, default=REPO)
    c = sub.add_parser("changes")
    c.add_argument("--from", dest="old", required=True)
    c.add_argument("--notes", type=Path, default=REPO / "docs" / "releases.md")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "requirements":
            args.out.write_text(filtered_lock(args.lock, args.platform), encoding="utf-8")
        elif args.cmd == "prepare":
            say(*prepare(args.root, {"firm_name": args.firm_name, "mode": args.mode, "provider_name": args.provider_name, "provider_email": args.provider_email,
                                      "provider_phone": args.provider_phone, "time_zone": args.time_zone, "backup_folder": args.backup_folder, "port": args.port,
                                      "network": args.network, "hostname": args.hostname, "encrypt": not args.no_backup_encryption,
                                      "portal_port": args.portal_port, "with_portal": args.with_portal, "instance": args.instance}))
        elif args.cmd == "units":
            config = json.loads((args.root / "install" / "install.json").read_text(encoding="utf-8"))
            args.out.mkdir(parents=True, exist_ok=True)
            for name, text in unit_files(args.root, args.python, config).items():
                (args.out / name).write_text(text, encoding="utf-8")
            (args.out / "schedule.json").write_text(json.dumps({"units": [n for n in unit_files(args.root, args.python, config) if n.endswith((".service", ".timer"))],
                                                              "marker": "# i485-pipeline" + (":" + config["instance"] if config.get("instance") else "")}), encoding="utf-8")
        elif args.cmd == "commands":  # JSON {review, worker, overnight, backup}: each an argument list (install.ps1 registers them in Task Scheduler)
            config = json.loads((args.root / "install" / "install.json").read_text(encoding="utf-8"))
            print(json.dumps(_commands(Path(args.root), args.python, config)))
        elif args.cmd == "cron":
            sys.stdout.write(merge_cron(args.root, sys.stdin.read()))
        elif args.cmd == "update":
            say(*update(args.root, args.new, args.python, args.skip_pip))
        elif args.cmd == "changes":
            say(*(changes(args.notes, args.old) or ["No release notes newer than that version."]))
    except Problem as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
