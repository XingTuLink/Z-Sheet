/**
 * Deterministic in-browser list querying for the Day 16 List View.
 *
 * Bootstrap already ships every row for an app (V0.1 stays at thousands of
 * rows, Day 18 keeps the same data path), so search / filter / sort / page
 * are pure functions over the in-memory records — no model calls, no
 * backend query language. The functions never mutate their inputs.
 *
 * Search mirrors design 28.6.1's spirit at this scale: case-insensitive
 * substring tokens (whitespace-split, AND-combined). Chinese needs no
 * tokenizer — substring matching covers unigram ("科") and bigram ("科技")
 * queries naturally; English lowercasing covers case variants.
 */

import type {
  BusinessField,
  FieldType,
  FieldValue,
  RecordRow,
  SortSpec,
} from './types'

export const DEFAULT_PAGE_SIZE = 10
export const PAGE_SIZE_OPTIONS = [10, 20, 50]

/** The synthetic value representing null/blank cells inside filter options. */
export const EMPTY_FILTER_VALUE = '__empty__'

/** Multi-select state: field key -> selected raw values. */
export type FilterState = Record<string, string[]>

export interface ListQuery {
  search: string
  searchKeys: string[]
  filters: FilterState
  /** Active sorts; model defaults unless the user clicked a column. */
  sorts: SortSpec[]
  page: number
  pageSize: number
}

export interface ListQueryResult {
  rows: RecordRow[]
  total: number
  page: number
  pageCount: number
  pageSize: number
}

function stringifyValue(value: FieldValue | undefined): string {
  if (value === null || value === undefined) return ''
  return String(value)
}

/** Whitespace-split, lowercase, drop blanks. */
export function searchTokens(query: string): string[] {
  return query.trim().toLowerCase().split(/\s+/).filter(Boolean)
}

/** AND over tokens, OR over the model-declared searchable fields. */
export function matchesSearch(
  row: RecordRow,
  searchKeys: string[],
  tokens: string[],
): boolean {
  if (tokens.length === 0) return true
  const haystacks = searchKeys.map((key) =>
    stringifyValue(row[key]).toLowerCase(),
  )
  return tokens.every((token) =>
    haystacks.some((haystack) => haystack.includes(token)),
  )
}

/** AND across filtered fields; within one field any selected value matches. */
export function matchesFilters(
  row: RecordRow,
  filters: FilterState,
): boolean {
  for (const [fieldKey, selected] of Object.entries(filters)) {
    if (selected.length === 0) continue
    const raw = stringifyValue(row[fieldKey])
    const normalized = raw === '' ? EMPTY_FILTER_VALUE : raw
    if (!selected.includes(normalized)) return false
  }
  return true
}

/** Numeric (and ISO date) columns compare by value; everything else by zh
 * locale text. Blank values always sink to the bottom regardless of dir. */
export function compareFieldValues(
  a: FieldValue | undefined,
  b: FieldValue | undefined,
  type: FieldType,
): number {
  const blank = (value: FieldValue | undefined) =>
    value === null || value === undefined || value === ''
  if (blank(a) && blank(b)) return 0
  if (blank(a)) return 1
  if (blank(b)) return -1

  if (type === 'number' || type === 'money' || type === 'date') {
    // ISO date strings (YYYY-MM-DD) order correctly lexicographically; keep
    // them with the numeric branch so "2026-2-1" style values still compare
    // by parsed time rather than locale text.
    const na = type === 'date' ? Date.parse(String(a)) : Number(a)
    const nb = type === 'date' ? Date.parse(String(b)) : Number(b)
    if (Number.isFinite(na) && Number.isFinite(nb) && na !== nb) {
      return na < nb ? -1 : 1
    }
  }
  return String(a).localeCompare(String(b), 'zh-Hans-CN')
}

/** Apply sorts in declaration order (first differing comparison wins). */
export function sortRows(
  rows: RecordRow[],
  sorts: SortSpec[],
  fieldOf: (key: string) => BusinessField | undefined,
): RecordRow[] {
  if (sorts.length === 0) return rows
  return [...rows].sort((a, b) => {
    for (const sort of sorts) {
      const field = fieldOf(sort.field)
      const compared = compareFieldValues(
        a[sort.field],
        b[sort.field],
        field?.type ?? 'string',
      )
      if (compared !== 0) return sort.dir === 'asc' ? compared : -compared
    }
    return 0
  })
}

export interface FilterOption {
  value: string
  label: string
  count: number
}

/** Distinct values for one filter field, counts included, blanks merged. */
export function buildFilterOptions(
  rows: RecordRow[],
  field: BusinessField,
): FilterOption[] {
  const counts = new Map<string, number>()
  for (const row of rows) {
    const raw = stringifyValue(row[field.key])
    const key = raw === '' ? EMPTY_FILTER_VALUE : raw
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  return [...counts.entries()]
    .map(([value, count]) => ({
      value,
      label: value === EMPTY_FILTER_VALUE ? '（空）' : value,
      count,
    }))
    .sort((a, b) =>
      compareFieldValues(
        a.value === EMPTY_FILTER_VALUE ? '' : a.value,
        b.value === EMPTY_FILTER_VALUE ? '' : b.value,
        field.type,
      ),
    )
}

export function activeFilterCount(filters: FilterState): number {
  return Object.values(filters).reduce(
    (sum, selected) => sum + (selected.length > 0 ? 1 : 0),
    0,
  )
}

/** Clamp page into the valid range after totals change. */
export function clampPage(page: number, pageCount: number): number {
  if (pageCount <= 0) return 1
  return Math.min(Math.max(1, page), pageCount)
}

/** Full pipeline: search -> filters -> sort -> paginate. */
export function applyListQuery(
  source: RecordRow[],
  query: ListQuery,
  fieldOf: (key: string) => BusinessField | undefined,
): ListQueryResult {
  const tokens = searchTokens(query.search)
  const searched = source.filter((row) =>
    matchesSearch(row, query.searchKeys, tokens),
  )
  const filtered = searched.filter((row) => matchesFilters(row, query.filters))
  const sorted = sortRows(filtered, query.sorts, fieldOf)

  const pageSize = query.pageSize > 0 ? query.pageSize : DEFAULT_PAGE_SIZE
  const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize))
  const page = clampPage(query.page, pageCount)
  const start = (page - 1) * pageSize

  return {
    rows: sorted.slice(start, start + pageSize),
    total: sorted.length,
    page,
    pageCount,
    pageSize,
  }
}

/** 1-based window of page numbers for the pager, capped to `maxButtons`. */
export function pageWindow(
  page: number,
  pageCount: number,
  maxButtons = 7,
): number[] {
  if (pageCount <= maxButtons) {
    return Array.from({ length: pageCount }, (_, index) => index + 1)
  }
  const edge = Math.floor(maxButtons / 2)
  let first = page - edge
  let last = page + edge
  if (first < 1) {
    last += 1 - first
    first = 1
  }
  if (last > pageCount) {
    first -= last - pageCount
    last = pageCount
  }
  const pages: number[] = []
  for (let index = Math.max(1, first); index <= Math.min(pageCount, last); index++) {
    pages.push(index)
  }
  return pages
}
