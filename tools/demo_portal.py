"""The two-minute tour of the client's side, from one command.

    python tools/demo_portal.py                 # builds the showcase client in a new temp folder, prints her sign-in link
    python tools/demo_portal.py --serve         # ... and runs the portal on this computer (Ctrl-C stops it)
    python tools/demo_portal.py --folder DIR    # build in DIR instead (kept; the default temp folder is yours to delete)
    python tools/demo_portal.py --quick         # skip reading her documents (faster; the review app then has no case page for her)

The showcase client (src/portal/demo.py, showcase()) is made up: Beatriz Exemplo Lima, a Portuguese-speaking SIJ applicant. She
has the questionnaire half done, one blurry passport photo to retake, a thread with the office (an answer to read, a new
question waiting for the office) and a biometrics appointment next month with its what-to-bring page. Nothing leaves this
computer: the outbox is a file in the folder.

PORTAL_OUTBOX_FULL_LINKS is switched on for this tool alone (the working link goes to the folder's outbox file so a reply you
send as the office can be opened); a real installation never sets it (src/portal/notify.py).
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))


def build(folder: Path, base: str, process: bool = True) -> tuple[dict, str]:
    """The showcase in folder (portal/ and clients/); returns what was built and her sign-in link (works once, 72 hours)."""
    from portal import demo
    from portal.store import PortalStore

    store = PortalStore(folder / "portal")
    built = demo.showcase(store, folder / "clients", process=process)
    return built, f"{base}/l/{store.new_link_token(demo.SHOWCASE_ID)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folder", type=Path, help="build here instead of in a new temp folder")
    parser.add_argument("--port", type=int, default=8600)
    parser.add_argument("--serve", action="store_true", help="run the portal on this computer after building")
    parser.add_argument("--quick", action="store_true", help="skip reading her documents")
    args = parser.parse_args(argv)
    os.environ["PORTAL_OUTBOX_FULL_LINKS"] = "1"  # this tool only
    folder = (args.folder or Path(tempfile.mkdtemp(prefix="portal-demo-"))).resolve()
    base = f"http://127.0.0.1:{args.port}"
    os.environ["PORTAL_DATA"], os.environ["PORTAL_BASE_URL"] = str(folder / "portal"), base
    built, link = build(folder, base, process=not args.quick)
    print(f"Made-up client {built['client']} built in {folder}")
    print(f"  {built['tasks']} thing(s) for her to do, {built['messages']} message(s) in the thread, a biometrics appointment on {built['appointment']}")
    print(f"Her sign-in link (works once): {link}")
    print("To see the office's side:  python src/review/server.py --data " + str(folder / "clients") + " --portal " + str(folder / "portal"))
    if not args.serve:
        print("Add --serve to run the portal here, so the link opens (it builds her again and prints a fresh link).")
        return 0
    import uvicorn

    from portal.app import create_app

    print(f"Running the portal at {base} (Ctrl-C to stop)")
    uvicorn.run(create_app(folder / "portal", base_url=base), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
