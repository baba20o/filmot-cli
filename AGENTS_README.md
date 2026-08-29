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
4. You can build curated knowledge bases and locate claim-bearing passages across sources
5. Date filtering lets you research current events in near-real-time
6. You can compare full saved transcripts for possible shared lineage
7. You can keep a strict claim/evidence register with explicit human assessments
8. You can route follow-up searches into named, resumable investigation sessions

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

**Syntax rule:** when combining OR with proximity, use grouped OR on either side of `NEAR/N` or `NOTNEAR/N`, for example `("memory" | "context") NEAR/20 "production"`. Do not write `"memory|context" NEAR/20 "production"`; that backend form is invalid.

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

This runs the **Scout → Staged Search → Preview → Download** pipeline:

1. **Scout** — Quick YouTube API probe for the latest uploads about your topic (last 7 days). Catches breaking news that Filmot hasn't indexed yet.
2. **Staged search** — Tries title+transcript, an exact phrase, and `NEAR/N` before considering loose transcript-wide matching.
3. **Safety gate** — Exact/`NEAR/N` fallbacks require at least 75% topic-token coverage in one visible passage; a high-cardinality loose fallback is blocked unless `--accept-broad` is explicit.
4. **Preview and rank** — Shows relevance, density, echo risk, and a separate audience/engagement `source-prior`; balanced ranking is the default.
5. **Download and checkpoint** — Saves selected transcripts and per-item outcomes under the Unicode-safe topic name.
6. **Probe (optional)** — Extracts cross-source entities, reports co-occurrence-window and source support, and runs transparent `NEAR/N` follow-ups.

**Why this matters:** Filmot indexes transcripts ~24-48 hours after upload. For breaking news, the scout phase finds videos that Filmot can't see yet. Without it, you'd miss same-day developments entirely.

The source prior is an unverified popularity/engagement heuristic, not a credibility or truth score. Treat every automatically selected transcript as candidate material until you inspect the passage and verify important claims.

Then cross-reference what your sources say:

```bash
# Locate passages that use the same claim term or phrase
filmot library compare "tritium" --sort density

# Search for specific terms across all saved transcripts
filmot library search "tokamak"

# Audit possible common lineage before counting sources as independent
filmot library echoes nuclear-fusion-energy --raw

# Record one exact claim and its classified source relationship
filmot claims add nuclear-fusion-energy "One exact, falsifiable statement"
filmot claims cite nuclear-fusion-energy c-CLAIMID \
  --video VIDEO_ID --at 180 --relation supports

# Export everything as structured markdown for deep analysis
filmot library context nuclear-fusion-energy --format structured

# Summarize the investigation later without conflating unlike counts
filmot sessions nuclear-fusion-energy --summary
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
| `--dedupe` | Skip transcripts with the same first-500-character fingerprint | Off |
| `--min-matches N` | Only download videos with N+ subtitle matches (0 to disable) | 2 |
| `--sort [balanced\|density\|source-prior\|viewcount]` | Rank fetched candidates; source-prior is an unverified audience/engagement heuristic | balanced |
| `--candidate-pages N` | Filmot pages fetched before client-side ranking | 3 |
| `--candidate-pool N` | Maximum Filmot candidates scored | 150 |
| `--accept-broad` | Permit a high-cardinality loose fallback after preview/gating | Off |
| `--broad-threshold N` | Loose-result cardinality requiring explicit acceptance | 1000 |
| `--channel` / `--channel-id` | Restrict candidates to resolved or exact channel IDs; fail closed on resolution errors | None |
| `--scout / --no-scout` | YouTube API freshness probe for latest uploads | On (if API key set) |
| `--scout-days N` | How far back the scout looks | 7 |
| `--probe` | Auto-extract entities from transcripts and run NEAR/N probes to discover related content | Off |
| `--verbose` | Show full transcript failure details | Off |

### Recommended Settings

```bash
# For most research topics — scout + balanced rank + min-matches 2 are defaults
filmot research "your topic" --depth 12 --dedupe

# Full pipeline: scout + search + probe (discovers content your initial search missed)
filmot research "Russia Ukraine ceasefire" --probe --depth 10 --dedupe

# For breaking news — narrow the scout window
filmot research "OpenClaw acquisition" --scout-days 3 --depth 15

# For popular topics with lots of content — be more selective
filmot research "artificial intelligence" --depth 15 --dedupe --min-matches 3 --min-views 50000

# For niche topics — cast a wider net, disable min-matches filter
filmot research "polymetallic nodules" --depth 20 --fallback --min-matches 0

# Review and explicitly permit a large loose fallback
filmot research "broad topic" --accept-broad --candidate-pages 5

# Restrict research to an explicitly resolved source
filmot research "grid demand" --channel "Lawrence Berkeley National Laboratory"

# Skip scout if you only want indexed transcripts (faster, no YouTube API needed)
filmot research "your topic" --no-scout --sort viewcount
```

### The Probe Phase (--probe)

The `--probe` flag activates Phase 4: automatic NEAR/N discovery. After downloading transcripts, it:

1. **Extracts key entities** — preserves source/sentence boundaries, filters stopwords, clusters likely ASR variants, and prefers terms supported by multiple transcripts
2. **Finds co-occurring pairs** — scans overlapping 50-word windows (25-word stride) without crossing source or sentence boundaries; a pair must occur in at least two transcripts
3. **Generates NEAR/N probes** — turns the top pairs into `"entity1" NEAR/15 "entity2"` searches; it retains a title constraint when the initial title stage proved usable, otherwise it applies the displayed passage-level topic relevance filter
4. **Downloads discoveries** — the top 3 new videos (not already in your library) from the probes are downloaded

**Why this is powerful:** Your initial search finds videos explicitly about your topic. The probe phase finds videos that discuss the *relationships within* your topic — angles, connections, and context your original search missed. Each iteration surfaces new entities that could feed further probing.

```bash
# Example output:
# Probing key relationships from 8 transcripts...
#   Entities: abu dhabi, donald trump, zelensky, elections, donbass, territory...
#   Probe 1: "donald trump" NEAR/15 "vladimir putin" (co-windows:8; sources:3; scope:title="...") → 115 API results
#   Probe 2: "moscow" NEAR/15 "sanctions" (co-windows:6; sources:2; scope:topic relevance post-filter) → 198 API results
#   Downloading 3 probe discoveries...
#     ✓ Russia Ukraine Ceasefire Deal | Zelensky and Europe Prepare (probe)
```

---

## Search Command

### Basic Search

```bash
filmot search "your query" --full --lang en
```

Use `--full` when you need all non-duplicate hit snippets returned for
displayed videos on the candidate pages already fetched. It removes the normal
per-video hit cap, but repeated duplicate segments may still be collapsed and
it does not fetch additional pages. Add `--lang en` for English videos.

### Key Search Options

| Option | Description | Example |
|--------|-------------|---------|
| `--min-matches N` | Only show videos with N+ subtitle matches | `--min-matches 3` |
| `--sort density` | Sort fetched candidates by matches-per-minute (client-side; not a global credibility score) | `--sort density` |
| `--pages N` / `--candidate-pool N` | Widen or cap the candidates considered by client-side ranking | `--pages 3 --candidate-pool 120` |
| `--limit N` / `--max-hits N` | Bound displayed videos and per-video hit details independently | `--limit 20 --max-hits 5` |
| `--dedupe` | Skip matching first-500-character transcript fingerprints during bulk download | `--dedupe` |
| `--title TEXT` | Filter by video title (supports operators) | `--title "fusion energy"` |
| `--min-views N` | Minimum view count | `--min-views 10000` |
| `--bulk-download TOPIC:N` | Download top N transcripts to library | `--bulk-download fusion:10` |
| `--start-date` / `--end-date` | Date range filter (yyyy-mm-dd) | `--start-date 2026-01-01` |
| `--sort` | Sort: `viewcount`, `likecount`, `uploaddate`, `duration`, `chanrank`, `id`, `density` | `--sort viewcount` |
| `--context N` | Characters of context per side in snippets (raise for fuller quotes) | `--context 120` |
| `--channel TEXT` | Resolve and display matching channel IDs; fail closed if none resolve | `--channel "Primary Lab"` |
| `--session NAME` | Route this search event to a named investigation | `--session robin-ai-scientist` |

Search-session routing precedence is explicit `--session`, then
`FILMOT_SESSION`, then the TOPIC parsed from `--bulk-download TOPIC[:N]`, then
the current date. The session only chooses the activity ledger; it does not
change query scope or the transcript-library topic.

### Reading the results (signals built into the display)

Every result surfaces navigation and heuristic signals inline, so you can triage at read speed:

- **Timestamped deep links** — the `Video:` URL and every match link jump straight to the moment (`&t=312s`), not 0:00. Click the hit, land on the sentence.
- **Engagement ratio** — `Engagement: 0.7%` (likes/views) next to the view count. It can help prioritize inspection, but does not establish expertise, independence, credibility, or truth.
- **Echo warning** — if result snippets share near-identical phrasing, they're tagged `[echo#N]` and a warning prints up top. This is an advisory reuse/common-lineage signal, not proof of copying, dependence, credibility, falsity, or truth. Use `library echoes` on the full saved transcripts before making an independence judgment.
- **Freshness note** — when your date window reaches the last few days, the tool reminds you Filmot lags ~24-48h and prints the exact `yt-search` command to catch launch-day coverage (guide Trap 5/7).

### Density Scoring

Search results automatically show **density scoring** — matches per minute of video. This tells you how focused a video is on your topic:

```
Matches (12): Density: 2.4/min
```

A 5-minute video with 12 matches (2.4/min) is more focused on the query than a 3-hour video with 4 matches (0.02/min). Density says nothing about truth or source authority. Unless you explicitly fetch multiple candidate pages, client-side sorting ranks only the returned page.

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

Unquoted words use loose transcript-wide implicit AND and may be far apart:

```bash
filmot search "machine learning"                  # loose AND
filmot search '"machine learning"'                # exact phrase
filmot search '("OpenAI" | "Anthropic") "safety"' # grouped OR + AND
filmot search '"AI" NEAR/20 "job loss"'           # proximity
```

Literal phrase and `NEAR/N` searches can undercount singular/plural,
inflection, spelling, and auto-caption variants. When a strict query returns
few results, try those variants before concluding the subject is rare.

### Get Full Transcript

```bash
filmot transcript VIDEO_ID --full
```

The `--full` flag outputs continuous text optimized for AI processing.

### Search Inside a Transcript (`--grep`)

You don't have to download the whole transcript and eyeball it — `--grep` runs the **same proximity operators** (NEAR/N, OR-groups, `~N` tilde, plain substring) against a single fetched transcript and prints only the matching moments, each with a timestamped deep link:

```bash
filmot transcript VIDEO_ID --grep '"risk management" NEAR/10 "position sizing"'
filmot transcript VIDEO_ID --grep '("memory" | "context") NEAR/15 "production"'
filmot transcript VIDEO_ID --grep '"first plasma"~5'
```

This closes the search → download → grep loop inside the tool: find a promising video with `search`, then probe its exact content without leaving the CLI.

### Save to Library

```bash
filmot transcript VIDEO_ID --full --save-to prompt-injection
```

The save retains the complete text, any available timestamped source-caption
segments, and normalized source metadata for local citations. Older text-only
records are normalized in memory with an empty segment list and are not
rewritten on read. If `--chunk` and `--timestamps` are both present, chunked
presentation wins for the terminal and plain-text export; raw/JSON output and
library saves still retain the original source segments. Bulk, pipeline, and
research library saves use the same record shape. Each save validates its
record, writes strict JSON to a unique same-directory temporary, flushes and
`fsync`s it, and atomically replaces the destination, so a failure before that
replace preserves any existing record.

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
filmot library list prompt-injection --raw

# Search across all saved transcripts (word-boundary by default)
filmot library search "attack vector"
filmot library search "attack vector" --topic prompt-injection --raw

# Cross-source concordance — locate passages that use the same term
filmot library compare "dark oxygen" --topic deep-sea-mining
filmot library compare "dark oxygen" --topic deep-sea-mining --raw

# Advisory full-transcript shared-phrasing analysis
filmot library echoes deep-sea-mining
filmot library echoes deep-sea-mining --ngram 5 --threshold 0.5 --raw
filmot library echoes deep-sea-mining --persist

# Get combined text for LLM context
filmot library context prompt-injection

# Structured markdown with metadata headers (auto-saves to file)
filmot library context prompt-injection --format structured

# Get combined text limited to 50K chars
filmot library context prompt-injection --max-chars 50000

# Show library statistics
filmot library stats

# Assign one ambiguous legacy directory after reviewing its ownership
filmot library migrate-topic "AI 人工知能"

# Delete a transcript
filmot library delete VIDEO_ID

# Delete entire topic
filmot library delete topic-name --all
```

Inspection is deliberately quiet: `library list`, `search`, `compare`, and
`stats` never append session events. `library context` also remains read-only
when it prints to stdout; a context file write, including the structured
format's automatic save, is logged. `library echoes` never logs, even with
`--persist`. Raw mode does not alter these rules.

### Library Search: Word-Boundary Matching

Library search uses **word-boundary matching** by default. This prevents false positives:

- Searching "ore" won't match "more", "before", "explore"
- Searching "patent" won't match "patents" — but **auto-fallback kicks in**

**Auto-fallback behavior:** When word-boundary search finds zero results, it automatically retries with substring matching and shows a hint:

```
No exact word matches. Showing substring matches (plurals/inflections):
```

This catches plurals, verb forms, and inflections (e.g., "patent" finds "patents", "laser" finds "lasers"). Use `--substring` flag to force substring matching from the start.

### Library Compare: Cross-Source Concordance

This is a lexical navigation tool: search for a term across saved transcripts and inspect the passages in which each source uses it. It counts text matches; it does not infer stance, agreement, contradiction, source independence, credibility, or truth.

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

**Tip:** Prefer specific phrases over generic words that also occur in idioms. Use `--sort density` to find sources that use the text most intensely, then read the passages and verify factual claims against primary sources.

Raw local `search` and `compare` rows carry citation-ready excerpt details:
text and character offsets for every record, plus timestamps and YouTube deep
links when source-caption segments were saved. A legacy text-only record
remains searchable, but Filmot does not invent a timestamp it never stored;
invalid, incomplete, or text-misaligned segment timing stays untimed.

### Library Echoes: Advisory Lineage Analysis

`library echoes TOPIC` compares the complete saved transcript for every source
in that topic using Unicode-normalized word n-gram Jaccard similarity. It
defaults to 5-word shingles and a `0.5` threshold, returns every pair score,
and builds deterministic single-linkage clusters for pairs that meet the
threshold. Method v2 pins its extended Han, Kana, Bopomofo, and Hangul
tokenization ranges; metadata also records the runtime Unicode database used
for NFKC normalization and case folding.

Clusters identify phrasing worth investigating. They do not establish which
source came first, whether one copied another, whether sources are independent,
or whether a claim is true. Without `--persist`, the command is read-only.
`--persist` writes a content-addressed, non-overwriting artifact at
`.filmot_data/analysis/TOPIC/echoes-HASH.json`; its SHA-256 covers the canonical
stored payload except the self-describing hash field, and an existing file is
verified before reuse. The command still does not log.
Human rendering is capped at the 25 strongest matching pairs; `--raw` and the
artifact retain the complete pair matrix.

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

## Claims and Evidence

Use the claim register after locating passages, not as an automatic fact
checker. A claim is an exact statement; every citation has a human-selected
relationship, and every verdict is an explicit human assessment.

```bash
# Add an atomic statement (default ID is stable and text-derived)
filmot claims add robin-ai-scientist \
  "Robin nominated ripasudil for testing in an AMD model"

# Cite a saved or external video at an exact moment
filmot claims cite robin-ai-scientist c-CLAIMID \
  --video VIDEO_ID --at 312 --relation supports \
  --excerpt "short exact source text" --secondary

# Cite a primary document and keep source text separate from analyst notes
filmot claims cite robin-ai-scientist c-CLAIMID \
  --source "https://example.org/paper" --source-kind paper \
  --relation qualifies --locator "Methods, p. 4" \
  --excerpt "short exact source text" --note "Scope limitation" \
  --primary --independence independent

# Record or supersede a human assessment
filmot claims assess robin-ai-scientist c-CLAIMID \
  --verdict mixed --confidence medium --note "Preclinical result only"

# Inspect the register or one claim
filmot claims show robin-ai-scientist
filmot claims show robin-ai-scientist c-CLAIMID --raw
```

All four subcommands accept `--raw`. Relations are `supports`, `contradicts`,
`qualifies`, `context`, `origin`, and `mentions`. Verdicts are `open`,
`supported`, `contradicted`, and `mixed`; confidence is `unknown`, `low`,
`medium`, or `high`. Use `--independence independent|echo` and
`--lineage-group` only when you have made that lineage judgment; Filmot does
not infer it from an echo cluster. `--at` requires a video and accepts only a
finite, non-negative number of seconds. `--video` cannot be paired with a
non-video `--source-kind` or with `--source`; one evidence event always
describes one source. `--video` accepts exactly 11 YouTube-ID characters
matching `[A-Za-z0-9_-]{11}`, not a URL, and malformed values fail before
persistence; a valid ID supplies its canonical YouTube URL.

Text-derived IDs use the recorded `utf8-ascii-whitespace/v1` method: exact
UTF-8 code points and case are preserved while ASCII whitespace is folded.
This avoids Python/Unicode-database-dependent IDs; manual IDs record `explicit`.
Evidence and assessment IDs record `canonical-json-array/v2`, which hashes an
unambiguous canonical JSON field vector. For evidence, v2 covers every
persisted identity/provenance input: claim and relation; source/kind;
video/time or document locator; excerpt and note; primary, independence, and
lineage classifications; plus title, channel, and research run. The derived
video deep link is validated separately. Strict replay validates those IDs and
the linear assessment-supersedes chain.

Claim events are strict, append-only files under
`.filmot_data/claims/TOPIC/*.json`. Persistence failures fail the command, and
a later assessment supersedes rather than erases history. Per-topic OS locks
cover each full read/check/append transaction, and every event is validated
before publication, so concurrent mutations cannot fork an assessment chain.
New events use `filmot.claim/v2` with contiguous per-topic sequence numbers.
Sequence-less `filmot.claim/v1` histories are accepted for compatibility,
ordered in memory, and never rewritten; a valid legacy history can be
continued by appending v2 events after its in-memory sequence. The v1 records
remain read-only even though the topic history remains appendable.
`add`, `cite`, and `assess` append compact session events containing IDs and
classifications but not the claim text or excerpts. `show` is read-only, does
not log, and does not create claim storage when the topic is absent.

---

## Session Ledger (resuming an investigation)

Searches, research phases, transcript/download writes, channel work, context
file writes, and compact claim mutations log to `.filmot_data/sessions/`.
Library `list`, `search`, `compare`, `stats`, stdout-only `context`, and every
`echoes` run do not log. This matters for agents: a fresh instance with no
memory of yesterday can read the ledger and resume an investigation without
polluting it merely by inspecting existing state.

`.filmot_data` is resolved from the invocation directory, so it belongs to the
active research project; set `FILMOT_DATA_DIR` when an agent must use a
different explicit project root. Do not treat proxy state as project memory.
Proxy credentials live in Filmot's per-user configuration directory, while
credential-free health and leases live in the per-user state directory.

```bash
filmot sessions                    # list all sessions (newest activity first)
filmot sessions fable-5-mythos     # replay a topic-scoped research session
filmot sessions fable-5-mythos --summary
filmot sessions fable-5-mythos --summary --raw
filmot sessions 2026-06-10         # replay a day's ad-hoc search queries
filmot sessions 2026-06-10 --raw   # one result with an events array
```

`research <topic>` logs a run ID, `research_start`, phase checkpoints, every
selected/downloaded item, and `research_end` with completed, failed, or
interrupted status to `<topic>.jsonl`. Search routing is explicit `--session`,
then `FILMOT_SESSION`, then the TOPIC from `--bulk-download TOPIC[:N]`, then
the current date. A search interrupted before its final outcome records one
`interrupted` event; interruption after that outcome does not duplicate it.
Partial runs therefore retain enough state to inspect completed work and resume
deliberately.

`--summary` folds one named session while keeping unlike universes separate:
standalone-search API/fetched/post-filter counts, staged research-search counts
after their explicit gates, selected-download and probe outcomes, unique saved
transcripts, failed attempts and unique failed videos, and claim mutations. It does not
reinterpret those numbers as one source count. Listing, replaying, or
summarizing sessions is read-only, never logs the inspection, and does not
create project storage on an empty workspace. Malformed/unreadable ledger
records are surfaced as typed read errors: a partly readable replay/summary is
`partial`, while a session with no readable events is `failed`, never silently
reported as complete or empty.

Legacy versions collapsed non-Latin names into `uncategorized` and could strip
Unicode from mixed-script topics. Filmot never assigns those ambiguous
directories automatically. `filmot library migrate-topic TOPIC` moves the
entire derived legacy directory and cannot infer or partition ownership.
Review the displayed source and destination slugs, and confirm only when every
source file belongs to that topic. Existing destination conflicts remain in
the legacy directory and are never overwritten.

Current topic slug v1 routing uses Python's bundled Unicode database for NFKC,
case folding, and character categories. Pin the Python minor version for a
shared `.filmot_data` directory and inspect routing before upgrading it:
changes to Unicode tables can move unusual or newly assigned code points. This
behavior remains for compatibility, and `migrate-topic` does not repair such
Unicode-version drift.

`sessions NAME --raw` emits one `filmot.result/v1` object, so use
`jq '.events[]'` when you want to stream individual `filmot.event/v1` records.
`sessions NAME --summary --raw` instead exposes the derived object in
`summary`.
The `.filmot_data/sessions/*.jsonl` storage files remain newline-delimited
internally. Proxy status, refresh, and probe activity is machine state and is
therefore deliberately absent from this project ledger.

---

## Pipeline Mode

For advanced workflows, pipe search results into the download command:

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

### Step 3: Locate Claim-Bearing Passages
```bash
# Find every saved passage using these exact terms, then inspect and verify it
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
filmot research "人工知能" --lang ja --depth 10 --dedupe --sort density
```

---

## Practical Tips

### Tip 1: Use `--dedupe` When Repackaging Is Likely
Many YouTube channels repackage the same content. `--dedupe` hashes the first
500 characters of each transcript and skips matching fingerprints. It is an
exact prefix check, not semantic near-duplicate or lineage analysis; use
`library echoes` separately when shared phrasing matters.

### Tip 2: `--sort density` Over `--sort viewcount`
Default sort is by views, which biases toward popular channels over focused content. `--sort density` (matches per minute) finds the videos most intensely focused on your topic.

### Tip 3: `--min-matches` Cuts Noise
A video with 1 passing mention is rarely useful. `--min-matches 2` or `--min-matches 3` ensures videos have substantial coverage of your query.

### Tip 4: Library Compare Is a Concordance
After building a library on a topic, use `library compare` to navigate every source that uses a term or exact phrase. The command surfaces passages for human or agent review; it does not establish agreement, contradiction, or truth by itself.

### Tip 5: Auto-Fallback Handles Plurals
Library search uses word-boundary matching but automatically falls back to substring matching when no exact matches are found. You don't need to worry about searching "patent" vs "patents".

### Tip 6: Structured Context for Long Analysis
`--format structured` creates well-organized markdown with video metadata headers. It auto-saves to a file so you don't dump 100KB+ to stdout.

### Tip 7: Disambiguate Colliding Vocabulary
Generic terminology can cross domains: "circuit tracing" also finds electricians, "induction heads" finds engine parts, and "Vera Rubin" can mean an observatory or Nvidia architecture. Anchor ambiguous phrases with a domain term using `NEAR/N`, then narrow with `--title`, `--category`, or an explicitly resolved channel.

### Tip 8: Conference Talks Are Gold
```bash
filmot search '"CES 2026"|"39C3"|"DEF CON"' --full --lang en
```

### Tip 9: Manual vs Auto Subtitles
Use `--manual-subs` for manually uploaded subtitles (higher quality, less coverage). Default searches auto-generated subtitles (wider coverage). Cannot search both in the same request.

### Tip 10: Non-English Language Codes
Most languages use standard ISO codes (`es`, `de`, `ja`, `ko`, `ar`, `hi`, `ru`). Two exceptions: **Hebrew** uses `iw` (not `he`), and **Chinese** requires omitting `--lang` entirely. For Russian, use shorter queries like `нейросеть` instead of `искусственный интеллект` when sorting — long Cyrillic URLs cause 500 errors. When in doubt, drop the `--lang` flag — the API matches query characters in any transcript.

### Tip 11: Pipe to Select-Object for Long Output
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
filmot channel-search chat-with-traders '("risk" | "drawdown") NEAR/10 ("position" | "sizing")'
filmot channel-search chat-with-traders '"revenge trading"~5'

# 4. Compare patterns across channels
filmot channel-search excess-returns "blew up"
filmot channel-search excess-returns '"risk management" NEAR/10 "position sizing"'
```

**Proximity terms match whole words.** `"account"` will not match "accounts" or "accountability" — add explicit alternatives for inflections: `("account" | "accounts")`. Terms must be double-quoted; unquoted operands like `risk NEAR/10 position` are rejected with an error explaining the supported forms.

**When to use this over `filmot search`:**
- You want to mine a **specific channel** exhaustively (not just videos that match a query)
- You need **offline search** — no API calls, no rate limits, no quota
- You want **cross-channel comparisons** — run the same query across different corpora
- You're building a knowledge base for an agent to reason over

**Channel search supports the same proximity operators as the API:**

| Operator | Syntax | Example |
|----------|--------|---------|
| Plain | `"text"` | `"Sharpe ratio"` |
| NEAR/N | `"phrase1" NEAR/N "phrase2"` or `("a" \| "b") NEAR/N ("c" \| "d")` | `'"risk" NEAR/10 "sizing"'` |
| Tilde | `"word1 word2"~N` | `'"blew up account"~5'` |

Use grouped OR for proximity queries. Do not put `|` inside a quoted NEAR operand like `"risk|drawdown" NEAR/10 "position"`.

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

Transcript commands print the actual redacted route plan and durable attempt
progress to stderr. Requests use bounded connect/read/route deadlines.
`FILMOT_PROXY_MODE=auto` tries the pool and then the initialized primary route;
`proxy-only` never implies direct fallback; `primary-only` uses only the
initialized primary route (including an explicit CLI `--proxy`);
`direct-only` ignores environment proxy variables.

`filmot proxy status` distinguishes available, recently healthy, untested,
cooling, failing, retired, and invalid sessions. `filmot proxy refresh`
re-pulls API-backed pools but reloads a file-backed session list locally.
`filmot proxy test` streams each redacted, bounded probe instead of buffering
all results.

Proxy configuration is machine-scoped by default. Put shared values such as
`WEBSHARE_API_TOKEN` in the per-user `config.env`:

- Windows: `%APPDATA%\filmot\config.env`
- macOS: `~/Library/Application Support/filmot/config.env`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/filmot/config.env`

Existing process environment variables take precedence, followed by the
per-user file and then `.env` in the current working directory. Useful
overrides are `FILMOT_CONFIG_FILE`, `FILMOT_CONFIG_DIR`,
`FILMOT_STATE_DIR`, and `FILMOT_CACHE_DIR`.

The default session inventory is `webshare_info.txt` in that per-user config
directory. Health, cooldowns, the round-robin cursor, and short-lived leases
are coordinated through a credential-free SQLite database in the per-user
state directory. Legacy `.filmot_data/webshare_info.txt`,
`.filmot_data/webshare_pool.json`, and
`.filmot_data/webshare_pool_file.json` are migrated non-destructively on first
use and retained. A custom `WEBSHARE_SESSION_FILE` is not relocated.

### Exit and raw-output contracts

- A successful empty search exits 0.
- Configuration, API, and total transport failures exit nonzero.
- Commands that permit partial item success print and log the partial counts;
  total item failure exits nonzero.
- Once Click accepts the command line, `--raw` emits exactly one JSON value on
  stdout. Interactive route progress is suppressed and other diagnostics stay
  off stdout; JSON error output still carries a nonzero exit. Invalid CLI
  syntax/options use Click's text usage error and exit 2 before raw mode runs.
  External user interruption and a broken stdout pipe can stop the process
  before a complete value is written and are outside this result contract.
  Search JSON reflects client-side filters, ranking, limits, and scope metadata
  rather than an untouched upstream response.
- Strict raw serialization rejects `NaN`/`Infinity` and emits ASCII-escaped
  JSON. Unicode strings therefore appear with JSON escapes on the wire but
  decode to the original code points. `main.py` propagates `main()`'s return
  through `SystemExit`, so automation receives the documented status.
- Raw command results use `filmot.result/v1`. Object payloads keep their domain
  keys and add `_filmot` metadata (`schema`, `command`, `status`, `errors`,
  `warnings`). Library list/search/compare use `rows`; local search and compare
  rows include citation details, with timestamps and deep links when saved
  segments exist. `library echoes` adds `clusters`, `method`, and
  `artifact_hash`. Claim commands use `rows`, and all four accept `--raw`.
  `sessions NAME --raw` exposes its replay in `events`, while
  `sessions NAME --summary --raw` uses `summary`; every replayed event uses the
  durable `filmot.event/v1` envelope with the same outcome vocabulary.
- Raw mode affects presentation only. It does not make a read-only command log
  or make a mutating command read-only.

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
| **Named follow-up search** | `filmot search "query" --session TOPIC` |
| **Locate a phrase across sources** | `filmot library compare "claim phrase" --sort density` |
| **Raw local citations** | `filmot library compare "claim phrase" --topic TOPIC --raw` |
| **Audit shared phrasing** | `filmot library echoes TOPIC --ngram 5 --threshold 0.5` |
| **Persist echo analysis** | `filmot library echoes TOPIC --persist --raw` |
| **Search library** | `filmot library search "term"` |
| **Structured export** | `filmot library context TOPIC --format structured` |
| **Basic search** | `filmot search "query" --full --lang en` |
| **Widen ranked candidates** | `filmot search "query" --pages 3 --candidate-pool 120 --sort density` |
| **Bound result/hit output** | `filmot search "query" --limit 20 --max-hits 5` |
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
| **Add a claim** | `filmot claims add TOPIC "exact statement"` |
| **Cite claim evidence** | `filmot claims cite TOPIC CLAIM_ID --source URL --relation supports` |
| **Assess a claim** | `filmot claims assess TOPIC CLAIM_ID --verdict supported --confidence medium` |
| **Show claims** | `filmot claims show TOPIC [CLAIM_ID] --raw` |
| **Replay a session** | `filmot sessions TOPIC` |
| **Summarize a session** | `filmot sessions TOPIC --summary --raw` |
| **Search non-English** | `filmot search "人工知能" --lang ja --full` |
| **Search Hebrew** | `filmot search "בינה מלאכותית" --lang iw --full` |
| **Search Chinese (no lang)** | `filmot search "人工智能" --full` |
| **Search Hindi** | `filmot search "कृत्रिम बुद्धिमत्ता" --lang hi --full` |
| **Search Russian** | `filmot search "нейросеть" --lang ru --full` |
| **Limit search output** | `filmot search "query" --limit 20 --max-hits 5` |
| **Download channel corpus** | `filmot channel-download "Channel Name" --workers 4` |
| **Check channel status** | `filmot channel-status` |
| **Search channel corpus** | `filmot channel-search SLUG "query"` |
| **Proximity search (channel)** | `filmot channel-search SLUG '"A" NEAR/10 "B"'` |
| **Tilde proximity (channel)** | `filmot channel-search SLUG '"word1 word2"~5'` |

---

*This guide was written based on actual research sessions using Filmot CLI across topics including AI security, nuclear fusion, solid-state batteries, brain-computer interfaces, humanoid robotics, and multilingual AI discourse (Hebrew, Chinese, Hindi, Russian).*

> **Next**: Read **[AGENTS_RESEARCH_GUIDE.md](AGENTS_RESEARCH_GUIDE.md)** for the methodology behind evaluating sources, detecting AI misinformation, cross-referencing claims across languages, and reporting findings with appropriate confidence levels.
