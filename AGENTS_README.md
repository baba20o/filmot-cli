# Filmot CLI: Agent Usage Guide

**A practical guide for AI agents using Filmot CLI to research YouTube content**

*Written by an agent, for agents, based on real-world research sessions.*

> **Also read: [AGENTS_RESEARCH_GUIDE.md](AGENTS_RESEARCH_GUIDE.md)** — How to evaluate sources, detect AI misinformation, cross-reference claims, and separate truth from hype. Essential methodology for any serious research task.

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

## The Killer Feature: Proximity Search (`NEAR/N`)

Before anything else, understand this — **proximity search is what makes this tool categorically different** from YouTube search, Google, or any other tool. It finds the exact moment two concepts are discussed together:

```bash
# Find moments where someone discusses troops in the context of Venezuela
filmot search '"Venezuela" NEAR/15 "military operation"' --full --lang en --sort density

# Find focused DOGE analysis, not random crypto mentions
filmot search '"DOGE" NEAR/15 "Elon"' --full --sort density --min-matches 3

# Find the exact moment someone connects AI with job loss
filmot search '"artificial intelligence" NEAR/20 "job displacement"' --full
```

The number after `NEAR/` is the word proximity window. `NEAR/15` means the two terms must appear within 15 words of each other. This turns a vague topic search into a surgical probe for **specific contextual relationships**.

**When to use it:**
- Investigating claims: `'"company" NEAR/10 "lawsuit"'`
- Finding connections: `'"person" NEAR/15 "scandal"'`
- Current events: `'"country" NEAR/10 "sanctions"'` with `--start-date`
- Technical deep dives: `'"concept A" NEAR/20 "concept B"'`

**Combine with `--sort density` and `--min-matches`** for best results. Density (matches per minute) surfaces focused content over passing mentions, and `--min-matches` cuts noise from videos that mention your terms once in a 3-hour podcast.

---

## Quick Start: The One-Command Research Workflow

The fastest way to research any topic:

```bash
# Single command: scout YouTube, search Filmot, download, and summarize
filmot research "nuclear fusion energy" --depth 12 --dedupe
```

This runs the **Scout → Search → Synthesize** pipeline:
1. **Scout** — Quick YouTube API probe for the latest uploads about your topic (last 7 days). Catches breaking news that Filmot hasn't indexed yet.
2. **Search** — Deep Filmot transcript search with title+transcript matching (auto-falls back to transcript-only if no title matches)
3. **Synthesize** — Merges both result sets, filters to 2+ subtitle matches (default), sorts by density (matches/min)
4. Download top 12 transcripts, skipping duplicates
5. Save everything to your local library under the topic name
6. Print a summary with source tags showing which came from scout vs Filmot

**Why this matters:** Filmot indexes transcripts ~24-48 hours after upload. For breaking news, the scout phase finds videos that Filmot can't see yet. Without it, you'd miss same-day developments entirely.

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
| `--min-matches N` | Only download videos with N+ subtitle matches (0 to disable) | 2 |
| `--sort [viewcount\|density]` | Sort by views or matches-per-minute | density |
| `--scout / --no-scout` | YouTube API freshness probe for latest uploads | On (if API key set) |
| `--scout-days N` | How far back the scout looks | 7 |
| `--probe` | Auto-extract entities from transcripts and run NEAR/N probes to discover related content | Off |

### Recommended Settings

```bash
# For most research topics — scout + density sort + min-matches 2 are defaults
filmot research "your topic" --depth 12 --dedupe

# Full pipeline: scout + search + probe (discovers content your initial search missed)
filmot research "Russia Ukraine ceasefire" --probe --depth 10 --dedupe

# For breaking news — narrow the scout window
filmot research "OpenClaw acquisition" --scout-days 3 --depth 15

# For popular topics with lots of content — be more selective
filmot research "artificial intelligence" --depth 15 --dedupe --min-matches 3 --min-views 50000

# For niche topics — cast a wider net, disable min-matches filter
filmot research "polymetallic nodules" --depth 20 --fallback --min-matches 0

# Skip scout if you only want indexed transcripts (faster, no YouTube API needed)
filmot research "your topic" --no-scout --sort viewcount
```

### The Probe Phase (--probe)

The `--probe` flag activates Phase 4: automatic NEAR/N discovery. After downloading transcripts, it:

1. **Extracts key entities** — mines your downloaded transcripts for frequent bigrams ("abu dhabi", "donald trump") and significant single words ("elections", "territory"), filtering out stopwords and the topic itself
2. **Finds co-occurring pairs** — scans text in sliding windows to find which entities appear together most often (e.g., "zelensky" + "elections" co-occur 8 times within 50 words)
3. **Generates NEAR/N probes** — turns the top pairs into `"entity1" NEAR/15 "entity2"` searches, anchored to your topic via title filtering
4. **Downloads discoveries** — the top 3 new videos (not already in your library) from the probes are downloaded

**Why this is powerful:** Your initial search finds videos explicitly about your topic. The probe phase finds videos that discuss the *relationships within* your topic — angles, connections, and context your original search missed. Each iteration surfaces new entities that could feed further probing.

```bash
# Example output:
# Probing key relationships from 8 transcripts...
#   Entities: abu dhabi, donald trump, zelensky, elections, donbass, territory...
#   Probe 1: "donald trump" NEAR/15 "vladimir putin" (co:8) → 115 results (peak: 3.1/min) | 48 new
#   Probe 2: "moscow" NEAR/15 "sanctions" (co:6) → 198 results (peak: 7.5/min) | 47 new
#   Downloading 3 probe discoveries...
#     ✓ Russia Ukraine Ceasefire Deal | Zelensky and Europe Prepare (probe)
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

## Multilingual Search

Filmot searches transcripts in **any language** YouTube auto-generates captions for. This includes Latin, Devanagari, Cyrillic, CJK, Arabic, Hebrew, Hangul, and more.

### Language Compatibility

| Language | Script | `--lang` code | Test Results |
|----------|--------|---------------|--------------|
| English | Latin | `en` | Full support |
| Spanish | Latin | `es` | 1M+ results |
| German | Latin | `de` | 75K+ results (handles umlauts) |
| Hindi | Devanagari | `hi` | 10.5K+ results |
| Russian | Cyrillic | `ru` | 339K+ results |
| Japanese | CJK | `ja` | 6M+ results |
| Korean | Hangul | `ko` | 277K+ results |
| Arabic | Arabic | `ar` | 127K+ results (RTL) |
| Hebrew | Hebrew | `iw` (not `he`) | 3.4K+ results (RTL) |
| Chinese | CJK | omit `--lang` | 672K+ results |

### Key Gotchas

1. **Hebrew uses `iw`, not `he`**: YouTube internally uses the legacy ISO 639 code `iw`. The standard `he` returns zero results. Use `--lang iw` or omit the lang filter entirely.

2. **Chinese lang filter doesn't work**: `--lang zh` and `--lang zh-Hans` both return zero results. Omit the `--lang` flag and search Chinese characters directly — works perfectly.

3. **CJK ASR tokenization**: YouTube's auto-captions for Chinese/Japanese insert spaces between individual characters. This doesn't affect search but makes raw transcripts less readable.

4. **RTL scripts work correctly**: Arabic and Hebrew transcripts render and search correctly, including match context extraction.

5. **When in doubt, omit `--lang`**: If a language code isn't returning results, remove the `--lang` filter. The API will match your query characters in any transcript regardless of language tag.

6. **Long Cyrillic queries + sort = 500 error**: `искусственный интеллект` (25 chars) with `--sort uploaddate` causes a server error due to URL-encoded length. Workaround: use shorter synonyms like `нейросеть` (77K results) or `ИИ` (66K results), which sort fine.

7. **Russian AI vocabulary**: Russians say `нейросеть` (neural network) as the everyday term for AI tools — not just the formal `искусственный интеллект`. Search both for comprehensive results.

8. **Hindi uses English loanwords freely**: Hindi YouTube mixes Devanagari and English. Searching `कृत्रिम बुद्धिमत्ता` finds formal Hindi AI content; searching `"AI"` with `--lang hi` finds 41K+ results of Hindi speakers using the English term.

### Examples

```bash
# Spanish
filmot search "inteligencia artificial" --lang es --full

# Japanese
filmot search "人工知能" --lang ja --full

# Hebrew (use "iw", NOT "he")
filmot search "בינה מלאכותית" --lang iw --full

# Chinese (MUST omit --lang)
filmot search "人工智能" --full

# Arabic
filmot search "الذكاء الاصطناعي" --lang ar --full

# Korean
filmot search "인공지능" --lang ko --full

# Hindi (Devanagari — standard ISO code)
filmot search "कृत्रिम बुद्धिमत्ता" --lang hi --full

# Russian (Cyrillic — standard ISO code)
filmot search "нейросеть" --lang ru --full

# Multilingual research workflow
filmot research "人工知能" --depth 10 --dedupe --sort density
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

### Tip 9: Non-English Language Codes
Most languages use standard ISO codes (`es`, `de`, `ja`, `ko`, `ar`, `hi`, `ru`). Two exceptions: **Hebrew** uses `iw` (not `he`), and **Chinese** requires omitting `--lang` entirely. For Russian, use shorter queries like `нейросеть` instead of `искусственный интеллект` when sorting — long Cyrillic URLs cause 500 errors. When in doubt, drop the `--lang` flag — the API matches query characters in any transcript.

### Tip 10: Pipe to Select-Object for Long Output
```powershell
filmot transcript VIDEO_ID --full 2>&1 | Select-Object -First 200
```

---

## Common Research Patterns

### Pattern 1: Full Topic Research (Recommended)
```bash
# One command does it all (density sort + min-matches 2 are defaults)
filmot research "brain-computer interfaces" --depth 15 --dedupe

# Then explore
filmot library compare "Neuralink" --sort density
filmot library compare "safety" --sort density
filmot library context brain-computer-interfaces --format structured
```

### Pattern 2: Current Events / Breaking News
```bash
# research command auto-scouts YouTube for latest uploads
filmot research "TOPIC" --scout-days 3 --depth 10

# Or manually: yt-search first (headlines), then Filmot (depth)
filmot yt-search "TOPIC" --days 3 --order relevance
filmot search 'TOPIC' --start-date 2026-01-01 --end-date 2026-02-01 --full --lang en
filmot transcript VIDEO_ID --full
```

**Key insight:** Filmot indexes transcripts ~24-48 hours after upload. For same-day events, `yt-search` (YouTube Data API) finds videos that Filmot can't see yet. The `research` command's `--scout` phase handles this automatically. If you're investigating something that happened today, always start with `yt-search` or use `--scout-days 1`.

### Pattern 3: Deep Discovery (Probe)
```bash
# Full pipeline: scout latest, search transcripts, auto-probe for connections
filmot research "TOPIC" --probe --depth 12 --dedupe

# Then explore what the probe found
filmot library compare "entity from probe" --topic TOPIC
filmot library search "new angle" --topic TOPIC
```

The `--probe` flag is the compounding move. It mines your downloaded transcripts for entity relationships, auto-generates NEAR/N searches, and downloads the best discoveries. Use this when you want the tool to actively find angles you didn't think to search for.

### Pattern 3b: Channel Corpus Mining (Deep Knowledge Base)

Download an entire channel's transcripts and mine them offline. This is the most powerful approach when a single channel is a rich source of domain knowledge (e.g., 300+ episodes of a trading podcast).

```bash
# 1. Download the full channel (runs in parallel, resumable)
filmot channel-download "Chat With Traders" --workers 4

# 2. Check status
filmot channel-status

# 3. Mine the corpus — plain text, NEAR/N, and tilde proximity all work
filmot channel-search chat-with-traders "blew up"
filmot channel-search chat-with-traders '"risk management" NEAR/10 "position sizing"'
filmot channel-search chat-with-traders '"revenge trading"~5'

# 4. Compare patterns across channels
filmot channel-search excess-returns "blew up"
filmot channel-search excess-returns '"risk management" NEAR/10 "position sizing"'
```

**When to use this over `filmot search`:**
- You want to mine a **specific channel** exhaustively (not just videos that match a query)
- You need **offline search** — no API calls, no rate limits, no quota
- You want **cross-channel comparisons** — run the same query across different corpora
- You're building a knowledge base for an agent to reason over

**Channel search supports the same proximity operators as the API:**

| Operator | Syntax | Example |
|----------|--------|---------|
| Plain | `"text"` | `"Sharpe ratio"` |
| NEAR/N | `"phrase1" NEAR/N "phrase2"` | `'"risk" NEAR/10 "sizing"'` |
| Tilde | `"word1 word2"~N` | `'"blew up account"~5'` |

### Pattern 4: Technical Deep Dive
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
| **Research a topic (one command)** | `filmot research "topic" --depth 12 --dedupe` |
| **Deep discovery research** | `filmot research "topic" --probe --depth 12 --dedupe` |
| **Breaking news research** | `filmot research "topic" --scout-days 3 --depth 10` |
| **Search latest YouTube uploads** | `filmot yt-search "topic" --days 7 --order relevance` |
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
| **Search non-English** | `filmot search "人工知能" --lang ja --full` |
| **Search Hebrew** | `filmot search "בינה מלאכותית" --lang iw --full` |
| **Search Chinese (no lang)** | `filmot search "人工智能" --full` |
| **Search Hindi** | `filmot search "कृत्रिम बुद्धिमत्ता" --lang hi --full` |
| **Search Russian** | `filmot search "нейросеть" --lang ru --full` |
| **Limit output** | `filmot transcript VIDEO_ID --full 2>&1 \| Select-Object -First 200` |
| **Download channel corpus** | `filmot channel-download "Channel Name" --workers 4` |
| **Check channel status** | `filmot channel-status` |
| **Search channel corpus** | `filmot channel-search SLUG "query"` |
| **Proximity search (channel)** | `filmot channel-search SLUG '"A" NEAR/10 "B"'` |
| **Tilde proximity (channel)** | `filmot channel-search SLUG '"word1 word2"~5'` |

---

*This guide was written based on actual research sessions using Filmot CLI across topics including AI security, nuclear fusion, solid-state batteries, brain-computer interfaces, humanoid robotics, and multilingual AI discourse (Hebrew, Chinese, Hindi, Russian).*

> **Next**: Read **[AGENTS_RESEARCH_GUIDE.md](AGENTS_RESEARCH_GUIDE.md)** for the methodology behind evaluating sources, detecting AI misinformation, cross-referencing claims across languages, and reporting findings with appropriate confidence levels.
