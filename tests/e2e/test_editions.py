"""A new USCIS edition, end to end: the nightly check finds one -> Keeping current says what changed and who is responsible for
reviewing the required update -> the packet tab of every case that uses the form says the same and "Ready to mail?" stops it -> and, beside the
online-filing choice, what USCIS itself says to upload. The world is made up (tests/e2e/world.py); the check's result is
written the way the nightly run records it. Run with E2E=1 (see conftest.py).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from test_more_filings import _client

NOW = datetime.now(timezone.utc).isoformat()
# what the nightly run records for a form USCIS has a newer edition of (src/editions.py); the sentence is copied from a page, never inferred
GRACE = ("There is no grace period for the revised edition of Form N-400 because this revision is necessary for USCIS to apply the final rule.")
HELD = {"ok": False, "ours": "01/20/25", "uscis": "03/01/27", "form": "Form N-400", "page": "https://www.uscis.gov/n-400", "read_on": "2026-10-02",
        "noticed": "2027-03-02", "published": "2027-03-01",
        "grace": {"sentences": [GRACE], "no_grace": True, "grace_stated": False, "ours": "01/20/25", "refused_from": None},
        "finding": "USCIS changed Form N-400 on 03/01/2027; packets that use it wait for the update."}


def test_a_new_edition_holds_the_packets_that_use_it(world, paralegal, attorney):
    w, d = _client(world, "case-held")
    w.add_doc(d, "green-card.pdf", "green_card")
    w.add_fact(d, "n400.lpr_date", "2018-03-01", "green-card.pdf", "green_card")
    world["live"].write_text(json.dumps({"at": NOW, "results": {"form_n400": HELD}}), encoding="utf-8")
    try:
        # Keeping current: what changed, USCIS's own words, whose job it is
        attorney.page.goto(world["review"])
        attorney.settle()
        attorney.page.locator("#all").click()
        attorney.page.get_by_role("button", name=re.compile("Keeping current")).click()
        attorney.settle()
        body = attorney.check("keeping-current-held")
        held = attorney.page.locator("#held-editions").inner_text()
        assert "Waiting for a new USCIS edition" in held and "USCIS changed Form N-400 on 03/01/2027; packets that use it wait for the update." in held
        assert GRACE in held and "https://www.uscis.gov/n-400, read 10/02/2026" in held and "Contact your provider about the required edition update." in held
        assert "schemas/" not in body and "src/" not in body and " -- " not in held and "—" not in held

        # the packet tab says the same, and the packet can't go out
        paralegal.open("case-held", "packet", "n400")
        body = paralegal.check("case-held-n400-packet")
        notice = paralegal.page.locator("[data-edition]").first.inner_text()
        assert "Waiting for the update." in notice and "USCIS changed Form N-400 on 03/01/2027" in notice and GRACE in notice and "Contact your provider about the required edition update." in notice
        paralegal.page.get_by_role("button", name=re.compile("uild packet")).first.click()
        assert "Packet built" in paralegal.toast()
        paralegal.settle()
        paralegal.page.get_by_text(re.compile("Ready to mail\\? Not yet")).first.wait_for(timeout=20000)
        body = paralegal.check("case-held-n400-not-ready")
        assert "N-400 edition. USCIS changed Form N-400 on 03/01/2027" in body and "Contact your provider about the required edition update." in body, body[-2500:]

        # a form that is current: no notice
        world["live"].write_text(json.dumps({"at": NOW, "results": {"form_n400": {"ok": True, "ours": "01/20/25", "uscis": "01/20/25"}}}), encoding="utf-8")
        paralegal.open("case-held", "packet", "n400")
        assert paralegal.page.locator("[data-edition]").count() == 0
    finally:
        world["live"].unlink(missing_ok=True)


def test_what_uscis_says_to_upload_sits_beside_the_online_choice(world, paralegal):
    w, d = _client(world, "case-guide")
    w.add_doc(d, "green-card.pdf", "green_card")
    w.add_fact(d, "n400.lpr_date", "2018-03-01", "green-card.pdf", "green_card")
    paralegal.open("case-guide", "packet", "n400")
    paralegal.page.locator("#upload-guide summary").click()
    guide = paralegal.page.locator("#upload-guide").inner_text()
    body = paralegal.check("case-guide-n400-upload-guide")
    assert "USCIS publishes the same eight steps for every form and no per-form upload screens" in guide
    assert "Step 3: Select “Upload a Filled-Out PDF Form.”" in guide and "page updated 08/19/2026, read 10/02/2026" in guide
    assert "Form N-400" in guide and "you cannot file Form N-400 online" in guide and "Checklist of Required Initial Evidence" in guide
    assert "Do not send original documents unless specifically requested" in guide
    assert paralegal.page.locator("#upload-guide a[href^='https://www.uscis.gov/']").count() >= 3 and "How it is filed:" in body
    # a filing that is filed on paper only has none
    paralegal.open("case-guide", "packet", "i485")
    assert paralegal.page.locator("#upload-guide").count() == 0
