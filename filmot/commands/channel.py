"""Channel profiles, analytics, and indexed-video listings from Filmot."""

from __future__ import annotations

from typing import Any, Dict, List, NoReturn, Optional, Sequence

import click
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from ..api import VIDEO_METADATA_COLUMNS, FilmotClient
from ..api_contract import FilmotAPIContractError
from ..channel_intel import (
    DATED_GROUPS,
    LISTING_CHANNEL_LIMIT,
    LISTING_PAGE_LIMIT,
    PROFILE_CHANNEL_LIMIT,
    VIDEO_STAT_GROUPS,
    ChannelReferenceError,
    channel_columns,
    fetch_channel_videos,
    resolve_channels,
)
from ..cli_support import (
    command_error as _command_error,
    console,
    emit_raw_result as _emit_raw_result,
    prepare_raw_result as _prepare_raw_result,
    status_context as _status,
    stderr_console,
    whole_word_summary as _whole_word_summary,
)
from ..schemas import (
    ChannelProfileResultData,
    ChannelVideosResultData,
    CommandResult,
    ErrorDetail,
    ResultStatus,
)


MAX_LISTING_PAGES = 50
_GROUP_ORDER = ("about", "growth", "words", "history", "stats", "monthly")


def _references(command: str, arguments: Sequence[str], *, raw: bool) -> List[str]:
    """Channel arguments, each of which may also be a comma-separated list."""
    references = [
        part.strip() for argument in arguments for part in argument.split(",") if part.strip()
    ]
    if not references:
        _usage_error(command, "name at least one channel", raw=raw)
    return references


def _failed(
    command: str,
    event: Dict[str, Any],
    *,
    raw: bool,
    stage: str,
    message: str,
    error_type: str,
    payload: Optional[Dict[str, Any]] = None,
) -> NoReturn:
    from ..ledger import log_event

    log_event(
        command,
        **event,
        status="failed",
        failure_stage=stage,
        error="{}: {}".format(error_type, _whole_word_summary(message, 500)),
    )
    _command_error(
        message,
        command=command,
        raw=raw,
        payload=payload,
        error_type=error_type,
        stage=stage,
    )
    raise AssertionError("unreachable")  # command_error always exits


def _usage_error(command: str, message: str, *, raw: bool) -> NoReturn:
    if raw:
        _command_error(
            message,
            command=command,
            raw=True,
            error_type="BadParameter",
            stage="validate-request",
        )
    raise click.UsageError(message)


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
    except ValueError as error:
        _failed(
            command, event, raw=raw, stage="configuration", message=str(error),
            error_type=type(error).__name__,
        )


def _api_failure(command: str, event: Dict[str, Any], raw: bool, result: Any) -> None:
    if isinstance(result, dict) and result.get("error"):
        _failed(
            command, event, raw=raw, stage="api", message=str(result["error"]),
            error_type=str(result.get("error_type") or "FilmotAPIError"), payload=result,
        )


# ========== CHANNEL PROFILE ==========

@click.command("channel")
@click.argument("channels", nargs=-1, required=True)
@click.option("--about", is_flag=True, help="Add the channel description and biography.")
@click.option(
    "--growth", is_flag=True,
    help="Add view and subscriber growth over 28, 90, 180, and 365 days, and the subscriber series.",
)
@click.option("--words", is_flag=True, help="Add the most weighted caption terms.")
@click.option(
    "--history", is_flag=True,
    help="Add previous names, handles, countries, and descriptions (3 credits).",
)
@click.option(
    "--stats", is_flag=True,
    help="Add video statistics: types, durations, views, likes, and caption languages (3 credits).",
)
@click.option("--monthly", is_flag=True, help="Add video statistics by upload month (3 credits).")
@click.option("--all", "everything", is_flag=True, help="Add every group (3 credits).")
@click.option(
    "--since", type=click.DateTime(formats=["%Y-%m-%d"]), metavar="YYYY-MM-DD",
    help="First upload date for video statistics, or first observation for the subscriber series (YYYY-MM-DD).",
)
@click.option(
    "--until", type=click.DateTime(formats=["%Y-%m-%d"]), metavar="YYYY-MM-DD",
    help="Last upload date or observation (YYYY-MM-DD); defaults to today in UTC.",
)
@click.option("--include-archive", is_flag=True, help="Also count archived videos in video statistics.")
@click.option("--raw", is_flag=True, help="Output one versioned JSON result")
def channel(
    channels: Sequence[str],
    about: bool,
    growth: bool,
    words: bool,
    history: bool,
    stats: bool,
    monthly: bool,
    everything: bool,
    since,
    until,
    include_archive: bool,
    raw: bool,
):
    """Show Filmot's profile and analytics for up to 20 channels.

    CHANNELS are UC channel IDs, @handles, or channel URLs; each @handle costs
    one channel-search lookup. On the direct Filmot API the profile and counts
    cost 1 credit; --history, --stats, --monthly, or --all cost 3. Counts are
    Filmot snapshots and may differ from what YouTube shows now.

    \b
      filmot channel @LinusTechTips
      filmot channel UCXuqSBlHAE6Xw-yeJA0Tunw --growth --words
      filmot channel @LinusTechTips --stats --since 2026-01-01
    """
    from ..ledger import log_result

    references = _references("channel", channels, raw=raw)
    chosen = dict(zip(_GROUP_ORDER, (about, growth, words, history, stats, monthly)))
    groups = ["all"] if everything else [group for group in _GROUP_ORDER if chosen[group]]
    start_date = since.date().isoformat() if since else None
    end_date = until.date().isoformat() if until else None
    if (start_date or end_date) and not DATED_GROUPS.intersection(groups):
        _usage_error(
            "channel", "--since and --until apply to --growth, --stats, --monthly, or --all",
            raw=raw,
        )
    if start_date and end_date and start_date > end_date:
        _usage_error("channel", "--since must not be later than --until", raw=raw)
    if include_archive and not VIDEO_STAT_GROUPS.intersection(groups):
        _usage_error(
            "channel", "--include-archive applies to --stats, --monthly, or --all", raw=raw,
        )
    columns = channel_columns(groups)
    event = {
        "channels": references,
        "columns": columns,
        "start_date": start_date,
        "end_date": end_date,
        "include_archive": include_archive,
        "raw": raw,
    }

    def fetch(client):
        with _status("[bold green]Looking up channels...", raw=raw):
            resolved = resolve_channels(references, client, limit=PROFILE_CHANNEL_LIMIT)
            return resolved, client.get_channels(
                [entry["channel_id"] for entry in resolved],
                columns=columns,
                start_date=start_date,
                end_date=end_date,
                include_archive=include_archive,
            )

    resolved, result = _lookup("channel", event, raw, "/getchannels", fetch)
    _api_failure("channel", event, raw, result)
    records = [dict(record) for record in result if isinstance(record, dict)]
    returned = {record.get("channelid") for record in records}
    data: ChannelProfileResultData = {
        "requested": resolved,
        "channels": records,
        "missing": [
            entry["channel_id"] for entry in resolved if entry["channel_id"] not in returned
        ],
        "columns": columns,
        "start_date": start_date,
        "end_date": end_date,
        "include_archive": include_archive,
    }
    outcome = CommandResult(
        command="channel",
        status=ResultStatus.COMPLETED if records else ResultStatus.EMPTY,
        data=data,
    )
    if raw:
        outcome = _prepare_raw_result(outcome)
    log_result(
        "channel",
        outcome,
        data={**event, "results": len(records), "missing": len(data["missing"])},
    )
    if raw:
        _emit_raw_result(outcome, indent=2)
        return
    _display_channels(data)


def _number(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "N/A"
    return "{:,}".format(value) if isinstance(value, int) else "{:,.0f}".format(value)


def _seconds(value: Any) -> str:
    """Compact duration: 45s, 12m 5s, 2m, 1h 3m."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "N/A"
    minutes, secs = divmod(int(round(value)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return "{}h {}m".format(hours, minutes) if minutes else "{}h".format(hours)
    if minutes:
        return "{}m {}s".format(minutes, secs) if secs else "{}m".format(minutes)
    return "{}s".format(secs)


def _text(value: Any, fallback: str = "N/A") -> str:
    return escape(str(value)) if value not in (None, "") else fallback


def _display_channels(data: ChannelProfileResultData) -> None:
    records = data.get("channels") or []
    if not records:
        console.print("[yellow]No channel found in Filmot's index.[/yellow]")
    for record in records:
        _display_profile(record)
        if record.get("description") or record.get("biography"):
            _display_about(record)
        if record.get("growth") or record.get("subscribergrowth"):
            _display_growth(record)
        if record.get("wordcloud"):
            terms = ", ".join(
                escape(str(term.get("text")))
                for term in record["wordcloud"][:20]
                if isinstance(term, dict)
            )
            console.print("[bold]Top caption terms:[/bold] {}".format(terms))
        if record.get("channelchanges"):
            _display_history(record["channelchanges"])
        if isinstance(record.get("videostats"), dict):
            _display_video_stats(record["videostats"])
    missing = data.get("missing") or []
    if missing:
        console.print("[yellow]Not in Filmot's index: {}[/yellow]".format(", ".join(missing)))


def _display_profile(record: Dict[str, Any]) -> None:
    counts = record.get("counts") or {}
    recent = counts.get("recentuploads") or {}
    flags = [
        label
        for field, label in (
            ("isverified", "verified"),
            ("isofficialartist", "official artist"),
            ("isjoinable", "memberships"),
        )
        if record.get(field) in (True, 1)
    ]
    lines = [
        "[bold]{}[/bold]  {}".format(_text(record.get("name"), "Unknown"), _text(record.get("handle"), "")),
        "Channel ID: {}".format(_text(record.get("channelid"))),
        "Country: {}".format(_text(record.get("country"))),
        "Subscribers: {}   Total views: {} (as of {})".format(
            _number(record.get("subcount")),
            _number(record.get("totalviewcount")),
            _text(record.get("updatedate")),
        ),
        "Joined YouTube: {}   Profile snapshot: {}".format(
            _text(record.get("joindate")), _text(record.get("fetcheddate")),
        ),
        "Indexed videos: {} ({} partial), {} to {}".format(
            _number(counts.get("indexedvideos")),
            _number(counts.get("partialdatavideos")),
            _text(record.get("firstvideodate")),
            _text(record.get("lastvideodate")),
        ),
        "Uploads in the last 15/30/60/180/360 days: {}".format(
            " / ".join(_number(recent.get(window)) for window in ("15d", "30d", "60d", "180d", "360d")),
        ),
    ]
    if flags:
        lines.append("Flags: {}".format(", ".join(flags)))
    console.print(Panel("\n".join(lines), title="Channel", border_style="blue"))


def _display_about(record: Dict[str, Any]) -> None:
    parts = [
        "{}\n{}".format(label, escape(str(record[field])))
        for field, label in (("description", "[bold]Description[/bold]"), ("biography", "[bold]Biography[/bold]"))
        if record.get(field)
    ]
    console.print(Panel("\n\n".join(parts), title="About", border_style="dim"))


def _display_growth(record: Dict[str, Any]) -> None:
    growth = record.get("growth") or {}
    if growth:
        windows = ("28d", "90d", "180d", "365d")
        table = Table(title="Growth (observed days in parentheses)")
        table.add_column("Metric")
        for window in windows:
            table.add_column(window, justify="right")
        for metric, label in (("totalviewcount", "Total views"), ("subcount", "Subscribers")):
            changes = growth.get(metric) or {}
            cells = []
            for window in windows:
                change = changes.get(window) if isinstance(changes, dict) else None
                if not isinstance(change, dict) or not isinstance(change.get("delta"), (int, float)):
                    cells.append("N/A")
                    continue
                days = change.get("actualdays")
                cells.append("{:+,} ({}d)".format(change["delta"], "?" if days is None else days))
            table.add_row(label, *cells)
        console.print(table)
    series = [point for point in record.get("subscribergrowth") or [] if isinstance(point, dict)]
    if series:
        console.print(
            "Subscriber observations: {}, from {} ({}) to {} ({})".format(
                len(series),
                _text(series[0].get("d")), _number(series[0].get("s")),
                _text(series[-1].get("d")), _number(series[-1].get("s")),
            )
        )


def _display_history(changes: Dict[str, Any]) -> None:
    table = Table(title="Previous profile values")
    table.add_column("Field")
    table.add_column("Value")
    table.add_column("Observed", no_wrap=True)
    for field, values in changes.items():
        for change in values if isinstance(values, list) else []:
            if isinstance(change, dict):
                table.add_row(
                    escape(str(field)),
                    escape(_whole_word_summary(
                        "" if change.get("val") is None else change["val"], 80,
                    )),
                    "{} to {}".format(_text(change.get("dstart"), "?"), _text(change.get("dend"), "?")),
                )
    console.print(table)


# Filmot closes the last duration bin with the int32 maximum: no upper bound.
_OPEN_EDGE = 2_147_483_647


def _histogram_label(low: Any, high: Any) -> str:
    if isinstance(high, (int, float)) and high >= _OPEN_EDGE:
        return "{}+".format(_seconds(low))
    return "{}-{}".format(_seconds(low), _seconds(high))


def _display_video_stats(stats: Dict[str, Any]) -> None:
    types = stats.get("videotypes") or {}
    console.print(
        "[bold]Video statistics:[/bold] {} videos uploaded {} to {}{}".format(
            _number(stats.get("analyzedvideos")),
            _text(stats.get("startdate"), "?"),
            _text(stats.get("enddate"), "?"),
            "; " + ", ".join(
                "{} {}".format(_number(count), escape(str(kind))) for kind, count in types.items()
            ) if types else "",
        )
    )
    edges = stats.get("durationhistogramedgessec") or []
    bins = stats.get("durationhistogram") or []
    if edges and bins:
        console.print("Durations: {}".format(", ".join(
            "{} {}".format(_histogram_label(edges[index], edges[index + 1]), _number(count))
            for index, count in enumerate(bins)
            if index + 1 < len(edges)
        )))
    distributions = stats.get("distributions") or {}
    if distributions:
        table = Table(title="Per-video distributions")
        table.add_column("Measure")
        for column in ("n", "median", "mean", "q1", "q3", "min", "max"):
            table.add_column(column, justify="right")
        for measure, label, show in (
            ("duration", "Duration", _seconds),
            ("viewcount", "Views", _number),
            ("likecount", "Likes", _number),
        ):
            summary = distributions.get(measure)
            if isinstance(summary, dict):
                table.add_row(label, _number(summary.get("n")), *(
                    show(summary.get(column)) for column in ("median", "mean", "q1", "q3", "min", "max")
                ))
        console.print(table)
    languages = [entry for entry in stats.get("languages") or [] if isinstance(entry, dict)]
    if languages:
        console.print("Caption languages: {}".format(", ".join(
            "{} {} ({} automatic, {} manual)".format(
                escape(str(entry.get("lang"))), _number(entry.get("total")),
                _number(entry.get("automatic")), _number(entry.get("manual")),
            )
            for entry in languages[:10]
        )))
    months = [month for month in stats.get("months") or [] if isinstance(month, dict)]
    if months:
        table = Table(title="By upload month")
        for column in ("Month", "Videos", "Regular", "Shorts", "Live", "Median views", "Median length"):
            table.add_column(column, justify="left" if column == "Month" else "right")
        for month in months:
            month_types = month.get("videotypes") or {}
            month_stats = month.get("distributions") or {}
            table.add_row(
                _text(month.get("month"), "?"),
                _number(sum(
                    count for count in month_types.values()
                    if isinstance(count, int) and not isinstance(count, bool)
                )),
                _number(month_types.get("regular")),
                _number(month_types.get("shorts")),
                _number(month_types.get("livestreams")),
                _number((month_stats.get("viewcount") or {}).get("median")),
                _seconds((month_stats.get("duration") or {}).get("median")),
            )
        console.print(table)


# ========== CHANNEL VIDEOS ==========

@click.command("channel-videos")
@click.argument("channels", nargs=-1, required=True)
@click.option(
    "--count", "-n", default=20, show_default=True, type=click.IntRange(1, LISTING_PAGE_LIMIT),
    help="Videos per channel on each page; channels times --count may not exceed 200.",
)
@click.option(
    "--page", default=1, show_default=True, type=click.IntRange(min=1),
    help="First page to fetch; page 1 holds the newest videos.",
)
@click.option(
    "--pages", default=1, show_default=True, type=click.IntRange(1, MAX_LISTING_PAGES),
    help="Pages to fetch, stopping early at the end of the listing; each costs 3 credits.",
)
@click.option("--with-subtitles", is_flag=True, help="Only videos Filmot knows have subtitles.")
@click.option(
    "--raw", is_flag=True,
    help="Output one versioned JSON result; its videos array pipes into `filmot download`.",
)
def channel_videos(
    channels: Sequence[str],
    count: int,
    page: int,
    pages: int,
    with_subtitles: bool,
    raw: bool,
):
    """List up to 10 channels' videos from Filmot's index, newest first.

    CHANNELS are UC channel IDs, @handles, or channel URLs; each @handle costs
    one channel-search lookup. Filmot's index also holds videos YouTube no
    longer serves. Each page costs 3 credits on the direct Filmot API. Pipe
    --raw into `filmot download` to fetch transcripts; for removed videos they
    come from Filmot's subtitle index.

    \b
      filmot channel-videos @LinusTechTips
      filmot channel-videos UCXuqSBlHAE6Xw-yeJA0Tunw --count 100 --pages 3
      filmot channel-videos @LinusTechTips --count 50 --raw | filmot download -t ltt
    """
    from ..ledger import log_result

    references = _references("channel-videos", channels, raw=raw)
    event = {
        "channels": references,
        "count_per_channel": count,
        "first_page": page,
        "pages": pages,
        "only_with_subtitles": with_subtitles,
        "raw": raw,
    }

    def fetch(client):
        with _status("[bold green]Looking up channels...", raw=raw):
            resolved = resolve_channels(references, client, limit=LISTING_CHANNEL_LIMIT)
        if len(resolved) * count > LISTING_PAGE_LIMIT:
            _usage_error(
                "channel-videos",
                "channels times --count may not exceed {} (got {} x {})".format(
                    LISTING_PAGE_LIMIT, len(resolved), count,
                ),
                raw=raw,
            )
        if pages > 1 and not raw:
            stderr_console.print(
                "[dim]Fetching up to {} pages (3 credits each)...[/dim]".format(pages)
            )
        with _status("[bold green]Listing indexed videos...", raw=raw):
            listing = fetch_channel_videos(
                client,
                [entry["channel_id"] for entry in resolved],
                count=count,
                first_page=page,
                pages=pages,
                only_with_subtitles=with_subtitles,
                columns=VIDEO_METADATA_COLUMNS,
            )
        return resolved, listing

    resolved, listing = _lookup("channel-videos", event, raw, "/getchannelvideos", fetch)
    error = listing.get("error")
    if error and not listing["videos"]:
        _api_failure("channel-videos", event, raw, error)
    data: ChannelVideosResultData = {
        "channels": resolved,
        "count_per_channel": count,
        "first_page": page,
        "pages_fetched": listing["pages_fetched"],
        "exhausted": listing["exhausted"],
        "next_page": listing["next_page"],
        "only_with_subtitles": with_subtitles,
        "videos": listing["videos"],
    }
    if error:
        outcome = CommandResult(
            command="channel-videos",
            status=ResultStatus.PARTIAL,
            data=data,
            errors=[ErrorDetail(
                type=str(error.get("error_type") or "FilmotAPIError"),
                message=str(error["error"]),
                stage="api",
            )],
        )
    else:
        outcome = CommandResult(
            command="channel-videos",
            status=ResultStatus.COMPLETED if data["videos"] else ResultStatus.EMPTY,
            data=data,
        )
    if raw:
        outcome = _prepare_raw_result(outcome)
    log_result(
        "channel-videos",
        outcome,
        data={
            **event,
            "results": len(data["videos"]),
            "pages_fetched": data["pages_fetched"],
            "exhausted": data["exhausted"],
        },
    )
    if raw:
        _emit_raw_result(outcome, indent=2)
        return
    _display_channel_videos(data, error)


def _display_channel_videos(data: ChannelVideosResultData, error: Optional[Dict[str, Any]]) -> None:
    from ..video_metadata import caption_languages

    videos = data.get("videos") or []
    several = len(data.get("channels") or []) > 1
    if videos:
        table = Table(title="Indexed videos, newest first")
        table.add_column("Uploaded", no_wrap=True)
        table.add_column("Title")
        if several:
            table.add_column("Channel")
        table.add_column("Length", justify="right")
        table.add_column("Views", justify="right")
        table.add_column("Captions")
        table.add_column("Video ID", no_wrap=True)
        for video in videos:
            manual = caption_languages(video.get("manualsubs"))
            automatic = [lang for lang in caption_languages(video.get("autosubs")) if lang not in manual]
            captions = ", ".join(["{} (manual)".format(lang) for lang in manual] + automatic)
            title = _text(video.get("title"), "Unknown Title")
            if video.get("ispartialdata") in (True, 1):
                title += " [dim](partial record)[/dim]"
            row = [_text(video.get("uploaddate")), title]
            if several:
                row.append(_text(video.get("channelname"), "Unknown"))
            row += [
                _seconds(video.get("duration")),
                _number(video.get("viewcount")),
                escape(captions) or "none listed",
                _text(video.get("id"), ""),
            ]
            table.add_row(*row)
        console.print(table)
    else:
        console.print("[yellow]No videos found in Filmot's index.[/yellow]")
    if error:
        console.print("[yellow]Page {} failed ({}); showing the pages before it.[/yellow]".format(
            data.get("next_page"), escape(str(error.get("error_type") or error.get("error"))),
        ))
    elif data.get("exhausted"):
        console.print("[dim]End of Filmot's listing for these channels.[/dim]")
    else:
        console.print("[dim]More may be available: add --page {}.[/dim]".format(data.get("next_page")))
    if videos:
        console.print("[dim]Pipe --raw into `filmot download -t TOPIC` to fetch these transcripts.[/dim]")
