import { useMemo, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'

import { formatCell } from '../format'
import { resolveAppRoute } from '../appRoute'
import {
  DEFAULT_PAGE_SIZE,
  PAGE_SIZE_OPTIONS,
  activeFilterCount,
  applyListQuery,
  buildFilterOptions,
  pageWindow,
  type FilterOption,
  type FilterState,
} from '../listQuery'
import { EmptyState, ModelError } from '../widgets'
import { findViewForEntity, getEntity, getField } from '../resolve'
import { useRuntime } from '../useRuntime'
import type { BusinessField, ListView as ListViewModel, SortSpec } from '../types'

interface ColumnSort {
  field: string
  dir: 'asc' | 'desc'
}

function FilterDropdown({
  field,
  options,
  selected,
  open,
  onOpenChange,
  onChange,
}: {
  field: BusinessField
  options: FilterOption[]
  selected: string[]
  open: boolean
  onOpenChange: (open: boolean) => void
  onChange: (selected: string[]) => void
}) {
  const active = selected.length > 0

  const toggle = (value: string) => {
    onChange(
      selected.includes(value)
        ? selected.filter((item) => item !== value)
        : [...selected, value],
    )
  }

  return (
    <div className="filter-dropdown">
      <button
        type="button"
        className={`filter-trigger${active ? ' is-active' : ''}${open ? ' is-open' : ''}`}
        onClick={() => onOpenChange(!open)}
      >
        {field.name}
        {active && <span className="filter-badge">{selected.length}</span>}
        <span className="filter-caret" aria-hidden>
          {open ? '▲' : '▼'}
        </span>
      </button>
      {open && (
        <>
          {/* Click-away layer; stopPropagation on the panel keeps it open. */}
          <div
            className="filter-backdrop"
            onClick={() => onOpenChange(false)}
          />
          <div
            className="filter-panel card"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="filter-options">
              {options.map((option) => (
                <label key={option.value} className="filter-option">
                  <input
                    type="checkbox"
                    checked={selected.includes(option.value)}
                    onChange={() => toggle(option.value)}
                  />
                  <span className="filter-option-label">{option.label}</span>
                  <span className="filter-option-count">{option.count}</span>
                </label>
              ))}
            </div>
            {active && (
              <div className="filter-footer">
                <button
                  type="button"
                  className="filter-clear"
                  onClick={() => onChange([])}
                >
                  清除筛选
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function Pager({
  page,
  pageCount,
  pageSize,
  onChange,
}: {
  page: number
  pageCount: number
  pageSize: number
  onChange: (patch: { page?: number; pageSize?: number }) => void
}) {
  return (
    <div className="pager">
      <div className="pager-size">
        每页
        <select
          value={pageSize}
          onChange={(event) =>
            onChange({ pageSize: Number(event.target.value), page: 1 })
          }
        >
          {PAGE_SIZE_OPTIONS.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
        条
      </div>
      <div className="pager-pages">
        <button
          type="button"
          className="pager-btn"
          disabled={page <= 1}
          onClick={() => onChange({ page: page - 1 })}
        >
          上一页
        </button>
        {pageWindow(page, pageCount).map((number) => (
          <button
            key={number}
            type="button"
            className={`pager-btn pager-num${number === page ? ' active' : ''}`}
            onClick={() => onChange({ page: number })}
          >
            {number}
          </button>
        ))}
        <button
          type="button"
          className="pager-btn"
          disabled={page >= pageCount}
          onClick={() => onChange({ page: page + 1 })}
        >
          下一页
        </button>
        <span className="pager-position">
          {page}/{pageCount} 页
        </span>
      </div>
    </div>
  )
}

export function ListView({ view }: { view: ListViewModel }) {
  const { bootstrap, records } = useRuntime()
  const location = useLocation()
  const { basePath } = resolveAppRoute(location.pathname)
  const model = bootstrap.model
  const entity = getEntity(model, view.entity)

  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState<FilterState>({})
  // null = honor the model's default sorts; clicking a header overrides.
  const [userSort, setUserSort] = useState<ColumnSort | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [openFilter, setOpenFilter] = useState<string | null>(null)

  const result0 = useMemo(() => {
    if (!entity) return null
    const rows = records[view.entity] ?? []
    const sorts: SortSpec[] = userSort ? [userSort] : (view.sorts ?? [])
    return applyListQuery(
      rows,
      {
        search,
        searchKeys: view.search ?? [],
        filters,
        sorts,
        page,
        pageSize,
      },
      (key) => getField(entity, key),
    )
  }, [entity, records, view.entity, view.search, view.sorts, search, filters, userSort, page, pageSize])

  if (!entity || !result0) {
    return <ModelError>列表视图 {view.key} 引用了不存在的实体。</ModelError>
  }
  const result = result0

  const allRows = records[view.entity] ?? []
  const detailView = findViewForEntity(model, view.entity, 'detail')
  const formView = findViewForEntity(model, view.entity, 'form')
  const columns = view.columns
    .map((key) => ({ key, field: getField(entity, key) }))
    .filter((column): column is { key: string; field: BusinessField } =>
      Boolean(column.field),
    )
  const filterFields = (view.filters ?? [])
    .map((key) => getField(entity, key))
    .filter((field): field is BusinessField => Boolean(field))
  const searchFields = (view.search ?? [])
    .map((key) => getField(entity, key)?.name)
    .filter((name): name is string => Boolean(name))

  const activeSorts: SortSpec[] = userSort ? [userSort] : (view.sorts ?? [])
  const sortOf = (fieldKey: string) =>
    activeSorts.find((sort) => sort.field === fieldKey)
  const filterCount = activeFilterCount(filters)
  const filtered = search.trim() !== '' || filterCount > 0

  const cycleSort = (fieldKey: string) => {
    setPage(1)
    setUserSort((current) => {
      if (current?.field !== fieldKey) return { field: fieldKey, dir: 'asc' }
      if (current.dir === 'asc') return { field: fieldKey, dir: 'desc' }
      return null // third click restores model defaults
    })
  }

  const clearAll = () => {
    setSearch('')
    setFilters({})
    setUserSort(null)
    setPage(1)
    setOpenFilter(null)
  }

  const updatePager = (patch: { page?: number; pageSize?: number }) => {
    if (patch.pageSize !== undefined) setPageSize(patch.pageSize)
    setPage(patch.page ?? 1)
  }

  return (
    <section className="view">
      <header className="view-header">
        <div>
          <h1>{view.title}</h1>
          <p className="view-sub">
            {filtered ? `筛出 ${result.total} 条 / 共 ${allRows.length} 条` : `共 ${allRows.length} 条记录`}
          </p>
        </div>
        {formView && (
          <Link className="btn btn-primary" to={`${basePath}/views/${formView.key}`}>
            新增{entity.name}
          </Link>
        )}
      </header>

      {((view.search?.length ?? 0) > 0 || filterFields.length > 0) && (
        <div className="list-toolbar">
          {(view.search?.length ?? 0) > 0 && (
            <div className="search-box">
              <span className="search-icon" aria-hidden>
                ⌕
              </span>
              <input
                type="search"
                className="search-input"
                value={search}
                placeholder={
                  searchFields.length > 0
                    ? `搜索${searchFields.join('、')}`
                    : '搜索'
                }
                onChange={(event) => {
                  setSearch(event.target.value)
                  setPage(1)
                }}
              />
            </div>
          )}
          {filterFields.length > 0 && (
            <div className="filter-group">
              {filterFields.map((field) => (
                <FilterDropdown
                  key={field.key}
                  field={field}
                  options={buildFilterOptions(allRows, field)}
                  selected={filters[field.key] ?? []}
                  open={openFilter === field.key}
                  onOpenChange={(open) => setOpenFilter(open ? field.key : null)}
                  onChange={(selected) => {
                    setFilters((previous) => ({
                      ...previous,
                      [field.key]: selected,
                    }))
                    setPage(1)
                  }}
                />
              ))}
            </div>
          )}
          {filtered && (
            <button type="button" className="list-clear" onClick={clearAll}>
              清除条件
            </button>
          )}
        </div>
      )}

      <div className="card grid-card">
        {result.total === 0 ? (
          <EmptyState>
            {filtered ? (
              <>
                没有符合条件的记录。
                <div style={{ marginTop: '12px' }}>
                  <button type="button" className="btn btn-secondary" onClick={clearAll}>
                    清除搜索与筛选
                  </button>
                </div>
              </>
            ) : (
              '暂无数据'
            )}
          </EmptyState>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                {columns.map(({ key, field }) => {
                  const sort = sortOf(key)
                  return (
                    <th key={key} className="sortable-th">
                      <button
                        type="button"
                        className="column-sort"
                        onClick={() => cycleSort(key)}
                        title="点击切换排序"
                      >
                        <span>{field.name}</span>
                        <span
                          className={`sort-caret${sort ? ' is-on' : ''}`}
                          aria-hidden
                        >
                          {sort ? (sort.dir === 'asc' ? '↑' : '↓') : '↕'}
                        </span>
                      </button>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row) => {
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
                              to={`${basePath}/views/${detailView.key}?id=${encodeURIComponent(String(value ?? ''))}`}
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

      {result.total > 0 && (
        <Pager
          page={result.page}
          pageCount={result.pageCount}
          pageSize={pageSize}
          onChange={updatePager}
        />
      )}
    </section>
  )
}
