/**
 * The queue behind the bookmarks card's link previews: which URLs to ask the
 * `get_link_previews` tool about, when, and what came back. No React and no
 * DOM, so the rules are testable as rules:
 *
 * - URLs are asked about in batches of at most eight, one batch in flight at
 *   a time, in the order they were requested. Requests made in the same tick
 *   (a card's first eight rows mounting) go out as one batch.
 * - A URL is asked about once. Its answer, a preview or nothing, is kept for
 *   the life of the card.
 * - If the tool says the bridge's rate limit was reached, the queue pauses
 *   for as long as the tool says, then asks about those URLs once more. If
 *   they come back rate-limited again the loader stops: the limit is still in
 *   force, and a card is not worth a retry loop.
 * - If the tool marks an answer `retry` (the bridge was too busy to make that
 *   page's thumbnails), what arrived is drawn at once and the URL is asked
 *   about once more a few seconds later, by the same quiet route: once per
 *   URL, and whatever the second answer is stands.
 * - If a call comes back "unavailable" (no host support, a refusal, a
 *   timeout) the loader stops too. Stopped, every URL without an answer has
 *   none, and the card settles to the rows it would have drawn anyway.
 */

import type { ToolCaller } from '../shell/tool-caller.ts'
import type { LinkPreview } from './read-previews.ts'

import { isWebUrl } from '../shell/safe-url.ts'
import { readPreviews } from './read-previews.ts'

/** The app-only tool the card calls; see `main.py`. */
export const PREVIEW_TOOL = 'get_link_previews'

/** The most URLs the tool takes in one call. */
export const PREVIEW_BATCH_SIZE = 8

/** How long a busy bridge is left before its unfinished previews are asked about again. */
export const BUSY_RETRY_DELAY_MS = 5000

export type PreviewState =
  /** Not asked about yet, or the answer is on its way: worth holding a place for. */
  | { readonly kind: 'pending' }
  /** Nothing to draw, and nothing coming. */
  | { readonly kind: 'none' }
  | { readonly kind: 'ready', readonly preview: LinkPreview }

const PENDING: PreviewState = { kind: 'pending' }
const NONE: PreviewState = { kind: 'none' }

export interface PreviewLoaderOptions {
  readonly callTool: ToolCaller['callTool']
  /** A breadcrumb for each turn the queue takes; optional so a test need not supply one. */
  readonly record?: (label: string, detail?: string) => void
  /** `setTimeout`, unless a test wants the clock. */
  readonly setTimer?: (callback: () => void, delayMs: number) => unknown
}

export interface PreviewLoader {
  /** Queue these URLs, skipping any already asked about and anything that is not a web URL. */
  readonly request: (urls: readonly string[]) => void
  readonly state: (url: string) => PreviewState
  /** Call `listener` whenever any URL's state changes; returns the unsubscribe. */
  readonly subscribe: (listener: () => void) => () => void
  /** Bumps on every change: a snapshot for `useSyncExternalStore`. */
  readonly version: () => number
  /** Stop for good: nothing further is sent, and nothing more changes. */
  readonly dispose: () => void
}

export const createPreviewLoader = ({ callTool, record = () => undefined, setTimer = setTimeout }: PreviewLoaderOptions): PreviewLoader => {
  const settled = new Map<string, PreviewState>()
  /** Everything requested and not yet settled, queued or in flight. */
  const asked = new Set<string>()
  const queue: string[] = []
  const retried = new Set<string>()
  const listeners = new Set<() => void>()
  let version = 0
  let busy = false
  let paused = false
  let stopped = false
  let disposed = false
  let pumpScheduled = false

  const changed = (): void => {
    version += 1
    for (const listener of listeners) listener()
  }

  const stop = (why: string): void => {
    stopped = true
    queue.length = 0
    asked.clear()
    record('previews:stopped', why)
  }

  const pump = async (): Promise<void> => {
    pumpScheduled = false
    if (busy || paused || stopped || disposed || queue.length === 0) return
    busy = true
    const batch = queue.splice(0, PREVIEW_BATCH_SIZE)
    record('previews:batch', String(batch.length))
    const outcome = await callTool(PREVIEW_TOOL, { urls: batch })
    busy = false
    if (disposed) return

    if (outcome.status === 'unavailable') {
      stop(outcome.reason)
      changed()
      return
    }

    const reply = readPreviews(outcome.data, batch)
    for (const [url, preview] of reply.settled) {
      asked.delete(url)
      // A second ask that comes back empty does not take away what the first gave.
      if (preview === undefined && settled.get(url)?.kind === 'ready') continue
      settled.set(url, preview === undefined ? NONE : { kind: 'ready', preview })
    }
    // A busy bridge: the row keeps what it was just given, and the URL goes
    // round once more after a short wait. The queue is not paused for it;
    // other rows have no reason to wait on this one's thumbnail.
    const unfinished = reply.busy.filter(url => !retried.has(url))
    if (unfinished.length > 0) {
      for (const url of unfinished) retried.add(url)
      record('previews:busy', `${String(unfinished.length)} again in ${String(BUSY_RETRY_DELAY_MS)}ms`)
      setTimer(() => {
        if (stopped || disposed) return
        for (const url of unfinished) asked.add(url)
        queue.push(...unfinished)
        void pump()
      }, BUSY_RETRY_DELAY_MS)
    }
    // A URL that already has an answer (a busy retry that met the rate limit)
    // keeps it; only rows still waiting count towards the limit's retry.
    const limited = reply.rateLimited.filter((url) => {
      if (!settled.has(url)) return true
      asked.delete(url)
      return false
    })
    const again = limited.filter(url => !retried.has(url))
    if (again.length < limited.length) {
      // Limited twice running: the wait was not enough, so give up quietly.
      stop('rate-limited')
      changed()
      return
    }
    if (again.length > 0) {
      for (const url of again) retried.add(url)
      queue.unshift(...again)
      paused = true
      record('previews:rate-limited', `${String(again.length)} held ${String(reply.retryAfterMs)}ms`)
      setTimer(() => {
        paused = false
        void pump()
      }, reply.retryAfterMs)
    }
    changed()
    void pump()
  }

  return {
    dispose: () => {
      disposed = true
      listeners.clear()
    },
    request: (urls) => {
      if (stopped || disposed) return
      let added = false
      for (const url of urls) {
        if (!isWebUrl(url) || settled.has(url) || asked.has(url)) continue
        asked.add(url)
        queue.push(url)
        added = true
      }
      // A microtask later, so every request made in this tick shares a batch.
      if (added && !pumpScheduled) {
        pumpScheduled = true
        queueMicrotask(() => {
          void pump()
        })
      }
    },
    state: url => settled.get(url) ?? (stopped || disposed || !isWebUrl(url) ? NONE : PENDING),
    subscribe: (listener) => {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
    version: () => version,
  }
}
