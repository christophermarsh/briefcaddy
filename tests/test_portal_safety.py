"""The client's side, the visits' leftovers (docs/research/buyer_walkthrough_3.md 2.3, 2.4, 2.6, 2.7, 3.3, 3.11; _4.md 5.1 to 5.3): a way out
on every portal screen, a safety line on the welcome screen, "sent to the office" until the office marks the answer done, no English word
on a Portuguese, Spanish or Haitian Creole screen, a new photo seen at once by the office (and read at once when the portal and the
review app share a machine), the welcome screen's list, and the appointment page on a phone. Invented clients only (src/portal/demo.py)."""

from __future__ import annotations

import io
import json
import re
import shutil
import sys
from pathlib import Path

import pytest
import test_portal_languages as langs
from communication_fixture import (
    accepted_link,
    approve_client,
    installation,
    review_request,
)
from fastapi.testclient import TestClient
from PIL import Image

import schema_path
from portal import demo as seed
from portal.app import create_app, sent_to_office
from portal.bank import load_bank
from portal.engine import arrived, decided_facts, read_new_uploads, settle_decided
from portal.store import PortalStore
from review.server import ReviewApp

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from portal_english import english_words

HTML = (REPO / "src" / "portal" / "static" / "portal.html").read_text(encoding="utf-8")
SCRIPT = re.search(r"<script[^>]*>(.*)</script>", HTML, re.DOTALL).group(1)
ID = seed.DEMO_ID
H = {"X-Portal": "1"}
LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"|`((?:[^`\\]|\\.)*)`')


def _ui(lang: str) -> list[str]:
    """Every text literal the page holds for one language (the blocks of UI, UI_MSG and UI_SAFE named by that language's code)."""
    out, inside = [], False
    for line in SCRIPT.splitlines():
        if re.match(rf"^  {lang}: \{{", line):
            inside = True
        elif re.match(r"^  (pt|es|en|ht): \{", line) or line.startswith(("};", "const ", "for ")):
            inside = False
        if inside:
            out += [m.group(1) or m.group(2) or "" for m in LITERAL.finditer(line)]
    return out


# -- 1. the way out and the safety line -------------------------------------------------------------------------


def test_the_page_has_a_way_out_on_every_screen_and_a_safety_line_in_four_languages():
    assert 'id="leave"' in HTML and HTML.index('id="leave"') < HTML.index('id="wrap"')  # in the top bar, which every screen shares
    assert 'const LEAVE_TO = "https://www.weather.gov/"' in HTML and "location.replace(LEAVE_TO)" in HTML
    for key in ("leave:", "leave_short:", "safe_line:", "sign_out_now:", "signed_out:", "sent_to_office:", "received_thanks:", "show_all:", "answer_waiting:"):
        assert HTML.count(key) == 4, key  # Portuguese, Spanish, English, Haitian Creole
    assert "If someone else uses your phone, you can sign out any time and come back with a new link. The office can also talk with you by phone instead." in HTML
    assert "Sent to the office on ${day}. They will read it; nothing more to do here." in HTML
    assert HTML.count("safetyLine()") >= 3  # defined, then on the welcome screen and on the finished screen: every client, restricted or not
    assert 'api("POST", "/api/logout")' in HTML


def test_the_portal_never_uses_the_words_vawa_or_abuse():
    """The data statement's promise: the client portal says nothing about why a person is a client (it must not know who is protected)."""
    pattern = re.compile(r"vawa|abus|maltrat|abuz|violence against", re.IGNORECASE)
    assert not pattern.findall(HTML)
    for where, text in langs._everything_a_client_reads():
        for value in text.values():
            for part in value if isinstance(value, list) else [value]:
                assert not pattern.search(str(part)), (where, part)


# -- 4. no English on a Portuguese, Spanish or Haitian Creole screen ----------------------------------------------


@pytest.mark.parametrize("lang", ["pt", "es", "ht"])
def test_no_english_word_on_a_non_english_screen(lang):
    found = {text[:80]: words for text in _ui(lang) if (words := english_words(text))}
    for where, text in langs._everything_a_client_reads():
        if where.startswith("question_bank.json.translation_status") or ".scan_guide." in where:  # the status is internal; the guide names the phone app's own buttons
            continue
        value = text.get(lang)
        for part in value if isinstance(value, list) else [value]:
            if words := english_words(part or ""):
                found[f"{where}: {(part or '')[:60]}"] = words
    assert not found, "\n".join(f"{k} -> {v}" for k, v in list(found.items())[:30])
    assert len(_ui(lang)) > 120  # the walk really reached the page's own words


def test_the_english_word_finder_finds_what_a_person_would_circle():
    assert english_words("Cartão do work permit (EAD)") == ["work", "permit"]
    assert english_words("Seu green card e o Social Security chegaram: I-485, A-Number, ZIP") == []  # names, acronyms, the card
    assert english_words("Pode tirar outra? A foto está fora de foco.") == []


def test_times_and_dates_are_written_in_the_clients_language_and_the_offices_zone():
    assert "function stamp(iso)" in HTML and "timeZone: zone" in HTML  # the office's clock, not the phone's
    assert "toLocaleTimeString([]" not in HTML  # the phone's own language and zone are not used for a time the client reads
    assert "function clockWords(text)" in HTML and HTML.count("clockWords(") >= 4  # appointment times, in the language's own way
    for z in ("(${z})",):
        assert HTML.count("time_yours: (z) => `") == 4 and z in HTML


# -- 7. the appointment page on a phone ---------------------------------------------------------------------------


def test_the_stage_ladder_collapses_to_the_current_stage_on_a_phone():
    assert "path collapsed" in HTML or '" collapsed"' in HTML
    assert ".path.collapsed li:not(.now) { display: none; }" in HTML and "@media (max-width: 700px) { .pathtoggle { display: inline-flex; } }" in HTML
    assert HTML.count("show_all:") == 4 and HTML.count("show_less:") == 4


# -- 3 and 6. the office has your answer; the welcome list ------------------------------------------------------------


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    root = tmp_path_factory.mktemp("safety")
    seed.seed(PortalStore(root / "portal"), root / "clients")
    return root


@pytest.fixture
def world(seeded, tmp_path, monkeypatch):
    root = installation(tmp_path / "w", monkeypatch)
    # Cache fictional document processing, not identity/consent/access proofs.
    # Each copied case receives its own genuine enrollment in this installation.
    shutil.copytree(seeded / "clients", root / "clients", dirs_exist_ok=True)
    store = PortalStore(root / "portal")
    store.add_client(ID, seed.PROFILE["name"], phone=seed.PROFILE["phone"], email=seed.PROFILE["email"],
                     language=seed.PROFILE["language"], consent=seed.PROFILE["consent"])
    cached = PortalStore(seeded / "portal")
    shutil.copytree(cached.client_dir(ID) / "uploads", store.client_dir(ID) / "uploads")
    store.save_answers(ID, cached.answers(ID))
    store.update_uploads(ID, cached.uploads(ID))
    store.save_tasks(ID, cached.tasks(ID))
    store.update_profile(ID, status="started")
    meta = json.loads((root / "clients" / ID / "meta.json").read_text(encoding="utf-8"))
    meta["source_folder"] = str(root / "portal" / "clients" / ID / "uploads")  # the copy's own uploads, as the case was made from the original's
    (root / "clients" / ID / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    approve_client(store, ID)
    app = ReviewApp(root / "clients", schema_path.path("field_map", "i485"), schema_path.path("template", "i485"),
                    schema_path.path("law", "policy_sijs"), root / "portal")
    return root, store, app


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(out, format="PNG")
    return out.getvalue()


def _ask(store, facts=None):
    request = store.add_request(ID, "What is your father's date of birth?", None, "Paulo Paralegal", facts=facts or ["applicant.father_dob"],
                             typed={"type": "date", "text_client": "Qual é a data de nascimento do seu pai?", "language": "pt",
                                    "machine_translated": False, "needs_translator": False})
    return review_request(store, ID, request, mode="translated")["request"]


def test_the_answer_stays_as_sent_to_the_office_until_the_office_marks_it_done(world, monkeypatch):
    root, store, app = world
    request = _ask(store)
    bank = load_bank()
    assert sent_to_office(store.requests(ID), "pt", bank) == []  # not answered yet: it is a box, not a line
    store.answer_request(ID, request["id"], reply="1970-05-04")
    [line] = sent_to_office(store.requests(ID), "pt", bank)
    assert line["text"] == "Qual é a data de nascimento do seu pai?" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", line["on"]) and not line["doc"]
    assert [r["id"] for r in store.requests(ID) if r["status"] == "open"] == []

    # the client's own page says so, and keeps saying so on the next visit
    monkeypatch.setenv("PORTAL_DATA", str(root / "portal"))
    client = TestClient(create_app(root / "portal"))
    assert client.get(f"/l/{accepted_link(store, ID)}", follow_redirects=False).status_code == 303
    assert [x["id"] for x in client.get("/api/me").json()["sent"]] == [request["id"]]

    # the office marks it done (the Requests list's button): gone from the client's page, kept on file with who and when
    done = app.request_done(ID, {"id": request["id"], "reviewer": "Paulo Paralegal"})
    assert done == {"settled": 1}
    assert client.get("/api/me").json()["sent"] == []
    [kept] = [r for r in store.requests(ID) if r["id"] == request["id"]]
    assert kept["settled_by"] == "Paulo Paralegal" and kept["settled_at"] and kept["status"] == "answered"
    with pytest.raises(ValueError, match="Enter your name"):
        app.request_done(ID, {"id": request["id"]})


def test_deciding_the_card_the_question_was_about_settles_the_answer(world):
    _root, store, _app = world
    request = _ask(store, ["applicant.father_dob"])
    store.answer_request(ID, request["id"], reply="1970-05-04")
    other = _ask(store, ["applicant.mother_dob"])
    store.answer_request(ID, other["id"], reply="1980-01-02")
    decided = {"applicant.father_dob": {"action": "confirm", "reviewer": "Jane Reviewer", "at": "2026-10-03T10:00:00+00:00", "item": {"facts": ["applicant.father_dob"]}}}
    assert settle_decided(store, ID, decided) == [request["id"]]  # only the question whose card was decided
    left = [x["id"] for x in sent_to_office(store.requests(ID), "pt", load_bank())]
    assert left == [other["id"]]
    assert next(r for r in store.requests(ID) if r["id"] == request["id"])["settled_by"] == "Jane Reviewer"
    assert decided_facts  # imported: the review app passes what the reviewers decided


def test_a_document_the_office_asked_for_is_also_sent_to_the_office(world):
    _root, store, _app = world
    request = store.add_request(ID, "Please send your marriage certificate", "marriage_certificate", "Paulo Paralegal",
                                typed={"language": "pt", "text_client": "Envie a sua certidão de casamento.", "type": "text"})
    request = review_request(store, ID, request, mode="translated")["request"]
    record = store.add_upload(ID, "marriage_certificate", "m.png", _png(), "image/png")
    store.answer_request(ID, request["id"], upload=record["id"])
    [line] = sent_to_office(store.requests(ID), "en", load_bank())
    assert line["doc"] is True


# -- 5. a new photo arrives ---------------------------------------------------------------------------------------


def test_what_the_client_sent_is_listed_for_the_office_until_it_is_read(world):
    root, store, app = world
    assert arrived(store.uploads(ID)) == []  # everything the seed uploaded has been read
    store.add_upload(ID, "passport", "p.png", _png(), "image/png", retake=True)
    store.add_upload(ID, "marriage_certificate", "m.png", _png(), "image/png")
    rows = arrived(store.uploads(ID))
    assert [(r["document"], r["retake"]) for r in rows] == [("passport", True), ("marriage certificate", False)]
    docs = app.documents(ID)["arrived"]
    assert [r["document"] for r in docs] == ["passport", "marriage certificate"] and all(re.fullmatch(r"\d\d/\d\d/\d{4}", r["on"]) for r in docs)
    from review.overview import my_work, overview
    from review.state import Catalog

    field_map = json.loads((schema_path.path("field_map", "i485")).read_text(encoding="utf-8"))
    catalog = Catalog(field_map, schema_path.path("template", "i485"), json.loads((schema_path.path("law", "policy_sijs")).read_text(encoding="utf-8"))["policies"])
    work = my_work(overview(root / "clients", field_map, schema_path.path("template", "i485"), catalog, root / "portal")["clients"], "paralegal")
    assert work["counts"]["new_photos"] == 2 and [p["document"] for p in work["new_photos"]] == ["passport", "marriage certificate"]


def test_a_new_photo_is_read_at_once_when_the_case_is_on_this_machine_and_the_retake_closes(world):
    root, store, _app = world
    case = root / "clients" / ID
    assert store.case_here(ID, root / "clients")  # the case was made from this client's own uploads
    before = len(json.loads((case / "documents.json").read_text(encoding="utf-8"))["documents"])
    store.save_tasks(ID, [{"id": "retake:old", "kind": "retake", "doc_id": "passport", "upload": "old", "text": "x", "texts": {}, "asked_at": "2026-10-02T10:00:00-04:00"}])
    lines = seed.DOCUMENTS["passport"][1] + ["EXEMPLO: SECOND PHOTO"]  # another photo of the same page: other bytes, so its own record
    store.add_upload(ID, "passport", "new.pdf", seed.document_pdf(lines), "application/pdf", retake=True)
    store.mark_retakes_received(ID, "passport")
    assert [t["id"] for t in store.tasks(ID)] == ["retake:old"] and store.tasks(ID)[0]["received_at"]  # "Thank you, we have it", until it is read

    done = read_new_uploads(store, ID, root / "clients")
    assert done == {"read": 1}
    assert arrived(store.uploads(ID)) == []  # read: no longer "not read yet"
    assert [u["status"] for u in store.uploads(ID) if u["doc_id"] == "passport"][-1] == "checked"
    assert not any(t["kind"] == "retake" for t in store.tasks(ID))  # the retake task closed
    records = json.loads((case / "documents.json").read_text(encoding="utf-8"))["documents"]
    assert len(records) == before + 1 and records[-1]["type"] == "passport" and records[-1]["source"] == "portal"
    assert "uploads_read" in (store.client_dir(ID) / "events.jsonl").read_text(encoding="utf-8")


def test_a_new_photo_waits_for_tonight_when_the_case_is_not_on_this_machine(world, tmp_path):
    _root, store, _app = world
    store.add_upload(ID, "passport", "new.png", _png(), "image/png", retake=True)
    assert not store.case_here(ID, tmp_path / "nowhere")
    assert read_new_uploads(store, ID, tmp_path / "nowhere") == {"read": 0, "why": "tonight"}
    [row] = arrived(store.uploads(ID))
    assert row["reading"] == "tonight"


def test_the_office_screens_say_it():
    html = (REPO / "src" / "review" / "static" / "index.html").read_text(encoding="utf-8")
    assert "Photos that arrived: ${(w.totals && w.totals.new_photos) || w.new_photos.length}" in html  # all of them are counted, 50 are shown
    assert '"New from the client, ", a.on, ":"' in html and "not read yet" in html and '(retake)' in html
    assert "It will be read tonight." in html and "It is being read now." in html
    assert '"/api/request-done"' in html and "Mark done" in html


# -- 6. the welcome screen's list --------------------------------------------------------------------------------


def test_the_welcome_screen_lists_each_thing_with_its_own_button():
    assert 'class: "todo-row"' in HTML and "S.focus = task.id" in HTML and "function renderFocus(m, task)" in HTML
    assert "go(started && firstOpen >= 0 ? firstOpen : 0); $(\"main\").querySelector(\".alert\")" not in HTML  # no button that lands on step 12
    assert HTML.count("asked_go:") == 4 and HTML.count("home:") >= 4


# -- the verifier's fixes -------------------------------------------------------------------------------------------


def test_leave_ends_the_session_and_nothing_in_the_source_speaks_of_protection():
    assert 'fetch("/api/logout"' in HTML and "keepalive: true" in HTML and HTML.index('fetch("/api/logout"') < HTML.index("location.replace(LEAVE_TO)")
    assert "min-height: 44px; min-width: 44px" in HTML and ".top { position: sticky; top: 0; z-index: 50;" in HTML  # a target a thumb finds, above the sheets (40)
    assert not re.search(r"protect", HTML, re.IGNORECASE)  # a person who views the page's source reads nothing about why
    assert "le ${day}" not in HTML  # French "le" in the Creole date line


def test_the_portuguese_bank_writes_the_zip_code_one_way():
    bank = (schema_path.path("question", "intake")).read_text(encoding="utf-8")
    assert '"pt": "CEP / ZIP code"' not in bank and bank.count('"pt": "Código ZIP"') >= 4


def test_every_document_name_the_office_reads_is_short_and_has_no_dash():
    """The Documents tab and My work name a document by `staff_name`/`_doc_name`, never by a sentence of the questionnaire."""
    import documents
    from portal.bank import bank_for
    from portal.engine import _doc_name, staff_name

    names = {}
    for filing in ("i485", "n400"):
        for d in bank_for({"filing": filing})["documents"]:
            names[d["id"]] = _doc_name(d["id"], None, {"doc_id": d["id"]}, "en")
    taxonomy = {t: documents.name(t) for t in documents.types()}  # the Documents tab's own names: no dash anywhere (a few forms' names run to 60 characters)
    assert not [v for v in taxonomy.values() if "—" in v or " -- " in v]
    names["unknown-kind"] = staff_name("unknown-kind")
    names["court-thing"] = staff_name("court-thing")
    assert names["unknown-kind"] == "document" and names["court-thing"] == "court papers"
    long = {k: v for k, v in names.items() if len(v) > 45 or "—" in v or " -- " in v}
    assert not long, long
    assert names["court_disposition"] == "court disposition" and "—" not in names["immigration_court"]
    for lang in ("pt", "es", "ht"):  # a sentence to the client names a document just as shortly
        for d in bank_for({"filing": "i485"})["documents"]:
            word = _doc_name(d["id"], None, {"doc_id": d["id"]}, lang)
            assert len(word) <= 40 and "—" not in word, (lang, d["id"], word)


def test_the_creole_thread_labels_the_offices_english_words():
    assert 'm.followup ? el("div", { class: "sub orig" }, t("msg_original")) : null' in HTML
    assert 'msg_original: "Mo biwo a menm (an angle)"' in HTML
