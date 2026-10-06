"""Accuracy against hand-filled references (src/accuracy.py, tools/accuracy_report.py, docs/accuracy.md, docs/public/accuracy.md).

Every number is the count of a run these tests reproduce: the made-up references are built by src/accuracy_samples.py from the
pipeline's own demonstration client, compared box by box, and the committed pages must be what the tool writes today. Nothing
here is real: every client is invented."""

import json
import re
from datetime import date, datetime
from pathlib import Path

import pytest

import accuracy
import accuracy_samples
import clock
import compare
import settings
import version
from factgraph import FactGraph
import schema_path

REPO = Path(__file__).resolve().parent.parent
REPORT, PUBLIC = REPO / "docs" / "accuracy.md", REPO / "docs" / "public" / "accuracy.md"
REAL_VERSION = version.VERSION  # read before any test patches it: the pages must say the release they were written for


def _doc(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")  # a checkout on Windows may hold CRLF


def _stamp(text: str) -> tuple[date, str]:
    """The run date and software version a committed page says it was written on."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})\. Software version (\d+\.\d+\.\d+)\.", text)
    assert m, "the page names its date and version"
    return date(int(m[3]), int(m[1]), int(m[2])), m[4]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The made-up references, built once (a few seconds), on the day and version the committed report names."""
    day, ver = _stamp(_doc(REPORT))
    mp = pytest.MonkeyPatch()
    mp.setattr(clock, "_now_override", datetime(day.year, day.month, day.day, 12, 0))
    mp.setattr(version, "VERSION", ver)
    work = tmp_path_factory.mktemp("accuracy")
    refs = accuracy_samples.build(work / "built")
    report = accuracy.run(work / "no-clients" / "clients", work / "run", samples=True, firm=False)
    yield {"refs": refs, "report": report, "work": work / "built"}
    mp.undo()


# -- the report tool on the made-up references -------------------------------------------------------------------------

def test_the_made_up_references_cover_every_track_and_every_cause(built):
    fig = built["report"]["samples"]
    assert fig["references"] == 4 and fig["cases"] == 3
    assert accuracy.ref_words(4, 3) == "4 reference forms on 3 cases" and accuracy.ref_words(4, 4) == "4 references" and accuracy.ref_words(1, 1) == "1 reference"
    assert max(s["references"] for s in fig["sources"].values()) == 4  # the document-type table counts reference forms too, not cases
    for text in (accuracy.render_report(built["report"]), accuracy.render_public(built["report"])):
        assert "4 reference forms on 3 cases" in text
    assert {f["title"].split(",")[0] for f in fig["forms"].values()} == {"Form I-485", "Form I-130", "Form N-400"}
    assert fig["forms"]["i485"]["references"] == 2  # the special immigrant juvenile case and the family case
    t = fig["total"]
    assert t["boxes"] == t["identical"] + t["different"] + t["unfilled"] and t["different"] and t["unfilled"]
    for f in fig["forms"].values():
        assert f["boxes"] == f["identical"] + f["different"] + f["unfilled"]
        assert sum(f["causes"].values()) == f["different"] and sum(f["unfilled_causes"].values()) == f["unfilled"]
    causes = {k: sum(f["causes"][k] for f in fig["forms"].values()) for k in accuracy.DISAGREE}
    unfilled = {k: sum(f["unfilled_causes"][k] for f in fig["forms"].values()) for k in accuracy.UNFILLED}
    assert all(causes.values()) and all(unfilled.values()), (causes, unfilled)  # each cause is shown by at least one box
    assert fig["changes"] == sum(len(v) for v in accuracy_samples.EDITS.values())


def test_every_figure_is_a_count_of_boxes_with_what_it_is_a_count_of(built):
    t = built["report"]["samples"]["total"]
    assert accuracy.pct(t["identical"], t["boxes"]) == f"{int(100 * t['identical'] / t['boxes'] + 0.5)}% of {t['boxes']:,} boxes"
    assert accuracy.pct(1, 1) == "100% of 1 box" and accuracy.pct(0, 0) == "no boxes"
    # a share never rounds up to 100% unless every box is identical, nor down to 0% unless none is
    assert [accuracy.pct_value(*x) for x in ((1211, 1212), (999, 1000), (995, 1000), (1212, 1212), (3, 1000), (1, 1000), (0, 1000), (5, 8))] == [
        "99.9%", "99.9%", "99.9%", "100%", "under 1%", "under 1%", "0%", "63%"]
    assert accuracy.pct(1211, 1212) == "99.9% of 1,212 boxes" and accuracy.pct(3, 1000) == "under 1% of 1,000 boxes"
    one_off = {"case": "c", "form": "i485", "title": "Form I-485, X", "boxes_compared": 1212, "identical": 1211, "identical_by_source": {"A reader": 1211}, "marks": [],
               "boxes": [{"kind": "different", "field": "f", "label": "", "cause": "reader", "source": "A reader", "key": "", "reference": "a", "ours": "b"}]}
    page = "\n".join(accuracy._section("t", "n", accuracy.figures([one_off]), False))
    assert "99.9% of 1,212 boxes" in page and "100%" not in page.replace("99.9%", "")
    assert accuracy.history_line([_night("2026-10-03", 1211)])["line"].startswith("Identical boxes 99.9% of 1,212 on 10/03/2026")
    assert accuracy.drops([_night("2026-10-02", 1212, 1212, "2026.10.4"), _night("2026-10-03", 1180, 1212, "2026.10.5")]) == [
        "Identical boxes fell from 100% of 1,212 to 97% of 1,212 after the 2026.10.5 update: a person should look."]
    text = accuracy.render_report(built["report"])
    assert accuracy.public_problems(text, text) == []  # every percentage in the report has its denominator
    # per document type: the boxes the pipeline filled, identical plus different
    for label, s in built["report"]["samples"]["sources"].items():
        assert s["identical"] + s["different"] > 0 and s["references"] >= 1, label
    filled = sum(s["identical"] + s["different"] for s in built["report"]["samples"]["sources"].values())
    assert filled == t["identical"] + t["different"]


def test_the_reasons_come_from_the_case_not_from_a_list(built):
    by = {(r["case"], r["form"]): {b["field"]: b for b in r["boxes"]} for r in built["report"]["results"][accuracy.SAMPLES]}
    sij = by[("sample-sij", "i485")]
    assert sij["Pt9Line76_YesNo"]["cause"] == "rule" and sij["Pt9Line76_YesNo"]["kind"] == "different"  # Item 74: the firm's rule says Yes
    assert sij["Pt1Line7_CityTownOfBirth[0]"]["cause"] == "reader" and "Birth certificate" in sij["Pt1Line7_CityTownOfBirth[0]"]["source"]
    assert sij["Pt7Line3_HeightInches[0]"]["cause"] == "answer"  # the client's own portal answer
    assert sij["Pt1Line19_SSN[0]"]["cause"] == "held"  # the card and the portal disagree: the box stays empty for a person
    assert sij["Pt5Line8_DateofBirth[0]"]["cause"] == "not_asked" and sij["Pt1Line18_CurrentAptSteFlrNumber[0]"]["cause"] == "no_document"
    assert sij["Part11_NameofLanguage[0]"]["cause"] == "out_of_scope"
    assert by[("sample-family", "i130")]["Pt2Line16_NumberofMarriages[0]"]["cause"] == "answer"  # a person's entry on the case
    assert by[("sample-n400", "n400")]["P4_Line3_ZipCode1[0]"]["cause"] == "rule"
    # a date is compared as typed: 1/14/2020 is not 01/14/2020
    assert sij["Pt1Line12_Date[0]"]["reference"] == "1/14/2020" and sij["Pt1Line12_Date[0]"]["ours"] == "01/14/2020"


def test_a_marked_box_is_the_references_own_error_from_then_on(built):
    results = built["report"]["results"][accuracy.SAMPLES]
    marked = accuracy.figures(results)
    assert marked["marked"] == 2 and sum(f["causes"]["reference"] for f in marked["forms"].values()) == 2
    plain = accuracy.figures(results, lambda r: [])  # the same boxes with nobody having marked anything
    assert plain["marked"] == 0 and sum(f["causes"]["reader"] for f in plain["forms"].values()) == 2 + sum(f["causes"]["reader"] for f in marked["forms"].values())
    assert plain["total"] == marked["total"]  # a mark changes the cause, never the count of identical boxes
    sij_marks = next(r for r in results if r["case"] == "sample-sij")["marks"]
    assert [m["field"] for m in sij_marks] == ["Pt1Line10_PassportNum[0]"] and sij_marks[0]["by"] and sij_marks[0]["at"] and sij_marks[0]["reason"]


def test_provenance_names_the_part_that_wrote_a_box():
    g = FactGraph("x")
    g.add_source("a.passport", "passport.pdf", "passport", "X", "X", 0.9)
    g.add_source("a.scan", "questionnaire.pdf#p3", "intake_questionnaire", "X", "X", 0.9)
    g.add_source("a.portal", "portal questionnaire", "intake_questionnaire", "X", "X", 0.9)
    g.add_source("a.office", "office question", "office_question", "X", "X", 0.9)
    g.add_source("a.firm", "x", "firm_profile", "X", "X", 1.0)
    g.add_source("a.both", "portal questionnaire", "intake_questionnaire", "X", "X", 0.9)
    g.add_source("a.both", "i94.pdf", "i94", "X", "X", 0.9)
    g.add_derived("a.rule", "Yes", "OVERSTAY-01", ["a.passport"])
    g.add_source("a.typed", "x", "passport", "X", "X", 0.9)
    g.set_by_review("a.typed", "Y", "Paulo")
    got = {k: accuracy.provenance(g.get(k)) for k in ("a.passport", "a.scan", "a.portal", "a.office", "a.firm", "a.both", "a.rule", "a.typed")}
    assert got["a.passport"][0] == "reader" and got["a.scan"][0] == "reader"  # a paper questionnaire is read by a reader
    assert got["a.portal"] == ("answer", "Client portal answers") and got["a.office"][0] == "answer" and got["a.firm"][0] == "answer"
    assert got["a.both"][0] == "reader" and got["a.both"][1] == "Client portal answers + I-94 reader"  # counted once, under both names
    assert got["a.rule"] == ("rule", "Rules and firm policies") and got["a.typed"] == ("answer", "A person's entry in review")


def test_a_box_the_pipeline_left_blank_gets_one_reason():
    g = FactGraph("x")
    g.add_source("a.held", "a.pdf", "passport", "1", "1", 0.9)
    g.add_source("a.held", "b.pdf", "i94", "2", "2", 0.9)  # two documents disagree
    g.add_source("a.blank", "x", "passport", "X", "X", 0.9)
    g.blank_by_review("a.blank", "Paulo")
    ref = accuracy.Reference("c", "i485", Path("r.pdf"), Path("o.pdf"), g,
                             {"Held[0]": "a.held", "Blank[0]": "a.blank", "Asked[0]": "a.asked", "Doc[0]": "a.doc", "Grp": "a.held"})
    asked = {"a.asked"}
    got = {box: accuracy.unfilled_cause(ref, box, asked)[0] for box in ("Held[0]", "Blank[0]", "Asked[0]", "Doc[0]", "Nowhere[0]", "Grp[3]")}
    assert got == {"Held[0]": "held", "Blank[0]": "held", "Asked[0]": "not_asked", "Doc[0]": "no_document", "Nowhere[0]": "out_of_scope", "Grp[3]": "held"}


# -- the pages the tool writes ---------------------------------------------------------------------------------------------

def test_the_committed_report_and_public_page_are_what_the_tool_writes_today(built):
    assert _doc(REPORT) == accuracy.render_report(built["report"]), "run: python tools/accuracy_report.py"
    assert _doc(PUBLIC) == accuracy.render_public(built["report"]), "run: python tools/accuracy_report.py"


def test_a_percentage_on_the_public_page_is_in_the_report_with_its_denominator():
    public, report = _doc(PUBLIC), _doc(REPORT)
    assert accuracy.public_problems(public, report) == []
    assert re.search(r"\d+% of [\d,]+ boxes?", public)
    said = re.search(r"software version (\d+\.\d+\.\d+)\.", public)
    assert said and said[1] == _stamp(report)[1] == REAL_VERSION, "the pages were written for another release: run tools/accuracy_report.py"
    # the check fails on a figure the report does not hold, and on a figure with no denominator
    assert any("is not in the report" in p for p in accuracy.public_problems(public + "\nWe are 99% of 1,212 boxes accurate.\n", report))
    for shape in ("We are 97% accurate.", "A hit rate of 94% overall.", "94 percent of boxes", "94 % of the boxes", "about 94 per cent", "right 19 in 20 times",
                  "1 out of 4 boxes", "99% of 358 references", "94% of 694", "94% of 694 forms"):
        assert accuracy.public_problems(shape, report), shape  # a share in figures, spaced, in words, as "N in M", or of something that is not boxes
    first = re.search(r"\d+(?:\.\d+)?% of [\d,]+ box(?:es)?", report).group(0)
    assert accuracy.public_problems(f"Of the boxes, {first} were identical.", report) == []
    assert any("is not in the report" in p for p in accuracy.public_problems(first.replace("% of ", "% of 9"), report))


def test_the_pages_say_what_they_must_and_nothing_more():
    public, report = _doc(PUBLIC), _doc(REPORT)
    for text in (public, report):
        assert "made-up" in text and "April 1, 1997" in text and "Item 74" in text  # the Item 74 lesson, in the method
        assert "The firm's own figure, on its own hand-filled references, is the one that matters" in text
        assert "—" not in text and " -- " not in text
        assert re.search(r"software version 2026\.\d+\.\d+", text, re.I) and re.search(r"\d{2}/\d{2}/\d{4}", text)
        assert "say nothing about how accurate the pipeline is on a real case" in text  # the made-up references measure nothing
    assert "The firm's own figure on its own references is the one that matters" in public
    for word in ("guarantee", "best in", "industry-leading", "99.9"):
        assert word not in public.lower()
    # the method: what counts as identical, how a Yes/No box and a date are compared, and that references are the firm's own filings
    method = "\n".join(accuracy.METHOD)
    for phrase in ("Yes/No", "MM/DD/YYYY", "a form a person at the firm filled in by hand", "fewer than 10 references", "answer key"):
        assert phrase in method.replace("With fewer", "fewer"), phrase


# -- the tool ---------------------------------------------------------------------------------------------------------------

def test_the_tool_writes_both_pages_and_the_history(built, tmp_path, monkeypatch, capsys):
    import sys

    sys.path.insert(0, str(REPO / "tools"))
    import accuracy_report

    monkeypatch.setattr(accuracy_samples, "build", lambda workdir: built["refs"])
    monkeypatch.setenv("I485_ACCURACY_HISTORY", str(tmp_path / "history.jsonl"))
    out = ["--clients", str(tmp_path / "data" / "clients"), "--report", str(tmp_path / "accuracy.md"), "--public", str(tmp_path / "public" / "accuracy.md")]
    assert accuracy_report.main(out + ["--history"]) == 0
    printed = capsys.readouterr().out
    assert "made-up references that ship with the product: 4 reference forms on 3 cases" in printed and "Added to" in printed
    assert _doc(tmp_path / "accuracy.md") == accuracy.render_report(built["report"]) and (tmp_path / "public" / "accuracy.md").exists()
    rec = json.loads((tmp_path / "history.jsonl").read_text(encoding="utf-8"))
    t = built["report"]["samples"]["total"]
    assert rec["set"] == "samples" and rec["boxes"] == t["boxes"] and rec["identical"] == t["identical"] and rec["version"] == version.VERSION
    assert accuracy_report.main(out + ["--no-samples"]) == 0 and "firm's own" not in capsys.readouterr().out
    assert "is the one that matters" in (tmp_path / "accuracy.md").read_text(encoding="utf-8")


# -- the firm's own references: found, compared, marked ------------------------------------------------------------------

@pytest.fixture
def firm(built, tmp_path, monkeypatch):
    """The sample cases stand in for the firm's processed cases; their hand-filled forms are the firm's own references."""
    clients = built["work"] / "clients"
    monkeypatch.setenv("I485_ACCURACY_HISTORY", str(tmp_path / "accuracy_history.jsonl"))
    refs = tmp_path / "reference"
    refs.mkdir()
    for pdf in (built["work"] / "references").glob("*.pdf"):
        case, _, form = pdf.stem.partition(".")
        (refs / (f"{case}.pdf" if form == "i485" else f"{case}.{form}.pdf")).write_bytes(pdf.read_bytes())
    for marks in (built["work"] / "references").glob("*.marks.json"):  # the two marks the made-up reviewer made
        (refs / marks.name).write_bytes(marks.read_bytes())
    (refs / "g-1055.pdf").write_bytes(b"%PDF-1.4 an official form, not a reference")
    (refs / "no-such-case.pdf").write_bytes(b"x")
    monkeypatch.setenv("I485_REFERENCE", str(refs))
    from conftest import save_shipped_office_as_the_firms

    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")  # Implementation note.
    save_shipped_office_as_the_firms(tmp_path / "settings.json")
    return {"clients": clients, "refs": refs}


def test_the_firms_own_references_are_found_by_the_case_folders_name(firm):
    found, aside = accuracy.find_references(firm["clients"])
    assert sorted((c.name, form) for c, form, _ in found) == [("sample-family", "i130"), ("sample-family", "i485"), ("sample-n400", "n400"), ("sample-sij", "i485")]
    assert aside == []
    (firm["refs"] / "sample-sij.i999.pdf").write_bytes(b"x")
    asides = accuracy.find_references(firm["clients"])[1]
    assert asides == ["sample-sij: a reference file named for a form the product does not fill."]  # no file name, no code on the screen


def test_the_nightly_run_records_the_figures_and_the_screen_shows_them(firm, built, tmp_path, monkeypatch):
    monkeypatch.setattr(accuracy_samples, "build", lambda workdir: pytest.fail("the made-up references stand in only while the firm has none"))
    said = accuracy.nightly(firm["clients"])
    assert said.startswith("Accuracy: ") and "4 reference forms on 3 cases (the firm's own references)" in said
    t = built["report"]["samples"]["total"]  # the same forms, the same cases: the same boxes
    hist = accuracy.read_history(accuracy.history_path(firm["clients"]))
    assert len(hist) == 1 and hist[0]["set"] == "firm" and (hist[0]["boxes"], hist[0]["identical"]) == (t["boxes"], t["identical"])
    assert hist[0]["version"] == version.VERSION and set(hist[0]["forms"]) == {"i485", "i130", "n400"}
    screen = accuracy.screen(firm["clients"])
    assert screen["can_mark"] and screen["latest"]["set"] == "firm" and screen["latest"]["figures"]["marked"] == 2  # the two marks beside the references
    assert screen["history"]["line"].startswith("Identical boxes ") and "on 4 reference forms on 3 cases (the firm's own references)" in screen["history"]["line"]
    assert {d["case"] for d in screen["different"]} == {"sample-sij", "sample-family", "sample-n400"}
    # a case this person may not open is counted, not listed
    hidden = accuracy.screen(firm["clients"], lambda case: case != "sample-sij")
    assert hidden["hidden"] and all(d["case"] != "sample-sij" for d in hidden["different"])
    assert accuracy.latest(firm["clients"])["set"] == "firm"


def test_with_no_references_of_its_own_the_made_up_ones_stand_in(built, tmp_path, monkeypatch):
    monkeypatch.setenv("I485_ACCURACY_HISTORY", str(tmp_path / "accuracy_history.jsonl"))
    monkeypatch.setenv("I485_REFERENCE", str(tmp_path / "none"))
    monkeypatch.setattr(accuracy_samples, "build", lambda workdir: built["refs"])
    clients = tmp_path / "data" / "clients"
    clients.mkdir(parents=True)
    said = accuracy.nightly(clients)
    assert "(the made-up references that ship with the product)" in said
    accuracy.nightly(clients)  # a second night, the same day: the page counts the day once
    hist = accuracy.read_history(accuracy.history_path(clients))
    assert len(hist) == 2 and {h["set"] for h in hist} == {"samples"} and len(accuracy.nights(hist)) == 1
    screen = accuracy.screen(clients)
    assert not screen["can_mark"] and screen["latest"]["set_name"] == "the made-up references that ship with the product"
    nobody = accuracy.screen(clients, lambda case: False)  # the made-up references are nobody's case: none is hidden from anyone
    assert nobody["hidden"] == 0 and len(nobody["different"]) == 21 and sum(1 for d in nobody["different"] if d["mark"]) == 2
    assert {d["reference"] for d in nobody["different"] if d["field"] == "Pt9Line76_YesNo"} == {"No"} and "Left blank" in {d["reference"] for d in nobody["different"]}
    assert not any(d["reference"].startswith("/") or d["ours"].startswith("/") for d in nobody["different"])  # a ticked box in words, never a form code
    with pytest.raises(ValueError, match="no references of the firm's own"):
        accuracy.mark(clients, "sample-sij", "i485", "Pt1Line7_CityTownOfBirth[0]", "reason", "Ana")


def test_an_attorney_marks_a_reference_wrong_and_it_counts_from_then_on(firm):
    accuracy.nightly(firm["clients"])
    before = accuracy.screen(firm["clients"])["latest"]["figures"]
    case, form, box = "sample-family", "i130", "Pt4Line7_CityTownOfBirth[0]"
    row = lambda: next(d for d in accuracy.screen(firm["clients"])["different"] if (d["case"], d["form"], d["field"]) == (case, form, box))  # noqa: E731
    assert row()["cause"] == "reader" and row()["mark"] is None
    mark = accuracy.mark(firm["clients"], case, form, box, "  The birth certificate has no state after the city.  ", "Ana Attorney")
    assert mark["by"] == "Ana Attorney" and mark["reason"] == "The birth certificate has no state after the city." and clock.parse(mark["at"])
    kept = json.loads((firm["refs"] / f"{case}.marks.json").read_text(encoding="utf-8"))["marks"]  # kept beside the reference
    assert kept == [mark]
    assert row()["cause"] == "reference" and row()["mark"]["by"] == "Ana Attorney" and row()["cause_name"].startswith("The reference's own error")
    after = accuracy.screen(firm["clients"])["latest"]["figures"]
    assert after["marked"] == before["marked"] + 1 and after["total"] == before["total"]
    # the mark is on the box, not the case: the case's other forms and boxes are untouched, and the mark is kept for the next night
    assert accuracy.nightly(firm["clients"]).startswith("Accuracy: ") and row()["mark"]["reason"] == mark["reason"]
    other = "Pt2Line6_CityTownOfBirth[0]"  # a different box on the same form, not marked
    for args, said in [((case, form, box, "again", "Ana"), "already marked"), ((case, form, other, "", "Ana"), "Say why"),
                       ((case, form, other, "x", ""), "Enter your name"), ((case, form, "Pt4Line20_Yes", "x", "Ana"), "not one the last comparison"),
                       (("nobody", form, box, "x", "Ana"), "not one the last comparison"), ((case, form, other, "x" * 501, "Ana"), "under 500")]:
        with pytest.raises(ValueError, match=said):
            accuracy.mark(firm["clients"], *args)
    assert (case, form, other) not in {(d["case"], d["form"], d["field"]) for d in accuracy.screen(firm["clients"])["different"] if d["mark"]}


def test_an_unfilled_box_cannot_be_marked(firm):
    accuracy.nightly(firm["clients"])
    unfilled = next(b for r in accuracy.latest(firm["clients"])["results"] if r["case"] == "sample-sij" for b in r["boxes"] if b["kind"] == "unfilled")
    with pytest.raises(ValueError, match="not one the last comparison"):
        accuracy.mark(firm["clients"], "sample-sij", "i485", unfilled["field"], "x", "Ana")


def test_a_reference_that_cannot_be_compared_is_said_so(firm, built):
    d = firm["clients"] / "sample-unprocessed"
    d.mkdir()
    (firm["refs"] / "sample-unprocessed.pdf").write_bytes((firm["refs"] / "sample-sij.pdf").read_bytes())
    refs, aside = accuracy.firm_references(firm["clients"], built["work"].parent / "again")
    assert len(refs) == 4 and aside == ["sample-unprocessed: the case has not been processed"]


# -- the nights' history, in words ---------------------------------------------------------------------------------------

def _night(day, identical, boxes=1212, ver="2026.10.5", kind="samples", forms=None, refs=4):
    return {"at": f"{day}T03:00:00-04:00", "day": day, "version": ver, "set": kind, "references": refs, "boxes": boxes, "identical": identical,
            "different": 0, "unfilled": 0, "forms": forms or {}}


def test_the_history_reads_in_words_with_what_each_figure_is_a_share_of():
    hist = [_night("2026-09-26", 1127, 1198, "2026.10.4"), _night("2026-10-02", 1135), _night("2026-10-03", 1139)]
    said = accuracy.history_line(hist)
    assert said["line"] == "Identical boxes 94% of 1,212 on 10/03/2026, 94% of 1,198 a week ago, on 4 references (the made-up references)."
    assert said["nights"][-1] == {"day": "2026-10-03", "text": "10/03: 94% of 1,212"} and said["set"] == "samples"
    assert accuracy.history_line([_night("2026-10-03", 1139)])["line"] == "Identical boxes 94% of 1,212 on 10/03/2026, on 4 references (the made-up references)."
    assert accuracy.history_line([]) == {"line": "", "nights": [], "set": None}
    firm = accuracy.history_line([_night("2026-10-03", 5, 8, kind="firm", refs=1)])["line"]
    assert firm == "Identical boxes 63% of 8 on 10/03/2026, on 1 reference (the firm's own references)."  # 5 of 8, rounded: never without the 8


def test_the_history_keeps_the_last_30_nights_and_counts_a_day_once():
    start = date(2026, 8, 1)
    hist = [_night((start.fromordinal(start.toordinal() + n)).isoformat(), 1100 + n) for n in range(45)]
    hist.append(_night("2026-09-14", 1000))  # a second run the same day: the later one is the night's
    rows = accuracy.nights(sorted(hist, key=lambda r: r["at"]))
    assert len(rows) == 30 and rows[0]["day"] == "2026-08-16" and rows[-1]["day"] == "2026-09-14"
    only = [_night("2026-10-03", 1100), _night("2026-10-03", 1139)]
    assert accuracy.nights(only) == [only[1]]


def test_a_fall_of_more_than_two_points_after_an_update_says_so_in_plain_words():
    old = _night("2026-10-02", 1139, 1212, "2026.10.4")  # 94%
    fell = _night("2026-10-03", 1090, 1212, "2026.10.5")  # 90%
    assert accuracy.drops([old, fell]) == ["Identical boxes fell from 94% of 1,212 to 90% of 1,212 after the 2026.10.5 update: a person should look."]
    hundred = _night("2026-10-02", 94, 100, "2026.10.4")
    assert accuracy.drops([hundred, _night("2026-10-03", 92, 100, "2026.10.5")]) == []  # exactly 2 points is not more than 2
    assert len(accuracy.drops([hundred, _night("2026-10-03", 91, 100, "2026.10.5")])) == 1
    assert accuracy.drops([old, _night("2026-10-03", 1150, 1212, "2026.10.5")]) == []  # a rise
    assert accuracy.drops([_night("2026-10-02", 1139, 1212, "2026.10.5"), _night("2026-10-03", 1050, 1212, "2026.10.5")]) == []  # the same release
    assert accuracy.drops([old, _night("2026-10-03", 1090, 1212, "2026.10.5", kind="firm")]) == []  # not the same references
    assert accuracy.drops([]) == [] and accuracy.drops([old]) == []
    forms = {"i485": {"title": "Form I-485, Application", "boxes": 500, "identical": 480}, "n400": {"title": "Form N-400, Application", "boxes": 80, "identical": 76}}
    worse = {"i485": {"title": "Form I-485, Application", "boxes": 500, "identical": 470}, "n400": {"title": "Form N-400, Application", "boxes": 80, "identical": 70}}
    said = accuracy.drops([_night("2026-10-02", 1139, 1212, "2026.10.4", forms=forms), _night("2026-10-03", 1139, 1212, "2026.10.5", forms=worse)])
    assert said == ["Identical boxes on Form N-400 fell from 95% of 80 to 88% of 80 after the 2026.10.5 update: a person should look."]  # the I-485's fell 2 points
    for line in [*said, accuracy.history_line([old, fell])["line"]]:
        assert "—" not in line and " -- " not in line and ".json" not in line and ".py" not in line


def test_a_damaged_history_is_read_for_what_it_holds(tmp_path):
    p = tmp_path / "h.jsonl"
    p.write_text(json.dumps(_night("2026-10-03", 5, 8)) + "\nnot json\n[1]\n" + json.dumps({"day": "2026-10-04"}) + "\n", encoding="utf-8")
    assert [r["day"] for r in accuracy.read_history(p)] == ["2026-10-03"] and accuracy.read_history(tmp_path / "none.jsonl") == []


# -- the review app and the overnight run ---------------------------------------------------------------------------------

def test_the_screens_routes_are_the_attorneys_and_a_mark_needs_a_name(firm, built, monkeypatch):
    from review.server import ReviewApp

    app = ReviewApp(firm["clients"], schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None)
    attorney, paralegal = {"role": "attorney", "name": "Ana Attorney"}, {"role": "paralegal", "name": "Paulo"}
    accuracy.nightly(firm["clients"])
    with pytest.raises(PermissionError):
        app.accuracy_references(paralegal)
    with pytest.raises(PermissionError):
        app.accuracy_mark({"case": "sample-sij"}, paralegal)
    screen = app.accuracy_references(attorney)
    assert screen["different"] and all("key" not in d for d in screen["different"])  # no fact keys on the screen
    row = {"case": "sample-sij", "form": "i485", "field": "x", "label": "Part 1. Social Security Number", "key": "applicant.ssn", "reference": "123-45-6789",
           "ours": "123456789", "cause": "reader", "cause_name": "", "source": "", "mark": None}
    with monkeypatch.context() as m:
        m.setattr(accuracy, "screen", lambda clients, may_open=None: {"history": {}, "drops": [], "latest": None, "different": [dict(row)], "hidden": 0, "can_mark": True})
        masked = app.accuracy_references(attorney)["different"][0]
    assert (masked["reference"], masked["ours"]) == ("***-**-6789", "***-**-6789")  # a number the screen never prints in full
    done = app.accuracy_mark({"case": "sample-n400", "form": "n400", "field": "P2_Line11_CountryOfNationality[0]", "reason": "Brazil is spelled in English on the form.",
                              "reviewer": "Ana Attorney"}, attorney)
    assert done["ok"] and done["mark"]["by"] == "Ana Attorney"
    with pytest.raises(ValueError, match="Enter your name"):
        app.accuracy_mark({"case": "sample-n400", "form": "n400", "field": "P2_Line8_DateOfBirth[0]", "reason": "x"}, attorney)
    monkeypatch.setattr(app, "may_open", lambda user, case: case != "sample-n400")
    with pytest.raises(ValueError, match="not one the last comparison"):  # the same answer for a case the person may not open
        app.accuracy_mark({"case": "sample-n400", "form": "n400", "field": "P2_Line8_DateOfBirth[0]", "reason": "x", "reviewer": "Ana"}, attorney)
    assert all(d["case"] != "sample-n400" for d in app.accuracy_references(attorney)["different"])


def test_the_overnight_run_adds_tonights_figures_and_never_fails(monkeypatch, tmp_path):
    import overnight

    monkeypatch.setenv("I485_ACCURACY", "0")
    assert overnight.accuracy_night(tmp_path) == "Accuracy: switched off."
    monkeypatch.setenv("I485_ACCURACY", "1")
    monkeypatch.setattr(accuracy, "nightly", lambda clients: f"Accuracy: ran on {clients.name}.")
    assert overnight.accuracy_night(tmp_path / "clients") == "Accuracy: ran on clients."

    def boom(clients):
        raise RuntimeError("no")

    monkeypatch.setattr(accuracy, "nightly", boom)
    assert overnight.accuracy_night(tmp_path) == "Accuracy: couldn't run (RuntimeError); it runs again tomorrow night."


def test_the_run_is_in_the_overnight_runs_steps():
    source = (REPO / "src" / "overnight.py").read_text(encoding="utf-8")
    assert "log(accuracy_night(out_root))" in source


def test_the_register_holds_the_nightly_run_and_the_public_pages_figures():
    items = {i["id"]: i for i in json.loads((schema_path.path("register", "maintenance")).read_text(encoding="utf-8"))["items"]}
    for key in ("accuracy_nightly", "accuracy_public_page"):
        item = items[key]
        assert item["party"] == "provider" and item["cadence"] == "monthly" and item["steps"] and item["firm_steps"] and item["last_checked"]
        assert not any(w in " ".join(item["firm_steps"]) for w in ("schemas/", "src/", ".json", ".py", "data/"))
    raw = (schema_path.path("register", "maintenance")).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert json.dumps(json.loads(raw), indent=2, ensure_ascii=False) + "\n" == raw  # re-dumps byte for byte


def test_the_made_up_references_name_real_boxes_and_the_edits_are_all_applied():
    assert sum(len(v) for v in accuracy_samples.EDITS.values()) == 42
    assert {k for k in accuracy_samples.MARKS} <= set(accuracy_samples.EDITS)
    for (case, form), marks in accuracy_samples.MARKS.items():
        assert {m[0] for m in marks} <= {e[0] for e in accuracy_samples.EDITS[(case, form)]}
    assert compare.short_name("form1[0].#subform[3].Pt1Line1_FamilyName[0]") == "Pt1Line1_FamilyName[0]"


# -- a reference that cannot be read, or has nothing to read, set aside in plain words ------------------------------------------

def _plain(lines):
    for line in lines:
        assert ".pdf" not in line and "Error" not in line and "Exception" not in line and "—" not in line and " -- " not in line, line


def _replace(firm, name, data: bytes):
    (firm["refs"] / name).write_bytes(data)


def test_one_bad_reference_file_does_not_cost_the_night_its_figure(firm, built):
    good = (firm["refs"] / "sample-sij.pdf").read_bytes()
    _replace(firm, "sample-sij.pdf", b"")  # zero bytes
    _replace(firm, "sample-n400.n400.pdf", b"%")  # one byte
    _replace(firm, "sample-family.pdf", good[:2000])  # cut off
    said = accuracy.nightly(firm["clients"])
    assert said.startswith("Accuracy: ") and "1 reference (the firm's own references)" in said  # the I-130 is still compared
    hist = accuracy.read_history(accuracy.history_path(firm["clients"]))
    assert len(hist) == 1 and hist[0]["set"] == "firm" and hist[0]["references"] == 1
    skipped = accuracy.latest(firm["clients"])["skipped"]
    assert sorted(skipped) == ["sample-family (Form I-485): the reference file could not be opened as a form.",
                               "sample-n400 (Form N-400): the reference file could not be opened as a form.",
                               "sample-sij (Form I-485): the reference file could not be opened as a form."]
    _plain(skipped)
    screen = accuracy.screen(firm["clients"])
    assert screen["latest"]["skipped"] == skipped and screen["latest"]["figures"]["references"] == 1
    _plain(screen["latest"]["skipped"])


def test_when_every_reference_is_bad_the_made_up_ones_stand_in(firm, built, monkeypatch):
    for pdf in firm["refs"].glob("*.pdf"):
        pdf.write_bytes(b"")
    monkeypatch.setattr(accuracy_samples, "build", lambda workdir: built["refs"])
    said = accuracy.nightly(firm["clients"])
    assert "(the made-up references that ship with the product)" in said
    assert accuracy.read_history(accuracy.history_path(firm["clients"]))[-1]["set"] == "samples"
    assert len(accuracy.latest(firm["clients"])["skipped"]) == 4


def test_a_printed_and_scanned_reference_has_no_boxes_and_is_set_aside_not_scored(firm, built):
    from pypdf import PdfWriter

    flat = PdfWriter()
    flat.add_blank_page(width=612, height=792)
    with open(firm["refs"] / "sample-sij.pdf", "wb") as fh:
        flat.write(fh)
    refs, aside = accuracy.firm_references(firm["clients"], built["work"].parent / "flat")
    results, more = accuracy.compare_all(refs)
    assert len(results) == 3 and not any(r["case"] == "sample-sij" for r in results)  # not scored as 268 boxes that all differ
    assert more == ["sample-sij (Form I-485): the reference has no filled-in boxes (a printed and scanned form has none); keep the PDF as the fillable form it was filled in."]
    _plain(more)
    assert "fillable" in (REPO / "docs" / "deployment.md").read_text(encoding="utf-8")


def test_a_history_cut_short_does_not_swallow_the_next_night(tmp_path):
    path = tmp_path / "h.jsonl"
    accuracy.append_history(path, _night("2026-10-02", 5, 8))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"day": "2026-10-03", "boxes": 8, "identi')  # the run died mid-write
    accuracy.append_history(path, _night("2026-10-04", 6, 8))
    assert [r["day"] for r in accuracy.read_history(path)] == ["2026-10-02", "2026-10-04"]
    accuracy.append_history(path, _night("2026-10-05", 7, 8))
    assert len(path.read_text(encoding="utf-8").splitlines()) == 4


def test_the_pages_do_not_claim_what_nothing_does():
    public, report = _doc(PUBLIC), _doc(REPORT)
    assert "Most vendors" not in public and "every release" not in public and "regenerated by hand when a release changes" in public
    for text in (public, report):
        assert "A fix made without the real form changed that answer" in text and "That was a wrong fix, not a comparison with a hand-filled form" in text
        assert "would have been a disagreement where the pipeline was right" not in text  # a hypothetical stated as if it happened
    assert accuracy.BY_TYPE in public and accuracy.BY_TYPE in report  # the share in that table is of the boxes the pipeline filled
    assert "Read from a document" in report and "From a rule" in report  # the report's columns name the source, they do not blame it
    for text in (public, report):
        assert "Not asked or not answered" in text
        assert "| The reader |" not in text and "| The rule |" not in text and "| Not asked |" not in text
