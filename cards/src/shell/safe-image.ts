/**
 * The rule for the one sanctioned image sink, `<SafeImage>`: `safe-url.ts`'s
 * counterpart for pictures.
 *
 * A card draws an image only from an inline `data:` URI, base64-encoded, of
 * one of the two formats the preview bridge makes (WebP for a page's picture,
 * PNG for a favicon), whose first bytes are that format's, with dimensions it
 * was told up front. Nothing else:
 *
 * - no remote URL, because a card loads nothing from the network, and an
 *   image fetched from a bookmarked site would tell that site who is reading;
 * - no SVG, because an SVG is a document that can carry script and fetch
 *   things of its own;
 * - no `data:` URI in any other spelling (a charset parameter, percent
 *   encoding, stray whitespace), so there is one shape to reason about.
 *
 * The server applies the same rule before it sends an image (`previews.py`),
 * and checks the dimensions against the image's own header as well. The rule
 * is applied again here because this is where the bytes meet the document.
 */

import { isRecord } from '../guards.ts'

const IMAGE_DATA_URI = /^data:image\/(webp|png);base64,([A-Za-z0-9+/]+={0,2})$/

/**
 * How each format's first bytes read in base64. A PNG opens with its eight
 * signature bytes. A WebP opens `RIFF`, four bytes of length, then `WEBP`:
 * the length falls across the fifth to twelfth characters, so those are not
 * compared, and the last three letters of `WEBP` land on a boundary.
 */
const startsAs: Readonly<Record<string, (base64: string) => boolean>> = {
  png: base64 => base64.startsWith('iVBORw0KGgo'),
  webp: base64 => base64.startsWith('UklGR') && base64.slice(12, 16) === 'RUJQ',
}

/**
 * A ceiling on the whole URI, in characters. The server sends thumbnails well
 * under this; an image far over it is not a thumbnail.
 */
export const MAX_IMAGE_DATA_URI_LENGTH = 96 * 1024

/** The longest side an image may claim. */
export const MAX_IMAGE_DIMENSION = 1024

/** Whether a card will draw this as an image's `src`. */
export const isImageDataUri = (value: unknown): value is string => {
  if (typeof value !== 'string' || value.length > MAX_IMAGE_DATA_URI_LENGTH) return false
  const match = IMAGE_DATA_URI.exec(value)
  const [, format, base64] = match ?? []
  return format !== undefined && base64 !== undefined && startsAs[format]?.(base64) === true
}

export interface SafeImageSource {
  readonly src: string
  readonly width: number
  readonly height: number
}

const readDimension = (value: unknown): number | undefined =>
  typeof value === 'number' && Number.isInteger(value) && value > 0 && value <= MAX_IMAGE_DIMENSION ? value : undefined

/**
 * Narrow a tool's `{ data_uri, width, height }` to something drawable, or
 * `undefined`. All or nothing: an image without its dimensions would reflow
 * the row as it decoded, so it is not drawn at all.
 */
export const readImage = (value: unknown): SafeImageSource | undefined => {
  if (!isRecord(value)) return undefined
  const src = value['data_uri']
  const width = readDimension(value['width'])
  const height = readDimension(value['height'])
  if (!isImageDataUri(src) || width === undefined || height === undefined) return undefined
  return { height, src, width }
}
