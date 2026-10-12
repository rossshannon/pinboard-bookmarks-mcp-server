/** The tags card while it loads: the head and one row, the floor the frame grows from. */

import type { ReactNode } from 'react'

import { SkeletonLine } from '../shell/primitives.tsx'

export const TagsSkeleton = (): ReactNode => (
  <>
    <div className="skeleton-row">
      <SkeletonLine width="5rem" />
      <SkeletonLine width="40%" />
    </div>
    <SkeletonLine width="70%" />
  </>
)
