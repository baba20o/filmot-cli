"""Typed result contracts for transcript-family CLI commands."""

import json
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


def _upload_enumeration(
    videos,
    *,
    max_pages=10,
    max_items=500,
    page_token=None,
    next_page_token=None,
    partial=False,
    warnings=None,
    errors=None,
    stopping_reason=None,
):
    """Return the bounded provider envelope expected by channel-download."""
    return {
        "provider": "youtube-data-api-v3",
        "videos": videos,
        "request": {
            "uploads_playlist_id": "uploads",
            "max_pages": max_pages,
            "max_items": max_items,
            "page_token": page_token,
        },
        "coverage": {
            "pages_attempted": 1,
            "pages_fetched": 1,
            "api_calls": 1,
            "returned": len(videos),
            "next_page_token": next_page_token,
            "stopping_reason": stopping_reason or (
                "page_budget" if next_page_token else "exhausted"
            ),
            "partial": partial,
        },
        "observed_at": "2026-09-12T12:00:00Z",
        "expires_at": "2026-10-12T12:00:00Z",
        "warnings": list(warnings or []),
        "errors": list(errors or []),
    }


def _save_grep_record(
    library,
    *,
    topic,
    video_id="abc12345678",
    language="en",
    segments=None,
):
    segments = segments or [{
        "start": 9.0,
        "duration": 3.0,
        "text": "alpha evidence",
    }]
    library.save(
        video_id=video_id,
        topic=topic,
        transcript_text=" ".join(item["text"] for item in segments),
        metadata={"language": language, "duration_seconds": 75.0},
        segments=segments,
    )


def _fetched_grep_transcript(video_id="abc12345678", language="en"):
    return {
        "video_id": video_id,
        "language": language,
        "full_text": "alpha evidence",
        "segments": [{
            "start": 9.0,
            "duration": 3.0,
            "text": "alpha evidence",
        }],
        "duration_seconds": 12.0,
        "segment_count": 1,
        "route": "direct",
        "routes_tried": ["direct"],
    }


def test_transcript_grep_local_hit_links_and_routes_to_source_topic(runner):
    from filmot.library import get_library

    video_id = "abc12345678"
    _save_grep_record(
        get_library(),
        topic="active-inference",
        video_id=video_id,
        segments=[
            {"start": 0.0, "duration": 5.0, "text": "opening context"},
            {
                "start": 65.0,
                "duration": 10.0,
                "text": "expected free energy selects policies",
            },
        ],
    )
    with (
        patch("filmot.transcript.get_transcript") as fetch,
        patch("filmot.transcript.routing_plan") as routing_plan,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = runner.invoke(
            cli,
            ["transcript", video_id, "--grep", "expected free energy"],
        )

    assert result.exit_code == 0, result.output
    assert "Transcript source: library/active-inference" in result.output
    assert "[1:05]" in result.output
    assert f"https://youtube.com/watch?v={video_id}&t=65s" in result.output
    fetch.assert_not_called()
    routing_plan.assert_not_called()
    log_result.assert_called_once()
    outcome = log_result.call_args.args[1]
    assert outcome.status_value == ResultStatus.COMPLETED.value
    assert log_result.call_args.kwargs["topic"] == "active-inference"
    assert log_result.call_args.kwargs["data"]["library_topic"] == (
        "active-inference"
    )


def test_transcript_grep_filters_language_before_ambiguity(runner):
    from filmot.library import get_library

    library = get_library()
    _save_grep_record(library, topic="english", language="en")
    _save_grep_record(
        library,
        topic="french",
        language="fr",
        segments=[{
            "start": 11.0,
            "duration": 3.0,
            "text": "alpha preuve",
        }],
    )
    with (
        patch("filmot.transcript.get_transcript") as fetch,
        patch("filmot.transcript.routing_plan") as routing_plan,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = runner.invoke(
            cli,
            ["transcript", "abc12345678", "--lang", "fr", "--grep", "preuve"],
        )

    assert result.exit_code == 0, result.output
    assert "library/french" in result.output
    fetch.assert_not_called()
    routing_plan.assert_not_called()
    assert log_result.call_args.kwargs["topic"] == "french"


def test_transcript_grep_deduplicates_equivalent_saved_copies(runner):
    from filmot.library import get_library

    library = get_library()
    _save_grep_record(library, topic="topic-one")
    _save_grep_record(library, topic="topic-two")
    with (
        patch("filmot.transcript.get_transcript") as fetch,
        patch("filmot.transcript.routing_plan") as routing_plan,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = runner.invoke(
            cli,
            ["transcript", "abc12345678", "--grep", "alpha"],
        )

    assert result.exit_code == 0, result.output
    assert "Transcript source: library (no external fetch)" in result.output
    fetch.assert_not_called()
    routing_plan.assert_not_called()
    outcome = log_result.call_args.args[1]
    assert outcome.data["library_copy_count"] == 2
    assert "library_topic" not in outcome.data
    assert log_result.call_args.kwargs["topic"] is None


def test_transcript_grep_distinct_saved_records_explain_external_fallback(runner):
    from filmot.library import get_library

    library = get_library()
    _save_grep_record(library, topic="topic-one")
    _save_grep_record(
        library,
        topic="topic-two",
        segments=[{
            "start": 12.0,
            "duration": 3.0,
            "text": "alpha alternative",
        }],
    )
    fetched = _fetched_grep_transcript()
    with (
        patch("filmot.transcript.get_transcript", return_value=fetched) as fetch,
        patch("filmot.transcript.routing_plan", return_value=_route_plan()),
        patch("filmot.transcript.describe_routing_plan", return_value="direct"),
        patch("filmot.ledger.log_result"),
    ):
        result = runner.invoke(
            cli,
            ["transcript", "abc12345678", "--grep", "alpha"],
        )

    assert result.exit_code == 0, result.output
    assert "multiple distinct usable transcripts" in result.output
    fetch.assert_called_once()


def test_transcript_grep_rejects_nonmonotonic_local_segment_times(runner):
    from filmot.library import get_library

    _save_grep_record(
        get_library(),
        topic="broken-times",
        segments=[
            {"start": 10.0, "duration": 2.0, "text": "alpha"},
            {"start": 5.0, "duration": 2.0, "text": "evidence"},
        ],
    )
    fetched = _fetched_grep_transcript()
    with (
        patch("filmot.transcript.get_transcript", return_value=fetched) as fetch,
        patch("filmot.transcript.routing_plan", return_value=_route_plan()),
        patch("filmot.transcript.describe_routing_plan", return_value="direct"),
        patch("filmot.ledger.log_result"),
    ):
        result = runner.invoke(
            cli,
            ["transcript", "abc12345678", "--grep", "alpha"],
        )

    assert result.exit_code == 0, result.output
    assert "lacks trustworthy timestamp segments" in result.output
    fetch.assert_called_once()


def test_transcript_grep_filters_invalid_copy_before_ambiguity(runner):
    from filmot.library import get_library

    library = get_library()
    _save_grep_record(library, topic="valid-times")
    _save_grep_record(
        library,
        topic="broken-times",
        segments=[
            {"start": 10.0, "duration": 2.0, "text": "alpha"},
            {"start": 5.0, "duration": 2.0, "text": "evidence"},
        ],
    )
    with (
        patch("filmot.transcript.get_transcript") as fetch,
        patch("filmot.transcript.routing_plan") as routing_plan,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = runner.invoke(
            cli,
            ["transcript", "abc12345678", "--grep", "alpha"],
        )

    assert result.exit_code == 0, result.output
    assert "library/valid-times" in result.output
    fetch.assert_not_called()
    routing_plan.assert_not_called()
    assert log_result.call_args.kwargs["topic"] == "valid-times"


def test_transcript_grep_raw_cache_fallback_reason_stays_on_stderr(runner):
    from filmot.library import get_library

    _save_grep_record(
        get_library(),
        topic="broken-times",
        segments=[
            {"start": 10.0, "duration": 2.0, "text": "alpha"},
            {"start": 5.0, "duration": 2.0, "text": "evidence"},
        ],
    )
    fetched = _fetched_grep_transcript()
    with (
        patch("filmot.transcript.get_transcript", return_value=fetched),
        patch("filmot.transcript.routing_plan", return_value=_route_plan()),
        patch("filmot.ledger.log_result"),
    ):
        result = runner.invoke(
            cli,
            ["transcript", "abc12345678", "--grep", "alpha", "--raw"],
        )

    assert result.exit_code == 0, result.output
    assert "lacks trustworthy timestamp segments" in result.stderr
    payload = json.loads(result.stdout)
    assert payload["match_count"] == 1
    assert "trustworthy timestamp" not in result.stdout


def test_transcript_grep_raw_has_stable_match_rows(runner):
    from filmot.library import get_library

    _save_grep_record(get_library(), topic="raw-topic")
    with (
        patch("filmot.transcript.get_transcript") as fetch,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = runner.invoke(
            cli,
            ["transcript", "abc12345678", "--grep", "alpha", "--raw"],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["query"] == "alpha"
    assert payload["match_count"] == 1
    assert payload["matches"] == [{
        "seconds": 9.0,
        "timestamp": "0:09",
        "deep_link": "https://youtube.com/watch?v=abc12345678&t=9s",
        "excerpt": "alpha evidence",
    }]
    assert payload["_filmot"]["status"] == "completed"
    fetch.assert_not_called()
    assert log_result.call_args.args[1].to_raw_dict() == payload


def test_transcript_grep_raw_no_match_is_typed_empty(runner):
    from filmot.library import get_library

    _save_grep_record(get_library(), topic="raw-topic")
    with patch("filmot.ledger.log_result") as log_result:
        result = runner.invoke(
            cli,
            ["transcript", "abc12345678", "--grep", "omega", "--raw"],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["query"] == "omega"
    assert payload["match_count"] == 0
    assert payload["matches"] == []
    assert payload["_filmot"]["status"] == "empty"
    assert log_result.call_args.args[1].status_value == ResultStatus.EMPTY.value


@pytest.mark.parametrize("raw", [False, True])
def test_transcript_grep_malformed_query_logs_actual_failure(runner, raw):
    arguments = [
        "transcript",
        "abc12345678",
        "--grep",
        '\"alpha\" NEAR/5 beta',
    ]
    if raw:
        arguments.append("--raw")
    with (
        patch("filmot.ledger.log_result") as log_result,
        patch("filmot.transcript.get_transcript") as fetch,
        patch(
            "filmot.commands.transcript._saved_transcript_for_grep"
        ) as local_lookup,
    ):
        result = runner.invoke(cli, arguments)

    assert result.exit_code == (1 if raw else 2), result.output
    log_result.assert_called_once()
    logged = log_result.call_args.args[1]
    assert logged.status_value == ResultStatus.FAILED.value
    assert logged.errors[0].type == "InvalidGrepQuery"
    assert logged.errors[0].stage == "parse-query"
    assert log_result.call_args.kwargs["topic"] is None
    fetch.assert_not_called()
    local_lookup.assert_not_called()
    if raw:
        payload = json.loads(result.stdout)
        assert payload["query"] == '\"alpha\" NEAR/5 beta'
        assert payload["matches"] == []
        assert payload["_filmot"]["errors"][0]["type"] == "InvalidGrepQuery"
        assert logged.to_raw_dict() == payload
    else:
        assert "could not be parsed" in result.output
        assert "NEAR/N" in result.output
        assert '"a b"~N' in result.output


@pytest.mark.parametrize("raw", [False, True])
def test_transcript_blank_grep_is_rejected_before_lookup_or_fetch(runner, raw):
    arguments = ["transcript", "abc12345678", "--grep", ""]
    if raw:
        arguments.append("--raw")
    with (
        patch("filmot.ledger.log_result") as log_result,
        patch("filmot.transcript.get_transcript") as fetch,
        patch(
            "filmot.commands.transcript._saved_transcript_for_grep"
        ) as local_lookup,
    ):
        result = runner.invoke(cli, arguments)

    assert result.exit_code == (1 if raw else 2), result.output
    logged = log_result.call_args.args[1]
    assert logged.status_value == ResultStatus.FAILED.value
    assert logged.errors[0].type == "InvalidGrepQuery"
    assert logged.errors[0].stage == "parse-query"
    fetch.assert_not_called()
    local_lookup.assert_not_called()
    if raw:
        payload = json.loads(result.stdout)
        assert payload["query"] == ""
        assert payload["matches"] == []
        assert logged.to_raw_dict() == payload
    else:
        assert "must contain non-whitespace text" in result.output


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
        patch(
            "filmot.channel_dl.enumerate_uploads_detailed",
            return_value=_upload_enumeration([]),
        ),
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


def test_channel_download_renders_unknown_counts_and_keeps_resolution_provenance(
    runner,
    tmp_path,
):
    requested_channel = "@Requested.Handle"
    canonical_channel_id = "UC1234567890123456789012"
    downloader = MagicMock()
    channel_dir = Path(tmp_path) / "example"
    downloader._resolve_channel_dir.return_value = ("example", channel_dir)
    downloader._load_manifest.return_value = {"videos": {}}
    info = {
        "channel_id": canonical_channel_id,
        "name": "Example",
        "video_count": None,
        "subscriber_count": None,
        "uploads_playlist_id": "uploads",
    }
    with (
        patch(
            "filmot.channel_dl.ChannelDownloader",
            return_value=downloader,
        ),
        patch("filmot.channel_dl.get_channel_info", return_value=info),
        patch(
            "filmot.channel_dl.enumerate_uploads_detailed",
            return_value=_upload_enumeration([]),
        ),
        patch("filmot.ledger.log_event") as mock_log_event,
        patch("filmot.ledger.log_result") as mock_log_result,
        patch(
            "filmot.commands.transcript._render_channel_download"
        ) as mock_renderer,
    ):
        result = runner.invoke(cli, ["channel-download", requested_channel])

    assert result.exit_code == 0, result.output
    assert f"Channel ID: {canonical_channel_id}" in result.output
    assert f"Requested as: {requested_channel}" in result.output
    assert "Videos: unknown" in result.output
    assert "Subscribers: unknown" in result.output

    outcome = _shared_outcome(mock_log_result, mock_renderer)
    assert outcome.data["channel_id"] == canonical_channel_id
    assert outcome.data["requested_channel"] == requested_channel
    assert mock_log_result.call_args.kwargs["data"]["channel_id"] == (
        canonical_channel_id
    )
    assert mock_log_result.call_args.kwargs["data"]["requested_channel"] == (
        requested_channel
    )

    start = next(
        call for call in mock_log_event.call_args_list
        if call.args[0] == "channel_download_start"
    )
    assert start.kwargs["channel_id"] == requested_channel
    assert start.kwargs["requested_channel"] == requested_channel

    saved_manifest = downloader._save_manifest.call_args.args[1]
    assert saved_manifest["channel"]["channel_id"] == canonical_channel_id
    assert saved_manifest["requested_channel"] == requested_channel


def test_channel_download_persists_canonical_identity_for_handle_input(
    runner,
    tmp_path,
):
    requested_channel = "@Requested.Handle"
    canonical_channel_id = "UC1234567890123456789012"
    channel_dir = Path(tmp_path) / "example"
    downloader = MagicMock()
    downloader._resolve_channel_dir.return_value = ("example", channel_dir)
    downloader._load_manifest.return_value = {"videos": {}}
    info = {
        "channel_id": canonical_channel_id,
        "name": "Example",
        "video_count": 1,
        "subscriber_count": 12,
        "uploads_playlist_id": "uploads",
    }
    videos = [{
        "video_id": "abc12345678",
        "title": "Video",
        "published_at": "2026-01-01T00:00:00Z",
    }]
    transcript = {
        "language": "en",
        "is_generated": False,
        "duration_seconds": 5.0,
        "segment_count": 1,
        "full_text": "two words",
        "segments": [{"start": 0.0, "duration": 5.0, "text": "two words"}],
        "route": "direct",
    }
    with (
        patch(
            "filmot.channel_dl.ChannelDownloader",
            return_value=downloader,
        ),
        patch("filmot.channel_dl.get_channel_info", return_value=info),
        patch(
            "filmot.channel_dl.enumerate_uploads_detailed",
            return_value=_upload_enumeration(videos),
        ),
        patch("filmot.transcript.get_transcript", return_value=transcript),
        patch("filmot.transcript.routing_plan", return_value=_route_plan()),
        patch("filmot.transcript.describe_routing_plan", return_value="direct"),
        patch("filmot.ledger.log_event") as mock_log_event,
        patch("filmot.ledger.log_result") as mock_log_result,
        patch(
            "filmot.commands.transcript._render_channel_download"
        ) as mock_renderer,
    ):
        result = runner.invoke(
            cli,
            ["channel-download", requested_channel, "--delay", "0"],
        )

    assert result.exit_code == 0, result.output
    outcome = _shared_outcome(mock_log_result, mock_renderer)
    assert outcome.status_value == ResultStatus.COMPLETED.value
    assert outcome.data["channel_id"] == canonical_channel_id
    assert outcome.data["requested_channel"] == requested_channel

    transcript_payload = downloader._save_transcript.call_args.args[2]
    assert transcript_payload["channel_id"] == canonical_channel_id
    assert transcript_payload["requested_channel"] == requested_channel

    saved_manifest = downloader._save_manifest.call_args.args[1]
    assert saved_manifest["channel"]["channel_id"] == canonical_channel_id
    assert saved_manifest["requested_channel"] == requested_channel

    item_event = next(
        call for call in mock_log_event.call_args_list
        if call.args[0] == "channel_download_item"
        and call.kwargs["status"] == "saved"
    )
    assert item_event.kwargs["channel_id"] == canonical_channel_id
    assert item_event.kwargs["requested_channel"] == requested_channel
    assert mock_log_result.call_args.kwargs["data"]["channel_id"] == (
        canonical_channel_id
    )
    assert mock_log_result.call_args.kwargs["data"]["requested_channel"] == (
        requested_channel
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
        "video_id": "def12345678",
        "title": "Video",
        "published_at": "2026-01-01T00:00:00",
    }]
    with (
        patch(
            "filmot.channel_dl.ChannelDownloader",
            return_value=downloader,
        ),
        patch("filmot.channel_dl.get_channel_info", return_value=info),
        patch(
            "filmot.channel_dl.enumerate_uploads_detailed",
            return_value=_upload_enumeration(videos),
        ),
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


def test_channel_download_uses_explicit_bounds_and_persists_continuation(
    runner,
    tmp_path,
):
    downloader = MagicMock()
    channel_dir = Path(tmp_path) / "example"
    downloader._resolve_channel_dir.return_value = ("example", channel_dir)
    downloader._load_manifest.return_value = {"videos": {}}
    info = {
        "channel_id": "UC1234567890123456789012",
        "name": "Example",
        "video_count": 900,
        "subscriber_count": 12,
        "uploads_playlist_id": "uploads",
    }
    provider = _upload_enumeration(
        [],
        max_pages=2,
        max_items=75,
        page_token="START",
        next_page_token="NEXT",
    )
    with (
        patch("filmot.channel_dl.ChannelDownloader", return_value=downloader),
        patch("filmot.channel_dl.get_channel_info", return_value=info),
        patch(
            "filmot.channel_dl.enumerate_uploads_detailed",
            return_value=provider,
        ) as enumerate_uploads,
        patch("filmot.ledger.log_event"),
        patch("filmot.ledger.log_result") as mock_log_result,
        patch(
            "filmot.commands.transcript._render_channel_download"
        ) as mock_renderer,
    ):
        result = runner.invoke(cli, [
            "channel-download",
            "@Example",
            "--pages",
            "2",
            "--max-results",
            "75",
            "--page-token",
            "START",
            "--limit",
            "1",
        ])

    assert result.exit_code == 0, result.output
    call = enumerate_uploads.call_args
    assert call.args == ("uploads",)
    assert call.kwargs["max_pages"] == 2
    assert call.kwargs["max_items"] == 75
    assert call.kwargs["page_token"] == "START"
    assert callable(call.kwargs["progress_callback"])

    outcome = _shared_outcome(mock_log_result, mock_renderer)
    assert outcome.status_value == ResultStatus.COMPLETED.value
    assert outcome.data["already_synced"] is False
    assert outcome.data["continuation"] == {
        "available": True,
        "next_page_token": "NEXT",
        "argv": [
            "filmot",
            "channel-download",
            "@Example",
            "--page-token",
            "NEXT",
            "--pages",
            "2",
            "--max-results",
            "75",
        ],
    }
    checkpoint = outcome.data["enumeration"]
    assert checkpoint["schema"] == "filmot.youtube-upload-enumeration/v1"
    assert checkpoint["observed_at"] == "2026-09-12T12:00:00Z"
    assert checkpoint["expires_at"] == "2026-10-12T12:00:00Z"
    assert checkpoint["coverage"]["next_page_token"] == "NEXT"
    saved_manifest = downloader._save_manifest.call_args.args[1]
    assert saved_manifest["upload_enumeration"] == checkpoint
    compact = mock_log_result.call_args.kwargs["data"]
    assert compact["enumeration"] == checkpoint
    assert compact["continuation"] == outcome.data["continuation"]


def test_channel_download_preserves_downloads_but_is_partial_on_provider_issue(
    runner,
    tmp_path,
):
    downloader = MagicMock()
    channel_dir = Path(tmp_path) / "example"
    downloader._resolve_channel_dir.return_value = ("example", channel_dir)
    downloader._load_manifest.return_value = {"videos": {}}
    info = {
        "channel_id": "UC1234567890123456789012",
        "name": "Example",
        "video_count": 2,
        "subscriber_count": 12,
        "uploads_playlist_id": "uploads",
    }
    videos = [{
        "video_id": "abc12345678",
        "title": "Preserved upload",
        "published_at": "2026-09-12T00:00:00Z",
    }]
    provider = _upload_enumeration(
        videos,
        next_page_token="FAILED_PAGE",
        partial=True,
        stopping_reason="partial_failure",
        warnings=["Earlier upload pages were preserved."],
        errors=[{
            "type": "YouTubeChannelAPIError",
            "message": "YouTube Data API playlistItems.list failed",
            "stage": "enumeration",
            "page": 2,
            "reason": "quotaExceeded",
        }],
    )
    transcript = {
        "language": "en",
        "is_generated": False,
        "duration_seconds": 5.0,
        "segment_count": 1,
        "full_text": "two words",
        "segments": [{"start": 0.0, "duration": 5.0, "text": "two words"}],
        "route": "direct",
    }
    with (
        patch("filmot.channel_dl.ChannelDownloader", return_value=downloader),
        patch("filmot.channel_dl.get_channel_info", return_value=info),
        patch(
            "filmot.channel_dl.enumerate_uploads_detailed",
            return_value=provider,
        ),
        patch("filmot.transcript.get_transcript", return_value=transcript),
        patch("filmot.transcript.routing_plan", return_value=_route_plan()),
        patch("filmot.transcript.describe_routing_plan", return_value="direct"),
        patch("filmot.ledger.log_event"),
        patch("filmot.ledger.log_result") as mock_log_result,
    ):
        result = runner.invoke(
            cli, ["channel-download", "@Example", "--delay", "0"]
    )

    assert result.exit_code == 0, result.output
    assert "Download Partially Complete" in result.output
    assert "Earlier upload pages were preserved." in result.output
    assert "Upload enumeration issue" in result.output
    outcome = mock_log_result.call_args.args[1]
    assert outcome.status_value == ResultStatus.PARTIAL.value
    assert outcome.data["downloaded"] == 1
    assert outcome.data["continuation"]["next_page_token"] == "FAILED_PAGE"
    assert outcome.errors[0].stage == "enumeration"
    assert outcome.errors[0].details["page"] == 2
    assert outcome.warnings == ["Earlier upload pages were preserved."]
    downloader._save_transcript.assert_called_once()
    saved_manifest = downloader._save_manifest.call_args.args[1]
    assert saved_manifest["upload_enumeration"]["coverage"]["partial"] is True


def test_channel_download_zero_row_partial_is_visible_and_not_fully_synced(
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
        "video_count": 2,
        "subscriber_count": 12,
        "uploads_playlist_id": "uploads",
    }
    provider = _upload_enumeration(
        [],
        next_page_token="RETRY_PAGE",
        partial=True,
        stopping_reason="partial_failure",
        warnings=["No usable upload rows were returned before failure."],
        errors=[{
            "type": "YouTubeChannelAPIError",
            "message": "A later uploads page failed",
            "stage": "enumeration",
        }],
    )
    with (
        patch("filmot.channel_dl.ChannelDownloader", return_value=downloader),
        patch("filmot.channel_dl.get_channel_info", return_value=info),
        patch(
            "filmot.channel_dl.enumerate_uploads_detailed",
            return_value=provider,
        ),
        patch("filmot.ledger.log_event"),
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = runner.invoke(cli, ["channel-download", "UC-example"])

    assert result.exit_code == 0, result.output
    assert "This run is partial; No new transcripts" in result.output
    assert "A later uploads page failed" in result.output
    assert "--page-token RETRY_PAGE" in result.output
    outcome = log_result.call_args.args[1]
    assert outcome.status_value == ResultStatus.PARTIAL.value
    assert outcome.data["already_synced"] is False


def test_channel_download_rejects_malformed_video_before_manifest_write(
    runner,
):
    downloader = MagicMock()
    info = {
        "name": "Example",
        "video_count": 1,
        "subscriber_count": 12,
        "uploads_playlist_id": "uploads",
    }
    malformed = _upload_enumeration([{
        "video_id": "../../escape",
        "title": "Bad identity",
        "published_at": "2026-09-12T00:00:00Z",
    }])
    with (
        patch("filmot.channel_dl.ChannelDownloader", return_value=downloader),
        patch("filmot.channel_dl.get_channel_info", return_value=info),
        patch(
            "filmot.channel_dl.enumerate_uploads_detailed",
            return_value=malformed,
        ),
        patch("filmot.transcript.get_transcript") as get_transcript,
        patch("filmot.ledger.log_event"),
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = runner.invoke(cli, ["channel-download", "UC-example"])

    assert result.exit_code == 1
    assert "exact 11-character YouTube video_id" in result.output
    assert log_result.call_args.args[1].status_value == ResultStatus.FAILED.value
    downloader._resolve_channel_dir.assert_not_called()
    downloader._save_manifest.assert_not_called()
    get_transcript.assert_not_called()


def test_channel_download_rejects_fresh_with_page_token_before_side_effects(
    runner,
):
    with (
        patch("filmot.channel_dl.ChannelDownloader") as downloader,
        patch("filmot.channel_dl.get_channel_info") as get_channel_info,
        patch("filmot.ledger.log_event") as log_event,
    ):
        result = runner.invoke(cli, [
            "channel-download",
            "@Example",
            "--fresh",
            "--page-token",
            "NEXT",
        ])

    assert result.exit_code == 2
    assert "--fresh cannot be combined with --page-token" in result.output
    downloader.assert_not_called()
    get_channel_info.assert_not_called()
    log_event.assert_not_called()


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
