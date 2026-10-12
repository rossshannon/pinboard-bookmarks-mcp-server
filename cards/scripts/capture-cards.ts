/**
 * Screenshots of every card scenario, in both themes and at two widths, for
 * looking at a change and for attaching to its PR:
 *
 *   npm run capture                      # everything
 *   npm run capture -- bookmarks search  # one card, one scenario
 *
 * Writes `tmp/captures/<card>/<scenario>-<theme>-<width>.png` (gitignored).
 * Renders through the same host as the browser checks.
 */

import console from 'node:console'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

import type { CardName } from './build-cards.ts'
import type { Theme } from './card-host.ts'

import { CARD_FIXTURES } from '../fixtures/index.ts'
import { CARDS } from './build-cards.ts'
import { launchBrowser, renderCard } from './card-host.ts'

const outRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../tmp/captures')
const [onlyCard, onlyScenario] = process.argv.slice(2)
const THEMES: readonly Theme[] = ['light', 'dark']
const WIDTHS = [600, 375] as const

const browser = await launchBrowser()
try {
  for (const card of CARDS as readonly CardName[]) {
    if (onlyCard !== undefined && card !== onlyCard) continue
    for (const scenario of Object.keys(CARD_FIXTURES[card])) {
      if (onlyScenario !== undefined && scenario !== onlyScenario) continue
      for (const theme of THEMES) {
        for (const width of WIDTHS) {
          const { errors, page } = await renderCard(browser, { card, scenario, theme, width })
          const file = path.join(outRoot, card, `${scenario}-${theme}-${String(width)}.png`)
          mkdirSync(path.dirname(file), { recursive: true })
          await page.locator('#card').screenshot({ path: file })
          await page.close()
          console.log(`✓ ${path.relative(process.cwd(), file)}${errors.length > 0 ? `  (console errors: ${errors.join('; ')})` : ''}`)
        }
      }
    }
  }
}
finally {
  await browser.close()
}
