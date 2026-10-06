"""One read of each blank form per process.

A packet fills ten forms and every fill used to read its template from disk again: parse the file, clone the whole document into a writer, read the
fields' lengths from a second clone, read the field names from a third reader (the second flake pass, docs/decisions.md: pypdf's
clone_document_from_reader was 12 seconds of a 24-second family packet, 30 clones for ten forms). This keeps the parsed template in memory, keyed by
its path, its change time and its size, and everything the product derives from it (the boxes' /MaxLen, the field table) is worked out once.

    writer(path)       a new PdfWriter holding a copy of the template, ready to fill (the template itself is never written to)
    max_lengths(path)  {full field name: /MaxLen}, a copy the caller may change
    fields(path)       {full field name: {"/FT": type, "/TU": tooltip, "/Opt": options}} as plain text, worked out under the template's lock: shared, read it, never change it

Safe where the review app serves several requests at once:
- every template has its own lock, held while that template is parsed or cloned; two requests for different templates never wait for each other;
- the table of templates has a lock of its own, held only to look an entry up or add an empty one (a dictionary operation, never a read of a file);
- a template is never changed once read: a fill clones it into a writer of the caller's own.

The table holds at most MAX_TEMPLATES (16) templates, the one used longest ago going first. The product ships 45 form templates and a packet uses 3 to 12
of them (the family packet 8). A parsed template holds about 14 MB of memory on average (measured on seven: the I-485 33 MB, the G-28 4.5 MB), so a full
table is about 230 MB beside the review app's 700 to 760 MB at 1,800 cases (docs/scale.md); a firm that fills a few kinds of form a day holds far fewer. A template file replaced on disk (a new edition) has a new change time: its next use reads the new file,
and the old one is dropped; no restart. The key is the path as given, so a form's own file is the only file its entry can serve (the edition a field
map names stays checked where it always was, in fill/where.py).
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, IndirectObject

MAX_TEMPLATES = 16

_table_lock = threading.Lock()
_table: OrderedDict[str, "_Template"] = OrderedDict()  # path -> its template, least recently used first


def _collect_max_lengths(root: DictionaryObject) -> dict[str, int]:
    """Every field's /MaxLen by full field name, walking the raw AcroForm tree (get_fields() does not reliably surface /MaxLen: fill/fill_pdf.py)."""
    acroform = root.get("/AcroForm")
    if acroform is None:
        return {}

    max_lengths: dict[str, int] = {}

    def walk(fields, path: str) -> None:
        for ref in fields:
            obj = ref.get_object() if isinstance(ref, IndirectObject) else ref
            if not isinstance(obj, DictionaryObject):
                continue
            name_part = obj.get("/T")
            full_name = f"{path}.{name_part}" if path and name_part else (name_part or path)
            if "/MaxLen" in obj and full_name:
                max_lengths[full_name] = int(obj["/MaxLen"])
            kids = obj.get("/Kids")
            if kids:
                walk(kids, full_name)

    walk(acroform.get("/Fields", []), "")
    return max_lengths


class _Template:
    """One template file as it was when read: the parsed document and what is worked out from it, each made on first use."""

    def __init__(self, path: str, stamp: tuple[int, int]):
        self.path = path
        self.stamp = stamp  # (change time in ns, size in bytes) of the file this entry read
        self.lock = threading.Lock()
        self._reader: PdfReader | None = None
        self._max_lengths: dict[str, int] | None = None
        self._fields: dict[str, dict[str, Any]] | None = None

    def _read(self) -> PdfReader:
        if self._reader is None:
            reader = PdfReader(self.path)
            if reader.is_encrypted:  # the agency's forms are locked against editing, not opened: the empty password
                reader.decrypt("")
            self._reader = reader
        return self._reader

    def writer(self) -> PdfWriter:
        with self.lock:
            return PdfWriter(clone_from=self._read())

    def max_lengths(self) -> dict[str, int]:
        with self.lock:
            if self._max_lengths is None:
                self._max_lengths = _collect_max_lengths(self._read().trailer["/Root"])
            return dict(self._max_lengths)

    def fields(self) -> dict[str, dict[str, Any]]:
        """Plain text only: pypdf's own field objects read the shared file again whenever one is looked into, and a thread doing that while another
        copies the template would spoil both. Everything the product reads of a field is resolved here, under the lock."""
        with self.lock:
            if self._fields is None:
                out: dict[str, dict[str, Any]] = {}
                for name, field in (self._read().get_fields() or {}).items():
                    entry: dict[str, Any] = {"/TU": str(field.get("/TU") or "")}
                    if field.get("/FT") is not None:
                        entry["/FT"] = str(field.get("/FT"))
                    opts = field.get("/Opt")
                    if opts:
                        entry["/Opt"] = [[str(x) for x in o] if isinstance(o, list) else str(o) for o in opts]
                    out[name] = entry
                self._fields = out
            return self._fields


def _entry(path: str | Path) -> _Template:
    key = os.fspath(path)
    info = os.stat(key)  # a template that is not there fails here, as opening it always did
    stamp = (info.st_mtime_ns, info.st_size)
    with _table_lock:
        entry = _table.get(key)
        if entry is None or entry.stamp != stamp:  # new, or the file was replaced (a new edition): the old reading is dropped
            entry = _table[key] = _Template(key, stamp)
        _table.move_to_end(key)
        while len(_table) > MAX_TEMPLATES:
            _table.popitem(last=False)
        return entry


def writer(path: str | Path) -> PdfWriter:
    """A new writer holding a copy of the template: the same document PdfWriter(clone_from=path) gives, without reading the file again."""
    return _entry(path).writer()


def max_lengths(path: str | Path) -> dict[str, int]:
    return _entry(path).max_lengths()


def fields(path: str | Path) -> dict[str, dict[str, Any]]:
    return _entry(path).fields()


def clear() -> None:
    """Forgets every template (a test of a cold start; the next use reads the files again)."""
    with _table_lock:
        _table.clear()
