/**
 * Narrowing `get_link_previews`' `structuredContent` into what a row draws.
 *
 * The tool answers `{ previews: [{ url, status, title, description,
 * site_name, theme_color, image, favicon, retry? }], retry_after_seconds? }`,
 * one entry per URL asked about. `retry: true` marks an entry the bridge was
 * too busy to finish: what it carries is drawn, and the URL is worth asking
 * about once more. Read as defensively as the bookmarks are: the
 * server sanitises what it sends, and this assumes it did not. Only URLs the
 * card asked about are read, every string is a string or it is nothing, and
 * an image is drawable (`safe-image.ts`) or it is dropped.
 *
 * `title` is not read: a row's title is the one its owner gave the bookmark
 * in Pinboard, not the page's. Nor is `theme_color`: a site's colour against
 * a host's palette is a contrast problem the card does not need.
 */

import type { SafeImageSource } from '../shell/safe-image.ts'

import { isRecord, isUnknownArray, readText } from '../guards.ts'
import { readImage } from '../shell/safe-image.ts'

export interface LinkPreview {
  readonly description: string | undefined
  readonly favicon: SafeImageSource | undefined
  readonly image: SafeImageSource | undefined
  readonly siteName: string | undefined
}

export interface PreviewReply {
  /** Every URL the tool settled: a preview, or `undefined` for a page with nothing to show. */
  readonly settled: ReadonlyMap<string, LinkPreview | undefined>
  /** URLs the tool could not look up yet because the bridge's rate limit was reached. */
  readonly rateLimited: readonly string[]
  /** Settled URLs whose thumbnails the bridge was too busy to make: worth one more ask. */
  readonly busy: readonly string[]
  /** How long the tool said to leave it before asking again. */
  readonly retryAfterMs: number
}

/** The wait when the tool names none, and the range one it does name is held to. */
const DEFAULT_RETRY_AFTER_MS = 30_000
const MIN_RETRY_AFTER_MS = 1000
const MAX_RETRY_AFTER_MS = 120_000

/** Longer than the server ever sends; a backstop, since the text is clamped by CSS anyway. */
const MAX_DESCRIPTION_LENGTH = 600
const MAX_SITE_NAME_LENGTH = 120

const readCapped = (value: unknown, limit: number): string | undefined => {
  const text = readText(value)?.trim()
  if (text === undefined || text === '') return undefined
  return text.length > limit ? `${text.slice(0, limit - 1).trimEnd()}…` : text
}

const readRetryAfter = (value: unknown): number => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return DEFAULT_RETRY_AFTER_MS
  return Math.min(Math.max(value * 1000, MIN_RETRY_AFTER_MS), MAX_RETRY_AFTER_MS)
}

const readPreview = (entry: Record<string, unknown>): LinkPreview | undefined => {
  const preview: LinkPreview = {
    description: readCapped(entry['description'], MAX_DESCRIPTION_LENGTH),
    favicon: readImage(entry['favicon']),
    image: readImage(entry['image']),
    siteName: readCapped(entry['site_name'], MAX_SITE_NAME_LENGTH),
  }
  const empty = preview.description === undefined && preview.favicon === undefined && preview.image === undefined && preview.siteName === undefined
  return empty ? undefined : preview
}

/**
 * Read a `get_link_previews` result against the URLs that were asked about.
 * Every asked URL comes back settled or rate-limited: one the tool left out,
 * or answered with a status the card does not know, settles as nothing.
 */
export const readPreviews = (data: Record<string, unknown>, asked: readonly string[]): PreviewReply => {
  const wanted = new Set(asked)
  const settled = new Map<string, LinkPreview | undefined>()
  const rateLimited: string[] = []
  const busy: string[] = []
  const entries = isUnknownArray(data['previews']) ? data['previews'] : []
  for (const entry of entries) {
    if (!isRecord(entry)) continue
    const url = entry['url']
    if (typeof url !== 'string' || !wanted.has(url) || settled.has(url) || rateLimited.includes(url)) continue
    if (entry['status'] === 'rate_limited') {
      rateLimited.push(url)
      continue
    }
    settled.set(url, entry['status'] === 'ok' ? readPreview(entry) : undefined)
    if (entry['retry'] === true && (entry['status'] === 'ok' || entry['status'] === 'no_data')) busy.push(url)
  }
  for (const url of asked) {
    if (!settled.has(url) && !rateLimited.includes(url)) settled.set(url, undefined)
  }
  return { busy, rateLimited, retryAfterMs: readRetryAfter(data['retry_after_seconds']), settled }
}
