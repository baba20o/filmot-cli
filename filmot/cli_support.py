"""Shared CLI presentation and error helpers.

Command modules depend on this leaf module instead of importing the root Click
group.  That keeps command registration one-way and gives raw/human output a
single redaction and serialization boundary.
"""

from contextlib import nullcontext
import errno
import json
import sys
from typing import Any, Optional

import click
from rich.console import Console

from .proxy_pool import redact_sensitive_text
from .schemas import CommandResult, ErrorDetail


console = Console()
stderr_console = Console(stderr=True)


class PipeFlushWrapper:
    """Proxy a text stream while suppressing shutdown flush pipe errors."""

    def __init__(self, wrapped):
        self.wrapped = wrapped

    def flush(self) -> None:
        try:
            self.wrapped.flush()
        except OSError as error:
            if error.errno not in (errno.EPIPE, errno.EINVAL):
                raise

    def __getattr__(self, name):
        return getattr(self.wrapped, name)


def silence_broken_pipe_streams() -> None:
    """Prevent interpreter shutdown from retrying a closed consumer pipe."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        if not isinstance(stream, PipeFlushWrapper):
            setattr(sys, stream_name, PipeFlushWrapper(stream))


def redact_diagnostic(value: Any) -> Any:
    """Recursively redact credential-bearing proxy userinfo."""
    if isinstance(value, dict):
        return {
            key: redact_diagnostic(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_diagnostic(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_diagnostic(item) for item in value)
    if isinstance(value, (str, Exception)):
        return redact_sensitive_text(value)
    return value


def command_error(
    message: str,
    *,
    command: str = "unknown",
    raw: bool = False,
    payload: Optional[dict] = None,
    error_type: str = "CommandError",
    stage: Optional[str] = None,
) -> None:
    """Terminate with a machine-detectable, credential-safe failure.

    Raw failures retain a domain mapping when one is supplied and attach the
    shared result metadata under ``_filmot``.
    """
    if command == "unknown":
        context = click.get_current_context(silent=True)
        if context is not None and context.info_name:
            command = str(context.info_name)
    safe_message = str(redact_diagnostic(message))
    if raw:
        data = redact_diagnostic(payload) if payload is not None else {
            "error": safe_message,
        }
        if not isinstance(data, dict):
            data = {"error": safe_message, "detail": data}
        result = CommandResult.failed(
            command,
            data,
            ErrorDetail(
                type=error_type,
                message=safe_message,
                stage=stage,
            ),
        )
        click.echo(json.dumps(result.to_raw_dict(), indent=2, ensure_ascii=False))
        raise click.exceptions.Exit(1)
    raise click.ClickException(safe_message)


def diagnostic(message: str, *, raw: bool = False) -> None:
    """Write a human diagnostic without contaminating raw JSON stdout."""
    if not raw:
        console.print(message)


def status_context(message: str, *, raw: bool = False):
    """Return a Rich status context only when stdout is human-oriented."""
    return nullcontext() if raw else console.status(message)


def whole_word_summary(value: object, max_chars: int = 220) -> str:
    """Cap a redacted error on a word boundary, retaining useful detail."""
    text = " ".join(str(redact_diagnostic(value)).split())
    if len(text) <= max_chars:
        return text
    prefix = text[: max_chars - 1]
    if " " in prefix:
        prefix = prefix.rsplit(" ", 1)[0]
    return prefix.rstrip(" ,.;:-") + "…"


def transcript_failure_detail(result: dict, *, verbose: bool) -> str:
    """Render a redacted transcript failure with optional route diagnostics."""
    error_type = str(result.get("error_type") or "TranscriptError")
    message = whole_word_summary(result.get("error") or "unknown error", 500)
    base = "{}: {}".format(error_type, message)
    if not verbose:
        return whole_word_summary(base)

    details = []
    if result.get("route"):
        details.append("route={}".format(result["route"]))
    if result.get("routes_tried"):
        details.append(
            "routes_tried=" + ",".join(map(str, result["routes_tried"]))
        )
    if result.get("route_errors"):
        route_errors = [
            {
                "route": item.get("route"),
                "kind": item.get("kind"),
                "error_type": item.get("error_type"),
                "error": whole_word_summary(item.get("error", ""), 500),
            }
            for item in result["route_errors"]
            if isinstance(item, dict)
        ]
        details.append(
            "route_errors="
            + json.dumps(
                redact_diagnostic(route_errors),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    return " | ".join([base, *details])


def transcript_route_progress(
    event: dict,
    *,
    context: Optional[str] = None,
) -> None:
    """Render one structured transcript-route event to stderr."""
    event_name = event.get("event", "")
    route = str(event.get("route") or "route")
    prefix = "{}: ".format(context) if context else ""
    attempt = event.get("attempt")
    elapsed = float(event.get("elapsed_s") or 0)

    if event_name == "pool_prepare":
        click.echo(
            "{}preparing {} ({} available; up to {} attempts)".format(
                prefix,
                route,
                event.get("available_sessions", 0),
                event.get("max_attempts", 0),
            ),
            err=True,
        )
    elif event_name == "pool_prepare_failed":
        detail = event.get("error_type") or "pool refresh failed"
        click.echo(
            "{}could not prepare {} ({})".format(prefix, route, detail),
            err=True,
        )
    elif event_name == "route_start":
        click.echo(
            "{}route {}: {} (deadline {:g}s)".format(
                prefix,
                attempt,
                route,
                float(event.get("timeout_s") or 0),
            ),
            err=True,
        )
    elif event_name == "route_success":
        click.echo(
            "{}route {} succeeded: {} ({:.2f}s)".format(
                prefix, attempt, route, elapsed
            ),
            err=True,
        )
    elif event_name == "route_timeout":
        terminated = (
            "; worker terminated"
            if event.get("worker_terminated")
            else ""
        )
        click.echo(
            "{}route {} timed out: {} ({:.2f}s{})".format(
                prefix, attempt, route, elapsed, terminated
            ),
            err=True,
        )
    elif event_name == "route_failed":
        detail = event.get("kind") or event.get("error_type") or "transport error"
        click.echo(
            "{}route {} failed: {} ({}, {:.2f}s)".format(
                prefix, attempt, route, detail, elapsed
            ),
            err=True,
        )
    elif event_name == "route_terminal":
        detail = event.get("error_type") or "transcript unavailable"
        click.echo(
            "{}route {} reached YouTube: {} ({}, {:.2f}s)".format(
                prefix, attempt, route, detail, elapsed
            ),
            err=True,
        )
    elif event_name == "routes_exhausted":
        click.echo(
            "{}all transcript routes exhausted ({} attempted)".format(
                prefix,
                event.get("attempts", 0),
            ),
            err=True,
        )


def route_progress_for(context: Optional[str] = None):
    """Create a route-event renderer carrying a video/title label."""
    return lambda event: transcript_route_progress(event, context=context)
