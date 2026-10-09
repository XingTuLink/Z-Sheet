// Day 15: two runtime apps share one renderer shell.
//   /app        -> the user's confirmed workspace app (app_key "workspace")
//   /app/demo/* -> the bundled demo app (app_key "default")
// Deriving both from the current pathname keeps nav links and the bootstrap
// query consistent without a second routing context.

import { DEMO_APP_KEY, WORKSPACE_APP_KEY } from '../api/runtime'

export interface AppRoute {
  appKey: string
  basePath: string
  isDemo: boolean
}

export function resolveAppRoute(pathname: string): AppRoute {
  if (pathname === '/app/demo' || pathname.startsWith('/app/demo/')) {
    return { appKey: DEMO_APP_KEY, basePath: '/app/demo', isDemo: true }
  }
  return { appKey: WORKSPACE_APP_KEY, basePath: '/app', isDemo: false }
}
