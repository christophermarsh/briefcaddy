"""Family-based adjustment of status (src/family.py): the petitioner's own
documents and facts, the questions, the I-130 / I-130A / I-864 filled from
the same case as the I-485, and their packet."""

import json
from datetime import date
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

import family
import packet
from classify import classify_text
from extract import extract_fields
from fill import fill_pdf
import schema_path

REPO = Path(__file__).resolve().parent.parent
US_PASSPORT = """PASSPORT
UNITED STATES OF AMERICA
Surname EXEMPLO
Given Names MICHAEL
Nationality UNITED STATES OF AMERICA
P<USAEXEMPLO<<MICHAEL<<<<<<<<<<<<<<<<<<<<<<<<<<<
5550001110USA8001019M3001011<<<<<<<<<<<<<<<<<<00
"""
TAX = """Form 1040 U.S. Individual Income Tax Return 2024
Department of the Treasury - Internal Revenue Service
11 This is your adjusted gross income 52,300
"""


def test_the_petitioners_documents_are_theirs_never_the_clients():
    assert classify_text(US_PASSPORT).doc_type == "us_passport"  # not the client's foreign passport
    keys = {f.fact_key: f.normalized_value for f in extract_fields("us_passport", US_PASSPORT)}
    assert keys["petitioner.status"] == "USC" and not any(k.startswith("applicant.") for k in keys)
    assert classify_text("UNITED STATES OF AMERICA\nCERTIFICATE OF NATURALIZATION\nCertificate Number 12345678").doc_type == "citizenship_certificate"
    assert classify_text("PERMANENT RESIDENT\nUSCIS# 012-345-678\nCategory IR1\nResident Since: 01/02/2015").doc_type == "green_card"
    assert classify_text(TAX).doc_type == "tax_return"
    assert {f.fact_key: f.normalized_value for f in extract_fields("tax_return", TAX)} == {"i864.tax_return_2024": "52300"}


def _case(tmp_path, answered=True):
    facts = {"applicant.family_name": "SOUZA", "applicant.given_name": "ANA", "applicant.dob": "1999-05-01", "applicant.sex": "F",
             "applicant.country_of_birth": "BRAZIL", "applicant.birth_city": "VILA VELHA", "applicant.physical_street": "10 EXAMPLE ST",
             "applicant.physical_city": "Boston", "applicant.physical_state": "MA", "applicant.physical_zip": "02110", "applicant.mailing_street": "10 EXAMPLE ST",
             "applicant.mailing_city": "Boston", "applicant.mailing_state": "MA", "applicant.mailing_zip": "02110", "applicant.i94_arrival_date": "2019-07-15",
             "applicant.i94_number": "12345678901", "applicant.marriage_date": "2023-06-10", "applicant.marriage_city": "Boston", "applicant.marriage_state": "MA",
             "applicant.marriage_country": "USA", "applicant.spouse_family_name": "EXEMPLO", "applicant.spouse_given_name": "MICHAEL",
             "applicant.spouse_dob": "1980-01-01", "applicant.spouse_country_of_birth": "USA", "applicant.father_family_name": "SOUZA",
             "applicant.father_given_name": "JOSE", "applicant.mother_family_name": "LIMA", "applicant.mother_given_name": "MARIA"}
    from portal.bank import PORTAL_DOC_ID
    from review.state import save_bundle, record_decision
    from synthetic_documents import process_retained_documents
    import subject_attribution as subjects
    import critical_review as critical

    d, source = tmp_path / "bundle", tmp_path / "source"
    result = process_retained_documents(d.name, source, [("us passport.pdf", US_PASSPORT), ("1040.pdf", TAX)])
    # These are explicit fictional intake answers, not reads from a phantom passport.
    for graph in (result.graph, result.raw_graph):
        for key, value in facts.items():
            graph.add_source(key, PORTAL_DOC_ID, "intake_questionnaire", value, value, 0.95, tier=3)
    save_bundle(result, d, source)
    petitioner = subjects.add_person(d, "Fictional Michael Exemplo", "petitioner", "Fictional Reviewer", "paralegal")
    review_keys = set()
    for row in subjects.views(d):
        if not row["slots"]:
            subjects.assign(d, row["instance_id"], row["fingerprint"], {}, "Fictional Reviewer", "paralegal",
                            reference_only=True, note="Tax reader has no supported subject slot; retain this original as reference and enter reviewed tax answers explicitly.")
            continue
        review_keys.update(f["key"] for f in row["facts"])
        subjects.assign(d, row["instance_id"], row["fingerprint"], {slot: petitioner["id"] for slot in row["slots"]},
                        "Fictional Reviewer", "paralegal")
    # Confirm the retained fictional originals through the production review API.
    for key, context in critical.context(d).items():
        if key not in review_keys:
            continue
        item = {"id": "fact:" + key, "kind": "fact", "level": "review", "title": "Review retained fictional source", "group": "other",
                "facts": [{"key": key, "input": {"type": "text"}}], "actions": ["confirm", "set", "blank"]}
        record_decision(d, item, {"action": "confirm", "reviewer": "Fictional Reviewer", "role": "paralegal", "values": {},
                                 "evidence_fingerprints": {key: context["fingerprint"]}})
    docs = {"us passport.pdf": "us_passport", "marriage.pdf": "marriage_certificate", "lease.pdf": "lease", "certidao.pdf": "birth_certificate",
            "passport.pdf": "passport", "i94.pdf": "i94", "1040.pdf": "tax_return", "court order.pdf": "sij_order"}
    for name in docs:
        if (source / name).exists():
            continue
        w = PdfWriter()
        w.add_blank_page(width=612, height=792)
        with open(source / name, "wb") as fh:
            w.write(fh)
    metadata = json.loads((d / "meta.json").read_text())
    metadata["classifications"].update(docs)
    (d / "meta.json").write_text(json.dumps(metadata))
    fill_pdf(schema_path.path("template", "i485"), {}, d / "i485_filled.pdf")
    family.answer(d, {"i864.tax_year1": "2024", "i864.tax_income1": "52300"}, "Fictional Reviewer", "paralegal",
                  note="Explicit fictional tax answers after reviewing the retained 1040; its unsupported subject read remains reference only.")
    if answered:
        family.answer(d, {"family.previous_petition": "No", "family.adjust_city": "BOSTON", "family.adjust_state": "MA", "petitioner.ssn": "999-01-2345",
                          "petitioner.sex": "M", "petitioner.birth_city": "BOSTON", "petitioner.citizenship_how": "birth", "petitioner.times_married": "1",
                          "petitioner.daytime_phone": "6175550100", "petitioner.parent1_family_name": "EXEMPLO", "petitioner.parent1_given_name": "JOHN",
                          "petitioner.parent1_sex": "M", "petitioner.parent1_country_of_birth": "USA", "petitioner.parent1_city_of_residence": "BOSTON",
                          "petitioner.parent2_family_name": "EXEMPLO", "petitioner.parent2_given_name": "MARY", "petitioner.parent2_sex": "F",
                          "petitioner.parent2_country_of_birth": "USA", "petitioner.parent2_city_of_residence": "BOSTON", "petitioner.address1_date_from": "2020-01-01",
                          "petitioner.employer1_name": "ACME LLC", "petitioner.employer1_occupation": "DRIVER", "petitioner.employer1_date_from": "2018-03-01",
                          "i864.household_size": "2", "i864.current_income": "52300", "petitioner.military": "No"}, "Sam")
    return d


def test_the_petitioner_comes_from_the_spouse_and_the_category_from_the_answers(tmp_path):
    d = _case(tmp_path)
    s = {q["key"]: q for q in family.status(d)["questions"]}
    assert s["family.relationship"]["value"] == "Spouse" and s["petitioner.status"]["value"] == "USC"
    assert s["petitioner.family_name"]["value"] == "EXEMPLO" and s["petitioner.dob"]["value"] == "1980-01-01"
    assert s["applicant.filing_category"]["value"] == "Spouse of U.S. citizen"
    from review.state import reviewed_graph

    g = reviewed_graph(d)  # the I-485 is refilled from this: its Part 2 box follows
    assert g.get("applicant.filing_category").value == "Spouse of U.S. citizen" and g.get("i864.tax_year1").value == "2024"


def test_the_sponsors_income_is_checked_against_this_years_guidelines(tmp_path, monkeypatch):
    guides = family.settings()["poverty_guidelines"]  # USCIS I-864P, effective 2026-03-01
    assert family.minimum_income(guides, "MA", 2, False) == 27050  # 48 states: 125%
    assert family.minimum_income(guides, "AK", 2, False) == 33813 and family.minimum_income(guides, "HI", 2, False) == 31113
    assert family.minimum_income(guides, "MA", 2, True) == 21640  # active duty, petitioning for a spouse: 100%
    assert family.minimum_income(guides, "MA", 10, False) == 69650 + 2 * 7100
    d = _case(tmp_path)
    assert not any("I-864" in p and "below" in p for p in family.status(d)["problems"])  # $52,300 for 2
    family.answer(d, {"i864.current_income": "20000"}, "Sam")
    assert any("below 125%" in p and "$27,050" in p for p in family.status(d)["problems"])
    monkeypatch.setattr(family, "settings", lambda: {})
    assert any("I-864P" in p for p in family.status(d)["problems"])  # never guessed when unset


def test_the_fees_come_from_the_current_fee_schedule():
    from fill.cover_letter import fee_text, load_config

    fees = family.fees()
    assert fees["edition"] == "10/01/26" and fees["paper"]["i130"] == 675 and fees["pl_119_21"]["i360_sij"] == 250
    family_letter = fee_text(load_config(schema_path.path("cover_letter", "family")))
    assert "$675" in family_letter and "$1,440" in family_letter and "$260" in family_letter and "$2,375" in family_letter
    sij_letter = fee_text(load_config(schema_path.path("cover_letter", "i360")))
    assert "$250" in sij_letter and "Public Law 119-21" in sij_letter and "no USCIS filing fee" in sij_letter


def test_the_family_packet_fills_every_form_from_the_same_case(tmp_path, monkeypatch):
    monkeypatch.setattr(packet, "_today", lambda: date(2026, 10, 1))
    d = _case(tmp_path)
    schema = packet.load_filing("family")
    row = {"summary": {"name": "ANA SOUZA"}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}
    plan = packet.plan(d, row, schema)
    assert [ex["id"] for ex in plan["exhibits"]] == ["petitioner_status", "marriage", "bona_fides", "birth", "passport", "admission", "sponsor"]
    assert {f["doc"] for f in plan["left_out"]} == {"court order.pdf"}
    m = packet.build(d, row, "Sam", schema)
    assert (d / "packet_family.pdf").exists() and not (d / "packet.pdf").exists()
    # one card authorization per form (USCIS: a separate payment for each request), each on top of what it pays for
    assert [s["tab"] for s in m["sections"]][1:12] == ["G-1450 (I-130, $675)", "Cover letter", "G-28 (I-130)", "I-130", "I-130A", "G-28",
                                                     "G-1450 (I-485, $1,440)", "I-485", "I-864", "G-1450 (I-765, $260)", "I-765"]
    assert [p["amount"] for p in m["payments"]] == [675, 1440, 260]
    assert {s["who"] for s in m["signatures"]} >= {"client", "petitioner", "attorney"}

    def boxes(name):
        return {k.rsplit(".", 1)[-1]: v.get("/V") for k, v in PdfReader(str(d / name)).get_fields().items()}

    i130 = boxes("i130_filled.pdf")
    assert i130["Pt1Line1_Spouse[0]"] == "/Y" and i130["Pt2Line36_USCitizen[0]"] == "/Y" and i130["Pt2Line23a_checkbox[0]"] == "/Y"
    assert (i130["Pt2Line4a_FamilyName[0]"], i130["Pt2Line4b_GivenName[0]"]) == ("EXEMPLO", "MICHAEL")  # the petitioner
    assert (i130["Pt4Line4a_FamilyName[0]"], i130["PtLine20a_FamilyName[0]"]) == ("SOUZA", "SOUZA")  # the beneficiary, also the petitioner's spouse
    assert i130["Pt2Line11_SSN[0]"] == "999012345" and i130["Pt4Line20_Yes[0]"] == "/Y"
    i130a = boxes("i130a_filled.pdf")
    assert (i130a["Pt1Line10_FamilyName[0]"], i130a["Pt1Line16_GivenName[0]"]) == ("SOUZA", "MARIA")
    i864 = boxes("i864_filled.pdf")
    assert i864["P1_Line1a-f_CB[0]"] == "/1A" and i864["P4_Line10_SocialSecurityNumber[0]"] == "999012345" and i864["Override[0]"] == "2"
    assert (i864["P6_Line19a_TaxYear[0]"], i864["P6_Line19a_TotalIncome[0]"]) == ("2024", "52300")
    g28 = boxes("g28_i130_filled.pdf")
    assert g28["Line1b_ListFormNumber[0]"] == "I-130, I-130A, I-864" and g28["Line4_Checkbox[3]"] == "/P"
    first = next(s["first_page"] for s in m["sections"] if s["tab"] == "Cover letter")  # after the I-130's G-1450, on top
    letter = PdfReader(str(d / "packet_family.pdf")).pages[first - 1].extract_text()
    assert "Petitioner: Michael Exemplo" in letter and "Beneficiary: Ana Souza" in letter and "P.O. BOX 805887" in letter


def test_the_review_server_offers_each_filing_and_saves_its_answers(tmp_path):
    from review.server import ReviewApp
    from test_review import TEMPLATE, _REPO, client as make_client  # noqa: F401

    bundle = make_client.__wrapped__(tmp_path)
    app = ReviewApp(bundle.parent, schema_path.path("field_map", "i485"), TEMPLATE, None)
    for filing in ("i485", "i360", "family"):
        assert app.packet_plan(bundle.name, filing)["filing"] == filing
    plan = app.packet_plan(bundle.name, "family")
    assert any(q["key"] == "petitioner.status" for q in plan["family"]["questions"])
    app.family_answer(bundle.name, {"values": {"petitioner.status": "USC"}, "reviewer": "Sam"})
    assert {q["key"]: q["value"] for q in app.packet_plan(bundle.name, "family")["family"]["questions"]}["petitioner.status"] == "USC"
    with pytest.raises(ValueError):
        app.family_answer(bundle.name, {"values": {"petitioner.status": "USC"}, "reviewer": ""})  # who answered is always recorded
    with pytest.raises(ValueError):
        app.packet_plan(bundle.name, "naturalization")  # not a filing (yet)
