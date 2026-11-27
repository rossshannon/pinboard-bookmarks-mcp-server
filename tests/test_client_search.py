"""The live client's search, used when the index is off: word matching, dates, freshness."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from pinboard_mcp_server.client import PinboardClient, matches_terms, query_terms
from pinboard_mcp_server.models import Bookmark

from .fakes import as_pinboard_object, post


def bookmark(
    url="https://example.com/",
    title="",
    notes="",
    tags=(),
    saved="2026-01-01T00:00:00Z",
):
    return Bookmark.from_pinboard(
        {
            "href": url,
            "description": title,
            "extended": notes,
            "tags": " ".join(tags),
            "time": saved,
        }
    )


class TestMatching:
    def test_terms(self):
        assert query_terms('Feedback  "Internal Tool" feedback') == [
            "feedback",
            "internal tool",
        ]

    def test_every_word_must_appear_in_any_order_and_any_field(self):
        tweet = bookmark(
            "https://x.com/patrickc/status/1",
            "We built a tool for feedback",
            tags=["via:twitter-like"],
        )

        assert matches_terms(tweet, query_terms("feedback tool"))
        assert matches_terms(tweet, query_terms("patrickc feedback"))
        assert matches_terms(tweet, query_terms("twitter-like tool"))
        assert not matches_terms(tweet, query_terms("feedback survey"))
        assert not matches_terms(tweet, query_terms('"feedback tool"'))

    def test_an_empty_query_matches_everything(self):
        assert matches_terms(bookmark(), [])


class TestModel:
    def test_the_id_is_stable_and_is_pinboards(self):
        assert bookmark().id == bookmark().id == "182ccedb33a9e03fbf1079b209da1a31"

    def test_flags_and_a_missing_time(self):
        made = Bookmark.from_pinboard(
            {
                "href": "https://a/",
                "description": "",
                "extended": "",
                "tags": "",
                "time": None,
                "shared": "no",
                "toread": "yes",
            }
        )

        assert made.saved_at is None
        assert made.private is True and made.to_read is True


def _account(posts):
    """A mock ``pinboard.Pinboard`` over raw posts, newest first."""
    objects = sorted(
        (as_pinboard_object(p) for p in posts), key=lambda b: b.time, reverse=True
    )
    pb = Mock()
    pb.posts.update.return_value = datetime(2026, 11, 26, 9, 0, 0)
    pb.posts.recent.return_value = {"posts": objects[:100]}

    def all_(**params):
        since = params.get("fromdt")
        tags = params.get("tag", "").split()
        return [
            b
            for b in objects
            if (not since or b.time.strftime("%Y-%m-%dT%H:%M:%SZ") >= since)
            and all(tag in b.tags for tag in tags)
        ]

    pb.posts.all.side_effect = all_
    pb.tags.get.return_value = [SimpleNamespace(name="python", count=2)]
    return pb


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


def _stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def client_for():
    made = []

    def build(posts):
        with patch(
            "pinboard_mcp_server.client.pinboard.Pinboard", return_value=_account(posts)
        ):
            client = PinboardClient("user:TOKEN")
        client.min_request_interval = 0.0
        made.append(client)
        return client

    yield build
    for client in made:
        client._executor.shutdown(wait=False)


class TestSearch:
    async def test_words_in_any_order_including_the_url(self, client_for, now):
        client = client_for(
            [
                post(
                    "https://x.com/patrickc/status/1",
                    "A tool for feedback",
                    time=_stamp(now),
                ),
                post("https://example.com/", "Feedback forms", time=_stamp(now)),
            ]
        )

        found = await client.search_bookmarks("patrickc feedback tool")

        assert [b.url for b in found] == ["https://x.com/patrickc/status/1"]

    async def test_widens_when_the_recent_hundred_do_not_fill_the_page(
        self, client_for, now
    ):
        posts = [
            post(
                f"https://example.com/{n}",
                "Filler",
                time=_stamp(now - timedelta(hours=n)),
            )
            for n in range(100)
        ]
        posts.append(
            post("https://old/", "Rare quokka", time=_stamp(now - timedelta(days=90)))
        )
        client = client_for(posts)

        found = await client.search_bookmarks("quokka")

        assert [b.url for b in found] == ["https://old/"]

    async def test_a_date_range_applies(self, client_for, now):
        client = client_for(
            [
                post("https://new/", "Quokka", time=_stamp(now - timedelta(days=1))),
                post("https://old/", "Quokka", time=_stamp(now - timedelta(days=20))),
            ]
        )

        found = await client.search_bookmarks(
            "quokka",
            from_date=now - timedelta(days=30),
            to_date=now - timedelta(days=10),
        )

        assert [b.url for b in found] == ["https://old/"]

    async def test_a_change_on_pinboard_clears_remembered_answers(
        self, client_for, now
    ):
        client = client_for([post("https://a/", "Quokka", time=_stamp(now))])
        assert len(await client.search_bookmarks("quokka")) == 1

        client._pb = _account(
            [
                post("https://a/", "Quokka", time=_stamp(now)),
                post("https://b/", "Quokka two", time=_stamp(now)),
            ]
        )
        client._pb.posts.update.return_value = datetime(2026, 11, 27, 9, 0, 0)
        client._cache_valid_until = None  # the recheck interval has passed

        assert len(await client.search_bookmarks("quokka")) == 2

    async def test_remembered_answers_expire_on_their_own(self, client_for):
        client = client_for([])

        assert client._query_cache.ttl == 300


class TestRecent:
    async def test_reaches_past_the_recent_hundred(self, client_for, now):
        client = client_for(
            [
                post(
                    f"https://example.com/{n}", time=_stamp(now - timedelta(minutes=n))
                )
                for n in range(150)
            ]
        )

        found = await client.get_recent_bookmarks(days=1, limit=200)

        assert len(found) == 150

    async def test_does_not_ask_for_more_when_the_hundred_already_span_the_window(
        self, client_for, now
    ):
        client = client_for(
            [
                post(f"https://example.com/{n}", time=_stamp(now - timedelta(days=n)))
                for n in range(120)
            ]
        )

        found = await client.get_recent_bookmarks(days=7, limit=50)

        assert len(found) == 7
        client._pb.posts.all.assert_not_called()


class TestTags:
    async def test_several_tags_reach_back_through_history(self, client_for, now):
        posts = [
            post(f"https://example.com/{n}", time=_stamp(now - timedelta(hours=n)))
            for n in range(100)
        ]
        posts.append(
            post(
                "https://old/",
                tags="python sqlite",
                time=_stamp(now - timedelta(days=900)),
            )
        )
        client = client_for(posts)

        found = await client.get_bookmarks_by_tags(["python", "sqlite"])

        assert [b.url for b in found] == ["https://old/"]
        assert client._pb.posts.all.call_args.kwargs["tag"] == "python sqlite"

    async def test_undated_bookmarks_sort_last_and_fall_outside_any_range(
        self, client_for, now
    ):
        dated = post("https://dated/", tags="python", time=_stamp(now))
        undated = as_pinboard_object(post("https://undated/", tags="python"))
        undated.time = None
        client = client_for([dated])
        client._pb.posts.recent.return_value["posts"].append(undated)

        everything = await client.get_bookmarks_by_tags(["python"])
        ranged = await client.get_bookmarks_by_tags(
            ["python"], from_date=now - timedelta(days=1)
        )

        assert [b.url for b in everything] == ["https://dated/", "https://undated/"]
        assert [b.url for b in ranged] == ["https://dated/"]


class TestPostsAllAllowance:
    """Pinboard allows ``posts/all`` once every five minutes; a search never waits for it."""

    def _history(self, now):
        posts = [
            post(
                f"https://example.com/{n}",
                "Filler",
                time=_stamp(now - timedelta(hours=n)),
            )
            for n in range(100)
        ]
        posts.append(
            post("https://old/", "Rare quokka", time=_stamp(now - timedelta(days=90)))
        )
        posts.append(
            post("https://older/", "Shy numbat", time=_stamp(now - timedelta(days=95)))
        )
        return posts

    async def test_a_second_call_too_soon_is_not_made(self, client_for, now):
        client = client_for(self._history(now))
        await client.search_bookmarks_extended("quokka", days_back=120)

        found = await client.search_bookmarks_extended("numbat", days_back=200)

        assert found == []
        assert client._pb.posts.all.call_count == 1

    async def test_a_partial_answer_is_not_remembered(self, client_for, now):
        client = client_for(self._history(now))
        await client.search_bookmarks_extended("quokka", days_back=120)
        await client.search_bookmarks_extended("numbat", days_back=200)

        client._last_posts_all = None  # five minutes later
        found = await client.search_bookmarks_extended("numbat", days_back=200)

        assert [b.url for b in found] == ["https://older/"]

    async def test_an_error_from_pinboard_does_not_fail_the_search(
        self, client_for, now
    ):
        posts = self._history(now)
        posts.append(post("https://new/", "Quokka today", time=_stamp(now)))
        client = client_for(posts)
        client._pb.posts.all.side_effect = RuntimeError("429 Too Many Requests")

        found = await client.search_bookmarks("quokka")

        assert [b.url for b in found] == ["https://new/"]

    async def test_nothing_is_called_while_the_index_sync_owns_it(
        self, client_for, now
    ):
        client = client_for(self._history(now))
        client.posts_all_enabled = False

        assert await client.search_bookmarks("quokka") == []
        assert await client.get_bookmarks_by_tags(["python"]) == []
        client._pb.posts.all.assert_not_called()

    def test_it_is_never_called_without_a_filter(self, client_for):
        client = client_for([])

        with pytest.raises(ValueError):
            client._posts_all()
