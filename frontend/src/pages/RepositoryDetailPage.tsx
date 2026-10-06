/**
 * Repository analysis view.
 *
 * Implements the dashboard filters from the spec: repository (this page),
 * author, file/directory (path), and commits — either a time period
 * (from/to) or a manually selected commit list. All filters narrow the same
 * commit set H used by the four metric tables: repository root, files,
 * directories, and authors.
 */
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  fetchAuthorMetrics,
  fetchDirectoryMetrics,
  fetchFileMetrics,
  fetchRepositoryMetrics,
} from '../api/metrics'
import { getRepository, listAuthors, listCommits, listFiles } from '../api/repositories'
import { ThemeToggle } from '../theme/ThemeToggle'
import type {
  Author,
  AuthorMetricsResponse,
  CommitSummary,
  DirectoryMetricsResponse,
  FileMetricsResponse,
  MetricsQuery,
  ObjectMetrics,
  Repository,
  RepositoryMetricsResponse,
} from '../types'

const MAX_LISTED_COMMITS = 200

function directoryOptions(files: string[]): string[] {
  const directories = new Set<string>()
  for (const file of files) {
    const parts = file.split('/')
    parts.pop()
    while (parts.length > 0) {
      directories.add(parts.join('/'))
      parts.pop()
    }
  }
  return [...directories].sort()
}

function toIso(value: string): string | undefined {
  if (!value) return undefined
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString()
}

function formatNumber(value: number): string {
  return value.toLocaleString()
}

function formatRate(value: number): string {
  return value.toFixed(2)
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`
}

function shortSha(sha: string): string {
  return sha.slice(0, 7)
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

interface MetricsBundle {
  repository: RepositoryMetricsResponse
  files: FileMetricsResponse
  directories: DirectoryMetricsResponse
  authors: AuthorMetricsResponse
}

function ObjectMetricsTable({
  title,
  objects,
  emptyText,
}: {
  title: string
  objects: ObjectMetrics[]
  emptyText: string
}) {
  return (
    <section className="panel">
      <h2>{title}</h2>
      {objects.length === 0 ? (
        <p className="muted">{emptyText}</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Path</th>
                <th className="num">Added</th>
                <th className="num">Removed</th>
                <th className="num">Growth</th>
                <th className="num">Churn</th>
                <th className="num">Mods (n)</th>
                <th className="num">Freq (η)</th>
                <th className="num">Rate (ρ)</th>
              </tr>
            </thead>
            <tbody>
              {objects.map((object) => (
                <tr key={object.path}>
                  <td className="mono">{object.path === '' ? '/' : object.path}</td>
                  <td className="num">+{formatNumber(object.added_lines)}</td>
                  <td className="num">−{formatNumber(object.removed_lines)}</td>
                  <td className="num">
                    {object.growth > 0 ? '+' : ''}
                    {formatNumber(object.growth)}
                  </td>
                  <td className="num">{formatNumber(object.churn)}</td>
                  <td className="num">{formatNumber(object.modifications)}</td>
                  <td className="num">{formatRate(object.modification_frequency)}</td>
                  <td className="num">{formatRate(object.churn_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

export function RepositoryDetailPage() {
  const params = useParams<{ repositoryId: string }>()
  const repositoryId = Number(params.repositoryId)

  const [repository, setRepository] = useState<Repository | null>(null)
  const [authors, setAuthors] = useState<Author[]>([])
  const [commits, setCommits] = useState<CommitSummary[]>([])
  const [files, setFiles] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Draft filter values (applied on submit).
  const [authorId, setAuthorId] = useState('')
  const [path, setPath] = useState('')
  const [fromValue, setFromValue] = useState('')
  const [toValue, setToValue] = useState('')
  const [selectedCommits, setSelectedCommits] = useState<string[]>([])

  const [applied, setApplied] = useState<MetricsQuery>({})
  const [metrics, setMetrics] = useState<MetricsBundle | null>(null)
  const [metricsLoading, setMetricsLoading] = useState(false)
  const [metricsError, setMetricsError] = useState<string | null>(null)

  const loadRepository = useCallback(async () => {
    try {
      setRepository(await getRepository(repositoryId))
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load repository')
    }
  }, [repositoryId])

  const loadLists = useCallback(async () => {
    try {
      const [authorList, commitList, fileList] = await Promise.all([
        listAuthors(repositoryId),
        listCommits(repositoryId, MAX_LISTED_COMMITS),
        listFiles(repositoryId),
      ])
      setAuthors(authorList)
      setCommits(commitList)
      setFiles(fileList.files)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load repository data')
    }
  }, [repositoryId])

  useEffect(() => {
    async function loadAll() {
      setLoading(true)
      await Promise.all([loadRepository(), loadLists()])
      setLoading(false)
    }
    void loadAll()
  }, [loadRepository, loadLists])

  // Poll while the repository is still being ingested.
  const status = repository?.status
  useEffect(() => {
    if (status !== 'pending' && status !== 'ingesting') return
    const timer = setInterval(() => {
      void loadRepository()
    }, 2500)
    return () => clearInterval(timer)
  }, [status, loadRepository])

  // Once ingestion finishes, refresh the filter option lists.
  useEffect(() => {
    if (status === 'ready' && !loading) void loadLists()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status])

  useEffect(() => {
    if (!repository || repository.status !== 'ready') return
    let cancelled = false
    setMetricsLoading(true)
    setMetricsError(null)
    Promise.all([
      fetchRepositoryMetrics(repository.id, applied),
      fetchFileMetrics(repository.id, applied),
      fetchDirectoryMetrics(repository.id, applied),
      fetchAuthorMetrics(repository.id, applied),
    ])
      .then(([repositoryMetrics, fileMetrics, directoryMetrics, authorMetrics]) => {
        if (cancelled) return
        setMetrics({
          repository: repositoryMetrics,
          files: fileMetrics,
          directories: directoryMetrics,
          authors: authorMetrics,
        })
      })
      .catch((err) => {
        if (!cancelled) {
          setMetricsError(err instanceof Error ? err.message : 'Failed to load metrics')
        }
      })
      .finally(() => {
        if (!cancelled) setMetricsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [repository, applied])

  const pathOptions = useMemo(() => [...files, ...directoryOptions(files)], [files])

  function handleApply(event: FormEvent) {
    event.preventDefault()
    setApplied({
      authorId: authorId || undefined,
      path: path.trim() || undefined,
      from: toIso(fromValue),
      to: toIso(toValue),
      commits: selectedCommits.length > 0 ? selectedCommits : undefined,
    })
  }

  function handleReset() {
    setAuthorId('')
    setPath('')
    setFromValue('')
    setToValue('')
    setSelectedCommits([])
    setApplied({})
  }

  function toggleCommit(sha: string) {
    setSelectedCommits((previous) =>
      previous.includes(sha) ? previous.filter((item) => item !== sha) : [...previous, sha],
    )
  }

  const activeFilters = useMemo(() => {
    const chips: string[] = []
    const chosenAuthor = authors.find((author) => String(author.id) === applied.authorId)
    if (chosenAuthor) chips.push(`Author: ${chosenAuthor.display_name}`)
    if (applied.path) chips.push(`Path: ${applied.path}`)
    if (applied.from) chips.push(`From: ${formatDate(applied.from)}`)
    if (applied.to) chips.push(`To: ${formatDate(applied.to)}`)
    if (applied.commits?.length) chips.push(`${applied.commits.length} selected commit(s)`)
    return chips
  }, [applied, authors])

  if (loading && !repository) {
    return <div className="page-loading">Loading repository…</div>
  }

  return (
    <div className="dashboard">
      <header className="topbar">
        <div className="topbar-brand">
          <span className="brand-mark">RAT</span>
          <Link to="/" className="topbar-title topbar-link">
            ← All repositories
          </Link>
        </div>
        <div className="topbar-user">
          <span className="topbar-title">{repository?.name ?? 'Repository'}</span>
          {repository && (
            <span className={`badge badge-${repository.status}`}>{repository.status}</span>
          )}
          <ThemeToggle />
        </div>
      </header>

      <main className="content">
        {error && <div className="alert alert-error">{error}</div>}

        {repository && repository.status !== 'ready' && (
          <section className="panel">
            <h2>
              {repository.status === 'error'
                ? 'Import failed'
                : 'Repository is being ingested…'}
            </h2>
            {repository.status === 'error' ? (
              <p className="alert alert-error">{repository.error_message ?? 'Unknown error'}</p>
            ) : (
              <p className="muted">This page refreshes automatically when ingestion finishes.</p>
            )}
            <div className="repo-meta">
              <span>Source: {repository.source_type === 'remote' ? 'URL clone' : 'ZIP upload'}</span>
              {repository.source_url && <span className="mono">{repository.source_url}</span>}
            </div>
          </section>
        )}

        {repository && repository.status === 'ready' && (
          <>
            <section className="repo-meta panel">
              <span>
                <strong>{formatNumber(repository.commit_count ?? 0)}</strong> commits
              </span>
              <span>
                <strong>{formatNumber(repository.author_count ?? 0)}</strong> authors
              </span>
              <span>
                branch <span className="mono">{repository.default_branch ?? '—'}</span>
              </span>
              <span>
                HEAD <span className="mono">{shortSha(repository.head_sha ?? '')}</span>
              </span>
              <span>ingested {formatDate(repository.last_ingested_at)}</span>
            </section>

            <section className="panel">
              <h2>Filters</h2>
              <form className="filter-form" onSubmit={handleApply}>
                <div className="filter-grid">
                  <label className="field">
                    <span>Author</span>
                    <select value={authorId} onChange={(event) => setAuthorId(event.target.value)}>
                      <option value="">All authors</option>
                      {authors.map((author) => (
                        <option key={author.id} value={author.id}>
                          {author.display_name}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="field">
                    <span>File or directory</span>
                    <input
                      type="text"
                      list="path-options"
                      value={path}
                      onChange={(event) => setPath(event.target.value)}
                      placeholder="e.g. sub/ or sub/b.txt"
                    />
                    <datalist id="path-options">
                      {pathOptions.map((option) => (
                        <option key={option} value={option} />
                      ))}
                    </datalist>
                  </label>

                  <label className="field">
                    <span>From (inclusive)</span>
                    <input
                      type="datetime-local"
                      value={fromValue}
                      onChange={(event) => setFromValue(event.target.value)}
                    />
                  </label>

                  <label className="field">
                    <span>To (exclusive)</span>
                    <input
                      type="datetime-local"
                      value={toValue}
                      onChange={(event) => setToValue(event.target.value)}
                    />
                  </label>
                </div>

                <div className="field">
                  <span>
                    Select specific commits{' '}
                    <em className="optional">
                      (optional; non-merge commits only are measured)
                    </em>
                  </span>
                  <div className="commit-list">
                    {commits.length === 0 && <p className="muted">No commits ingested.</p>}
                    {commits.map((commit) => (
                      <label
                        key={commit.id}
                        className={`commit-row${commit.is_merge ? ' commit-row-merge' : ''}`}
                      >
                        <input
                          type="checkbox"
                          disabled={commit.is_merge}
                          checked={selectedCommits.includes(commit.sha)}
                          onChange={() => toggleCommit(commit.sha)}
                        />
                        <span className="mono commit-sha">{shortSha(commit.sha)}</span>
                        <span className="commit-message">{commit.message ?? '(no message)'}</span>
                        <span className="commit-author">{commit.author_name ?? '—'}</span>
                        <span className="commit-date">{formatDate(commit.committed_at)}</span>
                        {commit.is_merge && <span className="badge badge-source">merge</span>}
                      </label>
                    ))}
                  </div>
                </div>

                <div className="filter-actions">
                  <button type="submit" className="btn btn-primary" disabled={metricsLoading}>
                    Apply filters
                  </button>
                  <button type="button" className="btn btn-ghost" onClick={handleReset}>
                    Reset
                  </button>
                  {activeFilters.map((chip) => (
                    <span key={chip} className="filter-chip">
                      {chip}
                    </span>
                  ))}
                </div>
              </form>
            </section>

            {metricsError && <div className="alert alert-error">{metricsError}</div>}

            {metrics && (
              <>
                <section className="stat-row">
                  <div className="stat-card">
                    <span className="stat-value">
                      {formatNumber(metrics.files.commit_count)}
                    </span>
                    <span className="stat-label">Commits in set (|H|)</span>
                  </div>
                  <div className="stat-card">
                    <span className="stat-value">
                      +{formatNumber(metrics.repository.repository.added_lines)}
                    </span>
                    <span className="stat-label">Added lines</span>
                  </div>
                  <div className="stat-card">
                    <span className="stat-value">
                      −{formatNumber(metrics.repository.repository.removed_lines)}
                    </span>
                    <span className="stat-label">Removed lines</span>
                  </div>
                  <div className="stat-card">
                    <span className="stat-value">
                      {metrics.repository.repository.growth > 0 ? '+' : ''}
                      {formatNumber(metrics.repository.repository.growth)}
                    </span>
                    <span className="stat-label">Growth (Δ)</span>
                  </div>
                  <div className="stat-card">
                    <span className="stat-value">
                      {formatNumber(metrics.repository.repository.churn)}
                    </span>
                    <span className="stat-label">Churn (λ)</span>
                  </div>
                  <div className="stat-card">
                    <span className="stat-value">
                      {formatNumber(metrics.repository.repository.modifications)}
                    </span>
                    <span className="stat-label">Modifications (n)</span>
                  </div>
                </section>

                <ObjectMetricsTable
                  title="Files"
                  objects={metrics.files.files}
                  emptyText="No file changes in this commit set."
                />
                <ObjectMetricsTable
                  title="Directories"
                  objects={metrics.directories.directories}
                  emptyText="No directory changes in this commit set."
                />

                <section className="panel">
                  <h2>Authors — {metrics.authors.path === '' ? 'whole repository' : metrics.authors.path}</h2>
                  {metrics.authors.authors.length === 0 ? (
                    <p className="muted">No author activity in this commit set.</p>
                  ) : (
                    <div className="table-wrap">
                      <table className="table">
                        <thead>
                          <tr>
                            <th>Author</th>
                            <th className="num">Modifications (n)</th>
                            <th className="num">Churn (λ)</th>
                            <th className="num">Ownership (ω)</th>
                          </tr>
                        </thead>
                        <tbody>
                          {metrics.authors.authors.map((author) => (
                            <tr key={author.author_id}>
                              <td>{author.display_name}</td>
                              <td className="num">{formatNumber(author.modifications)}</td>
                              <td className="num">{formatNumber(author.churn)}</td>
                              <td className="num">{formatPercent(author.ownership)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </section>
              </>
            )}

            {metricsLoading && <p className="muted">Loading metrics…</p>}
          </>
        )}
      </main>
    </div>
  )
}
