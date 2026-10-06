"""The client's case, in their words (brief I3, src/client_case.py, src/client_reminders.py): USCIS's own words quoted untouched, nothing without the firm's
keys, the estimate only from the file, what happens next at every stage, the preparation sheets, the reminders and the client's consent, "how was this step" and
Reports with the restricted rule, and the answer that stays "Sent to the office" for seven days. Everyone is made up (the Exemplo family); the receipt numbers are
made up; no test goes on the network.
"""

# ruff: noqa: F811  (the made-up firm's fixtures, world and app, are imported from tests/test_restricted.py and used as arguments)
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import case_status
import clock
import client_case
import client_reminders
import events
import journey
import restricted
from extract.uscis_notice import bring_list, extract, parse
from factgraph import FactGraph
from portal.app import create_app
from portal.notify import Notifier
from portal.store import PortalStore
from test_restricted import app, call, doc, make_case, server, sign_in, world  # noqa: F401 -- the made-up firm, its review app and its server
import schema_path

H = {"X-Portal": "1"}
TODAY = date(2026, 10, 3)

JANE = {"email": "jane@firm.example", "role": "paralegal"}
SAM = {"email": "sam@firm.example", "role": "attorney"}
RECEIPT = "IOE0999000777"
# the API page's own example answer (developer.uscis.gov swagger_3.yaml), as in tests/test_case_status.py
APPROVED = {"receiptNumber": RECEIPT, "formType": "I-485", "submittedDate": "09-05-2026 14:28:46", "modifiedDate": "09-05-2026 14:28:46",
            "current_case_status_text_en": "Case Was Approved",
            "current_case_status_desc_en": "<p>On September 5, 2026, we approved your Form I-485, Application to Register Permanent Residence or Adjust Status, "
                                           f"Receipt Number {RECEIPT}. We sent you an approval notice.</p>",
            "current_case_status_text_es": "Caso Fue Aprobado", "current_case_status_desc_es": "...", "hist_case_status": []}


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 3, 2, 0))  # 2 am on 10/03/2026, the office's own wall clock
    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": "October 2026", "area": "ALL CHARGEABILITY", "cutoff": None, "pd": "2023-01-10",
                                                                "current": None, "problems": []})
    client_case._keys["at"] = 0.0


def _add_notice(case_dir: Path, receipt: str, form: str, kind: str, when: str, **extra) -> None:
    g = FactGraph.load(case_dir / "fact_graph.json")
    slug = f"{kind}_{when.replace('-', '')}"
    titles = {"biometrics": "BIOMETRICS APPOINTMENT", "interview": "INTERVIEW", "receipt": "RECEIPT", "approval": "APPROVAL"}
    g.add_source(f"folder.uscis_case.{receipt}.{slug}", f"{kind}.pdf", "uscis_notice", receipt, f"{form} {titles[kind]}, {when}", 0.9)
    g.add_source(f"folder.notice.{receipt}.{slug}.date", f"{kind}.pdf", "uscis_notice", when, when, 0.85)
    for name, value in extra.items():
        g.add_source(f"folder.notice.{receipt}.{slug}.{name}", f"{kind}.pdf", "uscis_notice", value, value, 0.85)
    g.save(case_dir / "fact_graph.json")


def _case(root: Path, case_id: str = "case-ana", name: str = "Ana Clara Exemplo Souza") -> Path:
    return make_case(root, case_id, name, [doc("a1", "passport")])


# -- USCIS's own words -----------------------------------------------------------------------------------------------


def _saved_status(case_dir: Path, environment: str = "production") -> None:
    rec = case_status.record_of(APPROVED, datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc), environment)
    case_status.save(case_dir, RECEIPT, rec)


def _j(case_dir: Path, ready: bool = True, monkeypatch=None) -> dict:
    if monkeypatch is not None:
        monkeypatch.setattr(client_case, "keys_ready", lambda: ready)
    return journey.journey(case_dir, TODAY)


def test_uscis_words_are_quoted_untouched_in_english_with_the_sentence_in_the_clients_language(tmp_path, monkeypatch):
    d = _case(tmp_path)
    _saved_status(d)
    j = _j(d, True, monkeypatch)
    english = journey.client_view(j, "en")["status"]["items"][0]
    for lang, said, in_english in (("pt", "O USCIS disse em 2 de outubro de 2026", "Isto é o que o USCIS diz, em inglês"),
                                   ("es", "USCIS dijo el 2 de octubre de 2026", "Esto es lo que dice USCIS, en inglés"),
                                   ("ht", "USCIS te di nan dat sa a: 2 oktòb 2026", "Sa a se sa USCIS di, an angle"), ("en", "USCIS said on October 2, 2026", None)):
        status = journey.client_view(j, lang)["status"]
        item = status["items"][0]
        assert item["text"] == "Case Was Approved" and item["desc"] == english["desc"]  # USCIS's English, never put in other words
        assert item["desc"].startswith("On September 5, 2026, we approved your Form I-485") and "<p>" not in item["desc"]
        assert item["said_on"] == said and (in_english is None or status["in_english"] == in_english)
        assert "Aprobado" not in json.dumps(status, ensure_ascii=False) and "Aprovado" not in json.dumps(status, ensure_ascii=False)  # no machine draft of it


def test_nothing_shows_without_the_keys_and_nothing_from_the_sandbox(tmp_path, monkeypatch):
    d = _case(tmp_path)
    _saved_status(d)
    assert journey.client_view(_j(d, False, monkeypatch), "pt")["status"] is None  # the firm has no keys: nothing, whatever an old file holds
    assert journey.client_view(_j(d, True, monkeypatch), "pt")["status"] is not None
    other = _case(tmp_path, "case-bia", "Beatriz Exemplo Lima")
    _saved_status(other, "sandbox")  # staging answers are never a real client's
    assert journey.client_view(_j(other, True, monkeypatch), "pt")["status"] is None
    case_status.save(other, RECEIPT, {"receipt": RECEIPT, "error": "not_found", "checked_at": "2026-10-02T08:00:00+00:00", "environment": "production"})
    assert client_case.status_records(other, ready=True) == []  # an error is not a status


def test_two_receipts_of_one_form_are_told_apart_by_their_number(tmp_path):
    d = _case(tmp_path)
    for receipt in ("IOE0999000001", "IOE0999000002"):
        case_status.save(d, receipt, case_status.record_of(APPROVED | {"receiptNumber": receipt}, datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc), "production"))
    items = client_case.status_view(client_case.status_records(d, ready=True), "en")["items"]
    assert sorted(x["receipt"] for x in items) == ["IOE0999000001", "IOE0999000002"]


# -- USCIS's estimate: only from the file --------------------------------------------------------------------------------


def test_the_estimate_comes_only_from_the_file_with_the_figure_untouched(tmp_path):
    shipped = json.loads((schema_path.path("register", "processing_times")).read_text(encoding="utf-8"))
    assert shipped["last_attempt_result"].startswith("refused") and shipped["entries"] == []  # the page refused us on 10/03/2026: nothing is invented
    j = {"receipts": [{"receipt": "LIN2690000001", "form": "I-485", "closed": False}]}
    assert client_case.estimates(j, "en") == []
    entry = {"form": "I-485", "office": "Example Service Center", "receipt_prefixes": ["LIN"], "months": "6.5", "read_on": "2026-10-03", "url": "https://egov.uscis.gov/processing-times/"}
    path = tmp_path / "times.json"
    path.write_text(json.dumps({"entries": [entry]}), encoding="utf-8")
    assert client_case.estimates(j, "en", path) == ["USCIS's published estimate on October 3, 2026: 6.5 months for most cases at Example Service Center"]
    assert client_case.estimates(j, "pt", path) == ["Estimativa publicada pelo USCIS em 3 de outubro de 2026: 6.5 meses para a maioria dos casos em Example Service Center"]
    assert client_case.estimates(j, "ht", path)[0].startswith("Estimasyon USCIS pibliye nan dat 3 oktòb 2026: 6.5 mwa")
    assert client_case.estimates({"receipts": [{"receipt": "SRC2690000001", "form": "I-485", "closed": False}]}, "en", path) == []  # another office's receipt
    assert client_case.estimates({"receipts": [{"receipt": "LIN2690000001", "form": "N-400", "closed": False}]}, "en", path) == []  # another form
    assert client_case.estimates({"receipts": [{"receipt": "LIN2690000001", "form": "I-485", "closed": True}]}, "en", path) == []  # a closed case
    path.write_text(json.dumps({"entries": [{k: v for k, v in entry.items() if k != "read_on"}]}), encoding="utf-8")
    assert client_case.estimates(j, "en", path) == []  # an entry without its day is not shown
    assert client_case.estimates(j, "en", tmp_path / "missing.json") == []


# -- what happens next -----------------------------------------------------------------------------------------------


def test_every_stage_of_every_track_has_its_paragraph_in_four_languages():
    stages = {s for track in journey.settings()["stages"].values() for s in track}
    paragraphs = json.loads((schema_path.path("register", "client_next_steps")).read_text(encoding="utf-8"))["stages"]
    assert stages == set(paragraphs), (stages ^ set(paragraphs))
    for stage, words in paragraphs.items():
        for lang in client_case.LANGS:
            text = client_case.next_step(stage, lang)["text"]
            assert text == words[lang] and len(text) > 60, (stage, lang)
            assert "—" not in text and " -- " not in text and not re.search(r"\.json|_[a-z]+\b", text), (stage, lang)
            assert not re.search(r"\b(?:should|must|eligible|qualify)\b", text, re.I), (stage, lang)  # nothing legal: no advice, no "should" about the law
    assert client_case.next_step("no_such_stage", "pt") is None
    j = {"stage": "i485_pending", "stage_index": 0, "today": "2026-10-03", "stages": [{"id": "i485_pending"}], "notices": [], "filings": []}
    assert journey.client_view(j, "pt")["next"] == {"label": "O que acontece depois", "text": paragraphs["i485_pending"]["pt"]}
    assert journey.client_view(j, "fr")["next"]["label"] == "What happens next"  # not a portal language: English


# -- the preparation sheets ---------------------------------------------------------------------------------------------


def test_each_form_has_its_sheet_for_biometrics_and_the_interview():
    for form, track in (("I-485", None), ("N-400", None), ("I-589", None), ("I-130", None), ("I-360", None), ("I-485", "family"), (None, None), ("I-765", None)):
        for kind in ("biometrics", "interview"):
            for lang in client_case.LANGS:
                s = client_case.sheet(kind, form, track, lang)
                assert s["day"] and s["bring"] and (s["source"] is not None or s["key"] == "other")
                assert not any("—" in x or " -- " in x for x in s["day"] + s["bring"]), (kind, form, lang)
    assert client_case.sheet_key("interview", "I-485", "family") == "family" and client_case.sheet_key("interview", "I-130", None) == "family"
    assert client_case.sheet_key("interview", "I-485", "sij") == "i485" and client_case.sheet_key("interview", "I-360", "sij") == "sij"
    assert client_case.sheet_key("interview", "I-360", "vawa") == "other" and client_case.sheet_key("interview", "I-360", None) == "other"  # the SIJ page speaks of SIJ cases only
    vawa = client_case.sheet("interview", "I-360", "vawa", "en")
    assert not any("In these cases USCIS holds an interview only when" in x for x in vawa["day"]) and vawa["source"] is None
    assert client_case.sheet_key("biometrics", "I-485", None) == "default" and client_case.sheet_key("biometrics", "N-400", None) == "n400"
    assert client_case.sheet_key("interview", "I-765", None) == "other" and client_case.sheet("hearing", None, None, "en") is None
    n400 = client_case.sheet("biometrics", "N-400", None, "en")
    assert any("green card (Form I-551)" in x for x in n400["bring"]) and any("second photo ID" in x for x in n400["bring"])  # USCIS's naturalization list
    assert "(Form N-400)" in n400["title"] and n400["source"] == "Made from USCIS's own web pages (read on October 3, 2026) and the office's own advice."
    firm = json.loads((schema_path.path("register", "client_case")).read_text(encoding="utf-8"))["firm_lines"]  # the lines that are the firm's advice, listed for the attorney
    assert {"b_early", "i_bring_id", "i_day_prepared"} <= set(firm)
    assert "penalty of perjury" in client_case.sheet("biometrics", "I-485", None, "en")["day"][1]  # USCIS's own words on the digital signature
    assert "a representative or employee of the government of your country" in " ".join(asylum_en := client_case.sheet("interview", "I-589", None, "en")["day"]) and "fluent" in " ".join(asylum_en)
    assert "(and the family member who filed the immigrant petition for you, if applicable) must bring the originals" in " ".join(client_case.sheet("interview", "I-130", None, "en")["bring"])
    asylum = client_case.sheet("interview", "I-589", None, "pt")
    assert any("intérprete" in x for x in asylum["day"] + asylum["bring"]) and asylum["machine"] is None
    assert client_case.sheet("interview", "I-589", None, "ht")["machine"].startswith("Kreyòl ayisyen: tradiksyon otomatik")
    assert client_case.sheet("interview", "I-485", None, "en", phone="(617) 555-0100")["phone"] == "Questions? Call the office: (617) 555-0100"


def test_the_notices_own_list_is_read_and_quoted_as_written():
    text = ("Receipt Number Case Type\nIOE0999000777 I485 - APPLICATION TO REGISTER PERMANENT RESIDENCE OR ADJUST STATUS\nNotice Date\n09/20/2026\n"
            "ASC Appointment Notice\nDate and Time of Appointment: 10/10/2026 09:30 AM\nPlace\nUSCIS ASC\n1 Example Way\nBoston, MA 02110\n"
            "Please bring the following:\n- This appointment notice\n- A valid photo ID (passport or driver's license)\n2. Your Employment Authorization Card, if you have one\n\n"
            "Failure to appear may delay your case.\n")
    n = parse(text)
    assert n.kind == "biometrics" and n.bring == ["This appointment notice", "A valid photo ID (passport or driver's license)", "Your Employment Authorization Card, if you have one"]
    field = next(f for f in extract(text) if f.fact_key.endswith(".bring"))
    assert field.normalized_value == "\n".join(n.bring)
    assert bring_list("Please bring your notice and a photo ID to the appointment.\n") == ["Please bring your notice and a photo ID to the appointment."]
    assert bring_list("Notice Date\n09/20/2026\nPlace of Birth\n") is None and parse(text.replace("Please bring the following:", "")).bring is None
    sheet = client_case.sheet("biometrics", "I-485", None, "pt", n.bring)
    assert sheet["letter_list"] == n.bring and sheet["letter_label"].startswith("A sua carta lista estas coisas")  # English as written; our words around it
    assert client_case.sheet("biometrics", "I-485", None, "pt")["letter_list"] is None


def test_the_verifiers_three_probes_of_the_bring_rule():
    # 1: a sentence is the whole list: the lines under it are not things to bring
    assert bring_list("Please bring your notice and a photo ID to the appointment.\n- Failure to appear may result in the denial of your application.\n"
                      "- Rescheduling: call 1-800-375-5283\n") == ["Please bring your notice and a photo ID to the appointment."]
    # a list stops at the first line that is not a list item
    assert bring_list("Please bring the following:\n- This notice\n- A photo ID\nFailure to appear may delay your case.\n- Rescheduling: call 1-800-375-5283\n") == [
        "This notice", "A photo ID"]
    # 2: never cut, never labelled exact when cut: eleven items are all there; a list too long to hold, or an item too long, is not read
    eleven = [f"Item number {n}" for n in range(1, 12)]
    assert bring_list("Please bring the following:\n" + "\n".join(f"{n}. {x}" for n, x in enumerate(eleven, 1))) == eleven
    assert bring_list("Please bring the following:\n" + "\n".join(f"- Item {n}" for n in range(41))) is None
    assert bring_list("Please bring the following:\n- " + "x" * 601) is None and bring_list("Please bring the following:\n- " + "x" * 600) == ["x" * 600]
    # 3: the real I-797C's layout: the label ends a line, the items are numbered
    real = "NOTICE OF ACTION\nYou are hereby notified to appear for fingerprinting. YOU MUST BRING:\n1. THIS APPOINTMENT NOTICE and\n2. PHOTO IDENTIFICATION.\n\nPlease be on time."
    assert bring_list(real) == ["THIS APPOINTMENT NOTICE and", "PHOTO IDENTIFICATION."]


def test_a_list_read_from_a_notice_reaches_no_client_until_a_person_confirms_it(tmp_path):
    d = _case(tmp_path)
    key = f"{RECEIPT}.biometrics.2026-09-20"
    _add_notice(d, RECEIPT, "I-485", "biometrics", "2026-09-20", appointment="2026-10-10 09:30 AM", where="USCIS ASC, 1 Example Way, Boston, MA 02110",
                bring="This appointment notice\nA valid photo ID")
    j = journey.journey(d, TODAY)
    assert (j["notices"][0]["bring_state"], j["notices"][0]["where_state"]) == ("unconfirmed", "unconfirmed")
    page = journey.client_view(j, "pt")["prepare"][0]["sheet"]
    assert page["letter_list"] is None and page["bring"][-1] == "Leve o que a sua carta pedir."  # "Bring what your notice lists", and no list
    assert client_case.upcoming(j)[0]["bring"] is None and client_case.upcoming(j)[0]["where_confirmed"] is False
    assert [(r["id"], r["bring"], r["bring_state"]) for r in j["notice_reads"]] == [(key, ["This appointment notice", "A valid photo ID"], "unconfirmed")]
    with pytest.raises(ValueError, match="read again"):
        journey.mark(d, "bring_confirm", "Paulo Paralegal", key, ["Something else"])  # a person confirms what they looked at
    with pytest.raises(ValueError, match="No such appointment"):
        journey.mark(d, "bring_confirm", "Paulo Paralegal", "no.such.notice", ["x"])
    with pytest.raises(ValueError, match="Enter your name"):
        journey.mark(d, "bring_confirm", "", key, ["This appointment notice", "A valid photo ID"])
    journey.mark(d, "bring_confirm", "Paulo Paralegal", key, ["This appointment notice", "A valid photo ID"])
    j = journey.journey(d, TODAY)
    sheet = journey.client_view(j, "pt")["prepare"][0]["sheet"]
    assert sheet["letter_list"] == ["This appointment notice", "A valid photo ID"] and sheet["bring"][-1] != "Leve o que a sua carta pedir."
    assert client_case.upcoming(j)[0]["bring"] == ["This appointment notice", "A valid photo ID"]
    saved = json.loads((d / "status.json").read_text(encoding="utf-8"))["journey"]["bring"][key]
    assert (saved["state"], saved["by"]) == ("confirmed", "Paulo Paralegal") and saved["at"]
    said = [r for r in events.rows(events.base_path(tmp_path.parent), case="case-ana") if r["what"].startswith("Confirmed the notice's list of what to bring")]
    assert said and said[-1]["who"] == "Paulo Paralegal" and "photo ID" not in json.dumps(said)  # who and when, never the words
    # "Not this" keeps it from the client; a notice read again with other words than the ones a person looked at asks again
    journey.mark(d, "bring_reject", "Paulo Paralegal", key, ["This appointment notice", "A valid photo ID"])
    assert journey.client_view(journey.journey(d, TODAY), "pt")["prepare"][0]["sheet"]["letter_list"] is None
    notices = [{"receipt": RECEIPT, "kind": "biometrics", "date": "2026-09-20", "bring": ["Words read the second time"], "where": None}]
    client_case.apply_reads(notices, {"bring": {key: {"state": "confirmed", "lines": ["This appointment notice", "A valid photo ID"]}}})
    assert notices[0]["bring_state"] == "unconfirmed"


def test_an_address_read_from_a_notice_is_the_places_only_once_a_person_confirms_or_types_it(tmp_path):
    d = _case(tmp_path)
    key = f"{RECEIPT}.biometrics.2026-09-20"
    _add_notice(d, RECEIPT, "I-485", "biometrics", "2026-09-20", appointment="2026-10-10 09:30 AM", where="USCIS ASC, 1 Example Way, Boston, MA 02110")
    assert client_case.upcoming(journey.journey(d, TODAY))[0]["where_confirmed"] is False
    journey.mark(d, "place_confirm", "Paulo Paralegal", key, "USCIS ASC, 1 Example Way, Boston, MA 02110")
    assert client_case.upcoming(journey.journey(d, TODAY))[0]["where_confirmed"] is True
    journey.mark(d, "place_reject", "Paulo Paralegal", key, "USCIS ASC, 1 Example Way, Boston, MA 02110")
    j = journey.journey(d, TODAY)
    assert client_case.upcoming(j)[0]["where"] is None and journey.client_view(j, "en")["prepare"][0]["where"] is None  # said to be wrong: not shown at all
    with pytest.raises(ValueError, match="at least 8"):
        journey.mark(d, "place_set", "Paulo Paralegal", key, "Boston")
    journey.mark(d, "place_set", "Paulo Paralegal", key, "  USCIS Application Support Center,  10 Real Street, Boston, MA 02110 ")
    a = client_case.upcoming(journey.journey(d, TODAY))[0]
    assert a["where"] == "USCIS Application Support Center, 10 Real Street, Boston, MA 02110" and a["where_confirmed"] is True


def test_a_sheet_is_a_pdf_the_client_keeps_and_the_office_prints_with_a_draft_line(tmp_path):
    from pypdf import PdfReader

    sheet = client_case.sheet("biometrics", "I-485", None, "pt", ["This appointment notice"], "(617) 555-0100")
    labels = {"when": "Quando", "where": "Onde", "bring": "O que levar"}
    pdf = client_case.sheet_pdf(sheet, {"when": "sábado, 10 de outubro de 2026 · 09:30", "where": "USCIS ASC, 1 Example Way, Boston, MA 02110"}, labels, "Example Law")
    text = PdfReader(__import__("io").BytesIO(pdf)).pages[0].extract_text()
    assert pdf.startswith(b"%PDF") and "I-797C" in text and "This appointment notice" in text and "10 de outubro de 2026" in text and "DRAFT" not in text
    printed = client_case.sheet_pdf(sheet, {"when": "x", "where": None}, labels, "Example Law", draft=True)
    assert "DRAFT" in PdfReader(__import__("io").BytesIO(printed)).pages[0].extract_text()


def test_the_clients_page_carries_the_sheet_and_the_phone_downloads_it(tmp_path, monkeypatch):
    cases = tmp_path / "clients"
    d = _case(cases)
    _add_notice(d, RECEIPT, "I-485", "biometrics", "2026-09-20", appointment="2026-10-10 09:30 AM", where="USCIS ASC, 1 Example Way, Boston, MA 02110",
                bring="This appointment notice\nA valid photo ID")
    monkeypatch.setenv("I485_CASES", str(cases))
    j = journey.journey(d, TODAY)
    views = {lang: journey.client_view(j, lang) for lang in client_case.LANGS}
    page = views["pt"]["prepare"][0]
    assert page["id"] == f"{RECEIPT}.biometrics.2026-09-20" and page["sheet"]["letter_list"] is None  # read from the notice, not yet confirmed by a person
    journey.mark(d, "bring_confirm", "Paulo Paralegal", page["id"], ["This appointment notice", "A valid photo ID"])
    j = journey.journey(d, TODAY)
    views = {lang: journey.client_view(j, lang) for lang in client_case.LANGS}
    page = views["pt"]["prepare"][0]
    assert page["sheet"]["letter_list"] == ["This appointment notice", "A valid photo ID"]
    assert page["sheet"]["title"].startswith("Coleta de digitais (Formulário I-485)")
    store = PortalStore(tmp_path / "portal")
    store.add_client("case-ana", "Ana Clara Exemplo Souza", email="ana@example.com", language="pt")
    store.save_journey("case-ana", views)
    web = TestClient(create_app(root=tmp_path / "portal", base_url="https://portal.example", secure_cookies=False, notifier=Notifier(tmp_path / "outbox.jsonl", env={})))
    assert web.get("/api/sheet/0").status_code == 401  # signed in first
    web.get(f"/l/{store.new_link_token('case-ana')}", follow_redirects=False)
    r = web.get("/api/sheet/0")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    assert web.get("/api/sheet/1").status_code == 404 and web.get("/api/sheet/-1").status_code == 404
    from pypdf import PdfReader

    text = " ".join(PdfReader(__import__("io").BytesIO(r.content)).pages[0].extract_text().split())
    assert "This appointment notice" in text and "sábado, 10 de outubro de 2026" in text
    assert "Boston" not in text and "O endereço está na sua carta. Confira lá." in text  # the address was read by a program and nobody has checked it
    journey.mark(d, "place_confirm", "Paulo Paralegal", page["id"], "USCIS ASC, 1 Example Way, Boston, MA 02110")
    store.save_journey("case-ana", {lang: journey.client_view(journey.journey(d, TODAY), lang) for lang in client_case.LANGS})
    text = " ".join(PdfReader(__import__("io").BytesIO(web.get("/api/sheet/0").content)).pages[0].extract_text().split())
    assert "USCIS ASC, 1 Example Way, Boston, MA 02110" in text and "Confira este endereço na sua carta." in text and "Confira lá" not in text


# -- the reminders ---------------------------------------------------------------------------------------------------------


def _portal_client(data: Path, case_id: str, name: str, **kw) -> PortalStore:
    store = PortalStore(data / "portal")
    store.add_client(case_id, name, **kw)
    return store


def _outbox(data: Path) -> list[dict]:
    path = data / "portal" / "outbox.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _appointments(d: Path) -> None:
    _add_notice(d, RECEIPT, "I-485", "biometrics", "2026-09-20", appointment="2026-10-10 09:30 AM", where="USCIS ASC, 1 Example Way, Boston, MA 02110")
    _add_notice(d, RECEIPT, "I-485", "interview", "2026-09-25", appointment="2026-10-04 11:00 AM", where="USCIS Field Office, 2 Example Plaza, Boston, MA 02110")


def test_reminders_the_week_before_and_the_day_before_by_the_channels_the_client_agreed_to(tmp_path):
    data = tmp_path / "data"
    cid = tmp_path.name[:60]  # its own case id: the event ledger is shared by every test
    cases = data / "clients"
    d = _case(cases, cid)
    _appointments(d)
    # a person checked the interview's address against the notice; nobody checked the fingerprint appointment's
    journey.mark(d, "place_confirm", "Paulo Paralegal", f"{RECEIPT}.interview.2026-09-25", "USCIS Field Office, 2 Example Plaza, Boston, MA 02110")
    _portal_client(data, cid, "Ana Clara Exemplo Souza", email="ana@example.com", phone="+1 555 010 0100", language="pt", consent={"email": True, "sms": True, "whatsapp": False})
    line = client_reminders.nightly(cases, data, TODAY)
    assert line == "Client reminders: 2 waiting in the outbox (no provider)."  # two reminders (an e-mail and a text each: four rows below); no provider here
    rows = _outbox(data)
    assert sorted((r["channel"], r["to"]) for r in rows) == [("email", "ana@example.com")] * 2 + [("sms", "+1 555 010 0100")] * 2
    day = next(r for r in rows if r["channel"] == "email" and "amanhã" in r["subject"])
    phone = client_case.office_phone(d)
    assert day["body"].startswith("Lembrete do escritório ") and "você tem a sua entrevista amanhã, domingo, 4 de outubro de 2026 às 11:00." in day["body"]
    assert "Local: USCIS Field Office, 2 Example Plaza, Boston, MA 02110. Confira este endereço na sua carta." in day["body"] and "Leve a sua carta e o seu passaporte." in day["body"]
    assert (f"Ligue para o escritório: {phone}." in day["body"]) if phone else "Ligue" not in day["body"]
    week = next(r for r in rows if r["channel"] == "email" and "10 de outubro" in r["subject"])
    assert "a sua coleta de digitais em sábado, 10 de outubro de 2026 às 09:30." in week["body"] and "Responda PARE" not in week["body"]
    assert "O endereço está na sua carta. Confira lá." in week["body"] and "Example Way" not in week["body"]  # an address nobody checked is not sent
    assert "Responda PARE para não receber mais." in next(r for r in rows if r["channel"] == "sms")["body"]  # the text carries the opt-out words
    assert not re.search(r"/l/", json.dumps(rows))  # a reminder holds no sign-in link
    sent = client_reminders.read(d)["sent"]
    assert sorted((v["kind"], v["appointment"], v["for"], v["status"]) for v in sent.values()) == [("day", "interview", "2026-10-04", "queued"), ("week", "biometrics", "2026-10-10", "queued")]
    ledger = [r for r in events.rows(events.base_path(data), case=cid) if r["action"] == "reminded"]
    assert len(ledger) == 2 and all(r["what"] == "Sent the client a reminder about an appointment" and "2026" not in r["what"] for r in ledger)
    assert client_reminders.nightly(cases, data, TODAY) == "Client reminders: nothing to send." and len(_outbox(data)) == 4  # a second run sends nothing twice


def test_a_protected_case_and_a_client_with_no_consent_get_nothing(tmp_path):
    data = tmp_path / "data"
    cases = data / "clients"
    rosa = _case(cases, "case-rosa", "Rosa Exemplo")
    nina = _case(cases, "case-nina", "Nina Exemplo Lima")
    for d in (rosa, nina):
        _appointments(d)
    restricted.mark(rosa, True, "The client asked that only the attorney see this case.", "Sam Attorney", "attorney")
    _portal_client(data, "case-rosa", "Rosa Exemplo", email="rosa@example.com", language="pt", consent={"email": True, "sms": True, "whatsapp": False})
    _portal_client(data, "case-nina", "Nina Exemplo Lima", email="nina@example.com", language="es", consent={"email": False, "sms": False, "whatsapp": False})
    line = client_reminders.nightly(cases, data, TODAY)
    assert _outbox(data) == []  # nothing on any channel
    assert "2 left to the office (protected case)" in line and "2 with no channel the client agreed to" in line
    assert {v["status"] for v in client_reminders.read(rosa)["sent"].values()} == {"hand"}  # the office reaches that client by hand: settled
    assert {v["status"] for v in client_reminders.read(nina)["sent"].values()} == {"none"}
    assert "2 with no channel the client agreed to" in client_reminders.nightly(cases, data, TODAY) and "left to the office" not in client_reminders.nightly(cases, data, TODAY)  # "none" is tried again; "hand" is settled
    store = PortalStore(data / "portal")
    store.update_profile("case-nina", consent={"email": True, "sms": False, "whatsapp": False})  # the client agrees: the next night's reminder goes, in Spanish
    assert client_reminders.nightly(cases, data, TODAY) == "Client reminders: 2 waiting in the outbox (no provider)."
    assert {r["to"] for r in _outbox(data)} == {"nina@example.com"} and all("Recordatorio" in r["body"] for r in _outbox(data))


def test_an_ended_case_and_an_appointment_more_than_a_week_away_send_nothing(tmp_path):
    data = tmp_path / "data"
    cid = tmp_path.name[:60]  # its own case id: the event ledger is shared by every test
    cases = data / "clients"
    d = _case(cases, cid)
    _add_notice(d, RECEIPT, "I-485", "biometrics", "2026-09-20", appointment="2026-10-12 09:30 AM")  # nine days away
    _portal_client(data, cid, "Ana Clara Exemplo Souza", email="ana@example.com", language="en", consent={"email": True, "sms": False, "whatsapp": False})
    assert client_reminders.nightly(cases, data, TODAY) == "Client reminders: nothing to send." and _outbox(data) == []
    assert client_reminders.nightly(cases, data, date(2026, 10, 6)) == "Client reminders: 1 waiting in the outbox (no provider)."  # six days away: the week before
    assert client_reminders.kind_of(0) is None and client_reminders.kind_of(1) == "day" and client_reminders.kind_of(7) == "week" and client_reminders.kind_of(8) is None
    assert "Reminder from" in _outbox(data)[0]["body"] and "on Monday, October 12, 2026 at 9:30 AM." in _outbox(data)[0]["body"]


def test_reports_count_the_reminders_without_naming_a_case(tmp_path):
    data = tmp_path / "data"
    cid = tmp_path.name[:60]  # its own case id: the event ledger is shared by every test
    cases = data / "clients"
    d = _case(cases, cid)
    _appointments(d)
    _portal_client(data, cid, "Ana Clara Exemplo Souza", email="ana@example.com", language="pt", consent={"email": True, "sms": False, "whatsapp": False})
    client_reminders.nightly(cases, data, TODAY)
    table = client_reminders.summary([{"id": cid}], cases, TODAY)
    assert table == [{"reminder": "The week before", "total": 1, "sent": 0, "queued": 1, "hand": 0, "held": 0, "none": 0, "failed": 0},
                     {"reminder": "The day before", "total": 1, "sent": 0, "queued": 1, "hand": 0, "held": 0, "none": 0, "failed": 0}]
    assert client_reminders.summary([], cases, TODAY)[0]["total"] == 0  # a case the reader may not open is not counted


def test_a_restricted_case_gets_no_reminder_even_when_an_attorney_switched_messages_on(tmp_path):
    data = tmp_path / "data"
    cases = data / "clients"
    rosa = _case(cases, "case-rosa", "Rosa Exemplo")
    _add_notice(rosa, RECEIPT, "I-485", "interview", "2026-09-25", appointment="2026-10-04 11:00 AM", where="USCIS Field Office, 2 Example Plaza, Boston, MA 02110")
    restricted.mark(rosa, True, "The client asked that only the attorney see this case.", "Sam Attorney", "attorney")
    restricted.set_messages(rosa, True, "The client asked for texts about her case.", "Sam Attorney", "attorney")  # made for messages that carry only a link
    assert restricted.messages_allowed(rosa) and restricted.is_restricted(rosa)
    _portal_client(data, "case-rosa", "Rosa Exemplo", email="rosa@example.com", phone="+1 555 010 0100", language="pt", consent={"email": True, "sms": True, "whatsapp": False})
    lines = [client_reminders.nightly(cases, data, TODAY) for _ in range(3)]  # three nights
    assert _outbox(data) == []  # nothing on any channel
    assert lines == ["Client reminders: 1 left to the office (protected case).", "Client reminders: nothing to send.", "Client reminders: nothing to send."]
    assert client_reminders.summary([{"id": "case-rosa"}], cases, TODAY)[1]["hand"] == 1  # Reports: "left to the office"
    ledger = [r for r in events.rows(events.base_path(data), case="case-rosa") if r["action"] in ("reminded", "failed")]
    assert ledger == []


def test_a_failed_channel_is_tried_again_alone_and_a_channel_that_sent_is_never_sent_again(tmp_path, monkeypatch):
    data = tmp_path / "data"
    cid = tmp_path.name[:60]  # its own case id: the event ledger is shared by every test
    cases = data / "clients"
    d = _case(cases, cid)
    _add_notice(d, RECEIPT, "I-485", "biometrics", "2026-09-20", appointment="2026-10-10 09:30 AM")
    _portal_client(data, cid, "Ana Clara Exemplo Souza", email="ana@example.com", phone="+1 555 010 0100", language="pt", consent={"email": True, "sms": True, "whatsapp": False})
    tried = []

    def broken(self, to, subject, body, kind):
        tried.append(to)
        raise OSError("the text service is down")

    monkeypatch.setattr(Notifier, "_sms", broken)
    lines = [client_reminders.nightly(cases, data, date(2026, 10, 3 + n)) for n in range(3)]  # three nights
    assert lines == ["Client reminders: 1 could not be sent."] * 3
    assert [r["channel"] for r in _outbox(data)] == ["email"]  # one e-mail, not one a night
    assert len(tried) == 3  # the text was tried each night
    entry = next(iter(client_reminders.read(d)["sent"].values()))
    assert entry["channels"] == {"email": "queued", "sms": "failed", "whatsapp": "skipped"} and entry["status"] == "failed"
    ledger = [r["action"] for r in events.rows(events.base_path(data), case=cid) if r["action"] in ("reminded", "failed")]
    assert ledger.count("reminded") == 1 and ledger.count("failed") == 3  # one row for each attempt that sent or failed
    monkeypatch.undo()
    client_reminders.nightly(cases, data, date(2026, 10, 6))  # the service is back
    assert [r["channel"] for r in _outbox(data)] == ["email", "sms"]  # the text goes, the e-mail is not sent again
    assert next(iter(client_reminders.read(d)["sent"].values()))["status"] == "queued"
    assert client_reminders.nightly(cases, data, date(2026, 10, 7)) == "Client reminders: nothing to send." and len(_outbox(data)) == 2


def test_a_night_when_nothing_was_sent_and_nothing_failed_leaves_no_ledger_row(tmp_path):
    data = tmp_path / "data"
    cid = tmp_path.name[:60]  # its own case id: the event ledger is shared by every test
    cases = data / "clients"
    d = _case(cases, cid)
    _add_notice(d, RECEIPT, "I-485", "biometrics", "2026-09-20", appointment="2026-10-10 09:30 AM")
    _portal_client(data, cid, "Ana Clara Exemplo Souza", email="ana@example.com", language="pt", consent={"email": False, "sms": False, "whatsapp": False})
    for n in range(7):
        client_reminders.nightly(cases, data, date(2026, 10, 3 + n))
    assert [r for r in events.rows(events.base_path(data), case=cid) if r["action"] in ("reminded", "failed", "skipped")] == []
    assert next(iter(client_reminders.read(d)["sent"].values()))["status"] == "none"


def test_a_rescheduled_appointment_is_reminded_for_its_new_date_only(tmp_path):
    data = tmp_path / "data"
    cid = tmp_path.name[:60]  # its own case id: the event ledger is shared by every test
    cases = data / "clients"
    d = _case(cases, cid)
    _add_notice(d, RECEIPT, "I-485", "biometrics", "2026-09-20", appointment="2026-10-08 09:30 AM")  # the first notice
    _add_notice(d, RECEIPT, "I-485", "biometrics", "2026-09-28", appointment="2026-10-10 02:00 PM")  # rescheduled
    _portal_client(data, cid, "Ana Clara Exemplo Souza", email="ana@example.com", language="en", consent={"email": True, "sms": False, "whatsapp": False})
    client_reminders.nightly(cases, data, TODAY)
    rows = _outbox(data)
    assert len(rows) == 1 and "October 10, 2026" in rows[0]["body"] and "October 8" not in rows[0]["body"]


def test_a_hearings_court_is_the_place_and_it_says_to_check_the_notice(tmp_path):
    data = tmp_path / "data"
    cid = tmp_path.name[:60]  # its own case id: the event ledger is shared by every test
    cases = data / "clients"
    d = _case(cases, cid)
    journey.mark(d, "hearing", "Paulo Paralegal", value={"date": "2026-10-04", "time": "9:00 AM", "kind": "Master calendar", "court": "Boston Immigration Court"})
    _portal_client(data, cid, "Ana Clara Exemplo Souza", email="ana@example.com", language="es", consent={"email": True, "sms": False, "whatsapp": False})
    client_reminders.nightly(cases, data, TODAY)
    (row,) = _outbox(data)
    assert "su audiencia en la corte mañana, domingo, 4 de octubre de 2026 a las 09:00. Lugar: Boston Immigration Court. Revise la corte y la sala en la notificación de la audiencia." in row["body"]


def test_uscis_descriptions_have_their_character_references_read():
    rec = case_status.record_of(APPROVED | {"current_case_status_desc_en": "<p>Mom &amp; Dad&#39;s case&nbsp;was approved &lt;today&gt;.</p>"}, datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc), "production")
    assert rec["desc"] == "Mom & Dad's case was approved <today>."


# -- how was this step ---------------------------------------------------------------------------------------------------


def _web(tmp_path, monkeypatch, case_dir: Path | None = None, **profile):
    cases = tmp_path / "clients"
    cases.mkdir(exist_ok=True)
    monkeypatch.setenv("I485_CASES", str(cases))
    store = PortalStore(tmp_path / "portal")
    store.add_client("case-ana", "Ana Clara Exemplo Souza", email="ana@example.com", language=profile.get("language", "pt"))
    web = TestClient(create_app(root=tmp_path / "portal", base_url="https://portal.example", secure_cookies=False, notifier=Notifier(tmp_path / "outbox.jsonl", env={})))
    web.get(f"/l/{store.new_link_token('case-ana')}", follow_redirects=False)
    return web, store


def test_one_tap_of_feedback_after_a_milestone(tmp_path, monkeypatch):
    d = _case(tmp_path / "clients")
    web, store = _web(tmp_path, monkeypatch)
    milestone = {"id": f"biometrics.{RECEIPT}.2026-10-01", "kind": "biometrics", "date": "2026-10-01"}
    old = {"id": "mailed.i485.2026-07-01", "kind": "mailed", "date": "2026-07-01"}  # older than a month: not asked about
    future = {"id": f"interview.{RECEIPT}.2026-10-20", "kind": "interview", "date": "2026-10-20"}
    store.save_journey("case-ana", {"pt": {"stage": "i485_pending", "milestones": [milestone, old, future]}})
    ask = web.get("/api/me").json()["feedback"]
    assert (ask["id"], ask["kind"], ask["step"]) == (milestone["id"], "biometrics", "A sua coleta de digitais")
    assert ask["labels"]["feedback_prompt"] == "Como foi este passo?" and ask["labels"]["feedback_bad"] == "Ruim"
    assert web.post("/api/feedback", json={"step": milestone["id"], "face": "great", "comment": ""}, headers=H).status_code == 400  # one of the three faces
    assert web.post("/api/feedback", json={"step": milestone["id"], "face": "good", "comment": "x" * 501}, headers=H).json()["detail"] == "feedback_too_long"
    assert web.post("/api/feedback", json={"step": old["id"], "face": "good", "comment": ""}, headers=H).status_code == 400  # only the step the page asked about
    assert web.post("/api/feedback", json={"step": milestone["id"], "face": "good", "comment": "Foi rápido."}).status_code == 403  # a write needs the portal's header
    state = web.post("/api/feedback", json={"step": milestone["id"], "face": "good", "comment": "  Foi   rápido.  "}, headers=H).json()
    assert state["feedback"] is None  # asked once; the next step to ask about is none
    row = store.feedback("case-ana")[0]
    assert (row["step"], row["kind"], row["face"], row["comment"], row["language"]) == (milestone["id"], "biometrics", "good", "Foi rápido.", "pt")
    assert web.post("/api/feedback", json={"step": milestone["id"], "face": "bad", "comment": ""}, headers=H).status_code == 400 and len(store.feedback("case-ana")) == 1
    assert client_case.read_feedback(d)[0]["comment"] == "Foi rápido."  # on the case at once: the case is on this machine
    assert client_case.sync_feedback(d, tmp_path / "portal") == 0 and len(client_case.read_feedback(d)) == 1  # copied once
    ours = {r["action"] for r in events.rows(events.base_path(tmp_path), case="case-ana") if r["kind"] == "portal"}
    assert {"answered", "copied"} <= ours
    assert not any("rápido" in json.dumps(r) for r in events.rows(events.base_path(tmp_path), case="case-ana"))  # the ledger never holds what they wrote


def test_the_portal_milestones_are_the_agreement_and_the_documents(tmp_path, monkeypatch):
    web, store = _web(tmp_path, monkeypatch)
    assert web.get("/api/me").json()["feedback"] is None
    store.update_profile("case-ana", submitted_at="2026-10-01T15:00:00+00:00")
    assert web.get("/api/me").json()["feedback"]["id"] == "documents"
    store.save_engagement("case-ana", {"letter": {"id": "L1"}, "signed": {"letter": "L1", "how": "portal", "typed_name": "Ana", "at": "2026-10-02T20:00:00-04:00"}})
    assert web.get("/api/me").json()["feedback"]["id"] == "agreement"  # the latest first


def test_the_step_milestones_come_from_the_firms_records():
    j = {"today": "2026-10-03", "filings": [{"filing": "i485", "mailed_on": "2026-09-01"}, {"filing": "bia", "mailed_on": "2026-09-02"}],
         "notices": [{"receipt": RECEIPT, "kind": "biometrics", "appointment": "2026-09-20 09:00 AM", "date": "2026-09-01"},
                     {"receipt": RECEIPT, "kind": "interview", "appointment": "2026-10-20 09:00 AM", "date": "2026-09-05"},  # still to come
                     {"receipt": RECEIPT, "kind": "approval", "date": "2026-09-29"}]}
    assert [(m["kind"], m["date"]) for m in client_case.milestones(j)] == [("decision", "2026-09-29"), ("biometrics", "2026-09-20"), ("mailed", "2026-09-01")]
    assert client_case.step_name("mailed.i485.2026-09-01") == "Mailing the application" and client_case.step_name("weird") == "Another step"


def test_reports_count_each_step_and_office_and_show_a_restricted_cases_words_only_to_those_who_may_open_it(world, app, monkeypatch):
    for cid in ("case-ana", "case-rosa"):
        store = PortalStore(world.parent / "portal")
        if cid not in store.clients():
            store.add_client(cid, cid, email=f"{cid}@example.com", language="pt")
    store = PortalStore(world.parent / "portal")
    store.add_feedback("case-ana", f"biometrics.{RECEIPT}.2026-10-01", "biometrics", "good", "Foi fácil e rápido.", "pt")
    store.add_feedback("case-ana", "agreement", "agreement", "bad", "", "pt")
    store.add_feedback("case-rosa", f"interview.{RECEIPT}.2026-10-01", "interview", "bad", "Tive medo da entrevista.", "pt")  # a VAWA case: restricted by law
    for who, sees_rosa in ((JANE, False), (SAM, True)):
        tables = {t["id"]: t for t in app.reports(who["role"], who)["tables"]}
        counts = {(r["step"], r["office"]): (r["good"], r["ok"], r["bad"], r["answers"]) for r in tables["client_feedback"]["rows"]}
        assert counts[("Fingerprint appointment", "Every office")] == (1, 0, 0, 1) and counts[("Signing the agreement", "Every office")] == (0, 0, 1, 1)
        assert (("Interview", "Every office") in counts) is sees_rosa  # a protected case is in no count for someone who may not open it
        words = tables["client_feedback_words"]["rows"]
        assert sorted(w["comment"] for w in words) == sorted(["Tive medo da entrevista.", "Foi fácil e rápido."] if sees_rosa else ["Foi fácil e rápido."])
        assert "Tive medo" not in json.dumps(tables["client_feedback"]) and "Ana" not in json.dumps(tables["client_feedback"])  # the counts name no case and hold no sentence
        assert not any("Rosa" in json.dumps(t) for t in tables.values()) or sees_rosa
        csv = tables["client_feedback"]["columns"]
        assert [c["key"] for c in csv] == ["step", "office", "good", "ok", "bad", "answers"]
    assert client_case.read_feedback(world / "case-ana")[0]["face"] == "good"  # Reports copied them onto the cases


# -- an answer that never settles -----------------------------------------------------------------------------------


def test_an_answer_with_no_review_card_says_the_office_has_it_after_seven_days(tmp_path, monkeypatch):
    web, store = _web(tmp_path, monkeypatch)
    free = store.add_request("case-ana", "Qual é o nome da sua escola?", None, "Paulo Paralegal")
    carded = store.add_request("case-ana", "Qual é a sua data de nascimento?", None, "Paulo Paralegal", facts=["applicant.dob"])
    for r in (free, carded):
        store.answer_request("case-ana", r["id"], reply="Escola Exemplo")
    (sent,) = [x for x in web.get("/api/me").json()["sent"] if x["id"] == free["id"]]
    assert sent["kept"] is False and sent["on"] == "2026-10-03"
    def signed_in_again():  # a session lasts twelve hours: the days below are days later
        web.get(f"/l/{store.new_link_token('case-ana')}", follow_redirects=False)

    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 9, 12, 0))  # six days later
    signed_in_again()
    assert all(not x["kept"] for x in web.get("/api/me").json()["sent"])
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 10, 12, 0))  # seven days
    signed_in_again()
    shown = {x["id"]: x for x in web.get("/api/me").json()["sent"]}
    assert shown[free["id"]]["kept"] is True and shown[free["id"]]["kept_text"] == "O escritório recebeu a sua resposta"
    assert shown[carded["id"]]["kept"] is False  # a review card is behind this one: the card's decision settles it
    waiting = client_case.answers_waiting(tmp_path / "portal" / "clients" / "case-ana", date(2026, 10, 10))
    assert [(a["id"], a["days"], a["text"]) for a in waiting] == [(free["id"], 7, "Qual é o nome da sua escola?")]
    store.settle_requests("case-ana", "Paulo Paralegal", [free["id"]])  # Mark done
    assert all(x["id"] != free["id"] for x in web.get("/api/me").json()["sent"]) and client_case.answers_waiting(tmp_path / "portal" / "clients" / "case-ana", date(2026, 10, 10)) == []
    assert client_case.label("answer_kept", "en") == "The office has your answer" and client_case.label("answer_kept", "ht") == "Biwo a resevwa repons ou a"


def test_my_work_lists_the_answers_waiting_a_week_for_the_cases_a_person_may_open(world, app, monkeypatch):
    store = PortalStore(world.parent / "portal")
    store.add_client("case-ana", "Ana Clara Exemplo Souza", email="ana@example.com", language="pt")
    free = store.add_request("case-ana", "Qual é o nome da sua escola?", None, "Paulo Paralegal")
    store.answer_request("case-ana", free["id"], reply="Escola Exemplo")
    rosa = store.add_request("case-rosa", "Uma pergunta para a Rosa", None, "Paulo Paralegal")  # the VAWA case
    store.answer_request("case-rosa", rosa["id"], reply="resposta")
    app.roster.touch("case-ana"), app.roster.touch("case-rosa")  # (the portal lists them in its queue when a client answers)
    assert app.work("paralegal", "paralegal", JANE)["answers_week"] == []  # not yet a week
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 11, 9, 0))
    app.roster.touch("case-ana"), app.roster.touch("case-rosa")  # a week on: the rows are built again for the new day (every row is, in the background, after midnight)
    mine = app.work("paralegal", "paralegal", JANE)
    assert [(a["client"], a["days"]) for a in mine["answers_week"]] == [("case-ana", 8)] and mine["counts"]["answers_week"] == 1  # Rosa's case is not Jane's to see
    assert {a["client"] for a in app.work(None, "attorney", SAM)["answers_week"]} == {"case-ana", "case-rosa"}
    from review.overview import my_work

    assert my_work([{"id": "x", "answers_week": [{"id": "r", "days": 9, "text": "t", "at": "2026-10-01T10:00:00-04:00"}]}], "attorney")["answers_week"] == []  # the paralegal's list
    assert my_work([{"id": "x", "answers_week": [{"id": "r", "days": 9, "text": "t", "at": "2026-10-01T10:00:00-04:00"}]}], "paralegal")["counts"]["answers_week"] == 1


# -- the staff side of a sheet and the files ---------------------------------------------------------------------------


def test_the_office_prints_the_sheet_in_the_clients_language(world, app, monkeypatch):
    d = world / "case-ana"
    _add_notice(d, RECEIPT, "I-485", "interview", "2026-09-25", appointment="2026-10-20 11:00 AM", where="USCIS Field Office, 2 Example Plaza, Boston, MA 02110")
    store = PortalStore(world.parent / "portal")
    store.add_client("case-ana", "Ana Clara Exemplo Souza", email="ana@example.com", language="es")
    from pypdf import PdfReader

    pdf = app.prepare_sheet("case-ana", f"{RECEIPT}.interview.2026-09-25")
    text = " ".join(PdfReader(__import__("io").BytesIO(pdf)).pages[0].extract_text().split())
    assert "Entrevista (Formulario I-485)" in text and "DRAFT" in text and "martes, 20 de octubre de 2026" in text
    assert "Example Plaza" not in text and "La dirección está en su carta. Revísela allí." in text  # an address nobody checked is not printed
    journey.mark(d, "place_confirm", "Paulo Paralegal", f"{RECEIPT}.interview.2026-09-25", "USCIS Field Office, 2 Example Plaza, Boston, MA 02110")
    after = " ".join(PdfReader(__import__("io").BytesIO(app.prepare_sheet("case-ana", f"{RECEIPT}.interview.2026-09-25"))).pages[0].extract_text().split())
    assert "USCIS Field Office, 2 Example Plaza, Boston, MA 02110" in after and "Revise esta dirección en su carta." in after and "Revísela allí" not in after
    assert "Interview (Form I-485)" in PdfReader(__import__("io").BytesIO(app.prepare_sheet("case-ana", f"{RECEIPT}.interview.2026-09-25", "en"))).pages[0].extract_text()
    with pytest.raises(LookupError):
        app.prepare_sheet("case-ana", "no-such-appointment")
    with pytest.raises(LookupError):
        app.prepare_sheet("case-rosa-missing", f"{RECEIPT}.interview.2026-09-25")


def test_a_named_paralegal_confirms_what_a_notice_says_and_one_not_named_gets_the_unknown_case_answer(world, server):
    rosa = world / "case-rosa"  # a VAWA case: restricted by law
    lines = ["This appointment notice", "A valid photo ID"]
    _add_notice(rosa, RECEIPT, "I-485", "interview", "2026-09-25", appointment="2026-10-20 11:00 AM", where="USCIS Field Office, 2 Example Plaza, Boston, MA 02110",
                bring="\n".join(lines))
    restricted.name_person(rosa, "kim@firm.example", True, "Sam Attorney", "attorney", "Kim Exemplo")
    key = f"{RECEIPT}.interview.2026-09-25"
    kim, jane = sign_in(server, "kim@firm.example"), sign_in(server, "jane@firm.example")
    body = {"client": "case-rosa", "reviewer": "Kim Exemplo", "action": "bring_confirm", "item": key, "value": lines}
    status, _ = call(server + "/api/journey", kim, body)
    assert status == 200  # checking what a notice says is office work: not the attorney's step
    saved = json.loads((rosa / "status.json").read_text(encoding="utf-8"))["journey"]["bring"][key]
    assert (saved["state"], saved["by"]) == ("confirmed", "Kim Exemplo")
    status, _ = call(server + "/api/journey", kim, {"client": "case-rosa", "reviewer": "Kim Exemplo", "action": "place_confirm", "item": key,
                                                     "value": "USCIS Field Office, 2 Example Plaza, Boston, MA 02110"})
    assert status == 200
    status, said = call(server + "/api/journey", kim, {"client": "case-rosa", "reviewer": "Kim Exemplo", "action": "stage", "item": None, "value": "resident"})
    assert status == 403 and "attorney's to change" in said  # the legal steps stay the attorney's
    # a paralegal not named on the case gets the bytes a case that does not exist gets
    hidden = call(server + "/api/journey", jane, body)
    nothing = call(server + "/api/journey", jane, body | {"client": "case-no-such"})
    assert hidden == nothing and hidden[0] == 404


def test_the_new_files_are_in_the_catalog():
    import records

    assert records.coverage("case", "feedback.jsonl") == "listed" and records.coverage("case", "client_reminders.json") == "listed"
    assert records.coverage("portal", "feedback.json") == "listed"
