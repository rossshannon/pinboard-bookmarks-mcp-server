import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { fixtureData, fixtureError } from '../../fixtures/index.ts'
import { stubContext } from '../../test-support/context.ts'
import { BookmarksCard, INITIAL_ROWS } from './bookmarks-card.tsx'

const renderScenario = (scenario: string, context = stubContext()): ReturnType<typeof render> =>
  render(<BookmarksCard context={context} data={fixtureData('bookmarks', scenario)} />)

const rows = (): HTMLElement[] => screen.queryAllByRole('listitem').filter(item => item.classList.contains('bookmark'))

describe('BookmarksCard', () => {
  // A failed layout test must not leave its mocks behind for the next.
  afterEach(() => {
    vi.restoreAllMocks()
    Reflect.deleteProperty(document, 'fonts')
  })

  it('heads a search with the query and the count', () => {
    renderScenario('search')

    expect(screen.getByText('Pinboard search')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('“essays”')
    expect(screen.getByText('4 bookmarks')).toBeInTheDocument()
    expect(rows()).toHaveLength(4)
  })

  it('draws each bookmark as a link, its host and date, notes and tags', () => {
    renderScenario('search')

    const first = rows()[0]
    if (!first) throw new Error('no rows')
    const link = within(first).getByRole('link', { name: 'An app can be a home-cooked meal' })
    expect(link).toHaveAttribute('href', 'https://www.robinsloan.com/notes/home-cooked-app/')
    expect(within(first).getByText('robinsloan.com')).toBeInTheDocument()
    const time = within(first).getByText('9 Oct 2026')
    expect(time).toHaveAttribute('datetime', '2026-10-09T21:14:03Z')
    expect(time).toHaveAttribute('title', '9 Oct 2026, 9:14:03pm UTC')
    expect(within(first).getByText(/audience of four/)).toBeInTheDocument()
    expect(within(first).getByRole('list', { name: 'Tags' })).toHaveTextContent('softwarecraftessays')
  })

  it('marks the tag a search was for, and Pinboard’s private tags', () => {
    renderScenario('search')

    expect(screen.getAllByText('essays')[0]).toHaveClass('tag--matched')
    expect(screen.getByText('software', { selector: '.tag' })).not.toHaveClass('tag--matched')
    expect(screen.getByText('.toread')).toHaveClass('tag--private')
  })

  it('opens a plain-HTTP bookmark too', () => {
    renderScenario('search')

    expect(screen.getByRole('link', { name: 'How to Work Hard' })).toHaveAttribute('href', 'http://www.paulgraham.com/hwh.html')
  })

  it('says how far back an extended search looked', () => {
    renderScenario('extended')

    expect(screen.getByText('Searched the past year')).toBeInTheDocument()
  })

  it('heads recent bookmarks with their window', () => {
    renderScenario('recent')

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Recent bookmarks')
    expect(screen.getByText('Saved in the past 7 days')).toBeInTheDocument()
  })

  it('heads a tag listing with its tags and date range, and marks those tags', () => {
    renderScenario('tagged')

    expect(screen.getByText('Pinboard tags')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('software + essays')
    expect(screen.getByText('Saved 1 Jan 2026 to 12 Oct 2026')).toBeInTheDocument()
    for (const tag of screen.getAllByText('software', { selector: '.tag' })) expect(tag).toHaveClass('tag--matched')
  })

  it(`draws ${String(INITIAL_ROWS)} rows of a long list, and the rest on request`, () => {
    const context = stubContext()
    renderScenario('many', context)

    expect(rows()).toHaveLength(INITIAL_ROWS)
    expect(screen.getByText(`${String(INITIAL_ROWS)} of 23, 15 more`)).toBeInTheDocument()
    expect(context.remeasure).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Show all' }))

    expect(rows()).toHaveLength(23)
    expect(screen.getByRole('button', { name: 'Show fewer' })).toHaveAttribute('aria-expanded', 'true')
    expect(context.remeasure).toHaveBeenCalledOnce()

    fireEvent.click(screen.getByRole('button', { name: 'Show fewer' }))

    expect(rows()).toHaveLength(INITIAL_ROWS)
    expect(context.remeasure).toHaveBeenCalledTimes(2)
  })

  it('offers no control when everything fits', () => {
    renderScenario('search')

    expect(screen.queryByRole('button')).toBeNull()
  })

  it('says so when nothing matched', () => {
    renderScenario('empty')

    expect(screen.getByText('0 bookmarks')).toBeInTheDocument()
    expect(screen.getByText('Nothing in Pinboard matches “quasar knitting”.')).toBeInTheDocument()
    expect(screen.queryByRole('list')).toBeNull()
  })

  it('keeps hostile markup inert and refuses unsafe schemes', () => {
    const { container } = renderScenario('hostile')

    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('a')).toBeNull()
    // A bookmark with an unsafe URL still shows its title, as plain text.
    expect(screen.getByText('Data URL bookmark')).toBeInTheDocument()
    expect(container.querySelectorAll('.bookmark__title')[0]?.textContent).toBe('<img src=x onerror=alert(1)><script>alert(\'x\')</script>')
    expect(screen.getAllByText('<img src=x onerror=alert(1)><script>alert(\'x\')</script>').length).toBeGreaterThan(0)
    // An unparseable date drops out rather than printing "Invalid Date".
    expect(container.textContent).not.toMatch(/Invalid/)
  })

  it('keeps every character of very long content rather than truncating it', () => {
    const data = fixtureData('bookmarks', 'long')
    renderScenario('long')

    const bookmark = (data['bookmarks'] as Array<{ title: string }>)[0]
    expect(screen.getByRole('link')).toHaveTextContent(bookmark?.title ?? '')
    expect(screen.getAllByRole('listitem').filter(item => item.classList.contains('tag'))).toHaveLength(27)
  })

  it('offers "Show more" on a note the clamp cuts short, and opens it', () => {
    // jsdom has no layout: say the clamped note is taller than its box.
    vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('bookmark__notes') ? 120 : 0
    })
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('bookmark__notes') ? 60 : 0
    })
    const context = stubContext()
    renderScenario('long', context)

    const more = screen.getByRole('button', { name: 'Show more' })
    expect(more).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(more)

    const less = screen.getByRole('button', { name: 'Show less' })
    expect(less).toHaveAttribute('aria-expanded', 'true')
    expect(document.getElementById(less.getAttribute('aria-controls') ?? '')).toHaveClass('bookmark__notes--open')
    expect(context.remeasure).toHaveBeenCalledOnce()
  })

  it('rechecks the clamp when a font loads late and reflows the note', () => {
    const fonts = new EventTarget()
    Object.defineProperty(document, 'fonts', { configurable: true, value: fonts })
    let overflowing = false
    vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('bookmark__notes') && overflowing ? 120 : 60
    })
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(() => 60)
    renderScenario('long')
    expect(screen.queryByRole('button', { name: 'Show more' })).toBeNull()

    overflowing = true
    act(() => {
      fonts.dispatchEvent(new Event('loadingdone'))
    })

    expect(screen.getByRole('button', { name: 'Show more' })).toBeInTheDocument()
  })

  it('offers no "Show more" on a note that fits', () => {
    renderScenario('search')

    expect(screen.queryByRole('button', { name: 'Show more' })).toBeNull()
  })

  it('draws a tool failure in place of the list', () => {
    render(<BookmarksCard context={stubContext({ error: fixtureError('bookmarks', 'error') })} data={{}} />)

    expect(screen.getByRole('alert')).toHaveTextContent('Pinboard couldn’t answer that')
    expect(screen.getByRole('alert')).toHaveTextContent('Limit must be between 1 and 100')
    expect(screen.getByRole('alert')).not.toHaveTextContent('Error calling tool')
  })

  it('tells the reader to check the token when Pinboard refused it', () => {
    render(<BookmarksCard context={stubContext({ error: fixtureError('bookmarks', 'unauthenticated') })} data={{}} />)

    expect(screen.getByRole('alert')).toHaveTextContent('Pinboard didn’t accept the API token')
    expect(screen.getByRole('alert')).toHaveTextContent('PINBOARD_TOKEN')
  })

  it('falls back to a plain heading for a payload it does not recognise', () => {
    render(<BookmarksCard context={stubContext()} data={{ unexpected: true }} />)

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Bookmarks')
    expect(screen.getByText('No bookmarks to show.')).toBeInTheDocument()
  })
})
