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
  entity_change: '删除实体',
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
  '新增一个供应商模块，包含名称、联系人和电话',
]

interface PatchHistoryEntry {
  text: string
  at: number
  confirmed: boolean
}

const HISTORY_LIMIT = 10

function draftKey(appKey: string) {
  return `zsheet:patch:draft:${appKey}`
}

function historyStorageKey(appKey: string) {
  return `zsheet:patch:history:${appKey}`
}

function readDraft(appKey: string): string {
  try {
    return localStorage.getItem(draftKey(appKey)) ?? ''
  } catch {
    return ''
  }
}

function writeDraft(appKey: string, text: string) {
  try {
    if (text) localStorage.setItem(draftKey(appKey), text)
    else localStorage.removeItem(draftKey(appKey))
  } catch {
    // Storage unavailable (private mode) — history just won't persist.
  }
}

function readHistory(appKey: string): PatchHistoryEntry[] {
  try {
    const raw = localStorage.getItem(historyStorageKey(appKey))
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter(
        (item): item is PatchHistoryEntry =>
          typeof item === 'object' &&
          item !== null &&
          typeof (item as PatchHistoryEntry).text === 'string' &&
          typeof (item as PatchHistoryEntry).at === 'number',
      )
      .slice(0, HISTORY_LIMIT)
  } catch {
    return []
  }
}

function writeHistory(appKey: string, entries: PatchHistoryEntry[]) {
  try {
    localStorage.setItem(historyStorageKey(appKey), JSON.stringify(entries))
  } catch {
    // Storage unavailable — ignore.
  }
}

function rememberRequest(
  appKey: string,
  text: string,
  confirmed: boolean,
): PatchHistoryEntry[] {
  const existing = readHistory(appKey).filter((entry) => entry.text !== text)
  const next = [{ text, at: Date.now(), confirmed }, ...existing].slice(
    0,
    HISTORY_LIMIT,
  )
  writeHistory(appKey, next)
  return next
}

function formatHistoryTime(at: number): string {
  const date = new Date(at)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

interface PatchDialogProps {
  open: boolean
  appKey: string
  basePath: string
  onClose: () => void
}

export function PatchDialog({ open, appKey, basePath, onClose }: PatchDialogProps) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [text, setText] = useState(() => readDraft(appKey))
  const [history, setHistory] = useState<PatchHistoryEntry[]>(() =>
    readHistory(appKey),
  )
  const [preview, setPreview] = useState<PatchPreview | null>(null)
  const [generating, setGenerating] = useState(false)
  const [deciding, setDeciding] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Re-sync from storage whenever the dialog opens (render-phase adjustment,
  // the sanctioned alternative to setState inside an effect).
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setText(readDraft(appKey))
      setHistory(readHistory(appKey))
    }
  }

  if (!open) return null

  const busy = generating || deciding

  // Closing only dismisses the preview; the typed draft stays stored.
  const closeShell = () => {
    setPreview(null)
    setGenerating(false)
    setDeciding(false)
    setError(null)
    onClose()
  }

  const updateText = (next: string) => {
    setText(next)
    writeDraft(appKey, next)
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
      const result = await previewPatch(appKey, sentence)
      setPreview(result)
      setHistory(rememberRequest(appKey, sentence, false))
    } catch (err) {
      setError(failMessage(err, '生成修改预览失败，请稍后重试'))
    } finally {
      setGenerating(false)
    }
  }

  const handleConfirm = async () => {
    if (!preview) return
    const sentence = text.trim()
    setDeciding(true)
    setError(null)
    try {
      await confirmPatch(appKey, preview.session_id)
      setHistory(rememberRequest(appKey, sentence, true))
      updateText('')
      await queryClient.invalidateQueries({ queryKey: ['bootstrap', appKey] })
      navigate(basePath)
      closeShell()
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
    closeShell()
  }

  const clearHistory = () => {
    writeHistory(appKey, [])
    setHistory([])
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
              onChange={(event) => updateText(event.target.value)}
            />
            <div className="patch-examples">
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  type="button"
                  className="patch-example"
                  onClick={() => updateText(example)}
                  disabled={busy}
                >
                  {example}
                </button>
              ))}
            </div>
            {history.length > 0 && (
              <div className="patch-history">
                <div className="patch-history-head">
                  <span>最近提过的修改</span>
                  <button
                    type="button"
                    className="patch-history-clear"
                    onClick={clearHistory}
                    disabled={busy}
                  >
                    清空
                  </button>
                </div>
                <ul className="patch-history-list">
                  {history.map((entry) => (
                    <li key={`${entry.at}-${entry.text.slice(0, 8)}`}>
                      <button
                        type="button"
                        className="patch-history-item"
                        onClick={() => updateText(entry.text)}
                        disabled={busy}
                        title={entry.text}
                      >
                        <span className="patch-history-text">{entry.text}</span>
                        <span className="patch-history-meta">
                          <span
                            className={
                              entry.confirmed
                                ? 'patch-history-badge is-confirmed'
                                : 'patch-history-badge'
                            }
                          >
                            {entry.confirmed ? '已生效' : '未确认'}
                          </span>
                          <time>{formatHistoryTime(entry.at)}</time>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
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
                onClick={closeShell}
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
