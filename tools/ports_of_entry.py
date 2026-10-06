"""Build schemas/geo/ports_of_entry.json, the product's own list of U.S. ports of entry, from CBP's "Locate a Port of Entry" pages.

    python tools/ports_of_entry.py                 # downloads cbp.gov's list and every state's page, writes the table
    python tools/ports_of_entry.py --from DIR      # the same from pages saved earlier (DIR/index.html, DIR/<state>.html)

Only what CBP prints is kept: each port's name as CBP writes it ("San Luis, Arizona"), its four-digit port code, the state or
territory whose page lists it, and the place words of the name (the part before the state's own name, split at its commas:
"Marcelino Serna, Tornillo" gives both). No port is typed by hand and none is added from anywhere else. src/arrival.py reads the
table when a Notice to Appear prints a city with a state that is not a state ("SAN LUIS, Ad"): a city that names a port in exactly
one state is offered to a person as the product's reading, never filled by itself (wave K, brief K6).

cbp.gov is read with an ordinary browser user agent. If it refuses (403 or any other error), the tool says so and writes nothing:
nothing is scraped around a refusal, and the table already in the repo stays as it is (register item ports_of_entry).
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
import schema_path  # noqa: E402

import clock  # noqa: E402
from extract.arrival import STATE_NAMES  # noqa: E402
from extract.names import fold_name  # noqa: E402

OUT = schema_path.path("geo", "ports_of_entry")
BASE = "https://www.cbp.gov"
INDEX = BASE + "/about/contact/ports"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
STATE_LINK = re.compile(r'href="/about/contact/ports/([A-Za-z]{2})"')
ROW = re.compile(r'<th scope="row">\s*<a href="(/about/contact/ports/[^"]+)"[^>]*>([^<]+)</a>')
MODIFIED = re.compile(r"Last Modified:\s*([A-Z][a-z]{2}\.? \d{1,2}, \d{4})")
CODES = {code: name for name, code in STATE_NAMES.items()}


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=60) as r:  # an HTTPError (a 403 refusal) stops the build: see main()
        return r.read().decode("utf-8", "replace")


def places_of(name: str, state: str) -> list[str]:
    """The place words of a port's name: its comma-separated parts, without a note in brackets ("(Area Port)") and without a part
    that is only a state's name or code ("Eastport, ID, Idaho" gives EASTPORT). A port whose name also names another state
    ("Duluth, MN and Superior, WI": a port shared across a state line) gives none: its places cannot be put in one state. The same for
    a port whose name has another state's full name as a whole part ("Ashland, Wisconsin Port Of Entry", listed on CBP's Minnesota
    page): only a whole comma-separated part counts, with "Port of Entry" set aside, so "Kansas City", "West Virginia" and
    "Mascoutah" are never taken for Kansas, Virginia or Utah."""
    if any(code in CODES and code != state for code in re.findall(r"\b[A-Z]{2}\b", name)):
        return []
    bare = re.sub(r"\([^)]*\)", " ", name)
    parts = [re.sub(r"\s+PORT OF ENTRY$", "", fold_name(x)) for x in bare.split(",")]
    if any(p in STATE_NAMES and STATE_NAMES[p] != state for p in parts):
        return []
    states = set(STATE_NAMES) | set(CODES)
    return list(dict.fromkeys(p for p in (fold_name(x) for x in bare.split(",")) if p and p not in states))


def ports_on(page: str, state: str) -> list[dict]:
    """The ports one state's page lists: [{name, code, state, places, url}] as CBP prints them."""
    out = []
    for href, text in ROW.findall(page):
        printed = re.sub(r"\s+", " ", html.unescape(text)).strip()
        m = re.fullmatch(r"(.+?)\s*[-–]\s*(\d{3,4})", printed)
        name, code = (m.group(1), m.group(2)) if m else (printed, None)
        out.append({"name": name, "code": code, "state": state, "places": places_of(name, state), "url": BASE + href})
    return out


def build(pages: dict[str, str], index: str, read: str) -> dict:
    ports = []
    for state in sorted(pages):
        ports += ports_on(pages[state], state)
    ports.sort(key=lambda p: (p["state"], p["name"], p["code"] or ""))
    modified = MODIFIED.search(index)
    return {
        "_source": (f"U.S. Customs and Border Protection, \"Locate a Port of Entry\": {INDEX}"
                    + (f" (the page says \"Last Modified: {modified.group(1)}\")" if modified else "")
                    + f", and the list of ports on each state's and territory's page it links to ({INDEX}/<state>), read {read[5:7]}/{read[8:]}/{read[:4]}"
                    " by tools/ports_of_entry.py. Each port is copied as CBP prints it: its name, its port code and the state whose page lists it."),
        "_note": ("Used only to offer a reading to a person: when a Notice to Appear prints a city with a state that is not a state "
                  "(\"SAN LUIS, Ad\"), a city that is a port's place word in exactly one state is offered on the review card for a person to "
                  "confirm (src/arrival.py, brief K6). A name in two states gives no reading. Re-read yearly: register item ports_of_entry."),
        "read": read,
        "ports": ports,
    }


def main(argv: list[str]) -> int:
    saved = Path(argv[argv.index("--from") + 1]) if "--from" in argv else None
    try:
        index = (saved / "index.html").read_text(encoding="utf-8") if saved else fetch(INDEX)
        states = sorted({s.upper() for s in STATE_LINK.findall(index)})
        pages = {s: ((saved / f"{s.lower()}.html").read_text(encoding="utf-8") if saved else fetch(f"{INDEX}/{s.lower()}")) for s in states}
    except Exception as exc:  # noqa: BLE001 -- cbp.gov refused or could not be read: say so and write nothing
        print(f"cbp.gov could not be read ({exc}). Nothing was written; the table in the repo is unchanged.")
        return 1
    table = build(pages, index, clock.today().isoformat())
    if not table["ports"]:
        print("No port was found on cbp.gov's pages (the page layout may have changed). Nothing was written.")
        return 1
    OUT.write_text(json.dumps(table, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(table['ports'])} ports from {len(pages)} pages written to {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
