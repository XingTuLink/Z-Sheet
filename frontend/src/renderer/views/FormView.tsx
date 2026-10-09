import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'

import { RecordApiError } from '../../api/runtime'
import { resolveAppRoute } from '../appRoute'
import { findViewForEntity, getEntity, getField } from '../resolve'
import type {
  BusinessField,
  FieldValue,
  FormView as FormViewModel,
  RecordRow,
} from '../types'
import { useRuntime } from '../useRuntime'

function stringifyValue(value: FieldValue | undefined): string {
  if (value === null || value === undefined) return ''
  return String(value)
}

function FormControl({
  field,
  value,
  invalid,
  disabled,
  onChange,
}: {
  field: BusinessField
  value: string
  invalid: boolean
  disabled: boolean
  onChange: (value: string) => void
}) {
  const common = {
    id: `field-${field.key}`,
    value,
    disabled,
    className: invalid ? 'control invalid' : 'control',
    onChange: (event: { target: { value: string } }) => onChange(event.target.value),
  }

  if (field.type === 'enum') {
    return (
      <select {...common}>
        <option value="">请选择</option>
        {(field.values ?? []).map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    )
  }
  if (field.type === 'date') {
    return <input type="date" {...common} />
  }
  if (field.type === 'number') {
    return <input type="number" step="any" {...common} />
  }
  if (field.type === 'money') {
    return <input type="number" step="0.01" {...common} />
  }
  return <input type="text" {...common} />
}

export function FormView({ view }: { view: FormViewModel }) {
  const { bootstrap, records, saveRecord } = useRuntime()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { basePath } = resolveAppRoute(useLocation().pathname)
  const model = bootstrap.model
  const id = searchParams.get('id') ?? ''
  const mode = id ? 'update' : 'create'

  const entity = getEntity(model, view.entity)
  const existingRow =
    entity && id
      ? (records[entity.key] ?? []).find(
          (item) => String(item[entity.key_field] ?? '') === id,
        )
      : undefined
  const fields = useMemo(
    () =>
      entity
        ? (view.fields ?? entity.fields.map((field) => field.key))
            .map((key) => getField(entity, key))
            .filter((field): field is BusinessField => Boolean(field))
        : [],
    [entity, view.fields],
  )

  const [values, setValues] = useState<Record<string, string>>(() => {
    const next: Record<string, string> = {}
    if (entity && existingRow) {
      for (const field of fields) next[field.key] = stringifyValue(existingRow[field.key])
    }
    return next
  })
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitError, setSubmitError] = useState('')
  const [saving, setSaving] = useState(false)

  if (!entity) {
    return (
      <section className="view">
        <div className="card">
          <h1>模型配置有误</h1>
          <p className="muted">表单视图 {view.key} 引用了不存在的实体。</p>
        </div>
      </section>
    )
  }

  if (mode === 'update' && !existingRow) {
    const listView = findViewForEntity(model, view.entity, 'list')
    return (
      <section className="view">
        <div className="card">
          <h1>记录不存在</h1>
          <p className="muted">
            未找到标识为「{id}」的{entity.name}。
            {listView && (
              <>
                {' '}
                回到
                <Link to={`${basePath}/views/${listView.key}`}>{listView.title}</Link>
                。
              </>
            )}
          </p>
        </div>
      </section>
    )
  }

  const listView = findViewForEntity(model, view.entity, 'list')
  const cancelTarget = listView ? `${basePath}/views/${listView.key}` : basePath
  const formTitle = mode === 'update' ? `编辑${entity.name}` : view.title

  function setField(key: string, raw: string) {
    setValues((previous) => ({ ...previous, [key]: raw }))
    setErrors((previous) => {
      if (!previous[key]) return previous
      const next = { ...previous }
      delete next[key]
      return next
    })
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (saving) return
    const nextErrors: Record<string, string> = {}
    const row: RecordRow = {}

    for (const field of fields) {
      const raw = (values[field.key] ?? '').trim()
      if (raw === '') {
        if (field.key === entity!.key_field) {
          nextErrors[field.key] = '该项为必填项'
        } else {
          row[field.key] = null
        }
        continue
      }
      if (field.type === 'number' || field.type === 'money') {
        const numeric = Number(raw)
        if (!Number.isFinite(numeric)) {
          nextErrors[field.key] = '请输入有效数字'
        } else {
          row[field.key] = numeric as FieldValue
        }
      } else {
        row[field.key] = raw
      }
    }

    const keyRaw = (values[entity!.key_field] ?? '').trim()
    if (
      mode === 'create' &&
      keyRaw !== '' &&
      (records[entity!.key] ?? []).some(
        (item) => String(item[entity!.key_field] ?? '') === keyRaw,
      )
    ) {
      nextErrors[entity!.key_field] = `${entity!.name}标识已存在`
    }

    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors)
      setSubmitError('')
      return
    }

    setSaving(true)
    setSubmitError('')
    try {
      await saveRecord(entity!.key, row, mode)
      navigate(listView ? `${basePath}/views/${listView.key}` : basePath)
    } catch (error) {
      const message =
        error instanceof RecordApiError
          ? error.message
          : '保存失败，请稍后重试'
      if (error instanceof RecordApiError && error.status === 409) {
        setErrors({ [entity!.key_field]: message })
      } else {
        setSubmitError(message)
      }
      setSaving(false)
    }
  }

  return (
    <section className="view form-view">
      <header className="view-header">
        <h1>{formTitle}</h1>
      </header>
      <div className="notice">
        {mode === 'update'
          ? '保存后将立即更新业务数据，刷新页面仍然保留。'
          : '保存后将立即写入业务数据，刷新页面仍然保留。'}
      </div>
      {submitError && <div className="notice notice-error">{submitError}</div>}
      <form className="card form-card" onSubmit={handleSubmit} noValidate>
        {fields.map((field) => {
          const keyLocked = mode === 'update' && field.key === entity.key_field
          return (
            <div className="form-item" key={field.key}>
              <label htmlFor={`field-${field.key}`}>
                {field.name}
                {field.key === entity.key_field && (
                  <span className="required" aria-label="必填">
                    {' '}
                    *
                  </span>
                )}
                {keyLocked && <span className="muted lock-hint">（标识不可修改）</span>}
              </label>
              <FormControl
                field={field}
                value={values[field.key] ?? ''}
                invalid={Boolean(errors[field.key])}
                disabled={keyLocked}
                onChange={(raw) => setField(field.key, raw)}
              />
              {errors[field.key] && (
                <p className="field-error">{errors[field.key]}</p>
              )}
            </div>
          )
        })}
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? '保存中...' : '保存'}
          </button>
          <Link to={cancelTarget} className="btn btn-secondary">
            取消
          </Link>
        </div>
      </form>
    </section>
  )
}
