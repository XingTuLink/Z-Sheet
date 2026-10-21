// Workbench and per-app admin data. Both endpoints are read-only projections
// the backend derives from existing tables (model versions, rows, publications).

export interface AppPublicationInfo {
  status: string
  slug: string
  public: boolean
  model_version: number
  published_at: string | null
}

export interface AppSummary {
  app_key: string
  name: string
  entity_count: number
  record_count: number
  current_version: number
  publication: AppPublicationInfo | null
  updated_at: string
}

export interface EntitySummary {
  entity_key: string
  name: string
  record_count: number
}

export interface ChangeEvent {
  kind: 'model' | 'append' | 'publication'
  summary: string
  version: number | null
  status: string | null
  at: string
}

export interface AppOverview {
  app_key: string
  name: string
  entity_count: number
  record_count: number
  current_version: number
  version_count: number
  publication: AppPublicationInfo | null
  entities: EntitySummary[]
  recent_changes: ChangeEvent[]
  updated_at: string
}

export class AppsApiError extends Error {
  readonly status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'AppsApiError'
    this.status = status
  }
}

async function requestJson<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) {
    let detail = `加载失败（HTTP ${response.status}）`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // Keep the generic message for non-JSON responses.
    }
    throw new AppsApiError(response.status, detail)
  }
  return (await response.json()) as T
}

export function fetchApps(): Promise<AppSummary[]> {
  return requestJson<AppSummary[]>('/api/v1/apps')
}

export function fetchAppOverview(appKey: string): Promise<AppOverview> {
  return requestJson<AppOverview>(
    `/api/v1/apps/${encodeURIComponent(appKey)}/overview`,
  )
}
