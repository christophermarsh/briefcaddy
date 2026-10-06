"""Export explicit own-firm workflow references, pending independent adjudication."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from evaluation_candidates import export_candidates


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("firm-root", "corpus-root", "authorization", "actor", "out"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--cases", nargs="+", required=True)
    args = parser.parse_args(argv)
    try:
        result = export_candidates(args.firm_root, args.corpus_root, args.authorization, args.actor, args.cases, args.out)
    except (OSError, ValueError, TypeError, LookupError, PermissionError) as exc:
        print("Candidate export refused: " + str(exc), file=sys.stderr)
        return 2
    print(f"Retained {len(result['candidates'])} local candidate references; adjudication, training and release approval remain absent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
