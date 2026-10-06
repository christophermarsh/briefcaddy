"""Document-processing helper."""
from __future__ import annotations
import contextlib
import io
import json
import shutil
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import accuracy
import compare
import schema_path
DEMO = 'demo-ana'
EDITS: dict[tuple[str, str], list[tuple[str, str, str]]] = {('sample-sij', 'i485'): [('Pt1Line7_CityTownOfBirth[0]', 'SOROCABA SP', 'Fictional sample discrepancy for manual review.'), ('Pt1Line10_PassportNum[0]', 'XX0001243', 'Fictional sample discrepancy for manual review.'), ('Pt1Line12_Date[0]', '1/14/2020', 'Fictional sample discrepancy for manual review.'), ('Pt7Line3_HeightInches[0]', '4', 'Fictional sample discrepancy for manual review.'), ('Pt1Line18_RecentDateTo[0]', '07/15/2019', 'Fictional sample discrepancy for manual review.'), ('Pt9Line76_YesNo', '/N', 'Fictional sample discrepancy for manual review.'), ('Pt1Line2_FamilyName[0]', '', 'Fictional sample discrepancy for manual review.'), ('Pt1Line2_GivenName[0]', '', 'Fictional sample discrepancy for manual review.'), ('Pt1Line2_MiddleName[0]', '', 'Fictional sample discrepancy for manual review.'), ('Pt1Line19_SSN[0]', '123456789', 'Fictional sample discrepancy for manual review.'), ('Pt8Line13_YesNo', '/Y', 'Fictional sample discrepancy for manual review.'), ('Pt5Line8_DateofBirth[0]', '03/04/1982', 'Fictional sample discrepancy for manual review.'), ('Pt1Line9_USCISAccountNumber[0]', '123456789012', 'Fictional sample discrepancy for manual review.'), ('Pt1Line18_CurrentAptSteFlrNumber[0]', '300', 'Fictional sample discrepancy for manual review.'), ('Pt1Line18_PriorAddress_Number[0]', '3B', 'Fictional sample discrepancy for manual review.'), ('Part11_NameofLanguage[0]', 'PORTUGUESE', 'Fictional sample discrepancy for manual review.'), ('Pt1Line1_MiddleName[0]', 'CLARA', 'Fictional sample discrepancy for manual review.')], ('sample-family', 'i485'): [('Pt1Line7_CityTownOfBirth[0]', 'SOROCABA, SP', 'Fictional sample discrepancy for manual review.'), ('Pt1Line1_GivenName[0]', 'ANA-CLARA', 'Fictional sample discrepancy for manual review.'), ('Pt1Line18_Date[0]', '08/01/2021', 'Fictional sample discrepancy for manual review.'), ('Pt1Line19_SSN[0]', '123456789', 'Fictional sample discrepancy for manual review.'), ('Pt5Line8_DateofBirth[0]', '03/04/1982', 'Fictional sample discrepancy for manual review.'), ('Pt1Line18_CurrentAptSteFlrNumber[0]', '300', 'Fictional sample discrepancy for manual review.'), ('Pt1Line1_MiddleName[0]', 'CLARA', 'Fictional sample discrepancy for manual review.')], ('sample-family', 'i130'): [('Pt4Line9_DateOfBirth[0]', '3/14/2006', 'Fictional sample discrepancy for manual review.'), ('Pt4Line7_CityTownOfBirth[0]', 'SOROCABA SP', 'Fictional sample discrepancy for manual review.'), ('Pt2Line6_CityTownOfBirth[0]', 'BOSTON MA', 'Fictional sample discrepancy for manual review.'), ('Pt2Line16_NumberofMarriages[0]', '2', 'Fictional sample discrepancy for manual review.'), ('Pt2Line11_SSN[0]', '999012345', 'Fictional sample discrepancy for manual review.'), ('Pt2Line24_FamilyName[0]', 'EXEMPLO', 'Fictional sample discrepancy for manual review.'), ('Pt2Line12_StreetNumberName[0]', '5 EXAMPLE RD', 'Fictional sample discrepancy for manual review.'), ('P4Line5a_FamilyName[0]', 'SOUZA', 'Fictional sample discrepancy for manual review.')], ('sample-n400', 'n400'): [('P2_Line8_DateOfBirth[0]', '03/14/2005', 'Fictional sample discrepancy for manual review.'), ('P2_Line11_CountryOfNationality[0]', 'BRASIL', 'Fictional sample discrepancy for manual review.'), ('P4_Line3_ZipCode1[0]', '01110', 'Fictional sample discrepancy for manual review.'), ('P7_OccupationFieldStudy2[2]', 'STUDENT, SAMPLE MIDDLE SCHOOL', 'Fictional sample discrepancy for manual review.'), ('P7_Line4_Pounds3[0]', '0', 'Fictional sample discrepancy for manual review.'), ('Line12b_SSN[0]', '123456789', 'Fictional sample discrepancy for manual review.'), ('Line2_FamilyName1[0]', 'SOUZA', 'Fictional sample discrepancy for manual review.'), ('P10_Line3_HouseHoldSize[0]', '1', 'Fictional sample discrepancy for manual review.'), ('P12_6a', '/N', 'Fictional sample discrepancy for manual review.'), ('P14_Line1_nterpreterGivenName[0]', 'MARIA', 'Fictional sample discrepancy for manual review.')]}
MARKS: dict[tuple[str, str], list[tuple[str, str]]] = {('sample-sij', 'i485'): [('Pt1Line10_PassportNum[0]', 'Fictional sample discrepancy for manual review.')], ('sample-n400', 'n400'): [('P2_Line8_DateOfBirth[0]', 'Fictional sample discrepancy for manual review.')]}
MARKED_BY, MARKED_AT = ('Fictional reviewer', '2026-01-01')

def _seed(root: Path) -> Path:
    """Document-processing helper."""
    from portal import demo
    from portal.store import PortalStore
    if root.exists():
        shutil.rmtree(root)
    (root / 'clients').mkdir(parents=True)
    with contextlib.redirect_stdout(io.StringIO()):
        demo.seed(PortalStore(root / 'portal'), root / 'clients')
    return root / 'clients'

def _clone(clients: Path, name: str) -> Path:
    src, dst = (clients / DEMO, clients / name)
    shutil.copytree(src, dst)
    meta = json.loads((dst / 'meta.json').read_text(encoding='utf-8'))
    folder = Path(meta['source_folder'])
    new_folder = folder.parent.parent / name / 'uploads' if folder.name == 'uploads' else folder.with_name(name)
    shutil.copytree(folder, new_folder, dirs_exist_ok=True)
    meta.update(client_id=name, source_folder=str(new_folder))
    (dst / 'meta.json').write_text(json.dumps(meta, indent=1), encoding='utf-8')
    return dst

def _graphs(d: Path) -> list[Path]:
    return [p for p in (d / 'fact_graph_raw.json', d / 'fact_graph.json') if p.exists()]

def _add_fact(d: Path, key: str, value, doc: str='sample', doc_type: str='intake_questionnaire') -> None:
    from factgraph import FactGraph
    for path in _graphs(d):
        g = FactGraph.load(path)
        g.add_source(key, doc, doc_type, value, value, 0.95)
        g.save(path)

def _drop_facts(d: Path, *prefixes: str) -> None:
    for path in _graphs(d):
        data = json.loads(path.read_text(encoding='utf-8'))
        data['facts'] = {k: v for k, v in data['facts'].items() if not k.startswith(prefixes)}
        path.write_text(json.dumps(data), encoding='utf-8')

def _add_doc(d: Path, name: str, doc_type: str) -> None:
    from pypdf import PdfWriter
    meta = json.loads((d / 'meta.json').read_text(encoding='utf-8'))
    folder = Path(meta['source_folder'])
    folder.mkdir(parents=True, exist_ok=True)
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with open(folder / name, 'wb') as fh:
        w.write(fh)
    meta.setdefault('classifications', {})[name] = doc_type
    (d / 'meta.json').write_text(json.dumps(meta, indent=1), encoding='utf-8')

def _not_sij(d: Path) -> None:
    _drop_facts(d, 'folder.uscis_case.', 'folder.notice.', 'applicant.i360_', 'sij.')

def _refill_i485(d: Path) -> None:
    """Document-processing helper."""
    from fill import load_field_map
    from review.state import refill
    refill(d, load_field_map(schema_path.path('field_map', 'i485')), schema_path.path('template', 'i485'))

def cases(clients: Path) -> list[tuple[Path, str, str]]:
    """Document-processing helper."""
    import family
    import naturalization
    sij = _clone(clients, 'sample-sij')
    fam = _clone(clients, 'sample-family')
    _not_sij(fam)
    for key, value in {'petitioner.status': 'USC', 'petitioner.family_name': 'EXEMPLO', 'petitioner.given_name': 'MARCOS', 'petitioner.dob': '1980-02-02', 'family.relationship': 'Parent'}.items():
        _add_fact(fam, key, value)
    family.answer(fam, {'family.previous_petition': 'No', 'family.adjust_city': 'BOSTON', 'family.adjust_state': 'MA', 'petitioner.sex': 'M', 'petitioner.birth_city': 'BOSTON', 'petitioner.citizenship_how': 'birth', 'petitioner.times_married': '1', 'petitioner.daytime_phone': '6175550100', 'petitioner.military': 'No'}, 'Sample')
    _refill_i485(fam)
    n400 = _clone(clients, 'sample-n400')
    _not_sij(n400)
    _add_doc(n400, 'green-card.pdf', 'green_card')
    _add_fact(n400, 'n400.lpr_date', '2021-03-01', 'green-card.pdf', 'green_card')
    naturalization.answer(n400, {'n400.basis': naturalization.BASES[0], 'n400.trips': 'NONE', 'n400.parent_citizen_before_18': 'No', 'n400.disability_exception': 'No', 'n400.name_change': 'No', 'n400.ssa_card': 'No', 'n400.fee_reduction': 'No'}, 'Sample')
    return [(sij, 'i485', 'Special immigrant juvenile: Form I-485'), (fam, 'i485', 'Family-based: Form I-485 with the I-130'), (fam, 'i130', 'Family-based: Form I-130'), (n400, 'n400', 'Naturalization: Form N-400')]

def _states(field) -> list[str]:
    """Document-processing helper."""
    return [str(x) for x in field.get('/_States_') or [] if str(x) != '/Off']

def _edit(pdf_in: Path, pdf_out: Path, edits: list[tuple[str, str, str]]) -> int:
    """Document-processing helper."""
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(str(pdf_in))
    fields = reader.get_fields() or {}
    by_short: dict[str, list[str]] = {}
    for full in fields:
        by_short.setdefault(compare.short_name(full), []).append(full)
    values: dict[str, str] = {}
    for box, value, what in edits:
        if box in by_short:
            for full in by_short[box]:
                values[full] = value
            continue
        widgets = [full for short, fulls in by_short.items() if compare._WIDGET_INDEX.sub('', short) == box for full in fulls]
        if not widgets:
            raise KeyError(f'the sample edit {what!r} names a box that is not on the form: {box}')
        for full in widgets:
            values[full] = value if value and value in _states(fields[full]) else '/Off'
    writer = PdfWriter(clone_from=str(pdf_in))
    for page in writer.pages:
        writer.update_page_form_field_values(page, values)
    pdf_out.parent.mkdir(parents=True, exist_ok=True)
    with open(pdf_out, 'wb') as fh:
        writer.write(fh)
    return len(edits)

@contextlib.contextmanager
def _office_saved():
    """Document-processing helper."""
    import tempfile
    import settings
    facts = json.loads(schema_path.path('firm', 'firm_profile').read_text(encoding='utf-8'))['facts']
    companion = json.loads(schema_path.path('packet', 'companion_forms').read_text(encoding='utf-8')).get('firm', {})
    values = {k: v for k, v in (facts | companion).items() if k.startswith('firm.') and k != 'firm.g28_attached'}
    before = settings.PATH
    with tempfile.TemporaryDirectory(prefix='i485-accuracy-firm-') as scratch:
        settings.PATH = Path(scratch) / 'settings.json'
        settings.PATH.write_text(json.dumps({'firm': {'values': values, 'updated_by': 'The sample builder', 'history': []}}), encoding='utf-8')
        try:
            yield
        finally:
            settings.PATH = before

def build(workdir: Path) -> list[accuracy.Reference]:
    """Document-processing helper."""
    with _office_saved():
        return _build(Path(workdir))

def _build(workdir: Path) -> list[accuracy.Reference]:
    import logging
    logging.getLogger('pypdf').setLevel(logging.ERROR)
    clients = _seed(workdir)
    refs = []
    for case_dir, form, track in cases(clients):
        placeholder = workdir / 'references' / f'{case_dir.name}.{form}.pdf'
        ref = accuracy.prepare(case_dir, form, placeholder, workdir / 'ours', accuracy.SAMPLES, track)
        ref.changes = _edit(ref.ours_pdf, placeholder, EDITS.get((case_dir.name, form), []))
        for box, reason in MARKS.get((case_dir.name, form), []):
            accuracy.add_mark(placeholder.parent, case_dir.name, form, box, reason, MARKED_BY, MARKED_AT)
        ref.marks = accuracy.read_marks(placeholder.parent, case_dir.name)
        refs.append(ref)
    return refs
