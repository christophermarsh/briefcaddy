"""Back up the firm's data to one dated zip (the library is src/backups.py; it says what goes in and what stays out).

    python tools/backup.py --out D:\\i485-backups                       # a plain zip
    python tools/backup.py --out /mnt/backup --passphrase-file pass.txt --keep 14

    --out FOLDER           where the dated backup goes (another disk is best; never inside the data folder)
    --data-folder FOLDER   the firm's data folder (default: data/ next to the code)
    --documents FOLDER     the scanned documents (default: clients/ next to the code, when it exists)
    --clients FOLDER, --portal FOLDER   case folders or the portal's data, when they are not under the data folder
    --passphrase-file FILE (or the I485_BACKUP_PASSPHRASE environment variable) encrypts the whole backup. With no
                           passphrase the backup is a plain zip and the Clio key file is written beside it, never inside.
    --keep N               keep the newest N backups in that folder and remove the older ones

The passphrase is never taken on the command line (it would show in the list of running programs). Lose it and the
backup cannot be opened: keep it where the firm keeps its other keys.

Exit code 0: a good backup, recorded (Keeping current shows its date). 1: a file could not be read, so the backup is
not recorded as one. 2: nothing was written.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import backups as backup  # noqa: E402


def passphrase_from(args: argparse.Namespace) -> str | None:
    """The passphrase that encrypts this backup: the file named on the command line (its first line), else the one the server holds (src/firmsecrets.py: the environment,
    a file the environment names, the installer's own file)."""
    import firmsecrets

    if args.passphrase_file:
        if not Path(args.passphrase_file).is_file():
            raise backup.BackupError("The passphrase file could not be read.") from None
        return firmsecrets.get("backups.passphrase", path=args.passphrase_file, explicit=True)
    return firmsecrets.get("backups.passphrase")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Back up the firm's data to one dated zip.")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--data-folder", type=Path, default=REPO / "data")
    ap.add_argument("--documents", type=Path, default=REPO / "clients")
    ap.add_argument("--clients", type=Path)
    ap.add_argument("--portal", type=Path, default=Path(os.environ["PORTAL_DATA"]) if os.environ.get("PORTAL_DATA") else None)
    ap.add_argument("--passphrase-file", type=Path)
    ap.add_argument("--keep", type=int)
    args = ap.parse_args(argv)
    try:
        made = backup.make_backup(args.out, args.data_folder, args.documents, args.clients, args.portal, passphrase_from(args), args.keep)
    except backup.BackupError as exc:
        print(f"Not backed up: {exc}", file=sys.stderr)
        return 2
    s, m = made["summary"], made["manifest"]
    print(f"Backup written: {made['archive']}")
    print(f"  {m['counts']['cases']} case folders, {m['counts']['portal_clients']} portal clients, {m['counts']['files']} files, "
          f"{s['bytes'] / 1_000_000:.1f} MB, {'encrypted' if s['encrypted'] else 'not encrypted'}")
    for role, path in made["key_files"].items():
        print(f"  The {'Clio' if role == 'vault_key' else 'authenticator'} key file is kept apart, beside the backup: {path}")
    if made["key_files"]:
        print("  Keep it somewhere other than the backup (or give a passphrase so everything is encrypted together).")
    import firmsecrets

    held = [firmsecrets.BY_NAME[n].env[0] for n in ("accounts.totp_key", "clio.vault_key") if firmsecrets.where(n) == "environment"]
    if held:
        print(f"  Not in any backup: the key(s) kept in this server's environment ({', '.join(held)}). A restore onto a new server needs them too: keep a copy of them elsewhere.")
    for line in m["left_out"]:
        print(f"  Left out: {line}")
    for line in made["problems"]:
        print(f"  PROBLEM: {line}", file=sys.stderr)
    if made["problems"]:
        print("This backup is NOT counted: fix the problems above and run it again.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
