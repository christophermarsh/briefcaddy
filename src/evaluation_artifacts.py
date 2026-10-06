"""Own-case optional evaluation artifacts under an already authorized lifecycle.

No tools/evaluation imports, new legal authority, case-folder deletion, or
cross-case rewriting. Callers hold the installation gate/current legal approval.
"""
from __future__ import annotations

import shutil
from pathlib import Path

NAMESPACES = {"evaluation_authorizations": "evaluation-authorizations", "evaluation_candidates": "evaluation-candidates"}


def _targets(data_root, case):
    from portal.communication_consent import _safe
    if not isinstance(case, str) or case in {"", ".", ".."} or any(ch in case for ch in "/\\:") or any(ord(ch)<32 for ch in case):
        raise ValueError("Invalid own-case evaluation artifact basename.")
    root = _safe(Path(data_root).absolute())
    result = []
    for key, namespace in NAMESPACES.items():
        target = root / namespace / case
        _safe(target)
        if not target.resolve().is_relative_to((root / namespace).resolve()):
            raise ValueError("Evaluation cleanup escapes its own-case namespace.")
        if target.exists():
            if not target.is_dir():
                raise ValueError("The case's evaluation artifact folder is unavailable.")
            for child in target.rglob("*"):
                _safe(child)
        result.append((key, target))
    return result


def remove_case(data_root, case):
    """Preflight both namespaces before deleting only this case's artifact copies."""
    targets = _targets(data_root, case)
    removed = {key: 0 for key in NAMESPACES}
    for key, target in targets:
        if target.exists():
            removed[key] = sum(1 for child in target.rglob("*") if child.is_file())
            shutil.rmtree(target)
    return removed
