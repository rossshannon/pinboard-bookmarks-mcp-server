/**
 * The one sanctioned place a card renders an `<img>`. Its `src` is only ever
 * an inline raster `data:` URI (see `safe-image.ts`), checked again here so
 * that no caller can hand it anything else; any other value renders nothing.
 *
 * Decorative by design: the pictures a card draws (a page's thumbnail, a
 * site's favicon) repeat what the text beside them already says, so `alt` is
 * empty and assistive technology passes over them.
 */

import type { ReactNode } from 'react'

import type { SafeImageSource } from './safe-image.ts'

import { isImageDataUri } from './safe-image.ts'

export interface SafeImageProperties {
  readonly className?: string
  readonly image: SafeImageSource
}

/** An inline raster image with its dimensions set, so the row never reflows as it decodes; nothing for any other source. */
export const SafeImage = ({ className, image }: SafeImageProperties): ReactNode => {
  if (!isImageDataUri(image.src)) return null
  return (
    <img
      alt=""
      className={className}
      decoding="async"
      draggable={false}
      height={image.height}
      src={image.src}
      width={image.width}
    />
  )
}
