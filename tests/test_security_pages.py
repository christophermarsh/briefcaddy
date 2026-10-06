"""The security pack's pages (docs/security/*.md) must not describe a product that does not exist (wave R, R1): every code file a page names is in
the tree, every release it names is in docs/releases.md, and a claim that a built feature is "not built" is caught by name. A buyer reads these
pages against the product; a stale line costs the sale. (tests/test_security_pack.py covers the export of the pack as a zip.)"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PACK = sorted((REPO / "docs" / "security").glob("*.md"))
# features the pack once called unbuilt, each with the module that builds it: the page may describe them, never deny them
BUILT = {
    "restricted case": "src/restricted.py",
    "second factor": "src/review/totp.py",
    "view log": "src/review/server.py",
}
DENIED = re.compile(r"\b(?:is not built|not built yet|is not (?:yet )?(?:in place|done|supported))\b", re.I)


def _pages() -> list[tuple[Path, str]]:
    return [(p, p.read_text(encoding="utf-8")) for p in PACK]


def test_the_pack_has_pages():
    assert len(PACK) >= 8


@pytest.mark.parametrize("page", PACK, ids=lambda p: p.name)
def test_every_code_file_a_security_page_names_exists(page):
    text = page.read_text(encoding="utf-8")
    named = set(re.findall(r"\b((?:src|tools|tests)/[\w/.-]+\.(?:py|sh))\b", text))
    missing = sorted(n for n in named if not (REPO / n).exists())
    assert missing == [], f"{page.name} names code that is not in the tree: {missing}"


@pytest.mark.parametrize("page", PACK, ids=lambda p: p.name)
def test_every_release_a_security_page_names_is_in_the_release_notes(page):
    text = page.read_text(encoding="utf-8")
    releases = set(re.findall(r"^## (2026\.\d+\.\d+)", (REPO / "docs" / "releases.md").read_text(encoding="utf-8"), re.M))
    named = set(re.findall(r"\brelease (2026\.\d+\.\d+)\b", text))
    assert named <= releases, f"{page.name} names a release the notes do not have: {sorted(named - releases)}"


@pytest.mark.parametrize("feature,module", sorted(BUILT.items()))
def test_a_built_feature_is_never_called_unbuilt_by_the_pack(feature, module):
    assert (REPO / module).exists(), f"{module} is gone: update BUILT"
    hits = []
    for page, text in _pages():
        for n, line in enumerate(text.splitlines(), 1):
            if feature in line.lower() and DENIED.search(line):
                hits.append(f"{page.name}:{n}")
    assert hits == [], f"the pack says the {feature} is not built, but {module} builds it: {hits}"
