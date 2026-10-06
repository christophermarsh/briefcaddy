# Security

This is a local engineering candidate. No supported production-release series,
service-level promise or accepted hosted security boundary is established here.

## Reporting

GitHub private vulnerability reporting is enabled for this public repository.
Use **Security → Advisories → Report a vulnerability**:
https://github.com/christophermarsh/briefcaddy/security/advisories

Do not put client data, secrets or exploit details in public issues. Reports
should use fictional or redacted reproductions and include only the information
needed to understand the issue. No response-time or remediation SLA is promised.
If the private reporting option is unavailable, request a private contact route
from the maintainer without including sensitive details.

## Current boundaries

- Use fictional local data for the demo, bind it to loopback, and leave
  SMTP/Twilio/connectors disabled. Demo sign-in/outbox links are credentials.
- `FictionalActor` and disposable loopback PostgreSQL configuration are local
  characterization aids, not a production identity or tenant boundary.
- Current staff cookies are resolved through existing Accounts and case ACLs
  for the canonical consent workflow. Client sessions and request-provided
  actor/role/firm fields do not establish staff authority.
- New recorded/documented grants carry the current enrollment fingerprint.
  Foreign/changed/unbound grants deny dispatch. Older history may still show
  a grant; there is no automatic grant conversion.
- Account update/sign-out and the consent gate use different locks. A denied
  next call does not prove an in-flight send was revoked. Durable subject/
  session authority and that protocol remain a next implementation slice.
- Storage checks assume an exclusively controlled trusted root. Native Windows
  hostile-ACL, independent firm runtime/DB/storage/queue authority, crash/restore
  and multi-instance acceptance remain open.
- Browser sandbox, real phones, deployed PDFs, legal/language approval,
  calibration, provider grants and load gates are incomplete. Existing passing
  local cases do not certify production readiness.

Never use the legacy recursive packaging helpers as a privacy clearance check.
The local export requires independent inspection of its exact candidate manifest,
including executable literals, large JSON data, schemas and asset notices.
