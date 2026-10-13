// @vitest-environment node
import { build } from 'esbuild'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { z } from 'zod'

import { zodShimPath } from '../scripts/build-cards.ts'

/**
 * Parity gate for the `zod/v4` → `zod/mini` build alias
 * (`src/zod-classic-shim.ts`). The shim only holds if it validates the
 * host's postMessage frames identically to the zod the SDK is written against,
 * so a zod or SDK change its method map does not cover fails here rather than
 * shipping a card whose frame validation differs from the host's.
 *
 * Both frame-validating dependency trees — `sdk/types.js` and `ext-apps` — are
 * bundled twice from one entry, differing only in whether `build-cards.ts`'s
 * alias is applied, so the alias is the only variable. They are reached through
 * the bundle rather than imported here because the Node TS program cannot
 * resolve `ext-apps`'s types (the resolution the SDK needs is a bundler's, which
 * is what the bundle gives it).
 */

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

/** Schemas the ext-apps bridge and the SDK validate postMessage frames with. */
const SCHEMAS = [
  'CallToolRequestSchema',
  'CallToolResultSchema',
  'EmptyResultSchema',
  'ImplementationSchema',
  'JSONRPCMessageSchema',
  'CreateMessageResultSchema',
  'ListResourcesResultSchema',
  'ListToolsRequestSchema',
  'McpUiInitializeResultSchema',
  'McpUiThemeSchema',
  'McpUiToolResultNotificationSchema',
  'PingRequestSchema',
  'ReadResourceResultSchema',
] as const

interface Case {
  readonly accept: boolean
  readonly input: unknown
  readonly label: string
  readonly schema: SchemaName
}

type SchemaName = (typeof SCHEMAS)[number]

interface Vector {
  readonly constructsApp: boolean
  readonly extSchemaCount: number
  readonly results: boolean[]
  readonly unresolved: string[]
}

const HOST_INITIALIZE_RESULT = {
  hostCapabilities: {},
  hostContext: {},
  hostInfo: { name: 'host', version: '1' },
  protocolVersion: '2026-01-26',
}

// Cases chosen to reach across the shim's method surface, not just the happy path.
const CASES: readonly Case[] = [
  // The JSON-RPC envelope — the frame validator Protocol runs on every message.
  { accept: true, input: { id: 1, jsonrpc: '2.0', method: 'ui/initialize', params: { protocolVersion: '2026-01-26' } }, label: 'request ok', schema: 'JSONRPCMessageSchema' },
  { accept: true, input: { id: 1, jsonrpc: '2.0', result: {} }, label: 'response ok', schema: 'JSONRPCMessageSchema' },
  { accept: true, input: { jsonrpc: '2.0', method: 'notifications/tool-result', params: {} }, label: 'notification ok', schema: 'JSONRPCMessageSchema' },
  { accept: true, input: { error: { code: -1, message: 'm' }, id: 1, jsonrpc: '2.0' }, label: 'error ok', schema: 'JSONRPCMessageSchema' },
  { accept: false, input: { id: 1, jsonrpc: '1.0', method: 'x' }, label: 'bad jsonrpc version', schema: 'JSONRPCMessageSchema' },
  { accept: false, input: { id: 1, jsonrpc: '2.0' }, label: 'missing method/result', schema: 'JSONRPCMessageSchema' },
  { accept: false, input: { error: { code: -1 }, id: 1, jsonrpc: '2.0' }, label: 'error missing message', schema: 'JSONRPCMessageSchema' },
  { accept: false, input: { id: {}, jsonrpc: '2.0', method: 'm' }, label: 'id wrong type', schema: 'JSONRPCMessageSchema' },
  { accept: false, input: { id: 1, jsonrpc: '2.0', method: 'm', params: 'str' }, label: 'params wrong type', schema: 'JSONRPCMessageSchema' },
  { accept: false, input: 'hello', label: 'not an object', schema: 'JSONRPCMessageSchema' },
  { accept: false, input: null, label: 'null', schema: 'JSONRPCMessageSchema' },

  { accept: true, input: { content: [{ text: 'hi', type: 'text' }] }, label: 'text block ok', schema: 'CallToolResultSchema' },
  { accept: false, input: { content: [{ text: 123, type: 'text' }] }, label: 'text not a string', schema: 'CallToolResultSchema' },
  { accept: false, input: { content: [{ type: 'nope' }] }, label: 'unknown block type', schema: 'CallToolResultSchema' },
  { accept: false, input: { content: [{ mimeType: 'image/png', type: 'image' }] }, label: 'image missing data', schema: 'CallToolResultSchema' },
  { accept: true, input: { content: [{ data: 'AA', mimeType: 'image/png', type: 'image' }] }, label: 'image ok', schema: 'CallToolResultSchema' },
  // Block annotations carry `priority: z.number().min(0).max(1)` — the numeric
  // branch of the shim's min/max dispatch, which the length branch would
  // silently invert.
  { accept: false, input: { content: [{ annotations: { priority: 2 }, text: 'hi', type: 'text' }] }, label: 'annotation priority above max', schema: 'CallToolResultSchema' },
  { accept: false, input: { content: [{ annotations: { priority: -1 }, text: 'hi', type: 'text' }] }, label: 'annotation priority below min', schema: 'CallToolResultSchema' },
  { accept: true, input: { content: [{ annotations: { priority: 1 }, text: 'hi', type: 'text' }] }, label: 'annotation priority at bound', schema: 'CallToolResultSchema' },

  { accept: false, input: { name: 'x' }, label: 'missing version', schema: 'ImplementationSchema' },
  { accept: true, input: { name: 'x', version: '1' }, label: 'name+version ok', schema: 'ImplementationSchema' },

  // The shape the card document itself is served through.
  { accept: true, input: { contents: [{ text: 'hi', uri: 'ui://andopen/ping@abc' }] }, label: 'text contents ok', schema: 'ReadResourceResultSchema' },
  { accept: true, input: { contents: [{ blob: 'AA', uri: 'ui://andopen/ping@abc' }] }, label: 'blob contents ok', schema: 'ReadResourceResultSchema' },
  // The one refinement-bearing field reachable here — base64, checked by a
  // `.refine()` rather than by shape.
  { accept: false, input: { contents: [{ blob: 'not valid base64!!', uri: 'ui://andopen/ping@abc' }] }, label: 'blob not base64', schema: 'ReadResourceResultSchema' },
  { accept: false, input: { contents: [{ text: 'hi' }] }, label: 'contents missing uri', schema: 'ReadResourceResultSchema' },

  { accept: true, input: { resources: [{ name: 'n', uri: 'ui://andopen/ping@abc' }] }, label: 'resource ok', schema: 'ListResourcesResultSchema' },
  { accept: false, input: { resources: [{ uri: 'ui://andopen/ping@abc' }] }, label: 'resource missing name', schema: 'ListResourcesResultSchema' },
  // `annotations.lastModified` is the frame schemas' one `z.iso.datetime`, the
  // shim factory no other case reaches. RFC 3339 requires the seconds.
  { accept: true, input: { resources: [{ annotations: { lastModified: '2026-08-01T00:00:00Z' }, name: 'n', uri: 'ui://andopen/ping@abc' }] }, label: 'resource last modified ok', schema: 'ListResourcesResultSchema' },
  { accept: true, input: { resources: [{ annotations: { lastModified: '2026-08-01T00:00:00+01:00' }, name: 'n', uri: 'ui://andopen/ping@abc' }] }, label: 'resource last modified with offset', schema: 'ListResourcesResultSchema' },
  { accept: false, input: { resources: [{ annotations: { lastModified: '2026-08-01T00:00Z' }, name: 'n', uri: 'ui://andopen/ping@abc' }] }, label: 'resource last modified without seconds', schema: 'ListResourcesResultSchema' },

  // Requests the bridge answers.
  { accept: true, input: { method: 'tools/call', params: { name: 't' } }, label: 'call ok', schema: 'CallToolRequestSchema' },
  { accept: false, input: { method: 'tools/call', params: {} }, label: 'call missing name', schema: 'CallToolRequestSchema' },
  { accept: false, input: { method: 'tools/list', params: { name: 't' } }, label: 'call wrong method', schema: 'CallToolRequestSchema' },
  { accept: true, input: { method: 'tools/list' }, label: 'list ok', schema: 'ListToolsRequestSchema' },
  { accept: true, input: { method: 'tools/list', params: { cursor: 'c' } }, label: 'list with cursor', schema: 'ListToolsRequestSchema' },
  { accept: false, input: { method: 'tools/list', params: 'str' }, label: 'list params wrong type', schema: 'ListToolsRequestSchema' },
  { accept: true, input: { method: 'ping' }, label: 'ping ok', schema: 'PingRequestSchema' },
  { accept: false, input: { method: 'notifications/ping' }, label: 'ping wrong method', schema: 'PingRequestSchema' },
  { accept: true, input: {}, label: 'empty ok', schema: 'EmptyResultSchema' },
  // `content` is `.default([])`, so an empty result must still parse — the one
  // case that would catch a no-op `default` in the shim.
  { accept: true, input: {}, label: 'content defaults to empty', schema: 'CallToolResultSchema' },
  // The host's sampling response, validated by ext-apps.
  { accept: true, input: { content: { text: 'hi', type: 'text' }, model: 'm', role: 'assistant' }, label: 'sampling result ok', schema: 'CreateMessageResultSchema' },
  { accept: false, input: { content: { text: 'hi', type: 'text' }, role: 'assistant' }, label: 'sampling result missing model', schema: 'CreateMessageResultSchema' },
  // EmptyResultSchema is strict()-backed; without an extra-key case a shim bug
  // turning strict() into a no-op would flip nothing.
  { accept: false, input: { unexpected: 1 }, label: 'strict rejects extra key', schema: 'EmptyResultSchema' },

  // ext-apps's own schemas — the card's first host frame is an initialize result.
  { accept: true, input: HOST_INITIALIZE_RESULT, label: 'initialize result ok', schema: 'McpUiInitializeResultSchema' },
  { accept: false, input: { ...HOST_INITIALIZE_RESULT, protocolVersion: 7 }, label: 'protocol version not a string', schema: 'McpUiInitializeResultSchema' },
  { accept: false, input: { ...HOST_INITIALIZE_RESULT, hostContext: undefined }, label: 'initialize result missing host context', schema: 'McpUiInitializeResultSchema' },
  { accept: true, input: { method: 'ui/notifications/tool-result', params: {} }, label: 'tool-result notification ok', schema: 'McpUiToolResultNotificationSchema' },
  { accept: false, input: { method: 'notifications/tool-result', params: {} }, label: 'tool-result wrong method', schema: 'McpUiToolResultNotificationSchema' },
  { accept: true, input: 'dark', label: 'known theme', schema: 'McpUiThemeSchema' },
  { accept: false, input: 'chartreuse', label: 'unknown theme', schema: 'McpUiThemeSchema' },
]

const ENTRY = [
  'import * as sdk from \'@modelcontextprotocol/sdk/types.js\'',
  'import * as ext from \'@modelcontextprotocol/ext-apps\'',
  `const CASES = ${JSON.stringify(CASES)}`,
  'const find = name => sdk[name] ?? ext[name]',
  'export const extSchemaCount = Object.keys(ext).filter(name => name.endsWith(\'Schema\')).length',
  'export const unresolved = [...new Set(CASES.map(c => c.schema))].filter(name => find(name) === undefined)',
  'export const constructsApp = (() => { try { new ext.App({ name: \'parity\', version: \'0\' }); return true } catch { return false } })()',
  // Guarded: an unresolved name would otherwise throw during module evaluation
  // and the assertion naming it would never run.
  'export const results = unresolved.length > 0 ? [] : CASES.map(c => find(c.schema).safeParse(c.input).success)',
].join('\n')

const VECTOR = z.object({
  constructsApp: z.boolean(),
  extSchemaCount: z.number(),
  results: z.array(z.boolean()),
  unresolved: z.array(z.string()),
})

/** Bundle and evaluate the entry, so the schemas run against whichever zod the alias selects. */
const vector = async (alias: Record<string, string>): Promise<Vector> => {
  const bundled = await build({
    alias,
    bundle: true,
    format: 'esm',
    logLevel: 'silent',
    stdin: { contents: ENTRY, loader: 'js', resolveDir: repoRoot, sourcefile: 'parity-entry.js' },
    write: false,
  })

  const code = bundled.outputFiles[0]?.text ?? ''
  const module: unknown = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)
  return VECTOR.parse(module)
}

describe('zod shim parity', () => {
  it('accepts and rejects each frame identically under the shim and real zod', async () => {
    const real = await vector({})
    const shim = await vector({ 'zod/v4': zodShimPath })

    expect(shim.results).toStrictEqual(real.results)

    // Non-vacuous: a fixture that resolved nothing, or documented its own
    // expectations wrongly, would otherwise still agree with itself.
    expect(real.unresolved).toStrictEqual([])
    expect(real.results).toStrictEqual(CASES.map(testCase => testCase.accept))
    expect(real.results).toContain(true)
    expect(real.results).toContain(false)
  })

  it('constructs every ext-apps schema and an App under the shim', async () => {
    const real = await vector({})
    const shim = await vector({ 'zod/v4': zodShimPath })

    // Importing ext-apps evaluates all of its schemas, so a construction the
    // shim cannot serve throws before any case runs. Matching the real count
    // also proves the bundle kept them rather than shaking them out.
    expect(shim.extSchemaCount).toBe(real.extSchemaCount)
    expect(shim.extSchemaCount).toBeGreaterThan(0)

    // The App constructor calls into zod outside schema definition — z.config
    // among others — which no accept/reject case reaches.
    expect(shim.constructsApp).toBe(true)
  })

  it('exercises every listed schema', () => {
    for (const schema of SCHEMAS) {
      expect(CASES.some(testCase => testCase.schema === schema)).toBe(true)
    }
  })
})
