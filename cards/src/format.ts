/**
 * Formatting leaf for the cards: dates in the reader's zone, and counts.
 *
 * Every date a card prints is in the reader's own zone. A card runs in an
 * iframe in the host's browser, so the zone `Intl` resolves there is the
 * reader's device zone; it is read on every call rather than pinned, so a test
 * that fixes `TZ` is never fighting a value captured at load. The tool's own
 * output keeps the UTC ISO string for the agent.
 */

export const viewerTimeZone = (): string => new Intl.DateTimeFormat().resolvedOptions().timeZone

/** Keyed by purpose and zone: each formatter is worth building once. */
const formatterCache = new Map<string, Intl.DateTimeFormat>()

const cachedFormatter = (purpose: string, options: { readonly timeZone: string } & Intl.DateTimeFormatOptions): Intl.DateTimeFormat => {
  const key = `${purpose}|${options.timeZone}`
  const cached = formatterCache.get(key)
  if (cached) return cached
  const formatter = new Intl.DateTimeFormat('en-GB', options)
  formatterCache.set(key, formatter)
  return formatter
}

const partOf = (parts: readonly Intl.DateTimeFormatPart[], type: Intl.DateTimeFormatPartTypes): string =>
  parts.find(part => part.type === type)?.value ?? ''

/** `undefined` for an absent or unparseable value, so a row renders around it as if the field were never sent. */
const parseInstant = (value: unknown): Date | undefined => {
  if (typeof value !== 'string') return undefined
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? undefined : parsed
}

/** `12 Oct 2026`, on the reader's calendar. */
export const shortDate = (value: unknown, timeZone = viewerTimeZone()): string | undefined => {
  const parsed = parseInstant(value)
  if (parsed === undefined) return undefined
  const parts = cachedFormatter('date', { day: 'numeric', month: 'short', timeZone, year: 'numeric' }).formatToParts(parsed)
  return `${partOf(parts, 'day')} ${partOf(parts, 'month')} ${partOf(parts, 'year')}`
}

/**
 * The reader's zone as a short name at the given instant — `BST`, `GMT+13`,
 * `UTC` — since the name changes with daylight saving.
 */
export const zoneName = (atMs: number, timeZone = viewerTimeZone()): string =>
  partOf(cachedFormatter('zone', { timeZone, timeZoneName: 'short' }).formatToParts(atMs), 'timeZoneName')

/**
 * The same instant in full, for the `title` of every element that prints a
 * date: `12 Oct 2026, 6:51:07am BST (Europe/London)`. A row shows the day
 * alone, and the day can differ from the one the bookmark was saved on for a
 * reader in another zone, so hovering says exactly when.
 */
export const instantTitle = (value: unknown, timeZone = viewerTimeZone()): string | undefined => {
  const parsed = parseInstant(value)
  if (parsed === undefined) return undefined
  const parts = cachedFormatter('title', {
    day: 'numeric',
    hour: 'numeric',
    hourCycle: 'h12',
    minute: '2-digit',
    month: 'short',
    second: '2-digit',
    timeZone,
    year: 'numeric',
  }).formatToParts(parsed)
  const clock = `${partOf(parts, 'hour')}:${partOf(parts, 'minute')}:${partOf(parts, 'second')}${partOf(parts, 'dayPeriod').toLowerCase()}`
  const zone = zoneName(parsed.getTime(), timeZone)
  const named = zone === timeZone ? zone : `${zone} (${timeZone})`
  return `${partOf(parts, 'day')} ${partOf(parts, 'month')} ${partOf(parts, 'year')}, ${clock} ${named}`
}

const countFormatter = new Intl.NumberFormat('en-GB')

/** `1 bookmark`, `1,204 bookmarks`. */
export const countOf = (count: number, singular: string, plural = `${singular}s`): string =>
  `${countFormatter.format(count)} ${count === 1 ? singular : plural}`

/** A grouped number on its own: `1,204`. */
export const formatCount = (count: number): string => countFormatter.format(count)
