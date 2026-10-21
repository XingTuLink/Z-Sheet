// Admin overview: counters, per-entity row counts and the recent change feed.

import { useQuery } from '@tanstack/react-query'
import { Link, useLocation } from 'react-router-dom'

import { fetchAppOverview, type ChangeEvent } from '../api/apps'
import { resolveAppRoute } from '../renderer/appRoute'

const KIND_LABELS: Record<ChangeEvent['kind'], string> = {
  model: '模型',
  append: '追加数据',
  publication: '发布',
}

function formatTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function AdminOverviewPage() {
  const { appKey, basePath, isDemo } = resolveAppRoute(useLocation().pathname)
  const { data, isLoading, isError } = useQuery({
    queryKey: ['app-overview', appKey],
    queryFn: () => fetchAppOverview(appKey),
    retry: false,
    enabled: !isDemo,
  })

  if (isDemo) {
    return (
      <div className="card admin-placeholder">
        <p>演示系统不支持管理后台。</p>
      </div>
    )
  }
  if (isLoading) return <p className="muted">正在加载概览…</p>
  if (isError || !data) {
    return (
      <div className="card admin-placeholder">
        <p>无法加载系统概览。</p>
      </div>
    )
  }

  return (
    <section className="admin-page">
      <div className="admin-page-head">
        <div>
          <h1>{data.name}</h1>
          <p className="muted">版本 v{data.current_version} · 共 {data.version_count} 个版本</p>
        </div>
      </div>

      <div className="admin-stats">
        <div className="card admin-stat">
          <span className="admin-stat-label">业务实体</span>
          <span className="admin-stat-value">{data.entity_count}</span>
        </div>
        <div className="card admin-stat">
          <span className="admin-stat-label">记录总数</span>
          <span className="admin-stat-value">{data.record_count}</span>
        </div>
        <div className="card admin-stat">
          <span className="admin-stat-label">当前版本</span>
          <span className="admin-stat-value">v{data.current_version}</span>
        </div>
        <div className="card admin-stat">
          <span className="admin-stat-label">公开链接</span>
          <span className="admin-stat-value admin-stat-text">
            {data.publication?.public ? '已发布' : '未发布'}
          </span>
        </div>
      </div>

      <div className="admin-columns">
        <div className="card admin-panel">
          <h2>实体记录数</h2>
          {data.entities.length === 0 ? (
            <p className="muted">当前模型没有业务实体。</p>
          ) : (
            <table className="admin-table">
              <thead>
                <tr>
                  <th>实体</th>
                  <th className="admin-num">记录数</th>
                </tr>
              </thead>
              <tbody>
                {data.entities.map((entity) => (
                  <tr key={entity.entity_key}>
                    <td>{entity.name}</td>
                    <td className="admin-num">{entity.record_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card admin-panel">
          <h2>最近变化</h2>
          {data.recent_changes.length === 0 ? (
            <p className="muted">还没有变更记录。</p>
          ) : (
            <ul className="admin-events">
              {data.recent_changes.map((event, index) => (
                <li key={`${event.kind}-${event.at}-${index}`}>
                  <span className={`admin-event-kind kind-${event.kind}`}>
                    {KIND_LABELS[event.kind]}
                  </span>
                  <span className="admin-event-text">{event.summary}</span>
                  <span className="muted admin-event-time">{formatTime(event.at)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <p className="admin-foot-links">
        <Link to={`${basePath}/admin/model`}>查看模型结构</Link>
        <Link to={`${basePath}/admin/versions`}>管理版本与发布</Link>
      </p>
    </section>
  )
}
