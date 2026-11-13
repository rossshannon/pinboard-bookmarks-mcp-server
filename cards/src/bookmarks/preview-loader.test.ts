import { describe, expect, it, vi } from 'vitest'

import type { ToolCallOutcome } from '../shell/tool-caller.ts'
import type { PreviewLoader } from './preview-loader.ts'

import { BUSY_RETRY_DELAY_MS, createPreviewLoader, isPreviewableUrl, MAX_PREVIEW_URL_LENGTH, PENDING_RETRY_DELAY_MS, PREVIEW_BATCH_SIZE, PREVIEW_TOOL } from './preview-loader.ts'

const url = (index: number): string => `https://example.com/${String(index)}`
const urls = (count: number, from = 0): string[] => Array.from({ length: count }, (_, index) => url(from + index))

const ok = (asked: readonly string[], statusOf: (url: string) => string = () => 'ok', extra: Record<string, unknown> = {}): ToolCallOutcome => ({
  data: { previews: asked.map(asked_ => ({ site_name: `Site of ${asked_}`, status: statusOf(asked_), url: asked_ })), ...extra },
  status: 'ok',
})

interface Call {
  readonly urls: readonly string[]
  readonly answer: (outcome: ToolCallOutcome) => void
}

/** A loader whose tool calls wait to be answered by the test, and whose timers the test fires. */
const harness = (): {
  calls: Call[]
  loader: PreviewLoader
  record: ReturnType<typeof vi.fn<(label: string, detail?: string) => void>>
  timers: Array<{ delayMs: number, fire: () => void }>
} => {
  const calls: Call[] = []
  const timers: Array<{ delayMs: number, fire: () => void }> = []
  const record = vi.fn<(label: string, detail?: string) => void>()
  const loader = createPreviewLoader({
    callTool: (name, toolArguments) => {
      expect(name).toBe(PREVIEW_TOOL)
      return new Promise((resolve) => {
        calls.push({ answer: resolve, urls: toolArguments['urls'] as string[] })
      })
    },
    record,
    setTimer: (fire, delayMs) => {
      timers.push({ delayMs, fire })
    },
  })
  return { calls, loader, record, timers }
}

/** Let the queued microtasks (the coalesced pump, and each await in it) run. */
const settle = async (): Promise<void> => {
  for (let turn = 0; turn < 10; turn += 1) await Promise.resolve()
}

const call = (calls: Call[], index: number): Call => {
  const found = calls[index]
  if (found === undefined) throw new Error(`no call ${String(index)}`)
  return found
}

describe('createPreviewLoader', () => {
  it('sends requests made in the same tick as one batch, in order', async () => {
    const { calls, loader } = harness()

    for (const each of urls(5)) loader.request([each])
    await settle()

    expect(calls).toHaveLength(1)
    expect(call(calls, 0).urls).toEqual(urls(5))
  })

  it('sends at most eight URLs a call, one call at a time', async () => {
    const { calls, loader } = harness()

    loader.request(urls(20))
    await settle()
    expect(calls).toHaveLength(1)
    expect(call(calls, 0).urls).toEqual(urls(PREVIEW_BATCH_SIZE))

    call(calls, 0).answer(ok(call(calls, 0).urls))
    await settle()
    expect(calls).toHaveLength(2)
    expect(call(calls, 1).urls).toEqual(urls(8, 8))

    call(calls, 1).answer(ok(call(calls, 1).urls))
    await settle()
    expect(calls).toHaveLength(3)
    expect(call(calls, 2).urls).toEqual(urls(4, 16))

    call(calls, 2).answer(ok(call(calls, 2).urls))
    await settle()
    expect(calls).toHaveLength(3)
  })

  it('queues what is requested while a call is in flight for the next one', async () => {
    const { calls, loader } = harness()

    loader.request(urls(2))
    await settle()
    loader.request(urls(3, 2))
    await settle()
    expect(calls).toHaveLength(1)

    call(calls, 0).answer(ok(call(calls, 0).urls))
    await settle()
    expect(call(calls, 1).urls).toEqual(urls(3, 2))
  })

  it('asks about a URL once, however often it is requested', async () => {
    const { calls, loader } = harness()

    loader.request([url(1), url(1), url(2)])
    loader.request([url(2), url(1)])
    await settle()
    expect(call(calls, 0).urls).toEqual([url(1), url(2)])

    // Again while in flight, and again once answered.
    loader.request([url(1)])
    call(calls, 0).answer(ok(call(calls, 0).urls))
    await settle()
    loader.request([url(1), url(2)])
    await settle()

    expect(calls).toHaveLength(1)
  })

  it('never asks about anything but a web URL', async () => {
    const { calls, loader } = harness()

    loader.request(['javascript:alert(1)', 'data:text/html,x', '/relative', '', 'ftp://example.com/'])
    await settle()

    expect(calls).toHaveLength(0)
    expect(loader.state('javascript:alert(1)')).toEqual({ kind: 'none' })
  })

  it('is pending until answered, then holds the preview for good', async () => {
    const { calls, loader } = harness()

    expect(loader.state(url(1))).toEqual({ kind: 'pending' })
    loader.request([url(1)])
    await settle()
    expect(loader.state(url(1))).toEqual({ kind: 'pending' })

    call(calls, 0).answer(ok([url(1)]))
    await settle()

    const state = loader.state(url(1))
    expect(state.kind).toBe('ready')
    expect(state.kind === 'ready' && state.preview.siteName).toBe(`Site of ${url(1)}`)
    expect(loader.state(url(1))).toBe(state)
  })

  it.each(['no_data', 'error'])('settles a %s answer as nothing, and does not ask again', async (status) => {
    const { calls, loader } = harness()

    loader.request([url(1)])
    await settle()
    call(calls, 0).answer(ok([url(1)], () => status))
    await settle()

    expect(loader.state(url(1))).toEqual({ kind: 'none' })
    loader.request([url(1)])
    await settle()
    expect(calls).toHaveLength(1)
  })

  it('tells its subscribers on every change, and bumps the version', async () => {
    const { calls, loader } = harness()
    const listener = vi.fn()
    const unsubscribe = loader.subscribe(listener)
    expect(loader.version()).toBe(0)

    loader.request(urls(9))
    await settle()
    call(calls, 0).answer(ok(call(calls, 0).urls))
    await settle()
    expect(listener).toHaveBeenCalledOnce()
    expect(loader.version()).toBe(1)

    unsubscribe()
    call(calls, 1).answer(ok(call(calls, 1).urls))
    await settle()
    expect(listener).toHaveBeenCalledOnce()
    expect(loader.version()).toBe(2)
  })

  describe('what it will ask about', () => {
    it.each([
      'https://example.com/',
      'http://example.com/a?b=c#d',
      'HTTPS://EXAMPLE.COM/',
      `https://example.com/${'a'.repeat(MAX_PREVIEW_URL_LENGTH - 'https://example.com/'.length)}`,
    ])('is a URL the server would look up: %s', (candidate) => {
      expect(isPreviewableUrl(candidate)).toBe(true)
    })

    it.each([
      ['a script URL', 'javascript:alert(1)'],
      ['a relative path', '/relative'],
      ['no host', 'https:///path'],
      ['a space inside', 'https://example.com/a b'],
      ['a trailing newline', 'https://example.com/\n'],
      ['a tab inside', 'https://example.com/a\tb'],
      ['a host that does not parse', 'https://[not-a-host]/'],
      ['one character too long', `https://example.com/${'a'.repeat(MAX_PREVIEW_URL_LENGTH - 'https://example.com/'.length + 1)}`],
    ])('is not %s', (_name, candidate) => {
      expect(isPreviewableUrl(candidate)).toBe(false)
    })

    it('leaves a URL the server would refuse out of the batch, and holds no place for it', async () => {
      const { calls, loader } = harness()
      const tooLong = `https://example.com/${'a'.repeat(MAX_PREVIEW_URL_LENGTH)}`

      loader.request([url(0), tooLong, 'https://example.com/a b', url(1)])
      await settle()

      expect(call(calls, 0).urls).toEqual([url(0), url(1)])
      expect(loader.state(tooLong)).toEqual({ kind: 'none' })
      expect(loader.state(url(0))).toEqual({ kind: 'pending' })
    })
  })

  describe('when the bridge had not connected yet', () => {
    it.each(['not-connected', 'rejected-before-connect'] as const)('is suspended, not stopped, by "%s"', async (reason) => {
      const { calls, loader, record } = harness()
      const listener = vi.fn()
      loader.subscribe(listener)

      loader.request(urls(10))
      await settle()
      call(calls, 0).answer({ reason, status: 'unavailable' })
      await settle()

      // It looks stopped: no places held, nothing more sent.
      expect(loader.state(url(0))).toEqual({ kind: 'none' })
      expect(loader.state(url(9))).toEqual({ kind: 'none' })
      expect(calls).toHaveLength(1)
      expect(record).toHaveBeenCalledWith('previews:suspended', reason)
      expect(listener).toHaveBeenCalledOnce()

      loader.resume()
      await settle()

      // The same URLs, in the same order, and the rows wait again.
      expect(listener).toHaveBeenCalledTimes(2)
      expect(call(calls, 1).urls).toEqual(urls(8))
      expect(loader.state(url(9))).toEqual({ kind: 'pending' })
      call(calls, 1).answer(ok(call(calls, 1).urls))
      await settle()
      expect(loader.state(url(0)).kind).toBe('ready')
      expect(call(calls, 2).urls).toEqual(urls(2, 8))
    })

    it('remembers what is requested while suspended', async () => {
      const { calls, loader } = harness()

      loader.request([url(0)])
      await settle()
      call(calls, 0).answer({ reason: 'not-connected', status: 'unavailable' })
      await settle()
      loader.request([url(1)])
      await settle()
      expect(calls).toHaveLength(1)

      loader.resume()
      await settle()

      expect(call(calls, 1).urls).toEqual([url(0), url(1)])
    })

    it('resumes once: unavailable after that is final', async () => {
      const { calls, loader, record } = harness()

      loader.request([url(0)])
      await settle()
      call(calls, 0).answer({ reason: 'rejected-before-connect', status: 'unavailable' })
      await settle()
      loader.resume()
      await settle()
      call(calls, 1).answer({ reason: 'not-connected', status: 'unavailable' })
      await settle()
      loader.resume()
      await settle()

      expect(calls).toHaveLength(2)
      expect(record).toHaveBeenCalledWith('previews:stopped', 'not-connected')
      expect(loader.state(url(0))).toEqual({ kind: 'none' })
    })

    it('does nothing when resumed without having been suspended', async () => {
      const { calls, loader } = harness()
      const listener = vi.fn()
      loader.subscribe(listener)

      loader.resume()
      loader.request([url(0)])
      await settle()
      call(calls, 0).answer({ reason: 'rejected', status: 'unavailable' })
      await settle()
      loader.resume()
      await settle()

      expect(calls).toHaveLength(1)
      expect(listener).toHaveBeenCalledOnce()
    })
  })

  describe('when the server was still looking a page up', () => {
    const pendingFor = (which: (url: string) => boolean) => (asked: string): string => which(asked) ? 'pending' : 'ok'

    it('keeps the row waiting and asks about it once more a few seconds on', async () => {
      const { calls, loader, record, timers } = harness()

      loader.request(urls(3))
      await settle()
      call(calls, 0).answer(ok(call(calls, 0).urls, pendingFor(each => each === url(1))))
      await settle()

      expect(loader.state(url(0)).kind).toBe('ready')
      expect(loader.state(url(1))).toEqual({ kind: 'pending' })
      expect(timers).toHaveLength(1)
      expect(timers[0]?.delayMs).toBe(PENDING_RETRY_DELAY_MS)
      expect(record).toHaveBeenCalledWith('previews:pending', expect.stringContaining('1 again'))
      // Asking again in the meantime does not send it twice.
      loader.request([url(1)])
      await settle()
      expect(calls).toHaveLength(1)

      timers[0]?.fire()
      await settle()
      expect(call(calls, 1).urls).toEqual([url(1)])
      call(calls, 1).answer(ok([url(1)]))
      await settle()

      expect(loader.state(url(1)).kind).toBe('ready')
    })

    it('does not hold up the rest of the queue while it waits', async () => {
      const { calls, loader } = harness()

      loader.request(urls(10))
      await settle()
      call(calls, 0).answer(ok(call(calls, 0).urls, pendingFor(() => true)))
      await settle()

      expect(call(calls, 1).urls).toEqual(urls(2, 8))
    })

    it('gives the row nothing when it is still pending the second time', async () => {
      const { calls, loader, timers } = harness()

      loader.request([url(1), url(2)])
      await settle()
      call(calls, 0).answer(ok(call(calls, 0).urls, pendingFor(each => each === url(1))))
      await settle()
      timers[0]?.fire()
      await settle()
      call(calls, 1).answer(ok([url(1)], () => 'pending'))
      await settle()

      expect(loader.state(url(1))).toEqual({ kind: 'none' })
      expect(timers).toHaveLength(1)
      // Nothing stopped: the rest carries on.
      expect(loader.state(url(2)).kind).toBe('ready')
      loader.request([url(3)])
      await settle()
      expect(call(calls, 2).urls).toEqual([url(3)])
    })
  })

  describe('second chances are counted apart', () => {
    const busyOnce = (asked: readonly string[]): ToolCallOutcome => ({
      data: { previews: asked.map(each => ({ retry: true, site_name: 'Text only', status: 'ok', url: each })) },
      status: 'ok',
    })

    it('still retries a rate-limited URL after its pending retry was used', async () => {
      const { calls, loader, timers } = harness()

      loader.request([url(1)])
      await settle()
      call(calls, 0).answer(ok([url(1)], () => 'pending'))
      await settle()
      timers[0]?.fire()
      await settle()
      call(calls, 1).answer(ok([url(1)], () => 'rate_limited', { retry_after_seconds: 3 }))
      await settle()

      // Held for the limit, not given up on.
      expect(loader.state(url(1))).toEqual({ kind: 'pending' })
      expect(timers[1]?.delayMs).toBe(3000)
      timers[1]?.fire()
      await settle()
      call(calls, 2).answer(ok([url(1)]))
      await settle()

      expect(loader.state(url(1)).kind).toBe('ready')
    })

    it('still gives a rate-limited URL its busy retry afterwards', async () => {
      const { calls, loader, timers } = harness()

      loader.request([url(1)])
      await settle()
      call(calls, 0).answer(ok([url(1)], () => 'rate_limited', { retry_after_seconds: 3 }))
      await settle()
      timers[0]?.fire()
      await settle()
      call(calls, 1).answer(busyOnce([url(1)]))
      await settle()

      // The limit's retry is spent; the busy one is not.
      expect(timers).toHaveLength(2)
      expect(timers[1]?.delayMs).toBe(BUSY_RETRY_DELAY_MS)
      timers[1]?.fire()
      await settle()

      expect(call(calls, 2).urls).toEqual([url(1)])
    })

    it('does not stop the loader when a URL that used its busy retry is limited for the first time', async () => {
      const { calls, loader, record, timers } = harness()

      loader.request([url(1), url(2)])
      await settle()
      call(calls, 0).answer({
        data: { previews: [{ retry: true, status: 'no_data', url: url(1) }, { site_name: 'Two', status: 'ok', url: url(2) }] },
        status: 'ok',
      })
      await settle()
      timers[0]?.fire()
      await settle()
      // The row already has its answer (nothing), so the limit is not held against it.
      call(calls, 1).answer(ok([url(1)], () => 'rate_limited', { retry_after_seconds: 2 }))
      await settle()

      expect(record).not.toHaveBeenCalledWith('previews:stopped', expect.anything())
      loader.request([url(3)])
      timers.at(-1)?.fire()
      await settle()
      expect(calls.length).toBeGreaterThan(2)
    })
  })

  describe('when a call is unavailable', () => {
    it('stops: everything unanswered has no preview, and nothing more is sent', async () => {
      const { calls, loader, record } = harness()
      const listener = vi.fn()
      loader.subscribe(listener)

      loader.request(urls(12))
      await settle()
      call(calls, 0).answer({ reason: 'no-capability', status: 'unavailable' })
      await settle()

      expect(calls).toHaveLength(1)
      expect(loader.state(url(0))).toEqual({ kind: 'none' })
      expect(loader.state(url(11))).toEqual({ kind: 'none' })
      expect(loader.state('https://never-requested.example/')).toEqual({ kind: 'none' })
      expect(listener).toHaveBeenCalledOnce()
      expect(record).toHaveBeenCalledWith('previews:stopped', 'no-capability')

      loader.request(urls(3, 20))
      await settle()
      expect(calls).toHaveLength(1)
    })

    it('keeps the previews it already has', async () => {
      const { calls, loader } = harness()

      loader.request(urls(9))
      await settle()
      call(calls, 0).answer(ok(call(calls, 0).urls))
      await settle()
      call(calls, 1).answer({ reason: 'timeout', status: 'unavailable' })
      await settle()

      expect(loader.state(url(0)).kind).toBe('ready')
      expect(loader.state(url(8))).toEqual({ kind: 'none' })
    })
  })

  describe('when the bridge’s rate limit is reached', () => {
    const limitedFrom = (first: number) => (asked: string): string => Number(asked.split('/').at(-1)) >= first ? 'rate_limited' : 'ok'

    it('pauses for as long as the tool says, then asks about those URLs once more', async () => {
      const { calls, loader, timers } = harness()

      loader.request(urls(12))
      await settle()
      call(calls, 0).answer(ok(call(calls, 0).urls, limitedFrom(3), { retry_after_seconds: 7 }))
      await settle()

      // The three that were answered are drawn; the rest still have a place held.
      expect(loader.state(url(2)).kind).toBe('ready')
      expect(loader.state(url(3))).toEqual({ kind: 'pending' })
      // Nothing is sent during the wait, not even the next batch.
      expect(calls).toHaveLength(1)
      expect(timers).toHaveLength(1)
      expect(timers[0]?.delayMs).toBe(7000)

      timers[0]?.fire()
      await settle()

      // The held URLs go first, topped up from the queue to a full batch.
      expect(calls).toHaveLength(2)
      expect(call(calls, 1).urls).toEqual([...urls(5, 3), ...urls(3, 8)])

      call(calls, 1).answer(ok(call(calls, 1).urls))
      await settle()
      expect(loader.state(url(3)).kind).toBe('ready')
      expect(call(calls, 2).urls).toEqual([url(11)])
    })

    it('queues requests made during the wait behind the retry', async () => {
      const { calls, loader, timers } = harness()

      loader.request(urls(2))
      await settle()
      call(calls, 0).answer(ok(call(calls, 0).urls, limitedFrom(1)))
      await settle()
      loader.request([url(50)])
      await settle()
      expect(calls).toHaveLength(1)

      timers[0]?.fire()
      await settle()
      expect(call(calls, 1).urls).toEqual([url(1), url(50)])
    })

    it('waits thirty seconds when the tool names no wait', async () => {
      const { calls, loader, timers } = harness()

      loader.request([url(1)])
      await settle()
      call(calls, 0).answer(ok([url(1)], () => 'rate_limited'))
      await settle()

      expect(timers[0]?.delayMs).toBe(30_000)
    })

    it('gives up quietly when the retry is rate-limited too', async () => {
      const { calls, loader, record, timers } = harness()

      loader.request(urls(12))
      await settle()
      call(calls, 0).answer(ok(call(calls, 0).urls, limitedFrom(3)))
      await settle()
      timers[0]?.fire()
      await settle()
      call(calls, 1).answer(ok(call(calls, 1).urls, () => 'rate_limited'))
      await settle()

      // No second wait, no third call, and no place held for anything.
      expect(timers).toHaveLength(1)
      expect(calls).toHaveLength(2)
      expect(loader.state(url(0)).kind).toBe('ready')
      expect(loader.state(url(3))).toEqual({ kind: 'none' })
      expect(loader.state(url(11))).toEqual({ kind: 'none' })
      expect(record).toHaveBeenCalledWith('previews:stopped', 'rate-limited')
    })

    it('retries a URL limited for the first time in a later batch', async () => {
      const { calls, loader, timers } = harness()

      loader.request([url(1)])
      await settle()
      call(calls, 0).answer(ok([url(1)], () => 'rate_limited'))
      await settle()
      timers[0]?.fire()
      await settle()
      call(calls, 1).answer(ok([url(1)]))
      await settle()

      loader.request([url(2)])
      await settle()
      call(calls, 2).answer(ok([url(2)], () => 'rate_limited'))
      await settle()

      expect(timers).toHaveLength(2)
      expect(loader.state(url(2))).toEqual({ kind: 'pending' })
    })
  })

  describe('when the bridge was too busy to finish a preview', () => {
    const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
    const busy = (asked: readonly string[], which: (url: string) => boolean): ToolCallOutcome => ({
      data: { previews: asked.map(each => ({ site_name: 'Text only', status: 'ok', url: each, ...(which(each) ? { retry: true } : {}) })) },
      status: 'ok',
    })
    const finished = (asked: readonly string[]): ToolCallOutcome => ({
      data: { previews: asked.map(each => ({ image: { data_uri: PNG, height: 126, width: 240 }, site_name: 'Finished', status: 'ok', url: each })) },
      status: 'ok',
    })
    const imageOf = (loader: PreviewLoader, asked: string): unknown => {
      const state = loader.state(asked)
      return state.kind === 'ready' ? state.preview.image : 'not ready'
    }

    it('draws what arrived at once, and asks about that URL once more a few seconds on', async () => {
      const { calls, loader, record, timers } = harness()

      loader.request(urls(3))
      await settle()
      call(calls, 0).answer(busy(call(calls, 0).urls, each => each === url(1)))
      await settle()

      // Drawn straight away, not held as pending.
      expect(loader.state(url(1)).kind).toBe('ready')
      expect(imageOf(loader, url(1))).toBeUndefined()
      expect(calls).toHaveLength(1)
      expect(timers).toHaveLength(1)
      expect(timers[0]?.delayMs).toBe(BUSY_RETRY_DELAY_MS)
      expect(record).toHaveBeenCalledWith('previews:busy', expect.stringContaining('1 again'))

      timers[0]?.fire()
      await settle()
      expect(call(calls, 1).urls).toEqual([url(1)])

      call(calls, 1).answer(finished([url(1)]))
      await settle()
      expect(imageOf(loader, url(1))).toBeDefined()
    })

    it('does not hold up the rest of the queue while it waits', async () => {
      const { calls, loader } = harness()

      loader.request(urls(10))
      await settle()
      call(calls, 0).answer(busy(call(calls, 0).urls, () => true))
      await settle()

      expect(calls).toHaveLength(2)
      expect(call(calls, 1).urls).toEqual(urls(2, 8))
    })

    it('asks at most once more per URL, and lets the second answer stand', async () => {
      const { calls, loader, timers } = harness()

      loader.request([url(1)])
      await settle()
      call(calls, 0).answer(busy([url(1)], () => true))
      await settle()
      timers[0]?.fire()
      await settle()
      call(calls, 1).answer(busy([url(1)], () => true))
      await settle()

      expect(timers).toHaveLength(1)
      expect(calls).toHaveLength(2)
      expect(loader.state(url(1)).kind).toBe('ready')
      // And the card asking again changes nothing.
      loader.request([url(1)])
      await settle()
      expect(calls).toHaveLength(2)
    })

    it.each([
      ['comes back empty', (asked: readonly string[]) => ok(asked, () => 'error')],
      ['meets the rate limit', (asked: readonly string[]) => ok(asked, () => 'rate_limited')],
    ])('keeps what the first answer gave if the second %s', async (_name, second) => {
      const { calls, loader, timers } = harness()

      loader.request([url(1)])
      await settle()
      call(calls, 0).answer(busy([url(1)], () => true))
      await settle()
      timers[0]?.fire()
      await settle()
      call(calls, 1).answer(second([url(1)]))
      await settle()

      const state = loader.state(url(1))
      expect(state.kind === 'ready' && state.preview.siteName).toBe('Text only')
      // Nothing held, nothing stopped: a later row still loads.
      expect(timers).toHaveLength(1)
      loader.request([url(2)])
      await settle()
      expect(call(calls, 2).urls).toEqual([url(2)])
    })

    it('does not ask again once the loader has stopped', async () => {
      const { calls, loader, timers } = harness()

      loader.request(urls(9))
      await settle()
      call(calls, 0).answer(busy(call(calls, 0).urls, each => each === url(0)))
      await settle()
      call(calls, 1).answer({ reason: 'timeout', status: 'unavailable' })
      await settle()
      timers[0]?.fire()
      await settle()

      expect(calls).toHaveLength(2)
      expect(loader.state(url(0)).kind).toBe('ready')
    })
  })

  describe('once disposed', () => {
    it('sends nothing more and tells nobody anything', async () => {
      const { calls, loader, timers } = harness()
      const listener = vi.fn()
      loader.subscribe(listener)

      loader.request(urls(9))
      await settle()
      loader.dispose()
      call(calls, 0).answer(ok(call(calls, 0).urls))
      await settle()
      loader.request([url(40)])
      await settle()

      expect(calls).toHaveLength(1)
      expect(timers).toHaveLength(0)
      expect(listener).not.toHaveBeenCalled()
      expect(loader.state(url(0))).toEqual({ kind: 'none' })
    })

    it('does not retry after a wait that outlives it', async () => {
      const { calls, loader, timers } = harness()

      loader.request([url(1)])
      await settle()
      call(calls, 0).answer(ok([url(1)], () => 'rate_limited'))
      await settle()
      loader.dispose()
      timers[0]?.fire()
      await settle()

      expect(calls).toHaveLength(1)
    })
  })
})
