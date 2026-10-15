// Day 33: publish a confirmed app under a custom public slug.
// Management calls live next to the owner UI; the public bootstrap endpoint
// is the ONLY thing the read-only /p/:slug page may call.

export type PublicationStatus = 'published' | 'unpublished'

export interface Publication {
  app_key: string
  slug: string
  status: PublicationStatus
  model_version: number
  current_version: number
  public: boolean
  published_at: string | null
}

export class PublicationApiError extends Error {
  readonly status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'PublicationApiError'
    this.status = status
  }
}

export const SLUG_MIN_LENGTH = 2
export const SLUG_MAX_LENGTH = 40
const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$/

// Mirror of backend publication_repository.RESERVED_SLUGS; the server stays
// the authority, this list only gives instant feedback.
export const RESERVED_SLUGS = new Set([
  'api',
  'app',
  'apps',
  'p',
  'pub',
  'public',
  'admin',
  'login',
  'logout',
  'signup',
  'register',
  'health',
  'healthz',
  'ready',
  'assets',
  'static',
  'docs',
  'doc',
  'demo',
  'workspace',
  'default',
  'favicon',
  'robots',
  'sitemap',
  'ingestion',
  'models',
  'runtime',
  'v1',
])

export function normalizeSlug(raw: string): string {
  return raw.trim().toLowerCase()
}

/** Return a Chinese error message when invalid, or null when acceptable. */
export function validateSlug(raw: string): string | null {
  const slug = normalizeSlug(raw)
  if (!slug) return '链接标识不能为空'
  if (slug.length < SLUG_MIN_LENGTH || slug.length > SLUG_MAX_LENGTH) {
    return `链接标识长度需为 ${SLUG_MIN_LENGTH}-${SLUG_MAX_LENGTH} 个字符`
  }
  if (!SLUG_RE.test(slug)) {
    return '只能包含小写字母、数字和连字符（-），且不能以连字符开头或结尾'
  }
  if (RESERVED_SLUGS.has(slug)) {
    return `「${slug}」是系统保留字，请换一个`
  }
  return null
}

export function buildPublicUrl(slug: string): string {
  return `${window.location.origin}/p/${slug}`
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
    throw new PublicationApiError(response.status, detail)
  }
  return (await response.json()) as T
}

/** 404 means the app was never published; callers treat that as "no row". */
export async function fetchPublication(appKey: string): Promise<Publication | null> {
  try {
    return await requestJson<Publication>(
      `/api/v1/models/${encodeURIComponent(appKey)}/publication`,
    )
  } catch (error) {
    if (error instanceof PublicationApiError && error.status === 404) return null
    throw error
  }
}

export function publishApp(appKey: string, slug: string): Promise<Publication> {
  return requestJson<Publication>(
    `/api/v1/models/${encodeURIComponent(appKey)}/publication`,
    { method: 'POST', body: JSON.stringify({ slug }) },
  )
}

export function updatePublication(
  appKey: string,
  changes: { slug?: string; republish?: boolean },
): Promise<Publication> {
  return requestJson<Publication>(
    `/api/v1/models/${encodeURIComponent(appKey)}/publication`,
    { method: 'PUT', body: JSON.stringify(changes) },
  )
}

export function unpublishApp(appKey: string): Promise<Publication> {
  return requestJson<Publication>(
    `/api/v1/models/${encodeURIComponent(appKey)}/publication`,
    { method: 'DELETE' },
  )
}

export async function fetchPublicBootstrap(slug: string) {
  const response = await fetch(`/api/v1/p/${encodeURIComponent(slug)}/bootstrap`)
  if (!response.ok) {
    throw new Error(`public bootstrap failed with status ${response.status}`)
  }
  return (await response.json()) as import('../renderer/types').Bootstrap
}
