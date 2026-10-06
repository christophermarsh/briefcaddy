# Hosting roadmap

Keep the modular Python application. Use one logical PostgreSQL schema for
Azure-hosted and on-premises deployments, with storage/job/provider adapters
and separate web and worker identities. Firm cells need distinct runtime,
database, storage and queue authority. Kubernetes is not the default.

The planning assumption for the first firm is ten staff and 1,000 intake
clients per month × ten documents × five pages = 50,000 pages/month.
Twenty firms is an expansion target, not measured capacity. Billing is outside
this work. Existing connector code does not establish provider grants or a
Filevine/Drive integration commitment.

## Current checkpoint and next slice

The source snapshot contains migrations 0001–0005 and bounded fictional durable
upload/worker/review, admission repair/abandonment, orderly restart and canonical
staff consent work. There is no migration 0006 or durable production subject/
session implementation. Hosting development is paused during release preparation.

After baseline/release review and explicit authorization to resume, the smallest
next slice is durable staff subject/session mapping with an explicit revocation
protocol against the existing Accounts/case-ACL/consent authority. Own
`law_app/ports/identity.py`, `law_app/adapters/identity/`, the narrow PostgreSQL
identity repository/migration and existing consent contract tests. First establish
what happens to an in-flight dispatch during account disable/sign-out; keep the
existing consent store and grant semantics. Agree the contract before adding a
schema or converting any grants. Do not rebuild intake or consent features that
already exist.

## Phases 0–11

| Phase | Present state | Remaining work / exit gate |
| --- | --- | --- |
| 0. Repository baseline | Complete for bounded engineering planning; catalog and command seams exist. | Keep actual vs planned commands and known runtime blockers explicit. Public export requires independent privacy/provenance review. |
| 1. Contracts and first real slice | Partial: fictional byte upload, durable processing and human review implemented. | Resolve firm/cell identity contracts; complete durable staff identity/session and revocation slice with reused focused checks. |
| 2. Core extraction and local configuration | Partial: bootstrap paths/config/composition and adapters exist. | Move remaining globals/caches/daemon work behind explicit owned services; keep existing UI behavior and one application core. |
| 3. Persistence, bytes, import and jobs | Partial: five scoped fictional migrations, original bytes, durable intents/admission/outbox/claims. | Shared production schema, durable firm profile/accounts/case data, portable storage adapters, reviewed import/reconciliation and migration accounting. No live conversion yet. |
| 4. Authority, lifecycle and firm cells | Partial: current Accounts cookie/ACL consent, scoped fictional enrollment/evidence fences. | Durable identity/session authority, offboarding/in-flight revocation, MFA and support boundaries, enrollment/evidence lifecycle, runtime/DB/storage/queue identities and negative cross-firm gates. |
| 5. Health, install, updates and staging | Partial: local tooling and health/status code retained. | Reproducible supported packages/images, reviewed release allowlist/signing/update channel, configuration/secret injection, migrations/health hooks and disposable hosted/on-prem staging. Provisioning requires separate authorization. |
| 6. Durable workers and safe effects | Partial: bounded fictional worker, fenced leases, retry/outcome recovery and admission repair. | Production handlers, bounded resources/backpressure, cancellation/shutdown, safe provider effects, telemetry without client data and isolation under concurrent work. |
| 7. Integrated migration and acceptance | Deferred. | One integrated campaign after features stabilize: fictional migration/reconciliation, browser/backend, deployed PDF/native ACL, failure/recovery, measured load and backup restore. Resolve existing browser runtime blocker first. |
| 8. Independent review and narrow repair | Local slices have bounded review; full acceptance deferred. | Independent implementation/security/data/operations review with narrowly owned repairs and the same campaign evidence. Preserve failed results; no blanket certification. |
| 9. First-firm cutover | Deferred; no cloud/live-data action performed. | Explicit firm approval, verified restore/cutover/rollback, real device and attorney/language acceptance, training/support and retention decisions. |
| 10. Five-firm batch | Deferred. | Repeat proven onboarding/cell checks, reconcile each migration, confirm support/recovery/resource bounds before admitting a batch. |
| 11. Twenty-firm operation | Deferred. | Compatibility across supported Azure/on-prem versions, measured capacity, isolated operations/update/restore and bounded expansion. Target count alone is not acceptance. |

## Decisions still needed

Resolve the guide's D01–D10 decisions before dependent implementation: firm/cell
identity and enrollment, staff/offboarding authority, MFA, support access, outage
behavior, resource envelopes, migration/reconciliation, retention/deletion and
operations acceptance. Define owner, record the choice and its exit evidence;
do not infer grants, service levels or data policies from the prototype.

## Working policy

Implement small visible slices, using focused reusable checks while coding.
Run the broad integrated acceptance campaign after the features stabilize in
phases 7–8. Do not build a new giant harness or repeatedly run full suites to
rediscover a known runtime failure. Any future cloud account, spending,
deployment, credentials, provider delivery or real-data migration needs its
own explicit authorization and reviewed concrete plan.

Known gates: legacy demo CLI sign-in-link path rejected by canonical consent;
process-local deployment/portal/rate/render/export caches and locks;
consent/offboarding race protocol; old unbound grant display vs dispatch denial;
production runtime identities/cells; native ACL/browser/phone/deployed PDF;
attorney/language/calibration; restore/crash/load; unresolved asset rights and
release packaging. No source upload or public visibility change is part of this
local candidate preparation.
