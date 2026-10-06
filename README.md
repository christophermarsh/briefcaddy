# BriefCaddy

BriefCaddy is a Python immigration casework application with a staff review
workspace, client questionnaire portal, document readers, evidence-linked facts,
and draft packet preparation. This source snapshot preserves the existing
application and the first local hosting migration slices.

This is a local engineering candidate for independent privacy and redistribution
review. It is not an accepted production deployment. No infrastructure has been
provisioned, no client data has been migrated, and no repository source has been
published by this preparation step.

## Start here

- [FEATURES.md](FEATURES.md): retained capabilities and their limits.
- [HOSTING_ROADMAP.md](HOSTING_ROADMAP.md): completed slices and remaining phases.
- [THIRD_PARTY.md](THIRD_PARTY.md): licenses, provenance and withheld assets.
- [CONTRIBUTING.md](CONTRIBUTING.md): development and focused checks.
- [SECURITY.md](SECURITY.md): reporting and known deployment boundaries.

## Fictional local setup

Use an existing Python 3.12 interpreter. From a fresh copy, these commands
create and check a project-local core environment using the existing exact-pin
setup tool. Setup downloads packages; it does not install Python, services,
models or system tools. It was not run as part of this export.

```powershell
python tools/dev_setup.py setup
.\.venv-dev\Scripts\python.exe tools/dev_setup.py check
```

On Linux, use `.venv-dev/bin/python` for the second command. The full lock also
contains optional translation/model dependencies; the core profile deliberately
selects its own locked closure. PostgreSQL, OCR executables, browser binaries,
translation packages and model weights are separate setup decisions.

For an existing fictional portal fixture, use a fresh terminal with no provider
credentials. The following disables outbound providers for this terminal,
turns off learning/live checks and background workers, and creates a new scratch
fixture outside the source tree. `process=False` skips document processing.

```powershell
Get-ChildItem Env: | Where-Object { $_.Name -match '^(SMTP_|TWILIO_|MAIL_FROM$|CLIO_|GOOGLE_|MS_|FILEVINE_|USCIS_CASE_STATUS_|I485_|PORTAL_)' } | Remove-Item
$env:I485_SHADOW = '0'
$env:I485_LIVE_CHECKS = '0'
$env:I485_JOBS_WORKER = '0'
$env:I485_POSTURE_CHECKS = '0'
$env:I485_FIND_EMBEDDER = 'hashing'
$env:PYTHONPATH = "$PWD\src"
.\.venv-dev\Scripts\python.exe -c "import tempfile; from pathlib import Path; from portal.demo import showcase; from portal.store import PortalStore; folder = Path(tempfile.mkdtemp(prefix='briefcaddy-demo-')); result = showcase(PortalStore(folder / 'portal'), folder / 'clients', process=False); print(folder); print(result)"
```

The command prints a temporary fixture location and its fictional client/task
summary. Its outbox is a local file. This preparation checks fixture creation;
listener/browser acceptance is separate.

The retained `tools/demo_portal.py --quick` command currently creates its fixture
and then fails at legacy sign-in-link issuance: `PortalStore.new_link_token`
rejects that path under the canonical dispatch/consent guard. Its `--serve` tour
also depends on fixing that approved identity flow. This export does not bypass
the guard or present the tour as a working login path.

The staff server entry point is `python src/review/server.py`; its startup
parser supports `--data`, `--portal`, `--users`, `--deployment` and loopback
`--host`. Full staff/PDF workflows need the omitted, edition-compatible assets.
Do not treat the quick tour as proof of those workflows.

## What is included

The source, schemas, tests, tools and five PostgreSQL migrations are retained
from one reviewed commit without its Git history. Installation defaults are
fictional, learning models are disabled for the public demo, and private trial
narrative has been removed from the identified reader/example prose.

50 form/reference PDFs, 13 SVG assets, the CBP port-of-entry data, the Brazilian
municipality source registry and the original specimen-source registry are withheld pending rights
review. No client folders, credentials, runtime cluster, model weights, installed
binaries, research corpus, raw QA receipts or old repository history are
included. [docs/ASSETS.json](docs/ASSETS.json) lists missing asset paths and the
original hashes; the original application capability remains in source.

## License and release status

Project-authored code is offered under [Apache-2.0](LICENSE), with third-party
terms in [NOTICE](NOTICE) and [THIRD_PARTY.md](THIRD_PARTY.md). That selection
does not establish redistribution rights for every dependency or asset.
Privacy, third-party rights, release packaging and security-reporting review
must be accepted before a public release. Installer/updater entry points are withheld; binary packaging is disabled until
its exact file selection, notices and new release channel are reviewed.

The original client-linked people, handwriting and new-document parser fixtures
were replaced with independent fictional relations. Their private-cohort
coverage is not carried forward as release evidence; focused replacement checks
are recorded separately.
