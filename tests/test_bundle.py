"""The review bundle (src/review/bundle.py): behind a built packet, every filled
box and where it came from. Built on the demo client (src/portal/demo.py,
invented and labelled EXEMPLO) and on test_review's drawn "scan", never real
client data."""

import json
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from fill import load_field_map
from review import bundle
from review.overview import review_row
from review.state import Catalog, build_items, record_decision, refill
from test_review import CATALOG, FIELD_MAP, TEMPLATE, _get, _post, client, server  # noqa: F401 -- fixtures used below
import schema_path

_REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def demo(tmp_path_factory, request):
    """The demo client as the portal processes it, with its I-485 packet built."""
    import packet
    from portal import demo as seed
    from portal.store import PortalStore

    import settings
    from conftest import save_shipped_office_as_the_firms

    root = tmp_path_factory.mktemp("demo")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "PATH", root / "settings.json")
    save_shipped_office_as_the_firms(root / "settings.json")  # Implementation note.
    request.addfinalizer(mp.undo)
    seed.seed(PortalStore(root / "portal"), root / "clients")
    d = root / "clients" / seed.DEMO_ID
    field_map = load_field_map(schema_path.path("field_map", "i485"))
    policies = json.loads((schema_path.path("law", "policy_sijs")).read_text(encoding="utf-8"))["policies"]
    catalog = Catalog(field_map, TEMPLATE, policies)
    refill(d, field_map, TEMPLATE)
    packet.build(d, review_row(d, field_map, TEMPLATE, catalog), "Jane", packet.load_filing("i485"))
    return d


def _text(pdf: Path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)


def test_the_demo_clients_bundle_has_a_source_on_every_row(demo):
    data = bundle.rows(demo, "i485")
    assert len(data["rows"]) > 150 and {"G-28", "I-485", "I-765"} <= {r["form"] for r in data["rows"]}
    for row in data["rows"]:
        assert row["sources"], row
        assert all(s["kind"] != "none" for s in row["sources"]), row  # every box traced to a document, rule, reviewer or the firm
    kinds = {s["kind"] for r in data["rows"] for s in r["sources"]}
    assert {"document", "policy", "rule", "firm"} <= kinds
    name = next(r for r in data["rows"] if r.get("key") == "applicant.family_name" and r["form"] == "I-485")
    assert name["value"] == "EXEMPLO SOUZA" and name["ref"].startswith("Part 1")
    # Item 74 (Item 13 disagrees with the client's own answer, so it stays empty until a person decides)
    overstay = next(r for r in data["rows"] if r.get("key") == "applicant.part9.unlawfully_present_since_1997")
    rule = next(s for s in overstay["sources"] if s["kind"] == "rule")
    assert rule["rule"] == "OVERSTAY-01" and "admit-until date" in rule["text"] and overstay["value"] == "Yes (box ticked)"
    assert not any(r.get("key") == "applicant.part9.violated_nonimmigrant_status" for r in data["rows"])
    policy = next(s for r in data["rows"] for s in r["sources"] if s["kind"] == "policy" and s.get("rule") == "POLICY:NO-CREWMAN")
    assert policy["text"].startswith("The firm's standard answer") and policy["title"] == "Not a crewman"
    assert bundle._source_lines(policy)[0] == ("F2", "Firm policy: Not a crewman (NO-CREWMAN)")


def test_the_bundle_pdf_is_watermarked_and_lists_every_rule_with_its_approval(demo, tmp_path, monkeypatch):
    from rules import approval

    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))  # this test's own approvals
    record = bundle.build(demo, "i485", "Jane Paralegal")
    pdf, saved = bundle.paths(demo, "i485")
    assert pdf.parent == demo and pdf.name == "packet_review_bundle.pdf" and json.loads(saved.read_text())["rows"] == record["rows"]
    text = _text(pdf)
    assert "Example Immigration Office" in text and "INTERNAL REVIEW RECORD, NOT FOR FILING" in text and "Internal review record, not for filing" in text
    assert "Rules and firm policies used in this packet" in text
    assert "Rule: the overstay rule (I-94 admit-until date vs. the I-360 date) (OVERSTAY-01)" in text
    assert "Not yet approved for volume use." in text and "decisions.md" not in text
    assert record["rules"] and all(r["approval"]["state"] == "not_approved" for r in record["rules"])
    # the attorney approves the rule for every case: the next bundle says who and when
    approval.approve("OVERSTAY-01", "Ana Attorney", "attorney")
    bundle.build(demo, "i485", "Jane Paralegal")
    assert re.search(r"Approved by Ana Attorney on \d{2}/\d{2}/\d{4} for volume use\.", _text(pdf))
    assert bundle.info(demo, "i485")["stale"] is False


def test_a_reviewer_decision_and_the_scan_crop_are_in_the_bundle(client, server):  # noqa: F811
    import packet

    items = {i["id"]: i for i in build_items(client, FIELD_MAP, TEMPLATE, CATALOG)["open"]}
    record_decision(client, items["fact:applicant.weight_lbs"], {"reviewer": "Jane", "role": "paralegal", "action": "confirm", "note": "matches the scan"})
    refill(client, FIELD_MAP, TEMPLATE)
    status, body = _post(server + "/api/review-bundle", {"client": "demo", "reviewer": "Jane"})
    assert status == 400 and "Build the packet first" in body.decode()
    packet.build(client, review_row(client, FIELD_MAP, TEMPLATE, CATALOG), "Jane", packet.load_filing("i485"))
    status, body = _post(server + "/api/review-bundle", {"client": "demo", "reviewer": ""})
    assert status == 400  # the bundle records who built it
    status, record = _post(server + "/api/review-bundle", {"client": "demo", "reviewer": "Jane"})
    assert status == 200 and record["built_by"] == "Jane" and record["rows"] > 0
    weight = next(r for r in bundle.rows(client, "i485")["rows"] if r.get("key") == "applicant.weight_lbs")
    reviewer = next(s for s in weight["sources"] if s["kind"] == "reviewer")
    assert reviewer["name"] == "Jane" and reviewer["role"] == "paralegal" and reviewer["note"] == "matches the scan"
    assert re.fullmatch(r"\d{2}/\d{2}/\d{4}", reviewer["date"])
    scan = next(s for s in weight["sources"] if s["kind"] == "document" and s.get("crop"))
    assert scan["crop"] == {"doc": "q.pdf", "page": 0, "box": [10, 10, 200, 60]}
    status, pdf = _get(server + "/api/review-bundle.pdf?client=demo")
    assert status == 200 and pdf[:5] == b"%PDF-"
    pages = PdfReader(bundle.paths(client, "i485")[0]).pages
    assert any("/XObject" in (p["/Resources"] or {}) for p in pages)  # the crop of the scan, drawn in
    status, plan = _get(server + "/api/packet?client=demo")
    assert json.loads(plan)["review_bundle"]["rows"] == record["rows"]


def test_the_crop_is_the_review_cards_crop():
    from PIL import Image

    image = Image.new("L", (400, 300), 255)
    assert bundle.crop(image, "10,10,200,60").size == (212, 105)  # 12 to each side, 45 above and below a one-line answer
    assert bundle.crop(image, [10, 10, 200, 150]).size == (212, 162)  # a tall entry keeps its own margins


def test_a_review_bundle_record_is_never_taken_for_the_packet(tmp_path):
    """journey._latest_packet globs packet*.json: the bundle's record (packet_review_bundle.json), newer, must not win."""
    import os
    import time

    from journey import _latest_packet

    (tmp_path / "packet_i360.json").write_text(json.dumps({"built_at": "2026-10-01T10:00:00+00:00", "filing": "i360"}))
    (tmp_path / "packet_review_bundle.json").write_text(json.dumps({"built_at": "2026-10-02T10:00:00+00:00", "filing": "i485", "rows": 3}))
    (tmp_path / "packet_i360_review_bundle.json").write_text(json.dumps({"built_at": "2026-10-02T11:00:00+00:00", "filing": "i360", "rows": 3}))
    old = time.time() - 100
    os.utime(tmp_path / "packet_i360.json", (old, old))  # the bundle records are the newest files
    latest = _latest_packet(tmp_path)
    assert latest["built_at"] == "2026-10-01T10:00:00+00:00" and "rows" not in latest


# Implementation note.


def test_every_document_row_shows_its_file_and_page_and_a_crop_where_the_value_stands_in_one_place(demo, tmp_path, monkeypatch):
    """The demo's documents are read from their text layer: no page and no box were recorded, so the crops and page numbers were
    missing. The page is where the value stands in the file; the crop is drawn when it stands in one place only."""
    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))
    docs = [s for r in bundle.rows(demo, "i485")["rows"] for s in r["sources"] if s["kind"] == "document"]
    assert docs and all(isinstance(s.get("page"), int) and s["page"] >= 1 for s in docs), docs[:3]  # one-page documents: page 1
    cropped = [s for s in docs if s.get("crop")]
    assert cropped and len(cropped) < len(docs)  # some values stand in one place; a name that stands in several does not get a guess
    assert all({"page", "pdf_box", "page_size"} <= set(s["crop"]) for s in cropped)
    bundle.build(demo, "i485", "Jane Paralegal")
    pdf, _ = bundle.paths(demo, "i485")
    text = " ".join(_text(pdf).split())
    assert re.search(r"i94-[0-9a-f]+\.pdf, page 1 Says: ", text) or re.search(r"i94-[0-9a-f]+\.pdf, page 1", text)
    assert "page 1 (no crop: the value is not written on the page in a form that can be searched)" in text  # the state (SAO PAULO is printed SP): the reason is said
    assert "found by searching the page for it" in text
    assert sum("/XObject" in (p["/Resources"] or {}) for p in PdfReader(str(pdf)).pages) >= 1  # the crops are in the PDF
    assert "crop unavailable" not in text


def test_a_crop_that_cannot_be_drawn_says_so_and_is_logged(demo, tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))

    def unreadable(doc):
        raise OSError("the scan is unreadable")

    with caplog.at_level(logging.WARNING, logger="review.bundle"):
        bundle.build(demo, "i485", "Jane Paralegal", page_images=unreadable)
    text = " ".join(_text(bundle.paths(demo, "i485")[0]).split())
    assert "crop unavailable" in text
    assert any("could not read the pages" in r.message and "unreadable" in r.message for r in caplog.records), caplog.text


def test_rows_follow_the_forms_own_order(demo):
    rows = bundle.rows(demo, "i485")["rows"]
    assert [r["form"] for r in rows if r["form"] in ("G-28", "I-485", "I-765")] == sorted(
        (r["form"] for r in rows if r["form"] in ("G-28", "I-485", "I-765")), key=["G-28", "I-485", "I-765"].index)  # the packet's order
    g28 = [r["ref"] for r in rows if r["form"] == "G-28" and r.get("ref")]
    parts = [int(re.match(r"Part (\d+)", ref).group(1)) for ref in g28]
    assert parts == sorted(parts), g28  # the two-column page reads Part 1, then Part 2, not across
    part1 = [int(m.group(1)) for ref in g28 if ref.startswith("Part 1, ") and (m := re.search(r"Item (\d+)", ref))]
    assert part1 == sorted(part1) and part1[0] == 1, part1  # a G-28 used to start at Item 4, then 2B, 2A, 1, 3
    labels = [r["label"] for r in rows if r["form"] == "G-28"]
    assert labels.index(next(x for x in labels if x.startswith("2. A."))) < labels.index(next(x for x in labels if x.startswith("2. B.")))
    i485 = [int(m.group(1)) for r in rows if r["form"] == "I-485" and (m := re.fullmatch(r"Part 1, Item (\d+)", r.get("ref") or "")) and int(m.group(1)) <= 8]
    assert i485 == sorted(i485) and i485[0] == 1, i485


def test_the_clients_own_typing_is_not_called_a_document(demo, tmp_path, monkeypatch):
    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))
    rows = bundle.rows(demo, "i485")["rows"]
    given = next(r for r in rows if r.get("key") == "applicant.given_name" and r["form"] == "I-485")
    portal = next(s for s in given["sources"] if s["kind"] == "portal")
    assert re.fullmatch(r"The client's answer in the portal \(\d{2}/\d{2}/\d{4}\)", portal["title"])
    assert portal["question"] == "First and middle name(s)" and portal["value"] == "ANA CLARA"
    assert not any(s["kind"] == "document" and not s.get("doc") for r in rows for s in r["sources"])  # no "Document" without a file
    assert "\u2014" not in portal["question"]
    bundle.build(demo, "i485", "Jane Paralegal")
    text = " ".join(_text(bundle.paths(demo, "i485")[0]).split())
    assert re.search(r"The client's answer in the portal \(\d{2}/\d{2}/\d{4}\) Question: First and middle name\(s\) Says: ANA CLARA", text)
    assert "Document: The client's portal answers" not in text and "from client's portal answer" in text


def test_a_social_security_number_shows_its_last_four_digits_only(demo, tmp_path, monkeypatch):
    import shutil

    import packet

    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))
    d = tmp_path / "ana"
    shutil.copytree(demo, d)
    field_map = load_field_map(schema_path.path("field_map", "i485"))
    policies = json.loads((schema_path.path("law", "policy_sijs")).read_text(encoding="utf-8"))["policies"]
    catalog = Catalog(field_map, TEMPLATE, policies)
    # the card and the client's typing disagree (123-45-6789 against 123-45-6780): the paralegal decides, and the number is on the form
    item = next(i for i in build_items(d, field_map, TEMPLATE, catalog)["open"] if i["id"] == "fact:applicant.ssn")
    record_decision(d, item, {"reviewer": "Jane", "role": "paralegal", "action": "set", "values": {"applicant.ssn": "123-45-6789"}, "note": "the card controls"})
    refill(d, field_map, TEMPLATE)
    packet.build(d, review_row(d, field_map, TEMPLATE, catalog), "Jane", packet.load_filing("i485"))
    data = bundle.rows(d, "i485")
    row = next(r for r in data["rows"] if r.get("key") == "applicant.ssn" and r["form"] == "I-485")
    assert row["value"] == "***-**-6789"
    says = [s["value"] for s in row["sources"] if s.get("value")]
    assert says and all("123" not in v and "6789" in v or "6780" in v for v in says) and not any(re.search(r"\d{3}-?\d{2}-?\d{4}", v) for v in says)
    bundle.build(d, "i485", "Jane Paralegal")
    text = " ".join(_text(bundle.paths(d, "i485")[0]).split())
    assert "***-**-6789" in text and not re.search(r"123-?45-?678[09]", text), re.findall(r".{20}123-?45.{20}", text)
    # an A-Number is not a Social Security number, and the USCIS online account number is neither: neither is hidden
    assert any(r["value"] == "099000123" for r in data["rows"])


def test_private_numbers_are_found_by_what_they_are_never_by_their_digits():
    from review.state import is_private_number, mask_number

    for key in ("applicant.ssn", "i751.spouse_ssn", "asylum.child2_ssn", "n400.ssn", "petitioner.itin", "payment.routing_number", "payment.card_number", "payment.bank_account"):
        assert is_private_number(key), key
    for key in ("applicant.a_number", "applicant.uscis_online_account_number", "firm.uscis_online_account_number", "applicant.i360_receipt_number",
                "applicant.has_ssn", "applicant.passport_number"):
        assert not is_private_number(key), key
    assert is_private_number(None, "19. Social Security Number") and not is_private_number(None, "1. Enter U S C I S Online Account Number")
    assert mask_number("123-45-6789") == "***-**-6789" and mask_number("123456789") == "***-**-6789"
    assert mask_number("1234567890") == "******7890" and mask_number("1234") == "****" and mask_number("") == ""


# Implementation note.


def test_nearly_every_document_row_has_a_crop_with_the_value_boxed(demo):
    """28 of the demo's 50 document rows had no crop: the I-360 notice (a name, an A-Number and a date that stand on the page twice), the
    birth certificate and the passport (a country written BRASIL, a date written as the machine-readable line's YYMMDD), the I-94's "B2".
    Looked for as quoted, as recorded and in every written form, 49 of the 50 are found; the one left is a state printed as "SP"."""
    docs = [(r, s) for r in bundle.rows(demo, "i485")["rows"] for s in r["sources"] if s["kind"] == "document"]
    missing = [(r["label"], s["doc"][:12], s.get("no_crop")) for r, s in docs if not s.get("crop")]
    assert len(docs) == 50 and len(missing) <= 1, missing
    assert missing == [("15. B. Enter State / Province of Birth", "birth_certif", "the value is not written on the page in a form that can be searched")]
    notice = [s for r, s in docs if s["doc"].startswith("i360_approval") and r.get("key") in ("applicant.family_name", "applicant.a_number", "applicant.i360_priority_date")]
    assert len(notice) >= 4 and all(s.get("crop") for s in notice)  # the most useful source in an SIJ case
    twice = next(s["crop"] for s in notice if s["crop"]["places"] == 2)
    assert len(twice["marks"]) == 2 and twice["lines"]  # both places boxed, the lines of the page known so the crop starts between lines
    assert all(s["crop"]["marks"] for _r, s in docs if s.get("crop") and "box" not in s["crop"])


def test_the_crop_is_a_line_above_and_below_with_a_thin_box_round_the_value():
    from PIL import Image

    page = Image.new("L", (1224, 1584), 255)  # 2 pixels to the point
    found = {"pdf_box": [100.0, 200.0, 160.0, 212.0], "marks": [[100.0, 200.0, 160.0, 212.0]], "page_size": [612, 792],
             "lines": [[170.0, 182.0], [200.0, 212.0], [230.0, 242.0], [260.0, 272.0]]}
    out = bundle.found_crop(page, found)
    assert out.height == pytest.approx((242 - 170) * 2 + 7, abs=4)  # the whole line above and the whole line below, not half of them
    assert out.width >= 170 * 2 - 2  # wide enough for the words around a short value to be read
    boxed = [(x, y) for y in range(out.height) for x in range(out.width) if out.getpixel((x, y)) == 0]
    assert boxed and min(x for x, _ in boxed) > 0  # the box is drawn, and is not the crop's own edge
    plain = Image.new("L", out.size, 255)
    from PIL import ImageChops

    assert ImageChops.difference(out, plain).getbbox() is not None


def test_a_value_is_looked_for_in_every_way_it_is_written():
    renderings = {text for text, _mode in bundle._renderings("2032-01-10")}
    assert {"01/10/2032", "10 JAN 2032", "JANUARY 10, 2032", "2032 JANUARY 10", "320110"} <= renderings  # the last one: a passport's machine-readable line
    ids = {text for text, _mode in bundle._renderings("A099000123")}
    assert ids == {"A099000123", "099000123"}
    assert bundle._pattern("A099000123", "ids").search(bundle._fold_text("Petitioner A099 000 123"))  # spaces between the groups
    assert bundle._pattern("A099000123", "ids").search(bundle._fold_text("A-099-000-123"))
    assert bundle._pattern("BRASIL", "words").search(bundle._fold_text("REPUBLICA FEDERATIVA DO BRASIL"))
    assert not bundle._pattern("BRASIL", "words").search(bundle._fold_text("BRASILEIRO(A)"))  # a word, not the start of another
    assert ("BRASIL", "words") in bundle._renderings("BRAZIL")  # a country in the document's own language
    quoted = bundle._needles("birth certificate lists this parent as 'JOSE EXEMPLO SOUZA'. Split: EXEMPLO is a family surname", "EXEMPLO SOUZA")
    assert quoted[0] == ("JOSE EXEMPLO SOUZA", "words")  # what the reader quoted from the page comes first
