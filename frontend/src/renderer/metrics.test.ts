import { describe, expect, it } from 'vitest'

import { evaluateMetric } from './metrics'
import type { Metric, RecordRow } from './types'

function metric(op: Metric['formula']['op'], field: string | null = 'amount'): Metric {
  return {
    key: `${op}_metric`,
    name: '指标',
    entity: 'order',
    formula: { op, field },
    business_definition: '测试口径',
  }
}

const rows: RecordRow[] = [
  { amount: 10 },
  { amount: 20 },
  { amount: null },
  { amount: '30' } as unknown as RecordRow,
  { amount: true } as unknown as RecordRow,
]

describe('evaluateMetric', () => {
  it('counts every row', () => {
    expect(evaluateMetric(metric('count', null), rows)).toBe(5)
  })

  it('sums and averages finite numeric cells only', () => {
    expect(evaluateMetric(metric('sum'), rows)).toBe(30)
    expect(evaluateMetric(metric('avg'), rows)).toBe(15)
  })

  it('computes min and max', () => {
    expect(evaluateMetric(metric('min'), rows)).toBe(10)
    expect(evaluateMetric(metric('max'), rows)).toBe(20)
  })

  it('returns zero for numeric metrics without valid values', () => {
    expect(evaluateMetric(metric('sum'), [])).toBe(0)
    expect(evaluateMetric(metric('avg'), [{ amount: null }])).toBe(0)
  })
})
