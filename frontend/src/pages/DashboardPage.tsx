/**
 * Dashboard: repository management.
 *
 * Lists every repository of the signed-in user (with live status while an
 * import runs), and offers the two import forms required by the spec: a ZIP
 * archive of a working copy (including .git) and a remote URL that is
 * deeply cloned. Clicking a ready repository opens the metrics view.
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { ThemeToggle } from '../theme/ThemeToggle'
import {
  cloneRepository,
  deleteRepository,
  listRepositories,
  uploadRepository,
} from '../api/repositories'
import type { Repository, RepositoryStatus } from '../types'

const STATUS_LABELS: Record<RepositoryStatus, string> = {
  pending: 'Queued',
  ingesting: 'Ingesting…',
  ready: 'Ready',
  error: 'Error',
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

export function DashboardPage() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  const [repositories, setRepositories] = useState<Repository[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadName, setUploadName] = useState('')
  const [cloneUrl, setCloneUrl] = useState('')
  const [cloneName, setCloneName] = useState('')
  const [busy, setBusy] = useState<'upload' | 'clone' | null>(null)
  const [importError, setImportError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    try {
      setRepositories(await listRepositories())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load repositories')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // Poll while any import is queued or running so statuses update live.
  const importing = repositories.some(
    (repository) => repository.status === 'pending' || repository.status === 'ingesting',
  )
  useEffect(() => {
    if (!importing) return
    const timer = setInterval(() => {
      void load()
    }, 2500)
    return () => clearInterval(timer)
  }, [importing, load])

  const totals = useMemo(
    () => ({
      commits: repositories.reduce((sum, item) => sum + (item.commit_count ?? 0), 0),
      developers: repositories.reduce((sum, item) => sum + (item.author_count ?? 0), 0),
      ready: repositories.filter((item) => item.status === 'ready').length,
    }),
    [repositories],
  )

  async function handleLogout() {
    await logout()
    navigate('/login', { replace: true })
  }

  async function handleUpload(event: FormEvent) {
    event.preventDefault()
    if (!uploadFile) return
    setBusy('upload')
    setImportError(null)
    try {
      await uploadRepository(uploadFile, uploadName)
      setUploadFile(null)
      setUploadName('')
      if (fileInputRef.current) fileInputRef.current.value = ''
      await load()
    } catch (err) {
      setImportError(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setBusy(null)
    }
  }

  async function handleClone(event: FormEvent) {
    event.preventDefault()
    if (!cloneUrl.trim()) return
    setBusy('clone')
    setImportError(null)
    try {
      await cloneRepository(cloneUrl, cloneName)
      setCloneUrl('')
      setCloneName('')
      await load()
    } catch (err) {
      setImportError(err instanceof Error ? err.message : 'Clone failed')
    } finally {
      setBusy(null)
    }
  }

  async function handleDelete(repository: Repository) {
    if (!window.confirm(`Delete "${repository.name}" and all of its analysis data?`)) {
      return
    }
    try {
      await deleteRepository(repository.id)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  function openRepository(repository: Repository) {
    if (repository.status === 'ready') {
      navigate(`/repositories/${repository.id}`)
    }
  }

  return (
    <div className="dashboard">
      <header className="topbar">
        <div className="topbar-brand">
          <span className="brand-mark">RAT</span>
          <span className="topbar-title">Repository Analysis Tool</span>
        </div>
        <div className="topbar-user">
          <span className="user-chip">{user?.username}</span>
          <ThemeToggle />
          <button type="button" className="btn btn-ghost" onClick={handleLogout}>
            Sign out
          </button>
        </div>
      </header>

      <main className="content">
        <section className="welcome">
          <h1>Welcome back, {user?.full_name ?? user?.username}</h1>
          <p>
            Import a repository from a ZIP archive (with its .git directory) or clone
            one from a remote URL, then explore its metrics.
          </p>
        </section>

        <section className="stat-row">
          <div className="stat-card">
            <span className="stat-value">{repositories.length}</span>
            <span className="stat-label">Repositories</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{totals.ready}</span>
            <span className="stat-label">Ready to analyse</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{totals.commits}</span>
            <span className="stat-label">Commits ingested</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{totals.developers}</span>
            <span className="stat-label">Developers</span>
          </div>
        </section>

        <section className="import-grid">
          <form className="panel import-panel" onSubmit={handleUpload}>
            <h2>Upload a ZIP archive</h2>
            <p className="muted">
              A ZIP of the working copy, including the <code>.git</code> directory.
            </p>
            <label className="field">
              <span>Archive</span>
              <input
                ref={fileInputRef}
                type="file"
                accept=".zip,application/zip"
                onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)}
                required
              />
            </label>
            <label className="field">
              <span>
                Display name <em className="optional">(optional)</em>
              </span>
              <input
                type="text"
                value={uploadName}
                onChange={(event) => setUploadName(event.target.value)}
                placeholder="Defaults to the file name"
              />
            </label>
            <button type="submit" className="btn btn-primary" disabled={busy !== null || !uploadFile}>
              {busy === 'upload' ? 'Uploading…' : 'Upload and ingest'}
            </button>
          </form>

          <form className="panel import-panel" onSubmit={handleClone}>
            <h2>Clone from a URL</h2>
            <p className="muted">
              The repository is deeply cloned (full history, no shallow truncation).
            </p>
            <label className="field">
              <span>Repository URL</span>
              <input
                type="text"
                value={cloneUrl}
                onChange={(event) => setCloneUrl(event.target.value)}
                placeholder="https://github.com/owner/repo.git"
                required
              />
            </label>
            <label className="field">
              <span>
                Display name <em className="optional">(optional)</em>
              </span>
              <input
                type="text"
                value={cloneName}
                onChange={(event) => setCloneName(event.target.value)}
                placeholder="Defaults to the repository name"
              />
            </label>
            <button type="submit" className="btn btn-primary" disabled={busy !== null || !cloneUrl.trim()}>
              {busy === 'clone' ? 'Cloning…' : 'Clone and ingest'}
            </button>
          </form>
        </section>

        {importError && <div className="alert alert-error">{importError}</div>}
        {error && <div className="alert alert-error">{error}</div>}

        <section className="panel">
          <div className="section-head">
            <h2>Your repositories</h2>
            <button type="button" className="btn btn-ghost" onClick={() => void load()}>
              Refresh
            </button>
          </div>

          {loading ? (
            <p className="muted">Loading repositories…</p>
          ) : repositories.length === 0 ? (
            <p className="muted">No repositories yet — import one above to get started.</p>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Source</th>
                    <th>Status</th>
                    <th>Branch</th>
                    <th className="num">Commits</th>
                    <th className="num">Authors</th>
                    <th>Ingested</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {repositories.map((repository) => (
                    <tr key={repository.id}>
                      <td>
                        <button
                          type="button"
                          className="link-button"
                          onClick={() => openRepository(repository)}
                          disabled={repository.status !== 'ready'}
                        >
                          {repository.name}
                        </button>
                        {repository.error_message && (
                          <div className="row-error">{repository.error_message}</div>
                        )}
                      </td>
                      <td>
                        <span className="badge badge-source">
                          {repository.source_type === 'remote' ? 'URL' : 'ZIP'}
                        </span>
                      </td>
                      <td>
                        <span className={`badge badge-${repository.status}`}>
                          {STATUS_LABELS[repository.status]}
                        </span>
                      </td>
                      <td className="mono">{repository.default_branch ?? '—'}</td>
                      <td className="num">{repository.commit_count ?? '—'}</td>
                      <td className="num">{repository.author_count ?? '—'}</td>
                      <td>{formatDate(repository.last_ingested_at)}</td>
                      <td className="actions">
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          disabled={repository.status !== 'ready'}
                          onClick={() => openRepository(repository)}
                        >
                          Open
                        </button>
                        <button
                          type="button"
                          className="btn btn-danger btn-sm"
                          onClick={() => void handleDelete(repository)}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>
    </div>
  )
}
