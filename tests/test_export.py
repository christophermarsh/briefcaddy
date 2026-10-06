"""The firm's exit: tools/export_firm.py hands one client, or the whole installation, back as a dated zip.
Everything here is made up (Ana Clara Exemplo Souza, Maria Exemplo); no real data folder is read."""

import hashlib
import json
import os
import re
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import export_firm  # noqa: E402

HASH = "ab12" * 16
SALT = "cd34" * 8
TOKEN_HASH = "ef56" * 16


def make_install(root: Path) -> dict[str, Path]:
    """A made-up installation: two clients in the review app's folder, one of them with a portal folder, and the firm's files."""
    data, portal = root / "data" / "clients", root / "data" / "portal"
    ana = data / "ana-exemplo"
    (ana).mkdir(parents=True)
    for name, text in {"fact_graph.json": '{"facts": {"applicant.family_name": "SOUZA"}}', "fact_graph_raw.json": "{}", "decisions.json": '{"d": 1}',
                       "packet_i360.json": "{}", "flag_report.txt": "nothing"}.items():
        (ana / name).write_text(text, encoding="utf-8")
    (ana / "packet.pdf").write_bytes(b"%PDF-1.4 made up packet")
    (ana / "half.json.123.tmp").write_text("a write in progress", encoding="utf-8")
    (data / "maria-exemplo").mkdir()
    (data / "maria-exemplo" / "fact_graph.json").write_text("{}", encoding="utf-8")

    pa = portal / "clients" / "ana-exemplo"
    (pa / "uploads").mkdir(parents=True)
    (pa / "profile.json").write_text(json.dumps({"id": "ana-exemplo", "name": "Ana Clara Exemplo Souza", "email": "ana@example.com"}), encoding="utf-8")
    (pa / "answers.json").write_text("{}", encoding="utf-8")
    (pa / "events.jsonl").write_text('{"event": "signed_in"}\n', encoding="utf-8")
    (pa / "uploads" / "passport-0123456789abcdef.pdf").write_bytes(b"%PDF-1.4 made up passport")
    (ana / "meta.json").write_text(json.dumps({"client_id": "ana-exemplo", "source_folder": str(pa / "uploads")}), encoding="utf-8")
    (portal / "auth.json").write_text(json.dumps({"links": {TOKEN_HASH: {}}, "sessions": {TOKEN_HASH: {}}}), encoding="utf-8")
    (portal / "outbox.jsonl").write_text('{"body": "https://portal.example/?t=WORKING-LINK"}\n', encoding="utf-8")

    users = root / "data" / "review_users.json"
    users.write_text(json.dumps({
        "users": {"attorney@example.com": {"name": "Ana Attorney", "role": "attorney", "active": True, "created_at": "2026-01-01", "salt": SALT, "hash": HASH,
                                           "must_change": False, "failures": 2, "locked_until": None}},
        "sessions": {TOKEN_HASH: {"email": "attorney@example.com", "expires": "2026-12-31"}}}), encoding="utf-8")
    (root / "data" / "review_users_access.jsonl").write_text('{"event": "sign_in", "email": "attorney@example.com"}\n', encoding="utf-8")
    (root / "data" / "settings.json").write_text('{"fees": {}}', encoding="utf-8")
    (root / "data" / "maintenance_log.json").write_text('{"visa_bulletin": {"last_checked": "2026-10-01"}}', encoding="utf-8")
    db = sqlite3.connect(root / "data" / "learning.db")
    db.execute("create table corrections (id integer primary key, note text)")
    db.execute("insert into corrections (note) values ('made-up correction')")
    db.commit()
    db.close()
    return {"data": data, "portal": portal, "users": users, "root": root / "data"}


@pytest.fixture
def install(tmp_path):
    return make_install(tmp_path / "install")


def run(install, tmp_path, *extra, capsys=None):
    argv = ["--data", str(install["data"]), "--portal", str(install["portal"]), "--users", str(install["users"]),
            "--settings", str(install["root"] / "settings.json"), "--maintenance-log", str(install["root"] / "maintenance_log.json"), *extra]
    if "--out" not in extra:
        argv += ["--out", str(tmp_path / "out")]
    return export_firm.main(argv)


def only_zip(tmp_path) -> Path:
    zips = list((tmp_path / "out").glob("*.zip"))
    assert len(zips) == 1
    return zips[0]


def names(zip_path: Path) -> set[str]:
    with zipfile.ZipFile(zip_path) as zf:
        return set(zf.namelist())


def snapshot(root: Path) -> dict[str, tuple[int, str]]:
    return {p.relative_to(root).as_posix(): (p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(root.rglob("*")) if p.is_file()}


def test_one_client_gets_their_folder_and_their_portal_data_and_nobody_elses(install, tmp_path, capsys):
    assert run(install, tmp_path, "--client", "ana-exemplo") == 0
    zip_path = only_zip(tmp_path)
    got = names(zip_path)
    assert {"clients/ana-exemplo/fact_graph.json", "clients/ana-exemplo/decisions.json", "clients/ana-exemplo/packet.pdf", "clients/ana-exemplo/packet_i360.json",
            "portal/ana-exemplo/profile.json", "portal/ana-exemplo/events.jsonl", "portal/ana-exemplo/uploads/passport-0123456789abcdef.pdf",
            "manifest.json", "MANIFEST.md"} <= got
    assert not any("maria-exemplo" in n for n in got)
    assert not any(n.startswith("firm/") for n in got), "a single client's export carries no firm files unless asked"
    assert not any(n.endswith(".tmp") for n in got), "a write in progress is not exported"
    assert re.fullmatch(r"i485-export-ana-exemplo-\d{4}-\d{2}-\d{2}\.zip", zip_path.name)


def test_the_manifest_lists_every_file_with_a_true_checksum_and_a_line_saying_what_it_is(install, tmp_path):
    assert run(install, tmp_path, "--all") == 0
    zip_path = only_zip(tmp_path)
    with zipfile.ZipFile(zip_path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        listed = {f["path"]: f for f in manifest["files"]}
        assert set(listed) == set(zf.namelist()) - {"manifest.json", "MANIFEST.md"}
        for path, f in listed.items():
            body = zf.read(path)
            assert f["bytes"] == len(body) and f["sha256"] == hashlib.sha256(body).hexdigest(), path
            assert f["what"] and f["format"], path
        assert listed["clients/ana-exemplo/packet.pdf"]["format"] == "PDF"
        assert listed["portal/ana-exemplo/events.jsonl"]["format"].startswith("JSON Lines")
        assert listed["clients/ana-exemplo/decisions.json"]["what"].startswith("Every review decision")
        md = zf.read("MANIFEST.md").decode("utf-8")
    assert all(path in md for path in listed), "MANIFEST.md names every file"
    assert manifest["file_count"] == len(listed)


def test_the_whole_installation_includes_the_firms_files_without_passwords_or_tokens(install, tmp_path):
    assert run(install, tmp_path, "--all") == 0
    zip_path = only_zip(tmp_path)
    got = names(zip_path)
    assert {"firm/settings.json", "firm/maintenance_log.json", "firm/users.json", "firm/review_users_access.jsonl", "firm/learning.db",
            "clients/maria-exemplo/fact_graph.json", "clients/ana-exemplo/fact_graph.json"} <= got
    with zipfile.ZipFile(zip_path) as zf:
        accounts = json.loads(zf.read("firm/users.json"))
        assert accounts == {"users": {"attorney@example.com": {"name": "Ana Attorney", "role": "attorney", "active": True, "created_at": "2026-01-01"}}}
        everything = b"".join(zf.read(n) for n in zf.namelist())
    for secret in (HASH, SALT, TOKEN_HASH, "WORKING-LINK"):
        assert secret.encode() not in everything, f"{secret} must never leave in an export"
    assert not any(n.endswith(("auth.json", "outbox.jsonl", "deployment.json")) for n in got)


def test_the_learning_store_is_a_readable_copy(install, tmp_path):
    assert run(install, tmp_path, "--all") == 0
    with zipfile.ZipFile(only_zip(tmp_path)) as zf:
        copy = tmp_path / "copy.db"
        copy.write_bytes(zf.read("firm/learning.db"))
    assert sqlite3.connect(copy).execute("select note from corrections").fetchall() == [("made-up correction",)]


def test_firm_files_can_be_added_to_one_clients_export(install, tmp_path):
    assert run(install, tmp_path, "--client", "ana-exemplo", "--firm-files") == 0
    assert "firm/users.json" in names(only_zip(tmp_path))


def test_it_prints_the_file_count_and_the_zips_sha256(install, tmp_path, capsys):
    assert run(install, tmp_path, "--client", "ana-exemplo") == 0
    out = capsys.readouterr().out
    sha = re.search(r"sha256: ([0-9a-f]{64})", out).group(1)
    assert sha == hashlib.sha256(only_zip(tmp_path).read_bytes()).hexdigest()
    count = int(re.search(r"Files: (\d+)", out).group(1))
    assert count == len(names(only_zip(tmp_path)))


def test_it_only_reads(install, tmp_path):
    before = snapshot(install["root"].parent)
    assert run(install, tmp_path, "--all") == 0
    assert snapshot(install["root"].parent) == before, "nothing in the installation was created, changed or deleted"


@pytest.mark.parametrize("inside", ["data", "portal", "root"])
def test_it_refuses_to_write_inside_the_data(install, tmp_path, capsys, inside):
    target = {"data": install["data"], "portal": install["portal"], "root": install["root"]}[inside] / "exports"
    assert run(install, tmp_path, "--all", "--out", str(target)) == 2
    assert "Refusing to write" in capsys.readouterr().err
    assert not target.exists()


@pytest.mark.parametrize("which", ["users_folder", "data_parent", "portal_parent"])
def test_a_single_client_export_still_refuses_the_folders_that_hold_the_accounts_and_the_data(install, tmp_path, capsys, which):
    # review_users.json and deployment.json live beside the data: even with --client and no --firm-files the zip must not go there
    folder = {"users_folder": install["users"].parent, "data_parent": install["data"].parent, "portal_parent": install["portal"].parent}[which]
    assert run(install, tmp_path, "--client", "ana-exemplo", "--out", str(folder / "exports")) == 2
    assert "Refusing to write" in capsys.readouterr().err
    assert not (folder / "exports").exists()


def test_a_source_folder_shared_with_another_client_is_not_exported_with_one_client(install, tmp_path, capsys):
    shared = tmp_path / "install" / "clients" / "shared-source"
    shared.mkdir(parents=True)
    (shared / "maria-passport.pdf").write_bytes(b"%PDF-1.4 made up, Maria Exemplo's")
    for cid in ("ana-exemplo", "maria-exemplo"):
        (install["data"] / cid / "meta.json").write_text(json.dumps({"client_id": cid, "source_folder": str(shared)}), encoding="utf-8")
    assert run(install, tmp_path, "--client", "ana-exemplo") == 0
    assert not any(n.startswith("documents/") for n in names(only_zip(tmp_path)))
    assert "also named by another client" in capsys.readouterr().out
    (tmp_path / "out" / only_zip(tmp_path).name).unlink()
    assert run(install, tmp_path, "--all") == 0
    assert "documents/ana-exemplo/maria-passport.pdf" in names(only_zip(tmp_path))


def test_it_refuses_a_zip_path_inside_the_data_too(install, tmp_path):
    assert run(install, tmp_path, "--all", "--out", str(install["data"] / "ana-exemplo" / "mine.zip")) == 2
    assert not (install["data"] / "ana-exemplo" / "mine.zip").exists()


def test_it_never_overwrites_an_earlier_export(install, tmp_path, capsys):
    assert run(install, tmp_path, "--client", "ana-exemplo") == 0
    first = only_zip(tmp_path).read_bytes()
    assert run(install, tmp_path, "--client", "ana-exemplo") == 2
    assert "already exists" in capsys.readouterr().err
    assert only_zip(tmp_path).read_bytes() == first
    assert not list((tmp_path / "out").glob("*.part"))


@pytest.mark.parametrize("client", ["nobody-exemplo", "../clients", "..", "a/b", ""])
def test_an_unknown_or_unsafe_client_id_exports_nothing(install, tmp_path, capsys, client):
    assert run(install, tmp_path, "--client", client) == 2
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())


def test_a_portal_only_client_can_be_exported(install, tmp_path):
    store = install["portal"] / "clients" / "pilot-nova"
    store.mkdir()
    (store / "profile.json").write_text('{"id": "pilot-nova"}', encoding="utf-8")
    assert run(install, tmp_path, "--client", "pilot-nova") == 0
    assert names(only_zip(tmp_path)) >= {"portal/pilot-nova/profile.json"}


def test_a_source_folder_outside_the_client_folders_comes_along(install, tmp_path):
    source = tmp_path / "install" / "clients" / "ana-exemplo" / "source"
    source.mkdir(parents=True)
    (source / "passport.pdf").write_bytes(b"%PDF-1.4 made up")
    meta = install["data"] / "ana-exemplo" / "meta.json"
    meta.write_text(json.dumps({"client_id": "ana-exemplo", "source_folder": str(source)}), encoding="utf-8")
    assert run(install, tmp_path, "--client", "ana-exemplo") == 0
    assert "documents/ana-exemplo/passport.pdf" in names(only_zip(tmp_path))


def test_a_source_folder_on_another_machine_is_a_warning_not_a_failure(install, tmp_path, capsys):
    meta = install["data"] / "ana-exemplo" / "meta.json"
    meta.write_text(json.dumps({"client_id": "ana-exemplo", "source_folder": "/somewhere/else/ana-exemplo/source"}), encoding="utf-8")
    assert run(install, tmp_path, "--client", "ana-exemplo") == 0
    assert "not on this machine" in capsys.readouterr().out
    with zipfile.ZipFile(only_zip(tmp_path)) as zf:
        assert json.loads(zf.read("manifest.json"))["warnings"]


@pytest.mark.skipif(not hasattr(os, "symlink") or sys.platform == "win32", reason="needs symbolic links")
def test_a_link_is_not_followed(install, tmp_path):
    secret = tmp_path / "elsewhere.txt"
    secret.write_text("outside the client's folder", encoding="utf-8")
    os.symlink(secret, install["data"] / "ana-exemplo" / "link.txt")
    assert run(install, tmp_path, "--client", "ana-exemplo") == 0
    zip_path = only_zip(tmp_path)
    assert "clients/ana-exemplo/link.txt" not in names(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        assert any("link.txt" in line for line in json.loads(zf.read("manifest.json"))["left_out"])


def test_the_made_up_world_exports_whole(tmp_path):
    """The end-to-end tests' made-up installation (tests/e2e/world.py): the real shapes of the folders."""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "e2e"))
    import world

    paths = world.build(tmp_path / "world")
    code = export_firm.main(["--all", "--data", str(paths["clients"]), "--portal", str(paths["portal"]), "--users", str(paths["users"]),
                             "--settings", str(tmp_path / "none.json"), "--maintenance-log", str(tmp_path / "none2.json"), "--out", str(tmp_path / "out")])
    assert code == 0
    got = names(only_zip(tmp_path))
    assert "clients/demo-ana/fact_graph.json" in got and "portal/demo-ana/profile.json" in got and "portal/pilot-nova/profile.json" in got
    assert any(n.startswith("portal/demo-ana/uploads/") for n in got)
    assert "firm/users.json" in got
    with zipfile.ZipFile(only_zip(tmp_path)) as zf:
        assert "scrypt" not in zf.read("firm/users.json").decode() and '"hash"' not in zf.read("firm/users.json").decode()
