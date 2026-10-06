"""Paper or online (src/online_filing.py, schemas/law/online_filing.json): which filings
USCIS takes as an attorney's PDF upload, under which limits, at which fee, and
the bundle a paralegal uploads -- then the filing recorded and its receipt
number added (src/prefile.py). Every client value is CONSTRUCTED.
"""

import json
import re
import zipfile
from datetime import date

import pytest
from pypdf import PdfReader, PdfWriter

import fees
import journey
import online_filing
import packet
import prefile
import settings
from factgraph import FactGraph

TODAY = date(2026, 10, 2)
C8, C9, A5 = "(c)(8) Pending asylum application (I-589)", "(c)(9) Pending green card application (I-485)", "(a)(5) Asylee (asylum granted)"
ROW = {"summary": {"name": "ANA EXEMPLO", "a_number": "A099000001", "dob": "1995-03-14"}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}


def _graph(**extra):
    g = FactGraph("c")
    for key, value in ({"applicant.family_name": "EXEMPLO", "applicant.given_name": "ANA", "applicant.dob": "1995-03-14"}
                       | {k.replace("__", "."): v for k, v in extra.items()}).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _notice(g, receipt, form, kind, when):
    g.add_source(f"folder.uscis_case.{receipt}.{kind}_{when.replace('-', '')}", f"{kind}.pdf", "uscis_notice", receipt, f"{form} {kind.upper()}, {when}", 0.9)


def _online(filing, graph=None, **schema):
    return online_filing.eligibility(filing, None, packet.load_filing(filing) | schema, graph or _graph(), TODAY)


def test_every_filing_has_a_rule_with_its_source_sentence():
    data = online_filing.rules()
    assert data["page_updated"] == "07/24/2026" and data["source"].startswith("https://www.uscis.gov/file-online/")
    assert {"i485", "i360", "family", "n400", "i589", "i90", "i131", "n600", "i751", "ead", "address", "asylee", "i290b", "n336", "i601a"} <= set(data["filings"])
    for filing, entry in data["filings"].items():
        assert entry["online"] in (True, False, None) and (entry["online"] is True or entry["why"]), filing
        if entry["online"] is not None:
            assert entry["_source"], filing                                  # never a rule without USCIS's words
    assert data["upload"]["max_bytes"] == 12_000_000 and "12MB" in data["upload"]["_source"]
    assert online_filing.eligibility("a-new-filing", None, {"filing": "a-new-filing"}, _graph())["online"] is False   # not on the list: paper


def test_the_i485_goes_online_only_with_its_i130():
    sij = _online("i485")
    assert sij["online"] is False and "I-360" in sij["why"]
    assert _online("asylee")["online"] is False
    family = _online("family")
    assert family["online"] is True and family["file_as"] == "I-485" and any("I-130A" in n for n in family["notes"])
    assert _online("family", variant="petition_only")["file_as"] == "I-130"  # the I-130 alone is on the list
    later = _online("family", variant="i485_only")                            # the I-485 on an I-130 already filed: "All other ... by mail"
    assert later["online"] is False and "already filed" in later["why"]


def test_no_n400_online_with_a_reduced_fee_or_a_fee_waiver():
    assert _online("n400")["online"] is True
    assert _online("n400", _graph(n400__fee_reduction="Yes"))["online"] is False
    waived = _online("n400", fee_waiver=True)
    assert waived["online"] is False and "fee waiver" in waived["why"]


def test_the_work_permit_by_category_and_never_fee_exempt():
    assert _online("ead", _graph(ead__category=C8))["online"] is True
    a5 = _online("ead", _graph(ead__category=A5))
    assert a5["online"] is False and "(a)(5)" in a5["why"]
    unanswered = _online("ead")
    assert unanswered["online"] is False and unanswered["pending"] and "Eligibility category" in unanswered["why"]
    paid = _graph(ead__category=C9, ead__i485_fee_paid="Yes")
    _notice(paid, "IOE0999000111", "I-485", "receipt", "2026-05-01")      # filed with its fee after April 1, 2024: $260
    assert _online("ead", paid)["online"] is True
    sij = _graph(ead__category=C9, applicant__filing_category="Special Immigrant Juvenile")   # $0: USCIS says mail it
    exempt = _online("ead", sij)
    assert exempt["online"] is False and "refund" in exempt["why"]


def test_advance_parole_online_only_on_an_ioe_receipt():
    ap = "Advance parole (pending I-485)"
    assert _online("i131", _graph(i131__type=ap, i131__i485_receipt="IOE0999000222"))["online"] is True
    assert _online("i131", _graph(i131__type=ap, i131__i485_receipt="MSC0999000222"))["online"] is False
    reentry = _online("i131", _graph(i131__type="Reentry permit"))
    assert reentry["online"] is False and "reentry permit" in reentry["why"]
    assert _online("eoir28")["online"] is None and _online("visa")["online"] is None    # not USCIS filings


def test_the_online_fee_comes_only_from_the_g1055_online_lines():
    data = fees.load(TODAY)
    online, paper = data["online"], data["paper"]
    assert (online["i130"], online["i485"], online["n400"], online["i751"], online["i765"], online["i131_advance_parole"]) == (625, 1390, 710, 700, 470, 580)
    assert set(online) <= set(paper) and all(online[k] <= paper[k] for k in online)
    assert "n400_reduced" not in online                                       # "You cannot file online if you are requesting ... a reduced fee"
    pays = [{"form": "I-130", "amount": 675, "what": "Form I-130 filing fee"}, {"form": "I-485", "amount": 1440, "what": "Form I-485 filing fee"},
            {"form": "I-765", "amount": 260, "what": "Form I-765 filing fee"}]
    assert [(f["form"], f["paper"], f["online"]) for f in online_filing.online_fees("family", pays, TODAY)] == \
        [("I-130", 675, 625), ("I-485", 1440, 1390), ("I-765", 260, 260)]
    asylum = online_filing.online_fees("ead", [{"form": "I-765", "amount": 560, "what": "Pub. L. 119-21 asylum work permit fee: its own payment"}], TODAY)
    assert asylum[0]["online"] == 560                                          # one G-1055 figure for both
    unknown = online_filing.online_fees("family", [{"form": "I-485", "amount": 999, "what": "Form I-485 filing fee"}], TODAY)
    assert unknown[0]["online"] is None and online_filing.money(None) == "the amount the account shows"   # never assumed
    labels = {f["key"]: f["group"] for s in settings.specs() if s["id"] == "fees" for f in s["fields"] if f.get("group")}
    assert labels["online/i485"] == "USCIS (online filing, by PDF upload)"   # the attorney keeps them on the Settings page


def test_a_big_exhibit_is_split_within_the_limit():
    from fill.continuation import _Page

    w = PdfWriter()
    for n in range(6):  # pages with something on them: a scan's weight is its pages, not the file's frame
        p = _Page()
        for i in range(60):
            p.text(50, 750 - 12 * i, f"{n} {i} " + "EXEMPLO " * 10, "F3", 8)
        w.add_page(p.to_page(w))
    pages = list(w.pages)
    one = len(online_filing._pdf_bytes(pages[:1]))
    parts, too_large = online_filing.split_by_size(pages, one * 2 + 100)
    assert len(parts) == 3 and all(len(p) <= one * 2 + 100 for p in parts) and too_large == []
    assert sum(len(PdfReader(__import__("io").BytesIO(p)).pages) for p in parts) == 6
    alone, big = online_filing.split_by_size(pages[:2], one - 1)                # a page over the limit even alone: kept, and named
    assert big == [1, 2] and len(alone) == 2
    assert online_filing._name("Copy of the 2-year Permanent Resident Card (front and back), and each included child's", 40) == \
        "Copy of the 2-year Permanent Resident"


@pytest.fixture
def case(tmp_path):
    """A conditional resident and her I-751, as the pipeline leaves the bundle (made up)."""
    source = tmp_path / "source"
    source.mkdir()
    for name, pages in (("green-card.pdf", 1), ("marriage.pdf", 2)):
        w = PdfWriter()
        for _ in range(pages):
            w.add_blank_page(width=612, height=792)
        w.write(str(source / name))
    d = tmp_path / "bundle"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"source_folder": str(source), "classifications": {"green-card.pdf": "green_card",
                                                                                             "marriage.pdf": "marriage_certificate"}}))
    g = _graph(applicant__a_number="A099000001", applicant__physical_state="MA", applicant__physical_city="SOMERVILLE", n400__lpr_date="2024-11-01")
    g.save(d / "fact_graph.json")
    return d


def test_the_bundle_to_upload_and_the_filing_recorded(case, tmp_path, monkeypatch):
    from conftest import save_shipped_office_as_the_firms
    from rules import approval

    # Implementation note.
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "approved.json"))
    save_shipped_office_as_the_firms(tmp_path / "settings.json")
    with pytest.raises(ValueError, match="can't be filed online"):
        online_filing.choose(case, "i485", "online", "Jane")
    assert online_filing.status(case, "i751")["mode"] == "paper"              # paper until someone chooses
    online_filing.choose(case, "i751", "online", "Jane")
    s = online_filing.status(case, "i751")
    # the I-751's own fee rule (src/conditions.py): $750 on paper, $700 online (G-1055 10/01/26)
    assert s["mode"] == "online" and s["fees"][0] | {} == {"form": "I-751", "what": "Form I-751 filing fee", "paper": 750, "online": 700}

    import g28

    approval.approve(g28.PRACTICE_ID, "Sam Attorney", "attorney")
    g28.confirm(case, "Jane", "paralegal")  # the G-28's card, looked at (src/g28.py): the packet's gate asks for it
    m = online_filing.build_bundle(case, ROW, "Jane", packet.load_filing("i751"))
    names = zipfile.ZipFile(online_filing.bundle_paths(case, "i751")[0]).namelist()
    assert names[0] == "0 Checklist (read first).pdf"
    assert any("G-28" in n and "print, sign in ink, scan" in n for n in names) and any(n.startswith("2 Evidence to upload/A ") for n in names)
    assert not any("G-1450" in n or "G-1145" in n for n in names)              # paid in the account; the account shows the receipt
    assert all(f["bytes"] <= 12_000_000 for f in m["files"])
    text = " ".join(s["text"] for s in m["steps"])
    assert "choose Form I-751, then Upload a Filled-Out PDF Form" in text and "USCIS requires a G-28" in text
    assert "Form I-751 $700" in text and "No Form G-1450" in text and "in ink (form page" in text
    assert "scan them, or photograph them" in text and "no receipt number" in text
    assert "lockbox" not in text.lower() and " mail it" not in text
    check = prefile.check(case, "i751", TODAY)
    assert check["online"] and check["mail_to"] is None
    by = {c["id"]: c for c in check["checks"]}
    assert by["bundle"]["level"] == "pass" and by["online_page"]["level"] == "pass" and "Mailing address" not in {c["title"] for c in check["checks"]}

    with pytest.raises(ValueError, match="USCIS online account"):            # prepared for upload: recorded as uploaded
        prefile.record_filing(case, "i751", "2026-10-02", "USPS", "9400111899223456789012", "Ana Attorney", override="made-up case", today=TODAY)
    with pytest.raises(ValueError, match="three letters and ten digits"):
        prefile.record_filing(case, "i751", "2026-10-02", prefile.ONLINE_USCIS, "12345", "Ana Attorney", override="made-up case", today=TODAY)
    rec = prefile.record_filing(case, "i751", "2026-10-02", prefile.ONLINE_USCIS, "", "Ana Attorney", override="made-up case", today=TODAY)
    assert rec["online"] and rec["receipt"] is None and rec["mail_to"] is None and "$700 (online)" in rec["fee"]
    added = prefile.add_receipt(case, "ioe-0999000777", "Paulo Paralegal")    # the paralegal reads it off the case card
    assert added["receipt"] == "IOE0999000777" and added["receipt_by"] == "Paulo Paralegal"
    with pytest.raises(ValueError):
        prefile.add_receipt(case, "IOE12", "Paulo Paralegal")
    j = journey.journey(case, TODAY, graph=FactGraph.load(case / "fact_graph.json"))
    filed = [e["what"] for e in j["timeline"] if e["kind"] == "filed"]
    assert filed == ["Filed online in the USCIS online account: I-751 petition (removing conditions on residence) (receipt IOE0999000777), "
                     "recorded by Ana Attorney. Filed despite: Draft until 2 problems are settled (made-up case)"]
    assert {"receipt": "IOE0999000777", "form": None, "since": "2026-10-02"} in journey.summary(j)["receipts"]   # USCIS's case status follows it


def test_an_online_filing_without_its_receipt_number_is_followed_up(case):
    status = {"filings": [{"filing": "i751", "title": "I-751", "mailed_on": "2026-08-20", "carrier": prefile.ONLINE_USCIS, "tracking": "", "online": True,
                           "receipt": None, "by": "Ana Attorney", "at": "2026-08-20T12:00:00+00:00"}], "filed_at": "2026-08-20T12:00:00+00:00"}
    (case / "status.json").write_text(json.dumps(status))
    j = journey.journey(case, TODAY, graph=FactGraph.load(case / "fact_graph.json"))
    step = next(s for s in j["steps"] if s["id"] == "no_receipt")
    assert "Filed online 08/20/2026, 43 days ago" in step["text"] and "case card" in step["text"] and "tracking" not in step["text"]


# -- what USCIS itself says to upload (the guide beside the bundle) ------------------------------------------------------


def test_every_filing_by_upload_has_the_uscis_notes_for_its_forms_with_their_pages_and_dates():
    data = online_filing.rules()
    uploadable = {f for f, e in data["filings"].items() if e["online"] is True}
    assert uploadable == {"family", "n400", "i131", "i751", "ead", "parole"} == set(data["upload_guide"]["forms"])  # a filing added online needs its notes
    g = data["upload_guide"]
    assert g["steps_page"].startswith("https://www.uscis.gov/file-online/") and g["steps_updated"] == "08/19/2026" and g["read_on"] == "2026-10-02"
    assert [s.split(":")[0] for s in g["steps"]] == [f"Step {n}" for n in range(1, 9)]  # USCIS's eight steps, the same for every form
    assert "no per-form upload screens or screenshots" in g["_about"]
    for filing in uploadable:
        got = online_filing.guide(filing)
        assert got["steps"] == g["steps"] and got["no_per_form_steps"] and got["forms"]
        for f in got["forms"]:
            assert f["page"].startswith("https://www.uscis.gov/") and re.fullmatch(r"\d\d/\d\d/\d{4}", f["page_updated"]) and f["read_on"] == "2026-10-02"
            assert f["upload_notes"] or f["checklist"]
            assert f["checklist"] is None or f["checklist"]["url"].startswith("https://www.uscis.gov/") and f["checklist"]["title"]
    # a filing USCIS takes only on paper has no guide; the I-131 page has no evidence checklist and says so
    assert online_filing.guide("i360") is None and online_filing.guide("i589") is None and online_filing.guide("eoir28") is None
    assert online_filing.guide("i131")["forms"][0]["checklist"] is None
    assert [f["form"] for f in online_filing.guide("family")["forms"]][0].startswith("I-485") and len(online_filing.guide("family")["forms"]) == 3


def test_the_packet_panel_carries_the_guide_only_for_a_filing_that_can_go_online(case):
    on, off = online_filing.status(case, "i751", pays=[]), online_filing.status(case, "i360", pays=[])
    assert on["guide"]["forms"][0]["form"] == "I-751" and off["guide"] is None


def test_the_quarterly_item_that_watches_for_a_uscis_filing_api(tmp_path, monkeypatch):
    import maintenance

    item = next(i for i in maintenance.registry()["items"] if i["id"] == "uscis_online_filing_api")
    assert item["party"] == "provider" and item["cadence"] == "quarterly" and item["source"] == "https://developer.uscis.gov/apis"
    assert item["check"]["known"] == ["Case Status API", "FOIA Request and Status API"] and item["steps"] and item["where"]
    catalog = ('<h2 class="card-title">Case Status API</h2><h2 class="card-title">FOIA Request and Status API</h2>')
    monkeypatch.setattr(maintenance, "LAST_LIVE", tmp_path / "live.json")
    path = tmp_path / "maintenance.json"
    path.write_text(json.dumps({"items": [item]}), encoding="utf-8")
    assert maintenance.live_checks(path, get=lambda url, binary=False, timeout=60: catalog)["results"]["uscis_online_filing_api"] == {
        "ok": True, "ours": item["check"]["known"], "uscis": item["check"]["known"], "finding": None}
    r = maintenance.live_checks(path, get=lambda url, binary=False, timeout=60: catalog + '<h2 class="card-title">Filing API</h2>')["results"]["uscis_online_filing_api"]
    assert r["ok"] is False and "Filing API" in r["finding"] and "files a form" in r["finding"]
    r = maintenance.live_checks(path, get=lambda url, binary=False, timeout=60: "<p>a page with no API on it</p>")["results"]["uscis_online_filing_api"]
    assert r["ok"] is None and "Check by hand" in r["finding"]  # a changed page is never read as "no new API"
