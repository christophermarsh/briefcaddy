"""The engagement letter and fee agreement (src/engagement.py): the firm's own wording per office (Settings, Firm documents), filled in per case and
per filing; signed by the client on the portal or on paper; counter-signed by an attorney; and every record in the catalog (src/records.py) and the
ledger (src/events.py). Everyone here is made up ("Ana Clara Exemplo Souza")."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

import clock
import engagement
from file_policy_fixture import handover, archive_arguments, receipt_arguments
import events
import offices
import records
import settings
from portal.app import create_app
from portal.notify import Notifier
from portal.store import PortalStore
from rules import approval

sys.path.insert(0, str(Path(__file__).resolve().parent))
import firm_world  # noqa: E402
from communication_fixture import installation, approve_client, accepted_link

NAME = "Ana Clara Exemplo Souza"
FEE = "A flat fee of $2,500 for the work described, payable in five monthly payments."


@pytest.fixture
def firm(tmp_path, monkeypatch):
    """A firm with one made-up case in the portal (Portuguese), its own settings, approvals and ledger."""
    data = installation(tmp_path, monkeypatch)
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))
    clients = data / "clients"
    d = firm_world.make_case(clients, "case-ana")
    store = PortalStore(data / "portal")
    store.add_client("case-ana", NAME, email="ana@example.com", language="pt")
    return SimpleNamespace(root=data, clients=clients, case=d, store=store, portal=data / "portal")


def approve():
    approval.approve(engagement.PRACTICE_ID, "Sam Attorney", "attorney")


def text_of(pdf: Path) -> str:
    return " ".join(" ".join((page.extract_text() or "") for page in PdfReader(str(pdf)).pages).split())


def ledger(firm) -> list[dict]:
    return list(events.rows(firm.root / "events.jsonl"))


# -- the firm's wording ---------------------------------------------------------------------------------------------------------


def test_the_shipped_letters_hold_only_words_the_product_fills_in_in_every_language():
    shipped = engagement.shipped()
    assert set(shipped["documents"]) == set(engagement.KINDS)
    for kind, doc in shipped["documents"].items():
        assert set(doc["texts"]) == set(engagement.LANGS) == set(doc["title"]), kind
        counts = {lg: len(paras) for lg, paras in doc["texts"].items()}
        assert len(set(counts.values())) == 1, (kind, counts)  # the same paragraphs in every language
        for lg, paras in doc["texts"].items():
            words = {m for p in paras for m in re.findall(r"\[([^\]]+)\]", p)}
            assert words <= set(engagement.PLACEHOLDERS), (kind, lg, words - set(engagement.PLACEHOLDERS))
            # each language holds the same bracketed words as the English, so nothing the English fills in is lost in a translation
            assert words == {m for p in doc["texts"]["en"] for m in re.findall(r"\[([^\]]+)\]", p)}, (kind, lg)
            joined = " ".join(paras)
            assert "—" not in joined and " -- " not in joined
            assert not re.search(r"\b(compl(y|ies|iance)|Rule \d)", joined, re.I), (kind, lg)  # a letter never says it meets a rule
    for key, by_lang in shipped["phrases"].items():
        assert set(by_lang) == set(engagement.LANGS), key


def test_an_attorney_edits_one_offices_letter_and_a_translation_left_behind_is_no_longer_current(firm):
    doc = engagement.document("main", "engagement")
    assert doc["version"] == 0 and doc["current"] == {"en": True, "pt": True, "es": True, "ht": True} and doc["machine"]["pt"]
    with pytest.raises(PermissionError):
        engagement.save_document("main", "engagement", {"en": "x"}, "Jane Paralegal", "paralegal")
    with pytest.raises(ValueError, match=r"\[client nmae\]"):
        engagement.save_document("main", "engagement", {"en": "Dear [client nmae],"}, "Sam Attorney", "attorney")
    english = "\n\n".join(doc["texts"]["en"]).replace("Thank you for choosing", "Thank you for trusting")
    after = engagement.save_document("main", "engagement", {"en": english, "pt": "\n\n".join(doc["texts"]["pt"])}, "Sam Attorney", "attorney")
    assert after["version"] == 1 and after["english_version"] == 1 and after["by"] == "Sam Attorney"
    assert after["current"]["en"] and not after["current"]["pt"] and not after["current"]["es"]  # the English moved on; the Portuguese was not edited
    pt = "\n\n".join(after["texts"]["pt"]).replace("Obrigado por escolher", "Obrigado por confiar em")
    again = engagement.save_document("main", "engagement", {"pt": pt}, "Sam Attorney", "attorney")
    assert again["version"] == 2 and again["current"]["pt"] and not again["machine"]["pt"] and again["english_version"] == 1
    saved = json.loads((firm.root / engagement.FIRM_FILE).read_text(encoding="utf-8"))
    assert [h["version"] for h in saved["offices"]["main"]["engagement"]["history"]] == [1]
    rows = [r for r in ledger(firm) if r["kind"] == "settings"]
    assert rows[-1]["what"] == "Changed the firm's engagement letter and fee agreement for " + engagement.office_words(offices.offices()[0]["name"]) + ": Portuguese"


def test_the_approval_holds_for_the_wording_as_it_stands(firm):
    assert engagement.practice()["state"] == "not_approved"
    approve()
    assert engagement.approved()
    doc = engagement.document("main", "closing")
    engagement.save_document("main", "closing", {"en": "\n\n".join(doc["texts"]["en"] + ["A new line."])}, "Sam Attorney", "attorney")
    assert engagement.practice()["state"] == "changed"  # any edit asks for the approval again


# -- the agreement, per office and per filing ---------------------------------------------------------------------------------------


def test_the_agreement_names_the_client_the_filings_the_office_and_the_fee_typed_never_one_of_its_own(firm):
    with pytest.raises(ValueError, match="fee"):
        engagement.make_agreement(firm.case, ["i485"], "  ", "Paulo Paralegal", "paralegal", portal_root=firm.portal)
    with pytest.raises(ValueError, match="filing"):
        engagement.make_agreement(firm.case, ["not-a-filing"], FEE, "Paulo Paralegal", "paralegal", portal_root=firm.portal)
    view = engagement.make_agreement(firm.case, ["i485", "i360"], FEE, "Paulo Paralegal", "paralegal", portal_root=firm.portal)
    letter = engagement.read(firm.case)["letters"][-1]
    en = " ".join(letter["texts"]["en"])
    assert letter["kind"] == "engagement" and letter["filings"] == ["i485", "i360"] and letter["language"] == "pt"
    assert f"Dear {NAME}," in en and FEE in en and "Form I-485" in en and "I-360" in en and offices.offices()[0]["name"] in en
    assert "[" not in en and "We will tell you the government's filing fees" in en  # nothing typed: the product says it will tell, never an amount
    pt = " ".join(letter["texts"]["pt"])
    assert f"Olá, {NAME}." in pt and FEE in pt and "I-485" in pt and "I-360" in pt and "[" not in pt
    assert view["agreement"]["draft"] and view["agreement"]["id"] == letter["id"]
    text = text_of(engagement.pdf_path(firm.case, letter))
    assert "DRAFT: the wording of this letter is the attorney's to approve" in text and NAME in text and "Portuguese" in text
    assert "Client: signature" in text  # the lines to sign on, until it is signed


def test_each_office_has_its_own_wording_and_letterhead(firm):
    oid = settings.add_office("Sam Attorney")
    settings.save(oid, {"office.name": "Orlando, FL", "office.states": "FL", "firm.business_name": "Exemplo Law", "firm.street": "1 Example Way",
                        "firm.city": "Orlando", "firm.state": "FL", "firm.zip": "32801", "office.signer": "Rita Exemplo, Esq."}, "Sam Attorney")
    doc = engagement.document(oid, "engagement")
    engagement.save_document(oid, "engagement", {"en": "\n\n".join(doc["texts"]["en"]).replace("Our office in [office] handles your case.",
                                                                                                 "Our Florida office in [office] handles your case.")},
                             "Sam Attorney", "attorney")
    offices.choose(firm.case, oid, "Sam Attorney")
    engagement.make_agreement(firm.case, ["n400"], FEE, "Sam Attorney", "attorney", portal_root=firm.portal)
    letter = engagement.read(firm.case)["letters"][-1]
    assert letter["office"] == oid and letter["office_name"] == "Orlando, FL" and letter["signer"]["name"] == "Rita Exemplo, Esq."
    assert "Our Florida office in Orlando, FL handles your case." in " ".join(letter["texts"]["en"])
    assert "pt" not in letter["texts"]  # that office's Portuguese is not current with its new English: the English alone
    assert "Orlando" in text_of(engagement.pdf_path(firm.case, letter))


# -- signing ------------------------------------------------------------------------------------------------------------------------


def test_the_client_signs_in_the_portal_in_portuguese_and_the_signed_copy_goes_on_the_case(firm):
    engagement.make_agreement(firm.case, ["i485"], FEE, "Paulo Paralegal", "paralegal", portal_root=firm.portal)
    letter_id = engagement.read(firm.case)["letters"][-1]["id"]
    with pytest.raises(ValueError, match="approves the letters' wording first"):
        engagement.send(firm.case, letter_id, "Sam Attorney", "attorney", firm.portal)
    approve()
    engagement.send(firm.case, letter_id, "Sam Attorney", "attorney", firm.portal)
    app = create_app(root=firm.portal, base_url="https://portal.example", secure_cookies=False, notifier=Notifier(firm.portal / "outbox.jsonl", env={}))
    client = TestClient(app)
    approve_client(firm.store, "case-ana")
    client.get(f"/l/{accepted_link(firm.store, 'case-ana')}", follow_redirects=False)
    me = client.get("/api/me").json()
    assert me["agreement"]["language"] == "pt" and me["agreement"]["signed_on"] is None and "pt" in me["agreement"]["texts"] and "en" in me["agreement"]["texts"]
    assert client.post("/api/agreement", json={"letter": letter_id, "agree": False, "signature": NAME}, headers={"X-Portal": "1"}).status_code == 400
    r = client.post("/api/agreement", json={"letter": letter_id, "agree": True, "signature": NAME}, headers={"X-Portal": "1"})
    assert r.status_code == 200 and r.json()["agreement"]["signed_on"] == "2026-10-05"
    letter = engagement.read(firm.case)["letters"][-1]  # put on the case at once: the case is on this machine
    sig = letter["signature"]
    assert sig["how"] == "portal" and sig["typed_name"] == NAME and sig["language"] == "pt" and sig["address"] == "testclient" and sig["at"].startswith("2026-10-05T10:30")
    text = text_of(engagement.pdf_path(firm.case, letter))
    assert f"typed name “{NAME}”" in text and "10/05/2026 at 10:30 AM" in text and "internet address testclient" in text and "reading the letter in Portuguese" in text
    assert "DRAFT" not in text
    # signing again keeps the first signature
    client.post("/api/agreement", json={"letter": letter_id, "agree": True, "signature": "Someone Else"}, headers={"X-Portal": "1"})
    assert firm.store.engagement("case-ana")["signed"]["typed_name"] == NAME
    assert [r["action"] for r in ledger(firm) if r["kind"] == "engagement"] == ["made", "sent", "signing_evidence", "signed"]


def test_a_signature_the_review_app_had_not_seen_is_recorded_when_the_case_is_opened(firm):
    approve()
    engagement.make_agreement(firm.case, ["i485"], FEE, "Paulo Paralegal", "paralegal", portal_root=firm.portal)
    letter_id = engagement.read(firm.case)["letters"][-1]["id"]
    engagement.send(firm.case, letter_id, "Sam Attorney", "attorney", firm.portal)
    firm.store.sign_agreement("case-ana", letter_id, NAME, "203.0.113.9", "pt")  # the portal on another host
    assert not engagement.read(firm.case)["letters"][-1].get("signature")
    view = engagement.view(firm.case, firm.portal, "paralegal")
    assert view["agreement"]["signature"]["address"] == "203.0.113.9" and "Signed by the client in the portal" in view["agreement"]["signed_words"][0]


def test_the_agreement_signed_on_paper_and_counter_signed_by_the_attorney(firm):
    approve()
    engagement.make_agreement(firm.case, ["i485"], FEE, "Paulo Paralegal", "paralegal", portal_root=firm.portal)
    letter_id = engagement.read(firm.case)["letters"][-1]["id"]
    with pytest.raises(ValueError, match="client signs the agreement first"):
        engagement.countersign(firm.case, letter_id, "Sam Attorney", "Sam Attorney", "attorney", firm.portal)
    scan = engagement.pdf_path(firm.case, engagement.read(firm.case)["letters"][-1]).read_bytes()
    with pytest.raises(ValueError, match="future"):
        engagement.paper(firm.case, letter_id, scan, "2026-10-09", "Paulo Paralegal", "paralegal", firm.portal)
    with pytest.raises(ValueError, match="PDF, JPG or PNG"):
        engagement.paper(firm.case, letter_id, b"not a scan", "2026-10-04", "Paulo Paralegal", "paralegal", firm.portal)
    engagement.paper(firm.case, letter_id, scan, "2026-10-04", "Paulo Paralegal", "paralegal", firm.portal)
    letter = engagement.read(firm.case)["letters"][-1]
    assert letter["signature"]["how"] == "paper" and letter["signature"]["on"] == "2026-10-04"
    assert engagement.letter_pdf(firm.case, letter_id, paper_copy=True).read_bytes() == scan
    assert firm.store.engagement("case-ana")["signed"]["how"] == "paper"  # the client's page stops asking
    with pytest.raises(PermissionError):
        engagement.countersign(firm.case, letter_id, "Paulo Paralegal", "Paulo Paralegal", "paralegal", firm.portal)
    engagement.countersign(firm.case, letter_id, "Sam B. Attorney", "Sam Attorney", "attorney", firm.portal)
    letter = engagement.read(firm.case)["letters"][-1]
    assert letter["countersignature"]["typed_name"] == "Sam B. Attorney" and letter["countersignature"]["by"] == "Sam Attorney"
    text = text_of(engagement.pdf_path(firm.case, letter))
    assert "Signed by the client on paper on 10/04/2026" in text and "Counter-signed for the firm by “Sam B. Attorney” on 10/05/2026" in text


def test_a_client_not_in_the_portal_signs_on_paper(firm):
    approve()
    other = firm_world.make_case(firm.clients, "case-bia")
    engagement.make_agreement(other, ["i485"], FEE, "Paulo Paralegal", "paralegal", portal_root=firm.portal)
    letter = engagement.read(other)["letters"][-1]
    assert letter["language"] == "en" and set(letter["texts"]) == {"en"}
    with pytest.raises(LookupError, match="paper"):
        engagement.send(other, letter["id"], "Sam Attorney", "attorney", firm.portal)


# -- the records ---------------------------------------------------------------------------------------------------------------------


def _documented(record_id: str, prefix: str = "") -> set[str]:
    out = set()
    for name, *_rest in records.by_id(record_id)["fields"]:
        if name.startswith(prefix):
            out.add(re.split(r"[.\[]", name[len(prefix):])[0])
    return out


def test_every_field_written_is_in_the_catalog_and_every_write_is_in_the_ledger_in_words(firm):
    approve()
    engagement.make_agreement(firm.case, ["i485"], FEE, "Paulo Paralegal", "paralegal", government_fees="The government's fee is $1,440.",
                              additions="We will also attend the interview.", portal_root=firm.portal)
    letter_id = engagement.read(firm.case)["letters"][-1]["id"]
    engagement.send(firm.case, letter_id, "Sam Attorney", "attorney", firm.portal)
    firm.store.sign_agreement("case-ana", letter_id, NAME, "203.0.113.9", "pt")
    engagement.sync(firm.case, firm.portal)
    engagement.countersign(firm.case, letter_id, "Sam Attorney", "Sam Attorney", "attorney", firm.portal)
    engagement.end(firm.case, "closed", "Sam Attorney", "attorney", reason="The green card was approved.", portal_root=firm.portal)
    import ledger_seal
    from datetime import timedelta

    ledger_seal.nightly(events.base_path(firm.root), clock.today() + timedelta(days=1))
    # A closed-day seal is only realistic once the wall clock moves forward;
    # later handover events must not append into the already sealed day.
    clock._now_override += timedelta(days=1)
    binding = handover(firm.case, who="Sam Attorney", portal_root=firm.portal, include_work_product=True)
    engagement.export_file(firm.case, firm.clients, "Sam Attorney", "attorney", firm.portal, expected_binding_sha256=binding)
    sha = engagement.read(firm.case)["file"]["sha256"]
    engagement.approve_file(firm.case, sha, "Sam Attorney", "attorney", firm.portal, **archive_arguments(firm.case))
    engagement.file_returned(firm.case, clock.today().isoformat(), "in_person", "Sam Attorney", "attorney", firm.portal, sha256=sha, **receipt_arguments(firm.case))
    rec = json.loads((firm.case / engagement.FILE).read_text(encoding="utf-8"))
    assert rec["version"] == records.by_id("engagement")["version"] == engagement.VERSION
    assert set(rec) <= _documented("engagement")
    for letter in rec["letters"]:
        assert set(letter) <= _documented("engagement", "letters[]."), set(letter) - _documented("engagement", "letters[].")
    # every file the case folder holds is one the catalog lists
    import fnmatch

    held = {p.relative_to(firm.case).as_posix() for p in firm.case.rglob("*") if p.is_file()}
    patterns = records.patterns("case", exported_only=False) + records.NEVER_PATTERNS["case"]
    assert {h for h in held if not any(len(p.split("/")) == len(h.split("/")) and all(fnmatch.fnmatchcase(a, b) for a, b in zip(h.split("/"), p.split("/")))
                                        for p in patterns)} == set()
    assert records.coverage("portal", "engagement.json") == "listed" and records.coverage("firm", engagement.FIRM_FILE) == "listed"
    assert records.coverage("firm", engagement.DESTROYED_FILE) == "listed"
    rows = [r for r in ledger(firm) if r["kind"] == "engagement"]
    assert [r["action"] for r in rows] == ["made", "sent", "signing_evidence", "signed", "countersigned", "closed", "file_prepared", "file_approved", "file_handed_over"]
    assert {r["case"] for r in rows} == {"case-ana"} and rows[3]["who"] == "The client" and rows[4]["role"] == "attorney"
    for r in rows:  # in words: never the client's name, the fee, a date or a reason
        assert NAME not in r["what"] and "2,500" not in r["what"] and "1,440" not in r["what"] and "approved." not in r["what"] and not re.search(r"\d{2}/\d{2}/\d{4}", r["what"])
