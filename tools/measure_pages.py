"""Times every list and page of the review app and the client portal on a made-up firm at size, and writes docs/scale.md.

    python tools/make_world.py --cases 2000 --out /path/to/world            (once: minutes; see docs/deployment.md, "1,800 cases")
    python tools/measure_pages.py --world /path/to/world                    time everything, write docs/scale.md
    python tools/measure_pages.py --world /path/to/world --requests 3 --label before --json before.json     a quick look, kept as a file

It starts the review app and the portal on the world (their own processes, on free ports, with only the world's files: nothing of this computer's data), signs
in as the world's attorney and paralegal, and asks each page the same way the screen does: 20 requests each by default, then p50 and p95. The first request of
a page is shown apart ("first"): it pays for whatever the app had not read yet. Every request reads the whole answer, and the size of the answer is shown.

The overnight run is timed on a world of its own with 200 cases (it rewrites their records from the scans, so it never runs on the one the pages were timed on).
The cases' records are made first, as the nightly run would have warmed them (the timelines and each case's row), so the pages are timed as the morning finds them.

The budgets (BUDGETS below) are the ones docs/design_plan.md H5 sets; scale.md marks each page against its own. tests/test_scale.py fails a page that is over its
budget by more than half, so a slow test machine does not fail the suite on noise.
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
import schema_path  # noqa: E402
sys.path.insert(0, str(REPO / "tools"))

BUDGET_LIST = 1.5  # every list and page, p95, seconds
BUDGETS = {"The overview counts": 0.5, "Search": 1.0, "A case page": 2.0}  # a page named here has its own; every other one has BUDGET_LIST
SEARCHES = ("passport", "custody affidavit", "Rosa Exemplo")
GIVEN_NAMES = ("Rosa", "Marcos", "Luana", "Tiago", "Beatriz", "Caio", "Marisol", "Joel", "Ines", "Davi", "Lia", "Mateo", "Sofia", "Elias", "Camila", "Renan", "Yara", "Nilo", "Alma", "Kauan", "Dalila")  # the names the conflict search is asked about (the made-up firm's own)


def budget_for(name: str) -> float:
    return next((b for key, b in BUDGETS.items() if name.startswith(key)), BUDGET_LIST)


def pages(sample: list[str]) -> list[dict]:
    """The pages timed: name, the route (a function of which case is asked about, request number), who may ask (attorney only pages are not asked as the paralegal).
    legacy: the route an app from before wave H5 answers (the same page, whole)."""
    case = lambda path: (lambda k: f"{path}client={sample[k % len(sample)]}")  # noqa: E731
    out = [
        {"name": "All clients (first page)", "path": lambda k: "/api/overview?page=1", "who": "both"},
        {"name": "The overview counts", "path": lambda k: "/api/overview?page=1&counts=1", "who": "both", "legacy": lambda k: "/api/overview"},
        {"name": "My work", "path": lambda k: "/api/work", "who": "both"},
        {"name": "What's due (deadlines)", "path": lambda k: "/api/deadlines?page=1", "who": "both", "legacy": lambda k: "/api/overview"},
        {"name": "What's due (expiring documents)", "path": lambda k: "/api/expiring?days=90&page=1", "who": "both"},
        {"name": "Reports", "path": lambda k: "/api/reports", "who": "both"},
    ]
    out += [{"name": f"Search ({text})", "path": (lambda k, t=text: "/api/search?q=" + t.replace(" ", "+")), "who": "both"} for text in SEARCHES]
    out += [
        {"name": "The inbox", "path": lambda k: "/api/inbox", "who": "both"},
        {"name": "The client list (the picker)", "path": lambda k: "/api/clients", "who": "both"},
        {"name": "A case page (review items)", "path": case("/api/items?"), "who": "both"},
        {"name": "A case page, Where it stands tab", "path": case("/api/journey?"), "who": "both"},
        {"name": "A case page, Documents tab", "path": case("/api/documents?"), "who": "both"},
        {"name": "A case page, Packet tab", "path": case("/api/packet?"), "who": "both"},
        {"name": "My approvals (the first page)", "path": lambda k: "/api/approvals?page=1", "who": "attorney", "new": True},
        {"name": "Today (the first page)", "path": lambda k: "/api/today?page=1", "who": "both", "new": True},
        {"name": "What staff did", "path": lambda k: "/api/staff_log", "who": "attorney"},
        {"name": "Who viewed what", "path": lambda k: "/api/views", "who": "attorney"},
        {"name": "What changed across the firm", "path": lambda k: "/api/firm_events", "who": "attorney"},
        # the pages of waves H2 and I2 (no \"before\": they are not in the version this wave began from)
        {"name": "What's due, Month view (the firm)", "path": lambda k: "/api/month?scope=firm", "who": "both", "new": True},
        {"name": "What's due, Month view (mine)", "path": lambda k: "/api/month?scope=me", "who": "both", "new": True},
        {"name": "Add a client: the conflict search", "path": lambda k: "/api/conflict-search", "who": "both", "new": True,
         "body": lambda k: {"reviewer": "Measure Person", "purpose": "add", "name": f"{GIVEN_NAMES[k % len(GIVEN_NAMES)]} Exemplo Souza"}},
    ]
    for kind, label in (("firm", "the firm's address"), ("person", "a person's own address")):
        out.append({"name": f"The calendar address ({label})", "path": None, "who": "attorney" if kind == "firm" else "both", "cookie": False, "new": True, "token": kind})
    return out


def port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ask(where: int, path: str, cookie: str, body: dict | None = None) -> tuple[float, int, int]:
    """(seconds, status, bytes) of one GET (or one POST, when there is a body), the whole answer read."""
    t = time.perf_counter()
    conn = http.client.HTTPConnection("127.0.0.1", where, timeout=900)
    if body is None:
        conn.request("GET", path, headers={"Cookie": cookie})
    else:
        conn.request("POST", path, body=json.dumps(body), headers={"Cookie": cookie, "Content-Type": "application/json", "X-Review-App": "1"})
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return time.perf_counter() - t, r.status, len(body)


def ask_text(where: int, path: str, cookie: str, body: dict) -> tuple[int, str]:
    """One POST, its status and its text."""
    conn = http.client.HTTPConnection("127.0.0.1", where, timeout=900)
    conn.request("POST", path, body=json.dumps(body), headers={"Cookie": cookie, "Content-Type": "application/json", "X-Review-App": "1"})
    r = conn.getresponse()
    text = r.read().decode("utf-8", "replace")
    conn.close()
    return r.status, text


def wait_for(where: int, seconds: float = 120) -> None:
    end = time.time() + seconds
    while time.time() < end:
        try:
            ask(where, "/", "")
            return
        except OSError:
            time.sleep(0.3)
    raise SystemExit(f"nothing answered on port {where}")


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


def machine(path: Path) -> dict:
    """The computer and the disk the world is on, from what the system says."""
    cpu, mem = platform.processor() or "", ""
    try:
        cpu = next(line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines() if line.startswith("model name"))
        mem = f"{round(int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemTotal'))) / 1048576)} GB"
    except (OSError, StopIteration):
        pass
    fs = "unknown"
    try:
        best = ""
        for line in Path("/proc/mounts").read_text().splitlines():
            dev, mount, kind = line.split()[:3]
            if str(path).startswith(mount.rstrip("/") + "/") or mount == "/":
                if len(mount) >= len(best):
                    best, fs = mount, kind
    except OSError:
        pass
    disk = {"ext4": "the computer's own Linux disk (ext4)", "9p": "a Windows disk reached from WSL (/mnt/c)", "drvfs": "a Windows disk reached from WSL (/mnt/c)",
            "virtiofs": "a Windows disk reached from WSL (/mnt/c)"}.get(fs, f"a {fs} disk")
    return {"cpu": cpu, "threads": os.cpu_count(), "memory": mem, "os": f"{platform.system()} {platform.release()}", "python": platform.python_version(), "disk": disk, "filesystem": fs}


def warm(world: dict, workers: int = 8, log=print, legacy: bool = False) -> float:
    """What a night leaves: each case's review row and timeline, built in a pool; and, for the app of wave H5 and after, the lists' own copy of every case
    (src/review/roster.py), saved for the app to adopt (the overnight run's own step). The app builds the rest as it is asked."""
    from concurrent.futures import ProcessPoolExecutor

    t = time.time()
    _night_steps_after = lambda: _index_and_query(world, log)  # noqa: E731 -- the overnight run's search index and query layer steps
    if not legacy:
        try:
            from review import roster
        except ImportError:
            roster = None
        if roster is not None:
            roster.warm(world["clients"], world["portal"], workers=workers)
            _night_steps_after()
            log(f"warmed {len(world['case_ids'])} cases (every row, the lists' copy, the search index and the query layer) in {time.time() - t:.0f} s")
            return time.time() - t
    ids = world["case_ids"]
    chunks = [ids[i::workers] for i in range(workers)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_warm_chunk, [(world["clients"], world["env"], c) for c in chunks]))
    _night_steps_after()
    log(f"warmed {len(ids)} cases (every row and timeline, the search index and the query layer) in {time.time() - t:.0f} s")
    return time.time() - t


def _index_and_query(world: dict, log) -> None:
    """The overnight run's last steps (src/overnight.py search_index, query_layer): the firm-wide document index and the query layer, built, as a night leaves them."""
    import index
    import query

    t = time.time()
    index.rebuild_changed(Path(world["clients"]), integrity=False)
    query.rebuild_changed(Path(world["clients"]))
    log(f"search index and query layer built in {time.time() - t:.0f} s")


def _warm_chunk(args: tuple) -> int:
    clients, env, ids = args
    os.environ.update(env)
    from fill import load_field_map
    from review import overview
    from review.state import Catalog

    field_map = load_field_map(schema_path.path("field_map", "i485"))
    template = schema_path.path("template", "i485")
    catalog = Catalog(field_map, template, [])
    for case in ids:
        d = Path(clients) / case
        overview.review_row(d, field_map, template, catalog)
        overview.journey_row(d)
    return len(ids)


def start_apps(world: dict, log, code: Path = REPO) -> tuple[subprocess.Popen, subprocess.Popen, int, int, Path]:
    env = {**os.environ, **world["env"], "PYTHONUNBUFFERED": "1"}
    review_port, portal_port = port(), port()
    env["PORTAL_BASE_URL"] = f"http://127.0.0.1:{portal_port}"
    logfile = Path(tempfile.mkdtemp(prefix="measure-")) / "servers.log"
    out = open(logfile, "w")
    review = subprocess.Popen([sys.executable, "src/review/server.py", "--data", world["clients"], "--port", str(review_port), "--users", world["users"], "--portal", world["portal"]],
                              cwd=code, env=env, stdout=out, stderr=subprocess.STDOUT)
    portal = subprocess.Popen([sys.executable, "-m", "uvicorn", "portal.app:app", "--app-dir", "src", "--port", str(portal_port), "--log-level", "warning"],
                              cwd=code, env=env, stdout=out, stderr=subprocess.STDOUT)
    wait_for(review_port)
    wait_for(portal_port)
    log(f"review app on {review_port}, portal on {portal_port} (log: {logfile})")
    return review, portal, review_port, portal_port, logfile


def peak_memory(proc: subprocess.Popen) -> str | None:
    try:
        for line in Path(f"/proc/{proc.pid}/status").read_text().splitlines():
            if line.startswith("VmHWM"):
                return f"{round(int(line.split()[1]) / 1024)} MB"
    except OSError:
        pass
    return None


def sessions(world: dict) -> dict[str, str]:
    """A signed-in cookie for each of the world's two people (the same sessions the sign-in makes, without the password's slow hash)."""
    from review.auth import COOKIE, Accounts

    accounts = Accounts(Path(world["users"]))
    return {role: f"{COOKIE}={accounts.session_for(person['email'], 'measure')[0]}" for role, person in world["people"].items()}


def time_pages(world: dict, review_port: int, requests: int, only: list[str] | None, log, legacy: bool) -> list[dict]:
    cookies = sessions(world)
    closed = set(world["restricted_ids"])
    open_ids = [c for c in world["case_ids"] if c not in closed]
    step = max(1, len(open_ids) // max(requests, 1))
    sample = open_ids[::step][: max(requests, 1)]  # distinct cases spread over the world, so no request is a cache hit by luck
    rows = []
    tokens: dict[tuple[str, str], str] = {}
    for spec in pages(sample):
        if only and not any(o.lower() in spec["name"].lower() for o in only):
            continue
        if legacy and spec.get("new"):
            continue
        for role in ("attorney", "paralegal"):
            if spec["who"] == "attorney" and role != "attorney":
                continue
            if spec.get("token"):  # a calendar address: made as this person, then asked for as a calendar program does, with no sign-in
                if (role, spec["token"]) not in tokens:
                    code, text = ask_text(review_port, "/api/calendar", cookies[role], {"action": "make", "kind": spec["token"]})
                    tokens[(role, spec["token"])] = json.loads(text)["path"] if code == 200 else "/calendar/none.ics"
                path = (lambda k, p=tokens[(role, spec["token"])]: p)
            else:
                path = spec.get("legacy", spec["path"]) if legacy else spec["path"]
            send = spec.get("body")
            cookie = "" if spec.get("cookie") is False else cookies[role]
            times, size, status = [], 0, 200
            first = None
            for k in range(requests + 1):
                seconds, code, n = ask(review_port, path(k), cookie, send(k) if send else None)
                if code != 200:
                    status = code
                if k == 0:
                    first = seconds
                    continue
                times.append(seconds)
                size = n
            row = {"name": spec["name"], "role": role, "requests": len(times), "first": round(first, 3), "p50": round(percentile(times, 0.5), 3), "p95": round(percentile(times, 0.95), 3),
                   "max": round(max(times), 3), "kb": round(size / 1024), "status": status}
            rows.append(row)
            log(f"{spec['name']:44} {role:9} first {row['first']:7.3f}  p50 {row['p50']:7.3f}  p95 {row['p95']:7.3f}  {row['kb']:6} KB  [{status}]")
    return rows


def time_portal(world: dict, portal_port: int, requests: int, log) -> list[dict]:
    """The portal's welcome page and one client's own page (what the phone asks after the link is opened)."""
    from portal.store import PortalStore

    store = PortalStore(Path(world["portal"]))
    client = next(c for c in world["case_ids"] if (Path(world["portal"]) / "clients" / c / "profile.json").exists())
    token = store.new_link_token(client)
    conn = http.client.HTTPConnection("127.0.0.1", portal_port, timeout=60)
    conn.request("GET", f"/l/{token}")
    r = conn.getresponse()
    r.read()
    cookie = (r.getheader("Set-Cookie") or "").split(";")[0]
    conn.close()
    out = []
    for name, path, kind in (("The portal's welcome page", "/", ""), ("The portal's case page (one client)", "/api/me", cookie)):
        times, size, status = [], 0, 200
        first = None
        for k in range(requests + 1):
            seconds, code, n = ask(portal_port, path, kind)
            status = code if code != 200 else status
            if k == 0:
                first = seconds
                continue
            times.append(seconds)
            size = n
        row = {"name": name, "role": "client", "requests": len(times), "first": round(first, 3), "p50": round(percentile(times, 0.5), 3), "p95": round(percentile(times, 0.95), 3),
               "max": round(max(times), 3), "kb": round(size / 1024), "status": status}
        out.append(row)
        log(f"{name:44} {'client':9} first {row['first']:7.3f}  p50 {row['p50']:7.3f}  p95 {row['p95']:7.3f}  {row['kb']:6} KB  [{status}]")
    return out


def time_reminders(world: dict, log) -> dict | None:
    """The overnight run's staff reminders step (src/staff_reminders.py) on the whole firm: once as the night leaves things (the timelines' copy the run has just saved), once
    with no copy, reading every case's own folder (what the step did before wave H5)."""
    try:
        import staff_reminders
    except ImportError:
        return None
    path = Path(world["data"]) / staff_reminders.FILE
    for person in world["people"].values():
        staff_reminders.set_consent(path, person["email"], True, person.get("name") or "")
    out: dict = {"cases": world["cases"]}
    kept = os.environ.get("I485_ROSTER")
    for key, env in (("seconds", kept), ("seconds_without_copy", str(Path(world["data"]) / "no-such-copy.json"))):
        if env:
            os.environ["I485_ROSTER"] = env
        t = time.perf_counter()
        line = staff_reminders.nightly(Path(world["clients"]), Path(world["data"]))
        out[key] = round(time.perf_counter() - t, 2)
        out["line"] = line
    if kept:
        os.environ["I485_ROSTER"] = kept
    else:
        os.environ.pop("I485_ROSTER", None)
    log(f"staff reminders step on {world['cases']} cases: {out['seconds']} s with the night's copy, {out['seconds_without_copy']} s reading every folder ({out['line']})")
    return out


def time_overnight(cases: int, workers: int, log, code: Path = REPO, where: Path | None = None) -> dict:
    """The overnight run on a world of its own with `cases` cases (their scans, no logs): every case read, then the timelines, the index and the query layer."""
    import make_world

    root = Path(tempfile.mkdtemp(prefix="measure-night-", dir=where)) / "w"
    world = make_world.build(root, cases=cases, views=0, ledger=0, restricted=max(1, cases // 7), staff=2, sources=cases, log=lambda *_: None)
    env = {**os.environ, **world["env"], "I485_CASE_STATUS": "0", "I485_NOTICE_INBOX": "0", "I485_CLIO": "0"}
    t = time.time()
    done = subprocess.run([sys.executable, "src/overnight.py", "--clients-root", world["inputs"], "--out", world["clients"], "--data", world["data"], "--workers", str(workers),
                           "--no-vision", "--all"], cwd=code, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    seconds = time.time() - t
    tail = [line for line in done.stdout.splitlines() if line.strip()][-12:]
    shutil.rmtree(root.parent, ignore_errors=True)
    log(f"overnight run on {cases} cases, {workers} at a time: {seconds:.0f} s (exit {done.returncode})")
    return {"cases": cases, "workers": workers, "seconds": round(seconds), "exit": done.returncode, "tail": tail}


def version() -> str:
    try:
        import version as v

        return getattr(v, "VERSION", "") or getattr(v, "__version__", "") or ""
    except Exception:  # noqa: BLE001
        return ""


def section(result: dict, before: dict | None) -> list[str]:
    """One disk's part of docs/scale.md: the computer, the table of times and, with `before`, the times from before the fixes beside the new ones."""
    m, meta = result["machine"], result["meta"]
    lines = [f"## On {m['disk']}", "",
             f"Measured {meta['date']} on {m['cpu']} ({m['threads']} threads, {m['memory']}), {m['os']}, Python {m['python']}. The made-up firm ({meta['cases']:,} cases, "
             f"{meta['restricted']} of them restricted, {meta['ledger']:,} ledger rows, {meta['views']:,} view-log rows, {meta['staff']} staff) was on this disk; version {meta['version']}. "
             f"Each page was asked {meta['requests']} time{'' if meta['requests'] == 1 else 's'} as an attorney and as a paralegal"
             + (f" (the figures from before the fixes: {before['meta']['requests']} time{'' if before['meta']['requests'] == 1 else 's'}, because a single ask took that long)" if before else "") + ".", ""]
    old = {(r["name"], r["role"]): r for r in (before or {}).get("pages", [])}
    head = "| Page | Who | Budget | " + ("Before p50 | Before p95 | " if old else "") + "p50 | p95 | First | Size | Within budget |"
    lines += [head, "|" + "---|" * (head.count("|") - 1)]
    for r in result["pages"]:
        b = budget_for(r["name"]) if r["role"] != "client" else BUDGET_LIST
        was = old.get((r["name"], r["role"]))
        cells = [r["name"], r["role"], f"{b:g} s"] + ([f"{was['p50']:.2f}", f"{was['p95']:.2f}"] if old and was else ["", ""] if old else []) \
            + [f"{r['p50']:.2f}", f"{r['p95']:.2f}", f"{r['first']:.2f}", f"{r['kb']:,} KB", "yes" if r["p95"] <= b else "NO"]
        lines.append("| " + " | ".join(cells) + " |")
    over = sorted({f"{r['name']} as the {r['role']}" for r in result["pages"] if r["role"] != "client" and r["status"] == 200 and r["p95"] > budget_for(r["name"])})
    if over:
        lines += ["", "Over its budget in this run (marked NO): " + "; ".join(over) + ". Recorded as it was measured; another run on the same computer, at another moment, gave a different figure for the same page "
                  "(the computer was shared with other work), so which is the page's own is not confirmed."]
    night = result.get("overnight")
    if night:
        was = (before or {}).get("overnight") or {}
        lines += ["", "The overnight run: " + f"{night['cases']} cases (each read from its scan with the vision model off, then the timelines, the search index and the query layer), "
                  f"{night['workers']} at a time: **{night['seconds']} seconds**" + (f" (before the fixes: {was['seconds']} seconds, {was['workers']} at a time)." if was else ".")]
    rem = result.get("reminders")
    if rem:
        lines += ["", f"The overnight run's staff reminders step, over the whole firm ({rem['cases']:,} cases): **{rem['seconds']} seconds** from the timelines the run has just saved, "
                  f"against {rem['seconds_without_copy']} seconds reading every case's own folder (what it did before wave H5)."]
    if result.get("server_memory"):
        lines += ["", f"The review app's own memory at the end of the run: {result['server_memory']}; the portal's: {result.get('portal_memory') or 'not measured'}."]
    if result.get("warm_seconds"):
        lines += ["", f"What a night leaves for the morning (every case's row and timeline, the lists' own copy, the search index and the query layer) took {result['warm_seconds']:.0f} seconds to build "
                  "from nothing on this computer, in a pool of processes: the overnight run does it, so a morning's first look is not the one that pays."]
    return lines


def render(results: list[tuple[dict, dict | None]]) -> str:
    """docs/scale.md: how the table is read, then one section for each disk measured (the computer, the date, the version, the times)."""
    lines = ["# How fast it is at 1,800 cases", "",
             "Every list and page of the review app and the client portal, timed on a made-up firm of 2,000 cases written by `tools/make_world.py` (see deployment.md, \"1,800 cases\") "
             "and timed by `tools/measure_pages.py`; a firm's IT person can run both on their own computer to see the same figures for their hardware. The table gives, in seconds, the "
             "median (p50) and the slowest one in twenty (p95) of the asks, and the first ask apart (\"First\": the first request after the app starts, which pays for whatever "
             "the night had not left it). The budgets are the ones design_plan.md sets: every list and page under "
             f"{BUDGET_LIST} seconds at the 95th percentile, the overview counts under 0.5, Search under 1, a case page under 2. All clients and What's due now show 50 rows a page; "
             "the \"before\" columns are the same pages as they were before wave H5 (the whole firm in one answer, found by walking every case's folder on each ask). The month view, the "
             "conflict search and the calendar addresses came after that version, so they have no \"before\". A calendar address (the file a calendar program asks for) is built once a minute "
             "for everyone who asks, so its \"First\" is the build and the figures beside it are the kept copy. The very first ask after a start also loads the saved copy of every case "
             "(the \"First\" of All clients: 2.2 seconds on the Linux disk, which is over the 1.5 second budget; the \"Within budget\" column judges the slowest one in twenty after that first ask). "
             "With no saved copy at all (a new install, or the first start after an update that changes what a row holds) the first ask walks every case in the app: 63 seconds at "
             "2,000 cases on the Linux disk, minutes on the Windows disk. The overnight run builds the copy so that no morning pays for it.", ""]
    for result, before in results:
        lines += section(result, before) + [""]
    lines += [OTHER_JOBS]
    return "\n".join(lines).rstrip() + "\n"


# Not a page: the firm's IT person's monthly job (tools/fill_audit.py, brief K5), timed by hand on the development computer (a made-up firm's Exemplo clones), not by this tool.
OTHER_JOBS = ("## The monthly audit of what the office changes\n\n"
              "`tools/fill_audit.py` fills two forms for every case that has a Save and compares them with the accuracy tool's engine: about 6 seconds a case on the development computer "
              "(12 made-up cases in 74 seconds, the computer shared with other work), so about 3 hours at 1,800 cases with a Save, one case at a time. Run it at night. As first written it held every case's "
              "forms in memory (about 0.5 GB at 1,800 cases); it now keeps only the changed boxes (not measured again) and writes a checkpoint every 25 cases, so a run that stopped goes on with `--resume`. "
              "The reviewers' Saves are counted by the overnight run from the decision log: under a second for 300 cases, about 5 seconds at 1,800.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--world", type=Path, help="a firm made by tools/make_world.py")
    ap.add_argument("--doc", nargs="+", metavar="AFTER[:BEFORE]", help="write docs/scale.md from measurement files (--json wrote them), one section each, and measure nothing")
    ap.add_argument("--requests", type=int, default=20)
    ap.add_argument("--only", action="append", help="only the pages whose name has this in it (repeatable)")
    ap.add_argument("--json", type=Path, help="keep the measurements as a file")
    ap.add_argument("--before", type=Path, help="measurements from before the fixes (a file --json wrote), shown beside the new ones")
    ap.add_argument("--out", type=Path, default=REPO / "docs" / "scale.md")
    ap.add_argument("--no-doc", action="store_true", help="do not write docs/scale.md")
    ap.add_argument("--overnight-cases", type=int, default=200, help="cases for the overnight run's timing (0: skip it)")
    ap.add_argument("--workers", type=int, default=0, help="cases at a time for the overnight run (0: the run's own choice for this computer)")
    ap.add_argument("--night-in", type=Path, help="the folder to build the overnight run's own firm in (where the world is, to time it on the same disk)")
    ap.add_argument("--legacy", action="store_true", help="an app from before wave H5: ask the pages as they were")
    ap.add_argument("--code", type=Path, default=REPO, help="the folder whose src/ the apps run from (a copy of an earlier version, for the \"before\" column)")
    ap.add_argument("--no-warm", action="store_true")
    ap.add_argument("--no-pages", action="store_true", help="time only the portal and the overnight run")
    ap.add_argument("--merge", action="store_true", help="with --json: add to the measurements already in that file (a page asked again replaces its earlier figures)")
    ap.add_argument("--label", default="")
    args = ap.parse_args(argv)
    if args.doc:
        pairs = [tuple(Path(x) for x in spec.split(":", 1)) if ":" in spec else (Path(spec),) for spec in args.doc]
        loaded = [(json.loads(a[0].read_text(encoding="utf-8")), json.loads(a[1].read_text(encoding="utf-8")) if len(a) > 1 else None) for a in pairs]
        args.out.write_text(render(loaded), encoding="utf-8")
        print(f"wrote {args.out}")
        return 0
    if not args.world:
        ap.error("say --world (to measure) or --doc (to write the page)")

    world = json.loads((args.world / "world.json").read_text(encoding="utf-8"))
    os.environ.update(world["env"])
    log = lambda text: print(text, flush=True)  # noqa: E731
    warm_seconds = None if args.no_warm else warm(world, log=log, legacy=args.legacy)
    review, portal, review_port, portal_port, logfile = start_apps(world, log, args.code.resolve())
    try:
        rows = [] if args.no_pages else time_pages(world, review_port, args.requests, args.only, log, args.legacy)
        rows += time_portal(world, portal_port, args.requests, log) if not args.only else []
        memory, portal_memory = peak_memory(review), peak_memory(portal)
    finally:
        review.terminate()
        portal.terminate()
    night = None
    reminders = time_reminders(world, log) if not (args.legacy or args.only or args.no_pages) else None
    if args.overnight_cases and not args.only:
        import overnight

        workers = args.workers or (1 if args.legacy else overnight.default_workers())  # before the fixes the run took one case at a time unless told otherwise
        night = time_overnight(args.overnight_cases, workers, log, args.code.resolve(), args.night_in or args.world.parent)
    result = {"meta": {"date": date.today().strftime("%m/%d/%Y"), "version": version(), "cases": world["cases"], "restricted": world["restricted"], "ledger": world["ledger"],
                       "views": world["views"], "staff": world["staff"], "requests": args.requests, "label": args.label},
              "machine": machine(args.world), "pages": rows, "overnight": night, "reminders": reminders, "server_memory": memory, "portal_memory": portal_memory, "warm_seconds": warm_seconds}
    if args.json and args.merge and args.json.exists():
        earlier = json.loads(args.json.read_text(encoding="utf-8"))
        again = {(r["name"], r["role"]) for r in rows}
        result["pages"] = [r for r in earlier["pages"] if (r["name"], r["role"]) not in again] + rows
        result["overnight"] = night or earlier.get("overnight")
        result["reminders"] = reminders or earlier.get("reminders")
        result["server_memory"] = memory or earlier.get("server_memory")
        result["portal_memory"] = portal_memory or earlier.get("portal_memory")
        result["warm_seconds"] = warm_seconds or earlier.get("warm_seconds")
        result["meta"] = earlier["meta"]
        rows = result["pages"]
    if args.json:
        args.json.write_text(json.dumps(result, indent=1), encoding="utf-8")
    if not args.no_doc:
        before = json.loads(args.before.read_text(encoding="utf-8")) if args.before else None
        args.out.write_text(render([(result, before)]), encoding="utf-8")
        print(f"wrote {args.out}")
    over = [r for r in rows if r["status"] == 200 and r["p95"] > (budget_for(r["name"]) if r["role"] != "client" else BUDGET_LIST)]
    print(f"{len(over)} page(s) over budget" + (": " + ", ".join(sorted({r['name'] for r in over})) if over else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
