"""An explanation in Part 14 for every Yes the form says to explain (src/part14_explain.py, brief L2): the list and the lines it rests on, the
packet's gate per item, each item's suggestion from made-up facts with every slot cited, a missing or unconfirmed fact left as a visible blank, no
citation without the statute's text, both voices, the card's edit, approval, Undo and the approval taken back by a change, the approved text on
the form's own Part 14 page, the review bundle, the ledger in words, and a model never called to write. Everyone here is made up (the Exemplo
family); every receipt and A-Number is invented."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from pypdf import PdfReader

import clock
import drafting
import events
import packet
import part14_explain as px
import part14_voice
import rules
import settings
from extract import uscis_notice
from factgraph import FactGraph
from fill import load_field_map
from fill import where
from fill.continuation import find_page
from review.state import load_decision_log, refill, reviewed_graph, undo_decision
from rules import approval
from rules.engine import Rule
import schema_path

REPO = Path(__file__).resolve().parent.parent

TEMPLATE = schema_path.path("template", "i485")
FIELD_MAP = load_field_map(schema_path.path("field_map", "i485"))
EDITION = where.edition_of()

NTA = ("nta.pdf", "notice_to_appear", 1)
I360 = ("i360-approval.pdf", "uscis_notice", 1)
EAD = ("ead-approval.pdf", "uscis_notice", 1)
I94 = ("i94.pdf", "i94", 1)
CLIENT = ("questionnaire.pdf", "intake_questionnaire", 3)
RECEIPT = "IOE0912345678"  # invented
K = {name: f"applicant.part9.{name}" for name in ("worked_without_authorization", "in_removal_proceedings", "final_order_of_removal",
                                                   "applied_relief_from_removal", "arrested_cited_charged_detained", "pt9line75",
                                                   "unlawfully_present_since_1997", "committed_crime")}
NAME = {"applicant.family_name": ("EXEMPLO SOUZA", *NTA), "applicant.given_name": ("ANA CLARA", *NTA), "applicant.a_number": ("A099000111", *NTA),
        "applicant.dob": ("2007-04-02", *NTA)}
ARRIVED = {"applicant.nta_present": ("Yes", *NTA), "applicant.last_arrival_city": ("HIDALGO", *NTA), "applicant.last_arrival_state": ("TX", *NTA),
           "applicant.last_arrival_date": ("2019-06-12", *NTA), "applicant.last_arrival_manner": ("WITHOUT ADMISSION OR PAROLE", *NTA)}
APPROVED_I360 = {f"folder.uscis_case.{RECEIPT}.approval_20250501": ("I-360 APPROVAL (class SL6, Special Immigrant-Juvenile), 2025-05-01", *I360),
                 f"folder.notice.{RECEIPT}.approval_20250501.date": ("2025-05-01", *I360)}
JOBS = {"questionnaire.prior_employer1_employer": ("CAFE EXEMPLO", *CLIENT), "questionnaire.prior_employer1_country": ("USA", *CLIENT),
        "questionnaire.prior_employer1_date_from": ("2021-03-01", *CLIENT), "questionnaire.prior_employer1_date_to": ("2023-08-31", *CLIENT),
        "questionnaire.prior_employer2_employer": ("LOJA EXEMPLO", *CLIENT), "questionnaire.prior_employer2_state": ("MA", *CLIENT),
        "questionnaire.prior_employer2_date_from": ("2024-01-01", *CLIENT),
        "questionnaire.prior_employer3_employer": ("ESCOLA EXEMPLO", *CLIENT), "questionnaire.prior_employer3_occupation": ("STUDENT", *CLIENT),
        "questionnaire.prior_employer3_country": ("USA", *CLIENT), "questionnaire.prior_employer3_date_from": ("2019-09-01", *CLIENT)}
PERMIT = {"folder.uscis_case.IOE0998877665.approval_20240301": ("I-765 APPROVAL, 2024-03-01", *EAD),
          "folder.notice.IOE0998877665.approval_20240301.valid_from": ("2024-03-01", *EAD),
          "folder.notice.IOE0998877665.approval_20240301.valid_to": ("2026-02-28", *EAD)}
def hearing(day: str, outcome: str, decided: str | None = None) -> dict:
    """A hearing on the case page with what happened at it, as src/journey.py records it (a decision carries its date; a termination does not)."""
    result = {"outcome": outcome, "text": None, "by": "Paula Paralegal", "at": "2026-09-01T09:05:00-04:00"}
    if decided:
        result |= {"decision_date": decided, "written": False, "asylum": False, "appeal_waived": False}
    return {"id": f"hearing.{day}.0", "date": day, "kind": "Master calendar hearing", "court": "BOSTON", "by": "Paula Paralegal",
            "at": "2026-09-01T09:00:00-04:00", "result": result}


def court(*hearings: dict) -> dict:
    return {"journey": {"hearings": list(hearings)}}


ABSENTIA = court(hearing("2025-02-10", "Removal ordered in absentia", "2025-02-10"))
ORDERED = ABSENTIA
ORDER_OR_DENIED = court(hearing("2025-02-10", "Decision: removal ordered or relief denied", "2025-02-10"))
GRANTED = court(hearing("2025-02-10", "Decision: relief granted", "2025-02-10"))
TERMINATED = court(hearing("2025-03-03", "Case terminated or dismissed"))
ABSENTIA_THEN_TERMINATED = court(hearing("2019-01-10", "Removal ordered in absentia", "2019-01-10"), hearing("2025-03-03", "Case terminated or dismissed"))
PRE = "Yes, I was placed in removal proceedings before the immigration court."
SIJ = ("I am applying to adjust status based on my approved Special Immigrant Juvenile petition (Form I-360, receipt number IOE0912345678), "
       "approved on 05/01/2025.")


@pytest.fixture
def firm(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "portal"))
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))
    return tmp_path


def make_case(root: Path, facts: dict, status: dict | None = None, name: str = "case-exemplo", nta: bool = True) -> Path:
    d = root / "clients" / name
    d.mkdir(parents=True, exist_ok=True)
    g = FactGraph(name)
    for key, (value, doc, doc_type, tier) in facts.items():
        g.add_source(key, doc, doc_type, str(value), value, 0.95, tier=tier)
    g.save(d / "fact_graph.json")
    (d / "meta.json").write_text(json.dumps({"client_id": name, "classifications": {"nta.pdf": "notice_to_appear"} if nta else {}}), encoding="utf-8")
    if status is not None:
        (d / "status.json").write_text(json.dumps(status), encoding="utf-8")
    return d


def yes(*names: str) -> dict:
    return {K[n]: ("Yes", *CLIENT) for n in names}


def entry(case: Path, name: str) -> dict:
    return next(e for e in px.view(case, "attorney")["entries"] if e["key"] == K[name])


def ledger(root: Path) -> list[dict]:
    return list(events.rows(events.base_path()))


# -- the list and the lines it rests on -------------------------------------------------------------------------------------------------


def _page_text(page: int) -> str:
    reader = PdfReader(str(TEMPLATE))
    return " ".join((reader.pages[page - 1].extract_text() or "").split())


def test_every_listed_item_rests_on_a_line_the_form_prints_on_its_page():
    spec = px.held(EDITION)
    assert spec is not None and EDITION == "09/18/26"
    for item in spec["items"]:
        assert item["lines"], f"{item['key']} is listed with no line it rests on"
        for lid in item["lines"]:
            assert lid in spec["lines"], f"{item['key']}: no such line {lid}"
    for lid, line in spec["lines"].items():
        assert line["says"] and line["page"] and line["in"] in ("form", "instructions")
        if line["in"] == "form":  # the form's own page says it, word for word
            assert line["says"] in _page_text(line["page"]), f"{lid} is not printed on page {line['page']}"
    assert spec["instructions"]["read"] == "2026-10-04" and spec["instructions"]["url"].startswith("https://www.uscis.gov/")
    for n in spec["not_filled"]:
        assert n["lines"] and n["question"][:30] in _page_text(n["page"])


def test_the_list_is_every_part9_yes_no_answer_the_map_fills_numbered_by_the_edition():
    fills = {k for k, v in json.loads((schema_path.path("field_map", "i485")).read_text(encoding="utf-8"))["fact_to_acroform"].items()
             if k.startswith("applicant.part9.") and v.get("type") == "yes_no"}
    items = px.listed()
    assert {x["key"] for x in items} == fills  # the form's page 13: a Yes to ANY question in Part 9 is explained
    by = {x["key"]: x for x in items}
    assert (by[K["pt9line75"]]["page"], by[K["pt9line75"]]["part"], by[K["pt9line75"]]["item"]) == ("20", "9", "73")  # not the older edition's 75
    assert (by[K["unlawfully_present_since_1997"]]["page"], by[K["unlawfully_present_since_1997"]]["item"]) == ("20", "74")
    assert by[K["worked_without_authorization"]]["item"] == "12" and by[K["in_removal_proceedings"]]["item"] == "14"
    assert by[K["final_order_of_removal"]]["item"] == "15" and by[K["applied_relief_from_removal"]]["item"] == "18"
    assert by[K["arrested_cited_charged_detained"]]["item"] == "22"
    for x in items:
        n = int(re.match(r"\d+", x["item"]).group(0))
        ids = [line["id"] for line in x["lines"]]
        assert ids[0] == "form_p13"
        assert ("form_p14_22_41" in ids) == (22 <= n <= 41) and ("form_p20_74" in ids) == (n == 74) and ("form_p20_65" in ids) == (n == 65)
        assert ("form_p16_42_45" in ids) == (42 <= n <= 45) and ("form_p17_47_55" in ids) == (47 <= n <= 55)
    lines = by[K["unlawfully_present_since_1997"]]["lines"]
    assert lines[1]["where"] == "Form I-485, page 20" and "give the dates of unlawful presence" in lines[1]["says"]


def test_the_wordings_file_redumps_byte_for_byte_and_every_wording_has_both_voices():
    raw = (schema_path.path("firm", "part14_wordings")).read_text(encoding="utf-8")
    data = json.loads(raw)
    assert json.dumps(data, indent=2, ensure_ascii=False) + "\n" == raw
    listed = {x["key"] for x in data["forms"]["i485"]["editions"]["09/18/26"]["items"]}
    for w in data["wordings"] + list(data["sentences"].values()):
        assert set(w["text"]) == {"client", "office"} and w["when"] in data["when"]
        for voice, text in w["text"].items():
            assert not re.search(r"—| -- ", text)
            assert all(m in data["slots"] or m == "cite" for m in re.findall(r"\{(\w+)\}", text))
            assert text.startswith("Yes, ") == (voice == "client" and "item" in w)
    assert all(w["item"] in listed for w in data["wordings"])


def test_the_attorney_reads_every_wording_in_both_voices_and_every_line():
    review = " ".join((REPO / "docs" / "attorney_review.md").read_text(encoding="utf-8").split())
    data = px.shipped()
    for w in data["wordings"] + list(data["sentences"].values()):
        for text in w["text"].values():
            assert " ".join(text.split()) in review, text
    for line in px.held(EDITION)["lines"].values():
        assert " ".join(line["says"].split()) in review
    assert "245(g)" in review  # the citation the owner's example used, which the product does not copy: for the attorney to settle


def test_the_work_permits_first_day_is_read_from_its_approval_notice():
    text = ("Receipt Number Case Type\nIOE0998877665 I765 - APPLICATION FOR EMPLOYMENT AUTHORIZATION\nApproval Notice\n"
            "Notice Date\n03/01/2024\nValid from 03/01/2024 to 02/28/2026\n")
    n = uscis_notice.parse(text)
    assert n.form == "I-765" and (n.valid_from, n.valid_to) == ("2024-03-01", "2026-02-28")
    keys = {f.fact_key.rsplit(".", 1)[-1]: f.normalized_value for f in uscis_notice.extract(text)}
    assert keys["valid_from"] == "2024-03-01" and keys["valid_to"] == "2026-02-28"


# -- the gate ---------------------------------------------------------------------------------------------------------------------------------


def test_a_yes_without_an_approved_explanation_holds_the_packet_one_line_per_item(firm):
    case = make_case(firm, NAME | ARRIVED | yes("in_removal_proceedings", "pt9line75", "committed_crime"))
    assert px.problems(case) == ["Part 9, item 14 says Yes and has no explanation in Part 14.",
                                 "Part 9, item 23 says Yes and has no explanation in Part 14.",
                                 "Part 9, item 73 says Yes and has no explanation in Part 14."]
    refill(case, FIELD_MAP, TEMPLATE)
    plan = packet.plan(case, {"summary": {}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}, packet.load_filing("i485"))
    assert "Part 9, item 73 says Yes and has no explanation in Part 14." in plan["problems"] and not plan["ready"]
    px.approve(case, K["in_removal_proceedings"], "Sam Attorney", "attorney")
    px.approve(case, K["pt9line75"], "Sam Attorney", "attorney")
    px.edit(case, K["committed_crime"], "Yes, I was cited for driving without a license in Boston, Massachusetts on 03/03/2024. The charge was dismissed.",
            "Paula Paralegal", "paralegal")
    assert px.problems(case) == ["Part 9, item 23 says Yes and has no explanation in Part 14."]
    px.approve(case, K["committed_crime"], "Sam Attorney", "attorney")
    assert px.problems(case) == []
    plan = packet.plan(case, {"summary": {}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}, packet.load_filing("i485"))
    assert not any("Part 9, item" in p for p in plan["problems"])


def test_a_no_answer_asks_for_nothing(firm):
    case = make_case(firm, NAME | {K["in_removal_proceedings"]: ("No", *CLIENT), K["pt9line75"]: ("No", *CLIENT)})
    assert px.problems(case) == [] and px.view(case)["entries"] == []


def test_an_edition_whose_lines_are_not_held_is_said_not_passed(firm, monkeypatch):
    case = make_case(firm, NAME | yes("in_removal_proceedings"))
    monkeypatch.setattr(px, "edition", lambda: "01/01/30")
    assert px.problems(case) == ["The lines that say which answers need an explanation in Part 14 are not held for the 01/01/30 edition of Form I-485 "
                                 "yet: the explanations cannot be checked until they are."]


# -- the suggestion, item by item -------------------------------------------------------------------------------------------------------------


def _cited(e: dict) -> None:
    """Every slot that is filled has a source in words."""
    for s in e["suggestion"]["slots"]:
        if s["value"] or s["proposed"]:
            assert s["sources"] and all(x["words"] for x in s["sources"]), s


def test_items_14_and_15_from_an_in_absentia_order_on_the_court_record_and_the_i360_approval(firm):
    case = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("in_removal_proceedings", "final_order_of_removal"), ABSENTIA)
    e14, e15 = entry(case, "in_removal_proceedings"), entry(case, "final_order_of_removal")
    assert e14["text"] == PRE + " On 02/10/2025, the immigration judge ordered me removed in absentia. " + SIJ
    assert e15["text"] == "Yes, the immigration judge ordered me removed in absentia on 02/10/2025. " + SIJ
    assert e14["suggestion"]["wording"] == "proceedings_ordered_in_absentia" and e14["suggestion"]["sentences"] == ["proceedings_ordered_in_absentia", "sij_basis"]
    order = next(s for s in e14["suggestion"]["slots"] if s["name"] == "decision_date")
    assert order["sources"][0]["words"] == ("The court record on the case page: Removal ordered in absentia, decided 02/10/2025, at the hearing of 02/10/2025; "
                                            "recorded by Paula Paralegal on 09/01/2026")
    receipt = next(s for s in e14["suggestion"]["slots"] if s["name"] == "i360_receipt")
    assert receipt["sources"] == [{"words": "The I-360 approval notice", "doc": "i360-approval.pdf", "page": None}]
    _cited(e14)
    assert (e14["page"], e14["part"], e14["item"]) == ("14", "9", "14") and not e14["blanks"] and e14["state"] == "draft"


def test_removal_ordered_or_relief_denied_is_one_outcome_so_what_the_judge_decided_is_a_blank(firm):
    case = make_case(firm, NAME | ARRIVED | yes("in_removal_proceedings", "final_order_of_removal"), ORDER_OR_DENIED)
    blank = "[what the judge decided: ordered removal, or denied relief (say which)]"
    assert entry(case, "in_removal_proceedings")["text"] == PRE + f" On 02/10/2025, the immigration judge {blank}."
    e15 = entry(case, "final_order_of_removal")
    assert e15["text"] == f"Yes, on 02/10/2025, the immigration judge {blank}." and e15["state"] == "blanks"
    with pytest.raises(ValueError, match="Fill every blank in square brackets"):
        px.approve(case, K["final_order_of_removal"], "Sam Attorney", "attorney")


def test_a_terminated_case_is_never_called_pending_and_its_date_is_a_blank(firm):
    case = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("in_removal_proceedings"), TERMINATED)
    e = entry(case, "in_removal_proceedings")
    assert "pending" not in e["text"]
    assert e["text"] == (PRE + " On [the date the proceedings were terminated or dismissed], the immigration judge [terminated or dismissed (say which)] "
                         "the proceedings. " + SIJ)
    assert e["suggestion"]["wording"] == "proceedings_ended"
    how = next(s for s in e["suggestion"]["slots"] if s["name"] == "how_ended")
    assert how["value"] is None and how["sources"][0]["words"].startswith("The court record on the case page: Case terminated or dismissed, no date recorded")


def test_relief_granted_is_never_called_pending(firm):
    case = make_case(firm, NAME | ARRIVED | yes("in_removal_proceedings"), GRANTED)
    assert entry(case, "in_removal_proceedings")["text"] == PRE + " On 02/10/2025, the immigration judge granted [the relief the judge granted]."


def test_an_order_and_a_later_termination_are_written_by_a_person_from_the_record(firm):
    case = make_case(firm, NAME | ARRIVED | yes("in_removal_proceedings", "final_order_of_removal"), ABSENTIA_THEN_TERMINATED)
    blank = "[what the immigration court decided, and when, as its orders say]"
    e14, e15 = entry(case, "in_removal_proceedings"), entry(case, "final_order_of_removal")
    assert e14["text"] == PRE + f" {blank}." and e15["text"] == f"Yes, {blank}."
    history = e14["suggestion"]["slots"][0]
    assert [x["words"].split(";")[0] for x in history["sources"]] == [
        "The court record on the case page: Removal ordered in absentia, decided 01/10/2019, at the hearing of 01/10/2019",
        "The court record on the case page: Case terminated or dismissed, no date recorded, at the hearing of 03/03/2025"]


def test_item_14_pending_only_while_the_client_is_in_proceedings(firm):
    case = make_case(firm, NAME | ARRIVED | yes("in_removal_proceedings", "final_order_of_removal"))  # a Notice to Appear, nothing on the case page
    assert entry(case, "in_removal_proceedings")["text"] == ("Yes, I was placed in removal proceedings before the immigration court, and those "
                                                             "proceedings are pending.")
    e15 = entry(case, "final_order_of_removal")  # Yes to a final order with none on the case page: a blank for a person
    assert e15["text"] == "Yes, [what the immigration court decided, and when, as its orders say]." and e15["state"] == "blanks"
    with pytest.raises(ValueError, match="Fill every blank in square brackets"):
        px.approve(case, K["final_order_of_removal"], "Sam Attorney", "attorney")
    other = make_case(firm, NAME | yes("in_removal_proceedings"), name="case-other", nta=False)  # the client's Yes alone: no notice, no hearing
    assert entry(other, "in_removal_proceedings")["text"] == PRE + " [what the immigration court decided, and when, as its orders say]."


def test_item_12_the_periods_worked_outside_the_permits_dates_computed_and_cited(firm):
    case = make_case(firm, NAME | JOBS | PERMIT | yes("worked_without_authorization"))
    e = entry(case, "worked_without_authorization")
    slot = e["suggestion"]["slots"][0]
    periods = "03/01/2021 to 08/31/2023 (CAFE EXEMPLO); 01/01/2024 to 02/29/2024 (LOJA EXEMPLO); 03/01/2026 to the present (LOJA EXEMPLO)"
    assert slot["proposed"] == periods and slot["value"] is None  # the client's own job history: a person checks it and uses it
    assert e["text"] == ("Yes, I worked in the United States without employment authorization during these periods: "
                         "[the periods worked in the United States outside the work permit's dates].")
    words = [s["words"] for s in slot["sources"]]
    assert "The client's job history, line 1: CAFE EXEMPLO, 03/01/2021 to 08/31/2023" in words
    assert "The client's job history, line 3: ESCOLA EXEMPLO, 09/01/2019 to the present (not work)" in words  # a school is not work
    assert "A work permit approval notice: valid 03/01/2024 to 02/28/2026" in words
    # the paralegal uses the periods: the text is theirs, recorded
    px.edit(case, K["worked_without_authorization"], e["text"].replace("[the periods worked in the United States outside the work permit's dates]", periods),
            "Paula Paralegal", "paralegal")
    e = entry(case, "worked_without_authorization")
    assert e["how"] == "edited" and periods in e["text"] and e["state"] == "draft"


def test_item_12_a_single_day_outside_the_permit_is_said_once(firm):
    one_day = {"questionnaire.prior_employer1_employer": ("CAFE EXEMPLO", *CLIENT), "questionnaire.prior_employer1_country": ("USA", *CLIENT),
               "questionnaire.prior_employer1_date_from": ("2024-02-29", *CLIENT), "questionnaire.prior_employer1_date_to": ("2025-01-31", *CLIENT)}
    case = make_case(firm, NAME | one_day | PERMIT | yes("worked_without_authorization"))
    assert entry(case, "worked_without_authorization")["suggestion"]["slots"][0]["proposed"] == "02/29/2024 only (CAFE EXEMPLO)"


def test_item_12_a_card_with_only_its_last_day_asks_for_the_periods(firm):
    case = make_case(firm, NAME | JOBS | {"applicant.ead_expiration_date": ("2026-02-28", "ead.pdf", "work_permit", 1)} | yes("worked_without_authorization"))
    slot = entry(case, "worked_without_authorization")["suggestion"]["slots"][0]
    assert slot["value"] is None and slot["proposed"] is None and "shows only its last day (02/28/2026)" in slot["why"]


def test_item_18_from_the_i360_and_item_22_from_the_notice_and_the_arrival(firm):
    case = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("applied_relief_from_removal", "arrested_cited_charged_detained"))
    assert entry(case, "applied_relief_from_removal")["text"] == ("Yes, I applied for classification as a Special Immigrant Juvenile (Form I-360, receipt "
                                                                  "number IOE0912345678), and the petition was approved on 05/01/2025.")
    e22 = entry(case, "arrested_cited_charged_detained")
    # the arrival is the notice's; the detention is no document's (a notice can be issued years after the entry), and any other arrest,
    # citation, charge or detention is the client's to tell: both stay blanks, so it cannot be approved as it stands
    assert e22["text"] == ("Yes, on or about 06/12/2019, I entered the United States at or near HIDALGO, TX, and [what happened then, as the client tells it "
                           "(for example, detained by U.S. immigration officials, for how long, and released)]. [any other arrest, citation, charge or "
                           "detention, with where, when and its outcome, or that there was none].")
    filled = {s["name"]: s["sources"][0]["doc"] for s in e22["suggestion"]["slots"] if s["value"]}
    assert filled == {"arrival_city": "nta.pdf", "arrival_state": "nta.pdf", "arrival_date": "nta.pdf"}
    assert len(e22["blanks"]) == 2 and e22["state"] == "blanks" and "detained by U.S. immigration officials after" not in e22["text"]
    _cited(e22)
    with pytest.raises(ValueError, match="Fill every blank in square brackets"):
        px.approve(case, K["arrested_cited_charged_detained"], "Sam Attorney", "attorney")
    # the person writes what happened and the rest: then the attorney approves it
    done = e22["text"].replace(e22["blanks"][0], "I was held by Border Patrol for two days and released").replace(
        e22["blanks"][1] + ".", "I was cited for driving without a license in Boston on 03/03/2024; the charge was dismissed.")
    px.edit(case, K["arrested_cited_charged_detained"], done, "Paula Paralegal", "paralegal")
    px.approve(case, K["arrested_cited_charged_detained"], "Sam Attorney", "attorney")


def test_item_22_without_the_border_pattern_gets_no_suggestion(firm):
    case = make_case(firm, NAME | yes("arrested_cited_charged_detained"))
    assert entry(case, "arrested_cited_charged_detained")["suggestion"] is None


def test_no_wording_ships_for_an_item_the_firm_has_none_for_a_person_writes_it(firm):
    case = make_case(firm, NAME | yes("committed_crime"))
    e = entry(case, "committed_crime")
    assert e["suggestion"] is None and e["text"] == "" and e["state"] == "empty" and e["how"] == "none"
    assert [line["id"] for line in e["lines"]] == ["form_p13", "form_p14_22_41"]  # the card shows what the explanation must say
    with pytest.raises(ValueError, match="no explanation to approve"):
        px.approve(case, K["committed_crime"], "Sam Attorney", "attorney")


def test_items_73_and_74_from_the_settled_arrival_and_the_i360(firm):
    case = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("pt9line75", "unlawfully_present_since_1997"))
    e73 = entry(case, "pt9line75")
    assert e73["item"] == "73" and e73["page"] == "20"
    assert e73["text"] == ("Yes, I entered the United States without being inspected and admitted or paroled, at or near HIDALGO, TX on or about "
                           "06/12/2019. " + SIJ)
    assert entry(case, "unlawfully_present_since_1997")["text"] == ("Yes, I have been in the United States without being admitted or paroled since I "
                                                                    "entered on or about 06/12/2019.")


def test_item_74_after_an_admission_from_the_i94(firm):
    case = make_case(firm, NAME | {"applicant.i94_admit_until_date": ("2018-12-20", *I94), "applicant.last_arrival_manner": ("ADMITTED", *I94)}
                     | yes("unlawfully_present_since_1997"))
    assert entry(case, "unlawfully_present_since_1997")["text"] == "Yes, I remained in the United States after my authorized stay ended on 12/20/2018."


def test_item_74_never_says_a_stay_ended_on_something_that_is_not_a_date(firm):
    case = make_case(firm, NAME | {"applicant.i94_admit_until_date": ("D/S", *CLIENT), "applicant.last_arrival_manner": ("ADMITTED", *CLIENT)}
                     | yes("unlawfully_present_since_1997"))
    assert entry(case, "unlawfully_present_since_1997")["suggestion"] is None  # "duration of status" has no last day: a person writes it


def test_the_clients_own_arrival_is_a_blank_until_a_person_confirms_it(firm):
    case = make_case(firm, NAME | {"applicant.last_arrival_city": ("LAREDO", *CLIENT), "applicant.last_arrival_state": ("TX", *CLIENT),
                                   "applicant.last_arrival_date": ("2019-06-12", *CLIENT), "applicant.last_arrival_manner": ("WITHOUT ADMISSION OR PAROLE", *CLIENT)}
                     | yes("pt9line75"))
    e = entry(case, "pt9line75")
    assert e["text"] == ("Yes, I entered the United States without being inspected and admitted or paroled, at or near [the city of the last arrival], "
                         "[the state of the last arrival] on or about [the date of the last arrival].")
    city = next(s for s in e["suggestion"]["slots"] if s["name"] == "arrival_city")
    assert city["proposed"] == "LAREDO" and city["sources"][0]["words"] == "The client's own answer" and "not confirmed by a person" in city["why"]


def test_an_explanation_that_cites_the_arrival_waits_while_the_arrival_card_is_open(firm):
    disputed = {"applicant.nta_present": ("Yes", *NTA), "nta.arrival_city": ("HIDALGO", *NTA), "nta.arrival_state": ("TX", *NTA),
                "applicant.last_arrival_city": ("LAREDO", *CLIENT), "applicant.last_arrival_state": ("TX", *CLIENT),
                "applicant.last_arrival_manner": ("WITHOUT ADMISSION OR PAROLE", *NTA)}
    case = make_case(firm, NAME | disputed | yes("pt9line75"))
    e = entry(case, "pt9line75")
    assert e["state"] == "waiting"
    assert e["waits"] == ["The card \"Last arrival in the U.S.: the client's answer differs from the government paper\" is open: save it first."]  # once
    px.edit(case, K["pt9line75"], "Yes, I entered the United States without inspection at or near HIDALGO, TX.", "Paula Paralegal", "paralegal")
    with pytest.raises(ValueError, match="save it first"):
        px.approve(case, K["pt9line75"], "Sam Attorney", "attorney")


def test_a_misread_state_on_the_notice_waits_for_the_card_that_confirms_the_place(firm):
    misread = {"applicant.nta_present": ("Yes", *NTA), "nta.arrival_city": ("SAN LUIS", *NTA), "nta.arrival_state": ("AL", *NTA),
               "applicant.last_arrival_manner": ("WITHOUT ADMISSION OR PAROLE", *NTA)}
    case = make_case(firm, NAME | misread | yes("pt9line75"), name="case-misread")
    e = entry(case, "pt9line75")
    assert e["state"] == "waiting"
    assert e["waits"] == ["The card \"Last arrival in the U.S.: confirm the place the notice prints\" is open: save it first."]


# -- citations, voices, the model ------------------------------------------------------------------------------------------------------------


def test_no_citation_without_the_statutes_text_in_the_products_rules(firm, monkeypatch):
    case = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("pt9line75"))
    cite = entry(case, "pt9line75")["suggestion"]["cite"]
    assert cite == {"held": False, "needed": "the section of the law under which a Special Immigrant Juvenile adjusts status",
                    "note": "the attorney adds the citation"}
    assert "INA" not in entry(case, "pt9line75")["text"]
    # a rule with no text cites nothing either
    monkeypatch.setattr(rules, "ALL_RULES", rules.ALL_RULES + [Rule("SIJ-BASIS", [], [], lambda g: {}, statute_cite="Statute 1(a)")])
    assert entry(case, "pt9line75")["suggestion"]["cite"]["held"] is False
    # only a rule that holds the text, its source and the date it was read gives the citation, with them
    held = Rule("SIJ-BASIS", [], [], lambda g: {}, statute_cite="Statute 1(a)", statute_text="An invented statute's words.",
                statute_source="https://example.invalid/statute", statute_read="2026-10-04")
    monkeypatch.setattr(rules, "ALL_RULES", rules.ALL_RULES[:-1] + [held])
    e = entry(case, "pt9line75")
    assert e["suggestion"]["cite"] == {"held": True, "rule": "SIJ-BASIS", "cite": "Statute 1(a)", "text": "An invented statute's words.",
                                       "source": "https://example.invalid/statute", "read": "2026-10-04"}
    assert "I am applying to adjust status under Statute 1(a) based on my approved" in e["text"]


def test_both_voices_the_offices_once_set_and_approved(firm):
    case = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("pt9line75"), ORDERED)
    view = px.view(case)
    assert view["voice"]["voice"] == "client" and "not set" in view["voice"]["note"]
    settings.save("firm", {part14_voice.KEY: "office"}, "Sam Attorney")
    approval.approve(part14_voice.PRACTICE_ID, "Sam Attorney", "attorney")
    e = entry(case, "pt9line75")
    assert e["voice"] == "office" and e["text"] == (
        "The applicant entered the United States without being inspected and admitted or paroled, at or near HIDALGO, TX on or about 06/12/2019. "
        "The applicant is applying to adjust status based on an approved Special Immigrant Juvenile petition (Form I-360, receipt number "
        "IOE0912345678), approved on 05/01/2025.")
    assert "office's voice" in px.view(case)["voice"]["note"]


def test_a_model_is_never_called_to_write(firm, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a model was asked to write")

    monkeypatch.setattr(drafting, "local_model", refuse)
    monkeypatch.setattr(drafting, "smooth_with_model", refuse)
    case = make_case(firm, NAME | ARRIVED | APPROVED_I360 | JOBS | PERMIT | yes("in_removal_proceedings", "pt9line75", "worked_without_authorization"), ORDERED)
    view = px.view(case, "attorney")
    assert len(view["entries"]) == 3 and view["smoothing_on"] is False
    px.approve(case, K["pt9line75"], "Sam Attorney", "attorney")
    px.bundle_rows(case)
    with pytest.raises(ValueError, match="Grammar smoothing is off"):
        px.smooth(case, K["pt9line75"], "Paula Paralegal", "paralegal")


def test_the_grammar_helper_is_the_declarations(firm, monkeypatch):
    monkeypatch.setattr(drafting, "smoothing_on", lambda: True)
    case = make_case(firm, NAME | yes("committed_crime"))
    px.edit(case, K["committed_crime"], "Yes, I was cited for driving without license in Boston on 03/03/2024.", "Paula Paralegal", "paralegal")
    px.smooth(case, K["committed_crime"], "Paula Paralegal", "paralegal", model=lambda t: ("Yes, I was cited for reckless driving in Boston on 03/03/2024.", "test"))
    s = entry(case, "committed_crime")["smoothing"]
    assert s["accepted"] is False and "added words" in s["reason"]  # a changed fact is refused before anyone sees it
    with pytest.raises(ValueError, match="no grammar suggestion to take"):
        px.accept_smoothing(case, K["committed_crime"], "Paula Paralegal", "paralegal")
    px.smooth(case, K["committed_crime"], "Paula Paralegal", "paralegal", model=lambda t: ("Yes, I was cited for driving without a license in Boston on 03/03/2024.", "test"))
    assert entry(case, "committed_crime")["smoothing"]["accepted"] is True
    assert entry(case, "committed_crime")["text"] == "Yes, I was cited for driving without license in Boston on 03/03/2024."  # never used until accepted
    px.accept_smoothing(case, K["committed_crime"], "Paula Paralegal", "paralegal")
    e = entry(case, "committed_crime")
    assert e["text"] == "Yes, I was cited for driving without a license in Boston on 03/03/2024." and e["edit"]["suggestion"] is True


# -- the card: edit, approve, Undo, the approval taken back ----------------------------------------------------------------------------------


def test_the_attorney_approves_each_entry_and_a_change_takes_the_approval_back(firm):
    case = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("pt9line75"))
    key = K["pt9line75"]
    with pytest.raises(PermissionError, match="Only an attorney"):
        px.approve(case, key, "Paula Paralegal", "paralegal")
    with pytest.raises(ValueError, match="Enter your name"):
        px.approve(case, key, "", "attorney")
    view = px.approve(case, key, "Sam Attorney", "attorney")
    e = next(x for x in view["entries"] if x["key"] == key)
    assert e["state"] == "approved" and e["approval"]["who"] == "Sam Attorney" and e["approval"]["date"] == "10/05/2026" and e["approval"]["holds"]
    rec = px.read(case)["approvals"][key]
    assert rec["wording"] == "entered_without_inspection" and rec["voice"] == "client" and rec["facts"]["arrival_city"] == "HIDALGO"
    decision = load_decision_log(case)[px.approval_id(key)]
    assert decision["reviewer"] == "Sam Attorney" and decision["item"]["kind"] == "part14" and decision["values"]
    # the paralegal changes the text: the approval is taken back, kept on file with who and why
    px.edit(case, key, e["text"].replace("HIDALGO, TX", "HIDALGO, TEXAS"), "Paula Paralegal", "paralegal")
    e = entry(case, "pt9line75")
    assert e["approval"] is None and e["state"] == "draft" and e["edit"]["old"].endswith("05/01/2025.")
    assert load_decision_log(case)[px.approval_id(key)]["undone"]["by"] == "Paula Paralegal"
    hist = px.read(case)["history"][-1]
    assert hist["why"] == "the text changed after the approval" and hist["taken_back"]["who"] == "Paula Paralegal"
    assert px.problems(case) == ["Part 9, item 73 says Yes and has no explanation in Part 14."]
    # approved again, then Undo by hand (an attorney)
    px.approve(case, key, "Sam Attorney", "attorney")
    with pytest.raises(PermissionError):
        px.undo(case, key, "Paula Paralegal", "paralegal")
    px.undo(case, key, "Sam Attorney", "attorney")
    assert entry(case, "pt9line75")["approval"] is None
    # back to the suggestion
    px.revert(case, key, "Paula Paralegal", "paralegal")
    assert entry(case, "pt9line75")["how"] == "suggested"


def test_a_paralegal_may_undo_their_own_edit_from_the_decision_log_but_not_the_approval(firm):
    from review.auth import needs_attorney

    case = make_case(firm, NAME | yes("committed_crime"))
    px.edit(case, K["committed_crime"], "Yes, I was cited in Boston on 03/03/2024. The charge was dismissed.", "Paula Paralegal", "paralegal")
    px.approve(case, K["committed_crime"], "Sam Attorney", "attorney")
    log = load_decision_log(case)
    assert not needs_attorney(log[px.text_id(K["committed_crime"])]["item"], "set")
    assert needs_attorney(log[px.approval_id(K["committed_crime"])]["item"], "set")


def test_an_approval_that_no_longer_fits_the_text_or_the_facts_holds_the_packet(firm):
    case = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("pt9line75", "committed_crime"))
    px.edit(case, K["committed_crime"], "Yes, I was cited in Boston on 03/03/2024. The charge was dismissed.", "Paula Paralegal", "paralegal")
    px.approve(case, K["committed_crime"], "Sam Attorney", "attorney")
    px.approve(case, K["pt9line75"], "Sam Attorney", "attorney")
    # the Decision log's Undo of the edit (not through the card): the text is the suggestion again, which was never approved
    undo_decision(case, px.text_id(K["committed_crime"]), "Paula Paralegal", "paralegal")
    e = entry(case, "committed_crime")
    assert e["approval"] and not e["approval"]["holds"] and e["approval"]["why_not"] == "The explanation changed after the attorney approved it."
    # a fact the approved text was built on changes (the arrival date is corrected on a card)
    from review.state import record_decision

    record_decision(case, {"id": "fact:applicant.last_arrival_date", "kind": "fact", "level": "review", "title": "Date of last arrival", "group": "check",
                           "actions": ["set"], "facts": [{"key": "applicant.last_arrival_date", "input": {"type": "date"}}]},
                    {"action": "set", "values": {"applicant.last_arrival_date": "2019-06-14"}, "reviewer": "Paula Paralegal"})
    e73 = entry(case, "pt9line75")
    assert not e73["approval"]["holds"] and "the date of the last arrival" in e73["approval"]["why_not"]
    assert px.problems(case) == [
        "Part 9, item 23 says Yes and its explanation in Part 14 is no longer approved: the explanation changed after the attorney approved it. An "
        "attorney approves it again on the Explain the Yes answers card.",
        "Part 9, item 73 says Yes and its explanation in Part 14 is no longer approved: the facts it is built on changed after the approval: the date "
        "of the last arrival. An attorney approves it again on the Explain the Yes answers card."]


def test_the_decision_log_names_each_step_in_words_and_the_ledger_rows_are_words(firm):
    from review.state import Catalog, build_items

    case = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("pt9line75"))
    px.edit(case, K["pt9line75"], "Yes, I entered the United States without inspection.", "Paula Paralegal", "paralegal")
    px.approve(case, K["pt9line75"], "Sam Attorney", "attorney")
    px.undo(case, K["pt9line75"], "Sam Attorney", "attorney")
    titles = [d["title"] for d in build_items(case, FIELD_MAP, TEMPLATE, Catalog(FIELD_MAP, TEMPLATE))["done"]]
    assert "The Part 14 explanation for Part 9, item 73" in titles
    whats = [r["what"] for r in ledger(firm)]
    assert "Corrected: The Part 14 explanation for Part 9, item 73" in whats
    assert "Approved the Part 14 explanation for Part 9, item 73" in whats
    assert "Took back the approval of the Part 14 explanation for Part 9, item 73: taken back by hand" in whats
    for w in whats:
        assert "applicant." not in w and "part14" not in w and "HIDALGO" not in w  # never a key, never the text


# -- the form's own Part 14 page and the review bundle --------------------------------------------------------------------------------------


def test_the_approved_text_goes_on_the_forms_own_part14_page_with_page_part_item(firm):
    case = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("pt9line75", "in_removal_proceedings"), ORDERED)
    refill(case, FIELD_MAP, TEMPLATE)
    page = find_page(TEMPLATE)
    fields = PdfReader(str(case / "i485_filled.pdf")).get_fields()
    first = page.entries[0]
    assert not str(fields[first.text.name].get("/V") or "")  # nothing approved: nothing written
    px.approve(case, K["pt9line75"], "Sam Attorney", "attorney")
    px.approve(case, K["in_removal_proceedings"], "Sam Attorney", "attorney")
    refill(case, FIELD_MAP, TEMPLATE)
    fields = PdfReader(str(case / "i485_filled.pdf")).get_fields()
    got = [(str(fields[e.page.name].get("/V") or ""), str(fields[e.part.name].get("/V") or ""), str(fields[e.item.name].get("/V") or ""),
            " ".join(str(fields[e.text.name].get("/V") or "").split())) for e in page.entries[:2]]
    assert got[0][:3] == ("14", "9", "14") and got[0][3].startswith("Yes, I was placed in removal proceedings")  # the form's order
    assert got[1][:3] == ("20", "9", "73") and got[1][3].startswith("Yes, I entered the United States without being inspected")
    graph = reviewed_graph(case)
    assert graph.get("applicant.p14_block1_text").review.resolved_by == "Sam Attorney"  # the attorney's decision: no card asks about it again


def test_the_bundle_has_each_entry_with_its_slots_sources_and_the_approval(firm):
    from review.bundle import _Doc, _draw_explanations

    case = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("pt9line75"), ORDERED)
    px.approve(case, K["pt9line75"], "Sam Attorney", "attorney")
    rows = px.bundle_rows(case)
    assert rows["entries"][0]["approval"]["who"] == "Sam Attorney"
    doc = _Doc("EXAMPLE LAW LLP", "Review bundle", "test")
    _draw_explanations(doc, {"explanations": rows})
    from pypdf import PdfReader as R
    import io

    text = " ".join(" ".join((p.extract_text() or "") for p in R(io.BytesIO(doc.finish())).pages).split())
    assert "Part 14 explanations: where each came from" in text and "Part 9, item 73" in text
    assert "Approved by Sam Attorney on 10/05/2026" in text and "the city of the last arrival: HIDALGO" in text and "The I-360 approval notice" in text
    assert "the attorney adds the citation" in text
