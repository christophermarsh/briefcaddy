"""The client's side of the portal: a photo to retake, two-way messages with the office, the page that says what to bring and
where, and the firm-run demo client (src/portal/engine.py retake_tasks, messages.py, journey.py client_view "prepare",
demo.py showcase). Made-up people only. The document record (documents.json) is a fixture in the shape agent B1's
src/documents.py writes; until that is merged the portal reads it through engine.document_records, which returns nothing
when the file is absent."""

import io
import json
import re
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import journey
from extract.uscis_notice import appointment_place, extract, parse
from factgraph import FactGraph
from portal import demo, messages
from portal.app import create_app
from portal.bank import languages
from portal.engine import client_tasks, document_records, stamp_retakes
from portal.notify import Notifier
from portal.store import PortalStore
import schema_path

REPO = Path(__file__).resolve().parent.parent
H = {"X-Portal": "1"}
LANGS = languages()


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(out, format="PNG")
    return out.getvalue()


def _record(stored, quality, type_="passport", id_="rec-1"):
    """One entry of documents.json (the shape agent B1 writes)."""
    return {"id": id_, "files": [stored], "pages": [1], "type": type_, "person": "applicant", "quality": quality}


def _upload(doc_id, n=1, status="received"):
    stored = f"{doc_id}-{n:02d}.pdf"
    return {"id": f"{doc_id}-{n:02d}", "doc_id": doc_id, "filename": "photo.jpg", "stored": stored, "status": status}


def _retakes(records, uploads, lang="pt"):
    return [t for t in client_tasks({}, uploads, FactGraph("x"), lang, records=records) if t["kind"] == "retake"]


# --- retakes -----------------------------------------------------------------------------------------------


def test_a_blurry_cut_off_or_partial_photo_becomes_a_retake_task_naming_the_document_and_why():
    for quality, reasons in {"blurry": {"pt": "fora de foco", "es": "borrosa", "en": "blurry", "ht": "flou"},
                             "cut_off": {"pt": "cortada", "es": "cortada", "en": "cut off", "ht": "koupe"},
                             "partial": {"pt": "incompleta", "es": "incompleta", "en": "incomplete", "ht": "pa konplè"}}.items():
        uploads = [_upload("passport")]
        (task,) = _retakes([_record("passport-01.pdf", quality)], uploads)
        assert task["kind"] == "retake" and task["doc_id"] == "passport" and task["quality"] == quality and task["upload"] == "passport-01"
        assert task["doc_en"] == "passport" and task["why_en"]
        for lang in LANGS:  # the client may switch language after processing: the task carries every language
            assert reasons[lang] in task["texts"][lang], (quality, lang)
        assert "passaporte" in task["texts"]["pt"] and "pasaporte" in task["texts"]["es"] and "passport" in task["texts"]["en"] and "paspò" in task["texts"]["ht"]
        assert task["text"] == task["texts"]["pt"]
        assert uploads[0]["status"] == "retake"  # the document's own line says "send another photo" too


def test_a_readable_document_and_one_the_office_scanned_are_never_put_back_on_the_client():
    uploads = [_upload("passport")]
    assert _retakes([_record("passport-01.pdf", "readable")], uploads) == []
    assert _retakes([_record("passport-01.pdf", "unknown")], uploads) == []
    assert _retakes([_record("scanned-by-the-office.pdf", "blurry")], uploads) == []  # no portal upload behind it
    assert _retakes([], uploads) == []


def test_only_the_latest_photo_is_asked_again_and_a_new_one_replaces_the_request():
    old, new = _upload("passport", 1), _upload("passport", 2)
    assert _retakes([_record("passport-01.pdf", "blurry")], [old, new]) == []  # the newer photo came after the unclear one
    assert [t["upload"] for t in _retakes([_record("passport-02.pdf", "blurry", id_="b")], [old, new])] == ["passport-02"]
    # a combined scan is one record with a part suffix on its file name
    (task,) = _retakes([_record("passport-01.pdf#p1-2", "partial")], [old])
    assert task["quality"] == "partial"


def test_a_photo_that_could_not_be_read_at_all_gets_one_retake_task_not_two():
    uploads = [_upload("passport", status="retake")]
    tasks = _retakes([_record("passport-01.pdf", "blurry")], uploads)
    assert [t["id"] for t in tasks] == ["retake:passport-01"] and "quality" not in tasks[0]  # the earlier, plainer task


def test_the_record_is_read_when_it_is_there_and_nothing_when_it_is_not(tmp_path):
    assert document_records(tmp_path) == []
    (tmp_path / "documents.json").write_text("{not json", encoding="utf-8")
    assert document_records(tmp_path) == []
    (tmp_path / "documents.json").write_text(json.dumps({"version": 1, "documents": [_record("a.pdf", "blurry"), "junk"]}), encoding="utf-8")
    assert [d["id"] for d in document_records(tmp_path)] == ["rec-1"]
    (tmp_path / "documents.json").write_text("[]", encoding="utf-8")
    assert document_records(tmp_path) == []


def test_each_retake_keeps_the_day_it_was_first_asked(tmp_path):
    store = PortalStore(tmp_path)
    store.add_client("pilot-1", "Ana Teste", language="pt")
    tasks = _retakes([_record("passport-01.pdf", "blurry")], [_upload("passport")])
    stamp_retakes(store, "pilot-1", tasks)
    first = tasks[0]["asked_at"]
    assert first and "retake_requested" in (store.client_dir("pilot-1") / "events.jsonl").read_text(encoding="utf-8")
    store.save_tasks("pilot-1", tasks)
    again = _retakes([_record("passport-01.pdf", "blurry")], [_upload("passport")])
    stamp_retakes(store, "pilot-1", again)
    assert again[0]["asked_at"] == first  # the worker rebuilds the list; the date stays


@pytest.fixture
def portal(tmp_path):
    app = create_app(root=tmp_path, base_url="https://portal.example", secure_cookies=False, notifier=Notifier(tmp_path / "outbox.jsonl", env={}))
    store = app.state.store
    store.add_client("pilot-1", "Ana Teste", phone="+15550100199", email="ana@example.com", language="pt")
    return TestClient(app), store, tmp_path


def _sign_in(client, store, cid="pilot-1"):
    assert client.get(f"/l/{store.new_link_token(cid)}", follow_redirects=False).status_code == 303


def test_a_new_upload_for_the_document_keeps_its_retake_task_as_received_until_it_is_read(portal):
    client, store, _ = portal
    tasks = _retakes([_record("passport-01.pdf", "blurry")], [_upload("passport")])
    other = {"id": "retake:birth_certificate-01", "kind": "retake", "doc_id": "birth_certificate", "text": "x"}
    store.save_tasks("pilot-1", tasks + [other])
    _sign_in(client, store)
    me = client.get("/api/me").json()
    assert [t["id"] for t in me["tasks"] if t["kind"] == "retake"] == ["retake:passport-01", "retake:birth_certificate-01"]
    assert "fora de foco" in me["tasks"][0]["text"]
    sent = client.post("/api/upload", data={"doc_id": "passport"}, files={"file": ("photo.png", _png(), "image/png")}, headers=H)
    assert sent.status_code == 200
    # the new photo answers only this document's request, and the request stays as "received" ("Thank you, we have it") until the photo is read
    tasks = {t["id"]: t for t in sent.json()["tasks"] if t["kind"] == "retake"}
    assert set(tasks) == {"retake:passport-01", "retake:birth_certificate-01"}
    assert tasks["retake:passport-01"].get("received_at") and not tasks["retake:birth_certificate-01"].get("received_at")
    [photo] = [u for u in store.uploads("pilot-1") if u["doc_id"] == "passport" and u["status"] == "received"]
    assert photo["retake"] is True and photo["reading"] == "tonight"  # no case here to read it in: the office's list says tonight


# --- messages ----------------------------------------------------------------------------------------------


def test_the_client_writes_and_it_is_kept_with_the_time_in_their_own_folder(portal):
    client, store, tmp = portal
    assert client.post("/api/message", json={"text": "hello"}, headers=H).status_code == 401  # signed in only
    assert client.post("/api/message", json={"text": "hello"}).status_code == 403  # and only from the portal's own page
    _sign_in(client, store)
    assert client.post("/api/message", json={"text": "   "}, headers=H).status_code == 400
    assert client.post("/api/message", json={"text": "x" * 2001}, headers=H).status_code == 413
    me = client.post("/api/message", json={"text": "Posso mandar o passaporte amanhã?"}, headers=H).json()
    assert [(m["from"], m["text"]) for m in me["messages"]] == [("client", "Posso mandar o passaporte amanhã?")]
    (kept,) = store.messages("pilot-1")
    assert kept["status"] == "new" and kept["at"] and kept["language"] == "pt"
    log = (store.client_dir("pilot-1") / "events.jsonl").read_text(encoding="utf-8")
    assert "message_from_client" in log and "passaporte" not in log  # the log says a message came, never what it says
    assert not (tmp / "outbox.jsonl").exists()  # nothing leaves the installation: not even a notification, the office reads it in My work


def test_a_client_is_limited_to_a_few_messages_an_hour(portal):
    client, store, _ = portal
    _sign_in(client, store)
    codes = [client.post("/api/message", json={"text": f"n {n}"}, headers=H).status_code for n in range(22)]
    assert codes[:20] == [200] * 20 and codes[20:] == [429, 429]


def test_the_office_answer_reads_in_the_clients_language_with_the_offices_own_words(portal, monkeypatch):
    client, store, _ = portal
    monkeypatch.setattr("classify.translate.translate_text", lambda text, a, b="en": f"[{b}] {text}")
    store.add_message("pilot-1", "client", "Posso mandar amanhã?", language="pt")
    sent = messages.reply(store, "pilot-1", "Yes, tomorrow is fine.", "Paulo Paralegal")
    assert sent["translations"]["pt"] == {"language": "pt", "text": "[pt] Yes, tomorrow is fine.", "machine": True, "needs_translator": False}
    assert sent["translations"]["ht"]["text"] is None and sent["translations"]["ht"]["needs_translator"]  # Argos has no Haitian Creole
    assert [m["status"] for m in store.messages("pilot-1") if m["from"] == "client"] == ["handled"]  # an answer settles what was waiting
    _sign_in(client, store)
    pt = client.get("/api/me").json()["messages"]
    assert pt[1] == {"id": sent["id"], "from": "office", "at": sent["at"], "unseen": True, "text": "[pt] Yes, tomorrow is fine.",
                     "original": "Yes, tomorrow is fine.", "machine": True}
    es = client.post("/api/language", json={"language": "es"}, headers=H).json()["messages"][1]
    assert es["text"] == "[es] Yes, tomorrow is fine." and es["original"] == "Yes, tomorrow is fine."
    en = client.post("/api/language", json={"language": "en"}, headers=H).json()["messages"][1]
    assert en["text"] == "Yes, tomorrow is fine." and "original" not in en and "followup" not in en
    ht = client.post("/api/language", json={"language": "ht"}, headers=H).json()["messages"][1]
    assert ht["text"] == "Yes, tomorrow is fine." and ht["followup"] is True  # English, and the portal adds the Creole "the office will follow up" line
    assert client.post("/api/message-seen", headers=H).json()["messages"][1]["unseen"] is False


def test_a_changed_draft_is_the_staff_members_own_text_not_a_machine_one(portal):
    _, store, _ = portal
    draft = lambda text, lg: {"language": lg, "text": f"[{lg}] {text}", "machine": True, "needs_translator": False}  # noqa: E731
    sent = messages.reply(store, "pilot-1", "Please bring your passport.", "Paulo", translation="Traga o seu passaporte, por favor.", translate=draft)
    assert sent["translations"]["pt"] == {"language": "pt", "text": "Traga o seu passaporte, por favor.", "machine": False, "needs_translator": False, "edited_by": "Paulo"}
    assert sent["translations"]["es"]["machine"] is True  # the other languages keep their drafts
    with pytest.raises(ValueError):
        messages.reply(store, "pilot-1", "   ", "Paulo", translate=draft)


def test_translating_for_the_client_goes_through_one_function(monkeypatch):
    calls = []
    monkeypatch.setattr("classify.translate.translate_text", lambda text, a, b="en": calls.append((a, b)) or ("olá" if b == "pt" else None))
    assert messages.translate_for_client("hello", "pt") == {"language": "pt", "text": "olá", "machine": True, "needs_translator": False}
    assert messages.translate_for_client("hello", "es") == {"language": "es", "text": None, "machine": False, "needs_translator": True}  # no model installed
    assert messages.translate_for_client("hello", "en")["text"] == "hello"
    assert messages.translate_for_client("hello", "ht")["needs_translator"] and ("en", "ht") not in calls  # never even tried: no Creole model
    assert calls == [("en", "pt"), ("en", "es")]


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, profile, kind, link=""):
        self.sent.append((profile["id"], kind, link))
        return [{"channel": "email", "result": "sent"}]


def test_the_notification_that_the_office_answered_carries_no_case_details(portal):
    _, store, tmp = portal
    fake = FakeNotifier()
    sent = messages.reply(store, "pilot-1", "Your I-360 was approved on 08/20/2025. Bring A900000001.", "Paulo", translate=lambda t, lg: {"language": lg, "text": None, "machine": False, "needs_translator": True})
    result = messages.notify(store, fake, "pilot-1", "https://portal.example/l/abc", sent["id"])
    assert fake.sent == [("pilot-1", "office_reply", "https://portal.example/l/abc")]
    assert result["status"] == "sent" and store.messages("pilot-1")[-1]["delivery"]["status"] == "sent"
    # the real templates: in every language, a link and the firm's name, not a word of the answer
    from portal.notify import TEMPLATES, render

    for lang in LANGS:
        out = render("office_reply", lang, "https://portal.example/l/abc")
        assert "https://portal.example/l/abc" in out["body"] and "Case Review" in out["body"]
        assert not re.search(r"I-360|approved|A\d{9}|passport", out["subject"] + out["body"], re.I)
    assert set(TEMPLATES["office_reply"]) == set(LANGS)


def _review_app(tmp_path, store_root):
    from review.server import ReviewApp

    (tmp_path / "clients").mkdir(exist_ok=True)
    return ReviewApp(tmp_path / "clients", schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, store_root)


def test_the_paralegal_sees_the_message_in_my_work_and_the_reply_goes_out_by_the_clients_channels(portal, monkeypatch):
    client, store, tmp = portal
    monkeypatch.setattr("classify.translate.translate_text", lambda text, a, b="en": f"[{b}] {text}")
    monkeypatch.setenv("PORTAL_BASE_URL", "https://portal.example")
    app = _review_app(tmp, tmp)
    _sign_in(client, store)
    client.post("/api/message", json={"text": "Preciso mudar uma resposta. Meu endereço mudou."}, headers=H)
    work = app.work("paralegal")
    assert [(m["client"], m["name"], m["text"]) for m in work["messages"]] == [("pilot-1", "Ana Teste", "Preciso mudar uma resposta. Meu endereço mudou.")]
    assert work["counts"]["messages"] == 1 and app.work("attorney")["messages"] == []  # the paralegal's list, not the attorney's
    thread = app.client_messages("pilot-1")
    assert thread["language"] == "pt" and thread["messages"][0]["status"] == "new"
    draft = app.message_preview("pilot-1", {"text": "Thanks. Send us the new address here."})
    assert draft["text"] == "[pt] Thanks. Send us the new address here." and draft["machine"] and draft["language_name"] == "Portuguese"
    with pytest.raises(ValueError):
        app.message_reply("pilot-1", {"text": "Thanks.", "reviewer": ""})  # who answered is recorded
    out = app.message_reply("pilot-1", {"text": "Thanks. Send us the new address here.", "reviewer": "Paulo Paralegal"})
    assert out["delivery"]["status"] == "queued"  # no mail server here: it waited in the outbox, never "sent"
    mail = json.loads((tmp / "outbox.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert mail["channel"] == "email" and "/l/" in mail["body"] and "address" not in mail["body"].lower() and "endereço" not in mail["body"]
    assert "Thanks" not in mail["body"] and "Thanks" not in mail["subject"]
    app.roster.touch("pilot-1")  # the route that answers touches the case before it replies; this test calls the method
    assert app.work("paralegal")["messages"] == []  # answered: off the list
    answered = app.client_messages("pilot-1")["messages"]
    assert [m["from"] for m in answered] == ["client", "office"] and answered[0]["status"] == "handled" and answered[0]["handled_by"] == "Paulo Paralegal"
    assert answered[1]["translation"]["text"] == "[pt] Thanks. Send us the new address here." and answered[1]["by"] == "Paulo Paralegal"
    # and the client reads it, in Portuguese, with the office's words next to it
    assert client.get("/api/me").json()["messages"][1]["original"] == "Thanks. Send us the new address here."
    # a message marked handled without an answer leaves the list too
    client.post("/api/message", json={"text": "Obrigada."}, headers=H)
    app.roster.touch("pilot-1")  # (as installed the portal lists the client in its queue)
    assert len(app.work("paralegal")["messages"]) == 1
    app.message_done("pilot-1", {"reviewer": "Paulo Paralegal"})
    app.roster.touch("pilot-1")  # (the route touches before it answers)
    assert app.work("paralegal")["messages"] == []


def test_a_message_and_the_answer_land_on_the_case_timeline_with_the_time(portal):
    client, store, tmp = portal
    app = _review_app(tmp, tmp)
    assert app._message_events("pilot-1") == []
    store.add_message("pilot-1", "client", "Oi")
    messages.reply(store, "pilot-1", "Hello", "Paulo", translate=lambda t, lg: {"language": lg, "text": None, "machine": False, "needs_translator": True})
    events = app._message_events("pilot-1")
    assert [e["kind"] for e in events] == ["message", "message"] and re.search(r"^Message from the client at \d{1,2}:\d{2} [AP]M$", events[0]["what"])
    assert re.search(r"^Answer to the client from Paulo at \d{1,2}:\d{2} [AP]M$", events[1]["what"]) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", events[0]["date"])
    with pytest.raises(ValueError):
        app.message_reply("not-a-portal-client", {"text": "x", "reviewer": "Paulo"})
    assert app.client_messages("not-a-portal-client") == {"messages": [], "retakes": [], "language": None, "language_name": None}


def test_a_retake_waits_in_my_work_and_on_the_case_with_the_day_it_was_asked(portal):
    client, store, tmp = portal
    tasks = _retakes([_record("passport-01.pdf", "blurry")], [_upload("passport")])
    stamp_retakes(store, "pilot-1", tasks)
    store.save_tasks("pilot-1", tasks)
    app = _review_app(tmp, tmp)
    (waiting,) = app.work("paralegal")["retakes"]
    assert (waiting["client"], waiting["document"], waiting["why"]) == ("pilot-1", "passport", "blurry") and waiting["asked_at"]
    (on_case,) = app.client_messages("pilot-1")["retakes"]
    assert on_case["document"] == "passport" and on_case["asked_at"] == waiting["asked_at"]
    store.save_tasks("pilot-1", [])  # the client sent a new photo
    app.roster.touch("pilot-1")  # (the portal lists the client in its queue, which the lists read)
    assert app.work("paralegal")["retakes"] == [] and app.client_messages("pilot-1")["retakes"] == []


# --- the page that says what to bring, where and when ---------------------------------------------------------


def _j(notices=(), hearings=(), today="2026-10-02"):
    ids = journey.settings()["stages"]["sij"]
    return {"stage": "i485_ready", "stage_index": ids.index("i485_ready"), "today": today, "stages": [{"id": x} for x in ids], "filings": [],
            "notices": list(notices), "hearings": list(hearings)}


def test_a_biometrics_appointment_gets_a_page_with_when_where_and_what_to_bring_in_every_language():
    notice = {"kind": "biometrics", "appointment": "2026-11-12 09:30 AM", "date": "2026-10-01", "form": "I-485",
              "where": "USCIS Application Support Center, 1 Example Street, Springfield, MA 01103"}
    for lang in LANGS:
        view = journey.client_view(_j([notice]), lang)
        assert len(view["appointments"]) == len(view["prepare"]) == 1
        page, labels = view["prepare"][0], view["prepare_labels"]
        assert (page["kind"], page["date"], page["time"]) == ("biometrics", "2026-11-12", "09:30 AM")
        assert page["where"] == "USCIS Application Support Center, 1 Example Street, Springfield, MA 01103"
        assert page["where_note"] == labels["check_place"] and page["title"] == view["appointments"][0]["what"]
        assert len(page["bring"]) == 4 and all(page["bring"]) and labels["print"] and labels["open"]
    en = journey.client_view(_j([notice]), "en")["prepare"][0]
    assert en["bring"][0] == "The USCIS letter with the date and time." and "photo ID" in en["bring"][1]
    # what the appointment card said before is untouched
    assert journey.client_view(_j([notice]), "es")["appointments"] == [{"date": "2026-11-12", "time": "09:30 AM", "what": "Toma de huellas (biometría)",
                                                                       "bring": "Lleve la carta de USCIS y una identificación con foto."}]


def test_a_place_the_notice_did_not_give_is_never_guessed():
    view = journey.client_view(_j([{"kind": "interview", "appointment": "2026-11-12 10:00 AM", "date": "2026-10-01", "form": "I-485"}]), "pt")
    page = view["prepare"][0]
    assert page["where"] is None and page["where_note"] == view["prepare_labels"]["place_in_letter"] == "O endereço está na sua carta. Confira lá."
    assert len(page["bring"]) == 5 and "O escritório vai preparar você antes da entrevista." in page["bring"]
    undated = journey.client_view(_j([{"kind": "biometrics", "appointment": None, "date": "2026-10-01", "form": "I-485"}]), "en")
    assert undated["prepare"] == [] and undated["appointments"] == []  # an appointment with no date has no page
    assert journey.client_view(_j([{"kind": "biometrics", "appointment": "2026-09-01 10:00 AM", "date": "2026-08-01", "form": "I-485"}]), "en")["prepare"] == []  # past


def test_a_hearing_page_names_the_court_the_office_entered_and_the_pages_come_in_date_order():
    hearing = {"date": "2026-10-20", "time": "9:00 AM", "kind": "Master calendar", "court": "Boston Immigration Court", "result": None}
    notice = {"kind": "biometrics", "appointment": "2026-10-10 08:00 AM", "date": "2026-10-01", "form": "I-485"}
    view = journey.client_view(_j([notice], [hearing]), "en")
    assert [a["date"] for a in view["appointments"]] == ["2026-10-10", "2026-10-20"] == [p["date"] for p in view["prepare"]]
    court = view["prepare"][1]
    assert court["kind"] == "hearing" and court["where"] == "Boston Immigration Court" and court["where_note"] == view["prepare_labels"]["check_court"]
    assert "Arrive early: missing a hearing can lead to a removal order." in court["bring"]
    done = journey.client_view(_j([], [hearing | {"result": {"outcome": "Reset"}}]), "en")
    assert done["prepare"] == []


def test_the_place_is_read_from_the_notice_only_when_it_ends_in_a_state_and_zip():
    text = ("Receipt Number Case Type\nIOE9000000001 I485 - APPLICATION TO REGISTER PERMANENT RESIDENCE\nReceived Date Priority Date Applicant A099 000 001\n"
            "09/01/2026 09/01/2026 EXEMPLO, MARIA\nNotice Date Page Beneficiary\n09/05/2026 1 of 1\nNotice Type: Biometrics Appointment Notice\n"
            "Date and Time of Appointment: 11/12/2026 09:30 AM\nPlace:\nUSCIS Application Support Center\n1 Example Street\nSpringfield, MA 01103\n\nBring this notice.")
    n = parse(text)
    assert n.kind == "biometrics" and n.appointment == "2026-11-12 09:30 AM"
    assert n.where == "USCIS Application Support Center, 1 Example Street, Springfield, MA 01103"
    assert "folder.notice.IOE9000000001.biometrics_20260905.where" in {f.fact_key for f in extract(text)}
    assert appointment_place("Location: 9 Sample Road, Boston, MA 02110") == "9 Sample Road, Boston, MA 02110"
    assert appointment_place("Place of Birth: Campinas\nBrazil") is None  # a label alone is not an address
    assert appointment_place("Placement of your case\n12 Example Road\nSpringfield, MA 01103") is None  # "Place" must be a whole word
    assert appointment_place("Place of Birth: Brazil\n12 Example Road\nSpringfield, MA 01103") is None  # a birthplace, not an appointment
    assert appointment_place("Location:\nNotice date 09/05/2026 ID 12345") is None  # two capitals and digits are not a state and ZIP
    assert appointment_place("Location:\nExample Immigration Office\nSpringfield, MA 01103") is None  # no street line (a number, then words)
    assert appointment_place("Location: 9 Sample Road, Boston, XX 02110") is None  # XX is not a USPS state code
    assert appointment_place("Location:\nline one\n9 Sample Road\nline three\nline four\nBoston, MA 02110") is None  # four lines at most
    assert appointment_place("Place:\nUSCIS ASC\n9 Sample Road\nBoston, MA 021101234") == "USCIS ASC, 9 Sample Road, Boston, MA 021101234"  # a 9-digit ZIP
    assert appointment_place("Location:\nthe office downstairs\n\nSpringfield, MA 01103") is None  # a blank line ends the block
    assert parse(text.replace("Biometrics Appointment", "Approval").replace("Place:", "Nothing:")).where is None  # only appointment notices
    assert parse("Notice Type: Biometrics Appointment Notice\nDate and Time of Appointment: 11/12/2026 09:30 AM\nBring this notice.").where is None


def test_the_place_travels_from_the_notice_to_the_journey():
    g = FactGraph("c")
    g.add_source("folder.uscis_case.IOE9000000001.biometrics_20260905", "bio.pdf", "uscis_notice", "IOE9000000001", "I-485 BIOMETRICS APPOINTMENT, 2026-09-05", 0.9)
    for name, value in (("date", "2026-09-05"), ("appointment", "2026-11-12 09:30 AM"), ("where", "1 Example Street, Springfield, MA 01103")):
        g.add_source(f"folder.notice.IOE9000000001.biometrics_20260905.{name}", "bio.pdf", "uscis_notice", value, value, 0.85)
    (notice,) = journey.notices(g)
    assert notice["where"] == "1 Example Street, Springfield, MA 01103" and notice["appointment"] == "2026-11-12 09:30 AM"


def test_the_appointment_page_is_in_the_portal_and_prints_alone():
    page = (REPO / "src" / "portal" / "static" / "portal.html").read_text(encoding="utf-8")
    assert "@media print" in page and "openAppointment" in page and "window.print()" in page
    assert re.search(r"@media print \{.*?\.top, \.wrap, \.actions", page, re.S)  # the screen's own chrome stays off the paper


def test_the_new_screen_words_are_in_every_language():
    page = (REPO / "src" / "portal" / "static" / "portal.html").read_text(encoding="utf-8")
    block = page[page.index("const UI_MSG = {"):page.index("for (const lg of Object.keys(UI_MSG))")]
    keys = {lg: set(re.findall(r"\b([a-z_]+): \"", part)) for lg, part in zip(LANGS, re.split(r"\n  (?:pt|es|en|ht): \{", block)[1:])}
    assert set(keys) == set(LANGS) and len(keys["en"]) >= 12
    for lang in LANGS:
        assert keys[lang] == keys["en"], (lang, keys["en"] ^ keys[lang])
    pages = json.loads((schema_path.path("register", "journey")).read_text(encoding="utf-8"))["appointment_pages"]
    for key in ("biometrics", "interview", "hearing"):
        assert all(pages[key].get(lang) for lang in LANGS) and len({len(pages[key][lang]) for lang in LANGS}) == 1, key
    for key, words in pages["labels"].items():
        assert all(words.get(lang) for lang in LANGS), key
    assert "DRAFT" in pages["_note"] and "MACHINE DRAFT" in pages["_note"]


# --- the firm-run demo client ----------------------------------------------------------------------------------


def test_the_demo_client_builds_with_a_half_done_questionnaire_a_blurry_photo_a_thread_and_an_appointment(tmp_path):
    store = PortalStore(tmp_path / "portal")
    built = demo.showcase(store, tmp_path / "clients", process=False, today=date(2026, 10, 2))
    assert built == {"client": "demo-bia", "appointment": "2026-11-12", "tasks": 1, "messages": 3}
    profile = store.profile("demo-bia")
    assert profile["language"] == "pt" and profile["status"] == "started" and "Exemplo" in profile["name"]
    answers = store.answers("demo-bia")
    from portal.bank import bank_for, missing_required

    missing = missing_required(bank_for(profile), answers)
    assert len(answers) >= 15 and "dob" in answers and "ssn" in missing and len(missing) > 30  # half done: what she gave, and plenty to go
    (task,) = store.tasks("demo-bia")
    assert task["kind"] == "retake" and task["quality"] == "blurry" and task["doc_id"] == "passport" and "fora de foco" in task["text"] and task["asked_at"]
    assert [(m["from"], m.get("status")) for m in store.messages("demo-bia")] == [("client", "handled"), ("office", None), ("client", "new")]
    view = store.journey("demo-bia")["pt"]
    assert view["appointments"][0]["date"] == "2026-11-12" and view["prepare"][0]["where"] and view["prepare_labels"]["open"] == "O que levar e onde"
    assert (tmp_path / "clients" / "demo-bia" / "documents.json").exists()  # the record the retake was built from
    # a client who signs in sees all of it
    app = create_app(root=tmp_path / "portal", base_url="https://portal.example", secure_cookies=False, notifier=Notifier(tmp_path / "o.jsonl", env={}))
    client = TestClient(app)
    assert client.get(f"/l/{store.new_link_token('demo-bia')}", follow_redirects=False).status_code == 303
    me = client.get("/api/me").json()
    assert [t["kind"] for t in me["tasks"]].count("retake") == 1 and len(me["messages"]) == 3 and me["messages"][1]["unseen"] is True
    assert me["journey"]["prepare"][0]["bring"][0] == "A carta do USCIS com a data e a hora."
    # nothing in it is real: every document and name says so
    assert "Exemplo" in demo.SHOWCASE_PROFILE["name"] and demo.SHOWCASE_PROFILE["email"].endswith("@example.com")
    rebuilt = demo.showcase(store, tmp_path / "clients", process=False, today=date(2026, 10, 2))  # rebuilding starts clean, never doubles
    assert rebuilt["messages"] == 3 and len(store.uploads("demo-bia")) == 1


def test_the_next_months_appointment_is_never_on_a_weekend():
    assert demo.next_month_weekday(date(2026, 10, 2)) == date(2026, 11, 12)
    assert demo.next_month_weekday(date(2026, 12, 20)) == date(2027, 1, 12)
    for month in range(1, 13):
        assert demo.next_month_weekday(date(2026, month, 5)).weekday() < 5


def test_the_demo_tool_builds_in_a_folder_and_prints_a_link_that_works_once(tmp_path, capsys, monkeypatch):
    import sys

    for name in ("PORTAL_OUTBOX_FULL_LINKS", "PORTAL_DATA", "PORTAL_BASE_URL"):  # the tool sets these for itself: put them back afterwards
        monkeypatch.setenv(name, "unset-by-the-test")

    sys.path.insert(0, str(REPO / "tools"))
    import demo_portal

    assert demo_portal.main(["--folder", str(tmp_path / "d"), "--quick"]) == 0
    out = capsys.readouterr().out
    link = re.search(r"sign-in link \(works once\): (http://127\.0\.0\.1:8600/l/\S+)", out).group(1)
    store = PortalStore(tmp_path / "d" / "portal")
    assert store.redeem_link(link.rsplit("/", 1)[1]) and not store.redeem_link(link.rsplit("/", 1)[1])
    assert "demo-bia" in out and "1 thing(s) for her to do" in out


# --- the review fixes ----------------------------------------------------------------------------------------


def test_a_document_without_a_name_is_never_shown_by_its_id():
    from portal.engine import DOC_WORD, _doc_name

    up = {"doc_id": "immigration_court"}
    word = {lg: _doc_name("not_a_type", None, up, lg) for lg in LANGS}
    assert "immigration_court" not in " ".join(word.values())
    assert all(word.values())  # the bank's own label for the document, or the plain word
    assert _doc_name("not_a_type", None, {"doc_id": "zzz_unknown"}, "ht") == "dokiman" == DOC_WORD["ht"]
    assert _doc_name("not_a_type", None, {"doc_id": "zzz_unknown"}, "en") == "document"


def test_the_firms_name_comes_from_settings_with_the_built_in_name_as_the_fallback(tmp_path, monkeypatch):
    import settings
    from portal.notify import FIRM, firm_name, render

    monkeypatch.setattr(settings, "PATH", tmp_path / "none.json")
    assert firm_name() == FIRM == "Case Review" and render("invite", "en", "L")["subject"].startswith("Case Review:")
    (tmp_path / "settings.json").write_text(json.dumps({"firm": {"values": {"firm.business_name": "EXEMPLO LAW LLP"}}}), encoding="utf-8")
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    assert firm_name() == "EXEMPLO LAW LLP"
    for kind in ("invite", "reminder", "request", "case_update", "received", "office_reply"):
        for lang in LANGS:
            out = render(kind, lang, "https://portal.example/l/abc")
            assert out["subject"].startswith("EXEMPLO LAW LLP:") and "EXEMPLO LAW LLP" in out["body"] and "Georges" not in out["subject"] + out["body"], (kind, lang)
    (tmp_path / "settings.json").write_text(json.dumps({"firm": {"values": {"firm.business_name": "   "}}}), encoding="utf-8")
    assert firm_name() == FIRM  # a blank name is no name


def test_the_portal_page_carries_the_firms_name(portal, tmp_path, monkeypatch):
    import settings

    client, _, _ = portal
    assert "<title>Case Review</title>" in client.get("/").text and "%%" not in client.get("/").text
    (tmp_path / "s.json").write_text(json.dumps({"firm": {"values": {"firm.business_name": "Exemplo & Lima"}}}), encoding="utf-8")
    monkeypatch.setattr(settings, "PATH", tmp_path / "s.json")
    page = client.get("/").text
    assert "<title>Exemplo &amp; Lima</title>" in page and '<span class="mark">EL</span>' in page and "Georges" not in page.split("<script")[0]


def test_a_message_that_is_refused_says_why_as_a_code_the_portal_words_in_the_clients_language(portal):
    client, store, _ = portal
    _sign_in(client, store)
    assert client.post("/api/message", json={"text": ""}, headers=H).json()["detail"] == "empty_message"
    assert client.post("/api/message", json={"text": "x" * 2001}, headers=H).json()["detail"] == "message_too_long"
    for body in ([], ["x"], "text", 5, None):  # a JSON body that is not an object
        assert client.post("/api/message", json=body, headers=H).status_code == 400
    assert client.post("/api/message", content=b"{not json", headers=H | {"Content-Type": "application/json"}).status_code == 400
    page = (REPO / "src" / "portal" / "static" / "portal.html").read_text(encoding="utf-8")
    for code in ("empty_message", "message_too_long", "too_many_messages"):
        assert page.count(f"err_{code}:") == 4  # one wording per language
    for n in range(20):
        client.post("/api/message", json={"text": f"n {n}"}, headers=H)
    assert client.post("/api/message", json={"text": "one more"}, headers=H).json()["detail"] == "too_many_messages"
