"""The firm's side of the portal, from the command line.

    python src/portal/admin.py import clients.csv            # id,name,phone,email,language,email_ok,sms_ok,whatsapp_ok
                                                              # (sample: examples/portal_clients.csv -- made-up people)
    python src/portal/admin.py link    <id>                   # print a sign-in link, send nothing (testing, phone support)
    python src/portal/admin.py invite  [ids...]               # sign-in link to everyone not yet invited (or these)
    python src/portal/admin.py remind  [--days 3]             # started or invited, not submitted, quiet for N days
    python src/portal/admin.py request <id>                   # "the office asked for something" (after a review)
    python src/portal/admin.py --root <portal> worker --cases <cases> --jobs <jobs> [--once]  # the same installed jobs worker
    python src/portal/admin.py status                         # one line per client
    python src/portal/admin.py filing  <id> <i485|n400|parole>  # which questionnaire the client gets: green card, citizenship, or humanitarian parole
    python src/portal/admin.py journeys [--quiet]             # update each client's "your case" page (the overnight run does this too)

language: pt (Portuguese), es (Spanish), en (English) or ht (Haitian Creole); the
language's name works too ("Kreyòl", "Haitian Creole"). Blank: Portuguese.

Optional columns: filing (i485 or n400) and office (the office's name on the
Settings page, e.g. "Miami, FL"; without it, the office for the client's state).

Consent columns: only channels marked yes/1/true are ever used. Messages
carry no case details (src/portal/notify.py). With no SMTP/Twilio settings
every message goes to data/portal/outbox.jsonl instead (dry run).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portal.bank import bank_for, language_code, languages, missing_required, required_documents  # noqa: E402
from portal.notify import Notifier  # noqa: E402
from portal.store import CLIENT_ID, PortalStore  # noqa: E402
import clock

REPO = Path(__file__).resolve().parents[2]
YES = {"1", "y", "yes", "true", "sim", "si", "sí", "x"}


def _store(args) -> PortalStore:
    return PortalStore(args.root or os.environ.get("PORTAL_DATA", REPO / "data" / "portal"))


def _base_url() -> str:
    return os.environ.get("PORTAL_BASE_URL", "http://localhost:8600").rstrip("/")


def import_clients(store: PortalStore, path: Path, cases_root: Path | None = None) -> list[str]:
    """Each row a portal client. A new one is looked for first in the conflict search (src/conflicts.py), as the Docketwise import does: the search
    is logged and recorded on the client's case folder as not yet decided, so nothing goes to the client until an attorney decides (Settings, Conflict
    checks). A row whose search cannot run is skipped. cases_root: the case folders (default: the installation's, notify.cases_folder)."""
    import conflicts
    from portal.notify import cases_folder

    cases = Path(cases_root) if cases_root else cases_folder()
    added = []
    first = True
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            client_id = re.sub(r"[^a-z0-9_-]", "_", row.get("id", "").lower()).strip("_")
            if not CLIENT_ID.fullmatch(client_id) or not row.get("name"):
                print(f"skipped a row: needs an id and a name ({row.get('id')!r})")
                continue
            try:
                store.profile(client_id)
                known = True
            except LookupError:
                known = False
            if not known and not (cases / client_id / conflicts.FILE).exists():
                try:
                    record = conflicts.hold_new(cases, client_id, {"name": row["name"]}, by="the command line's import", purpose="cli", via="tool", refresh=first)
                except Exception as exc:  # noqa: BLE001 -- no client without the conflict check
                    print(f"{client_id}: skipped, the conflict search could not run ({type(exc).__name__}); run the import again")
                    continue
                first = False
                found = conflicts.counts(record, cases)
                print(f"{client_id}: conflict search: " + (f"{found['hits']} hit(s)" if found["hits"] else "no hits")
                      + (" and a hit on a restricted case" if found["hidden"] else "") + "; waits for an attorney's decision (Settings, Conflict checks) before any message")
            consent = {ch: row.get(f"{ch}_ok", "").lower() in YES for ch in ("email", "sms", "whatsapp")}
            language = language_code(row.get("language") or "pt")  # pt, es, en, ht -- or the language's name ("Kreyòl", "Spanish")
            if language is None:
                print(f"{client_id}: language {row['language']!r} isn't one the portal speaks ({', '.join(languages())}): Portuguese for now")
            filing = row.get("filing", "").lower()
            setup = ({"filing": filing} if filing in ("i485", "n400", "parole") else {})
            if row.get("office", "").strip():
                setup["office"] = row["office"].strip()
            store.add_client(client_id, row["name"], phone=row.get("phone", ""), email=row.get("email", ""),
                             language=language or "pt", consent=consent, initial_profile=setup)
            added.append(client_id)
    return added


def send(store: PortalStore, notifier: Notifier, client_id: str, kind: str, by: str | None = None, again: bool = False) -> list[dict]:
    """by: the person in the review app who sent it (the log says who); again: a second invitation, which the log says too."""
    # no sign-in link is made for a message that may not go (a restricted case): a link is a working credential for 72 hours
    results = notifier.send(store.profile(client_id), kind)
    store.log(client_id, f"sent_{kind}", {"channels": results, **({"by": by} if by else {}), **({"again": True} if again else {})})

    held = any(r.get("result") == "skipped" and r.get("channel") == "all" for r in results)  # a restricted case, or no case folders: nothing went
    # invited only when the message went somewhere: a send that reached no channel (none agreed to, restricted, failed) is not an invitation
    if kind == "invite" and not held and store.profile(client_id).get("status") == "invited" and any(r.get("result") == "sent" for r in results):
        store.update_profile(client_id, invited_at=clock.stamp())
    return results


def last_activity(store: PortalStore, client_id: str) -> datetime | None:
    path = store.client_dir(client_id) / "events.jsonl"
    if not path.exists():
        return None
    last = None
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event["event"] in ("signed_in", "answers_saved", "upload", "confirmed", "submitted", "sent_invite", "sent_reminder"):
            last = event["at"]
    return clock.parse(last) if last else None


def status_line(store: PortalStore, client_id: str) -> str:
    profile, answers = store.profile(client_id), store.answers(client_id)
    bank = bank_for(profile)
    docs = required_documents(answers, bank)
    uploads = store.uploads(client_id)
    have = sum(1 for d in docs if d["required"] and sum(u["doc_id"] == d["id"] and u.get("status") != "retake" for u in uploads) >= d["count"])
    need = sum(1 for d in docs if d["required"])
    tasks = store.tasks(client_id)
    return (f"{client_id:24} {profile.get('status', '?'):10} answers missing {len(missing_required(bank, answers)):3}  "
            f"documents {have}/{need}  client tasks {len([t for t in tasks if t['kind'] != 'upload'])}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", help="portal data folder (default data/portal or $PORTAL_DATA)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("import").add_argument("csv", type=Path)
    sub.add_parser("invite").add_argument("ids", nargs="*")
    sub.add_parser("remind").add_argument("--days", type=int, default=3)
    sub.add_parser("request").add_argument("id")
    sub.add_parser("link").add_argument("id")
    worker = sub.add_parser("worker")
    worker.add_argument("--cases", type=Path, required=True, help="configured case folder")
    worker.add_argument("--jobs", type=Path, required=True, help="configured jobs folder; this worker also services other due jobs")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--no-policies", action="store_true")
    sub.add_parser("status")
    filing = sub.add_parser("filing")
    filing.add_argument("id")
    filing.add_argument("filing", choices=["i485", "n400", "parole"])
    sub.add_parser("journeys").add_argument("--quiet", action="store_true", help="update the pages without telling anyone")
    args = parser.parse_args(argv)

    if args.command == "worker" and not (args.root or os.environ.get("PORTAL_DATA")):
        parser.error("worker requires --root or configured PORTAL_DATA, plus --cases and --jobs")
    store = _store(args)
    notifier = Notifier(store.root / "outbox.jsonl", store=store)
    if args.command == "import":
        if not args.csv.exists():
            print(f"{args.csv}: no such file. The firm's client list goes here, one row per client, with these columns:\n"
                  "  id,name,phone,email,language,email_ok,sms_ok,whatsapp_ok\n"
                  "A made-up sample to try it with: examples/portal_clients.csv")
            return 1
        added = import_clients(store, args.csv)
        print(f"{len(added)} clients imported")
    elif args.command == "invite":
        ids = args.ids or [c for c in store.clients() if not store.profile(c).get("invited_at")]
        for cid in ids:
            print(cid, send(store, notifier, cid, "invite"))
    elif args.command == "remind":
        cutoff = clock.now() - timedelta(days=args.days)
        for cid in store.clients():
            if store.profile(cid).get("status") == "submitted":
                continue
            last = last_activity(store, cid)
            if last is not None and last < cutoff:
                print(cid, send(store, notifier, cid, "reminder"))
    elif args.command == "link":
        print("Use the authenticated staff assisted-consent action. This CLI cannot authenticate a staff session or issue unrestricted client access.")
        return 1
    elif args.command == "request":
        print(args.id, send(store, notifier, args.id, "request"))
    elif args.command == "worker":
        from portal.engine import run_worker

        run_worker(store, once=args.once, use_policies=not args.no_policies, cases=args.cases.absolute(), jobs_root=args.jobs.absolute())
    elif args.command == "journeys":
        from journey import push_to_portal

        pushed = push_to_portal(Path(__file__).resolve().parents[2] / "data" / "clients", store.root, notify=not args.quiet)
        print(f"{len(pushed['changed'])} client page(s) changed, {pushed['notified']} client(s) told there's news")
    elif args.command == "filing":
        store.profile(args.id)  # raises for an unknown client
        store.update_profile(args.id, filing=args.filing)
        names = {"n400": "citizenship (N-400)", "parole": "humanitarian parole (I-131 and I-134)"}
        print(f"{args.id}: {names.get(args.filing, 'green card (I-485)')} questionnaire")
    elif args.command == "status":
        for cid in store.clients():
            print(status_line(store, cid))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
