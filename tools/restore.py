"""Test a backup, or restore one (the library is src/backups.py).

    python tools/restore.py BACKUP --check              # a test restore: unpack to a temporary folder, check, delete it
    python tools/restore.py BACKUP --into NEWFOLDER     # a real restore, into a new empty folder

BACKUP is a file made by tools/backup.py (a .zip, or a .zip.enc when it was encrypted: you are asked for the passphrase,
or it is read from --passphrase-file or the I485_BACKUP_PASSPHRASE environment variable).

--check opens every JSON file and every database in the backup, compares every file's size and sha256 with the list
inside the backup, counts the case folders and the portal clients against that list, and removes what it unpacked.
It needs room for one temporary copy of the data (--work-folder says where; the default is the computer's temporary
folder). When it finds nothing wrong it records today's date as the last test restore: Keeping current shows it.

--into never writes over anything: the folder must not exist yet or must be empty. The backup's own layout comes out
under it (data/ is the firm's data folder). Stop the review app, move the old data folder aside, and put the restored
one where it was. Every file is checked as it comes out.

Exit code 0: nothing wrong. 1: something does not match (listed). 2: nothing was done.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import backups as backup  # noqa: E402


def _is_encrypted(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(backup.MAGIC)) == backup.MAGIC
    except OSError:
        return False


def passphrase_for(args: argparse.Namespace) -> list[str] | str | None:
    """The passphrases to try, the one in use first and the older ones a rotation kept after it (src/firmsecrets.py); else asked for."""
    import firmsecrets

    if not _is_encrypted(args.backup):
        return None
    if args.passphrase_file:
        return firmsecrets.get_all("backups.passphrase", path=args.passphrase_file, explicit=True) or None
    held = firmsecrets.get_all("backups.passphrase")
    if held:
        return held
    try:
        return getpass.getpass("Passphrase for this backup: ")
    except EOFError:  # run by cron or Task Scheduler: there is nobody to ask
        raise backup.BackupError("This backup is encrypted and there is no terminal to ask for its passphrase. "
                                 "Give --passphrase-file, or set I485_BACKUP_PASSPHRASE.") from None


def report_lines(r: dict, what: str) -> list[str]:
    m, c = r["manifest"], r["checked"]
    lines = [f"Backup made {m['created_at']}, product version {m.get('product_version', 'unknown')}, {'encrypted' if r['encrypted'] else 'not encrypted'}.",
             f"{what}: {c['files']} files, {c['json']} JSON files opened, {c['databases']} databases opened, "
             f"{r['cases']['cases']} case folders and {r['cases']['portal_clients']} portal clients "
             f"(the backup says {m['counts'].get('cases')} and {m['counts'].get('portal_clients')})."]
    if r.get("vault"):
        lines.append(f"The saved Clio connection: {r['vault']}.")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Test or restore a backup.")
    ap.add_argument("backup", type=Path)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="a test restore into a temporary folder, deleted afterwards")
    mode.add_argument("--into", type=Path, help="restore into this new, empty folder")
    ap.add_argument("--passphrase-file", type=Path)
    ap.add_argument("--work-folder", type=Path, help="where --check unpacks (default: the temporary folder)")
    args = ap.parse_args(argv)
    try:
        phrase = passphrase_for(args)
        if args.check:
            r = backup.check_restore(args.backup, phrase, args.work_folder)
            what = "Test restore (unpacked, checked and removed)"
        else:
            r = backup.restore_into(args.backup, phrase, args.into)
            what = f"Restored into {args.into}"
    except backup.BackupError as exc:
        print(f"Nothing was done: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"Nothing was done: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    for line in report_lines(r, what):
        print(line)
    if r["ok"]:
        print("Nothing is wrong." + (" Recorded as the last test restore." if args.check else ""))
        return 0
    print(f"{len(r['problems'])} problem(s):", file=sys.stderr)
    for p in r["problems"]:
        print(f"  {p}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
