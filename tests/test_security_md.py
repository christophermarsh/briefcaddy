"""SECURITY.md (brief J2) is made from deployment.json and checked by a test, so it is never stale: the repository's copy names no provider and no
contact (placeholders), an installation's names its own; the number of days is the owner's to set; no bounty is promised; nothing in it is untrue."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import security_md  # noqa: E402

import deployment  # noqa: E402


def test_the_repositorys_security_md_is_what_the_code_makes_from_the_shipped_defaults():
    assert (REPO / "SECURITY.md").read_text(encoding="utf-8") == security_md.render(dict(deployment.DEFAULT["provider"]))
    assert security_md.main(["--check"]) == 0


def test_it_says_the_placeholders_until_the_owner_fills_them():
    text = (REPO / "SECURITY.md").read_text(encoding="utf-8")
    assert "[the provider's security contact]" in text and "[days]" in text and "[the provider's name]" in text
    assert "the software provider" not in text  # the shipped default name is not a name


def test_an_installation_with_a_security_contact_gets_its_own_and_the_check_still_compares_with_the_repositorys(tmp_path, monkeypatch):
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps({"mode": "hosted", "provider": {"name": "Exemplo Support Co", "email": "security@exemplo.example"}}), encoding="utf-8")
    monkeypatch.setattr(deployment, "PATH", path)
    out = tmp_path / "SECURITY.md"
    assert security_md.main(["--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "Write to security@exemplo.example (Exemplo Support Co's security contact)." in text and "made and supported by Exemplo Support Co" in text
    assert "[the provider's security contact]" not in text and "[days]" in text
    assert security_md.main(["--check"]) == 0  # the repository's copy is still the defaults' copy


def test_no_reward_is_promised_nothing_untrue_and_the_wording_is_plain():
    text = (REPO / "SECURITY.md").read_text(encoding="utf-8")
    lower = text.lower()
    assert "we do not offer payment or rewards" in lower and "bounty" not in lower
    for claim in ("soc 2 compliant", "certified", "guarantee", "military-grade", "bank-grade", "fully encrypted"):
        assert claim not in lower, claim
    assert "—" not in text and " -- " not in text
    assert "docs/security/threat_model.md" in text and (REPO / "docs" / "security" / "threat_model.md").exists()
    assert "penetration test and a SOC 2 report are not done yet" in " ".join(text.split())  # the owner's items are said as not done


def test_the_owners_script_checks_it():
    assert "tools/security_md.py --check" in (REPO / "tools" / "ci.sh").read_text(encoding="utf-8")
