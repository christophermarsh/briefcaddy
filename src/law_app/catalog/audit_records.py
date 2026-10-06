"""Logs records; keys retain the legacy global catalog order, not record versions.

Metadata is assembled once by registry; keep existing positions stable.
A newly appended record uses the next unused global position.
"""

from __future__ import annotations

from typing import Any

from .flags import PERSON

RECORDS: dict[int, dict[str, Any]] = {
    49: {"id": "accounts_log", "title": "The staff access log", "area": "logs", "files": ["review_users_access.jsonl"], "where": "data/review_users_access.jsonl", "format": "JSON Lines",
     "written_by": "The review app, for every sign-in, failure, sign-out, lockout and account change (src/review/auth.py log). Never a password or a code.",
     "version": None, "versions": [(1, "The only shape so far; no version field.")],
     "fields": [
         ("at", "text", "When (with its offset).", ""),
         ("event", "text", "What happened: signed_in, signed_out, sign_in_failed, account_locked, password_changed, account_added, account_changed, case_access, calendar_address_made, calendar_address_revoked, calendar_feed (a calendar address was read: one row a day for each address), support_let_in (who let support in, the hours, masked or plain), support_request (every request support made: the method, the path, the case and whether it was refused), support_ended (by itself at its hour, or by an attorney) and the like.", ""),
         ("email", "text", "The account the event is about (or, for a sign-in, the person at the keyboard).", ""),
         ("by", "text", "The attorney who did it, when it was done to someone else's account.", ""),
         ("address", "text", "The network address the request came from.", ""),
         ("client", "text", "The case, for a change to who may open it (its case id, which can be a name).", PERSON),
         ("(other fields)", "mixed", "Details of the event: role, reason, how, minutes, times_today, action, person, restricted, kind (person or firm, for a calendar address).", ""),
     ], "exported": True},
    50: {"id": "conflicts_log", "title": "The conflict log", "area": "logs", "files": ["conflict_checks.jsonl"], "where": "data/conflict_checks.jsonl", "format": "JSON Lines",
     "written_by": "The conflict search, for every search and every decision (src/conflicts.py): Add a client, a search by hand under Settings, the Docketwise import, "
                   "the Clio sync, an attorney's decision. Appended only, owner-only (0600). Settings, Conflict checks shows it to attorneys.",
     "version": None, "versions": [(1, "The only shape so far; no version field.")],
     "fields": [
         ("kind", "text", "search or decision.", ""),
         ("id", "text", "The row's id (a decision names its search by it).", ""),
         ("at", "text", "When (with its offset).", ""),
         ("by", "text", "Who ran it or decided: a staff member's name, the Docketwise import or the Clio sync.", ""),
         ("role", "text", "attorney or paralegal, when accounts are on.", ""),
         ("purpose", "text", "Why: add (adding a client), hand (by hand), import (Docketwise), sync (Clio), cli (the command line's import), case (again for a case waiting).", ""),
         ("case", "text", "The new case (its case id, which can be a name), when there is one.", PERSON),
         ("query", "object", "A search: what was searched for: name, other_names, dob, a_number, passport, parties.", PERSON),
         ("hits", "list", "A search: every hit in full: for, case, person, role, relationship, adverse, restricted, score, strength, sentence, names, birth_dates, client.", PERSON),
         ("matched", "object", "A search: for each person searched for, every case where someone matches by name or number alone, dates of birth left out (what decides the line a person who may not open the case sees).", PERSON),
         ("more", "integer", "A search written before matched was kept: how many weaker hits were left out.", ""),
         ("decision, words, reason, search", "text", "A decision: none, declined, waived or undecided; its words; the reason; the search it was about.", PERSON),
     ], "exported": True},
    51: {"id": "find_questions", "title": "The questions asked of Find across the firm", "area": "logs", "files": ["find_questions.jsonl"], "where": "data/find_questions.jsonl",
     "format": "JSON Lines",
     "written_by": "The review app, for every question asked on the Search page's Find across the firm (src/find.py log_question). Appended only, owner-only (0600). "
                   "The Search page shows it to attorneys; the event ledger's row for each question has who, when and the question's length, never its text.",
     "version": None, "versions": [(1, "The only shape so far; no version field (brief N1, 10/04/2026).")],
     "fields": [
         ("at", "text", "When (with its offset).", ""),
         ("who", "text", "Who asked: the signed-in staff member's name.", ""),
         ("role", "text", "attorney or paralegal, when accounts are on.", ""),
         ("length", "integer", "The question's length in characters.", ""),
         ("question", "text", "The question as typed: it can name a person or hold the firm's work product.", PERSON),
         ("hits", "integer", "How many passages the person who asked was shown.", ""),
     ], "exported": True},
    52: {"id": "views", "title": "The view log", "area": "logs", "files": ["review_views.jsonl"], "where": "data/review_views.jsonl", "format": "JSON Lines",
     "written_by": "The review app, once for every opening of a case, scan, document, filled form, packet or the portal answers (src/review/server.py viewed). "
                   "Never a value from the case.", "version": None, "versions": [(1, "The only shape so far; no version field.")],
     "fields": [
         ("at", "text", "When (with its offset).", ""),
         ("email", "text", "The signed-in person's sign-in, or null when the app runs without accounts.", ""),
         ("name", "text", "Their name.", ""),
         ("role", "text", "attorney or paralegal.", ""),
         ("client", "text", "The case that was opened (its case id, which can be a name).", PERSON),
         ("kind", "text", "What was opened: case, scan, document, filled_form, packet, translation, declaration, online_bundle, answers, rfe_response, review_bundle, documents, link "
                          "or case_summary (the summary for the attorney).", ""),
         ("file", "text", "The document's file name, when one was opened: a name the client or the office chose, which can name a person.", PERSON),
         ("address", "text", "The network address the request came from.", ""),
         ("restricted", "boolean", "True when the case was restricted when it was opened.", ""),
     ], "exported": True},
    53: {"id": "events", "title": "The event ledger", "area": "logs", "files": ["events-*.jsonl"], "where": "data/events-YYYY-MM.jsonl (named data/events.jsonl; one file a month)",
     "format": "JSON Lines", "written_by": "Every writer in the product, after it has written its own record (src/events.py record). Appended only: nothing rewrites or deletes a row.",
     "version": None, "versions": [(1, "The only shape so far; no version field. Added without raising it (10/04/2026): prev and hash, the chain (brief R2): a row written "
                                       "before it has neither, and the check (tools/verify_ledger.py) counts those apart.")],
     "fields": [
         ("at", "text", "When, with its offset: the firm's own time, as the clock wrote it.", ""),
         ("who", "text", "The person: a staff member's name, or The overnight run, The client, The importer, a connector's name, The product.", ""),
         ("role", "text", "attorney, paralegal, client, system or staff (a person using the app with no staff accounts).", ""),
         ("via", "text", "How: staff, overnight, portal, importer, connector, tool or system.", ""),
         ("case", "text", "The case's id (the client's folder name, which can be a name), or null for a change to the firm's own records.", PERSON),
         ("kind", "text", "Which record changed (the kinds are listed below).", ""),
         ("version", "integer", "The record's version after the write (a record with no version field is version 1).", ""),
         ("action", "text", "What was done, in one word.", ""),
         ("what", "text", "What changed, in a short plain sentence. Never a person's data: a fact is named by its key in words, never by its value.", ""),
         ("prev", "text", "The hash of the row before this one in the ledger (the last row of the month before, for a month's first row); empty for the first row ever and for the "
                          "first row after rows written before the chain.", ""),
         ("hash", "text", "The SHA-256 of this row's own canonical JSON without the hash (keys sorted, no spaces, characters as they are): a row changed, removed or moved after "
                          "it was written no longer matches the row after it.", ""),
     ], "exported": True},
    54: {"id": "ledger_anchors", "title": "The ledger's daily seals", "area": "logs", "files": ["ledger_anchors.jsonl"], "where": "data/ledger_anchors.jsonl", "format": "JSON Lines",
     "written_by": "The overnight run, once for each complete day that has rows in the event ledger (src/ledger_seal.py nightly), only when the ledger matches its chain. "
                   "Appended only, owner-only (0600). The firm can write the lines down or print them (tools/verify_ledger.py --anchors): a changed past cannot match them.",
     "version": None, "versions": [(1, "The only shape so far; no version field (brief R2, 10/04/2026).")],
     "fields": [
         ("day", "text", "The day sealed (YYYY-MM-DD, the firm's day).", ""),
         ("at", "text", "When the day's last row was written (with its offset).", ""),
         ("hash", "text", "The hash of the day's last row of the ledger: all the rows before it are held by it.", ""),
         ("rows", "integer", "How many rows of the ledger that day has.", ""),
     ], "exported": True},
    55: {"id": "ledger_redactions", "title": "The purges that blanked a case's rows in the ledger", "area": "logs", "files": ["ledger_redactions.jsonl"],
     "where": "data/ledger_redactions.jsonl", "format": "JSON Lines",
     "written_by": "The purge job, once for each purge (src/purge.py): a purged case's rows are blanked in place, keeping their time and their place in the chain, and "
                   "this line is what the ledger's check accepts them by (src/ledger_seal.py). Appended only, owner-only (0600). It holds no case id.",
     "version": None, "versions": [(1, "The only shape so far; no version field (briefs Q1 and R2 reconciled, 10/05/2026).")],
     "fields": [
         ("purge", "text", "The purge's id (data/purges.json names its case).", ""),
         ("rows", "integer", "How many rows of the ledger the purge blanked.", ""),
         ("digest", "text", "The SHA-256 of the blanked rows' hashes, in the ledger's order, one a line.", ""),
         ("at", "text", "When (with its offset).", ""),
     ], "exported": True},
    56: {"id": "ledger_check", "title": "The last check of the ledger", "area": "logs", "files": ["ledger_check.json"], "where": "data/ledger_check.json", "format": "JSON object",
     "written_by": "The overnight run, every night (src/ledger_seal.py nightly); read by the Settings line, so a view never walks the ledger. Owner-only (0600).",
     "version": None, "versions": [(1, "The only shape so far; no version field (brief R2, 10/04/2026).")],
     "fields": [
         ("at", "text", "When the check ran (with its offset).", ""),
         ("ok", "boolean", "True when every row, link and seal matched.", ""),
         ("rows", "integer", "How many rows of the chain were checked.", ""),
         ("before", "integer", "How many rows were written before the chain began.", ""),
         ("line", "text", "The check's one line: intact, or the first row that does not match (its time, who, which file, and whether it was changed, removed or cut short).", ""),
     ], "exported": False},
}
