# ruff: noqa: F811  (the fixtures imported from test_restricted are used as arguments)
"""Fictional example or implementation helper."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from test_drafting import case, firm  # noqa: F401 -- the made-up asylum case with a declaration, and the fake translator
from test_prefile import live  # noqa: F401 -- the live-check results a mailing is checked against (an autouse fixture there)
from test_restricted import PASSWORD, app, call, server, sign_in, world  # noqa: F401 -- the made-up restricted world and its review app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import import_docketwise as imp  # noqa: E402
import schema_path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"


# -- 1. an imported client can be invited before the case is processed ----------------------------------------------------


def _import_two(world, tmp_path):
    (tmp_path / "contacts.csv").write_text(
        "id,First Name,Last Name,Email,Mobile Phone Number,Language,Email OK\n"
        "c1,Ana,Importada Exemplo,ana.importada@example.com,+1 555 010 0301,Portuguese,yes\n"
        "c2,Rosa,Asilo Exemplo,rosa.asilo@example.com,+1 555 010 0302,Portuguese,yes\n", encoding="utf-8")
    (tmp_path / "matters.csv").write_text("ID,Number,Title,Client ID,Type,Status,Archived\n"
                                          "5101,A-1,Ana green card,c1,I-485,Open,\n"
                                          "5102,A-2,Rosa asylum,c2,Asilo,Open,\n", encoding="utf-8")
    return imp.run_import(tmp_path / "contacts.csv", tmp_path / "matters.csv", None, world, world.parent / "portal", cases=world)


def test_a_client_the_importer_made_can_be_invited_from_the_screen_before_the_case_is_processed(world, app, server, tmp_path):
    run = _import_two(world, tmp_path)
    ids = {o.title.split()[0]: o.client_id for o in run.outcomes if o.client_id}
    ana, rosa = ids["Ana"], ids["Rosa"]
    assert not (world / ana / "fact_graph.json").exists()  # nothing processed: the folder holds the documents and the import's record
    jane = sign_in(server, "jane@firm.example")
    sam = sign_in(server, "sam@firm.example")
    outbox = world.parent / "portal" / "outbox.jsonl"
    before = len(outbox.read_text(encoding="utf-8").splitlines()) if outbox.exists() else 0
    # an imported client waits for an attorney's conflict decision (src/conflicts.py): held until then, invited after
    status, text = call(server + "/api/client-invite", jane, {"client": ana, "reviewer": "Jane Doe"})
    assert status == 400 and "waiting for an attorney's decision" in text
    assert call(server + "/api/conflict-decide", sam, {"client": ana, "decision": "none", "reviewer": "Sam Attorney"})[0] == 200
    status, text = call(server + "/api/client-invite", jane, {"client": ana, "reviewer": "Jane Doe"})
    assert status == 200, text
    rows = [json.loads(line) for line in outbox.read_text(encoding="utf-8").splitlines()]
    assert len(rows) > before and any(r.get("to") == "ana.importada@example.com" for r in rows[before:])
    # the restricted row: a paralegal is told nothing (the same answer as a made-up id), the attorney's invitation sends nothing
    assert call(server + "/api/client-invite", jane, {"client": rosa, "reviewer": "Jane Doe"})[0] == 404
    count = len(outbox.read_text(encoding="utf-8").splitlines())
    status, text = call(server + "/api/client-invite", sam, {"client": rosa, "reviewer": "Sam Attorney"})
    assert len(outbox.read_text(encoding="utf-8").splitlines()) == count and "rosa.asilo@example.com" not in outbox.read_text(encoding="utf-8")
    assert status in (200, 400), text


# -- 2. no firm's name is built in ---------------------------------------------------------------------------------------


def _ssn_case(world, *, with_answer: bool):
    """A case whose Social Security number comes from the card (and, when with_answer, from the client's own answer too, one digit apart)."""
    from factgraph import FactGraph
    from test_restricted import doc, make_case

    d = make_case(world, "case-sam", "Sam Exemplo Souza", [doc("s1", "passport"), doc("s2", "ssn_card")])
    g = FactGraph.load(d / "fact_graph.json")
    g.add_source("applicant.ssn", "s2.pdf", "ssn_card", "123-45-6789", "123456789", 0.9)
    if with_answer:
        g.add_source("applicant.ssn", "portal questionnaire", "intake_questionnaire", "123-45-6780", "123456780", 0.95, tier=3)
    g.save(d / "fact_graph.json")
    return d


def _set_whose(app, who, value):
    return app.document_change("case-sam", {"reviewer": "Ana Attorney", "id": "s2", "field": "person", "value": value}, "attorney")


def test_a_document_set_to_the_spouse_stops_feeding_the_clients_answers_and_setting_it_back_restores_them(world, app):
    from review.state import load_decision_log, load_decisions, reviewed_graph

    d = _ssn_case(world, with_answer=True)
    assert reviewed_graph(d).get("applicant.ssn").status == "conflict"  # the card against the client's own answer
    done = _set_whose(app, "Ana Attorney", "spouse")
    assert "set aside" in done["note"] and "the spouse" in done["note"]
    fact = reviewed_graph(d).get("applicant.ssn")
    assert fact.status == "resolved" and fact.value == "123456780" and [s.doc_id for s in fact.sources] == ["portal questionnaire"]  # the client's own answer
    log = load_decisions(d)["document_person:s2"]  # the Decision log says what was set aside, by whom and why
    assert log["reviewer"] == "Ana Attorney" and "applicant.ssn" in log["item"]["facts"] and "set aside" in log["note"] and "the spouse" in log["note"]
    assert json.loads((d / "fact_graph.json").read_text(encoding="utf-8"))["facts"]["applicant.ssn"]["sources"][0]["doc_id"] == "s2.pdf"  # the saved graph keeps it
    back = _set_whose(app, "Ana Attorney", "applicant")
    assert "client's again" in back["note"]
    assert reviewed_graph(d).get("applicant.ssn").status == "conflict" and "document_person:s2" not in load_decisions(d)
    assert load_decision_log(d)["document_person:s2"]["undone"]["by"] == "Ana Attorney"


def test_a_number_left_with_only_what_the_client_typed_is_checked_again_when_its_card_is_set_aside(world, app):
    """Verifier: with the card set to the spouse the box takes the client's typed number and nothing flagged a typo any more."""
    from review.state import current_flags

    d = _ssn_case(world, with_answer=True)
    before = [f for f in current_flags(d, app.field_map, app.template)[1] if f.fact_key == "applicant.ssn"]
    assert before  # the card against the typed number: a card to settle
    _set_whose(app, "Ana Attorney", "spouse")
    after = [f for f in current_flags(d, app.field_map, app.template)[1] if f.fact_key == "applicant.ssn"]
    assert after and all(f.level in ("review", "blocking") for f in after), after  # still a card: the number is only what the client typed
    _set_whose(app, "Ana Attorney", "applicant")


def test_the_green_card_packet_holds_no_document_set_to_another_person_and_says_why(world, app, tmp_path):
    """Verifier: the I-485 packet still enclosed the Social Security card set to the spouse."""
    import packet
    from test_restricted import doc, make_case

    d = make_case(world, "case-exhibits", "Sam Exemplo Souza", [doc("s1", "passport"), doc("s2", "ssn_card")])
    source = tmp_path / "source"
    source.mkdir()
    for name in ("s1.pdf", "s2.pdf"):
        (source / name).write_bytes(b"%PDF-1.4 made up")
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    meta |= {"source_folder": str(source), "classifications": {"s1.pdf": "passport", "s2.pdf": "ssn_card"}}
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    app.document_change("case-exhibits", {"reviewer": "Ana Attorney", "id": "s2", "field": "person", "value": "spouse"}, "attorney")
    schema = packet.for_case(packet.load_filing("i485"), d)
    plan = packet.plan(d, {}, schema)
    in_packet = [f["doc"] for ex in plan["exhibits"] for f in ex["files"]]
    assert "s2.pdf" not in in_packet
    held = next(f for f in plan["left_out"] if f["doc"] == "s2.pdf")
    assert "Set to the spouse by Ana Attorney" in held["why"] and "never in the client's own packet" in held["why"]


def test_a_document_that_changes_what_is_worked_out_lists_those_facts_in_the_decision_log_too(world, app):
    """Verifier: an I-94 set to a child moved Part 9 and the log named only what the document gave directly. The log now names every fact that changed."""
    from factgraph import FactGraph
    from review.state import load_decisions
    from test_restricted import doc, make_case

    d = make_case(world, "case-derived", "Sam Exemplo Souza", [doc("i1", "i94")])
    g = FactGraph.load(d / "fact_graph.json")
    g.add_source("applicant.i94_arrival_date", "i1.pdf", "i94", "2019-07-15", "2019-07-15", 0.95)
    g.save(d / "fact_graph.json")
    (d / "fact_graph_raw.json").write_text((d / "fact_graph.json").read_text(encoding="utf-8"), encoding="utf-8")
    app.document_change("case-derived", {"reviewer": "Ana Attorney", "id": "i1", "field": "person", "value": "child_1"}, "attorney")
    facts = load_decisions(d)["document_person:i1"]["item"]["facts"]
    assert "applicant.i94_arrival_date" in facts


def test_a_number_only_the_card_gave_leaves_the_box_empty_while_the_card_is_the_spouses(world, app):
    from review.state import reviewed_graph

    d = _ssn_case(world, with_answer=False)
    assert reviewed_graph(d).get("applicant.ssn").value == "123456789"
    _set_whose(app, "Ana Attorney", "spouse")
    assert reviewed_graph(d).get("applicant.ssn") is None
    _set_whose(app, "Ana Attorney", "applicant")
    assert reviewed_graph(d).get("applicant.ssn").value == "123456789"


def test_the_clients_phone_stops_asking_about_a_card_set_to_the_spouse_and_asks_again_when_it_is_set_back(world, app):
    from portal.store import PortalStore

    d = _ssn_case(world, with_answer=True)
    store = PortalStore(world.parent / "portal")
    store.add_client("case-sam", "Sam Exemplo Souza", email="sam@example.com", language="pt")
    from portal.engine import sync_confirmations

    assert [t["question"] for t in sync_confirmations(store, "case-sam", d)] == ["ssn"]
    assert [t["kind"] for t in store.tasks("case-sam")] == ["confirm"]
    _set_whose(app, "Ana Attorney", "spouse")
    assert store.tasks("case-sam") == []  # rebuilt at once, not at the overnight run
    _set_whose(app, "Ana Attorney", "applicant")
    assert [t["question"] for t in store.tasks("case-sam")] == ["ssn"]


# -- 11. small ones ----------------------------------------------------------------------------------------------------------------


def test_a_case_with_nobody_s_name_goes_by_the_portals_name_then_new_case_with_the_date_never_its_id(world, app):
    from factgraph import FactGraph
    from portal.store import PortalStore
    from review.state import display_name
    from test_restricted import make_case

    d = make_case(world, "marie-exemplo-ayisyen", "Nobody Known", [])
    FactGraph("marie-exemplo-ayisyen").save(d / "fact_graph.json")  # a case made by Add documents: no name read from anything
    assert display_name(d, "ANA SOUZA") == "ANA SOUZA"
    assert display_name(d).startswith("New case, ") and re.fullmatch(r"New case, \d\d/\d\d/\d{4}", display_name(d)) and "marie" not in display_name(d).lower()
    store = PortalStore(world.parent / "portal")
    store.add_client("marie-exemplo-ayisyen", "Marie Exemplo Ayisyen", email="marie@example.com", language="ht")
    assert display_name(d) == "Marie Exemplo Ayisyen"
    rows = {r["id"]: r for r in app.overview("attorney", None)["clients"]}
    assert rows["marie-exemplo-ayisyen"]["summary"]["name"] == "Marie Exemplo Ayisyen"
    assert {c["id"]: c["name"] for c in app.clients()}["marie-exemplo-ayisyen"] == "Marie Exemplo Ayisyen"
    import index

    assert index._name_and_state(d)[0] == "Marie Exemplo Ayisyen"  # Search names the case the same way


def test_the_new_case_date_is_the_day_the_case_was_made_and_reprocessing_does_not_move_it(world):
    import os
    import time

    from factgraph import FactGraph
    from review.state import display_name
    from test_restricted import make_case

    d = make_case(world, "new-case-date", "Nobody Known", [])
    FactGraph("new-case-date").save(d / "fact_graph.json")
    (d / "source").mkdir()
    (d / "source" / "passport.pdf").write_bytes(b"%PDF-1.4 made up")  # the first document, put there when the case was made
    old = time.time() - 10 * 86400
    for p in [d, d / "source" / "passport.pdf", *d.iterdir()]:
        os.utime(p, (old, old))  # the case was made ten days ago
    first = display_name(d)
    for name in ("meta.json", "fact_graph.json", "documents.json"):
        os.utime(d / name, None)  # a reprocessing rewrites them today
    assert display_name(d) == first and first != f"New case, {time.strftime('%m/%d/%Y')}"


def test_the_portuguese_retake_text_has_no_gender_slip():
    from portal.engine import MESSAGES

    for kind in ("retake", "retake_quality", "still_need"):
        assert "(a)" not in MESSAGES[kind]["pt"] and "do seu documento" in MESSAGES[kind]["pt"], kind


# -- 10. the fifth wrong code says the account is locked -------------------------------------------------------------------------


def test_the_fifth_wrong_code_says_the_account_is_locked_for_fifteen_minutes(app, server):
    import urllib.request

    def post(path, body, cookie=None):
        req = urllib.request.Request(server + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Review-App": "1"} | ({"Cookie": cookie} if cookie else {}))
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read()), r.headers.get("Set-Cookie")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), None

    status, _, cookie = post("/api/login", {"email": "sam@firm.example", "password": PASSWORD})
    assert status == 200
    cookie = cookie.split(";")[0]
    seen = [post("/api/code", {"code": "000000"}, cookie) for _ in range(5)]
    assert [s[0] for s in seen[:4]] == [400, 400, 400, 400] and all("didn't work" in s[1]["error"] for s in seen[:4])
    status, body, _ = seen[4]
    assert "locked for 15 minutes" in body["error"] and "took too long" not in body["error"] and not body.get("sign_in")
    # the step under way ended with the lock: a sixth try still says why, never "took too long"
    status, body, _ = post("/api/code", {"code": "000000"}, cookie)
    assert "locked for 15 minutes" in body["error"], body


# -- 9. the smoothing card: was and suggested, and no Accept for no change -----------------------------------------------------


def test_a_suggestion_identical_to_the_paragraph_says_no_change_and_cannot_be_accepted(case, firm):
    import drafting
    import settings

    settings.save("drafting", {"grammar_smoothing": "on"}, "Ana Attorney")
    card = drafting.smooth(case, "i589", "Paulo Paralegal", model=lambda t: (t, "fake-model"))
    para = next(p for p in card["paragraphs"] if p["id"] == "asylum.b1b_explain")
    assert para["suggestion"]["accepted"] and para["suggestion"]["same"] is True
    with pytest.raises(ValueError, match="No change was suggested"):
        drafting.accept_suggestion(case, "i589", "asylum.b1b_explain", "Ana Attorney", "attorney")
    changed = "I am afraid that the gang will kill me if I go back, because they said so in front of the my mother"
    state = json.loads((case / drafting.STATE).read_text(encoding="utf-8"))
    state["filings"]["i589"]["smoothing"] = {}
    (case / drafting.STATE).write_text(json.dumps(state), encoding="utf-8")
    para = next(p for p in drafting.smooth(case, "i589", "Paulo Paralegal", model=lambda t: (changed, "fake-model"))["paragraphs"] if p["id"] == "asylum.b1b_explain")
    assert para["suggestion"]["same"] is False and para["suggestion"]["after"] == changed  # the card has both texts to show on their own lines


# -- 7. TPS panels on dead ground ----------------------------------------------------------------------------------------------


def _tps_case(world, country, name="case-tps"):
    from factgraph import FactGraph
    from test_restricted import doc, make_case

    d = make_case(world, name, "Tia Exemplo Souza", [doc("t1", "passport")])
    g = FactGraph.load(d / "fact_graph.json")
    for key, value in {"applicant.citizenship": country, "applicant.country_of_birth": country}.items():
        g.add_source(key, "t1.pdf", "passport", value, value, 0.9)
    g.save(d / "fact_graph.json")
    return d


def test_a_designation_awaiting_an_announcement_says_nothing_to_file_and_offers_nothing(world):
    import filing_questions
    import tps
    from datetime import date

    d = _tps_case(world, "EL SALVADOR")
    today = date(2026, 10, 3)
    graph = filing_questions.graph_for("tps", d, today)
    assert tps.window(graph, today)["kind"] == "awaiting" and not tps.offered(graph, today)
    panel = filing_questions.status("tps", d, today)
    assert panel["closed"] and panel["questions"] == [] and panel["client_questions"] == [] and len(panel["notes"]) == 1
    text = panel["notes"][0]["text"]
    assert "continued through 09/09/2026" in text and "nothing to file here" in text and "8 U.S.C. 1254a(b)(3)(C)" in text and "read 10/03/2026" in text
    assert "ended" not in text and "has passed" not in text and "ran through" not in text and "Sept." not in text  # not established: the statute extends it
    assert "says an announcement will be made" in text and "read 10/03/2026" in text  # the page's words, with the day read
    assert panel["problems"] and "nothing to file" in panel["problems"][0]  # a packet built for it stays a draft with the reason


def test_a_terminated_country_shows_the_sentence_and_nothing_else(world):
    import filing_questions
    from datetime import date

    panel = filing_questions.status("tps", _tps_case(world, "VENEZUELA"), date(2026, 10, 3))
    assert panel["closed"] and panel["questions"] == [] and panel["client_questions"] == []
    assert [n["title"] for n in panel["notes"]] == ["Terminated"] and "Nothing to file now" in panel["notes"][0]["text"]


def test_a_country_whose_designation_has_not_ended_keeps_its_questions_and_a_later_date_from_the_page_switches_it_on(world, tmp_path, monkeypatch):
    import filing_questions
    import tps
    from datetime import date

    today = date(2026, 10, 3)
    sudan = filing_questions.status("tps", _tps_case(world, "SUDAN", "case-tps2"), today)  # designated through 10/19/2026: not ended
    assert "closed" not in sudan and len(sudan["questions"]) > 50
    data = json.loads(tps.DATA.read_text(encoding="utf-8"))  # the page later shows an extension: its dates are copied in, nothing else changes
    data["countries"]["El Salvador"]["through"] = "2027-09-09"
    data["countries"]["El Salvador"]["periods"].append({"kind": "re-registration", "from": "2026-09-20", "to": "2026-11-20", "source": "TEST notice", "url": "https://example.test/fr"})
    (tmp_path / "tps.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(tps, "DATA", tmp_path / "tps.json")
    extended = filing_questions.status("tps", _tps_case(world, "EL SALVADOR"), today)
    assert "closed" not in extended and extended["questions"] and tps.window(filing_questions.graph_for("tps", world / "case-tps", today), today)["kind"] == "open"


# -- 6. a quality grade that follows its own measures --------------------------------------------------------------------------

CREOLE = ["REPIBLIK AYITI", "ACT NESANS", "Non timoun nan: Maryz Egzanp", "Dat li fèt: senk me mil nèf san swasant-dis", "Kote li fèt: Okay, Sid",
          "Papa: Jan Egzanp, kiltivatè, ki rete Okay", "Manman: Anèz Egzanp, machann, ki rete Okay", "Temwen: Pyè Egzanp ak Mari Dènis",
          "Ofisye eta sivil la siyen ak mete sèl li la a", "Yo anrejistre l nan gwo liv biwo a pou tout moun ka konnen"]


def _quality_case(tmp_path, own_text: bool):
    """A made-up Haitian Creole birth record the reader does not recognize (unclassified), as a PDF with a text layer of its own, or as a
    scan (the page's picture alone, its words read by OCR)."""
    from types import SimpleNamespace

    import documents
    import pypdfium2 as pdfium
    from classify.classifier import readability
    from portal.demo import document_pdf

    pdf = document_pdf(CREOLE)
    name = "kreyol.pdf"
    if own_text:
        (tmp_path / name).write_bytes(pdf)
    else:
        doc = pdfium.PdfDocument(pdf)
        page = doc[0]
        page.render(scale=1000 / page.get_width()).to_pil().convert("L").save(tmp_path / name, "PDF")
        doc.close()
    text = "\n".join(CREOLE)
    assert readability(text) < 3  # the words are not ones the reader knows: the old rule called every such page blurry
    built = documents.build(tmp_path, {name: SimpleNamespace(doc_type="unclassified", confidence=0.0)}, {}, texts={name: text}, ocr=False)
    return built["documents"][0]


def test_a_text_pdf_in_a_language_the_reader_does_not_know_is_graded_by_its_measures(tmp_path):
    r = _quality_case(tmp_path, own_text=True)
    said = r["quality_measures"]["said"]
    assert said.startswith("sharp, good contrast, ") and said.endswith(" words read") and r["quality_measures"]["scan"] is False
    assert (r["quality"], r["quality_basis"]) == ("readable", "measured")  # not "Hard to read" under "sharp, good contrast"


def test_a_scan_whose_words_read_as_nonsense_stays_hard_to_read_and_says_why(tmp_path):
    r = _quality_case(tmp_path, own_text=False)
    assert r["quality_measures"]["scan"] is True and r["quality"] == "blurry"
    assert r["quality_measures"]["said"].endswith("but the words read are not ones the reader recognizes")  # the grade and its words agree


def test_a_record_made_before_the_measures_follows_them_when_the_page_has_its_own_text(tmp_path):
    import documents

    r = _quality_case(tmp_path, own_text=True)
    old = dict(r) | {"quality": "blurry", "quality_basis": "reader", "quality_measures": None}  # what the rule alone said
    documents.save(tmp_path, {"version": 1, "built": "2026-10-01T02:00:00+00:00", "documents": [old]})
    got = next(x for x in documents.load(tmp_path, measure=True)["documents"] if x["id"] == r["id"])
    assert (got["quality"], got["quality_basis"]) == ("readable", "measured")


# -- 5. the mailing toast and the client's page -------------------------------------------------------------------------------


def test_the_forms_of_a_package_are_the_main_forms_not_the_appearance_or_the_payment():
    import prefile

    manifest = {"forms": [{"short": "G-28 (I-601)"}, {"short": "I-601"}, {"short": "I-212"}, {"short": "G-1450"}, {"short": "I-912"}, {"short": "I-601"}, {"short": "I-485 Supplement A"}]}
    assert prefile.package_forms(manifest) == ["I-601", "I-212"]
    assert prefile.package_forms({}) == []


def test_the_client_reads_every_form_in_the_envelope_named_as_a_form_in_all_four_languages():
    import journey

    record = {"filing": "i601", "forms": ["I-601", "I-212"], "mailed_on": "2026-10-02"}
    said = {lang: journey._client_events({"filings": [record]}, lang)[0]["text"] for lang in ("en", "pt", "es", "ht")}
    assert said["en"] == "We mailed your application (Form I-601 and Form I-212) to USCIS on 10/02/2026."
    assert said["pt"] == "Enviamos o seu pedido (Formulário I-601 e Formulário I-212) ao USCIS em 02/10/2026."
    assert said["es"] == "Enviamos su solicitud (Formulario I-601 y Formulario I-212) a USCIS el 02/10/2026."
    assert said["ht"] == "Nou te voye demann ou an (Fòm I-601 ak Fòm I-212) bay USCIS pa lapòs le 02/10/2026."
    # a record from before the package was kept names the form properly too, and never in lower case
    old = journey._client_events({"filings": [{"filing": "i601", "mailed_on": "2026-10-02"}]}, "en")[0]["text"]
    assert "Form I-601" in old and "i601" not in old
    three = journey._form_names(["I-601", "I-212", "I-765"], journey.settings()["client_events"], "en")
    assert three == "Form I-601, Form I-212 and Form I-765"


def test_recording_the_mailing_writes_the_clients_page_at_once_not_tonight(tmp_path, monkeypatch):
    import journey
    from portal.store import PortalStore
    from review.server import ReviewApp
    from test_prefile import _case

    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": None, "pd": None, "current": None, "problems": []})
    d = _case(tmp_path)
    manifest = json.loads((d / "packet.json").read_text(encoding="utf-8"))
    manifest["forms"] = [{"id": "g28", "short": "G-28", "file": "x.pdf"}, {"id": "i485", "short": "I-485", "file": "i485_filled.pdf"}, {"id": "i131", "short": "I-131", "file": "y.pdf"}]
    (d / "packet.json").write_text(json.dumps(manifest), encoding="utf-8")
    store = PortalStore(tmp_path / "portal")
    store.add_client("c1", "Ana Exemplo", email="ana@example.com", language="pt")
    app = ReviewApp(tmp_path, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, portal_root=tmp_path / "portal")
    journey.push_client(tmp_path, tmp_path / "portal", "c1")  # the page as it stood before the mailing (the first view is set up quietly)
    assert store.journey("c1")["pt"]["happened"] == []
    done = app.set_filed("c1", {"filing": "i485", "mailed_on": journey.clock.today().isoformat(), "carrier": "USPS", "tracking": "9400100000000000000000",
                                "override": "client turns 21 tomorrow", "reviewer": "Ana Attorney"}, "attorney")
    assert done["client_page"] == "now" and done["forms"] == ["I-485", "I-131"]
    seen = {lang: store.journey("c1")[lang]["happened"][0]["text"] for lang in ("pt", "en")}
    assert "(Formulário I-485 e Formulário I-131)" in seen["pt"] and "(Form I-485 and Form I-131)" in seen["en"]
    gone = app.set_filed("c1", {"filed": False, "reviewer": "Ana Attorney"}, "attorney")
    assert gone["client_page"] == "now" and store.journey("c1")["en"]["happened"] == []


# -- 4. a hand-set language that would drop "Needs a translation" asks first -----------------------------------------------------


def _birth_case(world, language="pt"):
    from test_restricted import doc, make_case

    record = doc("b1", "birth_certificate", text="certidao de nascimento")
    d = make_case(world, "case-bia2", "Bia Exemplo Lima", [record])
    data = json.loads((d / "documents.json").read_text(encoding="utf-8"))
    data["documents"][0]["language"] = language
    (d / "documents.json").write_text(json.dumps(data), encoding="utf-8")
    return d


def _row(app, client="case-bia2"):
    return next(r for r in app.documents(client)["documents"] if r["id"] == "b1")


def _lang(app, value, **extra):
    return app.document_change("case-bia2", {"reviewer": "Ana Attorney", "id": "b1", "field": "language", "value": value} | extra, "attorney")


def test_changing_a_translated_documents_language_to_english_asks_before_it_drops_the_translation(world, app):
    _birth_case(world)
    assert _row(app)["translation"] == "needed"
    asked = _lang(app, "en")
    assert asked["ask"]["question"] == "This document was marked as needing a translation. Keep that?"
    row = _row(app)
    assert row["language"] == "pt" and row["translation"] == "needed"  # nothing changed until the person answers
    kept = _lang(app, "en", keep_translation="yes")
    row = next(r for r in kept["documents"] if r["id"] == "b1")
    assert row["language"] == "en" and row["translation"] == "needed" and row["translation_kept"]["keep"] is True and "still needs a translation" in kept["note"]
    assert row["translation_kept"]["by"] == "Ana Attorney" and row["summary"]  # the summary stays with the record
    dropped = _lang(app, "pt")  # back to Portuguese: needed on its own again, no leftover answer
    assert "translation_kept" not in next(r for r in dropped["documents"] if r["id"] == "b1")


def test_a_no_is_recorded_and_the_badge_goes_while_the_summary_stays(world, app):
    _birth_case(world)
    done = _lang(app, "en", keep_translation="no")
    row = next(r for r in done["documents"] if r["id"] == "b1")
    assert row["language"] == "en" and row["translation"] is None and row["translation_kept"] == row["translation_kept"] | {"keep": False, "language": "pt", "by": "Ana Attorney"}
    assert "no longer needs a translation" in done["note"] and row["summary"] and "Portuguese" in row["summary"]


def test_a_change_that_makes_a_translation_needed_shows_the_badge_and_asks_nothing(world, app):
    _birth_case(world, "en")
    assert _row(app).get("translation") is None
    done = _lang(app, "pt")
    assert "ask" not in done and next(r for r in done["documents"] if r["id"] == "b1")["translation"] == "needed"


def test_the_language_of_a_document_that_needs_no_translation_changes_without_a_question(world, app):
    from test_restricted import doc, make_case

    make_case(world, "case-bia2", "Bia Exemplo Lima", [doc("b1", "passport")])
    done = _lang(app, "es")
    assert "ask" not in done and next(r for r in done["documents"] if r["id"] == "b1")["language"] == "es"


def test_the_screens_carry_the_firms_own_name_and_case_review_alone_when_none_is_saved(app, server, tmp_path, monkeypatch):
    import settings

    monkeypatch.setattr(settings, "PATH", tmp_path / "s.json")
    page = call(server + "/")[1]
    assert "<title>Case Review</title>" in page and 'class="mark">CR<' in page and "Georges" not in page and "G|C" not in page
    assert json.loads(call(server + "/api/me")[1])["firm"] == "" and app.code_issuer() == "Case Review"
    (tmp_path / "s.json").write_text(json.dumps({"firm": {"values": {"firm.business_name": "Exemplo & Lima Law LLP"}}}), encoding="utf-8")
    page = call(server + "/")[1]
    assert "<title>Case Review · Exemplo &amp; Lima Law LLP</title>" in page and 'class="mark">EL<' in page and "Georges" not in page
    assert 'class="sub">Exemplo &amp; Lima Law LLP</span>' in page
    assert json.loads(call(server + "/api/me")[1])["firm"] == "Exemplo & Lima Law LLP" and app.code_issuer() == "Exemplo & Lima Law LLP Case Review"


def test_no_source_or_static_file_carries_another_firms_name():
    """The name, its mark and its address live in Settings. Comments and the recognition of real notices may name the firm; nothing a
    screen, a title or a message says may."""
    import re

    pattern = re.compile(r"Georges|GEORGES|FictionalMarkerA|FictionalMarkerA|G\|C\b")  # "G|C" in capitals only: a regular expression in the code can hold "g|c"
    allowed = ("classify/patterns.py", "extract/uscis_notice.py", "src/journey.py", "learning/synthetic.py")
    for path in [*SRC.rglob("*.py"), *SRC.rglob("*.html")]:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line) and not line.lstrip().startswith(("#", '"""')):
                assert any(a in path.as_posix() for a in allowed), f"{path.relative_to(REPO)}:{n}: {line.strip()[:120]}"


SAMPLE = ("FictionalMarkerA", "FictionalMarkerA", "DEMO-000000", "000000000000", "MARGINAL", "884-1000", "GEORGESCOTE", "Georges", "GEORGES", "Cote LLP", "COTE LLP")


def _name_only(tmp_path, monkeypatch, **more):
    """What the installer writes: the firm's name and time zone, nothing else (plus, for a firm that went on, what it saved)."""
    import settings

    values = {"firm.business_name": "Exemplo & Lima Law LLP", "office.time_zone": "America/New_York"} | more
    (tmp_path / "s.json").write_text(json.dumps({"firm": {"values": values, "updated_by": "Installer", "updated_at": "2026-10-03T08:00:00-04:00", "history": []}}), encoding="utf-8")
    monkeypatch.setattr(settings, "PATH", tmp_path / "s.json")
    return settings


def test_a_firm_that_saved_only_its_name_files_nothing_in_the_sample_attorneys_name(tmp_path, monkeypatch, world):
    import batch
    import offices
    from fill.companion import load_profile
    from factgraph import FactGraph
    from test_restricted import make_case

    settings = _name_only(tmp_path, monkeypatch)
    assert settings.identity_withheld() and not settings.details_saved()
    facts = batch.load_firm_profile(schema_path.path("firm", "firm_profile"))
    assert facts.get("firm.business_name") == "Exemplo & Lima Law LLP" and facts.get("firm.g28_attached") == "Yes"
    for key in ("firm.preparer_family_name", "firm.preparer_given_name", "firm.attorney_bar_number", "firm.uscis_online_account_number", "firm.phone",
                "firm.email", "applicant.mailing_street", "applicant.mailing_in_care_of"):
        assert key not in facts, key
    assert not any(k in load_profile()["firm"] for k in ("firm.street", "firm.city", "firm.zip"))
    main = offices.offices()[0]["values"]
    assert not any(any(x in str(v) for x in SAMPLE) for v in main.values() if v != "Exemplo & Lima Law LLP")
    case = make_case(world, "case-nameonly", "Tia Exemplo Souza", [])
    assert offices.problems(case)[0].startswith("Save the office under Settings before this packet can be built")
    letter = offices.letter({"letterhead": {"name_light": "GEORGES | ", "name_bold": "COTE LLP", "address": "x", "tagline": "t", "attorneys": "FictionalMarkerA"},
                             "signer": {"name": "Andrew G. FictionalMarkerA, Esq.", "lines": ["Tel-(617) 884-1000"]}}, case)
    assert letter["letterhead"] == {"name_light": "", "name_bold": "EXEMPLO & LIMA LAW LLP", "address": "", "tagline": "", "attorneys": ""}  # the name alone
    assert letter["signer"] == {"name": "", "lines": []}
    g = FactGraph("x")  # a case read before the firm configured the product: the sample's facts leave it too
    for key in ("firm.preparer_family_name", "firm.attorney_bar_number", "applicant.mailing_street"):
        g.add_source(key, "firm_profile.json", "firm_profile", "SAMPLE", "SAMPLE", 1.0)
    offices.apply(g, case)
    assert all(g.get(k) is None for k in ("firm.preparer_family_name", "firm.attorney_bar_number", "applicant.mailing_street"))


def test_the_letterhead_of_a_firm_with_its_details_saved_is_its_own_and_the_roster_is_what_it_lists(tmp_path, monkeypatch, world):
    import offices
    from fill import cover_letter
    from test_restricted import make_case

    _name_only(tmp_path, monkeypatch, **{"firm.preparer_family_name": "EXEMPLO", "firm.preparer_given_name": "ANA", "firm.attorney_bar_number": "123456",
                                         "firm.street": "1 EXAMPLE ST", "firm.city": "SOMERVILLE", "firm.state": "MA", "firm.zip": "02143", "firm.phone": "6175550100",
                                         "firm.email": "ana@exemplo.example", "office.signer": "Ana B. Exemplo, Esq.", "office.tagline": "Immigration law",
                                         "office.attorneys": "Ana B. Exemplo, Esq., Bia Lima, Esq."})
    case = make_case(world, "case-fullfirm", "Tia Exemplo Souza", [])
    got = offices.letter(cover_letter.load_config(), case)
    assert got["letterhead"] == {"name_light": "", "name_bold": "EXEMPLO & LIMA LAW LLP", "address": "1 EXAMPLE ST, SOMERVILLE, MA, 02143",
                                 "tagline": "Immigration law", "attorneys": "Ana B. Exemplo, Esq., Bia Lima, Esq."}
    assert got["signer"]["name"] == "Ana B. Exemplo, Esq." and "Tel-(617) 555-0100" in got["signer"]["lines"]
    assert offices.problems(case) == []
    out, doc, _ = cover_letter._letter_doc(got, False)  # the letterhead draws with a long name too
    assert doc is not None and out is not None


def test_the_portal_faq_names_the_firm_or_says_the_office_in_every_language(tmp_path, monkeypatch):
    from portal.bank import localized_faq

    import settings

    monkeypatch.setattr(settings, "PATH", tmp_path / "none.json")
    safe = lambda lang: next(f["a"] for f in localized_faq(lang) if f["q"] in ("Is my information safe?", "¿Mi información está segura?", "Minhas informações estão seguras?", "Èske enfòmasyon mwen an sekirite?"))  # noqa: E731
    assert "the office see your answers" in safe("en") and "o escritório veem" in safe("pt") and "la oficina ven" in safe("es") and "biwo a ki" in safe("ht")
    for lang in ("pt", "es", "en", "ht"):
        assert "Georges" not in safe(lang) and "{office}" not in safe(lang)
    (tmp_path / "s.json").write_text(json.dumps({"firm": {"values": {"firm.business_name": "Exemplo & Lima"}}}), encoding="utf-8")
    monkeypatch.setattr(settings, "PATH", tmp_path / "s.json")
    assert "the Exemplo & Lima office" in safe("en") and "o escritório Exemplo & Lima" in safe("pt") and "la oficina Exemplo & Lima" in safe("es") and "biwo Exemplo & Lima" in safe("ht")


def test_no_schema_or_public_document_carries_the_sample_firm_but_the_shipped_defaults_that_say_settings_override_them():
    """Verifier, fix 3: the schemas and the public documents too. The only files that may hold the sample firm's identity are the shipped
    defaults named here, each saying that Settings overrides it."""
    pattern = re.compile(r"Georges|GEORGES|FictionalMarkerA|FictionalMarkerA|MARGINAL ST|DEMO-000000|000000000000|884-1000|georgescote", re.I)
    shipped_defaults = {p.relative_to(REPO).as_posix() for p in (schema_path.path("firm", "firm_profile"), schema_path.path("packet", "companion_forms"), schema_path.path("cover_letter", "i485"))}
    for folder in (schema_path.ROOT, REPO / "docs" / "public"):
        for path in folder.rglob("*"):
            rel = path.relative_to(REPO).as_posix()
            if not path.is_file() or path.suffix not in (".json", ".md", ".html", ".txt") or path.is_relative_to(schema_path.folder("geo")):
                continue  # the gazetteer's place names ("Saint-Georges") are not a firm
            text = path.read_text(encoding="utf-8")
            if pattern.search(text):
                assert rel in shipped_defaults, rel
                assert "Settings, Main office overrides each of them" in text, rel
