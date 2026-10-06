"""A demo-only world for recording the video: its own portal and review
folders, holding the made-up demo client (src/portal/demo.py) and a few
made-up clients at earlier stages so the All clients page isn't empty.
No real client's folder is anywhere in it.

    python tools/demo_video/world.py ~/demo_video/world [handwriting.ttf]

With a handwriting font it also adds a made-up client who filled in the
firm's paper questionnaire by hand (paper_form.py), read by the real pipeline.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from portal import demo  # noqa: E402
from portal.store import PortalStore  # noqa: E402

# Invented people, all named "Exemplo" like the demo client.
OTHERS = [
    ("pilot-bruno", "Bruno Exemplo Costa", "invited"),
    ("pilot-carla", "Carla Exemplo Dias", "started"),  # the one the video reminds, by text
    ("pilot-diego", "Diego Exemplo Ramos", "started"),
    ("pilot-fernanda", "Fernanda Exemplo Lopes", "invited"),
    ("pilot-gabriel", "Gabriel Exemplo Nunes", "submitted"),
]


def build(root: Path, hand_font: str | None = None) -> None:
    if root.exists():
        shutil.rmtree(root)
    store = PortalStore(root / "portal")
    bundles = root / "clients"
    bundles.mkdir(parents=True)
    demo.seed(store, bundles)
    for cid, name, status in OTHERS:
        if cid == "pilot-carla":
            store.add_client(cid, name, phone="+15550100177", email=f"{cid}@example.com", language="en",
                             consent={"email": True, "sms": True, "whatsapp": False})
        else:
            store.add_client(cid, name, phone="", email=f"{cid}@example.com", language="pt")
        store.update_profile(cid, status=status)
        if status != "invited":
            store.save_answers(cid, {"given_name": name.split()[0]})
    if hand_font:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import paper_form
        from process_clients import load_context, process_one

        source = root / "paper" / "source"
        sys.argv = ["paper_form", str(source), hand_font]
        paper_form.main()
        print(process_one("demo-paper", source, bundles / "demo-paper", load_context(policies=True))["counts"])


if __name__ == "__main__":
    build(Path(sys.argv[1]).expanduser(), str(Path(sys.argv[2]).expanduser()) if len(sys.argv) > 2 else None)
    print("demo world ready")
