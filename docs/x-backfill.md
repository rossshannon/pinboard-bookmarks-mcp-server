# Backfilling X likes and bookmarks into Pinboard

`pinboard-x-backfill` copies the tweets you have liked or bookmarked on X into
Pinboard, and can be run again and again without duplicating or overwriting
anything. It is a separate package (`src/pinboard_x_backfill/`) from the MCP
server, which stays read-only. It is the only code in this repository that
writes to Pinboard, and it only writes when you pass `--write`.

It lives in this repository, not in pinboard-bridge or Pincushion, because it
is a Python command-line job that runs on your own machine with your own
credentials, it feeds the search index being built here, and the bridge is a
public Heroku proxy that should not hold an X sign-in.

## What it does

```
X API (likes, bookmarks) ──fetch──┐
                                  ├──► snapshot (tweets.json) ──sync──► Pinboard
X data archive (like.js) ──import─┘
```

1. **`fetch`** reads your likes and bookmarks from the X API into a local
   snapshot. Reading costs money per tweet (see [Cost](#cost)), so the
   snapshot is kept, an interrupted backfill carries on from the page it
   reached, and once the backfill is complete a fetch reads small pages and
   stops at the first one holding a tweet it already has.
2. **`import-archive`** adds the likes from an X data archive. The archive
   holds every like the account still has, which the API may not page back to.
3. **`sync`** compares the snapshot with Pinboard and prints a plan. It is a
   dry run unless you add `--write`.

## What gets saved

For each tweet not already in Pinboard:

| Pinboard field | Value |
| --- | --- |
| URL | `https://x.com/<handle>/status/<id>` (or `https://x.com/i/web/status/<id>` when the author is unknown) |
| Title | `Full Name (@handle): tweet text` on one line, cut to 255 characters (fewer if it is mostly emoji) |
| Notes | The full text with `t.co` links expanded, a blank line, then `Author: Full Name (@handle)` and `Posted: 2026-11-20T14:03:00Z` |
| Tags | `via:twitter-like` and/or `via:twitter-bookmark` (both if you did both), plus any `--tag` |
| Date | When the tweet was posted. X does not say when you liked it. |
| Privacy | Private, unless `--shared` |

The author and the `Posted:` line are there so the search index can find a
tweet by who wrote it and when.

Notes are cut so that they fit in a request: Pinboard's API takes everything
in the URL, so a very long post keeps roughly its first 1,500 to 4,000
characters (fewer for non-Latin scripts and emoji).

## Why re-running is safe

- Before planning, every existing bookmark whose URL is a tweet is indexed by
  **tweet id**. `twitter.com` and `x.com`, `www.` and `mobile.`, a handle or
  `/i/web/status/`, tracking parameters, `/photo/1` suffixes and 2011-style
  `#!/` addresses all resolve to the same id, so a tweet IFTTT saved years ago as
  `https://twitter.com/user/status/1?s=20` is recognised and skipped.
- Every new save is sent with `replace=no`. If the index missed something,
  Pinboard itself refuses to overwrite a bookmark with the same URL.
- Existing bookmarks are never rewritten. The one exception is opt-in:
  `sync --tag-existing` adds a missing `via:twitter-…` tag to tweets already
  in Pinboard. It re-reads each bookmark from Pinboard immediately before
  resending its own title, notes, tags, date and flags. A bookmark whose
  notes are too long to resend in one request, or that you have since
  deleted, is listed as "left alone" and not touched.
- Correctness does not depend on local state. Delete the data folder and the
  next `sync` reaches the same plan from Pinboard alone.

### The local copy of your tweet bookmarks

Pinboard allows its "all bookmarks" call once every five minutes, so `sync`
downloads the full list only the first time (5,000 bookmarks a page; an
account of 20,000 takes about a quarter of an hour). After that it keeps a
local copy current by recording its own saves and asking Pinboard only for
bookmarks dated since the day before the previous run. Two consequences:

- A tweet bookmark you delete on Pinboard stays in the local copy, so the
  tweet is **not** saved again. That is usually what you want.
- `sync --refresh` throws the copy away and downloads the list again.

## Setup

### 1. Install

```bash
cd pinboard-bookmarks-mcp-server
uv sync            # or: pip install -e .
uv run pinboard-x-backfill --help
```

### 2. Pinboard token

Copy the token from <https://pinboard.in/settings/password> and export it:

```bash
export PINBOARD_TOKEN='username:HEXTOKEN'
```

### 3. An X developer app (only for `fetch`)

The archive route needs none of this. For the API:

1. Sign in at <https://console.x.com> with the X account whose likes you
   want, and create a project and an app. Owning the app with the same
   account is what makes the reads "owned reads", the cheap rate.
2. In the app's **User authentication settings**, turn on OAuth 2.0 with
   type **Native App** (a public client, no secret) and **Read** permission.
   Set the callback URL to `http://127.0.0.1:8739/callback`. Any website URL
   will do.
3. Add credits. The API is pay-per-use with no free allowance.
4. Export the app's OAuth 2.0 **Client ID**, then sign in:

```bash
export X_CLIENT_ID='…'
uv run pinboard-x-backfill auth
```

`auth` opens X in your browser, asks for read-only access (`tweet.read
users.read like.read bookmark.read offline.access`) and listens on
`127.0.0.1:8739` for the redirect. `auth --paste` lets you paste the
redirected address instead. If you chose a confidential client type, also
export `X_CLIENT_SECRET`. Tokens are kept in the data folder, readable only
by you, and refreshed automatically.

### 4. The data archive (optional, recommended for likes)

On X: **Settings → Your account → Download an archive of your data**. X
emails a link, usually within a day. Then:

```bash
uv run pinboard-x-backfill import-archive ~/Downloads/twitter-2026-….zip
```

Archive likes carry text but no author. Two ways to fill authors in, both
**before** the first `sync --write` (a saved bookmark is never rewritten):

- `fetch` pages the API all the way back; anything it returns replaces the
  archive copy with the full record.
- `enrich` asks X's free public oEmbed endpoint for each author, one a
  second, saving as it goes. Experimental: it has not been run against the
  live service yet. Try `enrich --limit 20` first.

## Running it

```bash
# 1. Try one page (100 tweets) of each and look at the bill in the console.
uv run pinboard-x-backfill fetch --max-pages 1

# 2. Dry run. Prints the plan and a sample; sends nothing.
uv run pinboard-x-backfill sync --plan-out plan.json

# 3. Save ten, check them on pinboard.in, then save the rest.
uv run pinboard-x-backfill sync --write --limit 10
uv run pinboard-x-backfill fetch          # carries on from page 2 to the end
uv run pinboard-x-backfill sync --write
```

Pinboard asks for three seconds between calls, so a large backfill is slow
by design: about 1,200 tweets an hour. It can be stopped with Ctrl-C and
started again at any point.

A fully offline dry run needs no token: download
<https://pinboard.in/export/format:json/> and pass it with
`sync --pinboard-export pinboard_export.json`.

### Keeping up afterwards

Once the backfill is done, the same two commands replace the IFTTT applet.
Run them on a schedule (cron or launchd), for example hourly:

```bash
pinboard-x-backfill fetch && pinboard-x-backfill sync --write
```

Once the backfill is complete, each run reads the newest 20 likes and 20
bookmarks, and more only when all 20 are new. That is 4 cents a run at the
owned-read rate. X's pricing page says a tweet read twice in one UTC day is
charged once, which would make an hourly schedule cost little more than a
daily one, but it calls that a "soft guarantee", so check the console after
the first day.

### Other commands and options

| | |
| --- | --- |
| `status` | What is in the data folder, and which credentials are set |
| `--data-dir DIR` | Default `~/.local/share/pinboard-x-backfill` (honours `XDG_DATA_HOME`) |
| `fetch --only like` / `--only bookmark` | One source only (also on `sync`) |
| `fetch --full` | Start the backfill again from the newest tweet |
| `sync --refresh` | Download the whole bookmark list again |
| `sync --url-host twitter.com` | Save `twitter.com` URLs instead of `x.com` |
| `sync --tag NAME` | Extra tag on new bookmarks (repeatable) |
| `sync --show N` | How many sample actions to print |

## Cost

From X's pricing page on 2026-11-27: reading your own likes and bookmarks
through an app you own is an "owned read" at **$0.001 per tweet**, so 10,000
likes cost about $10. Two things were not clear from the page and are worth
checking on the first small fetch: whether the author records that come back
with each page are billed separately (the general "user read" rate is $0.010),
and whether there is any ceiling on how far back the bookmarks endpoint
pages. The data archive is free.

## Limits and known unknowns

- Nothing here has been run against the live X API or the live Pinboard API:
  it was built against fakes and the published documentation, without
  credentials. Expect to find at least one wrinkle on the first real fetch.
- X's reference calls the field parameter `post.fields` while its bookmarks
  guide uses `tweet.fields`. The client tries `tweet.fields` and switches by
  itself if X rejects it.
- The shape of `like.js` is from long-standing community knowledge of the
  archive format, not from X documentation. A `bookmark.js` is read if the
  archive has one, but X does not document bookmarks as part of the archive.
- A tweet you later unlike or un-bookmark stays in Pinboard.
- Quoted tweets and images are not captured, only the text.
- A tweet first saved as a like and bookmarked later keeps only its first
  tag unless you run `sync --write --tag-existing`.

## Tests

```bash
uv run pytest tests/x_backfill
```

The tests use in-memory stand-ins for both services
(`tests/x_backfill/conftest.py`) and fixtures in `tests/x_backfill/fixtures/`.
No test touches the network.
