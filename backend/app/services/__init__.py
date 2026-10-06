"""Domain services for the RAT.

The ingestion and metrics pipeline lives here:

* ``git_ingest`` - walk the history of a repository working copy (including
  ``.git``) and persist commits, per-file diff statistics, and raw author
  identities (grouped into canonical authors by email).
* ``metrics`` - aggregate ``CommitFile`` rows into file and directory metrics
  (added/removed lines, growth, churn) over a commit set, filterable by
  committer-date range, explicit commits, authors, reference, and path.
* ``mailmap`` - planned: parse the repository's ``.mailmap`` file and resolve
  raw git identities to canonical authors; supports manual merges when no
  mailmap exists.
"""
