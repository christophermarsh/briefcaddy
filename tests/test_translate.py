"""classify/translate.py -- Argos Translate, run entirely locally (no
network request, nothing sent anywhere, same principle as classify/ocr.py's
Tesseract choice over a cloud OCR API). Tests are skipped if the pt/es/fr
language packages aren't installed locally, since this is an optional
dependency the same way Tesseract is."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from classify.translate import installed_language_codes, translate_text

_installed = installed_language_codes()
pytestmark_pt = pytest.mark.skipif("pt" not in _installed, reason="Argos Translate pt->en package not installed")
pytestmark_fr = pytest.mark.skipif("fr" not in _installed, reason="Argos Translate fr->en package not installed")


def test_translate_text_returns_none_for_an_uninstalled_language_pair():
    # "zz" is not a real language code -- no package can exist for it.
    assert translate_text("hello", "zz", "en") is None


@pytestmark_pt
def test_translate_text_translates_real_portuguese():
    # Real example case: the Portuguese birth certificate's own field
    # labels (docs/classify/patterns.py's birth_certificate signals).
    result = translate_text("Nome do pai", "pt", "en")
    assert result is not None
    assert "father" in result.lower()


@pytestmark_fr
def test_translate_text_translates_real_french():
    result = translate_text("Acte de naissance", "fr", "en")
    assert result is not None
    assert "birth" in result.lower()


def test_installed_language_codes_never_includes_english_itself():
    # English is the target, not a "from" language this pipeline
    # translates out of.
    assert "en" not in installed_language_codes()


def _package(folder, from_code, to_code):
    (folder / f"{from_code}-{to_code}").mkdir(parents=True)
    (folder / f"{from_code}-{to_code}" / "metadata.json").write_text(json.dumps({"from_code": from_code, "to_code": to_code, "package_version": "1"}))


def test_the_installed_languages_are_read_without_importing_the_translator(tmp_path):
    """The Documents tab asks which languages can be translated (translation.entry): that must not import the translator (torch and stanza, 7 to 20 seconds
    on the firm's disk, once per server start). Run in a process of its own, since this one may have imported it already. A language that reaches English
    through another installed language counts, as Argos pivots; English itself and a language with no way to English do not."""
    pytest.importorskip("argostranslate.settings")
    folder = tmp_path / "packages"
    for pair in (("pt", "en"), ("es", "en"), ("fr", "es"), ("de", "it"), ("en", "pt")):
        _package(folder, *pair)
    (folder / "not-a-package").mkdir()
    code = ("import sys, time; sys.path.insert(0, %r)\n"
            "from classify.translate import installed_language_codes\n"
            "started = time.monotonic(); codes = sorted(installed_language_codes()); took = time.monotonic() - started\n"
            "print(codes, 'argostranslate.translate' in sys.modules, 'torch' in sys.modules, took < 2)\n") % str(Path(__file__).resolve().parent.parent / "src")
    done = subprocess.run([sys.executable, "-c", code], env={**os.environ, "ARGOS_PACKAGES_DIR": str(folder)}, capture_output=True, text=True, timeout=120)
    assert done.stdout.strip() == "['es', 'fr', 'pt'] False False True", done.stderr[-500:]


def test_the_packages_folder_is_read_again_each_time(tmp_path, monkeypatch):
    settings = pytest.importorskip("argostranslate.settings")
    monkeypatch.setattr(settings, "package_dirs", [tmp_path])
    assert installed_language_codes() == set()
    _package(tmp_path, "pt", "en")  # a package installed while the app runs is seen by the next ask, as before
    assert installed_language_codes() == {"pt"}
    (tmp_path / "pt-en" / "metadata.json").write_text(json.dumps({"from_code": "pt", "to_code": "en", "type": "sbd"}))  # not a translation package
    assert installed_language_codes() == set()
