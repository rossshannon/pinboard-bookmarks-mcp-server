/**
 * The bookmarks card: one card for all four bookmark tools. The head says what
 * was asked (a search, a window of recent saves, a tag listing), and each row
 * is a bookmark drawn as a link card — where it lives and when it was saved,
 * the title as a link, its notes, and its tags.
 *
 * When the server says link previews are available (`previews_available`),
 * the card asks for them itself once it has rendered, through the app-only
 * `get_link_previews` tool (`preview-loader.ts`), and a row gains what the
 * page says about itself: a favicon and site name, a description where the
 * bookmark has no notes, a thumbnail. Without them, or on a host that cannot
 * call tools for a card, the row is the bookmark alone and is complete as
 * that.
 *
 * What a page says about itself is not to be trusted with the row's identity.
 * The host the link really goes to is always shown, taken from the bookmark's
 * URL; a page's name for itself only ever stands beside it.
 *
 * Every value renders as a JSX child, so React's escaping is the whole XSS
 * story; the one real `href` goes through `<SafeLink>` and the one real `src`
 * through `<SafeImage>`.
 */

import type { ReactNode } from 'react'

import { useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'

import type { CardRenderContext } from '../shell/card-shell.tsx'
import type { PreviewItem, PreviewLoader, PreviewState } from './preview-loader.ts'
import type { BookmarkRow } from './read-bookmarks.ts'
import type { RowWatcher } from './row-watcher.ts'

import { countOf, instantTitle, shortDate } from '../format.ts'
import { CardHead } from '../shell/card-head.tsx'
import { ErrorBlock } from '../shell/error-block.tsx'
import { Card } from '../shell/primitives.tsx'
import { SafeImage } from '../shell/safe-image.tsx'
import { SafeLink } from '../shell/safe-link.tsx'
import { isWebUrl } from '../shell/safe-url.ts'
import { describeSubject } from './describe.ts'
import { createPreviewLoader } from './preview-loader.ts'
import { matchedTags, readBookmarks } from './read-bookmarks.ts'
import { createRowWatcher } from './row-watcher.ts'
import { siteNameBeside } from './site-name.ts'

/**
 * Rows drawn before "Show all". A search returns up to a hundred and a tag
 * listing two hundred; a chat transcript is no place for a two-hundred-row
 * card, and the agent has every row anyway.
 */
export const INITIAL_ROWS = 8

const NO_PREVIEW: PreviewState = { kind: 'none' }

interface NotesProperties {
  readonly notes: string
  readonly remeasure: () => void
}

/**
 * A bookmark's notes, clamped to three lines so one essay doesn't swallow the
 * card, with "Show more" whenever the clamp actually hides something. Whether
 * it does is measured, not guessed from length: it depends on the frame's width.
 */
const Notes = ({ notes, remeasure }: NotesProperties): ReactNode => {
  const reference = useRef<HTMLParagraphElement>(null)
  const [open, setOpen] = useState(false)
  const [clamped, setClamped] = useState(false)
  const id = useId()

  useLayoutEffect(() => {
    const element = reference.current
    if (!element || open) return undefined
    const check = (): void => {
      setClamped(element.scrollHeight > element.clientHeight + 1)
    }
    check()
    // Rechecked whenever the clamp might start or stop hiding text: the box
    // resizes (a narrower frame), or a font arrives late and reflows it
    // without changing the box's three-line height. Either may be missing
    // outside a real browser.
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(check) : undefined
    observer?.observe(element)
    const fonts = typeof document.fonts === 'object' ? document.fonts : undefined
    fonts?.addEventListener('loadingdone', check)
    return () => {
      observer?.disconnect()
      fonts?.removeEventListener('loadingdone', check)
    }
  }, [notes, open])

  const mounted = useRef(false)
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true
      return
    }
    remeasure()
  }, [open, remeasure])

  return (
    <>
      <p className={open ? 'bookmark__notes bookmark__notes--open' : 'bookmark__notes'} id={id} ref={reference}>{notes}</p>
      {(clamped || open) && (
        <button
          aria-controls={id}
          aria-expanded={open}
          className="text-button"
          onClick={() => {
            setOpen(current => !current)
          }}
          type="button"
        >
          {open ? 'Show less' : 'Show more'}
        </button>
      )}
    </>
  )
}

/** The first letter or digit of a site's name, for the tile that stands in for a favicon. */
const initialOf = (host: string): string => /[\p{L}\p{N}]/u.exec(host)?.[0] ?? '·'

interface RowProperties {
  readonly context: CardRenderContext
  /** Ask about this row at once, not when it nears the viewport: the rows drawn before "Show all". */
  readonly eager: boolean
  readonly matched: ReadonlySet<string>
  /** `undefined` when previews are off for this card. */
  readonly previews: PreviewLoader | undefined
  readonly row: BookmarkRow
  /** The card's one watcher for rows that are not `eager`. */
  readonly watcher: RowWatcher<PreviewItem>
}

const Bookmark = ({ context, eager, matched, previews, row, watcher }: RowProperties): ReactNode => {
  const element = useRef<HTMLLIElement>(null)
  const { previewToken: token, url } = row
  // A row the server sent no token for is not one it will look up.
  const state = previews === undefined || url === undefined || token === undefined ? NO_PREVIEW : previews.state(url)
  const pending = state.kind === 'pending'

  // Ask for this row's preview: at once for the rows a card opens with, and
  // for the rest as each nears the viewport, so "Show all" on two hundred
  // bookmarks does not mean two hundred lookups nobody scrolls to.
  useEffect(() => {
    const target = element.current
    if (previews === undefined || url === undefined || token === undefined || !pending || !target) return undefined
    if (eager) {
      previews.request([{ token, url }])
      return undefined
    }
    return watcher.watch(target, { token, url })
  }, [eager, pending, previews, token, url, watcher])

  const preview = state.kind === 'ready' ? state.preview : undefined
  const saved = shortDate(row.savedAt)
  const { host } = row
  const siteName = siteNameBeside(preview?.siteName, host)
  // The owner's own words first; the page's description only fills the gap.
  const text = row.notes ?? preview?.description

  return (
    <li className="bookmark" ref={element}>
      <div className="bookmark__body">
        {(host !== undefined || saved !== undefined) && (
          <p className="bookmark__source">
            {host !== undefined && (preview?.favicon === undefined
              ? <span aria-hidden="true" className="favicon favicon--letter">{initialOf(host)}</span>
              : <SafeImage className="favicon" image={preview.favicon} />)}
            <span className="bookmark__where">
              {/* The page's own name may say anything; the host beside it is where the link goes. */}
              {siteName !== undefined && <span className="bookmark__site">{siteName}</span>}
              {host !== undefined && <span className="bookmark__host">{host}</span>}
              {saved !== undefined && <time dateTime={row.savedAt} title={instantTitle(row.savedAt)}>{saved}</time>}
            </span>
          </p>
        )}
        <p className="bookmark__title">
          {/* SafeLink draws nothing for a non-web URL; the title must still show. */}
          {url === undefined || !isWebUrl(url)
            ? row.title
            : <SafeLink breadcrumbs={context.breadcrumbs} links={context.links} title={url} url={url}>{row.title}</SafeLink>}
        </p>
        {text !== undefined && <Notes notes={text} remeasure={context.remeasure} />}
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
      </div>
      {/* The thumbnail's place is held while the answer is on its way, so the
          text beside it wraps once; a row that turns out to have no picture
          gives the place back. */}
      {pending && <div aria-hidden="true" className="bookmark__thumb bookmark__thumb--pending" />}
      {preview?.image !== undefined && (
        <div aria-hidden="true" className="bookmark__thumb">
          <SafeImage image={preview.image} />
        </div>
      )}
    </li>
  )
}

const subscribeToNothing = (): (() => void) => () => undefined
const versionOfNothing = (): number => 0

/**
 * The card's preview loader, made once and kept for the card's life, or
 * `undefined` while previews are off: the server did not offer them, or the
 * host cannot call tools for a card. Re-renders the caller whenever a preview
 * lands, and asks the shell to measure again, since a description or a
 * thumbnail changes a row's height.
 */
const usePreviews = (context: CardRenderContext, enabled: boolean): PreviewLoader | undefined => {
  // Through a ref, so the loader made on the first render calls whatever the
  // shell's current `callTool` is.
  const latest = useRef(context)
  useEffect(() => {
    latest.current = context
  })
  const [loader] = useState(() => createPreviewLoader({
    callTool: (name, toolArguments) => latest.current.callTool(name, toolArguments),
    record: (label, detail) => {
      latest.current.breadcrumbs.record(label, detail)
    },
  }))
  useEffect(() => () => {
    loader.dispose()
  }, [loader])

  // A call made before the bridge connected may have had no way through. The
  // loader is told when it connects, whatever it is doing at the time, and
  // the card renders again: `canCallTools()` may have a different answer now.
  const [, setConnections] = useState(0)
  const { onConnect } = context
  useEffect(() => onConnect(() => {
    loader.connected()
    setConnections(count => count + 1)
  }), [loader, onConnect])

  const active = enabled ? loader : undefined
  const version = useSyncExternalStore(active?.subscribe ?? subscribeToNothing, active?.version ?? versionOfNothing)
  const { remeasure } = context
  useEffect(() => {
    if (version > 0) remeasure()
  }, [remeasure, version])
  return active
}

interface BookmarksCardProperties {
  readonly context: CardRenderContext
  readonly data: Record<string, unknown>
}

export const BookmarksCard = ({ context, data }: BookmarksCardProperties): ReactNode => {
  const [expanded, setExpanded] = useState(false)
  const { remeasure } = context
  // Read here, ahead of the error return, so the hook below always runs.
  const view = readBookmarks(data)
  const previews = usePreviews(context, context.error === undefined && view.previewsAvailable && context.canCallTools())

  // Rows past the first eight ask for their previews as they near the
  // viewport, through one watcher, so rows that arrive together are asked
  // about together.
  const latestPreviews = useRef(previews)
  useEffect(() => {
    latestPreviews.current = previews
  })
  const [watcher] = useState(() => createRowWatcher<PreviewItem>((items) => {
    latestPreviews.current?.request(items)
  }))
  useEffect(() => () => {
    watcher.dispose()
  }, [watcher])

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
              {shown.map((row, index) => (
                <Bookmark context={context} eager={index < INITIAL_ROWS} key={row.key} matched={matched} previews={previews} row={row} watcher={watcher} />
              ))}
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
