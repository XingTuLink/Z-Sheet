import type {
  Bootstrap,
  RecordRow,
  SortSpec,
} from '../renderer/types'

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

/** Base path of the read APIs for an owned app. */
export function appApiBase(appKey: string): string {
  return `/api/v1/runtime/${encodeURIComponent(appKey)}`
}

/** Base path of the read APIs for a published app. */
export function publicApiBase(slug: string): string {
  return `/api/v1/p/${encodeURIComponent(slug)}`
}

export interface FacetCount {
  value: string
  count: number
}

export interface RowPage {
  entity: string
  rows: RecordRow[]
  total: number
  entity_total: number
  page: number
  page_count: number
  page_size: number
  facets: Record<string, FacetCount[]>
}

export interface RowQueryParams {
  page: number
  pageSize: number
  search: string
  searchFields: string[]
  filters: Record<string, string[]>
  sorts: SortSpec[]
  facets: string[]
}

export interface RelatedParams {
  parentEntity: string
  link: string
  parent: string
  limit: number
}

export interface RelatedPage {
  rows: RecordRow[]
  total: number
}

export interface DashboardData {
  metrics: Record<string, number>
  recent: {
    entity: string
    list_view: string | null
    detail_view: string | null
    rows: RecordRow[]
  } | null
  trend: {
    entity: string
    date_field: string
    measure_field: string | null
    points: { label: string; value: number }[]
  } | null
  distribution: {
    entity: string
    field: string
    items: { label: string; count: number }[]
    total: number
  } | null
}

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) {
    let detail = fallback
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // Keep the fallback for non-JSON error responses.
    }
    throw new RecordApiError(response.status, detail)
  }
  return (await response.json()) as T
}

/**
 * Loads the render model (not the business rows) in one round trip.
 * The demo app is seeded lazily by the backend on the first request.
 */
export async function fetchBootstrap(appKey: string): Promise<Bootstrap> {
  const response = await fetch(`${appApiBase(appKey)}/bootstrap`)
  if (!response.ok) {
    throw new Error(`bootstrap failed with status ${response.status}`)
  }
  return (await response.json()) as Bootstrap
}

export async function fetchRows(
  apiBase: string,
  entityKey: string,
  params: RowQueryParams,
): Promise<RowPage> {
  const query = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.pageSize),
    search: params.search,
    search_fields: params.searchFields.join(','),
    facets: params.facets.join(','),
  })
  if (params.sorts.length > 0) query.set('sorts', JSON.stringify(params.sorts))
  const filters = Object.entries(params.filters).filter(
    ([, selected]) => selected.length > 0,
  )
  if (filters.length > 0) {
    query.set('filters', JSON.stringify(Object.fromEntries(filters)))
  }
  const response = await fetch(
    `${apiBase}/entities/${encodeURIComponent(entityKey)}/rows?${query}`,
  )
  return readJson<RowPage>(response, `加载记录失败（HTTP ${response.status}）`)
}

/** A missing row (404) is a normal "not found" state for detail/form pages. */
export async function fetchRow(
  apiBase: string,
  entityKey: string,
  rowKey: string,
): Promise<RecordRow | null> {
  const response = await fetch(
    `${apiBase}/entities/${encodeURIComponent(entityKey)}/rows/${encodeURIComponent(rowKey)}`,
  )
  if (response.status === 404) return null
  const body = await readJson<{ row: RecordRow }>(
    response,
    `加载记录失败（HTTP ${response.status}）`,
  )
  return body.row
}

export async function fetchRelated(
  apiBase: string,
  entityKey: string,
  params: RelatedParams,
): Promise<RelatedPage> {
  const query = new URLSearchParams({
    parent_entity: params.parentEntity,
    link: params.link,
    parent: params.parent,
    limit: String(params.limit),
  })
  const response = await fetch(
    `${apiBase}/entities/${encodeURIComponent(entityKey)}/related?${query}`,
  )
  return readJson<RelatedPage>(
    response,
    `加载关联记录失败（HTTP ${response.status}）`,
  )
}

export async function fetchDashboard(apiBase: string): Promise<DashboardData> {
  const response = await fetch(`${apiBase}/dashboard`)
  return readJson<DashboardData>(
    response,
    `加载仪表盘失败（HTTP ${response.status}）`,
  )
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
    `${appApiBase(appKey)}/entities/${entityKey}/records`,
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
    `${appApiBase(appKey)}/entities/${entityKey}/records/${encodeURIComponent(key)}`,
    { method: 'PUT', body: JSON.stringify({ row }) },
  )
}

export function deleteRecord(
  appKey: string,
  entityKey: string,
  key: string,
): Promise<RecordRow> {
  return sendRecordRequest(
    `${appApiBase(appKey)}/entities/${entityKey}/records/${encodeURIComponent(key)}`,
    { method: 'DELETE' },
  )
}
