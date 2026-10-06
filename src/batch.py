"""Application parser or rule helper."""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from assemble import assemble, completeness_findings, consistency_findings
from classify import Classification, classify_text, extract_pages, split_documents
from extract import extract_fields
from factgraph import FactGraph
from fill import field_max_lengths, fill_pdf, map_facts_to_fields
from questionnaire import QuestionnaireReading, read_questionnaire_pdf
from rules import Rule, run_rules
from rules.policy import run_policies
from validate import Flag, render_report, validate_graph
import schema_path
QUESTIONNAIRE_DOC_TYPE = 'intake_questionnaire'
PAGED_TYPES = {'notice_to_appear', 'i213', 'marriage_certificate'}
PRIOR_FORM_TYPES = {'i485', 'i130', 'i589', 'i131', 'i360_petition', 'i864', 'i601', 'i212', 'i290b', 'i730', 'i914', 'i918', 'i539'}

@dataclass
class ClientResult:
    graph: FactGraph
    classifications: dict[str, Classification] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    review_flags: list[Flag] = field(default_factory=list)
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    raw_graph: FactGraph | None = None
    rules_used: list[str] = field(default_factory=list)
    policies_used: bool = False
    extracted: dict[str, list] = field(default_factory=dict)
    documents: dict[str, Any] | None = None
    boundary_plans: dict[str, Any] = field(default_factory=dict)
    document_texts: list[tuple[str, str]] = field(default_factory=list)

@dataclass
class FilingResult:
    filled_pdf_path: Path
    flags: list[Flag]
    report: str
    unmapped_facts: list[str]
FIRM_PROFILE_DOC_ID = 'firm_profile.json'

def load_firm_profile(path: str | Path) -> dict[str, str]:
    """Application parser or rule helper."""
    import settings
    facts = json.loads(Path(path).read_text(encoding='utf-8')).get('facts', {})
    saved = {k: v for k, v in settings.values('firm').items() if k in facts}
    return settings.shipped(facts) | saved

def process_documents(client_id: str, documents: list[tuple[str, str]], rules: list[Rule] | None=None, policies: list[dict[str, Any]] | None=None, firm_profile: dict[str, str] | None=None, questionnaire_reader: Callable[[str], QuestionnaireReading] | None=None, answers: list | None=None, answers_doc_id: str='portal questionnaire', office_answers: list | None=None, pages: dict[str, list[str]] | None=None, boundary_context: dict | None=None) -> ClientResult:
    """Application parser or rule helper."""
    graph = FactGraph(client_id)
    classifications: dict[str, Classification] = {}
    review_flags: list[Flag] = []
    result_evidence: dict[str, dict[str, Any]] = {}
    extracted_by_doc: dict[str, list] = {}
    import document_instances
    documents, pages, plans, instances = document_instances.prepare(documents, pages, boundary_context)
    for fact_key, value in (firm_profile or {}).items():
        graph.add_source(fact_key, FIRM_PROFILE_DOC_ID, 'firm_profile', value, value, 1.0)
    for extracted in answers or []:
        graph.add_source(extracted.fact_key, doc_id=answers_doc_id, doc_type=QUESTIONNAIRE_DOC_TYPE, raw_value=extracted.raw_value, normalized_value=extracted.normalized_value, confidence=extracted.confidence, tier=3)
    for extracted in office_answers or []:
        graph.add_source(extracted.fact_key, doc_id='office question', doc_type='office_question', raw_value=extracted.raw_value, normalized_value=extracted.normalized_value, confidence=extracted.confidence, tier=3)
    for doc_id, text in documents:
        classification = classify_text(text)
        classifications[doc_id] = classification
        if instances[doc_id]['state'] == 'unresolved':
            review_flags.append(Flag('blocking', 'documents', f'Review document boundaries in Documents: {doc_id}', kind='document_boundary'))
            continue
        if classification.doc_type in ('unclassified', 'web_printout') or not _read_beyond_type(classification.doc_type):
            continue
        if classification.doc_type in PRIOR_FORM_TYPES:
            graph.add_source('folder.uscis_forms', doc_id, classification.doc_type, classification.doc_type, classification.doc_type.upper().replace('_', '-'), 0.9)
            continue
        import reader_manifest
        read_manifest = reader_manifest.current(classification.doc_type)
        reading_issues = ['The document classification has competing matches: ' + ', '.join(classification.ambiguous_with)] if classification.ambiguous_with else []
        fields = extracted_by_doc[doc_id] = extract_fields(classification.doc_type, text)
        from subject_attribution import provenance
        for extracted in fields:
            if extracted.page is None and classification.doc_type in PAGED_TYPES and pages and pages.get(doc_id):
                extracted.page = unique_page_of(pages[doc_id], extracted.raw_value) if classification.doc_type == 'marriage_certificate' else page_of(pages[doc_id], extracted.raw_value)
            graph.add_source(extracted.fact_key, doc_id=doc_id, doc_type=classification.doc_type, raw_value=extracted.raw_value, normalized_value=extracted.normalized_value, confidence=extracted.confidence, page=extracted.page, read_manifest=read_manifest, reading_issues=reading_issues + extracted.reading_issues, **provenance(instances[doc_id], classification.doc_type, extracted))
        if questionnaire_reader is not None and classification.doc_type == QUESTIONNAIRE_DOC_TYPE:
            reading = questionnaire_reader(doc_id)
            if reading.unsupported_language:
                review_flags.append(Flag('blocking', 'questionnaire', f"{doc_id}: this questionnaire is in {reading.unsupported_language}, and there is no question map for the firm's {reading.unsupported_language} template yet: enter the answers by hand ({__import__('deployment').support_start()} can teach the reader this template).", kind='unsupported_language'))
                continue
            if reading.blank_template:
                review_flags.append(Flag('blocking', 'questionnaire', f"{doc_id}: this is the firm's BLANK questionnaire template. No answers were filled in. Get the client's completed questionnaire before this case can be prepared.", kind='blank_template'))
                continue
            for extracted in reading.extracted_fields():
                graph.add_source(extracted.fact_key, doc_id=doc_id, doc_type=classification.doc_type, raw_value=extracted.raw_value, normalized_value=extracted.normalized_value, confidence=extracted.confidence, tier=3, page=extracted.page, read_manifest=read_manifest, reading_issues=reading_issues + extracted.reading_issues, **provenance(instances[doc_id], classification.doc_type, extracted))
            for question_id, reason in reading.unread.items():
                review_flags.append(Flag('review', f'questionnaire.{question_id}', f"{doc_id}: questionnaire item '{question_id}' ficticioq read ({reason}): enter by hand", kind='unread'))
            result_evidence[doc_id] = reading.evidence
    raw = FactGraph.from_dict(graph.to_dict())
    derive(graph, rules, policies)
    review_flags.extend(cross_check(graph))
    return ClientResult(graph=graph, classifications=classifications, review_flags=review_flags, evidence=result_evidence, raw_graph=raw, rules_used=[r.rule_id for r in rules or []], policies_used=bool(policies), extracted=extracted_by_doc, boundary_plans=plans, document_texts=documents)

def split_pages(name: str, pages: list[str], segments: list) -> tuple[list[tuple[str, str]], dict[str, list[str]]]:
    """Application parser or rule helper."""
    if len(segments) == 1:
        return ([(name, '\n'.join(pages))], {name: pages})
    docs, paged = ([], {})
    for first, last, _kind in segments:
        label = f'p{first + 1}' if first == last else f'p{first + 1}-{last + 1}'
        docs.append((f'{name}#{label}', '\n'.join(pages[first:last + 1])))
        paged[f'{name}#{label}'] = [''] * first + pages[first:last + 1]
    return (docs, paged)

def unique_page_of(pages: list[str], raw: str) -> int | None:
    """Application parser or rule helper."""
    needle = re.sub('\\s+', ' ', str(raw or '')).strip().lower()
    hits = [n for n, page in enumerate(pages) if len(needle) >= 6 and needle in re.sub('\\s+', ' ', page).lower()]
    return hits[0] if len(hits) == 1 else None

def page_of(pages: list[str], raw: str) -> int | None:
    """Application parser or rule helper."""
    first = re.sub('\\s+', ' ', str(raw or '').split(' | ')[0]).strip().lower()
    if len(first) < 6:
        return None
    for n, page in enumerate(pages):
        if first in re.sub('\\s+', ' ', page).lower():
            return n
    return None

def _read_beyond_type(doc_type: str) -> bool:
    """Application parser or rule helper."""
    from classify.patterns import TYPES
    return (TYPES.get(doc_type) or {}).get('read_beyond_type', True)

def derive(graph: FactGraph, rules: list[Rule] | None, policies: list[dict[str, Any]] | None) -> None:
    """Application parser or rule helper."""
    assemble(graph)
    if rules:
        run_rules(graph, rules)
    if policies:
        run_policies(graph, policies)
CROSS_CHECKS = [('applicant.last_arrival_date_self_reported', 'applicant.i94_arrival_date'), ('questionnaire.a_number', 'applicant.a_number')]

def _present(graph: FactGraph, key: str) -> bool:
    fact = graph.get(key)
    return fact is not None and fact.status != 'missing'

def eligibility_alerts(graph: FactGraph) -> list[Flag]:
    """Application parser or rule helper."""
    flags = []
    marital = graph.get('applicant.marital_status')
    married = _present(graph, 'applicant.marriage_date') or (marital is not None and any((s.normalized_value == 'Married' for s in marital.sources)))
    if _present(graph, 'applicant.i360_receipt_number') and married:
        when = graph.get('applicant.marriage_date')
        flags.append(Flag('blocking', 'applicant.marital_status', 'ELIGIBILITY: folder shows an SIJS (I-360) basis AND a marriage' + (f' on {_us_date(str(when.value))}' if when else '') + ': SIJS adjustment generally requires the applicant to be unmarried. Attorney must review before filing.', kind='alert'))
    if _present(graph, 'applicant.criminal_record_present'):
        offenses = graph.get('applicant.criminal_offenses')
        flags.append(Flag('review', 'applicant.criminal_record_present', 'CRIMINAL HISTORY: a criminal court record is in the folder' + (f' ({offenses.value})' if offenses else '') + ': attorney must review Part 9 items 22-41 and obtain certified dispositions.', kind='alert'))
    flags += _notice_alerts(graph)
    prior_forms = sorted({s.doc_id for s in getattr(graph.get('folder.uscis_forms'), 'sources', [])})
    if prior_forms:
        flags.append(Flag('review', 'folder.uscis_forms', 'PRIOR FORMS: the folder contains completed USCIS form(s). ' + ', '.join(prior_forms) + '. They are ficticioq used to fill this I-485. If any was FILED, check Part 4 item 5 (previously applied for permanent residence) and Part 4 item 1, and whether a receipt or decision notice should be in the folder.', kind='alert'))
    serious = {'applicant.part9.final_order_of_removal': 'a final order of removal/deportation/exclusion', 'applicant.part9.prior_order_reinstated': 'a removal order that was reinstated', 'applicant.part9.voluntary_departure_ficticioq_departed': 'voluntary departure granted but ficticioq taken'}
    ticked = sorted({str(f.value): key.removeprefix('questionnaire.yes.') for key, f in graph.all_facts().items() if key.startswith('questionnaire.yes.')}.items())
    if ticked:
        flags.append(Flag('review', ticked[0][1], 'CLIENT TICKED: in the portal questionnaire the client said these apply to them: ' + '; '.join((what for what, _ in ticked)) + ". Those Part 9 boxes are left blank: read the client's explanation, then decide each item.", kind='alert'))
    unsure = sorted({str(f.value): key.removeprefix('questionnaire.unsure.') for key, f in graph.all_facts().items() if key.startswith('questionnaire.unsure.')}.items(), key=lambda qk: qk[0])
    unsure = [(key, question) for question, key in unsure]
    if unsure:
        flags.append(Flag('review', unsure[0][0], 'CLIENT FICTICIOQ SURE: in the portal questionnaire the client answered "I\'m ficticioq sure" to: ' + '; '.join((q for _, q in unsure)) + '. These answers are left blank: go over each question with the client (one short call) before filing.', kind='alert'))
    said_yes = [what for key, what in serious.items() if (f := graph.get(key)) is not None and f.value == 'Yes']
    if said_yes:
        flags.append(Flag('review', 'applicant.part9.final_order_of_removal', 'SERIOUS ANSWERS: the client answered Yes to ' + '; '.join(said_yes) + '. If true these usually bar adjustment; clients often misread these questions. Verify against the EOIR/immigration record (and the Notice to Appear, if any) before these answers go on Part 9.', kind='alert'))
    if _present(graph, 'applicant.nta_present'):
        flags.append(Flag('review', 'applicant.nta_present', 'REMOVAL PROCEEDINGS: a Notice to Appear is in the folder. Attorney must decide Part 2 item 1 (filing with EOIR vs. USCIS jurisdiction) and confirm Part 9 item 14.', kind='alert'))
    return flags

def _uscis_cases(graph: FactGraph) -> list[tuple[str, str]]:
    """Application parser or rule helper."""
    notices = [(k.split('.')[2], str(f.value)) for k, f in graph.all_facts().items() if k.startswith('folder.uscis_case.') and f.value]
    approved = {receipt for receipt, d in notices if ' APPROVAL' in d}
    problem = ('REQUEST FOR EVIDENCE', 'INTENT TO DENY', 'DENIAL', 'REJECTION')
    return [(r, d) for r, d in notices if not (r in approved and any((w in d for w in problem)))]

def _notice_alerts(graph: FactGraph) -> list[Flag]:
    """Application parser or rule helper."""
    from extract.uscis_notice import BASIS_BY_FORM
    flags = []
    cases = _uscis_cases(graph)
    other_bases = [f'{BASIS_BY_FORM[form]} {receipt}' for receipt, d in cases for form in [d.split(' ', 1)[0]] if form in BASIS_BY_FORM and 'APPROVAL' in d]
    if other_bases:
        flags.append(Flag('review', 'applicant.filing_category', 'FILING BASIS: the folder has approved petition(s) other than SIJS. ' + '; '.join(other_bases) + '. Part 2 takes exactly one category; the attorney chooses (the SIJS box is filled only by the SIJS policy).', kind='alert'))
    earlier = [f'{receipt} ({d})' for receipt, d in cases if d.startswith('I-485')]
    if earlier:
        flags.append(Flag('review', 'applicant.previously_applied_lpr', 'EARLIER I-485: a notice shows an adjustment application already filed. ' + '; '.join(earlier) + '. Part 4 item 5 (previously applied for permanent residence in the U.S.) must be Yes, with its outcome.', kind='alert'))
    said = graph.get('questionnaire.other_immigration_applications')
    beyond_sijs = [f'{receipt} ({d})' for receipt, d in cases if not d.startswith(('I-360', 'I-765'))]
    if said is not None and said.value == 'No' and beyond_sijs:
        flags.append(Flag('review', 'questionnaire.other_immigration_applications', "The client answered No to 'any immigration process besides SIJS?', but the folder has " + '; '.join(beyond_sijs) + '. Ask the client: Part 4 and Part 9 answers depend on it.', kind='crosscheck'))
    bad = [f'{receipt} ({d})' for receipt, d in cases if any((w in d for w in ('DENIAL', 'INTENT TO DENY', 'REQUEST FOR EVIDENCE', 'REJECTION')))]
    if bad:
        flags.append(Flag('review', 'folder.uscis_cases_problem', 'USCIS NOTICES TO ANSWER: ' + '; '.join(bad) + ': the attorney must review before filing.', kind='alert'))
    return flags

def cross_check(graph: FactGraph) -> list[Flag]:
    """Application parser or rule helper."""
    flags = eligibility_alerts(graph)
    flags += [Flag('review', key, message, kind='crosscheck') for key, message in consistency_findings(graph)]
    flags += [Flag('review', key, message, kind='missing') for key, message in completeness_findings(graph)]
    from name_events import flags as name_flags
    flags += name_flags(graph)
    for self_key, doc_key in CROSS_CHECKS:
        said, documented = (graph.get(self_key), graph.get(doc_key))
        if said is None or documented is None or said.status != 'resolved' or (documented.status != 'resolved'):
            continue
        a, b = (str(said.value), str(documented.value))
        if key_digits(a) != key_digits(b):
            doc = next((s.doc_id for s in documented.sources), 'the document')
            doc = doc.rsplit('.', 1)[0] if doc.lower().endswith('.pdf') else doc
            flags.append(Flag('review', doc_key, f"The client wrote {_us_date(a)} on the questionnaire; {doc} says {_us_date(b)}. The document's value is used on the I-485: confirm with the client which is right.", kind='crosscheck'))
    return flags

def _us_date(value: str) -> str:
    """Application parser or rule helper."""
    import re
    m = re.fullmatch('(\\d{4})-(\\d{2})-(\\d{2})', value)
    return f'{m.group(2)}/{m.group(3)}/{m.group(1)}' if m else value

def key_digits(value: str) -> str:
    import re
    digits = re.sub('\\D', '', value)
    return digits or value.upper()

def process_client_folder(client_id: str, folder: str | Path, rules: list[Rule] | None=None, policies: list[dict[str, Any]] | None=None, firm_profile: dict[str, str] | None=None, questionnaire_map: list[dict[str, Any]] | None=None, questionnaire_text_map: list[dict[str, Any]] | None=None, questionnaire_maps: dict[str, tuple[list, list]] | None=None, use_vision: bool | None=None, case_dir: str | Path | None=None) -> ClientResult:
    """Application parser or rule helper."""
    folder = Path(folder)
    documents: list[tuple[str, str]] = []
    errors: dict[str, str] = {}
    split_info: dict[str, dict[str, Any]] = {}
    paged: dict[str, list[str]] = {}
    for path in sorted(folder.glob('*.pdf')):
        try:
            pages = extract_pages(path)
        except Exception as exc:
            errors[path.name] = str(exc)
            continue
        if pages:
            from extract.sij_order import form_block
            pages[-1] += form_block(path)
        segments = split_documents(pages)
        split_info[path.name] = {'pages': len(pages), 'segments': segments}
        if len(segments) == 1:
            documents.append((path.name, '\n'.join(pages)))
            paged[path.name] = pages
            continue
        for first, last, _kind in segments:
            label = f'p{first + 1}' if first == last else f'p{first + 1}-{last + 1}'
            documents.append((f'{path.name}#{label}', '\n'.join(pages[first:last + 1])))
            paged[f'{path.name}#{label}'] = [''] * first + pages[first:last + 1]
    reader = None
    maps = questionnaire_maps or ({'pt': (questionnaire_map, questionnaire_text_map or [])} if questionnaire_map is not None else None)
    if maps is not None:

        def reader(doc_id: str) -> QuestionnaireReading:
            try:
                return read_questionnaire_pdf(folder / doc_id, maps=maps, use_vision=use_vision)
            except Exception as exc:
                errors[doc_id] = f'questionnaire reading failed: {exc}'
                return QuestionnaireReading()
    result = process_documents(client_id, documents, rules=rules, policies=policies, firm_profile=firm_profile, questionnaire_reader=reader, pages=paged, boundary_context=__import__('document_instances').context(folder, Path(case_dir) if case_dir else None, list(split_info)))
    result.errors.update(errors)
    if case_dir is not None:
        import document_instances
        document_instances.invalidate(Path(case_dir), result.boundary_plans)
        document_instances.stage(Path(case_dir), result.boundary_plans)
    record_documents(result, folder, documents, split_info)
    shadow_document_types(client_id, documents, result.classifications)
    return result

def record_documents(result: ClientResult, folder: str | Path, documents: list[tuple[str, str]], split_info: dict[str, dict] | None=None, source: str | None=None, uploads: list[dict] | None=None) -> None:
    """Application parser or rule helper."""
    try:
        import documents as records
        split_info = {name: {'pages': plan['page_count'], 'segments': [(s['first'], s['last'], s['type']) for s in plan['instances']]} for name, plan in result.boundary_plans.items()}
        result.documents = records.build(folder, result.classifications, result.extracted, split_info, texts=dict(result.document_texts or documents), graph=result.raw_graph, source=source or records.source_of(result.graph.client_id), uploads=uploads)
        result.documents['boundary_plans'] = result.boundary_plans
    except Exception as exc:
        raise RuntimeError('Document evidence records could ficticioq be built; processing has ficticioq completed.') from exc

def shadow_document_types(client_id: str, documents: list[tuple[str, str]], classifications: dict) -> None:
    """Application parser or rule helper."""
    try:
        from learning.shadow import observe_document_types
        observe_document_types(client_id, documents, classifications)
    except Exception:
        pass

def field_to_fact(field_map: dict[str, Any]) -> dict[str, str]:
    """Application parser or rule helper."""
    out: dict[str, str] = {}
    for fact_key, spec in field_map.items():
        for token in json.dumps(spec).split('"'):
            if token.startswith('form1['):
                out.setdefault(token, fact_key)
    return out

def part14_blocks(graph: FactGraph, prefix: str='applicant') -> list[tuple[int, Any]]:
    """Application parser or rule helper."""
    from fill.continuation import Block
    value = lambda key: str(f.value) if (f := graph.get(key)) is not None and f.status == 'resolved' and f.value else ''
    out, n = ([], 1)
    while graph.get(f'{prefix}.p14_block{n}_text') is not None:
        if value(f'{prefix}.p14_block{n}_text'):
            sources = graph.get(f'{prefix}.p14_block{n}_text').sources
            out.append((n, Block(*(value(f'{prefix}.p14_block{n}_{part}') for part in ('page', 'part', 'item', 'text')), source=str(sources[0].raw_value or '') if sources else '')))
        n += 1
    return out

def part14_spot_flags(graph: FactGraph, prefix: str='applicant') -> list[Flag]:
    """Application parser or rule helper."""
    return [Flag('review', f'{prefix}.p14_block{n}_text', f"Part 14 entry {n} has no page, part or item the edition can vouch for: enter them by hand on the form's Part 14 page.", kind='alert') for n, block in part14_blocks(graph, prefix) if not (block.page and block.part and block.item)]

def _part14_continuation(graph: FactGraph, field_map: dict[str, Any], pdf_path: Path, template: Path | None=None) -> list[Flag]:
    """Application parser or rule helper."""
    from fill.continuation import finish_part14
    entries = part14_blocks(graph)
    if not entries:
        return []
    template = Path(template) if template else schema_path.path('template', 'i485')
    value = lambda key: str(f.value) if (f := graph.get(key)) is not None and f.status == 'resolved' and f.value else ''
    layout = finish_part14(pdf_path, [b for _, b in entries], template, family=value('applicant.family_name'), given=value('applicant.given_name'), middle=value('applicant.middle_name'), a_number=value('applicant.a_number'))
    flags = [Flag('review', f'applicant.p14_block{entries[0][0]}_text', problem, kind='overflow') for problem in layout.problems]
    flags += part14_spot_flags(graph)
    if layout.copies:
        first_extra = next((n for n, _ in entries[layout.slots:]), entries[-1][0])
        flags.append(Flag('review', f'applicant.p14_block{first_extra}_text', f"PART 14 CONTINUED: the {len(entries)} entr{('y' if len(entries) == 1 else 'ies')} need more room than the form's own Part 14 page: {layout.copies} cop{('y' if layout.copies == 1 else 'ies')} of the form's Part 14 page added at the end of the PDF. The client signs and dates each copy.", kind='alert'))
    return flags

def finalize_client(result: ClientResult, template_path: str | Path, field_map: dict[str, Any], required_fact_keys: list[str], output_dir: str | Path) -> FilingResult:
    """Application parser or rule helper."""
    output_dir = Path(output_dir)
    mapping = map_facts_to_fields(result.graph, field_map)
    overflow_flags = []
    max_lengths = field_max_lengths(template_path)
    for name, value in list(mapping.values.items()):
        if name in max_lengths and isinstance(value, str) and (len(value) > max_lengths[name]):
            del mapping.values[name]
            fact_key = field_to_fact(field_map).get(name, name)
            overflow_flags.append(Flag('review', fact_key, f'{fact_key}: value {value!r} is longer than the form allows ({max_lengths[name]} characters, field {name}): field LEFT BLANK, enter a shorter value.', kind='overflow'))
    filled_pdf_path = output_dir / 'i485_filled.pdf'
    fill_pdf(template_path, mapping.values, filled_pdf_path)
    overflow_flags += _part14_continuation(result.graph, field_map, filled_pdf_path, Path(template_path))
    flags = validate_graph(result.graph, required_fact_keys) + result.review_flags + overflow_flags
    report = render_report(result.graph.client_id, flags)
    return FilingResult(filled_pdf_path=filled_pdf_path, flags=flags, report=report, unmapped_facts=mapping.unmapped_facts)

def run_batch(clients_root: str | Path, out_root: str | Path, rules: list[Rule] | None=None, policies: list[dict[str, Any]] | None=None, finalize: dict[str, Any] | None=None, firm_profile: dict[str, str] | None=None, questionnaire_map: list[dict[str, Any]] | None=None, questionnaire_text_map: list[dict[str, Any]] | None=None) -> dict[str, ClientResult]:
    """Application parser or rule helper."""
    clients_root = Path(clients_root)
    out_root = Path(out_root)
    results: dict[str, ClientResult] = {}
    for client_dir in sorted((p for p in clients_root.iterdir() if p.is_dir())):
        client_id = client_dir.name
        try:
            result = process_client_folder(client_id, client_dir, rules=rules, policies=policies, firm_profile=firm_profile, questionnaire_map=questionnaire_map, questionnaire_text_map=questionnaire_text_map, case_dir=out_root / client_id)
        except Exception as exc:
            result = ClientResult(graph=FactGraph(client_id), errors={'__client__': str(exc)})
            __import__('document_instances').fail_case(out_root / client_id, client_dir, str(exc))
            results[client_id] = result
            continue
        client_out_dir = out_root / client_id
        from review.state import save_bundle
        try:
            save_bundle(result, client_out_dir, client_dir)
        except Exception as exc:
            __import__('document_instances').fail_case(client_out_dir, client_dir, str(exc))
            result.errors['__bundle__'] = str(exc)
            results[client_id] = result
            continue
        if finalize is not None:
            try:
                from review.state import reviewed_graph
                result.graph = reviewed_graph(client_out_dir)
                result.review_flags += [Flag('blocking', 'documents', message, kind='document_subject') for message in __import__('subject_attribution').problems(client_out_dir)]
                filing = finalize_client(result, output_dir=client_out_dir, **finalize)
                (client_out_dir / 'flag_report.txt').write_text(filing.report, encoding='utf-8')
            except Exception as exc:
                result.errors['__finalize__'] = str(exc)
        results[client_id] = result
    return results
