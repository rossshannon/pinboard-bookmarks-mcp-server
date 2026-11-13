# MCP Apps result cards

In a host that supports [MCP Apps](https://modelcontextprotocol.io/docs/extensions/apps) (SEP-1865), every tool result renders as a card instead of a block of JSON:

| Card | Tools | Shows |
|---|---|---|
| `bookmarks` | `search_bookmarks`, `search_bookmarks_extended`, `list_recent_bookmarks`, `list_bookmarks_by_tags` | Each bookmark as a link card: site and date saved, title (linked), notes, tags. Tags you searched for are outlined, and private `.tags` are in italics. The first 8 rows show, with a "Show all" button for the rest. With [link previews](#link-previews) on, a row also gets the site's favicon and name, the page's description when the bookmark has no notes, and a thumbnail. |
| `tags` | `list_tags` | Tags by count as a bar chart. The top 20 show, with a filter box and "Show all". Lists are capped at 300 rows, so past 300 tags the button says "Show top 300" and the filter finds the rest. |

The model still gets the same structured result as before. The card is only for the reader. Hosts without MCP Apps support ignore the card entirely.

One more tool exists only for the bookmarks card, and only when link previews are configured:

| Tool | Called by | Returns |
|---|---|---|
| `get_link_previews` | The `bookmarks` card, never the model (`_meta.ui.visibility: ["app"]`) | For up to 8 URLs: site name, description, and a favicon and thumbnail as inline `data:` images. |

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
- **Links.** `SafeLink` is the only place a card renders an `href`, and only for `http(s)`. The sandbox blocks navigation, so a click goes to the shell's link opener (`link-opener.ts`): a plain click, a Cmd-, Ctrl- or Shift-click, and a middle-click alike. Only an Alt-click, which browsers treat as "download", is left to the browser. Once the bridge is connected it sends `ui/open-link`. Before that it uses ChatGPT's `window.openai.openExternal` when that exists, and otherwise holds the click until the bridge connects. A bookmark with any other scheme shows its title as text.
- **Errors.** A failed tool call renders an error block. A rejected token gets its own message that points at `PINBOARD_TOKEN`.
- **Tool calls.** A card can call a server tool itself with `context.callTool(name, args)` (`tool-caller.ts`). The call goes through the host (`tools/call` over the bridge) and resolves to the tool's `structuredContent`. It never rejects. If the bridge isn't connected in time, the host didn't advertise `serverTools`, the host or the tool refuses, or nothing comes back in 15 seconds, it resolves to `{ status: 'unavailable', reason }` and leaves a breadcrumb. `context.canCallTools()` says whether a call has anywhere to go, so a card needn't draw a placeholder for an answer that can't arrive. Before the handshake, on ChatGPT, it uses `window.openai.callTool` if that exists. If that channel refuses the call or answers with something unreadable, the call waits for the bridge instead and is sent down it when it connects. If none connects in time, the reason is `rejected-before-connect`, which isn't final: `context.onConnect(listener)` tells a card when the bridge has connected, so it can ask again. A call that has timed out is never sent afterwards.
- **Images.** `SafeImage` is the only place a card renders an `<img>`, and only from a `data:image/(webp|png);base64,…` URI whose bytes start as that format, with its width and height given (`safe-image.ts`). A remote URL, an SVG or anything else draws nothing.

Cards read `structuredContent` defensively, through the small readers in `guards.ts`. A missing or mistyped field drops out instead of throwing.

## Link previews

Link previews are opt-in. They are on when `PINBOARD_PREVIEW_BRIDGE_URL` names a pinboard-bridge instance and the cards themselves are on. Otherwise `get_link_previews` isn't registered, the bookmark tools return exactly what they always have, and the card draws its rows from the bookmark alone.

```
bookmark tool ──structuredContent { bookmarks: [{ url, preview_token, … }], previews_available: true }──▶ card renders its rows
                                                                          │
card ── tools/call get_link_previews { items: [≤ 8 × { url, token }] } ──▶ host ──▶ server (previews.py)
                                                                          │  GET {bridge}/preview?url=…&thumbnails=1
                                                                          │  Authorization: Bearer <PINBOARD_TOKEN>
                                                                          ▼
                                                                   pinboard-bridge ──▶ the bookmarked page
                                                                          │
card ◀── { previews: [{ url, status, site_name, description, image, favicon, … }] } ◀── sanitised, cached
  │
  └─ rows gain favicon, site name, description, thumbnail; card reports its new height
```

The tool only looks up a URL that arrives with the token a bookmark tool sent for it (see [Which URLs it will look up](#which-urls-it-will-look-up)).

### Privacy

To preview a bookmark, the server sends its URL to the bridge, with the Pinboard token as a bearer token. The bridge then fetches that page. This applies to private bookmarks too, whenever they are in a result the card shows. That is why the feature is off until someone names a bridge they trust.

- The bridge URL must be `https`. Plain `http` is accepted for `localhost` and `127.0.0.1` only. Anything else is ignored with a logged warning, and previews stay off.
- The server doesn't follow redirects from the bridge, so the token goes to that host only.
- The token is never logged, and this module's own log lines don't name the bookmark. The HTTP client's own INFO line for each request does include the request URL, as it would for any request.
- The card fetches nothing. Images arrive inside the tool result as `data:` URIs, which the default MCP Apps CSP (`img-src 'self' data:`) already allows, so the card's CSP is unchanged. The bridge's remote `imageUrl` and `faviconUrl` are never passed on.

### The tool

`get_link_previews(items)` takes a list of 1 to 8 objects, each `{"url": …, "token": …}`: a bookmark's `url` and the `preview_token` that came with it. A request of any other shape (not a list, none or more than 8, an element that isn't an object with a string `url`) is a `ValueError`. A URL sent twice is looked up once. It returns one entry per URL, in order. An item it won't look up (a wrong or missing token, a URL that isn't a web URL) gets an `error` entry of its own, so one bad item costs one row its preview and not the whole call.

```json
{
  "previews": [
    {
      "url": "https://example.com/article",
      "status": "ok",
      "title": "An article",
      "description": "What the article is about.",
      "site_name": "Example",
      "theme_color": "#1a2b3c",
      "image": { "data_uri": "data:image/webp;base64,…", "width": 240, "height": 126 },
      "favicon": { "data_uri": "data:image/png;base64,…", "width": 32, "height": 32 }
    }
  ]
}
```

- `status` is one of:
  - `ok`
  - `no_data`: the page has nothing to show.
  - `error`: the bridge couldn't read the page or didn't answer, or the tool won't look this item up.
  - `rate_limited`: the bridge's rate limit is in force.
  - `pending`: the lookup was still running when the call had to answer. It carries on, and asking again a few seconds later finds its result.
- Every field but `url` and `status` is `null` unless the status is `ok`.
- `retry_after_seconds` is added beside `previews` when any entry is `rate_limited`. It is the bridge's `Retry-After`, held between 1 and 120, or 30 if the bridge gave none.
- `retry: true` is added to an entry when the bridge was too busy to make that page's thumbnails (`thumbnailsStatus: "busy"`). The entry still carries whatever did arrive.

The result is a FastMCP `ToolResult`, not a plain dict. FastMCP serialises a dict twice, once as `structuredContent` and once as JSON in the text block, which would send every image over the wire twice. The `ToolResult` carries the whole result in `structuredContent`. Its text block is the same result as compact JSON with every `image` and `favicon` set to `null`, so it stays small. The card reads `structuredContent` and falls back to the text block on a channel that relays only `content`; there it draws the text of each preview without pictures. This is the one tool that departs from the "return a dict" rule in `CLAUDE.md`.

The tool's `_meta` (`ui.APP_ONLY_TOOL_META`) carries three keys:

| Key | What it does |
|---|---|
| `ui.visibility: ["app"]` | The MCP Apps standard. The host keeps the tool out of the model's tool list and lets only the card call it. |
| `openai/widgetAccessible: true` | ChatGPT's own key. It lets a widget call the tool through `window.openai.callTool`. |
| `openai/visibility: "private"` | ChatGPT's own key. It hides the tool from the model there. |

A host without MCP Apps support ignores all three and lists the tool like any other. So its description says it is for the card and isn't useful to call directly, and the tool is restricted so that a model calling it can do no harm.

#### Which URLs it will look up

A host that ignores the visibility keys shows the tool to the model, and a model can be persuaded to call a tool with any argument. Without a restriction, that would make the bridge fetch any URL, with the user's Pinboard token attached to the request that asks for it, and return the page's images as base64 into the model's context.

So the tool looks up a URL only when it arrives with a token that proves this server returned that URL as a bookmark:

- With previews on, each bookmark in the four bookmark tools' results carries a `preview_token`. Nothing is added when previews are off.
- The token is the first 10 bytes of `HMAC-SHA256(key, url)`, as unpadded base64url (14 characters).
- The key is `SHA-256(b"pinboard-mcp-preview-v1\0" + PINBOARD_TOKEN)`. The server keeps no secret of its own.
- `get_link_previews` recomputes the token for each item and compares with `hmac.compare_digest`. An item that doesn't match gets an `error` entry, and the bridge is never contacted about it.
- A URL must also be absolute `http` or `https`, have a host, contain no whitespace, and be at most 2,048 characters. The card applies the same rule before it asks (`isPreviewableUrl` in `preview-loader.ts`), and doesn't ask about a bookmark that came without a token.

The check is stateless. A card rendered before a server restart, or by another worker of the same account, still gets its previews. The tokens are never logged.

A new tool that returns bookmarks must build its result with `_bookmarks_result` in `main.py`, which adds the tokens.

Because the bridge's answers are derived from arbitrary web pages, `previews.py` validates every field before the card sees it:

| Field | Rule |
|---|---|
| `title`, `description`, `site_name` | Strings only, trimmed, put on one line, capped at 300, 500 and 100 characters. |
| `image`, `favicon` | `data_uri` must be a base64 `data:` URI of type `image/webp` or `image/png` and nothing else (no parameters, no whitespace), decode as base64, and be at most 60 KB (image) or 12 KB (favicon) long. These are the two formats the bridge makes: WebP for a page's picture, PNG for a favicon. The decoded bytes must start as the declared format. `width` and `height` must be whole numbers from 1 to 1024, and must equal the real dimensions read from the image's own header (PNG `IHDR`; WebP `VP8`, `VP8L` or `VP8X`). An image that fails any of these is dropped whole. |
| `theme_color` | A 3- or 6-digit hex colour, lower-cased. Anything else is dropped. The card doesn't use it at present. |

The card applies the format rule again itself (`safe-image.ts`: WebP or PNG only, starting with that format's bytes) and renders all text as text. It doesn't re-read the dimensions. The server doesn't judge what a `site_name` says; the card does (see [What the card does](#what-the-card-does)).

Limits and caching on the server:

Timing on the server:

| Limit | Value | Why |
|---|---|---|
| One lookup | 8.5 seconds, counted from when the request is sent | The bridge answers a thumbnail request within 7 seconds of its own. Time spent queued isn't counted. |
| Lookups in flight | 8, across all calls | One call's 8 URLs go out together in one wave. The bridge runs 8 jobs at once per client, so there are never more in flight than it accepts. |
| One call | Answers within 12 seconds | The card stops waiting for a tool call after 15 seconds. |

- A lookup still running when its call answers isn't cancelled. Its entry comes back `pending`, the lookup finishes into the cache, and the card's second ask usually finds it there.
- Two calls asking about the same URL at the same time share one lookup.
- A bridge response over 1 MB is refused.
- Nothing one lookup does can fail the call. Any unexpected exception in a lookup (a response nested too deeply to parse, a URL the HTTP client won't send) becomes that URL's `error` entry, and only the exception's type is logged.

Caching on the server, in memory, up to 500 URLs:

| Answer | Kept for |
|---|---|
| Any answer with `thumbnailsStatus: "complete"` (or none, from an older bridge): `ok`, `no_data`, or `error` for a page the bridge will never be able to read | 6 hours. The bridge has said the answer is settled. |
| Any answer with `thumbnailsStatus` `transient` or `unavailable` | 10 minutes, so it is asked for again later |
| `error` from a 400 (a URL the bridge refused) | 10 minutes |
| `busy` (`retry: true`) | Not kept. The card is about to ask again, and a cached answer would be the same one. |
| One page's lookup failed on the bridge's side: it timed out, or came back 5xx or with an unusable body | 60 seconds, so the next card showing that page doesn't wait out the timeout again |
| `rate_limited`, `pending` | Not kept |
| The bridge couldn't be reached (no connection, no DNS answer, no TLS) or rejected the token (401) | Not kept. This says nothing about any page. |

- When the bridge answers 429, nothing more is sent to it until its `Retry-After` has passed. Lookups that would have been sent in that time come back `rate_limited`. Because a call's lookups go out together, the rest of the call that met the 429 has usually been sent already.

The circuit breaker opens for 30 seconds, during which lookups come back `error` at once without contacting the bridge, when any of these happens:

- Three failures in a row to reach the bridge at all: a connection error, a DNS failure, a TLS error, a connect timeout. Any HTTP response resets the count.
- One 401.
- Every lookup that one call sent (two or more) timed out or came back 5xx or unusable. In that case those lookups' 60-second cache entries are removed too, since the failure was the bridge's and not the pages'.

One page timing out or answering 5xx doesn't count towards the breaker. A slow page can't lock other pages out.

`LinkPreviews.aclose()` runs when the server stops, from the FastMCP lifespan in `main.py`. It cancels lookups still in flight, waits for them, and closes the connection pool.

The bridge's `thumbnailsStatus`, on thumbnail-mode responses:

| Value | Meaning |
|---|---|
| `complete` | The thumbnails in the response are all there will be. A page that failed for good (not HTML, a 4xx, blocked) is also reported this way, with nothing in it. |
| `transient` | A timeout, network failure or 5xx, fetching the page or its images. It may not happen again. |
| `busy` | The bridge was too busy to make the thumbnails this time. |
| `unavailable` | The bridge can't make thumbnails. |

The bridge caches rendered thumbnails on its own side. It doesn't cache page metadata. Its thumbnails are WebP or PNG.

### What the card does

- It asks only when the result carries `previews_available: true` and `context.canCallTools()` is true, and only about rows that carry a `preview_token`.
- It asks about the rows it has drawn, in calls of up to 8 items (`{ url, token }`), one call at a time, each URL once (`preview-loader.ts`). The first 8 rows go out as one call as soon as the card renders.
- Rows past the first 8 exist only after "Show all". Those are asked about as they come near the viewport. One `IntersectionObserver` watches all of them (`row-watcher.ts`), so rows that scroll into view together are asked about in one call. Where there is no `IntersectionObserver`, they are asked about straight away, in order.
- The row's source line always shows the host the link goes to, taken from the bookmark's URL. The host is never truncated: a long one wraps, so its last labels, which are the real site, are always on the row.
- The page's own site name is shown before the host ("Robin Sloan · robinsloan.com") only when `siteNameBeside` (`site-name.ts`) allows it. It is dropped when it repeats the host, and when it contains anything shaped like a host name, an IP address or a URL, unless that is the row's own host or a parent domain of it. So `paypal.com`, `paypal.com/`, `https://paypal.com/` and `paypal.com – Secure login` are never shown on another site's row. A few honest names shaped like hosts ("Node.js") are dropped too. This rule lives only in the card.
- The site name is limited to 45% of the line and ends in an ellipsis past that. In a list narrower than 26rem (a phone) it is left out.
- While a row's answer is on its way, the place for its thumbnail is held with a placeholder, so the text wraps once. A row that turns out to have no picture gives the place back.
- The bookmark's own title is kept. Your notes are kept, and the page's description shows only on a row with no notes.
- When previews change a row's height, the card calls `remeasure()` and the frame follows.

### When something goes wrong

The card never shows an error for previews. It falls back to the rows it would have drawn without them.

| What happened | What the card does |
|---|---|
| The server has no bridge configured | Doesn't ask. |
| The host doesn't proxy tool calls | Doesn't ask, and holds no placeholders. |
| The host or the tool refuses a call, or it times out | Stops asking for the life of the card. Rows keep any previews they already have. |
| A call made before the bridge connected had no way through | `callTool` first tries ChatGPT's `window.openai.callTool` if there is one. If that refuses, or there is none, the call waits for the bridge and goes down it when it connects. If no bridge connects within 15 seconds the call ends as `not-connected` or `rejected-before-connect`: the card settles to the plain rows but keeps its queue, and asks again, once, if the bridge connects later. A connect is remembered whenever it arrives, including while the first call is still in flight. A call that timed out while waiting is never sent late. |
| `pending` entries (the server was still looking the page up) | Keeps their placeholders and asks about them once more after 5 seconds. If they are still `pending`, those rows get no preview. |
| A page has no data, or the bridge couldn't read it | That row stays as the bookmark alone. |
| `rate_limited` entries | Keeps their placeholders, waits `retry_after_seconds`, and asks about them once more. If they are rate-limited again, it stops asking. |
| `retry: true` entries (bridge busy) | Draws what arrived, and asks about those URLs once more after 5 seconds. Whatever comes back the second time stands. |

The three second chances (`rate_limited`, `pending`, `retry: true`) are counted separately for each URL, so using one doesn't use up another.

Each of these leaves a breadcrumb in the console (`[pinboard-card] callTool:…`, `previews:…`).

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

`fixtures/previews.json` holds `get_link_previews` results for the `rich` and `rich-many` bookmark fixtures: all found, mixed outcomes, rate-limited, busy, and a hostile one (remote, `javascript:` and SVG image sources, markup in strings) that stands for a server that sanitised nothing. `tests/test_previews.py` checks the well-behaved ones against the real sanitiser. The images in it are real WebP and PNG files drawn by `npm run fixtures:previews`. Rerun that only to change what the fixtures say.

The harness (`scripts/card-host.ts`) answers the card's tool calls from those fixtures when a render passes `previews`. Without it, the harness is a host that doesn't proxy tool calls. `npm run capture` takes each paired scenario both ways, as `rich-…png` and `rich+ok-…png`.

CI runs all of the above except `capture`, in the `cards` job.

### Size

Each card is about 100 KiB gzipped, most of it React and the ext-apps SDK. The SDK's zod schemas are aliased to `zod/mini` through `src/zod-classic-shim.ts`, which saves about 70 KiB. A parity test checks the shim against real zod. `npm run check` fails at 125 KiB.

## Hosts

- **App-called tools** are what link previews depend on: the host has to advertise `serverTools` at the handshake and honour `_meta.ui.visibility`. This has been tested against the SDK's own `AppBridge`, not yet against a live host. Where a host doesn't support it, the card shows no previews.
- **Claude** (web, desktop, mobile) renders MCP Apps for connectors. In &Open's experience, a local stdio server added through `claude_desktop_config.json` didn't render cards, while the same server added as a connector did. If cards don't appear, try it as a connector.
- **ChatGPT** apps render the same documents. The shell's `window.openai` fast path is for ChatGPT.
- **Other clients** (Cursor, Claude Code and so on) ignore `_meta.ui` and show the plain result.
