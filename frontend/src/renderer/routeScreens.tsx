// Index/detail route elements shared by the owner shell (/app) and the
// public read-only shell (/p/:slug). Both are wrapped in RuntimeProvider, so
// these screens only resolve the view from context and render it.

import { Navigate, useLocation, useParams } from 'react-router-dom'

import { resolveAppRoute } from './appRoute'
import { findViewByKey } from './resolve'
import { ViewResolver } from './ViewResolver'
import { useRuntime } from './useRuntime'

export function IndexRoute() {
  const { bootstrap } = useRuntime()
  const { basePath } = resolveAppRoute(useLocation().pathname)
  const first = bootstrap.model.navigation[0]
  if (!first) {
    return (
      <section className="view">
        <div className="card">
          <h1>未配置页面</h1>
          <p className="muted">当前模型没有声明任何导航项。</p>
        </div>
      </section>
    )
  }
  return <Navigate to={`${basePath}/views/${first.view}`} replace />
}

export function ViewRoute() {
  const { bootstrap } = useRuntime()
  const { viewKey } = useParams<{ viewKey: string }>()
  const view = viewKey ? findViewByKey(bootstrap.model, viewKey) : undefined

  if (!view) {
    return (
      <section className="view">
        <div className="card">
          <h1>页面不存在</h1>
          <p className="muted">模型中找不到视图「{viewKey ?? ''}」。</p>
        </div>
      </section>
    )
  }
  return <ViewResolver view={view} />
}
