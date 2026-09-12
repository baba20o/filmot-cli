"""Deterministic contracts for bounded public YouTube comment retrieval."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from filmot import youtube_comments as comments


NOW = datetime(2026, 9, 12, 14, 30, tzinfo=timezone.utc)
OBSERVED_AT = "2026-09-12T14:30:00Z"
EXPIRES_AT = "2026-10-12T14:30:00Z"
VIDEO_ID = "AbCdEfGhI12"
OTHER_VIDEO_ID = "ZyXwVuTsR98"
THREAD_ID = "Ugz-thread_1"
TOP_COMMENT_ID = "Ugx-top.level_1"
REPLY_ID = "Ugy-reply_1"
SECRET = "TEST-COMMENT-KEY-MUST-NOT-BE-RETAINED"
GOOGLE_API_KEY = "AIza" + "A" * 35


def api_result(data, attempts=1):
    return SimpleNamespace(data=data, attempts=attempts)


def comment_resource(
    comment_id=TOP_COMMENT_ID,
    *,
    parent_id=None,
    video_id=VIDEO_ID,
    text="Displayed plain text",
    like_count="0",
    include_optional=True,
):
    snippet = {
        "textDisplay": text,
        "likeCount": like_count,
        "publishedAt": "2026-09-10T10:00:00Z",
        "updatedAt": "2026-09-11T11:00:00Z",
    }
    if video_id is not None:
        snippet["videoId"] = video_id
    if parent_id is not None:
        snippet["parentId"] = parent_id
    if include_optional:
        snippet.update({
            "channelId": "UC-video-owner",
            "authorChannelId": {"value": "UC-comment-author"},
            "authorDisplayName": "Researcher [one]",
            "canRate": False,
            "viewerRating": "none",
            "moderationStatus": "published",
        })
    return {"id": comment_id, "snippet": snippet}


def thread_resource(
    thread_id=THREAD_ID,
    *,
    top_comment_id=TOP_COMMENT_ID,
    video_id=VIDEO_ID,
    total_reply_count=0,
    embedded_replies=None,
):
    resource = {
        "id": thread_id,
        "snippet": {
            "videoId": video_id,
            "channelId": "UC-video-owner",
            "canReply": False,
            "isPublic": True,
            "totalReplyCount": total_reply_count,
            "topLevelComment": comment_resource(top_comment_id),
        },
    }
    if embedded_replies is not None:
        resource["replies"] = {"comments": embedded_replies}
    return resource


class FakeResponse:
    def __init__(self, payload, *, status=200, secret=SECRET):
        self._payload = payload
        self.status_code = status
        self.headers = {}
        self.url = (
            "https://www.googleapis.com/youtube/v3/commentThreads?key={}"
        ).format(secret)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                "HTTP {} for {}".format(self.status_code, self.url),
                response=self,
            )


class SecretSession:
    def __repr__(self):
        return "<comment-session key={}>".format(SECRET)

    def get(self, *args, **kwargs):
        raise requests.Timeout("failed at ?key={}".format(SECRET))


class UnexpectedSecretSession:
    def __repr__(self):
        return "<unexpected-comment-session key={}>".format(GOOGLE_API_KEY)

    def get(self, *args, **kwargs):
        raise RuntimeError("unexpected transport " + GOOGLE_API_KEY)


def assert_traceback_is_credential_free(error, session=None):
    assert error.__cause__ is None
    assert error.__context__ is None
    traceback = error.__traceback__
    frames = []
    package_root = str(Path(comments.__file__).resolve().parent)
    while traceback is not None:
        filename = str(Path(traceback.tb_frame.f_code.co_filename).resolve())
        if filename.startswith(package_root + "/"):
            frames.append(traceback.tb_frame)
        traceback = traceback.tb_next
    assert frames
    for frame in frames:
        locals_repr = repr(frame.f_locals)
        assert SECRET not in locals_repr
        assert GOOGLE_API_KEY not in locals_repr
        if session is not None:
            assert not any(value is session for value in frame.f_locals.values())


@pytest.fixture(autouse=True)
def configured_provider(monkeypatch):
    monkeypatch.setattr(comments, "validate_youtube_api", lambda: True)


@pytest.mark.parametrize(
    "reference, expected",
    [
        (TOP_COMMENT_ID, (TOP_COMMENT_ID, None)),
        ("  {}  ".format(TOP_COMMENT_ID), (TOP_COMMENT_ID, None)),
        (
            "https://www.youtube.com/watch?v={}&lc={}".format(
                VIDEO_ID, TOP_COMMENT_ID
            ),
            (TOP_COMMENT_ID, VIDEO_ID),
        ),
        (
            "https://youtu.be/{}?lc={}&t=20".format(VIDEO_ID, TOP_COMMENT_ID),
            (TOP_COMMENT_ID, VIDEO_ID),
        ),
        (
            "https://music.youtube.com/watch?v={}&lc={}".format(
                VIDEO_ID, TOP_COMMENT_ID
            ),
            (TOP_COMMENT_ID, VIDEO_ID),
        ),
        ("", None),
        (None, None),
        (GOOGLE_API_KEY, None),
        ("api_key={}".format(SECRET), None),
        (
            "http://youtube.com/watch?v={}&lc={}".format(VIDEO_ID, TOP_COMMENT_ID),
            None,
        ),
        (
            "https://example.com/watch?v={}&lc={}".format(VIDEO_ID, TOP_COMMENT_ID),
            None,
        ),
        (
            "https://user:pass@youtube.com/watch?v={}&lc={}".format(
                VIDEO_ID, TOP_COMMENT_ID
            ),
            None,
        ),
        (
            "https://youtube.com:443/watch?v={}&lc={}".format(
                VIDEO_ID, TOP_COMMENT_ID
            ),
            None,
        ),
        ("https://youtube.com/watch?v={}".format(VIDEO_ID), None),
        (
            "https://youtube.com/watch?v={}&lc={}&lc=other".format(
                VIDEO_ID, TOP_COMMENT_ID
            ),
            None,
        ),
        (
            "https://youtube.com/watch?v={}&v={}&lc={}".format(
                VIDEO_ID, OTHER_VIDEO_ID, TOP_COMMENT_ID
            ),
            None,
        ),
        (
            "https://youtube.com/watch?v={}&lc=bad%0Aid".format(VIDEO_ID),
            None,
        ),
        ("https://[invalid/watch?v={}&lc=x".format(VIDEO_ID), None),
    ],
)
def test_comment_reference_parser_is_exact_bounded_and_total(reference, expected):
    assert comments.youtube_comment_reference(reference) == expected
    assert comments.youtube_comment_id(reference) == (
        expected[0] if expected is not None else None
    )


@pytest.mark.parametrize(
    "value, expected",
    [
        ("NEXT-page_1", "NEXT-page_1"),
        ("  NEXT-page_1  ", "NEXT-page_1"),
        ("", None),
        ("   ", None),
        (None, None),
        (True, None),
        ("bad\ntoken", None),
        (GOOGLE_API_KEY, None),
        ("access_token={}".format(SECRET), None),
        ("x" * (comments.MAX_COMMENT_PAGE_TOKEN_CHARS + 1), None),
    ],
)
def test_comment_page_token_validation_is_bounded_and_credential_aware(
    value, expected
):
    assert comments.youtube_comment_page_token(value) == expected


@pytest.mark.parametrize(
    "entrypoint, args, kwargs",
    [
        ("threads", ("not-an-eleven-char-id",), {}),
        ("threads", ("http://youtu.be/{}".format(VIDEO_ID),), {}),
        ("threads", (VIDEO_ID,), {"order": "popular"}),
        ("threads", (VIDEO_ID,), {"reply_mode": "all"}),
        ("threads", (VIDEO_ID,), {"search_terms": ""}),
        ("threads", (VIDEO_ID,), {"search_terms": "x\ny"}),
        (
            "threads",
            (VIDEO_ID,),
            {"search_terms": "x" * (comments.MAX_COMMENT_SEARCH_CHARS + 1)},
        ),
        ("replies", (GOOGLE_API_KEY,), {}),
        ("replies", ("https://youtube.com/watch?v={}".format(VIDEO_ID),), {}),
        (
            "replies",
            (TOP_COMMENT_ID,),
            {"video_reference": "not-video"},
        ),
        (
            "replies",
            (
                "https://youtube.com/watch?v={}&lc={}".format(
                    VIDEO_ID, TOP_COMMENT_ID
                ),
            ),
            {"video_reference": OTHER_VIDEO_ID},
        ),
        ("threads", (VIDEO_ID,), {"max_pages": 0}),
        ("threads", (VIDEO_ID,), {"max_pages": True}),
        ("threads", (VIDEO_ID,), {"max_pages": comments.MAX_COMMENT_PAGES + 1}),
        ("replies", (TOP_COMMENT_ID,), {"max_results": 0}),
        ("replies", (TOP_COMMENT_ID,), {"max_results": True}),
        (
            "replies",
            (TOP_COMMENT_ID,),
            {"max_results": comments.MAX_COMMENT_RESULTS + 1},
        ),
        ("threads", (VIDEO_ID,), {"page_token": ""}),
        ("replies", (TOP_COMMENT_ID,), {"page_token": "bad\ntoken"}),
        ("threads", (VIDEO_ID,), {"page_token": GOOGLE_API_KEY}),
        ("threads", (VIDEO_ID,), {"timeout": 0}),
        ("replies", (TOP_COMMENT_ID,), {"timeout": (1, float("inf"))}),
        ("threads", (VIDEO_ID,), {"retries": -1}),
        ("threads", (VIDEO_ID,), {"retries": comments.MAX_COMMENT_RETRIES + 1}),
        ("replies", (TOP_COMMENT_ID,), {"retry_backoff": float("nan")}),
    ],
)
def test_invalid_inputs_fail_before_configuration_or_network(
    monkeypatch, entrypoint, args, kwargs
):
    monkeypatch.setattr(
        comments,
        "validate_youtube_api",
        lambda: pytest.fail("configuration must not be read"),
    )
    monkeypatch.setattr(
        comments,
        "_request_json",
        lambda *request_args, **request_kwargs: pytest.fail(
            "network must not be used"
        ),
    )
    function = (
        comments.list_video_comment_threads_detailed
        if entrypoint == "threads"
        else comments.list_comment_replies_detailed
    )

    with pytest.raises(ValueError):
        function(*args, **kwargs)


def test_thread_request_is_exact_and_preserves_thread_and_top_level_id(monkeypatch):
    calls = []
    session = object()
    sleeper = lambda delay: None

    def request(url, params, **options):
        calls.append((url, params, options))
        return api_result(
            {
                "items": [thread_resource()],
                "pageInfo": {"totalResults": "31", "resultsPerPage": "1"},
            },
            attempts=2,
        )

    monkeypatch.setattr(comments, "_request_json", request)
    supplied = (
        "https://www.youtube.com/watch?v={}&key={}&feature=share#fragment"
    ).format(VIDEO_ID, SECRET)
    result = comments.list_video_comment_threads_detailed(
        supplied,
        search_terms="  research question  ",
        order="relevance",
        max_pages=3,
        max_results=7,
        page_token="START",
        timeout=(2, 9),
        retries=4,
        retry_backoff=0.25,
        sleep=sleeper,
        session=session,
        now=NOW,
    )

    assert len(calls) == 1
    url, params, options = calls[0]
    assert url == comments.YOUTUBE_COMMENT_THREADS_URL
    assert params == {
        "part": "snippet",
        "videoId": VIDEO_ID,
        "order": "relevance",
        "textFormat": "plainText",
        "searchTerms": "research question",
        "maxResults": 7,
        "pageToken": "START",
    }
    assert options == {
        "session": session,
        "connect_timeout": 2.0,
        "read_timeout": 9.0,
        "max_retries": 4,
        "retry_backoff": 0.25,
        "sleep": sleeper,
    }
    assert result["video_id"] == VIDEO_ID
    assert result["request"]["video_url"] == (
        "https://www.youtube.com/watch?v={}".format(VIDEO_ID)
    )
    assert SECRET not in repr(result)
    row = result["comment_threads"][0]
    assert row["thread_id"] == THREAD_ID
    assert row["top_level_comment"]["comment_id"] == TOP_COMMENT_ID
    assert row["thread_id"] != row["top_level_comment"]["comment_id"]
    assert row["top_level_comment"]["canonical_url"] == (
        "https://www.youtube.com/watch?v={}&lc={}".format(
            VIDEO_ID, TOP_COMMENT_ID
        )
    )
    assert result["coverage"]["page_info"] == {
        "reported_total": 31,
        "results_per_page": 1,
    }
    assert result["coverage"]["reported_total"] == 31
    assert result["api_calls"] == {
        "comment_threads": 2,
        "comments": 0,
        "total": 2,
    }
    assert result["quota"] == {
        "units_per_request": 1,
        "estimated_units": 2,
        "accounting": "attempts_conservative",
    }


def test_comment_fields_keep_zero_false_and_missing_distinct(monkeypatch):
    rich = thread_resource()
    sparse = thread_resource(
        "Ugz-thread_2", top_comment_id="Ugx-top.level_2"
    )
    sparse["snippet"].pop("channelId")
    sparse["snippet"].pop("canReply")
    sparse["snippet"].pop("isPublic")
    sparse["snippet"].pop("totalReplyCount")
    sparse["snippet"]["topLevelComment"] = comment_resource(
        "Ugx-top.level_2", text=None, like_count=None, include_optional=False
    )
    monkeypatch.setattr(
        comments,
        "_request_json",
        lambda *args, **kwargs: api_result({"items": [rich, sparse]}),
    )

    result = comments.list_video_comment_threads_detailed(VIDEO_ID, now=NOW)
    first, second = result["comment_threads"]

    assert first["can_reply"] is False
    assert first["is_public"] is True
    assert first["total_reply_count"] == 0
    assert first["top_level_comment"] == {
        "comment_id": TOP_COMMENT_ID,
        "parent_comment_id": None,
        "video_id": VIDEO_ID,
        "video_id_source": "api",
        "associated_channel_id": "UC-video-owner",
        "author_channel_id": "UC-comment-author",
        "author_display_name": "Researcher [one]",
        "text_display": "Displayed plain text",
        "like_count": 0,
        "can_rate": False,
        "viewer_rating": "none",
        "moderation_status": "published",
        "published_at": "2026-09-10T10:00:00Z",
        "updated_at": "2026-09-11T11:00:00Z",
        "canonical_url": (
            "https://www.youtube.com/watch?v={}&lc={}".format(
                VIDEO_ID, TOP_COMMENT_ID
            )
        ),
        "observed_at": OBSERVED_AT,
        "expires_at": EXPIRES_AT,
        "provider": "youtube-data-api-v3",
    }
    assert second["associated_channel_id"] is None
    assert second["can_reply"] is None
    assert second["is_public"] is None
    assert second["total_reply_count"] is None
    sparse_comment = second["top_level_comment"]
    assert sparse_comment["text_display"] is None
    assert sparse_comment["like_count"] is None
    assert sparse_comment["can_rate"] is None
    assert sparse_comment["author_channel_id"] is None
    assert sparse_comment["author_display_name"] is None


@pytest.mark.parametrize(
    "reported_total, embedded, expected_status, expected_complete",
    [
        (
            2,
            [
                comment_resource("reply-a", parent_id=TOP_COMMENT_ID),
                comment_resource("reply-b", parent_id=TOP_COMMENT_ID),
            ],
            "all_observed_at_response",
            True,
        ),
        (
            3,
            [comment_resource("reply-a", parent_id=TOP_COMMENT_ID)],
            "subset",
            False,
        ),
        (
            None,
            [comment_resource("reply-a", parent_id=TOP_COMMENT_ID)],
            "unknown",
            None,
        ),
        (
            0,
            [comment_resource("reply-a", parent_id=TOP_COMMENT_ID)],
            "inconsistent",
            None,
        ),
    ],
)
def test_embedded_reply_preview_reports_only_observed_completeness(
    monkeypatch,
    reported_total,
    embedded,
    expected_status,
    expected_complete,
):
    calls = []

    def request(url, params, **options):
        calls.append(params)
        return api_result({
            "items": [thread_resource(
                total_reply_count=reported_total,
                embedded_replies=embedded,
            )]
        })

    monkeypatch.setattr(comments, "_request_json", request)
    result = comments.list_video_comment_threads_detailed(
        VIDEO_ID, reply_mode="preview", now=NOW
    )

    assert calls[0]["part"] == "snippet,replies"
    row = result["comment_threads"][0]
    assert row["reply_coverage"] == {
        "mode": "embedded_preview",
        "returned": len(embedded),
        "reported_total": reported_total,
        "status": expected_status,
    }
    assert row["replies_complete"] is expected_complete
    assert all(
        reply["parent_comment_id"] == TOP_COMMENT_ID
        for reply in row["embedded_replies"]
    )
    assert all(
        reply["video_id_source"] == "thread_context"
        for reply in row["embedded_replies"]
    )
    assert any(
        "incomplete or uncertain" in warning for warning in result["warnings"]
    ) == (
        expected_status in {"subset", "unknown", "inconsistent"}
    )


def test_preview_filters_malformed_wrong_parent_and_duplicate_replies(monkeypatch):
    embedded = [
        comment_resource(REPLY_ID, parent_id=TOP_COMMENT_ID),
        comment_resource(REPLY_ID, parent_id=TOP_COMMENT_ID),
        comment_resource("wrong-parent", parent_id="another-parent"),
        {"id": "missing-snippet"},
        "not-a-resource",
    ]
    monkeypatch.setattr(
        comments,
        "_request_json",
        lambda *args, **kwargs: api_result({
            "items": [thread_resource(
                total_reply_count=5,
                embedded_replies=embedded,
            )]
        }),
    )

    result = comments.list_video_comment_threads_detailed(
        VIDEO_ID, reply_mode="preview", now=NOW
    )

    row = result["comment_threads"][0]
    assert [item["comment_id"] for item in row["embedded_replies"]] == [REPLY_ID]
    assert row["reply_coverage"]["status"] == "unknown"
    assert row["replies_complete"] is None
    assert result["coverage"]["embedded_replies_seen"] == 5
    assert result["coverage"]["embedded_replies_returned"] == 1
    assert result["coverage"]["embedded_replies_duplicates"] == 1
    assert result["coverage"]["embedded_replies_wrong_parent"] == 1
    assert result["coverage"]["embedded_replies_malformed"] == 2


def test_duplicate_threads_do_not_inflate_retained_preview_counts(monkeypatch):
    preview = [comment_resource(REPLY_ID, parent_id=TOP_COMMENT_ID)]
    duplicate = thread_resource(
        total_reply_count=1,
        embedded_replies=preview,
    )
    monkeypatch.setattr(
        comments,
        "_request_json",
        lambda *args, **kwargs: api_result({"items": [duplicate, duplicate]}),
    )

    result = comments.list_video_comment_threads_detailed(
        VIDEO_ID,
        reply_mode="preview",
        now=NOW,
    )

    assert len(result["comment_threads"]) == 1
    assert len(result["comment_threads"][0]["embedded_replies"]) == 1
    assert result["coverage"]["duplicates_skipped"] == 1
    assert result["coverage"]["embedded_replies_seen"] == 1
    assert result["coverage"]["embedded_replies_returned"] == 1


def test_reply_request_uses_parent_scope_and_comment_url_video_context(monkeypatch):
    calls = []

    def request(url, params, **options):
        calls.append((url, params, options))
        return api_result({"items": [
            comment_resource(REPLY_ID, parent_id=TOP_COMMENT_ID),
        ]})

    monkeypatch.setattr(comments, "_request_json", request)
    reference = "https://www.youtube.com/watch?v={}&lc={}".format(
        VIDEO_ID, TOP_COMMENT_ID
    )
    result = comments.list_comment_replies_detailed(
        reference,
        max_results=8,
        page_token="REPLY-START",
        timeout=3,
        retries=0,
        now=NOW,
    )

    assert calls[0][0] == comments.YOUTUBE_COMMENTS_URL
    assert calls[0][1] == {
        "part": "snippet",
        "parentId": TOP_COMMENT_ID,
        "textFormat": "plainText",
        "maxResults": 8,
        "pageToken": "REPLY-START",
    }
    assert result["parent_comment_id"] == TOP_COMMENT_ID
    assert result["video_id"] == VIDEO_ID
    assert result["request"]["video_id_source"] == "request_context"
    reply = result["replies"][0]
    assert reply["parent_comment_id"] == TOP_COMMENT_ID
    assert reply["video_id"] == VIDEO_ID
    assert reply["video_id_source"] == "request_context"
    assert reply["canonical_url"] == (
        "https://www.youtube.com/watch?v={}&lc={}".format(VIDEO_ID, REPLY_ID)
    )


def test_bare_parent_reply_request_does_not_invent_video_scope(monkeypatch):
    monkeypatch.setattr(
        comments,
        "_request_json",
        lambda *args, **kwargs: api_result({"items": [
            comment_resource(REPLY_ID, parent_id=TOP_COMMENT_ID),
        ]}),
    )

    result = comments.list_comment_replies_detailed(TOP_COMMENT_ID, now=NOW)

    assert result["video_id"] is None
    assert result["request"]["video_id_source"] is None
    assert result["replies"][0]["video_id"] is None
    assert result["replies"][0]["canonical_url"] is None


def test_reply_video_context_rejects_cross_video_response_rows(monkeypatch):
    monkeypatch.setattr(
        comments,
        "_request_json",
        lambda *args, **kwargs: api_result({
            "items": [comment_resource(
                REPLY_ID,
                parent_id=TOP_COMMENT_ID,
                video_id=OTHER_VIDEO_ID,
            )],
        }),
    )

    result = comments.list_comment_replies_detailed(
        TOP_COMMENT_ID,
        video_reference=VIDEO_ID,
        now=NOW,
    )

    assert result["replies"] == []
    assert result["coverage"]["unexpected_scope_skipped"] == 1
    assert result["coverage"]["partial"] is True


def test_reply_video_context_rejects_rows_without_video_identity(monkeypatch):
    row = comment_resource(REPLY_ID, parent_id=TOP_COMMENT_ID)
    row["snippet"].pop("videoId")
    monkeypatch.setattr(
        comments,
        "_request_json",
        lambda *args, **kwargs: api_result({"items": [row]}),
    )

    result = comments.list_comment_replies_detailed(
        TOP_COMMENT_ID,
        video_reference=VIDEO_ID,
        now=NOW,
    )

    assert result["replies"] == []
    assert result["coverage"]["unexpected_scope_skipped"] == 1
    assert result["coverage"]["partial"] is True


def test_thread_pagination_accounts_for_bad_scope_malformed_and_duplicates(
    monkeypatch,
):
    calls = []
    responses = iter([
        api_result(
            {
                "items": [
                    thread_resource("thread-a", top_comment_id="top-a"),
                    "malformed",
                    thread_resource(
                        "wrong-video",
                        top_comment_id="top-wrong",
                        video_id=OTHER_VIDEO_ID,
                    ),
                ],
                "nextPageToken": "PAGE-2",
                "pageInfo": {"totalResults": 10, "resultsPerPage": 3},
            },
            attempts=2,
        ),
        api_result(
            {
                "items": [
                    thread_resource("thread-a", top_comment_id="duplicate-top"),
                    thread_resource("thread-b", top_comment_id="top-b"),
                ],
                "nextPageToken": "PAGE-3",
                "pageInfo": {"totalResults": 10, "resultsPerPage": 2},
            },
            attempts=3,
        ),
    ])

    def request(url, params, **options):
        calls.append(params)
        return next(responses)

    monkeypatch.setattr(comments, "_request_json", request)
    result = comments.list_video_comment_threads_detailed(
        VIDEO_ID,
        max_pages=2,
        max_results=3,
        page_token="START",
        retries=0,
        now=NOW,
    )

    assert [row["thread_id"] for row in result["comment_threads"]] == [
        "thread-a",
        "thread-b",
    ]
    assert calls[0]["pageToken"] == "START"
    assert calls[0]["maxResults"] == 3
    assert calls[1]["pageToken"] == "PAGE-2"
    assert calls[1]["maxResults"] == 2
    coverage = result["coverage"]
    assert coverage["pages_attempted"] == 2
    assert coverage["pages_fetched"] == 2
    assert coverage["rows_seen"] == 5
    assert coverage["returned"] == 2
    assert coverage["duplicates_skipped"] == 1
    assert coverage["malformed_items_skipped"] == 1
    assert coverage["unexpected_scope_skipped"] == 1
    assert coverage["next_page_token"] == "PAGE-3"
    assert coverage["stopping_reason"] == "page_budget"
    assert coverage["partial"] is True
    assert coverage["api_attempts"] == 5
    assert coverage["api_calls"] == 5
    assert result["api_calls"]["total"] == 5
    assert result["quota"]["estimated_units"] == 5
    assert any("Malformed" in warning for warning in result["warnings"])
    assert any("outside the requested scope" in warning for warning in result["warnings"])
    assert any("Duplicate" in warning for warning in result["warnings"])


def test_reply_scope_filtering_dedupes_without_confusing_top_level_rows(monkeypatch):
    monkeypatch.setattr(
        comments,
        "_request_json",
        lambda *args, **kwargs: api_result({"items": [
            comment_resource(REPLY_ID, parent_id=TOP_COMMENT_ID),
            comment_resource(REPLY_ID, parent_id=TOP_COMMENT_ID),
            comment_resource("wrong", parent_id="different-parent"),
            comment_resource("top-level-without-parent"),
            {"id": "missing-snippet"},
        ]}),
    )

    result = comments.list_comment_replies_detailed(TOP_COMMENT_ID, now=NOW)

    assert [row["comment_id"] for row in result["replies"]] == [REPLY_ID]
    assert result["coverage"]["duplicates_skipped"] == 1
    assert result["coverage"]["unexpected_scope_skipped"] == 2
    assert result["coverage"]["malformed_items_skipped"] == 1
    assert result["coverage"]["partial"] is True


def test_result_budget_retains_exact_continuation_and_caps_request(monkeypatch):
    calls = []

    def request(url, params, **options):
        calls.append(params)
        return api_result({
            "items": [
                thread_resource("thread-a", top_comment_id="top-a"),
                thread_resource("thread-b", top_comment_id="top-b"),
                thread_resource("thread-c", top_comment_id="top-c"),
            ],
            "nextPageToken": "MORE",
        })

    monkeypatch.setattr(comments, "_request_json", request)
    result = comments.list_video_comment_threads_detailed(
        VIDEO_ID, max_pages=5, max_results=2, now=NOW
    )

    assert calls[0]["maxResults"] == 2
    assert len(result["comment_threads"]) == 2
    assert result["coverage"]["rows_seen"] == 3
    assert result["coverage"]["next_page_token"] == "MORE"
    assert result["coverage"]["stopping_reason"] == "result_budget"
    assert result["coverage"]["pages_fetched"] == 1


def test_request_page_size_never_exceeds_youtube_limit(monkeypatch):
    calls = []

    def request(url, params, **options):
        calls.append(params)
        return api_result({"items": []})

    monkeypatch.setattr(comments, "_request_json", request)
    comments.list_video_comment_threads_detailed(
        VIDEO_ID,
        max_results=500,
        now=NOW,
    )

    assert calls[0]["maxResults"] == 100


def test_repeated_token_wins_over_result_budget(monkeypatch):
    monkeypatch.setattr(
        comments,
        "_request_json",
        lambda *args, **kwargs: api_result({
            "items": [thread_resource()],
            "nextPageToken": "START",
        }),
    )

    result = comments.list_video_comment_threads_detailed(
        VIDEO_ID,
        max_pages=2,
        max_results=1,
        page_token="START",
        now=NOW,
    )

    assert result["coverage"]["stopping_reason"] == "partial_failure"
    assert result["coverage"]["partial"] is True
    assert result["coverage"]["next_page_token"] is None
    assert result["errors"][0]["stage"] == "pagination"


@pytest.mark.parametrize(
    "next_token",
    ["START", " padded ", "bad\ntoken", "\u202eTOKEN", 123, GOOGLE_API_KEY],
)
def test_invalid_or_repeated_next_token_stops_without_unsafe_continuation(
    monkeypatch, next_token
):
    monkeypatch.setattr(
        comments,
        "_request_json",
        lambda *args, **kwargs: api_result({
            "items": [thread_resource()],
            "nextPageToken": next_token,
        }),
    )

    result = comments.list_video_comment_threads_detailed(
        VIDEO_ID,
        max_pages=2,
        max_results=5,
        page_token="START",
        now=NOW,
    )

    assert len(result["comment_threads"]) == 1
    assert result["coverage"]["stopping_reason"] == "partial_failure"
    assert result["coverage"]["partial"] is True
    assert result["coverage"]["next_page_token"] is None
    assert result["errors"][0]["stage"] == "pagination"
    assert result["errors"][0]["category"] == "invalid_response"
    assert GOOGLE_API_KEY not in repr(result)


def test_first_malformed_envelope_is_a_typed_failure(monkeypatch):
    monkeypatch.setattr(
        comments,
        "_request_json",
        lambda *args, **kwargs: api_result(
            {"items": "not-an-array"}, attempts=3
        ),
    )

    with pytest.raises(comments.YouTubeAPIError) as caught:
        comments.list_video_comment_threads_detailed(VIDEO_ID, now=NOW)

    error = caught.value
    assert error.category == "invalid_response"
    assert error.reason == "malformedResponse"
    assert error.attempts == 3
    assert error.request is None
    assert error.response is None
    assert_traceback_is_credential_free(error)


def test_later_malformed_envelope_preserves_completed_page(monkeypatch):
    responses = iter([
        api_result({
            "items": [thread_resource()],
            "nextPageToken": "PAGE-2",
        }, attempts=2),
        api_result({"items": None}, attempts=1),
    ])
    monkeypatch.setattr(
        comments, "_request_json", lambda *args, **kwargs: next(responses)
    )

    result = comments.list_video_comment_threads_detailed(
        VIDEO_ID, max_pages=2, max_results=10, now=NOW
    )

    assert [row["thread_id"] for row in result["comment_threads"]] == [THREAD_ID]
    assert result["coverage"]["pages_attempted"] == 2
    assert result["coverage"]["pages_fetched"] == 1
    assert result["coverage"]["api_attempts"] == 3
    assert result["coverage"]["stopping_reason"] == "partial_failure"
    assert result["coverage"]["next_page_token"] == "PAGE-2"
    assert result["errors"][0]["stage"] == "comment-threads"


def test_first_api_failure_is_detached_and_keeps_safe_classification(monkeypatch):
    failure = comments.YouTubeAPIError(
        "leaking message key={}".format(SECRET),
        category="quota",
        reason="quotaExceeded",
        status_code=403,
        retryable=False,
        attempts=3,
    )

    def request(*args, **kwargs):
        raise failure

    monkeypatch.setattr(comments, "_request_json", request)
    with pytest.raises(comments.YouTubeAPIError) as caught:
        comments.list_video_comment_threads_detailed(VIDEO_ID, now=NOW)

    error = caught.value
    assert error is not failure
    assert error.to_dict() == {
        "type": "YouTubeAPIError",
        "category": "quota",
        "reason": "quotaExceeded",
        "status_code": 403,
        "retryable": False,
        "attempts": 3,
        "message": "YouTube API quota error (quotaExceeded, HTTP 403)",
    }
    assert SECRET not in str(error)
    assert error.request is None
    assert error.response is None
    assert_traceback_is_credential_free(error)


def test_later_api_failure_preserves_rows_attempts_and_restart_token(monkeypatch):
    failure = comments.YouTubeAPIError(
        "leaking later-page timeout key={}".format(SECRET),
        category="timeout",
        reason="timeout",
        retryable=True,
        attempts=2,
    )
    outcomes = iter([
        api_result({
            "items": [comment_resource(REPLY_ID, parent_id=TOP_COMMENT_ID)],
            "nextPageToken": "REPLY-PAGE-2",
        }, attempts=1),
        failure,
    ])

    def request(*args, **kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(comments, "_request_json", request)
    result = comments.list_comment_replies_detailed(
        TOP_COMMENT_ID, max_pages=2, max_results=10, now=NOW
    )

    assert [row["comment_id"] for row in result["replies"]] == [REPLY_ID]
    assert result["coverage"]["stopping_reason"] == "partial_failure"
    assert result["coverage"]["next_page_token"] == "REPLY-PAGE-2"
    assert result["coverage"]["api_attempts"] == 3
    assert result["coverage"]["api_calls"] == 3
    assert result["api_calls"] == {
        "comment_threads": 0,
        "comments": 3,
        "total": 3,
    }
    assert result["quota"]["estimated_units"] == 3
    assert result["errors"][0]["stage"] == "comments"
    assert result["errors"][0]["page"] == 2
    assert result["errors"][0]["message"] == (
        "YouTube API timeout error (timeout)"
    )
    assert SECRET not in repr(result)


def test_comments_disabled_is_availability_not_key_failure(monkeypatch):
    session = SimpleNamespace()

    def get(url, **kwargs):
        return FakeResponse({
            "error": {"errors": [{"reason": "commentsDisabled"}]}
        }, status=403)

    session.get = get
    result = comments.list_video_comment_threads_detailed(
        VIDEO_ID, retries=5, session=session, now=NOW
    )

    assert result["comment_threads"] == []
    assert result["availability"] == {
        "status": "disabled",
        "reason": "commentsDisabled",
        "http_status": 403,
    }
    assert result["coverage"]["pages_attempted"] == 1
    assert result["coverage"]["pages_fetched"] == 0
    assert result["coverage"]["stopping_reason"] == "comments_disabled"
    assert result["coverage"]["partial"] is False
    assert result["api_calls"]["total"] == 1
    assert result["errors"] == []
    assert any("disabled" in warning.lower() for warning in result["warnings"])


def test_later_comments_disabled_is_terminal_and_marks_prior_rows_partial(monkeypatch):
    disabled = comments.YouTubeAPIError(
        "safe comments unavailable",
        category="comments_unavailable",
        reason="commentsDisabled",
        status_code=403,
        retryable=False,
        attempts=1,
    )
    outcomes = iter([
        api_result({
            "items": [thread_resource()],
            "nextPageToken": "PAGE-2",
        }),
        disabled,
    ])

    def request(*args, **kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(comments, "_request_json", request)
    result = comments.list_video_comment_threads_detailed(
        VIDEO_ID, max_pages=2, max_results=10, now=NOW
    )

    assert len(result["comment_threads"]) == 1
    assert result["availability"]["status"] == "disabled"
    assert result["coverage"]["partial"] is True
    assert result["coverage"]["stopping_reason"] == "comments_disabled"
    assert result["coverage"]["next_page_token"] is None
    assert result["errors"] == []


def test_comments_disabled_is_not_reclassified_for_reply_endpoint(monkeypatch):
    failure = comments.YouTubeAPIError(
        "safe comments unavailable",
        category="comments_unavailable",
        reason="commentsDisabled",
        status_code=403,
        retryable=False,
        attempts=1,
    )
    monkeypatch.setattr(
        comments,
        "_request_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(comments.YouTubeAPIError) as caught:
        comments.list_comment_replies_detailed(TOP_COMMENT_ID, now=NOW)

    assert caught.value.category == "comments_unavailable"
    assert caught.value.reason == "commentsDisabled"


def test_generic_forbidden_remains_authentication_not_comments_disabled():
    session = SimpleNamespace()

    def get(url, **kwargs):
        return FakeResponse({
            "error": {"errors": [{"reason": "forbidden"}]}
        }, status=403)

    session.get = get
    with pytest.raises(comments.YouTubeAPIError) as caught:
        comments.list_video_comment_threads_detailed(
            VIDEO_ID, retries=0, session=session, now=NOW
        )

    assert caught.value.category == "authentication"
    assert caught.value.reason == "forbidden"
    assert caught.value.status_code == 403


def test_all_comment_data_has_an_exact_thirty_day_refresh_window(monkeypatch):
    reply = comment_resource(REPLY_ID, parent_id=TOP_COMMENT_ID)
    monkeypatch.setattr(
        comments,
        "_request_json",
        lambda *args, **kwargs: api_result({"items": [
            thread_resource(total_reply_count=1, embedded_replies=[reply])
        ]}),
    )

    result = comments.list_video_comment_threads_detailed(
        VIDEO_ID, reply_mode="preview", now=NOW
    )

    assert result["observed_at"] == OBSERVED_AT
    assert result["expires_at"] == EXPIRES_AT
    assert result["request"]["requested_at"] == OBSERVED_AT
    thread = result["comment_threads"][0]
    assert (thread["observed_at"], thread["expires_at"]) == (
        OBSERVED_AT,
        EXPIRES_AT,
    )
    top = thread["top_level_comment"]
    assert (top["observed_at"], top["expires_at"]) == (
        OBSERVED_AT,
        EXPIRES_AT,
    )
    embedded = thread["embedded_replies"][0]
    assert (embedded["observed_at"], embedded["expires_at"]) == (
        OBSERVED_AT,
        EXPIRES_AT,
    )


@pytest.mark.parametrize("entrypoint", ["threads", "replies"])
def test_transport_failure_traceback_drops_session_and_reference_secrets(entrypoint):
    session = SecretSession()
    if entrypoint == "threads":
        function = comments.list_video_comment_threads_detailed
        reference = (
            "https://youtube.com/watch?v={}&key={}&feature=share"
        ).format(VIDEO_ID, SECRET)
    else:
        function = comments.list_comment_replies_detailed
        reference = (
            "https://youtube.com/watch?v={}&lc={}&key={}"
        ).format(VIDEO_ID, TOP_COMMENT_ID, SECRET)

    with pytest.raises(comments.YouTubeAPIError) as caught:
        function(reference, session=session, retries=0, now=NOW)

    error = caught.value
    assert SECRET not in str(error)
    assert error.category == "timeout"
    assert error.reason == "timeout"
    assert error.request is None
    assert error.response is None
    assert_traceback_is_credential_free(error, session)


@pytest.mark.parametrize("entrypoint", ["threads", "replies"])
def test_unexpected_failure_is_generic_and_drops_all_provider_state(
    monkeypatch, entrypoint
):
    monkeypatch.setenv("YOUTUBE_API_KEY", GOOGLE_API_KEY)
    session = UnexpectedSecretSession()
    if entrypoint == "threads":
        function = comments.list_video_comment_threads_detailed
        reference = "https://youtube.com/watch?v={}&key={}".format(
            VIDEO_ID, GOOGLE_API_KEY
        )
    else:
        function = comments.list_comment_replies_detailed
        reference = "https://youtube.com/watch?v={}&lc={}&key={}".format(
            VIDEO_ID, TOP_COMMENT_ID, GOOGLE_API_KEY
        )

    with pytest.raises(comments.YouTubeAPIError) as caught:
        function(reference, session=session, retries=0, now=NOW)

    error = caught.value
    assert error.category == "unknown"
    assert error.reason == "unexpectedException"
    assert error.attempts == 0
    assert GOOGLE_API_KEY not in str(error)
    assert error.request is None
    assert error.response is None
    assert_traceback_is_credential_free(error, session)


def test_unexpected_clock_value_is_generic_and_drops_reference_secret(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", GOOGLE_API_KEY)
    supplied = "https://youtube.com/watch?v={}&key={}".format(
        VIDEO_ID, GOOGLE_API_KEY
    )

    with pytest.raises(comments.YouTubeAPIError) as caught:
        comments.list_video_comment_threads_detailed(
            supplied, now=GOOGLE_API_KEY
        )

    assert caught.value.reason == "unexpectedException"
    assert GOOGLE_API_KEY not in str(caught.value)
    assert_traceback_is_credential_free(caught.value)
