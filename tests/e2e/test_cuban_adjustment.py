"""A green card under the Cuban Adjustment Act, end to end through the screens, on the
made-up client case-cuban (tests/e2e/world.py: born in Cuba, paroled on 03/10/2025):

  the case page puts it on the Cuban Adjustment Act track and names the green card
  as the next filing -> its panel suggests the answers from the case, the fee and
  the family-based lockbox -> the work permit question is answered -> the packet is
  built with the G-1145 and one G-1450 per form, and the I-485 carries the CAA boxes.
"""

from __future__ import annotations

import json

from pypdf import PdfReader
from test_pathways import answer, build, next_filing, stage_is


def test_a_cuban_parolee_files_under_the_cuban_adjustment_act(world, paralegal):
    d = world["clients"] / "case-cuban"
    body = stage_is(paralegal, "case-cuban", "Green card under the Cuban Adjustment Act to prepare and file",
                    "Green card under the Cuban Adjustment Act (I-485): from 03/10/2026, one year in the U.S.")
    assert "Cuban Adjustment Act track" in body and "born in Cuba" in body, body[:1500]
    next_filing(paralegal, "case-cuban", "caa", "Green card under the Cuban Adjustment Act")
    body = paralegal.check("case-cuban-caa-panel")
    assert "Part 2, 3.f: the category" in body and "P.O. BOX 805887" in body and "$1,440" in body, body[:2000]
    category = paralegal.page.locator("tr").filter(has_text="How the client was let in").locator("select").first
    assert category.input_value() == "Paroled by DHS (INA 212(d)(5)(A))"                      # from the I-94 and the client's answer
    answer(paralegal, "Also ask for a work permit", "Yes")
    body = paralegal.check("case-cuban-caa-answered")
    assert "G-1450 (I-485, $1,440)" in body and "G-1450 (I-765, $260)" in body, body[:3000]
    build(paralegal, "case-cuban-caa-built")
    m = json.loads((d / "packet_caa.json").read_text(encoding="utf-8"))
    tabs = [s["tab"] for s in m["sections"]]
    assert tabs[:3] == ["G-1145", "G-1450 (I-485, $1,440)", "Cover letter"] and "I-765" in tabs, tabs
    assert m["mail_to"][2] == "P.O. BOX 805887" and "Form I-485, $1,440; Form I-765, $260" in m["fee"]
    boxes = {k: v.get("/V") for k, v in PdfReader(str(d / "i485_filled.pdf")).get_fields().items()}
    assert boxes["form1[0].#subform[6].Pt2Line3f_CB[0]"] == "/3f0"                           # Part 2, 3.f: The Cuban Adjustment Act
    assert boxes["form1[0].#subform[17].Pt9Line56_CB[7]"] == "/7"                             # Part 9, 56: exempt from public charge
