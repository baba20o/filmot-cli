"""Interactive REPL mode for Filmot CLI."""

import cmd
import shlex
from typing import Optional, Dict, Any, List
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown

from .api import FilmotClient
from .watchlist import get_watchlist
from .export import export_json, export_csv, generate_filename
from .cache import get_cache


console = Console()

# Search flags, named as on ``filmot search``, and the client keyword each sets.
SEARCH_FLAGS = {
    "lang": "lang",
    "page": "page",
    "category": "category",
    "exclude": "exclude_category",
    "channel_id": "channel_id",
    "channel": "channel",
    "channel_count": "channel_count",
    "title": "title",
    "min_views": "min_views",
    "max_views": "max_views",
    "min_likes": "min_likes",
    "max_likes": "max_likes",
    "min_duration": "start_duration",
    "max_duration": "end_duration",
    "start_date": "start_date",
    "end_date": "end_date",
    "country": "country",
    "license": "license_type",
    "sort": "sort_field",
    "order": "sort_order",
    "manual_subs": "search_manual_subs",
    "max_query_time": "max_query_time",
    "hit_format": "hit_format",
}
INTEGER_FLAGS = {
    "page", "channel_count", "min_views", "max_views", "min_likes", "max_likes",
    "min_duration", "max_duration", "country", "license", "max_query_time", "hit_format",
}
# Takes no value: --manual-subs alone searches manual subtitles.
SWITCH_FLAGS = {"manual_subs"}
# Values `filmot search` accepts. Its --sort density is a client-side sort
# the REPL does not perform.
FLAG_CHOICES = {
    "sort": ("viewcount", "likecount", "uploaddate", "duration", "chanrank", "id"),
    "order": ("asc", "desc"),
    "license": (1, 2),
    "hit_format": (0, 1),
}
# Bounds for integer flags; the rest only need to be at least 0.
FLAG_RANGES = {"page": (1, None), "channel_count": (1, None), "max_query_time": (4, 15000)}


def _flag(name: str) -> str:
    return "--" + name.replace("_", "-")


def _flag_value(name: str, value: str):
    """``value`` converted and checked for flag ``name``; ValueError explains a bad one."""
    if name in INTEGER_FLAGS:
        try:
            value = int(value)
        except ValueError:
            raise ValueError("{} needs a whole number, not {!r}".format(_flag(name), value)) from None
        low, high = FLAG_RANGES.get(name, (0, None))
        if value < low or (high is not None and value > high):
            limit = "at least {}".format(low) if high is None else "{} to {}".format(low, high)
            raise ValueError("{} must be {}".format(_flag(name), limit))
    choices = FLAG_CHOICES.get(name)
    if choices is not None and value not in choices:
        hint = "; density sorting is in filmot search only" if value == "density" else ""
        raise ValueError("{} must be one of: {}{}".format(
            _flag(name), ", ".join(str(choice) for choice in choices), hint
        ))
    return value


class FilmotREPL(cmd.Cmd):
    """Interactive command-line interface for Filmot."""
    
    intro = """
╔═══════════════════════════════════════════════════════════════╗
║           🎬 Filmot Interactive Mode 🎬                        ║
║                                                               ║
║  Search YouTube transcripts interactively                     ║
║  Type 'help' for commands, 'quit' to exit                     ║
╚═══════════════════════════════════════════════════════════════╝
"""
    prompt = "filmot> "
    
    def __init__(self):
        super().__init__()
        self.client = FilmotClient()
        self.watchlist = get_watchlist()
        self.last_results: Optional[Dict[str, Any]] = None
        self.last_query: str = ""
        self.history: List[str] = []
        
        # Default search params
        self.defaults = {
            "lang": None,
            "min_views": None,
            "max_views": None,
            "category": None,
            "sort": None,
            "order": None
        }
    
    def default(self, line: str):
        """Handle unknown commands as search queries."""
        if line.strip():
            self.do_search(line)
    
    def do_search(self, arg: str):
        """Search for videos by subtitle content.

        Usage: search <query> [--lang en] [--min-views 1000] [--category Education]

        Filters use the names of `filmot search` options, such as --sort,
        --order, --exclude, --min-duration, and --manual-subs.

        Examples:
            search machine learning
            search "python tutorial" --lang en --min-views 10000
        """
        if not arg.strip():
            console.print("[yellow]Please provide a search query[/yellow]")
            return

        try:
            query_str, params = self._parse_search(arg)
        except ValueError as error:
            console.print(f"[yellow]{escape(str(error))}[/yellow]")
            return
        if not query_str:
            console.print("[yellow]Please provide a search query[/yellow]")
            return

        self.last_query = query_str
        self.history.append(f"search {arg}")

        try:
            with console.status(f"[bold green]Searching for '{escape(query_str)}'..."):
                results = self.client.search_subtitles(query=query_str, **params)
        except KeyboardInterrupt:
            console.print("[yellow]Search cancelled.[/yellow]")
            return
        except Exception as error:  # a failed search must not end the session
            console.print(f"[red]Search failed: {escape(f'{type(error).__name__}: {error}')}[/red]")
            return

        if "error" in results:
            console.print(f"[red]Error: {escape(str(results['error']))}[/red]")
            return
        
        self.last_results = results
        self._display_results(results, query_str)

    def _parse_search(self, arg: str):
        """Split a search line into its query and client keyword arguments.

        Raises ValueError, with a message for the user, on an unknown flag or
        a missing or malformed value, so a typo never spends a search.
        """
        try:
            args = shlex.split(arg)
        except ValueError:
            args = arg.split()

        query = []
        options = {name: value for name, value in self.defaults.items() if value is not None}
        i = 0
        while i < len(args):
            token = args[i]
            i += 1
            if not token.startswith("--"):
                query.append(token)
                continue
            name = token[2:].replace("-", "_")
            if name not in SEARCH_FLAGS:
                raise ValueError("Unknown option {}. Options: {}".format(
                    token, " ".join(_flag(flag) for flag in SEARCH_FLAGS)
                ))
            if name in SWITCH_FLAGS:
                options[name] = 1
                continue
            if i >= len(args) or args[i].startswith("--"):
                raise ValueError("{} needs a value".format(token))
            options[name] = _flag_value(name, args[i])
            i += 1

        for label in ("views", "likes", "duration"):
            minimum, maximum = options.get("min_" + label), options.get("max_" + label)
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError("minimum {0} cannot exceed maximum {0}".format(label))
        return " ".join(query), {SEARCH_FLAGS[name]: value for name, value in options.items()}

    def _display_results(self, results: Dict[str, Any], query: str):
        """Display search results."""
        videos = results.get("result", [])
        total = results.get("totalresultcount", len(videos))
        
        if not videos:
            console.print("[yellow]No results found.[/yellow]")
            return
        
        console.print(Panel(f"[bold]Found {total:,} results for: {escape(query)}[/bold]"))
        
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("#", width=3)
        table.add_column("Title", max_width=50)
        table.add_column("Channel", max_width=20)
        table.add_column("Views", justify="right")
        table.add_column("Hits", justify="right")
        
        for i, video in enumerate(videos[:20], 1):
            views = video.get("viewcount", 0)
            views_str = f"{views:,}" if views < 1000000 else f"{views/1000000:.1f}M"
            
            table.add_row(
                str(i),
                escape(str(video.get("title", ""))[:50]),
                escape(str(video.get("channelname", ""))[:20]),
                views_str,
                str(len(video.get("hits", [])))
            )
        
        console.print(table)
        
        if len(videos) > 20:
            console.print(f"[dim]Showing 20 of {len(videos)} results. Use 'show N' to view more.[/dim]")
    
    def do_show(self, arg: str):
        """Show details for a specific result.
        
        Usage: show <number>
        
        Example: show 1
        """
        if not self.last_results:
            console.print("[yellow]No results to show. Run a search first.[/yellow]")
            return
        
        try:
            idx = int(arg) - 1
        except ValueError:
            console.print("[yellow]Please provide a result number.[/yellow]")
            return
        
        videos = self.last_results.get("result", [])
        if idx < 0 or idx >= len(videos):
            console.print(f"[yellow]Invalid number. Choose 1-{len(videos)}[/yellow]")
            return
        
        video = videos[idx]
        self._display_video_detail(video)
    
    def _display_video_detail(self, video: Dict[str, Any]):
        """Display detailed video information."""
        console.print(f"\n[bold cyan]{escape(str(video.get('title', 'Unknown')))}[/bold cyan]")
        console.print(f"Channel: {escape(str(video.get('channelname', '')))} | Views: {video.get('viewcount', 0):,}")
        console.print(f"Duration: {video.get('duration', 0) // 60}m | Uploaded: {escape(str(video.get('uploaddate', '')))}")
        console.print(f"URL: https://youtube.com/watch?v={escape(str(video.get('id', '')))}")
        
        hits = video.get("hits", [])
        if hits:
            console.print(f"\n[bold green]Subtitle Matches ({len(hits)}):[/bold green]")
            for hit in hits[:10]:
                start = hit.get("start", 0)
                mins, secs = divmod(int(start), 60)
                
                lines = hit.get("lines", [])
                if lines:
                    for line in lines[:3]:
                        console.print(f"  [{mins}:{secs:02d}] {escape(str(line.get('text', '')))}")
                else:
                    token = escape(str(hit.get("token", "")))
                    ctx = (
                        f"{escape(str(hit.get('ctx_before', '')))} [bold yellow]{token}[/bold yellow] "
                        f"{escape(str(hit.get('ctx_after', '')))}"
                    )
                    console.print(f"  [{mins}:{secs:02d}] ...{ctx}...")
            
            if len(hits) > 10:
                console.print(f"  [dim]... and {len(hits) - 10} more matches[/dim]")
    
    def do_save(self, arg: str):
        """Save a result to your watchlist.
        
        Usage: save <number> [notes]
        
        Example: save 1 Great tutorial on ML basics
        """
        if not self.last_results:
            console.print("[yellow]No results to save. Run a search first.[/yellow]")
            return
        
        parts = arg.split(maxsplit=1)
        if not parts:
            console.print("[yellow]Please provide a result number.[/yellow]")
            return
        
        try:
            idx = int(parts[0]) - 1
        except ValueError:
            console.print("[yellow]Please provide a valid number.[/yellow]")
            return
        
        notes = parts[1] if len(parts) > 1 else ""
        
        videos = self.last_results.get("result", [])
        if idx < 0 or idx >= len(videos):
            console.print(f"[yellow]Invalid number. Choose 1-{len(videos)}[/yellow]")
            return
        
        video = videos[idx]
        if self.watchlist.add_video(video, notes):
            console.print(f"[green]✓ Saved: {escape(str(video.get('title', ''))[:50])}[/green]")
        else:
            console.print("[yellow]Video already in watchlist.[/yellow]")
    
    def do_watchlist(self, arg: str):
        """View your watchlist.
        
        Usage: watchlist [--unwatched] [--tag TAG]
        """
        tag = None
        watched = None
        
        if "--unwatched" in arg:
            watched = False
        
        items = self.watchlist.get_watchlist(tag=tag, watched=watched)
        
        if not items:
            console.print("[yellow]Watchlist is empty.[/yellow]")
            return
        
        table = Table(title="📺 Watchlist", show_header=True)
        table.add_column("#", width=3)
        table.add_column("Title", max_width=40)
        table.add_column("Channel", max_width=20)
        table.add_column("Status", width=8)
        table.add_column("Added", width=12)
        
        for i, item in enumerate(items, 1):
            status = "✓" if item.get("watched") else ""
            added = item.get("added_at", "")[:10]
            
            table.add_row(
                str(i),
                escape(str(item.get("title", ""))[:40]),
                escape(str(item.get("channel_name", ""))[:20]),
                status,
                added
            )
        
        console.print(table)
    
    def do_export(self, arg: str):
        """Export last results to file.
        
        Usage: export [filename] [--format json|csv]
        
        Examples:
            export
            export results.json
            export my_search.csv --format csv
        """
        if not self.last_results:
            console.print("[yellow]No results to export. Run a search first.[/yellow]")
            return
        
        args = shlex.split(arg) if arg else []
        
        # Parse format
        fmt = "json"
        filename = None
        
        i = 0
        while i < len(args):
            if args[i] == "--format" and i + 1 < len(args):
                fmt = args[i + 1]
                i += 2
            else:
                filename = args[i]
                i += 1
        
        if not filename:
            filename = generate_filename("filmot_results", fmt)
        
        try:
            if fmt == "json":
                path = export_json(self.last_results, filename)
            else:
                path = export_csv(self.last_results, filename)
            
            console.print(f"[green]✓ Exported to: {escape(str(path))}[/green]")
        except Exception as e:
            console.print(f"[red]Export failed: {escape(str(e))}[/red]")
    
    def do_defaults(self, arg: str):
        """View or set default search parameters.
        
        Usage:
            defaults              - Show current defaults
            defaults lang en      - Set default language
            defaults min_views 1000
            defaults clear        - Reset all defaults

        Any search filter that takes a value can have a default.
        """
        if not arg:
            console.print("[bold]Current defaults:[/bold]")
            for key, value in self.defaults.items():
                console.print(f"  {key}: {escape(str(value or '(not set)'))}")
            return

        parts = arg.split(maxsplit=1)

        if parts[0] == "clear":
            self.defaults = {k: None for k in self.defaults}
            console.print("[green]✓ Defaults cleared[/green]")
            return

        if len(parts) == 2:
            key = parts[0].replace("-", "_")
            if key not in SEARCH_FLAGS or key in SWITCH_FLAGS:
                console.print(f"[yellow]Unknown parameter: {escape(parts[0])}[/yellow]")
                return
            try:
                value = _flag_value(key, parts[1])
            except ValueError as error:
                console.print(f"[yellow]{escape(str(error))}[/yellow]")
                return
            self.defaults[key] = value
            console.print(f"[green]✓ Set {key} = {escape(str(value))}[/green]")
        else:
            console.print("[yellow]Usage: defaults <key> <value>[/yellow]")
    
    def do_history(self, arg: str):
        """Show command history."""
        if not self.history:
            console.print("[yellow]No history yet.[/yellow]")
            return
        
        for i, cmd in enumerate(self.history[-20:], 1):
            console.print(f"  {i}. {escape(cmd)}")

    def do_cache(self, arg: str):
        """Manage the response cache.

        Usage:
            cache           - Show cache stats (also: cache stats)
            cache clear     - Clear all cache
            cache expired   - Clear only expired entries
        """
        cache = get_cache()
        arg = arg.strip()

        if arg in ("", "stats"):
            stats = cache.stats()
            console.print("[bold]Cache Statistics:[/bold]")
            console.print(f"  Total entries: {stats['total_entries']}")
            console.print(f"  Valid: {stats['valid_entries']}")
            console.print(f"  Expired: {stats['expired_entries']}")
            console.print(f"  Size: {stats['size_mb']} MB")
            console.print(f"  TTL: {stats['ttl_seconds']}s")
        elif arg == "clear":
            count = cache.clear()
            console.print(f"[green]✓ Cleared {count} cache entries[/green]")
        elif arg == "expired":
            count = cache.clear_expired()
            console.print(f"[green]✓ Cleared {count} expired entries[/green]")
        else:
            console.print("[yellow]Unknown cache command. Use: stats, clear, expired[/yellow]")
    
    def do_help(self, arg: str):
        """Show help information."""
        if arg:
            # Show help for specific command
            super().do_help(arg)
        else:
            help_text = """
## Available Commands

### Search
- `search <query>` - Search subtitles (or just type the query)
- `show <n>` - Show details for result #n
- `defaults` - View/set default search params

### Watchlist
- `save <n> [notes]` - Save result #n to watchlist
- `watchlist` - View your watchlist

### Export
- `export [file]` - Export results to JSON/CSV

### Utilities
- `cache` - Manage response cache
- `history` - Show command history
- `quit` or `exit` - Exit interactive mode

### Tips
- Just type a query to search (no need for 'search' prefix)
- Filter with `filmot search` option names, such as `--lang en`,
  `--sort viewcount --order desc`, `--min-duration 600`, or `--manual-subs`
"""
            console.print(Markdown(help_text))
    
    def do_quit(self, arg: str):
        """Exit interactive mode."""
        console.print("[dim]Goodbye! 👋[/dim]")
        return True
    
    def do_exit(self, arg: str):
        """Exit interactive mode."""
        return self.do_quit(arg)
    
    def do_EOF(self, arg: str):
        """Handle Ctrl+D."""
        console.print()
        return self.do_quit(arg)
    
    def emptyline(self):
        """Don't repeat last command on empty line."""
        pass


def start_repl():
    """Start the interactive REPL."""
    try:
        repl = FilmotREPL()
    except ValueError as error:  # no usable API credentials, for one
        console.print(f"[red]Configuration error: {escape(str(error))}[/red]")
        raise SystemExit(1) from None
    try:
        repl.cmdloop()
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted. Goodbye! 👋[/dim]")
