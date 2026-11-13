import { describe, expect, it, vi } from 'vitest'

import type { ToolCallOutcome } from '../shell/tool-caller.ts'
import type { PreviewLoader } from './preview-loader.ts'

import { BUSY_RETRY_DELAY_MS, createPreviewLoader, PREVIEW_BATCH_SIZE, PREVIEW_TOOL } from './preview-loader.ts'

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
