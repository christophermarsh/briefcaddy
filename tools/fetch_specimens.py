"""Download the public specimen documents in schemas/registers/training_sources.json and
read them the way the pipeline reads a client's scan (classify.extract_pages
for PDFs, classify.ocr for pictures), so the classifier trains on real OCR
text of real layouts -- with fictitious holders.

    python tools/fetch_specimens.py [--refresh] [--only id,id]

  - files go to data/training/specimens/, texts to data/training/specimens.json
    (data/ is never committed; the list of sources is);
  - already-downloaded files are reused (--refresh downloads again);
  - "local_file": a file the firm saved by hand into data/training/specimens/
    (mass.gov blocks automated downloads); "url" says where it came from;
  - a PDF entry with "pages": [] is read and previewed page by page but not
    used until its pages are chosen in the list.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
import schema_path  # noqa: E402

SOURCES = schema_path.path("register", "training_sources")
OUT = REPO / "data" / "training"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36 i485-pipeline-training"


def download(url: str, dest: Path, refresh: bool) -> Path:
    if dest.exists() and not refresh:
        return dest
    import httpx

    # Wikimedia asks for a descriptive user agent (and rate-limits browser-like ones)
    ua = "i485-pipeline-training/1.0 (law-firm document classifier; specimens only)" if "wikimedia.org" in url else UA
    r = httpx.get(url, headers={"User-Agent": ua}, follow_redirects=True, timeout=120)
    r.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return dest


def read_picture(path: Path) -> str:
    from PIL import Image

    from classify.ocr import ocr_image

    image = Image.open(path).convert("RGB")
    if image.width < 1600:  # web specimens are small; OCR wants ~300 dpi-sized letters
        scale = 1600 / image.width
        image = image.resize((1600, int(image.height * scale)), Image.LANCZOS)
    return ocr_image(image, work_dir=REPO / ".ocr_tmp")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    from classify import classify_text, extract_pages

    sources = json.loads(SOURCES.read_text(encoding="utf-8"))["sources"]
    only = set(filter(None, args.only.split(",")))
    out_path = OUT / "specimens.json"
    texts = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else []
    texts = [t for t in texts if only and t["id"] not in only]  # re-read what's asked for (everything by default)
    for s in sources:
        if only and s["id"] not in only:
            continue
        suffix = Path(s.get("local_file") or s["url"].split("?")[0]).suffix.lower() or ".bin"
        dest = OUT / "specimens" / f"{s['id']}-{hashlib.sha1(s['url'].encode()).hexdigest()[:8]}{suffix}"
        try:
            if s.get("local_file"):  # saved by the firm (the site blocks automated downloads)
                path = OUT / "specimens" / s["local_file"]
                if not path.exists():
                    print(f"!! {s['id']}: save {s['url']} as data/training/specimens/{s['local_file']}")
                    continue
            else:
                path = download(s["url"], dest, args.refresh)
        except Exception as exc:  # noqa: BLE001 -- one missing source never stops the rest
            print(f"!! {s['id']}: {type(exc).__name__}: {exc}")
            continue
        if suffix == ".pdf":
            pages = extract_pages(path)
            wanted = s.get("pages")
            for n, text in enumerate(pages, 1):
                chosen = wanted is None or n in (wanted or [])
                if wanted == [] or chosen:
                    texts.append({"id": s["id"], "type": s["type"], "page": n, "used": chosen, "text": text})
                if wanted == []:
                    print(f"   {s['id']} p{n}: {' '.join(text.split())[:150]}")
        else:
            texts.append({"id": s["id"], "type": s["type"], "page": 1, "used": True, "text": read_picture(path)})
        for t in [t for t in texts if t["id"] == s["id"] and t["used"]]:
            rules = classify_text(t["text"][:6000]).doc_type
            print(f"{'  ' if rules == t['type'] else '~~'} {s['id']:32} p{t['page']:<2} {t['type']:18} rules={rules:22} {len(t['text']):6} chars")
    OUT.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(texts, ensure_ascii=False, indent=0), encoding="utf-8")
    used = [t for t in texts if t["used"]]
    print(f"{len(used)} specimen pages from {len({t['id'] for t in used})} sources -> {out_path}")


if __name__ == "__main__":
    main()
