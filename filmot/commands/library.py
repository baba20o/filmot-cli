"""Transcript-library and research-session commands."""

from contextlib import nullcontext
import hashlib
import json as json_mod
from pathlib import Path
import shlex
from typing import Optional

import click
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..cli_support import (
    command_error as _command_error,
    console,
    emit_raw_result as _emit_raw_result,
)
from ..schemas import (
    CommandResult,
    ECHO_ANALYSIS_SCHEMA,
    EchoAnalysisResultData,
    ErrorDetail,
    LibraryResultData,
    ResultStatus,
    SessionResultData,
)
from .search import _format_duration


ECHO_HUMAN_PAIR_LIMIT = 25


def _render_library_list(
    outcome: CommandResult[LibraryResultData],
) -> None:
    """Render a typed library inventory."""
    _render_library_failure(outcome, "Library listing failed")
    data = outcome.data
    rows = data.get("rows") or []
    topic = data.get("topic")
    if not rows:
        if topic:
            console.print(
                f"[yellow]No transcripts in topic: {topic}[/yellow]"
            )
        else:
            console.print(
                "[yellow]Library is empty. Use 'filmot transcript VIDEO_ID "
                "--save-to TOPIC' to add.[/yellow]"
            )
        return

    if topic:
        table = Table(title=f"Transcripts in '{topic}'")
        table.add_column("Video ID", style="cyan")
        table.add_column("Title", style="white", max_width=50)
        table.add_column("Channel", style="green")
        table.add_column("Size", style="dim")
        table.add_column("Saved", style="dim")
        for row in rows:
            title = str(row.get("title", "Unknown"))
            if len(title) > 47:
                title = title[:47] + "..."
            table.add_row(
                str(row.get("video_id", "")),
                title,
                str(row.get("channel", "Unknown")),
                f"{int(row.get('char_count', 0)):,} chars",
                (
                    str(row.get("saved_at"))[:10]
                    if row.get("saved_at")
                    else "Unknown"
                ),
            )
        console.print(table)
        console.print(f"\n[dim]Total: {len(rows)} transcripts[/dim]")
        return

    table = Table(title="Transcript Library")
    table.add_column("Topic", style="cyan")
    table.add_column("Transcripts", style="white", justify="right")
    for row in rows:
        table.add_row(str(row.get("topic", "")), str(row.get("count", 0)))
    console.print(table)
    total = sum(int(row.get("count", 0)) for row in rows)
    console.print(
        f"\n[dim]Total: {len(rows)} topics, {total} transcripts[/dim]"
    )


def _render_library_failure(
    outcome: CommandResult[LibraryResultData],
    prefix: str,
) -> None:
    """Render a structured library failure through Click's error boundary."""
    if outcome.status_value != ResultStatus.FAILED.value:
        return
    message = (
        outcome.errors[0].message
        if outcome.errors
        else "unknown library error"
    )
    _command_error(f"{prefix}: {message}")


def _render_library_search(
    outcome: CommandResult[LibraryResultData],
) -> None:
    """Render library-search output from its typed result."""
    _render_library_failure(outcome, "Library search failed")
    data = outcome.data
    query = str(data.get("query") or "")
    topic = data.get("topic")
    rows = data.get("rows") or []
    summary = data.get("summary") or {}

    if summary.get("fallback_used"):
        console.print(
            "[dim]No exact word matches. Showing substring matches "
            "(plurals/inflections):[/dim]"
        )

    if not rows:
        console.print(f"[yellow]No matches for '{query}'[/yellow]")
        if not topic:
            console.print(
                "[dim]Try searching within a specific topic: "
                "--topic NAME[/dim]"
            )
        return

    console.print(
        f"\n[bold]Found {int(summary.get('matches', 0))} matches across "
        f"{len(rows)} transcripts[/bold]\n"
    )
    for row in rows[:10]:
        console.print(
            f"[bold cyan]{row['video_id']}[/bold cyan] "
            f"({row['match_count']} matches)"
        )
        console.print(
            f"  Topic: [green]{row['topic']}[/green] | "
            f"{row.get('title', 'Unknown')} - "
            f"{row.get('channel', 'Unknown')}"
        )
        details = row.get("match_details") or []
        if details:
            for match in details[:2]:
                locator = ""
                if match.get("timestamp") and match.get("url"):
                    locator = "[[link={url}]{timestamp}[/link]] ".format(
                        url=match["url"],
                        timestamp=match["timestamp"],
                    )
                console.print(
                    "  [dim]{}{}[/dim]".format(
                        locator,
                        match.get("excerpt", ""),
                    )
                )
        else:
            for match in row.get("matches", [])[:2]:
                console.print(f"  [dim]...{match}[/dim]")
        console.print()


def _emit_library_raw(outcome: CommandResult[LibraryResultData]) -> None:
    """Write the same typed outcome used by the human renderer."""
    _emit_raw_result(outcome)


@click.group()
def library():
    """Manage local transcript library.

    The library stores transcripts organized by topic/keyword for
    building curated knowledge bases.

    Examples:

    \b
        filmot library list                    # List all topics
        filmot library list prompt-injection   # List transcripts in topic
        filmot library search "attack"         # Search across all transcripts
        filmot library search "claim" --raw    # Timestamped local citations
        filmot library echoes prompt-injection  # Advisory full-text lineage scan
        filmot library context prompt-injection # Get all text for LLM context
        filmot library stats                   # Show library statistics
    """
    pass


def _youtube_metadata_video_ids(values: tuple[str, ...]) -> list[str]:
    """Normalize repeated/comma-separated exact YouTube IDs and URLs."""
    from ..discovery import youtube_video_id

    output = []
    seen = set()
    for value in values:
        for part in str(value).split(","):
            supplied = part.strip()
            if not supplied:
                continue
            video_id = youtube_video_id(supplied)
            if video_id is None:
                raise click.BadParameter(
                    "expected an 11-character YouTube video ID or supported "
                    "public URL; got {!r}".format(supplied),
                    param_hint="--video",
                )
            if video_id not in seen:
                seen.add(video_id)
                output.append(video_id)
    if values and not output:
        raise click.BadParameter(
            "at least one YouTube video ID is required when --video is supplied",
            param_hint="--video",
        )
    return output


def _youtube_metadata_inventory(lib, topic: Optional[str], video_ids: list[str]):
    """Return a stable record-copy inventory for one CLI selection."""
    if not video_ids:
        rows = lib.youtube_metadata_inventory(topic=topic)
    else:
        rows = []
        for video_id in video_ids:
            rows.extend(lib.youtube_metadata_inventory(
                topic=topic,
                video_id=video_id,
            ))
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise TypeError("YouTube metadata inventory must be a list of objects")
    deduped = {}
    for row in rows:
        copied = dict(row)
        key = (
            str(copied.get("topic") or ""),
            str(copied.get("video_id") or ""),
            str(copied.get("path") or ""),
        )
        deduped.setdefault(key, copied)
    return [deduped[key] for key in sorted(deduped)]


def _youtube_metadata_summary(rows: list[dict]) -> dict:
    classifications = {"current": 0, "expired": 0, "unmanaged": 0}
    states = {}
    for row in rows:
        classification = str(row.get("classification") or "unmanaged")
        classifications[classification] = classifications.get(classification, 0) + 1
        state = str(row.get("state") or "unmanaged")
        states[state] = states.get(state, 0) + 1
    return {
        "records": len(rows),
        "current": classifications.get("current", 0),
        "expired": classifications.get("expired", 0),
        "unmanaged": classifications.get("unmanaged", 0),
        "invalid": states.get("invalid", 0),
        "managed": sum(1 for row in rows if row.get("managed")),
        "legacy_adoptable": sum(
            1 for row in rows if row.get("legacy_adoptable")
        ),
        "states": states,
    }


def _select_youtube_metadata_rows(
    rows: list[dict],
    *,
    explicit: bool,
    include_all: bool,
) -> list[dict]:
    if explicit or include_all:
        return list(rows)
    return [
        row for row in rows if row.get("classification") == "expired"
    ]


def _bounded_metadata_targets(
    rows: list[dict],
    *,
    max_videos: int,
) -> tuple[list[dict], list[str], int]:
    """Apply an exact unique-video quota budget while retaining topic copies."""
    selected_ids = []
    selected_set = set()
    for row in rows:
        video_id = row.get("video_id")
        if not isinstance(video_id, str):
            continue
        if video_id not in selected_set:
            if len(selected_ids) >= max_videos:
                continue
            selected_set.add(video_id)
            selected_ids.append(video_id)
    bounded = [row for row in rows if row.get("video_id") in selected_set]
    total_unique = len({
        row.get("video_id")
        for row in rows
        if isinstance(row.get("video_id"), str)
    })
    return bounded, selected_ids, max(0, total_unique - len(selected_ids))


def _youtube_request_reference(request: dict) -> str:
    """Hash a credential-free effective request for lifecycle provenance."""
    from ..redaction import redact_sensitive_value

    safe_request = redact_sensitive_value(request)
    encoded = json_mod.dumps(
        safe_request,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@click.group("yt-data")
def yt_data():
    """Inspect, refresh, or purge saved YouTube API metadata.

    These commands manage API-derived metadata only. They never acquire
    captions and never delete transcript text or the corresponding YouTube
    resource.
    """
    pass


def _render_yt_data_status(
    outcome: CommandResult[LibraryResultData],
) -> None:
    _render_library_failure(outcome, "YouTube metadata status failed")
    rows = outcome.data.get("rows") or []
    summary = outcome.data.get("summary") or {}
    console.print(Panel(
        "[bold]Records:[/bold] {} | [green]Current:[/green] {} | "
        "[yellow]Expired:[/yellow] {} | [dim]Unmanaged:[/dim] {} | "
        "[red]Invalid:[/red] {}".format(
            summary.get("records", 0),
            summary.get("current", 0),
            summary.get("expired", 0),
            summary.get("unmanaged", 0),
            summary.get("invalid", 0),
        ),
        title="Saved YouTube API Metadata",
    ))
    if not rows:
        return
    table = Table()
    table.add_column("Topic", style="cyan")
    table.add_column("Video ID", style="white")
    table.add_column("Status")
    table.add_column("Expires (UTC)", style="dim")
    table.add_column("Fields", justify="right")
    for row in rows:
        classification = str(row.get("classification") or "unmanaged")
        color = {
            "current": "green",
            "expired": "yellow",
            "unmanaged": "dim",
        }.get(classification, "red")
        table.add_row(
            str(row.get("topic") or ""),
            str(row.get("video_id") or ""),
            "[{}]{}[/{}]".format(color, classification, color),
            str(row.get("expires_at") or "—"),
            str(row.get("owned_path_count") or 0),
        )
    console.print(table)


@yt_data.command("status")
@click.option("--topic", default=None, help="Limit inspection to one topic")
@click.option(
    "--video", "videos", multiple=True,
    help="Exact video ID/URL (repeat or comma-separate)",
)
@click.option("--expired", "expired_only", is_flag=True, help="Show expired rows only")
@click.option("--raw", is_flag=True, help="Output one versioned JSON result")
def yt_data_status(
    topic: Optional[str],
    videos: tuple[str, ...],
    expired_only: bool,
    raw: bool,
) -> None:
    """Inspect freshness and ownership locally without an API key."""
    from ..library import get_library

    video_ids = _youtube_metadata_video_ids(videos)
    try:
        rows = _youtube_metadata_inventory(get_library(), topic, video_ids)
        if expired_only:
            rows = [
                row for row in rows
                if row.get("classification") == "expired"
            ]
        data: LibraryResultData = {
            "topic": topic,
            "rows": rows,
            "summary": {
                **_youtube_metadata_summary(rows),
                "offline": True,
                "expired_only": expired_only,
                "video_ids": video_ids,
            },
        }
        outcome = CommandResult(
            command="yt-data-status",
            status=ResultStatus.COMPLETED if rows else ResultStatus.EMPTY,
            data=data,
        )
    except Exception as error:
        data = {
            "topic": topic,
            "rows": [],
            "summary": {"offline": True, "records": 0},
        }
        outcome = CommandResult.failed(
            "yt-data-status",
            data,
            ErrorDetail.from_exception(error, stage="inventory"),
        )
    if raw:
        _emit_library_raw(outcome)
    else:
        _render_yt_data_status(outcome)


def _render_yt_data_refresh(
    outcome: CommandResult[LibraryResultData],
) -> None:
    _render_library_failure(outcome, "YouTube metadata refresh failed")
    summary = outcome.data.get("summary") or {}
    if summary.get("dry_run"):
        console.print(
            "[cyan]Would refresh {} record(s) across {} unique video(s); "
            "no API calls or writes were made.[/cyan]".format(
                summary.get("selected_records", 0),
                summary.get("selected_videos", 0),
            )
        )
        return
    console.print(Panel(
        "[bold]Selected videos:[/bold] {} | [bold]API attempts:[/bold] {}\n"
        "[green]Refreshed records:[/green] {} | "
        "[yellow]Not returned:[/yellow] {} | "
        "[red]Record failures:[/red] {} | "
        "[dim]Unprocessed records:[/dim] {}".format(
            summary.get("selected_videos", 0),
            summary.get("api_calls", 0),
            summary.get("refreshed_records", 0),
            summary.get("not_returned_records", 0),
            summary.get("failed_records", 0),
            summary.get("unprocessed_records", 0),
        ),
        title="YouTube Metadata Refresh",
    ))
    if summary.get("request_ref"):
        console.print("[dim]Request: {}[/dim]".format(summary["request_ref"]))


@yt_data.command("refresh")
@click.option("--topic", default=None, help="Limit refresh to one topic")
@click.option(
    "--video", "videos", multiple=True,
    help="Refresh exact saved video ID/URL (repeat or comma-separate)",
)
@click.option(
    "--all", "include_all", is_flag=True,
    help="Refresh current and unmanaged rows too (default: expired only)",
)
@click.option(
    "--max-videos", default=500, type=click.IntRange(1, 5000),
    show_default=True, help="Unique-video/API work budget",
)
@click.option("--dry-run", is_flag=True, help="Preview scope without API calls or writes")
@click.option(
    "--connect-timeout", default=5.0,
    type=click.FloatRange(min=0, min_open=True), show_default=True,
)
@click.option(
    "--read-timeout", default=20.0,
    type=click.FloatRange(min=0, min_open=True), show_default=True,
)
@click.option(
    "--retries", default=2, type=click.IntRange(0, 5), show_default=True,
)
@click.option("--raw", is_flag=True, help="Output one versioned JSON result")
def yt_data_refresh(
    topic: Optional[str],
    videos: tuple[str, ...],
    include_all: bool,
    max_videos: int,
    dry_run: bool,
    connect_timeout: float,
    read_timeout: float,
    retries: int,
    raw: bool,
) -> None:
    """Refresh saved API metadata through videos.list, without captions.

    With no selector, only expired records are refreshed. ``--video`` targets
    every saved topic copy of an exact ID; ``--all`` explicitly widens scope.
    """
    from ..ledger import log_result
    from ..library import get_library

    if include_all and videos:
        raise click.UsageError("--all cannot be combined with --video")
    video_ids = _youtube_metadata_video_ids(videos)
    try:
        lib = get_library()
        inventory = _youtube_metadata_inventory(lib, topic, video_ids)
        selected = _select_youtube_metadata_rows(
            inventory,
            explicit=bool(video_ids),
            include_all=include_all,
        )
        selected, selected_ids, truncated = _bounded_metadata_targets(
            selected,
            max_videos=max_videos,
        )
    except Exception as error:
        data: LibraryResultData = {
            "topic": topic,
            "rows": [],
            "summary": {"selected_records": 0, "selected_videos": 0},
        }
        outcome = CommandResult.failed(
            "yt-data-refresh", data,
            ErrorDetail.from_exception(error, stage="inventory"),
        )
        log_result("yt-data-refresh", outcome, topic=topic, data=outcome.data["summary"])
        if raw:
            _emit_library_raw(outcome)
        else:
            _render_yt_data_refresh(outcome)
        return

    base_summary = {
        "selected_records": len(selected),
        "selected_videos": len(selected_ids),
        "truncated_videos": truncated,
        "scope": "explicit" if video_ids else "all" if include_all else "expired",
        "dry_run": dry_run,
    }
    if dry_run or not selected_ids:
        data = {
            "topic": topic,
            "rows": selected,
            "summary": {**base_summary, "api_calls": 0},
        }
        outcome = CommandResult(
            command="yt-data-refresh",
            status=ResultStatus.COMPLETED if selected else ResultStatus.EMPTY,
            data=data,
            warnings=(
                ["Selection exceeded --max-videos and was truncated."]
                if truncated else []
            ),
        )
        if not dry_run:
            log_result(
                "yt-data-refresh", outcome, topic=topic,
                data=outcome.data["summary"],
            )
        if raw:
            _emit_library_raw(outcome)
        else:
            _render_yt_data_refresh(outcome)
        return

    from .search import (
        _normalize_youtube_video_provider_result,
        _youtube_error_details,
    )
    from ..youtube_search import get_video_details_detailed

    try:
        provider = _normalize_youtube_video_provider_result(
            get_video_details_detailed(
                selected_ids,
                timeout=(connect_timeout, read_timeout),
                retries=retries,
            )
        )
    except Exception as error:
        data = {
            "topic": topic,
            "rows": [],
            "summary": {**base_summary, "api_calls": 0},
        }
        outcome = CommandResult.failed(
            "yt-data-refresh", data,
            ErrorDetail.from_exception(error, stage="video-details"),
        )
        log_result("yt-data-refresh", outcome, topic=topic, data=outcome.data["summary"])
        if raw:
            _emit_library_raw(outcome)
        else:
            _render_yt_data_refresh(outcome)
        return

    request = dict(provider.get("request") or {})
    coverage = dict(provider.get("coverage") or {})
    request_ref = _youtube_request_reference(request)
    details = {
        row.get("video_id"): row
        for row in provider.get("videos") or []
        if isinstance(row.get("video_id"), str)
    }
    id_outcomes = {
        row.get("video_id"): row
        for row in provider.get("id_outcomes") or []
        if isinstance(row.get("video_id"), str)
    }
    mutation_rows = []
    mutation_errors = []
    unprocessed_records = 0
    for target in selected:
        target_id = str(target.get("video_id") or "")
        target_topic = str(target.get("topic") or "")
        id_outcome = id_outcomes.get(target_id, {})
        state = id_outcome.get("status")
        if state == "unprocessed" or state not in {"observed", "not_returned"}:
            unprocessed_records += 1
            continue
        try:
            result = lib.replace_youtube_metadata(
                target_id,
                target_topic,
                details.get(target_id) if state == "observed" else None,
                observed_at=id_outcome.get("observed_at"),
                expires_at=id_outcome.get("expires_at"),
                request_ref=request_ref,
            )
            mutation_rows.append({
                "topic": target_topic,
                "video_id": target_id,
                **result,
            })
        except Exception as error:
            mutation_errors.append(ErrorDetail.from_exception(
                error,
                stage="store-metadata",
                details={"topic": target_topic, "video_id": target_id},
            ))

    provider_errors = _youtube_error_details(provider.get("errors"))
    warnings = list(provider.get("warnings") or [])
    if truncated:
        warnings.append("Selection exceeded --max-videos and was truncated.")
    failed_records = len(mutation_errors)
    refreshed_records = sum(
        1 for row in mutation_rows if row.get("state") == "current"
    )
    not_returned_records = sum(
        1 for row in mutation_rows if row.get("state") == "not_returned"
    )
    summary = {
        **base_summary,
        "request_ref": request_ref,
        "api_calls": coverage.get("api_calls", coverage.get("api_attempts", 0)),
        "refreshed_records": refreshed_records,
        "not_returned_records": not_returned_records,
        "failed_records": failed_records,
        "unprocessed_records": unprocessed_records,
        "conflict_paths": sum(
            len(row.get("conflict_paths") or []) for row in mutation_rows
        ),
    }
    incomplete = bool(
        provider_errors or mutation_errors or unprocessed_records or truncated
    )
    status = (
        ResultStatus.FAILED
        if provider_errors and not coverage.get("batches_completed")
        else ResultStatus.PARTIAL
        if incomplete
        else ResultStatus.COMPLETED
    )
    data = {
        "topic": topic,
        "rows": mutation_rows,
        "summary": summary,
        "request": request,
        "coverage": coverage,
    }
    outcome = CommandResult(
        command="yt-data-refresh",
        status=status,
        data=data,
        errors=[*provider_errors, *mutation_errors],
        warnings=warnings,
    )
    log_result(
        "yt-data-refresh", outcome, topic=topic,
        data={
            **summary,
            "request": request,
            "coverage": coverage,
        },
    )
    if raw:
        _emit_library_raw(outcome)
    else:
        _render_yt_data_refresh(outcome)


def _render_yt_data_purge(
    outcome: CommandResult[LibraryResultData],
) -> None:
    _render_library_failure(outcome, "YouTube metadata purge failed")
    summary = outcome.data.get("summary") or {}
    verb = "Would purge" if summary.get("dry_run") else "Purged"
    console.print(
        "[{}]{} {} API-metadata record(s); transcript content was untouched."
        "[/{}]".format(
            "cyan" if summary.get("dry_run") else "green",
            verb,
            summary.get("purged_records", summary.get("selected_records", 0)),
            "cyan" if summary.get("dry_run") else "green",
        )
    )


@yt_data.command("purge")
@click.option("--topic", default=None, help="Limit purge to one topic")
@click.option(
    "--video", "videos", multiple=True,
    help="Purge exact saved video ID/URL (repeat or comma-separate)",
)
@click.option(
    "--all", "include_all", is_flag=True,
    help="Include current metadata (default: expired only)",
)
@click.option(
    "--max-records", default=5000, type=click.IntRange(1),
    show_default=True, help="Record mutation budget",
)
@click.option("--dry-run", is_flag=True, help="Preview scope without writing")
@click.option("--yes", is_flag=True, help="Confirm metadata purge without prompting")
@click.option("--raw", is_flag=True, help="Output one versioned JSON result")
def yt_data_purge(
    topic: Optional[str],
    videos: tuple[str, ...],
    include_all: bool,
    max_records: int,
    dry_run: bool,
    yes: bool,
    raw: bool,
) -> None:
    """Delete owned API fields while retaining transcripts and local metadata."""
    from ..ledger import log_result
    from ..library import get_library

    if include_all and videos:
        raise click.UsageError("--all cannot be combined with --video")
    video_ids = _youtube_metadata_video_ids(videos)
    try:
        lib = get_library()
        inventory = _youtube_metadata_inventory(lib, topic, video_ids)
        selected = _select_youtube_metadata_rows(
            inventory,
            explicit=bool(video_ids),
            include_all=include_all,
        )
        truncated = max(0, len(selected) - max_records)
        selected = selected[:max_records]
    except Exception as error:
        data: LibraryResultData = {
            "topic": topic,
            "rows": [],
            "summary": {"selected_records": 0},
        }
        outcome = CommandResult.failed(
            "yt-data-purge", data,
            ErrorDetail.from_exception(error, stage="inventory"),
        )
        log_result("yt-data-purge", outcome, topic=topic, data=outcome.data["summary"])
        if raw:
            _emit_library_raw(outcome)
        else:
            _render_yt_data_purge(outcome)
        return

    summary = {
        "selected_records": len(selected),
        "truncated_records": truncated,
        "scope": "explicit" if video_ids else "all" if include_all else "expired",
        "dry_run": dry_run,
    }
    if dry_run or not selected:
        data = {"topic": topic, "rows": selected, "summary": summary}
        outcome = CommandResult(
            command="yt-data-purge",
            status=ResultStatus.COMPLETED if selected else ResultStatus.EMPTY,
            data=data,
            warnings=(
                ["Selection exceeded --max-records and was truncated."]
                if truncated else []
            ),
        )
        if not dry_run:
            log_result("yt-data-purge", outcome, topic=topic, data=summary)
        if raw:
            _emit_library_raw(outcome)
        else:
            _render_yt_data_purge(outcome)
        return

    if not yes:
        confirmed = click.confirm(
            "Purge YouTube API metadata from {} saved record(s)? "
            "Transcript text will remain".format(len(selected)),
            default=False,
        )
        if not confirmed:
            raise click.Abort()

    mutation_rows = []
    errors = []
    for target in selected:
        target_id = str(target.get("video_id") or "")
        target_topic = str(target.get("topic") or "")
        try:
            result = lib.purge_youtube_metadata(
                target_id,
                target_topic,
                reason=(
                    "expired"
                    if target.get("classification") == "expired"
                    else "operator"
                ),
            )
            mutation_rows.append({
                "topic": target_topic,
                "video_id": target_id,
                **result,
            })
        except Exception as error:
            errors.append(ErrorDetail.from_exception(
                error,
                stage="purge-metadata",
                details={"topic": target_topic, "video_id": target_id},
            ))
    summary.update({
        "purged_records": sum(
            1 for row in mutation_rows if row.get("status") == "updated"
        ),
        "noop_records": sum(
            1 for row in mutation_rows if row.get("status") == "noop"
        ),
        "failed_records": len(errors),
        "removed_paths": sum(
            len(row.get("removed_paths") or []) for row in mutation_rows
        ),
    })
    if truncated:
        errors.append(ErrorDetail(
            type="SelectionTruncated",
            message="Selection exceeded --max-records",
            stage="selection",
            details={"truncated_records": truncated},
        ))
    outcome = CommandResult(
        command="yt-data-purge",
        status=(
            ResultStatus.PARTIAL if errors else ResultStatus.COMPLETED
        ),
        data={"topic": topic, "rows": mutation_rows, "summary": summary},
        errors=errors,
    )
    log_result(
        "yt-data-purge", outcome, topic=topic,
        data=summary,
    )
    if raw:
        _emit_library_raw(outcome)
    else:
        _render_yt_data_purge(outcome)


@library.command("list")
@click.argument("topic", required=False)
@click.option("--raw", is_flag=True, help="Output one versioned JSON result")
def library_list(topic: str, raw: bool):
    """List topics or transcripts in a topic.

    Without arguments, lists all topics.
    With a topic name, lists all transcripts in that topic. This is a
    read-only inspection: it does not create storage or append a session event.
    """
    try:
        from ..library import get_library
        lib = get_library()

        if topic:
            # List transcripts in topic
            transcripts = lib.list_transcripts(topic)
            data: LibraryResultData = {
                "topic": topic,
                "rows": transcripts,
                "summary": {"transcripts": len(transcripts)},
            }
            outcome = CommandResult(
                command="library-list",
                status=(
                    ResultStatus.COMPLETED
                    if transcripts
                    else ResultStatus.EMPTY
                ),
                data=data,
            )
        else:
            # List all topics
            topics = lib.list_topics()
            total = sum(int(row.get("count", 0)) for row in topics)
            data = {
                "rows": topics,
                "summary": {
                    "topics": len(topics),
                    "transcripts": total,
                },
            }
            outcome = CommandResult(
                command="library-list",
                status=(
                    ResultStatus.COMPLETED
                    if topics
                    else ResultStatus.EMPTY
                ),
                data=data,
            )
    except Exception as error:
        data = {
            "topic": topic,
            "rows": [],
            "summary": {"topics": 0, "transcripts": 0},
        }
        outcome = CommandResult.failed(
            "library-list",
            data,
            ErrorDetail.from_exception(
                error,
                stage="list",
                details={"topic": topic},
            ),
        )
        if raw:
            _emit_library_raw(outcome)
        else:
            _render_library_list(outcome)
        raise click.exceptions.Exit(1)

    if raw:
        _emit_library_raw(outcome)
    else:
        _render_library_list(outcome)


@library.command("search")
@click.argument("query")
@click.option("--topic", "-t", default=None, help="Limit search to specific topic")
@click.option("--substring", is_flag=True, help="Use substring matching instead of word-boundary matching")
@click.option("--raw", is_flag=True, help="Output one versioned JSON result")
def library_search(query: str, topic: str, substring: bool, raw: bool):
    """Search for text across saved transcripts.

    Uses word-boundary matching by default (searching "ore" won't match "more").
    Use --substring for the old behavior. Timestamped saved segments produce
    citation-ready locators in --raw output. This command never logs or mutates
    the project library.
    """
    used_substring = substring
    fallback_used = False
    stage = "open-library"
    try:
        from ..library import get_library
        lib = get_library()
        stage = "search"
        status = (
            nullcontext()
            if raw
            else console.status(f"Searching library for '{query}'...")
        )
        with status:
            results = lib.search(
                query,
                topic=topic,
                substring=substring,
            )

        # Auto-fallback catches plurals/inflections.
        if not results and not substring:
            results = lib.search(query, topic=topic, substring=True)
            if results:
                used_substring = True
                fallback_used = True
        stage = "build-result"
        if not isinstance(results, list):
            raise TypeError("Library search results must be a list")
        total_matches = sum(int(row["match_count"]) for row in results)
        data = {
            "topic": topic,
            "query": query,
            "rows": results,
            "summary": {
                "substring": used_substring,
                "fallback_used": fallback_used,
                "sources": len(results),
                "matches": total_matches,
            },
        }
        outcome = CommandResult(
            command="library-search",
            status=(
                ResultStatus.COMPLETED
                if results
                else ResultStatus.EMPTY
            ),
            data=data,
            warnings=(
                ["Used substring fallback after word-boundary search was empty."]
                if fallback_used
                else []
            ),
        )
    except Exception as error:
        data: LibraryResultData = {
            "topic": topic,
            "query": query,
            "rows": [],
            "summary": {
                "substring": used_substring,
                "fallback_used": fallback_used,
                "sources": 0,
                "matches": 0,
            },
        }
        outcome = CommandResult.failed(
            "library-search",
            data,
            ErrorDetail.from_exception(
                error,
                stage=stage,
                details={"topic": topic, "substring": used_substring},
            ),
        )
        if raw:
            _emit_library_raw(outcome)
        else:
            _render_library_search(outcome)
        raise click.exceptions.Exit(1)
    if raw:
        _emit_library_raw(outcome)
    else:
        _render_library_search(outcome)


def _render_library_context(
    outcome: CommandResult[LibraryResultData],
) -> None:
    """Render context delivery from the same result written to the ledger."""
    if outcome.status_value == ResultStatus.FAILED.value:
        stage = outcome.errors[0].stage if outcome.errors else None
        prefix = (
            "Error saving file"
            if stage == "write-output"
            else "Error building context"
        )
        _render_library_failure(outcome, prefix)
        return

    data = outcome.data
    topic = str(data.get("topic") or "")
    rows = data.get("rows") or []
    summary = data.get("summary") or {}
    if outcome.status_value == ResultStatus.EMPTY.value:
        console.print(
            f"[yellow]No transcripts in topic: {topic}[/yellow]"
        )
        return

    output = data.get("output")
    if output:
        console.print(f"[green]✓ Saved context to: {output}[/green]")
        console.print(
            f"[dim]Size: {int(summary.get('chars', 0)):,} characters[/dim]"
        )
        return

    content = str(rows[0].get("content") or "") if rows else ""
    console.print(content)


@library.command("context")
@click.argument("topic")
@click.option("--max-chars", "-m", default=None, type=click.IntRange(1), help="Maximum total characters")
@click.option("--output", "-o", default=None, help="Save to file instead of printing")
@click.option("--format", "-f", "fmt", type=click.Choice(["text", "structured"]), default="text", help="Output format: text (plain) or structured (markdown with metadata)")
def library_context(topic: str, max_chars: int, output: str, fmt: str):
    """Get all transcripts in a topic as combined text.

    Useful for providing LLM context. Concatenates all transcripts
    with headers separating each video.

    Use --format structured for markdown with full metadata headers.
    Printing context is read-only and does not log. Writing --output (including
    the structured default output) creates missing parent directories and
    records the materialized artifact event. Parent-creation and file-write
    errors remain typed write-output failures.
    """
    from ..library import get_library
    from ..ledger import log_result
    lib = get_library()

    try:
        with console.status(f"Building context from '{topic}'..."):
            if fmt == "structured":
                context = _build_structured_context(lib, topic, max_chars)
            else:
                context = lib.get_context(topic, max_chars=max_chars)
    except Exception as error:
        data: LibraryResultData = {
            "topic": topic,
            "rows": [],
            "summary": {
                "format": fmt,
                "max_chars": max_chars,
                "chars": 0,
                "delivery": "failed",
            },
        }
        outcome = CommandResult.failed(
            "library-context",
            data,
            ErrorDetail.from_exception(
                error,
                stage="build-context",
                details={"topic": topic, "format": fmt},
            ),
        )
        _render_library_context(outcome)
        raise click.exceptions.Exit(1)

    if not context:
        data = {
            "topic": topic,
            "rows": [],
            "summary": {
                "format": fmt,
                "max_chars": max_chars,
                "chars": 0,
                "delivery": "empty",
            },
        }
        outcome = CommandResult(
            command="library-context",
            status=ResultStatus.EMPTY,
            data=data,
        )
        _render_library_context(outcome)
        return

    # Structured output defaults to a file instead of dumping large markdown.
    if fmt == "structured" and not output:
        from ..library import normalize_topic_name
        # Build the default filename from the normalized topic so path
        # separators or OS-invalid characters in TOPIC never become a path.
        output = f"{normalize_topic_name(topic)}-context.md"

    if output:
        try:
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            with open(output, "w", encoding="utf-8") as handle:
                handle.write(context)
        except Exception as error:
            data = {
                "topic": topic,
                "rows": [{"content": context}],
                "output": output,
                "summary": {
                    "format": fmt,
                    "max_chars": max_chars,
                    "chars": len(context),
                    "delivery": "failed",
                },
            }
            outcome = CommandResult.failed(
                "library-context",
                data,
                ErrorDetail.from_exception(
                    error,
                    stage="write-output",
                    details={"output": output},
                ),
            )
            log_result(
                "library_context",
                outcome,
                topic=topic,
                data={
                    "format": fmt,
                    "max_chars": max_chars,
                    "chars": len(context),
                    "output": output,
                    "delivery": "failed",
                },
            )
            _render_library_context(outcome)
            raise click.exceptions.Exit(1)
        delivery = "saved"
    else:
        delivery = "rendered"

    data = {
        "topic": topic,
        "rows": [{"content": context}],
        "summary": {
            "format": fmt,
            "max_chars": max_chars,
            "chars": len(context),
            "delivery": delivery,
        },
    }
    if output:
        data["output"] = output
    outcome = CommandResult.completed("library-context", data)
    if output:
        log_result(
            "library_context",
            outcome,
            topic=topic,
            data={
                "format": fmt,
                "max_chars": max_chars,
                "chars": len(context),
                "output": output,
                "delivery": delivery,
            },
        )
    _render_library_context(outcome)


def _build_structured_context(lib, topic: str, max_chars=None) -> str:
    """Build markdown-formatted context with full metadata."""
    transcripts = lib.list_transcripts(topic)
    if not transcripts:
        return ""

    parts = [f"# Topic: {topic}\n"]
    total_chars = len(parts[0])

    for idx, t in enumerate(transcripts, 1):
        data = lib.get(t["video_id"], topic)
        if not data:
            continue

        metadata = data.get("metadata", {})
        duration = metadata.get("duration_seconds", 0)
        duration_str = _format_duration(int(duration)) if duration else "Unknown"
        views = metadata.get("views", 0)
        views_str = f"{views:,}" if views else "N/A"

        header = f"\n---\n\n## {idx}. {metadata.get('title', t['video_id'])}\n"
        header += f"- **Channel:** {metadata.get('channel', 'Unknown')}\n"
        header += f"- **Video ID:** {t['video_id']}\n"
        header += f"- **Duration:** {duration_str} | **Views:** {views_str}\n"
        header += f"- **Language:** {metadata.get('language', 'N/A')}"
        header += f" (auto-generated)" if metadata.get('is_generated') else ""
        header += f"\n- **Saved:** {t.get('saved_at', 'Unknown')[:10]}\n\n"

        transcript = data.get("transcript", "")

        content = header + transcript + "\n"

        if max_chars and total_chars + len(content) > max_chars:
            remaining = max_chars - total_chars
            if remaining > len(header) + 500:
                content = content[:remaining] + "\n\n[TRUNCATED]\n"
                parts.append(content)
            break

        parts.append(content)
        total_chars += len(content)

    return "\n".join(parts)


def _render_library_stats(
    outcome: CommandResult[LibraryResultData],
) -> None:
    """Render library statistics from a typed result."""
    _render_library_failure(outcome, "Library statistics failed")
    summary = outcome.data.get("summary") or {}
    rows = outcome.data.get("rows") or []
    console.print(
        Panel(
            f"[bold]Topics:[/bold] {summary.get('total_topics', 0)}\n"
            f"[bold]Transcripts:[/bold] "
            f"{summary.get('total_transcripts', 0)}\n"
            f"[bold]Total Size:[/bold] "
            f"{summary.get('total_size_mb', 0)} MB",
            title="Library Statistics",
        )
    )
    if rows:
        console.print("\n[bold]By Topic:[/bold]")
        for row in rows:
            console.print(
                f"  {row['topic']}: {row['count']} transcripts"
            )


@library.command("stats")
def library_stats():
    """Show library statistics without logging or writing project state."""
    from ..library import get_library
    lib = get_library()

    try:
        stats = lib.stats()
    except Exception as error:
        data: LibraryResultData = {
            "rows": [],
            "summary": {
                "total_topics": 0,
                "total_transcripts": 0,
                "total_size_bytes": 0,
                "total_size_mb": 0,
            },
        }
        outcome = CommandResult.failed(
            "library-stats",
            data,
            ErrorDetail.from_exception(error, stage="statistics"),
        )
        _render_library_stats(outcome)
        raise click.exceptions.Exit(1)

    rows = list(stats.get("topics") or [])
    data = {
        "rows": rows,
        "summary": {
            "total_topics": int(stats.get("total_topics", 0)),
            "total_transcripts": int(
                stats.get("total_transcripts", 0)
            ),
            "total_size_bytes": int(stats.get("total_size_bytes", 0)),
            "total_size_mb": stats.get("total_size_mb", 0),
        },
    }
    outcome = CommandResult(
        command="library-stats",
        status=(
            ResultStatus.COMPLETED
            if data["summary"]["total_topics"]
            else ResultStatus.EMPTY
        ),
        data=data,
    )
    _render_library_stats(outcome)


def _render_library_delete(
    outcome: CommandResult[LibraryResultData],
) -> None:
    """Render a transcript/topic deletion outcome."""
    _render_library_failure(outcome, "Library delete failed")
    summary = outcome.data.get("summary") or {}
    target = str(summary.get("target") or "")
    scope = summary.get("scope")
    deleted = summary.get("deleted")

    if scope == "topic":
        count = int(deleted or 0)
        if count:
            console.print(
                f"[green]✓ Deleted topic '{target}' "
                f"({count} transcripts)[/green]"
            )
        else:
            console.print(
                f"[yellow]Topic not found or empty: {target}[/yellow]"
            )
        return

    if bool(deleted):
        topic = outcome.data.get("topic")
        scope_label = f"from {topic}" if topic else "from all topics"
        console.print(
            f"[green]✓ Deleted transcript {target} "
            f"{scope_label}[/green]"
        )
    else:
        console.print(
            f"[yellow]Transcript not found: {target}[/yellow]"
        )


@library.command("delete")
@click.argument("target")
@click.option("--topic", "-t", default=None, help="Delete from specific topic only")
@click.option("--all", "delete_all", is_flag=True, help="Delete entire topic (use with topic as TARGET)")
@click.confirmation_option(prompt="Are you sure you want to delete?")
def library_delete(target: str, topic: str, delete_all: bool):
    """Delete a transcript or entire topic.

    TARGET is either a video ID or topic name (with --all).

    Examples:

    \b
        filmot library delete VIDEO_ID              # Delete from all topics
        filmot library delete VIDEO_ID -t TOPIC    # Delete from specific topic
        filmot library delete TOPIC --all          # Delete entire topic
    """
    from ..library import get_library
    from ..ledger import log_result
    lib = get_library()

    if delete_all:
        try:
            deleted = lib.delete_topic(target)
        except Exception as error:
            data: LibraryResultData = {
                "topic": target,
                "rows": [],
                "summary": {
                    "target": target,
                    "scope": "topic",
                    "deleted": 0,
                },
            }
            outcome = CommandResult.failed(
                "library-delete",
                data,
                ErrorDetail.from_exception(
                    error,
                    stage="delete-topic",
                    details={"target": target},
                ),
            )
            log_result(
                "library_delete",
                outcome,
                topic=target,
                data={
                    "target": target,
                    "scope": "topic",
                    "deleted": 0,
                },
            )
            _render_library_delete(outcome)
            raise click.exceptions.Exit(1)
        result_topic = target
        scope = "topic"
    else:
        try:
            deleted = lib.delete(target, topic=topic)
        except Exception as error:
            data = {
                "topic": topic,
                "rows": [],
                "summary": {
                    "target": target,
                    "scope": "transcript",
                    "deleted": False,
                },
            }
            outcome = CommandResult.failed(
                "library-delete",
                data,
                ErrorDetail.from_exception(
                    error,
                    stage="delete-transcript",
                    details={"target": target, "topic": topic},
                ),
            )
            log_result(
                "library_delete",
                outcome,
                topic=topic,
                data={
                    "target": target,
                    "scope": "transcript",
                    "deleted": False,
                },
            )
            _render_library_delete(outcome)
            raise click.exceptions.Exit(1)
        result_topic = topic
        scope = "transcript"

    data = {
        "topic": result_topic,
        "rows": [],
        "summary": {
            "target": target,
            "scope": scope,
            "deleted": deleted,
        },
    }
    outcome = CommandResult(
        command="library-delete",
        status=(
            ResultStatus.COMPLETED
            if bool(deleted)
            else ResultStatus.EMPTY
        ),
        data=data,
    )
    log_result(
        "library_delete",
        outcome,
        topic=result_topic,
        data={
            "target": target,
            "scope": scope,
            "deleted": deleted,
        },
    )
    _render_library_delete(outcome)


def _render_library_migrate_topic(
    outcome: CommandResult[LibraryResultData],
) -> None:
    """Render the final state of an explicit legacy-topic migration."""
    _render_library_failure(outcome, "Topic migration failed")
    data = outcome.data
    summary = data.get("summary") or {}
    requested_topic = str(summary.get("requested_topic") or "")
    canonical_slug = str(
        summary.get("canonical_slug") or data.get("topic") or ""
    )
    legacy_slug = str(summary.get("legacy_slug") or "")
    reason = summary.get("reason")

    if reason == "already-canonical":
        console.print(
            f"[dim]No migration is needed: '{requested_topic}' already uses "
            f"'{canonical_slug}'.[/dim]"
        )
        return
    if reason == "no-source-files":
        console.print(
            f"[dim]No legacy transcript files found at "
            f"{summary.get('source')}.[/dim]"
        )
        return

    migrated = int(summary.get("migrated", 0))
    remaining = int(summary.get("remaining", 0))
    console.print(
        f"[green]Migrated {migrated} transcript file(s) to "
        f"'{canonical_slug}'.[/green]"
    )
    if remaining:
        console.print(
            f"[yellow]{remaining} file(s) remain in '{legacy_slug}', usually "
            "because the destination already exists or a source file is invalid."
            "[/yellow]"
        )


@library.command("migrate-topic")
@click.argument("topic")
@click.option(
    "--yes",
    is_flag=True,
    help="Confirm that the entire ambiguous legacy directory belongs to TOPIC",
)
def library_migrate_topic(topic: str, yes: bool):
    """Explicitly assign a pre-Unicode topic directory to TOPIC.

    Old Filmot versions discarded Unicode, so unrelated names could share a
    directory such as ``uncategorized`` or ``ai``. This command moves the
    entire derived legacy directory; it cannot infer or partition ownership.
    Review both paths and confirm only when every source file belongs to TOPIC.
    """
    from ..ledger import log_result
    from ..library import _legacy_normalize_topic, get_library

    try:
        lib = get_library()
        canonical_slug = lib._normalize_topic(topic)
        legacy_slug = _legacy_normalize_topic(topic)
    except Exception as error:
        data: LibraryResultData = {
            "topic": topic,
            "rows": [],
            "summary": {
                "requested_topic": topic,
                "canonical_slug": topic,
                "legacy_slug": "",
                "source_files": 0,
                "migrated": 0,
                "remaining": 0,
            },
        }
        outcome = CommandResult.failed(
            "library-migrate-topic",
            data,
            ErrorDetail.from_exception(
                error,
                stage="prepare-migration",
                details={"topic": topic},
            ),
        )
        log_result(
            "library_migrate_topic",
            outcome,
            topic=topic,
            data={
                "requested_topic": topic,
                "canonical_slug": topic,
                "legacy_slug": "",
                "source_files": 0,
                "migrated": 0,
                "remaining": 0,
            },
        )
        _render_library_migrate_topic(outcome)
        raise click.exceptions.Exit(1)

    if canonical_slug == legacy_slug:
        data = {
            "topic": canonical_slug,
            "rows": [],
            "summary": {
                "requested_topic": topic,
                "canonical_slug": canonical_slug,
                "legacy_slug": legacy_slug,
                "source_files": 0,
                "migrated": 0,
                "remaining": 0,
                "reason": "already-canonical",
            },
        }
        outcome = CommandResult(
            command="library-migrate-topic",
            status=ResultStatus.SKIPPED,
            data=data,
        )
        log_result(
            "library_migrate_topic",
            outcome,
            topic=canonical_slug,
            data=dict(data["summary"]),
        )
        _render_library_migrate_topic(outcome)
        raise click.exceptions.Exit(1)

    source = lib.transcripts_dir / legacy_slug
    destination = lib.transcripts_dir / canonical_slug
    source_files = sorted(source.glob("*.json")) if source.exists() else []
    if not source_files:
        data = {
            "topic": canonical_slug,
            "rows": [],
            "summary": {
                "requested_topic": topic,
                "canonical_slug": canonical_slug,
                "legacy_slug": legacy_slug,
                "source": str(source),
                "source_files": 0,
                "migrated": 0,
                "remaining": 0,
                "reason": "no-source-files",
            },
        }
        outcome = CommandResult(
            command="library-migrate-topic",
            status=ResultStatus.EMPTY,
            data=data,
        )
        log_result(
            "library_migrate_topic",
            outcome,
            topic=canonical_slug,
            data={
                key: value
                for key, value in data["summary"].items()
                if key != "source"
            },
        )
        _render_library_migrate_topic(outcome)
        return

    console.print(
        Panel(
            f"[bold yellow]Legacy source:[/bold yellow] {source}\n"
            f"[bold green]Destination:[/bold green] {destination}\n"
            f"[bold]Transcript files:[/bold] {len(source_files)}\n\n"
            "Old slugs can represent more than one original topic. This "
            "operation assigns the entire source directory to the destination; "
            "existing destination files are never overwritten.",
            title="Explicit legacy-topic migration",
            border_style="yellow",
        )
    )
    console.print(
        "[yellow]Warning: this assigns the entire source directory.[/yellow]"
    )
    if not yes:
        click.confirm(
            f"Assign all {len(source_files)} legacy files to '{canonical_slug}'?",
            abort=True,
        )

    try:
        migrated = lib.migrate_legacy_topic(topic)
    except Exception as error:
        data = {
            "topic": canonical_slug,
            "rows": [],
            "summary": {
                "requested_topic": topic,
                "legacy_slug": legacy_slug,
                "canonical_slug": canonical_slug,
                "source_files": len(source_files),
                "migrated": 0,
                "remaining": len(source_files),
            },
        }
        outcome = CommandResult.failed(
            "library-migrate-topic",
            data,
            ErrorDetail.from_exception(
                error,
                stage="move-files",
                details={
                    "source_files": len(source_files),
                    "legacy_slug": legacy_slug,
                    "canonical_slug": canonical_slug,
                },
            ),
        )
        log_result(
            "library_migrate_topic",
            outcome,
            topic=canonical_slug,
            data=dict(data["summary"]),
        )
        _render_library_migrate_topic(outcome)
        return

    remaining = (
        len(list(source.glob("*.json")))
        if source.exists()
        else 0
    )
    data = {
        "topic": canonical_slug,
        "rows": [],
        "summary": {
            "requested_topic": topic,
            "legacy_slug": legacy_slug,
            "canonical_slug": canonical_slug,
            "source_files": len(source_files),
            "migrated": migrated,
            "remaining": remaining,
        },
    }
    errors = (
        [
            ErrorDetail(
                type="MigrationIncomplete",
                message=(
                    f"{remaining} legacy transcript file(s) remain "
                    f"in '{legacy_slug}'."
                ),
                stage="move-files",
                details={
                    "remaining": remaining,
                    "migrated": migrated,
                },
            )
        ]
        if remaining
        else []
    )
    outcome = CommandResult(
        command="library-migrate-topic",
        status=(
            ResultStatus.PARTIAL
            if remaining
            else ResultStatus.COMPLETED
        ),
        data=data,
        errors=errors,
    )
    log_result(
        "library_migrate_topic",
        outcome,
        topic=canonical_slug,
        data=dict(data["summary"]),
    )
    _render_library_migrate_topic(outcome)
    if remaining:
        raise click.exceptions.Exit(2)


def _render_library_compare(
    outcome: CommandResult[LibraryResultData],
) -> None:
    """Render a concordance from its typed, precomputed source rows."""
    _render_library_failure(outcome, "Library comparison failed")
    data = outcome.data
    query = str(data.get("query") or "")
    rows = data.get("rows") or []
    summary = data.get("summary") or {}

    if not rows:
        console.print(
            f"[yellow]No sources mention '{query}'[/yellow]"
        )
        return

    if summary.get("fallback_used"):
        console.print(
            "[dim]No exact word matches. Showing substring matches "
            "(plurals/inflections):[/dim]"
        )
    console.print(
        Panel(
            f"[bold]'{query}' mentioned {summary.get('matches', 0)} times "
            f"across {len(rows)} sources[/bold]",
            title="Cross-Source Concordance",
        )
    )

    for row in rows:
        match_count = int(row.get("match_count", 0))
        mentions_label = "mention" if match_count == 1 else "mentions"
        density = row.get("density")
        density_str = (
            f" | [bold]{float(density):.1f}/min[/bold]"
            if density is not None
            else ""
        )
        console.print(
            f"\n[bold cyan]{row.get('title', 'Unknown')}[/bold cyan]"
        )
        console.print(
            f"  [dim]Channel:[/dim] {row.get('channel', 'Unknown')} | "
            f"[dim]Topic:[/dim] {row.get('topic', '')} | "
            f"[bold]{match_count} {mentions_label}{density_str}[/bold]"
        )
        console.print(
            f"  [dim]Video:[/dim] "
            f"https://youtube.com/watch?v={row.get('video_id', '')}"
        )
        details = row.get("excerpt_details") or [
            {"excerpt": match} for match in row.get("excerpts", [])[:3]
        ]
        for match in details[:3]:
            highlighted = str(match.get("excerpt", ""))
            for variant in (
                query,
                query.lower(),
                query.upper(),
                query.capitalize(),
            ):
                highlighted = highlighted.replace(
                    variant,
                    f"[bold yellow]{variant}[/bold yellow]",
                )
            locator = ""
            if match.get("timestamp") and match.get("url"):
                locator = "[[link={url}]{timestamp}[/link]] ".format(
                    url=match["url"],
                    timestamp=match["timestamp"],
                )
            console.print(f"  [dim]{locator}{highlighted}[/dim]")

    console.print(
        f"\n[dim]Sources sorted by {summary.get('sort_label')} "
        "(highest lexical occurrence first)[/dim]"
    )


@library.command("compare")
@click.argument("query")
@click.option("--topic", "-t", default=None, help="Limit to specific topic")
@click.option("--context", "-c", "context_chars", default=300, type=click.IntRange(0), help="Characters of context around matches (default: 300)")
@click.option("--sort", "sort_by", default="mentions", type=click.Choice(["mentions", "density"]), help="Sort by mention count (default) or density (mentions/min)")
@click.option("--raw", is_flag=True, help="Output one versioned JSON result")
def library_compare(
    query: str,
    topic: str,
    context_chars: int,
    sort_by: str,
    raw: bool,
):
    """Build a lexical concordance across sources for a term or phrase.

    This counts text matches and shows excerpts. It does not determine source
    independence, stance, agreement, contradiction, credibility, or truth.
    Timestamped saved segments produce deep-linked excerpt locators in --raw
    output. The command is read-only and never appends a session event.

    Examples:

    \b
        filmot library compare "dark oxygen" --topic deep-sea-mining
        filmot library compare "moratorium" --context 200
    """
    used_substring = False
    stage = "open-library"
    try:
        from ..library import get_library
        lib = get_library()
        stage = "search"
        status = (
            nullcontext()
            if raw
            else console.status(f"Comparing '{query}' across sources...")
        )
        with status:
            results = lib.search(query, topic=topic)

        # Auto-fallback catches plurals/inflections.
        if not results:
            results = lib.search(query, topic=topic, substring=True)
            if results:
                used_substring = True

        stage = "build-concordance"
        import re as _re

        pattern = (
            _re.compile(_re.escape(query), flags=_re.IGNORECASE)
            if used_substring
            else _re.compile(
                r"(?<!\w)"
                + _re.escape(query)
                + r"(?!\w)",
                flags=_re.IGNORECASE,
            )
        )
        rows = []
        for result in results:
            transcript_data = lib.get(
                result["video_id"],
                result["topic"],
            )
            duration = (
                transcript_data.get("metadata", {}).get(
                    "duration_seconds",
                    0,
                )
                if transcript_data
                else 0
            )
            density = (
                int(result["match_count"]) / (duration / 60)
                if duration and duration > 0
                else None
            )
            excerpt_details = (
                lib._find_match_details(
                    transcript_data.get("transcript", ""),
                    query,
                    context_chars=context_chars,
                    pattern=pattern,
                    min_gap=context_chars,
                    segments=transcript_data.get("segments") or [],
                    video_id=str(result["video_id"]),
                )
                if transcript_data
                else []
            )
            rows.append(
                {
                    "video_id": result["video_id"],
                    "topic": result["topic"],
                    "title": result.get("title", "Unknown"),
                    "channel": result.get("channel", "Unknown"),
                    "match_count": int(result["match_count"]),
                    "density": density,
                    "excerpts": [
                        item["excerpt"] for item in excerpt_details[:3]
                    ],
                    "excerpt_details": excerpt_details[:3],
                }
            )
        if sort_by == "density":
            rows.sort(
                key=lambda row: float(row.get("density") or 0),
                reverse=True,
            )
        stage = "build-result"
        total_mentions = sum(int(row["match_count"]) for row in rows)
        sort_label = (
            "density (mentions/min)"
            if sort_by == "density"
            else "mention count"
        )
        data = {
            "topic": topic,
            "query": query,
            "rows": rows,
            "summary": {
                "sort": sort_by,
                "sort_label": sort_label,
                "context": context_chars,
                "match_mode": (
                    "substring"
                    if used_substring
                    else "word_boundary"
                ),
                "fallback_used": used_substring,
                "sources": len(rows),
                "matches": total_mentions,
            },
        }
        outcome = CommandResult(
            command="library-compare",
            status=(
                ResultStatus.COMPLETED
                if rows
                else ResultStatus.EMPTY
            ),
            data=data,
            warnings=(
                ["Used substring fallback after word-boundary search was empty."]
                if used_substring
                else []
            ),
        )
    except Exception as error:
        data: LibraryResultData = {
            "topic": topic,
            "query": query,
            "rows": [],
            "summary": {
                "sort": sort_by,
                "sort_label": (
                    "density (mentions/min)"
                    if sort_by == "density"
                    else "mention count"
                ),
                "context": context_chars,
                "match_mode": (
                    "substring"
                    if used_substring
                    else "word_boundary"
                ),
                "fallback_used": used_substring,
                "sources": 0,
                "matches": 0,
            },
        }
        outcome = CommandResult.failed(
            "library-compare",
            data,
            ErrorDetail.from_exception(
                error,
                stage=stage,
                details={
                    "query": query,
                    "topic": topic,
                    "sort": sort_by,
                },
            ),
        )
        if raw:
            _emit_library_raw(outcome)
        else:
            _render_library_compare(outcome)
        raise click.exceptions.Exit(1)

    if raw:
        _emit_library_raw(outcome)
    else:
        _render_library_compare(outcome)


def _render_library_echoes(
    outcome: CommandResult[EchoAnalysisResultData],
) -> None:
    data = outcome.data
    summary = data.get("summary") or {}
    console.print(
        Panel(
            "Compared {sources} full transcripts across {pairs} pairs; "
            "{matched} pairs met the threshold and formed {clusters} clusters.".format(
                sources=summary.get("sources", 0),
                pairs=summary.get("pairs", 0),
                matched=summary.get("matched_pairs", 0),
                clusters=summary.get("clusters", 0),
            ),
            title="Echo Analysis: {}".format(data.get("topic", "")),
        )
    )
    matched_rows = sorted(
        (row for row in data.get("rows") or [] if row.get("matched")),
        key=lambda row: (
            -float(row.get("score") or 0),
            -int(row.get("shared_shingles") or 0),
            str(row.get("source_a") or ""),
            str(row.get("source_b") or ""),
        ),
    )
    if matched_rows:
        table = Table(title="Strongest shared-phrasing lineage candidates")
        table.add_column("Source A", style="cyan")
        table.add_column("Source B", style="cyan")
        table.add_column("Jaccard", justify="right")
        table.add_column("Shared n-grams", justify="right")
        for row in matched_rows[:ECHO_HUMAN_PAIR_LIMIT]:
            table.add_row(
                str(row.get("source_a", "")),
                str(row.get("source_b", "")),
                "{:.3f}".format(float(row.get("score") or 0)),
                str(row.get("shared_shingles", 0)),
            )
        console.print(table)
        if len(matched_rows) > ECHO_HUMAN_PAIR_LIMIT:
            console.print(
                "[dim]Showing {} of {} matched pairs; use --raw or --persist "
                "for the complete pair set.[/dim]".format(
                    ECHO_HUMAN_PAIR_LIMIT,
                    len(matched_rows),
                )
            )
    else:
        console.print("[dim]No source pair met the configured threshold.[/dim]")
    method = data.get("method") or {}
    console.print(
        "[dim]Advisory only: shared phrasing can suggest reuse or common "
        "lineage; it does not establish copying, independence, credibility, "
        "falsity, or truth. Method: {name}/v{version}, {ngram}-grams, "
        "threshold {threshold}.[/dim]".format(
            name=method.get("name", ""),
            version=method.get("version", ""),
            ngram=method.get("ngram", ""),
            threshold=method.get("threshold", ""),
        )
    )
    if data.get("artifact"):
        console.print("[green]Saved reproducible artifact: {}[/green]".format(data["artifact"]))


@library.command("echoes")
@click.argument("topic")
@click.option("--ngram", default=5, type=click.IntRange(1), show_default=True, help="Word n-gram size")
@click.option(
    "--threshold",
    default=0.5,
    type=click.FloatRange(min=0.0, max=1.0, min_open=True),
    show_default=True,
    help="Minimum Jaccard score for a pair to form a cluster",
)
@click.option("--persist", is_flag=True, help="Write a content-addressed analysis artifact")
@click.option("--raw", is_flag=True, help="Output one versioned JSON result")
def library_echoes(
    topic: str,
    ngram: int,
    threshold: float,
    persist: bool,
    raw: bool,
):
    """Compare full saved transcripts for advisory shared-phrasing clusters.

    The default command is read-only and never logs. --persist writes a
    content-addressed JSON artifact under .filmot_data/analysis/TOPIC/.
    Clusters indicate possible reuse or shared lineage, not proof of copying,
    dependence, credibility, falsity, or truth.
    """
    from ..analysis import analyze_echoes, persist_echo_analysis
    from ..library import get_library

    sources = []
    stage = "open-library"
    try:
        lib = get_library()
        stage = "read-corpus"
        for item in lib.read_topic_records(topic):
            metadata = item.get("metadata") or {}
            sources.append({
                "source_id": str(item["video_id"]),
                "title": str(metadata.get("title") or ""),
                "channel": str(metadata.get("channel") or ""),
                "url": str((item.get("source") or {}).get("url") or ""),
                "language": str(metadata.get("language") or ""),
                "transcript_source": str(metadata.get("source") or ""),
                "saved_at": str(item.get("saved_at") or ""),
                "text": str(item.get("transcript") or ""),
            })
        stage = "analyze-transcripts"
        analysis = analyze_echoes(
            sources,
            ngram=ngram,
            threshold=threshold,
            topic=topic,
        )
        artifact = None
        if persist:
            stage = "persist-analysis"
            artifact = persist_echo_analysis(topic, analysis)
        stage = "build-result"
        data: EchoAnalysisResultData = {
            "analysis_schema": ECHO_ANALYSIS_SCHEMA,
            "topic": topic,
            "rows": list(analysis["pairs"]),
            "clusters": list(analysis["clusters"]),
            "summary": dict(analysis["summary"]),
            "method": dict(analysis["method"]),
            "artifact_hash": str(analysis["artifact_hash"]),
        }
        if artifact is not None:
            data["artifact"] = str(artifact)
        outcome = CommandResult(
            command="library-echoes",
            status=(
                ResultStatus.COMPLETED
                if analysis["summary"]["pairs"]
                else ResultStatus.EMPTY
            ),
            data=data,
        )
    except Exception as error:
        _command_error(
            str(error),
            command="library-echoes",
            raw=raw,
            error_type=type(error).__name__,
            stage=stage,
        )

    if raw:
        _emit_raw_result(outcome)
    else:
        _render_library_echoes(outcome)



def _scout_reproduction_argv(row: dict) -> Optional[list]:
    """Return the exact direct-search argv when its request was recorded."""
    query = row.get("query")
    order = row.get("order")
    try:
        days = int(row.get("days"))
        max_results = int(row.get("max_results"))
    except (TypeError, ValueError):
        return None
    if not query or not order or days < 1 or max_results < 1:
        return None
    argv = [
        "filmot",
        "yt-search",
        str(query),
        "--days",
        str(days),
        "--max-results",
        str(max_results),
        "--order",
        str(order),
    ]
    request_channel_id = row.get("request_channel_id")
    if request_channel_id:
        argv.extend(["--channel-id", str(request_channel_id)])
    argv.extend(["--show-description", "--raw"])
    return argv


def _render_sessions(
    outcome: CommandResult[SessionResultData],
) -> None:
    """Render a session inventory or replay from one typed result."""
    if outcome.status_value == ResultStatus.FAILED.value:
        message = (
            outcome.errors[0].message
            if outcome.errors
            else "session ledger could not be read"
        )
        _command_error("Session read failed: {}".format(message))
    for error in outcome.errors:
        console.print(
            "[yellow]Incomplete session ledger:[/yellow] {}".format(
                error.message
            )
        )
    data = outcome.data
    name = data.get("name")
    if not name:
        rows = data.get("rows") or []
        if not rows:
            console.print(
                "[dim]No sessions logged yet. Run a search or research "
                "command first.[/dim]"
            )
            return
        table = Table(title="Research Sessions")
        table.add_column("Session", style="cyan")
        table.add_column("Events", justify="right")
        table.add_column("Last activity", style="dim")
        for row in rows:
            table.add_row(
                str(row["name"]),
                str(row["events"]),
                str(row["last_ts"]).replace("T", " "),
            )
        console.print(table)
        console.print(
            "\n[dim]Replay one with: filmot sessions <name>[/dim]"
        )
        return

    summary = data.get("summary")
    if isinstance(summary, dict):
        searches = summary.get("searches") or {}
        youtube_searches = summary.get("youtube_searches") or {}
        research_searches = summary.get("research_searches") or {}
        transcripts = summary.get("transcripts") or {}
        research = summary.get("research") or {}
        provenance = summary.get("research_provenance") or {}
        saved_provenance = provenance.get("saved_sources") or {}
        origin_counts = saved_provenance.get("origins") or {}
        origin_summary = ", ".join(
            "{} {}".format(stage, count)
            for stage, count in sorted(origin_counts.items())
        ) or "none recorded"
        claims = summary.get("claims") or {}
        console.print(
            Panel(
                "Events: {events}\n"
                "Standalone searches: {searches} ({queries} unique queries)\n"
                "Direct YouTube searches: {youtube_searches} "
                "({youtube_queries} unique queries)\n"
                "Research search stages: {research_stages}\n"
                "Saved transcripts: {saved} unique\n"
                "Transcript failures: {attempts} attempts / {failed} unique videos\n"
                "Research runs: {runs}\n"
                "  Selected downloads: {reported} saved / {research_skipped} skipped / "
                "{research_failed} failed / {research_deduped} deduped\n"
                "  Probes: {probe_saved} saved / {probe_failed} download failures / "
                "{probe_query_failed} query failures\n"
                "Saved-source origins: {source_origins}\n"
                "Claim mutations: {claim_events} ({claim_ids} claim IDs)".format(
                    events=summary.get("event_count", 0),
                    searches=searches.get("events", 0),
                    queries=len(searches.get("unique_queries") or []),
                    youtube_searches=youtube_searches.get("events", 0),
                    youtube_queries=len(
                        youtube_searches.get("unique_queries") or []
                    ),
                    research_stages=research_searches.get("stages", 0),
                    saved=transcripts.get("saved_unique", 0),
                    attempts=transcripts.get("failed_attempts", 0),
                    failed=transcripts.get("failed_unique", 0),
                    runs=research.get("runs", 0),
                    reported=research.get("saved_reported", 0),
                    research_skipped=research.get("skipped_reported", 0),
                    research_failed=research.get("failed_reported", 0),
                    research_deduped=research.get("deduped_reported", 0),
                    probe_saved=research.get("probe_saved_reported", 0),
                    probe_failed=research.get("probe_download_failed_reported", 0),
                    probe_query_failed=research.get("probe_query_failed_reported", 0),
                    source_origins=origin_summary,
                    claim_events=claims.get("mutation_events", 0),
                    claim_ids=len(claims.get("unique_claim_ids") or []),
                ),
                title="Session Summary: {}".format(name),
            )
        )
        scope_rows = searches.get("scope_rows") or []
        if scope_rows:
            table = Table(title="Standalone search universes")
            table.add_column("#", justify="right")
            table.add_column("Query")
            table.add_column("API total", justify="right")
            table.add_column("Fetched", justify="right")
            table.add_column("Post-filter", justify="right")
            table.add_column("Status")
            for index, row in enumerate(scope_rows, 1):
                table.add_row(
                    str(index),
                    Text(str(row.get("query", ""))),
                    str(row.get("api_total", 0)),
                    str(row.get("candidates_fetched", 0)),
                    str(row.get("post_filter_count", 0)),
                    str(row.get("status", "")),
                )
            console.print(table)
            for index, row in enumerate(scope_rows, 1):
                filters = row.get("effective_filters")
                details = []
                effective_query = row.get("effective_query")
                if effective_query is not None and effective_query != row.get("query"):
                    details.append("effective_query={}".format(
                        json_mod.dumps(effective_query, ensure_ascii=False)
                    ))
                if isinstance(filters, dict):
                    details.extend(
                        "{}={}".format(
                            key, json_mod.dumps(value, ensure_ascii=False)
                        )
                        for key, value in filters.items()
                        if value is not None and value != "" and value != []
                    )
                    if not details:
                        details.append("Recorded filters are unset")
                else:
                    details.append("Filters not recorded")
                console.print(Text("{}. Scope: {}".format(index, "; ".join(details))))
                truncated = row.get("effective_filters_truncated") or []
                if row.get("effective_query_truncated"):
                    truncated = [*truncated, "effective_query"]
                if truncated:
                    console.print(Text(
                        "   Truncated fields: {}. Replay the session for full values.".format(
                            ", ".join(truncated)
                        ),
                        style="dim",
                    ))
        youtube_scope_rows = youtube_searches.get("scope_rows") or []
        if youtube_scope_rows:
            table = Table(title="Direct YouTube search universes")
            table.add_column("#", justify="right")
            table.add_column("Query", max_width=38)
            table.add_column("UTC publication window", max_width=39)
            table.add_column("Pages", justify="right")
            table.add_column("Fetched", justify="right")
            table.add_column("Returned", justify="right")
            table.add_column("Enrichment")
            table.add_column("Status")
            for index, row in enumerate(youtube_scope_rows, 1):
                after = row.get("published_after") or "-"
                before = row.get("published_before") or "-"
                table.add_row(
                    str(index),
                    Text(str(row.get("query", ""))),
                    Text("{} .. {}".format(after, before)),
                    str(row.get("pages_fetched", 0)),
                    str(row.get("candidates_fetched", 0)),
                    str(row.get("returned", 0)),
                    str(row.get("enrichment_status") or "not recorded"),
                    str(row.get("status", "")),
                )
            console.print(table)
            for index, row in enumerate(youtube_scope_rows, 1):
                details = [
                    "order={}".format(row.get("order") or "not recorded"),
                    "cap={}".format(
                        row.get("max_results")
                        if row.get("max_results") is not None
                        else "not recorded"
                    ),
                    "stop={}".format(
                        row.get("stopping_reason") or "not recorded"
                    ),
                    "continuation={}".format(
                        "available"
                        if row.get("continuation_available")
                        else "none"
                    ),
                ]
                filters = row.get("effective_filters")
                if isinstance(filters, dict):
                    details.extend(
                        "{}={}".format(
                            key, json_mod.dumps(value, ensure_ascii=False)
                        )
                        for key, value in filters.items()
                        if value is not None and value != ""
                    )
                console.print(Text(
                    "{}. Scope: {}".format(index, "; ".join(details))
                ))
            omitted = int(youtube_searches.get("omitted", 0) or 0)
            if omitted:
                console.print(
                    "[dim]{} older direct YouTube search row(s) omitted from "
                    "this bounded summary.[/dim]".format(omitted)
                )
        research_scope_rows = research_searches.get("scope_rows") or []
        if research_scope_rows:
            table = Table(title="Research search universes (kept by stage)")
            table.add_column("Stage")
            table.add_column("Query")
            table.add_column("API total", justify="right")
            table.add_column("Fetched", justify="right")
            table.add_column("After gates", justify="right")
            table.add_column("Status")
            for row in research_scope_rows:
                table.add_row(
                    str(row.get("stage", "")),
                    str(row.get("query", "")),
                    str(row.get("api_total", 0)),
                    str(row.get("candidates_fetched", 0)),
                    str(row.get("post_filter_count", 0)),
                    str(row.get("status", "")),
                )
            console.print(table)
        scout_rows = (provenance.get("scout_runs") or {}).get("rows") or []
        if scout_rows:
            table = Table(title="Scout provenance")
            table.add_column("Run", style="dim", max_width=14)
            table.add_column("Query", max_width=42)
            table.add_column("Request", max_width=24)
            table.add_column("Found", justify="right")
            table.add_column("After gate", justify="right")
            table.add_column("Status")
            for row in scout_rows:
                table.add_row(
                    str(row.get("run_id", "")),
                    str(row.get("query", "")),
                    "{}d / {} / n={}".format(
                        row.get("days")
                        if row.get("days") is not None else "-",
                        row.get("order") or "-",
                        row.get("max_results")
                        if row.get("max_results") is not None else "-",
                    ),
                    str(row.get("candidates_found", 0)),
                    (
                        str(row.get("gate_after"))
                        if row.get("gate_after") is not None
                        else "-"
                    ),
                    str(row.get("status", "")),
                )
            console.print(table)
            for row in scout_rows:
                argv = _scout_reproduction_argv(row)
                if argv:
                    # Click writes one physical line. Rich would insert hard
                    # wraps, making the displayed reproduction command harder
                    # to copy back into a shell.
                    click.echo(
                        "Inspect scout candidates: {}".format(shlex.join(argv))
                    )
            omitted = int(
                (provenance.get("scout_runs") or {}).get("omitted", 0)
            )
            if omitted:
                console.print(
                    "[dim]{} older scout run(s) omitted.[/dim]".format(omitted)
                )
        probe_rows = (provenance.get("probe_queries") or {}).get("rows") or []
        probe_run_rows = (
            (provenance.get("probe_runs") or {}).get("rows") or []
        )
        if probe_run_rows:
            table = Table(title="Probe run outcomes")
            table.add_column("Run", style="dim", max_width=14)
            table.add_column("Status")
            table.add_column("Reason", max_width=28)
            table.add_column("Seeds", justify="right")
            table.add_column("Terms", justify="right")
            table.add_column("Queries e/p/d", justify="right")
            table.add_column("Failures", justify="right")
            table.add_column("Saved", justify="right")

            def probe_metric(row, field):
                value = row.get(field)
                return "-" if value is None else str(value)

            for row in probe_run_rows:
                table.add_row(
                    str(row.get("run_id", "")),
                    str(row.get("status", "")),
                    str(row.get("reason") or "-"),
                    probe_metric(row, "eligible_seeds"),
                    probe_metric(row, "terms"),
                    "{}/{}/{}".format(
                        probe_metric(row, "queries"),
                        probe_metric(row, "planned"),
                        probe_metric(row, "deferred"),
                    ),
                    probe_metric(row, "failures"),
                    probe_metric(row, "saved"),
                )
            console.print(table)
            for row in probe_run_rows:
                if row.get("reason"):
                    # Keep the machine-readable terminal state copyable on
                    # one physical line even when Rich narrows the table.
                    click.echo(
                        "Probe outcome: {} status={} reason={}".format(
                            row.get("run_id", ""),
                            row.get("status", ""),
                            row["reason"],
                        )
                    )
            omitted = int(
                (provenance.get("probe_runs") or {}).get("omitted", 0)
            )
            if omitted:
                console.print(
                    "[dim]{} older probe run(s) omitted.[/dim]".format(
                        omitted
                    )
                )
        if probe_rows:
            table = Table(title="Probe query provenance")
            table.add_column("Probe", style="dim", max_width=18)
            table.add_column("Query", max_width=48)
            table.add_column("Basis", justify="right")
            table.add_column("API", justify="right")
            table.add_column("Scoped", justify="right")
            table.add_column("Status")
            for row in probe_rows:
                probe_label = str(row.get("run_id", ""))
                if row.get("index") is not None:
                    probe_label += " #{}".format(row["index"])
                basis = "{}/{}".format(
                    row.get("co_windows")
                    if row.get("co_windows") is not None else "-",
                    row.get("source_support")
                    if row.get("source_support") is not None else "-",
                )
                table.add_row(
                    probe_label,
                    str(row.get("query", "")),
                    basis,
                    str(row.get("api_total", 0)),
                    str(row.get("scoped", 0)),
                    str(row.get("status", "")),
                )
            console.print(table)
            console.print("[dim]Probe basis is co-windows/source transcripts.[/dim]")
            omitted = int(
                (provenance.get("probe_queries") or {}).get("omitted", 0)
            )
            if omitted:
                console.print(
                    "[dim]{} older probe query/queries omitted.[/dim]".format(
                        omitted
                    )
                )
        source_rows = saved_provenance.get("rows") or []
        if source_rows:
            table = Table(title="Saved source provenance")
            table.add_column("Video ID", style="cyan", max_width=16)
            table.add_column("Title", max_width=42)
            table.add_column("Origin", max_width=16)
            table.add_column("Discovery query", max_width=48)
            for row in source_rows:
                origin = str(row.get("origin_stage") or "unknown")
                if origin == "probe" and row.get("probe_index") is not None:
                    origin += " #{}".format(row["probe_index"])
                origin_query = row.get("origin_query")
                if not origin_query:
                    if row.get("provenance_status") == "query_not_recorded":
                        origin_query = (
                            "query not recorded (manual save)"
                            if origin == "manual"
                            else "query not recorded (legacy event)"
                        )
                    else:
                        origin_query = "not recorded"
                table.add_row(
                    str(row.get("video_id", "")),
                    str(row.get("title") or "Unknown"),
                    origin,
                    str(origin_query),
                )
            console.print(table)
            omitted = int(saved_provenance.get("omitted", 0))
            if omitted:
                console.print(
                    "[dim]{} older saved source(s) omitted; origin totals above "
                    "still cover all rows.[/dim]".format(omitted)
                )
        console.print(
            "[dim]Standalone and research-stage counts are separate event totals; "
            "saved and failed video counts are unique IDs.[/dim]"
        )
        return

    events = data.get("events") or []
    if not events:
        console.print(f"[yellow]No session found for '{name}'.[/yellow]")
        return

    console.print(f"[bold]Session: {name}[/bold] ({len(events)} events)\n")
    for event in events:
        ts = str(event.get("ts", "")).replace("T", " ")
        kind = event.get("kind", "?")
        event_data = (
            event.get("data")
            if isinstance(event.get("data"), dict)
            else {}
        )
        if kind == "search":
            detail = (
                f"\"{event_data.get('query','')}\" → "
                f"{event_data.get('results',0)}/"
                f"{event_data.get('total','?')} results"
            )
            if event_data.get("start_date") or event_data.get("end_date"):
                detail += (
                    f" [{event_data.get('start_date','')}.."
                    f"{event_data.get('end_date','')}]"
                )
        elif kind == "research":
            detail = (
                f"\"{event_data.get('query','')}\" → saved "
                f"{event_data.get('saved',0)}, probe "
                f"{event_data.get('probe',0)} "
                f"(scout {event_data.get('scout',0)})"
            )
        elif kind == "channel-search":
            detail = (
                f"{event_data.get('slug','')}: "
                f"\"{event_data.get('query','')}\" → "
                f"{event_data.get('videos',0)} vids / "
                f"{event_data.get('hits',0)} hits"
            )
        elif kind == "transcript":
            detail = str(event_data.get("video_id", "")) + (
                f" grep \"{event_data.get('grep')}\""
                if event_data.get("grep")
                else ""
            )
        else:
            detail = json_mod.dumps(event_data, ensure_ascii=False)
        console.print(
            f"  [dim]{ts}[/dim] [cyan]{kind}[/cyan] "
            f"[dim]{event.get('status', '')}[/dim]  {detail}"
        )


@click.command("sessions")
@click.argument("name", required=False, default=None)
@click.option(
    "--summary",
    "show_summary",
    is_flag=True,
    help="Fold one session into separate standalone/research search universes",
)
@click.option("--raw", is_flag=True, help="Output one versioned JSON result")
def sessions(name: str, show_summary: bool, raw: bool):
    """Show the research session ledger so you can resume prior investigations.

    Every search, research run, and channel-search is logged to
    .filmot_data/sessions/. Without NAME, lists all sessions. With a NAME
    (topic slug or YYYY-MM-DD date), replays that session's events. Use
    --summary to keep standalone and staged-research candidate universes, final
    research totals, unique saved transcripts, and failed attempts separate.

    \b
    Examples:
        filmot sessions                       # list all sessions
        filmot sessions fable-5-mythos        # replay a topic session
        filmot sessions fable-5-mythos --summary
        filmot sessions 2026-06-10            # replay a day's ad-hoc queries
        filmot sessions 2026-06-10 --raw      # versioned JSON for piping
    """
    from ..ledger import list_sessions, read_events, summarize_events

    read_diagnostics = []
    read_failure = None
    stage = "list-sessions" if not name else "read-session"

    try:
        if not name:
            if show_summary:
                raise click.UsageError("--summary requires a session NAME")
            rows = list_sessions(diagnostics=read_diagnostics)
            result_data: SessionResultData = {"rows": rows}
        else:
            events = read_events(name, diagnostics=read_diagnostics)
            if show_summary:
                stage = "summarize-session"
                summary = summarize_events(name, events)
                if read_diagnostics:
                    summary["read_diagnostics"] = len(read_diagnostics)
                result_data = {
                    "name": name,
                    "summary": summary,
                }
            else:
                result_data = {
                    "name": name,
                    "events": events,
                }
    except click.UsageError:
        raise
    except Exception as error:
        read_failure = ErrorDetail.from_exception(error, stage=stage)
        result_data = (
            {
                "name": name,
                "summary": {
                    "name": name,
                    "event_count": 0,
                    "read_diagnostics": len(read_diagnostics) + 1,
                },
            }
            if name and show_summary
            else {"name": name, "events": []}
            if name
            else {"rows": []}
        )
    errors = [
        ErrorDetail(
            type=str(item.get("type") or "LedgerReadError"),
            message=str(item.get("message") or "Unreadable ledger record"),
            stage="read-session",
            details={
                key: value
                for key, value in item.items()
                if key not in {"type", "message"}
            },
        )
        for item in read_diagnostics
    ]
    if read_failure is not None:
        errors.append(read_failure)
    has_content = bool(
        result_data.get("rows")
        or result_data.get("events")
        or (
            isinstance(result_data.get("summary"), dict)
            and result_data["summary"].get("event_count")
        )
    )
    outcome = CommandResult(
        command="sessions",
        status=(
            ResultStatus.FAILED
            if read_failure is not None
            else ResultStatus.PARTIAL
            if read_diagnostics and has_content
            else ResultStatus.FAILED
            if read_diagnostics
            else ResultStatus.COMPLETED
            if has_content
            else ResultStatus.EMPTY
        ),
        data=result_data,
        errors=errors,
    )
    if raw:
        _emit_raw_result(outcome)
        if outcome.status_value == ResultStatus.FAILED.value:
            raise click.exceptions.Exit(1)
        return
    _render_sessions(outcome)


# ========== DOWNLOAD (STDIN) ==========
