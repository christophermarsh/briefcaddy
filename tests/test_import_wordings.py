"""Reading the Part 14 explanations out of the firm's past filed I-485s into the wording library (tools/import_wordings.py, brief L3): made-up filled forms the
test builds (nothing real is read), across two editions of the form; the item numbers put on the current edition by the table, an item with no pair kept under
"to place"; every date, place, name, number and receipt a blank; a candidate that is offered on no case until an attorney approves it in bulk. Everyone here
is made up (the Exemplo family)."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

import clock
import events
import part14_explain as px
import settings
import wordings
from fill import continuation as cont
from fill import where
from test_part14_explanations import APPROVED_I360, ARRIVED, K, NAME, entry, make_case, yes
import schema_path


@pytest.fixture
def firm(tmp_path, monkeypatch):
    """The firm's own files in a scratch folder, and the clock at 10/05/2026 (the same set-up as tests/test_part14_explanations.py)."""
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("PORTAL_DATA", str(tmp_path / "portal"))
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))
    return tmp_path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import import_wordings as iw  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = schema_path.path("template", "i485")
CURRENT = where.edition_of()
OLD = "01/20/25"
# the first client's own boxes on the made-up forms: invented
WHO = {"applicant.family_name": "EXEMPLO SOUZA", "applicant.given_name": "ANA CLARA", "applicant.a_number": "A099000111", "applicant.dob": "04/02/2007",
       "applicant.physical_street": "RUA DAS FLORES 100", "applicant.physical_city": "SPRINGFIELD", "applicant.physical_zip": "01103",
       "applicant.i360_receipt_number": "IOE0912345678", "applicant.last_arrival_city": "HIDALGO", "applicant.last_arrival_state": "TX",
       "applicant.last_arrival_date": "06/12/2019"}
FMAP = json.loads((schema_path.path("field_map", "i485")).read_text(encoding="utf-8"))["fact_to_acroform"]
ITEM73 = ("Yes, I entered the United States without inspection at or near HIDALGO, TX on or about 06/12/2019. I am applying to adjust status based on my approved "
          "Special Immigrant Juvenile petition (Form I-360, receipt number IOE0912345678), approved on 05/01/2025.")
ITEM12 = "Yes, I worked in the United States without employment authorization from 03/01/2021 to 08/31/2023 at Cafe Bonito in Worcester, Massachusetts."
ITEM14 = ("The applicant was placed in removal proceedings before the Boston Immigration Court. On 02/10/2025, Judge Maria Prado ordered the applicant removed in absentia. "
          "Ana Clara Exemplo Souza, born April 2, 2007, lives at Rua das Flores 100, Springfield 01103.")


def filled_form(path: Path, entries: list[tuple[str, str, str, str]], edition: str = CURRENT, who: dict[str, str] | None = None) -> Path:
    """A made-up filled I-485: the template with the client's boxes filled (`who`) and each (page, part, item, text) on the Additional Information page. An older edition
    is the same form with that edition printed on its pages."""
    writer = PdfWriter(clone_from=str(TEMPLATE))
    index = where._index(str(TEMPLATE))
    by_page: dict[int, dict[str, str]] = {}
    for key, value in (who if who is not None else WHO).items():
        name = where._first_field(FMAP[key])
        page = where._lookup(index, name)[0]
        by_page.setdefault(page - 1, {})[name] = value
    page14 = cont.find_page(TEMPLATE)
    for box, (page, part, item, text) in zip(page14.entries, entries):
        by_page.setdefault(page14.index, {}).update({box.page.name: page, box.part.name: part, box.item.name: item, box.text.name: text})
    for number, values in by_page.items():
        writer.update_page_form_field_values(writer.pages[number], values, auto_regenerate=False)
    if edition != CURRENT:
        for page in writer.pages:
            content = page.get_contents()
            if content is not None and CURRENT.encode() in content.get_data():
                stream = DecodedStreamObject()
                stream.set_data(content.get_data().replace(CURRENT.encode(), edition.encode()))
                page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(str(path))
    return path


@pytest.fixture
def refs(firm):
    folder = firm / "reference"
    folder.mkdir()
    return folder


def run(firm, refs, **kw) -> dict:
    return iw.run(refs, firm / "clients", "Ivan IT", log=lambda *_: None, **kw)


def library(firm) -> list[dict]:
    return wordings.every(wordings.root(firm / "clients"))


# -- reading a past filing ------------------------------------------------------------------------------------------------------------------


def test_the_explanations_of_a_current_edition_form_are_read_with_the_forms_own_numbers(firm, refs):
    filled_form(refs / "case-one.pdf", [("20", "9", "73", ITEM73), ("13", "9", "12", ITEM12), ("14", "9", "14", ITEM14)])
    form = iw.read_form(refs / "case-one.pdf")
    assert form["edition"] == CURRENT and form["reason"] == ""
    assert [(e["page"], e["part"], e["item"]) for e in form["entries"]] == [("20", "9", "73"), ("13", "9", "12"), ("14", "9", "14")]
    assert form["entries"][0]["text"] == ITEM73 and form["identity"]["applicant.family_name"] == "EXEMPLO SOUZA"
    assert form["slots"] == {"i360_receipt": "IOE0912345678", "arrival_city": "HIDALGO", "arrival_state": "TX", "arrival_date": "06/12/2019"}
    r = run(firm, refs)
    assert (r["files"], r["entries"], r["written"], r["to_place"], r["duplicates"], r["set_aside"]) == (1, 3, 3, 0, 0, [])
    by_key = {w["key"].rsplit(".", 1)[-1]: w for w in library(firm)}
    assert set(by_key) == {"pt9line75", "worked_without_authorization", "in_removal_proceedings"}
    w73 = by_key["pt9line75"]
    assert w73["text"] == ("Yes, I entered the United States without inspection at or near {arrival_city}, {arrival_state} on or about {arrival_date}. I am applying to adjust "
                           "status based on my approved Special Immigrant Juvenile petition (Form I-360, receipt number {i360_receipt}), approved on {date_1}.")
    assert (w73["status"], w73["origin"], w73["voice"], w73["from_file"], w73["edition"], w73["part"], w73["item"], w73["page"]) == (
        "candidate", "past_filing", "client", "case-one.pdf", CURRENT, "9", "73", "20")
    assert w73["approved"] is None and w73["uses"] == [] and w73["printed_as"] == {"edition": CURRENT, "part": "9", "item": "73", "page": "20"}
    assert by_key["worked_without_authorization"]["text"] == ("Yes, I worked in the United States without employment authorization from {date_1} to {date_2} at {place_1} in {place_2}.")
    # the office's voice is told, and a name, a date, a place and a client's own identifiers are all blanks
    w14 = by_key["in_removal_proceedings"]
    assert w14["voice"] == "office"
    assert w14["text"] == ("The applicant was placed in removal proceedings before the {place_1}. On {date_1}, {name_1} ordered the applicant removed in absentia. "
                           "{name_2} {name_3}, born {person_1}, lives at {person_2}, {name_4} {person_3}.")  # Implementation note.


def test_no_client_name_number_date_of_birth_or_address_of_a_past_filing_is_kept(firm, refs):
    filled_form(refs / "case-one.pdf", [("14", "9", "14", ITEM14), ("20", "9", "73", ITEM73), ("13", "9", "12", ITEM12)])
    run(firm, refs)
    alive = ("ana", "clara", "exemplo", "souza", "a099000111", "ioe0912345678", "april 2", "2007", "04/02", "rua das flores", "flores", "springfield", "01103", "hidalgo", "06/12/2019",
             "02/10/2025", "05/01/2025", "maria prado", "prado", "boston", "bonito", "worcester", "03/01/2021", "08/31/2023", "massachusetts")
    for path in wordings.root(firm / "clients").rglob("*.json"):
        body = path.read_text(encoding="utf-8").lower()
        assert [w for w in alive if re.search(rf"\b{re.escape(w)}\b", body)] == [], path
    assert [w for w in alive if re.search(rf"\b{re.escape(w)}\b", json.dumps(list(events.rows(events.base_path()))).lower())] == []
    for rec in library(firm):
        assert wordings.leaks(rec["text"], None, restricted=True, extra_identity=iw.read_form(refs / "case-one.pdf")["identity"]) == []


def test_an_older_editions_item_numbers_are_put_on_the_current_edition_by_the_table_and_the_rest_is_kept_to_place(firm, refs):
    old = [("20", "9", "75", ITEM73.replace("Yes, I entered", "Yes, I entered")), ("21", "9", "76", "Yes, I have been in the United States without being admitted since I entered on or about 06/12/2019."),
           ("5", "3", "7", "The applicant lived at Rua das Flores 100, Springfield, from 01/2020 to 06/2023 and at 12 Rua Nova, Boston, from 07/2023.")]
    filled_form(refs / "lima-old.pdf", old, edition=OLD)
    form = iw.read_form(refs / "lima-old.pdf")
    assert form["edition"] == OLD and [(e["part"], e["item"]) for e in form["entries"]] == [("9", "75"), ("9", "76"), ("3", "7")]
    r = run(firm, refs)
    assert (r["files"], r["entries"], r["written"], r["to_place"]) == (1, 3, 2, 1)
    by = {(w["part"], w["item"]): w for w in library(firm)}
    placed = by[("9", "73")]  # the old item 75 is this edition's item 73, on page 20
    assert placed["key"] == K["pt9line75"] and placed["edition"] == CURRENT and placed["page"] == "20" and placed["printed_as"]["edition"] == OLD and placed["printed_as"]["item"] == "75"
    assert placed["placed_by"] == f"the table for the {OLD} edition"
    assert by[("9", "74")]["key"] == K["unlawfully_present_since_1997"] or by[("9", "74")]["key"].endswith("unlawfully_present_since_1997")
    stray = by[("3", "7")]  # no pair for it: kept as the old form printed it, under to place, with no answer
    assert stray["key"] is None and stray["edition"] == OLD and stray["status"] == "candidate" and stray["voice"] == "office"
    assert (wordings.root(firm / "clients") / "i485" / "to_place" / f"{stray['id']}.json").is_file()
    assert "Rua" not in stray["text"] and "Boston" not in stray["text"] and "01/2020" not in stray["text"]
    # an older edition with no table at all keeps everything to place
    filled_form(refs / "prado-older.pdf", [("20", "9", "75", ITEM12)], edition="03/04/20")
    r2 = run(firm, refs)
    assert r2["to_place"] == 1 and r2["written"] == 0 and r2["duplicates"] == 3


def test_a_form_that_cannot_be_read_is_set_aside_in_words_and_another_form_is_left_alone(firm, refs):
    from pypdf import PdfWriter as W

    plain = W()
    plain.add_blank_page(width=612, height=792)
    plain.write(str(refs / "scanned.pdf"))
    (refs / "broken.pdf").write_bytes(b"not a pdf at all")
    filled_form(refs / "ok.pdf", [("14", "9", "14", ITEM14)], who={})
    filled_form(refs / "ok.n400.pdf", [("14", "9", "14", ITEM14)])
    filled_form(refs / "empty.pdf", [])
    (refs / "ok.marks.json").write_text("{}", encoding="utf-8")
    r = run(firm, refs)
    assert r["files"] == 1 and r["written"] == 1  # only ok.pdf: the N-400's is another form's
    why = dict(r["set_aside"])
    assert "no boxes to read" in why["scanned.pdf"] and "could not be opened as a form" in why["broken.pdf"] and "no explanation is written" in why["empty.pdf"]
    assert set(why) == {"scanned.pdf", "broken.pdf", "empty.pdf"}


def test_a_second_run_writes_nothing_new_and_a_dry_run_writes_nothing(firm, refs):
    filled_form(refs / "case-one.pdf", [("20", "9", "73", ITEM73)])
    dry = run(firm, refs, dry_run=True)
    assert dry["written"] == 1 and library(firm) == [] and not wordings.root(firm / "clients").exists()
    assert run(firm, refs)["written"] == 1
    again = run(firm, refs)
    assert (again["written"], again["duplicates"]) == (0, 1) and len(library(firm)) == 1
    rows = [r for r in events.rows(events.base_path()) if r["kind"] == "wordings"]
    assert [(r["action"], r["who"], r["via"]) for r in rows] == [("imported", "Ivan IT", "tool")]
    assert rows[0]["what"] == "Read 1 wording from 1 past filed form (not approved yet)"


# -- nothing is offered until an attorney approves it, in bulk -------------------------------------------------------------------------------


def test_a_candidate_is_offered_on_no_case_until_an_attorney_approves_it(firm, refs):
    filled_form(refs / "case-one.pdf", [("20", "9", "73", ITEM73), ("14", "9", "14", ITEM14), ("13", "9", "12", ITEM12)])
    run(firm, refs)
    base = wordings.root(firm / "clients")
    case = make_case(firm, NAME | ARRIVED | APPROVED_I360 | yes("pt9line75"), name="case-new")
    assert entry(case, "pt9line75")["firm"]["offers"] == []  # candidates are not offered
    mine = [w["id"] for w in library(firm) if w["key"] == K["pt9line75"]]
    with pytest.raises(PermissionError):
        wordings.approve_candidates(base, mine, "Paula Paralegal", "paralegal")
    with pytest.raises(ValueError, match="Enter your name"):
        wordings.approve_candidates(base, mine, "", "attorney")
    assert wordings.approve_candidates(base, mine, "Sam Attorney", "attorney") == {"changed": 1}
    (offer,) = entry(case, "pt9line75")["firm"]["offers"]
    assert offer["text"] == ("Yes, I entered the United States without inspection at or near HIDALGO, TX on or about 06/12/2019. I am applying to adjust status based on my "
                             "approved Special Immigrant Juvenile petition (Form I-360, receipt number IOE0912345678), approved on [a date].")
    assert offer["wrote"] == "From the office's past filings, approved by Sam Attorney on 10/05/2026. Not used on a case here yet." and offer["facts"] == []
    # approved in the library, retired never deleted
    (kept,) = [w for w in library(firm) if w["id"] == mine[0]]
    assert kept["status"] == "approved" and kept["approved"]["who"] == "Sam Attorney" and kept["origin"] == "past_filing" and kept["history"][-1]["what"] == "approved in bulk from a past filing"


def test_bulk_approval_edit_discard_and_place(firm, refs):
    filled_form(refs / "a.pdf", [("20", "9", "73", ITEM73), ("5", "3", "7", "The applicant lived at Rua Nova 12, Boston, from 07/2023 to the present day.")], who={})
    (refs / "b.pdf").write_bytes((refs / "a.pdf").read_bytes())
    filled_form(refs / "c.pdf", [("13", "9", "12", "Worked without a permit for some months in the winter, paid in cash by a neighbor each week.")], who={})
    r = run(firm, refs)
    assert r["written"] == 2 and r["to_place"] == 1 and r["duplicates"] == 2  # b.pdf is a.pdf again: the same words are one wording
    base = wordings.root(firm / "clients")
    by = {w["key"] or "to_place": w for w in library(firm)}
    voiceless = by[K["worked_without_authorization"]]
    assert voiceless["voice"] == "" and wordings.row(voiceless)["needs_voice"] is True  # no "Yes, I" and no "The applicant": the voice cannot be told
    with pytest.raises(ValueError, match="Say which voice"):
        wordings.approve_candidates(base, [voiceless["id"]], "Sam Attorney", "attorney")
    wordings.approve_candidates(base, [voiceless["id"]], "Sam Attorney", "attorney", voice="client")
    approved = next(w for w in library(firm) if w["key"] == K["worked_without_authorization"])
    assert approved["status"] == "approved" and approved["voice"] == "client" and approved["id"] != voiceless["id"]  # the voice is part of what a wording is: a new id
    assert not (base / "i485" / "worked_without_authorization" / f"{voiceless['id']}.json").exists() and len(library(firm)) == 3
    # one with no answer cannot be approved; placing it puts it on an answer of the current edition, to be approved
    stray = by["to_place"]
    with pytest.raises(ValueError, match="no item cannot be approved"):
        wordings.approve_candidates(base, [stray["id"]], "Sam Attorney", "attorney")
    with pytest.raises(ValueError, match="not one the form says to explain"):
        wordings.place(base, stray["id"], "applicant.part9.not_an_answer", "Sam Attorney", "attorney")
    placed = wordings.place(base, stray["id"], K["committed_crime"], "Sam Attorney", "attorney")["id"]
    now = next(w for w in library(firm) if w["id"] == placed)
    assert now["key"] == K["committed_crime"] and now["edition"] == CURRENT and now["status"] == "candidate" and (now["part"], now["item"]) == ("9", "23")
    # an edit before approval makes the typed words blanks again; set aside is never offered and the file stays
    edited = wordings.edit_candidate(base, placed, "The applicant was cited in Springfield on 03/03/2024.", "Sam Attorney", "attorney")["id"]
    assert next(w for w in library(firm) if w["id"] == edited)["text"] == "The applicant was cited in {place_1} on {date_1}."
    wordings.discard(base, [edited], "Sam Attorney", "attorney")
    assert next(w for w in library(firm) if w["id"] == edited)["status"] == "discarded"
    with pytest.raises(ValueError, match="Only a wording that is not approved"):
        wordings.edit_candidate(base, approved["id"], "x", "Sam Attorney", "attorney")


def test_an_approved_wording_of_an_older_edition_is_carried_to_the_current_one_as_a_candidate(firm):
    base = wordings.root(firm / "clients")
    old = {"id": "w-oldedition", "form": "i485", "edition": OLD, "key": None, "part": "9", "item": "75", "page": "20", "voice": "client", "office": "ma", "office_name": "x",
           "text": "Yes, I entered without inspection at {place_1}.", "slots": [], "pattern": {"present": [], "absent": []}, "status": "approved", "origin": "approval",
           "created": "2026-10-01T10:00:00-04:00", "approved": {"who": "Sam Attorney", "role": "attorney", "at": "2026-10-01T10:00:00-04:00"},
           "uses": [{"case": "case-a", "at": "2026-10-01T10:00:00-04:00", "by": "Sam Attorney", "role": "attorney", "via": "learned"}], "edits": [], "history": []}
    wordings._write(base, old)
    shown = wordings.library(base)
    assert [w["id"] for w in shown["to_place"]] == ["w-oldedition"]
    child = wordings.place(base, "w-oldedition", K["pt9line75"], "Sam Attorney", "attorney")["id"]
    kept = {w["id"]: w for w in wordings.every(base)}
    assert kept["w-oldedition"]["status"] == "approved" and wordings.count(kept["w-oldedition"]) == 1  # stays for the cases it was used on
    assert kept[child]["status"] == "candidate" and kept[child]["parent"] == "w-oldedition" and kept[child]["edition"] == CURRENT and kept[child]["uses"] == []


# -- the tool never reads a case and the table is the form's own -----------------------------------------------------------------------------


_WATCH = {"on": False, "opened": []}


def _audit(event, args):
    """Every open() the process makes, by any route (builtins.open, io.open, Path.read_text, pypdf's reads): the interpreter says so."""
    if _WATCH["on"] and event == "open" and args and isinstance(args[0], (str, bytes, Path)):
        _WATCH["opened"].append(str(args[0]))


sys.addaudithook(_audit)  # a hook cannot be taken away: it does nothing unless a test switches it on


def test_the_import_reads_only_the_references_folder_never_a_case(firm, refs):
    clients = firm / "clients"
    decoy = clients / "case-decoy"
    decoy.mkdir(parents=True)
    (decoy / "fact_graph.json").write_text("{}", encoding="utf-8")
    filled_form(refs / "case-one.pdf", [("20", "9", "73", ITEM73), ("14", "9", "14", ITEM14)])
    filled_form(refs / "case-two.pdf", [("13", "9", "12", ITEM12)], edition=OLD)
    _WATCH["opened"].clear()
    _WATCH["on"] = True
    try:
        r = run(firm, refs)
    finally:
        _WATCH["on"] = False
    assert r["files"] == 2 and r["written"] + r["to_place"] >= 3
    seen = _WATCH["opened"]
    assert any("case-one.pdf" in p for p in seen) and any("wordings" in p for p in seen)  # the hook sees the references and the library
    assert not [p for p in seen if "case-decoy" in p or str(clients) in p]  # and never anything under the case folders
    src = (REPO / "tools" / "import_wordings.py").read_text(encoding="utf-8")
    assert "for_case" not in src and "data/clients" in src.split('"""')[1] and "iterdir" not in src


def test_the_item_map_redumps_byte_for_byte_and_every_pair_is_an_answer_of_the_current_edition():
    raw = (schema_path.path("firm", "part14_item_map")).read_text(encoding="utf-8")
    assert json.dumps(json.loads(raw), indent=2, ensure_ascii=False) + "\n" == raw
    pairs = iw.item_map()["forms"]["i485"]
    for edition, table in pairs.items():
        assert table["to"] == CURRENT and table["_source"]
        for part, items in table["items"].items():
            for old, new in items.items():
                assert any(x["part"] == part and x["item"] == new for x in px.listed(CURRENT)), (edition, part, old, new)
    # the two the firm's filed examples show: the old 75 and 76 are the current 73 and 74
    spot = {x["item"]: x["key"] for x in px.listed(CURRENT) if x["part"] == "9"}
    assert spot["73"] == K["pt9line75"] and spot["74"].endswith("unlawfully_present_since_1997")


def test_the_command_says_what_it_did_in_words(firm, refs, capsys):
    filled_form(refs / "case-one.pdf", [("20", "9", "73", ITEM73)])
    assert iw.main(["--clients", str(firm / "clients"), "--references", str(refs), "--by", "Ivan IT", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Would write 1 wording for an attorney to approve and 0 to place, from 1 form (1 entry; 0 already in the library)." in out and "none is offered on a case until then" in out
    assert iw.main(["--clients", str(firm / "clients"), "--references", str(firm / "nowhere"), "--by", "Ivan IT"]) == 1
    assert "No folder of past filings" in capsys.readouterr().err
    assert iw.main(["--clients", str(firm / "clients"), "--references", str(refs), "--office", "zz"]) == 1
    assert "No office 'zz'" in capsys.readouterr().err
