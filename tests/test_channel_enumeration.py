"""Focused contracts for bounded YouTube uploads enumeration."""

from datetime import datetime, timezone
import json
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

import filmot.channel_dl as channel_dl
from filmot.channel_dl import (
    YouTubeChannelAPIError,
    enumerate_uploads_detailed,
    list_all_video_ids,
)


API_KEY = "TEST-CHANNEL-ENUMERATION-KEY-NEVER-LIVE"
PLAYLIST_ID = "UU-test-uploads"


def _playlist_item(video_id, *, title=None):
    return {
        "id": f"playlist-item-{video_id}",
        "snippet": {
            "resourceId": {"videoId": video_id},
            "title": title or video_id,
            "playlistId": PLAYLIST_ID,
        },
        "contentDetails": {"videoId": video_id},
        "status": {"privacyStatus": "public"},
    }


def _youtube_service(outcomes):
    requests = []
    for outcome in outcomes:
        request = MagicMock()
        if isinstance(outcome, Exception):
            request.execute.side_effect = outcome
        else:
            request.execute.return_value = outcome
        requests.append(request)

    list_method = MagicMock(side_effect=requests)
    resource = MagicMock()
    resource.list = list_method
    service = MagicMock()
    service.playlistItems.return_value = resource
    return service, list_method


def _install_service(monkeypatch, outcomes):
    service, list_method = _youtube_service(outcomes)
    monkeypatch.setenv("YOUTUBE_API_KEY", API_KEY)
    monkeypatch.setattr(
        "googleapiclient.discovery.build",
        lambda *args, **kwargs: service,
    )
    return list_method


def _http_error(*, status=403, reason="quotaExceeded"):
    response = Response({"status": str(status), "reason": "Forbidden"})
    content = json.dumps({
        "error": {
            "code": status,
            "message": f"request rejected for key={API_KEY}",
            "errors": [{"reason": reason}],
        }
    }).encode()
    return HttpError(
        response,
        content,
        uri=(
            "https://youtube.googleapis.com/youtube/v3/playlistItems"
            f"?part=snippet&key={API_KEY}"
        ),
    )


def _assert_provider_traceback_is_credential_free(error):
    assert error.__cause__ is None
    assert error.__context__ is None
    traceback = error.__traceback__
    provider_frames = []
    while traceback is not None:
        if traceback.tb_frame.f_code.co_filename == channel_dl.__file__:
            provider_frames.append(traceback.tb_frame)
        traceback = traceback.tb_next

    assert provider_frames
    for frame in provider_frames:
        assert API_KEY not in repr(frame.f_locals)
        assert not any(
            isinstance(value, (HttpError, Response, MagicMock))
            for value in frame.f_locals.values()
        )


def test_detailed_enumeration_resumes_dedupes_and_reports_coverage(monkeypatch):
    monkeypatch.setattr(
        channel_dl,
        "_utc_now",
        lambda: datetime(2026, 9, 12, 10, 30, tzinfo=timezone.utc),
    )
    pages = [
        {
            "items": [
                _playlist_item("video-one"),
                "not-an-object",
                {"id": "missing-video-id", "snippet": {"title": "missing"}},
            ],
            "nextPageToken": "PAGE-2",
            "pageInfo": {"totalResults": 8, "resultsPerPage": 3},
        },
        {
            "items": [
                _playlist_item("video-one", title="duplicate"),
                _playlist_item("video-two"),
            ],
            "nextPageToken": "PAGE-3",
            "pageInfo": {"totalResults": 8, "resultsPerPage": 2},
        },
    ]
    list_method = _install_service(monkeypatch, pages)
    progress = MagicMock()

    result = enumerate_uploads_detailed(
        PLAYLIST_ID,
        max_pages=2,
        max_items=10,
        page_token="START",
        progress_callback=progress,
    )

    assert [row["video_id"] for row in result["videos"]] == [
        "video-one",
        "video-two",
    ]
    assert result["provider"] == "youtube-data-api-v3"
    assert result["observed_at"] == "2026-09-12T10:30:00Z"
    assert result["expires_at"] == "2026-10-12T10:30:00Z"
    assert result["request"] == {
        "uploads_playlist_id": PLAYLIST_ID,
        "max_pages": 2,
        "max_items": 10,
        "page_token": "START",
        "page_budget": 2,
        "item_budget": 10,
        "initial_page_token": "START",
    }
    assert result["coverage"] == {
        "pages_attempted": 2,
        "pages_fetched": 2,
        "api_calls": 2,
        "items_seen": 5,
        "candidates_fetched": 3,
        "unique_results": 2,
        "returned": 2,
        "duplicates_skipped": 1,
        "malformed_items_skipped": 1,
        "idless_items_skipped": 1,
        "next_page_token": "PAGE-3",
        "page_info": {
            "approximate_total_results": 8,
            "results_per_page": 2,
        },
        "approximate_total": 8,
        "stopping_reason": "page_budget",
        "partial": False,
    }
    assert len(result["warnings"]) == 3
    assert result["errors"] == []
    assert result["api_calls"] == {"playlist_items": 2, "total": 2}
    assert [call.kwargs["pageToken"] for call in list_method.call_args_list] == [
        "START",
        "PAGE-2",
    ]
    assert [call.kwargs["maxResults"] for call in list_method.call_args_list] == [
        10,
        9,
    ]
    assert progress.call_args_list[0].args == (1, 8, 1)
    assert progress.call_args_list[1].args == (2, 8, 2)


def test_item_budget_stops_with_exact_continuation(monkeypatch):
    list_method = _install_service(
        monkeypatch,
        [{
            "items": [_playlist_item("video-one"), _playlist_item("video-two")],
            "nextPageToken": "MORE",
        }],
    )

    result = enumerate_uploads_detailed(
        PLAYLIST_ID,
        max_pages=5,
        max_items=2,
    )

    assert result["coverage"]["stopping_reason"] == "item_budget"
    assert result["coverage"]["next_page_token"] == "MORE"
    assert result["coverage"]["partial"] is False
    assert list_method.call_args.kwargs["maxResults"] == 2


def test_later_page_failure_preserves_rows_and_failed_page_token(monkeypatch):
    list_method = _install_service(
        monkeypatch,
        [
            {
                "items": [_playlist_item("video-one")],
                "nextPageToken": "PAGE-2",
            },
            _http_error(reason="playlistItemsNotAccessible"),
        ],
    )

    result = enumerate_uploads_detailed(
        PLAYLIST_ID,
        max_pages=3,
        max_items=20,
    )

    assert [row["video_id"] for row in result["videos"]] == ["video-one"]
    assert result["coverage"]["pages_attempted"] == 2
    assert result["coverage"]["pages_fetched"] == 1
    assert result["coverage"]["next_page_token"] == "PAGE-2"
    assert result["coverage"]["stopping_reason"] == "partial_failure"
    assert result["coverage"]["partial"] is True
    assert result["errors"][0]["stage"] == "enumeration"
    assert result["errors"][0]["page"] == 2
    assert result["errors"][0]["status"] == 403
    assert "playlistItemsNotAccessible" in result["errors"][0]["reason"]
    assert API_KEY not in repr(result)
    assert list_method.call_count == 2


def test_first_page_failure_remains_detached_safe_exception(monkeypatch):
    _install_service(monkeypatch, [_http_error()])

    with pytest.raises(YouTubeChannelAPIError) as caught:
        enumerate_uploads_detailed(
            PLAYLIST_ID,
            max_pages=2,
            max_items=10,
        )

    error = caught.value
    assert error.endpoint == "playlistItems.list"
    assert error.status == 403
    assert API_KEY not in str(error)
    assert API_KEY not in repr(error.to_dict())
    _assert_provider_traceback_is_credential_free(error)


def test_repeated_token_is_partial_for_detailed_api(monkeypatch):
    list_method = _install_service(
        monkeypatch,
        [
            {
                "items": [_playlist_item("video-one")],
                "nextPageToken": "SAME",
            },
            {
                "items": [_playlist_item("video-two")],
                "nextPageToken": "SAME",
            },
        ],
    )

    result = enumerate_uploads_detailed(
        PLAYLIST_ID,
        max_pages=10,
        max_items=20,
    )

    assert [row["video_id"] for row in result["videos"]] == [
        "video-one",
        "video-two",
    ]
    assert result["coverage"]["stopping_reason"] == "partial_failure"
    assert result["coverage"]["next_page_token"] is None
    assert result["coverage"]["partial"] is True
    assert result["errors"][0]["stage"] == "pagination"
    assert "repeated nextPageToken" in result["errors"][0]["reason"]
    assert list_method.call_count == 2


def test_cancel_check_preserves_pending_token_without_an_api_error(monkeypatch):
    monkeypatch.setattr(
        channel_dl,
        "_utc_now",
        lambda: datetime(2026, 9, 12, 10, 30, tzinfo=timezone.utc),
    )
    list_method = _install_service(
        monkeypatch,
        [{
            "items": [_playlist_item("video-one")],
            "nextPageToken": "PAGE-2",
        }],
    )
    decisions = iter((False, True))

    result = enumerate_uploads_detailed(
        PLAYLIST_ID,
        max_pages=10,
        max_items=50,
        cancel_check=lambda: next(decisions),
    )

    assert [row["video_id"] for row in result["videos"]] == ["video-one"]
    assert result["coverage"]["pages_attempted"] == 1
    assert result["coverage"]["pages_fetched"] == 1
    assert result["coverage"]["next_page_token"] == "PAGE-2"
    assert result["coverage"]["stopping_reason"] == "cancelled"
    assert result["coverage"]["partial"] is True
    assert result["observed_at"] == "2026-09-12T10:30:00Z"
    assert result["errors"] == []
    assert "cancelled" in result["warnings"][0].lower()
    assert list_method.call_count == 1


def test_cancel_before_first_page_has_no_metadata_observation(monkeypatch):
    list_method = _install_service(monkeypatch, [])

    result = enumerate_uploads_detailed(
        PLAYLIST_ID,
        max_pages=10,
        max_items=50,
        page_token="RESUME",
        cancel_check=lambda: True,
    )

    assert result["videos"] == []
    assert result["observed_at"] is None
    assert result["expires_at"] is None
    assert result["coverage"]["next_page_token"] == "RESUME"
    assert result["coverage"]["stopping_reason"] == "cancelled"
    assert list_method.call_count == 0


def test_cancel_check_failure_is_sanitized_and_detached(monkeypatch):
    list_method = _install_service(monkeypatch, [])

    def fail_cancel_check():
        raise RuntimeError("cancel callback retained " + API_KEY)

    with pytest.raises(YouTubeChannelAPIError) as caught:
        enumerate_uploads_detailed(
            PLAYLIST_ID,
            max_pages=2,
            max_items=10,
            cancel_check=fail_cancel_check,
        )

    error = caught.value
    assert error.endpoint == "playlist cancellation callback"
    assert error.reason == "cancel callback retained ***"
    assert API_KEY not in str(error)
    assert list_method.call_count == 0
    _assert_provider_traceback_is_credential_free(error)


def test_legacy_wrapper_remains_unbounded_list_returning(monkeypatch):
    list_method = _install_service(
        monkeypatch,
        [
            {
                "items": [_playlist_item("video-one")],
                "nextPageToken": "PAGE-2",
            },
            {"items": [_playlist_item("video-two")]},
        ],
    )

    videos = list_all_video_ids(PLAYLIST_ID)

    assert isinstance(videos, list)
    assert [row["video_id"] for row in videos] == ["video-one", "video-two"]
    assert list_method.call_count == 2
    assert all(
        call.kwargs["maxResults"] == 50
        for call in list_method.call_args_list
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_pages": 0, "max_items": 1},
        {"max_pages": True, "max_items": 1},
        {"max_pages": 1, "max_items": 0},
        {"max_pages": 1, "max_items": False},
        {"max_pages": 1, "max_items": 1, "page_token": ""},
    ],
)
def test_invalid_budgets_and_tokens_fail_before_client_creation(monkeypatch, kwargs):
    monkeypatch.setenv("YOUTUBE_API_KEY", API_KEY)
    build = MagicMock(side_effect=AssertionError("client must not be created"))
    monkeypatch.setattr("googleapiclient.discovery.build", build)

    with pytest.raises(ValueError):
        enumerate_uploads_detailed(PLAYLIST_ID, **kwargs)

    build.assert_not_called()
