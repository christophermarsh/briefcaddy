"""Authenticated staff workflow over the sole canonical communication authority.

This local service grants no document-processing authority or PostgreSQL access.
Transport supplies only its staff cookie token; request actor/role/root/firm
fields are never used to select identity, policy, stores or installation.
"""
from contextlib import contextmanager

from law_app.ports.identity import StaffIdentity
from portal import communication_consent as consent


class ConsentWorkflow:
    def __init__(self, identity: StaffIdentity, access, *, view, change, validate_body):
        self.identity = identity
        self.access = access
        self.view = view
        self.change_view = change
        self.validate_body = validate_body

    @contextmanager
    def _authorized(self, session_token, client_id, prospect):
        scope, store = self.access.reader.context(prospect=prospect)
        # Preserve the canonical gate -> account/store lock order. Identity is
        # resolved again for every direct service call, inside the effect gate.
        with consent.gate(scope):
            actor = self.identity.current(session_token)
            self.access.require(actor, client_id, prospect=prospect)
            consent.staff(scope, actor['email'], case=scope.cases / client_id)
            yield scope, store, actor

    def status(self, session_token, client_id, *, prospect=False):
        with self._authorized(session_token, client_id, prospect) as (_, _, actor):
            # Existing projection includes canonical eligibility and recovery;
            # retain its response contract, without a second permission store.
            return self.view(client_id, actor, prospect=prospect)

    def grant(self, session_token, client_id, body, *, prospect=False):
        return self.change(session_token, client_id, dict(body, action='grant'), prospect=prospect)

    def revoke(self, session_token, client_id, channels, *, prospect=False):
        return self.change(session_token, client_id, {'action':'revoke', 'channels':channels}, prospect=prospect)

    def change(self, session_token, client_id, body, *, prospect=False):
        with self._authorized(session_token, client_id, prospect) as (scope, store, actor):
            self.validate_body(body)
            if body.get('action') == 'grant':
                if not isinstance(body.get('evidence'), dict) or body['evidence'].get('kind') != 'consent':
                    raise ValueError('Actual own-client consent evidence is required.')
                for name in ('channel','client_approved_at','notice_version','language','source_kind','approval_description'):
                    if not isinstance(body.get(name), str):
                        raise ValueError('Supply the actual approval channel, time, wording, language, source and description.')
                return {'grant':consent.grant(scope, store, client_id, body['channel'], actor_email=actor['email'],
                    evidence_ref=body['evidence'], client_approved_at=body['client_approved_at'],
                    notice_version=body['notice_version'], language=body['language'],
                    source_kind=body['source_kind'], approval_description=body['approval_description'])}
            if body.get('action') == 'revoke':
                channels = body.get('channels')
                if not isinstance(channels, list) or any(not isinstance(channel, str) for channel in channels):
                    raise ValueError('Choose explicit communication channels to revoke.')
                return consent.revoke(scope, store, client_id, channels, actor_email=actor['email'])
            # Other existing controls retain their current purpose-specific
            # checks; the service adds current session authority around them.
            return self.change_view(client_id, body, actor, prospect=prospect)

    def dispatch(self, session_token, client_id, channel, provider, *, prospect=False, credential=True):
        with self._authorized(session_token, client_id, prospect) as (scope, store, _):
            return consent.dispatch(scope, store, client_id, channel, provider, credential=credential)
