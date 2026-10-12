/**
 * The one sanctioned place a card renders a real `href`. Ported from the
 * &Open card shell; the scheme rule is Pinboard's (see `safe-url.ts`).
 *
 * Renders an anchor only for an `http(s)://` URL, so a hostile URL renders no
 * anchor at all rather than a neutered one. A real anchor keeps native
 * affordances — middle-click, cmd/ctrl-click, "copy link address" — because
 * only the plain primary click is intercepted.
 *
 * That click goes to the shell's link opener (`link-opener.ts`): the host
 * sandbox blocks top navigation, so the host has to open it.
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

  const handleClick = (event: MouseEvent<HTMLAnchorElement>): void => {
    // Modified primary clicks keep their native new-tab/window behaviour.
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    breadcrumbs.record('openLink:requested', url)
    links.openLink({ url }).catch((error: unknown) => {
      breadcrumbs.record('openLink:error', error instanceof Error ? error.message : String(error))
    })
  }

  return (
    <a className={className} href={url} onClick={handleClick} rel="noreferrer noopener" target="_blank" title={title}>
      {children}
    </a>
  )
}
