// Day 33: strictly read-only shell for published apps (/p/:slug).
// It only calls the public bootstrap endpoint; no mutation affordance exists
// here, and the backend exposes no write endpoint under /api/v1/p.

import { useQuery } from '@tanstack/react-query'
import { NavLink, Outlet, useParams } from 'react-router-dom'

import { fetchPublicBootstrap } from '../api/publication'
import { publicApiBase } from '../api/runtime'
import { BrandLockup, CompanyByline, CompanyFooter } from '../components/Brand'
import { RuntimeProvider } from './RuntimeProvider'

export function PublicAppLayout() {
  const { slug = '' } = useParams<{ slug: string }>()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['public-bootstrap', slug],
    queryFn: () => fetchPublicBootstrap(slug),
    retry: false,
  })

  if (isLoading) {
    return (
      <main className="gate">
        <div className="card gate-card">
          <h1 className="gate-brand">
            <BrandLockup logoSize={34} />
          </h1>
          <p className="muted">正在加载已发布的系统…</p>
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
          <p>链接不存在或该系统已下线。</p>
          <p className="muted">请联系分享者确认链接是否仍然有效。</p>
          <CompanyByline />
        </div>
      </main>
    )
  }

  const basePath = `/p/${encodeURIComponent(slug)}`

  return (
    <RuntimeProvider
      key={`pub:${slug}:${data.version}`}
      bootstrap={data}
      apiBase={publicApiBase(slug)}
      readOnly
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
                to={`${basePath}/views/${item.view}`}
                className={({ isActive }) =>
                  isActive ? 'nav-link active' : 'nav-link'
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="topbar-right">
            <span className="public-readonly-badge" title="此系统由他人发布，仅可浏览">
              只读
            </span>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
        <CompanyFooter />
      </div>
    </RuntimeProvider>
  )
}
