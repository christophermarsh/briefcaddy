"""The citizenship questions in the client portal (schemas/questions/n400.json,
src/portal/bank.py load_bank("n400")) -- the same facts the review app's
Citizenship panel shows (src/naturalization.py). Answers here are CONSTRUCTED."""

from datetime import date

import naturalization
from factgraph import FactGraph
from portal.bank import YES_PREFIX, all_questions, answers_to_facts, bank_for, languages, load_bank, required_documents

N400 = load_bank(filing="n400")
LANGS = languages()  # pt, es, en, ht


def _texts(node):
    """Every client-facing text in a question: label, help, options, fields."""
    for key in ("label", "help", "title", "why"):
        if isinstance(node.get(key), dict):
            yield node[key]
    for child in node.get("options", []) + node.get("fields", []):
        if isinstance(child, dict):
            yield from _texts(child)


def test_citizenship_clients_get_the_shared_sections_then_the_n400s_own():
    ids = [s["id"] for s in N400["sections"]]
    assert ids[:6] == ["about", "addresses", "work", "marriage", "children", "biographic"]
    assert ids[6:] == ["n400_residence", "n400_character", "n400_oath", "n400_fee"]
    assert bank_for({"filing": "n400"})["sections"] == N400["sections"] and bank_for({})["sections"] == load_bank()["sections"]
    qids = [q["id"] for s in N400["sections"] for q in s["questions"]]
    assert len(qids) == len(set(qids))  # no id is used twice across the shared and the N-400 sections


def test_every_question_is_in_every_language_and_fills_a_fact_the_review_panel_knows():
    panel = {key for key, *_ in naturalization.QUESTIONS}
    for s in N400["sections"][6:]:
        for text in _texts(s):
            assert set(text) >= set(LANGS), (s["id"], text)
        for q in s["questions"]:
            for text in _texts(q):
                assert set(text) >= set(LANGS) and all(text[lang].strip() for lang in LANGS), (q["id"], text)
            facts = [q["fact"]] if q.get("fact") else list((q.get("facts") or {}).values())
            facts += [f for o in q.get("options", []) for f in o.get("facts", [])]
            for f in facts:
                assert f in panel or f.startswith("questionnaire."), (q["id"], f)
    # every Part 9 item the N-400 requires is asked in the portal, one way or another
    asked = {f for q in all_questions(N400).values() for f in ([q.get("fact")] + [x for o in q.get("options", []) for x in o.get("facts", [])]) if f}
    assert {f"n400.p9_{i}" for i in naturalization._P9_REQUIRED} <= asked


def test_answers_become_the_n400s_facts():
    answers = {"lpr_date": "2021-07-15", "trips_any": "Yes",
               "trips": [{"left": "2024-08-01", "returned": "2024-08-20", "countries": "Brasil"}, {"left": "2022-12-20", "returned": "2023-01-05", "countries": "Portugal"}],
               "n9_citizen": {"none": True}, "n9_police": {"selected": ["arrested"]},
               "n9_crimes": [{"what": "Driving without a license", "date": "2019-05-02", "place": "Framingham, MA, USA", "outcome": "Dismissed"}],
               "male_18_26": "No", "oath_31": "Yes", "want_fee_reduction": "Yes", "household_size": "4", "household_income": "52000"}
    facts = {f.fact_key: f.normalized_value for f in answers_to_facts(answers, N400)}
    assert facts["n400.lpr_date"] == "2021-07-15" and facts["n400.p9_1"] == "No" and facts["n400.p9_4"] == "No"  # none of these: No
    assert YES_PREFIX + "n400.p9_15b" in facts and "n400.p9_15b" not in facts  # ticked: blank, for the attorney
    assert facts["n400.p9_15a"] == "No" and facts["questionnaire.trip1_countries"] == "BRASIL"
    g = FactGraph("c")
    for key, value in facts.items():
        g.add_source(key, "portal questionnaire", "portal", value, value, 0.95, tier=3)
    naturalization.derive(g, date(2026, 10, 1))
    assert g.get("n400.trips").value == "08/01/2024 - 08/20/2024 BRASIL\n12/20/2022 - 01/05/2023 PORTUGAL"
    assert g.get("n400.crimes").value.startswith("DRIVING WITHOUT A LICENSE | 05/02/2019")
    assert g.get("n400.fee_reduction").value == "Yes"  # $52,000 against $132,000 for 4 people
    assert naturalization.eligibility(g, date(2026, 10, 1))["presence"]["days_outside"] == 33
    problems = " ".join(naturalization.problems(__import__("pathlib").Path("/nonexistent"), date(2026, 10, 1), graph=g, questions=[]))
    assert "the client ticked Part 9 item(s) 15b" in problems


def test_a_citizenship_client_is_invited_to_a_citizenship_questionnaire(tmp_path):
    import json

    from portal.admin import main
    from portal.notify import Notifier
    from portal.store import PortalStore

    store = PortalStore(tmp_path)
    store.add_client("c1", "Ana Exemplo", email="ana@example.com", language="es")
    main(["--root", str(tmp_path), "filing", "c1", "n400"])
    Notifier(tmp_path / "outbox.jsonl", env={}).send(store.profile("c1"), "invite", "https://portal.example/l/x")
    sent = json.loads((tmp_path / "outbox.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert "ciudadanía" in sent["subject"] and "green card" not in sent["body"]
    store.update_profile("c1", filing="i485")
    Notifier(tmp_path / "outbox.jsonl", env={}).send(store.profile("c1"), "invite", "https://portal.example/l/x")
    assert "green card" in json.loads((tmp_path / "outbox.jsonl").read_text(encoding="utf-8").splitlines()[-1])["subject"]


def test_the_documents_a_citizenship_client_is_asked_for():
    docs = {d["id"]: d["required"] for d in required_documents({"n9_police": {"selected": ["arrested"]}, "n9_crimes": [{}, {}], "want_fee_reduction": "Yes"}, N400)}
    assert docs["green_card"] and docs["court_disposition"] and docs["tax_return"] and docs["passport"] is False
    court = next(d for d in required_documents({"n9_police": {"selected": ["arrested"]}, "n9_crimes": [{}, {}]}, N400) if d["id"] == "court_disposition")
    assert court["count"] == 2  # one per case listed
