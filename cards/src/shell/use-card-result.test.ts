// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { extractToolError, extractToolResult, useCardResult } from './use-card-result.ts'

describe('extractToolResult', () => {
  it('returns undefined for a missing result', () => {
    expect(extractToolResult()).toBeUndefined()
    expect(extractToolResult(null)).toBeUndefined()
  })

  it('prefers structuredContent above every other tier', () => {
    const result = extractToolResult({
      content: [{ text: '{"from":"text"}' }],
      structuredContent: { from: 'structuredContent' },
      toolOutput: { from: 'toolOutput' },
    })

    expect(result).toEqual({ from: 'structuredContent' })
  })

  it('falls back to toolOutput when structuredContent is absent', () => {
    const result = extractToolResult({
      content: [{ text: '{"from":"text"}' }],
      toolOutput: { from: 'toolOutput' },
    })

    expect(result).toEqual({ from: 'toolOutput' })
  })

  it('falls back to parsing content[0].text when neither field is present', () => {
    const result = extractToolResult({ content: [{ text: '{"from":"text"}' }] })

    expect(result).toEqual({ from: 'text' })
  })

  it('returns undefined when content[0].text is not valid JSON, leaving waiting to persist', () => {
    expect(extractToolResult({ content: [{ text: 'not json' }] })).toBeUndefined()
  })

  it('returns undefined when content[0].text parses to a non-object JSON value', () => {
    expect(extractToolResult({ content: [{ text: '42' }] })).toBeUndefined()
    expect(extractToolResult({ content: [{ text: 'null' }] })).toBeUndefined()
  })

  it('wraps a JSON array in content[0].text as `result`, as structuredContent wraps a list', () => {
    expect(extractToolResult({ content: [{ text: '[{"tag":"a","count":1}]' }] })).toEqual({ result: [{ count: 1, tag: 'a' }] })
  })

  it('returns undefined when there is no content, structuredContent, or toolOutput', () => {
    expect(extractToolResult({})).toBeUndefined()
  })

  it('returns undefined when content is empty', () => {
    expect(extractToolResult({ content: [] })).toBeUndefined()
  })

  it('returns undefined when content[0] has no text field', () => {
    expect(extractToolResult({ content: [{}] })).toBeUndefined()
  })
})

describe('extractToolError', () => {
  it('returns undefined for a missing result', () => {
    expect(extractToolError()).toBeUndefined()
    expect(extractToolError(null)).toBeUndefined()
  })

  it('returns undefined for a result that did not fail', () => {
    expect(extractToolError({ structuredContent: { name: 'Dana' } })).toBeUndefined()
    expect(extractToolError({ content: [{ text: 'Nope.' }], isError: false })).toBeUndefined()
  })

  it('returns the failure text for a failed result', () => {
    const failure = extractToolError({ content: [{ text: 'This action is not permitted.' }], isError: true })

    expect(failure).toEqual({ text: 'This action is not permitted.' })
  })

  it('returns an empty text for a failure carrying nothing to show', () => {
    expect(extractToolError({ isError: true })).toEqual({ text: '' })
    expect(extractToolError({ content: [{}], isError: true })).toEqual({ text: '' })
  })

  it('outranks a payload on the same result, so a failure is never rendered as a body', () => {
    const failure = extractToolError({ isError: true, structuredContent: { gift_requests: [] } })

    expect(failure).toEqual({ text: '' })
  })
})

describe('useCardResult', () => {
  it('starts with no data and generation 0', () => {
    const { result } = renderHook(() => useCardResult())

    expect(result.current.data).toBeUndefined()
    expect(result.current.generation).toBe(0)
  })

  it('recordData sets the data and bumps the generation', () => {
    const { result } = renderHook(() => useCardResult())

    act(() => {
      result.current.recordData({ name: 'Dana' })
    })

    expect(result.current.data).toEqual({ name: 'Dana' })
    expect(result.current.generation).toBe(1)
  })

  it('recordData bumps the generation again on re-delivery', () => {
    const { result } = renderHook(() => useCardResult())

    act(() => {
      result.current.recordData({ name: 'Dana' })
    })
    act(() => {
      result.current.recordData({ name: 'Dana Okafor' })
    })

    expect(result.current.data).toEqual({ name: 'Dana Okafor' })
    expect(result.current.generation).toBe(2)
  })

  it('recordResult extracts and renders, returning true', () => {
    const { result } = renderHook(() => useCardResult())

    let rendered = false
    act(() => {
      rendered = result.current.recordResult({ structuredContent: { name: 'Dana' } })
    })

    expect(rendered).toBe(true)
    expect(result.current.data).toEqual({ name: 'Dana' })
    expect(result.current.generation).toBe(1)
  })

  it('recordResult leaves state unchanged and returns false when extraction fails', () => {
    const { result } = renderHook(() => useCardResult())

    let rendered = true
    act(() => {
      rendered = result.current.recordResult({ content: [{ text: 'not json' }] })
    })

    expect(rendered).toBe(false)
    expect(result.current.data).toBeUndefined()
    expect(result.current.generation).toBe(0)
  })

  it('recordResult records a failure and counts as rendered, so waiting does not persist', () => {
    const { result } = renderHook(() => useCardResult())

    let rendered = false
    act(() => {
      rendered = result.current.recordResult({ content: [{ text: 'This action is not permitted.' }], isError: true })
    })

    expect(rendered).toBe(true)
    expect(result.current.error).toEqual({ text: 'This action is not permitted.' })
    expect(result.current.generation).toBe(1)
  })

  it('a failure clears data already rendered, so the card shows the latest delivery only', () => {
    const { result } = renderHook(() => useCardResult())

    act(() => {
      result.current.recordData({ name: 'Dana' })
    })
    act(() => {
      result.current.recordResult({ content: [{ text: 'Authentication required: reconnect.' }], isError: true })
    })

    expect(result.current.data).toBeUndefined()
    expect(result.current.error).toEqual({ text: 'Authentication required: reconnect.' })
  })

  it('data arriving after a failure clears it, for the same reason', () => {
    const { result } = renderHook(() => useCardResult())

    act(() => {
      result.current.recordResult({ content: [{ text: 'Authentication required: reconnect.' }], isError: true })
    })
    act(() => {
      result.current.recordResult({ structuredContent: { name: 'Dana' } })
    })

    expect(result.current.error).toBeUndefined()
    expect(result.current.data).toEqual({ name: 'Dana' })
  })
})
