"""Weak workflow labels inferred from staff organization, awaiting adjudication.

These observations are not truth, training permission, independently adjudicated
evaluation references, or evidence authorizing reader thresholds/promotion.

  filed          -- the case was marked filed: every document's type stood
                    (still a workflow observation);
  final_packet   -- a final (non-draft) packet was built with these types;
  packet_move    -- a paralegal put a document into an exhibit. When that
                    exhibit holds one type, that's the label; when it holds
                    several (passport + visa), the label is "one of these".

A later label for the same document replaces earlier ones (superseded).
Only ids and types are stored.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import clock

SKIP = {"unclassified", "intake_questionnaire"}
LABELS_SCHEMA = """
create table if not exists labels (
  id integer primary key,
  at text not null,
  client text not null,
  doc text not null,
  task text not null default 'document_type',
  label text not null,          -- JSON list: one type, or "one of these" for a multi-type exhibit
  source text not null,         -- filed | final_packet | packet_move
  by text,
  superseded integer not null default 0
);
create index if not exists labels_doc on labels(client, doc, task);
"""


def _db(db_path: Path | None):
    from learning.store import DEFAULT_PATH, connect

    db = connect(db_path or DEFAULT_PATH)
    db.executescript(LABELS_SCHEMA)
    return db


def _write(db, client: str, doc: str, types: list[str], source: str, by: str | None) -> None:

    db.execute("update labels set superseded = 1 where client = ? and doc = ? and task = 'document_type' and superseded = 0", (client, doc))
    db.execute("insert into labels (at, client, doc, label, source, by) values (?, ?, ?, ?, ?, ?)",
               (clock.stamp(), client, doc, json.dumps(sorted(types)), source, by))


def record_packet_move(client_dir: Path, doc: str, exhibit: str | None, by: str, schema: dict[str, Any], db_path: Path | None = None) -> bool:
    """A paralegal placed a document in an exhibit (packet.choose)."""
    types = next((ex["types"] for ex in schema.get("exhibits", []) if ex["id"] == exhibit), None)
    if not types:
        return False  # "leave out" or "back to its usual place" says nothing about what it is
    db = _db(db_path)
    try:
        _write(db, client_dir.name, doc, types, "packet_move", by)
        db.commit()
    finally:
        db.close()
    return True


def record_confirmed(client_dir: Path, source: str, by: str, db_path: Path | None = None) -> int:
    """Every document's type stood (filed, or a final packet): one label each.
    A document a paralegal moved keeps its packet-move label."""
    meta = json.loads((client_dir / "meta.json").read_text(encoding="utf-8")) if (client_dir / "meta.json").exists() else {}
    moved = json.loads((client_dir / "packet_choices.json").read_text(encoding="utf-8")).get("files", {}) \
        if (client_dir / "packet_choices.json").exists() else {}
    db = _db(db_path)
    n = 0
    try:
        for doc, rules in (meta.get("classifications") or {}).items():
            if rules in SKIP or doc in moved:
                continue
            _write(db, client_dir.name, doc, [rules], source, by)
            n += 1
        db.commit()
    finally:
        db.close()
    return n


def current(db) -> dict[tuple[str, str], list[str]]:
    """Latest workflow label types; matching is descriptive agreement, not accuracy."""
    try:
        rows = db.execute("select client, doc, label from labels where task = 'document_type' and superseded = 0").fetchall()
    except Exception:  # noqa: BLE001 -- no labels table yet
        return {}
    return {(r["client"], r["doc"]): json.loads(r["label"]) for r in rows}
