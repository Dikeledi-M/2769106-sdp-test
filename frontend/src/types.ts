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
