"""Startup path defaults; no filesystem mutation or authority decisions."""

from collections.abc import Mapping
from pathlib import Path

import schema_path


def deployment_path(repo: Path, environ: Mapping[str, str]) -> Path:
    """Preserve deployment's empty-value fallback and relative-path semantics."""
    return Path(environ.get("I485_DEPLOYMENT") or repo / "deployment.json")


def staff_paths(repo: Path, environ: Mapping[str, str]) -> dict[str, Path]:
    """Keep defaults relative to the entrypoint's installation, not this package.

    Explicit CLI paths are handled by argparse and retain their existing relative
    path semantics. An empty PORTAL_DATA also keeps its existing Path('') meaning.
    """
    schemas = schema_path.schemas_in(repo)
    return {
        "data": repo / "data" / "clients",
        "deployment": deployment_path(repo, environ),
        "field_map": schema_path.path("field_map", "i485", schemas),
        "template": schema_path.path("template", "i485", schemas),
        "policy": schema_path.path("law", "policy_sijs", schemas),
        "portal": Path(environ.get("PORTAL_DATA", repo / "data" / "portal")),
        "users": repo / "data" / "review_users.json",
    }
