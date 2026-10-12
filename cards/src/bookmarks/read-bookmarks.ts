/**
 * Narrowing the bookmark tools' `structuredContent` into what the card draws.
 *
 * Four tools share this card, and each says what it was asked in its own keys:
 *
 * | Tool                         | Keys beside `bookmarks` and `total`   |
 * |------------------------------|----------------------------------------|
 * | `search_bookmarks`           | `query`                                |
 * | `search_bookmarks_extended`  | `query`, `days_back`                   |
 * | `list_recent_bookmarks`      | `days`                                 |
 * | `list_bookmarks_by_tags`     | `tags`, `from_date`, `to_date`         |
 *
 * so the subject is read from the payload's shape, never from a tool name
 * the card is not told. Every field goes through a guard; a malformed one
 * drops out rather than throwing, and a row with no usable URL or title keeps
 * its place with whatever it does have.
 */

import { isRecord, isUnknownArray, readCount, readText, readTextList } from '../guards.ts'
import { displayHost } from '../shell/safe-url.ts'

export interface BookmarkRow {
  /** Stable across re-renders: the tool's id, else the URL and position. */
  readonly key: string
  readonly url: string | undefined
  readonly title: string
  readonly host: string | undefined
  readonly notes: string | undefined
  readonly tags: readonly string[]
  readonly savedAt: string | undefined
}

export type BookmarksSubject =
  | { readonly kind: 'recent', readonly days: number | undefined }
  | { readonly kind: 'search', readonly query: string, readonly daysBack: number | undefined }
  | { readonly kind: 'tags', readonly tags: readonly string[], readonly fromDate: string | undefined, readonly toDate: string | undefined }
  | { readonly kind: 'unknown' }

export interface BookmarksView {
  readonly subject: BookmarksSubject
  readonly rows: readonly BookmarkRow[]
  /** The tool's `total` when it is a count, else the rows read. */
  readonly total: number
}

const readSubject = (data: Record<string, unknown>): BookmarksSubject => {
  const tags = readTextList(data['tags'])
  if (tags.length > 0) {
    return { fromDate: readText(data['from_date']), kind: 'tags', tags, toDate: readText(data['to_date']) }
  }
  const query = readText(data['query'])
  if (query !== undefined) return { daysBack: readCount(data['days_back']), kind: 'search', query }
  if ('days' in data) return { days: readCount(data['days']), kind: 'recent' }
  return { kind: 'unknown' }
}

const readRow = (value: unknown, index: number): BookmarkRow | undefined => {
  if (!isRecord(value)) return undefined
  const url = readText(value['url'])
  const host = url === undefined ? undefined : displayHost(url)
  const title = readText(value['title']) ?? host ?? url ?? 'Untitled'
  return {
    host,
    key: readText(value['id']) ?? `${url ?? ''}#${String(index)}`,
    notes: readText(value['notes'])?.trim(),
    savedAt: readText(value['saved_at']),
    tags: readTextList(value['tags']),
    title,
    url,
  }
}

export const readBookmarks = (data: Record<string, unknown>): BookmarksView => {
  const raw = isUnknownArray(data['bookmarks']) ? data['bookmarks'] : []
  const rows = raw.flatMap((value, index) => {
    const row = readRow(value, index)
    return row === undefined ? [] : [row]
  })
  return { rows, subject: readSubject(data), total: readCount(data['total']) ?? rows.length }
}

/**
 * The tags on a row the reader asked about, lower-cased: the ones a tag listing
 * was filtered by, or the words of a search that are tags in their own right.
 */
export const matchedTags = (subject: BookmarksSubject): ReadonlySet<string> => {
  switch (subject.kind) {
    case 'tags': {
      return new Set(subject.tags.map(tag => tag.toLowerCase()))
    }
    case 'search': {
      return new Set(subject.query.toLowerCase().split(/\s+/).filter(word => word !== ''))
    }
    default: {
      return new Set()
    }
  }
}
