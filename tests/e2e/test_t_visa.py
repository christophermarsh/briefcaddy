"""A T visa case through the screens, on a made-up client cloned from the demo
client (tests/e2e/world.py): the case page puts it on the T visa track and names
the I-914 as the next filing; the panel says where it goes and that Supplement
B is optional; a family member's questions appear once the count is set; the
packet is built with the G-28, the I-914 and the family member's Supplement A;
then, from More filings, the request to the law enforcement agency for
Supplement B is built on its own. Everything here is invented.
"""

from __future__ import annotations

from test_pathways import answer, build, next_filing, stage_is


def test_a_t_visa_with_a_family_member_and_the_supplement_b_request(world, paralegal):
    w = world["world"]
    d = w.clone(world["root"], "demo-ana", "case-tvisa")
    w._not_sij(d)
    w.name_paralegal(d)  # a T visa case is restricted (src/restricted.py): the attorney names the paralegal on it
    w.add_fact(d, "tvisa.victim", "Yes")                                      # the attorney's first answer
    stage_is(paralegal, "case-tvisa", "T visa application (I-914) to prepare and file", "File the T visa application (I-914)")
    next_filing(paralegal, "case-tvisa", "i914", "File the T visa application (I-914)")
    body = paralegal.check("case-tvisa-i914-panel")
    assert "P.O. BOX 4221" in body and "Supplement B is optional" in body and "No fee" in body, body[:2000]   # Massachusetts: Elgin
    assert "family member 1 (supplement a)" not in body.lower()                # a section header (shown in capitals)
    answer(paralegal, "Part 3, 11: how many family members", "1")
    body = paralegal.check("case-tvisa-i914-member")
    assert "family member 1 (supplement a)" in body.lower(), body[:2000]
    answer(paralegal, "Supplement A, Part 1: family member 1", "Parent")
    answer(paralegal, "Part 3, 1: their family name", "EXEMPLO LIMA")
    answer(paralegal, "Part 3, 1: their given name", "MARIA")
    answer(paralegal, "Part 3, 19: living in the United States now?", "Yes")
    answer(paralegal, "Part 4, 1-21: every answer is No (crimes", "Yes")
    body = build(paralegal, "case-tvisa-i914-built")
    order = paralegal.page.locator("section", has_text="In the packet, in order").first.inner_text()
    assert "Form I-914, Application for T Nonimmigrant Status" in order and "Supplement A" in order and "family member 1" in order, order[:1500]
    assert "G-1450" not in order                                                # no fee (Form G-1055)
    r = paralegal.page.request.get(paralegal.world["review"].rstrip("/") + "/api/form?client=case-tvisa&form=i914a_1")
    assert r.status == 200 and r.body()[:5] == b"%PDF-"
    # Supplement B: optional, asked of the agency on its own
    paralegal.page.select_option("select[aria-label='More filings']", "i914b")
    paralegal.settle()
    body = paralegal.check("case-tvisa-i914b-panel")
    assert "Optional evidence" in body and "Nothing here is mailed to USCIS" in body, body[:2000]
    for label, value in (("Part 2, 1: the agency", "EXAMPLE CITY POLICE"), ("Part 2, 5: the agency's mailing address: street", "5 EXAMPLE AVE"),
                         ("Part 2, 5: city", "SPRINGFIELD"), ("Part 2, 5: state (two letters)", "MA"), ("Part 2, 5: ZIP code", "01103")):
        answer(paralegal, label, value)
    build(paralegal, "case-tvisa-i914b-built")
    order = paralegal.page.locator("section", has_text="In the packet, in order").first.inner_text()
    assert "letter asking the agency for Supplement B" in order and "Cover letter" not in order, order[:1500]
