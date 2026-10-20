import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import {
  AppendApiError,
  confirmAppend,
  fetchAppendBatches,
  previewAppend,
  rollbackAppend,
  type AppendBatchMeta,
  type AppendPreview,
} from '../api/appends'
import { formatDateTime } from './format'

interface AppendDataDialogProps {
  open: boolean
  appKey: string
  entities: { key: string; name: string }[]
  onClose: () => void
}

const STRATEGY_LABEL: Record<string, string> = {
  skip: '跳过重复',
  overwrite: '覆盖旧数据',
  keep: '保留两条',
}

const STATUS_LABEL: Record<string, string> = {
  applied: '已应用',
  rolled_back: '已回滚',
  pending: '未确认',
}

export function AppendDataDialog({
  open,
  appKey,
  entities,
  onClose,
}: AppendDataDialogProps) {
  const queryClient = useQueryClient()
  const [entityKey, setEntityKey] = useState(entities[0]?.key ?? '')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<AppendPreview | null>(null)
  const [strategy, setStrategy] = useState('skip')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const batches = useQuery({
    queryKey: ['append-batches', appKey],
    queryFn: () => fetchAppendBatches(appKey),
    enabled: open,
    retry: false,
  })

  if (!open) return null

  const failMessage = (err: unknown, fallback: string) =>
    err instanceof AppendApiError ? err.message : fallback

  const resetAndClose = () => {
    if (busy) return
    setFile(null)
    setPreview(null)
    setStrategy('skip')
    setError(null)
    setNotice(null)
    onClose()
  }

  const refreshData = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['bootstrap', appKey] }),
      queryClient.invalidateQueries({ queryKey: ['append-batches', appKey] }),
    ])
  }

  const runPreview = async () => {
    if (!file || !entityKey) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await previewAppend(appKey, entityKey, file)
      setPreview(result)
      setStrategy(result.strategies[0]?.value ?? 'skip')
      await queryClient.invalidateQueries({
        queryKey: ['append-batches', appKey],
      })
    } catch (err) {
      setPreview(null)
      setError(failMessage(err, '预览失败，请稍后重试。'))
    } finally {
      setBusy(false)
    }
  }

  const applyAppend = async () => {
    if (!preview) return
    setBusy(true)
    setError(null)
    try {
      const result = await confirmAppend(preview.batch_id, strategy)
      setNotice(
        `已追加：新增 ${result.inserted} 行，覆盖 ${result.overwritten} 行，` +
          `跳过 ${result.skipped} 行；当前版本 v${result.version}`,
      )
      setPreview(null)
      setFile(null)
      await refreshData()
    } catch (err) {
      setError(failMessage(err, '追加失败，请稍后重试。'))
    } finally {
      setBusy(false)
    }
  }

  const rollback = async (batch: AppendBatchMeta) => {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await rollbackAppend(batch.id)
      setNotice(
        `已回滚「${batch.file_name || batch.entity_name}」这次追加；` +
          `删除 ${batch.inserted} 行，恢复 ${result.overwritten} 行`,
      )
      await refreshData()
    } catch (err) {
      setError(failMessage(err, '回滚失败，请稍后重试。'))
    } finally {
      setBusy(false)
    }
  }

  const applied = (batches.data ?? []).filter((batch) => batch.status === 'applied')

  return (
    <div className="patch-overlay" role="presentation" onClick={resetAndClose}>
      <div
        className="card patch-dialog append-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="追加数据"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="patch-head">
          <h2>追加数据</h2>
          <button
            type="button"
            className="patch-close"
            aria-label="关闭"
            onClick={resetAndClose}
            disabled={busy}
          >
            ×
          </button>
        </div>

        <div className="patch-body append-body">
          <p className="muted append-hint">
            把新的 Excel / CSV 行追加到已有实体。系统会按列名自动对齐字段，
            新出现的列会成为新字段并生成新版本；重复主键按你选择的方式处理。
          </p>

          <div className="append-form-row">
            <label className="append-field">
              <span>追加到</span>
              <select
                value={entityKey}
                onChange={(event) => {
                  setEntityKey(event.target.value)
                  setPreview(null)
                  setNotice(null)
                }}
                disabled={busy}
              >
                {entities.map((entity) => (
                  <option key={entity.key} value={entity.key}>
                    {entity.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="append-field append-file-field">
              <span>选择文件</span>
              <input
                type="file"
                accept=".xlsx,.csv"
                disabled={busy}
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null)
                  setPreview(null)
                  setNotice(null)
                  setError(null)
                }}
              />
            </label>
            <button
              type="button"
              className="btn btn-secondary append-preview-btn"
              onClick={() => void runPreview()}
              disabled={busy || !file || !entityKey}
            >
              {busy ? '处理中…' : '分析文件'}
            </button>
          </div>

          {notice && <p className="append-notice">{notice}</p>}
          {error && (
            <p className="patch-error" role="alert">
              {error}
            </p>
          )}

          {preview && (
            <>
              <div className="append-summary">
                <div className="append-summary-row">
                  <span className="append-summary-label">工作表</span>
                  <span>{preview.sheet_name}</span>
                </div>
                <div className="append-summary-row">
                  <span className="append-summary-label">可导入</span>
                  <span>{preview.valid_rows} 行</span>
                </div>
                <div className="append-summary-row">
                  <span className="append-summary-label">新增行</span>
                  <span>{preview.new_rows} 行</span>
                </div>
                <div className="append-summary-row">
                  <span className="append-summary-label">重复主键</span>
                  <span>{preview.duplicate_rows} 行</span>
                </div>
                {preview.invalid_rows > 0 && (
                  <div className="append-summary-row">
                    <span className="append-summary-label">无法解析</span>
                    <span>{preview.invalid_rows} 行</span>
                  </div>
                )}
              </div>

              {preview.new_fields.length > 0 && (
                <div className="append-new-fields">
                  <span className="append-summary-label">将新增字段</span>
                  <div className="append-chip-row">
                    {preview.new_fields.map((field) => (
                      <span key={field.key} className="append-chip">
                        {field.name}
                        <em>{field.type}</em>
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {preview.duplicate_sample.length > 0 && (
                <p className="muted append-detail">
                  重复主键示例：{preview.duplicate_sample.join('、')}
                </p>
              )}
              {preview.invalid_messages.length > 0 && (
                <p className="patch-warning">
                  {preview.invalid_messages.join('；')}
                </p>
              )}
              {preview.warnings.length > 0 && (
                <p className="muted append-detail">
                  {preview.warnings.join('；')}
                </p>
              )}

              <div className="append-strategies">
                {preview.strategies.map((option) => (
                  <label
                    key={option.value}
                    className={
                      strategy === option.value
                        ? 'append-strategy is-active'
                        : 'append-strategy'
                    }
                  >
                    <input
                      type="radio"
                      name="append-strategy"
                      value={option.value}
                      checked={strategy === option.value}
                      onChange={() => setStrategy(option.value)}
                      disabled={busy}
                    />
                    <span className="append-strategy-label">{option.label}</span>
                    <span className="muted">{option.description}</span>
                  </label>
                ))}
              </div>
            </>
          )}

          {applied.length > 0 && (
            <div className="append-history">
              <h3 className="append-history-title">最近追加</h3>
              <ul className="append-history-list">
                {applied.map((batch) => (
                  <li key={batch.id} className="append-history-row">
                    <div className="append-history-main">
                      <span className="append-history-file">
                        {batch.file_name || batch.sheet_name}
                      </span>
                      <span className="append-history-meta">
                        {batch.entity_name} · 新增 {batch.inserted} 行
                        {batch.overwritten > 0 && ` · 覆盖 ${batch.overwritten} 行`}
                        {batch.strategy &&
                          ` · ${STRATEGY_LABEL[batch.strategy] ?? batch.strategy}`}
                        {batch.status !== 'applied' &&
                          ` · ${STATUS_LABEL[batch.status] ?? batch.status}`}
                      </span>
                      <span className="append-history-meta">
                        {formatDateTime(batch.created_at)}
                      </span>
                    </div>
                    <button
                      type="button"
                      className="btn btn-secondary append-rollback-btn"
                      disabled={busy}
                      onClick={() => void rollback(batch)}
                    >
                      回滚这次追加
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {batches.isLoading && <p className="muted">正在读取追加记录…</p>}
        </div>

        <div className="patch-foot">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={resetAndClose}
            disabled={busy}
          >
            关闭
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void applyAppend()}
            disabled={busy || !preview}
          >
            {busy && preview ? '正在追加…' : '确认追加'}
          </button>
        </div>
      </div>
    </div>
  )
}
