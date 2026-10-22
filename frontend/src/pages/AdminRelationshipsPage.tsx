// Visual Relationship Editor: add, adjust and delete links between entities.
// Every change goes through preview -> confirm, so it lands as a new model
// version with the same validation and snapshot rules as an NL patch.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useLocation } from 'react-router-dom'

import { PatchApiError, type PatchPreview } from '../api/patch'
import {
  confirmRelationship,
  previewRelationship,
  rejectRelationship,
  type RelationshipInput,
  type RelationshipType,
} from '../api/relationships'
import { fetchBootstrap } from '../api/runtime'
import { resolveAppRoute } from '../renderer/appRoute'
import type { Entity, Link as ModelLink } from '../renderer/types'

const LINK_TYPE_LABELS: Record<RelationshipType, string> = {
  one_to_many: '一对多',
  one_to_one: '一对一',
}

type FormMode = { kind: 'add' } | { kind: 'edit'; link: ModelLink }

function messageOf(error: unknown): string {
  if (error instanceof PatchApiError) {
    if (error.status === 403) return '只有编辑者或所有者可以调整关联。'
    return error.message
  }
  return '请求失败，请稍后重试。'
}

export function AdminRelationshipsPage() {
  const { appKey, isDemo } = resolveAppRoute(useLocation().pathname)
  const queryClient = useQueryClient()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['bootstrap', appKey],
    queryFn: () => fetchBootstrap(appKey),
    retry: false,
    enabled: !isDemo,
  })

  const [mode, setMode] = useState<FormMode | null>(null)
  const [fromKey, setFromKey] = useState('')
  const [toKey, setToKey] = useState('')
  const [linkType, setLinkType] = useState<RelationshipType>('one_to_many')
  const [onFrom, setOnFrom] = useState('')
  const [onTo, setOnTo] = useState('')
  const [preview, setPreview] = useState<PatchPreview | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  if (isDemo) {
    return (
      <div className="card admin-placeholder">
        <p>演示系统不支持管理后台。</p>
      </div>
    )
  }
  if (isLoading) return <p className="muted">正在加载关联关系…</p>
  if (isError || !data) {
    return (
      <div className="card admin-placeholder">
        <p>无法加载模型结构。</p>
      </div>
    )
  }

  const { model } = data
  const entities = model.entities
  const entityByKey = (key: string): Entity | undefined =>
    entities.find((entity) => entity.key === key)
  const fieldName = (entityKey: string, fieldKey: string): string =>
    entityByKey(entityKey)?.fields.find((field) => field.key === fieldKey)?.name ??
    fieldKey

  function startAdd() {
    const first = entities[0]
    const second = entities[1] ?? first
    setMode({ kind: 'add' })
    setFromKey(first?.key ?? '')
    setToKey(second?.key ?? '')
    setOnFrom(first?.key_field ?? '')
    setOnTo(second?.key_field ?? '')
    setLinkType('one_to_many')
    setPreview(null)
    setError('')
    setMessage('')
  }

  function startEdit(link: ModelLink) {
    setMode({ kind: 'edit', link })
    setFromKey(link.from_)
    setToKey(link.to)
    setLinkType(link.type)
    setOnFrom(link.on.from_)
    setOnTo(link.on.to)
    setPreview(null)
    setError('')
    setMessage('')
  }

  function changeFrom(next: string) {
    setFromKey(next)
    setOnFrom(entityByKey(next)?.key_field ?? '')
  }

  function changeTo(next: string) {
    setToKey(next)
    setOnTo(entityByKey(next)?.key_field ?? '')
  }

  async function requestPreview(input: RelationshipInput) {
    setBusy(true)
    setError('')
    try {
      setPreview(await previewRelationship(appKey, input))
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setBusy(false)
    }
  }

  function handlePreview() {
    if (!mode) return
    const input: RelationshipInput =
      mode.kind === 'add'
        ? {
            action: 'add',
            from: fromKey,
            to: toKey,
            type: linkType,
            on: { from: onFrom, to: onTo },
          }
        : {
            action: 'update',
            link_key: mode.link.key,
            type: linkType,
            on: { from: onFrom, to: onTo },
          }
    void requestPreview(input)
  }

  function handleDelete(link: ModelLink) {
    const source = entityByKey(link.from_)?.name ?? link.from_
    const target = entityByKey(link.to)?.name ?? link.to
    if (!window.confirm(`确定删除「${source}」→「${target}」的关联吗？`)) return
    setMode(null)
    setMessage('')
    void requestPreview({ action: 'delete', link_key: link.key })
  }

  async function handleConfirm() {
    if (!preview) return
    setBusy(true)
    setError('')
    try {
      await confirmRelationship(appKey, preview.session_id)
      await queryClient.invalidateQueries({ queryKey: ['bootstrap', appKey] })
      await queryClient.invalidateQueries({ queryKey: ['app-overview', appKey] })
      setMessage('关联调整已应用到新版本。')
      setPreview(null)
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setBusy(false)
    }
  }

  async function handleCancel() {
    if (!preview) return
    const session = preview.session_id
    setPreview(null)
    try {
      await rejectRelationship(appKey, session)
    } catch {
      // A rejected preview simply expires; nothing else to clean up.
    }
  }

  const fromEntity = entityByKey(fromKey)
  const toEntity = entityByKey(toKey)

  return (
    <section className="admin-page">
      <div className="admin-page-head">
        <div>
          <h1>关联关系</h1>
          <p className="muted">
            当前版本 v{data.version} · 共 {model.links.length} 条关联
          </p>
        </div>
        {!mode && !preview && (
          <button type="button" className="btn btn-primary" onClick={startAdd}>
            新增关联
          </button>
        )}
      </div>

      {error && <p className="field-error">{error}</p>}
      {message && <p className="admin-hint">{message}</p>}

      {mode && (
        <div className="card admin-panel">
          <h2>{mode.kind === 'add' ? '新增关联' : '调整关联'}</h2>
          <div className="rel-form">
            <label className="rel-field">
              <span>起点实体</span>
              <select
                className="control"
                value={fromKey}
                disabled={mode.kind === 'edit'}
                onChange={(event) => changeFrom(event.target.value)}
              >
                {entities.map((entity) => (
                  <option key={entity.key} value={entity.key}>
                    {entity.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="rel-field">
              <span>终点实体</span>
              <select
                className="control"
                value={toKey}
                disabled={mode.kind === 'edit'}
                onChange={(event) => changeTo(event.target.value)}
              >
                {entities.map((entity) => (
                  <option key={entity.key} value={entity.key}>
                    {entity.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="rel-field">
              <span>关联类型</span>
              <select
                className="control"
                value={linkType}
                onChange={(event) =>
                  setLinkType(event.target.value as RelationshipType)
                }
              >
                <option value="one_to_many">一对多</option>
                <option value="one_to_one">一对一</option>
              </select>
            </label>
            <label className="rel-field">
              <span>起点匹配字段</span>
              <select
                className="control"
                value={onFrom}
                onChange={(event) => setOnFrom(event.target.value)}
              >
                {(fromEntity?.fields ?? []).map((field) => (
                  <option key={field.key} value={field.key}>
                    {field.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="rel-field">
              <span>终点匹配字段</span>
              <select
                className="control"
                value={onTo}
                onChange={(event) => setOnTo(event.target.value)}
              >
                {(toEntity?.fields ?? []).map((field) => (
                  <option key={field.key} value={field.key}>
                    {field.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {fromKey === toKey && (
            <p className="admin-hint">起点与终点相同即为自关联，两个匹配字段需不同。</p>
          )}
          <div className="rel-actions">
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy}
              onClick={handlePreview}
            >
              预览修改
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={busy}
              onClick={() => setMode(null)}
            >
              取消
            </button>
          </div>
        </div>
      )}

      {preview && (
        <div className="card admin-panel">
          <h2>修改预览</h2>
          <p className="rel-summary">{preview.changes[0]?.summary}</p>
          {preview.notes.length > 0 && (
            <ul className="rel-notes">
              {preview.notes.map((note, index) => (
                <li key={`${note}-${index}`}>{note}</li>
              ))}
            </ul>
          )}
          {preview.requires_confirmation && (
            <p className="admin-hint">该操作会删除关联及其详情页区块，确认后生效。</p>
          )}
          <div className="rel-actions">
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy}
              onClick={handleConfirm}
            >
              确认应用
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={busy}
              onClick={handleCancel}
            >
              放弃
            </button>
          </div>
        </div>
      )}

      <div className="card admin-panel">
        <h2>现有关系</h2>
        {model.links.length === 0 ? (
          <p className="muted">当前模型没有实体关联。</p>
        ) : (
          <table className="admin-table">
            <thead>
              <tr>
                <th>从</th>
                <th>到</th>
                <th>类型</th>
                <th>匹配字段</th>
                <th className="admin-num">操作</th>
              </tr>
            </thead>
            <tbody>
              {model.links.map((link) => (
                <tr key={link.key}>
                  <td>{entityByKey(link.from_)?.name ?? link.from_}</td>
                  <td>
                    {entityByKey(link.to)?.name ?? link.to}
                    {link.from_ === link.to && (
                      <span className="admin-field-key">自关联</span>
                    )}
                  </td>
                  <td>{LINK_TYPE_LABELS[link.type] ?? link.type}</td>
                  <td>
                    {fieldName(link.from_, link.on.from_)} →{' '}
                    {fieldName(link.to, link.on.to)}
                  </td>
                  <td className="admin-num">
                    <span className="row-actions">
                      <button
                        type="button"
                        className="btn-mini"
                        onClick={() => startEdit(link)}
                      >
                        编辑
                      </button>
                      <button
                        type="button"
                        className="btn-mini btn-danger"
                        onClick={() => handleDelete(link)}
                      >
                        删除
                      </button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}
