// Global model settings: one effective connection at a time, switchable, with
// the .env fallback shown explicitly. Keys are only sent when changed and only
// rendered masked afterwards.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import {
  activateLlmProfile,
  createLlmProfile,
  deleteLlmProfile,
  fetchLlmProfiles,
  fetchLlmStatus,
  testLlmConnection,
  updateLlmProfile,
  type LlmProfile,
  type LlmProfileInput,
  type LlmTestResult,
} from '../api/llm'
import { BrandLockup, CompanyFooter } from '../components/Brand'

interface FormState {
  name: string
  base_url: string
  api_key: string
  model: string
  timeout: string
  enable_thinking: boolean
  max_retries: string
  activate: boolean
}

const EMPTY_FORM: FormState = {
  name: '',
  base_url: '',
  api_key: '',
  model: '',
  timeout: '60',
  enable_thinking: true,
  max_retries: '1',
  activate: true,
}

function formFromProfile(profile: LlmProfile): FormState {
  return {
    name: profile.name,
    base_url: profile.base_url,
    api_key: '',
    model: profile.model,
    timeout: String(profile.timeout),
    enable_thinking: profile.enable_thinking,
    max_retries: String(profile.max_retries),
    activate: profile.is_active,
  }
}

function hostIsLocal(baseUrl: string): boolean {
  try {
    const host = new URL(baseUrl).hostname.toLowerCase()
    return host === 'localhost' || host === '127.0.0.1' || host === '::1'
  } catch {
    return false
  }
}

function validate(form: FormState, editing: LlmProfile | null): string | null {
  if (!form.name.trim()) return '请填写配置名称'
  if (!/^https?:\/\/.+/i.test(form.base_url.trim())) {
    return '接口地址必须是 http:// 或 https:// 开头的完整地址'
  }
  if (!form.model.trim()) return '请填写模型名'
  // Editing may keep an already stored key without retyping it.
  if (!hostIsLocal(form.base_url) && !form.api_key && !editing) {
    return '公有云接口需要填写 API Key'
  }
  const timeout = Number(form.timeout)
  if (!Number.isFinite(timeout) || timeout < 1 || timeout > 600) {
    return '超时时间需在 1-600 秒之间'
  }
  const retries = Number(form.max_retries)
  if (!Number.isInteger(retries) || retries < 0 || retries > 5) {
    return '重试次数需在 0-5 之间'
  }
  return null
}

export function SettingsPage() {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<LlmProfile | 'new' | null>(null)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [testResult, setTestResult] = useState<LlmTestResult | null>(null)

  const statusQuery = useQuery({
    queryKey: ['llm-status'],
    queryFn: fetchLlmStatus,
  })
  const profilesQuery = useQuery({
    queryKey: ['llm-profiles'],
    queryFn: fetchLlmProfiles,
  })

  const profiles = profilesQuery.data ?? []
  const current = editing === 'new' ? null : editing

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['llm-status'] }),
      queryClient.invalidateQueries({ queryKey: ['llm-profiles'] }),
    ])
  }

  const openNew = () => {
    setEditing('new')
    setForm(EMPTY_FORM)
    setError('')
    setNotice('')
    setTestResult(null)
  }

  const openEdit = (profile: LlmProfile) => {
    setEditing(profile)
    setForm(formFromProfile(profile))
    setError('')
    setNotice('')
    setTestResult(null)
  }

  const closeForm = () => {
    setEditing(null)
    setError('')
    setNotice('')
    setTestResult(null)
  }

  const toPayload = (): LlmProfileInput => ({
    name: form.name.trim(),
    base_url: form.base_url.trim().replace(/\/+$/, ''),
    api_key: form.api_key,
    model: form.model.trim(),
    timeout: Number(form.timeout),
    enable_thinking: form.enable_thinking,
    max_retries: Number(form.max_retries),
    activate: form.activate,
  })

  const onTest = async () => {
    const invalid = validate(form, current)
    if (invalid) {
      setError(invalid)
      return
    }
    setBusy(true)
    setError('')
    setTestResult(null)
    try {
      const payload = toPayload()
      setTestResult(
        await testLlmConnection({
          ...(current && !form.api_key ? { profile_id: current.id } : {}),
          base_url: payload.base_url,
          model: payload.model,
          ...(form.api_key ? { api_key: form.api_key } : {}),
          timeout: 15,
        }),
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : '测试连接失败')
    } finally {
      setBusy(false)
    }
  }

  const onSave = async () => {
    const invalid = validate(form, current)
    if (invalid) {
      setError(invalid)
      return
    }
    setBusy(true)
    setError('')
    try {
      const payload = toPayload()
      if (current) {
        const changes: Partial<LlmProfileInput> = {
          name: payload.name,
          base_url: payload.base_url,
          model: payload.model,
          timeout: payload.timeout,
          enable_thinking: payload.enable_thinking,
          max_retries: payload.max_retries,
          activate: payload.activate,
        }
        // An empty box means "keep the stored key"; only send what was typed.
        if (form.api_key) changes.api_key = form.api_key
        await updateLlmProfile(current.id, changes)
      } else {
        await createLlmProfile(payload)
      }
      await refresh()
      setNotice(current ? '配置已更新。' : '配置已保存。')
      setEditing(null)
      setTestResult(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  const onActivate = async (profile: LlmProfile) => {
    setBusy(true)
    setError('')
    try {
      await activateLlmProfile(profile.id)
      await refresh()
      setNotice(`已切换到「${profile.name}」，下一次模型调用立即生效。`)
    } catch (err) {
      setError(err instanceof Error ? err.message : '切换失败')
    } finally {
      setBusy(false)
    }
  }

  const onDelete = async (profile: LlmProfile) => {
    if (!window.confirm(`确定删除配置「${profile.name}」吗？`)) return
    setBusy(true)
    setError('')
    try {
      await deleteLlmProfile(profile.id)
      await refresh()
      setNotice('配置已删除。')
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败')
    } finally {
      setBusy(false)
    }
  }

  const status = statusQuery.data

  return (
    <div className="wb-page">
      <header className="wb-header">
        <Link to="/" className="brand brand-link">
          <BrandLockup logoSize={28} />
        </Link>
        <div className="wb-header-actions">
          <Link to="/" className="btn btn-secondary">
            返回工作台
          </Link>
        </div>
      </header>

      <main className="settings-body">
        <div className="wb-title-row">
          <div>
            <h1>模型设置</h1>
            <p className="muted wb-sub">
              配置用于理解表格的大模型。保存或切换后立即生效，无需重启。
            </p>
          </div>
        </div>

        <div className="card settings-status">
          <div className="settings-status-head">
            <h2>当前生效</h2>
            {status && (
              <span
                className={`settings-source ${status.source === 'database' ? 'is-db' : 'is-env'}`}
              >
                {status.source === 'database' ? '库内配置' : '环境变量回退'}
              </span>
            )}
          </div>
          {statusQuery.isLoading && <p className="muted">正在读取…</p>}
          {statusQuery.isError && <p className="muted">无法读取当前配置。</p>}
          {status && (
            <>
              <dl className="settings-grid">
                <div>
                  <dt>接口地址</dt>
                  <dd>{status.base_url || '未配置'}</dd>
                </div>
                <div>
                  <dt>模型</dt>
                  <dd>{status.model || '未配置'}</dd>
                </div>
                <div>
                  <dt>API Key</dt>
                  <dd>{status.api_key}</dd>
                </div>
                <div>
                  <dt>可用状态</dt>
                  <dd>{status.available ? '可以调用' : '不可用（缺少 Key 或模型）'}</dd>
                </div>
              </dl>
              {status.source === 'env' && (
                <p className="settings-hint">
                  当前没有启用中的库内配置，正在使用 .env 里的 ZSHEET_LLM_* 设置。
                  新增配置并启用后，库内配置优先。
                </p>
              )}
            </>
          )}
        </div>

        <div className="card settings-panel">
          <div className="admin-panel-head">
            <h2>保存的配置</h2>
            <button type="button" className="btn btn-primary" onClick={openNew}>
              新增配置
            </button>
          </div>

          {profiles.length === 0 ? (
            <p className="muted">
              还没有保存的配置。新增一套后，它会成为当前生效的模型。
            </p>
          ) : (
            <ul className="settings-list">
              {profiles.map((profile) => (
                <li key={profile.id} className="settings-item">
                  <div className="settings-item-main">
                    <div className="settings-item-title">
                      <strong>{profile.name}</strong>
                      {profile.is_active && (
                        <span className="settings-source is-db">使用中</span>
                      )}
                    </div>
                    <p className="muted">
                      {profile.base_url} · {profile.model} · {profile.api_key}
                    </p>
                  </div>
                  <div className="settings-item-actions">
                    {!profile.is_active && (
                      <button
                        type="button"
                        className="btn btn-secondary"
                        disabled={busy}
                        onClick={() => void onActivate(profile)}
                      >
                        启用
                      </button>
                    )}
                    <button
                      type="button"
                      className="btn btn-secondary"
                      disabled={busy}
                      onClick={() => openEdit(profile)}
                    >
                      编辑
                    </button>
                    <button
                      type="button"
                      className="btn btn-danger"
                      disabled={busy}
                      onClick={() => void onDelete(profile)}
                    >
                      删除
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {editing && (
          <div className="card settings-panel">
            <h2>{current ? `编辑「${current.name}」` : '新增配置'}</h2>
            <div className="settings-form">
              <label className="settings-field">
                <span>配置名称</span>
                <input
                  value={form.name}
                  maxLength={64}
                  placeholder="例如：DeepSeek 生产"
                  onChange={(event) =>
                    setForm({ ...form, name: event.target.value })
                  }
                />
              </label>
              <label className="settings-field">
                <span>接口地址</span>
                <input
                  value={form.base_url}
                  placeholder="https://api.deepseek.com 或 http://localhost:11434/v1"
                  onChange={(event) =>
                    setForm({ ...form, base_url: event.target.value })
                  }
                />
              </label>
              <label className="settings-field">
                <span>模型名</span>
                <input
                  value={form.model}
                  placeholder="例如：deepseek-chat"
                  onChange={(event) =>
                    setForm({ ...form, model: event.target.value })
                  }
                />
              </label>
              <label className="settings-field">
                <span>API Key</span>
                <input
                  type="password"
                  value={form.api_key}
                  autoComplete="off"
                  placeholder={
                    current
                      ? `已保存 ${current.api_key}，留空保持不变`
                      : '本地模型可留空'
                  }
                  onChange={(event) =>
                    setForm({ ...form, api_key: event.target.value })
                  }
                />
              </label>
              <label className="settings-field settings-field-short">
                <span>超时（秒）</span>
                <input
                  type="number"
                  min={1}
                  max={600}
                  value={form.timeout}
                  onChange={(event) =>
                    setForm({ ...form, timeout: event.target.value })
                  }
                />
              </label>
              <label className="settings-field settings-field-short">
                <span>失败重试</span>
                <input
                  type="number"
                  min={0}
                  max={5}
                  value={form.max_retries}
                  onChange={(event) =>
                    setForm({ ...form, max_retries: event.target.value })
                  }
                />
              </label>
              <label className="settings-check">
                <input
                  type="checkbox"
                  checked={form.enable_thinking}
                  onChange={(event) =>
                    setForm({ ...form, enable_thinking: event.target.checked })
                  }
                />
                <span>允许模型思考（推理型模型可开启）</span>
              </label>
              <label className="settings-check">
                <input
                  type="checkbox"
                  checked={form.activate}
                  onChange={(event) =>
                    setForm({ ...form, activate: event.target.checked })
                  }
                />
                <span>保存后立即启用</span>
              </label>
            </div>

            {testResult && (
              <div
                className={`notice settings-test ${testResult.status === 'ok' ? 'is-ok' : 'is-warn'}`}
              >
                <p>{testResult.detail}</p>
                {testResult.models.length > 0 && (
                  <p className="muted">
                    可用模型：{testResult.models.slice(0, 8).join('、')}
                    {testResult.models.length > 8 ? ' 等' : ''}
                  </p>
                )}
              </div>
            )}
            {error && <div className="notice error-notice">{error}</div>}
            {notice && <div className="notice settings-notice">{notice}</div>}

            <div className="settings-actions">
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy}
                onClick={() => void onSave()}
              >
                {busy ? '处理中…' : current ? '保存修改' : '保存配置'}
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                disabled={busy}
                onClick={() => void onTest()}
              >
                测试连接
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                disabled={busy}
                onClick={closeForm}
              >
                取消
              </button>
            </div>
          </div>
        )}

        {!editing && error && <div className="notice error-notice">{error}</div>}
        {!editing && notice && <div className="notice settings-notice">{notice}</div>}

        <p className="settings-hint">
          说明：API Key 只保存在本机数据库，接口仅返回掩码。没有启用任何库内配置时，
          系统继续使用 .env 中的 ZSHEET_LLM_* 设置。
        </p>
      </main>

      <CompanyFooter />
    </div>
  )
}
