"""Fictional fixture helper."""
from assemble import applicant_party, assemble
from extract.names import split_name, surname_tokens, same_person_name
from factgraph import FactGraph

CHILD = "RAFAELA DEMONSTRA MARFIM"
SPOUSE = "BENTO EXEMPLO AZUL"

def test_split_is_proven_by_a_grandparents_surname():
    hints = surname_tokens("ALINE DEMONSTRA MARFIM", "CAIO DEMONSTRA VERDE")
    split = split_name(CHILD, hints)
    assert (split.given, split.family, split.proven) == ("RAFAELA", "DEMONSTRA MARFIM", True)

def test_split_is_proven_by_the_parenthesised_surnames_on_a_marriage_record():
    split = split_name(SPOUSE, {"EXEMPLO", "AZUL"})
    assert (split.given, split.family, split.proven) == ("BENTO", "EXEMPLO AZUL", True)

def test_particle_before_the_inherited_surname_stays_with_the_family_name():
    split = split_name("RAFAELA DE DEMONSTRA MARFIM", {"DEMONSTRA", "MARFIM"})
    assert (split.given, split.family, split.proven) == ("RAFAELA", "DE DEMONSTRA MARFIM", True)

def test_compound_given_name_is_never_marked_proven():
    split = split_name("ANA MARIA DEMONSTRA MARFIM", {"DEMONSTRA", "MARFIM"})
    assert (split.given, split.family, split.proven) == ("ANA MARIA", "DEMONSTRA MARFIM", False)

def test_no_family_evidence_falls_back_to_the_firm_convention_unproven():
    split = split_name(CHILD, set())
    assert (split.given, split.family, split.proven) == ("RAFAELA", "DEMONSTRA MARFIM", False)

def test_explicit_family_name_preserves_compound_given_name():
    split = split_name("ANA MARIA DEMONSTRA MARFIM", explicit_family="DEMONSTRA MARFIM")
    assert (split.given, split.family, split.proven) == ("ANA MARIA", "DEMONSTRA MARFIM", True)

def test_one_letter_ocr_difference_is_tolerated_without_merging_different_people():
    assert same_person_name(CHILD, "RAFAELA DEMONSTRA MARFIM")
    assert same_person_name(CHILD, "RAFAELA DEMONSTRB MARFIM")
    assert not same_person_name(CHILD, SPOUSE)

def _graph():
    graph = FactGraph("fictional-evidence-relations")
    for key, value in {
        "applicant.given_name": "RAFAELA", "applicant.family_name": "DEMONSTRA MARFIM",
        "applicant.dob": "1994-04-08", "marriage.party_a.name": SPOUSE,
        "marriage.party_a.dob": "1991-07-19", "marriage.party_b.name": CHILD,
        "marriage.party_b.dob": "1994-04-08",
        "marriage.party_a.birthplace": "Brazil", "marriage.party_b.birthplace": "Brazil",
        "applicant.marriage_date": "2022-06-14",
    }.items():
        graph.add_source(key, "fictional-marriage.pdf", "marriage_certificate", value, value, 0.95)
    return graph

def test_applicant_is_found_by_name_and_birth_date_not_column():
    assert applicant_party(_graph()) == "party_b"

def test_spouse_boxes_come_from_the_marriage_certificate():
    graph = _graph()
    assemble(graph)
    assert graph.get("applicant.spouse_given_name").value == "BENTO"
    assert graph.get("applicant.spouse_family_name").value == "EXEMPLO AZUL"
    assert graph.get("applicant.spouse_dob").value == "1991-07-19"
