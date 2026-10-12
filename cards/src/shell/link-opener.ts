/**
 * Where a card's link clicks go. SafeLink hands every plain click here; this
 * picks the channel the host will actually answer:
 *
 * - the connected MCP Apps bridge (`ui/open-link`), once the handshake is done;
 * - before that, ChatGPT's own `window.openai.openExternal`, since that host
 *   may render from `toolOutput` and never finish the handshake;
 * - otherwise the click is held and sent through the bridge when it connects.
 *
 * Nothing is posted outside the bridge: a raw `ui/open-link` either reads as
 * a notification no host handles (no id) or earns a reply the card's own
 * transport never asked for (with one).
 */

import type { BreadcrumbLog } from './breadcrumbs.ts'

/** The one `App` capability a link needs, narrowed so a test double need not shape a whole `App`. */
export interface LinkOpener {
  readonly openLink: (params: { url: string }) => Promise<unknown>
}

export interface DeferredLinkOpener extends LinkOpener {
  /** Send the clicks held before the bridge connected. Call once it has. */
  readonly flush: () => void
}

const describeRejection = (error: unknown): string => error instanceof Error ? error.message : String(error)

/** `connectedApp` returns the App only once its handshake is done, `null` before. */
export const createLinkOpener = (connectedApp: () => LinkOpener | null, breadcrumbs: BreadcrumbLog): DeferredLinkOpener => {
  const held: string[] = []
  return {
    flush: () => {
      const app = connectedApp()
      if (!app) return
      for (const url of held.splice(0)) {
        breadcrumbs.record('openLink:flushed', url)
        app.openLink({ url }).catch((error: unknown) => {
          breadcrumbs.record('openLink:error', describeRejection(error))
        })
      }
    },
    openLink: async ({ url }) => {
      const app = connectedApp()
      if (app) return app.openLink({ url })
      const openExternal = globalThis.openai?.openExternal
      if (openExternal) {
        breadcrumbs.record('openLink:openai', url)
        openExternal({ href: url })
        return {}
      }
      breadcrumbs.record('openLink:held', url)
      held.push(url)
      return {}
    },
  }
}
