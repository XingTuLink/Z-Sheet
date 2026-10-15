// Day 15: runtime apps share one renderer shell.
//   /app          -> the user's confirmed workspace app (app_key "workspace")
//   /app/demo/*   -> the bundled demo app (app_key "default")
// Day 33 adds published read-only apps:
//   /p/:slug/*    -> a pinned publication; bootstrap comes from /api/v1/p/...
// Deriving basePath from the current pathname keeps nav links consistent.

import { DEMO_APP_KEY, WORKSPACE_APP_KEY } from '../api/runtime'

export interface AppRoute {
  /** Empty for public routes; callers fetch bootstrap by slug there. */
  appKey: string
  basePath: string
  isDemo: boolean
  isPublic: boolean
  publicSlug: string | null
}

export function resolveAppRoute(pathname: string): AppRoute {
  const publicMatch = /^\/p\/([^/]+)(?:\/|$)/.exec(pathname)
  if (publicMatch) {
    const slug = decodeURIComponent(publicMatch[1])
    return {
      appKey: '',
      basePath: `/p/${encodeURIComponent(slug)}`,
      isDemo: false,
      isPublic: true,
      publicSlug: slug,
    }
  }
  if (pathname === '/app/demo' || pathname.startsWith('/app/demo/')) {
    return {
      appKey: DEMO_APP_KEY,
      basePath: '/app/demo',
      isDemo: true,
      isPublic: false,
      publicSlug: null,
    }
  }
  return {
    appKey: WORKSPACE_APP_KEY,
    basePath: '/app',
    isDemo: false,
    isPublic: false,
    publicSlug: null,
  }
}
