"""Filmot CLI - Command Line Interface."""

import click
from contextlib import nullcontext
import errno
import math
import os
import re
import sys
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import print as rprint
import json as json_mod
from rich.progress import Progress, SpinnerColumn, TextColumn
from .api import FilmotClient


console = Console()
stderr_console = Console(stderr=True)


class _PipeFlushWrapper:
    """Proxy a text stream while suppressing shutdown flush pipe errors."""

    def __init__(self, wrapped):
        self.wrapped = wrapped

    def flush(self):
        try:
            self.wrapped.flush()
        except OSError as error:
            if error.errno not in (errno.EPIPE, errno.EINVAL):
                raise

    def __getattr__(self, name):
        return getattr(self.wrapped, name)


def _silence_broken_pipe_streams() -> None:
    """Prevent interpreter shutdown from retrying a closed consumer pipe."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        if not isinstance(stream, _PipeFlushWrapper):
            setattr(sys, stream_name, _PipeFlushWrapper(stream))


def _redact_diagnostic(value):
    """Recursively redact credential-bearing proxy userinfo."""
    from .proxy_pool import redact_sensitive_text

    if isinstance(value, dict):
        return {
            key: _redact_diagnostic(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_diagnostic(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_diagnostic(item) for item in value)
    if isinstance(value, str) or isinstance(value, Exception):
        return redact_sensitive_text(value)
    return value


def _command_error(message: str, *, raw: bool = False, payload: Optional[dict] = None):
    """Terminate a command with a machine-detectable failure.

    Human mode uses Click's standard non-zero error path.  Raw mode emits one
    JSON value on stdout and exits non-zero, preserving the stdout contract for
    callers that always parse JSON (including failures).
    """
    safe_message = _redact_diagnostic(str(message))
    if raw:
        body = (
            _redact_diagnostic(payload)
            if payload is not None
            else {"error": safe_message}
        )
        click.echo(json_mod.dumps(body, indent=2, ensure_ascii=False))
        raise click.exceptions.Exit(1)
    raise click.ClickException(safe_message)


def _diagnostic(message: str, *, raw: bool = False) -> None:
    """Write a human diagnostic without contaminating raw JSON stdout."""
    if raw:
        return
    console.print(message)


def _search_status(message: str, *, raw: bool = False):
    """Return a Rich status context only when stdout is human-oriented."""
    return nullcontext() if raw else console.status(message)


def _result_videos(results: dict) -> list:
    return results.get("result", results.get("videos", results.get("items", [])))


def _channel_candidates(payload: object) -> list:
    """Normalize the Filmot channel-search response to candidate dictionaries."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("channels", "items", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _resolve_channel_filter(client, term: str, count: Optional[int]) -> tuple[str, list]:
    """Resolve fuzzy channel text explicitly and fail closed on no match."""
    response = client.search_channels(term)
    if isinstance(response, dict) and response.get("error"):
        raise click.ClickException(
            f'Could not resolve --channel "{term}": {response["error"]}'
        )

    candidates = _channel_candidates(response)
    requested = max(1, count or 10)

    # Exact labels/handles are safer than broad fuzzy matches.  If the API
    # supplies an exact candidate, keep it first without silently widening to
    # every other fuzzy result.
    folded = term.casefold().strip().lstrip("@")
    exact = [
        item for item in candidates
        if str(item.get("label", "")).casefold().strip() == folded
        or str(item.get("newshortname", "")).casefold().strip().lstrip("@") == folded
    ]
    selected = (exact or candidates)[:requested]
    selected = [
        item for item in selected
        if item.get("value") or item.get("channelid") or item.get("id")
    ]
    if not selected:
        raise click.ClickException(
            f'No channels resolved for --channel "{term}". '
            "Use `filmot channels TERM` and pass an exact --channel-id."
        )

    ids = [
        str(item.get("value") or item.get("channelid") or item.get("id"))
        for item in selected
    ]
    normalized = [
        {
            "id": channel_id,
            "name": str(item.get("label") or item.get("name") or item.get("title") or "Unknown"),
        }
        for item, channel_id in zip(selected, ids)
    ]
    return ",".join(ids), normalized


def _merge_channel_ids(explicit_ids: Optional[str], resolved_ids: Optional[str]) -> Optional[str]:
    values = []
    for group in (explicit_ids, resolved_ids):
        if group:
            values.extend(part.strip() for part in group.split(",") if part.strip())
    return ",".join(dict.fromkeys(values)) or None


def _density(video: dict) -> float:
    duration = video.get("duration", 0) or 0
    hits = len(video.get("hits", []))
    return hits / (duration / 60) if duration > 0 else 0.0


def _search_scope(results: dict, requested_page: Optional[int] = None) -> dict:
    videos = _result_videos(results)
    total = results.get("totalresultcount")
    if total is None:
        total = len(videos)
    return {
        "api_total": total,
        "candidates_fetched": len(videos),
        "pages_fetched": results.get("pages_fetched", 1),
        "page": requested_page or 1,
        "partial": bool(results.get("partial")),
    }


def _low_result_query_hint(query: str, count: int) -> Optional[str]:
    """Suggest recall-preserving variants without weakening strict matching."""
    if count > 5:
        return None
    has_phrase = '"' in query
    has_proximity = bool(re.search(r"\b(?:NOT)?NEAR\s*/\s*\d+\b|\"[^\"\n]+\"~\d+", query, re.I))
    if not (has_phrase or has_proximity):
        return None
    return (
        f"Only {count} result{'s' if count != 1 else ''} matched the literal "
        "phrase/proximity query. Try singular/plural, inflection, spelling, or "
        "caption-transcription variants; strict matching is unchanged."
    )


def _whole_word_summary(value: object, max_chars: int = 220) -> str:
    """Cap an error on a word boundary, retaining useful detail."""
    text = " ".join(str(_redact_diagnostic(value)).split())
    if len(text) <= max_chars:
        return text
    prefix = text[: max_chars - 1]
    if " " in prefix:
        prefix = prefix.rsplit(" ", 1)[0]
    return prefix.rstrip(" ,.;:-") + "…"


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


class VideoIdType(click.ParamType):
    """Custom type for YouTube video IDs that handles IDs starting with dashes."""
    name = "video_id"

    def convert(self, value, param, ctx):
        if value is None:
            return None
        # Just pass through - the transcript module handles extraction
        return str(value)


# Global video ID type instance
VIDEO_ID = VideoIdType()


@click.group()
@click.version_option(version="0.3.0", prog_name="filmot")
def cli():
    """Filmot CLI - Search YouTube transcripts and metadata."""
    pass


# ========== SEARCH SUBTITLES ==========

@cli.command()
@click.argument("query")
@click.option("--lang", "-l", default=None, help="Language code (e.g., en, nl, fr, de)")
@click.option("--page", "-p", default=None, type=click.IntRange(1), help="Page number (50 results per page)")
@click.option("--pages", default=1, type=click.IntRange(1, 50), show_default=True,
              help="Fetch this many pages before client-side filtering/ranking")
@click.option("--candidate-pool", default=None, type=click.IntRange(1),
              help="Maximum candidates to fetch across --pages")
@click.option("--category", "-c", default=None, help="Video category (e.g., 'Science & Technology')")
@click.option("--exclude", default=None, help="Categories to exclude (comma-separated)")
@click.option("--channel-id", default=None, help="Limit to specific channel ID")
@click.option("--channel", default=None, help="Find top channels matching this text, then search those")
@click.option("--channel-count", default=None, type=click.IntRange(1), help="Limit top channels when using --channel (default 10)")
@click.option("--title", default=None, help="Filter by video title")
@click.option("--min-views", default=None, type=click.IntRange(0), help="Minimum view count")
@click.option("--max-views", default=None, type=click.IntRange(0), help="Maximum view count")
@click.option("--min-likes", default=None, type=click.IntRange(0), help="Minimum like count")
@click.option("--max-likes", default=None, type=click.IntRange(0), help="Maximum like count")
@click.option("--min-duration", default=None, type=click.IntRange(0), help="Minimum duration in seconds")
@click.option("--max-duration", default=None, type=click.IntRange(0), help="Maximum duration in seconds")
@click.option("--start-date", default=None, help="Start date (yyyy-mm-dd)")
@click.option("--end-date", default=None, help="End date (yyyy-mm-dd)")
@click.option("--country", default=None, type=click.IntRange(0), help="Country code (e.g., 217=US, 153=UK)")
@click.option("--license", "license_type", default=None, type=click.Choice(["1", "2"]), help="License: 1=Standard, 2=Creative Commons")
@click.option("--sort", default=None, type=click.Choice(["viewcount", "likecount", "uploaddate", "duration", "chanrank", "id", "density"]), help="Sort field (density = client-side matches/min sort)")
@click.option("--order", default=None, type=click.Choice(["asc", "desc"]), help="Sort order")
@click.option("--manual-subs", is_flag=True, help="Search manual subtitles only (default: auto subs). Cannot search both in same request.")
@click.option("--max-query-time", default=None, type=click.IntRange(4, 15000), help="Max query time in ms (4-15000)")
@click.option("--hit-format", default=None, type=click.Choice(["0", "1"]), help="Hit format: 0=context, 1=full lines")
@click.option("--full", is_flag=True, help="Show all hit details for each displayed video on fetched pages")
@click.option(
    "--raw",
    is_flag=True,
    help="Output one processed JSON response with scope metadata",
)
@click.option("--min-matches", default=None, type=click.IntRange(0), help="Only show videos with at least N subtitle matches")
@click.option("--limit", "--top", "limit", default=None, type=click.IntRange(0),
              help="Maximum video results to display/output")
@click.option("--max-hits", default=None, type=click.IntRange(0),
              help="Maximum hit details to display per video")
@click.option("--context", "context_chars", default=50, type=click.IntRange(0), help="Characters of context per side in snippets (raise for fuller quotes)")
@click.option("--bulk-download", default=None, help="Download top N transcripts to TOPIC (e.g., --bulk-download prompt-injection:10)")
@click.option("--fallback", is_flag=True, help="Use AWS Transcribe fallback during bulk download when captions unavailable")
@click.option("--dedupe", is_flag=True, help="Skip duplicate transcripts during bulk download")
@click.option("--no-proxy", is_flag=True, help="Bypass proxy during bulk download, connect directly")
def search(query: str, lang: str, page: int, pages: int, candidate_pool: int,
           category: str, exclude: str,
           channel_id: str, channel: str, channel_count: int, title: str,
           min_views: int, max_views: int, min_likes: int, max_likes: int,
           min_duration: int, max_duration: int, start_date: str, end_date: str,
           country: int, license_type: str, sort: str, order: str, manual_subs: bool,
           max_query_time: int, hit_format: str, full: bool, raw: bool, min_matches: int,
           limit: int, max_hits: int, context_chars: int, bulk_download: str,
           fallback: bool, dedupe: bool, no_proxy: bool):
    """Search for videos by subtitle/transcript content.

    Unquoted words use loose transcript-wide implicit AND: each word may occur
    anywhere in the same transcript and does not imply a relationship. Use an
    exact phrase or NEAR/N when the relationship itself matters. ``--full``
    expands hit details; it does not fetch additional result pages.

    Examples:

    \b
        filmot search "machine learning"             # loose transcript-wide AND
        filmot search '"machine learning"'           # exact phrase
        filmot search 'OpenAI|Anthropic'             # OR
        filmot search '"AI" NEAR/20 "job loss"'      # relationship/proximity
        filmot search "recipe" --pages 3 --sort density --limit 20
        filmot search "tutorial" --channel "programming" --channel-count 5
    """
    from .ledger import log_event

    if raw and bulk_download:
        raise click.UsageError("--raw and --bulk-download cannot be combined")
    if not bulk_download and (fallback or dedupe or no_proxy):
        raise click.UsageError(
            "--fallback, --dedupe, and --no-proxy require --bulk-download"
        )
    for minimum, maximum, label in (
        (min_views, max_views, "views"),
        (min_likes, max_likes, "likes"),
        (min_duration, max_duration, "duration"),
    ):
        if minimum is not None and maximum is not None and minimum > maximum:
            raise click.UsageError(
                f"minimum {label} cannot exceed maximum {label}"
            )

    try:
        client = FilmotClient()
        resolved_channels = []
        resolved_channel_ids = None
        if channel:
            try:
                resolved_channel_ids, resolved_channels = _resolve_channel_filter(
                    client, channel, channel_count
                )
            except click.ClickException as error:
                log_event(
                    "search",
                    query=query,
                    channel=channel,
                    channel_count=channel_count,
                    status="failed",
                    error=str(error),
                    raw=raw,
                )
                _command_error(str(error), raw=raw)
        effective_channel_ids = _merge_channel_ids(channel_id, resolved_channel_ids)

        api_kwargs = dict(
            lang=lang,
            category=category,
            exclude_category=exclude,
            channel_id=effective_channel_ids,
            title=title,
            min_views=min_views,
            max_views=max_views,
            min_likes=min_likes,
            max_likes=max_likes,
            start_duration=min_duration,
            end_duration=max_duration,
            start_date=start_date,
            end_date=end_date,
            country=country,
            license_type=int(license_type) if license_type else None,
            sort_field=sort if sort != "density" else None,
            sort_order=order if sort != "density" else None,
            search_manual_subs=1 if manual_subs else None,
            max_query_time=max_query_time,
            hit_format=int(hit_format) if hit_format else None,
        )

        if page is not None and (pages > 1 or candidate_pool is not None):
            raise click.UsageError(
                "--page cannot be combined with --pages or --candidate-pool"
            )

        fetch_many = pages > 1 or candidate_pool is not None
        with _search_status(
            f"[bold green]Searching subtitles for '{query}'...", raw=raw
        ):
            if fetch_many:
                results = client.search_subtitles_all(
                    query=query,
                    max_pages=pages,
                    max_results=candidate_pool,
                    **api_kwargs,
                )
            else:
                results = client.search_subtitles(
                    query=query,
                    page=page,
                    **api_kwargs,
                )

        diagnostics = []
        if client.last_query_rewrite and not raw:
            diagnostics.append(
                f"[dim]Rewrote unsupported proximity syntax:[/dim] "
                f"[dim]{client.last_query_rewrite['from']}[/dim] "
                f"[dim]->[/dim] "
                f"[dim]{client.last_query_rewrite['to']}[/dim]"
            )

        if "error" in results:
            log_event(
                "search",
                query=query,
                lang=lang,
                status="failed",
                error=str(results["error"]),
                channel=channel,
                channel_id=effective_channel_ids,
                title=title,
            )
            _command_error(
                str(results["error"]),
                raw=raw,
                payload=results if raw else None,
            )

        # Fail closed if the API ever returns a video outside the displayed
        # fuzzy-channel resolution.  Do not let a dropped upstream constraint
        # masquerade as a channel-scoped search.
        if effective_channel_ids:
            allowed = set((effective_channel_ids or "").split(","))
            outside = [
                video for video in _result_videos(results)
                if str(
                    video.get("channelid")
                    or video.get("channel_id")
                    or ""
                ) not in allowed
            ]
            if outside:
                message = (
                    "Filmot returned results outside, or without an ID in, the "
                    "requested channel set; discarding the response rather "
                    "than failing open."
                )
                log_event(
                    "search",
                    query=query,
                    channel=channel,
                    channel_id=effective_channel_ids,
                    resolved_channels=resolved_channels or None,
                    status="failed",
                    failure_stage="channel_validation",
                    outside_results=len(outside),
                    error=message,
                    raw=raw,
                )
                _command_error(
                    message,
                    raw=raw,
                )

        # Apply --min-matches filter (client-side)
        fetched_count = len(_result_videos(results))
        if min_matches is not None:
            videos = _result_videos(results)
            filtered = [v for v in videos if len(v.get("hits", [])) >= min_matches]
            original_count = len(videos)
            results["result"] = filtered
            if original_count != len(filtered):
                diagnostics.append(
                    f"[dim]Filtered: {original_count} -> {len(filtered)} videos "
                    f"(min {min_matches} matches)[/dim]"
                )

        # Apply --sort density (client-side sort by matches per minute)
        if sort == "density":
            videos = _result_videos(results)
            results["result"] = sorted(
                videos, key=_density, reverse=(order != "asc")
            )
            diagnostics.append("[dim]Sorted by density (matches/min)[/dim]")

        scope = _search_scope(results, page)
        scope["candidates_fetched"] = fetched_count
        scope["post_filter_count"] = len(_result_videos(results))
        results["scope"] = scope

        if scope["partial"]:
            diagnostics.append(
                "[yellow]Partial result set:[/yellow] pagination stopped early"
                + (
                    f" ({_whole_word_summary(results['page_error'])})"
                    if results.get("page_error")
                    else ""
                )
                + "."
            )

        if (sort == "density" or min_matches is not None) and \
                scope["api_total"] > scope["candidates_fetched"]:
            scope_message = (
                f"Client-side {'sorting/filtering' if sort == 'density' and min_matches is not None else 'sorting' if sort == 'density' else 'filtering'} "
                f"covered {scope['candidates_fetched']} fetched candidate(s) across "
                f"{scope['pages_fetched']} page(s), not all {scope['api_total']:,} API results. "
                "Increase --pages/--candidate-pool for a wider ranking scope."
            )
            diagnostics.append(f"[yellow]Scope:[/yellow] {scope_message}")

        # Hint if --title filter produced no results
        videos_list = _result_videos(results)
        if not videos_list and title:
            diagnostics.append(
                f"[yellow]No results with --title \"{title}\". "
                "Try without --title to broaden the search.[/yellow]"
            )

        low_result_hint = _low_result_query_hint(
            query,
            int(results.get("totalresultcount", fetched_count) or 0),
        )
        if low_result_hint:
            diagnostics.append(
                f"[yellow]Recall hint:[/yellow] {low_result_hint}"
            )

        # B1/B6c: write the complete event before any render, raw return, bulk
        # return, or pipe-sensitive output.
        log_event(
            "search",
            query=query,
            effective_query=(
                client.last_query_rewrite["to"]
                if client.last_query_rewrite else query
            ),
            lang=lang,
            page=page or 1,
            pages=scope["pages_fetched"],
            candidate_pool=candidate_pool,
            category=category,
            exclude_category=exclude,
            channel=channel,
            resolved_channels=resolved_channels or None,
            channel_id=effective_channel_ids,
            channel_count=channel_count,
            title=title,
            min_views=min_views,
            max_views=max_views,
            min_likes=min_likes,
            max_likes=max_likes,
            min_duration=min_duration,
            max_duration=max_duration,
            start_date=start_date,
            end_date=end_date,
            country=country,
            license=license_type,
            sort=sort,
            order=order,
            manual_subs=manual_subs,
            max_query_time=max_query_time,
            hit_format=hit_format,
            min_matches=min_matches,
            full=full,
            context_chars=context_chars,
            api_total=scope["api_total"],
            page_count=scope["candidates_fetched"],
            post_filter_count=scope["post_filter_count"],
            duplicates_skipped=results.get("duplicates_skipped", 0),
            # Backward-compatible aliases used by the sessions renderer.
            total=scope["api_total"],
            results=scope["post_filter_count"],
            partial=scope["partial"],
            raw=raw,
            bulk_download=bulk_download,
            fallback=fallback,
            dedupe=dedupe,
            no_proxy=no_proxy,
            limit=limit,
            max_hits=max_hits,
            status="partial" if scope["partial"] else "completed",
        )

        if resolved_channels:
            rendered = ", ".join(
                f'{item["name"]} ({item["id"]})' for item in resolved_channels
            )
            diagnostics.insert(
                0, f"[cyan]Resolved --channel:[/cyan] {rendered}"
            )

        # Rendering can encounter EPIPE. The durable event above is already on
        # disk before any of these human diagnostics are emitted.
        for message in diagnostics:
            _diagnostic(message, raw=raw)

        if raw:
            raw_results = dict(results)
            output_videos = _result_videos(results)
            if limit is not None:
                output_videos = output_videos[:limit]
            if max_hits is not None:
                output_videos = [
                    dict(
                        video,
                        hits=list(video.get("hits", []))[:max_hits],
                    )
                    for video in output_videos
                ]
            raw_results["result"] = output_videos
            raw_results["scope"] = dict(
                scope, output_count=len(output_videos),
                max_hits=max_hits,
            )
            raw_results["effective_filters"] = {
                "channel_id": effective_channel_ids,
                "resolved_channels": resolved_channels,
                "title": title,
                "lang": lang,
            }
            click.echo(json_mod.dumps(raw_results, indent=2, ensure_ascii=False))
            return

        if client.last_cache_hit:
            console.print("[dim]Cached response[/dim]")

        # Bulk download mode
        if bulk_download:
            if no_proxy:
                from .transcript import disable_proxy
                disable_proxy()
                console.print("[dim]Proxy disabled, using direct connection[/dim]")
            _bulk_download_transcripts(
                results,
                bulk_download,
                console,
                fallback=fallback,
                dedupe=dedupe,
                lang=lang,
            )
            return

        # Display formatted results
        _display_subtitle_results(
            results,
            query,
            full=full,
            context_chars=context_chars,
            limit=limit,
            max_hits=max_hits,
        )

        hint = _freshness_hint(start_date, end_date, query)
        if hint:
            console.print(f"\n{hint}")

    except BrokenPipeError:
        _silence_broken_pipe_streams()
        return
    except OSError as error:
        if error.errno in (errno.EPIPE, errno.EINVAL):
            _silence_broken_pipe_streams()
            return
        log_event(
            "search",
            query=query,
            status="failed",
            failure_stage="io",
            error=f"{type(error).__name__}: {error}",
            raw=raw,
        )
        _command_error(str(error), raw=raw)
    except click.exceptions.Exit:
        raise
    except click.ClickException:
        raise
    except ValueError as e:
        log_event(
            "search",
            query=query,
            status="failed",
            failure_stage="configuration",
            error=f"{type(e).__name__}: {e}",
            raw=raw,
        )
        _command_error(f"Configuration error: {e}", raw=raw)
    except Exception as e:
        log_event(
            "search",
            query=query,
            status="failed",
            failure_stage="unexpected",
            error=f"{type(e).__name__}: {e}",
            raw=raw,
        )
        _command_error(str(e), raw=raw)


def _format_timestamp(seconds: float) -> str:
    """Format seconds into readable timestamp."""
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}"
    else:
        return f"{mins}:{secs:02d}"


def _format_duration(duration: int) -> str:
    """Format duration in seconds into readable string."""
    mins, secs = divmod(duration, 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}h {mins}m"
    else:
        return f"{mins}m {secs}s"


def _hit_start(hit: dict) -> float:
    """Best-available start time (seconds) for a hit, across both hit formats."""
    lines = hit.get("lines", [])
    if lines:
        return lines[0].get("start", hit.get("start", 0))
    return hit.get("start", 0)


def _deep_link(video_id: str, start_seconds: float) -> str:
    """YouTube watch URL that jumps straight to *start_seconds*."""
    return f"https://youtube.com/watch?v={video_id}&t={int(start_seconds)}s"


def _display_hit(hit: dict, video_id: str, context_chars: int = 50):
    """Display a single hit match, handling both hit formats.

    context_chars controls how much surrounding context to show per side for
    hit_format=0 snippets (raise it with --context for fuller quotes).
    """
    start = hit.get("start", 0)
    token = hit.get("token", "")
    timestamp = _format_timestamp(start)
    link = _deep_link(video_id, start)

    # Check if this is hit_format=1 (has 'lines' array) or hit_format=0 (has ctx_before/after)
    lines = hit.get("lines", [])

    if lines:
        # Hit format 1: Full subtitle lines
        for line in lines:
            line_text = line.get("text", "")
            line_start = line.get("start", start)
            line_dur = line.get("dur", 0)
            line_ts = _format_timestamp(line_start)
            line_link = _deep_link(video_id, line_start)
            # Highlight the token in the line text
            highlighted = line_text.replace(token, f"[bold yellow]{token}[/bold yellow]")
            highlighted = highlighted.replace(token.capitalize(), f"[bold yellow]{token.capitalize()}[/bold yellow]")
            highlighted = highlighted.replace(token.upper(), f"[bold yellow]{token.upper()}[/bold yellow]")
            console.print(f"      [[link={line_link}]{line_ts}[/link]] {highlighted}")
    else:
        # Hit format 0: Context snippets
        ctx_before = hit.get("ctx_before", "")
        ctx_after = hit.get("ctx_after", "")

        # Truncate context if too long
        ctx_before = ctx_before[-context_chars:] if len(ctx_before) > context_chars else ctx_before
        ctx_after = ctx_after[:context_chars] if len(ctx_after) > context_chars else ctx_after

        console.print(f"      [[link={link}]{timestamp}[/link]] ...{ctx_before} [bold yellow]{token}[/bold yellow] {ctx_after}...")


def _hit_fingerprint_text(video: dict) -> str:
    """Normalized concatenation of a video's hit text, for echo detection."""
    parts = []
    for hit in video.get("hits", []):
        lines = hit.get("lines", [])
        if lines:
            parts.extend(line.get("text", "") for line in lines)
        else:
            parts.append(hit.get("ctx_before", ""))
            parts.append(hit.get("token", ""))
            parts.append(hit.get("ctx_after", ""))
    text = " ".join(parts).lower()
    return re.sub(r"[^a-z0-9 ]", " ", text)


def _detect_echo_clusters(videos: list, n: int = 5, threshold: float = 0.5) -> dict:
    """Flag videos whose hit phrasing is near-identical (script-copying / AI-slop echo).

    Builds word n-gram shingle sets per video and clusters by Jaccard similarity.
    Returns {video_index: cluster_label} only for videos in a cluster of >= 2.
    Convergence (different words, same idea) scores low and is left unflagged;
    echo (copied phrasing) scores high. See research guide §3 (convergence vs echo).
    """
    shingles = []
    for v in videos:
        words = _hit_fingerprint_text(v).split()
        grams = {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)} if len(words) >= n else set()
        shingles.append(grams)

    parent = list(range(len(videos)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(videos)):
        if not shingles[i]:
            continue
        for j in range(i + 1, len(videos)):
            if not shingles[j]:
                continue
            inter = len(shingles[i] & shingles[j])
            if not inter:
                continue
            union = len(shingles[i] | shingles[j])
            if union and inter / union >= threshold:
                parent[find(i)] = find(j)

    groups = {}
    for i in range(len(videos)):
        groups.setdefault(find(i), []).append(i)

    clusters = {}
    label = 0
    for members in groups.values():
        if len(members) >= 2:
            label += 1
            for idx in members:
                clusters[idx] = label
    return clusters


def _format_count(count: int) -> str:
    """Format large numbers into readable format (e.g., 1.5M, 2.3B)."""
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.1f}B"
    elif count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        return f"{count / 1_000:.1f}K"
    else:
        return str(count)


def _grep_transcript(result: dict, query: str, context_chars: int = 160) -> bool:
    """Run a proximity/plain query against a fetched transcript and print matches.

    Reuses the local proximity engine from channel_dl (NEAR/N, OR-groups, ~N tilde,
    plain substring) so the same operators work on a single transcript — closing the
    search → download → grep loop inside the tool. Match snippets carry timestamped
    deep links derived from the transcript's own segments.
    """
    from .channel_dl import (
        _parse_proximity_query,
        _looks_like_proximity,
        _find_grouped_near_matches,
        _find_tilde_matches,
        _phrase_occurrences,
    )

    video_id = result.get("video_id", "")
    text = result.get("full_text", "")
    segments = result.get("segments", [])
    if not text:
        console.print("[yellow]No transcript text to search.[/yellow]")
        return False

    # Build a char-offset → segment-start-time index matching how full_text was joined
    offsets = []  # (start_char, seg_start_seconds)
    pos = 0
    for seg in segments:
        seg_text = seg.get("text", "").replace("\n", " ")
        offsets.append((pos, seg.get("start", 0)))
        pos += len(seg_text) + 1  # +1 for the joining space

    def _time_at(char_pos: int) -> float:
        lo, hi, best = 0, len(offsets) - 1, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if offsets[mid][0] <= char_pos:
                best = offsets[mid][1]
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    parsed = _parse_proximity_query(query)
    if parsed[0] == "plain" and _looks_like_proximity(query):
        console.print(
            "[red]Error:[/red] query has proximity operators but couldn't be parsed. "
            'Use \'"a" NEAR/N "b"\', \'("a"|"b") NEAR/N "c"\', or \'"a b"~N\'.'
        )
        return False

    text_lower = text.lower()
    if parsed[0] == "near":
        _, left, right, dist = parsed
        spans = _find_grouped_near_matches(text, left, right, dist)
    elif parsed[0] == "tilde":
        _, words, dist = parsed
        spans = _find_tilde_matches(text, words, dist)
    else:
        q = parsed[1].lower()
        spans = [(m, m + len(q)) for m in _all_substring_positions(text_lower, q)]

    if not spans:
        console.print(f"[dim]No matches for '{query}' in transcript.[/dim]")
        return True

    console.print(f"[bold]{len(spans)} match(es) for '{query}':[/bold]\n")
    for start_c, end_c in spans:
        ts = _time_at(start_c)
        link = _deep_link(video_id, ts) if video_id else ""
        s = max(0, start_c - context_chars)
        e = min(len(text), end_c + context_chars)
        snippet = text[s:e].strip().replace("\n", " ")
        if s > 0:
            snippet = "..." + snippet
        if e < len(text):
            snippet = snippet + "..."
        loc = f"[link={link}]{_format_timestamp(ts)}[/link]" if link else _format_timestamp(ts)
        console.print(f"  [[cyan]{loc}[/cyan]] {snippet}\n")
    return True


def _all_substring_positions(haystack: str, needle: str) -> list:
    out, pos = [], 0
    while True:
        idx = haystack.find(needle, pos)
        if idx == -1:
            break
        out.append(idx)
        pos = idx + 1
    return out


def _freshness_hint(start_date: str, end_date: str, query: str) -> Optional[str]:
    """Return a one-line freshness hint if the query is hunting recent content.

    Filmot indexes transcripts ~24-48h behind upload, so a date window that
    reaches the last few days will miss the freshest coverage. yt-search hits
    the YouTube API live and fills that gap. Fires only on explicit recent-date
    signals to avoid noise on ordinary queries.
    """
    from datetime import date, datetime as _dt

    def _parse(d):
        if not d:
            return None
        try:
            return _dt.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            return None

    today = date.today()
    sd = _parse(start_date)
    ed = _parse(end_date)

    wants_recent = (sd is not None and (today - sd).days <= 14) or \
                   (ed is not None and (today - ed).days <= 3)
    if not wants_recent:
        return None

    days = 7
    if sd is not None:
        days = max(3, min(30, (today - sd).days + 2))
    safe_query = query.replace('"', "'")[:60]
    return (
        f"[yellow]Freshness note:[/yellow] Filmot indexes ~24-48h behind upload. "
        f"For launch-day / breaking coverage, also run: "
        f"[cyan]filmot yt-search \"{safe_query}\" --days {days}[/cyan]"
    )


def _backfill_metadata(video_id: str, title: str, channel: str) -> tuple:
    """Fetch title/channel from Filmot API when search results have Unknown metadata.

    Returns (title, channel) — using the originals if the lookup fails or isn't needed.
    """
    if title not in ("Unknown", "") and channel not in ("Unknown", ""):
        return title, channel
    try:
        client = FilmotClient()
        info = client.get_videos(video_id)
        videos = info.get("result", info.get("videos", []))
        if videos:
            v = videos[0] if isinstance(videos, list) else videos
            if title in ("Unknown", ""):
                title = v.get("title", v.get("name", title))
            if channel in ("Unknown", ""):
                channel = v.get("channelname", v.get("channeltitle", channel))
    except Exception:
        pass
    return title, channel


def _bulk_download_transcripts(
    results: dict,
    bulk_download: str,
    console,
    fallback: bool = False,
    dedupe: bool = False,
    lang: str = None,
):
    """Download transcripts from search results to library.

    Args:
        results: Search results from Filmot API
        bulk_download: Format "TOPIC:N" or just "TOPIC" (defaults to 10)
        console: Rich console for output
        fallback: If True, use AWS Transcribe when YouTube captions unavailable
        dedupe: If True, skip transcripts that are near-duplicates of already downloaded ones
        lang: Optional preferred transcript language
    """
    import hashlib
    from .library import get_library
    from .ledger import log_event
    from .transcript import (
        describe_routing_plan,
        get_transcript,
        get_transcript_with_fallback,
        routing_plan,
    )

    # Parse bulk_download format: "topic:10" or just "topic"
    if ":" in bulk_download:
        topic, count_str = bulk_download.rsplit(":", 1)
        try:
            max_count = int(count_str)
        except ValueError:
            topic = bulk_download
            max_count = 10
    else:
        topic = bulk_download
        max_count = 10
    max_count = max(0, max_count)

    videos = results.get("result", results.get("videos", results.get("items", [])))

    if not videos:
        console.print("[yellow]No videos to download.[/yellow]")
        log_event(
            "bulk_download",
            topic=topic,
            selected=0,
            saved=0,
            skipped=0,
            failed=0,
            status="empty",
        )
        return {
            "selected": 0,
            "saved": 0,
            "skipped": 0,
            "failed": 0,
        }

    # Limit to max_count
    videos_to_download = videos[:max_count]
    route_plan = routing_plan()
    click.echo(
        f"Transcript routes ({route_plan['mode']}): "
        f"{describe_routing_plan(route_plan)}; "
        f"route deadline {route_plan['route_timeout_s']:g}s",
        err=True,
    )

    library = get_library()
    success_count = 0
    skip_count = 0
    fail_count = 0
    dedupe_count = 0
    proxy_errors = False

    # Build dedup hash set from existing library entries
    seen_hashes = set()
    if dedupe:
        for t in library.list_transcripts(topic):
            data = library.get(t["video_id"], topic)
            if data:
                text = data.get("transcript", "")[:500]
                seen_hashes.add(hashlib.md5(text.encode()).hexdigest())

    console.print(f"\n[bold]Bulk downloading {len(videos_to_download)} transcripts to '{topic}'...[/bold]\n")

    for i, video in enumerate(videos_to_download, 1):
        video_id = video.get("id") or video.get("videoid")
        title = video.get("title", "Unknown")
        channel = video.get("channelname", video.get("channeltitle", video.get("channel", "Unknown")))

        # Backfill missing metadata from Filmot video API
        title, channel = _backfill_metadata(video_id, title, channel)

        # Check if already cached
        if library.exists(video_id, topic):
            log_event(
                "transcript_save", topic=topic, video_id=video_id,
                status="skipped", reason="already_exists", source="bulk_download",
            )
            skip_count += 1
            console.print(f"  [{i}/{len(videos_to_download)}] [yellow]Skip[/yellow] {video_id} - already in library")
            continue

        try:
            log_event(
                "transcript_save",
                topic=topic,
                video_id=video_id,
                status="started",
                source="bulk_download",
                index=i,
                selected=len(videos_to_download),
                lang=lang,
            )
            console.print(
                f"  [{i}/{len(videos_to_download)}] [dim]Fetching {video_id} "
                f"(transcript route ladder)...[/dim]"
            )
            route_progress = _route_progress_for(video_id)
            if fallback:
                result = get_transcript_with_fallback(
                    video_id,
                    languages=[lang] if lang else None,
                    use_aws_fallback=True,
                    aws_progress_callback=lambda stage, message, _id=video_id: (
                        click.echo(
                            f"{_id} AWS:{stage} {message}",
                            err=True,
                        )
                    ),
                    progress_callback=route_progress,
                    fresh_primary=True,
                )
            else:
                result = get_transcript(
                    video_id,
                    languages=[lang] if lang else None,
                    progress_callback=route_progress,
                    fresh_primary=True,
                )

            if any(
                str(route).startswith("pool:")
                for route in result.get("routes_tried", [])
            ) and result.get("route_errors"):
                proxy_errors = True

            if "error" in result:
                log_event(
                    "transcript_save", topic=topic, video_id=video_id,
                    status="failed", source="bulk_download",
                    error=_whole_word_summary(result["error"], 500),
                    error_type=result.get("error_type"),
                    route=result.get("route"),
                    routes_tried=result.get("routes_tried"),
                    route_errors=result.get("route_errors"),
                )
                if "proxy" in str(result["error"]).lower():
                    proxy_errors = True
                fail_count += 1
                console.print(
                    f"  [{i}/{len(videos_to_download)}] [red]Fail[/red] "
                    f"{video_id} - "
                    f"{_transcript_failure_detail(result, verbose=False)}"
                )
                continue

            full_text = result.get('full_text', '')

            # Deduplication check
            if dedupe and full_text:
                text_hash = hashlib.md5(full_text[:500].encode()).hexdigest()
                if text_hash in seen_hashes:
                    log_event(
                        "transcript_save", topic=topic, video_id=video_id,
                        status="skipped", reason="duplicate", source="bulk_download",
                    )
                    dedupe_count += 1
                    console.print(f"  [{i}/{len(videos_to_download)}] [magenta]Dedupe[/magenta] {video_id} - duplicate content")
                    continue
                seen_hashes.add(text_hash)

            # Save to library
            metadata = {
                "title": title,
                "channel": channel,
                "language": result.get("language"),
                "is_generated": result.get("is_generated"),
                "duration_seconds": result.get("duration_seconds"),
                "segment_count": result.get("segment_count"),
                "views": video.get("viewcount"),
                "route": result.get("route"),
            }
            library.save(
                video_id=video_id,
                topic=topic,
                transcript_text=full_text,
                metadata=metadata,
            )
            log_event(
                "transcript_save", topic=topic, video_id=video_id,
                status="saved", source="bulk_download",
                chars=len(full_text), route=result.get("route"),
            )
            success_count += 1
            console.print(f"  [{i}/{len(videos_to_download)}] [green]✓[/green] {video_id} - {title[:50]}...")

        except Exception as e:
            log_event(
                "transcript_save", topic=topic, video_id=video_id,
                status="failed", source="bulk_download",
                error=f"{type(e).__name__}: {_whole_word_summary(e, 500)}",
            )
            if "proxy" in str(e).lower():
                proxy_errors = True
            fail_count += 1
            console.print(
                f"  [{i}/{len(videos_to_download)}] [red]Fail[/red] "
                f"{video_id} - "
                f"{type(e).__name__}: {_whole_word_summary(e)}"
            )

    summary = f"\n[bold]Complete:[/bold] {success_count} saved, {skip_count} skipped, {fail_count} failed"
    if dedupe_count:
        summary += f", {dedupe_count} deduplicated"
    outcome = {
        "selected": len(videos_to_download),
        "saved": success_count,
        "skipped": skip_count + dedupe_count,
        "failed": fail_count,
        "deduped": dedupe_count,
    }
    log_event(
        "bulk_download",
        topic=topic,
        status=(
            "failed"
            if videos_to_download and fail_count == len(videos_to_download)
            else "completed_with_failures"
            if fail_count
            else "completed"
        ),
        routing_plan=route_plan,
        fallback=fallback,
        dedupe=dedupe,
        lang=lang,
        **outcome,
    )
    console.print(summary)
    if proxy_errors:
        console.print("[yellow]Proxy connection failures detected — your proxy may be down. Try --no-proxy to connect directly.[/yellow]")
    console.print(f"[dim]View with: filmot library list {topic}[/dim]")
    if videos_to_download and fail_count == len(videos_to_download):
        _command_error(
            f"All {fail_count} selected transcript downloads failed."
        )
    return outcome


def _display_subtitle_results(
    results: dict,
    query: str,
    full: bool = False,
    context_chars: int = 50,
    limit: Optional[int] = None,
    max_hits: Optional[int] = None,
):
    """Display subtitle search results with rich formatting.

    Args:
        results: API response dictionary
        query: Original search query
        full: If True, show all matches without truncation (useful for AI agents)
        context_chars: Characters of surrounding context per side for snippets
    """
    all_videos = _result_videos(results)
    videos = all_videos[:limit] if limit is not None else all_videos

    if not videos:
        console.print("[yellow]No results found.[/yellow]")
        return

    total = results.get("totalresultcount", len(videos))
    scope = results.get("scope", {})
    fetched = scope.get("candidates_fetched", len(all_videos))
    pages = scope.get("pages_fetched", results.get("pages_fetched", 1))
    panel_text = f"[bold]Found {total:,} API results for: {query}[/bold]"
    if fetched != total or len(videos) != fetched or pages > 1:
        panel_text += (
            f"\n[dim]Fetched {fetched:,} candidate(s) across {pages} page(s); "
            f"displaying {len(videos):,}.[/dim]"
        )
    console.print(Panel(panel_text))

    # Echo detection: flag results that share near-identical phrasing (research
    # guide §3 — convergence vs echo). Computed across the whole result set.
    echo_clusters = _detect_echo_clusters(videos)
    if echo_clusters:
        n_flagged = len(echo_clusters)
        n_groups = len(set(echo_clusters.values()))
        console.print(
            f"[yellow]⚠ Echo warning:[/yellow] {n_flagged} results across {n_groups} "
            f"cluster(s) share near-identical phrasing (possible script-copying / AI-slop). "
            f"Tagged [yellow]\\[echo#N][/yellow] below."
        )

    for i, video in enumerate(videos, 1):
        title = video.get("title", "Unknown Title")
        video_id = video.get("id", "")
        channel = video.get("channelname", "Unknown")
        channel_id = video.get("channelid", "")
        channel_subs = video.get("channelsubcount", 0)
        channel_country = video.get("channelcountryname", "")
        views = video.get("viewcount", 0)
        likes = video.get("likecount", 0)
        duration = video.get("duration", 0)
        category = video.get("category", "")
        upload_date = video.get("uploaddate", "")
        lang = video.get("lang", "")

        duration_str = _format_duration(duration)
        channel_subs_str = _format_count(channel_subs) if channel_subs else "N/A"

        # Engagement ratio (likes/views) — a visible source-triage signal, not
        # a credibility or truth score.
        eng_str = ""
        if views and views > 0:
            eng_pct = (likes / views) * 100
            eng_str = f" | [dim]Engagement:[/dim] {eng_pct:.1f}%"

        echo_tag = f" [yellow]\\[echo#{echo_clusters[i - 1]}][/yellow]" if (i - 1) in echo_clusters else ""

        # Result-level deep link jumps to the first match, not 0:00
        hits = video.get("hits", [])
        if hits:
            video_url = _deep_link(video_id, _hit_start(hits[0]))
        else:
            video_url = f"https://youtube.com/watch?v={video_id}"

        console.print(f"\n[bold cyan]{i}. {title}[/bold cyan]{echo_tag}")
        console.print(f"   [dim]Channel:[/dim] {channel} ({channel_subs_str} subs) | [dim]Country:[/dim] {channel_country}")
        console.print(f"   [dim]Views:[/dim] {views:,} | [dim]Likes:[/dim] {likes:,}{eng_str} | [dim]Duration:[/dim] {duration_str}")
        console.print(f"   [dim]Category:[/dim] {category} | [dim]Language:[/dim] {lang} | [dim]Uploaded:[/dim] {upload_date}")
        console.print(f"   [dim]Video:[/dim] {video_url}")
        console.print(f"   [dim]Channel:[/dim] https://youtube.com/channel/{channel_id}")

        # Display hits (subtitle matches) with density scoring
        if hits:
            density = 0
            density_str = ""
            is_live_stream = False
            if duration and duration > 0:
                density = len(hits) / (duration / 60)
                density_str = f" | [bold]{density:.1f}/min[/bold]"
                # Flag long live streams with very low density
                if duration > 7200 and density < 0.1:
                    is_live_stream = True

            live_tag = " [dim magenta]\\[live stream][/dim magenta]" if is_live_stream else ""
            console.print(f"   [bold green]Matches ({len(hits)}{density_str}):[/bold green]{live_tag}")

            # Deduplicate near-identical hit segments (looping live streams)
            hit_cap = max_hits if max_hits is not None else (None if full else 3)
            display_hits = hits if hit_cap is None else hits[:hit_cap]
            seen_texts = set()
            deduped_hits = []
            for hit in display_hits:
                # Build a text fingerprint from the hit content
                text_key = (hit.get("ctx_before", "") + hit.get("token", "") + hit.get("ctx_after", "")).strip()[:80]
                if not text_key:
                    # hit_format=1: use first line text
                    lines = hit.get("lines", [])
                    text_key = lines[0].get("text", "")[:80] if lines else ""
                if text_key in seen_texts:
                    continue
                seen_texts.add(text_key)
                deduped_hits.append(hit)

            for hit in deduped_hits:
                _display_hit(hit, video_id, context_chars=context_chars)

            hidden = len(display_hits) - len(deduped_hits)
            if hidden > 0:
                console.print(f"      [dim]... {hidden} duplicate segments hidden[/dim]")
            shown_cap = len(display_hits)
            if shown_cap < len(hits):
                remaining = len(hits) - shown_cap
                console.print(f"      [dim]... and {remaining} more matches[/dim]")


# ========== GET VIDEO ==========

@cli.command()
@click.argument("video_ids")
@click.option("--flags", "-f", default=None, type=int, help="Flags parameter")
@click.option("--raw", is_flag=True, help="Output raw JSON response")
def video(video_ids: str, flags: int, raw: bool):
    """Get metadata for one or more videos.

    VIDEO_IDS can be a single ID or comma-separated list.
    
    Examples:
    
        filmot video dQw4w9WgXcQ
        
        filmot video "dQw4w9WgXcQ,abc123,xyz789"
    """
    try:
        client = FilmotClient()
        with _search_status("[bold green]Fetching video metadata...", raw=raw):
            result = client.get_videos(video_ids, flags=flags)
        
        if "error" in result:
            _command_error(str(result["error"]), raw=raw, payload=result)

        from .ledger import log_event
        videos = result if isinstance(result, list) else [result]
        log_event(
            "video", video_ids=video_ids, flags=flags,
            results=len(videos), raw=raw,
        )
        
        if raw:
            click.echo(json_mod.dumps(result, indent=2, ensure_ascii=False))
            return

        if client.last_cache_hit:
            console.print("[dim]Cached response[/dim]")

        # Display formatted results
        _display_video_results(result)

    except click.exceptions.Exit:
        raise
    except click.ClickException:
        raise
    except ValueError as e:
        _command_error(f"Configuration error: {e}", raw=raw)
    except Exception as e:
        _command_error(str(e), raw=raw)


def _display_video_results(results):
    """Display video metadata in a formatted way."""
    videos = results if isinstance(results, list) else [results]
    
    if not videos:
        console.print("[yellow]No video found.[/yellow]")
        return
    
    for video in videos:
        title = video.get("title", "Unknown Title")
        video_id = video.get("id", "")
        channel = video.get("channelname", "Unknown")
        channel_id = video.get("channelid", "")
        duration = video.get("duration", 0)
        upload_date = video.get("uploaddate", "N/A")
        views = video.get("viewcount", 0)
        likes = video.get("likecount", 0)
        category = video.get("category", "")
        lang = video.get("lang", "")
        channel_subs = video.get("channelsubcount", 0)
        channel_country = video.get("channelcountryname", "")
        
        duration_str = _format_duration(duration)
        
        table = Table(title=f"[bold]{title}[/bold]", show_header=False)
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        
        table.add_row("Video ID", video_id)
        table.add_row("Duration", duration_str)
        table.add_row("Uploaded", upload_date)
        if views:
            table.add_row("Views", f"{views:,}")
        if likes:
            table.add_row("Likes", f"{likes:,}")
        if category:
            table.add_row("Category", category)
        if lang:
            table.add_row("Language", lang)
        table.add_row("Channel", channel)
        table.add_row("Channel ID", channel_id)
        if channel_subs:
            table.add_row("Channel Subscribers", _format_count(channel_subs))
        if channel_country:
            table.add_row("Channel Country", channel_country)
        table.add_row("Video URL", f"https://youtube.com/watch?v={video_id}")
        table.add_row("Channel URL", f"https://youtube.com/channel/{channel_id}")

        console.print(table)

        # Warn if views/likes are missing (API returns sparse data for some endpoints)
        if not views and not likes and not category:
            console.print("[dim]Note: Views, likes, and category not available from this endpoint. Use 'filmot search' for full metadata.[/dim]")


# ========== SEARCH CHANNELS ==========

@cli.command()
@click.argument("term")
@click.option("--raw", is_flag=True, help="Output raw JSON response")
def channels(term: str, raw: bool):
    """Search for YouTube channels by name or handle.
    
    Examples:
    
        filmot channels mrbeast
        
        filmot channels "Linus Tech Tips"
    """
    try:
        client = FilmotClient()
        with _search_status(
            f"[bold green]Searching channels for '{term}'...", raw=raw
        ):
            result = client.search_channels(term)
        
        if "error" in result:
            _command_error(str(result["error"]), raw=raw, payload=result)

        from .ledger import log_event
        log_event(
            "channels", query=term,
            results=len(_channel_candidates(result)), raw=raw,
        )
        
        if raw:
            click.echo(json_mod.dumps(result, indent=2, ensure_ascii=False))
            return

        if client.last_cache_hit:
            console.print("[dim]Cached response[/dim]")

        # Display formatted results
        _display_channel_results(result, term)
        
    except click.exceptions.Exit:
        raise
    except click.ClickException:
        raise
    except ValueError as e:
        _command_error(f"Configuration error: {e}", raw=raw)
    except Exception as e:
        _command_error(str(e), raw=raw)


def _display_channel_results(results: dict, term: str):
    """Display channel search results in a formatted table."""
    # API returns a list directly
    channels = results if isinstance(results, list) else results.get("channels", results.get("items", []))
    
    if not channels:
        console.print("[yellow]No channels found.[/yellow]")
        return
    
    console.print(Panel(f"[bold]Found {len(channels)} channels matching: {term}[/bold]"))
    
    table = Table()
    table.add_column("#", style="dim")
    table.add_column("Channel Name", style="cyan")
    table.add_column("Handle", style="green")
    table.add_column("Subscribers", justify="right", style="yellow")
    table.add_column("Total Views", justify="right")
    table.add_column("Channel ID", style="dim")
    
    for i, channel in enumerate(channels[:20], 1):  # Limit to first 20
        name = channel.get("label", "Unknown")
        channel_id = channel.get("value", "")
        handle = channel.get("newshortname", "") or ""
        subs = channel.get("subcountp", _format_count(channel.get("subcount", 0)))
        views = channel.get("viewcountp", _format_count(channel.get("viewcount", 0)))
        
        # Add @ prefix to handle if not already there
        if handle and not handle.startswith("@"):
            handle = f"@{handle}"
        
        table.add_row(str(i), name, handle, str(subs), str(views), channel_id)
    
    console.print(table)
    
    # Show channel URLs for the top results
    console.print("\n[dim]Top channel URLs:[/dim]")
    for i, channel in enumerate(channels[:5], 1):
        channel_id = channel.get("value", "")
        name = channel.get("label", "Unknown")
        console.print(f"   {i}. [cyan]{name}[/cyan]: https://youtube.com/channel/{channel_id}")
    
    if len(channels) > 20:
        console.print(f"\n[dim]... and {len(channels) - 20} more results[/dim]")


# ========== CONFIG ==========

@cli.command()
def config():
    """Show current configuration status."""
    from .config import API_KEY, API_HOST, BASE_URL
    from .cache import get_cache
    from .ledger import log_event
    from .rate_limiter import get_rate_limiter
    
    table = Table(title="Filmot CLI Configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")
    
    # Mask the API key for security
    masked_key = f"{API_KEY[:8]}...{API_KEY[-4:]}" if len(API_KEY) > 12 else "***"
    
    table.add_row("API Host", API_HOST)
    table.add_row("API Key", masked_key)
    table.add_row("Base URL", BASE_URL)
    
    # Cache stats
    cache = get_cache()
    stats = cache.stats()
    table.add_row("Cache Entries", str(stats["valid_entries"]))
    table.add_row("Cache Size", f"{stats['size_mb']} MB")
    
    # Rate limiter stats
    rl = get_rate_limiter()
    rl_stats = rl.stats()
    table.add_row("Requests Made", str(rl_stats["total_requests"]))

    log_event(
        "config",
        api_host=API_HOST,
        cache_entries=stats["valid_entries"],
        cache_size_mb=stats["size_mb"],
        requests_made=rl_stats["total_requests"],
    )
    console.print(table)


# ========== INTERACTIVE MODE ==========

@cli.command()
def interactive():
    """Start interactive REPL mode."""
    from .interactive import start_repl
    from .ledger import log_event

    log_event("interactive", status="started")
    start_repl()


# ========== CACHE MANAGEMENT ==========

@cli.command()
@click.option("--clear", is_flag=True, help="Clear all cache entries")
@click.option("--clear-expired", is_flag=True, help="Clear only expired entries")
def cache(clear: bool, clear_expired: bool):
    """Manage the response cache."""
    from .cache import get_cache
    from .ledger import log_event
    
    cache_instance = get_cache()
    
    if clear:
        count = cache_instance.clear()
        log_event("cache", action="clear", removed=count)
        console.print(f"[green]✓ Cleared {count} cache entries[/green]")
    elif clear_expired:
        count = cache_instance.clear_expired()
        log_event("cache", action="clear_expired", removed=count)
        console.print(f"[green]✓ Cleared {count} expired entries[/green]")
    else:
        stats = cache_instance.stats()
        log_event("cache", action="status", **stats)
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


# ========== EXPORT ==========

@cli.command()
@click.argument("query")
@click.option("--output", "-o", required=True, help="Output file path")
@click.option("--format", "-f", "fmt", type=click.Choice(["json", "csv"]), default="json", help="Export format")
@click.option("--pages", "-p", default=1, type=click.IntRange(1), help="Number of pages to fetch (default 1)")
@click.option("--detailed", is_flag=True, help="Export detailed hits (one row per hit for CSV)")
@click.option("--lang", "-l", default=None, help="Language code")
@click.option("--min-views", default=None, type=click.IntRange(0), help="Minimum view count")
@click.option("--category", "-c", default=None, help="Video category")
def export(query: str, output: str, fmt: str, pages: int, detailed: bool,
           lang: str, min_views: int, category: str):
    """Export search results to file.
    
    Examples:
    
        filmot export "machine learning" -o results.json
        
        filmot export "python tutorial" -o data.csv --format csv --pages 3
        
        filmot export "AI" -o hits.csv --format csv --detailed
    """
    from .export import export_json, export_csv, export_hits_detailed
    
    client = FilmotClient()
    
    with console.status(f"[bold green]Fetching {pages} page(s) for '{query}'..."):
        if pages == 1:
            results = client.search_subtitles(
                query=query, lang=lang, min_views=min_views, category=category
            )
        else:
            results = client.search_subtitles_all(
                query=query, max_pages=pages, 
                lang=lang, min_views=min_views, category=category
            )
    
    if "error" in results:
        _command_error(str(results["error"]))
    
    try:
        if fmt == "json":
            path = export_json(results, output)
        elif detailed:
            path = export_hits_detailed(results, output)
        else:
            path = export_csv(results, output)
        
        video_count = len(results.get("result", []))
        from .ledger import log_event
        log_event(
            "export", query=query, output=str(path), format=fmt,
            detailed=detailed, requested_pages=pages,
            pages_fetched=results.get("pages_fetched", 1),
            results=video_count,
        )
        console.print(f"[green]✓ Exported {video_count} videos to: {path}[/green]")
    except Exception as e:
        _command_error(f"Export failed: {e}")


# ========== BATCH OPERATIONS ==========

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
    from .batch import BatchProcessor, create_batch_file_template
    from pathlib import Path
    
    if not Path(file).exists():
        _command_error(
            f"File not found: {file}. Use `filmot batch-template` to create a sample."
        )
    
    client = FilmotClient()
    processor = BatchProcessor(client)
    
    try:
        queries = processor.load_queries_from_file(file)
    except Exception as e:
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
        
        results = processor.process_queries(queries, on_progress)
    
    # Show summary
    stats = processor.stats()
    from .ledger import log_event
    log_event(
        "batch", file=file, output=output, format=fmt,
        queries=len(queries), successful=stats["successful"],
        failed=stats["failed"], total_results=stats["total_results"],
        status=(
            "failed"
            if queries and stats["successful"] == 0 and stats["failed"]
            else "completed_with_failures"
            if stats["failed"]
            else "completed"
        ),
    )
    console.print(f"\n[bold]Results:[/bold]")
    console.print(f"  Successful: [green]{stats['successful']}[/green]")
    console.print(f"  Failed: [red]{stats['failed']}[/red]")
    console.print(f"  Total results: {stats['total_results']}")
    console.print(f"  Avg time: {stats['avg_duration_ms']:.0f}ms")
    
    # Export if output specified
    if output:
        try:
            path = processor.export_results(output, fmt)
            console.print(f"\n[green]✓ Results exported to: {path}[/green]")
        except Exception as e:
            _command_error(f"Export failed: {e}")

    if queries and stats["successful"] == 0 and stats["failed"]:
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
    from .ledger import log_event
    log_event(
        "batch_template",
        output=str(path),
        format=fmt,
    )
    console.print(f"[green]✓ Created template: {path}[/green]")


# ========== WATCHLIST ==========

@cli.group()
def watchlist():
    """Manage your video watchlist."""
    pass


@watchlist.command("list")
@click.option("--unwatched", is_flag=True, help="Show only unwatched videos")
@click.option("--tag", default=None, help="Filter by tag")
def watchlist_list(unwatched: bool, tag: str):
    """Show all watchlist items."""
    from .watchlist import get_watchlist
    from .ledger import log_event
    
    wl = get_watchlist()
    watched_filter = False if unwatched else None
    items = wl.get_watchlist(tag=tag, watched=watched_filter)
    log_event(
        "watchlist_list",
        unwatched=unwatched,
        tag=tag,
        results=len(items),
    )

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
    
    for i, item in enumerate(items, 1):
        status = "✓" if item.get("watched") else ""
        added = item.get("added_at", "")[:10]
        
        table.add_row(
            str(i),
            item.get("title", "")[:45],
            item.get("channel_name", "")[:20],
            status,
            added,
            item.get("video_id", "")
        )
    
    console.print(table)
    
    stats = wl.stats()
    console.print(f"\n[dim]Total: {stats['total_videos']} | Watched: {stats['watched']} | Unwatched: {stats['unwatched']}[/dim]")


@watchlist.command("add")
@click.argument("video_id")
@click.option("--notes", "-n", default="", help="Notes about the video")
def watchlist_add(video_id: str, notes: str):
    """Add a video to watchlist by ID."""
    from .watchlist import get_watchlist
    from .ledger import log_event
    
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
        log_event(
            "watchlist_add",
            video_id=video_id,
            status="failed",
            error=str(detail),
        )
        _command_error(f"Could not fetch video {video_id}: {detail}")
    
    video = result[0] if isinstance(result, list) else result
    video["id"] = video_id  # Ensure ID is set
    
    wl = get_watchlist()
    added = wl.add_video(video, notes)
    log_event(
        "watchlist_add",
        video_id=video_id,
        status="added" if added else "already_present",
        has_notes=bool(notes),
    )
    if added:
        console.print(f"[green]✓ Added: {video.get('title', video_id)}[/green]")
    else:
        console.print("[yellow]Video already in watchlist.[/yellow]")


@watchlist.command("remove")
@click.argument("video_id")
def watchlist_remove(video_id: str):
    """Remove a video from watchlist."""
    from .watchlist import get_watchlist
    from .ledger import log_event
    
    wl = get_watchlist()
    removed = wl.remove_video(video_id)
    log_event(
        "watchlist_remove",
        video_id=video_id,
        removed=removed,
    )
    if removed:
        console.print(f"[green]✓ Removed video: {video_id}[/green]")
    else:
        console.print(f"[yellow]Video not found in watchlist.[/yellow]")


@watchlist.command("watched")
@click.argument("video_id")
def watchlist_watched(video_id: str):
    """Mark a video as watched."""
    from .watchlist import get_watchlist
    from .ledger import log_event
    
    wl = get_watchlist()
    updated = wl.mark_watched(video_id, True)
    log_event(
        "watchlist_watched",
        video_id=video_id,
        updated=updated,
    )
    if updated:
        console.print(f"[green]✓ Marked as watched[/green]")
    else:
        console.print(f"[yellow]Video not found in watchlist.[/yellow]")


@watchlist.command("clear")
@click.confirmation_option(prompt="Are you sure you want to clear the watchlist?")
def watchlist_clear():
    """Clear all watchlist items."""
    from .watchlist import get_watchlist
    from .ledger import log_event
    
    wl = get_watchlist()
    count = wl.clear_watchlist()
    log_event("watchlist_clear", removed=count)
    console.print(f"[green]✓ Cleared {count} items from watchlist[/green]")


# ========== PAGINATED SEARCH ==========

@cli.command("search-all")
@click.argument("query")
@click.option("--pages", "-p", default=5, type=click.IntRange(1), help="Max pages to fetch (default 5)")
@click.option("--max-results", default=None, type=click.IntRange(1), help="Max total results")
@click.option("--lang", "-l", default=None, help="Language code")
@click.option("--min-views", default=None, type=click.IntRange(0), help="Minimum view count")
@click.option("--category", "-c", default=None, help="Video category")
@click.option("--output", "-o", default=None, help="Export results to file")
@click.option("--format", "-f", "fmt", type=click.Choice(["json", "csv"]), default="json")
def search_all(query: str, pages: int, max_results: int, lang: str, 
               min_views: int, category: str, output: str, fmt: str):
    """Search with automatic pagination to fetch multiple pages.
    
    Examples:
    
        filmot search-all "machine learning" --pages 10
        
        filmot search-all "tutorial" --max-results 200 -o results.json
    """
    from .export import export_json, export_csv
    from .ledger import log_event
    
    try:
        client = FilmotClient()

        with console.status(f"[bold green]Fetching up to {pages} pages for '{query}'..."):
            results = client.search_subtitles_all(
                query=query,
                max_pages=pages,
                max_results=max_results,
                lang=lang,
                min_views=min_views,
                category=category
            )

        if "error" in results:
            log_event(
                "search_all",
                query=query,
                lang=lang,
                category=category,
                min_views=min_views,
                requested_pages=pages,
                max_results=max_results,
                output=output,
                format=fmt,
                status="failed",
                failure_stage="api",
                error=str(results["error"]),
            )
            _command_error(str(results["error"]))
    except click.ClickException:
        raise
    except Exception as e:
        log_event(
            "search_all",
            query=query,
            lang=lang,
            category=category,
            min_views=min_views,
            requested_pages=pages,
            max_results=max_results,
            output=output,
            format=fmt,
            status="failed",
            failure_stage="request",
            error=f"{type(e).__name__}: {e}",
        )
        _command_error(str(e))

    videos = results.get("result", [])
    total = results.get("totalresultcount", len(videos))
    pages_fetched = results.get("pages_fetched", 1)

    exported_path = None
    if output:
        try:
            if fmt == "json":
                exported_path = export_json(results, output)
            else:
                exported_path = export_csv(results, output)
        except Exception as e:
            log_event(
                "search_all",
                query=query,
                lang=lang,
                category=category,
                min_views=min_views,
                requested_pages=pages,
                pages_fetched=pages_fetched,
                max_results=max_results,
                api_total=total,
                results=len(videos),
                partial=results.get("partial", False),
                page_error=results.get("page_error"),
                output=output,
                format=fmt,
                status="failed",
                failure_stage="export",
                error=f"{type(e).__name__}: {e}",
            )
            _command_error(f"Export failed: {e}")

    log_event(
        "search_all",
        query=query,
        lang=lang,
        category=category,
        min_views=min_views,
        requested_pages=pages,
        pages_fetched=pages_fetched,
        max_results=max_results,
        api_total=total,
        results=len(videos),
        partial=results.get("partial", False),
        page_error=results.get("page_error"),
        output=output,
        exported_path=str(exported_path) if exported_path else None,
        format=fmt,
        status="partial" if results.get("partial") else "completed",
    )

    console.print(Panel(
        f"[bold]Fetched {len(videos)} of {total:,} total results ({pages_fetched} pages)[/bold]"
    ))
    if results.get("partial"):
        console.print(
            "[yellow]Partial result set:[/yellow] pagination stopped early"
            + (
                f" ({_whole_word_summary(results['page_error'])})"
                if results.get("page_error")
                else ""
            )
            + "."
        )

    if output:
        console.print(f"[green]✓ Exported to: {exported_path}[/green]")
    else:
        # Display summary
        _display_subtitle_results(results, query)


# ========== TRANSCRIPT DOWNLOAD ==========

@cli.command("transcript")
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
    from .transcript import (
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
        from .ledger import log_event
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
        from .ledger import log_event
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
    if source == 'aws_transcribe':
        _diagnostic(
            f"[cyan]Transcribed via AWS Transcribe "
            f"(language: {result.get('language', 'unknown')})[/cyan]",
            raw=raw,
        )
        if timestamps:
            _diagnostic(
                "[yellow]AWS fallback does not provide segment timestamps; "
                "showing transcript text instead.[/yellow]",
                raw=raw,
            )
            timestamps = False

    from .ledger import log_event
    log_event(
        "transcript",
        topic=save_to,
        video_id=result.get("video_id", video_id),
        grep=grep,
        save_to=save_to,
        output=output,
        raw=raw,
        full=full,
        timestamps=timestamps,
        chunk=chunk,
        fallback=fallback,
        status="fetched",
        source=source,
        language=result.get("language"),
        chars=len(result.get("full_text", "")),
        route=result.get("route"),
        routes_tried=result.get("routes_tried"),
        routing_plan=route_plan,
    )

    # Grep mode: search within the transcript and print timestamped matches, then stop
    if grep:
        if not _grep_transcript(result, grep):
            raise click.exceptions.Exit(2)
        return

    # Save to library if --save-to specified
    if save_to:
        try:
            from .library import get_library
            library = get_library()

            # Check if already cached
            if library.exists(result['video_id'], save_to):
                _diagnostic(
                    f"[yellow]Already in library: "
                    f"{save_to}/{result['video_id']}[/yellow]",
                    raw=raw,
                )
                log_event(
                    "transcript_save",
                    topic=save_to,
                    video_id=result["video_id"],
                    status="skipped",
                    reason="already_exists",
                )
            else:
                # Fetch video metadata so library entries have title/channel
                video_title = "Unknown"
                video_channel = "Unknown"
                try:
                    client = FilmotClient()
                    video_info = client.get_videos(result['video_id'])
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
                    "language": result.get("language"),
                    "is_generated": result.get("is_generated"),
                    "duration_seconds": result.get("duration_seconds"),
                    "segment_count": result.get("segment_count"),
                    "route": result.get("route"),
                    "routes_tried": result.get("routes_tried"),
                }
                saved_path = library.save(
                    video_id=result['video_id'],
                    topic=save_to,
                    transcript_text=result.get('full_text', ''),
                    metadata=metadata,
                )
                log_event(
                    "transcript_save",
                    topic=save_to,
                    video_id=result["video_id"],
                    status="saved",
                    path=str(saved_path),
                    chars=len(result.get("full_text", "")),
                    source=source,
                    route=result.get("route"),
                    routes_tried=result.get("routes_tried"),
                )
                _diagnostic(
                    f"[green]✓ Saved to library: "
                    f"{save_to}/{result['video_id']}[/green]",
                    raw=raw,
                )
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            log_event(
                "transcript_save",
                topic=save_to,
                video_id=result["video_id"],
                status="failed",
                error=detail,
                route=result.get("route"),
            )
            _command_error(
                f"Could not save transcript to library: {detail}",
                raw=raw,
            )
    
    # Raw JSON output
    if raw:
        click.echo(json_mod.dumps(result, indent=2, ensure_ascii=False))
        return
    
    # Save to file
    if output:
        try:
            with open(output, 'w', encoding='utf-8') as f:
                if output.endswith('.json'):
                    json_mod.dump(result, f, indent=2)
                else:
                    # Plain text output
                    if timestamps and 'segments' in result:
                        for seg in result['segments']:
                            ts = format_timestamp(seg['start'])
                            f.write(f"[{ts}] {seg['text']}\n")
                    else:
                        f.write(result['full_text'])
            console.print(f"[green]✓ Saved transcript to: {output}[/green]")
            return
        except Exception as e:
            _command_error(f"Error saving file: {e}", raw=raw)
    
    # Full text output (for AI agents)
    if full:
        # Print metadata header
        console.print(Panel(
            f"[bold]Video ID:[/bold] {result['video_id']}\n"
            f"[bold]Language:[/bold] {result['language']} {'(auto-generated)' if result.get('is_generated') else '(manual)'}\n"
            f"[bold]Duration:[/bold] {format_timestamp(result.get('duration_seconds', 0))}\n"
            f"[bold]Segments:[/bold] {result.get('segment_count', 0)}",
            title="Transcript Info"
        ))
        
        # Print full transcript
        if chunk and 'chunks' in result:
            for c in result['chunks']:
                console.print(f"\n[bold cyan][{c['start_formatted']}][/bold cyan]")
                console.print(c['text'])
        else:
            console.print(f"\n{result['full_text']}")
        return
    
    # Default: Show with timestamps if available
    if timestamps and 'segments' in result:
        console.print(Panel(
            f"[bold]Video ID:[/bold] {result['video_id']}\n"
            f"[bold]Language:[/bold] {result['language']} {'(auto-generated)' if result.get('is_generated') else '(manual)'}\n"
            f"[bold]Duration:[/bold] {format_timestamp(result.get('duration_seconds', 0))}\n"
            f"[bold]Segments:[/bold] {result.get('segment_count', 0)}",
            title="Transcript"
        ))
        for seg in result['segments']:
            ts = format_timestamp(seg['start'])
            console.print(f"[dim][{ts}][/dim] {seg['text']}")
    elif chunk and 'chunks' in result:
        console.print(Panel(
            f"[bold]Video ID:[/bold] {result['video_id']}\n"
            f"[bold]Language:[/bold] {result['language']}\n"
            f"[bold]Chunks:[/bold] {len(result['chunks'])} × {result['chunk_minutes']} min",
            title="Chunked Transcript"
        ))
        for c in result['chunks']:
            console.print(f"\n[bold yellow]━━━ {c['start_formatted']} ━━━[/bold yellow]")
            console.print(c['text'][:500] + "..." if len(c['text']) > 500 else c['text'])
    else:
        # Just show summary and excerpt
        console.print(Panel(
            f"[bold]Video ID:[/bold] {result['video_id']}\n"
            f"[bold]Language:[/bold] {result['language']} {'(auto-generated)' if result.get('is_generated') else '(manual)'}\n"
            f"[bold]Duration:[/bold] {format_timestamp(result.get('duration_seconds', 0))}\n"
            f"[bold]Characters:[/bold] {len(result.get('full_text', ''))}",
            title="Transcript Summary"
        ))
        text = result.get('full_text', '')
        if len(text) > 1000:
            console.print(f"\n{text[:1000]}...\n")
            console.print("[dim]Use --full to see complete transcript[/dim]")
        else:
            console.print(f"\n{text}")


@cli.command()
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
    from .transcript import search_in_transcript
    
    with console.status(f"[bold green]Searching transcript for '{query}'..."):
        languages = [lang] if lang else None
        result = search_in_transcript(video_id, query, context, languages)
    
    if "error" in result:
        from .ledger import log_event
        log_event(
            "transcript_search", video_id=video_id, query=query,
            lang=lang, context=context, status="failed",
            error=str(result["error"]),
        )
        _command_error(str(result["error"]))

    from .ledger import log_event
    log_event(
        "transcript_search", video_id=video_id, query=query,
        lang=lang, context=context, status="completed",
        matches=result.get("match_count", 0),
    )
    
    console.print(Panel(
        f"[bold]Video ID:[/bold] {result['video_id']}\n"
        f"[bold]Query:[/bold] {result['query']}\n"
        f"[bold]Matches:[/bold] {result['match_count']}",
        title="Transcript Search"
    ))
    
    if result['match_count'] == 0:
        console.print("[yellow]No matches found in transcript.[/yellow]")
        return
    
    for i, match in enumerate(result['matches'], 1):
        console.print(f"\n[bold cyan]Match {i} @ {match['timestamp']}[/bold cyan]")
        # Highlight the query in context
        highlighted = match['context'].replace(
            query, f"[bold red]{query}[/bold red]"
        ).replace(
            query.lower(), f"[bold red]{query.lower()}[/bold red]"
        ).replace(
            query.upper(), f"[bold red]{query.upper()}[/bold red]"
        ).replace(
            query.capitalize(), f"[bold red]{query.capitalize()}[/bold red]"
        )
        console.print(f"  {highlighted}")


# ========== YOUTUBE API SEARCH ==========

@cli.command("yt-search")
@click.argument("query")
@click.option("--days", "-d", default=7, type=click.IntRange(1), help="Search videos from last N days (default: 7)")
@click.option("--max-results", "-n", default=25, type=click.IntRange(1, 50), help="Maximum results (default: 25, max: 50)")
@click.option("--order", "-o", default="date", 
              type=click.Choice(["date", "relevance", "viewCount", "rating", "title"]), 
              help="Sort order")
@click.option("--published-after", default=None, help="Only videos after this date (YYYY-MM-DD)")
@click.option("--published-before", default=None, help="Only videos before this date (YYYY-MM-DD)")
@click.option("--channel-id", default=None, help="Filter by channel ID")
@click.option("--region", default=None, help="Region code (e.g., US, GB, DE)")
@click.option("--lang", "-l", default=None, help="Relevance language code (e.g., en, es, de)")
@click.option("--safe-search", default=None, 
              type=click.Choice(["none", "moderate", "strict"]),
              help="Safe search filtering")
@click.option("--caption", default=None,
              type=click.Choice(["any", "closedCaption", "none"]),
              help="Filter by caption availability")
@click.option("--category", default=None, help="YouTube category ID")
@click.option("--definition", default=None,
              type=click.Choice(["any", "high", "standard"]),
              help="Video definition (high=HD, standard=SD)")
@click.option("--dimension", default=None,
              type=click.Choice(["any", "2d", "3d"]),
              help="Video dimension")
@click.option("--duration", default=None,
              type=click.Choice(["any", "short", "medium", "long"]),
              help="Duration: short (<4m), medium (4-20m), long (>20m)")
@click.option("--embeddable", is_flag=True, help="Only embeddable videos")
@click.option("--license", "video_license", default=None,
              type=click.Choice(["any", "creativeCommon", "youtube"]),
              help="Video license type")
@click.option("--syndicated", is_flag=True, help="Only syndicated videos")
@click.option("--type", "video_type", default=None,
              type=click.Choice(["any", "episode", "movie"]),
              help="Video type")
@click.option("--event-type", default=None,
              type=click.Choice(["completed", "live", "upcoming"]),
              help="Live stream event type")
@click.option("--location", default=None, help="Lat,Long coordinates (e.g., 37.42,-122.08)")
@click.option("--location-radius", default=None, help="Radius around location (e.g., 50km, 100mi)")
@click.option("--topic-id", default=None, help="Freebase topic ID")
@click.option("--transcript", "-t", is_flag=True, help="Also fetch and search transcript content")
@click.option("--transcript-query", default=None, help="Different query for transcript search")
@click.option("--show-description", is_flag=True, help="Show video descriptions")
def yt_search(query: str, days: int, max_results: int, order: str, 
              published_after: str, published_before: str, channel_id: str,
              region: str, lang: str, safe_search: str, caption: str,
              category: str, definition: str, dimension: str, duration: str,
              embeddable: bool, video_license: str, syndicated: bool,
              video_type: str, event_type: str, location: str, 
              location_radius: str, topic_id: str, transcript: bool, 
              transcript_query: str, show_description: bool):
    """Search YouTube directly for recent videos (bypasses Filmot).
    
    Use this when searching for very recent content that Filmot 
    may not have indexed yet. Requires YOUTUBE_API_KEY in .env file.
    
    Examples:
    
    \b
        filmot yt-search "moltbook" --days 7
        
        filmot yt-search "clawdbot" --days 3 --order relevance
        
        filmot yt-search "AI agents" --duration long --definition high
        
        filmot yt-search "tutorial" --caption closedCaption --lang en
        
        filmot yt-search "news" --region US --safe-search strict
        
        filmot yt-search "live coding" --event-type live
        
        filmot yt-search "AI" --license creativeCommon
        
        filmot yt-search "tech" --transcript --transcript-query "security"
    """
    try:
        from .youtube_search import search_recent, format_duration, validate_youtube_api
        
        validate_youtube_api()
        
        # Convert date strings to ISO format if provided
        pub_after = None
        pub_before = None
        if published_after:
            pub_after = f"{published_after}T00:00:00Z"
        if published_before:
            pub_before = f"{published_before}T23:59:59Z"
        
        with console.status(f"[bold green]Searching YouTube for '{query}' (last {days} days)..."):
            results = search_recent(
                query=query,
                days_back=days,
                max_results=max_results,
                order=order,
                published_after=pub_after,
                published_before=pub_before,
                channel_id=channel_id,
                region_code=region,
                relevance_language=lang,
                safe_search=safe_search,
                video_caption=caption,
                video_category_id=category,
                video_definition=definition,
                video_dimension=dimension,
                video_duration=duration,
                video_embeddable="true" if embeddable else None,
                video_license=video_license,
                video_syndicated="true" if syndicated else None,
                video_type=video_type,
                event_type=event_type,
                location=location,
                location_radius=location_radius,
                topic_id=topic_id,
            )
        
        from .ledger import log_event
        log_event(
            "yt-search", query=query, days=days, order=order,
            lang=lang, region=region, results=len(results) if results else 0,
            max_results=max_results, published_after=published_after,
            published_before=published_before, channel_id=channel_id,
            safe_search=safe_search, caption=caption, category=category,
            definition=definition, dimension=dimension, duration=duration,
            embeddable=embeddable, license=video_license,
            syndicated=syndicated, video_type=video_type,
            event_type=event_type, location=location,
            location_radius=location_radius, topic_id=topic_id,
            transcript=transcript, transcript_query=transcript_query,
        )

        if not results:
            console.print(f"[yellow]No videos found for '{query}' in the last {days} days.[/yellow]")
            return

        # Build summary line
        filters = []
        if region:
            filters.append(f"Region: {region}")
        if lang:
            filters.append(f"Lang: {lang}")
        if duration:
            filters.append(f"Duration: {duration}")
        if definition:
            filters.append(f"Definition: {definition}")
        if caption:
            filters.append(f"Caption: {caption}")
        if event_type:
            filters.append(f"Event: {event_type}")
        
        filter_text = " | ".join(filters) if filters else ""
        
        console.print(Panel(
            f"[bold]Found {len(results)} videos for:[/bold] {query}\n"
            f"[bold]Period:[/bold] Last {days} days | [bold]Order:[/bold] {order}"
            + (f"\n[bold]Filters:[/bold] {filter_text}" if filter_text else ""),
            title="YouTube Search Results"
        ))
        
        for i, video in enumerate(results, 1):
            duration_str = format_duration(video.get('duration', ''))
            views = f"{video.get('views', 0):,}"
            published = video.get('published_at', '')[:10]
            
            console.print(f"\n[bold cyan]{i}. {video['title']}[/bold cyan]")
            console.print(f"   Channel: [green]{video['channel_title']}[/green]")
            console.print(f"   Views: {views} | Duration: {duration_str} | Published: {published}")
            video_url = video.get("url") or (
                f"https://youtube.com/watch?v={video.get('video_id', '')}"
            )
            console.print(f"   [link={video_url}]{video_url}[/link]")
            
            if show_description and video.get('description'):
                desc = video['description'][:200]
                if len(video.get('description', '')) > 200:
                    desc += "..."
                console.print(f"   [dim]{desc}[/dim]")
            
            # Optionally fetch and search transcript
            if transcript:
                from .transcript import get_transcript, search_in_transcript
                
                search_term = transcript_query or query
                with console.status(f"   Fetching transcript..."):
                    try:
                        result = search_in_transcript(video['video_id'], search_term)
                        if result.get('match_count', 0) > 0:
                            console.print(f"   [bold green]✓ Found {result['match_count']} transcript matches for '{search_term}'[/bold green]")
                            for match in result['matches'][:3]:
                                console.print(f"      [{match['timestamp']}] ...{match['context'][:100]}...")
                        else:
                            console.print(f"   [dim]No transcript matches for '{search_term}'[/dim]")
                    except Exception as e:
                        console.print(f"   [dim]Transcript unavailable[/dim]")
                        
    except ValueError as e:
        _command_error(
            f"Configuration error: {e}. Add YOUTUBE_API_KEY to your .env file."
        )
    except Exception as e:
        _command_error(str(e))


# ========== TRANSCRIPT LIBRARY ==========

@cli.group()
def library():
    """Manage local transcript library.
    
    The library stores transcripts organized by topic/keyword for 
    building curated knowledge bases.
    
    Examples:
    
    \b
        filmot library list                    # List all topics
        filmot library list prompt-injection   # List transcripts in topic
        filmot library search "attack"         # Search across all transcripts
        filmot library context prompt-injection # Get all text for LLM context
        filmot library stats                   # Show library statistics
    """
    pass


@library.command("list")
@click.argument("topic", required=False)
def library_list(topic: str):
    """List topics or transcripts in a topic.
    
    Without arguments, lists all topics.
    With a topic name, lists all transcripts in that topic.
    """
    from .library import get_library
    from .ledger import log_event
    lib = get_library()
    
    if topic:
        # List transcripts in topic
        transcripts = lib.list_transcripts(topic)
        log_event(
            "library_list", topic=topic, scope="topic",
            transcripts=len(transcripts),
        )
        if not transcripts:
            console.print(f"[yellow]No transcripts in topic: {topic}[/yellow]")
            return
        
        table = Table(title=f"Transcripts in '{topic}'")
        table.add_column("Video ID", style="cyan")
        table.add_column("Title", style="white", max_width=50)
        table.add_column("Channel", style="green")
        table.add_column("Size", style="dim")
        table.add_column("Saved", style="dim")
        
        for t in transcripts:
            size = f"{t['char_count']:,} chars"
            saved = t['saved_at'][:10] if t.get('saved_at') else "Unknown"
            title = t.get('title', 'Unknown')
            if len(title) > 47:
                title = title[:47] + "..."
            table.add_row(t['video_id'], title, t.get('channel', 'Unknown'), size, saved)
        
        console.print(table)
        console.print(f"\n[dim]Total: {len(transcripts)} transcripts[/dim]")
    else:
        # List all topics
        topics = lib.list_topics()
        log_event("library_list", scope="all", topics=len(topics))
        if not topics:
            console.print("[yellow]Library is empty. Use 'filmot transcript VIDEO_ID --save-to TOPIC' to add.[/yellow]")
            return
        
        table = Table(title="Transcript Library")
        table.add_column("Topic", style="cyan")
        table.add_column("Transcripts", style="white", justify="right")
        
        for t in topics:
            table.add_row(t['topic'], str(t['count']))
        
        console.print(table)
        total = sum(t['count'] for t in topics)
        console.print(f"\n[dim]Total: {len(topics)} topics, {total} transcripts[/dim]")


@library.command("search")
@click.argument("query")
@click.option("--topic", "-t", default=None, help="Limit search to specific topic")
@click.option("--substring", is_flag=True, help="Use substring matching instead of word-boundary matching")
def library_search(query: str, topic: str, substring: bool):
    """Search for text across saved transcripts.

    Uses word-boundary matching by default (searching "ore" won't match "more").
    Use --substring for the old behavior.
    """
    from .library import get_library
    from .ledger import log_event
    lib = get_library()

    with console.status(f"Searching library for '{query}'..."):
        results = lib.search(query, topic=topic, substring=substring)

    # Auto-fallback: if word-boundary found nothing, retry with substring
    # Catches plurals/inflections (e.g., "laser" misses "lasers")
    used_substring = substring
    if not results and not substring:
        results = lib.search(query, topic=topic, substring=True)
        if results:
            used_substring = True
            console.print(f"[dim]No exact word matches. Showing substring matches (plurals/inflections):[/dim]")

    if not results:
        log_event(
            "library_search", topic=topic, query=query,
            substring=used_substring, sources=0, matches=0,
        )
        console.print(f"[yellow]No matches for '{query}'[/yellow]")
        if not topic:
            console.print("[dim]Try searching within a specific topic: --topic NAME[/dim]")
        return
    
    total_matches = sum(r['match_count'] for r in results)
    log_event(
        "library_search", topic=topic, query=query,
        substring=used_substring, sources=len(results), matches=total_matches,
    )
    console.print(f"\n[bold]Found {total_matches} matches across {len(results)} transcripts[/bold]\n")
    
    for r in results[:10]:  # Show top 10 transcripts
        console.print(f"[bold cyan]{r['video_id']}[/bold cyan] ({r['match_count']} matches)")
        console.print(f"  Topic: [green]{r['topic']}[/green] | {r.get('title', 'Unknown')} - {r.get('channel', 'Unknown')}")
        
        for match in r['matches'][:2]:  # Show first 2 matches per transcript
            # Highlight query in match
            console.print(f"  [dim]...{match}[/dim]")
        console.print()


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
    """
    from .library import get_library
    from .ledger import log_event
    lib = get_library()

    with console.status(f"Building context from '{topic}'..."):
        if fmt == "structured":
            context = _build_structured_context(lib, topic, max_chars)
        else:
            context = lib.get_context(topic, max_chars=max_chars)

    if not context:
        log_event(
            "library_context", topic=topic, format=fmt,
            max_chars=max_chars, chars=0, status="empty",
        )
        console.print(f"[yellow]No transcripts in topic: {topic}[/yellow]")
        return

    # Auto-generate output filename for structured format (avoids dumping large markdown to stdout)
    if fmt == "structured" and not output:
        output = f"{topic}-context.md"

    if output:
        try:
            with open(output, 'w', encoding='utf-8') as f:
                f.write(context)
            console.print(f"[green]✓ Saved context to: {output}[/green]")
            console.print(f"[dim]Size: {len(context):,} characters[/dim]")
            log_event(
                "library_context", topic=topic, format=fmt,
                max_chars=max_chars, chars=len(context), output=output,
                status="saved",
            )
        except Exception as e:
            log_event(
                "library_context", topic=topic, format=fmt,
                max_chars=max_chars, output=output, status="failed",
                error=str(e),
            )
            _command_error(f"Error saving file: {e}")
    else:
        log_event(
            "library_context", topic=topic, format=fmt,
            max_chars=max_chars, chars=len(context), status="rendered",
        )
        console.print(context)


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


@library.command("stats")
def library_stats():
    """Show library statistics."""
    from .library import get_library
    from .ledger import log_event
    lib = get_library()
    
    stats = lib.stats()
    log_event(
        "library_stats",
        topics=stats["total_topics"],
        transcripts=stats["total_transcripts"],
        size_bytes=stats["total_size_bytes"],
    )
    
    console.print(Panel(
        f"[bold]Topics:[/bold] {stats['total_topics']}\n"
        f"[bold]Transcripts:[/bold] {stats['total_transcripts']}\n"
        f"[bold]Total Size:[/bold] {stats['total_size_mb']} MB",
        title="Library Statistics"
    ))
    
    if stats['topics']:
        console.print("\n[bold]By Topic:[/bold]")
        for t in stats['topics']:
            console.print(f"  {t['topic']}: {t['count']} transcripts")


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
    from .library import get_library
    from .ledger import log_event
    lib = get_library()
    
    if delete_all:
        # Delete entire topic
        count = lib.delete_topic(target)
        log_event(
            "library_delete", topic=target, target=target,
            scope="topic", deleted=count,
        )
        if count > 0:
            console.print(f"[green]✓ Deleted topic '{target}' ({count} transcripts)[/green]")
        else:
            console.print(f"[yellow]Topic not found or empty: {target}[/yellow]")
    else:
        # Delete single transcript
        deleted = lib.delete(target, topic=topic)
        log_event(
            "library_delete", topic=topic, target=target,
            scope="transcript", deleted=bool(deleted),
        )
        if deleted:
            scope = f"from {topic}" if topic else "from all topics"
            console.print(f"[green]✓ Deleted transcript {target} {scope}[/green]")
        else:
            console.print(f"[yellow]Transcript not found: {target}[/yellow]")


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
    from .ledger import log_event
    from .library import _legacy_normalize_topic, get_library

    lib = get_library()
    canonical_slug = lib._normalize_topic(topic)
    legacy_slug = _legacy_normalize_topic(topic)
    if canonical_slug == legacy_slug:
        console.print(
            f"[dim]No migration is needed: '{topic}' already uses "
            f"'{canonical_slug}'.[/dim]"
        )
        return

    source = lib.transcripts_dir / legacy_slug
    destination = lib.transcripts_dir / canonical_slug
    source_files = sorted(source.glob("*.json")) if source.exists() else []
    if not source_files:
        console.print(
            f"[dim]No legacy transcript files found at {source}.[/dim]"
        )
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

    migrated = lib.migrate_legacy_topic(topic)
    remaining = (
        len(list(source.glob("*.json")))
        if source.exists()
        else 0
    )
    log_event(
        "library_migrate_topic",
        topic=canonical_slug,
        requested_topic=topic,
        legacy_slug=legacy_slug,
        canonical_slug=canonical_slug,
        source_files=len(source_files),
        migrated=migrated,
        remaining=remaining,
    )
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
        raise click.exceptions.Exit(2)


@library.command("compare")
@click.argument("query")
@click.option("--topic", "-t", default=None, help="Limit to specific topic")
@click.option("--context", "-c", "context_chars", default=300, type=click.IntRange(0), help="Characters of context around matches (default: 300)")
@click.option("--sort", "sort_by", default="mentions", type=click.Choice(["mentions", "density"]), help="Sort by mention count (default) or density (mentions/min)")
def library_compare(query: str, topic: str, context_chars: int, sort_by: str):
    """Build a lexical concordance across sources for a term or phrase.

    This counts text matches and shows excerpts. It does not determine source
    independence, stance, agreement, contradiction, credibility, or truth.

    Examples:

    \b
        filmot library compare "dark oxygen" --topic deep-sea-mining
        filmot library compare "moratorium" --context 200
    """
    from .library import get_library
    lib = get_library()

    with console.status(f"Comparing '{query}' across sources..."):
        results = lib.search(query, topic=topic)

    # Auto-fallback: if word-boundary found nothing, retry with substring
    used_substring = False
    if not results:
        results = lib.search(query, topic=topic, substring=True)
        if results:
            used_substring = True

    if not results:
        from .ledger import log_event
        log_event(
            "library_compare", topic=topic, query=query, sort=sort_by,
            context=context_chars, sources=0, matches=0,
        )
        console.print(f"[yellow]No sources mention '{query}'[/yellow]")
        return

    # Sort by density (mentions/min) if requested
    if sort_by == "density":
        for r in results:
            data = lib.get(r['video_id'], r['topic'])
            dur = data.get("metadata", {}).get("duration_seconds", 0) if data else 0
            r['_density'] = r['match_count'] / (dur / 60) if dur and dur > 0 else 0
        results.sort(key=lambda x: x['_density'], reverse=True)

    total_mentions = sum(r['match_count'] for r in results)
    sort_label = "density (mentions/min)" if sort_by == "density" else "mention count"
    from .ledger import log_event
    log_event(
        "library_compare",
        topic=topic,
        query=query,
        sort=sort_by,
        context=context_chars,
        match_mode="substring" if used_substring else "word_boundary",
        sources=len(results),
        matches=total_mentions,
    )
    if used_substring:
        console.print(
            "[dim]No exact word matches. Showing substring matches "
            "(plurals/inflections):[/dim]"
        )
    console.print(Panel(
        f"[bold]'{query}' mentioned {total_mentions} times across {len(results)} sources[/bold]",
        title="Cross-Source Concordance"
    ))

    for r in results:
        mentions_label = "mention" if r['match_count'] == 1 else "mentions"
        # Look up duration for density calculation
        data = lib.get(r['video_id'], r['topic'])
        density_str = ""
        if data:
            duration = data.get("metadata", {}).get("duration_seconds", 0)
            if duration and duration > 0:
                density = r['match_count'] / (duration / 60)
                density_str = f" | [bold]{density:.1f}/min[/bold]"

        console.print(f"\n[bold cyan]{r.get('title', 'Unknown')}[/bold cyan]")
        console.print(f"  [dim]Channel:[/dim] {r.get('channel', 'Unknown')} | [dim]Topic:[/dim] {r['topic']} | [bold]{r['match_count']} {mentions_label}{density_str}[/bold]")
        console.print(f"  [dim]Video:[/dim] https://youtube.com/watch?v={r['video_id']}")
        if data:
            transcript = data.get("transcript", "")
            import re as _re
            pattern = (
                _re.compile(_re.escape(query.lower()))
                if used_substring
                else _re.compile(r'(?<!\w)' + _re.escape(query.lower()) + r'(?!\w)')
            )
            matches = lib._find_matches(transcript, query.lower(), context_chars=context_chars, pattern=pattern, min_gap=context_chars)
            for match in matches[:3]:
                # Highlight the query term
                highlighted = match
                for variant in [query, query.lower(), query.upper(), query.capitalize()]:
                    highlighted = highlighted.replace(variant, f"[bold yellow]{variant}[/bold yellow]")
                console.print(f"  [dim]{highlighted}[/dim]")

    # Summary
    console.print(f"\n[dim]Sources sorted by {sort_label} (highest lexical occurrence first)[/dim]")



# ========== PROBE HELPERS ==========

_PROBE_STOPWORDS = frozenset({
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'shall', 'can', 'to', 'of', 'in', 'for',
    'on', 'with', 'at', 'by', 'from', 'up', 'about', 'into', 'through',
    'during', 'before', 'after', 'above', 'below', 'between', 'out', 'off',
    'over', 'under', 'again', 'further', 'then', 'once', 'and', 'but', 'or',
    'nor', 'not', 'so', 'very', 'just', 'than', 'too', 'also', 'that',
    'this', 'these', 'those', 'it', 'its', 'he', 'she', 'they', 'them',
    'their', 'his', 'her', 'we', 'our', 'you', 'your', 'me', 'my',
    'which', 'who', 'whom', 'what', 'where', 'when', 'how', 'why', 'all',
    'each', 'every', 'both', 'few', 'more', 'most', 'other', 'some', 'such',
    'no', 'only', 'own', 'same', 'if', 'as', 'because', 'while', 'until',
    'there', 'here', 'now', 'well', 'like', 'know', 'think', 'say', 'said',
    'going', 'really', 'right', 'get', 'got', 'go', 'come', 'came', 'make',
    'made', 'take', 'took', 'see', 'seen', 'want', 'look', 'way', 'thing',
    'things', 'much', 'many', 'even', 'still', 'back', 'kind', 'mean',
    'actually', 'something', 'anything', 'nothing', 'yeah', 'okay', 'yes',
    'um', 'uh', 'oh', 'people', 'time', 'year', 'years', 'one', 'two',
    'first', 'new', 'last', 'long', 'great', 'little', 'world', 'good',
    'big', 'need', 'help', 'try', 'start', 'part', 'day', 'days', 'point',
    'fact', 'lot', 'talk', 'talking', 'tell', 'told', 'called', 'keep',
    'let', 'put', 'end', 'set', 'run', 'show', 'turn', 'move', 'play',
    'live', 'believe', 'hold', 'bring', 'happen', 'must', 'pay', 'meet',
    'include', 'continue', 'stand', 'give', 'work', 'number', 'already',
    'since', 'different', 'away', 'able', 'possible', 'another', 'quite',
    'enough', 'done', 'left', 'second', 'next', 'three', 'four', 'five',
    'high', 'important', 'hand', 'sure', 'question', 'course',
    'video', 'watch', 'subscribe', 'channel', 'comment', 'share',
    'gonna', 'dont', 'wont', 'cant', 'didnt', 'doesnt', 'isnt', 'wasnt',
    'youre', 'theyre', 'weve', 'thats', 'whats', 'heres', 'theres',
    # Common in spoken/news transcripts
    'says', 'president', 'country', 'government', 'minister', 'official',
    'officials', 'state', 'report', 'reports', 'says', 'according',
    'million', 'billion', 'percent', 'tonight', 'today', 'yesterday',
    'breaking', 'update', 'latest', 'news', 'story', 'coverage',
})


def _probe_words(text: str) -> list[str]:
    """Tokenize Latin and common non-Latin transcript scripts conservatively."""
    import unicodedata

    def script(character: str) -> str:
        codepoint = ord(character)
        if 0x4E00 <= codepoint <= 0x9FFF:
            return "han"
        if (
            0x3040 <= codepoint <= 0x30FF
            or 0x31F0 <= codepoint <= 0x31FF
        ):
            return "kana"
        if 0xAC00 <= codepoint <= 0xD7AF:
            return "hangul"
        return "other"

    words = []
    for raw in re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE):
        if raw.isascii():
            if len(raw) >= 3:
                words.append(raw)
            continue

        # Split unspaced mixed-script Japanese into useful lexical runs.
        runs = []
        current = ""
        current_script = None
        for character in raw:
            if not unicodedata.category(character).startswith(("L", "N")):
                continue
            character_script = script(character)
            if current and character_script != current_script:
                runs.append((current_script, current))
                current = ""
            current_script = character_script
            current += character
        if current:
            runs.append((current_script, current))

        for run_script, run in runs:
            if run_script == "han" and len(run) > 6:
                # Chinese commonly has no word separators. Repeated 2–3
                # character units provide a bounded, transparent fallback.
                words.extend(run[index:index + 3] for index in range(len(run) - 2))
            elif len(run) >= 2:
                words.append(run)
    return words


def _extract_probe_terms(texts, topic, top_n=12):
    """Extract significant terms from transcript texts for NEAR/N probing.

    Preserve transcript and sentence boundaries, then prefer terms supported by
    multiple distinct sources.  This prevents a repeated caption glitch in one
    video—or a bigram accidentally formed at a transcript boundary—from
    consuming the probe budget.
    """
    import re
    from collections import Counter, defaultdict
    from difflib import SequenceMatcher

    topic_words = set(_probe_words(topic))
    source_sentences = []
    total_words = 0
    for text in texts:
        sentences = []
        # Newlines are meaningful for timestamped/manual transcripts; terminal
        # punctuation provides the best available boundary for continuous ASR.
        for sentence in re.split(r"(?:[.!?]+|\r?\n+)", text.lower()):
            words = _probe_words(sentence)
            if words:
                sentences.append(words)
                total_words += len(words)
        source_sentences.append(sentences)

    # Adaptive thresholds: scale with corpus size
    # ~100 words -> min 2, ~1000 -> min 3, ~5000+ -> min 5
    bigram_min = max(2, min(5, total_words // 500))
    single_min = max(2, min(8, total_words // 300))

    # Bigrams: two consecutive non-stopwords
    bigrams = Counter()
    bigram_sources = defaultdict(set)
    singles = Counter()
    single_sources = defaultdict(set)
    for source_index, sentences in enumerate(source_sentences):
        for words in sentences:
            for i in range(len(words) - 1):
                w1, w2 = words[i], words[i + 1]
                if w1 in _PROBE_STOPWORDS or w2 in _PROBE_STOPWORDS:
                    continue
                if w1 in topic_words and w2 in topic_words:
                    continue
                # Artificial whitespace can change CJK query semantics. Use
                # repeated non-Latin terms as singles and pair them later.
                if not (w1.isascii() and w2.isascii()):
                    continue
                term = f"{w1} {w2}"
                bigrams[term] += 1
                bigram_sources[term].add(source_index)
            for word in words:
                if (
                    word not in _PROBE_STOPWORDS
                    and word not in topic_words
                    and len(word) >= (4 if word.isascii() else 2)
                ):
                    singles[word] += 1
                    single_sources[word].add(source_index)

    # Rank by source support before raw repetition.  Bigrams remain preferable
    # at equal support/count because they are usually more discriminating.
    candidates = []
    for term, count in bigrams.items():
        if count >= bigram_min:
            candidates.append((len(bigram_sources[term]), count, 1, term))
    for term, count in singles.items():
        if count >= single_min:
            candidates.append((len(single_sources[term]), count, 0, term))
    candidates.sort(key=lambda item: (item[0], item[2], item[1]), reverse=True)

    # If at least two sources exist, first admit cross-source terms; retain a
    # single-source fallback so small or heterogeneous corpora still probe.
    if len(texts) >= 2 and any(item[0] >= 2 for item in candidates):
        candidates = [item for item in candidates if item[0] >= 2] + [
            item for item in candidates if item[0] < 2
        ]

    terms = []
    seen_words = set()
    for _, _, is_bigram, term in candidates:
        if not is_bigram and term in seen_words:
            continue

        # Cluster likely ASR variants (e.g. "threei atlas" /
        # "threeey atlas") and retain the higher-ranked canonical form.
        is_variant = False
        for existing in terms:
            same_tail = (
                len(term.split()) > 1
                and len(existing.split()) > 1
                and term.split()[-1] == existing.split()[-1]
            )
            if same_tail and SequenceMatcher(None, term, existing).ratio() >= 0.80:
                is_variant = True
                break
        if is_variant:
            continue

        terms.append(term)
        if is_bigram:
            seen_words.update(term.split())
        if len(terms) >= top_n:
            break

    return terms


def _find_probe_pairs(texts, terms, window_size=50, max_pairs=5):
    """Find co-occurring term pairs within text windows.

    Windows never cross transcript or sentence boundaries. Returns
    ``(term1, term2, co_window_count, supporting_source_count)``.
    """
    import re
    from collections import Counter, defaultdict

    # Build lookup: word -> set of terms it belongs to
    word_to_terms = {}
    for term in terms:
        for w in term.split():
            word_to_terms.setdefault(w, set()).add(term)

    # Slide through text in overlapping windows (inclusive of the tail)
    pair_counts = Counter()
    pair_sources = defaultdict(set)
    step = max(window_size // 2, 1)
    for source_index, text in enumerate(texts):
        for sentence in re.split(r"(?:[.!?]+|\r?\n+)", text.lower()):
            words = _probe_words(sentence)
            if not words:
                continue
            starts = list(range(0, max(len(words) - window_size, 0) + 1, step))
            tail_start = max(len(words) - window_size, 0)
            if tail_start not in starts:
                starts.append(tail_start)
            for start in starts:
                window = words[start:start + window_size]
                window_terms = set()

                for i, word in enumerate(window):
                    if word in word_to_terms:
                        for term in word_to_terms[word]:
                            parts = term.split()
                            if len(parts) == 1:
                                window_terms.add(term)
                            elif (
                                word == parts[0]
                                and i + len(parts) <= len(window)
                                and window[i:i + len(parts)] == parts
                            ):
                                window_terms.add(term)

                window_list = sorted(window_terms)
                for i in range(len(window_list)):
                    for j in range(i + 1, len(window_list)):
                        pair = (window_list[i], window_list[j])
                        pair_counts[pair] += 1
                        pair_sources[pair].add(source_index)

    # Filter: skip pairs where terms share any word (e.g., "president vladimir" + "vladimir putin")
    # Rank by specificity first (multi-word terms beat frequent generic singles), then count
    candidates = []
    for (t1, t2), count in pair_counts.items():
        if count < 2:
            continue
        if len(texts) >= 2 and len(pair_sources[(t1, t2)]) < 2:
            continue
        words_t1 = set(t1.split())
        words_t2 = set(t2.split())
        if words_t1 & words_t2:
            continue  # overlapping terms, skip
        specificity = (len(words_t1) > 1) + (len(words_t2) > 1)
        candidates.append((
            len(pair_sources[(t1, t2)]),
            specificity,
            count,
            t1,
            t2,
        ))

    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return [
        (t1, t2, count, source_count)
        for source_count, _, count, t1, t2 in candidates[:max_pairs]
    ]


def _probe_topic_words(topic: str) -> list[str]:
    """Significant topic words used to relevance-check probe results."""
    return [
        word
        for word in _probe_words(topic)
        if word not in _PROBE_STOPWORDS
        and len(word) >= (4 if word.isascii() else 2)
    ]


def _probe_hit_is_relevant(video: dict, topic_words: list[str]) -> bool:
    """True if a probe result mentions the research topic in its title or hit context."""
    if not topic_words:
        return True
    parts = [video.get("title", "")]
    for hit in video.get("hits", [])[:10]:
        parts.append(hit.get("ctx_before", ""))
        parts.append(hit.get("token", ""))
        parts.append(hit.get("ctx_after", ""))
        for line in hit.get("lines", [])[:3]:
            parts.append(line if isinstance(line, str) else str(line.get("text", "")))
    text = " ".join(parts).casefold()
    return any(_topic_token_present(text, word) for word in topic_words)


def _research_topic_tokens(topic: str) -> list[str]:
    """Meaningful Unicode topic tokens used for transparent relevance scoring."""
    tokens = re.findall(r"[^\W_]{2,}", topic.casefold(), flags=re.UNICODE)
    return [token for token in tokens if token not in _PROBE_STOPWORDS]


def _topic_token_present(text: str, token: str) -> bool:
    """Match ASCII tokens as words and non-ASCII tokens as script substrings."""
    if token.isascii():
        return bool(
            re.search(
                rf"(?<!\w){re.escape(token)}(?!\w)",
                text,
                flags=re.UNICODE,
            )
        )
    return token in text


def _research_query_ladder(topic: str) -> list[tuple[str, str]]:
    """Relationship-preserving transcript-only fallbacks, narrow to broad."""
    escaped = topic.replace('"', " ").strip()
    tokens = _research_topic_tokens(escaped)
    ladder = []
    if escaped:
        ladder.append(("exact_phrase", f'"{escaped}"'))
    proximity_tokens = tokens
    if len(tokens) >= 4 and len(tokens[0]) <= 2:
        # A leading acronym is often a modifier ("AI data center ..."); the
        # adjacent multi-word concepts carry the relationship being tested.
        proximity_tokens = tokens[1:]
    if len(proximity_tokens) >= 2:
        midpoints = [max(1, (len(proximity_tokens) + 1) // 2)]
        alternate = max(1, len(proximity_tokens) // 2)
        if alternate not in midpoints:
            midpoints.append(alternate)
        for split_index, midpoint in enumerate(midpoints):
            left = " ".join(proximity_tokens[:midpoint])
            right = " ".join(proximity_tokens[midpoint:])
            if left and right:
                stage = "proximity" if split_index == 0 else "proximity_alt"
                ladder.append((stage, f'"{left}" NEAR/25 "{right}"'))
    # De-duplicate degenerate forms while retaining stage names.
    seen = set()
    return [
        (stage, query)
        for stage, query in ladder
        if not (query in seen or seen.add(query))
    ]


def _research_hit_text(hit: dict) -> str:
    parts = [
        hit.get("ctx_before", ""),
        hit.get("token", ""),
        hit.get("ctx_after", ""),
    ]
    for line in hit.get("lines", []):
        parts.append(line if isinstance(line, str) else str(line.get("text", "")))
    return " ".join(parts).casefold()


def _candidate_assessment(video: dict, topic: str, echo_cluster=None) -> dict:
    """Return separate, explainable relevance and source-quality signals."""
    tokens = _research_topic_tokens(topic)
    title = str(video.get("title", "")).casefold()
    passages = []
    description = str(video.get("description", "")).casefold()
    if description:
        passages.append(description)
    passages.extend(
        _research_hit_text(hit)
        for hit in video.get("hits", [])[:25]
    )
    combined = " ".join([title] + passages)

    if tokens:
        token_coverage = (
            sum(_topic_token_present(combined, token) for token in tokens)
            / len(tokens)
        )
        passage_coverage = max(
            (
                sum(
                    _topic_token_present(passage, token)
                    for token in tokens
                ) / len(tokens)
                for passage in [title] + passages
            ),
            default=0.0,
        )
        title_coverage = (
            sum(_topic_token_present(title, token) for token in tokens)
            / len(tokens)
        )
    else:
        # Non-Latin topics still get relationship safety from the exact/proximity
        # fallback stage; do not pretend an ASCII tokenizer scored them.
        token_coverage = passage_coverage = title_coverage = 0.0

    views = max(int(video.get("viewcount", 0) or 0), 0)
    likes = max(int(video.get("likecount", 0) or 0), 0)
    subscribers = max(int(video.get("channelsubcount", 0) or 0), 0)
    engagement = likes / views if views else 0.0

    # A prior, not a verdict: independent visible signals remain inspectable.
    audience_signal = min(math.log10(subscribers + 1) / 6.0, 1.0)
    reach_signal = min(math.log10(views + 1) / 7.0, 1.0)
    engagement_signal = min(engagement / 0.05, 1.0)
    source_signal = (
        0.50 * audience_signal
        + 0.30 * reach_signal
        + 0.20 * engagement_signal
    )
    echo_penalty = 0.25 if echo_cluster is not None else 0.0
    density = _density(video)
    density_signal = min(math.log1p(density) / math.log(6), 1.0)
    relevance_signal = (
        0.50 * passage_coverage
        + 0.30 * title_coverage
        + 0.20 * token_coverage
    )
    balanced = (
        0.55 * relevance_signal
        + 0.25 * source_signal
        + 0.20 * density_signal
        - echo_penalty
    )
    return {
        "token_coverage": round(token_coverage, 3),
        "passage_coverage": round(passage_coverage, 3),
        "title_coverage": round(title_coverage, 3),
        "density": round(density, 3),
        "source_signal": round(source_signal, 3),
        "echo_cluster": echo_cluster,
        "balanced_score": round(balanced, 3),
        "views": views,
        "subscribers": subscribers,
        "engagement": round(engagement, 4),
    }


def _rank_research_candidates(videos: list, topic: str, sort_by: str) -> list:
    """Attach evidence and rank without conflating density/source authority."""
    echo_clusters = _detect_echo_clusters(videos)
    for index, video in enumerate(videos):
        video["_selection"] = _candidate_assessment(
            video, topic, echo_clusters.get(index)
        )

    if sort_by == "viewcount":
        key = lambda video: int(video.get("viewcount", 0) or 0)
    elif sort_by == "density":
        key = _density
    elif sort_by == "source-prior":
        key = lambda video: video["_selection"]["source_signal"]
    else:
        key = lambda video: video["_selection"]["balanced_score"]
    return sorted(videos, key=key, reverse=True)


def _research_candidate_preview(videos: list, count: int = 8) -> None:
    """Show why automatic research selected each candidate."""
    if not videos:
        return
    console.print("\n[bold]Candidate preview (relevance and source signals are separate):[/bold]")
    for index, video in enumerate(videos[:count], 1):
        selection = video.get("_selection", {})
        origin = "scout" if video.get("_from_scout") else video.get("_fallback_stage", "filmot")
        console.print(
            f"  {index}. {str(video.get('title', 'Unknown'))[:66]} "
            f"[dim]({video.get('channelname', 'Unknown')})[/dim]\n"
            f"     [dim]stage={origin}; relevance={selection.get('passage_coverage', 0):.2f}; "
            f"source-prior={selection.get('source_signal', 0):.2f}; "
            f"density={selection.get('density', 0):.2f}/min"
            f"{'; echo=' + str(selection['echo_cluster']) if selection.get('echo_cluster') else ''}[/dim]"
        )


# ========== RESEARCH COMMAND ==========

@cli.command("research")
@click.argument("topic")
@click.option("--depth", "-n", default=10, type=click.IntRange(0), show_default=True,
              help="Number of transcripts to download")
@click.option("--min-views", default=None, type=click.IntRange(0), help="Minimum view count filter")
@click.option("--lang", "-l", default=None, help="Language code (default: en)")
@click.option("--fallback", is_flag=True, help="Use AWS Transcribe fallback when captions are unavailable")
@click.option("--dedupe", is_flag=True, help="Skip duplicate transcripts")
@click.option("--min-matches", default=2, type=click.IntRange(0), show_default=True,
              help="Minimum subtitle hits per Filmot candidate (0 disables)")
@click.option(
    "--sort", "sort_by", default="balanced", show_default=True,
    type=click.Choice(["balanced", "density", "source-prior", "viewcount"]),
    help="Candidate ranking; source-prior is an unverified audience/engagement heuristic",
)
@click.option("--candidate-pages", default=3, type=click.IntRange(1, 20), show_default=True,
              help="Filmot pages to fetch before client-side ranking")
@click.option("--candidate-pool", default=150, type=click.IntRange(1), show_default=True,
              help="Maximum Filmot candidates to score")
@click.option("--accept-broad", is_flag=True,
              help="Allow a high-cardinality loose fallback after relationship-preserving stages fail")
@click.option("--broad-threshold", default=1000, type=click.IntRange(1), show_default=True,
              help="Require --accept-broad above this loose-fallback result count")
@click.option("--channel-id", default=None, help="Limit Filmot candidates to exact channel ID(s)")
@click.option("--channel", default=None, help="Resolve channel text explicitly, then fail closed")
@click.option("--channel-count", default=None, type=click.IntRange(1),
              help="Maximum fuzzy-channel resolutions (default: 10)")
@click.option("--scout/--no-scout", default=True,
              help="Run the YouTube freshness scout (requires YOUTUBE_API_KEY)")
@click.option("--scout-days", default=7, type=click.IntRange(1), show_default=True)
@click.option("--probe", is_flag=True,
              help="Extract cross-source entities and run transparent NEAR/N probes")
@click.option("--no-proxy", is_flag=True, help="Bypass proxy for transcript downloads")
@click.option("--verbose", is_flag=True, help="Show full transcript failure details")
def research(
    topic: str,
    depth: int,
    min_views: int,
    lang: str,
    fallback: bool,
    dedupe: bool,
    min_matches: int,
    sort_by: str,
    candidate_pages: int,
    candidate_pool: int,
    accept_broad: bool,
    broad_threshold: int,
    channel_id: str,
    channel: str,
    channel_count: int,
    scout: bool,
    scout_days: int,
    probe: bool,
    no_proxy: bool,
    verbose: bool,
):
    """Research TOPIC with staged search, visible selection, and checkpoints.

    The search ladder tries title+transcript, an exact phrase, and NEAR/N
    before a loose transcript-wide query. A loose result set above
    ``--broad-threshold`` is never downloaded unless ``--accept-broad`` is
    explicit. Density is topical concentration, not source credibility.

    \b
      filmot research "deep sea mining"
      filmot research "AI data center electricity demand" --candidate-pages 5
      filmot research "niche topic" --accept-broad --probe
      filmot research "fusion" --channel "International Energy Agency"
    """
    import hashlib
    import uuid

    from .ledger import log_event
    from .library import get_library
    from .transcript import (
        describe_routing_plan,
        disable_proxy,
        get_transcript,
        get_transcript_with_fallback,
        routing_plan,
    )

    library = get_library()
    normalized_topic = library._normalize_topic(topic)
    run_id = uuid.uuid4().hex[:12]
    run_status = "failed"
    run_error = None
    phase = "initializing"

    scout_videos = []
    total = 0
    fallback_stage = None
    success_count = 0
    skip_count = 0
    fail_count = 0
    dedupe_count = 0
    probe_success = 0
    probe_fail_count = 0
    probe_query_fail_count = 0
    total_chars = 0
    probe_chars = 0
    selected_count = 0
    resolved_channels = []
    effective_channel_ids = channel_id
    route_plan = None
    search_partial = False

    start_fields = {
        "run_id": run_id,
        "query": topic,
        "depth": depth,
        "lang": lang or "en",
        "min_views": min_views,
        "min_matches": min_matches,
        "sort": sort_by,
        "candidate_pages": candidate_pages,
        "candidate_pool": candidate_pool,
        "accept_broad": accept_broad,
        "broad_threshold": broad_threshold,
        "channel": channel,
        "channel_id": channel_id,
        "channel_count": channel_count,
        "scout": scout,
        "scout_days": scout_days,
        "probe": probe,
        "dedupe": dedupe,
        "fallback": fallback,
    }
    log_event("research_start", topic=normalized_topic, **start_fields)

    def checkpoint(current_phase: str, **fields) -> None:
        nonlocal phase
        phase = current_phase
        log_event(
            "research_checkpoint",
            topic=normalized_topic,
            run_id=run_id,
            phase=current_phase,
            **fields,
        )

    def fetch_transcript(video_id: str):
        route_progress = _route_progress_for(video_id)
        languages = [lang] if lang else None
        if fallback:
            return get_transcript_with_fallback(
                video_id,
                languages=languages,
                use_aws_fallback=True,
                aws_progress_callback=lambda stage, message: click.echo(
                    f"{video_id} AWS:{stage} {message}",
                    err=True,
                ),
                progress_callback=route_progress,
                fresh_primary=True,
            )
        return get_transcript(
            video_id,
            languages=languages,
            progress_callback=route_progress,
            fresh_primary=True,
        )

    try:
        if no_proxy:
            disable_proxy()
        route_plan = routing_plan()
        click.echo(
            f"Transcript routes ({route_plan['mode']}): "
            f"{describe_routing_plan(route_plan)}; "
            f"route deadline {route_plan['route_timeout_s']:g}s",
            err=True,
        )
        checkpoint("routing", status="ready", routing_plan=route_plan)

        client = FilmotClient()
        if channel:
            resolved_ids, resolved_channels = _resolve_channel_filter(
                client, channel, channel_count
            )
            effective_channel_ids = _merge_channel_ids(channel_id, resolved_ids)
            console.print(
                "[cyan]Resolved --channel:[/cyan] "
                + ", ".join(
                    f'{item["name"]} ({item["id"]})'
                    for item in resolved_channels
                )
            )

        console.print(f"[bold]Researching: {topic}[/bold]")
        console.print(f"[dim]Run ID: {run_id}[/dim]\n")

        # Phase 1: freshness scout. A scout failure is non-fatal but explicit.
        if scout:
            checkpoint(
                "scout",
                status="started",
                query=topic,
                days=scout_days,
                channel_id=effective_channel_ids,
            )
            try:
                from .youtube_search import search_recent, validate_youtube_api

                validate_youtube_api()
                scout_allowed_ids = set(
                    (effective_channel_ids or "").split(",")
                ) - {""}
                scout_channel_id = (
                    next(iter(scout_allowed_ids))
                    if len(scout_allowed_ids) == 1
                    else None
                )
                with console.status(
                    f"[bold cyan]Scouting YouTube for '{topic}' "
                    f"(last {scout_days} days)...[/bold cyan]"
                ):
                    scout_videos = search_recent(
                        query=topic,
                        days_back=scout_days,
                        max_results=10,
                        order="relevance",
                        channel_id=scout_channel_id,
                    ) or []
                if scout_allowed_ids:
                    scout_videos = [
                        video
                        for video in scout_videos
                        if str(video.get("channel_id") or "")
                        in scout_allowed_ids
                    ]
                checkpoint(
                    "scout",
                    status="completed",
                    results=len(scout_videos),
                    channel_id=effective_channel_ids,
                )
                console.print(
                    f"[cyan]Scout:[/cyan] Found {len(scout_videos)} recent upload(s)"
                )
                for index, video in enumerate(scout_videos[:5], 1):
                    console.print(
                        f"  {index}. {str(video.get('title', 'Unknown'))[:70]} "
                        f"[dim]({str(video.get('published_at', ''))[:10]}, "
                        f"{int(video.get('views', 0) or 0):,} views)[/dim]"
                    )
            except (ValueError, ImportError) as error:
                checkpoint("scout", status="skipped", error=str(error))
                console.print("[dim]Scout: Skipped (YOUTUBE_API_KEY not configured)[/dim]")
            except Exception as error:
                detail = f"{type(error).__name__}: {_whole_word_summary(error)}"
                checkpoint(
                    "scout",
                    status="failed",
                    error=detail,
                )
                console.print(
                    f"[yellow]Scout: Failed ({detail})[/yellow]"
                )
        else:
            checkpoint("scout", status="disabled", results=0)

        api_kwargs = {
            "lang": lang or "en",
            "min_views": min_views,
            "channel_id": effective_channel_ids,
        }

        def run_search(
            stage: str,
            query: str,
            *,
            title_filter: Optional[str] = None,
        ) -> dict:
            nonlocal search_partial
            checkpoint(
                "search",
                status="started",
                stage=stage,
                query=query,
                title=title_filter,
                channel_id=effective_channel_ids,
                lang=lang or "en",
                candidate_pages=candidate_pages,
                candidate_pool=candidate_pool,
            )
            with console.status(
                f"[bold green]Filmot {stage}: {query}[/bold green]"
            ):
                response = client.search_subtitles_all(
                    query=query,
                    title=title_filter,
                    max_pages=candidate_pages,
                    max_results=candidate_pool,
                    **api_kwargs,
                )
            if "error" in response:
                checkpoint(
                    "search",
                    status="failed",
                    stage=stage,
                    query=query,
                    title=title_filter,
                    error=response["error"],
                )
                _command_error(
                    f"Filmot {stage} search failed: {response['error']}"
                )
            stage_videos = _result_videos(response)
            if effective_channel_ids:
                allowed_ids = set(effective_channel_ids.split(","))
                outside = [
                    video for video in stage_videos
                    if str(
                        video.get("channelid")
                        or video.get("channel_id")
                        or ""
                    ) not in allowed_ids
                ]
                if outside:
                    checkpoint(
                        "search",
                        status="failed_closed",
                        stage=stage,
                        query=query,
                        channel_id=effective_channel_ids,
                        outside_results=len(outside),
                    )
                    _command_error(
                        "Filmot returned candidates outside, or without an ID "
                        "in, the requested channel set; refusing an "
                        "unrestricted fallback."
                    )
            checkpoint(
                "search",
                status="completed",
                stage=stage,
                query=query,
                title=title_filter,
                api_total=response.get("totalresultcount", len(stage_videos)),
                candidates=len(stage_videos),
                pages=response.get("pages_fetched", 1),
                partial=response.get("partial", False),
                page_error=response.get("page_error"),
            )
            if response.get("partial"):
                search_partial = True
                console.print(
                    f"[yellow]Search scope is partial:[/yellow] "
                    f"{response.get('page_error', 'later page failed')}"
                )
            return response

        def eligible_stage_candidates(response: dict, stage: str) -> list:
            candidates = _result_videos(response)
            if min_matches <= 0:
                return candidates
            eligible = [
                video
                for video in candidates
                if len(video.get("hits", [])) >= min_matches
            ]
            if len(eligible) != len(candidates):
                console.print(
                    f"[dim]{stage} hit filter: {len(candidates)} -> "
                    f"{len(eligible)} candidates (minimum {min_matches})[/dim]"
                )
                checkpoint(
                    "search_filter",
                    status="completed",
                    stage=stage,
                    candidates=len(candidates),
                    eligible=len(eligible),
                    min_matches=min_matches,
                )
            return eligible

        def relationship_evidence_gate(candidates: list, stage: str) -> list:
            """Require visible topic coherence before trusting fallback syntax."""
            if stage not in {"exact_phrase", "proximity", "proximity_alt"}:
                return candidates
            before = len(candidates)
            kept = []
            for video in candidates:
                assessment = _candidate_assessment(video, topic)
                video["_selection"] = assessment
                if (
                    assessment["passage_coverage"] >= 0.75
                    and assessment["token_coverage"] >= 0.75
                ):
                    kept.append(video)
            if len(kept) != before:
                checkpoint(
                    "relationship_gate",
                    status="completed",
                    stage=stage,
                    candidates_before=before,
                    candidates_after=len(kept),
                    threshold={
                        "passage_coverage": 0.75,
                        "token_coverage": 0.75,
                    },
                )
                console.print(
                    f"[dim]{stage} relationship-evidence gate: {before} -> "
                    f"{len(kept)} candidates (at least 75% topic coverage "
                    "within one visible passage)[/dim]"
                )
            return kept

        # Phase 2: relationship-preserving search ladder. A stage only stops
        # the ladder when it contains candidates that pass the download gate.
        results = run_search("title+transcript", topic, title_filter=topic)
        title_filter_works = bool(_result_videos(results))
        videos = eligible_stage_candidates(results, "title+transcript")
        fallback_stage = "title_transcript"

        if not videos:
            for stage, query in _research_query_ladder(topic):
                console.print(
                    f"[dim]No title+transcript candidates; trying {stage}: {query}[/dim]"
                )
                results = run_search(stage, query)
                videos = eligible_stage_candidates(results, stage)
                videos = relationship_evidence_gate(videos, stage)
                fallback_stage = stage
                if videos:
                    break

        broad_blocked = False
        if not videos:
            console.print(
                "[dim]Relationship-preserving stages returned no candidates; "
                "measuring loose transcript-wide fallback...[/dim]"
            )
            results = run_search("broad_loose", topic)
            videos = eligible_stage_candidates(results, "broad_loose")
            fallback_stage = "broad_loose"
            broad_total = int(results.get("totalresultcount", len(videos)) or 0)
            if broad_total > broad_threshold and not accept_broad:
                broad_blocked = True
                videos = []
                console.print(
                    f"[yellow]Safety gate:[/yellow] loose fallback has "
                    f"{broad_total:,} results (threshold {broad_threshold:,}). "
                    "It will not be downloaded automatically. Refine the topic "
                    "or rerun with --accept-broad after reviewing the scope."
                )
                checkpoint(
                    "broad_gate",
                    status="blocked",
                    stage=fallback_stage,
                    api_total=broad_total,
                    threshold=broad_threshold,
                )

        total = int(results.get("totalresultcount", len(videos)) or 0)
        for video in videos:
            video["_fallback_stage"] = fallback_stage

        videos = _rank_research_candidates(videos, topic, sort_by)

        # Even with explicit acceptance, a loose pool needs a relationship
        # threshold. Repeated isolated terms are not enough to auto-download.
        if fallback_stage == "broad_loose" and not broad_blocked:
            before = len(videos)
            topic_tokens = _research_topic_tokens(topic)
            if topic_tokens:
                videos = [
                    video for video in videos
                    if video["_selection"]["passage_coverage"] >= 0.60
                    and video["_selection"]["token_coverage"] >= 0.75
                ]
            console.print(
                f"[dim]Broad relevance gate: {before} -> {len(videos)} candidates "
                "(terms must cohere in a title/hit passage)[/dim]"
            )
            checkpoint(
                "broad_gate",
                status="accepted_explicit" if accept_broad else "accepted_bounded",
                api_total=total,
                candidates_before=before,
                candidates_after=len(videos),
                threshold={"passage_coverage": 0.60, "token_coverage": 0.75},
            )

        # Merge fresh scout videos and score them transparently. They remain
        # clearly tagged because Filmot hit evidence is unavailable.
        filmot_ids = {
            video.get("id") or video.get("videoid")
            for video in videos
        }
        for scout_video in scout_videos:
            if scout_video.get("video_id") in filmot_ids:
                continue
            videos.append({
                "id": scout_video.get("video_id"),
                "videoid": scout_video.get("video_id"),
                "title": scout_video.get("title", ""),
                "description": scout_video.get("description", ""),
                "channelname": scout_video.get("channel_title", ""),
                "viewcount": scout_video.get("views", 0),
                "duration": 0,
                "hits": [],
                "_from_scout": True,
                "_fallback_stage": "scout",
            })

        videos = _rank_research_candidates(videos, topic, sort_by)
        scout_before = sum(
            bool(video.get("_from_scout"))
            for video in videos
        )
        if scout_before:
            videos = [
                video
                for video in videos
                if not video.get("_from_scout")
                or (
                    video["_selection"]["passage_coverage"] >= 0.50
                    and video["_selection"]["token_coverage"] >= 0.60
                )
            ]
            scout_after = sum(
                bool(video.get("_from_scout"))
                for video in videos
            )
            console.print(
                f"[dim]Scout relevance gate: {scout_before} -> "
                f"{scout_after} candidates (topic terms must occur together "
                "in the title or description)[/dim]"
            )
            checkpoint(
                "scout_gate",
                status="completed",
                candidates_before=scout_before,
                candidates_after=scout_after,
                threshold={
                    "passage_coverage": 0.50,
                    "token_coverage": 0.60,
                },
            )
        accepted_scouts = any(
            video.get("_from_scout")
            for video in videos
        )
        if broad_blocked and not accepted_scouts:
            _command_error(
                f"Broad fallback blocked at {total:,} results. Refine TOPIC or "
                "rerun with --accept-broad after reviewing the risk."
            )
        if not videos:
            run_status = "complete_empty"
            console.print(
                "[yellow]No candidates passed the search and relevance gates.[/yellow]"
            )
            checkpoint(
                "selection",
                status="empty",
                fallback_stage=fallback_stage,
                filmot_total=total,
            )
            return

        _research_candidate_preview(videos)
        previewed = videos[: min(depth or 8, 8)]
        if previewed and all(
            video.get("_selection", {}).get("source_signal", 0) < 0.20
            for video in previewed
        ):
            console.print(
                "[yellow]Source-quality warning:[/yellow] every leading candidate "
                "has a weak visible source prior. Treat the corpus as discovery "
                "material and add authoritative channels/primary sources."
            )
        echoed = sum(
            bool(video.get("_selection", {}).get("echo_cluster"))
            for video in previewed
        )
        if echoed:
            console.print(
                f"[yellow]Echo warning:[/yellow] {echoed} leading candidate(s) "
                "share near-identical hit phrasing; the balanced rank penalized them."
            )

        scout_candidates = [video for video in videos if video.get("_from_scout")]
        if scout_candidates:
            scout_slots = min(len(scout_candidates), max(1, depth // 3)) if depth else 0
            filmot_candidates = [video for video in videos if not video.get("_from_scout")]
            videos_to_download = (
                filmot_candidates[: max(depth - scout_slots, 0)]
                + scout_candidates[:scout_slots]
            )
            if len(videos_to_download) < depth:
                selected_ids = {
                    video.get("id") or video.get("videoid")
                    for video in videos_to_download
                }
                videos_to_download.extend(
                    video for video in videos
                    if (video.get("id") or video.get("videoid")) not in selected_ids
                )
                videos_to_download = videos_to_download[:depth]
        else:
            videos_to_download = videos[:depth]
        selected_count = len(videos_to_download)
        checkpoint(
            "selection",
            status="completed",
            fallback_stage=fallback_stage,
            filmot_total=total,
            candidates=len(videos),
            selected=selected_count,
            candidate_pages=candidate_pages,
            candidate_pool=candidate_pool,
            ranking=sort_by,
            selections=[
                {
                    "video_id": video.get("id") or video.get("videoid"),
                    "title": video.get("title"),
                    "channel": video.get("channelname"),
                    "stage": video.get("_fallback_stage"),
                    "signals": video.get("_selection"),
                }
                for video in videos_to_download
            ],
        )

        seen_hashes = set()
        if dedupe:
            for item in library.list_transcripts(normalized_topic):
                data = library.get(item["video_id"], normalized_topic)
                if data:
                    seen_hashes.add(
                        hashlib.md5(
                            data.get("transcript", "")[:500].encode()
                        ).hexdigest()
                    )

        console.print(
            f"\n[bold]Downloading {len(videos_to_download)} transcript(s)...[/bold]"
        )
        proxy_errors = False
        for index, video in enumerate(videos_to_download, 1):
            video_id = video.get("id") or video.get("videoid")
            title_text = str(video.get("title", "Unknown"))
            channel_name = (
                video.get("channelname")
                or video.get("channeltitle")
                or video.get("channel")
                or "Unknown"
            )
            source = "scout" if video.get("_from_scout") else fallback_stage
            selection = video.get("_selection", {})
            checkpoint(
                "download_item",
                status="started",
                index=index,
                total=selected_count,
                video_id=video_id,
                title=title_text,
                stage=source,
                signals=selection,
            )
            title_text, channel_name = _backfill_metadata(
                video_id, title_text, channel_name
            )

            if library.exists(video_id, normalized_topic):
                skip_count += 1
                data = library.get(video_id, normalized_topic)
                if data:
                    total_chars += len(data.get("transcript", ""))
                checkpoint(
                    "download_item",
                    status="skipped",
                    video_id=video_id,
                    reason="already_exists",
                    stage=source,
                    signals=selection,
                )
                console.print(
                    f"  [{index}/{selected_count}] [yellow]Skip[/yellow] "
                    f"{title_text[:60]} (already saved)"
                )
                continue

            console.print(
                f"  [{index}/{selected_count}] [dim]Fetching {title_text[:60]} "
                "(route ladder in progress)...[/dim]"
            )
            try:
                transcript_result = fetch_transcript(video_id)
                if "error" in transcript_result:
                    error_text = str(transcript_result["error"])
                    error_display = _transcript_failure_detail(
                        transcript_result,
                        verbose=verbose,
                    )
                    fail_count += 1
                    if "proxy" in error_text.casefold():
                        proxy_errors = True
                    checkpoint(
                        "download_item",
                        status="failed",
                        video_id=video_id,
                        title=title_text,
                        stage=source,
                        signals=selection,
                        error=_whole_word_summary(error_text, 500),
                        error_type=transcript_result.get("error_type"),
                        route=transcript_result.get("route"),
                        routes_tried=transcript_result.get("routes_tried"),
                        route_errors=transcript_result.get("route_errors"),
                    )
                    console.print(
                        f"  [{index}/{selected_count}] [red]Fail[/red] "
                        f"{title_text[:60]} [dim]- {error_display}[/dim]"
                    )
                    continue

                full_text = transcript_result.get("full_text", "")
                if dedupe and full_text:
                    digest = hashlib.md5(full_text[:500].encode()).hexdigest()
                    if digest in seen_hashes:
                        dedupe_count += 1
                        checkpoint(
                            "download_item",
                            status="skipped",
                            video_id=video_id,
                            reason="duplicate",
                            stage=source,
                            signals=selection,
                        )
                        console.print(
                            f"  [{index}/{selected_count}] [magenta]Dedupe[/magenta] "
                            f"{title_text[:60]}"
                        )
                        continue
                    seen_hashes.add(digest)

                metadata = {
                    "title": title_text,
                    "channel": channel_name,
                    "language": transcript_result.get("language"),
                    "is_generated": transcript_result.get("is_generated"),
                    "duration_seconds": transcript_result.get("duration_seconds"),
                    "segment_count": transcript_result.get("segment_count"),
                    "views": video.get("viewcount"),
                    "research_run_id": run_id,
                    "selection_stage": source,
                    "selection_signals": selection,
                    "route": transcript_result.get("route"),
                }
                library.save(
                    video_id=video_id,
                    topic=normalized_topic,
                    transcript_text=full_text,
                    metadata=metadata,
                )
                success_count += 1
                total_chars += len(full_text)
                checkpoint(
                    "download_item",
                    status="saved",
                    video_id=video_id,
                    title=title_text,
                    channel=channel_name,
                    stage=source,
                    signals=selection,
                    chars=len(full_text),
                    route=transcript_result.get("route"),
                    routes_tried=transcript_result.get("routes_tried"),
                )
                console.print(
                    f"  [{index}/{selected_count}] [green]✓[/green] "
                    f"{title_text[:60]}"
                )
            except KeyboardInterrupt:
                raise
            except Exception as error:
                fail_count += 1
                detail = f"{type(error).__name__}: {error}"
                checkpoint(
                    "download_item",
                    status="failed",
                    video_id=video_id,
                    title=title_text,
                    stage=source,
                    signals=selection,
                    error=detail,
                )
                console.print(
                    f"  [{index}/{selected_count}] [red]Fail[/red] "
                    f"{title_text[:60]} [dim]- "
                    f"{detail if verbose else _whole_word_summary(detail)}[/dim]"
                )

        if proxy_errors:
            console.print(
                "[yellow]Proxy failures occurred. Inspect routes_tried in the "
                "session ledger or retry with --no-proxy.[/yellow]"
            )

        # Phase 4: probes with every effective constraint and count visible.
        if probe:
            checkpoint("probe", status="started")
            transcript_texts = []
            for item in library.list_transcripts(normalized_topic):
                data = library.get(item["video_id"], normalized_topic)
                if data and data.get("transcript"):
                    transcript_texts.append(data["transcript"])

            if len(transcript_texts) < 2:
                checkpoint(
                    "probe",
                    status="skipped",
                    reason="insufficient_transcripts",
                    transcripts=len(transcript_texts),
                )
                console.print(
                    f"[dim]Probe: Need at least 2 transcripts; got "
                    f"{len(transcript_texts)}.[/dim]"
                )
            else:
                terms = _extract_probe_terms(transcript_texts, topic)
                pairs = _find_probe_pairs(transcript_texts, terms)
                console.print(
                    f"\n[bold]Probing relationships from "
                    f"{len(transcript_texts)} transcript(s)...[/bold]"
                )
                console.print(
                    f"  Entities: [cyan]{', '.join(terms[:8]) or 'none'}[/cyan]"
                )
                existing_ids = {
                    item["video_id"]
                    for item in library.list_transcripts(normalized_topic)
                }
                probe_candidates = []
                topic_words = _probe_topic_words(topic)
                probe_title = topic if title_filter_works else None

                for index, (term1, term2, co_windows, source_support) in enumerate(
                    pairs, 1
                ):
                    probe_query = f'"{term1}" NEAR/15 "{term2}"'
                    effective_scope = {
                        "title": probe_title,
                        "channel_id": effective_channel_ids,
                        "lang": lang or "en",
                    }
                    checkpoint(
                        "probe_search",
                        status="started",
                        index=index,
                        query=probe_query,
                        constraints=effective_scope,
                        co_windows=co_windows,
                        source_support=source_support,
                    )
                    try:
                        with console.status(f"Probing: {probe_query}"):
                            probe_result = client.search_subtitles(
                                query=probe_query,
                                title=probe_title,
                                lang=lang or "en",
                                channel_id=effective_channel_ids,
                            )
                    except Exception as error:
                        detail = f"{type(error).__name__}: {error}"
                        probe_query_fail_count += 1
                        log_event(
                            "research_probe",
                            topic=normalized_topic,
                            run_id=run_id,
                            query=probe_query,
                            constraints=effective_scope,
                            co_windows=co_windows,
                            source_support=source_support,
                            status="failed",
                            error=detail,
                        )
                        checkpoint(
                            "probe_search",
                            status="failed",
                            index=index,
                            query=probe_query,
                            constraints=effective_scope,
                            error=detail,
                        )
                        console.print(
                            f"  Probe {index}: {probe_query} → "
                            f"[red]error[/red] ({_whole_word_summary(detail)})"
                        )
                        continue

                    if "error" in probe_result:
                        detail = str(probe_result["error"])
                        probe_query_fail_count += 1
                        log_event(
                            "research_probe",
                            topic=normalized_topic,
                            run_id=run_id,
                            query=probe_query,
                            constraints=effective_scope,
                            co_windows=co_windows,
                            source_support=source_support,
                            status="failed",
                            error=detail,
                        )
                        checkpoint(
                            "probe_search",
                            status="failed",
                            index=index,
                            query=probe_query,
                            constraints=effective_scope,
                            error=detail,
                        )
                        console.print(
                            f"  Probe {index}: {probe_query} → "
                            f"[red]error[/red] ({_whole_word_summary(detail)})"
                        )
                        continue

                    raw_hits = _result_videos(probe_result)
                    if effective_channel_ids:
                        allowed_ids = set(effective_channel_ids.split(","))
                        outside = [
                            video
                            for video in raw_hits
                            if str(
                                video.get("channelid")
                                or video.get("channel_id")
                                or ""
                            ) not in allowed_ids
                        ]
                        if outside:
                            checkpoint(
                                "probe_search",
                                status="failed_closed",
                                index=index,
                                query=probe_query,
                                constraints=effective_scope,
                                outside_results=len(outside),
                            )
                            log_event(
                                "research_probe",
                                topic=normalized_topic,
                                run_id=run_id,
                                query=probe_query,
                                constraints=effective_scope,
                                status="failed_closed",
                                outside_results=len(outside),
                            )
                            _command_error(
                                "Filmot returned probe candidates outside, or "
                                "without an ID in, the requested channel set; "
                                "refusing an unrestricted probe."
                            )
                    scoped_hits = [
                        video for video in raw_hits
                        if (video.get("id") or video.get("videoid")) not in existing_ids
                    ]
                    if not probe_title:
                        scoped_hits = [
                            video for video in scoped_hits
                            if _probe_hit_is_relevant(video, topic_words)
                        ]
                    api_total = int(
                        probe_result.get("totalresultcount", len(raw_hits)) or 0
                    )
                    peak_density = max(
                        (_density(video) for video in raw_hits),
                        default=0.0,
                    )
                    scope_label = (
                        f'title="{probe_title}"'
                        if probe_title else "topic relevance post-filter"
                    )
                    log_event(
                        "research_probe",
                        topic=normalized_topic,
                        run_id=run_id,
                        query=probe_query,
                        constraints=effective_scope,
                        co_windows=co_windows,
                        source_support=source_support,
                        status="completed",
                        api_total=api_total,
                        returned=len(raw_hits),
                        scoped=len(scoped_hits),
                        peak_density=round(peak_density, 3),
                    )
                    checkpoint(
                        "probe_search",
                        status="completed",
                        index=index,
                        query=probe_query,
                        constraints=effective_scope,
                        api_total=api_total,
                        returned=len(raw_hits),
                        scoped=len(scoped_hits),
                    )
                    console.print(
                        f"  Probe {index}: {probe_query} "
                        f"[dim](co-windows:{co_windows}; sources:{source_support}; "
                        f"scope:{scope_label})[/dim] → "
                        f"[green]{api_total:,} API results[/green]; "
                        f"{len(raw_hits)} returned; {len(scoped_hits)} new+scoped; "
                        f"peak {peak_density:.1f}/min"
                    )
                    ranked_probe = _rank_research_candidates(
                        scoped_hits, topic, "balanced"
                    )
                    for video in ranked_probe[:2]:
                        video_id = video.get("id") or video.get("videoid")
                        if video_id not in existing_ids:
                            probe_candidates.append(video)
                            existing_ids.add(video_id)

                for video in probe_candidates[:3]:
                    video_id = video.get("id") or video.get("videoid")
                    title_text = str(video.get("title", "Unknown"))
                    channel_name = (
                        video.get("channelname")
                        or video.get("channeltitle")
                        or "Unknown"
                    )
                    checkpoint(
                        "probe_download",
                        status="started",
                        video_id=video_id,
                        title=title_text,
                    )
                    title_text, channel_name = _backfill_metadata(
                        video_id, title_text, channel_name
                    )
                    console.print(
                        f"  [dim]Fetching probe discovery {title_text[:60]}...[/dim]"
                    )
                    try:
                        transcript_result = fetch_transcript(video_id)
                        if "error" in transcript_result:
                            detail = str(transcript_result["error"])
                            probe_fail_count += 1
                            checkpoint(
                                "probe_download",
                                status="failed",
                                video_id=video_id,
                                error=_whole_word_summary(detail, 500),
                                error_type=transcript_result.get("error_type"),
                                route=transcript_result.get("route"),
                                routes_tried=transcript_result.get("routes_tried"),
                                route_errors=transcript_result.get("route_errors"),
                            )
                            console.print(
                                f"  [red]Fail[/red] {title_text[:60]} "
                                f"[dim]- {_transcript_failure_detail(transcript_result, verbose=verbose)}[/dim]"
                            )
                            continue
                        full_text = transcript_result.get("full_text", "")
                        library.save(
                            video_id=video_id,
                            topic=normalized_topic,
                            transcript_text=full_text,
                            metadata={
                                "title": title_text,
                                "channel": channel_name,
                                "language": transcript_result.get("language"),
                                "is_generated": transcript_result.get("is_generated"),
                                "duration_seconds": transcript_result.get("duration_seconds"),
                                "segment_count": transcript_result.get("segment_count"),
                                "views": video.get("viewcount"),
                                "research_run_id": run_id,
                                "selection_stage": "probe",
                                "selection_signals": video.get("_selection"),
                                "route": transcript_result.get("route"),
                            },
                        )
                        probe_success += 1
                        probe_chars += len(full_text)
                        checkpoint(
                            "probe_download",
                            status="saved",
                            video_id=video_id,
                            chars=len(full_text),
                            route=transcript_result.get("route"),
                            routes_tried=transcript_result.get("routes_tried"),
                        )
                        console.print(
                            f"  [green]✓[/green] {title_text[:60]} "
                            "[dim](probe)[/dim]"
                        )
                    except KeyboardInterrupt:
                        raise
                    except Exception as error:
                        probe_fail_count += 1
                        detail = f"{type(error).__name__}: {error}"
                        checkpoint(
                            "probe_download",
                            status="failed",
                            video_id=video_id,
                            error=detail,
                        )
                        console.print(
                            f"  [red]Fail[/red] {title_text[:60]} "
                            f"[dim]- "
                            f"{detail if verbose else _whole_word_summary(detail)}[/dim]"
                        )
                checkpoint(
                    "probe",
                    status=(
                        "completed_with_failures"
                        if probe_fail_count or probe_query_fail_count
                        else "completed"
                    ),
                    transcripts=len(transcript_texts),
                    terms=len(terms),
                    queries=len(pairs),
                    query_failed=probe_query_fail_count,
                    candidates=len(probe_candidates),
                    saved=probe_success,
                    download_failed=probe_fail_count,
                )
        else:
            checkpoint("probe", status="disabled")

        total_item_failure = (
            selected_count > 0
            and fail_count >= selected_count
            and success_count + skip_count + dedupe_count + probe_success == 0
        )
        run_status = (
            "failed"
            if total_item_failure
            else "completed_with_failures"
            if fail_count or probe_fail_count or probe_query_fail_count
            else "completed_partial"
            if search_partial
            else "completed"
        )
        all_chars = total_chars + probe_chars
        # Keep the aggregate event for existing session readers while the
        # start/checkpoint/end events make partial runs resumable.
        log_event(
            "research",
            topic=normalized_topic,
            run_id=run_id,
            query=topic,
            scout=len(scout_videos),
            filmot_total=total,
            fallback_stage=fallback_stage,
            selected=selected_count,
            saved=success_count,
            skipped=skip_count,
            failed=fail_count,
            deduped=dedupe_count,
            probe=probe_success,
            probe_failed=probe_fail_count,
            probe_query_failed=probe_query_fail_count,
            depth=depth,
            ranking=sort_by,
            status=run_status,
            routing_plan=route_plan,
        )
        console.print(f"\n{'=' * 60}")
        console.print(f"[bold]Research complete: {normalized_topic}[/bold]")
        console.print(
            f"  Saved: {success_count} | Skipped: {skip_count} | "
            f"Failed: {fail_count} | Deduped: {dedupe_count} | "
            f"Probe: {probe_success} saved / {probe_fail_count} download failed "
            f"/ {probe_query_fail_count} query failed"
        )
        console.print(
            f"  Total content: {all_chars:,} characters "
            f"({all_chars / 1024:.0f} KB)"
        )

        transcripts = library.list_transcripts(normalized_topic)
        if transcripts:
            console.print(f"\n[bold]Sources ({len(transcripts)}):[/bold]")
            for item in transcripts:
                console.print(
                    f"  - {item.get('title', 'Unknown')} "
                    f"({item.get('channel', 'Unknown')})"
                )
        console.print("\n[dim]Next steps:[/dim]")
        console.print(
            f'  filmot library search "your query" --topic {normalized_topic}'
        )
        console.print(
            f'  filmot library compare "claim" --topic {normalized_topic}'
        )
        console.print(
            f"  filmot library context {normalized_topic} -o context.txt"
        )
        if total_item_failure:
            _command_error(
                f"All {fail_count} selected transcript downloads failed."
            )

    except KeyboardInterrupt:
        run_status = "interrupted"
        run_error = f"Interrupted during {phase}"
        checkpoint(
            phase,
            status="interrupted",
            saved=success_count,
            failed=fail_count,
            selected=selected_count,
        )
        raise click.Abort()
    except click.ClickException as error:
        run_error = str(error)
        raise
    except ValueError as error:
        run_error = f"Configuration error: {error}"
        _command_error(run_error)
    except Exception as error:
        run_error = f"{type(error).__name__}: {error}"
        _command_error(run_error)
    finally:
        log_event(
            "research_end",
            topic=normalized_topic,
            run_id=run_id,
            status=run_status,
            phase=phase,
            error=run_error,
            fallback_stage=fallback_stage,
            scout=len(scout_videos),
            filmot_total=total,
            selected=selected_count,
            saved=success_count,
            skipped=skip_count,
            failed=fail_count,
            deduped=dedupe_count,
            probe=probe_success,
            probe_failed=probe_fail_count,
            probe_query_failed=probe_query_fail_count,
            chars=total_chars + probe_chars,
            routing_plan=route_plan,
        )


# ========== CHANNEL DOWNLOAD ==========

@cli.command("channel-download")
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
    from .channel_dl import ChannelDownloader, get_channel_info
    from .ledger import log_event
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
    from .channel_dl import list_all_video_ids

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
        log_event(
            "channel_download_end", channel_id=channel_id, slug=slug,
            status="completed", enumerated=len(all_videos),
            downloaded=0, failed=0, already_done=len(already_done),
        )
        console.print("[green]✓ Channel is fully synced! Nothing new to download.[/green]")
        console.print(f"[dim]Total transcripts: {len(already_done)}[/dim]")
        return

    if already_done:
        stderr_console.print(f"[yellow]Resuming — already have {len(already_done)} transcripts, {len(to_download)} remaining[/yellow]")
    else:
        stderr_console.print(f"[blue]Downloading {len(to_download)} transcripts...[/blue]")

    if workers > 1:
        stderr_console.print(f"[blue]Using {workers} parallel workers[/blue]")

    # Phase 4: Download with progress bar
    from .transcript import (
        describe_routing_plan,
        get_transcript,
        routing_plan,
    )
    import time as _time
    from datetime import datetime as _dt

    # Force direct connection if --no-proxy
    if no_proxy:
        from .transcript import disable_proxy
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
    console.print()
    console.print(Panel(
        f"[bold green]Download Complete[/bold green]\n\n"
        f"Channel: [bold]{info['name']}[/bold]\n"
        f"This session: [green]{downloaded}[/green] downloaded, [red]{failed}[/red] failed\n"
        f"Total corpus: [bold]{done_total}/{len(all_videos)}[/bold] videos\n"
        f"Words downloaded: [bold]{total_words:,}[/bold]\n"
        f"Storage: {channel_dir}",
        title="Summary",
        border_style="green",
    ))
    log_event(
        "channel_download_end",
        channel_id=channel_id,
        slug=slug,
        status="interrupted" if _interrupted.is_set() else (
            "completed_with_failures" if failed else "completed"
        ),
        enumerated=len(all_videos),
        selected=len(to_download),
        downloaded=downloaded,
        failed=failed,
        corpus_done=done_total,
        words=total_words,
        routing_plan=route_plan,
    )
    if _interrupted.is_set():
        raise click.Abort()
    if to_download and failed == len(to_download) and downloaded == 0:
        _command_error(f"All {failed} selected channel transcripts failed.")


@cli.command("channel-status")
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
    from .channel_dl import ChannelDownloader
    from .ledger import log_event

    downloader = ChannelDownloader()

    if channel_slug is None:
        # List all channels
        channels = downloader.get_downloaded_channels()
        log_event(
            "channel_status",
            scope="all",
            channels=len(channels),
        )
        if not channels:
            console.print("[dim]No channels downloaded yet.[/dim]")
            console.print("[dim]Use: filmot channel-download <channel_id>[/dim]")
            return

        table = Table(title="Downloaded Channels", border_style="blue")
        table.add_column("Channel", style="bold")
        table.add_column("Downloaded", justify="right")
        table.add_column("Failed", justify="right")
        table.add_column("Total", justify="right")
        table.add_column("Last Updated", style="dim")
        table.add_column("Slug", style="dim")

        for ch in channels:
            table.add_row(
                ch['name'],
                str(ch['downloaded']),
                str(ch['failed']),
                str(ch['total_videos']),
                ch['last_updated'][:19] if ch['last_updated'] else '',
                ch['slug'],
            )
        console.print(table)
    else:
        # Show detailed stats
        stats = downloader.get_channel_stats(channel_slug)
        if not stats:
            log_event(
                "channel_status",
                scope="channel",
                slug=channel_slug,
                status="not_found",
            )
            console.print(f"[red]Channel '{channel_slug}' not found.[/red]")
            console.print("[dim]Run 'filmot channel-status' to see available channels.[/dim]")
            return

        ch = stats.get('channel', {})
        log_event(
            "channel_status",
            scope="channel",
            slug=channel_slug,
            status="found",
            downloaded=stats["downloaded"],
            failed=stats["failed"],
            pending=stats["pending"],
            total=stats["total_videos"],
        )
        console.print(Panel(
            f"[bold]{ch.get('name', channel_slug)}[/bold]\n"
            f"Channel ID: {ch.get('channel_id', 'N/A')}\n\n"
            f"Downloaded: [green]{stats['downloaded']}[/green] / {stats['total_videos']}\n"
            f"Failed: [red]{stats['failed']}[/red]\n"
            f"Pending: [yellow]{stats['pending']}[/yellow]\n\n"
            f"Total words: [bold]{stats['total_words']:,}[/bold]\n"
            f"Total duration: [bold]{stats['total_duration_hours']}[/bold] hours\n\n"
            f"Last sync: {stats['last_sync'][:19] if stats['last_sync'] else 'never'}\n"
            f"Storage: {stats['storage_path']}",
            title="Channel Corpus",
            border_style="cyan",
        ))


@cli.command("channel-search")
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
    from .channel_dl import ChannelDownloader, _parse_proximity_query
    import re

    def _format_near_operand(terms: list[str]) -> str:
        if len(terms) == 1:
            return f'"{terms[0]}"'
        return "(" + " | ".join(f'"{term}"' for term in terms) + ")"

    parsed = _parse_proximity_query(query)
    downloader = ChannelDownloader()

    # Build a display label for the query type
    if parsed[0] == 'near':
        qlabel = f"{_format_near_operand(parsed[1])} NEAR/{parsed[3]} {_format_near_operand(parsed[2])}"
    elif parsed[0] == 'tilde':
        qlabel = f'"{" ".join(parsed[1])}"~{parsed[2]}'
    else:
        qlabel = query

    try:
        with console.status(f"[blue]Searching '{qlabel}' across {channel_slug}..."):
            results = downloader.search_corpus(channel_slug, query)
    except ValueError as e:
        _command_error(str(e))

    if not results:
        from .ledger import log_event
        log_event(
            "channel-search", slug=channel_slug, query=query,
            videos=0, hits=0, limit=limit,
        )
        console.print(f"[dim]No matches for '{qlabel}' in {channel_slug}.[/dim]")
        return

    total_matches = sum(r['match_count'] for r in results)
    results = results[:limit]

    from .ledger import log_event
    log_event(
        "channel-search", slug=channel_slug, query=query,
        videos=len(results), hits=total_matches, limit=limit,
    )
    console.print(f"\n[bold]Found {len(results)} videos matching '{qlabel}' ({total_matches} total hits)[/bold]\n")

    for r in results:
        console.print(f"[bold cyan]{r['title']}[/bold cyan]")
        console.print(f"  [dim]{r['video_id']} | {r['published_at'][:10] if r['published_at'] else 'N/A'} | {r['match_count']} matches[/dim]")
        for snippet in r['snippets'][:2]:
            # Highlight keywords in the snippet
            highlighted = snippet
            if parsed[0] == 'near':
                terms = []
                seen = set()
                for group in [parsed[1], parsed[2]]:
                    for term in group:
                        key = term.lower()
                        if key not in seen:
                            terms.append(term)
                            seen.add(key)

                for term in sorted(terms, key=len, reverse=True):
                    highlighted = re.sub(
                        re.escape(term),
                        f"[bold yellow]{term}[/bold yellow]",
                        highlighted,
                        flags=re.IGNORECASE,
                    )
            elif parsed[0] == 'tilde':
                for w in parsed[1]:
                    highlighted = re.sub(
                        re.escape(w),
                        f"[bold yellow]{w}[/bold yellow]",
                        highlighted,
                        flags=re.IGNORECASE,
                    )
            else:
                highlighted = snippet.replace(query, f"[bold yellow]{query}[/bold yellow]")
            console.print(f"  [dim]→[/dim] {highlighted}")
        console.print()


# ========== SESSIONS (LEDGER) ==========

@cli.command("sessions")
@click.argument("name", required=False, default=None)
@click.option("--raw", is_flag=True, help="Output one JSON array")
def sessions(name: str, raw: bool):
    """Show the research session ledger so you can resume prior investigations.

    Every search, research run, and channel-search is logged to
    .filmot_data/sessions/. Without NAME, lists all sessions. With a NAME
    (topic slug or YYYY-MM-DD date), replays that session's events.

    \b
    Examples:
        filmot sessions                       # list all sessions
        filmot sessions fable-5-mythos        # replay a topic session
        filmot sessions 2026-06-10            # replay a day's ad-hoc queries
        filmot sessions 2026-06-10 --raw      # one JSON array for piping
    """
    from .ledger import list_sessions, read_events

    if not name:
        rows = list_sessions()
        if raw:
            click.echo(json_mod.dumps(rows, ensure_ascii=False))
            return
        if not rows:
            console.print("[dim]No sessions logged yet. Run a search or research command first.[/dim]")
            return
        table = Table(title="Research Sessions")
        table.add_column("Session", style="cyan")
        table.add_column("Events", justify="right")
        table.add_column("Last activity", style="dim")
        for r in rows:
            table.add_row(r["name"], str(r["events"]), r["last_ts"].replace("T", " "))
        console.print(table)
        console.print("\n[dim]Replay one with: filmot sessions <name>[/dim]")
        return

    events = read_events(name)
    if raw:
        click.echo(json_mod.dumps(events, ensure_ascii=False))
        return
    if not events:
        console.print(f"[yellow]No session found for '{name}'.[/yellow]")
        return

    console.print(f"[bold]Session: {name}[/bold] ({len(events)} events)\n")
    for e in events:
        ts = e.get("ts", "").replace("T", " ")
        kind = e.get("kind", "?")
        if kind == "search":
            detail = f"\"{e.get('query','')}\" → {e.get('results',0)}/{e.get('total','?')} results"
            if e.get("start_date") or e.get("end_date"):
                detail += f" [{e.get('start_date','')}..{e.get('end_date','')}]"
        elif kind == "research":
            detail = f"\"{e.get('query','')}\" → saved {e.get('saved',0)}, probe {e.get('probe',0)} (scout {e.get('scout',0)})"
        elif kind == "channel-search":
            detail = f"{e.get('slug','')}: \"{e.get('query','')}\" → {e.get('videos',0)} vids / {e.get('hits',0)} hits"
        elif kind == "transcript":
            detail = f"{e.get('video_id','')}" + (f" grep \"{e.get('grep')}\"" if e.get("grep") else "")
        else:
            detail = json_mod.dumps({k: v for k, v in e.items() if k not in ("ts", "kind")}, ensure_ascii=False)
        console.print(f"  [dim]{ts}[/dim] [cyan]{kind}[/cyan]  {detail}")


# ========== DOWNLOAD (STDIN) ==========

@cli.command("download")
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

    try:
        raw_input = sys.stdin.read()
        results = json_mod.loads(raw_input)
    except (json_mod.JSONDecodeError, ValueError) as e:
        _command_error(
            f"Invalid JSON from stdin: {e}. Pipe search results with --raw."
        )

    # Wrap in expected format if needed
    if isinstance(results, list):
        results = {"result": results}

    if no_proxy:
        from .transcript import disable_proxy
        disable_proxy()
        console.print("[dim]Proxy disabled, using direct connection[/dim]")

    _bulk_download_transcripts(
        results,
        f"{topic}:{count}",
        console,
        fallback=fallback,
        dedupe=dedupe,
        lang=lang,
    )


# ========== PROXY POOL ==========

@cli.group()
def proxy():
    """Manage the file/API proxy pool used for transcript fetches.

    Configure WEBSHARE_API_TOKEN or a WEBSHARE_SESSION_FILE. Availability means
    eligible now; "recently healthy" requires a successful bounded live fetch.
    """


def _require_pool():
    from .proxy_pool import get_pool
    pool = get_pool()
    if pool is None:
        raise click.ClickException(
            "No transcript proxy pool is configured. Set WEBSHARE_API_TOKEN "
            "or WEBSHARE_SESSION_FILE."
        )
    return pool


@proxy.command("status")
@click.option("--full", is_flag=True, help="Show every session, not just a summary")
def proxy_status(full: bool):
    """Show availability, recent live health, and per-session state."""
    pool = _require_pool()
    snap = pool.status_snapshot()

    from datetime import datetime
    last_refresh = (
        datetime.fromtimestamp(snap["last_refresh"]).strftime("%Y-%m-%d %H:%M:%S")
        if snap["last_refresh"]
        else "never"
    )

    source_detail = snap["source"]
    if snap.get("session_file"):
        source_detail += f" ({snap['session_file']})"
    from .ledger import log_event
    log_event(
        "proxy_status",
        source=snap["source"],
        total=snap["total"],
        available=snap["available"],
        recently_healthy=snap["recently_healthy"],
        in_flight=snap["in_flight"],
        untested=snap["untested"],
        cooling=snap["cooling"],
        failing=snap["failing"],
        retired=snap["retired"],
        invalid=snap["invalid"],
    )
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
    session_by_id = {session.id: session for session in pool._sessions}
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
        session = session_by_id.get(s["id"])
        display_id = (
            pool.redacted_session_id(session)
            if session is not None
            else "session-unknown"
        )
        t.add_row(
            display_id,
            s["country"] or "-",
            state,
            history,
        )
    console.print(t)


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
        console.print(
            f"[green]Pool now has {n} sessions: "
            f"{pool.available_count()} available, "
            f"{pool.recently_healthy_count()} recently healthy.[/green]"
        )
    except Exception as e:
        _command_error(f"Pool refresh/reload failed: {e}")

    from .ledger import log_event
    log_event(
        "proxy_refresh",
        source=pool.source,
        total=n,
        available=pool.available_count(),
        recently_healthy=pool.recently_healthy_count(),
        remote_rotation_requested=full,
        remote_rotation_error=(
            _whole_word_summary(rotation_error, 500)
            if rotation_error is not None else None
        ),
    )
    if rotation_error is not None:
        _command_error(
            "The session list refreshed, but the requested remote IP rotation failed."
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
    from .ledger import log_event
    from .transcript import probe_pool_session, routing_plan
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
        )
        if sess is None:
            console.print(
                "[yellow]No additional distinct session is available.[/yellow]"
            )
            break
        attempted_session_ids.add(sess.id)
        attempted += 1
        display_id = pool.redacted_session_id(sess)
        log_event(
            "proxy_test_item",
            source=pool.source,
            session=display_id,
            video_id=video_id,
            status="started",
            index=index,
            requested=count,
        )
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
        log_event(
            "proxy_test_item",
            source=pool.source,
            session=display_id,
            video_id=video_id,
            status="passed" if transport_ok else "failed",
            elapsed_seconds=round(elapsed, 3),
            error_type=result.get("error_type"),
            failure_kind=result.get("failure_kind"),
        )
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
    log_event(
        "proxy_test",
        source=pool.source,
        video_id=video_id,
        requested=count,
        attempted=attempted,
        passed=passed,
        failed=failed,
        unattempted=unattempted,
        budget_exhausted=budget_exhausted,
        route_timeout=configured_route_timeout,
        total_timeout=total_timeout,
    )
    console.print(
        f"[bold]Proxy probe summary:[/bold] {passed} passed, {failed} failed, "
        f"{unattempted} unattempted"
    )
    if budget_exhausted:
        console.print("[yellow]The total proxy-test budget was exhausted.[/yellow]")
    if passed == 0:
        _command_error("No proxy session passed the bounded live probe.")
    if failed or unattempted:
        raise click.exceptions.Exit(2)


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
