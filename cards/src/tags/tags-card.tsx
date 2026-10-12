/**
 * The tags card, for `list_tags`: every tag with its count, most-used first,
 * each row's background drawn as a bar to its share of the most-used tag — a
 * list that reads as a chart at a glance. A filter box narrows it, all in the
 * card; nothing here calls back to the server.
 */

import type { ReactNode } from 'react'

import { useEffect, useId, useRef, useState } from 'react'

import type { CardRenderContext } from '../shell/card-shell.tsx'

import { countOf, formatCount } from '../format.ts'
import { CardHead } from '../shell/card-head.tsx'
import { ErrorBlock } from '../shell/error-block.tsx'
import { Card } from '../shell/primitives.tsx'
import { filterTags, readTags } from './read-tags.ts'

/** Rows drawn before "Show all". */
export const INITIAL_TAGS = 20

/**
 * The most a filtered or expanded list draws. An account can carry thousands
 * of tags, and a card that lays out every one of them is a slow card; past
 * this the filter is the way in.
 */
export const MAX_TAGS = 300

interface TagsCardProperties {
  readonly context: CardRenderContext
  readonly data: Record<string, unknown>
}

export const TagsCard = ({ context, data }: TagsCardProperties): ReactNode => {
  const [expanded, setExpanded] = useState(false)
  const [filter, setFilter] = useState('')
  const filterId = useId()
  const { remeasure } = context

  const mounted = useRef(false)
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true
      return
    }
    remeasure()
  }, [expanded, filter, remeasure])

  if (context.error !== undefined) {
    return (
      <Card label="Pinboard tags">
        <CardHead eyebrow="Pinboard" title="Tags" />
        <ErrorBlock error={context.error} />
      </Card>
    )
  }

  const tags = readTags(data)
  const matching = filterTags(tags, filter)
  const filtering = filter.trim() !== ''
  const limit = filtering || expanded ? MAX_TAGS : INITIAL_TAGS
  const shown = matching.slice(0, limit)
  const most = tags[0]?.count ?? 0

  return (
    <Card label="Pinboard tags">
      <CardHead count={countOf(tags.length, 'tag')} eyebrow="Pinboard" title="Your tags" />
      {tags.length === 0
        ? <p className="empty">No tags yet.</p>
        : (
            <>
              {tags.length > INITIAL_TAGS && (
                <>
                  <label className="visually-hidden" htmlFor={filterId}>Filter tags</label>
                  <input
                    autoComplete="off"
                    className="filter"
                    id={filterId}
                    onChange={(event) => {
                      setFilter(event.target.value)
                    }}
                    placeholder="Filter tags"
                    spellCheck={false}
                    type="search"
                    value={filter}
                  />
                </>
              )}
              {shown.length === 0
                ? <p className="empty">{`No tags contain “${filter.trim()}”.`}</p>
                : (
                    <ol aria-label="Tags by use" className="tag-rows">
                      {shown.map(({ count, tag }) => (
                        <li className="tag-row" key={tag}>
                          <span aria-hidden="true" className="tag-row__bar" style={{ inlineSize: `${String(most > 0 ? Math.max(2, (count / most) * 100) : 0)}%` }} />
                          <span className={tag.startsWith('.') ? 'tag-row__name tag--private' : 'tag-row__name'}>{tag}</span>
                          <span className="tag-row__count">{formatCount(count)}</span>
                        </li>
                      ))}
                    </ol>
                  )}
              {(filtering ? matching.length > shown.length : tags.length > INITIAL_TAGS) && (
                <div className="foot">
                  <span>
                    {filtering
                      ? `First ${formatCount(shown.length)} of ${countOf(matching.length, 'match', 'matches')}`
                      : expanded
                        ? `${formatCount(shown.length)} of ${countOf(tags.length, 'tag')}`
                        : `Top ${formatCount(shown.length)} of ${countOf(tags.length, 'tag')}`}
                  </span>
                  {!filtering && (
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
                  )}
                </div>
              )}
            </>
          )}
    </Card>
  )
}
