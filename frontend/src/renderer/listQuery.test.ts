import { describe, expect, it } from 'vitest'

import {
  EMPTY_FILTER_VALUE,
  activeFilterCount,
  activeSorts,
  facetOptions,
  pageWindow,
} from './listQuery'

describe('activeFilterCount', () => {
  it('counts only fields with a selection', () => {
    expect(activeFilterCount({ a: ['x'], b: [], c: ['y', 'z'] })).toBe(2)
  })
})

describe('activeSorts', () => {
  it('falls back to the model defaults', () => {
    expect(activeSorts(null, [{ field: 'order_date', dir: 'desc' }])).toEqual([
      { field: 'order_date', dir: 'desc' },
    ])
  })
  it('a column click replaces the defaults', () => {
    expect(
      activeSorts({ field: 'amount', dir: 'asc' }, [
        { field: 'order_date', dir: 'desc' },
      ]),
    ).toEqual([{ field: 'amount', dir: 'asc' }])
  })
})

describe('facetOptions', () => {
  it('labels the blank bucket and keeps server counts', () => {
    expect(
      facetOptions([
        { value: '华东', count: 2 },
        { value: EMPTY_FILTER_VALUE, count: 1 },
      ]),
    ).toEqual([
      { value: '华东', label: '华东', count: 2 },
      { value: EMPTY_FILTER_VALUE, label: '（空）', count: 1 },
    ])
  })
  it('tolerates a missing facet', () => {
    expect(facetOptions(undefined)).toEqual([])
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
