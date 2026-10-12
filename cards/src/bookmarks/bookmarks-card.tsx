/**
 * The bookmarks card: one card for all four bookmark tools. The head says what
 * was asked (a search, a window of recent saves, a tag listing), and each row
 * is a bookmark as Pinboard draws one — the title as a link, then where it
 * lives and when it was saved, its notes, and its tags.
 *
 * Every value renders as a JSX child, so React's escaping is the whole XSS
 * story; the one real `href` goes through `<SafeLink>`.
 */

import type { ReactNode } from 'react'

import { useEffect, useRef, useState } from 'react'

import type { CardRenderContext } from '../shell/card-shell.tsx'
import type { BookmarkRow } from './read-bookmarks.ts'

import { countOf, instantTitle, shortDate } from '../format.ts'
import { CardHead } from '../shell/card-head.tsx'
import { ErrorBlock } from '../shell/error-block.tsx'
import { Card } from '../shell/primitives.tsx'
import { SafeLink } from '../shell/safe-link.tsx'
import { isWebUrl } from '../shell/safe-url.ts'
import { describeSubject } from './describe.ts'
import { matchedTags, readBookmarks } from './read-bookmarks.ts'

/**
 * Rows drawn before "Show all". A search returns up to a hundred and a tag
 * listing two hundred; a chat transcript is no place for a two-hundred-row
 * card, and the agent has every row anyway.
 */
export const INITIAL_ROWS = 8

interface RowProperties {
  readonly context: CardRenderContext
  readonly matched: ReadonlySet<string>
  readonly row: BookmarkRow
}

const Bookmark = ({ context, matched, row }: RowProperties): ReactNode => {
  const saved = shortDate(row.savedAt)
  return (
    <li className="bookmark">
      <p className="bookmark__title">
        {/* SafeLink draws nothing for a non-web URL; the title must still show. */}
        {row.url === undefined || !isWebUrl(row.url)
          ? row.title
          : <SafeLink breadcrumbs={context.breadcrumbs} links={context.links} title={row.url} url={row.url}>{row.title}</SafeLink>}
      </p>
      {(row.host !== undefined || saved !== undefined) && (
        <p className="bookmark__meta">
          {row.host !== undefined && <span className="bookmark__host">{row.host}</span>}
          {saved !== undefined && <time dateTime={row.savedAt} title={instantTitle(row.savedAt)}>{saved}</time>}
        </p>
      )}
      {row.notes !== undefined && <p className="bookmark__notes">{row.notes}</p>}
      {row.tags.length > 0 && (
        <ul aria-label="Tags" className="tags">
          {row.tags.map((tag, index) => {
            const classes = ['tag']
            if (matched.has(tag.toLowerCase())) classes.push('tag--matched')
            if (tag.startsWith('.')) classes.push('tag--private')
            return <li className={classes.join(' ')} key={`${tag}#${String(index)}`}>{tag}</li>
          })}
        </ul>
      )}
    </li>
  )
}

interface BookmarksCardProperties {
  readonly context: CardRenderContext
  readonly data: Record<string, unknown>
}

export const BookmarksCard = ({ context, data }: BookmarksCardProperties): ReactNode => {
  const [expanded, setExpanded] = useState(false)
  const { remeasure } = context

  // Growing or shrinking in place is a change of height the shell did not
  // cause, so it is asked to measure again once the rows are in the document.
  // Not on mount: the shell measures its own first render.
  const mounted = useRef(false)
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true
      return
    }
    remeasure()
  }, [expanded, remeasure])

  if (context.error !== undefined) {
    return (
      <Card label="Pinboard bookmarks">
        <CardHead eyebrow="Pinboard" title="Bookmarks" />
        <ErrorBlock error={context.error} />
      </Card>
    )
  }

  const view = readBookmarks(data)
  const words = describeSubject(view.subject)
  const matched = matchedTags(view.subject)
  const shown = expanded ? view.rows : view.rows.slice(0, INITIAL_ROWS)
  const hidden = view.rows.length - shown.length

  return (
    <Card label={`Pinboard: ${words.title}`}>
      <CardHead count={countOf(view.total, 'bookmark')} detail={words.detail} eyebrow={words.eyebrow} title={words.title} />
      {view.rows.length === 0
        ? <p className="empty">{words.empty}</p>
        : (
            <ol className="bookmarks">
              {shown.map(row => <Bookmark context={context} key={row.key} matched={matched} row={row} />)}
            </ol>
          )}
      {view.rows.length > INITIAL_ROWS && (
        <div className="foot">
          <span>{expanded ? `All ${countOf(view.rows.length, 'bookmark')}` : `${String(shown.length)} of ${String(view.rows.length)}, ${String(hidden)} more`}</span>
          <button
            aria-expanded={expanded}
            className="button"
            onClick={() => {
              setExpanded(current => !current)
            }}
            type="button"
          >
            {expanded ? 'Show fewer' : 'Show all'}
          </button>
        </div>
      )}
    </Card>
  )
}
