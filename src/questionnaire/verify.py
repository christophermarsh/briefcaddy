"""Application helper with evidence-bound inputs."""

from __future__ import annotations

from typing import Any

SCHEMA = {
    "type": "object",
    "properties": {"marked": {"type": "array", "items": {"type": "integer"}}},
    "required": ["marked"],
}


def prompt(printed_labels: list[str], multi: bool) -> str:
    listed = "  ".join(f'{i}. "{label}"' for i, label in enumerate(printed_labels, 1))
    return (
        "This image is one question from a scanned immigration intake questionnaire. "
        f"Its printed answer options are, in order: {listed}. "
        "Each option has a small printed box \"(   )\" immediately before its label. "
        "Which options have a hand-drawn mark (an X, a check, a scribble, or a filled-in box) INSIDE their own box? "
        "Handwritten words written on an answer line are explanations, NOT marks -- even when they run into a box. "
        + ("Several options may be marked. " if multi else "Normally only one option is marked. ")
        + "Return the numbers of the marked options, or an empty list if no box is marked."
    )


def vision_marks(image: Any, box: list[int], printed_labels: list[str], multi: bool, model_call) -> list[int] | None:
    """Application helper with evidence-bound inputs."""
    from PIL import Image

    x0, y0, x1, y1 = box
    crop = image.crop((x0, y0, x1, y1))
    crop = crop.resize((int(crop.width * 1.5), int(crop.height * 1.5)), Image.LANCZOS)
    try:
        answer = model_call(prompt(printed_labels, multi), crop, SCHEMA)
        marked = sorted({int(n) for n in answer.get("marked", [])})
    except Exception:  # noqa: BLE001
        return None
    if any(n < 1 or n > len(printed_labels) for n in marked):
        return None
    return marked
