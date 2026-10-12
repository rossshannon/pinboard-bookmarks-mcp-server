/**
 * Builds every card in `CARDS` with Vite's programmatic API into one
 * self-contained HTML document (`vite-plugin-singlefile` inlines the script
 * and the stylesheet), and writes it where the Python package serves it from:
 * `src/pinboard_mcp_server/ui/generated/<card>.html`.
 *
 * The built documents are committed. The server is a Python package installed
 * from PyPI with no Node anywhere near it, so the HTML has to ship as package
 * data; and committing it means a `git diff` shows exactly what a card change
 * did to what hosts receive. `check-cards.ts` rebuilds into a temporary
 * directory and byte-compares, so a stale document fails CI.
 *
 * Ported from the &Open MCP server's `build-cards.ts`, which codegens a TS
 * module instead: its Lambda bundle cannot load an `.html` file, and Python's
 * `importlib.resources` can.
 */

import react from '@vitejs/plugin-react'
import console from 'node:console'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'
import { gzipSync } from 'node:zlib'
import { build } from 'vite'
import { viteSingleFile } from 'vite-plugin-singlefile'

const cardsRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

/** Where the Python package reads the built documents from. */
export const GENERATED_DIRECTORY = path.resolve(cardsRoot, '../src/pinboard_mcp_server/ui/generated')

/**
 * Alias target for the card build's `zod/v4` → `zod/mini` swap: the SDK
 * validates every host frame with zod, and mini's runtime is a fraction of
 * classic's. Exported so the parity suite aliases against the same module.
 */
export const zodShimPath = path.join(cardsRoot, 'src/zod-classic-shim.ts')

/**
 * The gzipped ceiling. Nearly all of a card is the fixed React + SDK runtime;
 * a card that outgrows this is the thing to fix, not the ceiling.
 */
export const CARD_GZIP_CEILING_BYTES = 128_000

/** Card directories under `src/`, each a Vite entry with its own `index.html`. */
export const CARDS = ['bookmarks', 'tags'] as const

export type CardName = (typeof CARDS)[number]

export const gzipSize = (html: string): number => gzipSync(html).length

export const sizeReport = (bytes: number): string =>
  `${(bytes / 1024).toFixed(1)} KiB gzipped / ${(CARD_GZIP_CEILING_BYTES / 1024).toFixed(0)} KiB ceiling`

export const generatedPath = (card: CardName): string => path.join(GENERATED_DIRECTORY, `${card}.html`)

/**
 * Build one card into `outDirectory` and return the document. Deterministic by
 * design — no sourcemaps, no timestamp or environment defines, and the
 * toolchain pinned to exact versions — so a rebuild byte-matches.
 */
export const buildCardHtml = async (card: CardName, outDirectory: string): Promise<string> => {
  await build({
    build: { emptyOutDir: false, outDir: outDirectory, sourcemap: false },
    configFile: false,
    logLevel: 'warn',
    plugins: [react(), viteSingleFile()],
    // Anchored to the exact specifier, so `zod/v4/core` and friends still resolve to real zod.
    resolve: { alias: [{ find: /^zod\/v4$/, replacement: zodShimPath }] },
    root: path.join(cardsRoot, 'src', card),
  })
  // A trailing newline, so the committed file ends the way every other text file does.
  const html = readFileSync(path.join(outDirectory, 'index.html'), 'utf8')
  return html.endsWith('\n') ? html : `${html}\n`
}

/** Build every card into a throwaway directory, handing each document to `onCard`. */
export const buildAllCards = async (onCard: (card: CardName, html: string) => void): Promise<void> => {
  const buildRoot = mkdtempSync(path.join(tmpdir(), 'pinboard-cards-'))
  try {
    for (const card of CARDS) {
      onCard(card, await buildCardHtml(card, path.join(buildRoot, card)))
    }
  }
  finally {
    rmSync(buildRoot, { force: true, recursive: true })
  }
}

const isMainModule = (): boolean => {
  const invokedPath = process.argv[1]
  return invokedPath !== undefined && fileURLToPath(import.meta.url) === path.resolve(invokedPath)
}

if (isMainModule()) {
  mkdirSync(GENERATED_DIRECTORY, { recursive: true })
  await buildAllCards((card, html) => {
    const bytes = gzipSize(html)
    if (bytes > CARD_GZIP_CEILING_BYTES) throw new Error(`✖ ${card} exceeds the card size ceiling — ${sizeReport(bytes)}`)
    writeFileSync(generatedPath(card), html)
    console.log(`✓ built ${card} -> ${path.relative(path.resolve(cardsRoot, '..'), generatedPath(card))} (${sizeReport(bytes)})`)
  })
}
