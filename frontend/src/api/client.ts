/**
 * Minimal typed fetch client for the RAT API.
 *
 * - Attaches the Bearer access token to every request.
 * - On a 401 it transparently refreshes the token pair once and retries,
 *   using a single-flight promise so parallel requests share one refresh.
 */
import type { TokenPair } from '../types'

const API_BASE = '/api/v1'
const TOKEN_STORAGE_KEY = 'rat.tokens'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export function getTokens(): TokenPair | null {
  const raw = localStorage.getItem(TOKEN_STORAGE_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as TokenPair
  } catch {
    localStorage.removeItem(TOKEN_STORAGE_KEY)
    return null
  }
}

export function setTokens(tokens: TokenPair | null): void {
  if (tokens) {
    localStorage.setItem(TOKEN_STORAGE_KEY, JSON.stringify(tokens))
  } else {
    localStorage.removeItem(TOKEN_STORAGE_KEY)
  }
}

let refreshPromise: Promise<TokenPair | null> | null = null

function refreshTokens(): Promise<TokenPair | null> {
  if (!refreshPromise) {
    const current = getTokens()
    refreshPromise = (async () => {
      if (!current?.refresh_token) return null
      const response = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: current.refresh_token }),
      })
      if (!response.ok) {
        setTokens(null)
        return null
      }
      const tokens = (await response.json()) as TokenPair
      setTokens(tokens)
      return tokens
    })().finally(() => {
      refreshPromise = null
    })
  }
  return refreshPromise
}

async function extractErrorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json()
    const detail = body?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && detail.length > 0) {
      // FastAPI validation errors: [{loc, msg, type}, ...]
      return detail.map((item) => item?.msg ?? 'Invalid input').join('; ')
    }
  } catch {
    // Fall through to the generic message.
  }
  return `Request failed with status ${response.status}`
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
  allowRetry = true,
): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const tokens = getTokens()
  if (tokens) {
    headers.set('Authorization', `Bearer ${tokens.access_token}`)
  }

  const response = await fetch(`${API_BASE}${path}`, { ...options, headers })

  if (response.status === 401 && allowRetry) {
    const refreshed = await refreshTokens()
    if (refreshed) {
      return apiFetch<T>(path, options, false)
    }
  }

  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorMessage(response))
  }

  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}
