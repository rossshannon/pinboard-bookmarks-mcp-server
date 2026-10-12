// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { createBreadcrumbLog } from './breadcrumbs.ts'
import { SafeLink } from './safe-link.tsx'

const HOSTILE_URLS = ['javascript:alert(1)', 'JavaScript:alert(1)', 'data:text/html,<script>alert(1)</script>', 'ftp://files.example.com', '/relative/path', 'https://', ' https://padded.example.com']

describe('SafeLink', () => {
  it.each(HOSTILE_URLS)('renders no anchor for a hostile scheme: %s', (url) => {
    const breadcrumbs = createBreadcrumbLog()

    const { container } = render(<SafeLink links={{ openLink: vi.fn() }} breadcrumbs={breadcrumbs} url={url}>Bookmark title</SafeLink>)

    expect(container.querySelector('a')).toBeNull()
  })

  it('renders a real anchor for an https:// URL', () => {
    const breadcrumbs = createBreadcrumbLog()

    render(<SafeLink links={{ openLink: vi.fn() }} breadcrumbs={breadcrumbs} url="https://pinboard.in/u:ross/t:mcp/">Bookmark title</SafeLink>)

    const anchor = screen.getByRole('link', { name: 'Bookmark title' })
    expect(anchor).toHaveAttribute('href', 'https://pinboard.in/u:ross/t:mcp/')
    // A real, unmodified anchor: keyboard-focusable and no outline override
    // suppressing the card.css focus-visible ring.
    expect(anchor).not.toHaveAttribute('tabindex')
    expect(anchor).not.toHaveAttribute('style')
  })

  // Pinboard holds years of the plain-HTTP web; refusing those would leave
  // half a collection unclickable.
  it('renders a real anchor for an http:// URL too', () => {
    const breadcrumbs = createBreadcrumbLog()

    render(<SafeLink links={{ openLink: vi.fn() }} breadcrumbs={breadcrumbs} url="http://www.paulgraham.com/hwh.html">Bookmark title</SafeLink>)

    expect(screen.getByRole('link', { name: 'Bookmark title' })).toHaveAttribute('href', 'http://www.paulgraham.com/hwh.html')
  })

  it('applies the given className to the rendered anchor', () => {
    const breadcrumbs = createBreadcrumbLog()

    render(<SafeLink links={{ openLink: vi.fn() }} breadcrumbs={breadcrumbs} className="btn" url="https://pinboard.in/u:ross/t:mcp/">Bookmark title</SafeLink>)

    expect(screen.getByRole('link', { name: 'Bookmark title' })).toHaveClass('btn')
  })

  it('hands a plain click to the link opener, recording a breadcrumb', () => {
    const breadcrumbs = createBreadcrumbLog()
    vi.spyOn(console, 'log').mockImplementation((): void => { /* no-op: forensics logging is not under test here */ })
    const openLink = vi.fn().mockResolvedValue({})

    render(<SafeLink links={{ openLink }} breadcrumbs={breadcrumbs} url="https://pinboard.in/u:ross/t:mcp/">Bookmark title</SafeLink>)
    fireEvent.click(screen.getByRole('link', { name: 'Bookmark title' }))

    expect(openLink).toHaveBeenCalledExactlyOnceWith({ url: 'https://pinboard.in/u:ross/t:mcp/' })
    expect(breadcrumbs.entries()).toEqual([{ detail: 'https://pinboard.in/u:ross/t:mcp/', label: 'openLink:requested' }])
  })

  it('lets modified clicks keep their native new-tab behaviour', () => {
    const breadcrumbs = createBreadcrumbLog()
    vi.spyOn(console, 'log').mockImplementation((): void => { /* no-op: forensics logging is not under test here */ })
    const openLink = vi.fn().mockResolvedValue({})

    render(<SafeLink links={{ openLink }} breadcrumbs={breadcrumbs} url="https://pinboard.in/u:ross/t:mcp/">Bookmark title</SafeLink>)
    const anchor = screen.getByRole('link', { name: 'Bookmark title' })

    // cmd-click, ctrl-click, shift-click, alt-click: default NOT prevented
    // (fireEvent.click returns true) and nothing routed through the host.
    for (const modifier of [{ metaKey: true }, { ctrlKey: true }, { shiftKey: true }, { altKey: true }]) {
      expect(fireEvent.click(anchor, modifier)).toBe(true)
    }
    expect(openLink).not.toHaveBeenCalled()
    expect(breadcrumbs.entries()).toEqual([])
  })

  it('records a breadcrumb if the host rejects the openLink request', async () => {
    const breadcrumbs = createBreadcrumbLog()
    vi.spyOn(console, 'log').mockImplementation((): void => { /* no-op: forensics logging is not under test here */ })
    const openLink = vi.fn().mockRejectedValue(new Error('host denied the request'))

    render(<SafeLink links={{ openLink }} breadcrumbs={breadcrumbs} url="https://pinboard.in/u:ross/t:mcp/">Bookmark title</SafeLink>)
    fireEvent.click(screen.getByRole('link', { name: 'Bookmark title' }))
    // Let the rejected promise's .catch() handler run.
    await vi.waitFor(() => {
      expect(breadcrumbs.entries()).toHaveLength(2)
    })

    expect(breadcrumbs.entries()).toEqual([
      { detail: 'https://pinboard.in/u:ross/t:mcp/', label: 'openLink:requested' },
      { detail: 'host denied the request', label: 'openLink:error' },
    ])
  })

  it('stringifies a non-Error rejection for the breadcrumb', async () => {
    const breadcrumbs = createBreadcrumbLog()
    vi.spyOn(console, 'log').mockImplementation((): void => { /* no-op: forensics logging is not under test here */ })
    const openLink = vi.fn().mockRejectedValue('denied')

    render(<SafeLink links={{ openLink }} breadcrumbs={breadcrumbs} url="https://pinboard.in/u:ross/t:mcp/">Bookmark title</SafeLink>)
    fireEvent.click(screen.getByRole('link', { name: 'Bookmark title' }))
    await vi.waitFor(() => {
      expect(breadcrumbs.entries()).toHaveLength(2)
    })

    expect(breadcrumbs.entries()[1]).toEqual({ detail: 'denied', label: 'openLink:error' })
  })
})
