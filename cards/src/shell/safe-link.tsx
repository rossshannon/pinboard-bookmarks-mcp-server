/**
 * The one sanctioned place a card renders a real `href`. Ported from the
 * &Open card shell; the scheme rule is Pinboard's (see `safe-url.ts`).
 *
 * Renders an anchor only for an `http(s)://` URL, so a hostile URL renders no
 * anchor at all rather than a neutered one. A real anchor keeps "copy link
 * address" and the status-bar preview.
 *
 * Every click that would open it — plain, cmd/ctrl/shift-click, middle-click —
 * goes to the shell's link opener (`link-opener.ts`). The sandbox has no
 * `allow-popups`, so the browser's own new-tab handling would do nothing; the
 * host is the only thing that can open a link.
 */

import type { MouseEvent, ReactNode } from 'react'

import type { BreadcrumbLog } from './breadcrumbs.ts'
import type { LinkOpener } from './link-opener.ts'

import { isWebUrl } from './safe-url.ts'

export interface SafeLinkProperties {
  readonly links: LinkOpener
  readonly breadcrumbs: BreadcrumbLog
  readonly children: ReactNode
  readonly className?: string
  readonly title?: string
  readonly url: string
}

/** An `http(s)://`-only link that opens through the host; renders nothing for any other scheme. */
export const SafeLink = ({ breadcrumbs, children, className, links, title, url }: SafeLinkProperties): ReactNode => {
  if (!isWebUrl(url)) return null

  const open = (event: MouseEvent<HTMLAnchorElement>): void => {
    // Alt-click is "download" in most browsers; nothing for the host to do.
    if (event.altKey) return
    event.preventDefault()
    breadcrumbs.record('openLink:requested', url)
    links.openLink({ url }).catch((error: unknown) => {
      breadcrumbs.record('openLink:error', error instanceof Error ? error.message : String(error))
    })
  }

  return (
    <a
      className={className}
      href={url}
      onAuxClick={(event) => {
        if (event.button === 1) open(event)
      }}
      onClick={(event) => {
        if (event.button === 0) open(event)
      }}
      rel="noreferrer noopener"
      target="_blank"
      title={title}
    >
      {children}
    </a>
  )
}
