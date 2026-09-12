import hashlib
import json
from unittest.mock import patch

from click.testing import CliRunner

from filmot.cli import cli


def _yt_payload(videos):
    return {
        "query": "fresh systems research",
        "days": 2,
        "order": "date",
        "max_results": 25,
        "filters": {"region": "US"},
        "videos": videos,
        "_filmot": {
            "schema": "filmot.result/v1",
            "command": "yt-search",
            "status": "completed",
            "errors": [],
            "warnings": [],
        },
    }


def _transcript(video_id):
    return {
        "video_id": video_id,
        "full_text": "A complete transcript for the selected discovery.",
        "segments": [{"text": "A complete transcript", "start": 1.0}],
        "source": "youtube",
        "route": "direct",
        "routes_tried": ["direct"],
        "language": "en",
        "is_generated": True,
        "duration_seconds": 61.0,
        "segment_count": 1,
    }


def test_unchanged_yt_search_raw_payload_pipes_into_download():
    video = {
        "video_id": "abc12345678",
        "title": "Fresh result",
        "description": "Known at discovery time",
        "channel_title": "Research channel",
        "channel_id": "UC-research",
        "published_at": "2026-09-12T09:00:00Z",
        "views": 0,
        "likes": 4,
        "comments": None,
        "duration": "PT1M1S",
        "metadata_observed_at": "2026-09-12T10:00:00Z",
        "metadata_expires_at": "2026-10-12T10:00:00Z",
    }
    piped = json.dumps(_yt_payload([video]))
    with (
        patch("filmot.library.get_library") as get_library,
        patch(
            "filmot.transcript.get_transcript",
            return_value=_transcript(video["video_id"]),
        ) as get_transcript,
        patch("filmot.ledger.log_result") as log_result,
        patch("filmot.ledger.log_event"),
    ):
        library = get_library.return_value
        library.exists.return_value = False
        library.list_transcripts.return_value = []
        result = CliRunner().invoke(
            cli,
            ["download", "--topic", "fresh", "--count", "1"],
            input=piped,
        )

    assert result.exit_code == 0, result.output
    get_transcript.assert_called_once_with(
        video["video_id"],
        languages=None,
        progress_callback=get_transcript.call_args.kwargs["progress_callback"],
        fresh_primary=True,
    )
    saved = library.save.call_args.kwargs
    assert saved["video_id"] == video["video_id"]
    assert saved["metadata"]["title"] == "Fresh result"
    assert saved["metadata"]["channel"] == "Research channel"
    assert saved["metadata"]["channel_id"] == "UC-research"
    assert saved["metadata"]["views"] == 0
    assert saved["metadata"]["likes"] == 4
    assert saved["metadata"]["discovery_provider"] == "youtube"
    assert saved["metadata"]["provider_fields"][
        "metadata_observed_at"
    ] == "2026-09-12T10:00:00Z"
    discovery_ref = "sha256:" + hashlib.sha256(piped.encode("utf-8")).hexdigest()
    lifecycle = library.replace_youtube_metadata.call_args
    assert lifecycle.args[0:2] == (video["video_id"], "fresh")
    assert lifecycle.args[2]["provider"] == "youtube"
    assert lifecycle.kwargs["request_ref"] == discovery_ref
    assert log_result.call_args.args[1].data["discovery_ref"] == discovery_ref


def test_download_preflights_complete_batch_before_any_mutation():
    payload = _yt_payload([
        {"video_id": "abc12345678", "title": "Valid first row"},
        {"title": "Missing identity later in batch"},
    ])
    with (
        patch("filmot.library.get_library") as get_library,
        patch("filmot.transcript.get_transcript") as get_transcript,
        patch("filmot.ledger.log_result"),
        patch("filmot.ledger.log_event"),
    ):
        result = CliRunner().invoke(
            cli,
            ["download", "--topic", "fresh"],
            input=json.dumps(payload),
        )

    assert result.exit_code != 0
    assert "validation failed" in result.output.lower()
    get_library.assert_not_called()
    get_transcript.assert_not_called()
