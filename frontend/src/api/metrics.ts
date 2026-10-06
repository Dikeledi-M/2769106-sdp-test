/**
 * Metrics API calls.
 *
 * Every metrics endpoint accepts the same commit-set filters (author, path,
 * time range, explicit commit list); ``buildQuery`` maps the shared
 * ``MetricsQuery`` to the query string used by all four calls.
 */
import type {
  AuthorMetricsResponse,
  DirectoryMetricsResponse,
  FileMetricsResponse,
  MetricsQuery,
  RepositoryMetricsResponse,
} from '../types'
import { apiFetch } from './client'

export function buildQuery(query: MetricsQuery): string {
  const params = new URLSearchParams()
  if (query.authorId) params.set('author', query.authorId)
  if (query.path?.trim()) params.set('path', query.path.trim())
  if (query.from) params.set('from', query.from)
  if (query.to) params.set('to', query.to)
  for (const sha of query.commits ?? []) params.append('commit', sha)
  const serialized = params.toString()
  return serialized ? `?${serialized}` : ''
}

export function fetchFileMetrics(
  repositoryId: number,
  query: MetricsQuery,
): Promise<FileMetricsResponse> {
  return apiFetch<FileMetricsResponse>(
    `/repositories/${repositoryId}/metrics/files${buildQuery(query)}`,
  )
}

export function fetchDirectoryMetrics(
  repositoryId: number,
  query: MetricsQuery,
): Promise<DirectoryMetricsResponse> {
  return apiFetch<DirectoryMetricsResponse>(
    `/repositories/${repositoryId}/metrics/directories${buildQuery(query)}`,
  )
}

export function fetchRepositoryMetrics(
  repositoryId: number,
  query: MetricsQuery,
): Promise<RepositoryMetricsResponse> {
  // The repository endpoint always reports the root; ``path`` does not apply.
  const { path: _ignored, ...rootQuery } = query
  return apiFetch<RepositoryMetricsResponse>(
    `/repositories/${repositoryId}/metrics/repository${buildQuery(rootQuery)}`,
  )
}

export function fetchAuthorMetrics(
  repositoryId: number,
  query: MetricsQuery,
): Promise<AuthorMetricsResponse> {
  return apiFetch<AuthorMetricsResponse>(
    `/repositories/${repositoryId}/metrics/authors${buildQuery(query)}`,
  )
}
