/** Shared API types mirroring the backend Pydantic schemas. */

export interface User {
  id: number
  email: string
  username: string
  full_name: string | null
  is_active: boolean
  is_superuser: boolean
  created_at: string
}

export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface RegisterPayload {
  email: string
  username: string
  password: string
  full_name?: string | null
}

/* ---------- Repositories ---------- */

export type RepositorySource = 'upload' | 'remote'
export type RepositoryStatus = 'pending' | 'ingesting' | 'ready' | 'error'

export interface Repository {
  id: number
  name: string
  source_type: RepositorySource
  source_url: string | null
  default_branch: string | null
  head_sha: string | null
  status: RepositoryStatus
  error_message: string | null
  commit_count: number | null
  author_count: number | null
  created_at: string
  updated_at: string
  last_ingested_at: string | null
}

export interface Author {
  id: number
  display_name: string
  primary_email: string | null
  created_at: string
}

export interface CommitSummary {
  id: number
  sha: string
  message: string | null
  author_id: number | null
  author_name: string | null
  committed_at: string | null
  is_merge: boolean
  insertions: number | null
  deletions: number | null
  files_changed: number | null
}

export interface FileListResponse {
  files: string[]
}

/* ---------- Metrics ---------- */

export interface ObjectMetrics {
  path: string
  added_lines: number
  removed_lines: number
  growth: number
  churn: number
  modifications: number
  modification_frequency: number
  churn_rate: number
}

export interface AuthorMetrics {
  author_id: number
  display_name: string
  modifications: number
  churn: number
  ownership: number
}

export interface FileMetricsResponse {
  commit_count: number
  files: ObjectMetrics[]
}

export interface DirectoryMetricsResponse {
  commit_count: number
  directories: ObjectMetrics[]
}

export interface RepositoryMetricsResponse {
  commit_count: number
  repository: ObjectMetrics
}

export interface AuthorMetricsResponse {
  commit_count: number
  path: string
  authors: AuthorMetrics[]
}

/** Commit-set filters shared by every metrics call (all optional). */
export interface MetricsQuery {
  authorId?: string
  path?: string
  from?: string
  to?: string
  commits?: string[]
}
