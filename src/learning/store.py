"""The learning store: one SQLite file on the firm's machine
(data/learning.db; back it up by copying it). Started with what shadow mode
needs; the decisions, learned items and spot checks of docs/learning.md
join it as they are built.

Holds no document text -- only ids, answers and probabilities -- so the
store can be studied without re-reading client files.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import clock

REPO = Path(__file__).resolve().parents[2]
DEFAULT_PATH = REPO / "data" / "learning.db"

SCHEMA = """
create table if not exists model_runs (
  id integer primary key,
  at text not null,
  client text not null,
  doc text not null,
  task text not null,            -- e.g. 'document_type'
  model text not null,           -- e.g. 'nimble:9b-q4_K_M'
  wording text,                  -- version of the question's wording (decision.DOCUMENT_TYPES hash)
  rules text,                    -- what the pipeline's own rules said
  answer text,                   -- what the model said
  probability real,
  ms integer,
  error text,
  mode text not null default 'shadow'   -- 'shadow': recorded, never acted on
);
create index if not exists model_runs_task on model_runs(task, model);
create index if not exists model_runs_doc on model_runs(client, doc);
"""


def connect(path: str | Path = DEFAULT_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("pragma journal_mode=wal")  # the overnight run and the review app can both use it
    db.executescript(SCHEMA)
    return db


def record_run(db: sqlite3.Connection, **row: Any) -> None:
    row = {"at": clock.stamp(), "mode": "shadow"} | row
    cols = ", ".join(row)
    db.execute(f"insert into model_runs ({cols}) values ({', '.join('?' for _ in row)})", list(row.values()))
    db.commit()
