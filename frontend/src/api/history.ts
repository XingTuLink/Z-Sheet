// Day 26: model version history, undo and rollback API client.
// Rollback is append-only: restoring a version appends a new version row.

export class HistoryApiError extends Error {
  readonly status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'HistoryApiError'
    this.status = status
  }
}

export interface VersionMeta {
  id: number
  app_key: string
  version: number
  operator: string
  source_request: string | null
  validation_status: string
  has_patch: boolean
  created_at: string
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // Non-JSON error body: fall through to the generic message.
  }
  return `请求失败（HTTP ${response.status}）`
}

export async function fetchVersions(appKey: string): Promise<VersionMeta[]> {
  const response = await fetch(`/api/v1/models/${appKey}/versions`)
  if (!response.ok) {
    throw new HistoryApiError(response.status, await readError(response))
  }
  return (await response.json()) as VersionMeta[]
}

export async function undoLatest(appKey: string): Promise<VersionMeta> {
  const response = await fetch(`/api/v1/models/${appKey}/undo`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
  if (!response.ok) {
    throw new HistoryApiError(response.status, await readError(response))
  }
  return (await response.json()) as VersionMeta
}

export async function rollbackVersion(
  appKey: string,
  version: number,
): Promise<VersionMeta> {
  const response = await fetch(
    `/api/v1/models/${appKey}/versions/${version}/rollback`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    },
  )
  if (!response.ok) {
    throw new HistoryApiError(response.status, await readError(response))
  }
  return (await response.json()) as VersionMeta
}
