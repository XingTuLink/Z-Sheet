import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'

import { resolveAppRoute } from '../appRoute'
import { formatCell } from '../format'
import { findViewForEntity, getEntity } from '../resolve'
import { useRuntime } from '../useRuntime'
import type { DetailView as DetailViewModel } from '../types'
import { RelatedList } from './RelatedList'

export function DetailView({ view }: { view: DetailViewModel }) {
  const { bootstrap, records, deleteRecord } = useRuntime()
  const navigate = useNavigate()
  const model = bootstrap.model
  const [searchParams] = useSearchParams()
  const { basePath } = resolveAppRoute(useLocation().pathname)
  const id = searchParams.get('id') ?? ''

  const entity = getEntity(model, view.entity)
  if (!entity) {
    return (
      <section className="view">
        <div className="card">
          <h1>模型配置有误</h1>
          <p className="muted">详情视图 {view.key} 引用了不存在的实体。</p>
        </div>
      </section>
    )
  }

  const rows = records[view.entity] ?? []
  const row = rows.find(
    (item) => String(item[entity.key_field] ?? '') === id,
  )
  const listView = findViewForEntity(model, view.entity, 'list')
  const formView = findViewForEntity(model, view.entity, 'form')
  const listTarget = listView ? `${basePath}/views/${listView.key}` : basePath

  async function handleDelete() {
    if (!entity || !row) return
    const confirmed = window.confirm(`确定删除这条${entity.name}记录吗？此操作会立即保存。`)
    if (!confirmed) return
    try {
      await deleteRecord(entity.key, String(row[entity.key_field] ?? ''))
      navigate(listTarget)
    } catch {
      window.alert('删除失败，请稍后重试')
    }
  }

  return (
    <section className="view">
      <header className="view-header">
        <div>
          {listView && (
            <Link className="back-link" to={`${basePath}/views/${listView.key}`}>
              返回{listView.title}
            </Link>
          )}
          <h1>{view.title}</h1>
          {row && <p className="view-sub">{String(row[entity.key_field])}</p>}
        </div>
        {row && formView && (
          <div className="detail-actions">
            <Link
              className="btn btn-secondary"
              to={`${basePath}/views/${formView.key}?id=${encodeURIComponent(id)}`}
            >
              编辑
            </Link>
            <button type="button" className="btn btn-danger" onClick={handleDelete}>
              删除
            </button>
          </div>
        )}
      </header>

      {!row ? (
        <div className="card">
          <p className="muted">
            记录不存在。
            {listView && (
              <>
                {' '}
                回到<Link to={`${basePath}/views/${listView.key}`}>{listView.title}</Link>。
              </>
            )}
          </p>
        </div>
      ) : (
        view.blocks.map((block, index) =>
          block.type === 'fields' ? (
            <div className="card" key={`fields-${index}`}>
              <dl className="detail-grid">
                {entity.fields.map((field) => {
                  const value = row[field.key]
                  return (
                    <div className="detail-item" key={field.key}>
                      <dt>{field.name}</dt>
                      <dd>
                        {field.type === 'enum' && value ? (
                          <span className="tag">{formatCell(field, value)}</span>
                        ) : (
                          formatCell(field, value)
                        )}
                      </dd>
                    </div>
                  )
                })}
              </dl>
            </div>
          ) : (
            <RelatedList
              key={`related-${index}`}
              block={block}
              parentEntity={entity}
              parentRow={row}
            />
          ),
        )
      )}
    </section>
  )
}
