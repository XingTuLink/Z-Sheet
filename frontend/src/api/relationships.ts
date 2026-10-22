// Visual Relationship Editor client. The editor only submits a structured
// request; ops stay on the server and confirm uses the same patch-session
// endpoint as natural-language changes.

import { PatchApiError, confirmPatch, rejectPatch, type PatchPreview } from './patch'

export type RelationshipAction = 'add' | 'update' | 'delete'
export type RelationshipType = 'one_to_many' | 'one_to_one'

export interface RelationshipInput {
  action: RelationshipAction
  link_key?: string
  from?: string
  to?: string
  type?: RelationshipType
  on?: { from: string; to: string }
}

export async function previewRelationship(
  appKey: string,
  input: RelationshipInput,
): Promise<PatchPreview> {
  const response = await fetch(
    `/api/v1/apps/${encodeURIComponent(appKey)}/relationships/preview`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    },
  )
  if (!response.ok) {
    let detail = `关联调整失败（HTTP ${response.status}）`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // Keep the generic message for non-JSON error bodies.
    }
    throw new PatchApiError(response.status, detail)
  }
  return (await response.json()) as PatchPreview
}

export const confirmRelationship = confirmPatch
export const rejectRelationship = rejectPatch
