/**
 * The `[pinboard-card]`-tagged console logger and the received-event ring
 * the diagnostic card reads from. The SDK owns the JSON-RPC transport, so
 * this module never observes raw postMessages directly; the shell pushes one
 * entry per SDK lifecycle event it wires (connect result, render, size
 * sends, teardown, openLink).
 *
 * `console` is the forensics channel here on purpose: the server's logging
 * does not exist in the sandboxed iframe the card runs in, and the host's
 * devtools console is where someone debugging a card will look.
 */

const LOG_TAG = '[pinboard-card]'

/** How many recent breadcrumbs the diagnostic card can show; older ones roll off. */
const RING_CAPACITY = 50

/** A `[pinboard-card]`-tagged console logger plus a capped ring of breadcrumbs for the diagnostic card. */
export interface BreadcrumbLog {
  /** All breadcrumbs recorded so far, oldest first, capped at `RING_CAPACITY`. */
  readonly entries: () => readonly Breadcrumb[]
  /** Record a breadcrumb and log it to the console. */
  readonly record: (label: string, detail?: string) => void
  /** The recorded breadcrumbs rendered as the diagnostic card's one-line summary. */
  readonly summary: () => string
}

/** One received-event breadcrumb: a short label plus optional detail (e.g. an error message). */
interface Breadcrumb {
  readonly detail?: string
  readonly label: string
}

/** Create an independent breadcrumb log — one per `CardShell` mount. */
export const createBreadcrumbLog = (): BreadcrumbLog => {
  const ring: Breadcrumb[] = []

  const record = (label: string, detail?: string): void => {
    ring.push(detail === undefined ? { label } : { detail, label })
    if (ring.length > RING_CAPACITY) ring.shift()
    if (detail === undefined) console.log(LOG_TAG, label)
    else console.log(LOG_TAG, label, detail)
  }

  const entries = (): readonly Breadcrumb[] => ring

  const summary = (): string =>
    ring.length === 0 ? '(none)' : ring.map(entry => (entry.detail === undefined ? entry.label : `${entry.label} ${entry.detail}`)).join(' | ')

  return { entries, record, summary }
}
