/**
 * The shared card shell, ported from the &Open MCP server: the one place every
 * card's SDK bridge, lifecycle state machine, sizing and breadcrumbs live, so a
 * card component is only "how does this data render".
 *
 * State machine:
 *
 *   boot -> Rendered immediately if `window.openai.toolOutput` is already
 *     present (the ChatGPT fast path).
 *   boot -> after 1.5s, if nothing has rendered: Waiting (handshake resolved,
 *     tool likely still running) or Diagnostic (handshake rejected or silent).
 *     A card that passes a `skeleton` draws it from the first render until
 *     content or the diagnostic replaces it.
 *   Waiting -> Diagnostic after a further 10.5s (~12s in all).
 *   Any state -> Rendered on a tool-result, before or after the handshake.
 *   Rendered is ABSORBING: derived from "is there render data yet", so no
 *     later timer or connect error can paint a state card over content.
 *
 * `useApp`'s `onAppCreated` fires before `connect()`, so every handler is
 * registered there (`wireCardApp`, exported so the bridge tests drive this
 * exact wiring against a real `App`/`AppBridge` pair) and an early
 * tool-result is never missed. `autoResize: false`: sizing is `sizing.ts`'s
 * settle logic, not the SDK's forever-observing `ResizeObserver`.
 *
 * Two departures from the &Open shell. A card here can grow after it settles
 * (a "Show all" press), so the render context carries `remeasure`, which
 * re-arms the size reporter for one more settle window. And the shell adopts
 * the host's theme and style variables (`host-theme.ts`) rather than holding a
 * brand palette apart from them.
 */

import type { App, McpUiAppCapabilities } from '@modelcontextprotocol/ext-apps'
import type { ReactNode } from 'react'

import { useApp } from '@modelcontextprotocol/ext-apps/react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import type { BreadcrumbLog } from './breadcrumbs.ts'
import type { CardResultError, CardToolResultLike } from './use-card-result.ts'

import './card.css'
import { createBreadcrumbLog } from './breadcrumbs.ts'
import { applyHostLook } from './host-theme.ts'
import { Card } from './primitives.tsx'
import { createSizeReporter, measureDocument, prefersReducedMotion } from './sizing.ts'
import { useCardResult } from './use-card-result.ts'

// ChatGPT exposes the tool output synchronously on `window.openai` instead of
// (or ahead of) the MCP Apps postMessage handshake.
declare global {
  var openai: { readonly toolOutput?: Record<string, unknown> } | undefined
}

/** Milliseconds after boot before an unfinished handshake paints a state card. */
const STATE_CARD_DELAY_MS = 1500

/** Further milliseconds a "waiting" card tolerates before escalating to diagnostic. */
const WAITING_TO_DIAGNOSTIC_MS = 10_500

/** The two fades in `card.css`, and the margin the overlay outlasts them by. */
const FADE_OUT_MS = 200
const REVEAL_MS = 500
const OVERLAY_MARGIN_MS = 100

/** How far the frame must have opened for the skeleton to count as seen. */
const SKELETON_SEEN_PX = 48

/**
 * How close the frame must already be to the content's height, on the first
 * content render, for the card to count as a remount. Claude re-creates the
 * widget's iframe as its own UI around the tool call changes, and brings the
 * new one back at the card's last reported height; fading in again would
 * replay the load on every remount.
 */
const REMOUNT_TOLERANCE_PX = 4

/** What content fades in from on its first render, decided once. */
export interface FadeIn {
  readonly overlay: 'blank' | 'skeleton'
  readonly slow: boolean
}

export interface FadeInEvidence {
  /** Whether the skeleton survived to a paint. */
  readonly skeletonPainted: boolean
  /** The height most recently put on the wire; a skeleton painted into a 0px frame was not seen either. */
  readonly wireHeight: number
}

/**
 * Seen, the skeleton itself dissolves, quickly. Not seen, a blank card
 * dissolves in its place, slowly — dissolving shapes nobody watched arrive is
 * a flash of them.
 */
export const chooseFadeIn = ({ skeletonPainted, wireHeight }: FadeInEvidence): FadeIn => {
  const seen = skeletonPainted && wireHeight >= SKELETON_SEEN_PX
  return { overlay: seen ? 'skeleton' : 'blank', slow: !seen }
}

const CARD_CAPABILITIES: McpUiAppCapabilities = { availableDisplayModes: ['inline'] }
const CARD_APP_VERSION = '1'

export interface CardWiringCallbacks {
  readonly onError: (error: Error) => void
  /** The host changed something it reported at initialize — a theme, a frame. */
  readonly onHostContextChanged?: () => void
  readonly onResult: (result: CardToolResultLike | null | undefined) => void
  readonly onTeardown?: () => void
}

/**
 * Register every SDK handler the shell needs, before `connect()`. Standalone,
 * with no React, so the bridge tests exercise this exact wiring.
 */
export const wireCardApp = (app: App, callbacks: CardWiringCallbacks): void => {
  app.addEventListener('toolresult', (parameters) => {
    callbacks.onResult(parameters)
  })
  // No params forwarded: the SDK has already merged the change into
  // `getHostContext()` by the time a listener runs, and the whole is what
  // `applyHostLook` wants.
  app.addEventListener('hostcontextchanged', () => {
    callbacks.onHostContextChanged?.()
  })
  // The SDK does not answer ui/resource-teardown unless this is set, and an
  // unanswered request is a protocol violation; `{}` is the complete answer.
  app.onteardown = () => {
    callbacks.onTeardown?.()
    return {}
  }
  app.onerror = (error) => {
    callbacks.onError(error)
  }
}

const describeError = (error: Error): string => error.message

/** What a card needs beyond its data. */
export interface CardRenderContext {
  readonly app: App | null
  readonly breadcrumbs: BreadcrumbLog
  /** Set when the tool returned `isError`; `data` is empty whenever it is. */
  readonly error: CardResultError | undefined
  /**
   * Ask the shell to measure again after a change of height the shell did not
   * cause — a list expanded in place. Re-arms the settle window once.
   */
  readonly remeasure: () => void
}

/** Stable, so the render prop's data argument keeps its identity across renders. */
const NO_DATA: Record<string, unknown> = {}

export interface CardShellProperties {
  /** This card's `appInfo.name` for the handshake, e.g. `"pinboard-bookmarks-card"`. */
  readonly appName: string
  readonly children: (data: Record<string, unknown>, context: CardRenderContext) => ReactNode
  /** This card's loading skeleton, drawn from the first render until the result lands. */
  readonly skeleton?: ReactNode
  /** `document.title`, set on mount. */
  readonly title: string
}

type StatePhase = 'connecting' | 'diagnostic' | 'waiting'

export const CardShell = ({ appName, children, skeleton, title }: CardShellProperties): ReactNode => {
  useLayoutEffect(() => {
    document.title = title
  }, [title])

  const [breadcrumbs] = useState(createBreadcrumbLog)
  const { data, error: resultError, generation, recordData, recordResult } = useCardResult()
  const appReference = useRef<App | null>(null)
  const isConnectedReference = useRef(false)
  // "Has anything rendered yet", for the timer callbacks, which close over
  // refs because they cannot see a later render.
  const hasRenderedReference = useRef(false)
  const skeletonPaintedReference = useRef(false)
  const wireHeightReference = useRef(0)
  // Fixed on the first content render: a choice that changed mid-fade would
  // swap the overlay's animation and restart it.
  const fadeInReference = useRef<FadeIn | undefined>(undefined)
  const [crossfadeDone, setCrossfadeDone] = useState(false)

  const { app, error, isConnected } = useApp({
    appInfo: { name: appName, version: CARD_APP_VERSION },
    autoResize: false,
    capabilities: CARD_CAPABILITIES,
    onAppCreated: (created) => {
      appReference.current = created
      wireCardApp(created, {
        onError: (transportError) => {
          breadcrumbs.record('transport:error', transportError.message)
        },
        onHostContextChanged: () => {
          breadcrumbs.record('host:context-changed', applyHostLook(created))
        },
        onResult: (result) => {
          const rendered = recordResult(result)
          if (rendered) hasRenderedReference.current = true
          breadcrumbs.record(rendered ? 'render:tool-result' : 'render:tool-result-unparseable')
        },
        onTeardown: () => {
          breadcrumbs.record('teardown:received')
        },
      })
    },
  })

  const recordSizeError = (sendError: unknown): void => {
    breadcrumbs.record('size:error', sendError instanceof Error ? sendError.message : String(sendError))
  }

  const [sizeReporter] = useState(() => createSizeReporter({
    isConnected: () => isConnectedReference.current,
    isPreConnectHost: () => globalThis.openai !== undefined,
    sendFallback: (size) => {
      breadcrumbs.record('size:fallback', `${String(size.width)}x${String(size.height)}`)
      // Deliberate spec-nonconformance, as in the &Open shell: a dead
      // handshake never reaches `sendSizeChanged`, and a host keeps an unsized
      // iframe hidden — so the diagnostic card that exists to debug that host
      // reports its size outside the spec channel. No origin is knowable here.
      globalThis.parent.postMessage({ jsonrpc: '2.0', method: 'ui/notifications/size-changed', params: size }, '*')
    },
    sendViaApp: (size) => {
      const current = appReference.current
      if (!current) return
      breadcrumbs.record('size:sent', `${String(size.width)}x${String(size.height)}`)
      wireHeightReference.current = size.height
      current.sendSizeChanged(size).catch(recordSizeError)
    },
    // A ramp step: wire only, no breadcrumb (dozens per change would evict
    // the useful ones from the capped ring).
    sendViaAppStep: (size) => {
      const current = appReference.current
      if (!current) return
      wireHeightReference.current = size.height
      current.sendSizeChanged(size).catch(recordSizeError)
    },
  }))

  const remeasure = useCallback(() => {
    sizeReporter.onRender()
  }, [sizeReporter])

  useEffect(() => {
    isConnectedReference.current = isConnected
  }, [isConnected])

  useEffect(() => {
    sizeReporter.armBootLadder()
    return () => {
      sizeReporter.dispose()
    }
  }, [sizeReporter])

  useEffect(() => {
    if (!isConnected) return
    breadcrumbs.record('connect:resolved', applyHostLook(app))
    sizeReporter.flushOnConnect()
  }, [app, breadcrumbs, isConnected, sizeReporter])

  useEffect(() => {
    if (!error) return
    // Rendered is absorbing: this only ever logs.
    breadcrumbs.record('connect:error', describeError(error))
  }, [breadcrumbs, error])

  // The ChatGPT fast path, before the browser paints, so that host never
  // shows a connecting flash.
  useLayoutEffect(() => {
    const toolOutput = globalThis.openai?.toolOutput
    if (toolOutput) {
      hasRenderedReference.current = true
      recordData(toolOutput)
    }
  }, [recordData])

  const [statePhase, setStatePhase] = useState<StatePhase>('connecting')

  useEffect(() => {
    let waitingTimer: ReturnType<typeof setTimeout> | undefined
    const initialTimer = setTimeout(() => {
      if (hasRenderedReference.current) return
      if (isConnectedReference.current) {
        setStatePhase('waiting')
        waitingTimer = setTimeout(() => {
          if (hasRenderedReference.current) return
          setStatePhase('diagnostic')
        }, WAITING_TO_DIAGNOSTIC_MS)
      }
      else {
        setStatePhase('diagnostic')
      }
    }, STATE_CARD_DELAY_MS)
    return () => {
      clearTimeout(initialTimer)
      if (waitingTimer !== undefined) clearTimeout(waitingTimer)
    }
  }, [])

  useEffect(() => {
    if (generation === 0) return
    sizeReporter.onRender()
  }, [generation, sizeReporter])

  // A passive effect runs after paint: if the skeleton is still the rendered
  // branch by then, it was painted.
  useEffect(() => {
    if (!hasRenderedReference.current && skeleton !== undefined) {
      skeletonPaintedReference.current = true
    }
  }, [skeleton])

  const hasSkeleton = skeleton !== undefined
  useEffect(() => {
    if (generation !== 1 || !hasSkeleton) return
    const fading = fadeInReference.current?.slow ? REVEAL_MS : FADE_OUT_MS
    const timer = setTimeout(() => {
      setCrossfadeDone(true)
    }, fading + OVERLAY_MARGIN_MS)
    return () => {
      clearTimeout(timer)
    }
  }, [generation, hasSkeleton])

  // A remount shows its content at once; a layout effect, so the overlay
  // committed with the content is dropped before the first paint.
  useLayoutEffect(() => {
    if (generation !== 1 || fadeInReference.current === undefined) return
    if (Math.abs(measureDocument().height - globalThis.innerHeight) <= REMOUNT_TOLERANCE_PX) {
      setCrossfadeDone(true)
    }
  }, [generation])

  useEffect(() => {
    if (data !== undefined || resultError !== undefined) return
    if (statePhase === 'waiting') {
      sizeReporter.measure()
    }
    else if (statePhase === 'diagnostic') {
      if (isConnected) sizeReporter.measure()
      else sizeReporter.measureViaFallback()
    }
  }, [data, isConnected, resultError, sizeReporter, statePhase])

  if (data !== undefined || resultError !== undefined) {
    const content = children(data ?? NO_DATA, { app: appReference.current, breadcrumbs, error: resultError, remeasure })

    const fadingIn = skeleton !== undefined && generation === 1 && !crossfadeDone && !prefersReducedMotion()
    if (fadingIn) {
      fadeInReference.current ??= chooseFadeIn({
        skeletonPainted: skeletonPaintedReference.current,
        wireHeight: wireHeightReference.current,
      })
    }
    const fadeIn = fadeInReference.current

    // The content is in flow from this first commit, under the overlay, so
    // the measurement that ramps the frame is taken at once. It is never
    // wrapped: ending the crossfade drops only the overlay, and React keeps
    // the card it already built.
    return (
      <>
        {content}
        {fadingIn && fadeIn !== undefined && (
          <div aria-hidden="true" className={`overlay ${fadeIn.slow ? 'overlay--reveal' : 'overlay--fade-out'}`}>
            {fadeIn.overlay === 'blank' ? <div className="overlay__blank" /> : <Card fill>{skeleton}</Card>}
          </div>
        )}
      </>
    )
  }

  if (statePhase === 'diagnostic') {
    const errorText = error ? describeError(error) : undefined
    return (
      <Card label="Card diagnostic">
        <p className="state-title">Card diagnostic</p>
        {errorText !== undefined && <p className="state-detail">{`ui/initialize error: ${errorText}`}</p>}
        <p className="state-detail">{`JS ran; window.openai: ${String(Boolean(globalThis.openai))}`}</p>
        <p className="state-detail">{`Events (${String(breadcrumbs.entries().length)}): ${breadcrumbs.summary()}`}</p>
      </Card>
    )
  }

  if (skeleton !== undefined) {
    return (
      <Card fill>
        <div className="skeleton-group" role="status">
          <span className="visually-hidden">Loading…</span>
          {skeleton}
        </div>
      </Card>
    )
  }

  if (statePhase === 'waiting') {
    return (
      <Card>
        <p className="state-detail">Waiting for the tool result…</p>
      </Card>
    )
  }

  return null
}
