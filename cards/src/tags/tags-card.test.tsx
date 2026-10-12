import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { fixtureData, fixtureError } from '../../fixtures/index.ts'
import { stubContext } from '../../test-support/context.ts'
import { filterTags, readTags } from './read-tags.ts'
import { INITIAL_TAGS, TagsCard } from './tags-card.tsx'

const renderScenario = (scenario: string, context = stubContext()): ReturnType<typeof render> =>
  render(<TagsCard context={context} data={fixtureData('tags', scenario)} />)

const tagRows = (): HTMLElement[] => screen.queryAllByRole('listitem')

describe('TagsCard', () => {
  it('lists the most-used tags first, with a count of all of them', () => {
    renderScenario('normal')

    expect(screen.getByText('42 tags')).toBeInTheDocument()
    expect(tagRows()).toHaveLength(INITIAL_TAGS)
    expect(tagRows()[0]).toHaveTextContent('python')
    expect(screen.getByText(`Top ${String(INITIAL_TAGS)} of 42 tags`)).toBeInTheDocument()
  })

  it('draws each row’s bar to its share of the most-used tag', () => {
    const { container } = renderScenario('few')

    const bars = [...container.querySelectorAll<HTMLElement>('.tag-row__bar')].map(bar => bar.style.inlineSize)
    expect(bars).toEqual(['100%', `${String((5 / 12) * 100)}%`, '25%'])
  })

  it('shows every tag on request, asking the shell to measure again', () => {
    const context = stubContext()
    renderScenario('normal', context)

    fireEvent.click(screen.getByRole('button', { name: 'Show all' }))

    expect(tagRows()).toHaveLength(42)
    expect(context.remeasure).toHaveBeenCalledOnce()
  })

  it('filters as the reader types, and says when nothing matches', () => {
    renderScenario('normal')
    const filter = screen.getByRole('searchbox', { name: 'Filter tags' })

    fireEvent.change(filter, { target: { value: 'TOREAD' } })

    expect(tagRows().map(row => row.textContent?.replace(/\d+$/, ''))).toEqual(['.toread', 'toread'])
    expect(screen.queryByRole('button')).toBeNull()

    fireEvent.change(filter, { target: { value: 'zzz' } })

    expect(screen.getByText('No tags contain “zzz”.')).toBeInTheDocument()
  })

  it('offers no filter or control for a handful of tags', () => {
    renderScenario('few')

    expect(screen.queryByRole('searchbox')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('says so when there are no tags', () => {
    renderScenario('empty')

    expect(screen.getByText('No tags yet.')).toBeInTheDocument()
  })

  it('keeps hostile markup inert and skips malformed entries', () => {
    const { container } = renderScenario('hostile')

    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    // The count given as a string drops to 0; the entry with no tag and the
    // bare string drop out.
    expect(tagRows()).toHaveLength(3)
    expect(screen.getByText('</ol><script>alert(1)</script>')).toBeInTheDocument()
  })

  it('keeps a very long tag whole', () => {
    renderScenario('long')

    expect(tagRows()[0]).toHaveTextContent('1,204')
    expect(tagRows()[0]?.textContent?.length).toBeGreaterThan(200)
  })

  it('draws a tool failure in place of the list', () => {
    render(<TagsCard context={stubContext({ error: fixtureError('tags', 'error') })} data={{}} />)

    expect(screen.getByRole('alert')).toHaveTextContent('429 Too Many Requests')
  })
})

describe('readTags', () => {
  it('reads a `tags` key as well as FastMCP’s `result` wrapper', () => {
    expect(readTags({ tags: [{ count: 2, tag: 'b' }, { count: 2, tag: 'a' }] }).map(({ tag }) => tag)).toEqual(['a', 'b'])
  })

  it('reads nothing from a payload with no list', () => {
    expect(readTags({ result: 'nope' })).toEqual([])
  })
})

describe('filterTags', () => {
  it('keeps everything for a blank filter', () => {
    const tags = [{ count: 1, tag: 'a' }]

    expect(filterTags(tags, '   ')).toBe(tags)
  })
})
