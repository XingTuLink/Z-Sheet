import { BrowserRouter, Navigate, Route, Routes, useParams } from 'react-router-dom'

import { AppLayout } from './renderer/AppLayout'
import { ViewResolver } from './renderer/ViewResolver'
import { findViewByKey } from './renderer/resolve'
import { useRuntime } from './renderer/useRuntime'

function IndexRoute() {
  const { bootstrap } = useRuntime()
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
  return <Navigate to={`/views/${first.view}`} replace />
}

function ViewRoute() {
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

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<AppLayout />}>
          <Route index element={<IndexRoute />} />
          <Route path="views/:viewKey" element={<ViewRoute />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
