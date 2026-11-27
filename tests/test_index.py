"""The SQLite index: writing, full-text search, filters, counts and embeddings."""

import os
import sqlite3
import stat
import threading

import pytest

from pinboard_mcp_server.embeddings import embed_pending
from pinboard_mcp_server.index import (
    BookmarkIndex,
    Filters,
    Post,
    default_index_path,
    query_terms,
)
from pinboard_mcp_server.sync import import_export

from .fakes import EXPORT, FakeEmbedder, as_pinboard_object, post

COLLISON = "Patrick Collison (@patrickc): We built a small internal tool"


@pytest.fixture
def index(tmp_path) -> BookmarkIndex:
    return BookmarkIndex(tmp_path / "index.sqlite3")


@pytest.fixture
def loaded(index) -> BookmarkIndex:
    import_export(index, EXPORT)
    return index


def titles(outcome) -> list[str]:
    return [bookmark["title"] for bookmark in outcome.bookmarks]


class TestFile:
    @pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
    def test_the_file_is_private_to_its_owner(self, tmp_path):
        index = BookmarkIndex(tmp_path / "nested" / "index.sqlite3")

        assert stat.S_IMODE(index.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(index.path.parent.stat().st_mode) == 0o700

    def test_reopening_keeps_what_was_written(self, tmp_path):
        BookmarkIndex(tmp_path / "i.db").upsert([Post(url="https://example.com/")])

        assert BookmarkIndex(tmp_path / "i.db").count() == 1

    def test_path_comes_from_the_environment(self, tmp_path):
        assert default_index_path({"PINBOARD_INDEX_PATH": "~/x.db", "HOME": "/h"}) == (
            (tmp_path / "ignored").parent.__class__("~/x.db").expanduser()
        )
        assert default_index_path({"HOME": "/h"}).name == "bookmarks.sqlite3"

    def test_derived_fields_are_recomputed_when_the_rules_change(self, loaded):
        with loaded._writing() as db:
            db.execute("UPDATE bookmarks SET author_handle = NULL, domain = 'stale'")
            db.execute("UPDATE meta SET value = '0' WHERE key = 'derived_version'")

        reopened = BookmarkIndex(loaded.path)

        assert reopened.search("", Filters(author="patrickc")).bookmarks
        assert reopened.counts("domain")[0]["domain"] == "x.com"

    def test_usable_from_several_threads(self, loaded):
        seen: list[int] = []
        threads = [
            threading.Thread(target=lambda: seen.append(loaded.count()))
            for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert seen == [23] * 4


class TestWriting:
    def test_upsert_adds_updates_and_skips_the_unchanged(self, index):
        first = index.upsert([Post.from_pinboard(post("https://a/", "One"))])
        again = index.upsert([Post.from_pinboard(post("https://a/", "One"))])
        changed = index.upsert([Post.from_pinboard(post("https://a/", "Two"))])

        assert (first.added, again.unchanged, changed.updated) == (1, 1, 1)
        assert index.count() == 1
        assert index.get("https://a/")["title"] == "Two"

    def test_an_update_replaces_the_search_text_and_the_tags(self, index):
        index.upsert([Post.from_pinboard(post("https://a/", "Aardvark", tags="zoo"))])
        index.upsert([Post.from_pinboard(post("https://a/", "Badger", tags="sett"))])

        assert index.search("aardvark").bookmarks == []
        assert titles(index.search("badger")) == ["Badger"]
        assert index.tag_counts() == [{"tag": "sett", "count": 1}]

    def test_pinboard_objects_and_raw_dicts_read_the_same(self):
        raw = post("https://a/", "T", tags="x y", shared="no", toread="yes")

        assert Post.from_pinboard(raw) == Post.from_pinboard(as_pinboard_object(raw))

    def test_a_post_without_a_time_has_no_date(self, index):
        raw = post("https://a/")
        raw["time"] = ""
        index.upsert([Post.from_pinboard(raw)])

        assert index.get("https://a/")["saved_at"] is None

    def test_replace_range_removes_what_pinboard_no_longer_has(self, index):
        index.upsert(
            Post.from_pinboard(post(f"https://{n}/", time=f"2026-01-0{n}T12:00:00Z"))
            for n in (1, 2, 3)
        )

        delta = index.replace_range(
            "2026-01-02T00:00:00Z",
            "2026-01-03T00:00:00Z",
            [Post.from_pinboard(post("https://new/", time="2026-01-02T08:00:00Z"))],
        )

        assert (delta.added, delta.deleted) == (1, 1)
        assert index.get("https://2/") is None
        assert index.get("https://1/") and index.get("https://3/")
        assert index.search("", Filters()).bookmarks[1]["url"] == "https://new/"

    def test_deleting_a_bookmark_removes_it_from_search(self, loaded):
        loaded.delete_urls(["https://sqlite.org/fts5.html"])

        assert "SQLite FTS5 Extension" not in titles(loaded.search("fts5"))

    def test_a_failed_write_leaves_nothing_behind(self, index):
        with pytest.raises(RuntimeError):
            with index._writing():
                index.upsert([Post(url="https://a/")])
                raise RuntimeError("boom")

        assert index.count() == 0


class TestQueryTerms:
    def test_words_and_quoted_phrases(self):
        assert query_terms('feedback "internal tool" feedback') == [
            '"feedback"',
            '"internal tool"',
        ]

    @pytest.mark.parametrize("query", ["", "   ", '""', "* : ( )", '"'])
    def test_nothing_searchable(self, query):
        assert query_terms(query) == []

    @pytest.mark.parametrize(
        "query",
        [
            "a AND b",
            "NEAR(a b)",
            'unbalanced "quote',
            "col:value",
            "wild*",
            "a OR",
            "-not ^x",
        ],
    )
    def test_fts_syntax_is_inert(self, loaded, query):
        loaded.search(query)  # must not raise


class TestKeywordSearch:
    def test_words_in_any_order_across_fields(self, loaded):
        assert titles(loaded.search("tool feedback collison"))[0].startswith(COLLISON)

    def test_bookmarks_matching_every_word_come_before_partial_matches(self, loaded):
        outcome = loaded.search("sqlite extension vector")

        assert outcome.bookmarks[0]["url"] == "https://github.com/asg017/sqlite-vec"
        assert outcome.bookmarks[0]["matched_terms"] == "all"
        assert {b["matched_terms"] for b in outcome.bookmarks[1:]} == {"some"}

    def test_a_natural_language_question_still_finds_it(self, loaded):
        outcome = loaded.search(
            "what was that Patrick Collison tweet about building a feedback tool"
        )

        assert outcome.bookmarks[0]["author_handle"] == "patrickc"
        assert "feedback" in outcome.bookmarks[0]["title"]

    def test_urls_are_searched(self, loaded):
        assert titles(loaded.search("hwh"))[0] == "How to Work Hard"
        assert loaded.search("patrickc").bookmarks[0]["author_handle"] == "patrickc"

    def test_stemming_and_accents(self, loaded):
        assert titles(loaded.search("interview customers"))[0] == (
            "Running customer interviews that surface real problems"
        )
        assert titles(loaded.search("creme brulee zurich")) == [
            "Crème brûlée, the Zürich café way"
        ]

    def test_a_quoted_phrase_needs_the_words_together(self, loaded):
        assert titles(loaded.search('"dark universe"')) == [
            "Euclid: mapping the dark universe"
        ]
        assert loaded.search('"universe dark"').bookmarks == []

    def test_tags_are_searched(self, loaded):
        assert "Euclid: mapping the dark universe" in titles(loaded.search("astronomy"))

    def test_paging(self, loaded):
        first = loaded.search("sqlite", limit=2)
        second = loaded.search("sqlite", limit=2, offset=2)

        assert first.has_more is True
        assert len(first.bookmarks) == 2
        assert not {b["id"] for b in first.bookmarks} & {
            b["id"] for b in second.bookmarks
        }

    def test_ids_are_stable_and_are_pinboards(self, loaded):
        bookmark = loaded.search("fts5").bookmarks[0]

        assert bookmark["id"] == loaded.search("fts5").bookmarks[0]["id"]
        assert len(bookmark["id"]) == 32

    def test_an_unknown_mode_is_refused(self, loaded):
        with pytest.raises(ValueError, match="mode"):
            loaded.search("x", mode="fuzzy")


class TestFilters:
    def test_no_query_lists_newest_first(self, loaded):
        outcome = loaded.search("", limit=3)

        assert outcome.mode == "listing"
        assert [b["saved_at"][:10] for b in outcome.bookmarks] == [
            "2026-11-25",
            "2026-11-22",
            "2026-11-21",
        ]
        assert outcome.has_more is True

    @pytest.mark.parametrize(
        "author", ["patrickc", "@PatrickC", "Patrick Collison", "collison"]
    )
    def test_author_by_handle_or_name(self, loaded, author):
        outcome = loaded.search("", Filters(author=author))

        assert {b["author_handle"] for b in outcome.bookmarks} == {"patrickc"}
        assert len(outcome.bookmarks) == 3

    def test_the_question_from_the_audit(self, loaded):
        """ "That Patrick Collison tweet last week about building a feedback tool"."""
        outcome = loaded.search(
            "feedback tool",
            Filters(author="patrickc", since="2026-11-19T00:00:00Z"),
        )

        assert len(outcome.bookmarks) == 1
        assert outcome.bookmarks[0]["title"].startswith(COLLISON)
        assert outcome.bookmarks[0]["posted_at"] == "2026-11-19T16:20:00Z"

    def test_saved_and_posted_dates_filter_separately(self, loaded):
        # Posted on the 19th, liked on the 20th.
        day = {"since": "2026-11-19T00:00:00Z", "until": "2026-11-20T00:00:00Z"}

        assert loaded.search("", Filters(author="patrickc", **day)).bookmarks == []
        posted = loaded.search(
            "", Filters(author="patrickc", date_field="posted", **day)
        )
        assert len(posted.bookmarks) == 1

    def test_posted_falls_back_to_the_save_date(self, loaded):
        outcome = loaded.search(
            "",
            Filters(
                since="2026-11-22T00:00:00Z",
                until="2026-11-23T00:00:00Z",
                date_field="posted",
            ),
        )

        assert titles(outcome) == ["SQLite FTS5 Extension"]

    @pytest.mark.parametrize(
        "domain", ["x.com", "twitter.com", "https://www.twitter.com/"]
    )
    def test_domain_treats_twitter_and_x_as_one(self, loaded, domain):
        assert len(loaded.search("", Filters(domain=domain), limit=50).bookmarks) == 11

    def test_domain_includes_subdomains_but_not_lookalikes(self, index):
        index.upsert(
            Post(url=url)
            for url in (
                "https://docs.python.org/",
                "https://python.org/",
                "https://notpython.org/",
            )
        )

        found = {
            b["url"] for b in index.search("", Filters(domain="python.org")).bookmarks
        }

        assert found == {"https://docs.python.org/", "https://python.org/"}

    def test_like_wildcards_in_a_filter_are_literal(self, loaded):
        assert loaded.search("", Filters(author="%")).bookmarks == []
        assert loaded.search("", Filters(domain="%")).bookmarks == []

    def test_all_tags_are_required_case_insensitively(self, loaded):
        outcome = loaded.search("", Filters(tags=("SQLite", "reference")))

        assert set(titles(outcome)) == {
            "SQLite FTS5 Extension",
            "sqlite3 — DB-API 2.0 interface for SQLite databases",
        }

    @pytest.mark.parametrize(
        ("source", "count"),
        [
            # Six likes, and one tweet both liked and bookmarked.
            ("twitter-like", 7),
            ("likes", 7),
            ("twitter-bookmark", 3),
            ("via:twitter-bookmark", 3),
            ("twitter", 11),
            ("tweets", 11),
            ("bookmark", 12),
        ],
    )
    def test_source(self, loaded, source, count):
        assert (
            len(loaded.search("", Filters(source=source), limit=50).bookmarks) == count
        )

    def test_a_tweet_both_liked_and_bookmarked_answers_to_both(self, loaded):
        for source in ("twitter-like", "twitter-bookmark"):
            found = titles(loaded.search("embedding models", Filters(source=source)))
            assert any("Notes on running embedding models" in t for t in found)

    def test_to_read_and_private(self, loaded):
        assert len(loaded.search("", Filters(to_read=True)).bookmarks) == 2
        assert all(
            b["private"] for b in loaded.search("", Filters(private=True)).bookmarks
        )

    def test_filters_narrow_a_keyword_search(self, loaded):
        assert titles(loaded.search("sqlite", Filters(domain="github.com"))) == [
            "asg017/sqlite-vec: A vector search SQLite extension"
        ]


class TestCounts:
    def test_by_author_with_names(self, loaded):
        assert loaded.counts("author", limit=2) == [
            {"author": "patrickc", "count": 3, "name": "Patrick Collison"},
            {"author": "simonw", "count": 2, "name": "Simon Willison"},
        ]

    def test_by_month_newest_first(self, loaded):
        assert loaded.counts("month", limit=2) == [
            {"month": "2026-11", "count": 6},
            {"month": "2026-10", "count": 3},
        ]

    def test_by_year_on_the_posted_date(self, loaded):
        counts = loaded.counts("year", Filters(date_field="posted", source="twitter"))

        assert {"year": "2025", "count": 1} in counts

    def test_by_tag_within_a_query_and_a_filter(self, loaded):
        assert loaded.counts("tag", query="sqlite", limit=1) == [
            {"tag": "sqlite", "count": 4}
        ]
        assert loaded.counts("source", Filters(domain="x.com"))[0] == {
            "source": "twitter-like",
            "count": 6,
        }

    def test_by_domain(self, loaded):
        assert loaded.counts("domain", limit=1) == [{"domain": "x.com", "count": 11}]

    def test_an_unknown_grouping_is_refused(self, loaded):
        with pytest.raises(ValueError, match="group_by"):
            loaded.counts("colour")

    def test_tag_counts_merge_case(self, index):
        index.upsert(
            [
                Post(url="https://a/", tags=("Python",)),
                Post(url="https://b/", tags=("python", "web")),
            ]
        )

        assert [entry["count"] for entry in index.tag_counts()] == [2, 1]

    def test_day_counts(self, loaded):
        assert loaded.day_counts()["2026-11-20"] == 1
        assert sum(loaded.day_counts().values()) == 23


class TestSemantic:
    @pytest.fixture
    def embedded(self, loaded):
        embedder = FakeEmbedder()
        assert embed_pending(loaded, embedder, batch_size=7) == 23
        return loaded, embedder

    def test_everything_is_embedded_once(self, embedded):
        index, embedder = embedded

        assert index.pending_embedding_count(embedder.model_id) == 0
        assert embed_pending(index, embedder) == 0
        assert index.stats()["embedded"] == 23

    def test_semantic_finds_by_meaning_what_keywords_miss(self, embedded):
        index, embedder = embedded
        query = "customer complaints"

        keyword = index.search(query, mode="keyword")
        semantic = index.search(query, mode="semantic", embedder=embedder, limit=4)

        assert not any(t.startswith(COLLISON) for t in titles(keyword))
        assert semantic.mode == "semantic"
        assert any(t.startswith(COLLISON) for t in titles(semantic))
        assert (
            semantic.bookmarks[0]["similarity"] >= semantic.bookmarks[-1]["similarity"]
        )

    def test_hybrid_fuses_both_and_says_how_each_matched(self, embedded):
        index, embedder = embedded

        outcome = index.search("feedback tool", embedder=embedder, limit=5)

        assert outcome.mode == "hybrid"
        assert outcome.bookmarks[0]["title"].startswith(COLLISON)
        assert outcome.bookmarks[0]["matched_by"] == ["keyword", "semantic"]
        assert any(b["matched_by"] == ["semantic"] for b in outcome.bookmarks)

    def test_hybrid_pages_never_repeat_or_skip(self, embedded):
        index, embedder = embedded
        query = "feedback tool sqlite search python"

        everything = index.search(query, embedder=embedder, limit=100).bookmarks
        paged = []
        offset = 0
        while True:
            page = index.search(query, embedder=embedder, limit=3, offset=offset)
            paged.extend(page.bookmarks)
            offset += 3
            if not page.has_more:
                break

        assert len(everything) > 6
        assert [b["url"] for b in paged] == [b["url"] for b in everything]

    def test_hybrid_puts_bookmarks_with_every_word_first(self, embedded):
        index, embedder = embedded

        outcome = index.search("feedback tool", embedder=embedder, limit=100)

        full = [b.get("matched_terms") == "all" for b in outcome.bookmarks]
        assert any(full) and not all(full)
        assert full == sorted(full, reverse=True)

    def test_a_similarity_floor_drops_far_neighbours(self, embedded):
        index, embedder = embedded
        query = "customer complaints"
        loose = index.search(query, mode="semantic", embedder=embedder, limit=100)

        embedder.min_similarity = 0.3
        strict = index.search(query, mode="semantic", embedder=embedder, limit=100)

        assert 0 < len(strict.bookmarks) < len(loose.bookmarks)
        assert all(b["similarity"] >= 0.3 for b in strict.bookmarks)

    def test_filters_apply_to_semantic_results(self, embedded):
        index, embedder = embedded

        outcome = index.search(
            "feedback", Filters(author="dhh"), mode="semantic", embedder=embedder
        )

        assert {b["author_handle"] for b in outcome.bookmarks} == {"dhh"}

    def test_an_edited_bookmark_is_embedded_again(self, embedded):
        index, embedder = embedded
        index.upsert(
            [Post.from_pinboard(post("https://sqlite.org/fts5.html", "Rewritten"))]
        )

        assert index.pending_embedding_count(embedder.model_id) == 1
        assert embed_pending(index, embedder) == 1

    def test_a_new_model_re_embeds_everything(self, embedded):
        index, _ = embedded
        other = FakeEmbedder()
        other.model_id = "fake:other"

        assert index.pending_embedding_count(other.model_id) == 23

    def test_deleting_a_bookmark_deletes_its_vector(self, embedded):
        index, embedder = embedded
        index.delete_urls(["https://sqlite.org/fts5.html"])

        assert index.stats()["embedded"] == 22
        outcome = index.search("fts5 tokenizers", mode="semantic", embedder=embedder)
        assert "SQLite FTS5 Extension" not in titles(outcome)

    def test_without_a_model_search_is_keyword_and_says_why(self, loaded):
        outcome = loaded.search("feedback tool")

        assert outcome.mode == "keyword"
        assert "not set up" in outcome.semantic_note

    def test_a_model_still_loading_does_not_hold_search_up(self, embedded):
        index, embedder = embedded
        embedder.ready = False

        outcome = index.search("feedback tool", embedder=embedder)

        assert outcome.mode == "keyword"
        assert "loading" in outcome.semantic_note

    def test_nothing_embedded_yet(self, loaded):
        outcome = loaded.search("feedback tool", embedder=FakeEmbedder())

        assert outcome.mode == "keyword"
        assert "embedded" in outcome.semantic_note

    def test_a_failing_model_does_not_fail_search(self, embedded):
        index, embedder = embedded
        embedder.embed_query = lambda text: (_ for _ in ()).throw(OSError("model gone"))

        outcome = index.search("feedback tool", embedder=embedder)

        assert outcome.bookmarks
        assert "failed" in outcome.semantic_note

    def test_semantic_only_with_no_model_falls_back_to_keywords(self, loaded):
        outcome = loaded.search("feedback tool", mode="semantic")

        assert outcome.mode == "keyword"
        assert outcome.bookmarks

    def test_an_interrupted_run_keeps_its_finished_batches(self, loaded):
        embedder = FakeEmbedder()
        calls = iter([False, False, True])

        done = embed_pending(
            loaded, embedder, batch_size=5, should_stop=lambda: next(calls)
        )

        assert done == 10
        assert loaded.pending_embedding_count(embedder.model_id) == 13


def test_fts5_is_available_here():
    """The whole index rests on FTS5; fail loudly in CI if a Python lacks it."""
    sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE t USING fts5(x)")


class TestFastEmbedWrapper:
    """The real model cannot be downloaded in CI; a stand-in ``fastembed`` checks the wrapper."""

    @pytest.fixture
    def fastembed(self, monkeypatch):
        import sys
        import types

        import numpy as np

        loads = []

        class TextEmbedding:
            def __init__(self, model_name):
                loads.append(model_name)

            def passage_embed(self, texts):
                return (np.array([3.0, 4.0]) for _ in texts)

            def query_embed(self, text):
                yield np.array([0.0, 2.0])

        module = types.ModuleType("fastembed")
        module.TextEmbedding = TextEmbedding
        monkeypatch.setitem(sys.modules, "fastembed", module)
        return loads

    def test_vectors_come_back_unit_length_float32(self, fastembed):
        import numpy as np

        from pinboard_mcp_server.embeddings import FastEmbedEmbedder

        embedder = FastEmbedEmbedder("some/model")
        assert embedder.ready is False

        documents = embedder.embed_documents(["a", "b"])
        query = embedder.embed_query("q")

        assert embedder.model_id == "fastembed:some/model"
        assert embedder.ready is True
        assert fastembed == ["some/model"]  # loaded once
        assert np.frombuffer(documents[0], dtype=np.float32).tolist() == pytest.approx(
            [0.6, 0.8]
        )
        assert np.frombuffer(query, dtype=np.float32).tolist() == [0.0, 1.0]

    def test_the_default_embedder_follows_the_environment(self, fastembed, monkeypatch):
        from pinboard_mcp_server import embeddings

        monkeypatch.setattr(embeddings, "fastembed_installed", lambda: True)

        assert embeddings.default_embedder({}).model_name == embeddings.DEFAULT_MODEL
        assert (
            embeddings.default_embedder({"PINBOARD_EMBEDDING_MODEL": "x/y"}).model_name
            == "x/y"
        )
        assert embeddings.default_embedder({"PINBOARD_SEMANTIC": "off"}) is None

    def test_the_similarity_floor_follows_the_model_and_the_environment(
        self, fastembed, monkeypatch
    ):
        from pinboard_mcp_server import embeddings

        monkeypatch.setattr(embeddings, "fastembed_installed", lambda: True)
        default = embeddings.default_embedder({})
        other = embeddings.default_embedder({"PINBOARD_EMBEDDING_MODEL": "x/y"})
        stated = embeddings.default_embedder(
            {"PINBOARD_SEMANTIC_MIN_SIMILARITY": "0.6"}
        )
        nonsense = embeddings.default_embedder(
            {"PINBOARD_SEMANTIC_MIN_SIMILARITY": "high"}
        )

        assert default.min_similarity == embeddings.DEFAULT_MIN_SIMILARITY
        assert other.min_similarity == 0.0
        assert stated.min_similarity == 0.6
        assert nonsense.min_similarity == embeddings.DEFAULT_MIN_SIMILARITY

        monkeypatch.setattr(embeddings, "fastembed_installed", lambda: False)
        assert embeddings.default_embedder({}) is None

    def test_a_model_returning_too_few_vectors_is_an_error(self, loaded):
        embedder = FakeEmbedder()
        embedder.embed_documents = lambda texts: []

        with pytest.raises(RuntimeError, match="wrong number"):
            embed_pending(loaded, embedder)


def test_a_term_of_punctuation_alone_is_not_a_term():
    """FTS5 drops underscores as it does other punctuation; such a term could never match."""
    from pinboard_mcp_server.index import query_terms

    assert query_terms("feedback _ __ -- tool") == ['"feedback"', '"tool"']
    assert query_terms("snake_case") == ['"snake_case"']
