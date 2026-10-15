import { describe, expect, it } from 'vitest'

import {
  EMPTY_FILTER_VALUE,
  activeFilterCount,
  applyListQuery,
  buildFilterOptions,
  clampPage,
  compareFieldValues,
  matchesFilters,
  matchesSearch,
  pageWindow,
  searchTokens,
  sortRows,
  type ListQuery,
} from './listQuery'
import type { BusinessField, RecordRow } from './types'

const rows: RecordRow[] = [
  { order_no: 'SO-1', customer_name: '杭州云栖', region: '华东', amount: 199, order_date: '2026-01-03', note: null },
  { order_no: 'SO-2', customer_name: '深圳前海', region: '华南', amount: 199, order_date: '2026-01-05', note: '加急' },
  { order_no: 'SO-10', customer_name: '北京中关村', region: '华北', amount: 1200, order_date: '2026-02-01', note: null },
  { order_no: 'SO-3', customer_name: '广州天河', region: '华南', amount: 49, order_date: '2026-01-09', note: '' },
]

const fields: Record<string, BusinessField> = {
  order_no: { key: 'order_no', name: '订单编号', type: 'string', role: 'identifier', confidence: 1 },
  customer_name: { key: 'customer_name', name: '客户名称', type: 'string', role: 'dimension', confidence: 1 },
  region: { key: 'region', name: '区域', type: 'enum', role: 'dimension', values: ['华东', '华南', '华北'], confidence: 1 },
  amount: { key: 'amount', name: '金额', type: 'money', role: 'measure', confidence: 1 },
  order_date: { key: 'order_date', name: '下单日期', type: 'date', role: 'time', confidence: 1 },
  note: { key: 'note', name: '备注', type: 'string', role: 'text', confidence: 1 },
}
const fieldOf = (key: string) => fields[key]

describe('searchTokens', () => {
  it('trims, lowercases and splits on whitespace', () => {
    expect(searchTokens('  杭州  SO-1 \n ')).toEqual(['杭州', 'so-1'])
  })
  it('empty query yields no tokens', () => {
    expect(searchTokens('   ')).toEqual([])
  })
})

describe('matchesSearch', () => {
  it('matches everything when there are no tokens', () => {
    expect(rows.every((row) => matchesSearch(row, ['order_no'], []))).toBe(true)
  })
  it('AND-combines tokens across any searchable field', () => {
    const keys = ['order_no', 'customer_name']
    expect(matchesSearch(rows[0], keys, ['so', '杭州'])).toBe(true)
    expect(matchesSearch(rows[0], keys, ['so', '深圳'])).toBe(false)
  })
  it('matches chinese bigrams and unigrams as substrings', () => {
    expect(matchesSearch(rows[0], ['customer_name'], ['云栖'])).toBe(true)
    expect(matchesSearch(rows[0], ['customer_name'], ['杭'])).toBe(true)
  })
  it('stringifies numbers and never throws on null', () => {
    expect(matchesSearch(rows[0], ['amount'], ['199'])).toBe(true)
    expect(matchesSearch(rows[0], ['note'], ['x'])).toBe(false)
  })
  it('matches ISO dates by prefix', () => {
    expect(matchesSearch(rows[0], ['order_date'], ['2026-01'])).toBe(true)
  })
})

describe('matchesFilters', () => {
  it('ANDs across fields and ORs within one field', () => {
    const state = { region: ['华南', '华北'] }
    expect(matchesFilters(rows[1], state)).toBe(true)
    expect(matchesFilters(rows[2], state)).toBe(true)
    expect(matchesFilters(rows[0], state)).toBe(false)
  })
  it('combines multiple fields', () => {
    const state = { region: ['华南'], amount: ['49'] }
    expect(matchesFilters(rows[3], state)).toBe(true)
    expect(matchesFilters(rows[1], state)).toBe(false)
  })
  it('treats null and empty string as the empty bucket', () => {
    const state = { note: [EMPTY_FILTER_VALUE] }
    expect(matchesFilters(rows[0], state)).toBe(true) // null
    expect(matchesFilters(rows[3], state)).toBe(true) // ''
    expect(matchesFilters(rows[1], state)).toBe(false) // 加急
  })
  it('ignores fields with no selection', () => {
    expect(matchesFilters(rows[0], { region: [] })).toBe(true)
  })
})

describe('compareFieldValues', () => {
  it('orders numbers numerically (199 < 1200), not lexicographically', () => {
    expect(compareFieldValues(199, 1200, 'money')).toBe(-1)
  })
  it('orders ISO dates chronologically', () => {
    expect(compareFieldValues('2026-01-09', '2026-02-01', 'date')).toBe(-1)
  })
  it('orders text with zh collation', () => {
    expect(compareFieldValues('上海', '北京', 'string')).toBeGreaterThan(0)
  })
  it('sinks blanks to the bottom in either direction', () => {
    expect(compareFieldValues(null, 'x', 'string')).toBe(1)
    expect(compareFieldValues('', 'x', 'string')).toBe(1)
    expect(compareFieldValues('x', null, 'string')).toBe(-1)
    expect(compareFieldValues(null, null, 'string')).toBe(0)
  })
})

describe('sortRows', () => {
  it('applies the first sort and falls through ties to the next', () => {
    const sorted = sortRows(rows, [
      { field: 'amount', dir: 'asc' },
      { field: 'order_no', dir: 'asc' },
    ], fieldOf)
    expect(sorted.map((row) => row.order_no)).toEqual([
      'SO-3', 'SO-1', 'SO-2', 'SO-10',
    ])
  })
  it('returns the same array reference when no sorts are declared', () => {
    expect(sortRows(rows, [], fieldOf)).toBe(rows)
  })
})

describe('buildFilterOptions', () => {
  it('dedupes, counts, merges blanks and sorts numerically', () => {
    const options = buildFilterOptions(rows, fields.amount)
    expect(options.map((option) => [option.value, option.count])).toEqual([
      ['49', 1], ['199', 2], ['1200', 1],
    ])
  })
  it('merges null and empty into the labeled empty option', () => {
    const options = buildFilterOptions(rows, fields.note)
    const empty = options.find((option) => option.value === EMPTY_FILTER_VALUE)
    expect(empty?.count).toBe(3)
    expect(empty?.label).toBe('（空）')
  })
})

describe('activeFilterCount / clampPage', () => {
  it('counts only fields with a selection', () => {
    expect(activeFilterCount({ a: ['x'], b: [], c: ['y', 'z'] })).toBe(2)
  })
  it('clamps pages into range', () => {
    expect(clampPage(0, 5)).toBe(1)
    expect(clampPage(9, 5)).toBe(5)
    expect(clampPage(3, 0)).toBe(1)
  })
})

describe('applyListQuery', () => {
  const base: ListQuery = {
    search: '',
    searchKeys: ['order_no', 'customer_name'],
    filters: {},
    sorts: [{ field: 'order_date', dir: 'asc' }],
    page: 1,
    pageSize: 2,
  }

  it('searches, filters, sorts and pages end to end', () => {
    const result = applyListQuery(rows, base, fieldOf)
    expect(result.total).toBe(4)
    expect(result.pageCount).toBe(2)
    expect(result.rows.map((row) => row.order_no)).toEqual(['SO-1', 'SO-2'])

    const page2 = applyListQuery(rows, { ...base, page: 2 }, fieldOf)
    expect(page2.rows.map((row) => row.order_no)).toEqual(['SO-3', 'SO-10'])
  })

  it('combines keyword search with enum filter', () => {
    const query: ListQuery = {
      ...base,
      search: 'so',
      filters: { region: ['华南'] },
    }
    const result = applyListQuery(rows, query, fieldOf)
    expect(result.total).toBe(2)
    expect(result.rows.map((row) => row.order_no)).toEqual(['SO-2', 'SO-3'])
  })

  it('clamps an out-of-range page after the result set shrinks', () => {
    const result = applyListQuery(
      rows,
      { ...base, page: 5, filters: { region: ['华东'] } },
      fieldOf,
    )
    expect(result.page).toBe(1)
    expect(result.rows.map((row) => row.order_no)).toEqual(['SO-1'])
  })
})

describe('pageWindow', () => {
  it('shows every page when under the cap', () => {
    expect(pageWindow(1, 4)).toEqual([1, 2, 3, 4])
  })
  it('centers the window and stays inside bounds', () => {
    expect(pageWindow(5, 20, 7)).toEqual([2, 3, 4, 5, 6, 7, 8])
    expect(pageWindow(1, 20, 7)).toEqual([1, 2, 3, 4, 5, 6, 7])
    expect(pageWindow(20, 20, 7)).toEqual([14, 15, 16, 17, 18, 19, 20])
  })
})
