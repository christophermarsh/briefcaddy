"""The audit of what the office changes, on the firm's machine (src/audit_fill.py). Once a month, or after a release.

    python tools/fill_audit.py                    # the forms and the reviewers' Saves, written to data/audit_fill.json
    python tools/fill_audit.py --no-saves         # only the forms (the overnight run counts the Saves every night)
    python tools/fill_audit.py --by "Ana Attorney"   # who ran it, for the ledger
    python tools/fill_audit.py --resume           # go on from the checkpoint after a run that stopped (it takes hours at the firm's size: run it at night)

For every case that holds a hand-filled reference (data/reference, named after the case: docs/deployment.md) or whose I-485 the office
corrected on screen, the product's fill is compared with it box by box (src/compare.py, the accuracy tool's own engine). The boxes are
grouped by form, box and kind of change across cases, together with the reviewers' Saves; Reports shows them under "Boxes the office
changes". It reads the case folders and never changes one; it changes no form and no rule. A person reads the groups and decides.

The catalog it writes holds the changed boxes of each case with their values: owner-only, kept with the rest of data/, never in the export of
the firm's data. Run it on the machine that holds data/clients.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import audit_fill  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clients", type=Path, default=REPO / "data" / "clients", help="the case folders (default data/clients)")
    ap.add_argument("--reference", type=Path, help="the folder of the firm's hand-filled references (default data/reference, or I485_REFERENCE)")
    ap.add_argument("--no-saves", action="store_true", help="leave the reviewers' Saves as the overnight run last counted them")
    ap.add_argument("--resume", action="store_true", help="go on from the checkpoint a run that stopped left (same release; the cases already compared are not done again)")
    ap.add_argument("--by", default="", help="who ran it, for the event ledger (default: the audit itself)")
    args = ap.parse_args(argv)
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    if not args.clients.is_dir():
        print(f"No case folder at {args.clients}: run this on the machine that holds the firm's data.", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        data = audit_fill.run(args.clients, Path(tmp), args.reference, log=print, who=args.by or None, saves=not args.no_saves, resume=args.resume)
    f = data["forms"]
    print(f"Wrote {audit_fill.path(args.clients)}")
    print(f"Forms: {f['forms']} on {f['cases']} cases, {f['boxes']:,} boxes compared, {f['changed']:,} changed by the office.")
    if data.get("saves"):
        print(f"Saves: {data['saves']['saves']:,} changes on {data['saves']['cases']} cases.")
    for a in audit_fill.alerts(audit_fill.groups(data["rows"])):
        print(a["text"])
    for line in data.get("skipped") or []:
        print(f"Set aside: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
