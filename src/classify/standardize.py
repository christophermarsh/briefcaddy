"""Document-processing helper."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .classifier import Classification, classify_document


@dataclass
class RenamePlan:
    original_path: Path
    new_name: str
    doc_type: str
    confidence: float


def _assign_names(classifications: dict[Path, Classification]) -> list[RenamePlan]:
    """Document-processing helper."""
    by_doc_type: dict[str, list[Path]] = {}
    for path, classification in classifications.items():
        by_doc_type.setdefault(classification.doc_type, []).append(path)

    plans: list[RenamePlan] = []
    for doc_type, paths in by_doc_type.items():
        multiple = len(paths) > 1
        for index, path in enumerate(paths, start=1):
            classification = classifications[path]

            if doc_type == "unclassified":
                new_name = f"unclassified__{path.name}"
            elif classification.ambiguous_with:
                competing = "_".join(classification.ambiguous_with)
                new_name = f"{doc_type}__ambiguous_with_{competing}__{path.name}"
            elif multiple:
                new_name = f"{doc_type}_{index}.pdf"
            else:
                new_name = f"{doc_type}.pdf"

            plans.append(RenamePlan(path, new_name, doc_type, classification.confidence))

    plans.sort(key=lambda plan: str(plan.original_path))
    return plans


def plan_standard_names(folder: str | Path) -> list[RenamePlan]:
    """Document-processing helper."""
    folder = Path(folder)
    classifications = {path: classify_document(path) for path in sorted(folder.glob("*.pdf"))}
    return _assign_names(classifications)


def apply_standard_names(plans: list[RenamePlan], output_dir: str | Path, copy: bool = True) -> dict[str, str]:
    """Document-processing helper."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    renamed: dict[str, str] = {}

    for plan in plans:
        destination = output_dir / plan.new_name
        if plan.original_path.resolve() == destination.resolve():
            pass  # Supporting implementation.
        elif copy:
            shutil.copy2(plan.original_path, destination)
        else:
            plan.original_path.rename(destination)
        renamed[str(plan.original_path)] = str(destination)

    return renamed


def standardize_folder(
    folder: str | Path, output_dir: str | Path | None = None, copy: bool = True
) -> dict[str, str]:
    """Document-processing helper."""
    folder = Path(folder)
    destination = Path(output_dir) if output_dir is not None else folder
    plans = plan_standard_names(folder)
    return apply_standard_names(plans, destination, copy=copy)
