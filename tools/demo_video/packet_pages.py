"""Pictures of the demo packet's key pages, for the video's packet scene
(a headless browser shows no PDFs).

    python tools/demo_video/packet_pages.py <client bundle> <out dir>
"""

import json
import sys
from pathlib import Path

import pypdfium2 as pdfium

bundle, out = Path(sys.argv[1]).expanduser(), Path(sys.argv[2]).expanduser()
out.mkdir(parents=True, exist_ok=True)
manifest = json.loads((bundle / "packet.json").read_text())
first = {s["tab"]: s["first_page"] for s in manifest["sections"]}
pages = [("Index", 1), ("G-28", first["G-28"]), ("I-485", first["I-485"]), ("I-765", first["I-765"]),
         ("Exhibits", first.get("Exhibit A", first["I-765"]))]
pdf = pdfium.PdfDocument(str(bundle / "packet.pdf"))
pdf.init_forms()
shown = []
for label, page in pages:
    name = f"{label.lower().replace('-', '')}.png"
    pdf[page - 1].render(scale=1.6, may_draw_forms=True).to_pil().save(out / name)
    shown.append({"label": label, "file": name, "page": page})
(out / "pages.json").write_text(json.dumps({"pages": shown, "total": manifest["pages"],
                                            "signatures": manifest["signatures"]}, indent=1))
print(json.dumps(shown))
