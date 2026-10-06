"""Change one key or secret, and put the change on record (src/firmsecrets.py; docs/security/security_program.md, "Changing a key or secret").

    python tools/rotate_secret.py --list                          every name, what it is, where it lives now (never a value)
    echo -n "$NEW" | python tools/rotate_secret.py smtp.password  the new value on standard input, never as an argument (arguments show in the process list)
    python tools/rotate_secret.py clio.vault_key --generate       a key kind: the tool makes the new key (it is never shown)
    python tools/rotate_secret.py smtp.password --env-changed     the server's environment holds it: change it there, then say so here, and only the change is recorded

What it does for each kind:
  a secret the vault holds (smtp.password, twilio.auth_token, uscis.client_secret, google.signin_secret, gdrive.service_account, microsoft.client_secret, filevine.*, clio.client_secret):
      the new value is saved in the vault. When the environment holds it the environment wins, so the tool will not write a value that would be ignored: change the environment, then --env-changed.
  clio.vault_key (the vault's key): a new key is made and put first, every saved secret is saved again under it, the old key is dropped (with the key in the environment: put the new key first there
      yourself, run this with --env-changed after removing the old one).
  accounts.totp_key (the staff members' authenticator secrets' key): the same, for every authenticator secret (review/auth.py Accounts.rotate_key).
  backups.passphrase: the new passphrase goes first; the old ones stay listed after it, so every backup made before still opens (restore tries each).
  clio.access_token, clio.refresh_token: Clio's, not the firm's: an attorney connects to Clio again (Settings, Connections).

Every change is recorded (the name, when, who; never the value) in data/secrets_log.json, which the Settings page reads, and in the ledger (kind secrets).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import firmsecrets  # noqa: E402

KEY_NAMES = ("clio.vault_key", "accounts.totp_key")


def read_value() -> str:
    """The new value from standard input (not echoed when it is a terminal). Never from an argument."""
    text = getpass.getpass("New value (not shown): ") if sys.stdin.isatty() else sys.stdin.read()
    return text.strip("\r\n")


def listing(env, data_root: Path) -> list[str]:
    out = [f"{'name':26} {'where it lives':12} what it is"]
    for s in firmsecrets.SECRETS:
        out.append(f"{s.name:26} {firmsecrets.where(s.name, env=env, data_root=data_root) or 'not set':12} {s.label}")
    return out


def rotate(name: str, by: str, *, data_root: Path, env=None, generate: bool = False, env_changed: bool = False, users: Path | None = None, value: str | None = None) -> str:
    """Does the change and records it. Returns one sentence. Raises ValueError (words for the person) with nothing changed when it cannot."""
    env = os.environ if env is None else env
    if name not in firmsecrets.BY_NAME:
        raise ValueError(f"There is no secret called {name}. Run with --list to see the names.")
    spec = firmsecrets.BY_NAME[name]
    held = firmsecrets.where(name, env=env, data_root=data_root, path=firmsecrets.totp_key_path(users) if name == "accounts.totp_key" and users else None)
    if spec.app:
        raise ValueError(f"{spec.label} is Clio's, not the firm's: an attorney connects to Clio again (Settings, Connections) and Clio sends a new one.")
    if env_changed:
        if held != "environment":
            raise ValueError(f"{name} is not held in the server's environment here: leave out --env-changed and give the new value, or set it in the environment first.")
        if name in KEY_NAMES:  # a list of keys with the new one first and the old one after it: what each key opened is saved again under the first
            if name == "clio.vault_key":
                from connectors import clio

                n = clio.Vault(data_root, dict(env)).rotate_key()
            else:
                from review.auth import Accounts

                n = Accounts(users or data_root / "review_users.json").rotate_key()
            firmsecrets.record(name, by, "recorded", data_root=data_root)
            return f"Recorded that {name} was changed in the server's environment, and {n} saved value(s) were saved again under the first key listed. Remove the old key from the environment now."
        firmsecrets.record(name, by, "recorded", data_root=data_root)
        return f"Recorded that {name} was changed in the server's environment (the value is not seen here)."
    if held == "environment":
        raise ValueError(f"{name} is held in the server's environment, which wins over the vault: a new value saved here would be ignored. Change it in the environment, then run this with --env-changed.")
    if name == "clio.vault_key":
        if not generate:
            raise ValueError("The vault's key is made by the tool: run it with --generate.")
        from connectors import clio

        n = clio.Vault(data_root, dict(env)).rotate_key()
        firmsecrets.record(name, by, "rotated", data_root=data_root)
        return f"The vault's key was changed: {n} saved value(s) were saved again under the new key, and the old key was dropped."
    if name == "accounts.totp_key":
        if not generate:
            raise ValueError("The staff authenticator key is made by the tool: run it with --generate.")
        from review.auth import Accounts

        n = Accounts(users or data_root / "review_users.json").rotate_key()
        firmsecrets.record(name, by, "rotated", data_root=data_root)
        return f"The staff authenticator key was changed: {n} authenticator secret(s) were saved again under the new key, and the old key was dropped."
    if generate:
        raise ValueError(f"{name} is given by the provider, not made here: give the new value on standard input.")
    value = (read_value() if value is None else value).strip()
    if not value:
        raise ValueError("No new value was given (on standard input). Nothing was changed.")
    if name == "backups.passphrase":
        import backups

        if len(value) < backups.MIN_PASSPHRASE:
            raise ValueError(f"Use a passphrase of at least {backups.MIN_PASSPHRASE} characters. Nothing was changed.")
        path = firmsecrets.put_passphrase(value, by, env=env)
        return f"The backups' passphrase was changed: new backups use it, and the older ones stay listed after it in {path} so older backups still open."
    firmsecrets.put(name, value, by, env=env, data_root=data_root, how="rotated")
    return f"{name} was changed in the vault."


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n", 2)[2])
    ap.add_argument("name", nargs="?", help="the secret's name (see --list)")
    ap.add_argument("--list", action="store_true", help="every name and where it lives now; never a value")
    ap.add_argument("--generate", action="store_true", help="for a key: make the new key (it is never shown)")
    ap.add_argument("--env-changed", action="store_true", help="the server's environment holds it and was changed there: record only that")
    ap.add_argument("--data", type=Path, default=firmsecrets.DATA, help="the firm's data folder (default: this installation's)")
    ap.add_argument("--users", type=Path, help="the staff accounts' file (default: review_users.json in the data folder)")
    ap.add_argument("--by", help="who is changing it (default: the signed-in user)")
    args = ap.parse_args(argv)
    if args.list:
        print("\n".join(listing(os.environ, args.data)))
        return 0
    if not args.name:
        ap.error("give the secret's name, or --list")
    try:
        print(rotate(args.name, args.by or getpass.getuser(), data_root=args.data, generate=args.generate, env_changed=args.env_changed, users=args.users))
        return 0
    except ValueError as exc:
        print(f"Not changed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 -- the vault or the accounts' file could not be used: words, not a traceback
        print(f"Not changed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
