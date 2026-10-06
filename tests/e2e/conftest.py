"""The end-to-end tests drive the real review app and portal in a browser (Playwright), on a made-up world
(tests/e2e/world.py). They start servers and take minutes, so they run only when asked:

    E2E=1 .venv-wsl/bin/python -m pytest tests/e2e -q          # E2E_SHOTS=<folder> keeps a screenshot of every page

Needs `pip install playwright` and `python -m playwright install chromium`.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
import schema_path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

if not os.environ.get("E2E"):
    collect_ignore_glob = ["test_*.py"]  # the normal test run stays fast

# How long a test waits for the app before it calls a step failed (ms). A packet build or a refilled I-485 is 5 to 15 seconds of the app's own work on a quiet machine
# (flake pass 2: POST /api/packet, POST /api/absence, the first Documents tab of a case with a foreign paper), several times that in a full run at a load of 15 to 30:
# the old 30 s ceiling failed a step that was still working. The wait is for the thing itself (an element, a toast), so a passing test never waits longer.
SLOW = 120_000

_PORTAL = {"origin": ""}  # this worker's portal (set by the world fixture): the browser fixture gives each tab an address of its own there

LEAK = re.compile(r"\bnull\b|\bundefined\b|\bNaN\b|\[object Object\]")


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, seconds: float = 30) -> None:
    end = time.time() + seconds
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:  # noqa: BLE001 -- not up yet
            time.sleep(0.3)
    raise RuntimeError(f"{url} didn't start")


@pytest.fixture(scope="session")
def world(tmp_path_factory):
    import world as w

    root = tmp_path_factory.mktemp("world")
    info = w.build(root / "w")
    # the world's attorney has already been shown Getting started (it opens by itself on an attorney's first sign-in, src/getting_started.py);
    # test_getting_started.py makes attorneys of its own who have not
    (root / "getting_started.json").write_text(json.dumps({"seen": {w.ATTORNEY[0]: "2026-10-01T09:00:00-04:00"}}), encoding="utf-8")
    register = root / "maintenance.json"  # "Mark checked" writes here, never to the repo's register
    register.write_bytes((schema_path.path("register", "maintenance")).read_bytes())
    review_port, portal_port = _port(), _port()
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PORTAL_TRUSTED_PROXY": "127.0.0.1",  # (see the browser fixture: each tab has an address of its own)
           "PORTAL_DATA": str(info["portal"]), "PORTAL_BASE_URL": f"http://127.0.0.1:{portal_port}",
           "I485_SHADOW": "0", "I485_LIVE_CHECKS": "0", "I485_MAINTENANCE": str(register),
           "I485_LIVE_STATUS": str(root / "maintenance_status.json"),  # last night's check of the official sources: a test writes its own result here
           "I485_SETTINGS": str(root / "settings.json"), "I485_MAINTENANCE_LOG": str(root / "maintenance_log.json"),
           "I485_DEPLOYMENT": str(root / "deployment.json"),  # the Settings page saves here, never where the unit tests read
           "I485_RULES_APPROVED": str(root / "rules_approved.json"),  # a rule's approval for every case (src/rules/approval.py)
           "I485_POLICIES_FIRM": str(root / "policies_firm.json"),  # the attorney's edits to the firm's policies (src/rules/firm_policies.py)
           "I485_POSTURE": str(root / "posture.json"), "I485_POSTURE_CHECKS": "0",  # the machine's posture (src/posture.py): a test writes its own reading from recorded outputs
           "I485_BACKUP_LOG": str(root / "backup_log.json"),  # the last backup and test restore (src/backups.py)
           "I485_EVENTS": str(root / "events.jsonl"),  # the event ledger (src/events.py): one ledger for the world's cases and its firm records
           "I485_QUERY_DB": str(root / "query.db"),  # the query layer (src/query.py)
           "I485_QUERY_REFRESH": "0",  # brought up to date on every ask, not once a minute
           "I485_JOBS": str(root / "jobs"),  # the job queue (src/jobs.py): the app starts its own worker, as it does on a laptop with no installed one
           "I485_JOBS_WORKER": "1",  # (the unit tests turn it off for their own processes: the servers here are the real thing)
           "I485_ROSTER": str(root / "roster.json"),
           "I485_ROSTER_GAP": "0",  # the lists look at the ledger on every ask, so a test that changes a case and loads a page at once is not a half second early (as installed: at most twice a second)
           "I485_ROSTER_BUDGET": "60",  # and every case a test changed is read before the answer, however long that takes (as installed: 1.5 seconds, the rest in the background)
           "I485_INDEX": str(root / "index.db"),  # the firm-wide document index (src/index.py): built by the first search
           "I485_FIND_EMBEDDER": "hashing",  # Find across the firm (src/find.py): the deterministic embedder, never a model; its index is find.db beside the case folders
           "I485_INBOX": str(root / "inbox"),  # the notice inbox (src/inbox.py): a test drops a scan here
           "I485_ACCURACY": "0", "I485_ACCURACY_HISTORY": str(root / "accuracy_history.jsonl"),  # the accuracy record (src/accuracy.py): a test writes its own
           "I485_REFERENCE": str(root / "reference"),  # the firm's hand-filled references: a test puts one here
           "I485_AUDIT": "0", "I485_AUDIT_FILL": str(root / "audit_fill.json"),  # what the office changes (src/audit_fill.py): a test writes its own catalog
           "I485_CASES": str(info["clients"])}  # the portal's messages check these case folders for a restricted case (src/portal/notify.py)
    log = open(root / "servers.log", "w")
    review = subprocess.Popen([sys.executable, "src/review/server.py", "--data", str(info["clients"]), "--port", str(review_port),
                               "--users", str(info["users"]), "--portal", str(info["portal"])], cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    portal = subprocess.Popen([sys.executable, "-m", "uvicorn", "portal.app:app", "--app-dir", "src", "--port", str(portal_port), "--log-level", "warning"],
                              cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        _wait(f"http://127.0.0.1:{review_port}/")
        _wait(f"http://127.0.0.1:{portal_port}/")
        _PORTAL["origin"] = f"http://127.0.0.1:{portal_port}"
        yield info | {"register": register, "live": root / "maintenance_status.json", "review": f"http://127.0.0.1:{review_port}/", "portal_url": f"http://127.0.0.1:{portal_port}", "env": env, "log": root / "servers.log",
                      "world": w}
    finally:
        review.terminate()
        portal.terminate()
        log.close()
    log_text = (root / "servers.log").read_text(errors="replace")
    errors = [block[:1500] for block in log_text.split("Traceback (most recent call last):")[1:]
              if "BrokenPipeError" not in block and "ConnectionResetError" not in block]  # a browser closing mid-download isn't the app's fault
    assert not errors, "the servers logged errors:\n" + "\n---\n".join(errors)


@pytest.fixture
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None, chromium_sandbox=True, args=["--enable-automation"])
        if os.environ.get("E2E_SHOTS"):
            directory = Path(os.environ["E2E_SHOTS"])
            directory.mkdir(parents=True, exist_ok=True)
            session = b.new_browser_cdp_session()
            (directory / "e2e-chromium-launch.json").write_text(json.dumps(session.send("Browser.getBrowserCommandLine"), indent=2))
            session.detach()
        # Every browser tab reaches the portal from 127.0.0.1, and the portal's allowance for sign-in links (30 an hour) and for asking for a link (120 an hour) is per
        # address: one worker's thirty-odd portal tests share one allowance, which a full run spends ("too many attempts", the busy page). The portal's own setting for a
        # proxy in front of it (PORTAL_TRUSTED_PROXY, in the servers' env above) believes the first address in X-Forwarded-For: each tab (each browser context) gets an address of
        # its own, as a phone on its own network would, and the portal's limits stay exactly as shipped.
        made = iter(range(1, 1 << 20))
        new_context = b.new_context

        def new_context_on_its_own_address(**kw):
            n = next(made)
            ctx = new_context(**kw)
            ctx.set_default_timeout(SLOW)  # every tab, the clients' phones too (see SLOW)
            if _PORTAL["origin"]:  # only the portal's requests: the review app reads a forwarding header as a proxy in front (its setup page is then not shown to "the computer itself")
                address = f"10.{n >> 16 & 255}.{n >> 8 & 255}.{n & 255}"
                ctx.route(_PORTAL["origin"] + "/**", lambda route: route.continue_(headers={**route.request.headers, "x-forwarded-for": address}))
            return ctx

        b.new_context = new_context_on_its_own_address
        yield b
        b.close()


def code_for(world, email: str) -> str:
    """The code a person's authenticator app shows (world["secrets"]), for the earliest step the server still takes: later than
    the last one used for that account (a code works once) and at most one step ahead of now (waits when it must)."""
    from review import totp

    last = world.setdefault("last_steps", {}).get(email, -1)
    while int(time.time() // totp.STEP) + 1 <= last:
        time.sleep(1)
    step = max(int(time.time() // totp.STEP), last + 1)
    world["last_steps"][email] = step
    return totp.hotp(world["secrets"][email], step)


_SEEN: dict[str, dict[str, int]] = {}
_LEDGER: dict[str, dict] = {}  # per world: how far this process has read the ledger, and when each case was last named in it
_CACHES = {"overview.json", "journey_summary.json", "confidentiality.json"}  # what the app rewrites when it reads a case: not a change anyone made


def _last_named(world) -> dict[str, int]:
    """{case id: the time (ns since the epoch) of the latest ledger row naming it}, read from the world's ledger a piece at a time."""
    from datetime import datetime

    base = Path(world["env"]["I485_EVENTS"])
    state = _LEDGER.setdefault(str(base), {"offsets": {}, "last": {}})
    for path in sorted(base.parent.glob(base.stem + "-*" + base.suffix)):
        offset = state["offsets"].get(path.name, 0)
        with open(path, "rb") as f:
            f.seek(offset)
            for raw in f:
                if not raw.endswith(b"\n"):
                    break
                offset += len(raw)
                try:
                    row = json.loads(raw)
                    when = int(datetime.fromisoformat(row["at"]).timestamp() * 1e9)
                except (ValueError, KeyError, TypeError):
                    continue
                if row.get("case"):
                    state["last"][row["case"]] = max(state["last"].get(row["case"], 0), when)
        state["offsets"][path.name] = offset
    return state["last"]


def tell_the_app(world) -> list[str]:
    """The tests change case files by hand (a cloned case, a document record written straight to the folder) and then look at a screen. As installed the lists, the search index and
    the query layer follow the event ledger and the app's own writes, and a change nobody recorded shows at the next walk, ten minutes on. So a case whose files changed since the last
    look gets one ledger row, as a person's own change would: the app reads that case again before it answers. Returns the cases told."""
    clients = Path(world["clients"])
    seen = _SEEN.setdefault(str(clients), {})
    changed = []
    stamps: dict[str, int] = {}
    portal = Path(world["portal"]) / "clients"
    for root_folder in (clients, portal):  # the case's folder and the client's folder in the portal: one case id
        for d in sorted(p for p in root_folder.iterdir() if p.is_dir()) if root_folder.is_dir() else ():
            newest = stamps.get(d.name, 0)
            for root, _dirs, files in os.walk(d):
                for name in files:
                    if name in _CACHES or name.endswith((".tmp", ".part", ".lock")):
                        continue
                    try:
                        newest = max(newest, os.stat(os.path.join(root, name)).st_mtime_ns)
                    except OSError:
                        pass
            stamps[d.name] = newest
    named = _last_named(world)
    for name, newest in stamps.items():
        if seen.get(name) != newest:
            seen[name] = newest
            if named.get(name, 0) < newest:  # a ledger row at or after the last change is the app's own: it has said so already
                changed.append(name)
    if changed:
        sys.path.insert(0, str(REPO / "src"))
        import events

        kept = os.environ.get("I485_EVENTS")
        os.environ["I485_EVENTS"] = str(world["env"]["I485_EVENTS"])  # the world's ledger, not the unit tests'
        try:
            for case in changed:
                events.record("documents", "changed", "A file was changed outside the app", case=case, home=clients.parent, who="The test", role="system")
        finally:
            if kept is None:
                os.environ.pop("I485_EVENTS", None)
            else:
                os.environ["I485_EVENTS"] = kept
    return changed


@contextlib.contextmanager
def paralegal_not_named_on(world, case: str):
    """The case as the world was built: the paralegal is not named on it. Another file of this worker may have named her (a journey through the asylum case, an inbox
    test) and a test that counts what she may not see must not depend on which file ran first; she is named again afterwards if she was."""
    import world as w  # (puts src on the path)

    import restricted

    folder =Path(world["clients"]) / case
    named = any(p.get("email") == w.PARALEGAL[0] for p in restricted.record(folder)["people"])
    if named:
        restricted.name_person(folder, w.PARALEGAL[0], False, w.ATTORNEY[1], "attorney")
    try:
        yield
    finally:
        if named:
            restricted.name_person(folder, w.PARALEGAL[0], True, w.ATTORNEY[1], "attorney", w.PARALEGAL[1])


@pytest.fixture
def asylum_closed_to_the_paralegal(world):
    with paralegal_not_named_on(world, "case-asylum"):
        yield


def remembered(ctx, world, who) -> None:
    """The attorney's computer, remembered after a code (review/auth.py): their password signs them in with no code for 30 days."""
    device = (world.get("devices") or {}).get(who[0])
    if device:
        ctx.add_cookies([{"name": "review_device", "value": device, "domain": "127.0.0.1", "path": "/api/login"}])


def after_password(page, ctx, world, who) -> None:
    """After "Sign in": the work list, or (a computer not remembered, or a test turned remembering off) the code, remembered again."""
    for _ in range(3):
        try:
            page.locator("#client:visible, input[name=code], form .err:not(:empty)").first.wait_for()
        except Exception as exc:  # say what the sign-in screen said instead
            raise AssertionError(f"{who[0]} not signed in: " + page.locator("#main").inner_text()[-300:]) from exc
        if "Too many sign-in attempts" not in " ".join(page.locator("form .err").all_inner_texts()):
            break
        # the app's own limit (20 sign-ins a minute from one address, src/review/server.py ATTEMPTS): every test signs in from
        # 127.0.0.1, so a quick run of tests can meet it; wait it out as a person would, then sign in again
        time.sleep(61)
        page.get_by_role("button", name="Sign in").click()
    said = " ".join(page.locator("form .err").all_inner_texts()).strip()
    if said and not page.locator("input[name=code]").count():
        raise AssertionError(f"{who[0]} not signed in: {said}")
    if page.locator("input[name=code]").count():
        page.fill("input[name=code]", code_for(world, who[0]))
        page.locator("input[name=remember]").check()
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_selector("#client", state="visible")
        world["devices"][who[0]] = next(c["value"] for c in ctx.cookies() if c["name"] == "review_device")
    page.wait_for_selector("#client", state="visible")


def wait_until_searchable(world, screen, word: str, seconds: float = 90) -> None:
    """The review app looks for new document records at most once a minute (index.refresh): an earlier search on this worker's
    world may have just used that minute, so records a test has just written are not yet in the index. Searches the way a person
    does (each search asks the app to refresh, which it does once the minute is up) until the word is found."""
    end = time.time() + seconds
    while time.time() < end:
        tell_the_app(world)  # the records were written by hand: one ledger row for each case that changed, as a person's own change would leave
        found = screen.page.request.get(world["review"].rstrip("/") + "/api/search?q=" + word).json()
        if found.get("total"):
            return
        time.sleep(2)
    raise AssertionError(f"the search index never found {word!r} in {seconds:.0f} seconds")


def press(page, name: str = "Sign in", tries: int = 4) -> None:
    """Presses a sign-in screen's button (Sign in, Save and continue, Confirm and continue) and, when the app answers with its own limit
    (20 a minute from one address per step, src/review/server.py ATTEMPTS: every test signs in from 127.0.0.1, so a quick run of tests
    meets it), waits it out as a person would and presses again. after_password does the same for the shared sign-in; a test that
    walks the sign-in screens itself (test_admin.py) presses through this. Any other answer is left on the screen for the test to check."""
    for _ in range(tries):
        button = page.get_by_role("button", name=name)
        form = button.evaluate_handle("b => b.form")
        button.click()
        try:  # the screen changes (the form is gone) or the form says something
            page.wait_for_function("f => { const e = f.querySelector('.err'); return !f.isConnected || (e && e.textContent.trim() !== ''); }", arg=form, timeout=15000)
        except Exception:  # noqa: BLE001 -- still working (a slow machine): the test's own wait speaks next
            return
        if not form.evaluate("f => f.isConnected && f.querySelector('.err').textContent.includes('Too many sign-in attempts')"):
            return
        time.sleep(61)


class Screen:
    """A signed-in browser tab on the review app, which fails the test on any page error or leaked null/undefined."""

    def __init__(self, browser, world, who, color_scheme: str = "light"):
        self.world, self.who = world, who
        self.ctx = browser.new_context(viewport={"width": 1400, "height": 900}, color_scheme=color_scheme)
        self.page = self.ctx.new_page()
        self.errors: list[str] = []
        self.page.on("pageerror", lambda e: self.errors.append(f"page error: {e}"))
        self.page.on("console", lambda m: self.errors.append(f"console: {m.text}") if m.type == "error" and "400" not in m.text else None)
        self.page.on("dialog", lambda d: d.accept(self.answer_dialogs or ""))
        self.answer_dialogs: str | None = None
        self.shots = Path(os.environ["E2E_SHOTS"]) if os.environ.get("E2E_SHOTS") else None
        remembered(self.ctx, world, who)
        goto = self.page.goto

        def goto_after_telling_the_app(url, **kw):
            changed = tell_the_app(world)  # a page load asks the app for its lists: it must know what the test changed by hand
            if self._signed_in:
                if changed:
                    self.wait_listed(changed)
            else:
                self._told += changed  # changed before this screen could ask (a fixture cloned a case): listed once signed in
            return goto(url, **kw)

        self._signed_in = False
        self._told: list[str] = []
        self.page.goto = goto_after_telling_the_app
        self.page.goto(world["review"])
        self.page.fill("input[name=email]", who[0])
        self.page.fill("input[name=password]", who[2])
        self.page.get_by_role("button", name="Sign in").click()
        after_password(self.page, self.ctx, world, who)
        self.errors[:] = [e for e in self.errors if "429" not in e]  # a sign-in that waited out the limit (after_password) is not the page's fault
        self._signed_in = True
        if self._told:  # the page that signing in drew has asked for the lists, and a test that opens a case at once asks again while that one is still being answered
            self.wait_listed(self._told)
            self.page.wait_for_load_state("networkidle")

    def wait_listed(self, cases: list[str], seconds: float = 20) -> None:
        """Waits until All clients lists every case the test has just told the app about (a case this person may not open is never listed: then the wait ends at `seconds`).
        The lists read a changed case once, in the request that first sees the ledger's row; a second list request that arrives while that read is under way is answered
        from the entries as they were (src/review/roster.py sync: it takes the ledger's new rows under its lock and reads the cases outside it), so a page load that lands
        in that window shows All clients without the case. Asking here, before the page does, lets that read finish and leaves the page the settled list."""
        url = self.world["review"].rstrip("/") + "/api/clients"
        wanted = {c for c in cases if (Path(self.world["clients"]) / c / "fact_graph.json").exists()}  # a client invited and not yet processed has no case: All clients does not list them
        end = time.time() + seconds
        while True:
            try:
                got = self.page.request.get(url)
                listed = {c.get("id") for c in got.json()} if got.ok else set()
            except Exception:  # noqa: BLE001 -- a page mid-navigation, a slow answer: ask again
                listed = set()
            if wanted <= listed or time.time() > end:
                return
            time.sleep(0.25)

    def open(self, client: str, tab: str, filing: str | None = None) -> None:
        """A fresh page load on the client's tab (a URL that differs only after # wouldn't reload)."""
        self.page.goto("about:blank")
        self.page.goto(f"{self.world['review']}?tab={tab}" + (f"&filing={filing}" if filing else "") + f"#{client}")
        self.settle()

    def settle(self, ms: int = 600) -> None:
        tell_the_app(self.world)  # (a file a test wrote while the page was loading)
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(ms)
        self.page.wait_for_function("() => !document.body.innerText.includes('Laying out the packet') && !document.body.innerText.includes('Reading the case')",
                                    timeout=SLOW)

    def text(self) -> str:
        return self.page.locator("#main").inner_text()

    def when_it_says(self, *words: str) -> str:
        """The page's text once it holds every one of these words (the screen is drawn again after the app's answer: a read straight after the toast may be of the screen before it).
        If they never show, the text as it is is returned for the test's own assert to report."""
        try:
            self.page.wait_for_function("(ws) => { const t = document.getElementById('main').innerText; return ws.every((w) => t.includes(w)); }", arg=list(words))
        except Exception:  # noqa: BLE001 -- the assert that follows says what the screen held
            pass
        return self.text()

    def check(self, name: str) -> str:
        """The page as the person sees it: no page errors, no leaked null/undefined/NaN; kept as a screenshot when asked."""
        self.settle(200)
        if self.shots:
            self.shots.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(self.shots / f"{name}.png"), full_page=True)
        body = self.page.locator("body").inner_text()
        leaks = [line for line in body.splitlines() if LEAK.search(line)]
        assert not leaks, f"{name}: {leaks[:5]}"
        # a script error the page caught and showed as text ("q is not a function") is still a broken screen
        crashed = re.findall(r"[^\n]*(?:is not a function|is not defined|Cannot read properties|is not iterable)[^\n]*", body)
        assert not crashed, f"{name}: {crashed[:3]}"
        wide = self.page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        assert wide <= 1, f"{name}: the page is {wide}px wider than the window (sideways scrolling)"
        assert not self.errors, f"{name}: {self.errors}"
        return body

    def toast(self, ok: bool = True) -> str:
        """The message the app showed after an action -- asserting it was (or wasn't) an error."""
        loc = self.page.locator("#toast")
        loc.wait_for(state="visible", timeout=SLOW)
        text, bad = loc.inner_text(), "bad" in (loc.get_attribute("class") or "")
        assert bad != ok, f"expected {'success' if ok else 'an error'}, the app said: {text}"
        self.page.evaluate("document.getElementById('toast').style.display = 'none'")
        return text

    def close(self) -> None:
        self.ctx.close()


@pytest.fixture
def attorney(browser, world):
    import world as w

    s = Screen(browser, world, w.ATTORNEY)
    yield s
    s.close()


@pytest.fixture
def paralegal(browser, world):
    import world as w

    s = Screen(browser, world, w.PARALEGAL)
    yield s
    s.close()
