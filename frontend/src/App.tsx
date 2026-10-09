import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useParams,
} from 'react-router-dom'

import { UnderstandingPage } from './pages/UnderstandingPage'
import { UploadPage } from './pages/UploadPage'
import { AppLayout } from './renderer/AppLayout'
import { resolveAppRoute } from './renderer/appRoute'
import { ViewResolver } from './renderer/ViewResolver'
import { findViewByKey } from './renderer/resolve'
import { useRuntime } from './renderer/useRuntime'

function IndexRoute() {
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
        {/* Day 12: the first screen is upload + understanding progress. */}
        <Route path="/" element={<UploadPage />} />
        {/* Day 13: structured browse of the understanding result. */}
        <Route path="/understanding" element={<UnderstandingPage />} />
        {/* Day 15: /app is the user's confirmed workspace; /app/demo keeps
            the bundled demo runtime independently browsable. */}
        <Route path="/app" element={<AppLayout />}>
          <Route index element={<IndexRoute />} />
          <Route path="views/:viewKey" element={<ViewRoute />} />
        </Route>
        <Route path="/app/demo" element={<AppLayout />}>
          <Route index element={<IndexRoute />} />
          <Route path="views/:viewKey" element={<ViewRoute />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
