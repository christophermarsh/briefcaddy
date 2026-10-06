"""Where every file of schemas/ lives. This is the only place that knows.

    path("template", "i485")        -> schemas/forms/i485/template.pdf
    path("packet", "family")        -> schemas/packets/family.json
    glob("cover_letter")            -> every cover letter, in name order
    names("packet")                 -> ["address", "asylee", ...]
    named("i765_template.pdf")      -> the file a schema means by that name (a few schemas name another schema file:
                                       a form's "template", a cover letter's "base" and "lockbox_chart", a packet's "cover_letter")

The rule (docs/ARCHITECTURE.md, "The schemas folder"): a new schema file goes in the folder of its kind below and is reached through this
module, never by a typed path. tests/test_schema_path.py fails on a typed path and on a file no kind reaches.

`root` (the last argument of each function) is the schemas folder to look in: the shipped one unless a test or a tool is pointed at another.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "schemas"

# kind -> (where its files are, what they hold). {name} is the file's name in the call: path("packet", "family").
LAYOUT: dict[str, tuple[str, str]] = {
    "template": ("forms/{name}/template.pdf", "the blank form as the agency publishes it, one folder per form"),
    "field_map": ("forms/{name}/field_map.json", "which fact fills which box of a form, when the form has a map of its own (the I-485)"),
    "form_text": ("forms/{name}/text.json", "a form's own words and the lines the product holds word for word (the G-28, the EOIR-26A)"),
    "packet": ("packets/{name}.json", "what goes in a filing's packet and in what order (one per filing), and the forms' field maps (companion_forms)"),
    "cover_letter": ("cover_letters/{name}.json", "the cover letter of each filing: subject, enclosures, fee note (i485 is the base the others inherit)"),
    "question": ("questions/{name}.json", "the question banks the client answers in the portal (intake is the base the filings' own banks add to)"),
    "question_help": ("questions/help/{name}.json", "the help shown beside the questions: why each is asked, tips, the common questions"),
    "paper_map": ("questions/paper/{name}.json", "the maps from the firm's paper questionnaire (printed text and handwritten text) to fact keys"),
    "register": ("registers/{name}.json", "the lists that go out of date or that the office keeps: upkeep, closures, processing times, connectors, document types, names, the client's wording"),
    "law": ("law/{name}.json", "the law and the agencies' tables: fees, lockbox addresses, editions, rules, bulletins, courts, reference tables"),
    "firm": ("firm/{name}.json", "the firm's own: its profile, letters, payment, settings, wording library, the columns of its exports"),
    "geo": ("geo/{name}.json", "places: one file per country, and the ports of entry"),
    "reference_form": ("reference_forms/{name}.pdf", "older or foreign forms kept to read, never filled"),
}


def path(kind: str, name: str, root: Path | None = None) -> Path:
    """The file of this kind and name, in the shipped schemas folder or in `root`."""
    try:
        where = LAYOUT[kind][0]
    except KeyError:
        raise KeyError(f"no schema kind {kind!r} (expected one of {', '.join(LAYOUT)})") from None
    return (root or ROOT) / where.format(name=name)


def rel(kind: str, name: str) -> str:
    """The file as a person reads it in a sentence or a register's "where": schemas/law/fees.json (always with forward slashes)."""
    return f"{ROOT.name}/{LAYOUT[kind][0].format(name=name)}"


def folder(kind: str, root: Path | None = None) -> Path:
    """The folder that holds the files of this kind (for the forms: the one that holds a folder per form)."""
    return (root or ROOT) / LAYOUT[kind][0].split("/{name}")[0]


def glob(kind: str, root: Path | None = None) -> list[Path]:
    """Every file of this kind, sorted by name."""
    where = LAYOUT[kind][0]
    return sorted((root or ROOT).glob(where.format(name="*")), key=lambda p: _name_of(kind, p))


def _name_of(kind: str, file: Path) -> str:
    where = LAYOUT[kind][0]
    if where.startswith("forms/"):
        return file.parent.name
    return file.stem


def all_files(suffix: str = "", root: Path | None = None) -> list[Path]:
    """Every file under the schemas folder (those ending in `suffix`), in path order: for what has to read or watch them all."""
    return sorted(p for p in (root or ROOT).rglob("*") if p.is_file() and p.name.endswith(suffix))


def names(kind: str, root: Path | None = None) -> list[str]:
    """The names of the files of this kind, sorted: path(kind, name) is each one."""
    return [_name_of(kind, p) for p in glob(kind, root)]


def schemas_in(repo: Path) -> Path:
    """The schemas folder of a repository checked out (or copied) at `repo`."""
    return Path(repo) / "schemas"


def named(file_name: str, root: Path | None = None) -> Path:
    """The file a schema means when it names another schema file (the way the files were named before the folders): "i765_template.pdf",
    "cover_letter.json", "cover_letter_i589.json", "uscis_lockboxes_i589.json"."""
    stem, _, ext = file_name.rpartition(".")
    if ext == "pdf" and stem.endswith("_template"):
        return path("template", stem[: -len("_template")], root)
    if stem == "cover_letter":
        return path("cover_letter", "i485", root)
    if stem.startswith("cover_letter_"):
        return path("cover_letter", stem[len("cover_letter_"):], root)
    if stem.startswith("uscis_"):
        return path("law", stem, root)
    raise KeyError(f"no schema file is named {file_name!r} (schema_path.named reads a form's template, a cover letter, a lockbox chart)")
