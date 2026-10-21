// Global model connection settings. Keys are written once and only ever read
// back masked; the effective configuration falls back to .env when no profile
// is active.

export interface LlmProfile {
  id: number
  name: string
  base_url: string
  api_key: string
  model: string
  timeout: number
  enable_thinking: boolean
  max_retries: number
  is_active: boolean
  updated_at: string | null
}

export interface LlmStatus {
  source: 'database' | 'env'
  active_profile_id: number | null
  base_url: string
  model: string
  api_key: string
  is_local: boolean
  timeout: number
  enable_thinking: boolean
  max_retries: number
  available: boolean
}

export interface LlmProfileInput {
  name: string
  base_url: string
  api_key: string
  model: string
  timeout: number
  enable_thinking: boolean
  max_retries: number
  activate: boolean
}

export type LlmTestStatus =
  | 'ok'
  | 'unreachable'
  | 'unauthorized'
  | 'model_missing'
  | 'unsupported'
  | 'error'

export interface LlmTestResult {
  status: LlmTestStatus
  detail: string
  models: string[]
}

export class LlmApiError extends Error {
  readonly status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'LlmApiError'
    this.status = status
  }
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    let detail = `请求失败（HTTP ${response.status}）`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // Keep the generic message for non-JSON responses.
    }
    throw new LlmApiError(response.status, detail)
  }
  return (await response.json()) as T
}

export function fetchLlmStatus(): Promise<LlmStatus> {
  return requestJson<LlmStatus>('/api/v1/llm/status')
}

export function fetchLlmProfiles(): Promise<LlmProfile[]> {
  return requestJson<LlmProfile[]>('/api/v1/llm/profiles')
}

export function createLlmProfile(input: LlmProfileInput): Promise<LlmProfile> {
  return requestJson<LlmProfile>('/api/v1/llm/profiles', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export function updateLlmProfile(
  id: number,
  input: Partial<LlmProfileInput>,
): Promise<LlmProfile> {
  return requestJson<LlmProfile>(`/api/v1/llm/profiles/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  })
}

export function activateLlmProfile(id: number): Promise<LlmProfile> {
  return requestJson<LlmProfile>(`/api/v1/llm/profiles/${id}/activate`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export async function deleteLlmProfile(id: number): Promise<void> {
  await requestJson<{ detail: string }>(`/api/v1/llm/profiles/${id}`, {
    method: 'DELETE',
  })
}

export function testLlmConnection(input: {
  profile_id?: number
  base_url?: string
  api_key?: string
  model?: string
  timeout?: number
}): Promise<LlmTestResult> {
  return requestJson<LlmTestResult>('/api/v1/llm/test', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}
