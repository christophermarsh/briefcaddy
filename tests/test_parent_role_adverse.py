"""Independent fictional parent-role boundary probes for six-phase review."""
import pytest

from extract.birth_certificate import _filiation_entries


@pytest.mark.parametrize("heading", ["TESTEMUNHAS", "WITNESSES"])
def test_empty_parent_section_cannot_consume_following_witness_section(heading):
    text = (
        "FILIACAO\n" + heading + "\n"
        "ANA FICCAOO DOS FICCAOK, natural de Contagem - MG\n"
        "FICCAOL FICCAOG DA FICCAOB, natural de Belo Horizonte - MG\nAVOS"
    )
    assert _filiation_entries(text) == []
