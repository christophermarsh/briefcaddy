import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import os  # noqa: E402

# Shadow mode (src/learning/shadow.py) calls a local model and writes data/learning.db:
# never from tests -- the tests that check it pass their own settings and store.
os.environ.setdefault("I485_SHADOW", "0")
# The live maintenance check (src/maintenance.py) goes to uscis.gov: never from tests.
os.environ.setdefault("I485_LIVE_CHECKS", "0")
# The firm's own files -- its settings (src/settings.py), its upkeep log (src/maintenance.py), the installation's
# deployment.json (src/deployment.py): tests run on the shipped defaults, never this machine's, and anything a
# test writes lands in a scratch folder, never in the repo.
import tempfile  # noqa: E402

_FIRM = Path(tempfile.mkdtemp(prefix="i485-tests-"))
os.environ["I485_SETTINGS"] = str(_FIRM / "settings.json")
os.environ["I485_MAINTENANCE_LOG"] = str(_FIRM / "maintenance_log.json")
os.environ["I485_DEPLOYMENT"] = str(_FIRM / "deployment.json")
os.environ["I485_LIVE_STATUS"] = str(_FIRM / "maintenance_status.json")  # last night's live check (src/maintenance.py): never this machine's
os.environ["I485_RULES_APPROVED"] = str(_FIRM / "rules_approved.json")  # the attorney's rule approvals (src/rules/approval.py)
os.environ["I485_POLICIES_FIRM"] = str(_FIRM / "policies_firm.json")  # the attorney's edits to the firm's policies (src/rules/firm_policies.py)
os.environ["I485_INDEX"] = str(_FIRM / "index.db")  # the firm-wide document index (src/index.py); tests of it pass their own file
os.environ["I485_EVENTS"] = str(_FIRM / "events.jsonl")  # the event ledger (src/events.py); its rows are in events-YYYY-MM.jsonl beside this name
os.environ["I485_QUERY_DB"] = str(_FIRM / "query.db")  # the query layer (src/query.py); tests of it pass their own file
os.environ["I485_QUERY_REFRESH"] = "0"  # a screen brings the query layer up to date on every ask (in production: once a minute); test_query_layer tests the pace
os.environ["I485_FEEDBACK_EVERY"] = "0"  # the clients' feedback is brought onto the cases in the request (as installed: in the background, once a minute); a test that adds feedback and asks Reports at once
os.environ["I485_JOBS"] = str(_FIRM / "jobs")  # the job queue (src/jobs.py); the tests run a job themselves (jobs.work(..., once=True)) and the app starts no worker of its own
os.environ["I485_JOBS_WORKER"] = "0"
os.environ["I485_ROSTER"] = str(_FIRM / "roster.json")  # the lists' saved copy of every case (src/review/roster.py)
os.environ["I485_BACKUP_LOG"] = str(_FIRM / "backup_log.json")  # when the last backup and test restore were (src/backups.py): never this machine's
os.environ["I485_POSTURE"] = str(_FIRM / "posture.json")  # the machine's posture (src/posture.py): never this machine's
os.environ["I485_POSTURE_CHECKS"] = "0"  # the checks read the computer at the app's start and in the night: the tests that want them run them with recorded outputs
os.environ["I485_INBOX"] = str(_FIRM / "inbox")  # the notice inbox (src/inbox.py): the overnight run reads it; its tests pass their own folder
# the case folders the portal's messages check for a restricted case (src/portal/notify.py); a test with its own passes them
os.environ["I485_CASES"] = str(_FIRM / "clients")
(_FIRM / "clients").mkdir()
# the accuracy report (src/accuracy.py): the overnight run's comparison with hand-filled references is off in tests, and the history
# and the references' folder are scratch ones; the tests of the report pass their own
os.environ.setdefault("I485_ACCURACY", "0")
os.environ["I485_ACCURACY_HISTORY"] = str(_FIRM / "accuracy_history.jsonl")
os.environ["I485_REFERENCE"] = str(_FIRM / "reference")
# the audit of what the office changes (src/audit_fill.py): the overnight run's counting of the reviewers' Saves is off in tests, and the catalog is a scratch one
os.environ.setdefault("I485_AUDIT", "0")
os.environ["I485_AUDIT_FILL"] = str(_FIRM / "audit_fill.json")
# the firm's reader examples (src/reader_examples.py): every confirm or correction of a read value writes one; a scratch folder, never the repo's
os.environ["I485_READER_EXAMPLES"] = str(_FIRM / "reader_examples")
# Find across the firm (src/find.py): the deterministic hashing embedder, never the firm's Ollama; its index is data/find.db beside each test's case folders
os.environ["I485_FIND_EMBEDDER"] = "hashing"

import pytest

# The tests run in the mode the product ships in (I485_WALK_EVERY at its default, 600: the lists read their own copy of every case, kept up to date by the app's own writes and the
# event ledger). Each test has a saved copy of its own (a copy shared by two apps on different folders is refused anyway: it names its data folder).


@pytest.fixture(autouse=True)
def _own_saved_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("I485_ROSTER", str(tmp_path / "saved-lists.json"))


@pytest.fixture(autouse=True, scope="module")
def _own_saved_copy_per_module(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("I485_ROSTER", str(tmp_path_factory.mktemp("lists") / "saved-lists.json"))
    yield
    mp.undo()


@pytest.fixture
def make_fillable_pdf(tmp_path):
    """Builds a minimal AcroForm PDF with one text field per name in
    field_names, entirely via pypdf's low-level objects -- for tests that
    need a small, purpose-built fixture rather than the real (much larger)
    schemas/forms/i485/template.pdf. max_lengths optionally sets /MaxLen on
    specific fields, to test src/fill/fill_pdf.py's overflow check."""

    def _make(field_names: list[str], max_lengths: dict[str, int] | None = None) -> Path:
        from pypdf import PdfWriter
        from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject

        writer = PdfWriter()
        page = writer.add_blank_page(width=400, height=400)
        max_lengths = max_lengths or {}

        field_refs = []
        for i, name in enumerate(field_names):
            y = 10 + i * 30
            field = DictionaryObject()
            field.update(
                {
                    NameObject("/FT"): NameObject("/Tx"),
                    NameObject("/T"): TextStringObject(name),
                    NameObject("/Rect"): ArrayObject(
                        [NumberObject(10), NumberObject(y), NumberObject(200), NumberObject(y + 20)]
                    ),
                    NameObject("/Subtype"): NameObject("/Widget"),
                }
            )
            if name in max_lengths:
                field[NameObject("/MaxLen")] = NumberObject(max_lengths[name])
            field_ref = writer._add_object(field)
            field[NameObject("/P")] = page.indirect_reference
            field_refs.append(field_ref)

        page[NameObject("/Annots")] = ArrayObject(field_refs)

        acroform = DictionaryObject()
        acroform[NameObject("/Fields")] = ArrayObject(field_refs)
        writer._root_object[NameObject("/AcroForm")] = acroform

        path = tmp_path / "fillable.pdf"
        with open(path, "wb") as fh:
            writer.write(fh)
        return path

    return _make


def save_shipped_office_as_the_firms(path, by: str = "Ana Attorney") -> dict:
    """Fictional example or implementation helper."""
    import json as _json

    import schema_path

    facts = _json.loads(schema_path.path("firm", "firm_profile").read_text(encoding="utf-8"))["facts"]
    companion = _json.loads(schema_path.path("packet", "companion_forms").read_text(encoding="utf-8")).get("firm", {})
    values = {k: v for k, v in (facts | companion).items() if k.startswith("firm.") and k != "firm.g28_attached"}
    data = _json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}
    data["firm"] = {"values": (data.get("firm") or {}).get("values", {}) | values, "updated_by": by, "updated_at": "2026-10-04T09:00:00-04:00", "history": []}
    Path(path).write_text(_json.dumps(data), encoding="utf-8")
    return values
