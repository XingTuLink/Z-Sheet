import type { Bootstrap, RecordRow } from '../renderer/types'

export class RecordApiError extends Error {
  readonly status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'RecordApiError'
    this.status = status
  }
}

/** The bundled demo app; lazily seeded by the backend on first bootstrap. */
export const DEMO_APP_KEY = 'default'

/**
 * The user-generated app created by the Day 15 Confirm flow. V0.1 keeps a
 * single workspace app; re-confirming another workbook creates a new version.
 */
export const WORKSPACE_APP_KEY = 'workspace'

/**
 * Loads everything the renderer needs in one round trip.
 * The demo app is seeded lazily by the backend on the first request.
 */
export async function fetchBootstrap(appKey: string): Promise<Bootstrap> {
  const response = await fetch(`/api/v1/runtime/${appKey}/bootstrap`)
  if (!response.ok) {
    throw new Error(`bootstrap failed with status ${response.status}`)
  }
  return (await response.json()) as Bootstrap
}

async function sendRecordRequest(
  url: string,
  init: RequestInit,
): Promise<RecordRow> {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    let detail = `保存失败（HTTP ${response.status}）`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // Keep the generic message for non-JSON error responses.
    }
    throw new RecordApiError(response.status, detail)
  }
  if (response.status === 204) return {}
  const body = (await response.json()) as { row: RecordRow }
  return body.row
}

export function createRecord(
  appKey: string,
  entityKey: string,
  row: RecordRow,
): Promise<RecordRow> {
  return sendRecordRequest(
    `/api/v1/runtime/${appKey}/entities/${entityKey}/records`,
    { method: 'POST', body: JSON.stringify({ row }) },
  )
}

export function updateRecord(
  appKey: string,
  entityKey: string,
  key: string,
  row: RecordRow,
): Promise<RecordRow> {
  return sendRecordRequest(
    `/api/v1/runtime/${appKey}/entities/${entityKey}/records/${encodeURIComponent(key)}`,
    { method: 'PUT', body: JSON.stringify({ row }) },
  )
}

export function deleteRecord(
  appKey: string,
  entityKey: string,
  key: string,
): Promise<RecordRow> {
  return sendRecordRequest(
    `/api/v1/runtime/${appKey}/entities/${entityKey}/records/${encodeURIComponent(key)}`,
    { method: 'DELETE' },
  )
}
