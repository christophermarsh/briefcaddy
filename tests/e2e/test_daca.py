"""A DACA renewal, end to end through the screens, on the made-up client case-daca
(tests/e2e/world.py: a (c)(33) work permit that expires in 135 days):

  the case page puts it on the DACA track, with the renewal window on the timeline and
  the renewal as the next filing -> its panel says what USCIS takes today, the fees
  (G-1055) and the Chicago lockbox for Massachusetts -> the questions are answered ->
  the packet is built with the G-1145, one G-1450 for the I-821D and one for the I-765,
  and the filled I-821D, I-765 (c)(33) and I-765WS.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from pypdf import PdfReader
from test_pathways import answer, build, next_filing, stage_is


def test_a_daca_recipient_renews_with_the_i821d_and_the_i765(world, paralegal):
    d = world["clients"] / "case-daca"
    expires = date.today() + timedelta(days=135)
    body = stage_is(paralegal, "case-daca", "DACA recipient: renew every two years")
    assert "Deferred Action for Childhood Arrivals" in body and "120 days before DACA expires" in body, body[:2000]
    next_filing(paralegal, "case-daca", "daca", "DACA renewal (I-821D + I-765)")
    body = paralegal.check("case-daca-panel")
    assert "accepts initial requests but does not process them" in body and "01/24/2025" in body, body[:2500]
    assert "$85 for the I-821D and $520 for the I-765" in body and "P.O. BOX 5757" in body, body[:2500]
    for label, value in (("current annual income", "24000"), ("current annual expenses", "21000"), ("total current value", "1500"),
                         ("in immigration detention", "Not in immigration detention"), ("removal proceedings, or a removal order", "No"),
                         ("continuously residing", "Yes"), ("Left the U.S.", "No"), ("without advance parole", "No"), ("Lived at any other address", "No")):
        answer(paralegal, label, value)
    for n in range(10):  # Part 4: nothing to report
        answer(paralegal, ("1. Ever arrested", "2. Ever arrested", "3. Ever engaged", "4. Now or ever", "5.A. Ever", "5.B. Ever", "5.C. Ever", "5.D. Ever",
                           "6. Ever recruited", "7. Ever used")[n], "No")
    body = paralegal.check("case-daca-answered")
    assert "G-1450 (I-821D, $85)" in body and "G-1450 (I-765, $520)" in body, body[:3000]
    build(paralegal, "case-daca-built")
    m = json.loads((d / "packet_daca.json").read_text(encoding="utf-8"))
    tabs = [s["tab"] for s in m["sections"]]
    assert tabs[0] == "G-1145" and "Cover letter" in tabs and "I-821D" in tabs and "I-765" in tabs and "I-765WS" in tabs, tabs
    assert m["mail_to"][2] == "P.O. BOX 5757" and "$85" in m["fee"] and "$520" in m["fee"], m["fee"]
    f = {k.split(".")[-1]: v.get("/V") for k, v in PdfReader(str(d / "i821d_filled.pdf")).get_fields().items()}
    assert f["P1_Line2_Date[0]"] == expires.strftime("%m/%d/%Y") and f["P1_Line7_ANumber[0]"] == "099000123"
    e = {k.split(".")[-1]: v.get("/V") for k, v in PdfReader(str(d / "i765_daca_filled.pdf")).get_fields().items()}
    assert (e["section_1[0]"], e["section_2[0]"]) == ("c", "33")
