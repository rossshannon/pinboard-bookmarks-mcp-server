/**
 * Runtime type guards for the cards, used to narrow `<CardShell>`'s raw
 * `Record<string, unknown>` render data. A card never asserts a type: it reads
 * each field through one of these and draws what narrowed.
 */

export const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)

/** `Array.isArray` narrows to `any[]`; this keeps elements `unknown` for a reader to narrow properly. */
export const isUnknownArray = (value: unknown): value is readonly unknown[] => Array.isArray(value)

/** An empty string, whitespace and a malformed value all collapse to `undefined`, so a card never draws a blank field. */
export const readText = (value: unknown): string | undefined =>
  typeof value === 'string' && value.trim() !== '' ? value : undefined

/** A finite, non-negative whole number, else `undefined`. */
export const readCount = (value: unknown): number | undefined =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : undefined

/** The strings of an array, skipping anything else and anything blank. */
export const readTextList = (value: unknown): readonly string[] =>
  isUnknownArray(value) ? value.flatMap((item) => {
    const text = readText(item)
    return text === undefined ? [] : [text]
  }) : []
