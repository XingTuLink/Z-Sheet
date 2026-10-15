// Day 33: publish the workspace app under a custom public slug.
// The dialog covers the full lifecycle: first publish, copy link, notice that
// a newer model version exists (update publish), change slug (old link dies
// immediately), and unpublish. Each lifecycle phase is a small form whose
// local input state initializes from server data; a closed dialog renders
// nothing, so opening it again mounts the forms fresh and resets state.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { ReactNode } from 'react'

import {
  PublicationApiError,
  buildPublicUrl,
  fetchPublication,
  normalizeSlug,
  publishApp,
  unpublishApp,
  updatePublication,
  validateSlug,
  type Publication,
} from '../api/publication'

interface PublishDialogProps {
  open: boolean
  appKey: string
  onClose: () => void
}

const failMessage = (err: unknown, fallback: string) =>
  err instanceof PublicationApiError ? err.message : fallback

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    // Clipboard API can be unavailable on non-HTTPS hosts; fall back.
    const box = document.createElement('textarea')
    box.value = text
    box.style.position = 'fixed'
    box.style.opacity = '0'
    document.body.append(box)
    box.select()
    document.execCommand('copy')
    box.remove()
  }
}

function DialogShell({
  children,
  onClose,
  busy,
}: {
  children: ReactNode
  onClose: () => void
  busy: boolean
}) {
  return (
    <div
      className="patch-overlay"
      role="presentation"
      onClick={() => {
        if (!busy) onClose()
      }}
    >
      <div
        className="card patch-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="发布系统"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="patch-head">
          <h2>发布系统</h2>
          <button
            type="button"
            className="patch-close"
            aria-label="关闭"
            onClick={onClose}
            disabled={busy}
          >
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

function SlugInput({
  id,
  value,
  onChange,
}: {
  id: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <div className="publish-url-row">
      <span className="publish-url-prefix">{window.location.origin}/p/</span>
      <input
        id={id}
        className="publish-slug-input"
        value={value}
        maxLength={40}
        placeholder="例如：kehu-xiaoshou"
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  )
}

function CreateForm({
  appKey,
  onClose,
}: {
  appKey: string
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [slugInput, setSlugInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const invalid = slugInput.trim() ? validateSlug(slugInput) : null

  const handleCreate = async () => {
    const problem = validateSlug(slugInput)
    if (problem) {
      setError(problem)
      return
    }
    setBusy(true)
    setError(null)
    try {
      await publishApp(appKey, normalizeSlug(slugInput))
      await queryClient.invalidateQueries({ queryKey: ['publication', appKey] })
    } catch (err) {
      setError(failMessage(err, '发布失败，请稍后重试'))
      setBusy(false)
    }
  }

  return (
    <DialogShell onClose={onClose} busy={busy}>
      <div className="patch-body">
        <p className="publish-warn">
          发布后，任何拿到链接的人都可以<strong>浏览这个系统的全部数据</strong>
          （只读，不能修改）。请确认表中不含不便公开的信息。
        </p>
        <label className="publish-field" htmlFor="publish-slug-new">
          自定义链接标识
        </label>
        <SlugInput id="publish-slug-new" value={slugInput} onChange={setSlugInput} />
        <p className="publish-hint">
          2-40 位小写字母、数字或连字符；需以字母或数字开头结尾（大写会自动转小写）。
        </p>
        {invalid && <p className="patch-error" role="alert">{invalid}</p>}
      </div>
      {error && <p className="patch-error" role="alert">{error}</p>}
      <div className="patch-foot">
        <button
          type="button"
          className="btn btn-secondary"
          onClick={onClose}
          disabled={busy}
        >
          取消
        </button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => void handleCreate()}
          disabled={busy || invalid !== null || !slugInput.trim()}
        >
          {busy ? '发布中…' : '确认发布'}
        </button>
      </div>
    </DialogShell>
  )
}

function PublishedForm({
  appKey,
  publication,
  onClose,
}: {
  appKey: string
  publication: Publication
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [slugInput, setSlugInput] = useState(publication.slug)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const publicUrl = buildPublicUrl(publication.slug)
  const invalid = slugInput.trim() ? validateSlug(slugInput) : null
  const slugChanged = normalizeSlug(slugInput) !== publication.slug
  const modelStale = publication.current_version > publication.model_version

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['publication', appKey] })
  }

  const handleCopy = async () => {
    await copyText(publicUrl)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 2000)
  }

  const handleSaveChanges = async () => {
    const changes: { slug?: string; republish?: boolean } = {}
    if (slugChanged) {
      const problem = validateSlug(slugInput)
      if (problem) {
        setError(problem)
        return
      }
      changes.slug = normalizeSlug(slugInput)
    }
    if (modelStale) changes.republish = true
    if (changes.slug === undefined && !changes.republish) {
      setError('没有需要保存的改动')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await updatePublication(appKey, changes)
      await refresh()
    } catch (err) {
      setError(failMessage(err, '保存失败，请稍后重试'))
      setBusy(false)
    }
  }

  const handleRepublishOnly = async () => {
    setBusy(true)
    setError(null)
    try {
      await updatePublication(appKey, { republish: true })
      await refresh()
    } catch (err) {
      setError(failMessage(err, '更新发布失败，请稍后重试'))
      setBusy(false)
    }
  }

  const handleUnpublish = async () => {
    const confirmed = window.confirm(
      '取消发布后，现有公开链接将立即失效（链接标识会为你保留，他人无法占用）。确定下线吗？',
    )
    if (!confirmed) return
    setBusy(true)
    setError(null)
    try {
      await unpublishApp(appKey)
      await refresh()
    } catch (err) {
      setError(failMessage(err, '下线失败，请稍后重试'))
      setBusy(false)
    }
  }

  return (
    <DialogShell onClose={onClose} busy={busy}>
      <div className="patch-body">
        {publication.public ? (
          <>
            <p className="publish-ok">系统已发布，访客打开此链接即可只读浏览：</p>
            <div className="publish-link-box">
              <span className="publish-link-text">{publicUrl}</span>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => void handleCopy()}
              >
                {copied ? '已复制' : '复制链接'}
              </button>
              <a
                className="btn btn-secondary"
                href={`/p/${encodeURIComponent(publication.slug)}`}
                target="_blank"
                rel="noreferrer"
              >
                打开
              </a>
            </div>
            <p className="publish-hint">
              访客看到的是发布时的系统结构（v{publication.model_version}）；之后用「一句话修改」做出的改动，
              需要点「更新发布」访客才能看到，业务数据则始终实时可见。
            </p>
            {modelStale && (
              <p className="publish-update-hint">
                系统结构已有新版本（当前 v{publication.current_version}，发布中 v
                {publication.model_version}），保存更改后访客才会看到。
              </p>
            )}
            <label className="publish-field" htmlFor="publish-slug-edit">
              修改链接标识（旧链接会立即失效）
            </label>
            <SlugInput
              id="publish-slug-edit"
              value={slugInput}
              onChange={setSlugInput}
            />
            {invalid && <p className="patch-error" role="alert">{invalid}</p>}
          </>
        ) : (
          <>
            <p className="publish-warn">
              该系统当前处于<strong>已下线</strong>状态，公开链接无法访问。重新发布会沿用链接标识
              {publication.slug}，并钉住最新的系统结构（当前 v{publication.current_version}
              ，下线前为 v{publication.model_version}）。
            </p>
            <div className="publish-link-box">
              <span className="publish-link-text is-off">{publicUrl}</span>
            </div>
          </>
        )}
      </div>

      {error && <p className="patch-error" role="alert">{error}</p>}

      <div className="patch-foot">
        {publication.public ? (
          <>
            <button
              type="button"
              className="btn btn-danger"
              onClick={() => void handleUnpublish()}
              disabled={busy}
            >
              取消发布
            </button>
            {modelStale && !slugChanged && (
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => void handleRepublishOnly()}
                disabled={busy}
              >
                仅更新发布
              </button>
            )}
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void handleSaveChanges()}
              disabled={busy || invalid !== null}
            >
              {busy ? '保存中…' : '保存更改'}
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={onClose}
              disabled={busy}
            >
              关闭
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void handleRepublishOnly()}
              disabled={busy}
            >
              {busy ? '发布中…' : '重新发布'}
            </button>
          </>
        )}
      </div>
    </DialogShell>
  )
}

export function PublishDialog({ open, appKey, onClose }: PublishDialogProps) {
  const { data, isLoading } = useQuery({
    queryKey: ['publication', appKey],
    queryFn: () => fetchPublication(appKey),
    enabled: open,
  })

  if (!open) return null
  if (isLoading || data === undefined) {
    return (
      <DialogShell onClose={onClose} busy={false}>
        <p className="muted">正在读取发布状态…</p>
      </DialogShell>
    )
  }
  if (!data) {
    return <CreateForm appKey={appKey} onClose={onClose} />
  }
  return <PublishedForm appKey={appKey} publication={data} onClose={onClose} />
}
