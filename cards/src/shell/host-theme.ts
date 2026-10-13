/**
 * Adopting the host's look. Where &Open's cards carry a brand palette and
 * deliberately ignore the host's, a Pinboard card has no brand to protect, so
 * it takes whatever the host offers at the handshake and after:
 *
 * - `theme` becomes `data-theme` and `color-scheme` on the root, which is what
 *   every `light-dark()` in `card.css` follows. With no theme reported the
 *   root's own `color-scheme: light dark` falls back to the reader's OS.
 * - `styles.variables` become the spec's custom properties
 *   (`--color-background-primary`, `--font-sans`, …), which `card.css` reads
 *   with its own palette as the fallback for each, so a host that sends half
 *   of them gets a card that is half the host's and half its own, never blank.
 * - `styles.css.fonts` is injected once, for a host whose sans is self-hosted.
 *
 * Read off the SDK, which merges each `host-context-changed` into its cached
 * context before any listener runs, so re-reading the whole context is always
 * current.
 */

import type { App } from '@modelcontextprotocol/ext-apps'

import { applyDocumentTheme, applyHostFonts, applyHostStyleVariables } from '@modelcontextprotocol/ext-apps'

/** The one `App` capability this needs, narrowed for test doubles. */
interface HostContextSource {
  readonly getHostContext: App['getHostContext']
}

/** Apply whatever the host currently reports; returns the theme applied, if any, for the breadcrumb. */
export const applyHostLook = (app: HostContextSource | null): string | undefined => {
  const context = app?.getHostContext()
  if (!context) return undefined
  if (context.styles?.variables) applyHostStyleVariables(context.styles.variables)
  if (context.styles?.css?.fonts) applyHostFonts(context.styles.css.fonts)
  if (context.theme) {
    applyDocumentTheme(context.theme)
    return context.theme
  }
  return undefined
}
