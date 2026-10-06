"""The EOIR-26A (fee waiver request) from the client's own answers (src/eoir26a.py): the questions in four languages, the arithmetic, the fill of every
box, the I-912's reuse of the same answers, the typed signature, the drawn signature and the paper path with their records, the signing record
(an audit page), the attorney's attestation and the refusal while the EOIR ID is blank, and the gate on the court motion's and the appeal's packets.
Everyone here is made up ("Maria Exemplo Souza"; the EOIR ID is a made-up one a firm would type under Settings)."""

from __future__ import annotations

import base64
import io
import json
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter

import bia
import clock
import court_motion
import eoir26a
import events
import fee_waiver
import filing_questions
import packet
import settings
from factgraph import FactGraph
from portal.app import create_app
from portal.bank import clean
from portal.notify import Notifier
from portal.store import PortalStore

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import portal_english  # noqa: E402
import schema_path

REPO = Path(__file__).resolve().parent.parent

LANGS = ("pt", "es", "en", "ht")
TODAY = date(2026, 10, 5)
H = {"X-Portal": "1"}
NAME = "Maria Exemplo Souza"
BASE = {"applicant.family_name": "EXEMPLO SOUZA", "applicant.given_name": "MARIA", "applicant.dob": "1990-03-14", "applicant.a_number": "A099000777",
        "applicant.physical_street": "10 EXAMPLE STREET", "applicant.physical_city": "SPRINGFIELD", "applicant.physical_state": "MA", "applicant.physical_zip": "01103",
        "firm.preparer_given_name": "ANA", "firm.preparer_family_name": "EXEMPLO", "firm.business_name": "EXAMPLE LAW LLP", "firm.street": "1 EXAMPLE PLAZA",
        "firm.city": "BOSTON", "firm.state": "MA", "firm.zip": "02101", "firm.phone": "6175550100", "firm.email": "ANA@EXAMPLE.COM", "firm.eoir_id": "ZZ999999",
        "firm.licensing_authority": "MASSACHUSETTS", "firm.attorney_bar_number": "000000"}
# the client's own figures, as they type them in Portuguese (a decimal comma and a thousands point): income 1,850.50, expenses 2,100.75
ANSWERS = {"fw_income_work": "1.500,00", "fw_income_property": "0", "fw_income_interest": "0,50", "fw_income_other": "350",
           "fw_exp_rent": "1.200", "fw_exp_utilities": "180,25", "fw_exp_debts": "120.50", "fw_exp_living": "500", "fw_exp_other": "100"}
INCOME, EXPENSE = Decimal("1850.50"), Decimal("2100.75")


def text_of(pdf: Path, pages: list[int] | None = None) -> str:
    reader = PdfReader(str(pdf))
    return " ".join(" ".join((p.extract_text() or "") for i, p in enumerate(reader.pages) if pages is None or i in pages).split())


def fields_of(pdf: Path) -> dict[str, str]:
    return {k.rsplit(".", 1)[-1]: f.get("/V") for k, f in (PdfReader(str(pdf)).get_fields() or {}).items()}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A made-up firm: a case (a motion to the judge with a fee waiver asked), the client in the portal in Portuguese, the firm's settings and ledger."""
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "events.jsonl"))
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))
    monkeypatch.setattr(packet, "_today", lambda: TODAY)
    import journey

    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": None, "pd": None, "current": None, "problems": []})
    clients = tmp_path / "clients"
    clients.mkdir()
    portal = tmp_path / "portal"
    store = PortalStore(portal)
    store.add_client("case-maria", NAME, email="maria@example.com", language="pt")
    monkeypatch.setenv("PORTAL_DATA", str(portal))
    monkeypatch.setenv("I485_CASES", str(clients))
    w = SimpleNamespace(root=tmp_path, clients=clients, portal=portal, store=store)
    w.case = make_case(w, BASE)
    return w


def make_case(w, facts: dict, name: str = "case-maria") -> Path:
    d = w.clients / name
    d.mkdir(exist_ok=True)
    g = FactGraph(name)
    for key, value in facts.items():
        g.add_source(key, "test", "test", value, value, 1.0)
    g.save(d / "fact_graph.json")
    source = w.root / "source"
    source.mkdir(exist_ok=True)
    wr = PdfWriter()
    wr.add_blank_page(width=612, height=792)
    with open(source / "nta.pdf", "wb") as fh:
        wr.write(fh)
    (d / "meta.json").write_text(json.dumps({"source_folder": str(source), "classifications": {"nta.pdf": "notice_to_appear"}}), encoding="utf-8")
    return d


def ask_motion_waiver(w) -> None:
    filing_questions.answer("court_motion", w.case, {"ijmotion.fee_waiver": "Yes"}, "Sam")


def portal_client(w):
    app = create_app(root=w.portal, base_url="https://portal.example", secure_cookies=False, notifier=Notifier(w.portal / "outbox.jsonl", env={}))
    client = TestClient(app)
    client.get(f"/l/{w.store.new_link_token('case-maria')}", follow_redirects=False)
    return client


def answer_all(client, answers=ANSWERS):
    r = client.put("/api/answers", json=answers, headers=H)
    assert r.status_code == 200 and not r.json()["errors"], r.text
    return r.json()


def approve_and_open(w, drawn=False):
    view = eoir26a.view(w.case, w.portal, "attorney")
    eoir26a.approve_sentence(w.case, view["sentence"]["suggested"], False, "Sam Attorney", "attorney")
    eoir26a.open_signing(w.case, drawn, "Sam Attorney", "attorney", w.portal)


def png(ink=True, size=(800, 200)) -> str:
    im = Image.new("RGBA", size, (255, 255, 255, 0))
    if ink:
        d = ImageDraw.Draw(im)
        d.line([(40, 150), (200, 40), (330, 160), (520, 60), (700, 140)], fill=(10, 10, 10, 255), width=6)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# -- the questions -----------------------------------------------------------------------------------------------------------------------------


def test_an_amount_as_a_client_writes_it_is_dollars_and_cents():
    cases = {"1.500,00": "1500.00", "1,234.56": "1234.56", "$1200": "1200.00", "0": "0.00", "12,5": "12.50", "1,200": "1200.00", "1.200": "1200.00", "0,50": "0.50",
             "1.000.000": "1000000.00", " 350 ": "350.00", "99.9": "99.90"}
    for typed, want in cases.items():
        assert f"{eoir26a.parse_money(typed):.2f}" == want, typed
    for bad in ("", "abc", "-5", "12,345,6", "1.2.3,4", "1,2,3", "2000000", "1.000.001", "12 dollars", "1e3"):
        assert eoir26a.parse_money(bad) is None, bad
    assert clean({"type": "money"}, "1.500,00") == ("1500.00", "") and clean({"type": "money"}, "abc") == (None, "invalid_money")
    assert clean({"type": "money"}, "0") == ("0.00", "")  # "0" is an answer: the form says to answer every line even when it is $0.00


def test_the_section_asks_exactly_the_forms_rows_and_says_every_line_is_answered_even_when_it_is_zero():
    shipped = eoir26a.shipped()
    template = " ".join(" ".join(p.extract_text() for p in PdfReader(str(schema_path.path("template", "eoir26a"))).pages).split())
    squash = lambda s: re.sub(r"\s+", "", s)  # noqa: E731 -- the template's text has stray spaces inside words
    fields = set(fields_of(schema_path.path("template", "eoir26a")))
    assert [x["group"] for x in eoir26a.lines()] == ["income"] * 4 + ["expense"] * 5
    for line in eoir26a.lines():
        assert line["field"] in fields and squash(line["form_row"].split(" (")[0].split(",")[0]) in squash(template), line["id"]
    questions = shipped["section"]["questions"]
    assert [q["id"] for q in questions] == [x["id"] for x in eoir26a.lines()] and all(q["required"] and q["type"] == "money" for q in questions)
    for lang in LANGS:
        for q in questions:
            for part in ("label", "help", "tip"):
                assert q[part][lang].strip(), (q["id"], part, lang)  # help on every line, in every language
        for part in ("title",):
            assert shipped["section"][part][lang].strip()
        for part in ("intro", "example", "why"):
            assert shipped["section"][part][lang].strip()
    assert "$0.00" in shipped["section"]["intro"]["en"] and "type 0" in shipped["section"]["intro"]["en"]
    example = shipped["section"]["example"]["en"]
    assert round(Decimal(200) * 52 / 12, 2) == Decimal("866.67") and round(Decimal(400) * 26 / 12, 2) == Decimal("866.67") and Decimal(1200) / 12 == 100
    for said in ("200 × 52 = 10,400 a year", "10,400 ÷ 12 = 866.67 a month", "400 × 26 = 10,400", "divide by 3", "divide by 6", "$1,200 a year is $100 a month"):
        assert said in example, said


def test_the_forms_own_lines_the_product_holds_are_on_the_template_word_for_word():
    template = re.sub(r"\s+", "", " ".join(p.extract_text() for p in PdfReader(str(schema_path.path("template", "eoir26a"))).pages)).replace("’", "'").replace("“", '"').replace("”", '"')
    for line in eoir26a.shipped()["form_lines"]:
        said = re.sub(r"\s+", "", line["text"]).replace("’", "'").replace("“", '"').replace("”", '"')
        assert said in template, line["text"]
    assert eoir26a.shipped()["declaration"]["en"].startswith("I declare under penalty of perjury, pursuant to 28 U.S.C. § 1746")


def test_every_client_sentence_is_in_four_languages_with_the_same_slots_and_plain_on_screen():
    shipped = eoir26a.shipped()
    texts = [shipped["declaration"]] + [w["text"] for w in shipped["hardship"]["wordings"]]
    for q in shipped["section"]["questions"]:
        texts += [q["label"], q["help"], q["tip"]]
    texts += [shipped["section"][k] for k in ("title", "intro", "example", "why", "intro_appeal", "why_appeal")]
    for t in texts:
        assert set(t) == set(LANGS)
        slots = {lg: set(re.findall(r"\{\w+\}", t[lg])) for lg in LANGS}
        assert len({frozenset(s) for s in slots.values()}) == 1, t["en"]
        for lg in LANGS:
            assert "—" not in t[lg] and " -- " not in t[lg], t[lg]
            if lg != "en":
                assert not portal_english.english_words(re.sub(r"\{\w+\}", "", t[lg])), (lg, t[lg], portal_english.english_words(t[lg]))
    assert "MACHINE DRAFT" in shipped["_status"] and "DRAFT" in shipped["_status"]


# -- the arithmetic ---------------------------------------------------------------------------------------------------------------------------------


def test_the_totals_and_the_difference_are_the_clients_figures_added_and_a_line_not_answered_is_never_zero():
    amounts = {x["id"]: eoir26a.parse_money(ANSWERS[x["id"]]) for x in eoir26a.lines()}
    t = eoir26a.totals_of(amounts)
    assert t["income"] == INCOME and t["expense"] == EXPENSE and t["difference"] == Decimal("-250.25") and t["complete"]
    assert eoir26a.words(t) == {"income": "$1,850.50", "expense": "$2,100.75", "difference": "-$250.25"}
    partial = amounts | {"fw_exp_other": None}
    t = eoir26a.totals_of(partial)
    assert t["income"] == INCOME and t["expense"] is None and t["difference"] is None and t["missing"] == ["fw_exp_other"] and not t["complete"]
    assert eoir26a.totals_of({})["income"] is None  # nothing answered: no total, not $0.00


def test_the_item_4_sentence_follows_the_sign_of_the_difference_and_every_number_is_the_clients(world):
    g = FactGraph("x")
    for x in eoir26a.lines():
        g.add_source(x["fact"], "t", "t", "0.00", "0.00", 1.0)
    assert eoir26a.suggestion(g)["wording"] == "nothing_left"
    g2 = FactGraph("y")
    for x in eoir26a.lines():
        g2.add_source(x["fact"], "t", "t", "100.00" if x["group"] == "income" else "50.00", "100.00" if x["group"] == "income" else "50.00", 1.0)
    s = eoir26a.suggestion(g2)
    assert s["wording"] == "little_left" and "$400.00" in s["text"]["en"] and "$250.00" in s["text"]["en"] and "$150.00 is left each month" in s["text"]["en"]
    assert eoir26a.suggestion(FactGraph("z")) is None  # nothing answered: nothing suggested


# -- the fill ----------------------------------------------------------------------------------------------------------------------------------


def test_the_form_is_filled_from_the_clients_answers_every_box(world):
    ask_motion_waiver(world)
    client = portal_client(world)
    send = eoir26a.send(world.case, "Paulo Paralegal", "paralegal", world.portal)
    assert send["request"]["kind"] == "motion" and send["request"]["sent"]["by"] == "Paulo Paralegal"
    answer_all(client)
    schema = packet.for_case(packet.load_filing("court_motion"), world.case)
    assert "eoir26a" in schema["forms"]
    packet.build(world.case, {"summary": {"name": NAME}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}, "Sam", schema)
    f = fields_of(world.case / "eoir26a_filled.pdf")
    assert f["Name Last First Middle"] == "EXEMPLO SOUZA, MARIA" and f["Print name of alien filing the form"] == "MARIA EXEMPLO SOUZA" and f["Alien A Number"] == "A099000777"
    assert [f[x["field"]] for x in eoir26a.lines()] == ["1,500.00", "0.00", "0.50", "350.00", "1,200.00", "180.25", "120.50", "500.00", "100.00"]
    assert f["MonthIncome"] == "1,850.50" and f["MonthExpense"] == "2,100.75" and f["TotalTot"] == "-250.25"
    assert f["Print Name"] == "ANA EXEMPLO" and f["EOIR ID Number"] == "ZZ999999"
    assert not f.get("Information") and not f.get("AlienSigDate") and not f.get("Date")  # item 4 not approved, nothing signed, nothing attested yet


def test_a_line_the_client_has_not_answered_is_left_empty_and_the_totals_are_not_made(world):
    ask_motion_waiver(world)
    client = portal_client(world)
    eoir26a.send(world.case, "Paulo Paralegal", "paralegal", world.portal)
    client.put("/api/answers", json={k: v for k, v in ANSWERS.items() if k != "fw_exp_other"}, headers=H)
    packet.build(world.case, {"summary": {}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}, "Sam", packet.for_case(packet.load_filing("court_motion"), world.case))
    f = fields_of(world.case / "eoir26a_filled.pdf")
    assert f["IncomeEmployment"] == "1,500.00" and not f.get("ExpenseOther") and not f.get("MonthExpense") and not f.get("TotalTot") and f["MonthIncome"] == "1,850.50"
    problems = court_motion.problems(world.case, filing_questions.graph_for("court_motion", world.case, TODAY), TODAY)
    assert any("has 8 of 9 lines" in p for p in problems)


def test_the_card_shows_every_figure_with_its_source_and_date_and_the_arithmetic(world):
    ask_motion_waiver(world)
    client = portal_client(world)
    eoir26a.send(world.case, "Paulo Paralegal", "paralegal", world.portal)
    answer_all(client)
    eoir26a.set_figures(world.case, {"fw_exp_other": "140"}, "Paulo Paralegal", "paralegal", world.portal)  # a staff member's correction
    v = eoir26a.view(world.case, world.portal, "paralegal")
    sources = {f["id"]: f["source"] for f in v["figures"]}
    assert sources["fw_income_work"] == {"how": "portal", "by": "the client", "on": "2026-10-05"}
    assert sources["fw_exp_other"]["how"] == "typed" and sources["fw_exp_other"]["by"] == "Paulo Paralegal" and sources["fw_exp_other"]["on"] == "2026-10-05"
    assert v["totals"]["expense"] == "$2,140.75" and v["totals"]["difference"] == "-$290.25"
    assert v["arithmetic"][0] == "Item 1.A, total income: 1,500.00 + 0.00 + 0.50 + 350.00 = $1,850.50"
    assert v["arithmetic"][1] == "Item 2.B, total expenses: 1,200.00 + 180.25 + 120.50 + 500.00 + 140.00 = $2,140.75"
    assert v["arithmetic"][2] == "Item 3, the difference: $1,850.50 - $2,140.75 = -$290.25"
    with pytest.raises(ValueError, match="amount in dollars"):
        eoir26a.set_figures(world.case, {"fw_exp_rent": "lots"}, "Paulo Paralegal", "paralegal", world.portal)


# -- the I-912 -------------------------------------------------------------------------------------------------------------------------------------


def test_the_i912_takes_the_same_monthly_answers_and_shows_the_annual_figure_with_its_arithmetic(world):
    client = portal_client(world)
    eoir26a.send(world.case, "Paulo Paralegal", "paralegal", world.portal)
    answer_all(client)
    filing_questions.answer("i912", world.case, {"feewaiver.filing": "N-400 (citizenship)", "feewaiver.basis_income": "Yes", "feewaiver.household_size": "3",
                                                "feewaiver.earners": "1", "feewaiver.employment": "Employed", "feewaiver.changed_since_taxes": "No"}, "Sam")
    graph = filing_questions.graph_for("i912", world.case, TODAY)
    assert graph.get("feewaiver.own_income").value == "22,206.00"                    # 1,850.50 x 12
    assert graph.get("feewaiver.monthly_expenses").value == "2,100.75"
    assert "times 12 months = $22,206.00 a year" in graph.get("feewaiver.own_income").sources[-1].raw_value
    assert fee_waiver._dollars("22,206.00") == 22206
    notes = {n["title"]: n["text"] for n in fee_waiver.notes(graph, TODAY)}
    assert "$1,850.50 (their EOIR-26A answers) times 12 months = $22,206.00 a year" in notes["From the client's monthly answers"]
    status = filing_questions.status("i912", world.case, TODAY)
    asked = {q["key"] for q in status["questions"] if q["value"] is None}
    assert "feewaiver.own_income" not in asked                                       # the client is asked once
    filing_questions.answer("i912", world.case, {"feewaiver.own_income": "30000"}, "Sam")   # a person's answer still wins
    typed = filing_questions.graph_for("i912", world.case, TODAY)
    assert typed.get("feewaiver.own_income").value == "30000"
    note = {n["title"]: n["text"] for n in fee_waiver.notes(typed, TODAY)}["From the client's monthly answers"]
    assert note.startswith("The annual income on this form is the figure Sam typed: $30000.") and "$22,206.00 a year" in note and "Asked once" not in note   # the override is said, not the stale arithmetic


# -- the signing: typed, drawn, on paper -------------------------------------------------------------------------------------------------------


def test_the_client_signs_in_the_portal_in_portuguese_the_attorney_attests_and_the_packet_is_final(world):
    ask_motion_waiver(world)
    client = portal_client(world)
    assert client.get("/api/me").json()["money"] is None                           # nothing is asked until the office sends it
    eoir26a.send(world.case, "Paulo Paralegal", "paralegal", world.portal)
    me = client.get("/api/me").json()["money"]
    assert [q["id"] for q in me["section"]["questions"]] == [x["id"] for x in eoir26a.lines()] and me["section"]["title"] == "Seu dinheiro por mês"
    assert me["section"]["questions"][0]["help"].startswith("Dinheiro de trabalhar") and me["section"]["questions"][0]["tip"] and not me["complete"] and not me["open"]
    r = client.put("/api/answers", json={"fw_income_work": "muito"}, headers=H).json()
    assert r["errors"] == {"fw_income_work": "invalid_money"}
    answer_all(client)
    me = client.get("/api/me").json()["money"]
    assert me["complete"] and me["totals"] == {"income": "$1.850,50", "expense": "$2.100,75", "difference": "-$250,25"} and me["missing"] == []
    assert client.get("/api/me").json()["answers"]["fw_exp_rent"] == "1200.00"
    # the signing is the attorney's to open, and only once item 4 is decided
    with pytest.raises(ValueError, match="Approve item 4 first"):
        eoir26a.open_signing(world.case, False, "Sam Attorney", "attorney", world.portal)
    with pytest.raises(PermissionError):
        eoir26a.open_signing(world.case, False, "Paulo Paralegal", "paralegal", world.portal)
    view = eoir26a.view(world.case, world.portal, "paralegal")
    suggested = view["sentence"]["suggested"]
    assert suggested == ("My average monthly expenses ($2,100.75) are more than my average monthly income ($1,850.50). "
                         "I have nothing left each month to pay the filing fee.")
    eoir26a.edit_sentence(world.case, suggested + " I support my two children.", "Paulo Paralegal", "paralegal")
    with pytest.raises(PermissionError):
        eoir26a.approve_sentence(world.case, suggested, False, "Paulo Paralegal", "paralegal")
    eoir26a.approve_sentence(world.case, suggested, False, "Sam Attorney", "attorney")
    assert [d for d in eoir26a.read(world.case)["request"]["sentence"].items() if d[0] == "wording"] == [("wording", "expenses_exceed")]
    eoir26a.open_signing(world.case, False, "Sam Attorney", "attorney", world.portal)
    me = client.get("/api/me").json()["money"]
    assert me["open"] and not me["drawn"] and me["sentence"]["en"] == suggested
    assert me["sentence"]["own"].startswith("Minhas despesas médias por mês ($2.100,75)") and me["declaration"]["own"].startswith("Declaro, sob pena de perjúrio")
    assert me["declaration"]["en"].startswith("I declare under penalty of perjury")
    # the figures to sign are the ones on the case; they are not changed on the page now
    assert client.put("/api/answers", json={"fw_exp_rent": "1"}, headers=H).status_code == 409
    assert client.post("/api/fee-waiver-viewed", headers=H).status_code == 200
    bad = client.post("/api/fee-waiver-sign", json={"request": me["id"], "agree": False, "signature": NAME}, headers=H)
    assert bad.status_code == 400 and bad.json()["detail"] == "agree_first"
    assert client.post("/api/fee-waiver-sign", json={"request": me["id"], "agree": True, "signature": NAME, "drawing": png()}, headers=H).json()["detail"] == "drawing_not_allowed"
    r = client.post("/api/fee-waiver-sign", json={"request": me["id"], "agree": True, "signature": NAME}, headers=H)
    assert r.status_code == 200 and r.json()["money"]["signed_on"] == "2026-10-05"
    sig = eoir26a.read(world.case)["request"]["signature"]                           # on the case at once: the case is on this machine
    assert sig["how"] == "portal" and sig["typed_name"] == NAME and sig["language"] == "pt" and sig["address"] == "testclient" and sig["at"].startswith("2026-10-05T10:30")
    assert sig["covers"]["figures"]["fw_income_work"] == "1500.00" and sig["covers"]["sentence"] == suggested
    assert eoir26a.read(world.case)["request"]["viewed"].startswith("2026-10-05T10:30")
    # signing again keeps the first signature
    client.post("/api/fee-waiver-sign", json={"request": me["id"], "agree": True, "signature": "Someone Else"}, headers=H)
    assert eoir26a.read(world.case)["request"]["signature"]["typed_name"] == NAME
    # the packet is held until the attorney attests
    graph = filing_questions.graph_for("court_motion", world.case, TODAY)
    assert [p for p in eoir26a.problems(world.case, graph)] == ["The attorney has not attested to the fee waiver request (Form EOIR-26A): an attorney counter-signs it on the Fee waiver tab."]
    with pytest.raises(PermissionError):
        eoir26a.attest(world.case, "Paulo Paralegal", "Paulo Paralegal", "paralegal", world.portal)
    eoir26a.attest(world.case, "Ana B. Exemplo", "Ana Exemplo", "attorney", world.portal)
    graph = filing_questions.graph_for("court_motion", world.case, TODAY)
    assert eoir26a.problems(world.case, graph) == []
    packet.build(world.case, {"summary": {}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}, "Sam", packet.for_case(packet.load_filing("court_motion"), world.case))
    f = fields_of(world.case / "eoir26a_filled.pdf")
    assert f["AlienSigDate"] == "10/05/2026" and f["Date"] == "10/05/2026" and f["Information"] == suggested and f["EOIR ID Number"] == "ZZ999999"
    pdf = world.case / "eoir26a_filled.pdf"
    assert "/s/ MARIA EXEMPLO SOUZA" in text_of(pdf, [0]) and "/s/ Ana B. Exemplo" in text_of(pdf, [1])  # the legal name the case holds
    record = text_of(pdf, list(range(2, len(PdfReader(str(pdf)).pages))))
    for said in ("Signing record: Form EOIR-26A", "Made by Paulo Paralegal on 10/05/2026 at 10:30 AM", "Sent to the respondent's page by Paulo Paralegal",
                 "Signing opened by Sam Attorney", "Opened by the respondent: 10/05/2026 at 10:30 AM", "typed name “Maria Exemplo Souza”",
                 "from the internet address testclient", "reading the affidavit in Portuguese with the English beside it", "Income: Employment, including self-employment: $1,500.00",
                 "Total monthly income $1,850.50; total monthly expenses $2,100.75; difference -$250.25", "Item 4, as signed: “My average monthly expenses",
                 "Attested for the firm by ANA EXEMPLO (EOIR ID ZZ999999)", "counter-signed with the typed name “Ana B. Exemplo”", "it is not a signature"):
        assert said in record, said
    assert eoir26a.how_signed(sig) == "Typed name in the portal"
    rows = [r for r in events.rows(world.root / "events.jsonl", case="case-maria") if r["kind"] == "packet"]
    assert [r["action"] for r in rows if r["action"] in ("sent", "approved", "opened", "viewed", "signed", "attested")] == ["sent", "approved", "opened", "viewed", "signed", "attested"]
    assert all("1,500" not in r["what"] and "Maria" not in r["what"] for r in rows)    # a ledger row never holds a value
    portal_rows = [r for r in events.rows(world.root / "events.jsonl", case="case-maria") if r["kind"] == "portal"]
    assert {"The client signed the fee waiver request on their page", "The client opened the fee waiver request"} <= {r["what"] for r in portal_rows}


def test_a_drawn_signature_the_attorney_allowed_is_kept_as_an_image_and_placed_in_the_box(world):
    ask_motion_waiver(world)
    client = portal_client(world)
    eoir26a.send(world.case, "Paulo Paralegal", "paralegal", world.portal)
    answer_all(client)
    approve_and_open(world, drawn=True)
    me = client.get("/api/me").json()["money"]
    assert me["drawn"]
    for bad, code in (("data:image/png;base64,AAAA", "drawing_invalid"), ("data:text/html;base64,AAAA", "drawing_invalid"), (png(ink=False), "drawing_blank"),
                      ("data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 400_000).decode(), "drawing_invalid")):
        r = client.post("/api/fee-waiver-sign", json={"request": me["id"], "agree": True, "signature": NAME, "drawing": bad}, headers=H)
        assert r.status_code == 400 and r.json()["detail"] == code, (code, r.text[:200])
    assert not (world.portal / "clients" / "case-maria" / "fee_waiver").exists()       # nothing is kept from a refused drawing
    r = client.post("/api/fee-waiver-sign", json={"request": me["id"], "agree": True, "signature": NAME, "drawing": png()}, headers=H)
    assert r.status_code == 200
    sig = eoir26a.read(world.case)["request"]["signature"]
    assert sig["drawn"]["file"].endswith(".png") and len(sig["drawn"]["sha256"]) == 64 and eoir26a.how_signed(sig) == "Typed name and drawn signature in the portal"
    assert (world.case / "eoir26a" / sig["drawn"]["file"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert Image.open(io.BytesIO((world.case / "eoir26a" / sig["drawn"]["file"]).read_bytes())).mode == "RGB"   # flattened on white, re-saved: nothing else of the upload is kept
    kept = [world.case / "eoir26a" / sig["drawn"]["file"], world.portal / "clients" / "case-maria" / "fee_waiver" / sig["drawn"]["file"]]
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in kept)                    # the owner only, as the roster and the vault are
    again = client.post("/api/fee-waiver-sign", json={"request": me["id"], "agree": True, "signature": NAME}, headers=H)
    assert again.status_code == 400 and again.json()["detail"] == "fee_waiver_unknown"   # a second tap: a code the page words in the client's language
    eoir26a.attest(world.case, "Ana B. Exemplo", "Ana Exemplo", "attorney", world.portal)
    packet.build(world.case, {"summary": {}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}, "Sam", packet.for_case(packet.load_filing("court_motion"), world.case))
    reader = PdfReader(str(world.case / "eoir26a_filled.pdf"))
    assert list(reader.pages[0].images), "the drawn signature is on the form's first page"
    assert any(list(p.images) for p in reader.pages[2:]) and "A signature drawn on the phone is on the form and below" in text_of(world.case / "eoir26a_filled.pdf")


def test_the_form_signed_on_paper_is_the_scan_with_the_record_after_it(world):
    ask_motion_waiver(world)
    client = portal_client(world)
    eoir26a.send(world.case, "Paulo Paralegal", "paralegal", world.portal)
    answer_all(client)
    scan = PdfWriter()
    scan.add_blank_page(width=612, height=792)
    scan.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    scan.write(buf)
    with pytest.raises(ValueError, match="Approve item 4 first"):
        eoir26a.paper(world.case, buf.getvalue(), "2026-10-04", "Paulo Paralegal", "paralegal", world.portal)
    eoir26a.approve_sentence(world.case, "", True, "Sam Attorney", "attorney")      # item 4 approved empty
    with pytest.raises(ValueError, match="future"):
        eoir26a.paper(world.case, buf.getvalue(), "2026-10-09", "Paulo Paralegal", "paralegal", world.portal)
    with pytest.raises(ValueError, match="PDF, JPG or PNG"):
        eoir26a.paper(world.case, b"not a scan", "2026-10-04", "Paulo Paralegal", "paralegal", world.portal)
    out = eoir26a.paper(world.case, buf.getvalue(), "2026-10-04", "Paulo Paralegal", "paralegal", world.portal)
    sig = out["signature"]
    assert sig["how"] == "paper" and sig["words"] == "Signed on paper (the scan is what is filed)" and out["sentence"]["approved"]["state"] == "blank"
    saved = eoir26a.read(world.case)["request"]["signature"]
    assert saved["on"] == "2026-10-04" and saved["by"] == "Paulo Paralegal" and len(saved["sha256"]) == 64
    assert client.get("/api/me").json()["money"]["signed_on"] == "2026-10-04"          # the client's page stops asking
    with pytest.raises(ValueError, match="confirm “I signed and dated page 2 of the paper form”"):
        eoir26a.attest(world.case, "Ana B. Exemplo", "Ana Exemplo", "attorney", world.portal)           # a paper scan carries nothing of ours: the attorney confirms they signed it
    assert eoir26a.problems(world.case, filing_questions.graph_for("court_motion", world.case, TODAY))[-1] .startswith("The attorney has not attested")
    out = eoir26a.attest(world.case, "Ana B. Exemplo", "Ana Exemplo", "attorney", world.portal, paper_page_2=True)
    assert out["attestation"]["paper_page_2"]["confirmed"] == "I signed and dated page 2 of the paper form"
    printed = eoir26a.pdf_path(world.case, portal_root=world.portal)
    assert "The attorney confirmed on 10/05/2026 at 10:30 AM: “I signed and dated page 2 of the paper form”" in text_of(printed)
    assert eoir26a.read(world.case)["request"]["attestation"]["paper_page_2"]["at"].startswith("2026-10-05T10:30")
    packet.build(world.case, {"summary": {}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}, "Sam", packet.for_case(packet.load_filing("court_motion"), world.case))
    pages = PdfReader(str(world.case / "eoir26a_filled.pdf")).pages
    assert len(pages) >= 3 and not (pages[0].extract_text() or "").strip()             # the signed scan as it came (blank here), then the record
    assert "Signed on paper on 10/04/2026" in text_of(world.case / "eoir26a_filled.pdf") and "Item 4, as signed: left empty." in text_of(world.case / "eoir26a_filled.pdf")


# -- the attestation and the gate ------------------------------------------------------------------------------------------------------------------


def test_the_attestation_is_refused_while_the_eoir_id_is_blank_and_the_id_is_never_in_the_code(world):
    facts = {k: v for k, v in BASE.items() if k != "firm.eoir_id"}
    make_case(world, facts)
    ask_motion_waiver(world)
    client = portal_client(world)
    eoir26a.send(world.case, "Paulo Paralegal", "paralegal", world.portal)
    answer_all(client)
    approve_and_open(world)
    me = client.get("/api/me").json()["money"]
    client.post("/api/fee-waiver-sign", json={"request": me["id"], "agree": True, "signature": NAME}, headers=H)
    with pytest.raises(ValueError) as why:
        eoir26a.attest(world.case, "Ana B. Exemplo", "Ana Exemplo", "attorney", world.portal)
    assert str(why.value) == "Type the attorney's EOIR ID under Settings."
    assert not eoir26a.read(world.case)["request"]["attestation"]
    assert eoir26a.view(world.case, world.portal, "attorney")["attorney"] == {"name": "ANA EXEMPLO", "eoir_id_set": False, "eoir_id": None}
    settings_text = (REPO / "src" / "eoir26a.py").read_text(encoding="utf-8") + (schema_path.path("form_text", "eoir26a")).read_text(encoding="utf-8")
    assert "ZZ999999" not in settings_text and not re.search(r"\b[A-Z]{2}\d{6}\b", settings_text)   # the office types it under Settings


def test_figures_that_change_after_the_client_signed_void_the_signature_until_an_attorney_opens_it_again(world):
    ask_motion_waiver(world)
    client = portal_client(world)
    eoir26a.send(world.case, "Paulo Paralegal", "paralegal", world.portal)
    answer_all(client)
    approve_and_open(world)
    me = client.get("/api/me").json()["money"]
    client.post("/api/fee-waiver-sign", json={"request": me["id"], "agree": True, "signature": NAME}, headers=H)
    eoir26a.attest(world.case, "Ana B. Exemplo", "Ana Exemplo", "attorney", world.portal)
    assert eoir26a.problems(world.case, filing_questions.graph_for("court_motion", world.case, TODAY)) == []
    eoir26a.set_figures(world.case, {"fw_exp_rent": "1300"}, "Paulo Paralegal", "paralegal", world.portal)
    problems = eoir26a.problems(world.case, filing_questions.graph_for("court_motion", world.case, TODAY))
    assert problems == [eoir26a.STALE_ITEM4, "The figures or item 4 of the fee waiver request changed after the client signed it: an attorney voids the signature and the client signs again."]
    packet.build(world.case, {"summary": {}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}, "Sam", packet.for_case(packet.load_filing("court_motion"), world.case))
    assert "/s/ Maria" not in text_of(world.case / "eoir26a_filled.pdf") and not fields_of(world.case / "eoir26a_filled.pdf").get("AlienSigDate")   # a stale signature is not placed
    with pytest.raises(ValueError, match="signs first"):
        eoir26a.attest(world.case, "Ana B. Exemplo", "Ana Exemplo", "attorney", world.portal)
    with pytest.raises(PermissionError):
        eoir26a.reopen(world.case, "figures changed", "Paulo Paralegal", "paralegal", world.portal)
    with pytest.raises(ValueError, match="Say why"):
        eoir26a.reopen(world.case, " ", "Sam Attorney", "attorney", world.portal)
    eoir26a.reopen(world.case, "The rent was corrected by the office.", "Sam Attorney", "attorney", world.portal)
    rec = eoir26a.read(world.case)
    assert rec["history"][0]["voided"]["why"] == "The rent was corrected by the office." and rec["history"][0]["signature"]["typed_name"] == NAME and not rec["request"]["signature"]
    me = client.get("/api/me").json()["money"]
    assert not me["open"] and not me["locked"] and me["signed_on"] is None            # the client's page is open to answers again


def test_the_gate_names_each_missing_step_and_asks_nothing_when_no_waiver_is_asked(world):
    graph = filing_questions.graph_for("court_motion", world.case, TODAY)
    assert eoir26a.problems(world.case, graph) == [] and eoir26a.asked_by(graph) is None
    ask_motion_waiver(world)
    got = court_motion.problems(world.case, filing_questions.graph_for("court_motion", world.case, TODAY), TODAY)
    mine = [p for p in got if "Form EOIR-26A" in p]
    assert mine == ["The fee waiver request (Form EOIR-26A) has 0 of 9 lines of monthly income and expenses: every line is answered, $0.00 where it is nothing. "
                    "Send the questions to the client on the Fee waiver tab, or type the figures there.",
                    "The client has not signed the fee waiver request (Form EOIR-26A): the portal, or the signed paper scan, on the Fee waiver tab."]
    assert "motion" in (eoir26a.view(world.case, world.portal, "paralegal")["steps"][0]["detail"] or "").lower()


def test_an_appeal_to_the_board_carries_the_form_when_the_attorney_asks_for_the_waiver(world):
    facts = BASE | {"bia.decision_type": "Merits (removal, asylum, etc.)", "bia.decision_date": "2026-10-01", "bia.asylum": "No", "bia.detained": "Not detained",
                    "bia.last_hearing": "Boston Immigration Court", "bia.reasons": "THE REASONS.", "bia.oral_argument": "No", "bia.brief": "Yes",
                    "eoir.dhs_address": "OPLA BOSTON, 1 EXAMPLE WAY, BOSTON, MA 02101", "eoir.primary": "Primary", "eoir.pro_bono": "No"}
    make_case(world, facts)
    assert bia.forms_for(filing_questions.graph_for("bia", world.case, TODAY), ["eoir27", "eoir26"]) == ["eoir27", "eoir26"]
    filing_questions.answer("bia", world.case, {"bia.fee_waiver": "Yes"}, "Sam")
    graph = filing_questions.graph_for("bia", world.case, TODAY)
    assert eoir26a.asked_by(graph) == "appeal" and bia.forms_for(graph, ["eoir27", "eoir26"]) == ["eoir27", "eoir26", "eoir26a"]
    assert any("Form EOIR-26A, is in this packet" in n["text"] for n in bia.notes(graph, TODAY))
    assert any("has 0 of 9 lines" in p for p in bia.problems(world.case, graph, TODAY))
    client = portal_client(world)
    eoir26a.send(world.case, "Paulo Paralegal", "paralegal", world.portal)
    assert eoir26a.read(world.case)["request"]["kind"] == "appeal"
    answer_all(client)
    schema = packet.for_case(packet.load_filing("bia"), world.case)
    assert schema["forms"] == ["eoir27", "eoir26", "eoir26a"]
    packet.build(world.case, {"summary": {}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}, "Sam", schema)
    assert fields_of(world.case / "eoir26a_filled.pdf")["MonthIncome"] == "1,850.50"
    manifest = json.loads((world.case / "packet_bia.json").read_text(encoding="utf-8"))
    assert [s["tab"] for s in manifest["sections"]][:3] == ["EOIR-27", "EOIR-26", "EOIR-26A"]


def test_the_portal_offers_the_section_after_the_questionnaire_is_sent_and_never_before_the_office_sends_it(world):
    client = portal_client(world)
    world.store.update_profile("case-maria", status="submitted")
    assert client.put("/api/answers", json={"fw_income_work": "100"}, headers=H).status_code == 409  # not asked, and the questionnaire is closed
    ask_motion_waiver(world)
    eoir26a.send(world.case, "Paulo Paralegal", "paralegal", world.portal)
    r = client.put("/api/answers", json={"fw_income_work": "100"}, headers=H)
    assert r.status_code == 200 and r.json()["answers"]["fw_income_work"] == "100.00"          # asked now, though the questionnaire was sent long ago
    assert client.put("/api/answers", json={"given_name": "X"}, headers=H).status_code == 409   # the questionnaire itself is still closed
    with pytest.raises(ValueError, match="already"):
        eoir26a.send(world.case, "Paulo Paralegal", "paralegal", world.portal)
    assert not [m for m in r.json()["missing"] if m.startswith("fw_")]                       # the line is not part of the questionnaire's own count


def test_a_client_with_no_portal_has_the_figures_typed_and_signs_on_paper(world):
    (world.portal / "clients" / "case-maria" / "profile.json").unlink()
    ask_motion_waiver(world)
    with pytest.raises(LookupError, match="not in the portal"):
        eoir26a.send(world.case, "Paulo Paralegal", "paralegal", world.portal)
    eoir26a.set_figures(world.case, {k: v for k, v in ANSWERS.items()}, "Paulo Paralegal", "paralegal", world.portal)
    v = eoir26a.view(world.case, world.portal, "paralegal")
    assert v["complete"] and not v["in_portal"] and v["totals"]["difference"] == "-$250.25"
    eoir26a.approve_sentence(world.case, "", True, "Sam Attorney", "attorney")
    with pytest.raises(LookupError, match="not in the portal"):
        eoir26a.open_signing(world.case, False, "Sam Attorney", "attorney", world.portal)    # the portal's signing needs the portal: paper is the way
    scan = PdfWriter()
    scan.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    scan.write(buf)
    eoir26a.paper(world.case, buf.getvalue(), "2026-10-05", "Paulo Paralegal", "paralegal", world.portal)
    eoir26a.attest(world.case, "Ana B. Exemplo", "Ana Exemplo", "attorney", world.portal, paper_page_2=True)
    assert eoir26a.problems(world.case, filing_questions.graph_for("court_motion", world.case, TODAY)) == []


# -- the page, the screens, the catalog and the register -----------------------------------------------------------------------------------------


def test_the_portal_page_speaks_the_money_card_in_every_language_in_words_of_that_language():
    page = (REPO / "src" / "portal" / "static" / "portal.html").read_text(encoding="utf-8")
    for start, end in (("const UI_MONEY = {", "const UI_MONEY_ERRORS"), ("const UI_MONEY_ERRORS = {", "for (const lg of Object.keys(UI_MONEY))")):
        block = page[page.index(start):page.index(end)]
        parts = re.split(r"\n  (\w\w): \{", block)
        by_lang = dict(zip(parts[1::2], parts[2::2]))
        assert tuple(by_lang) == LANGS
        keys = {lg: re.findall(r"\b([a-z_0-9]+): [`\"(]", body) for lg, body in by_lang.items()}
        assert len(keys["en"]) >= 6 and all(set(keys[lg]) == set(keys["en"]) for lg in LANGS), (start, {lg: set(keys["en"]) ^ set(keys[lg]) for lg in LANGS})
        for lg in ("pt", "es", "ht"):
            for text in re.findall(r": [`\"]([^`\"]*)[`\"]", by_lang[lg]):
                assert not portal_english.english_words(re.sub(r"\$\{[^}]*\}", "", text)), (lg, text)
                assert "—" not in text and " -- " not in text
    for lg in LANGS:
        for word in ("invalid_money", "fee_waiver_locked", "drawing_invalid", "drawing_blank", "drawing_not_allowed", "money_incomplete"):
            assert word in page


def test_the_cases_files_and_the_ledger_are_what_the_catalog_says(world):
    import records

    ask_motion_waiver(world)
    client = portal_client(world)
    eoir26a.send(world.case, "Paulo Paralegal", "paralegal", world.portal)
    answer_all(client)
    approve_and_open(world, drawn=True)
    me = client.get("/api/me").json()["money"]
    client.post("/api/fee-waiver-sign", json={"request": me["id"], "agree": True, "signature": NAME, "drawing": png()}, headers=H)
    eoir26a.attest(world.case, "Ana B. Exemplo", "Ana Exemplo", "attorney", world.portal)
    record = json.loads((world.case / "eoir26a.json").read_text(encoding="utf-8"))
    ours = {name for name, *_ in records.by_id("eoir26a")["fields"]}
    assert set(record) <= {"version", "request", "history"} and set(record["request"]) <= {n.split(".")[1] for n in ours if n.startswith("request.")}
    assert set(record["request"]["signature"]) <= {"how", "typed_name", "case_name", "at", "address", "language", "covers", "drawn"}
    portal = json.loads((world.portal / "clients" / "case-maria" / "fee_waiver.json").read_text(encoding="utf-8"))
    assert set(portal) <= {"request", "signing", "viewed", "signed", "name_refused"} and set(portal["signed"]) <= {"request", "how", "typed_name", "case_name", "at", "address", "language", "covers", "drawn"}
    import fnmatch

    def listed(base: Path, area: str) -> set[str]:
        held = {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()}
        patterns = records.patterns(area, exported_only=False)
        return {h for h in held if not any(len(p.split("/")) == len(h.split("/")) and all(fnmatch.fnmatchcase(a, b) for a, b in zip(h.split("/"), p.split("/"))) for p in patterns)}

    case_strays = listed(world.case, "case") - {"source/nta.pdf"}
    assert not [s for s in case_strays if s.startswith("eoir26a")], case_strays
    assert not [s for s in listed(world.portal / "clients" / "case-maria", "portal") if s.startswith("fee_waiver")]
    ledger = [r for r in events.rows(world.root / "events.jsonl", case="case-maria")]
    assert {r["kind"] for r in ledger} <= set(events.KINDS) and all(set(r) == set(events.FIELDS + events.CHAIN) for r in ledger)


def test_the_review_app_serves_the_card_and_the_changes_with_who_made_them(world, tmp_path):
    from review.server import CASE_GET, CASE_POST, ReviewApp

    assert {"/api/eoir26a", "/api/eoir26a.pdf"} <= CASE_GET and {"/api/eoir26a", "/api/eoir26a-paper"} <= CASE_POST   # gated like every route that opens a case
    app = ReviewApp(world.clients, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, portal_root=world.portal)
    ask_motion_waiver(world)
    v = app.eoir26a_view("case-maria", {"role": "paralegal"})
    assert v["asked"] == "motion" and not v["complete"] and v["can_approve"] is False and v["in_portal"]
    out = app.eoir26a_change("case-maria", {"action": "send", "reviewer": "Paulo Paralegal"}, "paralegal")
    assert out["request"]["sent"]["by"] == "Paulo Paralegal"
    with pytest.raises(ValueError, match="Enter your name first"):
        app.eoir26a_change("case-maria", {"action": "figures", "values": {"fw_exp_rent": "1"}}, "paralegal")
    with pytest.raises(PermissionError):
        app.eoir26a_change("case-maria", {"action": "open", "reviewer": "Paulo Paralegal"}, "paralegal")
    with pytest.raises(ValueError, match="Unknown change"):
        app.eoir26a_change("case-maria", {"action": "nothing", "reviewer": "Paulo Paralegal"}, "paralegal")
    app.eoir26a_change("case-maria", {"action": "figures", "values": ANSWERS, "reviewer": "Paulo Paralegal"}, "paralegal")
    pdf = app.eoir26a_pdf("case-maria")
    assert fields_of(pdf)["MonthIncome"] == "1,850.50"
    with pytest.raises(LookupError):
        app.eoir26a_pdf("case-maria", paper_copy=True)
    with pytest.raises(LookupError):
        app.eoir26a_view("nobody-here")


def test_the_registers_hold_the_form_s_revision_and_its_lines_read_on_10_03_2026():
    register = json.loads((schema_path.path("register", "maintenance")).read_text(encoding="utf-8").replace("\r\n", "\n"))
    items = {i["id"]: i for i in register["items"]} if "items" in register else {i["id"]: i for k, v in register.items() if isinstance(v, list) for i in v if isinstance(i, dict)}
    for key in ("form_eoir26a", "eoir26a_signature_lines"):
        item = items[key]
        assert item["party"] == "provider" and item["source"] == "https://www.justice.gov/eoir/eoir-forms" and item["last_checked"] == "2026-10-03"
        assert item["check"]["type"] == "manual" and "Revised Aug. 2022" in item["check"]["note"]
    assert items["eoir26a_signature_lines"]["cadence"] == "quarterly"
    text = (schema_path.path("register", "maintenance")).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n" == text   # byte for byte on a re-dump
    companion = (schema_path.path("packet", "companion_forms")).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert json.dumps(json.loads(companion), indent=2, ensure_ascii=False) + "\n" == companion
    shipped = (schema_path.path("form_text", "eoir26a")).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert json.loads(shipped) == eoir26a.shipped() and eoir26a.shipped()["form"]["edition"] == "Rev. Aug. 2022" and eoir26a.shipped()["form"]["read"] == "2026-10-03"


def test_every_client_sentence_is_listed_for_the_attorney_word_for_word():
    review = (REPO / "docs" / "attorney_review.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    shipped = eoir26a.shipped()
    texts = [shipped["declaration"]] + [w["text"] for w in shipped["hardship"]["wordings"]] + [shipped["section"][k] for k in ("title", "intro", "example", "why")]
    for q in shipped["section"]["questions"]:
        texts += [q["label"], q["help"], q["tip"]]
    for t in texts:
        for lg in LANGS:
            assert t[lg] in review, (lg, t[lg][:60])
    for line in shipped["form_lines"]:
        assert line["text"] in review, line["text"][:60]
    page = (REPO / "src" / "portal" / "static" / "portal.html").read_text(encoding="utf-8")
    block = page[page.index("const UI_MONEY = {"):page.index("const UI_MONEY_ERRORS")]
    for text in re.findall(r"money_(?:title|open|waiting_office|sign|thanks|clear|draw): \"([^\"]*)\"", block):
        assert text in review, text
    assert "Which signature the court accepts is yours to decide" in review and "DRAFT" in review.split("## The fee waiver request, Form EOIR-26A")[1]
    for key in ("intro_appeal", "why_appeal"):
        for lg in LANGS:
            assert shipped["section"][key][lg] in review, (key, lg)
    assert "fee_waiver_unknown" in review and "I signed and dated page 2 of the paper form" in review


# -- after verification: item 4 beside its figures, the paper form's attorney, the appeal's wording, the I-912's note ---------------------------------


def _item4_ready(world):
    ask_motion_waiver(world)
    client = portal_client(world)
    eoir26a.send(world.case, "Paulo Paralegal", "paralegal", world.portal)
    answer_all(client)
    eoir26a.approve_sentence(world.case, eoir26a.view(world.case, world.portal, "attorney")["sentence"]["suggested"], False, "Sam Attorney", "attorney")
    return client


def test_item_4_approved_beside_figures_the_client_then_corrects_is_approved_again_before_the_signing_opens(world):
    client = _item4_ready(world)
    client.put("/api/answers", json={"fw_exp_rent": "1500"}, headers=H)                 # allowed: the signing is not open yet; expenses become $2,400.75
    v = eoir26a.view(world.case, world.portal, "attorney")
    assert v["totals"]["expense"] == "$2,400.75" and v["sentence"]["stale"] and not v["steps"][2]["done"] and v["steps"][2]["detail"] == eoir26a.STALE_ITEM4
    assert eoir26a.STALE_ITEM4 in v["problems"] and "$2,100.75" in v["sentence"]["approved"]["text"]
    with pytest.raises(ValueError, match="The figures changed after item 4 was approved: an attorney approves item 4 again."):
        eoir26a.open_signing(world.case, False, "Sam Attorney", "attorney", world.portal)
    eoir26a.approve_sentence(world.case, v["sentence"]["suggested"], False, "Sam Attorney", "attorney")      # approved again, beside the new figures
    assert eoir26a.STALE_ITEM4 not in eoir26a.view(world.case, world.portal, "attorney")["problems"]
    eoir26a.open_signing(world.case, False, "Sam Attorney", "attorney", world.portal)
    me = client.get("/api/me").json()["money"]
    assert me["sentence"]["own"].startswith("Minhas despesas médias por mês ($2.400,75)")                      # the Portuguese client reads it in Portuguese again
    client.post("/api/fee-waiver-sign", json={"request": me["id"], "agree": True, "signature": NAME}, headers=H)
    eoir26a.attest(world.case, "Ana B. Exemplo", "Ana Exemplo", "attorney", world.portal)
    assert eoir26a.problems(world.case, filing_questions.graph_for("court_motion", world.case, TODAY)) == []
    packet.build(world.case, {"summary": {}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}, "Sam", packet.for_case(packet.load_filing("court_motion"), world.case))
    f = fields_of(world.case / "eoir26a_filled.pdf")
    assert f["MonthExpense"] == "2,400.75" and "$2,400.75" in f["Information"] and "$2,100.75" not in f["Information"]   # items 1 to 3 and item 4 say the same


def test_a_staff_correction_after_item_4_is_approved_holds_the_paper_path_too_and_an_empty_item_4_never_goes_stale(world):
    _item4_ready(world)
    eoir26a.set_figures(world.case, {"fw_exp_rent": "1201"}, "Paulo Paralegal", "paralegal", world.portal)
    assert eoir26a.STALE_ITEM4 in eoir26a.problems(world.case, filing_questions.graph_for("court_motion", world.case, TODAY))
    scan = PdfWriter()
    scan.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    scan.write(buf)
    with pytest.raises(ValueError, match="approves item 4 again"):
        eoir26a.paper(world.case, buf.getvalue(), "2026-10-04", "Paulo Paralegal", "paralegal", world.portal)
    eoir26a.approve_sentence(world.case, "", True, "Sam Attorney", "attorney")                                 # approved empty: it names no totals
    eoir26a.set_figures(world.case, {"fw_exp_rent": "1202"}, "Paulo Paralegal", "paralegal", world.portal)
    assert eoir26a.STALE_ITEM4 not in eoir26a.problems(world.case, filing_questions.graph_for("court_motion", world.case, TODAY))


def test_the_form_the_office_prints_for_paper_has_the_attorneys_printed_name(world):
    ask_motion_waiver(world)
    eoir26a.set_figures(world.case, ANSWERS, "Paulo Paralegal", "paralegal", world.portal)
    f = fields_of(eoir26a.pdf_path(world.case, portal_root=world.portal))
    assert f["Print Name"] == "ANA EXEMPLO" and f["EOIR ID Number"] == "ZZ999999" and not f.get("Date")        # the attorney dates page 2 by hand
    said = json.loads((schema_path.path("packet", "companion_forms")).read_text(encoding="utf-8"))["forms"]["eoir26a"]["attorney_completes"]
    assert "AND the attorney sign and date page 2 by hand" in " ".join(said)                                    # the form's own line: the attorney "must complete, sign, and date"


def test_the_clients_words_name_the_board_on_an_appeal_and_the_judge_on_a_motion_in_every_language(world):
    shipped = eoir26a.shipped()["section"]
    for kind, word in (("appeal", {"pt": "Conselho", "es": "Junta", "en": "Board", "ht": "Komisyon"}), ("motion", {"pt": "juiz", "es": "juez", "en": "judge", "ht": "jij"})):
        for lg in LANGS:
            world.store.update_profile("case-maria", language=lg)
            world.store.save_fee_waiver("case-maria", {"request": {"id": "F1", "at": "2026-10-05T10:30:00-04:00", "kind": kind}})
            section = eoir26a.client_view(world.store, "case-maria", lg)["section"]
            assert word[lg] in section["why"], (kind, lg)
            if kind == "appeal":
                assert section["intro"] == shipped["intro_appeal"][lg] and "judge" not in section["why"] and "juiz" not in section["why"]
            else:
                assert section["intro"] == shipped["intro"][lg]
    for lg in ("pt", "es", "ht"):
        assert not portal_english.english_words(shipped["why_appeal"][lg]) and not portal_english.english_words(shipped["intro_appeal"][lg]), lg


# Implementation note.


def test_another_name_is_refused_on_the_phone_staff_are_told_and_the_form_prints_the_clients_legal_name(world):
    client = _item4_ready(world)
    eoir26a.open_signing(world.case, False, "Sam Attorney", "attorney", world.portal)
    me = client.get("/api/me").json()["money"]
    for other in ("Maria Outra Pessoa", "Mario Exemplo Souza", "Maria Souza"):             # another person, one letter off, a name cut short
        bad = client.post("/api/fee-waiver-sign", json={"request": me["id"], "agree": True, "signature": other}, headers=H)
        assert bad.status_code == 400 and bad.json()["detail"] == "name_not_client", other
    request = eoir26a.read(world.case)["request"]
    assert not request.get("signature") and len(request["name_refused"]) == 3              # nothing signed; each try kept by its date, never the name typed
    assert "Outra" not in json.dumps(request) and "Outra" not in (world.portal / "clients" / "case-maria" / "fee_waiver.json").read_text(encoding="utf-8")
    view = eoir26a.view(world.case, world.portal, "attorney")
    assert view["name_refused"][0] == "The client tried to sign as another name on 10/05/2026."
    # accents, capitals and the particles are set aside, as the names timeline sets them aside; the letters are not
    r = client.post("/api/fee-waiver-sign", json={"request": me["id"], "agree": True, "signature": "  maria   exemplo de souza "}, headers=H)
    assert r.status_code == 200
    sig = eoir26a.read(world.case)["request"]["signature"]
    assert sig["typed_name"] == "maria exemplo de souza" and sig["case_name"] == "MARIA EXEMPLO SOUZA"
    eoir26a.attest(world.case, "Ana B. Exemplo", "Ana Exemplo", "attorney", world.portal)
    packet.build(world.case, {"summary": {}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}, "Sam", packet.for_case(packet.load_filing("court_motion"), world.case))
    pdf = world.case / "eoir26a_filled.pdf"
    assert "/s/ MARIA EXEMPLO SOUZA" in text_of(pdf, [0]) and "maria exemplo de souza" not in text_of(pdf, [0])  # the box carries the legal name only
    record = text_of(pdf, list(range(2, len(PdfReader(str(pdf)).pages))))
    assert "typed name “maria exemplo de souza” (the name the case holds: “MARIA EXEMPLO SOUZA”)" in record
    assert "The client tried to sign the fee waiver request as another name: refused" in {r["what"] for r in events.rows(world.root / "events.jsonl", case="case-maria")}


def test_the_refusal_is_said_in_the_clients_four_languages_and_listed_for_the_attorney():
    page = (REPO / "src" / "portal" / "static" / "portal.html").read_text(encoding="utf-8")
    block = page[page.index("const UI_MONEY_ERRORS"):]
    said = re.findall(r'name_not_client: "([^"]*)"', block)
    assert len(said) == 4
    review = (REPO / "docs" / "attorney_review.md").read_text(encoding="utf-8")
    for text in said:
        assert text in review, text
