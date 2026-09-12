"""Filmot CLI - Command Line Interface."""

import click
import errno
import sys
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from ._version import __version__
from .api import FilmotClient
from .cli_support import (
    command_error as _command_error,
    console,
    silence_broken_pipe_streams as _silence_broken_pipe_streams,
)
from .schemas import CommandResult, ErrorDetail, ResultStatus
from .session_context import session_option


@click.group()
@click.version_option(version=__version__, prog_name="filmot")
@session_option
def cli():
    """Filmot CLI - Search YouTube transcripts and metadata."""
    pass


# ========== SEARCH SUBTITLES ==========

@cli.command()
def config():
    """Show resolved configuration and storage paths without writing state."""
    from .config import API_KEY, API_HOST, BASE_URL
    from .paths import (
        config_file,
        project_data_dir,
        proxy_health_db,
        user_cache_dir,
        user_config_dir,
        user_state_dir,
    )
    from .youtube_search import validate_youtube_api

    # Configuration inventory should never disclose credential fragments.
    key_status = "configured" if API_KEY else "not configured"
    try:
        validate_youtube_api()
        youtube_key_status = "configured"
    except ValueError:
        youtube_key_status = "not configured"
    rows = (
        ("API Host", API_HOST),
        ("Filmot API Key", key_status),
        ("YouTube API Key", youtube_key_status),
        ("Base URL", BASE_URL),
        ("Project Data", str(project_data_dir())),
        ("User Config", str(user_config_dir())),
        ("Config File", str(config_file())),
        ("User State", str(user_state_dir())),
        ("Proxy Health", str(proxy_health_db())),
        ("User Cache", str(user_cache_dir())),
    )
    console.print("[bold]Filmot CLI Configuration[/bold]")
    for label, value in rows:
        # ``soft_wrap`` preserves long resolved paths instead of replacing
        # their distinguishing suffix with a table ellipsis.
        console.print(
            f"[cyan]{label}:[/cyan] [green]{value}[/green]",
            soft_wrap=True,
        )


# ========== INTERACTIVE MODE ==========

@cli.command()
def interactive():
    """Start interactive REPL mode."""
    from .interactive import start_repl
    from .ledger import log_event

    log_event("interactive", status="started")
    start_repl()


# ========== CACHE MANAGEMENT ==========

def _render_cache_result(outcome: CommandResult) -> None:
    data = outcome.data
    action = data["action"]
    if action == "clear":
        console.print(
            f"[green]✓ Cleared {data['removed']} cache entries[/green]"
        )
        return
    if action == "clear_expired":
        console.print(
            f"[green]✓ Cleared {data['removed']} expired entries[/green]"
        )
        return

    stats = data["stats"]
    table = Table(title="Cache Statistics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Total Entries", str(stats["total_entries"]))
    table.add_row("Valid Entries", str(stats["valid_entries"]))
    table.add_row("Expired Entries", str(stats["expired_entries"]))
    table.add_row("Size", f"{stats['size_mb']} MB")
    table.add_row("TTL", f"{stats['ttl_seconds']} seconds")
    table.add_row("Directory", stats["cache_dir"])
    console.print(table)


@cli.command()
@click.option("--clear", is_flag=True, help="Clear all cache entries")
@click.option("--clear-expired", is_flag=True, help="Clear only expired entries")
def cache(clear: bool, clear_expired: bool):
    """Manage the response cache."""
    from .cache import get_cache

    cache_instance = get_cache()

    if clear:
        count = cache_instance.clear()
        data = {"action": "clear", "removed": count}
    elif clear_expired:
        count = cache_instance.clear_expired()
        data = {"action": "clear_expired", "removed": count}
    else:
        stats = cache_instance.stats()
        data = {"action": "status", "stats": stats}
    outcome = CommandResult.completed("cache", data)
    _render_cache_result(outcome)


# ========== EXPORT ==========

def _render_batch_result(outcome: CommandResult) -> None:
    data = outcome.data
    stats = data["stats"]
    console.print("\n[bold]Results:[/bold]")
    console.print(f"  Successful: [green]{stats['successful']}[/green]")
    console.print(f"  Failed: [red]{stats['failed']}[/red]")
    console.print(f"  Total results: {stats['total_results']}")
    console.print(f"  Avg time: {stats['avg_duration_ms']:.0f}ms")
    if data.get("artifact"):
        console.print(
            f"\n[green]✓ Results exported to: {data['artifact']}[/green]"
        )


@cli.command()
@click.argument("file")
@click.option("--output", "-o", default=None, help="Output file for results")
@click.option("--format", "-f", "fmt", type=click.Choice(["json", "csv"]), default="json", help="Output format")
def batch(file: str, output: str, fmt: str):
    """Process multiple queries from a file.

    Supports .txt (one query per line), .json (array of queries), and .csv files.

    Examples:

        filmot batch queries.txt -o results.json

        filmot batch queries.json -o results.csv --format csv
    """
    from .batch import BatchProcessor
    from .ledger import log_result
    from pathlib import Path

    if not Path(file).exists():
        message = (
            f"File not found: {file}. Use `filmot batch-template` to create "
            "a sample."
        )
        outcome = CommandResult.failed(
            "batch",
            {"file": file, "output": output, "format": fmt},
            ErrorDetail("FileNotFoundError", message, stage="load"),
        )
        log_result("batch", outcome, data=outcome.data)
        _command_error(
            message
        )

    client = FilmotClient()
    processor = BatchProcessor(client)

    try:
        queries = processor.load_queries_from_file(file)
    except Exception as e:
        outcome = CommandResult.failed(
            "batch",
            {"file": file, "output": output, "format": fmt},
            ErrorDetail.from_exception(e, stage="load"),
        )
        log_result("batch", outcome, data=outcome.data)
        _command_error(f"Failed to load queries: {e}")

    console.print(f"[bold]Loaded {len(queries)} queries from {file}[/bold]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        task = progress.add_task("Processing...", total=len(queries))

        def on_progress(current, total, result):
            status = "✓" if result.success else "✗"
            progress.update(task, advance=1, description=f"[{current}/{total}] {status} {result.query.query[:30]}...")

        try:
            processor.process_queries(queries, on_progress)
        except Exception as error:
            outcome = CommandResult.failed(
                "batch",
                {
                    "file": file,
                    "output": output,
                    "format": fmt,
                    "queries": len(queries),
                },
                ErrorDetail.from_exception(error, stage="process"),
            )
            log_result("batch", outcome, data=outcome.data)
            _command_error(f"Batch processing failed: {error}")

    stats = processor.stats()
    artifact = None
    if output:
        try:
            artifact = str(processor.export_results(output, fmt))
        except Exception as error:
            data = {
                "file": file,
                "output": output,
                "format": fmt,
                "queries": len(queries),
                "stats": stats,
            }
            outcome = CommandResult.failed(
                "batch",
                data,
                ErrorDetail.from_exception(error, stage="export"),
            )
            log_result("batch", outcome, data=data)
            _render_batch_result(outcome)
            _command_error(f"Export failed: {error}")

    total_failure = bool(
        queries
        and stats["successful"] == 0
        and stats["failed"]
    )
    data = {
        "file": file,
        "output": output,
        "format": fmt,
        "queries": len(queries),
        "stats": stats,
        "artifact": artifact,
    }
    errors = (
        [
            ErrorDetail(
                "BatchQueryFailures",
                f"{stats['failed']} batch query or queries failed",
                stage="process",
                details={
                    "failed": stats["failed"],
                    "successful": stats["successful"],
                },
            )
        ]
        if stats["failed"]
        else []
    )
    outcome = CommandResult(
        command="batch",
        status=(
            ResultStatus.FAILED
            if total_failure
            else ResultStatus.PARTIAL
            if stats["failed"]
            else ResultStatus.COMPLETED
        ),
        data=data,
        errors=errors,
    )
    log_result("batch", outcome, data=data)
    _render_batch_result(outcome)
    if total_failure:
        _command_error(f"All {stats['failed']} batch queries failed.")


@cli.command("batch-template")
@click.option("--format", "-f", "fmt", type=click.Choice(["json", "csv", "txt"]), default="json")
@click.option("--output", "-o", default=None, help="Output file path")
def batch_template(fmt: str, output: str):
    """Create a sample batch query file.

    Examples:

        filmot batch-template --format json -o queries.json

        filmot batch-template --format csv -o queries.csv
    """
    from .batch import create_batch_file_template

    if not output:
        output = f"queries_template.{fmt}"

    path = create_batch_file_template(output, fmt)
    data = {"output": str(path), "format": fmt}
    outcome = CommandResult.completed("batch-template", data)
    from .ledger import log_result
    log_result(
        "batch_template",
        outcome,
        data=data,
    )
    console.print(f"[green]✓ Created template: {outcome.data['output']}[/green]")


# ========== WATCHLIST ==========

@cli.group()
def watchlist():
    """Manage your video watchlist."""
    pass


def _render_watchlist_list(outcome: CommandResult) -> None:
    data = outcome.data
    items = data["items"]
    if not items:
        console.print("[yellow]Watchlist is empty.[/yellow]")
        return

    table = Table(title="📺 Watchlist")
    table.add_column("#", width=3)
    table.add_column("Title", max_width=45)
    table.add_column("Channel", max_width=20)
    table.add_column("✓", width=3)
    table.add_column("Added", width=12)
    table.add_column("Video ID", style="dim")
    for index, item in enumerate(items, 1):
        table.add_row(
            str(index),
            item.get("title", "")[:45],
            item.get("channel_name", "")[:20],
            "✓" if item.get("watched") else "",
            item.get("added_at", "")[:10],
            item.get("video_id", ""),
        )
    console.print(table)
    stats = data["stats"]
    console.print(
        f"\n[dim]Total: {stats['total_videos']} | "
        f"Watched: {stats['watched']} | "
        f"Unwatched: {stats['unwatched']}[/dim]"
    )


@watchlist.command("list")
@click.option("--unwatched", is_flag=True, help="Show only unwatched videos")
@click.option("--tag", default=None, help="Filter by tag")
def watchlist_list(unwatched: bool, tag: str):
    """Show all watchlist items."""
    from .watchlist import get_watchlist
    from .ledger import log_result

    wl = get_watchlist()
    watched_filter = False if unwatched else None
    items = wl.get_watchlist(tag=tag, watched=watched_filter)
    stats = wl.stats()
    data = {
        "items": items,
        "stats": stats,
        "unwatched": unwatched,
        "tag": tag,
    }
    outcome = CommandResult(
        command="watchlist-list",
        status=(
            ResultStatus.COMPLETED
            if items
            else ResultStatus.EMPTY
        ),
        data=data,
    )
    log_result(
        "watchlist_list",
        outcome,
        data={
            "unwatched": unwatched,
            "tag": tag,
            "results": len(items),
            "stats": stats,
        },
    )
    _render_watchlist_list(outcome)


@watchlist.command("add")
@click.argument("video_id")
@click.option("--notes", "-n", default="", help="Notes about the video")
def watchlist_add(video_id: str, notes: str):
    """Add a video to watchlist by ID."""
    from .watchlist import get_watchlist
    from .ledger import log_result

    # Fetch video info first
    client = FilmotClient()
    with console.status(f"[bold green]Fetching video info..."):
        result = client.get_videos(video_id)

    if "error" in result or not result:
        detail = (
            result.get("error")
            if isinstance(result, dict)
            else "empty video response"
        )
        message = f"Could not fetch video {video_id}: {detail}"
        outcome = CommandResult.failed(
            "watchlist-add",
            {
                "video_id": video_id,
                "has_notes": bool(notes),
            },
            ErrorDetail(
                type="VideoLookupError",
                message=message,
                stage="metadata",
            ),
        )
        log_result(
            "watchlist_add",
            outcome,
            data=outcome.data,
        )
        _command_error(message)

    video = result[0] if isinstance(result, list) else result
    video["id"] = video_id  # Ensure ID is set

    wl = get_watchlist()
    added = wl.add_video(video, notes)
    data = {
        "video_id": video_id,
        "title": video.get("title", video_id),
        "added": added,
        "has_notes": bool(notes),
    }
    outcome = CommandResult(
        command="watchlist-add",
        status=(
            ResultStatus.COMPLETED
            if added
            else ResultStatus.SKIPPED
        ),
        data=data,
    )
    log_result(
        "watchlist_add",
        outcome,
        data={
            "video_id": video_id,
            "added": added,
            "has_notes": bool(notes),
        },
    )
    if outcome.status_value == ResultStatus.COMPLETED.value:
        console.print(f"[green]✓ Added: {outcome.data['title']}[/green]")
    else:
        console.print("[yellow]Video already in watchlist.[/yellow]")


@watchlist.command("remove")
@click.argument("video_id")
def watchlist_remove(video_id: str):
    """Remove a video from watchlist."""
    from .watchlist import get_watchlist
    from .ledger import log_result

    wl = get_watchlist()
    removed = wl.remove_video(video_id)
    data = {"video_id": video_id, "removed": removed}
    outcome = CommandResult(
        command="watchlist-remove",
        status=(
            ResultStatus.COMPLETED
            if removed
            else ResultStatus.EMPTY
        ),
        data=data,
    )
    log_result(
        "watchlist_remove",
        outcome,
        data=data,
    )
    if outcome.status_value == ResultStatus.COMPLETED.value:
        console.print(f"[green]✓ Removed video: {video_id}[/green]")
    else:
        console.print(f"[yellow]Video not found in watchlist.[/yellow]")


@watchlist.command("watched")
@click.argument("video_id")
def watchlist_watched(video_id: str):
    """Mark a video as watched."""
    from .watchlist import get_watchlist
    from .ledger import log_result

    wl = get_watchlist()
    updated = wl.mark_watched(video_id, True)
    data = {"video_id": video_id, "updated": updated}
    outcome = CommandResult(
        command="watchlist-watched",
        status=(
            ResultStatus.COMPLETED
            if updated
            else ResultStatus.EMPTY
        ),
        data=data,
    )
    log_result(
        "watchlist_watched",
        outcome,
        data=data,
    )
    if outcome.status_value == ResultStatus.COMPLETED.value:
        console.print(f"[green]✓ Marked as watched[/green]")
    else:
        console.print(f"[yellow]Video not found in watchlist.[/yellow]")


@watchlist.command("clear")
@click.confirmation_option(prompt="Are you sure you want to clear the watchlist?")
def watchlist_clear():
    """Clear all watchlist items."""
    from .watchlist import get_watchlist
    from .ledger import log_result

    wl = get_watchlist()
    count = wl.clear_watchlist()
    data = {"removed": count}
    outcome = CommandResult.completed("watchlist-clear", data)
    log_result("watchlist_clear", outcome, data=data)
    console.print(
        f"[green]✓ Cleared {outcome.data['removed']} items from "
        "watchlist[/green]"
    )


# ========== PAGINATED SEARCH ==========

# Domain commands are standalone Click objects.  Registration lives here so
# core/domain modules never import the root group and circular imports stay
# impossible.
from .commands.claims import claims
from .commands.library import library, sessions, yt_data
from .commands.proxy import proxy
from .commands.research import (
    _candidate_assessment,
    _extract_probe_terms,
    _find_probe_pairs,
    _research_query_ladder,
    research,
)
from .commands.search import (
    _all_substring_positions,
    _backfill_metadata,
    _detect_echo_clusters,
    _deep_link,
    _display_subtitle_results,
    _freshness_hint,
    _hit_start,
    channels,
    export,
    search,
    search_all,
    video,
    yt_search,
    yt_video,
)
from .commands.transcript import (
    channel_download,
    channel_search,
    channel_status,
    download,
    transcript,
    transcript_search,
)
from .commands.youtube import yt_playlist, yt_playlists
from .commands.youtube_comments import yt_comments, yt_replies

for command in (
    search,
    video,
    channels,
    export,
    search_all,
    transcript,
    transcript_search,
    yt_search,
    yt_video,
    yt_playlist,
    yt_playlists,
    yt_comments,
    yt_replies,
    yt_data,
    claims,
    library,
    research,
    channel_download,
    channel_status,
    channel_search,
    sessions,
    download,
    proxy,
):
    cli.add_command(command)


def main():
    """Run Click with Unix-friendly broken-pipe handling, including flush."""
    try:
        try:
            result = cli(standalone_mode=False)
            exit_code = result if isinstance(result, int) else 0
        except click.ClickException as error:
            error.show()
            exit_code = error.exit_code
        except click.Abort:
            click.echo("Aborted!", err=True)
            exit_code = 1

        # Buffered output may not observe a closed downstream reader until
        # interpreter shutdown, which CPython reports as exit 120. Flush while
        # still inside the EPIPE boundary so normal `... | head` pipelines
        # finish successfully.
        sys.stdout.flush()
        sys.stderr.flush()
        return exit_code
    except SystemExit as error:
        # Click catches an EPIPE inside Command.main even when
        # ``standalone_mode=False``, swaps in its pacifying stream wrapper,
        # then raises SystemExit(1). Recognize that exact path without
        # converting unrelated SystemExit failures into success.
        pacify_type = click.utils.PacifyFlushWrapper
        if error.code == 1 and (
            isinstance(sys.stdout, pacify_type)
            or isinstance(sys.stderr, pacify_type)
        ):
            _silence_broken_pipe_streams()
            return 0
        raise
    except OSError as error:
        if error.errno not in (errno.EPIPE, errno.EINVAL):
            raise
        _silence_broken_pipe_streams()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
