"""SECURITY.md, made from the installation's deployment.json (src/deployment.py), so it is never stale (brief J2).

    python tools/security_md.py              # write SECURITY.md at the root from deployment.json (the provider's name and security contact)
    python tools/security_md.py --check      # fail (exit 1) when the repository's SECURITY.md differs from the repository's own copy
    python tools/security_md.py --out FILE   # write it elsewhere

The security contact is the provider's e-mail in deployment.json (the installer asks for it as "the security contact"); while it is not set the
file says "[the provider's security contact]". How many days until an answer is the owner's to set: the file says "[days]" until then (DAYS).
The check compares with what the code makes from the shipped defaults (no provider details), as tools/public_pages.py --check does: an
installation's own copy, with its contact, is made with the plain command and is never what the repository holds.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import deployment  # noqa: E402

TARGET = REPO / "SECURITY.md"
DAYS = "[days]"  # the owner sets how many days until the first answer; never a number we did not choose
NO_CONTACT = "[the provider's security contact]"
NO_NAME = "[the provider's name]"


def render(provider: dict) -> str:
    name = (provider.get("name") or "").strip()
    name = NO_NAME if not name or name == deployment.DEFAULT["provider"]["name"] else name
    email = (provider.get("email") or "").strip()
    contact = f"{email} ({name}'s security contact)" if email else NO_CONTACT
    return f"""# Security

How to tell us about a security problem in this software: the staff review app, the client portal, the overnight run, the connectors to other
systems, and the installer and update scripts that come with them. The software is made and supported by {name}.

This file is made from the installation's settings by `python tools/security_md.py` (`--check` in `tools/ci.sh`); edit those, not this file.

## How to report a problem

Write to {contact}.

Please say:

- what you found, and where (which screen, which address, which file);
- how to see it again, step by step;
- what someone could do with it.

Please do not:

- look at, copy, change or delete anyone's information beyond the least needed to show the problem;
- send a client's real information in your report (if you saw some, say so, and we will tell you what to do with it);
- test against a law firm's own installation without that firm's written permission: test against your own copy;
- try to overwhelm a server with traffic, or trick the firm's staff or clients.

## What to expect

- An answer within {DAYS} days, saying whether we can see the problem and what happens next.
- A fix goes out as an update like any other (the release notes in docs/releases.md list it), and a firm that runs the software on its own
  machine installs it with its update script.
- We do not offer payment or rewards for reports.

## What this covers

- Covered: the code in this repository, as shipped.
- Not covered, and to be reported to whoever runs them: a law firm's own computers, network, proxy and hosting (the firm's IT; the guide we give
  them is docs/hardening.md), and the outside services the software can connect to (Microsoft, Google, Clio, Filevine, Twilio, the firm's mail
  provider, USCIS).

## What is already known

The threat model, with every finding and whether it is fixed or left to the owner (an independent penetration test and a SOC 2 report are not
done yet), is docs/security/threat_model.md. The rest of the security papers are listed in docs/security/README.md.
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="fail when SECURITY.md differs from the repository's own copy")
    ap.add_argument("--out", type=Path, help="write here instead of SECURITY.md at the root")
    args = ap.parse_args(argv)
    if args.check:
        want = render(dict(deployment.DEFAULT["provider"]))
        have = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if have != want:
            print("SECURITY.md is not what the code makes: run python tools/security_md.py --out SECURITY.md with no deployment.json (the repository's copy).")
            return 1
        print("SECURITY.md is up to date.")
        return 0
    out = args.out or TARGET
    out.write_text(render(deployment.load()["provider"]), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
