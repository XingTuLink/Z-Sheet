import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { findViewForEntity, getEntity, getField } from '../resolve'
import type {
  BusinessField,
  Entity,
  FieldValue,
  FormView as FormViewModel,
  RecordRow,
} from '../types'
import { useRuntime } from '../useRuntime'

function FormControl({
  field,
  value,
  invalid,
  onChange,
}: {
  field: BusinessField
  value: string
  invalid: boolean
  onChange: (value: string) => void
}) {
  const common = {
    id: `field-${field.key}`,
    value,
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
  const { bootstrap, records, addRecord } = useRuntime()
  const navigate = useNavigate()
  const model = bootstrap.model

  const maybeEntity = getEntity(model, view.entity)
  const [values, setValues] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})

  if (!maybeEntity) {
    return (
      <section className="view">
        <div className="card">
          <h1>模型配置有误</h1>
          <p className="muted">表单视图 {view.key} 引用了不存在的实体。</p>
        </div>
      </section>
    )
  }

  // Const alias after the guard: narrowing stays valid inside closures.
  const entity: Entity = maybeEntity
  const fields = (view.fields ?? entity.fields.map((field) => field.key))
    .map((key) => getField(entity, key))
    .filter((field): field is BusinessField => Boolean(field))
  const listView = findViewForEntity(model, view.entity, 'list')
  const cancelTarget = listView ? `/views/${listView.key}` : '/'

  function setField(key: string, raw: string) {
    setValues((previous) => ({ ...previous, [key]: raw }))
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const nextErrors: Record<string, string> = {}
    const row: RecordRow = {}

    for (const field of fields) {
      const raw = (values[field.key] ?? '').trim()
      if (raw === '') {
        if (field.key === entity.key_field) {
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

    const keyRaw = (values[entity.key_field] ?? '').trim()
    const duplicated =
      keyRaw !== '' &&
      (records[entity.key] ?? []).some(
        (item) => String(item[entity.key_field] ?? '') === keyRaw,
      )
    if (duplicated) {
      nextErrors[entity.key_field] = `${entity.name}标识已存在`
    }

    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors)
      return
    }

    addRecord(entity.key, row)
    navigate(listView ? `/views/${listView.key}` : '/')
  }

  return (
    <section className="view form-view">
      <header className="view-header">
        <h1>{view.title}</h1>
      </header>
      <div className="notice">
        演示环境：提交的记录仅保存在当前页面中，刷新后恢复为初始数据。
      </div>
      <form className="card form-card" onSubmit={handleSubmit} noValidate>
        {fields.map((field) => (
          <div className="form-item" key={field.key}>
            <label htmlFor={`field-${field.key}`}>
              {field.name}
              {field.key === entity.key_field && (
                <span className="required" aria-label="必填">
                  {' '}
                  *
                </span>
              )}
            </label>
            <FormControl
              field={field}
              value={values[field.key] ?? ''}
              invalid={Boolean(errors[field.key])}
              onChange={(raw) => setField(field.key, raw)}
            />
            {errors[field.key] && (
              <p className="field-error">{errors[field.key]}</p>
            )}
          </div>
        ))}
        <div className="form-actions">
          <button type="submit" className="btn btn-primary">
            保存
          </button>
          <Link to={cancelTarget} className="btn btn-secondary">
            取消
          </Link>
        </div>
      </form>
    </section>
  )
}
