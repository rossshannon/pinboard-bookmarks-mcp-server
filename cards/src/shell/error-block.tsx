/**
 * What a card draws when its tool failed. A failed call carries its reason as
 * prose and no `structuredContent`; the shell hands that over on
 * `context.error`, and this draws it instead of the card sitting on its
 * waiting state until it escalated to the diagnostic box.
 */

import type { ReactNode } from 'react'

import type { CardResultError } from './use-card-result.ts'

/** FastMCP's own lead-in, which names the tool the reader just watched being called. */
const TOOL_ERROR_PREFIX = /^Error (?:calling|executing) tool '[^']*':\s*/

export type CardErrorKind = 'error' | 'unauthenticated'

/**
 * `unauthenticated` when Pinboard turned the token away — the one failure the
 * reader can fix themselves, so it gets its own words. The match is on what
 * Pinboard and the client say (`401`, `Unauthorized`, `PINBOARD_TOKEN`),
 * since an `isError` result carries no machine-readable code.
 */
export const classifyCardError = (text: string): CardErrorKind =>
  /\b401\b|unauthori[sz]ed|forbidden|PINBOARD_TOKEN|api token/i.test(text) ? 'unauthenticated' : 'error'

export const errorDetail = (text: string): string => text.replace(TOOL_ERROR_PREFIX, '').trim()

export const ErrorBlock = ({ error }: { readonly error: CardResultError }): ReactNode => {
  const kind = classifyCardError(error.text)
  const detail = errorDetail(error.text)
  return (
    <div className="error" role="alert">
      <p className="error__title">{kind === 'unauthenticated' ? 'Pinboard didn’t accept the API token' : 'Pinboard couldn’t answer that'}</p>
      {kind === 'unauthenticated'
        ? <p className="error__detail">Check PINBOARD_TOKEN in the server’s configuration; it takes the form username:TOKEN from pinboard.in/settings/password.</p>
        : detail !== '' && <p className="error__detail">{detail}</p>}
    </div>
  )
}
