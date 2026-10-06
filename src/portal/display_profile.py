"""Current confirmed display name, without rewriting submitted or contact evidence."""
from .queue_bridge import _safe


def current(store, client_id):
    profile = store.profile(client_id)
    scope = store.communication_scope()
    if scope is None:
        return profile
    from review.state import display_name
    case = _safe(scope.cases / client_id)
    _safe(case / "documents.json")
    return dict(profile, name=display_name(case, profile.get("name")))
