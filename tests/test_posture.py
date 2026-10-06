"""The machine's posture (src/posture.py): the disk encrypted, the screen locked, a recent backup on another device, the system updated, the firewall on. Read, never set.

Each check is tested against recorded outputs. WHERE EACH OUTPUT CAME FROM, in each fixture's name:

  REAL_*     run on a real system while this test was written: the cloud machine of this session (Ubuntu 24.04.4 LTS, Linux 6.18, a virtual machine with systemd not running, run as root),
             10/04/2026. Its disk is not encrypted, its firewall ruleset is empty, it has no desktop: those are the "off" and "not known" cases, as the machine really answered.
  DOC_LINUX  a Linux output written from the documentation (the man pages of lsblk, findmnt, systemctl, firewall-cmd and nft, and apt's history log format), NOT run: the cases the real
             machine could not give (an encrypted disk, a running firewall, a GNOME desktop). Marked in docs/decisions.md.
  DOC_WIN    a Windows output written from the documentation of the command and from published scripts that read the same property (BitLocker's Shell property, the screen saver registry
             values, Get-HotFix, Get-NetFirewallProfile, Win32_LogicalDisk, the Lxss registry key), NOT run on Windows: no Windows system was available. Marked in docs/decisions.md.

The rest: a check that cannot run says "not known" and why; the module runs only the commands on its own list (this file keeps a copy and fails on one outside it); the kept file, Settings'
lines and the morning report agree. Everyone here is made up."""

from __future__ import annotations

import ast
import os
import re
import stat
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

import backups
import clock
import posture
from posture import OFF, ON, UNKNOWN, Out
from posture_fixtures import (
    TODAY, REAL_LINUX_FINDMNT, REAL_LINUX_LSBLK_PLAIN,
    REAL_LINUX_SYSTEMD_DOWN, REAL_LINUX_NFT_EMPTY, REAL_LINUX_GSETTINGS_MISSING,
    REAL_APT_HISTORY, DOC_LINUX_FINDMNT_LUKS, DOC_LINUX_LSBLK_LUKS,
    DOC_LINUX_LSBLK_LVM_ON_LUKS, DOC_LINUX_FINDMNT_OVERLAY, DOC_LINUX_FINDMNT_BTRFS,
    DOC_LINUX_FINDMNT_USB, DOC_LINUX_FINDMNT_NFS, DOC_LINUX_NFT_RULES,
    DOC_LINUX_NFT_DENIED, DOC_RPM_LAST, DOC_WIN_BITLOCKER_ON,
    DOC_WIN_BITLOCKER_OFF, DOC_WIN_BITLOCKER_ODD, DOC_WIN_BITLOCKER_NOTHING,
    DOC_WIN_LXSS, DOC_WIN_SCREEN_MACHINE, DOC_WIN_SCREEN_SAVER,
    DOC_WIN_SCREEN_OFF, DOC_WIN_SCREEN_LONG, DOC_WIN_SCREEN_NOTHING,
    DOC_WIN_DRIVE_FIXED, DOC_WIN_DRIVE_NETWORK, DOC_WIN_HOTFIX_RECENT,
    DOC_WIN_HOTFIX_OLD, DOC_WIN_HOTFIX_NONE, DOC_WIN_FIREWALL_ON,
    DOC_WIN_FIREWALL_PUBLIC_OFF, NOT_INSTALLED, table,
)


def ctx(system="linux", data="/home/user/firm/data", log=None, today=TODAY, distro="Ubuntu", which=None, files=None, **answers):
    run = table(**answers)
    return posture.Context(system, run, Path(data), log or {}, today, distro, files or (lambda p: (_ for _ in ()).throw(OSError(p))), which or (lambda x: None)), run


# -- the disk ---------------------------------------------------------------------------------------------------------------------------------------------------------


def test_linux_disk_not_encrypted_is_off_as_this_real_machine_answered():
    c, run = ctx(findmnt=REAL_LINUX_FINDMNT, lsblk_chain=REAL_LINUX_LSBLK_PLAIN)
    got = posture.check_disk(c)
    assert got["result"] == OFF and got["side"] == "Linux" and got["line"] == "/dev/vda ext4; disk vda"
    assert got["command"].startswith("findmnt -n -o SOURCE,FSTYPE --target /home/user/firm/data; lsblk -s -n -o TYPE,NAME /dev/vda")
    assert "not on an encrypted" in got["why"] and "Putting the data" not in got["fix"] and "LUKS" in got["fix"] and "encrypted" in got["means_on"]


def test_linux_disk_on_luks_or_lvm_on_luks_is_on_and_a_btrfs_subvolume_is_read_from_its_device():
    for findmnt, chain in ((DOC_LINUX_FINDMNT_LUKS, DOC_LINUX_LSBLK_LUKS), (Out(0, "/dev/mapper/vg-root ext4\n"), DOC_LINUX_LSBLK_LVM_ON_LUKS), (DOC_LINUX_FINDMNT_BTRFS, DOC_LINUX_LSBLK_LUKS)):
        c, run = ctx(findmnt=findmnt, lsblk_chain=chain)
        assert posture.check_disk(c)["result"] == ON
    assert ("lsblk_chain", {"device": "/dev/mapper/cryptroot"}) in run.calls  # [/@home] is not part of the device


def test_linux_disk_on_a_container_or_a_command_that_failed_is_not_known_with_the_reason():
    for answers, words in (({"findmnt": DOC_LINUX_FINDMNT_OVERLAY}, "container or a network share"), ({"findmnt": NOT_INSTALLED}, "findmnt did not say"),
                           ({"findmnt": REAL_LINUX_FINDMNT, "lsblk_chain": Out(1, "", "lsblk: /dev/vda: not a block device")}, "lsblk did not say")):
        got = posture.check_disk(ctx(**answers)[0])
        assert got["result"] == UNKNOWN and got["why"].startswith("Not known: ") and words in got["why"]


def test_windows_disk_reads_bitlocker_for_the_drive_the_data_is_on():
    c, run = ctx("windows", data="D:\\firm\\data", win_bitlocker=DOC_WIN_BITLOCKER_ON)
    got = posture.check_disk(c)
    assert got["result"] == ON and got["side"] == "Windows" and got["line"] == "1" and "drive D:" in got["why"] and ("win_bitlocker", {"drive": "D"}) in run.calls
    assert got["command"].startswith('powershell -NoProfile -NonInteractive -Command "(New-Object -ComObject Shell.Application).NameSpace(\'D:\')')
    assert posture.check_disk(ctx("windows", data="D:\\firm\\data", win_bitlocker=DOC_WIN_BITLOCKER_OFF)[0])["result"] == OFF
    odd = posture.check_disk(ctx("windows", data="C:\\firm\\data", win_bitlocker=DOC_WIN_BITLOCKER_ODD)[0])
    assert odd["result"] == UNKNOWN and "value 2" in odd["why"]  # a value it does not read as on or off is not a guess
    nothing = posture.check_disk(ctx("windows", data="C:\\firm\\data", win_bitlocker=DOC_WIN_BITLOCKER_NOTHING)[0])
    assert nothing["result"] == UNKNOWN and "manage-bde -status C:" in nothing["why"]
    assert posture.check_disk(ctx("windows", data="\\\\nas\\share\\data")[0])["result"] == UNKNOWN  # a network share has no drive here


def test_wsl_disk_goes_through_the_windows_side_by_the_mount_or_by_where_the_distribution_lives():
    got = posture.check_disk(ctx("wsl", data="/mnt/d/firm/data", win_bitlocker=DOC_WIN_BITLOCKER_ON)[0])
    assert got["result"] == ON and got["side"] == "Windows, reached from WSL" and "drive D:" in got["why"]
    c, run = ctx("wsl", data="/home/atendente/firm/data", win_lxss=DOC_WIN_LXSS, win_bitlocker=DOC_WIN_BITLOCKER_OFF)  # WSL's own disk: the Lxss registry key says where
    got = posture.check_disk(c)
    assert got["result"] == OFF and "drive C:" in got["why"] and ("win_bitlocker", {"drive": "C"}) in run.calls and "Lxss" in got["command"] and "Shell.Application" in got["command"]
    assert got["line"].startswith("Ubuntu|") and got["line"].endswith("; 0")  # the line the registry gave for this distribution, then BitLocker's value
    lost = posture.check_disk(ctx("wsl", data="/home/atendente/firm/data", distro="Fedora", win_lxss=DOC_WIN_LXSS)[0])
    assert lost["result"] == UNKNOWN and "which drive holds this Linux distribution" in lost["why"]
    assert posture.check_disk(ctx("wsl", data="/mnt/c/firm/data")[0])["result"] == UNKNOWN  # powershell.exe could not be reached: not known, never a guess
    assert posture.argv_for("win_bitlocker", {"drive": "C"}, "wsl")[0] == "powershell.exe" and posture.argv_for("win_bitlocker", {"drive": "C"}, "windows")[0] == "powershell"


# -- the screen lock ----------------------------------------------------------------------------------------------------------------------------------------------------


def test_the_screen_check_says_there_is_no_desktop_on_a_server_and_names_a_desktop_it_cannot_read(monkeypatch):
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    monkeypatch.delenv("DESKTOP_SESSION", raising=False)
    got = posture.check_screen(ctx(gsettings_lock=REAL_LINUX_GSETTINGS_MISSING)[0])
    assert got["result"] == UNKNOWN and "no desktop session" in got["why"] and "a server" in got["why"] and got["command"] == ""
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "XFCE")
    got = posture.check_screen(ctx()[0])
    assert got["result"] == UNKNOWN and "XFCE" in got["why"] and "GNOME and KDE only" in got["why"] and got["side"] == "Linux (XFCE)"


def test_the_screen_check_reads_gnome_and_kde(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
    on = posture.check_screen(ctx(gsettings_lock=Out(0, "true\n"), gsettings_idle=Out(0, "uint32 300\n"))[0])
    assert on["result"] == ON and "5 minute(s)" in on["why"] and on["line"] == "lock-enabled true; idle-delay uint32 300"
    assert posture.check_screen(ctx(gsettings_lock=Out(0, "false\n"), gsettings_idle=Out(0, "uint32 300\n"))[0])["result"] == OFF
    assert posture.check_screen(ctx(gsettings_lock=Out(0, "true\n"), gsettings_idle=Out(0, "uint32 3600\n"))[0])["result"] == OFF  # an hour is too long
    assert posture.check_screen(ctx(gsettings_lock=Out(0, "true\n"), gsettings_idle=Out(0, "uint32 0\n"))[0])["result"] == OFF  # never idle
    assert posture.check_screen(ctx(gsettings_lock=REAL_LINUX_GSETTINGS_MISSING)[0])["result"] == UNKNOWN
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert posture.check_screen(ctx(kde_lock=Out(0, "true\n"), kde_timeout=Out(0, "10\n"))[0])["result"] == ON
    assert posture.check_screen(ctx(kde_lock=Out(0, "false\n"), kde_timeout=Out(0, "10\n"))[0])["result"] == OFF
    assert posture.check_screen(ctx(kde_lock=Out(0, "true\n"), kde_timeout=Out(0, "30\n"))[0])["result"] == OFF


def test_the_windows_screen_check_reads_the_machine_policy_then_the_screen_saver():
    for answer, result in ((DOC_WIN_SCREEN_MACHINE, ON), (DOC_WIN_SCREEN_SAVER, ON), (DOC_WIN_SCREEN_OFF, OFF), (DOC_WIN_SCREEN_LONG, OFF), (DOC_WIN_SCREEN_NOTHING, UNKNOWN)):
        got = posture.check_screen(ctx("windows", win_screen=answer)[0])
        assert got["result"] == result, (answer.out[:40], got)
    nothing = posture.check_screen(ctx("windows", win_screen=DOC_WIN_SCREEN_NOTHING)[0])
    assert "management service" in nothing["why"]  # a lock the registry does not show is not said to be off
    assert posture.check_screen(ctx("wsl", win_screen=DOC_WIN_SCREEN_MACHINE)[0])["side"] == "Windows, reached from WSL"
    assert posture.check_screen(ctx("windows", win_screen=Out(1, "", "powershell: not allowed by the policy"))[0])["result"] == UNKNOWN


# -- the backup ------------------------------------------------------------------------------------------------------------------------------------------------------


def log_of(folder, days_ago=0, today=TODAY):
    when = datetime(today.year, today.month, today.day, 7, 0).astimezone() - (datetime(1970, 1, 2) - datetime(1970, 1, 1)) * days_ago
    return {"last_backup": {"at": when.isoformat(timespec="seconds"), "file": "i485-backup-2026-10-05-070000.zip", "folder": folder}}


def test_the_backup_check_wants_a_fresh_backup_on_another_device_or_a_network_path():
    def finder(path):
        return {"/home/user/firm/data": REAL_LINUX_FINDMNT, "/mnt/usb/backups": DOC_LINUX_FINDMNT_USB, "/home/user/backups": REAL_LINUX_FINDMNT, "/mnt/nas": DOC_LINUX_FINDMNT_NFS}.get(path, Out(1, "", "no such mount"))

    def check(folder, days=0):
        c, _ = ctx(log=log_of(folder, days), findmnt=lambda path: finder(path))
        return posture.check_backup(c)

    on = check("/mnt/usb/backups")
    assert on["result"] == ON and "another device" in on["why"] and "0 days old (10/05/2026)" in on["why"] and on["side"].startswith("the backup log")
    assert check("/mnt/nas")["result"] == ON and "network path" in check("/mnt/nas")["why"]
    same = check("/home/user/backups")
    assert same["result"] == OFF and "same device as the data" in same["why"]
    stale = check("/mnt/usb/backups", days=5)
    assert stale["result"] == OFF and "5 days old" in stale["why"] and "every night" in stale["why"]
    gone = check("/mnt/unplugged")
    assert gone["result"] == UNKNOWN and "may be unplugged" in gone["why"]
    none = posture.check_backup(ctx(log={})[0])
    assert none["result"] == OFF and none["why"] == "No backup has been made yet."
    assert posture.check_backup(ctx(log={"last_backup": {"at": "garbage"}})[0])["result"] == UNKNOWN


def test_the_backup_check_on_windows_and_wsl_compares_drives_and_asks_windows_what_kind_of_drive():
    c, run = ctx("windows", data="C:\\firm\\data", log=log_of("D:\\backups"), win_drive=lambda drive: {"D": DOC_WIN_DRIVE_FIXED, "C": DOC_WIN_DRIVE_FIXED}[drive])
    got = posture.check_backup(c)
    assert got["result"] == ON and "another device" in got["why"] and ("win_drive", {"drive": "D"}) in run.calls
    assert posture.check_backup(ctx("windows", data="C:\\firm\\data", log=log_of("C:\\backups"), win_drive=DOC_WIN_DRIVE_FIXED)[0])["result"] == OFF  # the same drive
    net = posture.check_backup(ctx("windows", data="C:\\firm\\data", log=log_of("\\\\nas\\backups"), win_drive=DOC_WIN_DRIVE_FIXED)[0])
    assert net["result"] == ON and "network path" in net["why"]
    mapped = posture.check_backup(ctx("windows", data="C:\\firm\\data", log=log_of("Z:\\"), win_drive=lambda drive: DOC_WIN_DRIVE_NETWORK if drive == "Z" else DOC_WIN_DRIVE_FIXED)[0])
    assert mapped["result"] == ON and "network path" in mapped["why"]
    wsl = posture.check_backup(ctx("wsl", data="/home/atendente/firm/data", log=log_of("/mnt/d/backups"), win_lxss=DOC_WIN_LXSS, win_drive=DOC_WIN_DRIVE_FIXED)[0])
    assert wsl["result"] == ON and wsl["side"].endswith("Windows, reached from WSL")
    inside = posture.check_backup(ctx("wsl", data="/home/atendente/firm/data", log=log_of("/home/atendente/backups"), win_lxss=DOC_WIN_LXSS)[0])
    assert inside["result"] == OFF and "same device as the data" in inside["why"]  # both inside WSL's own disk


# -- the updates ---------------------------------------------------------------------------------------------------------------------------------------------------------


def test_the_update_check_reads_apts_log_rpms_list_and_windows_hotfixes():
    real = posture.check_updates(ctx(today=date(2026, 10, 4), files=lambda p: REAL_APT_HISTORY)[0])
    assert real["result"] == ON and real["line"] == "End-Date: 2026-10-04  15:13:54" and "10/04/2026" in real["why"] and real["side"] == "Linux (apt's own log)"
    old = posture.check_updates(ctx(today=date(2026, 12, 1), files=lambda p: REAL_APT_HISTORY)[0])
    assert old["result"] == OFF and "58 days ago" in old["why"]
    fedora = posture.check_updates(ctx(rpm_last=DOC_RPM_LAST)[0])
    assert fedora["result"] == ON and "10/03/2026" in fedora["why"]
    nothing = posture.check_updates(ctx()[0])
    assert nothing["result"] == UNKNOWN and "no apt history log and no rpm database" in nothing["why"]
    win = posture.check_updates(ctx("windows", win_hotfix=DOC_WIN_HOTFIX_RECENT)[0])
    assert win["result"] == ON and "KB5031356" in win["why"] and "09/23/2026" in win["why"] and win["line"] == "KB5031356 2026-09-23"
    assert posture.check_updates(ctx("wsl", win_hotfix=DOC_WIN_HOTFIX_OLD)[0])["result"] == OFF
    none = posture.check_updates(ctx("windows", win_hotfix=DOC_WIN_HOTFIX_NONE)[0])
    assert none["result"] == UNKNOWN and "listed none" in none["why"]


# -- the firewall ----------------------------------------------------------------------------------------------------------------------------------------------------


def test_the_firewall_check_reads_three_windows_profiles_or_the_linux_firewall_that_is_installed():
    assert posture.check_firewall(ctx("windows", win_firewall=DOC_WIN_FIREWALL_ON)[0])["result"] == ON
    off = posture.check_firewall(ctx("wsl", win_firewall=DOC_WIN_FIREWALL_PUBLIC_OFF)[0])
    assert off["result"] == OFF and "Public profile" in off["why"] and off["line"] == "Domain=True; Private=True; Public=False" and off["side"] == "Windows, reached from WSL"
    assert posture.check_firewall(ctx("windows", win_firewall=Out(1, "", "The term 'Get-NetFirewallProfile' is not recognized"))[0])["result"] == UNKNOWN
    ufw = lambda out: ctx(which=lambda x: "/usr/sbin/ufw" if x == "ufw" else None, systemctl_active=out)[0]  # noqa: E731
    assert posture.check_firewall(ufw(Out(0, "active\n")))["result"] == ON
    assert posture.check_firewall(ufw(Out(3, "inactive\n")))["result"] == OFF
    down = posture.check_firewall(ufw(REAL_LINUX_SYSTEMD_DOWN))  # ufw installed, systemd not running (as on this real machine)
    assert down["result"] == UNKNOWN and "could not be read here" in down["why"]
    assert posture.check_firewall(ctx(firewall_cmd_state=Out(0, "running\n"))[0])["result"] == ON
    assert posture.check_firewall(ctx(firewall_cmd_state=Out(252, "not running\n"))[0])["result"] == OFF
    assert posture.check_firewall(ctx(nft_ruleset=DOC_LINUX_NFT_RULES)[0])["result"] == ON
    assert posture.check_firewall(ctx(nft_ruleset=REAL_LINUX_NFT_EMPTY)[0])["result"] == OFF  # the real machine's empty ruleset
    assert posture.check_firewall(ctx(nft_ruleset=DOC_LINUX_NFT_DENIED)[0])["result"] == UNKNOWN  # listing rules needs an administrator
    none = posture.check_firewall(ctx()[0])
    assert none["result"] == UNKNOWN and "none of ufw, firewalld or nftables is installed" in none["why"]
    both = posture.check_firewall(ctx(which=lambda x: "/usr/sbin/ufw" if x == "ufw" else None, systemctl_active=Out(3, "inactive\n"), firewall_cmd_state=Out(0, "running\n"))[0])
    assert both["result"] == ON  # any one of the installed firewalls on is on


# -- a check that cannot run ------------------------------------------------------------------------------------------------------------------------------------------------


def test_a_check_that_cannot_run_here_says_not_known_and_why_and_never_guesses(tmp_path):
    mac = posture.run_all(tmp_path, run=table(), system="other", today=TODAY, log={})
    assert {c["id"] for c in mac["checks"]} == set(posture.CHECKS)
    for c in mac["checks"]:
        if c["id"] == "backup":
            continue  # the backup log is read wherever the product runs: "no backup yet" is a fact
        assert c["result"] == UNKNOWN and c["why"].startswith("Not known: ") and "Windows and Linux" in c["why"] and c["command"] == ""
    broken = posture.run_all(tmp_path, run=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")), system="linux", today=TODAY, log={}, which=lambda x: None,
                             linux_files=lambda p: (_ for _ in ()).throw(OSError(p)))
    stopped = {c["id"] for c in broken["checks"] if c["why"] == "Not known: the check itself stopped (RuntimeError)"}
    assert stopped == {"disk", "updates", "firewall"} and all(c["result"] == UNKNOWN for c in broken["checks"] if c["id"] in stopped)  # one check stopping is not a guess and not the others' loss
    assert all(c["at"] and c["side"] is not None and c["means_on"] and c["fix"] for c in mac["checks"] + broken["checks"])  # every check carries what "on" means, what IT does, and when


def test_the_system_is_named_from_the_os_and_wsl_is_told_from_linux(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    assert posture.system_name() == ("wsl" if sys.platform.startswith("linux") else "other")
    monkeypatch.delenv("WSL_DISTRO_NAME")
    monkeypatch.setattr(sys, "platform", "darwin")
    assert posture.system_name() == "other"


# -- read only: the commands it may run -----------------------------------------------------------------------------------------------------------------------------------

# This file's own copy of what the module may run. A command added to the module without being added here fails the test; a verb that changes something is refused whatever the list says.
ALLOWED_COMMANDS = {
    "findmnt -n -o SOURCE,FSTYPE --target {path}", "lsblk -s -n -o TYPE,NAME {device}", "systemctl is-active {unit}", "firewall-cmd --state", "nft list ruleset", "rpm -qa --last",
    "gsettings get org.gnome.desktop.screensaver lock-enabled", "gsettings get org.gnome.desktop.session idle-delay",
    "kreadconfig5 --file kscreenlockerrc --group Daemon --key Autolock", "kreadconfig5 --file kscreenlockerrc --group Daemon --key Timeout",
}
ALLOWED_CMDLETS = {"Get-ChildItem", "Get-ItemProperty", "Get-CimInstance", "Get-HotFix", "Get-NetFirewallProfile", "New-Object", "ForEach-Object", "Where-Object", "Sort-Object", "Select-Object"}
ALLOWED_POWERSHELL = {"win_bitlocker", "win_lxss", "win_screen", "win_drive", "win_hotfix", "win_firewall"}
CHANGES_A_SETTING = re.compile(r"\b(Set|Remove|Enable|Disable|Add|Start|Stop|Restart|Clear|Install|Uninstall|Update|Write|Out-File|Invoke|Register|Unregister|Reset|Suspend|Resume)-")
CHANGES_THE_SYSTEM = re.compile(r"\b(rm|mv|cp|tee|dd|chmod|chown|kill|reboot|shutdown|mkfs|cryptsetup|manage-bde|netsh|ufw|iptables|sed|apt|apt-get|dnf|yum|wget|curl|start|stop|enable|disable|restart|flush|delete|add|set)\b|[>]")


def test_the_module_may_run_only_the_listed_read_only_commands():
    shown_argv = {" ".join(spec["argv"]) for spec in posture.COMMANDS.values() if "argv" in spec}
    assert shown_argv == ALLOWED_COMMANDS
    assert {n for n, spec in posture.COMMANDS.items() if "ps" in spec} == ALLOWED_POWERSHELL
    for name, spec in posture.COMMANDS.items():
        text = spec["ps"] if "ps" in spec else " ".join(spec["argv"])
        assert not CHANGES_A_SETTING.search(text) and (not CHANGES_THE_SYSTEM.search(text) or "ps" in spec), (name, text)
        if "ps" in spec:
            cmdlets = set(re.findall(r"\b([A-Z][a-z]+-[A-Z][A-Za-z]+)\b", text))
            assert cmdlets <= ALLOWED_CMDLETS, (name, cmdlets - ALLOWED_CMDLETS)
        else:
            assert spec["argv"][0] in {"findmnt", "lsblk", "systemctl", "firewall-cmd", "nft", "rpm", "gsettings", "kreadconfig5"}


def test_run_refuses_anything_not_on_the_list_and_a_value_that_is_not_what_it_says(monkeypatch):
    started = []
    monkeypatch.setattr(posture.subprocess, "run", lambda *a, **k: started.append(a) or (_ for _ in ()).throw(AssertionError("started")))
    for name, params in (("rm", {}), ("manage-bde", {"drive": "C"}), ("systemctl_active", {"unit": "sshd"}), ("win_bitlocker", {"drive": "C:\\Windows"}), ("findmnt", {"path": "relative/path"}),
                         ("lsblk_chain", {"device": "sda"}), ("systemctl_start", {"unit": "ufw"})):
        with pytest.raises(posture.NotAllowed):
            posture._run(name, "linux", **params)
    assert not started  # nothing was started for any of them


def test_the_module_starts_a_process_in_one_place_and_writes_one_file_in_one_place():
    tree = ast.parse(Path(posture.__file__).read_text(encoding="utf-8"))
    parents = {}
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for node in ast.walk(fn):
            parents.setdefault(id(node), []).append(fn.name)
    starts, writes = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = getattr(node.func.value, "id", "")
            name = node.func.attr
            where = parents.get(id(node), ["(module)"])[-1]
            if owner == "subprocess" or (owner == "os" and name in ("system", "popen", "spawnl", "execv")):
                starts.add(where)
            if (owner == "os" and name in ("open", "replace", "remove", "unlink", "chmod", "rename", "mkdir")) or name in ("write_text", "write_bytes", "unlink", "mkdir", "touch"):
                writes.add(where)
    assert starts == {"_run"}, starts
    assert writes == {"keep"}, writes  # the one file it writes is its own reading of the machine (data/posture.json)
    source = Path(posture.__file__).read_text(encoding="utf-8")
    assert "shell=True" not in source and "os.system" not in source


# -- the kept file, Settings, the morning report ------------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def mixed(tmp_path):
    """A reading with one check of each result: the disk on, the screen not known, the firewall off, the backup off, the updates on."""
    run = table(findmnt=DOC_LINUX_FINDMNT_LUKS, lsblk_chain=DOC_LINUX_LSBLK_LUKS, nft_ruleset=REAL_LINUX_NFT_EMPTY)
    return posture.run_all(tmp_path, run=run, system="linux", today=TODAY, log={}, linux_files=lambda p: REAL_APT_HISTORY)


def test_the_kept_file_is_owner_only_and_the_screen_and_the_report_say_what_it_holds(mixed, tmp_path):
    path = tmp_path / "data" / "posture.json"
    posture.keep(mixed, path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 or os.name != "posix"
    assert posture.read(path) == mixed and not list(path.parent.glob("*.tmp"))
    assert posture.read(tmp_path / "nothing.json") is None
    view = posture.view(posture.read(path))
    by = {x["id"]: x for x in view["lines"]}
    assert [x["id"] for x in view["lines"]] == list(posture.CHECKS) and by["disk"]["result"] == ON and by["firewall"]["result"] == OFF and by["backup"]["result"] == OFF
    assert by["screen"]["line"].startswith("Screen lock: not known. ") and "Not known" not in by["screen"]["line"]
    assert view["note"].startswith("The product only reads these. It never changes a setting of the computer")
    assert by["firewall"]["command"].startswith("nft list ruleset") and by["firewall"]["output"] == "the ruleset is empty" and by["firewall"]["fix"]
    report = posture.report_lines(posture.read(path))
    assert report == [f"This computer: {by[i]['line']}" for i in ("screen", "backup", "firewall")]  # in the order of the checks: what is not on
    assert not any("Disk encryption" in x for x in report)  # what is on is not in the morning report
    assert posture.report_lines(None) == ["This computer: not checked yet."] and posture.view(None)["lines"] == []


def test_the_overnight_run_reads_keeps_and_reports_and_the_start_reads_in_the_background(mixed, tmp_path, monkeypatch):
    import overnight

    monkeypatch.setenv("I485_POSTURE", str(tmp_path / "kept" / "posture.json"))
    monkeypatch.setenv("I485_POSTURE_CHECKS", "1")
    monkeypatch.setattr(posture, "run_all", lambda data, **kw: mixed)
    text = overnight.posture_night(tmp_path / "data")
    assert text.splitlines()[:3] == posture.report_lines(mixed) and "your IT person changes what is off" in text.splitlines()[-1]
    kept = posture.read(posture.path_for(tmp_path / "data"))
    assert kept == mixed  # the same reading is what Settings shows
    monkeypatch.setattr(posture, "run_all", lambda data, **kw: {**mixed, "checks": [c | {"result": ON} for c in mixed["checks"]]})
    assert overnight.posture_night(tmp_path / "data") == ""  # all on: the morning report says nothing
    monkeypatch.setenv("I485_POSTURE_CHECKS", "0")
    assert overnight.posture_night(tmp_path / "data") == ""


def test_the_kept_file_is_a_file_a_backup_and_an_export_leave_out_and_the_catalog_knows(tmp_path):
    import records

    assert records.not_backed_up("posture.json") and "posture.json" in backups.SKIP_NAMES
    assert any(r["id"] == "posture" for r in records.WORKING_FILES) and records.coverage("firm", "posture.json") == "never"
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "posture.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data" / "settings.json").write_text("{}", encoding="utf-8")
    made = backups.make_backup(tmp_path / "bk", tmp_path / "data", log_path=tmp_path / "log.json")
    import zipfile

    with zipfile.ZipFile(made["archive"]) as zf:
        assert "data/settings.json" in zf.namelist() and "data/posture.json" not in zf.namelist()


# -- the route: Settings, This computer ---------------------------------------------------------------------------------------------------------------------------------------


def test_the_route_is_the_attorneys_and_shows_what_is_kept(mixed, tmp_path, monkeypatch):
    import test_approvals_queue as aq

    firm = aq.make_firm(tmp_path / "f", monkeypatch)
    monkeypatch.setenv("I485_POSTURE", str(firm.root / "data" / "posture.json"))
    posture.keep(mixed, firm.root / "data" / "posture.json")
    srv = aq.serve_app(firm)
    try:
        sam, jane = aq.sign_in(srv.base, aq.ATTORNEY), aq.sign_in(srv.base, aq.PARALEGAL)
        assert aq.call(srv.base + "/api/posture", jane)[0] == 403  # the data statement is the attorney's
        status, body = aq.call(srv.base + "/api/posture", sam)
        assert status == 200 and body == posture.view(mixed) and body["checked"]["date"] == clock.us_date(mixed["at"])
        os.remove(firm.root / "data" / "posture.json")
        assert aq.call(srv.base + "/api/posture", sam)[1]["lines"] == []  # not read yet: it says so
        monkeypatch.setenv("I485_POSTURE_CHECKS", "1")
        monkeypatch.setattr(posture, "run_all", lambda data, **kw: mixed)
        started = srv.app.start_posture()
        started.join(10)
        assert aq.call(srv.base + "/api/posture", sam)[1] == posture.view(mixed)  # the start's reading is what Settings shows
        monkeypatch.setenv("I485_POSTURE_CHECKS", "0")
        assert srv.app.start_posture() is None
    finally:
        srv.httpd.shutdown()
