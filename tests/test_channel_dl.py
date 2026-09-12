"""Tests for channel API integration, corpus search, and proximity parsing."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

import filmot.channel_dl as channel_dl
from filmot.channel_dl import (
    ChannelDownloader,
    YouTubeChannelAPIError,
    _find_near_matches,
    _parse_channel_reference,
    _find_tilde_matches,
    _parse_proximity_query,
    get_channel_info,
    list_all_video_ids,
)


CHANNEL_ID = "UC1234567890123456789012"
DUMMY_API_KEY = "DUMMY-SENTINEL-YOUTUBE-CHANNEL-KEY"


@pytest.mark.parametrize("value", [True, False, -1, "-2", 1.5])
def test_optional_channel_counts_reject_non_counter_values(value):
    assert channel_dl._optional_int(value) is None


def _youtube_service(resource_name, responses):
    """Return a discovery-shaped mock with sequential execute outcomes."""
    requests = []
    for outcome in responses:
        request = MagicMock()
        if isinstance(outcome, Exception):
            request.execute.side_effect = outcome
        else:
            request.execute.return_value = outcome
        requests.append(request)

    list_method = MagicMock()
    list_method.side_effect = requests
    resource = MagicMock()
    resource.list = list_method
    service = MagicMock()
    getattr(service, resource_name).return_value = resource
    return service, list_method


def _install_youtube_service(monkeypatch, service):
    monkeypatch.setenv("YOUTUBE_API_KEY", DUMMY_API_KEY)
    monkeypatch.setattr(
        "googleapiclient.discovery.build",
        lambda *args, **kwargs: service,
    )


def _channel_response(*, statistics=None):
    return {
        "items": [{
            "id": CHANNEL_ID,
            "snippet": {
                "title": "Research Channel",
                "description": "Long-form experiments",
                "customUrl": "@ResearchChannel",
                "publishedAt": "2020-02-03T04:05:06Z",
                "country": "US",
                "defaultLanguage": "en",
            },
            "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}},
            "statistics": statistics if statistics is not None else {
                "viewCount": "9001",
                "subscriberCount": "321",
                "videoCount": "42",
                "hiddenSubscriberCount": False,
            },
            "topicDetails": {
                "topicIds": ["/m/topic", 3],
                "topicCategories": [
                    "https://en.wikipedia.org/wiki/Science",
                    None,
                ],
            },
        }]
    }


def _http_error(endpoint, *, status=403, reason="quotaExceeded"):
    response = Response({"status": str(status), "reason": "Forbidden"})
    content = json.dumps({
        "error": {
            "code": status,
            "message": f"Request rejected for key={DUMMY_API_KEY}",
            "errors": [{"reason": reason}],
        }
    }).encode()
    return HttpError(
        response,
        content,
        uri=f"https://youtube.googleapis.com/youtube/v3/{endpoint}?key={DUMMY_API_KEY}",
    )


def _assert_channel_traceback_is_credential_free(error):
    """Assert provider frames retain neither secrets nor Google transport objects."""
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
        assert DUMMY_API_KEY not in repr(frame.f_locals)
        assert not any(
            isinstance(value, (HttpError, Response, MagicMock))
            for value in frame.f_locals.values()
        )


@pytest.mark.parametrize(
    ("reference", "filter_name", "filter_value"),
    [
        (CHANNEL_ID, "id", CHANNEL_ID),
        (f"https://www.youtube.com/channel/{CHANNEL_ID}/", "id", CHANNEL_ID),
        ("@Exact.Handle", "forHandle", "Exact.Handle"),
        ("https://youtube.com/@Exact.Handle/", "forHandle", "Exact.Handle"),
    ],
)
def test_get_channel_info_resolves_only_exact_id_handle_or_canonical_url(
    monkeypatch,
    reference,
    filter_name,
    filter_value,
):
    service, list_method = _youtube_service("channels", [_channel_response()])
    _install_youtube_service(monkeypatch, service)
    monkeypatch.setattr(
        channel_dl,
        "_utc_now",
        lambda: datetime(2026, 9, 12, 10, 30, tzinfo=timezone.utc),
    )

    info = get_channel_info(reference)

    params = list_method.call_args.kwargs
    assert params[filter_name] == filter_value
    assert ("forHandle" if filter_name == "id" else "id") not in params
    assert params["part"] == "snippet,contentDetails,statistics,topicDetails"
    assert info["channel_id"] == CHANNEL_ID
    assert info["resolved_by"] == (
        "channel_id" if filter_name == "id" else "handle"
    )
    assert info["observed_at"] == "2026-09-12T10:30:00Z"
    assert info["expires_at"] == "2026-10-12T10:30:00Z"


@pytest.mark.parametrize(
    "reference",
    [
        "Research Channel",
        "https://youtube.com/c/ResearchChannel",
        "https://youtube.com/user/ResearchChannel",
        f"http://youtube.com/channel/{CHANNEL_ID}",
        "http://youtube.com/@ResearchChannel",
        "https://example.com/@ResearchChannel",
        "https://youtube.com/@ResearchChannel?feature=shared",
        "UC-too-short",
    ],
)
def test_parse_channel_reference_rejects_fuzzy_or_noncanonical_inputs(reference):
    with pytest.raises(ValueError, match="YouTube|channel"):
        _parse_channel_reference(reference)


def test_get_channel_info_extracts_rich_public_metadata(monkeypatch):
    service, _ = _youtube_service("channels", [_channel_response()])
    _install_youtube_service(monkeypatch, service)

    info = get_channel_info("@ResearchChannel")

    assert info["name"] == "Research Channel"
    assert info["description"] == "Long-form experiments"
    assert info["uploads_playlist_id"] == "UU123"
    assert info["view_count"] == 9001
    assert info["subscriber_count"] == 321
    assert info["video_count"] == 42
    assert info["hidden_subscriber_count"] is False
    assert info["custom_url"] == "@ResearchChannel"
    assert info["published_at"] == "2020-02-03T04:05:06Z"
    assert info["country"] == "US"
    assert info["default_language"] == "en"
    assert info["topic_ids"] == ["/m/topic"]
    assert info["topic_categories"] == [
        "https://en.wikipedia.org/wiki/Science"
    ]


def test_get_channel_info_preserves_unknown_statistics_as_none(monkeypatch):
    service, _ = _youtube_service(
        "channels",
        [_channel_response(statistics={"hiddenSubscriberCount": True})],
    )
    _install_youtube_service(monkeypatch, service)

    info = get_channel_info(CHANNEL_ID)

    assert info["view_count"] is None
    assert info["subscriber_count"] is None
    assert info["video_count"] is None
    assert info["hidden_subscriber_count"] is True


def test_invalid_channel_reference_value_error_has_credential_free_traceback(
    monkeypatch,
):
    service, _ = _youtube_service("channels", [_channel_response()])
    _install_youtube_service(monkeypatch, service)

    with pytest.raises(ValueError, match="Unsupported YouTube channel reference") as caught:
        get_channel_info("not an exact channel reference")

    _assert_channel_traceback_is_credential_free(caught.value)


def test_channel_not_found_value_error_has_credential_free_traceback(monkeypatch):
    service, _ = _youtube_service("channels", [{"items": []}])
    _install_youtube_service(monkeypatch, service)

    with pytest.raises(ValueError, match="Channel not found") as caught:
        get_channel_info(CHANNEL_ID)

    _assert_channel_traceback_is_credential_free(caught.value)


def test_channels_http_error_becomes_detached_credential_safe_domain_error(monkeypatch):
    service, _ = _youtube_service("channels", [_http_error("channels")])
    _install_youtube_service(monkeypatch, service)

    with pytest.raises(YouTubeChannelAPIError) as caught:
        get_channel_info(CHANNEL_ID)

    error = caught.value
    assert error.endpoint == "channels.list"
    assert error.status == 403
    assert "quotaExceeded" in error.reason
    assert DUMMY_API_KEY not in str(error)
    assert DUMMY_API_KEY not in repr(error)
    assert "key=***" in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert not hasattr(error, "request")
    assert not hasattr(error, "response")
    assert not hasattr(error, "resp")
    assert not hasattr(error, "uri")
    _assert_channel_traceback_is_credential_free(error)


def test_client_initialization_failure_has_credential_free_traceback(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", DUMMY_API_KEY)

    def fail_build(*args, **kwargs):
        raise _http_error("discovery", reason="accessNotConfigured")

    monkeypatch.setattr("googleapiclient.discovery.build", fail_build)

    with pytest.raises(YouTubeChannelAPIError) as caught:
        get_channel_info(CHANNEL_ID)

    error = caught.value
    assert error.endpoint == "client initialization"
    assert error.status == 403
    assert "accessNotConfigured" in error.reason
    assert error.__cause__ is None
    assert error.__context__ is None
    _assert_channel_traceback_is_credential_free(error)


def test_playlist_http_error_on_later_page_is_sanitized(monkeypatch):
    first_page = {
        "items": [{"contentDetails": {"videoId": "video-one"}}],
        "nextPageToken": "page-2",
        "pageInfo": {"totalResults": 2},
    }
    service, list_method = _youtube_service(
        "playlistItems",
        [first_page, _http_error("playlistItems", reason="playlistItemsNotAccessible")],
    )
    _install_youtube_service(monkeypatch, service)

    with pytest.raises(YouTubeChannelAPIError) as caught:
        list_all_video_ids("UU123")

    error = caught.value
    assert list_method.call_count == 2
    assert list_method.call_args_list[1].kwargs["pageToken"] == "page-2"
    assert error.endpoint == "playlistItems.list"
    assert error.status == 403
    assert "playlistItemsNotAccessible" in error.reason
    assert DUMMY_API_KEY not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    _assert_channel_traceback_is_credential_free(error)


def test_playlist_callback_failure_is_sanitized_and_detached(monkeypatch):
    service, _ = _youtube_service(
        "playlistItems",
        [{"items": [], "pageInfo": {"totalResults": 0}}],
    )
    _install_youtube_service(monkeypatch, service)

    def fail_progress(current, total, page):
        raise RuntimeError("callback retained " + DUMMY_API_KEY)

    with pytest.raises(YouTubeChannelAPIError) as caught:
        list_all_video_ids("UU123", progress_callback=fail_progress)

    error = caught.value
    assert error.endpoint == "playlist progress callback"
    assert error.reason == "callback retained ***"
    assert DUMMY_API_KEY not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    _assert_channel_traceback_is_credential_free(error)


def test_playlist_enumeration_keeps_available_provenance_and_skips_missing_id(
    monkeypatch,
):
    page = {
        "items": [
            {
                "id": "playlist-item-1",
                "snippet": {
                    "resourceId": {"videoId": "video-one"},
                    "title": "A title",
                    "description": "x" * 600,
                    "publishedAt": "2026-09-01T00:00:00Z",
                    "playlistId": "UU123",
                    "position": 0,
                    "channelId": CHANNEL_ID,
                    "channelTitle": "Research Channel",
                    "videoOwnerChannelId": "UC9999999999999999999999",
                    "videoOwnerChannelTitle": "Video Owner",
                },
                "contentDetails": {
                    "videoId": "video-one",
                    "videoPublishedAt": "2026-08-31T00:00:00Z",
                },
                "status": {"privacyStatus": "public"},
            },
            {
                # Unavailable items can lose snippet ownership fields but still
                # expose a stable contentDetails ID and remain useful records.
                "id": "playlist-item-private",
                "snippet": {"title": "Private video", "position": "1"},
                "contentDetails": {"videoId": "video-private"},
                "status": {"privacyStatus": "private"},
            },
            {"id": "missing-video-id", "snippet": {"title": "Malformed"}},
            "not-an-object",
        ],
        "pageInfo": {"totalResults": 4},
    }
    service, list_method = _youtube_service("playlistItems", [page])
    _install_youtube_service(monkeypatch, service)
    progress = MagicMock()

    videos = list_all_video_ids("UU123", progress_callback=progress)

    assert [video["video_id"] for video in videos] == ["video-one", "video-private"]
    first = videos[0]
    assert first["published_at"] == "2026-09-01T00:00:00Z"
    assert first["video_published_at"] == "2026-08-31T00:00:00Z"
    assert len(first["description"]) == 500
    assert first["playlist_item_id"] == "playlist-item-1"
    assert first["playlist_id"] == "UU123"
    assert first["position"] == 0
    assert first["channel_id"] == CHANNEL_ID
    assert first["channel_title"] == "Research Channel"
    assert first["video_owner_channel_id"] == "UC9999999999999999999999"
    assert first["video_owner_channel_title"] == "Video Owner"
    assert first["privacy_status"] == "public"
    assert videos[1]["privacy_status"] == "private"
    assert videos[1]["position"] == 1
    progress.assert_called_once_with(2, 4, 1)
    assert list_method.call_args.kwargs["part"] == "snippet,contentDetails,status"


def test_playlist_enumeration_rejects_repeated_page_token(monkeypatch):
    pages = [
        {"items": [], "nextPageToken": "same"},
        {"items": [], "nextPageToken": "same"},
    ]
    service, _ = _youtube_service("playlistItems", pages)
    _install_youtube_service(monkeypatch, service)

    with pytest.raises(YouTubeChannelAPIError, match="repeated nextPageToken"):
        list_all_video_ids("UU123")


def _save_channel_transcript(downloader, slug, video_id, title, full_text):
    """Persist a minimal transcript payload for corpus search tests."""
    channel_dir = downloader._get_channel_dir(slug)
    downloader._save_transcript(
        channel_dir,
        video_id,
        {
            "video_id": video_id,
            "title": title,
            "published_at": "2025-01-01T00:00:00Z",
            "full_text": full_text,
        },
    )


def test_parse_near_query_with_or_group_on_left():
    parsed = _parse_proximity_query('("risk" | "drawdown") NEAR/10 "position"')
    assert parsed == ("near", ["risk", "drawdown"], ["position"], 10)


def test_parse_near_query_with_or_groups_on_both_sides():
    parsed = _parse_proximity_query('("risk" | "drawdown") NEAR/10 ("position" | "sizing")')
    assert parsed == ("near", ["risk", "drawdown"], ["position", "sizing"], 10)


def test_search_corpus_supports_or_group_on_left(tmp_path):
    downloader = ChannelDownloader(data_dir=str(tmp_path / ".filmot_data"))
    slug = "test-channel"

    _save_channel_transcript(
        downloader,
        slug,
        "vid12345678",
        "Risk and Sizing",
        "Good risk control depends on position sizing and discipline.",
    )
    _save_channel_transcript(
        downloader,
        slug,
        "vid87654321",
        "Market Structure",
        "Macro trends and liquidity matter, but sizing is discussed elsewhere.",
    )

    results = downloader.search_corpus(slug, '("risk" | "drawdown") NEAR/10 "position"')

    assert len(results) == 1
    assert results[0]["video_id"] == "vid12345678"
    assert results[0]["match_count"] >= 1


def test_search_corpus_supports_or_group_on_right(tmp_path):
    downloader = ChannelDownloader(data_dir=str(tmp_path / ".filmot_data"))
    slug = "test-channel"

    _save_channel_transcript(
        downloader,
        slug,
        "vid12345678",
        "Risk and Sizing",
        "A trader should manage risk with careful position sizing every day.",
    )

    results = downloader.search_corpus(slug, '"risk" NEAR/10 ("position" | "sizing")')

    assert len(results) == 1
    assert results[0]["video_id"] == "vid12345678"
    assert results[0]["match_count"] >= 1


def test_search_corpus_supports_or_groups_on_both_sides(tmp_path):
    downloader = ChannelDownloader(data_dir=str(tmp_path / ".filmot_data"))
    slug = "test-channel"

    _save_channel_transcript(
        downloader,
        slug,
        "vid12345678",
        "Drawdown and Sizing",
        "Limiting drawdown starts with position sizing and risk control.",
    )

    results = downloader.search_corpus(
        slug,
        '("risk" | "drawdown") NEAR/10 ("position" | "sizing")',
    )

    assert len(results) == 1
    assert results[0]["video_id"] == "vid12345678"
    assert results[0]["match_count"] >= 1


def test_parse_basic_near_query():
    parsed = _parse_proximity_query('"machine learning" NEAR/15 "neural network"')
    assert parsed == ("near", ["machine learning"], ["neural network"], 15)


def test_parse_tilde_query():
    parsed = _parse_proximity_query('"deep learning tensorflow"~5')
    assert parsed == ("tilde", ["deep", "learning", "tensorflow"], 5)


def test_near_matching_requires_word_boundaries():
    # "count" must not match inside "accountability"
    assert _find_near_matches("Her accountability and balance grew", "count", "balance", 5) == []
    assert _find_near_matches("Take count of the balance daily", "count", "balance", 5)


def test_near_distance_measured_from_phrase_edges():
    # "risk" is adjacent to the end of the phrase — must match at NEAR/1
    assert _find_near_matches("position sizing risk", "position sizing", "risk", 1)
    # ...but not when separated beyond the limit
    assert _find_near_matches("position sizing always beats reckless risk", "position sizing", "risk", 1) == []


def test_near_matches_phrases_across_newlines():
    assert _find_near_matches("position\nsizing controls risk", "position sizing", "risk", 3)


def test_tilde_repeated_word_requires_distinct_occurrences():
    assert _find_tilde_matches("we manage risk daily", ["risk", "risk"], 5) == []
    assert _find_tilde_matches("risk begets more risk", ["risk", "risk"], 5)


def test_tilde_matching_requires_word_boundaries():
    assert _find_tilde_matches("accountability matters here", ["count", "matters"], 5) == []
    assert _find_tilde_matches("the count matters here", ["count", "matters"], 5)


def test_search_corpus_rejects_unparseable_proximity_query(tmp_path):
    downloader = ChannelDownloader(data_dir=str(tmp_path / ".filmot_data"))
    slug = "test-channel"
    _save_channel_transcript(downloader, slug, "vid12345678", "T", "risk near position")

    with pytest.raises(ValueError, match="proximity operators"):
        downloader.search_corpus(slug, 'risk NEAR/10 position')  # unquoted terms


def test_search_corpus_skips_corrupted_transcript_files(tmp_path):
    downloader = ChannelDownloader(data_dir=str(tmp_path / ".filmot_data"))
    slug = "test-channel"
    _save_channel_transcript(
        downloader, slug, "vid12345678", "Good", "manage risk with position sizing"
    )
    channel_dir = downloader._get_channel_dir(slug)
    (channel_dir / "transcripts" / "corrupt.json").write_text('{"truncated', encoding="utf-8")

    results = downloader.search_corpus(slug, '"risk" NEAR/5 "sizing"')
    assert len(results) == 1


def _save_manifest_for(downloader, slug, channel_id, name):
    channel_dir = downloader._get_channel_dir(slug)
    downloader._save_manifest(
        channel_dir,
        {"channel": {"channel_id": channel_id, "name": name}, "videos": {}},
    )
    return channel_dir


def test_resolve_channel_dir_fresh_channel_uses_name_slug(tmp_path):
    downloader = ChannelDownloader(data_dir=str(tmp_path / ".filmot_data"))
    slug, channel_dir = downloader._resolve_channel_dir(
        {"channel_id": "UCaaa111", "name": "Chat With Traders"}
    )
    assert slug == "chat-with-traders"
    assert channel_dir.name == "chat-with-traders"


def test_resolve_channel_dir_survives_channel_rename(tmp_path):
    downloader = ChannelDownloader(data_dir=str(tmp_path / ".filmot_data"))
    old_dir = _save_manifest_for(downloader, "chat-with-traders", "UCaaa111", "Chat With Traders")

    # Channel renamed itself — resume must find the existing corpus by ID
    slug, channel_dir = downloader._resolve_channel_dir(
        {"channel_id": "UCaaa111", "name": "Trader Talks"}
    )
    assert slug == "chat-with-traders"
    assert channel_dir == old_dir


def test_resolve_channel_dir_disambiguates_slug_collision(tmp_path):
    downloader = ChannelDownloader(data_dir=str(tmp_path / ".filmot_data"))
    _save_manifest_for(downloader, "my-channel", "UCaaa111", "My Channel")

    # A different channel with a colliding name must not merge corpora
    slug, channel_dir = downloader._resolve_channel_dir(
        {"channel_id": "UCbbb222", "name": "MY_CHANNEL"}
    )
    assert slug == "my-channel-bbb222"
    assert channel_dir.name != "my-channel"


def test_save_transcript_is_atomic(tmp_path):
    downloader = ChannelDownloader(data_dir=str(tmp_path / ".filmot_data"))
    channel_dir = downloader._get_channel_dir("test-channel")
    path = downloader._save_transcript(channel_dir, "vid12345678", {"full_text": "hello"})
    assert path.exists()
    assert not list((channel_dir / "transcripts").glob("*.tmp"))
