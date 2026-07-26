"""Machine-global proxy pool inspection, refresh, and probe commands."""

import click
from rich.panel import Panel
from rich.table import Table

from ..cli_support import (
    command_error as _command_error,
    console,
    route_progress_for as _route_progress_for,
    whole_word_summary as _whole_word_summary,
)
from ..schemas import CommandResult, ErrorDetail, ProxyResultData, ResultStatus

@click.group()
def proxy():
    """Manage the file/API proxy pool used for transcript fetches.

    Configure WEBSHARE_API_TOKEN or a WEBSHARE_SESSION_FILE. Availability means
    eligible now; "recently healthy" requires a successful bounded live fetch.
    """


def _require_pool():
    from ..proxy_pool import get_pool
    pool = get_pool()
    if pool is None:
        raise click.ClickException(
            "No transcript proxy pool is configured. Set WEBSHARE_API_TOKEN "
            "or WEBSHARE_SESSION_FILE."
        )
    return pool


def _render_proxy_status(
    outcome: CommandResult[ProxyResultData],
    *,
    full: bool,
) -> None:
    """Render one typed machine-global proxy snapshot."""
    snap = dict(outcome.data.get("summary") or {})
    snap["sessions"] = list(outcome.data.get("sessions") or [])

    from datetime import datetime
    last_refresh = (
        datetime.fromtimestamp(snap["last_refresh"]).strftime("%Y-%m-%d %H:%M:%S")
        if snap["last_refresh"]
        else "never"
    )

    source_detail = snap["source"]
    if snap.get("session_file"):
        source_detail += f" ({snap['session_file']})"
    console.print(
        Panel(
            f"[bold]Source:[/bold] {source_detail}\n"
            f"[bold]Gateway:[/bold] {snap['gateway']}\n"
            f"[bold]Countries:[/bold] "
            f"{', '.join(snap['countries']) if snap['countries'] else 'all'}\n"
            f"[bold]Sessions:[/bold] {snap['total']} total | "
            f"{snap['available']} available now | "
            f"{snap['recently_healthy']} recently healthy | "
            f"{snap['untested']} untested | {snap['in_flight']} in flight\n"
            f"[bold]Problems:[/bold] {snap['cooling']} cooling | "
            f"{snap['failing']} failing | {snap['retired']} retired | "
            f"{snap['invalid']} invalid\n"
            f"[bold]Last refresh/reload:[/bold] {last_refresh}"
            f"{' [yellow](stale)[/yellow]' if snap['stale'] else ''}",
            title="Transcript Proxy Pool",
            border_style="cyan",
        )
    )

    if not snap["sessions"]:
        console.print("[yellow]Pool is empty. Run `filmot proxy refresh` to populate it.[/yellow]")
        return

    rows = (
        snap["sessions"]
        if full
        else [
            session
            for session in snap["sessions"]
            if session["state"] != "ready-untested"
        ]
    )
    if not rows and not full:
        console.print(
            f"[dim]All {snap['total']} sessions are available but untested. "
            "Use --full to list them or `filmot proxy test` to probe them.[/dim]"
        )
        return

    # Keep the identity and state readable at Click/Rich's normal 80-column
    # width. The previous 11-column table truncated both into indistinguishable
    # ``ses…`` / ``rea…`` labels.
    t = Table(show_lines=True)
    t.add_column("Session", no_wrap=True)
    t.add_column("Country", no_wrap=True)
    t.add_column("State", no_wrap=True)
    t.add_column("Health history")

    def _short_time(timestamp):
        if not timestamp:
            return "-"
        return datetime.fromtimestamp(timestamp).strftime("%m-%d %H:%M")

    state_styles = {
        "ready-tested": "[green]ready-tested[/green]",
        "ready-untested": "[cyan]ready-untested[/cyan]",
        "ready-stale": "[blue]ready-stale[/blue]",
        "ready-failing": "[yellow]ready-failing[/yellow]",
        "in-flight": "[magenta]in-flight[/magenta]",
        "cooldown": "[yellow]cooldown[/yellow]",
        "retired": "[red]retired[/red]",
        "invalid": "[red]invalid[/red]",
    }
    for s in rows:
        state = state_styles.get(s["state"], s["state"])
        history = (
            f"OK {s['success']} | 429 {s['fail_429']} | "
            f"blocked {s['fail_blocked']} | other {s['fail_other']}"
        )
        if s["cooldown_remaining_s"]:
            history += f"\ncooldown: {s['cooldown_remaining_s']}s"
        if s["last_success_at"]:
            history += f"\nlast OK: {_short_time(s['last_success_at'])}"
        if s["last_failure_at"]:
            history += f"\nlast fail: {_short_time(s['last_failure_at'])}"
        if s["last_error"]:
            history += f"\n{_whole_word_summary(s['last_error'], 80)}"
        t.add_row(
            str(s["id"]),
            s["country"] or "-",
            state,
            history,
        )
    console.print(t)


@proxy.command("status")
@click.option("--full", is_flag=True, help="Show every session, not just a summary")
def proxy_status(full: bool):
    """Show availability, recent live health, and per-session state."""
    pool = _require_pool()
    snapshot = pool.status_snapshot()
    sessions_by_id = {
        session.id: session
        for session in pool._sessions
    }
    safe_sessions = []
    for item in snapshot["sessions"]:
        safe_item = dict(item)
        session = sessions_by_id.get(item.get("id"))
        safe_item["id"] = (
            pool.redacted_session_id(session)
            if session is not None
            else "session-unknown"
        )
        safe_item.pop("username", None)
        safe_sessions.append(safe_item)
    data: ProxyResultData = {
        "source": str(snapshot["source"]),
        "summary": {
            key: value
            for key, value in snapshot.items()
            if key != "sessions"
        },
        "sessions": safe_sessions,
    }
    outcome = CommandResult(
        command="proxy-status",
        status=(
            ResultStatus.COMPLETED
            if snapshot["sessions"]
            else ResultStatus.EMPTY
        ),
        data=data,
    )
    _render_proxy_status(outcome, full=full)


def _render_proxy_refresh(
    outcome: CommandResult[ProxyResultData],
) -> None:
    summary = outcome.data.get("summary") or {}
    console.print(
        f"[green]Pool now has {summary.get('total', 0)} sessions: "
        f"{summary.get('available', 0)} available, "
        f"{summary.get('recently_healthy', 0)} recently healthy.[/green]"
    )


@proxy.command("refresh")
@click.option(
    "--full",
    is_flag=True,
    help="For API-backed pools, also request remote IP rotation",
)
def proxy_refresh(full: bool):
    """Refresh an API pool or reload a file-backed session list."""
    pool = _require_pool()
    rotation_error = None
    if full and pool.source == "session-file":
        console.print(
            "[dim]File-backed pool: remote IP rotation is not applicable; "
            "reloading the local session file instead.[/dim]"
        )
    elif full:
        try:
            with console.status("[bold blue]Asking Webshare to rotate the underlying proxy list..."):
                pool.request_full_refresh()
            console.print("[green]POST /proxy/list/refresh/ accepted (204).[/green]")
        except Exception as e:
            rotation_error = e
            console.print(
                f"[red]Remote IP rotation failed: "
                f"{_whole_word_summary(e)}[/red]"
            )
    try:
        action = (
            "Reloading local session file..."
            if pool.source == "session-file"
            else "Pulling current Webshare session list..."
        )
        with console.status(f"[bold blue]{action}"):
            n = pool.refresh(force=True)
    except Exception as e:
        _command_error(f"Pool refresh/reload failed: {e}")

    data: ProxyResultData = {
        "source": pool.source,
        "summary": {
            "total": n,
            "available": pool.available_count(),
            "recently_healthy": pool.recently_healthy_count(),
            "remote_rotation_requested": full,
            "remote_rotation_error": (
                _whole_word_summary(rotation_error, 500)
                if rotation_error is not None else None
            ),
        },
    }
    outcome = CommandResult(
        command="proxy-refresh",
        status=(
            ResultStatus.PARTIAL
            if rotation_error is not None
            else ResultStatus.COMPLETED
        ),
        data=data,
        errors=(
            [
                ErrorDetail(
                    type=type(rotation_error).__name__,
                    message=_whole_word_summary(rotation_error, 500),
                    stage="remote_rotation",
                )
            ]
            if rotation_error is not None
            else []
        ),
    )
    _render_proxy_refresh(outcome)
    if rotation_error is not None:
        _command_error(
            "The session list refreshed, but the requested remote IP rotation failed."
        )


def _render_proxy_test_summary(
    outcome: CommandResult[ProxyResultData],
) -> None:
    summary = outcome.data.get("summary") or {}
    console.print(
        f"[bold]Proxy probe summary:[/bold] "
        f"{summary.get('passed', 0)} passed, "
        f"{summary.get('failed', 0)} failed, "
        f"{summary.get('unattempted', 0)} unattempted"
    )
    if summary.get("budget_exhausted"):
        console.print(
            "[yellow]The total proxy-test budget was exhausted.[/yellow]"
        )


@proxy.command("test")
@click.option("--video-id", default="dQw4w9WgXcQ", help="Video to probe (default: a known short video)")
@click.option(
    "--count",
    "-n",
    default=3,
    type=click.IntRange(1),
    show_default=True,
    help="Number of sessions to test",
)
@click.option(
    "--route-timeout",
    type=click.FloatRange(min=0.01),
    default=None,
    help="Per-session deadline (defaults to FILMOT_TRANSCRIPT_ROUTE_TIMEOUT)",
)
@click.option(
    "--total-timeout",
    type=click.FloatRange(min=0.01),
    default=90.0,
    show_default=True,
    help="Total command deadline in seconds",
)
def proxy_test(
    video_id: str,
    count: int,
    route_timeout: float,
    total_timeout: float,
):
    """Stream bounded, redacted live probes through N pool sessions."""
    from ..transcript import probe_pool_session, routing_plan
    import time

    pool = _require_pool()
    deadline = time.monotonic() + total_timeout
    if pool.available_count() == 0:
        _command_error(
            "No loaded proxy sessions are available to test. "
            "Run `filmot proxy refresh` first."
        )

    configured_route_timeout = (
        route_timeout
        if route_timeout is not None
        else routing_plan()["route_timeout_s"]
    )
    attempted = 0
    passed = 0
    failed = 0
    budget_exhausted = False
    attempted_session_ids: set[str] = set()
    attempts = []

    console.print(
        f"[bold]Probing up to {count} sessions against {video_id} "
        f"(route {configured_route_timeout:g}s; total {total_timeout:g}s)[/bold]"
    )
    for index in range(1, count + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            budget_exhausted = True
            break
        sess = pool.pick(
            refresh=False,
            exclude_ids=attempted_session_ids,
            lease=True,
        )
        if sess is None:
            console.print(
                "[yellow]No additional distinct session is available.[/yellow]"
            )
            break
        attempted_session_ids.add(sess.id)
        attempted += 1
        display_id = pool.redacted_session_id(sess)
        console.print(
            f"[dim][{index}/{count}] testing {display_id} "
            f"({sess.country_code or 'unknown country'})...[/dim]"
        )
        result = probe_pool_session(
            pool,
            sess,
            video_id,
            route_timeout=min(configured_route_timeout, remaining),
            progress_callback=_route_progress_for(display_id),
        )
        elapsed = float(result.get("route_elapsed_seconds") or 0)
        transport_ok = bool(result.get("transport_ok"))
        attempt = {
            "index": index,
            "session": display_id,
            "country": sess.country_code,
            "transport_ok": transport_ok,
            "elapsed_seconds": round(elapsed, 3),
            "error_type": result.get("error_type"),
            "failure_kind": result.get("failure_kind"),
        }
        attempts.append(attempt)
        if transport_ok:
            passed += 1
            detail = (
                f"{result.get('segment_count', 0)} segments"
                if "error" not in result
                else result.get("error_type", "video has no usable transcript")
            )
            console.print(
                f"[green][{index}/{count}] {display_id}: transport OK[/green] "
                f"({elapsed:.2f}s; {detail})"
            )
        else:
            failed += 1
            detail = (
                result.get("failure_kind")
                or result.get("error_type")
                or "transport failure"
            )
            console.print(
                f"[red][{index}/{count}] {display_id}: {detail}[/red] "
                f"({elapsed:.2f}s)"
            )
    unattempted = count - attempted
    if deadline - time.monotonic() <= 0 and unattempted:
        budget_exhausted = True
    data: ProxyResultData = {
        "source": pool.source,
        "summary": {
            "video_id": video_id,
            "requested": count,
            "attempted": attempted,
            "passed": passed,
            "failed": failed,
            "unattempted": unattempted,
            "budget_exhausted": budget_exhausted,
            "route_timeout": configured_route_timeout,
            "total_timeout": total_timeout,
        },
        "attempts": attempts,
    }
    errors = []
    if failed:
        errors.append(
            ErrorDetail(
                type="ProxyProbeFailure",
                message=f"{failed} proxy session probe(s) failed",
                stage="probe",
                details={"failed": failed, "attempted": attempted},
            )
        )
    if unattempted:
        errors.append(
            ErrorDetail(
                type="IncompleteProxyProbe",
                message=f"{unattempted} requested probe(s) were not attempted",
                stage="budget" if budget_exhausted else "availability",
                details={
                    "unattempted": unattempted,
                    "budget_exhausted": budget_exhausted,
                },
            )
        )
    outcome = CommandResult(
        command="proxy-test",
        status=(
            ResultStatus.FAILED
            if passed == 0
            else ResultStatus.PARTIAL
            if failed or unattempted
            else ResultStatus.COMPLETED
        ),
        data=data,
        errors=errors,
    )
    _render_proxy_test_summary(outcome)
    if passed == 0:
        _command_error("No proxy session passed the bounded live probe.")
    if failed or unattempted:
        raise click.exceptions.Exit(2)
