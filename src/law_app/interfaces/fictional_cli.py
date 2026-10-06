"""One bounded local service command; no HTTP listener or installed app startup.

Operator-owned config selects the fictional actor/scope. Input is a single
bounded JSON message containing the existing test credential and command data.
Supply it over an anonymous pipe; never save/log it or put it in arguments/env.
"""
import argparse
import base64
import json
from pathlib import Path
import sys
from uuid import UUID

from law_app.bootstrap.fictional_runtime import compose_fictional_runtime

MAX_WIRE_BYTES = 23 * 1024 * 1024


def command(runtime, name, request):
    admission_fields = {'intake_id','revision_id','data_b64','generation_id','operation_id','expected_current_generation'}
    fields = {'upload': admission_fields, 'admission-status': admission_fields, 'abandon': admission_fields,
              'status': {'job_id'}, 'review': {'job_id'}}
    if type(request) is not dict or name not in fields or set(request) != fields[name]:
        raise ValueError('Exact fictional command required')
    if name in ('upload','admission-status','abandon'):
        data = base64.b64decode(request['data_b64'], validate=True)
        if name != 'upload':
            from law_app.ports.fictional_admissions import AdmissionRequest
            admitted = AdmissionRequest(UUID(request['intake_id']),UUID(request['revision_id']),request['generation_id'],
                request['operation_id'],request['expected_current_generation'])
            history = (runtime.service.admission_status(admitted,data) if name=='admission-status'
                       else runtime.service.abandon(admitted,data))
            reservation = history.reservation
            result = {'reservation_id':str(reservation.reservation_id),'evidence_id':str(reservation.evidence_id),
                'request_sha256':reservation.request_sha256,
                'original':{'revision_id':str(reservation.revision.revision_id),'sha256':reservation.revision.sha256,
                            'byte_length':reservation.revision.byte_length,'locator':reservation.revision.locator}}
            if name=='abandon':
                return result | {'state':'abandoned','abandoned_at':history.abandoned_at.isoformat(),'reused':history.reused}
            result.update(state=history.state,lifecycle=history.lifecycle,head_matches=history.head_matches,
                current_generation=history.current_generation,original_bytes=history.original_bytes,historical_only=history.historical_only)
            if history.receipt is not None:
                result['retained_intent']={'job_id':str(history.receipt.job_id),'attempt_id':str(history.receipt.attempt_id),
                                           'outbox_id':str(history.receipt.outbox_id)}
            return result
        receipt = runtime.service.upload(UUID(request['intake_id']), UUID(request['revision_id']), data,
            generation_id=request['generation_id'], operation_id=request['operation_id'],
            expected_current_generation=request['expected_current_generation'])
        return {'job_id': str(receipt.job_id), 'attempt_id': str(receipt.attempt_id),
                'outbox_id': str(receipt.outbox_id), 'reused': receipt.reused}
    job = UUID(request['job_id'])
    return runtime.service.status(job) if name == 'status' else runtime.service.review(job)


def main(argv=None, *, execute=command, commands=('upload','status','review','admission-status','abandon')):
    parser = argparse.ArgumentParser(description='Disabled-by-default fictional local service only')
    parser.add_argument('--config', type=Path, required=True, help='trusted fictional operator configuration')
    parser.add_argument('command', choices=commands)
    options = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(MAX_WIRE_BYTES + 1)
        if len(raw) > MAX_WIRE_BYTES:
            raise ValueError('Fictional command too large')
        incoming = json.loads(raw)
        if type(incoming) is not dict or set(incoming) != {'credential','request'}:
            raise ValueError('Exact fictional input required')
        config = json.loads(options.config.read_text(encoding='utf-8'))
        runtime = compose_fictional_runtime(config, incoming.pop('credential'))
        result = execute(runtime, options.command, incoming['request'])
        print(json.dumps(result, sort_keys=True, separators=(',',':')))
        return 0
    except Exception:
        # Driver diagnostics/configuration/input never enter CLI error output.
        print('Fictional command unavailable; outcome may be unconfirmed.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
