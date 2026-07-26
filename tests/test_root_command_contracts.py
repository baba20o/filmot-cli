"""Typed-outcome contracts for the small commands retained by the root CLI."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from filmot.cli import (
    _render_cache_result,
    cli,
)


def test_machine_cache_status_uses_typed_renderer_without_project_ledger():
    cache = MagicMock()
    cache.stats.return_value = {
        "total_entries": 3,
        "valid_entries": 2,
        "expired_entries": 1,
        "size_mb": 0.25,
        "ttl_seconds": 3600,
        "cache_dir": "/machine/cache",
    }

    with (
        patch("filmot.cache.get_cache", return_value=cache),
        patch(
            "filmot.cli._render_cache_result",
            wraps=_render_cache_result,
        ) as render,
        patch("filmot.ledger.log_event") as legacy_log,
    ):
        result = CliRunner().invoke(cli, ["cache"])

    assert result.exit_code == 0, result.output
    outcome = render.call_args.args[0]
    assert outcome.command == "cache"
    assert outcome.status_value == "completed"
    assert outcome.data["stats"]["valid_entries"] == 2
    legacy_log.assert_not_called()


def test_batch_ledger_and_human_summary_share_one_outcome(tmp_path):
    query_file = tmp_path / "queries.txt"
    query_file.write_text("typed contract\n", encoding="utf-8")
    processor = MagicMock()
    processor.load_queries_from_file.return_value = [object()]
    processor.stats.return_value = {
        "successful": 1,
        "failed": 0,
        "total_results": 4,
        "avg_duration_ms": 12.0,
    }

    with (
        patch("filmot.cli.FilmotClient"),
        patch("filmot.batch.BatchProcessor", return_value=processor),
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(cli, ["batch", str(query_file)])

    assert result.exit_code == 0, result.output
    outcome = log_result.call_args.args[1]
    assert outcome.command == "batch"
    assert outcome.status_value == "completed"
    assert outcome.data["stats"] == processor.stats.return_value
    assert "Successful: 1" in result.output


def test_watchlist_inventory_logs_the_same_typed_data_it_renders():
    watchlist = MagicMock()
    watchlist.get_watchlist.return_value = [{
        "video_id": "typed-video",
        "title": "Typed result",
        "channel_name": "Channel",
        "watched": False,
        "added_at": "2026-07-25T12:00:00",
    }]
    watchlist.stats.return_value = {
        "total_videos": 1,
        "watched": 0,
        "unwatched": 1,
    }

    with (
        patch("filmot.watchlist.get_watchlist", return_value=watchlist),
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(cli, ["watchlist", "list"])

    assert result.exit_code == 0, result.output
    outcome = log_result.call_args.args[1]
    assert outcome.command == "watchlist-list"
    assert outcome.data["items"][0]["video_id"] == "typed-video"
    assert "Typed result" in result.output
