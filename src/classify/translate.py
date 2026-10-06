"""Document-processing helper."""

from __future__ import annotations

import json
from functools import lru_cache


def warm_up(languages: tuple[str, ...] = ("pt", "es")) -> None:
    """Document-processing helper."""
    for code in languages:
        translate_text("Estudante" if code == "pt" else "Estudiante", code)


@lru_cache(maxsize=4096)  # Supporting implementation.
def translate_text(text: str, from_code: str, to_code: str = "en") -> str | None:
    """Document-processing helper."""
    try:
        import argostranslate.translate
    except ImportError:
        return None

    try:
        return argostranslate.translate.translate(text, from_code, to_code)
    except Exception:  # noqa: BLE001 -- e.g. language pair not installed; translation is optional
        return None


def installed_language_codes() -> set[str]:
    """Document-processing helper."""
    try:
        from argostranslate import settings
    except ImportError:
        return set()
    if settings.model_provider != settings.ModelProvider.OPENNMT:  # Supporting implementation.
        return _installed_language_codes_by_import()

    reaches: dict[str, set[str]] = {}  # Supporting implementation.
    for folder in settings.package_dirs:
        try:
            packages = [p for p in folder.iterdir() if p.is_dir()]
        except OSError:
            continue
        for package in packages:
            try:
                meta = json.loads((package / "metadata.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # Supporting implementation.
            if not isinstance(meta, dict) or meta.get("type", "translate") != "translate" or not meta.get("from_code") or not meta.get("to_code"):
                continue
            reaches.setdefault(meta["from_code"], set()).add(meta["to_code"])

    codes: set[str] = set()
    for start in reaches:
        seen, queue = {start}, [start]
        while queue:  # Supporting implementation.
            for nxt in reaches.get(queue.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        if start != "en" and "en" in seen:
            codes.add(start)
    return codes


def _installed_language_codes_by_import() -> set[str]:
    try:
        import argostranslate.translate
    except ImportError:
        return set()

    try:
        languages = argostranslate.translate.get_installed_languages()
    except Exception:  # noqa: BLE001 -- translation support is optional
        return set()

    codes = set()
    for language in languages:
        if language.code == "en":
            continue
        if any(t.to_lang.code == "en" for t in language.translations_from):
            codes.add(language.code)
    return codes
