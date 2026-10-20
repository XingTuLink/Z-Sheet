/**
 * List-view UI state helpers.
 *
 * Searching, filtering, sorting and paging run on the server; the renderer
 * only tracks what the user selected and presents the returned page.
 */

import type { FacetCount } from '../api/runtime'
import type { SortSpec } from './types'

export const DEFAULT_PAGE_SIZE = 10
export const PAGE_SIZE_OPTIONS = [10, 20, 50]

/** The synthetic value representing null/blank cells inside filter options. */
export const EMPTY_FILTER_VALUE = '__empty__'

/** Multi-select state: field key -> selected raw values. */
export type FilterState = Record<string, string[]>

export interface FilterOption {
  value: string
  label: string
  count: number
}

export function activeFilterCount(filters: FilterState): number {
  return Object.values(filters).reduce(
    (sum, selected) => sum + (selected.length > 0 ? 1 : 0),
    0,
  )
}

/** Server facet counts -> dropdown options (blank cells share one bucket). */
export function facetOptions(counts: FacetCount[] | undefined): FilterOption[] {
  return (counts ?? []).map((item) => ({
    value: item.value,
    label: item.value === EMPTY_FILTER_VALUE ? '（空）' : item.value,
    count: item.count,
  }))
}

/** The column clicked by the user, if any; otherwise the model defaults. */
export function activeSorts(
  userSort: SortSpec | null,
  defaults: SortSpec[] | undefined,
): SortSpec[] {
  return userSort ? [userSort] : (defaults ?? [])
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
