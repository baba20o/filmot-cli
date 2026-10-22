"""Filmot video metadata on the command surface: video, backfill, library enrich, saves."""

import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

from filmot.api import VIDEO_METADATA_COLUMNS
from filmot.api_providers import ProviderConfigurationError
from filmot.cli import cli
from filmot.commands.search import _backfill_metadata
from filmot.ledger import read_events
from filmot.library import TranscriptLibrary


FIXTURE = Path(__file__).parent / "fixtures" / "filmot_api" / "get_videos_columns.json"
VIDEO_ID = "abc12345678"
OTHER_ID = "def12345678"
BASIC_FIELDS = ("id", "title", "duration", "uploaddate", "channelid", "channelname")

research_module = importlib.import_module("filmot.commands.research")


def row(video_id=VIDEO_ID, **changes):
    value = dict(json.loads(FIXTURE.read_text(encoding="utf-8"))[0], id=video_id)
    value.update(changes)
    return value


def basic_row(video_id=VIDEO_ID, **changes):
    return {key: value for key, value in row(video_id, **changes).items() if key in BASIC_FIELDS}


def transcript(video_id, *_, **__):
    return {
        "video_id": video_id,
        "full_text": "Observations recorded in the field.",
        "segments": [{"text": "Observations recorded.", "start": 0.0, "duration": 3.0}],
        "source": "youtube",
        "route": "direct",
        "routes_tried": ["direct"],
        "language": "en",
        "is_generated": True,
        "duration_seconds": 3.0,
        "segment_count": 1,
    }


def lookup_client(*responses, provider="rapidapi"):
    client = MagicMock()
    client.get_videos.side_effect = list(responses)
    client.last_provider = provider
    return client


class FakeResponse:
    def __init__(self, body):
        self.status_code = 200
        self._body = body
        self.text = json.dumps(body)
        self.headers = requests.structures.CaseInsensitiveDict()

    def json(self):
        return self._body

    def raise_for_status(self):
        return None


@pytest.fixture
def http(monkeypatch):
    """Serve the real client from queued responses, recording each request."""
    calls = []
    queues = {"rapidapi": [], "direct": []}

    def request(self, method, url, params=None, headers=None, **_):
        provider = "direct" if "direct.filmot.invalid" in url else "rapidapi"
        calls.append({"provider": provider, "params": dict(params or {})})
        return queues[provider].pop(0)

    monkeypatch.setattr(requests.sessions.Session, "request", request)
    monkeypatch.setattr("filmot.api.get_rate_limiter", lambda *args, **kwargs: MagicMock())
    return SimpleNamespace(calls=calls, queues=queues)


@pytest.fixture
def lib(tmp_path, monkeypatch):
    library = TranscriptLibrary(data_dir=tmp_path / "data")
    monkeypatch.setattr("filmot.library.get_library", lambda: library)
    return library


@pytest.fixture
def run(stream_runner):
    def invoke(*args, **kwargs):
        return stream_runner.invoke(cli, list(args), env={"COLUMNS": "200"}, **kwargs)

    return invoke


# -------------------------------------------------------------------- video


@pytest.mark.parametrize("policy,provider", [("auto", "rapidapi"), ("direct", "direct")])
def test_video_requests_metadata_columns_on_both_providers(http, run, policy, provider):
    http.queues[provider].append(FakeResponse([row()]))

    result = run("--api-provider", policy, "video", VIDEO_ID, "--raw")

    assert result.exit_code == 0, result.output
    assert http.calls == [{
        "provider": provider,
        "params": {"id": VIDEO_ID, "columns": ",".join(VIDEO_METADATA_COLUMNS)},
    }]
    payload = json.loads(result.stdout)
    assert payload["detail"] == "full"
    assert payload["videos"][0]["manualsubs"] == " de-DE en es-419 ja pt-BR "


def test_basic_video_lookup_skips_columns_and_says_so(http, run):
    http.queues["rapidapi"].append(FakeResponse([basic_row()]))

    result = run("video", VIDEO_ID, "--basic")

    assert result.exit_code == 0, result.output
    assert http.calls[0]["params"] == {"id": VIDEO_ID}
    assert "Basic fields only" in result.stdout
    assert "Auto Captions" not in result.stdout


def test_full_video_display_shows_index_fields_without_markup(http, run):
    http.queues["rapidapi"].append(FakeResponse([row(
        description="Spoken [red]evidence[/red] " + "x" * 500, license="CC", isunlisted=1,
    )]))

    result = run("video", VIDEO_ID)

    output = result.stdout
    assert result.exit_code == 0, result.output
    assert "de-DE, en, es-419, ja, pt-BR" in output
    assert "Creative Commons" in output
    assert "Unlisted" in output
    assert "Filmot Snapshot" in output and "2026-01-02" in output
    assert "[red]evidence[/red]" in output
    assert "…" in output
    assert "Basic fields only" not in output


def test_video_display_marks_unknown_captions_and_never_parses_markup(http, run):
    http.queues["rapidapi"].append(FakeResponse([row(
        autosubs=None, manualsubs=" ", title="[red]Title[/red]",
        channelname="[bold]Channel[/bold]", category="[blink]Music[/blink]",
    )]))

    result = run("video", VIDEO_ID)

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert "unknown" in next(line for line in lines if "Auto Captions" in line)
    assert "none" in next(line for line in lines if "Manual Captions" in line)
    for literal in ("[red]Title[/red]", "[bold]Channel[/bold]", "[blink]Music[/blink]"):
        assert literal in result.stdout


def test_video_rejects_a_drifted_column_type(http, run):
    http.queues["rapidapi"].append(FakeResponse([row(viewcount="many")]))

    result = run("--api-provider", "rapidapi", "video", VIDEO_ID, "--raw")

    assert result.exit_code == 1
    assert "$[0].viewcount" in result.stdout


# ----------------------------------------------------------------- backfill


@pytest.mark.parametrize("response,expected", [
    ([row(OTHER_ID, title="Wrong video"), row()], ("Recorded video title 001", "Recorded channel 001")),
    ({"result": [row()]}, ("Recorded video title 001", "Recorded channel 001")),
    ([{"title": "Bare title", "channelname": "Bare channel"}], ("Bare title", "Bare channel")),
    ([row(OTHER_ID)], ("Unknown", "Unknown")),
    ({"error": "quota_exhausted"}, ("Unknown", "Unknown")),
])
def test_backfill_reads_the_requested_video_from_every_shape(response, expected):
    with patch("filmot.commands.search.FilmotClient") as client:
        client.return_value.get_videos.return_value = response
        assert _backfill_metadata(VIDEO_ID, "Unknown", "Unknown") == expected


def test_backfill_keeps_known_values_and_survives_failures():
    with patch("filmot.commands.search.FilmotClient") as client:
        client.return_value.get_videos.return_value = [row()]
        assert _backfill_metadata(VIDEO_ID, "Known", "") == ("Known", "Recorded channel 001")
        client.return_value.get_videos.side_effect = RuntimeError("offline")
        assert _backfill_metadata(VIDEO_ID, "Unknown", "") == ("Unknown", "")
        client.reset_mock()
        assert _backfill_metadata(VIDEO_ID, "Known", "Known") == ("Known", "Known")
        client.assert_not_called()


# ----------------------------------------------------------- library enrich


@pytest.fixture
def two_records(lib):
    lib.save(VIDEO_ID, "ufo", "First body.", metadata={"title": "Unknown"})
    lib.save(OTHER_ID, "ufo", "Second body.", metadata={"title": "Known", "channel": "Known"})
    return lib


def test_library_enrich_dry_run_previews_without_requests_or_events(two_records, run):
    with (
        patch("filmot.api.FilmotClient", side_effect=AssertionError("no client")),
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = run("library", "enrich", "--dry-run", "--raw")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"] == {
        "scope": "never-enriched", "dry_run": True, "selected_records": 2,
        "selected_videos": 2, "truncated_videos": 0, "unreadable_records": 0,
        "planned_calls": 1,
    }
    assert [item["video_id"] for item in payload["rows"]] == [VIDEO_ID, OTHER_ID]
    log_result.assert_not_called()


def test_library_enrich_fills_records_once_then_finds_nothing_to_do(two_records, run):
    client = lookup_client([row(), row(OTHER_ID)], provider="direct")
    with patch("filmot.api.FilmotClient", return_value=client):
        result = run("library", "enrich", "ufo", "--raw")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["_filmot"]["status"] == "completed"
    assert payload["summary"]["updated"] == 2
    client.get_videos.assert_called_once_with(
        "{},{}".format(VIDEO_ID, OTHER_ID), columns=VIDEO_METADATA_COLUMNS,
    )
    assert two_records.get(VIDEO_ID, "ufo")["metadata"]["title"] == "Recorded video title 001"
    assert two_records.get(OTHER_ID, "ufo")["metadata"]["title"] == "Known"
    assert two_records.get(OTHER_ID, "ufo")["filmot_enrichment"]["api_provider"] == "direct"
    events = [event for event in read_events("ufo") if event["kind"] == "library-enrich"]
    assert events[-1]["data"]["updated"] == 2

    with patch("filmot.api.FilmotClient", side_effect=AssertionError("nothing to look up")):
        again = run("library", "enrich", "ufo")
    assert again.exit_code == 0, again.output
    assert "No saved records need Filmot metadata" in again.stdout


def test_library_enrich_selectors_choose_exact_or_all_records(two_records, run):
    assert run("library", "enrich", "--all", "--video", VIDEO_ID).exit_code == 2

    with patch("filmot.api.FilmotClient", return_value=lookup_client([row()])):
        explicit = run("library", "enrich", "--video", "https://youtu.be/" + VIDEO_ID, "--raw")
    summary = json.loads(explicit.stdout)["summary"]
    assert (summary["scope"], summary["selected_records"], summary["updated"]) == ("explicit", 1, 1)

    with patch("filmot.api.FilmotClient", side_effect=AssertionError("preview only")):
        remaining = run("library", "enrich", "--dry-run", "--raw")
        everything = run("library", "enrich", "--all", "--dry-run", "--raw")
    assert json.loads(remaining.stdout)["summary"]["selected_records"] == 1
    assert json.loads(everything.stdout)["summary"]["selected_records"] == 2


def test_library_enrich_reports_lookup_failures_and_truncation(two_records, run):
    with patch("filmot.api.FilmotClient", return_value=lookup_client(RuntimeError("offline"))):
        failed = run("library", "enrich", "--raw")
    payload = json.loads(failed.stdout)
    assert failed.exit_code == 1
    assert payload["_filmot"]["status"] == "failed"
    assert payload["summary"]["failed"] == 2
    assert payload["_filmot"]["errors"][0]["stage"] == "lookup"
    assert "offline" in payload["_filmot"]["errors"][0]["message"]

    with patch("filmot.api.FilmotClient", return_value=lookup_client([row()])):
        partial = run("library", "enrich", "--max-videos", "1", "--raw")
    payload = json.loads(partial.stdout)
    assert payload["_filmot"]["status"] == "partial"
    assert payload["summary"]["truncated_videos"] == 1
    assert "truncated" in payload["_filmot"]["warnings"][0]

    with patch("filmot.api.FilmotClient", return_value=lookup_client([])):
        missing = run("library", "enrich", "--raw")
    payload = json.loads(missing.stdout)
    assert payload["_filmot"]["status"] == "completed"
    assert payload["summary"]["not_returned"] == 1


def test_library_enrich_renders_a_mixed_batch_failure(two_records, run):
    client = lookup_client([row()], RuntimeError("offline"))

    with (
        patch("filmot.video_metadata.VIDEO_BATCH_LIMIT", 1),
        patch("filmot.api.FilmotClient", return_value=client),
    ):
        result = run("library", "enrich")

    assert result.exit_code == 0, result.output
    assert "Filmot metadata: 1 updated, 1 failed, 1 lookup error" in result.stdout
    assert "Fail ufo/{} - RuntimeError: offline".format(OTHER_ID) in result.stdout


def test_library_enrich_dry_run_logs_nothing_when_inventory_fails(two_records, run, monkeypatch):
    monkeypatch.setattr(
        two_records, "filmot_enrichment_inventory",
        MagicMock(side_effect=PermissionError("transcripts directory denied")),
    )

    with patch("filmot.ledger.log_result") as log_result:
        result = run("library", "enrich", "--dry-run", "--raw")

    assert result.exit_code == 1
    assert json.loads(result.stdout)["_filmot"]["errors"][0]["stage"] == "inventory"
    log_result.assert_not_called()


def test_library_enrich_without_credentials_fails_before_writing(two_records, run):
    with patch(
        "filmot.api.FilmotClient",
        side_effect=ProviderConfigurationError("No Filmot API credentials are configured."),
    ):
        result = run("library", "enrich")

    assert result.exit_code == 1
    assert "No Filmot API credentials" in result.stderr
    assert "filmot_enrichment" not in two_records.get(VIDEO_ID, "ufo")


def test_library_enrich_skips_unreadable_records_visibly(two_records, run):
    (two_records.transcripts_dir / "ufo" / "zzz12345678.json").write_text("{", encoding="utf-8")

    with patch("filmot.api.FilmotClient", side_effect=AssertionError("preview only")):
        result = run("library", "enrich", "--dry-run")

    assert result.exit_code == 0, result.output
    assert "1 unreadable record(s) were skipped." in result.stdout
    assert "Would look up 2 record(s)" in result.stdout


# --------------------------------------------------------------- save paths


def test_transcript_save_enriches_the_new_record(lib, run, monkeypatch):
    monkeypatch.setenv("FILMOT_ENRICH_ON_SAVE", "1")
    client = MagicMock(last_provider="rapidapi")
    client.get_videos.side_effect = (
        lambda video_ids, flags=None, columns=None, lang=None:
        [row()] if columns else [basic_row(title="Basic title")]
    )

    with (
        patch("filmot.transcript.get_transcript", side_effect=transcript),
        patch("filmot.commands.transcript.FilmotClient", return_value=client),
    ):
        result = run("transcript", VIDEO_ID, "--save-to", "ufo", "--raw")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["_filmot"]["warnings"] == []
    metadata = lib.get(VIDEO_ID, "ufo")["metadata"]
    assert metadata["title"] == "Basic title"
    assert metadata["description"] == "Recorded video description"
    assert metadata["views_observed_at"] == "2026-01-02"
    assert [call.kwargs.get("columns") for call in client.get_videos.call_args_list] == [
        None, VIDEO_METADATA_COLUMNS,
    ]


def test_transcript_save_reports_a_failed_lookup_without_failing(lib, run, monkeypatch):
    monkeypatch.setenv("FILMOT_ENRICH_ON_SAVE", "1")
    client = lookup_client([basic_row()], RuntimeError("offline"))

    with (
        patch("filmot.transcript.get_transcript", side_effect=transcript),
        patch("filmot.commands.transcript.FilmotClient", return_value=client),
    ):
        raw = run("transcript", VIDEO_ID, "--save-to", "ufo", "--raw")

    assert raw.exit_code == 0, raw.output
    assert json.loads(raw.stdout)["_filmot"]["warnings"] == [
        "Filmot metadata enrichment failed: RuntimeError: offline",
    ]
    assert lib.exists(VIDEO_ID, "ufo")


def test_transcript_save_prints_the_enrichment_summary(lib, run, monkeypatch):
    monkeypatch.setenv("FILMOT_ENRICH_ON_SAVE", "1")
    client = lookup_client([basic_row()], [row()])

    with (
        patch("filmot.transcript.get_transcript", side_effect=transcript),
        patch("filmot.commands.transcript.FilmotClient", return_value=client),
    ):
        result = run("transcript", VIDEO_ID, "--save-to", "ufo")

    assert result.exit_code == 0, result.output
    assert "Filmot metadata: 1 updated" in result.stdout


def test_saves_make_no_lookup_when_enrichment_is_off(lib, run):
    client = lookup_client([basic_row()])

    with (
        patch("filmot.transcript.get_transcript", side_effect=transcript),
        patch("filmot.commands.transcript.FilmotClient", return_value=client),
    ):
        result = run("transcript", VIDEO_ID, "--save-to", "ufo", "--raw")

    assert result.exit_code == 0, result.output
    client.get_videos.assert_called_once_with(VIDEO_ID)
    assert "filmot_enrichment" not in lib.get(VIDEO_ID, "ufo")


def test_download_enriches_in_one_batch_after_youtube_registration(lib, run, monkeypatch):
    monkeypatch.setenv("FILMOT_ENRICH_ON_SAVE", "1")
    videos = [
        {
            "video_id": video_id, "title": "YouTube title", "description": "From YouTube",
            "channel_title": "YouTube channel", "channel_id": "UC-youtube",
            "published_at": "2026-09-12T09:00:00Z", "views": 10,
            "metadata_observed_at": "2026-09-12T10:00:00Z",
            "metadata_expires_at": "2026-10-12T10:00:00Z",
        }
        for video_id in (VIDEO_ID, OTHER_ID)
    ]
    piped = json.dumps({
        "videos": videos,
        "_filmot": {"schema": "filmot.result/v1", "command": "yt-search", "status": "completed"},
    })
    client = lookup_client([row(), row(OTHER_ID)])

    with (
        patch("filmot.transcript.get_transcript", side_effect=transcript),
        patch("filmot.commands.search.FilmotClient", return_value=client),
    ):
        result = run("download", "--topic", "fresh", "--count", "2", input=piped)

    assert result.exit_code == 0, result.output
    client.get_videos.assert_called_once_with(
        "{},{}".format(VIDEO_ID, OTHER_ID), columns=VIDEO_METADATA_COLUMNS,
    )
    assert "Filmot metadata: 2 updated" in result.stdout
    record = lib.get(VIDEO_ID, "fresh")
    assert record["metadata"]["description"] == "From YouTube"
    assert record["metadata"]["views"] == 10
    assert record["metadata"]["category"] == "Music"
    assert record["metadata_lifecycle"]["youtube"]["state"] == "current"
    assert "views" not in record["filmot_enrichment"]["filled_fields"]


def test_research_enriches_every_saved_source_in_one_batch(lib, stream_runner, monkeypatch):
    monkeypatch.setenv("FILMOT_ENRICH_ON_SAVE", "1")
    client = Mock(last_provider="rapidapi")
    client.search_subtitles_all.return_value = {
        "result": [
            {"id": video_id, "title": "Alpha beta field study", "channelname": "Research Channel",
             "duration": 600, "viewcount": 1000,
             "hits": [{"token": "alpha beta"}, {"token": "alpha beta"}]}
            for video_id in ("source00001", "source00002")
        ],
        "totalresultcount": 2,
    }
    client.get_videos.return_value = [row("source00001"), row("source00002")]
    monkeypatch.setattr(research_module, "FilmotClient", lambda: client)
    monkeypatch.setattr(
        research_module, "_backfill_metadata", lambda video_id, title, channel: (title, channel),
    )
    monkeypatch.setattr("filmot.transcript.get_transcript", transcript)

    result = stream_runner.invoke(
        cli, ["research", "alpha beta", "--depth", "2", "--raw", "--no-scout"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["filmot_metadata"] == {
        "records": 2, "updated": 2, "unchanged": 0, "not_returned": 0,
        "failed": 0, "lookup_errors": 0,
    }
    client.get_videos.assert_called_once_with(
        "source00001,source00002", columns=VIDEO_METADATA_COLUMNS,
    )
    assert lib.get("source00001", "alpha-beta")["metadata"]["description"] == (
        "Recorded video description"
    )
    assert any(event["kind"] == "filmot_enrichment" for event in read_events("alpha-beta"))
