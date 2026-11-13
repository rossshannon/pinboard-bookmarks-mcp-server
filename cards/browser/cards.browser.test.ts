/**
 * The built cards in a real Chromium, through the SDK's own `AppBridge` (see
 * `scripts/card-host.ts`). jsdom has no layout, so these are the checks that
 * see what a reader would: every scenario renders a card rather than the
 * shell's waiting or diagnostic state, nothing overflows sideways at phone
 * width, the host's frame ends up the card's height, the host's theme lands,
 * and a link click goes to the host instead of navigating the sandbox. And
 * for the bookmarks card's link previews: the tool call goes through the host
 * and comes back as pictures, the card asks the network for nothing, the
 * frame follows the rows as they change height, and it all fits a phone.
 *
 * Run `npm run build` first: these load the committed documents.
 */

import type { Browser, Frame, Page } from 'playwright-core'

import { afterAll, beforeAll, describe, expect, it } from 'vitest'

import type { CardName } from '../scripts/build-cards.ts'
import type { RenderOptions } from '../scripts/card-host.ts'

import { CARD_FIXTURES, PREVIEW_RUNS } from '../fixtures/index.ts'
import { CARDS } from '../scripts/build-cards.ts'
import { launchBrowser, renderCard, toolCalls, waitForSettle } from '../scripts/card-host.ts'

let browser: Browser
const open: Page[] = []

beforeAll(async () => {
  browser = await launchBrowser()
})

afterAll(async () => {
  await browser.close()
})

const render = async (options: RenderOptions): Promise<{ errors: string[], frame: Frame, page: Page, requests: string[] }> => {
  const { errors, page, requests } = await renderCard(browser, options)
  open.push(page)
  const frame = page.frames().find(candidate => candidate !== page.mainFrame())
  if (frame === undefined) throw new Error('the card iframe never loaded')
  return { errors, frame, page, requests }
}

/**
 * The card's content height against the inside of the iframe the host sized
 * from its reports. Content is the body's own box, not
 * `documentElement.scrollHeight`, which never reads less than the viewport and
 * so would pass a frame left too tall.
 */
const heights = async (page: Page, frame: Frame): Promise<{ document: number, iframe: number }> => ({
  document: await frame.evaluate(() => Math.ceil(document.body.getBoundingClientRect().height)),
  iframe: await page.locator('#card').evaluate(element => element.clientHeight),
})

describe.each(CARDS as readonly CardName[])('the %s card', (card) => {
  describe.each(Object.keys(CARD_FIXTURES[card]))('%s', (scenario) => {
    it.each([600, 375])('renders cleanly at %ipx', async (width) => {
      const { errors, frame, page } = await render({ card, scenario, width })

      expect(errors).toEqual([])
      const text = await frame.evaluate(() => document.body.innerText)
      expect(text).not.toMatch(/Card diagnostic|Waiting for/i)
      expect(await frame.locator('.overlay').count()).toBe(0)
      // Nothing wider than the frame: a long word or URL must wrap, not scroll.
      const overflow = await frame.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
      expect(overflow).toBeLessThanOrEqual(0)
      const { document: documentHeight, iframe } = await heights(page, frame)
      expect(Math.abs(iframe - documentHeight)).toBeLessThanOrEqual(1)
      await page.close()
    })
  })
})

describe('theming', () => {
  const background = (frame: Frame): Promise<string> =>
    frame.evaluate(() => getComputedStyle(document.body).colorScheme)

  it('follows the theme the host reports, and a later change to it', async () => {
    const { frame, page } = await render({ card: 'bookmarks', scenario: 'search', theme: 'dark' })
    expect(await background(frame)).toBe('dark')

    await page.evaluate(() => {
      (window as unknown as { __setHostTheme__: (theme: string) => void }).__setHostTheme__('light')
    })
    await expect.poll(() => background(frame)).toBe('light')
    await page.close()
  })

  // The colour, not just `color-scheme`: a build that down-levels
  // `light-dark()` keeps the property right and the paint wrong.
  it('paints the host’s theme even when the reader’s OS prefers the other', async () => {
    const paint = async (host: 'dark' | 'light', os: 'dark' | 'light'): Promise<string> => {
      const { frame, page } = await render({ card: 'bookmarks', scenario: 'search', theme: host })
      await page.emulateMedia({ colorScheme: os })
      const colour = await frame.evaluate(() => getComputedStyle(document.querySelector('.card') ?? document.body).backgroundColor)
      await page.close()
      return colour
    }

    const dark = await paint('dark', 'dark')
    const light = await paint('light', 'light')
    expect(dark).not.toBe(light)
    expect(await paint('dark', 'light')).toBe(dark)
    expect(await paint('light', 'dark')).toBe(light)
  })

  // The paint again: `matchMedia` only echoes Playwright's own emulation.
  it('falls back to the reader’s own preference when the host reports none', async () => {
    const paint = async (os: 'dark' | 'light', hostTheme: boolean): Promise<string> => {
      const { frame, page } = await render({ card: 'bookmarks', hostTheme, scenario: 'search', theme: os })
      const colour = await frame.evaluate(() => getComputedStyle(document.querySelector('.card') ?? document.body).backgroundColor)
      await page.close()
      return colour
    }

    const dark = await paint('dark', true)
    const light = await paint('light', true)
    expect(dark).not.toBe(light)
    expect(await paint('dark', false)).toBe(dark)
    expect(await paint('light', false)).toBe(light)
  })
})

describe('interaction', () => {
  it('grows the frame when "Show all" opens the rest of the bookmarks, and shrinks it back', async () => {
    const { frame, page } = await render({ card: 'bookmarks', scenario: 'many' })
    const before = (await heights(page, frame)).iframe

    await frame.getByRole('button', { name: 'Show all' }).click()
    await waitForSettle(page)
    const after = await heights(page, frame)
    expect(after.iframe).toBeGreaterThan(before)
    expect(Math.abs(after.iframe - after.document)).toBeLessThanOrEqual(1)

    await frame.getByRole('button', { name: 'Show fewer' }).click()
    await waitForSettle(page)
    expect((await heights(page, frame)).iframe).toBeCloseTo(before, -1)
    await page.close()
  })

  it('grows the frame when "Show all" opens the rest of the tags', async () => {
    const { frame, page } = await render({ card: 'tags', scenario: 'normal' })
    const before = (await heights(page, frame)).iframe

    await frame.getByRole('button', { name: 'Show all' }).click()
    await waitForSettle(page)
    expect((await heights(page, frame)).iframe).toBeGreaterThan(before)
    await page.close()
  })

  it('opens a clamped note with "Show more" and grows the frame to fit it', async () => {
    const { frame, page } = await render({ card: 'bookmarks', scenario: 'long' })
    const notes = frame.locator('.bookmark__notes').first()
    const clipped = (): Promise<boolean> => notes.evaluate(element => element.scrollHeight > element.clientHeight + 1)
    expect(await clipped()).toBe(true)
    const before = (await heights(page, frame)).iframe

    await frame.getByRole('button', { name: 'Show more' }).click()
    await waitForSettle(page)
    expect(await clipped()).toBe(false)
    const after = await heights(page, frame)
    expect(after.iframe).toBeGreaterThan(before)
    expect(Math.abs(after.iframe - after.document)).toBeLessThanOrEqual(1)
    await page.close()
  })

  // Nothing in the card changed, so only the ResizeObserver can catch this.
  it('follows a relayout the card did not cause, when the host narrows the frame', async () => {
    const { frame, page } = await render({ card: 'bookmarks', scenario: 'search' })
    const before = (await heights(page, frame)).iframe

    await page.locator('#card').evaluate((element) => {
      (element as HTMLElement).style.width = '300px'
    })
    await expect.poll(async () => {
      const now = await heights(page, frame)
      return now.iframe > before && Math.abs(now.iframe - now.document) <= 1
    }, { timeout: 5000 }).toBe(true)
    await page.close()
  })

  it('opens a bookmark through the host rather than navigating the sandbox', async () => {
    const { frame, page } = await render({ card: 'bookmarks', scenario: 'search' })
    const link = frame.getByRole('link').first()
    const href = await link.getAttribute('href')

    await link.click()
    await expect.poll(() => page.evaluate(() => (window as unknown as { __openedLinks__: string[] }).__openedLinks__)).toEqual([href])
    // Still the card, and no tab or window opened behind the host's back.
    expect(await frame.getByRole('link').first().getAttribute('href')).toBe(href)
    expect(page.context().pages()).toHaveLength(1)
    await page.close()
  })
})

describe('link previews', () => {
  const richUrls = (CARD_FIXTURES.bookmarks['rich']?.structuredContent?.['bookmarks'] as Array<{ url: string }>).map(bookmark => bookmark.url)
  const manyUrls = (CARD_FIXTURES.bookmarks['rich-many']?.structuredContent?.['bookmarks'] as Array<{ url: string }>).map(bookmark => bookmark.url)

  /** How many of the card's images have decoded to real pixels. */
  const decodedImages = (frame: Frame, selector: string): Promise<number> =>
    frame.locator(selector).evaluateAll(images => images.filter(image => (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0).length)

  describe.each(Object.entries(PREVIEW_RUNS).flatMap(([scenario, runs]) => runs.map(previews => ({ previews, scenario }))))('$scenario with $previews previews', ({ previews, scenario }) => {
    it.each([600, 375])('renders cleanly at %ipx', async (width) => {
      const { errors, frame, page, requests } = await render({ card: 'bookmarks', previews, scenario, width })

      expect(errors).toEqual([])
      expect(requests).toEqual([])
      expect(await frame.evaluate(() => document.body.innerText)).not.toMatch(/Card diagnostic|Waiting for/i)
      const overflow = await frame.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
      expect(overflow).toBeLessThanOrEqual(0)
      const { document: documentHeight, iframe } = await heights(page, frame)
      expect(Math.abs(iframe - documentHeight)).toBeLessThanOrEqual(1)
      // Every image the card drew is an inline raster that really decoded.
      const sources = await frame.locator('img').evaluateAll(images => images.map(image => image.getAttribute('src') ?? ''))
      for (const source of sources) expect(source).toMatch(/^data:image\/(webp|png|jpeg);base64,/)
      expect(await decodedImages(frame, 'img')).toBe(sources.length)
      await page.close()
    })
  })

  it('asks the host for the previews of the rows it drew, and draws them', async () => {
    const { frame, page } = await render({ card: 'bookmarks', previews: 'ok', scenario: 'rich' })

    expect(await toolCalls(page)).toEqual([{ arguments: { urls: richUrls }, name: 'get_link_previews' }])
    // Five of the six pages have a picture; all six a favicon.
    expect(await decodedImages(frame, '.bookmark__thumb img')).toBe(5)
    expect(await decodedImages(frame, 'img.favicon')).toBe(6)
    expect(await frame.locator('.bookmark__thumb--pending').count()).toBe(0)
    expect(await frame.locator('.bookmark__site').first().innerText()).toBe('Robin Sloan')
    await page.close()
  })

  it('makes no tool call on a host that does not offer them, and holds no places', async () => {
    const { frame, page } = await render({ card: 'bookmarks', scenario: 'rich' })

    expect(await toolCalls(page)).toEqual([])
    expect(await frame.locator('.bookmark__thumb').count()).toBe(0)
    expect(await frame.locator('img').count()).toBe(0)
    await page.close()
  })

  it('settles to the plain rows when the host refuses the call', async () => {
    const { errors, frame, page } = await render({ card: 'bookmarks', previews: 'rejected', scenario: 'rich' })

    expect(errors).toEqual([])
    expect(await toolCalls(page)).toHaveLength(1)
    expect(await frame.locator('.bookmark__thumb').count()).toBe(0)
    expect(await frame.locator('.bookmark').count()).toBe(6)
    const { document: documentHeight, iframe } = await heights(page, frame)
    expect(Math.abs(iframe - documentHeight)).toBeLessThanOrEqual(1)
    await page.close()
  })

  it('loads nothing from the network, even when the server sends remote and script URLs', async () => {
    const { errors, frame, page, requests } = await render({ card: 'bookmarks', previews: 'hostile', scenario: 'rich' })

    expect(requests).toEqual([])
    expect(errors).toEqual([])
    // The one honest entry is all that drew.
    expect(await frame.locator('.bookmark__thumb img').count()).toBe(1)
    expect(await frame.locator('script:not(:first-of-type), [onerror], [onload]').count()).toBe(0)
    expect(await frame.evaluate(() => (globalThis as { __pwned__?: boolean }).__pwned__)).toBeUndefined()
    expect(page.context().pages()).toHaveLength(1)
    await page.close()
  })

  // The previews land well after the card has settled at its first height, so
  // only a fresh size report can bring the frame to the new one.
  it('reports its height again when previews arrive late and change the rows', async () => {
    const { frame, page } = await render({ card: 'bookmarks', previewDelayMs: 1500, previews: 'ok', scenario: 'rich', width: 375 })

    const after = await heights(page, frame)
    expect(await decodedImages(frame, '.bookmark__thumb img')).toBe(5)
    expect(Math.abs(after.iframe - after.document)).toBeLessThanOrEqual(1)
    // Descriptions arrived for the rows without notes, so the card grew past
    // what the same rows measure without previews.
    const plain = await render({ card: 'bookmarks', scenario: 'rich', width: 375 })
    expect(after.iframe).toBeGreaterThan((await heights(plain.page, plain.frame)).iframe)
    const reports = await page.evaluate(() => (globalThis as unknown as { __sizeReports__: Array<{ height?: number }> }).__sizeReports__)
    expect(reports.at(-1)?.height).toBe(after.iframe)
    await plain.page.close()
    await page.close()
  })

  it('holds each row’s thumbnail place while the answer is on its way, without animating under reduced motion', async () => {
    // The harness asks for reduced motion; a held call keeps the rows pending.
    const { frame, page } = await render({ card: 'bookmarks', previewDelayMs: 2500, previews: 'ok', scenario: 'rich', waitForReady: false })
    await frame.locator('.bookmark__thumb--pending').first().waitFor({ timeout: 10_000 })

    expect(await frame.locator('.bookmark__thumb--pending').count()).toBe(6)
    expect(await frame.locator('.bookmark__thumb--pending').first().evaluate(element => getComputedStyle(element).animationName)).toBe('none')
    const held = await frame.locator('.bookmark__thumb--pending').first().boundingBox()

    await frame.locator('.bookmark__thumb img').first().waitFor({ timeout: 10_000 })
    // The picture takes exactly the place that was held for it.
    expect(await frame.locator('.bookmark__thumb').first().boundingBox()).toEqual(held)
    expect(await frame.locator('.bookmark__thumb--pending').count()).toBe(0)
    await page.close()
  })

  it('lays a row out as a link card at phone width: thumbnail at the trailing edge, nothing clipped', async () => {
    const { frame, page } = await render({ card: 'bookmarks', previews: 'ok', scenario: 'rich', width: 375 })

    const geometry = await frame.locator('.bookmark').first().evaluate((row) => {
      const box = (selector: string): DOMRect => {
        const element = row.querySelector(selector)
        if (!element) throw new Error(`no ${selector}`)
        return element.getBoundingClientRect()
      }
      const image = row.querySelector('.bookmark__thumb img')
      return {
        body: box('.bookmark__body'),
        favicon: box('img.favicon'),
        fit: image ? getComputedStyle(image).objectFit : '',
        row: row.getBoundingClientRect(),
        thumb: box('.bookmark__thumb'),
        title: box('.bookmark__title'),
        viewport: document.documentElement.clientWidth,
      }
    })

    expect(geometry.viewport).toBe(375)
    // A fixed 4:3 box, smaller than on a wide frame but still a picture.
    expect(geometry.thumb.width).toBeGreaterThanOrEqual(72)
    expect(geometry.thumb.width).toBeLessThanOrEqual(96)
    expect(geometry.thumb.width / geometry.thumb.height).toBeCloseTo(4 / 3, 1)
    expect(geometry.fit).toBe('cover')
    // Beside the text, at the trailing edge, level with the top of the row's text.
    expect(geometry.thumb.left).toBeGreaterThanOrEqual(geometry.body.right)
    expect(geometry.thumb.right).toBeLessThanOrEqual(geometry.row.right + 0.5)
    expect(geometry.thumb.right).toBeLessThanOrEqual(geometry.viewport)
    expect(geometry.thumb.top).toBeLessThan(geometry.title.top)
    // The text keeps most of the row.
    expect(geometry.body.width).toBeGreaterThan(220)
    expect(Math.round(geometry.favicon.width)).toBe(16)
    expect(Math.round(geometry.favicon.height)).toBe(16)

    // And on a wide frame the same box is larger, by the same rule.
    const wide = await render({ card: 'bookmarks', previews: 'ok', scenario: 'rich', width: 600 })
    const wideThumb = await wide.frame.locator('.bookmark__thumb').first().boundingBox()
    expect(wideThumb?.width).toBeGreaterThanOrEqual(96)
    expect(wideThumb?.width).toBeLessThanOrEqual(120)
    await wide.page.close()
    await page.close()
  })

  it('retries the rows the bridge rate-limited, after the wait the tool names', async () => {
    const { frame, page } = await render({ card: 'bookmarks', previews: 'rate-limited', scenario: 'rich' })

    await expect.poll(async () => (await toolCalls(page)).length, { timeout: 10_000 }).toBe(2)
    const calls = await toolCalls(page)
    expect(calls[1]?.arguments?.urls).toEqual(richUrls.slice(3))
    await expect.poll(() => frame.locator('.bookmark__thumb--pending').count(), { timeout: 10_000 }).toBe(0)
    await expect.poll(() => decodedImages(frame, '.bookmark__thumb img')).toBe(5)
    await waitForSettle(page)
    const { document: documentHeight, iframe } = await heights(page, frame)
    expect(Math.abs(iframe - documentHeight)).toBeLessThanOrEqual(1)
    await page.close()
  })

  it('asks once more about the rows a busy bridge left without thumbnails, and grows to fit them', async () => {
    const { frame, page } = await render({ card: 'bookmarks', previews: 'busy', scenario: 'rich' })
    // Settled on the first answer: three pictures, and no places held for the rest.
    expect(await frame.locator('.bookmark__thumb--pending').count()).toBe(0)
    expect((await toolCalls(page))).toHaveLength(1)

    await expect.poll(async () => (await toolCalls(page)).length, { timeout: 15_000 }).toBe(2)
    expect((await toolCalls(page))[1]?.arguments?.urls).toEqual([richUrls[1], richUrls[3]])
    await expect.poll(() => decodedImages(frame, '.bookmark__thumb img'), { timeout: 10_000 }).toBe(5)
    await waitForSettle(page)
    const { document: documentHeight, iframe } = await heights(page, frame)
    expect(Math.abs(iframe - documentHeight)).toBeLessThanOrEqual(1)
    await page.close()
  })

  it('asks about rows past the first eight only after "Show all", and only as they come into view', async () => {
    const { frame, page } = await render({ card: 'bookmarks', previews: 'ok', scenario: 'rich-many' })
    expect((await toolCalls(page)).map(call => call.arguments?.urls)).toEqual([manyUrls.slice(0, 8)])

    await frame.getByRole('button', { name: 'Show all' }).click()
    await waitForSettle(page)

    // The frame is now far taller than the 900px window: the rows below the
    // fold have not been asked about, and still hold their places.
    const afterExpand = (await toolCalls(page)).flatMap(call => call.arguments?.urls ?? [])
    expect(afterExpand.length).toBeLessThan(manyUrls.length)
    expect(afterExpand).toEqual(manyUrls.slice(0, afterExpand.length))
    expect(await frame.locator('.bookmark__thumb--pending').count()).toBe(manyUrls.length - afterExpand.length)

    await page.evaluate(() => {
      globalThis.scrollTo(0, document.documentElement.scrollHeight)
    })
    await expect.poll(async () => (await toolCalls(page)).flatMap(call => call.arguments?.urls ?? []).length, { timeout: 10_000 }).toBeGreaterThan(afterExpand.length)
    await expect.poll(() => frame.locator('.bookmark').last().locator('.bookmark__thumb--pending').count(), { timeout: 10_000 }).toBe(0)

    // Each URL asked about once, never more than eight a call.
    const calls = await toolCalls(page)
    const asked = calls.flatMap(call => call.arguments?.urls ?? [])
    expect(new Set(asked).size).toBe(asked.length)
    for (const call of calls) expect(call.arguments?.urls?.length).toBeLessThanOrEqual(8)
    await waitForSettle(page)
    const { document: documentHeight, iframe } = await heights(page, frame)
    expect(Math.abs(iframe - documentHeight)).toBeLessThanOrEqual(1)
    await page.close()
  })
})
