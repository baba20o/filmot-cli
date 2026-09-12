"""Transient, bounded YouTube public-discussion commands."""

from __future__ import annotations

import math
import re
import shlex
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import click
from rich.markup import escape
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from ..cli_support import (
    command_error as _command_error,
    console,
    emit_raw_result as _emit_raw_result,
    prepare_raw_result as _prepare_raw_result,
    status_context as _status,
    whole_word_summary as _whole_word_summary,
)
from ..schemas import (
    CommandResult,
    ErrorDetail,
    ResultStatus,
    YouTubeCommentRepliesResultData,
    YouTubeCommentThreadsResultData,
)
from ..session_context import continuation_argv_prefix
from ..youtube_comments import (
    DEFAULT_COMMENT_PAGES,
    DEFAULT_COMMENT_RESULTS,
    MAX_COMMENT_PAGES,
    MAX_COMMENT_RESULTS,
    youtube_comment_page_token,
    youtube_comment_reference,
    youtube_comment_url,
    youtube_comment_video_id,
)


DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 20.0
DEFAULT_RETRIES = 2
_TERMINAL_CONTROL_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]")
_UTC_RFC3339_RE = re.compile(
    r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z"
)
_COMMENT_ROW_FIELDS = (
    "comment_id",
    "parent_comment_id",
    "video_id",
    "video_id_source",
    "associated_channel_id",
    "author_channel_id",
    "author_display_name",
    "text_display",
    "like_count",
    "can_rate",
    "viewer_rating",
    "moderation_status",
    "published_at",
    "updated_at",
    "canonical_url",
    "observed_at",
    "expires_at",
    "provider",
)
_THREAD_FIELDS = (
    "thread_id",
    "video_id",
    "associated_channel_id",
    "can_reply",
    "is_public",
    "total_reply_count",
    "top_level_comment",
    "embedded_replies",
    "reply_coverage",
    "replies_complete",
    "observed_at",
    "expires_at",
    "provider",
)
_THREAD_REQUEST_FIELDS = (
    "video_id",
    "video_url",
    "requested_at",
    "order",
    "search_terms",
    "reply_mode",
    "parts",
    "text_format",
    "max_pages",
    "max_results",
    "page_token",
    "page_budget",
    "result_budget",
    "timeout",
    "retries",
    "retry_backoff_seconds",
)
_REPLY_REQUEST_FIELDS = (
    "parent_comment_id",
    "video_id",
    "video_id_source",
    "requested_at",
    "parts",
    "text_format",
    "max_pages",
    "max_results",
    "page_token",
    "page_budget",
    "result_budget",
    "timeout",
    "retries",
    "retry_backoff_seconds",
)
_PREVIEW_COVERAGE_FIELDS = (
    "embedded_replies_seen",
    "embedded_replies_returned",
    "embedded_replies_malformed",
    "embedded_replies_wrong_parent",
    "embedded_replies_duplicates",
)
_BASE_COVERAGE_FIELDS = (
    "pages_attempted",
    "pages_fetched",
    "rows_seen",
    "returned",
    "duplicates_skipped",
    "malformed_items_skipped",
    "unexpected_scope_skipped",
    "next_page_token",
    "page_info",
    "reported_total",
    "stopping_reason",
    "api_attempts",
    "api_calls",
    "partial",
)


def _terminal_text(value: object, fallback: str = "") -> str:
    if value is None or value == "":
        value = fallback
    cleaned = _TERMINAL_CONTROL_RE.sub("�", str(value))
    return "".join(
        "�" if unicodedata.category(character) in {"Cf", "Zl", "Zp"}
        else character
        for character in cleaned
    )


def _single_line_text(value: object, fallback: str = "") -> str:
    return _terminal_text(value, fallback).replace("\n", "�")


def _display(value: object, fallback: str = "") -> str:
    """Escape markup and terminal control text from public comments."""
    return escape(_terminal_text(value, fallback))


def _object(value: object, name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("YouTube comment provider {} must be an object".format(name))
    return dict(value)


def _object_list(value: object, name: str) -> List[Dict[str, Any]]:
    if not isinstance(value, list) or any(
        not isinstance(item, dict) for item in value
    ):
        raise TypeError(
            "YouTube comment provider {} must be a list of objects".format(name)
        )
    return [dict(item) for item in value]


def _string_list(value: object, name: str) -> List[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise TypeError(
            "YouTube comment provider {} must be a list of strings".format(name)
        )
    return [_whole_word_summary(item, 500) for item in value]


def _required_keys(
    result: Dict[str, Any], names: tuple[str, ...], provider_name: str
) -> None:
    missing = [name for name in names if name not in result]
    if missing:
        raise TypeError(
            "{} omitted required field(s): {}".format(
                provider_name, ", ".join(missing)
            )
        )


def _timestamps(result: Dict[str, Any], provider_name: str) -> None:
    parsed: Dict[str, datetime] = {}
    for name in ("observed_at", "expires_at"):
        value = result.get(name)
        if not isinstance(value, str) or not _UTC_RFC3339_RE.fullmatch(value):
            raise TypeError(
                "{} {} must be a UTC RFC3339 timestamp".format(provider_name, name)
            )
        try:
            parsed[name] = datetime.fromisoformat(
                value[:-1] + "+00:00"
            ).astimezone(timezone.utc)
        except ValueError:
            raise TypeError(
                "{} {} must be a valid UTC RFC3339 timestamp".format(
                    provider_name, name
                )
            ) from None
    if parsed["expires_at"] - parsed["observed_at"] != timedelta(days=30):
        raise TypeError(
            "{} observation window must be exactly 30 days".format(provider_name)
        )


def _exact_comment_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = youtube_comment_reference(value)
    return parsed == (value, None)


def _nonnegative_int_or_none(value: object) -> bool:
    return value is None or (
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
    )


def _normalize_comment_row(
    value: object,
    *,
    expected_parent_id: Optional[str],
    expected_video_id: Optional[str],
    expected_video_source: Optional[str],
) -> Dict[str, Any]:
    row = _object(value, "comment row")
    _required_keys(row, _COMMENT_ROW_FIELDS, "YouTube comment provider row")
    comment_id = row.get("comment_id")
    if not _exact_comment_id(comment_id):
        raise TypeError("YouTube comment provider returned an invalid comment ID")
    if row.get("parent_comment_id") != expected_parent_id:
        raise TypeError("YouTube comment provider returned an unexpected parent identity")
    if row.get("video_id") != expected_video_id:
        raise TypeError("YouTube comment provider returned an unexpected video identity")
    if row.get("video_id_source") != expected_video_source:
        raise TypeError("YouTube comment provider returned an unexpected video source")
    for name in (
        "associated_channel_id",
        "author_channel_id",
        "author_display_name",
        "text_display",
        "viewer_rating",
        "moderation_status",
        "published_at",
        "updated_at",
        "canonical_url",
        "observed_at",
        "expires_at",
    ):
        if row.get(name) is not None and not isinstance(row.get(name), str):
            raise TypeError(
                "YouTube comment provider row {} must be text or null".format(name)
            )
    if not _nonnegative_int_or_none(row.get("like_count")):
        raise TypeError("YouTube comment provider like_count must be non-negative or null")
    if row.get("can_rate") is not None and not isinstance(row.get("can_rate"), bool):
        raise TypeError("YouTube comment provider can_rate must be boolean or null")
    if row.get("provider") != "youtube-data-api-v3":
        raise TypeError("YouTube comment row must identify youtube-data-api-v3")
    if not row.get("observed_at") or not row.get("expires_at"):
        raise TypeError("YouTube comment row requires observation and expiry times")
    expected_url = (
        youtube_comment_url(expected_video_id, str(comment_id))
        if expected_video_id is not None
        else None
    )
    if row.get("canonical_url") != expected_url:
        raise TypeError("YouTube comment provider returned a noncanonical comment URL")
    return {name: row.get(name) for name in _COMMENT_ROW_FIELDS}


def _normalize_coverage(
    value: object,
    *,
    expected_returned: int,
    expected_pages: int,
    expected_results: int,
) -> Dict[str, Any]:
    coverage = _object(value, "coverage")
    if not isinstance(coverage.get("partial"), bool):
        raise TypeError("YouTube comment provider coverage.partial must be boolean")
    reason = coverage.get("stopping_reason")
    if not isinstance(reason, str) or not reason.strip():
        raise TypeError(
            "YouTube comment provider stopping_reason must be non-empty text"
        )
    token = coverage.get("next_page_token")
    if token is not None and youtube_comment_page_token(token) != token:
        raise TypeError(
            "YouTube comment provider next_page_token must be a safe opaque token or null"
        )
    for name in (
        "pages_attempted",
        "pages_fetched",
        "rows_seen",
        "returned",
        "duplicates_skipped",
        "malformed_items_skipped",
        "unexpected_scope_skipped",
        "api_attempts",
        "api_calls",
    ):
        if not _nonnegative_int_or_none(coverage.get(name)) or coverage.get(name) is None:
            raise TypeError(
                "YouTube comment provider coverage.{} must be non-negative".format(name)
            )
    if coverage["returned"] != expected_returned:
        raise TypeError("YouTube comment provider returned count contradicts its rows")
    if coverage["api_attempts"] != coverage["api_calls"]:
        raise TypeError("YouTube comment provider API attempt counts contradict")
    if (
        coverage["returned"] > expected_results
        or coverage["pages_attempted"] > expected_pages
        or coverage["pages_fetched"] > coverage["pages_attempted"]
        or coverage["returned"] > coverage["rows_seen"]
        or coverage["api_attempts"] < coverage["pages_attempted"]
    ):
        raise TypeError("YouTube comment provider exceeded the requested bounds")
    reason = coverage["stopping_reason"]
    token = coverage.get("next_page_token")
    if reason not in {
        "exhausted",
        "empty",
        "page_budget",
        "result_budget",
        "partial_failure",
        "comments_disabled",
    }:
        raise TypeError("YouTube comment provider returned an unknown stopping reason")
    if reason in {"exhausted", "empty", "comments_disabled"} and token is not None:
        raise TypeError("YouTube comment provider terminal state has a continuation")
    if reason in {"page_budget", "result_budget"} and token is None:
        raise TypeError("YouTube comment provider budget stop omitted its continuation")
    if reason == "partial_failure" and coverage["partial"] is not True:
        raise TypeError("YouTube comment provider partial failure is not marked partial")
    if any(
        coverage[name]
        for name in (
            "duplicates_skipped",
            "malformed_items_skipped",
            "unexpected_scope_skipped",
        )
    ) and coverage["partial"] is not True:
        raise TypeError("YouTube comment provider skipped rows without marking partial")
    page_info = _object(coverage.get("page_info"), "coverage.page_info")
    for name in ("reported_total", "results_per_page"):
        if not _nonnegative_int_or_none(page_info.get(name)):
            raise TypeError(
                "YouTube comment provider page_info.{} is invalid".format(name)
            )
    if (
        page_info.get("results_per_page") is not None
        and page_info["results_per_page"] > 100
    ):
        raise TypeError("YouTube comment provider page size exceeds the API bound")
    if coverage["rows_seen"] > 100 * coverage["pages_fetched"]:
        raise TypeError("YouTube comment provider row count exceeds fetched pages")
    if coverage.get("reported_total") != page_info.get("reported_total"):
        raise TypeError("YouTube comment provider reported totals contradict")
    projected = {name: coverage.get(name) for name in _BASE_COVERAGE_FIELDS}
    projected["page_info"] = {
        "reported_total": page_info.get("reported_total"),
        "results_per_page": page_info.get("results_per_page"),
    }
    return projected


def _normalize_accounting(
    api_calls_value: object,
    quota_value: object,
    *,
    expected_endpoint: str,
    coverage: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    api_calls = _object(api_calls_value, "api_calls")
    quota = _object(quota_value, "quota")
    for name in ("comment_threads", "comments", "total"):
        if not _nonnegative_int_or_none(api_calls.get(name)) or api_calls.get(name) is None:
            raise TypeError("YouTube comment provider API calls must be non-negative")
    if api_calls["total"] != api_calls["comment_threads"] + api_calls["comments"]:
        raise TypeError("YouTube comment provider API call totals contradict")
    if api_calls[expected_endpoint] != api_calls["total"]:
        raise TypeError("YouTube comment provider attributed calls to the wrong endpoint")
    other = "comments" if expected_endpoint == "comment_threads" else "comment_threads"
    if api_calls[other] != 0 or api_calls["total"] != coverage["api_attempts"]:
        raise TypeError("YouTube comment provider call accounting contradicts coverage")
    units_per_request = quota.get("units_per_request")
    estimated_units = quota.get("estimated_units")
    if (
        not _nonnegative_int_or_none(units_per_request)
        or units_per_request is None
        or not _nonnegative_int_or_none(estimated_units)
        or estimated_units is None
        or units_per_request != 1
        or estimated_units != api_calls["total"]
        or quota.get("accounting") != "attempts_conservative"
    ):
        raise TypeError("YouTube comment provider quota accounting is invalid")
    return (
        {
            "comment_threads": api_calls["comment_threads"],
            "comments": api_calls["comments"],
            "total": api_calls["total"],
        },
        {
            "units_per_request": quota["units_per_request"],
            "estimated_units": quota["estimated_units"],
            "accounting": quota["accounting"],
        },
    )


def _validate_effective_controls(
    request: Dict[str, Any],
    *,
    expected_pages: int,
    expected_results: int,
    expected_page_token: Optional[str],
    expected_connect_timeout: float,
    expected_read_timeout: float,
    expected_retries: int,
) -> Dict[str, Any]:
    timeout = _object(request.get("timeout"), "request.timeout")
    for name in ("max_pages", "page_budget", "max_results", "result_budget", "retries"):
        value = request.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(
                "YouTube comment provider request.{} must be an integer".format(name)
            )
    for name in ("connect_seconds", "read_seconds"):
        value = timeout.get(name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value <= 0
        ):
            raise TypeError(
                "YouTube comment provider request.timeout.{} is invalid".format(name)
            )
    retry_backoff = request.get("retry_backoff_seconds")
    if (
        isinstance(retry_backoff, bool)
        or not isinstance(retry_backoff, (int, float))
        or not math.isfinite(float(retry_backoff))
        or retry_backoff < 0
    ):
        raise TypeError("YouTube comment provider retry backoff is invalid")
    if (
        request.get("max_pages") != expected_pages
        or request.get("page_budget") != expected_pages
        or request.get("max_results") != expected_results
        or request.get("result_budget") != expected_results
        or request.get("page_token") != expected_page_token
        or timeout.get("connect_seconds") != expected_connect_timeout
        or timeout.get("read_seconds") != expected_read_timeout
        or request.get("retries") != expected_retries
    ):
        raise TypeError("YouTube comment provider changed effective request controls")
    projected = dict(request)
    projected["timeout"] = {
        "connect_seconds": timeout.get("connect_seconds"),
        "read_seconds": timeout.get("read_seconds"),
    }
    return projected


def _normalize_thread_result(
    value: object,
    *,
    expected_video_id: str,
    expected_order: str,
    expected_search_terms: Optional[str],
    expected_reply_mode: str,
    expected_pages: int,
    expected_results: int,
    expected_page_token: Optional[str],
    expected_connect_timeout: float,
    expected_read_timeout: float,
    expected_retries: int,
) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("YouTube comment-thread provider returned a non-object result")
    result = dict(value)
    _required_keys(
        result,
        (
            "provider",
            "video_id",
            "comment_threads",
            "replies_mode",
            "availability",
            "request",
            "coverage",
            "api_calls",
            "quota",
            "observed_at",
            "expires_at",
            "warnings",
            "errors",
        ),
        "YouTube comment-thread provider",
    )
    if result.get("provider") != "youtube-data-api-v3":
        raise TypeError("YouTube comment-thread provider must identify youtube-data-api-v3")
    if result.get("video_id") != expected_video_id:
        raise TypeError("YouTube comment-thread provider returned another video")
    if result.get("replies_mode") != expected_reply_mode:
        raise TypeError("YouTube comment-thread provider changed the reply mode")
    request = _object(result.get("request"), "request")
    _required_keys(
        request,
        _THREAD_REQUEST_FIELDS,
        "YouTube comment-thread provider request",
    )
    if (
        request.get("video_id") != expected_video_id
        or request.get("order") != expected_order
        or request.get("search_terms") != expected_search_terms
        or request.get("reply_mode") != expected_reply_mode
        or request.get("text_format") != "plainText"
        or request.get("video_url")
        != "https://www.youtube.com/watch?v={}".format(expected_video_id)
        or request.get("parts")
        != (["snippet", "replies"] if expected_reply_mode == "preview" else ["snippet"])
    ):
        raise TypeError("YouTube comment-thread provider request contradicts the command")
    request = {name: request.get(name) for name in _THREAD_REQUEST_FIELDS}
    request = _validate_effective_controls(
        request,
        expected_pages=expected_pages,
        expected_results=expected_results,
        expected_page_token=expected_page_token,
        expected_connect_timeout=expected_connect_timeout,
        expected_read_timeout=expected_read_timeout,
        expected_retries=expected_retries,
    )

    threads = _object_list(result.get("comment_threads"), "comment_threads")
    normalized_threads: List[Dict[str, Any]] = []
    seen_thread_ids = set()
    seen_comment_ids = set()
    for thread in threads:
        _required_keys(thread, _THREAD_FIELDS, "YouTube comment thread")
        thread_id = thread.get("thread_id")
        if not _exact_comment_id(thread_id) or thread_id in seen_thread_ids:
            raise TypeError(
                "YouTube comment-thread provider returned duplicate or invalid "
                "thread IDs"
            )
        seen_thread_ids.add(thread_id)
        if thread.get("video_id") != expected_video_id:
            raise TypeError("YouTube comment-thread provider returned a cross-video thread")
        if thread.get("provider") != "youtube-data-api-v3":
            raise TypeError("YouTube comment thread must identify youtube-data-api-v3")
        for name in ("associated_channel_id", "observed_at", "expires_at"):
            if thread.get(name) is not None and not isinstance(thread.get(name), str):
                raise TypeError("YouTube comment-thread provider returned invalid metadata")
        for name in ("can_reply", "is_public", "replies_complete"):
            if thread.get(name) is not None and not isinstance(thread.get(name), bool):
                raise TypeError("YouTube comment-thread provider returned an invalid flag")
        total_replies = thread.get("total_reply_count")
        if not _nonnegative_int_or_none(total_replies):
            raise TypeError("YouTube comment-thread provider reply count is invalid")
        top = _normalize_comment_row(
            thread.get("top_level_comment"),
            expected_parent_id=None,
            expected_video_id=expected_video_id,
            expected_video_source="api",
        )
        top_id = top["comment_id"]
        if top_id in seen_comment_ids:
            raise TypeError("YouTube comment-thread provider repeated a top-level comment")
        seen_comment_ids.add(top_id)
        embedded = _object_list(thread.get("embedded_replies"), "embedded_replies")
        normalized_embedded = []
        embedded_ids = set()
        for reply in embedded:
            normalized = _normalize_comment_row(
                reply,
                expected_parent_id=top_id,
                expected_video_id=expected_video_id,
                expected_video_source="thread_context",
            )
            if normalized["comment_id"] in embedded_ids:
                raise TypeError("YouTube comment-thread provider repeated an embedded reply")
            embedded_ids.add(normalized["comment_id"])
            normalized_embedded.append(normalized)
        reply_coverage = _object(thread.get("reply_coverage"), "reply_coverage")
        expected_mode = "embedded_preview" if expected_reply_mode == "preview" else "not_requested"
        if (
            reply_coverage.get("mode") != expected_mode
            or not _nonnegative_int_or_none(reply_coverage.get("returned"))
            or reply_coverage.get("returned") is None
            or not _nonnegative_int_or_none(
                reply_coverage.get("reported_total")
            )
            or reply_coverage.get("returned") != len(normalized_embedded)
            or reply_coverage.get("reported_total") != total_replies
            or reply_coverage.get("status") not in {
                "not_requested",
                "unknown",
                "subset",
                "all_observed_at_response",
                "inconsistent",
            }
        ):
            raise TypeError("YouTube comment-thread provider reply coverage contradicts rows")
        if expected_reply_mode == "none" and (
            normalized_embedded
            or reply_coverage.get("status") != "not_requested"
            or thread.get("replies_complete") is not None
        ):
            raise TypeError("YouTube comment-thread provider invented unrequested replies")
        if expected_reply_mode == "preview":
            status = reply_coverage.get("status")
            returned_replies = len(normalized_embedded)
            completeness = thread.get("replies_complete")
            if status == "not_requested":
                raise TypeError("YouTube comment-thread provider omitted preview coverage")
            if status == "all_observed_at_response" and (
                total_replies is None
                or returned_replies != total_replies
                or completeness is not True
            ):
                raise TypeError("YouTube comment-thread provider completeness contradicts coverage")
            if status == "subset" and (
                total_replies is None
                or returned_replies >= total_replies
                or completeness is not False
            ):
                raise TypeError("YouTube comment-thread provider subset contradicts completeness")
            if status == "inconsistent" and (
                total_replies is None
                or returned_replies <= total_replies
                or completeness is not None
            ):
                raise TypeError("YouTube comment-thread provider inconsistency is contradictory")
            if status == "unknown" and completeness is not None:
                raise TypeError(
                    "YouTube comment-thread provider unknown preview is marked "
                    "complete"
                )
        reply_coverage = {
            name: reply_coverage.get(name)
            for name in ("mode", "returned", "reported_total", "status")
        }
        normalized = dict(thread)
        normalized["top_level_comment"] = top
        normalized["embedded_replies"] = normalized_embedded
        normalized["reply_coverage"] = reply_coverage
        normalized_threads.append({name: normalized.get(name) for name in _THREAD_FIELDS})

    coverage = _normalize_coverage(
        result.get("coverage"),
        expected_returned=len(normalized_threads),
        expected_pages=expected_pages,
        expected_results=expected_results,
    )
    raw_coverage = _object(result.get("coverage"), "coverage")
    for name in _PREVIEW_COVERAGE_FIELDS:
        value = raw_coverage.get(name)
        if not _nonnegative_int_or_none(value) or value is None:
            raise TypeError(
                "YouTube comment provider coverage.{} must be non-negative".format(name)
            )
        coverage[name] = value
    embedded_returned = sum(
        len(thread["embedded_replies"]) for thread in normalized_threads
    )
    if coverage["embedded_replies_returned"] != embedded_returned:
        raise TypeError(
            "YouTube comment provider embedded reply count contradicts its rows"
        )
    classified_rows = sum(
        coverage[name]
        for name in (
            "embedded_replies_returned",
            "embedded_replies_wrong_parent",
            "embedded_replies_duplicates",
        )
    )
    # A malformed container increments the malformed counter without
    # representing a row in ``seen``; malformed row entries increment both.
    if not (
        classified_rows <= coverage["embedded_replies_seen"]
        <= classified_rows + coverage["embedded_replies_malformed"]
    ):
        raise TypeError(
            "YouTube comment provider embedded reply accounting contradicts"
        )
    if any(
        coverage[name]
        for name in (
            "embedded_replies_malformed",
            "embedded_replies_wrong_parent",
            "embedded_replies_duplicates",
        )
    ) and coverage["partial"] is not True:
        raise TypeError(
            "YouTube comment provider skipped preview rows without marking partial"
        )
    availability = _object(result.get("availability"), "availability")
    if availability.get("status") not in {"available", "disabled"}:
        raise TypeError("YouTube comment-thread provider availability is invalid")
    if availability["status"] == "disabled" and (
        availability.get("reason") != "commentsDisabled"
        or availability.get("http_status") != 403
        or coverage.get("stopping_reason") != "comments_disabled"
        or coverage.get("next_page_token") is not None
        or coverage.get("partial") != bool(coverage.get("pages_fetched"))
    ):
        raise TypeError("YouTube comment-thread provider disabled state is contradictory")
    if availability["status"] == "available" and (
        availability.get("reason") is not None
        or availability.get("http_status") is not None
        or coverage.get("stopping_reason") == "comments_disabled"
    ):
        raise TypeError("YouTube comment-thread provider invented an availability reason")
    availability = {
        "status": availability.get("status"),
        "reason": availability.get("reason"),
        "http_status": availability.get("http_status"),
    }
    api_calls, quota = _normalize_accounting(
        result.get("api_calls"),
        result.get("quota"),
        expected_endpoint="comment_threads",
        coverage=coverage,
    )
    result["comment_threads"] = normalized_threads
    result["request"] = request
    result["coverage"] = coverage
    result["availability"] = availability
    result["api_calls"] = api_calls
    result["quota"] = quota
    result["warnings"] = _string_list(result.get("warnings"), "warnings")
    result["errors"] = _object_list(result.get("errors"), "errors")
    _timestamps(result, "YouTube comment-thread provider")
    if request.get("requested_at") != result["observed_at"]:
        raise TypeError("YouTube comment request time contradicts its envelope")
    for thread in normalized_threads:
        if (
            thread.get("observed_at") != result["observed_at"]
            or thread.get("expires_at") != result["expires_at"]
        ):
            raise TypeError("YouTube comment thread timestamps contradict its envelope")
        comments = [thread["top_level_comment"], *thread["embedded_replies"]]
        if any(
            comment.get("observed_at") != result["observed_at"]
            or comment.get("expires_at") != result["expires_at"]
            for comment in comments
        ):
            raise TypeError("YouTube comment timestamps contradict their envelope")
    return result


def _normalize_reply_result(
    value: object,
    *,
    expected_parent_id: str,
    expected_video_id: Optional[str],
    expected_pages: int,
    expected_results: int,
    expected_page_token: Optional[str],
    expected_connect_timeout: float,
    expected_read_timeout: float,
    expected_retries: int,
) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("YouTube reply provider returned a non-object result")
    result = dict(value)
    _required_keys(
        result,
        (
            "provider",
            "parent_comment_id",
            "video_id",
            "replies",
            "request",
            "coverage",
            "api_calls",
            "quota",
            "observed_at",
            "expires_at",
            "warnings",
            "errors",
        ),
        "YouTube reply provider",
    )
    if result.get("provider") != "youtube-data-api-v3":
        raise TypeError("YouTube reply provider must identify youtube-data-api-v3")
    if result.get("parent_comment_id") != expected_parent_id:
        raise TypeError("YouTube reply provider returned another parent comment")
    if result.get("video_id") != expected_video_id:
        raise TypeError("YouTube reply provider returned another video context")
    request = _object(result.get("request"), "request")
    _required_keys(
        request,
        _REPLY_REQUEST_FIELDS,
        "YouTube reply provider request",
    )
    if (
        request.get("parent_comment_id") != expected_parent_id
        or request.get("video_id") != expected_video_id
        or request.get("video_id_source")
        != ("request_context" if expected_video_id else None)
        or request.get("text_format") != "plainText"
        or request.get("parts") != ["snippet"]
    ):
        raise TypeError("YouTube reply provider request contradicts the command")
    request = {name: request.get(name) for name in _REPLY_REQUEST_FIELDS}
    request = _validate_effective_controls(
        request,
        expected_pages=expected_pages,
        expected_results=expected_results,
        expected_page_token=expected_page_token,
        expected_connect_timeout=expected_connect_timeout,
        expected_read_timeout=expected_read_timeout,
        expected_retries=expected_retries,
    )
    replies = _object_list(result.get("replies"), "replies")
    normalized_replies = []
    seen_ids = set()
    expected_source = "request_context" if expected_video_id else None
    for reply in replies:
        normalized = _normalize_comment_row(
            reply,
            expected_parent_id=expected_parent_id,
            expected_video_id=expected_video_id,
            expected_video_source=expected_source,
        )
        if normalized["comment_id"] in seen_ids:
            raise TypeError("YouTube reply provider returned duplicate reply IDs")
        seen_ids.add(normalized["comment_id"])
        normalized_replies.append(normalized)
    coverage = _normalize_coverage(
        result.get("coverage"),
        expected_returned=len(normalized_replies),
        expected_pages=expected_pages,
        expected_results=expected_results,
    )
    if coverage.get("stopping_reason") == "comments_disabled":
        raise TypeError("YouTube reply provider invented a disabled-video state")
    api_calls, quota = _normalize_accounting(
        result.get("api_calls"),
        result.get("quota"),
        expected_endpoint="comments",
        coverage=coverage,
    )
    result["replies"] = normalized_replies
    result["request"] = request
    result["coverage"] = coverage
    result["api_calls"] = api_calls
    result["quota"] = quota
    result["warnings"] = _string_list(result.get("warnings"), "warnings")
    result["errors"] = _object_list(result.get("errors"), "errors")
    _timestamps(result, "YouTube reply provider")
    if request.get("requested_at") != result["observed_at"]:
        raise TypeError("YouTube reply request time contradicts its envelope")
    if any(
        reply.get("observed_at") != result["observed_at"]
        or reply.get("expires_at") != result["expires_at"]
        for reply in normalized_replies
    ):
        raise TypeError("YouTube reply timestamps contradict their envelope")
    return result


def _youtube_errors(value: object) -> List[ErrorDetail]:
    errors = _object_list(value, "errors")
    output = []
    safe_detail_names = {
        "category",
        "reason",
        "status_code",
        "retryable",
        "attempts",
        "page",
    }
    for error in errors:
        output.append(ErrorDetail(
            type=str(error.get("type") or "YouTubeAPIError"),
            message=_whole_word_summary(
                error.get("message") or "YouTube comment request was incomplete",
                500,
            ),
            stage=str(error.get("stage") or "youtube-api"),
            details={name: error[name] for name in safe_detail_names if name in error},
        ))
    return output


def _validated_page_token(
    value: Optional[str], *, command: str, raw: bool
) -> Optional[str]:
    if value is None:
        return None
    normalized = youtube_comment_page_token(value)
    value = ""
    if normalized is None:
        message = "--page-token must be a bounded nonblank YouTube token without control text"
        if raw:
            _command_error(
                message,
                command=command,
                raw=True,
                error_type="BadParameter",
                stage="validate-request",
            )
        raise click.BadParameter(message, param_hint="--page-token")
    return normalized


def _validated_search_terms(value: Optional[str], *, raw: bool) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    invalid = (
        not normalized
        or len(normalized) > 500
        or bool(re.search(r"[\x00-\x1f\x7f-\x9f]", normalized))
    )
    value = ""
    if invalid:
        message = "--search must be 1–500 characters without control text"
        if raw:
            _command_error(
                message,
                command="yt-comments",
                raw=True,
                error_type="BadParameter",
                stage="validate-request",
            )
        raise click.BadParameter(message, param_hint="--search")
    return normalized


def _validated_timeout(
    value: float, *, option: str, command: str, raw: bool
) -> float:
    if math.isfinite(value) and value > 0:
        return value
    message = "{} must be a finite number greater than zero".format(option)
    if raw:
        _command_error(
            message,
            command=command,
            raw=True,
            error_type="BadParameter",
            stage="validate-request",
        )
    raise click.BadParameter(message, param_hint=option)


def _continuation(
    command: str,
    identity: str,
    coverage: Dict[str, Any],
    *,
    pages: int,
    max_results: int,
    connect_timeout: float,
    read_timeout: float,
    retries: int,
    raw: bool,
    order: Optional[str] = None,
    search_terms: Optional[str] = None,
    reply_mode: Optional[str] = None,
    video_id: Optional[str] = None,
) -> Dict[str, Any]:
    token = coverage.get("next_page_token")
    available = isinstance(token, str) and bool(token)
    argv: List[str] = []
    if available:
        argv = [
            *continuation_argv_prefix(command),
            identity,
            "--page-token",
            token,
            "--pages",
            str(pages),
            "--max-results",
            str(max_results),
        ]
        if order is not None and order != "time":
            argv.extend(["--order", order])
        if search_terms is not None:
            argv.extend(["--search", search_terms])
        if reply_mode is not None and reply_mode != "none":
            argv.extend(["--replies", reply_mode])
        if video_id is not None:
            argv.extend(["--video", video_id])
        if connect_timeout != DEFAULT_CONNECT_TIMEOUT:
            argv.extend(["--connect-timeout", str(connect_timeout)])
        if read_timeout != DEFAULT_READ_TIMEOUT:
            argv.extend(["--read-timeout", str(read_timeout)])
        if retries != DEFAULT_RETRIES:
            argv.extend(["--retries", str(retries)])
        if raw:
            argv.append("--raw")
    return {
        "available": available,
        "next_page_token": token if available else None,
        "token_kind": (
            "comment_threads" if command == "yt-comments" else "comments"
        ),
        "argv": argv,
    }


def _render_continuation(continuation: Dict[str, Any], noun: str) -> None:
    if not continuation.get("available"):
        return
    argv = [str(item) for item in continuation.get("argv") or []]
    console.print("\n[yellow]More {} are available. Continue with:[/yellow]".format(noun))
    console.print(Text(shlex.join(argv), style="bold"), soft_wrap=True)


def _render_warnings_and_errors(outcome: CommandResult) -> None:
    for warning in outcome.warnings:
        console.print("[yellow]Warning: {}[/yellow]".format(_display(warning)))
    for error in outcome.errors:
        console.print("[yellow]{}: {}[/yellow]".format(
            _display(error.stage, "YouTube API"),
            _display(error.message),
        ))


def _comment_summary(value: object, limit: int = 700) -> str:
    return _terminal_text(_whole_word_summary(value or "", limit))


def _render_comment_line(comment: Dict[str, Any], *, prefix: str = "") -> None:
    author = _single_line_text(
        comment.get("author_display_name"), "Unknown author"
    )
    likes = comment.get("like_count")
    likes_label = f"{likes:,}" if isinstance(likes, int) else "unknown"
    published = _single_line_text(comment.get("published_at"), "time unknown")
    left_padding = len(prefix)
    console.print(Padding(
        Text(
            "{} · {} like(s) · {}".format(author, likes_label, published),
            style="bold",
        ),
        (0, 0, 0, left_padding),
    ))
    body = _comment_summary(comment.get("text_display"))
    console.print(Padding(
        Text(body or "(empty displayed text)"),
        (0, 0, 0, left_padding),
    ))
    author_id = comment.get("author_channel_id")
    if author_id:
        console.print(Padding(
            Text(
                "Author channel: {}".format(_single_line_text(author_id)),
                style="dim",
            ),
            (0, 0, 0, left_padding),
        ))


def _render_threads(outcome: CommandResult[YouTubeCommentThreadsResultData]) -> None:
    data = outcome.data
    threads = data.get("comment_threads") or []
    coverage = data.get("coverage") or {}
    availability = data.get("availability") or {}
    panel_style = "yellow" if outcome.status_value in {
        ResultStatus.PARTIAL.value,
        ResultStatus.SKIPPED.value,
    } else "blue"
    console.print(Panel(
        "Video: {}\nReturned: {} thread(s) | Reported total: {} | "
        "Pages: {}/{} | API attempts: {} | Stopped: {}".format(
            _display(data.get("video_id")),
            len(threads),
            _display(coverage.get("reported_total"), "unknown"),
            coverage.get("pages_fetched", 0),
            coverage.get("pages_attempted", 0),
            (data.get("api_calls") or {}).get("total", 0),
            _display(coverage.get("stopping_reason"), "not recorded"),
        ),
        title="YouTube Public Comment Threads",
        border_style=panel_style,
    ))
    if availability.get("status") == "disabled":
        console.print("[yellow]YouTube reports comments are disabled for this video.[/yellow]")
    for index, thread in enumerate(threads, 1):
        top = thread.get("top_level_comment") or {}
        total = thread.get("total_reply_count")
        preview = thread.get("embedded_replies") or []
        coverage_label = thread.get("reply_coverage") or {}
        console.print(Text("\n{}. Top-level comment".format(index), style="bold cyan"))
        _render_comment_line(top, prefix="   ")
        console.print(Text(
            "   Top-level comment ID: {}\n   Thread ID: {}\n   Replies: {}{}".format(
                _terminal_text(top.get("comment_id")),
                _terminal_text(thread.get("thread_id")),
                total if isinstance(total, int) else "unknown",
                (
                    " (preview {}/{})".format(
                        len(preview), total if isinstance(total, int) else "?"
                    )
                    if data.get("replies_mode") == "preview"
                    else ""
                ),
            ),
            style="dim",
        ))
        source = top.get("canonical_url")
        if source:
            console.print(Text("   Source: {}".format(source), style="dim"))
        preview_status = coverage_label.get("status")
        if (
            (isinstance(total, int) and total > 0)
            or bool(preview)
            or preview_status in {"subset", "unknown", "inconsistent"}
        ):
            reply_argv = [
                *continuation_argv_prefix("yt-replies"),
                str(top.get("comment_id")),
                "--video",
                str(data.get("video_id")),
            ]
            console.print(Text(
                "   Retrieve replies: {}".format(shlex.join(reply_argv)),
                style="bold",
            ), soft_wrap=True)
        for reply in preview:
            console.print(Text("   ↳ Embedded preview", style="cyan"))
            _render_comment_line(reply, prefix="      ")
        if preview_status == "subset":
            console.print("   [yellow]Preview is incomplete; use the reply command above.[/yellow]")
        elif preview_status == "unknown":
            console.print(
                "   [yellow]Preview completeness is unknown; use the reply "
                "command above.[/yellow]"
            )
        elif preview_status == "inconsistent":
            console.print(
                "   [yellow]Preview and reported reply count disagree; use the "
                "reply command above.[/yellow]"
            )
    if not threads and availability.get("status") != "disabled":
        console.print("[dim]No public comment threads were returned in this slice.[/dim]")
    _render_warnings_and_errors(outcome)
    _render_continuation(data.get("continuation") or {}, "comment threads")


def _render_replies(outcome: CommandResult[YouTubeCommentRepliesResultData]) -> None:
    data = outcome.data
    replies = data.get("replies") or []
    coverage = data.get("coverage") or {}
    console.print(Panel(
        "Returned: {} repl{} | Reported total: {} | Pages: {}/{} | "
        "API attempts: {} | Stopped: {}".format(
            len(replies),
            "y" if len(replies) == 1 else "ies",
            _display(coverage.get("reported_total"), "unknown"),
            coverage.get("pages_fetched", 0),
            coverage.get("pages_attempted", 0),
            (data.get("api_calls") or {}).get("total", 0),
            _display(coverage.get("stopping_reason"), "not recorded"),
        ),
        title="YouTube Public Comment Replies",
        border_style=(
            "yellow" if outcome.status_value == ResultStatus.PARTIAL.value else "blue"
        ),
    ))
    for index, reply in enumerate(replies, 1):
        console.print(Text("\n{}. Reply".format(index), style="bold cyan"))
        _render_comment_line(reply, prefix="   ")
        console.print(Text(
            "   Comment ID: {}".format(
                _terminal_text(reply.get("comment_id"))
            ),
            style="dim",
        ))
        source = reply.get("canonical_url")
        if source:
            console.print(Text("   Source: {}".format(source), style="dim"))
    if not replies:
        console.print("[dim]No public replies were returned in this slice.[/dim]")
    _render_warnings_and_errors(outcome)
    _render_continuation(data.get("continuation") or {}, "replies")


def _bounded_options(function):
    options = (
        click.option(
            "--pages",
            default=DEFAULT_COMMENT_PAGES,
            type=click.IntRange(1, MAX_COMMENT_PAGES),
            show_default=True,
            help="Maximum API pages to fetch",
        ),
        click.option(
            "--max-results",
            "-n",
            default=DEFAULT_COMMENT_RESULTS,
            type=click.IntRange(1, MAX_COMMENT_RESULTS),
            show_default=True,
            help="Thread or reply budget across pages",
        ),
        click.option("--page-token", default=None, help="Resume from the matching opaque YouTube token"),
        click.option(
            "--connect-timeout",
            default=DEFAULT_CONNECT_TIMEOUT,
            type=click.FloatRange(min=0, min_open=True),
            show_default=True,
            help="Per-request connection timeout in seconds",
        ),
        click.option(
            "--read-timeout",
            default=DEFAULT_READ_TIMEOUT,
            type=click.FloatRange(min=0, min_open=True),
            show_default=True,
            help="Per-request response timeout in seconds",
        ),
        click.option(
            "--retries",
            default=DEFAULT_RETRIES,
            type=click.IntRange(0, 5),
            show_default=True,
            help="Retries for transient failures",
        ),
        click.option("--raw", is_flag=True, help="Output one transient versioned JSON result"),
    )
    for option in reversed(options):
        function = option(function)
    return function


def _failure(
    *,
    command: str,
    error: BaseException,
    raw: bool,
    event_data: Dict[str, Any],
    stage: str,
) -> None:
    from ..ledger import log_event
    from ..youtube_search import YouTubeAPIError

    fields = dict(event_data)
    fields.update({
        "status": "failed",
        "failure_stage": stage,
        "error": _whole_word_summary(error, 500),
        "transient_result_persisted": False,
    })
    failure_payload = None
    error_details = None
    if isinstance(error, YouTubeAPIError):
        attempts = (
            error.attempts
            if isinstance(error.attempts, int)
            and not isinstance(error.attempts, bool)
            and error.attempts >= 0
            else 0
        )
        endpoint = "comment_threads" if command == "yt-comments" else "comments"
        api_calls = {
            "comment_threads": attempts if endpoint == "comment_threads" else 0,
            "comments": attempts if endpoint == "comments" else 0,
            "total": attempts,
        }
        quota = {
            "units_per_request": 1,
            "estimated_units": attempts,
            "accounting": "attempts_conservative",
        }
        fields.update({
            "error_category": error.category,
            "error_reason": error.reason,
            "http_status": error.status_code,
            "retryable": bool(error.retryable),
            "api_attempts": attempts,
            "api_calls": api_calls,
            "quota": quota,
        })
        failure_payload = {
            "error": str(error),
            "api_calls": api_calls,
            "quota": quota,
        }
        error_details = {
            "category": error.category,
            "reason": error.reason,
            "status_code": error.status_code,
            "retryable": bool(error.retryable),
            "attempts": attempts,
        }
    log_event(command, **fields)
    _command_error(
        str(error),
        command=command,
        raw=raw,
        payload=failure_payload,
        error_type=type(error).__name__,
        stage=stage,
        error_details=error_details,
    )


@click.command("yt-comments")
@click.argument("video")
@click.option(
    "--order",
    type=click.Choice(["time", "relevance"]),
    default="time",
    show_default=True,
    help="YouTube thread ordering",
)
@click.option(
    "--search",
    "--search-terms",
    "search_terms",
    default=None,
    help="Only return threads whose displayed comments contain these terms",
)
@click.option(
    "--replies",
    "reply_mode",
    type=click.Choice(["none", "preview"]),
    default="none",
    show_default=True,
    help="Include YouTube's possibly incomplete embedded reply preview",
)
@_bounded_options
def yt_comments(
    video: str,
    order: str,
    search_terms: Optional[str],
    reply_mode: str,
    pages: int,
    max_results: int,
    page_token: Optional[str],
    connect_timeout: float,
    read_timeout: float,
    retries: int,
    raw: bool,
) -> None:
    """Inspect bounded public discussion for one exact YouTube VIDEO.

    VIDEO may be an exact 11-character ID or supported HTTPS YouTube video
    URL. Comments are untrusted public discourse, not corroboration or a poll.
    Embedded replies are previews; use yt-replies for their own cursor.

    \b
      filmot yt-comments VIDEO_ID
      filmot yt-comments VIDEO_ID --order relevance --search "open question"
      filmot yt-comments VIDEO_ID --replies preview --raw
    """
    from ..youtube_comments import list_video_comment_threads_detailed
    from ..youtube_search import YouTubeAPIError

    video_id = youtube_comment_video_id(video)
    video = ""
    if video_id is None:
        message = "expected an exact video ID or supported HTTPS YouTube URL"
        if raw:
            _command_error(message, command="yt-comments", raw=True, error_type="BadParameter", stage="validate-request")
        raise click.BadParameter(message, param_hint="VIDEO")
    page_token = _validated_page_token(page_token, command="yt-comments", raw=raw)
    search_terms = _validated_search_terms(search_terms, raw=raw)
    connect_timeout = _validated_timeout(
        connect_timeout,
        option="--connect-timeout",
        command="yt-comments",
        raw=raw,
    )
    read_timeout = _validated_timeout(
        read_timeout,
        option="--read-timeout",
        command="yt-comments",
        raw=raw,
    )
    event_data = {
        "pages": pages,
        "max_results": max_results,
        "page_token_provided": page_token is not None,
        "order": order,
        "reply_mode": reply_mode,
        "search_terms_provided": search_terms is not None,
        "connect_timeout": connect_timeout,
        "read_timeout": read_timeout,
        "retries": retries,
        "raw": raw,
    }
    try:
        with _status("[bold green]Reading public YouTube discussion...", raw=raw):
            provider = list_video_comment_threads_detailed(
                video_id,
                search_terms=search_terms,
                order=order,
                reply_mode=reply_mode,
                max_pages=pages,
                max_results=max_results,
                page_token=page_token,
                timeout=(connect_timeout, read_timeout),
                retries=retries,
            )
        provider = _normalize_thread_result(
            provider,
            expected_video_id=video_id,
            expected_order=order,
            expected_search_terms=search_terms,
            expected_reply_mode=reply_mode,
            expected_pages=pages,
            expected_results=max_results,
            expected_page_token=page_token,
            expected_connect_timeout=connect_timeout,
            expected_read_timeout=read_timeout,
            expected_retries=retries,
        )
    except ValueError as error:
        _failure(command="yt-comments", error=error, raw=raw, event_data=event_data, stage="configuration")
    except YouTubeAPIError as error:
        _failure(command="yt-comments", error=error, raw=raw, event_data=event_data, stage="request")
    except (TypeError, RuntimeError) as error:
        _failure(command="yt-comments", error=error, raw=raw, event_data=event_data, stage="invalid-response")

    coverage = dict(provider["coverage"])
    continuation = _continuation(
        "yt-comments",
        video_id,
        coverage,
        pages=pages,
        max_results=max_results,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        retries=retries,
        raw=raw,
        order=order,
        search_terms=search_terms,
        reply_mode=reply_mode,
    )
    provider_errors = _youtube_errors(provider["errors"])
    threads = list(provider["comment_threads"])
    availability = dict(provider["availability"])
    status = (
        ResultStatus.PARTIAL
        if provider_errors or coverage.get("partial")
        else ResultStatus.SKIPPED
        if availability.get("status") == "disabled"
        else ResultStatus.COMPLETED
        if threads
        else ResultStatus.EMPTY
    )
    data: YouTubeCommentThreadsResultData = {
        "provider": provider["provider"],
        "video_id": video_id,
        "comment_threads": threads,
        "replies_mode": reply_mode,
        "availability": availability,
        "request": dict(provider["request"]),
        "coverage": coverage,
        "api_calls": dict(provider["api_calls"]),
        "quota": dict(provider["quota"]),
        "continuation": continuation,
        "observed_at": provider.get("observed_at"),
        "expires_at": provider.get("expires_at"),
    }
    outcome = CommandResult(
        command="yt-comments",
        status=status,
        data=data,
        errors=provider_errors,
        warnings=list(provider["warnings"]),
    )
    serialization_failed = False
    if raw:
        prepared = _prepare_raw_result(outcome)
        serialization_failed = prepared is not outcome
        outcome = prepared
    compact_data = {
        **event_data,
        "api_calls": data["api_calls"],
        "quota": data["quota"],
        "transient_result_persisted": False,
    }
    if serialization_failed:
        compact_data = {
            **event_data,
            "failure_stage": "serialize-result",
            "serialization_failed": True,
            "transient_result_persisted": False,
        }
    from ..ledger import log_result

    log_result("yt-comments", outcome, data=compact_data)
    if raw:
        _emit_raw_result(outcome, indent=2)
        return
    _render_threads(outcome)


@click.command("yt-replies")
@click.argument("parent_comment")
@click.option(
    "--video",
    "video_reference",
    default=None,
    help="Optional exact video ID/URL for canonical reply source links",
)
@_bounded_options
def yt_replies(
    parent_comment: str,
    video_reference: Optional[str],
    pages: int,
    max_results: int,
    page_token: Optional[str],
    connect_timeout: float,
    read_timeout: float,
    retries: int,
    raw: bool,
) -> None:
    """Retrieve bounded replies to one top-level YouTube comment.

    PARENT_COMMENT is the nested top-level comment ID printed by
    yt-comments, not its outer thread ID. A supported YouTube comment URL
    can supply both comment and video context. Follow continuations until
    stopping_reason=exhausted before calling the bounded cursor complete.

    \b
      filmot yt-replies TOP_LEVEL_COMMENT_ID
      filmot yt-replies TOP_LEVEL_COMMENT_ID --video VIDEO_ID --raw
    """
    from ..youtube_comments import list_comment_replies_detailed
    from ..youtube_search import YouTubeAPIError

    parsed = youtube_comment_reference(parent_comment)
    parent_comment = ""
    if parsed is None:
        message = "expected a top-level comment ID or supported YouTube comment URL"
        if raw:
            _command_error(message, command="yt-replies", raw=True, error_type="BadParameter", stage="validate-request")
        raise click.BadParameter(message, param_hint="PARENT_COMMENT")
    parent_comment_id, linked_video_id = parsed
    explicit_video_id = None
    if video_reference is not None:
        explicit_video_id = youtube_comment_video_id(video_reference)
        video_reference = ""
        if explicit_video_id is None:
            message = "--video must be an exact video ID or supported HTTPS YouTube URL"
            if raw:
                _command_error(message, command="yt-replies", raw=True, error_type="BadParameter", stage="validate-request")
            raise click.BadParameter(message, param_hint="--video")
    if linked_video_id and explicit_video_id and linked_video_id != explicit_video_id:
        message = "--video conflicts with the video in the comment URL"
        if raw:
            _command_error(message, command="yt-replies", raw=True, error_type="BadParameter", stage="validate-request")
        raise click.BadParameter(message, param_hint="--video")
    video_id = explicit_video_id or linked_video_id
    page_token = _validated_page_token(page_token, command="yt-replies", raw=raw)
    connect_timeout = _validated_timeout(
        connect_timeout,
        option="--connect-timeout",
        command="yt-replies",
        raw=raw,
    )
    read_timeout = _validated_timeout(
        read_timeout,
        option="--read-timeout",
        command="yt-replies",
        raw=raw,
    )
    event_data = {
        "video_context_provided": video_id is not None,
        "pages": pages,
        "max_results": max_results,
        "page_token_provided": page_token is not None,
        "connect_timeout": connect_timeout,
        "read_timeout": read_timeout,
        "retries": retries,
        "raw": raw,
    }
    try:
        with _status("[bold green]Reading public YouTube replies...", raw=raw):
            provider = list_comment_replies_detailed(
                parent_comment_id,
                video_reference=video_id,
                max_pages=pages,
                max_results=max_results,
                page_token=page_token,
                timeout=(connect_timeout, read_timeout),
                retries=retries,
            )
        provider = _normalize_reply_result(
            provider,
            expected_parent_id=parent_comment_id,
            expected_video_id=video_id,
            expected_pages=pages,
            expected_results=max_results,
            expected_page_token=page_token,
            expected_connect_timeout=connect_timeout,
            expected_read_timeout=read_timeout,
            expected_retries=retries,
        )
    except ValueError as error:
        _failure(command="yt-replies", error=error, raw=raw, event_data=event_data, stage="configuration")
    except YouTubeAPIError as error:
        _failure(command="yt-replies", error=error, raw=raw, event_data=event_data, stage="request")
    except (TypeError, RuntimeError) as error:
        _failure(command="yt-replies", error=error, raw=raw, event_data=event_data, stage="invalid-response")

    coverage = dict(provider["coverage"])
    continuation = _continuation(
        "yt-replies",
        parent_comment_id,
        coverage,
        pages=pages,
        max_results=max_results,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        retries=retries,
        raw=raw,
        video_id=video_id,
    )
    provider_errors = _youtube_errors(provider["errors"])
    replies = list(provider["replies"])
    status = (
        ResultStatus.PARTIAL
        if provider_errors or coverage.get("partial")
        else ResultStatus.COMPLETED
        if replies
        else ResultStatus.EMPTY
    )
    data: YouTubeCommentRepliesResultData = {
        "provider": provider["provider"],
        "parent_comment_id": parent_comment_id,
        "video_id": video_id,
        "replies": replies,
        "request": dict(provider["request"]),
        "coverage": coverage,
        "api_calls": dict(provider["api_calls"]),
        "quota": dict(provider["quota"]),
        "continuation": continuation,
        "observed_at": provider.get("observed_at"),
        "expires_at": provider.get("expires_at"),
    }
    outcome = CommandResult(
        command="yt-replies",
        status=status,
        data=data,
        errors=provider_errors,
        warnings=list(provider["warnings"]),
    )
    serialization_failed = False
    if raw:
        prepared = _prepare_raw_result(outcome)
        serialization_failed = prepared is not outcome
        outcome = prepared
    compact_data = {
        **event_data,
        "api_calls": data["api_calls"],
        "quota": data["quota"],
        "transient_result_persisted": False,
    }
    if serialization_failed:
        compact_data = {
            **event_data,
            "failure_stage": "serialize-result",
            "serialization_failed": True,
            "transient_result_persisted": False,
        }
    from ..ledger import log_result

    log_result("yt-replies", outcome, data=compact_data)
    if raw:
        _emit_raw_result(outcome, indent=2)
        return
    _render_replies(outcome)


__all__ = ["yt_comments", "yt_replies"]
