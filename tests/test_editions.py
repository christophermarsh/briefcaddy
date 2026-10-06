"""A new USCIS edition (src/editions.py): what the nightly check reads off the form's page, what it holds, and what the firm is told.

The two pages below are copied from uscis.gov/i-485 and uscis.gov/i-864 as read on 10/02/2026 (the alert above the form details,
nothing else kept); the grace words must come out of them exactly as USCIS wrote them, and a grace period is never inferred.
"""

from __future__ import annotations

import json
import shutil
from datetime import date

import pytest

import deployment
import editions
import maintenance
import prefile
import schema_path

I485_PAGE = """<div class="content-section"><div class="alert-message"><div class="messages messages--info "><div class="messages__text">
<p><strong>ALERT:</strong> On Sept. 4, 2026, USCIS published the&nbsp;<a href="https://example.org">Registration of Lawful Permanent Residence for Children
Born to Foreign Government Employees</a>, Interim Final Rule and a new 09/04/26 edition of the Form I-485, Application to Register Permanent Residence or Adjust Status.</p>
</div></div></div>
<div class="alert-message"><div class="messages messages--info "><div class="messages__text">
<p><strong>ALERT:</strong>&nbsp;On Sept. 18, 2026, USCIS published a revised edition of&nbsp;<a href="/i-485">Form I-485, Application to Register Permanent Residence or
Adjust Status</a> (edition date: 09/18/26). The form has been revised to align with the recently announced&nbsp;<a href="https://example.org">Public Charge Ground of
Inadmissibility</a> Final Rule. There is <strong>no grace period&nbsp;</strong>for the revised edition of Form I-485 because this revision is necessary for USCIS to apply
the final rule. Please note that USCIS will:&nbsp;</p><ul><li>Reject the 01/20/25 and 09/04/26 edition of Form I-485 if it is postmarked or electronically submitted
<strong>on or after</strong> Sept. 18, 2026; and</li><li>Only accept the 09/18/26 edition of Form I-485 if it is postmarked or electronically submitted <strong>on or after</strong>
Sept. 18, 2026.</li></ul></div></div></div></div>
<p>Edition Date 09/18/26</p>"""

I864_PAGE = """<div class="alert-message"><div class="messages messages--info "><div class="messages__text">
<p paraid="1"><strong>ALERT: </strong>On Aug. 31, 2026, USCIS published a new edition of <a href="/i-864">Form I-864, Affidavit of Support Under Section 213A of the INA </a>(edition date: 08/24/26).
USCIS is providing a 30-day grace period during which we will accept the 10/17/24 edition of Form I-864.&nbsp;&nbsp;</p><p paraid="1">Beginning Oct. 1, 2026, we will only accept
the 08/24/26 edition of Form I-864. USCIS will not process any 10/17/24 edition of Form I-864 postmarked or electronically submitted on or after Oct. 1, 2026. USCIS does not reject
Form I-485, Application to Register Permanent Residence or Adjust Status, if it is filed with a previous edition of Form I-864. If an edition other than 08/24/26 is submitted on or
after Oct. 1, 2026, USCIS will follow 8 CFR 103.2(b)(8) regarding the applicant&rsquo;s failure to submit the required initial evidence.&nbsp;&nbsp;</p>
<p><strong>Please note:</strong> The 08/24/26 edition of Form I-864 includes a privacy release.</p></div></div></div>
<p>Edition Date 08/24/26</p>"""

NO_GRACE = ("There is no grace period for the revised edition of Form I-485 because this revision is necessary for USCIS to apply the final rule.")
REJECT = "Reject the 01/20/25 and 09/04/26 edition of Form I-485 if it is postmarked or electronically submitted on or after Sept. 18, 2026; and"
ONLY = "Only accept the 09/18/26 edition of Form I-485 if it is postmarked or electronically submitted on or after Sept. 18, 2026."


def _result(page, ours, theirs, name="Form I-485", url="https://www.uscis.gov/i-485", today=date(2026, 10, 2)):
    out = {"ok": False, "ours": ours, "uscis": theirs, "form": name}
    return out | editions.read_page(url, ours, theirs, lambda u: page, None, today, name)


def test_the_grace_words_are_copied_from_the_alert_about_the_new_edition_only():
    r = _result(I485_PAGE, "01/20/25", "09/18/26")
    g = r["grace"]
    assert g["sentences"] == [NO_GRACE, REJECT, ONLY]  # word for word; the 09/04/26 alert is about another edition and isn't used
    assert g["no_grace"] and g["refused_from"] == "2026-09-18" and r["published"] == "2026-09-18"
    assert r["page"] == "https://www.uscis.gov/i-485" and r["read_on"] == "2026-10-02"
    r = _result(I864_PAGE, "10/17/24", "08/24/26", "Form I-864", "https://www.uscis.gov/i-864")
    assert r["grace"]["sentences"] == [
        "USCIS is providing a 30-day grace period during which we will accept the 10/17/24 edition of Form I-864.",
        "Beginning Oct. 1, 2026, we will only accept the 08/24/26 edition of Form I-864.",
        "USCIS will not process any 10/17/24 edition of Form I-864 postmarked or electronically submitted on or after Oct. 1, 2026."]
    assert r["grace"]["grace_stated"] and not r["grace"]["no_grace"] and r["grace"]["refused_from"] == "2026-10-01" and r["published"] == "2026-08-31"


def test_a_stated_grace_lets_the_old_edition_through_until_its_date_and_no_grace_never_does():
    r = _result(I864_PAGE, "10/17/24", "08/24/26", "Form I-864", "https://www.uscis.gov/i-864")
    inside = editions.verdict(r, date(2026, 9, 30))
    assert inside["held"] is False and inside["accepted_until"] == "2026-10-01"
    assert editions.verdict(r, date(2026, 10, 1))["held"] and editions.verdict(r, date(2026, 10, 2))["held"]  # from the day USCIS refuses it
    none = _result(I485_PAGE, "01/20/25", "09/18/26")
    assert editions.verdict(none, date(2026, 9, 1))["held"]  # "no grace period": held even before the date it names


def test_a_page_that_says_nothing_gives_no_grace_and_never_a_guess():
    quiet = '<div class="messages__text"><p>ALERT: A new 09/18/26 edition of Form I-485 is available.</p></div>'
    r = _result(quiet, "01/20/25", "09/18/26")
    assert r["grace"]["sentences"] == [] and r["grace"]["refused_from"] is None
    v = editions.verdict(r, date(2026, 10, 2))
    assert v["held"] and "ALERT: A new 09/18/26 edition of Form I-485 is available." in v["grace"] and "a person reads them" in v["grace"]  # an alert with no date: shown whole
    nothing = editions.verdict(_result("<p>no alert</p>", "01/20/25", "09/18/26"), date(2026, 10, 2))
    assert nothing["held"] and "says nothing about a grace period, so none is assumed" in nothing["grace"]  # no alert about the change at all
    # no alert at all, a page that can't be read, a form with no USCIS page: all held, none inferred
    assert editions.verdict(_result("<p>no alert</p>", "01/20/25", "09/18/26"), date(2026, 10, 2))["held"]
    broken = {"ok": False, "ours": "01/20/25", "uscis": "09/18/26", "form": "Form I-485"} | editions.read_page("https://www.uscis.gov/i-485", "01/20/25", "09/18/26",
                                                                                                                 lambda u: 1 / 0, None, date(2026, 10, 2), "Form I-485")
    assert broken["grace"] is None and editions.verdict(broken, date(2026, 10, 2))["held"]
    assert "No grace period is assumed: the page could not be read." in editions.verdict(broken, date(2026, 10, 2))["grace"] and "ZeroDivision" not in editions.verdict(broken, date(2026, 10, 2))["grace"]
    court = {"ok": False, "ours": "Feb. 2025", "uscis": "Jan. 2027", "form": "Form EOIR-28"} | editions.read_page(None, "Feb. 2025", "Jan. 2027", None, None, date(2026, 10, 2))
    assert editions.verdict(court, date(2026, 10, 2))["headline"].startswith("The immigration court changed Form EOIR-28")


def test_only_the_stated_shapes_give_a_date():
    def page(sentence):
        return f'<div class="messages__text"><p>On Oct. 1, 2026, USCIS published a new edition (edition date: 11/01/26). {sentence}</p></div>'

    def refused(sentence):
        return editions.read_alerts(page(sentence), "01/20/25", "11/01/26")["refused_from"]

    assert refused("You may also use the 01/20/25 edition until Dec. 1, 2026.") == "2026-12-01"  # "until": not on the day itself (the cautious reading)
    assert refused("You may use the 01/20/25 edition through Dec. 1, 2026.") == "2026-12-02"  # "through" includes it
    assert refused("USCIS will reject the 01/20/25 edition postmarked after Dec. 1, 2026.") == "2026-12-02"
    assert refused("Please use the new edition soon, within a month or so.") is None  # no date, no grace
    assert refused("The 03/01/24 edition may be used until Dec. 1, 2026.") is None  # another edition's date: not ours to read
    assert refused("Some other edition may be used until Dec. 1, 2026.") is None  # "edition" and a date, but not about ours or the old one


def test_the_nightly_check_keeps_what_it_read_and_when_it_first_saw_the_change(tmp_path, monkeypatch):
    path = tmp_path / "maintenance.json"
    shutil.copy(maintenance.REGISTRY, path)
    monkeypatch.setattr(maintenance, "LAST_LIVE", tmp_path / "live.json")
    template = (schema_path.path("template", "i485")).read_bytes()
    real = maintenance.pdf_edition
    monkeypatch.setattr(maintenance, "pdf_edition", lambda data: "01/20/27" if data == b"THE NEW PDF" else real(data))  # the edition is in a compressed stream: a stand-in for the new file

    def get(url, binary=False, timeout=60):
        if url.endswith("i-485.pdf"):
            return b"THE NEW PDF"
        if url == "https://www.uscis.gov/i-485":
            return I485_PAGE.replace("09/18/26", "01/20/27").replace("01/20/25 and", "09/18/26 and")
        if url.endswith(".pdf"):
            return template if "i-485" in url else (schema_path.path("template", "i864")).read_bytes()
        raise OSError("offline")

    out = maintenance.live_checks(path, get=get)["results"]["form_i485"]
    assert out["ok"] is False and out["uscis"] == "01/20/27" and out["form"] == "Form I-485" and out["page"] == "https://www.uscis.gov/i-485"
    assert out["grace"]["no_grace"] and out["read_on"] and out["noticed"]
    assert out["finding"].startswith("USCIS changed Form I-485 on 09/18/2026; packets that use it wait for the update.")
    assert "says: “There is no grace period" in out["finding"]
    # the next night: the day it was first seen is kept
    saved = json.loads((tmp_path / "live.json").read_text())
    saved["results"]["form_i485"]["noticed"] = "2026-09-20"
    (tmp_path / "live.json").write_text(json.dumps(saved), encoding="utf-8")
    assert maintenance.live_checks(path, get=get)["results"]["form_i485"]["noticed"] == "2026-09-20"
    # a form that is current has no hold, and Keeping current lists the form as due with the finding
    by = {i["id"]: i for i in maintenance.status(date(2026, 10, 15), path)}
    assert by["form_i485"]["due"] and by["form_i485"]["findings"][0].startswith("USCIS changed Form I-485")
    assert "form" not in maintenance.live_checks(path, get=get)["results"]["form_i864"]


def test_the_firm_is_told_the_provider_is_updating_it(tmp_path, monkeypatch):
    default = deployment.DEFAULT["provider"]["name"]
    assert editions.updating(default, default) == "Contact your provider about the required edition update."
    assert editions.updating("Acme Legal Software", default) == "Contact your provider (Acme Legal Software) about the required edition update."


# -- "Ready to mail?" and the packet tab ---------------------------------------------------------------------------


def _checks(tmp_path, result, today):
    """The edition check of a one-form packet whose live result is `result`."""
    live = tmp_path / "live.json"
    live.write_text(json.dumps({"at": "2026-10-02T00:00:00+00:00", "results": {"form_i485": result}}), encoding="utf-8")
    return live, prefile._edition_check("edition_i485", "I-485", "USCIS", result, today)


def test_a_held_packet_does_not_go_out_and_says_why(tmp_path):
    r = _result(I485_PAGE, "01/20/25", "09/18/26")
    _live, check = _checks(tmp_path, r, date(2026, 10, 2))
    assert check["level"] == "fail" and check["title"] == "I-485 edition"
    assert check["text"].startswith("USCIS changed Form I-485 on 09/18/2026; packets that use it wait for the update.")
    assert NO_GRACE in check["text"] and check["text"].endswith("Contact your provider about the required edition update.")


def test_a_packet_inside_a_stated_grace_goes_with_a_warning_and_the_last_day(tmp_path):
    r = _result(I864_PAGE, "10/17/24", "08/24/26", "Form I-864", "https://www.uscis.gov/i-864")
    _live, check = _checks(tmp_path, r, date(2026, 9, 25))
    assert check["level"] == "warn"
    assert "the old edition (10/17/24) is still accepted before 10/01/2026; review the replacement edition before that date" in check["text"] and "30-day grace period" in check["text"]
    assert "packets that use it wait" not in check["text"] and "Send the packet before 10/01/2026, or wait for the update." in check["text"]
    assert editions.verdict(r, date(2026, 10, 1))["headline"].endswith("packets that use it wait for the update.")
    assert _checks(tmp_path, r, date(2026, 10, 1))[1]["level"] == "fail"


def test_the_packet_tab_lists_the_held_forms_once_each(tmp_path):
    r = _result(I485_PAGE, "01/20/25", "09/18/26")
    live, _ = _checks(tmp_path, r, date(2026, 10, 2))
    holds = prefile.edition_holds([{"id": "i485", "short": "I-485"}, {"id": "g28", "short": "G-28"}, {"id": "i485", "short": "I-485"}], date(2026, 10, 2), live)
    assert [h["id"] for h in holds] == ["form_i485"]
    h = holds[0]
    assert h["held"] and h["headline"].startswith("USCIS changed Form I-485 on 09/18/2026") and h["updating"] == "Contact your provider about the required edition update."
    assert h["page"] == "https://www.uscis.gov/i-485" and h["read_on"] == "2026-10-02"
    assert prefile.edition_holds([{"id": "g28", "short": "G-28"}], date(2026, 10, 2), live) == []  # a form with no new edition: nothing


# -- what the firm and the packet screens are given ------------------------------------------------------------------------


def test_keeping_current_says_to_the_firm_what_changed_and_whose_job_it_is(tmp_path, monkeypatch):
    from review.server import ReviewApp

    dep = tmp_path / "deployment.json"
    dep.write_text(json.dumps({"mode": "hosted", "provider": {"name": "Acme Legal Software"}}), encoding="utf-8")
    monkeypatch.setattr(deployment, "PATH", dep)
    monkeypatch.setattr(maintenance, "FIRM_LOG", tmp_path / "firm_log.json")
    live = tmp_path / "live.json"
    r = _result(I485_PAGE, "01/20/25", "09/18/26")
    r["finding"] = editions.finding(r)  # as the nightly check records it: this is what makes the item due
    live.write_text(json.dumps({"at": "2026-10-02T00:00:00+00:00", "results": {"form_i485": r, "form_g28": {"ok": True, "ours": "09/17/18", "uscis": "09/17/18"}}}), encoding="utf-8")
    monkeypatch.setattr(maintenance, "LAST_LIVE", live)
    data = tmp_path / "clients"
    data.mkdir()
    repo = maintenance.REPO
    m = ReviewApp(data, schema_path.path("field_map", "i485", schema_path.schemas_in(repo)), schema_path.path("template", "i485", schema_path.schemas_in(repo)), None).maintenance()
    [held] = m["held"]
    assert held["id"] == "form_i485" and held["held"] and held["updating"] == "Contact your provider (Acme Legal Software) about the required edition update."
    assert held["headline"] == "USCIS changed Form I-485 on 09/18/2026; packets that use it wait for the update." and NO_GRACE in held["grace"]
    ours = {i["id"]: i for i in m["provider_items"]}
    assert ours["form_i485"]["status"] == "Needs attention" and ours["form_i485"]["note"] == held["headline"] + " " + held["updating"]  # the firm's row
    assert ours["form_g28"]["status"] == "Reviewed within cadence" and "note" not in ours["form_g28"]
    page = json.dumps(m)
    for word in ("schemas/", "src/", "tools/", ".py\""):
        assert word not in page, word


# -- the reader must not give a grace to the wrong form or edition --------------------------------------------------------


def _read864(sentence, ours="10/17/24", theirs="08/24/26", form="Form I-864"):
    page = f'<div class="messages__text"><p>ALERT: On Aug. 31, 2026, USCIS published a new edition of Form I-864 (edition date: {theirs}). {sentence}</p></div>'
    return editions.read_alerts(page, ours, theirs, form)


@pytest.mark.parametrize("sentence", [
    "USCIS will accept the previous edition of Form I-693 until Dec. 31, 2026.",  # another form
    "USCIS will accept the previous edition of Form I-864 until Dec. 31, 2026.",  # "previous" is not our edition's own date
    "USCIS will accept the previous edition (04/01/26) through Dec. 1, 2026.",  # an edition that isn't ours
    "USCIS will not reject the 10/17/24 edition if postmarked on or after Oct. 15, 2026.",  # the opposite of a refusal
    "USCIS does not reject the 10/17/24 edition postmarked after Oct. 15, 2026.",
    "USCIS will reject the 10/17/24 edition of Form I-693 on or after Dec. 31, 2026.",  # another form reusing our edition string
    "Beginning Dec. 31, 2026, we will only accept the 08/24/26 edition of Form I-693.",
    "USCIS will accept the 10/17/24 edition of Form I-693 until Dec. 31, 2026.",
    "USCIS does not reject Form I-485 if it is filed with a previous edition of Form I-864 until Dec. 31, 2026.",
])
def test_a_sentence_about_another_form_or_edition_or_a_not_reject_gives_no_date(sentence):
    got = _read864(sentence)
    assert got["refused_from"] is None, sentence
    assert got["unreadable"] is True  # so the whole alert is shown, never "says nothing"


@pytest.mark.parametrize("sentence,refused", [
    ("USCIS will accept the 10/17/24 edition of Form I-864 until Oct. 15, 2026.", "2026-10-15"),
    ("USCIS will accept the 10/17/24 edition through Oct. 15, 2026.", "2026-10-16"),  # names no form: it is about this page's
    ("USCIS will accept the 10/17/24 edition of Form I-864 and Form I-864A through Oct. 15, 2026.", "2026-10-16"),
    ("USCIS will reject the 10/17/24 edition of Form I-864 on or after Oct. 15, 2026.", "2026-10-15"),
])
def test_a_sentence_that_names_our_form_or_none_and_our_edition_gives_its_date(sentence, refused):
    assert _read864(sentence)["refused_from"] == refused


@pytest.mark.parametrize("sentence", [
    "USCIS will accept the 10/17/24 edition on or before Oct. 15, 2026.",
    "You may use the 10/17/24 edition by Oct. 15, 2026.",
    "USCIS will accept the 10/17/24 edition no later than Oct. 15, 2026.",
    "USCIS will accept the 10/17/24 edition through 10/15/26.",
    "USCIS will accept the 10/17/24 edition through September 18th, 2027.",
])
def test_a_sentence_in_an_unlisted_shape_holds_and_shows_the_whole_alert_not_nothing(sentence):
    r = {"ok": False, "ours": "10/17/24", "uscis": "08/24/26", "form": "Form I-864", "page": "https://www.uscis.gov/i-864", "read_on": "2026-10-02"}
    r["grace"] = _read864(sentence)
    r["published"] = r["grace"].pop("published")
    v = editions.verdict(r, date(2026, 10, 2))
    assert v["held"] and sentence in v["grace"] and "We could not read a date from USCIS's sentences; a person reads them." in v["grace"]
    assert "says nothing about a grace period" not in v["grace"]


def test_a_renamed_alert_class_is_still_read_from_the_pages_own_sentences():
    page = ('<div class="usa-alert__text"><p>On Aug. 31, 2026, USCIS published a new edition of Form I-864 (edition date: 08/24/26). Beginning Oct. 1, 2026, we will only '
            'accept the 08/24/26 edition of Form I-864.</p></div><p>Edition Date 08/24/26</p>')
    got = editions.read_alerts(page, "10/17/24", "08/24/26", "Form I-864")
    assert got["refused_from"] == "2026-10-01" and got["sentences"] == ["Beginning Oct. 1, 2026, we will only accept the 08/24/26 edition of Form I-864."]
    odd = '<div class="usa-alert__text"><p>Use the 08/24/26 edition by Oct. 1, 2026 and accept no other.</p></div>'
    got = editions.read_alerts(odd, "10/17/24", "08/24/26", "Form I-864")
    assert got["unreadable"] and "by Oct. 1, 2026" in got["alert"]  # a page that says something is never "says nothing"


# -- one failed night does not release a hold ---------------------------------------------------------------------------------


def test_a_failed_night_between_two_good_ones_keeps_the_hold_and_the_first_seen_date(tmp_path, monkeypatch):
    item = next(i for i in maintenance.registry()["items"] if i["id"] == "form_i485")
    path = tmp_path / "maintenance.json"
    path.write_text(json.dumps({"items": [item]}), encoding="utf-8")
    monkeypatch.setattr(maintenance, "LAST_LIVE", tmp_path / "live.json")
    monkeypatch.setattr(maintenance, "pdf_edition", lambda data: "09/18/26" if data == b"NEW" else "01/20/25")
    page = '<div class="messages__text"><p>ALERT: A new 09/18/26 edition of Form I-485 is out. There is no grace period.</p></div>'
    mode = {"fail": False}

    def get(url, binary=False, timeout=60):
        if url.endswith(".pdf"):
            if mode["fail"]:
                raise OSError("403 from USCIS")
            return b"NEW"
        return page

    def night(day, fail=False):
        mode["fail"] = fail
        monkeypatch.setattr(editions.clock, "today", lambda: date.fromisoformat(day))
        monkeypatch.setattr(maintenance.clock, "today", lambda: date.fromisoformat(day))
        return maintenance.live_checks(path, get=get)["results"]["form_i485"]

    one = night("2026-10-02")
    assert one["ok"] is False and one["noticed"] == "2026-10-02" and one["checked_on"] == "2026-10-02"
    two = night("2026-10-03", fail=True)  # USCIS's file couldn't be fetched
    assert two["ok"] is False and two["noticed"] == "2026-10-02" and two["recheck_failed_on"] == "2026-10-03"
    assert "It could not be re-checked on 10/03/2026." in two["finding"] and two["finding"].startswith("USCIS changed Form I-485 (new edition 09/18/26, first seen 10/02/2026)")
    three = night("2026-10-04", fail=True)  # a second failed night: stated once, not twice
    assert three["finding"].count("could not be re-checked") == 1 and three["recheck_failed_on"] == "2026-10-04"
    four = night("2026-10-05")
    assert four["ok"] is False and four["noticed"] == "2026-10-02" and "recheck_failed_on" not in four  # back: the first-seen date kept
    # and on the failed night "Ready to mail?" still stops the packet, with the same words
    check = prefile._edition_check("edition_i485", "I-485", "USCIS", two, date(2026, 10, 3))
    assert check["level"] == "fail" and "could not be re-checked on 10/03/2026" in check["text"] and "There is no grace period" in check["text"]


def test_a_page_that_cannot_be_read_again_keeps_what_it_said_the_night_before():
    first = {"ok": False, "ours": "10/17/24", "uscis": "08/24/26", "form": "Form I-864"} | editions.read_page(
        "https://www.uscis.gov/i-864", "10/17/24", "08/24/26", lambda u: I864_PAGE, None, date(2026, 10, 2), "Form I-864")
    again = {"ok": False, "ours": "10/17/24", "uscis": "08/24/26", "form": "Form I-864"} | editions.read_page(
        "https://www.uscis.gov/i-864", "10/17/24", "08/24/26", lambda u: 1 / 0, first, date(2026, 10, 3), "Form I-864")
    assert again["grace"] == first["grace"] and again["read_on"] == "2026-10-02" and again["noticed"] == first["noticed"]
    words = editions.verdict(again, date(2026, 10, 3))["grace"]
    assert "the page could not be read again on 10/03/2026; this is what it said on 10/02/2026" in words and "ZeroDivision" not in words


def test_an_edition_check_that_never_ran_is_not_a_pass(tmp_path):
    today = date(2026, 10, 3)
    unknown = {"ok": None, "finding": "Couldn't check (OSError). Check by hand: https://www.uscis.gov/i-485"}
    check = prefile._unchecked_edition("edition_i485", "I-485", "USCIS", unknown, today)
    assert check["level"] == "fail" and "couldn't be checked against USCIS" in check["text"] and "OSError" not in check["text"]
    carried = editions.carry({"ok": True, "ours": "09/18/26", "uscis": "09/18/26", "form": "Form I-485", "checked_on": "2026-10-01"}, today)
    assert prefile._unchecked_edition("edition_i485", "I-485", "USCIS", carried, today)["level"] == "warn"  # a good check two days ago stands, with a warning
    stale = carried | {"checked_on": "2026-09-20"}
    assert prefile._unchecked_edition("edition_i485", "I-485", "USCIS", stale, today)["level"] == "fail"
    assert prefile._unchecked_edition("edition_i485", "I-485", "USCIS", carried | {"checked_on": None}, today)["level"] == "fail"


def test_ready_to_mail_never_passes_an_edition_whose_check_did_not_run(tmp_path):
    live = tmp_path / "live.json"
    live.write_text(json.dumps({"at": "2026-10-03T00:00:00+00:00", "results": {"form_i485": {"ok": None, "finding": "Couldn't check"}}}), encoding="utf-8")
    # the same decision the packet's check makes: not "the current one"
    r = json.loads(live.read_text())["results"]["form_i485"]
    assert r.get("ok") is None and prefile._unchecked_edition("edition_i485", "I-485", "USCIS", r, date(2026, 10, 3))["level"] == "fail"
