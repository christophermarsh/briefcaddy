"""Records the demo: a browser runs each scene of script.json while its line
of voiceover plays, with a visible pointer, captions and motion-graphic cards
(cards.py), and every frame the page paints is saved (Chrome's screencast,
sharp at 1920x1080).

    python tools/demo_video/record.py --link <portal sign-in link> --review http://127.0.0.1:8486 \
        --audio ~/demo_video/audio --pages ~/demo_video/pages --world ~/demo_video/world \
        --font ~/demo_video/fonts/Inter.ttf --out ~/demo_video/take

Writes <out>/frames/*.jpg, <out>/frames.json (file, time) and <out>/scenes.json
(scene, start time) for assemble.py. Run against the demo world only
(world.py): the review app must not see any real client.
"""

import argparse
import asyncio
import base64
import json
import time
from pathlib import Path

from playwright.async_api import async_playwright

import cards

HERE = Path(__file__).resolve().parent
GAP = 0.6  # silence between two lines of voiceover, seconds

OVERLAY = r"""
(() => {
  const make = () => {
    if (document.getElementById('__demo_cursor')) return;
    if (window.__demoFont && !document.getElementById('__demo_font')) {
      const s = document.createElement('style'); s.id = '__demo_font'; s.textContent = window.__demoFont; document.documentElement.appendChild(s);
    }
    const c = document.createElement('div'); c.id = '__demo_cursor';
    c.innerHTML = '<svg width="30" height="30" viewBox="0 0 24 24"><path d="M5 2.5l13.5 10.2-6.1 1.2 3.6 7.1-2.6 1.3-3.6-7.2L5.2 19.6z" fill="#0f172a" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    Object.assign(c.style, {position: 'fixed', left: (window.__cx || 900) + 'px', top: (window.__cy || 500) + 'px', zIndex: 2147483647,
      pointerEvents: 'none', transition: 'left .7s cubic-bezier(.45,0,.2,1), top .7s cubic-bezier(.45,0,.2,1), opacity .3s', filter: 'drop-shadow(0 2px 3px rgba(0,0,0,.3))'});
    document.documentElement.appendChild(c);
    const cap = document.createElement('div'); cap.id = '__demo_caption';
    Object.assign(cap.style, {position: 'fixed', left: '50%', bottom: '86px', transform: 'translateX(-50%)', background: 'rgba(10,24,48,.92)',
      color: '#fff', font: '600 19px/1.35 DemoInter, system-ui, sans-serif', letterSpacing: '-.005em', padding: '11px 22px', borderRadius: '12px',
      zIndex: 2147483646, pointerEvents: 'none', opacity: 0, transition: 'opacity .45s', maxWidth: '82%', textAlign: 'center',
      boxShadow: '0 10px 30px rgba(0,0,0,.25)', whiteSpace: 'nowrap', borderLeft: '4px solid #5fd4ad'});
    document.documentElement.appendChild(cap);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', make); else make();
  window.__demo = {
    make,
    move(x, y) { make(); const c = document.getElementById('__demo_cursor'); c.style.left = x + 'px'; c.style.top = y + 'px'; window.__cx = x; window.__cy = y; },
    hide(h) { make(); document.getElementById('__demo_cursor').style.opacity = h ? 0 : 1; },
    ripple(x, y) {
      const r = document.createElement('div');
      Object.assign(r.style, {position: 'fixed', left: (x - 16) + 'px', top: (y - 16) + 'px', width: '32px', height: '32px', borderRadius: '50%',
        background: 'rgba(37,99,235,.35)', border: '2px solid rgba(37,99,235,.6)', zIndex: 2147483647, pointerEvents: 'none', transition: 'transform .5s, opacity .5s'});
      document.documentElement.appendChild(r);
      requestAnimationFrame(() => requestAnimationFrame(() => { r.style.transform = 'scale(2.2)'; r.style.opacity = '0'; }));
      setTimeout(() => r.remove(), 700);
    },
    caption(t) { make(); const c = document.getElementById('__demo_caption'); if (t) c.textContent = t; c.style.opacity = t ? 1 : 0; },
    card(html, id) {
      make();
      // its own shadow DOM: the page's styles (.card, .sub, .head...) must not reach the overlay
      const d = document.createElement('div'); d.id = id; d.attachShadow({mode: 'open'}).innerHTML = html;
      Object.assign(d.style, {position: 'fixed', inset: 0, zIndex: 2147483640, transition: 'opacity .7s', opacity: 0});
      document.documentElement.appendChild(d); requestAnimationFrame(() => requestAnimationFrame(() => { d.style.opacity = 1; }));
    },
    uncard(id) { const d = document.getElementById(id); if (d) { d.style.opacity = 0; setTimeout(() => d.remove(), 800); } },
  };
})();
"""


class Recorder:
    def __init__(self, page, cdp, out: Path):
        self.page, self.cdp, self.out = page, cdp, out
        self.frames: list[tuple[str, float]] = []
        self.scenes: list[dict] = []
        self.cx, self.cy, self.caption_text = 900, 500, ""
        (out / "frames").mkdir(parents=True, exist_ok=True)

    async def _on_frame(self, e) -> None:
        name = f"{len(self.frames):06d}.jpg"
        (self.out / "frames" / name).write_bytes(base64.b64decode(e["data"]))
        self.frames.append((name, e["metadata"]["timestamp"]))
        try:
            await self.cdp.send("Page.screencastFrameAck", {"sessionId": e["sessionId"]})
        except Exception:  # noqa: BLE001 -- the page navigated; the restart below takes over
            pass

    async def start_capture(self) -> None:
        self.cdp.on("Page.screencastFrame", lambda e: asyncio.ensure_future(self._on_frame(e)))
        await self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": 1920, "maxHeight": 1080})

    async def restart_capture(self) -> None:
        await self.cdp.send("Page.stopScreencast")
        await self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": 1920, "maxHeight": 1080})

    async def goto(self, url: str, wait_for: str | None = None) -> None:
        await self.page.goto(url, wait_until="networkidle")
        if wait_for:
            await self.page.wait_for_selector(wait_for, timeout=60000)
        await self.restart_capture()
        await self.page.evaluate(f"__demo.make(); window.__cx={self.cx}; window.__cy={self.cy}; __demo.move({self.cx},{self.cy}); __demo.caption({json.dumps(self.caption_text)})")

    async def caption(self, text: str) -> None:
        self.caption_text = text
        await self.page.evaluate(f"__demo.caption({json.dumps(text)})")

    async def card(self, html: str, cid: str) -> None:
        await self.page.evaluate(f"__demo.hide(true); __demo.caption(''); __demo.card({json.dumps(html)}, {json.dumps(cid)})")

    async def uncard(self, cid: str, caption: str | None = None) -> None:
        await self.page.evaluate(f"__demo.uncard({json.dumps(cid)}); __demo.hide(false)")
        if caption is not None:
            await self.caption(caption)

    async def point(self, locator, settle: float = 0.75, center: bool = True) -> None:
        if center:  # the target to the middle of the screen, clear of the caption at the bottom
            await locator.evaluate("e => e.scrollIntoView({block: 'center', behavior: 'smooth'})")
            await asyncio.sleep(0.55)
        box = await locator.bounding_box()
        self.cx, self.cy = round(box["x"] + min(box["width"] / 2, 60)), round(box["y"] + box["height"] / 2)
        await self.page.evaluate(f"__demo.move({self.cx},{self.cy})")
        await asyncio.sleep(settle)

    async def click(self, locator, after: float = 0.6, center: bool = True) -> None:
        await self.point(locator, center=center)
        await self.page.evaluate(f"__demo.ripple({self.cx},{self.cy})")
        await locator.click()
        await asyncio.sleep(after)

    async def scroll(self, dy: int, steps: int = 14) -> None:
        for _ in range(steps):
            await self.page.mouse.wheel(0, dy / steps)
            await asyncio.sleep(0.045)
        await asyncio.sleep(0.3)

    def scene(self, sid: str) -> None:
        self.scenes.append({"id": sid, "start": time.time()})

    async def hold(self, until: float) -> None:
        """Let the voiceover finish: the scene lasts at least its line plus a pause."""
        await asyncio.sleep(max(0.0, until - time.time()))


def paper_reads(bundle: Path) -> list[tuple[str, str]]:
    """What the pipeline read from the handwritten form (shown beside the scan)."""
    facts = json.loads((bundle / "fact_graph.json").read_text())["facts"]
    v = lambda k: (facts.get(k) or {}).get("value")  # noqa: E731
    us = lambda d: f"{d[5:7]}/{d[8:10]}/{d[:4]}" if d else ""  # noqa: E731
    street = ", ".join(x for x in (v("applicant.physical_street"), f"APT {v('applicant.physical_apt')}" if v("applicant.physical_apt") else "",
                                   v("applicant.physical_city")) if x)
    return [("Mother's full name", v("questionnaire.mother_name") or ""),
            ("Mother's date of birth", us(v("applicant.mother_dob"))),
            ("Home address", f"{street}, {v('applicant.physical_state')} {v('applicant.physical_zip')}"),
            ("Height · Weight", f"{v('applicant.height')} · {v('applicant.weight_lbs')} lb")]


def last_text(outbox: Path, to: str) -> str:
    lines = [json.loads(line) for line in outbox.read_text(encoding="utf-8").splitlines() if line.strip()]
    return next(m["body"] for m in reversed(lines) if m["channel"] == "sms" and m["to"] == to)


async def run(args) -> None:
    durations = json.loads((args.audio / "durations.json").read_text())
    captions = {s["id"]: s["caption"] for s in json.loads((HERE / "script.json").read_text(encoding="utf-8"))["scenes"]}
    review = args.review.rstrip("/")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1280, "height": 720}, device_scale_factor=1.5, bypass_csp=True, locale="en-US")
        await ctx.add_init_script(f"window.__demoFont = {json.dumps(cards.font_face(args.font))};")
        await ctx.add_init_script(OVERLAY)
        page = await ctx.new_page()
        cdp = await ctx.new_cdp_session(page)
        rec = Recorder(page, cdp, args.out)
        # warm the review app first (the first client page loads its translator)
        for path in ("/api/items?client=demo-ana", "/api/items?client=demo-paper", "/api/overview", "/api/packet?client=demo-ana"):
            await page.goto(review + path)

        await page.goto(args.link, wait_until="networkidle")
        await page.evaluate("__demo.make(); __demo.hide(true)")
        await page.evaluate(f"__demo.card({json.dumps(cards.title())}, 'title')")
        await asyncio.sleep(0.5)
        await rec.start_capture()

        async def scene(sid, body):
            rec.scene(sid)
            t0 = time.time()
            await rec.caption(captions.get(sid, ""))
            await body()
            await rec.hold(t0 + durations[sid] + GAP)

        # 1 the title sequence, over the client's welcome page
        async def title():
            await asyncio.sleep(max(6.2, durations["title"] - 0.4))
            await page.evaluate("__demo.uncard('title'); __demo.hide(false)")
        await scene("title", title)

        # 2 the portal: language, then into the questionnaire
        async def portal_welcome():
            await asyncio.sleep(1.0)
            await rec.click(page.locator("#lang"), after=0.2, center=False)
            await page.select_option("#lang", "en")
            await asyncio.sleep(1.6)
            await rec.click(page.get_by_role("button", name="Continue where I left off"))
        await scene("portal_welcome", portal_welcome)

        # 3 plain-language help and "I'm not sure"
        async def portal_help():
            await rec.click(page.locator(".steplist").get_by_text("Immigration and court history"))
            await asyncio.sleep(0.6)
            await rec.scroll(330)
            await rec.click(page.get_by_text("Not sure?", exact=True).first, after=1.4)
            await rec.point(page.get_by_text("I'm not sure", exact=True).first, settle=1.2)
        await scene("portal_help", portal_help)

        # 4 documents, with an example picture
        async def portal_docs():
            await rec.click(page.locator(".steplist").get_by_text("Documents"))
            await rec.scroll(260)
            await rec.click(page.get_by_text("See example").first, after=2.2)
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
        await scene("portal_docs", portal_docs)

        # 5 a handwritten paper questionnaire: the scan, what was read, then the review screen
        async def handwriting():
            await rec.card(cards.scan(args.world / "paper" / "questionario_preview.png", paper_reads(args.world / "clients" / "demo-paper")), "scan")
            await asyncio.sleep(6.4)
            await rec.goto(f"{review}/?tab=check&list=1#demo-paper", wait_for="article.card .evidence img")
            await rec.caption(captions["handwriting"])
            await asyncio.sleep(0.5)
            await rec.click(page.locator(".jump").get_by_text("Part 5"), after=1.0, center=False)
            parents = page.locator("article.card", has=page.locator(".evidence img")).filter(has_text="Mother").first
            await rec.point(parents.locator(".evidence img").first, settle=1.6)
            await rec.point(parents.locator("input").first, settle=1.4, center=False)
        await scene("handwriting", handwriting)

        # 6 the review app: the SSN typo
        async def review_ssn():
            await rec.goto(f"{review}/?tab=fix#demo-ana", wait_for="article.card")
            await page.fill("#reviewer", "Demo Paralegal")  # every decision records who made it
            ssn = page.locator("article.card", has_text="Social Security").first
            await rec.point(ssn.locator("h3"), settle=1.6)
            await rec.point(ssn.locator(".callout").first, settle=2.4)
            await rec.click(ssn.locator("input[type=radio][value='123-45-6789']"), after=0.8)
            await rec.click(ssn.get_by_role("button", name="Save"), after=1.2)
        await scene("review_ssn", review_ssn)

        # 7 ask the client
        async def review_ask():
            await page.wait_for_selector("article.card")
            father = page.locator("article.card", has_text="Father").first
            await rec.click(father.get_by_role("button", name="Ask the client"), after=1.0)
            await rec.click(page.get_by_role("button", name="Send to the client"), after=1.0)
        await scene("review_ask", review_ask)

        # 8 attorney sign-off
        async def review_attorney():
            await rec.click(page.locator("nav.queues").get_by_text("Attorney sign-off"), after=1.0)
            await rec.scroll(380)
        await scene("review_attorney", review_attorney)

        # 9 the packet
        async def packet():
            await rec.click(page.locator("nav.queues").get_by_text("Filing packet"), after=0.8)
            await page.wait_for_selector("text=In the packet, in order", timeout=60000)
            build = page.get_by_role("button", name="Rebuild packet").or_(page.get_by_role("button", name="Build packet"))
            await rec.click(build.first, after=0.2)
            await page.wait_for_selector("text=Packet built", timeout=120000)
            await asyncio.sleep(0.8)
            await rec.card(cards.gallery(args.pages), "gallery")
            await asyncio.sleep(max(4.5, durations["packet"] - (time.time() - rec.scenes[-1]["start"]) + 0.8))
            await rec.uncard("gallery", captions["packet"])
            await asyncio.sleep(0.6)
            await rec.scroll(900, steps=20)
        await scene("packet", packet)

        # 10 every case
        async def dashboard():
            await rec.click(page.locator("#all"), after=0.4, center=False)
            await page.wait_for_selector("table.clients tr.row", timeout=60000)
            await asyncio.sleep(1.0)
            await rec.point(page.locator(".tile").nth(1), settle=1.2, center=False)
            await rec.scroll(200)
            await rec.point(page.locator("tr.row", has_text="Carla").first, settle=0.6, center=False)
        await scene("dashboard", dashboard)

        # 11 a client went quiet: a reminder, on her phone, back into the portal
        async def followup():
            row = page.locator("tr.row", has_text="Carla").first
            await rec.click(row.get_by_role("button", name="Remind"), after=1.6, center=False)
            body = last_text(args.world / "portal" / "outbox.jsonl", "+15550100177")
            await rec.card(cards.phone(body), "phone")
            await asyncio.sleep(3.6)
            link = page.locator("#__sms_link")
            await page.evaluate("__demo.hide(false)")
            await rec.click(link, after=0.5, center=False)
            url = "http" + body.split("http", 1)[1].split()[0]
            await rec.goto(url, wait_for="text=Continue where I left off")
            await asyncio.sleep(1.4)
            await rec.click(page.get_by_role("button", name="Continue where I left off"), after=1.2)
        await scene("followup", followup)

        rec.scene("end")
        await rec.card(cards.end(), "end")
        await asyncio.sleep(4.2)
        rec.scene("stop")
        await cdp.send("Page.stopScreencast")
        await asyncio.sleep(0.3)
        await browser.close()

    (args.out / "frames.json").write_text(json.dumps(rec.frames))
    (args.out / "scenes.json").write_text(json.dumps(rec.scenes, indent=1))
    print(f"{len(rec.frames)} frames, {round(rec.scenes[-1]['start'] - rec.scenes[0]['start'], 1)} s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--link", required=True)
    ap.add_argument("--review", default="http://127.0.0.1:8486")
    for name in ("audio", "pages", "out", "world", "font"):
        ap.add_argument(f"--{name}", type=Path, required=True)
    args = ap.parse_args()
    for name in ("audio", "pages", "out", "world", "font"):
        setattr(args, name, getattr(args, name).expanduser())
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
