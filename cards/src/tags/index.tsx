import { createRoot } from 'react-dom/client'

import { CardShell } from '../shell/card-shell.tsx'
import { TagsCard } from './tags-card.tsx'
import { TagsSkeleton } from './tags-skeleton.tsx'

const root = document.querySelector('#root')

if (root) {
  createRoot(root).render(
    <CardShell appName="pinboard-tags-card" skeleton={<TagsSkeleton />} title="Pinboard tags">
      {(data, context) => <TagsCard context={context} data={data} />}
    </CardShell>,
  )
}
