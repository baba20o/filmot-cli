"""Transcript retrieval, pipeline download, and channel-corpus commands."""

import json as json_mod
import os
from pathlib import Path
from typing import Optional

import click
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from ..api import FilmotClient
from ..cli_support import (
    command_error as _command_error,
    console,
    diagnostic as _diagnostic,
    redact_diagnostic as _redact_diagnostic,
    status_context as _search_status,
    stderr_console,
    whole_word_summary as _whole_word_summary,
)
from ..schemas import CommandResult, ErrorDetail, ResultStatus
from .search import (
    _backfill_metadata,
    _bulk_download_transcripts,
    _grep_transcript,
)

def _transcript_failure_detail(result: dict, *, verbose: bool) -> str:
    """Render a redacted transcript failure with optional route diagnostics."""
    error_type = str(result.get("error_type") or "TranscriptError")
    message = _whole_word_summary(result.get("error") or "unknown error", 500)
    base = f"{error_type}: {message}"
    if not verbose:
        return _whole_word_summary(base)

    details = []
    if result.get("route"):
        details.append(f"route={result['route']}")
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
                "error": _whole_word_summary(item.get("error", ""), 500),
            }
            for item in result["route_errors"]
            if isinstance(item, dict)
        ]
        details.append(
            "route_errors="
            + json_mod.dumps(
                _redact_diagnostic(route_errors),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    return " | ".join([base, *details])


def _transcript_route_progress(event: dict, *, context: Optional[str] = None) -> None:
    """Render one structured transcript-route event to stderr.

    Route labels are already redacted by ``filmot.transcript``. Deliberately
    avoid printing raw transport exception text here because Requests errors
    can contain credential-bearing proxy URLs.
    """
    event_name = event.get("event", "")
    route = str(event.get("route") or "route")
    prefix = f"{context}: " if context else ""
    attempt = event.get("attempt")
    elapsed = float(event.get("elapsed_s") or 0)

    if event_name == "pool_prepare":
        click.echo(
            f"{prefix}preparing {route} "
            f"({event.get('available_sessions', 0)} available; "
            f"up to {event.get('max_attempts', 0)} attempts)",
            err=True,
        )
    elif event_name == "pool_prepare_failed":
        detail = event.get("error_type") or "pool refresh failed"
        click.echo(
            f"{prefix}could not prepare {route} ({detail})",
            err=True,
        )
    elif event_name == "route_start":
        click.echo(
            f"{prefix}route {attempt}: {route} "
            f"(deadline {float(event.get('timeout_s') or 0):g}s)",
            err=True,
        )
    elif event_name == "route_success":
        click.echo(
            f"{prefix}route {attempt} succeeded: {route} ({elapsed:.2f}s)",
            err=True,
        )
    elif event_name == "route_timeout":
        click.echo(
            f"{prefix}route {attempt} timed out: {route} ({elapsed:.2f}s)",
            err=True,
        )
    elif event_name == "route_failed":
        detail = event.get("kind") or event.get("error_type") or "transport error"
        click.echo(
            f"{prefix}route {attempt} failed: "
            f"{route} ({detail}, {elapsed:.2f}s)",
            err=True,
        )
    elif event_name == "route_terminal":
        detail = event.get("error_type") or "transcript unavailable"
        click.echo(
            f"{prefix}route {attempt} reached YouTube: "
            f"{route} ({detail}, {elapsed:.2f}s)",
            err=True,
        )
    elif event_name == "routes_exhausted":
        click.echo(
            f"{prefix}all transcript routes exhausted "
            f"({event.get('attempts', 0)} attempted)",
            err=True,
        )


def _route_progress_for(context: Optional[str] = None):
    """Create a callback carrying a video/title label."""
    return lambda event: _transcript_route_progress(event, context=context)


def _render_transcript(
    outcome: CommandResult[dict],
    *,
    full: bool,
    timestamps: bool,
    chunk: Optional[float],
    format_timestamp,
) -> None:
    """Render a successful transcript from its shared typed outcome."""
    data = outcome.data
    if full:
        console.print(Panel(
            f"[bold]Video ID:[/bold] {data['video_id']}\n"
            f"[bold]Language:[/bold] {data['language']} "
            f"{'(auto-generated)' if data.get('is_generated') else '(manual)'}\n"
            f"[bold]Duration:[/bold] "
            f"{format_timestamp(data.get('duration_seconds', 0))}\n"
            f"[bold]Segments:[/bold] {data.get('segment_count', 0)}",
            title="Transcript Info",
        ))
        if chunk and "chunks" in data:
            for item in data["chunks"]:
                console.print(
                    f"\n[bold cyan][{item['start_formatted']}][/bold cyan]"
                )
                console.print(item["text"])
        else:
            console.print(f"\n{data['full_text']}")
        return

    if timestamps and "segments" in data:
        console.print(Panel(
            f"[bold]Video ID:[/bold] {data['video_id']}\n"
            f"[bold]Language:[/bold] {data['language']} "
            f"{'(auto-generated)' if data.get('is_generated') else '(manual)'}\n"
            f"[bold]Duration:[/bold] "
            f"{format_timestamp(data.get('duration_seconds', 0))}\n"
            f"[bold]Segments:[/bold] {data.get('segment_count', 0)}",
            title="Transcript",
        ))
        for segment in data["segments"]:
            timestamp = format_timestamp(segment["start"])
            console.print(f"[dim][{timestamp}][/dim] {segment['text']}")
        return

    if chunk and "chunks" in data:
        console.print(Panel(
            f"[bold]Video ID:[/bold] {data['video_id']}\n"
            f"[bold]Language:[/bold] {data['language']}\n"
            f"[bold]Chunks:[/bold] {len(data['chunks'])} × "
            f"{data['chunk_minutes']} min",
            title="Chunked Transcript",
        ))
        for item in data["chunks"]:
            console.print(
                f"\n[bold yellow]━━━ {item['start_formatted']} ━━━[/bold yellow]"
            )
            text = item["text"]
            console.print(text[:500] + "..." if len(text) > 500 else text)
        return

    console.print(Panel(
        f"[bold]Video ID:[/bold] {data['video_id']}\n"
        f"[bold]Language:[/bold] {data['language']} "
        f"{'(auto-generated)' if data.get('is_generated') else '(manual)'}\n"
        f"[bold]Duration:[/bold] "
        f"{format_timestamp(data.get('duration_seconds', 0))}\n"
        f"[bold]Characters:[/bold] {len(data.get('full_text', ''))}",
        title="Transcript Summary",
    ))
    text = data.get("full_text", "")
    if len(text) > 1000:
        console.print(f"\n{text[:1000]}...\n")
        console.print("[dim]Use --full to see complete transcript[/dim]")
    else:
        console.print(f"\n{text}")


def _render_transcript_search(outcome: CommandResult[dict]) -> None:
    """Render transcript-search matches from the logged result payload."""
    data = outcome.data
    console.print(Panel(
        f"[bold]Video ID:[/bold] {data['video_id']}\n"
        f"[bold]Query:[/bold] {data['query']}\n"
        f"[bold]Matches:[/bold] {data['match_count']}",
        title="Transcript Search",
    ))
    if data["match_count"] == 0:
        console.print("[yellow]No matches found in transcript.[/yellow]")
        return

    query = data["query"]
    for index, match in enumerate(data["matches"], 1):
        console.print(
            f"\n[bold cyan]Match {index} @ {match['timestamp']}[/bold cyan]"
        )
        highlighted = match["context"].replace(
            query, f"[bold red]{query}[/bold red]"
        ).replace(
            query.lower(), f"[bold red]{query.lower()}[/bold red]"
        ).replace(
            query.upper(), f"[bold red]{query.upper()}[/bold red]"
        ).replace(
            query.capitalize(), f"[bold red]{query.capitalize()}[/bold red]"
        )
        console.print(f"  {highlighted}")


def _render_channel_download(outcome: CommandResult[dict]) -> None:
    """Render the final channel-download summary from its aggregate outcome."""
    data = outcome.data
    if data.get("already_synced"):
        console.print(
            "[green]✓ Channel is fully synced! Nothing new to download.[/green]"
        )
        console.print(
            f"[dim]Total transcripts: {data.get('already_done', 0)}[/dim]"
        )
        return

    channel = data.get("channel") or {}
    console.print()
    console.print(Panel(
        f"[bold green]Download Complete[/bold green]\n\n"
        f"Channel: [bold]{channel.get('name', 'Unknown')}[/bold]\n"
        f"This session: [green]{data.get('downloaded', 0)}[/green] "
        f"downloaded, [red]{data.get('failed', 0)}[/red] failed\n"
        f"Total corpus: [bold]{data.get('corpus_done', 0)}/"
        f"{data.get('enumerated', 0)}[/bold] videos\n"
        f"Words downloaded: [bold]{int(data.get('words', 0)):,}[/bold]\n"
        f"Storage: {data.get('storage', '')}",
        title="Summary",
        border_style="green",
    ))


def _render_channel_status(outcome: CommandResult[dict]) -> None:
    """Render channel inventory or one corpus from its typed outcome."""
    data = outcome.data
    scope = data["scope"]
    if scope == "all":
        channels = data.get("channels") or []
        if not channels:
            console.print("[dim]No channels downloaded yet.[/dim]")
            console.print(
                "[dim]Use: filmot channel-download <channel_id>[/dim]"
            )
            return

        table = Table(title="Downloaded Channels", border_style="blue")
        table.add_column("Channel", style="bold")
        table.add_column("Downloaded", justify="right")
        table.add_column("Failed", justify="right")
        table.add_column("Total", justify="right")
        table.add_column("Last Updated", style="dim")
        table.add_column("Slug", style="dim")
        for channel in channels:
            table.add_row(
                channel["name"],
                str(channel["downloaded"]),
                str(channel["failed"]),
                str(channel["total_videos"]),
                (
                    channel["last_updated"][:19]
                    if channel["last_updated"]
                    else ""
                ),
                channel["slug"],
            )
        console.print(table)
        return

    slug = data["slug"]
    stats = data.get("stats")
    if not stats:
        console.print(f"[red]Channel '{slug}' not found.[/red]")
        console.print(
            "[dim]Run 'filmot channel-status' to see available channels.[/dim]"
        )
        return

    channel = stats.get("channel", {})
    console.print(Panel(
        f"[bold]{channel.get('name', slug)}[/bold]\n"
        f"Channel ID: {channel.get('channel_id', 'N/A')}\n\n"
        f"Downloaded: [green]{stats['downloaded']}[/green] / "
        f"{stats['total_videos']}\n"
        f"Failed: [red]{stats['failed']}[/red]\n"
        f"Pending: [yellow]{stats['pending']}[/yellow]\n\n"
        f"Total words: [bold]{stats['total_words']:,}[/bold]\n"
        f"Total duration: [bold]{stats['total_duration_hours']}[/bold] hours\n\n"
        f"Last sync: "
        f"{stats['last_sync'][:19] if stats['last_sync'] else 'never'}\n"
        f"Storage: {stats['storage_path']}",
        title="Channel Corpus",
        border_style="cyan",
    ))


def _render_channel_search(outcome: CommandResult[dict]) -> None:
    """Render channel-corpus matches from the same result sent to the ledger."""
    import re

    data = outcome.data
    results = data.get("results") or []
    display_query = data["display_query"]
    slug = data["slug"]
    if not results:
        console.print(
            f"[dim]No matches for '{display_query}' in {slug}.[/dim]"
        )
        return

    summary = data["summary"]
    console.print(
        f"\n[bold]Found {summary['videos']} videos matching "
        f"'{display_query}' ({summary['hits']} total hits)[/bold]\n"
    )
    query_kind = data["query_kind"]
    terms = data["highlight_terms"]
    for result in results:
        console.print(f"[bold cyan]{result['title']}[/bold cyan]")
        published = (
            result["published_at"][:10]
            if result["published_at"]
            else "N/A"
        )
        console.print(
            f"  [dim]{result['video_id']} | {published} | "
            f"{result['match_count']} matches[/dim]"
        )
        for snippet in result["snippets"][:2]:
            highlighted = snippet
            if query_kind in {"near", "tilde"}:
                for term in sorted(terms, key=len, reverse=True):
                    highlighted = re.sub(
                        re.escape(term),
                        f"[bold yellow]{term}[/bold yellow]",
                        highlighted,
                        flags=re.IGNORECASE,
                    )
            else:
                query = data["query"]
                highlighted = snippet.replace(
                    query,
                    f"[bold yellow]{query}[/bold yellow]",
                )
            console.print(f"  [dim]→[/dim] {highlighted}")
        console.print()


class _DownloadProgressConsole:
    """Forward item progress while deferring final text to a typed renderer."""

    _FINAL_PREFIXES = (
        "[yellow]No videos to download.",
        "\n[bold]Complete:",
        "[yellow]Proxy connection failures detected",
        "[dim]View with:",
    )

    def __init__(self, target) -> None:
        self.target = target
        self.final_messages: list[str] = []

    def print(self, *objects, **kwargs) -> None:
        message = " ".join(str(item) for item in objects)
        if message.startswith(self._FINAL_PREFIXES):
            self.final_messages.append(message)
            return
        self.target.print(*objects, **kwargs)


def _render_download(outcome: CommandResult[dict]) -> None:
    """Render final pipeline-download messages captured in the outcome."""
    for message in outcome.data.get("human_messages") or []:
        console.print(message)


@click.command("transcript")
@click.argument("video_id", nargs=1)
@click.option("--lang", "-l", default=None, help="Preferred language code (e.g., en, es, de)")
@click.option("--timestamps", "-t", is_flag=True, help="Include timestamps for each segment")
@click.option("--chunk", "-c", default=None, type=click.FloatRange(min=0, min_open=True), help="Chunk transcript into N-minute segments")
@click.option("--raw", is_flag=True, help="Output raw JSON response")
@click.option("--output", "-o", default=None, help="Save transcript to file")
@click.option("--full", is_flag=True, help="Output complete transcript text (for AI processing)")
@click.option("--proxy", default=None, help="HTTP/HTTPS proxy URL (e.g., http://user:pass@host:port)")
@click.option("--no-proxy", is_flag=True, help="Disable proxy (ignore env vars, connect directly)")
@click.option("--save-to", default=None, help="Save transcript to library under TOPIC (e.g., --save-to prompt-injection)")
@click.option("--fallback/--no-fallback", default=False, help="Use AWS Transcribe if YouTube captions unavailable")
@click.option("--grep", default=None, help="Search within the transcript using proximity operators (NEAR/N, ~N) — prints timestamped matches")
@click.pass_context
def transcript(ctx, video_id: str, lang: str, timestamps: bool, chunk: float,
               raw: bool, output: str, full: bool, proxy: str, no_proxy: bool, save_to: str, fallback: bool, grep: str):
    """Download full YouTube transcript for deep analysis.

    This command fetches the complete transcript of a YouTube video,
    enabling AI agents to go beyond search snippets and truly understand
    video content.

    VIDEO_ID can be:

    \b
      - Just the ID: dQw4w9WgXcQ
      - Full URL: https://youtube.com/watch?v=dQw4w9WgXcQ
      - Short URL: https://youtu.be/dQw4w9WgXcQ
      - IDs starting with dash: -O1bjFPgRQM (just use it directly)

    If you get IP blocked, you can:

    \b
      1. Set proxy via --proxy flag
      2. Set HTTP_PROXY/HTTPS_PROXY environment variables
      3. Set WEBSHARE_PROXY_USERNAME and WEBSHARE_PROXY_PASSWORD in .env
         (for Webshare.io rotating residential proxies)

    To bypass proxy settings and connect directly, use --no-proxy.

    AWS Transcribe Fallback:

    \b
      Use --fallback to auto-transcribe when YouTube captions are unavailable.
      Requires: yt-dlp, boto3, AWS profile 'APIBoss' with Transcribe access.
      Flow: Download audio → Upload S3 → Transcribe → Cleanup

    Examples:

    \b
        filmot transcript dQw4w9WgXcQ

        filmot transcript "https://youtube.com/watch?v=VIDEO_ID" --full

        filmot transcript VIDEO_ID --timestamps --chunk 5

        filmot transcript VIDEO_ID -o transcript.txt

        filmot transcript VIDEO_ID --proxy http://user:pass@host:port

        filmot transcript VIDEO_ID --raw > data.json

        filmot transcript VIDEO_ID --fallback  # AWS fallback if no captions
    """
    from ..transcript import (
        configure_proxy,
        describe_routing_plan,
        disable_proxy,
        format_timestamp,
        get_transcript,
        get_transcript_with_fallback,
        get_transcript_with_timestamps,
        routing_plan,
    )

    if proxy and no_proxy:
        raise click.UsageError("--proxy and --no-proxy are mutually exclusive")
    if raw and grep:
        raise click.UsageError("--raw and --grep cannot be combined")
    if raw and output:
        raise click.UsageError("--raw and --output cannot be combined")
    if chunk is not None and fallback:
        raise click.UsageError("--chunk and --fallback cannot be combined")
    if grep and any((save_to, output, full, timestamps, chunk is not None, fallback)):
        raise click.UsageError(
            "--grep cannot be combined with --save-to, --output, --full, "
            "--timestamps, --chunk, or --fallback"
        )

    # Handle proxy configuration (status messages go to stderr to avoid polluting --raw)
    if no_proxy:
        disable_proxy()
    elif proxy:
        try:
            configure_proxy(http_proxy=proxy, exclusive=True)
        except Exception as e:
            _command_error(f"Proxy error: {e}", raw=raw)

    try:
        route_plan = routing_plan()
    except Exception as error:
        _command_error(f"Could not prepare transcript routes: {error}", raw=raw)
    if not raw:
        click.echo(
            f"Transcript routes ({route_plan['mode']}): "
            f"{describe_routing_plan(route_plan)}; "
            f"connect/read {route_plan['connect_timeout_s']:g}/"
            f"{route_plan['read_timeout_s']:g}s; "
            f"route deadline {route_plan['route_timeout_s']:g}s",
            err=True,
        )
    route_progress = None if raw else _route_progress_for(video_id)

    # Progress callback for AWS fallback
    def aws_progress(stage: str, msg: str):
        if not raw:
            click.echo(f"AWS:{stage} {msg}", err=True)

    try:
        with _search_status("[bold green]Fetching transcript...", raw=raw):
            languages = [lang] if lang else None

            if chunk:
                # Chunked mode doesn't support fallback (needs timestamps)
                result = get_transcript_with_timestamps(
                    video_id,
                    languages,
                    chunk_minutes=chunk,
                    progress_callback=route_progress,
                )
            elif fallback:
                # Use fallback-enabled fetch
                result = get_transcript_with_fallback(
                    video_id,
                    languages,
                    use_aws_fallback=True,
                    aws_progress_callback=aws_progress,
                    progress_callback=route_progress,
                )
            else:
                result = get_transcript(
                    video_id,
                    languages,
                    progress_callback=route_progress,
                )
    except Exception as error:
        from ..ledger import log_event
        detail = f"{type(error).__name__}: {error}"
        log_event(
            "transcript",
            topic=save_to,
            video_id=video_id,
            grep=grep,
            save_to=save_to,
            status="failed",
            error=detail,
        )
        _command_error(detail, raw=raw)

    if "error" in result:
        err_str = str(result.get('error', ''))
        err_low = err_str.lower()
        from ..ledger import log_event
        log_event(
            "transcript",
            topic=save_to,
            video_id=result.get("video_id", video_id),
            grep=grep,
            save_to=save_to,
            status="failed",
            error=_whole_word_summary(err_str, 500),
            error_type=result.get("error_type"),
            route=result.get("route"),
            routes_tried=result.get("routes_tried"),
            route_errors=result.get("route_errors"),
            routing_plan=route_plan,
        )

        if raw:
            _command_error(
                err_str,
                raw=True,
                payload=result,
            )

        console.print(f"[red]Error: {result['error']}[/red]")
        console.print(f"[dim]Video ID: {result.get('video_id', video_id)}[/dim]")
        if result.get("routes_tried"):
            console.print(f"[dim]Routes tried: {', '.join(result['routes_tried'])}[/dim]")

        # Classify the failure so we give the RIGHT advice, not a blanket --fallback tip
        is_proxy = any(s in err_low for s in ("proxy", "tunnel", "max retries", "connection"))
        is_ipblock = "ipblocked" in err_low or "blocked" in err_low
        is_captions = any(s in err_low for s in ("transcripts are disabled", "no transcript", "captions", "unavailable"))

        if is_proxy:
            console.print("\n[yellow]Tip: This is a transport/proxy failure, not a missing-caption issue.[/yellow]")
            console.print("  Your proxy may be down or out of credits. Connect directly with: [cyan]--no-proxy[/cyan]")
            console.print("  Or refresh the proxy pool / check WEBSHARE_API_TOKEN in .env")
        elif is_ipblock:
            console.print("\n[yellow]Tip: Your IP is blocked by YouTube. Route through a proxy:[/yellow]")
            console.print("  filmot transcript VIDEO_ID --proxy http://user:pass@host:port")
            console.print("  Or set WEBSHARE_PROXY_USERNAME/PASSWORD in .env for rotating proxies")
        elif is_captions and not fallback:
            console.print("\n[yellow]Tip: No captions found. Use --fallback to try AWS Transcribe.[/yellow]")
        elif not fallback:
            console.print("\n[dim]Tip: --fallback tries AWS Transcribe; --no-proxy bypasses the proxy.[/dim]")
        raise click.exceptions.Exit(1)

    # Show source if using fallback
    source = result.get('source', 'youtube')
    outcome = CommandResult.completed("transcript", result)
    transcript_data = outcome.data
    if source == 'aws_transcribe':
        _diagnostic(
            f"[cyan]Transcribed via AWS Transcribe "
            f"(language: {transcript_data.get('language', 'unknown')})[/cyan]",
            raw=raw,
        )
        if timestamps:
            _diagnostic(
                "[yellow]AWS fallback does not provide segment timestamps; "
                "showing transcript text instead.[/yellow]",
                raw=raw,
            )
            timestamps = False

    from ..ledger import log_event, log_result
    log_result(
        "transcript",
        outcome,
        topic=save_to,
        data={
            "video_id": transcript_data.get("video_id", video_id),
            "grep": grep,
            "save_to": save_to,
            "output": output,
            "raw": raw,
            "full": full,
            "timestamps": timestamps,
            "chunk": chunk,
            "fallback": fallback,
            "source": source,
            "language": transcript_data.get("language"),
            "chars": len(transcript_data.get("full_text", "")),
            "route": transcript_data.get("route"),
            "routes_tried": transcript_data.get("routes_tried"),
            "routing_plan": route_plan,
        },
    )

    # Grep mode: search within the transcript and print timestamped matches, then stop
    if grep:
        if not _grep_transcript(transcript_data, grep):
            raise click.exceptions.Exit(2)
        return

    # Save to library if --save-to specified
    if save_to:
        try:
            from ..library import get_library
            library = get_library()

            # Check if already cached
            if library.exists(transcript_data['video_id'], save_to):
                _diagnostic(
                    f"[yellow]Already in library: "
                    f"{save_to}/{transcript_data['video_id']}[/yellow]",
                    raw=raw,
                )
                log_event(
                    "transcript_save",
                    topic=save_to,
                    video_id=transcript_data["video_id"],
                    status="skipped",
                    reason="already_exists",
                )
            else:
                # Fetch video metadata so library entries have title/channel
                video_title = "Unknown"
                video_channel = "Unknown"
                try:
                    client = FilmotClient()
                    video_info = client.get_videos(transcript_data['video_id'])
                    if isinstance(video_info, list) and video_info:
                        video_title = video_info[0].get("title", "Unknown")
                        video_channel = video_info[0].get(
                            "channelname", "Unknown"
                        )
                    elif (
                        isinstance(video_info, dict)
                        and "error" not in video_info
                    ):
                        video_title = video_info.get("title", "Unknown")
                        video_channel = video_info.get(
                            "channelname", "Unknown"
                        )
                except Exception:
                    pass  # Metadata fetch is best-effort

                metadata = {
                    "title": video_title,
                    "channel": video_channel,
                    "language": transcript_data.get("language"),
                    "is_generated": transcript_data.get("is_generated"),
                    "duration_seconds": transcript_data.get("duration_seconds"),
                    "segment_count": transcript_data.get("segment_count"),
                    "route": transcript_data.get("route"),
                    "routes_tried": transcript_data.get("routes_tried"),
                }
                saved_path = library.save(
                    video_id=transcript_data['video_id'],
                    topic=save_to,
                    transcript_text=transcript_data.get('full_text', ''),
                    metadata=metadata,
                )
                log_event(
                    "transcript_save",
                    topic=save_to,
                    video_id=transcript_data["video_id"],
                    status="saved",
                    path=str(saved_path),
                    chars=len(transcript_data.get("full_text", "")),
                    source=source,
                    route=transcript_data.get("route"),
                    routes_tried=transcript_data.get("routes_tried"),
                )
                _diagnostic(
                    f"[green]✓ Saved to library: "
                    f"{save_to}/{transcript_data['video_id']}[/green]",
                    raw=raw,
                )
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            log_event(
                "transcript_save",
                topic=save_to,
                video_id=transcript_data["video_id"],
                status="failed",
                error=detail,
                route=transcript_data.get("route"),
            )
            _command_error(
                f"Could not save transcript to library: {detail}",
                raw=raw,
            )

    # Raw JSON output
    if raw:
        click.echo(
            json_mod.dumps(
                outcome.to_raw_dict(),
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    # Save to file
    if output:
        try:
            with open(output, 'w', encoding='utf-8') as f:
                if output.endswith('.json'):
                    json_mod.dump(transcript_data, f, indent=2)
                else:
                    # Plain text output
                    if timestamps and 'segments' in transcript_data:
                        for seg in transcript_data['segments']:
                            ts = format_timestamp(seg['start'])
                            f.write(f"[{ts}] {seg['text']}\n")
                    else:
                        f.write(transcript_data['full_text'])
            console.print(f"[green]✓ Saved transcript to: {output}[/green]")
            return
        except Exception as e:
            _command_error(f"Error saving file: {e}", raw=raw)

    _render_transcript(
        outcome,
        full=full,
        timestamps=timestamps,
        chunk=chunk,
        format_timestamp=format_timestamp,
    )


@click.command()
@click.argument("video_id")
@click.argument("query")
@click.option("--context", "-c", default=2, type=click.IntRange(0), help="Number of segments for context (default: 2)")
@click.option("--lang", "-l", default=None, help="Preferred language code")
def transcript_search(video_id: str, query: str, context: int, lang: str):
    """Search within a video's transcript.

    Finds all occurrences of a term within a video and shows context.
    Useful for navigating to specific parts of long videos.

    Examples:

    \b
        filmot transcript-search VIDEO_ID "fusion"

        filmot transcript-search VIDEO_ID "reactor" --context 3
    """
    from ..transcript import search_in_transcript

    with console.status(f"[bold green]Searching transcript for '{query}'..."):
        languages = [lang] if lang else None
        result = search_in_transcript(video_id, query, context, languages)

    from ..ledger import log_result
    if "error" in result:
        message = str(result["error"])
        outcome = CommandResult.failed(
            "transcript-search",
            result,
            ErrorDetail(
                type=str(result.get("error_type") or "TranscriptSearchError"),
                message=message,
                stage="transcript",
            ),
        )
        log_result(
            "transcript_search",
            outcome,
            data={
                "video_id": video_id,
                "query": query,
                "lang": lang,
                "context": context,
            },
        )
        _command_error(message)

    outcome = CommandResult(
        command="transcript-search",
        status=(
            ResultStatus.COMPLETED
            if result.get("match_count", 0)
            else ResultStatus.EMPTY
        ),
        data=result,
    )
    log_result(
        "transcript_search",
        outcome,
        data={
            "video_id": result.get("video_id", video_id),
            "query": result.get("query", query),
            "lang": lang,
            "context": context,
            "matches": result.get("match_count", 0),
        },
    )
    _render_transcript_search(outcome)


# ========== YOUTUBE API SEARCH ==========

@click.command("channel-download")
@click.argument("channel_id")
@click.option("--delay", "-d", default=1.0, type=click.FloatRange(min=0), help="Seconds between downloads (rate limiting, default: 1.0)")
@click.option("--lang", "-l", default="en", help="Preferred language code (default: en)")
@click.option("--limit", default=None, type=click.IntRange(1), help="Limit number of transcripts to download (for testing)")
@click.option("--workers", "-w", default=1, type=click.IntRange(1), help="Parallel download workers (default: 1, try 4 for speed)")
@click.option("--no-proxy", is_flag=True, help="Bypass proxy, connect directly with your IP")
@click.option("--fresh", is_flag=True, help="Ignore existing manifest, start fresh")
def channel_download(channel_id: str, delay: float, lang: str, limit: int, workers: int, no_proxy: bool, fresh: bool):
    """Download ALL transcripts from a YouTube channel.

    Enumerates every video on a channel via YouTube Data API, then downloads
    each transcript. Keeps a manifest for resume — if interrupted, re-run the
    same command to pick up where you left off (delta sync).

    Transcripts are stored in .filmot_data/channels/<channel-name>/

    \b
    Examples:
        filmot channel-download UCdnzT5Tl6pAkATOiDsPhqcg
        filmot channel-download UCdnzT5Tl6pAkATOiDsPhqcg --delay 2
        filmot channel-download UCdnzT5Tl6pAkATOiDsPhqcg --limit 5
        filmot channel-download UCdnzT5Tl6pAkATOiDsPhqcg --workers 4
    """
    from rich.progress import Progress, BarColumn, TaskProgressColumn, TimeRemainingColumn, MofNCompleteColumn
    from ..channel_dl import ChannelDownloader, get_channel_info
    from ..ledger import log_event, log_result
    import shutil

    downloader = ChannelDownloader()
    log_event(
        "channel_download_start",
        channel_id=channel_id,
        lang=lang,
        limit=limit,
        workers=workers,
        delay=delay,
        fresh=fresh,
        no_proxy=no_proxy,
    )

    # If --fresh, remove existing channel dir
    if fresh:
        try:
            info = get_channel_info(channel_id)
            _, channel_dir = downloader._resolve_channel_dir(info)
            if (channel_dir / "manifest.json").exists():
                stderr_console.print(f"[yellow]Removing existing data for {info['name']}...[/yellow]")
                shutil.rmtree(channel_dir)
        except Exception as error:
            detail = f"{type(error).__name__}: {_whole_word_summary(error, 500)}"
            log_event(
                "channel_download_end",
                channel_id=channel_id,
                status="failed",
                phase="fresh_reset",
                error=detail,
            )
            outcome = CommandResult.failed(
                "channel-download",
                {
                    "channel_id": channel_id,
                    "phase": "fresh_reset",
                },
                ErrorDetail.from_exception(error, stage="fresh_reset"),
            )
            log_result(
                "channel_download_end",
                outcome,
                data={
                    "channel_id": channel_id,
                    "phase": "fresh_reset",
                    "fresh": fresh,
                },
            )
            _command_error(f"Could not reset channel data for --fresh: {detail}")

    # Phase 1: Channel info
    with console.status("[bold blue]Fetching channel info..."):
        try:
            info = get_channel_info(channel_id)
        except Exception as e:
            log_event(
                "channel_download_end", channel_id=channel_id,
                status="failed", phase="channel_info",
                error=f"{type(e).__name__}: {e}",
            )
            outcome = CommandResult.failed(
                "channel-download",
                {
                    "channel_id": channel_id,
                    "phase": "channel_info",
                },
                ErrorDetail.from_exception(e, stage="channel_info"),
            )
            log_result(
                "channel_download_end",
                outcome,
                data={
                    "channel_id": channel_id,
                    "phase": "channel_info",
                },
            )
            _command_error(f"Error fetching channel: {e}")

    console.print(Panel(
        f"[bold]{info['name']}[/bold]\n"
        f"Channel ID: {channel_id}\n"
        f"Videos: {info['video_count']:,}\n"
        f"Subscribers: {info['subscriber_count']:,}",
        title="Channel",
        border_style="blue",
    ))

    # Phase 2: Enumerate videos
    from ..channel_dl import list_all_video_ids

    stderr_console.print("[blue]Enumerating all videos...[/blue]")

    try:
        all_videos = list_all_video_ids(
            info['uploads_playlist_id'],
            progress_callback=lambda cur, tot, pg: stderr_console.print(
                f"  [dim]Listed {cur}/{tot} videos (page {pg})[/dim]"
            ),
        )
    except Exception as e:
        log_event(
            "channel_download_end", channel_id=channel_id,
            status="failed", phase="enumerate",
            error=f"{type(e).__name__}: {e}",
        )
        outcome = CommandResult.failed(
            "channel-download",
            {
                "channel_id": channel_id,
                "channel": info,
                "phase": "enumerate",
            },
            ErrorDetail.from_exception(e, stage="enumerate"),
        )
        log_result(
            "channel_download_end",
            outcome,
            data={
                "channel_id": channel_id,
                "phase": "enumerate",
            },
        )
        _command_error(f"Error listing videos: {e}")

    stderr_console.print(f"[green]Found {len(all_videos)} videos[/green]")

    # Phase 3: Determine delta — resolve corpus by channel ID so renames
    # resume in place and same-name channels never merge
    slug, channel_dir = downloader._resolve_channel_dir(info)
    manifest = downloader._load_manifest(channel_dir)
    existing = manifest.get('videos', {})
    already_done = {k for k, v in existing.items() if v.get('status') == 'done'}

    to_download = [v for v in all_videos if v['video_id'] not in already_done]

    if limit is not None:
        to_download = to_download[:limit]

    if not to_download:
        outcome = CommandResult.completed(
            "channel-download",
            {
                "channel_id": channel_id,
                "channel": info,
                "slug": slug,
                "enumerated": len(all_videos),
                "selected": 0,
                "downloaded": 0,
                "failed": 0,
                "already_done": len(already_done),
                "corpus_done": len(already_done),
                "words": 0,
                "storage": str(channel_dir),
                "already_synced": True,
            },
        )
        log_result(
            "channel_download_end",
            outcome,
            data={
                "channel_id": channel_id,
                "slug": slug,
                "enumerated": len(all_videos),
                "selected": 0,
                "downloaded": 0,
                "failed": 0,
                "already_done": len(already_done),
            },
        )
        _render_channel_download(outcome)
        return

    if already_done:
        stderr_console.print(f"[yellow]Resuming — already have {len(already_done)} transcripts, {len(to_download)} remaining[/yellow]")
    else:
        stderr_console.print(f"[blue]Downloading {len(to_download)} transcripts...[/blue]")

    if workers > 1:
        stderr_console.print(f"[blue]Using {workers} parallel workers[/blue]")

    # Phase 4: Download with progress bar
    from ..transcript import (
        describe_routing_plan,
        get_transcript,
        routing_plan,
    )
    import time as _time
    from datetime import datetime as _dt

    # Force direct connection if --no-proxy
    if no_proxy:
        from ..transcript import disable_proxy
        disable_proxy()
    route_plan = routing_plan()
    click.echo(
        f"Transcript routes ({route_plan['mode']}): "
        f"{describe_routing_plan(route_plan)}; "
        f"route deadline {route_plan['route_timeout_s']:g}s",
        err=True,
    )
    import threading

    languages = [lang, f'{lang}-US', f'{lang}-GB'] if lang == 'en' else [lang, 'en']

    downloaded = 0
    failed = 0
    total_words = 0
    _lock = threading.Lock()  # Protects manifest, counters, and progress bar
    _interrupted = threading.Event()

    # Ensure manifest structure
    if 'videos' not in manifest:
        manifest['videos'] = {}
    for v in all_videos:
        if v['video_id'] not in manifest['videos']:
            manifest['videos'][v['video_id']] = {
                'title': v['title'],
                'published_at': v['published_at'],
                'status': 'pending',
            }

    def _download_one(v_item):
        """Download a single transcript. Thread-safe."""
        nonlocal downloaded, failed, total_words

        if _interrupted.is_set():
            return

        vid_id = v_item['video_id']

        try:
            result = get_transcript(
                vid_id,
                languages=languages,
                progress_callback=_route_progress_for(vid_id),
                fresh_primary=True,
            )

            with _lock:
                if 'error' in result:
                    manifest['videos'][vid_id].update({
                        'status': 'failed',
                        'error': result['error'],
                        'last_attempt': _dt.now().isoformat(),
                    })
                    failed += 1
                    log_event(
                        "channel_download_item", channel_id=channel_id,
                        slug=slug, video_id=vid_id, status="failed",
                        error=str(result["error"]),
                        routes_tried=result.get("routes_tried"),
                    )
                else:
                    transcript_data = {
                        'video_id': vid_id,
                        'title': v_item['title'],
                        'published_at': v_item['published_at'],
                        'channel': info['name'],
                        'channel_id': channel_id,
                        'language': result.get('language', ''),
                        'is_generated': result.get('is_generated', True),
                        'duration_seconds': result.get('duration_seconds', 0),
                        'segment_count': result.get('segment_count', 0),
                        'word_count': len(result.get('full_text', '').split()),
                        'full_text': result.get('full_text', ''),
                        'segments': result.get('segments', []),
                        'route': result.get('route'),
                        'downloaded_at': _dt.now().isoformat(),
                    }
                    downloader._save_transcript(channel_dir, vid_id, transcript_data)

                    wc = len(result.get('full_text', '').split())
                    total_words += wc
                    manifest['videos'][vid_id].update({
                        'status': 'done',
                        'language': result.get('language', ''),
                        'is_generated': result.get('is_generated', True),
                        'duration_seconds': result.get('duration_seconds', 0),
                        'word_count': wc,
                        'route': result.get('route'),
                        'downloaded_at': _dt.now().isoformat(),
                    })
                    downloaded += 1
                    log_event(
                        "channel_download_item", channel_id=channel_id,
                        slug=slug, video_id=vid_id, status="saved",
                        words=wc, route=result.get("route"),
                    )

                # Save manifest (crash-safe)
                done_total = sum(1 for vv in manifest['videos'].values() if vv.get('status') == 'done')
                manifest.update({
                    'channel': info,
                    'total_videos': len(all_videos),
                    'downloaded_count': done_total,
                    'failed_count': sum(1 for vv in manifest['videos'].values() if vv.get('status') == 'failed'),
                    'last_updated': _dt.now().isoformat(),
                })
                downloader._save_manifest(channel_dir, manifest)

        except Exception as e:
            with _lock:
                manifest['videos'][vid_id].update({
                    'status': 'failed',
                    'error': str(e),
                    'last_attempt': _dt.now().isoformat(),
                })
                failed += 1
                log_event(
                    "channel_download_item", channel_id=channel_id,
                    slug=slug, video_id=vid_id, status="failed",
                    error=f"{type(e).__name__}: {e}",
                )
                downloader._save_manifest(channel_dir, manifest)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=stderr_console,
    ) as progress:
        task = progress.add_task(f"Downloading ({workers}w)", total=len(to_download))

        if workers <= 1:
            # Sequential mode (original behavior)
            for i, v in enumerate(to_download):
                if _interrupted.is_set():
                    break
                title = v['title'][:50]
                progress.update(task, description=f"[cyan]{title}...")
                try:
                    _download_one(v)
                except KeyboardInterrupt:
                    _interrupted.set()
                    stderr_console.print("\n[yellow]Interrupted! Progress saved — re-run to resume.[/yellow]")
                    break
                progress.advance(task)
                if i < len(to_download) - 1:
                    _time.sleep(delay)
        else:
            # Parallel mode with ThreadPoolExecutor
            from concurrent.futures import ThreadPoolExecutor, as_completed

            try:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {}
                    for v in to_download:
                        fut = executor.submit(_download_one, v)
                        futures[fut] = v

                    for fut in as_completed(futures):
                        if _interrupted.is_set():
                            break
                        v = futures[fut]
                        try:
                            fut.result()  # Raise any exception from the thread
                        except Exception:
                            pass  # Already handled inside _download_one
                        progress.update(task, description=f"[cyan]{v['title'][:50]}...")
                        progress.advance(task)
            except KeyboardInterrupt:
                _interrupted.set()
                stderr_console.print("\n[yellow]Interrupted! Progress saved — re-run to resume.[/yellow]")

    # Final update
    manifest['last_sync'] = _dt.now().isoformat()
    downloader._save_manifest(channel_dir, manifest)

    # Summary
    done_total = sum(1 for v in manifest['videos'].values() if v.get('status') == 'done')
    interrupted = _interrupted.is_set()
    all_failed = bool(
        to_download
        and failed == len(to_download)
        and downloaded == 0
    )
    if interrupted:
        status = ResultStatus.INTERRUPTED
        errors = [
            ErrorDetail(
                type="Abort",
                message="Channel download interrupted",
                stage="download",
            )
        ]
        warnings = []
    elif all_failed:
        status = ResultStatus.FAILED
        errors = [
            ErrorDetail(
                type="ChannelDownloadError",
                message=(
                    f"All {failed} selected channel transcripts failed."
                ),
                stage="download",
            )
        ]
        warnings = []
    elif failed:
        status = ResultStatus.PARTIAL
        errors = []
        warnings = [f"{failed} selected transcript download(s) failed"]
    else:
        status = ResultStatus.COMPLETED
        errors = []
        warnings = []

    outcome = CommandResult(
        command="channel-download",
        status=status,
        data={
            "channel_id": channel_id,
            "channel": info,
            "slug": slug,
            "enumerated": len(all_videos),
            "selected": len(to_download),
            "downloaded": downloaded,
            "failed": failed,
            "already_done": len(already_done),
            "corpus_done": done_total,
            "words": total_words,
            "storage": str(channel_dir),
            "routing_plan": route_plan,
            "interrupted": interrupted,
            "already_synced": False,
        },
        errors=errors,
        warnings=warnings,
    )
    log_result(
        "channel_download_end",
        outcome,
        topic=None,
        data={
            "channel_id": channel_id,
            "slug": slug,
            "enumerated": len(all_videos),
            "selected": len(to_download),
            "downloaded": downloaded,
            "failed": failed,
            "corpus_done": done_total,
            "words": total_words,
            "routing_plan": route_plan,
        },
    )
    _render_channel_download(outcome)
    if interrupted:
        raise click.Abort()
    if all_failed:
        _command_error(f"All {failed} selected channel transcripts failed.")


@click.command("channel-status")
@click.argument("channel_slug", required=False, default=None)
def channel_status(channel_slug: str):
    """Show download status and stats for channel corpora.

    Without arguments, lists all downloaded channels.
    With a channel slug, shows detailed stats.

    \b
    Examples:
        filmot channel-status
        filmot channel-status chat-with-traders
    """
    from ..channel_dl import ChannelDownloader
    from ..ledger import log_result

    downloader = ChannelDownloader()

    if channel_slug is None:
        channels = downloader.get_downloaded_channels()
        outcome = CommandResult(
            command="channel-status",
            status=(
                ResultStatus.COMPLETED
                if channels
                else ResultStatus.EMPTY
            ),
            data={
                "scope": "all",
                "channels": channels,
            },
        )
        log_result(
            "channel_status",
            outcome,
            data={
                "scope": "all",
                "channels": len(channels),
            },
        )
        _render_channel_status(outcome)
        return

    stats = downloader.get_channel_stats(channel_slug)
    outcome = CommandResult(
        command="channel-status",
        status=(
            ResultStatus.COMPLETED
            if stats
            else ResultStatus.SKIPPED
        ),
        data={
            "scope": "channel",
            "slug": channel_slug,
            "stats": stats,
        },
    )
    if stats:
        event_data = {
            "scope": "channel",
            "slug": channel_slug,
            "downloaded": stats["downloaded"],
            "failed": stats["failed"],
            "pending": stats["pending"],
            "total": stats["total_videos"],
        }
    else:
        event_data = {
            "scope": "channel",
            "slug": channel_slug,
        }
    log_result(
            "channel_status",
        outcome,
        data=event_data,
    )
    _render_channel_status(outcome)


@click.command("channel-search")
@click.argument("channel_slug")
@click.argument("query")
@click.option("--limit", "-n", default=20, type=click.IntRange(1), help="Max results (default: 20)")
def channel_search(channel_slug: str, query: str, limit: int):
    """Search across all transcripts in a downloaded channel corpus.

    Supports proximity operators for finding words near each other:

    \b
    Plain search:
        filmot channel-search chat-with-traders "Sharpe ratio"

    NEAR/N – two phrases within N words of each other:
        filmot channel-search chat-with-traders '"machine learning" NEAR/10 "neural network"'

    ~N – words in a phrase within N words of each other:
        filmot channel-search chat-with-traders '"deep learning tensorflow"~5'
    """
    from ..channel_dl import ChannelDownloader, _parse_proximity_query
    from ..ledger import log_result

    def _format_near_operand(terms: list[str]) -> str:
        if len(terms) == 1:
            return f'"{terms[0]}"'
        return "(" + " | ".join(f'"{term}"' for term in terms) + ")"

    downloader = ChannelDownloader()

    try:
        parsed = _parse_proximity_query(query)
        if parsed[0] == "near":
            qlabel = (
                f"{_format_near_operand(parsed[1])} NEAR/{parsed[3]} "
                f"{_format_near_operand(parsed[2])}"
            )
        elif parsed[0] == "tilde":
            qlabel = f'"{" ".join(parsed[1])}"~{parsed[2]}'
        else:
            qlabel = query
        with console.status(f"[blue]Searching '{qlabel}' across {channel_slug}..."):
            results = downloader.search_corpus(channel_slug, query)
    except ValueError as e:
        outcome = CommandResult.failed(
            "channel-search",
            {
                "slug": channel_slug,
                "query": query,
                "limit": limit,
            },
            ErrorDetail.from_exception(e, stage="search"),
        )
        log_result(
            "channel-search",
            outcome,
            data={
                "slug": channel_slug,
                "query": query,
                "limit": limit,
            },
        )
        _command_error(str(e))

    total_matches = sum(result["match_count"] for result in results)
    limited_results = results[:limit]
    if parsed[0] == "near":
        highlight_terms = []
        seen = set()
        for group in (parsed[1], parsed[2]):
            for term in group:
                key = term.lower()
                if key not in seen:
                    highlight_terms.append(term)
                    seen.add(key)
    elif parsed[0] == "tilde":
        highlight_terms = list(parsed[1])
    else:
        highlight_terms = [query]

    outcome = CommandResult(
        command="channel-search",
        status=(
            ResultStatus.COMPLETED
            if limited_results
            else ResultStatus.EMPTY
        ),
        data={
            "slug": channel_slug,
            "query": query,
            "display_query": qlabel,
            "query_kind": parsed[0],
            "highlight_terms": highlight_terms,
            "results": limited_results,
            "summary": {
                "videos": len(limited_results),
                "hits": total_matches,
                "limit": limit,
            },
        },
    )
    log_result(
        "channel-search",
        outcome,
        data={
            "slug": channel_slug,
            "query": query,
            "videos": len(limited_results),
            "hits": total_matches,
            "limit": limit,
        },
    )
    _render_channel_search(outcome)


# ========== SESSIONS (LEDGER) ==========

@click.command("download")
@click.option("--topic", "-t", required=True, help="Library topic to save transcripts under")
@click.option("--count", "-n", default=50, type=click.IntRange(1), help="Maximum transcripts to download (default: 50)")
@click.option("--lang", "-l", default=None, help="Preferred transcript language code")
@click.option("--fallback", is_flag=True, help="Use AWS Transcribe fallback")
@click.option("--dedupe", is_flag=True, help="Skip duplicate transcripts")
@click.option("--no-proxy", is_flag=True, help="Bypass proxy, connect directly with your IP")
def download(
    topic: str,
    count: int,
    lang: str,
    fallback: bool,
    dedupe: bool,
    no_proxy: bool,
):
    """Download transcripts from piped search results.

    Reads JSON search results from stdin and downloads transcripts
    to the library. Enables pipeline workflows.

    Examples:

    \b
        filmot search "deep sea mining" --title "deep sea mining" --raw | filmot download -t deep-sea
        filmot search "AI safety" --pages 5 --raw > results.json
        type results.json | filmot download -t ai-safety --dedupe
    """
    import sys
    from ..ledger import log_result

    try:
        raw_input = sys.stdin.read()
        results = json_mod.loads(raw_input)
    except (json_mod.JSONDecodeError, ValueError) as e:
        outcome = CommandResult.failed(
            "download",
            {
                "topic": topic,
                "count": count,
                "lang": lang,
                "fallback": fallback,
                "dedupe": dedupe,
            },
            ErrorDetail.from_exception(e, stage="input"),
        )
        log_result(
            "download",
            outcome,
            topic=topic,
            data={
                "topic": topic,
                "count": count,
                "lang": lang,
                "fallback": fallback,
                "dedupe": dedupe,
                "phase": "input",
            },
        )
        _command_error(
            f"Invalid JSON from stdin: {e}. Pipe search results with --raw."
        )

    # Wrap in expected format if needed
    if isinstance(results, list):
        results = {"result": results}

    if no_proxy:
        from ..transcript import disable_proxy
        disable_proxy()
        console.print("[dim]Proxy disabled, using direct connection[/dim]")

    progress_console = _DownloadProgressConsole(console)
    try:
        summary = _bulk_download_transcripts(
            results,
            f"{topic}:{count}",
            progress_console,
            fallback=fallback,
            dedupe=dedupe,
            lang=lang,
        )
    except click.ClickException as error:
        outcome = CommandResult.failed(
            "download",
            {
                "topic": topic,
                "count": count,
                "lang": lang,
                "fallback": fallback,
                "dedupe": dedupe,
                "human_messages": progress_console.final_messages,
            },
            ErrorDetail.from_exception(error, stage="download"),
        )
        log_result(
            "download",
            outcome,
            topic=topic,
            data={
                "topic": topic,
                "count": count,
                "lang": lang,
                "fallback": fallback,
                "dedupe": dedupe,
                "phase": "download",
            },
        )
        _render_download(outcome)
        raise
    except Exception as error:
        outcome = CommandResult.failed(
            "download",
            {
                "topic": topic,
                "count": count,
                "lang": lang,
                "fallback": fallback,
                "dedupe": dedupe,
                "human_messages": progress_console.final_messages,
            },
            ErrorDetail.from_exception(error, stage="download"),
        )
        log_result(
            "download",
            outcome,
            topic=topic,
            data={
                "topic": topic,
                "count": count,
                "lang": lang,
                "fallback": fallback,
                "dedupe": dedupe,
                "phase": "download",
            },
        )
        _render_download(outcome)
        raise

    summary = dict(summary or {})
    selected = int(summary.get("selected", 0))
    failed = int(summary.get("failed", 0))
    if selected == 0:
        status = ResultStatus.EMPTY
        warnings = []
    elif failed:
        status = ResultStatus.PARTIAL
        warnings = [f"{failed} selected transcript download(s) failed"]
    else:
        status = ResultStatus.COMPLETED
        warnings = []
    outcome = CommandResult(
        command="download",
        status=status,
        data={
            "topic": topic,
            "count": count,
            "lang": lang,
            "fallback": fallback,
            "dedupe": dedupe,
            "no_proxy": no_proxy,
            **summary,
            "human_messages": progress_console.final_messages,
        },
        warnings=warnings,
    )
    log_result(
        "download",
        outcome,
        topic=topic,
        data={
            "topic": topic,
            "count": count,
            "lang": lang,
            "fallback": fallback,
            "dedupe": dedupe,
            "no_proxy": no_proxy,
            **summary,
        },
    )
    _render_download(outcome)


# ========== PROXY POOL ==========
