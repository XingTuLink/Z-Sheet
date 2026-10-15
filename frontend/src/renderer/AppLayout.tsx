import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'

import { fetchBootstrap } from '../api/runtime'
import { BrandLockup, CompanyByline, COMPANY_NAME } from '../components/Brand'
import { resolveAppRoute } from './appRoute'
import { PatchDialog } from './PatchDialog'
import { PublishDialog } from './PublishDialog'
import { RuntimeProvider } from './RuntimeProvider'
import { VersionHistoryDialog } from './VersionHistoryDialog'

export function AppLayout() {
  const location = useLocation()
  const route = resolveAppRoute(location.pathname)
  const [patchOpen, setPatchOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [publishOpen, setPublishOpen] = useState(false)
  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: ['bootstrap', route.appKey],
    queryFn: () => fetchBootstrap(route.appKey),
    retry: false,
  })

  if (isLoading) {
    return (
      <main className="gate">
        <div className="card gate-card">
          <h1 className="gate-brand">
            <BrandLockup logoSize={34} />
          </h1>
          <p className="muted">{route.isDemo ? '正在加载演示模型…' : '正在加载你的系统…'}</p>
          <CompanyByline />
        </div>
      </main>
    )
  }

  if (isError || !data) {
    return (
      <main className="gate">
        <div className="card gate-card">
          <h1 className="gate-brand">
            <BrandLockup logoSize={34} />
          </h1>
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
          <CompanyByline />
        </div>
      </main>
    )
  }

  return (
    <RuntimeProvider
      key={`${data.app_key}:${data.version}`}
      bootstrap={data}
    >
      <div className="app-shell">
        <header className="topbar">
          <div className="brand">
            <BrandLockup logoSize={26} />
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
            {!route.isDemo && (
              <>
                <button
                  type="button"
                  className="btn btn-secondary btn-patch-entry"
                  onClick={() => setPublishOpen(true)}
                >
                  发布
                </button>
                <button
                  type="button"
                  className="btn btn-primary btn-patch-entry"
                  onClick={() => setPatchOpen(true)}
                >
                  一句话修改
                </button>
                <button
                  type="button"
                  className="btn btn-secondary btn-patch-entry"
                  onClick={() => setHistoryOpen(true)}
                >
                  历史版本
                </button>
              </>
            )}
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
            <span className="brand-company" title={COMPANY_NAME}>
              {COMPANY_NAME}
            </span>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
        <PatchDialog
          open={patchOpen}
          appKey={route.appKey}
          basePath={route.basePath}
          onClose={() => setPatchOpen(false)}
        />
        <VersionHistoryDialog
          open={historyOpen}
          appKey={route.appKey}
          basePath={route.basePath}
          onClose={() => setHistoryOpen(false)}
        />
        <PublishDialog
          open={publishOpen}
          appKey={route.appKey}
          onClose={() => setPublishOpen(false)}
        />
      </div>
    </RuntimeProvider>
  )
}
