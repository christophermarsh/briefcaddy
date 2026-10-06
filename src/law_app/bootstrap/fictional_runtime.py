"""Explicit fictional CLI composition; no environment/installed-root discovery."""
from dataclasses import dataclass
from uuid import UUID

from law_app.application.intake.fictional_service import FictionalActor, FictionalIntakeService
from law_app.adapters.storage.filesystem.originals import FilesystemOriginals
from law_app.bootstrap.fictional_postgres import (
    FictionalPostgresProfile, build_fictional_original_journey, build_fictional_outbox_delivery,
)
from law_app.ports.portal_intents import FirmEnrollment


@dataclass(frozen=True)
class FictionalRuntime:
    actor: FictionalActor
    service: FictionalIntakeService
    journey: object
    delivery: object


def compose_fictional_runtime(config, credential):
    expected = {'enabled','disposable','host','database','port','originals_root',
                'firm_id','enrollment_id','actor_id','role','pending_work_cap'}
    if type(config) is not dict or set(config) != expected:
        raise ValueError('Exact fictional operator configuration required')
    if (config['enabled'] is not True or config['disposable'] is not True
            or config['host'] != '127.0.0.1' or type(config['port']) is not int
            or not 1024 <= config['port'] <= 65535 or type(credential) is not str
            or not 32 <= len(credential) <= 256 or type(config['pending_work_cap']) is not int
            or not 1 <= config['pending_work_cap'] <= 2**31-1):
        raise ValueError('Explicit disposable loopback configuration required')
    profile = FictionalPostgresProfile(True, True, config['host'], config['database'])
    actor = FictionalActor(FirmEnrollment(UUID(config['firm_id']), UUID(config['enrollment_id'])),
                          UUID(config['actor_id']), config['role'])
    originals = FilesystemOriginals(config['originals_root'], actor.scope, disposable=True)
    def connection_factory(*, host, database):
        import psycopg
        try:
            return psycopg.connect(host=host, port=config['port'], dbname=database,
                user='law_app_fictional', password=credential, autocommit=True, connect_timeout=5)
        except psycopg.Error:
            raise RuntimeError('Fictional database unavailable') from None
    journey = build_fictional_original_journey(profile, actor.scope,
        connection_factory=connection_factory, originals=originals)
    delivery = build_fictional_outbox_delivery(profile, actor.scope, connection_factory=connection_factory)
    from law_app.adapters.persistence.postgres.fictional_admissions import PostgresFictionalAdmissions
    admissions = (PostgresFictionalAdmissions(actor, journey, config['pending_work_cap'])
                  if actor.role == 'service' else None)
    return FictionalRuntime(actor, FictionalIntakeService(actor, journey, admissions), journey, delivery)
