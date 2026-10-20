import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'

import { fetchRelated } from '../../api/runtime'
import { resolveAppRoute } from '../appRoute'
import { formatCell } from '../format'
import { findViewForEntity, getEntity, getField } from '../resolve'
import { useRuntime } from '../useRuntime'
import type {
  BusinessField,
  DetailBlock,
  Entity,
  Link as ModelLink,
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
  parentRowKey,
}: {
  block: DetailBlock
  parentEntity: Entity
  parentRowKey: string
}) {
  const { bootstrap, apiBase } = useRuntime()
  const location = useLocation()
  const { basePath } = resolveAppRoute(location.pathname)
  const [limit, setLimit] = useState(INITIAL_LIMIT)

  const model = bootstrap.model
  const link: ModelLink | undefined = model.links.find(
    (item) => item.key === block.link,
  )
  const childKey = link
    ? link.from_ === parentEntity.key
      ? link.to
      : link.from_
    : undefined
  const child = childKey ? getEntity(model, childKey) : undefined
  const childList = child ? findViewForEntity(model, child.key, 'list') : undefined
  const childDetail = child
    ? findViewForEntity(model, child.key, 'detail')
    : undefined

  const { data, isPending } = useQuery({
    queryKey: [
      'related',
      apiBase,
      child?.key,
      link?.key,
      parentEntity.key,
      parentRowKey,
      limit,
    ],
    queryFn: () =>
      fetchRelated(apiBase, child!.key, {
        parentEntity: parentEntity.key,
        link: link!.key,
        parent: parentRowKey,
        limit,
      }),
    enabled: Boolean(child && link && parentRowKey),
  })

  if (!link || !child) {
    return (
      <div className="card placeholder-card">
        <h2>{block.title ?? '关联数据'}</h2>
        <p className="muted">关联配置不存在。</p>
      </div>
    )
  }

  const columns = childColumns(child, childList?.columns ?? [child.key_field])
  const title = block.title ?? `关联${child.name}`
  const rows = data?.rows ?? []
  const total = data?.total ?? 0

  return (
    <div className="card related-card">
      <div className="related-head">
        <h2>{title}</h2>
        <span className="related-count">
          {isPending && !data ? '加载中…' : `${total} 条`}
        </span>
      </div>
      {total === 0 ? (
        <p className="muted related-empty">
          {isPending && !data ? '正在加载关联记录…' : `暂无关联的${child.name}`}
        </p>
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
              {rows.map((row, index) => {
                const rowKey = String(row[child.key_field] ?? '')
                return (
                  <tr key={`${rowKey}-${index}`}>
                    {columns.map((field) => {
                      const value = row[field.key]
                      return (
                        <td key={field.key}>
                          {field.key === child.key_field && childDetail ? (
                            <Link
                              className="row-link"
                              to={`${basePath}/views/${childDetail.key}?id=${encodeURIComponent(rowKey)}`}
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
          {total > rows.length && (
            <div className="related-more">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setLimit((value) => value + LIMIT_STEP)}
              >
                显示更多（剩余 {total - rows.length} 条）
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
