"""Document-name standardization (src/classify/standardize.py). Naming
logic (_assign_names) is tested directly against fabricated Classification
objects -- no PDF fixtures needed, same reasoning as classify_text vs
classify_document. apply_standard_names gets a filesystem-level check with
real (empty) files, since copy/rename behavior is the one part that
actually needs a disk."""

from pathlib import Path

from classify import Classification
from classify.standardize import RenamePlan, _assign_names, apply_standard_names


def _c(doc_type, confidence=0.9, ambiguous_with=None):
    return Classification(doc_type=doc_type, confidence=confidence, ambiguous_with=ambiguous_with or [])


def test_single_document_of_a_type_gets_the_plain_doc_type_name():
    plans = _assign_names({Path("Approval Notice I130.pdf"): _c("i360_approval")})
    assert plans[0].new_name == "i360_approval.pdf"


def test_multiple_documents_of_the_same_type_get_numbered():
    classifications = {
        Path("scan1.pdf"): _c("birth_certificate"),
        Path("scan2.pdf"): _c("birth_certificate"),
    }
    plans = _assign_names(classifications)
    names = sorted(p.new_name for p in plans)
    assert names == ["birth_certificate_1.pdf", "birth_certificate_2.pdf"]


def test_unclassified_document_keeps_its_original_name_with_a_prefix():
    plans = _assign_names({Path("scan47.pdf"): _c("unclassified", confidence=0.0)})
    assert plans[0].new_name == "unclassified__scan47.pdf"


def test_ambiguous_classification_is_not_silently_renamed_to_the_top_guess():
    # Real signal shape from tests/test_classify.py's near-tie case: a
    # confident top_type but a close-scoring competitor -- must not be
    # renamed as if it were an unambiguous i94.
    plans = _assign_names({Path("notice.pdf"): _c("i94", ambiguous_with=["uscis_notice"])})
    assert plans[0].new_name == "i94__ambiguous_with_uscis_notice__notice.pdf"


def test_a_correctly_named_singleton_is_not_renumbered_by_an_unrelated_type():
    classifications = {
        Path("a.pdf"): _c("i94"),
        Path("b.pdf"): _c("ssn_card"),
    }
    plans = _assign_names(classifications)
    names = {p.new_name for p in plans}
    assert names == {"i94.pdf", "ssn_card.pdf"}


def test_apply_standard_names_copies_by_default_leaving_original_intact(tmp_path):
    original = tmp_path / "Approval Notice I130.pdf"
    original.write_bytes(b"%PDF-1.4 fake content")
    output_dir = tmp_path / "standardized"

    plan = RenamePlan(original, "i360_approval.pdf", "i360_approval", 0.95)
    renamed = apply_standard_names([plan], output_dir, copy=True)

    assert original.exists()  # untouched
    assert (output_dir / "i360_approval.pdf").exists()
    assert renamed[str(original)] == str(output_dir / "i360_approval.pdf")


def test_apply_standard_names_renames_in_place_when_copy_is_false(tmp_path):
    original = tmp_path / "Approval Notice I130.pdf"
    original.write_bytes(b"%PDF-1.4 fake content")

    plan = RenamePlan(original, "i360_approval.pdf", "i360_approval", 0.95)
    apply_standard_names([plan], tmp_path, copy=False)

    assert not original.exists()
    assert (tmp_path / "i360_approval.pdf").exists()


def test_apply_standard_names_skips_a_file_already_correctly_named(tmp_path):
    original = tmp_path / "i94.pdf"
    original.write_bytes(b"%PDF-1.4 fake content")

    plan = RenamePlan(original, "i94.pdf", "i94", 0.9)
    # Would raise (shutil.SameFileError) if this tried to copy onto itself.
    renamed = apply_standard_names([plan], tmp_path, copy=True)

    assert original.exists()
    assert renamed[str(original)] == str(tmp_path / "i94.pdf")
