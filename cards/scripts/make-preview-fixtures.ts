/**
 * Writes `fixtures/previews.json`: `get_link_previews` results for the
 * bookmarks in the `rich` and `rich-many` fixtures, with small real images
 * inlined as `data:` URIs, the way the server sends them.
 *
 *   npm run fixtures:previews
 *
 * The images are drawn on a canvas in Chromium and encoded there (WebP for
 * thumbnails, PNG for favicons), so they are real files a browser decodes,
 * not placeholders. Encoders differ between browser versions, so a rerun may
 * change the bytes; the file is committed, and this is only run again to
 * change what the fixtures say.
 */

import console from 'node:console'
import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { launchBrowser } from './card-host.ts'

const fixturesRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../fixtures')

interface Site {
  readonly description?: string
  /** No favicon when absent. */
  readonly favicon?: { readonly hue: number, readonly letter: string }
  /** No thumbnail when absent. `wide` is an Open Graph image's shape, `square` a Twitter summary's. */
  readonly image?: { readonly hue: number, readonly shape: 'square' | 'wide' }
  readonly siteName?: string
  readonly themeColor?: string
  readonly title?: string
}

const SITES: Readonly<Record<string, Site>> = {
  'http://www.paulgraham.com/hwh.html': {
    description: 'It might not seem there’s much to learn about how to work hard. Anyone who’s been to school knows what it entails, even if they chose not to do it.',
    favicon: { hue: 30, letter: 'P' },
    title: 'How to Work Hard',
  },
  'https://ciechanow.ski/mechanical-watch/': {
    description: 'An interactive article explaining the workings of a mechanical watch movement, from the mainspring to the escapement.',
    favicon: { hue: 210, letter: 'B' },
    image: { hue: 205, shape: 'wide' },
    siteName: 'Bartosz Ciechanowski',
    title: 'Mechanical Watch',
  },
  'https://craigmod.com/essays/fast_software/': {
    description: 'On the particular joy of software that responds before you have finished asking.',
    favicon: { hue: 350, letter: 'C' },
    image: { hue: 20, shape: 'wide' },
    siteName: 'Craig Mod',
    title: 'Fast Software, the Best Software',
  },
  'https://en.wikipedia.org/wiki/Dieter_Rams': {
    description: 'Dieter Rams is a German industrial designer most closely associated with the consumer products company Braun and the functionalist school of industrial design.',
    favicon: { hue: 0, letter: 'W' },
    image: { hue: 40, shape: 'square' },
    siteName: 'Wikipedia',
    title: 'Dieter Rams - Wikipedia',
  },
  'https://github.com/simonw/datasette': {
    description: 'An open source multi-tool for exploring and publishing data.',
    favicon: { hue: 250, letter: 'G' },
    image: { hue: 255, shape: 'wide' },
    siteName: 'GitHub',
    themeColor: '#1e2327',
    title: 'GitHub - simonw/datasette',
  },
  'https://linear.app/method': {
    description: 'Practices for building. The quality of software is the result of the practices behind it.',
    favicon: { hue: 265, letter: 'L' },
    image: { hue: 270, shape: 'wide' },
    siteName: 'Linear',
    title: 'Linear Method',
  },
  'https://modelcontextprotocol.io/docs/extensions/apps': {
    description: 'Build interactive UI applications that render inside MCP hosts like Claude Desktop: charts, forms and dashboards, delivered by the server and shown in the conversation.',
    favicon: { hue: 25, letter: 'M' },
    image: { hue: 28, shape: 'wide' },
    siteName: 'Model Context Protocol',
    themeColor: '#0f0f0f',
    title: 'MCP Apps - Model Context Protocol',
  },
  'https://oklch.com/': {
    description: 'OKLCH is a new way to encode colours, like hex, RGBA or HSL. A picker and converter for it.',
    favicon: { hue: 320, letter: 'O' },
    image: { hue: 300, shape: 'square' },
    siteName: 'OKLCH Color Picker',
    title: 'OKLCH Color Picker & Converter',
  },
  'https://practicaltypography.com/typography-in-ten-minutes.html': {
    description: 'This is a bold claim, but I stand behind it: if you learn and follow these five typography rules, you will be a better typographer than nearly every writer.',
    title: 'Typography in ten minutes',
  },
  'https://rauno.me/craft/interaction-design': {
    description: 'Why some interactions feel better than others: metaphors, kinetic physics, swipe gestures and responsive feedback.',
    favicon: { hue: 150, letter: 'R' },
    image: { hue: 160, shape: 'wide' },
    siteName: 'Rauno Freiberg',
    title: 'Invisible Details of Interaction Design',
  },
  'https://stripe.com/blog/payment-api-design': {
    description: 'Abstractions, mistakes and migrations: how the PaymentIntents API came to replace Charges.',
    favicon: { hue: 245, letter: 'S' },
    image: { hue: 240, shape: 'wide' },
    siteName: 'Stripe',
    themeColor: '#635bff',
    title: 'Stripe’s payments APIs: the first 10 years',
  },
  'https://web.dev/articles/light-dark': {
    description: 'The light-dark() CSS function takes two colours and returns the first when the used colour scheme is light, and the second when it is dark.',
    favicon: { hue: 220, letter: 'W' },
    image: { hue: 190, shape: 'wide' },
    siteName: 'web.dev',
    themeColor: '#fff',
    title: 'CSS light-dark() | Articles | web.dev',
  },
  'https://www.esa.int/Science_Exploration/Space_Science/Euclid': {
    description: 'ESA’s Euclid mission is designed to explore the composition and evolution of the dark Universe.',
    favicon: { hue: 215, letter: 'E' },
    image: { hue: 265, shape: 'wide' },
    siteName: 'European Space Agency',
    themeColor: '#003247',
    title: 'ESA - Euclid',
  },
  'https://www.figma.com/blog/how-figmas-multiplayer-technology-works/': {
    description: 'A look at the data structures and sync engine behind real-time editing in Figma.',
    favicon: { hue: 15, letter: 'F' },
    image: { hue: 330, shape: 'wide' },
    siteName: 'Figma',
    title: 'How Figma’s multiplayer technology works',
  },
  'https://www.gov.uk/guidance/government-design-principles': {
    description: 'The UK government’s design principles and examples of how they have been used.',
    favicon: { hue: 200, letter: 'G' },
    siteName: 'GOV.UK',
    themeColor: '#0b0c0c',
    title: 'Government Design Principles',
  },
  'https://www.joshwcomeau.com/css/interactive-guide-to-grid/': {
    description: 'CSS Grid is one of the most amazing parts of the CSS language. This tutorial builds an intuition for it, one interactive demo at a time.',
    favicon: { hue: 280, letter: 'J' },
    image: { hue: 285, shape: 'wide' },
    siteName: 'Josh W. Comeau',
    title: 'An Interactive Guide to CSS Grid',
  },
  'https://www.nytimes.com/interactive/2026/03/14/science/deep-sea-map.html': {
    description: 'Less than a third of the ocean floor has been charted in detail. A fleet of uncrewed ships is trying to finish the job.',
    favicon: { hue: 0, letter: 'T' },
    image: { hue: 200, shape: 'wide' },
    siteName: 'The New York Times',
    title: 'Mapping the Sea Floor, One Ping at a Time',
  },
  'https://www.robinsloan.com/notes/home-cooked-app/': {
    description: 'I made a messaging app for, and with, my family. It is, in every way, a home-cooked meal.',
    favicon: { hue: 45, letter: 'R' },
    image: { hue: 35, shape: 'square' },
    siteName: 'Robin Sloan',
    themeColor: '#fffdf5',
    title: 'An app can be a home-cooked meal',
  },
  'https://www.typewolf.com/site-of-the-day': {
    description: 'A daily look at what’s trending in type on the web.',
    favicon: { hue: 5, letter: 'T' },
    siteName: 'Typewolf',
    title: 'Site of the Day · Typewolf',
  },
  'https://www.youtube.com/watch?v=PUv66718DII': {
    description: 'Bret Victor’s talk from CUSEC 2012, on giving creators an immediate connection to what they are making.',
    favicon: { hue: 0, letter: 'Y' },
    image: { hue: 355, shape: 'wide' },
    siteName: 'YouTube',
    themeColor: '#ff0000',
    title: 'Bret Victor - Inventing on Principle',
  },
}

interface Thumbnail {
  readonly data_uri: string
  readonly height: number
  readonly width: number
}

type Drawing =
  | { readonly hue: number, readonly kind: 'favicon', readonly letter: string }
  | { readonly hue: number, readonly kind: 'image', readonly shape: 'square' | 'wide' }

/** Runs in the page. A thumbnail is a soft two-hue field with a few shapes on it; a favicon a rounded tile with a letter. */
const draw = (drawing: Drawing): { data_uri: string, height: number, width: number } => {
  const canvas = document.createElement('canvas')
  const context = canvas.getContext('2d')
  if (!context) throw new Error('no 2d context')
  if (drawing.kind === 'favicon') {
    canvas.width = 32
    canvas.height = 32
    context.fillStyle = `hsl(${String(drawing.hue)} 62% 46%)`
    context.beginPath()
    context.roundRect(0, 0, 32, 32, 7)
    context.fill()
    context.fillStyle = '#fff'
    context.font = '700 20px system-ui, sans-serif'
    context.textAlign = 'center'
    context.textBaseline = 'middle'
    context.fillText(drawing.letter, 16, 17)
    return { data_uri: canvas.toDataURL('image/png'), height: 32, width: 32 }
  }
  const width = drawing.shape === 'wide' ? 320 : 240
  const height = drawing.shape === 'wide' ? 168 : 240
  canvas.width = width
  canvas.height = height
  const field = context.createLinearGradient(0, 0, width, height)
  field.addColorStop(0, `hsl(${String(drawing.hue)} 70% 62%)`)
  field.addColorStop(1, `hsl(${String((drawing.hue + 55) % 360)} 65% 38%)`)
  context.fillStyle = field
  context.fillRect(0, 0, width, height)
  // Seeded by the hue, so each site's picture is its own and a rerun redraws it the same.
  let seed = drawing.hue * 9301 + 49_297
  const next = (): number => {
    seed = (seed * 9301 + 49_297) % 233_280
    return seed / 233_280
  }
  for (let index = 0; index < 5; index += 1) {
    context.fillStyle = `hsl(${String((drawing.hue + 20 + index * 37) % 360)} 80% ${String(55 + next() * 30)}% / ${String(0.18 + next() * 0.22)})`
    context.beginPath()
    context.arc(next() * width, next() * height, 30 + next() * 70, 0, Math.PI * 2)
    context.fill()
  }
  context.fillStyle = 'rgb(255 255 255 / 0.85)'
  context.beginPath()
  context.roundRect(width * 0.12, height * 0.62, width * 0.5, height * 0.07, 6)
  context.roundRect(width * 0.12, height * 0.74, width * 0.32, height * 0.07, 6)
  context.fill()
  return { data_uri: canvas.toDataURL('image/webp', 0.72), height, width }
}

interface Entry {
  readonly description: string | null
  readonly favicon: Thumbnail | null
  readonly image: Thumbnail | null
  readonly site_name: string | null
  readonly status: string
  readonly theme_color: string | null
  readonly title: string | null
  readonly url: string
}

const blank = (url: string, status: string): Entry => ({
  description: null,
  favicon: null,
  image: null,
  site_name: null,
  status,
  theme_color: null,
  title: null,
  url,
})

const browser = await launchBrowser()
const page = await browser.newPage()
const full = new Map<string, Entry>()
try {
  for (const [url, site] of Object.entries(SITES)) {
    full.set(url, {
      description: site.description ?? null,
      favicon: site.favicon ? await page.evaluate(draw, { ...site.favicon, kind: 'favicon' as const }) : null,
      image: site.image ? await page.evaluate(draw, { ...site.image, kind: 'image' as const }) : null,
      site_name: site.siteName ?? null,
      status: 'ok',
      theme_color: site.themeColor ?? null,
      title: site.title ?? null,
      url,
    })
  }
}
finally {
  await browser.close()
}

const entry = (url: string): Entry => {
  const found = full.get(url)
  if (found === undefined) throw new Error(`no site for ${url}`)
  return found
}

/** The `rich` bookmarks, in order: the rows every scenario but `ok` is about. */
const bookmarks = JSON.parse(readFileSync(path.join(fixturesRoot, 'bookmarks.json'), 'utf8')) as Record<string, { structuredContent: { bookmarks: Array<{ url: string }> } }>
const richUrls = (bookmarks['rich']?.structuredContent.bookmarks ?? []).map(bookmark => bookmark.url)
const [first, second, third, fourth, fifth, sixth] = richUrls
if (first === undefined || second === undefined || third === undefined || fourth === undefined || fifth === undefined || sixth === undefined) {
  throw new Error('the rich bookmarks fixture needs six rows')
}

const validImage = entry(first).image
const markup = '<img src=x onerror=alert(1)><script>alert(\'x\')</script>'

const scenarios = {
  // Every bookmark in `rich` and `rich-many`, all found.
  'ok': { previews: [...full.values()] },
  // One of each outcome, and the partial ones: no thumbnail, no favicon.
  'mixed': {
    previews: [
      entry(first),
      blank(second, 'no_data'),
      blank(third, 'error'),
      { ...entry(fourth), image: null },
      { ...entry(fifth), favicon: null, site_name: null },
      { ...entry(sixth), description: null, image: null },
    ],
  },
  // What a server that sanitised nothing might send. The card's own guards
  // are all that stands between these and the document.
  'hostile': {
    previews: [
      { ...blank(first, 'ok'), description: markup, favicon: { data_uri: 'javascript:alert(1)', height: 32, width: 32 }, image: { data_uri: 'https://evil.example/tracker.png', height: 168, width: 320 }, site_name: markup, theme_color: 'red;background:url(https://evil.example/c)', title: markup },
      { ...blank(second, 'ok'), description: 'An SVG is a document, not a picture.', favicon: { data_uri: 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIG9ubG9hZD0iYWxlcnQoMSkiLz4=', height: 32, width: 32 }, image: { data_uri: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', height: 168, width: 320 }, site_name: 'SVG' },
      { ...blank(third, 'ok'), description: 'A data URI that tries to close its attribute.', image: { data_uri: 'data:image/png;base64,AAAA" onerror="alert(1)', height: 168, width: 320 }, site_name: 'Attribute' },
      { ...blank(fourth, 'ok'), description: 'A real image with dimensions nobody could mean.', favicon: validImage && { ...validImage, height: 'tall', width: -1 }, image: validImage && { ...validImage, height: 0, width: 99_999 }, site_name: 'Dimensions' },
      { ...blank(fifth, 'pwned'), description: 'A status the card has never heard of.', image: '//evil.example/protocol-relative.png', site_name: { toString: 'nope' } },
      // The one honest entry, so the capture shows a real thumbnail survives beside the rest.
      { ...entry(sixth), theme_color: '#12345' },
      // And one nobody asked about.
      entry('https://oklch.com/'),
    ],
  },
  // The bridge's limit reached half-way through a batch.
  'rate-limited': {
    previews: [entry(first), entry(second), entry(third), blank(fourth, 'rate_limited'), blank(fifth, 'rate_limited'), blank(sixth, 'rate_limited')],
    retry_after_seconds: 2,
  },
  // The bridge too busy to make some thumbnails: the text arrives, the
  // pictures do not, and those entries ask to be tried again.
  'busy': {
    previews: [
      entry(first),
      { ...entry(second), image: null, retry: true },
      entry(third),
      { ...entry(fourth), favicon: null, image: null, retry: true },
      entry(fifth),
      entry(sixth),
    ],
  },
}

const fixtures = Object.fromEntries(Object.entries(scenarios).map(([name, structuredContent]) => [name, { structuredContent }]))
const file = path.join(fixturesRoot, 'previews.json')
writeFileSync(file, `${JSON.stringify(fixtures, null, 2)}\n`)
console.log(`✓ wrote ${path.relative(process.cwd(), file)}`)
