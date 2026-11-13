/**
 * One watcher for every row past a card's first eight: it says which rows
 * have come near the viewport, so their previews can be asked for then and
 * not when "Show all" draws two hundred of them.
 *
 * One `IntersectionObserver` for the card, not one per row. A browser reports
 * everything a single observer saw change in one callback, so the six rows
 * that scroll into view together are handed over together and go out as one
 * `get_link_previews` call. An observer per row reports each in its own
 * callback, and the first row is sent alone before the second is heard of.
 *
 * Without `IntersectionObserver` (jsdom, an old webview) a watched row is
 * reported at once: asking early is better than never asking.
 */

/**
 * How far outside the viewport a row is when it is reported. Inside a host's
 * cross-origin iframe a margin is ignored, by the browser's own privacy rule,
 * and the row is reported as it comes into view instead; the margin is for
 * everywhere else.
 */
export const NEAR_VIEWPORT_MARGIN = '300px 0px'

export interface RowWatcher {
  /** Report `url` once `element` nears the viewport; returns the way to stop watching. */
  readonly watch: (element: Element, url: string) => () => void
  readonly dispose: () => void
}

/** Whether `first` comes before `second` in the document. */
const precedes = (first: Element, second: Element): number =>
  (first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING) === 0 ? 1 : -1

/** `onNear` gets each callback's rows as one list, in document order. */
export const createRowWatcher = (onNear: (urls: readonly string[]) => void): RowWatcher => {
  const watched = new Map<Element, string>()
  let observer: IntersectionObserver | undefined

  const report = (entries: readonly IntersectionObserverEntry[]): void => {
    const near: Element[] = []
    for (const entry of entries) {
      if (!entry.isIntersecting || !watched.has(entry.target) || near.includes(entry.target)) continue
      near.push(entry.target)
    }
    if (near.length === 0) return
    const urls: string[] = []
    for (const element of near.toSorted(precedes)) {
      const url = watched.get(element)
      watched.delete(element)
      observer?.unobserve(element)
      if (url !== undefined) urls.push(url)
    }
    onNear(urls)
  }

  return {
    dispose: () => {
      observer?.disconnect()
      observer = undefined
      watched.clear()
    },
    watch: (element, url) => {
      if (typeof IntersectionObserver !== 'function') {
        onNear([url])
        return () => undefined
      }
      observer ??= new IntersectionObserver(report, { rootMargin: NEAR_VIEWPORT_MARGIN })
      watched.set(element, url)
      observer.observe(element)
      return () => {
        if (!watched.delete(element)) return
        observer?.unobserve(element)
      }
    },
  }
}
