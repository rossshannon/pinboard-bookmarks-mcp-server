"""``pinboard-mcp-index``: manage and query the local bookmark index by hand.

The MCP server keeps the index in step on its own. This command is for
seeding it from an export file, running a sync from a scheduler, and checking
from a terminal what a search returns.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

from pinboard_mcp_server.embeddings import default_embedder, embed_pending
from pinboard_mcp_server.index import BookmarkIndex, default_index_path
from pinboard_mcp_server.service import IndexService
from pinboard_mcp_server.sync import PinboardSync, import_export, sync_status


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pinboard-mcp-index",
        description="Manage the local search index of your Pinboard bookmarks.",
    )
    parser.add_argument(
        "--index",
        metavar="PATH",
        help="Index file (default: PINBOARD_INDEX_PATH or the user data directory)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Log progress")
    commands = parser.add_subparsers(dest="command", required=True)

    sync = commands.add_parser(
        "sync", help="Sync the index from Pinboard (needs PINBOARD_TOKEN)"
    )
    sync.add_argument("--full", action="store_true", help="Re-read the whole account")
    sync.add_argument(
        "--no-wait",
        action="store_true",
        help="Stop instead of waiting out Pinboard's five-minute posts/all interval",
    )
    sync.add_argument(
        "--no-embed", action="store_true", help="Skip embedding afterwards"
    )

    load = commands.add_parser(
        "import", help="Load a Pinboard JSON export (pinboard.in/export/format:json/)"
    )
    load.add_argument("file")

    commands.add_parser("status", help="Show the index's size and sync state")
    commands.add_parser("embed", help="Embed bookmarks for semantic search")

    search = commands.add_parser("search", help="Search the index")
    search.add_argument("query", nargs="*")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument(
        "--mode", default="auto", choices=["auto", "keyword", "semantic", "hybrid"]
    )
    search.add_argument("--author")
    search.add_argument("--domain")
    search.add_argument("--source")
    search.add_argument("--tag", action="append", default=[])
    search.add_argument("--from", dest="from_date", metavar="DATE")
    search.add_argument("--to", dest="to_date", metavar="DATE")
    search.add_argument(
        "--posted", action="store_true", help="Filter on the posted date"
    )
    search.add_argument("--json", action="store_true", help="Print raw JSON")
    return parser


def _print(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _embed(index: BookmarkIndex) -> None:
    embedder = default_embedder()
    if embedder is None:
        print(
            'Semantic search is not installed. To add it: pip install "pinboard-bookmarks-mcp-server[semantic]"',
            file=sys.stderr,
        )
        return
    done = embed_pending(index, embedder)
    print(f"Embedded {done} bookmarks with {embedder.model_id}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s"
    )
    index = BookmarkIndex(args.index or default_index_path())

    if args.command == "status":
        _print(sync_status(index))
        return 0

    if args.command == "import":
        delta = import_export(index, args.file)
        print(
            f"Imported {args.file}: {delta.added} added, {delta.updated} updated, "
            f"{delta.unchanged} unchanged. The index now holds {index.count()} bookmarks."
        )
        return 0

    if args.command == "embed":
        _embed(index)
        return 0

    if args.command == "sync":
        token = os.getenv("PINBOARD_TOKEN")
        if not token:
            print(
                "Error: PINBOARD_TOKEN environment variable is required",
                file=sys.stderr,
            )
            return 1
        import pinboard  # type: ignore

        syncer = PinboardSync(
            pinboard.Pinboard(token), index, wait_for_windows=not args.no_wait
        )
        report = syncer.sync(full=args.full)
        _print(report.as_dict())
        if report.error:
            return 1
        if report.stopped:
            print(
                "Stopped before the sync finished; run it again in five minutes to continue.",
                file=sys.stderr,
            )
        if not args.no_embed:
            _embed(index)
        return 0

    # search
    service = IndexService(index, embedder=default_embedder())
    filters = service.filters(
        from_date=args.from_date,
        to_date=args.to_date,
        date_field="posted" if args.posted else "saved",
        tags=args.tag,
        author=args.author,
        domain=args.domain,
        source=args.source,
    )
    warm_up = getattr(service.embedder, "warm_up", None)
    if warm_up is not None and args.mode != "keyword":
        try:
            warm_up()
        except Exception as error:
            print(
                f"Embedding model unavailable ({type(error).__name__}); keyword only",
                file=sys.stderr,
            )
    outcome = index.search(
        " ".join(args.query),
        filters,
        mode=args.mode,
        limit=args.limit,
        embedder=service.embedder,
    )
    if args.json:
        _print({"mode": outcome.mode, "bookmarks": outcome.bookmarks})
        return 0
    if outcome.semantic_note and args.mode != "keyword":
        print(f"({outcome.semantic_note})", file=sys.stderr)
    for bookmark in outcome.bookmarks:
        when = (bookmark.get("saved_at") or "")[:10]
        who = f" @{bookmark['author_handle']}" if bookmark.get("author_handle") else ""
        print(f"{when}{who}  {bookmark['title'][:100]}")
        print(f"            {bookmark['url']}")
    if not outcome.bookmarks:
        print("No matches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
