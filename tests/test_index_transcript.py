"""Transcripts from Filmot's subtitle index: source choice, parsing, and fallback."""

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from filmot import transcript as transcript_module
from filmot.api import FilmotClient
from filmot.api_providers import ProviderConfigurationError, _policy_override, selected_policy
from filmot.index_transcript import (
    _choose_track,
    _segments,
    fetch_index_transcript,
    transcript_source,
)


FIXTURE = Path(__file__).parent / "fixtures" / "filmot_api" / "get_videos_subtitles.json"
VIDEO_ID = "abc12345678"
YOUTUBE_FAILURE = {
    "error": "Transcripts are disabled for this video",
    "error_type": "TranscriptsDisabled",
    "video_id": VIDEO_ID,
    "routes_tried": ["direct"],
    "route_errors": [],
}


def row(**changes):
    value = dict(json.loads(FIXTURE.read_text(encoding="utf-8"))[0], id=VIDEO_ID)
    value.update(changes)
    return value


def track(lang, isauto=0, lines=(("caption", 1.0),)):
    return {"lang": lang, "isauto": isauto, "transcript": [{"t": t, "s": s} for t, s in lines]}


class IndexClient:
    def __init__(self, response, provider="rapidapi"):
        self.response = response
        self.calls = []
        self.last_provider = provider

    def get_videos(self, video_ids, flags=None, columns=None, lang=None):
        self.calls.append({"ids": video_ids, "columns": columns, "lang": lang})
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


@pytest.fixture
def index_client():
    def install(response, provider="rapidapi"):
        client = IndexClient(response, provider)
        patcher = patch("filmot.index_transcript.FilmotClient", return_value=client)
        patcher.start()
        installed.append(patcher)
        return client

    installed = []
    yield install
    for patcher in installed:
        patcher.stop()


# ----------------------------------------------------------------- parsing


def test_source_comes_from_argument_then_environment_then_auto(monkeypatch):
    monkeypatch.delenv("FILMOT_TRANSCRIPT_SOURCE", raising=False)
    assert transcript_source() == "auto"
    monkeypatch.setenv("FILMOT_TRANSCRIPT_SOURCE", " Filmot ")
    assert transcript_source() == "filmot"
    assert transcript_source("youtube") == "youtube"
    with pytest.raises(ValueError, match="transcript source"):
        transcript_source("archive")


def test_track_choice_follows_language_order_and_prefers_manual_captions():
    tracks = [track("en", isauto=1), track("de"), track("en", isauto=0), "junk", {"lang": 5}]

    assert _choose_track(tracks, ["en"]) is tracks[2]
    assert _choose_track(tracks, ["en-US"])["lang"] == "en"
    assert _choose_track(tracks, ["fr", "de"]) is tracks[1]
    assert _choose_track(tracks, ["fr"]) is None
    assert _choose_track(None, ["en"]) is None


def test_segments_run_to_the_next_line_and_skip_unusable_lines():
    lines = [
        {"t": "third", "s": 9.0}, {"t": "first", "s": 1.0}, {"t": "second", "s": 4.5},
        {"t": "bool", "s": True}, {"t": "negative", "s": -1}, {"t": "nan", "s": float("nan")},
        {"t": 7, "s": 2.0}, "junk",
    ]

    segments = _segments(lines, video_duration=30)

    assert segments == [
        {"text": "first", "start": 1.0, "duration": 3.5},
        {"text": "second", "start": 4.5, "duration": 4.5},
        {"text": "third", "start": 9.0, "duration": 10.0},
    ]
    assert _segments([{"t": "end", "s": 17.0}], video_duration=19)[0]["duration"] == 2.0
    assert _segments([{"t": "end", "s": 17.0}], video_duration=None)[0]["duration"] == 0.0


# ------------------------------------------------------------------- fetch


def test_index_transcript_matches_the_transcript_result_shape(index_client):
    client = index_client([row()])

    result = fetch_index_transcript(VIDEO_ID)

    assert client.calls == [{"ids": VIDEO_ID, "columns": ("subtitles", "lastfetchdate"), "lang": "en"}]
    assert result["video_id"] == VIDEO_ID
    assert (result["language"], result["is_generated"]) == ("en", False)
    assert [segment["start"] for segment in result["segments"]] == [1.2, 5.318, 7.974]
    assert result["segments"][0]["duration"] == pytest.approx(4.118)
    assert result["full_text"] == " ".join(["recorded caption line"] * 3)
    assert result["segment_count"] == 3
    assert result["duration_seconds"] == pytest.approx(7.974 + 10.0)
    assert (result["source"], result["route"]) == ("filmot_index", "filmot-index")
    assert result["transcript_index"] == {
        "provider": "filmot", "language": "en", "is_generated": False,
        "observed_at": "2026-01-02", "api_provider": "rapidapi",
    }


def test_several_base_languages_request_every_track(index_client):
    client = index_client([row(subtitles=[track("de", lines=(("Hallo", 0.0),))])])

    result = fetch_index_transcript(VIDEO_ID, ["en", "de"], preserve_formatting=True)

    assert client.calls[0]["lang"] is None
    assert result["language"] == "de"
    assert result["full_text"] == "Hallo"


@pytest.mark.parametrize("response,error_type,message", [
    (ProviderConfigurationError("No Filmot API credentials are configured."),
     "IndexUnavailable", "No Filmot API credentials"),
    (RuntimeError("upstream echoed test-direct-key"), "RuntimeError", "RuntimeError: upstream echoed"),
    ({"error": "quota_exhausted", "error_type": "FilmotQuotaExhausted"}, "FilmotQuotaExhausted", "quota_exhausted"),
    ([], "VideoNotIndexed", "no record of this video"),
    ([row(subtitles=[track("de"), track("ja")])], "NoIndexTranscript", "(it has: de, ja)"),
    ([row(subtitles=[track("en", lines=(("  ", 1.0),))])], "NoIndexTranscript", "no en/en-US/en-GB subtitles for this video."),
])
@pytest.mark.parametrize("timeout", [None, 5.0], ids=["inline", "bounded"])
def test_index_failures_are_typed_results_never_exceptions(
    index_client, response, error_type, message, timeout,
):
    if isinstance(response, ProviderConfigurationError):
        with patch("filmot.index_transcript.FilmotClient", side_effect=response):
            result = fetch_index_transcript(VIDEO_ID, timeout=timeout)
    else:
        index_client(response)
        result = fetch_index_transcript(VIDEO_ID, timeout=timeout)

    assert result["error_type"] == error_type
    assert message in result["error"]
    assert "test-direct-key" not in result["error"]
    assert (result["video_id"], result["route"]) == (VIDEO_ID, "filmot-index")


# --------------------------------------------------------------- fallback


@pytest.fixture
def youtube():
    with patch.object(transcript_module, "_youtube_transcript") as fake:
        yield fake


def test_auto_falls_back_to_the_index_when_youtube_fails(youtube, index_client, monkeypatch):
    monkeypatch.setenv("FILMOT_TRANSCRIPT_SOURCE", "auto")
    youtube.return_value = dict(YOUTUBE_FAILURE)
    index_client([row()])
    events = []

    result = transcript_module.get_transcript(VIDEO_ID, progress_callback=events.append)

    assert result["source"] == "filmot_index"
    assert result["routes_tried"] == ["direct", "filmot-index"]
    assert result["youtube_error_type"] == "TranscriptsDisabled"
    assert result["transcript_index"]["youtube_error_type"] == "TranscriptsDisabled"
    assert [(event["event"], event["route"]) for event in events] == [
        ("route_start", "filmot-index"), ("route_success", "filmot-index"),
    ]
    assert events[0]["attempt"] == 2


def test_a_failed_index_keeps_the_youtube_error_and_records_the_attempt(youtube, index_client, monkeypatch):
    monkeypatch.setenv("FILMOT_TRANSCRIPT_SOURCE", "auto")
    youtube.return_value = dict(YOUTUBE_FAILURE)
    index_client([])

    result = transcript_module.get_transcript(VIDEO_ID)

    assert result["error_type"] == "TranscriptsDisabled"
    assert result["routes_tried"] == ["direct", "filmot-index"]
    assert result["route_errors"][-1]["error_type"] == "VideoNotIndexed"


def test_without_credentials_the_index_is_skipped_silently(youtube, monkeypatch):
    import filmot.config as app_config

    monkeypatch.setenv("FILMOT_TRANSCRIPT_SOURCE", "auto")
    monkeypatch.setattr(app_config, "DIRECT_API_KEY", "")
    monkeypatch.setattr(app_config, "RAPIDAPI_KEY", "")
    youtube.return_value = dict(YOUTUBE_FAILURE)
    events = []

    with patch("filmot.index_transcript.FilmotClient", side_effect=AssertionError("no index")):
        result = transcript_module.get_transcript(VIDEO_ID, progress_callback=events.append)

    assert result == YOUTUBE_FAILURE
    assert events == []


def test_an_empty_manual_track_gives_way_to_a_usable_automatic_one():
    tracks = [track("en", isauto=0, lines=(("   ", 1.0),)), track("en", isauto=1, lines=(("words", 2.0),))]

    assert _choose_track(tracks, ["en"]) is tracks[1]


def test_the_index_attempt_is_bounded_by_the_route_timeout(youtube, index_client):
    youtube.return_value = dict(YOUTUBE_FAILURE)
    events = []

    with patch("filmot.index_transcript.FilmotClient") as client_type:
        client_type.return_value = IndexClient([row()])
        transcript_module.get_transcript(
            VIDEO_ID, source="auto", route_timeout=3.5, progress_callback=events.append,
        )

    assert client_type.call_args.kwargs["deadline"].seconds == 3.5
    assert events[0]["timeout_s"] == 3.5


def stalled_index(stall, release):
    """A FilmotClient stand-in that blocks while being built or while answering."""
    client = IndexClient([row()])
    answer = client.get_videos

    def get_videos(*args, **kwargs):
        if stall == "request":
            release.wait(10)
        return answer(*args, **kwargs)

    def build(**_):
        # Building the client stands in for any wait before the request is sent.
        if stall == "client":
            release.wait(10)
        return client

    client.get_videos = get_videos
    return build


@pytest.mark.parametrize("stall", ["client", "request"])
def test_the_index_wait_is_bounded_in_wall_clock_time(stall):
    release = threading.Event()
    started = time.monotonic()
    try:
        with patch("filmot.index_transcript.FilmotClient", side_effect=stalled_index(stall, release)):
            result = fetch_index_transcript(VIDEO_ID, timeout=0.2)
        elapsed = time.monotonic() - started
    finally:
        release.set()

    assert result["error_type"] == "IndexTimeout"
    assert "within 0.2s" in result["error"]
    assert elapsed < 3


def test_an_index_timeout_is_reported_as_a_route_timeout():
    release = threading.Event()
    events = []
    try:
        with patch("filmot.index_transcript.FilmotClient", side_effect=stalled_index("request", release)):
            result = transcript_module.get_transcript(
                VIDEO_ID, source="filmot", route_timeout=0.2, progress_callback=events.append,
            )
    finally:
        release.set()

    assert [event["event"] for event in events] == ["route_start", "route_timeout"]
    assert (events[1]["route"], events[1]["kind"]) == ("filmot-index", "index")
    assert result["route_errors"] == [{
        "route": "filmot-index", "kind": "index",
        "error_type": "IndexTimeout", "error": result["error"],
    }]


def real_client_after(release, sent):
    """Builds a real FilmotClient once ``release`` is set; requests are recorded, never sent."""
    def record(*args, **request):
        sent.append(request)
        raise requests.exceptions.ConnectionError("not sent in tests")

    def build(**kwargs):
        release.wait(10)
        client = FilmotClient(use_cache=False, **kwargs)
        client.session.request = record
        client.rate_limiter = MagicMock()
        return client

    return build


def join_abandoned_lookups():
    for thread in threading.enumerate():
        if thread.name == "filmot-index-lookup":
            thread.join(5)


def test_a_lookup_abandoned_during_setup_never_sends_a_request():
    release, sent = threading.Event(), []
    with patch("filmot.index_transcript.FilmotClient", side_effect=real_client_after(release, sent)):
        try:
            result = fetch_index_transcript(VIDEO_ID, timeout=0.2)
        finally:
            release.set()
        join_abandoned_lookups()

    assert result["error_type"] == "IndexTimeout"
    assert sent == []


@pytest.mark.parametrize("step", ["start", "join"])
def test_ctrl_c_while_waiting_stops_the_lookup_from_sending(step):
    release, sent = threading.Event(), []
    original = getattr(threading.Thread, step)

    def interrupted(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise KeyboardInterrupt

    with patch("filmot.index_transcript.FilmotClient", side_effect=real_client_after(release, sent)):
        with patch.object(threading.Thread, step, interrupted):
            with pytest.raises(KeyboardInterrupt):
                fetch_index_transcript(VIDEO_ID, timeout=30.0)
        release.set()
        join_abandoned_lookups()

    assert sent == []


def test_the_bounded_lookup_keeps_this_invocations_api_provider():
    seen = []

    def build(**_):
        seen.append(selected_policy())
        return IndexClient([row()])

    token = _policy_override.set("direct")
    try:
        with patch("filmot.index_transcript.FilmotClient", side_effect=build):
            result = fetch_index_transcript(VIDEO_ID, timeout=5.0)
    finally:
        _policy_override.reset(token)

    assert "error" not in result
    assert seen == ["direct"]


def test_index_only_mode_never_reaches_aws(youtube, index_client):
    index_client([])

    with patch("filmot.aws_transcribe.transcribe_video", side_effect=AssertionError("no AWS")):
        result = transcript_module.get_transcript_with_fallback(VIDEO_ID, source="filmot")

    youtube.assert_not_called()
    assert result["error_type"] == "VideoNotIndexed"
    assert result["routes_tried"] == ["filmot-index"]


def test_aws_runs_when_youtube_and_the_index_both_fail(youtube, index_client):
    youtube.return_value = dict(YOUTUBE_FAILURE)
    index_client([])

    with (
        patch("filmot.aws_transcribe.check_dependencies", return_value=(True, "")),
        patch("filmot.aws_transcribe.transcribe_video", return_value=("spoken words", "en")),
    ):
        result = transcript_module.get_transcript_with_fallback(VIDEO_ID, source="auto")

    assert result["source"] == "aws_transcribe"
    assert result["routes_tried"] == ["direct", "filmot-index", "aws-transcribe"]
    assert result["youtube_error_type"] == "TranscriptsDisabled"


def test_an_index_only_plan_does_not_prepare_youtube_routing():
    with (
        patch("filmot.index_transcript.configured_providers", return_value={"direct": object()}),
        patch.object(transcript_module, "_init_api", side_effect=AssertionError("no YouTube")),
    ):
        plan = transcript_module.routing_plan("filmot")

    assert (plan["mode"], plan["routes"], plan["index"]) == ("index-only", [], "only")


@pytest.mark.parametrize("source", ["youtube", "auto"])
def test_youtube_success_or_youtube_source_never_queries_the_index(youtube, source):
    youtube.return_value = (
        {"video_id": VIDEO_ID, "language": "en", "full_text": "ok", "segments": []}
        if source == "auto" else dict(YOUTUBE_FAILURE)
    )

    with patch("filmot.index_transcript.FilmotClient", side_effect=AssertionError("no index")):
        result = transcript_module.get_transcript(VIDEO_ID, source=source)

    assert result == youtube.return_value


def test_filmot_source_skips_youtube(youtube, index_client, monkeypatch):
    monkeypatch.setenv("FILMOT_TRANSCRIPT_SOURCE", "filmot")
    index_client([row()])

    result = transcript_module.get_transcript(VIDEO_ID)

    youtube.assert_not_called()
    assert result["source"] == "filmot_index"
    assert (result["routes_tried"], result["route_errors"]) == (["filmot-index"], [])


def test_aws_fallback_runs_only_after_both_youtube_and_the_index_fail(youtube, index_client):
    youtube.return_value = dict(YOUTUBE_FAILURE)
    index_client([row()])

    with patch("filmot.aws_transcribe.transcribe_video", side_effect=AssertionError("no AWS")):
        result = transcript_module.get_transcript_with_fallback(VIDEO_ID, source="auto")

    assert result["source"] == "filmot_index"


def test_chunked_transcripts_can_come_from_the_index(youtube, index_client):
    youtube.return_value = dict(YOUTUBE_FAILURE)
    index_client([row()])

    result = transcript_module.get_transcript_with_timestamps(VIDEO_ID, chunk_minutes=1, source="auto")

    assert result["source"] == "filmot_index"
    assert "recorded caption line" in result["chunks"][0]["text"]


@pytest.mark.parametrize("source,providers,index,suffix", [
    ("auto", {"direct": object()}, "fallback", " -> Filmot subtitle index"),
    ("auto", {}, "off", ""),
    ("youtube", {"direct": object()}, "off", ""),
])
def test_route_plans_describe_the_index(source, providers, index, suffix):
    with patch("filmot.index_transcript.configured_providers", return_value=providers):
        plan = transcript_module.routing_plan(source)

    assert (plan["transcript_source"], plan["index"]) == (source, index)
    assert transcript_module.describe_routing_plan(plan).endswith(suffix or "direct")


def test_an_index_only_plan_says_so():
    with patch("filmot.index_transcript.configured_providers", return_value={"direct": object()}):
        plan = transcript_module.routing_plan("filmot")

    assert transcript_module.describe_routing_plan(plan) == "Filmot subtitle index only"
