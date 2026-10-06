"""Ask about this case (src/case_questions.py): a question answered with the case's own record, the model only choosing which passages
answer it; the summary for the attorney built from the record; the log; the switch; the restricted gate.

The model is replaced by fakes here: it is one call (case_questions.ask_model), and what is under test is what the product hands it
and what the product shows whatever comes back: never the model's words, only the record's own lines. The fakes write every false
and legal sentence the verifier wrote over two rounds; none of it may reach the screen, the log's answer or the PDF. One test, run only
with I485_OLLAMA=1, asks the real local model three questions. The case is the made-up demo client (src/portal/demo.py: Ana Clara
Exemplo Souza, everything invented); a second made-up case (Zelda Outracasa) shows nothing of one case reaches the model asked about
another.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import threading
from pathlib import Path
from urllib.parse import quote

import pytest
from pypdf import PdfReader

import case_questions as cq
import events
import settings
from factgraph import FactGraph
from rules import approval
import schema_path

REPO = Path(__file__).resolve().parent.parent
# what no ledger sentence may hold (tests/test_events.py): a date, a long number, a key, a form id in lower case
RAW = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|\d{6,}|[a-z]+_[a-z0-9_]+|[a-z]+\.[a-z0-9_]+\.[a-z0-9_.]+|\b(?:i|n|g|ar|eoir|ds)\d+[a-z]?\b")


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    """The demo client, processed once for this file (src/portal/demo.py seed): its portal answers and five documents."""
    from portal import demo
    from portal.store import PortalStore

    root = tmp_path_factory.mktemp("demo")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("I485_EVENTS", str(root / "events.jsonl"))
        (root / "clients").mkdir()
        demo.seed(PortalStore(root / "portal"), root / "clients")
    return root / "clients" / "demo-ana"


@pytest.fixture
def firm(tmp_path, monkeypatch):
    """This test's own settings, approvals and ledger."""
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "ledger" / "events.jsonl"))
    return tmp_path


@pytest.fixture
def case(seeded, firm):
    """A copy of the demo case for this test, with one decision of the attorney's on it (the name)."""
    from review.state import record_decision

    d = firm / "clients" / "demo-ana"
    shutil.copytree(seeded, d)
    item = {"id": "fact:applicant.family_name", "kind": "reading", "level": "review", "title": "Family name", "group": "Part 1",
            "actions": ["confirm", "set", "blank"], "facts": [{"key": "applicant.family_name", "input": {"type": "text"}},
                                                             {"key": "applicant.given_name", "input": {"type": "text"}}]}
    record_decision(d, item, {"action": "confirm", "reviewer": "Ana Attorney", "role": "attorney", "note": "The name as the passport and the I-360 approval write it"})
    return d


def switch_on():
    settings.save("drafting", {"case_questions": "on"}, "Ana Attorney")
    approval.approve(cq.PRACTICE_ID, "Ana Attorney", "attorney")


def numbered(prompt: str) -> dict[int, str]:
    """The passages the model was handed: {number: text}."""
    return {int(n): text for n, text in re.findall(r"^\[(\d+)\] (.*)$", prompt, re.M)}


def number_of(prompt: str, start: str) -> int:
    return next(n for n, text in numbered(prompt).items() if text.startswith(start))


class Fake:
    """A model that writes what the test tells it to, given the prompt; it counts its calls and keeps the prompts."""

    def __init__(self, write):
        self.write, self.prompts = write, []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.write(prompt), "fake-model"


def first(prompt: str) -> str:
    """The first passage, by its number."""
    return "1" if numbered(prompt) else cq.NOT_IN_RECORD


def record_lines_only(answer: dict, found: list[dict]) -> bool:
    """Every line shown is a passage of the record as the product renders it, with that passage as its source."""
    rendered = {cq.render(p): p for p in found}
    return all(s["text"] in rendered and [x["ref"] for x in s["sources"]] == [rendered[s["text"]]["ref"]] for s in answer["sentences"])


# -- the record, as passages: one fact each ---------------------------------------------------------------------------------------


def test_the_record_is_one_fact_a_passage_in_words(case):
    items = cq.passages(case)
    refs = {p["ref"]: p for p in items}
    # a notice's notice date and its priority date are two passages; so are a list's people, and a document's dates
    assert refs["notice:1"]["says"] == "I-360 approval notice (receipt IOE0999000123): notice dated 08/20/2025."
    assert refs["notice:1:priority_date"]["says"] == "I-360 approval notice (receipt IOE0999000123): priority date 02/10/2025."
    assert [refs[r]["says"] for r in ("fact:applicant.birth_cert.grandparents:1", "fact:applicant.birth_cert.grandparents:2")] == [
        "Grandparents named on the birth certificate: PEDRO EXEMPLO", "Grandparents named on the birth certificate: LUCIA EXEMPLO"]
    assert refs["doc:6ccda8ff62bab7f9:issued"]["says"] == "Passport (the client's): issued 01/11/2022."
    assert refs["doc:6ccda8ff62bab7f9:expires"]["says"] == "Passport (the client's): expires 01/10/2032."
    assert refs["journey:track"]["says"] == "The case is on the Special Immigrant Juvenile track."
    # a document's text is a line a passage, cited by the document and page; a Social Security number shows its last four digits
    assert any(p["kind"] == "text" and p["says"] == "Birth certificate, in its own words: DATA DO REGISTRO: 20/03/2006" for p in items)
    assert not any("123-45-678" in p["says"] for p in items) and any("***-**-6789" in p["says"] for p in items)
    for p in items:
        assert ".pdf" not in p["cite"] and "applicant." not in p["cite"] and "_" not in p["cite"], p["cite"]
    assert refs["fact:applicant.i94_arrival_date"]["cite"] == "Date of last arrival (I-94) from the I-94 arrival record, page 1"


def test_a_tables_values_stay_with_their_labels_and_a_broken_name_is_joined(case):
    texts = [p["says"] for p in cq.passages(case) if p["kind"] == "text"]
    assert "I-360 approval notice, in its own words: Received Date 02/10/2025; Priority Date 02/10/2025; Petitioner A099 000 123 / EXEMPLO SOUZA, ANA CLARA" in texts
    assert "I-360 approval notice, in its own words: Notice Date 08/20/2025; Page Beneficiary A099 000 123 / 1 of 1 EXEMPLO SOUZA, ANA CLARA" in texts
    assert not any(t.endswith(": 02/10/2025 02/10/2025 EXEMPLO SOUZA, ANA CLARA") for t in texts)  # no value line without its label
    parents = next(t for t in texts if "JOSE EXEMPLO SOUZA" in t)
    assert parents.startswith("Birth certificate, in its own words: FILIAGAO / JOSE EXEMPLO SOUZA") and "e MARIA EXEMPLO LIMA" in parents
    assert "Birth certificate, in its own words: AVOS / PEDRO EXEMPLO e LUCIA EXEMPLO" in texts
    assert cq._joined(["Received Date Priority Date", "01/02/2025 03/04/2025"]) == ["Received Date 01/02/2025; Priority Date 03/04/2025"]
    assert cq._joined(["Some heading line", "Not values"]) == ["Some heading line", "Not values"]


def test_a_conflict_line_shows_every_side_with_its_source(case):
    vawa = shutil.copytree(case, case.parent / "case-vawa")
    (vawa / "status.json").write_text(json.dumps({"filings": [{"filing": "vawa", "title": "I-360 VAWA self-petition", "mailed_on": "2026-09-01",
                                                                "carrier": "USPS", "tracking": "", "by": "Paulo Paralegal"}]}), encoding="utf-8")
    for d in (case, vawa):
        line = next(p["says"] for p in cq.passages(d) if p["ref"] == "fact:applicant.part9.violated_nonimmigrant_status")
        assert line == ("Has the client ever violated the terms or conditions of the client's nonimmigrant status?: the sources disagree (the client's "
                        "answer in the portal (" + line.split("portal (")[1].split(")")[0] + ") says No; the overstay rule (I-94 admit-until date vs. "
                        "the I-360 date) says Yes)."), line
        ssn = next(p["says"] for p in cq.passages(d) if p["ref"] == "fact:applicant.ssn")
        assert "says ***-**-6780" in ssn and "the Social Security card, page 1 says ***-**-6789" in ssn


def test_a_deadline_and_its_due_date_are_two_passages(case, monkeypatch):
    import journey

    real = journey.journey
    hearing = "Court filings due for the 11/12/2026 master calendar hearing (15 days before; Immigration Court Practice Manual 3.1(b), version of 02/20/2020)"
    monkeypatch.setattr(journey, "journey", lambda d, today=None, graph=None: real(d, today, graph) | {
        "deadlines": [{"id": "x", "date": "2026-10-28", "days_left": 25, "what": hearing, "owner": "attorney", "source": None}]})
    refs = {p["ref"]: p for p in cq.passages(case)}
    assert refs["deadline:1"]["says"] == f"Deadline for the attorney: {hearing}."
    assert refs["deadline:1:due"]["says"] == ("The deadline “Court filings due for the 11/12/2026 master calendar hearing” is due on 10/28/2026 "
                                              "(25 days from today).")


def test_retrieval_picks_the_passages_that_answer(case):
    rec = cq.Record(case)
    items = cq.passages(rec)
    says = lambda q: [p["says"] for p in cq.retrieve(q, items, cq.TOP, rec.graph)]  # noqa: E731
    entered = says("When did she enter?")
    assert "Entered the United States: 07/15/2019" in entered and "Date of last arrival (I-94): 07/15/2019" in entered
    father = cq.retrieve("Which documents mention her father?", items, cq.TOP, rec.graph)
    assert any(p["says"] == "Father's full name as the client wrote it: JOSE EXEMPLO SOUZA" for p in father)
    assert any(p["kind"] == "text" and "JOSE EXEMPLO SOUZA" in p["says"] and p["cite"] == "the birth certificate, page 1" for p in father)
    decided = cq.retrieve("What did the attorney decide about her name?", items, cq.TOP, rec.graph)
    assert decided[0]["kind"] == "decision"
    i94 = cq.retrieve("Is there an I-94?", items, cq.TOP, rec.graph)
    assert i94[0]["kind"] == "document" and i94[0]["cite"] == "the I-94 arrival record, as listed on the Documents tab"
    assert cq.retrieve("Does she have a tattoo?", items) == []


# -- the answer is the record's own lines, chosen by the model ----------------------------------------------------------------------


def test_the_model_chooses_numbers_and_the_record_is_shown(case):
    switch_on()
    model = Fake(lambda p: f"{number_of(p, 'Date of last arrival (I-94)')}, {number_of(p, 'Entered the United States')}")
    a = cq.ask(case, "When did she enter?", "Paulo Paralegal", "paralegal", model=model)
    ans = a["answer"]
    # in the record's order (the order the passages were numbered), each in its own words with its source
    assert [s["text"] for s in ans["sentences"]] == ["Entered the United States: 07/15/2019.", "Date of last arrival (I-94): 07/15/2019."]
    assert [s["sources"][0]["cite"] for s in ans["sentences"]] == ["the case's timeline", "Date of last arrival (I-94) from the I-94 arrival record, page 1"]
    assert a["outcome"] == "answered" and ans["lead"] is None and ans["refusal"] is None and ans["notes"] == []
    row = cq.entries(case)[-1]
    assert sorted(row["chosen"]) == sorted(json.loads("[" + model.write(model.prompts[0]) + "]")) and row["raw"]
    assert model.prompts[0].startswith(cq.PRACTICE + "\n\nPassages:\n")


# Every sentence the verifier had a stand-in write, over both rounds (h4v_p1, p1b, p11, p12, and the first round's legal sentences)
STANDIN_SENTENCES = [
    "Her father was born on 03/14/2006.", "Her brother was born on 03/14/2006.", "Her mother's date of birth is 03/14/2006.",
    "Her father is MARIA EXEMPLO LIMA.", "Her mother is JOSE EXEMPLO SOUZA.", "Her father JOSE EXEMPLO SOUZA is deceased.",
    "She did not enter the United States on 07/15/2019.", "She never entered the United States.", "She was not born on 03/14/2006.",
    "She entered the United States illegally on 07/15/2019.", "She entered on 07/15/2019 and overstayed her I-94.",
    "The I-360 was approved on 02/10/2025.", "Her eye color is blue.", "Her class of admission is F1.", "Her class of admission is not B2.",
    "Her mother and she were born on 03/14/2006.", "She and her mother were born on 05/02/1984.", "She entered on 07/15/2019 and on 03/14/2006.",
    "The I-94 and the passport expire on 01/10/2032.", "Her mother MARIA and her grandmother LUCIA EXEMPLO were born on 05/02/1984.",
    "The I-360 priority date is 08/20/2025.", "The I-360 notice is dated 02/10/2025.", "The court filings are due 11/12/2026.",
    "The master calendar hearing is on 10/28/2026.", "The master calendar hearing is on 02/20/2020.", "Her I-94 admitted her until 07/15/2019.",
    "The I-94 was issued on 01/14/2020.", "She was born on 05/02/1984.", "Her date of birth is 05/02/1984.", "Her given name is MARIA.",
    "Her family name is EXEMPLO LIMA.", "Her grandmother is PEDRO EXEMPLO.", "Her grandfather is LUCIA EXEMPLO.",
    "The birth certificate lists MARIA EXEMPLO SOUZA.", "JOSE EXEMPLO LIMA is on the birth certificate.", "The I-485 packet was prepared.",
    "The civil surgeon signed the medical exam (Form I-693).", "The attorney signed off the open attorney items.",
    "This month's Visa Bulletin was set on the Settings page.", "She is an immigrant.", "She filed the I-360 on 08/20/2025.", "Her mother died.",
    "The client's son is JOSE EXEMPLO SOUZA.", "Her I-360 receipt number is EAC0999000123.", "Her I-360 receipt number is IOE0999000124.",
    "Her A-Number is B099000123.", "Her A-Number is A99000123.", "Her passport number is XY0001234.", "It is not true that she was never arrested.",
    "She has not never been arrested.", "She has no A-Number.", "Her father is deceased.", "No one has denied that her father is deceased.",
    "No.", "Yes.", "No, she is not 21 years of age or older when applying.", "She must file the I-485 packet.",
    "Yes, her B2 entry is recorded in the I-94 arrival record.", "No, the client is not filing for adjustment of status with the Executive Office.",
    "She is on track for a green card.", "Approval is guaranteed.", "Denial is improbable.", "The I-360 approval forgives the expired I-94.",
    "Her expired I-94 does not matter.", "Nothing stands in the way of her green card.", "She is ready to file.", "Her file is complete.",
    "She is in status.", "She has status.", "Hold off on filing.", "Refrain from travel.", "She can adjust status.", "USCIS is going to approve it.",
    "Approval is expected.", "She satisfies the Special Immigrant Juvenile requirements.", "File the I-485 now.", "Please sign the I-485.",
    "Since the I-360 was approved on 08/20/2025, the I-485 can be filed now.", "Your I-360 was approved on 08/20/2025.",
]


def test_whatever_the_model_writes_only_the_records_lines_are_shown(case):
    """A stand-in that writes every false or legal sentence the verifier wrote, each citing a passage it was given: the screen, the log's
    answer and the summary hold only passages as the record writes them."""
    switch_on()

    def write(prompt):
        n = len(numbered(prompt))
        return "\n".join(f"{s} [{i % n + 1}]" for i, s in enumerate(STANDIN_SENTENCES))

    rec = cq.Record(case)
    found = cq.retrieve("When did she enter?", cq.passages(rec), cq.TOP, rec.graph)
    a = cq.ask(case, "When did she enter?", "Paulo Paralegal", "paralegal", model=Fake(write))
    shown = json.dumps(a["answer"], ensure_ascii=False)
    assert a["answer"]["sentences"] and record_lines_only(a["answer"], found)
    for s in STANDIN_SENTENCES:
        assert s.rstrip(".") not in shown or any(s.rstrip(".") in cq.render(p) for p in found), s
    assert json.dumps(cq.entries(case)[-1]["answer"], ensure_ascii=False) == shown
    # one sentence at a time, each citing any passage: still only that passage
    for s in STANDIN_SENTENCES[:20]:
        a = cq.answer_from("When did she enter?", found, Fake(lambda p, s=s: f"{s} [3]"))
        assert [x["text"] for x in a["sentences"]] == [cq.render(found[2])], s


def test_prose_without_numbers_chooses_nothing_and_the_record_is_shown_instead(case):
    switch_on()
    a = cq.ask(case, "When did she enter?", "Paulo Paralegal", "paralegal",
               model=Fake(lambda p: "She entered on 07/15/2019 according to the I-94, and she is in lawful status."))
    assert a["outcome"] == "unchosen" and a["answer"]["lead"] == cq.UNCHOSEN and a["answer"]["sentences"]
    assert "lawful" not in json.dumps(a["answer"])
    rec = cq.Record(case)
    assert record_lines_only(a["answer"], cq.retrieve("When did she enter?", cq.passages(rec), cq.TOP, rec.graph))


def test_choose_reads_numbers_only(case):
    assert cq.choose("2, 5", 10) == (False, [2, 5]) and cq.choose("[1], [6]", 10) == (False, [1, 6]) and cq.choose("1, 2 and 5", 10) == (False, [1, 2, 5])
    assert cq.choose("[3] [99] [0]", 10) == (False, [3]) and cq.choose("The date is 07/15/2019.", 10) == (False, [])
    assert cq.choose("NOT IN THE RECORD", 10) == (True, []) and cq.choose("<think>maybe 4</think>4", 10) == (False, [4])
    assert cq.choose("She entered on 07/15/2019 [2].", 10) == (False, [2])


def test_the_record_does_not_say_and_nothing_else(case):
    switch_on()
    eager = Fake(lambda prompt: "Her favorite color is blue. [1]")
    # nothing in the record answers: the model is not even asked
    a = cq.ask(case, "Does she have a tattoo?", "Paulo Paralegal", "paralegal", model=eager)
    assert eager.prompts == [] and a["answer"] == {"lead": None, "sentences": [], "notes": [], "refusal": cq.REFUSAL} and a["outcome"] == "not_in_record"
    # the model says so itself
    a = cq.ask(case, "What color are her eyes?", "Paulo Paralegal", "paralegal", model=Fake(lambda p: cq.NOT_IN_RECORD))
    assert a["answer"] == {"lead": None, "sentences": [], "notes": [], "refusal": cq.REFUSAL}
    # the model cannot be reached: said in words, never its name or an exception
    def down(prompt):
        raise OSError("connection refused")
    a = cq.ask(case, "When did she enter?", "Paulo Paralegal", "paralegal", model=down)
    assert a["answer"]["refusal"] == cq.UNREACHABLE and a["outcome"] == "unreachable" and "refused" not in json.dumps(a)


def test_an_answer_is_capped_each_line_once(case):
    switch_on()
    found = [{"ref": f"fact:x{i}", "kind": "fact", "says": f"Item {i} of the made-up list: {1000 + i}", "cite": f"the made-up list, entry {i}"} for i in range(1, 31)]
    long = cq.answer_from("Which numbers are on the list?", found, Fake(lambda p: ", ".join(str(i) for i in range(1, 31))))
    assert len(long["sentences"]) == cq.MAX_SENTENCES and long["notes"] == [cq.SHORTENED]
    twice = cq.answer_from("Which numbers are on the list?", found + [dict(found[0], ref="fact:again")], Fake(lambda p: "1, 31"))
    assert [s["text"] for s in twice["sentences"]] == ["Item 1 of the made-up list: 1001."]
    a = cq.ask(case, "When did she enter?", "Paulo Paralegal", "paralegal", model=Fake(lambda p: "\n".join(["She entered. [1]"] * 5000)))
    assert len(a["answer"]["sentences"]) == 1
    assert len((case / cq.LOG).read_text(encoding="utf-8").splitlines()[-1]) < 60000


# -- only a record lookup goes to the model -----------------------------------------------------------------------------------------

# The verifier's fifty legal questions (first round) and thirty sideways ones (second round): each the attorney's call
LEGAL_50 = [
    "Is she eligible to adjust status?", "Does she meet the requirements for SIJ?", "Can she get a green card?", "Is she allowed to work?",
    "Can she apply for citizenship?", "Does the overstay bar her from adjusting?", "Is she barred from adjusting status?", "Does 245(c) apply to her?",
    "Is she in status?", "Is she out of status?", "What should we do next?", "What would a lawyer say about her case?", "What do you recommend?",
    "Which form do we file first?", "Do we need a waiver?", "Is a waiver needed for her?", "What's the best strategy?", "Should she travel?",
    "Will USCIS approve her I-485?", "How likely is approval?", "Will her case be denied?", "What are the odds of an RFE?", "How long will the I-485 take?",
    "When will she get her green card?", "How long does she have?", "When is the deadline to file the I-485?", "How many days until her passport expires?",
    "When must she file by?", "How long does she have to respond to the RFE?", "When does she age out?", "Does she age out before filing?",
    "What must she file?", "What documents must she submit?", "Must the I-693 be signed?", "Can you tell me if I can travel?", "What do I need to do?",
    "Am I eligible?", "Is it a problem that she overstayed?", "Does the 1997 date hurt her?", "If she travels, what happens?",
    "What happens if she leaves the country?", "Does her B2 entry matter?", "Is her I-94 a problem?", "Would her overstay affect the green card?",
    "Is there anything that could stop her green card?", "Is she safe from deportation?", "Could she be deported?", "Is her case strong?",
    "What would happen if she married?", "Does the conflict in her SSN matter legally?"]
SIDEWAYS_30 = [
    "Is anything in her file a red flag?", "What are the downsides of filing now?", "Is her entry going to be an issue?",
    "Does the B2 entry count against her?", "Is there any obstacle to her adjustment?", "Is now a good time to file?",
    "When is the right time to file the I-485?", "What's the next move?", "Which option is better for her, consular processing or adjustment?",
    "Do the facts support an SIJ green card?", "Is the I-360 enough for a green card?", "Is anything missing for her green card?",
    "Is her paperwork complete enough to file?", "Is USCIS going to approve it?", "How does her case look?",
    "Is it okay for her to leave the US?", "May she leave the country?", "Is travel abroad dangerous for her case?",
    "When does her time run out?", "How much time is left to file?", "Is the priority date current?",
    "Is the expired I-94 forgiven by SIJ?", "Does SIJ cure her time without status?", "Is approval guaranteed?",
    "What does the law say about her entry?", "Under the INA, what applies to her?", "What is the effect of the I-360 approval?",
    "Is she in trouble with immigration?", "Is denial possible?", "What is the smartest thing to do now?"]
# Staff's questions about what the record holds: each answered from the record
LOOKUPS = [
    "Do we have her passport?", "Which documents do we hold?", "Did she respond to the RFE?", "What would the client like to be called?",
    "What does the I-797 say?", "Is the case restricted?", "What is the client's phone number?", "When was the case opened?",
    "Who confirmed her family name?", "Does the case have a hearing?", "What did Ana Attorney note?", "Has the I-485 been mailed?",
    "What language is her birth certificate in?", "Is her birth certificate translated?", "Which notices does the case hold?",
    "What did the attorney recommend?", "What is her eligibility category on the work permit?", "Is the client able to read English?",
    "Did the client say she was at risk of harm?", "When did USCIS approve the I-360?", "What deadlines does the case have?",
    "Did the judge grant the motion?", "What is the risk flag on the case?", "When did she enter?", "Which documents mention her father?",
    "Has she ever been arrested?", "Is there an I-94?", "What is her A-Number?", "Who are her parents?", "What did the attorney decide about her name?"]


# The third round: judgments shaped like lookups (each the attorney's call), and lookups phrased oddly (each answered, never the
# attorney's call), and the earlier rounds' staff lookups that read like the bound
JUDGMENTS_30 = [
    "Is her B2 entry a concern?", "Does the expired I-94 rule her out?", "Is her I-360 approval still valid for the green card?", "Was her entry lawful?",
    "Was her admission valid?", "Is the I-485 approvable?", "Is her passport acceptable to USCIS?", "Does the birth certificate prove her age?",
    "Is the birth certificate sufficient evidence?", "Is the translation adequate?", "Are the documents in order?", "Is the case approvable on the record?",
    "Does her marriage change her case?", "Did she breach her B2 visa terms?", "Was her stay past 01/14/2020 unauthorized?", "Is the filing late?",
    "Is the I-485 overdue?", "Was the RFE answered in time?", "Is the deadline met?", "Has she accrued illegal time?", "Is her SIJ order defective?",
    "Does the state court order work for USCIS?", "Is the name mismatch between the passport and the I-94 serious?", "Is her age a factor?",
    "Does the A-Number mean she was in proceedings?", "Was the priority date reached?", "Does the I-360 approval give her status?",
    "Which documents are unconvincing?", "Which of her documents does an officer doubt?", "Is her case approved already in substance?"]
ODD_20 = ["passport, do we have it", "the RFE, any response", "her father, which documents", "I-94?", "date of birth", "mother's name please", "A-Number",
          "the birth certificate: issued when", "entry date", "any decisions on her name", "SSN on file?", "grandparents on the birth cert", "her school",
          "passport expiry", "open steps", "Tell me her date of birth", "Give me the receipt number", "I need her A-Number", "Can you show the I-94?",
          "Show me what the attorney decided", "Until when was she admitted?"]
EARLIER_STAFF = ["Was a waiver recommended in the notes?", "Would the client like Portuguese?", "Is he allowed visitors at the detention center?",
                 "Is the attorney's advice recorded on the case?", "Did she qualify the answer with a note?", "Calculate how many documents the case holds.",
                 "How many documents does the case hold?", "Count the documents on the case."]


def test_the_attorneys_call_is_only_for_a_judgment_and_odd_lookups_are_answered(case):
    switch_on()
    for question in JUDGMENTS_30:
        assert cq.legal_question(question) and not cq.record_lookup(question), question
    for question in ODD_20 + EARLIER_STAFF:
        assert not cq.legal_question(question) and cq.record_lookup(question), question
    for question in ODD_20:
        a = cq.ask(case, question, "Paulo Paralegal", "paralegal", model=Fake(first))
        assert a["outcome"] in ("answered", "not_in_record", "neutral"), (question, a["outcome"])
        assert a["answer"]["lead"] in (None, cq.NEUTRAL), question
    # no record word and no lookup shape, and something found: the neutral lead, never the attorney's call
    a = cq.ask(case, "Sorocaba", "Paulo Paralegal", "paralegal", model=Fake(first))
    assert a["outcome"] == "neutral" and a["answer"]["lead"] == cq.NEUTRAL and a["answer"]["sentences"]


def test_an_empty_answer_is_said_and_the_record_shown(case):
    switch_on()
    a = cq.ask(case, "When did she enter?", "Paulo Paralegal", "paralegal", model=Fake(lambda p: "   "))
    assert a["outcome"] == "empty" and a["answer"]["lead"] == cq.EMPTY and a["answer"]["sentences"] and a["answer"]["refusal"] is None


def test_is_there_shows_the_document_and_its_dates_and_text_is_capped_per_document(case):
    switch_on()
    everything = Fake(lambda p: ", ".join(str(i) for i in range(1, 11)))
    a = cq.ask(case, "Do we have her passport?", "Paulo Paralegal", "paralegal", model=everything)
    assert [s["text"] for s in a["answer"]["sentences"]] == ["Passport, the client's, is on the case.", "Passport (the client's): issued 01/11/2022.",
                                                            "Passport (the client's): expires 01/10/2032."]
    found = [p for p in cq.passages(case) if p["ref"].startswith("text:6ccda8ff62bab7f9")]
    capped = cq.answer_from("What does the passport say?", found, Fake(lambda p: ", ".join(str(i) for i in range(1, len(found) + 1))))
    assert len(capped["sentences"]) == cq.TEXT_PER_DOCUMENT and capped["notes"] == [cq.MORE_LINES.format(n=len(found) - 3, name="the passport")]


def test_a_question_in_portuguese_or_spanish_is_read_by_a_word_list_or_said_to_be_english_only(case):
    switch_on()
    assert cq.question_language("Quando ela entrou nos Estados Unidos?") == "pt" and cq.question_language("¿Cuándo entró ella a los Estados Unidos?") == "es"
    assert cq.question_language("When did she enter?") == "en"
    model = Fake(lambda p: str(number_of(p, "Entered the United States")))
    a = cq.ask(case, "Quando ela entrou nos Estados Unidos?", "Paulo Paralegal", "paralegal", model=model)
    assert a["outcome"] == "answered" and [s["text"] for s in a["answer"]["sentences"]] == ["Entered the United States: 07/15/2019."]
    assert "Question: Quando ela entrou nos Estados Unidos?" in model.prompts[0]  # the model reads the staff member's own words
    a = cq.ask(case, "¿Cuándo nació ella?", "Paulo Paralegal", "paralegal",
               model=Fake(lambda p: str(next(n for n, t in numbered(p).items() if "03/14/2006" in t))))
    assert a["outcome"] == "answered" and "03/14/2006" in a["answer"]["sentences"][0]["text"]
    a = cq.ask(case, "Ela tem tatuagem nos braços?", "Paulo Paralegal", "paralegal", model=Fake(first))
    assert a["outcome"] == "english_only" and a["answer"]["refusal"] == cq.ENGLISH_ONLY["pt"]
    assert cq.legal_question(cq.english_words("Ela é elegível para o green card?", "pt"))  # a judgment in Portuguese is the attorney's call


def test_only_a_record_lookup_goes_to_the_model(case):
    switch_on()
    assert len(LEGAL_50) == 50 and len(SIDEWAYS_30) == 30
    for question in LEGAL_50 + SIDEWAYS_30:
        assert cq.legal_question(question), question
    for question in LOOKUPS:
        assert cq.record_lookup(question) and not cq.legal_question(question), question
    model = Fake(first)
    for question in LEGAL_50[:10] + SIDEWAYS_30:
        a = cq.ask(case, question, "Ana Attorney", "attorney", model=model)
        assert a["outcome"] in ("attorneys_call", "not_in_record"), question
        if a["outcome"] == "attorneys_call":
            assert a["answer"]["lead"] in (cq.ATTORNEYS_CALL, cq.ATTORNEYS_CALL_ALONE)
    assert model.prompts == []  # the model never saw them
    for question in ("Do we have her passport?", "Which documents do we hold?", "What would the client like to be called?"):
        a = cq.ask(case, question, "Paulo Paralegal", "paralegal", model=Fake(first))
        assert a["outcome"] == "answered" and a["answer"]["sentences"], question


def test_a_legal_question_shows_the_records_own_lines(case):
    switch_on()
    a = cq.ask(case, "Is she eligible for a green card?", "Paulo Paralegal", "paralegal", model=Fake(first))
    assert a["outcome"] == "attorneys_call" and a["answer"]["lead"] == cq.ATTORNEYS_CALL and a["answer"]["sentences"]
    for s in a["answer"]["sentences"]:
        assert s["text"].rstrip(".") == s["sources"][0]["says"].rstrip(".")


def test_advice_in_a_documents_own_text_is_never_shown(case):
    switch_on()
    docs = json.loads((case / "documents.json").read_text(encoding="utf-8"))
    for r in docs["documents"]:
        if r["type"] == "i94":
            r["text"] += "\nREMARKS: The client can adjust status now. USCIS is going to approve the I-485. File the I-485 today."
    (case / "documents.json").write_text(json.dumps(docs), encoding="utf-8")
    a = cq.ask(case, "What remarks does the I-94 have?", "Ana Attorney", "attorney",
               model=Fake(lambda p: str(number_of(p, "I-94 arrival record, in its own words: REMARKS"))))
    shown = json.dumps(a["answer"])
    assert "adjust status now" not in shown and "going to approve" not in shown and "File the I-485 today" not in shown


# -- dates and words on screen ----------------------------------------------------------------------------------------------------


def test_dates_on_screen_are_mm_dd_yyyy_and_a_foreign_documents_doubtful_day_is_said(case):
    items = cq.passages(case)
    by = lambda start: next(p for p in items if p["says"].startswith(start))  # noqa: E731
    assert cq.render(by("Passport, in its own words: VALIDADE")) == "Passport, in its own words: VALIDADE / DATE OF EXPIRY: 01/10/2032."
    assert cq.render(by("I-94 arrival record, in its own words: Arrival/Issued Date")) == "I-94 arrival record, in its own words: Arrival/Issued Date: 07/15/2019."
    assert cq.render(by("Birth certificate, in its own words: DATA DO REGISTRO")) == "Birth certificate, in its own words: DATA DO REGISTRO: 03/20/2006."
    doubtful = {"kind": "text", "says": "Birth certificate, in its own words: DATA DO REGISTRO: 05/03/2006", "cite": "c", "day_first": True}
    assert cq.render(doubtful) == f"Birth certificate, in its own words: DATA DO REGISTRO: 03/05/2006 {cq.AMBIGUOUS}."


CODE = re.compile(r"\b[A-Z]{2,}(?:-[A-Z]{2,})+\b|\b[a-z]+_[a-z0-9_]+\b|\b\w+\.\w+\.\w+\b|\bOrg(?:anization)?\d|'naturalidade'|\bI am\b|\bWhen I\b|\bI was\b")


def test_no_code_key_or_first_person_in_a_citation_or_a_label(case):
    for p in cq.passages(case):
        assert not CODE.search(p["cite"]), p["cite"]
        if p["kind"] != "text":  # a document's own words are the document's
            assert not CODE.search(p["says"]), p["says"]
    policy = next(p for p in cq.passages(case) if p["ref"] == "fact:applicant.filing_category")
    assert policy["cite"] == 'Filing category, worked out by the firm\'s standard answer "SIJ filing category"'


# -- the summary for the attorney: the record, no model ---------------------------------------------------------------------------


def test_the_summary_is_the_records_own_lines_draft_and_never_filed(case):
    switch_on()
    before = {p.relative_to(case) for p in case.rglob("*")}
    model = Fake(first)
    s = cq.summary(case, "Ana Attorney", "attorney", model=model)
    assert model.prompts == []  # no model
    assert [p["title"] for p in s["parts"]] == [title for _part, title in cq.SUMMARY_PARTS] and "DRAFT" in s["note"]
    parts = {p["title"]: p["answer"] for p in s["parts"]}
    assert [x["text"] for x in parts["Who the client is"]["sentences"][:3]] == ["Given name: ANA CLARA.", "Family name: EXEMPLO SOUZA.", "Date of birth: 03/14/2006."]
    assert parts["Track and stage"]["sentences"][0]["text"] == "The case is on the Special Immigrant Juvenile track."
    assert [x["text"] for x in parts["What was filed and when"]["sentences"][:2]] == [
        "I-360 approval notice (receipt IOE0999000123): notice dated 08/20/2025.", "I-360 approval notice (receipt IOE0999000123): priority date 02/10/2025."]
    open_lines = [x["text"] for x in parts["What is open"]["sentences"]]
    assert any(t.startswith("Has the client ever violated the terms or conditions") and "the sources disagree" in t for t in open_lines)  # the record's own conflict
    assert any(t.startswith("Open step for the attorney") for t in open_lines)
    assert parts["Decisions made"]["sentences"][0]["text"].startswith("Ana Attorney (attorney) confirmed Family name, Given name on ")
    assert parts["Documents with details assumed"]["sentences"][0]["text"] == "Social Security card: whose it is was assumed: the only person on the case."
    for answer in parts.values():
        for x in answer["sentences"]:
            assert x["sources"] and x["text"].rstrip(".") == x["sources"][0]["says"].rstrip(".") or "/" in x["text"]
    after = {p.relative_to(case) for p in case.rglob("*")}
    assert after - before == {Path(cq.LOG)}
    pdf = cq.summary_pdf(case, s["id"])
    text = " ".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)
    assert "Summary for the attorney (DRAFT)" in text and "From:" in text and "not for filing" in text and "Special Immigrant Juvenile" in text
    with pytest.raises(LookupError):
        cq.summary_pdf(case, "0123456789ab")
    with pytest.raises(LookupError):
        cq.summary_pdf(case, "../../etc")
    for module in ("packet.py", "review/bundle.py", "fill/cover_letter.py"):
        assert "case_questions" not in (REPO / "src" / module).read_text(encoding="utf-8") and "questions.jsonl" not in (REPO / "src" / module).read_text(encoding="utf-8")


def test_a_summary_says_none_recorded_and_how_many_more(seeded, firm):
    switch_on()
    d = firm / "clients" / "demo-ana"
    shutil.copytree(seeded, d)
    docs = json.loads((d / "documents.json").read_text(encoding="utf-8"))
    base = docs["documents"]
    docs["documents"] = [dict(base[i % len(base)], id=f"{i:016x}", files=[f"copy{i}.pdf"], doc_ids=[f"copy{i}.pdf"], issued=f"20{10 + i % 15:02d}-0{1 + i % 9}-1{i % 9}")
                         for i in range(30)]  # thirty documents, each with a date of its own
    (d / "documents.json").write_text(json.dumps(docs), encoding="utf-8")
    s = cq.summary(d, "Ana Attorney", "attorney")
    parts = {p["title"]: p["answer"] for p in s["parts"]}
    assert parts["Decisions made"] == {"lead": None, "sentences": [], "notes": [], "refusal": cq.NONE_RECORDED}
    held = len({cq.render(p) for p in cq.passages(d) if "documents" in p["topics"]})  # each distinct line once
    assert held > cq.SUMMARY_TOP and parts["Documents held"]["notes"] == [cq.MORE.format(n=held - cq.SUMMARY_TOP)]
    assert len(parts["Documents held"]["sentences"]) == cq.SUMMARY_TOP


# -- the log -----------------------------------------------------------------------------------------------------------------------


def test_every_question_is_logged_on_the_case_and_in_the_ledger_without_its_words(case, firm):
    switch_on()
    model = Fake(lambda p: str(number_of(p, "Date of last arrival (I-94)")))
    cq.ask(case, "When did she enter? Her uncle José says 2019-07-15", "Paulo Paralegal", "paralegal", model=model)
    cq.ask(case, "Is she eligible?", "Ana Attorney", "attorney", model=model)
    cq.summary(case, "Ana Attorney", "attorney")
    rows = cq.entries(case)
    assert [r["kind"] for r in rows] == ["question", "question", "summary"]
    one = rows[0]
    assert one["who"] == "Paulo Paralegal" and one["role"] == "paralegal" and one["question"].startswith("When did she enter?")
    assert one["outcome"] == "answered" and one["model"] == "fake-model" and one["chosen"] and one["raw"]
    assert one["passages"] and all(set(p) == {"ref", "cite", "says"} for p in one["passages"])
    assert one["answer"]["sentences"][0]["text"] == "Date of last arrival (I-94): 07/15/2019."
    assert rows[1]["outcome"] == "attorneys_call" and rows[1]["model"] is None
    assert oct(os.stat(case / cq.LOG).st_mode & 0o777) == "0o600"
    ledger = [r for r in events.rows(firm / "ledger" / "events.jsonl") if r["kind"] == "questions"]
    assert [(r["who"], r["role"], r["case"], r["action"]) for r in ledger] == [("Paulo Paralegal", "paralegal", "demo-ana", "asked"), ("Ana Attorney", "attorney", "demo-ana", "asked"),
                                                                              ("Ana Attorney", "attorney", "demo-ana", "summarised")]
    assert re.fullmatch(r"Asked a question about the case: answered with 1 line of the record, chosen from \d+ places", ledger[0]["what"]), ledger[0]["what"]
    assert ledger[1]["what"].startswith("Asked a question about the case: the attorney's call")
    for r in ledger:  # the person's words never reach the ledger: no question text, no date, no number
        assert "uncle" not in r["what"] and "José" not in r["what"] and not RAW.search(r["what"]), r["what"]
    assert events.KINDS["questions"] == {"name": "Questions asked about the case", "firm": False}


# -- off by default, approved, changed -----------------------------------------------------------------------------------------


def test_off_until_an_attorney_switches_it_on_and_approves_the_practice(case, monkeypatch):
    spec = next(s for s in settings.specs() if s["id"] == "drafting")
    assert spec["title"] == "Drafting and models"
    field = next(f for f in spec["fields"] if f["key"] == "case_questions")
    assert field["label"] == "Questions about a case answered by the local model" and field["default"] == "off" and field["value"] == "off"
    assert not cq.is_on() and cq.why_not() == cq.OFF
    with pytest.raises(ValueError, match="Questions about a case are off"):
        cq.ask(case, "When did she enter?", "Paulo Paralegal", "paralegal", model=Fake(first))
    with pytest.raises(ValueError, match="Questions about a case are off"):
        cq.summary(case, "Paulo Paralegal", "paralegal")
    settings.save("drafting", {"case_questions": "on"}, "Ana Attorney")
    assert cq.is_on() and cq.why_not() == cq.NOT_APPROVED
    with pytest.raises(ValueError, match="not approved yet"):
        cq.ask(case, "When did she enter?", "Paulo Paralegal", "paralegal", model=Fake(first))
    approval.approve(cq.PRACTICE_ID, "Ana Attorney", "attorney")
    assert cq.why_not() is None and cq.practice()["state"] == "approved"
    assert cq.ask(case, "When did she enter?", "Paulo Paralegal", "paralegal", model=Fake(first))["outcome"] == "answered"
    with pytest.raises(ValueError, match="Enter your name"):
        cq.ask(case, "When did she enter?", " ", "paralegal", model=Fake(first))
    with pytest.raises(ValueError, match="Type a question"):
        cq.ask(case, "   ", "Paulo Paralegal", "paralegal", model=Fake(first))
    with pytest.raises(ValueError, match="up to 500 characters"):
        cq.ask(case, "x" * 501, "Paulo Paralegal", "paralegal", model=Fake(first))
    monkeypatch.setattr(cq, "PRACTICE", cq.PRACTICE + " Answer in rhyme.")
    approval._catalog.cache_clear()
    try:
        assert cq.practice()["state"] == "changed" and cq.why_not() == cq.CHANGED
        with pytest.raises(ValueError, match="changed since the attorney approved it"):
            cq.ask(case, "When did she enter?", "Paulo Paralegal", "paralegal", model=Fake(first))
    finally:
        monkeypatch.undo()
        approval._catalog.cache_clear()


def test_the_practice_is_in_the_approval_catalog_and_on_the_settings_page(case, tmp_path):
    entry = next(r for r in approval.catalog() if r["id"] == cq.PRACTICE_ID)
    assert entry["kind"] == "practice" and entry["plain_text"] == cq.PRACTICE and entry["name"] == cq.PRACTICE_NAME
    for words in ("numbers only", "NOT IN THE RECORD", "never shows anything you write"):
        assert words in cq.PRACTICE, words
    app = _app(case.parent, tmp_path)
    section = next(s for s in app.settings("attorney")["sections"] if s["id"] == "drafting")
    shown = {p["id"]: p for p in section["practices"]}
    assert shown[cq.PRACTICE_ID]["plain_text"] == cq.PRACTICE and shown[cq.PRACTICE_ID]["state"] == "not_approved" and "PRACTICE:DRAFTING" in shown
    assert cq.prompt([{"says": "A-Number: A099000123"}], "What is her A-Number?") == (
        cq.PRACTICE + "\n\nPassages:\n[1] A-Number: A099000123\n\nQuestion: What is her A-Number?\n")
    for text in (cq.PRACTICE, cq.REFUSAL, cq.ATTORNEYS_CALL, cq.UNCHOSEN, cq.UNREACHABLE, cq.CUT_OFF, cq.OFF, cq.NOT_APPROVED, cq.CHANGED, cq.INTRO,
                 cq.SUMMARY_NOTE, cq.SHORTENED, cq.NONE_RECORDED, cq.MORE, section["help"]):
        assert "—" not in text and " -- " not in text, text


def test_the_pdf_prints_what_latin1_has_and_arrows_in_words():
    assert cq.latin1("Filing packet tab → I-485 packet “ok” São") == 'Filing packet tab to I-485 packet "ok" São'


# -- no other case's record reaches the model ---------------------------------------------------------------------------------


OTHER = {"applicant.given_name": "ZELDA", "applicant.family_name": "OUTRACASA", "applicant.a_number": "A077777777", "applicant.dob": "1999-12-25",
         "applicant.i94_arrival_date": "2018-02-02", "questionnaire.father_name": "OTAVIO OUTRACASA"}


def _other_case(root: Path) -> Path:
    d = root / "case-zelda"
    d.mkdir(parents=True)
    g = FactGraph("case-zelda")
    for key, value in OTHER.items():
        g.add_source(key, "portal questionnaire", "intake_questionnaire", value, value, 0.95)
    g.save(d / "fact_graph.json")
    (d / "meta.json").write_text(json.dumps({"client_id": "case-zelda", "classifications": {}}), encoding="utf-8")
    (d / "documents.json").write_text(json.dumps({"version": 1, "built": None, "documents": [
        {"id": "z1z1z1z1z1z1z1z1", "files": ["z.pdf"], "doc_ids": ["z.pdf"], "pages": [1], "type": "birth_certificate", "person": "applicant",
         "language": "pt", "text": "CERTIDAO DE NASCIMENTO ZELDA OUTRACASA PAI OTAVIO OUTRACASA", "identifiers": {}, "roles": [], "tags": []}]}), encoding="utf-8")
    return d


def test_no_other_cases_record_reaches_the_model(case):
    switch_on()
    _other_case(case.parent)
    model = Fake(first)
    for question in ("When did she enter?", "Which documents mention her father?", "What is her A-Number?", "What is her date of birth?"):
        cq.ask(case, question, "Paulo Paralegal", "paralegal", model=model)
    assert len(model.prompts) == 4
    sent = "\n".join(model.prompts)
    for value in ("ZELDA", "OUTRACASA", "077777777", "12/25/1999", "02/02/2018", "OTAVIO", "case-zelda"):
        assert value not in sent, value
    model = Fake(first)
    cq.ask(case.parent / "case-zelda", "When did she enter?", "Paulo Paralegal", "paralegal", model=model)
    assert "02/02/2018" in model.prompts[0] and "EXEMPLO" not in model.prompts[0] and "07/15/2019" not in model.prompts[0]
    refs = {p["ref"] for p in cq.passages(case)}
    for row in cq.entries(case):
        for p in row.get("passages") or []:
            assert p["ref"] in refs


# -- through the review app: the routes and the restricted gate ---------------------------------------------------------------


def _app(root: Path, tmp_path: Path, accounts=None):
    from review.server import ReviewApp

    return ReviewApp(root, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, accounts=accounts,
                     views_log=tmp_path / "views.jsonl")


@pytest.fixture
def served(case, tmp_path, monkeypatch):
    """The review app over the demo case and a VAWA case (restricted by law); a paralegal named on neither, a second paralegal named on
    the VAWA case, an attorney; the model faked inside the app (the one call)."""
    import restricted
    import second_factor
    from review.auth import Accounts
    from review.server import make_handler, serve
    from test_restricted import PASSWORD

    root = case.parent
    vawa = shutil.copytree(case, root / "case-vawa")
    (vawa / cq.LOG).unlink(missing_ok=True)
    meta = json.loads((vawa / "meta.json").read_text(encoding="utf-8"))
    (vawa / "meta.json").write_text(json.dumps(meta | {"client_id": "case-vawa"}), encoding="utf-8")
    (vawa / "status.json").write_text(json.dumps({"filings": [{"filing": "vawa", "title": "I-360 VAWA self-petition", "mailed_on": "2026-09-01",
                                                                "carrier": "USPS", "tracking": "", "by": "Paulo Paralegal"}]}), encoding="utf-8")
    accounts = Accounts(tmp_path / "staff.json")
    for email, name, role in (("jane@firm.example", "Jane Doe", "paralegal"), ("kim@firm.example", "Kim Exemplo", "paralegal"),
                              ("sam@firm.example", "Sam Attorney", "attorney")):
        accounts.change_password(email, accounts.add(email, name, role), PASSWORD)
    second_factor.set_up(accounts, "sam@firm.example", PASSWORD)
    app = _app(root, tmp_path, accounts)
    restricted.name_person(vawa, "kim@firm.example", True, "Sam Attorney", "attorney", "Kim Exemplo")
    monkeypatch.setattr(cq, "ask_model", Fake(first))
    switch_on()
    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def test_the_routes_answer_and_a_restricted_case_is_closed_like_everywhere(served, case):
    from review import server as srv
    from test_restricted import call, sign_in

    jane, kim, sam = sign_in(served, "jane@firm.example"), sign_in(served, "kim@firm.example"), sign_in(served, "sam@firm.example")
    for route in ("/api/case-questions", "/api/case-summary.pdf"):
        assert route in srv.CASE_GET
    for route in ("/api/case-question", "/api/case-summary"):
        assert route in srv.CASE_POST
    status, text = call(served + "/api/case-question", jane, {"client": "demo-ana", "question": "When did she enter?"})
    a = json.loads(text)
    assert status == 200 and a["who"] == "Jane Doe" and a["answer"]["sentences"] and all(s["sources"] for s in a["answer"]["sentences"])
    panel = json.loads(call(served + "/api/case-questions?client=demo-ana", jane)[1])
    assert panel["on"] and panel["why_not"] is None and panel["history"][0]["question"] == "When did she enter?" and panel["total"] == 1
    s = json.loads(call(served + "/api/case-summary", sam, {"client": "demo-ana"})[1])
    assert s["kind"] == "summary" and s["parts"]
    status, pdf = call(served + f"/api/case-summary.pdf?client=demo-ana&id={s['id']}", sam)
    assert status == 200 and pdf.startswith("%PDF")
    for route in ("/api/case-questions", "/api/case-summary.pdf"):
        hidden = call(served + route + "?client=case-vawa&id=" + s["id"], jane)
        made_up = call(served + route + "?client=" + quote("Nobody Here, Ç") + "&id=" + s["id"], jane)
        assert hidden == made_up and hidden[0] == 404 and json.loads(hidden[1]) == srv.UNKNOWN, (route, hidden, made_up)
    for route in ("/api/case-question", "/api/case-summary"):
        hidden = call(served + route, jane, {"client": "case-vawa", "question": "When did she enter?"})
        made_up = call(served + route, jane, {"client": "nobody-here", "question": "When did she enter?"})
        assert hidden == made_up and hidden[0] == 404 and json.loads(hidden[1]) == srv.UNKNOWN, (route, hidden, made_up)
    assert not (case.parent / "case-vawa" / cq.LOG).exists()
    assert call(served + "/api/case-question", kim, {"client": "case-vawa", "question": "When did she enter?"})[0] == 200
    assert call(served + "/api/case-questions?client=case-vawa", sam)[0] == 200
    settings.save("drafting", {"case_questions": "off"}, "Sam Attorney")
    status, text = call(served + "/api/case-question", sam, {"client": "demo-ana", "question": "When did she enter?"})
    assert status == 400 and json.loads(text)["error"] == cq.OFF
    assert json.loads(call(served + "/api/case-questions?client=demo-ana", sam)[1])["why_not"] == cq.OFF


# -- the model's budget -----------------------------------------------------------------------------------------------------------


def test_a_question_has_its_own_time_budget_never_doubled_and_a_cut_off_answer_says_so(monkeypatch, case):
    import drafting
    from vision import ollama

    tried = []
    monkeypatch.setattr(ollama, "_is_wsl", lambda: True)
    monkeypatch.setattr(ollama, "_post_via_windows_curl", lambda *a: tried.append(a) or {"response": "x"})

    def slow(url, payload, timeout):
        tried.append(("http", timeout))
        raise TimeoutError("timed out")

    monkeypatch.setattr(ollama, "_post_http", slow)
    with pytest.raises(TimeoutError):
        cq.ask_model("a prompt")
    assert tried == [("http", cq.QUESTION_TIMEOUT)]
    monkeypatch.setattr(ollama, "_post_http", lambda url, payload, timeout: {"response": "1, 2", "done_reason": "length"})
    with pytest.raises(drafting.ModelCutOff):
        cq.ask_model("a prompt")
    assert drafting.smooth_with_model("a paragraph") == ("", drafting.os.environ.get("DRAFTING_MODEL", ollama.DEFAULT_MODEL))
    switch_on()

    def cut(prompt):
        raise drafting.ModelCutOff("cut")

    a = cq.ask(case, "When did she enter?", "Paulo Paralegal", "paralegal", model=cut)
    assert a["outcome"] == "cut_off" and a["answer"]["refusal"] == cq.CUT_OFF


# -- the real local model (only when it is up) ------------------------------------------------------------------------------------


@pytest.mark.skipif(os.environ.get("I485_OLLAMA") != "1", reason="asks the local model on this machine: I485_OLLAMA=1 runs it")
def test_the_local_model_chooses_passages_for_three_questions_of_the_demo_case(case):
    switch_on()
    items = {p["ref"]: p for p in cq.passages(case)}
    for question in ("When did she enter?", "Which documents mention her father?", "What did the attorney decide about her name?"):
        a = cq.ask(case, question, "Ana Attorney", "attorney")
        row = cq.entries(case)[-1]
        assert a["outcome"] == "answered" and a["answer"]["sentences"], (question, row["raw"])
        for s in a["answer"]["sentences"]:  # each line is a real passage of this case's record, as the product renders it
            src = s["sources"][0]
            assert src["ref"] in items and s["text"] == cq.render(items[src["ref"]]) and src["cite"] == items[src["ref"]]["cite"], s
