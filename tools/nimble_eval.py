"""Trial of a local decision model (Nimble) on one job: what kind of document
is this? Compared with the pipeline's own rules, on the firm's documents,
entirely on this machine. Also how a new wording of the question is scored
before it goes live (docs/learning.md, lever 1).

    python tools/nimble_eval.py --data data/clients --out ~/nimble_eval [--labels labels.json] [--wording new_types.json]

  - truth: --labels ({"client/file.pdf": "doc_type"}) where given, else the rules' own answer;
  - the model sees only the document's text, never its file name;
  - each document is tried twice: as read, and "hard" -- its first lines
    removed, like a poor scan or a photo of part of a page (where rules fail);
  - --wording: a JSON file of document types and descriptions to try instead
    of the current ones (src/learning/decision.py → DOCUMENT_TYPES);
  - texts are cached in --out, so a re-run takes minutes; results name client
    files -- keep --out local.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from classify import classify_text, extract_pages  # noqa: E402
from learning.decision import DOCUMENT_TYPES, document_type  # noqa: E402
from learning.shadow import wording_version  # noqa: E402


def hard(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[max(3, len(lines) // 4):])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", default="nimble:9b-q4_K_M")
    ap.add_argument("--labels", type=Path)
    ap.add_argument("--wording", type=Path)
    ap.add_argument("--chars", type=int, default=6000)
    args = ap.parse_args()
    labels = json.loads(args.labels.read_text()) if args.labels else {}
    types = json.loads(args.wording.read_text(encoding="utf-8")) if args.wording else DOCUMENT_TYPES
    args.out.mkdir(parents=True, exist_ok=True)
    cache = args.out / "texts.json"
    texts = json.loads(cache.read_text()) if cache.exists() else {}
    ask = lambda text: document_type(text, args.model, args.chars, types)  # noqa: E731
    ask("warm-up")  # load the model before timing
    rows = []
    for bundle in sorted(p for p in args.data.iterdir() if (p / "meta.json").exists()):
        meta = json.loads((bundle / "meta.json").read_text())
        folder = Path(meta["source_folder"])
        for doc, rule in meta["classifications"].items():
            key = f"{bundle.name}/{doc}"
            if key not in texts:
                name, _, part = doc.partition("#")
                if not (folder / name).exists():
                    continue
                pages = extract_pages(folder / name)
                if part:  # "file.pdf#p2-3"
                    a, _, b = part[1:].partition("-")
                    pages = pages[int(a) - 1:int(b or a)]
                texts[key] = "\n".join(pages)
                cache.write_text(json.dumps(texts))
            truth = labels.get(key, rule)
            for variant, text in (("as read", texts[key]), ("hard", hard(texts[key]))):
                rule_now = classify_text(text[: args.chars]).doc_type if variant == "hard" else rule
                pick, prob, secs = ask(text)
                rows.append({"doc": key, "variant": variant, "truth": truth, "rules": rule_now, "nimble": pick, "p": round(prob, 3), "s": round(secs, 3)})
                print(f"{variant:8} truth={truth:26} rules={rule_now:26} nimble={pick:26} p={prob:.2f} {secs * 1000:.0f}ms", flush=True)
    summary = {"wording": wording_version(types)}
    for variant in ("as read", "hard"):
        rs = [r for r in rows if r["variant"] == variant]
        summary[variant] = {"documents": len(rs), "rules_right": sum(r["rules"] == r["truth"] for r in rs),
                            "nimble_right": sum(r["nimble"] == r["truth"] for r in rs),
                            "nimble_confidently_wrong": sum(r["nimble"] not in (r["truth"], "other") and r["p"] >= 0.9 for r in rs),
                            "rules_unclassified": sum(r["rules"] == "unclassified" for r in rs),
                            "nimble_right_where_rules_failed": sum(r["nimble"] == r["truth"] and r["rules"] != r["truth"] for r in rs),
                            "nimble_wrong_where_rules_right": sum(r["nimble"] != r["truth"] and r["rules"] == r["truth"] for r in rs),
                            "median_ms": round(1000 * statistics.median(r["s"] for r in rs))}
    (args.out / f"results-{summary['wording']}.json").write_text(json.dumps({"rows": rows, "summary": summary}, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
