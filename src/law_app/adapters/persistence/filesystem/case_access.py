"""Trusted local composition retains ReviewApp's canonical access semantics."""
from portal import communication_consent as consent
from portal.store import PortalStore


class CanonicalCaseAccess:
    def __init__(self, app):
        self.app = app

    def context(self, *, prospect=False):
        portal = self.app._need_portal().absolute()
        cases = self.app.data_root.absolute()
        scope = consent.Scope(cases.parent.parent,
            portal / 'prospects' if prospect else portal,
            cases.parent / 'prospects' if prospect else cases)
        if self.app.accounts is None:
            raise PermissionError('Sign in with a current staff account first.')
        if self.app.accounts.path.absolute() != scope.data / 'review_users.json':
            raise ValueError("Use this installation's configured staff account store.")
        store = __import__('prospects').store(portal) if prospect else PortalStore(scope.portal)
        return scope, store

    def may_open(self, actor, client_id, *, prospect=False):
        if self.app.accounts is None:
            return False
        if prospect:
            try:
                self.app.prospect_dir(actor, client_id)
            except LookupError:
                return False
            return True
        return self.app.may_open(actor, client_id)
