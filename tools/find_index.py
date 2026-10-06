"""Build Find across the firm's index of meaning (src/find.py) from scratch, or bring it up to date, with the firm's own model.

    python tools/find_index.py                  # from scratch: data/find.db is made new from every case and the wording library
    python tools/find_index.py --changed        # only the cases whose records changed since their rows were built
    python tools/find_index.py --model bge-m3   # another model on this machine's Ollama (the file is built again: the vectors remember their model)

    --clients FOLDER   the case folders (default: data/clients next to the code)
    --db FILE          the index file (default: I485_FIND, else data/find.db)

Nothing leaves the machine: the model is the firm's own Ollama (OLLAMA_URL, FIND_MODEL). The text is masked before it is embedded.
Exit code 0: built. 1: the model did not answer (nothing changed). 2: no case folder.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import find  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--clients", type=Path, default=REPO / "data" / "clients")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--changed", action="store_true", help="only the cases whose records changed")
    ap.add_argument("--model", default=None, help="the Ollama model (FIND_MODEL; qwen3-embedding:0.6b)")
    args = ap.parse_args(argv)
    if not args.clients.is_dir():
        print(f"No case folder at {args.clients}.")
        return 2
    model = find.OllamaEmbedder(args.model) if args.model else find.embedder()
    db = args.db or find.default_path(args.clients)
    started = time.monotonic()
    try:
        r = (find.rebuild_changed if args.changed else find.rebuild_all)(args.clients, db, model=model)
    except find.ModelUnavailable as exc:
        print(f"{exc} Start Ollama and pull the model (ollama pull {getattr(model, 'model', '')}), then run this again.")
        return 1
    print(f"Find across the firm: {r['rebuilt']} case(s) indexed, {r['passages']} passages, {r['removed']} removed, "
          f"{r['unreadable']} unreadable, in {time.monotonic() - started:.0f} seconds with {model.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
