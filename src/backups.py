"""Backups of the firm's data, and the test that proves one can be restored (tools/backup.py, tools/restore.py).

What goes in, as one dated zip with a manifest (a sha256 for every file):

  data/       the firm's data folder as it is: the case folders, the portal's data, the settings, the staff accounts,
              the learning store, the upkeep log, the event ledger and its daily seals (data/ledger_anchors.jsonl: a backup
              made on an earlier day holds the seals of its day, which a later rewrite cannot change; tools/verify_ledger.py --data
              <the unpacked folder> checks a restored ledger against them) (the default layout keeps everything under data/)
  documents/  the folder the scanned documents come from (clients/ next to the code), when it exists
  clients/, portal/, extra/<n>/   anything the installation keeps somewhere else (--clients, --portal, the I485_*
              paths that point outside data/): the manifest says where each came from

Six things are listed apart from the ordinary files, in the manifest's "separate" part, because each is treated
differently: the document index and the query layer (copies a person can build again, but slowly), the Clio vault (the saved connection:
encrypted already), the vault's key file, and deployment.json (it can hold the provider's keys).
The key file is the one that matters: a vault and its key together are the connection. So:

  with a passphrase   the whole archive is encrypted (Fernet, the pattern the Clio vault uses, in 1 MiB pieces so
                      a large backup needs no memory; the key is made from the passphrase with scrypt), and the
                      key file goes inside it;
  without one         the archive is plain, and the key file is written BESIDE it as its own file, never inside:
                      whoever finds the archive alone finds a vault they cannot open.

Left out on purpose: the backup log itself (it records the backups, a restore would carry a stale one), lock files,
half-written temp files and SQLite's -wal and -shm files (a database is copied with SQLite's own backup, which is
consistent while the app is running), links (never followed), and the backup folder itself when it sits inside what
is backed up.

The log (data/backup_log.json, I485_BACKUP_LOG) holds the date of the last backup and of the last test restore;
Keeping current reads it (status() below). A backup that could not read a file is not recorded as a backup.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import struct
import tempfile
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

import clock
import oslock

REPO = Path(__file__).resolve().parents[1]
LOG = Path(os.environ.get("I485_BACKUP_LOG") or REPO / "data" / "backup_log.json")
FORMAT = 1
MAGIC = b"I485BAK1"
CHUNK = 1 << 20
SCRYPT_LOG_N, SCRYPT_R, SCRYPT_P = 15, 8, 1  # 32 MiB: gentle on a small server, the passphrase is the weak part either way
MIN_PASSPHRASE = 12
BACKUP_MAX_DAYS = 2  # a backup is meant to run every night: older than this is overdue
RESTORE_MAX_DAYS = 31  # the upkeep register's "monthly": the test restore is the monthly check
PATTERN = re.compile(r"^i485-backup-\d{4}-\d{2}-\d{2}-\d{6}\.zip(\.enc)?$")
SKIP_SUFFIXES = (".tmp", ".part", ".lock", "-wal", "-shm")
SKIP_NAMES = {"backup_log.json", "posture.json"}  # (posture.json: a reading of the machine it was made on; a restore onto another would carry a stale one)
# where a file kept outside data/ is named by the environment (the test worlds and some installations); a folder or a file
EXTRA_ENV = (("I485_INDEX", "file"), ("I485_QUERY_DB", "file"), ("I485_FIND", "file"), ("I485_SETTINGS", "file"), ("I485_MAINTENANCE_LOG", "file"), ("I485_RULES_APPROVED", "file"),
             ("I485_POLICIES_FIRM", "file"), ("I485_INBOX", "dir"), ("I485_WORDINGS", "dir"),
             ("I485_READER_EXAMPLES", "dir"))  # the firm's reader examples (src/reader_examples.py) when kept outside data/; I485_FIND is the find index (src/find.py)
# the key files that are kept apart from the archive when it is not encrypted, and the ending each gets beside it
KEY_ROLES = {"vault_key": ".vault-key", "totp_key": ".totp-key"}
ROLES = {"index.db": "index", "query.db": "query", "find.db": "find", "secrets.enc": "vault", "vault.key": "vault_key", "deployment.json": "deployment"}
EXPORTS = "exports"  # data/exports/: the dated zips of the firm's data (tools/export_firm.py --everything): copies of the data, never in a backup of it


class BackupError(Exception):
    """Something the person running the tool should read (no traceback)."""


# -- the pieces ------------------------------------------------------------------------------------------------------

def inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


class Item:
    """One file going into the archive: where it is, what it is called inside, and its part in the manifest."""

    def __init__(self, arcname: str, path: Path, role: str = "data"):
        self.arcname, self.path, self.role = arcname, path, role


def _role(arcname: str, path: Path) -> str:
    base = path.name
    if base == "secrets.enc" and path.parent.name == "clio":
        return "vault"
    if base == "vault.key" and path.parent.name == "clio":
        return "vault_key"
    if base.endswith("_totp.key"):  # the staff accounts' authenticator secrets' key, beside the accounts file (review/auth.py key_path)
        return "totp_key"
    if base in ("index.db", "query.db", "find.db", "deployment.json") and (base != "deployment.json" or arcname.startswith("product/")):
        return ROLES[base]
    return "data"


def _walk(folder: Path, label: str, skip_inside: list[Path], left_out: list[str]) -> Iterator[Item]:
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder).as_posix()
        if path.is_symlink():
            left_out.append(f"{label}/{rel}: a link, not followed")
        elif path.is_file():
            if path.name in SKIP_NAMES or path.name.endswith(SKIP_SUFFIXES):
                continue
            if any(inside(path.resolve(), s) for s in skip_inside):
                continue
            yield Item(f"{label}/{rel}", path, _role(f"{label}/{rel}", path))


def sources(data: Path, documents: Path | None = None, clients: Path | None = None, portal: Path | None = None,
            deployment: Path | None = None) -> list[tuple[str, Path]]:
    """[(label, folder or file)] to back up. data/ is the firm's folder; the others only when they sit outside it."""
    data = data.resolve()
    out: list[tuple[str, Path]] = [("data", data)]
    for label, folder in (("documents", documents), ("clients", clients), ("portal", portal)):
        if folder is not None and Path(folder).exists() and not inside(Path(folder).resolve(), data) and not inside(data, Path(folder).resolve()):
            out.append((label, Path(folder).resolve()))
    n = 0
    for env, _kind in EXTRA_ENV:
        value = os.environ.get(env)
        if value and Path(value).exists() and not inside(Path(value).resolve(), data):
            n += 1
            out.append((f"extra/{n}", Path(value).resolve()))
    dep = Path(deployment) if deployment else Path(os.environ.get("I485_DEPLOYMENT") or REPO / "deployment.json")
    if dep.is_file():
        out.append(("product", dep.resolve()))
    return out


def plan(srcs: list[tuple[str, Path]], skip_inside: list[Path], left_out: list[str]) -> list[Item]:
    items: list[Item] = []
    for label, path in srcs:
        if path.is_file():
            items.append(Item(f"{label}/{path.name}", path, _role(f"{label}/{path.name}", path)))
        else:
            items.extend(_walk(path, label, skip_inside, left_out))
    return items


def _is_sqlite(path: Path) -> bool:
    if path.suffix.lower() != ".db":
        return False
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _snapshot(path: Path, into: Path) -> Path:
    """A consistent copy of a SQLite file that the running app may be writing (SQLite's own backup, not a file copy)."""
    target = into / "snapshot.db"
    target.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return target


def _case_roots(arcnames: list[str]) -> dict[str, int]:
    """How many case folders and portal clients the files say there are: the first folder under .../clients/ and under .../portal/clients/."""
    cases, portal = set(), set()
    for name in arcnames:
        parts = name.split("/")
        for i, part in enumerate(parts[:-2]):
            if part == "clients":
                (portal if i > 0 and parts[i - 1] == "portal" else cases).add("/".join(parts[:i + 2]))
                break
    return {"cases": len(cases), "portal_clients": len(portal)}


# -- encryption ------------------------------------------------------------------------------------------------------

def _fernet(passphrase: str, salt: bytes, log_n: int, r: int, p: int):
    from cryptography.fernet import Fernet

    key = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=2**log_n, r=r, p=p, dklen=32, maxmem=128 * r * (2**log_n + p + 2) + 2**20)
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_file(src: Path, dst: Path, passphrase: str) -> None:
    """src -> dst: header (magic, scrypt salt and cost), then 1 MiB pieces, each its own Fernet token that carries its number and
    whether it is the last, so a piece cannot be dropped, repeated or moved without the reader noticing."""
    salt = os.urandom(16)
    fernet = _fernet(passphrase, salt, SCRYPT_LOG_N, SCRYPT_R, SCRYPT_P)
    with open(src, "rb") as f, open(dst, "wb") as out:
        out.write(MAGIC + salt + struct.pack(">BBB", SCRYPT_LOG_N, SCRYPT_R, SCRYPT_P))
        index, block = 0, f.read(CHUNK)
        while True:
            following = f.read(CHUNK)
            token = fernet.encrypt(struct.pack(">QB", index, 0 if following else 1) + block)
            out.write(struct.pack(">I", len(token)) + token)
            if not following:
                break
            index, block = index + 1, following


def decrypt_file(src: Path, dst: Path, passphrase: str) -> None:
    from cryptography.fernet import InvalidToken

    wrong = BackupError("That passphrase does not open this backup, or the backup is damaged.")
    with open(src, "rb") as f, open(dst, "wb") as out:
        head = f.read(len(MAGIC) + 16 + 3)
        if len(head) < len(MAGIC) + 19 or head[:len(MAGIC)] != MAGIC:
            raise BackupError("This is not an encrypted backup made by this tool.")
        salt = head[len(MAGIC):len(MAGIC) + 16]
        log_n, r, p = struct.unpack(">BBB", head[len(MAGIC) + 16:])
        if log_n > 20 or r > 16 or p > 4:  # a header that asks for absurd work is not ours
            raise wrong
        fernet = _fernet(passphrase, salt, log_n, r, p)
        index, last = 0, False
        while True:
            size = f.read(4)
            if not size:
                break
            if last or len(size) < 4:
                raise wrong
            token = f.read(struct.unpack(">I", size)[0])
            try:
                plain = fernet.decrypt(token)
            except InvalidToken:
                raise wrong from None
            number, final = struct.unpack(">QB", plain[:9])
            if number != index:
                raise wrong
            out.write(plain[9:])
            index, last = index + 1, bool(final)
        if not last:
            raise BackupError("This backup is cut short: it was not copied completely.")


def _passphrase_ok(passphrase: str | None) -> None:
    if passphrase is not None and len(passphrase) < MIN_PASSPHRASE:
        raise BackupError(f"Use a passphrase of at least {MIN_PASSPHRASE} characters. A short sentence is easy to remember and hard to guess.")


# -- making a backup -------------------------------------------------------------------------------------------------

def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def make_backup(out_dir: Path, data: Path, documents: Path | None = None, clients: Path | None = None, portal: Path | None = None,
                passphrase: str | None = None, keep: int | None = None, deployment: Path | None = None, log_path: Path | None = None) -> dict[str, Any]:
    """Writes the dated zip (and, unencrypted, the key file beside it) into out_dir; records it in the log; returns what was made:
    {"archive", "key_file", "summary", "manifest", "problems"}. A file it could not read is a problem: the archive is kept for
    inspection but is not recorded as a backup."""
    _passphrase_ok(passphrase)
    data = Path(data)
    if not data.is_dir():
        raise BackupError(f"There is no data folder at {data}. Nothing was backed up.")
    out_dir = Path(out_dir).resolve()
    left_out: list[str] = []
    srcs = sources(data, documents, clients, portal, deployment)
    items = plan(srcs, [out_dir, data / EXPORTS], left_out)
    if not any(i.role == "data" for i in items):
        raise BackupError("There is nothing in the data folder to back up.")
    out_dir.mkdir(parents=True, exist_ok=True)
    at = started = clock.now()  # started_at: the restore drill's "since" is the install's changes after this, not after the backup's end (a record changed while the backup ran is "since" too)
    while True:  # two backups in the same second (a test, a double click) get the next second's name
        name = f"i485-backup-{at:%Y-%m-%d-%H%M%S}.zip"
        final = out_dir / (name + (".enc" if passphrase else ""))
        if not final.exists():
            break
        at += timedelta(seconds=1)
    work = Path(tempfile.mkdtemp(prefix="i485-backup-", dir=out_dir))
    problems: list[str] = []
    files: list[dict[str, Any]] = []
    separate: dict[str, Any] = {"index": None, "query": None, "find": None, "vault": None, "vault_key": None, "totp_key": None, "deployment": None}
    key_files: dict[str, Path] = {}
    try:
        plain = work / name
        with zipfile.ZipFile(plain, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for item in items:
                keep_apart = item.role in KEY_ROLES and not passphrase
                try:
                    source, temp = item.path, None
                    if _is_sqlite(item.path):
                        source = temp = _snapshot(item.path, work)
                    st = source.stat()
                    mtime = datetime.fromtimestamp(st.st_mtime)  # the file's own time, kept on the entry
                    when = mtime.timetuple()[:6] if mtime.year >= 1980 else (1980, 1, 1, 0, 0, 0)
                    digest, size = hashlib.sha256(), 0
                    if keep_apart:  # the key goes beside the archive, never in it
                        key_files[item.role] = final.with_name(final.name + KEY_ROLES[item.role])
                        fd = os.open(key_files[item.role], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                        with open(item.path, "rb") as src, os.fdopen(fd, "wb") as out:
                            data_bytes = src.read()
                            digest.update(data_bytes)
                            size = len(data_bytes)
                            out.write(data_bytes)
                    else:
                        info = zipfile.ZipInfo(item.arcname, date_time=when)
                        info.compress_type = zipfile.ZIP_DEFLATED
                        with open(source, "rb") as src, zf.open(info, "w", force_zip64=True) as out:
                            for chunk in iter(lambda: src.read(CHUNK), b""):
                                digest.update(chunk)
                                size += len(chunk)
                                out.write(chunk)
                    if temp is not None:
                        temp.unlink(missing_ok=True)
                except FileNotFoundError:
                    left_out.append(f"{item.arcname}: gone while the backup ran")
                    continue
                except (OSError, sqlite3.Error) as exc:
                    problems.append(f"{item.arcname}: could not be read ({type(exc).__name__})")
                    continue
                row = {"path": item.arcname, "bytes": size, "sha256": digest.hexdigest()}
                if item.role == "data":
                    files.append(row)
                else:
                    separate[item.role] = row | {"stored": "beside the archive" if keep_apart else "in the archive"}
            counts = {"files": len(files) + sum(1 for v in separate.values() if v), "bytes": sum(f["bytes"] for f in files)}
            counts |= _case_roots([f["path"] for f in files])
            try:
                from version import VERSION
            except Exception:  # noqa: BLE001 -- the version is a courtesy
                VERSION = "unknown"
            manifest = {"format": FORMAT, "started_at": started.isoformat(timespec="seconds"), "created_at": clock.stamp("seconds"), "product_version": VERSION, "encrypted": bool(passphrase),
                        "sources": {label: str(p) for label, p in srcs}, "counts": counts, "files": files, "separate": separate,
                        "left_out": left_out, "problems": problems}
            zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        part = final.with_name(final.name + ".part")
        if passphrase:
            encrypt_file(plain, part, passphrase)
        else:
            shutil.move(str(plain), part)
        os.replace(part, final)
        try:
            os.chmod(final, 0o600)
        except OSError:
            pass
    finally:
        shutil.rmtree(work, ignore_errors=True)
    summary = {"at": manifest["created_at"], "file": final.name, "folder": str(out_dir), "encrypted": bool(passphrase), "bytes": final.stat().st_size,
               "sha256": _sha(final), "counts": counts, "complete": not problems, "left_out": len(left_out)}
    summary_file = final.with_name(final.name + ".summary.json")
    summary_file.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if problems:  # kept to look at, under a name that no list of backups and no prune counts
        failed = final.with_name(final.name + ".failed")
        for old in (final, summary_file, *key_files.values()):
            os.replace(old, old.with_name(old.name + ".failed"))
        final, key_files = failed, {r: k.with_name(k.name + ".failed") for r, k in key_files.items()}
        summary["file"] = final.name
    else:
        _record("last_backup", summary, log_path)
        if keep:
            prune(out_dir, keep, final.name)
    return {"archive": final, "key_file": key_files.get("vault_key"), "key_files": key_files, "summary": summary, "manifest": manifest, "problems": problems}


def archives(folder: Path) -> list[Path]:
    """The backups in a folder, oldest first (the names carry the time)."""
    return sorted(p for p in Path(folder).iterdir() if p.is_file() and PATTERN.match(p.name)) if Path(folder).is_dir() else []


def prune(folder: Path, keep: int, just_made: str) -> list[str]:
    """Keeps the newest `keep` backups (always the one just made); removes the older ones with their summary and key file."""
    old = [p for p in archives(folder) if p.name != just_made]
    gone = []
    for p in old[:max(0, len(old) - (keep - 1))]:
        for extra in (p, p.with_name(p.name + ".summary.json"), *[p.with_name(p.name + end) for end in KEY_ROLES.values()]):
            extra.unlink(missing_ok=True)
        gone.append(p.name)
    return gone


# -- reading one back ------------------------------------------------------------------------------------------------

def _open(archive: Path, passphrase: str | list[str] | None, work: Path) -> Path:
    """The plain zip: the archive itself, or its decryption into work. passphrase: one, or the list a rotation leaves (the first that opens it is used)."""
    archive = Path(archive)
    if not archive.is_file():
        raise BackupError(f"There is no backup at {archive}.")
    with open(archive, "rb") as f:
        encrypted = f.read(len(MAGIC)) == MAGIC
    if not encrypted:
        return archive
    if not passphrase:
        raise BackupError("This backup is encrypted: give its passphrase.")
    plain = work / "plain.zip"
    tried = [passphrase] if isinstance(passphrase, str) else list(passphrase)
    for n, one in enumerate(tried):
        try:
            decrypt_file(archive, plain, one)
            return plain
        except BackupError:
            if n == len(tried) - 1:
                raise
    return plain


def _safe(name: str) -> bool:
    parts = name.split("/")
    return bool(name) and not name.startswith("/") and ".." not in parts and ":" not in parts[0] and "\\" not in name


def _check_json(path: Path) -> str | None:
    try:
        if path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
        else:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    json.loads(line)
    except (OSError, ValueError) as exc:
        return f"is not readable JSON ({type(exc).__name__})"
    return None


def _check_sqlite(path: Path) -> str | None:
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            row = con.execute("PRAGMA integrity_check").fetchone()
        finally:
            con.close()
        return None if row and row[0] == "ok" else "is a damaged database"
    except sqlite3.Error:
        return "is a damaged database"


def extract(archive: Path, passphrase: str | None, target: Path, work: Path | None = None) -> dict[str, Any]:
    """Unpacks into target (which must be empty or new), checking every file against the manifest's size and sha256, opening every
    JSON and every database, and counting the cases. Returns the report: {"ok", "problems", "manifest", "checked", "cases"}."""
    target = Path(target)
    problems: list[str] = []
    own = work is None
    work = Path(work) if work else Path(tempfile.mkdtemp(prefix="i485-restore-"))
    try:
        zpath = _open(archive, passphrase, work)
        try:
            zf = zipfile.ZipFile(zpath)
        except zipfile.BadZipFile:
            raise BackupError("This backup is damaged: it cannot be opened as an archive.") from None
        with zf:
            try:
                manifest = json.loads(zf.read("manifest.json"))
            except (KeyError, ValueError):
                raise BackupError("This backup has no readable list of its files, so it cannot be checked.") from None
            listed = {f["path"]: f for f in manifest["files"]} | {v["path"]: v for v in manifest["separate"].values() if v and v["stored"] == "in the archive"}
            target.mkdir(parents=True, exist_ok=True)
            seen: set[str] = set()
            for info in zf.infolist():
                if info.filename == "manifest.json" or info.is_dir():
                    continue
                if not _safe(info.filename):
                    problems.append(f"{info.filename}: a path that leaves the backup folder, not restored")
                    continue
                seen.add(info.filename)
                want = listed.get(info.filename)
                dest = target / info.filename
                dest.parent.mkdir(parents=True, exist_ok=True)
                digest, size = hashlib.sha256(), 0
                try:
                    with zf.open(info) as src, open(dest, "wb") as out:
                        for chunk in iter(lambda: src.read(CHUNK), b""):
                            digest.update(chunk)
                            size += len(chunk)
                            out.write(chunk)
                except (zipfile.BadZipFile, OSError, EOFError, RuntimeError) as exc:
                    problems.append(f"{info.filename}: damaged in the backup ({type(exc).__name__})")
                    continue
                if want is None:
                    problems.append(f"{info.filename}: in the backup but not on its list")
                elif digest.hexdigest() != want["sha256"] or size != want["bytes"]:
                    problems.append(f"{info.filename}: does not match what was backed up (damaged or changed)")
            for path in sorted(set(listed) - seen):
                problems.append(f"{path}: on the list but missing from the backup")
        opened = {"json": 0, "databases": 0}
        for rel in sorted(seen):
            path = target / rel
            if not path.is_file():
                continue
            bad = None
            if path.suffix in (".json", ".jsonl"):
                opened["json"] += 1
                bad = _check_json(path)
            elif _is_sqlite(path):
                opened["databases"] += 1
                bad = _check_sqlite(path)
            if bad:
                problems.append(f"{rel}: {bad}")
        if os.name == "posix":  # what comes out is client data: the owner only, whatever the umask
            for path in [target, *target.rglob("*")]:
                if not path.is_symlink():
                    os.chmod(path, 0o700 if path.is_dir() else 0o600)
        found = _case_roots(sorted(seen))
        want_counts = manifest.get("counts") or {}
        for key, label in (("cases", "case folders"), ("portal_clients", "portal clients")):
            if found[key] != want_counts.get(key):
                problems.append(f"The backup says it holds {want_counts.get(key)} {label}; {found[key]} came back.")
        vault = _vault_opens(target, manifest)
        return {"ok": not problems, "problems": problems, "manifest": manifest,
                "checked": {"files": len(seen), **opened}, "cases": found, "vault": vault, "encrypted": bool(manifest.get("encrypted"))}
    finally:
        if own:
            shutil.rmtree(work, ignore_errors=True)


def _vault_opens(target: Path, manifest: dict[str, Any]) -> str | None:
    """"opens", "not checked: ..." or None (no vault in this backup): tries the saved Clio connection with the key that came with it."""
    vault = (manifest.get("separate") or {}).get("vault")
    if not vault:
        return None
    key = (manifest.get("separate") or {}).get("vault_key")
    if not key or key.get("stored") != "in the archive":
        return "not checked: the key is kept apart from this backup, as it should be"
    try:
        from cryptography.fernet import InvalidToken

        import secretbox

        # a key file may list several keys after a rotation (the first encrypts, each decrypts): secretbox reads it the way the app does
        secretbox.cipher((target / key["path"]).read_bytes()).decrypt((target / vault["path"]).read_bytes())
        return "opens"
    except (InvalidToken, ValueError, OSError):
        return "does not open with its key"


def check_restore(archive: Path, passphrase: str | None = None, work_parent: Path | None = None, log_path: Path | None = None) -> dict[str, Any]:
    """The test restore: unpacks into a temporary folder, checks it as extract() does, deletes the folder, and records the result
    (the date a test restore last worked is what Keeping current shows)."""
    work = Path(tempfile.mkdtemp(prefix="i485-restore-", dir=work_parent))
    try:
        report = extract(archive, passphrase, work / "restored", work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _record("last_test_restore", {"at": clock.stamp("seconds"), "backup": Path(archive).name, "ok": report["ok"], "problems": len(report["problems"]),
                                   "files": report["checked"]["files"], "cases": report["cases"]["cases"]}, log_path, only_if=report["ok"])
    return report


def restore_into(archive: Path, passphrase: str | None, folder: Path) -> dict[str, Any]:
    """A real restore: into a folder that does not exist yet or is empty (never over live data). Everything is checked as it comes out;
    a problem leaves the folder in place for inspection, and the report says so."""
    folder = Path(folder)
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise BackupError(f"{folder} is not empty. Restore into a new, empty folder, then move the restored data into place yourself.")
    return extract(archive, passphrase, folder)


# -- the restore drill -----------------------------------------------------------------------------------------------------

DRILL_MAX_DAYS = RESTORE_MAX_DAYS  # the register's "monthly": the drill is run on the first night of the month, and whenever it is overdue
DRILL_SHOWN = 50  # the differences the log names; the rest are counted
DRILL_LOCK = "restore_drill.lock"  # beside the log: two drills (the night's and a button's) never run at once; a *.lock is left out of a backup


class DrillStopped(Exception):
    """The hour the overnight run was to end came while the drill was still comparing."""


def latest(log_path: Path | None = None) -> Path | None:
    """The newest backup: the one the log says was last made, else the newest in that folder."""
    last = read_log(log_path).get("last_backup") or {}
    folder, name = last.get("folder"), last.get("file")
    if folder and name and (Path(folder) / name).is_file():
        return Path(folder) / name
    found = archives(Path(folder)) if folder else []
    return found[-1] if found else None


def drill_passphrase() -> list[str] | None:
    """The passphrases the overnight run and the button have, the one in use first and the older ones after it (a backup made before a rotation still opens): read by
    name, src/firmsecrets.py (the environment's, else a file named by the environment, else the installer's own file for the nightly backup)."""
    import firmsecrets

    return firmsecrets.get_all("backups.passphrase") or None


def _changed(path: Path) -> float:
    """When a live file last changed: a database in WAL mode changes in its -wal file first."""
    t = path.stat().st_mtime
    wal = path.with_name(path.name + "-wal")
    return max(t, wal.stat().st_mtime) if wal.exists() else t


def _title(arc: str) -> str:
    """What a file in the backup is, in the catalog's words (src/records.py), for a line a person reads: never the case, never the file."""
    import records

    parts = arc.split("/")
    if parts[0] == "documents":
        return "A scanned document"
    if parts[0] != "data":
        return records.describe("firm", parts[-1])
    rel = parts[1:]
    if rel[:1] == ["clients"] and len(rel) >= 3:
        said = records.describe("case", "/".join(rel[2:]))
        return "A file of a case" if said == "A file" else f"{said} (of a case)"
    if rel[:2] == ["portal", "clients"] and len(rel) >= 4:
        return records.describe("portal", "/".join(rel[3:]))
    return records.describe("firm", "/".join(rel))


def _field_diffs(a: Any, b: Any, field: str = "") -> list[str]:
    """The fields (a.b[2].c) where two parsed JSON values differ: names only, never a value (the log is read by IT; a value is a client's)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for k in sorted(set(a) | set(b), key=str):
            f = f"{field}.{k}" if field else str(k)
            out += [f] if (k not in a or k not in b) else _field_diffs(a[k], b[k], f)
        return out
    if isinstance(a, list) and isinstance(b, list):
        out = []
        for i in range(max(len(a), len(b))):
            f = f"{field}[{i}]"
            out += [f] if (i >= len(a) or i >= len(b)) else _field_diffs(a[i], b[i], f)
        return out
    return [] if (a == b and type(a) is type(b)) or (isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool) and a == b) else [field or "(the whole record)"]


def _table_counts(path: Path) -> dict[str, int] | None:
    """Rows in each table of a database opened read-only, or None when it cannot be opened."""
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            tables = [r[0] for r in con.execute("select name from sqlite_master where type = 'table' and name not like 'sqlite_%'")]
            out = {}
            for t in tables:
                try:
                    out[t] = con.execute(f'select count(*) from "{t}"').fetchone()[0]
                except sqlite3.Error:  # a virtual table whose module this build does not have: not counted, on both sides
                    out[t] = -1
            return out
        finally:
            con.close()
    except sqlite3.Error:
        return None


def _jsonl_rows(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, clock.zone()).isoformat(timespec="seconds")


def drill(archive: Path | None = None, passphrase: str | None = None, *, log_path: Path | None = None, live: dict[str, Path] | None = None,
          deadline: datetime | None = None, work_parent: Path | None = None, by: str = "The restore drill", register: bool = True) -> dict[str, Any]:
    """The restore drill: the latest backup restored (restore_into: the one restore there is) into a scratch folder, and every file the backup holds compared
    with the live install it was made from. Nothing is written into the live install; the scratch folder is removed, whatever the result.

    Compared: the same set of files (a live file the backup does not hold, a backed-up file gone from the live install: except what the catalog says a backup leaves out,
    src/records.py NOT_BACKED_UP), every JSON record field by field, every JSON Lines file (the event ledger's months) row by row, every database's tables and their row counts,
    every other file (the scanned documents) by sha256, and the vault opening with its key. A difference is "since" when the live file changed after the backup was made (the live copy's
    time is later than the backup's: the install went on working, so it is expected and counted) and a failure when it did not (the live copy has not changed since, so the
    backup and the install disagree: a file lost, a record damaged, a file the backup missed). A file gone from the live install is "since" only when its folder changed
    after the backup. Each difference names the record, the field and the two times; a value is never written, because the log is read by IT and a value is a client's.
    A row of the ledger gone from a month is a failure whenever, because the ledger only grows.

    Returns {"ran", "ok", "stopped", "line", ...}; records it in the backup log (last_restore_drill) and, when it passed, as the register's check of the "restore_drill" item.
    deadline: the hour the overnight run ends (an aware datetime): a drill still comparing then stops, says so, and counts for nothing (it runs again)."""
    log_path = Path(log_path) if log_path else LOG
    started = time.monotonic()
    archive = Path(archive) if archive else latest(log_path)
    if archive is None:
        return {"ran": False, "ok": False, "stopped": False, "line": "No backup has been made yet, so there is nothing to restore."}
    try:
        with oslock.locked(log_path.with_name(DRILL_LOCK), timeout=0.0):
            work = Path(tempfile.mkdtemp(prefix="i485-drill-", dir=work_parent))
            try:
                out = _drill(archive, passphrase, work, live, deadline)
            except DrillStopped:
                out = {"stopped": True}
            except BackupError as exc:  # the backup cannot be opened at all: that is what a drill is for
                out = {"stopped": False, "failures": [{"file": archive.name, "plain": f"The latest backup could not be restored: {exc}", "what": str(exc), "kind": "archive"}],
                       "expected": [], "checked": {"files": 0}, "manifest": {}, "expected_count": 0}
            finally:
                shutil.rmtree(work, ignore_errors=True)  # pass or fail, nothing of the restore is left on the disk
    except oslock.LockBusy:
        return {"ran": False, "ok": False, "stopped": False, "line": "A restore drill is already running."}
    seconds = round(time.monotonic() - started, 1)
    if out["stopped"]:
        stopped_at = clock.now().strftime("%H:%M")
        entry = {"at": clock.stamp("seconds"), "backup": archive.name, "seconds": seconds, "stopped_at": stopped_at}
        _record("last_restore_drill_stopped", entry, log_path)
        return {"ran": True, "ok": False, "stopped": True, "seconds": seconds, "line": f"The restore drill was stopped at {stopped_at}, the hour the overnight run ends, before it finished. It runs again tonight."}
    failures = out["failures"]
    entry = {"at": clock.stamp("seconds"), "backup": archive.name, "backup_at": (out["manifest"] or {}).get("started_at") or (out["manifest"] or {}).get("created_at"), "ok": not failures, "seconds": seconds,
             "checked": out["checked"], "expected": out["expected_count"], "failures": len(failures), "first": failures[0]["plain"] if failures else None,
             "differences": failures[:DRILL_SHOWN], "since": out["expected"][:DRILL_SHOWN]}
    _record("last_restore_drill", entry, log_path)
    if register and not failures:
        try:  # the register's own record: the firm's check of the monthly item, by the drill (src/maintenance.py)
            import maintenance

            maintenance.mark("restore_drill", by)
        except KeyError:
            pass  # a register of a test world without the item
    return {"ran": True, "ok": not failures, "stopped": False, **entry, "line": drill_status(log_path=log_path)["line"]}


def _drill(archive: Path, passphrase: str | None, work: Path, live: dict[str, Path] | None, deadline: datetime | None) -> dict[str, Any]:
    import records

    def check() -> None:
        if deadline is not None and clock.now() >= deadline:
            raise DrillStopped

    report = restore_into(archive, passphrase, work / "restored")
    restored = work / "restored"
    manifest = report["manifest"]
    made = clock.parse(manifest.get("started_at") or manifest["created_at"])  # when the backup began (an older backup says only when it ended)
    backup_at = made.timestamp() if made else 0.0
    day = _mdy(made.astimezone(clock.zone()).date()) if made else "the backup"
    failures: list[dict[str, Any]] = []
    expected: list[dict[str, Any]] = []
    checked = {"files": 0, "json": 0, "ledger_months": 0, "databases": 0, "documents": 0}

    def fail(arc: str, plain: str, what: str, field: str | None = None, live_at: float | None = None, kind: str = "differs") -> None:
        failures.append({"file": arc, "field": field, "backup_time": manifest.get("started_at") or manifest["created_at"], "live_time": _iso(live_at) if live_at else None, "plain": plain, "what": what, "kind": kind})

    def since(arc: str, what: str, field: str | None, live_at: float) -> None:
        expected.append({"file": arc, "field": field, "backup_time": manifest.get("started_at") or manifest["created_at"], "live_time": _iso(live_at), "what": what})

    for problem in report["problems"]:  # what restoring the backup itself found: a file that does not match its list, a case count that is wrong
        fail(archive.name, "The latest backup does not restore cleanly: " + problem.split(": ", 1)[-1], problem, kind="archive")
    if report.get("vault") == "does not open with its key":
        fail(archive.name, "The saved Clio connection in the latest backup does not open with its key.", "The vault does not open with the key that came with it.", kind="vault")
    elif (manifest.get("separate") or {}).get("vault") and str(report.get("vault") or "").startswith("not checked"):
        beside = archive.with_name(archive.name + KEY_ROLES["vault_key"])
        if beside.is_file():  # the key kept apart, as it should be: tried here with the copy beside the archive
            try:
                import secretbox

                secretbox.cipher(beside.read_bytes()).decrypt((restored / manifest["separate"]["vault"]["path"]).read_bytes())
            except Exception:  # noqa: BLE001 -- any way the key fails is a vault that does not open
                fail(archive.name, "The saved Clio connection in the latest backup does not open with the key kept beside it.", "The vault does not open with the key beside the archive.", kind="vault")

    srcs = {label: Path(p) for label, p in (manifest.get("sources") or {}).items()} if live is None else dict(live)
    data = srcs.get("data")
    for label, folder in srcs.items():
        if not folder.exists():
            fail(archive.name, f"The install the backup was made from is not where it was ({label}).", f"{label}: {folder} does not exist", kind="install")
    items = {i.arcname: i for i in plan([(label, p) for label, p in srcs.items() if p.exists()], [archive.resolve().parent, *([data / EXPORTS] if data else [])], [])
             if not records.not_backed_up(i.arcname.split("/", 1)[-1])}
    listed = {f["path"]: f for f in manifest["files"]} | {v["path"]: v for v in (manifest.get("separate") or {}).values() if v}
    gone_while = {line.split(":", 1)[0] for line in manifest.get("left_out") or []}

    for arc in sorted(set(items) - set(listed) - gone_while):  # a live file the backup does not hold
        check()
        t = _changed(items[arc].path)
        if t > backup_at:
            since(arc, "added to the install after the backup was made", None, t)
        else:
            fail(arc, f"{_title(arc)} is in the install and not in the backup of {day}, and it is older than the backup.", f"{arc}: in the install, not in the backup", None, t, "not backed up")
    for arc in sorted(listed):
        check()
        row, item = listed[arc], items.get(arc)
        checked["files"] += 1
        if item is None:  # a file the backup holds, not in the install: gone since (its folder changed after) or lost
            folder = (srcs.get(arc.split("/", 1)[0]) or Path(".")) / arc.split("/", 1)[1] if "/" in arc else Path(".")
            while not folder.exists() and folder != folder.parent:
                folder = folder.parent
            t = folder.stat().st_mtime if folder.exists() else 0.0
            if t > backup_at:
                since(arc, "taken out of the install after the backup was made", None, t)
            else:
                fail(arc, f"{_title(arc)} is in the backup of {day} and gone from the install, and nothing in its folder has changed since.", f"{arc}: in the backup, gone from the install", None, t, "lost")
            continue
        t = _changed(item.path)
        after = t > backup_at
        copy = restored / arc
        if not copy.is_file():  # the key kept beside the archive: its hash against the live key's
            if _sha(item.path) != row["sha256"]:
                if after:
                    since(arc, "a key file replaced after the backup was made", None, t)
                else:
                    fail(arc, f"{_title(arc)} is not what the backup of {day} holds, and it has not changed since.", f"{arc}: a key file differs", None, t, "bytes")
            continue
        if _is_sqlite(item.path):
            checked["databases"] += 1
            mine, theirs = _table_counts(copy), _table_counts(item.path)
            if theirs is None or mine is None:
                fail(arc, f"{_title(arc)} cannot be opened for the comparison.", f"{arc}: a database that cannot be opened", None, t, "database")
                continue
            for table in sorted(set(mine) | set(theirs)):
                if mine.get(table) != theirs.get(table):
                    what = f"table {table}: {mine.get(table, 'absent')} rows in the backup, {theirs.get(table, 'absent')} in the install"
                    if after:
                        since(arc, what, table, t)
                    else:
                        fail(arc, f"{_title(arc)} holds a different number of rows than the backup of {day}, and it has not changed since.", f"{arc}: {what}", table, t, "database")
            continue
        suffix = arc.rsplit(".", 1)[-1] if "." in arc.rsplit("/", 1)[-1] else ""
        if suffix == "jsonl":
            ledger = arc.rsplit("/", 1)[-1].startswith("events-")
            checked["ledger_months" if ledger else "json"] += 1
            try:
                mine_rows, live_rows = _jsonl_rows(copy), _jsonl_rows(item.path)
            except (OSError, ValueError):
                fail(arc, f"{_title(arc)} cannot be read for the comparison.", f"{arc}: not readable", None, t, "unreadable")
                continue
            if ledger and len(live_rows) < len(mine_rows):
                fail(arc, f"The event ledger holds fewer rows than the backup of {day}: a month of it has lost rows.", f"{arc}: {len(mine_rows)} rows in the backup, {len(live_rows)} in the install", "rows", t, "ledger")
                continue
            if len(live_rows) > len(mine_rows):  # the install went on writing the ledger: rows added since
                since(arc, f"{len(live_rows) - len(mine_rows)} row(s) added after the backup was made", "rows", t)
            for n, (a, b) in enumerate(zip(mine_rows, live_rows), start=1):
                if a == b:
                    continue
                try:
                    fields = _field_diffs(json.loads(a), json.loads(b)) or ["(the row)"]
                except ValueError:
                    fields = ["(the row)"]
                for f in fields:
                    if after:
                        since(arc, f"row {n}, {f}", f"row {n}.{f}", t)
                    else:
                        fail(arc, f"{_title(arc)} differs from the backup of {day} in a row, and the install has not changed since.", f"{arc}: row {n}, {f}", f"row {n}.{f}", t, "differs")
            continue
        if _sha(item.path) == row["sha256"]:
            checked["json" if suffix == "json" else "documents"] += 1
            continue
        if suffix == "json":  # the same record, field by field
            checked["json"] += 1
            try:
                fields = _field_diffs(json.loads(copy.read_text(encoding="utf-8")), json.loads(item.path.read_text(encoding="utf-8"))) or ["(the whole record)"]
            except (OSError, ValueError):
                fail(arc, f"{_title(arc)} is not readable JSON in the install.", f"{arc}: not readable JSON", None, t, "unreadable")
                continue
            for f in fields:
                if after:
                    since(arc, f"field {f}", f, t)
                else:
                    fail(arc, f"{_title(arc)} differs from the backup of {day}, and the install has not changed it since.", f"{arc}: field {f}", f, t, "differs")
            continue
        checked["documents"] += 1  # a document, the same bytes or not
        if after:
            since(arc, "replaced after the backup was made", None, t)
        else:
            fail(arc, f"{_title(arc)} is not the same bytes as in the backup of {day}, and it has not changed since.", f"{arc}: sha256 differs", None, t, "bytes")
    return {"stopped": False, "failures": failures, "expected": expected, "expected_count": len(expected), "checked": checked, "manifest": manifest}


# -- the log and what Keeping current shows ----------------------------------------------------------------------------

def _record(kind: str, entry: dict[str, Any], log_path: Path | None = None, only_if: bool = True) -> None:
    path = Path(log_path) if log_path else LOG
    log = read_log(path)
    log.setdefault("history", []).append({"kind": kind} | entry)
    log["history"] = log["history"][-60:]
    if only_if:
        log[kind] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(log, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_log(path: Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else LOG
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def _mdy(d: date) -> str:
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def _days(stamp: Any, today: date) -> tuple[date, int] | None:
    d = clock.local_date(stamp)
    return (d, (today - d).days) if d else None


def status(today: date | None = None, log_path: Path | None = None) -> dict[str, Any]:
    """What Keeping current says about backups, from the log: {"line", "overdue", "backup": {...}|None, "restore": {...}|None, "findings"}.
    The line reads "Last backup 10/03/2026; last test restore 09/28/2026, 34 days ago: overdue." A backup is overdue after
    BACKUP_MAX_DAYS, a test restore after RESTORE_MAX_DAYS (the register's monthly)."""
    today = today or clock.today()
    log = read_log(log_path)
    parts, findings = [], []
    b, r = log.get("last_backup"), log.get("last_test_restore")
    bd, rd = (_days(b["at"], today) if b else None), (_days(r["at"], today) if r else None)
    if bd is None:
        parts.append("No backup has been made yet")
        findings.append("No backup has been made yet.")
    else:
        late = bd[1] > BACKUP_MAX_DAYS
        parts.append(f"Last backup {_mdy(bd[0])}" + (f", {bd[1]} days ago: overdue" if late else ""))
        if late:
            findings.append(f"The last backup was {bd[1]} days ago; a backup should run every night.")
    if rd is None:
        parts.append("no test restore yet: overdue")
        findings.append("A test restore has never been done: do one now to know the backups can be used.")
    else:
        late = rd[1] > RESTORE_MAX_DAYS
        when = "today" if rd[1] == 0 else f"{rd[1]} day{'s' if rd[1] != 1 else ''} ago"
        parts.append(f"last test restore {_mdy(rd[0])}, {when}" + (": overdue" if late else ""))
        if late:
            findings.append(f"The last test restore was {rd[1]} days ago; do one every month.")
    line = "; ".join(parts) + "."
    return {"line": line, "overdue": bool(findings), "findings": findings, "backup": b, "restore": r, "drill": drill_status(today, log_path)}


def _minutes(seconds: float) -> str:
    n = round(seconds / 60)
    return "less than a minute" if seconds < 60 else f"{n} minute{'s' if n != 1 else ''}"


def drill_status(today: date | None = None, log_path: Path | None = None) -> dict[str, Any]:
    """What Settings (Keeping current) and the morning report say of the restore drill, from the log: {"line", "overdue", "findings", "last"}.
    "Last restore drill: 10/04/2026, passed in 3 minutes." / "Last restore drill: 10/04/2026, failed: <the first difference>." / "Last restore drill: never run."
    It is overdue when never run, when the last one failed, and after DRILL_MAX_DAYS (the register's monthly). A drill the night's end stopped counts for nothing: it is said, and runs again."""
    today = today or clock.today()
    log = read_log(log_path)
    d, stopped = log.get("last_restore_drill"), log.get("last_restore_drill_stopped")
    dd = _days(d["at"], today) if d else None
    findings: list[str] = []
    if dd is None:
        line = "Last restore drill: never run."
        findings.append("A restore drill has never been run: it restores the latest backup and compares it with the live install. It runs on the first night of every month, or run it now.")
    elif d.get("ok"):
        line = f"Last restore drill: {_mdy(dd[0])}, passed in {_minutes(d.get('seconds') or 0)}."
    else:
        first = str(d.get("first") or "a difference was found").rstrip(".")
        line = f"Last restore drill: {_mdy(dd[0])}, failed: {first}."
        findings.append(f"The last restore drill ({_mdy(dd[0])}) failed: {first}. A backup that does not restore as the install is cannot be relied on: see the restore drill in docs/deployment.md.")
    if dd is not None and dd[1] > DRILL_MAX_DAYS:
        findings.append(f"The last restore drill was {dd[1]} days ago; it runs on the first night of every month.")
    unfinished = bool(stopped) and (d is None or clock.key(stopped["at"]) > clock.key(d["at"]))  # the night ended before the last attempt did: it runs again tonight
    if unfinished and (sd := _days(stopped["at"], today)):
        line += f" The drill of {_mdy(sd[0])} was stopped at {stopped.get('stopped_at')}, before it finished; it runs again tonight."
    return {"line": line, "overdue": bool(findings), "findings": findings, "last": d, "unfinished": unfinished,
            "failed_lately": bool(d) and not d.get("ok") and dd is not None and dd[1] <= DRILL_MAX_DAYS}
