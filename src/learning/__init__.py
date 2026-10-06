"""How the system gets smarter for the firm (docs/learning.md).

  store.py     -- the learning store: one SQLite file (data/learning.db)
  decision.py  -- local decision models (Ollama /v1/systemone, e.g. Nimble)
  shadow.py    -- shadow mode: a model answers alongside the rules, recorded,
                  never acted on
  report.py    -- how a shadow model is doing, and whether to trust it

Nothing here changes what the pipeline decides: learned changes go live
only after an attorney approves them.
"""
