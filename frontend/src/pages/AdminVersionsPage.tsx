// Versions and publication for one system. The heavy interactions (undo,
// rollback, slug changes) stay in the existing dialogs so this page adds no
// second implementation of those rules.

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useLocation } from 'react-router-dom'

import { fetchVersions } from '../api/history'
import { buildPublicUrl, fetchPublication } from '../api/publication'
import { PublishDialog } from '../renderer/PublishDialog'
import { VersionHistoryDialog } from '../renderer/VersionHistoryDialog'
import { resolveAppRoute } from '../renderer/appRoute'
import { formatDateTime } from '../renderer/format'

export function AdminVersionsPage() {
  const { appKey, basePath, isDemo } = resolveAppRoute(useLocation().pathname)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [publishOpen, setPublishOpen] = useState(false)

  const versions = useQuery({
    queryKey: ['versions', appKey],
    queryFn: () => fetchVersions(appKey),
    enabled: !isDemo,
    retry: false,
  })
  const publication = useQuery({
    queryKey: ['publication', appKey],
    queryFn: () => fetchPublication(appKey),
    enabled: !isDemo,
    retry: false,
  })

  if (isDemo) {
    return (
      <div className="card admin-placeholder">
        <p>演示系统不支持管理后台。</p>
      </div>
    )
  }

  const latest = (versions.data ?? [])
    .slice()
    .sort((a, b) => b.version - a.version)
    .slice(0, 8)

  return (
    <section className="admin-page">
      <div className="admin-page-head">
        <div>
          <h1>版本与发布</h1>
          <p className="muted">回滚会追加一个新版本，历史版本始终保留。</p>
        </div>
      </div>

      <div className="admin-columns">
        <div className="card admin-panel">
          <div className="admin-panel-head">
            <h2>版本历史</h2>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setHistoryOpen(true)}
            >
              版本历史与回滚
            </button>
          </div>
          {versions.isLoading && <p className="muted">正在加载版本…</p>}
          {versions.isError && <p className="muted">无法加载版本历史。</p>}
          {!versions.isLoading && !versions.isError && (
            <table className="admin-table">
              <thead>
                <tr>
                  <th>版本</th>
                  <th>来源</th>
                  <th>创建时间</th>
                </tr>
              </thead>
              <tbody>
                {latest.map((version) => (
                  <tr key={version.id}>
                    <td>v{version.version}</td>
                    <td className="admin-cell-wrap">
                      {version.source_request ?? '从表格导入生成'}
                    </td>
                    <td>{formatDateTime(version.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card admin-panel">
          <div className="admin-panel-head">
            <h2>公开发布</h2>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setPublishOpen(true)}
            >
              发布设置
            </button>
          </div>
          {publication.isLoading && <p className="muted">正在加载发布状态…</p>}
          {publication.isError && <p className="muted">无法加载发布状态。</p>}
          {!publication.isLoading && !publication.isError && (
            <div className="admin-publish">
              <p>
                状态：
                <strong>{publication.data?.public ? '已发布' : '未发布'}</strong>
              </p>
              {publication.data?.public && (
                <>
                  <p className="muted">
                    钉住版本 v{publication.data.model_version}
                  </p>
                  <a
                    href={buildPublicUrl(publication.data.slug)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {buildPublicUrl(publication.data.slug)}
                  </a>
                </>
              )}
              {!publication.data?.public && (
                <p className="muted">
                  发布后可通过只读链接分享给没有账号的人查看业务数据。
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      <VersionHistoryDialog
        open={historyOpen}
        appKey={appKey}
        basePath={basePath}
        onClose={() => setHistoryOpen(false)}
      />
      <PublishDialog
        open={publishOpen}
        appKey={appKey}
        onClose={() => setPublishOpen(false)}
      />
    </section>
  )
}
