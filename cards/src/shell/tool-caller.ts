/**
 * How a card calls a server tool of its own accord: the bookmarks card asking
 * for link previews after it has rendered. `link-opener.ts`'s sibling, and the
 * same choice of channel:
 *
 * - the connected MCP Apps bridge (`tools/call`, proxied by the host), when
 *   the host said at the handshake that it proxies tool calls;
 * - before the handshake, ChatGPT's own `window.openai.callTool` when that
 *   exists, since that host may render from `toolOutput` and never finish it;
 * - otherwise the call waits for the bridge to connect, up to the timeout.
 *
 * Every call resolves and none rejects. A card's own tool call is a nicety on
 * top of a result it has already drawn, so whatever goes wrong — no bridge, a
 * host that does not proxy tool calls or refuses this one, a tool that fails,
 * an answer that never comes — is one outcome, "unavailable", with the reason
 * kept for the breadcrumbs. The card carries on with what it has.
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
}

export interface DeferredToolCaller extends ToolCaller {
  /** Release the calls waiting for the bridge. Call once it has connected. */
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

  const send = async (name: string, toolArguments: Record<string, unknown>): Promise<ToolCallOutcome> => {
    let app = connectedApp()
    if (!app) {
      const viaOpenAi = openAiCallTool()
      if (viaOpenAi) {
        breadcrumbs.record('callTool:openai', name)
        return readOutcome(await viaOpenAi(name, toolArguments))
      }
      breadcrumbs.record('callTool:held', name)
      await new Promise<void>((resolve) => {
        waiting.push(resolve)
      })
      app = connectedApp()
      if (!app) return unavailable('not-connected')
    }
    if (app.getHostCapabilities()?.serverTools === undefined) return unavailable('no-capability')
    return readOutcome(await app.callServerTool({ arguments: toolArguments, name }, { timeout: timeoutMs }))
  }

  return {
    callTool: async (name, toolArguments) => {
      breadcrumbs.record('callTool:requested', name)
      let timer: ReturnType<typeof setTimeout> | undefined
      const timedOut = new Promise<ToolCallOutcome>((resolve) => {
        timer = setTimeout(() => {
          resolve(unavailable(connectedApp() ? 'timeout' : 'not-connected'))
        }, timeoutMs)
      })
      const answered = send(name, toolArguments).catch((error: unknown): ToolCallOutcome => {
        breadcrumbs.record('callTool:error', describeRejection(error))
        return unavailable('rejected')
      })
      const outcome = await Promise.race([answered, timedOut])
      clearTimeout(timer)
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
    },
  }
}
