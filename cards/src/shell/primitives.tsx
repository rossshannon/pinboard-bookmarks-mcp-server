/**
 * The card surface, and the placeholders a loading card is drawn from.
 * Styling lives in `card.css`; these only pick classes.
 */

import type { ReactNode } from 'react'

interface CardProperties {
  readonly children: ReactNode
  /**
   * As tall as the frame the host has already drawn (`min-height: 100vh`, the
   * iframe's viewport). Used by the loading skeleton, so the card arrives where
   * the host's own placeholder was instead of stepping down to the skeleton's
   * height and growing back.
   */
  readonly fill?: boolean
  readonly label?: string
}

/** Every card body and every shell state card renders into one of these. */
export const Card = ({ children, fill = false, label }: CardProperties): ReactNode => (
  <section aria-label={label} className={fill ? 'card card--fill' : 'card'}>
    {children}
  </section>
)

interface SkeletonProperties {
  /** Width as a CSS length or percentage; height comes from the shape. */
  readonly width?: string
}

/** One line of text's worth of placeholder, at the line height of the text it stands in for. */
export const SkeletonLine = ({ width = '100%' }: SkeletonProperties): ReactNode => (
  <div className="skeleton skeleton--line" style={{ width }} />
)

/** A chip's worth: a tag. */
export const SkeletonChip = ({ width = '4rem' }: SkeletonProperties): ReactNode => (
  <div className="skeleton skeleton--chip" style={{ width }} />
)
