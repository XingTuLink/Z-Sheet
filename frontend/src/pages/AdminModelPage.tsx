// Read-only model structure: entities, fields and links as generated. Editing
// happens through natural language in the business shell, not here.

import { useQuery } from '@tanstack/react-query'
import { useLocation } from 'react-router-dom'

import { fetchBootstrap } from '../api/runtime'
import { resolveAppRoute } from '../renderer/appRoute'
import type { FieldRole, FieldType } from '../renderer/types'

const TYPE_LABELS: Record<FieldType, string> = {
  string: '文本',
  number: '数字',
  money: '金额',
  date: '日期',
  enum: '枚举',
  phone: '电话',
}

const ROLE_LABELS: Record<FieldRole, string> = {
  identifier: '唯一标识',
  dimension: '维度',
  measure: '度量',
  time: '时间',
  enum: '分类',
  text: '文本',
}

const LINK_TYPE_LABELS: Record<'one_to_many' | 'one_to_one', string> = {
  one_to_many: '一对多',
  one_to_one: '一对一',
}

export function AdminModelPage() {
  const { appKey, isDemo } = resolveAppRoute(useLocation().pathname)
  const { data, isLoading, isError } = useQuery({
    queryKey: ['bootstrap', appKey],
    queryFn: () => fetchBootstrap(appKey),
    retry: false,
    enabled: !isDemo,
  })

  if (isDemo) {
    return (
      <div className="card admin-placeholder">
        <p>演示系统不支持管理后台。</p>
      </div>
    )
  }
  if (isLoading) return <p className="muted">正在加载模型结构…</p>
  if (isError || !data) {
    return (
      <div className="card admin-placeholder">
        <p>无法加载模型结构。</p>
      </div>
    )
  }

  const { model } = data
  const entityName = (key: string) =>
    model.entities.find((entity) => entity.key === key)?.name ?? key

  return (
    <section className="admin-page">
      <div className="admin-page-head">
        <div>
          <h1>模型结构</h1>
          <p className="muted">
            当前版本 v{data.version} · {model.entities.length} 个实体 ·{' '}
            {model.links.length} 条关联 · {model.metrics.length} 个指标
          </p>
        </div>
      </div>

      {model.entities.map((entity) => (
        <div key={entity.key} className="card admin-panel">
          <div className="admin-entity-head">
            <h2>{entity.name}</h2>
            <span className="muted">
              唯一标识：{entity.fields.find((field) => field.key === entity.key_field)?.name
                ?? entity.key_field}
              {entity.source?.sheet ? ` · 来源 ${entity.source.sheet}` : ''}
            </span>
          </div>
          <table className="admin-table">
            <thead>
              <tr>
                <th>字段</th>
                <th>类型</th>
                <th>角色</th>
                <th>取值</th>
              </tr>
            </thead>
            <tbody>
              {entity.fields.map((field) => (
                <tr key={field.key}>
                  <td>
                    {field.name}
                    <span className="admin-field-key">{field.key}</span>
                  </td>
                  <td>{TYPE_LABELS[field.type] ?? field.type}</td>
                  <td>{ROLE_LABELS[field.role] ?? field.role}</td>
                  <td className="admin-cell-wrap">
                    {field.values && field.values.length > 0
                      ? field.values.join('、')
                      : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}

      <div className="card admin-panel">
        <h2>关联关系</h2>
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
                <th className="admin-num">置信度</th>
              </tr>
            </thead>
            <tbody>
              {model.links.map((link) => {
                const from = model.entities.find((e) => e.key === link.from_)
                const to = model.entities.find((e) => e.key === link.to)
                const fromField = from?.fields.find((f) => f.key === link.on.from_)
                const toField = to?.fields.find((f) => f.key === link.on.to)
                return (
                  <tr key={link.key}>
                    <td>{from?.name ?? entityName(link.from_)}</td>
                    <td>
                      {to?.name ?? entityName(link.to)}
                      {link.from_ === link.to && (
                        <span className="admin-field-key">自关联</span>
                      )}
                    </td>
                    <td>{LINK_TYPE_LABELS[link.type] ?? link.type}</td>
                    <td>
                      {fromField?.name ?? link.on.from_} → {toField?.name ?? link.on.to}
                    </td>
                    <td className="admin-num">
                      {Math.round((link.confidence ?? 0) * 100)}%
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="card admin-panel">
        <h2>指标</h2>
        {model.metrics.length === 0 ? (
          <p className="muted">当前模型没有指标。</p>
        ) : (
          <table className="admin-table">
            <thead>
              <tr>
                <th>指标</th>
                <th>实体</th>
                <th>业务口径</th>
              </tr>
            </thead>
            <tbody>
              {model.metrics.map((metric) => (
                <tr key={metric.key}>
                  <td>{metric.name}</td>
                  <td>{entityName(metric.entity)}</td>
                  <td className="admin-cell-wrap">{metric.business_definition}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}
