"""What needs keeping up to date, and whether it is (src/maintenance.py, schemas/registers/maintenance.json).

    python tools/check_maintenance.py              # what's due, from the dates and the settings
    python tools/check_maintenance.py --live       # also ask the official sources (form editions, fees, I-864P, USCIS pages)
    python tools/check_maintenance.py --mark visa_bulletin --by "Andrew"   # record a check done today
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import maintenance  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--mark")
    ap.add_argument("--by")
    args = ap.parse_args()
    if args.mark:
        if not args.by:
            sys.exit("--by <name>: every check records who did it")
        item = maintenance.mark(args.mark, args.by)
        print(f"{item['id']}: checked {item['last_checked']} by {args.by}")
        return
    if args.live:
        out = maintenance.live_checks()
        for iid, r in out["results"].items():
            print(f"  {'ok ' if r.get('ok') else ('?? ' if r.get('ok') is None else 'NEW')} {iid:20} {r.get('finding') or (r.get('uscis') or '')}")
        print()
    for item in maintenance.status():
        flag = "DUE" if item["due"] else "ok "
        print(f"{flag} {item['id']:20} {item['owner']:9} {item['cadence']:9} last {item['last_checked'] or 'never':10}  {item['what'][:70]}")
        for f in item["findings"]:
            print(f"      -> {f}")


if __name__ == "__main__":
    main()
