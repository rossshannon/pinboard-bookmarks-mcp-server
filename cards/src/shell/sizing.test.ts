// @vitest-environment jsdom
import type { Mock } from 'vitest'

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { CardSize, SizeChannels } from './sizing.ts'

import { createSizeReporter, measureDocument, prefersReducedMotion, SIZE_LADDER_DELAYS_MS, SIZE_RAMP_MAX_MS, SIZE_RAMP_MS, SIZE_RAMP_SPEED_PX_PER_MS, SIZE_SETTLE_DELAY_MS } from './sizing.ts'

const defineSize = (element: Element, property: 'clientHeight' | 'offsetHeight' | 'scrollHeight' | 'scrollWidth', value: number): void => {
  Object.defineProperty(element, property, { configurable: true, value })
}

describe('measureDocument', () => {
  it('falls back to 1x1024 (globalThis.innerWidth) when the document reports no size', () => {
    expect(measureDocument()).toEqual({ height: 1, width: globalThis.innerWidth })
  })

  it('takes the max across documentElement/body scroll and offset sizes', () => {
    defineSize(document.documentElement, 'scrollHeight', 200)
    defineSize(document.body, 'scrollHeight', 340)
    defineSize(document.body, 'offsetHeight', 120)
    defineSize(document.documentElement, 'scrollWidth', 500)
    defineSize(document.body, 'scrollWidth', 640)

    expect(measureDocument()).toEqual({ height: 340, width: 640 })
  })

  // The frame the host gave us is the viewport, and documentElement.scrollHeight
  // is floored at it — reading it on a short card reports the frame straight
  // back, so the card can grow its frame but never shrink it.
  it('ignores the viewport floor so a card shorter than its frame reports its own height', () => {
    defineSize(document.documentElement, 'clientHeight', 220)
    defineSize(document.documentElement, 'scrollHeight', 220)
    defineSize(document.body, 'scrollHeight', 179)
    defineSize(document.body, 'offsetHeight', 179)

    expect(measureDocument().height).toBe(179)
  })

  // The one thing the body cannot see: content overflowing the html element
  // without growing the body. It is distinguishable because it exceeds the
  // viewport rather than resting exactly on it.
  it('still takes documentElement height when content overflows past the viewport', () => {
    defineSize(document.documentElement, 'clientHeight', 220)
    defineSize(document.documentElement, 'scrollHeight', 400)
    defineSize(document.body, 'scrollHeight', 179)
    defineSize(document.body, 'offsetHeight', 179)

    expect(measureDocument().height).toBe(400)
  })

  // scrollHeight rounds to the nearest pixel; reporting it for a card 179.34px
  // tall gives a frame a third of a pixel short, which scrolls.
  it('rounds a fractional height up, so the frame is never a fraction short', () => {
    defineSize(document.documentElement, 'clientHeight', 220)
    defineSize(document.documentElement, 'scrollHeight', 220)
    defineSize(document.body, 'scrollHeight', 179)
    defineSize(document.body, 'offsetHeight', 179)
    const rectSpy = vi.spyOn(document.body, 'getBoundingClientRect').mockReturnValue(DOMRect.fromRect({ height: 179.34 }))

    expect(measureDocument().height).toBe(180)

    rectSpy.mockRestore()
  })

  it('falls back to 1 when even globalThis.innerWidth reports nothing', () => {
    // Reset the previous test's overrides: jsdom's document persists across
    // tests in this file.
    defineSize(document.documentElement, 'scrollWidth', 0)
    defineSize(document.body, 'scrollWidth', 0)
    const innerWidthSpy = vi.spyOn(globalThis, 'innerWidth', 'get').mockReturnValue(0)

    expect(measureDocument().width).toBe(1)

    innerWidthSpy.mockRestore()
  })
})

describe('prefersReducedMotion', () => {
  afterEach(() => {
    // jsdom has no matchMedia; each test that installs one takes it away again.
    Reflect.deleteProperty(globalThis, 'matchMedia')
  })

  it('is false where the environment cannot answer — jsdom has no matchMedia', () => {
    expect(prefersReducedMotion()).toBe(false)
  })

  it('reads the reduce preference off the media query', () => {
    const matchMedia = vi.fn<(query: string) => { matches: boolean }>().mockReturnValue({ matches: true })
    Object.defineProperty(globalThis, 'matchMedia', { configurable: true, value: matchMedia })

    expect(prefersReducedMotion()).toBe(true)
    expect(matchMedia).toHaveBeenCalledWith('(prefers-reduced-motion: reduce)')
  })
})

interface Harness {
  readonly channels: SizeChannels
  readonly isConnected: { current: boolean }
  readonly isPreConnectHost: { current: boolean }
  readonly measure: Mock<() => CardSize>
  readonly sendFallback: Mock<(size: CardSize) => void>
  readonly sendViaApp: Mock<(size: CardSize) => void>
  readonly sendViaAppStep: Mock<(size: CardSize) => void>
}

const createHarness = (initialSize: CardSize): Harness => {
  const isConnected = { current: false }
  // Defaults to the `window.openai` host, the one told a size before the
  // handshake; tests that care about a conforming host flip it.
  const isPreConnectHost = { current: true }
  const measure = vi.fn<() => CardSize>().mockReturnValue(initialSize)
  const sendViaApp = vi.fn<(size: CardSize) => void>()
  const sendViaAppStep = vi.fn<(size: CardSize) => void>()
  const sendFallback = vi.fn<(size: CardSize) => void>()
  const channels: SizeChannels = {
    isConnected: () => isConnected.current,
    isPreConnectHost: () => isPreConnectHost.current,
    sendFallback,
    sendViaApp,
    sendViaAppStep,
  }
  return { channels, isConnected, isPreConnectHost, measure, sendFallback, sendViaApp, sendViaAppStep }
}

/** Every height put on the wire — ramp steps and landings alike — in the order they were sent. */
const wireHeights = (harness: Harness): number[] => {
  const entries = [
    ...harness.sendViaAppStep.mock.calls.map(([size], index) => ({ height: size.height, order: harness.sendViaAppStep.mock.invocationCallOrder[index] ?? 0 })),
    ...harness.sendViaApp.mock.calls.map(([size], index) => ({ height: size.height, order: harness.sendViaApp.mock.invocationCallOrder[index] ?? 0 })),
  ]
  return entries.toSorted((a, b) => a.order - b.order).map(entry => entry.height)
}

describe('createSizeReporter', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('a pre-connect measurement raw-posts immediately and coalesces for the SDK flush', () => {
    // Without the raw post here, a window.openai host that never answers
    // ui/initialize would get no size on any channel for a fast-path render.
    const harness = createHarness({ height: 100, width: 200 })
    const reporter = createSizeReporter(harness.channels, harness.measure)

    reporter.measure()

    expect(harness.sendViaApp).not.toHaveBeenCalled()
    expect(harness.sendFallback).toHaveBeenCalledExactlyOnceWith({ height: 100, width: 200 })
  })

  it('sends a host that answers the handshake nothing before it, so nothing it has drawn is resized underneath it', () => {
    // The raw notification is spec'd to be ignored before initialize, but a host
    // that honoured it would size its frame — and then the flush below opens a
    // ramp from zero, dropping the card to a few pixels before rebuilding it.
    const harness = createHarness({ height: 100, width: 200 })
    harness.isPreConnectHost.current = false
    const reporter = createSizeReporter(harness.channels, harness.measure)

    reporter.measure()

    expect(harness.sendFallback).not.toHaveBeenCalled()

    // Still coalesced, so the height is not lost — it goes out as the opening
    // ramp the moment the handshake resolves.
    harness.isConnected.current = true
    reporter.flushOnConnect()
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)

    expect(harness.sendViaApp).toHaveBeenCalledExactlyOnceWith({ height: 100, width: 200 })
    expect(harness.sendViaAppStep).not.toHaveBeenCalled()
  })

  it('fast-path render with a handshake that never resolves still reports sizes via the raw channel', () => {
    const harness = createHarness({ height: 100, width: 200 })
    const reporter = createSizeReporter(harness.channels, harness.measure)

    reporter.onRender()
    vi.advanceTimersByTime(2000)

    expect(harness.sendViaApp).not.toHaveBeenCalled()
    expect(harness.sendFallback).toHaveBeenCalledExactlyOnceWith({ height: 100, width: 200 })
  })

  it('flushes the coalesced pre-connect measurement once connected, in one report when the raw channel already sent that height', () => {
    // A host told this height before the handshake has drawn its frame at it.
    // Ramping the flush from zero would collapse that frame to a sliver and
    // grow it back, so the height goes out once, unramped.
    const harness = createHarness({ height: 100, width: 200 })
    const reporter = createSizeReporter(harness.channels, harness.measure)

    reporter.measure()
    expect(harness.sendFallback).toHaveBeenCalledExactlyOnceWith({ height: 100, width: 200 })

    harness.isConnected.current = true
    reporter.flushOnConnect()
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)

    expect(harness.sendViaApp).toHaveBeenCalledExactlyOnceWith({ height: 100, width: 200 })
    expect(harness.sendViaAppStep).not.toHaveBeenCalled()
  })

  it('the first report goes out whole, and every later change ramps from it', () => {
    // The host holds a placeholder of its own until the card speaks. Ramping
    // the first report from zero collapses that placeholder to a sliver and
    // climbs back; reported whole, the frame settles onto the skeleton once.
    const harness = createHarness({ height: 150, width: 400 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.measure()

    expect(harness.sendViaApp).toHaveBeenCalledExactlyOnceWith({ height: 150, width: 400 })
    expect(harness.sendViaAppStep).not.toHaveBeenCalled()

    harness.measure.mockReturnValue({ height: 520, width: 400 })
    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)

    const heights = wireHeights(harness)
    expect(heights[0]).toBe(150)
    expect(heights[1]).toBeGreaterThan(150)
    expect(heights).toStrictEqual(heights.toSorted((a, b) => a - b))
    expect(heights.at(-1)).toBe(520)
    // The steps are wire-only; the bookkept sends are the first report and the landing.
    expect(harness.sendViaApp).toHaveBeenCalledTimes(2)
    expect(harness.sendViaAppStep.mock.calls).toHaveLength(heights.length - 2)
  })

  it('does nothing on flushOnConnect when no pre-connect measurement was taken', () => {
    const harness = createHarness({ height: 100, width: 200 })
    const reporter = createSizeReporter(harness.channels, harness.measure)

    harness.isConnected.current = true
    reporter.flushOnConnect()

    expect(harness.sendViaApp).not.toHaveBeenCalled()
  })

  it('sends via the app when connected', () => {
    const harness = createHarness({ height: 300, width: 400 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)

    expect(harness.sendViaApp).toHaveBeenCalledExactlyOnceWith({ height: 300, width: 400 })
    expect(harness.sendFallback).not.toHaveBeenCalled()
  })

  it('dedupes an identical size, connected or not', () => {
    const harness = createHarness({ height: 300, width: 400 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.measure()
    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)

    expect(harness.sendViaApp).toHaveBeenCalledOnce()
  })

  it('sends a changed size again after a duplicate', () => {
    const harness = createHarness({ height: 300, width: 400 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)
    harness.measure.mockReturnValue({ height: 500, width: 400 })
    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MS)

    expect(harness.sendViaApp).toHaveBeenCalledTimes(2)
    expect(harness.sendViaApp).toHaveBeenLastCalledWith({ height: 500, width: 400 })
  })

  it('sends a travel too small to ramp in one report, not fifteen indistinguishable steps', () => {
    // An eased curve over a 1px travel rounds nearly every step to the same
    // integer height, so a ramp here buys the host nothing but message volume —
    // reachable when a layout shift lands inside the settle window.
    const harness = createHarness({ height: 500, width: 400 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.measure()
    harness.measure.mockReturnValue({ height: 501, width: 400 })
    reporter.measure()

    expect(harness.sendViaApp).toHaveBeenCalledTimes(2)
    expect(harness.sendViaApp).toHaveBeenLastCalledWith({ height: 501, width: 400 })
    expect(harness.sendViaAppStep).not.toHaveBeenCalled()
  })

  // The host applies each report by setting the iframe's height with no
  // transition of its own, so the only way the frame can move rather than jump
  // is a stream of intermediate heights. Every one is between the two ends,
  // they never go backwards, and the last lands exactly on the target — a
  // rounded easing curve could otherwise leave the frame a pixel short forever.
  it('ramps a changed height to the host over SIZE_RAMP_MS, monotonically, landing exactly on the target', () => {
    const harness = createHarness({ height: 100, width: 400 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)
    harness.sendViaApp.mockClear()
    harness.sendViaAppStep.mockClear()
    harness.measure.mockReturnValue({ height: 400, width: 400 })
    reporter.measure()

    // Nothing is sent synchronously: the first step of the ramp is a frame away.
    expect(harness.sendViaApp).not.toHaveBeenCalled()
    expect(harness.sendViaAppStep).not.toHaveBeenCalled()

    vi.advanceTimersByTime(SIZE_RAMP_MS / 2)
    const midway = wireHeights(harness).at(-1)
    expect(midway).toBeGreaterThan(100)
    expect(midway).toBeLessThan(400)

    vi.advanceTimersByTime(SIZE_RAMP_MS / 2)
    const heights = wireHeights(harness)
    expect(heights.length).toBeGreaterThan(5)
    expect(heights.at(-1)).toBe(400)
    expect(heights).toStrictEqual(heights.toSorted((a, b) => a - b))
    // Ease-out: more than half the distance is covered in the first half.
    expect(midway).toBeGreaterThan(250)
    // One recorded send per change — the landing — however many steps led to it.
    expect(harness.sendViaApp).toHaveBeenCalledExactlyOnceWith({ height: 400, width: 400 })
  })

  it('ramps downward too, for the content that turns out shorter than its skeleton', () => {
    const harness = createHarness({ height: 400, width: 400 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)
    harness.sendViaApp.mockClear()
    harness.sendViaAppStep.mockClear()
    harness.measure.mockReturnValue({ height: 150, width: 400 })
    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MS)

    const heights = wireHeights(harness)
    expect(heights.at(-1)).toBe(150)
    expect(heights).toStrictEqual(heights.toSorted((a, b) => b - a))
  })

  // A measurement that arrives mid-ramp — a font swap caught by the boot
  // ladder, say — retargets from where the wire currently stands, not from the
  // old target: the frame turns rather than snapping to the old target first.
  it('a retarget mid-ramp continues from the height most recently sent, not from the abandoned target', () => {
    const harness = createHarness({ height: 100, width: 400 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)
    harness.measure.mockReturnValue({ height: 400, width: 400 })
    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MS / 3)
    const wireHeight = wireHeights(harness).at(-1) ?? 0
    expect(wireHeight).toBeGreaterThan(100)
    expect(wireHeight).toBeLessThan(400)

    harness.sendViaApp.mockClear()
    harness.sendViaAppStep.mockClear()
    harness.measure.mockReturnValue({ height: 200, width: 400 })
    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MS)

    const heights = wireHeights(harness)
    // Every step of the new ramp sits between where the wire was and the new target.
    for (const height of heights) {
      expect(height).toBeLessThanOrEqual(Math.max(wireHeight, 200))
      expect(height).toBeGreaterThanOrEqual(Math.min(wireHeight, 200))
    }
    expect(heights.at(-1)).toBe(200)
  })

  // Every real load can take this path: a tool answers while the growth into
  // the card from an earlier, smaller measurement is still in flight. The
  // retarget test above advances past that ramp first, so this is the one that
  // would catch a ramp refusing to be cut short by a later target.
  it('a result landing mid-ramp turns the frame from where the ramp had got to, rather than starting again', () => {
    const harness = createHarness({ height: 150, width: 400 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    // The first report, whole, then a growth to a taller card interrupted
    // part-way by a taller measurement still.
    reporter.measure()
    harness.measure.mockReturnValue({ height: 400, width: 400 })
    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MS / 3)
    const partWay = wireHeights(harness).at(-1) ?? 0
    expect(partWay).toBeGreaterThan(150)
    expect(partWay).toBeLessThan(400)

    harness.sendViaApp.mockClear()
    harness.sendViaAppStep.mockClear()
    harness.measure.mockReturnValue({ height: 620, width: 400 })
    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)

    const heights = wireHeights(harness)
    // From where the ramp stood, not back to the first report's height…
    expect(heights[0]).toBeGreaterThanOrEqual(partWay)
    expect(heights).toStrictEqual(heights.toSorted((a, b) => a - b))
    // …and landing on the new target.
    expect(harness.sendViaApp).toHaveBeenCalledExactlyOnceWith({ height: 620, width: 400 })
    expect(heights.at(-1)).toBe(620)
  })

  it('sends each height in one report, no ramp, when the viewer prefers reduced motion', () => {
    const harness = createHarness({ height: 150, width: 400 })
    const reporter = createSizeReporter(harness.channels, harness.measure, () => true)
    harness.isConnected.current = true

    reporter.measure()
    harness.measure.mockReturnValue({ height: 500, width: 400 })
    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)

    // Both the opening and the change of height went out whole: no steps, no timers.
    expect(harness.sendViaAppStep).not.toHaveBeenCalled()
    expect(wireHeights(harness)).toStrictEqual([150, 500])
  })

  it('a width-only change is one message, there being no height to move through', () => {
    const harness = createHarness({ height: 300, width: 400 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)
    harness.sendViaApp.mockClear()
    harness.sendViaAppStep.mockClear()
    harness.measure.mockReturnValue({ height: 300, width: 375 })
    reporter.measure()

    expect(harness.sendViaApp).toHaveBeenCalledExactlyOnceWith({ height: 300, width: 375 })
    vi.advanceTimersByTime(SIZE_RAMP_MS)
    expect(harness.sendViaApp).toHaveBeenCalledOnce()
    expect(harness.sendViaAppStep).not.toHaveBeenCalled()
  })

  // `measureDocument` floors an empty body at 1px. That is the report that
  // collapsed the host's frame to a line: nothing mounted yet is not a size,
  // and the host keeps whatever it had until there is one.
  it('never reports a document that measures as nothing, on either channel', () => {
    const harness = createHarness({ height: 1, width: 400 })
    const reporter = createSizeReporter(harness.channels, harness.measure)

    reporter.measureViaFallback()
    reporter.measure()
    harness.isConnected.current = true
    reporter.measure()
    reporter.flushOnConnect()

    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)
    expect(harness.sendFallback).not.toHaveBeenCalled()
    expect(harness.sendViaApp).not.toHaveBeenCalled()
    expect(harness.sendViaAppStep).not.toHaveBeenCalled()
  })

  it('dispose cancels a ramp in flight', () => {
    const harness = createHarness({ height: 100, width: 400 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)
    harness.measure.mockReturnValue({ height: 400, width: 400 })
    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MS / 3)
    harness.sendViaApp.mockClear()
    harness.sendViaAppStep.mockClear()

    reporter.dispose()
    vi.advanceTimersByTime(SIZE_RAMP_MS)

    expect(harness.sendViaApp).not.toHaveBeenCalled()
    expect(harness.sendViaAppStep).not.toHaveBeenCalled()
  })

  // A ten-row list is a thousand pixels from its one-block skeleton. At a fixed
  // 240ms the ease-out curve put half of that into the first three frames — a
  // jump, on that scale. So the duration is a floor, and a longer travel takes
  // as long as it needs to stay under the speed ceiling.
  it('a long travel takes longer, at a bounded speed, so a tall list does not lurch', () => {
    const harness = createHarness({ height: 100, width: 400 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)
    harness.sendViaApp.mockClear()
    harness.sendViaAppStep.mockClear()
    harness.measure.mockReturnValue({ height: 1300, width: 400 })
    reporter.measure()

    // Still in flight at the floor duration.
    vi.advanceTimersByTime(SIZE_RAMP_MS)
    expect(harness.sendViaApp).not.toHaveBeenCalled()

    // 1200px at 1.6px/ms is 750ms, rounded up to whole steps.
    vi.advanceTimersByTime(1200 / SIZE_RAMP_SPEED_PX_PER_MS - SIZE_RAMP_MS + 16)
    expect(harness.sendViaApp).toHaveBeenCalledExactlyOnceWith({ height: 1300, width: 400 })

    // No single step moves the frame further than the speed ceiling allows at
    // the curve's steepest — the property that stops a tall card lurching.
    const heights = [100, ...wireHeights(harness)]
    const largestStep = Math.max(...heights.slice(1).map((height, index) => height - (heights[index] ?? 0)))
    expect(largestStep).toBeLessThan(100)
  })

  it('a content ramp is never slower than the opening, however far it travels', () => {
    const harness = createHarness({ height: 100, width: 400 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)
    harness.sendViaApp.mockClear()
    harness.measure.mockReturnValue({ height: 3100, width: 400 })
    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)

    expect(harness.sendViaApp).toHaveBeenCalledExactlyOnceWith({ height: 3100, width: 400 })
  })

  it('flushOnConnect does not resend a size already sent via measure()', () => {
    const harness = createHarness({ height: 300, width: 400 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.measure()
    reporter.flushOnConnect()
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)

    expect(harness.sendViaApp).toHaveBeenCalledOnce()
  })

  it('measureViaFallback sends via the raw fallback channel regardless of connection state', () => {
    const harness = createHarness({ height: 50, width: 60 })
    const reporter = createSizeReporter(harness.channels, harness.measure)

    reporter.measureViaFallback()

    expect(harness.sendFallback).toHaveBeenCalledExactlyOnceWith({ height: 50, width: 60 })
    expect(harness.sendViaApp).not.toHaveBeenCalled()
  })

  it('measureViaFallback dedupes against the last sent size', () => {
    const harness = createHarness({ height: 50, width: 60 })
    const reporter = createSizeReporter(harness.channels, harness.measure)

    reporter.measureViaFallback()
    reporter.measureViaFallback()

    expect(harness.sendFallback).toHaveBeenCalledOnce()
  })

  it('a fallback send never suppresses the SDK send after a late connect', () => {
    // A spec-compliant host ignores raw pre-initialized size notifications, so
    // the SDK path must still send even when the fallback already reported the
    // identical size (the diagnostic painted at 1.5s, then initialize
    // succeeded late).
    const harness = createHarness({ height: 50, width: 60 })
    const reporter = createSizeReporter(harness.channels, harness.measure)

    reporter.measureViaFallback()
    harness.isConnected.current = true
    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)

    expect(harness.sendFallback).toHaveBeenCalledExactlyOnceWith({ height: 50, width: 60 })
    expect(harness.sendViaApp).toHaveBeenCalledExactlyOnceWith({ height: 50, width: 60 })
  })

  it('measureViaFallback no-ops once settled', () => {
    const harness = createHarness({ height: 10, width: 10 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.onRender()
    vi.advanceTimersByTime(SIZE_SETTLE_DELAY_MS)
    harness.measure.mockReturnValue({ height: 80, width: 80 })

    reporter.measureViaFallback()

    expect(harness.sendFallback).not.toHaveBeenCalled()
  })

  it('onRender measures and reports at once; the settle timer re-measures once, and a ramp in flight still lands', () => {
    const harness = createHarness({ height: 10, width: 10 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.onRender()
    expect(harness.measure).toHaveBeenCalledTimes(1)
    expect(harness.sendViaApp).toHaveBeenCalledExactlyOnceWith({ height: 10, width: 10 })

    // A tall late measurement, ramped: the settle window is shorter than a ramp
    // bound by speed can be. Settling stops new measurements; a ramp already in
    // flight lands regardless, so the frame is never left short of the height
    // the card asked for.
    harness.measure.mockReturnValue({ height: 1200, width: 10 })
    reporter.measure()
    vi.advanceTimersByTime(SIZE_SETTLE_DELAY_MS)
    expect(harness.measure).toHaveBeenCalledTimes(3)
    expect(harness.sendViaApp).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)
    expect(harness.sendViaApp).toHaveBeenCalledTimes(2)
    expect(wireHeights(harness).at(-1)).toBe(1200)
    expect(harness.measure).toHaveBeenCalledTimes(3)

    // Settled: further ticks measure nothing further, ladder or not.
    vi.advanceTimersByTime(Math.max(...SIZE_LADDER_DELAYS_MS))
    expect(harness.measure).toHaveBeenCalledTimes(3)
  })

  it('the boot ladder catches a late layout shift (e.g. a font swap) after a render, before settle', () => {
    const harness = createHarness({ height: 10, width: 10 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.armBootLadder()
    reporter.onRender()
    harness.measure.mockReturnValue({ height: 20, width: 10 })
    vi.advanceTimersByTime(SIZE_LADDER_DELAYS_MS[0] + SIZE_RAMP_MS)

    expect(harness.sendViaApp).toHaveBeenLastCalledWith({ height: 20, width: 10 })
  })

  it('stops sending once settled', () => {
    const harness = createHarness({ height: 10, width: 10 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.onRender()
    // Past both the settle (400ms) and any ramp a later measurement could start.
    vi.advanceTimersByTime(SIZE_RAMP_MAX_MS)
    harness.sendViaApp.mockClear()
    harness.sendViaAppStep.mockClear()
    harness.measure.mockReturnValue({ height: 999, width: 999 })

    reporter.measure()
    vi.advanceTimersByTime(Math.max(...SIZE_LADDER_DELAYS_MS))

    expect(harness.sendViaApp).not.toHaveBeenCalled()
  })

  it('a re-render before settle lifts settle and is not settled early by the earlier timer (generation guard)', () => {
    const harness = createHarness({ height: 10, width: 10 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.onRender()
    vi.advanceTimersByTime(SIZE_SETTLE_DELAY_MS - 50)
    // A second render generation before the first settles.
    harness.measure.mockReturnValue({ height: 30, width: 10 })
    reporter.onRender()

    // The first render's settle timer fires here; it must not settle the
    // second generation.
    vi.advanceTimersByTime(50)

    harness.sendViaApp.mockClear()
    harness.measure.mockReturnValue({ height: 40, width: 10 })
    reporter.measure()
    vi.advanceTimersByTime(SIZE_RAMP_MS)
    expect(harness.sendViaApp).toHaveBeenLastCalledWith({ height: 40, width: 10 })

    // The second generation's own settle timer, fired later, does settle.
    vi.advanceTimersByTime(SIZE_SETTLE_DELAY_MS)
    harness.sendViaApp.mockClear()
    harness.measure.mockReturnValue({ height: 50, width: 10 })
    reporter.measure()
    expect(harness.sendViaApp).not.toHaveBeenCalled()
  })

  it('dispose clears pending timers so they never fire', () => {
    const harness = createHarness({ height: 10, width: 10 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.onRender()
    harness.sendViaApp.mockClear()
    reporter.dispose()
    vi.advanceTimersByTime(Math.max(...SIZE_LADDER_DELAYS_MS) + SIZE_SETTLE_DELAY_MS)

    expect(harness.sendViaApp).not.toHaveBeenCalled()
  })

  it('armBootLadder re-measures at each ladder delay', () => {
    const harness = createHarness({ height: 10, width: 10 })
    const reporter = createSizeReporter(harness.channels, harness.measure)
    harness.isConnected.current = true

    reporter.armBootLadder()
    expect(harness.measure).not.toHaveBeenCalled()

    harness.measure.mockReturnValue({ height: 15, width: 10 })
    // The first tick's report is the first report: sent whole, the instant it
    // fires. The extra SIZE_RAMP_MAX_MS in this advance is headroom, not a wait
    // on a ramp — there is nothing left in flight for it to catch.
    vi.advanceTimersByTime(SIZE_LADDER_DELAYS_MS[0] + SIZE_RAMP_MAX_MS)
    expect(harness.sendViaApp).toHaveBeenCalledExactlyOnceWith({ height: 15, width: 10 })

    // The last tick (the second and third measured the same 15, so deduped).
    harness.measure.mockReturnValue({ height: 25, width: 10 })
    vi.advanceTimersByTime(SIZE_LADDER_DELAYS_MS[3] - SIZE_LADDER_DELAYS_MS[0] - SIZE_RAMP_MAX_MS + SIZE_RAMP_MS)
    expect(harness.sendViaApp).toHaveBeenCalledTimes(2)
    expect(harness.sendViaApp).toHaveBeenLastCalledWith({ height: 25, width: 10 })
  })
})
