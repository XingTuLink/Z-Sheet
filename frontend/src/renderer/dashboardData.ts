import type {
  BusinessField,
  BusinessModel,
  DetailView,
  Entity,
  ListView,
  RecordRow,
} from './types'

const MAX_RECENT = 5
const MAX_TREND_POINTS = 6
const MAX_DISTRIBUTION_ITEMS = 6

export interface RecentSelection {
  entity: Entity
  listView?: ListView
  detailView?: DetailView
  sortField?: BusinessField
  rows: RecordRow[]
}

export interface TrendPoint {
  label: string
  value: number
}

export interface TrendSelection {
  entity: Entity
  dateField: BusinessField
  measureField?: BusinessField
  points: TrendPoint[]
}

export interface DistributionItem {
  label: string
  count: number
}

export interface DistributionSelection {
  entity: Entity
  field: BusinessField
  items: DistributionItem[]
  total: number
}

function listViewFor(model: BusinessModel, entityKey: string): ListView | undefined {
  return model.views.find(
    (view): view is ListView => view.kind === 'list' && view.entity === entityKey,
  )
}

function detailViewFor(model: BusinessModel, entityKey: string): DetailView | undefined {
  return model.views.find(
    (view): view is DetailView => view.kind === 'detail' && view.entity === entityKey,
  )
}

function timeField(entity: Entity): BusinessField | undefined {
  return (
    entity.fields.find((field) => field.role === 'time') ??
    entity.fields.find((field) => field.type === 'date')
  )
}

function nonEmptyRows(rows: RecordRow[], fieldKey: string): RecordRow[] {
  return rows.filter((row) => {
    const value = row[fieldKey]
    return value !== null && value !== undefined && value !== ''
  })
}

export function selectRecent(
  model: BusinessModel,
  records: Record<string, RecordRow[]>,
): RecentSelection | null {
  const candidates = model.entities
    .map((entity) => ({
      entity,
      field: timeField(entity),
      rows: records[entity.key] ?? [],
    }))
    .filter((item) => item.rows.length > 0)

  const dated = candidates
    .map((item) => ({
      ...item,
      datedRows: item.field ? nonEmptyRows(item.rows, item.field.key) : [],
    }))
    .filter((item) => item.field && item.datedRows.length > 0)
    .sort((a, b) => {
      if (b.datedRows.length !== a.datedRows.length) {
        return b.datedRows.length - a.datedRows.length
      }
      const aLatest = String(a.datedRows[0]?.[a.field!.key] ?? '')
      const bLatest = String(b.datedRows[0]?.[b.field!.key] ?? '')
      return bLatest.localeCompare(aLatest)
    })

  const chosen = dated[0] ?? candidates.sort((a, b) => b.rows.length - a.rows.length)[0]
  if (!chosen) return null

  const rows = chosen.field
    ? [...chosen.rows]
        .filter((row) => row[chosen.field!.key] !== null && row[chosen.field!.key] !== undefined)
        .sort((a, b) =>
          String(b[chosen.field!.key] ?? '').localeCompare(
            String(a[chosen.field!.key] ?? ''),
          ),
        )
        .slice(0, MAX_RECENT)
    : chosen.rows.slice(0, MAX_RECENT)

  return {
    entity: chosen.entity,
    listView: listViewFor(model, chosen.entity.key),
    detailView: detailViewFor(model, chosen.entity.key),
    sortField: chosen.field,
    rows,
  }
}

export function selectTrend(
  model: BusinessModel,
  records: Record<string, RecordRow[]>,
): TrendSelection | null {
  const candidates = model.entities
    .map((entity) => {
      const dateField = timeField(entity)
      const rows = dateField ? nonEmptyRows(records[entity.key] ?? [], dateField.key) : []
      return { entity, dateField, rows }
    })
    .filter((item): item is {
      entity: Entity
      dateField: BusinessField
      rows: RecordRow[]
    } => Boolean(item.dateField && item.rows.length > 0))
    .sort((a, b) => b.rows.length - a.rows.length)

  const chosen = candidates[0]
  if (!chosen) return null

  const measureField = chosen.entity.fields.find(
    (field) => field.type === 'money' && field.role === 'measure',
  )
  const buckets = new Map<string, number>()
  for (const row of chosen.rows) {
    const label = String(row[chosen.dateField.key] ?? '').slice(0, 7)
    if (!/^\d{4}-\d{2}$/.test(label)) continue
    const numeric = measureField ? row[measureField.key] : 1
    const contribution =
      measureField && typeof numeric === 'number' && Number.isFinite(numeric) ? numeric : 1
    buckets.set(label, (buckets.get(label) ?? 0) + contribution)
  }

  const points = [...buckets.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .slice(-MAX_TREND_POINTS)
    .map(([label, value]) => ({ label, value }))
  if (points.length === 0) return null

  return {
    entity: chosen.entity,
    dateField: chosen.dateField,
    measureField,
    points,
  }
}

export function selectDistribution(
  model: BusinessModel,
  records: Record<string, RecordRow[]>,
): DistributionSelection | null {
  const options: DistributionSelection[] = []
  for (const entity of model.entities) {
    for (const field of entity.fields) {
      if (field.type !== 'enum') continue
      const counts = new Map<string, number>()
      for (const row of records[entity.key] ?? []) {
        const value = row[field.key]
        if (value === null || value === undefined || value === '') continue
        const label = String(value)
        counts.set(label, (counts.get(label) ?? 0) + 1)
      }
      if (counts.size < 2) continue
      const items = [...counts.entries()]
        .map(([label, count]) => ({ label, count }))
        .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
        .slice(0, MAX_DISTRIBUTION_ITEMS)
      const total = [...counts.values()].reduce((sum, count) => sum + count, 0)
      options.push({ entity, field, items, total })
    }
  }

  // Prefer a genuinely categorical, fully visible enum (at least two values and
  // no more than the Top-N cap), then the field with the most populated rows.
  return (
    options
      .filter((option) => option.items.length <= MAX_DISTRIBUTION_ITEMS)
      .sort((a, b) => b.total - a.total)[0] ?? null
  )
}
