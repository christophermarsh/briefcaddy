#!/usr/bin/env bash
# The set-up of a Claude Code cloud environment for this repository (claude.ai/code, environment settings, "setup script": `bash tools/cloud_setup.sh`).
# The same packages tools/ci.sh and .github/workflows/ci.yml install, plus the browser the e2e tests drive, in a virtual environment of its
# own (.venv-cloud, Python 3.12: the lock file pins packages for 3.12, and the machine's system Python may be another version or refuse pip).
# It must finish inside the environment's caching window (about five minutes), so nothing here downloads a model or a large file except the
# browser and, when the network allows, the two offline translation packages the fifth-visit tests need.
#
# A cloud session sees only what git holds: no data/, no clients/, no real case. Its work is a branch; the owner's machine merges.
# Everything below is best effort: a step that cannot run says so and the script goes on, so a session always starts.
set -uo pipefail
cd "$(dirname "$0")/.."

say() { echo "cloud set-up: $*"; }

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -qq >/dev/null 2>&1 || say "apt-get update failed (no sudo or no network): going on"
  sudo apt-get install -y -qq tesseract-ocr python3.12 python3.12-venv >/dev/null 2>&1 || say "apt-get install failed: going on with what is here"
fi

# a Python 3.12 of its own: uv when present (fast, fetches 3.12 if the machine lacks it), else python3.12, else whatever python3 is.
# An environment uv makes has no pip in it: installs then go through `uv pip` (same environment, same result).
PY=""
INSTALL=""
if command -v uv >/dev/null 2>&1; then
  if uv venv --python 3.12 .venv-cloud >/dev/null 2>&1; then PY=.venv-cloud/bin/python; INSTALL="uv pip install --quiet --python $PY"; fi
fi
if [ -z "$PY" ]; then
  for candidate in python3.12 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -m venv .venv-cloud >/dev/null 2>&1; then
      PY=.venv-cloud/bin/python
      $PY -m ensurepip --upgrade >/dev/null 2>&1 || true
      $PY -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
      INSTALL="$PY -m pip install --quiet"
      break
    fi
  done
fi
if [ -z "$PY" ]; then say "no Python could make a virtual environment: stopping"; exit 0; fi
say "python: $($PY --version 2>&1); installing with: ${INSTALL%% *}"

if ! $INSTALL --no-deps -r requirements.lock >/dev/null 2>&1; then
  say "the lock file did not install on this Python (it pins for 3.12): installing requirements.txt instead"
  $INSTALL -r requirements.txt >/dev/null 2>&1 || say "requirements.txt failed too"
fi
$INSTALL -r requirements-dev.txt >/dev/null 2>&1 || say "requirements-dev.txt failed: ruff and the scanners may be missing"

# the browser for tests/e2e (E2E=1): the build the installed Playwright expects, with the system libraries it needs
$PY -m playwright install --with-deps chromium >/dev/null 2>&1 || $PY -m playwright install chromium >/dev/null 2>&1 || say "playwright could not fetch chromium: the browser tests will not run"

# the offline translation packages (Argos: Portuguese and Spanish to English) the translation tests use; a small download, skipped when the network refuses
$PY - <<'EOF' 2>/dev/null || echo "cloud set-up: the translation packages were not installed (no network to their index): two fifth-visit tests will say so"
import argostranslate.package as p
p.update_package_index()
wanted = {("pt", "en"), ("es", "en"), ("en", "pt"), ("en", "es")}
have = {(x.from_code, x.to_code) for x in p.get_installed_packages()}
for pkg in p.get_available_packages():
    if (pkg.from_code, pkg.to_code) in wanted - have:
        p.install_from_path(pkg.download())
print("cloud set-up: translation packages", sorted({(x.from_code, x.to_code) for x in p.get_installed_packages()}))
EOF

$PY - <<'EOF'
import importlib.util
missing = [n for n in ("pypdf", "playwright", "pytest", "xdist") if importlib.util.find_spec(n) is None]
print("cloud set-up: packages present" if not missing else f"cloud set-up: missing {missing}")
EOF
tesseract --version 2>/dev/null | head -1 || say "tesseract missing: the OCR tests will skip or fail"
say "use .venv-cloud/bin/python for every command"
