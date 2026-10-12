/** The head every card draws: an eyebrow with the pin, a title, a detail line, and a count. */

import type { ReactNode } from 'react'

/** Pinboard's pin, drawn inline: a card loads nothing it does not carry. */
const PinIcon = (): ReactNode => (
  <svg aria-hidden="true" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24">
    <path d="M12 17v5" />
    <path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z" />
  </svg>
)

interface CardHeadProperties {
  readonly count?: string | undefined
  readonly detail?: string | undefined
  readonly eyebrow: string
  readonly title: string
}

export const CardHead = ({ count, detail, eyebrow, title }: CardHeadProperties): ReactNode => (
  <header className="head">
    <div className="head__text">
      <p className="eyebrow">
        <PinIcon />
        {eyebrow}
      </p>
      <h1 className="head__title">{title}</h1>
      {detail !== undefined && <p className="head__detail">{detail}</p>}
    </div>
    {count !== undefined && <p className="count">{count}</p>}
  </header>
)
