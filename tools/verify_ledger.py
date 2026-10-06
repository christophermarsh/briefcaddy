"""Is the event ledger as it was written? (src/ledger_seal.py)

    python tools/verify_ledger.py                 # walks every month of data/events-YYYY-MM.jsonl
    python tools/verify_ledger.py --data <folder> # a copy of the data folder (a restored backup)
    python tools/verify_ledger.py --anchors       # also prints each day's seal: the lines to write down or print and keep

Prints "intact: N rows from MM/DD/YYYY to MM/DD/YYYY (M rows before the chain began)" and exits 0, or names the first row that does not match (its time, who,
which file, and whether it was changed, removed or the file cut short after it) and exits 1. It reads; it changes nothing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import events  # noqa: E402
import ledger_seal  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", help="the data folder (default: this installation's)")
    ap.add_argument("--anchors", action="store_true", help="print each day's seal after the result")
    args = ap.parse_args(argv)
    base = events.base_path(args.data)
    result = ledger_seal.verify(base)
    print(result["line"])
    if args.anchors:
        for a in ledger_seal.read_anchors(base):
            print(ledger_seal.seal_text(a).replace("The record's seal for ", "").replace(": ", "  ", 1))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
