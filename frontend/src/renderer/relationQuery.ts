/**
 * Deterministic related-row selection for Detail View related_list blocks.
 *
 * A V0.1 link is one-to-many: parent.on.from = child.on.to. The detail page
 * is normally on the parent (customer -> that customer's orders), but the
 * helper is symmetric so a defensively-authored child detail block still
 * resolves safely. Matching is blank-sensitive: an empty parent key never
 * matches an empty child cell.
 */

import type {
  BusinessField,
  Entity,
  FieldValue,
  Link,
  RecordRow,
  SortSpec,
} from './types'
import { sortRows } from './listQuery'

export interface RelatedRowsQuery {
  rows: RecordRow[]
  currentEntity: Entity
  parentRow: RecordRow
  link: Link
  childEntity: Entity
  sorts: SortSpec[]
  limit?: number
}

function sameKeyValue(a: FieldValue | undefined, b: FieldValue | undefined): boolean {
  if (a === null || a === undefined || a === '') return false
  if (b === null || b === undefined || b === '') return false
  return String(a) === String(b)
}

/** Resolve which side of the link the detail entity and child are on. */
export function resolveRelationSides(
  currentEntityKey: string,
  link: Link,
): { parentField: string; childField: string; childEntityKey: string } | null {
  if (link.from_ === currentEntityKey) {
    return {
      parentField: link.on.from_,
      childField: link.on.to,
      childEntityKey: link.to,
    }
  }
  if (link.to === currentEntityKey) {
    return {
      parentField: link.on.to,
      childField: link.on.from_,
      childEntityKey: link.from_,
    }
  }
  return null
}

export function selectRelatedRows({
  rows,
  currentEntity,
  parentRow,
  link,
  childEntity,
  sorts,
  limit,
}: RelatedRowsQuery): RecordRow[] {
  const sides = resolveRelationSides(currentEntity.key, link)
  if (sides === null || sides.childEntityKey !== childEntity.key) return []

  const parentValue = parentRow[sides.parentField]
  const matched = rows.filter((row) =>
    sameKeyValue(row[sides.childField], parentValue),
  )
  const fieldOf = (key: string): BusinessField | undefined =>
    childEntity.fields.find((field) => field.key === key)
  const sorted = sortRows(matched, sorts, fieldOf)
  return typeof limit === 'number' && limit > 0 ? sorted.slice(0, limit) : sorted
}
