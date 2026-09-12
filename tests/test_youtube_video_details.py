"""Focused contracts for detailed ``videos.list`` metadata lookup."""

from datetime import datetime, timezone

import pytest
import requests

from filmot import youtube_search as youtube


KEY = "TEST-DETAIL-KEY-NEVER-LIVE"
NOW = datetime(2026, 4, 5, 6, 7, 8, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.url = (
            "https://www.googleapis.com/youtube/v3/videos"
            f"?part=snippet&key={KEY}"
        )

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {self.status_code} for {self.url}", response=self
            )


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


def detail(video_id, *, views=None):
    item = {
        "id": video_id,
        "snippet": {
            "title": f"Title {video_id}",
            "channelTitle": "Research Channel",
            "channelId": "UC123",
            "publishedAt": "2026-04-01T00:00:00Z",
        },
        "statistics": {},
    }
    if views is not None:
        item["statistics"]["viewCount"] = str(views)
    return item


def outcome_map(result):
    return {row["video_id"]: row for row in result["id_outcomes"]}


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


def test_detailed_restores_unique_request_order_and_neutral_absence():
    session = FakeSession(FakeResponse({
        "items": [
            detail("a", views=0),
            detail("b", views=12),
            detail("b", views=99),
            detail("unexpected"),
            None,
        ]
    }))

    result = youtube.get_video_details_detailed(
        ["b", "a", "b", "not-returned", None, ""],
        timeout=(2, 9),
        retries=1,
        retry_backoff=0.25,
        now=NOW,
        session=session,
    )

    assert [row["video_id"] for row in result["videos"]] == ["b", "a"]
    assert result["videos"][0]["views"] == 12
    assert result["videos"][1]["views"] == 0
    assert result["request"] == {
        "video_ids": ["b", "a", "not-returned"],
        "input_count": 6,
        "unique_id_count": 3,
        "requested_at": "2026-04-05T06:07:08Z",
        "parts": list(youtube.VIDEO_DETAIL_PARTS),
        "batch_size": 50,
        "batches_planned": 1,
        "timeout": {"connect_seconds": 2.0, "read_seconds": 9.0},
        "retries": 1,
        "retry_backoff_seconds": 0.25,
    }
    assert result["observed_at"] == "2026-04-05T06:07:08Z"
    assert result["expires_at"] == "2026-05-05T06:07:08Z"

    outcomes = outcome_map(result)
    assert outcomes["b"]["status"] == "observed"
    assert outcomes["a"]["status"] == "observed"
    assert outcomes["not-returned"] == {
        "video_id": "not-returned",
        "status": "not_returned",
        "observed_at": "2026-04-05T06:07:08Z",
        "expires_at": "2026-05-05T06:07:08Z",
    }
    coverage = result["coverage"]
    assert coverage["requested_count"] == 3
    assert coverage["matched_video_ids"] == ["b", "a"]
    assert coverage["missing_video_ids"] == ["not-returned"]
    assert coverage["unprocessed_video_ids"] == []
    assert coverage["duplicate_input_ids_skipped"] == 1
    assert coverage["invalid_input_ids_skipped"] == 2
    assert coverage["duplicate_response_items_skipped"] == 1
    assert coverage["unexpected_items_skipped"] == 1
    assert coverage["malformed_items_skipped"] == 2
    assert coverage["api_calls"] == 1
    assert coverage["stopping_reason"] == "not_returned"
    assert coverage["partial"] is True
    assert result["errors"] == []
    assert KEY not in repr(result)
    assert session.calls[0][1]["timeout"] == (2.0, 9.0)


def test_default_batches_fifty_and_preserves_order_across_batches():
    ids = [f"v{index:03d}" for index in range(105)]
    session = FakeSession(
        FakeResponse({"items": [detail(value) for value in reversed(ids[:50])]}),
        FakeResponse({"items": [detail(value) for value in reversed(ids[50:100])]}),
        FakeResponse({"items": [detail(value) for value in reversed(ids[100:])]}),
    )

    result = youtube.get_video_details_detailed(ids, now=NOW, session=session)

    assert [row["video_id"] for row in result["videos"]] == ids
    assert [
        len(call[1]["params"]["id"].split(",")) for call in session.calls
    ] == [50, 50, 5]
    assert result["coverage"]["batches_planned"] == 3
    assert result["coverage"]["batches_attempted"] == 3
    assert result["coverage"]["batches_completed"] == 3
    assert result["coverage"]["api_attempts"] == 3
    assert result["coverage"]["api_calls"] == 3
    assert result["coverage"]["matched_count"] == 105
    assert result["coverage"]["stopping_reason"] == "completed"
    assert result["coverage"]["partial"] is False
    assert result["api_calls"] == {"details": 3, "total": 3}


def test_custom_batch_and_retry_configuration_is_effective_and_recorded():
    delays = []
    session = FakeSession(
        requests.Timeout(f"failed at ?key={KEY}"),
        FakeResponse({"items": [detail("a"), detail("b")]}),
        FakeResponse({"items": [detail("c")]}),
    )

    result = youtube.get_video_details_detailed(
        ["a", "b", "c"],
        batch_size=2,
        timeout=4,
        retries=1,
        retry_backoff=0.5,
        sleep=delays.append,
        now=NOW,
        session=session,
    )

    assert delays == [0.5]
    assert result["request"]["batch_size"] == 2
    assert result["request"]["timeout"] == {
        "connect_seconds": 4.0,
        "read_seconds": 4.0,
    }
    assert result["coverage"]["batches_completed"] == 2
    assert result["coverage"]["api_attempts"] == 3
    assert [row["video_id"] for row in result["videos"]] == ["a", "b", "c"]


def test_later_batch_failure_preserves_completed_and_separates_outcomes():
    ids = [f"v{index:02d}" for index in range(51)]
    returned = ids[:49]
    session = FakeSession(
        FakeResponse({"items": [detail(value) for value in returned]}),
        requests.Timeout(f"failed at ?key={KEY}"),
    )

    result = youtube.get_video_details_detailed(
        ids, retries=0, now=NOW, session=session
    )

    assert [row["video_id"] for row in result["videos"]] == returned
    assert result["coverage"]["missing_video_ids"] == [ids[49]]
    assert result["coverage"]["unprocessed_video_ids"] == [ids[50]]
    assert outcome_map(result)[ids[49]]["status"] == "not_returned"
    assert outcome_map(result)[ids[50]] == {
        "video_id": ids[50],
        "status": "unprocessed",
        "observed_at": None,
        "expires_at": None,
    }
    assert result["coverage"]["batches_attempted"] == 2
    assert result["coverage"]["batches_completed"] == 1
    assert result["coverage"]["failed_batch_index"] == 2
    assert result["coverage"]["api_attempts"] == 2
    assert result["coverage"]["api_calls"] == 2
    assert result["coverage"]["stopping_reason"] == "partial_failure"
    assert result["coverage"]["partial"] is True
    assert result["errors"][0]["stage"] == "video-details"
    assert result["errors"][0]["category"] == "timeout"
    assert KEY not in repr(result)


def test_first_batch_failure_returns_safe_all_unprocessed_envelope():
    session = FakeSession(FakeResponse({
        "error": {"errors": [{"reason": "quotaExceeded"}]}
    }, status=403))

    result = youtube.get_video_details_detailed(
        ["a", "b"], retries=5, now=NOW, session=session
    )

    assert result["videos"] == []
    assert result["observed_at"] is None
    assert result["expires_at"] is None
    assert result["coverage"]["missing_video_ids"] == []
    assert result["coverage"]["unprocessed_video_ids"] == ["a", "b"]
    assert [row["status"] for row in result["id_outcomes"]] == [
        "unprocessed", "unprocessed"
    ]
    assert result["coverage"]["batches_completed"] == 0
    assert result["coverage"]["api_attempts"] == 1
    assert result["errors"][0]["reason"] == "quotaExceeded"
    assert KEY not in repr(result)
    # Quota exhaustion is non-retryable.
    assert len(session.calls) == 1


def test_empty_input_needs_no_key_and_makes_no_transport_call(monkeypatch):
    monkeypatch.setattr(youtube, "YOUTUBE_API_KEY", "")
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    session = FakeSession()

    result = youtube.get_video_details_detailed([], now=NOW, session=session)

    assert session.calls == []
    assert result["videos"] == []
    assert result["id_outcomes"] == []
    assert result["request"]["video_ids"] == []
    assert result["coverage"]["requested_count"] == 0
    assert result["coverage"]["api_attempts"] == 0
    assert result["coverage"]["api_calls"] == 0
    assert result["coverage"]["stopping_reason"] == "empty"
    assert result["coverage"]["partial"] is False
    assert result["warnings"] == []
    assert result["errors"] == []


@pytest.mark.parametrize("items", [None, {}, "bad"])
def test_malformed_items_envelope_is_counted_as_not_returned(items):
    session = FakeSession(FakeResponse({"items": items}))

    result = youtube.get_video_details_detailed(
        ["a"], retries=0, now=NOW, session=session
    )

    assert result["videos"] == []
    assert result["id_outcomes"][0]["status"] == "not_returned"
    assert result["coverage"]["malformed_items_skipped"] == 1
    assert result["coverage"]["missing_video_ids"] == ["a"]
    assert result["coverage"]["stopping_reason"] == "not_returned"
    assert result["coverage"]["partial"] is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"batch_size": 0},
        {"batch_size": 51},
        {"batch_size": True},
        {"retries": -1},
        {"retries": 1.5},
        {"timeout": 0},
        {"timeout": (1, float("inf"))},
        {"retry_backoff": -1},
    ],
)
def test_invalid_configuration_fails_before_network(kwargs):
    session = FakeSession()

    with pytest.raises(ValueError):
        youtube.get_video_details_detailed(
            ["a"], now=NOW, session=session, **kwargs
        )

    assert session.calls == []


def test_legacy_list_and_raising_contract_remain_unchanged():
    successful = FakeSession(FakeResponse({"items": [detail("a")]}))
    result = youtube.get_video_details(["a"], now=NOW, session=successful)
    assert isinstance(result, list)
    assert [row["video_id"] for row in result] == ["a"]

    failing = FakeSession(requests.Timeout(f"failed at ?key={KEY}"))
    with pytest.raises(youtube.YouTubeAPIError) as caught:
        youtube.get_video_details(
            ["a"], max_retries=0, now=NOW, session=failing
        )

    error = caught.value
    assert error.request is None
    assert error.response is None
    assert error.__context__ is None
    assert KEY not in str(error)
    assert_provider_traceback_is_credential_free(error)
