"""Stage 6 -- fill (docs/ARCHITECTURE.md sections 5-6). Writes
field_values straight into a PDF's named AcroForm fields via pypdf --
no coordinate-based overlay, and no manual re-typing step anywhere in
this pipeline (docs/GRAPH_MODEL.md: "Fill writes FROM the graph, never
re-types" -- this is the structural fix for the "Worcesrter"/"Framigham"
class of error, not just a check for it after the fact).

Found the hard way, by actually inspecting a generated PDF: pypdf silently
truncates a value that exceeds a text field's own /MaxLen (a real
constraint baked into the real I-485's AcroForm -- e.g. the SSN and
A-Number fields are both /MaxLen 9, expecting exactly 9 digits with no
formatting) and only prints an easy-to-miss stderr warning while doing
it. That already silently corrupted the SSN and A-Number in every filled
PDF this pipeline had generated before this was caught (fixed at the
field_map.py layer with a "digits_only" type). Rather than trust field
mapping to always get every current and future field's formatting right,
this module checks every value against the template's own /MaxLen before
writing anything, and raises loudly instead of writing a silently
truncated value -- get_fields() doesn't reliably surface /MaxLen (checked
directly: it was missing from that summary even though the field's own
raw object has it), so this walks the raw AcroForm field tree itself.
"""

from __future__ import annotations

from pathlib import Path

from . import template_cache


class FieldLengthExceeded(ValueError):
    pass


def field_max_lengths(template_path: str | Path) -> dict[str, int]:
    """Every field's /MaxLen in template_path, by full field name -- lets a
    caller set aside an overlong value (and flag it) before fill_pdf()
    would refuse the whole fill."""
    return template_cache.max_lengths(template_path)


def fill_pdf(template_path: str | Path, field_values: dict[str, str], output_path: str | Path) -> None:
    """template_path is the blank I-485 (or any AcroForm PDF); field_values
    keys are AcroForm field names, already resolved by field_map.py -- this
    function does no fact-graph lookups of its own, so it stays reusable
    for any templated form, not just the I-485.

    Raises FieldLengthExceeded if any value would overflow its field's own
    /MaxLen -- a real, previously-silent bug (see module docstring), not a
    hypothetical one."""
    writer = template_cache.writer(template_path)  # a copy of the template read once for the process (fill/template_cache.py)

    max_lengths = template_cache.max_lengths(template_path)
    violations = [
        (name, value, max_lengths[name])
        for name, value in field_values.items()
        if name in max_lengths and isinstance(value, str) and len(value) > max_lengths[name]
    ]
    if violations:
        detail = "; ".join(f"{name}={value!r} (max {max_len})" for name, value, max_len in violations)
        raise FieldLengthExceeded(f"Value(s) exceed their field's MaxLen and would be silently truncated: {detail}")

    for page in writer.pages:
        writer.update_page_form_field_values(page, field_values)

    # USCIS templates also carry an XFA copy of the form, which pypdf doesn't
    # fill. Adobe Reader prefers XFA when it's there, so it could show (and
    # print) the blank XFA form over the filled AcroForm fields. Without XFA,
    # every reader shows the filled fields.
    acroform = writer._root_object.get("/AcroForm")
    if acroform is not None:
        acroform.get_object().pop("/XFA", None)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as fh:
        writer.write(fh)
