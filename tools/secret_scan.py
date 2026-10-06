"""Generic source secret-pattern scanner. No embedded client fingerprints, attorney allowlists or private evidence paths. Pattern hits are heuristic and do not establish privacy clearance."""
from __future__ import annotations
import hashlib
import re
import subprocess
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
PATTERNS = Path(__file__).resolve().with_name('secret_patterns.txt')
MAX_BYTES = 5 * 1024 * 1024
SKIP_DIRS = {'.git', 'data', 'clients', '.venv', '.venv-wsl', 'venv', 'node_modules', '__pycache__', '.pytest_cache', '.ruff_cache', '.claude', '.ocr_tmp'}
SKIP_SUFFIXES = {'.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.zip', '.gz', '.xz', '.bz2', '.7z', '.db', '.sqlite', '.pyc', '.woff', '.woff2', '.ttf', '.otf', '.mp4', '.webm', '.mov', '.traineddata', '.bin', '.pt', '.onnx', '.pkl', '.joblib', '.npz', '.npy'}
ALLOW = 'secret-scan: allow'
BANNED = []
KNOWN = {}
WORD = re.compile('[a-z0-9]+')

def patterns(path: Path=PATTERNS) -> list[tuple[str, re.Pattern]]:
    out = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        name, regex = line.split(None, 1)
        out.append((name, re.compile(regex.strip())))
    return out

def _paths(root: Path) -> list[Path]:
    """What the scan covers: the files git holds (tracked and staged) when root is a checkout, else every file under root.
    A file nobody added to git is not scanned: it cannot reach a commit, and a scratch file a person keeps beside the
    checkout must not fail the firm's CI. Add a file first (git add), then scan."""
    try:
        out = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--cached'], capture_output=True, check=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return sorted(root.rglob('*'))
    return [root / name.decode('utf-8', errors='replace') for name in out.split(b'\x00') if name]

def _files(root: Path):
    for path in _paths(root):
        if not path.exists():
            continue
        rel = path.relative_to(root)
        if any((part in SKIP_DIRS or part.startswith('.venv') for part in rel.parts[:-1])) or not path.is_file():
            continue
        if path.suffix.lower() in SKIP_SUFFIXES or path.resolve() == PATTERNS:
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b'\x00' in data[:8192]:
            continue
        yield (rel, data.decode('utf-8', errors='replace'))

def _mask(text: str) -> str:
    return text[:4] + '...' if len(text) > 8 else '...'

def scan(root: Path=REPO, banned: list[tuple[int, str]] | None=None, pattern_list: list[tuple[str, re.Pattern]] | None=None, known: dict[str, set[str] | None] | None=None) -> list[dict]:
    """Every hit under root: {"file", "line", "what", "seen"} (seen: the start of the match, masked; for a banned name, nothing of it)."""
    banned = BANNED if banned is None else banned
    known = KNOWN if known is None else known
    pattern_list = patterns() if pattern_list is None else pattern_list
    by_length: dict[int, set[str]] = {}
    for length, digest in banned:
        by_length.setdefault(length, set()).add(digest)
    found, words_checked = ([], {})

    def banned_in(word: str) -> str | None:
        """The whole word's hash when a banned string is inside it, else None (each word worked out once for the whole tree)."""
        if word not in words_checked:
            inside = any((hashlib.sha256(word[i:i + length].encode()).hexdigest() in digests for length, digests in by_length.items() for i in range(0, len(word) - length + 1)))
            words_checked[word] = hashlib.sha256(word.encode()).hexdigest() if inside else None
        return words_checked[word]
    for rel, text in _files(Path(root)):
        where = rel.as_posix()
        for n, line in enumerate(text.splitlines(), 1):
            if ALLOW in line:
                continue
            for name, rx in pattern_list:
                m = rx.search(line)
                if m:
                    found.append({'file': where, 'line': n, 'what': name, 'seen': _mask(m.group(0))})
            for word in set(WORD.findall(line.lower())):
                digest = banned_in(word)
                if digest is not None and (not (digest in known and (known[digest] is None or where in known[digest]))):
                    found.append({'file': where, 'line': n, 'what': 'caller-supplied synthetic exclusion match', 'seen': ''})
    return found

def main(argv: list[str] | None=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else REPO
    hits = scan(root)
    for h in hits:
        print(f"{h['file']}:{h['line']}: {h['what']}" + (f" ({h['seen']})" if h['seen'] else ''))
    print(f'secret scan: {len(hits)} found' if hits else 'secret scan: nothing found')
    return 1 if hits else 0
if __name__ == '__main__':
    sys.exit(main())
