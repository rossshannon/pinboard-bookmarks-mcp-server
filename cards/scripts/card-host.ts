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
 *
 * With `previews` set it also proxies tool calls, as a host that supports
 * app-called tools does: it advertises `serverTools` at the handshake and
 * answers the bookmarks card's `get_link_previews` calls from
 * `fixtures/previews.json`. Without it the host advertises no such thing,
 * which is the other case a card has to look right in.
 */

import type { Browser, Page } from 'playwright-core'

import * as esbuild from 'esbuild'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'

import type { CardName } from './build-cards.ts'

import type { PreviewRequestItem, PreviewScenario } from '../fixtures/index.ts'

import { answerPreviews, CARD_FIXTURES, previewText, verifiedUrls } from '../fixtures/index.ts'
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
  /**
   * Proxy the card's tool calls, answering `get_link_previews` from this
   * preview fixture; `'rejected'` proxies them and refuses every one. Left
   * out, the host does not offer tool calls at all. `rate-limited`, `busy`
   * and `pending` answer their first call from that fixture and later ones
   * from `ok`, as a bridge whose limit has lifted, whose queue has cleared,
   * or whose lookups have finished, would.
   */
  readonly previews?: 'rejected' | PreviewScenario
  /** Hold each tool call's answer back this long. */
  readonly previewDelayMs?: number
  /** `false` returns as soon as the host page is up, to look at the card on its way to settling. */
  readonly waitForReady?: boolean
}

/** One tool call the card made through the host, as the browser checks read it back. */
export interface RecordedToolCall {
  readonly arguments?: { readonly items?: ReadonlyArray<{ readonly token?: string, readonly url?: string }> }
  readonly name: string
}

/** The URLs one recorded call asked about, in order. */
export const askedUrls = (call: RecordedToolCall | undefined): string[] =>
  (call?.arguments?.items ?? []).map(item => item.url ?? '')

const HOST_SCRIPT = `
import { AppBridge, PostMessageTransport } from '@modelcontextprotocol/ext-apps/app-bridge'

var config = window.__CARD__
var iframe = document.getElementById('card')
var target = iframe.contentWindow
var quietTimer = null
var delivered = false
var callsInFlight = 0

function settleSoon() {
  if (!delivered || callsInFlight > 0) return
  if (quietTimer !== null) clearTimeout(quietTimer)
  quietTimer = setTimeout(function () { document.body.setAttribute('${READY_ATTRIBUTE}', 'true') }, ${String(READY_QUIET_MS)})
}

window.__sizeReports__ = []
var hostContext = { containerDimensions: { width: config.width }, platform: 'web' }
if (config.hostTheme) hostContext.theme = config.theme
var capabilities = config.toolCalls ? { openLinks: {}, serverTools: {} } : { openLinks: {} }
var bridge = new AppBridge(null, { name: 'pinboard-card-harness', version: '0' }, capabilities, { hostContext: hostContext })

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

// Answered in Node (\`__answerToolCall__\`, exposed by renderCard), where the
// fixtures are; a refusal comes back as \`{ error }\` and is thrown here, so
// the card sees the JSON-RPC error a real host would send.
window.__toolCalls__ = []
if (config.toolCalls) {
  bridge.oncalltool = async function (params) {
    window.__toolCalls__.push({ name: params.name, arguments: params.arguments })
    callsInFlight += 1
    if (quietTimer !== null) clearTimeout(quietTimer)
    document.body.removeAttribute('${READY_ATTRIBUTE}')
    try {
      var answer = await window.__answerToolCall__(params.name, params.arguments || {}, window.__toolCalls__.length)
      if (answer.error) throw new Error(answer.error)
      return answer.result
    }
    finally {
      callsInFlight -= 1
      settleSoon()
    }
  }
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
    toolCalls: options.previews !== undefined,
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
  /**
   * Every URL the page or the card asked the network for. The host page is set
   * as content and the card is a `srcdoc`, so a card that loads nothing leaves
   * this empty; inline `data:` images are not requests and are not listed.
   */
  readonly requests: string[]
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
  const requests: string[] = []
  page.on('request', (request) => {
    if (!/^(?:data|about|blob):/i.test(request.url())) requests.push(request.url())
  })
  const { previewDelayMs = 0, previews } = options
  if (previews !== undefined) {
    await page.exposeFunction('__answerToolCall__', async (name: string, toolArguments: { items?: readonly PreviewRequestItem[] }, callNumber: number) => {
      if (previewDelayMs > 0) await new Promise(resolve => setTimeout(resolve, previewDelayMs))
      if (previews === 'rejected') return { error: 'Tool calls are not permitted for this app' }
      if (name !== 'get_link_previews') return { error: `Unknown tool: ${name}` }
      const scenario = (previews === 'rate-limited' || previews === 'busy' || previews === 'pending') && callNumber > 1 ? 'ok' : previews
      // As the server answers: only an item carrying the token its URL was
      // sent with is looked up, and any other is that item's own error.
      const items = toolArguments.items ?? []
      const verified = new Set(verifiedUrls(items))
      const urls = items.map(item => typeof item.url === 'string' ? item.url : '')
      const looked = answerPreviews(scenario, urls.filter(url => verified.has(url)))
      const found = (looked['previews'] ?? []) as ReadonlyArray<{ readonly url?: unknown }>
      const refused = (url: string): Record<string, unknown> => ({ description: null, favicon: null, image: null, site_name: null, status: 'error', theme_color: null, title: null, url })
      const structuredContent = scenario === 'hostile'
        ? looked
        : { ...looked, previews: urls.map(url => found.find(entry => entry.url === url) ?? refused(url)) }
      // The data once, and the same result as text without its images.
      return { result: { content: [{ text: previewText(structuredContent), type: 'text' }], structuredContent } }
    })
  }
  await page.setContent(await hostPage(options))
  if (options.waitForReady !== false) await page.waitForSelector(`body[${READY_ATTRIBUTE}]`, { timeout: 20_000 })
  return { errors, page, requests }
}

/** Every tool call the card has made through the host so far, oldest first. */
export const toolCalls = (page: Page): Promise<RecordedToolCall[]> =>
  page.evaluate(() => (globalThis as unknown as { __toolCalls__: RecordedToolCall[] }).__toolCalls__)

/** Wait for the card to settle again after an interaction. */
export const waitForSettle = async (page: Page): Promise<void> => {
  await page.waitForFunction(attribute => !document.body.hasAttribute(attribute), READY_ATTRIBUTE, { timeout: 5000 }).catch(() => undefined)
  await page.waitForSelector(`body[${READY_ATTRIBUTE}]`, { timeout: 20_000 })
}
