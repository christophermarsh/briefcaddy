"""Root's initial six independent probes, extended with builder regressions.

All names/text/PDFs are fictional; none reconstructs the user's actual OCR.
"""
from extract.marriage_certificate import extract
from test_name_events import _graph, MA_NO_FIELD
import name_events
import pytest

A = "ELENA MARIS TESTE"
B = "LUCIA ALBA EXEMPLO"
AFTER_B = "LUCIA ALBA NOVA"
HEADER = "Certificate of Marriage\nParty A Party B\nName: " + A + " Name: " + B + "\n"


def after_values(text):
    return {f.fact_key: f.normalized_value for f in extract(text) if f.fact_key.endswith(("name_after", "surname_after"))}


def test_paired_full_name_row_keeps_party_b_separate():
    values = after_values(HEADER + "Name after Marriage: " + A + " Name after Marriage: " + AFTER_B)
    assert values["marriage.party_b.name_after"] == AFTER_B
    assert values["marriage.party_a.name_after"] == A


def test_empty_first_column_does_not_shift_party_b():
    values = after_values(HEADER + "Name after Marriage: ----- Name after Marriage: " + AFTER_B)
    assert values == {"marriage.party_b.name_after": AFTER_B}


def test_unattributed_single_field_cannot_silently_become_party_a():
    values = after_values(HEADER + "Name after Marriage: " + AFTER_B)
    assert not values, values


def test_explicit_separate_party_blocks_retain_party_b():
    text = ("Certificate of Marriage\nParty A\nName: " + A + "\nName after Marriage: " + A
            + "\nParty B\nName: " + B + "\nName after Marriage: " + AFTER_B)
    values = after_values(text)
    assert values["marriage.party_b.name_after"] == AFTER_B
    assert values["marriage.party_a.name_after"] == A


def test_explicit_party_b_label_is_not_assigned_to_party_a():
    values = after_values(HEADER + "Party B Name after Marriage: " + AFTER_B)
    assert values == {"marriage.party_b.name_after": AFTER_B}


def test_no_extracted_field_does_not_claim_printed_absence():
    graph = _graph(marriage=MA_NO_FIELD)
    question = name_events.settle(graph)["question"]
    sentence = question["sentence"].lower()
    assert "does not say" not in sentence and "none says" not in sentence, sentence
    assert any(word in sentence for word in ("read", "extract", "verify", "check")), sentence


@pytest.mark.parametrize("tail,state", [
    ("", "not_extracted"),
    ("Party B Full Name after Marriage: -----", "not_stated"),
    ("Party B Full Name after Marriage: ??? unreadable", "unreadable"),
    ("Full Name after Marriage: " + AFTER_B, "ambiguous"),
    ("Party B Full Name after Marriage: " + AFTER_B + "\nParty B Full Name after Marriage: LUCIA ALBA OUTRA", "ambiguous"),
])
def test_uncertainty_retains_attempt_without_inventing_name(tail, state):
    fields = {f.fact_key: f for f in extract(HEADER + tail)}
    attempt = fields["marriage.party_b.after_read_state"]
    assert attempt.normalized_value == state
    assert "marriage.party_b.name_after" not in fields
    if tail:
        assert tail.splitlines()[0] in attempt.raw_value
    if state in {"unreadable", "ambiguous"}:
        assert attempt.reading_issues


def test_explicit_b_first_block_keeps_original_identity_and_dob():
    text = ("Certificate of Marriage\nParty B\nName: " + B + "\nDate of Birth: MAY 2, 2004"
            + "\nFull Name after Marriage: " + AFTER_B + "\nParty A\nName: " + A
            + "\nDate of Birth: MARCH 14, 2006\nFull Name after Marriage: " + A)
    values = {f.fact_key: f.normalized_value for f in extract(text)}
    assert values["marriage.party_b.name"] == B
    assert values["marriage.party_b.dob"] == "2004-05-02"
    assert values["marriage.party_b.name_after"] == AFTER_B
    assert values["marriage.party_a.name"] == A


def test_later_uncertain_certificate_cannot_be_hidden_by_earlier_name_event():
    from test_name_events import MA_FULL_NAME, MA_NO_FIELD
    later = MA_NO_FIELD.replace("JULY 1, 2026", "AUGUST 3, 2026") + "\nFull Name after Marriage: UNATTRIBUTED EXAMPLE"
    graph = _graph(marriage=MA_FULL_NAME, extra=(("later.pdf", "marriage_certificate", extract(later)),))
    data = name_events.settle(graph)
    assert data["question"]["open"] and data["question"]["doc"] == "later.pdf"
    assert data["question"]["read_state"] == "ambiguous"
    assert not data["decided"]


def test_conflicting_retained_field_sources_do_not_choose_first_name():
    from test_name_events import MA_FULL_NAME
    graph = _graph(marriage=MA_FULL_NAME)
    graph.add_source("marriage.party_a.name_after", "casamento.pdf", "marriage_certificate", "Competing read", "ANA CLARA OUTRA", 0.9)
    data = name_events.settle(graph)
    assert not any(e["kind"] == "marriage" for e in data["events"])
    assert data["question"]["open"] and data["question"]["read_state"] == "ambiguous"


def test_unique_marriage_page_is_unknown_for_repeated_or_absent_text():
    from batch import unique_page_of
    line = "Party B Full Name after Marriage: " + AFTER_B
    assert unique_page_of(["Cover", line], line) == 1
    assert unique_page_of([line, line], line) is None
    assert unique_page_of(["Cover", "Something else"], line) is None


def test_actual_retained_party_b_full_name_page_two_save_prior_and_undo(tmp_path):
    from test_name_events import MA_REAL_SHAPE, BIRTH, MARRIED
    from test_name_cards import (_retained_name_case, _items, CURRENT, _set, _value,
                                 _independent_name_source_checks, EARLIER)
    from review.state import reviewed_graph, load_decision_log, undo_decision
    first = MA_REAL_SHAPE + "\nPage 1 of 2"
    after = "Certificate of Marriage\nPage 2 of 2\nParty A Full Name after Marriage: JOAO PEDRO TESTE\nParty B FULL Name after Marriage: " + MARRIED
    out = _retained_name_case(tmp_path, first + "\n" + after, applicant_party="party_b",
                              pages={"casamento.pdf": [first, after]})
    card = _items(out)[CURRENT]
    from review.evidence import annotate
    annotate(out, card)
    event = next(e for e in card["timeline"]["events"] if e.get("parent_key") == "marriage.party_b.name_after")
    assert event["name"] == MARRIED and event["party"] == "party_b" and event["page"] == 1
    assert event["source_location"]["page"] == 1 and event["source_location"]["state"] == "current"
    assert card["facts"][0]["input"]["options"] == [MARRIED, BIRTH]
    graph = reviewed_graph(out)
    source = next(s for s in graph.get("applicant.family_name").sources if "marriage.party_b.name_after" in s.from_facts)
    actual = next(s for s in graph.get("marriage.party_b.name_after").sources if s.doc_id == source.doc_id)
    assert source.input_evidence == [actual.evidence_version] and source.page == 1
    assert "Party B FULL Name after Marriage" in actual.raw_value
    _independent_name_source_checks(out)
    _set(out, CURRENT, MARRIED, note="Reviewed fictional Party B full printed name.")
    graph = reviewed_graph(out)
    assert _value(graph, "applicant.family_name") == "EXEMPLO SOUZA TESTE"
    assert (_value(graph, "applicant.other_name1_given"), _value(graph, "applicant.other_name1_family")) == ("ANA CLARA", "EXEMPLO SOUZA")
    _independent_name_source_checks(out)
    assert load_decision_log(out)[CURRENT]["reviewer"] == "Pat Paralegal"
    undo_decision(out, CURRENT, "Pat Paralegal")
    assert CURRENT in _items(out) and load_decision_log(out)[CURRENT]["undone"]["by"] == "Pat Paralegal"
    assert EARLIER in _items(out)[CURRENT]["facts"][0]["input"]["options"]


@pytest.mark.parametrize("printed,expected", [("LUCIA ALBA NOVÁ", "LUCIA ALBA NOVA"), ("ANA MARÍA NOVA", "ANA MARIA NOVA"),
                                             ("LUCIA ALBA NOŁA", None), ("LUCIA ALBA nova", None), ("LUCIA ALBA & NOVA", None)])
def test_accented_or_unsupported_uppercase_never_becomes_truncated_candidate(printed, expected):
    fields = after_values(HEADER + "Party B Full Name after Marriage: " + printed)
    assert fields.get("marriage.party_b.name_after") == expected


@pytest.mark.parametrize("change", ["value", "raw", "page", "instance", "evidence", "reader"])
def test_saved_choice_reopens_when_same_certificate_read_changes(change):
    from test_name_events import MA_FULL_NAME, BIRTH
    graph = _graph(marriage=MA_FULL_NAME, reviews={name_events.CURRENT_KEY: BIRTH,
                                                name_events.OVER_KEY: '["certidao.pdf", "casamento.pdf"]'})
    assert name_events.settle(graph)["decided"]
    original = next(s for s in graph.get("marriage.party_a.name_after").sources if s.doc_id == "casamento.pdf")
    if change == "value":
        original.normalized_value = "ANA CLARA EXEMPLO NOVA"
    elif change == "raw":
        original.raw_value += " amended fictional read"
    elif change == "page":
        original.page = 1
    elif change == "instance":
        original.instance_id = "replacement-fictional-instance"
    elif change == "evidence":
        original.evidence_version = "replacement-fictional-evidence"
    else:
        original.read_manifest = {"reader": "replacement-fictional-reader"}
    data = name_events.settle(graph)
    assert not data["decided"] and data["stale"]["evidence_changed"]
    assert data["current"]["name"] == BIRTH
    assert graph.get(name_events.CURRENT_KEY).review.chosen_value == BIRTH
    assert any(flag.kind == "names" for flag in name_events.flags(graph))


def test_legacy_missing_snapshot_requires_marriage_review_without_inventing_history():
    from test_name_events import MA_FULL_NAME, BIRTH
    graph = _graph(marriage=MA_FULL_NAME, reviews={name_events.CURRENT_KEY: BIRTH})
    graph._facts.pop(name_events.EVIDENCE_KEY)
    old_review = graph.get(name_events.CURRENT_KEY).review
    data = name_events.settle(graph)
    assert not data["decided"] and data["stale"]["legacy_evidence"]
    assert graph.get(name_events.CURRENT_KEY).review == old_review
    assert graph.get(name_events.EVIDENCE_KEY) is None
    assert not name_events.settle(_graph(marriage=None, reviews={name_events.CURRENT_KEY: BIRTH})).get("stale")


@pytest.mark.parametrize("removed", ["removed", "unmatched"])
def test_saved_choice_holds_when_prior_marriage_becomes_unavailable(removed):
    from test_name_events import MA_FULL_NAME, MARRIED
    graph = _graph(marriage=MA_FULL_NAME, reviews={name_events.CURRENT_KEY: MARRIED})
    if removed == "removed":
        for fact in graph.all_facts().values():
            fact.sources[:] = [s for s in fact.sources if s.doc_type != "marriage_certificate"]
    else:
        graph._subject_roles = {"casamento.pdf": {"party_a": "spouse", "party_b": "other"}}
    data = name_events.settle(graph)
    assert data["stale"]["source_unavailable"] and not data["decided"]
    assert data["current"]["name"] == MARRIED
    assert any(flag.kind == "names" for flag in name_events.flags(graph))
    assert "no longer attributable" in data["why"]


def test_reversed_column_header_keeps_party_name_dob_and_full_after_together():
    text = ("Certificate of Marriage\nParty B Party A\nName: " + B + " Name: " + A
            + "\nDate of Birth: MAY 2, 2004 Date of Birth: MARCH 14, 2006"
            + "\nFull Name after Marriage: " + AFTER_B + " Full Name after Marriage: " + A)
    values = {f.fact_key: f.normalized_value for f in extract(text)}
    assert values["marriage.party_b.name"] == B and values["marriage.party_b.dob"] == "2004-05-02"
    assert values["marriage.party_b.name_after"] == AFTER_B
    assert values["marriage.party_a.name"] == A and values["marriage.party_a.name_after"] == A


def test_saved_radio_name_absent_from_reread_remains_human_review_context():
    from test_name_events import MA_FULL_NAME, MARRIED
    graph = _graph(marriage=MA_FULL_NAME, reviews={name_events.CURRENT_KEY: MARRIED})
    source = next(s for s in graph.get("marriage.party_a.name_after").sources if s.doc_id == "casamento.pdf")
    source.normalized_value = "ANA CLARA EXEMPLO NOVA"
    source.raw_value = "Name after Marriage: ANA CLARA EXEMPLO NOVA"
    data = name_events.settle(graph)
    assert not data["decided"] and data["stale"]["evidence_changed"]
    assert data["current"]["name"] == MARRIED and data["current"]["kind"] == "review_choice"
    assert any(e["kind"] == "marriage" and e["name"] == "ANA CLARA EXEMPLO NOVA" for e in data["events"])
    assert not any(e["kind"] == "marriage" and e["name"] == MARRIED for e in data["events"])
    assert graph.get(name_events.CURRENT_KEY).review.chosen_value == MARRIED
    assert any(flag.kind == "names" for flag in name_events.flags(graph))
    assert "ANA CLARA EXEMPLO NOVA" not in data["others"]
