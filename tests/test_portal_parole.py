"""The humanitarian parole questions in the client portal (schemas/questions/parole.json, src/portal/bank.py load_bank("parole")):
the supporter's I-134 questions and the person's, in the portal's four languages, filling the same parole.* facts the review
app's panel shows. DRAFT wording: the Haitian Creole is a machine draft (the bank says so).
"""

from datetime import date

import parole
from factgraph import FactGraph
from filing_questions import questions
from portal.bank import all_questions, answers_to_facts, bank_for, clean, languages, load_bank, missing_required, visible

BANK = load_bank(filing="parole")
LANGS = languages()


def _texts(node):
    if isinstance(node, dict):
        if set(node) >= set(LANGS) and all(isinstance(v, str) for v in node.values()):
            yield node
        else:
            for v in node.values():
                yield from _texts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _texts(v)


def test_every_wording_is_in_all_four_languages():
    assert LANGS == ("pt", "es", "en", "ht") and bank_for({"filing": "parole"})["sections"] == BANK["sections"]
    labels = list(_texts(BANK["sections"]))
    assert len(labels) > 90
    for t in labels:
        assert all(t[lang].strip() for lang in LANGS), t
    assert "MACHINE DRAFT" in BANK["_status"] and "DRAFT" in BANK["_status"]


def test_every_answer_fills_a_fact_the_filings_panel_asks():
    asked = {key for key, *_ in questions(parole)}
    for q in all_questions(BANK).values():
        keys = ([q["fact"]] if q.get("fact") else []) + list((q.get("facts") or {}).values())
        assert keys and all(k in asked for k in keys), (q["id"], keys)
        for o in q.get("options") or []:   # a choice's value is exactly the word the filing's panel knows
            panel = next(spec for key, _l, _s, spec, _r, _w in questions(parole) if key == keys[0])
            if panel["type"] == "choice" and q["type"] == "choice":
                assert o["value"] in panel["options"], (q["id"], o["value"])


def test_an_answered_questionnaire_becomes_the_filings_facts():
    answers = {"for_whom": parole.OTHER, "count": "1", "reason": "Needs surgery.", "stay": "6 months", "visa_why": "No appointment for two years.",
               "b_relationship": "SISTER", "b_family_name": "Exemplo", "b_given_name": "Lúcia", "b_dob": "1990-05-05", "b_sex": "F", "b_marital": "Single",
               "b_birth_city": "Recife", "b_birth_country": "Brasil", "b_citizenship": "Brasil", "b_street": "Rua Exemplo 10", "b_city": "Recife", "b_country": "Brasil",
               "b_in_proceedings": "No", "s_family_name": "Exemplo Souza", "s_given_name": "Ana", "s_dob": "1985-03-14", "s_birth_city": "Recife", "s_birth_country": "Brasil",
               "s_status": "U.S. citizen", "s_relationship": "SISTER", "s_address": {"street": "10 Example St", "city": "Somerville", "state": "MA", "zip": "02143"},
               "s_same_address": "Yes", "s_phone": "6175550101", "s_employment": "Employed", "s_job": "Nurse", "s_employer": "Example Hospital", "s_income": "62000",
               "s_dependents": 2, "s_other_i134": 0, "s_contributions": "No"}
    assert missing_required(BANK, answers) == []
    for qid, value in answers.items():
        assert clean(all_questions(BANK)[qid], value)[1] == "", qid
    assert not visible(all_questions(BANK)["s_job"], {"s_employment": "Retired"}) and not visible(all_questions(BANK)["s_asset1_amount"], {})
    g = FactGraph("c")
    for f in answers_to_facts(answers, BANK):
        g.add_source(f.fact_key, "portal questionnaire", "intake_questionnaire", f.raw_value, f.normalized_value, f.confidence)
    v = lambda k: g.get(k).value  # noqa: E731
    assert v("parole.b1_family_name") == "EXEMPLO" and v("parole.b1_country_of_birth") == "BRAZIL" and v("parole.sponsor_mailing_city") == "SOMERVILLE"
    assert v("parole.sponsor_status") == "U.S. citizen" and v("parole.for_whom") == parole.OTHER and v("parole.sponsor_dependents") == "2"
    parole.derive(g, date(2026, 10, 2))
    assert parole.people(g) == [1] and parole.fee(g, date(2026, 10, 2))[0] == 630
