// Local account endpoints. The session lives in an HttpOnly cookie, so the
// browser never handles the token itself; every call just includes the cookie.

export interface AuthUser {
  id: number
  email: string
  display_name: string
  created_at: string
}

export interface AuthState {
  user: AuthUser | null
  /** True once the instance has an account: the workbench then requires login. */
  auth_required: boolean
}

export class AuthApiError extends Error {
  readonly status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'AuthApiError'
    this.status = status
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (response.status === 204) {
    return undefined as T
  }
  if (!response.ok) {
    let detail = `请求失败（HTTP ${response.status}）`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // Non-JSON error body: keep the generic message.
    }
    throw new AuthApiError(response.status, detail)
  }
  return (await response.json()) as T
}

const JSON_HEADERS = { 'Content-Type': 'application/json' }

export function fetchMe(): Promise<AuthState> {
  return request<AuthState>('/api/v1/auth/me')
}

export function login(email: string, password: string): Promise<AuthState> {
  return request<AuthState>('/api/v1/auth/login', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ email, password }),
  })
}

export function register(
  email: string,
  password: string,
  displayName: string,
): Promise<AuthState> {
  return request<AuthState>('/api/v1/auth/register', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      email,
      password,
      display_name: displayName,
    }),
  })
}

export function logout(): Promise<void> {
  return request<void>('/api/v1/auth/logout', { method: 'POST' })
}
