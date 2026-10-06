"""Connectors to the firm's other systems -- ready, not switched on.

The pipeline reads client documents from clients/<id>/source/*.pdf and keeps
its results in data/. These connectors let that come from, and go back to,
the systems the firm already uses, without changing the pipeline:

  documents in:   local folders (today) · Filevine projects · Google Drive folders
  results out:    nowhere (today) · Filevine (documents + a note) · Google Drive
  staff sign-in:  local accounts (today) · Microsoft 365 · Google Workspace (wired into the review app)
  both ways:      Clio (clio.py): matters and documents in; packets, stages, deadlines out

A source is only ever *mirrored* into clients/<id>/source (sync.py): the
pipeline, the overnight run and the review app keep working on local files,
and all reading stays on the firm's machine. Which connector is active is
schemas/registers/connectors.json; secrets come only from environment variables.
Nothing here is called by the pipeline yet (docs/integrations.md), except Clio,
which an attorney connects in Settings, Connections (its secrets: data/clio, encrypted).
"""

from .base import DocumentSource, RemoteClient, RemoteDoc, ResultSink  # noqa: F401
