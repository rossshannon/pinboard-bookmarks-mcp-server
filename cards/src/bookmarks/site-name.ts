/**
 * Whether a page's name for itself is fit to show on a bookmark's row.
 *
 * The row always shows the host its link goes to, taken from the bookmark's
 * URL. A page's `og:site_name` is shown before it, and a page can put
 * anything there. The one thing it must not be able to do is name another
 * site: "paypal.com – Secure login" beside a host nobody reads to the end of
 * is the whole of a phishing page's work done for it. So a site name that
 * contains anything shaped like a host or a URL is not shown at all, unless
 * what it names is this row's own host or a parent of it.
 *
 * This is the only place the rule lives. The server passes the site name on
 * as text and does not judge it.
 *
 * The cost is a few honest names ("Node.js", "Last.fm") that are shaped like
 * hosts and so are not shown on pages elsewhere. Those rows show the host
 * alone, which is what they showed before there were previews.
 */

/** One or more dot-separated labels and a final label of letters: `example.com`, `a.b.example`. */
const HOST_SHAPED = /(?:[\p{L}\p{N}](?:[\p{L}\p{N}-]*[\p{L}\p{N}])?\.)+\p{L}{2,}/gu

/** Four dotted numbers, as an address is written. */
const ADDRESS_SHAPED = /\d{1,3}(?:\.\d{1,3}){3}/u

/** A scheme and its slashes, with or without a host worth the name after them. */
const URL_SHAPED = /[a-z][a-z\d+.-]*:\/\//iu

const withoutWww = (host: string): string => host.toLowerCase().replace(/^www\./u, '')

/**
 * The name as it would be read, for looking through: compatibility forms
 * folded to the plain ones (a full-width `．` is a dot to the eye), invisible
 * formatting characters removed, and the ideographic full stops that sit
 * where a dot would taken as dots.
 */
const asRead = (name: string): string =>
  name.normalize('NFKC').replaceAll(/\p{Cf}/gu, '').replaceAll(/[。｡]/gu, '.')

/** Whether `named` is this row's own host or a parent domain of it. */
const isOwn = (named: string, host: string): boolean => named === host || host.endsWith(`.${named}`)

/**
 * A page's name for itself, when it is worth showing beside the host and
 * safe to: `undefined` when there is no host to show it beside, when it only
 * repeats the host, or when it names any other host, address or URL.
 */
export const siteNameBeside = (siteName: string | undefined, host: string | undefined): string | undefined => {
  if (siteName === undefined || host === undefined) return undefined
  const own = withoutWww(host)
  const read = asRead(siteName).trim()
  if (read === '' || withoutWww(read) === own) return undefined
  if (ADDRESS_SHAPED.test(read)) return undefined
  const named = [...read.matchAll(HOST_SHAPED)].map(match => withoutWww(match[0]))
  if (named.some(each => !isOwn(each, own))) return undefined
  // A URL with nothing host-shaped in it (`https://localhost/`) is still a URL.
  if (URL_SHAPED.test(read) && named.length === 0) return undefined
  return siteName
}
