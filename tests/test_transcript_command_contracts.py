"""Typed result contracts for transcript-family CLI commands."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from filmot.cli import cli
from filmot.schemas import ResultStatus


@pytest.fixture
def runner():
    return CliRunner()


def _shared_outcome(mock_log_result, mock_renderer):
    """Return and verify the object shared by ledger and human renderer."""
    outcome = mock_log_result.call_args.args[1]
    assert mock_renderer.call_args.args[0] is outcome
    return outcome


def _route_plan():
    return {
        "mode": "direct-only",
        "connect_timeout_s": 8.0,
        "read_timeout_s": 15.0,
        "route_timeout_s": 30.0,
    }


@pytest.mark.parametrize(
    ("arguments", "expected_options"),
    [
        ([], {"full": False, "timestamps": False, "chunk": None}),
        (["--full"], {"full": True, "timestamps": False, "chunk": None}),
        (
            ["--timestamps"],
            {"full": False, "timestamps": True, "chunk": None},
        ),
        (["--chunk", "2"], {"full": False, "timestamps": False, "chunk": 2.0}),
    ],
)
def test_transcript_human_branches_share_logged_outcome(
    runner,
    arguments,
    expected_options,
):
    payload = {
        "video_id": "vid",
        "language": "en",
        "is_generated": True,
        "duration_seconds": 10,
        "segment_count": 1,
        "segments": [{"start": 0, "text": "hello", "duration": 1}],
        "full_text": "hello",
        "chunks": [
            {"start": 0, "start_formatted": "00:00", "text": "hello"}
        ],
        "chunk_minutes": 2.0,
        "route": "direct",
        "routes_tried": ["direct"],
    }
    with (
        patch("filmot.transcript.get_transcript", return_value=payload),
        patch(
            "filmot.transcript.get_transcript_with_timestamps",
            return_value=payload,
        ),
        patch("filmot.transcript.routing_plan", return_value=_route_plan()),
        patch(
            "filmot.transcript.describe_routing_plan",
            return_value="direct",
        ),
        patch("filmot.ledger.log_result") as mock_log_result,
        patch(
            "filmot.commands.transcript._render_transcript"
        ) as mock_renderer,
    ):
        result = runner.invoke(cli, ["transcript", "vid", *arguments])

    assert result.exit_code == 0, result.output
    outcome = _shared_outcome(mock_log_result, mock_renderer)
    assert outcome.command == "transcript"
    assert outcome.status_value == ResultStatus.COMPLETED.value
    assert outcome.data is payload
    for key, expected in expected_options.items():
        assert mock_renderer.call_args.kwargs[key] == expected


@pytest.mark.parametrize(
    ("matches", "expected_status"),
    [
        ([], ResultStatus.EMPTY),
        (
            [{"timestamp": "00:01", "context": "alpha match"}],
            ResultStatus.COMPLETED,
        ),
    ],
)
def test_transcript_search_shares_result_for_empty_and_matches(
    runner,
    matches,
    expected_status,
):
    payload = {
        "video_id": "vid",
        "query": "alpha",
        "match_count": len(matches),
        "matches": matches,
    }
    with (
        patch(
            "filmot.transcript.search_in_transcript",
            return_value=payload,
        ),
        patch("filmot.ledger.log_result") as mock_log_result,
        patch(
            "filmot.commands.transcript._render_transcript_search"
        ) as mock_renderer,
    ):
        result = runner.invoke(
            cli,
            ["transcript-search", "vid", "alpha"],
        )

    assert result.exit_code == 0, result.output
    outcome = _shared_outcome(mock_log_result, mock_renderer)
    assert outcome.data is payload
    assert outcome.status_value == expected_status.value


def test_channel_download_already_synced_shares_aggregate_and_keeps_start_event(
    runner,
    tmp_path,
):
    downloader = MagicMock()
    downloader._resolve_channel_dir.return_value = (
        "example",
        Path(tmp_path) / "example",
    )
    downloader._load_manifest.return_value = {"videos": {}}
    info = {
        "name": "Example",
        "video_count": 0,
        "subscriber_count": 12,
        "uploads_playlist_id": "uploads",
    }
    with (
        patch(
            "filmot.channel_dl.ChannelDownloader",
            return_value=downloader,
        ),
        patch("filmot.channel_dl.get_channel_info", return_value=info),
        patch("filmot.channel_dl.list_all_video_ids", return_value=[]),
        patch("filmot.ledger.log_event") as mock_log_event,
        patch("filmot.ledger.log_result") as mock_log_result,
        patch(
            "filmot.commands.transcript._render_channel_download"
        ) as mock_renderer,
    ):
        result = runner.invoke(cli, ["channel-download", "UC-example"])

    assert result.exit_code == 0, result.output
    outcome = _shared_outcome(mock_log_result, mock_renderer)
    assert outcome.status_value == ResultStatus.COMPLETED.value
    assert outcome.data["already_synced"] is True
    assert any(
        call.args[0] == "channel_download_start"
        for call in mock_log_event.call_args_list
    )


def test_channel_download_all_failed_preserves_item_event_and_nonzero_exit(
    runner,
    tmp_path,
):
    downloader = MagicMock()
    channel_dir = Path(tmp_path) / "example"
    downloader._resolve_channel_dir.return_value = ("example", channel_dir)
    downloader._load_manifest.return_value = {"videos": {}}
    info = {
        "name": "Example",
        "video_count": 1,
        "subscriber_count": 12,
        "uploads_playlist_id": "uploads",
    }
    videos = [{
        "video_id": "vid",
        "title": "Video",
        "published_at": "2026-01-01T00:00:00",
    }]
    with (
        patch(
            "filmot.channel_dl.ChannelDownloader",
            return_value=downloader,
        ),
        patch("filmot.channel_dl.get_channel_info", return_value=info),
        patch("filmot.channel_dl.list_all_video_ids", return_value=videos),
        patch(
            "filmot.transcript.get_transcript",
            return_value={
                "error": "transport failed",
                "error_type": "ProxyError",
                "routes_tried": ["direct"],
            },
        ),
        patch("filmot.transcript.routing_plan", return_value=_route_plan()),
        patch(
            "filmot.transcript.describe_routing_plan",
            return_value="direct",
        ),
        patch("filmot.ledger.log_event") as mock_log_event,
        patch("filmot.ledger.log_result") as mock_log_result,
        patch(
            "filmot.commands.transcript._render_channel_download"
        ) as mock_renderer,
    ):
        result = runner.invoke(
            cli,
            ["channel-download", "UC-example", "--delay", "0"],
        )

    assert result.exit_code != 0
    outcome = _shared_outcome(mock_log_result, mock_renderer)
    assert outcome.status_value == ResultStatus.FAILED.value
    assert any(
        call.args[0] == "channel_download_item"
        and call.kwargs["status"] == "failed"
        for call in mock_log_event.call_args_list
    )


@pytest.mark.parametrize(
    ("channels", "expected_status"),
    [
        ([], ResultStatus.EMPTY),
        (
            [{
                "name": "Example",
                "downloaded": 2,
                "failed": 0,
                "total_videos": 2,
                "last_updated": "2026-01-01T00:00:00",
                "slug": "example",
            }],
            ResultStatus.COMPLETED,
        ),
    ],
)
def test_channel_status_inventory_shares_logged_outcome(
    runner,
    channels,
    expected_status,
):
    downloader = MagicMock()
    downloader.get_downloaded_channels.return_value = channels
    with (
        patch(
            "filmot.channel_dl.ChannelDownloader",
            return_value=downloader,
        ),
        patch("filmot.ledger.log_result") as mock_log_result,
        patch(
            "filmot.commands.transcript._render_channel_status"
        ) as mock_renderer,
    ):
        result = runner.invoke(cli, ["channel-status"])

    assert result.exit_code == 0, result.output
    outcome = _shared_outcome(mock_log_result, mock_renderer)
    assert outcome.status_value == expected_status.value
    assert outcome.data["channels"] is channels


def test_channel_search_shares_limited_results_and_full_hit_count(runner):
    downloader = MagicMock()
    downloader.search_corpus.return_value = [
        {
            "title": "First",
            "video_id": "one",
            "published_at": "2026-01-01T00:00:00",
            "match_count": 3,
            "snippets": ["alpha"],
        },
        {
            "title": "Second",
            "video_id": "two",
            "published_at": "2026-01-02T00:00:00",
            "match_count": 2,
            "snippets": ["alpha"],
        },
    ]
    with (
        patch(
            "filmot.channel_dl.ChannelDownloader",
            return_value=downloader,
        ),
        patch("filmot.ledger.log_result") as mock_log_result,
        patch(
            "filmot.commands.transcript._render_channel_search"
        ) as mock_renderer,
    ):
        result = runner.invoke(
            cli,
            ["channel-search", "example", "alpha", "--limit", "1"],
        )

    assert result.exit_code == 0, result.output
    outcome = _shared_outcome(mock_log_result, mock_renderer)
    assert outcome.status_value == ResultStatus.COMPLETED.value
    assert len(outcome.data["results"]) == 1
    assert outcome.data["summary"] == {
        "videos": 1,
        "hits": 5,
        "limit": 1,
    }


def test_download_defers_aggregate_messages_to_shared_outcome(runner):
    def fake_download(results, spec, output, **kwargs):
        output.print("item progress")
        output.print("\n[bold]Complete:[/bold] 1 saved, 0 skipped, 0 failed")
        output.print("[dim]View with: filmot library list topic[/dim]")
        return {
            "selected": 1,
            "saved": 1,
            "skipped": 0,
            "failed": 0,
            "deduped": 0,
        }

    with (
        patch(
            "filmot.commands.transcript._bulk_download_transcripts",
            side_effect=fake_download,
        ),
        patch("filmot.ledger.log_result") as mock_log_result,
        patch(
            "filmot.commands.transcript._render_download"
        ) as mock_renderer,
    ):
        result = runner.invoke(
            cli,
            ["download", "--topic", "topic", "--count", "1"],
            input='{"result":[{"id":"vid"}]}',
        )

    assert result.exit_code == 0, result.output
    assert "item progress" in result.output
    assert "Complete:" not in result.output
    outcome = _shared_outcome(mock_log_result, mock_renderer)
    assert outcome.status_value == ResultStatus.COMPLETED.value
    assert outcome.data["human_messages"] == [
        "\n[bold]Complete:[/bold] 1 saved, 0 skipped, 0 failed",
        "[dim]View with: filmot library list topic[/dim]",
    ]
