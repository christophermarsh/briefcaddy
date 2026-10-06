#!/usr/bin/env bash
# Known vulnerabilities in the exact versions we ship (requirements.lock), with pip-audit
# (https://pypi.org/project/pip-audit/, read 2026-10-02: "-r" audits a requirements file; "--no-deps"
# skips dependency resolution and "requires all requirements are pinned to an exact version", which a
# pip freeze is; exit code 0 = none found, 1 = "one or more known vulnerabilities were found").
# Register item dependency_scan (schemas/registers/maintenance.json): monthly, and before every release.
#
#   bash tools/check_deps.sh                 # the lock next to the code
#   bash tools/check_deps.sh other.lock      # another lock file
#
# Needs pip-audit (requirements-dev.txt): pip install -r requirements-dev.txt
# It asks the PyPI vulnerability service over the internet; only package names and versions are sent.
set -euo pipefail
cd "$(dirname "$0")/.."
LOCK="${1:-requirements.lock}"
PY="${PYTHON:-python3}"
if ! "$PY" -m pip_audit --version >/dev/null 2>&1; then
  echo "pip-audit is not installed: pip install -r requirements-dev.txt" >&2
  exit 2
fi
echo "Checking $LOCK for known vulnerabilities ($("$PY" -m pip_audit --version))"
# one line per finding: package, version, advisory id, the fixed versions (add --desc for each advisory's text);
# the exit code says whether any were found. --disable-pip: audit the pinned list as written, with no dry-run
# install (the lock holds stanza above argostranslate's "==" pin on purpose, see decisions.md 10/02/2026, and a
# dry-run install would refuse that).
"$PY" -m pip_audit -r "$LOCK" --no-deps --disable-pip
