/**
 * Every card's fixtures, keyed by the card's `ui://` name and then by
 * scenario. Each is a whole tool result as a host delivers it: a success
 * carries `structuredContent`, a failure `isError` and its text, so the same
 * entry drives the jsdom suites, the browser checks and the capture gallery.
 *
 * Kept as JSON so the Python suite can read them too, and check that what
 * the cards are tested against is what the tools actually return.
 */

import type { CardToolResultLike } from '../src/shell/use-card-result.ts'

import bookmarks from './bookmarks.json' with { type: 'json' }
import tags from './tags.json' with { type: 'json' }

export type FixtureSet = Readonly<Record<string, CardToolResultLike>>

export const CARD_FIXTURES: Readonly<Record<'bookmarks' | 'tags', FixtureSet>> = { bookmarks, tags }

/** One fixture's `structuredContent`, for the jsdom suites, which render a card component directly. */
export const fixtureData = (card: keyof typeof CARD_FIXTURES, scenario: string): Record<string, unknown> => {
  const result = CARD_FIXTURES[card][scenario]
  if (result === undefined) throw new Error(`no ${card} fixture named ${scenario}`)
  return result.structuredContent ?? {}
}

/** One fixture's failure, for the error scenarios. */
export const fixtureError = (card: keyof typeof CARD_FIXTURES, scenario: string): { text: string } => {
  const result = CARD_FIXTURES[card][scenario]
  const text = result?.content?.[0]?.text
  if (result?.isError !== true || typeof text !== 'string') throw new Error(`${card} fixture ${scenario} is not a failure`)
  return { text }
}
