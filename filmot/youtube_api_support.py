"""Shared safety and pagination primitives for YouTube Data API providers.

The public provider modules deliberately keep their endpoint-specific parsing
and policy decisions separate.  This module owns the small, repetitive layer
that every bounded API-key reader needs: validated controls, credential-free
exceptions, request options, page metadata, and opaque continuation tokens.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from .youtube_search import (
    DEFAULT_MAX_RETRIES,
    YouTubeAPIError,
    _optional_int,
    _resolve_timeout,
)


def _detached_youtube_error(error: YouTubeAPIError) -> YouTubeAPIError:
    """Copy only bounded scalar diagnostics into a fresh public exception."""
    category = str(getattr(error, "category", "unknown"))
    if not re.fullmatch(r"[a-z_]{1,40}", category):
        category = "unknown"
    reason = str(getattr(error, "reason", "requestFailed"))
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", reason):
        reason = "requestFailed"
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        status_code = None
    attempts = getattr(error, "attempts", 1)
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        attempts = 1
    status = ", HTTP {}".format(status_code) if status_code is not None else ""
    return YouTubeAPIError(
        "YouTube API {} error ({}{})".format(category, reason, status),
        category=category,
        reason=reason,
        status_code=status_code,
        retryable=bool(getattr(error, "retryable", False)),
        attempts=attempts,
    )


def _unexpected_provider_error(resource: str) -> YouTubeAPIError:
    """Return a generic failure retaining no unexpected exception material."""
    return YouTubeAPIError(
        "YouTube API client failed before a safe {} response was available".format(
            resource
        ),
        category="unknown",
        reason="unexpectedException",
        retryable=False,
        attempts=0,
    )


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
    """Validate every generic control-plane value before an API request."""
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


def _invalid_response(
    endpoint: str,
    reason: str,
    *,
    attempts: int = 1,
) -> YouTubeAPIError:
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        attempts = 1
    return YouTubeAPIError(
        "YouTube API invalid response ({}) at {}".format(reason, endpoint),
        category="invalid_response",
        reason="malformedResponse",
        retryable=False,
        attempts=attempts,
    )


def _error_row(
    error: YouTubeAPIError, *, stage: str, page: int
) -> Dict[str, Any]:
    # Partial-result paths serialize this row instead of raising through the
    # provider's public detachment wrapper. Detach here so no caller can
    # accidentally retain a transport message or attached request state.
    safe_error = _detached_youtube_error(error)
    return {"stage": stage, "page": page, **safe_error.to_dict()}


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
    if not isinstance(value, str) or not value.strip():
        return None, False
    return value, True
