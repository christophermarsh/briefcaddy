# Retained capabilities

Status labels describe this snapshot, not legal, clinical or deployment
acceptance. **Implemented** means executable source exists. **Tested locally**
means bounded fictional evidence exists for the named behavior. **Experimental**
means a limited or optional adapter/protocol. **Asset-required** means source is
retained but the export omits a required template, fixture or model.
**Deferred** means acceptance or implementation still remains.

## Application inventory

| Capability | Main source/seam | Status and boundary |
| --- | --- | --- |
| PDF/image ingestion, OCR, barcode/MRZ reading, document classification and page grouping | `classify/`, `documents.py`, `document_instances.py`, `reader_manifest.py` | Implemented; OCR executables and optional models are separate assets. Reader accuracy/calibration acceptance remains. |
| Typed readers for identity, civil, travel, court, immigration and petition records | `extract/`, `schemas/registers/document_types.json` | Implemented; private-trial prose is removed. No population-wide accuracy claim. |
| Questionnaire/handwriting ingestion, names, dates, addresses, geocoding and person attribution | `questionnaire/`, `extract/geo.py`, `name_match.py`, `people.py`, `subject_attribution.py` | Implemented; uncertainty and source association need human review. Brazilian municipality registry omitted. |
| Evidence-linked fact graph, conflicts, corrections, provenance and restricted-case boundaries | `factgraph/`, `conflicts.py`, `correction_provenance.py`, `source_association.py`, `restricted.py` | Implemented; retained policy code does not prove every concurrent boundary. |
| Staff workspace, roster, lists, reports, search, case assignment, notes/questions and front desk | `review/`, `query.py`, `index.py`, `find.py`, `case_assignment.py`, `case_notes.py` | Implemented; existing caches/locks/globals remain partly process-local. |
| Staff local accounts, passwords, setup, TOTP/recovery and remembered devices | `review/auth.py`, `review/users.py`, `review/totp.py` | Implemented; production identity lifecycle and durable session integration deferred. |
| Client questionnaire, uploads, retakes, task list, messages and journey views | `portal/`, `client_case.py`, `client_next_steps` register | Implemented; direct fictional fixture can run without form PDFs. Legacy demo CLI login-link action is blocked by the current consent guard. Real phones/accessibility/browser acceptance remains. |
| English, Portuguese, Spanish and Haitian Creole client wording; translation hooks | `portal/bank.py`, `translation.py`, `classify/translate.py`, `schemas/questions/` | Implemented/experimental; bundles may contain draft/fallback wording. Attorney/language acceptance and optional translation models remain. |
| Prospect intake, eligibility/draft pathways, case opening and engagement letters | `prospects.py`, `engagement.py`, `apply_for.py`, `filing_questions.py` | Implemented; legal/wording acceptance remains. |
| Signing evidence, client file delivery and controlled export | `signing_evidence.py`, `client_file.py`, `client_file_policy.py`, `export_reader.py` | Implemented; deployed PDF and browser gates remain. |
| Canonical staff-authorized consent workflow | `law_app/application/communications/consent.py`, `law_app/ports/identity.py`, `law_app/adapters/identity/local_accounts.py` | Tested locally in bounded HTTP/canonical/identity cases; same existing consent store, current staff cookie and ACL. See security limits below. |
| SMTP/SMS/WhatsApp notification, opt-in/verification, STOP suppression and reminders | `portal/notify.py`, `client_reminders.py`, `staff_reminders.py` | Implemented; provider setup, wording and live delivery acceptance deferred. Demo providers off. |
| Deadlines, calendars, hearings, court closures, appointments and daily work | `clock.py`, `deadline` rules, `calendar_feed.py`, `court.py`, `day_plan.py`, `closures.py` | Implemented; legal/time-sensitive registers need current attorney approval. |
| Form fill, companion forms, evidence ordering, cover letters, overflow/continuation pages and packet assembly | `fill/`, `assemble.py`, `packet/`, `part14_explain.py`, `schemas/packets/` | Implemented/asset-required; all exported PDF templates are withheld. |
| SIJ and family adjustment, petitions and affidavits of support | `family.py`, `visa.py`, `g28.py`, `rules/`, `schemas/forms/` | Implemented/asset-required; filing validity is an attorney decision. |
| Naturalization, citizenship certificates/replacements, card renewal, conditions and work/travel authorization | `naturalization.py`, `certificate.py`, `n565.py`, `card_renewal.py`, `conditions.py`, `work_permit.py`, `travel.py` | Implemented/asset-required; edition and deployed-PDF acceptance remain. |
| Asylum/asylee, refugee family petition, humanitarian parole, Cuban adjustment, TPS and DACA | `asylum.py`, `asylee.py`, `i730.py`, `parole.py`, `cuban_adjustment.py`, `tps.py`, `daca.py` | Implemented/asset-required; draft/legal policy gates remain. |
| VAWA, U and T filings, certification/declarations and restricted communications | `vawa.py`, `u_visa.py`, `u_certification.py`, `t_visa.py`, `t_visa_declaration.py` | Implemented/asset-required; heightened case restrictions and attorney review apply. |
| EOIR appearances/appeals, bond, cancellation, hearings and motions/pleadings | `bia.py`, `bond.py`, `cancellation.py`, `hearing_request.py`, `court_motion.py`, `court_pleading.py`, `eoir26a.py` | Implemented/asset-required; templates and legal acceptance remain. |
| Address change, FOIA/records, RFE, waivers, appeals/reopening, expedition and online filing preparation | `address_change.py`, `g639.py`, `records.py`, `rfe.py`, `waiver.py`, `inadmissibility_waiver.py`, `motion.py`, `expedite.py`, `online_filing.py` | Implemented/asset-required; no automatic government submission acceptance. |
| Fees, payment forms and fee waivers | `fees.py`, `payment.py`, `fee_waiver.py` | Implemented; billing platform is excluded from hosting scope. |
| Connector/import/export code | `connectors/`, `tools/import_*.py`, `case_status.py` | Implemented/experimental; Clio, Docketwise, Filevine, Drive, Microsoft and government adapters require separate grants/fixtures/acceptance. No integration is assumed active. |
| Backups/restore, maintenance, approvals, secrets inventory, posture, updates and audit/event records | `backups.py`, `maintenance.py`, `approvals.py`, `firmsecrets.py`, `posture.py`, `events.py`, `tools/` | Implemented locally; installed/deployed recovery and public release packaging remain. |
| Reader feedback, accuracy samples, text classifier training and shadow evaluation | `learning/`, `accuracy.py`, `accuracy_samples.py`, `reader_examples.py`, `tools/train_textcat.py` | Experimental; public demo models disabled, specimen registry empty, model weights and private examples omitted. |

The complete executable inventory is in `docs/SOURCE_INVENTORY.json`; omitted
dependencies are in `docs/ASSETS.json`. A retained module name alone is not a
tested filing workflow. The 45 form-template directories and five reference
PDF paths remain represented by their mappings/code, with their PDF bytes absent.

## Hosting work already present

| Slice | Current result |
| --- | --- |
| Startup parser, configuration/path seams, records catalog and composition root | Implemented; compatibility-oriented extraction around the modular Python core. `--deployment` does not isolate every process global. |
| Filesystem storage/portal/queue ports | Implemented; immutable original-byte publication with scoped keys, digest verification, bounded uploads and link refusal. Exclusive trusted root assumed; native hostile-ACL gate remains. |
| PostgreSQL migrations 0001–0005 | Implemented for fictional intent, synthetic claims, outbox delivery, admission and abandonment. Not a production customer-data migration. |
| Scoped fictional upload → processing → review | Tested locally; exact revision/evidence/enrollment binding, DB-clock leases, fenced generations and durable intent/outbox receipts. Synthetic processing still requires human review. |
| Separate fictional service and worker CLI | Implemented, disabled by default and loopback/disposable configuration only. `FictionalActor` is not production authentication. |
| Retry, unconfirmed outcome, admission repair and abandonment | Tested locally on fictional fixtures; no silent overwrite or automatic data conversion. |
| Orderly runtime restart | One bounded SQL journey passed with 16 separate CLI processes and two orderly PostgreSQL restarts; retained IDs/bytes, no duplicate. Not abrupt-crash, backup-restore or capacity proof. |
| Canonical staff consent adaptation | Locally checked affected cases; independent review accepted the local bound. Durable subject/session mapping and in-flight revocation protocol remain unimplemented. |

Current-enrollment fingerprints bind newly documented and staff-recorded grants.
Unbound, foreign or changed-enrollment grants deny dispatch; older historical
views/replays may still display a grant. There is no automatic grant backfill.
`Accounts.update` and `sign_out` use the account lock rather than the consent
gate: next-call offboarding denial is not proof of in-flight revocation.

## Acceptance evidence and limits

The inherited PR22 baseline had nine cloud non-browser passes. The focused
browser run stopped before assertions at the Chromium sandbox ownership gate.
Earlier V4 roster (16 passes) and a 44-page PDF artifact only support their
recorded hash scopes. The later consent slice had 32 distinct eventually
passing affected cases across focused runs, with overlap and failed attempts;
it was not one clean 32-case campaign.

This export uses a small isolated syntax/import/fictional check recorded in
`docs/EXPORT_CHECKS.json`; it does not inherit a production-readiness label.
Broad backend/browser acceptance, real phones, native Windows ACL isolation,
deployed PDFs, attorney/language review, accuracy calibration, recovery/load,
production identity, provider grants and actual firm isolation remain open.

The CBP port dataset and installer entry points are withheld in the repaired
source-only candidate. Binary packaging is disabled. Original linked private
parser fixtures have been retired from this export and replaced by independent
fictional relations; inherited cohort/calibration coverage is not claimed.
