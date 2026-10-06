"""Leaving the machine: what of the firm is on it, deleting all of it, and proving it is gone (docs/security/exit_procedure.md).

    python tools/leave.py                       list what of the firm is on this machine, with sizes: nothing is changed
    python tools/leave.py --delete --keep exports --keep backups
                                                delete everything the firm did not choose to keep, and write the receipt
    python tools/leave.py --verify              walk the same list: "nothing of the firm remains except: ..." or what is left

What it lists: every folder and file the data dictionary names (the cases, the portal's records, the firm's settings, staff, logs, ledger and caches, in the data folder
and wherever an I485_* setting puts one), the folder the scans come from, deployment.json and the install folder, the models the product downloaded, the virtual
environment, the work folders the readers leave (.ocr_tmp, clients/.work, the product's i485-backup-*, i485-restore-* and i485-accuracy-firm-* folders in the machine's temp folder), the scheduled and always-on services, and
the backups' and exports' folders.

What it never decides for the firm: the exports and the backups. The attorney says for each, `--keep` or `--remove`; the tool does not guess, and without an answer it
stops. Kept backups that are encrypted keep their passphrase file too (without it they cannot be opened). Kept models, with `--keep models`.

What it refuses: to run while the product is running (a held lock file, or something answering on the review app's port: stop the services first); to delete without an
export that matches its own manifest (`--export <zip>`, else the newest in the exports folder; `--skip-export-check` when the data is being abandoned on purpose, and the
receipt says so); to delete until the firm's name is typed back (`--confirm-name` for a script); and to touch a folder that holds this install or the user's home.

The receipt, `leaving-receipt-YYYY-MM-DD.txt` in the install folder, is the one file left beside what was kept: what was deleted, when, by whom, the sizes, what was kept,
and what the firm must still do itself. Deleting is not wiping a disk: the space is freed, not overwritten (the firm's media disposal and disk encryption cover that).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

PORT = 8485
KEEPS = ("exports", "backups", "models")
# the settings an installation uses to put a file or folder outside data/ (src/backups.py EXTRA_ENV and the others the tests and some installations set)
PATH_ENV = ("I485_INDEX", "I485_QUERY_DB", "I485_FIND", "I485_SETTINGS", "I485_MAINTENANCE_LOG", "I485_RULES_APPROVED", "I485_POLICIES_FIRM", "I485_INBOX", "I485_WORDINGS",
            "I485_READER_EXAMPLES", "I485_JOBS", "I485_ROSTER", "I485_ACCURACY_HISTORY", "I485_REFERENCE", "I485_AUDIT_FILL", "I485_BACKUP_LOG", "I485_DEPLOYMENT",
            "I485_QUERY_REFRESH_FILE", "PORTAL_DATA")
TEMP_PREFIXES = ("i485-backup-", "i485-restore-", "i485-accuracy-firm-")  # the work folders the product makes in the machine's temp folder
WINDOWS_TASKS = ("I485 Review App", "I485 Job Worker", "I485 Overnight Run", "I485 Backup")
CRON_MARK = "# i485-pipeline"
STILL_YOURS = [
    "Older backups kept off this machine (on another disk, in the cloud, with the IT person): every backup holds the firm's data until it is deleted or expires.",
    "The Clio record: the matters and documents in Clio itself are the firm's to keep or delete in Clio. This tool only removes the connection kept here.",
    "The mail provider's copies: messages the product sent (reminders, the portal's links) are in the provider's own logs and the recipients' mailboxes.",
    "The export itself: it holds every case, restricted ones included. Keep it on encrypted storage, and copy the printed seals of the ledger (verify_ledger.py --anchors) somewhere apart.",
    "The disk: deleting frees the space, it does not overwrite it. Erase or destroy the drive with the firm's own media-disposal practice, or rely on full-disk encryption whose key is destroyed.",
    "Models and packages this tool left alone, and the product's own code folders (src, tools, docs): they hold no client data; delete the install folder when the firm is done with the receipt.",
]


class LeaveError(Exception):
    """Something the person running the tool should read (no traceback)."""


@dataclass
class Item:
    key: str  # a stable name: "data/clients", "venv", "service:cron"
    label: str  # what it is, in words
    kind: str  # data, documents, install, work, models, venv, services, backups, exports
    path: Path | None = None
    why: str = ""
    keep: str | None = None  # the --keep word that spares it
    detail: dict[str, Any] = field(default_factory=dict)  # a service's name, a cron line


def human(n: int) -> str:
    size = float(n)
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} bytes" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} bytes"


def measure(path: Path | None) -> tuple[int, int]:
    """(files, bytes) under a path, links counted as themselves and never followed."""
    if path is None or not (path.exists() or path.is_symlink()):
        return 0, 0
    if path.is_symlink() or path.is_file():
        try:
            return 1, path.lstat().st_size
        except OSError:
            return 1, 0
    files = size = 0
    for here, dirs, names in os.walk(path, followlinks=False):
        for name in names + [d for d in dirs if (Path(here) / d).is_symlink()]:
            try:
                size += (Path(here) / name).lstat().st_size
                files += 1
            except OSError:
                files += 1
    return files, size


# -- what is on the machine --------------------------------------------------------------------------------------------------------------------


def firm_name(root: Path, env: Mapping[str, str]) -> str:
    """The firm's name as Settings holds it (what the installer asked first), else "" ."""
    path = Path(env.get("I485_SETTINGS") or root / "data" / "settings.json")
    try:
        values = (json.loads(path.read_text(encoding="utf-8")).get("firm") or {}).get("values") or {}
    except (OSError, ValueError, AttributeError):
        return ""
    for key in ("firm.business_name", "firm_name", "business_name"):
        if str(values.get(key) or "").strip():
            return " ".join(str(values[key]).split())
    return ""


def install_config(root: Path) -> dict[str, Any]:
    try:
        return json.loads((root / "install" / "install.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _catalog_label(name: str) -> str:
    """What the data dictionary calls a file or folder directly in the data folder."""
    import records

    for d in records.DATABASES:
        if d["where"].rsplit("/", 1)[-1] == name:
            return d["title"]
    for w in records.WORKING_FILES:
        if w["where"].rstrip("/").rsplit("/", 1)[-1] == name:
            return w["title"]
    r = records.record_of("firm", name)
    if r:
        return r["title"]
    first = {"clients": "The cases", "portal": "The client portal's records", "prospects": "First-call records", "inbox": "The notice inbox", "models": "The document-type model the firm trained",
             "exports": "Exports of the firm's data", "reference": "The firm's hand-filled references", "wordings": "The firm's wordings", "jobs": "The job queue", "clio": "The Clio connection",
             "reader_examples": "The reader examples"}.get(name)
    if first:
        return first
    for rec in records.RECORDS:  # a folder some record keeps its files in ("inbox/queue.json", "clio/state.json")
        if any(p.split("/")[0] == name and "/" in p for p in rec["files"]):
            return rec["title"] + " (a folder)"
    if any(name.startswith(prefix) for prefix in ("events-", "ledger_")):
        return "The event ledger and its seals"
    return "Not named by the data dictionary (a file the firm or its IT put here)"


def _outside(path: Path, root: Path) -> bool:
    return not (path == root or root in path.parents)


def model_folders(env: Mapping[str, str]) -> list[Path]:
    """The folders of models the product downloaded: the offline translator's packages (Argos), and stanza's resources. I485_MODEL_FOLDERS (a path list) names others."""
    found: list[Path] = []
    for part in (env.get("I485_MODEL_FOLDERS") or "").split(os.pathsep):
        if part:
            found.append(Path(part))
    if "I485_MODEL_FOLDERS" not in env:
        try:
            from argostranslate import settings as argos

            found.append(Path(argos.data_dir))
        except Exception:  # noqa: BLE001 -- the translator is optional
            data = Path(env.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
            found.append(data / "argos-translate")
        found.append(Path.home() / "stanza_resources")
    out: list[Path] = []
    for p in found:
        if p.exists() and p not in out:
            out.append(p)
    return out


def plan(root: Path, env: Mapping[str, str] | None = None) -> list[Item]:
    """Every place something of the firm is on this machine, found now. Nothing is changed."""
    env = os.environ if env is None else env
    root = Path(root).resolve()
    data = Path(env.get("I485_DATA") or root / "data").resolve()
    items: list[Item] = []
    seen: list[Path] = []

    def add(item: Item) -> None:
        if item.path is not None:
            if not (item.path.exists() or item.path.is_symlink()):
                return
            if any(item.path == s or s in item.path.parents for s in seen):
                return  # inside an item already listed: deleted with it
        items.append(item)
        if item.path is not None:
            seen.append(item.path)

    exports = data / "exports"
    config = install_config(root)
    backups = Path(config.get("backup_folder") or root / "backups").expanduser().resolve()
    if data.is_dir():
        for child in sorted(data.iterdir()):
            if child == exports:
                continue
            add(Item(f"data/{child.name}", _catalog_label(child.name), "data", child, "In the data folder."))
    add(Item("exports", "Exports of the firm's data (the zips an attorney made)", "exports", exports, "The firm's copy of everything: kept or removed by the attorney's word.", keep="exports"))
    add(Item("backups", "The backups' folder", "backups", backups, "Every backup holds the firm's data.", keep="backups"))
    for key, label, path in ((".ocr_tmp", "The readers' work folder (Tesseract's and the scans' temporary files)", root / ".ocr_tmp"), ("clients/.work", "The vision reader's work folder", root / "clients" / ".work")):
        add(Item(key, label, "work", path))
    passphrase = root / "install" / "backup_passphrase.txt"
    add(Item("documents", "The folder the scans come from (clients/)", "documents", root / "clients"))
    for path in dict.fromkeys([root / "deployment.json", Path(env["I485_DEPLOYMENT"]).resolve()] if env.get("I485_DEPLOYMENT") else [root / "deployment.json"]):
        add(Item("deployment" if path == root / "deployment.json" else "deployment:" + path.name, "deployment.json (who runs the install; it can hold the provider's keys)", "install", path))
    for name in PATH_ENV:  # a file or folder an I485_* setting puts outside the data folder
        value = env.get(name)
        if not value:
            continue
        p = Path(value).resolve()
        for target in [p] + ([] if p.is_dir() else sorted(p.parent.glob(p.name + "-*"))):  # a database's -wal and -shm files go with it
            if target.exists() and _outside(target, data):
                add(Item(f"env:{name}:{target.name}", f"Named by {name}: " + _catalog_label(target.name), "data", target, f"{name} puts it outside the data folder."))
    import events

    base = events.base_path(data)  # I485_EVENTS: the ledger's month files, its seals and its redactions beside the name
    if base.parent.is_dir() and _outside(base.parent, data):
        for p in sorted(base.parent.iterdir()):
            if p.name.startswith((base.stem + "-", "ledger_", base.stem + ".")):
                add(Item(f"env:I485_EVENTS:{p.name}", "The event ledger and its seals (named by I485_EVENTS)", "data", p))
    keep_passphrase = Item("install/backup_passphrase.txt", "The backups' passphrase (kept with kept encrypted backups: without it they cannot be opened)", "install", passphrase, keep="backups")
    install = root / "install"
    if install.is_dir():
        for child in sorted(install.iterdir()):
            if child == passphrase:
                add(keep_passphrase)
            else:
                add(Item(f"install/{child.name}", "The installer's files: its answers, logs and the scheduled jobs' files", "install", child))
    tmp = Path(env.get("I485_LEAVE_TEMP") or tempfile.gettempdir())
    for p in sorted(x for prefix in TEMP_PREFIXES for x in tmp.glob(prefix + "*")):  # the product's own (src/backups.py, src/accuracy_samples.py), never another program's
        add(Item(f"temp:{p.name}", "A work folder a backup or a restore left in the machine's temp folder", "work", p))
    for p in model_folders(env):
        add(Item(f"models:{p}", "Models the product downloaded (the offline translator's language packages)", "models", p, "Shared with any other program that uses them.", keep="models"))
    for name in (".venv", ".venv-wsl"):
        add(Item(name, "The product's Python environment (the installed packages)", "venv", root / name))
    add_services(items, env)
    return items


def add_services(items: list[Item], env: Mapping[str, str]) -> None:
    """The scheduled and always-on services: systemd user units, cron lines with the installer's mark, and on Windows the installer's tasks."""
    home = Path(env.get("HOME") or Path.home())
    unit_dir = Path(env.get("I485_SYSTEMD_DIR") or home / ".config" / "systemd" / "user")
    if unit_dir.is_dir():
        for p in sorted(unit_dir.glob("i485-*")):
            items.append(Item(f"service:systemd:{p.name}", "A systemd user unit the installer registered", "services", p, detail={"unit": p.name}))
    crontab = _run([env.get("I485_CRONTAB") or "crontab", "-l"], env)
    ours = [ln for ln in crontab.splitlines() if CRON_MARK in ln] if crontab else []
    if ours:
        items.append(Item("service:cron", f"{len(ours)} cron line(s) the installer registered ({CRON_MARK})", "services", None, detail={"lines": ours}))
    if os.name == "nt":
        for task in WINDOWS_TASKS:
            if _run(["schtasks", "/Query", "/TN", task], env) is not None:
                items.append(Item(f"service:task:{task}", "A Windows scheduled task the installer registered", "services", None, detail={"task": task}))


def _run(cmd: list[str], env: Mapping[str, str]) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def remove_service(item: Item, env: Mapping[str, str]) -> str | None:
    """Takes one service out. Returns a problem in words, or None."""
    try:
        if "unit" in item.detail:
            unit = item.detail["unit"]
            if unit.endswith((".service", ".timer")):
                subprocess.run(["systemctl", "--user", "disable", "--now", unit], capture_output=True, timeout=30)
            if item.path is not None:
                item.path.unlink(missing_ok=True)
            return None
        if "lines" in item.detail:
            command = env.get("I485_CRONTAB") or "crontab"
            current = _run([command, "-l"], env) or ""
            kept = "".join(ln + "\n" for ln in current.splitlines() if CRON_MARK not in ln)
            r = subprocess.run([command, "-"], input=kept, text=True, capture_output=True, timeout=20)
            return None if r.returncode == 0 else "the crontab could not be rewritten: " + (r.stderr or "").strip()
        if "task" in item.detail:
            r = subprocess.run(["schtasks", "/Delete", "/TN", item.detail["task"], "/F"], capture_output=True, text=True, timeout=20)
            return None if r.returncode == 0 else "the task could not be deleted: " + (r.stdout or r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


# -- is the product running ---------------------------------------------------------------------------------------------------------------------


def running(root: Path, env: Mapping[str, str] | None = None, probe: Callable[[int], bool] | None = None) -> list[str]:
    """What shows the product is running: a lock file some process holds (the overnight run, the job worker, the Clio steps), or something answering on the review app's
    port. [] when nothing does."""
    import oslock

    env = os.environ if env is None else env
    root = Path(root).resolve()
    data = Path(env.get("I485_DATA") or root / "data").resolve()
    folders = [data]
    if env.get("I485_JOBS"):
        folders.append(Path(env["I485_JOBS"]))
    found: list[str] = []
    for folder in folders:
        if not folder.is_dir():
            continue
        for p in sorted(folder.rglob("*.lock")):
            if p.is_file() and oslock.held_by_someone(p):
                found.append(f"{p.name} is held by a running process ({p.relative_to(folder).as_posix()})")
    port = int(install_config(root).get("port") or PORT)
    if (probe or _answers)(port):
        found.append(f"something answers on port {port}: the review app is running")
    return found


def _answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


# -- the export the firm holds ----------------------------------------------------------------------------------------------------------------


def check_export(root: Path, named: Path | None, env: Mapping[str, str]) -> dict[str, Any]:
    """The export the firm leaves with: named, else the newest zip in the exports folder. Checked against its own manifest. Returns {"path", "files", "made", "problems", "newer": how
    many files in the data folder changed after it was made}. Raises LeaveError when there is none."""
    import export_firm

    data = Path(env.get("I485_DATA") or root / "data").resolve()
    zips = sorted((data / "exports").glob("i485-firm-data-*.zip"), key=lambda p: p.stat().st_mtime) if (data / "exports").is_dir() else []
    path = Path(named) if named else (zips[-1] if zips else None)
    if path is None or not path.is_file():
        raise LeaveError("There is no export of the firm's data to leave with. Make one first (python tools/export_firm.py --everything), or name one with --export <zip>.")
    problems = export_firm.verify_zip(path)
    manifest = {}
    try:
        import zipfile

        with zipfile.ZipFile(path) as z:
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
    except (OSError, KeyError, ValueError):
        pass
    made = path.stat().st_mtime
    newer = 0
    for folder in (data / "clients", data / "portal"):
        if folder.is_dir():
            for here, _dirs, names in os.walk(folder, followlinks=False):
                newer += sum(1 for n in names if (Path(here) / n).lstat().st_mtime > made + 1)
    return {"path": str(path), "files": manifest.get("file_count"), "made": manifest.get("made"), "problems": problems, "newer": newer}


# -- deleting -----------------------------------------------------------------------------------------------------------------------------------


def _writable(func, path, _info) -> None:
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    func(path)


def delete_path(path: Path) -> str | None:
    """Removes a file, a link (never what it points to) or a folder with everything in it. Returns a problem in words, or None."""
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=_writable)
            else:
                shutil.rmtree(path, onerror=_writable)
        if path.exists() or path.is_symlink():
            return "it could not be removed"
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def guard(items: list[Item], root: Path) -> None:
    """Never a path that holds this install, the user's home, or the top of a disk."""
    home = Path.home().resolve()
    for it in items:
        p = it.path
        if p is None:
            continue
        if p == root or p in root.parents or p == home or p in home.parents or len(p.parts) < 3:
            raise LeaveError(f"Refusing to delete {p}: it holds this install or the user's folders. Check the settings that name it.")


def chosen(items: list[Item], keep: list[str], remove: list[str]) -> tuple[list[Item], list[Item]]:
    """(to delete, to keep). The exports and the backups need the attorney's word either way."""
    present = {it.keep for it in items if it.keep and it.path is not None and it.kind in ("exports", "backups")}
    undecided = sorted(k for k in present if k not in keep and k not in remove)
    if undecided:
        raise LeaveError("The attorney decides what happens to the " + " and the ".join(undecided) + f": say --keep {undecided[0]} or --remove {undecided[0]} for each. The tool does not guess.")
    gone, spared = [], []
    for it in items:
        (spared if it.keep and it.keep in keep else gone).append(it)
    return gone, spared


def leave(root: Path, *, keep: list[str], remove: list[str], confirm: Callable[[str], str], by: str, export: Path | None = None, skip_export_check: bool = False,
          env: Mapping[str, str] | None = None, probe: Callable[[int], bool] | None = None, say: Callable[[str], None] = print) -> Path:
    """Deletes everything the firm did not choose to keep, then writes the receipt and returns its path. Raises LeaveError, with nothing deleted, when it must refuse."""
    import clock

    env = os.environ if env is None else env
    root = Path(root).resolve()
    busy = running(root, env, probe)
    if busy:
        raise LeaveError("The product is running: " + "; ".join(busy) + ". Stop the review app, the job worker and the scheduled jobs first, then run this again.")
    items = plan(root, env)
    if not items:
        raise LeaveError("Nothing of the firm was found on this machine.")
    guard(items, root)
    gone, spared = chosen(items, keep, remove)
    exp = None
    if not skip_export_check:
        exp = check_export(root, export, env)
        if exp["problems"]:
            raise LeaveError("The export does not match its own manifest, so it is not a safe copy to leave with: " + "; ".join(exp["problems"][:3]) + ". Make a new export first.")
        say(f"The export you are leaving with: {exp['path']} ({exp['files']} files, made {exp['made']}, matches its manifest)."
            + (f" {exp['newer']} file(s) in the cases or the portal changed after it was made: make a new export if they matter." if exp["newer"] else ""))
    name = firm_name(root, env) or root.name
    typed = " ".join(confirm(f"This deletes the firm's data from this machine and cannot be undone. Type the firm's name ({name}) to go on: ").split())
    if typed.casefold() != name.casefold():
        raise LeaveError("The name typed does not match the firm's name. Nothing was deleted.")
    started = clock.now()
    done: list[dict[str, Any]] = []
    order = {"services": 0, "work": 1, "data": 2, "documents": 2, "install": 3, "exports": 4, "backups": 4, "models": 5, "venv": 6}
    for it in sorted(gone, key=lambda i: order.get(i.kind, 3)):
        files, size = measure(it.path)
        problem = remove_service(it, env) if it.kind == "services" else (delete_path(it.path) if it.path is not None else None)
        done.append({"key": it.key, "label": it.label, "kind": it.kind, "path": str(it.path) if it.path else None, "files": files, "bytes": size, "problem": problem})
        say(f"{'Could not delete' if problem else 'Deleted'} {it.path or it.label} ({files} files, {human(size)})" + (f": {problem}" if problem else ""))
    data = Path(env.get("I485_DATA") or root / "data").resolve()
    for empty in (data, root / "install", root / "clients", root / "backups"):  # a folder the firm emptied is not left behind
        try:
            if empty.is_dir() and not any(empty.iterdir()):
                empty.rmdir()
        except OSError:
            pass
    return write_receipt(root, started=started, by=by, name=name, done=done, kept=spared, export=exp, skipped=skip_export_check, keep=keep, remove=remove)


def write_receipt(root: Path, *, started, by: str, name: str, done: list[dict[str, Any]], kept: list[Item], export: dict[str, Any] | None, skipped: bool, keep: list[str], remove: list[str]) -> Path:
    import clock
    from version import VERSION

    when = clock.now()
    receipt = {"firm": name, "by": by, "started": started.isoformat(timespec="seconds"), "finished": when.isoformat(timespec="seconds"), "product_version": VERSION,
               "install_folder": str(root), "export_checked": export, "export_check_skipped": skipped, "decided": {"keep": sorted(keep), "remove": sorted(remove)},
               "deleted": done, "not_deleted": [d for d in done if d["problem"]],
               "kept": [{"key": k.key, "label": k.label, "path": str(k.path), "files": measure(k.path)[0], "bytes": measure(k.path)[1]} for k in kept],
               "deleted_files": sum(d["files"] for d in done if not d["problem"]), "deleted_bytes": sum(d["bytes"] for d in done if not d["problem"])}
    lines = ["Receipt: the firm's data deleted from this machine", "", f"Firm: {name}", f"Deleted by: {by}", f"Started: {_us(started)}   Finished: {_us(when)}",
             f"Product version: {VERSION}", f"Install folder: {root}", "",
             f"Deleted: {receipt['deleted_files']} files, {human(receipt['deleted_bytes'])}, in {len([d for d in done if not d['problem']])} places:"]
    for d in done:
        lines.append(f"  {'NOT DELETED' if d['problem'] else 'deleted'}  {d['path'] or d['label']}  ({d['files']} files, {human(d['bytes'])})  {d['label']}" + (f"  PROBLEM: {d['problem']}" if d["problem"] else ""))
    lines += ["", "Kept, on the firm's word:"] + ([f"  {k['path']}  ({k['files']} files, {human(k['bytes'])})  {k['label']}" for k in receipt["kept"]] or ["  nothing"])
    lines += ["", "The export checked before deleting: " + ("skipped by the firm's choice (--skip-export-check)" if skipped else f"{export['path']}, {export['files']} files, made {export['made']}, matched its manifest")]
    lines += ["", "What the firm must still do itself:"] + [f"  - {s}" for s in STILL_YOURS]
    lines += ["", "-- the same, for a program --", json.dumps(receipt, indent=2, ensure_ascii=False)]
    path = root / f"leaving-receipt-{when.date().isoformat()}.txt"
    n = 2
    while path.exists():
        path = root / f"leaving-receipt-{when.date().isoformat()}-{n}.txt"
        n += 1
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _us(when) -> str:
    return when.strftime("%m/%d/%Y %H:%M")


# -- the list and the check -------------------------------------------------------------------------------------------------------------------


def listing(items: list[Item], root: Path) -> list[str]:
    out = ["Of the firm, on this machine:"]
    total = 0
    paths = [i.path for i in items if i.path is not None]
    for it in items:
        files, size = measure(it.path)
        if not any(q != it.path and q in (it.path.parents if it.path else ()) for q in paths):  # a folder inside another listed one is counted once, in the outer
            total += size
        out.append(f"  {human(size):>10}  {files:>7} files  {it.path or it.label}" + f"\n{'':>31}{it.label}" + (f" (the attorney decides: --keep {it.keep} or --remove {it.keep})" if it.kind in ("exports", "backups") else ""))
    out.append(f"  {human(total):>10}  in all")
    return out


def verify(root: Path, env: Mapping[str, str] | None = None, keep: list[str] | None = None) -> tuple[bool, str]:
    """(nothing left, the sentence): walks the same list. What is left is named; the receipt, and what the firm kept, are the only things allowed."""
    root = Path(root).resolve()
    items = plan(root, env)
    receipts = sorted(root.glob("leaving-receipt-*.txt"))
    wanted = set(keep or [])
    if not wanted and receipts:  # what was kept is what the last receipt says
        try:
            wanted = set(json.loads(receipts[-1].read_text(encoding="utf-8").split("-- the same, for a program --", 1)[1])["decided"]["keep"])
        except (OSError, ValueError, KeyError, IndexError):
            wanted = set()
    left = [it for it in items if not (it.keep and it.keep in wanted)]
    kept = [it for it in items if it.keep and it.keep in wanted]
    if left:
        return False, "Still on this machine: " + "; ".join(f"{it.path or it.label} ({human(measure(it.path)[1])})" for it in left)
    if not receipts:
        return False, "Nothing else of the firm remains, but there is no receipt (leaving-receipt-*.txt): run --delete to make one."
    return True, "nothing of the firm remains except: " + ", ".join([str(r) for r in receipts] + [str(k.path) for k in kept])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=REPO, help="the install folder (default: the folder this tool is in)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--delete", action="store_true", help="delete everything not kept, and write the receipt")
    mode.add_argument("--verify", action="store_true", help="check that nothing of the firm remains except what was kept and the receipt")
    ap.add_argument("--keep", action="append", default=[], choices=KEEPS, help="keep this: the exports, the backups, or the downloaded models (repeat for more)")
    ap.add_argument("--remove", action="append", default=[], choices=KEEPS[:2], help="delete the exports or the backups too (the attorney's word)")
    ap.add_argument("--export", type=Path, help="the export zip the firm leaves with (default: the newest in the exports folder)")
    ap.add_argument("--skip-export-check", action="store_true", help="delete without an export that matches its manifest (the receipt says so)")
    ap.add_argument("--confirm-name", help="the firm's name, typed back (otherwise it is asked for)")
    ap.add_argument("--by", default=None, help="who is deleting (the receipt names them; default: the signed-in user)")
    args = ap.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.verify:
            ok, text = verify(root, keep=args.keep)
            print(text)
            return 0 if ok else 1
        if args.delete:
            confirm = (lambda _prompt: args.confirm_name) if args.confirm_name is not None else input
            path = leave(root, keep=args.keep, remove=args.remove, confirm=confirm, by=args.by or getpass.getuser(), export=args.export, skip_export_check=args.skip_export_check)
            print(f"Receipt: {path}")
            return 0
        items = plan(root)
        print("\n".join(listing(items, root)) if items else "Nothing of the firm was found on this machine.")
        busy = running(root)
        if busy:
            print("\nThe product is running (" + "; ".join(busy) + "): --delete will refuse until it is stopped.")
        return 0
    except LeaveError as exc:
        print(f"Not done: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
