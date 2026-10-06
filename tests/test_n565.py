"""The replacement naturalization or citizenship certificate (src/n565.py): who can ask for what (uscis.gov/n-565 and the N-565
Instructions 02/27/25), the fee (G-1055 10/01/26: $555, $0 for a USCIS error), the Phoenix lockbox, the filled form -- whose Part 4
boxes are named by what they hold, not by their on-values -- and where the case path offers it. Every client value is CONSTRUCTED.
"""

import re
from datetime import date

from pypdf import PdfReader

import enotice
import journey
import n565
import packet
from factgraph import FactGraph
from fill.companion import fill_companions, load_profile

TODAY = date(2026, 10, 2)


def _graph(**extra):
    g = FactGraph("c")
    for key, value in ({"applicant.family_name": "EXEMPLO SOUZA", "applicant.given_name": "ANA CLARA", "applicant.dob": "1990-03-14",
                        "applicant.country_of_birth": "BRAZIL", "applicant.citizenship": "BRAZIL", "applicant.a_number": "A099000123",
                        "applicant.marital_status": "Married", "applicant.physical_street": "10 EXAMPLE ST", "applicant.physical_city": "SOMERVILLE",
                        "applicant.physical_state": "MA", "applicant.physical_zip": "02143"}
                       | {k.replace("__", "."): v for k, v in extra.items()}).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _notice(g, receipt, form, kind, when):
    g.add_source(f"folder.uscis_case.{receipt}.{kind}_{when.replace('-', '')}", f"{kind}.pdf", "uscis_notice", receipt, f"{form} {kind.upper()}, {when}", 0.9)


def _boxes(path):
    return {re.split(r"(?<!\\)\.", n)[-1]: x.get("/V") for n, x in PdfReader(str(path)).get_fields().items()}


def test_what_the_case_already_says(tmp_path):
    g = _graph()
    _notice(g, "IOE0999000555", "N-400", "approval", "2020-06-01")
    n565.derive(g, TODAY)
    v = lambda k: g.get(k).value  # noqa: E731
    assert v("n565.document") == n565.NATURALIZATION and v("n565.cert_family_name") == "EXEMPLO SOUZA" and v("n565.cert_dob") == "1990-03-14"
    assert v("n565.mailing_city") == "SOMERVILLE" and v("n565.mailing_country") == "USA" and v("n565.marital_status") == "Married"
    assert v("n565.lives_abroad") == "No" and g.get("n565.lost_citizenship") is None  # a sworn answer is never guessed
    named = _graph(n565__reason=n565.NAME, n565__name_change_how=n565.MARRIAGE, n565__name_change_date="2024-05-01")
    n565.derive(named, TODAY)  # the date goes in the box of the way the name changed
    assert named.get("n565.name_date_marriage").value == "2024-05-01" and named.get("n565.name_date_court") is None


def test_the_fee_the_address_and_the_g1145():
    assert n565.fee(_graph(), TODAY)[0] == 555
    assert n565.fee(_graph(n565__reason=n565.ERROR), TODAY)[0] == 0 and n565.letter(_graph(n565__reason=n565.ERROR), TODAY)["no_payment"]
    assert n565.letter(_graph(), TODAY)["mail_to"] == ["USCIS", "Attn: N-565", "P.O. Box 20050", "Phoenix, AZ 85036-0050"]
    assert "$555" in n565.letter(_graph(), TODAY)["fees"] and "n565" in enotice.LOCKBOX_FILINGS
    text = " ".join(n["text"] for n in n565.notes(_graph(n565__reason=n565.ERROR, n565__document=n565.NATURALIZATION), TODAY))
    assert "$0" in text and "Nebraska Service Center" in text and "swore to it" in text
    assert "Filed online it is also $0" in text and "$505" not in text                           # the schedule makes a USCIS error $0 however it is filed
    assert "Filed online it is $505" in " ".join(n["text"] for n in n565.notes(_graph(), TODAY))   # a Naturalization certificate's own error is USCIS's only if it was theirs


def test_more_questions_wait_only_for_the_reason():
    assert n565.more_to_come(_graph()) is True and n565.more_to_come(_graph(n565__reason=n565.ERROR)) is False


def test_what_each_reason_needs(tmp_path):
    out = lambda g: " ".join(n565.problems(tmp_path, g, TODAY))  # noqa: E731
    assert "photo ID" in out(_graph(n565__reason=n565.LOST)) and "police report and/or a sworn statement" in out(_graph(n565__reason=n565.LOST))
    assert "Evidence of the name change" in out(_graph(n565__reason=n565.NAME))
    assert "birth certificate showing the sex at birth" in out(_graph(n565__reason=n565.SEX))
    both = out(_graph(n565__reason=n565.BIRTHDATE, n565__document=n565.NATURALIZATION))
    assert "only for a new Certificate of Citizenship" in both
    assert "two identical color passport-style photos" in out(_graph(n565__lives_abroad="Yes"))
    assert "two identical" not in out(_graph(n565__lives_abroad="No"))


def test_the_filled_form_and_its_g28(tmp_path):
    g = _graph(n565__document=n565.NATURALIZATION, n565__reason=n565.ERROR, n565__error_what="Sex", n565__error_explanation="The sex is printed M.",
               n565__lost_citizenship="No", n565__certificate_number="A99000123", n565__issued_by="USCIS BOSTON", n565__issued_on="2020-07-04",
               n565__cert_given_name="ANA CLARA", n565__cert_family_name="EXEMPLO SOUZA", n565__cert_dob="1990-03-14", n565__cert_country_of_birth="BRAZIL",
               n565__former_citizenship="BRAZIL")
    n565.derive(g, TODAY)
    profile = load_profile()
    profile["forms"] = {k: profile["forms"][k] for k in ("g28_n565", "n565")}
    done = fill_companions(g, tmp_path, profile)
    f = _boxes(tmp_path / "n565_filled.pdf")
    assert f["Part3_Item1[1]"] == "/NN" and f["Pt3CheckBox4[0]"] == "/Y" and f["Pt4_Item1_Gender[0]"] == "/Widowed"   # Sex is the box named Gender, on-value /Widowed
    assert f["Part2_Item6[0]"] == "/No" and f["Part2_Item5[0]"] == "/Married" and f["P1Line6_DateOfDeclaration[0]"] == "07/04/2020"
    assert f["P1Line1_FamilyName[0]"] == f["P1Line1_FamilyName[1]"] == "EXEMPLO SOUZA" and f["ANum[0]"] == f["ANum[1]"] == "099000123"
    assert f["P2Line3_State[0]"].strip() == "MA" and f["Pt4_AdditionalInfo[0]"] == "The sex is printed M."
    assert _boxes(tmp_path / "g28_n565_filled.pdf")["Line1b_ListFormNumber[0]"] == "N-565"
    assert not [k for k in done["n565"]["left_blank"] if k.startswith("n565.cert") or k in ("applicant.family_name", "n565.mailing_street")]


def test_the_sections_follow_the_reason():
    g = _graph(n565__document=n565.NATURALIZATION)
    assert "Lost, stolen or destroyed" not in {s for s in [sec[0] for sec in n565.SECTIONS if len(sec) > 3 and sec[3](g)]}
    assert "Lost, stolen or destroyed" in {sec[0] for sec in n565.SECTIONS if len(sec) > 3 and sec[3](_graph(n565__reason=n565.LOST))}
    special = _graph(n565__document=n565.SPECIAL)
    shown = {sec[0] for sec in n565.SECTIONS if len(sec) <= 3 or sec[3](special)}
    assert "Special certificate for a foreign country" in shown and "The reason" not in shown


def test_the_packet_is_on_the_engine_and_the_case_path_offers_it(tmp_path):
    forms = load_profile()["forms"]
    schema = packet.load_filing("n565")
    assert schema["forms"] == ["g28_n565", "n565"] and all(f in forms for f in schema["forms"]) and schema["filing"] == "n565"
    g = _graph()
    _notice(g, "IOE0999000555", "N-400", "approval", "2020-06-01")
    journey.mark(tmp_path, "oath", "Paralegal", value={"date": "2020-07-04"})
    j = journey.journey(tmp_path, TODAY, graph=g)
    assert j["stage"] == "citizen"
    assert not any(f["filing"] == "n565" for f in j["next_filings"])   # not offered until a person records why: the end of the road otherwise
    recorded = _graph(n565__reason=n565.LOST)
    _notice(recorded, "IOE0999000555", "N-400", "approval", "2020-06-01")
    now = next(f for f in journey.journey(tmp_path, TODAY, graph=recorded)["next_filings"] if f["filing"] == "n565")
    assert now["now"] is True and "lost, stolen or destroyed" in now["label"]
