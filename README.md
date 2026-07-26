# Filmot CLI

A powerful command-line interface for the [Filmot](https://filmot.com/) YouTube transcript search engine — Search YouTube video transcripts, get video metadata, and discover channels.

## Why Filmot CLI?

Google lets you search *titles* and *descriptions*. Filmot lets you search **inside indexed YouTube transcripts** — a large archive of auto-generated and manual captions covering spoken video content.

This means you can do things no other search tool can:

```bash
# Research a topic in one command: search → filter → download → summarize
filmot research "deep sea mining" --depth 15 --dedupe --min-matches 2 --sort density

# Locate matching passages across saved sources, then verify the claim
filmot library compare "dark oxygen" --sort density

# Find the exact moments where "deep sea mining" is discussed near "new species"
# in Filmot's index; widen the fetched candidate set explicitly when needed
filmot search '"deep sea mining" NEAR/20 "new species"'

# Search transcript content, but only in videos ABOUT a topic (title filter)
# Reduces 266,323 results to 172 laser-focused hits
filmot search "deep sea mining" --title "deep sea mining"

# Build a research library: search → download full transcripts → cross-reference
filmot search "polymetallic nodules" --title "deep sea" --bulk-download "deep-sea-research"
filmot library compare "cobalt" --topic deep-sea-research --sort density
```

**The proximity search (`NEAR/N`) is the killer feature.** Searching `"artificial intelligence" NEAR/20 "job displacement"` across Filmot's transcript index finds moments where two concepts occur close together, rather than videos that merely contain both words somewhere.

**The `--title` filter is your precision lever.** Without it, searching for "deep sea mining" returns 266,000+ results (any video that mentions the words). With `--title "deep sea mining"`, you get 172 results — every one a video *dedicated to* the topic. Combine `--title` with a different content query to ask questions like: "Which deep sea mining videos discuss dark oxygen?"

## Features

### Core Features
- **Subtitle Search** — Find videos by transcript/subtitle content with 24 filter options
- **Video Metadata** — Get comprehensive details for any YouTube video
- **Channel Discovery** — Search and explore YouTube channels by name or handle
- **Transcript Download** — Fetch full YouTube transcripts for deep content analysis
- **Rich Terminal UI** — Beautiful formatted output with tables, colors, and clickable links
- **Expanded Hit Mode** — Show all non-duplicate hit details returned for the displayed videos without widening the fetched page scope
- **Structured JSON Mode** — Emit the processed search response, including client-side filters and scope metadata, as one JSON value

### Research & Analysis
- **Compound Research** — `filmot research` orchestrates search → filter → download → summary in one command
- **Cross-Source Concordance** — `filmot library compare` locates matching passages across saved sources
- **Density Scoring** — Matches-per-minute metric reveals the most focused content
- **Deduplication** — Skip duplicate/near-duplicate transcripts during bulk download
- **Word-Boundary Search** — Library search with smart fallback for plurals/inflections
- **Structured Export** — Markdown output with full metadata headers for LLM context
- **Pipeline Mode** — Pipe search results into download for custom workflows

### Advanced Features
- **Smart Caching** — File-based response caching with configurable TTL to reduce API calls
- **Rate Limiting** — Built-in token bucket rate limiter with adaptive backoff
- **Pagination** — Automatic multi-page fetching with `search-all` command
- **Export** — Export results to JSON or CSV for analysis
- **Watchlist** — Save videos locally with notes and tags for later viewing
- **Batch Processing** — Process multiple queries from files (txt/json/csv)
- **Interactive Mode** — REPL-style interface for exploratory searching

## Requirements

- Python 3.9+
- RapidAPI account with Filmot API subscription

## Installation

### Option 1: Install as CLI tool (Recommended)

```bash
# Clone the repository
git clone https://github.com/baba20o/filmot-cli.git
cd filmot-cli

# Install as editable package
pip install -e .

# Now you can use 'filmot' command from anywhere
filmot --help
```

### Option 2: Run directly with Python

```bash
# Clone the repository
git clone https://github.com/baba20o/filmot-cli.git
cd filmot-cli

# Install dependencies
pip install -r requirements.txt

# Run with python
python main.py --help
```

### Configure API credentials

Create a `.env` file in the directory where you run Filmot:
```env
RAPIDAPI_KEY=your_rapidapi_key_here
RAPIDAPI_HOST=filmot-tube-metadata-archive.p.rapidapi.com
```

Get your API key from [Filmot API on RapidAPI](https://filmot.com/api).
For credentials shared across projects, use Filmot's per-user `config.env`
instead. See [Storage and configuration](#storage-and-configuration).

### Storage and configuration

Filmot deliberately separates research artifacts from machine-level runtime
state:

| Kind | Default location | Override |
| --- | --- | --- |
| Transcripts, research libraries, watchlists, and session ledgers | `<current directory>/.filmot_data` | `FILMOT_DATA_DIR` |
| Per-user configuration and proxy credentials | Windows `%APPDATA%\filmot`; macOS `~/Library/Application Support/filmot`; Linux `${XDG_CONFIG_HOME:-~/.config}/filmot` | `FILMOT_CONFIG_DIR` |
| Per-user mutable state, including proxy health and rate limiting | Windows `%LOCALAPPDATA%\filmot`; macOS `~/Library/Application Support/filmot`; Linux `${XDG_STATE_HOME:-~/.local/state}/filmot` | `FILMOT_STATE_DIR` |
| Response cache | Native per-user cache directory (`%LOCALAPPDATA%`, `~/Library/Caches`, or `${XDG_CACHE_HOME:-~/.cache}`) under `filmot` | `FILMOT_CACHE_DIR` |

Configuration precedence is: existing process environment, per-user
`config.env`, then an explicit `.env` in the current working directory. The
per-user file can be redirected with `FILMOT_CONFIG_FILE`. Filmot does not
search parent directories for `.env`, so editable and packaged installs behave
the same way.

Proxy credential inventories are stored in the per-user configuration
directory with owner-only permissions where the platform supports them.
Credential-free proxy health, cooldown, rotation, and short-lived leases live
in `proxy/health.sqlite3` under the per-user state directory, allowing
concurrent Filmot processes to coordinate safely without writing secrets to
the database.

## Usage

> **Note:** If installed with `pip install -e .`, replace `python main.py` with `filmot` in all examples below.

### Research a Topic (One Command)

The fastest way to build a knowledge base on any topic:

```bash
# Scout, run staged searches, preview candidates, download, and checkpoint
filmot research "nuclear fusion energy" --depth 12 --dedupe --min-matches 2

# Options:
#   --depth N                  Number of transcripts to download (default: 10)
#   --candidate-pages N        Filmot pages fetched before client-side ranking
#   --candidate-pool N         Maximum Filmot candidates to score
#   --sort MODE                balanced (default), density, source-prior, or viewcount
#   --channel/--channel-id     Constrain sources; fuzzy names resolve and fail closed
#   --accept-broad             Explicitly allow a high-cardinality loose fallback
#   --verbose                  Show full transcript failure details
#   --fallback                 Use AWS Transcribe when captions are unavailable
```

`research` first tries title+transcript, exact-phrase, and `NEAR/N` stages. Exact/`NEAR/N` fallback candidates must cover at least 75% of the topic tokens in one visible passage. A loose transcript-wide fallback above `--broad-threshold` is blocked unless you explicitly pass `--accept-broad`, and accepted broad candidates must still pass a passage-level relevance gate. The default balanced rank shows relevance, density, echo risk, and a separate `source-prior`. That prior is only an audience/engagement heuristic; it is not a credibility or truth score.

### Search Subtitles

Search for videos containing specific text in their transcripts:

```bash
# Basic search
filmot search "hello world"

# Search in specific language
filmot search "machine learning" --lang en

# Filter by views and sort by popularity
filmot search "recipe" --min-views 10000 --sort viewcount --order desc

# Sort by relevance density (matches per minute)
filmot search "fusion energy" --sort density --min-matches 3

# Filter by minimum subtitle matches (cuts noise)
filmot search "quantum computing" --min-matches 2 --full

# Search within specific channels
filmot search "tutorial" --channel "programming" --channel-count 5

# Fetch three candidate pages, display 20 videos, at most 5 hits each
filmot search "AI" --pages 3 --sort density --limit 20 --max-hits 5

# Bulk download with deduplication
filmot search "deep sea mining" --bulk-download deep-sea:10 --dedupe

# Export the processed search response as JSON
filmot search "AI" --raw > results.json
```

Fuzzy `--channel` names are resolved to displayed channel IDs before searching. If no channel resolves—or Filmot returns a result outside the resolved ID set—the command fails closed instead of silently widening the search.

### Query Syntax (Full-Text Operators)

Filmot uses [Manticore Search](https://manticoresearch.com/) under the hood. The following operators are supported in your search queries:

#### Basic Operators

| Operator | Syntax | Description | Example |
|----------|--------|-------------|---------|
| **AND** | `word1 word2` | Both words must appear somewhere in the same transcript; adjacency or a relationship is not implied | `python tutorial` |
| **OR** | `word1 \| word2` | Either word can match | `"9 11" \| "nine eleven"` |
| **Phrase** | `"exact phrase"` | Words must appear adjacent, in order | `"machine learning"` |
| **Grouping** | `(expr1 \| expr2)` | Group expressions for complex queries | `python ("tutorial" \| "course")` |

#### Advanced Operators

| Operator | Syntax | Description | Example |
|----------|--------|-------------|---------|
| **Proximity** | `"words here"~N` | Words within N words of each other | `"cat dog"~5` |
| **NEAR** | `word1 NEAR/N word2` | Words within N words, any order (max 500) | `hello NEAR/3 world` |
| **NOTNEAR** | `word1 NOTNEAR/N word2` | word1 NOT within N words of word2 (max 500) | `python NOTNEAR/10 beginner` |
| **NOT** | `-word` | Exclude videos containing word (global) | `python -beginner` |
| **Wildcard** | `"word * word"` | Match exactly one word in between | `"sentiment * shared"` |

> **Not Supported:** Prefix wildcards (`thermo*`), Quorum (`"words"/N`), and Strict Order (`<<`) are not currently supported by the Filmot API.

> **NOTNEAR vs NOT:** The NOT operator (`-word`) excludes the **entire video** if the excluded word appears **anywhere** in the transcript. NOTNEAR is usually more practical — it only excludes matches where the terms appear close together. For example, `python NOTNEAR/10 beginner` finds "python" mentions that aren't in a beginner context (max distance: 500 words).

> **Recall tip:** Exact phrases and `NEAR/N` stay literal. A low count is not evidence that a subject is rare: try singular/plural, inflection, spelling, and likely auto-caption variants before drawing that conclusion.

#### OR with Phrases (Handling Transcription Variations)

YouTube auto-captions transcribe words inconsistently. Use OR (`|`) with phrases to catch all variations:

```bash
# Find all mentions of "9/11" regardless of how it was transcribed
filmot search '"9 11" | "nine eleven" | "september 11" | "9/11"'

# Find references to "AI" with variations
filmot search '"artificial intelligence" | "A.I." | "AI"'

# Brand name variations
filmot search '"iPhone" | "i phone" | "i-phone"'
```

> **Proximity syntax caveat:** Do not put `|` inside a quoted phrase when using `NEAR/N` or `NOTNEAR/N`. This backend form is invalid: `"memory|context" NEAR/20 "production"`. Use grouped OR instead: `("memory" | "context") NEAR/20 "production"`. Filmot now rewrites the invalid form automatically, but the grouped form is the correct syntax to write.

#### Combining Operators with Grouping

Use parentheses `()` to build complex queries combining multiple operators:

```bash
# Python AND (tutorial OR course)
filmot search 'python ("tutorial" | "course")'

# Find "machine learning" near "neural network" within 10 words
filmot search '"machine learning" NEAR/10 "neural network"'

# Proximity - words within 5 words of each other
filmot search '"deep learning tensorflow"~5'

# Find Python content that's NOT in a beginner context
filmot search 'python NOTNEAR/10 beginner'

# Complex: Find Filmot mentions near YouTube-related terms (with spelling variations)
filmot search '("filmot" | "philmot" | "filmont") NEAR/50 ("youtube" | "transcript" | "subtitle")'

# Correct way to use OR inside proximity queries
filmot search '("memory" | "context") NEAR/20 "production"'
filmot search '"agent memory" NEAR/20 ("vector" | "graph" | "database")'

# Find advanced Python discussions (exclude videos with "beginner" anywhere)
filmot search 'python "advanced" -beginner'
```

> **Note:** When using `|` (OR) in PowerShell, wrap your query in single quotes to prevent shell interpretation.

#### `--title` Supports Operators Too

The `--title` filter also supports Manticore operators, not just plain text:

```bash
# Implicit AND: title must contain "deep" AND "sea" AND "mining" (in any order)
filmot search "cobalt" --title "deep sea mining"

# Exact phrase: title must contain "deep sea mining" as a phrase
filmot search "cobalt" --title '"deep sea mining"'

# OR: title has "deep sea" AND (mining OR extraction)
filmot search "cobalt" --title 'deep sea (mining | extraction)'
```

> **Tip:** Avoid `AND` as a keyword in `--title` — it's treated as a literal word, not an operator. Use implicit AND (just space-separate words) instead.

### Search Options Reference

| Option | Description | Example |
|--------|-------------|---------|
| `--lang, -l` | Language code | `--lang en` |
| `--page, -p` | Page number (50 results/page) | `--page 2` |
| `--pages` | Fetch N pages before client-side filtering/ranking | `--pages 3` |
| `--candidate-pool` | Cap candidates fetched across `--pages` | `--candidate-pool 120` |
| `--category, -c` | Video category | `--category "Education"` |
| `--exclude` | Exclude categories (comma-separated) | `--exclude "Music,Gaming"` |
| `--channel-id` | Limit to specific channel ID(s), comma-delimited | `--channel-id UCxyz...,UCabc...` |
| `--channel` | Resolve channel text to displayed IDs; fail closed on no match | `--channel "tech"` |
| `--channel-count` | Max channels for `--channel` (default 10) | `--channel-count 5` |
| `--title` | Filter by video title — supports operators | `--title "deep sea mining"` |
| `--min-views` | Minimum view count | `--min-views 10000` |
| `--max-views` | Maximum view count | `--max-views 1000000` |
| `--min-likes` | Minimum like count | `--min-likes 500` |
| `--max-likes` | Maximum like count | `--max-likes 50000` |
| `--min-duration` | Minimum duration (seconds) | `--min-duration 600` |
| `--max-duration` | Maximum duration (seconds) | `--max-duration 3600` |
| `--start-date` | Videos after date (yyyy-mm-dd) | `--start-date 2024-01-01` |
| `--end-date` | Videos before date (yyyy-mm-dd) | `--end-date 2024-12-31` |
| `--country` | Country code (see table below) | `--country 217` |
| `--license` | 1=Standard, 2=Creative Commons | `--license 2` |
| `--sort` | Sort by: `viewcount`, `likecount`, `uploaddate`, `duration`, `chanrank`, `id`, `density` | `--sort viewcount` |
| `--order` | Order: `asc` or `desc` | `--order desc` |
| `--manual-subs` | Search manual subtitles only (default: auto subs) | `--manual-subs` |
| `--max-query-time` | Max query time (4-15000 ms) | `--max-query-time 5000` |
| `--hit-format` | 0=context snippets, 1=full lines | `--hit-format 1` |
| `--full` | Show all non-duplicate hit snippets returned for displayed videos on fetched candidate pages; duplicate segments may be collapsed and no extra pages are fetched | `--full` |
| `--limit`, `--top` | Maximum video results to display/output | `--limit 20` |
| `--max-hits` | Maximum hit details displayed per video | `--max-hits 5` |
| `--raw` | Emit exactly one JSON value on stdout, including JSON errors | `--raw` |
| `--min-matches` | Only show videos with N+ subtitle matches | `--min-matches 3` |
| `--bulk-download` | Download top N transcripts to TOPIC | `--bulk-download topic:10` |
| `--fallback` | Use AWS Transcribe fallback for bulk download | `--fallback` |
| `--dedupe` | Skip duplicate transcripts during bulk download | `--dedupe` |

### Multilingual Search

Filmot supports searching transcripts in any language YouTube auto-generates captions for, including Devanagari, Cyrillic, CJK, Arabic, Hebrew, and more.

| Language | Script | `--lang` code | Notes |
|----------|--------|---------------|-------|
| English | Latin | `en` | Full support |
| Spanish | Latin | `es` | 1M+ results |
| German | Latin | `de` | Handles umlauts |
| Hindi | Devanagari | `hi` | 10.5K+ results |
| Russian | Cyrillic | `ru` | 339K+ results |
| Japanese | CJK | `ja` | 6M+ results |
| Korean | Hangul | `ko` | 277K+ results |
| Arabic | Arabic | `ar` | RTL supported |
| Hebrew | Hebrew | `iw` (not `he`) | YouTube uses legacy code |
| Chinese | CJK | omit `--lang` | `--lang zh` returns 0 results |

```bash
# Latin-script languages — use standard codes
filmot search "inteligencia artificial" --lang es --full

# Japanese, Korean, Arabic — standard codes work
filmot search "人工知能" --lang ja --full
filmot search "인공지능" --lang ko --full

# Hebrew — use "iw" (YouTube's legacy code), NOT "he"
filmot search "בינה מלאכותית" --lang iw --full

# Chinese — omit --lang entirely (zh/zh-Hans don't work)
filmot search "人工智能" --full

# Hindi, Russian — standard ISO codes work
filmot search "कृत्रिम बुद्धिमत्ता" --lang hi --full
filmot search "нейросеть" --lang ru --full
```

> **Tip:** If a language code isn't returning results, try omitting `--lang` entirely. The API will match your query in transcripts regardless of language tag.

### Get Video Metadata

Retrieve metadata for one or more YouTube videos:

```bash
# Single video
python main.py video dQw4w9WgXcQ

# Multiple videos (comma-separated)
python main.py video "dQw4w9WgXcQ,abc123def,xyz789ghi"

# Raw JSON output
python main.py video dQw4w9WgXcQ --raw
```

### Download Transcripts

Fetch full YouTube transcripts for deep content analysis. This goes beyond search snippets — get the **complete** video content:

```bash
# Get transcript summary (shows excerpt)
filmot transcript VIDEO_ID

# Get FULL transcript (for AI processing)
filmot transcript VIDEO_ID --full

# Chunk transcript into 10-minute segments (easier to navigate)
filmot transcript VIDEO_ID --chunk 10

# Include timestamps for each segment
filmot transcript VIDEO_ID --timestamps

# Save to file
filmot transcript VIDEO_ID -o transcript.txt

# Export as JSON
filmot transcript VIDEO_ID --raw > data.json

# Works with YouTube URLs too
filmot transcript "https://youtube.com/watch?v=VIDEO_ID" --full

# Save directly to library under a topic
filmot transcript VIDEO_ID --full --save-to my-topic

# Use AWS Transcribe fallback when YouTube captions unavailable
filmot transcript VIDEO_ID --fallback --full
```

### Search Within Transcripts

Find specific terms within a video's transcript with context:

```bash
# Find all mentions of "fusion" in a video
filmot transcript-search VIDEO_ID "fusion"

# Get more context around matches
filmot transcript-search VIDEO_ID "reactor" --context 3
```

This is perfect for navigating long videos — jump directly to the relevant timestamps!

### Pipeline Download

Download transcripts from piped search results for custom workflows:

```bash
# Search with raw output, pipe to download
filmot search "deep sea mining" --title "deep sea mining" --raw | filmot download -t deep-sea --dedupe

# Export a multi-page search, then feed the JSON file to download
filmot search-all "AI safety" --pages 5 --output results.json --format json
# Bash:
filmot download -t ai-safety --dedupe -n 20 < results.json
# PowerShell:
Get-Content -Raw results.json | filmot download -t ai-safety --dedupe -n 20
```

### Transcript Library

Manage a local library of transcripts organized by topic for building curated knowledge bases:

```bash
# List all topics
filmot library list

# List transcripts in a topic
filmot library list deep-sea-mining

# Search across all saved transcripts (word-boundary matching by default)
filmot library search "dark oxygen"

# Force substring matching (catches plurals automatically via fallback)
filmot library search "patent" --substring

# Find passages where different sources use the same term or phrase
filmot library compare "cobalt" --sort density
filmot library compare "moratorium" --context 200 --topic deep-sea-mining

# Get combined text for LLM context
filmot library context deep-sea-mining

# Structured markdown with metadata headers (auto-saves to file)
filmot library context deep-sea-mining --format structured

# Get combined text limited to 50K chars
filmot library context deep-sea-mining --max-chars 50000

# Show library statistics
filmot library stats

# Explicitly assign an ambiguous pre-Unicode topic directory
filmot library migrate-topic "AI 人工知能"

# Delete a transcript or entire topic
filmot library delete VIDEO_ID
filmot library delete topic-name --all
```

`migrate-topic` moves an entire legacy directory; it cannot infer which files
belong to which original topic or partition a directory that old versions
merged. Review the displayed source and destination paths, and confirm only
when every source file belongs to the requested topic. Existing destination
conflicts remain in the legacy directory rather than being overwritten.

### Search Channels

Find YouTube channels by name or handle:

```bash
# Search by name
python main.py channels mrbeast

# Search by phrase
python main.py channels "Linus Tech Tips"

# Raw JSON output
python main.py channels mrbeast --raw
```

### Channel Corpus: Download & Mine Entire Channels

Download all transcripts from a YouTube channel to build a local knowledge corpus that can be searched offline with full-text and proximity operators.

#### Download a Channel

```bash
# Download all transcripts from a channel (by name, handle, or URL)
filmot channel-download "Chat With Traders"

# Parallel download with 4 workers (3x faster)
filmot channel-download "Excess Returns" --workers 4

# Limit to most recent 50 videos
filmot channel-download "All-In Podcast" --limit 50

# Resume interrupted download (automatic — just re-run the same command)
filmot channel-download "Chat With Traders" --workers 4

# Bypass proxy and connect directly
filmot channel-download "Excess Returns" --workers 4 --no-proxy

# Force re-download (ignore cached manifest)
filmot channel-download "Chat With Traders" --fresh
```

#### Check Download Status

```bash
# List all downloaded channels
filmot channel-status

# Detailed stats for a specific channel
filmot channel-status chat-with-traders
```

#### Search Within a Channel Corpus

Search locally across all downloaded transcripts. Supports plain text, NEAR/N proximity, and tilde proximity — the same operators available in `filmot search` (API), but running entirely offline against your local corpus.

```bash
# Plain substring search
filmot channel-search chat-with-traders "Sharpe ratio"

# NEAR/N — find two phrases within N words of each other
filmot channel-search chat-with-traders '"risk management" NEAR/10 "position sizing"'

# NEAR/N with grouped OR on either side
filmot channel-search chat-with-traders '("risk" | "drawdown") NEAR/10 ("position" | "sizing")'

# ~N (tilde) — find words within N words of each other
filmot channel-search chat-with-traders '"blew up account"~5'

# Limit results
filmot channel-search excess-returns "diversification" --limit 10
```

| Operator | Syntax | Example |
|----------|--------|---------|
| **Plain** | `"text"` | `"Sharpe ratio"` |
| **NEAR/N** | `"phrase1" NEAR/N "phrase2"` or `("a" \| "b") NEAR/N ("c" \| "d")` | `'"risk management" NEAR/10 "position sizing"'` |
| **Tilde** | `"word1 word2"~N` | `'"blew up account"~5'` |

> **Important:** For proximity queries, use grouped OR like `("risk" | "drawdown") NEAR/10 "position"`. Do not use quoted pipes like `"risk|drawdown" NEAR/10 "position"`.

> **Whole-word matching:** Proximity operators match whole words — `"account"` will not match "accounts" or "accountability". For plurals and inflections, add explicit alternatives: `("account" | "accounts")`. Terms must be double-quoted; unquoted operands (e.g. `risk NEAR/10 position`) are rejected with an error.

> **Note:** `channel-search` runs entirely offline against your downloaded corpus. No API calls, no rate limits, no quota. Download once, search forever.

### View Configuration

Check your current API configuration:

```bash
python main.py config
```

## Country Codes

| Code | Country | Code | Country | Code | Country |
|------|---------|------|---------|------|---------|
| 217 | United States | 153 | United Kingdom | 95 | Germany |
| 88 | France | 188 | Spain | 110 | Italy |
| 234 | Nigeria | 109 | India | 27 | Brazil |
| 35 | Canada | 13 | Australia | 116 | Japan |
| 189 | South Korea | 166 | Russia | 155 | Mexico |

> See the [Filmot API documentation](https://filmot.com/api) for a complete list of 250+ country codes.

## Project Structure

```
filmot-cli/
├── .env                    # Optional project-specific settings (git-ignored)
├── .gitignore              # Git ignore rules
├── requirements.txt        # Python dependencies
├── main.py                 # CLI entry point
├── README.md               # This file
├── AGENTS_README.md        # Agent-specific usage guide
└── filmot/
    ├── __init__.py         # Package exports
    ├── _version.py         # Single source for package/CLI version
    ├── __main__.py         # Python -m filmot support
    ├── config.py           # Configuration & environment loading
    ├── paths.py            # Project and per-user storage resolution
    ├── api.py              # Filmot API wrapper with caching/rate limiting
    ├── api_contract.py     # Recorded-response structural validation
    ├── cli.py              # Root group and command registration
    ├── cli_support.py      # Shared raw/human/error presentation boundary
    ├── schemas.py          # Versioned result and ledger-event contracts
    ├── ledger.py           # Project-local append-only research events
    ├── commands/
    │   ├── search.py       # Search, metadata, export, and scout commands
    │   ├── research.py     # Staged research workflow
    │   ├── transcript.py   # Transcript and channel-corpus commands
    │   ├── library.py      # Library and session commands
    │   └── proxy.py        # Machine-global proxy operations
    ├── _process.py         # Killable JSON subprocess boundary
    ├── _transcript_worker.py # Isolated transcript route worker
    ├── transcript.py       # Transcript routing and failure classification
    ├── proxy_pool.py       # Cross-process proxy health and leases
    ├── cache.py            # File-based response caching with auto-purge
    ├── rate_limiter.py     # Token bucket rate limiter
    ├── channel_dl.py       # Channel corpus downloader: parallel download, resume, proximity search
    ├── library.py          # Transcript library: storage, search, compare
    ├── export.py           # JSON/CSV export functionality
    ├── watchlist.py        # Local video watchlist management
    ├── batch.py            # Batch query processing
    ├── interactive.py      # Interactive REPL mode
    └── aws_transcribe.py   # AWS Transcribe fallback (optional)
```

## Dependencies

| Package | Purpose |
|---------|---------|
| `requests` | HTTP API calls |
| `python-dotenv` | Environment variable management |
| `click` | CLI framework |
| `rich` | Terminal formatting, tables, colors |
| `youtube-transcript-api` | YouTube transcript download |
| `google-api-python-client` | YouTube Data API v3 (channel downloads) |

**Optional dependencies:**
| Package | Purpose |
|---------|---------|
| `boto3` | AWS Transcribe fallback |
| `yt-dlp` | Audio download for AWS fallback |

---

## Advanced Features

### Export Results

Export search results to JSON or CSV files for analysis:

```bash
# Export to JSON
python main.py export "machine learning" -o results.json

# Export to CSV
python main.py export "python tutorial" -o results.csv --format csv

# Export multiple pages
python main.py export "AI" -o data.json --pages 5

# Export with detailed hits (one row per subtitle match)
python main.py export "pandas" -o hits.csv --format csv --detailed
```

### Paginated Search

Automatically fetch multiple pages of results:

```bash
# Fetch up to 10 pages of results
python main.py search-all "machine learning" --pages 10

# Limit total results and export
python main.py search-all "tutorial" --max-results 200 -o results.json

# With filters
python main.py search-all "python" --pages 5 --lang en --min-views 10000
```

### Cache Management

View and manage the response cache:

```bash
# View cache statistics
python main.py cache

# Clear all cached responses
python main.py cache --clear

# Clear only expired entries
python main.py cache --clear-expired
```

### Watchlist

Save videos for later viewing:

```bash
# List watchlist
python main.py watchlist list

# Add a video by ID
python main.py watchlist add dQw4w9WgXcQ --notes "Great tutorial"

# Show only unwatched videos
python main.py watchlist list --unwatched

# Mark as watched
python main.py watchlist watched dQw4w9WgXcQ

# Remove from watchlist
python main.py watchlist remove dQw4w9WgXcQ

# Clear entire watchlist
python main.py watchlist clear
```

### Batch Processing

Process multiple queries from a file:

```bash
# Create a template file
python main.py batch-template --format json -o queries.json

# Process queries from file
python main.py batch queries.txt -o results.json

# Export batch results as CSV
python main.py batch queries.json -o results.csv --format csv
```

**Supported file formats:**
- `.txt` — One query per line
- `.json` — Array of queries with optional parameters
- `.csv` — Query column with optional lang, min_views columns

**Example `queries.json`:**
```json
[
  {"query": "python tutorial", "lang": "en"},
  {"query": "javascript basics", "min_views": 10000},
  {"query": "machine learning", "category": "Education"}
]
```

### Interactive Mode

Start an interactive REPL for exploratory searching:

```bash
python main.py interactive
```

**Available REPL commands:**
```
filmot> help              # Show all commands
filmot> search pandas     # Quick search
filmot> show 1            # Show details of result #1
filmot> save 1            # Save result to watchlist
filmot> watchlist         # View watchlist
filmot> export results.json  # Export last search results
filmot> defaults lang en  # Set default search options
filmot> cache stats       # View cache statistics
filmot> history           # View search history
filmot> quit              # Exit REPL
```

---

## Example Output

### Subtitle Search
```
╭─────────────────────────────────────────────────────────╮
│ Found 356,432 results for: python                       │
╰─────────────────────────────────────────────────────────╯

1. Python Full Course for free
   Channel: Bro Code | Views: 20,213,163 | Likes: 958,283
   Duration: 12h 0m | Category: Science & Technology | Uploaded: 2021-02-15
   URL: https://youtube.com/watch?v=XKHEtdqhLK8
   Matches (11): Density: 0.02/min
      [8:56] ...re it's not necessary but it's common practice for python and...
      [2:04:31] ...so that is the return statement functions can send python values...
      [2:05:20] ...been working with positional arguments already and python knows...
      ... and 8 more matches
```

### Channel Search
```
╭─────────────────────────────────────────────╮
│ Channels matching: mrbeast                  │
╰─────────────────────────────────────────────╯
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ # ┃ Channel Name         ┃ Handle          ┃ Subscribers  ┃ Views   ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ 1 │ MrBeast              │ @MrBeast        │ 463M         │ 52.3B   │
│ 2 │ MrBeast Gaming       │ @MrBeastGaming  │ 53.7M        │ 7.8B    │
│ 3 │ MrBeast 2            │ @MrBeast2       │ 55.3M        │ 7.3B    │
└───┴──────────────────────┴─────────────────┴──────────────┴─────────┘
```

## API Authentication

This CLI requires a RapidAPI key for the Filmot Tube Metadata Archive API:

1. Create a free account at [RapidAPI](https://rapidapi.com/)
2. Subscribe to the [Filmot API](https://filmot.com/api)
3. Copy your API key from the dashboard
4. Add it to your per-user `config.env` (recommended) or project `.env`

## Webshare Proxy Pool (transcript fetching)

YouTube aggressively rate-limits and blocks data-center IPs (AWS, GCP, etc.).
When YouTube's IP block hits this CLI's transcript fetcher, the request returns
nothing usable. To avoid this, the CLI ships with a **dynamic Webshare proxy
pool**: it pulls a list of residential proxy sessions from your Webshare
account, rotates through them, tracks per-session health, and cools down or
retires sessions that get rate-limited / blocked / fail to connect.

### Setup

1. Sign up at [webshare.io](https://www.webshare.io/) and get a residential plan.
2. Copy your API token from the [user API keys page](https://dashboard.webshare.io/userapi/keys).
3. Add it to the per-user `config.env` (recommended) or project `.env`:
   ```bash
   WEBSHARE_API_TOKEN=your_40_char_token_here
   ```
4. Verify:
   ```bash
   filmot proxy refresh   # populate the pool
   filmot proxy status    # see available and recently healthy sessions
   filmot proxy test      # stream bounded probes through 3 sessions
   ```

### Configuration

| Env var                      | Default       | Purpose                                                            |
| ---------------------------- | ------------- | ------------------------------------------------------------------ |
| `WEBSHARE_API_TOKEN`         | _(unset)_     | Enables API-backed pool discovery and remote refresh.               |
| `WEBSHARE_SESSION_FILE`      | Per-user config `webshare_info.txt` | Optional file-backed `host:port:user:password` pool. |
| `FILMOT_PROXY_MODE`          | `proxy-only` if token set, else `auto` | `auto` \| `proxy-only` \| `primary-only` \| `direct-only` |
| `FILMOT_PROXY_COUNTRIES`     | _(all)_       | Comma-separated ISO-2 codes (e.g. `US,GB,CA`).                     |
| `FILMOT_PROXY_REFRESH_HOURS` | `6`           | How often the pool re-pulls the session list from Webshare.        |
| `FILMOT_PROXY_MAX_SESSIONS`  | `50`          | Cap on sessions kept in the pool.                                  |
| `FILMOT_PROXY_RETRY_LIMIT`   | `4`           | Max pool sessions to try per transcript before giving up.          |
| `FILMOT_PROXY_HEALTH_HOURS`  | `24`          | Recent-success window used by the “healthy” metric.                 |
| `FILMOT_PROXY_LEASE_SECONDS` | `120`         | Cross-process lease lifetime for a selected proxy session.          |
| `FILMOT_TRANSCRIPT_CONNECT_TIMEOUT` | `8`    | Per-request connection deadline in seconds.                        |
| `FILMOT_TRANSCRIPT_READ_TIMEOUT` | `15`       | Per-request read deadline in seconds.                              |
| `FILMOT_TRANSCRIPT_ROUTE_TIMEOUT` | `30`      | Overall deadline for one transcript route in seconds.              |

On AWS hosts, `proxy-only` mode is the right default — direct requests will
typically be blocked. `proxy-only` tries only eligible pool sessions.
`direct-only` bypasses all proxy routes. `auto` tries the file/API pool first,
then the initialized primary route (an explicit proxy, legacy Webshare proxy,
environment proxy, or direct connection). `primary-only` uses only that
initialized primary route; the CLI selects it for an explicit `--proxy`.

### CLI commands

- `filmot proxy status` — separates sessions available now from sessions with a
  recent successful live fetch, plus untested, cooling, failing, retired, and
  invalid states. Use `--full` for every redacted session.
- `filmot proxy refresh` — re-pulls an API-backed list. For a file-backed pool,
  it re-reads the local session file while preserving retained health history.
  `--full` requests remote IP rotation only for API-backed pools.
- `filmot proxy test [-n N] [--video-id VID]` — probes up to `N` distinct
  loaded sessions by fetching a known short transcript through each. Run
  `proxy refresh` first if no sessions are loaded. It prints the redacted route
  before the call, streams each result, and enforces per-route and total
  command budgets.

### Storage and legacy migration

API-discovered credentials are cached in an owner-only inventory in Filmot's
per-user configuration directory. Proxy health and active leases are stored
transactionally in the credential-free per-user SQLite health database. This
keeps rotation coordinated across projects and prevents one process from
clobbering another process's counters or selecting the same leased session.

On first use, Filmot non-destructively migrates the former project-local proxy
files:

- `.filmot_data/webshare_info.txt` is copied to the per-user configuration
  directory.
- `.filmot_data/webshare_pool.json` and
  `.filmot_data/webshare_pool_file.json` contribute health counters to SQLite;
  API credentials are split into the private inventory.
- The legacy files are retained. An existing destination is never overwritten.

An explicitly configured custom `WEBSHARE_SESSION_FILE` remains authoritative.

### Legacy fallback

The older `WEBSHARE_PROXY_USERNAME` / `WEBSHARE_PROXY_PASSWORD` static rotating
endpoint still works for users without an API token, but it doesn't get health
tracking or rotation — prefer the API-token path.

## AI Agent Integration

This CLI is designed to work seamlessly with AI agents and LLMs. See [AGENTS_README.md](AGENTS_README.md) for the full agent-focused guide.

### Recommended Agent Workflow

```bash
# 1. Research a topic with staged fallbacks and balanced candidate ranking
filmot research "your topic" --depth 12 --dedupe --min-matches 2

# 2. Navigate matching passages across sources, then verify the claims
filmot library compare "specific claim" --sort density

# 3. Export structured context for deep analysis
filmot library context your-topic --format structured
```

### Key Agent Features

- **`filmot research`** — Single compound command that orchestrates search → filter → download → summary
- **`filmot library compare`** — Lexical cross-source concordance for locating matching passages
- **`--sort density`** — Sort fetched candidates by matches-per-minute to find focused text coverage; this is not a credibility score
- **`--min-matches N`** — Filter out videos with only passing mentions
- **`--dedupe`** — Skip duplicate transcripts during bulk download
- **`--format structured`** — Markdown export with metadata headers, auto-saved to file
- **`--full`** — Expand non-duplicate hit snippets for displayed videos on fetched candidate pages; it does not widen the page scope
- **`--pages` / `--candidate-pool`** — Widen the candidates considered by client-side ranking
- **`--limit` / `--max-hits`** — Bound video and per-video hit output separately
- **`--raw`** — Exactly one JSON value on stdout for programmatic access
- **Word-boundary search** — Library search prevents false positives, auto-falls back to substring for plurals

### Expanded Hit Output

Use `--full` to remove the normal per-video hit cap for displayed videos on
the candidate pages already fetched. Repeated duplicate segments may still be
collapsed, and the option does not fetch additional result pages:

```bash
# Get complete transcript matches for AI processing
filmot search "machine learning tutorial" --full --hit-format 1

# Combine with filters for targeted results
filmot search "python basics" --full --min-views 100000 --lang en
```

### Raw JSON for Programmatic Access

```bash
# Pipe to file for processing
filmot search "data science" --raw > results.json

# Use with jq for filtering
filmot search "tutorial" --raw | jq '.result[0].hits'
```

Raw mode suppresses interactive progress and keeps any remaining diagnostics
off stdout. Stdout contains one JSON value on success and one JSON error value
with a nonzero exit status on operational failure. Every raw command result
uses the `filmot.result/v1` contract. Mapping payloads retain their useful
top-level domain keys and add `_filmot` metadata containing `schema`,
`command`, `status`, `errors`, and `warnings`. For `search`, the payload is the
processed response after channel validation, client-side filtering/ranking,
`--limit`, and `--max-hits`, with explicit scope metadata; it is not an
untouched copy of the upstream API payload. `video` uses `videos`, `channels`
uses `channels`, and `filmot sessions NAME --raw` uses `events`. Every session
entry is itself a `filmot.event/v1` record with the same
command/status/data/errors/warnings vocabulary.

### Example Agent Workflow (Python)

```python
import subprocess
import json

# Search for relevant content
result = subprocess.run(
    ["filmot", "search", "python tutorial", "--raw"],
    capture_output=True, text=True
)
data = json.loads(result.stdout)

# Process results in your agent
for video in data.get("result", []):
    title = video["title"]
    hits = video["hits"]
    # Feed to LLM for analysis...
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Acknowledgments

- [Filmot](https://filmot.com/) - YouTube subtitle search engine
- [RapidAPI](https://rapidapi.com/) - API marketplace
- [Click](https://click.palletsprojects.com/) - CLI framework
- [Rich](https://rich.readthedocs.io/) - Terminal formatting library
