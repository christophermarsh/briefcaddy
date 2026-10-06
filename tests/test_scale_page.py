"""docs/scale.md is written by tools/measure_pages.py from measurements: the table, the budgets, the before and after, the overnight run."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import measure_pages  # noqa: E402


def test_the_scale_page_is_written_from_the_measurements_and_marks_a_page_over_its_budget():
    machine = {"cpu": "A made-up processor", "threads": 8, "memory": "16 GB", "os": "Linux", "python": "3.12.3", "disk": "the computer's own Linux disk (ext4)"}
    meta = {"date": "10/03/2026", "version": "x", "cases": 2000, "restricted": 300, "ledger": 500000, "views": 200000, "staff": 42, "requests": 20}
    page = lambda name, p50, p95, role="attorney": {"name": name, "role": role, "requests": 20, "first": p95, "p50": p50, "p95": p95, "max": p95, "kb": 12, "status": 200}  # noqa: E731
    after = {"machine": machine, "meta": meta, "pages": [page("All clients (first page)", 0.02, 0.05), page("Search (passport)", 0.5, 1.2), page("The overview counts", 0.01, 0.02)],
             "overnight": {"cases": 200, "workers": 7, "seconds": 86}, "server_memory": "900 MB", "portal_memory": "100 MB", "warm_seconds": 40}
    before = {"machine": machine, "meta": meta | {"requests": 5}, "pages": [page("All clients (first page)", 17.0, 18.0)], "overnight": {"cases": 200, "workers": 1, "seconds": 419}}
    text = measure_pages.render([(after, before)])
    assert "## On the computer's own Linux disk (ext4)" in text and "Before p50 | Before p95" in text
    assert "| All clients (first page) | attorney | 1.5 s | 17.00 | 18.00 | 0.02 | 0.05 |" in text and text.count("| yes |") == 2 and "| NO |" in text  # Search at 1.2 s is over its 1 s budget
    assert "**86 seconds** (before the fixes: 419 seconds, 1 at a time)" in text and "900 MB" in text
    assert measure_pages.percentile(list(range(1, 21)), 0.95) == 19
    assert measure_pages.budget_for("The overview counts") == 0.5 and measure_pages.budget_for("Search (passport)") == 1.0 and measure_pages.budget_for("A case page (review items)") == 2.0
    assert measure_pages.budget_for("Reports") == measure_pages.BUDGET_LIST
