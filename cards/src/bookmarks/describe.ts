/**
 * The words the card's head and empty state use for each subject. Kept apart
 * from the component so each phrasing is a plain function of its input.
 */

import type { BookmarksSubject } from './read-bookmarks.ts'

import { shortDate } from '../format.ts'

/** `the past day`, `the past 7 days`, `the past year`, `the past two years`. */
export const pastDays = (days: number): string => {
  if (days === 1) return 'the past day'
  if (days === 365) return 'the past year'
  if (days === 730) return 'the past two years'
  return `the past ${String(days)} days`
}

/**
 * A `YYYY-MM-DD` the reader typed is a calendar date, not an instant, so it is
 * printed in UTC: in any other zone midnight UTC can fall on the day before.
 */
const calendarDate = (value: string): string => shortDate(value, 'UTC') ?? value

export interface SubjectWords {
  readonly eyebrow: string
  readonly title: string
  readonly detail: string | undefined
  readonly empty: string
}

export const describeSubject = (subject: BookmarksSubject): SubjectWords => {
  switch (subject.kind) {
    case 'search': {
      return {
        detail: subject.daysBack === undefined ? undefined : `Searched ${pastDays(subject.daysBack)}`,
        empty: `Nothing in Pinboard matches “${subject.query}”.`,
        eyebrow: 'Pinboard search',
        title: `“${subject.query}”`,
      }
    }
    case 'recent': {
      const window = subject.days === undefined ? 'recently' : `in ${pastDays(subject.days)}`
      return {
        detail: `Saved ${window}`,
        empty: `Nothing saved ${window}.`,
        eyebrow: 'Pinboard',
        title: 'Recent bookmarks',
      }
    }
    case 'tags': {
      const { fromDate, toDate } = subject
      let detail: string | undefined
      if (fromDate !== undefined && toDate !== undefined) detail = `Saved ${calendarDate(fromDate)} to ${calendarDate(toDate)}`
      else if (fromDate !== undefined) detail = `Saved since ${calendarDate(fromDate)}`
      else if (toDate !== undefined) detail = `Saved until ${calendarDate(toDate)}`
      const joined = subject.tags.join(' + ')
      return {
        detail,
        empty: `No bookmarks tagged ${joined}.`,
        eyebrow: subject.tags.length === 1 ? 'Pinboard tag' : 'Pinboard tags',
        title: joined,
      }
    }
    default: {
      return { detail: undefined, empty: 'No bookmarks to show.', eyebrow: 'Pinboard', title: 'Bookmarks' }
    }
  }
}
