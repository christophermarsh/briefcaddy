"""Assembly: one settled name for every form (wave K, brief K1). The name after marriage fills Part 1 item 1 of the I-485 and the
name boxes of every other form in the packet; the earlier name fills item 2 and the other forms' "other names" boxes; NOT APPLICABLE
is written nowhere. One source of truth in assemble (src/name_events.py), never a form of its own. The made-up Exemplo client of
tests/test_name_events.py: born and approved as ANA CLARA EXEMPLO SOUZA, married as ANA CLARA EXEMPLO SOUZA TESTE."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pypdf import PdfReader

import packet
from fill import load_field_map, map_facts_to_fields
from fill.companion import field_map_for, fill_companions, load_profile
from rules.policy import load_policy_profile, run_policies
from test_name_events import MA_AFTER, MA_NO_FIELD, _graph
import schema_path

REPO = Path(__file__).resolve().parents[1]
FIELD_MAP = load_field_map(schema_path.path("field_map", "i485"))
GIVEN, FAMILY, EARLIER_FAMILY = "ANA CLARA", "EXEMPLO SOUZA TESTE", "EXEMPLO SOUZA"
# the forms of the packets this client's filings use, each with its own name boxes (schemas/packets/companion_forms.json)
FORMS = ("g28", "i765", "i131", "i360", "i130", "n400", "g28_n400", "i589", "g28_i589", "i601a")
# the boxes for the earlier name on each: (fact key, the value it must hold)
OTHER = {"i765": ("applicant.other_name1_family", EARLIER_FAMILY), "i131": ("applicant.other_name1_family", EARLIER_FAMILY),
         "i360": ("applicant.other_name1_family", EARLIER_FAMILY), "i130": ("applicant.other_name1_family", EARLIER_FAMILY),
         "i601a": ("applicant.other_name1_family", EARLIER_FAMILY), "n400": ("n400.other_name1_family", EARLIER_FAMILY),
         "i589": ("asylum.other_names", f"{GIVEN} {EARLIER_FAMILY}")}


def _settled(marriage=MA_AFTER):
    import asylum
    import naturalization

    g = _graph(marriage=marriage)
    run_policies(g, load_policy_profile(schema_path.path("law", "policy_sijs"), firm=False))
    naturalization.derive(g, date(2026, 10, 3))
    asylum.derive(g, date(2026, 10, 3))
    return g


def _filled(path: Path) -> dict[str, str]:
    return {name: str(f.get("/V") or "") for name, f in (PdfReader(str(path)).get_fields() or {}).items()}


def test_the_i485_part_1_items_1_and_2_carry_the_settled_names():
    values = map_facts_to_fields(_settled(), FIELD_MAP).values
    box = lambda short: next(v for k, v in values.items() if k.endswith(short))  # noqa: E731
    assert (box("Pt1Line1_FamilyName[0]"), box("Pt1Line1_GivenName[0]")) == (FAMILY, GIVEN)
    assert (box("Pt1Line2_FamilyName[0]"), box("Pt1Line2_GivenName[0]")) == (EARLIER_FAMILY, GIVEN)
    assert "NOT APPLICABLE" not in [v for k, v in values.items() if "Pt1Line2" in k]


def test_every_form_in_the_packet_carries_the_one_settled_name(tmp_path):
    graph = _settled()
    profile = load_profile()
    profile = {**profile, "forms": {k: profile["forms"][k] for k in FORMS}}
    fill_companions(graph, tmp_path, profile)
    for form_id in FORMS:
        form = profile["forms"][form_id]
        fields = field_map_for(form)
        got = _filled(tmp_path / form["output"])
        for key, want in (("applicant.family_name", FAMILY), ("applicant.given_name", GIVEN)) + ((OTHER[form_id],) if form_id in OTHER else ()):
            boxes = (fields.get(key) or {}).get("fields") or []
            assert boxes, (form_id, key)
            for name in boxes:
                assert got.get(name) == want, (form_id, key, name, got.get(name))


def test_the_g1145_carries_the_settled_name(tmp_path):
    import enotice

    entry = enotice.render(tmp_path, _settled(), packet.load_filing("i485"))
    assert entry is not None
    got = _filled(tmp_path / entry["file"])
    assert {k.rsplit(".", 1)[-1]: v for k, v in got.items() if v in (FAMILY, GIVEN)} == {"LastName[0]": FAMILY, "FirstName[0]": GIVEN}


def test_with_one_name_on_every_document_item_2_is_not_applicable_and_no_other_name_box_is_filled(tmp_path):
    graph = _settled(None)  # a birth certificate, the I-360 approval and the client's answers, one name on each, no marriage certificate
    values = map_facts_to_fields(graph, FIELD_MAP).values
    assert {v for k, v in values.items() if "Pt1Line2_" in k} == {"NOT APPLICABLE"}
    assert next(v for k, v in values.items() if k.endswith("Pt1Line1_FamilyName[0]")) == EARLIER_FAMILY
    assert graph.get("n400.other_name1_family") is None and graph.get("asylum.other_names") is None


def test_a_marriage_certificate_without_a_name_after_marriage_leaves_item_2_blank_never_not_applicable():
    """Brief K6: the certificate does not say which name the client uses now, so the card asks; until then item 1 keeps the birth name
    for a person to confirm and item 2 is blank, though the client answered "no other names"."""
    graph = _settled(MA_NO_FIELD)
    values = map_facts_to_fields(graph, FIELD_MAP).values
    assert not [v for k, v in values.items() if "Pt1Line2" in k and v]
    assert next(v for k, v in values.items() if k.endswith("Pt1Line1_FamilyName[0]")) == EARLIER_FAMILY
    assert graph.get("applicant.family_name").tier == 3 and graph.get("applicant.na.other_names") is None


def test_the_eoir_42_lists_every_other_name_and_the_n400_follows_the_timeline():
    import cancellation
    import naturalization
    from extract.name_change_order import extract as extract_order
    from test_name_events import ORDER

    g = _graph(extra=(("decreto.pdf", "name_change_order", extract_order(ORDER)),))  # ANA CLARA TESTE now; two earlier names
    g.add_source("n400.other_name1_family", "naturalization.derive", "derived", "an earlier reading", "SOUZA", 0.85, tier=3)
    naturalization.derive(g, date(2026, 10, 3))
    assert g.get("n400.other_name1_family").value == FAMILY  # the timeline's newest earlier name, not the earlier reading
    cancellation.derive(g, date(2026, 10, 3))
    assert g.get("cancel.other_name1").value == f"{FAMILY}, {GIVEN}"
    assert g.get("cancel.other_name2").value == f"{EARLIER_FAMILY}, {GIVEN}"


def test_the_eoir_42_name_2_takes_what_fits_and_the_notes_list_the_rest():
    import cancellation

    g = _graph(marriage=MA_NO_FIELD)
    for n, (given, family) in enumerate((("ANA", "EXEMPLO"), ("ANA CLARA", "SOUZA REIS")), start=1):
        g.add_source(f"questionnaire.other_name{n}_given", "portal questionnaire", "intake_questionnaire", given, given, 0.95, tier=3)
        g.add_source(f"questionnaire.other_name{n}_family", "portal questionnaire", "intake_questionnaire", family, family, 0.95, tier=3)
    long_given, long_family = "NINA", "EXEMPLO DOS REIS SOUZA DA COSTA PEREIRA LIMA"
    g.add_source("questionnaire.other_name3_given", "portal questionnaire", "intake_questionnaire", long_given, long_given, 0.95, tier=3)
    g.add_source("questionnaire.other_name3_family", "portal questionnaire", "intake_questionnaire", long_family, long_family, 0.95, tier=3)
    from assemble import assemble

    assemble(g)
    first, second, left = cancellation.other_names(g)
    room = cancellation._room("eoir42b", "Name 2")
    assert 20 <= room <= 60 and (second is None or len(second) <= room)
    assert first == "EXEMPLO, ANA" and second == "SOUZA REIS, ANA CLARA"
    assert left == [f"{long_family}, {long_given}"]
    cancellation.derive(g, date(2026, 10, 3))
    assert g.get("cancel.other_name2").value == "SOUZA REIS, ANA CLARA"
    note = next(n for n in cancellation.notes(g, date(2026, 10, 3)) if n["title"] == "Other names that do not fit")
    assert note["level"] == "warn" and f"{long_family}, {long_given}" in note["text"]

