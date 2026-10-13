/**
 * The CI gate for the committed card documents: rebuild every card into a
 * temporary directory and byte-compare against what is committed, and hold
 * each to the size ceiling. Never writes into the repo; a mismatch names the
 * command that fixes it. A merge conflict in a generated document is resolved
 * by rebuilding, never by hand.
 */

import console from 'node:console'
import { existsSync, readFileSync } from 'node:fs'
import process from 'node:process'

import { buildAllCards, CARD_GZIP_CEILING_BYTES, generatedPath, gzipSize, sizeReport } from './build-cards.ts'

let ok = true

await buildAllCards((card, html) => {
  const committedPath = generatedPath(card)
  const committed = existsSync(committedPath) ? readFileSync(committedPath, 'utf8') : undefined
  if (committed === html) {
    console.log(`✓ ${card}: committed document is current`)
  }
  else {
    ok = false
    console.error(`✖ ${card}: committed document is ${committed === undefined ? 'missing' : 'stale'} — run \`npm run build\` in cards/ and commit the result`)
  }
  const bytes = gzipSize(html)
  if (bytes > CARD_GZIP_CEILING_BYTES) {
    ok = false
    console.error(`✖ ${card}: exceeds the size ceiling — ${sizeReport(bytes)}`)
  }
  else {
    console.log(`✓ ${card}: within the size ceiling — ${sizeReport(bytes)}`)
  }
})

if (!ok) process.exitCode = 1
