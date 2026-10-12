/**
 * Ported unchanged from the &Open MCP server's card shell, where every
 * constant here was tuned against live hosts (Claude, ChatGPT); the examples
 * in its comments name that server's cards.
 *
 * Size reporting, built on top of the SDK's `app.sendSizeChanged`
 * (`autoResize: false` — the SDK's own `ResizeObserver`-driven auto-resize
 * would report forever; the 400ms settle window exists because live hosts
 * broke without it).
 *
 * `app.sendSizeChanged` throws "Not connected" before `connect()` resolves,
 * and per spec a host ignores `ui/notifications/size-changed` sent before
 * `ui/notifications/initialized` anyway. So a measurement taken while the
 * handshake is still in flight is coalesced (latest wins) rather than sent,
 * and `flushOnConnect` sends the coalesced value the instant `connect()`
 * resolves.
 *
 * Deliberate, documented spec-nonconformance: when the handshake is known
 * dead (rejected, or never resolves) the shell paints a diagnostic card and
 * needs it to be visible — but a host that never saw
 * `ui/notifications/initialized` keeps the iframe hidden/zero-height
 * forever, since it never gets a spec-legal size report either. `measureViaFallback`
 * exists for exactly that paint: a raw `window.parent.postMessage`
 * `ui/notifications/size-changed` notification outside the SDK/spec, so the
 * diagnostic — which exists to debug precisely this kind of broken host — is
 * actually visible. It is called only for that state-card paint, never for
 * real rendered content.
 */

export interface CardSize {
  readonly height: number
  readonly width: number
}

/** `Math.max` across `documentElement`/`body` scroll/offset sizes. */
export const measureDocument = (): CardSize => {
  const documentElement = document.documentElement
  const body = document.body
  // `documentElement.scrollHeight` cannot report less than the viewport, and in
  // an iframe the viewport is the frame the host currently gives us — so on a
  // card shorter than its frame it just hands that frame back and the card can
  // never shrink. It still carries the one thing the body cannot: content that
  // overflows the html element without growing the body, e.g. something
  // absolutely positioned against the initial containing block. That case is
  // the one where it exceeds the viewport, so read it only then.
  const documentOverflow = documentElement.scrollHeight > documentElement.clientHeight
    ? documentElement.scrollHeight
    : 0
  // `scrollHeight` and `offsetHeight` round to the nearest pixel, so a card
  // 647.34px tall reports 647, the host sets the frame to 647, and the last
  // third of a pixel overflows it. That is enough to show a scrollbar wherever
  // scrollbars take up space (macOS with a mouse attached). A card whose height
  // follows its width — the send preview's square photo tiles — lands on a
  // fraction at most widths, so the exact height is read and rounded up.
  const exactHeight = Math.ceil(body.getBoundingClientRect().height)
  const height = Math.max(documentOverflow, exactHeight, body.scrollHeight || 0, body.offsetHeight || 0) || 1
  const width = Math.max(documentElement.scrollWidth || 0, body.scrollWidth || 0) || globalThis.innerWidth || 1
  return { height, width }
}

/** Background re-measurement ticks that keep checking for late layout shifts (e.g. a font swap) until settled. */
export const SIZE_LADDER_DELAYS_MS = [50, 200, 600, 1600] as const

/** How long after a render the shell keeps reporting size before settling — some hosts dislike resize chatter after "loaded". */
export const SIZE_SETTLE_DELAY_MS = 400

/**
 * How long a change of height takes to reach the host. The host applies each
 * size report by setting the iframe's height, with no transition of its own, so
 * a single report is a jump. Sent as a stream of intermediate heights instead,
 * the frame moves — the only way to animate a container this document does not
 * own. A floor, not the duration: a long travel takes longer, bounded by
 * `SIZE_RAMP_SPEED_PX_PER_MS` below.
 */
export const SIZE_RAMP_MS = 240

/**
 * The longest a ramp may take, however far it travels.
 *
 * Longer than the settle window — as a ramp bound by speed can be too — so a
 * settle timer can fire with a ramp still in flight. That is fine: settling
 * stops *new measurements* from being sent, and a ramp already started lands
 * regardless — the frame is never left short of the height the card asked for.
 *
 * 992 rather than 990: a ramp is a whole number of 16ms steps, and the value
 * here is where it lands, not where it would like to. 990 would land at 992
 * and every test that waits the stated duration would find the last step
 * still pending.
 */
export const SIZE_RAMP_MAX_MS = 992

/**
 * The fastest the frame moves during a content ramp, in pixels per
 * millisecond. `SIZE_RAMP_MS` is a floor: a travel that would exceed this
 * speed in 240ms takes as long as it needs instead, up to the opening's
 * duration. Without it the ramp was 240ms whatever the distance, and on a
 * ten-row campaign list — a thousand pixels — the ease-out curve put half the
 * travel into the first three frames, which on that scale is a jump. 1.6 is
 * just above the speed the one-row gift request's 365px needs to fit the
 * floor, so the case that already read as one motion is unchanged.
 */
export const SIZE_RAMP_SPEED_PX_PER_MS = 1.6

/** One report per frame at 60Hz. Timer-driven rather than `requestAnimationFrame`: these are messages, not paints, and timers are what the tests can drive. */
const SIZE_RAMP_STEP_MS = 16

/**
 * The smallest travel that gets a ramp at all; anything shorter is sent in one
 * report. `SIZE_RAMP_MS` floors the duration, not the distance, so a 1px
 * change still spends the full 15 steps — a 500px card growing to 501px sends
 * `[500, 500, 500, 501, 501, …]`, fourteen messages the host already has and
 * one it needed, because an eased curve over a travel this short rounds almost
 * every step to the same integer. Below the floor a viewer could not tell the
 * steps apart from a jump either way, so there is nothing a ramp buys here.
 * Reachable when a layout shift lands inside the settle window.
 */
const SIZE_RAMP_MIN_TRAVEL_PX = 4

/**
 * A document that measures as nothing has nothing mounted in it yet — the
 * `|| 1` floor in `measureDocument` is what an empty body reports. Sending it
 * would collapse the host's frame to a line, and the host keeps whatever it had
 * until a real measurement arrives if it is simply not sent.
 */
const EMPTY_DOCUMENT_HEIGHT_PX = 1

/** Ease-out cubic: fast away from the old height, settling gently onto the new one. */
const easeOut = (t: number): number => 1 - (1 - t) ** 3

/**
 * Whether the viewer has asked the operating system for less motion. The
 * card's own animations honour it through card.css's `prefers-reduced-motion` block,
 * but a ramp is timer-driven size reports, not CSS — the largest motion on
 * screen, the frame itself, and the one thing no class can stop. Read on every
 * change of height rather than once, so a preference toggled while the card is
 * open takes effect on the next report.
 */
export const prefersReducedMotion = (): boolean => {
  // The DOM types say `matchMedia` is always there; jsdom, where the shell's
  // tests run, does not have it. Reading it off a view of the global whose
  // property is optional is what lets the call be optional without a lint
  // exception — a directly annotated `X | undefined` local would be narrowed
  // straight back by the assignment.
  const global: { readonly matchMedia?: (query: string) => MediaQueryList } = globalThis
  return global.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

export interface SizeChannels {
  /** Whether the SDK app is currently connected (`app.sendSizeChanged` is safe to call). */
  readonly isConnected: () => boolean
  /**
   * Whether this host is one that will never answer `ui/initialize` — a
   * `window.openai` host, which renders from `toolOutput` and leaves the
   * handshake unanswered. Only such a host is sent a size before the handshake
   * resolves: it has no other channel, while a host that does answer would be
   * told a height and then watched the opening ramp start again from zero.
   */
  readonly isPreConnectHost: () => boolean
  /** The raw-postMessage fallback, for a state-card paint on a dead/rejected handshake only. */
  readonly sendFallback: (size: CardSize) => void
  /**
   * `app.sendSizeChanged` for the size the host is being asked for — once per
   * change, when a ramp lands. This is the call to record: it is the size the
   * host was told, and what a card that shows its own reported size shows.
   */
  readonly sendViaApp: (size: CardSize) => void
  /**
   * `app.sendSizeChanged` for one intermediate height of a ramp. Wire only:
   * a ramp is between fifteen and sixty-two of these per change of height, and
   * recording each one would write that many breadcrumbs into a capped ring
   * and re-render the shell as many times for a height the host holds for a
   * frame.
   */
  readonly sendViaAppStep: (size: CardSize) => void
}

export interface SizeReporter {
  /** Arm the background re-measurement ladder once (call on mount). */
  readonly armBootLadder: () => void
  /** Clear all pending timers (call on unmount). */
  readonly dispose: () => void
  /** `connect()` resolved: send the latest coalesced pre-connect measurement, if any and not a duplicate. */
  readonly flushOnConnect: () => void
  /** A generic measurement: sends (deduped) via the app when connected, otherwise coalesces for `flushOnConnect`. No-ops once settled. */
  readonly measure: () => void
  /** The fallback send for a diagnostic-card paint on a dead/rejected handshake. No-ops once settled. */
  readonly measureViaFallback: () => void
  /** A new render generation: immediate measurement, then lifts settle and arms the 400ms settle timer, guarded so a re-render inside the window is not settled early by the previous render's timer. */
  readonly onRender: () => void
}

/** Exported because the shell dedupes against it too: a re-report of the same size must not re-render a card that shows it. */
export const isSameSize = (a: CardSize | undefined, b: CardSize): boolean => a !== undefined && a.width === b.width && a.height === b.height

/** Create an independent size reporter — one per `CardShell` mount. */
export const createSizeReporter = (channels: SizeChannels, measure: () => CardSize = measureDocument, reducedMotion: () => boolean = prefersReducedMotion): SizeReporter => {
  // `lastSent` is the size the host was last asked for — the target of any ramp
  // in flight — and what a measurement is deduped against. `wireHeight` is the
  // height most recently put on the wire, which during a ramp lags behind it;
  // a retarget mid-ramp starts from here, so the frame turns rather than jumps.
  let lastSent: CardSize | undefined
  let wireHeight: number | undefined
  let lastFallback: CardSize | undefined
  let pendingPreConnect: CardSize | undefined
  let settled = false
  let generation = 0
  let rampTimer: ReturnType<typeof setTimeout> | undefined
  const timers = new Set<ReturnType<typeof setTimeout>>()

  const clearTimers = (): void => {
    for (const timer of timers) clearTimeout(timer)
    timers.clear()
    rampTimer = undefined
  }

  const cancelRamp = (): void => {
    if (rampTimer === undefined) return
    clearTimeout(rampTimer)
    timers.delete(rampTimer)
    rampTimer = undefined
  }

  /**
   * Ask the host for `target`: in one message for the first report, for a
   * width-only change, or when the viewer prefers reduced motion; otherwise as
   * a ramp of intermediate heights from where the wire currently stands. Only
   * the landing step goes through `sendViaApp`; the rest are steps.
   *
   * The first report is a jump on purpose. The host draws a placeholder of its
   * own while it fetches the card — Claude's is around 380px — and holds it
   * until the card speaks, so a ramp from zero collapses that placeholder to a
   * sliver and climbs back over a second, which is two movements in opposite
   * directions before anything is readable. Reported whole, the frame settles
   * once, onto the skeleton, and the skeleton fades in over it
   * (`--animate-appear`). The one height the reader watches move is the
   * skeleton growing into the card.
   */
  const sendViaAppSmoothly = (target: CardSize): void => {
    cancelRamp()
    lastSent = target
    const from = wireHeight
    if (from === undefined || reducedMotion()) {
      wireHeight = target.height
      channels.sendViaApp(target)
      return
    }
    const distance = Math.abs(target.height - from)
    if (distance < SIZE_RAMP_MIN_TRAVEL_PX) {
      wireHeight = target.height
      channels.sendViaApp(target)
      return
    }
    const duration = Math.min(SIZE_RAMP_MAX_MS, Math.max(SIZE_RAMP_MS, distance / SIZE_RAMP_SPEED_PX_PER_MS))
    const steps = Math.ceil(duration / SIZE_RAMP_STEP_MS)
    let step = 0
    const tick = (): void => {
      step += 1
      if (step >= steps) {
        // The final step lands exactly on the target: rounding the eased value
        // could otherwise leave the frame a pixel short forever.
        wireHeight = target.height
        rampTimer = undefined
        channels.sendViaApp(target)
        return
      }
      const height = Math.round(from + (target.height - from) * easeOut(step / steps))
      wireHeight = height
      channels.sendViaAppStep({ height, width: target.width })
      schedule()
    }
    // Through `addTimer` like every other timer here, so tracking stays in one
    // place; the id is kept as well, because this is the one timer that is
    // cancelled on its own when a ramp is retargeted.
    const schedule = (): void => {
      rampTimer = addTimer(tick, SIZE_RAMP_STEP_MS)
    }
    schedule()
  }

  /** The document's size, or nothing when it is too early to have one: settled, or a body that measures as empty. */
  const measureIfReportable = (): CardSize | undefined => {
    if (settled) return undefined
    const size = measure()
    return size.height <= EMPTY_DOCUMENT_HEIGHT_PX ? undefined : size
  }

  const measureNow = (): void => {
    const size = measureIfReportable()
    if (size === undefined) return
    if (!channels.isConnected()) {
      // Pre-connect: coalesce for the SDK flush at connect resolution, and send
      // via the raw channel now only to a host that will never answer the
      // handshake — it would otherwise get no size on any channel for a
      // fast-path render. A host that does answer is left alone: the spec says
      // it ignores a pre-initialized notification, but one that honours it
      // instead would size its frame and then watch `flushOnConnect` open a
      // ramp from zero underneath it, collapsing the card it had just drawn.
      pendingPreConnect = size
      if (channels.isPreConnectHost()) sendFallbackDeduped(size)
      return
    }
    if (isSameSize(lastSent, size)) return
    sendViaAppSmoothly(size)
  }

  // Deduped against fallback sends only — never recorded in lastSent. A
  // spec-compliant host ignores raw pre-initialized notifications, so if the
  // handshake later succeeds the SDK path must still send this size; sharing
  // lastSent would dedupe it away and leave the iframe unsized.
  const sendFallbackDeduped = (size: CardSize): void => {
    if (isSameSize(lastFallback, size)) return
    lastFallback = size
    // The wire carries this height now, whoever put it there. A host that acts
    // on the raw notification has drawn the frame at it, and a later ramp that
    // still believed the wire was empty would open from zero underneath it,
    // collapsing the card before growing it back.
    wireHeight = size.height
    channels.sendFallback(size)
  }

  const measureViaFallback = (): void => {
    const size = measureIfReportable()
    if (size === undefined) return
    sendFallbackDeduped(size)
  }

  const flushOnConnect = (): void => {
    const size = pendingPreConnect
    pendingPreConnect = undefined
    if (settled || size === undefined || isSameSize(lastSent, size)) return
    sendViaAppSmoothly(size)
  }

  // Timers remove themselves from the tracking set when they fire —
  // clearTimeout on a fired id is a no-op, but a long-lived card receiving
  // many re-deliveries would otherwise grow the set unboundedly.
  const addTimer = (callback: () => void, delay: number): ReturnType<typeof setTimeout> => {
    const timer = setTimeout(() => {
      timers.delete(timer)
      callback()
    }, delay)
    timers.add(timer)
    return timer
  }

  const armBootLadder = (): void => {
    for (const delay of SIZE_LADDER_DELAYS_MS) {
      addTimer(measureNow, delay)
    }
  }

  const onRender = (): void => {
    settled = false
    generation += 1
    const renderGeneration = generation
    measureNow()
    addTimer(() => {
      if (renderGeneration !== generation) return
      measureNow()
      settled = true
    }, SIZE_SETTLE_DELAY_MS)
  }

  return { armBootLadder, dispose: clearTimers, flushOnConnect, measure: measureNow, measureViaFallback, onRender }
}
