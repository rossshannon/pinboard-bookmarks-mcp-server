import { vi } from 'vitest'

import type { CardRenderContext } from '../src/shell/card-shell.tsx'

import { createBreadcrumbLog } from '../src/shell/breadcrumbs.ts'

/** A render context with no bridge: what a card sees before connect, and enough for every jsdom suite. */
export const stubContext = (overrides: Partial<CardRenderContext> = {}): CardRenderContext => ({
  app: null,
  breadcrumbs: createBreadcrumbLog(),
  error: undefined,
  remeasure: vi.fn(),
  ...overrides,
})
