"""Search, metadata, channel lookup, export, and YouTube scout commands."""

import errno
import json as json_mod
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

import click
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from ..api import FilmotClient
from ..api_contract import FilmotAPIContractError, validate_api_response
from ..cli_support import (
    command_error as _command_error,
    console,
    diagnostic as _diagnostic,
    emit_raw_result as _emit_raw_result,
    prepare_raw_result as _prepare_raw_result,
    route_progress_for as _route_progress_for,
    silence_broken_pipe_streams as _silence_broken_pipe_streams,
    status_context as _search_status,
    transcript_failure_detail as _transcript_failure_detail,
    whole_word_summary as _whole_word_summary,
)
from ..schemas import (
    ChannelResultData,
    CommandResult,
    ErrorDetail,
    ResultStatus,
    SearchResultData,
    VideoResultData,
)


class ExportResultData(TypedDict, total=False):
    """Search payload plus the artifact produced by ``export``."""

    query: str
    result: List[Dict[str, Any]]
    totalresultcount: int
    scope: Dict[str, Any]
    output: str
    format: str
    detailed: bool
    effective_filters: Dict[str, Any]


class SearchAllResultData(TypedDict, total=False):
    """Paginated search outcome consumed by its ledger and renderer."""

    query: str
    result: List[Dict[str, Any]]
    totalresultcount: int
    pages_fetched: int
    partial: bool
    page_error: str
    scope: Dict[str, Any]
    output: Optional[str]
    exported_path: Optional[str]
    format: str
    effective_filters: Dict[str, Any]


class YouTubeSearchResultData(TypedDict, total=False):
    """Recent YouTube discovery outcome and its effective presentation flags."""

    query: str
    videos: List[Dict[str, Any]]
    days: int
    max_results: int
    order: str
    filters: Dict[str, Any]
    transcript: bool
    transcript_query: Optional[str]
    show_description: bool


def _result_videos(results: dict) -> list:
    return results.get("result", results.get("videos", results.get("items", [])))


def _channel_candidates(payload: object) -> list:
    """Normalize the Filmot channel-search response to candidate dictionaries."""
    # The low-level client validates network and cache responses, but this
    # command boundary deliberately validates again.  That keeps mocked,
    # injected, or future alternate clients from turning malformed data into a
    # legitimate-looking empty result.
    validate_api_response("/getsearchchannels", payload)
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


def _pagination_errors(results: dict) -> List[ErrorDetail]:
    """Describe a concrete early-pagination failure on a partial result."""
    page_error = results.get("page_error")
    if not results.get("partial") or not page_error:
        return []
    scope = (
        results.get("scope")
        if isinstance(results.get("scope"), dict)
        else {}
    )
    return [
        ErrorDetail(
            type="PaginationError",
            message=_whole_word_summary(page_error, 500),
            stage="pagination",
            details={
                "pages_fetched": int(
                    scope.get(
                        "pages_fetched",
                        results.get("pages_fetched", 0),
                    )
                    or 0
                ),
                "candidates_fetched": int(
                    scope.get(
                        "candidates_fetched",
                        len(_result_videos(results)),
                    )
                    or 0
                ),
            },
        )
    ]


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


def _parse_bulk_download_spec(spec: str) -> tuple[str, int]:
    """Return the library topic and bounded count from ``TOPIC[:N]``."""
    if ":" in spec:
        topic, count_str = spec.rsplit(":", 1)
        try:
            max_count = int(count_str)
        except ValueError:
            topic = spec
            max_count = 10
    else:
        topic = spec
        max_count = 10
    return topic, max(0, max_count)


def _resolve_search_session(
    session: Optional[str],
    bulk_download: Optional[str],
) -> Optional[str]:
    """Prefer an explicit/environment session, then the bulk-download topic."""
    if session:
        return session
    if bulk_download:
        topic, _ = _parse_bulk_download_spec(bulk_download)
        return topic or None
    return None


@click.command()
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
@click.option(
    "--session",
    default=None,
    envvar="FILMOT_SESSION",
    show_envvar=True,
    help=(
        "Route this search event to a named session; overrides FILMOT_SESSION "
        "and the inferred --bulk-download topic"
    ),
)
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
           session: str, fallback: bool, dedupe: bool, no_proxy: bool):
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
        filmot search "follow-up phrase" --session my-investigation

    Session routing changes only the activity ledger, never search scope. The
    precedence is --session, FILMOT_SESSION, --bulk-download TOPIC, then the
    date-scoped ad-hoc ledger.
    """
    from ..ledger import log_event

    resolved_session = _resolve_search_session(session, bulk_download)
    search_event_logged = False

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
    if page is not None and (pages > 1 or candidate_pool is not None):
        raise click.UsageError(
            "--page cannot be combined with --pages or --candidate-pool"
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
            except FilmotAPIContractError as error:
                log_event(
                    "search",
                    topic=resolved_session,
                    query=query,
                    channel=channel,
                    channel_count=channel_count,
                    status="failed",
                    failure_stage="invalid-response",
                    error=str(error),
                    raw=raw,
                )
                _command_error(
                    str(error),
                    raw=raw,
                    payload=error.as_response() if raw else None,
                    error_type=type(error).__name__,
                    stage="invalid-response",
                )
            except click.ClickException as error:
                log_event(
                    "search",
                    topic=resolved_session,
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
                topic=resolved_session,
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
                    topic=resolved_session,
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

        if resolved_channels:
            rendered = ", ".join(
                f'{item["name"]} ({item["id"]})' for item in resolved_channels
            )
            diagnostics.insert(
                0, f"[cyan]Resolved --channel:[/cyan] {rendered}"
            )

        output_results: SearchResultData = dict(results)
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
        output_results["result"] = output_videos
        output_results["scope"] = dict(
            scope,
            output_count=len(output_videos),
            max_hits=max_hits,
        )
        output_results["effective_filters"] = {
            "channel_id": effective_channel_ids,
            "resolved_channels": resolved_channels,
            "title": title,
            "lang": lang,
        }
        outcome = CommandResult(
            command="search",
            status=(
                ResultStatus.PARTIAL
                if scope["partial"]
                else ResultStatus.COMPLETED
            ),
            data=output_results,
            errors=_pagination_errors(output_results),
        )
        if raw:
            outcome = _prepare_raw_result(outcome)

        # B1/B6c: persist the same typed outcome consumed by raw and human
        # renderers before any pipe-sensitive output. The ledger stores a
        # compact projection, but its command/status/errors/warnings contract
        # comes directly from ``outcome``.
        from ..ledger import log_result
        log_result(
            "search",
            outcome,
            topic=resolved_session,
            data={
                "query": query,
                "effective_query": (
                    client.last_query_rewrite["to"]
                    if client.last_query_rewrite else query
                ),
                "lang": lang,
                "page": page or 1,
                "pages": scope["pages_fetched"],
                "candidate_pool": candidate_pool,
                "category": category,
                "exclude_category": exclude,
                "channel": channel,
                "resolved_channels": resolved_channels or None,
                "channel_id": effective_channel_ids,
                "channel_count": channel_count,
                "title": title,
                "min_views": min_views,
                "max_views": max_views,
                "min_likes": min_likes,
                "max_likes": max_likes,
                "min_duration": min_duration,
                "max_duration": max_duration,
                "start_date": start_date,
                "end_date": end_date,
                "country": country,
                "license": license_type,
                "sort": sort,
                "order": order,
                "manual_subs": manual_subs,
                "max_query_time": max_query_time,
                "hit_format": hit_format,
                "min_matches": min_matches,
                "full": full,
                "context_chars": context_chars,
                "api_total": scope["api_total"],
                "page_count": scope["candidates_fetched"],
                "post_filter_count": scope["post_filter_count"],
                "duplicates_skipped": results.get("duplicates_skipped", 0),
                # Backward-compatible aliases used by the sessions renderer.
                "total": scope["api_total"],
                "results": scope["post_filter_count"],
                "partial": scope["partial"],
                "raw": raw,
                "bulk_download": bulk_download,
                "fallback": fallback,
                "dedupe": dedupe,
                "no_proxy": no_proxy,
                "limit": limit,
                "max_hits": max_hits,
            },
        )
        search_event_logged = True

        # Rendering can encounter EPIPE. The durable event above is already on
        # disk before any of these human diagnostics are emitted.
        for message in diagnostics:
            _diagnostic(message, raw=raw)

        if raw:
            _emit_raw_result(outcome, indent=2)
            return

        if client.last_cache_hit:
            console.print("[dim]Cached response[/dim]")

        # Bulk download mode
        if bulk_download:
            if no_proxy:
                from ..transcript import disable_proxy
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
            outcome,
            query,
            full=full,
            context_chars=context_chars,
            limit=None,
            max_hits=max_hits,
        )

        hint = _freshness_hint(start_date, end_date, query)
        if hint:
            console.print(f"\n{hint}")

    except KeyboardInterrupt:
        if not search_event_logged:
            interrupted_data: SearchResultData = {
                "query": query,
                "result": [],
                "scope": {
                    "api_total": 0,
                    "candidates_fetched": 0,
                    "post_filter_count": 0,
                    "output_count": 0,
                },
                "effective_filters": {
                    "channel_id": channel_id,
                    "title": title,
                    "lang": lang,
                },
            }
            interrupted_outcome = CommandResult(
                command="search",
                status=ResultStatus.INTERRUPTED,
                data=interrupted_data,
                errors=[ErrorDetail(
                    type="KeyboardInterrupt",
                    message="Search interrupted by user",
                    stage="user-interrupt",
                )],
            )
            from ..ledger import log_result
            log_result(
                "search",
                interrupted_outcome,
                topic=resolved_session,
                data={
                    "query": query,
                    "interrupted": True,
                    "failure_stage": "user-interrupt",
                    "raw": raw,
                },
            )
            if raw:
                _emit_raw_result(interrupted_outcome)
                raise click.exceptions.Exit(1)
        raise click.Abort()
    except BrokenPipeError:
        _silence_broken_pipe_streams()
        return
    except OSError as error:
        if error.errno in (errno.EPIPE, errno.EINVAL):
            _silence_broken_pipe_streams()
            return
        log_event(
            "search",
            topic=resolved_session,
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
            topic=resolved_session,
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
            topic=resolved_session,
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
    """Concatenate a video's visible hit text for advisory echo detection."""
    parts = []
    for hit in video.get("hits", []):
        lines = hit.get("lines", [])
        if lines:
            parts.extend(line.get("text", "") for line in lines)
        else:
            parts.append(hit.get("ctx_before", ""))
            parts.append(hit.get("token", ""))
            parts.append(hit.get("ctx_after", ""))
    return " ".join(parts)


def _detect_echo_clusters(videos: list, n: int = 5, threshold: float = 0.5) -> dict:
    """Flag videos whose visible hit phrasing is near-identical.

    Builds word n-gram shingle sets per video and clusters by Jaccard similarity.
    Returns {video_index: cluster_label} only for videos in a cluster of >= 2.
    Convergence (different words, same idea) scores low and is left unflagged;
    shared phrasing scores high and is an advisory lineage candidate, not proof
    of copying or dependence. See research guide §3 (convergence vs echo).
    """
    from ..analysis import analyze_echoes

    analysis = analyze_echoes(
        [
            {
                "source_id": str(index),
                "text": _hit_fingerprint_text(video),
            }
            for index, video in enumerate(videos)
        ],
        ngram=n,
        threshold=threshold,
        include_pair_rows=False,
    )
    mapped = {}
    for label, cluster in enumerate(analysis["clusters"], 1):
        for source_id in cluster["source_ids"]:
            mapped[int(source_id)] = label
    return mapped


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


def _evaluate_transcript_grep(
    result: dict,
    query: str,
    context_chars: int = 160,
) -> CommandResult[dict]:
    """Return one typed grep outcome for both human and raw presentation."""
    from ..channel_dl import (
        _parse_proximity_query,
        _looks_like_proximity,
        _find_grouped_near_matches,
        _find_tilde_matches,
    )

    video_id = result.get("video_id", "")
    text = result.get("full_text", "")
    segments = result.get("segments", [])
    data = {
        "video_id": video_id,
        "language": result.get("language"),
        "query": query,
        "match_count": 0,
        "matches": [],
        "source": result.get("source", "youtube"),
        "route": result.get("route"),
        "routes_tried": result.get("routes_tried"),
    }
    for key in ("library_topic", "library_copy_count"):
        if result.get(key) is not None:
            data[key] = result[key]

    if not isinstance(query, str) or not query.strip():
        message = "grep query must contain non-whitespace text"
        return CommandResult.failed(
            "transcript",
            data,
            ErrorDetail(
                type="InvalidGrepQuery",
                message=message,
                stage="parse-query",
            ),
        )

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
    proximity_marker = bool(
        re.search(r'\bNEAR\s*/|"\s*~', query, re.IGNORECASE)
    )
    if parsed[0] == "plain" and (
        _looks_like_proximity(query) or proximity_marker
    ):
        message = (
            "query has proximity operators but could not be parsed; use "
            '\'"a" NEAR/N "b"\', \'("a"|"b") NEAR/N "c"\', or \'"a b"~N\''
        )
        return CommandResult.failed(
            "transcript",
            data,
            ErrorDetail(
                type="InvalidGrepQuery",
                message=message,
                stage="parse-query",
            ),
        )

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

    rows = []
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
        rows.append({
            "seconds": float(ts),
            "timestamp": _format_timestamp(ts),
            "deep_link": link,
            "excerpt": snippet,
        })
    data["match_count"] = len(rows)
    data["matches"] = rows
    return CommandResult(
        command="transcript",
        status=ResultStatus.COMPLETED if rows else ResultStatus.EMPTY,
        data=data,
    )


def _render_transcript_grep(outcome: CommandResult[dict]) -> None:
    """Render the typed grep result without re-parsing or re-evaluating it."""
    if not outcome.ok:
        message = (
            outcome.errors[0].message
            if outcome.errors
            else "transcript grep failed"
        )
        console.print(f"[red]Error:[/red] {message}")
        return
    query = outcome.data.get("query", "")
    rows = outcome.data.get("matches", [])
    if not rows:
        console.print(f"[dim]No matches for '{query}' in transcript.[/dim]")
        return
    console.print(f"[bold]{len(rows)} match(es) for '{query}':[/bold]\n")
    for row in rows:
        timestamp = row["timestamp"]
        link = row["deep_link"]
        excerpt = row["excerpt"]
        if link:
            console.print(
                f"  [[cyan]{timestamp}[/cyan]] "
                f"[link={link}]{link}[/link] {excerpt}\n"
            )
        else:
            console.print(f"  [[cyan]{timestamp}[/cyan]] {excerpt}\n")


def _grep_transcript(result: dict, query: str, context_chars: int = 160) -> bool:
    """Backward-compatible human wrapper around the typed grep path."""
    outcome = _evaluate_transcript_grep(result, query, context_chars)
    _render_transcript_grep(outcome)
    return outcome.ok


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
        dedupe: If True, skip matching first-500-character fingerprints
        lang: Optional preferred transcript language
    """
    import hashlib
    from ..library import get_library
    from ..ledger import log_event
    from ..transcript import (
        describe_routing_plan,
        get_transcript,
        get_transcript_with_fallback,
        routing_plan,
    )

    # Parse bulk_download format: "topic:10" or just "topic".
    topic, max_count = _parse_bulk_download_spec(bulk_download)

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

            if not full_text.strip():
                log_event(
                    "transcript_save", topic=topic, video_id=video_id,
                    status="skipped", reason="empty_transcript", source="bulk_download",
                )
                console.print(f"  [{i}/{len(videos_to_download)}] [yellow]Skip[/yellow] {video_id} - empty transcript")
                continue

            # Save to library
            metadata = {
                "title": title,
                "channel": channel,
                "channel_id": video.get("channelid"),
                "published_at": video.get("uploaddate"),
                "source": result.get("source", "youtube"),
                "language": result.get("language"),
                "is_generated": result.get("is_generated"),
                "duration_seconds": result.get("duration_seconds"),
                "segment_count": result.get("segment_count"),
                "views": video.get("viewcount"),
                "route": result.get("route"),
                "routes_tried": result.get("routes_tried"),
            }
            library.save(
                video_id=video_id,
                topic=topic,
                transcript_text=full_text,
                metadata=metadata,
                segments=result.get("segments", []),
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
    results,
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
    if isinstance(results, CommandResult):
        results = results.data
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
            f"cluster(s) share near-identical phrasing (possible reuse/common lineage). "
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

@click.command()
@click.argument("video_ids")
@click.option("--flags", "-f", default=None, type=int, help="Flags parameter")
@click.option("--raw", is_flag=True, help="Output one versioned JSON result")
def video(video_ids: str, flags: int, raw: bool):
    """Get metadata for one or more videos.

    VIDEO_IDS can be a single ID or comma-separated list.

    Examples:

        filmot video dQw4w9WgXcQ

        filmot video "dQw4w9WgXcQ,abc123,xyz789"
    """
    from ..ledger import log_event, log_result

    try:
        client = FilmotClient()
        with _search_status("[bold green]Fetching video metadata...", raw=raw):
            result = client.get_videos(video_ids, flags=flags)

        if isinstance(result, dict) and "error" in result:
            log_event(
                "video",
                video_ids=video_ids,
                flags=flags,
                raw=raw,
                status="failed",
                failure_stage="api",
                error=str(result["error"]),
            )
            _command_error(str(result["error"]), raw=raw, payload=result)

        if isinstance(result, list):
            videos = result
        elif isinstance(result, dict) and "result" in result:
            videos = result["result"]
            if not isinstance(videos, list):
                raise ValueError("Video response 'result' must be a list")
        elif isinstance(result, dict):
            videos = [result] if result else []
        elif result is None:
            videos = []
        else:
            raise ValueError("Video response must be an object or list")
        if any(not isinstance(item, dict) for item in videos):
            raise ValueError("Every video response item must be an object")
        videos = [dict(item) for item in videos]
        data: VideoResultData = {
            "video_ids": video_ids,
            "videos": videos,
        }
        outcome = CommandResult(
            command="video",
            status=(
                ResultStatus.COMPLETED
                if videos
                else ResultStatus.EMPTY
            ),
            data=data,
        )
        if raw:
            outcome = _prepare_raw_result(outcome)
        log_result(
            "video",
            outcome,
            data={
                "video_ids": video_ids,
                "flags": flags,
                "results": len(videos),
                "raw": raw,
            },
        )

        if raw:
            _emit_raw_result(outcome, indent=2)
            return

        if client.last_cache_hit:
            console.print("[dim]Cached response[/dim]")

        # Display formatted results
        _display_video_results(outcome)

    except click.exceptions.Exit:
        raise
    except click.ClickException:
        raise
    except ValueError as e:
        log_event(
            "video",
            video_ids=video_ids,
            flags=flags,
            raw=raw,
            status="failed",
            failure_stage="configuration",
            error=f"{type(e).__name__}: {e}",
        )
        _command_error(f"Configuration error: {e}", raw=raw)
    except Exception as e:
        log_event(
            "video",
            video_ids=video_ids,
            flags=flags,
            raw=raw,
            status="failed",
            failure_stage="unexpected",
            error=f"{type(e).__name__}: {e}",
        )
        _command_error(str(e), raw=raw)


def _display_video_results(
    outcome: CommandResult[VideoResultData],
) -> None:
    """Display video metadata from the shared command-result contract."""
    videos = outcome.data.get("videos") or []

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

@click.command()
@click.argument("term")
@click.option("--raw", is_flag=True, help="Output one versioned JSON result")
def channels(term: str, raw: bool):
    """Search for YouTube channels by name or handle.

    Examples:

        filmot channels mrbeast

        filmot channels "Linus Tech Tips"
    """
    from ..ledger import log_event, log_result

    try:
        client = FilmotClient()
        with _search_status(
            f"[bold green]Searching channels for '{term}'...", raw=raw
        ):
            result = client.search_channels(term)

        if isinstance(result, dict) and "error" in result:
            log_event(
                "channels",
                query=term,
                raw=raw,
                status="failed",
                failure_stage="api",
                error=str(result["error"]),
            )
            _command_error(str(result["error"]), raw=raw, payload=result)

        candidates = _channel_candidates(result)
        if any(not isinstance(item, dict) for item in candidates):
            raise ValueError("Every channel response item must be an object")
        candidates = [dict(item) for item in candidates]
        data: ChannelResultData = {
            "query": term,
            "channels": candidates,
        }
        outcome = CommandResult(
            command="channels",
            status=(
                ResultStatus.COMPLETED
                if candidates
                else ResultStatus.EMPTY
            ),
            data=data,
        )
        if raw:
            outcome = _prepare_raw_result(outcome)
        log_result(
            "channels",
            outcome,
            data={
                "query": term,
                "results": len(candidates),
                "raw": raw,
            },
        )

        if raw:
            _emit_raw_result(outcome, indent=2)
            return

        if client.last_cache_hit:
            console.print("[dim]Cached response[/dim]")

        # Display formatted results
        _display_channel_results(outcome)

    except click.exceptions.Exit:
        raise
    except click.ClickException:
        raise
    except FilmotAPIContractError as e:
        log_event(
            "channels",
            query=term,
            raw=raw,
            status="failed",
            failure_stage="invalid-response",
            error=f"{type(e).__name__}: {e}",
        )
        _command_error(
            str(e),
            raw=raw,
            payload=e.as_response() if raw else None,
            error_type=type(e).__name__,
            stage="invalid-response",
        )
    except ValueError as e:
        log_event(
            "channels",
            query=term,
            raw=raw,
            status="failed",
            failure_stage="configuration",
            error=f"{type(e).__name__}: {e}",
        )
        _command_error(f"Configuration error: {e}", raw=raw)
    except Exception as e:
        log_event(
            "channels",
            query=term,
            raw=raw,
            status="failed",
            failure_stage="unexpected",
            error=f"{type(e).__name__}: {e}",
        )
        _command_error(str(e), raw=raw)


def _display_channel_results(
    outcome: CommandResult[ChannelResultData],
) -> None:
    """Display channel results from the shared command-result contract."""
    term = outcome.data.get("query", "")
    channels = outcome.data.get("channels") or []

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


def _render_export_result(
    outcome: CommandResult[ExportResultData],
) -> None:
    """Render the artifact summary from the same result written to the ledger."""
    data = outcome.data
    console.print(
        f"[green]✓ Exported {len(data.get('result') or [])} videos to: "
        f"{data.get('output', '')}[/green]"
    )
    if outcome.status_value == ResultStatus.PARTIAL.value:
        detail = outcome.errors[0].message if outcome.errors else ""
        console.print(
            "[yellow]Partial export:[/yellow] pagination stopped early"
            + (f" ({detail})" if detail else "")
            + "."
        )


@click.command()
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
    from ..export import export_json, export_csv, export_hits_detailed
    from ..ledger import log_event, log_result

    failure_data = {
        "query": query,
        "output": output,
        "format": fmt,
        "detailed": detailed,
        "requested_pages": pages,
        "lang": lang,
        "min_views": min_views,
        "category": category,
    }
    try:
        client = FilmotClient()
        with console.status(
            f"[bold green]Fetching {pages} page(s) for '{query}'..."
        ):
            if pages == 1:
                results = client.search_subtitles(
                    query=query,
                    lang=lang,
                    min_views=min_views,
                    category=category,
                )
            else:
                results = client.search_subtitles_all(
                    query=query,
                    max_pages=pages,
                    lang=lang,
                    min_views=min_views,
                    category=category,
                )
    except ValueError as error:
        log_event(
            "export",
            **failure_data,
            status="failed",
            failure_stage="configuration",
            error=f"{type(error).__name__}: {error}",
        )
        _command_error(f"Configuration error: {error}")
    except Exception as error:
        log_event(
            "export",
            **failure_data,
            status="failed",
            failure_stage="request",
            error=f"{type(error).__name__}: {error}",
        )
        _command_error(str(error))

    if isinstance(results, dict) and "error" in results:
        log_event(
            "export",
            **failure_data,
            status="failed",
            failure_stage="api",
            error=str(results["error"]),
        )
        _command_error(str(results["error"]))

    try:
        if fmt == "json":
            path = export_json(results, output)
        elif detailed:
            path = export_hits_detailed(results, output)
        else:
            path = export_csv(results, output)
    except Exception as error:
        log_event(
            "export",
            **failure_data,
            status="failed",
            failure_stage="export",
            error=f"{type(error).__name__}: {error}",
        )
        _command_error(f"Export failed: {error}")

    videos = _result_videos(results)
    scope = _search_scope(results)
    scope["post_filter_count"] = len(videos)
    scope["output_count"] = len(videos)
    result_data: ExportResultData = {
        "query": query,
        "result": videos,
        "totalresultcount": int(
            results.get("totalresultcount", len(videos)) or 0
        ),
        "scope": scope,
        "output": str(path),
        "format": fmt,
        "detailed": detailed,
        "effective_filters": {
            "lang": lang,
            "min_views": min_views,
            "category": category,
        },
    }
    outcome = CommandResult(
        command="export",
        status=(
            ResultStatus.PARTIAL
            if scope["partial"]
            else ResultStatus.COMPLETED
            if videos
            else ResultStatus.EMPTY
        ),
        data=result_data,
        errors=_pagination_errors(results),
    )
    log_result(
        "export",
        outcome,
        data={
            **failure_data,
            "output": str(path),
            "pages_fetched": scope["pages_fetched"],
            "results": len(videos),
            "api_total": scope["api_total"],
            "partial": scope["partial"],
            "page_error": results.get("page_error"),
        },
    )
    _render_export_result(outcome)


# ========== BATCH OPERATIONS ==========


def _render_search_all_result(
    outcome: CommandResult[SearchAllResultData],
) -> None:
    """Render paginated search/export data from its durable outcome."""
    data = outcome.data
    videos = data.get("result") or []
    total = int(data.get("totalresultcount", len(videos)) or 0)
    pages_fetched = int(data.get("pages_fetched", 1) or 1)
    console.print(
        Panel(
            f"[bold]Fetched {len(videos)} of {total:,} total results "
            f"({pages_fetched} pages)[/bold]"
        )
    )
    if outcome.status_value == ResultStatus.PARTIAL.value:
        detail = outcome.errors[0].message if outcome.errors else ""
        console.print(
            "[yellow]Partial result set:[/yellow] pagination stopped early"
            + (f" ({detail})" if detail else "")
            + "."
        )

    if data.get("output"):
        console.print(
            f"[green]✓ Exported to: {data.get('exported_path', '')}[/green]"
        )
    else:
        _display_subtitle_results(outcome, str(data.get("query", "")))


@click.command("search-all")
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
    from ..export import export_json, export_csv
    from ..ledger import log_event, log_result

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

    scope = _search_scope(results)
    scope["post_filter_count"] = len(videos)
    scope["output_count"] = len(videos)
    result_data: SearchAllResultData = dict(results)
    result_data.update({
        "query": query,
        "result": videos,
        "totalresultcount": total,
        "pages_fetched": pages_fetched,
        "partial": bool(results.get("partial")),
        "scope": scope,
        "output": output,
        "exported_path": (
            str(exported_path)
            if exported_path is not None
            else None
        ),
        "format": fmt,
        "effective_filters": {
            "lang": lang,
            "min_views": min_views,
            "category": category,
            "max_results": max_results,
        },
    })
    outcome = CommandResult(
        command="search-all",
        status=(
            ResultStatus.PARTIAL
            if results.get("partial")
            else ResultStatus.COMPLETED
            if videos
            else ResultStatus.EMPTY
        ),
        data=result_data,
        errors=_pagination_errors(results),
    )
    log_result(
        "search_all",
        outcome,
        data={
            "query": query,
            "lang": lang,
            "category": category,
            "min_views": min_views,
            "requested_pages": pages,
            "pages_fetched": pages_fetched,
            "max_results": max_results,
            "api_total": total,
            "results": len(videos),
            "partial": results.get("partial", False),
            "page_error": results.get("page_error"),
            "output": output,
            "exported_path": (
                str(exported_path)
                if exported_path is not None
                else None
            ),
            "format": fmt,
        },
    )
    _render_search_all_result(outcome)


# ========== TRANSCRIPT DOWNLOAD ==========


def _render_yt_search_result(
    outcome: CommandResult[YouTubeSearchResultData],
) -> None:
    """Render recent YouTube discovery from its shared command outcome."""
    from ..youtube_search import format_duration

    data = outcome.data
    query = str(data.get("query", ""))
    days = int(data.get("days", 0) or 0)
    order = str(data.get("order", "date"))
    videos = data.get("videos") or []
    filters = data.get("filters") or {}

    if not videos:
        console.print(
            f"[yellow]No videos found for '{query}' in the last {days} "
            "days.[/yellow]"
        )
        return

    filter_labels = []
    for label, key in (
        ("Region", "region"),
        ("Lang", "lang"),
        ("Duration", "duration"),
        ("Definition", "definition"),
        ("Caption", "caption"),
        ("Event", "event_type"),
    ):
        if filters.get(key):
            filter_labels.append(f"{label}: {filters[key]}")
    filter_text = " | ".join(filter_labels)

    console.print(
        Panel(
            f"[bold]Found {len(videos)} videos for:[/bold] {query}\n"
            f"[bold]Period:[/bold] Last {days} days | "
            f"[bold]Order:[/bold] {order}"
            + (
                f"\n[bold]Filters:[/bold] {filter_text}"
                if filter_text
                else ""
            ),
            title="YouTube Search Results",
        )
    )

    for index, video in enumerate(videos, 1):
        duration_str = format_duration(video.get("duration", ""))
        views = f"{video.get('views', 0):,}"
        published = str(video.get("published_at", ""))[:10]

        console.print(
            f"\n[bold cyan]{index}. {video['title']}[/bold cyan]"
        )
        console.print(
            f"   Channel: [green]{video['channel_title']}[/green]"
        )
        console.print(
            f"   Views: {views} | Duration: {duration_str} | "
            f"Published: {published}"
        )
        video_url = video.get("url") or (
            f"https://youtube.com/watch?v={video.get('video_id', '')}"
        )
        console.print(f"   [link={video_url}]{video_url}[/link]")

        if data.get("show_description") and video.get("description"):
            description = str(video["description"])[:200]
            if len(str(video.get("description", ""))) > 200:
                description += "..."
            console.print(f"   [dim]{description}[/dim]")

        if data.get("transcript"):
            transcript_result = video.get("transcript_search") or {}
            search_term = transcript_result.get("query") or (
                data.get("transcript_query") or query
            )
            if transcript_result.get("status") == "failed":
                error = transcript_result.get("error") or {}
                error_type = error.get("type") or "TranscriptError"
                console.print(
                    f"   [dim]Transcript unavailable ({error_type})[/dim]"
                )
            elif transcript_result.get("match_count", 0) > 0:
                console.print(
                    f"   [bold green]✓ Found "
                    f"{transcript_result['match_count']} transcript matches "
                    f"for '{search_term}'[/bold green]"
                )
                for match in transcript_result.get("matches", [])[:3]:
                    console.print(
                        f"      [{match.get('timestamp', '?')}] ..."
                        f"{str(match.get('context', ''))[:100]}..."
                    )
            else:
                console.print(
                    f"   [dim]No transcript matches for "
                    f"'{search_term}'[/dim]"
                )


def _evaluate_yt_search_transcripts(
    videos: List[Dict[str, Any]],
    search_term: str,
    *,
    raw: bool,
) -> tuple[List[Dict[str, Any]], List[ErrorDetail]]:
    """Attach the same bounded transcript-search result for both renderers."""
    from ..transcript import search_in_transcript

    enriched = []
    errors = []
    for video in videos:
        item = dict(video)
        video_id = str(item.get("video_id") or "")
        transcript_data: Dict[str, Any] = {
            "query": search_term,
            "status": ResultStatus.EMPTY.value,
            "match_count": 0,
            "matches": [],
        }
        try:
            with _search_status(
                f"Fetching transcript for {video_id}...",
                raw=raw,
            ):
                result = search_in_transcript(video_id, search_term)
            if not isinstance(result, dict):
                raise TypeError("transcript search returned a non-object result")
            if result.get("error"):
                error_type = str(
                    result.get("error_type") or "TranscriptUnavailable"
                )
                message = _whole_word_summary(result.get("error"), 500)
                transcript_data.update({
                    "status": ResultStatus.FAILED.value,
                    "error": {"type": error_type, "message": message},
                })
                errors.append(ErrorDetail(
                    type=error_type,
                    message=message,
                    stage="transcript-search",
                    details={"video_id": video_id},
                ))
            else:
                matches = result.get("matches")
                if not isinstance(matches, list):
                    raise TypeError("transcript search matches must be a list")
                match_rows = [dict(match) for match in matches if isinstance(match, dict)]
                transcript_data.update({
                    "status": (
                        ResultStatus.COMPLETED.value
                        if match_rows
                        else ResultStatus.EMPTY.value
                    ),
                    "match_count": len(match_rows),
                    "matches": match_rows,
                    "language": result.get("language"),
                    "is_generated": result.get("is_generated"),
                })
        except Exception as error:
            error_type = type(error).__name__
            message = _whole_word_summary(error, 500)
            transcript_data.update({
                "status": ResultStatus.FAILED.value,
                "error": {"type": error_type, "message": message},
            })
            errors.append(ErrorDetail(
                type=error_type,
                message=message,
                stage="transcript-search",
                details={"video_id": video_id},
            ))
        item["transcript_search"] = transcript_data
        enriched.append(item)
    return enriched, errors


@click.command("yt-search")
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
@click.option("--raw", is_flag=True, help="Output one versioned JSON result")
def yt_search(query: str, days: int, max_results: int, order: str,
              published_after: str, published_before: str, channel_id: str,
              region: str, lang: str, safe_search: str, caption: str,
              category: str, definition: str, dimension: str, duration: str,
              embeddable: bool, video_license: str, syndicated: bool,
              video_type: str, event_type: str, location: str,
              location_radius: str, topic_id: str, transcript: bool,
              transcript_query: str, show_description: bool, raw: bool):
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
    from ..ledger import log_event, log_result
    from ..youtube_search import search_recent, validate_youtube_api

    filters = {
        "lang": lang,
        "region": region,
        "published_after": published_after,
        "published_before": published_before,
        "channel_id": channel_id,
        "safe_search": safe_search,
        "caption": caption,
        "category": category,
        "definition": definition,
        "dimension": dimension,
        "duration": duration,
        "embeddable": embeddable,
        "license": video_license,
        "syndicated": syndicated,
        "video_type": video_type,
        "event_type": event_type,
        "location": location,
        "location_radius": location_radius,
        "topic_id": topic_id,
        "max_results": max_results,
    }
    event_data = {
        "query": query,
        "days": days,
        "order": order,
        **filters,
        "transcript": transcript,
        "transcript_query": transcript_query,
        "raw": raw,
    }

    try:
        validate_youtube_api()
        pub_after = (
            f"{published_after}T00:00:00Z"
            if published_after
            else None
        )
        pub_before = (
            f"{published_before}T23:59:59Z"
            if published_before
            else None
        )
        with _search_status(
            f"[bold green]Searching YouTube for '{query}' "
            f"(last {days} days)...",
            raw=raw,
        ):
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
    except ValueError as error:
        log_event(
            "yt-search",
            **event_data,
            status="failed",
            failure_stage="configuration",
            error=f"{type(error).__name__}: {error}",
        )
        _command_error(
            f"Configuration error: {error}. Add YOUTUBE_API_KEY to your "
            ".env file.",
            raw=raw,
            error_type=type(error).__name__,
            stage="configuration",
        )
    except Exception as error:
        log_event(
            "yt-search",
            **event_data,
            status="failed",
            failure_stage="request",
            error=f"{type(error).__name__}: {error}",
        )
        _command_error(
            str(error),
            raw=raw,
            error_type=type(error).__name__,
            stage="request",
        )

    result_videos = list(results or [])
    transcript_errors: List[ErrorDetail] = []
    if transcript and result_videos:
        result_videos, transcript_errors = _evaluate_yt_search_transcripts(
            result_videos,
            str(transcript_query or query),
            raw=raw,
        )

    result_data: YouTubeSearchResultData = {
        "query": query,
        "videos": result_videos,
        "days": days,
        "max_results": max_results,
        "order": order,
        "filters": filters,
        "transcript": transcript,
        "transcript_query": transcript_query,
        "show_description": show_description,
    }
    outcome = CommandResult(
        command="yt-search",
        status=(
            ResultStatus.PARTIAL
            if transcript_errors
            else ResultStatus.COMPLETED
            if result_videos
            else ResultStatus.EMPTY
        ),
        data=result_data,
        errors=transcript_errors,
    )
    if raw:
        outcome = _prepare_raw_result(outcome)
    log_result(
        "yt-search",
        outcome,
        data={
            **event_data,
            "results": len(result_videos),
            "transcript_failures": len(transcript_errors),
        },
    )
    if raw:
        _emit_raw_result(outcome, indent=2)
        return
    _render_yt_search_result(outcome)


# ========== TRANSCRIPT LIBRARY ==========
