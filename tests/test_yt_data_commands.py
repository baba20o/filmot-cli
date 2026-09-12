"""CLI contracts for the saved YouTube API metadata lifecycle."""

import json
import re
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from filmot.cli import cli


EXPIRED_A = {
    "topic": "alpha",
    "video_id": "abc12345678",
    "path": "/library/alpha/abc12345678.json",
    "classification": "expired",
    "state": "current",
    "managed": True,
    "legacy_adoptable": False,
    "owned_paths": ["/metadata/views"],
    "owned_path_count": 1,
    "observed_at": "2026-07-01T00:00:00Z",
    "expires_at": "2026-07-31T00:00:00Z",
}
EXPIRED_A_COPY = {
    **EXPIRED_A,
    "topic": "beta",
    "path": "/library/beta/abc12345678.json",
}
EXPIRED_B = {
    **EXPIRED_A,
    "video_id": "def12345678",
    "path": "/library/alpha/def12345678.json",
}
CURRENT = {
    **EXPIRED_A,
    "video_id": "ghi12345678",
    "path": "/library/alpha/ghi12345678.json",
    "classification": "current",
    "expires_at": "2026-10-01T00:00:00Z",
}


def _details_result():
    return {
        "provider": "youtube-data-api-v3",
        "videos": [{
            "video_id": "abc12345678",
            "provider": "youtube-data-api-v3",
            "title": "Fresh title",
            "views": 0,
            "metadata_observed_at": "2026-09-12T12:00:00Z",
            "metadata_expires_at": "2026-10-12T12:00:00Z",
        }],
        "id_outcomes": [
            {
                "video_id": "abc12345678",
                "status": "observed",
                "observed_at": "2026-09-12T12:00:00Z",
                "expires_at": "2026-10-12T12:00:00Z",
            },
            {
                "video_id": "def12345678",
                "status": "not_returned",
                "observed_at": "2026-09-12T12:00:00Z",
                "expires_at": "2026-10-12T12:00:00Z",
            },
        ],
        "request": {
            "video_ids": ["abc12345678", "def12345678"],
            "requested_at": "2026-09-12T12:00:00Z",
            "parts": ["snippet", "statistics"],
            "timeout": {"connect_seconds": 5.0, "read_seconds": 20.0},
            "retries": 2,
        },
        "coverage": {
            "requested_count": 2,
            "matched_count": 1,
            "missing_count": 1,
            "batches_completed": 1,
            "api_calls": 1,
            "stopping_reason": "not_returned",
            "partial": True,
        },
        "observed_at": "2026-09-12T12:00:00Z",
        "expires_at": "2026-10-12T12:00:00Z",
        "warnings": ["One ID was not returned."],
        "errors": [],
    }


def test_yt_data_status_is_offline_read_only_and_typed():
    library = MagicMock()
    library.youtube_metadata_inventory.return_value = [EXPIRED_A, CURRENT]
    with (
        patch("filmot.library.get_library", return_value=library),
        patch("filmot.youtube_search.get_video_details_detailed") as provider,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(
            cli, ["yt-data", "status", "--expired", "--raw"]
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [row["video_id"] for row in payload["rows"]] == ["abc12345678"]
    assert payload["summary"]["offline"] is True
    assert payload["summary"]["expired"] == 1
    assert payload["_filmot"]["command"] == "yt-data-status"
    provider.assert_not_called()
    log_result.assert_not_called()


def test_yt_data_refresh_dry_run_makes_no_api_call_or_write():
    library = MagicMock()
    library.youtube_metadata_inventory.return_value = [EXPIRED_A, CURRENT]
    with (
        patch("filmot.library.get_library", return_value=library),
        patch("filmot.youtube_search.get_video_details_detailed") as provider,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(
            cli, ["yt-data", "refresh", "--dry-run", "--raw"]
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["selected_records"] == 1
    assert payload["summary"]["selected_videos"] == 1
    assert payload["summary"]["api_calls"] == 0
    provider.assert_not_called()
    library.replace_youtube_metadata.assert_not_called()
    log_result.assert_not_called()


def test_yt_data_refresh_fetches_each_id_once_and_updates_every_topic_copy():
    library = MagicMock()
    library.youtube_metadata_inventory.return_value = [
        EXPIRED_A,
        EXPIRED_A_COPY,
        EXPIRED_B,
        CURRENT,
    ]

    def replace(video_id, topic, candidate, **kwargs):
        return {
            "status": "updated",
            "state": "current" if candidate is not None else "not_returned",
            "path": f"/library/{topic}/{video_id}.json",
            "changed_paths": ["/metadata/views"] if candidate else [],
            "removed_paths": [],
            "conflict_paths": [],
            "owned_paths": ["/metadata/views"] if candidate else [],
            "observed_at": kwargs["observed_at"],
            "expires_at": kwargs["expires_at"],
            "request_ref": kwargs["request_ref"],
        }

    library.replace_youtube_metadata.side_effect = replace
    with (
        patch("filmot.library.get_library", return_value=library),
        patch(
            "filmot.youtube_search.get_video_details_detailed",
            return_value=_details_result(),
        ) as provider,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(
            cli, ["yt-data", "refresh", "--raw"]
        )

    assert result.exit_code == 0, result.output
    provider.assert_called_once_with(
        ["abc12345678", "def12345678"],
        timeout=(5.0, 20.0),
        retries=2,
    )
    assert library.replace_youtube_metadata.call_count == 3
    calls = library.replace_youtube_metadata.call_args_list
    assert [(call.args[0], call.args[1]) for call in calls] == [
        ("abc12345678", "alpha"),
        ("def12345678", "alpha"),
        ("abc12345678", "beta"),
    ]
    assert calls[0].args[2]["views"] == 0
    assert calls[1].args[2] is None
    refs = {call.kwargs["request_ref"] for call in calls}
    assert len(refs) == 1
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", refs.pop())
    payload = json.loads(result.output)
    assert payload["summary"]["refreshed_records"] == 2
    assert payload["summary"]["not_returned_records"] == 1
    assert payload["summary"]["api_calls"] == 1
    assert payload["_filmot"]["status"] == "completed"
    compact = log_result.call_args.kwargs["data"]
    assert "rows" not in compact
    assert "Fresh title" not in json.dumps(compact)


def test_yt_data_refresh_first_batch_failure_never_extends_expiry():
    library = MagicMock()
    library.youtube_metadata_inventory.return_value = [EXPIRED_A]
    provider = {
        "provider": "youtube-data-api-v3",
        "videos": [],
        "id_outcomes": [{
            "video_id": "abc12345678",
            "status": "unprocessed",
            "observed_at": None,
            "expires_at": None,
        }],
        "request": {"video_ids": ["abc12345678"]},
        "coverage": {
            "batches_completed": 0,
            "api_calls": 1,
            "partial": True,
        },
        "warnings": [],
        "errors": [{
            "stage": "video-details",
            "type": "YouTubeAPIError",
            "message": "quota exhausted",
        }],
    }
    with (
        patch("filmot.library.get_library", return_value=library),
        patch(
            "filmot.youtube_search.get_video_details_detailed",
            return_value=provider,
        ),
        patch("filmot.ledger.log_result"),
    ):
        result = CliRunner().invoke(
            cli, ["yt-data", "refresh", "--raw"]
        )

    assert result.exit_code == 1
    assert json.loads(result.output)["_filmot"]["status"] == "failed"
    library.replace_youtube_metadata.assert_not_called()


def test_yt_data_purge_defaults_to_expired_and_preserves_current_scope():
    library = MagicMock()
    library.youtube_metadata_inventory.return_value = [EXPIRED_A, CURRENT]
    library.purge_youtube_metadata.return_value = {
        "status": "updated",
        "state": "purged",
        "path": EXPIRED_A["path"],
        "removed_paths": ["/metadata/views"],
        "owned_paths": [],
    }
    with (
        patch("filmot.library.get_library", return_value=library),
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(
            cli, ["yt-data", "purge", "--yes", "--raw"]
        )

    assert result.exit_code == 0, result.output
    library.purge_youtube_metadata.assert_called_once_with(
        "abc12345678", "alpha", reason="expired"
    )
    payload = json.loads(result.output)
    assert payload["summary"]["purged_records"] == 1
    assert payload["summary"]["removed_paths"] == 1
    assert log_result.call_args.args[1].status_value == "completed"


def test_yt_data_purge_requires_confirmation_but_dry_run_does_not():
    library = MagicMock()
    library.youtube_metadata_inventory.return_value = [EXPIRED_A]
    with patch("filmot.library.get_library", return_value=library):
        declined = CliRunner().invoke(
            cli, ["yt-data", "purge"], input="n\n"
        )
        preview = CliRunner().invoke(
            cli, ["yt-data", "purge", "--dry-run", "--raw"]
        )

    assert declined.exit_code == 1
    assert "Aborted" in declined.output
    assert preview.exit_code == 0, preview.output
    library.purge_youtube_metadata.assert_not_called()


def test_yt_data_selector_is_validated_before_inventory_or_api():
    library = MagicMock()
    with (
        patch("filmot.library.get_library", return_value=library),
        patch("filmot.youtube_search.get_video_details_detailed") as provider,
    ):
        result = CliRunner().invoke(
            cli,
            ["yt-data", "refresh", "--video", "definitely-invalid"],
        )

    assert result.exit_code == 2
    library.youtube_metadata_inventory.assert_not_called()
    provider.assert_not_called()


@pytest.mark.parametrize("subcommand", ["refresh", "purge"])
def test_yt_data_empty_video_selector_never_widens_scope(subcommand):
    library = MagicMock()
    with (
        patch("filmot.library.get_library", return_value=library),
        patch("filmot.youtube_search.get_video_details_detailed") as provider,
    ):
        result = CliRunner().invoke(
            cli,
            ["yt-data", subcommand, "--video", ",,", "--yes"]
            if subcommand == "purge"
            else ["yt-data", subcommand, "--video", ",,"],
        )

    assert result.exit_code == 2
    assert "at least one YouTube video ID is required" in result.output
    library.youtube_metadata_inventory.assert_not_called()
    library.purge_youtube_metadata.assert_not_called()
    provider.assert_not_called()


def test_yt_data_refresh_raw_inventory_failure_is_one_typed_json_result():
    with (
        patch("filmot.library.get_library", side_effect=OSError("store busy")),
        patch("filmot.ledger.log_result"),
    ):
        result = CliRunner().invoke(
            cli, ["yt-data", "refresh", "--raw"]
        )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["_filmot"]["command"] == "yt-data-refresh"
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["stage"] == "inventory"


def test_yt_data_purge_raw_inventory_failure_is_one_typed_json_result():
    with (
        patch("filmot.library.get_library", side_effect=OSError("store busy")),
        patch("filmot.ledger.log_result"),
    ):
        result = CliRunner().invoke(
            cli, ["yt-data", "purge", "--raw"]
        )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["_filmot"]["command"] == "yt-data-purge"
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["stage"] == "inventory"
