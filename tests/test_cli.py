"""The ``pinboard-mcp-index`` command."""

import json

import pytest

from pinboard_mcp_server import cli
from pinboard_mcp_server.index import BookmarkIndex

from .fakes import EXPORT, FakeEmbedder, FakePinboard, export_posts


@pytest.fixture
def path(tmp_path, monkeypatch) -> str:
    monkeypatch.setenv("PINBOARD_SEMANTIC", "off")
    return str(tmp_path / "index.sqlite3")


@pytest.fixture
def loaded(path) -> str:
    assert cli.main(["--index", path, "import", str(EXPORT)]) == 0
    return path


def test_import_reports_what_it_loaded(path, capsys):
    assert cli.main(["--index", path, "import", str(EXPORT)]) == 0

    out = capsys.readouterr().out
    assert "23 added" in out and "holds 23 bookmarks" in out


def test_status_is_json(loaded, capsys):
    capsys.readouterr()

    assert cli.main(["--index", loaded, "status"]) == 0

    status = json.loads(capsys.readouterr().out)
    assert status["bookmarks"] == 23
    assert status["complete"] is False  # imported, never synced


def test_search_prints_matches(loaded, capsys):
    capsys.readouterr()

    assert (
        cli.main(
            [
                "--index",
                loaded,
                "search",
                "feedback",
                "tool",
                "--author",
                "patrickc",
                "--from",
                "2026-11-19",
            ]
        )
        == 0
    )

    out = capsys.readouterr().out
    assert "2026-11-20 @patrickc  Patrick Collison (@patrickc): We built" in out
    assert out.count("https://") == 1


def test_search_as_json_with_filters(loaded, capsys):
    capsys.readouterr()

    cli.main(
        [
            "--index",
            loaded,
            "search",
            "--source",
            "twitter-bookmark",
            "--tag",
            "sqlite",
            "--json",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "listing"
    assert [b["author_handle"] for b in result["bookmarks"]] == ["simonw"]


def test_search_with_no_matches(loaded, capsys):
    capsys.readouterr()

    cli.main(["--index", loaded, "search", "zyzzyva", "--posted", "--to", "2026-01-01"])

    assert "No matches." in capsys.readouterr().out


def test_the_index_path_can_come_from_the_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PINBOARD_INDEX_PATH", str(tmp_path / "env.db"))

    cli.main(["import", str(EXPORT)])

    assert BookmarkIndex(tmp_path / "env.db").count() == 23


def test_sync_needs_a_token(path, monkeypatch, capsys):
    monkeypatch.delenv("PINBOARD_TOKEN", raising=False)

    assert cli.main(["--index", path, "sync"]) == 1
    assert "PINBOARD_TOKEN" in capsys.readouterr().err


def test_sync_runs_against_pinboard(path, monkeypatch, capsys):
    import pinboard

    monkeypatch.setenv("PINBOARD_TOKEN", "user:TOKEN")
    monkeypatch.setattr(
        pinboard, "Pinboard", lambda token: FakePinboard(export_posts())
    )
    monkeypatch.setattr("pinboard_mcp_server.sync.CALL_INTERVAL", 0.0)

    assert cli.main(["--index", path, "sync", "--no-wait"]) == 0

    report = json.loads(capsys.readouterr().out.split("\n}\n")[0] + "\n}")
    assert report["added"] == 23 and report["complete"] is True
    assert BookmarkIndex(path).count() == 23


def test_sync_reports_a_failure(path, monkeypatch, capsys):
    import pinboard

    account = FakePinboard(export_posts())
    account.fail_all_with = [500]
    monkeypatch.setenv("PINBOARD_TOKEN", "user:TOKEN")
    monkeypatch.setattr(pinboard, "Pinboard", lambda token: account)
    monkeypatch.setattr("pinboard_mcp_server.sync.CALL_INTERVAL", 0.0)

    assert cli.main(["--index", path, "sync", "--no-embed"]) == 1
    assert "TOKEN" not in capsys.readouterr().out


def test_embed_without_the_extra_says_how_to_get_it(loaded, capsys):
    capsys.readouterr()

    assert cli.main(["--index", loaded, "embed"]) == 0
    assert "semantic" in capsys.readouterr().err


def test_embed_and_semantic_search(loaded, monkeypatch, capsys):
    embedder = FakeEmbedder()
    monkeypatch.setattr(cli, "default_embedder", lambda: embedder)
    capsys.readouterr()

    assert cli.main(["--index", loaded, "embed"]) == 0
    assert "Embedded 23 bookmarks" in capsys.readouterr().out

    cli.main(
        [
            "--index",
            loaded,
            "search",
            "customer",
            "complaints",
            "--mode",
            "semantic",
            "--limit",
            "4",
        ]
    )
    assert "Patrick Collison" in capsys.readouterr().out
