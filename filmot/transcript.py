"""
YouTube transcript downloader for deep content analysis.

This module allows fetching full transcripts from YouTube videos,
enabling AI agents to go beyond search snippets and truly understand
video content.
"""

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
)
from typing import Any, Callable, Optional
import queue
import re
import os
import threading
import time
import requests
from dotenv import load_dotenv

from .proxy_pool import (
    WebshareProxyError,
    classify_transport_error,
    get_pool,
    redact_sensitive_text,
)

load_dotenv()

# Default number of pool sessions to try on transport-class failures.
_POOL_RETRY_LIMIT = int(os.getenv("FILMOT_PROXY_RETRY_LIMIT", "4"))

# Every individual HTTP operation gets a connect/read deadline, and every
# complete route attempt gets a wall-clock budget. The route budget matters
# because one transcript fetch may issue several HTTP requests (fetch, list,
# translation) and requests' read timeout is not an overall deadline.
DEFAULT_CONNECT_TIMEOUT = 8.0
DEFAULT_READ_TIMEOUT = 15.0
DEFAULT_ROUTE_TIMEOUT = 30.0

# Webshare gateway for the legacy username/password path.
_WEBSHARE_GATEWAY = os.getenv("WEBSHARE_GATEWAY", "p.webshare.io:80")
# WebshareProxyConfig appends a "-rotate" suffix to the username for the
# rotating-residential endpoint. Static-session Webshare plans reject that
# endpoint with "Tunnel connection failed: 400". Default to the bare-username
# gateway (which those plans accept); set FILMOT_WEBSHARE_ROTATE=1 to opt back
# into the library's rotating behavior.
_WEBSHARE_ROTATE = os.getenv("FILMOT_WEBSHARE_ROTATE", "0").strip().lower() in ("1", "true", "yes")

# Global API instance - holds the "primary" non-pool client (direct, legacy
# Webshare via env vars, or an operator-supplied proxy from configure_proxy()).
# The dynamic Webshare pool is layered on top by get_transcript().
_api = None
_proxy_configured = False
_initialized = False
_proxy_source = "direct"
_primary_override: Optional[dict] = None


class TranscriptRouteTimeout(TimeoutError):
    """Raised when one route exceeds its complete wall-clock budget."""

    def __init__(self, route: str, timeout_seconds: float) -> None:
        self.route = route
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Transcript route {route!r} timed out after "
            f"{timeout_seconds:g} seconds"
        )


class _BoundedSession(requests.Session):
    """Requests session that supplies connect/read timeouts by default."""

    def __init__(self, connect_timeout: float, read_timeout: float) -> None:
        super().__init__()
        # Proxy routes are resolved explicitly below. Leaving Requests'
        # environment inheritance enabled would make a route labelled
        # ``direct`` silently honor HTTP(S)_PROXY, breaking --no-proxy.
        self.trust_env = False
        self._filmot_timeout = (connect_timeout, read_timeout)

    def request(self, method, url, **kwargs):  # noqa: ANN001 - requests signature
        kwargs.setdefault("timeout", self._filmot_timeout)
        return super().request(method, url, **kwargs)


def _env_timeout(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _new_http_session() -> requests.Session:
    return _BoundedSession(
        _env_timeout("FILMOT_TRANSCRIPT_CONNECT_TIMEOUT", DEFAULT_CONNECT_TIMEOUT),
        _env_timeout("FILMOT_TRANSCRIPT_READ_TIMEOUT", DEFAULT_READ_TIMEOUT),
    )


def _build_direct_api() -> YouTubeTranscriptApi:
    """Build a direct YouTubeTranscriptApi client."""
    return YouTubeTranscriptApi(http_client=_new_http_session())


def _build_webshare_api(proxy_username: str, proxy_password: str) -> YouTubeTranscriptApi:
    """Build a Webshare-backed YouTubeTranscriptApi client.

    Static-session Webshare plans reject the library's default ``-rotate``
    endpoint (400). By default we route the bare username through the gateway
    via GenericProxyConfig, which those plans accept. Set FILMOT_WEBSHARE_ROTATE=1
    to use the library's WebshareProxyConfig rotating endpoint instead.
    """
    if _WEBSHARE_ROTATE:
        from youtube_transcript_api.proxies import WebshareProxyConfig
        return YouTubeTranscriptApi(
            proxy_config=WebshareProxyConfig(
                proxy_username=proxy_username,
                proxy_password=proxy_password,
                retries_when_blocked=0,
            ),
            http_client=_new_http_session(),
        )
    url = f"http://{proxy_username}:{proxy_password}@{_WEBSHARE_GATEWAY}"
    return _build_generic_proxy_api(url, url)


def _build_generic_proxy_api(http_proxy: Optional[str], https_proxy: Optional[str]) -> YouTubeTranscriptApi:
    """Build a generic HTTP/HTTPS proxy-backed YouTubeTranscriptApi client."""
    from youtube_transcript_api.proxies import GenericProxyConfig

    return YouTubeTranscriptApi(
        proxy_config=GenericProxyConfig(
            http_url=http_proxy,
            https_url=https_proxy or http_proxy,
        ),
        http_client=_new_http_session(),
    )


def _primary_from_environment() -> tuple[YouTubeTranscriptApi, str, bool]:
    """Build the "primary" non-pool client from environment config.

    Order of precedence (skipping the dynamic pool, which is layered separately):

    1. ``WEBSHARE_PROXY_USERNAME`` + ``WEBSHARE_PROXY_PASSWORD`` (legacy single
       rotating endpoint via ``WebshareProxyConfig``).
    2. ``HTTP_PROXY`` / ``HTTPS_PROXY``.
    3. Direct connection.

    Returns ``(api, label, is_proxy)``.
    """
    webshare_user = os.getenv("WEBSHARE_PROXY_USERNAME")
    webshare_pass = os.getenv("WEBSHARE_PROXY_PASSWORD")
    if webshare_user and webshare_pass:
        try:
            return (
                _build_webshare_api(webshare_user, webshare_pass),
                "legacy-webshare",
                True,
            )
        except ImportError:
            pass

    http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
    https_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    if http_proxy or https_proxy:
        try:
            return (
                _build_generic_proxy_api(http_proxy, https_proxy),
                "env-proxy",
                True,
            )
        except ImportError:
            pass

    return _build_direct_api(), "direct", False


def _init_api() -> None:
    """Initialise the "primary" client (the non-pool route)."""
    global _api, _proxy_configured, _initialized, _proxy_source
    global _primary_override

    if _initialized:
        return

    if _resolve_proxy_mode() == "direct-only":
        _api, _proxy_source, _proxy_configured = (
            _build_direct_api(),
            "direct",
            False,
        )
    else:
        _api, _proxy_source, _proxy_configured = _primary_from_environment()
    _primary_override = None
    _initialized = True


def _resolve_proxy_mode() -> str:
    """Decide the active routing mode.

    ``FILMOT_PROXY_MODE`` may be set to ``auto``, ``proxy-only``,
    ``primary-only``, or ``direct-only``. ``primary-only`` is also used by the
    CLI's explicit ``--proxy`` override so a configured Webshare pool cannot
    silently replace the operator-selected route. When unset, we default to
    ``proxy-only`` whenever a Webshare API token is configured (typical
    AWS-host case where direct fetches are routinely blocked) and ``auto``
    otherwise.
    """
    mode = (os.getenv("FILMOT_PROXY_MODE") or "").strip().lower()
    if mode in {"auto", "proxy-only", "primary-only", "direct-only"}:
        return mode
    return "proxy-only" if os.getenv("WEBSHARE_API_TOKEN") else "auto"


def configure_proxy(
    webshare_username: Optional[str] = None,
    webshare_password: Optional[str] = None,
    http_proxy: Optional[str] = None,
    https_proxy: Optional[str] = None,
    *,
    exclusive: bool = False,
) -> None:
    """Configure the transcript API to use a proxy as the *primary* client.

    By default the dynamic Webshare pool remains the first leg of an ``auto``
    route ladder. With ``exclusive=True`` the configured proxy is the only
    route; this is the behavior of the CLI's explicit ``--proxy`` option.

    For Webshare (legacy single rotating endpoint):
        configure_proxy(webshare_username="user", webshare_password="pass")

    For generic HTTP/HTTPS proxy:
        configure_proxy(http_proxy="http://user:pass@host:port")
    """
    global _api, _proxy_configured, _initialized, _proxy_source
    global _primary_override

    if webshare_username and webshare_password:
        _api = _build_webshare_api(webshare_username, webshare_password)
        _proxy_source = "legacy-webshare"
        _primary_override = {
            "kind": "webshare",
            "username": webshare_username,
            "password": webshare_password,
        }
    elif http_proxy or https_proxy:
        _api = _build_generic_proxy_api(http_proxy or https_proxy, https_proxy)
        _proxy_source = "explicit-proxy"
        _primary_override = {
            "kind": "generic",
            "http_proxy": http_proxy or https_proxy,
            "https_proxy": https_proxy,
        }
    else:
        raise ValueError("Must provide either Webshare credentials or proxy URL")

    _proxy_configured = True
    _initialized = True
    if exclusive:
        os.environ["FILMOT_PROXY_MODE"] = "primary-only"


def get_api() -> YouTubeTranscriptApi:
    """Get the configured API instance."""
    _init_api()
    return _api


def _fresh_primary_api() -> YouTubeTranscriptApi:
    """Build a primary client whose Requests session is owned by one fetch."""
    _init_api()
    if _primary_override:
        if _primary_override["kind"] == "webshare":
            return _build_webshare_api(
                _primary_override["username"],
                _primary_override["password"],
            )
        return _build_generic_proxy_api(
            _primary_override["http_proxy"],
            _primary_override["https_proxy"],
        )
    if _resolve_proxy_mode() == "direct-only":
        return _build_direct_api()
    api, _, _ = _primary_from_environment()
    return api


def disable_proxy() -> None:
    """Disable all proxy routes and force direct connection only.

    Sets ``FILMOT_PROXY_MODE=direct-only`` for the rest of this process so the
    dynamic pool is also skipped, then resets the primary client.
    """
    global _api, _proxy_configured, _initialized, _proxy_source
    global _primary_override
    os.environ["FILMOT_PROXY_MODE"] = "direct-only"
    _api = _build_direct_api()
    _proxy_configured = False
    _proxy_source = "direct"
    _primary_override = None
    _initialized = True


def is_proxy_configured() -> bool:
    """Check if a proxy is configured."""
    _init_api()
    return _proxy_configured


def reset_api() -> None:
    """Reset to default API without proxy."""
    global _initialized, _primary_override
    _initialized = False
    _primary_override = None
    _init_api()


def routing_plan() -> dict:
    """Return the actual transcript route plan without consuming a pool route.

    This initializes the primary client before reading its label, fixing the
    previous ordering bug where callers could report ``direct`` even though
    environment proxy configuration had not yet been inspected. Pool
    construction only reads local configuration/state; no session is picked
    and no transcript or Webshare management request is made.
    """
    _init_api()
    mode = _resolve_proxy_mode()
    pool = None if mode in {"direct-only", "primary-only"} else get_pool()
    routes = []
    if pool is not None and mode in {"auto", "proxy-only"}:
        routes.append(
            {
                "kind": "pool",
                "label": f"{pool.source} pool",
                "source": pool.source,
                "max_attempts": _POOL_RETRY_LIMIT,
                "total_sessions": len(pool._sessions),
                "available_sessions": pool.available_count(),
            }
        )

    if mode == "direct-only":
        routes.append(
            {
                "kind": "primary",
                "label": "direct",
                "source": "direct",
                "max_attempts": 1,
            }
        )
        primary_label = "direct"
        primary_is_proxy = False
    else:
        primary_label = _proxy_source
        primary_is_proxy = _proxy_configured
        if mode in {"auto", "primary-only"}:
            routes.append(
                {
                    "kind": "primary",
                    "label": primary_label,
                    "source": primary_label,
                    "max_attempts": 1,
                }
            )

    return {
        "mode": mode,
        "routes": routes,
        "pool_configured": pool is not None,
        "pool_source": pool.source if pool is not None else None,
        "primary": primary_label,
        "primary_is_proxy": primary_is_proxy,
        "has_direct_route": any(r["source"] == "direct" for r in routes),
        "connect_timeout_s": _env_timeout(
            "FILMOT_TRANSCRIPT_CONNECT_TIMEOUT", DEFAULT_CONNECT_TIMEOUT
        ),
        "read_timeout_s": _env_timeout(
            "FILMOT_TRANSCRIPT_READ_TIMEOUT", DEFAULT_READ_TIMEOUT
        ),
        "route_timeout_s": _env_timeout(
            "FILMOT_TRANSCRIPT_ROUTE_TIMEOUT", DEFAULT_ROUTE_TIMEOUT
        ),
    }


def describe_routing_plan(plan: Optional[dict] = None) -> str:
    """Render :func:`routing_plan` as a concise CLI-friendly description."""
    plan = plan or routing_plan()
    if not plan["routes"]:
        return f"no usable routes ({plan['mode']} mode)"
    labels = []
    for route in plan["routes"]:
        if route["kind"] == "pool":
            labels.append(
                f"{route['label']} (up to {route['max_attempts']} attempts, "
                f"{route['available_sessions']} available)"
            )
        else:
            labels.append(route["label"])
    return " -> ".join(labels)


def extract_video_id(video_input: str) -> str:
    """
    Extract video ID from various YouTube URL formats or return as-is if already an ID.
    
    Supports:
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://youtube.com/watch?v=VIDEO_ID&other_params
    - Just the VIDEO_ID itself
    """
    # Already a video ID (11 characters, alphanumeric with - and _)
    if re.match(r'^[a-zA-Z0-9_-]{11}$', video_input):
        return video_input
    
    # YouTube URL patterns
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/v/([a-zA-Z0-9_-]{11})',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, video_input)
        if match:
            return match.group(1)
    
    # If nothing matched, return as-is and let the API handle errors
    return video_input


def get_transcript(
    video_id: str,
    languages: Optional[list[str]] = None,
    preserve_formatting: bool = False,
    *,
    route_timeout: Optional[float] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    fresh_primary: bool = False,
) -> dict:
    """
    Fetch the full transcript for a YouTube video.
    
    Args:
        video_id: YouTube video ID or URL
        languages: Preferred languages in order (e.g., ['en', 'en-US'])
                   If None, tries to get any available transcript
        preserve_formatting: If True, keeps original line breaks
        route_timeout: Maximum wall-clock seconds for each route attempt.
                      Defaults to ``FILMOT_TRANSCRIPT_ROUTE_TIMEOUT`` or 30.
        progress_callback: Optional callable receiving structured route events.
        
    Returns:
        dict with:
            - video_id: The video ID
            - language: Language code of the transcript
            - is_generated: Whether it's auto-generated
            - segments: List of {text, start, duration} dicts
            - full_text: Complete transcript as single string
            - duration_seconds: Total video duration
    """
    video_id = extract_video_id(video_id)
    
    if languages is None:
        languages = ['en', 'en-US', 'en-GB']
    
    attempts: list[str] = []
    route_errors: list[dict] = []
    last_error: Optional[Exception] = None
    attempt_timeout = (
        route_timeout
        if route_timeout is not None and route_timeout > 0
        else _env_timeout("FILMOT_TRANSCRIPT_ROUTE_TIMEOUT", DEFAULT_ROUTE_TIMEOUT)
    )

    for attempt_number, (label, api, on_outcome) in enumerate(
        _iter_routes(
            progress_callback,
            route_errors,
            fresh_primary=fresh_primary,
        ),
        1,
    ):
        attempts.append(label)
        started = time.monotonic()
        _emit_progress(
            progress_callback,
            event="route_start",
            route=label,
            attempt=attempt_number,
            timeout_s=attempt_timeout,
        )
        try:
            result = _call_with_route_timeout(
                lambda: _fetch_transcript_from_api(
                    api,
                    video_id,
                    languages=languages,
                    preserve_formatting=preserve_formatting,
                ),
                route=label,
                timeout_seconds=attempt_timeout,
            )
        except (TranscriptsDisabled, VideoUnavailable, NoTranscriptFound) as terminal:
            # Caption-side terminal failures — not a transport problem.
            # Don't penalise the route; surface the error immediately.
            if on_outcome is not None:
                on_outcome("success", None)
            _emit_progress(
                progress_callback,
                event="route_terminal",
                route=label,
                attempt=attempt_number,
                elapsed_s=time.monotonic() - started,
                error_type=type(terminal).__name__,
                error=str(terminal),
            )
            return _terminal_error_result(
                terminal,
                video_id,
                label,
                routes_tried=attempts,
                route_errors=route_errors,
            )
        except Exception as exc:  # noqa: BLE001 - we re-raise via result dict
            kind = classify_transport_error(exc)
            safe_error = redact_sensitive_text(exc)
            if on_outcome is not None:
                on_outcome("failure", (kind or "other", safe_error))
            last_error = exc
            elapsed = time.monotonic() - started
            route_errors.append(
                {
                    "route": label,
                    "kind": kind or "other",
                    "error_type": type(exc).__name__,
                    "error": safe_error,
                    "elapsed_s": elapsed,
                }
            )
            _emit_progress(
                progress_callback,
                event=(
                    "route_timeout"
                    if isinstance(exc, TranscriptRouteTimeout)
                    else "route_failed"
                ),
                route=label,
                attempt=attempt_number,
                elapsed_s=elapsed,
                kind=kind or "other",
                error_type=type(exc).__name__,
                error=safe_error,
            )
            if not kind:
                # Unknown / non-transport error — stop retrying.
                break
            continue

        # Success.
        if on_outcome is not None:
            on_outcome("success", None)
        if isinstance(result, dict) and "error" not in result:
            result["route"] = label
            result["route_elapsed_seconds"] = time.monotonic() - started
            result["routes_tried"] = list(attempts)
            result["route_errors"] = list(route_errors)
        _emit_progress(
            progress_callback,
            event="route_success",
            route=label,
            attempt=attempt_number,
            elapsed_s=time.monotonic() - started,
        )
        return result

    if last_error is not None:
        error_msg = redact_sensitive_text(last_error)
        error_type = type(last_error).__name__
    elif route_errors:
        error_msg = route_errors[-1]["error"]
        error_type = route_errors[-1]["error_type"]
    elif _resolve_proxy_mode() == "proxy-only":
        error_msg = (
            "No proxy route is available in proxy-only mode. Configure a "
            "Webshare pool or choose auto/direct-only mode."
        )
        error_type = "NoRouteAvailable"
    else:
        error_msg = "all routes exhausted"
        error_type = "RoutesExhausted"
    _emit_progress(
        progress_callback,
        event="routes_exhausted",
        attempts=len(attempts),
        error_type=error_type,
        error=error_msg,
    )
    return {
        "error": error_msg,
        "error_type": error_type,
        "video_id": video_id,
        "routes_tried": attempts,
        "route_errors": route_errors,
    }


def _emit_progress(
    callback: Optional[Callable[[dict], None]],
    **event: Any,
) -> None:
    """Emit a progress event without allowing UI code to break retrieval."""
    if callback is None:
        return
    try:
        callback(event)
    except Exception:
        # Progress is observational. A renderer/log sink must never turn a
        # successful transcript fetch into a failed one.
        return


def _call_with_route_timeout(
    operation: Callable[[], dict],
    *,
    route: str,
    timeout_seconds: float,
) -> dict:
    """Run one route attempt with a strict wall-clock deadline.

    The worker is daemonized so a stuck third-party call cannot keep the CLI
    alive. The underlying bounded requests session still has connect/read
    deadlines and will clean itself up shortly after the caller advances.
    """
    outcome: queue.Queue = queue.Queue(maxsize=1)

    def _worker() -> None:
        try:
            outcome.put((True, operation()))
        except BaseException as exc:  # propagate the original exception
            outcome.put((False, exc))

    worker = threading.Thread(
        target=_worker,
        name=f"filmot-transcript-{route}",
        daemon=True,
    )
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        raise TranscriptRouteTimeout(route, timeout_seconds)

    succeeded, value = outcome.get_nowait()
    if succeeded:
        return value
    raise value


def _terminal_error_result(
    exc: Exception,
    video_id: str,
    route: str,
    *,
    routes_tried: Optional[list[str]] = None,
    route_errors: Optional[list[dict]] = None,
) -> dict:
    if isinstance(exc, TranscriptsDisabled):
        msg = "Transcripts are disabled for this video"
    elif isinstance(exc, VideoUnavailable):
        msg = "Video is unavailable"
    else:
        msg = str(exc) or "No transcript available"
    return {
        "error": redact_sensitive_text(msg),
        "error_type": type(exc).__name__,
        "video_id": video_id,
        "route": route,
        "routes_tried": list(routes_tried or [route]),
        "route_errors": list(route_errors or []),
    }


def _iter_routes(
    progress_callback: Optional[Callable[[dict], None]] = None,
    setup_errors: Optional[list[dict]] = None,
    *,
    fresh_primary: bool = False,
):
    """Yield ``(label, api_client, on_outcome)`` tuples per the active mode.

    ``on_outcome`` is a callable invoked once per attempt with either
    ``("success", None)`` or ``("failure", (kind, summary))`` so the pool can
    update health stats. ``None`` for non-pool routes.
    """
    # Initialize before reading ``_proxy_source``. Python evaluates tuple
    # elements left-to-right, so the former ``(_proxy_source, get_api(), ...)``
    # could capture the default label before get_api inspected the environment.
    _init_api()
    mode = _resolve_proxy_mode()
    pool = None if mode in {"direct-only", "primary-only"} else get_pool()

    # 1. Pool sessions (try up to N).
    if pool is not None and mode in {"auto", "proxy-only"}:
        # ``pick`` may refresh an empty/stale API-backed pool. Announce that
        # bounded management step before it begins so the CLI never appears
        # frozen while preparing the first route.
        _emit_progress(
            progress_callback,
            event="pool_prepare",
            route=f"{pool.source} pool",
            available_sessions=pool.available_count(),
            max_attempts=_POOL_RETRY_LIMIT,
        )
        for _ in range(_POOL_RETRY_LIMIT):
            try:
                session = pool.pick(lease=True)
            except WebshareProxyError as exc:
                safe_error = redact_sensitive_text(exc)
                error = {
                    "route": f"pool:{pool.source}:refresh",
                    "kind": "pool_refresh",
                    "error_type": type(exc).__name__,
                    "error": safe_error,
                    "elapsed_s": 0.0,
                }
                if setup_errors is not None:
                    setup_errors.append(error)
                _emit_progress(
                    progress_callback,
                    event="pool_prepare_failed",
                    route=f"{pool.source} pool",
                    error_type=type(exc).__name__,
                    error=safe_error,
                )
                break
            if session is None:
                break
            url = pool.proxy_url(session)
            try:
                api = _build_generic_proxy_api(url, url)
            except Exception:
                pool.release(session)
                raise

            def _cb(outcome, info, _s=session, _p=pool):
                if outcome == "success":
                    _p.report_success(_s)
                else:
                    kind, summary = info
                    _p.report_failure(_s, kind or "other", summary=summary)

            yield (f"pool:{pool.source}:{pool.redacted_session_id(session)}", api, _cb)

    # 2. Primary client (direct, env-proxy, legacy-webshare, or operator-supplied).
    if mode == "direct-only":
        yield (
            "direct",
            _fresh_primary_api() if fresh_primary else get_api(),
            None,
        )
    elif mode in {"auto", "primary-only"}:
        primary_api = _fresh_primary_api() if fresh_primary else get_api()
        primary_label = _proxy_source
        yield (primary_label, primary_api, None)


def probe_pool_session(
    pool,
    session,
    video_id: str,
    *,
    languages: Optional[list[str]] = None,
    route_timeout: Optional[float] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Boundedly probe one explicitly selected pool session.

    This is the integration point for ``proxy test``: it uses the same
    connect/read/overall deadlines as normal transcript retrieval, reports
    health to the pool, and emits progress before the potentially slow call.
    """
    video_id = extract_video_id(video_id)
    languages = languages or ["en", "en-US", "en-GB"]
    timeout_seconds = (
        route_timeout
        if route_timeout is not None and route_timeout > 0
        else _env_timeout("FILMOT_TRANSCRIPT_ROUTE_TIMEOUT", DEFAULT_ROUTE_TIMEOUT)
    )
    label = f"pool:{pool.source}:{pool.redacted_session_id(session)}"
    api = _build_generic_proxy_api(
        pool.proxy_url(session),
        pool.proxy_url(session),
    )
    started = time.monotonic()
    _emit_progress(
        progress_callback,
        event="route_start",
        route=label,
        attempt=1,
        timeout_s=timeout_seconds,
    )
    try:
        result = _call_with_route_timeout(
            lambda: _fetch_transcript_from_api(
                api,
                video_id,
                languages=languages,
                preserve_formatting=False,
            ),
            route=label,
            timeout_seconds=timeout_seconds,
        )
    except (TranscriptsDisabled, VideoUnavailable, NoTranscriptFound) as terminal:
        # The route reached YouTube successfully; captions are a video concern.
        pool.report_success(session)
        result = _terminal_error_result(terminal, video_id, label)
        result["transport_ok"] = True
        result["route_elapsed_seconds"] = time.monotonic() - started
        _emit_progress(
            progress_callback,
            event="route_terminal",
            route=label,
            attempt=1,
            elapsed_s=result["route_elapsed_seconds"],
            error_type=type(terminal).__name__,
            error=str(terminal),
        )
        return result
    except Exception as exc:  # noqa: BLE001 - normalized to a result
        kind = classify_transport_error(exc) or "other"
        safe_error = redact_sensitive_text(
            exc,
            (session.username, session.password),
        )
        pool.report_failure(session, kind, summary=safe_error)
        result = {
            "error": safe_error,
            "error_type": type(exc).__name__,
            "video_id": video_id,
            "route": label,
            "failure_kind": kind,
            "transport_ok": False,
            "route_elapsed_seconds": time.monotonic() - started,
        }
        _emit_progress(
            progress_callback,
            event=(
                "route_timeout"
                if isinstance(exc, TranscriptRouteTimeout)
                else "route_failed"
            ),
            route=label,
            attempt=1,
            elapsed_s=result["route_elapsed_seconds"],
            kind=kind,
            error_type=type(exc).__name__,
            error=safe_error,
        )
        return result

    pool.report_success(session)
    result["route"] = label
    result["transport_ok"] = True
    result["route_elapsed_seconds"] = time.monotonic() - started
    _emit_progress(
        progress_callback,
        event="route_success",
        route=label,
        attempt=1,
        elapsed_s=result["route_elapsed_seconds"],
    )
    return result


def _fetch_transcript_from_api(
    api: YouTubeTranscriptApi,
    video_id: str,
    languages: list[str],
    preserve_formatting: bool,
) -> dict:
    """Fetch transcript using a specific API client.

    Caption-side terminal failures (``TranscriptsDisabled``, ``VideoUnavailable``,
    ``NoTranscriptFound`` after translation attempts) propagate as exceptions so
    the route iterator can distinguish them from transport failures.
    """
    try:
        # Try the simple fetch first
        try:
            transcript = api.fetch(video_id, languages=languages, preserve_formatting=preserve_formatting)
        except NoTranscriptFound:
            # Try to list and translate into the first requested language
            target_lang = languages[0].split('-')[0] if languages else 'en'
            transcript_list = api.list(video_id)
            translated = None
            for t in transcript_list:
                if t.is_translatable:
                    translated = t.translate(target_lang).fetch(preserve_formatting=preserve_formatting)
                    break
            if translated:
                transcript = translated
            else:
                # Bubble up as a terminal NoTranscriptFound so the route iterator
                # treats it as caption-side, not transport-side.
                raise NoTranscriptFound(video_id, languages, transcript_list)
        
        # Convert to dict format for consistency
        segments = [
            {
                'text': seg.text,
                'start': seg.start,
                'duration': getattr(seg, 'duration', 0),
            }
            for seg in transcript
        ]
        
        # Build full text
        if preserve_formatting:
            full_text = '\n'.join(seg['text'] for seg in segments)
        else:
            full_text = ' '.join(seg['text'].replace('\n', ' ') for seg in segments)
        
        # Calculate total duration
        if segments:
            last_seg = segments[-1]
            duration_seconds = last_seg['start'] + last_seg.get('duration', 0)
        else:
            duration_seconds = 0
        
        return {
            'video_id': transcript.video_id,
            'language': transcript.language_code,
            'is_generated': transcript.is_generated,
            'segments': segments,
            'full_text': full_text,
            'duration_seconds': duration_seconds,
            'segment_count': len(segments),
        }
        
    except (TranscriptsDisabled, VideoUnavailable, NoTranscriptFound):
        # Re-raise terminal errors so the route iterator can short-circuit.
        raise


def get_transcript_with_timestamps(
    video_id: str,
    languages: Optional[list[str]] = None,
    chunk_minutes: float = 5.0,
    *,
    route_timeout: Optional[float] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    fresh_primary: bool = False,
) -> dict:
    """
    Fetch transcript with timestamps, optionally chunked into time segments.
    
    Useful for navigating long videos - groups transcript into chunks
    with their start times.
    
    Args:
        video_id: YouTube video ID or URL
        languages: Preferred languages
        chunk_minutes: Group segments into chunks of this duration
        route_timeout: Maximum wall-clock seconds for each route attempt
        progress_callback: Optional callable receiving structured route events
        
    Returns:
        dict with chunked transcript for easier navigation
    """
    result = get_transcript(
        video_id,
        languages,
        preserve_formatting=False,
        route_timeout=route_timeout,
        progress_callback=progress_callback,
        fresh_primary=fresh_primary,
    )
    
    if 'error' in result:
        return result
    
    segments = result['segments']
    chunk_seconds = chunk_minutes * 60
    
    chunks = []
    current_chunk = {
        'start': 0,
        'start_formatted': '0:00',
        'texts': [],
    }
    
    for seg in segments:
        chunk_index = int(seg['start'] // chunk_seconds)
        expected_start = chunk_index * chunk_seconds
        
        if expected_start != current_chunk['start'] and current_chunk['texts']:
            # Save current chunk and start new one
            current_chunk['text'] = ' '.join(current_chunk['texts'])
            del current_chunk['texts']
            chunks.append(current_chunk)
            
            current_chunk = {
                'start': expected_start,
                'start_formatted': format_timestamp(expected_start),
                'texts': [],
            }
        
        current_chunk['texts'].append(seg['text'].replace('\n', ' '))
    
    # Don't forget the last chunk
    if current_chunk['texts']:
        current_chunk['text'] = ' '.join(current_chunk['texts'])
        del current_chunk['texts']
        chunks.append(current_chunk)
    
    result['chunks'] = chunks
    result['chunk_minutes'] = chunk_minutes
    
    return result


def format_timestamp(seconds: float) -> str:
    """Convert seconds to human-readable timestamp (H:MM:SS or M:SS)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


def search_in_transcript(
    video_id: str,
    query: str,
    context_segments: int = 2,
    languages: Optional[list[str]] = None,
) -> dict:
    """
    Search for specific terms within a video's transcript.
    
    Returns matching segments with surrounding context.
    
    Args:
        video_id: YouTube video ID or URL
        query: Search term (case-insensitive)
        context_segments: Number of segments before/after to include
        languages: Preferred languages
        
    Returns:
        dict with matching segments and their context
    """
    result = get_transcript(video_id, languages)
    
    if 'error' in result:
        return result
    
    segments = result['segments']
    query_lower = query.lower()
    matches = []
    
    for i, seg in enumerate(segments):
        if query_lower in seg['text'].lower():
            # Get context
            start_idx = max(0, i - context_segments)
            end_idx = min(len(segments), i + context_segments + 1)
            
            context_text = ' '.join(
                s['text'].replace('\n', ' ') 
                for s in segments[start_idx:end_idx]
            )
            
            matches.append({
                'timestamp': format_timestamp(seg['start']),
                'start_seconds': seg['start'],
                'matched_text': seg['text'],
                'context': context_text,
                'segment_index': i,
            })
    
    return {
        'video_id': result['video_id'],
        'query': query,
        'match_count': len(matches),
        'matches': matches,
        'language': result['language'],
        'is_generated': result['is_generated'],
    }


def get_transcript_with_fallback(
    video_id: str,
    languages: Optional[list[str]] = None,
    preserve_formatting: bool = False,
    use_aws_fallback: bool = True,
    aws_progress_callback=None,
    route_timeout: Optional[float] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    fresh_primary: bool = False,
) -> dict:
    """
    Fetch transcript with AWS Transcribe fallback.
    
    Attempts to get transcript using youtube-transcript-api first.
    If that fails (captions disabled, video unavailable, etc.) and
    use_aws_fallback is True, falls back to AWS Transcribe.
    
    AWS Transcribe flow:
    1. Download audio using yt-dlp
    2. Upload to S3
    3. Start transcription job with language auto-detection
    4. Poll for completion
    5. Fetch result and cleanup
    
    Requires:
    - AWS credentials configured (profile 'APIBoss')
    - yt-dlp installed
    - boto3 and requests packages
    
    Args:
        video_id: YouTube video ID or URL
        languages: Preferred languages for youtube-transcript-api
        preserve_formatting: Keep original line breaks
        use_aws_fallback: Enable AWS Transcribe fallback
        aws_progress_callback: Optional callback(stage, message) for AWS progress
        route_timeout: Maximum wall-clock seconds for each YouTube route
        progress_callback: Optional callable receiving YouTube route events
    
    Returns:
        dict with:
            - video_id: The video ID
            - language: Language code
            - is_generated: Whether it's auto-generated (True for AWS)
            - full_text: Complete transcript
            - source: 'youtube' or 'aws_transcribe'
            - segments: List of segments (empty for AWS)
            - error: Error message if both methods fail
    """
    video_id = extract_video_id(video_id)
    
    # Try YouTube transcript API first
    result = get_transcript(
        video_id,
        languages,
        preserve_formatting,
        route_timeout=route_timeout,
        progress_callback=progress_callback,
        fresh_primary=fresh_primary,
    )
    
    if 'error' not in result:
        result['source'] = 'youtube'
        return result
    
    youtube_error = result.get('error', 'Unknown error')
    
    # If fallback disabled, return the error
    if not use_aws_fallback:
        return result

    youtube_routes_tried = list(result.get("routes_tried") or [])
    youtube_route_metadata = {
        "youtube_error": youtube_error,
        "youtube_error_type": result.get("error_type"),
        "youtube_routes_tried": youtube_routes_tried,
        "routes_tried": [*youtube_routes_tried, "aws-transcribe"],
        "route_errors": list(result.get("route_errors") or []),
    }
    
    # Try AWS Transcribe fallback
    try:
        from .aws_transcribe import transcribe_video, check_dependencies, AWSTranscribeError
        
        # Check dependencies first
        deps_ok, deps_msg = check_dependencies()
        if not deps_ok:
            return {
                'error': redact_sensitive_text(
                    f"YouTube error: {youtube_error}. "
                    f"AWS fallback unavailable: {deps_msg}"
                ),
                'error_type': 'FallbackUnavailable',
                'video_id': video_id,
                'route': 'aws-transcribe',
                **youtube_route_metadata,
            }
        
        # Run transcription
        transcript_text, detected_language = transcribe_video(
            video_id,
            identify_language=True,
            cleanup=True,
            progress_callback=aws_progress_callback,
        )
        
        return {
            'video_id': video_id,
            'language': detected_language or 'unknown',
            'is_generated': True,
            'segments': [],  # AWS doesn't provide timestamps in simple mode
            'full_text': transcript_text,
            'duration_seconds': 0,
            'segment_count': 0,
            'source': 'aws_transcribe',
            'route': 'aws-transcribe',
            **youtube_route_metadata,
        }
        
    except Exception as e:
        return {
            'error': redact_sensitive_text(
                f"YouTube error: {youtube_error}. AWS fallback error: {e}"
            ),
            'error_type': type(e).__name__,
            'video_id': video_id,
            'route': 'aws-transcribe',
            **youtube_route_metadata,
        }
