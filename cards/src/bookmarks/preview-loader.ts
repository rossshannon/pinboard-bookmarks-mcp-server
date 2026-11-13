/**
 * The queue behind the bookmarks card's link previews: which URLs to ask the
 * `get_link_previews` tool about, when, and what came back. No React and no
 * DOM, so the rules are testable as rules:
 *
 * - Bookmarks are asked about in batches of at most eight, one batch in
 *   flight at a time, in the order they were requested. Requests made in the
 *   same tick (a card's first eight rows mounting) go out as one batch.
 * - Each is asked about as the server's own pair: its URL and the
 *   `preview_token` the server sent with it. The tool looks up nothing else,
 *   so a bookmark that came without a token is not asked about.
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
 * - The exception: a call that failed only for want of a bridge
 *   (`not-connected`, `rejected-before-connect`). If the bridge has connected
 *   since that call was sent, the batch is sent again at once, down the
 *   bridge. If it has not, the loader is suspended: it looks stopped, but
 *   keeps its queue, and carries on when the card says the bridge has
 *   connected (`connected()`). A connect is remembered whenever it comes, so
 *   one that lands while the first call is still in flight is not lost. A
 *   call sent after the connect that still finds no way through is final.
 *
 * The loader is in exactly one phase at a time:
 *
 *   idle ──request──▶ calling ──answer──▶ idle
 *                        │ ├─ rate-limited ─▶ waiting ──timer──▶ idle
 *                        │ ├─ no bridge yet ─▶ suspended ──connected()──▶ idle
 *                        │ └─ unavailable ──▶ stopped
 *   any ──dispose()──▶ disposed
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

/** A bookmark to ask about: its URL and the token the server sent with it. */
export interface PreviewItem {
  readonly url: string
  readonly token: string
}

/**
 * Where the loader is. `calling` has a batch in flight; `waiting` is sitting
 * out the bridge's rate limit; `suspended` is waiting for a bridge to call
 * through; `stopped` and `disposed` are for good.
 */
type Phase = 'calling' | 'disposed' | 'idle' | 'stopped' | 'suspended' | 'waiting'

/** The three ways an answer can ask to be asked again. Each URL gets one of each. */
type RetryKind = 'busy' | 'pending' | 'rate-limited'

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
  /** Queue these bookmarks, in this order, skipping any already asked about and any the tool would refuse. */
  readonly request: (items: readonly PreviewItem[]) => void
  readonly state: (url: string) => PreviewState
  /** Call `listener` whenever any URL's state changes; returns the unsubscribe. */
  readonly subscribe: (listener: () => void) => () => void
  /** Bumps on every change: a snapshot for `useSyncExternalStore`. */
  readonly version: () => number
  /**
   * The bridge has connected. Remembered from then on; and if the loader was
   * suspended for want of it, it carries on from where it left off.
   */
  readonly connected: () => void
  /** Stop for good: nothing further is sent, and nothing more changes. */
  readonly dispose: () => void
}

export const createPreviewLoader = ({ callTool, record = () => undefined, setTimer = setTimeout }: PreviewLoaderOptions): PreviewLoader => {
  const settled = new Map<string, PreviewState>()
  /** The token each requested URL came with. */
  const tokens = new Map<string, string>()
  /** Everything requested and not yet settled, queued or in flight. */
  const asked = new Set<string>()
  const queue: string[] = []
  const retried: Readonly<Record<RetryKind, Set<string>>> = { 'busy': new Set(), 'pending': new Set(), 'rate-limited': new Set() }
  const listeners = new Set<() => void>()
  let version = 0
  let phase: Phase = 'idle'
  /** Whether the bridge is known to have connected. A fact, not a phase: it is true in any of them. */
  let bridgeConnected = false

  const over = (): boolean => phase === 'stopped' || phase === 'disposed'

  const changed = (): void => {
    version += 1
    for (const listener of listeners) listener()
  }

  const stop = (why: string): void => {
    phase = 'stopped'
    queue.length = 0
    asked.clear()
    record('previews:stopped', why)
  }

  /**
   * Give these URLs their one second chance of this kind: `again` puts them
   * back in the queue after `delayMs`, unless the loader has finished by
   * then. Returns the URLs that had already used theirs, for the caller to
   * settle its own way.
   */
  const retryOnce = (kind: RetryKind, urls: readonly string[], delayMs: number, again: (fresh: readonly string[]) => void): readonly string[] => {
    const fresh = urls.filter(url => !retried[kind].has(url))
    if (fresh.length > 0) {
      for (const url of fresh) retried[kind].add(url)
      record(`previews:${kind}`, `${String(fresh.length)} again in ${String(delayMs)}ms`)
      setTimer(() => {
        if (over()) return
        again(fresh)
        void pump()
      }, delayMs)
    }
    return urls.filter(url => !fresh.includes(url))
  }

  const pump = async (): Promise<void> => {
    if (phase !== 'idle' || queue.length === 0) return
    phase = 'calling'
    const batch = queue.splice(0, PREVIEW_BATCH_SIZE)
    const sentWithBridge = bridgeConnected
    record('previews:batch', String(batch.length))
    const outcome = await callTool(PREVIEW_TOOL, { items: batch.map(url => ({ token: tokens.get(url) ?? '', url })) })
    if (phase !== 'calling') return
    phase = 'idle'

    if (outcome.status === 'unavailable') {
      if (NOT_FINAL.has(outcome.reason) && !sentWithBridge) {
        // Not a refusal, only no way through when it was sent. The batch goes
        // back to the head of the queue: for now, if the bridge has connected
        // in the meantime, and otherwise for when it does.
        queue.unshift(...batch)
        if (bridgeConnected) {
          record('previews:resent', outcome.reason)
          void pump()
          return
        }
        phase = 'suspended'
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
    // round once more after a short wait. The queue is not held for it;
    // other rows have no reason to wait on this one's thumbnail.
    retryOnce('busy', reply.busy, BUSY_RETRY_DELAY_MS, (fresh) => {
      for (const url of fresh) asked.add(url)
      queue.push(...fresh)
    })
    // A URL that already has an answer (a busy retry that met the rate limit
    // or the deadline) keeps it; only rows still waiting go round again.
    const stillWaiting = (url: string): boolean => {
      if (!settled.has(url)) return true
      asked.delete(url)
      return false
    }
    // Still running on the server, which finishes the lookup whether or not
    // anyone is waiting: the row keeps its place, and one more ask a few
    // seconds on usually finds the answer cached. The queue is not held.
    const stillPending = retryOnce('pending', reply.pending.filter(stillWaiting), PENDING_RETRY_DELAY_MS, (fresh) => {
      queue.push(...fresh)
    })
    for (const url of stillPending) {
      asked.delete(url)
      settled.set(url, NONE)
    }
    // The bridge's rate limit: those URLs go to the head of the queue, and
    // the whole queue waits the limit out.
    const limited = reply.rateLimited.filter(stillWaiting)
    if (limited.some(url => retried['rate-limited'].has(url))) {
      // Limited twice running: the wait was not enough, so give up quietly.
      stop('rate-limited')
      changed()
      return
    }
    retryOnce('rate-limited', limited, reply.retryAfterMs, () => {
      if (phase === 'waiting') phase = 'idle'
    })
    if (limited.length > 0) {
      queue.unshift(...limited)
      phase = 'waiting'
    }
    changed()
    void pump()
  }

  return {
    connected: () => {
      bridgeConnected = true
      if (phase !== 'suspended') return
      phase = 'idle'
      record('previews:resumed')
      changed()
      void pump()
    },
    dispose: () => {
      phase = 'disposed'
      listeners.clear()
    },
    request: (items) => {
      if (over()) return
      let added = false
      for (const { token, url } of items) {
        if (token === '' || !isPreviewableUrl(url) || settled.has(url) || asked.has(url)) continue
        tokens.set(url, token)
        asked.add(url)
        queue.push(url)
        added = true
      }
      // A microtask later, so every request made in this tick shares a batch.
      if (added) {
        queueMicrotask(() => {
          void pump()
        })
      }
    },
    state: url => settled.get(url) ?? (over() || phase === 'suspended' || !isPreviewableUrl(url) ? NONE : PENDING),
    subscribe: (listener) => {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
    version: () => version,
  }
}
