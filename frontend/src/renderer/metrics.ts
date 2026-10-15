import type { Metric, RecordRow } from './types'

function numericValues(rows: RecordRow[], field: string | null | undefined): number[] {
  if (!field) return []
  return rows
    .map((row) => row[field])
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
}

/**
 * Evaluate a V0.1 metric directly from bootstrap rows. Non-numeric and null
 * cells are ignored by numeric aggregations; count always counts rows.
 */
export function evaluateMetric(metric: Metric, rows: RecordRow[]): number {
  const { formula } = metric
  if (formula.op === 'count') return rows.length

  const values = numericValues(rows, formula.field)
  if (values.length === 0) return 0
  switch (formula.op) {
    case 'sum':
      return values.reduce((total, value) => total + value, 0)
    case 'avg':
      return values.reduce((total, value) => total + value, 0) / values.length
    case 'min':
      return Math.min(...values)
    case 'max':
      return Math.max(...values)
  }
}
