"""The software's version: shown on the Settings page and Keeping current, so a firm (or our support) knows which release
it is running. Raise it with every release we ship; the same release goes to every hosted firm and every installation.

What each release changed is docs/releases.md (shipped with the user guide): a heading per version, "## 2026.10.4 (2026-10-02)",
then one plain line per change, "- ...". The Settings page shows them under the version number, read-only."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

VERSION = "2026.10.10"
RELEASED = "2026-10-04"
RELEASE_NOTES = Path(__file__).resolve().parents[1] / "docs" / "releases.md"
_HEADING = re.compile(r"^##\s+(\d{4}\.\d{1,2}\.\d+)\s*\((\d{4}-\d{2}-\d{2})\)\s*$")


def releases(path: Path = RELEASE_NOTES, limit: int = 6) -> list[dict[str, Any]]:
    """[{"version", "released" (YYYY-MM-DD), "lines"}], newest first as the file lists them, at most limit. A missing or unreadable
    file gives none: the version number still shows, the notes do not."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            out.append({"version": m.group(1), "released": m.group(2), "lines": []})
        elif out and line.startswith("- "):
            out[-1]["lines"].append(line[2:].strip())
        elif out and line.startswith("  ") and out[-1]["lines"]:  # a line wrapped under its bullet
            out[-1]["lines"][-1] += " " + line.strip()
    return out[:limit]
