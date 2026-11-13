import { afterEach, describe, expect, it, vi } from 'vitest'

import { createRowWatcher, NEAR_VIEWPORT_MARGIN } from './row-watcher.ts'

interface StubObserver {
  readonly callback: IntersectionObserverCallback
  disconnected: boolean
  readonly options: IntersectionObserverInit | undefined
  readonly targets: Element[]
}

const observers: StubObserver[] = []

const stubIntersectionObserver = (): void => {
  observers.length = 0
  vi.stubGlobal('IntersectionObserver', class {
    private readonly record: StubObserver
    constructor(callback: IntersectionObserverCallback, options?: IntersectionObserverInit) {
      this.record = { callback, disconnected: false, options, targets: [] }
      observers.push(this.record)
    }

    disconnect(): void {
      this.record.disconnected = true
      this.record.targets.length = 0
    }

    observe(target: Element): void {
      this.record.targets.push(target)
    }

    unobserve(target: Element): void {
      const index = this.record.targets.indexOf(target)
      if (index !== -1) this.record.targets.splice(index, 1)
    }
  })
}

const theObserver = (): StubObserver => {
  const [observer] = observers
  if (!observer) throw new Error('no observer was made')
  return observer
}

/** Report these rows to the one observer, as a browser would in a single callback. */
const report = (entries: ReadonlyArray<readonly [Element, boolean]>): void => {
  const observer = theObserver()
  observer.callback(
    entries.map(([target, isIntersecting]) => ({ isIntersecting, target }) as IntersectionObserverEntry),
    observer as unknown as IntersectionObserver,
  )
}

/** Rows in a list in the document, so document order means something. */
const rowsInDocument = (count: number): HTMLLIElement[] => {
  const list = document.createElement('ol')
  document.body.append(list)
  return Array.from({ length: count }, () => {
    const row = document.createElement('li')
    list.append(row)
    return row
  })
}

const urlOf = (index: number): string => `https://example.com/${String(index)}`

describe('createRowWatcher', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.replaceChildren()
  })

  it('watches every row with one observer, made on first use', () => {
    stubIntersectionObserver()
    const watcher = createRowWatcher(vi.fn())
    expect(observers).toHaveLength(0)

    const rows = rowsInDocument(5)
    for (const [index, row] of rows.entries()) watcher.watch(row, urlOf(index))

    expect(observers).toHaveLength(1)
    expect(theObserver().targets).toEqual(rows)
    expect(theObserver().options).toEqual({ rootMargin: NEAR_VIEWPORT_MARGIN })
  })

  it('hands over the rows of one callback as one list, in document order', () => {
    stubIntersectionObserver()
    const onNear = vi.fn()
    const watcher = createRowWatcher(onNear)
    const rows = rowsInDocument(6)
    for (const [index, row] of rows.entries()) watcher.watch(row, urlOf(index))

    report([[rows[4] as Element, true], [rows[1] as Element, true], [rows[5] as Element, false], [rows[2] as Element, true]])

    expect(onNear).toHaveBeenCalledExactlyOnceWith([urlOf(1), urlOf(2), urlOf(4)])
    // Reported once: those rows are let go, the others still watched.
    expect(theObserver().targets).toEqual([rows[0], rows[3], rows[5]])
  })

  it('says nothing for a callback in which nothing came near', () => {
    stubIntersectionObserver()
    const onNear = vi.fn()
    const watcher = createRowWatcher(onNear)
    const [row] = rowsInDocument(1)
    watcher.watch(row as Element, urlOf(0))

    report([[row as Element, false]])

    expect(onNear).not.toHaveBeenCalled()
    expect(theObserver().targets).toEqual([row])
  })

  it('reports a row once, even if the browser names it twice or again later', () => {
    stubIntersectionObserver()
    const onNear = vi.fn()
    const watcher = createRowWatcher(onNear)
    const [row] = rowsInDocument(1)
    watcher.watch(row as Element, urlOf(0))

    report([[row as Element, true], [row as Element, true]])
    report([[row as Element, true]])

    expect(onNear).toHaveBeenCalledExactlyOnceWith([urlOf(0)])
  })

  it('stops watching a row on request', () => {
    stubIntersectionObserver()
    const onNear = vi.fn()
    const watcher = createRowWatcher(onNear)
    const rows = rowsInDocument(2)
    const stop = watcher.watch(rows[0] as Element, urlOf(0))
    watcher.watch(rows[1] as Element, urlOf(1))

    stop()
    stop()
    report([[rows[0] as Element, true], [rows[1] as Element, true]])

    expect(theObserver().targets).toEqual([])
    expect(onNear).toHaveBeenCalledExactlyOnceWith([urlOf(1)])
  })

  it('lets everything go when disposed', () => {
    stubIntersectionObserver()
    const onNear = vi.fn()
    const watcher = createRowWatcher(onNear)
    const [row] = rowsInDocument(1)
    watcher.watch(row as Element, urlOf(0))
    const observer = theObserver()

    watcher.dispose()
    observer.callback([{ isIntersecting: true, target: row as Element } as IntersectionObserverEntry], observer as unknown as IntersectionObserver)

    expect(observer.disconnected).toBe(true)
    expect(onNear).not.toHaveBeenCalled()
  })

  it('reports a row at once where there is no IntersectionObserver', () => {
    vi.stubGlobal('IntersectionObserver', undefined)
    const onNear = vi.fn()
    const watcher = createRowWatcher(onNear)
    const [row] = rowsInDocument(1)

    const stop = watcher.watch(row as Element, urlOf(0))

    expect(onNear).toHaveBeenCalledExactlyOnceWith([urlOf(0)])
    expect(stop).not.toThrow()
  })
})
