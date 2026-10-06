"""Runs the whole pipeline for one or more client folders and prepares
them for the review app.

    python src/process_clients.py                       # every folder in clients/
    python src/process_clients.py maria_eduarda client_2  # just these
    python src/process_clients.py --policies ...        # + SIJS policy profile (unapproved)
    python src/process_clients.py --no-vision ...       # skip handwriting (no Ollama)

Reads clients/<id>/source/*.pdf (put reference/past I-485s elsewhere, e.g.
raw/ -- they must never be inputs), writes data/clients/<id>/: the review
bundle (src/review/state.py), the filled I-485 and the flag report. Any
decisions already made in the review app are re-applied, so re-running the
pipeline doesn't lose review work.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from batch import load_firm_profile, process_client_folder  # noqa: E402
from fill import load_field_map  # noqa: E402
from questionnaire.reader import load_questionnaire_maps  # noqa: E402
from review.state import refill, save_bundle  # noqa: E402
from rules import ALL_RULES  # noqa: E402
from rules.policy import load_policy_profile  # noqa: E402
import schema_path

REPO = Path(__file__).resolve().parent.parent


def load_context(policies: bool = False, use_vision: bool = True) -> dict:
    """What every client's run needs, loaded once (per process)."""
    schemas = schema_path.ROOT
    return {
        "field_map": load_field_map(schema_path.path("field_map", "i485", schemas)),
        "template": schema_path.path("template", "i485", schemas),
        "maps": load_questionnaire_maps(schemas),  # every questionnaire language the firm has a map for
        "policies": load_policy_profile(schema_path.path("law", "policy_sijs", schemas)) if policies else None,
        "firm_profile": load_firm_profile(schema_path.path("firm", "firm_profile", schemas)),
        "use_vision": use_vision,
    }


def _index(out: Path) -> None:
    """The case's documents into the firm-wide search index (src/index.py); the nightly run catches up on anything missed here."""
    try:
        import index

        index.rebuild(out)
    except Exception as exc:  # noqa: BLE001 -- search is a convenience: it must never cost a client its processing
        print(f"{out.name}: the search index couldn't be updated ({type(exc).__name__}); the nightly run tries again.", file=sys.stderr)


def _query(out: Path) -> None:
    """The case's rows in the query layer (src/query.py); the nightly run catches up on anything missed here."""
    try:
        import query

        query.rebuild(out)
    except Exception as exc:  # noqa: BLE001 -- the query layer is a copy: it must never cost a client its processing
        print(f"{out.name}: the query layer couldn't be updated ({type(exc).__name__}); the nightly run tries again.", file=sys.stderr)


def process_one(name: str, source: Path, out: Path, ctx: dict) -> dict:
    """One client folder through the pipeline into its review bundle;
    decisions already made are re-applied by refill()."""
    started = time.time()
    result = process_client_folder(
        name, source, rules=ALL_RULES, policies=ctx["policies"],
        firm_profile=ctx["firm_profile"],
        questionnaire_maps=ctx["maps"],
        use_vision=ctx["use_vision"],
        case_dir=out,
    )
    save_bundle(result, out, source)
    summary = refill(out, ctx["field_map"], ctx["template"])
    _index(out)
    _query(out)
    return {"client": name, "counts": summary["counts"], "seconds": round(time.time() - started, 1),
            "errors": result.errors, "documents": len(result.classifications)}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clients", nargs="*", help="client folder names (default: all)")
    ap.add_argument("--clients-root", type=Path, default=REPO / "clients")
    ap.add_argument("--out", type=Path, default=REPO / "data" / "clients")
    ap.add_argument("--policies", action="store_true", help="apply schemas/law/policy_sijs.json (NOT attorney-approved yet)")
    ap.add_argument("--no-vision", action="store_true", help="no vision model: skip handwritten text answers and the second checkbox reader")
    args = ap.parse_args(argv)

    ctx = load_context(args.policies, not args.no_vision)
    names = args.clients or sorted(p.name for p in args.clients_root.iterdir() if (p / "source").is_dir())
    for name in names:
        r = process_one(name, args.clients_root / name / "source", args.out / name, ctx)
        c = r["counts"]
        errors = f"  errors: {r['errors']}" if r["errors"] else ""
        print(f"{name}: {c['blocking']} blocking, {c['review']} review  ({r['seconds']:.0f}s){errors}")


if __name__ == "__main__":
    main()
