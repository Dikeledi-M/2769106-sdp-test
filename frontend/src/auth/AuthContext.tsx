/**
 * Global authentication state.
 *
 * On mount it tries to restore the session from stored tokens (via
 * GET /auth/me, which transparently refreshes expired access tokens).
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import * as authApi from '../api/auth'
import { getTokens } from '../api/client'
import type { RegisterPayload, User } from '../types'

interface AuthContextValue {
  user: User | null
  /** True while the initial session restore is in flight. */
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (payload: RegisterPayload) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    async function restoreSession() {
      if (!getTokens()) {
        setLoading(false)
        return
      }
      try {
        const me = await authApi.fetchMe()
        if (!cancelled) setUser(me)
      } catch {
        // Stored tokens are no longer valid; the client already cleared them.
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void restoreSession()
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    await authApi.login(email, password)
    setUser(await authApi.fetchMe())
  }, [])

  const register = useCallback(async (payload: RegisterPayload) => {
    await authApi.register(payload)
    // Sign the user in right after registration.
    await authApi.login(payload.email, payload.password)
    setUser(await authApi.fetchMe())
  }, [])

  const logout = useCallback(async () => {
    await authApi.logout()
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({ user, loading, login, register, logout }),
    [user, loading, login, register, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
