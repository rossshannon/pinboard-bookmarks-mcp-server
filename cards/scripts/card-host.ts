/**
 * A minimal MCP Apps host for the built cards, for the browser checks and the
 * capture command. Modelled on the &Open server's `card-preview.ts`.
 *
 * It renders the committed documents — the bytes the Python package serves —
 * through the SDK's own `AppBridge`, the class a production host uses, in a
 * `sandbox="allow-scripts"` iframe on an opaque origin, as a real host does.
 * There is no second, test-only path to a rendered card: a check that took a
 * shortcut around the bridge would keep passing while the real thing broke.
 *
 * The host behaves as Claude does where it matters: it answers the handshake
 * with a theme, sets the iframe's height from each size report, and delivers
 * the tool result once the card says it is initialised.
 */

import type { Browser, Page } from 'playwright-core'

import * as esbuild from 'esbuild'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'

import type { CardName } from './build-cards.ts'

import { CARD_FIXTURES } from '../fixtures/index.ts'
import { generatedPath } from './build-cards.ts'

const cardsRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

/** Set on `<body>` once the result is delivered and the card has gone this long without resizing. */
export const READY_ATTRIBUTE = 'data-card-ready'

/** Longer than the shell's 400ms settle window, so the shell going quiet ends it. */
const READY_QUIET_MS = 700

export type Theme = 'dark' | 'light'

export interface RenderOptions {
  readonly card: CardName
  readonly scenario: string
  readonly theme?: Theme
  /** The iframe's width: what the host gives the card. Claude's transcript column is ~600–700; a phone ~375. */
  readonly width?: number
  /** Hold the result back this long after the handshake, to see the skeleton. */
  readonly delayMs?: number
  /** Whether the host reports its theme at the handshake, or leaves the card to the OS preference. */
  readonly hostTheme?: boolean
}

const HOST_SCRIPT = `
import { AppBridge, PostMessageTransport } from '@modelcontextprotocol/ext-apps/app-bridge'

var config = window.__CARD__
var iframe = document.getElementById('card')
var target = iframe.contentWindow
var quietTimer = null
var delivered = false

function settleSoon() {
  if (!delivered) return
  if (quietTimer !== null) clearTimeout(quietTimer)
  quietTimer = setTimeout(function () { document.body.setAttribute('${READY_ATTRIBUTE}', 'true') }, ${String(READY_QUIET_MS)})
}

window.__sizeReports__ = []
var hostContext = { containerDimensions: { width: config.width }, platform: 'web' }
if (config.hostTheme) hostContext.theme = config.theme
var bridge = new AppBridge(null, { name: 'pinboard-card-harness', version: '0' }, { openLinks: {} }, { hostContext: hostContext })

bridge.addEventListener('sizechange', function (params) {
  window.__sizeReports__.push(params)
  if (typeof params.height === 'number') iframe.style.height = params.height + 'px'
  document.body.removeAttribute('${READY_ATTRIBUTE}')
  settleSoon()
})

window.__openedLinks__ = []
bridge.onopenlink = async function (params) {
  window.__openedLinks__.push(params.url)
  return {}
}

bridge.addEventListener('initialized', function () {
  setTimeout(function () {
    bridge.sendToolResult(config.result).then(function () {
      delivered = true
      settleSoon()
    })
  }, config.delayMs)
})

window.__setHostTheme__ = function (theme) { bridge.setHostContext({ theme: theme }) }

bridge.connect(new PostMessageTransport(target, target)).then(function () {
  iframe.srcdoc = config.html
})
`

let hostScript: Promise<string> | undefined

/** IIFE, so the bridge's dynamic imports stay in the one inline script. */
const bundleHostScript = async (): Promise<string> => {
  hostScript ??= esbuild.build({
    bundle: true,
    format: 'iife',
    logLevel: 'warning',
    platform: 'browser',
    stdin: { contents: HOST_SCRIPT, loader: 'js', resolveDir: cardsRoot, sourcefile: 'card-host.js' },
    write: false,
  }).then(result => result.outputFiles[0]?.text ?? '')
  return hostScript
}

/** The host page for one render: the iframe, its config, and the bundled bridge. */
export const hostPage = async (options: RenderOptions): Promise<string> => {
  const result = CARD_FIXTURES[options.card][options.scenario]
  if (result === undefined) throw new Error(`no ${options.card} fixture named ${options.scenario}`)
  const theme = options.theme ?? 'light'
  const config = {
    delayMs: options.delayMs ?? 0,
    hostTheme: options.hostTheme ?? true,
    html: readFileSync(generatedPath(options.card), 'utf8'),
    result: result.isError === true ? result : { content: [], ...result },
    theme,
    width: options.width ?? 600,
  }
  // `<` escaped so neither the fixture nor the document can close this script.
  const json = JSON.stringify(config).replaceAll('<', String.raw`\u003c`)
  const pageBackground = theme === 'dark' ? '#262624' : '#faf9f5'
  return `<!doctype html>
<html><head><meta charset="utf-8"><style>
  body { margin: 0; padding: 16px; background: ${pageBackground}; }
  iframe { display: block; border: 1px solid rgb(128 128 128 / 0.35); border-radius: 12px; width: ${String(config.width)}px; height: 0; }
</style></head>
<body><iframe id="card" sandbox="allow-scripts" title="${options.card} card"></iframe>
<script>window.__CARD__ = ${json}</script>
<script>${await bundleHostScript()}</script>
</body></html>`
}

/** Launch Chromium: `PLAYWRIGHT_CHROMIUM_PATH` when set (a pre-installed browser), else Playwright's own. */
export const launchBrowser = async (): Promise<Browser> => {
  const executablePath = process.env['PLAYWRIGHT_CHROMIUM_PATH']
  return chromium.launch(executablePath === undefined || executablePath === '' ? {} : { executablePath })
}

export interface RenderedCard {
  readonly page: Page
  /** Console errors from the page or the card, collected for the length of the render. */
  readonly errors: string[]
}

/** Render one card scenario and wait until it has settled. */
export const renderCard = async (browser: Browser, options: RenderOptions): Promise<RenderedCard> => {
  const page = await browser.newPage({
    colorScheme: options.theme ?? 'light',
    deviceScaleFactor: 2,
    reducedMotion: 'reduce',
    timezoneId: 'UTC',
    viewport: { height: 900, width: (options.width ?? 600) + 32 },
  })
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('pageerror', (error) => {
    errors.push(error.message)
  })
  await page.setContent(await hostPage(options))
  await page.waitForSelector(`body[${READY_ATTRIBUTE}]`, { timeout: 20_000 })
  return { errors, page }
}

/** Wait for the card to settle again after an interaction. */
export const waitForSettle = async (page: Page): Promise<void> => {
  await page.waitForFunction(attribute => !document.body.hasAttribute(attribute), READY_ATTRIBUTE, { timeout: 5000 }).catch(() => undefined)
  await page.waitForSelector(`body[${READY_ATTRIBUTE}]`, { timeout: 20_000 })
}
