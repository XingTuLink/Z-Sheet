import { describe, expect, it } from 'vitest'

import { resolveRelationSides, selectRelatedRows } from './relationQuery'
import type { Entity, Link, RecordRow } from './types'

const customer: Entity = {
  key: 'customer',
  name: '客户',
  key_field: 'customer_name',
  fields: [{ key: 'customer_name', name: '客户名称', type: 'string', role: 'identifier', confidence: 1 }],
}

const order: Entity = {
  key: 'order',
  name: '订单',
  key_field: 'order_no',
  fields: [
    { key: 'order_no', name: '订单编号', type: 'string', role: 'identifier', confidence: 1 },
    { key: 'customer_name', name: '客户名称', type: 'string', role: 'dimension', confidence: 1 },
    { key: 'order_date', name: '日期', type: 'date', role: 'time', confidence: 1 },
  ],
}

const link: Link = {
  key: 'customer_orders',
  from_: 'customer',
  to: 'order',
  type: 'one_to_many',
  on: { from_: 'customer_name', to: 'customer_name' },
  confidence: 0.9,
}

const orders: RecordRow[] = [
  { order_no: 'O-3', customer_name: '甲', order_date: '2026-03-01' },
  { order_no: 'O-1', customer_name: '甲', order_date: '2026-01-01' },
  { order_no: 'O-2', customer_name: '乙', order_date: '2026-02-01' },
  { order_no: 'O-4', customer_name: null, order_date: '2026-04-01' },
  { order_no: 'O-5', customer_name: '', order_date: '2026-05-01' },
]

describe('resolveRelationSides', () => {
  it('resolves parent-to-child field sides', () => {
    expect(resolveRelationSides('customer', link)).toEqual({
      parentField: 'customer_name',
      childField: 'customer_name',
      childEntityKey: 'order',
    })
  })
  it('resolves the inverse side defensively', () => {
    expect(resolveRelationSides('order', link)?.childEntityKey).toBe('customer')
  })
  it('returns null for an unrelated entity', () => {
    expect(resolveRelationSides('product', link)).toBeNull()
  })
})

describe('selectRelatedRows', () => {
  it('matches one customer and honors the child list date sort', () => {
    const rows = selectRelatedRows({
      rows: orders,
      currentEntity: customer,
      parentRow: { customer_name: '甲' },
      link,
      childEntity: order,
      sorts: [{ field: 'order_date', dir: 'desc' }],
    })
    expect(rows.map((row) => row.order_no)).toEqual(['O-3', 'O-1'])
  })

  it('never matches null/empty child keys against an empty parent', () => {
    const rows = selectRelatedRows({
      rows: orders,
      currentEntity: customer,
      parentRow: { customer_name: '' },
      link,
      childEntity: order,
      sorts: [],
    })
    expect(rows).toEqual([])
  })

  it('applies a limit after sorting', () => {
    const rows = selectRelatedRows({
      rows: orders,
      currentEntity: customer,
      parentRow: { customer_name: '甲' },
      link,
      childEntity: order,
      sorts: [{ field: 'order_date', dir: 'desc' }],
      limit: 1,
    })
    expect(rows.map((row) => row.order_no)).toEqual(['O-3'])
  })
})
