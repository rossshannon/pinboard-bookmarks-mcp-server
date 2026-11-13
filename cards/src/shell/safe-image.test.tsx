import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { isImageDataUri, MAX_IMAGE_DATA_URI_LENGTH, readImage } from './safe-image.ts'
import { SafeImage } from './safe-image.tsx'

const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='

/** The header of a 240 by 126 WebP: all a guard that reads no further needs. */
const WEBP = 'data:image/webp;base64,UklGRhYAAABXRUJQVlA4WAoAAAAAAAAA7wAAfQAA'

/** A PNG's signature and nothing after it, with each amount of padding. */
const PNG_START = 'iVBORw0KGgo'

describe('isImageDataUri', () => {
  it('takes a base64 PNG or WebP that starts as one', () => {
    expect(isImageDataUri(PNG)).toBe(true)
    expect(isImageDataUri(WEBP)).toBe(true)
  })

  it('takes base64 padding', () => {
    expect(isImageDataUri(`data:image/png;base64,${PNG_START}A`)).toBe(true)
    expect(isImageDataUri(`data:image/png;base64,${PNG_START}AAA=`)).toBe(true)
    expect(isImageDataUri(`data:image/png;base64,${PNG_START}AA==`)).toBe(true)
  })

  it.each([
    ['a JPEG, which the bridge never sends', 'data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/'],
    ['a PNG labelled as a WebP', PNG.replace('image/png', 'image/webp')],
    ['a WebP labelled as a PNG', WEBP.replace('image/webp', 'image/png')],
    ['a JPEG labelled as a PNG', 'data:image/png;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/'],
    ['a RIFF file that is not a WebP', 'data:image/webp;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEA'],
    ['bytes that are no image at all', 'data:image/png;base64,AAAA'],
    ['too few bytes to tell', 'data:image/webp;base64,UklGR'],
  ])('refuses %s', (_name, value) => {
    expect(isImageDataUri(value)).toBe(false)
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
    const prefix = `data:image/png;base64,${PNG_START}`
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
