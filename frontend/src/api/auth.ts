/** Authentication API calls. */
import type { RegisterPayload, TokenPair, User } from '../types'
import { apiFetch, getTokens, setTokens } from './client'

export async function login(email: string, password: string): Promise<TokenPair> {
  const tokens = await apiFetch<TokenPair>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
  setTokens(tokens)
  return tokens
}

export function register(payload: RegisterPayload): Promise<User> {
  return apiFetch<User>('/auth/register', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function logout(): Promise<void> {
  const tokens = getTokens()
  if (tokens) {
    try {
      await apiFetch<void>('/auth/logout', {
        method: 'POST',
        body: JSON.stringify({ refresh_token: tokens.refresh_token }),
      })
    } catch {
      // Local logout must succeed even if the server call fails.
    }
  }
  setTokens(null)
}

export function fetchMe(): Promise<User> {
  return apiFetch<User>('/auth/me')
}
