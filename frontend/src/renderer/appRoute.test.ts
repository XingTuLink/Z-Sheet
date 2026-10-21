import { describe, expect, it } from 'vitest'

import { resolveAppRoute } from './appRoute'

describe('resolveAppRoute', () => {
  it('keeps the legacy workspace paths including views and admin', () => {
    for (const path of ['/app', '/app/views/customer', '/app/admin', '/app/admin/model']) {
      const route = resolveAppRoute(path)
      expect(route.appKey).toBe('workspace')
      expect(route.basePath).toBe('/app')
    }
  })

  it('routes the bundled demo before generic app keys', () => {
    for (const path of ['/app/demo', '/app/demo/views/sales']) {
      const route = resolveAppRoute(path)
      expect(route.appKey).toBe('default')
      expect(route.basePath).toBe('/app/demo')
      expect(route.isDemo).toBe(true)
    }
  })

  it('derives the app key and base path of generated systems', () => {
    const route = resolveAppRoute('/app/sales-2026/views/orders')
    expect(route.appKey).toBe('sales-2026')
    expect(route.basePath).toBe('/app/sales-2026')
    expect(route.isDemo).toBe(false)
  })

  it('decodes percent-encoded app keys', () => {
    const route = resolveAppRoute('/app/app-abc123')
    expect(route.appKey).toBe('app-abc123')
    expect(route.basePath).toBe('/app/app-abc123')
  })

  it('treats /p/:slug as a public route', () => {
    const route = resolveAppRoute('/p/kehu-xiaoshou')
    expect(route.isPublic).toBe(true)
    expect(route.publicSlug).toBe('kehu-xiaoshou')
  })
})
