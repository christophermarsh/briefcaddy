"""Accuracy against hand-filled references: the report, and how to add the firm's own references.

    python tools/accuracy_report.py                 # every reference: the firm's own, and the made-up ones that ship with the product
    python tools/accuracy_report.py --no-samples    # only the firm's own
    python tools/accuracy_report.py --history       # and add tonight's figures to data/accuracy_history.jsonl

Writes docs/accuracy.md (everything, for the firm) and docs/public/accuracy.md (the page for a prospective firm: the made-up
references only, with the method). Every figure is a count of boxes, written with the number it is a count of and with the
number of references behind it.

Adding one of the firm's own references (the form a person filled by hand and filed for a client the pipeline has processed):
  1. Save the filled PDF in the reference folder, data/reference (I485_REFERENCE points elsewhere), under the case's folder
     name in data/clients:   <case>.pdf for the I-485,   <case>.<form>.pdf for another form (n400, i130, i130a, i765 ...).
  2. Run this tool, or wait for the overnight run: it compares the two box by box and adds the case to the figures.
  3. A box that differs where the reference was the one that was wrong: mark it in the review app (Keeping current, Accuracy
     record; attorney only), with the reason. It is kept in data/reference/<case>.marks.json and counted as the reference's own error.
The reference is only the answer key: nothing that fills a form reads it.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import accuracy  # noqa: E402
import audit_fill  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clients", type=Path, default=REPO / "data" / "clients", help="the case folders (default data/clients)")
    ap.add_argument("--reference", type=Path, help="the folder of the firm's hand-filled references (default data/reference, or I485_REFERENCE)")
    ap.add_argument("--report", type=Path, default=REPO / "docs" / "accuracy.md", help="where the full report goes")
    ap.add_argument("--public", type=Path, default=REPO / "docs" / "public" / "accuracy.md", help="where the page for a prospective firm goes")
    ap.add_argument("--no-samples", action="store_true", help="leave out the made-up references (and so the public page)")
    ap.add_argument("--no-firm", action="store_true", help="leave out the firm's own references")
    ap.add_argument("--history", action="store_true", help="add this run's figures to the nightly history")
    args = ap.parse_args(argv)
    logging.getLogger("pypdf").setLevel(logging.ERROR)

    with tempfile.TemporaryDirectory() as tmp:
        report = accuracy.run(args.clients, Path(tmp), samples=not args.no_samples, firm=not args.no_firm, ref_dir=args.reference)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(accuracy.render_report(report), encoding="utf-8", newline="\n")
    print(f"Wrote {args.report}")
    if not args.no_samples:
        args.public.parent.mkdir(parents=True, exist_ok=True)
        args.public.write_text(accuracy.render_public(report), encoding="utf-8", newline="\n")
        print(f"Wrote {args.public}")
    for name in (accuracy.FIRM, accuracy.SAMPLES):
        fig = report[name]
        if fig["references"]:
            t = fig["total"]
            print(f"{accuracy.SET_NAMES[name]}: {accuracy.ref_words(fig['references'], fig['cases'])}, {accuracy.pct(t['identical'], t['boxes'])} identical, "
                  f"{t['different']} different, {t['unfilled']} unfilled")
    for line in report["skipped"]:
        print(f"Set aside: {line}")
    if not args.no_samples:
        problems = accuracy.public_problems(args.public.read_text(encoding="utf-8"), args.report.read_text(encoding="utf-8"))
        for line in problems:
            print(f"PUBLIC PAGE: {line}")
        if problems:
            return 1
    if args.history:
        chosen = accuracy.chosen_set(report)
        audit = accuracy.audit_counts(args.clients)  # what the office changes (tools/fill_audit.py): its counts go into the history beside the figure, so a drift shows
        accuracy.append_history(accuracy.history_path(args.clients), accuracy.history_record(report, chosen, audit))
        if audit:
            print(f"The audit of what the office changes, last run {audit['forms_day']}: {audit['changed']} of {audit['boxes']:,} boxes changed, "
                  f"{audit['groups']} box(es) changed the same way on {audit_fill.THRESHOLD} or more cases.")
        print(f"Added to {accuracy.history_path(args.clients)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
