"""The templates are read once per process (src/fill/template_cache.py): the same bytes as reading each file every time, a replaced file is
read again, the table is bounded, and several requests at once do not wait for each other or spoil a fill. The last test is the packet's
time (brief P1: a family packet in seconds)."""

from __future__ import annotations

import io
import os
import shutil
import threading
import time
from datetime import date

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, IndirectObject

import packet
import schema_path
from fill import fill_pdf, template_cache
from test_family import _case

FORMS = ("g28", "i765", "i130", "i864")


def _template(name: str):
    return schema_path.path("template", name)


def _bytes(writer: PdfWriter) -> bytes:
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _limits_by_a_fresh_clone(path) -> dict[str, int]:
    """What field_max_lengths gave before the cache: a clone of the file read just now, its AcroForm walked."""
    acroform = PdfWriter(clone_from=str(path))._root_object.get("/AcroForm")
    out: dict[str, int] = {}

    def walk(fields, prefix: str) -> None:
        for ref in fields:
            obj = ref.get_object() if isinstance(ref, IndirectObject) else ref
            if not isinstance(obj, DictionaryObject):
                continue
            part = obj.get("/T")
            full = f"{prefix}.{part}" if prefix and part else (part or prefix)
            if "/MaxLen" in obj and full:
                out[full] = int(obj["/MaxLen"])
            if obj.get("/Kids"):
                walk(obj["/Kids"], full)

    walk(acroform.get("/Fields", []) if acroform is not None else [], "")
    return out


def test_the_cache_gives_what_reading_the_file_gives():
    for name in FORMS:
        path = _template(name)
        template_cache.clear()
        cold = template_cache.writer(path)
        warm = template_cache.writer(path)  # the second copy comes from the reading kept in memory
        assert _bytes(cold) == _bytes(warm) == _bytes(PdfWriter(clone_from=str(path)))
        assert template_cache.max_lengths(path) == _limits_by_a_fresh_clone(path) and template_cache.max_lengths(path)
        assert list(template_cache.fields(path)) == list((PdfWriter(clone_from=str(path)).get_fields() or {}))


def test_the_fields_are_plain_text_and_say_what_the_file_says():
    """Plain text, so reading them never goes back to the shared file (a pypdf field object does, and a thread doing that while another copies the template would spoil both)."""
    from pypdf import PdfReader

    for name in ("i765", "i130", "n400"):
        path = _template(name)
        read = PdfReader(str(path)).get_fields() or {}
        got = template_cache.fields(path)
        assert list(got) == list(read)
        for key, field in read.items():
            assert got[key]["/TU"] == str(field.get("/TU") or "") and got[key].get("/FT") == (str(field["/FT"]) if field.get("/FT") is not None else None)
            if field.get("/FT") == "/Ch" and field.get("/Opt"):
                assert [str(o[0] if isinstance(o, list) else o) for o in got[key]["/Opt"]] == [str(o[0] if isinstance(o, list) else o) for o in field["/Opt"]]
        assert all(type(v) in (str, list) for entry in got.values() for v in entry.values())


def test_a_copy_of_the_limits_is_the_callers_own():
    path = _template("g28")
    template_cache.max_lengths(path).clear()
    assert template_cache.max_lengths(path)


def test_a_fill_is_the_same_cold_and_warm(tmp_path):
    path = _template("i765")
    values = {name: "A" for name in list(template_cache.max_lengths(path))[:5]}
    template_cache.clear()
    fill_pdf(path, values, tmp_path / "cold.pdf")
    fill_pdf(path, values, tmp_path / "warm.pdf")
    assert (tmp_path / "cold.pdf").read_bytes() == (tmp_path / "warm.pdf").read_bytes()


def test_a_template_replaced_on_disk_is_read_again_without_a_restart(tmp_path):
    copy = tmp_path / "template.pdf"
    shutil.copyfile(_template("g28"), copy)
    g28 = template_cache.max_lengths(copy)
    first = _bytes(template_cache.writer(copy))
    shutil.copyfile(_template("i765"), copy)  # a new edition: another file in the same place
    assert template_cache.max_lengths(copy) == _limits_by_a_fresh_clone(copy) != g28
    assert _bytes(template_cache.writer(copy)) == _bytes(PdfWriter(clone_from=str(copy))) != first
    stat = copy.stat()  # the same size, a later change time: also read again
    again = tmp_path / "again.pdf"
    shutil.copyfile(_template("g28"), again)
    template_cache.fields(again)
    entry = template_cache._table[str(again)]
    os.utime(again, ns=(stat.st_atime_ns, entry.stamp[0] + 1_000_000_000))
    template_cache.fields(again)
    assert template_cache._table[str(again)] is not entry


def test_two_paths_never_share_a_reading(tmp_path):
    other = tmp_path / "g28.pdf"
    shutil.copyfile(_template("g28"), other)  # the same bytes under another name: its own entry (the key is the path)
    assert template_cache._entry(other) is not template_cache._entry(_template("g28"))


def test_the_table_is_bounded_and_drops_the_one_used_longest_ago(monkeypatch):
    template_cache.clear()
    monkeypatch.setattr(template_cache, "MAX_TEMPLATES", 2)
    for name in ("g28", "i765", "g28", "i130"):  # g28 is used again, so i765 goes when i130 comes
        template_cache.max_lengths(_template(name))
    assert list(template_cache._table) == [str(_template("g28")), str(_template("i130"))]
    assert template_cache.MAX_TEMPLATES == 2 and len(template_cache._table) == 2


def test_the_bound_is_larger_than_what_a_packet_uses():
    assert template_cache.MAX_TEMPLATES >= 12  # a packet fills 3 to 12 forms


def test_each_template_has_its_own_lock_and_one_does_not_hold_up_another():
    a, b = _template("g28"), _template("i765")
    template_cache.clear()
    first, second = template_cache._entry(a), template_cache._entry(b)
    assert first.lock is not second.lock
    done: list[bytes] = []
    with first.lock:  # a request is busy with the G-28 ...
        worker = threading.Thread(target=lambda: done.append(_bytes(template_cache.writer(b))))
        worker.start()
        worker.join(timeout=60)  # ... and the I-765 is read and copied meanwhile
        assert not worker.is_alive() and done


def test_requests_at_once_each_get_the_same_fill_as_one_at_a_time(tmp_path):
    values = {name: "B" for name in list(template_cache.max_lengths(_template("i130")))[:8]}
    serial = {}
    for name in FORMS:
        names = {k: v for k, v in values.items() if k in template_cache.max_lengths(_template(name))}
        fill_pdf(_template(name), names, tmp_path / f"{name}.serial.pdf")
        serial[name] = (tmp_path / f"{name}.serial.pdf").read_bytes()
    template_cache.clear()  # cold, so the threads race to read each template as well
    errors: list[BaseException] = []

    def work(n: int, name: str) -> None:
        try:
            names = {k: v for k, v in values.items() if k in template_cache.max_lengths(_template(name))}
            fill_pdf(_template(name), names, tmp_path / f"{name}.{n}.pdf")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n, name)) for n in range(4) for name in FORMS]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)
    assert not errors
    for n in range(4):
        for name in FORMS:
            assert (tmp_path / f"{name}.{n}.pdf").read_bytes() == serial[name]


def _build_family(tmp_path, monkeypatch):
    monkeypatch.setattr(packet, "_today", lambda: date(2026, 10, 1))
    tmp_path.mkdir(parents=True, exist_ok=True)
    d = _case(tmp_path)
    row = {"summary": {"name": "ANA SOUZA"}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}
    started = time.monotonic()
    manifest = packet.build(d, row, "Sam", packet.load_filing("family"))
    return d, manifest, time.monotonic() - started


def _outputs(d) -> dict[str, bytes]:
    return {f.name: f.read_bytes() for f in sorted(d.glob("*.pdf"))}


def test_a_packet_built_twice_is_the_same_cold_and_warm(tmp_path, monkeypatch):
    template_cache.clear()
    cold_dir, cold, _ = _build_family(tmp_path / "cold", monkeypatch)  # every template read from its file
    warm_dir, warm, _ = _build_family(tmp_path / "warm", monkeypatch)  # every template from memory
    assert _outputs(cold_dir) == _outputs(warm_dir) and len(_outputs(cold_dir)) > 10  # every filled form and the packet itself, byte for byte
    assert cold["sha256"] == warm["sha256"] and cold["pages"] == warm["pages"] and b"/CreationDate" not in _outputs(cold_dir)["packet_family.pdf"]  # the writer stamps no date


def test_a_family_packet_is_built_in_seconds(tmp_path, monkeypatch):
    """The build a paralegal makes thirty times a day, in this process, templates already read. Measured on this machine (4 cores, a quiet disk): 15.0 s
    before the cache, 6.0 s after; the budget is 12 s, between the two, so the old way fails it and a slower machine has room
    (docs/scale.md, "A packet build", has the figures and what the firm's Windows disk adds). A wall-clock measurement says nothing while the machine is busy with other
    work, so it is skipped, with its reason, when the load per core is above 2."""
    try:
        per_core = os.getloadavg()[0] / (os.cpu_count() or 1)
    except (OSError, AttributeError):
        per_core = 0.0
    if per_core > 2:
        pytest.skip(f"the machine is busy (load {per_core:.1f} per core, above 2): a timing measured now would measure the other work")
    _build_family(tmp_path / "first", monkeypatch)  # reads the templates and the lists that are kept for the process
    _d, _manifest, seconds = _build_family(tmp_path / "second", monkeypatch)
    budget = 12 * max(1.0, min(4.0, per_core))  # the load per core, between 1 and 4, stretches it (the machine's own tests at -n 4 share its cores)
    assert seconds < budget, f"a family packet took {seconds:.1f} s to build (the budget is {budget:.0f} s for this machine's load; 12 s on a quiet one)"
