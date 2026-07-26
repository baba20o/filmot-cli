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
        assert "API Key: configured" in result.output
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
            "segments": [],
            "language": "en",
            "segment_count": 1,
            "duration_seconds": 1,
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
        assert metadata["route"] == "direct"
        assert metadata["routes_tried"] == ["direct"]
        assert any(
            item.kwargs.get("topic") == "topic"
            for item in mock_log_event.call_args_list
        )
        logged = mock_log_result.call_args.args[1]
        assert logged.to_raw_dict() == payload
        assert "full_text" not in mock_log_result.call_args.kwargs["data"]

    @patch("filmot.proxy_pool.get_pool", return_value=None)
    @patch("filmot.ledger.log_event")
    @patch("filmot.library.get_library")
    @patch("filmot.transcript.get_transcript")
    def test_raw_library_save_failure_is_one_json_error(
        self,
        mock_get_transcript,
        mock_get_library,
        mock_log,
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
        failed_save = [
            call
            for call in mock_log.call_args_list
            if call.args[0] == "transcript_save"
            and call.kwargs.get("status") == "failed"
        ]
        assert len(failed_save) == 1

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
            "segments": [],
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
        assert "Scout relevance gate: 1 -> 0" in result.output
        mock_transcript.assert_not_called()

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
            "segments": [],
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
        legacy_dir.mkdir()
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

    @patch("filmot.ledger.list_sessions", return_value=[])
    def test_empty_session_inventory_raw_is_empty_versioned_result(
        self, mock_list_sessions, runner
    ):
        result = runner.invoke(cli, ["sessions", "--raw"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["rows"] == []
        assert payload["_filmot"]["status"] == "empty"


class TestMainModule:
    """Test python -m filmot entry point."""

    def test_module_importable(self):
        """The __main__ module can be imported without executing main()."""
        from filmot import __main__
        assert hasattr(__main__, 'main')

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
