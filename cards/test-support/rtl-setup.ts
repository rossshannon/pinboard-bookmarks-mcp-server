// jest-dom's matchers and Testing Library's cleanup, registered once for every
// jsdom suite.
import '@testing-library/jest-dom/vitest'

import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => {
  cleanup()
})
