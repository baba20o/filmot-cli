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


def test_search_raw_serialization_failure_is_the_logged_outcome():
    response = {
        "result": [{"id": "video-id", "hits": [], "score": float("nan")}],
        "totalresultcount": 1,
    }
    with (
        patch("filmot.commands.search.FilmotClient") as client_type,
        patch("filmot.ledger.log_result") as log_result,
    ):
        _search_client(client_type, response)
        result = CliRunner().invoke(
            cli,
            ["search", "alpha", "--session", "study", "--raw"],
        )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["stage"] == "serialize-result"
    logged = log_result.call_args.args[1]
    assert logged.to_raw_dict() == payload
    assert log_result.call_args.kwargs["topic"] == "study"


@pytest.mark.parametrize(
    ("arguments", "environment", "expected_session"),
    [
        ([], {}, None),
        (["--session", "cli-session"], {}, "cli-session"),
        ([], {"FILMOT_SESSION": "env-session"}, "env-session"),
        (
            ["--bulk-download", "bulk-session:0"],
            {},
            "bulk-session",
        ),
        (
            [
                "--session",
                "cli-session",
                "--bulk-download",
                "bulk-session:0",
            ],
            {"FILMOT_SESSION": "env-session"},
            "cli-session",
        ),
        (
            ["--bulk-download", "topic:non-numeric"],
            {},
            "topic:non-numeric",
        ),
    ],
)
def test_search_routes_success_to_resolved_session(
    arguments,
    environment,
    expected_session,
    monkeypatch,
):
    response = {"result": [], "totalresultcount": 0}
    monkeypatch.delenv("FILMOT_SESSION", raising=False)
    with (
        patch("filmot.commands.search.FilmotClient") as client_type,
        patch("filmot.ledger.log_result") as log_result,
        patch("filmot.commands.search._bulk_download_transcripts"),
    ):
        _search_client(client_type, response)
        result = CliRunner().invoke(
            cli,
            ["search", "alpha", *arguments],
            env=environment,
        )

    assert result.exit_code == 0, result.output
    assert log_result.call_args.kwargs["topic"] == expected_session


@pytest.mark.parametrize(
    ("failure", "expected_stage"),
    [
        (OSError("disk unavailable"), "io"),
        (ValueError("bad configuration"), "configuration"),
        (RuntimeError("transport exploded"), "unexpected"),
    ],
)
def test_search_routes_exception_failures_to_session(failure, expected_stage):
    with (
        patch(
            "filmot.commands.search.FilmotClient",
            side_effect=failure,
        ),
        patch("filmot.ledger.log_event") as log_event,
    ):
        result = CliRunner().invoke(
            cli,
            ["search", "alpha", "--session", "failure-session"],
        )

    assert result.exit_code != 0
    assert log_event.call_args.kwargs["topic"] == "failure-session"
    assert log_event.call_args.kwargs["failure_stage"] == expected_stage


def test_search_routes_api_failure_to_environment_session(monkeypatch):
    monkeypatch.delenv("FILMOT_SESSION", raising=False)
    with (
        patch("filmot.commands.search.FilmotClient") as client_type,
        patch("filmot.ledger.log_event") as log_event,
    ):
        _search_client(client_type, {"error": "api rejected"})
        result = CliRunner().invoke(
            cli,
            ["search", "alpha"],
            env={"FILMOT_SESSION": "failure-session"},
        )

    assert result.exit_code != 0
    assert log_event.call_args.kwargs["topic"] == "failure-session"
    assert log_event.call_args.kwargs["status"] == "failed"


def test_search_routes_api_failure_to_inferred_bulk_session(monkeypatch):
    monkeypatch.delenv("FILMOT_SESSION", raising=False)
    with (
        patch("filmot.commands.search.FilmotClient") as client_type,
        patch("filmot.ledger.log_event") as log_event,
    ):
        _search_client(client_type, {"error": "api rejected"})
        result = CliRunner().invoke(
            cli,
            ["search", "alpha", "--bulk-download", "bulk-session:3"],
        )

    assert result.exit_code != 0
    assert log_event.call_args.kwargs["topic"] == "bulk-session"
    assert log_event.call_args.kwargs["status"] == "failed"


def test_search_interrupt_records_resumable_named_session_event():
    with (
        patch(
            "filmot.commands.search.FilmotClient",
            side_effect=KeyboardInterrupt,
        ),
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(
            cli,
            ["search", "alpha", "--session", "interrupted-study"],
        )

    assert result.exit_code != 0
    assert log_result.call_count == 1
    assert log_result.call_args.args[0] == "search"
    outcome = log_result.call_args.args[1]
    assert outcome.status_value == "interrupted"
    assert outcome.errors[0].stage == "user-interrupt"
    assert log_result.call_args.kwargs["topic"] == "interrupted-study"


def test_search_raw_interrupt_is_one_typed_json_result():
    with (
        patch(
            "filmot.commands.search.FilmotClient",
            side_effect=KeyboardInterrupt,
        ),
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(
            cli,
            ["search", "alpha", "--session", "study", "--raw"],
        )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["_filmot"]["status"] == "interrupted"
    assert payload["_filmot"]["errors"][0]["stage"] == "user-interrupt"
    assert payload["query"] == "alpha"
    log_result.assert_called_once()


def test_search_interrupt_after_outcome_log_does_not_duplicate_event():
    response = {"result": [], "totalresultcount": 0}
    with (
        patch("filmot.commands.search.FilmotClient") as client_type,
        patch("filmot.ledger.log_result") as log_result,
        patch("filmot.ledger.log_event") as log_event,
        patch(
            "filmot.commands.search._display_subtitle_results",
            side_effect=KeyboardInterrupt,
        ),
    ):
        _search_client(client_type, response)
        result = CliRunner().invoke(
            cli,
            ["search", "alpha", "--session", "interrupted-study"],
        )

    assert result.exit_code != 0
    log_result.assert_called_once()
    log_event.assert_not_called()


def test_search_help_exposes_session_environment_variable():
    result = CliRunner().invoke(cli, ["search", "--help"])

    assert result.exit_code == 0, result.output
    assert "--session" in result.output
    assert "FILMOT_SESSION" in result.output


def test_search_pagination_usage_error_precedes_client_construction():
    with patch("filmot.commands.search.FilmotClient") as client_type:
        result = CliRunner().invoke(
            cli,
            ["search", "alpha", "--page", "1", "--pages", "2", "--raw"],
        )

    assert result.exit_code == 2
    assert "cannot be combined" in result.output
    client_type.assert_not_called()


def test_search_session_creates_named_ledger(tmp_path, monkeypatch):
    response = {"result": [], "totalresultcount": 0}
    monkeypatch.chdir(tmp_path)
    with patch("filmot.commands.search.FilmotClient") as client_type:
        _search_client(client_type, response)
        result = CliRunner().invoke(
            cli,
            ["search", "alpha", "--session", "Robin Study"],
        )

    assert result.exit_code == 0, result.output
    ledger_path = (
        tmp_path
        / ".filmot_data"
        / "sessions"
        / "robin-study.jsonl"
    )
    event = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert event["kind"] == "search"
    assert event["topic"] == "robin-study"
    assert event["data"]["query"] == "alpha"


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


@pytest.mark.parametrize(
    "response",
    [
        "malformed",
        {"channels": "malformed"},
        {},
        [{"value": "channel-id"}],
    ],
)
def test_channels_raw_rejects_malformed_backend_shape(response):
    with (
        patch("filmot.commands.search.FilmotClient") as client_type,
        patch("filmot.ledger.log_event") as log_event,
    ):
        client = client_type.return_value
        client.search_channels.return_value = response
        result = CliRunner().invoke(
            cli,
            ["channels", "alpha", "--raw"],
        )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["type"] == "FilmotAPIContractError"
    assert payload["_filmot"]["errors"][0]["stage"] == "invalid-response"
    assert log_event.call_count == 1
    assert log_event.call_args.kwargs["failure_stage"] == "invalid-response"


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
