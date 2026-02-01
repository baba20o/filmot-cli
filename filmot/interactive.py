"""Interactive REPL mode for Filmot CLI."""

import cmd
import shlex
from typing import Optional, Dict, Any, List
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown

from .api import FilmotClient
from .watchlist import get_watchlist
from .export import export_json, export_csv, generate_filename
from .cache import get_cache


console = Console()


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
        
        Examples:
            search machine learning
            search "python tutorial" --lang en --min-views 10000
        """
        if not arg.strip():
            console.print("[yellow]Please provide a search query[/yellow]")
            return
        
        # Parse arguments
        try:
            args = shlex.split(arg)
        except ValueError:
            args = arg.split()
        
        query = []
        params = dict(self.defaults)
        
        i = 0
        while i < len(args):
            if args[i].startswith("--"):
                key = args[i][2:].replace("-", "_")
                if i + 1 < len(args) and not args[i + 1].startswith("--"):
                    value = args[i + 1]
                    # Convert numeric values
                    try:
                        value = int(value)
                    except ValueError:
                        pass
                    params[key] = value
                    i += 2
                else:
                    params[key] = True
                    i += 1
            else:
                query.append(args[i])
                i += 1
        
        query_str = " ".join(query)
        if not query_str:
            console.print("[yellow]Please provide a search query[/yellow]")
            return
        
        self.last_query = query_str
        self.history.append(f"search {arg}")
        
        with console.status(f"[bold green]Searching for '{query_str}'..."):
            results = self.client.search_subtitles(query=query_str, **params)
        
        if "error" in results:
            console.print(f"[red]Error: {results['error']}[/red]")
            return
        
        self.last_results = results
        self._display_results(results, query_str)
    
    def _display_results(self, results: Dict[str, Any], query: str):
        """Display search results."""
        videos = results.get("result", [])
        total = results.get("totalresultcount", len(videos))
        
        if not videos:
            console.print("[yellow]No results found.[/yellow]")
            return
        
        console.print(Panel(f"[bold]Found {total:,} results for: {query}[/bold]"))
        
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
                video.get("title", "")[:50],
                video.get("channelname", "")[:20],
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
        console.print(f"\n[bold cyan]{video.get('title', 'Unknown')}[/bold cyan]")
        console.print(f"Channel: {video.get('channelname', '')} | Views: {video.get('viewcount', 0):,}")
        console.print(f"Duration: {video.get('duration', 0) // 60}m | Uploaded: {video.get('uploaddate', '')}")
        console.print(f"URL: https://youtube.com/watch?v={video.get('id', '')}")
        
        hits = video.get("hits", [])
        if hits:
            console.print(f"\n[bold green]Subtitle Matches ({len(hits)}):[/bold green]")
            for hit in hits[:10]:
                start = hit.get("start", 0)
                mins, secs = divmod(int(start), 60)
                
                lines = hit.get("lines", [])
                if lines:
                    for line in lines[:3]:
                        console.print(f"  [{mins}:{secs:02d}] {line.get('text', '')}")
                else:
                    token = hit.get("token", "")
                    ctx = f"{hit.get('ctx_before', '')} [bold yellow]{token}[/bold yellow] {hit.get('ctx_after', '')}"
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
            console.print(f"[green]✓ Saved: {video.get('title', '')[:50]}[/green]")
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
                item.get("title", "")[:40],
                item.get("channel_name", "")[:20],
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
            
            console.print(f"[green]✓ Exported to: {path}[/green]")
        except Exception as e:
            console.print(f"[red]Export failed: {e}[/red]")
    
    def do_defaults(self, arg: str):
        """View or set default search parameters.
        
        Usage:
            defaults              - Show current defaults
            defaults lang en      - Set default language
            defaults min_views 1000
            defaults clear        - Reset all defaults
        """
        if not arg:
            console.print("[bold]Current defaults:[/bold]")
            for key, value in self.defaults.items():
                console.print(f"  {key}: {value or '(not set)'}")
            return
        
        parts = arg.split(maxsplit=1)
        
        if parts[0] == "clear":
            self.defaults = {k: None for k in self.defaults}
            console.print("[green]✓ Defaults cleared[/green]")
            return
        
        if len(parts) == 2:
            key = parts[0]
            value = parts[1]
            
            if key in self.defaults:
                try:
                    value = int(value)
                except ValueError:
                    pass
                self.defaults[key] = value
                console.print(f"[green]✓ Set {key} = {value}[/green]")
            else:
                console.print(f"[yellow]Unknown parameter: {key}[/yellow]")
        else:
            console.print("[yellow]Usage: defaults <key> <value>[/yellow]")
    
    def do_history(self, arg: str):
        """Show command history."""
        if not self.history:
            console.print("[yellow]No history yet.[/yellow]")
            return
        
        for i, cmd in enumerate(self.history[-20:], 1):
            console.print(f"  {i}. {cmd}")
    
    def do_cache(self, arg: str):
        """Manage the response cache.
        
        Usage:
            cache           - Show cache stats
            cache clear     - Clear all cache
            cache expired   - Clear only expired entries
        """
        cache = get_cache()
        
        if not arg:
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
            console.print("[yellow]Unknown cache command. Use: clear, expired[/yellow]")
    
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
- Use `--param value` in searches for filters
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
        repl.cmdloop()
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted. Goodbye! 👋[/dim]")
