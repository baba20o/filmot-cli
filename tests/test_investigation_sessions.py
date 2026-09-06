"""Investigation routing through actual commands and their nested operations."""

import json
from contextvars import Context
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import click
from click.testing import CliRunner

from filmot.cli import cli
from filmot.ledger import log_event, read_events
from filmot.library import get_library
from filmot.session_context import current_session, session_option


def _search_client(mock):
    client = mock.return_value
    client.last_cache_hit = False
    client.last_query_rewrite = None
    client.search_subtitles.return_value = {"result": [], "totalresultcount": 0}
    return client


def test_investigation_collects_search_save_and_claims_without_moving_topic(tmp_path):
    runner = CliRunner()
    environment = {"FILMOT_SESSION": "Memory Investigation"}
    with patch("filmot.commands.search.FilmotClient") as client:
        _search_client(client)
        result = runner.invoke(cli, ["search", "memory", "--raw"], env=environment)
    assert result.exit_code == 0, result.output

    transcript = {
        "video_id": "abc12345678", "language": "en", "full_text": "Memory evidence.",
        "segments": [{"start": 2.0, "duration": 3.0, "text": "Memory evidence."}],
        "duration_seconds": 5.0, "segment_count": 1,
    }
    with patch("filmot.transcript.get_transcript", return_value=transcript), patch(
        "filmot.commands.transcript.FilmotClient"
    ) as client:
        client.return_value.get_videos.return_value = [{"title": "Memory"}]
        result = runner.invoke(cli, ["transcript", "abc12345678", "--save-to", "agent-memory", "--raw"], env=environment)
    assert result.exit_code == 0, result.output
    assert get_library().exists("abc12345678", "agent-memory")
    assert not get_library().exists("abc12345678", "memory-investigation")

    commands = [
        ["claims", "add", "agent-memory", "Memory can be revised.", "--id", "memory-claim", "--raw"],
        ["claims", "cite", "agent-memory", "memory-claim", "--video", "abc12345678", "--at", "2", "--relation", "supports", "--raw"],
        ["claims", "assess", "agent-memory", "memory-claim", "--verdict", "supported", "--raw"],
    ]
    for arguments in commands:
        result = runner.invoke(cli, arguments, env=environment)
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["topic"] == "agent-memory"

    events = read_events("memory-investigation")
    kinds = {event["kind"] for event in events}
    assert {"search", "transcript", "transcript_save", "claims_add", "claims_cite", "claims_assess"} <= kinds
    assert all(event["data"]["session"] == "memory-investigation" for event in events)
    assert all(event["topic"] == "agent-memory" for event in events if event["kind"].startswith("claims_") or event["kind"] == "transcript_save")
    assert not read_events("agent-memory")
    assert current_session() is None


@pytest.mark.parametrize("arguments, expected", [
    (["--session", "root", "claims", "add", "topic", "statement", "--raw"], "root"),
    (["--session", "root", "claims", "--session", "group", "add", "topic", "statement", "--raw"], "group"),
    (["--session", "root", "claims", "--session", "group", "add", "topic", "statement", "--session", "leaf", "--raw"], "leaf"),
])
def test_nearest_explicit_flag_beats_parent_and_environment(arguments, expected):
    result = CliRunner().invoke(cli, arguments, env={"FILMOT_SESSION": "environment"})
    assert result.exit_code == 0, result.output
    assert len(read_events(expected)) == 1
    assert not read_events("environment")
    assert current_session() is None


def test_failed_command_closes_session_and_defaults_stay_unchanged():
    with patch("filmot.commands.search.FilmotClient", side_effect=RuntimeError("offline")):
        result = CliRunner().invoke(cli, ["search", "memory", "--session", "failed", "--raw"])
    assert result.exit_code == 1
    assert read_events("failed")[0]["status"] == "failed"
    assert current_session() is None
    log_event("ad_hoc", query="unrelated")
    log_event("topic_event", topic="corpus")
    assert len(read_events("failed")) == 1
    assert len(read_events(datetime.now().strftime("%Y-%m-%d"))) == 1
    assert len(read_events("corpus")) == 1


def test_invalid_command_options_do_not_leak_session():
    result = CliRunner().invoke(cli, ["--session", "transient", "search", "word", "--pages", "0"])
    assert result.exit_code == 2
    assert current_session() is None
    assert not read_events("transient")


@pytest.mark.parametrize("arguments", [
    ["search", "word", "--session", "transient", "--pages", "0"],
    ["search", "word", "--pages", "0", "--session", "transient"],
    ["claims", "--session", "group", "unknown-command"],
    ["claims", "--session", "group", "add", "topic", "--session", "leaf"],
    ["claims", "cite", "topic", "claim", "--session", "leaf", "--relation", "invalid"],
], ids=[
    "search-local-before-invalid-range", "search-local-after-invalid-range",
    "claims-group-unknown-command", "claims-leaf-missing-argument",
    "claims-cite-invalid-choice",
])
def test_local_parse_errors_release_session_before_unrelated_activity(arguments):
    # Run inside a new context so a regression cannot poison later test cases.
    # Observe state and actual ledger routing before discarding that context.
    def invoke_and_observe():
        result = CliRunner().invoke(cli, arguments)
        after = current_session()
        log_event("after_parser_failure")
        return result, after

    result, after = Context().run(invoke_and_observe)

    assert result.exit_code == 2, result.output
    assert after is None
    events = read_events(datetime.now().strftime("%Y-%m-%d"))
    assert [event["kind"] for event in events] == ["after_parser_failure"]
    for session in ("transient", "root", "group", "leaf"):
        assert not read_events(session)


@pytest.mark.parametrize("arguments, environment", [
    (["search", "word", "--pages", "0"], {"FILMOT_SESSION": "environment"}),
    (["--session", "root", "claims", "--session", "group", "assess", "topic", "claim",
      "--session", "leaf", "--verdict", "invalid"], {"FILMOT_SESSION": "environment"}),
    (["--session", "root", "search", "word", "--session", "   "], {}),
], ids=[
    "environment-default", "nested-parent-group-and-leaf", "blank-leaf-session",
])
def test_parse_error_restores_enclosing_explicit_session(arguments, environment):
    def invoke_with_enclosing_session():
        observed = {}

        @click.command()
        @session_option
        def enclosing_command():
            observed["before"] = current_session()
            observed["result"] = CliRunner().invoke(cli, arguments, env=environment)
            observed["after"] = current_session()
            log_event("after_nested_parser_failure")

        observed["outer_result"] = CliRunner().invoke(
            enclosing_command, ["--session", "enclosing"]
        )
        observed["closed"] = current_session()
        return observed

    observed = Context().run(invoke_with_enclosing_session)

    assert observed["outer_result"].exit_code == 0, observed["outer_result"].output
    assert observed["result"].exit_code == 2, observed["result"].output
    assert observed["before"] == observed["after"] == "enclosing"
    assert observed["closed"] is None
    assert [event["kind"] for event in read_events("enclosing")] == ["after_nested_parser_failure"]
    for session in ("environment", "root", "group", "leaf"):
        assert not read_events(session)


def test_named_session_does_not_migrate_another_investigations_topic(tmp_path):
    # A Unicode topic's old ASCII fallback can collide with an explicitly named
    # investigation. Its topic/query is not permission to steal its history.
    with patch("filmot.commands.search.FilmotClient") as client:
        _search_client(client)
        result = CliRunner().invoke(cli, ["search", "人工知能", "--session", "session", "--raw"])
    assert result.exit_code == 0, result.output
    assert not read_events("人工知能")
    log_event("research_start", topic="人工知能", query="人工知能")
    assert len(read_events("人工知能")) == 1
    assert len(read_events("session")) == 1


def test_parallel_download_item_events_inherit_investigation(tmp_path):
    downloader = MagicMock()
    downloader._resolve_channel_dir.return_value = ("example", tmp_path / "example")
    downloader._load_manifest.return_value = {"videos": {}}
    info = {"name": "Example", "video_count": 2, "subscriber_count": 12, "uploads_playlist_id": "uploads"}
    videos = [{"video_id": video, "title": "Video", "published_at": "2026-01-01"} for video in ("one", "two")]
    with patch("filmot.channel_dl.ChannelDownloader", return_value=downloader), patch(
        "filmot.channel_dl.get_channel_info", return_value=info
    ), patch("filmot.channel_dl.list_all_video_ids", return_value=videos), patch(
        "filmot.transcript.get_transcript", return_value={"error": "transport failed"}
    ):
        result = CliRunner().invoke(cli, ["channel-download", "UC-example", "--workers", "2", "--delay", "0", "--session", "parallel"])
    assert result.exit_code == 1, result.output
    items = [event for event in read_events("parallel") if event["kind"] == "channel_download_item"]
    assert {event["data"]["video_id"] for event in items} == {"one", "two"}
    assert current_session() is None
