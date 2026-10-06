"""Build/verify releases; retain code rollback and stage verified data recovery.

No key is made here. Obtain the Ed25519 public key through the provider's
trusted channel before verification; a key included in an archive is not trusted.
Updates require all services stopped and installation-local stores/secrets.
"""
from __future__ import annotations
import argparse
import ast
import base64
from datetime import datetime, timezone
import fnmatch
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile
from install_support import PRODUCT, Problem, filtered_lock
PREFIXES = ('I485_', 'PORTAL_', 'CLIO_', 'SMTP_', 'TWILIO_', 'USCIS_', 'GOOGLE_', 'MSGRAPH_', 'MS_', 'FILEVINE_', 'OLLAMA_', 'VISION_', 'FIND_')
RUNTIME_KEYS = {'PORTAL_BASE_URL', 'PORTAL_TRUSTED_PROXY', 'PORTAL_READ_AT_ONCE', 'SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_FROM', 'TWILIO_SMS_FROM', 'TWILIO_WHATSAPP_FROM', 'TWILIO_ACCOUNT_SID', 'OLLAMA_URL', 'VISION_MODEL', 'FIND_MODEL'}

def environment(root: Path) -> dict[str, str]:
    root = root.resolve()
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(PREFIXES) and k.upper() != 'PYTHONPATH'}
    runtime = root / 'install/runtime.json'
    values = json.loads(runtime.read_text(encoding='utf-8')) if runtime.exists() else {}
    if not isinstance(values, dict) or set(values) - RUNTIME_KEYS or any((not isinstance(v, str) for v in values.values())):
        raise ValueError("install/runtime.json has unsupported settings. Store secrets in the firm's vault, not this file.")
    env.update(values)
    env.update({'I485_DEPLOYMENT': str(root / 'deployment.json'), 'I485_SETTINGS': str(root / 'data/settings.json'), 'I485_CASES': str(root / 'data/clients'), 'I485_CLIENTS_ROOT': str(root / 'clients'), 'PORTAL_DATA': str(root / 'data/portal'), 'I485_BACKUP_PASSPHRASE_FILE': str(root / 'install/backup_passphrase.txt')})
    return env
BINARY_PACKAGING_ENABLED = False
FORMAT = 'i485-release-1'
DOMAIN = b'i485-release-1\x00'
MAX_BYTES = 2000000000
MAX_FILES = 30000
META = 'release.json'
REQUIRED_FILES = frozenset({'src/version.py', 'src/records.py', 'src/review/server.py', 'src/portal/app.py', 'src/jobs.py', 'src/overnight.py', 'tools/run_install.py', 'tools/install_support.py', 'tools/release_support.py', 'tools/backup.py', 'requirements.lock', 'install.ps1', 'install.sh', 'update.ps1', 'update.sh', 'docs/releases.md'})

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    os.chmod(tmp, 384)
    os.replace(tmp, path)

def literals(path: Path) -> dict:
    """Read catalog constants without executing an incoming release's Python."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    values = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            names = node.targets if isinstance(node, ast.Assign) else [node.target]
            if len(names) != 1:
                continue
            if isinstance(names[0], (ast.Tuple, ast.List)):
                try:
                    parts = ast.literal_eval(node.value)
                    if len(parts) == len(names[0].elts):
                        for target, value in zip(names[0].elts, parts):
                            if isinstance(target, ast.Name):
                                values[target.id] = value
                except (ValueError, TypeError):
                    pass
                continue
            if not isinstance(names[0], ast.Name):
                continue
            expression = node.value
            if expression is None:
                continue

            class Replace(ast.NodeTransformer):

                def visit_Name(self, n):
                    return ast.copy_location(ast.Constant(values[n.id]), n) if n.id in values and isinstance(values[n.id], (str, int)) else n
            try:
                value = ast.literal_eval(Replace().visit(expression))
                if isinstance(node, ast.AugAssign):
                    if isinstance(node.op, ast.Add) and isinstance(values.get(names[0].id), list) and isinstance(value, list):
                        values[names[0].id] += value
                else:
                    values[names[0].id] = value
            except (ValueError, TypeError):
                continue
    return values

def version(root: Path) -> tuple[str, str]:
    values = literals(root / 'src/version.py')
    found = re.search('^## (\\d{4}\\.\\d+\\.\\d+) \\((\\d{4}-\\d{2}-\\d{2})\\)$', (root / 'docs/releases.md').read_text(encoding='utf-8'), re.M)
    result = (values.get('VERSION'), values.get('RELEASED'))
    if not found or result != found.groups():
        raise Problem('Executable version and newest release heading disagree.')
    return result

def catalog(root: Path) -> list[dict]:
    entries = literals(root / 'src/records.py').get('RECORDS')
    if not isinstance(entries, list) or not entries:
        raise Problem('The record catalog could not be read; no upgrade is supported.')
    return entries

def record_versions(root: Path) -> dict:
    return {r['id']: {'version': r.get('version'), 'versions': [v[0] for v in r.get('versions', [])]} for r in catalog(root)}

def safe_member(name: str) -> bool:
    p = PurePosixPath(name)
    return bool(name) and (not p.is_absolute()) and ('\\' not in name) and all((part not in ('', '.', '..') and ':' not in part and (part.rstrip(' .') == part) and (not re.match('^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\\.|$)', part, re.I)) for part in name.split('/')))

def product_files(root: Path) -> list[Path]:
    raise RuntimeError('Binary packaging is disabled for this source-only candidate. Review the exact release allowlist, LICENSE/NOTICE/third-party inclusion and new update channel before enabling it.')
    result = []
    for name in PRODUCT:
        path = root / name
        reject_links(path)
        if not path.exists():
            continue
        paths = walk_files(path) if path.is_dir() else [path]
        for p in paths:
            reject_links(p)
            if p.is_file() and '__pycache__' not in p.parts and (p.suffix != '.pyc'):
                if p.name.startswith('.env') or p.suffix.lower() in ('.key', '.pem', '.pfx', '.p12'):
                    raise Problem('A secret/key-shaped file was found in the product. Remove it before building.')
                result.append(p)
    return sorted(result)

def walk_files(root: Path):
    """Check each directory before entering it; never traverse a junction."""
    reject_links(root)
    pending = [root]
    while pending:
        folder = pending.pop()
        for path in sorted(folder.iterdir()):
            reject_links(path)
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                yield path

def require_product(names) -> None:
    if not REQUIRED_FILES.issubset(names):
        raise Problem('The release is missing required application, installer or recovery files.')

def reject_links(path: Path) -> None:
    """Reject symlinks and Windows junction/reparse ancestors before resolving."""
    for p in (path, *path.parents):
        if p.exists() or p.is_symlink():
            attrs = getattr(p.lstat(), 'st_file_attributes', 0)
            if p.is_symlink() or attrs & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024):
                raise Problem('A path contains a symlink or Windows reparse point; operator review is required.')

def build(root: Path, archive: Path, key: Path) -> dict:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    reject_links(root)
    reject_links(archive)
    root, archive, key = (root.resolve(), archive.resolve(), key.resolve())
    if key.is_relative_to(root):
        raise Problem('Keep the signing key outside the source/product tree.')
    sidecars = [archive.with_name(archive.name + suffix) for suffix in ('.sha256', '.sig')]
    if archive.is_relative_to(root) or any((p.exists() or p.is_symlink() for p in [archive, *sidecars])):
        raise Problem('Write the release to a new archive outside the source tree.')
    private = serialization.load_pem_private_key(key.read_bytes(), password=None)
    if not isinstance(private, Ed25519PrivateKey):
        raise Problem('The signing key must be an Ed25519 PEM private key.')
    v, day = version(root)
    paths = product_files(root)
    require_product({p.relative_to(root).as_posix() for p in paths})
    manifest = {'format': FORMAT, 'version': v, 'released': day, 'record_versions': record_versions(root), 'migration_policy': 'current_versions_only', 'files': {p.relative_to(root).as_posix(): {'sha256': digest(p), 'bytes': p.stat().st_size} for p in paths}}
    archive.parent.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        with archive.open('xb') as output:
            created.append(archive)
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zf:
                for p in paths:
                    zf.write(p, p.relative_to(root).as_posix())
                zf.writestr(META, json.dumps(manifest, sort_keys=True))
        sha = digest(archive)
        with sidecars[0].open('x', encoding='ascii') as stream:
            created.append(sidecars[0])
            stream.write(sha + '  ' + archive.name + '\n')
        with sidecars[1].open('xb') as stream:
            created.append(sidecars[1])
            stream.write(base64.b64encode(private.sign(DOMAIN + bytes.fromhex(sha))) + b'\n')
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return {'version': v, 'sha256': sha, 'files': len(paths), 'signed': True}

def verify(archive: Path, key: Path, destination: Path | None=None) -> dict:
    """Authenticate bytes before opening them; no release Python is imported."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    archive, key = (archive.resolve(), key.resolve())
    if archive.stat().st_size > MAX_BYTES:
        raise Problem('Release archive exceeds the supported size.')
    sha = digest(archive)
    checksum = archive.with_name(archive.name + '.sha256').read_text(encoding='ascii').split()
    if len(checksum) != 2 or checksum != [sha, archive.name]:
        raise Problem('The release checksum does not match.')
    public = serialization.load_pem_public_key(key.read_bytes())
    if not isinstance(public, Ed25519PublicKey):
        raise Problem('The trusted verification key must be Ed25519.')
    from cryptography.exceptions import InvalidSignature
    try:
        public.verify(base64.b64decode(archive.with_name(archive.name + '.sig').read_bytes().strip(), validate=True), DOMAIN + bytes.fromhex(sha))
    except InvalidSignature as exc:
        raise Problem('The release signature does not match the trusted public key.') from exc
    with zipfile.ZipFile(archive) as zf:
        entries = zf.infolist()
        names = [i.filename for i in entries]
        if len(entries) > MAX_FILES or sum((i.file_size for i in entries)) > MAX_BYTES or len(set((n.casefold() for n in names))) != len(names):
            raise Problem('Release archive has duplicate paths or exceeds its extraction budget.')
        if any((not safe_member(i.filename) or i.is_dir() or stat.S_ISLNK(i.external_attr >> 16) for i in entries)):
            raise Problem('Release contains an unsupported path or link.')
        if META not in names or zf.getinfo(META).file_size > 4000000:
            raise Problem('Release metadata is missing or too large.')
        manifest = json.loads(zf.read(META))
        listed = manifest.get('files')
        if manifest.get('format') != FORMAT or manifest.get('migration_policy') != 'current_versions_only' or (not isinstance(listed, dict)):
            raise Problem('Unsupported release format or migration policy.')
        if set(names) != set(listed) | {META} or any((PurePosixPath(n).parts[0] not in PRODUCT for n in listed)):
            raise Problem('The archive contains unlisted or non-product files.')
        require_product(set(listed))
        for name, expected in listed.items():
            raw = zf.read(name)
            if len(raw) != expected['bytes'] or hashlib.sha256(raw).hexdigest() != expected['sha256']:
                raise Problem('An archived file differs from the signed file list.')
        with tempfile.TemporaryDirectory(prefix='i485-verified-release-') as tmp:
            folder = Path(tmp)
            for name in listed:
                p = folder / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(zf.read(name))
            if version(folder) != (manifest.get('version'), manifest.get('released')) or record_versions(folder) != manifest.get('record_versions'):
                raise Problem('Signed release metadata disagrees with its version or record catalog.')
            if destination is not None:
                reject_links(destination)
                if destination.exists():
                    raise Problem('Extract into a new folder only.')
                shutil.copytree(folder, destination)
    return {'version': manifest['version'], 'sha256': sha, 'record_versions': manifest['record_versions'], 'verified': True}

def inspect_records(product: Path, data: Path) -> dict:
    """Validate known record shapes/versions, recording unchanged rows; no invented migrations."""
    entries = catalog(product)
    rows, unlisted = ([], 0)
    for p in sorted(walk_files(data)):
        if not p.is_file() or p.suffix not in ('.json', '.jsonl'):
            continue
        rel = p.relative_to(data).as_posix()
        parts = rel.split('/')
        area, match = ('case', '/'.join(parts[2:])) if len(parts) > 2 and parts[0] == 'clients' else ('portal', '/'.join(parts[3:])) if len(parts) > 3 and parts[:2] == ['portal', 'clients'] else ('firm', rel)
        r = next((r for r in entries if r.get('area') in ({area, 'logs'} if area == 'firm' else {area}) and any((fnmatch.fnmatchcase(match, pattern) for pattern in r.get('files', [])))), None)
        try:
            value = json.loads(p.read_text(encoding='utf-8')) if p.suffix == '.json' else [json.loads(line) for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]
        except (ValueError, UnicodeError):
            raise Problem('A JSON record is corrupt; repair it before upgrade (no client value is printed).') from None
        if r is None:
            candidates = value if p.suffix == '.jsonl' else [value]
            if any((isinstance(row, dict) and 'version' in row for row in candidates)):
                raise Problem('An uncatalogued versioned record needs an approved migration contract; upgrade refused.')
            unlisted += 1
            continue
        if 'JSON object' in r['format'] and (not isinstance(value, dict)):
            raise Problem(f"Record shape unsupported for catalog item {r['id']}.")
        expected = r.get('version')
        if expected is not None:
            candidates = value if p.suffix == '.jsonl' else [value]
            for row in candidates:
                actual = row.get('version', 1) if isinstance(row, dict) else None
                if type(actual) is not int or actual != expected:
                    raise Problem(f"No approved migration for catalog item {r['id']} to version {expected}; upgrade refused.")
        rows.append({'record': r['id'], 'file_sha256': hashlib.sha256(rel.encode()).hexdigest(), 'version': expected or 1, 'outcome': 'validated; no migration required'})
    return {'records': rows, 'unlisted_json': unlisted, 'applied_migrations': [], 'policy': 'current_versions_only'}

def import_old(root: Path, name: str):
    sys.path.insert(0, str(root / 'src'))
    spec = importlib.util.spec_from_file_location(name, root / 'src' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def installation(root: Path) -> dict:
    reject_links(root)
    root = root.resolve()
    if not (root / 'install/install.json').is_file() or root.is_symlink():
        raise Problem('Use a prepared installation with install/install.json.')
    inherited = [k for k in os.environ if k.upper().startswith(PREFIXES)]
    if inherited:
        raise Problem('Unset inherited product settings/credentials before update: ' + ', '.join(sorted(inherited)) + '. Configure installation-local runtime settings and the R4 vault.')
    for p in (root / 'data', root / 'clients', root / 'install', root / 'deployment.json'):
        reject_links(p)
        if not p.resolve().is_relative_to(root):
            raise Problem('This updater supports installation-local owned paths only.')
    config = json.loads((root / 'install/install.json').read_text(encoding='utf-8'))
    if not config.get('encrypted') or not (root / 'install/backup_passphrase.txt').is_file():
        raise Problem('Upgrade requires encrypted backups with a readable installation-local passphrase and keys.')
    return config

def backup_verified(root: Path, folder: Path) -> dict:
    backups = import_old(root, 'backups')
    phrase = (root / 'install/backup_passphrase.txt').read_text(encoding='utf-8').splitlines()[0]
    made = backups.make_backup(folder, root / 'data', documents=root / 'clients', portal=root / 'data/portal', passphrase=phrase, deployment=root / 'deployment.json', log_path=root / 'data/backup_log.json')
    if made['problems']:
        raise Problem('Pre-upgrade backup failed; product/data were not replaced.')
    report = backups.check_restore(made['archive'], phrase, work_parent=folder, log_path=root / 'data/backup_log.json')
    if not report['ok'] or report.get('vault') not in (None, 'opens'):
        raise Problem('Pre-upgrade restore/vault verification failed; product/data were not replaced.')
    ledger = import_old(root, 'ledger_seal').verify(import_old(root, 'events').base_path(root / 'data'))
    if not ledger['ok']:
        raise Problem('The ledger is not intact; upgrade refused.')
    return {'archive': str(made['archive']), 'sha256': digest(made['archive']), 'verified': True, 'vault': report.get('vault'), 'ledger_intact': True}

def copy_product(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for p in product_files(source):
        dest = destination / p.relative_to(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)

def replace_product(root: Path, source: Path) -> None:
    product_files(source)
    for name in PRODUCT:
        target = root / name
        reject_links(target)
        if not target.resolve().is_relative_to(root):
            raise Problem('Product replacement path is outside this installation.')
        if target.exists():
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        incoming = source / name
        if incoming.exists():
            shutil.copytree(incoming, target) if incoming.is_dir() else shutil.copy2(incoming, target)

def upgrade(root: Path, archive: Path, key: Path, python: str, stopped: bool, skip_pip: bool) -> dict:
    if not stopped:
        raise Problem('Stop review, portal, worker, overnight and backup services first; confirm with --services-stopped.')
    installation(root)
    root = root.resolve()
    version(root)
    verified = verify(archive, key)
    if tuple(map(int, verified['version'].split('.'))) <= tuple(map(int, version(root)[0].split('.'))):
        raise Problem('Upgrade requires a newer release. Use the retained generation for rollback.')
    generation = root / 'install/updates' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
    generation.mkdir(parents=True)
    os.chmod(generation, 448)
    report = {'format': FORMAT, 'root': str(root), 'before': version(root)[0], 'after': verified['version'], 'state': 'preparing', 'release': verified}
    try:
        incoming = generation / 'incoming'
        verify(archive, key, incoming)
        report['backup'] = backup_verified(root, generation / 'backup')
        report['records'] = inspect_records(incoming, root / 'data')
        copy_product(root, generation / 'code')
        pointer = root / 'install/active_environment.json'
        report['previous_environment'] = json.loads(pointer.read_text(encoding='utf-8')) if pointer.exists() else None
        write_json(generation / 'upgrade.json', report)
        platform = 'windows' if os.name == 'nt' else 'linux'
        if skip_pip:
            if filtered_lock(root / 'requirements.lock', platform) != filtered_lock(incoming / 'requirements.lock', platform):
                raise Problem('--skip-pip requires identical installed and incoming package locks.')
            new_python = python
        else:
            env_dir = root / 'install/environments' / generation.name
            child_env = environment(root)
            subprocess.run([python, '-m', 'venv', str(env_dir)], check=True, env=child_env, capture_output=True)
            new_python = str(env_dir / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))
            req = generation / 'requirements.txt'
            req.write_text(filtered_lock(incoming / 'requirements.lock', platform), encoding='utf-8')
            subprocess.run([new_python, '-m', 'pip', 'install', '--no-deps', '-r', str(req)], check=True, env=child_env, capture_output=True)
        report['state'] = 'replacing_code'
        write_json(generation / 'upgrade.json', report)
        replace_product(root, incoming)
        write_json(root / 'install/active_environment.json', {'python': new_python})
        report['state'] = 'installed; operator startup verification pending'
        write_json(generation / 'upgrade.json', report)
        return {'version': verified['version'], 'generation': str(generation), 'backup_verified': True, 'migration': 'no approved version transitions required', 'startup_verified': False, 'recovery': 'code-rollback restores code/environment; data-stage writes verified data recovery to a new folder'}
    except Exception:
        if report['state'] == 'replacing_code':
            replace_product(root, generation / 'code')
            previous = report.get('previous_environment')
            if previous is None:
                (root / 'install/active_environment.json').unlink(missing_ok=True)
            else:
                write_json(root / 'install/active_environment.json', previous)
        report['state'] = 'failed; no data rollback performed'
        write_json(generation / 'upgrade.json', report)
        raise

def recovery(root: Path, generation: Path, destination: Path | None, stopped: bool) -> dict:
    reject_links(root)
    reject_links(generation)
    root, generation = (root.resolve(), generation.resolve())
    if not stopped:
        raise Problem('Stop all installation services and confirm --services-stopped.')
    if not generation.is_relative_to(root / 'install/updates') or generation.is_symlink():
        raise Problem("Choose this installation's retained update generation.")
    report = json.loads((generation / 'upgrade.json').read_text(encoding='utf-8'))
    if report.get('root') != str(root) or not report.get('backup', {}).get('verified'):
        raise Problem('This generation has no verified backup for this installation.')
    if destination is None:
        if not (generation / 'code/src/version.py').exists():
            raise Problem('This generation has no previous product copy.')
        replace_product(root, generation / 'code')
        previous = report.get('previous_environment')
        if previous is None:
            (root / 'install/active_environment.json').unlink(missing_ok=True)
        else:
            write_json(root / 'install/active_environment.json', previous)
        return {'code_rolled_back': True, 'version': report['before'], 'data_restored': False, 'next': 'data-stage if data recovery is needed; startup verification remains required'}
    reject_links(destination)
    destination = destination.resolve()
    if destination.exists() or destination.is_relative_to(root / 'data') or destination.is_relative_to(root / 'clients'):
        raise Problem('Stage recovered firm data into a new folder outside the live stores.')
    archive = Path(report['backup']['archive'])
    if not archive.resolve().is_relative_to(generation) or digest(archive) != report['backup']['sha256']:
        raise Problem('Retained backup is missing or has changed.')
    backups = import_old(root, 'backups')
    phrases = (root / 'install/backup_passphrase.txt').read_text(encoding='utf-8').splitlines()
    restored = backups.restore_into(archive, phrases, destination)
    if not restored['ok'] or restored.get('vault') not in (None, 'opens'):
        raise Problem('Staged restore failed verification. Inspect the stage; live stores were untouched.')
    if not import_old(root, 'ledger_seal').verify(import_old(root, 'events').base_path(destination / 'data'))['ok']:
        raise Problem('Restored ledger verification failed; live stores were untouched.')
    return {'staged': str(destination), 'verified': True, 'live_data_replaced': False, 'next': 'Follow docs/onboarding.md to preserve live stores and promote the stage while services remain stopped.'}

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build')
    b.add_argument('--root', type=Path, required=True)
    b.add_argument('--archive', type=Path, required=True)
    b.add_argument('--signing-key', type=Path, required=True)
    v = sub.add_parser('verify')
    v.add_argument('archive', type=Path)
    v.add_argument('--public-key', type=Path, required=True)
    v.add_argument('--extract-to', type=Path)
    u = sub.add_parser('upgrade')
    u.add_argument('archive', type=Path)
    u.add_argument('--public-key', type=Path, required=True)
    u.add_argument('--root', type=Path, required=True)
    u.add_argument('--python', default=sys.executable)
    u.add_argument('--skip-pip', action='store_true')
    u.add_argument('--services-stopped', action='store_true')
    for name in ('code-rollback', 'data-stage'):
        p = sub.add_parser(name)
        p.add_argument('generation', type=Path)
        p.add_argument('--root', type=Path, required=True)
        p.add_argument('--services-stopped', action='store_true')
        if name == 'data-stage':
            p.add_argument('--into', type=Path, required=True)
    args = ap.parse_args()
    try:
        if args.command == 'build':
            result = build(args.root, args.archive, args.signing_key)
        elif args.command == 'verify':
            result = verify(args.archive, args.public_key, args.extract_to)
        elif args.command == 'upgrade':
            result = upgrade(args.root, args.archive, args.public_key, args.python, args.services_stopped, args.skip_pip)
        else:
            result = recovery(args.root, args.generation, getattr(args, 'into', None), args.services_stopped)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        print('Release operation stopped: ' + (str(exc) if isinstance(exc, Problem) else type(exc).__name__) + '. No signature or startup proof is claimed.', file=sys.stderr)
        return 2
if __name__ == '__main__':
    raise SystemExit(main())
