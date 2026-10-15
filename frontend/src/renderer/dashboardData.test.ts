import { describe, expect, it } from 'vitest'

import { selectDistribution, selectRecent, selectTrend } from './dashboardData'
import type { BusinessModel, RecordRow } from './types'

const model: BusinessModel = {
  version: '0.1',
  app: { name: '测试应用', source: null },
  entities: [
    {
      key: 'customer',
      name: '客户',
      key_field: 'name',
      fields: [
        { key: 'name', name: '名称', type: 'string', role: 'identifier', confidence: 1 },
        {
          key: 'region',
          name: '区域',
          type: 'enum',
          role: 'dimension',
          values: ['华东', '华北'],
          confidence: 1,
        },
      ],
    },
    {
      key: 'order',
      name: '订单',
      key_field: 'no',
      fields: [
        { key: 'no', name: '单号', type: 'string', role: 'identifier', confidence: 1 },
        { key: 'date', name: '日期', type: 'date', role: 'time', confidence: 1 },
        { key: 'amount', name: '金额', type: 'money', role: 'measure', confidence: 1 },
      ],
    },
  ],
  links: [],
  metrics: [],
  views: [
    { kind: 'list', key: 'customer_list', entity: 'customer', title: '客户', columns: ['name'] },
    { kind: 'detail', key: 'customer_detail', entity: 'customer', title: '客户详情', blocks: [{ type: 'fields' }] },
    { kind: 'list', key: 'order_list', entity: 'order', title: '订单', columns: ['no', 'date'] },
    { kind: 'detail', key: 'order_detail', entity: 'order', title: '订单详情', blocks: [{ type: 'fields' }] },
  ],
  navigation: [],
}

const records: Record<string, RecordRow[]> = {
  customer: [
    { name: '甲', region: '华东' },
    { name: '乙', region: '华北' },
    { name: '丙', region: '华东' },
    { name: '丁', region: null },
  ],
  order: [
    { no: 'O3', date: '2026-03-01', amount: 300 },
    { no: 'O1', date: '2026-01-01', amount: 100 },
    { no: 'O2', date: '2026-02-01', amount: 200 },
    { no: 'O4', date: null, amount: 400 },
  ],
}

describe('selectRecent', () => {
  it('prefers a populated time entity and returns newest rows', () => {
    const recent = selectRecent(model, records)
    expect(recent?.entity.key).toBe('order')
    expect(recent?.rows.map((row) => row.no)).toEqual(['O3', 'O2', 'O1'])
    expect(recent?.listView?.key).toBe('order_list')
    expect(recent?.detailView?.key).toBe('order_detail')
  })

  it('falls back to the entity with the most rows', () => {
    const recent = selectRecent(model, { customer: records.customer })
    expect(recent?.entity.key).toBe('customer')
    expect(recent?.rows).toHaveLength(4)
  })
})

describe('selectTrend', () => {
  it('buckets money measure by month', () => {
    const trend = selectTrend(model, records)
    expect(trend?.measureField?.key).toBe('amount')
    expect(trend?.points).toEqual([
      { label: '2026-01', value: 100 },
      { label: '2026-02', value: 200 },
      { label: '2026-03', value: 300 },
    ])
  })

  it('returns null without valid dated rows', () => {
    expect(selectTrend(model, { customer: records.customer, order: [] })).toBeNull()
  })
})

describe('selectDistribution', () => {
  it('counts low-cardinality enum values and orders by frequency', () => {
    const distribution = selectDistribution(model, records)
    expect(distribution?.field.key).toBe('region')
    expect(distribution?.items).toEqual([
      { label: '华东', count: 2 },
      { label: '华北', count: 1 },
    ])
    expect(distribution?.total).toBe(3)
  })
})
