"""Ctrl-C stops a parallel channel download promptly, and a stopped fetch
starts no further transcript route."""

import copy
import threading
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

import filmot.transcript as transcript_module
from filmot.cli import cli
from filmot.transcript import TranscriptRouteTimeout, get_transcript


def test_a_set_stop_event_means_no_route_starts(monkeypatch):
    def no_routes(*args, **kwargs):
        raise AssertionError("a route started after stop was set")

    monkeypatch.setattr(transcript_module, "_iter_routes", no_routes)
    stop = threading.Event()
    stop.set()

    result = get_transcript("dQw4w9WgXcQ", stop=stop, source="auto")

    assert result["error_type"] == "Cancelled"
    assert result["routes_tried"] == []


def _no_index_fallback(monkeypatch):
    # The Filmot index would spend credits; a stopped fetch must not try it.
    monkeypatch.setattr("filmot.index_transcript.fetch_index_transcript", MagicMock(
        side_effect=AssertionError("the index fallback ran after stop was set")
    ))
    monkeypatch.setattr("filmot.index_transcript.index_available", lambda: True)


def _times_out(route_config, *, route, **kwargs):
    raise TranscriptRouteTimeout(route, 1.0)


def test_stopping_mid_fetch_requests_no_further_route(monkeypatch):
    stop = threading.Event()
    produced = []

    def routes(*args, **kwargs):
        produced.append("first")
        yield "pool:first", {"kind": "direct"}, None
        produced.append("second")  # the real iterator leases a session here
        yield "pool:second", {"kind": "direct"}, None

    def first_route_times_out(route_config, *, route, **kwargs):
        stop.set()  # Ctrl-C arrives while the first route runs
        raise TranscriptRouteTimeout(route, 1.0)

    monkeypatch.setattr(transcript_module, "_iter_routes", routes)
    monkeypatch.setattr(transcript_module, "_call_with_route_timeout", first_route_times_out)
    _no_index_fallback(monkeypatch)

    result = get_transcript("dQw4w9WgXcQ", stop=stop, source="auto")

    assert result["error_type"] == "Cancelled"
    assert result["routes_tried"] == ["pool:first"]
    assert produced == ["first"]


def test_a_route_leased_as_the_stop_arrives_is_handed_back_unused(monkeypatch):
    stop = threading.Event()
    outcomes = []

    def routes(*args, **kwargs):
        yield "pool:first", {"kind": "direct"}, lambda *outcome: outcomes.append(("first",) + outcome)
        stop.set()  # Ctrl-C lands while the next lease is prepared
        yield "pool:second", {"kind": "direct"}, lambda *outcome: outcomes.append(("second",) + outcome)

    monkeypatch.setattr(transcript_module, "_iter_routes", routes)
    monkeypatch.setattr(transcript_module, "_call_with_route_timeout", _times_out)
    _no_index_fallback(monkeypatch)

    result = get_transcript("dQw4w9WgXcQ", stop=stop, source="auto")

    assert result["error_type"] == "Cancelled"
    assert result["routes_tried"] == ["pool:first"]
    assert ("second", "release", None) in outcomes


def _channel(tmp_path, count):
    downloader = MagicMock()
    downloader._resolve_channel_dir.return_value = ("example", tmp_path / "example")
    downloader._load_manifest.return_value = {"videos": {}}
    saved, manifests = {}, []
    downloader._save_transcript.side_effect = lambda path, video, data: saved.__setitem__(video, data)
    downloader._save_manifest.side_effect = lambda path, manifest: manifests.append(copy.deepcopy(manifest))
    info = {"name": "Example", "video_count": count, "subscriber_count": 12, "uploads_playlist_id": "uploads"}
    videos = [
        {"video_id": "video{:06d}".format(index), "title": "Video [/{}]".format(index), "published_at": "2026-01-01"}
        for index in range(count)
    ]
    enumeration = {
        "provider": "youtube-data-api-v3",
        "videos": videos,
        "request": {"uploads_playlist_id": "uploads", "max_pages": 10, "max_items": 500, "page_token": None},
        "coverage": {
            "pages_attempted": 1, "pages_fetched": 1, "api_calls": 1, "returned": count,
            "next_page_token": None, "stopping_reason": "exhausted", "partial": False,
        },
        "observed_at": "2026-09-12T12:00:00Z",
        "expires_at": "2026-10-12T12:00:00Z",
        "warnings": [],
        "errors": [],
    }
    return downloader, info, enumeration, saved, manifests


def test_ctrl_c_cancels_queued_downloads_and_keeps_finished_work(tmp_path):
    downloader, info, enumeration, saved, manifests = _channel(tmp_path, 6)
    started = []
    both_active = threading.Event()

    def fetch(video_id, languages=None, progress_callback=None, fresh_primary=False, stop=None):
        started.append(video_id)
        if len(started) == 2:
            both_active.set()
        # A running fetch ends once told to stop, as a real one does when
        # Ctrl-C also stops its worker process: one finishes, one fails.
        assert stop.wait(10)
        if video_id == "video000000":
            return {"language": "en", "full_text": "hello there", "segments": [], "source": "youtube"}
        return {"error": "worker stopped", "error_type": "TranscriptWorkerError"}

    def interrupted_while_waiting(futures, timeout=None):
        assert both_active.wait(10)
        raise KeyboardInterrupt

    with patch("filmot.channel_dl.ChannelDownloader", return_value=downloader), patch(
        "filmot.channel_dl.get_channel_info", return_value=info
    ), patch(
        "filmot.channel_dl.enumerate_uploads_detailed", return_value=enumeration
    ), patch("filmot.transcript.get_transcript", side_effect=fetch), patch(
        "concurrent.futures.as_completed", interrupted_while_waiting
    ):
        result = CliRunner().invoke(cli, ["channel-download", "UC-example", "--workers", "2", "--delay", "0"])

    assert result.exit_code == 1, result.output
    assert "Interrupted!" in result.output
    assert sorted(started) == ["video000000", "video000001"]  # queued ones never ran
    assert list(saved) == ["video000000"]
    statuses = {video: entry["status"] for video, entry in manifests[-1]["videos"].items()}
    assert statuses.pop("video000000") == "done"
    # The interrupted failure is not recorded; it stays pending with the rest.
    assert set(statuses.values()) == {"pending"}
