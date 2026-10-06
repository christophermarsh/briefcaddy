# Contributing

Start with the Python 3.12 core profile in README.md. Keep fixtures fictional,
provider delivery off, and generated/runtime files outside version control.
Do not add client documents, identifiers, sign-in links, vaults, account files,
database clusters, model weights or private QA evidence to a patch.

The legacy source uses top-level imports under `src`; from PowerShell:

```powershell
$env:PYTHONPATH = "$PWD\src"
$env:I485_SHADOW = '0'
$env:I485_LIVE_CHECKS = '0'
.\.venv-dev\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/contract/repositories/test_portal_intent_inputs.py::test_digest_freezes_unicode_json_and_ordered_exact_inputs tests/contract/repositories/test_portal_intent_inputs.py::test_fictional_profile_is_disabled_and_consumes_local_endpoint_only_when_used tests/contract/repositories/test_portal_intent_inputs.py::test_actual_original_bytes_reuse_and_immutable_conflict
```

These check pure intent/profile behavior and fictional bytes; they do not
contact PostgreSQL or providers. Existing test fixtures redirect firm data to
temporary locations. SQL, browser, model, native ACL and PDF checks have separate
runtime/asset prerequisites. Omitted templates and the empty public specimen
registry will block tests that require them; do not replace them with unrelated
current PDFs and assume the field mappings still apply.

Reuse the nearest existing checks for each implementation slice. After stable
features, use the integrated acceptance gate in HOSTING_ROADMAP.md. Keep failed
attempts and evidence scope accurate. Prefer bounded fixes over new redundant
test scaffolding or repeated full suites.

Public packaging must use a reviewed explicit file manifest. The legacy
`tools/install_support.py` PRODUCT list and `tools/release_support.py`
`product_files` recurse into whole directories, including `docs`; they are
retained implementation, not an approved public-export filter. Binary package enumeration is disabled and installer/updater entry points are
withheld. Before enabling install/update releases, configure the new repository/channel, trust/signing
policy, platform package, notices and the exact release file set.

Include third-party source, edition, license and required notices before adding
an asset. Project Apache-2.0 does not replace upstream terms. Do not import the
original repository's history or quarantined files into a new public repository.
Report security concerns using SECURITY.md rather than an issue with exploit
details or client information.
