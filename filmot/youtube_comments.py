"""Bounded public YouTube comment and reply retrieval.

Comments are mutable user-generated API data, not transcript evidence or a
representative audience sample.  The provider therefore keeps retrieval
exact, transient, independently resumable, and small by default.  It never
fans out from threads into full replies: callers use the separate reply cursor
for that work.
"""

from __future__ import annotations

import re
import time
import unicodedata
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit

from .discovery import youtube_video_id
from .youtube_api_support import (
    _detached_youtube_error,
    _error_row,
    _invalid_response,
    _mapping,
    _merge_page_info,
    _next_token,
    _page_info,
    _request_options,
    _text,
    _unexpected_provider_error,
    _validate_request_controls,
)
from .youtube_search import (
    DEFAULT_RETRY_BACKOFF_SECONDS,
    METADATA_TTL_DAYS,
    YouTubeAPIError,
    _clock_now,
    _format_rfc3339,
    _optional_int,
    _request_json,
    validate_youtube_api,
)


YOUTUBE_COMMENT_THREADS_URL = (
    "https://www.googleapis.com/youtube/v3/commentThreads"
)
YOUTUBE_COMMENTS_URL = "https://www.googleapis.com/youtube/v3/comments"

DEFAULT_COMMENT_PAGES = 1
DEFAULT_COMMENT_RESULTS = 25
MAX_COMMENT_PAGES = 10
MAX_COMMENT_RESULTS = 500
MAX_COMMENT_RETRIES = 5
MAX_COMMENT_ID_CHARS = 512
MAX_COMMENT_PAGE_TOKEN_CHARS = 4096
MAX_COMMENT_SEARCH_CHARS = 500

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
_GOOGLE_API_KEY_RE = re.compile(r"^AIza[A-Za-z0-9_-]{35}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|secret|password)\s*="
)

_SAFE_VALUE_ERROR_MESSAGES = frozenset({
    "max_pages must be a positive integer",
    "max_results must be a positive integer",
    "max_pages cannot exceed 10",
    "max_results cannot exceed 500",
    "page_token must be non-empty text when provided",
    "page_token is too long or contains unsupported control text",
    "timeout and retry_backoff values must be numeric",
    "timeouts must be finite and greater than zero",
    "retry_backoff must be finite and non-negative",
    "retries must be a non-negative integer",
    "retries cannot exceed 5",
    "timeout must be seconds or a (connect, read) pair",
    "video_reference must be an exact video ID or supported HTTPS YouTube URL",
    "parent_comment_reference must be a comment ID or supported YouTube comment URL",
    "video_reference conflicts with the video in parent_comment_reference",
    "order must be 'time' or 'relevance'",
    "reply_mode must be 'none' or 'preview'",
    "search_terms must be non-empty text when provided",
    "search_terms is too long or contains unsupported control text",
    (
        "Missing YOUTUBE_API_KEY in Filmot configuration. Get one from "
        "https://console.cloud.google.com/apis/credentials"
    ),
})


def _safe_public_value_error(error: ValueError) -> str:
    message = str(error)
    if message in _SAFE_VALUE_ERROR_MESSAGES:
        return message
    return "Invalid YouTube comment request"


def _opaque_identifier(value: Any, *, strip: bool = True) -> Optional[str]:
    """Validate an opaque bounded identifier without inventing a grammar."""
    if not isinstance(value, str):
        return None
    candidate = value.strip() if strip else value
    if (
        not candidate
        or len(candidate) > MAX_COMMENT_ID_CHARS
        or _CONTROL_RE.search(candidate)
        or any(
            unicodedata.category(character) in {"Cf", "Cs", "Zl", "Zp"}
            for character in candidate
        )
        or _GOOGLE_API_KEY_RE.fullmatch(candidate)
        or _CREDENTIAL_ASSIGNMENT_RE.search(candidate)
    ):
        return None
    if not strip and candidate != candidate.strip():
        return None
    return candidate


def youtube_comment_page_token(value: Any) -> Optional[str]:
    """Return a safe opaque continuation token, or ``None`` when invalid."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > MAX_COMMENT_PAGE_TOKEN_CHARS
        or _CONTROL_RE.search(candidate)
        or any(
            unicodedata.category(character) in {"Cf", "Cs", "Zl", "Zp"}
            for character in candidate
        )
        or _GOOGLE_API_KEY_RE.fullmatch(candidate)
        or _CREDENTIAL_ASSIGNMENT_RE.search(candidate)
    ):
        return None
    return candidate


def _strict_video_id(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if youtube_video_id(candidate) == candidate:
        return candidate
    try:
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").casefold()
        has_port = parsed.port is not None
    except (UnicodeError, ValueError):
        return None
    if (
        parsed.scheme != "https"
        or host not in _YOUTUBE_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or has_port
    ):
        return None
    if parsed.path == "/watch":
        video_values = [
            item for name, item in parse_qsl(parsed.query, keep_blank_values=True)
            if name == "v"
        ]
        if len(video_values) != 1:
            return None
    return youtube_video_id(candidate)


def youtube_comment_video_id(value: Any) -> Optional[str]:
    """Return an exact ID from a bare ID or strict HTTPS YouTube video URL."""
    return _strict_video_id(value)


def youtube_comment_reference(
    value: Any,
) -> Optional[Tuple[str, Optional[str]]]:
    """Return ``(comment_id, video_id)`` from an ID or exact comment URL."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    bare = _opaque_identifier(candidate)
    # A URL is never treated as an opaque bare ID even if it passes the broad
    # identifier safety checks.
    if bare is not None and "://" not in candidate:
        return bare, None

    try:
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").casefold()
        has_port = parsed.port is not None
    except (UnicodeError, ValueError):
        return None
    if (
        parsed.scheme != "https"
        or host not in _YOUTUBE_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or has_port
    ):
        return None
    query = parse_qsl(parsed.query, keep_blank_values=True)
    comment_values = [item for name, item in query if name == "lc"]
    if len(comment_values) != 1:
        return None
    comment_id = _opaque_identifier(comment_values[0])
    video_id = _strict_video_id(candidate)
    if comment_id is None or video_id is None:
        return None
    return comment_id, video_id


def youtube_comment_id(value: Any) -> Optional[str]:
    parsed = youtube_comment_reference(value)
    return parsed[0] if parsed is not None else None


def youtube_comment_url(video_id: str, comment_id: str) -> str:
    return "https://www.youtube.com/watch?{}".format(urlencode({
        "v": video_id,
        "lc": comment_id,
    }))


def _comment_resource(
    item: Any,
    *,
    video_id: Optional[str],
    video_id_source: Optional[str],
    observed_at: str,
    expires_at: str,
) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    comment_id = _opaque_identifier(item.get("id"), strip=False)
    snippet = _mapping(item.get("snippet"))
    if comment_id is None or not snippet:
        return None
    returned_video_id = snippet.get("videoId")
    if returned_video_id is not None:
        if (
            not isinstance(returned_video_id, str)
            or _strict_video_id(returned_video_id) != returned_video_id
            or (video_id is not None and returned_video_id != video_id)
        ):
            return None
    author = _mapping(snippet.get("authorChannelId"))
    author_channel_id = _text(author.get("value"))
    if author_channel_id == "":
        author_channel_id = None
    parent_id = _text(snippet.get("parentId"))
    if parent_id is not None:
        parent_id = _opaque_identifier(parent_id, strip=False)
        if parent_id is None:
            return None
    associated_channel_id = _text(snippet.get("channelId"))
    if associated_channel_id == "":
        associated_channel_id = None
    author_display_name = _text(snippet.get("authorDisplayName"))
    if author_display_name == "":
        author_display_name = None
    return {
        "comment_id": comment_id,
        "parent_comment_id": parent_id,
        "video_id": video_id,
        "video_id_source": video_id_source,
        "associated_channel_id": associated_channel_id,
        "author_channel_id": author_channel_id,
        "author_display_name": author_display_name,
        "text_display": _text(snippet.get("textDisplay")),
        "like_count": _optional_int(snippet.get("likeCount")),
        "can_rate": (
            snippet.get("canRate")
            if isinstance(snippet.get("canRate"), bool)
            else None
        ),
        "viewer_rating": _text(snippet.get("viewerRating")),
        "moderation_status": _text(snippet.get("moderationStatus")),
        "published_at": _text(snippet.get("publishedAt")),
        "updated_at": _text(snippet.get("updatedAt")),
        "canonical_url": (
            youtube_comment_url(video_id, comment_id) if video_id else None
        ),
        "observed_at": observed_at,
        "expires_at": expires_at,
        "provider": "youtube-data-api-v3",
    }


def _comment_thread_resource(
    item: Any,
    *,
    expected_video_id: str,
    reply_mode: str,
    observed_at: str,
    expires_at: str,
) -> Tuple[Optional[Dict[str, Any]], str, Dict[str, int]]:
    counts = {
        "embedded_replies_seen": 0,
        "embedded_replies_returned": 0,
        "embedded_replies_malformed": 0,
        "embedded_replies_wrong_parent": 0,
        "embedded_replies_duplicates": 0,
    }
    if not isinstance(item, dict):
        return None, "malformed", counts
    thread_id = _opaque_identifier(item.get("id"), strip=False)
    snippet = _mapping(item.get("snippet"))
    if thread_id is None or not snippet:
        return None, "malformed", counts
    returned_video_id = _text(snippet.get("videoId"))
    if returned_video_id != expected_video_id:
        return None, "unexpected_scope", counts
    top = _comment_resource(
        snippet.get("topLevelComment"),
        video_id=expected_video_id,
        video_id_source="api",
        observed_at=observed_at,
        expires_at=expires_at,
    )
    if top is None or top.get("parent_comment_id") is not None:
        return None, "malformed", counts
    total_reply_count = _optional_int(snippet.get("totalReplyCount"))
    embedded_replies: List[Dict[str, Any]] = []
    reply_status = "not_requested"
    replies_complete: Optional[bool] = None

    if reply_mode == "preview":
        raw_container = item.get("replies")
        if raw_container is not None and not isinstance(raw_container, dict):
            counts["embedded_replies_malformed"] += 1
        raw_replies = _mapping(raw_container).get("comments", [])
        if not isinstance(raw_replies, list):
            raw_replies = []
            counts["embedded_replies_malformed"] += 1
        seen_reply_ids = set()
        for raw_reply in raw_replies:
            counts["embedded_replies_seen"] += 1
            parsed = _comment_resource(
                raw_reply,
                video_id=expected_video_id,
                video_id_source="thread_context",
                observed_at=observed_at,
                expires_at=expires_at,
            )
            if parsed is None:
                counts["embedded_replies_malformed"] += 1
                continue
            if parsed.get("parent_comment_id") != top["comment_id"]:
                counts["embedded_replies_wrong_parent"] += 1
                continue
            reply_id = parsed["comment_id"]
            if reply_id in seen_reply_ids:
                counts["embedded_replies_duplicates"] += 1
                continue
            seen_reply_ids.add(reply_id)
            embedded_replies.append(parsed)
        counts["embedded_replies_returned"] = len(embedded_replies)
        invalid_preview_rows = sum(
            counts[name]
            for name in (
                "embedded_replies_malformed",
                "embedded_replies_wrong_parent",
                "embedded_replies_duplicates",
            )
        )
        if total_reply_count is None or invalid_preview_rows:
            reply_status = "unknown"
        elif len(embedded_replies) == total_reply_count:
            reply_status = "all_observed_at_response"
            replies_complete = True
        elif len(embedded_replies) < total_reply_count:
            reply_status = "subset"
            replies_complete = False
        else:
            reply_status = "inconsistent"

    return {
        "thread_id": thread_id,
        "video_id": expected_video_id,
        "associated_channel_id": _text(snippet.get("channelId")),
        "can_reply": (
            snippet.get("canReply")
            if isinstance(snippet.get("canReply"), bool)
            else None
        ),
        "is_public": (
            snippet.get("isPublic")
            if isinstance(snippet.get("isPublic"), bool)
            else None
        ),
        "total_reply_count": total_reply_count,
        "top_level_comment": top,
        "embedded_replies": embedded_replies,
        "reply_coverage": {
            "mode": "embedded_preview" if reply_mode == "preview" else "not_requested",
            "returned": len(embedded_replies),
            "reported_total": total_reply_count,
            "status": reply_status,
        },
        "replies_complete": replies_complete,
        "observed_at": observed_at,
        "expires_at": expires_at,
        "provider": "youtube-data-api-v3",
    }, "ok", counts


def _bounded_controls(
    *,
    max_pages: int,
    max_results: int,
    page_token: Optional[str],
    timeout: Optional[Any],
    retries: Optional[int],
    retry_backoff: float,
) -> Tuple[int, int, Optional[str], float, float, int, float]:
    if isinstance(max_pages, int) and not isinstance(max_pages, bool):
        if max_pages > MAX_COMMENT_PAGES:
            raise ValueError("max_pages cannot exceed 10")
    if isinstance(max_results, int) and not isinstance(max_results, bool):
        if max_results > MAX_COMMENT_RESULTS:
            raise ValueError("max_results cannot exceed 500")
    if isinstance(retries, int) and not isinstance(retries, bool):
        if retries > MAX_COMMENT_RETRIES:
            raise ValueError("retries cannot exceed 5")
    if page_token is not None:
        safe_token = youtube_comment_page_token(page_token)
        if safe_token is None:
            if isinstance(page_token, str) and not page_token.strip():
                raise ValueError("page_token must be non-empty text when provided")
            raise ValueError(
                "page_token is too long or contains unsupported control text"
            )
        page_token = safe_token
    return _validate_request_controls(
        max_pages=max_pages,
        max_results=max_results,
        page_token=page_token,
        timeout=timeout,
        retries=retries,
        retry_backoff=retry_backoff,
    )


def _base_coverage() -> Dict[str, Any]:
    return {
        "pages_attempted": 0,
        "pages_fetched": 0,
        "rows_seen": 0,
        "returned": 0,
        "duplicates_skipped": 0,
        "malformed_items_skipped": 0,
        "unexpected_scope_skipped": 0,
        "next_page_token": None,
        "page_info": {
            "reported_total": None,
            "results_per_page": None,
        },
        "reported_total": None,
        "stopping_reason": "page_budget",
        "api_attempts": 0,
        "api_calls": 0,
        "partial": False,
    }


def _comment_pages(
    *,
    endpoint: str,
    stage: str,
    base_params: Dict[str, Any],
    max_pages: int,
    max_results: int,
    page_token: Optional[str],
    request_options: Dict[str, Any],
    parse_item: Callable[[Any], Tuple[Optional[Dict[str, Any]], str]],
    allow_comments_disabled: bool,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    seen_ids = set()
    seen_tokens = set()
    current_token = page_token
    continuation = page_token
    coverage = _base_coverage()
    errors: List[Dict[str, Any]] = []
    availability = {
        "status": "available",
        "reason": None,
        "http_status": None,
    }
    page_info = coverage["page_info"]

    for page in range(1, max_pages + 1):
        coverage["pages_attempted"] += 1
        if current_token:
            seen_tokens.add(current_token)
        params = {
            **base_params,
            "maxResults": min(100, max_results - len(rows)),
            "pageToken": current_token,
        }
        try:
            result = _request_json(endpoint, params, **request_options)
        except YouTubeAPIError as error:
            coverage["api_attempts"] += error.attempts
            coverage["api_calls"] = coverage["api_attempts"]
            if (
                allow_comments_disabled
                and str(error.reason).casefold() == "commentsdisabled"
                and error.status_code == 403
            ):
                availability = {
                    "status": "disabled",
                    "reason": "commentsDisabled",
                    "http_status": error.status_code,
                }
                coverage["stopping_reason"] = "comments_disabled"
                continuation = None
                if coverage["pages_fetched"]:
                    coverage["partial"] = True
                break
            if not coverage["pages_fetched"]:
                raise
            errors.append(_error_row(error, stage=stage, page=page))
            coverage["stopping_reason"] = "partial_failure"
            coverage["partial"] = True
            continuation = current_token
            break

        coverage["api_attempts"] += result.attempts
        coverage["api_calls"] = coverage["api_attempts"]
        items = result.data.get("items")
        if not isinstance(items, list):
            error = _invalid_response(
                endpoint,
                "{}.items is not an array".format(stage),
                attempts=result.attempts,
            )
            if not coverage["pages_fetched"]:
                raise error
            errors.append(_error_row(error, stage=stage, page=page))
            coverage["stopping_reason"] = "partial_failure"
            coverage["partial"] = True
            continuation = current_token
            break

        coverage["pages_fetched"] += 1
        upstream_info = _page_info(result.data)
        _merge_page_info(page_info, {
            "reported_total": upstream_info.get("approximate_total_results"),
            "results_per_page": upstream_info.get("results_per_page"),
        })
        coverage["rows_seen"] += len(items)
        for item in items:
            parsed, disposition = parse_item(item)
            if parsed is None:
                if disposition == "duplicate":
                    coverage["duplicates_skipped"] += 1
                elif disposition == "unexpected_scope":
                    coverage["unexpected_scope_skipped"] += 1
                else:
                    coverage["malformed_items_skipped"] += 1
                coverage["partial"] = True
                continue
            identity = parsed.get("thread_id") or parsed.get("comment_id")
            if not isinstance(identity, str):
                coverage["malformed_items_skipped"] += 1
                coverage["partial"] = True
                continue
            if identity in seen_ids:
                coverage["duplicates_skipped"] += 1
                coverage["partial"] = True
                continue
            seen_ids.add(identity)
            rows.append(parsed)
            if len(rows) >= max_results:
                break

        token, token_valid = _next_token(result.data)
        if not token_valid or (
            token is not None and youtube_comment_page_token(token) != token
        ):
            error = _invalid_response(
                endpoint,
                "{}.nextPageToken is invalid".format(stage),
                attempts=result.attempts,
            )
            errors.append(_error_row(error, stage="pagination", page=page))
            coverage["stopping_reason"] = "partial_failure"
            coverage["partial"] = True
            continuation = None
            break
        continuation = token
        if token is None:
            coverage["stopping_reason"] = "empty" if (
                coverage["pages_fetched"] == 1
                and not rows
                and not coverage["partial"]
            ) else "exhausted"
            break
        if token in seen_tokens:
            error = _invalid_response(
                endpoint,
                "{} pagination repeated a token".format(stage),
                attempts=result.attempts,
            )
            errors.append(_error_row(error, stage="pagination", page=page))
            coverage["stopping_reason"] = "partial_failure"
            coverage["partial"] = True
            continuation = None
            break
        if len(rows) >= max_results:
            coverage["stopping_reason"] = "result_budget"
            break
        current_token = token
        if page >= max_pages:
            coverage["stopping_reason"] = "page_budget"
            break

    coverage["returned"] = len(rows)
    coverage["next_page_token"] = continuation
    coverage["reported_total"] = page_info["reported_total"]
    warnings = [
        "Comments are public audience discourse, not corroboration or a representative sample.",
        (
            "text_display is YouTube's displayed plain text and may differ "
            "from the author's original input."
        ),
    ]
    if availability["status"] == "disabled":
        warnings.append("YouTube reports that comments are disabled for this video.")
    if errors:
        warnings.append(
            "Comment retrieval was incomplete; usable completed rows were preserved."
        )
    if coverage["malformed_items_skipped"]:
        warnings.append("Malformed comment resources were skipped.")
    if coverage["unexpected_scope_skipped"]:
        warnings.append("Comment resources outside the requested scope were skipped.")
    if coverage["duplicates_skipped"]:
        warnings.append("Duplicate comment resources were skipped.")
    return {
        "rows": rows,
        "coverage": coverage,
        "availability": availability,
        "warnings": warnings,
        "errors": errors,
    }


def _list_video_comment_threads_detailed(
    video_reference: str,
    *,
    search_terms: Optional[str] = None,
    order: str = "time",
    reply_mode: str = "none",
    max_pages: int = DEFAULT_COMMENT_PAGES,
    max_results: int = DEFAULT_COMMENT_RESULTS,
    page_token: Optional[str] = None,
    timeout: Optional[Any] = None,
    retries: Optional[int] = None,
    session: Optional[Any] = None,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    now: Optional[Any] = None,
) -> Dict[str, Any]:
    (
        max_pages,
        max_results,
        page_token,
        connect_timeout,
        read_timeout,
        retries,
        retry_backoff,
    ) = _bounded_controls(
        max_pages=max_pages,
        max_results=max_results,
        page_token=page_token,
        timeout=timeout,
        retries=retries,
        retry_backoff=retry_backoff,
    )
    video_id = _strict_video_id(video_reference)
    if video_id is None:
        raise ValueError(
            "video_reference must be an exact video ID or supported HTTPS YouTube URL"
        )
    if order not in {"time", "relevance"}:
        raise ValueError("order must be 'time' or 'relevance'")
    if reply_mode not in {"none", "preview"}:
        raise ValueError("reply_mode must be 'none' or 'preview'")
    if search_terms is not None:
        if not isinstance(search_terms, str) or not search_terms.strip():
            raise ValueError("search_terms must be non-empty text when provided")
        search_terms = search_terms.strip()
        if (
            len(search_terms) > MAX_COMMENT_SEARCH_CHARS
            or _CONTROL_RE.search(search_terms)
        ):
            raise ValueError(
                "search_terms is too long or contains unsupported control text"
            )

    requested_at_dt = _clock_now(now)
    observed_at = _format_rfc3339(requested_at_dt)
    expires_at = _format_rfc3339(
        requested_at_dt + timedelta(days=METADATA_TTL_DAYS)
    )
    validate_youtube_api()
    options = _request_options(
        session=session,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        retries=retries,
        retry_backoff=retry_backoff,
        sleep=sleep,
    )
    preview_counts = {
        "embedded_replies_seen": 0,
        "embedded_replies_returned": 0,
        "embedded_replies_malformed": 0,
        "embedded_replies_wrong_parent": 0,
        "embedded_replies_duplicates": 0,
    }
    seen_thread_ids = set()
    seen_top_level_ids = set()

    def parse_item(item: Any) -> Tuple[Optional[Dict[str, Any]], str]:
        row, disposition, counts = _comment_thread_resource(
            item,
            expected_video_id=video_id,
            reply_mode=reply_mode,
            observed_at=observed_at,
            expires_at=expires_at,
        )
        if row is not None:
            thread_id = row["thread_id"]
            top_level_id = row["top_level_comment"]["comment_id"]
            if (
                thread_id in seen_thread_ids
                or top_level_id in seen_top_level_ids
            ):
                return None, "duplicate"
            seen_thread_ids.add(thread_id)
            seen_top_level_ids.add(top_level_id)
            # Preview coverage describes rows attached to retained threads.
            # Do not count embedded rows from a duplicate outer resource that
            # the cursor will discard.
            for name, value in counts.items():
                preview_counts[name] += value
        return row, disposition

    base_params: Dict[str, Any] = {
        "part": "snippet,replies" if reply_mode == "preview" else "snippet",
        "videoId": video_id,
        "order": order,
        "textFormat": "plainText",
    }
    if search_terms is not None:
        base_params["searchTerms"] = search_terms
    enumeration = _comment_pages(
        endpoint=YOUTUBE_COMMENT_THREADS_URL,
        stage="comment-threads",
        base_params=base_params,
        max_pages=max_pages,
        max_results=max_results,
        page_token=page_token,
        request_options=options,
        parse_item=parse_item,
        allow_comments_disabled=True,
    )
    coverage = {**enumeration["coverage"], **preview_counts}
    if any(
        preview_counts[name]
        for name in (
            "embedded_replies_malformed",
            "embedded_replies_wrong_parent",
            "embedded_replies_duplicates",
        )
    ):
        coverage["partial"] = True
    incomplete_preview_states = {
        thread.get("reply_coverage", {}).get("status")
        for thread in enumeration["rows"]
    } & {"subset", "unknown", "inconsistent"}
    if reply_mode == "preview" and incomplete_preview_states:
        enumeration["warnings"].append(
            "Embedded reply coverage is incomplete or uncertain; use "
            "yt-replies with the top-level comment ID."
        )
    api_attempts = coverage["api_attempts"]
    return {
        "provider": "youtube-data-api-v3",
        "video_id": video_id,
        "comment_threads": enumeration["rows"],
        "replies_mode": reply_mode,
        "availability": enumeration["availability"],
        "request": {
            "video_id": video_id,
            "video_url": "https://www.youtube.com/watch?v={}".format(video_id),
            "requested_at": observed_at,
            "order": order,
            "search_terms": search_terms,
            "reply_mode": reply_mode,
            "parts": base_params["part"].split(","),
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
            "retry_backoff_seconds": retry_backoff,
        },
        "coverage": coverage,
        "api_calls": {"comment_threads": api_attempts, "comments": 0, "total": api_attempts},
        "quota": {
            "units_per_request": 1,
            "estimated_units": api_attempts,
            "accounting": "attempts_conservative",
        },
        "observed_at": observed_at,
        "expires_at": expires_at,
        "warnings": enumeration["warnings"],
        "errors": enumeration["errors"],
    }


def list_video_comment_threads_detailed(
    video_reference: str,
    *,
    search_terms: Optional[str] = None,
    order: str = "time",
    reply_mode: str = "none",
    max_pages: int = DEFAULT_COMMENT_PAGES,
    max_results: int = DEFAULT_COMMENT_RESULTS,
    page_token: Optional[str] = None,
    timeout: Optional[Any] = None,
    retries: Optional[int] = None,
    session: Optional[Any] = None,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    now: Optional[Any] = None,
) -> Dict[str, Any]:
    """Return a bounded thread slice while detaching sensitive failures."""
    youtube_failure = None
    value_failure_message = None
    unexpected_failure = False
    try:
        return _list_video_comment_threads_detailed(
            video_reference,
            search_terms=search_terms,
            order=order,
            reply_mode=reply_mode,
            max_pages=max_pages,
            max_results=max_results,
            page_token=page_token,
            timeout=timeout,
            retries=retries,
            session=session,
            retry_backoff=retry_backoff,
            sleep=sleep,
            now=now,
        )
    except YouTubeAPIError as error:
        youtube_failure = _detached_youtube_error(error)
    except ValueError as error:
        value_failure_message = _safe_public_value_error(error)
    except Exception:
        unexpected_failure = True

    video_reference = ""
    search_terms = None
    order = ""
    reply_mode = ""
    max_pages = 0
    max_results = 0
    page_token = None
    timeout = None
    retries = None
    session = None
    retry_backoff = 0.0
    sleep = None
    now = None
    if youtube_failure is not None:
        raise youtube_failure from None
    if value_failure_message is not None:
        raise ValueError(value_failure_message) from None
    if unexpected_failure:
        raise _unexpected_provider_error("comment-thread") from None
    raise RuntimeError("unreachable YouTube comment-thread state")


def _list_comment_replies_detailed(
    parent_comment_reference: str,
    *,
    video_reference: Optional[str] = None,
    max_pages: int = DEFAULT_COMMENT_PAGES,
    max_results: int = DEFAULT_COMMENT_RESULTS,
    page_token: Optional[str] = None,
    timeout: Optional[Any] = None,
    retries: Optional[int] = None,
    session: Optional[Any] = None,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    now: Optional[Any] = None,
) -> Dict[str, Any]:
    (
        max_pages,
        max_results,
        page_token,
        connect_timeout,
        read_timeout,
        retries,
        retry_backoff,
    ) = _bounded_controls(
        max_pages=max_pages,
        max_results=max_results,
        page_token=page_token,
        timeout=timeout,
        retries=retries,
        retry_backoff=retry_backoff,
    )
    parsed_reference = youtube_comment_reference(parent_comment_reference)
    if parsed_reference is None:
        raise ValueError(
            "parent_comment_reference must be a comment ID or supported YouTube comment URL"
        )
    parent_comment_id, linked_video_id = parsed_reference
    explicit_video_id = None
    if video_reference is not None:
        explicit_video_id = _strict_video_id(video_reference)
        if explicit_video_id is None:
            raise ValueError(
                "video_reference must be an exact video ID or supported HTTPS YouTube URL"
            )
    if (
        linked_video_id is not None
        and explicit_video_id is not None
        and linked_video_id != explicit_video_id
    ):
        raise ValueError(
            "video_reference conflicts with the video in parent_comment_reference"
        )
    video_id = explicit_video_id or linked_video_id
    video_id_source = (
        "request_context" if video_id is not None else None
    )

    requested_at_dt = _clock_now(now)
    observed_at = _format_rfc3339(requested_at_dt)
    expires_at = _format_rfc3339(
        requested_at_dt + timedelta(days=METADATA_TTL_DAYS)
    )
    validate_youtube_api()
    options = _request_options(
        session=session,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        retries=retries,
        retry_backoff=retry_backoff,
        sleep=sleep,
    )

    def parse_item(item: Any) -> Tuple[Optional[Dict[str, Any]], str]:
        returned_video_id = _mapping(_mapping(item).get("snippet")).get(
            "videoId"
        )
        if (
            video_id is not None
            and returned_video_id != video_id
        ):
            return None, "unexpected_scope"
        row = _comment_resource(
            item,
            video_id=video_id,
            video_id_source=video_id_source,
            observed_at=observed_at,
            expires_at=expires_at,
        )
        if row is None:
            return None, "malformed"
        if row.get("parent_comment_id") != parent_comment_id:
            return None, "unexpected_scope"
        return row, "ok"

    enumeration = _comment_pages(
        endpoint=YOUTUBE_COMMENTS_URL,
        stage="comments",
        base_params={
            "part": "snippet",
            "parentId": parent_comment_id,
            "textFormat": "plainText",
        },
        max_pages=max_pages,
        max_results=max_results,
        page_token=page_token,
        request_options=options,
        parse_item=parse_item,
        allow_comments_disabled=False,
    )
    coverage = enumeration["coverage"]
    api_attempts = coverage["api_attempts"]
    return {
        "provider": "youtube-data-api-v3",
        "parent_comment_id": parent_comment_id,
        "video_id": video_id,
        "replies": enumeration["rows"],
        "request": {
            "parent_comment_id": parent_comment_id,
            "video_id": video_id,
            "video_id_source": video_id_source,
            "requested_at": observed_at,
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
            "retry_backoff_seconds": retry_backoff,
        },
        "coverage": coverage,
        "api_calls": {"comment_threads": 0, "comments": api_attempts, "total": api_attempts},
        "quota": {
            "units_per_request": 1,
            "estimated_units": api_attempts,
            "accounting": "attempts_conservative",
        },
        "observed_at": observed_at,
        "expires_at": expires_at,
        "warnings": enumeration["warnings"],
        "errors": enumeration["errors"],
    }


def list_comment_replies_detailed(
    parent_comment_reference: str,
    *,
    video_reference: Optional[str] = None,
    max_pages: int = DEFAULT_COMMENT_PAGES,
    max_results: int = DEFAULT_COMMENT_RESULTS,
    page_token: Optional[str] = None,
    timeout: Optional[Any] = None,
    retries: Optional[int] = None,
    session: Optional[Any] = None,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    now: Optional[Any] = None,
) -> Dict[str, Any]:
    """Return a bounded reply slice while detaching sensitive failures."""
    youtube_failure = None
    value_failure_message = None
    unexpected_failure = False
    try:
        return _list_comment_replies_detailed(
            parent_comment_reference,
            video_reference=video_reference,
            max_pages=max_pages,
            max_results=max_results,
            page_token=page_token,
            timeout=timeout,
            retries=retries,
            session=session,
            retry_backoff=retry_backoff,
            sleep=sleep,
            now=now,
        )
    except YouTubeAPIError as error:
        youtube_failure = _detached_youtube_error(error)
    except ValueError as error:
        value_failure_message = _safe_public_value_error(error)
    except Exception:
        unexpected_failure = True

    parent_comment_reference = ""
    video_reference = None
    max_pages = 0
    max_results = 0
    page_token = None
    timeout = None
    retries = None
    session = None
    retry_backoff = 0.0
    sleep = None
    now = None
    if youtube_failure is not None:
        raise youtube_failure from None
    if value_failure_message is not None:
        raise ValueError(value_failure_message) from None
    if unexpected_failure:
        raise _unexpected_provider_error("comment-reply") from None
    raise RuntimeError("unreachable YouTube comment-reply state")


__all__ = [
    "DEFAULT_COMMENT_PAGES",
    "DEFAULT_COMMENT_RESULTS",
    "MAX_COMMENT_PAGES",
    "MAX_COMMENT_RESULTS",
    "list_comment_replies_detailed",
    "list_video_comment_threads_detailed",
    "youtube_comment_id",
    "youtube_comment_page_token",
    "youtube_comment_reference",
    "youtube_comment_url",
    "youtube_comment_video_id",
]
