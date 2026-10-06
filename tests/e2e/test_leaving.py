"""The drill: leaving the product, as docs/security/exit_procedure.md says to (brief Q3). A small made-up firm is built (tools/make_world.py), and the document's own numbered
steps are run on it: the export, its check, the unzipping, the first page opened in a browser with scripts off (a case's values and a document reachable from it, a restricted case
marked), the ledger's check, the list of what is on the machine, the deletion, the proof and the receipt. The test reads the steps from the document, so the document and the tools
cannot drift: a step that names a command that is not there, or a flag it does not have, fails here.

The made-up install is not this checkout (a real install runs the tools from its own folder, where they find it), so the drill adds to each command only the flags that say where the
made-up install is (--data, --portal, --users, --root) and the answers a person would type (the firm's name, who). Everyone here is made up."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))

import leave  # noqa: E402
import make_world  # noqa: E402
import records  # noqa: E402
from test_leave import steps  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_drill_follows_the_exit_procedure_from_export_to_receipt(browser, tmp_path):
    timings: dict[str, float] = {}
    root = tmp_path / "install"
    t = time.time()
    world = make_world.build(root, cases=8, views=100, ledger=200, restricted=2, staff=2, sources=3, workers=2, log=lambda *a: None)
    timings["the made-up firm (8 cases)"] = time.time() - t
    shutil.move(str(root / "world.json"), str(tmp_path / "world.json"))  # the builder's own note, not a file of an install
    data = root / "data"
    # what the installer leaves besides the data: its folder, deployment.json, the Python environment, the backups' folder with one real backup, work folders
    import backups

    (root / "install").mkdir()
    bak = tmp_path / "backups"
    (root / "install" / "install.json").write_text(json.dumps({"port": free_port(), "backup_folder": str(bak), "encrypted": False}), encoding="utf-8")
    (root / "deployment.json").write_text(json.dumps({"mode": "on_premises"}), encoding="utf-8")
    (root / ".venv" / "lib").mkdir(parents=True)
    (root / ".venv" / "lib" / "site.py").write_text("# made up", encoding="utf-8")
    (root / ".ocr_tmp").mkdir()
    (root / ".ocr_tmp" / "page.tsv").write_text("tsv", encoding="utf-8")
    env = {**os.environ, **world["env"], "I485_MODEL_FOLDERS": "", "I485_LEAVE_TEMP": str(tmp_path / "temp"), "I485_SYSTEMD_DIR": str(tmp_path / "units"),
           "I485_CRONTAB": str(tmp_path / "no-crontab"), "HOME": str(tmp_path / "home")}
    os.environ.update({k: v for k, v in world["env"].items()})  # this process writes the backup and plans the deletion on the same install
    made = backups.make_backup(bak, data, clients=root / "clients")
    assert not made["problems"]
    sizes_before = {i.key: leave.measure(i.path) for i in leave.plan(root, env)}
    where = {"<export folder>": str(data / "exports"), "<unzipped export>": str(tmp_path / "unzipped"), "<install folder>": str(root)}
    named = {"export_firm.py": ["--data", str(data / "clients"), "--portal", str(data / "portal"), "--users", str(data / "review_users.json")], "leave.py": ["--root", str(root)]}
    zip_path: list[str] = []

    def command(text: str, extra: list[str] = ()) -> subprocess.CompletedProcess:
        for k, v in {**where, "<the zip>": zip_path[0] if zip_path else ""}.items():
            text = text.replace(k, v)
        parts = text.split()
        tool = parts[1] if parts[0] == "python" and parts[1].endswith(".py") else ""
        if parts[0] == "python":
            parts[0] = sys.executable
        if tool.startswith("tools/"):
            parts[1] = str(REPO / tool)
            parts += named.get(tool.split("/")[-1], [])
        started = time.time()
        r = subprocess.run(parts + list(extra), capture_output=True, text=True, env=env, cwd=REPO, timeout=600)
        timings[" ".join(text.split()[:3] + [w for w in text.split()[3:] if w.startswith("--")][:2])] = time.time() - started
        return r

    found = {n: re.findall(r"`([^`]+)`", body) for n, body in steps()}
    assert len(found) == 9

    # 1. export, 2. check the zip
    r = command(next(c for c in found[1] if c.startswith("python tools/export_firm.py")))
    assert r.returncode == 0, r.stderr
    zip_path.append(re.search(r"Wrote (.+\.zip)", r.stdout).group(1))
    sha = re.search(r"sha256: (\w{64})", r.stdout).group(1)
    assert hashlib.sha256(Path(zip_path[0]).read_bytes()).hexdigest() == sha
    r = command(next(c for c in found[2] if c.startswith("python tools/export_firm.py")))
    assert r.returncode == 0 and "matches its manifest" in r.stdout, r.stdout
    # 3. unzip it
    r = command(next(c for c in found[3] if c.startswith("python -m zipfile")))
    assert r.returncode == 0, r.stderr
    out = tmp_path / "unzipped"
    index = out / "index.html"
    assert index.is_file()
    started = time.time()
    case_ids = world["case_ids"]
    restricted_id = world["restricted_ids"][0]
    plain_id = next(c for c in case_ids if c not in world["restricted_ids"])
    # the first page, opened in a browser with scripts off
    ctx = browser.new_context(java_script_enabled=False)
    page = ctx.new_page()
    try:
        page.goto(index.as_uri())
        assert "exported" in page.title()
        assert page.locator("details.case").count() == len(case_ids)
        assert page.locator(f"#case-{restricted_id} .badge").count() == 1 and page.locator(f"#case-{plain_id} .badge").count() == 0
        page.click(f"#case-{plain_id} > summary")  # a case opens with the browser's own folding: no script
        graph = json.loads((out / "cases" / plain_id / "fact_graph.json").read_text(encoding="utf-8"))["facts"]
        key, fact = next((k, f) for k, f in sorted(graph.items()) if isinstance(f, dict) and f.get("value") not in (None, "") and len(str(f["value"])) > 6)
        assert page.locator(f"#case-{plain_id}").get_by_text(str(fact["value"]), exact=True).first.is_visible()  # a value of the case, on the page
        link = page.locator(f"#case-{plain_id} a[href='cases/{plain_id}/fact_graph.json']").first
        link.click()  # and the record's own file, one click away
        assert str(fact["value"]) in page.content()
        page.go_back()
        documents = [a for a in page.locator(f"#case-{plain_id} a").all() if re.search(r"\.(pdf|png|jpe?g)$", a.get_attribute("href") or "")]
        assert documents, "the case lists no document"
        target = (out / documents[0].get_attribute("href")).resolve()
        assert target.is_file() and target.read_bytes()[:4] == b"%PDF"  # a document is a link to the file in the export
        page.click(f"#case-{restricted_id} > summary")
        assert "restricted" in page.locator(f"#case-{restricted_id} .notice").first.inner_text().lower()
        month = page.locator("a:has-text('read as a table')").first
        month.click()  # the ledger as a table
        assert page.locator("table tr").count() > 2 and "The ledger," in page.title()
    finally:
        ctx.close()
    on = browser.new_context()  # with scripts on, the search narrows the list and "open every case" opens them
    page = on.new_page()
    try:
        page.goto(index.as_uri())
        page.fill("#find", "no-such-client-anywhere")
        assert page.locator("details.case").evaluate_all("els => els.filter(e => e.style.display === 'none').length") == len(case_ids)
        page.fill("#find", plain_id)
        assert page.locator(f"#case-{plain_id}").is_visible() and page.locator("details.case:visible").count() == 1
        page.click("#openall")
        assert page.locator("details.case[open]").count() >= 1
    finally:
        on.close()
    timings["the first page in the browser"] = time.time() - started
    # 4. the ledger's copy
    r = command(next(c for c in found[4] if c.startswith("python tools/verify_ledger.py")))
    assert r.returncode == 0 and r.stdout.startswith("intact: "), r.stdout
    # 5. stop the product: the units the document names are the units the installer registers
    import install_support

    units = install_support.unit_files(root, sys.executable, {"port": 8485, "host": "127.0.0.1", "backup_folder": str(bak), "encrypted": False})
    step5 = " ".join(found[5])
    assert all(name in units for name in re.findall(r"i485-[a-z]+\.(?:service|timer)", step5)) and len(re.findall(r"i485-[a-z]+\.(?:service|timer)", step5)) == 4
    # 6. what is on the machine
    r = command(next(c for c in found[6] if c.startswith("python tools/leave.py")))
    assert r.returncode == 0 and "Of the firm, on this machine:" in r.stdout and "The cases" in r.stdout and "The client portal's records" in r.stdout and "deployment.json" in r.stdout
    # 7. delete (the name is typed back; here it is given), 8. prove it
    r = command(next(c for c in found[7] if c.startswith("python tools/leave.py")), ["--confirm-name", "Exemplo Law (made up)", "--by", "Sam Attorney"])
    assert r.returncode == 0, r.stderr + r.stdout
    receipt = Path(re.search(r"Receipt: (.+)", r.stdout).group(1))
    r = command(next(c for c in found[8] if c.startswith("python tools/leave.py")))
    assert r.returncode == 0 and r.stdout.startswith("nothing of the firm remains except: ") and receipt.name in r.stdout, r.stdout
    # nothing the catalog names remains: every file left is the receipt, the export and the folder the firm kept its backups in
    remaining = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    assert remaining == {receipt.name, f"data/exports/{Path(zip_path[0]).name}"}, sorted(remaining)
    for pattern in records.patterns("firm", exported_only=False) + records.patterns("logs", exported_only=False):
        assert not list((data).glob(pattern)), pattern
    assert not (root / "clients").exists() and not (root / ".venv").exists() and not (root / ".ocr_tmp").exists() and not (data / "clients").exists() and not (data / "portal").exists()
    assert list(bak.glob("i485-backup-*.zip")) and made["archive"].exists()  # the backups were kept on the attorney's word
    # the receipt is complete
    text = receipt.read_text(encoding="utf-8")
    block = json.loads(text.split("-- the same, for a program --", 1)[1])
    assert block["by"] == "Sam Attorney" and block["firm"] == "Exemplo Law (made up)" and block["started"] and block["finished"] and block["product_version"] and block["not_deleted"] == []
    deleted = {d["key"]: d for d in block["deleted"]}
    for key, (files, size) in sizes_before.items():
        if key in ("exports", "backups"):
            continue
        assert key in deleted and deleted[key]["problem"] is None, key
        if key not in ("documents",):  # (the work folder inside the scans' folder is counted in its own line)
            assert deleted[key]["files"] >= files and deleted[key]["bytes"] >= size, key  # never less than the list said; the export's own rows grew the ledger and the access log
    assert {k["key"] for k in block["kept"]} == {"exports", "backups"} and block["export_checked"]["problems"] == [] and block["export_checked"]["path"] == zip_path[0]
    assert block["deleted_files"] > 100 and block["deleted_bytes"] > 0 and all(line in text for line in leave.STILL_YOURS)
    # 9. keep the receipt
    if shutil.which("sha256sum"):
        r = command(next(c for c in found[9] if c.startswith("sha256sum")).replace("<install folder>", str(root)).replace("YYYY-MM-DD", receipt.stem.removeprefix("leaving-receipt-")))
        assert r.returncode == 0 and r.stdout.split()[0] == hashlib.sha256(receipt.read_bytes()).hexdigest()
    print("\nthe drill, in seconds:", json.dumps({k: round(v, 2) for k, v in timings.items()}, indent=1))
    pytest.drill_timings = timings  # for the report
