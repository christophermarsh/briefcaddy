"""Stage 8 -- the flag report (docs/ARCHITECTURE.md section 8). Per
GRAPH_MODEL.md, this stage adds no information validate.py's walk doesn't
already have; it only groups and renders Flags for a paralegal/attorney,
ranked the way the review queue should actually work:

1. Blocking -- required field with no source at all; case can't be filed
   until a paralegal calls the client.
2. Review -- Tier 2 derived answers and Tier 3 questionnaire-sourced
   answers; not blocking, but needs a human sign-off.
3. Informational -- Tier 1 conflicts resolved automatically; visible for
   audit, not action.
"""

from __future__ import annotations

from .validate import LEVEL_ORDER, Flag

LEVELS = ("blocking", "review", "informational")


def group_flags(flags: list[Flag]) -> dict[str, list[Flag]]:
    grouped: dict[str, list[Flag]] = {level: [] for level in LEVELS}
    for flag in sorted(flags, key=lambda f: LEVEL_ORDER[f.level]):
        grouped[flag.level].append(flag)
    return grouped


def render_report(client_id: str, flags: list[Flag]) -> str:
    grouped = group_flags(flags)
    lines = [f"Flag report for {client_id}", "=" * (len(client_id) + 16)]
    for level in LEVELS:
        entries = grouped[level]
        lines.append("")
        lines.append(f"{level.upper()} ({len(entries)})")
        if not entries:
            lines.append("  (none)")
        for entry in entries:
            lines.append(f"  - {entry.message}")
    return "\n".join(lines)
