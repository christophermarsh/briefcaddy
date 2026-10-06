"""A made-up world for the end-to-end tests: the demo client (src/portal/demo.py,
everything invented and labelled EXEMPLO), cloned into one client per kind of
case the firm handles, each shaped the way the pipeline would record it --
documents in the folder, USCIS notices as facts -- so every stage the tests
see comes from the folder, as it would for a real client.

    case-sij        SIJ: the state court's order, nothing filed yet
    case-family     family-based: a citizen parent petitioning, nothing filed yet
    case-spouse     family-based: married a citizen in 2025 (the green card will be conditional)
    case-consular   family-based, I-130 approved, no I-485: NVC and the consulate
    case-asylum     asylum: nothing filed yet
    case-resident   a resident (green card) for citizenship, the I-90, the I-751
    case-court      a Notice to Appear: immigration court, the EOIR-28
    case-cuban      born in Cuba, paroled on 03/10/2025: the Cuban Adjustment Act after one year in the U.S.
    case-daca       a DACA recipient (a (c)(33) work permit expiring in 135 days): the renewal
    demo-ana       the demo client as seeded: SIJ, I-360 approved (in the portal)

No real client's data is used anywhere.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from factgraph import FactGraph  # noqa: E402

ATTORNEY = ("attorney@example.com", "Ana Attorney", "e2e-attorney-pass-1")
PARALEGAL = ("paralegal@example.com", "Paulo Paralegal", "e2e-paralegal-pass-1")
CASES = ["case-sij", "case-family", "case-spouse", "case-consular", "case-asylum", "case-resident", "case-court", "case-cuban", "case-daca"]


def _graphs(d: Path) -> list[Path]:
    return [p for p in (d / "fact_graph_raw.json", d / "fact_graph.json") if p.exists()]


def add_fact(d: Path, key: str, value, doc: str = "e2e-world", doc_type: str = "intake_questionnaire", raw=None) -> None:
    """A fact as the pipeline records it: a source on the raw graph (re-derived on every read) and the saved one."""
    for path in _graphs(d):
        g = FactGraph.load(path)
        g.add_source(key, doc, doc_type, raw if raw is not None else value, value, 0.95)
        g.save(path)


def drop_facts(d: Path, *prefixes: str) -> None:
    for path in _graphs(d):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["facts"] = {k: v for k, v in data["facts"].items() if not k.startswith(prefixes)}
        path.write_text(json.dumps(data), encoding="utf-8")


def add_doc(d: Path, name: str, doc_type: str) -> None:
    """A document in the client's folder (a one-page PDF) and its classification."""
    from pypdf import PdfWriter

    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    folder = Path(meta["source_folder"])
    folder.mkdir(parents=True, exist_ok=True)
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with open(folder / name, "wb") as f:
        w.write(f)
    meta.setdefault("classifications", {})[name] = doc_type
    (d / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")


def drop_docs(d: Path, *doc_types: str) -> None:
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    meta["classifications"] = {k: v for k, v in (meta.get("classifications") or {}).items() if v not in doc_types}
    (d / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")


def add_notice(d: Path, receipt: str, form: str, kind: str, when: str) -> None:
    """A USCIS notice, as the notice reader records it (src/extract/uscis_notice.py, src/journey.py notices)."""
    doc = f"notice-{form.lower()}-{kind}-{when}.pdf"
    add_doc(d, doc, "uscis_notice")
    add_fact(d, f"folder.uscis_case.{receipt}.{kind}_{when.replace('-', '')}", f"{form} {kind.upper()}, {when}", doc, "uscis_notice", raw=receipt)


def clone(root: Path, src: str, dst: str) -> Path:
    s, d = root / "clients" / src, root / "clients" / dst
    shutil.copytree(s, d)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    folder = Path(meta["source_folder"])
    new_folder = folder.parent.parent / dst / "uploads" if folder.name == "uploads" else folder.with_name(dst)
    shutil.copytree(folder, new_folder, dirs_exist_ok=True)
    meta.update(client_id=dst, source_folder=str(new_folder))
    (d / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return d


def name_paralegal(d: Path) -> None:
    """The attorney names the world's paralegal on the case: a VAWA, T, U or asylum case is restricted (src/restricted.py),
    and only the staff named on it may open it besides the attorneys. Harmless on a case that is not restricted."""
    import restricted

    restricted.name_person(d, PARALEGAL[0], True, ATTORNEY[1], "attorney", PARALEGAL[1])


def _not_sij(d: Path) -> None:
    drop_facts(d, "folder.uscis_case.", "folder.notice.", "applicant.i360_", "sij.")
    drop_docs(d, "i360_approval", "sij_order")


def build(root: Path) -> dict:
    """The world, from nothing: returns the paths and the staff accounts' sign-ins."""
    from portal import demo
    from portal.store import PortalStore
    from review.auth import Accounts

    if root.exists():
        shutil.rmtree(root)
    store = PortalStore(root / "portal")
    (root / "clients").mkdir(parents=True)
    demo.seed(store, root / "clients")

    d = clone(root, "demo-ana", "case-sij")  # a teenager whose state-court order just came in: the I-360 is next
    _not_sij(d)
    add_doc(d, "sij-order.pdf", "sij_order")
    add_fact(d, "sij.order_date", "2026-08-20", "sij-order.pdf", "sij_order")

    d = clone(root, "demo-ana", "case-family")  # a U.S. citizen parent petitioning: I-130 + I-485 together
    _not_sij(d)
    for key, value in {"petitioner.status": "U.S. citizen", "petitioner.family_name": "EXEMPLO", "petitioner.given_name": "MARCOS",
                       "petitioner.dob": "1980-02-02", "family.relationship": "parent"}.items():
        add_fact(d, key, value)

    d = clone(root, "demo-ana", "case-consular")  # the I-130 was approved and the beneficiary is abroad
    _not_sij(d)
    for key, value in {"petitioner.status": "U.S. citizen", "petitioner.family_name": "EXEMPLO", "petitioner.given_name": "MARCOS"}.items():
        add_fact(d, key, value)
    add_notice(d, "IOE0999000300", "I-130", "approval", "2026-06-01")

    d = clone(root, "demo-ana", "case-asylum")  # arrived recently, afraid to return: the I-589
    _not_sij(d)
    add_fact(d, "asylum.basis_political_opinion", "Yes")

    d = clone(root, "demo-ana", "case-resident")  # a resident since 2021: citizenship, the card, the conditions
    _not_sij(d)
    add_doc(d, "green-card.pdf", "green_card")
    add_fact(d, "n400.lpr_date", "2021-03-01", "green-card.pdf", "green_card")

    d = clone(root, "demo-ana", "case-court")  # in removal proceedings
    _not_sij(d)
    add_doc(d, "nta.pdf", "notice_to_appear")
    add_fact(d, "applicant.nta_present", "Yes", "nta.pdf", "notice_to_appear")

    d = clone(root, "demo-ana", "case-spouse")  # married a U.S. citizen in 2025: the green card will be a 2-year one
    _not_sij(d)
    for key, value in {"petitioner.status": "U.S. citizen", "petitioner.family_name": "EXEMPLO", "petitioner.given_name": "RAFAEL",
                       "family.relationship": "spouse", "applicant.marriage_date": "2025-06-14", "applicant.marital_status": "Married"}.items():
        add_fact(d, key, value)

    d = clone(root, "demo-ana", "case-cuban")  # born in Cuba, paroled in 2025: a green card under the Cuban Adjustment Act
    _not_sij(d)
    drop_facts(d, "applicant.country_of_birth", "applicant.citizenship", "applicant.travel_document_country", "applicant.i94_arrival_date",
               "applicant.i94_class_of_admission", "applicant.last_arrival_", "questionnaire.entry_how", "questionnaire.visa_type")
    for key, value in {"applicant.country_of_birth": "CUBA", "applicant.citizenship": "CUBA", "applicant.i94_arrival_date": "2025-03-10",
                       "applicant.last_arrival_date_self_reported": "2025-03-10", "applicant.i94_class_of_admission": "DT",
                       "questionnaire.entry_how": "parole", "questionnaire.release_document": "i94"}.items():
        add_fact(d, key, value)

    d = clone(root, "demo-ana", "case-daca")  # a DACA recipient (made up): the (c)(33) work permit expires in 135 days, inside the renewal window
    _not_sij(d)
    drop_facts(d, "applicant.dob", "applicant.ead_")
    for key, value in {"applicant.dob": "1999-03-14", "applicant.ead_category": "C33",
                       "applicant.ead_expiration_date": (date.today() + timedelta(days=135)).isoformat()}.items():
        add_fact(d, key, value)

    # clients who have only been invited: the portal's questionnaires from the start (green card, and citizenship)
    store.add_client("pilot-nova", "Nova Exemplo Teste", phone="", email="pilot-nova@example.com", language="pt")
    store.add_client("pilot-cidadao", "Caio Exemplo Cidadao", phone="", email="pilot-cidadao@example.com", language="es")
    store.update_profile("pilot-cidadao", filing="n400")

    accounts = Accounts(root / "users.json")
    for email, name, password in (ATTORNEY, PARALEGAL):
        accounts.add(email, name, "attorney" if "attorney" in email else "paralegal")
        accounts._set_password(email, password, must_change=False)
    # the attorney signs in with a code from an authenticator app (review/auth.py): set up here, with this browser-less
    # "remembered device" for the tests' sign-ins (conftest.Screen), and the secret for the tests that type a code
    import clock
    from review import totp

    token, _ = accounts.sign_in(ATTORNEY[0], ATTORNEY[2])
    letters = accounts.enrol_start(token, "Case Review")["letters"].replace(" ", "")
    secret = __import__("base64").b32decode(letters + "=" * (-len(letters) % 8))
    now = clock.utcnow().timestamp()
    accounts.enrol_finish(token, totp.code_at(secret, now))
    token, _ = accounts.sign_in(ATTORNEY[0], ATTORNEY[2])
    device = accounts.verify_code(token, totp.code_at(secret, now + totp.STEP), remember=True)["device"]  # one step ahead: allowed
    return {"root": root, "clients": root / "clients", "portal": root / "portal", "users": root / "users.json",
            "secrets": {ATTORNEY[0]: secret}, "devices": {ATTORNEY[0]: device}, "last_steps": {ATTORNEY[0]: totp.step(now) + 1}}


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "data" / "e2e_world"))
