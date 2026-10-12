import { describe, expect, it } from 'vitest'

import { countOf, instantTitle, shortDate, viewerTimeZone } from './format.ts'

describe('format', () => {
  it('reads the zone the tests pin', () => {
    expect(viewerTimeZone()).toBe('UTC')
  })

  it('prints a date on the reader’s calendar, which can be the next day', () => {
    expect(shortDate('2026-07-01T13:29:00Z')).toBe('1 Jul 2026')
    expect(shortDate('2026-07-01T13:29:00Z', 'Pacific/Auckland')).toBe('2 Jul 2026')
  })

  it('titles an instant to the second, naming the zone', () => {
    expect(instantTitle('2026-10-12T05:51:07Z', 'Europe/Dublin')).toBe('12 Oct 2026, 6:51:07am GMT+1 (Europe/Dublin)')
    expect(instantTitle('2026-10-12T05:51:07Z')).toBe('12 Oct 2026, 5:51:07am UTC')
  })

  it('drops an unparseable or absent value rather than printing "Invalid Date"', () => {
    expect(shortDate('not a date')).toBeUndefined()
    expect(shortDate(undefined)).toBeUndefined()
    expect(instantTitle(42)).toBeUndefined()
  })

  it('counts with grouping and the right noun', () => {
    expect(countOf(1, 'tag')).toBe('1 tag')
    expect(countOf(1204, 'bookmark')).toBe('1,204 bookmarks')
    expect(countOf(2, 'match', 'matches')).toBe('2 matches')
  })
})
