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
 *   about once more a few seconds later: once per URL, and whatever the
 *   second answer is stands.
 * - If the tool answers `pending` (the lookup was still running when the
 *   server had to reply), the row keeps waiting and the URL is asked about
 *   once more a few seconds later, by when the server has usually finished.
 *   Pending a second time, the row gets nothing.
 * - Those three second chances are counted separately. A URL that used its
 *   busy retry and then met the rate limit still gets the limit's one retry.
 * - If a call comes back "unavailable" the loader stops, with one exception.
 *   Stopped, every URL without an answer has none, and the card settles to
 *   the rows it would have drawn anyway.
 * - The exception: a call that failed only because the bridge had not
 *   connected yet (`not-connected`, `rejected-before-connect`) suspends the
 *   loader instead. It looks stopped, but keeps its queue, and `resume()`,
 *   which the card calls when the bridge does connect, sets it going again.
 *   Once: unavailable after that is final.
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

/** How long a lookup the server was still running is left before it is asked about again. */
export const PENDING_RETRY_DELAY_MS = 5000

/** The longest URL the tool will look up; `MAX_URL_LENGTH` in `previews.py`. */
export const MAX_PREVIEW_URL_LENGTH = 2048

/**
 * Whether the tool would look this URL up: `is_web_url` in `previews.py`, as
 * near as two URL parsers agree. The card asks about nothing else, so a row
 * the server would refuse costs no place in a batch and holds no placeholder.
 */
export const isPreviewableUrl = (url: string): boolean => {
  if (!isWebUrl(url) || url.length > MAX_PREVIEW_URL_LENGTH || /\s/u.test(url)) return false
  try {
    return new URL(url).hostname !== ''
  }
  catch {
    return false
  }
}

/** Reasons a call was unavailable that the bridge connecting may yet cure. */
const NOT_FINAL: ReadonlySet<string> = new Set(['not-connected', 'rejected-before-connect'])

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
  /** Queue these URLs, in this order, skipping any already asked about and any the tool would refuse. */
  readonly request: (urls: readonly string[]) => void
  readonly state: (url: string) => PreviewState
  /** Call `listener` whenever any URL's state changes; returns the unsubscribe. */
  readonly subscribe: (listener: () => void) => () => void
  /** Bumps on every change: a snapshot for `useSyncExternalStore`. */
  readonly version: () => number
  /**
   * The bridge has connected: if the loader was suspended for want of it,
   * carry on from where it left off. Does nothing otherwise.
   */
  readonly resume: () => void
  /** Stop for good: nothing further is sent, and nothing more changes. */
  readonly dispose: () => void
}

export const createPreviewLoader = ({ callTool, record = () => undefined, setTimer = setTimeout }: PreviewLoaderOptions): PreviewLoader => {
  const settled = new Map<string, PreviewState>()
  /** Everything requested and not yet settled, queued or in flight. */
  const asked = new Set<string>()
  const queue: string[] = []
  // One second chance of each kind per URL, counted apart: using one must
  // not use up another.
  const rateRetried = new Set<string>()
  const busyRetried = new Set<string>()
  const pendingRetried = new Set<string>()
  const listeners = new Set<() => void>()
  let version = 0
  let busy = false
  let paused = false
  let stopped = false
  let suspended = false
  let resumed = false
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
    if (busy || paused || stopped || suspended || disposed || queue.length === 0) return
    busy = true
    const batch = queue.splice(0, PREVIEW_BATCH_SIZE)
    record('previews:batch', String(batch.length))
    const outcome = await callTool(PREVIEW_TOOL, { urls: batch })
    busy = false
    if (disposed) return

    if (outcome.status === 'unavailable') {
      if (!resumed && NOT_FINAL.has(outcome.reason)) {
        // Not a refusal, only no way through yet. The batch goes back to the
        // head of the queue for `resume()`.
        suspended = true
        queue.unshift(...batch)
        record('previews:suspended', outcome.reason)
      }
      else {
        stop(outcome.reason)
      }
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
    const unfinished = reply.busy.filter(url => !busyRetried.has(url))
    if (unfinished.length > 0) {
      for (const url of unfinished) busyRetried.add(url)
      record('previews:busy', `${String(unfinished.length)} again in ${String(BUSY_RETRY_DELAY_MS)}ms`)
      setTimer(() => {
        if (stopped || disposed) return
        for (const url of unfinished) asked.add(url)
        queue.push(...unfinished)
        void pump()
      }, BUSY_RETRY_DELAY_MS)
    }
    // A URL that already has an answer (a busy retry that met the rate limit
    // or the deadline) keeps it; only rows still waiting go round again.
    const stillWaiting = (url: string): boolean => {
      if (!settled.has(url)) return true
      asked.delete(url)
      return false
    }
    // Still running on the server, which finishes the lookup whether or not
    // anyone is waiting: the row keeps its place, and one more ask a few
    // seconds on usually finds the answer cached. The queue is not paused.
    const running = reply.pending.filter(stillWaiting)
    const askAgain = running.filter(url => !pendingRetried.has(url))
    for (const url of running) {
      if (askAgain.includes(url)) continue
      asked.delete(url)
      settled.set(url, NONE)
    }
    if (askAgain.length > 0) {
      for (const url of askAgain) pendingRetried.add(url)
      record('previews:pending', `${String(askAgain.length)} again in ${String(PENDING_RETRY_DELAY_MS)}ms`)
      setTimer(() => {
        if (stopped || disposed) return
        queue.push(...askAgain)
        void pump()
      }, PENDING_RETRY_DELAY_MS)
    }
    const limited = reply.rateLimited.filter(stillWaiting)
    const again = limited.filter(url => !rateRetried.has(url))
    if (again.length < limited.length) {
      // Limited twice running: the wait was not enough, so give up quietly.
      stop('rate-limited')
      changed()
      return
    }
    if (again.length > 0) {
      for (const url of again) rateRetried.add(url)
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
        if (!isPreviewableUrl(url) || settled.has(url) || asked.has(url)) continue
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
    resume: () => {
      if (!suspended || stopped || disposed) return
      suspended = false
      resumed = true
      record('previews:resumed')
      changed()
      void pump()
    },
    state: url => settled.get(url) ?? (stopped || suspended || disposed || !isPreviewableUrl(url) ? NONE : PENDING),
    subscribe: (listener) => {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
    version: () => version,
  }
}
