"""The fee waiver (src/fee_waiver.py) and the expedite request (src/expedite.py):
150% of the poverty guidelines from the firm's own table, the I-912 carried
inside the application it waives (no card authorization for the waived fee,
the letter saying so), and the expedite sheet with USCIS's conditions.
Every client value is CONSTRUCTED.
"""

import json
import re
from datetime import date

from pypdf import PdfReader

import expedite
import fee_waiver
import packet
import payment
from factgraph import FactGraph
from fill.companion import fill_companions, load_profile

TODAY = date(2026, 10, 1)


def _graph(**extra):
    g = FactGraph("c")
    for key, value in ({"applicant.family_name": "EXEMPLO", "applicant.given_name": "ANA", "applicant.dob": "1990-03-14", "applicant.a_number": "A099999999",
                        "applicant.physical_state": "MA"} | {k.replace("__", "."): v for k, v in extra.items()}).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _notice(g, receipt, form, kind, when):
    g.add_source(f"folder.uscis_case.{receipt}.{kind}_{when.replace('-', '')}", f"{kind}.pdf", "uscis_notice", receipt, f"{form} {kind.upper()}, {when}", 0.9)


def test_150_percent_from_the_firms_poverty_table():
    # the contiguous-states guideline the firm records (I-864P, effective 2026): 4 people $33,000 -> 150% $49,500
    assert fee_waiver.threshold(_graph(feewaiver__household_size="4"))[0] == 49500
    assert fee_waiver.threshold(_graph(feewaiver__household_size="1"))[0] == (21640 - 5680) * 3 // 2   # one person: two's less one step
    g = _graph(feewaiver__filing=fee_waiver.LABELS["n400"], feewaiver__basis_income="Yes", feewaiver__household_size="2",
               feewaiver__own_income="40,000")
    fee_waiver.derive(g, TODAY)
    assert g.get("feewaiver.total_income").value == "40,000" and g.get("feewaiver.forms").value == "N-400"
    assert any("above 150%" in p for p in fee_waiver.problems(None, g, TODAY))                       # $40,000 > $32,460


def test_the_i912_travels_inside_the_n400(tmp_path):
    d = tmp_path / "case"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"classifications": {}}), encoding="utf-8")
    g = _graph(feewaiver__filing=fee_waiver.LABELS["n400"], feewaiver__basis_benefit="Yes", feewaiver__benefit_type="MEDICAID")
    g.save(d / "fact_graph.json")
    schema = packet.for_case(packet.load_filing("n400"), d)
    assert schema["fee_waiver"] and schema["forms"][-1] == "i912" and "fee waiver's evidence" in schema["handwork"][-1]["text"]
    assert payment.payments(schema, d, g, TODAY) == []                                           # the waived fee: no card authorization
    letter = packet._letter_config(schema, d)
    assert "fee waiver (Form I-912)" in letter["fees"] and letter["no_payment"] and letter["form_order"][-1] == "i912"
    assert "fee_waiver" not in packet.for_case(packet.load_filing("i90"), d)                      # only the application it goes with
    fee_waiver.derive(g, TODAY)
    profile = load_profile()
    profile["forms"] = {"i912": profile["forms"]["i912"]}
    fill_companions(g, tmp_path, profile)
    f = {re.split(r"(?<!\\)\.", n)[-1]: x.get("/V") for n, x in PdfReader(str(tmp_path / "i912_filled.pdf")).get_fields().items()}
    assert f["P1_Line1_Checkbox[0]"] == "/A" and f["Part3_Line1_FormsFiled1[0]"] == "N-400" and f["Part4_Line1_TypeofBene1[0]"] == "MEDICAID"


def test_the_expedite_sheet_and_usciss_conditions(tmp_path):
    g = _graph(expedite__criterion=expedite.CRITERIA[2], expedite__needed_by="2026-10-10", expedite__reason="Her mother is gravely ill.",
               expedite__evidence="Hospital letter\nBirth certificate")
    _notice(g, "IOE0999000700", "I-131", "receipt", "2026-07-01")
    expedite.derive(g, TODAY)
    assert g.get("expedite.receipt").value == "IOE0999000700" and g.get("expedite.form").value == "I-131"
    assert any("Emergency Travel" in p for p in expedite.problems(tmp_path, g, TODAY))                 # 9 days away: not an expedite
    _notice(g, "IOE0999000700", "I-131", "rfe", "2026-09-01")
    assert any("request for evidence is open" in p for p in expedite.problems(tmp_path, g, TODAY))
    expedite.render(tmp_path, g, TODAY)
    text = PdfReader(str(tmp_path / "expedite_request.pdf")).pages[0].extract_text()
    assert "EXPEDITE REQUEST" in text and "IOE0999000700" in text and "Hospital letter" in text and "800-375-5283" in text
