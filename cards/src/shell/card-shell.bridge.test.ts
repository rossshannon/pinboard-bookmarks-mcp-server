// @vitest-environment jsdom
/**
 * Ported from the &Open card shell. Bridge tests: the real `App` from the
 * shell's wiring (`wireCardApp`) connected to a real `AppBridge` over
 * `InMemoryTransport` — no React. jsdom only so the theming checks have a
 * document to theme. This is the "does the SDK integration actually work end-to-end"
 * layer; `card-shell.test.tsx` covers the React state machine with a mocked
 * `useApp`.
 */
import type { McpUiAppCapabilities, McpUiStyles } from '@modelcontextprotocol/ext-apps'
import type { JSONRPCMessage } from '@modelcontextprotocol/sdk/types.js'

import { App, McpUiInitializeRequestSchema } from '@modelcontextprotocol/ext-apps'
import { AppBridge } from '@modelcontextprotocol/ext-apps/app-bridge'
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js'
import { describe, expect, it, vi } from 'vitest'

import type { CardWiringCallbacks } from './card-shell.tsx'

import { wireCardApp } from './card-shell.tsx'
import { applyHostLook } from './host-theme.ts'

const TEST_APP_INFO = { name: 'pinboard-test-card', version: '1' }
const TEST_HOST_INFO = { name: 'pinboard-test-host', version: '1' }
const TEST_CAPABILITIES: McpUiAppCapabilities = { availableDisplayModes: ['inline'] }

describe('wireCardApp (bridge)', () => {
  it('completes the ui/initialize handshake, sending protocolVersion 2026-01-26', async () => {
    const [viewTransport, hostTransport] = InMemoryTransport.createLinkedPair()
    const sent: JSONRPCMessage[] = []
    const originalSend = viewTransport.send.bind(viewTransport)
    viewTransport.send = (message, options) => {
      sent.push(message)
      return originalSend(message, options)
    }
    const app = new App(TEST_APP_INFO, TEST_CAPABILITIES, { autoResize: false })
    const bridge = new AppBridge(null, TEST_HOST_INFO, {})
    wireCardApp(app, { onError: vi.fn(), onResult: vi.fn() })

    await bridge.connect(hostTransport)
    await app.connect(viewTransport)

    const initializeMessage = sent.find(message => 'method' in message && message.method === 'ui/initialize')
    const parsed = McpUiInitializeRequestSchema.parse(initializeMessage)
    expect(parsed.params.protocolVersion).toBe('2026-01-26')
  })

  it('renders a tool-result delivered after the handshake completes', async () => {
    const [viewTransport, hostTransport] = InMemoryTransport.createLinkedPair()
    const app = new App(TEST_APP_INFO, TEST_CAPABILITIES, { autoResize: false })
    const bridge = new AppBridge(null, TEST_HOST_INFO, {})
    const onResult = vi.fn<CardWiringCallbacks['onResult']>()
    wireCardApp(app, { onError: vi.fn(), onResult })

    await bridge.connect(hostTransport)
    await app.connect(viewTransport)
    await bridge.sendToolResult({ content: [], structuredContent: { name: 'Dana' } })
    await vi.waitFor(() => {
      expect(onResult).toHaveBeenCalledOnce()
    })

    expect(onResult).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ structuredContent: { name: 'Dana' } }))
  })

  it('renders a tool-result delivered before the handshake resolves (registered in onAppCreated, before connect)', async () => {
    const [viewTransport, hostTransport] = InMemoryTransport.createLinkedPair()
    const app = new App(TEST_APP_INFO, TEST_CAPABILITIES, { autoResize: false })
    const bridge = new AppBridge(null, TEST_HOST_INFO, {})
    const onResult = vi.fn<CardWiringCallbacks['onResult']>()
    // Wired before connect(), exactly like onAppCreated -- so a tool-result
    // arriving mid-handshake is never missed.
    wireCardApp(app, { onError: vi.fn(), onResult })

    await bridge.connect(hostTransport)
    const connectPromise = app.connect(viewTransport)
    // The transport is attached (super.connect() has run) as soon as
    // app.connect() starts, before the ui/initialize round trip finishes --
    // sending the result now exercises the pre-handshake-completion path.
    await bridge.sendToolResult({ content: [], structuredContent: { name: 'Pre-handshake' } })
    await connectPromise

    await vi.waitFor(() => {
      expect(onResult).toHaveBeenCalledOnce()
    })
    expect(onResult).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ structuredContent: { name: 'Pre-handshake' } }))
  })

  it('answers a teardown request exactly once, with an empty result', async () => {
    const [viewTransport, hostTransport] = InMemoryTransport.createLinkedPair()
    const app = new App(TEST_APP_INFO, TEST_CAPABILITIES, { autoResize: false })
    const bridge = new AppBridge(null, TEST_HOST_INFO, {})
    const onTeardown = vi.fn()
    wireCardApp(app, { onError: vi.fn(), onResult: vi.fn(), onTeardown })

    await bridge.connect(hostTransport)
    await app.connect(viewTransport)
    const result = await bridge.teardownResource({})

    expect(result).toEqual({})
    expect(onTeardown).toHaveBeenCalledOnce()
  })

  it('emits ui/open-link when the app calls openLink', async () => {
    const [viewTransport, hostTransport] = InMemoryTransport.createLinkedPair()
    const app = new App(TEST_APP_INFO, TEST_CAPABILITIES, { autoResize: false })
    const bridge = new AppBridge(null, TEST_HOST_INFO, {})
    const onOpenLink = vi.fn().mockResolvedValue({})
    bridge.onopenlink = onOpenLink
    wireCardApp(app, { onError: vi.fn(), onResult: vi.fn() })

    await bridge.connect(hostTransport)
    await app.connect(viewTransport)
    await app.openLink({ url: 'https://pinboard.in/u:ross/' })

    expect(onOpenLink).toHaveBeenCalledExactlyOnceWith({ url: 'https://pinboard.in/u:ross/' }, expect.anything())
  })

  it('adopts the theme and style variables the host reports at the handshake', async () => {
    const [viewTransport, hostTransport] = InMemoryTransport.createLinkedPair()
    const app = new App(TEST_APP_INFO, TEST_CAPABILITIES, { autoResize: false })
    const variables = { '--color-background-primary': 'rgb(1, 2, 3)' } as unknown as McpUiStyles
    const bridge = new AppBridge(null, TEST_HOST_INFO, {}, { hostContext: { styles: { variables }, theme: 'dark' } })
    wireCardApp(app, { onError: vi.fn(), onResult: vi.fn() })

    await bridge.connect(hostTransport)
    await app.connect(viewTransport)

    expect(applyHostLook(app)).toBe('dark')
    expect(document.documentElement.dataset['theme']).toBe('dark')
    expect(document.documentElement.style.colorScheme).toBe('dark')
    expect(document.documentElement.style.getPropertyValue('--color-background-primary')).toBe('rgb(1, 2, 3)')
  })

  it('follows a theme change the host makes after the handshake', async () => {
    const [viewTransport, hostTransport] = InMemoryTransport.createLinkedPair()
    const app = new App(TEST_APP_INFO, TEST_CAPABILITIES, { autoResize: false })
    const bridge = new AppBridge(null, TEST_HOST_INFO, {}, { hostContext: { theme: 'light' } })
    const onHostContextChanged = vi.fn(() => applyHostLook(app))
    wireCardApp(app, { onError: vi.fn(), onHostContextChanged, onResult: vi.fn() })

    await bridge.connect(hostTransport)
    await app.connect(viewTransport)
    bridge.setHostContext({ theme: 'dark' })

    await vi.waitFor(() => {
      expect(onHostContextChanged).toHaveBeenCalled()
    })
    expect(document.documentElement.dataset['theme']).toBe('dark')
  })

  it('forwards a transport error to onError', async () => {
    const [viewTransport, hostTransport] = InMemoryTransport.createLinkedPair()
    const app = new App(TEST_APP_INFO, TEST_CAPABILITIES, { autoResize: false })
    const bridge = new AppBridge(null, TEST_HOST_INFO, {})
    const onError = vi.fn()
    wireCardApp(app, { onError, onResult: vi.fn() })

    await bridge.connect(hostTransport)
    await app.connect(viewTransport)
    const transportError = new Error('transport exploded')
    viewTransport.onerror?.(transportError)

    expect(onError).toHaveBeenCalledExactlyOnceWith(transportError)
  })
})
