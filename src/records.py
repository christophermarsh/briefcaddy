"""Compatibility import for the canonical record catalog.

The old import and law_app.catalog.registry resolve to the same module so
existing callers, including catalog replacements, keep one shared authority.
The record definitions live in law_app.catalog's case, portal, firm and audit
modules; dictionary generation and exports continue to use this facade.
"""

import sys

from law_app.catalog import registry

sys.modules[__name__] = registry
