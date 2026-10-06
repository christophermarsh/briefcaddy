"""One product, two ways to run it (src/deployment.py): hosted by us, or on
the firm's own machine. What changes is who keeps the server, and whom the
screens send people to; what never changes is that a firm sees no file names,
no code and none of our upkeep steps."""

from __future__ import annotations

import json

import pytest

import deployment
import maintenance
from review.server import ReviewApp
import schema_path


@pytest.fixture
def mode(tmp_path, monkeypatch):
    def set_mode(name: str, provider: str = "Acme Legal Software") -> None:
        path = tmp_path / "deployment.json"
        path.write_text(json.dumps({"mode": name, "provider": {"name": provider, "email": "support@example.com"}}), encoding="utf-8")
        monkeypatch.setattr(deployment, "PATH", path)

    monkeypatch.setattr(maintenance, "FIRM_LOG", tmp_path / "firm_log.json")
    monkeypatch.setattr(maintenance, "LAST_LIVE", tmp_path / "live.json")
    return set_mode


def test_without_a_deployment_file_it_is_the_firms_own_machine(tmp_path, monkeypatch):
    monkeypatch.setattr(deployment, "PATH", tmp_path / "missing.json")
    assert deployment.load()["mode"] == "on_premises" and deployment.support() == "your IT" and deployment.support_start() == "Your IT"


def test_who_keeps_the_server_follows_the_mode(mode):
    mode("hosted")
    assert deployment.responsible("host") == "provider" and deployment.support() == "Acme Legal Software"
    mode("on_premises")
    assert deployment.responsible("host") == "firm" and deployment.support() == "your IT"
    assert deployment.responsible("provider") == "provider" and deployment.responsible("firm") == "firm"
    with pytest.raises(ValueError):
        mode("cloud")
        deployment.load()


def _app(tmp_path):
    data = tmp_path / "clients"
    data.mkdir()
    repo = maintenance.REPO
    return ReviewApp(data, schema_path.path("field_map", "i485", schema_path.schemas_in(repo)), schema_path.path("template", "i485", schema_path.schemas_in(repo)), None)


@pytest.mark.parametrize("name", ["hosted", "on_premises"])
def test_the_firm_sees_its_own_upkeep_and_ours_only_as_a_status(mode, tmp_path, name):
    mode(name)
    m = _app(tmp_path).maintenance()
    page = json.dumps(m)
    for word in ("schemas/", "src/", ".py\"", "tools/", "Kept in", "\"where\"", "IT:"):
        assert word not in page, word
    mine = {i["id"] for i in m["items"]}
    assert {"visa_bulletin", "fee_schedule", "firm_details"} <= mine and "form_i485" not in mine
    assert ("backups" in mine) == (name == "on_premises")  # the server's upkeep: the firm's IT on its own machine, ours when hosted
    ours = {i["id"]: i for i in m["provider_items"]}
    assert "form_i485" in ours and set(ours["form_i485"]) == {"id", "what", "cadence", "last_checked", "status", "state", "due", "check_type", "findings", "responsible", "required_role", "live_check"}
    assert all("Acme Legal Software" in s or "{provider}" not in s for i in m["items"] for s in i["steps"])
    assert m["about"]["provider"]["name"] == "Acme Legal Software" and m["about"]["version"]


def test_the_firm_cannot_mark_our_items(mode, tmp_path):
    mode("on_premises")
    app = _app(tmp_path)
    with pytest.raises(PermissionError, match="Acme Legal Software"):
        app.maintenance_mark({"id": "form_i485", "reviewer": "Paulo"})
    app.maintenance_mark({"id": "backups", "reviewer": "Paulo"})  # its own server: its own IT records the check
    assert next(i for i in app.maintenance()["items"] if i["id"] == "backups")["checked_by"] == "Paulo"
