"""Canonical prepared fictional installation; no provider or outbound calls."""
import pytest
import restricted
import source_association
import second_factor

from upload_workflow_fixtures import source_firm, world, app, server, call, sign_in  # noqa: F401 -- pytest fixture registration and helper reexports
from assignment_route_fixtures import PASSWORD

ATTORNEY = "drive-attorney@fictional.example"


@pytest.fixture
def drive_world(world):  # noqa: F811 -- pytest fixture injection
    accounts = world["accounts"]
    accounts.change_password(ATTORNEY, accounts.add(ATTORNEY, "Pat Drive Attorney", "attorney"), PASSWORD)
    second_factor.set_up(accounts, ATTORNEY, PASSWORD)
    restricted.name_person(world["scope"].cases / world["client"], ATTORNEY, True,
                           world["attorney"]["name"], "attorney", "Pat Drive Attorney")
    return world


@pytest.fixture
def configured_world(drive_world):
    world = drive_world  # noqa: F811 -- pytest fixture injection
    # Actual canonical enrollment, not a fabricated association/approval. This
    # fixture starts typed-only; source/critical/legal review stays independent.
    result = source_association.associate(world["scope"].root, world["store"], world["client"],
                                        actor_email=ATTORNEY, use_policies=False)
    assert result["associated"]
    return world
