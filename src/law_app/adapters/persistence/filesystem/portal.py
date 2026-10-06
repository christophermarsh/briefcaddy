"""Legacy no-link paths and atomic JSON publication for transition filesystem users.

Callers own authority checks and locks. _atomic resolves _safe in this module;
queue_bridge retains compatibility aliases without forwarding global overrides.
"""

import json
import os
import re
import secrets
import stat
from pathlib import Path


def _safe(path):
    path = Path(path).absolute()
    for ancestor in (path, *path.parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        else:
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise ValueError("Portal queue path uses a link or reparse point")
    return path


def _atomic(path, value, *, stage_prefix=None):
    path = _safe(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stage_prefix is not None and (not isinstance(stage_prefix, str) or not re.fullmatch(r"portal-(?:job|queue)-[0-9a-f]{64}", stage_prefix)):
        raise ValueError("Invalid portal staging scope")
    part_name = (stage_prefix + "-" if stage_prefix else path.name + ".") + secrets.token_hex(8) + ".tmp"
    part = _safe(path.with_name(part_name))
    fd = os.open(part, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(part, path)
    if os.name != "nt":
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
