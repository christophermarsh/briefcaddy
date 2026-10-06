"""Positive current report authority applies to all SQL count outputs."""
import query


def test_missing_and_wrong_case_index_ids_cannot_leak_document_counts(tmp_path, monkeypatch):
    path = tmp_path / "fictional-query.db"
    with query.connect(path) as database:
        for case, doc, protected in [("allowed", "ordinary", 0), ("allowed", "private", 1), ("unknown", "orphan", 0), ("unknown", "secret", 1), ("ALLOWED", "wrong-case", 1)]:
            database.execute("insert into documents(case_id,doc_id,type,quality,confidential) values(?,?,?,?,?)", (case, doc, "passport", "readable", protected))
    monkeypatch.setattr(query, "fresh", lambda *_args, **_kwargs: query.open_read(path))
    ordinary, qualities, left = query.document_counts(tmp_path, shown=set(), permitted={"allowed"})
    assert ordinary == [("passport", 1, 1)] and qualities == [("readable", 1)] and left == 1
    assert query.document_counts(tmp_path, shown=set(), permitted=set()) == ([], [], 0)
    all_allowed = query.document_counts(tmp_path, shown={"allowed"}, permitted={"allowed"})
    assert all_allowed == ([("passport", 2, 1)], [("readable", 2)], 0)
    # Omitted positive scope keeps legacy pure-query caller behavior.
    assert query.document_counts(tmp_path, shown=None)[0] == [("passport", 5, 3)]
