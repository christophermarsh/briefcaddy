"""The office's questions in the client's language (docs/design_plan.md Part 7, item 1; src/portal/questions.py):
a typed question (words, a date, Yes or No, a choice), drafted into the client's portal language by the offline
translator -- replaced here by a fake, so Argos isn't needed -- seen and corrected by the paralegal, answered in the
portal with the matching input, and read back by staff in words. Made-up clients only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import portal.questions as questions
from portal.app import create_app
from portal.notify import Notifier
from review.server import ReviewApp
import schema_path

REPO = Path(__file__).resolve().parent.parent
H = {"X-Portal": "1"}
FAKE = {("What is your father's date of birth?", "pt"): "Qual é a data de nascimento do seu pai?",
        ("Did your father ever live in the United States?", "pt"): "Seu pai já morou nos Estados Unidos?",
        ("Where did your parents marry?", "pt"): "Onde seus pais se casaram?",
        ("In a church", "pt"): "Em uma igreja", ("At a registry office", "pt"): "Em um cartório",
        ("What is your father's date of birth?", "es"): "¿Cuál es la fecha de nacimiento de su padre?"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A portal with three made-up clients (Portuguese, Haitian Creole, English), the review app beside it, and a fake
    translator that knows a few Portuguese and Spanish sentences and no Creole (Argos has no Creole model)."""
    calls = []

    def fake(text, lang):
        calls.append((text, lang))
        return FAKE.get((text.strip(), lang), "")

    monkeypatch.setattr(questions, "machine_translate", fake)
    app = create_app(root=tmp_path / "portal", base_url="https://portal.example", secure_cookies=False,
                     notifier=Notifier(tmp_path / "portal" / "outbox.jsonl", env={}))
    store = app.state.store
    store.add_client("pilot-ana", "Ana Clara Exemplo Souza", email="ana@example.com", language="pt")
    store.add_client("pilot-rose", "Rose Egzanp Jean", email="rose@example.com", language="ht")
    store.add_client("pilot-maria", "Maria Exemplo", email="maria@example.com", language="en")
    (tmp_path / "clients").mkdir()
    review = ReviewApp(tmp_path / "clients", schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None,
                       tmp_path / "portal")
    return TestClient(app), store, review, calls


def _portal_tasks(client, store, cid):
    r = client.get(f"/l/{store.new_link_token(cid)}", follow_redirects=False)
    assert r.status_code == 303
    return [t for t in client.get("/api/me").json()["tasks"] if t["kind"] == "request"]


def _ask(review, cid, **body):
    out = review.ask_client(cid, {"reviewer": "Jane", "queue": True} | body)
    review.ask_send(cid, {"reviewer": "Jane"})
    return out["request"]


def test_a_date_question_reaches_a_portuguese_client_in_portuguese_and_comes_back_as_a_date(world):
    client, store, review, _ = world
    preview = review.ask_preview("pilot-ana", {"text": "What is your father's date of birth?", "type": "date"})
    assert preview == {"language": "pt", "language_name": "Portuguese", "text_client": "Qual é a data de nascimento do seu pai?",
                       "options_client": [], "needs_translator": False}
    request = _ask(review, "pilot-ana", text="What is your father's date of birth?", type="date", text_client=preview["text_client"],
                   facts=["father.dob"])
    stored = store.requests("pilot-ana")[0]
    assert {k: stored[k] for k in ("type", "text", "text_en", "text_client", "language", "machine_translated", "needs_translator")} == {
        "type": "date", "text": "What is your father's date of birth?", "text_en": "What is your father's date of birth?",
        "text_client": "Qual é a data de nascimento do seu pai?", "language": "pt", "machine_translated": True, "needs_translator": False}

    [task] = _portal_tasks(client, store, "pilot-ana")
    assert task["text"] == "Qual é a data de nascimento do seu pai?" and task["type"] == "date" and not task["in_english"]
    bad = client.post("/api/request-reply", json={"request": request["id"], "reply": "05/05/1970"}, headers=H)
    assert bad.status_code == 400 and bad.json()["detail"] == "invalid_date"  # the portal's date picker sends YYYY-MM-DD
    assert client.post("/api/request-reply", json={"request": request["id"], "reply": ""}, headers=H).status_code == 400
    after = client.post("/api/request-reply", json={"request": request["id"], "reply": "1970-05-04"}, headers=H).json()
    assert not [t for t in after["tasks"] if t["kind"] == "request"]
    assert store.requests("pilot-ana")[0]["reply"] == "1970-05-04"  # stored as an ISO date

    seen = review.client_requests("pilot-ana")[0]
    assert seen["reply_words"] == "05/04/1970"  # staff read MM/DD/YYYY
    assert seen["asked_line"] == "Asked in Portuguese: Qual é a data de nascimento do seu pai? (machine translation)"
    page = review.answers_page("pilot-ana")
    assert "Questions from the office" in page and "What is your father&#x27;s date of birth?" in page
    assert "Asked in Portuguese: Qual é a data de nascimento do seu pai?" in page and "05/04/1970" in page
    assert '<span id="father.dob"></span>' in page  # "Open the answers" from the card lands on it
    out = {"cards": [{"facts": [{"key": "father.dob"}]}, {"facts": [{"key": "applicant.dob"}]}]}
    review._note_client_answered("pilot-ana", out)
    assert out["cards"][0]["asked"] == [{"question": "What is your father's date of birth?", "answer": "05/04/1970", "by": "Jane",
                                         "answered_at": store.requests("pilot-ana")[0]["answered_at"],
                                         "asked_line": "Asked in Portuguese: Qual é a data de nascimento do seu pai? (machine translation)"}]
    assert "asked" not in out["cards"][1]


def test_the_paralegal_corrects_the_translation(world):
    _, store, review, _ = world
    _ask(review, "pilot-ana", text="What is your father's date of birth?", type="date", text_client="Qual é a data de nascimento do seu pai? ")
    assert store.requests("pilot-ana")[0]["machine_translated"] is True  # the same words, a stray space
    _ask(review, "pilot-ana", text="What is your father's date of birth?", type="date", text_client="Em que dia o seu pai nasceu?")
    corrected = store.requests("pilot-ana")[1]
    assert corrected["text_client"] == "Em que dia o seu pai nasceu?" and corrected["machine_translated"] is False
    assert review.client_requests("pilot-ana")[1]["asked_line"] == "Asked in Portuguese: Em que dia o seu pai nasceu?"
    # no translation in the body (an older screen, or the paralegal typed on): the server drafts it itself
    _ask(review, "pilot-ana", text="What is your father's date of birth?", type="date")
    assert store.requests("pilot-ana")[2]["text_client"] == "Qual é a data de nascimento do seu pai?"


def test_a_yes_no_question_uses_the_banks_own_yes_and_no(world):
    client, store, review, _ = world
    request = _ask(review, "pilot-ana", text="Did your father ever live in the United States?", type="yes_no")
    [task] = _portal_tasks(client, store, "pilot-ana")
    assert task["text"] == "Seu pai já morou nos Estados Unidos?"
    assert task["options"] == [{"value": "Yes", "label": "Sim"}, {"value": "No", "label": "Não"}]
    assert client.post("/api/request-reply", json={"request": request["id"], "reply": "Talvez"}, headers=H).json()["detail"] == "invalid_choice"
    client.post("/api/request-reply", json={"request": request["id"], "reply": "No"}, headers=H)
    assert review.client_requests("pilot-ana")[0]["reply_words"] == "No"
    assert "Did your father ever live in the United States?" in review.answers_page("pilot-ana")


def test_a_choice_question_in_portuguese(world):
    client, store, review, _ = world
    with pytest.raises(ValueError, match="at least two choices"):
        review.ask_client("pilot-ana", {"reviewer": "Jane", "text": "Where did your parents marry?", "type": "choice", "options": "In a church"})
    with pytest.raises(ValueError, match="what kind of answer"):
        review.ask_client("pilot-ana", {"reviewer": "Jane", "text": "Where did your parents marry?", "type": "essay"})
    preview = review.ask_preview("pilot-ana", {"text": "Where did your parents marry?", "type": "choice", "options": ["In a church", "At a registry office"]})
    assert preview["options_client"] == ["Em uma igreja", "Em um cartório"]
    request = _ask(review, "pilot-ana", text="Where did your parents marry?", type="choice", options="In a church\nAt a registry office\n",
                   text_client=preview["text_client"], options_client=preview["options_client"])
    assert request["options"] == [{"value": "In a church", "en": "In a church", "client": "Em uma igreja"},
                                  {"value": "At a registry office", "en": "At a registry office", "client": "Em um cartório"}]
    [task] = _portal_tasks(client, store, "pilot-ana")
    assert task["text"] == "Onde seus pais se casaram?"
    assert [o["label"] for o in task["options"]] == ["Em uma igreja", "Em um cartório"]
    assert client.post("/api/request-reply", json={"request": request["id"], "reply": "On a beach"}, headers=H).status_code == 400
    client.post("/api/request-reply", json={"request": request["id"], "reply": "At a registry office"}, headers=H)
    assert review.client_requests("pilot-ana")[0]["reply_words"] == "At a registry office"  # staff read the English choice


def test_creole_has_no_translator_so_the_question_goes_in_english_and_says_so(world):
    client, store, review, calls = world
    preview = review.ask_preview("pilot-rose", {"text": "What is your father's date of birth?", "type": "date"})
    assert preview["needs_translator"] and preview["text_client"] == "" and preview["language_name"] == "Haitian Creole"
    _ask(review, "pilot-rose", text="What is your father's date of birth?", type="date", text_client="")
    stored = store.requests("pilot-rose")[0]
    assert stored["text_client"] == "" and stored["needs_translator"] and not stored["machine_translated"]
    [task] = _portal_tasks(client, store, "pilot-rose")
    assert task["text"] == "What is your father's date of birth?" and task["in_english"] and task["type"] == "date"
    line = review.client_requests("pilot-rose")[0]["asked_line"]
    assert line == "Asked in English (needs a translator for Haitian Creole): the client was told, in Haitian Creole, that the office will follow up."
    assert "needs a translator for Haitian Creole" in review.answers_page("pilot-rose")
    # the portal's sentence that the office will follow up exists in every language, Creole included
    html = (REPO / "src" / "portal" / "static" / "portal.html").read_text(encoding="utf-8")
    assert html.count("asked_in_english: ") == 4 and "biwo a ap kontakte ou" in html


def test_a_client_who_switches_language_sees_the_english_and_the_follow_up_line(world):
    client, store, review, _ = world
    _ask(review, "pilot-ana", text="What is your father's date of birth?", type="date")
    _portal_tasks(client, store, "pilot-ana")
    me = client.post("/api/language", json={"language": "es"}, headers=H).json()
    [task] = [t for t in me["tasks"] if t["kind"] == "request"]
    assert task["text"] == "What is your father's date of birth?" and task["in_english"]  # translated for Portuguese only
    me = client.post("/api/language", json={"language": "pt"}, headers=H).json()
    assert [t["text"] for t in me["tasks"] if t["kind"] == "request"] == ["Qual é a data de nascimento do seu pai?"]


def test_an_english_reader_needs_no_translation(world):
    client, store, review, calls = world
    _ask(review, "pilot-maria", text="What is your father's date of birth?", type="date")
    stored = store.requests("pilot-maria")[0]
    assert stored["text_client"] == "What is your father's date of birth?" and not stored["machine_translated"] and not stored["needs_translator"]
    assert not calls  # the translator is never called for an English reader
    [task] = _portal_tasks(client, store, "pilot-maria")
    assert not task["in_english"] and review.client_requests("pilot-maria")[0]["asked_line"] == ""


def test_a_document_request_and_an_old_request_still_work(world):
    """A document request is answered by the upload (its type stays words); a request stored before questions were typed
    (no type, no language) shows as before, in English, with the line that the office will follow up."""
    client, store, review, _ = world
    request = _ask(review, "pilot-ana", text="Please send your passport.", type="date", doc_id="passport")
    assert request["type"] == "text"
    old = store.add_request("pilot-ana", "Please tell us your mother's maiden name.", None, "Jane")
    [doc, plain] = _portal_tasks(client, store, "pilot-ana")
    assert doc["doc_id"] == "passport" and plain["type"] == "text" and plain["in_english"]
    client.post("/api/request-reply", json={"request": old["id"], "reply": "  EXEMPLO  "}, headers=H)
    assert store.requests("pilot-ana")[1]["reply"] == "EXEMPLO" and review.client_requests("pilot-ana")[1]["reply_words"] == "EXEMPLO"
    message = json.loads((Path(store.root) / "outbox.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert "/l/" in message["body"] and "passport" not in message["body"].lower()  # still only a link, no case details


def test_the_real_translator_returns_nothing_for_creole():
    """The one real call (no fake): Argos has no Haitian Creole model, so the draft is always empty."""
    assert questions.machine_translate("What is your father's date of birth?", "ht") == ""
    assert questions.machine_translate("", "pt") == ""


def test_an_answer_over_the_limit_is_refused_not_cut(world):
    """Words over 2,000 characters come back with a 400 the portal shows in the client's language; nothing is stored cut short."""
    client, store, review, _ = world
    request = _ask(review, "pilot-ana", text="Please tell us about your father's travels.")
    _portal_tasks(client, store, "pilot-ana")
    long = client.post("/api/request-reply", json={"request": request["id"], "reply": "x" * (questions.REPLY_MAX + 1)}, headers=H)
    assert long.status_code == 400 and long.json()["detail"] == "too_long"
    assert store.requests("pilot-ana")[0]["status"] == "open" and "reply" not in store.requests("pilot-ana")[0]
    client.post("/api/request-reply", json={"request": request["id"], "reply": "y" * questions.REPLY_MAX}, headers=H)
    assert store.requests("pilot-ana")[0]["reply"] == "y" * questions.REPLY_MAX  # exactly the limit is fine
    html = (REPO / "src" / "portal" / "static" / "portal.html").read_text(encoding="utf-8")
    assert html.count(", too_long: ") == 4 and "const REPLY_MAX = 2000;" in html  # the message in every language, the count's limit


def test_a_yes_no_question_in_english_has_english_buttons(world):
    """Creole has no translation: the question goes in English, and so do its Yes and No (never a mix)."""
    client, store, review, _ = world
    _ask(review, "pilot-rose", text="Did your father ever live in the United States?", type="yes_no")
    [task] = _portal_tasks(client, store, "pilot-rose")
    assert task["in_english"] and [o["label"] for o in task["options"]] == ["Yes", "No"]
