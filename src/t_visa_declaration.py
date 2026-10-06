"""Asking a law enforcement agency for Form I-914, Supplement B, Declaration
for Trafficking Victim (edition 01/20/25) -- the T visa's optional evidence
(src/t_visa.py). Sources, read 10/02/2026: 8 CFR 214.204(e)-(h) (eCFR,
current as of 09/30/2026); the Supplement B Instructions (uscis.gov/i-914);
USCIS Policy Manual, Volume 3, Part B, Chapter 3 (current as of 09/23/2026).

  It is optional, carries no special weight and grants nothing; completing it
    is at the official's discretion and needs no formal investigation or
    prosecution (214.204(e)(1)-(6)). So the I-914 never waits for it here.
  The certifying agency is a Federal, State, Tribal or local law enforcement
    agency, prosecutor, judge, labor agency, children's or adult protective
    services, or another authority over trafficking (214.201, "LEA").
  A supervising official signs it (214.204(e)(4)); Part 6 also takes the
    supervisor's name and signature; an original signature, never a copy or a
    typed name (Supplement B Instructions, Part 6). The instructions ask the
    official to complete the form themselves: the draft only types the
    victim's identity (Part 1) and the agency's own details the firm has
    (Part 2), for the official to check; Parts 3-6 stay blank.
  The agency gives the signed form to the applicant, who files it with the
    I-914 or later (the instructions); an agency that withdraws it writes to
    the Vermont Service Center itself.

The packet is the firm's request letter and the pre-filled draft: nothing
here goes to USCIS, and there is no fee (Form G-1055 10/01/26: $0).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from filing_questions import DATE, TEXT, iso, us, value
from holders import CLIENT, producer

TITLE = "Law enforcement declaration request (I-914 Supplement B)"
RETURNED = ["Not yet", "Returned, signed", "The agency declined"]
SECTIONS = [
    ("The certifying agency (Supplement B, Part 2)", "the attorney", [
        ("tvisa.lea_name", "Part 2, 1: the agency (police, prosecutor, labor agency, protective services...)", TEXT, True),
        ("tvisa.lea_official", "Part 2, 2: the certifying official's name, if known", TEXT, False),
        ("tvisa.lea_title", "Part 2, 3: the official's title, if known", TEXT, False),
        ("tvisa.lea_division", "Part 2, 4: the official's division or office, if known", TEXT, False),
        ("tvisa.lea_street", "Part 2, 5: the agency's mailing address: street", TEXT, True),
        ("tvisa.lea_city", "Part 2, 5: city", TEXT, True),
        ("tvisa.lea_state", "Part 2, 5: state (two letters)", TEXT, True),
        ("tvisa.lea_zip", "Part 2, 5: ZIP code", TEXT, True),
        ("tvisa.lea_phone", "Part 2, 6: the agency's daytime phone, if known", TEXT, False),
        ("tvisa.lea_case_number", "Part 2, 11: the agency's case number, if any", TEXT, False),
    ]),
    ("Tracking the request", "the paralegal", [
        ("tvisa.supb_requested_on", "Date the request went to the agency", DATE, False),
        ("tvisa.supb_returned", "Has it come back?", {"type": "choice", "options": RETURNED}, False),
    ]),
]


def derive(graph, today: date):
    import t_visa

    return t_visa.derive(graph, today)  # the agency the crime was reported to fills Part 2 by default


def notes(graph, today: date) -> list[dict[str, str]]:
    v = lambda k: value(graph, k)  # noqa: E731
    asked, back = iso(v("tvisa.supb_requested_on")), v("tvisa.supb_returned")
    out = [{"level": "info", "title": "Optional evidence",
            "text": "Supplement B is optional evidence of the victimization and the cooperation; the agency decides whether to sign it, and USCIS decides "
                    "eligibility (8 CFR 214.204(e), (g)). The I-914 can be filed without it, and the signed form can follow later."},
           {"level": "info", "title": "What the agency does",
            "text": "The official completes Parts 3-5, signs Part 6, and a supervising official signs too, in ink (8 CFR 214.204(e)(4); the "
                    "Supplement B instructions). The draft only types the client's identity and the agency's details for them to check."},
           {"level": "info", "title": "No fee", "text": "Form G-1055: $0 for Supplement B. Nothing here is mailed to USCIS."}]
    if v("tvisa.supb_wanted") == "No":
        out.append({"level": "warn", "title": "Not requested in the T visa questions",
                    "text": "The attorney answered No to asking for Supplement B on the T visa application: confirm before sending."})
    if back == "Returned, signed":
        out.append({"level": "ok", "title": "Returned", "text": "Add the signed original to the folder and put it in the T visa packet's law enforcement exhibit."})
    elif asked:
        out.append({"level": "info", "title": "Sent", "text": f"Requested {us(asked)}. If nothing comes back, the I-914 goes without it."})
    return out


@producer(CLIENT)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    if not (v("applicant.family_name") and v("applicant.dob")):
        out.append("The client's name and date of birth (Part 1) aren't settled in the case yet.")
    return out


def render(client_dir: Path, graph, today: date) -> None:
    """lea_request_letter.pdf: the firm's letter to the agency, on the case office's letterhead (src/fill/cover_letter.py, src/offices.py)."""
    import offices
    from fill.cover_letter import case_facts, load_config, render_response

    v = lambda k: value(graph, k)  # noqa: E731
    config = offices.letter(load_config(), client_dir, v("applicant.physical_state"))
    official = ", ".join(x for x in (v("tvisa.lea_official"), v("tvisa.lea_title")) if x)
    place = " ".join(x for x in (f"{v('tvisa.lea_city')}," if v("tvisa.lea_city") else None, v("tvisa.lea_state"), v("tvisa.lea_zip")) if x)
    to = [x for x in (official, v("tvisa.lea_division"), v("tvisa.lea_name"), v("tvisa.lea_street"), place) if x]
    re_lines = ["Request for Form I-914, Supplement B, Declaration for Trafficking Victim"] + (
        [f"Your case number: {v('tvisa.lea_case_number')}"] if v("tvisa.lea_case_number") else [])
    intro = ("This office represents the person named above, who is applying to U.S. Citizenship and Immigration Services for T nonimmigrant status as "
             "a victim of a severe form of trafficking in persons (INA 101(a)(15)(T)). We respectfully ask your agency to complete and sign the "
             "enclosed Form I-914, Supplement B, Declaration for Trafficking Victim. To save your time we typed the victim's identifying information "
             "in Part 1 and your agency's details we have in Part 2: please check them, and complete the rest yourself.")
    items = [{"text": "Completing Supplement B is at your agency's discretion. It does not require a formal investigation or prosecution, and it "
                      "does not depend on the outcome of one (8 CFR 214.204(e)(5)-(6); Supplement B instructions)."},
             {"text": "A supervising official responsible for the detection, investigation or prosecution of severe forms of trafficking in persons "
                      "signs it (8 CFR 214.204(e)(4)), and Part 6 also takes the supervisor's name and signature, in ink: a copy or a typed name is "
                      "not accepted (Supplement B instructions)."},
             {"text": "USCIS, not your agency, decides whether the applicant is eligible; signing it grants no immigration benefit (8 CFR "
                      "214.204(e)(3), (g))."},
             {"text": "Please return the signed original to this office at the address above. Information about a T visa applicant is protected "
                      "by 8 U.S.C. 1367."}]
    draft = not all(v(k) for k in ("tvisa.lea_name", "tvisa.lea_street", "tvisa.lea_city", "tvisa.lea_state", "tvisa.lea_zip"))
    pdf = render_response(config | {"who": "Victim"}, case_facts(client_dir), today, draft, to or ["[the agency's address]"], re_lines, intro, items)
    (client_dir / "lea_request_letter.pdf").write_bytes(pdf)
