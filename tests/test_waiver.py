"""The provisional unlawful presence waiver (src/waiver.py): who can file under
8 CFR 212.7(e), the fee (G-1055 10/01/26), where it goes, and the filled
boxes -- including the history pairs whose Yes is the second box. Every client
value is CONSTRUCTED.
"""

import re
from datetime import date

from pypdf import PdfReader

import waiver
from factgraph import FactGraph
from fill.companion import fill_companions, load_profile

TODAY = date(2026, 10, 1)


def _graph(**extra):
    g = FactGraph("c")
    for key, value in ({"applicant.family_name": "EXEMPLO", "applicant.given_name": "ANA", "applicant.dob": "1995-03-14", "applicant.country_of_birth": "BRAZIL",
                        "applicant.physical_street": "10 EXAMPLE ST", "applicant.physical_city": "SOMERVILLE", "applicant.physical_state": "MA",
                        "applicant.physical_zip": "02143", "family.relationship": "Spouse", "petitioner.status": "USC", "petitioner.family_name": "EXEMPLO",
                        "petitioner.given_name": "JOAO", "applicant.filing_category": "Spouse of U.S. citizen"}
                       | {k.replace("__", "."): v for k, v in extra.items()}).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _notice(g, receipt, form, kind, when):
    g.add_source(f"folder.uscis_case.{receipt}.{kind}_{when.replace('-', '')}", f"{kind}.pdf", "uscis_notice", receipt, f"{form} {kind.upper()}, {when}", 0.9)


def test_a_citizens_spouse_with_an_approved_i130(tmp_path):
    g = _graph(waiver__iv_fee_paid="Yes", waiver__q39b_killing="No", waiver__q40a_armed_group="Yes")
    _notice(g, "IOE0999000555", "I-130", "approval", "2026-04-01")
    waiver.derive(g, TODAY)
    v = lambda k: g.get(k).value  # noqa: E731
    assert v("waiver.basis") == "Immediate relative (I-130)" and v("waiver.petition_receipt") == "IOE0999000555"
    assert v("waiver.relative_is") == "U.S. citizen spouse" and v("waiver.relative_given_name") == "JOAO"
    assert v("waiver.physical_city") == "SOMERVILLE" and v("waiver.mailing_city") == "SOMERVILLE"
    assert waiver.fee(g, TODAY)[0] == 795 and waiver.problems(tmp_path, g, TODAY) == []
    # the G-1055 (10/01/26) charges nothing for a VAWA self-petitioner, as for an SIJ (src/vawa.py marks the case)
    g.add_source("vawa.classification", "test", "test", "Spouse of a U.S. citizen", "Spouse of a U.S. citizen", 1.0)
    assert waiver.fee(g, TODAY) == (0, "a VAWA self-petitioner: no fee (G-1055)")
    assert "P.O. BOX 4599" in waiver.letter(g, TODAY)["mail_to"]
    profile = load_profile()
    profile["forms"] = {"i601a": profile["forms"]["i601a"]}
    fill_companions(g, tmp_path, profile)
    f = {re.split(r"(?<!\\)\.", n)[-1]: x.get("/V") for n, x in PdfReader(str(tmp_path / "i601a_filled.pdf")).get_fields().items()}
    assert f["Pt3Line4_Option2[0]"] == "/2" and f["Pt4Line2a_Checkbox[0]"] == "/A" and f["Pt3Line3a_USCISReceiptNumber[0]"] == "IOE0999000555"
    assert f["Pt1Checkbox38b_Checkbox[0]"] == "/N" and f["Pt1Checkbox38b_Checkbox[1]"] in (None, "/Off")   # 39.B: No is the FIRST box
    assert f["Pt1Checkbox39a_Checkbox[1]"] == "/Y"                                                          # 40.A: Yes is the SECOND box


def test_who_212_7_e_4_shuts_out(tmp_path):
    young = _graph(applicant__dob="2010-01-01", waiver__iv_fee_paid="No", waiver__q28_proceedings="Not administratively closed (or recalendared)",
                   waiver__q29_final_order="Yes", waiver__q30b_reinstated="Yes")
    _notice(young, "IOE0999000555", "I-485", "receipt", "2026-05-01")
    out = " ".join(waiver.problems(tmp_path, young, TODAY))
    for cite in ("(4)(i)", "(4)(ii)", "(3)(iv)", "(4)(iii)", "(4)(iv)", "(4)(v)", "(4)(vi)"):
        assert f"212.7(e){cite}" in out, cite
    sibling = _graph(family__relationship="Sibling", applicant__filing_category="Sibling of U.S. citizen")
    waiver.derive(sibling, TODAY)
    assert any("isn't a qualifying relative" in p for p in waiver.problems(tmp_path, sibling, TODAY))
