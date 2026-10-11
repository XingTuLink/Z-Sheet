import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  HistoryApiError,
  fetchVersions,
  rollbackVersion,
  undoLatest,
  type VersionMeta,
} from '../api/history'
import { formatDateTime } from './format'

type PendingAction = { kind: 'undo' } | { kind: 'rollback'; version: number } | null

interface VersionHistoryDialogProps {
  open: boolean
  appKey: string
  basePath: string
  onClose: () => void
}

function describeSource(version: VersionMeta): string {
  if (version.source_request === null) return '从表格导入生成'
  if (version.source_request.startsWith('回滚到版本')) {
    return version.source_request
  }
  return `一句话修改：${version.source_request}`
}

export function VersionHistoryDialog({
  open,
  appKey,
  basePath,
  onClose,
}: VersionHistoryDialogProps) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [pending, setPending] = useState<PendingAction>(null)
  const [acting, setActing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['versions', appKey],
    queryFn: () => fetchVersions(appKey),
    enabled: open,
    retry: false,
  })

  if (!open) return null

  const versions = (data ?? [])
    .slice()
    .sort((a, b) => b.version - a.version)
  const currentVersion = versions[0]?.version ?? 0

  const resetAndClose = () => {
    setPending(null)
    setActing(false)
    setError(null)
    onClose()
  }

  const handleOverlayClose = () => {
    if (acting) return
    resetAndClose()
  }

  const failMessage = (err: unknown, fallback: string) =>
    err instanceof HistoryApiError ? err.message : fallback

  const applyAction = async () => {
    if (!pending) return
    setActing(true)
    setError(null)
    try {
      if (pending.kind === 'undo') {
        await undoLatest(appKey)
      } else {
        await rollbackVersion(appKey, pending.version)
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['bootstrap', appKey] }),
        queryClient.invalidateQueries({ queryKey: ['versions', appKey] }),
      ])
      navigate(basePath)
      resetAndClose()
    } catch (err) {
      setError(failMessage(err, '操作失败，请稍后重试'))
      setActing(false)
      setPending(null)
    }
  }

  const pendingTarget =
    pending?.kind === 'rollback'
      ? `版本 ${pending.version}`
      : `版本 ${Math.max(currentVersion - 1, 1)}`

  return (
    <div className="patch-overlay" role="presentation" onClick={handleOverlayClose}>
      <div
        className="card patch-dialog history-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="历史版本"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="patch-head">
          <h2>历史版本</h2>
          <button
            type="button"
            className="patch-close"
            aria-label="关闭"
            onClick={handleOverlayClose}
            disabled={acting}
          >
            ×
          </button>
        </div>

        <div className="patch-body history-body">
          {isLoading && <p className="muted">正在加载版本历史…</p>}
          {isError && (
            <p className="patch-empty">版本历史加载失败，请关闭后重试。</p>
          )}

          {!isLoading && !isError && (
            <>
              <div className="history-undo-bar">
                <span>
                  当前为版本 {currentVersion}
                  {currentVersion > 1 && '，可以撤销最近一次修改'}
                </span>
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={acting || currentVersion <= 1}
                  onClick={() => {
                    setError(null)
                    setPending({ kind: 'undo' })
                  }}
                >
                  撤销上次修改
                </button>
              </div>

              <ul className="history-list">
                {versions.map((version) => (
                  <li
                    key={version.id}
                    className={
                      version.version === currentVersion
                        ? 'history-row is-current'
                        : 'history-row'
                    }
                  >
                    <div className="history-row-main">
                      <span className="history-version">v{version.version}</span>
                      {version.version === currentVersion && (
                        <span className="history-tag">当前版本</span>
                      )}
                      <span className="history-source">
                        {describeSource(version)}
                      </span>
                    </div>
                    <div className="history-row-meta">
                      <span>{formatDateTime(version.created_at)}</span>
                      <span className="muted">操作人：{version.operator}</span>
                      {version.version !== currentVersion && (
                        <button
                          type="button"
                          className="btn btn-secondary history-rollback-btn"
                          disabled={acting}
                          onClick={() => {
                            setError(null)
                            setPending({ kind: 'rollback', version: version.version })
                          }}
                        >
                          回滚到此版本
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}

          {pending && (
            <p className="patch-warning">
              将把系统恢复为{pendingTarget}的状态并生成一个新版本；历史版本不会被删除。
            </p>
          )}
          {error && (
            <p className="patch-error" role="alert">
              {error}
            </p>
          )}
        </div>

        <div className="patch-foot">
          {pending ? (
            <>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setPending(null)}
                disabled={acting}
              >
                取消
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={() => void applyAction()}
                disabled={acting}
              >
                {acting ? '正在恢复…' : '确认恢复并生成新版本'}
              </button>
            </>
          ) : (
            <button
              type="button"
              className="btn btn-secondary"
              onClick={resetAndClose}
              disabled={acting}
            >
              关闭
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
