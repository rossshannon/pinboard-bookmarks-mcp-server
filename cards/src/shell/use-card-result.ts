/**
 * Result extraction: `structuredContent` > `toolOutput` >
 * `JSON.parse(content[0].text)`, in that priority order. A parse failure (or
 * no usable field at all) yields no result, so the "waiting for the tool
 * result" state persists rather than crashing.
 *
 * A failed call is extracted separately and first: it carries its reason as
 * prose in `content[0].text` and no `structuredContent` at all, so the
 * extraction above would find nothing and the card would sit waiting until it
 * escalated to the diagnostic box — a host-debugging panel for what is really
 * "you are not signed in".
 *
 * `toolOutput` is not a field the MCP Apps spec's `ui/notifications/tool-result`
 * params carry (see `McpUiToolResultNotification` in `@modelcontextprotocol/ext-apps`);
 * the tier is kept because a non-conformant host may still deliver it there.
 * `window.openai.toolOutput` (the ChatGPT/Codex fast path) is a different,
 * already-unwrapped value — `CardShell` uses it directly as render data, not
 * through this extraction.
 */

import { useCallback, useState } from 'react'

import { isRecord } from '../guards.ts'

export interface CardResultError {
  /** `content[0].text` from the failed result; empty when the result carried no text to show. */
  readonly text: string
}

/** The shape `ontoolresult` params (and any host-delivered result-like object) can plausibly carry. */
export interface CardToolResultLike {
  // The index signature keeps this assignable from the SDK's real (block-type
  // union) content array without tripping TS's "weak type" detection: some
  // block variants (image, audio, …) share no properties with `{ text? }` at
  // all, which a plain all-optional element type rejects outright.
  readonly content?: ReadonlyArray<{ readonly [key: string]: unknown, readonly text?: string }> | undefined
  readonly isError?: boolean | undefined
  readonly structuredContent?: Record<string, unknown> | undefined
  readonly toolOutput?: Record<string, unknown> | undefined
}

/** Extract render data from a tool-result-shaped object: `structuredContent` > `toolOutput` > parsed `content[0].text`. */
export const extractToolResult = (result?: CardToolResultLike | null): Record<string, unknown> | undefined => {
  if (!result) return undefined
  if (result.structuredContent) return result.structuredContent
  if (result.toolOutput) return result.toolOutput
  const text = result.content?.[0]?.text
  if (typeof text !== 'string') return undefined
  try {
    const parsed: unknown = JSON.parse(text)
    return isRecord(parsed) ? parsed : undefined
  }
  catch {
    return undefined
  }
}

/**
 * Checked before the data extraction, not after: a result flagged `isError` is a
 * failure whatever else it carries, so a tool that one day sends both cannot
 * have its failure outranked by a payload.
 */
export const extractToolError = (result?: CardToolResultLike | null): CardResultError | undefined => {
  if (result?.isError !== true) return undefined
  const text = result.content?.[0]?.text
  // An empty text still draws a failure state — the generic one.
  return { text: typeof text === 'string' ? text : '' }
}

export interface UseCardResult {
  /** The current render data, or `undefined` before anything has rendered. */
  readonly data: Record<string, unknown> | undefined
  /** The failure the card should draw, or `undefined` while nothing has failed. */
  readonly error: CardResultError | undefined
  /** Bumps on every successful render (fast path, extracted tool-result, or failure), including re-delivery — the sizing settle-window guard. */
  readonly generation: number
  /** Fast path: `window.openai.toolOutput` is already the final data, no extraction. Always renders. */
  readonly recordData: (data: Record<string, unknown>) => void
  /** `ontoolresult`-shaped delivery: extracts a failure first, then data. Returns whether it rendered (an unparseable success leaves the prior state — "waiting" persists). */
  readonly recordResult: (result?: CardToolResultLike | null) => boolean
}

/** Owns the shell's render state: failure-then-data extraction priority, and the render generation counter sizing re-arms on. */
export const useCardResult = (): UseCardResult => {
  const [data, setData] = useState<Record<string, unknown>>()
  const [error, setError] = useState<CardResultError>()
  const [generation, setGeneration] = useState(0)

  const recordData = useCallback((next: Record<string, unknown>): void => {
    setData(next)
    // Only the latest delivery is shown, so data arriving after a failure
    // clears it rather than rendering both.
    setError(undefined)
    setGeneration(current => current + 1)
  }, [])

  const recordResult = useCallback((result?: CardToolResultLike | null): boolean => {
    const failure = extractToolError(result)
    if (failure !== undefined) {
      setError(failure)
      setData(undefined)
      setGeneration(current => current + 1)
      return true
    }
    const extracted = extractToolResult(result)
    if (extracted === undefined) return false
    recordData(extracted)
    return true
  }, [recordData])

  return { data, error, generation, recordData, recordResult }
}
