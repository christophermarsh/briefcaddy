"""The Notice to Appear read in full (brief K2): the allegations, the charge, the respondent's address, the hearing line, the
file and event numbers; the arrival place, date and manner ranked above the client's own answer; the I-485's items 10 and 11 from
the settled values; the card when the client and the notice disagree; the packet gate. Every person, number and place is made up
(the Exemplo family; a made-up notice at or near Hidalgo, TX, as src/learning/synthetic.py writes it)."""

from __future__ import annotations

import pytest

from assemble import assemble, consistency_findings
from extract import extract_fields
from extract.arrival import read_arrival, split_place
from extract.notice_to_appear import allegations
from factgraph import FactGraph
import schema_path

NTA = """U.S. DEPARTMENT OF HOMELAND SECURITY
NOTICE TO APPEAR
In removal proceedings under section 240 of the Immigration and Nationality Act:
Subject ID: 123456789
FINS: 1234567890
File No: 201234567
DOB: 03/03/2008
Event No: ABC1234567890
In the Matter of:
Respondent: Ana Clara Exemplo Souza
currently residing at:
12 EXEMPLO ST in c/o MARIA EXEMPLO FRAMINGHAM MA 01702
Xx You are an alien present in the United States who has not been admitted or paroled.
The Department of Homeland Security alleges that you:
1. You are not a citizen or national of the United States;
2. You are a native of BRAZIL and a citizen of BRAZIL;
3. You arrived in the United States at or near Hidalgo, TX on or about September 14, 2022;
4. You were not then admitted or paroled after inspection by an Immigration Officer.
On the basis of the foregoing, it is charged that you are subject to removal from the United States pursuant to the following provision(s) of law:
212(a)(6)(A)(i) of the Immigration and Nationality Act, as amended
YOU ARE ORDERED to appear before an immigration judge of the United States Department of Justice at:
15 New Sudbury Street, Room 320, Boston MA
on a date to be set at a time to be set to show why you should not be removed from the United States
Form I-862 (6/22)
"""


def facts(text: str = NTA) -> dict:
    return {f.fact_key: f.normalized_value for f in extract_fields("notice_to_appear", text)}


# --- the reader -------------------------------------------------------------------------------------------------------------


def test_every_numbered_allegation_is_kept_as_text():
    got = facts()
    assert [n for n, _ in allegations(NTA)] == [1, 2, 3, 4]
    assert got["nta.allegation1"] == "You are not a citizen or national of the United States"
    assert got["nta.allegation3"] == "You arrived in the United States at or near Hidalgo, TX on or about September 14, 2022"
    assert got["nta.allegation4"].startswith("You were not then admitted or paroled")


def test_native_of_and_citizen_of_keep_the_applicants_own_keys():
    got = facts()
    assert got["nta.native_of"] == got["applicant.country_of_birth"] == "BRAZIL"
    assert got["nta.citizen_of"] == got["applicant.citizenship"] == "BRAZIL"


def test_the_arrival_is_a_place_a_date_and_the_qualifier_as_printed():
    got = facts()
    assert got["nta.arrival_place"] == "HIDALGO, TX"
    assert (got["nta.arrival_city"], got["nta.arrival_state"]) == ("HIDALGO", "TX")
    assert got["nta.arrival_date"] == "2022-09-14"
    assert got["nta.arrival_date_qualifier"] == "on or about"


def test_a_state_spelled_out_is_the_same_state_and_every_synthetic_place_reads():
    for place, city, state in (("El Paso, Texas", "EL PASO", "TX"), ("San Ysidro, CA", "SAN YSIDRO", "CA"), ("Boston, MA", "BOSTON", "MA")):
        got = facts(NTA.replace("Hidalgo, TX", place))
        assert (got["nta.arrival_city"], got["nta.arrival_state"]) == (city, state)


@pytest.mark.parametrize("date", ["09/14/2022", "Sep. 14, 2022", "SEPTEMBER 14, 2022"])
def test_the_dates_a_notice_prints_all_read(date):
    assert facts(NTA.replace("September 14, 2022", date))["nta.arrival_date"] == "2022-09-14"


def test_a_place_that_is_not_a_city_and_a_state_is_kept_as_printed_and_never_split():
    got = facts(NTA.replace("Hidalgo, TX", "the Rio Grande River"))
    assert got["nta.arrival_place"] == "THE RIO GRANDE RIVER"
    assert "nta.arrival_city" not in got and "nta.arrival_state" not in got
    assert got["nta.arrival_date"] == "2022-09-14"  # the date is still read
    for odd in ("Hidalgo, Narnia", "PDN", "Hidalgo"):  # a state that is not one, a port code, a bare city: no city and no state
        got = facts(NTA.replace("Hidalgo, TX", odd))
        assert "nta.arrival_city" not in got and "nta.arrival_state" not in got


def test_an_allegation_in_non_standard_words_stays_text_only():
    odd = NTA.replace("3. You arrived in the United States at or near Hidalgo, TX on or about September 14, 2022;",
                      "3. Respondent was encountered by agents somewhere near the border in the autumn of 2022;")
    got = facts(odd)
    assert got["nta.allegation3"].startswith("Respondent was encountered")
    assert not any(k in got for k in ("nta.arrival_place", "nta.arrival_city", "nta.arrival_date"))


def test_a_date_that_does_not_parse_is_not_guessed():
    got = facts(NTA.replace("September 14, 2022", "Septembre 14, 2022"))
    assert got["nta.arrival_place"] == "HIDALGO, TX" and "nta.arrival_date" not in got


def test_the_manner_comes_from_the_box_and_the_allegation():
    assert facts()["nta.arrival_manner"] == "present without admission or parole"
    assert facts()["applicant.nta_admission_status"] == "not_admitted_or_paroled"
    arriving = NTA.replace("Xx You are an alien present in the United States who has not been admitted or paroled.", "Xx You are an arriving alien.")
    no_arrival_allegation = arriving.replace("4. You were not then admitted or paroled after inspection by an Immigration Officer.", "4. You applied for admission.")
    assert facts(no_arrival_allegation)["nta.arrival_manner"] == "arriving alien"
    # no box marked: the allegation says it
    unmarked = NTA.replace("Xx You are an alien present in the United States who has not been admitted or paroled.", "[_] You are an arriving alien.")
    assert facts(unmarked)["nta.arrival_manner"] == "present without admission or parole"
    admitted = unmarked.replace("4. You were not then admitted or paroled after inspection by an Immigration Officer.",
                                "4. You were admitted to the United States at Boston, MA on or about 01/05/2020 as a B-2 visitor.")
    assert facts(admitted)["nta.arrival_manner"] == "admitted but removable"


def test_a_box_and_an_allegation_that_disagree_leave_the_manner_to_a_person():
    clash = NTA.replace("4. You were not then admitted or paroled after inspection by an Immigration Officer.", "4. You were admitted to the United States at Boston, MA.")
    got = facts(clash)
    assert "nta.arrival_manner" not in got and got["applicant.nta_admission_status"] == "not_admitted_or_paroled"


def test_the_charge_is_the_provision_as_printed():
    assert facts()["nta.charge"] == "212(a)(6)(A)(i) of the Immigration and Nationality Act, as amended"


def test_the_respondents_address_is_kept_as_dhs_has_it_including_in_care_of():
    assert facts()["nta.respondent_address"] == "12 EXEMPLO ST in c/o MARIA EXEMPLO FRAMINGHAM MA 01702"
    one_line = NTA.replace("currently residing at:\n12 EXEMPLO ST in c/o MARIA EXEMPLO FRAMINGHAM MA 01702",
                           "currently residing at: 12 EXEMPLO ST FRAMINGHAM MA 01702 (Number, street, city and ZIP code) (Area code and phone number)")
    assert facts(one_line)["nta.respondent_address"] == "12 EXEMPLO ST FRAMINGHAM MA 01702"


def test_the_phone_printed_beside_the_address_is_its_own_fact():
    # the real notice's line: the address, then "+1 (555)-010-0100" under the form's "(Area code and phone number)" caption
    with_phone = NTA.replace("12 EXEMPLO ST in c/o MARIA EXEMPLO FRAMINGHAM MA 01702",
                             "IN C/O MARIA EXEMPLO 12 EXEMPLO ST FRAMINGHAM, MASSACHUSETTS, 01702   +1 (555)-010-0100\n"
                             "(Number, street, city, state and ZIP code) (Area code and phone number)")
    got = facts(with_phone)
    assert got["nta.respondent_address"] == "IN C/O MARIA EXEMPLO 12 EXEMPLO ST FRAMINGHAM, MASSACHUSETTS, 01702"
    assert got["nta.respondent_phone"] == "(555) 010-0100"
    assert "nta.respondent_phone" not in facts()


def test_what_a_person_can_rely_on_in_the_address_as_the_scan_read_it():
    from extract.notice_to_appear import read_address

    clean = read_address("IN C/O MARIA EXEMPLO 12 EXEMPLO ST FRAMINGHAM, MASSACHUSETTS, 01702")
    assert (clean["number"], clean["street"], clean["state"], clean["zip"], clean["readable"]) == ("12", "EXEMPLO", "MA", "01702", True)
    assert read_address("12 EXEMPLO ST in c/o MARIA EXEMPLO FRAMINGHAM MA 01702")["state"] == "MA"  # "in c/o" is not Indiana
    # the real notice's typewritten line as the black-and-white reading had it (made-up name): nothing a screen may show as the address
    garbled = read_address("IN CU MARIA EXEMPLO 49 TESTE S7 FRAMINGHAN, MASSACHUSETIS, ULTSe ei 0974) -464-r04e")
    assert garbled["readable"] is False and garbled["zip"] is None and garbled["state"] == "MA"  # one or two characters off a state's name
    assert garbled["text"] == "IN CU MARIA EXEMPLO 49 TESTE FRAMINGHAN, MASSACHUSETIS, ULTSe ei"  # "S7" is neither a word nor a number
    # the grayscale reading: the ZIP code did not read, so still not clean, but the street and the city did
    partly = read_address("IN C/O MARIA EXEMPLO 40 TESTE ST FRAMINGHAM, MASSACHUSETTS, OL? S2 +]")
    assert (partly["number"], partly["street"], partly["state"], partly["zip"], partly["readable"]) == ("40", "TESTE", "MA", None, False)
    assert partly["text"] == "IN C/O MARIA EXEMPLO 40 TESTE ST FRAMINGHAM, MASSACHUSETTS"
    assert read_address("")["readable"] is False and read_address("")["text"] == ""


def test_the_hearing_line_to_be_set_and_a_set_hearing():
    got = facts()
    assert got["nta.hearing_place"] == "15 New Sudbury Street, Room 320, Boston MA"
    assert got["nta.hearing_status"] == "to be set" and "nta.hearing_date" not in got
    set_for = NTA.replace("on a date to be set at a time to be set to show", "on November 18, 2026 at 8:30 AM to show")
    got = facts(set_for)
    assert (got["nta.hearing_date"], got["nta.hearing_time"]) == ("2026-11-18", "8:30 AM") and "nta.hearing_status" not in got


def test_the_file_number_and_the_event_number_as_printed():
    got = facts()
    assert got["nta.file_number"] == "A201234567" and got["nta.event_number"] == "ABC1234567890"


def test_a_notice_without_the_new_wording_still_gives_what_it_gave_before():
    old = "NOTICE TO APPEAR\nDOB: 03/03/2003\nXx You are an alien present in the United States who has not been admitted or paroled.\n" \
          "The Department of Homeland Security alleges that you:\n2. You are a native of BRAZIL and a citizen of BRAZIL ;\n"
    got = facts(old)
    assert got["applicant.nta_present"] == "Yes" and got["applicant.country_of_birth"] == "BRAZIL" and got["applicant.dob"] == "2003-03-03"
    assert not any(k.startswith("nta.arrival_") and k != "nta.arrival_manner" or k in ("nta.charge", "nta.respondent_address") for k in got)


def test_the_places_helpers_split_only_a_city_and_a_real_state():
    assert split_place("Hidalgo, TX").city == "HIDALGO"
    assert split_place("a place other than a port of entry").city is None
    arrival = read_arrival("You arrived in the United States at or near Hidalgo, TX on or about September 14, 2022;")
    assert (arrival.place.state, arrival.date, arrival.qualifier) == ("TX", "2022-09-14", "on or about")
    assert read_arrival("You were not then admitted or paroled") is None


# --- the I-213 and the other DHS papers --------------------------------------------------------------------------------------

I213 = """U.S. Department of Homeland Security   Record of Deportable/Inadmissible Alien
Family Name (CAPS) First Middle Sex Hair Eyes Cmplxn
Country of Citizenship Passport Number and Country of Issue File Number Height Weight Occupation
Date, Place, Time, and Manner of Last Entry Passenger Boarded at  F.B.I. Number
Method of Location/Apprehension
EXEMPLO SOUZA
ANA CLARA
BRAZIL
09/14/2022 Unknown Time, PDN, WI-Without Inspection
File Number: 201234567
Narrative (Outline particulars under which alien was located/apprehended.)
Form I-213 (Rev. 08/01/07)
"""


def test_an_i213_is_classified_by_its_title_and_captions_even_when_it_mentions_a_notice_to_appear():
    from classify import classify_text

    assert classify_text(I213).doc_type == "i213"
    assert classify_text(I213 + "The subject was issued a Notice to Appear.\n").doc_type == "i213"
    assert classify_text(NTA).doc_type == "notice_to_appear"


def test_the_i213_entry_line_is_read_as_printed_and_a_port_code_is_never_a_city():
    got = {f.fact_key: f.normalized_value for f in extract_fields("i213", I213)}
    assert got["i213.entry_date"] == "2022-09-14"
    assert got["i213.entry_place"] == "PDN" and "i213.entry_city" not in got and "i213.entry_state" not in got
    assert got["i213.entry_manner"] == "present without admission or parole"
    assert got["i213.file_number"] == "A201234567"


def test_an_i213_narrative_in_the_standard_words_gives_a_city_and_a_state():
    text = "Record of Deportable/Inadmissible Alien\nNarrative: The subject stated that he entered the United States at or near Hidalgo, TX on or about September 14, 2022.\n"
    got = {f.fact_key: f.normalized_value for f in extract_fields("i213", text)}
    assert (got["i213.entry_city"], got["i213.entry_state"], got["i213.entry_date"]) == ("HIDALGO", "TX", "2022-09-14")
    assert got["i213.entry_date_qualifier"] == "on or about"


def test_an_i213_line_that_is_not_in_the_printed_shape_gives_nothing():
    assert extract_fields("i213", "Record of Deportable/Inadmissible Alien\nDate, Place, Time, and Manner of Last Entry\nsee narrative\n") == []


# --- the rank: the paper above the client, the I-94 above the notice's "on or about" ---------------------------------------------


def _graph(client: dict | None = None, i94: str | None = None, nta: str | None = NTA, i213: str | None = None) -> FactGraph:
    """A case: the client's own answers (Tier 3, as the questionnaire reader records them) and the papers in the folder."""
    g = FactGraph("t")
    for key, value in (client or {}).items():
        g.add_source(key, "q.pdf", "intake_questionnaire", value, value, 0.7, tier=3)
    for doc_id, doc_type, text in (("nta.pdf", "notice_to_appear", nta), ("i213.pdf", "i213", i213)):
        for f in extract_fields(doc_type, text) if text else []:
            g.add_source(f.fact_key, doc_id, doc_type, f.raw_value, f.normalized_value, f.confidence, page=0)
    if i94:
        g.add_source("applicant.i94_arrival_date", "i94.pdf", "i94", i94, i94, 0.9)
        g.add_source("applicant.i94_number", "i94.pdf", "i94", "1", "1", 0.9)
    assemble(g)
    return g


CLIENT = {"applicant.last_arrival_city": "MCALLEN", "applicant.last_arrival_state": "TX", "applicant.last_arrival_date_self_reported": "2022-09-16",
          "questionnaire.entry_how": "inspected", "questionnaire.entered_via_border": "No"}


def test_the_notice_outranks_the_client_on_place_date_and_manner_and_both_stay_as_sources():
    g = _graph(CLIENT)
    assert [g.get(k).value for k in ("applicant.last_arrival_city", "applicant.last_arrival_state")] == ["HIDALGO", "TX"]
    assert g.get("applicant.last_arrival_date").value == "2022-09-14" and g.get("applicant.last_arrival_date").tier == 1
    assert g.get("applicant.last_arrival_manner").value == "WITHOUT ADMISSION OR PAROLE"
    city = g.get("applicant.last_arrival_city")
    assert city.status == "resolved" and city.tier == 1 and city.resolution.resolved_by == "ARRIVAL-01"
    assert {s.doc_id for s in city.sources} == {"q.pdf", "nta.pdf"}  # the client's own answer is kept, never deleted
    assert any(s.page == 0 and "Hidalgo, TX" in s.raw_value for s in city.sources)  # the page and the raw line travel with the value


def test_the_i94_date_outranks_the_notices_on_or_about_date_and_the_place_still_comes_from_the_notice():
    g = _graph(CLIENT, i94="2022-09-12")
    assert g.get("applicant.last_arrival_date").value == "2022-09-12"
    assert g.get("applicant.last_arrival_city").value == "HIDALGO"
    text = " ".join(m for _k, m in consistency_findings(g))
    assert "I-94 says 09/12/2022" in text and "09/14/2022 (on or about)" in text and "The I-94's own date is used" in text


def test_with_an_i94_the_manner_stays_with_the_i94_not_the_notice():
    g = _graph({"questionnaire.entry_how": "inspected", "questionnaire.entered_via_border": "No"}, i94="2022-09-12")
    g.add_source("applicant.i94_class_of_admission", "i94.pdf", "i94", "B2", "B2", 0.9)
    assemble(g)
    assert g.get("applicant.last_arrival_manner").value == "ADMITTED"


def test_without_a_paper_the_clients_own_answers_stand_at_tier_3():
    g = _graph(CLIENT, nta=None)
    assert g.get("applicant.last_arrival_city").value == "MCALLEN" and g.get("applicant.last_arrival_city").tier == 3
    assert g.get("applicant.last_arrival_date").value == "2022-09-16" and g.get("applicant.last_arrival_date").tier == 3
    assert consistency_findings(g) == [] or all("last arrival" not in m.lower() for _k, m in consistency_findings(g))


def test_an_i213_ranks_like_the_notice_and_the_notice_comes_first():
    i213 = "Record of Deportable/Inadmissible Alien\nNarrative: The subject entered the United States at or near El Paso, TX on or about September 15, 2022.\n"
    g = _graph(CLIENT, nta=None, i213=i213)
    assert [g.get(k).value for k in ("applicant.last_arrival_city", "applicant.last_arrival_date")] == ["EL PASO", "2022-09-15"]
    both = _graph(CLIENT, i213=i213)
    assert both.get("applicant.last_arrival_city").value == "HIDALGO"
    assert any("El Paso" in m.title() and "not HIDALGO" in m for _k, m in consistency_findings(both))


def test_the_card_text_says_both_and_the_pick_and_why_in_plain_words():
    (key, text), = [f for f in consistency_findings(_graph(CLIENT)) if f[0] == "applicant.last_arrival_city"]
    assert "the Notice to Appear says at or near HIDALGO, TX; the client wrote MCALLEN, TX" in text
    assert "the Notice to Appear says 09/14/2022 (on or about); the client wrote 09/16/2022 (2 days apart)" in text
    assert "present without admission or parole; the client said admitted" in text
    assert "outranks the client's own answer" in text and "declaration" in text
    assert not any(bad in text for bad in ("—", " -- ", "applicant.", ".pdf", "2022-09"))


def test_a_client_who_agrees_with_the_notice_raises_no_card():
    agree = {"applicant.last_arrival_city": "HIDALGO", "applicant.last_arrival_state": "TX", "applicant.last_arrival_date_self_reported": "2022-09-14",
             "questionnaire.entry_how": "border", "questionnaire.entered_via_border": "Yes"}
    assert [f for f in consistency_findings(_graph(agree)) if f[0] == "applicant.last_arrival_city"] == []


def test_a_notice_in_non_standard_words_fills_nothing_and_the_card_shows_its_text():
    odd = NTA.replace("3. You arrived in the United States at or near Hidalgo, TX on or about September 14, 2022;",
                      "3. You entered the United States at a place and time unknown to the Department;")
    g = _graph(CLIENT, nta=odd)
    assert g.get("applicant.last_arrival_city").value == "MCALLEN"  # nothing taken from words that are not the standard ones
    (key, text), = [f for f in consistency_findings(g) if f[0] == "applicant.last_arrival_city"]
    assert "words it as" in text and "at a place and time unknown to the Department" in text and "nothing is filled from it" in text


ALLEGATIONS_3_4 = ("3. You arrived in the United States at or near Hidalgo, TX on or about September 14, 2022;\n"
                   "4. You were not then admitted or paroled after inspection by an Immigration Officer.")
TWO_ARRIVALS = NTA.replace(ALLEGATIONS_3_4, "3. You arrived in the United States at or near San Ysidro, CA on or about June 1, 2015;\n"
                                            "4. You were removed from the United States on or about March 3, 2016;\n"
                                            "5. You again arrived in the United States at or near Hidalgo, TX on or about September 14, 2022;\n"
                                            "6. You were not then admitted or paroled after inspection by an Immigration Officer.")
TRUE_LAST = {"applicant.last_arrival_city": "HIDALGO", "applicant.last_arrival_state": "TX", "applicant.last_arrival_date_self_reported": "2022-09-14"}


def test_a_notice_that_lists_two_arrivals_gives_none_and_keeps_every_text():
    got = facts(TWO_ARRIVALS)
    assert not any(k in got for k in ("nta.arrival_place", "nta.arrival_city", "nta.arrival_state", "nta.arrival_date"))
    assert got["nta.arrival_count"] == 2
    assert got["nta.arrival_text"].count("|") == 1 and "San Ysidro" in got["nta.arrival_text"] and "Hidalgo" in got["nta.arrival_text"]
    for n, start in ((3, "You arrived in"), (4, "You were removed"), (5, "You again arrived"), (6, "You were not then admitted")):
        assert got[f"nta.allegation{n}"].startswith(start), n  # every allegation's text is kept


def test_two_arrivals_fill_nothing_and_the_card_says_a_person_chooses_with_both_texts():
    g = _graph(nta=TWO_ARRIVALS)  # no client answer: nothing on the form from the notice
    assert g.get("applicant.last_arrival_city") is None and g.get("applicant.last_arrival_date") is None
    (key, text), = [f for f in consistency_findings(g) if f[0] == "applicant.last_arrival_city"]
    assert "The notice lists more than one arrival; a person chooses:" in text and "San Ysidro, CA on or about June 1, 2015" in text and "at or near Hidalgo, TX on or about September 14, 2022" in text
    assert "Nothing is filled from it" in text and "apart" not in text and "outranks" not in text


def test_a_client_who_wrote_the_true_last_arrival_is_not_told_the_notice_disagrees():
    g = _graph(TRUE_LAST, nta=TWO_ARRIVALS)
    assert [g.get(k).value for k in ("applicant.last_arrival_city", "applicant.last_arrival_date")] == ["HIDALGO", "2022-09-14"]  # the client's own answer, untouched
    (key, text), = [f for f in consistency_findings(g) if f[0] == "applicant.last_arrival_city"]
    assert " apart" not in text and "the client wrote HIDALGO" not in text and "says" not in text and "lists more than one arrival" in text


def test_an_arrival_in_other_words_beside_a_standard_one_is_two_arrivals():
    odd = NTA.replace("4. You were not then admitted", "4. You entered the United States again in the autumn of 2023;\n5. You were not then admitted")
    got = facts(odd)
    assert got["nta.arrival_count"] == 2 and "nta.arrival_city" not in got


ADMITTED_FIRST = ("3. You were admitted to the United States at Boston, MA on or about January 5, 2015 as a B-2 visitor;\n"
                  "4. You remained in the United States beyond July 5, 2015 without authorization;\n"
                  "5. You arrived in the United States at or near Hidalgo, TX on or about September 14, 2022;\n"
                  "6. You were not then admitted or paroled after inspection by an Immigration Officer.")


def test_allegations_that_state_an_admission_and_a_non_admission_give_no_manner():
    got = facts(NTA.replace(ALLEGATIONS_3_4, ADMITTED_FIRST).replace("Xx You are an alien", "[_] You are an alien"))  # no box marked
    assert "nta.arrival_manner" not in got
    assert (got["nta.arrival_city"], got["nta.arrival_date"]) == ("HIDALGO", "2022-09-14")  # the one arrival still reads
    marked = facts(NTA.replace(ALLEGATIONS_3_4, ADMITTED_FIRST))  # the box marked: still none, the allegations disagree with each other and the box
    assert "nta.arrival_manner" not in marked


def test_item_11_stays_unset_for_a_person_when_the_notice_states_two_manners():
    g = _graph(nta=NTA.replace(ALLEGATIONS_3_4, ADMITTED_FIRST).replace("Xx You are an alien", "[_] You are an alien"))
    assert g.get("applicant.last_arrival_manner") is None


def test_one_allegation_that_states_the_arrival_and_the_non_admission_gives_both():
    combined = NTA.replace(ALLEGATIONS_3_4, "3. You arrived in the United States at or near Hidalgo, TX on or about September 14, 2022 and were not then admitted or paroled.")
    got = facts(combined.replace("Xx You are an alien", "[_] You are an alien"))
    assert got["nta.arrival_city"] == "HIDALGO" and got["nta.arrival_manner"] == "present without admission or parole"


def test_a_place_that_names_a_crossing_or_a_landmark_is_not_split_into_a_city():
    for odd in ("the Rio Grande River, TX", "Anzalduas International Bridge, TX", "Hidalgo Port of Entry, TX", "near the border, TX"):
        got = facts(NTA.replace("Hidalgo, TX", odd))
        assert "nta.arrival_city" not in got and got["nta.arrival_place"] == odd.upper(), odd
    assert facts(NTA.replace("Hidalgo, TX", "Rio Grande City, TX"))["nta.arrival_city"] == "RIO GRANDE CITY"
    assert facts(NTA.replace("Hidalgo, TX", "Port Isabel, TX"))["nta.arrival_city"] == "PORT ISABEL"


def test_a_date_that_cannot_be_read_keeps_the_place():
    for date in ("14 de setembro de 2022", "the autumn of 2022"):
        got = facts(NTA.replace("September 14, 2022", date))
        assert (got["nta.arrival_city"], got["nta.arrival_state"]) == ("HIDALGO", "TX") and "nta.arrival_date" not in got, date


def test_a_person_who_saved_the_card_has_decided_and_the_settling_leaves_those_boxes_alone():
    g = FactGraph("t")
    for key, value in CLIENT.items():
        g.add_source(key, "q.pdf", "intake_questionnaire", value, value, 0.7, tier=3)
    g.set_by_review("applicant.last_arrival_city", "MCALLEN", "Jane", "the client's own city")
    for f in extract_fields("notice_to_appear", NTA):
        g.add_source(f.fact_key, "nta.pdf", "notice_to_appear", f.raw_value, f.normalized_value, f.confidence)
    assemble(g)
    assert g.get("applicant.last_arrival_city").value == "MCALLEN"
    assert [f for f in consistency_findings(g) if f[0] == "applicant.last_arrival_city"] == []


def test_every_place_the_synthetic_generator_writes_is_read():
    from learning import synthetic

    # the generator damages its text like a bad scan: a place it garbled is never turned into a city and a state
    seen = set()
    for text, kind in synthetic.generate(60, seed=7, types=["notice_to_appear"]):
        got = facts(text)
        if "nta.arrival_city" in got:
            seen.add((got["nta.arrival_city"], got["nta.arrival_state"]))
            assert got["nta.arrival_place"] == f"{got['nta.arrival_city']}, {got['nta.arrival_state']}"
    assert seen and seen <= {("HIDALGO", "TX"), ("EL PASO", "TX"), ("SAN YSIDRO", "CA"), ("BOSTON", "MA")}


# --- the review card, the I-485's boxes, the gate --------------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

from synthetic_documents import process_retained_documents, retain_documents  # noqa: E402
from fill import load_field_map  # noqa: E402
from questionnaire import QuestionnaireReading  # noqa: E402
from review.state import Catalog, build_items, load_decision_log, load_decisions, record_decision, refill, reviewed_graph, save_bundle, undo_decision  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
TEMPLATE = schema_path.path("template", "i485")
FIELD_MAP = load_field_map(schema_path.path("field_map", "i485"))
CATALOG = Catalog(FIELD_MAP, TEMPLATE)
CITY_BOX, STATE_BOX, DATE_BOX = (f"form1[0].#subform[1].Pt1Line10_{n}[0]" for n in ("CityTown", "State", "DateofArrival"))
MANNER_BOX = "form1[0].#subform[1].Pt2Line11_CB[2]"


def _notice_pages(text):
    # A genuinely numbered two-page notice. Its first page has no allegation,
    # so the original source alias and allegation page 2 remain meaningful.
    return ["U.S. DEPARTMENT OF HOMELAND SECURITY\nNOTICE TO APPEAR\nIn removal proceedings under section 240\nPage 1 of 2",
            text + "\nPage 2 of 2"]


def _reader(doc_id):
    from extract.base import ExtractedField

    r = QuestionnaireReading()
    r.text_fields = [ExtractedField("applicant.last_arrival_date_self_reported", "x", "2022-09-16", 0.7),
                     ExtractedField("applicant.last_arrival_city", "x", "MCALLEN", 0.7), ExtractedField("applicant.last_arrival_state", "x", "TX", 0.7),
                     ExtractedField("questionnaire.entry_how", "x", "inspected", 0.7)]
    r.evidence = {
        "last_entry_date": {"kind": "text", "input_kind": "date", "describe": "the date of the last entry", "facts": {"value": "applicant.last_arrival_date_self_reported"},
                            "page": 0, "box": [10, 10, 300, 60], "status": "ok", "reads": [{"value": "2022-09-16"}], "values": {"value": "2022-09-16"}, "reason": ""},
        "last_entry_place": {"kind": "text", "input_kind": "us_place", "describe": "the place of the last entry",
                             "facts": {"city": "applicant.last_arrival_city", "state": "applicant.last_arrival_state"},
                             "page": 0, "box": [10, 80, 300, 130], "status": "ok", "reads": [{"city": "MCALLEN", "state": "TX"}], "values": {}, "reason": ""},
    }
    return r


@pytest.fixture
def arrival_case(tmp_path):
    source = tmp_path / "clients" / "demo" / "source"
    source.mkdir(parents=True)
    result = process_retained_documents("demo", source, [("nta.pdf", NTA), ("q.pdf", "Questionário para Ajuste de Status\nI485 - SIJS\n")],
                                       questionnaire_reader=_reader, pages={"nta.pdf": _notice_pages(NTA)})
    out = tmp_path / "data" / "demo"
    save_bundle(result, out, source)
    refill(out, FIELD_MAP, TEMPLATE)
    return out


def _cards(case):
    return {i["id"]: i for i in build_items(case, FIELD_MAP, TEMPLATE, CATALOG)["open"]}


def test_items_10_and_11_of_the_filled_i485_come_from_the_notice(arrival_case):
    from pypdf import PdfReader

    fields = PdfReader(str(arrival_case / "i485_filled.pdf")).get_fields()
    assert (fields[CITY_BOX]["/V"], fields[STATE_BOX]["/V"], fields[DATE_BOX]["/V"]) == ("HIDALGO", "TX", "09/14/2022")
    assert fields[MANNER_BOX]["/V"] == "/11C"  # came in without admission or parole
    assert fields["form1[0].#subform[1].Pt2Line11_CB[0]"].get("/V") in (None, "/Off")  # not "admitted", as the client said


def test_the_card_shows_both_papers_at_their_pages_the_pick_and_why(arrival_case):
    card = _cards(arrival_case)["crosscheck:applicant.last_arrival_city"]
    assert [f["key"] for f in card["facts"]] == ["applicant.last_arrival_city", "applicant.last_arrival_state", "applicant.last_arrival_date", "applicant.last_arrival_manner"]
    assert card["actions"] == ["acknowledge", "set"] and card["title"].startswith("Last arrival in the U.S.")
    assert set(card["docs"]) == {"nta.pdf", "q.pdf"}
    nta = next(e for e in card["evidence"] if e["doc"] == "nta.pdf")
    assert nta["page"] == 1 and nta["describe"] == "the Notice to Appear"  # the allegation is on the second page of the file
    assert any(e["doc"] == "q.pdf" and e["box"] for e in card["evidence"])  # the client's own answer, cropped from the questionnaire scan
    sources = {s["doc"]: s["value"] for f in card["facts"] if f["key"] == "applicant.last_arrival_city" for s in f["sources"]}
    assert sources == {"q.pdf": "MCALLEN", "nta.pdf": "HIDALGO"} and card["facts"][0]["value"] == "HIDALGO"
    assert "the Notice to Appear says at or near HIDALGO, TX; the client wrote MCALLEN, TX" in " ".join(card["messages"])


def test_a_paper_added_after_the_first_run_keeps_its_page_through_the_inbox_path(arrival_case, tmp_path):
    """The portal's uploads, the front desk's scans and the scan inbox's reprocessing pass the page texts: a card opens the paper at its page."""
    import json

    import inbox
    from batch import split_pages
    from factgraph import FactGraph

    folder = Path(json.loads((arrival_case / "meta.json").read_text(encoding="utf-8"))["source_folder"])
    pages = _notice_pages(NTA)
    retain_documents(folder, [("second_nta.pdf", "\n".join(pages))], {"second_nta.pdf": pages})
    docs, paged = split_pages("second_nta.pdf", pages, [(0, 1, "notice_to_appear")])
    inbox.reprocess_documents(arrival_case, folder, docs, {"second_nta.pdf": {"pages": 2}}, tmp_path / "index.db", source="folder", who="Jane", pages=paged)
    raw = FactGraph.load(arrival_case / "fact_graph_raw.json")
    mine = [s for s in raw.get("nta.arrival_city").sources if s.doc_id == "second_nta.pdf"]
    assert mine and mine[0].page == 1
    # Without page texts the ingestion path now reads the retained original.
    # It must recover the same actual page instead of trusting joined text.
    retain_documents(folder, [("third_nta.pdf", "\n".join(pages))], {"third_nta.pdf": pages})
    docs, _ = split_pages("third_nta.pdf", pages, [(0, 1, "notice_to_appear")])
    inbox.reprocess_documents(arrival_case, folder, docs, {"third_nta.pdf": {"pages": 2}}, tmp_path / "index.db", source="folder", who="Jane")
    assert [s.page for s in FactGraph.load(arrival_case / "fact_graph_raw.json").get("nta.arrival_city").sources if s.doc_id == "third_nta.pdf"] == [1]


def test_the_tps_and_visa_sheets_read_the_settled_date_and_do_not_call_it_the_i94s():
    import datetime

    import tps
    import visa

    g = _graph(CLIENT)  # a notice, no I-94
    tps.derive(g, datetime.date(2026, 10, 1))
    assert g.get("tps.last_entry_date").value == "2022-09-14"
    assert g.get("tps.last_entry_date").sources[0].raw_value == "the last entry in the case"
    assert "I-94" not in g.get("tps.entry_date").sources[0].raw_value
    assert any(key == "applicant.last_arrival_date" for _s, label, key in visa.SHEET if label == "Last arrival in the U.S.")


def test_split_pages_pads_a_part_so_a_page_number_is_the_files_own():
    from batch import split_pages

    docs, paged = split_pages("scan.pdf", ["i94", "nta p1", "nta p2"], [(0, 0, "i94"), (1, 2, "notice_to_appear")])
    assert [d for d, _t in docs] == ["scan.pdf#p1", "scan.pdf#p2-3"] and paged["scan.pdf#p2-3"] == ["", "nta p1", "nta p2"]
    one, paged_one = split_pages("a.pdf", ["x", "y"], [(0, 1, "i94")])
    assert one == [("a.pdf", "x\ny")] and paged_one == {"a.pdf": ["x", "y"]}


def _companion_date(g, tmp_path, form: str, box: str):
    from pypdf import PdfReader

    from fill.companion import fill_companions, load_profile

    profile = load_profile()
    profile["forms"] = {form: profile["forms"][form]}
    fill_companions(g, tmp_path, profile)
    fields = PdfReader(str(tmp_path / profile["forms"][form]["output"])).get_fields()
    return next((v.get("/V") for k, v in fields.items() if k.rsplit(".", 1)[-1] == box), None)


def test_the_i765_prints_the_same_date_of_last_entry_as_i485_item_10_from_a_notice(tmp_path, arrival_case):
    from pypdf import PdfReader

    i485 = PdfReader(str(arrival_case / "i485_filled.pdf")).get_fields()[DATE_BOX]["/V"]
    g = reviewed_graph(arrival_case)
    assert g.get("applicant.i94_arrival_date") is None and i485 == "09/14/2022"  # a notice and no I-94
    assert _companion_date(g, tmp_path, "i765", "Line21_DateOfLastEntry[0]") == i485


def test_every_companion_form_reads_the_settled_date_not_the_i94s():
    import json

    raw = (schema_path.path("packet", "companion_forms")).read_text(encoding="utf-8")
    assert "applicant.i94_arrival_date" not in raw and raw.count('"applicant.last_arrival_date"') == 14
    assert json.loads(raw)


def test_a_companion_filled_from_a_graph_never_assembled_still_gets_the_date(tmp_path):
    g = FactGraph("t")
    g.add_source("applicant.i94_arrival_date", "i94.pdf", "i94", "x", "2022-09-12", 0.9)
    assert _companion_date(g, tmp_path, "i765", "Line21_DateOfLastEntry[0]") == "09/12/2022"
    only_client = FactGraph("t")
    only_client.add_source("applicant.last_arrival_date_self_reported", "q.pdf", "intake_questionnaire", "x", "2022-09-16", 0.7, tier=3)
    assert _companion_date(only_client, tmp_path, "i765", "Line21_DateOfLastEntry[0]") == "09/16/2022"


def test_arrival_01_is_a_registered_rule_and_every_rule_id_in_the_code_is_registered():
    import re

    from review.state import RULE_NAMES, rule_info
    from rules import ALL_RULES

    ids = {r.rule_id for r in ALL_RULES}
    assert "ARRIVAL-01" in ids and ids <= set(RULE_NAMES)
    seen = set()
    for path in (_REPO / "src").rglob("*.py"):
        seen |= set(re.findall(r"""["']([A-Z]{3,12}-0\d)["']""", path.read_text(encoding="utf-8")))
    assert "ARRIVAL-01" in seen and seen <= ids, seen - ids  # a rule named in the code is a rule the attorney can read and approve
    page = (_REPO / "docs" / "rules" / "ARRIVAL-01.md").read_text(encoding="utf-8")
    assert "GRAPH_MODEL.md" in page and "outranks" in page and "I-94" in page
    info = rule_info("ARRIVAL-01")
    assert info["approval"]["state"] == "not_approved" and "government paper outranks the client's own statement" in info["plain_text"]


def test_the_card_shows_the_rule_and_its_approval_and_an_attorney_can_approve_it(arrival_case, tmp_path, monkeypatch):
    from review.server import ReviewApp

    text = " ".join(_cards(arrival_case)["crosscheck:applicant.last_arrival_city"]["messages"])
    assert "The rule behind this (the last-arrival rule" in text and "Not yet approved" in text
    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))
    (tmp_path / "app" / "clients").mkdir(parents=True)
    app = ReviewApp(tmp_path / "app" / "clients", schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None)
    with pytest.raises(PermissionError):
        app.rules_approve({"rule": "ARRIVAL-01", "reviewer": "Paulo Paralegal"}, "paralegal")
    rules = {r["id"]: r for r in app.rules_approve({"rule": "ARRIVAL-01", "reviewer": "Ana Attorney"}, "attorney")["rules"]}
    assert rules["ARRIVAL-01"]["approval"]["state"] == "approved"
    text = " ".join(_cards(arrival_case)["crosscheck:applicant.last_arrival_city"]["messages"])
    assert "Not yet approved" not in text and "Ana Attorney" in text


def test_save_records_who_and_undo_reopens_it(arrival_case):
    item = _cards(arrival_case)["crosscheck:applicant.last_arrival_city"]
    values = {f["key"]: f["value"] for f in item["facts"]}
    record_decision(arrival_case, item, {"reviewer": "Jane Paralegal", "action": "set", "values": values, "note": "checked both papers"})
    assert "crosscheck:applicant.last_arrival_city" not in _cards(arrival_case)
    saved = load_decisions(arrival_case)["crosscheck:applicant.last_arrival_city"]
    assert saved["reviewer"] == "Jane Paralegal" and saved["action"] == "set"
    graph = reviewed_graph(arrival_case)
    assert graph.get("applicant.last_arrival_city").value == "HIDALGO" and graph.get("applicant.last_arrival_city").review.resolved_by == "Jane Paralegal"
    undo_decision(arrival_case, "crosscheck:applicant.last_arrival_city", "Jane Paralegal")
    assert "crosscheck:applicant.last_arrival_city" in _cards(arrival_case)
    log = load_decision_log(arrival_case)["crosscheck:applicant.last_arrival_city"]
    assert log["undone"]["by"] == "Jane Paralegal"


def test_a_reviewer_who_prefers_the_clients_city_puts_it_on_the_form(arrival_case):
    from pypdf import PdfReader

    item = _cards(arrival_case)["crosscheck:applicant.last_arrival_city"]
    values = {f["key"]: f["value"] for f in item["facts"]} | {"applicant.last_arrival_city": "MCALLEN"}
    record_decision(arrival_case, item, {"reviewer": "Ana Attorney", "action": "set", "values": values, "note": "the client's own place"})
    fields = PdfReader(refill(arrival_case, FIELD_MAP, TEMPLATE)["pdf"]).get_fields()
    assert fields[CITY_BOX]["/V"] == "MCALLEN" and fields[DATE_BOX]["/V"] == "09/14/2022"


def test_the_packet_waits_for_the_card_until_it_is_saved(arrival_case):
    import packet
    from review.overview import review_row

    row = review_row(arrival_case, FIELD_MAP, TEMPLATE, CATALOG)
    assert row["fix"] >= 1
    assert any("review card" in p and "still open" in p for p in packet.plan(arrival_case, row)["problems"])
    item = _cards(arrival_case)["crosscheck:applicant.last_arrival_city"]
    record_decision(arrival_case, item, {"reviewer": "Jane", "action": "acknowledge", "note": "the client's date goes in the declaration"})
    assert "crosscheck:applicant.last_arrival_city" not in _cards(arrival_case)


SET_HEARING = NTA.replace("on a date to be set at a time to be set to show", "on November 18, 2026 at 8:30 AM to show")


def _journey(tmp_path, monkeypatch, text, marks=None):
    import json

    import journey

    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": "October 2026", "area": "ALL CHARGEABILITY", "cutoff": None, "pd": "2023-01-10",
                                                                "current": None, "problems": []})
    d = tmp_path / "bundle"
    d.mkdir(exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"classifications": {"nta.pdf": "notice_to_appear"}}), encoding="utf-8")
    (d / "fact_graph.json").write_text("{}", encoding="utf-8")
    if marks:
        (d / "status.json").write_text(json.dumps({"journey": marks}), encoding="utf-8")
    from datetime import date

    return journey.journey(d, date(2026, 10, 1), graph=_graph(nta=text))


def test_a_hearing_the_notice_sets_is_on_the_timeline_unconfirmed_with_a_step_and_a_client_deadline(tmp_path, monkeypatch):
    j = _journey(tmp_path, monkeypatch, SET_HEARING)
    event = next(e for e in j["timeline"] if e["date"] == "2026-11-18")
    assert "first hearing set by the Notice to Appear at 8:30 AM" in event["what"] and "not confirmed yet" in event["what"] and event["doc"] == "nta.pdf"
    step = next(s for s in j["steps"] if s["id"] == "hearing.nta")
    assert step["owner"] == "paralegal" and "11/18/2026" in step["text"] and "check it against the court" in step["text"]
    assert any(d["date"] == "2026-11-18" and d["owner"] == "client" and "not confirmed yet" in d["what"] for d in j["deadlines"])


def test_the_unconfirmed_hearing_is_marked_in_the_calendar_feed_and_clios_entry_and_never_reminds_the_client(tmp_path, monkeypatch):
    """An unconfirmed hearing is still a date the office must watch: it reaches the calendar feed and the Clio entry, marked "not confirmed" in
    the words each keeps when it cuts the sentence short; the client's reminders go only for a hearing a person entered and confirmed."""
    from datetime import date

    import calendar_feed
    import client_reminders
    import inbox
    import json
    import journey
    from connectors import clio
    from test_client_case import _outbox, _portal_client
    from test_restricted import doc, make_case

    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": "October 2026", "area": "ALL CHARGEABILITY", "cutoff": None, "pd": "2023-01-10",
                                                                "current": None, "problems": []})
    today = date(2026, 10, 3)
    data = tmp_path / "data"
    case_id = tmp_path.name[:60]
    cases = data / "clients"
    d = make_case(cases, case_id, "Ana Clara Exemplo Souza", [doc("a1", "passport")])
    folder = tmp_path / "source"
    notice = NTA.replace("on a date to be set at a time to be set to show", "on October 9, 2026 at 8:30 AM to show")
    docs = [("nta.pdf", notice)]
    retain_documents(folder, docs)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    (d / "meta.json").write_text(json.dumps(meta | {"source_folder": str(folder)}), encoding="utf-8")
    inbox.reprocess_documents(d, folder, docs, {"nta.pdf": {"pages": 1}}, data / "index.db",
                              source="folder", who="Pat Paralegal", pages={"nta.pdf": [notice]})

    j = journey.journey(d, today)
    deadline = next(x for x in j["deadlines"] if x["id"] == "hearing.nta")
    assert deadline["what"].startswith("Immigration court, not confirmed yet: first hearing set by the Notice to Appear at 8:30 AM")
    assert deadline["appt"]["kind"] == "hearing_unconfirmed" and deadline["appt"]["time"] == "8:30 AM"
    row = {"id": case_id, "summary": {"name": "ANA CLARA EXEMPLO SOUZA"}, "journey": j}
    events = calendar_feed.build([row], {"email": "jane@firm.example", "role": "attorney"}, "firm", lambda *a: True, "http://x", today)
    hearing = next(e for e in events if e["date"] == date(2026, 10, 9))
    assert "not confirmed" in hearing["title"] and "not confirmed yet" in hearing["description"] and hearing["time"] is not None
    assert "not confirmed yet" in clio._summary(deadline["what"])  # the entry's headline, cut at 120 characters

    _portal_client(data, case_id, "Ana Clara Exemplo Souza", email="ana@example.com", phone="+1 555 010 0100", language="pt",
                   consent={"email": True, "sms": True, "whatsapp": False})
    assert client_reminders.nightly(cases, data, today) == "Client reminders: nothing to send." and _outbox(data) == []  # the hearing is 6 days away: still nothing
    assert client_reminders.nightly(cases, data, date(2026, 10, 8)) == "Client reminders: nothing to send." and _outbox(data) == []  # and the day before
    assert journey.client_view(j, "pt")["appointments"] == []  # the client's own page does not show it either


def test_a_notice_with_the_date_to_be_set_adds_no_hearing(tmp_path, monkeypatch):
    j = _journey(tmp_path, monkeypatch, NTA)
    assert not any(e["what"].startswith("Immigration court") for e in j["timeline"]) and not any(s["id"] == "hearing.nta" for s in j["steps"])


def test_once_the_hearing_is_entered_the_notices_line_steps_aside(tmp_path, monkeypatch):
    entered = {"hearings": [{"id": "hearing.2026-11-18.0", "date": "2026-11-18", "time": "8:30 AM", "kind": "Master calendar", "court": "Boston", "by": "Jane",
                             "at": "2026-10-01T10:00:00-04:00"}]}
    j = _journey(tmp_path, monkeypatch, SET_HEARING, entered)
    assert not any(s["id"] == "hearing.nta" for s in j["steps"]) and not any("first hearing set by the Notice" in e["what"] for e in j["timeline"])


def test_the_eoir28_notes_the_address_dhs_has_only_when_it_is_not_the_clients_home(tmp_path):
    import court

    g = _graph({"applicant.physical_street": "99 OTHER RD", "applicant.physical_zip": "02143"})
    note = next(n for n in court.notes(g, __import__("datetime").date(2026, 10, 1)) if n["title"] == "The address DHS has")
    assert "12 EXEMPLO ST in c/o MARIA EXEMPLO FRAMINGHAM MA 01702" in note["text"] and "if the client has moved" in note["text"]
    same = _graph({"applicant.physical_street": "12 EXEMPLO ST", "applicant.physical_zip": "01702"})
    assert not any(n["title"] == "The address DHS has" for n in court.notes(same, __import__("datetime").date(2026, 10, 1)))


def test_an_address_the_scan_read_badly_is_never_shown_as_the_clients_address(tmp_path):
    import court

    # the real notice's typewritten line as the scan read it on 10/04/2026 (the name made up): the screen said "lists the client's
    # address as IN CU ... S7 ..., MASSACHUSETIS, ULTSe ei 0974) -464-r04e" and that it differed from the home address
    garbled = NTA.replace("12 EXEMPLO ST in c/o MARIA EXEMPLO FRAMINGHAM MA 01702",
                          "IN CU MARIA EXEMPLO 49 TESTE S7 FRAMINGHAN, MASSACHUSETIS, ULTSe ei 0974) -464-r04e")
    g = _graph({"applicant.physical_street": "99 OTHER RD", "applicant.physical_zip": "01702"}, nta=garbled)
    note = next(n for n in court.notes(g, __import__("datetime").date(2026, 10, 1)) if n["title"] == "The address DHS has")
    assert "does not read it clearly" in note["text"] and "compare" in note["text"] and "differs" not in note["text"]
    assert "0974)" not in note["text"] and "-464-r04e" not in note["text"] and "What did read: IN CU MARIA EXEMPLO 49 TESTE FRAMINGHAN" in note["text"]
    # the same scan read partly (the ZIP code lost): still a note to compare, with what did read
    partly = NTA.replace("12 EXEMPLO ST in c/o MARIA EXEMPLO FRAMINGHAM MA 01702", "IN C/O MARIA EXEMPLO 40 TESTE ST FRAMINGHAM, MASSACHUSETTS, OL? S2 +]")
    note = next(n for n in court.notes(_graph({"applicant.physical_street": "99 OTHER RD", "applicant.physical_zip": "01702"}, nta=partly),
                                       __import__("datetime").date(2026, 10, 1)) if n["title"] == "The address DHS has")
    assert "What did read: IN C/O MARIA EXEMPLO 40 TESTE ST FRAMINGHAM, MASSACHUSETTS." in note["text"] and "OL?" not in note["text"]
    # read cleanly and the same as the home address: no note; read cleanly and different: "differs", the address shown
    clean = NTA.replace("12 EXEMPLO ST in c/o MARIA EXEMPLO FRAMINGHAM MA 01702", "IN C/O MARIA EXEMPLO 40 TESTE ST FRAMINGHAM, MASSACHUSETTS, 01702")
    assert not any(n["title"] == "The address DHS has" for n in court.notes(_graph({"applicant.physical_street": "40 TESTE ST", "applicant.physical_zip": "01702"}, nta=clean),
                                                                            __import__("datetime").date(2026, 10, 1)))
    note = next(n for n in court.notes(_graph({"applicant.physical_street": "99 OTHER RD", "applicant.physical_zip": "01702"}, nta=clean),
                                       __import__("datetime").date(2026, 10, 1)) if n["title"] == "The address DHS has")
    assert "lists the client's address as IN C/O MARIA EXEMPLO 40 TESTE ST FRAMINGHAM, MASSACHUSETTS, 01702. It differs" in note["text"]


def test_every_save_is_a_ledger_row_under_the_reviewers_name(arrival_case):
    import events

    item = _cards(arrival_case)["crosscheck:applicant.last_arrival_city"]
    record_decision(arrival_case, item, {"reviewer": "Jane Paralegal", "action": "acknowledge", "note": "ok"})
    rows = [r for r in events.rows(events.base_path(arrival_case.parent.parent), case="demo") if r["kind"] == "decisions"]
    assert rows and rows[-1]["who"] == "Jane Paralegal" and rows[-1]["action"] == "acknowledged"
    assert "MCALLEN" not in rows[-1]["what"] and "HIDALGO" not in rows[-1]["what"]  # the ledger names the item, never the values


# --- K6: a notice whose state OCR misread, and the allegation across lines ---------------------------------------------------------

import json  # noqa: E402

import arrival  # noqa: E402
from extract.arrival import STATE_CODES, city_and_token  # noqa: E402

# The shape the real case showed (in shape only): the allegation over two lines, the state read by OCR as "Ad". Made-up client, made-up date.
SAN_LUIS = NTA.replace("3. You arrived in the United States at or near Hidalgo, TX on or about September 14, 2022;",
                       "3. You arrived in the United States at or near SAN LUIS, Ad, on or about December 2,\n2021;")
ELSEWHERE = {"applicant.last_arrival_city": "NEWARK", "applicant.last_arrival_state": "NJ", "applicant.last_arrival_date_self_reported": "2021-12-02"}


def test_the_allegation_reads_with_a_line_break_at_every_comma_and_after_near():
    one_line = "You arrived in the United States at or near SAN LUIS, Ad, on or about December 2, 2021"
    broken = one_line.replace("near ", "near\n").replace(", ", ",\n")
    assert broken.count("\n") == 4
    for text in (one_line, broken, broken.replace("\n", " \n  ")):
        got = read_arrival(text)
        assert got is not None and got.place.text == "SAN LUIS, Ad" and got.date == "2021-12-02" and got.qualifier == "on or about", text
        assert got.place.city is None and got.place.state is None  # "Ad" is no state: nothing is split
    # and inside a whole notice, where the allegation's own lines are joined before it is read
    wrapped = NTA.replace("3. You arrived in the United States at or near Hidalgo, TX on or about September 14, 2022;",
                          "3. You arrived in the United States at or near\nSAN LUIS,\nAd,\non or about December 2,\n2021;")
    for text in (SAN_LUIS, wrapped):
        got = facts(text)
        assert got["nta.arrival_text"] == "SAN LUIS, Ad"  # verbatim, its lower case kept
        assert got["nta.arrival_date"] == "2021-12-02" and got["nta.arrival_date_qualifier"] == "on or about"
        assert "nta.arrival_city" not in got and "nta.arrival_state" not in got


def test_the_token_is_never_read_as_a_state_by_itself():
    assert city_and_token("SAN LUIS, Ad") == ("SAN LUIS", "Ad")
    assert city_and_token("Hidalgo, TX") is None and city_and_token("the Rio Grande River, Ad") is None and city_and_token("PDN") is None
    g = _graph(ELSEWHERE, nta=SAN_LUIS.replace("SAN LUIS, Ad", "EXEMPLOVILLE, Ad"))  # one letter from AZ, but no port of that name: no reading
    assert arrival.reading(g) is None and g.get("applicant.last_arrival_city").value == "NEWARK"
    (key, text), = [f for f in consistency_findings(g) if f[0] == "applicant.last_arrival_city"]
    assert "words it as \"EXEMPLOVILLE, Ad\"" in text and "port of entry" not in text


def test_a_port_in_one_state_is_offered_as_the_reading_and_item_10_waits_for_a_person():
    g = _graph(ELSEWHERE, nta=SAN_LUIS)
    read = arrival.reading(g)
    assert (read["city"], read["state"], read["words"], read["state_words"], read["printed"]) == ("SAN LUIS", "AZ", "San Luis", "Arizona", "SAN LUIS, Ad")
    for key in ("applicant.last_arrival_city", "applicant.last_arrival_state"):
        fact = g.get(key)
        assert fact.status == "resolved" and fact.value is None, key  # empty until a person confirms: never the reading, never the other state
        assert any(s.doc_id == "q.pdf" for s in fact.sources)  # the client's own answer is kept as a source
    assert g.get("applicant.last_arrival_date").value == "2021-12-02"  # the date is the notice's, as before
    (key, text), = [f for f in consistency_findings(g) if f[0] == "applicant.last_arrival_city"]
    assert 'The notice prints "SAN LUIS, Ad". San Luis is a port of entry in Arizona. Confirm or correct.' in text
    assert "The client wrote NEWARK, NJ." in text and "stay empty until a person saves this card" in text
    assert not any(bad in text for bad in ("—", " -- ", "applicant.", ".pdf", "2021-12", "ports_of_entry"))
    alone = _graph({}, nta=SAN_LUIS)  # no answer from the client: the card still asks
    assert alone.get("applicant.last_arrival_city") is None and arrival.reading(alone)["state"] == "AZ"
    assert [k for k, _m in consistency_findings(alone)] == ["applicant.last_arrival_city"]


def test_a_city_that_is_a_port_in_two_states_or_an_empty_table_gives_no_reading(monkeypatch):
    g = _graph(ELSEWHERE, nta=SAN_LUIS.replace("SAN LUIS, Ad", "PORTLAND, Oe"))  # Portland, Maine and Portland, Oregon
    assert sorted(arrival.port_states("Portland")) == ["ME", "OR"] and arrival.reading(g) is None
    assert g.get("applicant.last_arrival_city").value == "NEWARK"  # as before K6: the printed text on the card, nothing read
    monkeypatch.setattr(arrival, "_ports", lambda: {})  # the firm's attorney has not filled the table: the rule still holds, with no reading
    assert arrival.reading(_graph(ELSEWHERE, nta=SAN_LUIS)) is None


def test_the_port_table_is_cbps_own_list_with_its_source_and_re_dumps_byte_for_byte():
    raw = schema_path.path("geo", "ports_of_entry").read_text(encoding="utf-8")
    table = json.loads(raw)
    assert json.dumps(table, indent=2, ensure_ascii=False) + "\n" == raw
    read = table["read"]
    assert "https://www.cbp.gov/about/contact/ports" in table["_source"] and f"read {read[5:7]}/{read[8:]}/{read[:4]}" in table["_source"]
    assert len(table["ports"]) > 300 and all(p["url"].startswith("https://www.cbp.gov/about/contact/ports/") and p["state"] in STATE_CODES
                                             for p in table["ports"])
    san_luis = [p for p in table["ports"] if "SAN LUIS" in p["places"]]
    assert [(p["name"], p["code"], p["state"]) for p in san_luis] == [("San Luis, Arizona", "2608", "AZ")]
    register = {i["id"]: i for i in json.loads((schema_path.path("register", "maintenance")).read_text(encoding="utf-8"))["items"]}
    item = register["ports_of_entry"]
    assert item["party"] == "provider" and item["cadence"] == "yearly" and "schemas/geo/ports_of_entry.json" in item["where"]
    assert "https://www.cbp.gov/about/contact/ports" in item["source"]


def test_a_port_listed_under_another_states_name_gives_no_place():
    """Verification of K6: CBP lists "Ashland, Wisconsin Port Of Entry" on its Minnesota page; ASHLAND must not be read as Minnesota. Only a
    whole part naming another state counts: Kansas City, West Virginia and Mascoutah are not Kansas, Virginia or Utah."""
    import sys

    sys.path.insert(0, str(_REPO / "tools"))
    from ports_of_entry import places_of

    assert places_of("Ashland, Wisconsin Port Of Entry", "MN") == []
    assert places_of("Kansas City, Missouri", "MO") == ["KANSAS CITY"]
    assert places_of("Charleston, West Virginia", "WV") == ["CHARLESTON"]
    assert places_of("MidAmerica St. Louis Airport, Mascoutah, Illinois", "IL") == ["MIDAMERICA ST LOUIS AIRPORT", "MASCOUTAH"]
    assert places_of("Tacoma, Washington, Washington", "WA") == ["TACOMA"]
    assert arrival.port_states("Ashland") == {}
    g = _graph(ELSEWHERE, nta=SAN_LUIS.replace("SAN LUIS, Ad", "ASHLAND, Wl"))  # WI read with a lower-case L
    assert arrival.reading(g) is None


def test_a_real_state_that_contradicts_cbps_list_fills_nothing_and_the_card_shows_both():
    """Verification of K6, S9: "SAN LUIS, Al" is a real state (Alabama), but San Luis is a port of entry only in Arizona: the printed state
    may be misread, so nothing is filled at Tier 1 and the card names both."""
    g = _graph(ELSEWHERE, nta=SAN_LUIS.replace("SAN LUIS, Ad", "SAN LUIS, Al"))
    assert (g.get("nta.arrival_city").value, g.get("nta.arrival_state").value) == ("SAN LUIS", "AL")  # the paper's own facts, as printed
    read = arrival.reading(g)
    assert read["mismatch"] is True and (read["state"], read["printed_state"]) == ("AZ", "AL")
    for key in ("applicant.last_arrival_city", "applicant.last_arrival_state"):
        assert g.get(key).value is None and not any(s.doc_id == "nta.pdf" for s in g.get(key).sources), key  # not filled from the notice
    (key, text), = [f for f in consistency_findings(g) if f[0] == "applicant.last_arrival_city"]
    assert 'The notice prints "SAN LUIS, Al". San Luis is a port of entry only in Arizona, not in Alabama' in text
    assert "(\"Al\" as printed, or AZ from CBP's list of ports of entry)" in text and "The client wrote NEWARK, NJ." in text
    assert "outranks" not in text
    # a city and state that agree with the list, or a city that is a port in two states, are read as before
    assert arrival.reading(_graph(ELSEWHERE)) is None and _graph(ELSEWHERE).get("applicant.last_arrival_city").value == "HIDALGO"
    portland = _graph(ELSEWHERE, nta=SAN_LUIS.replace("SAN LUIS, Ad", "PORTLAND, OR"))
    assert arrival.reading(portland) is None and portland.get("applicant.last_arrival_state").value == "OR"


def test_the_country_gazetteer_skips_the_port_table():
    from extract import geo

    geo._index.cache_clear()
    assert geo._index()["BRAZIL"] == "BR" and not any("PORT" in k for k in geo._index())


@pytest.fixture
def port_case(tmp_path):
    source = tmp_path / "clients" / "demo" / "source"
    source.mkdir(parents=True)
    result = process_retained_documents("demo", source, [("nta.pdf", SAN_LUIS), ("q.pdf", "Questionário para Ajuste de Status\nI485 - SIJS\n")],
                                       questionnaire_reader=_reader, pages={"nta.pdf": _notice_pages(SAN_LUIS)})
    out = tmp_path / "data" / "demo"
    save_bundle(result, out, source)
    refill(out, FIELD_MAP, TEMPLATE)
    return out


def test_the_card_offers_the_reading_in_the_boxes_and_a_save_fills_item_10(port_case):
    import packet
    from pypdf import PdfReader

    from review.overview import review_row

    fields = PdfReader(str(port_case / "i485_filled.pdf")).get_fields()
    assert not fields[CITY_BOX].get("/V") and not fields[STATE_BOX].get("/V")  # neither the client's other city nor the reading, yet
    assert fields[DATE_BOX]["/V"] == "12/02/2021"
    card = _cards(port_case)["crosscheck:applicant.last_arrival_city"]
    assert card["title"] == "Last arrival in the U.S.: confirm the place the notice prints"
    boxes = {f["key"]: f for f in card["facts"]}
    assert (boxes["applicant.last_arrival_city"]["value"], boxes["applicant.last_arrival_city"]["suggest"]) == (None, "SAN LUIS")
    assert (boxes["applicant.last_arrival_state"]["value"], boxes["applicant.last_arrival_state"]["suggest"]) == (None, "AZ")
    assert 'The notice prints "SAN LUIS, Ad". San Luis is a port of entry in Arizona. Confirm or correct.' in " ".join(card["messages"])
    row = review_row(port_case, FIELD_MAP, TEMPLATE, CATALOG)
    assert any("review card" in p and "still open" in p for p in packet.plan(port_case, row)["problems"])  # the packet waits

    values = {"applicant.last_arrival_city": "SAN LUIS", "applicant.last_arrival_state": "AZ", "applicant.last_arrival_date": "2021-12-02"}
    record_decision(port_case, card, {"reviewer": "Jane Paralegal", "action": "set", "values": values, "note": "checked the notice"})
    assert "crosscheck:applicant.last_arrival_city" not in _cards(port_case)
    fields = PdfReader(refill(port_case, FIELD_MAP, TEMPLATE)["pdf"]).get_fields()
    assert (fields[CITY_BOX]["/V"], fields[STATE_BOX]["/V"]) == ("SAN LUIS", "AZ")
    g = reviewed_graph(port_case)
    assert g.get("applicant.last_arrival_state").review.resolved_by == "Jane Paralegal" and arrival.reading(g) is None
    undo_decision(port_case, "crosscheck:applicant.last_arrival_city", "Jane Paralegal")
    assert "crosscheck:applicant.last_arrival_city" in _cards(port_case)  # Undo reopens it, and the boxes wait again

