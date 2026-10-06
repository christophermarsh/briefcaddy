"""The Settings page lays its fields out in rows whose boxes line up (the owner's walk through the real Main office, 10/04/2026): a label that
wraps to two lines used to push its box lower than the boxes beside it, and a drop-down was shorter than a text box. Everything is invented."""

from __future__ import annotations

MEASURE = """() => {
  const out = [];
  for (const grid of document.querySelectorAll('.setgrid')) {
    const rows = new Map();
    for (const f of grid.querySelectorAll(':scope > .setfield')) {
      const box = f.querySelector(':scope > input, :scope > select, :scope > span:not(.setlbl):not(.hint)');
      if (!box) continue;
      const r = box.getBoundingClientRect(), l = f.querySelector('.setlbl').getBoundingClientRect();
      const key = Math.round(l.top);  // the fields of one row start at the same height
      if (!rows.has(key)) rows.set(key, []);
      rows.get(key).push({ top: Math.round(r.top), height: Math.round(r.height), tag: box.tagName });
    }
    for (const [key, boxes] of rows) if (boxes.length > 1) out.push(boxes);
  }
  return out;
}"""


def test_the_boxes_of_one_row_of_fields_line_up_whatever_their_labels_say(world, attorney):
    attorney.page.goto("about:blank")  # a page that differs only after the # would not reload
    attorney.page.goto(world["review"] + "#settings")
    attorney.settle()
    attorney.page.wait_for_selector(".setgrid .setfield", state="attached", timeout=20000)
    attorney.page.evaluate("() => document.querySelectorAll('details').forEach((d) => { d.open = true; })")  # a section folded away has no layout to measure
    rows = attorney.page.evaluate(MEASURE)
    assert rows, "the Settings page showed no row with two fields"
    for boxes in rows:
        assert len({b["top"] for b in boxes}) == 1, f"boxes in one row start at different heights: {boxes}"
        assert max(b["height"] for b in boxes) - min(b["height"] for b in boxes) <= 2, f"boxes of different heights in one row: {boxes}"
