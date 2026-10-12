/**
 * State-machine tests for `<CardShell>`, under fake timers, against a mocked
 * `useApp` — in jsdom nothing answers a real `PostMessageTransport`, so a real
 * handshake would hang. `card-shell.bridge.test.ts` covers the real SDK.
 *
 * The mock is a tiny real hook, as in the &Open shell's suite: `useState` for
 * `{ app, isConnected, error }`, and `onAppCreated` called once, before any
 * connect, the way the real hook calls it.
 */
import type { McpUiHostContext } from '@modelcontextprotocol/ext-apps'

import { act, render, screen } from '@testing-library/react'
import { useRef, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { CardRenderContext, CardShellProperties } from './card-shell.tsx'
import type { CardSize } from './sizing.ts'

import { SIZE_SETTLE_DELAY_MS } from './sizing.ts'

interface FakeApp {
  addEventListener: (event: string, handler: (parameters: unknown) => void) => void
  getHostContext: () => McpUiHostContext | undefined
  onerror?: (error: Error) => void
  onteardown?: () => unknown
  openLink: ReturnType<typeof vi.fn>
  sendSizeChanged: ReturnType<typeof vi.fn<(size: CardSize) => Promise<void>>>
}

interface MockAppState {
  app: FakeApp | null
  error: Error | null
  isConnected: boolean
}

let toolResultHandler: ((parameters: unknown) => void) | undefined
let hostContextChangedHandler: ((parameters: unknown) => void) | undefined
let hostContext: McpUiHostContext | undefined
let fakeApp: FakeApp
let setMockState: ((state: MockAppState) => void) | undefined

vi.mock('@modelcontextprotocol/ext-apps/react', () => ({
  useApp: (options: { onAppCreated?: (app: FakeApp) => void }): MockAppState => {
    const [state, setState] = useState<MockAppState>({ app: null, error: null, isConnected: false })
    const created = useRef(false)
    if (!created.current) {
      created.current = true
      options.onAppCreated?.(fakeApp)
    }
    setMockState = setState
    return state
  },
}))

const { CardShell, chooseFadeIn } = await import('./card-shell.tsx')

/** What jsdom's body measures as, so the shell has a size to report rather than refusing an empty document. */
const BODY_HEIGHT = 200

const renderChild = vi.fn<CardShellProperties['children']>((data, context) => (
  <p data-testid="rendered">{`${String(data['name'])}::${context.error?.text ?? 'ok'}`}</p>
))

const renderShell = (skeleton?: boolean): ReturnType<typeof render> => render(
  <CardShell appName="pinboard-test-card" skeleton={skeleton ? <p data-testid="skeleton">skeleton</p> : undefined} title="Test card">
    {renderChild}
  </CardShell>,
)

const connect = (): void => {
  act(() => {
    setMockState?.({ app: fakeApp, error: null, isConnected: true })
  })
}

const deliver = (result: unknown): void => {
  act(() => {
    toolResultHandler?.(result)
  })
}

const lastContext = (): CardRenderContext => {
  const call = renderChild.mock.calls.at(-1)
  if (!call) throw new Error('the card never rendered')
  return call[1]
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.spyOn(console, 'log').mockImplementation(() => { /* breadcrumbs are not under test */ })
  Object.defineProperty(document.body, 'scrollHeight', { configurable: true, value: BODY_HEIGHT })
  toolResultHandler = undefined
  hostContextChangedHandler = undefined
  hostContext = undefined
  fakeApp = {
    addEventListener: (event, handler) => {
      if (event === 'toolresult') toolResultHandler = handler
      if (event === 'hostcontextchanged') hostContextChangedHandler = handler
    },
    getHostContext: () => hostContext,
    openLink: vi.fn().mockResolvedValue({}),
    sendSizeChanged: vi.fn<(size: CardSize) => Promise<void>>().mockResolvedValue(),
  }
  renderChild.mockClear()
  globalThis.openai = undefined
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  delete document.documentElement.dataset['theme']
  document.documentElement.style.colorScheme = ''
})

describe('CardShell', () => {
  it('sets the document title', () => {
    renderShell()

    expect(document.title).toBe('Test card')
  })

  it('draws the skeleton from the first render, labelled for assistive technology', () => {
    renderShell(true)

    expect(screen.getByTestId('skeleton')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Loading…')
  })

  it('renders the structured content of a tool result once connected', () => {
    renderShell(true)
    connect()
    deliver({ structuredContent: { name: 'Pinboard' } })

    expect(screen.getByTestId('rendered')).toHaveTextContent('Pinboard::ok')
  })

  it('renders a result delivered before the handshake resolves', () => {
    renderShell()
    deliver({ structuredContent: { name: 'early' } })

    expect(screen.getByTestId('rendered')).toHaveTextContent('early::ok')
  })

  it('holds a link clicked before the handshake and sends it through the bridge once connected', async () => {
    renderShell()
    deliver({ structuredContent: { name: 'early' } })

    await act(async () => {
      await lastContext().links.openLink({ url: 'https://pinboard.in/' })
    })
    expect(fakeApp.openLink).not.toHaveBeenCalled()

    connect()
    expect(fakeApp.openLink).toHaveBeenCalledExactlyOnceWith({ url: 'https://pinboard.in/' })

    await act(async () => {
      await lastContext().links.openLink({ url: 'https://example.com/' })
    })
    expect(fakeApp.openLink).toHaveBeenLastCalledWith({ url: 'https://example.com/' })
  })

  it('opens links with window.openai.openExternal on a host that never connects', async () => {
    const openExternal = vi.fn()
    globalThis.openai = { openExternal, toolOutput: { name: 'fast' } }
    renderShell()

    await act(async () => {
      await lastContext().links.openLink({ url: 'https://pinboard.in/' })
    })

    expect(openExternal).toHaveBeenCalledExactlyOnceWith({ href: 'https://pinboard.in/' })
    expect(fakeApp.openLink).not.toHaveBeenCalled()
  })

  it('renders window.openai.toolOutput at once, on the ChatGPT fast path', () => {
    globalThis.openai = { toolOutput: { name: 'fast' } }

    renderShell(true)

    expect(screen.getByTestId('rendered')).toHaveTextContent('fast')
    expect(screen.queryByTestId('skeleton')).toBeNull()
  })

  it('renders window.openai.toolOutput set after mount, when the host announces it', () => {
    globalThis.openai = {}
    renderShell(true)
    expect(screen.queryByTestId('rendered')).toBeNull()

    globalThis.openai = { toolOutput: { name: 'late' } }
    act(() => {
      globalThis.dispatchEvent(new Event('openai:set_globals'))
    })

    expect(screen.getByTestId('rendered')).toHaveTextContent('late')
    act(() => {
      vi.advanceTimersByTime(12_000)
    })
    expect(screen.queryByText(/Card diagnostic/)).toBeNull()
  })

  it('hands a failed call to the card as context.error, with no data', () => {
    renderShell()
    connect()
    deliver({ content: [{ text: 'Pinboard is down', type: 'text' }], isError: true })

    expect(screen.getByTestId('rendered')).toHaveTextContent('undefined::Pinboard is down')
  })

  it('says it is waiting after 1.5s when the handshake resolved but no result came', () => {
    renderShell()
    connect()
    act(() => {
      vi.advanceTimersByTime(1500)
    })

    expect(screen.getByText('Waiting for the tool result…')).toBeInTheDocument()
  })

  it('escalates a wait to the diagnostic card after ~12s', () => {
    renderShell()
    connect()
    act(() => {
      vi.advanceTimersByTime(12_000)
    })

    expect(screen.getByText('Card diagnostic')).toBeInTheDocument()
  })

  it('shows the diagnostic card after 1.5s when the handshake never resolves', () => {
    renderShell(true)
    act(() => {
      vi.advanceTimersByTime(1500)
    })

    expect(screen.getByText('Card diagnostic')).toBeInTheDocument()
    expect(screen.queryByTestId('skeleton')).toBeNull()
  })

  it('never paints a state card over content that has rendered', () => {
    renderShell()
    deliver({ structuredContent: { name: 'kept' } })
    act(() => {
      vi.advanceTimersByTime(20_000)
    })
    act(() => {
      setMockState?.({ app: null, error: new Error('handshake rejected'), isConnected: false })
    })

    expect(screen.getByTestId('rendered')).toHaveTextContent('kept')
    expect(screen.queryByText('Card diagnostic')).toBeNull()
  })

  it('answers teardown with an empty result', () => {
    renderShell()

    expect(fakeApp.onteardown?.()).toEqual({})
  })

  it('adopts the host theme on connect, and follows a change to it', () => {
    hostContext = { theme: 'dark' }
    renderShell()
    connect()

    expect(document.documentElement.dataset['theme']).toBe('dark')

    hostContext = { theme: 'light' }
    act(() => {
      hostContextChangedHandler?.({ theme: 'light' })
    })

    expect(document.documentElement.dataset['theme']).toBe('light')
    expect(document.documentElement.style.colorScheme).toBe('light')
  })

  it('reports the size again when a card asks it to remeasure after settling', () => {
    renderShell()
    connect()
    deliver({ structuredContent: { name: 'grows' } })
    act(() => {
      vi.advanceTimersByTime(SIZE_SETTLE_DELAY_MS + 2000)
    })
    const before = fakeApp.sendSizeChanged.mock.calls.length
    expect(before).toBeGreaterThan(0)

    Object.defineProperty(document.body, 'scrollHeight', { configurable: true, value: BODY_HEIGHT + 300 })
    act(() => {
      lastContext().remeasure()
    })
    act(() => {
      vi.advanceTimersByTime(2000)
    })

    expect(fakeApp.sendSizeChanged.mock.calls.length).toBeGreaterThan(before)
    expect(fakeApp.sendSizeChanged.mock.calls.at(-1)?.[0].height).toBe(BODY_HEIGHT + 300)
  })
})

describe('chooseFadeIn', () => {
  it('dissolves the skeleton quickly when the reader saw it', () => {
    expect(chooseFadeIn({ skeletonPainted: true, wireHeight: 300 })).toEqual({ overlay: 'skeleton', slow: false })
  })

  it('fades a blank card slowly when the skeleton was never painted', () => {
    expect(chooseFadeIn({ skeletonPainted: false, wireHeight: 300 })).toEqual({ overlay: 'blank', slow: true })
  })

  it('counts a skeleton painted into an unsized frame as unseen', () => {
    expect(chooseFadeIn({ skeletonPainted: true, wireHeight: 10 })).toEqual({ overlay: 'blank', slow: true })
  })
})
