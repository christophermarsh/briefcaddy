"""Bring a firm's Clio cases over from a Clio export, without a connection: one client folder and one portal client per matter, with their documents.

    python tools/import_clio.py --contacts contacts.csv --matters matters.csv --documents <folder or .zip> \\
        --out <clients folder> --portal <portal data folder> [--dry-run] [--merge] [--language pt|es|en|ht]

For a firm that is switching to this product and does not connect to Clio (the connection is src/connectors/clio.py: use it when the firm will keep
Clio). What it reads is the firm's own export, nothing is fetched from Clio:
  --contacts   Clio's Contacts export (a CSV)
  --matters    Clio's Matters export (a CSV)
  --documents  the matters' files, one folder per matter (named by the matter's id, number or description), or a .zip of that
  --mapping    which column holds what (default schemas/firm/import_clio_columns.json). Clio's own help pages for its export refused the request when this was
               written (HTTP 403, 10/03/2026), so no column list was read: every name in that file is a guess from Clio's API objects until the firm's own
               export has been compared with it, and the report says which headings were found and which were not.

It is the same engine as tools/import_docketwise.py (read it for what is made, what a second run does and what a dry run prints): the same safety (a dry
run writes nothing, a client that exists is never overwritten, nothing is sent to anyone), the same conflict search before a case exists, and the same
rule for a protected case: a matter whose practice area names a VAWA, T or U case (8 U.S.C. 1367) or an asylum case (8 CFR 208.6) is restricted from
the first time it comes in, before its portal client exists, and is never invited; the report names each one. What differs is the system it reads:
  - a case's folder ends in -cl<the matter's id> (the name a connection to Clio gives the same matter, so connecting later finds these cases), or in a
    short code made from the matter number when the export has no id (then a later connection would make a second case: prefer an export with the id);
  - a matter whose status is closed is left out unless --include-archived;
  - the matter's practice area is its type.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
import schema_path  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))

import import_docketwise as engine  # noqa: E402

CLIO = {"name": "Clio", "key": "clio", "prefix": "cl", "state": "clio_import.json", "mapping": schema_path.path("firm", "import_clio_columns"),
        "tool": "tools/import_clio.py", "archived_word": "closed", "name_from_matter": True,
        "export_how": "the Export button at the bottom of Clio's {what} page",
        "unpublished": "Clio's help pages for its export refused the request when this was written, so the headings its export uses are not known",
        "consent_note": "Clio holds no consent to be messaged, and nothing read says its export carries any",
        "guess_note": "Clio's help pages for its export could not be read, so these names come from Clio's API and are guesses until compared with the firm's own export"}
engine.SOURCES["clio"] = CLIO

ImportProblem = engine.ImportProblem
Run, Outcome = engine.Run, engine.Outcome


def run_import(*args, **kwargs) -> engine.Run:
    """engine.run_import, reading a Clio export (the same arguments; the column mapping defaults to schemas/firm/import_clio_columns.json)."""
    before = engine.use(CLIO)
    try:
        if len(args) < 6:  # engine.run_import's sixth argument is the mapping
            kwargs.setdefault("mapping_path", CLIO["mapping"])
        return engine.run_import(*args, **kwargs)
    finally:
        engine.use(before)


def report(run: engine.Run) -> str:
    before = engine.use(CLIO)
    try:
        return engine.report(run)
    finally:
        engine.use(before)


def main(argv: list[str] | None = None) -> int:
    before = engine.use(CLIO)
    try:
        return engine.main(argv)
    finally:
        engine.use(before)


if __name__ == "__main__":
    sys.exit(main())
