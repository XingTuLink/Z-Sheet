// Day 12: upload a workbook and follow real understanding stages over SSE.
// The endpoint streams one JSON frame per Server-Sent Event:
//   stage  -> { key, count }  a pipeline stage just made progress
//   result -> full ProfiledParsedWorkbook payload
//   error  -> { detail }

export type StageKey = 'sheets' | 'fields' | 'entities' | 'relations' | 'assemble'

export interface StageEvent {
  key: StageKey
  count: number
}

export type FieldType = 'string' | 'number' | 'money' | 'date' | 'enum' | 'phone'
export type FieldRole =
  | 'identifier'
  | 'dimension'
  | 'measure'
  | 'time'
  | 'enum'
  | 'text'

export interface InferredLink {
  key: string
  from_entity: string
  to_entity: string
  on_from: string
  on_to: string
  confidence: number
  needs_review: boolean
  review_reason?: string | null
}

export interface UnderstandingEntity {
  key: string
  name: string
  key_field: string
  source?: { file: string; sheet?: string | null } | null
  fields: UnderstandingField[]
}

export interface UnderstandingField {
  key: string
  name: string
  type: FieldType
  role: FieldRole
  values?: string[] | null
  // Design 10.1: every inference carries a confidence tier and an explicit
  // review flag (medium/low confidence always needs review).
  confidence: number
  needs_review: boolean
  review_reason?: string | null
}

/** Sheet-level summary the Understanding page browses (Day 13). */
export interface UnderstandingSheet {
  name: string
  is_empty: boolean
  columns: string[]
  profile: { row_count: number }
  inferred_entity: {
    key: string
    name: string
    key_field: string | null
    confidence: number
    needs_review: boolean
  } | null
}

export interface UnderstandingResult {
  file_name: string
  file_type: string
  sheets: UnderstandingSheet[]
  assembly_notes: string[]
  // Day 21: which engine produced the entities/fields — the cloud LLM, or the
  // deterministic rules fallback (LLM unavailable / call failed).
  understanding_engine: 'llm' | 'rules'
  understanding_notes: string[]
  inferred_links: InferredLink[]
  business_model: {
    app: { name: string }
    entities: UnderstandingEntity[]
    metrics: unknown[]
  } | null
  business_model_yaml: string | null
}

const STORAGE_KEY = 'zs-understanding-result'

/**
 * Persist the understanding result for the Understanding page. Row data and
 * frequency samples are structural noise for browsing and can blow the
 * sessionStorage quota, so only sheet-level understanding output is kept.
 */
export function saveUnderstandingResult(result: UnderstandingResult): boolean {
  const slim: UnderstandingResult = {
    ...result,
    sheets: result.sheets.map((sheet) => ({
      name: sheet.name,
      is_empty: sheet.is_empty,
      columns: sheet.columns,
      profile: { row_count: sheet.profile.row_count },
      inferred_entity: sheet.inferred_entity,
    })),
  }
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(slim))
    // A new file invalidates every prior review decision.
    sessionStorage.removeItem(REVIEW_KEY)
    return true
  } catch {
    return false
  }
}

export function loadUnderstandingResult(): UnderstandingResult | null {
  const raw = sessionStorage.getItem(STORAGE_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as UnderstandingResult
  } catch {
    return null
  }
}

/**
 * Day 14 local review decisions. Persisted only in sessionStorage until the
 * Day 15 Confirm flow writes the corrected model server-side; nothing here
 * mutates the inferred confidence (design 10.1 forbids relabelling medium/low
 * confidence as certain — an acknowledgement is a human action, not a new
 * inference).
 */
export type ReviewDecision = 'accepted' | 'rejected'

export interface LinkReview {
  // Undefined while the user has only edited the on-field mapping but not yet
  // accepted or rejected the relation.
  decision?: ReviewDecision
  on_from: string
  on_to: string
}

export interface ReviewState {
  // Acknowledged entities/fields: `${entityKey}` or `${entityKey}.${fieldKey}`.
  acknowledged: Record<string, boolean>
  links: Record<string, LinkReview>
}

const REVIEW_KEY = 'zs-review-state'

export function emptyReviewState(): ReviewState {
  return { acknowledged: {}, links: {} }
}

export function loadReviewState(): ReviewState {
  const raw = sessionStorage.getItem(REVIEW_KEY)
  if (!raw) return emptyReviewState()
  try {
    const parsed = JSON.parse(raw) as Partial<ReviewState>
    return {
      acknowledged: parsed.acknowledged ?? {},
      links: parsed.links ?? {},
    }
  } catch {
    return emptyReviewState()
  }
}

export function saveReviewState(state: ReviewState): void {
  try {
    sessionStorage.setItem(REVIEW_KEY, JSON.stringify(state))
  } catch {
    // Session storage full/disabled: review actions simply do not survive a
    // reload; the in-memory state still drives the current page.
  }
}

/**
 * Standard workbook template offered when the pipeline cannot recognize any
 * business entity. A plain GET (anchor download) — the backend streams a
 * pre-filled .xlsx that itself passes the full pipeline.
 */
export const TEMPLATE_URL = '/api/v1/ingestion/template'

/** Day 15 Confirm result (mirrors backend ConfirmResponse). */
export interface ConfirmResponse {
  app_key: string
  version: number
  app_name: string
  links_accepted: string[]
  links_rejected: string[]
  record_counts: Record<string, number>
}

/**
 * Day 15/22: submit the reviewed workbook to the Confirm endpoint. The server
 * reuses the understanding result cached at parse time (keyed by file hash) —
 * never client-supplied structure — and applies the review decisions; the
 * file must be sent again because uploads themselves are never stored.
 *
 * Resolves to the generated app descriptor, or throws with the server's
 * Chinese detail message (plus `unresolved` items when the queue is not
 * clear).
 */
export async function confirmWorkbook(
  file: File,
  review: ReviewState,
): Promise<ConfirmResponse> {
  const decisions = {
    acknowledged: Object.entries(review.acknowledged)
      .filter(([, acknowledged]) => Boolean(acknowledged))
      .map(([id]) => id),
    links: Object.fromEntries(
      Object.entries(review.links)
        .filter(([, value]) => value.decision !== undefined)
        .map(([key, value]) => [
          key,
          { decision: value.decision, on_from: value.on_from, on_to: value.on_to },
        ]),
    ),
  }

  const form = new FormData()
  form.append('file', file)
  form.append('decisions', JSON.stringify(decisions))

  const response = await fetch('/api/v1/ingestion/confirm', {
    method: 'POST',
    body: form,
  })

  let payload: unknown = null
  try {
    payload = await response.json()
  } catch {
    payload = null
  }

  if (!response.ok) {
    const detail =
      payload &&
      typeof payload === 'object' &&
      'detail' in payload &&
      typeof (payload as { detail?: unknown }).detail === 'string'
        ? (payload as { detail: string }).detail
        : `生成失败（HTTP ${response.status}），请稍后重试。`
    const error = new Error(detail) as Error & { unresolved?: string[] }
    if (
      payload &&
      typeof payload === 'object' &&
      'unresolved' in payload &&
      Array.isArray((payload as { unresolved?: unknown }).unresolved)
    ) {
      error.unresolved = (payload as { unresolved: string[] }).unresolved
    }
    throw error
  }

  return payload as ConfirmResponse
}

/**
 * POST the file to the SSE parse endpoint and invoke `onStage` whenever a
 * real pipeline stage completes. Resolves with the full understanding
 * result when the final `result` frame arrives.
 */
export async function parseWorkbookWithProgress(
  file: File,
  onStage: (event: StageEvent) => void,
): Promise<UnderstandingResult> {
  const form = new FormData()
  form.append('file', file)

  const response = await fetch('/api/v1/ingestion/parse/stream', {
    method: 'POST',
    body: form,
  })
  if (!response.ok || !response.body) {
    throw new Error(`服务暂不可用（HTTP ${response.status}），请确认后端已启动。`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const handleBlock = (block: string): UnderstandingResult | undefined => {
    let event = ''
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event: ')) event = line.slice('event: '.length)
      else if (line.startsWith('data: ')) dataLines.push(line.slice('data: '.length))
    }
    if (!event) return undefined
    const data = dataLines.join('\n')

    if (event === 'stage') {
      onStage(JSON.parse(data) as StageEvent)
      return undefined
    }
    if (event === 'result') {
      return JSON.parse(data) as UnderstandingResult
    }
    if (event === 'error') {
      const payload = JSON.parse(data) as { detail?: unknown }
      throw new Error(
        typeof payload.detail === 'string' && payload.detail
          ? payload.detail
          : '文件解析失败，请检查文件格式后重试。',
      )
    }
    return undefined
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let separator: number
    while ((separator = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, separator)
      buffer = buffer.slice(separator + 2)
      const result = handleBlock(block)
      if (result) return result
    }
  }

  throw new Error('连接中断，未收到分析结果，请重试。')
}
