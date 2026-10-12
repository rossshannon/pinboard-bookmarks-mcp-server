import { createRoot } from 'react-dom/client'

import { CardShell } from '../shell/card-shell.tsx'
import { BookmarksCard } from './bookmarks-card.tsx'
import { BookmarksSkeleton } from './bookmarks-skeleton.tsx'

// <CardShell> owns the handshake, lifecycle, sizing and theming;
// <BookmarksCard> is only how the data renders.
const root = document.querySelector('#root')

if (root) {
  createRoot(root).render(
    <CardShell appName="pinboard-bookmarks-card" skeleton={<BookmarksSkeleton />} title="Pinboard bookmarks">
      {(data, context) => <BookmarksCard context={context} data={data} />}
    </CardShell>,
  )
}
