"""A small document-type classifier trained on this machine: character
patterns (3-5 letters at a time, so it copes with "EMPL0YMENT" and
"SOCIALCUITY") weighed by a logistic regression. Trained in about a minute on
invented documents (learning/synthetic.py) and public specimens of the real
ones (schemas/registers/training_sources.json, tools/fetch_specimens.py); answers in a few milliseconds,
without a GPU -- a second opinion next to the rules and Nimble, in shadow
mode until the report says otherwise (docs/learning.md).

    python src/learning/textcat.py train     # -> data/models/document_type.pkl (+ .json card)

The model file is the firm's own (data/ is never committed) and is only ever
loaded from there: a pickle runs code when loaded, so never load one sent
from elsewhere.
"""

from __future__ import annotations

import json
import pickle
import re
import time
from pathlib import Path
from typing import Any

from learning.synthetic import VERSION as DATA_VERSION, WRITERS, damage, generate, readable, strip_accents
from learning.store import REPO
import clock

DEFAULT_PATH = REPO / "data" / "models" / "document_type.pkl"
SPECIMENS = REPO / "data" / "training" / "specimens.json"  # tools/fetch_specimens.py
NAME = "textcat"


def prepare(text: str) -> str:
    """Accents off, lower case, one space -- the same for training and use."""
    return re.sub(r"\s+", " ", strip_accents(text).lower()).strip()


def specimens(path: Path = SPECIMENS) -> list[tuple[str, str, str]]:
    """[(text, type, source id)]: public specimen pages (schemas/registers/training_sources.json)
    as the pipeline's OCR read them, minus those it couldn't read."""
    if not Path(path).exists():
        return []
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return [(r["text"], r["type"], r["id"]) for r in rows if r.get("used") and r["type"] in WRITERS and readable(r["text"])]


def specimen_copies(pages: list[tuple[str, str, str]], n_per_type: int, seed: int = 0) -> list[tuple[str, str]]:
    """Each specimen page once as read, plus damaged copies (like the invented
    ones) -- at most a quarter as many as a type's invented documents."""
    import random
    from collections import Counter

    r = random.Random(seed)
    per_type = Counter(k for _, k, _ in pages)
    out = []
    for text, kind, _ in pages:
        # few copies of each: many copies of four passport pages teach the
        # specimens' made-up holders, not what a passport looks like
        copies = max(1, min(20, (n_per_type // 4) // per_type[kind]))
        lines = text.splitlines()
        out.append((text, kind))
        out.extend((damage(lines, random.Random(r.random())), kind) for _ in range(copies))
    return out


def build(n_per_type: int = 600, seed: int = 0, c: float = 4.0, use_specimens: bool = True, specimens_path: Path = SPECIMENS,
          pages: list[tuple[str, str, str]] | None = None):
    """pages: the specimen pages to train on (default: all that are readable)."""
    docs = generate(n_per_type, seed)
    if use_specimens:
        docs += specimen_copies(specimens(specimens_path) if pages is None else pages, n_per_type, seed)
    return fit(docs, c)


def fit(docs: list[tuple[str, str]], c: float = 4.0):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline

    pipe = make_pipeline(TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), preprocessor=prepare, sublinear_tf=True, min_df=3,
                                         max_features=300_000),
                         LogisticRegression(C=c, max_iter=3000))
    pipe.fit([t for t, _ in docs], [k for _, k in docs])
    return pipe


def train(path: Path = DEFAULT_PATH, n_per_type: int = 600, seed: int = 0, c: float = 4.0, use_specimens: bool = True,
          specimens_path: Path = SPECIMENS) -> dict[str, Any]:
    import sklearn
    from collections import Counter

    started = time.time()
    pages = specimens(specimens_path) if use_specimens else []
    pipe = build(n_per_type, seed, c, use_specimens, specimens_path)
    held_out = generate(150, seed + 1)  # fresh invented documents: is it learning types, not memorising?
    right = sum(p == k for p, (_, k) in zip(pipe.predict([t for t, _ in held_out]), held_out))
    card = {"name": NAME, "version": f"{DATA_VERSION}{'+sp' + str(len(pages)) if pages else ''}-{n_per_type}-{seed}-c{c:g}", "data": DATA_VERSION,
            "types": sorted(WRITERS),
            "trained_on": f"{n_per_type} invented documents per type (learning/synthetic.py)"
                          + (f" + {len(pages)} public specimen pages from {len({p[2] for p in pages})} sources (schemas/registers/training_sources.json)" if pages else "")
                          + " -- no client documents",
            "specimens_by_type": dict(Counter(k for _, k, _ in pages)),
            "synthetic_held_out": f"{right}/{len(held_out)}", "seconds": round(time.time() - started, 1), "sklearn": sklearn.__version__,
            "at": clock.stamp("seconds")}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump({"pipeline": pipe, "card": card}, fh)
    path.with_suffix(".json").write_text(json.dumps(card, indent=1))
    return card


_LOADED: dict[Path, dict[str, Any]] = {}


def load(path: Path = DEFAULT_PATH) -> dict[str, Any]:
    path = Path(path)
    if path not in _LOADED:
        if not path.exists():
            raise FileNotFoundError(f"{path} -- not trained yet: python src/learning/textcat.py train")
        with open(path, "rb") as fh:
            _LOADED[path] = pickle.load(fh)  # noqa: S301 -- the firm's own file under data/models (see above)
    return _LOADED[path]


def document_type(text: str, path: Path = DEFAULT_PATH) -> tuple[str, float, float]:
    """(type, probability of that type, seconds) -- same shape as decision.document_type."""
    started = time.time()
    pipe = load(path)["pipeline"]
    probs = pipe.predict_proba([text])[0]
    best = int(probs.argmax())
    return str(pipe.classes_[best]), float(probs[best]), time.time() - started


if __name__ == "__main__":
    import sys

    if sys.argv[1:2] == ["train"]:
        print(json.dumps(train(), indent=1))
    else:
        print(document_type(sys.stdin.read()))
