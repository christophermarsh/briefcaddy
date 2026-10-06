"""Attributed printed names retain all tokens; unsupported tails stay uncertain."""
import pytest

from extract.marriage_certificate import extract


@pytest.mark.parametrize("party", ["A", "B"])
@pytest.mark.parametrize("label", ["Name after Marriage", "Full Name after Marriage", "Surname after Marriage"])
@pytest.mark.parametrize("printed", ["LUCIA ALBA X", "LUCIA ALBA S", "LUCIA ALBA D E", "LÚCIA ALBA D E"])
def test_full_attributed_read_preserves_initials_and_letter_sequence(party, label, printed):
    text = "Certificate of Marriage\nParty " + party + " " + label + ": " + printed
    fields = {row.fact_key: row for row in extract(text)}
    prefix = "marriage.party_" + party.lower() + "."
    key = prefix + ("surname_after" if label.startswith("Surname") else "name_after")
    assert fields[prefix + "after_read_state"].normalized_value == "explicit"
    value = fields[key]
    assert value.normalized_value == printed.upper().replace("Ú", "U").replace(".", "")
    assert printed in value.raw_value and not value.reading_issues


@pytest.mark.parametrize("suffix", ["ip", "iped", "other", "2", "x", "X2", "= ip", "X."])
def test_unconsumed_printed_suffix_is_held_with_raw_evidence_not_dropped(suffix):
    raw = "Name after Marriage: MARCO NOVA Name after Marriage: LUCIA ALBA " + suffix
    fields = {row.fact_key: row for row in extract("Certificate of Marriage\nParty A Party B\n" + raw)}
    assert fields["marriage.party_a.name_after"].normalized_value == "MARCO NOVA"
    assert "marriage.party_b.name_after" not in fields
    state = fields["marriage.party_b.after_read_state"]
    assert state.normalized_value == "unreadable" and state.reading_issues and suffix in state.raw_value


def test_paired_party_columns_preserve_all_initials_without_sharing_names():
    text = "Certificate of Marriage\nParty B Party A\nName after Marriage: LUCIA ALBA D E Name after Marriage: MARCO NOVA X"
    fields = {row.fact_key: row for row in extract(text)}
    assert fields["marriage.party_b.name_after"].normalized_value == "LUCIA ALBA D E"
    assert fields["marriage.party_a.name_after"].normalized_value == "MARCO NOVA X"
    assert all(fields["marriage.party_" + party + ".after_read_state"].normalized_value == "explicit" for party in ("a", "b"))


@pytest.mark.parametrize("party", ["A", "B"])
@pytest.mark.parametrize("label", ["Name after Marriage", "Full Name after Marriage"])
@pytest.mark.parametrize("printed", ["UNKNOWN", "UNREADABLE", "ILLEGIBLE", "ALPHA", "E EES TE A", "LUCIA A ALBA X", "LUCIA ALBA X S Z"])
def test_full_name_plausibility_stays_conservative_with_initial_allowance(party, label, printed):
    raw = "Party " + party + " " + label + ": " + printed
    fields = {row.fact_key: row for row in extract("Certificate of Marriage\n" + raw)}
    prefix = "marriage.party_" + party.lower() + "."
    assert prefix + "name_after" not in fields
    state = fields[prefix + "after_read_state"]
    assert state.normalized_value == "unreadable" and state.reading_issues and printed in state.raw_value
