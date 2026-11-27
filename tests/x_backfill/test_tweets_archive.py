"""Tweet records, URL matching, bookmark fields and the archive reader."""

import zipfile
from urllib.parse import quote

import pytest

from pinboard_x_backfill.archive import ArchiveError, parse_archive_js, read_archive
from pinboard_x_backfill.store import TweetStore
from pinboard_x_backfill.tweets import (
    BOOKMARK,
    LIKE,
    NOTES_MAX_ENCODED,
    TITLE_MAX,
    Tweet,
    bookmark_notes,
    bookmark_tags,
    bookmark_title,
    clean_handle,
    normalise_time,
    snowflake_time,
    tweet_id_from_url,
)

from .conftest import FIXTURES


@pytest.mark.parametrize(
    "url",
    [
        "https://twitter.com/simonw/status/1900456864773046272",
        "https://x.com/simonw/status/1900456864773046272",
        "http://www.twitter.com/simonw/status/1900456864773046272/",
        "https://mobile.twitter.com/simonw/status/1900456864773046272?s=20&t=x",
        "https://x.com/i/web/status/1900456864773046272",
        "https://x.com/i/status/1900456864773046272#m",
        "https://twitter.com/simonw/statuses/1900456864773046272",
        "https://X.com/SimonW/status/1900456864773046272/photo/1",
        "  https://x.com/simonw/status/1900456864773046272  ",
    ],
)
def test_every_spelling_of_a_tweet_url_gives_the_same_id(url):
    assert tweet_id_from_url(url) == "1900456864773046272"


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/simonw",
        "https://example.com/simonw/status/1900456864773046272",
        "https://nottwitter.com/a/status/1",
        "https://x.com/simonw/status/abc",
        "https://x.com/simonw/status/123abc",
        "https://x.com/i/lists/123",
        "http://[bad",
        "",
    ],
)
def test_other_urls_are_not_tweets(url):
    assert tweet_id_from_url(url) is None


def test_snowflake_time(ids):
    assert snowflake_time(ids["collison"]) == "2026-11-20T14:03:00Z"
    assert snowflake_time("20") == ""  # pre-snowflake id
    assert snowflake_time("nope") == ""
    assert snowflake_time("9" * 40) == ""


def test_normalise_time():
    assert normalise_time("2026-11-20T14:03:00.000Z") == "2026-11-20T14:03:00Z"
    assert normalise_time("2026-11-20T15:03:00+01:00") == "2026-11-20T14:03:00Z"
    assert normalise_time("2026-11-20T14:03:00") == "2026-11-20T14:03:00Z"
    assert normalise_time("") == ""
    assert normalise_time("last week") == ""


def test_clean_handle():
    assert clean_handle("@patrickc") == "patrickc"
    assert clean_handle("not a handle") == ""
    assert clean_handle("") == ""


def test_title_and_notes_with_author(ids):
    tweet = Tweet(
        id=ids["collison"],
        kinds={LIKE},
        text="We built an internal feedback tool in a weekend.\nNotes below.",
        author_handle="patrickc",
        author_name="Patrick Collison",
        created_at="2026-11-20T14:03:00Z",
    )
    assert bookmark_title(tweet) == (
        "Patrick Collison (@patrickc): We built an internal feedback tool "
        "in a weekend. Notes below."
    )
    assert bookmark_notes(tweet) == (
        "We built an internal feedback tool in a weekend.\nNotes below.\n\n"
        "Author: Patrick Collison (@patrickc)\nPosted: 2026-11-20T14:03:00Z"
    )
    assert tweet.url() == f"https://x.com/patrickc/status/{ids['collison']}"
    assert tweet.url("twitter.com").startswith("https://twitter.com/patrickc/")


def test_title_and_notes_without_author_or_text(ids):
    bare = Tweet(id=ids["gone"], kinds={LIKE})
    assert bookmark_title(bare) == f"Tweet {ids['gone']}"
    assert bookmark_notes(bare) == "Posted: 2021-01-05T07:07:07Z"
    assert bare.url() == f"https://x.com/i/web/status/{ids['gone']}"
    handle_only = Tweet(id="5", kinds={LIKE}, author_handle="a")
    assert bookmark_title(handle_only) == "@a: tweet 5"
    assert bookmark_notes(handle_only) == "Author: @a"
    assert bookmark_title(Tweet(id="5", text="hi", author_handle="a")) == "@a: hi"
    assert bookmark_title(Tweet(id="5", text="just text")) == "just text"


def test_long_fields_are_cut_to_fit(ids):
    tweet = Tweet(
        id=ids["long"],
        kinds={LIKE},
        text="é🙂 " * 5000,
        author_handle="rauchg",
        author_name="Guillermo Rauch",
    )
    title = bookmark_title(tweet)
    assert len(title) == TITLE_MAX and title.endswith("…")
    notes = bookmark_notes(tweet)
    assert len(quote(notes, safe="")) <= NOTES_MAX_ENCODED
    assert notes.endswith(f"Posted: {snowflake_time(ids['long'])}")
    assert "…\n\nAuthor: Guillermo Rauch (@rauchg)" in notes


def test_tags():
    assert bookmark_tags(Tweet(id="1", kinds={BOOKMARK, LIKE})) == [
        "via:twitter-like",
        "via:twitter-bookmark",
    ]
    assert bookmark_tags(
        Tweet(id="1", kinds={LIKE}), ["x", "", "via:twitter-like"]
    ) == [
        "via:twitter-like",
        "x",
    ]


def test_merge_prefers_the_api_and_unites_kinds():
    archive = Tweet(id="1", kinds={LIKE}, text="trunc…", source="archive")
    api = Tweet(
        id="1",
        kinds={BOOKMARK},
        text="full text",
        author_handle="a",
        author_name="A",
        created_at="2026-01-01T00:00:00Z",
        source="api",
    )
    assert archive.merge(api) is True
    assert archive.kinds == {LIKE, BOOKMARK}
    assert (archive.text, archive.author_handle, archive.source) == (
        "full text",
        "a",
        "api",
    )
    # An archive copy never overwrites what the API gave.
    assert (
        archive.merge(Tweet(id="1", kinds={LIKE}, text="other", source="archive"))
        is False
    )
    assert archive.text == "full text"


def test_parse_like_js(ids):
    tweets = parse_archive_js((FIXTURES / "like.js").read_text())
    assert [t.id for t in tweets] == [
        ids["old"],
        ids["collison"],
        ids["gone"],
        ids["ifttt"],  # id recovered from expandedUrl
    ]
    assert tweets[0].text == "An old favourite from 2019 > everything since"
    assert tweets[0].kinds == {LIKE} and tweets[0].source == "archive"
    assert tweets[2].text == ""


def test_parse_bookmark_js_and_junk_entries():
    source = (
        'window.YTD.bookmark.part0 = [{"bookmark": {"tweetId": "42"}}, 7, {}, '
        '{"bookmark": {"fullText": "no id"}}];'
    )
    tweets = parse_archive_js(source)
    assert [(t.id, t.kinds) for t in tweets] == [("42", {BOOKMARK})]


@pytest.mark.parametrize(
    "source",
    [
        "[]",
        "window.YTD.tweets.part0 = []",
        "window.YTD.like.part0 = [oops",
        'window.YTD.like.part0 = {"like": 1}',
    ],
)
def test_bad_archive_files_are_refused(source):
    with pytest.raises(ArchiveError):
        parse_archive_js(source)


def test_read_archive_from_file_folder_and_zip(tmp_path, ids):
    like_js = (FIXTURES / "like.js").read_text()
    assert len(read_archive(FIXTURES / "like.js")) == 4

    data = tmp_path / "archive" / "data"
    data.mkdir(parents=True)
    (data / "like.js").write_text(like_js)
    (data / "like-part1.js").write_text(
        f'window.YTD.like.part1 = [{{"like": {{"tweetId": "{ids["old"]}"}}}}, '
        '{"like": {"tweetId": "77"}}]'
    )
    (data / "bookmark.js").write_text(
        f'window.YTD.bookmark.part0 = [{{"bookmark": {{"tweetId": "{ids["old"]}"}}}}]'
    )
    (data / "tweets.js").write_text("window.YTD.tweets.part0 = []")
    for root in (tmp_path / "archive", data):
        tweets = {t.id: t for t in read_archive(root)}
        assert len(tweets) == 5
        assert tweets[ids["old"]].kinds == {LIKE, BOOKMARK}
        assert tweets[ids["old"]].text.startswith("An old favourite")

    zipped = tmp_path / "twitter-archive.zip"
    with zipfile.ZipFile(zipped, "w") as archive:
        archive.write(data / "like.js", "data/like.js")
        archive.write(data / "tweets.js", "data/tweets.js")
        archive.writestr("assets/like.js", "not the real one")
    assert len(read_archive(zipped)) == 4


def test_read_archive_errors(tmp_path):
    with pytest.raises(ArchiveError, match="no such file"):
        read_archive(tmp_path / "missing")
    with pytest.raises(ArchiveError, match="no like.js"):
        read_archive(tmp_path)


def test_store_round_trip(tmp_path):
    path = tmp_path / "data" / "tweets.json"
    store = TweetStore(path)
    assert store.merge([Tweet(id="2", kinds={LIKE}, source="archive")]) == (1, 0)
    assert store.merge([Tweet(id="2", kinds={LIKE}, text="t", source="api")]) == (0, 1)
    assert store.merge([Tweet(id="2", kinds={LIKE}, text="t", source="api")]) == (0, 0)
    assert store.fetched("2", LIKE) and not store.fetched("2", BOOKMARK)
    assert not store.fetched("3", LIKE)
    store.save()
    assert path.stat().st_mode & 0o777 == 0o600
    again = TweetStore(path)
    assert len(again) == 1 and again.tweets["2"].text == "t"
