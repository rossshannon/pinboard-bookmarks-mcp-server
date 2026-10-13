import { describe, expect, it } from 'vitest'

import { classifyCardError, errorDetail } from './error-block.tsx'

describe('classifyCardError', () => {
  it.each([
    '401 Client Error: Unauthorized for url: https://api.pinboard.in/v1/posts/recent',
    'Error: PINBOARD_TOKEN environment variable is required',
    'Forbidden',
  ])('reads "%s" as a refused token', (text) => {
    expect(classifyCardError(text)).toBe('unauthenticated')
  })

  it('reads anything else as a plain failure', () => {
    expect(classifyCardError('Limit must be between 1 and 100')).toBe('error')
    expect(classifyCardError('')).toBe('error')
  })
})

describe('errorDetail', () => {
  it('drops FastMCP’s lead-in naming the tool', () => {
    expect(errorDetail('Error calling tool \'list_tags\': boom')).toBe('boom')
    expect(errorDetail('Error executing tool \'list_tags\': boom')).toBe('boom')
    expect(errorDetail('plain')).toBe('plain')
  })
})
