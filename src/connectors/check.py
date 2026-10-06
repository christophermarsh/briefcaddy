"""Read-only checks for the day the firm hands over credentials: proves the
connection and shows what the connector would see, changing nothing.

    python -m connectors.check filevine           # sign in, list a few SIJS projects and one project's documents
    python -m connectors.check google_drive       # sign in, list client folders and one folder's files
    python -m connectors.check microsoft          # SharePoint/OneDrive: list client folders and one folder's files
    python -m connectors.check google_sign_in     # print the Google sign-in link (no sign-in happens)
    python -m connectors.check microsoft_sign_in  # print the Microsoft sign-in link
    python -m connectors.check clio               # Clio: whose connection, the mapped matters, one matter's documents

Run from src/. Nothing is downloaded, uploaded or written.
"""

from __future__ import annotations

import json
import sys
import schema_path

SETTINGS = schema_path.path("register", "connectors")
SOURCES = {"filevine": ("connectors.filevine", "Filevine"), "google_drive": ("connectors.gdrive", "GoogleDrive"),
           "microsoft": ("connectors.msgraph", "Microsoft365")}


def main(argv: list[str] | None = None) -> int:
    import importlib

    which = (argv or sys.argv[1:] or ["?"])[0]
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    try:
        if which in SOURCES:
            module, cls = SOURCES[which]
            source = getattr(importlib.import_module(module), cls)(settings.get(which) or {})
            clients = source.clients()
            print(f"{which}: signed in. {len(clients)} client(s) visible.")
            for c in clients[:5]:
                print(f"  {c.id}  {c.name}")
            if clients:
                docs = source.documents(clients[0])
                print(f"First client's documents ({len(docs)}):")
                for d in docs[:10]:
                    print(f"  {d.name}  ({d.mime or '?'}, {d.size or '?'} bytes)")
        elif which == "clio":  # connected from the review app's Settings page (data/clio); the same check as its "Test" button
            from connectors import clio

            ok, lines = clio.check()
            for line in lines:
                print(line)
            return 0 if ok else 1
        elif which in ("google_sign_in", "microsoft_sign_in"):
            from connectors.signin import GoogleSignIn, MicrosoftSignIn

            cfg = settings["sign_in"]
            redirect = cfg.get("redirect_uri") or "http://127.0.0.1:8485/auth/callback"
            signin = GoogleSignIn(cfg["google_domain"], redirect) if which == "google_sign_in" else MicrosoftSignIn(redirect)
            print(signin.authorize_url("check", "check"))
        else:
            print(__doc__)
            return 2
    except Exception as exc:  # noqa: BLE001 -- a check reports, it doesn't crash
        print(f"{which}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
