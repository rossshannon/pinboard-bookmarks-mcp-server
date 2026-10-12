/**
 * The one sanctioned place a card renders a real `href`. Ported from the
 * &Open card shell; the scheme rule is Pinboard's (see `safe-url.ts`).
 *
 * Renders an anchor only for an `http(s)://` URL, so a hostile URL renders no
 * anchor at all rather than a neutered one. A real anchor keeps native
 * affordances — middle-click, cmd/ctrl-click, "copy link address" — because
 * only the plain primary click is intercepted.
 *
 * That click always goes through `app.openLink`: the host sandbox blocks top
 * navigation. Before the bridge connects (or on a `window.openai` host that
 * never answers the handshake) there is no `app`, so the standard method is
 * posted raw instead, mirroring the sizing fallback in `sizing.ts`.
 */

import type { App } from '@modelcontextprotocol/ext-apps'
import type { MouseEvent, ReactNode } from 'react'

import type { BreadcrumbLog } from './breadcrumbs.ts'

import { isWebUrl } from './safe-url.ts'

/** The one `App` capability `SafeLink` needs, narrowed so a test double need not shape a whole `App`. */
interface LinkOpener {
  readonly openLink: App['openLink']
}

export interface SafeLinkProperties {
  readonly app: LinkOpener | null
  readonly breadcrumbs: BreadcrumbLog
  readonly children: ReactNode
  readonly className?: string
  readonly title?: string
  readonly url: string
}

let rawRequestId = 0

/** An `http(s)://`-only link that opens through the host; renders nothing for any other scheme. */
export const SafeLink = ({ app, breadcrumbs, children, className, title, url }: SafeLinkProperties): ReactNode => {
  if (!isWebUrl(url)) return null

  const handleClick = (event: MouseEvent<HTMLAnchorElement>): void => {
    // Modified primary clicks keep their native new-tab/window behaviour.
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    if (!app) {
      breadcrumbs.record('openLink:raw-fallback', url)
      // No origin is knowable from inside the sandboxed iframe. `ui/open-link`
      // is a request, so it carries an id: without one a host reads it as a
      // notification it has no handler for, and drops it.
      rawRequestId += 1
      globalThis.parent.postMessage({ id: `pinboard-card-open-link-${String(rawRequestId)}`, jsonrpc: '2.0', method: 'ui/open-link', params: { url } }, '*')
      return
    }
    breadcrumbs.record('openLink:requested', url)
    app.openLink({ url }).catch((error: unknown) => {
      breadcrumbs.record('openLink:error', error instanceof Error ? error.message : String(error))
    })
  }

  return (
    <a className={className} href={url} onClick={handleClick} rel="noreferrer noopener" target="_blank" title={title}>
      {children}
    </a>
  )
}
