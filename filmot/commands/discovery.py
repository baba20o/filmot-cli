"""Discovery searches: video titles and metadata, notable channels, subtitle exports."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Sequence

import click
from rich.markup import escape
from rich.table import Table

from ..api import FilmotClient
from ..api_contract import FilmotAPIContractError
from ..channel_intel import ChannelReferenceError, resolve_channels
from ..cli_support import (
    command_error as _command_error,
    console,
    emit_raw_result as _emit_raw_result,
    prepare_raw_result as _prepare_raw_result,
    status_context as _status,
    stderr_console,
    whole_word_summary as _whole_word_summary,
)
from ..discovery_search import (
    CHANNEL_PAGE_LIMIT,
    CHANNEL_COLUMNS,
    CHANNEL_RESULT_WINDOW,
    CHANNEL_SORTS,
    DEFAULT_EXPORT_COLUMNS,
    DEFAULT_TITLE_COLUMNS,
    EXPORT_COLUMNS,
    EXPORT_FORMATS,
    EXPORT_PAGE_LIMIT,
    EXPORT_SORTS,
    FILTER_CHANNEL_LIMIT,
    GROWTH_WINDOWS,
    TITLE_LAST_PAGE,
    TITLE_COLUMNS,
    TITLE_PAGE_LIMIT,
    TITLE_SUBTITLE_LAST_PAGE,
    TITLE_SUBTITLE_PAGE_LIMIT,
    TITLE_SORTS,
    VIDEO_BASE_COLUMNS,
    ExportFormatError,
    channel_filter,
    drop_unset,
    export_format,
    fetch_export_pages,
    fetch_pages,
    merge_export_pages,
    result_rows,
    scoped,
    write_atomically,
)
from ..schemas import (
    ChannelFinderResultData,
    CommandResult,
    ErrorDetail,
    ResultStatus,
    SubtitleExportResultData,
    TitleSearchResultData,
)
from .channel import _failed, _number, _seconds, _text, _usage_error, _video_table


MAX_PAGES = 20
_DATE = click.DateTime(formats=["%Y-%m-%d"])


def _date(value) -> Optional[str]:
    return value.date().isoformat() if value else None


def _split(value: Optional[str]) -> List[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _check_dates(command: str, start: Optional[str], end: Optional[str], raw: bool) -> None:
    if start and end and start > end:
        _usage_error(command, "the start date must not be later than the end date", raw=raw)


def _bounded_text(command: str, option: str, value: Optional[str], limit: int, raw: bool) -> None:
    if value is not None and len(value) > limit:
        _usage_error(command, "{} accepts at most {} characters".format(option, limit), raw=raw)


def _columns(command, value, everything, allowed, raw, *, required=(), default=None):
    if everything and value is not None:
        _usage_error(command, "use --all or --columns, not both", raw=raw)
    if everything:
        return "all"
    if value is None:
        return ",".join(default) if default else None
    selected = _split(value)
    if selected == ["all"]:
        return "all"
    unknown = [column for column in selected if column not in allowed]
    if not selected or unknown:
        _usage_error(
            command, "--columns must name fields from {} or all".format(", ".join(allowed)), raw=raw,
        )
    return ",".join(dict.fromkeys([*required, *selected]))


def _export_options(command, output, fmt, allowed, raw):
    if fmt and not output:
        _usage_error(command, "--format needs --output", raw=raw)
    if not output:
        return None
    selected = export_format(output, fmt)
    if selected not in allowed:
        _usage_error(command, "export format must be {}".format(", ".join(allowed)), raw=raw)
    return selected


def _finish_export(command, output, fmt, filters, event, exported, raw):
    """Persist validated pages and report a typed summary, including partial exports."""
    error = exported.get("error")
    if error and not exported["bodies"]:
        _failed(
            command, event, raw=raw, stage=error.get("stage", "api"), message=str(error["error"]),
            error_type=str(error.get("error_type") or "FilmotAPIError"), payload=error,
        )
    path = Path(output)
    written = exported["rows"] > 0
    content = merge_export_pages(fmt, exported["bodies"]) if written else b""
    if written:
        try:
            write_atomically(path, content)
        except OSError as failure:
            _failed(
                command, event, raw=raw, stage="write-output", message="Could not write {}: {}".format(path, failure),
                error_type=type(failure).__name__,
            )
    data = {
        "output": str(path), "written": written, "format": fmt, "filters": filters,
        "first_page": event["page"], "per_page": event["per_page"],
        "pages_fetched": exported["pages_fetched"], "rows": exported["rows"], "bytes": len(content),
        "exhausted": exported["exhausted"], "next_page": exported["next_page"],
        "last_page_reached": exported["last_page_reached"],
    }
    outcome = _outcome(command, data, exported["rows"], error)
    if _finish(command, outcome, raw, {**event, "rows": exported["rows"], "bytes": len(content)}):
        return
    if written:
        console.print("Wrote {} records to {}".format(exported["rows"], escape(str(path))))
    else:
        console.print("[yellow]No matches; nothing was written.[/yellow]")
    _paging_note(data, error)


def _check_channel_filters(command: str, included: Optional[str], excluded: Optional[str], raw: bool) -> None:
    if included and excluded:
        _usage_error(command, "use --channel-id or --exclude-channels, not both", raw=raw)
    for option, value in (("--channel-id", included), ("--exclude-channels", excluded)):
        # An empty list would silently widen (and raise the price of) the search.
        if value is not None and not _split(value):
            _usage_error(command, "{} names no channel".format(option), raw=raw)


def _lookup(command: str, event: Dict[str, Any], raw: bool, endpoint: str, fetch):
    """Run ``fetch(client)`` and turn every expected failure into a typed error."""
    from ..api_providers import providers_for

    try:
        # Direct-only endpoints fail here, before any @handle lookup is spent.
        providers_for(endpoint)
        return fetch(FilmotClient())
    except ChannelReferenceError as error:
        _failed(
            command, event, raw=raw, stage="resolve-channel", message=str(error),
            error_type=error.error_type, payload=error.payload,
        )
    except FilmotAPIContractError as error:
        _failed(
            command, event, raw=raw, stage="invalid-response", message=str(error),
            error_type=type(error).__name__, payload=error.as_response(),
        )
    except ExportFormatError as error:
        _failed(
            command, event, raw=raw, stage="invalid-response", message=str(error),
            error_type=type(error).__name__,
        )
    except ValueError as error:
        _failed(
            command, event, raw=raw, stage="configuration", message=str(error),
            error_type=type(error).__name__,
        )


def _channel_ids(client: Any, included: Optional[str], excluded: Optional[str]) -> Dict[str, List[str]]:
    """UC IDs for the --channel-id and --exclude-channels references."""
    return {
        name: [
            entry["channel_id"]
            for entry in resolve_channels(_split(value), client, limit=FILTER_CHANNEL_LIMIT)
        ] if value else []
        for name, value in (("included", included), ("excluded", excluded))
    }


def _outcome(command: str, data: Dict[str, Any], found: Any, error: Optional[Dict[str, Any]]):
    if error:
        return CommandResult(
            command=command,
            status=ResultStatus.PARTIAL,
            data=data,
            errors=[ErrorDetail(
                type=str(error.get("error_type") or "FilmotAPIError"),
                message=str(error["error"]),
                stage=str(error.get("stage") or "api"),
            )],
        )
    return CommandResult(
        command=command,
        status=ResultStatus.COMPLETED if found else ResultStatus.EMPTY,
        data=data,
    )


def _finish(command: str, outcome: CommandResult, raw: bool, summary: Dict[str, Any]) -> bool:
    """Log ``outcome``; emit it and return True in raw mode."""
    from ..ledger import log_result

    if raw:
        outcome = _prepare_raw_result(outcome)
    log_result(command, outcome, data=summary)
    if raw:
        _emit_raw_result(outcome, indent=2)
    return raw


def _paging_note(data: Dict[str, Any], error: Optional[Dict[str, Any]]) -> None:
    if error:
        console.print("[yellow]Page {} failed ({}); showing the pages before it.[/yellow]".format(
            data.get("next_page"), escape(str(error.get("error_type") or error.get("error"))),
        ))
    elif data.get("last_page_reached"):
        console.print("[dim]Filmot pages this search no further.[/dim]")
    elif data.get("exhausted"):
        console.print("[dim]No more results.[/dim]")
    else:
        console.print("[dim]More may be available: add --page {}.[/dim]".format(data.get("next_page")))


def _channel_options(function):
    function = click.option(
        "--exclude-channels",
        help="Leave out these channels (comma-separated UC IDs, @handles, or channel URLs).",
    )(function)
    return click.option(
        "--channel-id",
        help="Only these channels (comma-separated UC IDs, @handles, or channel URLs); "
             "1 to 5 channels lower the price.",
    )(function)


def _video_filters(function):
    """Filters that titles and subtitle-export share, named like search's."""
    for option in reversed((
        click.option("--min-views", type=click.IntRange(min=1), help="Minimum view count."),
        click.option("--max-views", type=click.IntRange(min=1), help="Maximum view count."),
        click.option("--min-likes", type=click.IntRange(min=1), help="Minimum like count."),
        click.option("--max-likes", type=click.IntRange(min=1), help="Maximum like count."),
        click.option("--min-duration", type=click.IntRange(min=0), help="Minimum duration in seconds."),
        click.option("--max-duration", type=click.IntRange(min=0), help="Maximum duration in seconds."),
        click.option("--start-date", type=_DATE, metavar="YYYY-MM-DD", help="Earliest upload date."),
        click.option("--end-date", type=_DATE, metavar="YYYY-MM-DD", help="Latest upload date."),
        click.option("--category", "-c", help="Exact YouTube category name."),
        click.option("--exclude", help="Categories to exclude (comma-separated)."),
        click.option("--license", "license_type", type=click.Choice(["1", "2"]), help="1=Standard, 2=Creative Commons."),
    )):
        function = option(function)
    return function


# ========== TITLES ==========

@click.command("titles")
@click.argument("query", required=False)
@_channel_options
@_video_filters
@click.option("--country", help="Filmot channel-country IDs (comma-separated, e.g. 217 for the US).")
@click.option("--caption-lang", help="Caption languages the video must have (comma-separated codes).")
@click.option("--only-caption-langs", is_flag=True, help="Require exactly the --caption-lang languages, no others.")
@click.option("--detected-lang", help="Detected spoken-language code.")
@click.option(
    "--detected-in-captions", is_flag=True,
    help="Also match --detected-lang against the caption languages.",
)
@click.option("--unlisted/--listed", default=None, help="Only unlisted, or only listed, videos.")
@click.option("--live/--not-live", default=None, help="Only livestreams, or no livestreams.")
@click.option("--partial/--complete", default=None, help="Only partial, or only complete, index records.")
@click.option(
    "--sort", type=click.Choice(TITLE_SORTS),
    help="Order by a field; id is when Filmot indexed the video, avg_ratio is vocabulary ease.",
)
@click.option("--order", type=click.Choice(["asc", "desc"]), help="Sort order.")
@click.option("--columns", help="Selected metadata fields; base video identity fields are retained.")
@click.option("--all", "everything", is_flag=True, help="All metadata and raw subtitles (at most 10 videos per page).")
@click.option("--output", "-o", type=click.Path(dir_okay=False), help="Write a server export to this file.")
@click.option("--format", "-f", "fmt", type=click.Choice(EXPORT_FORMATS), help="Export format; requires --output.")
@click.option("--page", "-p", default=1, show_default=True, type=click.IntRange(1, TITLE_SUBTITLE_LAST_PAGE), help="First page (max 500, or 5000 with raw subtitles).")
@click.option("--pages", default=1, show_default=True, type=click.IntRange(1, MAX_PAGES), help="Pages to fetch.")
@click.option("--per-page", type=click.IntRange(1, TITLE_PAGE_LIMIT), help="Videos per page (default 50, or 10 with raw subtitles).")
@click.option("--raw", is_flag=True, help="Output one versioned JSON result; its videos array pipes into `filmot download`.")
def titles(query, channel_id, exclude_channels, min_views, max_views, min_likes, max_likes,
           min_duration, max_duration, start_date, end_date, category, exclude, license_type,
           country, caption_lang, only_caption_langs, detected_lang, detected_in_captions,
           unlisted, live, partial, sort, order, columns, everything, output, fmt, page, pages, per_page, raw):
    """Search video titles and metadata in Filmot's index.

    QUERY is a title expression (implicit AND, |, "exact phrases", grouping,
    -exclusion) or a space-separated list of video IDs; filters alone also
    work. Each page costs 3 credits on the direct Filmot API, or 1 when
    --channel-id names 1 to 5 channels.

    \b
      filmot titles '"sqlite" wal' --min-views 10000 --sort viewcount --order desc
      filmot titles --channel-id @LinusTechTips --start-date 2026-01-01 --pages 2
      filmot titles "fusion reactor" --raw | filmot download -t fusion
    """
    start, end = _date(start_date), _date(end_date)
    selected = _columns(
        "titles", columns, everything, TITLE_COLUMNS, raw,
        required=VIDEO_BASE_COLUMNS, default=DEFAULT_TITLE_COLUMNS,
    )
    subtitles = selected == "all" or "subtitles" in _split(selected)
    last_page = TITLE_SUBTITLE_LAST_PAGE if subtitles else TITLE_LAST_PAGE
    page_limit = TITLE_SUBTITLE_PAGE_LIMIT if subtitles else TITLE_PAGE_LIMIT
    per_page = per_page if per_page is not None else (TITLE_SUBTITLE_PAGE_LIMIT if subtitles else 50)
    if page > last_page or per_page > page_limit:
        _usage_error("titles", "this selection supports --page up to {} and --per-page up to {}".format(last_page, page_limit), raw=raw)
    fmt = _export_options("titles", output, fmt, EXPORT_FORMATS, raw)
    _check_dates("titles", start, end, raw)
    _check_channel_filters("titles", channel_id, exclude_channels, raw)
    if only_caption_langs and not caption_lang:
        _usage_error("titles", "--only-caption-langs needs --caption-lang", raw=raw)
    if detected_in_captions and not detected_lang:
        _usage_error("titles", "--detected-in-captions needs --detected-lang", raw=raw)
    if order and not sort:
        _usage_error("titles", "--order needs --sort", raw=raw)
    criteria = drop_unset({
        "minViews": min_views, "maxViews": max_views, "minLikes": min_likes, "maxLikes": max_likes,
        "startDate": start, "endDate": end, "startDuration": min_duration, "endDuration": max_duration,
        "category": category, "excludeCategory": exclude,
        "license": int(license_type) if license_type else None,
        "country": country, "captionLanguages": caption_lang,
        "capLangExactMatch": 1 if only_caption_langs else None,
        "detectedLanguage": detected_lang,
        "qDetectedLangInCapLangs": "true" if detected_in_captions else None,
        "unlisted": None if unlisted is None else int(unlisted),
        "live": None if live is None else int(live),
        "completeData": None if partial is None else int(not partial),
    })
    if not (query or channel_id or exclude_channels or criteria):
        _usage_error("titles", "give a title query or at least one filter", raw=raw)
    event = {"query": query, "filters": criteria, "page": page, "pages": pages, "per_page": per_page, "raw": raw}
    if output:
        event.update(output=str(output), format=fmt)

    def fetch(client):
        with _status("[bold green]Resolving channels...", raw=raw):
            channels = _channel_ids(client, channel_id, exclude_channels)
        filters = drop_unset({
            "titleQuery": query,
            "channelID": channel_filter(channels["included"], channels["excluded"]),
            **criteria,
            "sortField": sort,
            "sortOrder": (order or "desc") if sort else None,
            "columns": selected,
        })
        if pages > 1 and not raw:
            stderr_console.print("[dim]Fetching up to {} pages ({} credit{} each)...[/dim]".format(
                pages, *(("1", "") if scoped(channels["included"]) else ("3", "s")),
            ))
        with _status("[bold green]Searching titles...", raw=raw):
            if output:
                return filters, fetch_export_pages(
                    lambda number: client.export_metadata_search(
                        dict(filters, page=number, perPageCount=per_page, format=fmt, exportInline=1),
                    ),
                    fmt, per_page=per_page, first_page=page, pages=pages,
                    stop_on_short=False, last_page=last_page,
                )
            return filters, fetch_pages(
                lambda number: client.search_metadata(
                    dict(filters, page=number, perPageCount=per_page),
                ),
                result_rows,
                first_page=page,
                pages=pages,
                last_page=last_page,
            )

    filters, listing = _lookup("titles", event, raw, "/getmetadatasearch", fetch)
    if output:
        return _finish_export("titles", output, fmt, filters, event, listing, raw)
    error = listing.get("error")
    if error and not listing["rows"]:
        _failed(
            "titles", event, raw=raw, stage="api", message=str(error["error"]),
            error_type=str(error.get("error_type") or "FilmotAPIError"), payload=error,
        )
    data: TitleSearchResultData = {
        "query": query,
        "filters": filters,
        "first_page": page,
        "per_page": per_page,
        "pages_fetched": listing["pages_fetched"],
        "exhausted": listing["exhausted"],
        "next_page": listing["next_page"],
        "last_page_reached": listing["last_page_reached"],
        "videos": listing["rows"],
    }
    outcome = _outcome("titles", data, data["videos"], error)
    if _finish("titles", outcome, raw, {**event, "results": len(data["videos"])}):
        return
    if data["videos"]:
        console.print(_video_table(data["videos"], title="Title matches", show_channel=True))
    else:
        console.print("[yellow]No videos matched.[/yellow]")
    _paging_note(data, error)
    if data["videos"]:
        console.print("[dim]Pipe --raw into `filmot download -t TOPIC` to fetch these transcripts.[/dim]")


# ========== FIND CHANNELS ==========

@click.command("find-channels")
@click.option("--description", help="Words to match in channel names, handles, and descriptions.")
@click.option("--topics", help="Words to match in channel topics.")
@click.option("--min-subscribers", type=click.FloatRange(min=0), help="Minimum subscribers.")
@click.option("--max-subscribers", type=click.FloatRange(min=0), help="Maximum subscribers.")
@click.option("--min-views", type=click.FloatRange(min=0), help="Minimum total channel views.")
@click.option("--max-views", type=click.FloatRange(min=0), help="Maximum total channel views.")
@click.option("--min-videos", type=click.FloatRange(min=0), help="Minimum reported video count.")
@click.option("--max-videos", type=click.FloatRange(min=0), help="Maximum reported video count.")
@click.option("--min-avg-duration", type=click.FloatRange(min=0), help="Minimum average video length in seconds.")
@click.option("--max-avg-duration", type=click.FloatRange(min=0), help="Maximum average video length in seconds.")
@click.option("--created-after", type=_DATE, metavar="YYYY-MM-DD", help="Channel created on or after.")
@click.option("--created-before", type=_DATE, metavar="YYYY-MM-DD", help="Channel created on or before.")
@click.option("--first-video-after", type=_DATE, metavar="YYYY-MM-DD", help="First indexed video on or after.")
@click.option("--first-video-before", type=_DATE, metavar="YYYY-MM-DD", help="First indexed video on or before.")
@click.option("--min-gained-views", type=float, help="Minimum views gained in --views-window days (normalized).")
@click.option("--max-gained-views", type=float, help="Maximum views gained in --views-window days (normalized).")
@click.option("--views-window", type=click.Choice(GROWTH_WINDOWS), help="Days for gained-view filters and sorting (default 28).")
@click.option("--min-gained-subs", type=float, help="Minimum subscribers gained in --subs-window days (normalized).")
@click.option("--max-gained-subs", type=float, help="Maximum subscribers gained in --subs-window days (normalized).")
@click.option("--subs-window", type=click.Choice(GROWTH_WINDOWS), help="Days for gained-subscriber filters and sorting (default 28).")
@click.option("--country", "countries", multiple=True, help="Channel country name; repeat for several.")
@click.option("--auto-lang", "auto_languages", multiple=True, help="Automatic-caption language every channel must have; repeatable.")
@click.option("--columns", help="Selected channel fields; channelid is retained.")
@click.option("--all", "everything", is_flag=True, help="Include every channel field, including descriptions and topics.")
@click.option("--output", "-o", type=click.Path(dir_okay=False), help="Write a server export to this file.")
@click.option("--format", "-f", "fmt", type=click.Choice(["json", "jsonl"]), help="Export format; requires --output.")
@click.option("--sort", type=click.Choice(CHANNEL_SORTS), help="Order by a field; semantic_score is match relevance.")
@click.option("--order", type=click.Choice(["asc", "desc"]), help="Sort order (default desc).")
@click.option("--page", "-p", default=1, show_default=True, type=click.IntRange(min=1), help="First page.")
@click.option("--pages", default=1, show_default=True, type=click.IntRange(1, MAX_PAGES), help="Pages to fetch.")
@click.option("--per-page", default=25, show_default=True, type=click.IntRange(1, CHANNEL_PAGE_LIMIT), help="Channels per page.")
@click.option("--raw", is_flag=True, help="Output one versioned JSON result")
def find_channels(description, topics, min_subscribers, max_subscribers, min_views, max_views,
                  min_videos, max_videos, min_avg_duration, max_avg_duration, created_after,
                  created_before, first_video_after, first_video_before, min_gained_views,
                  max_gained_views, views_window, min_gained_subs, max_gained_subs, subs_window,
                  countries, auto_languages, columns, everything, output, fmt, sort, order, page, pages, per_page, raw):
    """Find notable channels in Filmot's index by size, growth, place, and topic.

    Filmot indexes about 63 million notable channels; only the first 10,000
    matches of a search can be paged to. Each page costs 1 credit on the
    direct Filmot API. Pass the channel IDs it finds to `channel` or
    `channel-videos`.

    \b
      filmot find-channels --topics "database internals" --min-subscribers 10000
      filmot find-channels --country Canada --sort gainedsubcount --subs-window 90
      filmot find-channels --description sqlite --auto-lang en --raw
    """
    selected = _columns("find-channels", columns, everything, CHANNEL_COLUMNS, raw, required=("channelid",))
    fmt = _export_options("find-channels", output, fmt, ("json", "jsonl"), raw)
    _bounded_text("find-channels", "--description", description, 500, raw)
    _bounded_text("find-channels", "--topics", topics, 500, raw)
    languages = _split(",".join(auto_languages))
    if auto_languages and not languages:
        _usage_error("find-channels", "--auto-lang must name a language", raw=raw)
    if len(languages) > 100:
        _usage_error("find-channels", "--auto-lang accepts at most 100 languages", raw=raw)
    for language in languages:
        _bounded_text("find-channels", "--auto-lang", language, 20, raw)
    dates = {
        "channelCreatedAfter": _date(created_after),
        "channelCreatedBefore": _date(created_before),
        "firstVideoAfter": _date(first_video_after),
        "firstVideoBefore": _date(first_video_before),
    }
    _check_dates("find-channels", dates["channelCreatedAfter"], dates["channelCreatedBefore"], raw)
    _check_dates("find-channels", dates["firstVideoAfter"], dates["firstVideoBefore"], raw)
    if order and not sort:
        _usage_error("find-channels", "--order needs --sort", raw=raw)
    if views_window and min_gained_views is None and max_gained_views is None and sort != "gainedviewcount":
        _usage_error(
            "find-channels",
            "--views-window applies to --min/--max-gained-views or --sort gainedviewcount",
            raw=raw,
        )
    if subs_window and min_gained_subs is None and max_gained_subs is None and sort != "gainedsubcount":
        _usage_error(
            "find-channels",
            "--subs-window applies to --min/--max-gained-subs or --sort gainedsubcount",
            raw=raw,
        )
    if (page - 1) * per_page >= CHANNEL_RESULT_WINDOW:
        _usage_error(
            "find-channels",
            "only the first {:,} matching channels can be paged to".format(CHANNEL_RESULT_WINDOW),
            raw=raw,
        )
    filters = drop_unset({
        "description": description, "topics": topics,
        "minSubscribers": min_subscribers, "maxSubscribers": max_subscribers,
        "minTotalViewCount": min_views, "maxTotalViewCount": max_views,
        "minVideos": min_videos, "maxVideos": max_videos,
        "minAvgVideoDuration": min_avg_duration, "maxAvgVideoDuration": max_avg_duration,
        **dates,
        "minGainedViewCount": min_gained_views, "maxGainedViewCount": max_gained_views,
        "gainedViewCountPeriod": int(views_window) if views_window else None,
        "minGainedSubCount": min_gained_subs, "maxGainedSubCount": max_gained_subs,
        "gainedSubPeriod": int(subs_window) if subs_window else None,
        "countries": ",".join(countries) or None,
        "autoLanguages": ",".join(languages) or None,
        "sortField": sort,
        "sortOrder": (order or "desc") if sort else None,
        "columns": selected,
    })
    event = {"filters": filters, "page": page, "pages": pages, "per_page": per_page, "raw": raw}
    if output:
        event.update(output=str(output), format=fmt)
    last_page = (CHANNEL_RESULT_WINDOW - 1) // per_page + 1

    def fetch(client):
        with _status("[bold green]Finding channels...", raw=raw):
            if output:
                return fetch_export_pages(
                    lambda number: client.export_advanced_channel_search(
                        dict(filters, page=number, perPageCount=per_page, format=fmt),
                    ),
                    fmt, per_page=per_page, first_page=page, pages=pages,
                    stop_on_short=False, last_page=last_page,
                )
            return fetch_pages(
                lambda number: client.search_advanced_channels(
                    dict(filters, page=number, perPageCount=per_page),
                ),
                result_rows,
                first_page=page,
                pages=pages,
                last_page=last_page,
            )

    listing = _lookup("find-channels", event, raw, "/getadvancedchannelsearch", fetch)
    if output:
        return _finish_export("find-channels", output, fmt, filters, event, listing, raw)
    error = listing.get("error")
    if error and not listing["rows"]:
        _failed(
            "find-channels", event, raw=raw, stage="api", message=str(error["error"]),
            error_type=str(error.get("error_type") or "FilmotAPIError"), payload=error,
        )
    response = listing.get("response")
    data: ChannelFinderResultData = {
        "filters": filters,
        "first_page": page,
        "per_page": per_page,
        "pages_fetched": listing["pages_fetched"],
        "exhausted": listing["exhausted"],
        "next_page": listing["next_page"],
        "last_page_reached": listing["last_page_reached"],
        "max_results": response.get("maxresults") if isinstance(response, dict) else None,
        "channels": listing["rows"],
    }
    outcome = _outcome("find-channels", data, data["channels"], error)
    if _finish("find-channels", outcome, raw, {**event, "results": len(data["channels"])}):
        return
    _display_found_channels(data["channels"], int(views_window or 28))
    _paging_note(data, error)


def _display_found_channels(channels: Sequence[Dict[str, Any]], window: int) -> None:
    if not channels:
        console.print("[yellow]No channels matched.[/yellow]")
        return
    table = Table(title="Notable channels")
    table.add_column("Channel")
    table.add_column("Country")
    for column in ("Subscribers", "Total views", "Videos", "Avg views", "Avg length", "Views gained ({}d)".format(window)):
        table.add_column(column, justify="right")
    table.add_column("Channel ID", no_wrap=True)
    for channel in channels:
        growth = (channel.get("growth") or {}).get("totalviewcount") or {}
        gained = growth.get("{}d".format(window)) if isinstance(growth, dict) else None
        table.add_row(
            "{} {}".format(_text(channel.get("name"), "Unknown"), _text(channel.get("handle"), "")).strip(),
            _text(channel.get("country"), ""),
            _number(channel.get("subcount")),
            _number(channel.get("totalviewcount")),
            _number(channel.get("videocount")),
            _number(channel.get("avgvideoviewcount")),
            _seconds(channel.get("avgdurationseconds")),
            _number(gained.get("normalizeddelta")) if isinstance(gained, dict) else "N/A",
            _text(channel.get("channelid"), ""),
        )
    console.print(table)
    console.print("[dim]Pass a channel ID to `filmot channel` or `filmot channel-videos` for more.[/dim]")


# ========== SUBTITLE EXPORT ==========

@click.command("subtitle-export")
@click.argument("query")
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False), help="File to write.")
@click.option(
    "--format", "-f", "fmt", type=click.Choice(EXPORT_FORMATS),
    help="jsonl, json, csv, or zip; defaults to the output file's extension, else jsonl.",
)
@click.option(
    "--subtitles", is_flag=True,
    help="Include each match's full subtitles (ZIP: one .srt per video).",
)
@click.option("--no-snippets", is_flag=True, help="Leave out the matched-text snippets (hits).")
@click.option("--columns", help="Comma-separated fields to export (see the docs); defaults to Filmot's set.")
@click.option("--lang", "-l", help="Subtitle language code.")
@click.option("--title", help="Additional title expression.")
@click.option("--manual-subs", is_flag=True, help="Search manual subtitles instead of automatic ones.")
@click.option(
    "--no-guess-video-language/--guess-video-language", default=None,
    help="Include manual subtitle matches regardless of the likely spoken language, or require a likely match.",
)
@click.option("--detected-lang", help="Detected spoken-language code.")
@click.option("--country", type=click.IntRange(min=1), help="Filmot channel-country ID (e.g. 217 for the US).")
@_channel_options
@_video_filters
@click.option(
    "--sort", type=click.Choice(EXPORT_SORTS),
    help="Order by a field; channelvariety and bm25match use their own relevance order.",
)
@click.option("--order", type=click.Choice(["asc", "desc"]), help="Sort order.")
@click.option("--page", "-p", default=1, show_default=True, type=click.IntRange(min=1), help="First page.")
@click.option("--pages", default=1, show_default=True, type=click.IntRange(1, MAX_PAGES), help="Pages to fetch.")
@click.option("--per-page", default=100, show_default=True, type=click.IntRange(1, EXPORT_PAGE_LIMIT), help="Matches per page.")
@click.option("--raw", is_flag=True, help="Output one versioned JSON summary of the written file")
def subtitle_export(query, output, fmt, subtitles, no_snippets, columns, lang, title, manual_subs,
                    no_guess_video_language, detected_lang, country, channel_id, exclude_channels, min_views, max_views,
                    min_likes, max_likes, min_duration, max_duration, start_date, end_date,
                    category, exclude, license_type, sort, order, page, pages, per_page, raw):
    """Export subtitle-search matches to a CSV, JSON, JSON Lines, or ZIP file.

    Filmot builds the file server-side, up to 500 matches per page; pages are
    merged into one file, written only once all are fetched. Each page costs 5
    credits on the direct Filmot API, or 1 when --channel-id names 1 to 5
    channels. --subtitles with a ZIP adds one .srt file per matching video.

    \b
      filmot subtitle-export '"sqlite wal"' -o matches.csv
      filmot subtitle-export "fusion" --channel-id @Veritasium -o fusion.zip --subtitles
      filmot subtitle-export "gpu" --lang en --pages 3 --per-page 500 -o gpu.jsonl
    """
    fmt = export_format(output, fmt)
    _bounded_text("subtitle-export", "--lang", lang, 20, raw)
    if not query.strip():
        _usage_error("subtitle-export", "query must not be empty", raw=raw)
    start, end = _date(start_date), _date(end_date)
    _check_dates("subtitle-export", start, end, raw)
    _check_channel_filters("subtitle-export", channel_id, exclude_channels, raw)
    if order and not sort:
        _usage_error("subtitle-export", "--order needs --sort", raw=raw)
    if order and sort in ("channelvariety", "bm25match"):
        _usage_error("subtitle-export", "--sort {} has its own order; drop --order".format(sort), raw=raw)
    selected = _split(columns)
    all_columns = selected == ["all"]
    unknown = [] if all_columns else [column for column in selected if column not in EXPORT_COLUMNS]
    if unknown:
        _usage_error(
            "subtitle-export",
            "unknown --columns {}; choose from {}".format(", ".join(unknown), ", ".join(EXPORT_COLUMNS)),
            raw=raw,
        )
    if columns is not None and not selected:
        _usage_error("subtitle-export", "--columns must name at least one field or all", raw=raw)
    if subtitles and not all_columns:
        selected = [*(selected or DEFAULT_EXPORT_COLUMNS), "subtitles"]
    criteria = drop_unset({
        "lang": lang, "title": title, "searchManualSubs": 1 if manual_subs else None,
        "noGuessVideoLanguage": None if no_guess_video_language is None else int(no_guess_video_language),
        "detectedLang": detected_lang, "country": country,
        "minViews": min_views, "maxViews": max_views, "minLikes": min_likes, "maxLikes": max_likes,
        "startDuration": min_duration, "endDuration": max_duration,
        "startDate": start, "endDate": end, "category": category, "excludeCategory": exclude,
        "license": int(license_type) if license_type else None,
        "sortField": sort, "sortOrder": (order or "desc") if sort else None,
        "columns": ",".join(dict.fromkeys(selected)) or None,
        "getSnippets": 0 if no_snippets else None,
    })
    path = Path(output)
    event = {
        "query": query, "output": str(path), "format": fmt, "filters": criteria,
        "page": page, "pages": pages, "per_page": per_page, "raw": raw,
    }

    def fetch(client):
        with _status("[bold green]Resolving channels...", raw=raw):
            channels = _channel_ids(client, channel_id, exclude_channels)
        filters = drop_unset({
            "query": query,
            "channelID": channel_filter(channels["included"], channels["excluded"]),
            **criteria,
        })
        if not raw:
            stderr_console.print("[dim]Exporting up to {} page(s) of {} matches ({} credit{} each)...[/dim]".format(
                pages, per_page, *(("1", "") if scoped(channels["included"]) else ("5", "s")),
            ))
        export = fetch_export_pages(
            lambda number: client.export_subtitle_search(
                dict(filters, page=number, perPageCount=per_page, format=fmt, exportInline=1),
            ),
            fmt,
            per_page=per_page,
            first_page=page,
            pages=pages,
            on_page=None if raw else (
                lambda number, rows: stderr_console.print("[dim]  page {}: {} matches[/dim]".format(number, rows))
            ),
        )
        return filters, export

    filters, export = _lookup("subtitle-export", event, raw, "/getsubtitlesearch", fetch)
    error = export.get("error")
    if error and not export["bodies"]:
        _failed(
            "subtitle-export", event, raw=raw, stage="api", message=str(error["error"]),
            error_type=str(error.get("error_type") or "FilmotAPIError"), payload=error,
        )
    # With no matches there is nothing worth replacing an existing file with.
    written = export["rows"] > 0
    content = merge_export_pages(fmt, export["bodies"]) if written else b""
    if written:
        try:
            write_atomically(path, content)
        except OSError as write_error:
            _failed(
                "subtitle-export", event, raw=raw, stage="write-output",
                message="Could not write {}: {}".format(path, write_error),
                error_type=type(write_error).__name__,
            )
    data: SubtitleExportResultData = {
        "query": query,
        "output": str(path),
        "written": written,
        "format": fmt,
        "filters": filters,
        "first_page": page,
        "per_page": per_page,
        "pages_fetched": export["pages_fetched"],
        "rows": export["rows"],
        "bytes": len(content),
        "exhausted": export["exhausted"],
        "next_page": export["next_page"],
    }
    outcome = _outcome("subtitle-export", data, export["rows"], error)
    if _finish("subtitle-export", outcome, raw, {**event, "rows": export["rows"], "bytes": len(content)}):
        return
    if not written:
        console.print("[yellow]No matches; nothing was written.[/yellow]")
        return
    console.print("Wrote {} matches ({} page{}, {:,} bytes) to {}".format(
        export["rows"], export["pages_fetched"], "" if export["pages_fetched"] == 1 else "s",
        len(content), escape(str(path)),
    ))
    _paging_note(data, error)
    if error:
        console.print("[dim]Continue with --page {} into a different file.[/dim]".format(data["next_page"]))
