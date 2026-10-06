"""Explicit fictional typed answers, with no claim of scanned source proof."""
from extract.base import ExtractedField
from review.state import save_bundle
from synthetic_documents import process_retained_documents

SHORT = "I worked at the fictional Example Shop."


def typed_part14(world, app, tmp_path):
    source = tmp_path / "typed-only-source"
    answers = []
    for number, text in [(1, SHORT), (2, "")]:
        for suffix, value in {"page": "14", "part": "9", "item": "12", "text": text}.items():
            answers.append(ExtractedField(f"applicant.p14_block{number}_{suffix}", value, value, 1.0))
    result = process_retained_documents("case-ana", source, [], answers=answers)
    save_bundle(result, world / "case-ana", source)
    app.roster.touch("case-ana")
    return world / "case-ana"
