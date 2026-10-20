import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState, type CSSProperties } from 'react'
import { Link, useLocation } from 'react-router-dom'

import { fetchRows } from '../../api/runtime'
import { formatCell } from '../format'
import { resolveAppRoute } from '../appRoute'
import {
  DEFAULT_PAGE_SIZE,
  PAGE_SIZE_OPTIONS,
  activeFilterCount,
  activeSorts,
  facetOptions,
  pageWindow,
  type FilterOption,
  type FilterState,
} from '../listQuery'
import { EmptyState, ModelError } from '../widgets'
import { findViewForEntity, getEntity, getField } from '../resolve'
import { useRuntime } from '../useRuntime'
import type { BusinessField, ListView as ListViewModel, SortSpec } from '../types'

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
  const { bootstrap, apiBase, readOnly, deleteRecord } = useRuntime()
  const location = useLocation()
  const { basePath } = resolveAppRoute(location.pathname)
  const model = bootstrap.model
  const entity = getEntity(model, view.entity)

  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState<FilterState>({})
  // null = honor the model's default sorts; clicking a header overrides.
  const [userSort, setUserSort] = useState<SortSpec | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [openFilter, setOpenFilter] = useState<string | null>(null)

  const sorts = activeSorts(userSort, view.sorts)
  const { data, isPending, isError } = useQuery({
    queryKey: [
      'rows',
      apiBase,
      view.entity,
      search,
      filters,
      sorts,
      page,
      pageSize,
      view.search,
      view.filters,
    ],
    queryFn: () =>
      fetchRows(apiBase, view.entity, {
        page,
        pageSize,
        search,
        searchFields: view.search ?? [],
        filters,
        sorts,
        facets: view.filters ?? [],
      }),
    // Keep the current page on screen while the next one loads, so paging
    // never flashes an empty grid or collapses the fixed row heights.
    placeholderData: keepPreviousData,
  })

  if (!entity) {
    return <ModelError>列表视图 {view.key} 引用了不存在的实体。</ModelError>
  }

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

  const sortOf = (fieldKey: string) =>
    sorts.find((sort) => sort.field === fieldKey)
  const filterCount = activeFilterCount(filters)
  const filtered = search.trim() !== '' || filterCount > 0
  const rows = data?.rows ?? []
  const total = data?.total ?? 0
  const currentPage = data?.page ?? page
  const pageCount = data?.page_count ?? 1

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

  const handleDelete = async (key: string) => {
    const confirmed = window.confirm(`确定删除这条${entity.name}记录吗？此操作会立即保存。`)
    if (!confirmed) return
    try {
      await deleteRecord(entity.key, key)
    } catch {
      window.alert('删除失败，请稍后重试')
    }
  }

  return (
    <section className="view">
      <header className="view-header">
        <div>
          <h1>{view.title}</h1>
          <p className="view-sub">
            {!data
              ? '正在加载…'
              : filtered
                ? `筛出 ${total} 条 / 共 ${data.entity_total} 条`
                : `共 ${data.entity_total} 条记录`}
          </p>
        </div>
        {formView && !readOnly && (
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
                  options={facetOptions(data?.facets[field.key])}
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

      <div
        className="card grid-card"
        data-paged={total > 0 ? 'true' : undefined}
        style={
          total > 0 ? ({ '--grid-rows': pageSize } as CSSProperties) : undefined
        }
      >
        {isError ? (
          <EmptyState>记录加载失败，请刷新页面重试。</EmptyState>
        ) : isPending && !data ? (
          <EmptyState>正在加载记录…</EmptyState>
        ) : total === 0 ? (
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
                {!readOnly && <th className="row-action-head">操作</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const rowKey = String(row[entity.key_field] ?? '')
                return (
                  <tr key={rowKey}>
                    {columns.map(({ key, field }) => {
                      const value = row[key]
                      const text = formatCell(field, value)
                      const title =
                        value === null || value === undefined || value === ''
                          ? undefined
                          : text
                      if (key === entity.key_field && detailView) {
                        return (
                          <td key={key} title={title}>
                            <Link
                              className="row-link"
                              to={`${basePath}/views/${detailView.key}?id=${encodeURIComponent(String(value ?? ''))}`}
                            >
                              {text}
                            </Link>
                          </td>
                        )
                      }
                      return (
                        <td key={key} title={title}>
                          {field.type === 'enum' && value ? (
                            <span className="tag">{text}</span>
                          ) : (
                            text
                          )}
                        </td>
                      )
                    })}
                    {!readOnly && (
                      <td className="row-action-cell">
                        <span className="row-actions">
                          {formView && (
                            <Link
                              className="row-link"
                              to={`${basePath}/views/${formView.key}?id=${encodeURIComponent(rowKey)}`}
                            >
                              编辑
                            </Link>
                          )}
                          <button
                            type="button"
                            className="row-link row-action-danger"
                            onClick={() => void handleDelete(rowKey)}
                          >
                            删除
                          </button>
                        </span>
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {total > 0 && (
        <Pager
          page={currentPage}
          pageCount={pageCount}
          pageSize={pageSize}
          onChange={updatePager}
        />
      )}
    </section>
  )
}
