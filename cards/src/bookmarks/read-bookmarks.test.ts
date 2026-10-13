import { describe, expect, it } from 'vitest'

import { describeSubject, pastDays } from './describe.ts'
import { matchedTags, readBookmarks } from './read-bookmarks.ts'

describe('readBookmarks', () => {
  it('keeps a row with no title, titling it by its host', () => {
    const view = readBookmarks({ bookmarks: [{ url: 'https://www.example.com/a' }], query: 'x', total: 1 })

    expect(view.rows[0]).toMatchObject({ host: 'example.com', title: 'example.com' })
  })

  it('titles a row with neither title nor URL "Untitled", and keys it by position', () => {
    const view = readBookmarks({ bookmarks: [{}, {}] })

    expect(view.rows.map(row => [row.title, row.key])).toEqual([['Untitled', '#0'], ['Untitled', '#1']])
  })

  it('skips entries that are not objects, and counts what it read when total is malformed', () => {
    const view = readBookmarks({ bookmarks: ['junk', null, { title: 'Real' }], total: 'three' })

    expect(view.rows).toHaveLength(1)
    expect(view.total).toBe(1)
  })

  it('reads no rows from a payload without a bookmark list', () => {
    expect(readBookmarks({}).rows).toEqual([])
  })

  it('reads the subject from the payload’s own keys, tags first', () => {
    expect(readBookmarks({ bookmarks: [], query: 'q', tags: ['a'] }).subject.kind).toBe('tags')
    expect(readBookmarks({ bookmarks: [], days_back: 30, query: 'q' }).subject).toEqual({ daysBack: 30, kind: 'search', query: 'q' })
    expect(readBookmarks({ bookmarks: [], days: 3 }).subject).toEqual({ days: 3, kind: 'recent' })
    expect(readBookmarks({ bookmarks: [] }).subject).toEqual({ kind: 'unknown' })
  })
})

describe('matchedTags', () => {
  it('matches every word of a search, case-insensitively', () => {
    expect([...matchedTags({ daysBack: undefined, kind: 'search', query: 'Python  Testing' })]).toEqual(['python', 'testing'])
  })

  it('matches nothing for recent bookmarks', () => {
    expect(matchedTags({ days: 7, kind: 'recent' }).size).toBe(0)
  })
})

describe('describeSubject', () => {
  it.each([[1, 'the past day'], [7, 'the past 7 days'], [365, 'the past year'], [730, 'the past two years']])('words %i days as "%s"', (days, words) => {
    expect(pastDays(days)).toBe(words)
  })

  it('words a tag listing bounded on one side', () => {
    expect(describeSubject({ fromDate: '2026-01-01', kind: 'tags', tags: ['a'], toDate: undefined }).detail).toBe('Saved since 1 Jan 2026')
    // A date-time keeps its own calendar day, wherever the reader is.
    expect(describeSubject({ fromDate: '2026-01-01T05:00', kind: 'tags', tags: ['a'], toDate: undefined }).detail).toBe('Saved since 1 Jan 2026')
    expect(describeSubject({ fromDate: '20260101', kind: 'tags', tags: ['a'], toDate: undefined }).detail).toBe('Saved since 1 Jan 2026')
    expect(describeSubject({ fromDate: '2026-01-01T23:30:00-05:00', kind: 'tags', tags: ['a'], toDate: undefined }).detail).toBe('Saved since 1 Jan 2026')
    expect(describeSubject({ fromDate: undefined, kind: 'tags', tags: ['a'], toDate: '2026-02-01' }).detail).toBe('Saved until 1 Feb 2026')
    expect(describeSubject({ fromDate: undefined, kind: 'tags', tags: ['a'], toDate: undefined }).detail).toBeUndefined()
  })

  it('words recent bookmarks with no window', () => {
    expect(describeSubject({ days: undefined, kind: 'recent' }).detail).toBe('Saved recently')
  })
})
