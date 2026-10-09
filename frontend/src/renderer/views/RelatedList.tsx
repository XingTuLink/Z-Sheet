import { useMemo, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'

import { resolveAppRoute } from '../appRoute'
import { formatCell } from '../format'
import { selectRelatedRows } from '../relationQuery'
import { findViewForEntity, getEntity, getField } from '../resolve'
import { useRuntime } from '../useRuntime'
import type {
  BusinessField,
  DetailBlock,
  Entity,
  Link as ModelLink,
  RecordRow,
} from '../types'

const INITIAL_LIMIT = 5
const LIMIT_STEP = 5

function childColumns(child: Entity, preferredKeys: string[]): BusinessField[] {
  return preferredKeys
    .map((key) => getField(child, key))
    .filter((field): field is BusinessField => Boolean(field))
}

export function RelatedList({
  block,
  parentEntity,
  parentRow,
}: {
  block: DetailBlock
  parentEntity: Entity
  parentRow: RecordRow
}) {
  const { bootstrap, records } = useRuntime()
  const location = useLocation()
  const { basePath } = resolveAppRoute(location.pathname)
  const [limit, setLimit] = useState(INITIAL_LIMIT)

  const model = bootstrap.model
  const link: ModelLink | undefined = model.links.find((item) => item.key === block.link)

  const view = useMemo(() => {
    if (!link) return null
    const childKey =
      link.from_ === parentEntity.key
        ? link.to
        : link.from_
    const child = getEntity(model, childKey)
    const childList = child
      ? (findViewForEntity(model, child.key, 'list') as
          | Extract<(typeof model.views)[number], { kind: 'list' }>
          | undefined)
      : undefined
    const childDetail = child
      ? findViewForEntity(model, child.key, 'detail')
      : undefined
    if (!child) return null
    const allRows = records[child.key] ?? []
    const total = selectRelatedRows({
      rows: allRows,
      currentEntity: parentEntity,
      parentRow,
      link,
      childEntity: child,
      sorts: childList?.sorts ?? [],
    })
    const visible = total.slice(0, limit)
    return {
      child,
      columns: childColumns(child, childList?.columns ?? [child.key_field]),
      detailKey: childDetail?.key,
      total,
      visible,
    }
  }, [link, limit, model, parentEntity, parentRow, records])

  if (!link || !view) {
    return (
      <div className="card placeholder-card">
        <h2>{block.title ?? '关联数据'}</h2>
        <p className="muted">关联配置不存在。</p>
      </div>
    )
  }

  const { child, columns, detailKey, total, visible } = view
  const title = block.title ?? `关联${child.name}`

  return (
    <div className="card related-card">
      <div className="related-head">
        <h2>{title}</h2>
        <span className="related-count">{total.length} 条</span>
      </div>
      {total.length === 0 ? (
        <p className="muted related-empty">暂无关联的{child.name}</p>
      ) : (
        <>
          <table className="grid related-grid">
            <thead>
              <tr>
                {columns.map((field) => (
                  <th key={field.key}>{field.name}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => {
                const rowKey = String(row[child.key_field] ?? '')
                return (
                  <tr key={rowKey}>
                    {columns.map((field) => {
                      const value = row[field.key]
                      return (
                        <td key={field.key}>
                          {field.key === child.key_field && detailKey ? (
                            <Link
                              className="row-link"
                              to={`${basePath}/views/${detailKey}?id=${encodeURIComponent(rowKey)}`}
                            >
                              {formatCell(field, value)}
                            </Link>
                          ) : field.type === 'enum' && value ? (
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
          {total.length > visible.length && (
            <div className="related-more">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setLimit((value) => value + LIMIT_STEP)}
              >
                显示更多（剩余 {total.length - visible.length} 条）
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
