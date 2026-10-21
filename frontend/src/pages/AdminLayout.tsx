// Admin shell for one system: overview, model structure and version/release.
// The business runtime stays at /app/:appKey; management lives beside it so
// day-to-day pages do not carry release controls.

import { useQuery } from '@tanstack/react-query'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'

import { fetchBootstrap } from '../api/runtime'
import { BrandLockup, CompanyFooter } from '../components/Brand'
import { resolveAppRoute } from '../renderer/appRoute'

export function AdminLayout() {
  const route = resolveAppRoute(useLocation().pathname)
  const { data, isLoading, isError } = useQuery({
    queryKey: ['bootstrap', route.appKey],
    queryFn: () => fetchBootstrap(route.appKey),
    retry: false,
  })

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <BrandLockup logoSize={26} />
          <span className="brand-app">{data?.model.app.name ?? '系统管理'}</span>
        </div>
        <div className="topbar-right">
          <Link to={route.basePath} className="nav-link">
            返回系统
          </Link>
          <Link to="/" className="nav-link nav-link-right">
            工作台
          </Link>
        </div>
      </header>
      <div className="admin-body">
        <aside className="admin-side">
          <NavLink
            end
            to={`${route.basePath}/admin`}
            className={({ isActive }) =>
              isActive ? 'admin-link active' : 'admin-link'
            }
          >
            概览
          </NavLink>
          <NavLink
            to={`${route.basePath}/admin/model`}
            className={({ isActive }) =>
              isActive ? 'admin-link active' : 'admin-link'
            }
          >
            模型结构
          </NavLink>
          <NavLink
            to={`${route.basePath}/admin/versions`}
            className={({ isActive }) =>
              isActive ? 'admin-link active' : 'admin-link'
            }
          >
            版本与发布
          </NavLink>
        </aside>
        <main className="admin-content">
          {isLoading && <p className="muted">正在加载系统…</p>}
          {isError && (
            <div className="card admin-placeholder">
              <p>无法加载这个系统。</p>
              <p className="muted">请确认系统存在且后端服务已启动。</p>
            </div>
          )}
          {!isLoading && !isError && data && <Outlet />}
        </main>
      </div>
      <CompanyFooter />
    </div>
  )
}
