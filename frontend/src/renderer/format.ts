import type { BusinessField, FieldValue } from './types'

export const EMPTY_CELL = '—'

function groupThousands(intPart: string): string {
  return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

/** Deterministic, locale-independent number grouping. */
export function formatNumber(value: number): string {
  const [intPart, decPart] = String(value).split('.')
  const grouped = groupThousands(intPart)
  return decPart === undefined ? grouped : `${grouped}.${decPart}`
}

export function formatMoney(value: number): string {
  const sign = value < 0 ? '-' : ''
  return `¥${sign}${groupThousands(String(Math.trunc(Math.abs(value))))}.${String(
    Math.abs(value).toFixed(2).split('.')[1],
  )}`
}

/** Format an ISO timestamp as local `YYYY-MM-DD HH:mm`; invalid input → '—'. */
export function formatDateTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return EMPTY_CELL
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}`
  )
}

/**
 * The single cell/text rendering rule for the whole renderer.
 * Pure and deterministic: same model + same value -> same string.
 */
export function formatCell(
  field: BusinessField,
  value: FieldValue | undefined,
): string {
  if (value === null || value === undefined || value === '') {
    return EMPTY_CELL
  }
  if (field.type === 'money') {
    return typeof value === 'number' ? formatMoney(value) : String(value)
  }
  if (field.type === 'number') {
    return typeof value === 'number' ? formatNumber(value) : String(value)
  }
  return String(value)
}
