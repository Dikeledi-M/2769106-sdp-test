/**
 * Dashboard shell.
 *
 * Milestone 1 only authenticates the user; repository management
 * (ZIP upload / remote clone) and metric visualisations arrive next.
 */
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

const PLANNED_FEATURES = [
  'Import a repository from a ZIP archive (including .git) or a remote URL',
  'Per-developer, per-file, per-directory and repository-wide metrics',
  'Filter by repository, author, path, or a commit range / commit list',
  'Merge authors automatically via .mailmap or manually in the UI',
]

export function DashboardPage() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  async function handleLogout() {
    await logout()
    navigate('/login', { replace: true })
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
          <button type="button" className="btn btn-ghost" onClick={handleLogout}>
            Sign out
          </button>
        </div>
      </header>

      <main className="content">
        <section className="welcome">
          <h1>Welcome back, {user?.full_name ?? user?.username}</h1>
          <p>
            You are signed in. Repository ingestion and metric dashboards are the next
            milestones — the foundation is ready:
          </p>
        </section>

        <section className="stat-row">
          <div className="stat-card">
            <span className="stat-value">0</span>
            <span className="stat-label">Repositories</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">0</span>
            <span className="stat-label">Commits ingested</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">0</span>
            <span className="stat-label">Developers</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">0</span>
            <span className="stat-label">Files tracked</span>
          </div>
        </section>

        <section className="panel">
          <h2>Coming next</h2>
          <ul className="feature-list">
            {PLANNED_FEATURES.map((feature) => (
              <li key={feature}>{feature}</li>
            ))}
          </ul>
        </section>
      </main>
    </div>
  )
}
