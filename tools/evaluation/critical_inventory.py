"""Materialize the EV3 inventory for EV9's exact-key scorer.

Engineering acceptance is supplied by the independent reviewer. It is not a
reader release approval, an attorney field decision, or a model-asset pin.
"""
from __future__ import annotations

import re
from .corpus import digest


def materialize(source, manifest, observations, *, accepted_by, acceptance_evidence):
    if (source.get("version") != 1 or source.get("unknown_key_policy") != "require_source_review"
            or not isinstance(accepted_by, str) or not accepted_by.strip() or not acceptance_evidence):
        raise ValueError("Versioned EV3 inventory and engineering acceptance evidence required")
    runs = observations if isinstance(observations, list) else [observations]
    if not runs or any(not isinstance(run, dict) or not isinstance(run.get("facts"), list) for run in runs):
        raise ValueError("One or more observation runs required; comparisons share their union inventory")
    candidates = {f["key"] for case in manifest["cases"] for f in case["expected"]}
    candidates |= {f["key"] for run in runs for f in run["facts"]}
    def matches(key):
        if key in source["keys"] or any(re.fullmatch(p, key) for p in source["patterns"]):
            return True
        return not (key in source["reference_only_keys"] or any(re.fullmatch(p, key) for p in source["reference_only_patterns"]))
    return {"version": 1, "state": "accepted", "keys": sorted(k for k in candidates if matches(k)),
            "inventory_source_digest": digest(source), "inventory_id": source["inventory_id"],
            "accepted_by": accepted_by.strip(), "acceptance_evidence": acceptance_evidence,
            "materialized_corpus_digest": digest(manifest), "materialized_observation_digests": sorted({digest(run) for run in runs}),
            "unknown_key_policy": source["unknown_key_policy"], "model_release_approval": False}


def main():
    import argparse
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "manifest", "output", "accepted-by", "acceptance-evidence"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--observations", required=True, nargs="+", help="All runs in one comparison; reuse the resulting exact artifact")
    args = parser.parse_args()
    read = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
    result = materialize(read(args.source), read(args.manifest), [read(p) for p in args.observations],
                         accepted_by=args.accepted_by, acceptance_evidence=args.acceptance_evidence)
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
