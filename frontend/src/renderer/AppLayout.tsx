import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'

import { RecordApiError, appApiBase, fetchBootstrap } from '../api/runtime'
import { BrandLockup, CompanyByline, CompanyFooter, LOGO_SRC } from '../components/Brand'
import { AppendDataDialog } from './AppendDataDialog'
import { resolveAppRoute } from './appRoute'
import { PatchDialog } from './PatchDialog'
import { RuntimeProvider } from './RuntimeProvider'

export function AppLayout() {
  const location = useLocation()
  const route = resolveAppRoute(location.pathname)
  const [patchOpen, setPatchOpen] = useState(false)
  const [appendOpen, setAppendOpen] = useState(false)
  const { data, isLoading, isError, isFetching, error, refetch } = useQuery({
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
    const status = error instanceof RecordApiError ? error.status : null
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
          ) : status === 403 ? (
            <>
              <p>没有访问权限。</p>
              <p className="muted">
                当前账号不是这个系统的成员。可以请所有者把你的账号加入成员，或返回工作台。
              </p>
              <p style={{ marginTop: '20px' }}>
                <Link to="/" className="btn btn-secondary">
                  返回工作台
                </Link>
              </p>
            </>
          ) : status === 401 ? (
            <>
              <p>请先登录。</p>
              <p className="muted">登录后即可查看你有权限的系统。</p>
              <p style={{ marginTop: '20px' }}>
                <Link to="/login" className="btn btn-primary">
                  去登录
                </Link>
              </p>
            </>
          ) : (
            <>
              <p>还没有已生成的系统。</p>
              <p className="muted">
                上传一张 Excel 并完成确认后，这里会用你自己的模型和数据渲染成可浏览的系统。
              </p>
              <p style={{ marginTop: '20px', display: 'flex', gap: '12px', justifyContent: 'center' }}>
                <Link to="/new" className="btn btn-primary">
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
    <>
      <RuntimeProvider
        key={`${data.app_key}:${data.version}`}
        bootstrap={data}
        apiBase={appApiBase(data.app_key)}
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
                  onClick={() => setAppendOpen(true)}
                >
                  追加数据
                </button>
                <Link
                  to={`${route.basePath}/admin`}
                  className="btn btn-secondary btn-patch-entry"
                >
                  管理
                </Link>
              </>
            )}
            {!route.isDemo && (
              <NavLink to="/app/demo" className="nav-link">
                演示系统
              </NavLink>
            )}
            <NavLink to="/" className="nav-link nav-link-right">
              工作台
            </NavLink>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
        <CompanyFooter />
        {!route.isDemo && !patchOpen && (
          <button
            type="button"
            className="patch-fab"
            aria-label="用一句话修改系统"
            title="用一句话修改系统"
            onClick={() => setPatchOpen(true)}
          >
            <img src={LOGO_SRC} alt="" width={32} height={32} />
            <span className="patch-fab-ring" aria-hidden="true" />
          </button>
        )}
        <PatchDialog
          open={patchOpen}
          appKey={route.appKey}
          basePath={route.basePath}
          onClose={() => setPatchOpen(false)}
        />
      </div>
      </RuntimeProvider>
      <AppendDataDialog
        open={appendOpen}
        appKey={route.appKey}
        entities={data.model.entities.map((entity) => ({
          key: entity.key,
          name: entity.name,
        }))}
        onClose={() => setAppendOpen(false)}
      />
    </>
  )
}
