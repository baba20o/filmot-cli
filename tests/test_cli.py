"""Tests for the filmot CLI commands (click integration tests)."""

import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from filmot.cli import (
    _candidate_assessment,
    cli,
    _extract_probe_terms,
    _find_probe_pairs,
    _research_query_ladder,
)
from filmot.commands.research import _topic_relationship_evidence


@pytest.fixture
def runner():
    return CliRunner()


class TestCLIEntryPoint:
    """Basic smoke tests for the CLI."""

    def test_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Filmot" in result.output or "filmot" in result.output.lower()

    def test_version(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.4.0" in result.output

    def test_config_is_read_only_and_reports_storage_scopes(
        self, monkeypatch, tmp_path, runner
    ):
        import filmot.config as app_config
        from filmot.paths import (
            config_file,
            project_data_dir,
            proxy_health_db,
            user_cache_dir,
            user_config_dir,
            user_state_dir,
        )

        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.chdir(project)
        secret = "super-secret-api-key-1234"
        monkeypatch.setattr(app_config, "API_KEY", secret)

        result = runner.invoke(cli, ["config"])

        assert result.exit_code == 0, result.output
        assert "Filmot API Key: configured" in result.output
        assert "YouTube API Key:" in result.output
        assert secret not in result.output
        assert secret[:8] not in result.output
        assert secret[-4:] not in result.output
        for path in (
            project_data_dir(),
            user_config_dir(),
            config_file(),
            user_state_dir(),
            proxy_health_db(),
            user_cache_dir(),
        ):
            assert str(path) in result.output
        assert not (project / ".filmot_data").exists()
        assert not user_config_dir().exists()
        assert not user_state_dir().exists()
        assert not user_cache_dir().exists()

    def test_search_help(self, runner):
        result = runner.invoke(cli, ["search", "--help"])
        assert result.exit_code == 0
        assert "--bulk-download" in result.output
        assert "--fallback" in result.output
        assert "loose transcript-wide implicit AND" in result.output
        help_lines = [line.strip() for line in result.output.splitlines()]
        assert any(
            line.startswith("filmot search '\"machine learning\"'")
            for line in help_lines
        )
        assert any(
            line.startswith("filmot search 'OpenAI|Anthropic'")
            for line in help_lines
        )
        assert "--max-hits" in result.output

    def test_transcript_help(self, runner):
        result = runner.invoke(cli, ["transcript", "--help"])
        assert result.exit_code == 0
        assert "--fallback" in result.output

    @pytest.mark.parametrize(
        "arguments",
        [
            ["search", "alpha", "--page", "0"],
            ["search-all", "alpha", "--pages", "0"],
            ["search-all", "alpha", "--max-results", "-1"],
            ["export", "alpha", "--output", "out.json", "--pages", "0"],
            ["download", "--topic", "topic", "--count", "0"],
        ],
    )
    def test_pagination_and_batch_ranges_reject_nonpositive_values(
        self, runner, arguments
    ):
        result = runner.invoke(cli, arguments, input="{}")
        assert result.exit_code != 0
        assert "not in the range" in result.output


def _mock_client(mock_client_type, response):
    client = mock_client_type.return_value
    client.last_query_rewrite = None
    client.last_cache_hit = False
    client.search_subtitles.return_value = response
    client.search_subtitles_all.return_value = response
    return client


class TestSearchContracts:
    @patch("filmot.ledger.log_result")
    @patch("filmot.commands.search.FilmotClient")
    def test_raw_stdout_is_one_json_value_with_client_sort(
        self, mock_client_type, mock_log, runner
    ):
        client = _mock_client(mock_client_type, {
            "result": [{
                "id": "vid",
                "duration": 60,
                "hits": [
                    {"start": 1, "token": "alpha"},
                    {"start": 2, "token": "alpha"},
                ],
            }],
            "totalresultcount": 10,
        })

        result = runner.invoke(
            cli,
            [
                "search", "alpha", "--raw", "--sort", "density",
                "--min-matches", "1", "--max-hits", "1",
            ],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["result"][0]["id"] == "vid"
        assert len(payload["result"][0]["hits"]) == 1
        assert payload["scope"]["api_total"] == 10
        assert payload["_filmot"] == {
            "schema": "filmot.result/v1",
            "command": "search",
            "status": "completed",
            "errors": [],
            "warnings": [],
        }
        assert "Sorted by density" not in result.stdout
        assert mock_log.called
        logged = mock_log.call_args.args[1]
        assert logged.to_raw_dict() == payload
        client.search_subtitles.assert_called_once()

    @patch("filmot.ledger.log_result")
    @patch("filmot.commands.search._display_subtitle_results", side_effect=BrokenPipeError)
    @patch("filmot.commands.search.FilmotClient")
    def test_search_logs_before_pipe_sensitive_render(
        self, mock_client_type, mock_display, mock_log, runner
    ):
        _mock_client(mock_client_type, {
            "result": [{"id": "vid", "hits": []}],
            "totalresultcount": 1,
        })

        result = runner.invoke(cli, ["search", "alpha"])

        assert result.exit_code == 0
        assert result.exception is None
        mock_log.assert_called_once()

    @patch("filmot.ledger.log_event")
    @patch("filmot.commands.search.FilmotClient")
    def test_api_error_is_nonzero(self, mock_client_type, mock_log, runner):
        _mock_client(mock_client_type, {"error": "synthetic failure"})

        result = runner.invoke(cli, ["search", "alpha"])

        assert result.exit_code != 0
        assert "synthetic failure" in result.output
        assert mock_log.call_args.kwargs["status"] == "failed"

    @patch("filmot.ledger.log_event")
    @patch("filmot.commands.search.FilmotClient")
    def test_pages_use_paginated_candidate_pool(
        self, mock_client_type, mock_log, runner
    ):
        client = _mock_client(mock_client_type, {
            "result": [],
            "totalresultcount": 500,
            "pages_fetched": 3,
        })

        result = runner.invoke(
            cli,
            ["search", "alpha", "--pages", "3", "--candidate-pool", "120", "--raw"],
        )

        assert result.exit_code == 0, result.output
        client.search_subtitles_all.assert_called_once()
        assert client.search_subtitles_all.call_args.kwargs["max_pages"] == 3
        assert client.search_subtitles_all.call_args.kwargs["max_results"] == 120

    @patch("filmot.commands.search.FilmotClient")
    def test_explicit_page_cannot_be_silently_ignored_by_candidate_pool(
        self, mock_client_type, runner
    ):
        result = runner.invoke(
            cli,
            ["search", "alpha", "--page", "3", "--candidate-pool", "100"],
        )

        assert result.exit_code != 0
        assert "--page cannot be combined" in result.output
        mock_client_type.return_value.search_subtitles.assert_not_called()
        mock_client_type.return_value.search_subtitles_all.assert_not_called()

    @patch("filmot.commands.search.FilmotClient")
    def test_unresolved_fuzzy_channel_fails_closed(
        self, mock_client_type, runner
    ):
        client = _mock_client(mock_client_type, {"result": []})
        client.search_channels.return_value = []

        result = runner.invoke(
            cli, ["search", "electricity", "--channel", "No Such Primary Source"]
        )

        assert result.exit_code != 0
        assert "No channels resolved" in result.output
        client.search_subtitles.assert_not_called()

    @patch("filmot.ledger.log_event")
    @patch("filmot.commands.search.FilmotClient")
    def test_fuzzy_channel_is_resolved_to_explicit_id(
        self, mock_client_type, mock_log, runner
    ):
        client = _mock_client(mock_client_type, {
            "result": [{
                "id": "vid",
                "channelid": "UC_PRIMARY",
                "hits": [],
            }],
            "totalresultcount": 1,
        })
        client.search_channels.return_value = [{
            "label": "Primary Lab",
            "value": "UC_PRIMARY",
        }]

        result = runner.invoke(
            cli, ["search", "electricity", "--channel", "Primary Lab", "--raw"]
        )

        assert result.exit_code == 0, result.output
        assert (
            client.search_subtitles.call_args.kwargs["channel_id"]
            == "UC_PRIMARY"
        )
        payload = json.loads(result.stdout)
        assert payload["effective_filters"]["resolved_channels"] == [{
            "id": "UC_PRIMARY",
            "name": "Primary Lab",
        }]

    @patch("filmot.commands.search.FilmotClient")
    def test_fuzzy_channel_rejects_unverifiable_result_without_channel_id(
        self, mock_client_type, runner
    ):
        client = _mock_client(mock_client_type, {
            "result": [{"id": "vid", "hits": []}],
            "totalresultcount": 1,
        })
        client.search_channels.return_value = [{
            "label": "Primary Lab",
            "value": "UC_PRIMARY",
        }]

        result = runner.invoke(
            cli, ["search", "electricity", "--channel", "Primary Lab"]
        )

        assert result.exit_code != 0
        assert "without an ID" in result.output

    @patch("filmot.ledger.log_event")
    @patch("filmot.commands.search.FilmotClient")
    def test_channel_validation_failure_is_logged(
        self, mock_client_type, mock_log, runner
    ):
        _mock_client(mock_client_type, {
            "result": [{
                "id": "vid",
                "channelid": "UC_OUTSIDE",
                "hits": [],
            }],
            "totalresultcount": 1,
        })

        result = runner.invoke(
            cli,
            ["search", "alpha", "--channel-id", "UC_ALLOWED"],
        )

        assert result.exit_code != 0
        failure = mock_log.call_args
        assert failure.args[0] == "search"
        assert failure.kwargs["status"] == "failed"
        assert failure.kwargs["failure_stage"] == "channel_validation"

    @patch("filmot.ledger.log_event")
    @patch("filmot.commands.search.FilmotClient")
    def test_literal_low_result_search_prints_inflection_hint(
        self, mock_client_type, mock_log, runner
    ):
        _mock_client(mock_client_type, {
            "result": [{"id": "vid", "hits": []}],
            "totalresultcount": 1,
        })

        result = runner.invoke(
            cli, ["search", '"sparse autoencoders" NEAR/25 "superposition"']
        )

        assert result.exit_code == 0, result.output
        assert "singular/plural" in result.output


class TestMetadataRawContracts:
    @patch("filmot.ledger.log_result")
    @patch("filmot.commands.search.FilmotClient")
    def test_video_raw_uses_shared_result_contract(
        self, mock_client_type, mock_log, runner
    ):
        client = mock_client_type.return_value
        client.last_cache_hit = False
        client.get_videos.return_value = [
            {"id": "vid", "title": "Typed metadata"}
        ]

        result = runner.invoke(cli, ["video", "vid", "--raw"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["video_ids"] == "vid"
        assert payload["videos"][0]["title"] == "Typed metadata"
        assert payload["_filmot"]["command"] == "video"
        assert payload["_filmot"]["schema"] == "filmot.result/v1"
        assert mock_log.call_args.args[1].to_raw_dict() == payload

    @patch("filmot.ledger.log_result")
    @patch("filmot.commands.search.FilmotClient")
    def test_video_raw_unwraps_contract_result_list(
        self, mock_client_type, mock_log, runner
    ):
        client = mock_client_type.return_value
        client.last_cache_hit = False
        client.get_videos.return_value = {
            "result": [
                {"id": "one", "title": "First"},
                {"id": "two", "title": "Second"},
            ]
        }

        result = runner.invoke(cli, ["video", "one,two", "--raw"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert [item["id"] for item in payload["videos"]] == ["one", "two"]
        assert mock_log.call_args.kwargs["data"]["results"] == 2

    @patch("filmot.ledger.log_result")
    @patch("filmot.commands.search.FilmotClient")
    def test_video_raw_serialization_failure_is_the_logged_outcome(
        self, mock_client_type, mock_log, runner
    ):
        client = mock_client_type.return_value
        client.last_cache_hit = False
        client.get_videos.return_value = [{"id": "vid", "score": float("nan")}]

        result = runner.invoke(cli, ["video", "vid", "--raw"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["_filmot"]["errors"][0]["stage"] == "serialize-result"
        assert mock_log.call_args.args[1].to_raw_dict() == payload

    @patch("filmot.ledger.log_result")
    @patch("filmot.commands.search.FilmotClient")
    def test_channels_raw_uses_shared_result_contract(
        self, mock_client_type, mock_log, runner
    ):
        client = mock_client_type.return_value
        client.last_cache_hit = False
        client.search_channels.return_value = [
            {"value": "channel-id", "label": "Typed channel"}
        ]

        result = runner.invoke(cli, ["channels", "typed", "--raw"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["query"] == "typed"
        assert payload["channels"][0]["value"] == "channel-id"
        assert payload["_filmot"]["command"] == "channels"
        assert payload["_filmot"]["schema"] == "filmot.result/v1"
        assert mock_log.call_args.args[1].to_raw_dict() == payload


class TestTranscriptRawContract:
    @patch("filmot.proxy_pool.get_pool", return_value=None)
    @patch("filmot.ledger.log_event")
    @patch("filmot.transcript.get_transcript", return_value=None)
    def test_raw_non_object_backend_response_is_one_json_failure(
        self, mock_get_transcript, mock_log, mock_pool, runner
    ):
        result = runner.invoke(cli, ["transcript", "video-id", "--raw"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["_filmot"]["errors"][0]["stage"] == "validate-response"
        assert mock_log.call_args.kwargs["failure_stage"] == "validate-response"

    @patch("filmot.proxy_pool.get_pool", return_value=None)
    @patch("filmot.ledger.log_event")
    @patch(
        "filmot.transcript.get_transcript",
        return_value={"video_id": "video-id", "full_text": None, "segments": []},
    )
    def test_raw_malformed_mapping_response_is_one_json_failure(
        self, mock_get_transcript, mock_log, mock_pool, runner
    ):
        result = runner.invoke(cli, ["transcript", "video-id", "--raw"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["_filmot"]["errors"][0]["stage"] == "validate-response"

    @patch("filmot.proxy_pool.get_pool", return_value=None)
    @patch("filmot.ledger.log_event")
    @patch(
        "filmot.transcript.get_transcript",
        return_value={
            "video_id": "other-id",
            "language": "en",
            "full_text": "hello",
            "segments": [],
        },
    )
    def test_raw_mismatched_video_identity_is_one_json_failure(
        self, mock_get_transcript, mock_log, mock_pool, runner
    ):
        result = runner.invoke(cli, ["transcript", "video-id", "--raw"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["_filmot"]["errors"][0]["stage"] == "validate-response"
        assert "does not match" in payload["_filmot"]["errors"][0]["message"]

    @patch("filmot.proxy_pool.get_pool", return_value=None)
    @patch("filmot.ledger.log_event")
    @patch(
        "filmot.transcript.get_transcript",
        return_value={
            "video_id": "video-id",
            "full_text": "hello",
            "segments": [],
        },
    )
    def test_missing_language_fails_before_completed_log(
        self, mock_get_transcript, mock_log, mock_pool, runner
    ):
        result = runner.invoke(cli, ["transcript", "video-id", "--raw"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["_filmot"]["errors"][0]["stage"] == "validate-response"
        assert "language" in payload["_filmot"]["errors"][0]["message"]

    @pytest.mark.parametrize("full_text", ["", "   \n\t"])
    @patch("filmot.proxy_pool.get_pool", return_value=None)
    @patch("filmot.ledger.log_event")
    @patch("filmot.transcript.get_transcript")
    def test_empty_transcript_is_invalid_before_logging_or_side_effects(
        self,
        mock_get_transcript,
        mock_log,
        mock_pool,
        runner,
        full_text,
    ):
        mock_get_transcript.return_value = {
            "video_id": "video-id",
            "language": "en",
            "full_text": full_text,
            "segments": [],
        }

        result = runner.invoke(cli, ["transcript", "video-id", "--raw"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        error = payload["_filmot"]["errors"][0]
        assert error["stage"] == "validate-response"
        assert "non-empty" in error["message"]
        assert mock_log.call_args.kwargs["failure_stage"] == "validate-response"

    @pytest.mark.parametrize("value", ["nan", "inf"])
    @patch("filmot.transcript.get_transcript")
    def test_non_finite_chunk_fails_before_fetch(
        self, mock_get_transcript, runner, value
    ):
        result = runner.invoke(
            cli,
            ["transcript", "video-id", "--chunk", value, "--raw"],
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["_filmot"]["errors"][0]["stage"] == "validate-options"
        mock_get_transcript.assert_not_called()

    @patch("filmot.proxy_pool.get_pool", return_value=None)
    @patch("filmot.ledger.log_result")
    @patch("filmot.library.get_library")
    @patch(
        "filmot.transcript.get_transcript",
        return_value={
            "video_id": "video-id",
            "language": "en",
            "full_text": "hello",
            "segments": [],
            "score": float("nan"),
        },
    )
    def test_raw_serialization_failure_logs_then_exits_before_library_save(
        self,
        mock_get_transcript,
        mock_get_library,
        mock_log_result,
        mock_pool,
        runner,
    ):
        result = runner.invoke(
            cli,
            ["transcript", "video-id", "--save-to", "science", "--raw"],
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["_filmot"]["errors"][0]["stage"] == "serialize-result"
        assert mock_log_result.call_args.args[1].to_raw_dict() == payload
        mock_get_library.assert_not_called()

    @patch("filmot.proxy_pool.get_pool", return_value=None)
    @patch("filmot.ledger.log_result")
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.transcript.get_transcript")
    @patch("filmot.commands.transcript.FilmotClient")
    def test_raw_save_confirmation_stays_off_stdout(
        self,
        mock_client_type,
        mock_get_transcript,
        mock_proxy_configured,
        mock_get_library,
        mock_log_event,
        mock_log_result,
        mock_pool,
        runner,
    ):
        mock_get_transcript.return_value = {
            "video_id": "vid",
            "full_text": "hello",
            "segments": [
                {"text": "hello", "start": 0.0, "duration": 1.0}
            ],
            "language": "en",
            "segment_count": 1,
            "duration_seconds": 1,
            "source": "youtube",
            "route": "direct",
            "routes_tried": ["direct"],
        }
        library = mock_get_library.return_value
        library.exists.return_value = False
        library.save.return_value = "/tmp/vid.json"
        mock_client_type.return_value.get_videos.return_value = {
            "title": "Title",
            "channelname": "Channel",
        }

        result = runner.invoke(
            cli, ["transcript", "vid", "--raw", "--save-to", "topic"]
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["video_id"] == "vid"
        assert payload["_filmot"] == {
            "schema": "filmot.result/v1",
            "command": "transcript",
            "status": "completed",
            "errors": [],
            "warnings": [],
        }
        assert "Saved to library" not in result.stdout
        metadata = library.save.call_args.kwargs["metadata"]
        assert metadata["source"] == "youtube"
        assert metadata["route"] == "direct"
        assert metadata["routes_tried"] == ["direct"]
        assert library.save.call_args.kwargs["segments"] == [
            {"text": "hello", "start": 0.0, "duration": 1.0}
        ]
        assert any(
            item.kwargs.get("topic") == "topic"
            for item in mock_log_event.call_args_list
        )
        saved_event = next(
            item
            for item in mock_log_event.call_args_list
            if item.args[0] == "transcript_save"
            and item.kwargs.get("status") == "saved"
        )
        assert saved_event.kwargs["title"] == "Title"
        assert saved_event.kwargs["channel"] == "Channel"
        logged = mock_log_result.call_args.args[1]
        assert logged.to_raw_dict() == payload
        assert "full_text" not in mock_log_result.call_args.kwargs["data"]

    @patch("filmot.proxy_pool.get_pool", return_value=None)
    @patch("filmot.ledger.log_result")
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.transcript.get_transcript")
    def test_raw_library_save_failure_is_one_json_error(
        self,
        mock_get_transcript,
        mock_get_library,
        mock_log,
        mock_log_result,
        mock_pool,
        runner,
    ):
        mock_get_transcript.return_value = {
            "video_id": "vid",
            "full_text": "hello",
            "segments": [],
            "language": "en",
            "route": "direct",
        }
        library = mock_get_library.return_value
        library.exists.return_value = False
        library.save.side_effect = OSError("disk full")

        result = runner.invoke(
            cli, ["transcript", "vid", "--raw", "--save-to", "topic"]
        )

        assert result.exit_code != 0
        payload = json.loads(result.stdout)
        assert "disk full" in payload["error"]
        assert payload["_filmot"]["errors"][0]["stage"] == "save-library"
        assert mock_log_result.call_args.args[1].to_raw_dict() == payload
        failed_save = [
            call
            for call in mock_log.call_args_list
            if call.args[0] == "transcript_save"
            and call.kwargs.get("status") == "failed"
        ]
        assert len(failed_save) == 1

    @patch("filmot.proxy_pool.get_pool", return_value=None)
    @patch(
        "filmot.transcript.get_transcript_with_timestamps",
        return_value={
            "video_id": "video-id",
            "language": "en",
            "full_text": "caption text",
            "segments": [
                {"text": "caption text", "start": 0.0, "duration": 1.0}
            ],
            "segment_count": 1,
            "chunks": [
                {
                    "start": 0.0,
                    "start_formatted": "0:00",
                    "text": "chunked presentation",
                }
            ],
            "chunk_minutes": 5.0,
        },
    )
    def test_chunk_takes_precedence_for_human_and_plain_file_output(
        self, mock_chunked, mock_pool, runner, tmp_path
    ):
        rendered = runner.invoke(
            cli,
            ["transcript", "video-id", "--chunk", "5", "--timestamps"],
        )
        destination = tmp_path / "chunked.txt"
        written = runner.invoke(
            cli,
            [
                "transcript",
                "video-id",
                "--chunk",
                "5",
                "--timestamps",
                "--output",
                str(destination),
            ],
        )

        assert rendered.exit_code == 0, rendered.output
        assert "chunked presentation" in rendered.output
        assert written.exit_code == 0, written.output
        assert destination.read_text(encoding="utf-8") == (
            "[0:00] chunked presentation\n"
        )

    @pytest.mark.parametrize(
        "options",
        [
            ["--chunk", "5", "--fallback"],
            ["--grep", "alpha", "--save-to", "topic"],
            ["--grep", "alpha", "--output", "transcript.txt"],
            ["--grep", "alpha", "--full"],
            ["--grep", "alpha", "--timestamps"],
        ],
    )
    @patch("filmot.transcript.get_transcript")
    def test_incompatible_modes_fail_before_fetch(
        self, mock_transcript, runner, options
    ):
        result = runner.invoke(cli, ["transcript", "video-id", *options])

        assert result.exit_code != 0
        assert "cannot be combined" in result.output
        mock_transcript.assert_not_called()


class TestBulkDownloadContracts:
    @patch("filmot.transcript.get_transcript")
    @patch("filmot.library.get_library")
    @patch("filmot.ledger.log_event")
    @patch("filmot.commands.search.FilmotClient")
    def test_search_bulk_propagates_language_and_uses_fresh_primary(
        self,
        mock_client_type,
        mock_log,
        mock_get_library,
        mock_transcript,
        runner,
    ):
        _mock_client(mock_client_type, {
            "result": [{
                "id": "video-id",
                "title": "Japanese source",
                "channelname": "Primary channel",
                "hits": [],
            }],
            "totalresultcount": 1,
        })
        library = mock_get_library.return_value
        library.list_transcripts.return_value = []
        library.exists.return_value = False
        mock_transcript.return_value = {
            "video_id": "video-id",
            "language": "ja",
            "full_text": "日本語の文字起こし",
            "segments": [
                {"text": "日本語", "start": 3.0, "duration": 2.0}
            ],
            "source": "youtube",
            "route": "direct",
        }

        result = runner.invoke(
            cli,
            [
                "search",
                "人工知能",
                "--lang",
                "ja",
                "--bulk-download",
                "japanese:1",
            ],
        )

        assert result.exit_code == 0, result.output
        assert mock_transcript.call_args.kwargs["languages"] == ["ja"]
        assert mock_transcript.call_args.kwargs["fresh_primary"] is True
        assert library.save.call_args.kwargs["topic"] == "japanese"
        assert library.save.call_args.kwargs["segments"] == [
            {"text": "日本語", "start": 3.0, "duration": 2.0}
        ]
        assert library.save.call_args.kwargs["metadata"]["source"] == "youtube"


class TestSearchAllLedger:
    @patch("filmot.ledger.log_event")
    @patch("filmot.commands.search.FilmotClient")
    def test_api_failure_is_logged(
        self, mock_client_type, mock_log, runner
    ):
        _mock_client(mock_client_type, {"error": "synthetic failure"})

        result = runner.invoke(cli, ["search-all", "alpha"])

        assert result.exit_code != 0
        assert mock_log.call_args.kwargs["status"] == "failed"
        assert mock_log.call_args.kwargs["failure_stage"] == "api"

    @patch("filmot.export.export_json", side_effect=OSError("disk full"))
    @patch("filmot.ledger.log_event")
    @patch("filmot.commands.search.FilmotClient")
    def test_export_failure_is_final_failed_event(
        self, mock_client_type, mock_log, mock_export, runner
    ):
        _mock_client(mock_client_type, {
            "result": [{"id": "video-id"}],
            "totalresultcount": 1,
            "pages_fetched": 1,
        })

        result = runner.invoke(
            cli,
            ["search-all", "alpha", "--output", "out.json"],
        )

        assert result.exit_code != 0
        assert mock_log.call_args.kwargs["status"] == "failed"
        assert mock_log.call_args.kwargs["failure_stage"] == "export"


class TestProbeExtraction:
    def test_relevance_scoring_does_not_match_acronyms_inside_words(self):
        assessment = _candidate_assessment(
            {
                "title": "Said chair repair",
                "hits": [{"token": "waiting on the main train"}],
            },
            "AI",
        )

        assert assessment["token_coverage"] == 0
        assert assessment["passage_coverage"] == 0

    def test_probe_pairs_do_not_cross_source_or_sentence_boundaries(self):
        assert _find_probe_pairs(
            ["alpha alpha.", "beta beta."],
            ["alpha", "beta"],
        ) == []

    def test_probe_pairs_report_distinct_source_support(self):
        pairs = _find_probe_pairs(
            ["alpha beta. alpha beta.", "alpha beta. alpha beta."],
            ["alpha", "beta"],
        )
        assert pairs[0] == ("alpha", "beta", 4, 2)

    def test_probe_pairs_require_cross_source_support(self):
        assert _find_probe_pairs(
            ["alpha beta. alpha beta.", "unrelated material."],
            ["alpha", "beta"],
        ) == []

    def test_probe_pair_salience_prioritizes_live_shaped_specific_relations(
        self,
    ):
        terms = [
            "machine learning",
            "neural network",
            "deep learning",
            "free energy",
            "generative models",
        ]
        texts = [
            "free energy generative models. " * 20
            + "deep learning free energy. " * 12
            + "free energy machine learning. " * 9
            + "machine learning neural network. deep learning neural network.",
            "free energy generative models. " * 16
            + "deep learning free energy. " * 10
            + "free energy machine learning. " * 7
            + "machine learning neural network. deep learning neural network.",
            "free energy generative models. " * 12
            + "deep learning free energy. " * 8
            + "free energy machine learning. " * 5
            + "machine learning neural network. deep learning neural network.",
            # A long generic course can repeat one phrase hundreds of times,
            # but source-length normalization must not let it consume the
            # highest-value relationship slots.
            "generic filler machine learning. " * 500
            + "deep learning tutorial. " * 80
            + "machine learning neural network. deep learning neural network.",
        ]

        pairs = _find_probe_pairs(texts, terms, include_scores=True)
        names = [(left, right) for left, right, *_ in pairs]

        assert names[:3] == [
            ("free energy", "generative models"),
            ("deep learning", "free energy"),
            ("free energy", "machine learning"),
        ]
        assert names[3:] == [
            ("machine learning", "neural network"),
            ("deep learning", "neural network"),
        ]
        for _, _, co_windows, source_support, basis in pairs:
            assert basis["method"] == "source_normalized_tfidf_v1"
            assert basis["score"] == round(basis["score"], 3)
            assert basis["co_windows"] == co_windows
            assert basis["source_support"] == source_support
            assert basis["left"]["document_frequency"] >= 2
            assert basis["right"]["document_frequency"] >= 2

    def test_terms_prefer_cross_source_support_over_one_source_repetition(self):
        terms = _extract_probe_terms(
            [
                "caption glitch " * 20 + ". shared concept. shared concept.",
                "shared concept. shared concept.",
                "shared concept. shared concept.",
            ],
            "unrelated topic",
            top_n=2,
        )
        assert "shared concept" in terms

    def test_probe_terms_require_phrases_and_collapse_plural_variants(self):
        texts = [
            (
                "Basically trying data model models. "
                "Free energy and generative model interact. "
            ) * 4,
            (
                "Trying models with data, basically. "
                "Free energy and generative models interact. "
            ) * 4,
        ]

        terms = _extract_probe_terms(
            texts,
            "active inference reinforcement learning",
            top_n=12,
        )

        assert not {"data", "model", "models", "trying", "basically"} & set(terms)
        assert "free energy" in terms
        assert len({term for term in terms if term.startswith("generative model")}) == 1

    def test_probe_pairs_reject_singular_plural_self_relationships(self):
        assert _find_probe_pairs(
            ["model models. model models.", "model models. model models."],
            ["model", "models"],
        ) == []

    def test_relationship_evidence_is_field_aware_and_ordered(self):
        scattered = {
            "title": "Scaling LLM inference with reinforcement learning",
            "description": "An active discussion of language model training",
        }
        title_supported = {
            "title": "Active inference and reinforcement learning compared",
        }
        repeated_description = {
            "title": "A technical field report",
            "description": (
                "Active inference and reinforcement learning are compared. "
                "The limits of active inference versus reinforcement learning "
                "are then tested."
            ),
        }

        scattered_evidence = _topic_relationship_evidence(
            scattered, "active inference reinforcement learning"
        )
        assert scattered_evidence == {
            "admitted": False,
            "title_spans": 0,
            "description_spans": 0,
            "hit_spans": 0,
            "corroborating_spans": 0,
            "max_span_words": 16,
        }

        title_evidence = _topic_relationship_evidence(
            title_supported, "active inference reinforcement learning"
        )
        assert title_evidence["admitted"] is True
        assert title_evidence["title_spans"] == 1

        repeated_evidence = _topic_relationship_evidence(
            repeated_description, "active inference reinforcement learning"
        )
        assert repeated_evidence["admitted"] is True
        assert repeated_evidence["description_spans"] == 2

        multilingual = _topic_relationship_evidence(
            {"title": "人工知能と社会変革"}, "人工知能と社会変革"
        )
        assert multilingual["admitted"] is True
        assert multilingual["title_spans"] == 1

    def test_one_description_enumeration_is_not_lexical_corroboration(self):
        video = {
            "title": (
                "Does Your Brain Build Thousands of Realities? | "
                "A Thousand Brains"
            ),
            "description": (
                "This overview discusses cortical columns, perception, "
                "movement, prediction, and reference frames. "
                + "Neocortical models and sensory evidence are reviewed. " * 35
                + "Alternative perspectives include predictive processing, "
                "active inference, reinforcement learning, dynamical-systems "
                "approaches, and contemporary deep learning."
            ),
        }

        evidence = _topic_relationship_evidence(
            video, "active inference reinforcement learning"
        )

        assert evidence["admitted"] is False
        assert evidence["title_spans"] == 0
        assert evidence["description_spans"] == 1
        assert evidence["corroborating_spans"] == 1

    def test_description_and_distinct_hit_can_lexically_corroborate(self):
        evidence = _topic_relationship_evidence(
            {
                "title": "A technical field report",
                "description": "Active inference and reinforcement learning.",
                "hits": [{
                    "ctx_before": "The comparison tests",
                    "token": "active inference versus reinforcement learning",
                }],
            },
            "active inference reinforcement learning",
        )

        assert evidence["admitted"] is True
        assert evidence["description_spans"] == 1
        assert evidence["hit_spans"] == 1

    def test_non_latin_terms_and_pairs_are_extractable(self):
        texts = [
            "人工知能と社会変革と量子技術。社会変革と量子技術。",
            "社会変革と量子技術。社会変革と量子技術。",
        ]
        terms = _extract_probe_terms(texts, "人工知能", top_n=8)
        pairs = _find_probe_pairs(texts, terms)

        assert "社会変革" in terms
        assert "量子技術" in terms
        assert any(
            {left, right} == {"社会変革", "量子技術"}
            for left, right, _, _ in pairs
        )

        scored = _find_probe_pairs(texts, terms, include_scores=True)
        multilingual_pair = next(
            row for row in scored
            if {row[0], row[1]} == {"社会変革", "量子技術"}
        )
        basis = multilingual_pair[4]
        assert basis["method"] == "source_normalized_tfidf_v1"
        assert basis["score"] > 0
        assert {basis["left"]["term"], basis["right"]["term"]} == {
            "社会変革",
            "量子技術",
        }

    def test_odd_length_ladder_tries_both_adjacent_concept_splits(self):
        ladder = _research_query_ladder(
            "sparse autoencoder superposition"
        )
        queries = [query for _, query in ladder]

        assert (
            '"sparse autoencoder" NEAR/25 "superposition"'
            in queries
        )
        assert (
            '"sparse" NEAR/25 "autoencoder superposition"'
            in queries
        )


class TestResearchSafetyAndLedger:
    @staticmethod
    def _library(mock_get_library):
        library = mock_get_library.return_value
        library._normalize_topic.return_value = "topic"
        library.list_transcripts.return_value = []
        library.exists.return_value = False
        return library

    @staticmethod
    def _probe_corpus(library, records):
        library.list_transcripts.return_value = [
            {"video_id": video_id} for video_id in records
        ]
        library.get.side_effect = (
            lambda video_id, topic=None: records.get(video_id)
        )

    @staticmethod
    def _research_candidate(video_id, label=None):
        label = label or video_id
        return {
            "id": video_id,
            "title": f"Alpha beta {label}",
            "channelname": "Research Channel",
            "duration": 600,
            "viewcount": 1000,
            "hits": [
                {"token": "alpha beta"},
                {"ctx_before": "alpha", "token": "beta"},
            ],
        }

    @patch(
        "filmot.commands.research._backfill_metadata",
        side_effect=lambda video_id, title, channel: (title, channel),
    )
    @patch("filmot.transcript.get_transcript")
    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_relationship_ladder_accumulates_unique_candidates_and_origins(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_proxy,
        mock_transcript,
        mock_backfill,
        runner,
    ):
        library = self._library(mock_get_library)
        client = mock_client_type.return_value
        strong = self._research_candidate("strong", "strong origin")
        strong["_fallback_stage"] = "stale_internal_value"
        duplicate = self._research_candidate(
            "strong", "weaker duplicate must not replace origin"
        )
        exact = self._research_candidate("exact", "exact source")
        proximity = self._research_candidate("near", "proximity source")
        client.search_subtitles_all.side_effect = [
            {"result": [strong], "totalresultcount": 1},
            {"result": [duplicate, exact], "totalresultcount": 2},
            {"result": [proximity], "totalresultcount": 1},
        ]
        mock_transcript.side_effect = lambda video_id, **kwargs: {
            "video_id": video_id,
            "full_text": f"alpha beta transcript for {video_id}",
            "segments": [{
                "text": f"alpha beta transcript for {video_id}",
                "start": 0.0,
                "duration": 1.0,
            }],
            "source": "youtube",
            "route": "direct",
        }

        result = runner.invoke(
            cli,
            [
                "research",
                "alpha beta",
                "--no-scout",
                "--depth",
                "3",
            ],
        )

        assert result.exit_code == 0, result.output
        assert client.search_subtitles_all.call_count == 3
        compact_output = " ".join(result.output.split())
        assert (
            "exact_phrase accumulation: 2 qualified, 1 new, "
            "1 duplicate(s); unique pool 2/3."
            in compact_output
        )
        assert [
            call.kwargs["query"]
            for call in client.search_subtitles_all.call_args_list
        ] == [
            "alpha beta",
            '"alpha beta"',
            '"alpha" NEAR/25 "beta"',
        ]
        assert {
            call.args[0] for call in mock_transcript.call_args_list
        } == {"strong", "exact", "near"}
        saved_stages = {
            call.kwargs["video_id"]:
            call.kwargs["metadata"]["selection_stage"]
            for call in library.save.call_args_list
        }
        assert saved_stages == {
            "strong": "title+transcript",
            "exact": "exact_phrase",
            "near": "proximity",
        }
        selection = next(
            call.kwargs
            for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "selection"
        )
        assert {
            row["video_id"]: row["stage"]
            for row in selection["selections"]
        } == saved_stages
        exact_accumulation = next(
            call.kwargs
            for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "search_accumulate"
            and call.kwargs.get("stage") == "exact_phrase"
        )
        assert exact_accumulation["added"] == 1
        assert exact_accumulation["duplicates"] == 1

    @patch("filmot.transcript.get_transcript")
    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_relationship_ladder_stops_calls_when_initial_stage_meets_depth(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_proxy,
        mock_transcript,
        runner,
    ):
        self._library(mock_get_library)
        client = mock_client_type.return_value
        client.search_subtitles_all.return_value = {
            "result": [
                self._research_candidate("first"),
                self._research_candidate("second"),
                self._research_candidate("third"),
            ],
            "totalresultcount": 3,
        }
        mock_transcript.return_value = {
            "video_id": "candidate",
            "full_text": "alpha beta transcript",
            "segments": [],
            "source": "youtube",
            "route": "direct",
        }

        result = runner.invoke(
            cli,
            [
                "research",
                "alpha beta",
                "--no-scout",
                "--depth",
                "2",
            ],
        )

        assert result.exit_code == 0, result.output
        assert client.search_subtitles_all.call_count == 1
        ladder_checkpoint = next(
            call.kwargs
            for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "search_ladder"
        )
        assert ladder_checkpoint["status"] == "target_reached"
        assert ladder_checkpoint["unique_pool"] == 3
        assert mock_transcript.call_count == 2

    def test_research_help_describes_depth_as_accumulated_maximum(self, runner):
        result = runner.invoke(cli, ["research", "--help"])

        assert result.exit_code == 0, result.output
        compact_output = " ".join(result.output.split())
        assert "accumulates unique title+transcript" in compact_output
        assert "Maximum transcripts to select" in compact_output
        assert "0 previews the initial Filmot scope" in compact_output
        assert "enabled scout and explicit probes still run" in compact_output
        assert "even when current discovery is empty" in compact_output
        assert "empty selection and probing continues" in compact_output
        assert "never widened with a loose" in compact_output

    @patch("filmot.transcript.get_transcript")
    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_depth_zero_keeps_preview_scope_without_expanding_ladder(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_proxy,
        mock_transcript,
        runner,
    ):
        self._library(mock_get_library)
        client = mock_client_type.return_value
        client.search_subtitles_all.return_value = {
            "result": [self._research_candidate("preview")],
            "totalresultcount": 1,
        }

        result = runner.invoke(
            cli,
            [
                "research",
                "alpha beta",
                "--no-scout",
                "--depth",
                "0",
            ],
        )

        assert result.exit_code == 0, result.output
        assert client.search_subtitles_all.call_count == 1
        mock_transcript.assert_not_called()
        assert "Depth target is 0" in result.output
        compact_output = " ".join(result.output.split())
        assert "preview pool 1" in compact_output
        assert "/0" not in compact_output
        ladder_checkpoint = next(
            call.kwargs
            for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "search_ladder"
        )
        assert ladder_checkpoint["status"] == "target_zero"
        assert ladder_checkpoint["stages"] == ["title+transcript"]

    @patch(
        "filmot.commands.research._backfill_metadata",
        side_effect=lambda video_id, title, channel: (title, channel),
    )
    @patch("filmot.transcript.get_transcript")
    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_exhausted_nonempty_relationship_pool_skips_broad_and_explains_underfill(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_proxy,
        mock_transcript,
        mock_backfill,
        runner,
    ):
        library = self._library(mock_get_library)
        client = mock_client_type.return_value
        first = self._research_candidate("only", "only safe source")
        duplicate = self._research_candidate("only", "later duplicate")
        client.search_subtitles_all.side_effect = [
            {"result": [first], "totalresultcount": 1},
            {"result": [duplicate], "totalresultcount": 1},
            {"result": [], "totalresultcount": 0},
        ]
        mock_transcript.return_value = {
            "video_id": "only",
            "full_text": "alpha beta transcript",
            "segments": [],
            "source": "youtube",
            "route": "direct",
        }

        result = runner.invoke(
            cli,
            [
                "research",
                "alpha beta",
                "--no-scout",
                "--depth",
                "6",
            ],
        )

        assert result.exit_code == 0, result.output
        assert client.search_subtitles_all.call_count == 3
        search_stages = [
            call.kwargs["stage"]
            for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "search"
            and call.kwargs.get("status") == "started"
        ]
        assert search_stages == [
            "title+transcript",
            "exact_phrase",
            "proximity",
        ]
        compact_output = " ".join(result.output.split())
        assert (
            "Relationship-preserving ladder exhausted with 1/6"
            in compact_output
        )
        assert (
            "loose transcript-wide fallback is not entered"
            in compact_output
        )
        assert "Qualified pool under target:" in compact_output
        assert "Next action:" in compact_output
        assert (
            "increasing --depth alone will not widen" in compact_output
        )
        assert library.save.call_args.kwargs["metadata"][
            "selection_stage"
        ] == "title+transcript"
        underfill = next(
            call.kwargs
            for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "candidate_underfill"
        )
        assert underfill["target"] == 6
        assert underfill["qualified"] == 1
        assert underfill["remaining"] == 5
        assert underfill["reason"] == (
            "relationship_ladder_exhausted_nonempty"
        )
        assert underfill["next_action"] == "targeted_exact_or_near_search"
        selection = next(
            call.kwargs
            for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "selection"
        )
        assert selection["filmot_total"] == 1

    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.ledger.log_result")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_empty_partial_scope_keeps_one_partial_status_and_error(
        self,
        mock_client_type,
        mock_get_library,
        mock_log_result,
        mock_log,
        mock_proxy,
        runner,
    ):
        self._library(mock_get_library)
        mock_client_type.return_value.search_subtitles_all.return_value = {
            "result": [],
            "totalresultcount": 0,
            "partial": True,
            "page_error": "page 2 timed out",
        }

        result = runner.invoke(
            cli,
            ["research", "alpha beta", "--no-scout", "--depth", "0"],
        )

        assert result.exit_code == 0, result.output
        outcome = mock_log_result.call_args.args[1]
        assert outcome.status_value == "partial"
        assert outcome.errors[0].type == "PartialSearch"
        assert "page 2 timed out" in outcome.errors[0].message
        assert "No candidates passed" in result.output
        end_events = [
            call
            for call in mock_log.call_args_list
            if call.args[0] == "research_end"
        ]
        assert end_events[-1].kwargs["status"] == "partial"

    @patch("filmot.youtube_search.search_recent")
    @patch("filmot.youtube_search.validate_youtube_api")
    @patch("filmot.transcript.get_transcript")
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_channel_constraint_also_filters_freshness_scout(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_transcript,
        mock_validate,
        mock_search_recent,
        runner,
    ):
        self._library(mock_get_library)
        mock_client_type.return_value.search_subtitles_all.return_value = {
            "result": [],
            "totalresultcount": 0,
        }
        mock_search_recent.return_value = [{
            "video_id": "outside",
            "channel_id": "UC_OUTSIDE",
            "title": "Outside source",
        }]

        result = runner.invoke(
            cli,
            [
                "research",
                "alpha beta",
                "--depth",
                "1",
                "--channel-id",
                "UC_ALLOWED",
            ],
        )

        assert result.exit_code == 0, result.output
        assert mock_search_recent.call_args.kwargs["channel_id"] == "UC_ALLOWED"
        assert mock_search_recent.call_args.kwargs["max_results"] == 10
        assert mock_search_recent.call_args.kwargs["order"] == "relevance"
        scout_started = [
            call.kwargs
            for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "scout"
            and call.kwargs.get("status") == "started"
        ][-1]
        assert scout_started["days"] == 7
        assert scout_started["max_results"] == 10
        assert scout_started["order"] == "relevance"
        assert scout_started["channel_id"] == "UC_ALLOWED"
        assert scout_started["request_channel_id"] == "UC_ALLOWED"
        assert "Scout: Found 0 recent upload" in result.output
        mock_transcript.assert_not_called()

    @patch("filmot.youtube_search.search_recent")
    @patch("filmot.youtube_search.validate_youtube_api")
    @patch("filmot.transcript.get_transcript")
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_unrelated_scout_is_not_forced_into_download_slots(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_transcript,
        mock_validate,
        mock_search_recent,
        runner,
    ):
        self._library(mock_get_library)
        mock_client_type.return_value.search_subtitles_all.return_value = {
            "result": [],
            "totalresultcount": 0,
        }
        mock_search_recent.return_value = [{
            "video_id": "unrelated",
            "channel_id": "UC_ANY",
            "title": "Completely unrelated cooking lesson",
            "description": "A recipe for soup",
        }]

        result = runner.invoke(
            cli,
            ["research", "alpha beta", "--depth", "1"],
        )

        assert result.exit_code == 0, result.output
        assert "Scout lexical admission gate: 1 -> 0" in result.output
        mock_transcript.assert_not_called()

    @patch("filmot.youtube_search.search_recent")
    @patch("filmot.youtube_search.validate_youtube_api")
    @patch("filmot.transcript.get_transcript")
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_scout_rejects_topic_words_scattered_across_unrelated_metadata(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_transcript,
        mock_validate,
        mock_search_recent,
        runner,
    ):
        self._library(mock_get_library)
        mock_client_type.return_value.search_subtitles_all.return_value = {
            "result": [],
            "totalresultcount": 0,
        }
        # The old bag-of-words gate admitted this: the title covers three of
        # four tokens and the description supplies the remaining one, despite
        # never stating the requested relationship in a single passage.
        mock_search_recent.return_value = [{
            "video_id": "lexical-false-positive",
            "channel_id": "UC_ANY",
            "title": "Scaling LLM Inference with Reinforcement Learning",
            "description": "An active discussion of language model training",
        }]

        result = runner.invoke(
            cli,
            [
                "research",
                "active inference reinforcement learning",
                "--depth",
                "1",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "Scout lexical admission gate: 1 -> 0" in result.output
        mock_transcript.assert_not_called()

    @patch("filmot.youtube_search.search_recent")
    @patch("filmot.youtube_search.validate_youtube_api")
    @patch("filmot.transcript.get_transcript")
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_scout_never_displaces_higher_global_rank_at_depth_one_or_two(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_transcript,
        mock_validate,
        mock_search_recent,
        runner,
    ):
        self._library(mock_get_library)
        filmot_candidates = [
            {
                "id": "filmot-a",
                "title": "Active inference and reinforcement learning source A",
                "channelname": "Established A",
                "duration": 600,
                "viewcount": 5_000_000,
                "likecount": 250_000,
                "channelsubcount": 2_000_000,
                "hits": [
                    {
                        "ctx_before": "Mechanistic evidence begins",
                        "token": "active inference and reinforcement learning",
                        "ctx_after": "under controlled source A tests",
                    },
                    {
                        "ctx_before": "Policy comparison follows",
                        "token": "active inference versus reinforcement learning",
                        "ctx_after": "with source A measurements",
                    },
                ],
            },
            {
                "id": "filmot-b",
                "title": "Active inference and reinforcement learning source B",
                "channelname": "Established B",
                "duration": 600,
                "viewcount": 1_000_000,
                "likecount": 50_000,
                "channelsubcount": 1_000_000,
                "hits": [
                    {
                        "ctx_before": "Formal derivation establishes",
                        "token": "active inference and reinforcement learning",
                        "ctx_after": "inside source B simulations",
                    },
                    {
                        "ctx_before": "Independent evaluation contrasts",
                        "token": "active inference versus reinforcement learning",
                        "ctx_after": "using source B benchmarks",
                    },
                ],
            },
        ]
        mock_client_type.return_value.search_subtitles_all.return_value = {
            "result": filmot_candidates,
            "totalresultcount": 2,
        }
        mock_search_recent.return_value = [{
            "video_id": "scout-low",
            "channel_id": "UC_SCOUT",
            "channel_title": "New Scout",
            "title": "Active inference and reinforcement learning scout source",
            "description": "A newly uploaded lexical match.",
            "views": 21,
        }]

        def transcript_for(video_id, **kwargs):
            return {
                "video_id": video_id,
                "full_text": f"transcript for {video_id}",
                "segments": [{
                    "text": f"transcript for {video_id}",
                    "start": 0.0,
                    "duration": 1.0,
                }],
                "source": "youtube",
                "route": "direct",
            }

        mock_transcript.side_effect = transcript_for
        for depth, expected_ids in (
            (1, ["filmot-a"]),
            (2, ["filmot-a", "filmot-b"]),
        ):
            mock_log.reset_mock()
            mock_transcript.reset_mock()

            result = runner.invoke(
                cli,
                [
                    "research",
                    "active inference reinforcement learning",
                    "--depth",
                    str(depth),
                ],
            )

            assert result.exit_code == 0, result.output
            fetched_ids = [
                call.args[0] for call in mock_transcript.call_args_list
            ]
            assert fetched_ids == expected_ids
            selection = [
                call.kwargs
                for call in mock_log.call_args_list
                if call.args[0] == "research_checkpoint"
                and call.kwargs.get("phase") == "selection"
            ][-1]
            assert [
                item["video_id"] for item in selection["selections"]
            ] == expected_ids

            preview = result.output.split("Candidate preview", 1)[1].split(
                "Downloading", 1
            )[0]
            assert preview.index("source A") < preview.index("source B")
            assert preview.index("source B") < preview.index("scout source")
            assert "scout-lexical-spans=t1/d0/h0" in preview
            assert preview.count("scout-lexical-spans=") == 1

    @patch("filmot.youtube_search.search_recent")
    @patch("filmot.youtube_search.validate_youtube_api")
    @patch("filmot.transcript.get_transcript")
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_saved_scout_preserves_available_source_metadata(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_transcript,
        mock_validate,
        mock_search_recent,
        runner,
    ):
        library = self._library(mock_get_library)
        mock_client_type.return_value.search_subtitles_all.return_value = {
            "result": [],
            "totalresultcount": 0,
        }
        mock_search_recent.return_value = [{
            "video_id": "scout-video",
            "channel_id": "UC_SCOUT",
            "channel_title": "Scout Channel",
            "published_at": "2026-08-22T12:00:00Z",
            "title": "Alpha beta field report",
            "description": "Alpha beta evidence and analysis",
            "views": 321,
        }]
        mock_transcript.return_value = {
            "video_id": "scout-video",
            "full_text": "alpha beta transcript",
            "segments": [
                {"text": "alpha beta transcript", "start": 4.0, "duration": 2.0}
            ],
            "source": "youtube",
            "route": "direct",
        }

        result = runner.invoke(
            cli,
            ["research", "alpha beta", "--depth", "1"],
        )

        assert result.exit_code == 0, result.output
        metadata = library.save.call_args.kwargs["metadata"]
        assert metadata["channel_id"] == "UC_SCOUT"
        assert metadata["published_at"] == "2026-08-22T12:00:00Z"
        assert metadata["views"] == 321

    @patch("filmot.commands.research._backfill_metadata", return_value=("Title", "Channel"))
    @patch("filmot.transcript.get_transcript")
    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_relationship_fallback_rejects_partial_topic_coverage(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_proxy,
        mock_transcript,
        mock_backfill,
        runner,
    ):
        self._library(mock_get_library)
        client = mock_client_type.return_value
        client.search_subtitles_all.side_effect = [
            {"result": [], "totalresultcount": 0},
            {"result": [], "totalresultcount": 0},
            {
                "result": [
                    {
                        "id": "coherent",
                        "title": "technical source",
                        "hits": [
                            {"token": "alpha beta"},
                            {"ctx_before": "alpha", "token": "beta"},
                        ],
                    },
                    {
                        "id": "partial",
                        "title": "adjacent source",
                        "hits": [{"token": "alpha"}, {"token": "alpha"}],
                    },
                ],
                "totalresultcount": 2,
            },
        ]
        mock_transcript.return_value = {
            "video_id": "coherent",
            "full_text": "alpha beta transcript",
            "segments": [
                {"text": "alpha beta", "start": 9.0, "duration": 2.0}
            ],
            "source": "youtube",
            "route": "direct",
        }

        result = runner.invoke(
            cli,
            [
                "research",
                "alpha beta",
                "--no-scout",
                "--depth",
                "2",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "relationship-evidence gate: 2 -> 1" in result.output
        assert mock_transcript.call_count == 1
        assert mock_transcript.call_args.args[0] == "coherent"
        library = mock_get_library.return_value
        assert library.save.call_args.kwargs["segments"] == [
            {"text": "alpha beta", "start": 9.0, "duration": 2.0}
        ]
        assert library.save.call_args.kwargs["metadata"]["source"] == "youtube"

    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_huge_broad_fallback_requires_explicit_acceptance(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_proxy,
        runner,
    ):
        self._library(mock_get_library)
        client = mock_client_type.return_value
        client.search_subtitles_all.side_effect = [
            {"result": [], "totalresultcount": 0},
            {"result": [], "totalresultcount": 0},
            {"result": [], "totalresultcount": 0},
            {
                "result": [{"id": "noise", "hits": []}],
                "totalresultcount": 63527,
            },
        ]

        result = runner.invoke(
            cli,
            [
                "research",
                "AI data center electricity demand",
                "--no-scout",
                "--depth",
                "1",
            ],
        )

        assert result.exit_code != 0
        assert "Broad fallback blocked" in result.output
        events = [(c.args[0], c.kwargs) for c in mock_log.call_args_list]
        assert events[0][0] == "research_start"
        assert events[-1][0] == "research_end"
        assert events[-1][1]["status"] == "failed"

    @patch("filmot.commands.research._backfill_metadata", return_value=("Title", "Channel"))
    @patch("filmot.transcript.get_transcript", side_effect=KeyboardInterrupt)
    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_interrupt_writes_resumable_end_event(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_proxy,
        mock_transcript,
        mock_backfill,
        runner,
    ):
        self._library(mock_get_library)
        mock_client_type.return_value.search_subtitles_all.return_value = {
            "result": [{
                "id": "vid",
                "title": "alpha beta",
                "channelname": "Channel",
                "duration": 60,
                "hits": [{"token": "alpha"}, {"token": "beta"}],
            }],
            "totalresultcount": 1,
        }

        result = runner.invoke(
            cli,
            [
                "research",
                "alpha beta",
                "--no-scout",
                "--depth",
                "1",
            ],
        )

        assert result.exit_code != 0
        end_calls = [
            c for c in mock_log.call_args_list if c.args[0] == "research_end"
        ]
        assert end_calls[-1].kwargs["status"] == "interrupted"
        assert end_calls[-1].kwargs["phase"] == "download_item"
        started_index = next(
            index
            for index, call in enumerate(mock_log.call_args_list)
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "download_item"
            and call.kwargs.get("status") == "started"
        )
        interrupted_index = next(
            index
            for index, call in enumerate(mock_log.call_args_list)
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("status") == "interrupted"
        )
        assert started_index < interrupted_index
        assert any(
            c.args[0] == "research_checkpoint"
            and c.kwargs.get("status") == "interrupted"
            for c in mock_log.call_args_list
        )

    @patch("filmot.commands.research._extract_probe_terms")
    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_probe_excludes_frontier_and_skips_with_one_eligible_seed(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_proxy,
        mock_extract,
        runner,
    ):
        library = self._library(mock_get_library)
        self._probe_corpus(library, {
            "selected": {
                "video_id": "selected",
                "transcript": "selected relationship source",
                "metadata": {"selection_stage": "proximity"},
            },
            "scout": {
                "video_id": "scout",
                "transcript": "automatic scout frontier",
                "metadata": {"selection_stage": "scout"},
            },
            "probe": {
                "video_id": "probe",
                "transcript": "automatic probe frontier",
                "metadata": {"selection_stage": "probe"},
            },
        })
        mock_client_type.return_value.search_subtitles_all.return_value = {
            "result": [{
                "id": "search-seed",
                "title": "Alpha beta relationship source",
                "hits": [
                    {"token": "alpha beta"},
                    {"token": "alpha beta"},
                ],
            }],
            "totalresultcount": 1,
        }

        result = runner.invoke(
            cli,
            [
                "research",
                "alpha beta",
                "--no-scout",
                "--depth",
                "0",
                "--probe",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "1 eligible selected/manual transcript" in result.output
        assert "2 automatic frontier" in result.output
        assert "probe:1, scout:1" in result.output
        assert "need at least 2 eligible" in result.output
        mock_extract.assert_not_called()
        seed_checkpoint = next(
            call.kwargs for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "probe_seed"
        )
        assert seed_checkpoint["eligible"] == 1
        assert seed_checkpoint["excluded"] == 2
        assert seed_checkpoint["excluded_by_stage"] == {
            "scout": 1,
            "probe": 1,
        }
        skipped = next(
            call.kwargs for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "probe"
            and call.kwargs.get("status") == "skipped"
        )
        assert skipped["reason"] == "insufficient_eligible_seeds"

    @patch("filmot.commands.research._find_probe_pairs", return_value=[])
    @patch(
        "filmot.commands.research._extract_probe_terms",
        return_value=["deliberate practice", "expert performance"],
    )
    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_probe_explains_when_supported_pair_frontier_is_empty(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_proxy,
        mock_terms,
        mock_pairs,
        runner,
    ):
        library = self._library(mock_get_library)
        self._probe_corpus(library, {
            "manual-a": {
                "video_id": "manual-a",
                "transcript": "deliberate practice expert performance",
            },
            "manual-b": {
                "video_id": "manual-b",
                "transcript": "deliberate practice expert performance",
            },
        })
        mock_client_type.return_value.search_subtitles_all.return_value = {
            "result": [self._research_candidate("preview")],
            "totalresultcount": 1,
        }

        result = runner.invoke(
            cli,
            [
                "research",
                "alpha beta",
                "--no-scout",
                "--depth",
                "0",
                "--probe",
            ],
        )

        assert result.exit_code == 0, result.output
        compact_output = " ".join(result.output.split())
        assert "Probe empty:" in compact_output
        assert "no relationship pair met" in compact_output
        assert "0 queries run" in compact_output
        mock_client_type.return_value.search_subtitles.assert_not_called()
        terminal_probe = next(
            call.kwargs for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "probe"
            and call.kwargs.get("status") == "empty"
        )
        assert terminal_probe["reason"] == "no_cross_source_pairs"
        assert terminal_probe["terms"] == 2
        assert terminal_probe["queries"] == 0
        assert terminal_probe["queries_planned"] == 0

    @patch("filmot.commands.research._find_probe_pairs", return_value=[])
    @patch(
        "filmot.commands.research._extract_probe_terms",
        return_value=["deliberate practice", "expert performance"],
    )
    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_empty_discovery_still_runs_explicit_probe_from_saved_sources(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_proxy,
        mock_terms,
        mock_pairs,
        runner,
    ):
        library = self._library(mock_get_library)
        self._probe_corpus(library, {
            "manual-a": {
                "video_id": "manual-a",
                "transcript": "deliberate practice with useful feedback",
            },
            "manual-b": {
                "video_id": "manual-b",
                "transcript": "expert performance through focused practice",
            },
        })
        mock_client_type.return_value.search_subtitles_all.return_value = {
            "result": [],
            "totalresultcount": 0,
        }

        result = runner.invoke(
            cli,
            [
                "research",
                "alpha beta",
                "--no-scout",
                "--depth",
                "1",
                "--probe",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "No current discovery candidates passed" in result.output
        assert "Downloading 0 transcript" not in result.output
        assert "2 eligible selected/manual transcript" in result.output
        assert "Probe empty:" in result.output
        selection = next(
            call.kwargs for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "selection"
        )
        assert selection["status"] == "empty"
        terminal_probe = next(
            call.kwargs for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "probe"
            and call.kwargs.get("status") == "empty"
        )
        assert terminal_probe["reason"] == "no_cross_source_pairs"
        assert terminal_probe["eligible_seeds"] == 2
        assert terminal_probe["queries"] == 0
        assert terminal_probe["queries_planned"] == 0
        research_end = next(
            call.kwargs for call in reversed(mock_log.call_args_list)
            if call.args[0] == "research_end"
        )
        assert research_end["status"] == "completed"

    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_empty_discovery_without_probe_keeps_terminal_empty_behavior(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_proxy,
        runner,
    ):
        library = self._library(mock_get_library)
        self._probe_corpus(library, {
            "manual-a": {
                "video_id": "manual-a",
                "transcript": "saved source remains available",
            },
        })
        mock_client_type.return_value.search_subtitles_all.return_value = {
            "result": [],
            "totalresultcount": 0,
        }

        result = runner.invoke(
            cli,
            ["research", "alpha beta", "--no-scout", "--depth", "1"],
        )

        assert result.exit_code == 0, result.output
        assert "No candidates passed" in result.output
        assert "Probe seeds:" not in result.output
        assert not any(
            call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "probe_seed"
            for call in mock_log.call_args_list
        )
        selection = next(
            call.kwargs for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "selection"
        )
        assert selection["status"] == "empty"
        research_end = next(
            call.kwargs for call in reversed(mock_log.call_args_list)
            if call.args[0] == "research_end"
        )
        assert research_end["status"] == "empty"

    @patch(
        "filmot.commands.research._find_probe_pairs",
        return_value=[
            (
                "machine learning",
                "neural network",
                7,
                3,
                {"method": "source_normalized_tfidf_v1", "score": 9.06},
            ),
            (
                "deep learning",
                "neural network",
                5,
                3,
                {"method": "source_normalized_tfidf_v1", "score": 7.94},
            ),
        ],
    )
    @patch(
        "filmot.commands.research._extract_probe_terms",
        return_value=["machine learning", "neural network", "deep learning"],
    )
    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_probe_broad_sampled_zero_stops_and_records_lower_ranked_tail(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_proxy,
        mock_terms,
        mock_pairs,
        runner,
    ):
        library = self._library(mock_get_library)
        self._probe_corpus(library, {
            "manual-a": {
                "video_id": "manual-a",
                "transcript": "machine learning and neural network",
                "metadata": {},
            },
            "selected-b": {
                "video_id": "selected-b",
                "transcript": "machine learning and neural network",
                "metadata": {"selection_stage": "exact_phrase"},
            },
            "old-scout": {
                "video_id": "old-scout",
                "transcript": "frontier text must not seed",
                "metadata": {"selection_stage": "scout"},
            },
        })
        client = mock_client_type.return_value
        client.search_subtitles_all.return_value = {
            "result": [{
                "id": "search-seed",
                "channelid": "UC_SCOPE",
                "title": (
                    "Active inference reinforcement learning source"
                ),
                "hits": [
                    {"token": "active inference reinforcement learning"},
                    {"token": "active inference reinforcement learning"},
                ],
            }],
            "totalresultcount": 1,
        }
        client.search_subtitles.return_value = {
            "result": [
                {
                    "id": f"generic-{index}",
                    "title": f"Generic neural network course {index}",
                    "channelid": "UC_SCOPE",
                    "hits": [{"token": "machine learning neural network"}],
                }
                for index in range(50)
            ],
            "totalresultcount": 9797,
        }

        result = runner.invoke(
            cli,
            [
                "research",
                "active inference reinforcement learning",
                "--no-scout",
                "--depth",
                "0",
                "--probe",
                "--channel-id",
                "UC_SCOPE",
            ],
        )

        assert result.exit_code == 0, result.output
        assert client.search_subtitles.call_count == 1
        assert "2 eligible selected/manual transcript" in result.output
        assert "1 automatic frontier" in result.output
        assert "score:9.060" in result.output
        assert "Sampled zero" in result.output
        assert "50/9,797 results (0.5%)" in result.output
        assert "global zero." in result.output
        assert "manual query: filmot search" in result.output
        compact_output = " ".join(result.output.split())
        assert (
            "Exact manual query: filmot search '"
            '"machine learning" NEAR/15 "neural network"'
            "' --lang en --title 'active inference reinforcement learning' "
            "--channel-id UC_SCOPE"
        ) in compact_output
        probe_event = next(
            call.kwargs for call in mock_log.call_args_list
            if call.args[0] == "research_probe"
        )
        assert probe_event["status"] == "broad_sampled"
        assert probe_event["api_total"] == 9797
        assert probe_event["returned"] == 50
        assert probe_event["scoped"] == 0
        assert probe_event["sample_coverage"] == 0.0051
        assert probe_event["informativeness"]["score"] == 9.06
        deferred = next(
            call.kwargs for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "probe_search"
            and call.kwargs.get("status") == "deferred"
        )
        assert deferred["index"] == 2
        assert deferred["reason"] == "broad_sampled_tail_budget"
        assert deferred["query"] == (
            '"deep learning" NEAR/15 "neural network"'
        )

    @patch(
        "filmot.commands.research._find_probe_pairs",
        return_value=[
            ("specific alpha", "specific beta", 8, 3, {"score": 12.0}),
            ("specific gamma", "specific delta", 6, 2, {"score": 10.0}),
            ("generic alpha", "generic beta", 4, 2, {"score": 4.0}),
        ],
    )
    @patch(
        "filmot.commands.research._extract_probe_terms",
        return_value=["specific alpha", "specific beta"],
    )
    @patch("filmot.commands.research._backfill_metadata")
    @patch("filmot.transcript.get_transcript")
    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_probe_stops_api_calls_at_three_candidate_capacity(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_proxy,
        mock_transcript,
        mock_backfill,
        mock_terms,
        mock_pairs,
        runner,
    ):
        library = self._library(mock_get_library)
        self._probe_corpus(library, {
            "manual-a": {"video_id": "manual-a", "transcript": "seed a"},
            "manual-b": {"video_id": "manual-b", "transcript": "seed b"},
        })
        client = mock_client_type.return_value
        client.search_subtitles_all.return_value = {
            "result": [{
                "id": "search-seed",
                "title": "Alpha beta relationship source",
                "hits": [
                    {"token": "alpha beta"},
                    {"token": "alpha beta"},
                ],
            }],
            "totalresultcount": 1,
        }

        def candidates(prefix):
            return {
                "result": [
                    {
                        "id": f"{prefix}-{suffix}",
                        "title": f"Alpha beta candidate {prefix}-{suffix}",
                        "channelname": "Channel",
                        "duration": 60,
                        "hits": [],
                    }
                    for suffix in ("a", "b")
                ],
                "totalresultcount": 2,
            }

        client.search_subtitles.side_effect = [
            candidates("first"),
            candidates("second"),
        ]

        def transcript_result(video_id, *args, **kwargs):
            return {
                "video_id": video_id,
                "full_text": f"transcript for {video_id}",
                "segments": [{
                    "text": f"transcript for {video_id}",
                    "start": 0.0,
                    "duration": 1.0,
                }],
                "source": "youtube",
                "route": "direct",
            }

        mock_transcript.side_effect = transcript_result
        mock_backfill.side_effect = (
            lambda video_id, title, channel: (title, channel)
        )

        result = runner.invoke(
            cli,
            [
                "research",
                "alpha beta",
                "--no-scout",
                "--depth",
                "0",
                "--probe",
            ],
        )

        assert result.exit_code == 0, result.output
        assert client.search_subtitles.call_count == 2
        assert library.save.call_count == 3
        assert mock_transcript.call_count == 3
        assert "Probe candidate capacity reached (3)" in result.output
        probe_indexes = [
            call.kwargs["metadata"]["probe_index"]
            for call in library.save.call_args_list
        ]
        assert probe_indexes == [1, 1, 2]
        assert all(
            call.kwargs["metadata"]["probe_query"]
            for call in library.save.call_args_list
        )
        deferred = next(
            call.kwargs for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "probe_search"
            and call.kwargs.get("status") == "deferred"
        )
        assert deferred["index"] == 3
        assert deferred["reason"] == "candidate_capacity_reached"

    @patch(
        "filmot.commands.research._find_probe_pairs",
        return_value=[
            ("free energy", "machine learning", 5, 2, {"score": 11.36}),
        ],
    )
    @patch(
        "filmot.commands.research._extract_probe_terms",
        return_value=["free energy", "machine learning"],
    )
    @patch("filmot.commands.research._backfill_metadata")
    @patch("filmot.transcript.get_transcript")
    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_probe_broad_sample_with_coherent_candidate_is_retained(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_proxy,
        mock_transcript,
        mock_backfill,
        mock_terms,
        mock_pairs,
        runner,
    ):
        library = self._library(mock_get_library)
        self._probe_corpus(library, {
            "manual-a": {"video_id": "manual-a", "transcript": "seed a"},
            "manual-b": {"video_id": "manual-b", "transcript": "seed b"},
        })
        client = mock_client_type.return_value
        client.search_subtitles_all.return_value = {
            "result": [{
                "id": "search-seed",
                "title": (
                    "Active inference reinforcement learning source"
                ),
                "hits": [
                    {"token": "active inference reinforcement learning"},
                    {"token": "active inference reinforcement learning"},
                ],
            }],
            "totalresultcount": 1,
        }
        client.search_subtitles.return_value = {
            "result": [{
                "id": "relevant",
                "title": "Active inference reinforcement learning comparison",
                "channelname": "Channel",
                "duration": 120,
                "hits": [{"token": "free energy machine learning"}],
            }],
            "totalresultcount": 9797,
        }
        mock_transcript.return_value = {
            "video_id": "relevant",
            "full_text": "relevant transcript",
            "segments": [{
                "text": "relevant transcript",
                "start": 0.0,
                "duration": 1.0,
            }],
            "source": "youtube",
            "route": "direct",
        }
        mock_backfill.side_effect = (
            lambda video_id, title, channel: (title, channel)
        )

        result = runner.invoke(
            cli,
            [
                "research",
                "active inference reinforcement learning",
                "--no-scout",
                "--depth",
                "0",
                "--probe",
            ],
        )

        assert result.exit_code == 0, result.output
        mock_transcript.assert_called_once()
        assert library.save.call_count == 1
        assert library.save.call_args.kwargs["video_id"] == "relevant"
        assert library.save.call_args.kwargs["metadata"]["probe_query"] == (
            '"free energy" NEAR/15 "machine learning"'
        )
        assert "Sampled zero" not in result.output
        probe_event = next(
            call.kwargs for call in mock_log.call_args_list
            if call.args[0] == "research_probe"
        )
        assert probe_event["status"] == "completed"
        assert probe_event["api_total"] == 9797
        assert probe_event["scoped"] == 1
        assert probe_event["sample_coverage"] == 0.0001

    @patch(
        "filmot.commands.research._find_probe_pairs",
        return_value=[("language models", "neural networks", 6, 2)],
    )
    @patch(
        "filmot.commands.research._extract_probe_terms",
        return_value=["language models", "neural networks"],
    )
    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_probe_displays_and_logs_effective_scope_and_raw_counts(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_proxy,
        mock_terms,
        mock_pairs,
        runner,
    ):
        library = self._library(mock_get_library)
        library.list_transcripts.return_value = [
            {"video_id": "saved-a"},
            {"video_id": "saved-b"},
        ]
        library.get.return_value = {
            "transcript": "language models and neural networks. " * 4
        }
        client = mock_client_type.return_value
        client.search_subtitles_all.return_value = {
            "result": [{
                "id": "candidate",
                "title": "alpha beta",
                "channelname": "Channel",
                "duration": 60,
                "hits": [{"token": "alpha"}, {"token": "beta"}],
            }],
            "totalresultcount": 1,
        }
        client.search_subtitles.return_value = {
            "result": [],
            "totalresultcount": 1354,
        }

        result = runner.invoke(
            cli,
            [
                "research",
                "alpha beta",
                "--no-scout",
                "--depth",
                "0",
                "--probe",
            ],
        )

        assert result.exit_code == 0, result.output
        assert 'scope:title="alpha beta"' in result.output
        assert "1,354 API results" in result.output
        probe_calls = [
            c for c in mock_log.call_args_list
            if c.args[0] == "research_probe"
        ]
        assert probe_calls[-1].kwargs["constraints"]["title"] == "alpha beta"
        assert probe_calls[-1].kwargs["api_total"] == 1354
        assert probe_calls[-1].kwargs["returned"] == 0
        assert probe_calls[-1].kwargs["scoped"] == 0

    @patch(
        "filmot.commands.research._find_probe_pairs",
        return_value=[("free energy", "generative model", 6, 2)],
    )
    @patch(
        "filmot.commands.research._extract_probe_terms",
        return_value=["free energy", "generative model"],
    )
    @patch(
        "filmot.commands.research._backfill_metadata",
        return_value=("Active inference reinforcement learning case study", "Channel"),
    )
    @patch("filmot.transcript.get_transcript")
    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_probe_rejects_lexical_false_positive_and_saves_query_provenance(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_proxy,
        mock_transcript,
        mock_backfill,
        mock_terms,
        mock_pairs,
        runner,
    ):
        library = self._library(mock_get_library)
        library.list_transcripts.return_value = [
            {"video_id": "saved-a"},
            {"video_id": "saved-b"},
        ]
        library.get.return_value = {
            "transcript": "Free energy and generative model interact. " * 4
        }
        client = mock_client_type.return_value
        client.search_subtitles_all.return_value = {
            "result": [{
                "id": "seed",
                "title": "Active inference reinforcement learning overview",
                "hits": [{"token": "active inference"}, {"token": "reinforcement learning"}],
            }],
            "totalresultcount": 1,
        }
        client.search_subtitles.return_value = {
            "result": [
                {
                    "id": "unrelated",
                    "title": "Data Science Full Course",
                    "description": "Active students learn model inference",
                    "hits": [{"token": "free energy generative model"}],
                },
                {
                    "id": "coherent",
                    "title": "Active inference reinforcement learning case study",
                    "channelname": "Channel",
                    "hits": [{"token": "free energy generative model"}],
                },
            ],
            "totalresultcount": 2,
        }
        mock_transcript.return_value = {
            "video_id": "coherent",
            "full_text": "coherent transcript",
            "segments": [{"text": "coherent transcript", "start": 0.0, "duration": 1.0}],
            "source": "youtube",
            "route": "direct",
        }

        result = runner.invoke(
            cli,
            [
                "research",
                "active inference reinforcement learning",
                "--no-scout",
                "--depth",
                "0",
                "--probe",
            ],
        )

        assert result.exit_code == 0, result.output
        mock_transcript.assert_called_once()
        assert mock_transcript.call_args.args[0] == "coherent"
        metadata = library.save.call_args.kwargs["metadata"]
        assert metadata["probe_query"] == '"free energy" NEAR/15 "generative model"'
        assert metadata["probe_index"] == 1
        saved_checkpoints = [
            call.kwargs
            for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "probe_download"
            and call.kwargs.get("status") == "saved"
        ]
        assert saved_checkpoints[-1]["probe_query"] == metadata["probe_query"]
        assert saved_checkpoints[-1]["probe_index"] == 1
        assert saved_checkpoints[-1]["title"] == metadata["title"]
        assert saved_checkpoints[-1]["channel"] == metadata["channel"]
        started_checkpoints = [
            call.kwargs
            for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
            and call.kwargs.get("phase") == "probe_download"
            and call.kwargs.get("status") == "started"
        ]
        assert started_checkpoints[-1]["title"] == (
            "Active inference reinforcement learning case study"
        )
        assert started_checkpoints[-1]["channel"] == "Channel"

    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_interrupt_during_search_records_started_search_phase(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_proxy,
        runner,
    ):
        self._library(mock_get_library)
        mock_client_type.return_value.search_subtitles_all.side_effect = (
            KeyboardInterrupt
        )

        result = runner.invoke(
            cli,
            ["research", "alpha beta", "--no-scout", "--depth", "1"],
        )

        assert result.exit_code != 0
        checkpoints = [
            call.kwargs
            for call in mock_log.call_args_list
            if call.args[0] == "research_checkpoint"
        ]
        assert any(
            event.get("phase") == "search"
            and event.get("status") == "started"
            for event in checkpoints
        )
        assert checkpoints[-1]["phase"] == "search"
        assert checkpoints[-1]["status"] == "interrupted"

    @patch(
        "filmot.commands.research._find_probe_pairs",
        return_value=[("language models", "neural networks", 4, 2)],
    )
    @patch(
        "filmot.commands.research._extract_probe_terms",
        return_value=["language models", "neural networks"],
    )
    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_probe_channel_scope_fails_closed_on_outside_result(
        self,
        mock_client_type,
        mock_get_library,
        mock_log,
        mock_proxy,
        mock_terms,
        mock_pairs,
        runner,
    ):
        library = self._library(mock_get_library)
        library.list_transcripts.return_value = [
            {"video_id": "saved-a"},
            {"video_id": "saved-b"},
        ]
        library.get.return_value = {
            "transcript": "language models and neural networks. " * 4
        }
        client = mock_client_type.return_value
        client.search_subtitles_all.return_value = {
            "result": [{
                "id": "candidate",
                "channelid": "UC_ALLOWED",
                "title": "alpha beta",
                "hits": [{"token": "alpha"}, {"token": "beta"}],
            }],
            "totalresultcount": 1,
        }
        client.search_subtitles.return_value = {
            "result": [{
                "id": "outside",
                "channelid": "UC_OUTSIDE",
                "title": "alpha beta",
                "hits": [],
            }],
            "totalresultcount": 1,
        }

        result = runner.invoke(
            cli,
            [
                "research",
                "alpha beta",
                "--no-scout",
                "--depth",
                "0",
                "--probe",
                "--channel-id",
                "UC_ALLOWED",
            ],
        )

        assert result.exit_code != 0
        assert "outside" in result.output
        assert any(
            call.args[0] == "research_probe"
            and call.kwargs.get("status") == "failed_closed"
            for call in mock_log.call_args_list
        )

    @patch(
        "filmot.commands.research._find_probe_pairs",
        return_value=[("language models", "neural networks", 4, 2)],
    )
    @patch(
        "filmot.commands.research._extract_probe_terms",
        return_value=["language models", "neural networks"],
    )
    @patch("filmot.transcript.is_proxy_configured", return_value=False)
    @patch("filmot.ledger.log_event")
    @patch("filmot.ledger.log_result")
    @patch("filmot.library.get_library")
    @patch("filmot.commands.research.FilmotClient")
    def test_probe_query_failure_is_aggregated(
        self,
        mock_client_type,
        mock_get_library,
        mock_log_result,
        mock_log,
        mock_proxy,
        mock_terms,
        mock_pairs,
        runner,
    ):
        library = self._library(mock_get_library)
        library.list_transcripts.return_value = [
            {"video_id": "saved-a"},
            {"video_id": "saved-b"},
        ]
        library.get.return_value = {
            "transcript": "language models and neural networks. " * 4
        }
        client = mock_client_type.return_value
        client.search_subtitles_all.return_value = {
            "result": [{
                "id": "candidate",
                "title": "alpha beta",
                "hits": [{"token": "alpha"}, {"token": "beta"}],
            }],
            "totalresultcount": 1,
        }
        client.search_subtitles.return_value = {"error": "probe unavailable"}

        result = runner.invoke(
            cli,
            [
                "research",
                "alpha beta",
                "--no-scout",
                "--depth",
                "0",
                "--probe",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "1 query failed" in result.output
        aggregate = mock_log_result.call_args
        assert aggregate.args[0] == "research"
        outcome = aggregate.args[1]
        assert outcome.data["probe_query_failed"] == 1
        assert outcome.status_value == "partial"
        assert outcome.errors[0].type == "ResearchItemFailure"
        end_events = [
            call
            for call in mock_log.call_args_list
            if call.args[0] == "research_end"
        ]
        assert end_events[-1].kwargs["status"] == "partial"


def _proxy_pool_with_sessions(tmp_path, sessions):
    """Build an in-memory API-backed pool without making management requests."""
    import time

    from filmot.proxy_pool import WebshareProxyPool

    pool = WebshareProxyPool(
        "test-token",
        state_path=tmp_path / "proxy-pool.sqlite3",
    )
    pool._sessions = sessions
    pool._last_refresh = time.time()
    for session in sessions:
        initial_successes = max(int(session.success), 0)
        if initial_successes:
            session.success = 0
            for _ in range(initial_successes):
                pool.report_success(session)
    return pool


class TestProxyCommands:
    def test_status_separates_availability_and_recent_health_without_secrets(
        self, tmp_path, runner
    ):
        import time

        from filmot.commands.proxy import _render_proxy_status
        from filmot.proxy_pool import WebshareSession

        untested = WebshareSession(
            id="credential-untested",
            username="secret-user-untested",
            password="secret-password-untested",
            country_code="US",
        )
        healthy = WebshareSession(
            id="credential-healthy",
            username="secret-user-healthy",
            password="secret-password-healthy",
            country_code="CA",
            success=1,
            last_success_at=time.time(),
        )
        pool = _proxy_pool_with_sessions(tmp_path, [untested, healthy])

        with (
            patch("filmot.proxy_pool.get_pool", return_value=pool),
            patch("filmot.ledger.log_event") as mock_log,
            patch(
                "filmot.commands.proxy._render_proxy_status",
                wraps=_render_proxy_status,
            ) as mock_render,
        ):
            result = runner.invoke(cli, ["proxy", "status", "--full"])

        assert result.exit_code == 0, result.output
        assert "2 total" in result.output
        assert "2 available now" in result.output
        assert "1 recently healthy" in result.output
        assert "1 untested" in result.output
        assert "ready-tested" in result.output
        assert "ready-untested" in result.output
        assert pool.redacted_session_id(untested) in result.output
        assert pool.redacted_session_id(healthy) in result.output
        for secret in (
            untested.id,
            untested.username,
            untested.password,
            healthy.id,
            healthy.username,
            healthy.password,
        ):
            assert secret not in result.output
            assert secret not in json.dumps(
                mock_render.call_args.args[0].to_dict()
            )
        status_outcome = mock_render.call_args.args[0]
        assert "username" not in status_outcome.data["sessions"][0]
        mock_log.assert_not_called()

    def test_file_backed_refresh_reloads_without_remote_rotation(self, runner):
        pool = MagicMock()
        pool.source = "session-file"
        pool.refresh.return_value = 2
        pool.available_count.return_value = 2
        pool.recently_healthy_count.return_value = 0

        with (
            patch("filmot.proxy_pool.get_pool", return_value=pool),
            patch("filmot.ledger.log_event") as mock_log,
        ):
            result = runner.invoke(cli, ["proxy", "refresh", "--full"])

        assert result.exit_code == 0, result.output
        assert "remote IP rotation is not applicable" in result.output
        assert "2 available" in result.output
        assert "0 recently healthy" in result.output
        pool.request_full_refresh.assert_not_called()
        pool.refresh.assert_called_once_with(force=True)
        mock_log.assert_not_called()

    def test_refresh_rotation_failure_is_a_typed_partial_outcome(self, runner):
        from filmot.commands.proxy import _render_proxy_refresh

        pool = MagicMock()
        pool.source = "webshare-api"
        pool.request_full_refresh.side_effect = RuntimeError(
            "rotation unavailable"
        )
        pool.refresh.return_value = 2
        pool.available_count.return_value = 2
        pool.recently_healthy_count.return_value = 1

        with (
            patch("filmot.proxy_pool.get_pool", return_value=pool),
            patch(
                "filmot.commands.proxy._render_proxy_refresh",
                wraps=_render_proxy_refresh,
            ) as mock_render,
            patch("filmot.ledger.log_event") as mock_log,
        ):
            result = runner.invoke(cli, ["proxy", "refresh", "--full"])

        assert result.exit_code == 1, result.output
        outcome = mock_render.call_args.args[0]
        assert outcome.status_value == "partial"
        assert outcome.errors[0].type == "RuntimeError"
        assert outcome.errors[0].stage == "remote_rotation"
        mock_log.assert_not_called()

    def test_proxy_test_uses_distinct_redacted_sessions_and_exits_partial(
        self, tmp_path, runner
    ):
        from filmot.commands.proxy import _render_proxy_test_summary
        from filmot.proxy_pool import WebshareSession

        first = WebshareSession(
            id="credential-first",
            username="secret-user-first",
            password="secret-password-first",
        )
        second = WebshareSession(
            id="credential-second",
            username="secret-user-second",
            password="secret-password-second",
        )
        pool = _proxy_pool_with_sessions(tmp_path, [first, second])
        probe_results = [
            {
                "transport_ok": True,
                "segment_count": 3,
                "route_elapsed_seconds": 0.01,
            },
            {
                "transport_ok": False,
                "failure_kind": "connection",
                "error_type": "TranscriptRouteTimeout",
                "route_elapsed_seconds": 0.02,
            },
        ]

        with (
            patch("filmot.proxy_pool.get_pool", return_value=pool),
            patch.object(pool, "pick", wraps=pool.pick) as mock_pick,
            patch(
                "filmot.transcript.probe_pool_session",
                side_effect=probe_results,
            ) as mock_probe,
            patch("filmot.ledger.log_event") as mock_log,
            patch(
                "filmot.commands.proxy._render_proxy_test_summary",
                wraps=_render_proxy_test_summary,
            ) as mock_render,
        ):
            result = runner.invoke(
                cli,
                [
                    "proxy",
                    "test",
                    "--count",
                    "3",
                    "--route-timeout",
                    "0.1",
                    "--total-timeout",
                    "5",
                ],
            )

        assert result.exit_code == 2, result.output
        assert "1 passed, 1 failed, 1 unattempted" in result.output
        assert len(mock_probe.call_args_list) == 2
        assert all(
            call_item.kwargs["lease"] is True
            for call_item in mock_pick.call_args_list
        )
        attempted = [call.args[1] for call in mock_probe.call_args_list]
        assert attempted == [first, second]
        assert pool.redacted_session_id(first) in result.output
        assert pool.redacted_session_id(second) in result.output
        for secret in (
            first.id,
            first.username,
            first.password,
            second.id,
            second.username,
            second.password,
        ):
            assert secret not in result.output
        outcome = mock_render.call_args.args[0]
        assert outcome.status_value == "partial"
        assert {error.type for error in outcome.errors} == {
            "ProxyProbeFailure",
            "IncompleteProxyProbe",
        }
        mock_log.assert_not_called()

    def test_proxy_test_with_no_passing_session_is_failure(
        self, tmp_path, runner
    ):
        from filmot.proxy_pool import WebshareSession

        session = WebshareSession(
            id="credential-only",
            username="secret-user-only",
            password="secret-password-only",
        )
        pool = _proxy_pool_with_sessions(tmp_path, [session])
        failed_probe = {
            "transport_ok": False,
            "failure_kind": "connection",
            "error_type": "TranscriptRouteTimeout",
            "route_elapsed_seconds": 0.01,
        }

        with (
            patch("filmot.proxy_pool.get_pool", return_value=pool),
            patch(
                "filmot.transcript.probe_pool_session",
                return_value=failed_probe,
            ),
            patch("filmot.ledger.log_event"),
        ):
            result = runner.invoke(
                cli,
                [
                    "proxy",
                    "test",
                    "--count",
                    "1",
                    "--route-timeout",
                    "0.1",
                ],
            )

        assert result.exit_code == 1, result.output
        assert "0 passed, 1 failed, 0 unattempted" in result.output
        assert "No proxy session passed" in result.output
        assert session.id not in result.output
        assert session.username not in result.output
        assert session.password not in result.output

    def test_proxy_test_empty_pool_gives_refresh_guidance(self, tmp_path, runner):
        pool = _proxy_pool_with_sessions(tmp_path, [])

        with (
            patch("filmot.proxy_pool.get_pool", return_value=pool),
            patch("filmot.transcript.probe_pool_session") as mock_probe,
        ):
            result = runner.invoke(cli, ["proxy", "test"])

        assert result.exit_code == 1, result.output
        assert "filmot proxy refresh" in result.output
        mock_probe.assert_not_called()


class TestLibraryTopicMigration:
    def test_migrate_topic_requires_confirmation_then_moves_legacy_files(
        self, tmp_path, runner
    ):
        from filmot.library import TranscriptLibrary

        library = TranscriptLibrary(str(tmp_path / ".filmot_data"))
        legacy_dir = library.transcripts_dir / "uncategorized"
        legacy_dir.mkdir(parents=True)
        legacy_path = legacy_dir / "legacy-video.json"
        legacy_path.write_text(
            json.dumps({
                "video_id": "legacy-video",
                "topic": "uncategorized",
                "transcript": "legacy transcript",
                "metadata": {},
            }),
            encoding="utf-8",
        )

        with (
            patch("filmot.library.get_library", return_value=library),
            patch("filmot.ledger.log_result") as mock_log,
        ):
            declined = runner.invoke(
                cli,
                ["library", "migrate-topic", "人工知能"],
                input="n\n",
            )
            migrated = runner.invoke(
                cli,
                ["library", "migrate-topic", "人工知能", "--yes"],
            )

        assert declined.exit_code == 1
        assert "assigns the entire source directory" in declined.output
        assert migrated.exit_code == 0, migrated.output
        assert "Migrated 1 transcript file" in migrated.output
        assert not legacy_path.exists()
        saved = library.get("legacy-video", "人工知能")
        assert saved["transcript"] == "legacy transcript"
        assert saved["topic"] == "人工知能"
        mock_log.assert_called_once()
        migration = mock_log.call_args
        assert migration.args[0] == "library_migrate_topic"
        outcome = migration.args[1]
        assert outcome.command == "library-migrate-topic"
        assert outcome.status_value == "completed"
        assert migration.kwargs["data"]["legacy_slug"] == "uncategorized"
        assert migration.kwargs["data"]["canonical_slug"] == "人工知能"
        assert migration.kwargs["data"]["migrated"] == 1


class TestSessionsRawContract:
    @patch("filmot.ledger.read_events")
    def test_named_session_raw_is_one_versioned_result(
        self, mock_read_events, runner
    ):
        mock_read_events.return_value = [
            {"ts": "2026-01-01T00:00:00", "kind": "search"},
            {"ts": "2026-01-01T00:01:00", "kind": "research"},
        ]

        result = runner.invoke(cli, ["sessions", "topic", "--raw"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["name"] == "topic"
        assert payload["events"] == mock_read_events.return_value
        assert payload["_filmot"] == {
            "schema": "filmot.result/v1",
            "command": "sessions",
            "status": "completed",
            "errors": [],
            "warnings": [],
        }

    @patch("filmot.ledger.read_events")
    def test_named_session_raw_summary_is_derived_without_replay_body(
        self, mock_read_events, runner
    ):
        mock_read_events.return_value = [{
            "ts": "2026-01-01T00:00:00",
            "kind": "search",
            "status": "completed",
            "data": {
                "query": "alpha",
                "api_total": 24,
                "page_count": 20,
                "post_filter_count": 15,
            },
        }]

        result = runner.invoke(
            cli,
            ["sessions", "topic", "--summary", "--raw"],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert "events" not in payload
        assert payload["summary"]["searches"]["scope_rows"][0] == {
            "ts": "2026-01-01T00:00:00",
            "status": "completed",
            "query": "alpha",
            "api_total": 24,
            "candidates_fetched": 20,
            "post_filter_count": 15,
            "partial": False,
        }

    def test_summary_requires_session_name(self, runner):
        result = runner.invoke(cli, ["sessions", "--summary"])

        assert result.exit_code == 2
        assert "requires a session NAME" in result.output

    @patch("filmot.ledger.list_sessions", return_value=[])
    def test_empty_session_inventory_raw_is_empty_versioned_result(
        self, mock_list_sessions, runner
    ):
        result = runner.invoke(cli, ["sessions", "--raw"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["rows"] == []
        assert payload["_filmot"]["status"] == "empty"

    def test_summary_surfaces_malformed_ledger_line_as_partial(
        self,
        runner,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        sessions_dir = tmp_path / ".filmot_data" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / "topic.jsonl").write_text(
            json.dumps({
                "ts": "2026-01-01T00:00:00",
                "kind": "search",
                "status": "completed",
                "query": "alpha",
                "results": 1,
            })
            + "\n{not-json\n",
            encoding="utf-8",
        )

        result = runner.invoke(
            cli,
            ["sessions", "topic", "--summary", "--raw"],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["_filmot"]["status"] == "partial"
        assert payload["_filmot"]["errors"][0]["stage"] == "read-session"
        assert payload["_filmot"]["errors"][0]["details"]["line"] == 2
        assert payload["summary"]["event_count"] == 1
        assert payload["summary"]["read_diagnostics"] == 1

    @pytest.mark.parametrize(
        "invalid_line",
        [
            '{"schema":"filmot.event/v1","data":{"score":NaN}}',
            (
                '{"schema":"filmot.event/v1","ts":"2026-01-01",'
                '"kind":"search","command":"search","status":"bogus",'
                '"data":{},"errors":[],"warnings":[]}'
            ),
        ],
    )
    def test_session_rejects_non_json_or_invalid_v1_event(
        self,
        runner,
        tmp_path,
        monkeypatch,
        invalid_line,
    ):
        monkeypatch.chdir(tmp_path)
        sessions_dir = tmp_path / ".filmot_data" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / "topic.jsonl").write_text(
            invalid_line + "\n",
            encoding="utf-8",
        )

        result = runner.invoke(cli, ["sessions", "topic", "--raw"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["_filmot"]["status"] == "failed"
        assert payload["_filmot"]["errors"][0]["stage"] == "read-session"
        json.dumps(payload, allow_nan=False)

    def test_unreadable_only_session_is_failed_not_empty(
        self,
        runner,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        sessions_dir = tmp_path / ".filmot_data" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / "topic.jsonl").write_text(
            "{not-json\n",
            encoding="utf-8",
        )

        result = runner.invoke(
            cli,
            ["sessions", "topic", "--summary", "--raw"],
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["_filmot"]["status"] == "failed"
        assert payload["summary"]["event_count"] == 0
        assert payload["summary"]["read_diagnostics"] == 1

    def test_inventory_does_not_hide_corrupt_session_file(
        self,
        runner,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        sessions_dir = tmp_path / ".filmot_data" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / "broken.jsonl").write_text(
            "{not-json\n",
            encoding="utf-8",
        )

        result = runner.invoke(cli, ["sessions", "--raw"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["rows"] == []
        assert payload["_filmot"]["status"] == "failed"
        assert payload["_filmot"]["errors"][0]["details"]["line"] == 1

    @pytest.mark.parametrize(
        ("arguments", "target"),
        [
            (["sessions", "--raw"], "filmot.ledger.list_sessions"),
            (["sessions", "topic", "--raw"], "filmot.ledger.read_events"),
        ],
    )
    def test_session_runtime_read_failure_is_one_raw_result(
        self,
        runner,
        arguments,
        target,
    ):
        with patch(target, side_effect=OSError("synthetic read failure")):
            result = runner.invoke(cli, arguments)

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["_filmot"]["status"] == "failed"
        assert payload["_filmot"]["errors"][0]["message"] == (
            "synthetic read failure"
        )

    def test_session_storage_file_is_reported_as_failed_topology(
        self,
        runner,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        data_dir = tmp_path / ".filmot_data"
        data_dir.mkdir()
        (data_dir / "sessions").write_text("not a directory", encoding="utf-8")

        result = runner.invoke(cli, ["sessions", "--raw"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["_filmot"]["status"] == "failed"
        assert payload["_filmot"]["errors"][0]["type"] == (
            "InvalidSessionStorage"
        )


class TestMainModule:
    """Test python -m filmot entry point."""

    def test_module_importable(self):
        """The __main__ module can be imported without executing main()."""
        from filmot import __main__
        assert hasattr(__main__, 'main')

    def test_script_entrypoint_propagates_click_usage_exit_code(self):
        repository = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "main.py", "transcript"],
            cwd=repository,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 2
        assert "Missing argument" in result.stderr

    def test_script_entrypoint_propagates_raw_operational_exit_code(self):
        repository = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                "main.py",
                "claims",
                "cite",
                "science",
                "c-missing",
                "--relation",
                "supports",
                "--raw",
            ],
            cwd=repository,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert payload["_filmot"]["status"] == "failed"

    @pytest.mark.skipif(
        os.name == "nt",
        reason="Unix broken-pipe exit behavior",
    )
    def test_real_subprocess_early_pipe_close_exits_zero(self, tmp_path):
        sessions_dir = tmp_path / ".filmot_data" / "sessions"
        sessions_dir.mkdir(parents=True)
        event = {
            "ts": "2026-01-01T00:00:00",
            "kind": "search",
            "payload": "x" * 1000,
        }
        (sessions_dir / "pipe.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for _ in range(3000)),
            encoding="utf-8",
        )
        environment = os.environ.copy()
        repository = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = (
            repository
            + (
                os.pathsep + environment["PYTHONPATH"]
                if environment.get("PYTHONPATH")
                else ""
            )
        )
        process = subprocess.Popen(
            [sys.executable, "-m", "filmot", "sessions", "pipe", "--raw"],
            cwd=tmp_path,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        assert process.stdout.read(1)
        process.stdout.close()
        stderr = process.stderr.read().decode(errors="replace")
        exit_code = process.wait(timeout=10)

        assert exit_code == 0, stderr
