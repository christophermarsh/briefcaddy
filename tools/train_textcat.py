"""Train the document-type classifier on invented documents, then test it on
the firm's real ones -- the same documents, truth and "hard" variant as the
Nimble trial (tools/nimble_eval.py), so the three can be compared row by row.
Real documents are only ever tested on, never trained on.

    python tools/train_textcat.py --trial ~/nimble_eval [--results results-17199683.json] [--per-type 600] [--c 4] [--textcat-threshold 0.6] [--no-save]

  --trial    the folder tools/nimble_eval.py wrote (texts.json, results-*.json);
  --results  which Nimble run to compare with (default: the newest);
  --no-save  try settings without replacing data/models/document_type.pkl;
  --no-specimens        invented documents only (no tools/fetch_specimens.py pages);
  --specimen-check      also test on public specimens with each source held out.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from learning import textcat  # noqa: E402
from nimble_eval import hard  # noqa: E402
import schema_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial", type=Path, required=True)
    ap.add_argument("--results", type=str)
    ap.add_argument("--per-type", type=int, default=600)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--c", type=float, default=4.0)
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--no-specimens", action="store_true", help="invented documents only")
    ap.add_argument("--specimen-check", action="store_true",
                    help="also: how well it places public specimens it never saw (each source held out in turn)")
    ap.add_argument("--threshold", type=float, default=0.9, help="Nimble's")
    ap.add_argument("--textcat-threshold", type=float, default=0.6)
    args = ap.parse_args()

    started = time.time()
    if args.no_save:
        pipe, card = textcat.build(args.per_type, args.seed, args.c, not args.no_specimens), {"version": "unsaved" + ("" if args.no_specimens else "+sp")}
    else:
        card = textcat.train(n_per_type=args.per_type, seed=args.seed, c=args.c, use_specimens=not args.no_specimens)
        pipe = textcat.load()["pipeline"]
    print(f"trained in {time.time() - started:.0f}s: {card}", flush=True)

    texts = json.loads((args.trial / "texts.json").read_text())
    results = args.trial / args.results if args.results else max(args.trial.glob("results-*.json"), key=lambda p: p.stat().st_mtime)
    rows = json.loads(results.read_text())["rows"]
    out = []
    for r in rows:
        text = texts[r["doc"]] if r["variant"] == "as read" else hard(texts[r["doc"]])
        t0 = time.time()
        probs = pipe.predict_proba([text])[0]
        best = int(probs.argmax())
        r = r | {"textcat": str(pipe.classes_[best]), "tp": round(float(probs[best]), 3), "tms": round(1000 * (time.time() - t0), 1)}
        out.append(r)
        mark = "  " if r["textcat"] == r["truth"] else "XX"
        print(f"{mark} {r['variant']:8} {r['doc'][:44]:44} truth={r['truth']:20} rules={r['rules']:20} nimble={r['nimble']:20} "
              f"textcat={r['textcat']:20} p={r['tp']:.2f}")

    def score(rs):
        sure = lambda r: r["tp"] >= args.textcat_threshold and r["textcat"] != "other"  # noqa: E731
        return {"documents": len(rs), "rules_right": sum(r["rules"] == r["truth"] for r in rs),
                "nimble_right": sum(r["nimble"] == r["truth"] for r in rs), "textcat_right": sum(r["textcat"] == r["truth"] for r in rs),
                "textcat_confidently_wrong": sum(sure(r) and r["textcat"] != r["truth"] for r in rs),
                "rules_then_textcat": sum((r["rules"] if r["rules"] != "unclassified" else (r["textcat"] if sure(r) else "unclassified")) == r["truth"] for r in rs),
                "rules_then_nimble": sum((r["rules"] if r["rules"] != "unclassified" else (r["nimble"] if r["p"] >= args.threshold and r["nimble"] != "other" else "unclassified")) == r["truth"] for r in rs),
                "textcat_median_ms": sorted(r["tms"] for r in rs)[len(rs) // 2]}

    summary = {"model": card.get("version"), "compared_with": results.name, "threshold": args.threshold, "textcat_threshold": args.textcat_threshold}
    for variant in ("as read", "hard"):
        summary[variant] = score([r for r in out if r["variant"] == variant])
        summary[variant + " (real clients only)"] = score([r for r in out if r["variant"] == variant and not r["doc"].startswith("demo-")])
    (args.trial / f"textcat-{card.get('version')}.json").write_text(json.dumps({"rows": out, "summary": summary}, indent=1))
    if args.specimen_check:
        summary["specimens (source held out)"] = specimen_check(args)
    print(json.dumps(summary, indent=1))


def specimen_check(args, folds: int = 5) -> dict:
    """Real layouts the model never saw: train without one fifth of the
    specimen source files, test on them; five times. Against invented-only."""
    import random

    pages = textcat.specimens()
    # Held out by source FILE, not entry: RENIEC's birth, marriage and death records are one PDF, the CJP 35
    # and CJP 37 one court's pair -- holding out one while its near-identical sibling trains says little.
    sources = json.loads((schema_path.path("register", "training_sources")).read_text(encoding="utf-8"))["sources"]
    file_of = {s["id"]: s.get("local_file") and s["url"] + "#" + s["local_file"] or s["url"] for s in sources}
    file_of.update({sid: "https://www.mass.gov/lists/miscellaneous-probate-and-family-court-forms" for sid in file_of if sid.startswith("ma-cjp")})
    pages = [(t, k, file_of.get(sid, sid)) for t, k, sid in pages]
    ids = sorted({sid for _, _, sid in pages})
    random.Random(0).shuffle(ids)
    out = {}
    for label, with_specimens in (("invented only", False), ("invented + the other specimens", True)):
        right = sure_wrong = 0
        for f in range(folds):
            held = set(ids[f::folds])
            pipe = textcat.build(args.per_type, args.seed, args.c, with_specimens, pages=[p for p in pages if p[2] not in held])
            test = [p for p in pages if p[2] in held]
            for probs, (_, kind, _) in zip(pipe.predict_proba([t for t, _, _ in test]), test):
                pick = pipe.classes_[probs.argmax()]
                right += int(pick == kind)
                sure_wrong += int(pick not in (kind, "other") and probs.max() >= args.textcat_threshold)
            if not with_specimens:  # without specimens every fold trains the same model: one is enough
                right, sure_wrong = _all(pipe, pages, args.textcat_threshold)
                break
        out[label] = {"right": right, "of": len(pages), "sure_and_wrong": sure_wrong}
        print(label, out[label], flush=True)
    return out


def _all(pipe, pages, threshold):
    right = sure_wrong = 0
    for probs, (_, kind, _) in zip(pipe.predict_proba([t for t, _, _ in pages]), pages):
        pick = pipe.classes_[probs.argmax()]
        right += int(pick == kind)
        sure_wrong += int(pick not in (kind, "other") and probs.max() >= threshold)
    return right, sure_wrong


if __name__ == "__main__":
    main()
