// Day 25: NL Patch preview/confirm/reject API client.
// The session token is opaque: the client only sends the sentence and later
// the token plus its decision, never patch ops.

export class PatchApiError extends Error {
  readonly status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'PatchApiError'
    this.status = status
  }
}

export interface PatchChange {
  kind: string
  risk: 'low' | 'high'
  summary: string
}

export interface PatchUnsupported {
  category: string
  reason: string
  suggestion: string
}

export interface PatchPreview {
  session_id: string
  base_version: number
  actionable: boolean
  requires_confirmation: boolean
  changes: PatchChange[]
  unsupported: PatchUnsupported[]
  notes: string[]
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

export async function previewPatch(appKey: string, text: string): Promise<PatchPreview> {
  const response = await fetch(`/api/v1/models/${appKey}/patch-sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  if (!response.ok) {
    throw new PatchApiError(response.status, await readError(response))
  }
  return (await response.json()) as PatchPreview
}

export async function confirmPatch(appKey: string, sessionId: string): Promise<void> {
  const response = await fetch(
    `/api/v1/models/${appKey}/patch-sessions/${encodeURIComponent(sessionId)}/confirm`,
    { method: 'POST' },
  )
  if (!response.ok) {
    throw new PatchApiError(response.status, await readError(response))
  }
}

export async function rejectPatch(appKey: string, sessionId: string): Promise<void> {
  const response = await fetch(
    `/api/v1/models/${appKey}/patch-sessions/${encodeURIComponent(sessionId)}/reject`,
    { method: 'POST' },
  )
  if (!response.ok) {
    throw new PatchApiError(response.status, await readError(response))
  }
}
