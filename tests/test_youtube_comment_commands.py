"""CLI contracts for transient, bounded YouTube public discussion."""

from copy import deepcopy
import json
from unittest.mock import patch

from click.testing import CliRunner
import pytest

from filmot.cli import cli
from filmot.discovery import preflight_candidates
from filmot.youtube_comments import youtube_comment_url
from filmot.youtube_search import YouTubeAPIError


VIDEO_ID = "AbCdEfGhI12"
OTHER_VIDEO_ID = "ZyXwVuTsR98"
THREAD_ID = "Ugz-thread_ledger_sentinel"
TOP_COMMENT_ID = "Ugx-top.comment_ledger_sentinel"
REPLY_ID = "Ugy-reply_ledger_sentinel"
AUTHOR_ID = "UC-comment-author-ledger-sentinel"
AUTHOR_NAME = "AUTHOR-NAME-NOT-FOR-LEDGER"
COMMENT_TEXT = "COMMENT-TEXT-NOT-FOR-LEDGER"
THREAD_TOKEN = "THREAD-TOKEN-NOT-FOR-LEDGER"
REPLY_TOKEN = "REPLY-TOKEN-NOT-FOR-LEDGER"
SECRET = "CLI-COMMENT-SECRET-MUST-NOT-SURVIVE"
GOOGLE_API_KEY = "AIza" + "A" * 35
OBSERVED_AT = "2026-09-12T14:30:00Z"
EXPIRES_AT = "2026-10-12T14:30:00Z"


def comment_row(
    comment_id=TOP_COMMENT_ID,
    *,
    parent_id=None,
    video_id=VIDEO_ID,
    video_id_source="api",
):
    canonical_url = None
    if video_id is not None:
        canonical_url = (
            "https://www.youtube.com/watch?v={}&lc={}".format(
                video_id, comment_id
            )
        )
    return {
        "comment_id": comment_id,
        "parent_comment_id": parent_id,
        "video_id": video_id,
        "video_id_source": video_id_source,
        "associated_channel_id": "UC-video-owner",
        "author_channel_id": AUTHOR_ID,
        "author_display_name": AUTHOR_NAME,
        "text_display": COMMENT_TEXT,
        "like_count": 0,
        "can_rate": False,
        "viewer_rating": "none",
        "moderation_status": "published",
        "published_at": "2026-09-10T10:00:00Z",
        "updated_at": "2026-09-11T11:00:00Z",
        "canonical_url": canonical_url,
        "observed_at": OBSERVED_AT,
        "expires_at": EXPIRES_AT,
        "provider": "youtube-data-api-v3",
    }


def thread_row(*, reply_mode="none", total_reply_count=2):
    embedded = []
    reply_status = "not_requested"
    replies_complete = None
    if reply_mode == "preview":
        embedded = [
            comment_row(
                REPLY_ID,
                parent_id=TOP_COMMENT_ID,
                video_id_source="thread_context",
            )
        ]
        reply_status = "subset"
        replies_complete = False
    return {
        "thread_id": THREAD_ID,
        "video_id": VIDEO_ID,
        "associated_channel_id": "UC-video-owner",
        "can_reply": False,
        "is_public": True,
        "total_reply_count": total_reply_count,
        "top_level_comment": comment_row(),
        "embedded_replies": embedded,
        "reply_coverage": {
            "mode": (
                "embedded_preview" if reply_mode == "preview" else "not_requested"
            ),
            "returned": len(embedded),
            "reported_total": total_reply_count,
            "status": reply_status,
        },
        "replies_complete": replies_complete,
        "observed_at": OBSERVED_AT,
        "expires_at": EXPIRES_AT,
        "provider": "youtube-data-api-v3",
    }


def coverage(
    *,
    returned=1,
    token=None,
    partial=False,
    stopping_reason=None,
    reported_total=7,
    attempts=1,
):
    if stopping_reason is None:
        stopping_reason = "page_budget" if token else "exhausted"
    return {
        "pages_attempted": 1,
        "pages_fetched": 1,
        "rows_seen": returned,
        "returned": returned,
        "duplicates_skipped": 0,
        "malformed_items_skipped": 0,
        "unexpected_scope_skipped": 0,
        "next_page_token": token,
        "page_info": {
            "reported_total": reported_total,
            "results_per_page": returned,
        },
        "reported_total": reported_total,
        "stopping_reason": stopping_reason,
        "api_attempts": attempts,
        "api_calls": attempts,
        "partial": partial,
        "embedded_replies_seen": 0,
        "embedded_replies_returned": 0,
        "embedded_replies_malformed": 0,
        "embedded_replies_wrong_parent": 0,
        "embedded_replies_duplicates": 0,
    }


def thread_provider(
    *,
    order="time",
    search_terms=None,
    reply_mode="none",
    token=None,
    include_thread=True,
    partial=False,
    disabled=False,
    errors=None,
    max_pages=1,
    max_results=25,
    page_token=None,
    connect_timeout=5.0,
    read_timeout=20.0,
    retries=2,
):
    rows = [thread_row(reply_mode=reply_mode)] if include_thread else []
    returned = len(rows)
    reported_total = 7 if rows else 0
    stopping_reason = None
    if disabled:
        stopping_reason = "comments_disabled"
    elif not rows and not partial:
        stopping_reason = "empty"
    result = {
        "provider": "youtube-data-api-v3",
        "video_id": VIDEO_ID,
        "comment_threads": rows,
        "replies_mode": reply_mode,
        "availability": {
            "status": "disabled" if disabled else "available",
            "reason": "commentsDisabled" if disabled else None,
            "http_status": 403 if disabled else None,
        },
        "request": {
            "video_id": VIDEO_ID,
            "video_url": "https://www.youtube.com/watch?v={}".format(VIDEO_ID),
            "requested_at": OBSERVED_AT,
            "order": order,
            "search_terms": search_terms,
            "reply_mode": reply_mode,
            "parts": ["snippet", "replies"] if reply_mode == "preview" else ["snippet"],
            "text_format": "plainText",
            "max_pages": max_pages,
            "max_results": max_results,
            "page_token": page_token,
            "page_budget": max_pages,
            "result_budget": max_results,
            "timeout": {
                "connect_seconds": connect_timeout,
                "read_seconds": read_timeout,
            },
            "retries": retries,
            "retry_backoff_seconds": 0.5,
        },
        "coverage": coverage(
            returned=returned,
            token=None if disabled else token,
            partial=partial,
            stopping_reason=stopping_reason,
            reported_total=reported_total,
        ),
        "api_calls": {"comment_threads": 1, "comments": 0, "total": 1},
        "quota": {
            "units_per_request": 1,
            "estimated_units": 1,
            "accounting": "attempts_conservative",
        },
        "observed_at": OBSERVED_AT,
        "expires_at": EXPIRES_AT,
        "warnings": [
            "Comments are public discourse, not corroboration or a poll."
        ],
        "errors": list(errors or []),
    }
    if disabled and not partial:
        result["coverage"]["pages_fetched"] = 0
    if reply_mode == "preview" and rows:
        result["coverage"]["embedded_replies_seen"] = 1
        result["coverage"]["embedded_replies_returned"] = 1
    return result


def reply_provider(
    *,
    video_id=None,
    token=None,
    include_reply=True,
    partial=False,
    errors=None,
    max_pages=1,
    max_results=25,
    page_token=None,
    connect_timeout=5.0,
    read_timeout=20.0,
    retries=2,
):
    rows = []
    if include_reply:
        rows = [
            comment_row(
                REPLY_ID,
                parent_id=TOP_COMMENT_ID,
                video_id=video_id,
                video_id_source="request_context" if video_id else None,
            )
        ]
    reported_total = 4 if rows else 0
    return {
        "provider": "youtube-data-api-v3",
        "parent_comment_id": TOP_COMMENT_ID,
        "video_id": video_id,
        "replies": rows,
        "request": {
            "parent_comment_id": TOP_COMMENT_ID,
            "video_id": video_id,
            "video_id_source": "request_context" if video_id else None,
            "requested_at": OBSERVED_AT,
            "parts": ["snippet"],
            "text_format": "plainText",
            "max_pages": max_pages,
            "max_results": max_results,
            "page_token": page_token,
            "page_budget": max_pages,
            "result_budget": max_results,
            "timeout": {
                "connect_seconds": connect_timeout,
                "read_seconds": read_timeout,
            },
            "retries": retries,
            "retry_backoff_seconds": 0.5,
        },
        "coverage": coverage(
            returned=len(rows),
            token=token,
            partial=partial,
            stopping_reason=(
                "partial_failure"
                if partial
                else "page_budget"
                if token
                else "exhausted"
                if rows
                else "empty"
            ),
            reported_total=reported_total,
        ),
        "api_calls": {"comment_threads": 0, "comments": 1, "total": 1},
        "quota": {
            "units_per_request": 1,
            "estimated_units": 1,
            "accounting": "attempts_conservative",
        },
        "observed_at": OBSERVED_AT,
        "expires_at": EXPIRES_AT,
        "warnings": ["Replies are transient public discussion."],
        "errors": list(errors or []),
    }


def invoke_with_provider(command, provider_result, *extra):
    if command == "yt-comments":
        target = "filmot.youtube_comments.list_video_comment_threads_detailed"
        argv = [command, VIDEO_ID, *extra, "--raw"]
    else:
        target = "filmot.youtube_comments.list_comment_replies_detailed"
        argv = [command, TOP_COMMENT_ID, *extra, "--raw"]
    with patch(target, return_value=provider_result), patch(
        "filmot.ledger.log_result"
    ):
        return CliRunner().invoke(cli, argv)


def test_yt_comments_raw_forwards_every_option_and_has_exact_continuation():
    supplied = (
        "https://www.youtube.com/watch?v={}&key={}&feature=share#fragment"
    ).format(VIDEO_ID, SECRET)
    provider_result = thread_provider(
        order="relevance",
        search_terms="open question",
        reply_mode="preview",
        token=THREAD_TOKEN,
        max_pages=3,
        max_results=77,
        page_token="START",
        connect_timeout=2.5,
        read_timeout=9.5,
        retries=1,
    )
    with (
        patch(
            "filmot.youtube_comments.list_video_comment_threads_detailed",
            return_value=provider_result,
        ) as provider,
        patch("filmot.ledger.log_result") as log_result,
        patch("filmot.ledger.log_event") as log_event,
    ):
        result = CliRunner().invoke(
            cli,
            [
                "yt-comments",
                supplied,
                "--order",
                "relevance",
                "--search",
                "  open question  ",
                "--replies",
                "preview",
                "--pages",
                "3",
                "--max-results",
                "77",
                "--page-token",
                "START",
                "--connect-timeout",
                "2.5",
                "--read-timeout",
                "9.5",
                "--retries",
                "1",
                "--raw",
            ],
        )

    assert result.exit_code == 0, result.output
    provider.assert_called_once_with(
        VIDEO_ID,
        search_terms="open question",
        order="relevance",
        reply_mode="preview",
        max_pages=3,
        max_results=77,
        page_token="START",
        timeout=(2.5, 9.5),
        retries=1,
    )
    payload = json.loads(result.output)
    assert SECRET not in result.output
    assert set(payload) == {
        "provider",
        "video_id",
        "comment_threads",
        "replies_mode",
        "availability",
        "request",
        "coverage",
        "api_calls",
        "quota",
        "continuation",
        "observed_at",
        "expires_at",
        "_filmot",
    }
    assert payload["_filmot"] == {
        "schema": "filmot.result/v1",
        "command": "yt-comments",
        "status": "completed",
        "errors": [],
        "warnings": provider_result["warnings"],
    }
    assert payload["continuation"] == {
        "available": True,
        "next_page_token": THREAD_TOKEN,
        "token_kind": "comment_threads",
        "argv": [
            "filmot",
            "yt-comments",
            VIDEO_ID,
            "--page-token",
            THREAD_TOKEN,
            "--pages",
            "3",
            "--max-results",
            "77",
            "--order",
            "relevance",
            "--search",
            "open question",
            "--replies",
            "preview",
            "--connect-timeout",
            "2.5",
            "--read-timeout",
            "9.5",
            "--retries",
            "1",
            "--raw",
        ],
    }
    assert log_result.call_args.args[1].to_raw_dict() == payload
    log_event.assert_not_called()


def test_yt_comments_projects_unknown_nested_provider_fields():
    provider_result = thread_provider(reply_mode="preview")
    provider_result["comment_threads"][0]["reply_coverage"][
        "unknown_provider_field"
    ] = "must-not-cross-boundary"
    result = invoke_with_provider(
        "yt-comments",
        provider_result,
        "--replies",
        "preview",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert set(payload["comment_threads"][0]["reply_coverage"]) == {
        "mode",
        "returned",
        "reported_total",
        "status",
    }
    assert "must-not-cross-boundary" not in result.output


def test_yt_replies_raw_forwards_every_option_and_has_exact_continuation():
    provider_result = reply_provider(
        video_id=VIDEO_ID,
        token=REPLY_TOKEN,
        max_pages=4,
        max_results=88,
        page_token="REPLY-START",
        connect_timeout=3.5,
        read_timeout=10.5,
        retries=0,
    )
    with (
        patch(
            "filmot.youtube_comments.list_comment_replies_detailed",
            return_value=provider_result,
        ) as provider,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(
            cli,
            [
                "yt-replies",
                TOP_COMMENT_ID,
                "--video",
                "https://youtu.be/{}?feature=share".format(VIDEO_ID),
                "--pages",
                "4",
                "-n",
                "88",
                "--page-token",
                "REPLY-START",
                "--connect-timeout",
                "3.5",
                "--read-timeout",
                "10.5",
                "--retries",
                "0",
                "--raw",
            ],
        )

    assert result.exit_code == 0, result.output
    provider.assert_called_once_with(
        TOP_COMMENT_ID,
        video_reference=VIDEO_ID,
        max_pages=4,
        max_results=88,
        page_token="REPLY-START",
        timeout=(3.5, 10.5),
        retries=0,
    )
    payload = json.loads(result.output)
    assert set(payload) == {
        "provider",
        "parent_comment_id",
        "video_id",
        "replies",
        "request",
        "coverage",
        "api_calls",
        "quota",
        "continuation",
        "observed_at",
        "expires_at",
        "_filmot",
    }
    assert payload["_filmot"]["status"] == "completed"
    assert payload["continuation"] == {
        "available": True,
        "next_page_token": REPLY_TOKEN,
        "token_kind": "comments",
        "argv": [
            "filmot",
            "yt-replies",
            TOP_COMMENT_ID,
            "--page-token",
            REPLY_TOKEN,
            "--pages",
            "4",
            "--max-results",
            "88",
            "--video",
            VIDEO_ID,
            "--connect-timeout",
            "3.5",
            "--read-timeout",
            "10.5",
            "--retries",
            "0",
            "--raw",
        ],
    }
    assert log_result.call_args.args[1].to_raw_dict() == payload


@pytest.mark.parametrize(
    "provider_result, expected_status",
    [
        (thread_provider(), "completed"),
        (thread_provider(include_thread=False), "empty"),
        (thread_provider(include_thread=False, disabled=True), "skipped"),
        (
            thread_provider(
                partial=True,
                errors=[{
                    "type": "YouTubeAPIError",
                    "stage": "comment-threads",
                    "message": "A later page failed",
                    "category": "timeout",
                    "page": 2,
                }],
            ),
            "partial",
        ),
        (thread_provider(partial=True, disabled=True), "partial"),
    ],
)
def test_yt_comments_status_matrix(provider_result, expected_status):
    result = invoke_with_provider("yt-comments", provider_result)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["_filmot"]["status"] == expected_status
    if expected_status == "skipped":
        assert payload["availability"]["reason"] == "commentsDisabled"
        assert payload["coverage"]["stopping_reason"] == "comments_disabled"


@pytest.mark.parametrize(
    "provider_result, expected_status",
    [
        (reply_provider(), "completed"),
        (reply_provider(include_reply=False), "empty"),
        (
            reply_provider(
                partial=True,
                errors=[{
                    "type": "YouTubeAPIError",
                    "stage": "comments",
                    "message": "A later reply page failed",
                    "category": "timeout",
                    "page": 2,
                }],
            ),
            "partial",
        ),
    ],
)
def test_yt_replies_status_matrix(provider_result, expected_status):
    result = invoke_with_provider("yt-replies", provider_result)

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["_filmot"]["status"] == expected_status


@pytest.mark.parametrize("command", ["yt-comments", "yt-replies"])
def test_public_discussion_raw_output_is_deliberately_not_pipeline_input(command):
    provider_result = thread_provider() if command == "yt-comments" else reply_provider()
    result = invoke_with_provider(command, provider_result)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert not any(name in payload for name in ("result", "videos", "items"))
    preflight = preflight_candidates(payload)
    assert not preflight.ok
    assert preflight.candidates == ()
    assert preflight.errors[0].code == "candidate_array_missing"


def test_human_threads_distinguish_outer_thread_from_reply_parent():
    provider_result = thread_provider(token=None)
    with patch(
        "filmot.youtube_comments.list_video_comment_threads_detailed",
        return_value=provider_result,
    ), patch("filmot.ledger.log_result"):
        result = CliRunner().invoke(cli, ["yt-comments", VIDEO_ID])

    assert result.exit_code == 0, result.output
    assert "Top-level comment ID: {}".format(TOP_COMMENT_ID) in result.output
    assert "Thread ID: {}".format(THREAD_ID) in result.output
    expected = "filmot yt-replies {} --video {}".format(
        TOP_COMMENT_ID, VIDEO_ID
    )
    assert expected in result.output
    assert "filmot yt-replies {}".format(THREAD_ID) not in result.output


def test_human_reply_drill_down_preserves_named_session():
    provider_result = thread_provider(token=None)
    with patch(
        "filmot.youtube_comments.list_video_comment_threads_detailed",
        return_value=provider_result,
    ), patch("filmot.ledger.log_result"):
        result = CliRunner().invoke(
            cli,
            ["--session", "Public Discussion Audit", "yt-comments", VIDEO_ID],
        )

    assert result.exit_code == 0, result.output
    assert (
        "filmot --session 'Public Discussion Audit' yt-replies {} --video {}".format(
            TOP_COMMENT_ID, VIDEO_ID
        )
        in result.output
    )


@pytest.mark.parametrize(
    "preview_status, reported_total, expected_warning",
    [
        ("unknown", None, "Preview completeness is unknown"),
        ("inconsistent", 0, "Preview and reported reply count disagree"),
    ],
)
def test_human_threads_offer_reply_cursor_for_uncertain_previews(
    preview_status,
    reported_total,
    expected_warning,
):
    provider_result = thread_provider(reply_mode="preview", token=None)
    thread = provider_result["comment_threads"][0]
    thread["total_reply_count"] = reported_total
    thread["reply_coverage"]["reported_total"] = reported_total
    thread["reply_coverage"]["status"] = preview_status
    thread["replies_complete"] = None
    with patch(
        "filmot.youtube_comments.list_video_comment_threads_detailed",
        return_value=provider_result,
    ), patch("filmot.ledger.log_result"):
        result = CliRunner().invoke(
            cli,
            ["yt-comments", VIDEO_ID, "--replies", "preview"],
        )

    assert result.exit_code == 0, result.output
    assert "filmot yt-replies {} --video {}".format(
        TOP_COMMENT_ID,
        VIDEO_ID,
    ) in result.output
    assert expected_warning in result.output


@pytest.mark.parametrize(
    "parent_reference, video_option, expected_video",
    [
        (TOP_COMMENT_ID, [], None),
        (TOP_COMMENT_ID, ["--video", VIDEO_ID], VIDEO_ID),
        (
            "https://www.youtube.com/watch?v={}&lc={}".format(
                VIDEO_ID, TOP_COMMENT_ID
            ),
            [],
            VIDEO_ID,
        ),
    ],
)
def test_yt_replies_optional_video_context_is_exact(
    parent_reference, video_option, expected_video
):
    provider_result = reply_provider(video_id=expected_video, token=None)
    with patch(
        "filmot.youtube_comments.list_comment_replies_detailed",
        return_value=provider_result,
    ) as provider, patch("filmot.ledger.log_result"):
        result = CliRunner().invoke(
            cli,
            ["yt-replies", parent_reference, *video_option, "--raw"],
        )

    assert result.exit_code == 0, result.output
    provider.assert_called_once_with(
        TOP_COMMENT_ID,
        video_reference=expected_video,
        max_pages=1,
        max_results=25,
        page_token=None,
        timeout=(5.0, 20.0),
        retries=2,
    )
    payload = json.loads(result.output)
    assert payload["video_id"] == expected_video
    expected_url = None
    if expected_video is not None:
        expected_url = "https://www.youtube.com/watch?v={}&lc={}".format(
            expected_video, REPLY_ID
        )
    assert payload["replies"][0]["canonical_url"] == expected_url
    assert payload["replies"][0]["video_id_source"] == (
        "request_context" if expected_video else None
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_required",
        "wrong_provider",
        "wrong_video",
        "wrong_reply_mode",
        "request_video",
        "request_order",
        "request_search",
        "request_reply_mode",
        "request_text_format",
        "duplicate_thread",
        "invalid_thread_id",
        "cross_video_thread",
        "invalid_thread_flag",
        "negative_reply_count",
        "top_has_parent",
        "top_cross_video",
        "top_wrong_source",
        "top_wrong_provider",
        "top_noncanonical_url",
        "embedded_wrong_parent",
        "reply_coverage_count",
        "reply_coverage_boolean_count",
        "reply_coverage_mode",
        "reply_subset_complete",
        "reply_all_count_mismatch",
        "reply_preview_not_requested",
        "reply_unknown_complete",
        "reply_inconsistent_count",
        "preview_aggregate_returned",
        "coverage_returned",
        "coverage_partial_type",
        "coverage_blank_reason",
        "coverage_bad_token",
        "coverage_attempts_disagree",
        "coverage_totals_disagree",
        "coverage_page_size",
        "coverage_row_volume",
        "coverage_page_bound",
        "coverage_api_underattempts",
        "api_call_total",
        "api_wrong_endpoint",
        "quota_estimate",
        "quota_boolean_units",
        "boolean_request_control",
        "invalid_retry_backoff",
        "invalid_timestamp_text",
        "invalid_timestamp_window",
        "skips_without_partial",
        "availability_status",
        "availability_reason",
        "available_comments_disabled",
        "disabled_with_token",
        "timestamp_type",
        "warnings_type",
        "errors_type",
    ],
)
def test_yt_comments_rejects_provider_boundary_contradictions(mutation):
    value = thread_provider(reply_mode="preview", token=THREAD_TOKEN)
    row = value["comment_threads"][0]
    top = row["top_level_comment"]
    if mutation == "missing_required":
        value.pop("coverage")
    elif mutation == "wrong_provider":
        value["provider"] = "filmot"
    elif mutation == "wrong_video":
        value["video_id"] = OTHER_VIDEO_ID
    elif mutation == "wrong_reply_mode":
        value["replies_mode"] = "none"
    elif mutation == "request_video":
        value["request"]["video_id"] = OTHER_VIDEO_ID
    elif mutation == "request_order":
        value["request"]["order"] = "relevance"
    elif mutation == "request_search":
        value["request"]["search_terms"] = "invented"
    elif mutation == "request_reply_mode":
        value["request"]["reply_mode"] = "none"
    elif mutation == "request_text_format":
        value["request"]["text_format"] = "html"
    elif mutation == "duplicate_thread":
        value["comment_threads"].append(deepcopy(row))
        value["coverage"]["returned"] = 2
    elif mutation == "invalid_thread_id":
        row["thread_id"] = 7
    elif mutation == "cross_video_thread":
        row["video_id"] = OTHER_VIDEO_ID
    elif mutation == "invalid_thread_flag":
        row["can_reply"] = 1
    elif mutation == "negative_reply_count":
        row["total_reply_count"] = -1
    elif mutation == "top_has_parent":
        top["parent_comment_id"] = "another-parent"
    elif mutation == "top_cross_video":
        top["video_id"] = OTHER_VIDEO_ID
    elif mutation == "top_wrong_source":
        top["video_id_source"] = "thread_context"
    elif mutation == "top_wrong_provider":
        top["provider"] = "filmot"
    elif mutation == "top_noncanonical_url":
        top["canonical_url"] = "https://example.test/comment"
    elif mutation == "embedded_wrong_parent":
        row["embedded_replies"][0]["parent_comment_id"] = "another-parent"
    elif mutation == "reply_coverage_count":
        row["reply_coverage"]["returned"] = 2
    elif mutation == "reply_coverage_boolean_count":
        row["reply_coverage"]["returned"] = True
    elif mutation == "reply_coverage_mode":
        row["reply_coverage"]["mode"] = "full"
    elif mutation == "reply_subset_complete":
        row["replies_complete"] = True
    elif mutation == "reply_all_count_mismatch":
        row["reply_coverage"]["status"] = "all_observed_at_response"
        row["replies_complete"] = True
    elif mutation == "reply_preview_not_requested":
        row["reply_coverage"]["status"] = "not_requested"
        row["replies_complete"] = None
    elif mutation == "reply_unknown_complete":
        row["reply_coverage"]["status"] = "unknown"
        row["replies_complete"] = False
    elif mutation == "reply_inconsistent_count":
        row["reply_coverage"]["status"] = "inconsistent"
        row["replies_complete"] = None
    elif mutation == "preview_aggregate_returned":
        value["coverage"]["embedded_replies_returned"] = 2
    elif mutation == "coverage_returned":
        value["coverage"]["returned"] = 0
    elif mutation == "coverage_partial_type":
        value["coverage"]["partial"] = "false"
    elif mutation == "coverage_blank_reason":
        value["coverage"]["stopping_reason"] = ""
    elif mutation == "coverage_bad_token":
        value["coverage"]["next_page_token"] = "bad\ntoken"
    elif mutation == "coverage_attempts_disagree":
        value["coverage"]["api_calls"] = 2
    elif mutation == "coverage_totals_disagree":
        value["coverage"]["reported_total"] = 99
    elif mutation == "coverage_page_size":
        value["coverage"]["page_info"]["results_per_page"] = 101
    elif mutation == "coverage_row_volume":
        value["coverage"]["rows_seen"] = 101
    elif mutation == "coverage_page_bound":
        value["coverage"]["pages_attempted"] = 2
        value["coverage"]["pages_fetched"] = 2
        value["coverage"]["api_attempts"] = 2
        value["coverage"]["api_calls"] = 2
        value["api_calls"]["comment_threads"] = 2
        value["api_calls"]["total"] = 2
        value["quota"]["estimated_units"] = 2
    elif mutation == "coverage_api_underattempts":
        value["coverage"]["api_attempts"] = 0
        value["coverage"]["api_calls"] = 0
        value["api_calls"]["comment_threads"] = 0
        value["api_calls"]["total"] = 0
        value["quota"]["estimated_units"] = 0
    elif mutation == "api_call_total":
        value["api_calls"]["total"] = 2
    elif mutation == "api_wrong_endpoint":
        value["api_calls"] = {"comment_threads": 0, "comments": 1, "total": 1}
    elif mutation == "quota_estimate":
        value["quota"]["estimated_units"] = 2
    elif mutation == "quota_boolean_units":
        value["quota"]["units_per_request"] = True
    elif mutation == "boolean_request_control":
        value["request"]["max_pages"] = True
    elif mutation == "invalid_retry_backoff":
        value["request"]["retry_backoff_seconds"] = float("nan")
    elif mutation == "invalid_timestamp_text":
        value["observed_at"] = "not-a-time"
    elif mutation == "invalid_timestamp_window":
        value["expires_at"] = "2026-10-13T14:30:00Z"
    elif mutation == "skips_without_partial":
        value["coverage"]["duplicates_skipped"] = 1
    elif mutation == "availability_status":
        value["availability"]["status"] = "unknown"
    elif mutation == "availability_reason":
        value["availability"]["reason"] = "commentsDisabled"
    elif mutation == "available_comments_disabled":
        value["coverage"]["stopping_reason"] = "comments_disabled"
        value["coverage"]["next_page_token"] = None
    elif mutation == "disabled_with_token":
        value["availability"] = {
            "status": "disabled",
            "reason": "commentsDisabled",
            "http_status": 403,
        }
        value["coverage"]["stopping_reason"] = "comments_disabled"
        value["coverage"]["partial"] = True
    elif mutation == "timestamp_type":
        value["observed_at"] = 123
    elif mutation == "warnings_type":
        value["warnings"] = [None]
    elif mutation == "errors_type":
        value["errors"] = ["not-an-object"]

    with (
        patch(
            "filmot.youtube_comments.list_video_comment_threads_detailed",
            return_value=value,
        ),
        patch("filmot.ledger.log_event") as log_event,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(
            cli, ["yt-comments", VIDEO_ID, "--replies", "preview", "--raw"]
        )

    assert result.exit_code == 1, (mutation, result.output, result.exception)
    payload = json.loads(result.output)
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["stage"] == "invalid-response"
    assert log_event.call_args.kwargs["failure_stage"] == "invalid-response"
    log_result.assert_not_called()


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_required",
        "wrong_provider",
        "wrong_parent",
        "wrong_video",
        "request_parent",
        "request_video",
        "request_text_format",
        "invalid_reply_id",
        "wrong_reply_parent",
        "wrong_reply_video",
        "wrong_video_source",
        "wrong_reply_provider",
        "noncanonical_url",
        "duplicate_reply",
        "coverage_returned",
        "coverage_bad_token",
        "coverage_attempts",
        "coverage_page_bound",
        "coverage_api_underattempts",
        "comments_disabled_reason",
        "api_wrong_endpoint",
        "quota_units",
        "quota_boolean_estimate",
        "timestamp_type",
        "warnings_type",
        "errors_type",
    ],
)
def test_yt_replies_rejects_provider_boundary_contradictions(mutation):
    value = reply_provider(video_id=VIDEO_ID, token=REPLY_TOKEN)
    row = value["replies"][0]
    if mutation == "missing_required":
        value.pop("request")
    elif mutation == "wrong_provider":
        value["provider"] = "filmot"
    elif mutation == "wrong_parent":
        value["parent_comment_id"] = "another-parent"
    elif mutation == "wrong_video":
        value["video_id"] = OTHER_VIDEO_ID
    elif mutation == "request_parent":
        value["request"]["parent_comment_id"] = "another-parent"
    elif mutation == "request_video":
        value["request"]["video_id"] = OTHER_VIDEO_ID
    elif mutation == "request_text_format":
        value["request"]["text_format"] = "html"
    elif mutation == "invalid_reply_id":
        row["comment_id"] = 4
    elif mutation == "wrong_reply_parent":
        row["parent_comment_id"] = "another-parent"
    elif mutation == "wrong_reply_video":
        row["video_id"] = OTHER_VIDEO_ID
    elif mutation == "wrong_video_source":
        row["video_id_source"] = "api"
    elif mutation == "wrong_reply_provider":
        row["provider"] = "filmot"
    elif mutation == "noncanonical_url":
        row["canonical_url"] = "https://example.test/comment"
    elif mutation == "duplicate_reply":
        value["replies"].append(deepcopy(row))
        value["coverage"]["returned"] = 2
    elif mutation == "coverage_returned":
        value["coverage"]["returned"] = 0
    elif mutation == "coverage_bad_token":
        value["coverage"]["next_page_token"] = " "
    elif mutation == "coverage_attempts":
        value["coverage"]["api_attempts"] = 2
        value["coverage"]["api_calls"] = 2
    elif mutation == "coverage_page_bound":
        value["coverage"]["pages_attempted"] = 2
        value["coverage"]["pages_fetched"] = 2
        value["coverage"]["api_attempts"] = 2
        value["coverage"]["api_calls"] = 2
        value["api_calls"]["comments"] = 2
        value["api_calls"]["total"] = 2
        value["quota"]["estimated_units"] = 2
    elif mutation == "coverage_api_underattempts":
        value["coverage"]["api_attempts"] = 0
        value["coverage"]["api_calls"] = 0
        value["api_calls"]["comments"] = 0
        value["api_calls"]["total"] = 0
        value["quota"]["estimated_units"] = 0
    elif mutation == "comments_disabled_reason":
        value["coverage"]["stopping_reason"] = "comments_disabled"
        value["coverage"]["next_page_token"] = None
    elif mutation == "api_wrong_endpoint":
        value["api_calls"] = {"comment_threads": 1, "comments": 0, "total": 1}
    elif mutation == "quota_units":
        value["quota"]["units_per_request"] = 100
    elif mutation == "quota_boolean_estimate":
        value["quota"]["estimated_units"] = True
    elif mutation == "timestamp_type":
        value["expires_at"] = []
    elif mutation == "warnings_type":
        value["warnings"] = "warning"
    elif mutation == "errors_type":
        value["errors"] = [None]

    with (
        patch(
            "filmot.youtube_comments.list_comment_replies_detailed",
            return_value=value,
        ),
        patch("filmot.ledger.log_event") as log_event,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(
            cli,
            ["yt-replies", TOP_COMMENT_ID, "--video", VIDEO_ID, "--raw"],
        )

    assert result.exit_code == 1, (mutation, result.output, result.exception)
    payload = json.loads(result.output)
    assert payload["_filmot"]["errors"][0]["stage"] == "invalid-response"
    assert log_event.call_args.kwargs["failure_stage"] == "invalid-response"
    log_result.assert_not_called()


@pytest.mark.parametrize("command", ["yt-comments", "yt-replies"])
def test_comment_commands_reject_provider_rows_beyond_requested_budget(command):
    if command == "yt-comments":
        value = thread_provider(max_results=1)
        second = deepcopy(value["comment_threads"][0])
        second["thread_id"] = "Ugz-second-thread"
        second_top = second["top_level_comment"]
        second_top["comment_id"] = "Ugx-second-top-level"
        second_top["canonical_url"] = youtube_comment_url(
            VIDEO_ID,
            second_top["comment_id"],
        )
        value["comment_threads"].append(second)
        target = (
            "filmot.youtube_comments.list_video_comment_threads_detailed"
        )
        argv = [command, VIDEO_ID, "--max-results", "1", "--raw"]
    else:
        value = reply_provider(max_results=1)
        value["replies"].append(comment_row(
            "Ugy-second-reply",
            parent_id=TOP_COMMENT_ID,
            video_id=None,
            video_id_source=None,
        ))
        target = "filmot.youtube_comments.list_comment_replies_detailed"
        argv = [command, TOP_COMMENT_ID, "--max-results", "1", "--raw"]
    value["coverage"]["returned"] = 2
    value["coverage"]["rows_seen"] = 2

    with patch(target, return_value=value), patch(
        "filmot.ledger.log_event"
    ) as log_event, patch("filmot.ledger.log_result") as log_result:
        result = CliRunner().invoke(cli, argv)

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["_filmot"]["errors"][0]["stage"] == "invalid-response"
    assert log_event.call_args.kwargs["failure_stage"] == "invalid-response"
    log_result.assert_not_called()


def test_human_renderers_neutralize_rich_markup_and_terminal_controls():
    threads = thread_provider(reply_mode="preview", token="[red]NEXT[/red]")
    top = threads["comment_threads"][0]["top_level_comment"]
    top["author_display_name"] = (
        "[bold]literal author[/bold]\x1b[31m\nFAKE-LABEL"
    )
    top["text_display"] = (
        "[red]literal body[/red]\x07after\rline\n"
        "UNTRUSTED-LINE\u202e"
    )
    threads["warnings"] = ["[magenta]literal warning[/magenta]\x1b"]
    with patch(
        "filmot.youtube_comments.list_video_comment_threads_detailed",
        return_value=threads,
    ), patch("filmot.ledger.log_result"):
        thread_result = CliRunner().invoke(
            cli, ["yt-comments", VIDEO_ID, "--replies", "preview"]
        )

    replies = reply_provider(video_id=VIDEO_ID, token="[blue]MORE[/blue]")
    replies["replies"][0]["author_display_name"] = "[u]literal reply[/u]\x1b"
    replies["replies"][0]["text_display"] = "[green]reply body[/green]\x00"
    with patch(
        "filmot.youtube_comments.list_comment_replies_detailed",
        return_value=replies,
    ), patch("filmot.ledger.log_result"):
        reply_result = CliRunner().invoke(
            cli, ["yt-replies", TOP_COMMENT_ID, "--video", VIDEO_ID]
        )

    assert thread_result.exit_code == reply_result.exit_code == 0
    for literal in (
        "[bold]literal author[/bold]",
        "[red]literal body[/red]",
        "[magenta]literal warning[/magenta]",
        "[red]NEXT[/red]",
    ):
        assert literal in thread_result.output
    for literal in (
        "[u]literal reply[/u]",
        "[green]reply body[/green]",
        "[blue]MORE[/blue]",
    ):
        assert literal in reply_result.output
    for output in (thread_result.output, reply_result.output):
        assert "\x1b" not in output
        assert "\x00" not in output
        assert "\x07" not in output
        assert "\u202e" not in output
        assert "�" in output
    assert "\nUNTRUSTED-LINE" not in thread_result.output
    assert "\nFAKE-LABEL" not in thread_result.output
    untrusted_line = next(
        line
        for line in thread_result.output.splitlines()
        if "UNTRUSTED-LINE" in line
    )
    assert untrusted_line.startswith("   ")


@pytest.mark.parametrize(
    "command, argv",
    [
        ("yt-comments", ["not-a-video?key=" + SECRET]),
        ("yt-comments", [GOOGLE_API_KEY]),
        ("yt-comments", [VIDEO_ID, "--page-token", "api_key=" + SECRET]),
        ("yt-comments", [VIDEO_ID, "--page-token", "\u202eTOKEN"]),
        ("yt-comments", [VIDEO_ID, "--search", "   "]),
        ("yt-comments", [VIDEO_ID, "--search", "bad\nsearch"]),
        ("yt-replies", [GOOGLE_API_KEY]),
        ("yt-replies", [TOP_COMMENT_ID, "--page-token", GOOGLE_API_KEY]),
        ("yt-replies", [TOP_COMMENT_ID, "--video", "bad?key=" + SECRET]),
        (
            "yt-replies",
            [
                "https://youtube.com/watch?v={}&lc={}".format(
                    VIDEO_ID, TOP_COMMENT_ID
                ),
                "--video",
                OTHER_VIDEO_ID,
            ],
        ),
    ],
)
def test_invalid_domain_input_is_typed_before_provider_or_ledger(command, argv):
    with (
        patch(
            "filmot.youtube_comments.list_video_comment_threads_detailed"
        ) as thread_api,
        patch("filmot.youtube_comments.list_comment_replies_detailed") as reply_api,
        patch("filmot.ledger.log_event") as log_event,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(cli, [command, *argv, "--raw"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["stage"] == "validate-request"
    assert SECRET not in result.output
    assert GOOGLE_API_KEY not in result.output
    thread_api.assert_not_called()
    reply_api.assert_not_called()
    log_event.assert_not_called()
    log_result.assert_not_called()


@pytest.mark.parametrize(
    "command, identity, options",
    [
        ("yt-comments", VIDEO_ID, ["--pages", "0"]),
        ("yt-comments", VIDEO_ID, ["--max-results", "501"]),
        ("yt-comments", VIDEO_ID, ["--connect-timeout", "0"]),
        ("yt-comments", VIDEO_ID, ["--read-timeout", "0"]),
        ("yt-comments", VIDEO_ID, ["--retries", "6"]),
        ("yt-replies", TOP_COMMENT_ID, ["--pages", "11"]),
        ("yt-replies", TOP_COMMENT_ID, ["--max-results", "0"]),
        ("yt-replies", TOP_COMMENT_ID, ["--connect-timeout", "nan"]),
        ("yt-replies", TOP_COMMENT_ID, ["--retries", "-1"]),
    ],
)
def test_click_bounds_fail_before_provider_or_ledger(command, identity, options):
    with (
        patch(
            "filmot.youtube_comments.list_video_comment_threads_detailed"
        ) as thread_api,
        patch("filmot.youtube_comments.list_comment_replies_detailed") as reply_api,
        patch("filmot.ledger.log_event") as log_event,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(cli, [command, identity, *options, "--raw"])

    assert result.exit_code != 0
    thread_api.assert_not_called()
    reply_api.assert_not_called()
    log_event.assert_not_called()
    log_result.assert_not_called()


@pytest.mark.parametrize(
    "command, error, expected_stage",
    [
        (
            "yt-comments",
            ValueError("Missing YOUTUBE_API_KEY in Filmot configuration"),
            "configuration",
        ),
        (
            "yt-comments",
            YouTubeAPIError(
                "timeout at https://example.test?key=" + SECRET,
                category="timeout",
                reason="timeout",
                retryable=True,
                attempts=2,
            ),
            "request",
        ),
        ("yt-comments", RuntimeError("malformed provider response"), "invalid-response"),
        (
            "yt-replies",
            ValueError("Missing YOUTUBE_API_KEY in Filmot configuration"),
            "configuration",
        ),
        (
            "yt-replies",
            YouTubeAPIError(
                "quota failure at https://example.test?key=" + SECRET,
                category="quota",
                reason="quotaExceeded",
                status_code=403,
                attempts=1,
            ),
            "request",
        ),
        ("yt-replies", TypeError("provider returned a list"), "invalid-response"),
    ],
)
def test_provider_failures_are_one_typed_redacted_result(
    command, error, expected_stage
):
    target = (
        "filmot.youtube_comments.list_video_comment_threads_detailed"
        if command == "yt-comments"
        else "filmot.youtube_comments.list_comment_replies_detailed"
    )
    identity = VIDEO_ID if command == "yt-comments" else TOP_COMMENT_ID
    with (
        patch(target, side_effect=error),
        patch("filmot.ledger.log_event") as log_event,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(cli, [command, identity, "--raw"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["stage"] == expected_stage
    if isinstance(error, YouTubeAPIError):
        endpoint = "comment_threads" if command == "yt-comments" else "comments"
        other_endpoint = "comments" if endpoint == "comment_threads" else "comment_threads"
        assert payload["api_calls"] == {
            endpoint: error.attempts,
            other_endpoint: 0,
            "total": error.attempts,
        }
        assert payload["quota"] == {
            "units_per_request": 1,
            "estimated_units": error.attempts,
            "accounting": "attempts_conservative",
        }
        assert payload["_filmot"]["errors"][0]["details"] == {
            "category": error.category,
            "reason": error.reason,
            "status_code": error.status_code,
            "retryable": error.retryable,
            "attempts": error.attempts,
        }
        assert log_event.call_args.kwargs["api_attempts"] == error.attempts
        assert log_event.call_args.kwargs["quota"]["estimated_units"] == error.attempts
    assert SECRET not in result.output
    assert SECRET not in repr(log_event.call_args)
    assert log_event.call_args.kwargs["failure_stage"] == expected_stage
    assert log_event.call_args.kwargs["transient_result_persisted"] is False
    log_result.assert_not_called()


@pytest.mark.parametrize("command", ["yt-comments", "yt-replies"])
def test_raw_serialization_failure_is_logged_as_the_safe_failed_outcome(command):
    provider_result = thread_provider() if command == "yt-comments" else reply_provider()
    provider_result["errors"] = [{
        "message": "Synthetic provider diagnostic",
        "page": object(),
    }]
    target = (
        "filmot.youtube_comments.list_video_comment_threads_detailed"
        if command == "yt-comments"
        else "filmot.youtube_comments.list_comment_replies_detailed"
    )
    identity = VIDEO_ID if command == "yt-comments" else TOP_COMMENT_ID
    with patch(target, return_value=provider_result), patch(
        "filmot.ledger.log_result"
    ) as log_result:
        result = CliRunner().invoke(cli, [command, identity, "--raw"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["stage"] == "serialize-result"
    outcome = log_result.call_args.args[1]
    assert outcome.to_raw_dict() == payload
    compact = log_result.call_args.kwargs["data"]
    assert compact["failure_stage"] == "serialize-result"
    assert compact["serialization_failed"] is True
    compact_json = json.dumps(compact)
    for forbidden in (
        THREAD_ID,
        TOP_COMMENT_ID,
        REPLY_ID,
        AUTHOR_ID,
        AUTHOR_NAME,
        COMMENT_TEXT,
        THREAD_TOKEN,
        REPLY_TOKEN,
        "canonical_url",
        "continuation",
        "argv",
    ):
        assert forbidden not in compact_json


def test_named_session_routes_compact_events_without_public_comment_payloads(
    tmp_path,
):
    data_dir = tmp_path / "session-data"
    comments_value = thread_provider(
        token=THREAD_TOKEN,
        search_terms="SEARCH-TEXT-NOT-FOR-LEDGER",
    )
    replies_value = reply_provider(video_id=VIDEO_ID, token=REPLY_TOKEN)
    runner = CliRunner()
    env = {"FILMOT_DATA_DIR": str(data_dir)}
    with patch(
        "filmot.youtube_comments.list_video_comment_threads_detailed",
        return_value=comments_value,
    ), patch(
        "filmot.youtube_comments.list_comment_replies_detailed",
        return_value=replies_value,
    ):
        comments_result = runner.invoke(
            cli,
            [
                "--session",
                "Public Discussion Audit",
                "yt-comments",
                VIDEO_ID,
                "--search",
                "SEARCH-TEXT-NOT-FOR-LEDGER",
                "--raw",
            ],
            env=env,
        )
        replies_result = runner.invoke(
            cli,
            [
                "--session",
                "Public Discussion Audit",
                "yt-replies",
                TOP_COMMENT_ID,
                "--video",
                VIDEO_ID,
                "--raw",
            ],
            env=env,
        )

    assert comments_result.exit_code == 0, comments_result.output
    assert replies_result.exit_code == 0, replies_result.output
    comments_payload = json.loads(comments_result.output)
    replies_payload = json.loads(replies_result.output)
    assert comments_payload["continuation"]["argv"][:4] == [
        "filmot",
        "--session",
        "Public Discussion Audit",
        "yt-comments",
    ]
    assert replies_payload["continuation"]["argv"][:4] == [
        "filmot",
        "--session",
        "Public Discussion Audit",
        "yt-replies",
    ]
    path = data_dir / "sessions" / "public-discussion-audit.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [event["kind"] for event in events] == ["yt-comments", "yt-replies"]
    assert [event["command"] for event in events] == ["yt-comments", "yt-replies"]
    assert all(
        event["data"]["session"] == "public-discussion-audit"
        for event in events
    )
    assert all(
        event["data"]["transient_result_persisted"] is False
        for event in events
    )
    assert events[0]["data"]["api_calls"]["comment_threads"] == 1
    assert events[1]["data"]["api_calls"]["comments"] == 1
    assert events[0]["data"]["search_terms_provided"] is True
    assert events[1]["data"]["video_context_provided"] is True
    for event in events:
        assert not any(
            name in event["data"]
            for name in (
                "comment_threads",
                "embedded_replies",
                "replies",
                "coverage",
                "availability",
                "continuation_available",
            )
        )
    durable = "\n".join(json.dumps(event) for event in events)
    for forbidden in (
        THREAD_ID,
        TOP_COMMENT_ID,
        REPLY_ID,
        AUTHOR_ID,
        AUTHOR_NAME,
        COMMENT_TEXT,
        THREAD_TOKEN,
        REPLY_TOKEN,
        "SEARCH-TEXT-NOT-FOR-LEDGER",
        VIDEO_ID,
        "https://www.youtube.com",
        "next_page_token",
        '"continuation"',
        '"argv"',
        '"parent_comment_id"',
        '"embedded_replies"',
        '"replies"',
        '"coverage"',
        '"availability"',
        '"continuation_available"',
    ):
        assert forbidden not in durable


def test_comment_commands_are_registered_to_the_domain_module():
    assert cli.commands["yt-comments"].callback.__module__ == (
        "filmot.commands.youtube_comments"
    )
    assert cli.commands["yt-replies"].callback.__module__ == (
        "filmot.commands.youtube_comments"
    )


def test_comment_command_help_exposes_bounds_and_discussion_semantics():
    runner = CliRunner()
    thread_help = runner.invoke(cli, ["yt-comments", "--help"])
    reply_help = runner.invoke(cli, ["yt-replies", "--help"])

    assert thread_help.exit_code == reply_help.exit_code == 0
    assert "untrusted public discourse" in thread_help.output
    assert "not corroboration or a poll" in thread_help.output
    assert "possibly incomplete" in thread_help.output
    assert "nested top-level comment ID" in reply_help.output
    normalized_reply_help = " ".join(reply_help.output.split())
    assert "not its outer thread ID" in normalized_reply_help
    assert "stopping_reason=exhausted" in reply_help.output
    for output in (thread_help.output, reply_help.output):
        normalized_output = " ".join(output.split())
        for option in (
            "--pages",
            "--max-results",
            "--page-token",
            "--connect-timeout",
            "--read-timeout",
            "--retries",
            "--raw",
        ):
            assert option in output
        assert "default: 1" in normalized_output
        assert "default: 25" in normalized_output
        assert "1<=x<=10" in normalized_output
        assert "1<=x<=500" in normalized_output
    assert "--order" in thread_help.output
    assert "--search" in thread_help.output
    assert "--replies" in thread_help.output
    assert "--video" in reply_help.output
