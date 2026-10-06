"""Drafting with provenance (src/drafting.py, docs/design_plan.md Part 6): the client's declaration from their own answers.

A made-up asylum client (Ana Clara Exemplo Souza) answers two of the I-589's long questions: one typed in English by the
paralegal in the filing's questions, one typed by the client in Portuguese in the portal. The offline translator and the
grammar model are replaced by fakes: the real ones are each one call (portal/questions.translate_to_english,
drafting.smooth_with_model) and their output is not what is under test; what the draft does with it is.
"""

import json
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

import asylum
import drafting
import packet
import settings
from factgraph import FactGraph
from portal.questions import OFFICE_DOC_ID, OFFICE_DOC_TYPE
from rules import approval
import schema_path

BASE = {"applicant.family_name": "EXEMPLO SOUZA", "applicant.given_name": "ANA CLARA", "applicant.a_number": "A099000001", "applicant.dob": "1995-03-14",
        "applicant.sex": "F", "applicant.country_of_birth": "BRAZIL", "applicant.citizenship": "BRAZIL", "applicant.marital_status": "Single",
        "applicant.physical_street": "10 EXAMPLE ST", "applicant.physical_city": "SOMERVILLE", "applicant.physical_state": "MA",
        "applicant.physical_zip": "02143", "applicant.i94_arrival_date": "2026-02-10", "asylum.basis_political": "Yes", "asylum.basis_social_group": "Yes"}
PORTUGUESE = ("Em março de 2025 os homens da gangue ameaçaram minha família porque meu pai não pagou.\n"
              "Eles disseram que voltariam para me buscar.")
ENGLISH = {"Em março de 2025 os homens da gangue ameaçaram minha família porque meu pai não pagou.":
           "In March 2025 the gang men threatened my family because my father did not pay.",
           "Eles disseram que voltariam para me buscar.": "They said they would come back for me."}
FEAR = "I am afraid that the gang will kill me if I go back, because they said so in front of my mother."
ROW_DONE = {"summary": {"name": "ANA CLARA EXEMPLO SOUZA", "a_number": "A099000001", "dob": "1995-03-14"}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}


@pytest.fixture
def firm(tmp_path, monkeypatch):
    """This test's own settings and rule approvals, and a fake offline translator that counts its calls."""
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))
    calls = []

    def fake(text, lang):
        calls.append((text, lang))
        return ENGLISH.get(text, f"[en] {text}")

    import portal.questions as questions
    from classify import translate

    monkeypatch.setattr(questions, "translate_to_english", fake)
    monkeypatch.setattr(translate, "installed_language_codes", lambda: {"pt", "es", "fr"})
    return calls


@pytest.fixture
def case(tmp_path, firm):
    """A processed asylum case: the client's Portuguese reply to the office's question (as the portal engine records it, its words as
    typed and the capitals the form is filled in), and the paralegal's English answer to Part B, 1.B in the filing's questions."""
    d, source = tmp_path / "case", tmp_path / "source"
    d.mkdir()
    source.mkdir()
    g = FactGraph("t-asylum")
    for key, value in BASE.items():
        g.add_source(key, "intake.pdf", "intake_questionnaire", value, value, 0.95)
    g.add_source("asylum.b1a", "intake.pdf", "intake_questionnaire", "Yes", "Yes", 0.95)
    g.add_source("asylum.b1a_explain", OFFICE_DOC_ID, OFFICE_DOC_TYPE, PORTUGUESE, PORTUGUESE.upper(), 0.95)
    g.save(d / "fact_graph.json")
    (d / "meta.json").write_text(json.dumps({"client_id": "t-asylum", "source_folder": str(source), "classifications": {}}), encoding="utf-8")
    asylum.answer(d, {"asylum.b1b": "Yes", "asylum.b1b_explain": FEAR}, "Paulo Paralegal", "paralegal")
    return d


def _approve():
    approval.approve(drafting.PRACTICE_ID, "Ana Attorney", "attorney")


def _text(pdf: Path) -> str:
    return re.sub(r"\s+", " ", "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages))


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", text) if s.strip()]


# --- the draft -----------------------------------------------------------------------------------------------------------


def test_the_draft_is_the_clients_answers_in_the_questions_order_each_tagged(case, firm):
    d = drafting.declaration(case, "i589")
    paras = d["paragraphs"]
    # Part B, 1.A before 1.B: the order the I-589 asks them, not the order they were answered
    assert [p["id"] for p in paras] == ["asylum.b1a_explain", "asylum.b1b_explain"]
    first, second = paras
    assert first["label"].startswith("Part B, 1.A") and second["label"].startswith("Part B, 1.B")
    assert first["original"] == PORTUGUESE  # the client's words as typed, never the capitals the form is filled in
    assert (first["language"], first["language_name"], first["language_how"]) == ("pt", "Portuguese", "read from the words")
    assert first["source"]["kind"] == "portal" and "Typed by the client in the portal" in first["source"]["words"]
    assert (second["language"], second["source"]["kind"], second["source"]["by"]) == ("en", "reviewer", "Paulo Paralegal")
    assert re.fullmatch(r"\d\d/\d\d/\d{4}", second["source"]["date"]) and second["source"]["role"] == "paralegal"
    # English: the client's own words for the English answer; none yet for the Portuguese one, and nothing made up for it
    assert second["english"] == FEAR and second["english_how"] == "original"
    assert first["english"] == "" and first["english_how"] == "none" and "No English draft yet" in first["problem"]
    assert firm == []  # the translator is not called until someone asks for the English


def test_no_sentence_of_the_draft_is_not_in_an_answer(case, firm):
    drafting.make_english(case, "i589", "Paulo Paralegal")
    answers = [PORTUGUESE, FEAR]
    d = drafting.declaration(case, "i589")
    for p in d["paragraphs"]:
        for sentence in _sentences(p["original"]):
            assert any(sentence in a for a in answers), sentence
        if p["language"] == "en":  # an English answer is its own English: word for word
            assert all(any(s in a for a in answers) for s in _sentences(p["english"]))


def test_the_english_draft_is_the_machines_marked_as_such_and_made_once(case, firm):
    card = drafting.make_english(case, "i589", "Paulo Paralegal")
    first = card["paragraphs"][0]
    assert first["english"] == "\n".join(ENGLISH.values()) and first["english_how"] == "machine"
    assert first["made_by"]["who"] == "Paulo Paralegal" and "Argos" in (first["engine"] or "")
    assert [lang for _t, lang in firm] == ["pt", "pt"]  # line by line, the one translator call
    with pytest.raises(ValueError, match="already has its English"):
        drafting.make_english(case, "i589", "Paulo Paralegal")
    assert card["machine"] == 1 and card["needs_english"] == 0


def test_haitian_creole_has_no_machine_english_and_says_so(case, firm):
    g = FactGraph.load(case / "fact_graph.json")
    creole = "Mwen pa ka retounen nan peyi mwen paske gang yo te menase fanmi mwen ak pitit mwen yo."
    g.add_source("asylum.b4_explain", OFFICE_DOC_ID, OFFICE_DOC_TYPE, creole, creole.upper(), 0.95)
    g.save(case / "fact_graph.json")
    card = drafting.make_english(case, "i589", "Paulo Paralegal")
    para = next(p for p in card["paragraphs"] if p["id"] == "asylum.b4_explain")
    assert para["language"] == "ht" and para["english"] == "" and "no Haitian Creole model" in para["problem"]
    card = drafting.edit(case, "i589", "asylum.b4_explain", "I cannot go back to my country because the gangs threatened my family.", "Marta Tradutora")
    para = next(p for p in card["paragraphs"] if p["id"] == "asylum.b4_explain")
    assert para["english_how"] == "edited" and para["problem"] == ""


# --- a person's edit is a decision ---------------------------------------------------------------------------------------


def test_a_reviewer_edit_is_recorded_like_any_decision_with_the_old_and_the_new(case, firm):
    from review.learning import outcomes

    new = "I am afraid the gang will kill me if I go back. They said so in front of my mother."
    card = drafting.edit(case, "i589", "asylum.b1b_explain", new, "Ana Attorney", "attorney")
    para = next(p for p in card["paragraphs"] if p["id"] == "asylum.b1b_explain")
    assert para["english"] == new and para["english_how"] == "edited" and para["original"] == FEAR  # the client's words are kept
    assert (para["edit"]["who"], para["edit"]["role"], para["edit"]["old"], para["edit"]["new"]) == ("Ana Attorney", "attorney", FEAR, new)
    log = json.loads((case / "decisions.json").read_text(encoding="utf-8"))
    entry = log["declaration:i589:asylum.b1b_explain"]
    assert entry["reviewer"] == "Ana Attorney" and entry["old"] == FEAR and entry["values"] == {"declaration.i589.asylum.b1b_explain": new}
    assert entry["item"]["kind"] == "declaration" and re.match(r"\d{4}-\d{2}-\d{2}T", entry["at"])
    assert not [r for r in outcomes(case) if r["kind"] == "declaration"]  # not a box a reader filled: never in the accuracy record
    with pytest.raises(ValueError, match="Nothing changed"):
        drafting.edit(case, "i589", "asylum.b1b_explain", new, "Ana Attorney")
    with pytest.raises(ValueError, match="Enter your name first"):
        drafting.edit(case, "i589", "asylum.b1b_explain", "x", "")
    # back to the client's words: the edit is marked undone, kept on file with who
    card = drafting.revert(case, "i589", "asylum.b1b_explain", "Ana Attorney", "attorney")
    para = next(p for p in card["paragraphs"] if p["id"] == "asylum.b1b_explain")
    assert para["english"] == FEAR and para["edit"] is None
    assert para["edits"][0]["undone"]["who"] == "Ana Attorney" and para["edits"][0]["new"] == new


# --- grammar smoothing ------------------------------------------------------------------------------------------------------


def test_grammar_smoothing_is_off_by_default(case, firm):
    assert drafting.smoothing_on() is False
    section = next(s for s in settings.specs() if s["id"] == "drafting")
    assert section["fields"][0]["value"] == "off"
    with pytest.raises(ValueError, match="Grammar smoothing is off"):
        drafting.smooth(case, "i589", "Paulo Paralegal", model=lambda t: (t, "fake"))
    assert drafting.card(case, "i589")["can_smooth"] is False


def test_a_grammar_suggestion_is_never_used_until_a_person_accepts_it(case, firm):
    settings.save("drafting", {"grammar_smoothing": "on"}, "Ana Attorney")
    tense = "I am afraid that the gang would kill me if I went back, because they said so in front of my mother."
    card = drafting.smooth(case, "i589", "Paulo Paralegal", model=lambda t: (tense, "fake-model"))
    para = next(p for p in card["paragraphs"] if p["id"] == "asylum.b1b_explain")
    assert para["english"] == FEAR and not para["suggestion"]["accepted"] and "would went" in para["suggestion"]["reason"]
    with pytest.raises(ValueError, match="no grammar suggestion to take"):
        drafting.accept_suggestion(case, "i589", "asylum.b1b_explain", "Ana Attorney", "attorney")

    good = "I am afraid that the gang will kill me if I go back, because they said so in front of the my mother"  # an article, no full stop
    state = json.loads((case / drafting.STATE).read_text(encoding="utf-8"))
    state["filings"]["i589"]["smoothing"] = {}
    (case / drafting.STATE).write_text(json.dumps(state), encoding="utf-8")
    card = drafting.smooth(case, "i589", "Paulo Paralegal", model=lambda t: (good, "fake-model"))
    para = next(p for p in card["paragraphs"] if p["id"] == "asylum.b1b_explain")
    # passes the check, but the declaration keeps the client's words until a person accepts it
    assert para["suggestion"]["accepted"] and para["english"] == FEAR and para["english_how"] == "original"
    assert {"op": "added", "text": "the"} in para["suggestion"]["diff"]
    card = drafting.accept_suggestion(case, "i589", "asylum.b1b_explain", "Ana Attorney", "attorney")
    para = next(p for p in card["paragraphs"] if p["id"] == "asylum.b1b_explain")
    assert para["english"] == good and para["english_how"] == "edited" and para["edit"]["suggestion"] and para["original"] == FEAR
    entry = json.loads((case / "decisions.json").read_text(encoding="utf-8"))["declaration:i589:asylum.b1b_explain"]
    assert entry["reviewer"] == "Ana Attorney" and entry["old"] == FEAR and entry["note"] == drafting.SUGGESTION_NOTE
    card = drafting.revert(case, "i589", "asylum.b1b_explain", "Ana Attorney", "attorney")  # undone like any edit
    para = next(p for p in card["paragraphs"] if p["id"] == "asylum.b1b_explain")
    assert para["english"] == FEAR and para["edits"][0]["undone"]["who"] == "Ana Attorney"
    card = drafting.decline_suggestion(case, "i589", "asylum.b1b_explain", "Paulo Paralegal")
    para = next(p for p in card["paragraphs"] if p["id"] == "asylum.b1b_explain")
    assert para["suggestion"]["declined"]["who"] == "Paulo Paralegal" and para["english"] == FEAR
    with pytest.raises(ValueError, match="no grammar suggestion to take"):
        drafting.accept_suggestion(case, "i589", "asylum.b1b_explain", "Ana Attorney", "attorney")


def test_a_model_that_cannot_be_reached_is_said_without_its_name(case, firm):
    settings.save("drafting", {"grammar_smoothing": "on"}, "Ana Attorney")

    def broken(text):
        raise ConnectionRefusedError("refused")

    card = drafting.smooth(case, "i589", "Paulo Paralegal", model=broken)
    para = next(p for p in card["paragraphs"] if p["id"] == "asylum.b1b_explain")
    assert para["english"] == FEAR and not para["suggestion"]["accepted"]
    assert drafting._state(case, "i589")["smoothing"]["asylum.b1b_explain"]["engine"] == "the local model could not be reached"


# Every pair the verifier found accepted by the first check: each changes what a sworn statement says, and each is refused now.
REFUSED = [
    ("I was beaten and raped by the soldiers.", "I was beaten or raped by the soldiers."),
    ("They took my phone or my wallet, I am not sure which.", "They took my phone and my wallet, I am not sure which."),
    ("I went to the police. They helped me a little.", "I went to the police, but they helped me a little."),
    ("I was arrested. I was beaten.", "I was arrested, then I was beaten."),
    ("My father beat my uncle.", "My uncle beat my father."),
    ("The police hit the gang members.", "The gang members hit the police."),
    ("he hit me", "me hit he"),
    ("My brother was killed by the gang leader.", "My brother killed by the gang leader."),
    ("My brother was killed.", "My brother killed."),
    ("I was not arrested. I was beaten.", "I was arrested. I was not beaten."),
    ("He did not threaten me. My cousin threatened me.", "He did threaten me. My cousin did not threaten me."),
    ("I did not see him.", "I did not not see him."),
    ("My brother is in prison.", "My brother was in prison."),
    ("They have my passport.", "They had my passport."),
    ("They do threaten my family.", "They did threaten my family."),
    ("They threaten my family.", "They threatened my family."),
    ("I deny it.", "I denied it."),
    ("They will kill me.", "They will killed me."),
    ("My son was killed.", "My sons were killed."),
    ("He hit me hard.", "He hardly hit me."),
    ("They set fire to my shop.", "They fired to my shop."),
    ("They came with my father.", "They came for my father."),
    ("I gave money to the gang.", "I gave money for the gang."),
    ("They shot him in the house.", "They shot him at the house."),
    ("They came on 05/03/2019.", "They came on 03/05/2019."),
    ("I was arrested in 2019 and released in 2020.", "I was arrested in 2020 and released in 2019."),
    ("He had two sons and three daughters.", "He had three sons and two daughters."),
    ("They beat me. They beat me again. They beat my son.", "They beat me again. They beat my son."),
    ("The officer said the gang would kill me.", "The officer, said the gang, would kill me."),
    ("My brother, not my father, was arrested.", "My brother not, my father was arrested."),
    ("He told me I will kill you.", 'He told me: "I will kill you."'),
    ("I left. The house burned.", "I left the house. Burned."),
    ("He hit me.", "He hit me. Он убил моего брата."),
    ("He hit me. I did not hit him.", "He did nоt hit me. I did not hit him."),
    ("He hit me.", "He hit me on ٢٠١٩."),
    ("Rose told me the police will come.", "The police told me Rose will come."),
    ("They took her car.", "They took hers car."),
    ("The men came.", "They men came."),
    ("He hit me.", "He did not hit me."),
    ("Someone hit me.", "He hit me."),
    ("In the evening the men came to our house and they shouted at my mother and they broke the windows of the kitchen.",
     "In the evening the men came to our house and they shouted at my mother and they broke the windows of the kitchen. 他杀了我的兄弟."),
    ("he hit me", ""),
    ("a. b.", "A.\n\nB."),
]
ALLOWED = [
    ("i went to police", "I went to the police"),                   # "the" inserted, a capital
    ("Soldier hit me in 2024.", "A soldier hit me in 2024."),        # an added article
    ("He hit me  in the house.", "He hit me in the house."),         # a dropped double space
    ("he hit me", "He hit me."),                                     # a full stop at the very end
    ("I fled. I hid at my aunt's house.", "I fled.\nI hid at my aunt's house."),
]


@pytest.mark.parametrize("before,after", REFUSED)
def test_a_suggestion_that_changes_what_is_said_is_refused(before, after):
    ok, why = drafting.smoothing_allowed(before, after)
    assert not ok and why, (before, after)


@pytest.mark.parametrize("before,after", ALLOWED)
def test_a_suggestion_that_only_touches_articles_capitals_and_spacing_is_shown(before, after):
    assert drafting.smoothing_allowed(before, after) == (True, ""), (before, after)


# --- the practice, the final mark, the signature, the exhibit ----------------------------------------------------------------


def test_the_practice_must_be_approved_before_the_attorney_marks_it_final(case, firm):
    card = drafting.card(case, "i589")
    assert card["practice"]["state"] == "not_approved" and card["practice"]["plain_text"] == drafting.PRACTICE
    drafting.make_english(case, "i589", "Paulo Paralegal")
    with pytest.raises(ValueError, match="approves the drafting practice first"):
        drafting.mark_final(case, "i589", "Ana Attorney", "attorney")
    _approve()
    assert drafting.card(case, "i589")["practice"]["state"] == "approved"
    with pytest.raises(PermissionError, match="Only an attorney"):
        drafting.mark_final(case, "i589", "Paulo Paralegal", "paralegal")
    card = drafting.mark_final(case, "i589", "Ana Attorney", "attorney")
    assert card["final"]["who"] == "Ana Attorney" and card["pdf"]


def test_final_needs_every_paragraph_in_english(case, firm):
    _approve()
    with pytest.raises(ValueError, match="no English yet"):
        drafting.mark_final(case, "i589", "Ana Attorney", "attorney")


def test_the_exhibit_is_draft_until_the_client_signed_then_it_goes_in_the_packet(case, firm):
    _approve()
    drafting.make_english(case, "i589", "Paulo Paralegal")
    drafting.mark_final(case, "i589", "Ana Attorney", "attorney")
    pdf = drafting.pdf_path(case, "i589")
    text = _text(pdf)
    assert "DECLARATION OF ANA CLARA EXEMPLO SOUZA" in text and "In support of Form I-589" in text
    assert "1. In March 2025 the gang men threatened my family" in text and "2. " + FEAR in text
    assert "The declarant's own words, in Portuguese" in text and "1. Em março de 2025" in text
    assert drafting.DECLARE_IN_US in text and "DRAFT: not for filing until the client signs" in text
    assert "machine translation" in text
    assert any("(DRAFT) Tj" in op for op in _ops(pdf))

    plan = packet.plan(case, ROW_DONE, packet.load_filing("i589"))
    claim = next(ex for ex in plan["exhibits"] if ex["id"] == "claim")
    entry = next(f for f in claim["files"] if f.get("declaration") == "i589")
    assert entry["type"] == "declaration" and entry["generated"] and "DRAFT until the client signs" in entry["label"]
    assert any("not signed yet" in p for p in plan["problems"]) and any("still the machine translation" in p for p in plan["problems"])

    with pytest.raises(ValueError, match="in the future"):
        drafting.client_signed(case, "i589", "2099-01-01", "Paulo Paralegal")
    card = drafting.client_signed(case, "i589", "2026-09-30", "Paulo Paralegal", "paralegal")
    assert card["signed"]["date"] == "09/30/2026" and card["signed"]["who"] == "Paulo Paralegal"
    text = _text(pdf)
    assert "Signed by the declarant on 09/30/2026 (recorded by Paulo Paralegal)" in text and "DRAFT: not for filing" not in text
    assert not any("(DRAFT) Tj" in op for op in _ops(pdf))
    plan = packet.plan(case, ROW_DONE, packet.load_filing("i589"))
    assert not any("not signed yet" in p for p in plan["problems"])
    assert any("signs the declaration in ink" in c["text"] for c in plan["checklist"])


def test_a_change_after_the_final_mark_takes_the_declaration_out_of_the_packet(case, firm):
    _approve()
    drafting.make_english(case, "i589", "Paulo Paralegal")
    drafting.mark_final(case, "i589", "Ana Attorney", "attorney")
    drafting.edit(case, "i589", "asylum.b1b_explain", FEAR + " I have nowhere else to go.", "Paulo Paralegal", "paralegal")
    card = drafting.card(case, "i589")
    assert card["final"] is None and card["changed_since_final"]
    plan = packet.plan(case, ROW_DONE, packet.load_filing("i589"))
    assert not any(f.get("declaration") for ex in plan["exhibits"] for f in ex["files"])
    assert any("changed after the attorney marked it final" in p for p in plan["problems"])
    with pytest.raises(ValueError, match="Mark the declaration as the client's final first"):
        drafting.client_signed(case, "i589", "2026-09-30", "Paulo Paralegal")


def _ops(pdf: Path) -> list[str]:
    return [op for page in PdfReader(str(pdf)).pages for op in page.get_contents().get_data().decode("latin-1").splitlines()]


# --- the review bundle -----------------------------------------------------------------------------------------------------


def test_the_bundle_shows_each_paragraphs_source_and_the_edit(case, firm):
    from review import bundle

    new = "I am afraid the gang will kill me if I go back."
    drafting.edit(case, "i589", "asylum.b1b_explain", new, "Ana Attorney", "attorney")
    rows = drafting.bundle_rows(case, "i589")
    para = next(p for p in rows["paragraphs"] if p["id"] == "asylum.b1b_explain")
    assert para["edits"][0]["old"] == FEAR and para["edits"][0]["new"] == new and para["edits"][0]["who"] == "Ana Attorney"
    assert drafting.bundle_rows(case, "i485") is None

    from review.state import reviewed_graph

    reviewed_graph(case).save(case / "fact_graph_reviewed.json")  # what "Apply" writes in the review app
    packet.build(case, ROW_DONE, "Jane", packet.load_filing("i589"))
    data = bundle.rows(case, "i589")
    assert data["declaration"]["paragraphs"][1]["edits"][0]["new"] == new
    bundle.build(case, "i589", "Jane")
    text = _text(bundle.paths(case, "i589")[0])
    assert "The client's declaration: where each paragraph came from" in text
    assert "Question id: asylum.b1b_explain" in text and "Edited by Ana Attorney (attorney)" in text
    assert "Old: " + FEAR in text and "New: " + new in text and "Typed by the client in the portal" in text


# --- the cover letter's case paragraph ----------------------------------------------------------------------------------------


def test_the_cover_paragraph_states_the_cases_facts_each_with_its_source(case, firm):
    cover = drafting.cover_paragraph(case, "i589")
    texts = [s["text"] for s in cover["sentences"]]
    assert texts == ["The applicant applies for asylum and for withholding of removal.",
                     "The application is based on political opinion and membership in a particular social group, as marked in Part B, question 1 of Form I-589.",
                     "The applicant last arrived in the United States on February 10, 2026."]
    arrival = cover["sentences"][2]["facts"][0]
    assert arrival["key"] == "applicant.last_arrival_date" and arrival["source"] == "read from intake.pdf"
    assert cover["text"] == " ".join(texts)

    config = packet._letter_config(packet.load_filing("i589"), case)
    assert config["case_paragraph"] == cover["text"]
    from fill.cover_letter import intro

    words = intro(config, "Ana Clara Exemplo Souza", "her")
    assert words.startswith("Please be advised that this law office has been retained to represent <b>Ana Clara Exemplo Souza</b>")
    assert "immigration matters. The applicant applies for asylum" in words and words.endswith("we have provided the following documents:")
    # a letter without the slot keeps the firm's fixed wording, with nothing added
    plain = intro({"noun": "application"}, "Ana", "her")
    assert plain == ("Please be advised that this law office has been retained to represent <b>Ana</b> with respect to her immigration matters. "
                     "In support of her application, we have provided the following documents:")


def test_a_sentence_whose_fact_is_missing_is_left_out(case, firm):
    for key in ("applicant.i94_arrival_date", "asylum.basis_political", "asylum.basis_social_group"):
        g = FactGraph.load(case / "fact_graph.json")
        g.blank_by_review(key, "test")
        g.save(case / "fact_graph.json")
    cover = drafting.cover_paragraph(case, "i589")
    assert [s["text"] for s in cover["sentences"]] == ["The applicant applies for asylum and for withholding of removal."]
    assert "[" not in cover["text"]


def test_the_four_letters_have_the_slot_and_no_other(firm):
    schemas = schema_path.ROOT
    with_slot = schema_path.names("cover_letter", schemas)
    with_slot = sorted(n for n in with_slot if "{case_paragraph}" in schema_path.path("cover_letter", n, schemas).read_text(encoding="utf-8"))
    assert with_slot == ["i589", "i914", "u_visa", "vawa"]


def test_the_u_t_and_vawa_cover_sentences(tmp_path, firm):
    def graph(**facts):
        g = FactGraph("c")
        for k, v in facts.items():
            g.add_source(k, "answers", "test", v, v, 1.0)
        return g

    u = drafting.cover_paragraph(tmp_path, "u_visa", graph(**{"uvisa.crime": "Felonious assault", "uvisa.crime_date": "2025-04-02",
                                                                 "uvisa.crime_place": "Boston, MA", "uvisa.supb_signed": "2026-08-01", "uvisa.members": "1"}))
    assert [s["text"] for s in u["sentences"]] == [
        "The petitioner petitions for U nonimmigrant status, and for 1 qualifying family member on Form I-918, Supplement A.",
        "The qualifying criminal activity is felonious assault, which took place on or about April 2, 2025 in Boston, MA.",
        "The certifying official signed the enclosed Form I-918, Supplement B, on August 1, 2026."]
    t = drafting.cover_paragraph(tmp_path, "i914", graph(**{"tvisa.victim": "Yes", "tvisa.trafficking_began": "2024-11-01"}))
    assert t["sentences"][1]["text"] == "In Part 3, Item 1 of Form I-914, the applicant answers Yes: a victim of a severe form of trafficking in persons."
    v = drafting.cover_paragraph(tmp_path, "vawa", graph(**{"vawa.classification": "Spouse", "vawa.abuser_status": "Lawful permanent resident",
                                                             "vawa.marriage_date": "2019-05-04", "vawa.marriage_place": "Lowell, MA"}))
    assert [s["text"] for s in v["sentences"]] == [
        "The self-petitioner requests classification as a Self-Petitioning Spouse of Abusive U.S. citizen or Lawful Permanent Resident (Form I-360, Part 2, Item 1.I).",
        "The abuser is now, or was, a Lawful permanent resident (Form I-360, Part 10, Item 5).",
        "The self-petitioner married the abuser on May 4, 2019 in Lowell, MA."]


# --- the questions the declaration is built from ----------------------------------------------------------------------------


def test_the_t_u_and_vawa_accounts_are_the_clients_questions():
    for filing, prefix in (("i914", "tvisa.account_"), ("u_visa", "uvisa.account_"), ("vawa", "vawa.account_")):
        keys = [k for k, _l, _s in drafting.questions(filing)]
        assert keys and all(k.startswith(prefix) for k in keys), (filing, keys)
    assert [k for k, _l, _s in drafting.questions("i589")][:2] == ["asylum.b1a_explain", "asylum.b1b_explain"]
    with pytest.raises(ValueError):
        drafting.questions("i485")


def test_the_practice_is_on_the_rules_list_with_its_approval(firm):
    entry = next(r for r in approval.catalog() if r["id"] == drafting.PRACTICE_ID)
    assert entry["kind"] == "practice" and entry["plain_text"] == drafting.PRACTICE
    assert approval.status(drafting.PRACTICE_ID)["state"] == "not_approved"
    _approve()
    assert approval.status(drafting.PRACTICE_ID)["state"] == "approved"


def test_the_signature_line_is_28_usc_1746():
    # 28 U.S.C. 1746 (2024 edition, govinfo.gov, read 10/02/2026): within the United States, (2); without, (1)
    assert drafting.DECLARE_IN_US == "I declare under penalty of perjury that the foregoing is true and correct."
    assert drafting.DECLARE_ABROAD == "I declare under penalty of perjury under the laws of the United States of America that the foregoing is true and correct."
    assert "govinfo.gov" in drafting.USC_1746 and "10/02/2026" in drafting.USC_1746


# --- a language the system is not sure of, a paragraph in two languages ------------------------------------------------------


def _reply(case, key, text):
    """The client's typed reply to the office's question about one answer, as the portal engine records it."""
    g = FactGraph.load(case / "fact_graph.json")
    g.add_source(key, OFFICE_DOC_ID, OFFICE_DOC_TYPE, text, text.upper(), 0.95)
    g.save(case / "fact_graph.json")


@pytest.mark.parametrize("short", ["Sim, fui preso duas vezes.", "Fui ameaçado pela polícia em 2019 em Goiânia."])
def test_a_short_foreign_answer_takes_the_portal_language_and_blocks_the_final_mark_until_confirmed(case, firm, short):
    _approve()
    _reply(case, "asylum.b2_explain", short)
    para = next(p for p in drafting.declaration(case, "i589", client_language="pt")["paragraphs"] if p["id"] == "asylum.b2_explain")
    assert (para["language"], para["language_sure"], para["english_how"]) == ("pt", False, "none")
    assert "the language the client reads the portal in" in para["language_how"]
    alone = next(p for p in drafting.declaration(case, "i589")["paragraphs"] if p["id"] == "asylum.b2_explain")
    assert alone["language_sure"] is False and "assumed English" in alone["language_how"]  # never printed as English without a person
    drafting.make_english(case, "i589", "Paulo Paralegal", client_language="pt")
    with pytest.raises(ValueError, match="Confirm the language"):
        drafting.mark_final(case, "i589", "Ana Attorney", "attorney")
    card = drafting.confirm_language(case, "i589", "asylum.b2_explain", "pt", "Paulo Paralegal", "paralegal")
    para = next(p for p in card["paragraphs"] if p["id"] == "asylum.b2_explain")
    assert para["language"] == "pt" and para["language_sure"] and "confirmed by Paulo Paralegal" in para["language_how"]
    assert para["english_how"] == "machine"  # the English made for Portuguese holds
    entry = json.loads((case / "decisions.json").read_text(encoding="utf-8"))["declaration-language:i589:asylum.b2_explain"]
    assert entry["reviewer"] == "Paulo Paralegal" and list(entry["values"].values()) == ["pt"]
    card = drafting.mark_final(case, "i589", "Ana Attorney", "attorney")
    text = _text(drafting.pdf_path(case, "i589"))
    assert card["final"] and "The declarant's own words, in Portuguese" in text and short.split(",")[0].split(" ")[0] in text


def test_a_paragraph_in_two_languages_is_mixed_and_waits_for_a_person(case, firm):
    mixed = ("Os homens da gangue ameaçaram minha família porque meu pai não pagou.\n"
             "Mwen pa ka retounen nan peyi mwen paske yo te menase fanmi mwen ak pitit mwen yo.")
    _reply(case, "asylum.b4_explain", mixed)
    para = next(p for p in drafting.declaration(case, "i589")["paragraphs"] if p["id"] == "asylum.b4_explain")
    assert para["language"] == "mixed" and not para["language_sure"] and "Portuguese and Haitian Creole" in para["language_how"]
    assert para["english_how"] == "none" and "Confirm the language" in para["problem"]
    card = drafting.make_english(case, "i589", "Paulo Paralegal")  # the Portuguese answer is translated; the mixed one waits
    assert next(p for p in card["paragraphs"] if p["id"] == "asylum.b4_explain")["english_how"] == "none"
    card = drafting.confirm_language(case, "i589", "asylum.b4_explain", "ht", "Marta Tradutora")
    para = next(p for p in card["paragraphs"] if p["id"] == "asylum.b4_explain")
    assert para["language"] == "ht" and para["language_sure"]


# --- a paragraph left out ---------------------------------------------------------------------------------------------------


def test_a_paragraph_can_be_left_out_and_put_back_as_a_recorded_decision(case, firm):
    _approve()
    drafting.make_english(case, "i589", "Paulo Paralegal")
    card = drafting.include(case, "i589", "asylum.b1a_explain", False, "Ana Attorney", "attorney")
    out = next(p for p in card["paragraphs"] if p["id"] == "asylum.b1a_explain")
    kept = next(p for p in card["paragraphs"] if p["id"] == "asylum.b1b_explain")
    assert not out["included"] and out["n"] is None and kept["n"] == 1 and card["machine"] == 0
    entry = json.loads((case / "decisions.json").read_text(encoding="utf-8"))["declaration-include:i589:asylum.b1a_explain"]
    assert entry["reviewer"] == "Ana Attorney" and list(entry["values"].values()) == [drafting.EXCLUDE] and entry["old"] == drafting.INCLUDE
    drafting.mark_final(case, "i589", "Ana Attorney", "attorney")
    text = _text(drafting.pdf_path(case, "i589"))
    assert "1. " + FEAR in text and "gang men threatened" not in text and "Portuguese" not in text
    drafting.include(case, "i589", "asylum.b1a_explain", True, "Ana Attorney", "attorney")  # back in: the text changed
    assert drafting.card(case, "i589")["changed_since_final"]


# --- the PDF route never hands out a changed declaration ---------------------------------------------------------------------


def test_the_pdf_is_refused_once_the_text_changed_after_the_final_mark(case, firm):
    _approve()
    drafting.make_english(case, "i589", "Paulo Paralegal")
    drafting.mark_final(case, "i589", "Ana Attorney", "attorney")
    drafting.client_signed(case, "i589", "2026-09-30", "Paulo Paralegal")
    assert drafting.current_pdf(case, "i589") == drafting.pdf_path(case, "i589")
    drafting.edit(case, "i589", "asylum.b1b_explain", FEAR + " Again.", "Paulo Paralegal")
    with pytest.raises(LookupError, match="changed after it was marked final"):
        drafting.current_pdf(case, "i589")
    drafting.take_back(case, "i589", "Ana Attorney", "attorney")
    with pytest.raises(LookupError, match="not marked as the client's final"):
        drafting.current_pdf(case, "i589")


# --- cover sentences the form's own choices don't make -----------------------------------------------------------------------


def test_cover_sentences_left_out_for_other_and_for_an_attempt(tmp_path, firm):
    def graph(**facts):
        g = FactGraph("c")
        for k, v in facts.items():
            g.add_source(k, "answers", "test", v, v, 1.0)
        return g

    v = drafting.cover_paragraph(tmp_path, "vawa", graph(**{"vawa.classification": "Spouse", "vawa.abuser_status": "Other"}))
    assert not any("The abuser is" in s["text"] for s in v["sentences"]) and "a Other" not in v["text"]
    for crime in ("Attempt to commit any of the named crimes", "Conspiracy to commit any of the named crimes",
                  "Solicitation to commit any of the named crimes"):
        u = drafting.cover_paragraph(tmp_path, "u_visa", graph(**{"uvisa.crime": crime, "uvisa.crime_date": "2025-04-02"}))
        assert not any("criminal activity" in s["text"] for s in u["sentences"]), crime
