# MCP Apps result cards

In a host that supports [MCP Apps](https://modelcontextprotocol.io/docs/extensions/apps) (SEP-1865), every tool result renders as a card instead of a block of JSON:

| Card | Tools | Shows |
|---|---|---|
| `bookmarks` | `search_bookmarks`, `search_bookmarks_extended`, `list_recent_bookmarks`, `list_bookmarks_by_tags` | Title (linked), site, date saved, notes, tags. Tags you searched for are outlined, and private `.tags` are in italics. The first 8 rows show, with a "Show all" button for the rest. |
| `tags` | `list_tags` | Tags by count as a bar chart. The top 20 show, with a filter box and "Show all". Lists are capped at 300 rows, so past 300 tags the button says "Show top 300" and the filter finds the rest. |

The model still gets the same structured result as before. The card is only for the reader. Hosts without MCP Apps support ignore the card entirely.

## How it fits together

```
tool descriptor ──_meta.ui.resourceUri──▶ ui://pinboard/bookmarks@<hash>
                                                 │  resources/read
                                                 ▼
                         text/html;profile=mcp-app (self-contained, ~100 KiB gzipped)
                                                 │  host renders in a sandboxed iframe
                                                 ▼
                     card ◀── ui/initialize, tool result (structuredContent) ── host
                     card ──── size reports, ui/open-link ───────────────────▶ host
```

- **Server** (`src/pinboard_mcp_server/ui/`) serves the committed documents in `ui/generated/`. Each URI carries a fingerprint of the bytes, so a host that caches by URI picks up a new build. The same resource is also registered as a template on the fingerprint, so a stale URI still resolves to the running build.
- **Cards** (`cards/`) are React 19, built by Vite with `vite-plugin-singlefile` into one HTML file each. They load nothing from the network themselves. Their CSP admits one thing: the fonts a host hands over in `styles.css.fonts`, from Claude's asset origin and Google Fonts (`HOST_FONT_ORIGINS`). Without those origins the host's faces would be blocked and the card would fall back to the system font.
- **Off switch**: `PINBOARD_MCP_APPS_UI=false` (or `0`/`no`/`off`) removes the `_meta` from every tool and registers no `ui://` resources.

The design is a port of the &Open MCP server's card layer. The shell and sizing code are carried over almost verbatim, and the header notes in `cards/src/shell/` say where.

## The shell

`cards/src/shell/card-shell.tsx` wraps every card. It owns everything that isn't the card's own content:

- **Handshake and state.**
  - A skeleton shows immediately.
  - On ChatGPT, where the result is already on `window.openai.toolOutput`, the card renders immediately.
  - Otherwise the card waits for the tool result over the bridge.
  - If nothing has arrived after 1.5s, the card shows "Waiting for…".
  - If nothing has arrived after 12s, it shows a "Card diagnostic" with breadcrumbs that help explain why.
  - Once rendered, the card never goes back to waiting.
- **Sizing.** The card reports its height to the host. It measures on a ladder of delays after each render and ramps smoothly when it grows. Once content has rendered, a `ResizeObserver` on the body re-reports the size whenever the layout changes height on its own, for example when the host changes the frame's width or a host font loads late. The SDK's own always-on auto-resize stays off (`autoResize: false`), and cards still call `remeasure()` when they expand, as "Show all" and "Show more" do. When the bridge isn't connected yet, the card posts a raw `ui/notifications/size-changed` instead, so a host can size it.
- **Theme.** The card adopts the host's theme, its CSS variables (`--color-background-primary` and so on) and its fonts through ext-apps, and follows later changes to them. `card.css` defines `--pb-*` tokens that use the host's variable when there is one. Otherwise they fall back to OKLCH `light-dark()` values, so a host that sends only `theme`, or nothing at all, still looks right.
- **Links.** `SafeLink` is the only place a card renders an `href`, and only for `http(s)`. The sandbox blocks navigation, so a plain click goes to the shell's link opener (`link-opener.ts`). Once the bridge is connected it sends `ui/open-link`. Before that it uses ChatGPT's `window.openai.openExternal` when that exists, and otherwise holds the click until the bridge connects. A bookmark with any other scheme shows its title as text.
- **Errors.** A failed tool call renders an error block. A rejected token gets its own message that points at `PINBOARD_TOKEN`.

Cards read `structuredContent` defensively, through the small readers in `guards.ts`. A missing or mistyped field drops out instead of throwing.

## Working on the cards

Node 22.18+ is needed for development only. The Python package never runs Node.

```bash
cd cards
npm ci
npm run build          # rebuild src/pinboard_mcp_server/ui/generated/*.html: commit the result
npm test               # unit, bridge and contract-shape tests (Vitest + jsdom)
npm run check          # committed documents == a fresh build, and under the 125 KiB gzip ceiling
npm run test:browser   # the built documents in real Chromium through AppBridge
npm run capture        # screenshots of every fixture, both themes, 600px and 375px → tmp/captures/
```

If Chromium is already installed (as in Claude Code's cloud containers), point at it with `PLAYWRIGHT_CHROMIUM_PATH`. Otherwise run `npx playwright-core install chromium` once.

Fixtures live in `cards/fixtures/*.json`: normal, empty, hostile (markup and `javascript:` URLs), very long, and error results. `tests/test_ui.py` checks that their shapes match what the real tools return, so the fixtures can't drift from the server.

CI runs all of the above except `capture`, in the `cards` job.

### Size

Each card is about 100 KiB gzipped, most of it React and the ext-apps SDK. The SDK's zod schemas are aliased to `zod/mini` through `src/zod-classic-shim.ts`, which saves about 70 KiB. A parity test checks the shim against real zod. `npm run check` fails at 125 KiB.

## Hosts

- **Claude** (web, desktop, mobile) renders MCP Apps for connectors. In &Open's experience, a local stdio server added through `claude_desktop_config.json` didn't render cards, while the same server added as a connector did. If cards don't appear, try it as a connector.
- **ChatGPT** apps render the same documents. The shell's `window.openai` fast path is for ChatGPT.
- **Other clients** (Cursor, Claude Code and so on) ignore `_meta.ui` and show the plain result.
