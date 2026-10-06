"""Create or check the project's clean Python 3.12 core development environment.

Run with an existing Python 3.12: python tools/dev_setup.py setup|check.
No application startup, customer installation, system setup or model downloads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import venv
from pathlib import Path

from install_support import LINUX_ONLY, Problem, filtered_lock

REPO = Path(__file__).resolve().parents[1]
ENV = REPO / ".venv-dev"
# Native backend/fictional-workflow dependencies and their locked closure.
# Browser, local translation and local-model stacks remain explicit later setup.
CORE = frozenset("""
annotated-doc annotated-types anyio certifi cffi charset-normalizer colorama
cryptography execnet fastapi h11 httpcore httpx idna iniconfig joblib numpy
packaging pillow pillow-heif pluggy pycparser pydantic pydantic-core pygments
pypdf pypdfium2 pytesseract pytest pytest-xdist python-multipart requests
reportlab ruff scikit-learn scipy setuptools starlette threadpoolctl
typing-inspection typing-extensions urllib3 uvicorn zipcodes zxing-cpp
twilio pyjwt aiohttp aiohttp-retry aiohappyeyeballs aiosignal attrs frozenlist
multidict propcache yarl
click cloudpickle narwhals opentelemetry-api
""".split())


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)", line)
        if not match:
            raise Problem(f"Development requirements must be exact pins: {line}")
        name, version = match.groups()
        name = normalized(name)
        if name in out and out[name] != version:
            raise Problem(f"Conflicting development pin: {name}")
        out[name] = version
    return out


def profile() -> tuple[dict, str]:
    platform = "windows" if sys.platform == "win32" else "linux"
    lock = REPO / "requirements.lock"
    all_pins = pins(lock.read_text(encoding="utf-8"))
    available = pins(filtered_lock(lock, platform, development=True))
    missing = CORE - available.keys()
    if missing:
        raise Problem(f"Core packages missing from repository lock: {', '.join(sorted(missing))}")
    expected = {name: available[name] for name in sorted(CORE)}
    if platform == "windows":
        expected.update(pins((REPO / "requirements-dev-windows.txt").read_text(encoding="utf-8")))
    text = "".join(f"{name}=={version}\n" for name, version in sorted(expected.items()))
    omitted = {name: "Linux-only package" if platform == "windows" and LINUX_ONLY.match(name)
               else "Outside core development profile" for name in sorted(all_pins.keys() - expected.keys())}
    return {"profile": "core", "platform": platform, "python": "3.12", "pins": expected,
            "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
            "profile_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "excluded": omitted}, text


def interpreter() -> Path:
    return ENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def check_location() -> None:
    if ENV.is_symlink() or ENV.resolve() != REPO / ENV.name:
        raise Problem(".venv-dev must be a plain project-local directory.")


def check_environment() -> dict:
    check_location()
    if not interpreter().is_file():
        raise Problem(".venv-dev is missing. Run tools/dev_setup.py setup with Python 3.12.")
    config = (ENV / "pyvenv.cfg").read_text(encoding="utf-8").lower()
    if not re.search(r"^include-system-site-packages\s*=\s*false\s*$", config, re.MULTILINE):
        raise Problem(".venv-dev inherits system packages; choose a clean environment before proceeding.")
    # Isolated mode ignores PYTHONPATH and user-site packages. Inspect the actual
    # environment interpreter, even when this command uses a different Python.
    probe = """
import importlib.metadata as m, json, re, sys
norm = lambda n: re.sub(r'[-_.]+', '-', n).lower()
print(json.dumps({'python': list(sys.version_info[:2]), 'prefix': sys.prefix,
                  'packages': {norm(d.metadata['Name']): d.version for d in m.distributions()}}))
"""
    actual = json.loads(subprocess.check_output([str(interpreter()), "-I", "-c", probe], text=True))
    if actual["python"] != [3, 12] or Path(actual["prefix"]).resolve() != ENV.resolve():
        raise Problem(".venv-dev must use its own Python 3.12 interpreter.")
    return actual


def verify(expected: dict) -> None:
    actual = check_environment()
    mismatches = [f"{n}: expected {v}, found {actual['packages'].get(n, 'MISSING')}"
                  for n, v in expected["pins"].items() if actual["packages"].get(n) != v]
    extras = actual["packages"].keys() - expected["pins"].keys() - {"pip"}
    if mismatches or extras:
        raise Problem("\n".join(mismatches + [f"Outside profile: {n}" for n in sorted(extras)]))
    subprocess.run([str(interpreter()), "-I", "-m", "pip", "check"], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "check"))
    args = parser.parse_args()
    try:
        if sys.version_info[:2] != (3, 12):
            raise Problem("Run this command with an existing Python 3.12; it never installs Python.")
        expected, requirements = profile()
        manifest = ENV / "profile.json"
        if args.action == "setup":
            check_location()
            if not ENV.exists():
                venv.EnvBuilder(with_pip=True, system_site_packages=False).create(ENV)
            check_environment()
            (ENV / "requirements.lock").write_text(requirements, encoding="utf-8")
            subprocess.run([str(interpreter()), "-I", "-m", "pip", "--isolated", "install",
                            "--disable-pip-version-check", "--no-deps", "--only-binary=:all:",
                            "--retries", "0", "--index-url", "https://pypi.org/simple",
                            "-r", str(ENV / "requirements.lock")], check=True)
            verify(expected)
            manifest.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
        else:
            verify(expected)
            if not manifest.is_file() or json.loads(manifest.read_text(encoding="utf-8")) != expected:
                raise Problem("Development lock/profile changed; run setup to refresh the environment receipt.")
        print(f"Verified Python 3.12 core profile: {len(expected['pins'])} exact pins, no inherited packages.")
        print(f"Interpreter: {interpreter()}")
        print(f"Lock SHA256: {expected['lock_sha256']}")
        return 0
    except (Problem, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Developer setup: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
