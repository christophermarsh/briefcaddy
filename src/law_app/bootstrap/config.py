"""Load startup inputs without constructing an app or starting services."""

import argparse
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from law_app.bootstrap.paths import staff_paths


def load_deployment(path: Path, *, defaults: dict[str, Any], modes: tuple[str, ...]) -> dict[str, Any]:
    """Read the selected installation's settings with explicit legacy defaults.

    Keep top-level key filtering, provider merging, missing-file behavior and
    validation exactly as the compatibility caller previously handled them.
    Nothing here chooses a global installation or enables a provider.
    """
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    out = defaults | {k: v for k, v in saved.items() if k in defaults}
    out["provider"] = defaults["provider"] | (saved.get("provider") or {})
    if out["mode"] not in modes:
        raise ValueError(f"deployment.json: mode must be one of {', '.join(modes)}.")
    return out


def staff_arguments(argv: list[str] | None = None, *, repo: Path,
                    environ: Mapping[str, str] | None = None) -> argparse.Namespace:
    """Resolve startup inputs; access checks and startup effects stay with callers."""
    paths = staff_paths(repo, os.environ if environ is None else environ)
    parser = argparse.ArgumentParser(description="I-485 review app")
    parser.add_argument("--data", default=paths["data"], type=Path)
    parser.add_argument("--deployment", default=paths["deployment"], type=Path,
                        help="deployment record for this installation (I485_DEPLOYMENT or deployment.json)")
    parser.add_argument("--field-map", default=paths["field_map"], type=Path)
    parser.add_argument("--template", default=paths["template"], type=Path)
    parser.add_argument("--policy", default=paths["policy"], type=Path)
    parser.add_argument("--port", default=8485, type=int)
    parser.add_argument("--portal", default=paths["portal"], type=Path,
                        help="the client portal's data folder, for clients not processed yet")
    parser.add_argument("--users", default=paths["users"], type=Path,
                        help="staff accounts (src/review/users.py); when the file exists, everyone signs in")
    parser.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to serve the office network (accounts required)")
    parser.add_argument("--hostname", action="append", default=[], help="a name staff use to reach it, e.g. review.office.lan (repeatable)")
    parser.add_argument("--secure-cookies", action="store_true", help="set when served over HTTPS (e.g. behind the firm's reverse proxy)")
    parser.add_argument("--behind-tls-proxy", action="store_true",
                        help="every request reaches this app through the firm's HTTPS proxy: HSTS on every response, Secure cookies")
    parser.add_argument("--trusted-proxy", action="append", default=[],
                        help="the address of the firm's proxy (e.g. 127.0.0.1); only its X-Forwarded-Proto and X-Forwarded-For "
                             "are believed (repeatable)")
    return parser.parse_args(argv)
