import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { isImageDataUri, MAX_IMAGE_DATA_URI_LENGTH, readImage } from './safe-image.ts'
import { SafeImage } from './safe-image.tsx'

const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='

describe('isImageDataUri', () => {
  it.each(['webp', 'png', 'jpeg'])('takes a base64 %s', (format) => {
    expect(isImageDataUri(`data:image/${format};base64,AAAA`)).toBe(true)
  })

  it('takes base64 padding', () => {
    expect(isImageDataUri('data:image/png;base64,AAA=')).toBe(true)
    expect(isImageDataUri('data:image/png;base64,AA==')).toBe(true)
  })

  it.each([
    ['a remote URL', 'https://evil.example/tracker.png'],
    ['a plain-HTTP URL', 'http://evil.example/tracker.png'],
    ['a protocol-relative URL', '//evil.example/tracker.png'],
    ['a relative path', '/tracker.png'],
    ['a javascript: URL', 'javascript:alert(1)'],
    ['a blob: URL', 'blob:https://evil.example/1234'],
    ['a base64 SVG', 'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4='],
    ['an inline SVG', 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'],
    ['a GIF', 'data:image/gif;base64,AAAA'],
    ['an HTML document', 'data:text/html;base64,AAAA'],
    ['a data URI that is not base64', 'data:image/png,AAAA'],
    ['a charset parameter', 'data:image/png;charset=utf-8;base64,AAAA'],
    ['an empty payload', 'data:image/png;base64,'],
    ['an upper-cased scheme', 'DATA:image/png;base64,AAAA'],
    ['leading whitespace', ' data:image/png;base64,AAAA'],
    ['a trailing newline', 'data:image/png;base64,AAAA\n'],
    ['whitespace in the payload', 'data:image/png;base64,AA AA'],
    ['an attribute break-out', 'data:image/png;base64,AAAA" onerror="alert(1)'],
    ['padding in the middle', 'data:image/png;base64,AA==AAAA'],
    ['percent-encoding', 'data:image/png;base64,AAAA%0A'],
  ])('refuses %s', (_name, value) => {
    expect(isImageDataUri(value)).toBe(false)
  })

  it.each([undefined, null, 42, {}, ['data:image/png;base64,AAAA']])('refuses a non-string (%j)', (value) => {
    expect(isImageDataUri(value)).toBe(false)
  })

  it('refuses a URI past the size ceiling', () => {
    const prefix = 'data:image/png;base64,'
    expect(isImageDataUri(prefix + 'A'.repeat(MAX_IMAGE_DATA_URI_LENGTH - prefix.length))).toBe(true)
    expect(isImageDataUri(prefix + 'A'.repeat(MAX_IMAGE_DATA_URI_LENGTH - prefix.length + 1))).toBe(false)
  })
})

describe('readImage', () => {
  it('narrows a tool’s image to a source with its dimensions', () => {
    expect(readImage({ data_uri: PNG, height: 126, width: 240 })).toEqual({ height: 126, src: PNG, width: 240 })
  })

  it.each([
    ['no width', { data_uri: PNG, height: 126 }],
    ['a zero height', { data_uri: PNG, height: 0, width: 240 }],
    ['a negative width', { data_uri: PNG, height: 126, width: -240 }],
    ['a fractional width', { data_uri: PNG, height: 126, width: 240.5 }],
    ['a string width', { data_uri: PNG, height: 126, width: '240' }],
    ['a width past the ceiling', { data_uri: PNG, height: 126, width: 1025 }],
    ['an infinite height', { data_uri: PNG, height: Infinity, width: 240 }],
    ['a remote source', { data_uri: 'https://evil.example/x.png', height: 126, width: 240 }],
    ['no source', { height: 126, width: 240 }],
  ])('drops an image with %s whole', (_name, value) => {
    expect(readImage(value)).toBeUndefined()
  })

  it.each([undefined, null, PNG, 42, [PNG]])('drops anything that is not an object (%j)', (value) => {
    expect(readImage(value)).toBeUndefined()
  })
})

describe('SafeImage', () => {
  it('renders a decorative image with its dimensions set', () => {
    const { container } = render(<SafeImage className="favicon" image={{ height: 32, src: PNG, width: 32 }} />)

    const image = container.querySelector('img')
    expect(image).toHaveAttribute('src', PNG)
    expect(image).toHaveAttribute('alt', '')
    expect(image).toHaveAttribute('width', '32')
    expect(image).toHaveAttribute('height', '32')
    expect(image).toHaveAttribute('decoding', 'async')
    expect(image).toHaveClass('favicon')
  })

  // The guard is in the sink itself, so a caller that skipped `readImage` gains nothing.
  it.each([
    'https://evil.example/tracker.png',
    'javascript:alert(1)',
    'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=',
  ])('renders nothing for %s', (source) => {
    const { container } = render(<SafeImage image={{ height: 32, src: source, width: 32 }} />)

    expect(container).toBeEmptyDOMElement()
  })
})
