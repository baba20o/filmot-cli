"""--source on transcript-producing commands, and index transcripts in the library."""

import importlib
import json
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from filmot.cli import cli
from filmot.library import TranscriptLibrary


FIXTURE = Path(__file__).parent / "fixtures" / "filmot_api" / "get_videos_subtitles.json"
VIDEO_ID = "abc12345678"
YOUTUBE_FAILURE = {
    "error": "Transcripts are disabled for this video",
    "error_type": "TranscriptsDisabled",
    "video_id": VIDEO_ID,
    "routes_tried": ["direct"],
    "route_errors": [],
}
TRANSCRIPT = {
    "video_id": VIDEO_ID,
    "full_text": "Observations recorded.",
    "segments": [{"text": "Observations recorded.", "start": 0.0, "duration": 2.0}],
    "language": "en",
    "duration_seconds": 2.0,
    "segment_count": 1,
    "source": "youtube",
    "route": "direct",
}

research_module = importlib.import_module("filmot.commands.research")


def index_row():
    return dict(json.loads(FIXTURE.read_text(encoding="utf-8"))[0], id=VIDEO_ID)


def index_client():
    client = MagicMock(last_provider="rapidapi")
    client.get_videos.return_value = [index_row()]
    return client


@pytest.fixture
def run(stream_runner):
    def invoke(*args, **kwargs):
        return stream_runner.invoke(cli, list(args), env={"COLUMNS": "200"}, **kwargs)

    return invoke


def test_transcript_can_come_only_from_the_index(run):
    with (
        patch("filmot.index_transcript.FilmotClient", return_value=index_client()),
        patch("filmot.transcript._youtube_transcript", side_effect=AssertionError("no YouTube")),
    ):
        raw = run("transcript", VIDEO_ID, "--source", "filmot", "--raw")
        human = run("transcript", VIDEO_ID, "--source", "filmot")

    assert raw.exit_code == 0, raw.output
    payload = json.loads(raw.stdout)
    assert payload["_filmot"]["status"] == "completed"
    assert (payload["source"], payload["route"]) == ("filmot_index", "filmot-index")
    assert payload["transcript_index"]["observed_at"] == "2026-01-02"
    assert human.exit_code == 0, human.output
    assert "Filmot subtitle index only" in human.stderr
    assert "From Filmot's subtitle index (en manual captions; snapshot 2026-01-02)" in human.stdout


def save_index_fallback(run, library, monkeypatch):
    monkeypatch.setattr("filmot.library.get_library", lambda: library)
    metadata = MagicMock(last_provider="rapidapi")
    metadata.get_videos.return_value = [{"id": VIDEO_ID, "title": "Zoo", "channelname": "jawed"}]

    with (
        patch("filmot.transcript._youtube_transcript", return_value=dict(YOUTUBE_FAILURE)),
        patch("filmot.index_transcript.FilmotClient", return_value=index_client()),
        patch("filmot.commands.transcript.FilmotClient", return_value=metadata),
    ):
        return run("transcript", VIDEO_ID, "--save-to", "zoo", "--source", "auto")


def test_a_fallback_transcript_is_saved_with_its_index_provenance(run, tmp_path, monkeypatch):
    library = TranscriptLibrary(data_dir=tmp_path / "data")

    result = save_index_fallback(run, library, monkeypatch)

    assert result.exit_code == 0, result.output
    assert "-> Filmot subtitle index" in result.stderr
    assert "YouTube failed: TranscriptsDisabled" in result.stdout
    record = library.get(VIDEO_ID, "zoo")
    assert record["metadata"]["source"] == "filmot_index"
    assert record["metadata"]["routes_tried"] == ["direct", "filmot-index"]
    assert record["metadata"]["transcript_index"]["youtube_error_type"] == "TranscriptsDisabled"
    assert record["source"]["transcript_source"] == "filmot_index"
    assert [segment["start"] for segment in record["segments"]] == [1.2, 5.318, 7.974]


def test_a_saved_index_transcript_keeps_its_provenance_when_reused_for_grep(run, tmp_path, monkeypatch):
    library = TranscriptLibrary(data_dir=tmp_path / "data")
    assert save_index_fallback(run, library, monkeypatch).exit_code == 0

    with (
        patch("filmot.transcript._youtube_transcript", side_effect=AssertionError("no YouTube")),
        patch("filmot.index_transcript.FilmotClient", side_effect=AssertionError("no index")),
    ):
        result = run("transcript", VIDEO_ID, "--grep", "recorded")

    assert result.exit_code == 0, result.output
    assert "From Filmot's subtitle index (en manual captions; snapshot 2026-01-02)." in result.stdout
    # The earlier YouTube failure belongs to the save, not to this lookup.
    assert "YouTube failed" not in result.stdout


def test_download_passes_the_source_to_each_fetch(run):
    piped = json.dumps({
        "videos": [{"video_id": VIDEO_ID, "title": "Known", "channel_title": "Channel"}],
        "_filmot": {"schema": "filmot.result/v1", "command": "yt-search", "status": "completed"},
    })
    with (
        patch("filmot.library.get_library") as get_library,
        patch("filmot.transcript.get_transcript", return_value=dict(TRANSCRIPT)) as fetch,
    ):
        get_library.return_value.exists.return_value = False
        get_library.return_value.list_transcripts.return_value = []
        result = run("download", "--topic", "topic", "--count", "1", "--source", "youtube", input=piped)

    assert result.exit_code == 0, result.output
    assert fetch.call_args.kwargs["source"] == "youtube"


def test_search_bulk_download_passes_the_source(run):
    with (
        patch("filmot.commands.search.FilmotClient") as client_type,
        patch("filmot.library.get_library") as get_library,
        patch("filmot.transcript.get_transcript", return_value=dict(TRANSCRIPT)) as fetch,
    ):
        client = client_type.return_value
        client.last_query_rewrite = None
        client.last_cache_hit = False
        client.search_subtitles.return_value = {
            "result": [{"id": VIDEO_ID, "title": "Known", "channelname": "Channel", "hits": []}],
            "totalresultcount": 1,
        }
        get_library.return_value.exists.return_value = False
        get_library.return_value.list_transcripts.return_value = []
        result = run("search", "alpha", "--bulk-download", "topic:1", "--source", "filmot")

    assert result.exit_code == 0, result.output
    assert fetch.call_args.kwargs["source"] == "filmot"


def test_research_passes_the_source_and_reports_the_plan(run, monkeypatch, library):
    client = Mock()
    client.search_subtitles_all.return_value = {
        "result": [{
            "id": VIDEO_ID, "title": "Alpha beta field study", "channelname": "Research",
            "duration": 600, "viewcount": 1000,
            "hits": [{"token": "alpha beta"}, {"token": "alpha beta"}],
        }],
        "totalresultcount": 1,
    }
    monkeypatch.setattr(research_module, "FilmotClient", lambda: client)
    monkeypatch.setattr("filmot.library.get_library", lambda: library)
    monkeypatch.setattr(
        research_module, "_backfill_metadata", lambda video_id, title, channel: (title, channel),
    )
    calls = []

    def fetch(video_id, **kwargs):
        calls.append(kwargs)
        return dict(TRANSCRIPT, video_id=video_id)

    monkeypatch.setattr("filmot.transcript.get_transcript", fetch)

    result = run("research", "alpha beta", "--depth", "1", "--raw", "--no-scout", "--source", "filmot")

    assert result.exit_code == 0, result.output
    assert calls and all(call["source"] == "filmot" for call in calls)
    assert json.loads(result.stdout)["routing_plan"]["index"] == "only"


@pytest.mark.parametrize("arguments", [
    ["transcript", VIDEO_ID, "--source", "filmot", "--fallback"],
    ["download", "--topic", "topic", "--source", "filmot", "--fallback"],
    ["search", "alpha", "--bulk-download", "topic:1", "--source", "filmot", "--fallback"],
    ["research", "alpha", "--source", "filmot", "--fallback"],
])
def test_index_only_mode_refuses_the_aws_fallback(run, arguments):
    with patch("filmot.transcript.get_transcript", side_effect=AssertionError("no fetch")):
        result = run(*arguments)

    assert result.exit_code == 2
    assert "cannot be combined with --source filmot" in result.output + result.stderr


def test_the_environment_can_select_index_only_mode_too(stream_runner):
    with patch("filmot.transcript.get_transcript", side_effect=AssertionError("no fetch")):
        result = stream_runner.invoke(
            cli, ["transcript", VIDEO_ID, "--fallback"],
            env={"FILMOT_TRANSCRIPT_SOURCE": "filmot"},
        )

    assert result.exit_code == 2
    assert "FILMOT_TRANSCRIPT_SOURCE=filmot" in result.output + result.stderr


def test_the_library_names_index_transcripts_consistently():
    canonical = TranscriptLibrary._canonical_transcript_source

    assert canonical("", "filmot-index") == "filmot_index"
    assert canonical("filmot-index") == "filmot_index"
    assert canonical("", "aws-transcribe") == "aws_transcribe"
    assert canonical(None) == "youtube"
