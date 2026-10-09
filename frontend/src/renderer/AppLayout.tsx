import { useQuery } from '@tanstack/react-query'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'

import { fetchBootstrap } from '../api/runtime'
import { resolveAppRoute } from './appRoute'
import { RuntimeProvider } from './RuntimeProvider'

export function AppLayout() {
  const location = useLocation()
  const route = resolveAppRoute(location.pathname)
  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: ['bootstrap', route.appKey],
    queryFn: () => fetchBootstrap(route.appKey),
    retry: false,
  })

  if (isLoading) {
    return (
      <main className="gate">
        <div className="card gate-card">
          <h1 className="gate-brand">Z-Sheet</h1>
          <p className="muted">{route.isDemo ? '正在加载演示模型…' : '正在加载你的系统…'}</p>
        </div>
      </main>
    )
  }

  if (isError || !data) {
    return (
      <main className="gate">
        <div className="card gate-card">
          <h1 className="gate-brand">Z-Sheet</h1>
          {route.isDemo ? (
            <>
              <p>无法加载演示模型，请确认后端服务已启动。</p>
              <p className="muted">开发模式下后端默认运行在 http://localhost:8000</p>
            </>
          ) : (
            <>
              <p>还没有已生成的系统。</p>
              <p className="muted">
                上传一张 Excel 并完成确认后，这里会用你自己的模型和数据渲染成可浏览的系统。
              </p>
              <p style={{ marginTop: '20px', display: 'flex', gap: '12px', justifyContent: 'center' }}>
                <Link to="/" className="btn btn-primary">
                  去上传表格
                </Link>
                <Link to="/app/demo" className="btn btn-secondary">
                  先看演示系统
                </Link>
              </p>
            </>
          )}
          {route.isDemo && (
            <p style={{ marginTop: '20px' }}>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void refetch()}
                disabled={isFetching}
              >
                {isFetching ? '加载中…' : '重新加载'}
              </button>
            </p>
          )}
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
                to={`${route.basePath}/views/${item.view}`}
                className={({ isActive }) =>
                  isActive ? 'nav-link active' : 'nav-link'
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="topbar-right">
            {route.isDemo ? (
              <NavLink to="/app" className="nav-link">
                我的系统
              </NavLink>
            ) : (
              <NavLink to="/app/demo" className="nav-link">
                演示系统
              </NavLink>
            )}
            <NavLink to="/" className="nav-link nav-link-right">
              上传表格
            </NavLink>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </RuntimeProvider>
  )
}
