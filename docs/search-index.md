# The local search index

Pinboard is where bookmarks are saved. The index is where questions are asked.

Pinboard's API is the Delicious-era one: it has no search endpoint, no field
for an author or a source, and every call waits behind a three-second spacing
rule. So the server mirrors the account into one SQLite file and runs every
query there. The file is a cache: delete it and the next start rebuilds it.

```
Pinboard ──posts/update, posts/dates, posts/all──▶ sync.py ──▶ SQLite file ◀── MCP tools
                                                              (index.py)   ◀── pinboard-mcp-index
                                             embeddings.py ──▶ vectors
```

| Module | What it does |
|---|---|
| `enrich.py` | Reads domain, source, tweet author and posted date out of a bookmark's own fields |
| `index.py` | The SQLite file: schema, writes, full-text and semantic search, filters, counts |
| `sync.py` | Keeps the file in step with Pinboard; the background worker |
| `embeddings.py` | The optional local embedding model |
| `service.py` | Argument parsing and result shaping for the MCP tools |
| `cli.py` | The `pinboard-mcp-index` command |

## What is stored

One row per bookmark: Pinboard's fields (URL, title, notes, tags, save time,
private and to-read flags, change signature) and the fields derived from them.

| Derived field | Where it comes from |
|---|---|
| `domain` | The URL's host, `www.` dropped, `twitter.com` folded into `x.com` |
| `source` | A `via:` tag: `via:twitter-like` → `twitter-like`, `via:twitter-bookmark` → `twitter-bookmark`. Any tweet URL without one is `twitter`; everything else is `bookmark`. Legacy `.from:twitter`, `twitter-fav` and `!fromtwitter` on a tweet count as likes |
| `author_handle` | The tweet URL: `x.com/<handle>/status/<id>` |
| `author_name` | A title shaped `Full Name (@handle): text` (what the Twitter backfill writes) or `Full Name on X: "text"` (a browser's page title) |
| `posted_at` | The tweet id. Ids since late 2010 are "snowflakes" whose top bits are a millisecond timestamp, so the date a tweet was written is in its URL. A `Posted: <ISO date>` line in the notes overrides it |

So a bookmark has two dates: `saved_at`, when you saved or liked it, and
`posted_at`, when it was written. Tools filter on either (`date_field`).

Derivation is a pure function of the stored fields. When the rules improve,
`DERIVED_VERSION` in `index.py` is bumped and every row is re-derived the next
time the file is opened; nothing is fetched again.

## Search

**Keyword.** An FTS5 index covers title, notes, tags, URL and author, with the
`porter unicode61 remove_diacritics 2` tokenizer: "tools" finds "tool", "creme"
finds "crème". Results are ranked by BM25 with the author and title weighted
above notes and the URL. A query is first run requiring every word; if that
does not fill the page, bookmarks matching any word follow. That is what lets
an LLM pass a whole question and still get the right thing at the top. Each
result says which it was (`matched_terms`: `all` or `some`).

Everything a caller types is quoted before it reaches FTS5, so `AND`, `*`, `:`
and stray quotes are just text.

**Semantic.** With the `semantic` extra installed, each bookmark's title,
notes, author and topical tags are embedded by a local model
(`BAAI/bge-small-en-v1.5` through fastembed: ONNX, no API key, nothing leaves
the machine). A query is embedded the same way and compared with every
bookmark by cosine similarity.

The comparison is a brute-force numpy dot product, not a vector extension.
Fifty thousand 384-dimension vectors is 77 MB and a few milliseconds to scan,
and it avoids SQLite's loadable-extension support, which the Python that ships
with macOS is built without.

**Hybrid** (the default when embeddings exist) runs both and fuses the two
rankings by reciprocal rank. A bookmark containing every query word counts in
full; a partial keyword match counts for half. Each result lists how it was
found (`matched_by`).

Search never waits for the model: while it is loading, or before anything has
been embedded, results are keyword-only and `semantic_note` says why.

## Sync

One sync is:

1. **`posts/update`**: one cheap call that says when the account last changed.
   Unchanged since last time: stop.
2. **`posts/dates`**: one cheap call returning the number of bookmarks on every
   day of the account's history. Compared with the counts recorded at the last
   sync, it names exactly the days that gained or lost a bookmark, anywhere in
   history. This is why a backfill that saves old tweets under their original
   dates is picked up: a "what is new since my newest bookmark" check would
   never see them.
3. **`posts/all` with `fromdt` and `todt`** for each run of changed days (with a
   day's margin either side), and that range of the index is made to match:
   added, updated, and removed if Pinboard no longer has it.

The first sync is the same algorithm with nothing recorded: every day is
"changed", and the account is read in windows of at most 10,000 bookmarks,
newest first. `posts/all` is never called without both date bounds.

Pinboard asks for `posts/all` at most once every five minutes. A sync needing
several windows waits between them; the time of the last call is kept in the
file, so a restart or the CLI honours it too. Progress is recorded after each
window, so a sync that is interrupted resumes rather than restarts. An HTTP 429
backs off (5, 10, 20 minutes) and then gives up until the next scheduled check.

**Edits.** Editing a bookmark changes `posts/update` but no day's count, so the
edit cannot be located. An account that fits in one window is simply re-read
(one call). For a larger one the last 30 days are re-read, and a full pass is
scheduled, at most once a week. `pinboard-mcp-index sync --full` forces one.

The MCP server runs this in a daemon thread: once at start, then every five
minutes (`PINBOARD_INDEX_SYNC_INTERVAL`), then embeds anything new.
`get_index_status(refresh=true)` asks for a pass now.

### Seeding from an export

`pinboard-mcp-index import <file>` loads Pinboard's JSON export
(pinboard.in/export/format:json/) with no API calls. The next sync compares
Pinboard's day counts with the file's and fetches only the days that differ.

## Privacy

The file contains every bookmark, private ones included. It is created with
mode 600 in a directory with mode 700. The Pinboard token is never written to
it, and errors recorded in it (shown by `get_index_status`) carry an HTTP
status, never a URL.

## What has not been verified

Written without access to a Pinboard account, against a fake of the API that
returns what the `pinboard` library returns. Worth checking on the first real
run:

- that `posts/dates` reports days in UTC. If it does not, the sync still
  converges (every window carries a day's margin), it just fetches a little
  more than it needs to on the first comparison after an import;
- that Pinboard enforces the five-minute `posts/all` limit with HTTP 429. The
  sync assumes the documented limit and backs off on a 429 either way;
- the embedding model itself. The vector code is tested with a stand-in model,
  because the build environment cannot download the real one. Ranking quality
  with `bge-small-en-v1.5` on real bookmarks is untested.
