"""The portal's languages (schemas/questions/intake.json "languages": Portuguese,
Spanish, English, Haitian Creole): every client-facing text names each of them,
a text that doesn't is shown in English (never blank, never a key), and the
firm's client list may name a language by its code or its name. Made-up clients only.

The Haitian Creole is a MACHINE DRAFT for the firm's certified translator
(question_bank.json translation_status): these tests check it is there and
whole, not that it is right."""

import copy
import json
import re
from pathlib import Path

import pytest
from communication_fixture import ATTORNEY, STAFF, approve_client, installation
from fastapi.testclient import TestClient

import schema_path
from portal.bank import (
    language_code,
    language_names,
    languages,
    load_bank,
    localized,
    localized_faq,
    localized_scan_guide,
)
from portal.notify import STOP, TEMPLATES, Notifier

REPO = Path(__file__).resolve().parent.parent

LANGS = languages()
H = {"X-Portal": "1"}


def _client_texts(node, path=""):
    """(where, {lang: text}) for every client-facing text object: a dict with "en" and another language."""
    if isinstance(node, dict):
        if "en" in node and ("pt" in node or "es" in node):
            yield path, node
            return
        for key, value in node.items():
            if not key.startswith("_"):
                yield from _client_texts(value, f"{path}.{key}")
    elif isinstance(node, list):
        for n, value in enumerate(node):
            yield from _client_texts(value, f"{path}[{n}]")


def _everything_a_client_reads():
    for name, file in (("question_bank.json", schema_path.path("question", "intake")), ("question_bank_n400.json", schema_path.path("question", "n400")),
                       ("question_help.json", schema_path.path("question_help", "portal")), ("eoir26a.json", schema_path.path("form_text", "eoir26a"))):
        yield from ((name + where, text) for where, text in _client_texts(json.loads(file.read_text(encoding="utf-8"))))
    journey = json.loads((schema_path.path("register", "journey")).read_text(encoding="utf-8"))
    for part in ("client", "client_appointments", "client_events", "client_labels", "appointment_pages"):  # the "your case" page and the what-to-bring pages
        yield from ((f"journey.json.{part}" + where, text) for where, text in _client_texts({part: journey[part]}))
    for name, file, parts in (("client_case.json", schema_path.path("register", "client_case"), ("labels", "lines")), ("client_next_steps.json", schema_path.path("register", "client_next_steps"), ("labels", "stages"))):  # USCIS's words, what happens next, the sheets, the feedback card
        document = json.loads(file.read_text(encoding="utf-8"))
        for part in parts:
            yield from ((f"{name}.{part}" + where, text) for where, text in _client_texts({part: document[part]}))
    from portal.engine import DOC_NAMES, MESSAGES, RETAKE_REASONS
    from portal.notify import APPOINTMENT_WORDS

    yield from ((f"notify.APPOINTMENT_WORDS.{k}.{kind}", words) for k, v in APPOINTMENT_WORDS.items() for kind, words in (v.items() if "en" not in v else [("", v)]))
    yield from ((f"engine.RETAKE_REASONS.{k}", v) for k, v in RETAKE_REASONS.items())
    yield from ((f"engine.DOC_NAMES.{k}", v) for k, v in DOC_NAMES.items())
    yield from ((f"engine.MESSAGES.{k}", v) for k, v in MESSAGES.items())
    yield from ((f"notify.TEMPLATES.{k}", {lg: m["subject"] + " / " + m["body"] for lg, m in v.items()}) for k, v in TEMPLATES.items())
    yield "notify.STOP", STOP


def test_haitian_creole_is_one_of_the_portals_languages():
    assert LANGS == ("pt", "es", "en", "ht")
    assert language_names()["ht"] == "Haitian Creole" and set(language_names()) == set(LANGS)
    status = load_bank()["translation_status"]["ht"]
    assert "MACHINE DRAFT" in status and "certified" in status  # never presented as a certified translation


def test_every_client_facing_text_is_in_every_language():
    """A new question (or message, or "your case" text) without its Haitian Creole fails here."""
    seen, missing = 0, []
    for where, text in _everything_a_client_reads():
        seen += 1
        for lang in LANGS:
            value = text.get(lang)
            parts = value if isinstance(value, list) else [value]
            english = text["en"] if isinstance(text["en"], list) else [text["en"]]
            if not value or len(parts) != len(english) or not all(isinstance(p, str) and p.strip() for p in parts):
                missing.append(f"{where}: {lang}")
    assert not missing, "\n".join(missing[:40])
    assert seen > 700  # the walk really reached the questions, help, messages and the case page


def test_the_creole_keeps_every_placeholder_and_form_name():
    for where, text in _everything_a_client_reads():
        english = " ".join(text["en"]) if isinstance(text["en"], list) else text["en"]
        creole = " ".join(text["ht"]) if isinstance(text["ht"], list) else text["ht"]
        assert set(re.findall(r"\{\w*\}", english)) == set(re.findall(r"\{\w*\}", creole)), where
        for name in set(re.findall(r"\b(?:I|N|G)-\d{2,3}[A-Z]?\b|\bUSCIS\b", english)):  # legal form names stay as they are
            assert name in creole, (where, name)


def test_the_portal_page_speaks_every_language():
    """The screens' own words (portal.html UI): one block per language, the same keys as English."""
    page = (REPO / "src" / "portal" / "static" / "portal.html").read_text(encoding="utf-8")
    ui = page[page.index("const UI = {"):page.index("const LOCALES")]
    starts = list(re.finditer(r"\n  (\w\w): \{\n", ui))
    blocks = {m.group(1): ui[m.end():(starts[n + 1].start() if n + 1 < len(starts) else len(ui))] for n, m in enumerate(starts)}
    assert tuple(blocks) == LANGS
    keys = {lang: set(re.findall(r"(?:^\s*|[,{]\s*)([a-z_0-9]+): (?=[\"'(\[{`])", body, flags=re.MULTILINE)) for lang, body in blocks.items()}
    assert len(keys["en"]) > 90
    for lang in LANGS:
        assert keys["en"] <= keys[lang], (lang, sorted(keys["en"] - keys[lang]))
    assert "months" in keys["ht"] and "weekdays" in keys["ht"]  # dates in Creole words: browsers have no Creole calendar
    foreign = page[page.index("const FOREIGN"):page.index("const US_STATES")]
    assert foreign.count('ht: "') == foreign.count('en: "') == 7


def test_a_text_without_the_clients_language_is_shown_in_english():
    bank = copy.deepcopy(load_bank())
    about = bank["sections"][0]
    for text in (about["title"], about["questions"][0]["label"], about["questions"][0]["help"]):
        del text["ht"]
    help = {"sections": {}, "questions": {}, "faq": [{"q": {"pt": "P?", "en": "Q?"}, "a": {"pt": "R", "en": "A"}}],
            "scan_guide": {"title": {"en": "How to scan"}, "steps": [{"who": {"en": "iPhone"}, "how": {"en": "Open Files"}}]}}
    shown = localized(bank, "ht", {}, help)[0]
    assert shown["title"] == "About you" and shown["questions"][0]["label"] == "First and middle name(s)"
    assert shown["questions"][0]["help"].startswith("Exactly as on your passport")
    assert shown["questions"][2]["label"].startswith("Èske ou te janm")  # the texts that have Creole keep it
    assert localized_faq("ht", help) == [{"q": "Q?", "a": "A"}]
    assert localized_scan_guide("ht", help)["steps"][0] == {"who": "iPhone", "how": "Open Files"}
    from portal.engine import _say, _texts

    assert _say({"pt": "x", "en": "English"}, "ht") == "English"
    assert _texts(lambda lg: {"en": "Which is right?", "ht": "Kilès ki kòrèk?"}.get(lg, ""), "ht")["text"] == "Kilès ki kòrèk?"


def test_the_case_page_in_creole_and_in_a_language_it_lacks(monkeypatch):
    import journey

    j = {"stage": "i485_pending", "stage_index": 1, "today": "2026-10-02", "stages": [{"id": "intake"}, {"id": "i485_pending"}, {"id": "resident"}],
         "notices": [{"kind": "biometrics", "appointment": "2026-10-20 09:00", "date": "2026-09-01", "form": "I-485"}],
         "filings": [{"filing": "i485", "mailed_on": "2026-08-03"}]}
    view = journey.client_view(j, "ht")
    assert view["title"] == "Demann green card la depoze" and view["path"][0] == {"id": "intake", "name": "N ap kòmanse dosye ou", "state": "done"}
    assert view["appointments"][0]["what"].startswith("Randevou pou anprent dwèt") and view["happened_label"]
    assert "03/08/2026" in " ".join(h["text"] for h in view["happened"])  # day first, as Haiti writes dates
    assert journey.client_view(j, "fr")["title"] == "Green card application filed"  # not a portal language: English
    texts = copy.deepcopy(journey.settings())
    del texts["client"]["i485_pending"]["ht"]
    monkeypatch.setattr(journey, "settings", lambda: texts)
    assert journey.client_view(j, "ht")["title"] == "Green card application filed"  # one text without Creole: English, not blank


def test_the_client_list_may_name_the_language():
    for written in ("ht", "HT", " Kreyòl ", "kreyol ayisyen", "Haitian Creole", "créole haïtien", "Creole"):
        assert language_code(written) == "ht", written
    assert language_code("Português") == "pt" and language_code("Spanish") == "es" and language_code("en") == "en"
    assert language_code("fr") is None and language_code("") is None


def test_importing_a_creole_client(tmp_path, capsys, monkeypatch):
    from portal.admin import import_clients
    from portal.store import PortalStore

    csv = tmp_path / "clients.csv"
    csv.write_text("id,name,phone,email,language,email_ok,sms_ok,whatsapp_ok\n"
                   "pilot-rose,Rose Egzanp Jean,+1 555 010 0104,rose@example.com,ht,yes,yes,no\n"
                   "pilot-jak,Jak Egzanp Pyè,,jak@example.com,Kreyòl,yes,no,no\n"
                   "pilot-x,Xavier Exemplo,,x@example.com,fr,yes,no,no\n", encoding="utf-8")
    import conflicts

    data = installation(tmp_path, monkeypatch)
    store = PortalStore(data / "portal")
    cases = data / "clients"  # the configured installation's canonical case folders
    assert import_clients(store, csv, cases_root=cases) == ["pilot-rose", "pilot-jak", "pilot-x"]
    assert [store.profile(c)["language"] for c in ("pilot-rose", "pilot-jak", "pilot-x")] == ["ht", "ht", "pt"]
    assert "pilot-x: language 'fr' isn't one the portal speaks" in capsys.readouterr().out  # said, not silently changed
    conflicts.decide(cases / "pilot-rose", "none", "", by="Sam Attorney", role="attorney")  # held until an attorney decides
    notifier = Notifier(data / "portal" / "outbox.jsonl", env={}, cases_root=cases, store=store)
    # Imported CSV choices are not permission or verified phone control.
    held = notifier.send(store.profile("pilot-rose"), "invite")
    assert not any(s["result"] == "sent" or s["result"].startswith("dry-run") for s in held)
    assert not notifier.outbox.exists()
    approve_client(store, "pilot-rose")
    approve_client(store, "pilot-rose", channel="sms")
    tokens = []
    def accepted_neutral(destination, subject, body, kind):
        assert kind == "verify_contact"
        tokens.append(re.search(r"/l/([A-Za-z0-9_-]+)", body).group(1))
        return "sent"  # fictional provider acceptance; no network or message delivery
    with monkeypatch.context() as transport:
        transport.setattr(notifier, "_sms", accepted_neutral)
        verification = notifier.verify_contact(store.profile("pilot-rose"), "sms", actor_email=STAFF)
    assert verification["credential_active"] and len(tokens) == 1
    verified_session = store.redeem_link(tokens[0])
    assert verified_session and store.session_client(verified_session) is None
    assert store.session_client(verified_session, consent_only=True) == "pilot-rose"
    sent = notifier.send(store.profile("pilot-rose"), "invite", "https://portal.example/l/abc")
    assert [s["channel"] for s in sent if s["result"] != "skipped"] == ["email", "sms"]
    out = [json.loads(line) for line in notifier.outbox.read_text(encoding="utf-8").splitlines()]
    assert out[0]["subject"].endswith("kesyonè green card ou") and out[0]["body"].startswith("Bonjou!") and "Rose" not in out[0]["body"]
    assert out[1]["body"].endswith("Reponn STOP pou ou pa resevwa mesaj ankò.")  # STOP: the keyword the SMS provider honors


def test_a_client_reads_the_portal_in_creole(tmp_path, monkeypatch):
    from portal.app import create_app
    from portal.staff_access import issue

    data = installation(tmp_path, monkeypatch)
    root = data / "portal"
    app = create_app(root=root, base_url="https://portal.example", secure_cookies=False, notifier=Notifier(root / "outbox.jsonl", env={}))
    store = app.state.store
    (data / "clients" / "pilot-rose").mkdir()
    store.add_client("pilot-rose", "Rose Egzanp Jean", email="rose@example.com", language="ht")
    approve_client(store, "pilot-rose")
    client = TestClient(app)
    # Staff handover permits reading-language changes; provider credentials
    # intentionally keep their original-language binding.
    link = issue(store.communication_scope(), store, "pilot-rose", actor_email=STAFF, agreed=True)
    token = link["token"]
    assert client.get(f"/l/{token}", follow_redirects=False).status_code == 200
    assert client.post("/api/access-link", json={"token": token}, headers=H).status_code == 200
    me = client.get("/api/me").json()
    assert me["language"] == "ht" and me["first_name"] == "Rose"
    assert me["sections"][0]["title"] == "Enfòmasyon sou ou" and me["sections"][0]["why"].startswith("Enfòmasyon pèsonèl ou")
    assert next(d for d in me["documents"] if d["id"] == "birth_certificate")["label"].startswith("Ak nesans")
    assert me["faq"][0]["q"] == "Konbyen tan sa pran?" and me["scan_guide"]["title"].startswith("Kijan pou eskane")
    assert client.post("/api/language", json={"language": "en"}, headers=H).json()["sections"][0]["title"] == "About you"
    assert client.post("/api/language", json={"language": "ht"}, headers=H).json()["language"] == "ht"
    assert client.post("/api/language", json={"language": "fr"}, headers=H).status_code == 400


def test_a_creole_clients_task_and_the_staff_side(tmp_path, monkeypatch):
    """The task list in Creole; the review app names the language and sends the staff's request as typed."""
    from portal.app import _in_language
    from portal.engine import MESSAGES, _texts
    from portal.store import PortalStore
    from review.answers_page import render
    from review.server import ReviewApp

    task = {"id": "retake:1", "kind": "retake", **_texts(lambda lg: MESSAGES["retake"].get(lg, MESSAGES["retake"]["en"]).format(doc="paspò"), "ht")}
    assert task["text"].startswith("Nou pa t ka li foto paspò ou a") and _in_language(task, "pt")["text"].startswith("Não conseguimos")
    data = installation(tmp_path, monkeypatch)
    store = PortalStore(data / "portal")
    (data / "clients" / "pilot-rose").mkdir()
    store.add_client("pilot-rose", "Rose Egzanp Jean", email="rose@example.com", language="ht")
    approve_client(store, "pilot-rose")
    store.save_answers("pilot-rose", {"given_name": "Rose"})
    app = ReviewApp(data / "clients", schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, data / "portal")
    overview = app.overview()
    assert next(r for r in overview["clients"] if r["id"] == "pilot-rose")["language"] == "ht"
    assert overview["language_names"]["ht"] == "Haitian Creole"
    app.ask_client("pilot-rose", {"text": "Please send your passport.", "doc_id": "passport", "reviewer": "Jane"})
    assert store.requests("pilot-rose")[0]["text"] == "Please send your passport."  # typed in English, sent as typed
    outbox = data / "portal" / "outbox.jsonl"
    assert not outbox.exists()  # asking alone does not publish unreviewed wording or send
    from portal.communication_consent import retain_evidence
    from portal.request_readiness import review_request as publish_review
    scope = store.communication_scope()
    evidence = retain_evidence(scope, b'{"fictional":true,"review":"Explicit complete English fallback choice"}',
                               actor_email=ATTORNEY, kind="request_wording", store=store, client="pilot-rose")
    publish_review(scope, store, "pilot-rose", store.requests("pilot-rose")[0]["id"], actor_email=ATTORNEY,
                   evidence_ref=evidence, mode="english_fallback", publish=True,
                   fallback_reason="FICTIONAL TEST: the exact English request was reviewed; a qualified Creole translation is unavailable.")
    sent = Notifier(outbox, env={}, cases_root=data / "clients", store=store).send(store.profile("pilot-rose"), "request")
    assert any(s["result"].startswith("dry-run") for s in sent)
    message = json.loads(outbox.read_text(encoding="utf-8").splitlines()[-1])
    assert message["subject"].endswith("nou bezwen yon bagay nan men ou") and "passport" not in message["body"].lower()
    page = render("Rose Egzanp Jean", store.answers("pilot-rose"), load_bank(), "ht", None)
    assert "Asked in Haitian Creole: Premye non ak dezyèm non" in page


@pytest.mark.parametrize("lang", ["pt", "es", "en", "ht"])
def test_dates_on_a_confirmation_follow_the_language(lang):
    from portal.engine import _shown

    assert _shown("2007-09-04", lang) == ("09/04/2007" if lang == "en" else "04/09/2007")
