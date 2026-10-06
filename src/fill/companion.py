"""Companion forms filed with the I-485 -- Form G-28 (the attorney's notice
of appearance) and Form I-765 (work permit) -- filled from the same
reviewed case as the I-485, so a correction made in review reaches every
form.

The field maps are in schemas/packets/companion_forms.json, using each form's
short field names; a short name fills every field with that name (the
I-765 prints the client's name again on its last page). Firm choices for
SIJS filings (the I-765 category, "the attorney's representation extends
beyond preparing the form", ...) are that file's "constants" and need the
attorney's approval. What a form needs but the case doesn't have is left
blank and listed, never guessed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from factgraph import FactGraph

from .field_map import map_facts_to_fields
from . import template_cache
from .fill_pdf import field_max_lengths, fill_pdf
import schema_path

SCHEMAS = schema_path.ROOT


def load_profile(path: Path = schema_path.path("packet", "companion_forms")) -> dict[str, Any]:
    """The companion forms, with the firm's details as set on the Settings page (src/settings.py) over the defaults."""
    import settings

    profile = json.loads(path.read_text(encoding="utf-8"))
    profile["firm"] = settings.overlay("firm", settings.shipped(profile.get("firm", {})))
    for form in profile.get("forms", {}).values():
        if "map_from" in form:  # the same template filled for another filing: its map, with the filing's own fact keys (the DACA I-765)
            _borrow_map(form, profile["forms"][form["map_from"]])
    return profile


def _borrow_map(form: dict[str, Any], source: dict[str, Any]) -> None:
    """form["map"] and form["optional"] from another form's, each key renamed by form["rename"]
    ({"ead.": "daca_ead."}: a prefix ending in a dot, or a whole key) -- so one filing's answers never fill another's form."""
    def renamed(key: str) -> str:
        for old, new in (form.get("rename") or {}).items():
            if key == old or (old.endswith(".") and key.startswith(old)):
                return new + key[len(old):]
        return key

    form["map"] = {renamed(k): spec for k, spec in source["map"].items()} | form.get("map", {})
    form["optional"] = sorted({renamed(k) for k in source.get("optional", [])} | set(form.get("optional", [])))
    form.setdefault("template", source["template"])


def _qualify(spec: dict[str, Any], names: dict[str, list[str]]) -> dict[str, Any]:
    """Short field names -> the template's full names (every field with that name)."""
    def full(short: str) -> list[str]:
        if short not in names:
            raise KeyError(f"no field named {short} in the template")
        return names[short]

    spec = dict(spec)
    if "fields" in spec:
        spec["fields"] = [f for short in spec["fields"] for f in full(short)]
    if "options" in spec:
        spec["options"] = {value: ([[full(f)[0], o] for f, o in option] if isinstance(option[0], list) else [full(option[0])[0], option[1]])
                           for value, option in spec["options"].items()}
    for side in ("yes", "no"):
        if side in spec:
            spec[side] = [full(spec[side][0])[0], spec[side][1]]
    return spec


def field_map_for(form: dict[str, Any], schemas: Path = SCHEMAS) -> dict[str, Any]:
    names: dict[str, list[str]] = {}
    for full_name in template_cache.fields(schema_path.named(form["template"], schemas)):
        # split on unescaped dots: the N-400 names a box "P9_Line7\.b\.[0]" (its short name keeps the "\.")
        parts = re.split(r"(?<!\\)\.", full_name)
        names.setdefault(parts[-1], []).append(full_name)
        # and with its page: the I-131 reuses "CB_AppType[0]" on four pages, so its map says "P2[0].CB_AppType[12]"
        names.setdefault(".".join(parts[-2:]), []).append(full_name)
        # and with two parents, where even that repeats: the I-134 has "#area[0].P2_Line8_DateOfBirth[0]" on the supporter's page and the beneficiary's
        if len(parts) > 2:
            names.setdefault(".".join(parts[-3:]), []).append(full_name)
    return {key: _qualify(spec, names) for key, spec in form["map"].items()}


def _with_constants(graph: FactGraph, profile: dict[str, Any]) -> FactGraph:
    """The case's facts, plus the firm's address and the filing constants
    where the case doesn't already have them."""
    for key, value in {**profile.get("firm", {}), **profile.get("constants", {})}.items():
        if graph.get(key) is None:
            graph.add_source(key, "companion_forms.json", "firm_profile", value, value, 1.0)
    import g28  # the G-28's client mailing address: the card's (src/g28.py apply), else the client's own home address, as before

    g28.fallback(graph)
    if graph.get("applicant.last_arrival_date") is None:  # the date of last entry every form prints is the settled one (src/arrival.py)
        import arrival

        arrival.settle_date(graph)
    city, state = (graph.get(f"applicant.last_arrival_{p}") for p in ("city", "state"))
    if graph.get("applicant.last_arrival_place") is None and city is not None and city.status == "resolved" and city.value:
        place = f"{city.value}, {state.value}" if state is not None and state.status == "resolved" and state.value else str(city.value)
        graph.add_source("applicant.last_arrival_place", "fact_graph", "derived", place, place, 1.0)
    return graph


def fill_companions(graph: FactGraph, out_dir: Path, profile: dict[str, Any] | None = None, schemas: Path = SCHEMAS) -> dict[str, Any]:
    """Fills every companion form into out_dir; returns, per form, its file,
    the boxes it couldn't fill, and what the attorney completes by hand."""
    profile = profile or load_profile(schema_path.path("packet", "companion_forms", schemas))
    graph = _with_constants(graph, profile)
    out: dict[str, Any] = {}
    for form_id, form in profile["forms"].items():
        field_map = field_map_for(form, schemas)
        mapping = map_facts_to_fields(graph, field_map, form_id)  # form_id: a paper marked absent reads the word this form's instructions give
        limits = field_max_lengths(schema_path.named(form["template"], schemas))
        too_long = sorted({name.rsplit(".", 1)[-1] for name, v in mapping.values.items() if isinstance(v, str) and len(v) > limits.get(name, 10**6)})
        values = {name: v for name, v in mapping.values.items() if name.rsplit(".", 1)[-1] not in too_long}
        values, not_options = _match_dropdowns(values, schema_path.named(form["template"], schemas))
        path = Path(out_dir) / form["output"]
        cleared = [name for name in form.get("clear", []) if name not in values]  # boxes the agency's template ships filled ("0.00")
        fill_pdf(schema_path.named(form["template"], schemas), values | {name: "" for name in cleared}, path)
        if cleared:
            _drop_defaults(path, cleared)
        copies = _additional_pages(graph, form, field_map, schema_path.named(form["template"], schemas), path)  # first: the signing record below stays last
        if form_id == "eoir26a":  # the client's and the attorney's signatures in their boxes, and the signing record after the last page (src/eoir26a.py)
            import eoir26a

            eoir26a.finish(path, graph, Path(out_dir))
        import absence

        blank = [key for key in field_map if not _has(graph, key) and absence.marked_value(graph, key, form_id) is None and key not in form.get("optional", [])]
        # a list on the form that has no choice for what the case says (the I-130's class of admission has no NOT APPLICABLE): empty, and listed
        short_bad = {entry.split(":")[0] for entry in not_options}
        no_option = [key for key, spec in field_map.items() if any(f.rsplit(".", 1)[-1] in short_bad for f in spec.get("fields", [])) and key not in blank]
        out[form_id] = {"title": form["title"], "path": str(path), "left_blank": blank, "too_long": too_long, "not_an_option": not_options, "no_option": no_option,
                        "attorney_completes": form.get("attorney_completes", []), "signatures": form.get("signatures", {}),
                        "additional_page_copies": copies}
    return out


def _additional_pages(graph: FactGraph, form: dict[str, Any], field_map: dict[str, Any], template: Path, path: Path) -> int:
    """A form whose map carries Part 14 entries ("<prefix>.p14_block<n>_text", as the N-400's does) gets every entry laid
    out on the form's own Additional Information page, and copies of that page for what its boxes can't hold
    (fill/continuation.py): the form's own page, never a sheet of ours. Returns how many copies were added."""
    from batch import part14_blocks

    from .continuation import finish_part14

    prefix = next((key[: -len(".p14_block1_text")] for key in field_map if key.endswith(".p14_block1_text")), None)
    entries = part14_blocks(graph, prefix) if prefix else []
    if not entries:
        return 0
    text = lambda key: str(f.value) if (f := graph.get(key)) is not None and f.status == "resolved" and f.value else ""  # noqa: E731
    return finish_part14(path, [b for _, b in entries], template, family=text("applicant.family_name"), given=text("applicant.given_name"),
                         middle=text("applicant.middle_name"), a_number=text("applicant.a_number")).copies


def _drop_defaults(path: Path, names: list[str]) -> None:
    """A cleared box keeps no default value either, so a form reset doesn't bring the template's "0.00" back (Form EOIR-26A)."""
    from pypdf import PdfWriter
    from pypdf.generic import NameObject

    writer = PdfWriter(clone_from=str(path))
    for page in writer.pages:
        for annot in page.get("/Annots") or []:
            field = annot.get_object()
            if field.get("/T") in names or (field.get("/Parent") and field["/Parent"].get_object().get("/T") in names):
                for obj in (field, field["/Parent"].get_object() if field.get("/Parent") else None):
                    if obj is not None:
                        obj.pop(NameObject("/DV"), None)
    with open(path, "wb") as fh:
        writer.write(fh)


def _dropdown_options(template: Path) -> dict[str, list[str]]:
    out = {}
    for name, field in template_cache.fields(template).items():
        if field.get("/FT") == "/Ch" and field.get("/Opt"):
            out[name] = [str(o[0] if isinstance(o, list) else o) for o in field["/Opt"]]
    return out


def _match_dropdowns(values: dict[str, Any], template: Path) -> tuple[dict[str, Any], list[str]]:
    """A dropdown takes only its own options: "MA" goes in as " MA" where the
    form lists " MA" (the N-400, I-90...). A value that isn't an option at all
    is left out and reported -- never written as free text into a list box."""
    options = _dropdown_options(template)
    out, bad = dict(values), []
    for name, value in values.items():
        opts = options.get(name)
        if not opts or not isinstance(value, str) or value in opts:
            continue
        match = next((o for o in opts if o.strip().upper() == value.strip().upper()), None)
        if match is not None:
            out[name] = match
        else:
            out.pop(name)
            bad.append(f"{name.rsplit('.', 1)[-1]}: {value}")
    return out, bad


def _has(graph: FactGraph, key: str) -> bool:
    fact = graph.get(key)
    return fact is not None and fact.status == "resolved" and fact.value not in (None, "")
