"""Contracts for bounded YouTube playlist and playlist-shelf discovery."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from filmot import youtube_resources as resources


NOW = datetime(2026, 9, 12, 14, 30, tzinfo=timezone.utc)
PLAYLIST_ID = "PLcollective_intelligence"
CHANNEL_ID = "UC1234567890123456789012"
SECRET = "TEST-KEY-MUST-NOT-BE-RETAINED"
GOOGLE_API_KEY = "AIza" + "A" * 35


class SecretSession:
    def __repr__(self):
        return "<session key={}>".format(SECRET)

    def get(self, *args, **kwargs):
        raise requests.Timeout("failed at ?key={}".format(SECRET))


class UnexpectedSecretSession:
    def get(self, *args, **kwargs):
        raise RuntimeError("unexpected transport " + GOOGLE_API_KEY)


def assert_resource_traceback_is_credential_free(error, session):
    assert error.__cause__ is None
    assert error.__context__ is None
    traceback = error.__traceback__
    frames = []
    package_root = str(Path(resources.__file__).resolve().parent)
    while traceback is not None:
        filename = str(Path(traceback.tb_frame.f_code.co_filename).resolve())
        if filename.startswith(package_root + "/"):
            frames.append(traceback.tb_frame)
        traceback = traceback.tb_next
    assert frames
    for frame in frames:
        assert SECRET not in repr(frame.f_locals)
        assert GOOGLE_API_KEY not in repr(frame.f_locals)
        if session is not None:
            assert not any(value is session for value in frame.f_locals.values())


def api_result(data, attempts=1):
    return SimpleNamespace(data=data, attempts=attempts)


def playlist(playlist_id=PLAYLIST_ID, *, title="Collective Intelligence"):
    return {
        "id": playlist_id,
        "snippet": {
            "title": title,
            "description": "A research path",
            "channelId": CHANNEL_ID,
            "channelTitle": "Research Lab",
            "publishedAt": "2026-09-01T00:00:00Z",
        },
        "contentDetails": {"itemCount": "3"},
        "status": {"privacyStatus": "public"},
    }


def playlist_item(item_id, video_id=None, *, position=0):
    resource = {
        "id": item_id,
        "snippet": {
            "playlistId": PLAYLIST_ID,
            "position": position,
            "title": "Item {}".format(item_id),
            "channelId": CHANNEL_ID,
            "channelTitle": "Research Lab",
        },
        "contentDetails": {},
        "status": {"privacyStatus": "public"},
    }
    if video_id is not None:
        resource["contentDetails"]["videoId"] = video_id
    return resource


def detail_result(video_ids, *, attempts=1, partial=False):
    videos = [
        {
            "video_id": video_id,
            "title": "Video {}".format(video_id),
            "description": "detail",
            "provider": "youtube-data-api-v3",
        }
        for video_id in video_ids
    ]
    return {
        "videos": videos,
        "id_outcomes": [
            {"video_id": video_id, "status": "observed"}
            for video_id in video_ids
        ],
        "coverage": {
            "api_attempts": attempts,
            "api_calls": attempts,
            "partial": partial,
        },
        "warnings": [],
        "errors": [],
        "api_calls": {"details": attempts, "total": attempts},
    }


@pytest.fixture(autouse=True)
def configured_provider(monkeypatch):
    monkeypatch.setattr(resources, "validate_youtube_api", lambda: True)


@pytest.mark.parametrize(
    "reference, expected",
    [
        (PLAYLIST_ID, PLAYLIST_ID),
        (
            "https://www.youtube.com/watch?v=video&list={}&index=2".format(
                PLAYLIST_ID
            ),
            PLAYLIST_ID,
        ),
        (
            "https://music.youtube.com/playlist?list={}".format(PLAYLIST_ID),
            PLAYLIST_ID,
        ),
        ("http://youtube.com/playlist?list={}".format(PLAYLIST_ID), None),
        ("https://example.com/playlist?list={}".format(PLAYLIST_ID), None),
        ("https://youtube.com/playlist?list=one&list=two", None),
        ("https://[invalid/playlist?list={}".format(PLAYLIST_ID), None),
        ("", None),
        (None, None),
        (GOOGLE_API_KEY, None),
        (
            "https://youtube.com/playlist?list={}".format(GOOGLE_API_KEY),
            None,
        ),
    ],
)
def test_playlist_reference_parser_is_exact_and_total(reference, expected):
    assert resources.youtube_playlist_id(reference) == expected


def test_playlist_enumeration_is_bounded_enriched_and_credential_safe(monkeypatch):
    calls = []
    responses = iter(
        [
            api_result({"items": [playlist()]}),
            api_result(
                {
                    "items": [
                        playlist_item("item-a", "video-a", position=0),
                        playlist_item("item-b", "video-a", position=1),
                    ],
                    "nextPageToken": "PAGE-2",
                    "pageInfo": {"totalResults": 3, "resultsPerPage": 2},
                }
            ),
            api_result(
                {
                    "items": [playlist_item("item-c", None, position=2)],
                    "pageInfo": {"totalResults": 3, "resultsPerPage": 1},
                }
            ),
        ]
    )

    def request(url, params, **options):
        calls.append((url, params, options))
        return next(responses)

    detail_calls = []

    def details(video_ids, **options):
        detail_calls.append((list(video_ids), options))
        return detail_result(video_ids)

    monkeypatch.setattr(resources, "_request_json", request)
    monkeypatch.setattr(resources, "get_video_details_detailed", details)
    supplied = (
        "https://youtube.com/watch?v=ignored&list={}"
        "&key={}&index=1#fragment"
    ).format(PLAYLIST_ID, SECRET)

    result = resources.get_playlist_detailed(
        supplied,
        max_pages=2,
        max_results=3,
        page_token="START",
        timeout=(2, 9),
        retries=0,
        now=NOW,
    )

    assert result["request"]["playlist_id"] == PLAYLIST_ID
    assert result["request"]["playlist_url"] == (
        "https://www.youtube.com/playlist?list={}".format(PLAYLIST_ID)
    )
    assert SECRET not in repr(result)
    assert [row["playlist_item_id"] for row in result["playlist_items"]] == [
        "item-a",
        "item-b",
        "item-c",
    ]
    assert [row["video_id"] for row in result["videos"]] == ["video-a"]
    assert result["videos"][0]["playlist_item_id"] == "item-a"
    assert detail_calls[0][0] == ["video-a"]
    assert calls[1][1]["pageToken"] == "START"
    assert calls[2][1]["pageToken"] == "PAGE-2"
    assert result["coverage"]["duplicate_video_ids"] == 1
    assert result["coverage"]["idless_items"] == 1
    assert result["coverage"]["stopping_reason"] == "result_budget"
    assert result["api_calls"] == {
        "playlists": 1,
        "playlist_items": 2,
        "videos": 1,
        "total": 4,
    }


def test_later_playlist_item_failure_preserves_prior_page(monkeypatch):
    failure = resources.YouTubeAPIError(
        "safe timeout",
        category="timeout",
        reason="timeout",
        retryable=True,
        attempts=2,
    )
    responses = iter(
        [
            api_result({"items": [playlist()]}),
            api_result(
                {
                    "items": [playlist_item("item-a", "video-a")],
                    "nextPageToken": "PAGE-2",
                }
            ),
            failure,
        ]
    )

    def request(*args, **kwargs):
        outcome = next(responses)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(resources, "_request_json", request)
    monkeypatch.setattr(
        resources,
        "get_video_details_detailed",
        lambda video_ids, **kwargs: detail_result(video_ids),
    )

    result = resources.get_playlist_detailed(
        PLAYLIST_ID, max_pages=2, max_results=10, retries=0, now=NOW
    )

    assert [row["video_id"] for row in result["videos"]] == ["video-a"]
    assert result["coverage"]["partial"] is True
    assert result["coverage"]["stopping_reason"] == "partial_failure"
    assert result["coverage"]["next_page_token"] == "PAGE-2"
    assert result["errors"][0]["stage"] == "playlist-items"
    assert result["api_calls"]["total"] == 5


def test_playlist_not_returned_does_not_enumerate_or_infer_reason(monkeypatch):
    calls = []

    def request(url, params, **options):
        calls.append(url)
        return api_result({"items": []})

    monkeypatch.setattr(resources, "_request_json", request)
    monkeypatch.setattr(
        resources,
        "get_video_details_detailed",
        lambda *args, **kwargs: pytest.fail("details must not be requested"),
    )

    result = resources.get_playlist_detailed(PLAYLIST_ID, now=NOW)

    assert result["playlist"] is None
    assert result["videos"] == []
    assert result["coverage"]["stopping_reason"] == "playlist_not_returned"
    assert result["coverage"]["partial"] is True
    assert result["errors"] == []
    assert calls == [resources.YOUTUBE_PLAYLISTS_URL]


def test_channel_playlist_shelf_resolves_handle_and_retains_continuation(monkeypatch):
    calls = []
    responses = iter(
        [
            api_result(
                {
                    "items": [
                        {
                            "id": CHANNEL_ID,
                            "snippet": {"title": "Research Lab"},
                            "contentDetails": {
                                "relatedPlaylists": {"uploads": "UUuploads"}
                            },
                            "statistics": {"videoCount": "12"},
                        }
                    ]
                }
            ),
            api_result(
                {
                    "items": [playlist("PLone", title="One")],
                    "nextPageToken": "MORE",
                }
            ),
        ]
    )

    def request(url, params, **options):
        calls.append((url, params))
        return next(responses)

    monkeypatch.setattr(resources, "_request_json", request)

    result = resources.list_channel_playlists_detailed(
        "@ResearchLab", max_pages=1, max_results=5, now=NOW
    )

    assert result["channel"]["channel_id"] == CHANNEL_ID
    assert result["channel"]["video_count"] == 12
    assert [row["playlist_id"] for row in result["playlists"]] == ["PLone"]
    assert result["coverage"]["next_page_token"] == "MORE"
    assert result["coverage"]["stopping_reason"] == "page_budget"
    assert calls[0][1]["forHandle"] == "ResearchLab"
    assert calls[1][1]["channelId"] == CHANNEL_ID


def test_first_shelf_page_failure_preserves_resolved_channel(monkeypatch):
    failure = resources.YouTubeAPIError(
        "safe timeout",
        category="timeout",
        reason="timeout",
        retryable=True,
        attempts=2,
    )
    responses = iter([
        api_result({
            "items": [{
                "id": CHANNEL_ID,
                "snippet": {"title": "Research Lab"},
            }]
        }),
        failure,
    ])

    def request(*args, **kwargs):
        outcome = next(responses)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(resources, "_request_json", request)

    result = resources.list_channel_playlists_detailed(
        "@ResearchLab", max_pages=3, page_token="RETRY-ME", now=NOW
    )

    assert result["channel"]["channel_id"] == CHANNEL_ID
    assert result["playlists"] == []
    assert result["coverage"]["pages_attempted"] == 1
    assert result["coverage"]["pages_fetched"] == 0
    assert result["coverage"]["next_page_token"] == "RETRY-ME"
    assert result["coverage"]["stopping_reason"] == "partial_failure"
    assert result["coverage"]["partial"] is True
    assert result["errors"][0]["stage"] == "playlists"
    assert result["api_calls"] == {
        "channels": 1,
        "playlists": 2,
        "total": 3,
    }


@pytest.mark.parametrize(
    "reference",
    [
        "@safe&key={}".format(SECRET),
        "@safe={}".format(SECRET),
        "@foo:bar",
        "@_leading",
        "@trailing-",
    ],
)
def test_noncanonical_handle_fails_before_configuration_or_network(
    monkeypatch, reference
):
    monkeypatch.setattr(
        resources,
        "validate_youtube_api",
        lambda: pytest.fail("configuration must not be read"),
    )
    monkeypatch.setattr(
        resources,
        "_request_json",
        lambda *args, **kwargs: pytest.fail("network must not be used"),
    )

    with pytest.raises(ValueError):
        resources.list_channel_playlists_detailed(reference)


def test_playlist_failure_traceback_drops_caller_session_and_url_secret():
    session = SecretSession()
    supplied = "https://youtube.com/playlist?list={}&key={}".format(
        PLAYLIST_ID, SECRET
    )

    with pytest.raises(resources.YouTubeAPIError) as caught:
        resources.get_playlist_detailed(
            supplied, session=session, retries=0, now=NOW
        )

    assert_resource_traceback_is_credential_free(caught.value, session)


def test_channel_failure_traceback_drops_caller_session():
    session = SecretSession()

    with pytest.raises(resources.YouTubeAPIError) as caught:
        resources.list_channel_playlists_detailed(
            "@ResearchLab", session=session, retries=0, now=NOW
        )

    assert_resource_traceback_is_credential_free(caught.value, session)


@pytest.mark.parametrize("failure_kind", ["invalid-now", "runtime-session"])
def test_unexpected_playlist_failure_drops_all_provider_state(
    monkeypatch, failure_kind
):
    monkeypatch.setenv("YOUTUBE_API_KEY", GOOGLE_API_KEY)
    supplied = (
        "https://youtube.com/playlist?list={}&key={}".format(
            PLAYLIST_ID, GOOGLE_API_KEY
        )
    )
    session = UnexpectedSecretSession() if failure_kind == "runtime-session" else None
    now = NOW if session is not None else GOOGLE_API_KEY

    with pytest.raises(resources.YouTubeAPIError) as caught:
        resources.get_playlist_detailed(
            supplied,
            session=session,
            retries=0,
            now=now,
        )

    assert GOOGLE_API_KEY not in str(caught.value)
    assert caught.value.reason == "unexpectedException"
    assert_resource_traceback_is_credential_free(caught.value, session)


def test_unexpected_channel_failure_drops_all_provider_state(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", GOOGLE_API_KEY)
    session = UnexpectedSecretSession()

    with pytest.raises(resources.YouTubeAPIError) as caught:
        resources.list_channel_playlists_detailed(
            "@ResearchLab",
            session=session,
            retries=0,
            now=NOW,
        )

    assert GOOGLE_API_KEY not in str(caught.value)
    assert caught.value.reason == "unexpectedException"
    assert_resource_traceback_is_credential_free(caught.value, session)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_pages": 0},
        {"max_results": True},
        {"page_token": ""},
        {"timeout": 0},
        {"retries": -1},
        {"retry_backoff": float("nan")},
    ],
)
def test_invalid_controls_fail_before_configuration_or_network(monkeypatch, kwargs):
    monkeypatch.setattr(
        resources,
        "validate_youtube_api",
        lambda: pytest.fail("configuration must not be read"),
    )
    monkeypatch.setattr(
        resources,
        "_request_json",
        lambda *args, **options: pytest.fail("network must not be used"),
    )

    with pytest.raises(ValueError):
        resources.get_playlist_detailed(PLAYLIST_ID, **kwargs)
