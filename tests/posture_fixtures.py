"""The machine's posture (src/posture.py): the disk encrypted, the screen locked, a recent backup on another device, the system updated, the firewall on. Read, never set.

Each check is tested against recorded outputs. WHERE EACH OUTPUT CAME FROM, in each fixture's name:

  REAL_*     run on a real system while this test was written: the cloud machine of this session (Ubuntu 24.04.4 LTS, Linux 6.18, a virtual machine with systemd not running, run as root),
             10/04/2026. Its disk is not encrypted, its firewall ruleset is empty, it has no desktop: those are the "off" and "not known" cases, as the machine really answered.
  DOC_LINUX  a Linux output written from the documentation (the man pages of lsblk, findmnt, systemctl, firewall-cmd and nft, and apt's history log format), NOT run: the cases the real
             machine could not give (an encrypted disk, a running firewall, a GNOME desktop). Marked in docs/decisions.md.
  DOC_WIN    a Windows output written from the documentation of the command and from published scripts that read the same property (BitLocker's Shell property, the screen saver registry
             values, Get-HotFix, Get-NetFirewallProfile, Win32_LogicalDisk, the Lxss registry key), NOT run on Windows: no Windows system was available. Marked in docs/decisions.md.

Shared by tests/test_posture.py and tests/e2e/test_posture.py."""

from __future__ import annotations

from datetime import date

from posture import Out

TODAY = date(2026, 10, 5)

REAL_LINUX_FINDMNT = Out(0, "/dev/vda ext4\n")  # findmnt -n -o SOURCE,FSTYPE --target /home/user/law-app
REAL_LINUX_LSBLK_PLAIN = Out(0, "disk vda\n")  # lsblk -s -n -o TYPE,NAME /dev/vda
REAL_LINUX_SYSTEMD_DOWN = Out(1, "", "System has not been booted with systemd as init system (PID 1). Can't operate.\nFailed to connect to bus: Host is down\n")
REAL_LINUX_NFT_EMPTY = Out(0, "")  # nft list ruleset, as root
REAL_LINUX_GSETTINGS_MISSING = Out(1, "", "No such schema 'org.gnome.desktop.screensaver'\n")
REAL_APT_HISTORY = ("Start-Date: 2026-10-04  15:13:51\nCommandline: apt-get install -y --no-install-recommends xvfb\nUpgrade: libglib2.0-bin:amd64 (2.80.0-6ubuntu3.8, 2.80.0-6ubuntu3.9)\n"
                    "End-Date: 2026-10-04  15:13:54\n")
DOC_LINUX_FINDMNT_LUKS = Out(0, "/dev/mapper/cryptroot ext4\n")
DOC_LINUX_LSBLK_LUKS = Out(0, "crypt cryptroot\npart nvme0n1p3\ndisk nvme0n1\n")
DOC_LINUX_LSBLK_LVM_ON_LUKS = Out(0, "lvm vg-root\ncrypt cryptlvm\npart sda3\ndisk sda\n")
DOC_LINUX_FINDMNT_OVERLAY = Out(0, "overlay overlay\n")
DOC_LINUX_FINDMNT_BTRFS = Out(0, "/dev/mapper/cryptroot[/@home] btrfs\n")
DOC_LINUX_FINDMNT_USB = Out(0, "/dev/sdb1 ext4\n")
DOC_LINUX_FINDMNT_NFS = Out(0, "nas.office.example:/backups nfs4\n")
DOC_LINUX_NFT_RULES = Out(0, "table inet filter {\n\tchain input {\n\t\ttype filter hook input priority filter; policy drop;\n\t\tct state established,related accept\n\t}\n}\n")
DOC_LINUX_NFT_DENIED = Out(1, "", "Operation not permitted (you must be root)\n")
DOC_RPM_LAST = Out(0, "kernel-core-6.5.6-300.fc39.x86_64                 Fri 03 Oct 2026 06:25:12 AM UTC\nbash-5.2.21-1.fc39.x86_64                         Mon 29 Sep 2026 09:10:00 AM UTC\n")
DOC_WIN_BITLOCKER_ON, DOC_WIN_BITLOCKER_OFF, DOC_WIN_BITLOCKER_ODD = Out(0, "1\r\n"), Out(0, "0\r\n"), Out(0, "2\r\n")
DOC_WIN_BITLOCKER_NOTHING = Out(1, "", "Exception calling \"NameSpace\": The system cannot find the drive specified.\r\n")
DOC_WIN_LXSS = Out(0, "Ubuntu|\\\\?\\C:\\Users\\atendente\\AppData\\Local\\Packages\\CanonicalGroupLimited.Ubuntu_79rhkp1fndgsc\\LocalState\r\nDebian|D:\\wsl\\debian\r\n")
DOC_WIN_SCREEN_MACHINE = Out(0, "InactivityTimeoutSecs=600\r\nScreenSaveActive=\r\nScreenSaverIsSecure=\r\nScreenSaveTimeOut=\r\nPolicyScreenSaveActive=\r\nPolicyScreenSaverIsSecure=\r\nPolicyScreenSaveTimeOut=\r\n")
DOC_WIN_SCREEN_SAVER = Out(0, "InactivityTimeoutSecs=\r\nScreenSaveActive=1\r\nScreenSaverIsSecure=1\r\nScreenSaveTimeOut=300\r\nPolicyScreenSaveActive=\r\nPolicyScreenSaverIsSecure=\r\nPolicyScreenSaveTimeOut=\r\n")
DOC_WIN_SCREEN_OFF = Out(0, "InactivityTimeoutSecs=\r\nScreenSaveActive=1\r\nScreenSaverIsSecure=0\r\nScreenSaveTimeOut=600\r\nPolicyScreenSaveActive=\r\nPolicyScreenSaverIsSecure=\r\nPolicyScreenSaveTimeOut=\r\n")
DOC_WIN_SCREEN_LONG = Out(0, "InactivityTimeoutSecs=3600\r\nScreenSaveActive=\r\nScreenSaverIsSecure=\r\nScreenSaveTimeOut=\r\nPolicyScreenSaveActive=\r\nPolicyScreenSaverIsSecure=\r\nPolicyScreenSaveTimeOut=\r\n")
DOC_WIN_SCREEN_NOTHING = Out(0, "InactivityTimeoutSecs=\r\nScreenSaveActive=\r\nScreenSaverIsSecure=\r\nScreenSaveTimeOut=\r\nPolicyScreenSaveActive=\r\nPolicyScreenSaverIsSecure=\r\nPolicyScreenSaveTimeOut=\r\n")
DOC_WIN_DRIVE_FIXED, DOC_WIN_DRIVE_NETWORK = Out(0, "DriveType=3 ProviderName=\r\n"), Out(0, "DriveType=4 ProviderName=\\\\nas\\backups\r\n")
DOC_WIN_HOTFIX_RECENT, DOC_WIN_HOTFIX_OLD, DOC_WIN_HOTFIX_NONE = Out(0, "KB5031356 2026-09-23\r\n"), Out(0, "KB5025885 2026-05-09\r\n"), Out(0, "")
DOC_WIN_FIREWALL_ON, DOC_WIN_FIREWALL_PUBLIC_OFF = Out(0, "Domain=True\r\nPrivate=True\r\nPublic=True\r\n"), Out(0, "Domain=True\r\nPrivate=True\r\nPublic=False\r\n")
NOT_INSTALLED = Out(127, "", "not installed")


def table(**answers):
    """A runner that answers each listed command from a recorded output (a callable gets the parameters); a command not listed is "not installed"."""
    calls = []

    def run(name, system="linux", **params):
        calls.append((name, params))
        got = answers.get(name, NOT_INSTALLED)
        return got(**params) if callable(got) else got

    run.calls = calls
    return run


