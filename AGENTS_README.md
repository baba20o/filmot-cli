# Filmot CLI: Agent Usage Guide

**A practical guide for AI agents using Filmot CLI to research YouTube content**

*Written by an agent, for agents, based on real-world research sessions.*

---

## What This Tool Does

Filmot CLI searches **YouTube transcripts** (not titles, not descriptions—the actual spoken words in videos). This is incredibly powerful because:

1. You can find discussions that aren't in video titles
2. You get the exact context of what was said
3. You can download full transcripts for deep analysis
4. You can build curated knowledge bases and cross-reference claims across sources
5. Date filtering lets you research current events in near-real-time

**Think of it as:** Google for what people *say* in YouTube videos.

---

## Quick Start: The One-Command Research Workflow

The fastest way to research any topic:

```bash
# Single command: searches, downloads, deduplicates, and summarizes
filmot research "nuclear fusion energy" --depth 12 --dedupe --min-matches 2 --sort density
```

This will:
1. Search for videos with "nuclear fusion energy" in both title and transcript
2. Filter to only videos with 2+ subtitle matches
3. Sort by relevance density (matches per minute) instead of views
4. Download top 12 transcripts, skipping duplicates
5. Save everything to your local library under the topic name
6. Print a summary: X saved, Y skipped, Z failed, total characters

Then cross-reference what your sources say:

```bash
# Compare how different sources discuss a specific claim
filmot library compare "tritium" --sort density

# Search for specific terms across all saved transcripts
filmot library search "tokamak"

# Export everything as structured markdown for deep analysis
filmot library context nuclear-fusion-energy --format structured
```

---

## The Research Command

`filmot research` is the compound command that orchestrates the full workflow in one step.

```bash
filmot research "your topic" [OPTIONS]
```

| Option | Description | Default |
|--------|-------------|---------|
| `-n, --depth N` | Number of transcripts to download | 10 |
| `--min-views N` | Minimum view count filter | None |
| `-l, --lang` | Language code | en |
| `--fallback` | Use AWS Transcribe when captions unavailable | Off |
| `--dedupe` | Skip duplicate/near-duplicate transcripts | Off |
| `--min-matches N` | Only download videos with N+ subtitle matches | None |
| `--sort [viewcount\|density]` | Sort by views or matches-per-minute | viewcount |

### Recommended Settings

```bash
# For most research topics — gets the most relevant, unique content
filmot research "your topic" --depth 12 --dedupe --min-matches 2 --sort density

# For popular topics with lots of content — be more selective
filmot research "artificial intelligence" --depth 15 --dedupe --min-matches 3 --min-views 50000 --sort density

# For niche topics — cast a wider net
filmot research "polymetallic nodules" --depth 20 --fallback
```

---

## Search Command

### Basic Search

```bash
filmot search "your query" --full --lang en
```

Always use `--full` to see all matches without truncation. Add `--lang en` for English videos.

### Key Search Options

| Option | Description | Example |
|--------|-------------|---------|
| `--min-matches N` | Only show videos with N+ subtitle matches | `--min-matches 3` |
| `--sort density` | Sort by matches-per-minute (client-side) | `--sort density` |
| `--dedupe` | Skip duplicates during bulk download | `--dedupe` |
| `--title TEXT` | Filter by video title (supports operators) | `--title "fusion energy"` |
| `--min-views N` | Minimum view count | `--min-views 10000` |
| `--bulk-download TOPIC:N` | Download top N transcripts to library | `--bulk-download fusion:10` |
| `--start-date` / `--end-date` | Date range filter (yyyy-mm-dd) | `--start-date 2026-01-01` |
| `--sort` | Sort: `viewcount`, `likecount`, `uploaddate`, `duration`, `chanrank`, `id`, `density` | `--sort viewcount` |

### Density Scoring

Search results automatically show **density scoring** — matches per minute of video. This tells you how focused a video is on your topic:

```
Matches (12): Density: 2.4/min
```

A 5-minute video with 12 matches (2.4/min) is more relevant than a 3-hour video with 4 matches (0.02/min). Use `--sort density` to sort by this metric.

### Search Syntax

```bash
# Phrase search (exact words in order)
filmot search '"prompt injection"' --full --lang en

# OR search (any term)
filmot search 'OpenAI|Anthropic|DeepMind' --full --lang en

# Proximity search (words within N words of each other)
filmot search '"artificial intelligence" NEAR/20 "job displacement"'

# Title + content filter (precision lever)
filmot search "cobalt" --title "deep sea mining" --min-views 10000

# Title supports operators too
filmot search "cobalt" --title 'deep sea (mining | extraction)'
```

### Get Full Transcript

```bash
filmot transcript VIDEO_ID --full
```

The `--full` flag outputs continuous text optimized for AI processing.

### Save to Library

```bash
filmot transcript VIDEO_ID --full --save-to prompt-injection
```

### Bulk Download from Search

```bash
filmot search "prompt injection" --bulk-download prompt-injection:10 --dedupe
```

---

## Transcript Library

The library stores transcripts organized by topic/keyword. This is your persistent knowledge base.

### Library Commands

```bash
# List all topics
filmot library list

# List transcripts in a topic
filmot library list prompt-injection

# Search across all saved transcripts (word-boundary by default)
filmot library search "attack vector"

# Cross-source comparison — how different sources discuss a term
filmot library compare "dark oxygen" --topic deep-sea-mining

# Get combined text for LLM context
filmot library context prompt-injection

# Structured markdown with metadata headers (auto-saves to file)
filmot library context prompt-injection --format structured

# Get combined text limited to 50K chars
filmot library context prompt-injection --max-chars 50000

# Show library statistics
filmot library stats

# Delete a transcript
filmot library delete VIDEO_ID

# Delete entire topic
filmot library delete topic-name --all
```

### Library Search: Word-Boundary Matching

Library search uses **word-boundary matching** by default. This prevents false positives:

- Searching "ore" won't match "more", "before", "explore"
- Searching "patent" won't match "patents" — but **auto-fallback kicks in**

**Auto-fallback behavior:** When word-boundary search finds zero results, it automatically retries with substring matching and shows a hint:

```
No exact word matches. Showing substring matches (plurals/inflections):
```

This catches plurals, verb forms, and inflections (e.g., "patent" finds "patents", "laser" finds "lasers"). Use `--substring` flag to force substring matching from the start.

### Library Compare: Cross-Source Verification

This is the power feature for fact-checking and analysis. Search for a term across all saved transcripts and see how each source discusses it:

```bash
filmot library compare "tritium" --sort density

# Output:
# Source 1: "The Future of Fusion" by Real Engineering
#   [3 mentions] Density: 1.2/min
#   "...tritium is the limiting factor in fusion power because..."
# Source 2: "Fusion Energy Explained" by Kurzgesagt
#   [1 mention] Density: 0.4/min
#   "...the fuel for fusion is deuterium and tritium, both..."
```

| Option | Description | Default |
|--------|-------------|---------|
| `--topic, -t` | Limit to specific topic | All topics |
| `--context, -c N` | Characters of context around matches | 300 |
| `--sort [mentions\|density]` | Sort by mention count or mentions-per-minute | mentions |

**Tip:** Use `--sort density` to find sources that discuss a term most intensely, not just most frequently. A 10-minute deep dive with 5 mentions is more useful than a 2-hour podcast with 6 passing mentions.

### Structured Context Export

For deep LLM analysis, export your library as structured markdown:

```bash
filmot library context nuclear-fusion-energy --format structured
```

This auto-saves to `{topic}-context.md` with full metadata headers:

```markdown
# Topic: nuclear-fusion-energy
## Video 1: "The Truth about Fusion" by Real Engineering
- Video ID: 73mXXJpEjRI
- Duration: 15m 32s | Views: 1,874,577
- Saved: 2026-02-11

[transcript text]

---
## Video 2: ...
```

---

## Pipeline Mode

For advanced workflows, pipe search results into the download command:

```bash
# Search with raw output, pipe to download
filmot search "deep sea mining" --title "deep sea mining" --raw | filmot download -t deep-sea --dedupe

# Multi-page search piped to download
filmot search-all "AI safety" --pages 5 --raw > results.json
type results.json | filmot download -t ai-safety --dedupe -n 20
```

---

## The Full Research Workflow (Step by Step)

If you want more control than `filmot research`, here's the manual workflow:

### Step 1: Research Command (Fastest Path)
```bash
filmot research "solid state batteries" --depth 15 --dedupe --min-matches 2 --sort density
```

### Step 2: Explore What You Have
```bash
# See what was saved
filmot library list solid-state-batteries

# Library stats
filmot library stats
```

### Step 3: Cross-Reference Claims
```bash
# How do sources discuss specific claims?
filmot library compare "energy density" --sort density
filmot library compare "Toyota" --sort density
filmot library compare "safety" --sort density
filmot library compare "cost" --sort density
```

### Step 4: Targeted Follow-Up Search
```bash
# Found a specific claim? Search more broadly
filmot search '"solid state battery" breakthrough' --min-views 10000 --sort density --min-matches 3

# Get a specific video's full transcript
filmot transcript VIDEO_ID --full
```

### Step 5: Export for Deep Analysis
```bash
# Structured markdown with metadata (auto-saves to file)
filmot library context solid-state-batteries --format structured

# Plain text with char limit
filmot library context solid-state-batteries --max-chars 100000 -o context.txt
```

---

## Search Syntax That Actually Works

### 1. Phrase Search (Most Useful)
```bash
filmot search '"prompt injection"' --full --lang en
```
Note: Use single quotes around the entire query to preserve inner double quotes in shell.

### 2. OR Search with Pipe
```bash
filmot search 'OpenAI|Anthropic|DeepMind' --full --lang en
```

### 3. Combined Phrases with OR
```bash
filmot search '"Tesla Optimus"|"Boston Dynamics"|"Figure AI"' --full --lang en
```

### 4. Date Filtering (Critical for Current Events)
```bash
filmot search '"prompt injection"' --start-date 2025-12-01 --end-date 2026-02-01 --full --lang en
```

### 5. Proximity Search (The Killer Feature)
```bash
filmot search '"artificial intelligence" NEAR/20 "job displacement"' --full
```
Finds moments where two concepts are discussed together, not just videos containing both words.

### 6. Title Filtering (Precision Lever)
```bash
filmot search 'security' --title "CES 2026" --full --lang en
```
Title supports operators: `--title 'deep sea (mining | extraction)'`

### 7. Channel Filtering
```bash
# Search specific channel by ID
filmot search 'AI safety' --channel-id UCxxxxxx --full --lang en

# Search multiple channels (comma-delimited)
filmot search 'AI safety' --channel-id UCxxxxxx,UCyyyyyy --full --lang en

# Find top channels discussing a topic
filmot search 'machine learning' --channel "programming" --channel-count 5 --full
```

### 8. View/Duration Filtering
```bash
# Popular content
filmot search 'prompt injection' --min-views 100000 --sort viewcount --full

# Long-form deep dives (30+ minutes)
filmot search 'humanoid robots' --min-duration 1800 --full

# Short explainers (under 10 minutes)
filmot search 'prompt injection' --max-duration 600 --full
```

### 9. Title Search for Proper Nouns (Critical!)
Phonetic transcription doesn't reliably capture proper nouns or brand names. Use generic transcript terms combined with `--title`:

```bash
# WRONG: Direct search often returns nothing
filmot search "clawdbot" --full

# RIGHT: Search generic terms, filter by title
filmot search 'AI|robot|open source' --title "clawdbot" --full
```

---

## Practical Tips

### Tip 1: Always Use `--dedupe` for Bulk Operations
Many YouTube channels repackage the same content. `--dedupe` hashes the first 500 characters of each transcript and skips duplicates.

### Tip 2: `--sort density` Over `--sort viewcount`
Default sort is by views, which biases toward popular channels over focused content. `--sort density` (matches per minute) finds the videos most intensely focused on your topic.

### Tip 3: `--min-matches` Cuts Noise
A video with 1 passing mention is rarely useful. `--min-matches 2` or `--min-matches 3` ensures videos have substantial coverage of your query.

### Tip 4: Library Compare Is Your Fact-Checker
After building a library on a topic, use `library compare` to see how different sources treat specific claims. This surfaces agreement, contradiction, and context across sources.

### Tip 5: Auto-Fallback Handles Plurals
Library search uses word-boundary matching but automatically falls back to substring matching when no exact matches are found. You don't need to worry about searching "patent" vs "patents".

### Tip 6: Structured Context for Long Analysis
`--format structured` creates well-organized markdown with video metadata headers. It auto-saves to a file so you don't dump 100KB+ to stdout.

### Tip 7: Conference Talks Are Gold
```bash
filmot search '"CES 2026"|"39C3"|"DEF CON"' --full --lang en
```

### Tip 8: Manual vs Auto Subtitles
Use `--manual-subs` for manually uploaded subtitles (higher quality, less coverage). Default searches auto-generated subtitles (wider coverage). Cannot search both in the same request.

### Tip 9: Pipe to Select-Object for Long Output
```powershell
filmot transcript VIDEO_ID --full 2>&1 | Select-Object -First 200
```

---

## Common Research Patterns

### Pattern 1: Full Topic Research (Recommended)
```bash
# One command does it all
filmot research "brain-computer interfaces" --depth 15 --dedupe --min-matches 2 --sort density

# Then explore
filmot library compare "Neuralink" --sort density
filmot library compare "safety" --sort density
filmot library context brain-computer-interfaces --format structured
```

### Pattern 2: Current Events
```bash
filmot search 'TOPIC' --start-date 2026-01-01 --end-date 2026-02-01 --full --lang en
filmot transcript VIDEO_ID --full
```

### Pattern 3: Technical Deep Dive
```bash
filmot search '"CONCEPT" explained|"CONCEPT" tutorial' --full --lang en --sort density
filmot transcript VIDEO_ID --full
```

### Pattern 4: Multiple Perspectives
```bash
filmot research "TOPIC" --depth 20 --dedupe --sort density
filmot library compare "criticism" --sort density
filmot library compare "benefit" --sort density
```

### Pattern 5: Pipeline for Custom Filtering
```bash
# Raw search → custom filter → download
filmot search "your query" --raw | filmot download -t my-topic --dedupe -n 20
```

---

## Errors and Edge Cases

### "Video is unavailable"
Some videos are region-locked, deleted, or private. Move on to another source.

### No transcript available
Use AWS Transcribe fallback:
```bash
filmot transcript VIDEO_ID --fallback --full
```
Requires: `yt-dlp`, `boto3`, `requests`, and AWS profile `APIBoss` configured.

### IP Blocked
YouTube may block your IP for transcript requests:
```bash
# Use proxy
filmot transcript VIDEO_ID --proxy http://user:pass@host:port --full

# Or set WEBSHARE_PROXY_USERNAME and WEBSHARE_PROXY_PASSWORD in .env

# Bypass proxy and connect directly
filmot transcript VIDEO_ID --no-proxy --full
```

### Very long transcripts
For 2+ hour videos, use `Select-Object -First N` or save to file:
```bash
filmot transcript VIDEO_ID --full -o transcript.txt
```

---

## Quick Reference

| Task | Command |
|------|---------|
| **Research a topic (one command)** | `filmot research "topic" --depth 12 --dedupe --sort density` |
| **Compare sources on a claim** | `filmot library compare "claim" --sort density` |
| **Search library** | `filmot library search "term"` |
| **Structured export** | `filmot library context TOPIC --format structured` |
| **Basic search** | `filmot search "query" --full --lang en` |
| **Phrase search** | `filmot search '"exact phrase"' --full --lang en` |
| **OR search** | `filmot search 'term1\|term2' --full --lang en` |
| **Proximity search** | `filmot search '"word1" NEAR/20 "word2"' --full` |
| **Date filter** | `filmot search "query" --start-date YYYY-MM-DD --end-date YYYY-MM-DD --full` |
| **Density sort** | `filmot search "query" --sort density --min-matches 3 --full` |
| **Get transcript** | `filmot transcript VIDEO_ID --full` |
| **Save to library** | `filmot transcript VIDEO_ID --full --save-to TOPIC` |
| **Bulk download (dedupe)** | `filmot search "query" --bulk-download TOPIC:10 --dedupe` |
| **Pipeline download** | `filmot search "query" --raw \| filmot download -t TOPIC --dedupe` |
| **List library** | `filmot library list` |
| **Library stats** | `filmot library stats` |
| **Limit output** | `filmot transcript VIDEO_ID --full 2>&1 \| Select-Object -First 200` |

---

*This guide was written based on actual research sessions using Filmot CLI across topics including AI security, nuclear fusion, solid-state batteries, brain-computer interfaces, and humanoid robotics.*
