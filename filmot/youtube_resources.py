"""Bounded public YouTube playlist discovery for research workflows.

This module builds on the credential-erasing HTTP boundary in
``youtube_search``.  It deliberately exposes only read-only API-key operations
and returns request/coverage envelopes that callers can inspect, resume, and
log without retaining the developer key.
"""

from __future__ import annotations

import math
import re
import time
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

from .channel_dl import _parse_channel_reference
from .youtube_search import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_READ_TIMEOUT_SECONDS,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    METADATA_TTL_DAYS,
    YouTubeAPIError,
    _clock_now,
    _format_rfc3339,
    _optional_int,
    _request_json,
    _resolve_timeout,
    _thumbnail,
    get_video_details_detailed,
    validate_youtube_api,
)


YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
YOUTUBE_PLAYLISTS_URL = "https://www.googleapis.com/youtube/v3/playlists"
YOUTUBE_PLAYLIST_ITEMS_URL = (
    "https://www.googleapis.com/youtube/v3/playlistItems"
)

PLAYLIST_PARTS = ("snippet", "contentDetails", "status", "localizations")
PLAYLIST_ITEM_PARTS = ("snippet", "contentDetails", "status")
CHANNEL_PARTS = ("snippet", "contentDetails", "statistics", "topicDetails")

_PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,200}$")
_PLAYLIST_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


def youtube_playlist_id(value: Any) -> Optional[str]:
    """Return an exact playlist ID from a bare ID or known YouTube URL.

    YouTube does not publish a stable prefix/length grammar for every playlist
    family, so validation intentionally accepts a bounded URL-safe identifier
    instead of guessing which current prefixes are exhaustive.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if _PLAYLIST_ID_RE.fullmatch(candidate):
        return candidate

    try:
        parsed = urlsplit(candidate)
        hostname = (parsed.hostname or "").casefold()
        has_port = parsed.port is not None
    except (UnicodeError, ValueError):
        return None
    if (
        parsed.scheme != "https"
        or hostname not in _PLAYLIST_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or has_port
    ):
        return None
    values = parse_qs(parsed.query, keep_blank_values=True).get("list", [])
    if len(values) != 1:
        return None
    playlist_id = unquote(values[0]).strip()
    return playlist_id if _PLAYLIST_ID_RE.fullmatch(playlist_id) else None


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _validate_request_controls(
    *,
    max_pages: int,
    max_results: int,
    page_token: Optional[str],
    timeout: Optional[Any],
    retries: Optional[int],
    retry_backoff: float,
) -> Tuple[int, int, Optional[str], float, float, int, float]:
    """Validate every control-plane value before an API request is made."""
    for name, value in (("max_pages", max_pages), ("max_results", max_results)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("{} must be a positive integer".format(name))
    if page_token is not None:
        if not isinstance(page_token, str) or not page_token.strip():
            raise ValueError("page_token must be non-empty text when provided")
        page_token = page_token.strip()
    connect_timeout, read_timeout = _resolve_timeout(timeout)
    try:
        connect_timeout = float(connect_timeout)
        read_timeout = float(read_timeout)
        retry_backoff = float(retry_backoff)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("timeout and retry_backoff values must be numeric") from None
    if (
        not math.isfinite(connect_timeout)
        or not math.isfinite(read_timeout)
        or connect_timeout <= 0
        or read_timeout <= 0
    ):
        raise ValueError("timeouts must be finite and greater than zero")
    if not math.isfinite(retry_backoff) or retry_backoff < 0:
        raise ValueError("retry_backoff must be finite and non-negative")
    if retries is None:
        retries = DEFAULT_MAX_RETRIES
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise ValueError("retries must be a non-negative integer")
    return (
        max_pages,
        max_results,
        page_token,
        connect_timeout,
        read_timeout,
        retries,
        retry_backoff,
    )


def _invalid_response(endpoint: str, reason: str) -> YouTubeAPIError:
    return YouTubeAPIError(
        "YouTube API invalid response ({}) at {}".format(reason, endpoint),
        category="invalid_response",
        reason="malformedResponse",
        retryable=False,
        attempts=1,
    )


def _playlist_resource(
    item: Any,
    *,
    observed_at: str,
    expires_at: str,
) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    playlist_id = _text(item.get("id"))
    if not playlist_id:
        return None
    snippet = _mapping(item.get("snippet"))
    details = _mapping(item.get("contentDetails"))
    status = _mapping(item.get("status"))
    return {
        "playlist_id": playlist_id,
        "title": _text(snippet.get("title")) or "",
        "description": _text(snippet.get("description")) or "",
        "channel_id": _text(snippet.get("channelId")),
        "channel_title": _text(snippet.get("channelTitle")),
        "published_at": _text(snippet.get("publishedAt")),
        "thumbnail": _thumbnail(snippet),
        "thumbnails": _mapping(snippet.get("thumbnails")) or None,
        "tags": _string_list(snippet.get("tags")),
        "default_language": _text(snippet.get("defaultLanguage")),
        "localized": _mapping(snippet.get("localized")) or None,
        "localizations": _mapping(item.get("localizations")) or None,
        "item_count": _optional_int(details.get("itemCount")),
        "privacy_status": _text(status.get("privacyStatus")),
        "url": "https://www.youtube.com/playlist?list={}".format(playlist_id),
        "observed_at": observed_at,
        "expires_at": expires_at,
        "provider": "youtube-data-api-v3",
    }


def _channel_resource(
    item: Any,
    *,
    observed_at: str,
    expires_at: str,
) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    channel_id = _text(item.get("id"))
    if not channel_id:
        return None
    snippet = _mapping(item.get("snippet"))
    content = _mapping(item.get("contentDetails"))
    related = _mapping(content.get("relatedPlaylists"))
    statistics = _mapping(item.get("statistics"))
    topics = _mapping(item.get("topicDetails"))
    return {
        "channel_id": channel_id,
        "title": _text(snippet.get("title")) or "",
        "description": _text(snippet.get("description")) or "",
        "custom_url": _text(snippet.get("customUrl")),
        "published_at": _text(snippet.get("publishedAt")),
        "country": _text(snippet.get("country")),
        "default_language": _text(snippet.get("defaultLanguage")),
        "thumbnail": _thumbnail(snippet),
        "thumbnails": _mapping(snippet.get("thumbnails")) or None,
        "uploads_playlist_id": _text(related.get("uploads")),
        "likes_playlist_id": _text(related.get("likes")),
        "view_count": _optional_int(statistics.get("viewCount")),
        "subscriber_count": _optional_int(statistics.get("subscriberCount")),
        "video_count": _optional_int(statistics.get("videoCount")),
        "hidden_subscriber_count": (
            statistics.get("hiddenSubscriberCount")
            if isinstance(statistics.get("hiddenSubscriberCount"), bool)
            else None
        ),
        "topic_ids": _string_list(topics.get("topicIds")),
        "topic_categories": _string_list(topics.get("topicCategories")),
        "url": "https://www.youtube.com/channel/{}".format(channel_id),
        "observed_at": observed_at,
        "expires_at": expires_at,
        "provider": "youtube-data-api-v3",
    }


def _playlist_item_resource(
    item: Any,
    *,
    playlist_id: str,
    observed_at: str,
    expires_at: str,
) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    snippet = _mapping(item.get("snippet"))
    content = _mapping(item.get("contentDetails"))
    status = _mapping(item.get("status"))
    resource = _mapping(snippet.get("resourceId"))
    video_id = _text(content.get("videoId")) or _text(resource.get("videoId"))
    playlist_item_id = _text(item.get("id"))
    if playlist_item_id is None and not snippet and not content and not status:
        return None
    return {
        "playlist_item_id": playlist_item_id,
        "playlist_id": _text(snippet.get("playlistId")) or playlist_id,
        "playlist_position": _optional_int(snippet.get("position")),
        "playlist_added_at": _text(snippet.get("publishedAt")),
        "playlist_item_title": _text(snippet.get("title")) or "",
        "playlist_item_description": _text(snippet.get("description")) or "",
        "playlist_item_thumbnail": _thumbnail(snippet),
        "playlist_item_thumbnails": _mapping(snippet.get("thumbnails")) or None,
        "playlist_channel_id": _text(snippet.get("channelId")),
        "playlist_channel_title": _text(snippet.get("channelTitle")),
        "video_id": video_id,
        "video_owner_channel_id": _text(snippet.get("videoOwnerChannelId")),
        "video_owner_channel_title": _text(
            snippet.get("videoOwnerChannelTitle")
        ),
        "video_published_at": _text(content.get("videoPublishedAt")),
        "privacy_status": _text(status.get("privacyStatus")),
        "observed_at": observed_at,
        "expires_at": expires_at,
        "provider": "youtube-data-api-v3",
    }


def _error_row(error: YouTubeAPIError, *, stage: str, page: int) -> Dict[str, Any]:
    return {"stage": stage, "page": page, **error.to_dict()}


def _request_options(
    *,
    session: Optional[Any],
    connect_timeout: float,
    read_timeout: float,
    retries: int,
    retry_backoff: float,
    sleep: Callable[[float], None],
) -> Dict[str, Any]:
    return {
        "session": session,
        "connect_timeout": connect_timeout,
        "read_timeout": read_timeout,
        "max_retries": retries,
        "retry_backoff": retry_backoff,
        "sleep": sleep,
    }


def _page_info(response: Dict[str, Any]) -> Dict[str, Optional[int]]:
    raw = _mapping(response.get("pageInfo"))
    return {
        "approximate_total_results": _optional_int(raw.get("totalResults")),
        "results_per_page": _optional_int(raw.get("resultsPerPage")),
    }


def _merge_page_info(
    current: Dict[str, Optional[int]],
    update: Dict[str, Optional[int]],
) -> None:
    for name, value in update.items():
        if value is not None:
            current[name] = value


def _next_token(response: Dict[str, Any]) -> Tuple[Optional[str], bool]:
    value = response.get("nextPageToken")
    if value is None or value == "":
        return None, True
    if not isinstance(value, str):
        return None, False
    return value, True


def _enumerate_playlists(
    channel_id: str,
    *,
    max_pages: int,
    max_results: int,
    page_token: Optional[str],
    observed_at: str,
    expires_at: str,
    request_options: Dict[str, Any],
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    seen_ids = set()
    seen_tokens = set()
    current_token = page_token
    continuation = page_token
    pages_attempted = 0
    pages_fetched = 0
    api_attempts = 0
    malformed = 0
    duplicates = 0
    stopping_reason = "page_budget"
    partial = False
    errors: List[Dict[str, Any]] = []
    page_info: Dict[str, Optional[int]] = {
        "approximate_total_results": None,
        "results_per_page": None,
    }

    for page in range(1, max_pages + 1):
        pages_attempted += 1
        if current_token:
            seen_tokens.add(current_token)
        params = {
            "part": ",".join(PLAYLIST_PARTS),
            "channelId": channel_id,
            "maxResults": min(50, max_results - len(rows)),
            "pageToken": current_token,
        }
        try:
            result = _request_json(YOUTUBE_PLAYLISTS_URL, params, **request_options)
        except YouTubeAPIError as error:
            api_attempts += error.attempts
            if not rows:
                raise
            errors.append(_error_row(error, stage="playlists", page=page))
            stopping_reason = "partial_failure"
            partial = True
            continuation = current_token
            break
        api_attempts += result.attempts
        items = result.data.get("items")
        if not isinstance(items, list):
            error = _invalid_response(
                YOUTUBE_PLAYLISTS_URL,
                "playlists.items is not an array",
            )
            if not rows:
                raise error
            errors.append(_error_row(error, stage="playlists", page=page))
            stopping_reason = "partial_failure"
            partial = True
            continuation = current_token
            break

        pages_fetched += 1
        _merge_page_info(page_info, _page_info(result.data))
        for item in items:
            parsed = _playlist_resource(
                item,
                observed_at=observed_at,
                expires_at=expires_at,
            )
            if parsed is None:
                malformed += 1
                continue
            playlist_id = parsed["playlist_id"]
            if playlist_id in seen_ids:
                duplicates += 1
                continue
            seen_ids.add(playlist_id)
            rows.append(parsed)
            if len(rows) >= max_results:
                break

        token, token_valid = _next_token(result.data)
        if not token_valid:
            error = _invalid_response(
                YOUTUBE_PLAYLISTS_URL,
                "playlists.nextPageToken is not text",
            )
            errors.append(_error_row(error, stage="pagination", page=page))
            stopping_reason = "partial_failure"
            partial = True
            continuation = None
            break
        continuation = token
        if len(rows) >= max_results:
            stopping_reason = "result_budget"
            break
        if token is None:
            stopping_reason = "exhausted"
            break
        if token in seen_tokens:
            error = _invalid_response(
                YOUTUBE_PLAYLISTS_URL,
                "playlists pagination repeated a token",
            )
            errors.append(_error_row(error, stage="pagination", page=page))
            stopping_reason = "partial_failure"
            partial = True
            continuation = None
            break
        current_token = token
        if page >= max_pages:
            stopping_reason = "page_budget"
            break

    warnings = []
    if errors:
        warnings.append(
            "Playlist enumeration stopped after a later failure; earlier rows "
            "were preserved."
        )
    if malformed:
        warnings.append("Malformed playlist resources were skipped.")
    if duplicates:
        warnings.append("Duplicate playlist resources were skipped.")
    return {
        "rows": rows,
        "coverage": {
            "pages_attempted": pages_attempted,
            "pages_fetched": pages_fetched,
            "api_attempts": api_attempts,
            "api_calls": api_attempts,
            "returned": len(rows),
            "duplicates_skipped": duplicates,
            "malformed_items_skipped": malformed,
            "next_page_token": continuation,
            "page_info": page_info,
            "approximate_total": page_info["approximate_total_results"],
            "stopping_reason": stopping_reason,
            "partial": partial,
        },
        "warnings": warnings,
        "errors": errors,
    }


def list_channel_playlists_detailed(
    channel_reference: str,
    *,
    max_pages: int = 2,
    max_results: int = 100,
    page_token: Optional[str] = None,
    timeout: Optional[Any] = None,
    retries: Optional[int] = None,
    session: Optional[Any] = None,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    now: Optional[Any] = None,
) -> Dict[str, Any]:
    """List a channel's public playlists with bounded resumable coverage."""
    (
        max_pages,
        max_results,
        page_token,
        connect_timeout,
        read_timeout,
        retries,
        retry_backoff,
    ) = _validate_request_controls(
        max_pages=max_pages,
        max_results=max_results,
        page_token=page_token,
        timeout=timeout,
        retries=retries,
        retry_backoff=retry_backoff,
    )
    filter_name, filter_value = _parse_channel_reference(channel_reference)
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

    channel_params = {
        "part": ",".join(CHANNEL_PARTS),
        filter_name: filter_value,
    }
    channel_result = _request_json(
        YOUTUBE_CHANNELS_URL,
        channel_params,
        **options,
    )
    channel_items = channel_result.data.get("items")
    if not isinstance(channel_items, list):
        raise _invalid_response(
            YOUTUBE_CHANNELS_URL,
            "channels.items is not an array",
        )
    malformed_channels = 0
    channel = None
    for raw_channel in channel_items:
        candidate = _channel_resource(
            raw_channel,
            observed_at=observed_at,
            expires_at=expires_at,
        )
        if candidate is None:
            malformed_channels += 1
            continue
        if filter_name == "id" and candidate["channel_id"] != filter_value:
            malformed_channels += 1
            continue
        channel = candidate
        break

    base_request = {
        "channel_reference": channel_reference,
        "requested_at": observed_at,
        "max_pages": max_pages,
        "max_results": max_results,
        "page_token": page_token,
        "page_budget": max_pages,
        "result_budget": max_results,
        "parts": list(PLAYLIST_PARTS),
        "timeout": {
            "connect_seconds": connect_timeout,
            "read_seconds": read_timeout,
        },
        "retries": retries,
        "retry_backoff_seconds": retry_backoff,
    }
    if channel is None:
        warnings = [
            "The completed channels.list request did not return the requested "
            "channel; no availability reason was inferred."
        ]
        if malformed_channels:
            warnings.append("Malformed or unexpected channel resources were skipped.")
        return {
            "provider": "youtube-data-api-v3",
            "channel": None,
            "playlists": [],
            "request": base_request,
            "coverage": {
                "channel_returned": False,
                "pages_attempted": 0,
                "pages_fetched": 0,
                "api_attempts": channel_result.attempts,
                "api_calls": channel_result.attempts,
                "returned": 0,
                "next_page_token": None,
                "stopping_reason": "channel_not_returned",
                "partial": True,
                "malformed_channels_skipped": malformed_channels,
            },
            "observed_at": observed_at,
            "expires_at": expires_at,
            "warnings": warnings,
            "errors": [],
            "api_calls": {
                "channels": channel_result.attempts,
                "playlists": 0,
                "total": channel_result.attempts,
            },
        }

    enumeration = _enumerate_playlists(
        channel["channel_id"],
        max_pages=max_pages,
        max_results=max_results,
        page_token=page_token,
        observed_at=observed_at,
        expires_at=expires_at,
        request_options=options,
    )
    playlist_calls = enumeration["coverage"]["api_attempts"]
    coverage = {
        "channel_returned": True,
        "malformed_channels_skipped": malformed_channels,
        **enumeration["coverage"],
    }
    coverage["api_attempts"] = channel_result.attempts + playlist_calls
    coverage["api_calls"] = coverage["api_attempts"]
    request = {
        **base_request,
        "channel_id": channel["channel_id"],
        "resolved_by": "channel_id" if filter_name == "id" else "handle",
    }
    warnings = list(enumeration["warnings"])
    if malformed_channels:
        warnings.append("Malformed or unexpected channel resources were skipped.")
    return {
        "provider": "youtube-data-api-v3",
        "channel": channel,
        "playlists": enumeration["rows"],
        "request": request,
        "coverage": coverage,
        "observed_at": observed_at,
        "expires_at": expires_at,
        "warnings": warnings,
        "errors": enumeration["errors"],
        "api_calls": {
            "channels": channel_result.attempts,
            "playlists": playlist_calls,
            "total": channel_result.attempts + playlist_calls,
        },
    }


def _enumerate_playlist_items(
    playlist_id: str,
    *,
    max_pages: int,
    max_results: int,
    page_token: Optional[str],
    observed_at: str,
    expires_at: str,
    request_options: Dict[str, Any],
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    seen_item_ids = set()
    seen_tokens = set()
    current_token = page_token
    continuation = page_token
    pages_attempted = 0
    pages_fetched = 0
    api_attempts = 0
    malformed = 0
    idless = 0
    duplicate_items = 0
    page_info: Dict[str, Optional[int]] = {
        "approximate_total_results": None,
        "results_per_page": None,
    }
    stopping_reason = "page_budget"
    partial = False
    errors: List[Dict[str, Any]] = []

    for page in range(1, max_pages + 1):
        pages_attempted += 1
        if current_token:
            seen_tokens.add(current_token)
        params = {
            "part": ",".join(PLAYLIST_ITEM_PARTS),
            "playlistId": playlist_id,
            "maxResults": min(50, max_results - len(rows)),
            "pageToken": current_token,
        }
        try:
            result = _request_json(
                YOUTUBE_PLAYLIST_ITEMS_URL,
                params,
                **request_options,
            )
        except YouTubeAPIError as error:
            api_attempts += error.attempts
            errors.append(_error_row(error, stage="playlist-items", page=page))
            stopping_reason = "partial_failure"
            partial = True
            continuation = current_token
            break
        api_attempts += result.attempts
        items = result.data.get("items")
        if not isinstance(items, list):
            error = _invalid_response(
                YOUTUBE_PLAYLIST_ITEMS_URL,
                "playlistItems.items is not an array",
            )
            errors.append(_error_row(error, stage="playlist-items", page=page))
            stopping_reason = "partial_failure"
            partial = True
            continuation = current_token
            break

        pages_fetched += 1
        _merge_page_info(page_info, _page_info(result.data))
        for item in items:
            parsed = _playlist_item_resource(
                item,
                playlist_id=playlist_id,
                observed_at=observed_at,
                expires_at=expires_at,
            )
            if parsed is None:
                malformed += 1
                continue
            item_id = parsed.get("playlist_item_id")
            if item_id and item_id in seen_item_ids:
                duplicate_items += 1
                continue
            if item_id:
                seen_item_ids.add(item_id)
            if parsed.get("video_id") is None:
                idless += 1
            rows.append(parsed)
            if len(rows) >= max_results:
                break

        token, token_valid = _next_token(result.data)
        if not token_valid:
            error = _invalid_response(
                YOUTUBE_PLAYLIST_ITEMS_URL,
                "playlistItems.nextPageToken is not text",
            )
            errors.append(_error_row(error, stage="pagination", page=page))
            stopping_reason = "partial_failure"
            partial = True
            continuation = None
            break
        continuation = token
        if len(rows) >= max_results:
            stopping_reason = "result_budget"
            break
        if token is None:
            stopping_reason = "exhausted"
            break
        if token in seen_tokens:
            error = _invalid_response(
                YOUTUBE_PLAYLIST_ITEMS_URL,
                "playlistItems pagination repeated a token",
            )
            errors.append(_error_row(error, stage="pagination", page=page))
            stopping_reason = "partial_failure"
            partial = True
            continuation = None
            break
        current_token = token
        if page >= max_pages:
            stopping_reason = "page_budget"
            break

    video_ids = []
    seen_video_ids = set()
    duplicate_video_ids = 0
    for row in rows:
        video_id = row.get("video_id")
        if not isinstance(video_id, str):
            continue
        if video_id in seen_video_ids:
            duplicate_video_ids += 1
            continue
        seen_video_ids.add(video_id)
        video_ids.append(video_id)

    warnings = []
    if errors:
        warnings.append(
            "Playlist-item enumeration was incomplete; usable earlier rows "
            "were preserved."
        )
    if malformed:
        warnings.append("Malformed playlist-item resources were skipped.")
    if idless:
        warnings.append(
            "Playlist items without a usable video ID were retained as item "
            "evidence but excluded from video metadata lookup."
        )
    if duplicate_items:
        warnings.append("Duplicate playlist-item resources were skipped.")
    if duplicate_video_ids:
        warnings.append(
            "Repeated videos remain visible as playlist items but were looked "
            "up only once."
        )
    return {
        "rows": rows,
        "video_ids": video_ids,
        "coverage": {
            "pages_attempted": pages_attempted,
            "pages_fetched": pages_fetched,
            "api_attempts": api_attempts,
            "api_calls": api_attempts,
            "items_seen": (
                len(rows) + malformed + duplicate_items
            ),
            "playlist_items_returned": len(rows),
            "unique_video_ids": len(video_ids),
            "duplicate_video_ids": duplicate_video_ids,
            "duplicate_items_skipped": duplicate_items,
            "malformed_items_skipped": malformed,
            "idless_items": idless,
            "next_page_token": continuation,
            "page_info": page_info,
            "approximate_total": page_info["approximate_total_results"],
            "stopping_reason": stopping_reason,
            "partial": partial,
        },
        "warnings": warnings,
        "errors": errors,
    }


def _video_with_playlist_context(
    video: Dict[str, Any],
    playlist_item: Dict[str, Any],
) -> Dict[str, Any]:
    output = dict(video)
    for name in (
        "playlist_item_id",
        "playlist_id",
        "playlist_position",
        "playlist_added_at",
        "playlist_item_title",
        "playlist_item_description",
        "playlist_item_thumbnail",
        "playlist_channel_id",
        "playlist_channel_title",
        "video_owner_channel_id",
        "video_owner_channel_title",
        "video_published_at",
    ):
        output[name] = playlist_item.get(name)
    output["provenance"] = {
        "resource": "playlistItem",
        "playlist_id": playlist_item.get("playlist_id"),
        "playlist_item_id": playlist_item.get("playlist_item_id"),
        "playlist_position": playlist_item.get("playlist_position"),
        "playlist_added_at": playlist_item.get("playlist_added_at"),
    }
    return output


def get_playlist_detailed(
    playlist_reference: str,
    *,
    max_pages: int = 2,
    max_results: int = 100,
    page_token: Optional[str] = None,
    timeout: Optional[Any] = None,
    retries: Optional[int] = None,
    session: Optional[Any] = None,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    now: Optional[Any] = None,
) -> Dict[str, Any]:
    """Inspect a playlist and return a pipeline-safe enriched video slice.

    The complete bounded playlist-item slice is retained in ``playlist_items``.
    ``videos`` contains only IDs observed by a completed ``videos.list``
    response, with playlist position/provenance attached, so raw output can be
    piped into Filmot's transcript downloader without treating unavailable or
    unprocessed IDs as current video observations.
    """
    playlist_id = youtube_playlist_id(playlist_reference)
    # The supplied URL may contain unrelated query parameters (including a
    # mistakenly pasted credential).  Retain only the parsed public identity
    # before any validation or network error can expose this frame.
    playlist_reference = None
    if playlist_id is None:
        raise ValueError(
            "playlist_reference must be a playlist ID or supported YouTube URL"
        )
    (
        max_pages,
        max_results,
        page_token,
        connect_timeout,
        read_timeout,
        retries,
        retry_backoff,
    ) = _validate_request_controls(
        max_pages=max_pages,
        max_results=max_results,
        page_token=page_token,
        timeout=timeout,
        retries=retries,
        retry_backoff=retry_backoff,
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

    metadata_result = _request_json(
        YOUTUBE_PLAYLISTS_URL,
        {"part": ",".join(PLAYLIST_PARTS), "id": playlist_id},
        **options,
    )
    metadata_items = metadata_result.data.get("items")
    if not isinstance(metadata_items, list):
        raise _invalid_response(
            YOUTUBE_PLAYLISTS_URL,
            "playlists.items is not an array",
        )
    playlist = None
    malformed_playlists = 0
    unexpected_playlists = 0
    for raw_playlist in metadata_items:
        candidate = _playlist_resource(
            raw_playlist,
            observed_at=observed_at,
            expires_at=expires_at,
        )
        if candidate is None:
            malformed_playlists += 1
            continue
        if candidate["playlist_id"] != playlist_id:
            unexpected_playlists += 1
            continue
        if playlist is None:
            playlist = candidate

    request = {
        "playlist_id": playlist_id,
        "playlist_url": "https://www.youtube.com/playlist?list={}".format(
            playlist_id
        ),
        "requested_at": observed_at,
        "max_pages": max_pages,
        "max_results": max_results,
        "page_token": page_token,
        "page_budget": max_pages,
        "result_budget": max_results,
        "playlist_parts": list(PLAYLIST_PARTS),
        "playlist_item_parts": list(PLAYLIST_ITEM_PARTS),
        "video_details": True,
        "timeout": {
            "connect_seconds": connect_timeout,
            "read_seconds": read_timeout,
        },
        "retries": retries,
        "retry_backoff_seconds": retry_backoff,
    }
    if playlist is None:
        warnings = [
            "The completed playlists.list request did not return the requested "
            "playlist; no availability reason was inferred."
        ]
        if malformed_playlists or unexpected_playlists:
            warnings.append("Malformed or unexpected playlist resources were skipped.")
        return {
            "provider": "youtube-data-api-v3",
            "playlist": None,
            "playlist_items": [],
            "videos": [],
            "video_id_outcomes": [],
            "request": request,
            "coverage": {
                "playlist_returned": False,
                "playlist_items_returned": 0,
                "unique_video_ids": 0,
                "videos_returned": 0,
                "pages_attempted": 0,
                "pages_fetched": 0,
                "api_attempts": metadata_result.attempts,
                "api_calls": metadata_result.attempts,
                "next_page_token": None,
                "stopping_reason": "playlist_not_returned",
                "partial": True,
                "malformed_playlists_skipped": malformed_playlists,
                "unexpected_playlists_skipped": unexpected_playlists,
            },
            "observed_at": observed_at,
            "expires_at": expires_at,
            "warnings": warnings,
            "errors": [],
            "api_calls": {
                "playlists": metadata_result.attempts,
                "playlist_items": 0,
                "videos": 0,
                "total": metadata_result.attempts,
            },
        }

    enumeration = _enumerate_playlist_items(
        playlist_id,
        max_pages=max_pages,
        max_results=max_results,
        page_token=page_token,
        observed_at=observed_at,
        expires_at=expires_at,
        request_options=options,
    )
    details = get_video_details_detailed(
        enumeration["video_ids"],
        timeout=(connect_timeout, read_timeout),
        retries=retries,
        session=session,
        retry_backoff=retry_backoff,
        sleep=sleep,
        now=requested_at_dt,
    )
    first_item_by_video: Dict[str, Dict[str, Any]] = {}
    for item in enumeration["rows"]:
        video_id = item.get("video_id")
        if isinstance(video_id, str):
            first_item_by_video.setdefault(video_id, item)
    videos = [
        _video_with_playlist_context(video, first_item_by_video[video["video_id"]])
        for video in details["videos"]
        if video.get("video_id") in first_item_by_video
    ]
    outcome_by_id = {
        item.get("video_id"): item
        for item in details["id_outcomes"]
        if isinstance(item, dict) and isinstance(item.get("video_id"), str)
    }
    playlist_items = []
    for item in enumeration["rows"]:
        copied = dict(item)
        video_outcome = outcome_by_id.get(copied.get("video_id"))
        copied["video_metadata_status"] = (
            video_outcome.get("status")
            if isinstance(video_outcome, dict)
            else "not_applicable"
        )
        playlist_items.append(copied)

    detail_coverage = dict(details["coverage"])
    item_coverage = dict(enumeration["coverage"])
    total_calls = (
        metadata_result.attempts
        + int(item_coverage.get("api_attempts") or 0)
        + int(detail_coverage.get("api_attempts") or 0)
    )
    partial = bool(item_coverage.get("partial") or detail_coverage.get("partial"))
    coverage = {
        **item_coverage,
        "playlist_returned": True,
        "videos_returned": len(videos),
        "video_details": detail_coverage,
        "malformed_playlists_skipped": malformed_playlists,
        "unexpected_playlists_skipped": unexpected_playlists,
        "api_attempts": total_calls,
        "api_calls": total_calls,
        "partial": partial,
    }
    warnings = [*enumeration["warnings"], *details["warnings"]]
    if malformed_playlists or unexpected_playlists:
        warnings.append("Malformed or unexpected playlist resources were skipped.")
    errors = [*enumeration["errors"], *details["errors"]]
    return {
        "provider": "youtube-data-api-v3",
        "playlist": playlist,
        "playlist_items": playlist_items,
        "videos": videos,
        "video_id_outcomes": details["id_outcomes"],
        "request": request,
        "coverage": coverage,
        "observed_at": observed_at,
        "expires_at": expires_at,
        "warnings": warnings,
        "errors": errors,
        "api_calls": {
            "playlists": metadata_result.attempts,
            "playlist_items": item_coverage.get("api_attempts", 0),
            "videos": detail_coverage.get("api_attempts", 0),
            "total": total_calls,
        },
    }


__all__ = [
    "get_playlist_detailed",
    "list_channel_playlists_detailed",
    "youtube_playlist_id",
]
