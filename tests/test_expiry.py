"""The expiry radar (src/expiry.py, src/review/expiring.py): one deadline for each expiring document a case still
needs, read from the case's document record, with the rule it comes from and the filing it opens.

Every document, name and date here is made up. TODAY is a fixed day so the windows are exact.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

import expiry
import journey
from factgraph import FactGraph
from review import expiring as firm_list
from review.overview import PASSED_DAYS, deadlines as firm_deadlines, history_once_missed
import schema_path

TODAY = date(2026, 10, 2)


def iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def record(doc_id: str, doc_type: str, person: str = "applicant", expires: str | None = None, issued: str | None = None, added: str = "2026-09-01T10:00:00+00:00",
           confidential: str | None = None) -> dict:
    return {"id": doc_id, "files": [f"{doc_id}.pdf"], "doc_ids": [f"{doc_id}.pdf"], "pages": [1], "type": doc_type, "confidence": 0.9, "person": person,
            "person_set_by": None, "language": "en", "issued": issued, "expires": expires, "identifiers": {}, "quality": "readable", "hash": doc_id, "source": "folder",
            "added": added, "roles": [], "tags": [], "confidential": confidential, "text": "", "translated": None}


def case_dir(tmp_path: Path, *records: dict, name: str = "case") -> Path:
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    (d / "documents.json").write_text(json.dumps({"version": 1, "built": "2026-10-01T02:00:00+00:00", "documents": list(records)}), encoding="utf-8")
    return d


def by_kind(found: list[dict]) -> dict[str, dict]:
    return {d["expiry"]["kind"]: d for d in found}


# --- each kind of document ---------------------------------------------------------------------------------------------


def test_a_passport_in_a_consular_case_is_due_with_its_rule_and_source(tmp_path):
    d = case_dir(tmp_path, record("p1", "passport", expires=iso(40)))
    [one] = expiry.deadlines(d, TODAY, {"consular": True})
    e = one["expiry"]
    assert (one["id"], one["date"], one["days_left"], one["kind"], one["owner"]) == ("expiry.passport.p1", iso(40), 40, "expiry", "paralegal")
    assert e["document"]["type_name"] == "Passport" and e["tracks"] == ["consular"] and e["filing"] is None
    assert "currently valid passport" in e["rule"] and "9 FAM 504.4-4(A)" in e["source"] and "read 10/02/2026" in e["source"]
    assert "consular processing" in one["what"] and "needed for" in one["what"]


def test_a_passport_says_which_tracks_need_it_and_a_case_with_neither_gets_none(tmp_path):
    d = case_dir(tmp_path, record("p1", "passport", expires=iso(40)))
    both = expiry.deadlines(d, TODAY, {"consular": True, "travel": True})[0]
    assert both["expiry"]["tracks"] == ["consular", "travel"] and "consular processing and travel on advance parole" in both["what"]
    assert "does not take the place of a required passport" in both["expiry"]["rule"] and "I-131 Instructions" in both["expiry"]["source"]
    assert expiry.deadlines(d, TODAY, {}) == []  # neither a consular nor a travel case: a passport is no deadline


def test_a_travel_case_is_known_from_an_advance_parole_document(tmp_path):
    d = case_dir(tmp_path, record("p1", "passport", expires=iso(40)), record("ap", "advance_parole", expires=iso(200)))
    found = by_kind(expiry.deadlines(d, TODAY, {}))
    assert found["passport"]["expiry"]["tracks"] == ["travel"]
    assert found["advance_parole"]["expiry"]["filing"] == "i131" and found["advance_parole"]["expiry"]["filing_name"].startswith("I-131")
    assert "no earlier filing window" in found["advance_parole"]["what"]  # no official window exists, so none is invented


def test_a_work_permit_far_off_is_a_deadline_with_the_renewal_window_and_filing(tmp_path):
    d = case_dir(tmp_path, record("w1", "work_permit", expires=iso(300)))
    [one] = expiry.deadlines(d, TODAY)
    e = one["expiry"]
    window = journey.settings()["deadlines"]["ead_renewal_days_before"]
    assert one["date"] == iso(300) and e["opens"] == iso(300 - window) and e["filing"] == "ead" and e["filing_name"] == "I-765 (work permit)"
    assert f"from {(TODAY + timedelta(days=300 - window)).strftime('%m/%d/%Y')}" in one["what"]
    assert "274a.13(e)" in e["source"] and "Form I-765 page" in e["source"]


def test_the_green_card_is_an_i90_for_a_ten_year_card_and_an_i751_for_a_two_year_one(tmp_path):
    ten = case_dir(tmp_path, record("g1", "green_card", issued="2017-01-15", expires=iso(120)), name="ten")
    [i90] = expiry.deadlines(ten, TODAY, {"resident": True})
    assert i90["expiry"]["filing"] == "i90" and i90["expiry"]["filing_name"].startswith("I-90") and "Form I-90" in i90["what"]
    months = journey.settings()["deadlines"]["card_renewal_months_before"]
    assert i90["expiry"]["opens"] == expiry._months_before(TODAY + timedelta(days=120), months).isoformat()
    two = case_dir(tmp_path, record("g2", "green_card", issued="2025-01-15", expires="2027-01-14"), name="two")
    [i751] = expiry.deadlines(two, TODAY, {"resident": True})
    assert i751["expiry"]["filing"] == "i751" and i751["expiry"]["opens"] == "2026-10-16" and "2-year conditional card" in i751["what"]  # 90 days before 01/14/2027
    unknown = case_dir(tmp_path, record("g3", "green_card", expires=iso(120)), name="unknown")
    [either] = expiry.deadlines(unknown, TODAY, {"resident": True})  # no issue date: it can't be told, so no filing is offered
    assert either["expiry"]["filing"] is None and "Form I-751" in either["what"] and "Form I-90" in either["what"]


def test_a_card_valid_two_years_or_less_is_taken_as_conditional_with_no_slack(tmp_path):
    exactly = case_dir(tmp_path, record("g1", "green_card", issued="2025-01-15", expires="2027-01-15"), name="exact")
    assert expiry.deadlines(exactly, TODAY, {"resident": True})[0]["expiry"]["filing"] == "i751"
    a_day_over = case_dir(tmp_path, record("g2", "green_card", issued="2025-01-15", expires="2027-01-16"), name="over")
    assert expiry.deadlines(a_day_over, TODAY, {"resident": True})[0]["expiry"]["filing"] == "i90"  # no slack: nothing in the sources allows one
    assert "inference from the card's validity, not a rule" in expiry.rules()["kinds"]["green_card"]["_conditional"]


def test_an_i94_is_due_until_a_green_card_application_is_filed_and_never_after_it_has_passed(tmp_path):
    d = case_dir(tmp_path, record("a1", "i94", expires=iso(25)))
    [one] = expiry.deadlines(d, TODAY, {})
    assert "Form I-539" in one["what"] and one["expiry"]["filing"] is None and "Form I-539 page" in one["expiry"]["source"]
    assert expiry.deadlines(d, TODAY, {"filed": "i485"}) == []  # a green card application is filed: the stay no longer rides on this date
    lapsed = case_dir(tmp_path, record("a2", "i94", expires=iso(-400)), name="lapsed")
    assert expiry.deadlines(lapsed, TODAY, {}) == []  # an extension had to be filed before: nothing left on this clock


def test_tps_ends_with_the_re_registration_rule_and_no_invented_window(tmp_path):
    d = case_dir(tmp_path, record("t1", "tps_approval", expires=iso(75)))
    [one] = expiry.deadlines(d, TODAY, {})
    assert one["expiry"]["opens"] is None and one["expiry"]["filing"] is None
    assert "Federal Register notice" in one["what"] and "244.17" in one["expiry"]["source"]
    assert expiry.deadlines(case_dir(tmp_path, record("t2", "tps_approval", expires=iso(-30)), name="gone"), TODAY, {}) == []  # an extension may exist


def test_daca_is_filed_by_120_days_before_it_ends_and_a_year_of_grace_after(tmp_path):
    cfg = journey.settings()["deadlines"]
    far = case_dir(tmp_path, record("d1", "daca_approval", expires=iso(300)), name="far")
    [one] = expiry.deadlines(far, TODAY, {"daca": True})
    assert one["date"] == iso(300 - cfg["daca_renewal_by_days"]) and one["expiry"]["opens"] == iso(300 - cfg["daca_renewal_earliest_days"])
    assert one["expiry"]["filing"] == "daca" and one["expiry"]["filing_name"].startswith("DACA renewal") and "from " in one["what"]
    late = case_dir(tmp_path, record("d2", "daca_approval", expires=iso(-100)), name="late")
    [grace] = expiry.deadlines(late, TODAY, {"daca": True})
    assert grace["owner"] == "attorney" and grace["date"] == expiry._plus_years(TODAY - timedelta(days=100), 1).isoformat() and "last day" in grace["what"]
    over = case_dir(tmp_path, record("d3", "daca_approval", expires=iso(-500)), name="over")
    assert expiry.deadlines(over, TODAY, {"daca": True}) == []  # more than a year: a new request, which journey.py raises for the attorney


def test_a_drivers_license_is_due_only_while_the_packet_lists_it_as_identity_and_is_not_yet_filed(tmp_path):
    d = case_dir(tmp_path, record("l1", "drivers_license", expires=iso(20)))
    [one] = expiry.deadlines(d, TODAY, {})  # the I-485 packet's identity exhibit lists it (schemas/packets/i485.json)
    assert "identity document in the packet" in one["what"] and "valid government-issued driver's license" in one["expiry"]["source"]
    assert expiry.deadlines(d, TODAY, {"filed": "i485"}) == []
    assert expiry.deadlines(d, TODAY, {"packet_filing": "daca"}) == []  # a DACA renewal's packet doesn't ask for it (schemas/packets/daca.json)


def test_a_police_clearance_lasts_two_years_from_its_issue_date_not_twelve_months(tmp_path):
    d = case_dir(tmp_path, record("c1", "police_clearance", issued="2025-02-20"))
    [one] = expiry.deadlines(d, TODAY, {"consular": True})
    assert one["date"] == "2027-02-20" and "(two years)" in one["what"] and "two years from the date of their issuance" in one["expiry"]["source"]
    assert expiry.deadlines(d, TODAY, {}) == []  # not a consular case
    assert expiry.deadlines(case_dir(tmp_path, record("c2", "police_clearance"), name="undated"), TODAY, {"consular": True}) == []  # no issue date read


def test_a_leap_day_clearance_ends_on_march_first(tmp_path):
    d = case_dir(tmp_path, record("c1", "police_clearance", issued="2024-02-29"))
    assert expiry.deadlines(d, TODAY, {"consular": True})[0]["date"] == "2026-03-01"


# --- what the case still needs -----------------------------------------------------------------------------------------


def test_a_renewed_passport_replaces_the_old_one_and_each_person_has_their_own(tmp_path):
    d = case_dir(tmp_path, record("old", "passport", expires=iso(-200)), record("new", "passport", expires=iso(2000), added="2026-09-20T10:00:00+00:00"),
                 record("kid", "passport", person="child_1", expires=iso(50)), record("dad", "passport", person="petitioner", expires=iso(10)))
    found = expiry.deadlines(d, TODAY, {"consular": True})
    assert {(x["expiry"]["document"]["id"], x["expiry"]["document"]["person_name"]) for x in found} == {("new", "Applicant"), ("kid", "Child 1")}  # a petitioner's isn't the case's
    assert "Passport (Child 1)" in next(x for x in found if x["expiry"]["document"]["id"] == "kid")["what"]


def test_a_newer_document_whose_date_was_not_read_is_said_not_ignored(tmp_path):
    d = case_dir(tmp_path, record("old", "passport", expires=iso(-5)), record("new", "passport", expires=None, added="2026-09-25T10:00:00+00:00"))
    [one] = expiry.deadlines(d, TODAY, {"consular": True})
    assert one["level"] == "overdue" and "A newer one was added after it but its date was not read" in one["what"]


def test_a_resident_needs_the_green_card_not_the_work_permit_and_a_citizen_needs_nothing(tmp_path):
    d = case_dir(tmp_path, record("w", "work_permit", expires=iso(30)), record("g", "green_card", issued="2017-01-15", expires=iso(30)))
    assert [x["expiry"]["kind"] for x in expiry.deadlines(d, TODAY, {"resident": True})] == ["green_card"]
    assert [x["expiry"]["kind"] for x in expiry.deadlines(d, TODAY, {"resident": False})] == ["ead"]  # no green card case yet, so its card is not waited on
    assert expiry.deadlines(d, TODAY, {"resident": True, "stage": "citizen"}) == []


def test_a_green_card_with_a_renewal_already_filed_is_not_a_deadline(tmp_path):
    d = case_dir(tmp_path, record("g", "green_card", issued="2017-01-15", expires=iso(30)))
    renewal = [{"form": "I-90", "date": iso(-20)}]
    assert expiry.deadlines(d, TODAY, {"resident": True, "notices": renewal}) == []


def test_a_work_permit_in_a_daca_case_carries_dacas_dates(tmp_path):
    d = case_dir(tmp_path, record("w", "work_permit", expires=iso(135)))
    [one] = expiry.deadlines(d, TODAY, {"daca": True})
    assert one["expiry"]["kind"] == "daca" and one["expiry"]["filing"] == "daca"


def test_dates_a_reviewer_reads_off_a_document_start_the_watch_and_survive_reprocessing(tmp_path):
    import documents

    d = case_dir(tmp_path, record("g1", "green_card"), record("l1", "drivers_license"))  # no reader reads these dates
    assert expiry.deadlines(d, TODAY, {"resident": True}) == []
    saved = documents.set_dates(d, "g1", f"2017-01-15|{iso(120)}", "Paulo Paralegal", "paralegal")
    assert (saved["issued"], saved["expires"], saved["dates_set_by"]["who"]) == ("2017-01-15", iso(120), "Paulo Paralegal")
    [one] = expiry.deadlines(d, TODAY, {"resident": True})
    assert one["expiry"]["filing"] == "i90" and one["date"] == iso(120)
    # the next processing run writes the record again without the dates: the reviewer's stay
    rebuilt = documents.merge(documents.read(d), {"documents": [record("g1", "green_card"), record("l1", "drivers_license")]})
    kept = next(r for r in rebuilt["documents"] if r["id"] == "g1")
    assert (kept["issued"], kept["expires"]) == ("2017-01-15", iso(120)) and next(r for r in rebuilt["documents"] if r["id"] == "l1")["expires"] is None
    documents.set_dates(d, "g1", "|", "Paulo Paralegal")  # cleared again
    assert expiry.deadlines(d, TODAY, {"resident": True}) == []
    for bad in ("2026-13-45|", "|soon", f"{iso(10)}|{iso(5)}"):
        with pytest.raises(ValueError):
            documents.set_dates(d, "g1", bad, "Paulo Paralegal")
    with pytest.raises(ValueError):
        documents.set_dates(d, "g1", f"|{iso(5)}", "")  # who made the change is always recorded


def test_no_document_record_means_no_deadline(tmp_path):
    assert expiry.deadlines(tmp_path / "nothing", TODAY) == []
    assert expiry.deadlines(case_dir(tmp_path), TODAY) == []


# --- against journey.py's own deadlines --------------------------------------------------------------------------------


def test_journeys_own_deadline_wins_and_the_radar_fills_the_gaps(tmp_path):
    d = case_dir(tmp_path, record("w1", "work_permit", expires=iso(30)), record("w2", "work_permit", person="spouse", expires=iso(40)),
                 record("g1", "green_card", issued="2017-01-15", expires=iso(30)), record("dc", "daca_approval", expires=iso(100)))
    # journey.py has the client's own work permit, the green card and DACA: the radar adds only the spouse's work permit
    found = expiry.deadlines(d, TODAY, {"ids": {"ead", "i90", "daca"}, "daca": False, "resident": False})
    assert [(x["expiry"]["kind"], x["expiry"]["document"]["person"]) for x in found] == [("ead", "spouse")]
    found = expiry.deadlines(d, TODAY, {"ids": set(), "resident": False})
    assert {x["expiry"]["document"]["id"] for x in found} == {"w1", "w2"}  # journey.py has nothing: the radar fills in


def _bundle(tmp_path: Path, *records: dict, facts: dict | None = None) -> Path:
    d = case_dir(tmp_path, *records, name="bundle")
    (d / "meta.json").write_text(json.dumps({"classifications": {}}), encoding="utf-8")
    (d / "fact_graph.json").write_text("{}", encoding="utf-8")
    return d


def test_a_document_list_of_the_wrong_shape_is_said_too(tmp_path):
    d = _bundle(tmp_path, record("w9", "work_permit", expires=iso(400)))
    (d / "documents.json").write_text(json.dumps({"documents": "x"}), encoding="utf-8")  # valid JSON, not a list of records
    j = journey.journey(d, TODAY, graph=FactGraph("c"))
    assert [s["id"] for s in j["steps"] if s["id"] == "expiry.unreadable"] == ["expiry.unreadable"]


def test_a_filing_record_with_no_carrier_still_reads(tmp_path):
    d = _bundle(tmp_path, record("w9", "work_permit", expires=iso(400)))
    (d / "status.json").write_text(json.dumps({"filings": [{"filing": "i485", "title": "I-485 application", "mailed_on": "2026-09-01"}]}), encoding="utf-8")
    j = journey.journey(d, TODAY, graph=FactGraph("c"))
    mailed = [e for e in j["timeline"] if e["kind"] == "filed"]
    assert mailed and mailed[0]["what"].startswith("Mailed: I-485 application (carrier not recorded)") and "recorded by ?" in mailed[0]["what"]


def test_the_radar_is_part_of_journeys_deadline_list(tmp_path):
    d = _bundle(tmp_path, record("w9", "work_permit", expires=iso(400)))
    g = FactGraph("c")
    j = journey.journey(d, TODAY, graph=g)
    ids = [x["id"] for x in j["deadlines"]]
    assert "expiry.ead.w9" in ids and "ead" not in ids  # the renewal window is far off: journey.py has no deadline yet, the radar does
    summary = journey.summary(j)
    kept = next(x for x in summary["deadlines"] if x["id"] == "expiry.ead.w9")
    assert kept["expiry"]["filing"] == "ead" and kept["expiry"]["source"]  # the summary keeps what the list needs


def test_a_damaged_document_list_is_said_not_hidden_and_the_rest_of_the_case_still_reads(tmp_path):
    d = _bundle(tmp_path, record("w9", "work_permit", expires=iso(400)))
    (d / "documents.json").write_text("{not json", encoding="utf-8")
    j = journey.journey(d, TODAY, graph=FactGraph("c"))
    [warning] = [s for s in j["steps"] if s["id"] == "expiry.unreadable"]
    assert warning["owner"] == "paralegal" and warning["urgent"] and "not being watched" in warning["text"]
    assert not [x for x in j["deadlines"] if x["id"].startswith("expiry.")] and j["stage"]


def test_journeys_own_work_permit_deadline_replaces_the_radars(tmp_path):
    d = _bundle(tmp_path, record("w9", "work_permit", expires=iso(60)))
    g = FactGraph("c")
    g.add_source("applicant.ead_expiration_date", "w9.pdf", "work_permit", iso(60), iso(60), 0.98)
    j = journey.journey(d, TODAY, graph=g)
    ids = [x["id"] for x in j["deadlines"]]
    assert "ead" in ids and not [i for i in ids if i.startswith("expiry.ead")]


# --- the "passed" display rule -----------------------------------------------------------------------------------------


def test_an_expired_document_is_never_history_however_long_ago(tmp_path):
    d = case_dir(tmp_path, record("p1", "passport", expires=iso(-900)))
    [one] = expiry.deadlines(d, TODAY, {"consular": True})
    assert not history_once_missed(one["id"])
    row = {"id": "c1", "summary": {"name": "ANA CLARA EXEMPLO SOUZA"},
           "journey": {"deadlines": [{k: one[k] for k in ("id", "date", "what", "owner")} | {"expiry": one["expiry"]}]}}
    due = firm_deadlines([row], horizon_days=60)
    assert due and due[0]["level"] == "overdue" and due[0]["days_left"] < -PASSED_DAYS  # still red: the document still has to be replaced
    assert due[0]["expiry"]["document"]["type_name"] == "Passport"  # and the list keeps what it is


# --- the rules file ----------------------------------------------------------------------------------------------------


def test_every_rule_has_its_source_and_the_day_it_was_read_and_every_filing_is_real():
    import packet

    rules = expiry.rules()
    assert set(rules["kinds"]) == {"ead", "green_card", "daca", "tps", "advance_parole", "i94", "passport", "drivers_license", "police_clearance"}
    for kind, spec in rules["kinds"].items():
        sources = [t["source"] for t in spec["tracks"].values()] if "tracks" in spec else [spec["source"]]
        assert all(re.search(r"read 10/02/2026", s) or "src/daca.py" in s for s in sources), kind
        assert spec["after_end"] in ("keep", "drop") and spec["types"]
        assert {spec["filing"], spec.get("filing_conditional")} - {None} <= set(packet.FILINGS), kind
    taxonomy = {t["id"]: t for t in json.loads((schema_path.path("register", "document_types")).read_text(encoding="utf-8"))["types"]}
    for spec in rules["kinds"].values():
        for t in spec["types"]:
            assert t in taxonomy or t == "police_certificate", t  # every type the radar reads is one the taxonomy names
            if t in taxonomy and t != "police_clearance":
                assert taxonomy[t]["expires"], t


def test_what_the_rules_say_on_screen_is_plain_words_with_the_urls_kept_in_the_file():
    for name, spec in expiry.rules()["kinds"].items():
        parts = [spec] + list(spec.get("tracks", {}).values())
        assert all(p["url"] and all(u.startswith("https://") for u in p["url"]) for p in parts if "source" in p), name  # the pages themselves
        shown = [spec.get("rule", ""), spec.get("source", "")] + [t[k] for t in spec.get("tracks", {}).values() for k in ("name", "rule", "source")]
        for text in shown:
            assert not re.search(r"\.(pdf|json|py|html)\b|schemas/|src/|https?:|uscis\.gov|ecfr|fam\.state|CT:VISA|radar", text, re.I), (name, text)
            assert " -- " not in text and "—" not in text, (name, text)


def test_the_windows_the_radar_uses_are_the_case_timelines_own():
    cfg = journey.settings()["deadlines"]
    assert (cfg["ead_renewal_days_before"], cfg["card_renewal_months_before"], cfg["daca_renewal_earliest_days"], cfg["daca_renewal_by_days"]) == (180, 6, 150, 120)
    text = json.dumps(expiry.rules())
    assert "180 days" in text and "six months" in text and "120 and 150 days" in text  # the sources say the same numbers


def test_the_rules_file_is_dumped_the_way_the_other_schemas_are():
    path = Path(expiry.RULES)
    assert path.read_text(encoding="utf-8").replace("\r\n", "\n") == json.dumps(json.loads(path.read_text(encoding="utf-8")), indent=2, ensure_ascii=False) + "\n"


# --- the firm-wide list: by office and by the person responsible ---------------------------------------------------------


def _row(cid: str, name: str, office: str, *deadlines: dict) -> dict:
    return {"id": cid, "summary": {"name": name}, "office": office,
            "journey": {"deadlines": [{k: d[k] for k in ("id", "date", "what", "owner")} | {"expiry": d["expiry"]} for d in deadlines]}}


@pytest.fixture
def firm(tmp_path):
    """Three made-up cases in two offices: a passport (Boston), a work permit (Boston), a protected case's passport (Miami)."""
    root = tmp_path / "clients"
    root.mkdir()
    ana = case_dir(root, record("a1", "passport", expires=iso(15)), name="ana")
    maria = case_dir(root, record("m1", "work_permit", expires=iso(200)), name="maria")
    rosa = case_dir(root, record("r1", "passport", expires=iso(30), confidential="1367"), name="rosa")
    rows = [_row("ana", "ANA CLARA EXEMPLO SOUZA", "Massachusetts", *expiry.deadlines(ana, TODAY, {"consular": True})),
            _row("maria", "MARIA EXEMPLO", "Massachusetts", *expiry.deadlines(maria, TODAY)),
            _row("rosa", "ROSA EXEMPLO", "Florida", *expiry.deadlines(rosa, TODAY, {"consular": True})),
            {"id": "joao", "summary": {"name": "JOAO EXEMPLO"}, "office": "Florida", "journey": {"deadlines": []}}]
    (ana / "decisions.json").write_text(json.dumps({"i1": {"reviewer": "Paulo Paralegal", "at": "2026-09-01T10:00:00+00:00"},
                                                    "i2": {"reviewer": "Ana Attorney", "at": "2026-09-20T10:00:00+00:00"}}), encoding="utf-8")
    (rosa / "decisions.json").write_text(json.dumps({"i1": {"reviewer": "Paulo Paralegal", "at": "2026-09-02T10:00:00+00:00"}}), encoding="utf-8")
    return rows, root


def at(today: date):
    from datetime import datetime

    return datetime(today.year, today.month, today.day, 9, 0)


def test_the_list_runs_soonest_first_inside_the_horizon_and_names_the_filing(firm):
    rows, root = firm
    got = firm_list.expiring(rows, root, today=at(TODAY), horizon_days=90)
    assert [(i["name"], i["document"]) for i in got["items"]] == [("ANA CLARA EXEMPLO SOUZA", "Passport"), ("ROSA EXEMPLO", "Passport")]  # Maria's is 200 days off
    first = got["items"][0]
    assert (first["date"], first["days_left"], first["whose"], first["level"]) == (iso(15), 15, "Applicant", "soon")
    wide = firm_list.expiring(rows, root, today=at(TODAY), horizon_days=365)
    maria = next(i for i in wide["items"] if i["name"] == "MARIA EXEMPLO")
    assert maria["filing"] == "ead" and maria["filing_name"] == "I-765 (work permit)" and "Form I-765 page" in maria["source"]


def test_the_list_filters_by_office_and_says_which_offices_there_are(firm):
    rows, root = firm
    got = firm_list.expiring(rows, root, today=at(TODAY), horizon_days=365, office="Massachusetts")
    assert {i["name"] for i in got["items"]} == {"ANA CLARA EXEMPLO SOUZA", "MARIA EXEMPLO"}
    assert got["offices"] == ["Florida", "Massachusetts"]  # the choices don't shrink with the filter
    assert {i["name"] for i in firm_list.expiring(rows, root, today=at(TODAY), horizon_days=365, office="Florida", role="attorney")["items"]} == {"ROSA EXEMPLO"}


def test_the_person_responsible_is_the_reviewer_of_the_latest_decision(firm):
    rows, root = firm
    got = firm_list.expiring(rows, root, today=at(TODAY), horizon_days=365)
    who = {i["name"]: i["reviewer"] for i in got["items"]}
    assert who == {"ANA CLARA EXEMPLO SOUZA": "Ana Attorney", "MARIA EXEMPLO": None, "ROSA EXEMPLO": "Paulo Paralegal"}
    assert got["reviewers"] == ["Ana Attorney", "Paulo Paralegal", firm_list.NOBODY]
    assert [i["name"] for i in firm_list.expiring(rows, root, today=at(TODAY), horizon_days=365, reviewer="Ana Attorney")["items"]] == ["ANA CLARA EXEMPLO SOUZA"]
    assert [i["name"] for i in firm_list.expiring(rows, root, today=at(TODAY), horizon_days=365, reviewer=firm_list.NOBODY)["items"]] == ["MARIA EXEMPLO"]


def test_a_protected_cases_documents_are_for_an_attorney_and_a_paralegal_is_told_how_many(firm):
    rows, root = firm
    para = firm_list.expiring(rows, root, today=at(TODAY), horizon_days=365, role="paralegal")
    assert "ROSA EXEMPLO" not in {i["name"] for i in para["items"]} and para["restricted"] == 1
    att = firm_list.expiring(rows, root, today=at(TODAY), horizon_days=365, role="attorney")
    assert "ROSA EXEMPLO" in {i["name"] for i in att["items"]} and att["restricted"] == 0


def test_nothing_on_screen_is_an_id_or_a_file_name(firm):
    rows, root = firm
    for i in firm_list.expiring(rows, root, today=at(TODAY), horizon_days=365)["items"]:
        shown = " ".join(str(i[k]) for k in ("document", "whose", "what", "filing_name") if i[k])
        assert not re.search(r"\.pdf|expiry\.|_", shown), shown  # no file names, ids or keys
        assert " -- " not in shown and "—" not in shown  # no em dashes on screen
        assert re.search(r"\d\d/\d\d/\d{4}", shown) and not re.search(r"\d{4}-\d{2}-\d{2}", shown)  # dates as MM/DD/YYYY
