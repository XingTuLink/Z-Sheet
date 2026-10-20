// Append Excel/CSV rows to an entity of the generated system.
// Preview is cached server-side; confirm only sends the batch id + strategy.

export class AppendApiError extends Error {
  readonly status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'AppendApiError'
    this.status = status
  }
}

export interface AppendFieldRef {
  column: string
  key: string
  name: string
  type: string
  is_key: boolean
  is_new: boolean
}

export interface AppendStrategyOption {
  value: 'skip' | 'overwrite' | 'keep'
  label: string
  description: string
}

export interface AppendPreview {
  batch_id: string
  app_key: string
  entity_key: string
  entity_name: string
  base_version: number
  file_name: string
  sheet_name: string
  available_sheets: string[]
  total_rows: number
  valid_rows: number
  new_rows: number
  duplicate_rows: number
  invalid_rows: number
  matched_fields: AppendFieldRef[]
  new_fields: AppendFieldRef[]
  duplicate_sample: string[]
  invalid_messages: string[]
  warnings: string[]
  strategies: AppendStrategyOption[]
}

export interface AppendResult {
  batch_id: string
  app_key: string
  entity_key: string
  status: string
  version: number
  inserted: number
  overwritten: number
  skipped: number
  duplicate_keys: string[]
}

export interface AppendBatchMeta {
  id: string
  app_key: string
  entity_key: string
  entity_name: string
  file_name: string
  sheet_name: string
  base_version: number
  new_version: number | null
  strategy: string | null
  status: string
  inserted: number
  overwritten: number
  created_at: string
}

async function readError(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string' && body.detail) return body.detail
  } catch {
    // Non-JSON error body: fall through to the generic message.
  }
  return `${fallback}（HTTP ${response.status}）`
}

/** Upload a workbook and preview how it will merge into one entity. */
export async function previewAppend(
  appKey: string,
  entityKey: string,
  file: File,
): Promise<AppendPreview> {
  const form = new FormData()
  form.append('file', file)
  form.append('app_key', appKey)
  form.append('entity_key', entityKey)

  let response: Response
  try {
    response = await fetch('/api/v1/ingestion/appends/preview', {
      method: 'POST',
      body: form,
    })
  } catch {
    throw new AppendApiError(0, '无法连接服务，请确认后端已启动后重试。')
  }
  if (!response.ok) {
    throw new AppendApiError(
      response.status,
      await readError(response, '预览失败，请稍后重试。'),
    )
  }
  return (await response.json()) as AppendPreview
}

export async function confirmAppend(
  batchId: string,
  strategy: string,
): Promise<AppendResult> {
  const response = await fetch('/api/v1/ingestion/appends/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ batch_id: batchId, strategy }),
  })
  if (!response.ok) {
    throw new AppendApiError(
      response.status,
      await readError(response, '追加失败，请稍后重试。'),
    )
  }
  return (await response.json()) as AppendResult
}

export async function rollbackAppend(batchId: string): Promise<AppendResult> {
  const response = await fetch(
    `/api/v1/ingestion/appends/${encodeURIComponent(batchId)}/rollback`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    },
  )
  if (!response.ok) {
    throw new AppendApiError(
      response.status,
      await readError(response, '回滚失败，请稍后重试。'),
    )
  }
  return (await response.json()) as AppendResult
}

export async function fetchAppendBatches(
  appKey: string,
): Promise<AppendBatchMeta[]> {
  const response = await fetch(
    `/api/v1/ingestion/appends?app_key=${encodeURIComponent(appKey)}`,
  )
  if (!response.ok) {
    throw new AppendApiError(
      response.status,
      await readError(response, '追加记录加载失败。'),
    )
  }
  return (await response.json()) as AppendBatchMeta[]
}
