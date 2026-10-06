"""The owner's scanning script (tools/ci.sh, brief J2): its steps exist and call what is in the tree, the GitHub Actions file runs the same script
and only when someone asks, and the secret scan's patterns hit a planted sample of each kind in a scratch folder (never in the tree: each sample is
put together here at run time, so this file holds none). The tree itself scans clean."""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import secret_scan  # noqa: E402

SCRIPT = (REPO / "tools" / "ci.sh").read_text(encoding="utf-8")
STEPS = {"pip-audit": "tools/check_deps.sh", "ruff": "ruff check --select F,E9", "secrets": "tools/secret_scan.py", "dictionary": "tools/data_dictionary.py --check",
         "register": "register_redumps", "public": "tools/public_pages.py --check", "security-md": "tools/security_md.py --check", "unit": "pytest"}


def test_every_step_is_in_the_script_and_what_it_calls_is_in_the_tree():
    steps = dict(re.findall(r"^step (\S+) (.*)$", SCRIPT, re.M))
    assert set(STEPS) <= set(steps), set(STEPS) - set(steps)
    for name, needle in STEPS.items():
        assert needle in steps[name], (name, steps[name])
    for tool in ("tools/check_deps.sh", "tools/secret_scan.py", "tools/secret_patterns.txt", "tools/data_dictionary.py", "tools/public_pages.py"):
        assert (REPO / tool).exists(), tool
    assert 'sys.exit(0 if json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\\n" == text else 1)' in SCRIPT  # the register's own rule
    assert "set -uo pipefail" in SCRIPT and 'exit 1' in SCRIPT  # every step runs; the script fails at the end when one failed


def test_the_actions_file_runs_the_same_script_and_only_when_someone_asks():
    flow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "run: bash tools/ci.sh" in flow and "workflow_dispatch:" in flow
    trigger = flow.split("\non:", 1)[1].split("\npermissions:", 1)[0]
    assert "push" not in trigger and "pull_request" not in trigger and "schedule" not in trigger  # ready, not switched on: the owner decides
    assert "contents: read" in flow and "requirements.lock" in flow and "requirements-dev.txt" in flow


def test_the_script_passes_and_fails_by_its_steps(tmp_path):
    """Run for real with every slow or networked step left out: the register step passes on the tree as it is."""
    out = subprocess.run(["bash", "tools/ci.sh"], cwd=REPO, capture_output=True, text=True, timeout=300,
                         env={"PATH": "/usr/bin:/bin", "PYTHON": sys.executable, "CI_SKIP": "pip-audit ruff secrets dictionary public security-md unit"})
    assert out.returncode == 0 and "ok: register" in out.stdout and "Every step passed." in out.stdout, out.stdout + out.stderr


def _planted() -> dict[str, str]:
    """One sample of each shape, put together at run time (no sample is written in this file)."""
    hexes = "0123456789abcdef" * 2
    return {
        "private_key": "-----BEGIN " + "RSA PRIVATE" + " KEY-----",
        "twilio_account_sid": "AC" + hexes,
        "twilio_api_key": "SK" + hexes,
        "twilio_auth_token": "TWILIO_AUTH_TOKEN=" + hexes,
        "google_oauth_client_secret": "GOCSPX" + "-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4",
        "google_api_key": "AI" + "za" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q",
        "google_service_account_key": '"private_key_id": "' + "ab" * 20 + '"',
        "microsoft_client_secret": "abc" + "8Q~" + "Xy1Zw2Vu3Ts4Rq5Po6Nm7Lk8Ji9Hg0Fe1",
        "aws_access_key": "AK" + "IA" + "ABCDEFGHIJ234567",
        "github_token": "gh" + "p_" + "A1b2C3d4" * 5,
        "slack_token": "xo" + "xb-" + "1234567890-abcdefghij",
        "stripe_live_key": "sk" + "_live_" + "A1b2C3d4E5f6G7h8I9j0K1l2",
        "sendgrid_api_key": "SG" + "." + "A" * 22 + "." + "B" * 43,
        "fernet_key_in_settings": "I485_TOTP_KEY=" + "A" * 43 + "=",
        "oauth_secret_assigned": "client_secret = '" + "Zz9" * 8 + "'",
        # the verification's two plants (S2): a bare 40-hex token, and a password literal
        "generic_token_assigned": "tok" + "en = '" + "ab" * 20 + "'",
        "password_literal": "pass" + "word = 'correct horse " + "battery staple'",
    }


def test_each_pattern_hits_its_planted_sample_in_a_scratch_folder(tmp_path):
    names = {name for name, _ in secret_scan.patterns()}
    samples = _planted()
    assert set(samples) == names  # a sample for every pattern in the list
    for name, sample in samples.items():
        (tmp_path / f"{name}.txt").write_text(f"some text before\nsetting = {sample}\n", encoding="utf-8")
    hits = secret_scan.scan(tmp_path, banned=[])
    found = {(h["file"], h["what"]) for h in hits}
    for name in samples:
        assert (f"{name}.txt", name) in found, name
    assert all(len(h["seen"]) <= 7 for h in hits)  # the secret itself is never printed whole


def test_a_real_clients_name_is_found_by_its_hash_inside_a_word_in_any_case(tmp_path):
    made_up = "exemplonome"  # stands in for a banned name: the list itself holds only hashes
    banned = [(len(made_up), hashlib.sha256(made_up.encode()).hexdigest())]
    (tmp_path / "a.md").write_text("Client: Maria EXEMPLONOME\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("x = 'superexemplonomes'\n", encoding="utf-8")
    (tmp_path / "c.txt").write_text("Maria Exemplo, nothing here\n", encoding="utf-8")
    hits = secret_scan.scan(tmp_path, banned=banned, pattern_list=[], known={})
    assert sorted((h["file"], h["line"]) for h in hits) == [("a.md", 1), ("b.py", 1)] and all(h["seen"] == "" for h in hits)  # never printed
    whole = hashlib.sha256(b"superexemplonomes").hexdigest()
    assert [h["file"] for h in secret_scan.scan(tmp_path, banned=banned, pattern_list=[], known={whole: {"b.py"}})] == ["a.md"]  # a known word, where it was


def test_what_never_goes_in_git_and_what_is_not_text_are_skipped_and_an_allowed_line_is_too(tmp_path):
    sample = _planted()["aws_access_key"]
    for folder in ("data", "clients", ".git", ".venv-wsl", "__pycache__"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "x.txt").write_text(sample, encoding="utf-8")
    (tmp_path / "scan.pdf").write_text(sample, encoding="utf-8")
    (tmp_path / "blob.bin2").write_bytes(b"\0\1" + sample.encode())
    (tmp_path / "fixture.py").write_text(f"KEY = '{sample}'  # secret-scan: allow (a made-up key a test needs)\n", encoding="utf-8")
    assert secret_scan.scan(tmp_path, banned=[]) == []
    assert secret_scan.main([str(tmp_path)]) == 0


def test_the_tree_scans_clean_and_the_command_says_so(capsys):
    assert secret_scan.scan(REPO) == []
    assert secret_scan.main([]) == 0 and "nothing found" in capsys.readouterr().out


def test_the_pattern_list_and_the_scanner_hold_no_real_clients_name():
    """The list of banned names is hashes only: neither file holds a name in clear (checked against the hashes themselves)."""
    lengths = {n for n, _ in secret_scan.BANNED}
    digests = {d for _, d in secret_scan.BANNED}
    for name in ("secret_scan.py", "secret_patterns.txt"):
        words = set(re.findall(r"[a-z0-9]+", (REPO / "tools" / name).read_text(encoding="utf-8").lower()))
        assert not [w for w in words for n in lengths for i in range(len(w) - n + 1) if hashlib.sha256(w[i:i + n].encode()).hexdigest() in digests], name


@pytest.mark.parametrize("name", ["secret_scan.py", "secret_patterns.txt", "ci.sh"])
def test_the_scanning_files_are_plain_text_with_no_secret_of_their_own(name):
    text = (REPO / "tools" / name).read_text(encoding="utf-8")
    for pattern, rx in secret_scan.patterns():
        assert not rx.search(text) or name == "secret_patterns.txt", (name, pattern)
