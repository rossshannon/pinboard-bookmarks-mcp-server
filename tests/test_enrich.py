"""What is read out of a bookmark's own fields: domain, source, author, posted date."""

from datetime import datetime, timezone

import pytest

from pinboard_mcp_server import enrich
from pinboard_mcp_server.enrich import derive


def snowflake(iso: str) -> int:
    moment = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
    return (int(moment.timestamp() * 1000) - enrich.TWITTER_EPOCH_MS) << 22


TWEET_ID = snowflake("2026-11-19T16:20:00")


class TestDomain:
    @pytest.mark.parametrize(
        ("url", "domain"),
        [
            ("https://www.example.com/a", "example.com"),
            ("https://Example.COM:8443/a", "example.com"),
            ("https://twitter.com/jack/status/20", "x.com"),
            ("https://mobile.twitter.com/jack/status/20", "x.com"),
            ("https://x.com/jack", "x.com"),
            ("https://m.youtube.com/watch?v=1", "youtube.com"),
            ("https://blog.example.co.uk/post", "blog.example.co.uk"),
            ("https://m.io/", "m.io"),
            ("not a url", ""),
            ("", ""),
        ],
    )
    def test_domain_is_normalised(self, url, domain):
        assert derive(url).domain == domain

    def test_a_filter_value_normalises_the_same_way(self):
        assert enrich.normalise_domain("https://www.Twitter.com/") == "x.com"
        assert enrich.normalise_domain("GitHub.com") == "github.com"


class TestTweets:
    def test_the_backfill_shape_gives_handle_name_source_and_posted_date(self):
        found = derive(
            f"https://x.com/patrickc/status/{TWEET_ID}",
            "Patrick Collison (@patrickc): We built a tool",
            "We built a tool",
            ["via:twitter-like"],
        )

        assert found.author_handle == "patrickc"
        assert found.author_name == "Patrick Collison"
        assert found.source == "twitter-like"
        assert found.posted_at == "2026-11-19T16:20:00Z"
        assert found.tweet_id == str(TWEET_ID)
        assert found.author_text == "Patrick Collison patrickc"

    def test_a_bookmarked_tweet_outranks_a_like(self):
        found = derive(
            f"https://x.com/a/status/{TWEET_ID}",
            tags=["via:twitter-like", "via:twitter-bookmark"],
        )

        assert found.source == "twitter-bookmark"
        assert enrich.sources_of(
            ["via:twitter-like", "via:twitter-bookmark"], found.source
        ) == {"twitter-like", "twitter-bookmark"}

    @pytest.mark.parametrize("tag", [".from:twitter", "twitter-fav", "!fromtwitter"])
    def test_legacy_like_tags_count_as_likes(self, tag):
        assert (
            derive(f"https://twitter.com/a/status/{TWEET_ID}", tags=[tag]).source
            == "twitter-like"
        )

    def test_a_legacy_tag_on_something_that_is_not_a_tweet_changes_nothing(self):
        assert derive("https://example.com/", tags=["twitter-fav"]).source == "bookmark"

    def test_a_tweet_with_no_via_tag_is_still_a_tweet(self):
        found = derive(f"https://x.com/naval/status/{TWEET_ID}", 'Naval on X: "Read"')

        assert found.source == "twitter"
        assert found.author_handle == "naval"
        assert found.author_name == "Naval"

    def test_a_handle_only_title(self):
        found = derive(f"https://x.com/i/web/status/{TWEET_ID}", "@simonw: shipped it")

        assert found.author_handle == "simonw"
        assert found.author_name is None

    @pytest.mark.parametrize("path", ["i/status", "i/web/status"])
    def test_an_anonymous_status_url_has_no_handle(self, path):
        found = derive(f"https://x.com/{path}/{TWEET_ID}")

        assert found.tweet_id == str(TWEET_ID)
        assert found.author_handle is None

    def test_a_handle_starting_with_i_is_a_handle(self):
        assert derive(f"https://x.com/ian/status/{TWEET_ID}").author_handle == "ian"

    def test_pre_snowflake_ids_carry_no_date(self):
        found = derive("https://twitter.com/jack/status/20")

        assert found.tweet_id == "20"
        assert found.posted_at is None

    def test_an_id_from_the_future_is_not_believed(self):
        now = datetime(2020, 1, 1, tzinfo=timezone.utc)

        assert derive(f"https://x.com/a/status/{TWEET_ID}", now=now).posted_at is None

    def test_a_posted_line_in_the_notes_wins_over_the_id(self):
        found = derive(
            f"https://x.com/a/status/{TWEET_ID}",
            notes="Some text\nPosted: 2026-11-01T08:00:00Z\nMore",
        )

        assert found.posted_at == "2026-11-01T08:00:00Z"

    def test_a_profile_or_other_x_page_is_not_a_tweet(self):
        found = derive("https://x.com/patrickc", "Patrick Collison (@patrickc): bio")

        assert found.tweet_id is None
        assert found.author_handle is None
        assert found.source == "bookmark"

    def test_a_title_that_looks_like_a_tweet_on_another_site_names_no_author(self):
        found = derive("https://example.com/x", "Someone (@handle): said a thing")

        assert found.author_handle is None


class TestSource:
    def test_any_via_tag_names_a_source(self):
        assert (
            derive("https://example.com/", tags=["via:readwise"]).source == "readwise"
        )

    def test_a_hand_saved_bookmark(self):
        assert derive("https://example.com/", tags=["python"]).source == "bookmark"


class TestDates:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("2026-11-20", "2026-11-20T00:00:00Z"),
            ("2026-11-20T08:05:11Z", "2026-11-20T08:05:11Z"),
            ("2026-11-20T09:05:11+01:00", "2026-11-20T08:05:11Z"),
            ("2026-11-20 08:05:11", "2026-11-20T08:05:11Z"),
        ],
    )
    def test_parse_and_format_round_trip_in_utc(self, text, expected):
        moment = enrich.parse_utc(text)

        assert moment is not None
        assert enrich.format_utc(moment) == expected

    @pytest.mark.parametrize("text", ["", "yesterday", "2026-13-45"])
    def test_nonsense_is_not_a_date(self, text):
        assert enrich.parse_utc(text) is None

    def test_url_hash_is_pinboards(self):
        assert enrich.url_hash("https://example.com/") == (
            "182ccedb33a9e03fbf1079b209da1a31"
        )
