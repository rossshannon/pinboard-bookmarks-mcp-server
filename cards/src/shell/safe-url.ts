/**
 * The scheme rule for the one sanctioned link sink, `<SafeLink>`.
 *
 * Absolute `http://` or `https://` only. &Open's cards take `https://` alone,
 * but a Pinboard account is years of the web as it was, and plenty of what it
 * holds was bookmarked over plain HTTP; refusing those would leave half a
 * collection unclickable. Escaping does not stop a `javascript:` or `data:`
 * scheme, and a relative path resolves nowhere inside the host sandbox, so
 * anything else renders no anchor at all.
 */

const WEB_URL = /^https?:\/\/[^\s/]/i

/** Whether a card will render this URL as a link. */
export const isWebUrl = (url: string): boolean => WEB_URL.test(url)

/**
 * The host part of a URL, for display, without a leading `www.`; `undefined`
 * for anything that is not an openable web URL.
 */
export const displayHost = (url: string): string | undefined => {
  if (!isWebUrl(url)) return undefined
  try {
    return new URL(url).hostname.replace(/^www\./i, '') || undefined
  }
  catch {
    return undefined
  }
}
