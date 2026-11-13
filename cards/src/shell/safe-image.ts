/**
 * The rule for the one sanctioned image sink, `<SafeImage>`: `safe-url.ts`'s
 * counterpart for pictures.
 *
 * A card draws an image only from an inline `data:` URI of a raster format,
 * base64-encoded, with dimensions it was told up front. Nothing else:
 *
 * - no remote URL, because a card loads nothing from the network, and an
 *   image fetched from a bookmarked site would tell that site who is reading;
 * - no SVG, because an SVG is a document that can carry script and fetch
 *   things of its own;
 * - no `data:` URI in any other spelling (a charset parameter, percent
 *   encoding, stray whitespace), so there is one shape to reason about.
 *
 * The server applies the same rule before it sends an image (`previews.py`).
 * It is applied again here because this is where the bytes meet the document.
 */

import { isRecord } from '../guards.ts'

const IMAGE_DATA_URI = /^data:image\/(?:webp|png|jpeg);base64,[A-Za-z0-9+/]+={0,2}$/

/**
 * A ceiling on the whole URI, in characters. The server sends thumbnails well
 * under this; an image far over it is not a thumbnail.
 */
export const MAX_IMAGE_DATA_URI_LENGTH = 96 * 1024

/** The longest side an image may claim. */
export const MAX_IMAGE_DIMENSION = 1024

/** Whether a card will draw this as an image's `src`. */
export const isImageDataUri = (value: unknown): value is string =>
  typeof value === 'string' && value.length <= MAX_IMAGE_DATA_URI_LENGTH && IMAGE_DATA_URI.test(value)

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
