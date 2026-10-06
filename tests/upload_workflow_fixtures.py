"""Prepared fictional installation reused without e2e module-name collisions."""
import importlib.util
import sys
from pathlib import Path

import pytest
import restricted
import schema_path
import settings
from review.auth import Accounts
from review.server import ReviewApp

from assignment_route_fixtures import call, sign_in, server, PASSWORD  # noqa: F401 -- pytest fixture registration and helper reexports

_name = "upload_workflow_association_fixture_source"
_source = sys.modules.get(_name)
if _source is None:
    _spec = importlib.util.spec_from_file_location(_name, Path(__file__).with_name("test_source_association.py"))
    _source = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _source
    _spec.loader.exec_module(_source)
source_firm = _source.firm


@pytest.fixture
def world(source_firm, monkeypatch):
    scope, store, client, attorney, pages = source_firm
    monkeypatch.setattr(settings, "PATH", scope.data / "settings.json")
    monkeypatch.setenv("I485_JOBS_WORKER", "0")
    accounts = Accounts(scope.data / "review_users.json")
    for email, name in (("jane@firm.example", "Jane Fictional"), ("kim@firm.example", "Kim Fictional")):
        accounts.change_password(email, accounts.add(email, name, "paralegal"), PASSWORD)
        restricted.name_person(scope.cases / client, email, True, attorney["name"], "attorney", name)
    return {"scope": scope, "store": store, "client": client, "accounts": accounts, "pages": pages, "attorney": attorney}


@pytest.fixture
def app(world):
    scope = world["scope"]
    return ReviewApp(scope.cases, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None,
                     portal_root=scope.portal, accounts=world["accounts"])
