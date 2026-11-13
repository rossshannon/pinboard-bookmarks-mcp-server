/**
 * How a card calls a server tool of its own accord: the bookmarks card asking
 * for link previews after it has rendered. `link-opener.ts`'s sibling, and the
 * same choice of channel:
 *
 * - the connected MCP Apps bridge (`tools/call`, proxied by the host), when
 *   the host said at the handshake that it proxies tool calls;
 * - before the handshake, ChatGPT's own `window.openai.callTool` when that
 *   exists, since that host may render from `toolOutput` and never finish it;
 * - if that early channel refuses the call, or there is none, the call waits
 *   for the bridge to connect, up to the timeout, and goes down it then.
 *
 * Every call resolves and none rejects. A card's own tool call is a nicety on
 * top of a result it has already drawn, so whatever goes wrong — no bridge, a
 * host that does not proxy tool calls or refuses this one, a tool that fails,
 * an answer that never comes — is one outcome, "unavailable", with the reason
 * kept for the breadcrumbs. The card carries on with what it has.
 *
 * A call the card has stopped waiting for is never sent late. One parked for
 * the bridge finds, when the bridge arrives, that its time ran out, and goes
 * no further: the card has moved on, and may already have asked again.
 *
 * Two of the reasons are not final. "not-connected" and
 * "rejected-before-connect" say only that no bridge came in time; one may
 * still connect, and `onConnect` tells a card when it has, so the card can
 * ask again down the proper channel.
 */

import type { BreadcrumbLog } from './breadcrumbs.ts'
import type { CardToolResultLike } from './use-card-result.ts'

import { extractToolError, extractToolResult } from './use-card-result.ts'

/** How long a call may take before the card stops waiting for it. */
export const TOOL_CALL_TIMEOUT_MS = 15_000

export type ToolUnavailableReason =
  /** The host said at the handshake that it does not proxy tool calls. */
  | 'no-capability'
  /** No bridge connected in time, and no other channel to use. */
  | 'not-connected'
  /** The host, or the server behind it, refused the call. */
  | 'rejected'
  /**
   * ChatGPT's `window.openai.callTool`, tried because the bridge had not
   * connected yet, refused the call or answered with nothing readable, and
   * no bridge connected in time to try instead.
   */
  | 'rejected-before-connect'
  /** Nothing came back in time. */
  | 'timeout'
  /** The tool ran and reported a failure. */
  | 'tool-error'
  /** The tool answered with nothing a card can read. */
  | 'unreadable'

export type ToolCallOutcome =
  | { readonly status: 'ok', readonly data: Record<string, unknown> }
  | { readonly status: 'unavailable', readonly reason: ToolUnavailableReason }

/** The `App` capabilities a tool call needs, narrowed so a test double need not shape a whole `App`. */
export interface ToolCallingApp {
  readonly callServerTool: (
    params: { arguments?: Record<string, unknown>, name: string },
    options?: { timeout?: number },
  ) => Promise<CardToolResultLike>
  readonly getHostCapabilities: () => { readonly serverTools?: object | undefined } | undefined
}

export interface ToolCaller {
  /**
   * Call a server tool through the host and resolve to its
   * `structuredContent`, or to "unavailable". Never rejects.
   */
  readonly callTool: (name: string, toolArguments: Record<string, unknown>) => Promise<ToolCallOutcome>
  /**
   * Whether a call made now has a channel to go down: `false` once the host
   * is known not to proxy tool calls, so a card need not draw a placeholder
   * for an answer that cannot come.
   */
  readonly canCallTools: () => boolean
  /**
   * Call `listener` when the bridge finishes its handshake; returns the
   * unsubscribe. A call that was "unavailable" for want of a bridge is worth
   * making again then, if `canCallTools()` still says so.
   */
  readonly onConnect: (listener: () => void) => () => void
}

export interface DeferredToolCaller extends ToolCaller {
  /** Release the calls waiting for the bridge, and tell the `onConnect` listeners. Call once it has connected. */
  readonly flush: () => void
}

type OpenAiCallTool = (name: string, toolArguments: Record<string, unknown>) => Promise<unknown>

const openAiCallTool = (): OpenAiCallTool | undefined => {
  const candidate = globalThis.openai?.callTool
  return typeof candidate === 'function' ? candidate : undefined
}

const describeRejection = (error: unknown): string => error instanceof Error ? error.message : String(error)

const unavailable = (reason: ToolUnavailableReason): ToolCallOutcome => ({ reason, status: 'unavailable' })

/** A tool result, from either channel, as an outcome. */
const readOutcome = (result: unknown): ToolCallOutcome => {
  if (typeof result !== 'object' || result === null) return unavailable('unreadable')
  const resultLike = result as CardToolResultLike
  if (extractToolError(resultLike) !== undefined) return unavailable('tool-error')
  const data = extractToolResult(resultLike)
  return data === undefined ? unavailable('unreadable') : { data, status: 'ok' }
}

/** `connectedApp` returns the App only once its handshake is done, `null` before. */
export const createToolCaller = (
  connectedApp: () => ToolCallingApp | null,
  breadcrumbs: BreadcrumbLog,
  timeoutMs = TOOL_CALL_TIMEOUT_MS,
): DeferredToolCaller => {
  const waiting: Array<() => void> = []
  const connectListeners = new Set<() => void>()

  /** One call's standing: whether anyone is still waiting for its answer. */
  interface Standing { wanted: boolean }

  /** Try ChatGPT's own channel. `undefined` means it did not work, and the bridge is still worth trying. */
  const sendEarly = async (viaOpenAi: OpenAiCallTool, name: string, toolArguments: Record<string, unknown>): Promise<ToolCallOutcome | undefined> => {
    breadcrumbs.record('callTool:openai', name)
    // What this channel resolves to is not documented, and a host may offer
    // it without letting this tool through it. Neither says the bridge would
    // refuse. A tool that ran and failed is another matter: that is final.
    try {
      const outcome = readOutcome(await viaOpenAi(name, toolArguments))
      return outcome.status === 'unavailable' && outcome.reason === 'unreadable' ? undefined : outcome
    }
    catch (error) {
      breadcrumbs.record('callTool:error', describeRejection(error))
      return undefined
    }
  }

  const send = async (name: string, toolArguments: Record<string, unknown>, standing: Standing, early: { failed: boolean }): Promise<ToolCallOutcome> => {
    let app = connectedApp()
    if (!app) {
      const viaOpenAi = openAiCallTool()
      if (viaOpenAi) {
        const outcome = await sendEarly(viaOpenAi, name, toolArguments)
        if (outcome !== undefined) return outcome
        early.failed = true
        // The bridge may have connected while that was in flight.
        app = connectedApp()
      }
    }
    if (!app) {
      breadcrumbs.record('callTool:held', name)
      await new Promise<void>((resolve) => {
        waiting.push(resolve)
      })
      app = connectedApp()
    }
    // Time ran out while this waited, and the card was told so: sending it
    // now would be a call nobody reads, beside whatever the card sent since.
    if (!standing.wanted) return unavailable('timeout')
    if (!app) return unavailable(early.failed ? 'rejected-before-connect' : 'not-connected')
    if (app.getHostCapabilities()?.serverTools === undefined) return unavailable('no-capability')
    return readOutcome(await app.callServerTool({ arguments: toolArguments, name }, { timeout: timeoutMs }))
  }

  return {
    callTool: async (name, toolArguments) => {
      breadcrumbs.record('callTool:requested', name)
      const standing: Standing = { wanted: true }
      const early = { failed: false }
      let timer: ReturnType<typeof setTimeout> | undefined
      const timedOut = new Promise<ToolCallOutcome>((resolve) => {
        timer = setTimeout(() => {
          if (connectedApp()) resolve(unavailable('timeout'))
          else resolve(unavailable(early.failed ? 'rejected-before-connect' : 'not-connected'))
        }, timeoutMs)
      })
      const answered = send(name, toolArguments, standing, early).catch((error: unknown): ToolCallOutcome => {
        breadcrumbs.record('callTool:error', describeRejection(error))
        return unavailable('rejected')
      })
      const outcome = await Promise.race([answered, timedOut])
      clearTimeout(timer)
      standing.wanted = false
      breadcrumbs.record(outcome.status === 'ok' ? 'callTool:result' : 'callTool:unavailable', outcome.status === 'ok' ? name : `${name} ${outcome.reason}`)
      return outcome
    },
    canCallTools: () => {
      const app = connectedApp()
      if (app) return app.getHostCapabilities()?.serverTools !== undefined
      // Before the handshake a call waits for it, unless this is ChatGPT
      // rendering from `toolOutput` with no `callTool` of its own: there the
      // handshake may never come, and a card should not hold a place for it.
      return globalThis.openai === undefined || openAiCallTool() !== undefined
    },
    flush: () => {
      for (const release of waiting.splice(0)) release()
      for (const listener of connectListeners) listener()
    },
    onConnect: (listener) => {
      connectListeners.add(listener)
      return () => {
        connectListeners.delete(listener)
      }
    },
  }
}
