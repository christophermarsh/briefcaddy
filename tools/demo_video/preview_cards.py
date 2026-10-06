"""Stills of the motion-graphic cards, part-way through their animation, to
check the design without recording a whole take.

    python tools/demo_video/preview_cards.py <out dir> --world ~/demo_video/world --pages ~/demo_video/pages --font Inter.ttf
"""

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

import cards
from record import paper_reads

SAMPLE_TEXT = ("Reminder from Georges | Cote: your questionnaire hasn't been submitted yet. "
               "Continue here: http://localhost:8601/l/EXAMPLEtoken123 Reply STOP to opt out.")


async def main(args) -> None:
    shots = {
        "title": (cards.title(), 4.6),
        "scan": (cards.scan(args.world / "paper" / "questionario_preview.png", paper_reads(args.world / "clients" / "demo-paper")), 4.0),
        "phone": (cards.phone(SAMPLE_TEXT), 3.2),
        "gallery": (cards.gallery(args.pages), 2.8),
        "end": (cards.end(), 2.4),
    }
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=1.5)
        for name, (html, at) in shots.items():
            await page.set_content(f"<style>{cards.font_face(args.font)} body{{margin:0}}</style><div style='position:fixed;inset:0'>{html}</div>")
            await asyncio.sleep(at)
            await page.screenshot(path=str(args.out / f"card_{name}.png"))
        await browser.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    for name in ("world", "pages", "font"):
        ap.add_argument(f"--{name}", type=Path, required=True)
    a = ap.parse_args()
    for name in ("out", "world", "pages", "font"):
        setattr(a, name, getattr(a, name).expanduser())
    asyncio.run(main(a))
