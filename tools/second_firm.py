"""Operator-only synthetic two-installation drill. No installation is run here.

Use plan, start, seed, check, and packet in that order (docs/onboarding.md).
All mutable commands refuse directories without this drill's synthetic marker.
The check invokes real storage/search/roster/security APIs in separate runtimes;
it does not substitute for browser, scheduler or first-packet acceptance.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

from run_install import environment, python_for

MARKER = ".synthetic-firm-drill.json"
FIRMS = {"a": {"name": "Synthetic Alder Firm", "port": 8485, "portal_port": 8600},
         "b": {"name": "Synthetic Birch Firm", "port": 8486, "portal_port": 8601}}
EMAIL = "operator@synthetic.example"
CASES = ("fixture-001", "fixture-protected")


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fixture(root: Path) -> dict:
    from release_support import reject_links
    reject_links(root)
    value = read(root / MARKER)
    if value.get("kind") != "synthetic-two-firm-1" or value.get("root") != str(root.resolve()) or value.get("firm") not in FIRMS:
        raise ValueError("This is not a prepared synthetic drill installation.")
    for part in ("data", "clients", "install"):
        reject_links(root / part)
    return value


def plan(archive: Path, key: Path, out: Path) -> dict:
    from release_support import verify, reject_links
    reject_links(out)
    out = out.resolve()
    if out.exists():
        raise ValueError("Choose a new drill directory.")
    verified = verify(archive, key)
    out.mkdir(parents=True)
    os.chmod(out, 0o700)
    for firm, config in FIRMS.items():
        root = out / ("firm-" + firm)
        verify(archive, key, root)
        save(root / MARKER, {"kind": "synthetic-two-firm-1", "root": str(root), "firm": firm,
                            "marker": "synthetic" + firm + secrets.token_hex(8), "search_marker": "drillword" + firm + secrets.token_hex(8), **config})
    save(out / "drill.json", {"kind": "synthetic-two-firm-1", "release": verified,
                             "firms": {f: str(out / ("firm-" + f)) for f in FIRMS},
                             "proof": "pending; installers and probes have not run"})
    return {"drill": str(out), "next": "Record start, then run each installation's installer with docs/onboarding.md options."}


def child(root: Path, action: str, other: Path | None = None) -> dict:
    fixture(root)
    py = python_for(root, str(root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")))
    args = [py, str(root / "tools/second_firm.py"), action, "--root", str(root)]
    if other is not None:
        fixture(other)
        args += ["--other", str(other)]
    result = subprocess.run(args, cwd=root, env=environment(root), capture_output=True, text=True)
    if result.returncode:
        raise ValueError("Synthetic operator step stopped. No isolation pass is claimed; inspect the installation and repeat from new fixtures.")
    return json.loads(result.stdout)


def modules(root: Path) -> dict:
    # Every child loads only its own installation's imports and scoped environment.
    scoped = environment(root)
    os.environ.clear()
    os.environ.update(scoped)
    sys.path.insert(0, str(root / "src"))
    import backups, events, find, firmsecrets, ledger_seal, restricted  # noqa: F401 -- returned through locals()
    from review.auth import Accounts  # noqa: F401 -- returned through locals()
    from portal.store import PortalStore  # noqa: F401 -- returned through locals()
    from review import roster  # noqa: F401 -- returned through locals()
    from factgraph import FactGraph  # noqa: F401 -- returned through locals()
    return locals()


def seed(root: Path) -> dict:
    info = fixture(root)
    config = read(root / "install/install.json")
    if config.get("port") != info["port"] or config.get("portal_port") != info["portal_port"] or not config.get("portal_enabled"):
        raise ValueError("Install this firm with the planned review/portal ports and portal enabled.")
    m = modules(root)
    accounts = m["Accounts"](root / "data/review_users.json")
    portal = m["PortalStore"](root / "data/portal")
    if accounts.users() or portal.clients() or any((root / "data/clients").iterdir()) or (root / "install/drill-credentials.json").exists():
        raise ValueError("Seed requires an empty synthetic installation; existing records will not be overwritten.")
    password, staff_password = secrets.token_urlsafe(30), secrets.token_urlsafe(30)
    accounts.create_first(EMAIL, info["name"], password, accounts.new_setup_code())
    temp = accounts.add("staff@synthetic.example", "Synthetic staff", "paralegal", by=EMAIL)
    accounts.change_password("staff@synthetic.example", temp, staff_password)
    for case in CASES:
        folder = root / "data/clients" / case
        folder.mkdir(parents=True)
        graph = m["FactGraph"](case)
        graph.add_source("applicant.family_name", "synthetic-intake", "intake_questionnaire", info["marker"], info["marker"], 1.0, tier=3)
        graph.save(folder / "fact_graph.json")
        save(folder / "notes.json", {"notes": [{"id": "same-note", "text": "sharedword " + info["search_marker"], "at": datetime.now(timezone.utc).isoformat()}]})
        portal.add_client(case, info["marker"] + " " + case, email=case + "@synthetic.example", language="en", consent={"email": False, "sms": False, "whatsapp": False})
        if case == "fixture-protected":
            m["restricted"].protect_new(folder, m["restricted"].FIRM_KIND, "Synthetic confidential matter", "Added as", EMAIL)
        documents = root / "clients" / case
        documents.mkdir(parents=True)
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_metadata({"/Title": "SYNTHETIC ONLY " + info["marker"]})
        with (documents / "same-document.pdf").open("wb") as stream:
            writer.write(stream)
        row = m["events"].record("documents", "fixture_created", "sharedword " + info["search_marker"], case_dir=folder, who=info["name"])
        if not row:
            raise ValueError("The synthetic ledger event was not recorded.")
    vault_value = secrets.token_urlsafe(32)
    m["firmsecrets"].put("smtp.password", vault_value, "Synthetic drill", env={}, data_root=root / "data")
    link = portal.new_link_token(CASES[0])
    session = portal.redeem_link(link)
    # Private evidence is installation-local and never included in printed reports.
    save(root / "install/drill-credentials.json", {"password": password, "staff_password": staff_password, "vault_value": vault_value,
                                                  "portal_link": portal.new_link_token(CASES[0]), "portal_session": session})
    return {"seeded": True, "firm": info["firm"], "proof": "pending"}


def artifacts(root: Path) -> dict:
    fixture(root)
    m = modules(root)
    model = m["find"].HashingEmbedder()
    m["find"].rebuild_all(root / "data/clients", model=model)
    m["roster"].warm(root / "data/clients", root / "data/portal", workers=1)
    base = m["events"].base_path(root / "data")
    # Synthetic fixture only: close today's generated event set for a cross-seal probe.
    m["ledger_seal"].nightly(base, date.today() + timedelta(days=2))
    verified = m["ledger_seal"].verify(base)
    if not verified["ok"] or not verified["rows"] or not verified["anchors"]:
        raise ValueError("Synthetic ledger did not produce a nonempty intact sealed record.")
    phrase = (root / "install/backup_passphrase.txt").read_text(encoding="utf-8").splitlines()[0]
    made = m["backups"].make_backup(root / "install/drill-backups", root / "data", documents=root / "clients", portal=root / "data/portal",
                                    deployment=root / "deployment.json", passphrase=phrase, log_path=root / "data/backup_log.json")
    if made["problems"]:
        raise ValueError("Synthetic backup has problems.")
    save(root / "install/drill-artifacts.json", {"archive": str(made["archive"]), "prepared_at": datetime.now(timezone.utc).isoformat()})
    return {"prepared": True}


def probe(root: Path, other: Path) -> dict:
    info, foreign = fixture(root), fixture(other)
    if info["firm"] == foreign["firm"] or root.resolve() == other.resolve():
        raise ValueError("Choose the other synthetic installation.")
    m = modules(root)
    local = read(root / "install/drill-credentials.json")
    remote = read(other / "install/drill-credentials.json")
    checks = {}
    config, remote_config = read(root / "install/install.json"), read(other / "install/install.json")
    checks["configured_ports"] = (config["port"], config["portal_port"]) == (info["port"], info["portal_port"]) and len({config["port"], config["portal_port"], remote_config["port"], remote_config["portal_port"]}) == 4
    checks["configured_paths"] = environment(root)["I485_CASES"] == str(root / "data/clients") and environment(root)["PORTAL_DATA"] == str(root / "data/portal")
    accounts = m["Accounts"](root / "data/review_users.json")
    try:
        accounts.sign_in(EMAIL, remote["password"])
        checks["cross_staff_password_refused"] = False
    except ValueError:
        checks["cross_staff_password_refused"] = True
    checks["colliding_staff_accounts"] = {u["email"] for u in accounts.users()} == {EMAIL, "staff@synthetic.example"}
    portal = m["PortalStore"](root / "data/portal")
    checks["own_portal_session"] = portal.session_client(local["portal_session"]) == CASES[0]
    checks["cross_portal_session_refused"] = portal.session_client(remote["portal_session"]) is None
    checks["cross_portal_link_refused"] = portal.redeem_link(remote["portal_link"]) is None
    profiles = json.dumps([portal.profile(cid) for cid in portal.clients()])
    checks["portal_records"] = info["marker"] in profiles and foreign["marker"] not in profiles and set(portal.clients()) == set(CASES)
    staff = {"email": "staff@synthetic.example", "role": "paralegal"}
    scope = m["restricted"].scope(staff, root / "data/clients")
    checks["restricted_case_scope"] = CASES[1] in scope["hidden"] and not m["restricted"].visible_to(staff, root / "data/clients" / CASES[1])
    model = m["find"].HashingEmbedder()
    db = m["find"].default_path(root / "data/clients")
    hit = m["find"].search("sharedword", db_path=db, model=model, hidden=scope["hidden"], confidential_cases=scope["confidential"], attorney=False)
    text = json.dumps(hit["results"])
    checks["search_records_and_restriction"] = bool(hit["results"]) and info["search_marker"] in text and foreign["search_marker"] not in text and all(r["case"] != CASES[1] for r in hit["results"])
    roster = m["roster"].warm(root / "data/clients", root / "data/portal", workers=1)
    entries = json.dumps(roster.rows(staff))
    checks["roster_records_and_restriction"] = info["marker"] in entries and foreign["marker"] not in entries and roster.hides(staff, roster.entries[CASES[1]])
    value = m["firmsecrets"].get("smtp.password", env={}, data_root=root / "data")
    checks["r4_secret_store"] = value == local["vault_value"] and value != remote["vault_value"]
    from cryptography.fernet import InvalidToken
    cipher = m["firmsecrets"].vault_cipher(m["firmsecrets"].vault_folder(root / "data"), env={})
    foreign_cipher = m["firmsecrets"].vault_cipher(m["firmsecrets"].vault_folder(other / "data"), env={})
    try:
        foreign_cipher.decrypt(cipher.encrypt(b"synthetic-key-probe"))
        checks["r4_cross_key_refused"] = False
    except InvalidToken:
        checks["r4_cross_key_refused"] = True
    base = m["events"].base_path(root / "data")
    checks["own_ledger_nonempty"] = m["ledger_seal"].verify(base)["ok"] and m["ledger_seal"].verify(base)["rows"] > 0
    stage = root / "install/drill-cross-ledger"
    stage.mkdir()
    for path in m["events"].files(base):
        shutil.copy2(path, stage / path.name)
    cross_base = stage / base.name
    m["ledger_seal"].write_anchors(cross_base, m["ledger_seal"].read_anchors(m["events"].base_path(other / "data")))
    checks["foreign_ledger_seal_refused"] = not m["ledger_seal"].verify(cross_base)["ok"]
    phrases = (root / "install/backup_passphrase.txt").read_text(encoding="utf-8").splitlines()
    if not phrases or not phrases[0]:
        raise ValueError("Synthetic backup passphrase is missing.")
    phrase = phrases[0]
    archive = Path(read(root / "install/drill-artifacts.json")["archive"])
    other_archive = Path(read(other / "install/drill-artifacts.json")["archive"])
    from release_support import reject_links
    reject_links(archive); reject_links(other_archive)
    if not archive.resolve().is_relative_to(root / "install/drill-backups") or not other_archive.resolve().is_relative_to(other / "install/drill-backups"):
        raise ValueError("Drill archive pointer leaves its synthetic installation.")
    own_stage = root / "install/drill-restored"
    restored = m["backups"].restore_into(archive, phrase, own_stage)
    checks["own_backup_and_vault_restore"] = restored["ok"] and restored.get("vault") == "opens"
    restored_value = m["firmsecrets"].get("smtp.password", env={}, data_root=own_stage / "data")
    restored_profiles = [read(own_stage / "data/portal/clients" / case / "profile.json") for case in CASES]
    restored_text = json.dumps(restored_profiles)
    checks["restored_records_and_secret"] = restored_value == local["vault_value"] and info["marker"] in restored_text and foreign["marker"] not in restored_text and {p["id"] for p in restored_profiles} == set(CASES)
    checks["restored_ledger"] = m["ledger_seal"].verify(m["events"].base_path(own_stage / "data"))["ok"]
    try:
        m["backups"].restore_into(other_archive, phrase, root / "install/drill-foreign-restore")
        checks["cross_backup_passphrase_refused"] = False
    except m["backups"].BackupError:
        checks["cross_backup_passphrase_refused"] = True
    return {"firm": info["firm"], "checks": checks, "api_probes_passed": all(checks.values()),
            "pending": ["live HTTP/browser authorization and restricted screens", "service task identity and startup", "first reviewed packet and elapsed time", "machine posture and both running side by side"],
            "embedding": "offline hashing fixture; production semantic model acceptance pending"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=("plan", "start", "seed", "check", "packet", "_seed", "_artifacts", "_probe"))
    ap.add_argument("--root", type=Path, required=True, help="New parent for plan; drill parent for check; firm directory otherwise")
    ap.add_argument("--archive", type=Path)
    ap.add_argument("--public-key", type=Path)
    ap.add_argument("--other", type=Path)
    ap.add_argument("--packet", type=Path, help="Operator-reviewed packet.pdf under this synthetic case's folder")
    args = ap.parse_args()
    try:
        root = args.root.absolute()
        if args.action == "plan":
            if not args.archive or not args.public_key:
                raise ValueError("plan requires a signed archive and independently trusted public key.")
            result = plan(args.archive, args.public_key, root)
        elif args.action == "check":
            manifest = read(root / "drill.json")
            if manifest.get("kind") != "synthetic-two-firm-1":
                raise ValueError("Invalid drill manifest.")
            a, b = root / "firm-a", root / "firm-b"
            for firm in (a, b):
                child(firm, "_artifacts")
            result = {"firms": [child(a, "_probe", b), child(b, "_probe", a)], "live_acceptance": "pending"}
            save(root / "isolation-report.json", result)
        else:
            fixture(root)
            if args.action == "start":
                result = {"installer_started_at": datetime.now(timezone.utc).isoformat(), "machine": sys.platform, "manual_steps": "Record prerequisites, setup code, authenticator enrollment, portal hardening and packet review in drill notes."}
                save(root / "install/drill-start.json", result)
            elif args.action == "seed":
                result = child(root, "_seed")
            elif args.action == "_seed":
                result = seed(root)
            elif args.action == "_artifacts":
                result = artifacts(root)
            elif args.action == "_probe":
                if args.other is None:
                    raise ValueError("Probe requires the other synthetic firm.")
                result = probe(root, args.other.absolute())
            else:
                from release_support import reject_links
                if args.packet is None:
                    raise ValueError("Choose the actual reviewed packet.pdf.")
                reject_links(args.packet)
                packet = args.packet.resolve()
                if not packet.is_relative_to(root / "data/clients") or packet.name != "packet.pdf" or not packet.is_file() or not packet.with_suffix(".json").is_file():
                    raise ValueError("A real packet.pdf and packet.json under this synthetic installation are required.")
                start = read(root / "install/drill-start.json")
                now = datetime.now(timezone.utc)
                result = {"operator_recorded_at": now.isoformat(), "elapsed_seconds": (now - datetime.fromisoformat(start["installer_started_at"])).total_seconds(),
                          "packet_sha256": hashlib.sha256(packet.read_bytes()).hexdigest(), "packet_review": "operator must attach review notes; existence alone is not accuracy proof"}
                save(root / "install/drill-first-packet.json", result)
        print(json.dumps(result, indent=2))
        if args.action == "check" and not all(f["api_probes_passed"] for f in result["firms"]):
            return 1
        return 0
    except Exception as exc:
        print("Synthetic drill stopped: " + type(exc).__name__ + ". No isolation or onboarding pass claimed. Check docs/onboarding.md.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
