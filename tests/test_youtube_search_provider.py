"""Deterministic contract tests for the rich YouTube provider layer."""

from datetime import datetime, timezone

import pytest
import requests

from filmot import youtube_search as youtube


KEY = "TEST-YOUTUBE-KEY-NEVER-LIVE"
NOW = datetime(2026, 1, 15, 12, 30, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, payload, status=200, headers=None, key=KEY):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.url = f"https://www.googleapis.com/youtube/v3/search?key={key}"

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(
                f"HTTP {self.status_code} for {self.url}", response=self
            )
            raise error


class FakeSession:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def search_item(video_id, title=None):
    return {
        "id": {"kind": "youtube#video", "videoId": video_id},
        "snippet": {
            "title": title or video_id,
            "description": f"description {video_id}",
            "channelTitle": "Channel",
            "channelId": "UC123",
            "publishedAt": "2026-01-14T00:00:00Z",
            "liveBroadcastContent": "none",
            "thumbnails": {"high": {"url": f"https://img/{video_id}"}},
        },
    }


def assert_provider_traceback_is_credential_free(error):
    traceback = error.__traceback__
    provider_frames = []
    while traceback is not None:
        if traceback.tb_frame.f_code.co_filename.endswith("youtube_search.py"):
            provider_frames.append(traceback.tb_frame)
        traceback = traceback.tb_next
    assert provider_frames
    for frame in provider_frames:
        assert KEY not in repr(frame.f_locals)
        assert not any(
            isinstance(value, requests.Response)
            for value in frame.f_locals.values()
        )


@pytest.fixture(autouse=True)
def configured_key(monkeypatch):
    monkeypatch.setattr(youtube, "YOUTUBE_API_KEY", KEY)


def test_detailed_paginates_dedupes_and_records_coverage_and_filter():
    session = FakeSession(
        FakeResponse({
            "items": [
                search_item("a"),
                {"id": {"channelId": "not-a-video"}, "snippet": {}},
                "malformed",
            ],
            "nextPageToken": "PAGE-2",
            "pageInfo": {"totalResults": 999, "resultsPerPage": 3},
        }),
        FakeResponse({
            "items": [search_item("a", "duplicate"), search_item("b")],
            "nextPageToken": "PAGE-3",
            "pageInfo": {"totalResults": 999, "resultsPerPage": 2},
        }),
    )

    result = youtube.search_recent_detailed(
        "research",
        max_results=10,
        max_pages=2,
        page_token="START",
        enrich=False,
        timeout=(2, 9),
        retries=0,
        video_paid_product_placement="true",
        now=NOW,
        session=session,
    )

    assert [row["video_id"] for row in result["videos"]] == ["a", "b"]
    assert [row["search_rank"] for row in result["videos"]] == [1, 2]
    assert result["request"] == {
        "query": "research",
        "requested_at": "2026-01-15T12:30:00Z",
        "published_after": "2026-01-08T12:30:00Z",
        "published_before": None,
        "order": "date",
        "days": 7,
        "max_results": 10,
        "max_pages": 2,
        "page_token": "START",
        "filters": {"video_paid_product_placement": "true"},
        "result_budget": 10,
        "page_budget": 2,
        "initial_page_token": "START",
        "timeout": {"connect_seconds": 2.0, "read_seconds": 9.0},
        "retries": 0,
    }
    assert result["coverage"] == {
        "pages_fetched": 2,
        "api_calls": 2,
        "search_calls": 2,
        "detail_calls": 0,
        "candidates_fetched": 3,
        "unique_results": 2,
        "unique_candidates": 2,
        "returned": 2,
        "duplicates_skipped": 1,
        "malformed_items_skipped": 2,
        "next_page_token": "PAGE-3",
        "page_info": {
            "approximate_total_results": 999,
            "results_per_page": 2,
        },
        "approximate_total": 999,
        "stopping_reason": "page_budget",
        "partial": False,
    }
    assert session.calls[0][1]["params"]["pageToken"] == "START"
    assert session.calls[1][1]["params"]["pageToken"] == "PAGE-2"
    assert session.calls[0][1]["params"]["videoPaidProductPlacement"] == "true"
    assert all(call[1]["timeout"] == (2.0, 9.0) for call in session.calls)


def test_result_budget_stops_early_and_retains_continuation_token():
    session = FakeSession(FakeResponse({
        "items": [search_item("a"), search_item("b"), search_item("c")],
        "nextPageToken": "MORE",
    }))

    result = youtube.search_recent_detailed(
        "q", max_results=2, max_pages=5, enrich=False, now=NOW, session=session
    )

    assert [row["video_id"] for row in result["videos"]] == ["a", "b"]
    assert result["coverage"]["stopping_reason"] == "result_budget"
    assert result["coverage"]["next_page_token"] == "MORE"
    assert result["coverage"]["pages_fetched"] == 1


def test_later_search_failure_preserves_earlier_page_as_partial():
    session = FakeSession(
        FakeResponse({"items": [search_item("a")], "nextPageToken": "NEXT"}),
        FakeResponse({
            "error": {"errors": [{"reason": "quotaExceeded"}]}
        }, status=403),
    )

    result = youtube.search_recent_detailed(
        "q", max_results=10, max_pages=2, enrich=False,
        retries=4, now=NOW, session=session,
    )

    assert [row["video_id"] for row in result["videos"]] == ["a"]
    assert result["coverage"]["stopping_reason"] == "partial_failure"
    assert result["coverage"]["partial"] is True
    assert result["errors"][0]["stage"] == "search"
    assert result["errors"][0]["category"] == "quota"
    # Quota exhaustion is never retried.
    assert len(session.calls) == 2


def test_first_page_google_error_is_typed_nonretaining_and_not_retried():
    response = FakeResponse({
        "error": {"errors": [{"reason": "quotaExceeded"}]}
    }, status=403)
    session = FakeSession(response)

    with pytest.raises(youtube.YouTubeAPIError) as caught:
        youtube.search_recent_detailed(
            "q", retries=10, enrich=False, now=NOW, session=session
        )

    error = caught.value
    assert error.category == "quota"
    assert error.reason == "quotaExceeded"
    assert error.status_code == 403
    assert error.retryable is False
    assert error.request is None
    assert error.response is None
    assert error.__context__ is None
    assert KEY not in str(error)
    assert KEY not in repr(error.to_dict())
    assert len(session.calls) == 1


def test_timeout_retries_are_bounded_and_use_explicit_timeout():
    leaking = f"connection failed for ?key={KEY}"
    session = FakeSession(
        requests.Timeout(leaking),
        requests.Timeout(leaking),
        FakeResponse({"items": []}),
    )
    delays = []

    result = youtube.search_recent_detailed(
        "q",
        enrich=False,
        timeout=(1.5, 4),
        retries=2,
        retry_backoff=0.25,
        sleep=delays.append,
        now=NOW,
        session=session,
    )

    assert result["api_calls"]["search"] == 3
    assert delays == [0.25, 0.5]
    assert all(call[1]["timeout"] == (1.5, 4.0) for call in session.calls)


def test_native_boundary_strips_a_key_already_present_in_endpoint():
    session = FakeSession(requests.ConnectionError(f"failed ?key={KEY}"))

    with pytest.raises(youtube.YouTubeAPIError) as caught:
        youtube._get_json(
            f"https://www.googleapis.com/youtube/v3/search?key={KEY}",
            {},
            session=session,
            max_retries=0,
        )

    assert KEY not in str(caught.value)
    assert caught.value.__context__ is None
    assert_provider_traceback_is_credential_free(caught.value)


def test_first_page_failure_has_no_credentials_in_any_provider_traceback_frame():
    session = FakeSession(FakeResponse({
        "error": {"errors": [{"reason": "quotaExceeded"}]}
    }, status=403))

    with pytest.raises(youtube.YouTubeAPIError) as caught:
        youtube.search_recent_detailed(
            "q", enrich=False, retries=0, now=NOW, session=session
        )

    assert_provider_traceback_is_credential_free(caught.value)


def test_details_failure_has_no_credentials_in_any_provider_traceback_frame():
    session = FakeSession(requests.Timeout(f"failed ?key={KEY}"))

    with pytest.raises(youtube.YouTubeAPIError) as caught:
        youtube.get_video_details(
            ["video-id"], max_retries=0, now=NOW, session=session
        )

    assert_provider_traceback_is_credential_free(caught.value)


def test_details_batch_by_fifty_and_distinguish_unknown_from_zero():
    ids = [f"v{index}" for index in range(105)]
    session = FakeSession(
        FakeResponse({"items": [{
            "id": "v0",
            "statistics": {"viewCount": "0", "commentCount": "0"},
            "contentDetails": {"duration": "PT1M"},
        }]}),
        FakeResponse({"items": [{
            "id": "v50",
            "statistics": {},
            "contentDetails": {},
        }]}),
        FakeResponse({"items": [{"id": "v100"}]}),
    )

    details = youtube.get_video_details(ids, session=session, now=NOW)

    assert [len(call[1]["params"]["id"].split(",")) for call in session.calls] == [50, 50, 5]
    assert all(
        call[1]["params"]["part"] == ",".join(youtube.VIDEO_DETAIL_PARTS)
        for call in session.calls
    )
    assert details[0]["views"] == 0
    assert details[0]["comments"] == 0
    assert details[0]["likes"] is None
    assert details[1]["views"] is None


@pytest.mark.parametrize("value", [True, False, -1, "-2", 1.5])
def test_optional_counts_reject_non_counter_values(value):
    assert youtube._optional_int(value) is None


def test_enrichment_merges_by_id_preserves_rank_and_marks_missing():
    session = FakeSession(
        FakeResponse({"items": [search_item("a"), search_item("b")]}),
        FakeResponse({"items": [{
            "id": "b",
            "snippet": {
                "title": "canonical b",
                "channelTitle": "Canonical Channel",
                "channelId": "UC999",
                "tags": ["science"],
                "categoryId": "28",
                "defaultLanguage": "en",
                "defaultAudioLanguage": "en-US",
                "localized": {"title": "localized b"},
                "thumbnails": {"maxres": {"url": "https://img/max"}},
            },
            "statistics": {
                "viewCount": "12", "likeCount": "0", "commentCount": "3"
            },
            "contentDetails": {
                "duration": "PT2M3S", "definition": "hd", "caption": "true",
                "licensedContent": True, "projection": "rectangular",
                "regionRestriction": {"blocked": ["DE"]},
            },
            "status": {
                "uploadStatus": "processed", "privacyStatus": "public",
                "license": "youtube", "embeddable": True,
                "publicStatsViewable": True, "madeForKids": False,
                "containsSyntheticMedia": True,
            },
            "liveStreamingDetails": {
                "scheduledStartTime": "2026-01-16T00:00:00Z",
                "concurrentViewers": "7", "activeLiveChatId": "chat",
            },
            "paidProductPlacementDetails": {"hasPaidProductPlacement": True},
            "topicDetails": {
                "topicIds": ["/m/science"],
                "topicCategories": ["https://example.test/science"],
            },
        }]}),
    )

    result = youtube.search_recent_detailed(
        "q", max_results=2, now=NOW, session=session
    )

    first, second = result["videos"]
    assert [first["video_id"], second["video_id"]] == ["a", "b"]
    assert [first["search_rank"], second["search_rank"]] == [1, 2]
    assert first["title"] == "a"
    assert first["metadata_status"] == "missing"
    assert second["title"] == "canonical b"
    assert second["views"] == 12
    assert second["likes"] == 0
    assert second["contains_synthetic_media"] is True
    assert second["concurrent_viewers"] == 7
    assert second["paid_product_placement"] is True
    assert second["paid_product_placement_disclosure"] == "declared"
    assert second["topic_ids"] == ["/m/science"]
    assert second["metadata_observed_at"] == "2026-01-15T12:30:00Z"
    assert second["metadata_expires_at"] == "2026-02-14T12:30:00Z"
    assert second["observed_at"] == "2026-01-15T12:30:00Z"
    assert second["expires_at"] == "2026-02-14T12:30:00Z"
    assert result["enrichment"]["matched_count"] == 1
    assert result["enrichment"]["missing_count"] == 1
    assert result["enrichment"]["requested"] == 2
    assert result["enrichment"]["returned"] == 1
    assert result["enrichment"]["partial"] is True


def test_explicit_false_disclosure_differs_from_unobserved():
    session = FakeSession(FakeResponse({"items": [
        {"id": "false", "paidProductPlacementDetails": {
            "hasPaidProductPlacement": False
        }},
        {"id": "unknown"},
    ]}))

    false, unknown = youtube.get_video_details(
        ["false", "unknown"], session=session, now=NOW
    )

    assert false["paid_product_placement"] is False
    assert false["paid_product_placement_disclosure"] == "not_declared"
    assert unknown["paid_product_placement"] is None
    assert unknown["paid_product_placement_disclosure"] == "not_observed"


def test_optional_enrichment_failure_preserves_search_rows():
    leaking = requests.Timeout(f"timeout at ?key={KEY}")
    session = FakeSession(
        FakeResponse({"items": [search_item("a")]}),
        leaking,
    )

    result = youtube.search_recent_detailed(
        "q", retries=0, now=NOW, session=session
    )

    assert [row["video_id"] for row in result["videos"]] == ["a"]
    assert result["videos"][0]["metadata_status"] == "enrichment_failed"
    assert result["enrichment"]["partial"] is True
    assert result["errors"][0]["stage"] == "enrichment"
    assert result["errors"][0]["category"] == "timeout"
    assert KEY not in repr(result)


@pytest.mark.parametrize(
    "after,before",
    [
        ("2026-01-01", None),
        ("2026-99-01T00:00:00Z", None),
        ("2026-02-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        ("2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    ],
)
def test_invalid_or_contradictory_bounds_fail_before_network(after, before):
    session = FakeSession()

    with pytest.raises(ValueError):
        youtube.search_recent_detailed(
            "q",
            published_after=after,
            published_before=before,
            now=NOW,
            session=session,
        )

    assert session.calls == []


def test_api_key_is_read_from_environment_at_call_time(monkeypatch):
    runtime_key = "RUNTIME-ROTATED-KEY"
    monkeypatch.setattr(youtube, "YOUTUBE_API_KEY", youtube._IMPORTED_YOUTUBE_API_KEY)
    monkeypatch.setenv("YOUTUBE_API_KEY", runtime_key)
    session = FakeSession(FakeResponse({"items": []}))

    youtube.search_videos("q", session=session, now=NOW)

    assert session.calls[0][1]["params"]["key"] == runtime_key


def test_legacy_search_recent_still_returns_a_list(monkeypatch):
    expected = [{"video_id": "a"}]
    monkeypatch.setattr(
        youtube,
        "search_recent_detailed",
        lambda **kwargs: {"videos": expected},
    )

    result = youtube.search_recent("q")

    assert result is expected


def test_non_mapping_detail_items_are_ignored_safely():
    session = FakeSession(FakeResponse({
        "items": [None, "bad", {"id": {"not": "a string"}}, {"id": "ok"}]
    }))

    details = youtube.get_video_details(["ok"], session=session, now=NOW)

    assert [row["video_id"] for row in details] == ["ok"]
