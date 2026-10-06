"""Domain services for the RAT.

The ingestion and metrics pipeline lives here:

* ``git_ingest`` - walk the history of a repository working copy (including
  ``.git``) and persist commits, per-file diff statistics, and raw author
  identities (grouped into canonical authors by email).
* ``metrics`` - aggregate ``CommitFile`` rows into file, directory,
  repository, and author metrics (added/removed lines, growth, churn,
  modifications, modification frequency, churn rate, ownership) over a
  commit set, filterable by committer-date range, explicit commits, authors,
  reference, and path.
* ``repository_import`` - import repositories from ZIP archives (safe
  extraction) or remote URLs (deep clone), then ingest them in the
  background while the repository row tracks the import status.
* ``mailmap`` - planned: parse the repository's ``.mailmap`` file and resolve
  raw git identities to canonical authors; supports manual merges when no
  mailmap exists.
"""
