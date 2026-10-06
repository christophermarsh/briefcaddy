"""The research note on a hardware key for attorneys (brief R7, docs/research/hardware_key_note.md) stays what the brief asked for: dated, a draft,
exactly five sections, no library named that is not on PyPI, every test it cites real, and pointed to from the decisions log and the security
program. The note recommends; this only guards its shape, so a later edit cannot leave it half-written."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NOTE = REPO / "docs" / "research" / "hardware_key_note.md"

SECTIONS = [
    "1. What the second factor already covers",
    "2. What a hardware key adds",
    "3. The choices",
    "4. The recommendation",
    "5. What the buyer is told meanwhile",
]

# Every package the note names, each checked on pypi.org/pypi/<name>/json on 10/04/2026 (the choices, and the packages they pull in).
ON_PYPI = {
    "webauthn", "fido2", "py-webauthn", "webauthn-rp", "passkeys", "pywarp",  # the choices
    "soft-webauthn", "django-mfa2", "django-passkeys",  # named in passing
    "cbor2", "pyasn1", "pyasn1-modules", "pyopenssl", "cryptography", "oscrypto", "certvalidator", "asn1crypto", "pyscard", "pip-audit",
}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _text() -> str:
    return NOTE.read_text(encoding="utf-8")


def _section(text: str, number: int) -> str:
    parts = re.split(r"^## ", text, flags=re.M)[1:]
    return parts[number - 1]


def test_the_note_exists_is_dated_and_a_draft_for_the_provider():
    assert NOTE.exists()
    head = "\n".join(_text().splitlines()[:5])
    assert re.search(r"\b(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/20\d\d\b", head), "the note must carry its date, MM/DD/YYYY, at the top"
    assert "DRAFT for the provider" in head


def test_the_note_has_exactly_the_five_sections_in_order():
    headings = re.findall(r"^## (.+)$", _text(), flags=re.M)
    assert headings == SECTIONS


def test_the_note_names_no_library_that_is_not_on_pypi():
    text = _text()
    named: set[str] = set()
    # the first cell of every row of the choices table, a name in backticks that looks like a package (not a path or a file)
    for row in _section(text, 3).splitlines():
        if row.startswith("|") and not row.startswith("|---"):
            first = row.split("|")[1]
            named |= {t for t in re.findall(r"`([^`]+)`", first) if NAME.match(t)}
    # anything that is installed by name, and any backticked name that looks like a WebAuthn, FIDO or passkey package
    named |= {t for t in re.findall(r"pip install ([^\s`]+)", text) if NAME.match(t)}
    # (a class or function of a library, such as Fido2Server, has capitals or underscores: not a package name)
    named |= {t for t in re.findall(r"`([^`]+)`", text) if NAME.match(t) and t == t.lower() and "_" not in t and re.search(r"webauthn|fido|passkey|pywarp", t)}
    named = {n.lower() for n in named}
    assert named, "the note names no library at all"
    assert named <= ON_PYPI, f"libraries named in the note that are not on the PyPI list in this test: {sorted(named - ON_PYPI)}"
    # the choices the brief asked about are all there
    assert {"webauthn", "fido2"} <= named


def test_the_note_has_the_table_and_a_recommendation_and_two_sentences_for_the_buyer():
    text = _text()
    assert sum(1 for line in _section(text, 3).splitlines() if line.startswith("|")) >= 6
    assert re.search(r"\*\*(Keep the time-based code|Build with)", _section(text, 4))
    quote = [line for line in _section(text, 5).splitlines() if line.startswith("> ")]
    assert len(quote) == 1 and len(re.findall(r"[.;]\s+[A-Z]", quote[0])) >= 1, "the buyer's words are one quoted block of two sentences"


def test_every_test_the_note_cites_exists():
    cited = re.findall(r"`(tests/[\w/]+\.py)::(\w+)`", _text())
    assert cited, "the note cites no test"
    missing = [f"{path}::{name}" for path, name in cited if not re.search(rf"^\s*def {name}\(", (REPO / path).read_text(encoding="utf-8"), re.M)]
    assert missing == [], f"tests the note cites that are not there: {missing}"


def test_no_em_dash_in_the_note():
    assert "—" not in _text() and "–" not in _text()


def test_the_decisions_log_and_the_security_program_point_to_the_note():
    decisions = (REPO / "docs" / "decisions.md").read_text(encoding="utf-8")
    assert "docs/research/hardware_key_note.md" in decisions
    program = (REPO / "docs" / "security" / "security_program.md").read_text(encoding="utf-8")
    line = next(ln for ln in program.splitlines() if "Hardware keys and passkeys are not supported" in ln)
    assert re.search(r"see the research note of (0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/20\d\d", line)
