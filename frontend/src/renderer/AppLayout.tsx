import { useQuery } from '@tanstack/react-query'
import { NavLink, Outlet } from 'react-router-dom'

import { DEMO_APP_KEY, fetchBootstrap } from '../api/runtime'
import { RuntimeProvider } from './RuntimeProvider'

export function AppLayout() {
  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: ['bootstrap', DEMO_APP_KEY],
    queryFn: () => fetchBootstrap(DEMO_APP_KEY),
    retry: false,
  })

  if (isLoading) {
    return (
      <main className="gate">
        <div className="card gate-card">
          <h1 className="gate-brand">Z-Sheet</h1>
          <p className="muted">正在加载演示模型…</p>
        </div>
      </main>
    )
  }

  if (isError || !data) {
    return (
      <main className="gate">
        <div className="card gate-card">
          <h1 className="gate-brand">Z-Sheet</h1>
          <p>无法加载演示模型，请确认后端服务已启动。</p>
          <p className="muted">开发模式下后端默认运行在 http://localhost:8000</p>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void refetch()}
            disabled={isFetching}
          >
            {isFetching ? '加载中…' : '重新加载'}
          </button>
        </div>
      </main>
    )
  }

  return (
    <RuntimeProvider bootstrap={data}>
      <div className="app-shell">
        <header className="topbar">
          <div className="brand">
            <span className="brand-name">Z-Sheet</span>
            <span className="brand-app">{data.model.app.name}</span>
          </div>
          <nav className="nav" aria-label="主导航">
            {data.model.navigation.map((item) => (
              <NavLink
                key={item.view}
                to={`/views/${item.view}`}
                className={({ isActive }) =>
                  isActive ? 'nav-link active' : 'nav-link'
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </RuntimeProvider>
  )
}
