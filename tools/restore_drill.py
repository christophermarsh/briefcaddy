"""The restore drill: restore the latest backup into a scratch folder and compare it, record by record, with the live install (the library is src/backups.py drill).

    python tools/restore_drill.py                         # the newest backup the log records
    python tools/restore_drill.py BACKUP                  # this one
    python tools/restore_drill.py --passphrase-file F     # an encrypted backup (or I485_BACKUP_PASSPHRASE, or I485_BACKUP_PASSPHRASE_FILE)

The backup is unpacked with the one restore there is (tools/restore.py --into does the same) into a scratch folder that is removed when the drill ends, pass or fail. Every file the
backup holds is compared with the install it was made from: the same set of files, every JSON record field by field, the event ledger's months row by row, every database's
tables and row counts, every document by its sha256, and the saved Clio connection opening with its key. The install is never written to.

A difference is "since" when the live copy changed after the backup was made (the install went on working: expected, counted) and a failure when it did not (the backup and
the install disagree). Each difference is printed with the record, the field and the two times; a value is never printed. The result is recorded in the backup log (Keeping
current shows it) and, when the drill passed, as the register's check of the monthly restore drill item.

Exit code 0: it passed. 1: it failed (the differences are listed). 2: nothing was done (no backup, a wrong passphrase, another drill running).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backups as backup  # noqa: E402
import restore  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Restore the latest backup into a scratch folder and compare it with the live install.")
    ap.add_argument("backup", type=Path, nargs="?", help="a backup made by tools/backup.py (default: the newest the log records)")
    ap.add_argument("--passphrase-file", type=Path)
    ap.add_argument("--work-folder", type=Path, help="where the scratch folder is made (default: the computer's temporary folder)")
    args = ap.parse_args(argv)
    archive = args.backup or backup.latest()
    if archive is None:
        print("Nothing was done: no backup has been made yet (give one, or run tools/backup.py first).", file=sys.stderr)
        return 2
    args.backup = archive
    try:
        phrase = restore.passphrase_for(args) if archive.is_file() else None
        result = backup.drill(archive, phrase or backup.drill_passphrase(), work_parent=args.work_folder)
    except backup.BackupError as exc:
        print(f"Nothing was done: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"Nothing was done: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if not result.get("ran"):
        print(f"Nothing was done: {result['line']}", file=sys.stderr)
        return 2
    if result.get("stopped"):
        print(result["line"])
        return 2
    c = result["checked"]
    print(f"Backup {result['backup']}, made {result['backup_at']}.")
    print(f"Compared in {result['seconds']} s: {c['files']} files, {c['json']} JSON files, {c['ledger_months']} months of the ledger, {c['databases']} databases, "
          f"{c['documents']} documents by sha256.")
    print(f"{result['expected']} difference(s) since the backup was made (expected: the install changed after it).")
    for d in result["differences"]:
        print(f"  FAILED {d['what']} (the backup: {d['backup_time']}; the install: {d['live_time']})", file=sys.stderr)
    if result["failures"] > len(result["differences"]):
        print(f"  ... and {result['failures'] - len(result['differences'])} more", file=sys.stderr)
    print(result["line"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
