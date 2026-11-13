import { describe, expect, it } from 'vitest'

import { previewData } from '../../fixtures/index.ts'
import { readPreviews } from './read-previews.ts'

const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
const A = 'https://a.example/'
const B = 'https://b.example/'

const entry = (url: string, overrides: Record<string, unknown> = {}): Record<string, unknown> => ({
  description: 'About the page.',
  favicon: { data_uri: PNG, height: 32, width: 32 },
  image: { data_uri: PNG, height: 126, width: 240 },
  site_name: 'Example',
  status: 'ok',
  theme_color: '#123456',
  title: 'The page’s own title',
  url,
  ...overrides,
})

describe('readPreviews', () => {
  it('reads a preview into what a row draws', () => {
    const reply = readPreviews({ previews: [entry(A)] }, [A])

    expect(reply.settled.get(A)).toEqual({
      description: 'About the page.',
      favicon: { height: 32, src: PNG, width: 32 },
      image: { height: 126, src: PNG, width: 240 },
      siteName: 'Example',
    })
    expect(reply.rateLimited).toEqual([])
  })

  it('leaves the page’s title and theme colour unread', () => {
    const preview = readPreviews({ previews: [entry(A)] }, [A]).settled.get(A)

    expect(preview).not.toHaveProperty('title')
    expect(preview).not.toHaveProperty('themeColor')
  })

  it.each(['no_data', 'error', 'pwned', undefined, 42])('settles a %s entry as nothing, whatever else it carries', (status) => {
    const reply = readPreviews({ previews: [entry(A, { status })] }, [A])

    expect(reply.settled.has(A)).toBe(true)
    expect(reply.settled.get(A)).toBeUndefined()
  })

  it('settles an ok entry with nothing drawable as nothing', () => {
    const reply = readPreviews({ previews: [entry(A, { description: null, favicon: null, image: null, site_name: '  ' })] }, [A])

    expect(reply.settled.get(A)).toBeUndefined()
  })

  it('keeps the parts that are good when others are not', () => {
    const preview = readPreviews({
      previews: [entry(A, {
        description: 42,
        favicon: { data_uri: 'javascript:alert(1)', height: 32, width: 32 },
        image: { data_uri: 'https://evil.example/x.png', height: 126, width: 240 },
      })],
    }, [A]).settled.get(A)

    expect(preview).toEqual({ description: undefined, favicon: undefined, image: undefined, siteName: 'Example' })
  })

  it('trims and caps the strings', () => {
    const preview = readPreviews({ previews: [entry(A, { description: `  ${'d'.repeat(5000)}`, site_name: 's'.repeat(5000) })] }, [A]).settled.get(A)

    expect(preview?.description).toHaveLength(600)
    expect(preview?.description?.endsWith('…')).toBe(true)
    expect(preview?.siteName).toHaveLength(120)
  })

  it('reports rate-limited URLs apart, with the wait the tool named', () => {
    const reply = readPreviews({ previews: [entry(A), entry(B, { status: 'rate_limited' })], retry_after_seconds: 17 }, [A, B])

    expect([...reply.settled.keys()]).toEqual([A])
    expect(reply.rateLimited).toEqual([B])
    expect(reply.retryAfterMs).toBe(17_000)
  })

  it.each([
    [undefined, 30_000],
    ['soon', 30_000],
    [Number.NaN, 30_000],
    [0, 1000],
    [-5, 1000],
    [0.2, 1000],
    [9999, 120_000],
  ])('holds a retry_after_seconds of %j to something sensible', (value, expected) => {
    expect(readPreviews({ previews: [], retry_after_seconds: value }, []).retryAfterMs).toBe(expected)
  })

  it('settles an entry the bridge was too busy to finish, and marks it for another ask', () => {
    const reply = readPreviews({ previews: [entry(A, { image: null, retry: true }), entry(B)] }, [A, B])

    expect(reply.settled.get(A)?.siteName).toBe('Example')
    expect(reply.settled.get(A)?.image).toBeUndefined()
    expect(reply.busy).toEqual([A])
    expect(reply.rateLimited).toEqual([])
  })

  it('reports a lookup the server was still running apart: not settled, worth another ask', () => {
    const reply = readPreviews({ previews: [entry(A), { status: 'pending', url: B }] }, [A, B])

    expect([...reply.settled.keys()]).toEqual([A])
    expect(reply.pending).toEqual([B])
    expect(reply.busy).toEqual([])
    expect(reply.rateLimited).toEqual([])
  })

  it('draws nothing a pending entry carries', () => {
    const reply = readPreviews({ previews: [entry(A, { status: 'pending' })] }, [A])

    expect(reply.settled.has(A)).toBe(false)
    expect(reply.pending).toEqual([A])
  })

  it('marks a busy entry with no data too', () => {
    const reply = readPreviews({ previews: [{ retry: true, status: 'no_data', url: A }] }, [A])

    expect(reply.settled.get(A)).toBeUndefined()
    expect(reply.busy).toEqual([A])
  })

  it.each([
    ['a truthy value that is not true', { retry: 'true' }],
    ['retry: false', { retry: false }],
    ['an error', { retry: true, status: 'error' }],
    ['an unknown status', { retry: true, status: 'pwned' }],
  ])('does not mark %s for another ask', (_name, overrides) => {
    expect(readPreviews({ previews: [entry(A, overrides)] }, [A]).busy).toEqual([])
  })

  it('ignores entries for URLs it did not ask about', () => {
    const reply = readPreviews({ previews: [entry('https://unasked.example/'), entry(A)] }, [A])

    expect([...reply.settled.keys()]).toEqual([A])
  })

  it('takes the first entry for a URL and ignores repeats', () => {
    const reply = readPreviews({ previews: [entry(A, { site_name: 'First' }), entry(A, { site_name: 'Second' }), entry(A, { status: 'rate_limited' })] }, [A])

    expect(reply.settled.get(A)?.siteName).toBe('First')
    expect(reply.rateLimited).toEqual([])
  })

  it('settles as nothing every asked URL the tool left out', () => {
    const reply = readPreviews({ previews: [entry(A)] }, [A, B])

    expect(reply.settled.has(B)).toBe(true)
    expect(reply.settled.get(B)).toBeUndefined()
  })

  it.each([
    ['no previews key', {}],
    ['previews that are not a list', { previews: 'nope' }],
    ['entries that are not objects', { previews: [null, 'x', 42, [entry(A)]] }],
    ['entries without a URL', { previews: [{ status: 'ok' }, { status: 'ok', url: 42 }] }],
  ])('survives %s', (_name, data) => {
    const reply = readPreviews(data, [A])

    expect(reply.settled.get(A)).toBeUndefined()
    expect(reply.settled.has(A)).toBe(true)
  })

  describe('against the fixtures', () => {
    const urlsOf = (scenario: Parameters<typeof previewData>[0]): string[] =>
      (previewData(scenario)['previews'] as Array<{ url: string }>).map(preview => preview.url)

    it('reads every ok fixture entry as a preview', () => {
      const urls = urlsOf('ok')
      const reply = readPreviews(previewData('ok'), urls)

      expect(reply.settled.size).toBe(urls.length)
      expect([...reply.settled.values()].every(preview => preview !== undefined)).toBe(true)
      expect([...reply.settled.values()].some(preview => preview?.image !== undefined)).toBe(true)
    })

    it('lets nothing hostile through', () => {
      const urls = urlsOf('hostile')
      const reply = readPreviews(previewData('hostile'), urls)
      const previews = [...reply.settled.values()].filter(preview => preview !== undefined)

      // Exactly the honest entries keep their pictures: the last two.
      expect(previews.filter(preview => preview.image !== undefined)).toHaveLength(2)
      for (const preview of previews) {
        for (const image of [preview.image, preview.favicon]) {
          if (image !== undefined) expect(image.src).toMatch(/^data:image\/(webp|png|jpeg);base64,[A-Za-z0-9+/]+={0,2}$/)
        }
      }
      // Markup stays a string; the card renders it as text.
      expect(previews[0]?.siteName).toContain('<script>')
    })
  })
})
