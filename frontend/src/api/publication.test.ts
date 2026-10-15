import { describe, expect, it } from 'vitest'

import { normalizeSlug, validateSlug } from './publication'

describe('normalizeSlug', () => {
  it('trims and lowercases like the backend', () => {
    expect(normalizeSlug('  Kehu-Link  ')).toBe('kehu-link')
  })
})

describe('validateSlug', () => {
  it.each([
    ['', '不能为空'],
    ['a', '长度'],
    ['x'.repeat(41), '长度'],
    ['-abc', '连字符'],
    ['abc-', '连字符'],
    ['a_b', '小写字母'],
    ['中文链接', '小写字母'],
    ['api', '保留'],
    ['admin', '保留'],
    ['workspace', '保留'],
  ])('rejects %j with a Chinese reason', (raw, fragment) => {
    expect(validateSlug(raw)).toContain(fragment)
  })

  it('accepts well-formed slugs', () => {
    expect(validateSlug('kehu-xiaoshou')).toBeNull()
    expect(validateSlug('a1')).toBeNull()
    expect(validateSlug('x'.repeat(40))).toBeNull()
    expect(validateSlug('ABC-123')).toBeNull()
  })
})
