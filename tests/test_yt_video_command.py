"""Command contract for exact-ID YouTube metadata lookup."""

import json
from unittest.mock import patch

from click.testing import CliRunner

from filmot.cli import cli


def _provider_result(*, partial=False):
    return {
        "provider": "youtube-data-api-v3",
        "videos": [{
            "video_id": "abc12345678",
            "title": "Exact source",
            "description": "A direct API description",
            "channel_title": "Primary Channel",
            "published_at": "2026-09-10T12:00:00Z",
            "views": 0,
            "likes": None,
            "comments": 3,
            "duration": "PT2M3S",
            "url": "https://youtube.com/watch?v=abc12345678",
            "metadata_observed_at": "2026-09-12T12:00:00Z",
            "metadata_expires_at": "2026-10-12T12:00:00Z",
        }],
        "request": {
            "video_ids": ["abc12345678", "def12345678"],
            "requested_at": "2026-09-12T12:00:00Z",
            "parts": ["snippet", "statistics"],
        },
        "coverage": {
            "requested_count": 2,
            "matched_count": 1,
            "missing_count": 1,
            "missing_video_ids": ["def12345678"],
            "unprocessed_video_ids": [],
            "batches_completed": 1,
            "api_calls": 1,
            "stopping_reason": "not_returned",
            "partial": partial,
        },
        "id_outcomes": [
            {"video_id": "abc12345678", "status": "observed"},
            {"video_id": "def12345678", "status": "not_returned"},
        ],
        "observed_at": "2026-09-12T12:00:00Z",
        "expires_at": "2026-10-12T12:00:00Z",
        "warnings": ["One requested video was not returned."],
        "errors": [],
    }


def test_yt_video_accepts_urls_commas_dedupes_and_emits_pipeline_shape():
    with (
        patch(
            "filmot.youtube_search.get_video_details_detailed",
            return_value=_provider_result(partial=True),
        ) as provider,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(cli, [
            "yt-video",
            "https://www.youtube.com/shorts/abc12345678,def12345678",
            "https://youtu.be/abc12345678",
            "--raw",
        ])

    assert result.exit_code == 0, result.output
    provider.assert_called_once_with(
        ["abc12345678", "def12345678"],
        timeout=(5.0, 20.0),
        retries=2,
    )
    payload = json.loads(result.output)
    assert payload["videos"][0]["views"] == 0
    assert payload["coverage"]["missing_video_ids"] == ["def12345678"]
    assert payload["_filmot"]["command"] == "yt-video"
    assert payload["_filmot"]["status"] == "partial"
    outcome = log_result.call_args.args[1]
    assert outcome.data == {
        "videos": payload["videos"],
        "id_outcomes": payload["id_outcomes"],
        "request": payload["request"],
        "coverage": payload["coverage"],
        "observed_at": "2026-09-12T12:00:00Z",
        "expires_at": "2026-10-12T12:00:00Z",
        "show_description": False,
    }
    # Durable event data records request/coverage, not API descriptions.
    compact = log_result.call_args.kwargs["data"]
    assert "videos" not in compact
    assert "description" not in json.dumps(compact)


def test_yt_video_rejects_malformed_identity_before_provider_call():
    with patch(
        "filmot.youtube_search.get_video_details_detailed"
    ) as provider:
        result = CliRunner().invoke(cli, ["yt-video", "not-a-video-id"])

    assert result.exit_code == 2
    assert "11-character YouTube video ID" in result.output
    provider.assert_not_called()


def test_yt_video_human_output_preserves_zero_and_missing_counts():
    with (
        patch(
            "filmot.youtube_search.get_video_details_detailed",
            return_value=_provider_result(),
        ),
        patch("filmot.ledger.log_result"),
    ):
        result = CliRunner().invoke(cli, [
            "yt-video", "abc12345678", "def12345678",
            "--show-description",
        ])

    assert result.exit_code == 0, result.output
    assert "Views: 0 | Likes: unknown | Comments: 3" in result.output
    assert "A direct API description" in result.output
    assert "not_returned" in result.output


def test_yt_video_first_batch_failure_is_typed_raw_failure():
    provider = {
        "provider": "youtube-data-api-v3",
        "videos": [],
        "request": {"video_ids": ["abc12345678"]},
        "coverage": {
            "requested_count": 1,
            "matched_count": 0,
            "batches_completed": 0,
            "api_calls": 1,
            "stopping_reason": "request_failed",
            "partial": True,
        },
        "warnings": [],
        "errors": [{
            "stage": "videos.list",
            "type": "YouTubeAPIError",
            "message": "YouTube API quota error",
            "category": "quota",
        }],
    }
    with (
        patch(
            "filmot.youtube_search.get_video_details_detailed",
            return_value=provider,
        ),
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(
            cli, ["yt-video", "abc12345678", "--raw"]
        )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["stage"] == "videos.list"
    assert log_result.call_args.args[1].status_value == "failed"
