// Runtime apps share one renderer shell.
//   /app                  -> the legacy workspace app (app_key "workspace")
//   /app/demo/*           -> the bundled demo app (app_key "default")
//   /app/:appKey/*        -> any generated system, including the workspace
//   /p/:slug/*            -> a pinned publication; bootstrap from /api/v1/p/...
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
  // Fixed second segments of the legacy workspace, never app keys.
  if (
    pathname === '/app/admin' ||
    pathname.startsWith('/app/admin/') ||
    pathname === '/app/views' ||
    pathname.startsWith('/app/views/')
  ) {
    return {
      appKey: WORKSPACE_APP_KEY,
      basePath: '/app',
      isDemo: false,
      isPublic: false,
      publicSlug: null,
    }
  }
  const appMatch = /^\/app\/([^/]+)(?:\/|$)/.exec(pathname)
  if (appMatch) {
    const appKey = decodeURIComponent(appMatch[1])
    return {
      appKey,
      basePath: `/app/${encodeURIComponent(appKey)}`,
      isDemo: false,
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
