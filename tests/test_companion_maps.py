"""Every companion form's map (schemas/packets/companion_forms.json) against its real
template: every box it names exists, and every checkbox / radio value it
writes is one the box actually has (a wrong on-value leaves the box blank on
the printed form, silently -- the N-600's applicant marital-status boxes are
numbered differently from its parents' boxes, for one)."""

import re

import pytest
from pypdf import PdfReader

from fill.companion import load_profile
import schema_path

PROFILE = load_profile()


def _template(form):
    reader = PdfReader(str(schema_path.named(form["template"])))
    if reader.is_encrypted:
        reader.decrypt("")
    names, states = set(), {}
    for name in reader.get_fields() or {}:
        parts = re.split(r"(?<!\\)\.", name)
        names.update({parts[-1], ".".join(parts[-2:]), ".".join(parts[-3:])})  # a short name, or with its page ("P2[0].CB_AppType[12]"), or two levels up
    for page in reader.pages:
        for a in page.get("/Annots") or []:
            a = a.get_object()
            full, node = [], a
            while node is not None:
                if node.get("/T"):
                    full.append(str(node["/T"]))
                node = node.get("/Parent").get_object() if node.get("/Parent") else None
            ap = a.get("/AP", {}).get("/N") if a.get("/AP") else None
            if ap is not None and hasattr(ap, "keys"):
                parts = re.split(r"(?<!\\)\.", ".".join(reversed(full)))
                for key in (parts[-1], ".".join(parts[-2:]), ".".join(parts[-3:])):
                    states.setdefault(key, set()).update(str(k) for k in ap.keys() if k != "/Off")
    return names, states


@pytest.mark.parametrize("form_id", sorted(PROFILE["forms"]))
def test_every_box_and_value_exists_on_the_form(form_id):
    form = PROFILE["forms"][form_id]
    names, states = _template(form)
    missing, wrong = [], []
    for key, spec in form["map"].items():
        if not isinstance(spec, dict):
            continue
        for field in spec.get("fields") or []:
            if field not in names:
                missing.append(f"{key}: {field}")
        pairs = [spec[s] for s in ("yes", "no") if s in spec]
        for option in (spec.get("options") or {}).values():
            pairs += option if isinstance(option[0], list) else [option]
        for field, on in pairs:
            if field not in names:
                missing.append(f"{key}: {field}")
            elif states.get(field) and on not in states[field]:
                wrong.append(f"{key}: {field} has {sorted(states[field])}, the map writes {on!r}")
        for k in ("feet_field", "inches_field"):
            if k in spec and spec[k] not in names:
                missing.append(f"{key}: {spec[k]}")
    assert not missing and not wrong, missing + wrong


def test_the_i485_map_too():
    from fill import load_field_map

    fmap = load_field_map(schema_path.path("field_map", "i485"))
    reader = PdfReader(str(schema_path.path("template", "i485")))
    if reader.is_encrypted:
        reader.decrypt("")
    names = set(reader.get_fields() or {})
    states = {}
    for page in reader.pages:
        for a in page.get("/Annots") or []:
            a = a.get_object()
            full, node = [], a
            while node is not None:
                if node.get("/T"):
                    full.append(str(node["/T"]))
                node = node.get("/Parent").get_object() if node.get("/Parent") else None
            ap = a.get("/AP", {}).get("/N") if a.get("/AP") else None
            if ap is not None and hasattr(ap, "keys"):
                states.setdefault(".".join(reversed(full)), set()).update(str(k) for k in ap.keys() if k != "/Off")
    bad = []
    for key, spec in fmap.items():
        if isinstance(spec, list):
            spec = {"fields": spec}
        bad += [f"{key}: {f}" for f in spec.get("fields") or [] if f not in names]
        pairs = [spec[s] for s in ("yes", "no") if s in spec]
        for option in (spec.get("options") or {}).values():
            pairs += option if isinstance(option[0], list) else [option]
        bad += [f"{key}: {f} {on!r} not in {sorted(states.get(f, []))}" for f, on in pairs if f not in names or (states.get(f) and on not in states[f])]
    assert not bad, bad
