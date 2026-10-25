# Filmot CLI

Search inside indexed YouTube transcripts, discover videos and channels, and
build a local research library with timestamped passages and source metadata.
Filmot CLI uses [Filmot's official API](https://filmot.com/api-documentation),
with optional YouTube Data API discovery and metadata reads.

[Quick start](#quick-start) · [Examples](#examples) ·
[Command reference](https://github.com/baba20o/filmot-cli/blob/main/docs/CLI_REFERENCE.md) ·
[Configuration](https://github.com/baba20o/filmot-cli/blob/main/docs/CONFIGURATION.md) · [Contributing](https://github.com/baba20o/filmot-cli/blob/main/CONTRIBUTING.md)

## What you can do

- Search spoken words with phrases, proximity operators, title filters, and
  bounded pagination.
- Discover videos by title or metadata, inspect channel profiles, and export
  Filmot results as JSON, JSON Lines, CSV, or subtitle ZIPs.
- Acquire available transcripts, save them by topic, and read or compare
  timestamped passages locally.
- Run staged research, maintain named sessions, and record claims with explicit
  evidence and human assessments.
- Use the optional YouTube Data API for recent-upload discovery, exact video
  metadata, playlists, and public comments/replies.
- Feed versioned JSON results into scripts while retaining errors, partial
  results, and continuation information.

Search results reflect the upstream index and selected scope. Title matching,
view counts, and ranking are navigation aids; verify a source's content before
using it as evidence.

## Quick start

Use **Python 3.9 or newer**. A Filmot API key enables indexed search and Filmot
metadata features. Reading an existing local library does not require a key.

### Install

```bash
git clone https://github.com/baba20o/filmot-cli.git
cd filmot-cli
python -m venv .venv
```

Activate the environment on Linux/macOS:

```bash
source .venv/bin/activate
```

Or in Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Then install and check the CLI:

```bash
python -m pip install -e .
filmot --help
```

You can also run `python -m filmot` or `python main.py` from this checkout.
For a direct-Python setup without installing the package, install
`requirements.txt` first. Development and distribution instructions are in
[CONTRIBUTING.md](https://github.com/baba20o/filmot-cli/blob/main/CONTRIBUTING.md).

### Configure your key

Read [Filmot's official API documentation](https://filmot.com/api-documentation)
and create a key at [Filmot API keys](https://filmot.com/api-keys). Set it in
your per-user `config.env`, or create a `.env` in the directory where you run
Filmot:

```env
FILMOT_API_KEY=your_filmot_api_key_here
```

The per-user file is `%APPDATA%\filmot\config.env` on Windows,
`~/Library/Application Support/filmot/config.env` on macOS, and
`${XDG_CONFIG_HOME:-~/.config}/filmot/config.env` on Linux.
Process environment values take precedence over the per-user file, which takes
precedence over the project `.env`. Keep real credentials out of Git.

```bash
# Inspect configuration and paths without printing credentials or making API calls
filmot config

# Fetch one candidate page and show five videos with at most two hits each
filmot search "battery recycling" --pages 1 --limit 5 --max-hits 2
```

Filmot API requests consume credits. `--limit` bounds displayed results;
`--pages` and `--candidate-pool` bound fetched candidates. See
[routing and credits](https://github.com/baba20o/filmot-cli/blob/main/docs/CONFIGURATION.md#api-providers-routing-and-credits).
The official provider is named `direct` in CLI options. Existing RapidAPI
subscriptions remain supported as legacy compatibility.

Set `YOUTUBE_API_KEY` separately if you use YouTube Data API commands. See
[YouTube API behavior](https://github.com/baba20o/filmot-cli/blob/main/docs/YOUTUBE_API.md) for setup, quota, and saved-metadata
refresh/deletion expectations. Optional proxy and AWS fallback setup is covered
in [Configuration](https://github.com/baba20o/filmot-cli/blob/main/docs/CONFIGURATION.md).

## Examples

Shell examples use Bash quoting. For phrase queries containing literal double
quotes, Windows PowerShell 5.1 needs different escaping; see Microsoft's
[native argument-passing guidance](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_parsing#passing-arguments-that-contain-quote-characters).

### Find a specific discussion

```bash
# Locate words near one another in indexed captions
filmot search '"battery" NEAR/15 "recycling"' --pages 1 --limit 10

# Add a title constraint to narrow the candidate set
filmot search '"lithium recovery"' --title recycling --pages 1 --limit 10

# Discover videos by title/metadata rather than caption text
filmot titles '"battery recycling"' --min-views 10000 --pages 1 --raw
```

Proximity and title expressions are explained in the
[query syntax reference](https://github.com/baba20o/filmot-cli/blob/main/docs/CLI_REFERENCE.md#query-syntax-full-text-operators).
The title constraint narrows matches; it does not establish topical relevance.

### Build and inspect a research library

```bash
filmot research "battery recycling" --depth 5 --candidate-pages 1 --candidate-pool 50
filmot library list battery-recycling
filmot library compare "lithium" --topic battery-recycling

# Replace VIDEO_ID with an ID from the saved library
filmot library read VIDEO_ID --topic battery-recycling --from 1:30 --to 3:00
```

`--depth` caps selected transcripts. Research candidate-page and candidate-pool
limits apply **per search stage**; scout, probe, transcript acquisition, and
metadata enrichment are separate work. The
[research reference](https://github.com/baba20o/filmot-cli/blob/main/docs/CLI_REFERENCE.md#research-a-topic-one-command)
explains staging, selection, and partial outcomes.

### Export or pipe discovery results

```bash
mkdir -p exports
filmot titles sqlite --pages 1 --output exports/sqlite.jsonl
filmot subtitle-export '"sqlite wal"' --pages 1 --output exports/matches.csv

filmot titles '"battery recycling"' --raw | filmot download -t battery-recycling -n 10
```

`download -n` selects the first N input candidates before checking for existing
saves; its default is 50. Replaying the same input does not advance past skipped
records. For automation, retain raw discovery artifacts and inspect
`_filmot.status` and continuation fields. See
[pipeline download](https://github.com/baba20o/filmot-cli/blob/main/docs/CLI_REFERENCE.md#pipeline-download) and the
[JSON result contract](https://github.com/baba20o/filmot-cli/blob/main/docs/CLI_REFERENCE.md#raw-json-for-programmatic-access).

## Find the right command

| Task | Commands | Reference |
| --- | --- | --- |
| Search indexed captions | `search`, `search-all`, `export` | [Search and options](https://github.com/baba20o/filmot-cli/blob/main/docs/CLI_REFERENCE.md#search-subtitles) |
| Find videos by title or metadata; export matches | `titles`, `subtitle-export` | [Discovery searches](https://github.com/baba20o/filmot-cli/blob/main/docs/CLI_REFERENCE.md#discovery-searches) |
| Find and inspect channels | `channels`, `find-channels`, `channel`, `channel-videos` | [Channel profiles](https://github.com/baba20o/filmot-cli/blob/main/docs/CLI_REFERENCE.md#channel-profiles-and-analytics) |
| Acquire transcripts or a channel corpus | `transcript`, `download`, `channel-download` | [Transcript acquisition](https://github.com/baba20o/filmot-cli/blob/main/docs/CLI_REFERENCE.md#download-transcripts) |
| Read, search, compare, or export saved passages | `library` | [Local library](https://github.com/baba20o/filmot-cli/blob/main/docs/CLI_REFERENCE.md#transcript-library) |
| Fill missing Filmot metadata | `library enrich` | [Enrichment and budgets](https://github.com/baba20o/filmot-cli/blob/main/docs/CLI_REFERENCE.md#filmot-metadata-in-the-library) |
| Research and keep an evidence trail | `research`, `sessions`, `claims` | [Claims](https://github.com/baba20o/filmot-cli/blob/main/docs/CLI_REFERENCE.md#claims-and-evidence) and [sessions](https://github.com/baba20o/filmot-cli/blob/main/docs/CLI_REFERENCE.md#research-sessions) |
| Discover current public YouTube metadata | `yt-search`, `yt-video`, `yt-playlists`, `yt-playlist` | [YouTube discovery](https://github.com/baba20o/filmot-cli/blob/main/docs/CLI_REFERENCE.md#search-recent-youtube-uploads) |
| Inspect public discussion | `yt-comments`, `yt-replies` | [Comments and replies](https://github.com/baba20o/filmot-cli/blob/main/docs/CLI_REFERENCE.md#inspect-public-youtube-comments-and-replies) |
| Maintain saved YouTube API fields | `yt-data status`, `yt-data refresh`, `yt-data purge` | [Metadata lifecycle](https://github.com/baba20o/filmot-cli/blob/main/docs/CLI_REFERENCE.md#saved-youtube-metadata-lifecycle) |
| Manage batch jobs, cache, watchlist, or REPL | `batch`, `cache`, `watchlist`, `interactive` | [Utilities](https://github.com/baba20o/filmot-cli/blob/main/docs/CLI_REFERENCE.md#advanced-features) |

Use `filmot COMMAND --help` for the complete option set and current defaults.
Research data stays under project-local `.filmot_data/` by default; machine
configuration, cache, and proxy state use per-user locations. See
[storage and configuration](https://github.com/baba20o/filmot-cli/blob/main/docs/CONFIGURATION.md#storage-and-configuration).

## Documentation

| Guide | Contents |
| --- | --- |
| [CLI reference](https://github.com/baba20o/filmot-cli/blob/main/docs/CLI_REFERENCE.md) | Commands, options, examples, limits, result schemas, and failure behavior |
| [Configuration](https://github.com/baba20o/filmot-cli/blob/main/docs/CONFIGURATION.md) | API providers, credentials, storage, proxies, and optional AWS fallback |
| [Agent usage guide](https://github.com/baba20o/filmot-cli/blob/main/AGENTS_README.md) | Research workflows and automation guidance |
| [Research methodology](https://github.com/baba20o/filmot-cli/blob/main/docs/AGENTS_RESEARCH_GUIDE.md) | Source assessment, cross-checking, and evidence handling |
| [Local passages](https://github.com/baba20o/filmot-cli/blob/main/docs/LOCAL_PASSAGES.md) | Reading bounded passages from saved transcript copies |
| [Filmot API coverage](https://github.com/baba20o/filmot-cli/blob/main/docs/FILMOT_API_COVERAGE.md) | Endpoint/parameter coverage, enrichment semantics, and deliberate limits |
| [YouTube API behavior](https://github.com/baba20o/filmot-cli/blob/main/docs/YOUTUBE_API.md) | Shipped YouTube features and metadata lifecycle requirements |
| [Roadmap](https://github.com/baba20o/filmot-cli/blob/main/docs/NEXT-SLICE.md) and [YouTube roadmap](https://github.com/baba20o/filmot-cli/blob/main/docs/YOUTUBE_ROADMAP.md) | Follow-ups and historical implementation scope |
| [Changelog](https://github.com/baba20o/filmot-cli/blob/main/CHANGELOG.md) | Released and unreleased changes |
| [Contributing](https://github.com/baba20o/filmot-cli/blob/main/CONTRIBUTING.md) | Development setup, project map, local-file conventions, and package checks |

## Contributing

See [CONTRIBUTING.md](https://github.com/baba20o/filmot-cli/blob/main/CONTRIBUTING.md) for development and testing instructions.
Reports with a reproducible command, redacted output, and expected behavior
help diagnose issues. Keep credentials and private research captures out of
issues and commits.

## License

MIT — see [LICENSE](https://github.com/baba20o/filmot-cli/blob/main/LICENSE).

## Acknowledgments

- [Filmot](https://filmot.com/) and its [official API](https://filmot.com/api-documentation)
- [Click](https://click.palletsprojects.com/) for the CLI framework
- [Rich](https://rich.readthedocs.io/) for terminal formatting
