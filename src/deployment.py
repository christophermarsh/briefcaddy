"""How this copy of the software is run: by us for the firm ("hosted"), or
on the firm's own machine ("on_premises"). The same code serves both; this
file only decides who does the upkeep that comes with running a server,
and whom the screens tell people to ask.

  hosted        we run the server: backups, sign-in secrets and updates are
                ours, and the firm contacts our support.
  on_premises   the firm runs it: its IT keeps the machine, the backups and
                the secrets, and installs the updates we send.

Either way, the product's upkeep (form editions, USCIS addresses, legal
rules, the document readers) is ours, and the firm's own choices (the
Visa Bulletin month, fees, the firm's details) are made on the Settings page.
Nothing on screen names a file or a line of code.

Set per installation in deployment.json next to the code (not in git;
deployment.example.json shows the shape) or the I485_DEPLOYMENT path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from law_app.bootstrap.config import load_deployment
from law_app.bootstrap.paths import deployment_path

REPO = Path(__file__).resolve().parents[1]
PATH = deployment_path(REPO, os.environ)
MODES = ("hosted", "on_premises")
DEFAULT = {"mode": "on_premises", "provider": {"name": "the software provider", "email": "", "phone": "", "url": ""}}


def load() -> dict[str, Any]:
    return load_deployment(PATH, defaults=DEFAULT, modes=MODES)


def hosted() -> bool:
    return load()["mode"] == "hosted"


def provider() -> str:
    return load()["provider"]["name"]


def support() -> str:
    """Whom the screens tell people to ask about the system itself."""
    return provider() if hosted() else "your IT"


def responsible(party: str) -> str:
    """Who does a piece of upkeep: "firm" or "provider". party is the register's: firm, provider, or host (whoever runs the server)."""
    if party == "host":
        return "provider" if hosted() else "firm"
    return "firm" if party == "firm" else "provider"


def about() -> dict[str, Any]:
    """What the screens may show: the mode, the provider's contact, the version. No paths."""
    from version import RELEASED, VERSION

    d = load()
    return {"mode": d["mode"], "provider": d["provider"], "support": support(), "version": VERSION, "released": RELEASED}


def support_start() -> str:
    """support() at the start of a sentence: "Your IT", "The software provider" (str.capitalize would write "Your it")."""
    s = support()
    return s[:1].upper() + s[1:]
