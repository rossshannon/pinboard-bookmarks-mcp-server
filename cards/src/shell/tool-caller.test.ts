import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ToolCallingApp } from './tool-caller.ts'

import { createBreadcrumbLog } from './breadcrumbs.ts'
import { createToolCaller, TOOL_CALL_TIMEOUT_MS } from './tool-caller.ts'

type Capabilities = ReturnType<ToolCallingApp['getHostCapabilities']>

/** An app whose host proxies tool calls, unless `capabilities` says otherwise (rest arguments, so `undefined` can be passed and meant). */
const appThat = (callServerTool: ToolCallingApp['callServerTool'], ...capabilities: [Capabilities?]): ToolCallingApp => ({
  callServerTool,
  getHostCapabilities: () => capabilities.length === 0 ? { serverTools: {} } : capabilities[0],
})

const labels = (log: ReturnType<typeof createBreadcrumbLog>): string[] => log.entries().map(entry => entry.label)

describe('createToolCaller', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.spyOn(console, 'log').mockImplementation(() => { /* breadcrumbs are not under test */ })
    globalThis.openai = undefined
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    globalThis.openai = undefined
  })

  it('calls the tool through the connected bridge and resolves to its structuredContent', async () => {
    const callServerTool = vi.fn<ToolCallingApp['callServerTool']>().mockResolvedValue({ content: [], structuredContent: { previews: [] } })
    const breadcrumbs = createBreadcrumbLog()
    const tools = createToolCaller(() => appThat(callServerTool), breadcrumbs)

    const outcome = await tools.callTool('get_link_previews', { urls: ['https://example.com'] })

    expect(outcome).toEqual({ data: { previews: [] }, status: 'ok' })
    expect(callServerTool).toHaveBeenCalledExactlyOnceWith(
      { arguments: { urls: ['https://example.com'] }, name: 'get_link_previews' },
      { timeout: TOOL_CALL_TIMEOUT_MS },
    )
    expect(labels(breadcrumbs)).toEqual(['callTool:requested', 'callTool:result'])
  })

  it('reads a result that only carries JSON text', async () => {
    const tools = createToolCaller(() => appThat(vi.fn().mockResolvedValue({ content: [{ text: '{"previews":[1]}', type: 'text' }] })), createBreadcrumbLog())

    expect(await tools.callTool('t', {})).toEqual({ data: { previews: [1] }, status: 'ok' })
  })

  it('is unavailable, without calling, when the host does not proxy tool calls', async () => {
    const callServerTool = vi.fn<ToolCallingApp['callServerTool']>()
    const breadcrumbs = createBreadcrumbLog()
    const tools = createToolCaller(() => appThat(callServerTool, { }), breadcrumbs)

    expect(await tools.callTool('t', {})).toEqual({ reason: 'no-capability', status: 'unavailable' })
    expect(callServerTool).not.toHaveBeenCalled()
    expect(breadcrumbs.summary()).toContain('callTool:unavailable t no-capability')
  })

  it('is unavailable when the host reported no capabilities at all', async () => {
    const tools = createToolCaller(() => appThat(vi.fn(), undefined), createBreadcrumbLog())

    expect(await tools.callTool('t', {})).toEqual({ reason: 'no-capability', status: 'unavailable' })
  })

  it('is unavailable when the host rejects the call, and says why in the breadcrumbs', async () => {
    const breadcrumbs = createBreadcrumbLog()
    const tools = createToolCaller(() => appThat(vi.fn().mockRejectedValue(new Error('MCP error -32601: Method not found'))), breadcrumbs)

    expect(await tools.callTool('t', {})).toEqual({ reason: 'rejected', status: 'unavailable' })
    expect(breadcrumbs.summary()).toContain('callTool:error MCP error -32601: Method not found')
  })

  it('is unavailable when the call throws synchronously', async () => {
    const tools = createToolCaller(() => appThat(() => {
      throw new Error('Not connected')
    }), createBreadcrumbLog())

    expect(await tools.callTool('t', {})).toEqual({ reason: 'rejected', status: 'unavailable' })
  })

  it('is unavailable when the tool reports a failure', async () => {
    const tools = createToolCaller(() => appThat(vi.fn().mockResolvedValue({ content: [{ text: 'Every URL must be an absolute http(s) URL', type: 'text' }], isError: true })), createBreadcrumbLog())

    expect(await tools.callTool('t', {})).toEqual({ reason: 'tool-error', status: 'unavailable' })
  })

  it.each([
    ['nothing', undefined],
    ['no content', { content: [] }],
    ['prose', { content: [{ text: 'not json', type: 'text' }] }],
  ])('is unavailable when the tool answers with %s', async (_name, result) => {
    const tools = createToolCaller(() => appThat(vi.fn().mockResolvedValue(result)), createBreadcrumbLog())

    expect(await tools.callTool('t', {})).toEqual({ reason: 'unreadable', status: 'unavailable' })
  })

  it('stops waiting after the timeout', async () => {
    const tools = createToolCaller(() => appThat(() => new Promise(() => { /* never answers */ })), createBreadcrumbLog())

    const pending = tools.callTool('t', {})
    await vi.advanceTimersByTimeAsync(TOOL_CALL_TIMEOUT_MS - 1)
    let settled = false
    void pending.then(() => {
      settled = true
    })
    await Promise.resolve()
    expect(settled).toBe(false)

    await vi.advanceTimersByTimeAsync(1)
    expect(await pending).toEqual({ reason: 'timeout', status: 'unavailable' })
  })

  it('ignores an answer that arrives after the timeout', async () => {
    let answer: (result: { structuredContent: Record<string, unknown> }) => void = () => undefined
    const tools = createToolCaller(() => appThat(() => new Promise((resolve) => {
      answer = resolve
    })), createBreadcrumbLog(), 100)

    const pending = tools.callTool('t', {})
    await vi.advanceTimersByTimeAsync(100)
    answer({ structuredContent: { late: true } })

    expect(await pending).toEqual({ reason: 'timeout', status: 'unavailable' })
  })

  describe('before the bridge connects', () => {
    it('holds the call and sends it once the bridge is up', async () => {
      let connected: ToolCallingApp | null = null
      const callServerTool = vi.fn<ToolCallingApp['callServerTool']>().mockResolvedValue({ structuredContent: { ok: true } })
      const breadcrumbs = createBreadcrumbLog()
      const tools = createToolCaller(() => connected, breadcrumbs)

      const pending = tools.callTool('t', { a: 1 })
      await vi.advanceTimersByTimeAsync(500)
      expect(callServerTool).not.toHaveBeenCalled()
      expect(labels(breadcrumbs)).toContain('callTool:held')

      connected = appThat(callServerTool)
      tools.flush()

      expect(await pending).toEqual({ data: { ok: true }, status: 'ok' })
      expect(callServerTool).toHaveBeenCalledOnce()
    })

    it('is unavailable if the bridge never connects', async () => {
      const tools = createToolCaller(() => null, createBreadcrumbLog())

      const pending = tools.callTool('t', {})
      await vi.advanceTimersByTimeAsync(TOOL_CALL_TIMEOUT_MS)

      expect(await pending).toEqual({ reason: 'not-connected', status: 'unavailable' })
    })

    it('uses ChatGPT’s own callTool when that host has one', async () => {
      const callTool = vi.fn().mockResolvedValue({ structuredContent: { from: 'openai' } })
      globalThis.openai = { callTool }
      const breadcrumbs = createBreadcrumbLog()
      const tools = createToolCaller(() => null, breadcrumbs)

      expect(await tools.callTool('get_link_previews', { urls: [] })).toEqual({ data: { from: 'openai' }, status: 'ok' })
      expect(callTool).toHaveBeenCalledExactlyOnceWith('get_link_previews', { urls: [] })
      expect(labels(breadcrumbs)).toContain('callTool:openai')
    })

    it('says so apart when ChatGPT’s callTool rejects: the bridge has not been tried', async () => {
      globalThis.openai = { callTool: vi.fn().mockRejectedValue(new Error('denied')) }
      const breadcrumbs = createBreadcrumbLog()
      const tools = createToolCaller(() => null, breadcrumbs)

      expect(await tools.callTool('t', {})).toEqual({ reason: 'rejected-before-connect', status: 'unavailable' })
      expect(labels(breadcrumbs)).toContain('callTool:error')
    })

    it.each([undefined, 'done', {}, { content: [] }])('takes an answer it cannot read from ChatGPT’s callTool (%j) the same way', async (answer) => {
      globalThis.openai = { callTool: vi.fn().mockResolvedValue(answer) }
      const tools = createToolCaller(() => null, createBreadcrumbLog())

      expect(await tools.callTool('t', {})).toEqual({ reason: 'rejected-before-connect', status: 'unavailable' })
    })

    it('still takes a tool’s own failure through ChatGPT’s callTool as final', async () => {
      globalThis.openai = { callTool: vi.fn().mockResolvedValue({ content: [{ text: 'no', type: 'text' }], isError: true }) }
      const tools = createToolCaller(() => null, createBreadcrumbLog())

      expect(await tools.callTool('t', {})).toEqual({ reason: 'tool-error', status: 'unavailable' })
    })

    it('prefers the bridge to window.openai once it is connected', async () => {
      const viaOpenAi = vi.fn()
      globalThis.openai = { callTool: viaOpenAi }
      const tools = createToolCaller(() => appThat(vi.fn().mockResolvedValue({ structuredContent: { from: 'bridge' } })), createBreadcrumbLog())

      expect(await tools.callTool('t', {})).toEqual({ data: { from: 'bridge' }, status: 'ok' })
      expect(viaOpenAi).not.toHaveBeenCalled()
    })
  })

  describe('onConnect', () => {
    it('tells its listeners when the shell flushes, until they unsubscribe', () => {
      const tools = createToolCaller(() => null, createBreadcrumbLog())
      const listener = vi.fn()
      const unsubscribe = tools.onConnect(listener)

      expect(listener).not.toHaveBeenCalled()
      tools.flush()
      expect(listener).toHaveBeenCalledOnce()

      unsubscribe()
      tools.flush()
      expect(listener).toHaveBeenCalledOnce()
    })
  })

  describe('canCallTools', () => {
    it('is the host’s serverTools capability once connected', () => {
      expect(createToolCaller(() => appThat(vi.fn()), createBreadcrumbLog()).canCallTools()).toBe(true)
      expect(createToolCaller(() => appThat(vi.fn(), {}), createBreadcrumbLog()).canCallTools()).toBe(false)
      expect(createToolCaller(() => appThat(vi.fn(), undefined), createBreadcrumbLog()).canCallTools()).toBe(false)
    })

    it('allows for a handshake still to come', () => {
      expect(createToolCaller(() => null, createBreadcrumbLog()).canCallTools()).toBe(true)
    })

    it('is false on ChatGPT’s toolOutput path when that host has no callTool', () => {
      globalThis.openai = { toolOutput: {} }
      expect(createToolCaller(() => null, createBreadcrumbLog()).canCallTools()).toBe(false)

      globalThis.openai = { callTool: vi.fn(), toolOutput: {} }
      expect(createToolCaller(() => null, createBreadcrumbLog()).canCallTools()).toBe(true)
    })
  })
})
