/**
 * Narrowing `list_tags` output. The tool returns a bare list, which MCP cannot
 * carry as `structuredContent` (an object), so FastMCP wraps it as
 * `{ "result": [...] }`; the reader takes `result`, and `tags` too, in case
 * the tool ever names its own key.
 */

import { isRecord, isUnknownArray, readCount, readText } from '../guards.ts'

export interface TagCount {
  readonly tag: string
  readonly count: number
}

/** Most-used first; ties alphabetically, so the order is stable. */
const byUse = (a: TagCount, b: TagCount): number => b.count - a.count || a.tag.localeCompare(b.tag)

export const readTags = (data: Record<string, unknown>): readonly TagCount[] => {
  const list = isUnknownArray(data['result']) ? data['result'] : isUnknownArray(data['tags']) ? data['tags'] : []
  return list
    .flatMap((value) => {
      if (!isRecord(value)) return []
      const tag = readText(value['tag'])
      return tag === undefined ? [] : [{ count: readCount(value['count']) ?? 0, tag }]
    })
    .toSorted(byUse)
}

/** Case-insensitive substring match; an empty filter keeps everything. */
export const filterTags = (tags: readonly TagCount[], filter: string): readonly TagCount[] => {
  const needle = filter.trim().toLowerCase()
  return needle === '' ? tags : tags.filter(({ tag }) => tag.toLowerCase().includes(needle))
}
