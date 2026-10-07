import { Link } from 'react-router-dom'

import { formatCell } from '../format'
import { EmptyState, ModelError } from '../widgets'
import { findViewForEntity, getEntity, getField } from '../resolve'
import { useRuntime } from '../useRuntime'
import type { BusinessField, ListView as ListViewModel } from '../types'

export function ListView({ view }: { view: ListViewModel }) {
  const { bootstrap, records } = useRuntime()
  const model = bootstrap.model
  const entity = getEntity(model, view.entity)
  if (!entity) {
    return <ModelError>列表视图 {view.key} 引用了不存在的实体。</ModelError>
  }

  const rows = records[view.entity] ?? []
  const detailView = findViewForEntity(model, view.entity, 'detail')
  const formView = findViewForEntity(model, view.entity, 'form')
  const columns = view.columns
    .map((key) => ({ key, field: getField(entity, key) }))
    .filter((column): column is { key: string; field: BusinessField } =>
      Boolean(column.field),
    )

  return (
    <section className="view">
      <header className="view-header">
        <div>
          <h1>{view.title}</h1>
          <p className="view-sub">共 {rows.length} 条记录</p>
        </div>
        {formView && (
          <Link className="btn btn-primary" to={`/views/${formView.key}`}>
            新增{entity.name}
          </Link>
        )}
      </header>

      <div className="card grid-card">
        {rows.length === 0 ? (
          <EmptyState>暂无数据</EmptyState>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                {columns.map(({ key, field }) => (
                  <th key={key}>{field.name}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const rowKey = String(row[entity.key_field] ?? '')
                return (
                  <tr key={rowKey}>
                    {columns.map(({ key, field }) => {
                      const value = row[key]
                      if (key === entity.key_field && detailView) {
                        return (
                          <td key={key}>
                            <Link
                              className="row-link"
                              to={`/views/${detailView.key}?id=${encodeURIComponent(String(value ?? ''))}`}
                            >
                              {formatCell(field, value)}
                            </Link>
                          </td>
                        )
                      }
                      return (
                        <td key={key}>
                          {field.type === 'enum' && value ? (
                            <span className="tag">{formatCell(field, value)}</span>
                          ) : (
                            formatCell(field, value)
                          )}
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}
