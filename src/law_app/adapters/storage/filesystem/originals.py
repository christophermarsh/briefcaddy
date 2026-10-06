"""Disposable exclusive-root original store, separate from installed storage.

Root and all descendants must have no concurrent untrusted filesystem writers.
Path checks refuse observed links/reparse points; they are not a Windows ACL or
hostile concurrent path-replacement sandbox. Interrupted staging files remain.
"""
import hashlib
import os
from pathlib import Path
import stat
from uuid import uuid4

from law_app.ports.originals import (
    OriginalConflict, OriginalRevision, OriginalUnavailable, original_key,
    original_locator, validate_revision,
)


class FilesystemOriginals:
    MAX_BYTES = 16 * 1024 * 1024

    def __init__(self, root, scope, *, disposable=False):
        if disposable is not True:
            raise ValueError("Explicit disposable exclusive root required")
        self.root = Path(root).absolute()
        self.scope = scope
        original_key(scope, scope.firm_id, scope.enrollment_id)
        if self.root.resolve() != self.root:
            raise OriginalUnavailable("Original root unavailable")
        self._safe(self.root, directory=True)

    @staticmethod
    def _safe(path, *, directory=False):
        info = path.lstat()
        if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
                or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
                or not directory and info.st_nlink != 1):
            raise OriginalUnavailable("Original unavailable")
        return info

    def _path(self, intake, revision, *, create=False):
        self._safe(self.root, directory=True)
        current = self.root
        for key in original_key(self.scope, intake, revision):
            current /= key
            if create:
                current.mkdir(exist_ok=True)
            self._safe(current, directory=True)
        return current / 'original.bin'

    def _bytes(self, path):
        before = self._safe(path)
        with path.open('rb') as source:
            opened = os.fstat(source.fileno())
            if (opened.st_ino, opened.st_dev) != (before.st_ino, before.st_dev):
                raise OriginalUnavailable("Original unavailable")
            value = source.read(self.MAX_BYTES + 1)
        after = self._safe(path)
        if ((after.st_ino, after.st_dev, after.st_size, after.st_mtime_ns)
                != (before.st_ino, before.st_dev, before.st_size, before.st_mtime_ns)
                or len(value) > self.MAX_BYTES or len(value) != before.st_size):
            raise OriginalUnavailable("Original unavailable")
        return value

    def publish(self, intake_id, revision_id, data):
        if type(data) is not bytes or len(data) > self.MAX_BYTES:
            raise ValueError("Bounded original bytes required")
        descriptor = OriginalRevision(self.scope, intake_id, revision_id,
            hashlib.sha256(data).hexdigest(), len(data), original_locator(self.scope, intake_id, revision_id))
        path = self._path(intake_id, revision_id, create=True)
        if path.exists() or path.is_symlink():
            if self._bytes(path) != data:
                raise OriginalConflict("Immutable original identity conflicts")
            return descriptor
        stage = path.parent / ('staging-' + uuid4().hex)
        with stage.open('xb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if self._bytes(stage) != data:
            raise OriginalUnavailable("Original unavailable")
        self._path(intake_id, revision_id)
        try:
            # Atomic no-replace publication. Never rename over an existing key.
            os.link(stage, path)
        except FileExistsError:
            if self._bytes(path) != data:
                raise OriginalConflict("Immutable original identity conflicts") from None
        stage.unlink()
        self.verified_read(descriptor)
        return descriptor

    def verified_read(self, revision):
        validate_revision(revision, self.scope, self.MAX_BYTES)
        try:
            value = self._bytes(self._path(revision.intake_id, revision.revision_id))
        except OSError:
            raise OriginalUnavailable("Original unavailable") from None
        if len(value) != revision.byte_length or hashlib.sha256(value).hexdigest() != revision.sha256:
            raise OriginalUnavailable("Original unavailable")
        return value
