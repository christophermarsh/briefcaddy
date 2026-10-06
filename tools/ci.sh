#!/usr/bin/env bash
# The checks the owner runs before every release and once a month (register item ci_scan): one script, the same steps on a laptop and in
# .github/workflows/ci.yml (ready, not switched on: the owner decides whether to turn GitHub Actions on; docs/hardening.md "Scanning").
#
#   bash tools/ci.sh                      # every step; exit 0 only when every one passed
#   PYTHON=.venv/bin/python bash tools/ci.sh
#   CI_SKIP="pip-audit" bash tools/ci.sh  # leave steps out by name (space-separated), e.g. on a computer with no internet
#
# Steps (each says ok or FAILED; the script goes on to the next and fails at the end):
#   pip-audit    known vulnerabilities in the exact versions shipped (tools/check_deps.sh; needs requirements-dev.txt and the internet:
#                only package names and versions are sent)
#   ruff         undefined names, unused imports and syntax errors (ruff F,E9, ruff is pinned in requirements.lock)
#   secrets      private keys, providers' tokens and secrets, a long token or key or any password literal written into a file, real clients' names (tools/secret_scan.py, a plain pattern list, no package; it scans the files git holds, tracked or staged, never a scratch file beside the checkout)
#   dictionary   the data dictionary matches the code (tools/data_dictionary.py --check)
#   register     the upkeep register (schemas/registers/maintenance.json) re-dumps byte for byte
#   public       the public pages match the code (tools/public_pages.py --check)
#   security-md  SECURITY.md is what the code makes (tools/security_md.py --check)
#   unit        the unit tests (the browser tests need a browser: E2E=1, run apart)
set -uo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
SKIP=" ${CI_SKIP:-} "
failed=()

step() {
  local name="$1"
  shift
  if [[ "$SKIP" == *" $name "* ]]; then
    echo "== $name: left out (CI_SKIP)"
    return
  fi
  echo "== $name"
  if "$@"; then
    echo "   ok: $name"
  else
    echo "   FAILED: $name"
    failed+=("$name")
  fi
}

register_redumps() {
  "$PY" - <<'PYEOF'
import json, sys
sys.path.insert(0, "src")
import schema_path
path = schema_path.path("register", "maintenance")
text = open(path, encoding="utf-8").read()
sys.exit(0 if json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n" == text else 1)
PYEOF
}

step pip-audit env PYTHON="$PY" bash tools/check_deps.sh
step ruff "$PY" -m ruff check --select F,E9 src tools tests
step secrets "$PY" tools/secret_scan.py
step dictionary "$PY" tools/data_dictionary.py --check
step register register_redumps
step public "$PY" tools/public_pages.py --check
step security-md "$PY" tools/security_md.py --check
step unit "$PY" -m pytest -q -p no:cacheprovider -n "${CI_WORKERS:-8}" tests

if ((${#failed[@]})); then
  echo "FAILED: ${failed[*]}"
  exit 1
fi
echo "Every step passed."
