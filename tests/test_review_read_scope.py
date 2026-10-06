"""Operation-local reuse still detects new bytes and contact changes next time."""
import json
import os
from pathlib import Path

import document_instances
import read_scope
from test_subject_attribution import saved, I94


def test_original_is_hashed_once_per_read_and_replacement_is_detected_next_time(tmp_path, monkeypatch):
    case = saved(tmp_path, [("i94.pdf", I94[0])])
    original = Path(json.loads((case / "meta.json").read_text())["source_folder"]) / "i94.pdf"
    actual = document_instances._source_hash
    calls = []

    def counted(path):
        calls.append(path)
        return actual(path)

    monkeypatch.setattr(document_instances, "_source_hash", counted)
    with read_scope.scope():
        assert not document_instances.views(case)[0]["stale"]
        with read_scope.scope():
            assert not document_instances.views(case)[0]["stale"]
        # A consumer modifying its plan must not alter another consumer's view.
        first = document_instances.views(case)
        first[0]["instances"][0]["state"] = "unresolved"
        assert document_instances.views(case)[0]["instances"][0]["state"] != "unresolved"
    assert calls == [original]
    stamp = original.stat()
    data = original.read_bytes()
    original.write_bytes(data[:-8] + b"Changed!")
    os.utime(original, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with read_scope.scope():
        assert document_instances.views(case)[0]["stale"]
    assert calls == [original, original]
