"""Application parser or rule helper."""
from __future__ import annotations
from factgraph import FactGraph
from .engine import Rule

def _overstay_01(graph: FactGraph) -> dict[str, str]:
    admit_until = graph.get('applicant.i94_admit_until_date').value
    priority_date = graph.get('applicant.i360_priority_date').value
    overstayed = admit_until < priority_date
    value = 'Yes' if overstayed else 'No'
    return {'applicant.part9.violated_nonimmigrant_status': value, 'applicant.part9.unlawfully_present_since_1997': value}
OVERSTAY_01 = Rule(rule_id='OVERSTAY-01', inputs=['applicant.i94_admit_until_date', 'applicant.i360_priority_date'], outputs=['applicant.part9.violated_nonimmigrant_status', 'applicant.part9.unlawfully_present_since_1997'], requires_absent=['applicant.interim_status_extension'], apply=_overstay_01, plain_text="If the I-94's admit-until date is earlier than the I-360's priority date and no interim status-extension document exists, Part 9 Item 13 (violated the terms of a nonimmigrant status) and Item 74 (unlawfully present since April 1, 1997) are both answered Yes; otherwise both No. If a status extension is in the folder, a person decides.", source='firm practice, see decisions.md 2026-09-29')

def _name_01(graph: FactGraph) -> dict[str, str]:
    married_name = graph.get('applicant.marriage_certificate_name').value
    return {'applicant.current_legal_name': married_name}
NAME_01 = Rule(rule_id='NAME-01', inputs=['applicant.birth_name', 'applicant.marriage_certificate_name'], outputs=['applicant.current_legal_name'], apply=_name_01, plain_text="A person's current legal name is the most recent name supported by a marriage certificate (the only name-change document the rule reads today), not simply the earliest identity document on file. With no marriage certificate in the folder, this rule does not fire.", source='firm practice, see decisions.md 2026-09-29')

def _citizenship_01(graph: FactGraph) -> dict[str, str]:
    citizenship_fact = graph.get('applicant.citizenship')
    if citizenship_fact is None or citizenship_fact.status != 'conflict':
        return {}
    country_of_birth = graph.get('applicant.country_of_birth').value
    return {'applicant.citizenship': country_of_birth}
CITIZENSHIP_01 = Rule(rule_id='CITIZENSHIP-01', inputs=['applicant.country_of_birth'], outputs=['applicant.citizenship'], apply=_citizenship_01, plain_text='When documents report different citizenships for the client (more than one citizenship), the country of birth is what gets reported. A client with only one citizenship never triggers this rule.', source='firm practice, see decisions.md 2026-09-29')

def _nta_01(graph: FactGraph) -> dict[str, str]:
    out = {'applicant.part9.in_removal_proceedings': 'Yes'}
    status = graph.get('applicant.nta_admission_status')
    if status is not None and status.status == 'resolved':
        out['applicant.entered_without_inspection'] = 'Yes' if status.value == 'ficticioq_admitted_or_paroled' else 'No'
    return out
NTA_01 = Rule(rule_id='NTA-01', inputs=['applicant.nta_present'], outputs=['applicant.part9.in_removal_proceedings', 'applicant.entered_without_inspection'], apply=_nta_01, plain_text='A notice to appear is evidence of the notice and its recorded allegations. Keep marked admission options and source facts distinct; attorney review is required before using them in filing answers. A notice alone does not establish every immigration-history fact.', source='firm practice, see decisions.md 2026-09-30')

def _crim_01(graph: FactGraph) -> dict[str, str]:
    return {'applicant.part9.arrested_cited_charged_detained': 'Yes'}
CRIM_01 = Rule(rule_id='CRIM-01', inputs=['applicant.criminal_record_present'], outputs=['applicant.part9.arrested_cited_charged_detained'], apply=_crim_01, plain_text='A criminal docket or charging record in the folder means the client was at least charged: Part 9 Item 22 (EVER arrested, cited, charged or detained) is answered Yes, even if the charge was later dismissed. It says nothing about Item 23 (committed a crime): a charge is not an admission.', source='firm practice, see decisions.md 2026-09-30')

def _arrival_01(graph: FactGraph) -> dict[str, str]:
    return {}
ARRIVAL_01 = Rule(rule_id='ARRIVAL-01', inputs=[], outputs=[], apply=_arrival_01, plain_text='The client\'s last arrival (Part 1 items 10 and 11) is taken from the highest source: the I-94\'s own arrival date, then a government paper in the folder (a Notice to Appear, then a Record of Deportable/Inadmissible Alien), then what the client wrote. A government paper outranks the client\'s own statement, and the I-94\'s date outranks a notice\'s "on or about" date. A notice that lists more than one arrival, or states two ways of arriving, is not used: a person chooses. The client\'s own answer is kept and shown on a card, and what a person saves on that card is final.', source="firm practice, see decisions.md 2026-10-03 (K2): a government paper outranks the client's statement, the tier rule in docs/GRAPH_MODEL.md")
ALL_RULES = [OVERSTAY_01, NAME_01, CITIZENSHIP_01, NTA_01, CRIM_01, ARRIVAL_01]
