"""Stage 5 (field mapping) and stage 6 (fill). Most tests use a small
synthetic field map and a synthetic fillable PDF built by the
make_fillable_pdf fixture (conftest.py); the *_against_real_template
tests at the bottom load the actual schemas/forms/i485/field_map.json against
the real schemas/forms/i485/template.pdf, so a typo'd field name in the real map
fails a test instead of only surfacing when filling a real client's form."""

import json
from pathlib import Path

import pytest

from factgraph import FactGraph
from fill import FieldLengthExceeded, fill_pdf, load_field_map, map_facts_to_fields
import schema_path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REAL_TEMPLATE = schema_path.path("template", "i485")
_REAL_FIELD_MAP = schema_path.path("field_map", "i485")


def test_load_field_map_reads_fact_to_acroform_section(tmp_path):
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"fact_to_acroform": {"applicant.i94_number": ["I94Num"]}}))
    field_map = load_field_map(path)
    assert field_map == {"applicant.i94_number": ["I94Num"]}


def test_load_field_map_handles_missing_section(tmp_path):
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"_status": "skeleton"}))
    assert load_field_map(path) == {}


def test_map_facts_to_fields_includes_only_resolved_facts():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.i94_number", "i94.pdf", "i94", "14335150685", "14335150685", 0.98)
    graph.add_source("applicant.eye_color", "drivers_license.pdf", "drivers_license", "BRO", "Brown", 0.95)
    graph.add_source("applicant.eye_color", "old_intake.pdf", "intake_form", "Black", "Black", 0.4)
    graph.mark_missing("applicant.employment_history", "no source yet")

    field_map = {
        "applicant.i94_number": ["Pt1Line_I94Number"],
        "applicant.eye_color": ["Pt1Line_EyeColor"],
        "applicant.employment_history": ["Pt13Line_Employment"],
    }
    result = map_facts_to_fields(graph, field_map)

    assert result.values == {"Pt1Line_I94Number": "14335150685"}
    assert result.unmapped_facts == []


def test_map_facts_to_fields_writes_to_every_target_field():
    # A single fact can back more than one line on the real form
    # (docs/GRAPH_MODEL.md: "target(s)").
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.current_legal_name", "marriage_certificate.pdf", "marriage_certificate", "Sabrina", "Sabrina", 0.9)
    field_map = {"applicant.current_legal_name": ["Pt1Line1_FamilyName", "Pt6Line1_Signature_PrintedName"]}
    result = map_facts_to_fields(graph, field_map)
    assert result.values == {
        "Pt1Line1_FamilyName": "Sabrina",
        "Pt6Line1_Signature_PrintedName": "Sabrina",
    }


def test_map_facts_to_fields_flags_a_resolved_fact_with_no_map_entry():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.i94_number", "i94.pdf", "i94", "14335150685", "14335150685", 0.98)
    result = map_facts_to_fields(graph, field_map={})
    assert result.values == {}
    assert result.unmapped_facts == ["applicant.i94_number"]


def test_map_facts_to_fields_yes_no_selects_the_right_field_and_on_value():
    graph = FactGraph("maria_eduarda")
    graph.add_derived("applicant.part9.violated_nonimmigrant_status", "Yes", "OVERSTAY-01", ["x"])
    field_map = {
        "applicant.part9.violated_nonimmigrant_status": {
            "type": "yes_no",
            "yes": ["Pt8Line13_YesNo[1]", "/Y"],
            "no": ["Pt8Line13_YesNo[0]", "/N"],
        }
    }
    result = map_facts_to_fields(graph, field_map)
    assert result.values == {"Pt8Line13_YesNo[1]": "/Y"}


def test_map_facts_to_fields_choice_by_value_selects_the_matching_option():
    # Real example case: the license read "BRO" -> normalized to "Brown"
    # by src/extract/drivers_license.py -- the real I-485 has a separate
    # Btn field per eye color, not one text field.
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.eye_color", "drivers_license.pdf", "drivers_license", "BRO", "Brown", 0.95)
    field_map = {
        "applicant.eye_color": {
            "type": "choice_by_value",
            "options": {
                "Blue": ["Eyecolor[0]", "/BU"],
                "Brown": ["Eyecolor[2]", "/BN"],
            },
        }
    }
    result = map_facts_to_fields(graph, field_map)
    assert result.values == {"Eyecolor[2]": "/BN"}


def test_map_facts_to_fields_choice_by_value_unmapped_when_value_has_no_option():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.eye_color", "drivers_license.pdf", "drivers_license", "DIC", "Dichromatic", 0.9)
    field_map = {"applicant.eye_color": {"type": "choice_by_value", "options": {"Brown": ["Eyecolor[2]", "/BN"]}}}
    result = map_facts_to_fields(graph, field_map)
    assert result.values == {}
    assert result.unmapped_facts == ["applicant.eye_color"]


def test_map_facts_to_fields_date_reformats_iso_to_mm_dd_yyyy():
    # Found by actually generating a filled PDF: the form's own field
    # tooltips say "Enter the 2-digit Month, 2-digit Day, and 4-digit
    # Year", but every date fact in this pipeline is stored as ISO
    # internally -- a "text" mapping would write "2002-02-20" verbatim.
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.dob", "i94.pdf", "i94", "2002 February 20", "2002-02-20", 0.9)
    field_map = {"applicant.dob": {"type": "date", "fields": ["Pt1Line3_DOB[0]"]}}
    result = map_facts_to_fields(graph, field_map)
    assert result.values == {"Pt1Line3_DOB[0]": "02/20/2002"}


def test_map_facts_to_fields_date_unmapped_when_value_is_not_a_real_date():
    graph = FactGraph("maria_eduarda")
    graph.add_derived("applicant.dob", "unknown", "SOME-RULE", [])
    field_map = {"applicant.dob": {"type": "date", "fields": ["Pt1Line3_DOB[0]"]}}
    result = map_facts_to_fields(graph, field_map)
    assert result.values == {}
    assert result.unmapped_facts == ["applicant.dob"]


def test_map_facts_to_fields_digits_only_strips_ssn_formatting():
    # Found by actually generating a filled PDF: the real SSN field is
    # /MaxLen 9 (exactly 9 raw digits) but applicant.ssn's normalized
    # value keeps its readable hyphens ("681-53-4454") -- a "text"
    # mapping overflows the field and pypdf silently truncates it.
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.ssn", "ssn.pdf", "ssn_card", "681-53-4454", "681-53-4454", 0.97)
    field_map = {"applicant.ssn": {"type": "digits_only", "fields": ["Pt1Line19_SSN[0]"]}}
    result = map_facts_to_fields(graph, field_map)
    assert result.values == {"Pt1Line19_SSN[0]": "681534454"}


def test_map_facts_to_fields_digits_only_strips_a_number_prefix():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.a_number", "notice.pdf", "i360_approval", "A201 821 016", "A201821016", 0.9)
    field_map = {"applicant.a_number": {"type": "digits_only", "fields": ["Pt1Line4_AlienNumber[0]"]}}
    result = map_facts_to_fields(graph, field_map)
    assert result.values == {"Pt1Line4_AlienNumber[0]": "201821016"}


def test_map_facts_to_fields_digits_only_unmapped_when_value_has_no_digits():
    graph = FactGraph("maria_eduarda")
    graph.add_derived("applicant.ssn", "unknown", "SOME-RULE", [])
    field_map = {"applicant.ssn": {"type": "digits_only", "fields": ["Pt1Line19_SSN[0]"]}}
    result = map_facts_to_fields(graph, field_map)
    assert result.values == {}
    assert result.unmapped_facts == ["applicant.ssn"]


def test_map_facts_to_fields_digits_split_writes_one_digit_per_field():
    # The real Weight field is three separate single-digit boxes, not one
    # field (Pt7Line4_Weight1/2/3).
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.weight_lbs", "questionnaire.pdf", "intake_questionnaire", "118 lbs", "118", 0.85, tier=3)
    field_map = {"applicant.weight_lbs": {"type": "digits_split", "fields": ["W1", "W2", "W3"]}}
    result = map_facts_to_fields(graph, field_map)
    assert result.values == {"W1": "1", "W2": "1", "W3": "8"}


def test_map_facts_to_fields_digits_split_unmapped_when_digit_count_does_not_match():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.weight_lbs", "questionnaire.pdf", "intake_questionnaire", "1500", "1500", 0.85, tier=3)
    field_map = {"applicant.weight_lbs": {"type": "digits_split", "fields": ["W1", "W2", "W3"]}}
    result = map_facts_to_fields(graph, field_map)
    assert result.values == {}
    assert result.unmapped_facts == ["applicant.weight_lbs"]


def test_map_facts_to_fields_height_feet_inches_splits_the_combined_value():
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.height", "drivers_license.pdf", "drivers_license", "5'-05\"", "5'5\"", 0.95)
    field_map = {
        "applicant.height": {
            "type": "height_feet_inches",
            "feet_field": "HeightFeet[0]",
            "inches_field": "HeightInches[0]",
        }
    }
    result = map_facts_to_fields(graph, field_map)
    assert result.values == {"HeightFeet[0]": "5", "HeightInches[0]": "5"}


def test_fill_pdf_writes_values_into_named_fields(make_fillable_pdf, tmp_path):
    template = make_fillable_pdf(["Pt1Line_I94Number", "Pt1Line_EyeColor"])
    output = tmp_path / "filled.pdf"

    fill_pdf(template, {"Pt1Line_I94Number": "14335150685", "Pt1Line_EyeColor": "Brown"}, output)

    from pypdf import PdfReader

    fields = PdfReader(output).get_fields()
    assert fields["Pt1Line_I94Number"]["/V"] == "14335150685"
    assert fields["Pt1Line_EyeColor"]["/V"] == "Brown"


def test_fill_pdf_creates_output_directory_if_missing(make_fillable_pdf, tmp_path):
    template = make_fillable_pdf(["Pt1Line_I94Number"])
    output = tmp_path / "nested" / "dir" / "filled.pdf"

    fill_pdf(template, {"Pt1Line_I94Number": "14335150685"}, output)

    assert output.exists()


def test_fill_pdf_refuses_to_silently_truncate_a_value_exceeding_maxlen(make_fillable_pdf, tmp_path):
    # Found by actually generating a filled PDF: pypdf silently truncates
    # a value longer than a field's /MaxLen instead of raising. This is
    # the real SSN field's own constraint (9 digits) with a value that
    # already has its hyphens stripped by field_map.py's "digits_only"
    # type but is still too long, e.g. a bad extraction.
    template = make_fillable_pdf(["Pt1Line19_SSN[0]"], max_lengths={"Pt1Line19_SSN[0]": 9})
    output = tmp_path / "filled.pdf"

    with pytest.raises(FieldLengthExceeded):
        fill_pdf(template, {"Pt1Line19_SSN[0]": "1234567890"}, output)

    assert not output.exists()


def test_fill_pdf_allows_a_value_within_maxlen(make_fillable_pdf, tmp_path):
    template = make_fillable_pdf(["Pt1Line19_SSN[0]"], max_lengths={"Pt1Line19_SSN[0]": 9})
    output = tmp_path / "filled.pdf"

    fill_pdf(template, {"Pt1Line19_SSN[0]": "681534454"}, output)

    from pypdf import PdfReader

    assert PdfReader(output).get_fields()["Pt1Line19_SSN[0]"]["/V"] == "681534454"


def test_field_map_and_fill_pdf_compose_end_to_end(make_fillable_pdf, tmp_path):
    # The actual stage 5 -> 6 handoff: map_facts_to_fields' output feeds
    # fill_pdf directly, with no re-typing step in between.
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.i94_number", "i94.pdf", "i94", "14335150685", "14335150685", 0.98)
    field_map = {"applicant.i94_number": ["Pt1Line_I94Number"]}
    mapping = map_facts_to_fields(graph, field_map)

    template = make_fillable_pdf(["Pt1Line_I94Number"])
    output = tmp_path / "filled.pdf"
    fill_pdf(template, mapping.values, output)

    from pypdf import PdfReader

    assert PdfReader(output).get_fields()["Pt1Line_I94Number"]["/V"] == "14335150685"


def test_real_field_map_targets_all_exist_as_real_acroform_fields():
    # Guards against a typo'd field name in schemas/forms/i485/field_map.json --
    # every field name it references must actually exist on the real form.
    from pypdf import PdfReader

    reader = PdfReader(_REAL_TEMPLATE)
    reader.decrypt("")
    real_field_names = set(reader.get_fields().keys())

    field_map = load_field_map(_REAL_FIELD_MAP)
    referenced_fields: set[str] = set()
    for mapping in field_map.values():
        mapping_type = mapping.get("type", "text")
        if mapping_type == "text":
            referenced_fields.update(mapping["fields"])
        elif mapping_type == "yes_no":
            referenced_fields.add(mapping["yes"][0])
            referenced_fields.add(mapping["no"][0])
        elif mapping_type == "choice_by_value":
            referenced_fields.update(field for field, _ in mapping["options"].values())
        elif mapping_type == "height_feet_inches":
            referenced_fields.add(mapping["feet_field"])
            referenced_fields.add(mapping["inches_field"])

    missing = referenced_fields - real_field_names
    assert missing == set()


def test_real_field_map_fills_the_real_template_end_to_end(tmp_path):
    # The full stage 3 -> 5 -> 6 handoff against the actual blank I-485.
    graph = FactGraph("maria_eduarda")
    graph.add_source("applicant.i94_number", "i94.pdf", "i94", "14335150685", "14335150685", 0.98)
    graph.add_source("applicant.ssn", "ssn.pdf", "ssn_card", "123-45-6789", "123-45-6789", 0.97)
    graph.add_source("applicant.eye_color", "drivers_license.pdf", "drivers_license", "BRO", "Brown", 0.95)
    graph.add_source("applicant.height", "drivers_license.pdf", "drivers_license", "5'-05\"", "5'5\"", 0.95)
    graph.add_derived("applicant.part9.violated_nonimmigrant_status", "Yes", "OVERSTAY-01", ["x"])
    graph.add_derived("applicant.part9.unlawfully_present_since_1997", "Yes", "OVERSTAY-01", ["x"])

    field_map = load_field_map(_REAL_FIELD_MAP)
    mapping = map_facts_to_fields(graph, field_map)
    assert mapping.unmapped_facts == []

    output = tmp_path / "filled_real.pdf"
    fill_pdf(_REAL_TEMPLATE, mapping.values, output)

    from pypdf import PdfReader

    filled_fields = PdfReader(output).get_fields()
    assert filled_fields["form1[0].#subform[2].P1Line12_I94[0]"]["/V"] == "14335150685"
    # SSN field is /MaxLen 9 -- digits only, hyphens stripped (field_map's
    # "digits_only" type; found and fixed after this exact end-to-end test
    # caught pypdf silently truncating the hyphenated form).
    assert filled_fields["form1[0].#subform[3].Pt1Line19_SSN[0]"]["/V"] == "123456789"
    assert filled_fields["form1[0].#subform[12].Pt7Line5_Eyecolor[2]"]["/V"] == "/BN"
    assert filled_fields["form1[0].#subform[12].Pt7Line3_HeightFeet[0]"]["/V"] == "5"
    assert filled_fields["form1[0].#subform[12].Pt7Line3_HeightInches[0]"]["/V"] == "5"
    assert filled_fields["form1[0].#subform[13].Pt8Line13_YesNo[1]"]["/V"] == "/Y"
    assert filled_fields["form1[0].#subform[20].Pt9Line76_YesNo[1]"]["/V"] == "/Y"
