"""Domain services for the RAT.

The ingestion and metrics pipeline lives here (added in the next milestones):

* ``ingestion`` - import a repository from a ZIP archive (containing ``.git``)
  or by deep-cloning a remote URL; walk history and persist commits,
  per-file changes, and raw author identities.
* ``mailmap`` - parse the repository's ``.mailmap`` file and resolve raw
  git identities to canonical authors; supports manual merges when no
  mailmap exists.
* ``metrics`` - aggregate ``Commit`` / ``CommitFile`` rows into per-author,
  per-file, per-directory, and repository-wide metrics, filterable by
  repository, author, path, and commit range (time span or explicit list).
"""
