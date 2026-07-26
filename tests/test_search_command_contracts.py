"""Shared result/event contracts for search-domain commands."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from filmot.cli import cli


def _search_client(mock_client_type, response):
    client = mock_client_type.return_value
    client.last_cache_hit = False
    client.last_query_rewrite = None
    client.search_subtitles.return_value = response
    client.search_subtitles_all.return_value = response
    return client


def test_search_partial_page_error_is_in_raw_and_ledger_outcome():
    response = {
        "result": [{"id": "video-id", "hits": []}],
        "totalresultcount": 5,
        "pages_fetched": 1,
        "partial": True,
        "page_error": "page two timed out",
    }
    with (
        patch("filmot.commands.search.FilmotClient") as client_type,
        patch("filmot.ledger.log_result") as log_result,
    ):
        _search_client(client_type, response)
        result = CliRunner().invoke(cli, ["search", "alpha", "--raw"])

    assert result.exit_code == 0, result.output
    raw = json.loads(result.stdout)
    assert raw["_filmot"]["status"] == "partial"
    assert raw["_filmot"]["errors"] == [{
        "type": "PaginationError",
        "message": "page two timed out",
        "stage": "pagination",
        "details": {
            "pages_fetched": 1,
            "candidates_fetched": 1,
        },
    }]
    logged = log_result.call_args.args[1]
    assert logged.to_raw_dict() == raw


def test_export_logs_and_renders_the_same_partial_outcome():
    response = {
        "result": [{"id": "video-id", "hits": []}],
        "totalresultcount": 10,
        "pages_fetched": 1,
        "partial": True,
        "page_error": "page two failed",
    }
    with (
        patch("filmot.commands.search.FilmotClient") as client_type,
        patch(
            "filmot.export.export_json",
            return_value=Path("artifact.json"),
        ),
        patch("filmot.ledger.log_result") as log_result,
        patch(
            "filmot.commands.search._render_export_result"
        ) as renderer,
    ):
        _search_client(client_type, response)
        result = CliRunner().invoke(
            cli,
            ["export", "alpha", "-o", "artifact.json", "--pages", "2"],
        )

    assert result.exit_code == 0, result.output
    outcome = log_result.call_args.args[1]
    assert renderer.call_args.args[0] is outcome
    assert outcome.status_value == "partial"
    assert outcome.errors[0].stage == "pagination"
    assert outcome.data["output"] == "artifact.json"
    compact = log_result.call_args.kwargs["data"]
    assert compact["results"] == 1
    assert compact["partial"] is True
    assert "result" not in compact


def test_search_all_logs_and_renders_the_same_partial_outcome():
    response = {
        "result": [{"id": "video-id", "hits": []}],
        "totalresultcount": 20,
        "pages_fetched": 2,
        "partial": True,
        "page_error": "page three failed",
    }
    with (
        patch("filmot.commands.search.FilmotClient") as client_type,
        patch("filmot.ledger.log_result") as log_result,
        patch(
            "filmot.commands.search._render_search_all_result"
        ) as renderer,
    ):
        _search_client(client_type, response)
        result = CliRunner().invoke(cli, ["search-all", "alpha"])

    assert result.exit_code == 0, result.output
    outcome = log_result.call_args.args[1]
    assert renderer.call_args.args[0] is outcome
    assert outcome.status_value == "partial"
    assert outcome.errors[0].message == "page three failed"
    assert outcome.data["scope"]["pages_fetched"] == 2
    compact = log_result.call_args.kwargs["data"]
    assert compact["results"] == 1
    assert compact["page_error"] == "page three failed"
    assert "result" not in compact


def test_yt_search_logs_and_renders_the_same_outcome():
    videos = [{
        "video_id": "video-id",
        "title": "Recent result",
        "channel_title": "Channel",
        "published_at": "2026-07-25T00:00:00Z",
        "views": 5,
        "duration": "PT1M",
    }]
    with (
        patch("filmot.youtube_search.validate_youtube_api"),
        patch("filmot.youtube_search.search_recent", return_value=videos),
        patch("filmot.ledger.log_result") as log_result,
        patch(
            "filmot.commands.search._render_yt_search_result"
        ) as renderer,
    ):
        result = CliRunner().invoke(
            cli,
            ["yt-search", "alpha", "--days", "3", "--region", "US"],
        )

    assert result.exit_code == 0, result.output
    outcome = log_result.call_args.args[1]
    assert renderer.call_args.args[0] is outcome
    assert outcome.status_value == "completed"
    assert outcome.data["videos"] == videos
    assert outcome.data["filters"]["region"] == "US"
    compact = log_result.call_args.kwargs["data"]
    assert compact["results"] == 1
    assert "videos" not in compact


@pytest.mark.parametrize(
    ("command", "method"),
    [
        (["video", "video-id"], "get_videos"),
        (["channels", "channel"], "search_channels"),
    ],
)
@pytest.mark.parametrize(
    ("failure_stage", "failure"),
    [
        ("api", "api"),
        ("configuration", ValueError("bad configuration")),
        ("unexpected", RuntimeError("transport exploded")),
    ],
)
def test_metadata_failures_write_one_structured_event(
    command,
    method,
    failure_stage,
    failure,
):
    with (
        patch("filmot.commands.search.FilmotClient") as client_type,
        patch("filmot.ledger.log_event") as log_event,
    ):
        client = client_type.return_value
        if failure_stage == "configuration":
            client_type.side_effect = failure
        elif failure_stage == "api":
            getattr(client, method).return_value = {"error": "api rejected"}
        else:
            getattr(client, method).side_effect = failure

        result = CliRunner().invoke(cli, command)

    assert result.exit_code != 0
    assert log_event.call_count == 1
    event = log_event.call_args
    assert event.args[0] == command[0]
    assert event.kwargs["status"] == "failed"
    assert event.kwargs["failure_stage"] == failure_stage
    assert event.kwargs["error"]


def test_export_failure_keeps_structured_failure_event():
    response = {
        "result": [{"id": "video-id"}],
        "totalresultcount": 1,
    }
    with (
        patch("filmot.commands.search.FilmotClient") as client_type,
        patch("filmot.export.export_json", side_effect=OSError("disk full")),
        patch("filmot.ledger.log_event") as log_event,
    ):
        _search_client(client_type, response)
        result = CliRunner().invoke(
            cli,
            ["export", "alpha", "-o", "artifact.json"],
        )

    assert result.exit_code != 0
    assert log_event.call_count == 1
    assert log_event.call_args.kwargs["status"] == "failed"
    assert log_event.call_args.kwargs["failure_stage"] == "export"


def test_yt_search_configuration_failure_is_structured():
    with (
        patch(
            "filmot.youtube_search.validate_youtube_api",
            side_effect=ValueError("missing key"),
        ),
        patch("filmot.ledger.log_event") as log_event,
    ):
        result = CliRunner().invoke(cli, ["yt-search", "alpha"])

    assert result.exit_code != 0
    assert log_event.call_count == 1
    assert log_event.call_args.args[0] == "yt-search"
    assert log_event.call_args.kwargs["status"] == "failed"
    assert log_event.call_args.kwargs["failure_stage"] == "configuration"
