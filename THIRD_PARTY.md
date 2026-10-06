# Third-party material and provenance

Apache-2.0 applies to authorized project-authored source. Dependencies, data,
templates, fonts, artwork and model weights retain their own licenses and
redistribution requirements. This inventory records what was observed; it is
not a blanket license-compatibility or redistribution clearance.

| Material | Included? | Provenance / terms / remaining gate |
| --- | --- | --- |
| Project source, schemas, tests and tools | Yes, selected from a single commit | Apache-2.0, copyright 2026 Christopher Marsh; private defaults/narrative neutralized in candidate. Independent privacy and source provenance review pending. |
| Python dependencies | Pins only; no installed distributions bundled | `docs/DEPENDENCIES.json`: 138 lock pins, installed distribution metadata where available. 78 optional/other pins were absent in the inspected core environment; their terms remain unresolved here. Preserve distribution notices and review transitive/native components before packaging. |
| GeoNames-derived JSON gazetteers | 20 retained files | GeoNames, https://www.geonames.org/, CC BY 4.0, https://creativecommons.org/licenses/by/4.0/. Upstream attribution/terms: https://download.geonames.org/export/dump/readme.txt. NOTICE records modification; `docs/GEODATA.json` pins each resulting file. |
| CBP port-of-entry JSON | Withheld | Publisher terms/version unresolved; original hash and recorded source in `docs/ASSETS.json`. No automatic acquisition or policy workaround. |
| Brazilian municipality registry | Withheld | IBGE-related source/terms/version need review before restoration or rebuild. The retained `BR.json` has zero places; it does not contain municipality-derived places in this snapshot. |
| Form/reference PDF templates | 50 withheld | Exact original paths/hashes and available recorded publisher links in `docs/ASSETS.json`. Edition/source history and redistribution are not cleared. State, local and foreign specimens are not assumed U.S. federal public-domain works. |
| SVG artwork/icons | 13 withheld | Author/origin/terms unresolved. Preserve features in source; restore only reviewed assets or suitable independently authored replacements. Some UI visuals may be absent. |
| Original specimen-source registry | Withheld; public registry empty | Prior corpus licenses are not copied as clearance. No specimens or training data accompany this snapshot. Populate only after per-source provenance/rights review. |
| Translation/OCR/ML/browser/runtime packages and weights | Not bundled | Explicit optional setup, own terms, platform prerequisites and validation. No implicit download or installed binary provenance claim. |
| Nayuki QR generator portions | Yes, `src/review/qr.py` | MIT; original Project Nayuki copyright, permission and warranty terms retained unchanged. See `docs/THIRD_PARTY_SOURCES.json`. |
| Installer/updater entry points and binary packages | Withheld/disabled | Binary `product_files` enumeration fails closed until explicit file selection, all notices and channel/trust review. |
| Private research/learning corpora, raw QA evidence and repository history | Excluded | Not part of the source candidate or release package. |

## GeoNames modification/version notice

`tools/build_gazetteer.py` selects places and aliases, normalizes postal and
administrative regions, applies local overrides, and restructures the source as
application JSON. The resulting data differs from upstream; no endorsement is
implied. The original GeoNames download dates/releases were not retained.
`docs/GEODATA.json` gives content SHA-256 identifiers for this snapshot, not an
invented upstream version. Before distributing a rebuilt dataset, record the
actual downloaded release/date, source/license and changes; separately review
the omitted BR municipality input before any future rebuild.

## Restoring required assets

1. Consult `docs/ASSETS.json` for the missing path, expected original hash and
   any recorded publisher/download link. Unknown edition/provenance is explicit.
2. Resolve rights and the exact compatible edition with the maintainer. A
   current publisher link does not prove it supplies the edition used by the
   retained AcroForm mapping.
3. Acquire from the authorized publisher, record source/date/terms, and compare
   bytes against the expected hash. On mismatch, inspect printed fields and
   mapping compatibility, then review any necessary code/schema changes.
4. Restore only approved files to their listed paths in a private working copy.
   Run the existing relevant fill/packet/reader checks. Packaging needs a new
   explicit rights-cleared manifest and third-party notices.

Do not run `tools/fetch_specimens.py` expecting a complete corpus: the public
registry is intentionally empty. No PDF/icon bytes in the private quarantine are
authorized for automatic upload, even though the source code is project-owned.
