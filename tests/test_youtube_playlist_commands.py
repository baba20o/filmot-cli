"""CLI contracts for curated YouTube playlist discovery."""

from copy import deepcopy
import json
from unittest.mock import patch

from click.testing import CliRunner
import pytest

from filmot.cli import cli
from filmot.discovery import preflight_candidates
from filmot.youtube_resources import YouTubeAPIError


PLAYLIST_ID = "PLcollective_intelligence"
CHANNEL_ID = "UC1234567890123456789012"
VIDEO_ID = "abc12345678"
SECRET = "CLI-PLAYLIST-SECRET-MUST-NOT-SURVIVE"
GOOGLE_API_KEY = "AIza" + "A" * 35


def playlist_provider(*, partial=False, token="NEXT"):
    errors = []
    if partial:
        errors = [{
            "type": "YouTubeAPIError",
            "stage": "playlist-items",
            "message": "A later playlist page timed out",
            "category": "timeout",
            "page": 2,
        }]
    return {
        "provider": "youtube-data-api-v3",
        "playlist": {
            "playlist_id": PLAYLIST_ID,
            "title": "Collective Intelligence",
            "description": "PLAYLIST-DESCRIPTION-NOT-FOR-LEDGER",
            "channel_id": CHANNEL_ID,
            "channel_title": "Research Lab",
            "item_count": 120,
            "privacy_status": "public",
        },
        "playlist_items": [
            {
                "playlist_item_id": "item-one",
                "playlist_id": PLAYLIST_ID,
                "playlist_position": 0,
                "playlist_item_title": "Observed item",
                "playlist_item_description": "ITEM-DESCRIPTION-NOT-FOR-LEDGER",
                "video_id": VIDEO_ID,
                "video_metadata_status": "observed",
            },
            {
                "playlist_item_id": "item-two",
                "playlist_id": PLAYLIST_ID,
                "playlist_position": 1,
                "playlist_item_title": "ID-less item",
                "playlist_item_description": "Unavailable item evidence",
                "video_id": None,
                "video_metadata_status": "not_applicable",
            },
        ],
        "videos": [{
            "video_id": VIDEO_ID,
            "title": "How groups think",
            "description": "FULL-DESCRIPTION-NOT-FOR-LEDGER",
            "channel_id": CHANNEL_ID,
            "channel_title": "Research Lab",
            "published_at": "2026-09-01T00:00:00Z",
            "views": 0,
            "likes": None,
            "comments": 3,
            "duration": "PT8M2S",
            "url": "https://youtube.com/watch?v={}".format(VIDEO_ID),
            "provider": "youtube-data-api-v3",
            "provenance": {
                "resource": "playlistItem",
                "playlist_id": PLAYLIST_ID,
                "playlist_item_id": "item-one",
                "playlist_position": 0,
            },
        }],
        "video_id_outcomes": [
            {"video_id": VIDEO_ID, "status": "observed"}
        ],
        "request": {
            "playlist_id": PLAYLIST_ID,
            "playlist_url": (
                "https://www.youtube.com/playlist?list={}".format(PLAYLIST_ID)
            ),
            "max_pages": 3,
            "max_results": 77,
            "page_token": "START",
        },
        "coverage": {
            "playlist_returned": True,
            "pages_attempted": 2,
            "pages_fetched": 1 if partial else 2,
            "playlist_items_returned": 2,
            "unique_video_ids": 1,
            "videos_returned": 1,
            "idless_items": 1,
            "next_page_token": token,
            "stopping_reason": "partial_failure" if partial else "page_budget",
            "api_attempts": 4,
            "partial": partial,
        },
        "observed_at": "2026-09-12T12:00:00Z",
        "expires_at": "2026-10-12T12:00:00Z",
        "warnings": ["One ID-less playlist item was retained."],
        "errors": errors,
        "api_calls": {
            "playlists": 1,
            "playlist_items": 2,
            "videos": 1,
            "total": 4,
        },
    }


def shelf_provider(*, partial=False, token="SHELF-NEXT"):
    errors = []
    if partial:
        errors = [{
            "type": "YouTubeAPIError",
            "stage": "playlists",
            "message": "Playlist enumeration stopped",
        }]
    return {
        "provider": "youtube-data-api-v3",
        "channel": {
            "channel_id": CHANNEL_ID,
            "title": "Research Lab",
            "description": "CHANNEL-DESCRIPTION-NOT-FOR-LEDGER",
            "video_count": 200,
        },
        "playlists": [
            {
                "playlist_id": PLAYLIST_ID,
                "title": "Collective Intelligence",
                "description": "SHELF-DESCRIPTION-NOT-FOR-LEDGER",
                "item_count": 120,
                "privacy_status": "public",
            },
            {
                "playlist_id": "PLactive_inference",
                "title": "Active Inference",
                "description": "Another shelf description",
                "item_count": 0,
                "privacy_status": "public",
            },
        ],
        "request": {
            "channel_reference": "@ResearchLab",
            "channel_id": CHANNEL_ID,
            "max_pages": 2,
            "max_results": 40,
        },
        "coverage": {
            "channel_returned": True,
            "pages_attempted": 2,
            "pages_fetched": 1 if partial else 2,
            "returned": 2,
            "next_page_token": token,
            "stopping_reason": "partial_failure" if partial else "page_budget",
            "api_attempts": 3,
            "partial": partial,
        },
        "observed_at": "2026-09-12T12:00:00Z",
        "expires_at": "2026-10-12T12:00:00Z",
        "warnings": [],
        "errors": errors,
        "api_calls": {"channels": 1, "playlists": 2, "total": 3},
    }


def test_yt_playlist_raw_forwards_bounds_and_is_pipeline_shaped():
    supplied = (
        "https://youtube.com/watch?v=ignored&list={}"
        "&key={}&index=1#fragment"
    ).format(PLAYLIST_ID, SECRET)
    with (
        patch(
            "filmot.youtube_resources.get_playlist_detailed",
            return_value=playlist_provider(),
        ) as provider,
        patch("filmot.ledger.log_result") as log_result,
        patch("filmot.ledger.log_event") as log_event,
    ):
        result = CliRunner().invoke(cli, [
            "yt-playlist",
            supplied,
            "--pages",
            "3",
            "--max-results",
            "77",
            "--page-token",
            "START",
            "--connect-timeout",
            "2",
            "--read-timeout",
            "8",
            "--retries",
            "1",
            "--show-description",
            "--raw",
        ])

    assert result.exit_code == 0, result.output
    provider.assert_called_once_with(
        PLAYLIST_ID,
        max_pages=3,
        max_results=77,
        page_token="START",
        timeout=(2.0, 8.0),
        retries=1,
    )
    payload = json.loads(result.output)
    assert SECRET not in result.output
    assert payload["provider"] == "youtube-data-api-v3"
    assert payload["videos"][0]["views"] == 0
    assert len(payload["playlist_items"]) == 2
    assert payload["_filmot"]["command"] == "yt-playlist"
    assert payload["_filmot"]["status"] == "completed"
    assert sum(name in payload for name in ("result", "videos", "items")) == 1
    preflight = preflight_candidates(payload)
    assert preflight.ok
    assert [row["video_id"] for row in preflight.candidates] == [VIDEO_ID]
    assert payload["continuation"] == {
        "available": True,
        "next_page_token": "NEXT",
        "argv": [
            "filmot",
            "yt-playlist",
            PLAYLIST_ID,
            "--page-token",
            "NEXT",
            "--pages",
            "3",
            "--max-results",
            "77",
            "--connect-timeout",
            "2.0",
            "--read-timeout",
            "8.0",
            "--retries",
            "1",
            "--show-description",
            "--raw",
        ],
    }
    outcome = log_result.call_args.args[1]
    assert outcome.to_raw_dict() == payload
    compact = log_result.call_args.kwargs["data"]
    assert compact["playlist_items"] == 2
    assert compact["videos"] == 1
    assert compact["continuation"] == payload["continuation"]
    compact_json = json.dumps(compact)
    assert "DESCRIPTION-NOT-FOR-LEDGER" not in compact_json
    assert SECRET not in compact_json
    log_event.assert_not_called()


def test_yt_playlists_raw_uses_resolved_id_for_continuation_and_compact_log():
    with (
        patch(
            "filmot.youtube_resources.list_channel_playlists_detailed",
            return_value=shelf_provider(),
        ) as provider,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(cli, [
            "yt-playlists",
            "https://youtube.com/@ResearchLab",
            "--pages",
            "2",
            "--max-results",
            "40",
            "--raw",
        ])

    assert result.exit_code == 0, result.output
    provider.assert_called_once_with(
        "@ResearchLab",
        max_pages=2,
        max_results=40,
        page_token=None,
        timeout=(5.0, 20.0),
        retries=2,
    )
    payload = json.loads(result.output)
    assert payload["_filmot"]["status"] == "completed"
    assert payload["continuation"]["argv"] == [
        "filmot",
        "yt-playlists",
        CHANNEL_ID,
        "--page-token",
        "SHELF-NEXT",
        "--pages",
        "2",
        "--max-results",
        "40",
        "--raw",
    ]
    compact = log_result.call_args.kwargs["data"]
    assert compact["playlists"] == 2
    assert compact["channel_id"] == CHANNEL_ID
    assert not isinstance(compact["playlists"], list)
    assert "DESCRIPTION-NOT-FOR-LEDGER" not in json.dumps(compact)


@pytest.mark.parametrize(
    "field, bad_value",
    [
        (None, None),
        ("playlist", []),
        ("playlist_items", {}),
        ("playlist_items", None),
        ("playlist_items", [None]),
        ("videos", {}),
        ("videos", None),
        ("videos", [{}]),
        ("video_id_outcomes", {}),
        ("video_id_outcomes", None),
        ("request", []),
        ("request", None),
        ("coverage", []),
        ("coverage", None),
        ("api_calls", []),
        ("api_calls", None),
        ("warnings", {}),
        ("warnings", None),
        ("errors", ["bad"]),
        ("errors", None),
        ("provider", {}),
        ("provider", None),
        ("provider", "filmot"),
    ],
)
def test_yt_playlist_malformed_provider_is_one_typed_raw_failure(
    field, bad_value
):
    provider_result = playlist_provider()
    if field is None:
        provider_result = {}
    else:
        provider_result[field] = bad_value
    with (
        patch(
            "filmot.youtube_resources.get_playlist_detailed",
            return_value=provider_result,
        ),
        patch("filmot.ledger.log_event") as log_event,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(
            cli, ["yt-playlist", PLAYLIST_ID, "--raw"]
        )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["stage"] == "invalid-response"
    assert log_event.call_args.kwargs["failure_stage"] == "invalid-response"
    log_result.assert_not_called()


@pytest.mark.parametrize(
    "field, bad_value",
    [
        (None, None),
        ("channel", []),
        ("playlists", {}),
        ("playlists", None),
        ("playlists", [{}]),
        ("request", []),
        ("request", None),
        ("coverage", {"next_page_token": 123}),
        ("coverage", None),
        ("api_calls", []),
        ("api_calls", None),
        ("warnings", {}),
        ("warnings", None),
        ("errors", ["bad"]),
        ("errors", None),
        ("provider", {}),
        ("provider", None),
        ("provider", "filmot"),
    ],
)
def test_yt_playlists_malformed_provider_is_one_typed_raw_failure(
    field, bad_value
):
    provider_result = shelf_provider()
    if field is None:
        provider_result = {}
    else:
        provider_result[field] = bad_value
    with (
        patch(
            "filmot.youtube_resources.list_channel_playlists_detailed",
            return_value=provider_result,
        ),
        patch("filmot.ledger.log_event") as log_event,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(
            cli, ["yt-playlists", "@ResearchLab", "--raw"]
        )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["_filmot"]["errors"][0]["stage"] == "invalid-response"
    assert log_event.call_args.kwargs["failure_stage"] == "invalid-response"
    log_result.assert_not_called()


@pytest.mark.parametrize(
    "mutations, expected_status",
    [
        ({"playlist": None, "playlist_items": [], "videos": []}, "empty"),
        ({"playlist_items": [], "videos": []}, "empty"),
        ({"videos": []}, "completed"),
    ],
)
def test_yt_playlist_status_matrix(mutations, expected_status):
    provider_result = playlist_provider(token=None)
    provider_result.update(deepcopy(mutations))
    if not provider_result["playlist_items"]:
        provider_result["video_id_outcomes"] = []
    elif not provider_result["videos"]:
        provider_result["video_id_outcomes"] = [{
            "video_id": VIDEO_ID,
            "status": "not_returned",
        }]
    if provider_result["playlist"] is None:
        provider_result["coverage"]["playlist_returned"] = False
        provider_result["coverage"]["stopping_reason"] = "playlist_not_returned"
        provider_result["coverage"]["partial"] = True
    with (
        patch(
            "filmot.youtube_resources.get_playlist_detailed",
            return_value=provider_result,
        ),
        patch("filmot.ledger.log_result"),
    ):
        result = CliRunner().invoke(
            cli, ["yt-playlist", PLAYLIST_ID, "--raw"]
        )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["_filmot"]["status"] == expected_status


@pytest.mark.parametrize(
    "mutation",
    [
        "playlist_missing_with_video",
        "video_outside_playlist_slice",
        "outcome_outside_playlist_slice",
        "observed_outcome_without_video",
        "request_for_another_playlist",
    ],
)
def test_yt_playlist_rejects_cross_scope_provider_rows(mutation):
    provider_result = playlist_provider(token=None)
    if mutation == "playlist_missing_with_video":
        provider_result["playlist"] = None
        provider_result["playlist_items"] = []
    elif mutation == "video_outside_playlist_slice":
        provider_result["videos"][0]["video_id"] = "xyz98765432"
    elif mutation == "outcome_outside_playlist_slice":
        provider_result["video_id_outcomes"][0]["video_id"] = "xyz98765432"
    elif mutation == "observed_outcome_without_video":
        provider_result["videos"] = []
    else:
        provider_result["request"]["playlist_id"] = "PLanother_scope"

    with (
        patch(
            "filmot.youtube_resources.get_playlist_detailed",
            return_value=provider_result,
        ),
        patch("filmot.ledger.log_event") as log_event,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(
            cli, ["yt-playlist", PLAYLIST_ID, "--raw"]
        )

    assert result.exit_code == 1
    assert json.loads(result.output)["_filmot"]["errors"][0]["stage"] == (
        "invalid-response"
    )
    assert log_event.call_args.kwargs["failure_stage"] == "invalid-response"
    log_result.assert_not_called()


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_channel",
        "channel_missing_with_playlists",
        "playlist_for_another_channel",
        "request_for_another_channel",
    ],
)
def test_yt_playlists_rejects_cross_scope_provider_rows(mutation):
    provider_result = shelf_provider(token=None)
    provider_result["request"]["channel_reference"] = CHANNEL_ID
    if mutation == "wrong_channel":
        provider_result["channel"]["channel_id"] = "UCabcdefghijklmnopqrstuv"
        provider_result["request"]["channel_id"] = (
            provider_result["channel"]["channel_id"]
        )
    elif mutation == "channel_missing_with_playlists":
        provider_result["channel"] = None
    elif mutation == "playlist_for_another_channel":
        provider_result["playlists"][0]["channel_id"] = (
            "UCabcdefghijklmnopqrstuv"
        )
    else:
        provider_result["request"]["channel_reference"] = "@AnotherChannel"

    with (
        patch(
            "filmot.youtube_resources.list_channel_playlists_detailed",
            return_value=provider_result,
        ),
        patch("filmot.ledger.log_event") as log_event,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(
            cli, ["yt-playlists", CHANNEL_ID, "--raw"]
        )

    assert result.exit_code == 1
    assert json.loads(result.output)["_filmot"]["errors"][0]["stage"] == (
        "invalid-response"
    )
    assert log_event.call_args.kwargs["failure_stage"] == "invalid-response"
    log_result.assert_not_called()


def test_yt_playlist_partial_human_output_keeps_items_and_copyable_next_step():
    with (
        patch(
            "filmot.youtube_resources.get_playlist_detailed",
            return_value=playlist_provider(partial=True),
        ),
        patch("filmot.ledger.log_result"),
    ):
        result = CliRunner().invoke(cli, [
            "yt-playlist",
            PLAYLIST_ID,
            "--pages",
            "2",
            "--max-results",
            "40",
        ])

    assert result.exit_code == 0, result.output
    assert "Views: 0" in result.output
    assert "No usable video ID" in result.output
    assert "A later playlist page timed out" in result.output
    expected = (
        "filmot yt-playlist {} --page-token NEXT --pages 2 --max-results 40"
    ).format(PLAYLIST_ID)
    assert expected in result.output
    assert result.output.count("NEXT") == 1


def test_yt_playlists_partial_with_no_rows_is_visible_not_empty():
    provider_result = shelf_provider(partial=True, token="RETRY")
    provider_result["playlists"] = []
    provider_result["coverage"]["returned"] = 0
    with (
        patch(
            "filmot.youtube_resources.list_channel_playlists_detailed",
            return_value=provider_result,
        ),
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(
            cli, ["yt-playlists", "@ResearchLab"]
        )

    assert result.exit_code == 0, result.output
    assert "Playlist enumeration stopped" in result.output
    assert log_result.call_args.args[1].status_value == "partial"


@pytest.mark.parametrize(
    "command, identity",
    [
        ("yt-playlist", "not a playlist?key={}".format(SECRET)),
        ("yt-playlist", GOOGLE_API_KEY),
        ("yt-playlists", "@safe&key={}".format(SECRET)),
    ],
)
def test_raw_invalid_identity_is_one_json_error_before_provider(
    command, identity
):
    target = (
        "filmot.youtube_resources.get_playlist_detailed"
        if command == "yt-playlist"
        else "filmot.youtube_resources.list_channel_playlists_detailed"
    )
    with patch(target) as provider:
        result = CliRunner().invoke(cli, [command, identity, "--raw"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["stage"] == "validate-request"
    assert SECRET not in result.output
    assert GOOGLE_API_KEY not in result.output
    provider.assert_not_called()


@pytest.mark.parametrize(
    "command, identity",
    [("yt-playlist", PLAYLIST_ID), ("yt-playlists", "@ResearchLab")],
)
def test_blank_page_token_is_a_pre_request_typed_validation_failure(
    command, identity
):
    target = (
        "filmot.youtube_resources.get_playlist_detailed"
        if command == "yt-playlist"
        else "filmot.youtube_resources.list_channel_playlists_detailed"
    )
    with patch(target) as provider, patch("filmot.ledger.log_event") as log_event:
        result = CliRunner().invoke(
            cli, [command, identity, "--page-token", "   ", "--raw"]
        )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["_filmot"]["errors"][0]["stage"] == "validate-request"
    provider.assert_not_called()
    log_event.assert_not_called()


@pytest.mark.parametrize(
    "command, coverage_field, bad_value",
    [
        ("yt-playlist", "partial", "false"),
        ("yt-playlist", "next_page_token", " "),
        ("yt-playlist", "stopping_reason", ""),
        ("yt-playlist", "playlist_returned", False),
        ("yt-playlists", "partial", "false"),
        ("yt-playlists", "next_page_token", " "),
        ("yt-playlists", "stopping_reason", ""),
        ("yt-playlists", "channel_returned", False),
    ],
)
def test_playlist_commands_reject_contradictory_coverage_controls(
    command, coverage_field, bad_value
):
    is_playlist = command == "yt-playlist"
    provider_result = (
        playlist_provider(token=None)
        if is_playlist
        else shelf_provider(token=None)
    )
    provider_result["coverage"][coverage_field] = bad_value
    target = (
        "filmot.youtube_resources.get_playlist_detailed"
        if is_playlist
        else "filmot.youtube_resources.list_channel_playlists_detailed"
    )
    identity = PLAYLIST_ID if is_playlist else "@ResearchLab"
    with (
        patch(target, return_value=provider_result),
        patch("filmot.ledger.log_event") as log_event,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(cli, [command, identity, "--raw"])

    assert result.exit_code == 1
    assert json.loads(result.output)["_filmot"]["errors"][0]["stage"] == (
        "invalid-response"
    )
    assert log_event.call_args.kwargs["failure_stage"] == "invalid-response"
    log_result.assert_not_called()


def test_playlist_human_output_escapes_provider_markup_and_token():
    provider_result = playlist_provider(token="[red]NEXT[/red]")
    provider_result["playlist"]["title"] = "[red]Curated[/red]"
    provider_result["videos"][0]["title"] = "[bold]Observed[/bold]"
    provider_result["warnings"] = ["[yellow]literal warning[/yellow]"]
    with (
        patch(
            "filmot.youtube_resources.get_playlist_detailed",
            return_value=provider_result,
        ),
        patch("filmot.ledger.log_result"),
    ):
        result = CliRunner().invoke(cli, ["yt-playlist", PLAYLIST_ID])

    assert result.exit_code == 0, result.output
    assert "[red]Curated[/red]" in result.output
    assert "[bold]Observed[/bold]" in result.output
    assert "[yellow]literal warning[/yellow]" in result.output
    assert "[red]NEXT[/red]" in result.output


def test_playlist_shelf_human_output_escapes_provider_markup():
    provider_result = shelf_provider(token=None)
    provider_result["channel"]["title"] = "[red]Channel[/red]"
    provider_result["playlists"][0]["title"] = "[bold]Shelf row[/bold]"
    with (
        patch(
            "filmot.youtube_resources.list_channel_playlists_detailed",
            return_value=provider_result,
        ),
        patch("filmot.ledger.log_result"),
    ):
        result = CliRunner().invoke(cli, ["yt-playlists", "@ResearchLab"])

    assert result.exit_code == 0, result.output
    assert "[red]Channel[/red]" in result.output
    assert "[bold]Shelf row[/bold]" in result.output


def test_provider_request_failure_is_typed_and_redacted():
    error = YouTubeAPIError(
        "YouTube timeout at https://example.test?key={}".format(SECRET),
        category="timeout",
        reason="timeout",
        retryable=True,
        attempts=1,
    )
    with (
        patch(
            "filmot.youtube_resources.get_playlist_detailed",
            side_effect=error,
        ),
        patch("filmot.ledger.log_event") as log_event,
    ):
        result = CliRunner().invoke(
            cli, ["yt-playlist", PLAYLIST_ID, "--raw"]
        )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["_filmot"]["errors"][0]["stage"] == "request"
    assert SECRET not in result.output
    assert SECRET not in repr(log_event.call_args)


def test_raw_serialization_failure_is_the_logged_safe_outcome():
    provider_result = playlist_provider()
    provider_result["coverage"]["bad_number"] = float("nan")
    with (
        patch(
            "filmot.youtube_resources.get_playlist_detailed",
            return_value=provider_result,
        ),
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(
            cli, ["yt-playlist", PLAYLIST_ID, "--raw"]
        )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["stage"] == "serialize-result"
    outcome = log_result.call_args.args[1]
    assert outcome.to_raw_dict() == payload
    assert log_result.call_args.kwargs["data"] == {
        "playlist_id": PLAYLIST_ID,
        "pages": 1,
        "max_results": 25,
        "page_token": None,
        "connect_timeout": 5.0,
        "read_timeout": 20.0,
        "retries": 2,
        "raw": True,
        "failure_stage": "serialize-result",
        "serialization_failed": True,
    }


def test_playlist_command_help_exposes_small_bounded_defaults():
    runner = CliRunner()
    playlist_help = runner.invoke(cli, ["yt-playlist", "--help"])
    shelf_help = runner.invoke(cli, ["yt-playlists", "--help"])

    assert playlist_help.exit_code == shelf_help.exit_code == 0
    assert "default one-page, 25-item slice" in playlist_help.output
    for output in (playlist_help.output, shelf_help.output):
        assert "--pages" in output
        assert "--max-results" in output
        assert "--page-token" in output
        assert "--connect-timeout" in output
        assert "--read-timeout" in output
        assert "--retries" in output
        assert "--raw" in output
