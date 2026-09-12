"""YouTube Data API discovery and public video metadata.

The original list-returning helpers remain compatible.  The additive
``search_recent_detailed`` and ``get_video_details_detailed`` provider APIs
record request/coverage information, preserve completed work after optional
or later-batch failures, and expose only credential-safe failures.
"""

from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import requests

# Importing config runs the explicit user-config/project-.env sequence without
# python-dotenv's implicit parent-directory search.
from . import config as _config  # noqa: F401


YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
_IMPORTED_YOUTUBE_API_KEY = YOUTUBE_API_KEY
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_READ_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 0.5
MAX_RETRY_DELAY_SECONDS = 8.0
METADATA_TTL_DAYS = 30
VIDEO_DETAIL_PARTS = (
    "snippet",
    "statistics",
    "contentDetails",
    "status",
    "liveStreamingDetails",
    "paidProductPlacementDetails",
    "topicDetails",
)

_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_SAFE_REASON_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


class YouTubeAPIError(requests.exceptions.HTTPError):
    """Structured YouTube failure that cannot retain request credentials.

    Subclassing ``HTTPError`` preserves existing catch behavior.  No request,
    response, response body, params, or originating exception is attached: any
    of those can retain the API key embedded in the prepared query URL.
    """

    def __init__(
        self,
        message: str,
        *,
        category: str,
        reason: str,
        status_code: Optional[int] = None,
        retryable: bool = False,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.reason = reason
        self.status_code = status_code
        self.retryable = retryable
        self.attempts = attempts

    def to_dict(self) -> Dict[str, Any]:
        """Return a stable JSON-safe diagnostic."""
        return {
            "type": type(self).__name__,
            "category": self.category,
            "reason": self.reason,
            "status_code": self.status_code,
            "retryable": self.retryable,
            "attempts": self.attempts,
            "message": str(self),
        }


@dataclass(frozen=True)
class _RequestResult:
    data: Dict[str, Any]
    attempts: int


def _api_key() -> str:
    """Resolve the key at call time while honoring legacy monkeypatches."""
    if YOUTUBE_API_KEY != _IMPORTED_YOUTUBE_API_KEY:
        return YOUTUBE_API_KEY
    return os.getenv("YOUTUBE_API_KEY", "")


def validate_youtube_api() -> bool:
    """Check whether a YouTube API key is configured right now."""
    if not _api_key():
        raise ValueError(
            "Missing YOUTUBE_API_KEY in .env file. "
            "Get one from https://console.cloud.google.com/apis/credentials"
        )
    return True


def _safe_reason(value: Any, fallback: str) -> str:
    value = str(value or "")
    return value if _SAFE_REASON_RE.fullmatch(value) else fallback


def _safe_endpoint(url: str) -> str:
    """Keep useful endpoint context while dropping all query parameters."""
    return str(url).split("?", 1)[0].split("#", 1)[0]


def _error_category(reason: str, status_code: Optional[int]) -> str:
    normalized = reason.lower()
    if normalized in {
        "quotaexceeded", "dailylimitexceeded", "dailylimitexceededunreg",
    }:
        return "quota"
    if normalized in {
        "ratelimitexceeded", "userratelimitexceeded", "toomanyrequests",
    } or status_code == 429:
        return "rate_limit"
    if normalized in {
        "keyinvalid", "forbidden", "accountdelegationforbidden",
        "iprefererblocked", "accessnotconfigured", "youtubesignuprequired",
    } or status_code in {401, 403}:
        return "authentication"
    if status_code == 404:
        return "not_found"
    if status_code is not None and 400 <= status_code < 500:
        return "invalid_request"
    if status_code is not None and status_code >= 500:
        return "server"
    return "unknown"


def _google_error(response: Any, endpoint: str, attempts: int) -> YouTubeAPIError:
    status_code = getattr(response, "status_code", None)
    reason = "httpError"
    try:
        payload = response.json()
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        errors = error.get("errors", []) if isinstance(error, dict) else []
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            reason = _safe_reason(errors[0].get("reason"), reason)
        elif isinstance(error, dict):
            reason = _safe_reason(error.get("status"), reason)
    except (TypeError, ValueError, requests.RequestException):
        pass

    category = _error_category(reason, status_code)
    retryable = category in {"rate_limit", "server"}
    status = f", HTTP {status_code}" if status_code is not None else ""
    return YouTubeAPIError(
        f"YouTube API {category} error ({reason}{status}) at {_safe_endpoint(endpoint)}",
        category=category,
        reason=reason,
        status_code=status_code,
        retryable=retryable,
        attempts=attempts,
    )


def _transport_error(
    exc: requests.RequestException,
    endpoint: str,
    attempts: int,
) -> YouTubeAPIError:
    if isinstance(exc, requests.Timeout):
        category, reason, retryable = "timeout", "timeout", True
    elif isinstance(exc, requests.ConnectionError):
        category, reason, retryable = "network", "connectionError", True
    else:
        category, reason, retryable = "network", "requestError", False
    # Never interpolate exc: requests messages routinely contain ?key=...
    return YouTubeAPIError(
        f"YouTube API {category} error ({reason}) at {_safe_endpoint(endpoint)}",
        category=category,
        reason=reason,
        retryable=retryable,
        attempts=attempts,
    )


def _retry_after_seconds(response: Any) -> Optional[float]:
    try:
        value = response.headers.get("Retry-After")
    except (AttributeError, TypeError):
        return None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _request_json(
    url: str,
    params: Dict[str, Any],
    *,
    session: Optional[Any] = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: float = DEFAULT_READ_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> _RequestResult:
    """Issue a bounded request and return its mapping body plus attempt count."""
    try:
        connect_timeout = float(connect_timeout)
        read_timeout = float(read_timeout)
        retry_backoff = float(retry_backoff)
        if (
            not math.isfinite(connect_timeout)
            or not math.isfinite(read_timeout)
            or connect_timeout <= 0
            or read_timeout <= 0
        ):
            raise ValueError(
                "YouTube connect/read timeouts must be finite and greater than zero"
            )
        if isinstance(max_retries, bool) or not isinstance(max_retries, int):
            raise ValueError("YouTube retries must be an integer")
        if max_retries < 0:
            raise ValueError("YouTube retries cannot be negative")
        if not math.isfinite(retry_backoff) or retry_backoff < 0:
            raise ValueError("YouTube retry backoff must be finite and non-negative")
    except (TypeError, ValueError, OverflowError):
        # A legacy caller may have put a key in the input mapping. Ensure even
        # validation-error traceback locals do not retain that mapping.
        params = {}
        session = None
        url = _safe_endpoint(url)
        raise

    # Keep credentials out of caller frames. Legacy callers may still supply a
    # key in ``params``; copy it into the ephemeral native request and rebind
    # the traceback-visible argument to a credential-free mapping immediately.
    supplied_params = params
    supplied_key = supplied_params.get("key")
    params = {
        name: value
        for name, value in supplied_params.items()
        if str(name).lower() != "key"
    }
    request_params = dict(params)
    request_params["key"] = supplied_key or _api_key()
    supplied_params = None
    supplied_key = None

    request_get = session.get if session is not None else requests.get
    attempts = 0
    while True:
        attempts += 1
        response = None
        request_error: Optional[YouTubeAPIError] = None
        try:
            response = request_get(
                url,
                params=request_params,
                timeout=(connect_timeout, read_timeout),
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            request_error = (
                _google_error(response, url, attempts)
                if response is not None
                else _transport_error(exc, url, attempts)
            )
        # Raise after leaving the handler. ``raise ... from None`` suppresses
        # display of a context but, when used *inside* an except block, Python
        # still retains the originating requests exception on ``__context__``.
        # That object can carry the prepared credential-bearing request URL.
        if request_error is not None:
            if not request_error.retryable or attempts > max_retries:
                # An exception traceback retains this frame. Remove native
                # objects and the credential-bearing request mapping before
                # raising the detached public diagnostic.
                response = None
                request_params = {}
                request_get = None
                session = None
                url = _safe_endpoint(url)
                raise request_error
            retry_after = _retry_after_seconds(response)
            delay = (
                retry_after
                if retry_after is not None
                else retry_backoff * (2 ** (attempts - 1))
            )
            sleep(min(MAX_RETRY_DELAY_SECONDS, delay))
            continue

        json_error: Optional[YouTubeAPIError] = None
        try:
            data = response.json()
        except (TypeError, ValueError, requests.RequestException):
            json_error = YouTubeAPIError(
                f"YouTube API response error (invalidJson) at {_safe_endpoint(url)}",
                category="response",
                reason="invalidJson",
                status_code=getattr(response, "status_code", None),
                retryable=True,
                attempts=attempts,
            )
        if json_error is not None:
            if attempts > max_retries:
                response = None
                request_params = {}
                request_get = None
                session = None
                url = _safe_endpoint(url)
                raise json_error
            sleep(min(MAX_RETRY_DELAY_SECONDS, retry_backoff * (2 ** (attempts - 1))))
            continue
        if not isinstance(data, dict):
            error = YouTubeAPIError(
                f"YouTube API response error (invalidEnvelope) at {_safe_endpoint(url)}",
                category="response",
                reason="invalidEnvelope",
                status_code=getattr(response, "status_code", None),
                retryable=True,
                attempts=attempts,
            )
            if attempts > max_retries:
                data = None
                response = None
                request_params = {}
                request_get = None
                session = None
                url = _safe_endpoint(url)
                raise error
            sleep(min(MAX_RETRY_DELAY_SECONDS, retry_backoff * (2 ** (attempts - 1))))
            continue
        return _RequestResult(data=data, attempts=attempts)


def _get_json(url: str, params: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    """Compatibility wrapper returning only the JSON mapping."""
    try:
        return _request_json(url, params, **kwargs).data
    except YouTubeAPIError:
        url = _safe_endpoint(url)
        params = {}
        kwargs = {}
        raise


def _clock_now(now: Optional[Any] = None) -> datetime:
    current = now() if callable(now) else now
    if current is None:
        current = datetime.now(timezone.utc)
    if not isinstance(current, datetime):
        raise TypeError("now must be a datetime or a callable returning one")
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _format_rfc3339(value: datetime) -> str:
    value = value.astimezone(timezone.utc)
    timespec = "microseconds" if value.microsecond else "seconds"
    return value.isoformat(timespec=timespec).replace("+00:00", "Z")


def _normalize_rfc3339(value: Any, field: str) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        # Naive datetimes historically meant UTC in this module.
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return _format_rfc3339(value)
    if not isinstance(value, str) or not _RFC3339_RE.fullmatch(value):
        raise ValueError(f"{field} must be an RFC3339 timestamp with a timezone")
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError:
        raise ValueError(f"{field} must be a valid RFC3339 timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return _format_rfc3339(parsed)


def _validate_bounds(after: Any, before: Any) -> Tuple[Optional[str], Optional[str]]:
    normalized_after = _normalize_rfc3339(after, "published_after")
    normalized_before = _normalize_rfc3339(before, "published_before")
    if normalized_after and normalized_before:
        parsed_after = datetime.fromisoformat(normalized_after.replace("Z", "+00:00"))
        parsed_before = datetime.fromisoformat(normalized_before.replace("Z", "+00:00"))
        if parsed_after >= parsed_before:
            raise ValueError("published_after must be earlier than published_before")
    return normalized_after, normalized_before


def _resolve_timeout(timeout: Optional[Any]) -> Tuple[float, float]:
    if timeout is None:
        return DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_READ_TIMEOUT_SECONDS
    if isinstance(timeout, (int, float)):
        return float(timeout), float(timeout)
    if isinstance(timeout, (tuple, list)) and len(timeout) == 2:
        return float(timeout[0]), float(timeout[1])
    raise ValueError("timeout must be seconds or a (connect, read) pair")


def _thumbnail(snippet: Mapping[str, Any]) -> str:
    thumbnails = snippet.get("thumbnails", {})
    if not isinstance(thumbnails, dict):
        return ""
    for size in ("maxres", "standard", "high", "medium", "default"):
        candidate = thumbnails.get(size, {})
        if isinstance(candidate, dict) and isinstance(candidate.get("url"), str):
            return candidate["url"]
    return ""


def _search_item(item: Any, rank: int, observed_at: str) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    identity = item.get("id", {})
    if not isinstance(identity, dict):
        return None
    video_id = identity.get("videoId")
    if not isinstance(video_id, str) or not video_id:
        return None
    snippet = item.get("snippet", {})
    if not isinstance(snippet, dict):
        snippet = {}
    return {
        "video_id": video_id,
        "title": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "channel_title": snippet.get("channelTitle", ""),
        "channel_id": snippet.get("channelId", ""),
        "published_at": snippet.get("publishedAt", ""),
        "thumbnail": _thumbnail(snippet),
        "url": f"https://youtube.com/watch?v={video_id}",
        "live_broadcast_content": snippet.get("liveBroadcastContent"),
        "search_rank": rank,
        "search_observed_at": observed_at,
        "provider": "youtube-data-api-v3",
    }


def _search_params(
    *,
    query: str,
    max_results: int,
    published_after: Optional[str],
    published_before: Optional[str],
    order: str,
    channel_id: Optional[str],
    region_code: Optional[str],
    relevance_language: Optional[str],
    safe_search: Optional[str],
    video_caption: Optional[str],
    video_category_id: Optional[str],
    video_definition: Optional[str],
    video_dimension: Optional[str],
    video_duration: Optional[str],
    video_embeddable: Optional[str],
    video_license: Optional[str],
    video_syndicated: Optional[str],
    video_type: Optional[str],
    video_paid_product_placement: Optional[str],
    event_type: Optional[str],
    location: Optional[str],
    location_radius: Optional[str],
    topic_id: Optional[str],
    page_token: Optional[str] = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "q": query,
        "part": "snippet",
        "type": "video",
        "maxResults": min(max_results, 50),
        "order": order,
    }
    optional = {
        "publishedAfter": published_after,
        "publishedBefore": published_before,
        "channelId": channel_id,
        "regionCode": region_code,
        "relevanceLanguage": relevance_language,
        "safeSearch": safe_search,
        "videoCaption": video_caption,
        "videoCategoryId": video_category_id,
        "videoDefinition": video_definition,
        "videoDimension": video_dimension,
        "videoDuration": video_duration,
        "videoEmbeddable": video_embeddable,
        "videoLicense": video_license,
        "videoSyndicated": video_syndicated,
        "videoType": video_type,
        "videoPaidProductPlacement": video_paid_product_placement,
        "eventType": event_type,
        "topicId": topic_id,
        "pageToken": page_token,
    }
    params.update({name: value for name, value in optional.items() if value is not None})
    if location:
        params["location"] = location
        params["locationRadius"] = location_radius or "50km"
    return params


def search_videos(
    query: str,
    max_results: int = 25,
    published_after: Optional[datetime] = None,
    published_before: Optional[datetime] = None,
    order: str = "date",
    channel_id: Optional[str] = None,
    region_code: Optional[str] = None,
    relevance_language: Optional[str] = None,
    safe_search: Optional[str] = None,
    video_caption: Optional[str] = None,
    video_category_id: Optional[str] = None,
    video_definition: Optional[str] = None,
    video_dimension: Optional[str] = None,
    video_duration: Optional[str] = None,
    video_embeddable: Optional[str] = None,
    video_license: Optional[str] = None,
    video_syndicated: Optional[str] = None,
    video_type: Optional[str] = None,
    event_type: Optional[str] = None,
    location: Optional[str] = None,
    location_radius: Optional[str] = None,
    topic_id: Optional[str] = None,
    video_paid_product_placement: Optional[str] = None,
    *,
    session: Optional[Any] = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: float = DEFAULT_READ_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    now: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Search one YouTube result page and return video rows."""
    validate_youtube_api()
    if max_results < 1:
        raise ValueError("max_results must be greater than zero")
    after, before = _validate_bounds(published_after, published_before)
    observed_at = _format_rfc3339(_clock_now(now))
    params = _search_params(
        query=query, max_results=max_results,
        published_after=after, published_before=before, order=order,
        channel_id=channel_id, region_code=region_code,
        relevance_language=relevance_language, safe_search=safe_search,
        video_caption=video_caption, video_category_id=video_category_id,
        video_definition=video_definition, video_dimension=video_dimension,
        video_duration=video_duration, video_embeddable=video_embeddable,
        video_license=video_license, video_syndicated=video_syndicated,
        video_type=video_type,
        video_paid_product_placement=video_paid_product_placement,
        event_type=event_type, location=location,
        location_radius=location_radius, topic_id=topic_id,
    )
    try:
        result = _request_json(
            YOUTUBE_SEARCH_URL, params, session=session,
            connect_timeout=connect_timeout, read_timeout=read_timeout,
            max_retries=max_retries, retry_backoff=retry_backoff, sleep=sleep,
        )
    except YouTubeAPIError:
        session = None
        raise
    videos: List[Dict[str, Any]] = []
    items = result.data.get("items", [])
    if not isinstance(items, list):
        return videos
    for item in items:
        parsed = _search_item(item, len(videos) + 1, observed_at)
        if parsed is not None:
            videos.append(parsed)
    return videos[:max_results]


def _optional_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return parsed if parsed >= 0 else None


def _copy_mapping(value: Any) -> Optional[Dict[str, Any]]:
    return dict(value) if isinstance(value, dict) else None


def _copy_list(value: Any) -> Optional[List[Any]]:
    return list(value) if isinstance(value, list) else None


def _video_detail(item: Any, observed_at: datetime) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    video_id = item.get("id")
    if not isinstance(video_id, str) or not video_id:
        return None
    snippet = item.get("snippet", {})
    statistics = item.get("statistics", {})
    content = item.get("contentDetails", {})
    status = item.get("status", {})
    live = item.get("liveStreamingDetails", {})
    paid = item.get("paidProductPlacementDetails", {})
    topics = item.get("topicDetails", {})
    snippet = snippet if isinstance(snippet, dict) else {}
    statistics = statistics if isinstance(statistics, dict) else {}
    content = content if isinstance(content, dict) else {}
    status = status if isinstance(status, dict) else {}
    live = live if isinstance(live, dict) else {}
    paid = paid if isinstance(paid, dict) else {}
    topics = topics if isinstance(topics, dict) else {}

    if "hasPaidProductPlacement" in paid:
        paid_value = paid.get("hasPaidProductPlacement")
        paid_placement = paid_value if isinstance(paid_value, bool) else None
        disclosure = (
            "declared"
            if paid_placement is True
            else "not_declared"
            if paid_placement is False
            else "not_observed"
        )
    else:
        paid_placement = None
        disclosure = "not_observed"

    return {
        "video_id": video_id,
        "title": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "channel_title": snippet.get("channelTitle", ""),
        "channel_id": snippet.get("channelId", ""),
        "published_at": snippet.get("publishedAt", ""),
        "thumbnail": _thumbnail(snippet),
        "thumbnails": _copy_mapping(snippet.get("thumbnails")),
        "url": f"https://youtube.com/watch?v={video_id}",
        "tags": _copy_list(snippet.get("tags")),
        "category_id": snippet.get("categoryId"),
        "default_language": snippet.get("defaultLanguage"),
        "default_audio_language": snippet.get("defaultAudioLanguage"),
        "live_broadcast_content": snippet.get("liveBroadcastContent"),
        "localized": _copy_mapping(snippet.get("localized")),
        "views": _optional_int(statistics.get("viewCount")),
        "likes": _optional_int(statistics.get("likeCount")),
        "favorites": _optional_int(statistics.get("favoriteCount")),
        "comments": _optional_int(statistics.get("commentCount")),
        "duration": content.get("duration"),
        "dimension": content.get("dimension"),
        "definition": content.get("definition"),
        "caption": content.get("caption"),
        "licensed_content": content.get("licensedContent"),
        "content_rating": _copy_mapping(content.get("contentRating")),
        "projection": content.get("projection"),
        "region_restriction": _copy_mapping(content.get("regionRestriction")),
        "upload_status": status.get("uploadStatus"),
        "failure_reason": status.get("failureReason"),
        "rejection_reason": status.get("rejectionReason"),
        "privacy_status": status.get("privacyStatus"),
        "scheduled_publish_at": status.get("publishAt"),
        "license": status.get("license"),
        "embeddable": status.get("embeddable"),
        "public_stats_viewable": status.get("publicStatsViewable"),
        "made_for_kids": status.get("madeForKids"),
        "self_declared_made_for_kids": status.get("selfDeclaredMadeForKids"),
        "contains_synthetic_media": status.get("containsSyntheticMedia"),
        "actual_start_time": live.get("actualStartTime"),
        "actual_end_time": live.get("actualEndTime"),
        "scheduled_start_time": live.get("scheduledStartTime"),
        "scheduled_end_time": live.get("scheduledEndTime"),
        "concurrent_viewers": _optional_int(live.get("concurrentViewers")),
        "active_live_chat_id": live.get("activeLiveChatId"),
        "paid_product_placement": paid_placement,
        "paid_product_placement_disclosure": disclosure,
        "topic_ids": _copy_list(topics.get("topicIds")),
        "relevant_topic_ids": _copy_list(topics.get("relevantTopicIds")),
        "topic_categories": _copy_list(topics.get("topicCategories")),
        "metadata_observed_at": _format_rfc3339(observed_at),
        "metadata_expires_at": _format_rfc3339(
            observed_at + timedelta(days=METADATA_TTL_DAYS)
        ),
        "observed_at": _format_rfc3339(observed_at),
        "expires_at": _format_rfc3339(
            observed_at + timedelta(days=METADATA_TTL_DAYS)
        ),
        "metadata_status": "observed",
        "provider": "youtube-data-api-v3",
    }


def _dedupe_ids(video_ids: Iterable[Any]) -> List[str]:
    output: List[str] = []
    seen = set()
    for value in video_ids:
        if not isinstance(value, str) or not value or value in seen:
            continue
        output.append(value)
        seen.add(value)
    return output


def _fetch_video_details(
    video_ids: Sequence[str],
    *,
    session: Optional[Any],
    connect_timeout: float,
    read_timeout: float,
    max_retries: int,
    retry_backoff: float,
    sleep: Callable[[float], None],
    now: Optional[Any],
    allow_partial: bool,
    batch_size: int = 50,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    unique_ids = _dedupe_ids(video_ids)
    observed_at = _clock_now(now)
    details: List[Dict[str, Any]] = []
    calls = 0
    completed_ids: List[str] = []
    error_data: Optional[Dict[str, Any]] = None
    malformed_items = 0
    batches_attempted = 0
    failed_batch_index: Optional[int] = None

    if isinstance(batch_size, bool) or not isinstance(batch_size, int):
        raise ValueError("YouTube detail batch_size must be an integer")
    if not 1 <= batch_size <= 50:
        raise ValueError("YouTube detail batch_size must be between 1 and 50")

    for offset in range(0, len(unique_ids), batch_size):
        batch = unique_ids[offset:offset + batch_size]
        batches_attempted += 1
        params = {
            "id": ",".join(batch),
            "part": ",".join(VIDEO_DETAIL_PARTS),
        }
        try:
            result = _request_json(
                YOUTUBE_VIDEOS_URL, params, session=session,
                connect_timeout=connect_timeout, read_timeout=read_timeout,
                max_retries=max_retries, retry_backoff=retry_backoff, sleep=sleep,
            )
        except YouTubeAPIError as error:
            calls += error.attempts
            if not allow_partial:
                session = None
                params = {}
                raise
            error_data = error.to_dict()
            failed_batch_index = batches_attempted
            break
        calls += result.attempts
        completed_ids.extend(batch)
        items = result.data.get("items", [])
        if not isinstance(items, list):
            items = []
            malformed_items += 1
        for item in items:
            parsed = _video_detail(item, observed_at)
            if parsed is None:
                malformed_items += 1
                continue
            details.append(parsed)

    found_ids = {row["video_id"] for row in details}
    missing_ids = [
        video_id for video_id in completed_ids if video_id not in found_ids
    ]
    unprocessed_ids = [
        video_id for video_id in unique_ids if video_id not in completed_ids
    ]
    return details, {
        "requested_count": len(unique_ids),
        "api_calls": calls,
        "api_attempts": calls,
        "batch_size": batch_size,
        "batches_planned": math.ceil(len(unique_ids) / batch_size)
        if unique_ids
        else 0,
        "batches_attempted": batches_attempted,
        "batches_completed": math.ceil(len(completed_ids) / batch_size)
        if completed_ids
        else 0,
        "failed_batch_index": failed_batch_index,
        "matched_count": len(found_ids),
        "missing_count": len(missing_ids),
        "missing_video_ids": missing_ids,
        "unprocessed_video_ids": unprocessed_ids,
        "completed_ids": completed_ids,
        "malformed_items_skipped": malformed_items,
        "error": error_data,
        "partial": len(found_ids) != len(unique_ids),
    }


def get_video_details(
    video_ids: List[str],
    *,
    session: Optional[Any] = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: float = DEFAULT_READ_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    now: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Return public metadata for all IDs, in batches of at most 50."""
    validate_youtube_api()
    if not video_ids:
        return []
    try:
        details, _ = _fetch_video_details(
            video_ids, session=session, connect_timeout=connect_timeout,
            read_timeout=read_timeout, max_retries=max_retries,
            retry_backoff=retry_backoff, sleep=sleep, now=now,
            allow_partial=False,
        )
    except YouTubeAPIError:
        session = None
        raise
    return details


def get_video_details_detailed(
    video_ids: Sequence[str],
    *,
    batch_size: int = 50,
    timeout: Optional[Any] = None,
    retries: Optional[int] = None,
    session: Optional[Any] = None,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    now: Optional[Any] = None,
) -> Dict[str, Any]:
    """Return ordered public video metadata with explicit lookup coverage.

    Input IDs are de-duplicated without changing first-occurrence order.  The
    ``videos`` list contains only resources returned by ``videos.list`` and is
    restored to that request order even if YouTube returns a different order.
    ``id_outcomes`` covers every unique requested ID and distinguishes a
    completed request that did not return an ID (``not_returned``) from an ID
    whose batch was never completed (``unprocessed``).  ``not_returned`` is a
    deliberately neutral transport observation; it does not assert that a
    video is deleted, private, invalid, or otherwise unavailable.

    A YouTube failure is returned in the credential-safe ``errors`` array and
    preserves metadata from earlier successful batches.  Configuration and
    argument errors still raise before any network request.  The legacy
    :func:`get_video_details` helper retains its original list/raise contract.
    """
    if isinstance(video_ids, (str, bytes)):
        raise TypeError("video_ids must be a sequence of ID strings")

    try:
        supplied_ids = list(video_ids)
    except TypeError:
        raise TypeError("video_ids must be a sequence of ID strings") from None

    unique_ids = _dedupe_ids(supplied_ids)
    invalid_ids_skipped = sum(
        1 for value in supplied_ids if not isinstance(value, str) or not value
    )
    valid_input_count = len(supplied_ids) - invalid_ids_skipped
    duplicate_ids_skipped = valid_input_count - len(unique_ids)

    if isinstance(batch_size, bool) or not isinstance(batch_size, int):
        raise ValueError("batch_size must be an integer")
    if not 1 <= batch_size <= 50:
        raise ValueError("batch_size must be between 1 and 50")
    if retries is None:
        max_retries = DEFAULT_MAX_RETRIES
    elif isinstance(retries, bool) or not isinstance(retries, int):
        raise ValueError("retries must be an integer")
    else:
        max_retries = retries
    if max_retries < 0:
        raise ValueError("retries cannot be negative")

    connect_timeout, read_timeout = _resolve_timeout(timeout)
    try:
        connect_timeout = float(connect_timeout)
        read_timeout = float(read_timeout)
        retry_backoff = float(retry_backoff)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(
            "timeout and retry_backoff values must be numeric"
        ) from None
    if (
        not math.isfinite(connect_timeout)
        or not math.isfinite(read_timeout)
        or connect_timeout <= 0
        or read_timeout <= 0
    ):
        raise ValueError("timeouts must be finite and greater than zero")
    if not math.isfinite(retry_backoff) or retry_backoff < 0:
        raise ValueError("retry_backoff must be finite and non-negative")

    requested_at_dt = _clock_now(now)
    requested_at = _format_rfc3339(requested_at_dt)
    batches_planned = (
        math.ceil(len(unique_ids) / batch_size) if unique_ids else 0
    )
    request = {
        "video_ids": unique_ids,
        "input_count": len(supplied_ids),
        "unique_id_count": len(unique_ids),
        "requested_at": requested_at,
        "parts": list(VIDEO_DETAIL_PARTS),
        "batch_size": batch_size,
        "batches_planned": batches_planned,
        "timeout": {
            "connect_seconds": connect_timeout,
            "read_seconds": read_timeout,
        },
        "retries": max_retries,
        "retry_backoff_seconds": retry_backoff,
    }

    if unique_ids:
        validate_youtube_api()
        details, metadata = _fetch_video_details(
            unique_ids,
            session=session,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            sleep=sleep,
            now=requested_at_dt,
            allow_partial=True,
            batch_size=batch_size,
        )
    else:
        details = []
        metadata = {
            "requested_count": 0,
            "api_calls": 0,
            "api_attempts": 0,
            "batch_size": batch_size,
            "batches_planned": 0,
            "batches_attempted": 0,
            "batches_completed": 0,
            "failed_batch_index": None,
            "matched_count": 0,
            "missing_count": 0,
            "missing_video_ids": [],
            "unprocessed_video_ids": [],
            "completed_ids": [],
            "malformed_items_skipped": 0,
            "error": None,
            "partial": False,
        }

    # YouTube normally returns ID-filtered resources in request order, but the
    # API contract does not require callers to depend on response ordering.
    # Keep the first usable row for each requested ID and deterministically
    # restore the exact first-occurrence request order here.
    requested_set = set(unique_ids)
    details_by_id: Dict[str, Dict[str, Any]] = {}
    unexpected_items = 0
    duplicate_response_items = 0
    for detail in details:
        video_id = detail.get("video_id")
        if video_id not in requested_set:
            unexpected_items += 1
            continue
        if video_id in details_by_id:
            duplicate_response_items += 1
            continue
        details_by_id[video_id] = detail
    ordered_videos = [
        details_by_id[video_id]
        for video_id in unique_ids
        if video_id in details_by_id
    ]

    completed_ids = set(metadata.pop("completed_ids"))
    matched_ids = [
        video_id for video_id in unique_ids if video_id in details_by_id
    ]
    missing_ids = [
        video_id
        for video_id in unique_ids
        if video_id in completed_ids and video_id not in details_by_id
    ]
    unprocessed_ids = [
        video_id for video_id in unique_ids if video_id not in completed_ids
    ]

    observed_at = requested_at if completed_ids else None
    expires_at = (
        _format_rfc3339(requested_at_dt + timedelta(days=METADATA_TTL_DAYS))
        if completed_ids
        else None
    )
    id_outcomes = []
    matched_set = set(matched_ids)
    missing_set = set(missing_ids)
    for video_id in unique_ids:
        if video_id in matched_set:
            status = "observed"
        elif video_id in missing_set:
            status = "not_returned"
        else:
            status = "unprocessed"
        id_outcomes.append({
            "video_id": video_id,
            "status": status,
            "observed_at": observed_at if status != "unprocessed" else None,
            "expires_at": expires_at if status != "unprocessed" else None,
        })

    malformed_items = (
        metadata["malformed_items_skipped"] + unexpected_items
    )
    error_data = metadata.get("error")
    partial = bool(missing_ids or unprocessed_ids)
    stopping_reason = (
        "empty"
        if not unique_ids
        else "partial_failure"
        if error_data is not None
        else "not_returned"
        if missing_ids
        else "completed"
    )
    coverage = {
        "requested_count": len(unique_ids),
        "matched_count": len(matched_ids),
        "missing_count": len(missing_ids),
        "unprocessed_count": len(unprocessed_ids),
        "matched_video_ids": matched_ids,
        "missing_video_ids": missing_ids,
        "unprocessed_video_ids": unprocessed_ids,
        "batch_size": batch_size,
        "batches_planned": batches_planned,
        "batches_attempted": metadata["batches_attempted"],
        "batches_completed": metadata["batches_completed"],
        "failed_batch_index": metadata["failed_batch_index"],
        "api_attempts": metadata["api_attempts"],
        "api_calls": metadata["api_attempts"],
        "malformed_items_skipped": malformed_items,
        "unexpected_items_skipped": unexpected_items,
        "duplicate_response_items_skipped": duplicate_response_items,
        "duplicate_input_ids_skipped": duplicate_ids_skipped,
        "invalid_input_ids_skipped": invalid_ids_skipped,
        "stopping_reason": stopping_reason,
        "partial": partial,
    }

    warnings: List[str] = []
    errors: List[Dict[str, Any]] = []
    if error_data is not None:
        errors.append({
            "stage": "video-details",
            "batch_index": metadata["failed_batch_index"],
            **error_data,
        })
        warnings.append(
            "Video metadata lookup stopped after a batch failed; completed "
            "batch results were preserved."
        )
    if missing_ids:
        warnings.append(
            "Some requested video IDs were not returned by videos.list; no "
            "availability reason was inferred."
        )
    if malformed_items:
        warnings.append("Malformed or unexpected video detail items were skipped.")
    if duplicate_response_items:
        warnings.append("Duplicate video detail items were skipped.")
    if duplicate_ids_skipped:
        warnings.append("Duplicate requested video IDs were de-duplicated in order.")
    if invalid_ids_skipped:
        warnings.append("Empty or non-string requested video IDs were skipped.")

    return {
        "provider": "youtube-data-api-v3",
        "videos": ordered_videos,
        "id_outcomes": id_outcomes,
        "request": request,
        "coverage": coverage,
        "observed_at": observed_at,
        "expires_at": expires_at,
        "warnings": warnings,
        "errors": errors,
        "api_calls": {
            "details": metadata["api_attempts"],
            "total": metadata["api_attempts"],
        },
    }


def _filters_dict(**values: Any) -> Dict[str, Any]:
    return {name: value for name, value in values.items() if value is not None}


def search_recent_detailed(
    query: str,
    days_back: int = 7,
    max_results: int = 25,
    order: str = "date",
    published_after: Optional[str] = None,
    published_before: Optional[str] = None,
    channel_id: Optional[str] = None,
    region_code: Optional[str] = None,
    relevance_language: Optional[str] = None,
    safe_search: Optional[str] = None,
    video_caption: Optional[str] = None,
    video_category_id: Optional[str] = None,
    video_definition: Optional[str] = None,
    video_dimension: Optional[str] = None,
    video_duration: Optional[str] = None,
    video_embeddable: Optional[str] = None,
    video_license: Optional[str] = None,
    video_syndicated: Optional[str] = None,
    video_type: Optional[str] = None,
    event_type: Optional[str] = None,
    location: Optional[str] = None,
    location_radius: Optional[str] = None,
    topic_id: Optional[str] = None,
    video_paid_product_placement: Optional[str] = None,
    *,
    max_pages: int = 1,
    page_token: Optional[str] = None,
    enrich: bool = True,
    timeout: Optional[Any] = None,
    retries: Optional[int] = None,
    now: Optional[Any] = None,
    session: Optional[Any] = None,
    sleep: Callable[[float], None] = time.sleep,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> Dict[str, Any]:
    """Search with bounded pagination, enrichment, and explicit coverage.

    A failure before the first usable search page raises
    :class:`YouTubeAPIError`. A later search-page failure or any enrichment
    failure is returned as structured partial state while preserving all rows
    already discovered.
    """
    validate_youtube_api()
    if max_results < 1:
        raise ValueError("max_results must be greater than zero")
    if days_back < 0:
        raise ValueError("days_back cannot be negative")
    if max_pages < 1:
        raise ValueError("max_pages must be greater than zero")
    connect_timeout, read_timeout = _resolve_timeout(timeout)
    max_retries = DEFAULT_MAX_RETRIES if retries is None else retries
    if max_retries < 0:
        raise ValueError("retries cannot be negative")

    requested_at_dt = _clock_now(now)
    requested_at = _format_rfc3339(requested_at_dt)
    if published_after is None:
        published_after = _format_rfc3339(
            requested_at_dt - timedelta(days=days_back)
        )
    normalized_after, normalized_before = _validate_bounds(
        published_after, published_before
    )

    common = dict(
        query=query, published_after=normalized_after,
        published_before=normalized_before, order=order, channel_id=channel_id,
        region_code=region_code, relevance_language=relevance_language,
        safe_search=safe_search, video_caption=video_caption,
        video_category_id=video_category_id,
        video_definition=video_definition, video_dimension=video_dimension,
        video_duration=video_duration, video_embeddable=video_embeddable,
        video_license=video_license, video_syndicated=video_syndicated,
        video_type=video_type,
        video_paid_product_placement=video_paid_product_placement,
        event_type=event_type, location=location,
        location_radius=location_radius, topic_id=topic_id,
    )
    videos: List[Dict[str, Any]] = []
    seen_ids = set()
    search_calls = 0
    pages_fetched = 0
    duplicates = 0
    malformed = 0
    next_page_token: Optional[str] = page_token
    page_info: Dict[str, Any] = {
        "approximate_total_results": None,
        "results_per_page": None,
    }
    search_error: Optional[Dict[str, Any]] = None
    stopping_reason = "page_budget"

    current_page_token = page_token
    for page_number in range(1, max_pages + 1):
        remaining = max_results - len(videos)
        params = _search_params(
            max_results=min(50, remaining),
            page_token=current_page_token,
            **common,
        )
        try:
            result = _request_json(
                YOUTUBE_SEARCH_URL, params, session=session,
                connect_timeout=connect_timeout, read_timeout=read_timeout,
                max_retries=max_retries, retry_backoff=retry_backoff, sleep=sleep,
            )
        except YouTubeAPIError as error:
            search_calls += error.attempts
            if not videos:
                session = None
                params = {}
                raise
            search_error = error.to_dict()
            stopping_reason = "partial_failure"
            break

        search_calls += result.attempts
        pages_fetched += 1
        raw_page_info = result.data.get("pageInfo", {})
        if isinstance(raw_page_info, dict):
            total = _optional_int(raw_page_info.get("totalResults"))
            per_page = _optional_int(raw_page_info.get("resultsPerPage"))
            if total is not None:
                page_info["approximate_total_results"] = total
            if per_page is not None:
                page_info["results_per_page"] = per_page

        items = result.data.get("items", [])
        if not isinstance(items, list):
            items = []
            malformed += 1
        for item in items:
            parsed = _search_item(item, len(videos) + 1, requested_at)
            if parsed is None:
                malformed += 1
                continue
            video_id = parsed["video_id"]
            if video_id in seen_ids:
                duplicates += 1
                continue
            seen_ids.add(video_id)
            videos.append(parsed)
            if len(videos) >= max_results:
                break

        token = result.data.get("nextPageToken")
        next_page_token = token if isinstance(token, str) and token else None
        if len(videos) >= max_results:
            stopping_reason = "result_budget"
            break
        if not next_page_token:
            stopping_reason = "exhausted"
            break
        if page_number >= max_pages:
            stopping_reason = "page_budget"
            break
        current_page_token = next_page_token

    detail_defaults = {
        "views": None,
        "likes": None,
        "favorites": None,
        "comments": None,
        "duration": None,
        "metadata_observed_at": None,
        "metadata_expires_at": None,
        "observed_at": None,
        "expires_at": None,
        "metadata_status": "not_requested",
        "paid_product_placement": None,
        "paid_product_placement_disclosure": "not_observed",
    }
    for video in videos:
        for name, value in detail_defaults.items():
            video.setdefault(name, value)

    enrichment: Dict[str, Any] = {
        "requested": 0,
        "api_calls": 0,
        "batches_completed": 0,
        "requested_count": 0,
        "matched_count": 0,
        "missing_count": 0,
        "error": None,
        "partial": False,
    }
    if enrich and videos:
        details, enrichment_meta = _fetch_video_details(
            [video["video_id"] for video in videos], session=session,
            connect_timeout=connect_timeout, read_timeout=read_timeout,
            max_retries=max_retries, retry_backoff=retry_backoff,
            sleep=sleep, now=requested_at_dt, allow_partial=True,
        )
        completed = set(enrichment_meta.pop("completed_ids"))
        details_by_id = {detail["video_id"]: detail for detail in details}
        for video in videos:
            video_id = video["video_id"]
            detail = details_by_id.get(video_id)
            rank = video["search_rank"]
            search_observed = video["search_observed_at"]
            if detail is not None:
                video.update(detail)
                video["search_rank"] = rank
                video["search_observed_at"] = search_observed
            elif video_id in completed:
                video["metadata_status"] = "missing"
            else:
                video["metadata_status"] = "enrichment_failed"
        enrichment = {
            "requested": enrichment_meta["requested_count"],
            **enrichment_meta,
        }

    filters = _filters_dict(
        channel_id=channel_id, region_code=region_code,
        relevance_language=relevance_language, safe_search=safe_search,
        video_caption=video_caption, video_category_id=video_category_id,
        video_definition=video_definition, video_dimension=video_dimension,
        video_duration=video_duration, video_embeddable=video_embeddable,
        video_license=video_license, video_syndicated=video_syndicated,
        video_type=video_type,
        video_paid_product_placement=video_paid_product_placement,
        event_type=event_type, location=location,
        location_radius=location_radius, topic_id=topic_id,
    )
    request = {
        "query": query,
        "requested_at": requested_at,
        "published_after": normalized_after,
        "published_before": normalized_before,
        "order": order,
        "days": days_back,
        "max_results": max_results,
        "max_pages": max_pages,
        "page_token": page_token,
        "filters": filters,
        "result_budget": max_results,
        "page_budget": max_pages,
        "initial_page_token": page_token,
        "timeout": {
            "connect_seconds": connect_timeout,
            "read_seconds": read_timeout,
        },
        "retries": max_retries,
    }
    coverage = {
        "pages_fetched": pages_fetched,
        "api_calls": search_calls,
        "search_calls": search_calls,
        "detail_calls": enrichment["api_calls"],
        "candidates_fetched": len(videos) + duplicates,
        "unique_results": len(videos),
        "unique_candidates": len(videos),
        "returned": len(videos),
        "duplicates_skipped": duplicates,
        "malformed_items_skipped": malformed,
        "next_page_token": next_page_token,
        "page_info": page_info,
        "approximate_total": page_info["approximate_total_results"],
        "stopping_reason": stopping_reason,
        "partial": search_error is not None or bool(enrichment.get("partial")),
    }
    if not enrichment["requested"]:
        enrichment["status"] = "not_requested"
    elif enrichment.get("error") is not None:
        enrichment["status"] = (
            "partial" if enrichment.get("matched_count") else "failed"
        )
    elif enrichment.get("missing_count"):
        enrichment["status"] = "partial"
    else:
        enrichment["status"] = "completed"
    observed = next(
        (
            row.get("metadata_observed_at")
            for row in videos
            if row.get("metadata_observed_at")
        ),
        None,
    )
    expires = next(
        (
            row.get("metadata_expires_at")
            for row in videos
            if row.get("metadata_expires_at")
        ),
        None,
    )
    enrichment["observed_at"] = observed
    enrichment["expires_at"] = expires
    enrichment["returned"] = enrichment.get("matched_count", 0)
    errors = []
    warnings = []
    if search_error is not None:
        errors.append({"stage": "search", **search_error})
        warnings.append("Search stopped after a later page failed; earlier results were preserved.")
    if enrichment.get("error") is not None:
        errors.append({"stage": "enrichment", **enrichment["error"]})
        warnings.append("Optional metadata enrichment failed; search results were preserved.")
    if enrichment.get("missing_count"):
        warnings.append("Some discovered videos were absent from the metadata response.")
    if malformed:
        warnings.append("Malformed or non-video search items were skipped.")
    return {
        "provider": "youtube-data-api-v3",
        "videos": videos,
        "request": request,
        "coverage": coverage,
        "enrichment": enrichment,
        "warnings": warnings,
        "errors": errors,
        "api_calls": {
            "search": search_calls,
            "details": enrichment["api_calls"],
            "total": search_calls + enrichment["api_calls"],
        },
    }


def search_recent(
    query: str,
    days_back: int = 7,
    max_results: int = 25,
    order: str = "date",
    published_after: Optional[str] = None,
    published_before: Optional[str] = None,
    channel_id: Optional[str] = None,
    region_code: Optional[str] = None,
    relevance_language: Optional[str] = None,
    safe_search: Optional[str] = None,
    video_caption: Optional[str] = None,
    video_category_id: Optional[str] = None,
    video_definition: Optional[str] = None,
    video_dimension: Optional[str] = None,
    video_duration: Optional[str] = None,
    video_embeddable: Optional[str] = None,
    video_license: Optional[str] = None,
    video_syndicated: Optional[str] = None,
    video_type: Optional[str] = None,
    event_type: Optional[str] = None,
    location: Optional[str] = None,
    location_radius: Optional[str] = None,
    topic_id: Optional[str] = None,
    video_paid_product_placement: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Compatibility wrapper returning only recent video rows."""
    return search_recent_detailed(
        query=query, days_back=days_back, max_results=max_results, order=order,
        published_after=published_after, published_before=published_before,
        channel_id=channel_id, region_code=region_code,
        relevance_language=relevance_language, safe_search=safe_search,
        video_caption=video_caption, video_category_id=video_category_id,
        video_definition=video_definition, video_dimension=video_dimension,
        video_duration=video_duration, video_embeddable=video_embeddable,
        video_license=video_license, video_syndicated=video_syndicated,
        video_type=video_type, event_type=event_type, location=location,
        location_radius=location_radius, topic_id=topic_id,
        video_paid_product_placement=video_paid_product_placement,
    )["videos"]


def format_duration(iso_duration: Optional[str]) -> str:
    """Convert an ISO 8601 duration to a compact human-readable value."""
    if not iso_duration:
        return ""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration)
    if not match:
        return iso_duration
    hours, minutes, seconds = match.groups()
    hours = int(hours) if hours else 0
    minutes = int(minutes) if minutes else 0
    seconds = int(seconds) if seconds else 0
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"
