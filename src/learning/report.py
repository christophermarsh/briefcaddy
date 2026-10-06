"""How a shadow model is doing -- the numbers the attorney looks at before
anything goes live.

Two references. Against the rules' own answer (always there), it counts, per
model and question wording -- agree / rescued / unsure / disagree, below.
Against weak workflow labels (src/learning/labels.py), it measures descriptive
agreement. This is not held-out accuracy or a threshold/release recommendation.
An approved held-out cohort must use tools/evaluate_corpus.py and its independent
references, frozen policy, comparison, named review and rollback gates.


  agree        -- model and rules said the same
  rescued      -- the rules couldn't place it; the model was sure (>= threshold)
  unsure       -- the rules couldn't place it; the model wasn't sure (stays with a person)
  disagree     -- both were sure and differ (worth a person's look: one of them is wrong)

    python src/learning/report.py          # from the project folder
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from learning.store import DEFAULT_PATH, connect


def thresholds() -> dict[str, float]:
    """Each shadow model's own confidence threshold (schemas/firm/learning.json):
    probabilities are not comparable between models -- Nimble's run high, the
    classifier's lower."""
    from learning.shadow import SETTINGS

    try:
        cfg = json.loads(SETTINGS.read_text(encoding="utf-8")).get("shadow", {})
    except (OSError, ValueError):
        return {}
    out = {}
    for task, c in cfg.items():
        if task.startswith("document_type") and "threshold" in c:
            out["textcat" if task.endswith("_textcat") else c.get("model", "")] = float(c["threshold"])
    return out


def document_type_report(db_path: Path = DEFAULT_PATH, threshold: float | None = None) -> dict[str, Any]:
    """threshold: one for every model, or None for each model's own (thresholds())."""
    own = thresholds() if threshold is None else {}
    if not Path(db_path).exists():
        return _empty_report()
    db = connect(db_path)
    try:
        rows = db.execute("""select model, wording, client, doc, rules, answer, probability, ms, error from model_runs
                             where task = 'document_type' and id in (select max(id) from model_runs where task = 'document_type' group by client, doc, model)
                          """).fetchall()
        from learning.labels import current

        workflow_labels = current(db)
    except sqlite3.Error:
        return _empty_report()
    finally:
        db.close()
    models: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        m = models.setdefault((r["model"], r["wording"] or ""), {"model": r["model"], "wording": r["wording"], "documents": 0, "errors": 0,
                                                                  "agree": 0, "rescued": 0, "unsure": 0, "disagree": 0, "ms": [], "examples": [],
                                                                  "labelled": 0, "model_right": 0, "rules_right": 0, "_right_p": [], "_wrong_p": []})
        m["documents"] += 1
        if r["error"]:
            m["errors"] += 1
            continue
        m["ms"].append(r["ms"] or 0)
        allowed = workflow_labels.get((r["client"], r["doc"]))
        if allowed:
            m["labelled"] += 1
            m["rules_right"] += r["rules"] in allowed
            if r["answer"] in allowed:
                m["model_right"] += 1
                m["_right_p"].append(r["probability"] or 0)
            elif r["answer"] != "other":  # "other" is an honest "don't know", not a wrong answer
                m["_wrong_p"].append(r["probability"] or 0)
        sure = (r["probability"] or 0) >= (threshold if threshold is not None else own.get(r["model"], 0.9)) and r["answer"] != "other"
        if r["rules"] in (None, "unclassified"):
            kind = "rescued" if sure else "unsure"
        elif r["answer"] == r["rules"]:
            kind = "agree"
        else:
            kind = "disagree" if sure else "agree"  # an unsure dissent changes nothing
        m[kind] += 1
        if kind in ("rescued", "disagree") and len(m["examples"]) < 20:
            m["examples"].append({"client": r["client"], "doc": r["doc"], "rules": r["rules"], "model": r["answer"], "p": r["probability"]})
    out = []
    for m in models.values():
        ms = sorted(m.pop("ms"))
        judged = m["agree"] + m["disagree"]
        m.pop("_right_p"); m.pop("_wrong_p")
        out.append(m | {"threshold": threshold if threshold is not None else own.get(m["model"], 0.9), "median_ms": ms[len(ms) // 2] if ms else None, "agreement": round(m["agree"] / judged, 3) if judged else None,
                        "suggested_threshold": None, "label_strength": "workflow_observation",
                        "independently_adjudicated": False, "threshold_promotion_allowed": False,
                        "model_workflow_agreement": m["model_right"] / m["labelled"] if m["labelled"] else None,
                        "rules_workflow_agreement": m["rules_right"] / m["labelled"] if m["labelled"] else None})
    return _empty_report() | {"runs": len(rows), "threshold": threshold, "models": out}


def _empty_report():
    return {"runs": 0, "models": [], "label_strength": "workflow_observation",
            "independently_adjudicated": False, "threshold_promotion_allowed": False,
            "release_gate": "Requires independently adjudicated held-out tools/evaluate_corpus.py evidence, frozen policy, named review and rollback; workflow agreement cannot authorize promotion."}


MIN_LABELS = 20


def suggest_threshold(right_p: list[float], wrong_p: list[float]) -> dict[str, Any] | None:
    """Exploratory sample statistic only; never approval or a release gate.

    The lowest confidence above which the model has never been wrong on
    labelled documents -- and how many it would then place. None until there
    are MIN_LABELS labelled answers: a threshold from a handful is a guess."""
    if len(right_p) + len(wrong_p) < MIN_LABELS:
        return None
    cut = max(0.5, (int(max(wrong_p) * 100) + 1) / 100) if wrong_p else 0.5
    if cut > 0.99:  # wrong even when 99% sure: no threshold makes it safe -- tune the wording first
        return {"threshold": None, "of": len(right_p) + len(wrong_p), "wrong_answers_seen": len(wrong_p)}
    return {"threshold": cut, "would_place": sum(p >= cut for p in right_p), "of": len(right_p) + len(wrong_p),
            "wrong_answers_seen": len(wrong_p)}


def main() -> None:
    print(json.dumps(document_type_report(), indent=1))


if __name__ == "__main__":
    main()
