import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  PatchApiError,
  confirmPatch,
  previewPatch,
  rejectPatch,
  type PatchPreview,
} from '../api/patch'

const UNSUPPORTED_LABELS: Record<string, string> = {
  data_query: '临时查询数据',
  data_edit: '修改业务数据',
  conditional_format: '条件格式/标色',
  time_window_metric: '时间窗口指标',
  entity_change: '新增/删除实体',
  many_to_many: '多对多关联',
  layout: '自由布局调整',
  permission: '权限管理',
  workflow: '审批/工作流',
  bi: '透视表/复杂图表',
  excel_sync: 'Excel 双向同步',
  unknown: '暂不支持的操作',
}

const EXAMPLES = [
  '客户列表加一个按负责人筛选',
  '订单列表默认按下单日期从新到旧排序',
  '在客户里加一个微信号文本字段',
]

interface PatchDialogProps {
  open: boolean
  appKey: string
  basePath: string
  onClose: () => void
}

export function PatchDialog({ open, appKey, basePath, onClose }: PatchDialogProps) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [text, setText] = useState('')
  const [preview, setPreview] = useState<PatchPreview | null>(null)
  const [generating, setGenerating] = useState(false)
  const [deciding, setDeciding] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!open) return null

  const busy = generating || deciding

  const resetAndClose = () => {
    setText('')
    setPreview(null)
    setGenerating(false)
    setDeciding(false)
    setError(null)
    onClose()
  }

  const failMessage = (err: unknown, fallback: string) =>
    err instanceof PatchApiError ? err.message : fallback

  const handleGenerate = async () => {
    const sentence = text.trim()
    if (!sentence) {
      setError('请先用一句话描述要修改的内容')
      return
    }
    setGenerating(true)
    setError(null)
    try {
      setPreview(await previewPatch(appKey, sentence))
    } catch (err) {
      setError(failMessage(err, '生成修改预览失败，请稍后重试'))
    } finally {
      setGenerating(false)
    }
  }

  const handleConfirm = async () => {
    if (!preview) return
    setDeciding(true)
    setError(null)
    try {
      await confirmPatch(appKey, preview.session_id)
      await queryClient.invalidateQueries({ queryKey: ['bootstrap', appKey] })
      navigate(basePath)
      resetAndClose()
    } catch (err) {
      setError(failMessage(err, '提交失败，请稍后重试'))
      setDeciding(false)
    }
  }

  const discardPreview = async () => {
    if (preview?.actionable) {
      // Best effort: the pending session expires server-side regardless.
      try {
        await rejectPatch(appKey, preview.session_id)
      } catch {
        // Ignored — the user is leaving the preview either way.
      }
    }
    setPreview(null)
    setError(null)
  }

  const handleOverlayClose = () => {
    if (busy) return
    if (preview?.actionable) {
      void rejectPatch(appKey, preview.session_id).catch(() => undefined)
    }
    resetAndClose()
  }

  return (
    <div
      className="patch-overlay"
      role="presentation"
      onClick={handleOverlayClose}
    >
      <div
        className="card patch-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="用一句话修改系统"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="patch-head">
          <h2>用一句话修改系统</h2>
          <button
            type="button"
            className="patch-close"
            aria-label="关闭"
            onClick={handleOverlayClose}
            disabled={busy}
          >
            ×
          </button>
        </div>

        {!preview ? (
          <>
            <textarea
              className="patch-input"
              value={text}
              maxLength={500}
              rows={4}
              placeholder="描述你想怎么调整，例如：客户列表加一个按负责人筛选"
              onChange={(event) => setText(event.target.value)}
            />
            <div className="patch-examples">
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  type="button"
                  className="patch-example"
                  onClick={() => setText(example)}
                  disabled={busy}
                >
                  {example}
                </button>
              ))}
            </div>
          </>
        ) : (
          <div className="patch-body">
            <p className="patch-sentence">{text.trim()}</p>

            {preview.changes.length > 0 && (
              <ul className="patch-changes">
                {preview.changes.map((change, index) => (
                  <li key={`${change.kind}-${index}`} className="patch-change">
                    <span
                      className={
                        change.risk === 'high'
                          ? 'patch-tag patch-tag-high'
                          : 'patch-tag patch-tag-low'
                      }
                    >
                      {change.risk === 'high' ? '高风险' : '普通'}
                    </span>
                    <span>{change.summary}</span>
                  </li>
                ))}
              </ul>
            )}

            {preview.requires_confirmation && (
              <p className="patch-warning">
                本次修改包含删除字段、指标或关联等高风险操作，确认后将立即生成一个新版本。
              </p>
            )}

            {preview.unsupported.length > 0 && (
              <div className="patch-unsupported">
                <p className="patch-section-title">以下要求 V0.1 暂不能通过修改实现：</p>
                <ul>
                  {preview.unsupported.map((item, index) => (
                    <li key={`${item.category}-${index}`}>
                      <span className="patch-unsupported-name">
                        {UNSUPPORTED_LABELS[item.category] ?? '暂不支持的操作'}
                      </span>
                      ：{item.reason}
                      {item.suggestion ? `（建议：${item.suggestion}）` : ''}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {!preview.actionable && (
              <p className="patch-empty">
                这句话里没有识别出可以直接执行的系统修改，请换一种说法。
              </p>
            )}

            {preview.notes.length > 0 && (
              <ul className="patch-notes">
                {preview.notes.map((note, index) => (
                  <li key={index}>{note}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        {error && <p className="patch-error" role="alert">{error}</p>}

        <div className="patch-foot">
          {!preview ? (
            <>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={resetAndClose}
                disabled={busy}
              >
                取消
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void handleGenerate()}
                disabled={busy || !text.trim()}
              >
                {generating ? '正在理解…' : '生成修改预览'}
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => void discardPreview()}
                disabled={busy}
              >
                {preview.actionable ? '放弃修改' : '重新描述'}
              </button>
              {preview.actionable && (
                <button
                  type="button"
                  className={
                    preview.requires_confirmation ? 'btn btn-danger' : 'btn btn-primary'
                  }
                  onClick={() => void handleConfirm()}
                  disabled={busy}
                >
                  {deciding ? '提交中…' : '确认修改并发布新版本'}
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
