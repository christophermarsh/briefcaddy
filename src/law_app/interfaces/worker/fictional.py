"""Separate, bounded local worker process for one fictional delivery."""
from uuid import UUID

from law_app.adapters.persistence.postgres.outbox_delivery import deliver_fictional_once
from law_app.interfaces.fictional_cli import main
from law_app.ports.synthetic_work import ClaimRejected


def command(runtime, name, request):
    if runtime.actor.role != 'worker':
        raise ClaimRejected('Fictional worker unavailable')
    if name != 'once' or type(request) is not dict or set(request) not in (set(), {'outbox_id'}):
        raise ValueError('Exact fictional delivery command required')
    explicit = 'outbox_id' in request
    result = deliver_fictional_once(runtime.delivery, UUID(request['outbox_id']) if explicit else None,
        runtime.actor.actor_id, runtime.journey.delivery_handler(runtime.actor.actor_id))
    if result is None:
        return ({'delivery': 'not_claimed'} if explicit else
                {'delivery': 'nothing_claimed_within_scan', 'scan_limit': runtime.delivery.SCAN_LIMIT})
    receipt, value = result
    return {'delivery': 'acknowledged', 'result_id': str(receipt.result_id),
            'receipt_id': str(receipt.receipt_id), 'review_required': value['review_required']}


if __name__ == '__main__':
    raise SystemExit(main(execute=command, commands=('once',)))
