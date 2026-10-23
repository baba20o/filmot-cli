"""Filmot API client - Core API interactions."""

import re
import time
import logging
import requests
from typing import Optional, Dict, Any, List, Generator, Tuple
from . import api_usage, config
from .api_contract import FilmotAPIContractError, validate_api_response
from .api_providers import (
    Provider,
    configured_providers,
    configured_secrets,
    providers_for,
)
from .cache import get_cache
from .rate_limiter import get_rate_limiter
from .redaction import redact_sensitive_text

logger = logging.getLogger(__name__)

# Retry config for 429 responses
MAX_RETRIES = 4
DEFAULT_RETRY_WAIT = 5  # seconds, if no Retry-After header
MAX_RETRY_AFTER = 60  # seconds; a provider's Retry-After never waits longer
DEFAULT_REQUEST_TIMEOUT = (10, 45)  # connect, read seconds

_ignored_settings_reported = False


def _report_ignored_project_settings() -> None:
    """Say once per process that a project .env tried to set protected settings."""
    global _ignored_settings_reported
    if config.IGNORED_PROJECT_SETTINGS and not _ignored_settings_reported:
        _ignored_settings_reported = True
        logger.warning(
            "Ignored %s from the project .env: API endpoints, the per-user "
            "config location, and TLS settings come only from the environment "
            "or the per-user config.",
            ", ".join(config.IGNORED_PROJECT_SETTINGS),
        )

# Every non-subtitle /getvideos column. Raw subtitles are requested separately
# because selecting them caps a batch at 10 IDs instead of 200.
VIDEO_METADATA_COLUMNS = (
    "description", "category", "viewcount", "likecount", "autosubs",
    "manualsubs", "isunlisted", "islivestream", "license", "ispartialdata",
    "lastfetchdate", "channelsubcount", "channelcountryname",
)
VIDEO_BATCH_LIMIT = 200

# Canonical channel-suggestion fields follow the direct API; the RapidAPI
# gateway returns the same values under legacy names.
_CHANNEL_FIELD_ALIASES = (
    ("channelid", ("channelid", "value", "id")),
    ("name", ("name", "label", "title")),
    ("handle", ("handle", "newshortname")),
    ("totalviewcount", ("totalviewcount", "viewcount")),
)


def _rewrite_pipe_phrase_operand(operand: str) -> tuple[str, bool]:
    """Rewrite a quoted pipe phrase into grouped OR syntax.

    Example:
        '"memory|context"' -> '("memory" | "context")'
    """
    match = re.fullmatch(r'\s*"([^"]*\|[^"]*)"\s*', operand)
    if not match:
        return operand, False

    parts = [part.strip() for part in match.group(1).split("|") if part.strip()]
    if len(parts) < 2:
        return operand, False

    rewritten = "(" + " | ".join(f'"{part}"' for part in parts) + ")"
    return rewritten, True


def _rewrite_quoted_or_in_proximity_query(query: str) -> tuple[str, Optional[Dict[str, str]]]:
    """Rewrite unsupported quoted-pipe NEAR/NOTNEAR operands into grouped OR syntax."""
    match = re.match(r'^\s*(.+?)\s+((?:NOT)?NEAR\s*/\s*\d+)\s+(.+?)\s*$', query, re.IGNORECASE)
    if not match:
        return query, None

    left_operand, operator, right_operand = match.groups()
    left_rewritten, left_changed = _rewrite_pipe_phrase_operand(left_operand)
    right_rewritten, right_changed = _rewrite_pipe_phrase_operand(right_operand)

    if not (left_changed or right_changed):
        return query, None

    rewritten_query = f"{left_rewritten} {operator} {right_rewritten}"
    return rewritten_query, {"from": query, "to": rewritten_query}


def normalize_channel_suggestion(item: Any) -> Any:
    """Add canonical direct-API channel fields while keeping provider fields.

    Idempotent: records that already carry canonical fields are unchanged.
    """
    if not isinstance(item, dict):
        return item
    normalized = dict(item)
    for canonical, aliases in _CHANNEL_FIELD_ALIASES:
        if normalized.get(canonical) not in (None, ""):
            continue
        for alias in aliases:
            if item.get(alias) not in (None, ""):
                normalized[canonical] = item[alias]
                break
    return normalized


def normalize_channel_suggestions(payload: Any) -> Any:
    """Normalize a channel-search list or envelope; errors pass through."""
    if isinstance(payload, list):
        return [normalize_channel_suggestion(item) for item in payload]
    if isinstance(payload, dict) and "error" not in payload:
        for key in ("channels", "items", "result"):
            if isinstance(payload.get(key), list):
                envelope = dict(payload)
                envelope[key] = [normalize_channel_suggestion(item) for item in payload[key]]
                return envelope
    return payload


def _known_secrets() -> Tuple[str, ...]:
    """Every configured credential, so no response or error can echo any key."""
    return configured_secrets()


def _scrub_secrets(value: Any, secrets: Tuple[str, ...]) -> Any:
    """Replace configured credential values anywhere in response-shaped data.

    Only literal key values are replaced. Field names are left alone because
    API payloads legitimately use names such as ``token`` for matched phrases.
    """
    if not secrets:
        return value
    if isinstance(value, str):
        for secret in secrets:
            if secret in value:
                value = value.replace(secret, "***")
        return value
    if isinstance(value, dict):
        return {
            _scrub_secrets(key, secrets): _scrub_secrets(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub_secrets(item, secrets) for item in value]
    return value


def _json_body(response) -> Optional[Dict[str, Any]]:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _bounded_text(value: str, limit: int = 300) -> str:
    """Redact the complete text before truncating, so no key is split unseen."""
    return _scrub_secrets(value, _known_secrets())[:limit]


def _response_message(response) -> str:
    """Return a short API-supplied message; never an HTML page."""
    body = _json_body(response) or {}
    message = body.get("message") or body.get("error")
    return _bounded_text(message) if isinstance(message, str) else ""


def _quota_count(value: Any) -> Optional[int]:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _quota_details(response) -> Optional[Dict[str, Any]]:
    """Recognize credit or plan exhaustion, which retrying cannot fix."""
    body = _json_body(response) or {}
    if body.get("error") == "quota_exhausted":
        details: Dict[str, Any] = {}
        for key in ("cost", "used", "limit", "remaining"):
            count = _quota_count(body.get(key))
            if count is not None:
                details[key] = count
        reset_at = body.get("reset_at")
        if isinstance(reset_at, str) and api_usage.valid_timestamp(reset_at.strip()):
            details["reset_at"] = reset_at.strip()
        return details
    message = body.get("message")
    if isinstance(message, str) and "quota" in message.casefold():
        return {"message": _bounded_text(message)}
    return None


class Deadline:
    """A point on the monotonic clock after which no request work starts.

    It is fixed when created, so time spent before a request (client setup, a
    cache lookup) counts against it. ``cancel()`` moves it to now, letting a
    caller that stops waiting also stop work it left running in a thread.
    """

    def __init__(self, seconds: float):
        self.seconds = seconds
        self._at = time.monotonic() + seconds

    def remaining(self) -> float:
        return self._at - time.monotonic()

    def cancel(self) -> None:
        self._at = float("-inf")


class FilmotClient:
    """Client for interacting with the Filmot API through configured providers."""

    def __init__(self, use_cache: bool = True, cache_ttl: int = 3600,
                 rate_limit: float = 2.0, burst_size: int = 5,
                 request_timeout=DEFAULT_REQUEST_TIMEOUT,
                 deadline: Optional[Deadline] = None):
        """
        Initialize the Filmot client.

        Args:
            use_cache: Enable response caching
            cache_ttl: Cache time-to-live in seconds
            rate_limit: Requests per second limit
            burst_size: Maximum burst of requests
            deadline: Optional Deadline for every request call: socket
                timeouts are capped to what remains, and no request,
                Retry-After wait, or failover starts past it. A read timeout
                limits silence, not total time, so a caller needing a hard
                bound must also stop waiting at the deadline
        """
        self.deadline = deadline
        # Fail before any work when no provider can serve the selected policy.
        configured_providers(require=True)
        _report_ignored_project_settings()
        self.session = requests.Session()

        # Optional caching
        self.use_cache = use_cache
        self.cache = get_cache(ttl=cache_ttl) if use_cache else None

        # Rate limiting
        self.rate_limiter = get_rate_limiter(rate_limit, burst_size)
        self.request_timeout = request_timeout

        # Track cache hits and the serving provider for caller feedback
        self.last_cache_hit = False
        self.last_provider = None
        self.last_query_rewrite = None

    def _request(self, method: str, endpoint: str, params: Optional[Dict] = None,
                 data: Optional[Dict] = None, skip_cache: bool = False) -> Any:
        """Make an API request with caching, rate limiting, retries, and failover."""
        providers = providers_for(endpoint)

        # Remove None values from params
        if params:
            params = {k: v for k, v in params.items() if v is not None}
        else:
            params = {}

        # Check cache first (only for GET requests)
        self.last_cache_hit = False
        self.last_provider = None
        if method == "GET" and self.use_cache and self.cache and not skip_cache:
            for provider in providers:
                cached = self.cache.get(endpoint, params, namespace=provider.name)
                if cached is None:
                    continue
                cached = _scrub_secrets(cached, _known_secrets())
                try:
                    validate_api_response(endpoint, cached)
                except FilmotAPIContractError as contract_error:
                    logger.error("%s", contract_error)
                    return contract_error.as_response()
                self.last_cache_hit = True
                self.last_provider = provider.name
                api_usage.record_cache_hit(provider.name)
                return cached

        result: Any = None
        for index, provider in enumerate(providers):
            result, failover_reason = self._request_provider(
                provider, method, endpoint, params, data,
            )
            self.last_provider = provider.name
            if (
                failover_reason is None
                or index + 1 == len(providers)
                or (self.deadline is not None and self.deadline.remaining() <= 0)
            ):
                return result
            following = providers[index + 1]
            api_usage.record_failover(endpoint, provider.name, following.name, failover_reason)
            logger.warning(
                "%s failed on %s (%s); retrying with the %s",
                provider.label, endpoint, failover_reason, following.label,
            )
        return result

    def _request_provider(
        self,
        provider: Provider,
        method: str,
        endpoint: str,
        params: Dict[str, Any],
        data: Optional[Dict],
    ) -> Tuple[Any, Optional[str]]:
        """Return ``(result, failover_reason)``; the reason is None when final."""
        url = provider.url(endpoint)
        deadline = self.deadline
        for attempt in range(MAX_RETRIES + 1):
            if deadline is not None and deadline.remaining() <= 0:
                return self._past_deadline(provider), "connection"
            # Apply rate limiting
            self.rate_limiter.acquire()

            timeout = self.request_timeout
            if deadline is not None:
                # The rate limiter may have waited past the deadline.
                remaining = deadline.remaining()
                if remaining <= 0:
                    return self._past_deadline(provider), "connection"
                timeout = (
                    tuple(min(part, remaining) for part in timeout)
                    if isinstance(timeout, tuple) else min(timeout, remaining)
                )

            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=data,
                    headers=provider.headers(),
                    timeout=timeout,
                    # requests strips only Authorization on cross-origin
                    # redirects; custom key headers would follow a Location
                    # to any host, so authenticated calls never redirect.
                    allow_redirects=False,
                )
            except requests.exceptions.RequestException as e:
                # Count the attempt even though no response (or credit) arrived.
                api_usage.record_transport_error(provider.name)
                message = redact_sensitive_text(e, _known_secrets())
                return self._error(
                    provider, "FilmotConnectionError",
                    "Could not reach the {}: {}".format(provider.label, message),
                ), "connection"

            api_usage.record_response(provider.name, getattr(response, "headers", None))
            status = getattr(response, "status_code", None)
            if isinstance(status, int) and 300 <= status < 400:
                return self._error(
                    provider, "FilmotResponseError",
                    "The {} answered with an unexpected redirect (HTTP {}); the "
                    "request was not forwarded.".format(provider.label, status),
                    status,
                ), "invalid-response"
            try:
                response.raise_for_status()
            except requests.exceptions.HTTPError:
                status = response.status_code
                if status == 429:
                    quota = _quota_details(response)
                    if quota is not None:
                        return self._quota_error(provider, quota), "quota"
                    if hasattr(self.rate_limiter, 'report_rate_limit'):
                        self.rate_limiter.report_rate_limit()

                    if attempt < MAX_RETRIES:
                        # Retry-After in whole seconds; anything else, such as
                        # an HTTP date, waits DEFAULT_RETRY_WAIT.
                        retry_after = response.headers.get("Retry-After")
                        if retry_after:
                            try:
                                # A negative value would crash sleep(); a huge
                                # one would hang the command.
                                wait = min(max(int(retry_after), 0), MAX_RETRY_AFTER)
                            except ValueError:
                                wait = DEFAULT_RETRY_WAIT
                        else:
                            # Exponential backoff: 5, 10, 20, 40
                            wait = DEFAULT_RETRY_WAIT * (2 ** attempt)
                        if deadline is not None and wait >= deadline.remaining():
                            # Waiting would overrun the deadline; give up now.
                            return self._error(
                                provider, "FilmotRateLimited",
                                "The {} is rate limiting {}, and waiting would pass "
                                "the {:g}s deadline.".format(
                                    provider.label, endpoint, deadline.seconds,
                                ),
                                status,
                            ), "rate-limited"

                        logger.warning(
                            "Rate limited (429) on %s by the %s — waiting %ds before retry %d/%d",
                            endpoint, provider.label, wait, attempt + 1, MAX_RETRIES,
                        )
                        time.sleep(wait)
                        continue

                    logger.error("Rate limited (429) on %s — exhausted %d retries", endpoint, MAX_RETRIES)
                    return self._error(
                        provider, "FilmotRateLimited",
                        "The {} is still rate limiting {} after {} retries.".format(
                            provider.label, endpoint, MAX_RETRIES,
                        ),
                        status,
                    ), "rate-limited"
                if status in (401, 403):
                    return self._error(
                        provider, "FilmotAuthenticationError",
                        "The {} rejected its credentials (HTTP {}). Check {}.".format(
                            provider.label, status, provider.key_env,
                        ),
                        status,
                    ), "auth"
                if status == 404:
                    return self._error(
                        provider, "FilmotEndpointUnavailable",
                        "{} is not available from the {} (HTTP 404).".format(
                            endpoint, provider.label,
                        ),
                        status,
                    ), "unavailable"
                detail = _response_message(response)
                suffix = ": {}".format(detail) if detail else ""
                if status >= 500:
                    return self._error(
                        provider, "FilmotServiceError",
                        "The {} returned HTTP {}{}.".format(provider.label, status, suffix),
                        status,
                    ), "server-error"
                return self._error(
                    provider, "FilmotRequestError",
                    "The {} rejected the request (HTTP {}){}.".format(
                        provider.label, status, suffix,
                    ),
                    status,
                ), None

            try:
                result = response.json()
            except ValueError:
                return self._error(
                    provider, "FilmotResponseError",
                    "The {} returned a response that is not JSON.".format(provider.label),
                ), "invalid-response"
            # Response bodies reach raw output, the ledger, and the cache.
            result = _scrub_secrets(result, _known_secrets())
            try:
                validate_api_response(endpoint, result)
            except FilmotAPIContractError as contract_error:
                logger.error("%s", contract_error)
                return contract_error.as_response(), None

            # Report success to adaptive rate limiter
            if hasattr(self.rate_limiter, 'report_success'):
                self.rate_limiter.report_success()

            # Cache successful responses
            if (
                method == "GET"
                and self.use_cache
                and self.cache
                and not (
                    isinstance(result, dict)
                    and "error" in result
                )
            ):
                self.cache.set(endpoint, params, result, namespace=provider.name)

            if attempt > 0:
                logger.info("Request succeeded after %d retries: %s", attempt, endpoint)

            return result, None

        return self._error(
            provider, "FilmotRateLimited", "max retries exceeded",
        ), "rate-limited"

    @staticmethod
    def _error(
        provider: Provider,
        error_type: str,
        message: str,
        status_code: Optional[int] = None,
        **details: Any,
    ) -> Dict[str, Any]:
        """Build a credential-free error response naming the provider."""
        secrets = _known_secrets()
        result: Dict[str, Any] = {
            "error": redact_sensitive_text(message, secrets),
            "error_type": error_type,
            "provider": provider.name,
        }
        if isinstance(status_code, int) and not isinstance(status_code, bool):
            result["status_code"] = status_code
        result.update(_scrub_secrets(details, secrets))
        return result

    def _past_deadline(self, provider: Provider) -> Dict[str, Any]:
        return self._error(
            provider, "FilmotTimeout",
            "The {} did not answer within {:g}s.".format(provider.label, self.deadline.seconds),
        )

    def _quota_error(self, provider: Provider, quota: Dict[str, Any]) -> Dict[str, Any]:
        if "message" in quota:
            message = "The {} quota is exhausted: {}".format(provider.label, quota["message"])
        else:
            message = "The {} has no credits left (used {} of {}; resets {}).".format(
                provider.label,
                quota.get("used", "?"),
                quota.get("limit", "?"),
                quota.get("reset_at", "unknown"),
            )
        return self._error(provider, "FilmotQuotaExhausted", message, 429, quota=quota)

    def get(self, endpoint: str, params: Optional[Dict] = None, skip_cache: bool = False) -> Any:
        """Make a GET request."""
        return self._request("GET", endpoint, params=params, skip_cache=skip_cache)

    # ========== API ENDPOINTS ==========

    def search_channels(self, term: str) -> Any:
        """
        Find YouTube Channels by name or handle.

        Args:
            term: Channel name or handle to search for (e.g., "mrbeast")

        Returns:
            List of matching channels carrying canonical ``channelid``, ``name``,
            ``handle``, and ``totalviewcount`` fields from either provider
        """
        return normalize_channel_suggestions(
            self.get("/getsearchchannels", params={"term": term})
        )

    def get_videos(
        self,
        video_ids: str,
        flags: Optional[int] = None,
        columns: Optional[Any] = None,
        lang: Optional[str] = None,
    ) -> Any:
        """
        Get metadata for a single video or list of videos.

        Args:
            video_ids: Single video ID or comma-separated list (e.g., "dQw4w9WgXcQ")
            flags: Optional legacy flags parameter
            columns: Optional extra fields, as a list or comma-separated string.
                Either provider accepts ``VIDEO_METADATA_COLUMNS``; requesting
                columns costs 2 direct-API credits instead of 1.
            lang: Optional subtitle base-language token

        Returns:
            Video metadata
        """
        params = {"id": video_ids}
        if flags is not None:
            params["flags"] = flags
        if columns:
            params["columns"] = columns if isinstance(columns, str) else ",".join(columns)
        if lang:
            params["lang"] = lang
        return self.get("/getvideos", params=params)

    def search_subtitles(
        self,
        query: str,
        lang: Optional[str] = None,
        page: Optional[int] = None,
        category: Optional[str] = None,
        exclude_category: Optional[str] = None,
        license_type: Optional[int] = None,
        min_views: Optional[int] = None,
        max_views: Optional[int] = None,
        min_likes: Optional[int] = None,
        max_likes: Optional[int] = None,
        country: Optional[int] = None,
        channel_id: Optional[str] = None,
        title: Optional[str] = None,
        start_duration: Optional[int] = None,
        end_duration: Optional[int] = None,
        search_manual_subs: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sort_field: Optional[str] = None,
        sort_order: Optional[str] = None,
        max_query_time: Optional[int] = None,
        hit_format: Optional[int] = None,
        channel: Optional[str] = None,
        channel_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Find YouTube Videos by content of subtitles and other parameters.

        Args:
            query: Text to find in subtitles. Phrases can be enclosed in double quotes.
            lang: Two-letter language code (e.g., "en", "nl", "fr", "de")
            page: Page number (default 1, max 50 results per page)
            category: Video category (e.g., "Science & Technology", "Gaming")
            exclude_category: Comma-delimited categories to exclude (e.g., "Music,Gaming")
            license_type: 1=Standard YouTube, 2=Creative Commons
            min_views: Minimum view count
            max_views: Maximum view count
            min_likes: Minimum like count
            max_likes: Maximum like count
            country: Country code (integer, e.g., 153=UK, 217=US)
            channel_id: Limit search to specific channel ID(s). Accepts multiple comma-delimited IDs.
            title: Filter by video title
            start_duration: Minimum video duration in seconds
            end_duration: Maximum video duration in seconds
            search_manual_subs: 0=auto subtitles (default), 1=manual subtitles only. Cannot search both.
            start_date: Start date filter (yyyy-mm-dd)
            end_date: End date filter (yyyy-mm-dd)
            sort_field: Sort by field (e.g., "viewcount")
            sort_order: Sort order ("asc" or "desc")
            max_query_time: Max query time in ms (4-15000)
            hit_format: Format of hits array (0 or 1)
            channel: Find top N channels matching text, then search those
            channel_count: Limit top channels when using channel param (default 10)

        Returns:
            Search results with matching videos and subtitle hits
        """
        query, rewrite = _rewrite_quoted_or_in_proximity_query(query)
        self.last_query_rewrite = rewrite

        params = {
            "query": query,
            "lang": lang,
            "page": page,
            "category": category,
            "excludeCategory": exclude_category,
            "license": license_type,
            "minViews": min_views,
            "maxViews": max_views,
            "minLikes": min_likes,
            "maxLikes": max_likes,
            "country": country,
            "channelID": channel_id,
            "title": title,
            "startDuration": start_duration,
            "endDuration": end_duration,
            "searchManualSubs": search_manual_subs,
            "startDate": start_date,
            "endDate": end_date,
            "sortField": sort_field,
            "sortOrder": sort_order,
            "maxQueryTime": max_query_time,
            "hitFormat": hit_format,
            "channel": channel,
            "channelCount": channel_count,
        }
        return self.get("/getsearchsubtitles", params=params)

    def search_subtitles_paginated(
        self,
        query: str,
        max_pages: int = 10,
        max_results: Optional[int] = None,
        **kwargs
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Search subtitles with automatic pagination.

        Yields results page by page until max_pages or max_results is reached.

        Args:
            query: Search query
            max_pages: Maximum number of pages to fetch (default 10)
            max_results: Maximum total results to return (optional)
            **kwargs: All other search_subtitles parameters

        Yields:
            Each page of results as a dictionary
        """
        total_results = 0
        kwargs.pop("page", None)

        for page_num in range(1, max_pages + 1):
            result = self.search_subtitles(query, page=page_num, **kwargs)

            if "error" in result:
                yield result
                break

            videos = result.get("result", result.get("videos", []))
            if not videos:
                # The request still consumed a page and may carry an accurate
                # total count. Yield it so aggregate scope does not misleadingly
                # report that zero pages were fetched.
                yield result
                break  # No more results

            yield result

            total_results += len(videos)

            # Check if we've reached max_results
            if max_results and total_results >= max_results:
                break

            # Check if we've fetched all available results. If the API omits
            # totalresultcount, fall back to stopping on a short page rather
            # than always stopping after page 1.
            total_available = result.get("totalresultcount")
            if total_available is not None:
                if total_results >= total_available:
                    break
            elif len(videos) < 50:
                break

    def search_subtitles_all(
        self,
        query: str,
        max_pages: int = 10,
        max_results: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Search subtitles and aggregate all pages into a single result.

        Args:
            query: Search query
            max_pages: Maximum number of pages to fetch (default 10)
            max_results: Maximum total results to return (optional)
            **kwargs: All other search_subtitles parameters

        Returns:
            Aggregated results with all videos combined
        """
        all_videos = []
        seen_video_ids = set()
        duplicates_skipped = 0
        total_count = 0
        pages_fetched = 0
        page_error = None

        # Enforce max_results on distinct IDs here. Passing it into the raw
        # paginator could stop early when a changing index repeats a video
        # across adjacent pages.
        for page_result in self.search_subtitles_paginated(
            query,
            max_pages,
            None,
            **kwargs,
        ):
            if "error" in page_result:
                if all_videos:
                    # Preserve partial work, but make incompleteness explicit to
                    # callers instead of silently presenting a complete corpus.
                    page_error = page_result.get("error")
                    break
                return page_result

            videos = page_result.get("result", page_result.get("videos", []))
            for video in videos:
                video_id = video.get("id") or video.get("videoid")
                if video_id and video_id in seen_video_ids:
                    duplicates_skipped += 1
                    continue
                if video_id:
                    seen_video_ids.add(video_id)
                all_videos.append(video)
            total_count = page_result.get("totalresultcount", len(all_videos))
            pages_fetched += 1

            # Trim to max_results if specified
            if max_results and len(all_videos) >= max_results:
                all_videos = all_videos[:max_results]
                break

        aggregated = {
            "result": all_videos,
            "totalresultcount": total_count,
            "pages_fetched": pages_fetched,
            "results_returned": len(all_videos),
            "duplicates_skipped": duplicates_skipped,
            "partial": page_error is not None,
        }
        if page_error is not None:
            aggregated["page_error"] = page_error
        return aggregated
