# Filmot CLI

A powerful command-line interface for the [Filmot](https://filmot.com/) YouTube transcript search engine — Search YouTube video transcripts, get video metadata, and discover channels.

## 🌟 Features

### Core Features
- 🔍 **Subtitle Search** — Find videos by transcript/subtitle content with 24 filter options
- 📺 **Video Metadata** — Get comprehensive details for any YouTube video
- 📢 **Channel Discovery** — Search and explore YouTube channels by name or handle
- 📝 **Transcript Download** — Fetch full YouTube transcripts for deep content analysis
- 🎨 **Rich Terminal UI** — Beautiful formatted output with tables, colors, and clickable links
- 🤖 **AI Agent Mode** — Full output mode with no truncation for LLM/agent workflows
- 📤 **Raw JSON Mode** — Export raw API responses for scripting and automation

### Advanced Features
- 💾 **Smart Caching** — File-based response caching with configurable TTL to reduce API calls
- ⚡ **Rate Limiting** — Built-in token bucket rate limiter with adaptive backoff
- 📑 **Pagination** — Automatic multi-page fetching with `search-all` command
- 📊 **Export** — Export results to JSON or CSV for analysis
- 📺 **Watchlist** — Save videos locally with notes and tags for later viewing
- 🔄 **Batch Processing** — Process multiple queries from files (txt/json/csv)
- 🖥️ **Interactive Mode** — REPL-style interface for exploratory searching

## 📋 Requirements

- Python 3.8+
- RapidAPI account with Filmot API subscription

## 🚀 Installation

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

Create a `.env` file in the project root:
```env
RAPIDAPI_KEY=your_rapidapi_key_here
RAPIDAPI_HOST=filmot-tube-metadata-archive.p.rapidapi.com
```

Get your API key from [Filmot API on RapidAPI](https://filmot.com/api).

## 📖 Usage

> **Note:** If installed with `pip install -e .`, replace `python main.py` with `filmot` in all examples below.

### Search Subtitles

Search for videos containing specific text in their transcripts:

```bash
# Basic search
filmot search "hello world"

# Search in specific language
python main.py search "machine learning" --lang en

# Filter by views and sort by popularity
python main.py search "recipe" --min-views 10000 --sort viewcount --order desc

# Search within specific channels
python main.py search "tutorial" --channel "programming" --channel-count 5

# Filter by country and license
python main.py search "news" --country 217 --license 2

# Get full subtitle lines instead of snippets
python main.py search "python" --hit-format 1

# Full output for AI agents (no truncation)
python main.py search "AI" --full --hit-format 1

# Export raw JSON
python main.py search "AI" --raw > results.json
```

### 🔤 Query Syntax (Full-Text Operators)

Filmot uses [Manticore Search](https://manticoresearch.com/) under the hood. The following operators are supported in your search queries:

#### Basic Operators

| Operator | Syntax | Description | Example |
|----------|--------|-------------|---------|
| **AND** | `word1 word2` | Both words must appear (implicit) | `python tutorial` |
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

> **⚠️ Not Supported:** Prefix wildcards (`thermo*`), Quorum (`"words"/N`), and Strict Order (`<<`) are not currently supported by the Filmot API.

> **💡 NOTNEAR vs NOT:** The NOT operator (`-word`) excludes the **entire video** if the excluded word appears **anywhere** in the transcript. NOTNEAR is usually more practical — it only excludes matches where the terms appear close together. For example, `python NOTNEAR/10 beginner` finds "python" mentions that aren't in a beginner context (max distance: 500 words).

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

#### Combining Operators with Grouping

Use parentheses `()` to build complex queries combining multiple operators:

```bash
# Python AND (tutorial OR course)
filmot search 'python ("tutorial" | "course")'

# Find "machine learning" near "neural network" within 10 words
filmot search '"machine learning" NEAR/10 "neural network"'

# Exact phrase with proximity - words within 5 words of each other
filmot search '"deep learning" "tensorflow"~5'

# Find Python content that's NOT in a beginner context
filmot search 'python NOTNEAR/10 beginner'

# Complex: Find Filmot mentions near YouTube-related terms (with spelling variations)
filmot search '("filmot" | "philmot" | "filmont") NEAR/50 ("youtube" | "transcript" | "subtitle")'

# Find advanced Python discussions (exclude videos with "beginner" anywhere)
filmot search 'python "advanced" -beginner'
```

> **Note:** When using `|` (OR) in PowerShell, wrap your query in single quotes to prevent shell interpretation.

### Search Options Reference

| Option | Description | Example |
|--------|-------------|---------|
| `--lang, -l` | Language code | `--lang en` |
| `--page, -p` | Page number (50 results/page) | `--page 2` |
| `--category, -c` | Video category | `--category "Education"` |
| `--exclude` | Exclude categories (comma-separated) | `--exclude "Music,Gaming"` |
| `--channel-id` | Limit to specific channel ID | `--channel-id UCxyz...` |
| `--channel` | Search within channels matching text | `--channel "tech"` |
| `--channel-count` | Max channels for `--channel` (default 10) | `--channel-count 5` |
| `--title` | Filter by video title | `--title "review"` |
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
| `--sort` | Sort by: `viewcount` or `date` | `--sort viewcount` |
| `--order` | Order: `asc` or `desc` | `--order desc` |
| `--manual-subs` | Search manual subtitles only | `--manual-subs` |
| `--max-query-time` | Max query time (4-15000 ms) | `--max-query-time 5000` |
| `--hit-format` | 0=context snippets, 1=full lines | `--hit-format 1` |
| `--full` | Show all matches (no truncation) | `--full` |
| `--raw` | Output raw JSON response | `--raw` |

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

### View Configuration

Check your current API configuration:

```bash
python main.py config
```

## 🌍 Country Codes

| Code | Country | Code | Country | Code | Country |
|------|---------|------|---------|------|---------|
| 217 | United States | 153 | United Kingdom | 95 | Germany |
| 88 | France | 188 | Spain | 110 | Italy |
| 234 | Nigeria | 109 | India | 27 | Brazil |
| 35 | Canada | 13 | Australia | 116 | Japan |
| 189 | South Korea | 166 | Russia | 155 | Mexico |

> See the [Filmot API documentation](https://filmot.com/api) for a complete list of 250+ country codes.

## 📁 Project Structure

```
filmot-cli/
├── .env                    # API credentials (git-ignored)
├── .gitignore              # Git ignore rules
├── requirements.txt        # Python dependencies
├── main.py                 # CLI entry point
├── README.md               # This file
└── filmot/
    ├── __init__.py         # Package init with version
    ├── config.py           # Configuration & environment loading
    ├── api.py              # FilmotClient API wrapper with caching/rate limiting
    ├── cli.py              # Click CLI commands & Rich formatting
    ├── cache.py            # File-based response caching
    ├── rate_limiter.py     # Token bucket rate limiter
    ├── export.py           # JSON/CSV export functionality
    ├── watchlist.py        # Local video watchlist management
    ├── batch.py            # Batch query processing
    └── interactive.py      # Interactive REPL mode
```

## 🔧 Dependencies

| Package | Purpose |
|---------|---------|
| `requests` | HTTP API calls |
| `python-dotenv` | Environment variable management |
| `click` | CLI framework |
| `rich` | Terminal formatting, tables, colors |

---

## 📚 Advanced Features

### 📊 Export Results

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

### 📑 Paginated Search

Automatically fetch multiple pages of results:

```bash
# Fetch up to 10 pages of results
python main.py search-all "machine learning" --pages 10

# Limit total results and export
python main.py search-all "tutorial" --max-results 200 -o results.json

# With filters
python main.py search-all "python" --pages 5 --lang en --min-views 10000
```

### 💾 Cache Management

View and manage the response cache:

```bash
# View cache statistics
python main.py cache

# Clear all cached responses
python main.py cache --clear

# Clear only expired entries
python main.py cache --clear-expired
```

### 📺 Watchlist

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

### 🔄 Batch Processing

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

### 🖥️ Interactive Mode

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

## 📊 Example Output

### Subtitle Search
```
╭─────────────────────────────────────────────────────────╮
│ Found 356,432 results for: python                       │
╰─────────────────────────────────────────────────────────╯

1. Python Full Course for free 🐍
   Channel: Bro Code | Views: 20,213,163 | Likes: 958,283
   Duration: 12h 0m | Category: Science & Technology | Uploaded: 2021-02-15
   URL: https://youtube.com/watch?v=XKHEtdqhLK8
   Matches (11):
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

## 🔐 API Authentication

This CLI requires a RapidAPI key for the Filmot Tube Metadata Archive API:

1. Create a free account at [RapidAPI](https://rapidapi.com/)
2. Subscribe to the [Filmot API](https://filmot.com/api)
3. Copy your API key from the dashboard
4. Add it to your `.env` file

## � AI Agent Integration

This CLI is designed to work seamlessly with AI agents and LLMs that need complete context from YouTube transcripts.

### Full Output Mode

Use `--full` to get all subtitle matches without truncation:

```bash
# Get complete transcript matches for AI processing
python main.py search "machine learning tutorial" --full --hit-format 1

# Combine with filters for targeted results
python main.py search "python basics" --full --min-views 100000 --lang en
```

### Raw JSON for Programmatic Access

```bash
# Pipe to file for processing
python main.py search "data science" --raw > results.json

# Use with jq for filtering
python main.py search "tutorial" --raw | jq '.result[0].hits'
```

### Example Agent Workflow

```python
import subprocess
import json

# Search for relevant content
result = subprocess.run(
    ["python", "main.py", "search", "python tutorial", "--raw", "--full"],
    capture_output=True, text=True
)
data = json.loads(result.stdout)

# Process results in your agent
for video in data.get("result", []):
    title = video["title"]
    hits = video["hits"]
    # Feed to LLM for analysis...
```

## �🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

## 🙏 Acknowledgments

- [Filmot](https://filmot.com/) - YouTube subtitle search engine
- [RapidAPI](https://rapidapi.com/) - API marketplace
- [Click](https://click.palletsprojects.com/) - CLI framework
- [Rich](https://rich.readthedocs.io/) - Terminal formatting library
