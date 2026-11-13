import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { PreviewScenario } from '../../fixtures/index.ts'
import type { CardRenderContext } from '../shell/card-shell.tsx'
import type { ToolCallOutcome } from '../shell/tool-caller.ts'

import { answerPreviews, fixtureData, fixtureError } from '../../fixtures/index.ts'
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

  it('heads each row with the site’s initial, its host and the date, above the title', () => {
    renderScenario('search')

    const first = rows()[0]
    if (!first) throw new Error('no rows')
    const source = first.querySelector('.bookmark__source')
    // The stylesheet capitalises the initial.
    expect(source).toHaveTextContent('rrobinsloan.com9 Oct 2026')
    const tile = first.querySelector('.favicon--letter')
    expect(tile).toHaveTextContent('r')
    expect(tile).toHaveAttribute('aria-hidden', 'true')
    // Above the title, as a link card has it.
    const title = first.querySelector('.bookmark__title')
    expect(source && title && (source.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING)).toBeTruthy()
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

/** Everything the card asked the tool about, call by call. */
const askedOf = (callTool: CardRenderContext['callTool']): string[][] =>
  vi.mocked(callTool).mock.calls.map(([, toolArguments]) => toolArguments['urls'] as string[])

/** A context whose host can call tools, answering `get_link_previews` from a preview fixture. */
const previewContext = (scenario: PreviewScenario, overrides: Partial<CardRenderContext> = {}): CardRenderContext => stubContext({
  callTool: vi.fn<CardRenderContext['callTool']>((_name, toolArguments) =>
    Promise.resolve({ data: answerPreviews(scenario, toolArguments['urls'] as string[]), status: 'ok' })),
  canCallTools: () => true,
  ...overrides,
})

/** Let the loader's coalescing microtask, the tool call and the re-render all happen. */
const settle = async (): Promise<void> => {
  await act(async () => {
    for (let turn = 0; turn < 20; turn += 1) await Promise.resolve()
  })
}

const row = (index: number): HTMLElement => {
  const found = rows()[index]
  if (!found) throw new Error(`no row ${String(index)}`)
  return found
}

const urlsOf = (scenario: string): string[] =>
  (fixtureData('bookmarks', scenario)['bookmarks'] as Array<{ url: string }>).map(bookmark => bookmark.url)

describe('BookmarksCard link previews', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  describe('when the server does not offer them', () => {
    it('asks for nothing and holds no place for a thumbnail', async () => {
      const context = previewContext('ok')
      const { container } = renderScenario('search', context)
      await settle()

      expect(context.callTool).not.toHaveBeenCalled()
      expect(container.querySelector('.bookmark__thumb')).toBeNull()
      expect(container.querySelector('img')).toBeNull()
    })

    it.each([false, 'true', 1, null])('takes previews_available: %j as no', async (flag) => {
      const context = previewContext('ok')
      render(<BookmarksCard context={context} data={{ ...fixtureData('bookmarks', 'rich'), previews_available: flag }} />)
      await settle()

      expect(context.callTool).not.toHaveBeenCalled()
    })
  })

  describe('when the host cannot call tools', () => {
    it('asks for nothing and draws the rows as they are', async () => {
      const context = previewContext('ok', { canCallTools: () => false })
      const { container } = renderScenario('rich', context)
      await settle()

      expect(context.callTool).not.toHaveBeenCalled()
      expect(container.querySelector('.bookmark__thumb')).toBeNull()
      expect(rows()).toHaveLength(6)
      expect(row(0).querySelector('.bookmark__host')).toHaveTextContent('robinsloan.com')
      expect(row(0).querySelector('.bookmark__site')).toBeNull()
    })
  })

  describe('when they are available', () => {
    it('asks about the rows it drew, once, in one call', async () => {
      const context = previewContext('ok')
      renderScenario('rich', context)
      await settle()

      expect(context.callTool).toHaveBeenCalledExactlyOnceWith('get_link_previews', { urls: urlsOf('rich') })
    })

    it('holds the thumbnail’s place on every row while the answer is on its way', () => {
      const context = previewContext('ok', { callTool: vi.fn(() => new Promise<ToolCallOutcome>(() => { /* never answers */ })) })
      const { container } = renderScenario('rich', context)

      expect(container.querySelectorAll('.bookmark__thumb--pending')).toHaveLength(6)
      for (const placeholder of container.querySelectorAll('.bookmark__thumb--pending')) {
        expect(placeholder).toHaveAttribute('aria-hidden', 'true')
      }
      // The rows themselves are already whole.
      expect(screen.getByRole('link', { name: 'An app can be a home-cooked meal' })).toBeInTheDocument()
    })

    it('draws a row as a link card: favicon, site name, thumbnail', async () => {
      const { container } = renderScenario('rich', previewContext('ok'))
      await settle()

      const first = row(0)
      expect(first.querySelector('.bookmark__site')).toHaveTextContent('Robin Sloan')
      // Where the link really goes is on the row itself, after the page's own name, not in a tooltip.
      expect(first.querySelector('.bookmark__host')).toHaveTextContent('robinsloan.com')
      expect(first.querySelector('.bookmark__source')).toHaveTextContent(/^Robin Sloanrobinsloan\.com/)
      expect(first.querySelector('.bookmark__source [title="robinsloan.com"]')).toBeNull()
      const favicon = first.querySelector('img.favicon')
      expect(favicon).toHaveAttribute('src', expect.stringMatching(/^data:image\/png;base64,/))
      expect(favicon).toHaveAttribute('alt', '')
      expect(favicon).toHaveAttribute('width', '32')
      expect(first.querySelector('.favicon--letter')).toBeNull()
      const thumbnail = first.querySelector('.bookmark__thumb img')
      expect(thumbnail).toHaveAttribute('src', expect.stringMatching(/^data:image\/webp;base64,/))
      expect(thumbnail).toHaveAttribute('alt', '')
      expect(thumbnail).toHaveAttribute('width', '240')
      expect(thumbnail).toHaveAttribute('height', '240')
      expect(thumbnail).toHaveAttribute('decoding', 'async')
      expect(first.querySelector('.bookmark__thumb')).toHaveAttribute('aria-hidden', 'true')
      expect(container.querySelector('.bookmark__thumb--pending')).toBeNull()
    })

    it('keeps the bookmark’s own title, not the page’s', async () => {
      renderScenario('rich', previewContext('ok'))
      await settle()

      expect(screen.getByRole('link', { name: 'MCP Apps: interactive UI for MCP tools' })).toBeInTheDocument()
      expect(screen.queryByText('MCP Apps - Model Context Protocol')).toBeNull()
    })

    it('keeps the owner’s notes, and fills a row without any with the page’s description', async () => {
      renderScenario('rich', previewContext('ok'))
      await settle()

      // Notes win over the description the preview also carries.
      expect(row(0).querySelector('.bookmark__notes')).toHaveTextContent(/audience of four/)
      expect(row(0)).not.toHaveTextContent(/home-cooked meal\.$/)
      // No notes: the description takes their place, in their style.
      expect(row(1).querySelector('.bookmark__notes')).toHaveTextContent(/^Build interactive UI applications/)
    })

    it('gives the place back on a row whose page has no picture', async () => {
      renderScenario('rich', previewContext('ok'))
      await settle()

      // paulgraham.com: a favicon and a description, no image.
      expect(row(2).querySelector('.bookmark__thumb')).toBeNull()
      expect(row(2).querySelector('img.favicon')).not.toBeNull()
    })

    it('asks the shell to measure again when the previews land', async () => {
      const context = previewContext('ok')
      renderScenario('rich', context)
      expect(context.remeasure).not.toHaveBeenCalled()

      await settle()

      expect(context.remeasure).toHaveBeenCalledOnce()
    })

    it('settles a row with no data or an error to the bookmark alone', async () => {
      const { container } = renderScenario('rich', previewContext('mixed'))
      await settle()

      for (const index of [1, 2]) {
        expect(row(index).querySelector('.bookmark__thumb')).toBeNull()
        expect(row(index).querySelector('img')).toBeNull()
        expect(row(index).querySelector('.favicon--letter')).not.toBeNull()
        expect(row(index).querySelector('.bookmark__notes')).toBeNull()
      }
      expect(row(1).querySelector('.bookmark__host')).toHaveTextContent('modelcontextprotocol.io')
      expect(row(1).querySelector('.bookmark__site')).toBeNull()
      // A preview with a picture and no favicon keeps the letter tile; one with no site name keeps the host.
      expect(row(4).querySelector('.bookmark__thumb img')).not.toBeNull()
      expect(row(4).querySelector('.favicon--letter')).toHaveTextContent('w')
      expect(row(4).querySelector('.bookmark__host')).toHaveTextContent('web.dev')
      expect(row(4).querySelector('.bookmark__site')).toBeNull()
      expect(container.querySelector('.bookmark__thumb--pending')).toBeNull()
    })

    it('never asks about a bookmark that is not a web URL', async () => {
      const context = previewContext('ok')
      const { container } = render(<BookmarksCard context={context} data={{ ...fixtureData('bookmarks', 'hostile'), previews_available: true }} />)
      await settle()

      expect(context.callTool).not.toHaveBeenCalled()
      expect(container.querySelector('.bookmark__thumb')).toBeNull()
    })

    it('does not ask again when the same result is delivered twice', async () => {
      const context = previewContext('ok')
      const { rerender } = renderScenario('rich', context)
      await settle()

      rerender(<BookmarksCard context={{ ...context }} data={{ ...fixtureData('bookmarks', 'rich') }} />)
      await settle()

      expect(context.callTool).toHaveBeenCalledOnce()
    })
  })

  describe('what a page calls itself', () => {
    const withSiteName = (siteName: unknown): CardRenderContext => previewContext('ok', {
      callTool: vi.fn<CardRenderContext['callTool']>((_name, toolArguments) => Promise.resolve({
        data: { previews: (toolArguments['urls'] as string[]).map(url => ({ site_name: siteName, status: 'ok', url })) },
        status: 'ok',
      })),
    })

    it.each([
      ['the host again', 'robinsloan.com'],
      ['the host in other letters', 'RobinSloan.com'],
      ['the host with www.', 'www.robinsloan.com'],
      ['some other host', 'paypal.com'],
      ['some other host, dressed up', 'accounts.google.com'],
    ])('is not shown when it is %s', async (_name, siteName) => {
      renderScenario('rich', withSiteName(siteName))
      await settle()

      expect(row(0).querySelector('.bookmark__site')).toBeNull()
      expect(row(0).querySelector('.bookmark__host')).toHaveTextContent('robinsloan.com')
      expect(row(0).querySelector('.bookmark__source')).not.toHaveTextContent('paypal')
    })

    it('is shown beside the host, never in place of it', async () => {
      renderScenario('rich', withSiteName('PayPal — Secure Login'))
      await settle()

      for (const each of rows()) {
        expect(each.querySelector('.bookmark__site')).toHaveTextContent('PayPal — Secure Login')
        expect(each.querySelector('.bookmark__host')?.textContent).toMatch(/^[a-z0-9.-]+$/)
        // The name first, then the host, then the date.
        const [site, host] = each.querySelectorAll('.bookmark__source > span:not(.favicon)')
        expect(site).toHaveClass('bookmark__site')
        expect(host).toHaveClass('bookmark__host')
      }
    })
  })

  describe('when the first call had no way through', () => {
    /** A context whose `onConnect` listeners the test fires, as the shell does at the handshake. */
    const connectable = (overrides: Partial<CardRenderContext>): { connect: () => void, context: CardRenderContext } => {
      const listeners = new Set<() => void>()
      const context = previewContext('ok', {
        onConnect: (listener) => {
          listeners.add(listener)
          return () => {
            listeners.delete(listener)
          }
        },
        ...overrides,
      })
      return {
        connect: () => {
          act(() => {
            for (const listener of listeners) listener()
          })
        },
        context,
      }
    }

    it('settles to the plain rows, then asks again when the bridge connects', async () => {
      let connected = false
      const { connect, context } = connectable({
        callTool: vi.fn<CardRenderContext['callTool']>((_name, toolArguments) => Promise.resolve(connected
          ? { data: answerPreviews('ok', toolArguments['urls'] as string[]), status: 'ok' }
          : { reason: 'rejected-before-connect', status: 'unavailable' })),
      })
      const { container } = renderScenario('rich', context)
      await settle()

      expect(context.callTool).toHaveBeenCalledOnce()
      expect(container.querySelector('.bookmark__thumb')).toBeNull()

      connected = true
      connect()
      await settle()

      expect(askedOf(context.callTool)).toEqual([urlsOf('rich'), urlsOf('rich')])
      expect(container.querySelectorAll('.bookmark__thumb img')).toHaveLength(5)
      expect(container.querySelector('.bookmark__thumb--pending')).toBeNull()
    })

    it('stays settled when the host that connects refuses too', async () => {
      const { connect, context } = connectable({
        callTool: vi.fn<CardRenderContext['callTool']>()
          .mockResolvedValueOnce({ reason: 'not-connected', status: 'unavailable' })
          .mockResolvedValue({ reason: 'rejected', status: 'unavailable' }),
      })
      const { container } = renderScenario('rich', context)
      await settle()
      connect()
      await settle()
      connect()
      await settle()

      expect(context.callTool).toHaveBeenCalledTimes(2)
      expect(container.querySelector('.bookmark__thumb')).toBeNull()
    })

    it('starts asking at the handshake on a host that had no channel before it', async () => {
      let canCall = false
      const { connect, context } = connectable({ canCallTools: () => canCall })
      const { container } = renderScenario('rich', context)
      await settle()
      expect(context.callTool).not.toHaveBeenCalled()

      canCall = true
      connect()
      await settle()

      expect(context.callTool).toHaveBeenCalledExactlyOnceWith('get_link_previews', { urls: urlsOf('rich') })
      expect(container.querySelectorAll('.bookmark__thumb img')).toHaveLength(5)
    })
  })

  describe('when the server was still looking some pages up', () => {
    it('keeps those rows’ places, asks once more, and draws what it finds', async () => {
      vi.useFakeTimers()
      try {
        let calls = 0
        const context = previewContext('ok', {
          callTool: vi.fn<CardRenderContext['callTool']>((_name, toolArguments) => {
            calls += 1
            return Promise.resolve({ data: answerPreviews(calls === 1 ? 'pending' : 'ok', toolArguments['urls'] as string[]), status: 'ok' })
          }),
        })
        const { container } = renderScenario('rich', context)
        await settle()

        expect(container.querySelectorAll('.bookmark__thumb--pending')).toHaveLength(2)
        expect(row(2).querySelector('.bookmark__thumb--pending')).not.toBeNull()
        expect(row(0).querySelector('.bookmark__thumb img')).not.toBeNull()

        await act(async () => {
          await vi.advanceTimersByTimeAsync(5000)
        })
        await settle()

        expect(askedOf(context.callTool)).toEqual([urlsOf('rich'), [urlsOf('rich')[2], urlsOf('rich')[4]]])
        expect(container.querySelector('.bookmark__thumb--pending')).toBeNull()
        expect(row(4).querySelector('.bookmark__thumb img')).not.toBeNull()
      }
      finally {
        vi.useRealTimers()
      }
    })

    it('gives the places back when the second answer is pending too', async () => {
      vi.useFakeTimers()
      try {
        const context = previewContext('pending')
        const { container } = renderScenario('rich', context)
        await settle()
        await act(async () => {
          await vi.advanceTimersByTimeAsync(5000)
        })
        await settle()

        expect(context.callTool).toHaveBeenCalledTimes(2)
        expect(container.querySelector('.bookmark__thumb--pending')).toBeNull()
        await act(async () => {
          await vi.advanceTimersByTimeAsync(600_000)
        })
        expect(context.callTool).toHaveBeenCalledTimes(2)
      }
      finally {
        vi.useRealTimers()
      }
    })
  })

  describe('against a server that sanitised nothing', () => {
    it('draws no image from anything but an inline raster, and no markup at all', async () => {
      const { container } = renderScenario('rich', previewContext('hostile'))
      await settle()

      for (const image of container.querySelectorAll('img')) {
        expect(image.getAttribute('src')).toMatch(/^data:image\/(webp|png|jpeg);base64,[A-Za-z0-9+/]+={0,2}$/)
        expect(image.getAttributeNames().toSorted()).toEqual(['alt', 'class', 'decoding', 'draggable', 'height', 'src', 'width'].filter(name => name !== 'class' || image.hasAttribute('class')))
      }
      // Only the one honest entry among the six rows has pictures.
      expect(container.querySelectorAll('.bookmark__thumb img')).toHaveLength(1)
      expect(container.querySelectorAll('img.favicon')).toHaveLength(1)
      expect(container.querySelector('script')).toBeNull()
      expect(container.querySelector('svg:not(.eyebrow svg)')).toBeNull()
      expect(container.querySelector('[onerror], [onload], [style*="url"]')).toBeNull()
      expect(container.innerHTML).not.toContain('evil.example')
      // The markup arrives as text.
      expect(row(0).querySelector('.bookmark__site')?.textContent).toBe('<img src=x onerror=alert(1)><script>alert(\'x\')</script>')
      // Whatever a page calls itself, every row still says where its link goes.
      for (const each of rows()) expect(each.querySelector('.bookmark__host')?.textContent).toMatch(/^[a-z0-9.-]+$/)
      // An unknown status is not a preview, whatever came with it.
      expect(row(4)).not.toHaveTextContent('A status the card has never heard of.')
      expect(container.querySelector('.bookmark__thumb--pending')).toBeNull()
    })
  })

  describe('when the tool call fails', () => {
    it.each(['rejected', 'timeout', 'no-capability', 'tool-error', 'unreadable', 'not-connected'] as const)('settles quietly to the plain rows (%s)', async (reason) => {
      const context = previewContext('ok', { callTool: vi.fn<CardRenderContext['callTool']>().mockResolvedValue({ reason, status: 'unavailable' }) })
      const { container } = renderScenario('rich', context)
      expect(container.querySelectorAll('.bookmark__thumb--pending')).toHaveLength(6)

      await settle()

      expect(container.querySelector('.bookmark__thumb')).toBeNull()
      expect(container.querySelector('img')).toBeNull()
      expect(screen.queryByRole('alert')).toBeNull()
      expect(rows()).toHaveLength(6)
      expect(context.callTool).toHaveBeenCalledOnce()
    })

    it('does not ask again after "Show all"', async () => {
      const context = previewContext('ok', { callTool: vi.fn<CardRenderContext['callTool']>().mockResolvedValue({ reason: 'rejected', status: 'unavailable' }) })
      renderScenario('rich-many', context)
      await settle()

      fireEvent.click(screen.getByRole('button', { name: 'Show all' }))
      await settle()

      expect(context.callTool).toHaveBeenCalledOnce()
    })
  })

  describe('when the bridge’s rate limit is reached', () => {
    it('keeps the limited rows’ places, retries them once after the wait, and fills them in', async () => {
      vi.useFakeTimers()
      try {
        let calls = 0
        const context = previewContext('ok', {
          callTool: vi.fn<CardRenderContext['callTool']>((_name, toolArguments) => {
            calls += 1
            return Promise.resolve({ data: answerPreviews(calls === 1 ? 'rate-limited' : 'ok', toolArguments['urls'] as string[]), status: 'ok' })
          }),
        })
        const { container } = renderScenario('rich', context)
        await settle()

        expect(container.querySelectorAll('.bookmark__thumb img')).toHaveLength(2)
        expect(container.querySelectorAll('.bookmark__thumb--pending')).toHaveLength(3)
        expect(context.callTool).toHaveBeenCalledOnce()

        // The fixture says to leave it two seconds.
        await act(async () => {
          await vi.advanceTimersByTimeAsync(1999)
        })
        expect(context.callTool).toHaveBeenCalledOnce()
        await act(async () => {
          await vi.advanceTimersByTimeAsync(1)
        })
        await settle()

        expect(askedOf(context.callTool)[1]).toEqual(urlsOf('rich').slice(3))
        expect(container.querySelector('.bookmark__thumb--pending')).toBeNull()
        expect(container.querySelectorAll('.bookmark__thumb img')).toHaveLength(5)
      }
      finally {
        vi.useRealTimers()
      }
    })

    it('gives the places back if the retry is limited too', async () => {
      vi.useFakeTimers()
      try {
        const context = previewContext('rate-limited')
        const { container } = renderScenario('rich', context)
        await settle()
        await act(async () => {
          await vi.advanceTimersByTimeAsync(2000)
        })
        await settle()

        expect(context.callTool).toHaveBeenCalledTimes(2)
        expect(container.querySelector('.bookmark__thumb--pending')).toBeNull()
        expect(container.querySelectorAll('.bookmark__thumb img')).toHaveLength(2)
        await act(async () => {
          await vi.advanceTimersByTimeAsync(600_000)
        })
        expect(context.callTool).toHaveBeenCalledTimes(2)
      }
      finally {
        vi.useRealTimers()
      }
    })
  })

  describe('when the bridge was too busy to make some thumbnails', () => {
    it('draws the text at once, then asks about those rows once more and adds the pictures', async () => {
      vi.useFakeTimers()
      try {
        let calls = 0
        const context = previewContext('ok', {
          callTool: vi.fn<CardRenderContext['callTool']>((_name, toolArguments) => {
            calls += 1
            return Promise.resolve({ data: answerPreviews(calls === 1 ? 'busy' : 'ok', toolArguments['urls'] as string[]), status: 'ok' })
          }),
        })
        const { container } = renderScenario('rich', context)
        await settle()

        // Nothing is left pending: the busy rows have their text and wait quietly.
        expect(container.querySelector('.bookmark__thumb--pending')).toBeNull()
        expect(row(1).querySelector('.bookmark__site')).toHaveTextContent('Model Context Protocol')
        expect(row(1).querySelector('.bookmark__thumb')).toBeNull()
        expect(row(3).querySelector('img')).toBeNull()

        await act(async () => {
          await vi.advanceTimersByTimeAsync(5000)
        })
        await settle()

        expect(askedOf(context.callTool)).toEqual([urlsOf('rich'), [urlsOf('rich')[1], urlsOf('rich')[3]]])
        expect(row(1).querySelector('.bookmark__thumb img')).not.toBeNull()
        expect(row(3).querySelector('img.favicon')).not.toBeNull()
        expect(context.remeasure).toHaveBeenCalledTimes(2)

        // Once per URL: no third call, however long the card stays up.
        await act(async () => {
          await vi.advanceTimersByTimeAsync(600_000)
        })
        expect(context.callTool).toHaveBeenCalledTimes(2)
      }
      finally {
        vi.useRealTimers()
      }
    })
  })

  describe('past the first rows', () => {
    /** An IntersectionObserver the test drives: nothing intersects until told to. */
    const observers: Array<{ callback: IntersectionObserverCallback, options: IntersectionObserverInit | undefined, targets: Element[] }> = []
    const stubIntersectionObserver = (): void => {
      observers.length = 0
      vi.stubGlobal('IntersectionObserver', class {
        private readonly record: (typeof observers)[number]
        constructor(callback: IntersectionObserverCallback, options?: IntersectionObserverInit) {
          this.record = { callback, options, targets: [] }
          observers.push(this.record)
        }

        disconnect(): void {
          this.record.targets.length = 0
        }

        observe(target: Element): void {
          this.record.targets.push(target)
        }

        unobserve(target: Element): void {
          const index = this.record.targets.indexOf(target)
          if (index !== -1) this.record.targets.splice(index, 1)
        }
      })
    }
    /** What a browser does: everything one observer saw change, in one callback. */
    const scrollAllIntoView = (targets: readonly Element[]): void => {
      const observer = observers.find(candidate => targets.every(target => candidate.targets.includes(target)))
      if (!observer) throw new Error('no one observer is watching all of those rows')
      act(() => {
        observer.callback(targets.map(target => ({ isIntersecting: true, target }) as IntersectionObserverEntry), observer as unknown as IntersectionObserver)
      })
    }
    const scrollIntoView = (target: Element, isIntersecting = true): void => {
      const observer = observers.find(candidate => candidate.targets.includes(target))
      if (!observer) throw new Error('nothing is observing that row')
      act(() => {
        observer.callback([{ isIntersecting, target } as IntersectionObserverEntry], observer as unknown as IntersectionObserver)
      })
    }

    it('asks only about the first eight until "Show all"', async () => {
      stubIntersectionObserver()
      const context = previewContext('ok')
      renderScenario('rich-many', context)
      await settle()

      expect(askedOf(context.callTool)).toEqual([urlsOf('rich-many').slice(0, INITIAL_ROWS)])
      expect(observers).toHaveLength(0)
    })

    it('then asks about each further row only as it nears the viewport', async () => {
      stubIntersectionObserver()
      const context = previewContext('ok')
      const { container } = renderScenario('rich-many', context)
      await settle()
      fireEvent.click(screen.getByRole('button', { name: 'Show all' }))
      await settle()

      // Twelve more rows drawn, each watched by the card's one observer, with its place held, and nothing asked yet.
      expect(rows()).toHaveLength(20)
      expect(observers).toHaveLength(1)
      expect(observers[0]?.targets).toEqual(rows().slice(8))
      expect(observers.every(observer => observer.options?.root === undefined)).toBe(true)
      expect(container.querySelectorAll('.bookmark__thumb--pending')).toHaveLength(12)
      expect(context.callTool).toHaveBeenCalledOnce()

      // Seen but not intersecting: still nothing.
      scrollIntoView(row(8), false)
      await settle()
      expect(context.callTool).toHaveBeenCalledOnce()

      scrollIntoView(row(8))
      scrollIntoView(row(9))
      await settle()

      expect(askedOf(context.callTool)[1]).toEqual(urlsOf('rich-many').slice(8, 10))
      expect(row(8).querySelector('.bookmark__thumb--pending')).toBeNull()
      expect(row(10).querySelector('.bookmark__thumb--pending')).not.toBeNull()
    })

    it('batches a screenful of rows into calls of eight, one after another', async () => {
      stubIntersectionObserver()
      const context = previewContext('ok')
      renderScenario('rich-many', context)
      await settle()
      fireEvent.click(screen.getByRole('button', { name: 'Show all' }))
      await settle()

      for (const each of rows().slice(8)) scrollIntoView(each)
      await settle()

      const asked = askedOf(context.callTool)
      expect(asked.map(batch => batch.length)).toEqual([8, 8, 4])
      expect(asked.flat()).toEqual(urlsOf('rich-many'))
      expect(new Set(asked.flat()).size).toBe(20)
    })

    it('asks about rows that come into view together in one call, not the first alone', async () => {
      stubIntersectionObserver()
      const context = previewContext('ok')
      renderScenario('rich-many', context)
      await settle()
      fireEvent.click(screen.getByRole('button', { name: 'Show all' }))
      await settle()

      // Six rows scroll in at once, reported out of order as a browser may.
      scrollAllIntoView([row(11), row(8), row(13), row(9), row(12), row(10)])
      await settle()

      const asked = askedOf(context.callTool)
      expect(asked).toHaveLength(2)
      expect(asked[1]).toEqual(urlsOf('rich-many').slice(8, 14))
      // Reported rows are no longer watched; the rest still are.
      expect(observers[0]?.targets).toEqual(rows().slice(14))
    })

    it('does not ask twice for rows hidden and shown again', async () => {
      stubIntersectionObserver()
      const context = previewContext('ok')
      renderScenario('rich-many', context)
      await settle()
      fireEvent.click(screen.getByRole('button', { name: 'Show all' }))
      await settle()
      for (const each of rows().slice(8)) scrollIntoView(each)
      await settle()
      const before = vi.mocked(context.callTool).mock.calls.length

      fireEvent.click(screen.getByRole('button', { name: 'Show fewer' }))
      fireEvent.click(screen.getByRole('button', { name: 'Show all' }))
      await settle()

      expect(context.callTool).toHaveBeenCalledTimes(before)
      expect(observers).toHaveLength(1)
      expect(observers[0]?.targets).toHaveLength(0)
      // Straight from the card's cache.
      expect(row(19).querySelector('.bookmark__thumb--pending')).toBeNull()
    })

    it('loads the rest in order where there is no IntersectionObserver', async () => {
      vi.stubGlobal('IntersectionObserver', undefined)
      const context = previewContext('ok')
      renderScenario('rich-many', context)
      await settle()
      fireEvent.click(screen.getByRole('button', { name: 'Show all' }))
      await settle()

      const asked = askedOf(context.callTool)
      expect(asked.map(batch => batch.length)).toEqual([8, 8, 4])
      expect(asked.flat()).toEqual(urlsOf('rich-many'))
    })
  })
})
