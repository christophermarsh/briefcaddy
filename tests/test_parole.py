"""Humanitarian parole for someone outside the U.S. (src/parole.py): the I-131's Part 1, item 7 with an I-134 for each person, the fee
(G-1055 10/01/26: $630 each, $0 for the General Fee Exemptions), the Dallas lockbox, one set of forms for each person, and the filled
forms. Every client value is CONSTRUCTED.
"""

from datetime import date

from pypdf import PdfReader

import enotice
import packet
import parole
from factgraph import FactGraph
from fill.companion import fill_companions, load_profile

TODAY = date(2026, 10, 2)


def _graph(**extra):
    g = FactGraph("c")
    for key, value in ({"applicant.family_name": "EXEMPLO SOUZA", "applicant.given_name": "ANA CLARA", "applicant.dob": "1985-03-14", "applicant.sex": "F",
                        "applicant.country_of_birth": "BRAZIL", "applicant.citizenship": "BRAZIL", "applicant.birth_city": "RECIFE",
                        "applicant.physical_street": "10 EXAMPLE ST", "applicant.physical_city": "SOMERVILLE", "applicant.physical_state": "MA",
                        "applicant.physical_zip": "02143", "applicant.daytime_phone": "6175550101", "applicant.email": "ana@example.test",
                        "firm.preparer_family_name": "EXEMPLO", "firm.preparer_given_name": "MARIA"}
                       | {k.replace("__", "."): v for k, v in extra.items()}).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _someone_else(**extra):
    answers = {"parole__for_whom": parole.OTHER, "parole__count": "2", "parole__sponsor_is": parole.SUPPORTER_IS[0], "parole__sponsor_status": "U.S. citizen",
               "parole__sponsor_employment": "Employed", "parole__sponsor_job": "NURSE", "parole__sponsor_employer": "EXAMPLE HOSPITAL",
               "parole__sponsor_income": "62000", "parole__sponsor_relationship": "SISTER", "parole__reason": "Needs surgery not available at home.",
               "parole__stay": "6 months", "parole__embassy_city": "RECIFE", "parole__embassy_country": "BRAZIL",
               "parole__sponsor_asset1_type": "Savings - Bank Account", "parole__sponsor_asset1_amount": "15000", "parole__sponsor_asset2_type": "Checking - Bank Account",
               "parole__sponsor_asset2_amount": "3500", "parole__sponsor_contributions": "No", "parole__sponsor_other_i134s": "0", "parole__sponsor_dependents": "2"}
    for n, name in ((1, "LUCIA"), (2, "PEDRO")):
        answers |= {f"parole__b{n}_given_name": name, f"parole__b{n}_family_name": "EXEMPLO", f"parole__b{n}_dob": "1990-05-05", f"parole__b{n}_sex": "F" if n == 1 else "M",
                    f"parole__b{n}_street": "RUA EXEMPLO 10", f"parole__b{n}_city": "RECIFE", f"parole__b{n}_country": "BRAZIL", f"parole__b{n}_citizenship": "BRAZIL",
                    f"parole__b{n}_country_of_birth": "BRAZIL", f"parole__b{n}_city_of_birth": "RECIFE", f"parole__b{n}_marital_status": "Single",
                    f"parole__b{n}_in_proceedings": "No", f"parole__b{n}_relationship": "SISTER"}
    return parole.derive(_graph(**(answers | extra)), TODAY)


class _Boxes:
    """A filled form's boxes by the tail of their full name ("P4[0].Part2_Line1_FamilyName[0]", or just the last part): the I-131 and
    I-134 repeat short names across pages, so a name whose boxes disagree is an error, not a guess."""

    def __init__(self, path):
        self.fields = {name: f.get("/V") for name, f in PdfReader(str(path)).get_fields().items()}

    def __getitem__(self, tail):
        hits = [v for name, v in self.fields.items() if name == tail or name.endswith("." + tail)]
        assert hits and len({str(h) for h in hits}) == 1, f"{tail}: {hits}"   # the name is printed again on the last page: the copies agree
        return hits[0]


def _fields(path):
    return _Boxes(path)


def test_who_the_request_is_for_and_how_many():
    me = parole.derive(_graph(parole__for_whom=parole.SELF), TODAY)
    assert parole.people(me) == [1] and me.get("parole.count").value == "1" and me.get("parole.b1_family_name").value == "EXEMPLO SOUZA"
    assert me.get("parole.b1_relationship").value.startswith("the client")
    g = _someone_else()
    assert parole.people(g) == [1, 2] and parole.people(_graph(parole__for_whom=parole.OTHER)) == []
    assert g.get("parole.sponsor_family_name").value == "EXEMPLO SOUZA" and g.get("parole.sponsor_mailing_city").value == "SOMERVILLE"
    assert g.get("parole.sponsor_assets_total").value == "18500" and g.get("parole.sponsor_mailing_same").value == "Yes"


def test_the_fee_the_address_and_one_payment_for_each_person():
    g = _someone_else()
    assert parole.fee(g, TODAY)[0] == 630 and parole.address(g)[0] == ["USCIS", "Attn: HP", "P.O. Box 660865", "Dallas, TX 75266-0865"]
    pays = parole.payments(g, TODAY, ["x"])
    assert [(p[1], p[2], p[3]) for p in pays] == [("I-131", 630, "Form I-131 filing fee for LUCIA EXEMPLO"), ("I-131", 630, "Form I-131 filing fee for PEDRO EXEMPLO")]
    exempt = _someone_else(parole__b2_exemption=parole.EXEMPT[1])
    assert parole.fee(exempt, TODAY, 2)[0] == 0 and [p[2] for p in parole.payments(exempt, TODAY, ["x"])] == [630, 0]
    text = parole.letter(exempt, TODAY)["fees"]
    assert "$630 for LUCIA EXEMPLO" in text and parole.letter(exempt, TODAY)["no_payment"] is False
    assert "No fee is due for PEDRO EXEMPLO: a current or former U.S. armed forces service member" in text   # the exempt person's $0 and the reason
    assert "parole" in enotice.LOCKBOX_FILINGS
    note = " ".join(n["text"] for n in parole.notes(g, TODAY))
    assert "$630 for each person" in note and "$1,260 in all" in note and "$580 each" in note
    assert "$1,020 for a person who enters before 10/16/2026 and $1,050 from then on" in note and "postmarked" not in note   # CBP charges at the port
    assert "$1,050" in " ".join(n["text"] for n in parole.notes(g, date(2026, 10, 16)))   # the FY 2027 alert's amount, from its date
    one = " ".join(n["text"] for n in parole.notes(exempt, TODAY))
    assert "person 1: $630" in one and "person 2: $0 (no fee: a current or former U.S. armed forces service member" in one and "$630 in all" in one
    assert "No filing fee is due" in parole.letter(_someone_else(parole__b1_exemption=parole.EXEMPT[1], parole__b2_exemption=parole.EXEMPT[1]), TODAY)["fees"]


def test_a_client_filing_for_themself_on_a_protected_track_gets_the_exemption():
    plain = parole.derive(_graph(parole__for_whom=parole.SELF), TODAY)
    assert plain.get("parole.b1_exemption").value == parole.NO_EXEMPTION and parole.fee(plain, TODAY, 1)[0] == 630
    vawa = parole.derive(_graph(parole__for_whom=parole.SELF, vawa__classification="I. spouse"), TODAY)
    assert vawa.get("parole.b1_exemption").value == parole.EXEMPT[5] and parole.fee(vawa, TODAY, 1)[0] == 0
    t = parole.derive(_graph(parole__for_whom=parole.SELF, tvisa__victim="Yes"), TODAY)
    assert t.get("parole.b1_exemption").value == parole.EXEMPT[3]
    u = parole.derive(_graph(parole__for_whom=parole.SELF, uvisa__cert_requested="2026-01-01"), TODAY)
    assert u.get("parole.b1_exemption").value == parole.EXEMPT[4]
    # for someone else the exemption turns on THAT person: a VAWA case does not make it for the beneficiary
    other = _someone_else(vawa__classification="I. spouse")
    assert other.get("parole.b1_exemption").value == parole.NO_EXEMPTION


def test_part_3_and_part_4_are_asked_and_filled(tmp_path):
    import filing_questions
    import pytest

    assert filing_questions._checked("Height", parole.HEIGHT, "5'7\"") == "5'7\"" and filing_questions._checked("Weight", parole.WEIGHT, "150") == "150"
    for bad, spec in (("170 cm", parole.HEIGHT), ("heavy", parole.WEIGHT)):   # the boxes take feet and inches, and digits
        with pytest.raises(ValueError):
            filing_questions._checked("x", spec, bad)
    keys = {key: required for _s, _w, items, *_ in parole.SECTIONS for key, _l, _sp, required in items}
    for k in ("ethnicity", "race", "height", "weight_lbs", "eye_color", "hair_color"):
        assert keys[f"parole.b1_{k}"] is False    # optional on the panel
    assert keys["parole.b1_prior_reentry_or_rtd"] is True and keys["parole.b1_prior_advance_parole"] is True   # the form skips to Part 8 only after item 3.c
    me = parole.derive(_graph(parole__for_whom=parole.SELF, applicant__ethnicity="Not Hispanic or Latino", applicant__race="White", applicant__height="5'7\"",
                              applicant__weight_lbs="150", applicant__eye_color="Brown", applicant__hair_color="Black"), TODAY)
    assert [me.get(f"parole.b1_{k}").value for k in ("ethnicity", "race", "height", "weight_lbs", "eye_color", "hair_color")] == \
        ["Not Hispanic or Latino", "White", "5'7\"", "150", "Brown", "Black"]
    g = _someone_else(parole__b2_ethnicity="Hispanic or Latino", parole__b2_race="Asian", parole__b2_height="5'11\"", parole__b2_weight_lbs="180", parole__b2_eye_color="Green",
                      parole__b2_hair_color="Gray", parole__b2_prior_reentry_or_rtd="Yes", parole__b2_prior_reentry_date="2019-04-01", parole__b2_prior_reentry_disposition="LOST",
                      parole__b2_prior_advance_parole="No")
    profile = load_profile()
    forms = {"i131_parole_2": packet.instance("i131_parole_2", profile["forms"])}
    fill_companions(parole.person_graph(g, "i131_parole", 2), tmp_path, {**profile, "forms": forms})
    f = _fields(tmp_path / "i131_parole_2_filled.pdf")
    assert f["P3_Line1_Ethnicity[1]"] == "/H" and f["P3_Line2_Race_Asian[0]"] == "/A" and f["P3_Line3_HeightFeet[0]"] == "5" and f["P3_Line3_HeightInches[0]"] == "11"
    assert [f[f"P3_Line4_Pound{i}[0]"] for i in (1, 2, 3)] == ["1", "8", "0"] and f["P3_Line5_EyeColor[6]"] == "/GRN" and f["P3_Line6_HairColor[2]"] == "/GRY"
    assert f["P4_Line2a_YesNo[0]"] == "/Y" and f["P4_Line2b_DateIssued[0]"] == "04/01/2019" and f["P4_Line2c_Disposition[0]"] == "LOST" and f["P4_Line3a_YesNo[1]"] == "/N"


def test_refugee_status_is_item_13_of_part_1_and_blank_until_answered():
    label = next(l for _s, _w, items, *_ in parole.SECTIONS for k, l, _sp, _r in items if k == "parole.refugee_status")
    assert label.startswith("Part 1, 13")
    assert parole.derive(_graph(parole__for_whom=parole.SELF), TODAY).get("parole.refugee_status") is None
    assert "Part 1, item 13" in " ".join(load_profile()["forms"]["i131_parole"]["attorney_completes"])


def test_more_questions_to_come_only_until_the_number_of_people_is_known(tmp_path):
    import filing_questions

    assert parole.more_to_come(_graph(parole__for_whom=parole.OTHER)) is True
    assert parole.more_to_come(_someone_else()) is False and parole.more_to_come(parole.derive(_graph(parole__for_whom=parole.SELF), TODAY)) is False
    assert filing_questions.module("parole").more_to_come is parole.more_to_come


def test_the_upload_guide_names_parole_once_the_guide_exists():
    """E2's upload_guide (schemas/law/online_filing.json) names every filing that can go online by upload: parole is one, so its entry is there."""
    import online_filing

    data = online_filing.rules()
    if "upload_guide" in data:
        assert "parole" in data["upload_guide"]["forms"]


def test_one_set_of_forms_for_each_person():
    g = _someone_else()
    schema = parole.case_schema(packet.load_filing("parole"), g, TODAY)
    assert schema["forms"] == ["g28_parole_1", "i131_parole_1", "i134_1", "g28_parole_2", "i131_parole_2", "i134_2"]
    companions = load_profile()["forms"]
    assert packet.form_output("i134_2") == "i134_2_filled.pdf" and packet.instance("i131_parole_2", companions)["short"] == "I-131 (2)"
    p2 = parole.person_graph(g, "i131_parole", 2)
    assert p2.get("parole.them.given_name").value == "PEDRO" and p2.get("parole.ben.sex").value == "M" and p2.get("parole.ben.i134_mailing_country").value == "BRAZIL"
    me = parole.derive(_graph(parole__for_whom=parole.SELF, parole__b1_street="RUA X 1", parole__b1_city="RECIFE", parole__b1_country="BRAZIL"), TODAY)
    own = parole.person_graph(me, "i131_parole", 1)
    assert own.get("parole.them.family_name") is None and own.get("parole.ben.family_name").value == "EXEMPLO SOUZA"   # items 16-27 stay blank


def test_the_forms_fill_for_one_person(tmp_path):
    g = _someone_else()
    profile = load_profile()
    forms = {fid: packet.instance(fid, profile["forms"]) if fid not in profile["forms"] else profile["forms"][fid] for fid in ("g28_parole_2", "i131_parole_2", "i134_2")}
    own = parole.person_graph(g, "i131_parole", 2)
    done = fill_companions(own, tmp_path, {**profile, "forms": forms})
    f = _fields(tmp_path / "i131_parole_2_filled.pdf")
    assert f["P3[0].CB_AppType[5]"] == "/23"                                                                              # Part 1, item 7
    assert f["Part2_Line1_FamilyName[0]"] == "EXEMPLO SOUZA" and f["P2_Line16_GivenName[0]"] == "PEDRO"      # the client, and the person
    assert f["P4_Line1_YesNo[1]"] == "/N" and f["P8_Line1_Explain[0]"] == "Needs surgery not available at home."
    assert f["P8_Line3b_CityOrTown[0]"] == "RECIFE" and f["P2_Line24_Country[0]"] == "BRAZIL"
    i = _fields(tmp_path / "i134_2_filled.pdf")
    assert i["Pt3Line17[0]"] == "/PD" and i["#subform[0].Pt1Line1_FamilyName[0]"] == "EXEMPLO SOUZA"
    assert i["#subform[0].Part2_Item11_City[0]"] == "SOMERVILLE" and i["P4[0].Part2_Item11_City[0]"] == "RECIFE"      # the supporter's city, the person's
    assert i["P8[0].#area[0].P2_Line8_DateOfBirth[0]"] == "05/05/1990"
    assert i["PG2[0].#area[0].P2_Line8_DateOfBirth[0]"] == "03/14/1985"                                                   # not the person's
    assert i["PG2[0].P2_Line10_ImmigrationStatus[0]"] == "/C" and i["PG2[0].StatusEmployment_CB[0]"] == "/E" and i["P3[0].Pt3Line116_Annual[0]"] == "62000"
    assert i["P3[0].Pt3Line1Cell1_TypeofAssetDropDownList[0]"] == "Savings - Bank Account" and i["P3[0].Pt3Line9Cell9_Total[0]"] == "18500"
    assert i["Pt3_Line4_Sex_CB[0]"] == "/M" and i["Pt3_Line8_MaritalStatus[0]"] == "/S"
    assert f["Part10_Line4_ApplicantSignature[0]"] is None   # nothing is signed for them
    assert not [k for k in done["i131_parole_2"]["left_blank"] if k.startswith(("parole.type", "parole.reason", "parole.them.family_name"))]
    assert not [k for k in done["i134_2"]["left_blank"] if k in ("parole.sponsor_family_name", "parole.ben.family_name", "parole.sponsor_income")]


def test_what_stops_the_request(tmp_path):
    out = lambda g: " ".join(parole.problems(tmp_path, g, TODAY))  # noqa: E731
    assert "How many people" in out(_graph(parole__for_whom=parole.OTHER))
    full = out(_someone_else())
    assert "biographical page" in full and "supporter's identity document" in full and "income and assets" in full
    assert "numbers only" in out(_someone_else(parole__sponsor_income="a lot"))
    assert "describe the specific contributions" in out(_someone_else(parole__sponsor_contributions="Yes"))
    assert "the job and the employer's name" in out(_someone_else(parole__sponsor_job="", parole__sponsor_employer=""))
    assert "only if THAT person qualifies" in out(_someone_else(parole__b1_exemption=parole.EXEMPT[2]))
    assert "future" in out(_someone_else(parole__b1_dob="2030-01-01"))


def test_the_packet_is_on_the_engine():
    forms = load_profile()["forms"]
    schema = packet.load_filing("parole")
    assert all(f.rsplit("_", 1)[0] in forms for f in schema["forms"]) and schema["filing"] == "parole"
    assert forms["i131_parole"]["template"] == forms["i131"]["template"] and forms["i134"]["template"] == "i134_template.pdf"
    assert "supporter" in packet.SIGNERS
