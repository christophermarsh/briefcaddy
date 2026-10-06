"""The client's last arrival, through the screens (brief K2, src/arrival.py): a made-up Notice to Appear says the client arrived at or near
Hidalgo, TX on or about 09/14/2022 and was not admitted or paroled; the client's questionnaire says McAllen, TX, 09/16/2022 and that they were
admitted. The paralegal sees one card with both papers (the notice open at its page), the product's pick and why; Save records who; the packet
waits for the card until it is saved; Undo reopens it. Everything here is invented."""

from __future__ import annotations

import json
import re

from factgraph import FactGraph

NTA = """U.S. DEPARTMENT OF HOMELAND SECURITY
NOTICE TO APPEAR
In removal proceedings under section 240 of the Immigration and Nationality Act:
File No: 201234567
Respondent: Ana Clara Exemplo Souza
currently residing at:
12 EXEMPLO ST FRAMINGHAM MA 01702
Xx You are an alien present in the United States who has not been admitted or paroled.
The Department of Homeland Security alleges that you:
1. You are not a citizen or national of the United States;
2. You are a native of BRAZIL and a citizen of BRAZIL;
3. You arrived in the United States at or near Hidalgo, TX on or about September 14, 2022;
4. You were not then admitted or paroled after inspection by an Immigration Officer.
On the basis of the foregoing, it is charged that you are subject to removal from the United States pursuant to the following provision(s) of law:
212(a)(6)(A)(i) of the Immigration and Nationality Act, as amended
"""


def _case(world, name="case-arrival", nta=NTA):
    from extract import extract_fields

    w = world["world"]
    d = w.clone(world["root"], "demo-ana", name)
    w._not_sij(d)
    w.drop_facts(d, "applicant.i94_", "applicant.last_arrival_", "questionnaire.entry_how", "questionnaire.entered_via_border", "questionnaire.visa_type")
    w.add_doc(d, "nta.pdf", "notice_to_appear")
    for key, value in {"applicant.last_arrival_city": "MCALLEN", "applicant.last_arrival_state": "TX", "applicant.last_arrival_date_self_reported": "2022-09-16",
                       "questionnaire.entry_how": "inspected"}.items():
        w.add_fact(d, key, value, "questionnaire.pdf")
    for path in w._graphs(d):
        g = FactGraph.load(path)
        for f in extract_fields("notice_to_appear", nta):
            g.add_source(f.fact_key, "nta.pdf", "notice_to_appear", f.raw_value, f.normalized_value, f.confidence, page=0)
        g.save(path)
    return d


def _open_cards(screen) -> int:
    screen.open("case-arrival", "packet")
    found = re.search(r"(\d+) review cards? still open", screen.text())
    return int(found.group(1)) if found else 0


def test_the_arrival_card_save_the_packet_gate_and_undo(world, paralegal):
    d = _case(world)
    before = _open_cards(paralegal)
    assert before >= 1

    paralegal.open("case-arrival", "fix")
    paralegal.check("arrival-card")
    card = paralegal.page.locator("article.card", has_text="Last arrival in the U.S.").first
    text = card.inner_text()
    assert "Sources disagree" in text
    assert "the Notice to Appear says at or near HIDALGO, TX; the client wrote MCALLEN, TX" in text, text
    assert "the Notice to Appear says 09/14/2022 (on or about); the client wrote 09/16/2022 (2 days apart)" in text, text
    assert "present without admission or parole; the client said admitted" in text, text
    assert "outranks the client's own answer" in text and "declaration" in text, text
    # both papers on the card, the notice open at its page
    assert "Notice to Appear" in text and "Open at page 1" in text, text
    assert card.locator("a", has_text="Open at page 1").first.get_attribute("href").endswith("#page=1")
    # the boxes hold the notice's values, the client's own answer is beside them as a source
    assert card.locator("input[type=text], input:not([type])").first.input_value() == "HIDALGO"
    assert "McAllen".upper() in text.upper() and "questionnaire" in text.lower()
    for bad in ("applicant.", "nta.", ".pdf", "ARRIVAL-01"):
        assert bad not in text.replace("nta.pdf", ""), (bad, text)
    assert "—" not in text and " -- " not in text

    # Save: recorded under the reviewer's name; the card is gone and the packet waits for one card less
    card.get_by_role("button", name="Save").click()
    assert "Saved" in paralegal.toast()
    paralegal.settle()
    decisions = json.loads((d / "decisions.json").read_text(encoding="utf-8"))
    entry = decisions["crosscheck:applicant.last_arrival_city"]
    assert entry["reviewer"] == "Paulo Paralegal" and entry["action"] == "set" and entry["values"]["applicant.last_arrival_city"] == "HIDALGO"
    paralegal.open("case-arrival", "fix")
    assert not paralegal.page.locator("article.card", has_text="Last arrival in the U.S.").count()
    assert _open_cards(paralegal) == before - 1

    # the decision log keeps it with its Undo
    paralegal.open("case-arrival", "done")
    log = paralegal.check("arrival-decision-log")
    assert "Last arrival" in log and "Paulo Paralegal" in log
    paralegal.page.locator("tr, li, article", has_text="Last arrival").get_by_role("button", name="Undo").first.click()
    paralegal.settle()
    paralegal.open("case-arrival", "fix")
    assert paralegal.page.locator("article.card", has_text="Last arrival in the U.S.").count() == 1
    assert _open_cards(paralegal) == before


def test_a_state_the_scan_misread_is_offered_from_cbps_ports_and_a_person_confirms_it(world, paralegal):
    """Brief K6: the notice's allegation runs over two lines and OCR read the state as "Ad". San Luis is a port of entry in Arizona
    only, so the card offers AZ in the boxes as the product's reading; item 10 waits until the paralegal saves it."""
    san_luis = NTA.replace("3. You arrived in the United States at or near Hidalgo, TX on or about September 14, 2022;",
                           "3. You arrived in the United States at or near SAN LUIS, Ad, on or about December 2,\n2021;")
    d = _case(world, "case-san-luis", san_luis)
    paralegal.open("case-san-luis", "fix")
    card = paralegal.page.locator("article.card", has_text="Last arrival in the U.S.: confirm the place the notice prints").first
    text = card.inner_text()
    assert 'The notice prints "SAN LUIS, Ad". San Luis is a port of entry in Arizona. Confirm or correct.' in text, text
    assert "The client wrote MCALLEN, TX." in text and "stay empty until a person saves this card" in text, text
    values = [i.input_value() for i in card.locator("input[type=text]:not(.note), input:not([type]):not(.note)").all()]
    assert values[:2] == ["SAN LUIS", "AZ"], values  # the reading in the boxes, dashed as the product's, for a person to confirm
    assert card.locator("input.suggested").count() >= 2
    for bad in ("applicant.", "nta.", "ARRIVAL-01", "—", " -- ", "ports_of_entry"):
        assert bad not in text.replace("nta.pdf", ""), (bad, text)
    paralegal.check("arrival-port-reading")
    card.get_by_role("button", name="Save").click()
    paralegal.toast()
    paralegal.settle()
    entry = json.loads((d / "decisions.json").read_text(encoding="utf-8"))["crosscheck:applicant.last_arrival_city"]
    assert (entry["values"]["applicant.last_arrival_city"], entry["values"]["applicant.last_arrival_state"]) == ("SAN LUIS", "AZ")
    assert entry["reviewer"] == "Paulo Paralegal"
