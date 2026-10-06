"""Local independent evaluation. Explicit corpus roots; fresh output artifacts.

validate / run / score / score-import / freeze-policy / compare / decision
Nothing in a manifest is executed as a command. No model is promoted.
"""
from __future__ import annotations

import argparse
import json
import sys
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from evaluation.corpus import load, validate, write_new, digest
from evaluation.runner import run, imported
from evaluation.scoring import score
from evaluation.releases import freeze_policy, compare, decision


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("validate", "run", "score", "score-import", "freeze-policy"):
        sub = commands.add_parser(action)
        sub.add_argument("--manifest", required=True)
        sub.add_argument("--corpus-root", required=True)
        if action != "validate":
            sub.add_argument("--out", required=True)
        if action == "run":
            sub.add_argument("--config", required=True)
        if action in ("score", "score-import"):
            sub.add_argument("--critical-inventory", required=True)
        if action == "score":
            sub.add_argument("--run-dir", required=True)
        if action == "score-import":
            sub.add_argument("--observations", required=True)
            sub.add_argument("--declared-config", required=True)
            sub.add_argument("--declared-provenance", required=True)
        if action == "freeze-policy":
            sub.add_argument("--thresholds", required=True)
            sub.add_argument("--owner", required=True)
    sub = commands.add_parser("compare")
    for option in ("incumbent-report", "challenger-report", "incumbent-run", "challenger-run", "policy", "out"):
        sub.add_argument("--" + option, required=True)
    sub = commands.add_parser("decision")
    for option in ("comparison", "owner", "reason", "rollback-target", "out"):
        sub.add_argument("--" + option, required=True)
    args = parser.parse_args(argv)
    exact_command = [sys.executable, str(Path(__file__).resolve()), *(sys.argv[1:] if argv is None else argv)]
    try:
        manifest = load(args.manifest) if hasattr(args, "manifest") else None
        if manifest is not None:
            validation = validate(manifest, args.corpus_root)
        if args.action == "validate":
            result = validation
        elif args.action == "run":
            observations, metadata = run(manifest, args.corpus_root, load(args.config), args.out, exact_command)
            result = {"kind": metadata["kind"], "observations_digest": digest(observations), "cases": len(observations["outcomes"])}
        elif args.action in ("score", "score-import"):
            if args.action == "score":
                observations = load(Path(args.run_dir) / "observations.json")
                metadata = load(Path(args.run_dir) / "run.json")
                if metadata.get("kind") != "local_pipeline_execution" or metadata.get("observations_digest") != digest(observations) or metadata.get("configuration_digest") != digest(metadata.get("configuration")) or metadata.get("configuration_digest") != observations.get("configuration_digest"):
                    raise ValueError("local run evidence/configuration mismatch")
            else:
                observations = load(args.observations)
                metadata = imported(observations, manifest, load(args.declared_config), load(args.declared_provenance), exact_command)
            report = score(manifest, observations, load(args.critical_inventory))
            report.update(provenance_kind=metadata["kind"], configuration_is_declared_only=metadata["kind"] == "imported_observations")
            if args.action == "score-import" and Path(str(args.out) + ".provenance.json").exists():
                raise FileExistsError("import provenance output must be new")
            write_new(args.out, report)
            result = {"report_digest": digest(report), "provenance_kind": metadata["kind"], "precision": report["extraction"]["proposal"]["precision"], "coverage": report["extraction"]["proposal"]["coverage"]}
            if args.action == "score-import":
                write_new(str(args.out) + ".provenance.json", metadata)
        elif args.action == "freeze-policy":
            policy = freeze_policy(manifest, load(args.thresholds), args.owner, exact_command)
            write_new(args.out, policy)
            result = {"policy_digest": digest(policy), "approved": False}
        elif args.action == "compare":
            comparison = compare(load(args.incumbent_report), load(args.challenger_report), load(args.incumbent_run), load(args.challenger_run), load(args.policy))
            write_new(args.out, comparison)
            result = {"comparison_digest": digest(comparison), "proposal": comparison["proposal"], "approved": False}
        else:
            draft = decision(load(args.comparison), args.owner, args.reason, args.rollback_target)
            write_new(args.out, draft)
            result = {"decision_digest": digest(draft), "state": "draft", "approved": False}
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError, subprocess.CalledProcessError) as exc:
        print("Evaluation refused: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
