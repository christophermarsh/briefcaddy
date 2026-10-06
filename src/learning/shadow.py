"""Shadow mode: while a client is processed, local models -- the decision
model (Nimble) and the classifier trained here (textcat) -- answer the same
question the rules answered, and both are recorded in the learning
store. Nothing it says changes the pipeline's result.

Safe by construction:
  - off unless schemas/firm/learning.json enables it (and never in tests:
    I485_SHADOW=0);
  - a model that's missing or slow costs one error row, never the client's
    processing;
  - only ids, answers and probabilities are stored, never document text.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
import schema_path

SETTINGS = schema_path.path("firm", "learning")


def settings(task: str, path: Path = SETTINGS) -> dict[str, Any] | None:
    if os.environ.get("I485_SHADOW", "1") == "0" or not path.exists():
        return None
    cfg = json.loads(path.read_text(encoding="utf-8")).get("shadow", {}).get(task)
    return cfg if cfg and cfg.get("enabled") else None


def wording_version(types: dict[str, str]) -> str:
    """Which wording of the question was used: tuning it is tracked, not guessed."""
    return hashlib.sha1(json.dumps(types, sort_keys=True).encode()).hexdigest()[:8]


def _document_type_models(settings_path: Path, ask) -> list[tuple[str, str, Any]]:
    """[(model name, version of what it was given, ask)] -- each enabled model:
    the decision model (Nimble) and the classifier trained here (textcat)."""
    from learning.decision import DOCUMENT_TYPES, document_type

    models = []
    cfg = settings("document_type", settings_path)
    if cfg:
        models.append((cfg["model"], wording_version(DOCUMENT_TYPES),
                       ask or (lambda text: document_type(text, cfg["model"], cfg.get("max_chars", 6000)))))
    local = settings("document_type_textcat", settings_path)
    if local and ask is None:
        from learning import textcat

        path = Path(local.get("path") or textcat.DEFAULT_PATH)
        path = path if path.is_absolute() else schema_path.ROOT.parent / path
        try:
            version = textcat.load(path)["card"]["version"]
        except FileNotFoundError:
            version = "untrained"
        models.append((textcat.NAME, version, lambda text: textcat.document_type(text, path)))
    return models


def observe_document_types(client_id: str, documents: list[tuple[str, str]], classifications: dict[str, Any],
                           db_path: Path | None = None, settings_path: Path = SETTINGS, ask=None) -> int:
    """Records each shadow model's document type for each document; returns how
    many answers were recorded. classifications: doc_id -> the rules' Classification."""
    models = _document_type_models(settings_path, ask)
    if not models:
        return 0
    from learning.decision import DecisionModelError
    from learning.store import DEFAULT_PATH, connect, record_run

    db = connect(db_path or DEFAULT_PATH)
    n = 0
    try:
        for model, wording, ask_model in models:
            for doc_id, text in documents:
                rules = getattr(classifications.get(doc_id), "doc_type", None)
                if rules == "intake_questionnaire" or not text.strip():
                    continue  # the questionnaire has its own readers; an empty page tells the model nothing
                try:
                    answer, prob, secs = ask_model(text)
                    record_run(db, client=client_id, doc=doc_id, task="document_type", model=model, wording=wording,
                               rules=rules, answer=answer, probability=round(prob, 4), ms=int(secs * 1000))
                except (DecisionModelError, FileNotFoundError) as exc:
                    record_run(db, client=client_id, doc=doc_id, task="document_type", model=model, wording=wording,
                               rules=rules, error=str(exc)[:300])
                    break  # the model isn't there: one error row, then the client carries on without it
                n += 1
    finally:
        db.close()
    return n


def backfill(data_root: Path, db_path: Path | None = None) -> dict[str, int]:
    """Shadow answers for clients processed before shadow mode existed: the
    same text the pipeline read (re-extracted), the rules' answer from meta.json."""
    from types import SimpleNamespace

    from classify import extract_pages

    counts = {}
    for bundle in sorted(p for p in data_root.iterdir() if (p / "meta.json").exists()):
        meta = json.loads((bundle / "meta.json").read_text(encoding="utf-8"))
        folder, documents, classes = Path(meta["source_folder"]), [], {}
        for doc, rules in meta.get("classifications", {}).items():
            name, _, part = doc.partition("#")
            if rules == "intake_questionnaire" or not (folder / name).exists():
                continue
            pages = extract_pages(folder / name)
            if part:
                a, _, b = part[1:].partition("-")
                pages = pages[int(a) - 1:int(b or a)]
            documents.append((doc, "\n".join(pages)))
            classes[doc] = SimpleNamespace(doc_type=rules)
        counts[bundle.name] = observe_document_types(bundle.name, documents, classes, db_path)
    return counts


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "data" / "clients"
    print(backfill(root))
