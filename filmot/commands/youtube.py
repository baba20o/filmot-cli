"""Bounded YouTube resource commands for curated research paths."""

from __future__ import annotations

import shlex
from typing import Any, Dict, List, Optional

import click
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..cli_support import (
    command_error as _command_error,
    console,
    emit_raw_result as _emit_raw_result,
    prepare_raw_result as _prepare_raw_result,
    status_context as _status,
    whole_word_summary as _whole_word_summary,
)
from ..schemas import (
    CommandResult,
    ErrorDetail,
    ResultStatus,
    YouTubePlaylistResultData,
    YouTubePlaylistShelfResultData,
)


DEFAULT_PLAYLIST_PAGES = 1
DEFAULT_PLAYLIST_RESULTS = 25
MAX_PLAYLIST_PAGES = 100
MAX_PLAYLIST_RESULTS = 5000
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 20.0
DEFAULT_RETRIES = 2


def _display(value: object, fallback: str = "") -> str:
    """Escape provider-controlled text before Rich markup rendering."""
    if value is None or value == "":
        value = fallback
    return escape(str(value))


def _object(value: object, name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("YouTube playlist provider {} must be an object".format(name))
    return dict(value)


def _optional_object(value: object, name: str) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    return _object(value, name)


def _object_list(value: object, name: str) -> List[Dict[str, Any]]:
    if not isinstance(value, list) or any(
        not isinstance(item, dict) for item in value
    ):
        raise TypeError(
            "YouTube playlist provider {} must be a list of objects".format(
                name
            )
        )
    return [dict(item) for item in value]


def _string_list(value: object, name: str) -> List[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise TypeError(
            "YouTube playlist provider {} must be a list of strings".format(
                name
            )
        )
    return [_whole_word_summary(item, 500) for item in value]


def _required_keys(
    result: Dict[str, Any], names: tuple[str, ...], provider_name: str
) -> None:
    missing = [name for name in names if name not in result]
    if missing:
        raise TypeError(
            "{} omitted required field(s): {}".format(
                provider_name, ", ".join(missing)
            )
        )


def _timestamps(result: Dict[str, Any], provider_name: str) -> None:
    for name in ("observed_at", "expires_at"):
        value = result.get(name)
        if value is not None and not isinstance(value, str):
            raise TypeError(
                "{} {} must be text or null".format(provider_name, name)
            )


def _validated_page_token(
    value: Optional[str], *, command: str, raw: bool
) -> Optional[str]:
    """Reject a blank continuation before configuration, logging, or quota use."""
    if value is None:
        return None
    normalized = value.strip()
    invalid = not normalized
    value = ""
    if invalid:
        message = "--page-token must contain a non-whitespace YouTube token"
        if raw:
            _command_error(
                message,
                command=command,
                raw=True,
                error_type="BadParameter",
                stage="validate-request",
            )
        raise click.BadParameter(message, param_hint="--page-token")
    return normalized


def _normalize_playlist_result(
    value: object, *, expected_playlist_id: str
) -> Dict[str, Any]:
    """Validate the playlist provider boundary before logging or rendering."""
    if not isinstance(value, dict):
        raise TypeError("YouTube playlist provider returned a non-object result")
    result = dict(value)
    _required_keys(
        result,
        (
            "provider",
            "playlist",
            "playlist_items",
            "videos",
            "video_id_outcomes",
            "request",
            "coverage",
            "warnings",
            "errors",
            "api_calls",
        ),
        "YouTube playlist provider",
    )
    provider = result.get("provider")
    if provider != "youtube-data-api-v3":
        raise TypeError(
            "YouTube playlist provider must identify youtube-data-api-v3"
        )
    result["playlist"] = _optional_object(result.get("playlist"), "playlist")
    if result["playlist"] is not None and not isinstance(
        result["playlist"].get("playlist_id"), str
    ):
        raise TypeError(
            "YouTube playlist provider playlist must contain playlist_id text"
        )
    if (
        result["playlist"] is not None
        and result["playlist"].get("playlist_id") != expected_playlist_id
    ):
        raise TypeError(
            "YouTube playlist provider returned an unexpected playlist identity"
        )
    result["playlist_items"] = _object_list(
        result.get("playlist_items"), "playlist_items"
    )
    result["videos"] = _object_list(result.get("videos"), "videos")
    result["video_id_outcomes"] = _object_list(
        result.get("video_id_outcomes"), "video_id_outcomes"
    )
    from ..discovery import youtube_video_id

    item_video_ids = set()
    for item in result["playlist_items"]:
        if item.get("playlist_id") != expected_playlist_id:
            raise TypeError(
                "YouTube playlist provider returned an unexpected item identity"
            )
        item_video_id = item.get("video_id")
        if item_video_id is None:
            continue
        if (
            not isinstance(item_video_id, str)
            or youtube_video_id(item_video_id) != item_video_id
        ):
            raise TypeError(
                "YouTube playlist provider items must contain exact YouTube "
                "video IDs or null"
            )
        item_video_ids.add(item_video_id)

    observed_video_ids = set()

    for video in result["videos"]:
        video_id = video.get("video_id")
        if (
            not isinstance(video_id, str)
            or youtube_video_id(video_id) != video_id
        ):
            raise TypeError(
                "YouTube playlist provider videos must contain exact YouTube "
                "video IDs"
            )
        if video_id not in item_video_ids or video_id in observed_video_ids:
            raise TypeError(
                "YouTube playlist provider videos must uniquely belong to "
                "the requested playlist slice"
            )
        observed_video_ids.add(video_id)

    outcome_ids = set()
    outcome_observed_ids = set()
    for item in result["video_id_outcomes"]:
        video_id = item.get("video_id")
        status = item.get("status")
        if (
            not isinstance(video_id, str)
            or youtube_video_id(video_id) != video_id
            or video_id not in item_video_ids
            or video_id in outcome_ids
        ):
            raise TypeError(
                "YouTube playlist provider outcomes must uniquely cover "
                "playlist video IDs"
            )
        if status not in {"observed", "not_returned", "unprocessed"}:
            raise TypeError(
                "YouTube playlist provider outcomes contain an unknown status"
            )
        outcome_ids.add(video_id)
        if status == "observed":
            outcome_observed_ids.add(video_id)
    if outcome_ids != item_video_ids:
        raise TypeError(
            "YouTube playlist provider outcomes must cover every playlist "
            "video ID"
        )
    if outcome_observed_ids != observed_video_ids:
        raise TypeError(
            "YouTube playlist provider observed outcomes must match videos"
        )

    for name in ("request", "coverage", "api_calls"):
        result[name] = _object(result.get(name), name)
    if result["request"].get("playlist_id") != expected_playlist_id:
        raise TypeError(
            "YouTube playlist provider returned an unexpected request identity"
        )
    partial = result["coverage"].get("partial")
    if not isinstance(partial, bool):
        raise TypeError(
            "YouTube playlist provider coverage.partial must be boolean"
        )
    stopping_reason = result["coverage"].get("stopping_reason")
    if not isinstance(stopping_reason, str) or not stopping_reason.strip():
        raise TypeError(
            "YouTube playlist provider stopping_reason must be non-empty text"
        )
    playlist_returned = result["coverage"].get("playlist_returned")
    if (
        not isinstance(playlist_returned, bool)
        or playlist_returned != (result["playlist"] is not None)
    ):
        raise TypeError(
            "YouTube playlist provider playlist_returned contradicts playlist"
        )
    next_token = result["coverage"].get("next_page_token")
    if next_token is not None and not isinstance(next_token, str):
        raise TypeError(
            "YouTube playlist provider next_page_token must be text or null"
        )
    if isinstance(next_token, str) and not next_token.strip():
        raise TypeError(
            "YouTube playlist provider next_page_token cannot be blank"
        )
    result["warnings"] = _string_list(result.get("warnings"), "warnings")
    result["errors"] = _object_list(result.get("errors"), "errors")
    if result["playlist"] is None and any(
        (
            result["playlist_items"],
            result["videos"],
            result["video_id_outcomes"],
            next_token,
        )
    ):
        raise TypeError(
            "YouTube playlist provider returned rows without the requested "
            "playlist"
        )
    _timestamps(result, "YouTube playlist provider")
    return result


def _normalize_playlist_shelf_result(
    value: object, *, expected_channel_reference: str
) -> Dict[str, Any]:
    """Validate the playlist-shelf provider boundary before use."""
    if not isinstance(value, dict):
        raise TypeError(
            "YouTube channel playlist provider returned a non-object result"
        )
    result = dict(value)
    _required_keys(
        result,
        (
            "provider",
            "channel",
            "playlists",
            "request",
            "coverage",
            "warnings",
            "errors",
            "api_calls",
        ),
        "YouTube channel playlist provider",
    )
    provider = result.get("provider")
    if provider != "youtube-data-api-v3":
        raise TypeError(
            "YouTube channel playlist provider must identify "
            "youtube-data-api-v3"
        )
    from ..channel_dl import _parse_channel_reference
    from ..youtube_resources import youtube_playlist_id

    result["channel"] = _optional_object(result.get("channel"), "channel")
    channel_id = None
    if result["channel"] is not None:
        channel_id = result["channel"].get("channel_id")
        try:
            parsed_name, parsed_value = _parse_channel_reference(channel_id)
        except (TypeError, ValueError):
            parsed_name, parsed_value = None, None
        if parsed_name != "id" or parsed_value != channel_id:
            raise TypeError(
                "YouTube channel playlist provider channel must contain an "
                "exact channel ID"
            )
        if (
            expected_channel_reference.startswith("UC")
            and channel_id != expected_channel_reference
        ):
            raise TypeError(
                "YouTube channel playlist provider returned an unexpected "
                "channel identity"
            )
    result["playlists"] = _object_list(result.get("playlists"), "playlists")
    for playlist in result["playlists"]:
        playlist_id = playlist.get("playlist_id")
        if (
            not isinstance(playlist_id, str)
            or youtube_playlist_id(playlist_id) != playlist_id
        ):
            raise TypeError(
                "YouTube channel playlist provider playlists must contain an "
                "exact playlist ID"
            )
        playlist_channel_id = playlist.get("channel_id")
        if (
            playlist_channel_id is not None
            and playlist_channel_id != channel_id
        ):
            raise TypeError(
                "YouTube channel playlist provider returned a playlist for "
                "another channel"
            )
    for name in ("request", "coverage", "api_calls"):
        result[name] = _object(result.get(name), name)
    if (
        result["request"].get("channel_reference")
        != expected_channel_reference
    ):
        raise TypeError(
            "YouTube channel playlist provider returned an unexpected "
            "request identity"
        )
    if (
        channel_id is not None
        and result["request"].get("channel_id") != channel_id
    ):
        raise TypeError(
            "YouTube channel playlist provider request does not match its "
            "channel"
        )
    partial = result["coverage"].get("partial")
    if not isinstance(partial, bool):
        raise TypeError(
            "YouTube channel playlist provider coverage.partial must be boolean"
        )
    stopping_reason = result["coverage"].get("stopping_reason")
    if not isinstance(stopping_reason, str) or not stopping_reason.strip():
        raise TypeError(
            "YouTube channel playlist provider stopping_reason must be "
            "non-empty text"
        )
    channel_returned = result["coverage"].get("channel_returned")
    if (
        not isinstance(channel_returned, bool)
        or channel_returned != (result["channel"] is not None)
    ):
        raise TypeError(
            "YouTube channel playlist provider channel_returned contradicts "
            "channel"
        )
    next_token = result["coverage"].get("next_page_token")
    if next_token is not None and not isinstance(next_token, str):
        raise TypeError(
            "YouTube channel playlist provider next_page_token must be text "
            "or null"
        )
    if isinstance(next_token, str) and not next_token.strip():
        raise TypeError(
            "YouTube channel playlist provider next_page_token cannot be blank"
        )
    result["warnings"] = _string_list(result.get("warnings"), "warnings")
    result["errors"] = _object_list(result.get("errors"), "errors")
    if result["channel"] is None and (result["playlists"] or next_token):
        raise TypeError(
            "YouTube channel playlist provider returned rows without the "
            "requested channel"
        )
    _timestamps(result, "YouTube channel playlist provider")
    return result


def _youtube_errors(value: object) -> List[ErrorDetail]:
    """Project provider errors into Filmot's shared typed result contract."""
    errors = _object_list(value, "errors")
    output = []
    for error in errors:
        output.append(ErrorDetail(
            type=str(error.get("type") or "YouTubeAPIError"),
            message=_whole_word_summary(
                error.get("message")
                or "YouTube playlist request was incomplete",
                500,
            ),
            stage=str(error.get("stage") or "youtube-api"),
            details={
                name: item
                for name, item in error.items()
                if name not in {"type", "message", "stage"}
            },
        ))
    return output


def _canonical_channel_reference(value: str) -> str:
    """Return the strict canonical identity without retaining rejected input."""
    from ..channel_dl import _parse_channel_reference

    failure = None
    filter_name = None
    filter_value = None
    try:
        filter_name, filter_value = _parse_channel_reference(value)
    except ValueError as error:
        failure = str(error)
    value = ""
    if failure is not None:
        raise ValueError(failure)
    if filter_name == "id":
        return str(filter_value)
    return "@{}".format(filter_value)


def _continuation(
    command: str,
    identity: str,
    coverage: Dict[str, Any],
    *,
    pages: int,
    max_results: int,
    connect_timeout: float,
    read_timeout: float,
    retries: int,
    show_description: bool,
    raw: bool,
) -> Dict[str, Any]:
    """Build one directly executable continuation with matching controls."""
    token = coverage.get("next_page_token")
    available = isinstance(token, str) and bool(token)
    argv: List[str] = []
    if available:
        argv = [
            "filmot",
            command,
            identity,
            "--page-token",
            token,
            "--pages",
            str(pages),
            "--max-results",
            str(max_results),
        ]
        if connect_timeout != DEFAULT_CONNECT_TIMEOUT:
            argv.extend(["--connect-timeout", str(connect_timeout)])
        if read_timeout != DEFAULT_READ_TIMEOUT:
            argv.extend(["--read-timeout", str(read_timeout)])
        if retries != DEFAULT_RETRIES:
            argv.extend(["--retries", str(retries)])
        if show_description:
            argv.append("--show-description")
        if raw:
            argv.append("--raw")
    return {
        "available": available,
        "next_page_token": token if available else None,
        "argv": argv,
    }


def _status_for_playlist(
    playlist: Optional[Dict[str, Any]],
    playlist_items: List[Dict[str, Any]],
    coverage: Dict[str, Any],
    errors: List[ErrorDetail],
) -> ResultStatus:
    if playlist is None:
        return ResultStatus.EMPTY
    if errors or coverage.get("partial"):
        return ResultStatus.PARTIAL
    return ResultStatus.COMPLETED if playlist_items else ResultStatus.EMPTY


def _status_for_shelf(
    channel: Optional[Dict[str, Any]],
    playlists: List[Dict[str, Any]],
    coverage: Dict[str, Any],
    errors: List[ErrorDetail],
) -> ResultStatus:
    if channel is None:
        return ResultStatus.EMPTY
    if errors or coverage.get("partial"):
        return ResultStatus.PARTIAL
    return ResultStatus.COMPLETED if playlists else ResultStatus.EMPTY


def _render_warnings_and_errors(outcome: CommandResult) -> None:
    for warning in outcome.warnings:
        console.print(
            "[yellow]Warning: {}[/yellow]".format(_display(warning))
        )
    for error in outcome.errors:
        console.print(
            "[yellow]{}: {}[/yellow]".format(
                _display(error.stage, "YouTube API"),
                _display(error.message),
            )
        )


def _render_continuation(continuation: Dict[str, Any], noun: str) -> None:
    if not continuation.get("available"):
        return
    argv = [str(item) for item in continuation.get("argv") or []]
    console.print(
        "\n[yellow]More {} are available. Continue with:[/yellow]".format(
            noun
        )
    )
    console.print(Text(shlex.join(argv), style="bold"), soft_wrap=True)


def _render_playlist(outcome: CommandResult[YouTubePlaylistResultData]) -> None:
    data = outcome.data
    playlist = data.get("playlist")
    coverage = data.get("coverage") or {}
    items = data.get("playlist_items") or []
    videos = data.get("videos") or []
    continuation = data.get("continuation") or {}

    if playlist is None:
        console.print(
            "[yellow]No public playlist metadata was returned. YouTube did "
            "not establish an availability reason.[/yellow]"
        )
        _render_warnings_and_errors(outcome)
        return

    declared_count = playlist.get("item_count")
    declared = (
        f"{declared_count:,}"
        if isinstance(declared_count, int)
        else "unknown"
    )
    api_calls = data.get("api_calls") or {}
    console.print(Panel(
        "[bold]{}[/bold]\n"
        "Channel: {} | Privacy: {} | Declared items: {}\n"
        "Slice: {} item(s), {} current video resource(s) | "
        "Pages: {}/{} | API attempts: {} | Stopped: {}".format(
            _display(playlist.get("title"), "Untitled playlist"),
            _display(playlist.get("channel_title"), "Unknown channel"),
            _display(playlist.get("privacy_status"), "unknown"),
            declared,
            len(items),
            len(videos),
            coverage.get("pages_fetched", 0),
            coverage.get("pages_attempted", 0),
            api_calls.get("total", coverage.get("api_attempts", 0)),
            _display(coverage.get("stopping_reason"), "not recorded"),
        ),
        title="YouTube Playlist",
        border_style=(
            "yellow"
            if outcome.status_value == ResultStatus.PARTIAL.value
            else "blue"
        ),
    ))

    by_video_id = {
        video.get("video_id"): video
        for video in videos
        if isinstance(video.get("video_id"), str)
    }
    from ..youtube_search import format_duration

    for index, item in enumerate(items, 1):
        video_id = item.get("video_id")
        video = by_video_id.get(video_id, {})
        position = item.get("playlist_position")
        number = position + 1 if isinstance(position, int) else index
        title = (
            video.get("title")
            or item.get("playlist_item_title")
            or "Untitled playlist item"
        )
        console.print(
            "\n[bold cyan]{}. {}[/bold cyan]".format(
                number, _display(title)
            )
        )
        channel = (
            video.get("channel_title")
            or item.get("video_owner_channel_title")
            or "Unknown channel"
        )
        published = str(
            video.get("published_at")
            or item.get("video_published_at")
            or "unknown"
        )[:10]
        views = video.get("views")
        duration = video.get("duration")
        console.print(
            "   Channel: [green]{}[/green] | Published: {} | Views: {} | "
            "Duration: {}".format(
                _display(channel),
                _display(published),
                f"{views:,}" if isinstance(views, int) else "unknown",
                _display(
                    format_duration(duration)
                    if isinstance(duration, str)
                    else "",
                    "unknown",
                ),
            )
        )
        if isinstance(video_id, str) and video_id:
            url = "https://youtube.com/watch?v={}".format(video_id)
            console.print("   [link={0}]{0}[/link]".format(url))
        else:
            console.print("   [dim]No usable video ID was exposed.[/dim]")
        metadata_status = item.get("video_metadata_status")
        if metadata_status not in (None, "observed"):
            console.print(
                "   [dim]Video metadata: {}[/dim]".format(
                    _display(metadata_status)
                )
            )
        if data.get("show_description"):
            description = (
                video.get("description")
                or item.get("playlist_item_description")
            )
            if description:
                text = str(description)
                console.print(
                    "   [dim]{}[/dim]".format(
                        _display(
                            text[:500] + ("..." if len(text) > 500 else "")
                        )
                    )
                )

    if not items:
        console.print("[dim]No playlist items were returned in this slice.[/dim]")
    _render_warnings_and_errors(outcome)
    _render_continuation(continuation, "playlist items")


def _render_playlist_shelf(
    outcome: CommandResult[YouTubePlaylistShelfResultData],
) -> None:
    data = outcome.data
    channel = data.get("channel")
    playlists = data.get("playlists") or []
    coverage = data.get("coverage") or {}
    continuation = data.get("continuation") or {}
    if channel is None:
        console.print(
            "[yellow]No public channel metadata was returned. YouTube did "
            "not establish an availability reason.[/yellow]"
        )
        _render_warnings_and_errors(outcome)
        return

    approximate = coverage.get("approximate_total")
    approximate_label = (
        f"~{approximate:,}" if isinstance(approximate, int) else "unknown"
    )
    api_calls = data.get("api_calls") or {}
    console.print(Panel(
        "[bold]{}[/bold]\n"
        "Returned: {} public playlist(s) | Approx. total: {} | "
        "Pages: {}/{} | API attempts: {} | Stopped: {}".format(
            _display(channel.get("title"), "Unknown channel"),
            len(playlists),
            approximate_label,
            coverage.get("pages_fetched", 0),
            coverage.get("pages_attempted", 0),
            api_calls.get("total", coverage.get("api_attempts", 0)),
            _display(coverage.get("stopping_reason"), "not recorded"),
        ),
        title="YouTube Channel Playlists",
        border_style=(
            "yellow"
            if outcome.status_value == ResultStatus.PARTIAL.value
            else "blue"
        ),
    ))
    if playlists:
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("#", justify="right", style="dim")
        table.add_column("Playlist")
        table.add_column("Items", justify="right")
        table.add_column("Privacy")
        table.add_column("ID", style="dim")
        for index, playlist in enumerate(playlists, 1):
            item_count = playlist.get("item_count")
            table.add_row(
                str(index),
                Text(str(playlist.get("title") or "Untitled playlist")),
                f"{item_count:,}" if isinstance(item_count, int) else "?",
                Text(str(playlist.get("privacy_status") or "unknown")),
                Text(str(playlist.get("playlist_id") or "")),
            )
            if data.get("show_description") and playlist.get("description"):
                text = str(playlist["description"])
                table.add_row(
                    "",
                    Text(
                        text[:300] + ("..." if len(text) > 300 else ""),
                        style="dim",
                    ),
                    "",
                    "",
                    "",
                )
        console.print(table)
    else:
        console.print("[dim]No public playlists were returned in this slice.[/dim]")
    _render_warnings_and_errors(outcome)
    _render_continuation(continuation, "playlists")


def _playlist_options(function):
    """Apply identical bounded transport controls to both playlist commands."""
    options = (
        click.option(
            "--pages",
            default=DEFAULT_PLAYLIST_PAGES,
            type=click.IntRange(1, MAX_PLAYLIST_PAGES),
            show_default=True,
            help="Maximum API pages to fetch",
        ),
        click.option(
            "--max-results",
            "-n",
            default=DEFAULT_PLAYLIST_RESULTS,
            type=click.IntRange(1, MAX_PLAYLIST_RESULTS),
            show_default=True,
            help="Playlist-row budget across pages",
        ),
        click.option(
            "--page-token",
            default=None,
            help="Resume from an opaque YouTube nextPageToken",
        ),
        click.option(
            "--connect-timeout",
            default=DEFAULT_CONNECT_TIMEOUT,
            type=click.FloatRange(min=0, min_open=True),
            show_default=True,
            help="Per-request connection timeout in seconds",
        ),
        click.option(
            "--read-timeout",
            default=DEFAULT_READ_TIMEOUT,
            type=click.FloatRange(min=0, min_open=True),
            show_default=True,
            help="Per-request response timeout in seconds",
        ),
        click.option(
            "--retries",
            default=DEFAULT_RETRIES,
            type=click.IntRange(0, 5),
            show_default=True,
            help="Retries for transient failures",
        ),
        click.option(
            "--show-description",
            is_flag=True,
            help="Show playlist or video descriptions",
        ),
        click.option("--raw", is_flag=True, help="Output one versioned JSON result"),
    )
    for option in reversed(options):
        function = option(function)
    return function


@click.command("yt-playlist")
@click.argument("playlist")
@_playlist_options
def yt_playlist(
    playlist: str,
    pages: int,
    max_results: int,
    page_token: Optional[str],
    connect_timeout: float,
    read_timeout: float,
    retries: int,
    show_description: bool,
    raw: bool,
) -> None:
    """Inspect one exact public YouTube playlist and its video metadata.

    PLAYLIST may be a bare playlist ID or an HTTPS YouTube URL containing one.
    The default one-page, 25-item slice is deliberately small. Raw output keeps
    a pipeline-compatible ``videos`` array for ``filmot download``.

    \b
      filmot yt-playlist PLAYLIST_ID
      filmot yt-playlist "https://youtube.com/playlist?list=PLAYLIST_ID" --raw
    """
    from ..ledger import log_event, log_result
    from ..youtube_resources import (
        YouTubeAPIError,
        get_playlist_detailed,
        youtube_playlist_id,
    )

    playlist_id = youtube_playlist_id(playlist)
    playlist = ""
    if playlist_id is None:
        message = "expected a playlist ID or supported HTTPS YouTube URL"
        if raw:
            _command_error(
                message,
                command="yt-playlist",
                raw=True,
                error_type="BadParameter",
                stage="validate-request",
            )
        raise click.BadParameter(message, param_hint="PLAYLIST")
    page_token = _validated_page_token(
        page_token, command="yt-playlist", raw=raw
    )

    event_data = {
        "playlist_id": playlist_id,
        "pages": pages,
        "max_results": max_results,
        "page_token": page_token,
        "connect_timeout": connect_timeout,
        "read_timeout": read_timeout,
        "retries": retries,
        "raw": raw,
    }
    try:
        with _status(
            "[bold green]Reading YouTube playlist {}...".format(playlist_id),
            raw=raw,
        ):
            provider = get_playlist_detailed(
                playlist_id,
                max_pages=pages,
                max_results=max_results,
                page_token=page_token,
                timeout=(connect_timeout, read_timeout),
                retries=retries,
            )
        provider = _normalize_playlist_result(
            provider, expected_playlist_id=playlist_id
        )
    except ValueError as error:
        log_event(
            "yt-playlist",
            **event_data,
            status="failed",
            failure_stage="configuration",
            error="{}: {}".format(
                type(error).__name__, _whole_word_summary(error, 500)
            ),
        )
        _command_error(
            str(error),
            command="yt-playlist",
            raw=raw,
            error_type=type(error).__name__,
            stage="configuration",
        )
    except YouTubeAPIError as error:
        log_event(
            "yt-playlist",
            **event_data,
            status="failed",
            failure_stage="request",
            error=_whole_word_summary(error, 500),
            error_category=error.category,
            error_reason=error.reason,
            http_status=error.status_code,
        )
        _command_error(
            str(error),
            command="yt-playlist",
            raw=raw,
            error_type=type(error).__name__,
            stage="request",
        )
    except (TypeError, RuntimeError) as error:
        log_event(
            "yt-playlist",
            **event_data,
            status="failed",
            failure_stage="invalid-response",
            error="{}: {}".format(
                type(error).__name__, _whole_word_summary(error, 500)
            ),
        )
        _command_error(
            str(error),
            command="yt-playlist",
            raw=raw,
            error_type=type(error).__name__,
            stage="invalid-response",
        )

    provider_errors = _youtube_errors(provider.get("errors"))
    playlist_data = provider.get("playlist")
    playlist_items = list(provider.get("playlist_items") or [])
    videos = list(provider.get("videos") or [])
    coverage = dict(provider.get("coverage") or {})
    continuation = _continuation(
        "yt-playlist",
        playlist_id,
        coverage,
        pages=pages,
        max_results=max_results,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        retries=retries,
        show_description=show_description,
        raw=raw,
    )
    data: YouTubePlaylistResultData = {
        "provider": provider["provider"],
        "playlist": playlist_data,
        "playlist_items": playlist_items,
        "videos": videos,
        "video_id_outcomes": list(provider.get("video_id_outcomes") or []),
        "request": dict(provider.get("request") or {}),
        "coverage": coverage,
        "api_calls": dict(provider.get("api_calls") or {}),
        "continuation": continuation,
        "observed_at": provider.get("observed_at"),
        "expires_at": provider.get("expires_at"),
        "show_description": show_description,
    }
    outcome = CommandResult(
        command="yt-playlist",
        status=_status_for_playlist(
            playlist_data, playlist_items, coverage, provider_errors
        ),
        data=data,
        errors=provider_errors,
        warnings=list(provider.get("warnings") or []),
    )
    serialization_failed = False
    if raw:
        prepared = _prepare_raw_result(outcome)
        serialization_failed = prepared is not outcome
        outcome = prepared
    compact_data = {
        **event_data,
        "request": data["request"],
        "coverage": coverage,
        "api_calls": data["api_calls"],
        "playlist_returned": playlist_data is not None,
        "playlist_title": (
            playlist_data.get("title") if playlist_data else None
        ),
        "channel_id": (
            playlist_data.get("channel_id") if playlist_data else None
        ),
        "playlist_items": len(playlist_items),
        "videos": len(videos),
        "continuation": continuation,
    }
    if serialization_failed:
        compact_data = {
            **event_data,
            "failure_stage": "serialize-result",
            "serialization_failed": True,
        }
    log_result(
        "yt-playlist",
        outcome,
        data=compact_data,
    )
    if raw:
        _emit_raw_result(outcome, indent=2)
        return
    _render_playlist(outcome)


@click.command("yt-playlists")
@click.argument("channel")
@_playlist_options
def yt_playlists(
    channel: str,
    pages: int,
    max_results: int,
    page_token: Optional[str],
    connect_timeout: float,
    read_timeout: float,
    retries: int,
    show_description: bool,
    raw: bool,
) -> None:
    """List the public playlist shelf for one exact YouTube channel.

    CHANNEL may be a 24-character UC ID, @handle, or canonical HTTPS channel
    URL. Use a returned playlist ID with ``filmot yt-playlist``.

    \b
      filmot yt-playlists @GoogleDevelopers
      filmot yt-playlists UC_x5XG1OV2P6uZZ5FSM9Ttw --raw
    """
    from ..ledger import log_event, log_result
    from ..youtube_resources import YouTubeAPIError, list_channel_playlists_detailed

    canonical = None
    validation_error = None
    try:
        canonical = _canonical_channel_reference(channel)
    except ValueError as error:
        validation_error = str(error)
    channel = ""
    if validation_error is not None or canonical is None:
        message = (
            "expected an exact UC channel ID, @handle, or canonical HTTPS "
            "YouTube channel URL"
        )
        if raw:
            _command_error(
                message,
                command="yt-playlists",
                raw=True,
                error_type="BadParameter",
                stage="validate-request",
            )
        raise click.BadParameter(message, param_hint="CHANNEL")
    canonical = str(canonical)
    page_token = _validated_page_token(
        page_token, command="yt-playlists", raw=raw
    )

    event_data = {
        "channel_reference": canonical,
        "pages": pages,
        "max_results": max_results,
        "page_token": page_token,
        "connect_timeout": connect_timeout,
        "read_timeout": read_timeout,
        "retries": retries,
        "raw": raw,
    }
    try:
        with _status(
            "[bold green]Reading public playlists for {}...".format(canonical),
            raw=raw,
        ):
            provider = list_channel_playlists_detailed(
                canonical,
                max_pages=pages,
                max_results=max_results,
                page_token=page_token,
                timeout=(connect_timeout, read_timeout),
                retries=retries,
            )
        provider = _normalize_playlist_shelf_result(
            provider, expected_channel_reference=canonical
        )
    except ValueError as error:
        log_event(
            "yt-playlists",
            **event_data,
            status="failed",
            failure_stage="configuration",
            error="{}: {}".format(
                type(error).__name__, _whole_word_summary(error, 500)
            ),
        )
        _command_error(
            str(error),
            command="yt-playlists",
            raw=raw,
            error_type=type(error).__name__,
            stage="configuration",
        )
    except YouTubeAPIError as error:
        log_event(
            "yt-playlists",
            **event_data,
            status="failed",
            failure_stage="request",
            error=_whole_word_summary(error, 500),
            error_category=error.category,
            error_reason=error.reason,
            http_status=error.status_code,
        )
        _command_error(
            str(error),
            command="yt-playlists",
            raw=raw,
            error_type=type(error).__name__,
            stage="request",
        )
    except (TypeError, RuntimeError) as error:
        log_event(
            "yt-playlists",
            **event_data,
            status="failed",
            failure_stage="invalid-response",
            error="{}: {}".format(
                type(error).__name__, _whole_word_summary(error, 500)
            ),
        )
        _command_error(
            str(error),
            command="yt-playlists",
            raw=raw,
            error_type=type(error).__name__,
            stage="invalid-response",
        )

    provider_errors = _youtube_errors(provider.get("errors"))
    channel_data = provider.get("channel")
    playlists = list(provider.get("playlists") or [])
    coverage = dict(provider.get("coverage") or {})
    continuation_identity = (
        str(channel_data.get("channel_id"))
        if channel_data and channel_data.get("channel_id")
        else canonical
    )
    continuation = _continuation(
        "yt-playlists",
        continuation_identity,
        coverage,
        pages=pages,
        max_results=max_results,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        retries=retries,
        show_description=show_description,
        raw=raw,
    )
    data: YouTubePlaylistShelfResultData = {
        "provider": provider["provider"],
        "channel": channel_data,
        "playlists": playlists,
        "request": dict(provider.get("request") or {}),
        "coverage": coverage,
        "api_calls": dict(provider.get("api_calls") or {}),
        "continuation": continuation,
        "observed_at": provider.get("observed_at"),
        "expires_at": provider.get("expires_at"),
        "show_description": show_description,
    }
    outcome = CommandResult(
        command="yt-playlists",
        status=_status_for_shelf(
            channel_data, playlists, coverage, provider_errors
        ),
        data=data,
        errors=provider_errors,
        warnings=list(provider.get("warnings") or []),
    )
    serialization_failed = False
    if raw:
        prepared = _prepare_raw_result(outcome)
        serialization_failed = prepared is not outcome
        outcome = prepared
    compact_data = {
        **event_data,
        "request": data["request"],
        "coverage": coverage,
        "api_calls": data["api_calls"],
        "channel_returned": channel_data is not None,
        "channel_id": (
            channel_data.get("channel_id") if channel_data else None
        ),
        "channel_title": (
            channel_data.get("title") if channel_data else None
        ),
        "playlists": len(playlists),
        "continuation": continuation,
    }
    if serialization_failed:
        compact_data = {
            **event_data,
            "failure_stage": "serialize-result",
            "serialization_failed": True,
        }
    log_result(
        "yt-playlists",
        outcome,
        data=compact_data,
    )
    if raw:
        _emit_raw_result(outcome, indent=2)
        return
    _render_playlist_shelf(outcome)


__all__ = ["yt_playlist", "yt_playlists"]
