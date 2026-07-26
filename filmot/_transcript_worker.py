"""Private subprocess entry point for one transcript route attempt.

The parent process sends a versioned JSON request on stdin. This worker owns
the HTTP client and returns one JSON object on stdout, allowing the parent to
terminate the entire network operation at the route deadline.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Iterable

from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from .proxy_pool import classify_transport_error, redact_sensitive_text
from .transcript import (
    _TRANSCRIPT_WORKER_PROTOCOL_VERSION,
    _build_direct_api,
    _build_generic_proxy_api,
    _build_webshare_api,
    _fetch_transcript_from_api,
)


PROTOCOL_VERSION = _TRANSCRIPT_WORKER_PROTOCOL_VERSION


class WorkerRequestError(ValueError):
    """Raised when the parent sends an invalid worker request."""


def _route_secrets(route: Dict[str, Any]) -> tuple[str, ...]:
    values = []
    for key in (
        "username",
        "password",
        "http_proxy",
        "https_proxy",
    ):
        value = route.get(key)
        if value:
            values.append(str(value))
    return tuple(values)


def _error_payload(
    status: str,
    error: BaseException,
    *,
    secrets: Iterable[str] = (),
    failure_kind: str = "",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "status": status,
        "error": {
            "error_type": type(error).__name__,
            "message": redact_sensitive_text(error, secrets),
        },
    }
    if failure_kind:
        payload["error"]["failure_kind"] = failure_kind
    return payload


def _validate_request(payload: object) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise WorkerRequestError("Worker request must be one JSON object")
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise WorkerRequestError("Unsupported transcript worker protocol")

    route = payload.get("route")
    if not isinstance(route, dict):
        raise WorkerRequestError("Worker request is missing a route object")
    route_kind = route.get("kind")
    if route_kind not in {"direct", "generic-proxy", "webshare"}:
        raise WorkerRequestError("Worker route kind is invalid")

    video_id = payload.get("video_id")
    if not isinstance(video_id, str) or not video_id:
        raise WorkerRequestError("Worker request is missing a video ID")

    languages = payload.get("languages")
    if (
        not isinstance(languages, list)
        or not languages
        or not all(isinstance(language, str) and language for language in languages)
    ):
        raise WorkerRequestError("Worker request languages must be non-empty strings")

    for key in ("connect_timeout", "read_timeout"):
        value = payload.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise WorkerRequestError(f"Worker request {key} must be positive")

    if route_kind == "generic-proxy" and not (
        route.get("http_proxy") or route.get("https_proxy")
    ):
        raise WorkerRequestError("Generic proxy route is missing its URL")
    if route_kind == "webshare" and not (
        route.get("username") and route.get("password")
    ):
        raise WorkerRequestError("Webshare route is missing credentials")

    return payload


def _build_api(request: Dict[str, Any]):
    route = request["route"]
    connect_timeout = float(request["connect_timeout"])
    read_timeout = float(request["read_timeout"])
    if route["kind"] == "direct":
        return _build_direct_api(
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )
    if route["kind"] == "webshare":
        return _build_webshare_api(
            str(route["username"]),
            str(route["password"]),
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )
    return _build_generic_proxy_api(
        route.get("http_proxy"),
        route.get("https_proxy"),
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
    )


def handle_request(payload: object) -> Dict[str, Any]:
    """Execute one validated worker request and return a protocol response."""
    try:
        request = _validate_request(payload)
    except BaseException as exc:
        return _error_payload("internal_error", exc)

    route = request["route"]
    secrets = _route_secrets(route)
    try:
        api = _build_api(request)
        result = _fetch_transcript_from_api(
            api,
            request["video_id"],
            languages=request["languages"],
            preserve_formatting=bool(request.get("preserve_formatting", False)),
        )
    except (TranscriptsDisabled, VideoUnavailable, NoTranscriptFound) as exc:
        return _error_payload("terminal", exc, secrets=secrets)
    except BaseException as exc:
        failure_kind = classify_transport_error(exc)
        return _error_payload(
            "transport_error" if failure_kind else "internal_error",
            exc,
            secrets=secrets,
            failure_kind=failure_kind,
        )

    return {
        "protocol_version": PROTOCOL_VERSION,
        "status": "success",
        "result": result,
    }


def main() -> int:
    try:
        raw_request = sys.stdin.buffer.read()
        request = json.loads(raw_request.decode("utf-8"))
        response = handle_request(request)
    except BaseException as exc:
        response = _error_payload("internal_error", exc)

    encoded = json.dumps(
        response,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    except BrokenPipeError:
        # The parent timed out or was interrupted and has already terminated
        # its side of the protocol.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
