"""Filmot CLI - Command Line Interface."""

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import print as rprint
from rich.json import JSON
from rich.progress import Progress, SpinnerColumn, TextColumn
from .api import FilmotClient


console = Console()


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
@click.option("--page", "-p", default=None, type=int, help="Page number (50 results per page)")
@click.option("--category", "-c", default=None, help="Video category (e.g., 'Science & Technology')")
@click.option("--exclude", default=None, help="Categories to exclude (comma-separated)")
@click.option("--channel-id", default=None, help="Limit to specific channel ID")
@click.option("--channel", default=None, help="Find top channels matching this text, then search those")
@click.option("--channel-count", default=None, type=int, help="Limit top channels when using --channel (default 10)")
@click.option("--title", default=None, help="Filter by video title")
@click.option("--min-views", default=None, type=int, help="Minimum view count")
@click.option("--max-views", default=None, type=int, help="Maximum view count")
@click.option("--min-likes", default=None, type=int, help="Minimum like count")
@click.option("--max-likes", default=None, type=int, help="Maximum like count")
@click.option("--min-duration", default=None, type=int, help="Minimum duration in seconds")
@click.option("--max-duration", default=None, type=int, help="Maximum duration in seconds")
@click.option("--start-date", default=None, help="Start date (yyyy-mm-dd)")
@click.option("--end-date", default=None, help="End date (yyyy-mm-dd)")
@click.option("--country", default=None, type=int, help="Country code (e.g., 217=US, 153=UK)")
@click.option("--license", "license_type", default=None, type=click.Choice(["1", "2"]), help="License: 1=Standard, 2=Creative Commons")
@click.option("--sort", default=None, type=click.Choice(["viewcount", "likecount", "uploaddate", "duration", "chanrank", "id"]), help="Sort field")
@click.option("--order", default=None, type=click.Choice(["asc", "desc"]), help="Sort order")
@click.option("--manual-subs", is_flag=True, help="Search manual subtitles only (default: auto subs). Cannot search both in same request.")
@click.option("--max-query-time", default=None, type=int, help="Max query time in ms (4-15000)")
@click.option("--hit-format", default=None, type=click.Choice(["0", "1"]), help="Hit format: 0=context, 1=full lines")
@click.option("--full", is_flag=True, help="Show all matches (no truncation) - useful for AI agents")
@click.option("--raw", is_flag=True, help="Output raw JSON response")
def search(query: str, lang: str, page: int, category: str, exclude: str, 
           channel_id: str, channel: str, channel_count: int, title: str, 
           min_views: int, max_views: int, min_likes: int, max_likes: int,
           min_duration: int, max_duration: int, start_date: str, end_date: str,
           country: int, license_type: str, sort: str, order: str, manual_subs: bool,
           max_query_time: int, hit_format: str, full: bool, raw: bool):
    """Search for videos by subtitle/transcript content.
    
    Examples:
    
        filmot search "hello world"
        
        filmot search "machine learning" --lang en --category "Science & Technology"
        
        filmot search "recipe" --min-views 10000 --sort viewcount --order desc
        
        filmot search "tutorial" --channel "programming" --channel-count 5
        
        filmot search "news" --country 217 --license 2
    """
    try:
        client = FilmotClient()
        with console.status(f"[bold green]Searching subtitles for '{query}'..."):
            results = client.search_subtitles(
                query=query,
                lang=lang,
                page=page,
                category=category,
                exclude_category=exclude,
                channel_id=channel_id,
                channel=channel,
                channel_count=channel_count,
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
                sort_field=sort,
                sort_order=order,
                search_manual_subs=1 if manual_subs else None,
                max_query_time=max_query_time,
                hit_format=int(hit_format) if hit_format else None,
            )
        
        if "error" in results:
            console.print(f"[red]Error: {results['error']}[/red]")
            return
        
        if raw:
            console.print(JSON.from_data(results))
            return
        
        # Display formatted results
        _display_subtitle_results(results, query, full=full)
        
    except ValueError as e:
        console.print(f"[red]Configuration Error: {e}[/red]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


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


def _display_hit(hit: dict, video_id: str):
    """Display a single hit match, handling both hit formats."""
    start = hit.get("start", 0)
    token = hit.get("token", "")
    timestamp = _format_timestamp(start)
    link = f"https://youtube.com/watch?v={video_id}&t={int(start)}"
    
    # Check if this is hit_format=1 (has 'lines' array) or hit_format=0 (has ctx_before/after)
    lines = hit.get("lines", [])
    
    if lines:
        # Hit format 1: Full subtitle lines
        for line in lines:
            line_text = line.get("text", "")
            line_start = line.get("start", start)
            line_dur = line.get("dur", 0)
            line_ts = _format_timestamp(line_start)
            # Highlight the token in the line text
            highlighted = line_text.replace(token, f"[bold yellow]{token}[/bold yellow]")
            highlighted = highlighted.replace(token.capitalize(), f"[bold yellow]{token.capitalize()}[/bold yellow]")
            highlighted = highlighted.replace(token.upper(), f"[bold yellow]{token.upper()}[/bold yellow]")
            console.print(f"      [[link={link}]{line_ts}[/link]] {highlighted}")
    else:
        # Hit format 0: Context snippets
        ctx_before = hit.get("ctx_before", "")
        ctx_after = hit.get("ctx_after", "")
        
        # Truncate context if too long
        ctx_before = ctx_before[-50:] if len(ctx_before) > 50 else ctx_before
        ctx_after = ctx_after[:50] if len(ctx_after) > 50 else ctx_after
        
        console.print(f"      [[link={link}]{timestamp}[/link]] ...{ctx_before} [bold yellow]{token}[/bold yellow] {ctx_after}...")


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


def _display_subtitle_results(results: dict, query: str, full: bool = False):
    """Display subtitle search results with rich formatting.
    
    Args:
        results: API response dictionary
        query: Original search query
        full: If True, show all matches without truncation (useful for AI agents)
    """
    videos = results.get("result", results.get("videos", results.get("items", [])))
    
    if not videos:
        console.print("[yellow]No results found.[/yellow]")
        return
    
    total = results.get("totalresultcount", len(videos))
    console.print(Panel(f"[bold]Found {total:,} results for: {query}[/bold]"))
    
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
        
        console.print(f"\n[bold cyan]{i}. {title}[/bold cyan]")
        console.print(f"   [dim]Channel:[/dim] {channel} ({channel_subs_str} subs) | [dim]Country:[/dim] {channel_country}")
        console.print(f"   [dim]Views:[/dim] {views:,} | [dim]Likes:[/dim] {likes:,} | [dim]Duration:[/dim] {duration_str}")
        console.print(f"   [dim]Category:[/dim] {category} | [dim]Language:[/dim] {lang} | [dim]Uploaded:[/dim] {upload_date}")
        console.print(f"   [dim]Video:[/dim] https://youtube.com/watch?v={video_id}")
        console.print(f"   [dim]Channel:[/dim] https://youtube.com/channel/{channel_id}")
        
        # Display hits (subtitle matches)
        hits = video.get("hits", [])
        if hits:
            console.print(f"   [bold green]Matches ({len(hits)}):[/bold green]")
            display_hits = hits if full else hits[:3]
            for hit in display_hits:
                _display_hit(hit, video_id)
            
            if not full and len(hits) > 3:
                console.print(f"      [dim]... and {len(hits) - 3} more matches[/dim]")


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
        with console.status(f"[bold green]Fetching video metadata..."):
            result = client.get_videos(video_ids, flags=flags)
        
        if "error" in result:
            console.print(f"[red]Error: {result['error']}[/red]")
            return
        
        if raw:
            console.print(JSON.from_data(result))
            return
        
        # Display formatted results
        _display_video_results(result)
        
    except ValueError as e:
        console.print(f"[red]Configuration Error: {e}[/red]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


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
        with console.status(f"[bold green]Searching channels for '{term}'..."):
            result = client.search_channels(term)
        
        if "error" in result:
            console.print(f"[red]Error: {result['error']}[/red]")
            return
        
        if raw:
            console.print(JSON.from_data(result))
            return
        
        # Display formatted results
        _display_channel_results(result, term)
        
    except ValueError as e:
        console.print(f"[red]Configuration Error: {e}[/red]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


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
    
    console.print(table)


# ========== INTERACTIVE MODE ==========

@cli.command()
def interactive():
    """Start interactive REPL mode."""
    from .interactive import start_repl
    start_repl()


# ========== CACHE MANAGEMENT ==========

@cli.command()
@click.option("--clear", is_flag=True, help="Clear all cache entries")
@click.option("--clear-expired", is_flag=True, help="Clear only expired entries")
def cache(clear: bool, clear_expired: bool):
    """Manage the response cache."""
    from .cache import get_cache
    
    cache_instance = get_cache()
    
    if clear:
        count = cache_instance.clear()
        console.print(f"[green]✓ Cleared {count} cache entries[/green]")
    elif clear_expired:
        count = cache_instance.clear_expired()
        console.print(f"[green]✓ Cleared {count} expired entries[/green]")
    else:
        stats = cache_instance.stats()
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
@click.option("--pages", "-p", default=1, type=int, help="Number of pages to fetch (default 1)")
@click.option("--detailed", is_flag=True, help="Export detailed hits (one row per hit for CSV)")
@click.option("--lang", "-l", default=None, help="Language code")
@click.option("--min-views", default=None, type=int, help="Minimum view count")
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
        console.print(f"[red]Error: {results['error']}[/red]")
        return
    
    try:
        if fmt == "json":
            path = export_json(results, output)
        elif detailed:
            path = export_hits_detailed(results, output)
        else:
            path = export_csv(results, output)
        
        video_count = len(results.get("result", []))
        console.print(f"[green]✓ Exported {video_count} videos to: {path}[/green]")
    except Exception as e:
        console.print(f"[red]Export failed: {e}[/red]")


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
        console.print(f"[red]File not found: {file}[/red]")
        console.print("[dim]Tip: Use 'filmot batch-template' to create a sample file[/dim]")
        return
    
    client = FilmotClient()
    processor = BatchProcessor(client)
    
    try:
        queries = processor.load_queries_from_file(file)
    except Exception as e:
        console.print(f"[red]Failed to load queries: {e}[/red]")
        return
    
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
            console.print(f"[red]Export failed: {e}[/red]")


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
    
    wl = get_watchlist()
    watched_filter = False if unwatched else None
    items = wl.get_watchlist(tag=tag, watched=watched_filter)
    
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
    
    # Fetch video info first
    client = FilmotClient()
    with console.status(f"[bold green]Fetching video info..."):
        result = client.get_videos(video_id)
    
    if "error" in result or not result:
        console.print(f"[red]Could not fetch video: {video_id}[/red]")
        return
    
    video = result[0] if isinstance(result, list) else result
    video["id"] = video_id  # Ensure ID is set
    
    wl = get_watchlist()
    if wl.add_video(video, notes):
        console.print(f"[green]✓ Added: {video.get('title', video_id)}[/green]")
    else:
        console.print("[yellow]Video already in watchlist.[/yellow]")


@watchlist.command("remove")
@click.argument("video_id")
def watchlist_remove(video_id: str):
    """Remove a video from watchlist."""
    from .watchlist import get_watchlist
    
    wl = get_watchlist()
    if wl.remove_video(video_id):
        console.print(f"[green]✓ Removed video: {video_id}[/green]")
    else:
        console.print(f"[yellow]Video not found in watchlist.[/yellow]")


@watchlist.command("watched")
@click.argument("video_id")
def watchlist_watched(video_id: str):
    """Mark a video as watched."""
    from .watchlist import get_watchlist
    
    wl = get_watchlist()
    if wl.mark_watched(video_id, True):
        console.print(f"[green]✓ Marked as watched[/green]")
    else:
        console.print(f"[yellow]Video not found in watchlist.[/yellow]")


@watchlist.command("clear")
@click.confirmation_option(prompt="Are you sure you want to clear the watchlist?")
def watchlist_clear():
    """Clear all watchlist items."""
    from .watchlist import get_watchlist
    
    wl = get_watchlist()
    count = wl.clear_watchlist()
    console.print(f"[green]✓ Cleared {count} items from watchlist[/green]")


# ========== PAGINATED SEARCH ==========

@cli.command("search-all")
@click.argument("query")
@click.option("--pages", "-p", default=5, type=int, help="Max pages to fetch (default 5)")
@click.option("--max-results", default=None, type=int, help="Max total results")
@click.option("--lang", "-l", default=None, help="Language code")
@click.option("--min-views", default=None, type=int, help="Minimum view count")
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
        console.print(f"[red]Error: {results['error']}[/red]")
        return
    
    videos = results.get("result", [])
    total = results.get("totalresultcount", len(videos))
    pages_fetched = results.get("pages_fetched", 1)
    
    console.print(Panel(
        f"[bold]Fetched {len(videos)} of {total:,} total results ({pages_fetched} pages)[/bold]"
    ))
    
    if output:
        try:
            if fmt == "json":
                path = export_json(results, output)
            else:
                path = export_csv(results, output)
            console.print(f"[green]✓ Exported to: {path}[/green]")
        except Exception as e:
            console.print(f"[red]Export failed: {e}[/red]")
    else:
        # Display summary
        _display_subtitle_results(results, query)


# ========== TRANSCRIPT DOWNLOAD ==========

@cli.command("transcript")
@click.argument("video_id", nargs=1)
@click.option("--lang", "-l", default=None, help="Preferred language code (e.g., en, es, de)")
@click.option("--timestamps", "-t", is_flag=True, help="Include timestamps for each segment")
@click.option("--chunk", "-c", default=None, type=float, help="Chunk transcript into N-minute segments")
@click.option("--raw", is_flag=True, help="Output raw JSON response")
@click.option("--output", "-o", default=None, help="Save transcript to file")
@click.option("--full", is_flag=True, help="Output complete transcript text (for AI processing)")
@click.option("--proxy", default=None, help="HTTP/HTTPS proxy URL (e.g., http://user:pass@host:port)")
@click.option("--no-proxy", is_flag=True, help="Disable proxy (ignore env vars, connect directly)")
@click.pass_context
def transcript(ctx, video_id: str, lang: str, timestamps: bool, chunk: float, 
               raw: bool, output: str, full: bool, proxy: str, no_proxy: bool):
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
    
    Examples:
    
    \b
        filmot transcript dQw4w9WgXcQ
        
        filmot transcript "https://youtube.com/watch?v=VIDEO_ID" --full
        
        filmot transcript VIDEO_ID --timestamps --chunk 5
        
        filmot transcript VIDEO_ID -o transcript.txt
        
        filmot transcript VIDEO_ID --proxy http://user:pass@host:port
        
        filmot transcript VIDEO_ID --raw > data.json
    """
    from .transcript import get_transcript, get_transcript_with_timestamps, format_timestamp, configure_proxy, is_proxy_configured, disable_proxy
    import json
    
    # Handle proxy configuration
    if no_proxy:
        disable_proxy()
        console.print(f"[dim]Proxy disabled, using direct connection[/dim]")
    elif proxy:
        try:
            configure_proxy(http_proxy=proxy)
            console.print(f"[dim]Using proxy: {proxy.split('@')[-1] if '@' in proxy else proxy}[/dim]")
        except Exception as e:
            console.print(f"[red]Proxy error: {e}[/red]")
            return
    elif is_proxy_configured():
        console.print(f"[dim]Using proxy from environment[/dim]")
    
    with console.status(f"[bold green]Fetching transcript..."):
        languages = [lang] if lang else None
        
        if chunk:
            result = get_transcript_with_timestamps(video_id, languages, chunk_minutes=chunk)
        else:
            result = get_transcript(video_id, languages)
    
    if "error" in result:
        console.print(f"[red]Error: {result['error']}[/red]")
        console.print(f"[dim]Video ID: {result.get('video_id', video_id)}[/dim]")
        if "IpBlocked" in str(result.get('error', '')) or "blocked" in str(result.get('error', '')).lower():
            console.print("\n[yellow]Tip: Your IP is blocked by YouTube. Try using a proxy:[/yellow]")
            console.print("  filmot transcript VIDEO_ID --proxy http://user:pass@host:port")
            console.print("  Or set WEBSHARE_PROXY_USERNAME/PASSWORD in .env for rotating proxies")
        return
    
    # Raw JSON output
    if raw:
        import json
        print(json.dumps(result, indent=2))
        return
    
    # Save to file
    if output:
        try:
            with open(output, 'w', encoding='utf-8') as f:
                if output.endswith('.json'):
                    import json
                    json.dump(result, f, indent=2)
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
            console.print(f"[red]Error saving file: {e}[/red]")
            return
    
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
@click.option("--context", "-c", default=2, type=int, help="Number of segments for context (default: 2)")
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
        console.print(f"[red]Error: {result['error']}[/red]")
        return
    
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
@click.option("--days", "-d", default=7, type=int, help="Search videos from last N days (default: 7)")
@click.option("--max-results", "-n", default=25, type=int, help="Maximum results (default: 25, max: 50)")
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
            console.print(f"   [link={video['url']}]{video['url']}[/link]")
            
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
        console.print(f"[red]Configuration Error: {e}[/red]")
        console.print("[yellow]Add YOUTUBE_API_KEY to your .env file[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


def main():
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
