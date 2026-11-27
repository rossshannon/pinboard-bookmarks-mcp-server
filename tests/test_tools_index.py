"""The MCP tools answering from the local index, end to end through an MCP client."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from pinboard_mcp_server import main
from pinboard_mcp_server.embeddings import embed_pending
from pinboard_mcp_server.index import BookmarkIndex, Post
from pinboard_mcp_server.service import IndexService, index_enabled, parse_bound
from pinboard_mcp_server.sync import import_export

from .fakes import EXPORT, FakeEmbedder, FakePinboard, export_posts, post

#: The keys the bookmarks card reads from a result and from each row.
CARD_RESULT_KEYS = {"bookmarks", "total"}
CARD_ROW_KEYS = {"id", "url", "title", "tags", "notes", "saved_at"}


@pytest.fixture
def service(tmp_path) -> IndexService:
    index = BookmarkIndex(tmp_path / "index.sqlite3")
    import_export(index, EXPORT)
    index.set_meta("last_synced_at", "2026-11-27T12:00:00Z")
    return IndexService(index)


@pytest.fixture
async def mcp(service, mock_client, monkeypatch):
    """An MCP client against the real server, the index loaded from the export fixture."""
    monkeypatch.setattr(main, "client", mock_client, raising=False)
    monkeypatch.setattr(main, "index_service", service)
    async with Client(main.mcp) as client:
        yield client


async def call(mcp, name, **arguments):
    return (await mcp.call_tool(name, arguments)).structured_content


class TestSearchBookmarks:
    async def test_the_question_from_the_audit(self, mcp):
        """ "What was that Patrick Collison tweet last week about building a feedback tool?"."""
        result = await call(
            mcp,
            "search_bookmarks",
            query="feedback tool",
            author="patrickc",
            from_date="2026-11-19",
        )

        assert result["total"] == 1
        tweet = result["bookmarks"][0]
        assert tweet["author_handle"] == "patrickc"
        assert tweet["author_name"] == "Patrick Collison"
        assert tweet["source"] == "twitter-like"
        assert tweet["posted_at"] == "2026-11-19T16:20:00Z"
        assert "feedback" in tweet["notes"]
        assert result["filters"] == {
            "from": "2026-11-19T00:00:00Z",
            "date_field": "saved",
            "author": "patrickc",
        }

    async def test_the_result_has_everything_the_card_reads(self, mcp):
        result = await call(mcp, "search_bookmarks", query="sqlite")

        assert CARD_RESULT_KEYS <= set(result)
        assert result["query"] == "sqlite"
        assert all(CARD_ROW_KEYS <= set(row) for row in result["bookmarks"])
        assert result["search_mode"] == "keyword"
        assert result["index"] == {
            "bookmarks": 23,
            "last_synced_at": "2026-11-27T12:00:00Z",
            "complete": True,
        }

    async def test_the_mock_client_is_never_asked(self, mcp, mock_client):
        await call(mcp, "search_bookmarks", query="sqlite")
        await call(mcp, "list_recent_bookmarks", days=30)
        await call(mcp, "list_bookmarks_by_tags", tags=["sqlite"])
        await call(mcp, "list_tags")

        mock_client.search_bookmarks.assert_not_called()
        mock_client.get_recent_bookmarks.assert_not_called()
        mock_client.get_bookmarks_by_tags.assert_not_called()
        mock_client.get_all_tags.assert_not_called()

    async def test_an_empty_query_lists_by_filter(self, mcp):
        result = await call(mcp, "search_bookmarks", source="twitter-bookmark")

        assert result["search_mode"] == "listing"
        assert [b["author_handle"] for b in result["bookmarks"]] == [
            "simonw",
            "simonw",
            "patrickc",
        ]

    async def test_a_bare_to_date_includes_that_whole_day(self, mcp):
        result = await call(
            mcp, "search_bookmarks", from_date="2026-11-20", to_date="2026-11-20"
        )

        assert [b["saved_at"] for b in result["bookmarks"]] == ["2026-11-20T08:05:11Z"]

    async def test_posted_date_filter(self, mcp):
        result = await call(
            mcp,
            "search_bookmarks",
            author="patrickc",
            from_date="2026-11-19",
            to_date="2026-11-19",
            date_field="posted",
        )

        assert result["total"] == 1

    async def test_domain_and_tags(self, mcp):
        result = await call(
            mcp, "search_bookmarks", domain="github.com", tags=["sqlite"]
        )

        assert [b["url"] for b in result["bookmarks"]] == [
            "https://github.com/asg017/sqlite-vec"
        ]

    async def test_paging_with_offset(self, mcp):
        first = await call(mcp, "search_bookmarks", limit=5)
        second = await call(mcp, "search_bookmarks", limit=5, offset=5)

        assert first["has_more"] is True
        assert not {b["id"] for b in first["bookmarks"]} & {
            b["id"] for b in second["bookmarks"]
        }

    async def test_hybrid_when_embeddings_exist(self, mcp, service):
        service.embedder = FakeEmbedder()
        embed_pending(service.index, service.embedder)

        result = await call(mcp, "search_bookmarks", query="customer complaints")

        assert result["search_mode"] == "hybrid"
        assert any(b["matched_by"] == ["semantic"] for b in result["bookmarks"])

    async def test_without_embeddings_it_says_so(self, mcp):
        result = await call(mcp, "search_bookmarks", query="x", mode="semantic")

        assert result["search_mode"] == "keyword"
        assert "semantic" in result["semantic_note"]

    @pytest.mark.parametrize(
        "arguments",
        [
            {"query": "x", "limit": 0},
            {"query": "x", "limit": 101},
            {"query": "x", "offset": -1},
            {"query": "x", "mode": "fuzzy"},
            {"query": "x", "date_field": "published"},
            {"query": "x", "from_date": "last week"},
        ],
    )
    async def test_bad_arguments_are_refused(self, mcp, arguments):
        with pytest.raises(ToolError):
            await mcp.call_tool("search_bookmarks", arguments)


class TestOtherTools:
    async def test_extended_search_keeps_its_shape(self, mcp, service):
        now = datetime.now(timezone.utc)
        service.index.upsert(
            Post.from_pinboard(
                post(
                    f"https://example.com/{days}",
                    f"Quokka sighting {days}",
                    time=(now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
            )
            for days in (3, 300, 900)
        )

        result = await call(
            mcp, "search_bookmarks_extended", query="quokka", days_back=365
        )

        assert {"bookmarks", "total", "query", "days_back"} <= set(result)
        assert "filters" not in result
        assert [b["title"] for b in result["bookmarks"]] == [
            "Quokka sighting 3",
            "Quokka sighting 300",
        ]

    async def test_recent_reaches_past_one_hundred(self, mcp, service):
        now = datetime.now(timezone.utc)
        service.index.upsert(
            Post.from_pinboard(
                post(
                    f"https://example.com/{n}",
                    time=(now - timedelta(minutes=n)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
            )
            for n in range(150)
        )

        result = await call(mcp, "list_recent_bookmarks", days=1, limit=200)

        assert result["total"] == 150
        assert result["days"] == 1
        assert result["has_more"] is False

    async def test_several_tags_reach_back_through_history(self, mcp):
        result = await call(mcp, "list_bookmarks_by_tags", tags=["python", "sqlite"])

        assert [b["saved_at"][:4] for b in result["bookmarks"]] == ["2021"]
        assert result["tags"] == ["python", "sqlite"]
        assert result["from_date"] is None and result["to_date"] is None

    async def test_tags_with_dates(self, mcp):
        result = await call(
            mcp,
            "list_bookmarks_by_tags",
            tags=["sqlite"],
            from_date="2026-11-01",
            to_date="2026-11-30",
        )

        assert result["total"] == 2

    async def test_list_tags_comes_from_the_index(self, mcp):
        result = (await mcp.call_tool("list_tags", {})).structured_content["result"]

        assert {"tag": "sqlite", "count": 4} in result

    async def test_list_tags_asks_pinboard_until_the_first_sync_finishes(
        self, mcp, service, mock_client
    ):
        service.index.set_meta("last_synced_at", None)

        await mcp.call_tool("list_tags", {})

        mock_client.get_all_tags.assert_called_once()

    async def test_count_by_author(self, mcp):
        result = await call(mcp, "count_bookmarks", group_by="author", limit=1)

        assert result["groups"] == [
            {"author": "patrickc", "count": 3, "name": "Patrick Collison"}
        ]

    async def test_count_by_month_with_filters(self, mcp):
        result = await call(
            mcp,
            "count_bookmarks",
            group_by="month",
            source="twitter",
            from_date="2026-11-01",
        )

        assert result["groups"] == [{"month": "2026-11", "count": 4}]
        assert result["filters"]["source"] == "twitter"

    async def test_count_refuses_an_unknown_grouping(self, mcp):
        with pytest.raises(ToolError, match="group_by"):
            await mcp.call_tool("count_bookmarks", {"group_by": "colour"})

    async def test_status(self, mcp):
        result = await call(mcp, "get_index_status")

        assert result["enabled"] is True
        assert result["bookmarks"] == 23
        assert result["tweets"] == 11
        assert result["complete"] is True
        assert result["semantic_search"]["available"] is False
        assert result["syncing_from_pinboard"] is False

    async def test_status_with_a_model(self, mcp, service):
        service.embedder = FakeEmbedder()

        result = await call(mcp, "get_index_status", refresh=True)

        assert result["refresh_requested"] is True
        assert result["semantic_search"] == {
            "available": True,
            "model": "fake:bag-of-words",
            "model_loaded": True,
            "bookmarks_waiting_to_be_embedded": 23,
        }

    async def test_an_unfinished_first_sync_is_flagged_on_results(self, mcp, service):
        service.index.set_meta("last_synced_at", None)

        result = await call(mcp, "search_bookmarks", query="sqlite")

        assert result["index"]["complete"] is False
        assert "still running" in result["index"]["note"]


class TestWithoutTheIndex:
    """Switched off, or empty: the tools ask Pinboard, as they did before."""

    @pytest.fixture
    async def live(self, mock_client, monkeypatch, tmp_path):
        mock_client.search_bookmarks = AsyncMock(return_value=[])
        monkeypatch.setattr(main, "client", mock_client, raising=False)
        monkeypatch.setattr(
            main, "index_service", IndexService(BookmarkIndex(tmp_path / "empty.db"))
        )
        async with Client(main.mcp) as client:
            yield client

    async def test_an_empty_index_falls_back_to_pinboard(self, live, mock_client):
        result = await call(
            live, "search_bookmarks", query="python", from_date="2026-01-01"
        )

        assert set(result) == {"bookmarks", "total", "query"}
        kwargs = mock_client.search_bookmarks.call_args.kwargs
        assert kwargs["query"] == "python"
        assert kwargs["from_date"] == datetime(2026, 1, 1)

    async def test_a_bare_to_date_covers_its_day_there_too(self, live, mock_client):
        await call(live, "search_bookmarks", query="python", to_date="2026-01-31")

        assert mock_client.search_bookmarks.call_args.kwargs["to_date"] == datetime(
            2026, 1, 31, 23, 59, 59
        )

    @pytest.mark.parametrize(
        "arguments",
        [
            {"query": "x", "author": "patrickc"},
            {"query": "x", "offset": 5},
            {"query": ""},
        ],
    )
    async def test_index_only_arguments_say_what_is_missing(self, live, arguments):
        with pytest.raises(ToolError, match="index"):
            await live.call_tool("search_bookmarks", arguments)

    async def test_count_needs_the_index(self, live):
        with pytest.raises(ToolError, match="index"):
            await live.call_tool("count_bookmarks", {})

    async def test_status_when_switched_off(self, mock_client, monkeypatch):
        monkeypatch.setattr(main, "client", mock_client, raising=False)
        monkeypatch.setattr(main, "index_service", None)
        async with Client(main.mcp) as client:
            result = await call(client, "get_index_status")

        assert result["enabled"] is False


class TestConfiguration:
    @pytest.mark.parametrize("value", ["off", "OFF", "false", "0", "no"])
    def test_switched_off(self, value):
        assert index_enabled({"PINBOARD_INDEX": value}) is False

    @pytest.mark.parametrize("value", ["", "on", "true"])
    def test_on_by_default(self, value):
        assert index_enabled({"PINBOARD_INDEX": value}) is True
        assert index_enabled({}) is True

    def test_from_environment(self, tmp_path):
        service = IndexService.from_environment(
            environ={
                "PINBOARD_INDEX_PATH": str(tmp_path / "x" / "i.db"),
                "PINBOARD_INDEX_SYNC_INTERVAL": "5",
                "PINBOARD_SEMANTIC": "off",
            }
        )

        assert service.index.path == tmp_path / "x" / "i.db"
        assert service.syncer is None and service.embedder is None
        assert service.worker.interval == 60.0
        assert service.ready() is False

    def test_open_index_starts_a_worker_and_survives_a_bad_path(
        self, tmp_path, monkeypatch
    ):
        token_client = type(
            "C",
            (),
            {
                "pinboard": FakePinboard(export_posts()),
                "_rate_limit_sync": lambda self: None,
            },
        )()
        monkeypatch.setenv("PINBOARD_INDEX_PATH", str(tmp_path / "i.db"))
        monkeypatch.setenv("PINBOARD_SEMANTIC", "off")
        service = main._open_index(token_client)
        try:
            assert service is not None
            for _ in range(200):
                if service.ready() and service.index.get_meta("last_synced_at"):
                    break
                service.worker.join(0.05)
            assert service.index.count() == 23
        finally:
            service.stop()
            service.worker.join(5)

        blocker = tmp_path / "file"
        blocker.write_text("")
        monkeypatch.setenv("PINBOARD_INDEX_PATH", str(blocker / "nested" / "i.db"))
        assert main._open_index(token_client) is None

        monkeypatch.setenv("PINBOARD_INDEX", "off")
        assert main._open_index(token_client) is None


class TestParseBound:
    def test_bounds(self):
        assert parse_bound("2026-11-20", "from_date") == "2026-11-20T00:00:00Z"
        assert parse_bound("2026-11-20", "to_date", end=True) == "2026-11-21T00:00:00Z"
        assert (
            parse_bound("2026-11-20T10:00:00Z", "to_date", end=True)
            == "2026-11-20T10:00:01Z"
        )
        assert parse_bound(None, "x") is None and parse_bound("  ", "x") is None

    def test_a_bad_date_names_the_argument(self):
        with pytest.raises(ValueError, match="to_date must be in ISO format"):
            parse_bound("soon", "to_date")
