"""Manage review-app staff accounts (run on the machine that hosts the app).

    python src/review/users.py add jane@yourfirm.com "Jane Doe" paralegal
    python src/review/users.py add sam@yourfirm.com "Sam Attorney" attorney
    python src/review/users.py list
    python src/review/users.py reset jane@yourfirm.com     # new one-time password
    python src/review/users.py role jane@yourfirm.com attorney
    python src/review/users.py disable jane@yourfirm.com   # / enable
    python src/review/users.py setup-code                  # before the first account: the code for "Set up the first attorney"
    python src/review/users.py rotate-key                  # the authenticator secrets' encryption key

"add" and "reset" print a one-time password: give it to the person in
person or by phone (not by email), and they choose their own at first
sign-in. A reset also clears the person's authenticator app (a lost phone):
they set it up again at that first sign-in.

"rotate-key" encrypts every saved authenticator secret again under a new
key (review/auth.py Accounts.rotate_key). With the key file beside the
accounts file, it makes the new key itself. With I485_TOTP_KEY in the
server's environment: first list the new key, a comma, then the old one;
run this; then leave only the new key and restart the app.

The first attorney is normally made from the screen, not here: "setup-code"
makes the accounts file (empty) and prints a one-time code. The review app
then shows "Set up the first attorney" to a request that carries the code
(add ?setup=CODE to the address), or, with a box for the code, to the
computer that runs it; the code is needed either way, and the app prints a
code of its own each time it starts while there is no account (where it goes:
docs/deployment.md, "The first attorney, from the screen").
Once the account exists the screen never comes back. The installer
(install.sh, install.ps1) does this and prints the address.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from review.auth import ROLES, Accounts  # noqa: E402

DEFAULT = Path(__file__).resolve().parents[2] / "data" / "review_users.json"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Review app staff accounts")
    parser.add_argument("--file", default=DEFAULT, type=Path)
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("email")
    add.add_argument("name")
    add.add_argument("role", choices=ROLES)
    sub.add_parser("list")
    sub.add_parser("setup-code")
    sub.add_parser("rotate-key")
    for cmd in ("reset", "disable", "enable"):
        sub.add_parser(cmd).add_argument("email")
    role = sub.add_parser("role")
    role.add_argument("email")
    role.add_argument("role", choices=ROLES)
    args = parser.parse_args(argv)
    accounts = Accounts(args.file)
    try:
        if args.cmd == "add":
            print(f"Account created for {args.name} ({args.role}). One-time password: {accounts.add(args.email, args.name, args.role)}")
        elif args.cmd == "reset":
            print(f"New one-time password for {args.email}: {accounts.reset(args.email)}")
        elif args.cmd in ("disable", "enable"):
            accounts.update(args.email, active=args.cmd == "enable")
            print(f"{args.email}: {'enabled' if args.cmd == 'enable' else 'disabled (signed out everywhere)'}")
        elif args.cmd == "setup-code":
            print(accounts.new_setup_code())
        elif args.cmd == "rotate-key":
            print(f"Re-encrypted {accounts.rotate_key()} authenticator secret(s) under the new key.")
        elif args.cmd == "role":
            accounts.update(args.email, role=args.role)
            print(f"{args.email} is now {args.role}")
        else:
            for u in accounts.users():
                print(f"{u['email']:40} {u['name']:28} {u['role']:10} {'active' if u['active'] else 'DISABLED'}{'  (must set password)' if u['must_change'] else ''}"
                      f"{'  (code set up)' if u['code_set_up'] else ''}")
    except (ValueError, LookupError) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
