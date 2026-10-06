"""Retained birth/marriage examples with explicit named printed-role mappings."""
import documents
import subject_attribution as subjects
from review.state import save_bundle
from synthetic_documents import process_retained_documents
from test_people import SECOND_TRANSLATOR, MA_MARRIAGE


def retain_place_case(world, app, tmp_path, *, disagreement=False):
    case = world / "case-Ana"
    source = tmp_path / "place-source"
    marriage = MA_MARRIAGE.replace("SAO ANA, BRAZIL", "SOROCABA, BRAZIL") if disagreement else MA_MARRIAGE
    result = process_retained_documents(case.name, source, [("birth.pdf", SECOND_TRANSLATOR), ("marriage.pdf", marriage)],
                                        pages={"birth.pdf": [SECOND_TRANSLATOR], "marriage.pdf": [marriage]})
    save_bundle(result, case, source)
    actor = "Fictional Setup Reviewer"
    named = {"applicant": "Ficcaof Ficcaog da Ficcaob Ficcaok", "spouse": "Mateo Ficcaod Ficcaom",
             "mother": "Ficcaol Ficcaog Ficcaoc", "father": "Ana Ficcaoo dos Ficcaok"}
    people = {}
    for role, label in named.items():
        existing = next((p for p in documents.read(case)["case_subjects"]["people"] if p["case_role"] == role), None)
        if existing:
            subjects.rename_person(case, existing["id"], label, actor, "paralegal")
            people[role] = existing["id"]
        else:
            people[role] = subjects.add_person(case, label, role, actor, "paralegal")["id"]
    people["spouse_mother"] = subjects.add_person(case, "Ana Lucia Ficcaom Lopez (Mateo's mother)", "other", actor, "paralegal")["id"]
    people["spouse_father"] = subjects.add_person(case, "Juan Carlos Ficcaod Perez (Mateo's father)", "other", actor, "paralegal")["id"]
    mappings = {"birth_certificate": {"birth_subject": people["applicant"], "parent_a": people["mother"], "parent_b": people["father"]},
                "marriage_certificate": {"party_a": people["spouse"], "party_b": people["applicant"],
                                         "party_a_parent1": people["spouse_mother"], "party_a_parent2": people["spouse_father"],
                                         "party_b_parent1": people["mother"], "party_b_parent2": people["father"]}}
    for row in subjects.views(case):
        assert row["type"] in mappings and set(row["slots"]) <= set(mappings[row["type"]]), row
        subjects.assign(case, row["instance_id"], row["fingerprint"], {slot: mappings[row["type"]][slot] for slot in row["slots"]}, actor, "paralegal")
    app.roster.touch(case.name)
    return case
