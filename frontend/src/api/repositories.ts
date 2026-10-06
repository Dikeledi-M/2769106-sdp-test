/** Repository management API calls. */
import type { Author, CommitSummary, FileListResponse, Repository } from '../types'
import { apiFetch } from './client'

export function listRepositories(): Promise<Repository[]> {
  return apiFetch<Repository[]>('/repositories')
}

export function getRepository(repositoryId: number): Promise<Repository> {
  return apiFetch<Repository>(`/repositories/${repositoryId}`)
}

export function uploadRepository(file: File, name?: string): Promise<Repository> {
  const form = new FormData()
  form.append('file', file)
  if (name && name.trim()) form.append('name', name.trim())
  return apiFetch<Repository>('/repositories/upload', { method: 'POST', body: form })
}

export function cloneRepository(url: string, name?: string): Promise<Repository> {
  return apiFetch<Repository>('/repositories/clone', {
    method: 'POST',
    body: JSON.stringify({ url: url.trim(), name: name?.trim() || undefined }),
  })
}

export function deleteRepository(repositoryId: number): Promise<void> {
  return apiFetch<void>(`/repositories/${repositoryId}`, { method: 'DELETE' })
}

export function listAuthors(repositoryId: number): Promise<Author[]> {
  return apiFetch<Author[]>(`/repositories/${repositoryId}/authors`)
}

export function listCommits(repositoryId: number, limit = 200): Promise<CommitSummary[]> {
  return apiFetch<CommitSummary[]>(`/repositories/${repositoryId}/commits?limit=${limit}`)
}

export function listFiles(repositoryId: number): Promise<FileListResponse> {
  return apiFetch<FileListResponse>(`/repositories/${repositoryId}/files`)
}
