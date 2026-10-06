"""Every question the readers look for must exist on the firm's CURRENT
questionnaire template. The template changed wording once already ("Seu
ultimo trabalho fora" -> "Seu ultimo trabalho/estudo fora"), and the old
anchor silently stopped finding the foreign job on every new form."""

from __future__ import annotations

import json
import re
from pathlib import Path
import schema_path

ROOT = Path(__file__).resolve().parent.parent
LINES = [line.split("\t", 1)[1] for line in (ROOT / "tests/fixtures/questionnaire_template_pt.txt").read_text(encoding="utf-8").splitlines()
         if "\t" in line and not line.startswith("#")]


def test_every_text_field_anchor_is_on_the_current_template():
    fields = json.loads(schema_path.path("paper_map", "text_map").read_text(encoding="utf-8"))["fields"]
    missing = [f["id"] for f in fields if sum(1 for line in LINES if re.search(f["anchor"], line)) < f.get("occurrence", 1)]
    assert missing == []


def test_every_checkbox_question_anchor_is_on_the_current_template():
    questions = json.loads(schema_path.path("paper_map", "map").read_text(encoding="utf-8"))["questions"]
    missing = [q["id"] for q in questions if q.get("anchor") and not any(re.search(q["anchor"], line) for line in LINES)]
    assert missing == []
