// Members and their app-level roles. Only the app owner may call the write
// endpoints; everybody else receives a 403 the page turns into a hint.

export type MemberRole = 'owner' | 'editor' | 'viewer'

export const ROLE_LABELS: Record<MemberRole, string> = {
  owner: '所有者',
  editor: '编辑者',
  viewer: '查看者',
}

export interface Member {
  user_id: number
  email: string
  display_name: string
  role: MemberRole
  added_at: string
}

export interface MemberList {
  app_key: string
  members: Member[]
  /** No rows yet: created before accounts existed, open to every account. */
  unclaimed: boolean
}

export class MembersApiError extends Error {
  readonly status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'MembersApiError'
    this.status = status
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    let detail = `操作失败（HTTP ${response.status}）`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // Keep the generic message for non-JSON responses.
    }
    throw new MembersApiError(response.status, detail)
  }
  return (await response.json()) as T
}

function base(appKey: string): string {
  return `/api/v1/apps/${encodeURIComponent(appKey)}/members`
}

const JSON_HEADERS = { 'Content-Type': 'application/json' }

export function fetchMembers(appKey: string): Promise<MemberList> {
  return request<MemberList>(base(appKey))
}

export function addMember(
  appKey: string,
  email: string,
  role: MemberRole,
): Promise<MemberList> {
  return request<MemberList>(base(appKey), {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ email, role }),
  })
}

export function updateMemberRole(
  appKey: string,
  userId: number,
  role: MemberRole,
): Promise<MemberList> {
  return request<MemberList>(`${base(appKey)}/${userId}`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify({ role }),
  })
}

export function removeMember(
  appKey: string,
  userId: number,
): Promise<MemberList> {
  return request<MemberList>(`${base(appKey)}/${userId}`, { method: 'DELETE' })
}
