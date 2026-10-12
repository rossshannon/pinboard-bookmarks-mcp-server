/**
 * The built cards in a real Chromium, through the SDK's own `AppBridge` (see
 * `scripts/card-host.ts`). jsdom has no layout, so these are the checks that
 * see what a reader would: every scenario renders a card rather than the
 * shell's waiting or diagnostic state, nothing overflows sideways at phone
 * width, the host's frame ends up the card's height, the host's theme lands,
 * and a link click goes to the host instead of navigating the sandbox.
 *
 * Run `npm run build` first: these load the committed documents.
 */

import type { Browser, Frame, Page } from 'playwright-core'

import { afterAll, beforeAll, describe, expect, it } from 'vitest'

import type { CardName } from '../scripts/build-cards.ts'
import type { RenderOptions } from '../scripts/card-host.ts'

import { CARD_FIXTURES } from '../fixtures/index.ts'
import { CARDS } from '../scripts/build-cards.ts'
import { launchBrowser, renderCard, waitForSettle } from '../scripts/card-host.ts'

let browser: Browser
const open: Page[] = []

beforeAll(async () => {
  browser = await launchBrowser()
})

afterAll(async () => {
  await browser.close()
})

const render = async (options: RenderOptions): Promise<{ errors: string[], frame: Frame, page: Page }> => {
  const { errors, page } = await renderCard(browser, options)
  open.push(page)
  const frame = page.frames().find(candidate => candidate !== page.mainFrame())
  if (frame === undefined) throw new Error('the card iframe never loaded')
  return { errors, frame, page }
}

/** The card's own document height against the iframe the host sized from its reports. */
const heights = async (page: Page, frame: Frame): Promise<{ document: number, iframe: number }> => ({
  document: await frame.evaluate(() => document.documentElement.scrollHeight),
  iframe: await page.locator('#card').evaluate(element => element.getBoundingClientRect().height),
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
      expect(Math.abs(iframe - documentHeight)).toBeLessThanOrEqual(2)
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

  it('falls back to the reader’s own preference when the host reports none', async () => {
    const { frame, page } = await render({ card: 'tags', hostTheme: false, scenario: 'normal', theme: 'dark' })
    const scheme = await frame.evaluate(() => matchMedia('(prefers-color-scheme: dark)').matches)
    expect(scheme).toBe(true)
    await page.close()
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
    expect(Math.abs(after.iframe - after.document)).toBeLessThanOrEqual(2)

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
