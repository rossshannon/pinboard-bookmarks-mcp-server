/**
 * The bookmarks card while it loads: the head and one row. One row, not
 * several, because a skeleton is the floor the frame grows from, never a
 * ceiling it shrinks to — a three-row skeleton would close up around a
 * one-result search.
 */

import type { ReactNode } from 'react'

import { SkeletonChip, SkeletonLine } from '../shell/primitives.tsx'

export const BookmarksSkeleton = (): ReactNode => (
  <>
    <div className="skeleton-row">
      <SkeletonLine width="7rem" />
      <SkeletonLine width="55%" />
    </div>
    <div className="skeleton-row">
      <SkeletonLine width="80%" />
      <SkeletonLine width="35%" />
      <div className="skeleton-chips">
        <SkeletonChip />
        <SkeletonChip width="3rem" />
        <SkeletonChip width="5rem" />
      </div>
    </div>
  </>
)
