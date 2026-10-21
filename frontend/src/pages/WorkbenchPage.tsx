// Workbench: every generated system in one place. Cards are links into the
// runtime shell; the side column lists the latest changes so the home page
// answers "what did I build and what changed recently" without opening each
// system.

import { useQueries, useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import {
  fetchAppOverview,
  fetchApps,
  type AppSummary,
  type ChangeEvent,
} from '../api/apps'
import { BrandLockup, CompanyFooter } from '../components/Brand'

function formatTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function AppCard({ app }: { app: AppSummary }) {
  return (
    <Link to={`/app/${encodeURIComponent(app.app_key)}`} className="wb-card card">
      <div className="wb-card-head">
        <h2>{app.name}</h2>
        {app.publication?.public && <span className="wb-badge">已发布</span>}
      </div>
      <p className="wb-card-stats">
        <span>{app.entity_count} 个实体</span>
        <span className="wb-dot" aria-hidden="true">·</span>
        <span>{app.record_count} 条记录</span>
        <span className="wb-dot" aria-hidden="true">·</span>
        <span>版本 v{app.current_version}</span>
      </p>
      <p className="wb-card-foot">更新于 {formatTime(app.updated_at)}</p>
    </Link>
  )
}

export function WorkbenchPage() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['apps'],
    queryFn: fetchApps,
  })

  const apps = data ?? []
  const recent = [...apps]
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 5)

  // One overview per recently touched system, merged into a single feed.
  const overviews = useQueries({
    queries: recent.map((app) => ({
      queryKey: ['app-overview', app.app_key],
      queryFn: () => fetchAppOverview(app.app_key),
      retry: false,
      staleTime: 30_000,
    })),
  })
  const feed = overviews
    .flatMap((query, index) =>
      (query.data?.recent_changes ?? []).map((event: ChangeEvent) => ({
        ...event,
        appKey: recent[index].app_key,
        appName: recent[index].name,
      })),
    )
    .sort((a, b) => b.at.localeCompare(a.at))
    .slice(0, 8)

  return (
    <div className="wb-page">
      <header className="wb-header">
        <Link to="/" className="brand brand-link">
          <BrandLockup logoSize={28} />
        </Link>
        <div className="wb-header-actions">
          <Link to="/settings" className="btn btn-secondary">
            模型设置
          </Link>
          <Link to="/new" className="btn btn-primary">
            新建系统
          </Link>
        </div>
      </header>

      <main className="wb-body">
        <section className="wb-main">
          <div className="wb-title-row">
            <div>
              <h1>我的系统</h1>
              <p className="muted wb-sub">
                上传表格生成业务系统，或继续使用已有的系统。
              </p>
            </div>
          </div>

          {isLoading && (
            <div className="card wb-placeholder">
              <p className="muted">正在加载系统…</p>
            </div>
          )}

          {isError && (
            <div className="card wb-placeholder">
              <p>无法加载系统列表。</p>
              <p className="muted">
                {error instanceof Error ? error.message : '请确认后端服务已启动。'}
              </p>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => void refetch()}
              >
                重新加载
              </button>
            </div>
          )}

          {!isLoading && !isError && apps.length === 0 && (
            <Link to="/new" className="wb-empty card">
              <span className="wb-empty-title">还没有系统</span>
              <span className="muted">
                点击这里上传 Excel 或 CSV，完成后会出现在这个工作台。
              </span>
              <span className="btn btn-primary">新建系统</span>
            </Link>
          )}

          {!isLoading && !isError && apps.length > 0 && (
            <div className="wb-grid">
              {apps.map((app) => (
                <AppCard key={app.app_key} app={app} />
              ))}
            </div>
          )}
        </section>

        <aside className="wb-side">
          <div className="card wb-side-card">
            <h2>最近动态</h2>
            {feed.length === 0 ? (
              <p className="muted">生成第一个系统后，这里会显示最近变化。</p>
            ) : (
              <ul className="wb-feed">
                {feed.map((event, index) => (
                  <li key={`${event.appKey}-${event.at}-${index}`}>
                    <Link to={`/app/${encodeURIComponent(event.appKey)}`}>
                      {event.appName}
                    </Link>
                    <span className="wb-feed-summary">{event.summary}</span>
                    <span className="muted wb-feed-time">{formatTime(event.at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="card wb-side-card">
            <h2>演示系统</h2>
            <p className="muted">
              不想先上传文件？用内置的演示数据看看生成后的效果。
            </p>
            <Link to="/app/demo" className="btn btn-secondary">
              打开演示系统
            </Link>
          </div>
        </aside>
      </main>

      <CompanyFooter />
    </div>
  )
}
