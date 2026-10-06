"""Refresh the fictional preview without a store, notifications or live cases.

Run from the repository root with the project Python environment. The fixture
and demo answers are invented; this tool accepts no client IDs or case paths.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from portal.demo import answers
from portal.questionnaire_pdf import render


def main():
    overrides = json.loads((ROOT / "tests/fixtures/questionnaire_preview_fictional.json").read_text(encoding="utf-8"))
    profile = {"id": "fictional-preview-only", "name": "Fictional Ana Clara Exemplo Souza", "status": "submitted",
               "submitted_at": "2026-10-05T16:00:00+00:00", "language": "en",
               "attestation": {"language": "en", "typed_name": "Fictional Ana Clara Exemplo Souza"}}
    target = ROOT / "output/pdf/intake-questionnaire-preview.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(render(profile, answers() | overrides))
    print(target.relative_to(ROOT))


if __name__ == "__main__":
    main()
