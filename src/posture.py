"""The machine's posture: what the firm's own computer must keep true, read and never set.

The product runs on the firm's own machine (docs/deployment.md). docs/hardening.md and docs/security/security_program.md say what the firm must keep true of it, and an attorney
who signs the data statement promises it. This module reads each of five duties off the computer and says plainly what it found:

    disk       the disk that holds the firm's data is encrypted (BitLocker on Windows, dm-crypt or LUKS on Linux)
    screen     the screen locks by itself, with a password, after at most LOCK_MAX_SECONDS of no use
    backup     the newest backup is under BACKUP_MAX_DAYS old and was written to another device or a network path
    updates    the last operating system update was installed within UPDATE_MAX_DAYS
    firewall   the firewall is on (Windows: all three profiles; Linux: ufw, firewalld or nftables)

Each check records the plain result ("on", "off", "not known"), why, the side that answered (Windows, Windows reached from WSL, Linux), the command it ran and the exact output line
it read, what "on" means in one sentence, what the firm's IT person does when it is not on (per system) and the time. A check that cannot run on this machine says "not known: <why>"
and never guesses.

It changes nothing on the machine, ever. Every command is in COMMANDS, a fixed list of read-only commands (the Windows ones are PowerShell scripts written out here in full); _run is
the only place a process is started and refuses a name that is not in the list; tests/test_posture.py holds its own copy of the list and fails on a command outside it, and on a word
in one that changes something. Under WSL the Windows checks go through the Windows side as the product already does for Tesseract and Ollama (powershell.exe, which WSL can start);
the finding says which side answered.

The result is kept in data/posture.json (owner-only, 0600): written when the review app starts and once a day by the overnight run, read by Settings ("This computer") and the morning
report. docs/decisions.md says, command by command, which were run on a real system and which were written from the documentation.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import clock

ON, OFF, UNKNOWN = "on", "off", "not known"
LOCK_MAX_SECONDS = 900  # a screen that locks after 15 minutes of no use, or sooner
BACKUP_MAX_DAYS = 2  # as src/backups.py: a backup is meant to run every night
UPDATE_MAX_DAYS = 35  # Windows' monthly security update comes the second Tuesday; a month and a few days
TIMEOUT = 25  # seconds a command may take (Get-HotFix is slow on some machines: UPDATE_TIMEOUT)
UPDATE_TIMEOUT = 90
FILE = "posture.json"
NETWORK_FILESYSTEMS = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "sshfs", "fuse.sshfs", "davfs", "fuse.davfs", "ceph", "glusterfs", "afs"}

DUTIES: dict[str, tuple[str, str]] = {
    "disk": ("Disk encryption", "The disk that holds the firm's data is encrypted, so a stolen or lost computer shows nothing."),
    "screen": ("Screen lock", "The screen locks by itself after at most 15 minutes of no use and asks for the password."),
    "backup": ("Backup on another device", "The newest backup is under 2 days old and was written to another device or a network path, not the disk it was made from."),
    "updates": ("Operating system updates", "The last operating system update was installed within the last 35 days."),
    "firewall": ("Firewall", "The computer's firewall is switched on."),
}

# What the firm's IT person does when a check is not on, per system: the product never does it. The command or the setting's name.
FIX: dict[str, dict[str, str]] = {
    "disk": {"windows": "Turn on BitLocker for the drive that holds the data (Settings, Privacy and security, Device encryption, or Control Panel, BitLocker Drive Encryption; "
                        "from an administrator prompt, manage-bde -on C:), and keep the recovery key away from the machine.",
             "wsl": "Turn on BitLocker for the Windows drive that holds the data or WSL's own disk (Control Panel, BitLocker Drive Encryption; from an administrator prompt, "
                    "manage-bde -on C:), and keep the recovery key away from the machine.",
             "linux": "Put the data disk on LUKS (cryptsetup, at install time or by moving the data to an encrypted volume); lsblk -f shows crypto_LUKS under it."},
    "screen": {"windows": "Group Policy or Settings: Interactive logon: Machine inactivity limit (900 seconds or less), or the screen saver set to lock on resume (Settings, Personalization, "
                          "Lock screen, Screen saver settings: On resume, display logon screen, wait 15 minutes or less).",
               "wsl": "On the Windows side: Interactive logon: Machine inactivity limit (900 seconds or less), or Settings, Personalization, Lock screen, Screen saver settings: "
                      "On resume, display logon screen, wait 15 minutes or less.",
               "linux": "On the desktop: Settings, Privacy, Screen Lock (GNOME: automatic screen lock on, blank screen after 15 minutes or less) or System Settings, Screen Locking (KDE). "
                        "A server with no desktop has no screen to lock: lock the room."},
    "backup": {"windows": "Point tools/backup.py --out at another drive, a network share or a removable disk that is not the data drive (docs/hardening.md, Backups), and check that the "
                          "nightly task ran (Task Scheduler).",
               "wsl": "Point tools/backup.py --out at another Windows drive (/mnt/d/...) or a network share, not the disk the data is on (docs/hardening.md, Backups), and check the nightly timer.",
               "linux": "Point tools/backup.py --out at another disk or a mounted network share (/mnt/...), not the disk the data is on (docs/hardening.md, Backups), and check the nightly timer."},
    "updates": {"windows": "Settings, Windows Update: install what is waiting and restart; check Update history.",
                "wsl": "On the Windows side: Settings, Windows Update: install what is waiting and restart; also update the Linux distribution (sudo apt update && sudo apt upgrade).",
                "linux": "sudo apt update && sudo apt upgrade (Debian, Ubuntu) or sudo dnf upgrade (Fedora, RHEL), then restart if it asks; switch on automatic security updates."},
    "firewall": {"windows": "Settings, Windows Security, Firewall and network protection: switch the Domain, Private and Public profiles on (from an administrator prompt, "
                            "netsh advfirewall set allprofiles state on).",
                 "wsl": "On the Windows side: Settings, Windows Security, Firewall and network protection: switch the Domain, Private and Public profiles on.",
                 "linux": "sudo ufw enable (Ubuntu, Debian), or sudo systemctl enable --now firewalld (Fedora, RHEL), or an nftables ruleset loaded at boot "
                          "(sudo systemctl enable --now nftables)."},
}

# -- the only commands the module may run: read-only ---------------------------------------------------------------------------------------------------------------------
# A name, its arguments ({placeholders} are filled from validated values), and for a PowerShell script the script (written out here, never built from outside text). _run is the
# only place a process is started. tests/test_posture.py keeps its own list of these and fails on a command that is not in it.
PS_FLAGS = ("-NoProfile", "-NonInteractive", "-Command")
COMMANDS: dict[str, dict[str, Any]] = {
    "win_bitlocker": {"ps": "(New-Object -ComObject Shell.Application).NameSpace('{drive}:').Self.ExtendedProperty('System.Volume.BitLockerProtection')"},
    "win_lxss": {"ps": "Get-ChildItem 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Lxss' | ForEach-Object { $p = Get-ItemProperty $_.PSPath; $p.DistributionName + '|' + $p.BasePath }"},
    "win_screen": {"ps": "$m = Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System' -ErrorAction SilentlyContinue; "
                         "$d = Get-ItemProperty 'HKCU:\\Control Panel\\Desktop' -ErrorAction SilentlyContinue; "
                         "$p = Get-ItemProperty 'HKCU:\\Software\\Policies\\Microsoft\\Windows\\Control Panel\\Desktop' -ErrorAction SilentlyContinue; "
                         "'InactivityTimeoutSecs=' + $m.InactivityTimeoutSecs; 'ScreenSaveActive=' + $d.ScreenSaveActive; 'ScreenSaverIsSecure=' + $d.ScreenSaverIsSecure; "
                         "'ScreenSaveTimeOut=' + $d.ScreenSaveTimeOut; 'PolicyScreenSaveActive=' + $p.ScreenSaveActive; 'PolicyScreenSaverIsSecure=' + $p.ScreenSaverIsSecure; "
                         "'PolicyScreenSaveTimeOut=' + $p.ScreenSaveTimeOut"},
    "win_drive": {"ps": "Get-CimInstance Win32_LogicalDisk -Filter \"DeviceID='{drive}:'\" | ForEach-Object { 'DriveType=' + $_.DriveType + ' ProviderName=' + $_.ProviderName }"},
    "win_hotfix": {"ps": "Get-HotFix | Where-Object { $_.InstalledOn } | Sort-Object InstalledOn -Descending | Select-Object -First 1 | "
                         "ForEach-Object { $_.HotFixID + ' ' + $_.InstalledOn.ToString('yyyy-MM-dd') }", "timeout": UPDATE_TIMEOUT},
    "win_firewall": {"ps": "Get-NetFirewallProfile | ForEach-Object { $_.Name + '=' + $_.Enabled }"},
    "findmnt": {"argv": ["findmnt", "-n", "-o", "SOURCE,FSTYPE", "--target", "{path}"]},
    "lsblk_chain": {"argv": ["lsblk", "-s", "-n", "-o", "TYPE,NAME", "{device}"]},
    "systemctl_active": {"argv": ["systemctl", "is-active", "{unit}"]},
    "firewall_cmd_state": {"argv": ["firewall-cmd", "--state"]},
    "nft_ruleset": {"argv": ["nft", "list", "ruleset"]},
    "rpm_last": {"argv": ["rpm", "-qa", "--last"], "timeout": UPDATE_TIMEOUT},
    "gsettings_lock": {"argv": ["gsettings", "get", "org.gnome.desktop.screensaver", "lock-enabled"]},
    "gsettings_idle": {"argv": ["gsettings", "get", "org.gnome.desktop.session", "idle-delay"]},
    "kde_lock": {"argv": ["kreadconfig5", "--file", "kscreenlockerrc", "--group", "Daemon", "--key", "Autolock"]},
    "kde_timeout": {"argv": ["kreadconfig5", "--file", "kscreenlockerrc", "--group", "Daemon", "--key", "Timeout"]},
}
UNITS = ("ufw", "firewalld", "nftables")  # the only units systemctl is asked about
APT_HISTORY = "/var/log/apt/history.log"  # read, not run: the only file read besides the product's own


class Out:
    """What a command printed: its exit code, standard output and error, or why it did not run (rc 127: not installed; 124: too slow)."""

    def __init__(self, rc: int, out: str = "", err: str = ""):
        self.rc, self.out, self.err = rc, out, err

    @property
    def lines(self) -> list[str]:
        return [x.strip() for x in self.out.replace("\r", "").splitlines() if x.strip()]


class NotAllowed(Exception):
    """A command that is not on the list (tests/test_posture.py): never started."""


def _powershell(system: str) -> str:
    return "powershell" if system == "windows" else "powershell.exe"  # from WSL the Windows program is started by its .exe name


def argv_for(name: str, params: dict[str, str], system: str) -> list[str]:
    """The command as it is run, with its placeholders filled from values checked here (a drive letter, an absolute path, a /dev path, a unit from UNITS)."""
    if name not in COMMANDS:
        raise NotAllowed(name)
    spec = COMMANDS[name]
    values = dict(params)
    if "drive" in values and not re.fullmatch(r"[A-Za-z]", str(values["drive"])):
        raise NotAllowed(f"{name}: a drive is one letter")
    if "path" in values and not (str(values["path"]).startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", str(values["path"]))):
        raise NotAllowed(f"{name}: a path is absolute")
    if "device" in values and not str(values["device"]).startswith("/dev/"):
        raise NotAllowed(f"{name}: a device is under /dev")
    if "unit" in values and values["unit"] not in UNITS:
        raise NotAllowed(f"{name}: not a unit this check asks about")
    def fill(text: str) -> str:  # only the named placeholders: a PowerShell script is full of braces of its own
        for key, value in values.items():
            text = text.replace("{" + key + "}", str(value))
        return text

    if "ps" in spec:
        return [_powershell(system), *PS_FLAGS, fill(spec["ps"])]
    return [fill(part) for part in spec["argv"]]


def shown(name: str, params: dict[str, str], system: str) -> str:
    """The command in the words an IT person can paste: PowerShell scripts in quotes."""
    argv = argv_for(name, params, system)
    if "ps" in COMMANDS[name]:
        return f'{argv[0]} {" ".join(PS_FLAGS)} "{argv[-1]}"'
    return " ".join(argv)


def _run(name: str, system: str = "linux", **params: str) -> Out:
    """Runs one listed command: nothing else. A program that is not installed, a command too slow and a failure to start are an Out with a code, never an exception."""
    argv = argv_for(name, params, system)
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=COMMANDS[name].get("timeout", TIMEOUT), stdin=subprocess.DEVNULL, check=False)  # noqa: S603
    except FileNotFoundError:
        return Out(127, "", f"{argv[0]}: not installed")
    except subprocess.TimeoutExpired:
        return Out(124, "", f"{argv[0]}: took too long")
    except OSError as exc:
        return Out(126, "", f"{argv[0]}: {type(exc).__name__}")
    return Out(done.returncode, done.stdout, done.stderr)


# -- the machine --------------------------------------------------------------------------------------------------------------------------------------------------------


def system_name() -> str:
    """"windows" (Windows Python), "wsl" (Linux under Windows), "linux", or "other" (macOS and the rest: the product supports Windows, with or without WSL, and Linux)."""
    if os.name == "nt":
        return "windows"
    if sys.platform.startswith("linux"):
        try:
            microsoft = "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            microsoft = False
        return "wsl" if os.environ.get("WSL_DISTRO_NAME") or microsoft else "linux"
    return "other"


SIDES = {"windows": "Windows", "wsl": "Windows, reached from WSL", "linux": "Linux", "other": "this operating system"}


class Context:
    """What a check reads from: the system, the runner (tests give recorded outputs), the firm's data folder, the backup log and today."""

    def __init__(self, system: str, run: Callable[..., Out], data: Path, log: dict[str, Any], today: date, wsl_distro: str | None = None, linux_files=None, which=None):
        self.system, self._run, self.data, self.log, self.today = system, run, Path(data), log, today
        self.which = which or shutil.which
        self.distro = wsl_distro if wsl_distro is not None else os.environ.get("WSL_DISTRO_NAME") or ""
        self.read_text = linux_files or (lambda path: Path(path).read_text(encoding="utf-8", errors="replace"))

    def run(self, name: str, **params: str) -> tuple[Out, str]:
        return self._run(name, self.system, **params), shown(name, params, self.system)

    @property
    def windows_side(self) -> bool:
        return self.system in ("windows", "wsl")


def _found(duty: str, result: str, why: str, ctx: Context, command: str = "", line: str = "", side: str | None = None) -> dict[str, Any]:
    title, means = DUTIES[duty]
    fixes = FIX[duty]
    return {"id": duty, "duty": title, "result": result, "why": why, "means_on": means, "side": side or SIDES.get(ctx.system, ""), "command": command, "line": line,
            "at": clock.stamp("seconds"), "fix": fixes.get(ctx.system) or fixes["linux"]}


def _unknown(duty: str, why: str, ctx: Context, command: str = "", line: str = "", side: str | None = None) -> dict[str, Any]:
    return _found(duty, UNKNOWN, f"Not known: {why}", ctx, command, line, side)


def _failed(out: Out) -> str:
    """The reason a command gave nothing: its own words, or that it is not installed."""
    return (out.err.strip().splitlines() or [f"it exited with code {out.rc}"])[0][:200]


# -- where a path is: the disk it is on, as Windows or Linux names it -------------------------------------------------------------------------------------------------------


def _wsl_drive(path: str) -> str | None:
    m = re.match(r"^/mnt/([a-zA-Z])(/|$)", path.replace("\\", "/"))
    return m.group(1).upper() if m else None


def _distro_drive(ctx: Context) -> tuple[str | None, str, str]:
    """The Windows drive WSL keeps this distribution's own disk on, from the Lxss registry key: (letter or None, command shown, the line read)."""
    out, cmd = ctx.run("win_lxss")
    for line in out.lines:
        name, _, base = line.partition("|")
        if name == ctx.distro:
            m = re.search(r"([A-Za-z]):\\", base)
            return (m.group(1).upper() if m else None), cmd, line
    return None, cmd, ""


def _data_drive(ctx: Context, path: Path | str) -> tuple[str | None, str, str, str]:
    """(the drive letter holding the path on the Windows side, why not, the command shown, the line read). WSL's own disk is where Lxss says the distribution lives."""
    text = str(path)
    if ctx.system == "windows":
        m = re.match(r"^([A-Za-z]):", text)
        if m:
            return m.group(1).upper(), "", "", ""  # the drive is the path's own letter: no command, no output line
        return None, f"{text} is not on a lettered drive (a network share?)", "", ""
    letter = _wsl_drive(text)
    if letter:
        return letter, "", "", ""  # /mnt/<letter> is Windows' drive <letter>: no command, no output line
    letter, cmd, line = _distro_drive(ctx)
    if letter:
        return letter, "", cmd, line
    return None, "Windows would not say which drive holds this Linux distribution's disk", cmd, line


# -- the five checks --------------------------------------------------------------------------------------------------------------------------------------------------


def check_disk(ctx: Context) -> dict[str, Any]:
    if ctx.windows_side:
        letter, why, cmd, line = _data_drive(ctx, ctx.data)
        if letter is None:
            return _unknown("disk", why, ctx, cmd, line)
        out, cmd2 = ctx.run("win_bitlocker", drive=letter)
        read = out.lines[-1] if out.lines else ""
        both, said = "; ".join(x for x in (cmd, cmd2) if x), "; ".join(x for x in (line, read) if x)  # the lookup of the drive (WSL's own disk), then the status
        if out.rc != 0 or not out.lines:
            return _unknown("disk", f"Windows did not say whether drive {letter}: is encrypted ({_failed(out)}). It may need an administrator: manage-bde -status {letter}:", ctx, both, said)
        if not re.fullmatch(r"\d+", read):
            return _unknown("disk", f"Windows answered for drive {letter}: with something this check does not read", ctx, both, said)
        value = int(read)
        if value in (1, 3, 5):  # the values scripts that read this property treat as protected (decisions.md: written from the documentation, not run)
            return _found("disk", ON, f"BitLocker protection is on for drive {letter}:, the drive that holds the firm's data.", ctx, both, said)
        if value == 0:
            return _found("disk", OFF, f"BitLocker protection is off for drive {letter}:, the drive that holds the firm's data.", ctx, both, said)
        return _unknown("disk", f"Windows gave the value {value} for drive {letter}: (suspended or being encrypted?), which this check does not read as on or off", ctx, both, said)
    if ctx.system != "linux":
        return _unknown("disk", f"this check reads Windows and Linux, and this computer is {ctx.system}", ctx)
    where, cmd = ctx.run("findmnt", path=str(ctx.data))
    first = where.lines[0] if where.lines else ""
    if where.rc != 0 or not first:
        return _unknown("disk", f"findmnt did not say which disk holds the data folder ({_failed(where)})", ctx, cmd, first)
    source = re.sub(r"\[.*?\]", "", first.split()[0])
    fstype = first.split()[1] if len(first.split()) > 1 else ""
    if not source.startswith("/dev/"):
        return _unknown("disk", f"the data folder is on {fstype or 'a file system'} ({source}), not on a disk this check can read: a container or a network share", ctx, cmd, first)
    chain, cmd2 = ctx.run("lsblk_chain", device=source)
    if chain.rc != 0 or not chain.lines:
        return _unknown("disk", f"lsblk did not say what {source} is made of ({_failed(chain)})", ctx, cmd2, " ".join(chain.lines))
    types = [x.split()[0] for x in chain.lines]
    line = f"{first}; " + "; ".join(chain.lines)
    if "crypt" in types:
        return _found("disk", ON, f"The data folder is on {source}, which sits on an encrypted (dm-crypt or LUKS) volume.", ctx, f"{cmd}; {cmd2}", line)
    return _found("disk", OFF, f"The data folder is on {source}, which is not on an encrypted (dm-crypt or LUKS) volume.", ctx, f"{cmd}; {cmd2}", line)


def _kv(out: Out) -> dict[str, str]:
    return {k.strip(): v.strip() for k, _, v in (line.partition("=") for line in out.lines) if k.strip()}


def _desktop() -> str:
    return (os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "").strip()


def check_screen(ctx: Context) -> dict[str, Any]:
    if ctx.windows_side:
        out, cmd = ctx.run("win_screen")
        if out.rc != 0 or not out.lines:
            return _unknown("screen", f"Windows did not answer ({_failed(out)})", ctx, cmd)
        v = _kv(out)
        line = "; ".join(x for x in out.lines if not x.endswith("="))[:300]
        machine = v.get("InactivityTimeoutSecs", "")
        active, secure, timeout = (v.get("PolicyScreenSaveActive") or v.get("ScreenSaveActive", ""), v.get("PolicyScreenSaverIsSecure") or v.get("ScreenSaverIsSecure", ""),
                                   v.get("PolicyScreenSaveTimeOut") or v.get("ScreenSaveTimeOut", ""))
        if machine.isdigit() and 0 < int(machine) <= LOCK_MAX_SECONDS:
            return _found("screen", ON, f"Windows locks the screen after {int(machine) // 60} minute(s) of no use (the machine inactivity limit).", ctx, cmd, f"InactivityTimeoutSecs={machine}")
        if active == "1" and secure == "1" and timeout.isdigit() and 0 < int(timeout) <= LOCK_MAX_SECONDS:
            return _found("screen", ON, f"The screen saver of the account the apps run under locks after {int(timeout) // 60} minute(s) and asks for the password.", ctx, cmd, line)
        if not any(v.get(k) for k in v):
            return _unknown("screen", "Windows holds no screen lock setting this check reads for the account the apps run under, and no machine policy; "
                                      "a lock set by a management service (Intune, MDM) is not read", ctx, cmd, line)
        return _found("screen", OFF, "The screen does not lock by itself within 15 minutes with a password, by the machine policy or by the screen saver of the account the apps run under.",
                      ctx, cmd, line)
    if ctx.system != "linux":
        return _unknown("screen", f"this check reads Windows and Linux, and this computer is {ctx.system}", ctx)
    desktop = _desktop()
    if not desktop:
        return _unknown("screen", "this computer has no desktop session for the account the apps run under (a server), so there is no screen lock to read", ctx, "", "XDG_CURRENT_DESKTOP is empty")
    d = desktop.upper()
    if any(x in d for x in ("GNOME", "UNITY", "CINNAMON", "MATE", "BUDGIE", "PANTHEON")):
        lock, c1 = ctx.run("gsettings_lock")
        idle, c2 = ctx.run("gsettings_idle")
        if lock.rc != 0 or not lock.lines:
            return _unknown("screen", f"the desktop is {desktop}, but its settings could not be read ({_failed(lock)})", ctx, c1, " ".join(lock.lines), f"Linux ({desktop})")
        m = re.search(r"(\d+)\s*$", idle.lines[0]) if idle.lines else None
        secs = int(m.group(1)) if m else None
        line = f"lock-enabled {lock.lines[0]}; idle-delay {idle.lines[0] if idle.lines else '?'}"
        if lock.lines[0] == "true" and secs is not None and 0 < secs <= LOCK_MAX_SECONDS:
            return _found("screen", ON, f"The {desktop} desktop locks the screen after {secs // 60} minute(s) of no use.", ctx, f"{c1}; {c2}", line, f"Linux ({desktop})")
        return _found("screen", OFF, f"The {desktop} desktop does not lock the screen by itself within 15 minutes.", ctx, f"{c1}; {c2}", line, f"Linux ({desktop})")
    if "KDE" in d:
        lock, c1 = ctx.run("kde_lock")
        tmo, c2 = ctx.run("kde_timeout")
        if lock.rc != 0 or not lock.lines:
            return _unknown("screen", f"the desktop is {desktop}, but its settings could not be read ({_failed(lock)})", ctx, c1, " ".join(lock.lines), f"Linux ({desktop})")
        minutes = int(tmo.lines[0]) if tmo.lines and tmo.lines[0].isdigit() else None
        line = f"Autolock {lock.lines[0]}; Timeout {tmo.lines[0] if tmo.lines else '?'}"
        if lock.lines[0] == "true" and minutes is not None and 0 < minutes * 60 <= LOCK_MAX_SECONDS:
            return _found("screen", ON, f"The KDE desktop locks the screen after {minutes} minute(s) of no use.", ctx, f"{c1}; {c2}", line, f"Linux ({desktop})")
        return _found("screen", OFF, "The KDE desktop does not lock the screen by itself within 15 minutes.", ctx, f"{c1}; {c2}", line, f"Linux ({desktop})")
    return _unknown("screen", f"the desktop is {desktop}, and this check reads GNOME and KDE only", ctx, "", f"XDG_CURRENT_DESKTOP={desktop}", f"Linux ({desktop})")


def _where(ctx: Context, path: str) -> tuple[tuple[str, str] | None, bool, str, str]:
    """Which device a path is on, as an identity to compare: (("win", drive) | ("unc", host) | ("posix", source), is it a network path, why not known, the line read and command)."""
    text = str(path)
    if ctx.windows_side and text.startswith("\\\\"):
        return ("unc", text.split("\\")[2] if len(text.split("\\")) > 2 else text), True, "", f"{text} is a network path"
    if ctx.system == "windows" or (ctx.system == "wsl" and _wsl_drive(text)):
        letter, why, cmd, line = _data_drive(ctx, text)
        if not letter:
            return None, False, why, ""
        out, cmd = ctx.run("win_drive", drive=letter)
        line = out.lines[0] if out.lines else ""
        m = re.search(r"DriveType=(\d+)", line)
        if out.rc != 0 or not m:
            return ("win", letter), False, "", f"drive {letter}: (its type could not be read: {_failed(out)}) [{cmd}]"
        return ("win", letter), m.group(1) == "4", "", f"{line} [{cmd}]"
    if ctx.system == "wsl":  # a path inside WSL's own disk
        letter, cmd, line = _distro_drive(ctx)
        return ("win", letter or "?"), False, "", f"WSL's own disk is on drive {letter or '?'}: [{cmd}]"
    out, cmd = ctx.run("findmnt", path=text)
    first = out.lines[0] if out.lines else ""
    if out.rc != 0 or not first:
        return None, False, f"findmnt did not say which device holds {text} ({_failed(out)}); the folder may be unplugged", ""
    source = re.sub(r"\[.*?\]", "", first.split()[0])
    fstype = first.split()[1] if len(first.split()) > 1 else ""
    return ("posix", source), fstype in NETWORK_FILESYSTEMS or source.startswith("//") or ":" in source.split("/")[0], "", f"{first} [{cmd}]"


def check_backup(ctx: Context) -> dict[str, Any]:
    last = (ctx.log or {}).get("last_backup")
    cmd = "read the backup log (data/backup_log.json): last_backup"
    if not last:
        return _found("backup", OFF, "No backup has been made yet.", ctx, cmd, "no last_backup in the log", "the backup log")
    when = clock.local_date(last.get("at"))
    folder = str(last.get("folder") or "")
    if when is None or not folder:
        return _unknown("backup", "the backup log does not say when or where the last backup was written", ctx, cmd, json.dumps({k: last.get(k) for k in ("at", "file", "folder")}), "the backup log")
    days = (ctx.today - when).days
    line = f"last_backup at {last.get('at')}, folder {folder}"
    there, network, why, read = _where(ctx, folder)
    if there is None:
        return _unknown("backup", why, ctx, cmd, line, "the backup log")
    here, _, why2, read2 = _where(ctx, str(ctx.data))
    if here is None:
        return _unknown("backup", why2, ctx, cmd, line, "the backup log")
    other = network or there != here
    detail = f"{line}; folder: {read}; data folder: {read2}"
    how = "a network path" if network else "another device" if other else "the same device as the data"
    age = f"{days} day{'s' if days != 1 else ''} old ({when.month:02d}/{when.day:02d}/{when.year})"
    side = "the backup log, then " + (SIDES[ctx.system] if ctx.system != "linux" else "Linux")
    if days > BACKUP_MAX_DAYS:
        return _found("backup", OFF, f"The newest backup is {age}: a backup should be made every night.", ctx, cmd, detail, side)
    if not other:
        return _found("backup", OFF, f"The newest backup is {age} but was written to {how}: a disk that fails takes both.", ctx, cmd, detail, side)
    return _found("backup", ON, f"The newest backup is {age} and was written to {how}.", ctx, cmd, detail, side)


def _days_since(ctx: Context, when: date) -> int:
    return (ctx.today - when).days


def check_updates(ctx: Context) -> dict[str, Any]:
    if ctx.windows_side:
        out, cmd = ctx.run("win_hotfix")
        line = out.lines[-1] if out.lines else ""
        m = re.match(r"^(\S+)\s+(\d{4})-(\d{2})-(\d{2})$", line)
        if out.rc != 0 or not m:
            return _unknown("updates", f"Windows did not say when the last update was installed ({_failed(out) if out.rc != 0 else 'it listed none'})", ctx, cmd, line)
        when = date(int(m.group(2)), int(m.group(3)), int(m.group(4)))
        days = _days_since(ctx, when)
        said = f"The last Windows update ({m.group(1)}) was installed {days} day{'s' if days != 1 else ''} ago, on {when.month:02d}/{when.day:02d}/{when.year}."
        return _found("updates", ON if days <= UPDATE_MAX_DAYS else OFF, said, ctx, cmd, line)
    if ctx.system != "linux":
        return _unknown("updates", f"this check reads Windows and Linux, and this computer is {ctx.system}", ctx)
    try:
        text = ctx.read_text(APT_HISTORY)
    except OSError:
        text = ""
    ends = [x.strip() for x in text.splitlines() if x.startswith("End-Date:")]
    if ends:
        line = ends[-1]
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", line)
        if m:
            when = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            days = _days_since(ctx, when)
            said = f"The package manager (apt) last ran {days} day{'s' if days != 1 else ''} ago, on {when.month:02d}/{when.day:02d}/{when.year}."
            return _found("updates", ON if days <= UPDATE_MAX_DAYS else OFF, said, ctx, f"read {APT_HISTORY}: the last End-Date line", line, "Linux (apt's own log)")
    out, cmd = ctx.run("rpm_last")
    first = out.lines[0] if out.lines else ""
    m = re.search(r"(\d{1,2}) (\w{3}) (\d{4})", first)
    if out.rc == 0 and m:
        try:
            when = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y").date()
        except ValueError:
            when = None
        if when:
            days = _days_since(ctx, when)
            said = f"The last package installed or updated (rpm) was {days} day{'s' if days != 1 else ''} ago, on {when.month:02d}/{when.day:02d}/{when.year}."
            return _found("updates", ON if days <= UPDATE_MAX_DAYS else OFF, said, ctx, cmd, first)
    return _unknown("updates", "this computer has no apt history log and no rpm database this check reads (it reads Debian, Ubuntu and RPM-based systems)", ctx, f"read {APT_HISTORY}; {cmd}", "")


def check_firewall(ctx: Context) -> dict[str, Any]:
    if ctx.windows_side:
        out, cmd = ctx.run("win_firewall")
        profiles = _kv(out)
        line = "; ".join(f"{k}={v}" for k, v in profiles.items())
        if out.rc != 0 or not profiles:
            return _unknown("firewall", f"Windows did not answer ({_failed(out)})", ctx, cmd, line)
        off = [k for k, v in profiles.items() if v.lower() != "true"]
        if off:
            return _found("firewall", OFF, f"The Windows firewall is off for the {', '.join(off)} profile{'s' if len(off) != 1 else ''}.", ctx, cmd, line)
        return _found("firewall", ON, f"The Windows firewall is on for all {len(profiles)} profiles ({', '.join(profiles)}).", ctx, cmd, line)
    if ctx.system != "linux":
        return _unknown("firewall", f"this check reads Windows and Linux, and this computer is {ctx.system}", ctx)
    seen: list[tuple[str, str, str, str]] = []  # (program, result, command, line)
    for program, probe in (("ufw", _ufw), ("firewalld", _firewalld), ("nftables", _nftables)):
        got = probe(ctx)
        if got is not None:
            seen.append((program, *got))
    if not seen:
        return _unknown("firewall", "none of ufw, firewalld or nftables is installed here (a firewall in front of the computer is not seen from it)", ctx, "", "")
    for program, result, cmd, line in seen:
        if result == ON:
            return _found("firewall", ON, f"The {program} firewall is on.", ctx, cmd, line)
    known = [s for s in seen if s[1] == OFF]
    if known:
        program, _, cmd, line = known[0]
        return _found("firewall", OFF, f"The {program} firewall is installed and off.", ctx, cmd, line)
    program, _, cmd, line = seen[0]
    return _unknown("firewall", f"{program} is installed but could not be read here ({line})", ctx, cmd, line)


def _installed(ctx: Context, name: str, params: dict[str, str]) -> Out | None:
    out = ctx._run(name, ctx.system, **params)
    return None if out.rc == 127 else out


def _systemd(out: Out) -> str:
    """systemctl's answer as ON, OFF, or UNKNOWN (it could not talk to systemd)."""
    first = out.lines[0] if out.lines else ""
    if first == "active":
        return ON
    if first in ("inactive", "failed", "unknown", "dead"):
        return OFF
    return UNKNOWN


def _ufw(ctx: Context) -> tuple[str, str, str] | None:
    out = _installed(ctx, "systemctl_active", {"unit": "ufw"})
    if out is None:
        return None
    cmd = shown("systemctl_active", {"unit": "ufw"}, ctx.system)
    if not ctx.which("ufw"):  # systemctl answers "inactive" for a unit that is not there: ufw must be installed
        return None
    return _systemd(out), cmd, out.lines[0] if out.lines else _failed(out)


def _firewalld(ctx: Context) -> tuple[str, str, str] | None:
    out = _installed(ctx, "firewall_cmd_state", {})
    if out is None:
        return None
    cmd = shown("firewall_cmd_state", {}, ctx.system)
    first = out.lines[0] if out.lines else _failed(out)
    return (ON if first == "running" else OFF if "not running" in first else UNKNOWN), cmd, first


def _nftables(ctx: Context) -> tuple[str, str, str] | None:
    out = _installed(ctx, "nft_ruleset", {})
    if out is None:
        return None
    cmd = shown("nft_ruleset", {}, ctx.system)
    if out.rc != 0:
        return UNKNOWN, cmd, "listing the rules needs an administrator: " + _failed(out)
    hooks = [x for x in out.lines if re.search(r"\bhook (input|forward)\b", x)]
    if hooks:
        return ON, cmd, hooks[0]
    return OFF, cmd, "no chain attached to the input or forward hook" if out.lines else "the ruleset is empty"


CHECKS: dict[str, Callable[[Context], dict[str, Any]]] = {"disk": check_disk, "screen": check_screen, "backup": check_backup, "updates": check_updates, "firewall": check_firewall}


def run_all(data: Path, run: Callable[..., Out] | None = None, system: str | None = None, today: date | None = None, log: dict[str, Any] | None = None,
            wsl_distro: str | None = None, linux_files=None, which=None) -> dict[str, Any]:
    """Every check, on this computer: {"at", "system", "side", "checks": [...]}. run: how a command is run (tests give recorded outputs); log: the backup log (default: the real one)."""
    import backups

    system = system or system_name()
    ctx = Context(system, run or _run, Path(data), backups.read_log() if log is None else log, today or clock.today(), wsl_distro, linux_files, which)
    found = []
    for duty, check in CHECKS.items():
        try:
            found.append(check(ctx))
        except Exception as exc:  # noqa: BLE001 -- one check failing to run is "not known", never a guess and never the others' loss
            found.append(_unknown(duty, f"the check itself stopped ({type(exc).__name__})", ctx))
    return {"at": clock.stamp("seconds"), "system": system, "side": SIDES.get(system, ""), "checks": found}


# -- the kept result, and the words ----------------------------------------------------------------------------------------------------------------------------------------


def path_for(data: Path | None = None) -> Path:
    """data/posture.json (I485_POSTURE: a test world keeps its own)."""
    if os.environ.get("I485_POSTURE"):
        return Path(os.environ["I485_POSTURE"])
    return Path(data) / FILE if data else Path(__file__).resolve().parents[1] / "data" / FILE


def keep(result: dict[str, Any], path: Path) -> None:
    """The result in an owner-only file (0600): it says how this firm's computer is set up."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("checks"), list) else None


def enabled() -> bool:
    """The checks run at start and in the night unless switched off (I485_POSTURE_CHECKS=0: the tests, and a copy that must not read its machine)."""
    return os.environ.get("I485_POSTURE_CHECKS", "1") != "0"


def refresh(data: Path, **kw: Any) -> dict[str, Any]:
    """Runs every check and keeps the result: what the review app does at its start and the overnight run does once a day. Reads only; writes data/posture.json."""
    result = run_all(data, **kw)
    keep(result, path_for(data))
    return result


def line(check: dict[str, Any]) -> str:
    """One check in plain words: "Disk encryption: off. The data folder is on /dev/vda, which is not on an encrypted volume." """
    why = str(check.get("why") or "")
    return f"{check['duty']}: {check['result']}. " + (why[len("Not known: "):].capitalize() if why.startswith("Not known: ") else why)


def view(result: dict[str, Any] | None) -> dict[str, Any]:
    """What Settings ("This computer") draws: one line per check, red when off and grey when not known, with the fold "How this was checked"."""
    if not result:
        return {"checked": None, "system": None, "lines": [], "note": "Not checked yet. The computer is read when the review app starts and every night."}
    at = clock.parse(result.get("at"))
    checked = clock.local(at.isoformat()) if at else None
    return {"checked": {"date": clock.us_date(result["at"]), "time": checked.strftime("%H:%M") if checked else ""}, "system": result.get("side"),
            "lines": [{"id": c["id"], "line": line(c), "result": c["result"], "means_on": c["means_on"], "side": c["side"], "command": c["command"], "output": c["line"],
                       "at": c["at"], "fix": c["fix"]} for c in result["checks"]],
            "note": "The product only reads these. It never changes a setting of the computer: your IT person does, and each line says how."}


def report_lines(result: dict[str, Any] | None) -> list[str]:
    """The morning report's lines: the checks that are off or not known (nothing when all are on)."""
    if not result:
        return ["This computer: not checked yet."]
    return [f"This computer: {line(c)}" for c in result["checks"] if c["result"] != ON]
