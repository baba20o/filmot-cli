# Filmot CLI: Agent Usage Guide

**A practical guide for AI agents using Filmot CLI to research YouTube content**

*Written by an agent, for agents, based on real-world research sessions.*

> **Also read: [AGENTS_RESEARCH_GUIDE.md](AGENTS_RESEARCH_GUIDE.md)** — How to evaluate sources, detect AI misinformation, cross-reference claims, and separate truth from hype. Essential methodology for any serious research task.

---

## What This Tool Does

Filmot CLI's indexed `search` and `research` paths search **YouTube
transcripts**—the actual spoken words, not merely titles or descriptions.
Direct `yt-search` is a separate recent-upload metadata discovery path; it
attaches transcript matches only when `--transcript` is requested. `yt-video`
fetches current public metadata for known IDs. `yt-playlists` and `yt-playlist`
follow channel-curated paths without a search-ranking call, and `yt-data`
maintains the YouTube-owned fields that were saved with transcripts. The
indexed transcript search is powerful because:

1. You can find discussions that aren't in video titles
2. You get the exact context of what was said
3. You can download full transcripts for deep analysis
4. You can build curated knowledge bases and locate claim-bearing passages across sources
5. Date filtering lets you research current events in near-real-time
6. You can compare full saved transcripts for possible shared lineage
7. You can keep a strict claim/evidence register with explicit human assessments
8. You can route follow-up searches into named, resumable investigation sessions

**Think of it as:** Google for what people *say* in YouTube videos, with direct
YouTube discovery available when the transcript index has not caught up yet.

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

1. **Scout** — Queries recent YouTube uploads by relevance (10 results over the last 7 days by default), then applies an inspectable lexical admission gate. Catches breaking news that Filmot hasn't indexed yet.
2. **Accumulating staged search** — Adds unique qualified candidates from title+transcript, exact phrase, and `NEAR/N` in that order until the depth target is met or the safe ladder is exhausted; duplicates keep their first, stronger-stage origin.
3. **Safety gate** — Exact/`NEAR/N` fallbacks require at least 75% topic-token coverage in one visible passage. A nonempty underfilled safe pool never triggers loose search merely to fill slots; a high-cardinality loose fallback after a completely empty safe ladder is blocked unless `--accept-broad` is explicit.
4. **Preview and rank** — Shows relevance, density, echo risk, and a separate audience/engagement `source-prior`; balanced ranking is the default and one global rank governs selection regardless of source origin.
5. **Download and checkpoint** — Saves selected transcripts and per-item outcomes under the Unicode-safe topic name.
6. **Probe (optional)** — Extracts cross-source entities, reports co-occurrence-window and source support, and runs transparent `NEAR/N` follow-ups.

**Why this matters:** Filmot indexes transcripts ~24-48 hours after upload. For breaking news, the scout phase finds videos that Filmot can't see yet. Without it, you'd miss same-day developments entirely.

The source prior is an unverified popularity/engagement heuristic, not a credibility or truth score. Treat every automatically selected transcript as candidate material until you inspect the passage and verify important claims.

`--depth` is a maximum, not a guaranteed corpus size. When the safe ladder
underfills, Filmot keeps the qualified sources and tells you to inspect them,
refine TOPIC, or run a targeted exact/`NEAR/N` search; increasing `--depth`
alone cannot widen an exhausted pool. `--depth 0` skips Filmot ladder expansion
beyond the initial title+transcript stage and downloads no selected candidates;
an enabled scout can still join the preview. If you also pass `--probe`, that
separately requested phase can still use existing eligible library seeds.
“Qualified” here means visible lexical/coverage evidence, not a
semantic, credibility, or truth judgment.

The same separation holds at every depth: a legitimately empty current
discovery records an empty selection, then an explicit `--probe` continues from
eligible preexisting transcripts in the topic library and records its terminal
probe accounting. A fatal broad-scope safety gate is different—it still fails
closed and does not permit probe continuation.

The scout gate requires one bounded ordered topic span in the title, or two
non-overlapping spans in total across the description and/or de-duplicated
visible hit passages. It is lexical admission—not semantic relevance or source
credibility. An admitted scout competes in the same global rank, and selection
follows that order exactly; Filmot does not reserve a scout download slot or
use a hidden origin quota.

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
| `-n, --depth N` | Maximum selected transcripts to download; 0 previews the initial scope | 10 |
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
| `--scout / --no-scout` | YouTube freshness probe; admitted results join the global rank without an origin quota | On (if API key set) |
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

# Preview only the initial Filmot scope; do not expand or download selections
filmot research "your topic" --no-scout --depth 0
```

### The Probe Phase (--probe)

The `--probe` flag activates Phase 4: automatic NEAR/N discovery. After downloading transcripts, it:

1. **Bounds the seed corpus** — requires at least two selected/manual transcripts and excludes automatic scout/probe discoveries, preventing recursive frontier pollution
2. **Extracts key entities** — preserves source/sentence boundaries, filters stopwords, clusters likely ASR variants, and prefers terms supported by multiple transcripts
3. **Ranks co-occurring pairs** — scans overlapping 50-word windows (25-word stride) without crossing source or sentence boundaries; pairs need support in at least two transcripts and are ranked with source-length-normalized TF-IDF-style salience plus co-window/source support
4. **Runs scoped NEAR/N probes** — turns ranked pairs into `"entity1" NEAR/15 "entity2"` searches; it retains a title constraint when the initial title stage proved usable, otherwise it applies the displayed passage-level topic relevance filter
5. **Stops with visible budgets** — downloads at most 3 new discoveries and defers the lower-ranked query tail once that capacity is filled. If an over-threshold query's returned sample contains zero new scoped candidates, Filmot labels it `broad_sampled`, says it is not a global zero, stops the tail, and prints the exact constrained `filmot search` follow-up

**Why this is powerful:** Your initial search finds videos explicitly about your topic. The probe phase finds videos that discuss the *relationships within* your topic—angles, connections, and context your original search missed—without automatically feeding its own discoveries back into the next frontier.

An empty probe frontier is explicit. If the eligible seeds produce no
sufficiently specific terms, Filmot reports and logs `no_candidate_terms`; if
terms exist but no pair meets cross-source/co-window support, it records
`no_cross_source_pairs`. Both terminal outcomes run zero probe queries. They
describe the lexical extraction frontier, not the absence of a semantic
relationship in the source material.

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

This closes the search → download → grep loop inside the tool. Before using an
external transcript route, grep reuses one unambiguous language-compatible
library record only when its finite, monotonic timestamp segments reproduce
the complete stored text exactly. Equivalent copies across topics collapse;
distinct usable copies, language mismatches, text-only records, or invalid
timing fall back explicitly to the normal route ladder. Blank or malformed
proximity syntax fails preflight before local lookup, route setup, or network
access. `--grep` cannot be combined with `--save-to`, `--output`, `--full`,
`--timestamps`, `--chunk`, or `--fallback`.

Human matches show the readable timestamp and literal timestamped YouTube URL,
ready for `claims cite --at`. Raw grep returns `query`, `match_count`, and
stable `matches` rows containing `seconds`, `timestamp`, `deep_link`, and
`excerpt`. A clean miss is typed `empty`; malformed syntax is a typed
`InvalidGrepQuery` at `parse-query`, using the same evaluated outcome that is
logged.

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

# Materialize into a nested path; missing parents are created
filmot library context prompt-injection --output exports/prompts/context.txt

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
format's automatic save, is logged. `--output` creates missing parent
directories; parent-creation or file-write errors return the same typed
`write-output` failure, exit nonzero, and log a failed delivery.
`library echoes` never logs, even with `--persist`. Raw mode does not alter
these rules.

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
  --video VIDEO_ID --at 5:12 --relation supports \
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
not infer it from an echo cluster. `--at` requires a video and accepts finite,
non-negative seconds or the `M:SS`/`H:MM:SS` timestamps displayed by transcript
and library commands; Filmot stores canonical numeric seconds. `--video` cannot be paired with a
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
A successful append normally removes its publication temporary source. If the
event is durable but unlinking the temporary fails, the command surfaces the
durable destination and exact retained temporary without claiming rollback. If
publication and cleanup both fail, it reports both errors, names the exact
retained temporary, and says the destination was not confirmed. Claim
operations do not scan for or delete historical or unrelated `.tmp` leftovers.
Exact mutation retries are content-idempotent; recovery should inspect the
reported durable/not-confirmed outcome rather than infer state from the error
alone.
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

For every recorded scout request, the human summary prints a copyable command
equivalent to:

```bash
filmot yt-search "TOPIC" --days 7 --max-results 10 \
  --order relevance --show-description --raw
```

The actual query, days, maximum results, order, and request channel are taken
from that run rather than reconstructed from current defaults. The command
reproduces the upstream request; a multi-channel local post-filter remains
visible in raw provenance because one YouTube request cannot express it.

`research <topic>` logs a run ID, `research_start`, phase checkpoints, every
selected/downloaded item, and `research_end` with completed, failed, or
interrupted status to `<topic>.jsonl`. Search routing is explicit `--session`,
then `FILMOT_SESSION`, then the TOPIC from `--bulk-download TOPIC[:N]`, then
the current date. A search interrupted before its final outcome records one
`interrupted` event; interruption after that outcome does not duplicate it.
Partial runs therefore retain enough state to inspect completed work and resume
deliberately.

`--summary` folds one named session while keeping unlike universes separate:
standalone Filmot-search API/fetched/post-filter counts, direct YouTube search
universes, staged research-search counts after their explicit gates,
selected-download and probe outcomes, unique saved transcripts, failed
attempts and unique failed videos, and claim mutations. Direct YouTube rows
stay separate from Filmot totals and record their exact UTC bounds, order,
cap, filters, pages, fetched/returned counts, enrichment/partial state,
stopping reason, and continuation availability. A
bounded provenance view links scout gates, probe queries, and saved sources to
their discovery stage/query; older probe downloads without that field say
`query not recorded` instead of guessing. Successful manual
`transcript --save-to` events are saved-source origin `manual`. Human output
says `query not recorded (manual save)`; raw provenance uses null
`origin_query` plus `provenance_status: query_not_recorded`, never inferring a
query from adjacent activity. New successful manual-save events include
best-effort title/channel display metadata; legacy events that did not record
it remain `Unknown` rather than being inferred. Each provenance section keeps
its 25 most recent rows and reports omitted counts. Scout provenance includes the effective
request, found/gated counts, and reproduction command. Bounded probe-run outcomes retain terminal
status/reason and seed, term, executed/planned/deferred query, failure, and
saved counts—including zero-query `no_candidate_terms` and
`no_cross_source_pairs` runs. Probe-query provenance preserves operational
`broad_sampled`, `deferred`, and `failed_closed` states instead of normalizing
them into misleading completed rows, with API/returned/scoped counts retained
when recorded. It does not reinterpret those numbers as one source count.
Listing, replaying, or
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

For advanced workflows, pipe Filmot or direct YouTube discovery results into
the same download command:

```bash
# Search with raw output, pipe to download
filmot search "deep sea mining" --title "deep sea mining" --raw | filmot download -t deep-sea --dedupe

# Export a multi-page search, then feed the JSON file to download
filmot search-all "AI safety" --pages 5 --output results.json --format json
# Bash:
filmot download -t ai-safety --dedupe -n 20 < results.json
# PowerShell:
Get-Content -Raw results.json | filmot download -t ai-safety --dedupe -n 20

# Fresh YouTube discovery is accepted unchanged
filmot yt-search "fresh AI safety" --pages 2 --max-results 75 --raw \
  | filmot download -t ai-safety -n 20

# Exact-ID YouTube metadata is the same pipeline shape
filmot yt-video dQw4w9WgXcQ,aqz-KE-bpKQ --raw \
  | filmot download -t exact-sources -n 2

# A bounded curated playlist exposes a current-video candidate array
filmot yt-playlist PLAYLIST_ID --pages 1 --max-results 25 --raw \
  | filmot download -t curated-sources -n 10 --dedupe
```

The provider-neutral boundary accepts bare candidate arrays and
`result`/`videos`/`items` envelopes, normalizes Filmot and YouTube aliases, and
preflights the complete input before any transcript request or library write.
Missing, malformed, or conflicting identities reject the batch; zero-valued
counters remain observed zeroes and unknown values remain null. Unknown native
fields survive only in a bounded, credential-scrubbed `provider_fields`
mapping. The exact decoded stdin text receives a `sha256:` discovery reference.
Each successfully saved direct-YouTube row then registers the fields owned by
that observation; a registration failure keeps the transcript, marks the
aggregate partial, and leaves explicit provenance for later lifecycle adoption.
For playlist discovery, `playlist_items` preserves curator order and item-level
provenance while the top-level `videos` array contains only distinct resources
actually returned by completed metadata calls. Only `yt-playlist` has that
pipeline array; `yt-playlists` returns playlist-shelf rows and must first hand
one playlist ID to `yt-playlist`.

For one selected video, preserve the exact discovery handoff explicitly:

```bash
filmot yt-search "fresh topic" --raw > discovery.json
filmot transcript VIDEO_ID --save-to TOPIC --discovery discovery.json
```

`--discovery` selects exactly one matching ID, hashes the supplied bytes into
a `sha256:` discovery reference, and never infers metadata from adjacent
session history. New records keep the supplied metadata. On existing records,
Filmot-provider candidates use atomic fill-only enrichment. Direct-YouTube
candidates instead replace exactly the previous YouTube-owned path set: fields
omitted from the new observation are cleared, while manual or other-provider
paths are preserved and reported as conflicts. Both paths leave transcript
text, caption segments, `saved_at`, acquisition, citations, and unrelated
fields unchanged. This manual route still acquires the transcript before its
existing-record check; `yt-data refresh --video VIDEO_ID` is the metadata-only
route.

### Exact-ID metadata and saved-data maintenance

Use `yt-video` when IDs are already known. It makes only `videos.list` calls,
never acquires captions, validates/deduplicates before quota, and batches at
most 50 IDs per call:

```bash
filmot yt-video dQw4w9WgXcQ https://youtu.be/aqz-KE-bpKQ --show-description
filmot yt-video dQw4w9WgXcQ,aqz-KE-bpKQ --raw > exact-videos.json
```

Raw output is ordered by the first occurrence of each ID and contains the
credential-free request, coverage, observation/expiry timestamps, and an
`id_outcomes` row for every ID. Treat the states literally: `observed` was
returned; `not_returned` was omitted by a completed batch for an unspecified
reason; `unprocessed` never received a completed response. Earlier batches
survive a later failure. Missing counters are null and an observed zero stays
zero.

### Curated playlist discovery

Use one exact channel identity to list its public playlists, then inspect a
deliberately bounded playlist slice:

```bash
filmot yt-playlists @exact_handle --pages 1 --max-results 25 --raw \
  > playlist-shelf.json
filmot yt-playlist PLAYLIST_ID --pages 1 --max-results 25 --raw \
  > playlist-videos.json
filmot download -t curated-topic -n 10 --dedupe < playlist-videos.json
```

Both commands default to one page and 25 rows; `--pages` accepts 1–100 and
`--max-results` accepts 1–5000. A playlist-shelf row is one playlist, while a
playlist row is one ordered item—not necessarily one distinct current video.
An item can lack a usable ID, a video can appear at several positions, and a
completed `videos.list` call can omit an ID without explaining why. Preserve
those distinctions instead of treating the counts as interchangeable or
inferring deletion/privacy.

Before retries, a one-page shelf normally costs two calls (`channels.list` and
`playlists.list`); a nonempty one-page playlist normally costs three
(`playlists.list`, `playlistItems.list`, and one batched `videos.list`). Defaults
are 5/20-second connect/read timeouts and two transient retries. Raw results
expose actual `api_calls`, coverage, a stopping reason, and a copyable
`continuation.argv`; replay the opaque token only with the same identity and
bounds. All returned fields are 30-day API observations. Playlist curation is
useful discovery provenance, not proof of credibility, completeness, or source
independence.

### Saved-data maintenance

Audit and maintain direct-YouTube fields after they enter the transcript
library:

```bash
# Quiet, offline inventory
filmot yt-data status --expired --raw

# Default refresh/purge scope is expired saved copies
filmot yt-data refresh --dry-run --raw
filmot yt-data refresh
filmot yt-data purge --dry-run --raw
filmot yt-data purge --yes

# Exact IDs cover every topic copy unless --topic narrows them
filmot yt-data refresh --video dQw4w9WgXcQ,aqz-KE-bpKQ --max-videos 100

# Explicitly widen to current and unmanaged records
filmot yt-data refresh --all --max-videos 500
filmot yt-data purge --all --max-records 5000 --yes
```

`status` uses no key or network, classifies each record copy as current,
expired, or unmanaged, and surfaces invalid records separately. Refresh
fetches each unique ID once, then atomically updates each topic copy. It removes the previous exact owned-path
set before applying the returned observation, preserving unowned values.
`not_returned` clears previous owned data without inferring availability;
`unprocessed` performs no mutation and cannot extend expiry. Purge is offline,
confirmation-gated, and deletes only owned API paths—not transcripts,
segments, citations, acquisition, or manual/other-provider metadata. Dry runs
make no API calls or writes. Multi-record work is atomic per record, not as one
transaction.

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

# Or manually: preserve a reusable discovery artifact, then search Filmot depth
filmot yt-search "TOPIC" --days 3 --max-results 10 \
  --order relevance --show-description --raw > discovery.json
filmot search 'TOPIC' --start-date 2026-01-01 --end-date 2026-02-01 --full --lang en
filmot transcript VIDEO_ID --save-to TOPIC --discovery discovery.json
```

**Key insight:** Filmot indexes transcripts ~24-48 hours after upload. For same-day events, `yt-search` (YouTube Data API) finds videos that Filmot can't see yet. The `research` command's `--scout` phase handles this automatically. If you're investigating something that happened today, always start with `yt-search` or use `--scout-days 1`.

Direct `yt-search` defaults to the last 7 days, date order, 25 distinct
results, one page, and public metadata enrichment; an explicit
`--published-after` replaces that relative lower bound. The research scout uses
and records relevance order with 10. Widen explicitly with `--pages` (maximum 10) and
`--max-results` (maximum 500), or resume an opaque continuation with
`--page-token`. Each page is another search quota call. Exact RFC3339
`--published-after`/`--published-before` values freeze the time scope;
relative `--days` does not. A date-only `--published-before` includes that UTC
date by sending the next UTC midnight because the API upper bound is exclusive.
Replay a continuation token only with the exact emitted bounds and filters.

`yt-search --raw` returns one `filmot.result/v1` object with `videos`, the
credential-free effective `request`, `coverage`, and optional `enrichment`.
Treat the API total as approximate and use the recorded `next_page_token` and
`stopping_reason`, not that total, for coverage decisions. A first-page failure
fails; a later-page or optional detail failure preserves earlier discovery and
returns `partial`. Missing counters are null, not zero. Enriched metadata is a
time-stamped observation with a 30-day expiry. Exported JSON is not maintained
automatically. Direct-YouTube fields saved in the transcript library can be
inspected and explicitly refreshed or purged with `yt-data`; scheduling
remains the operator's responsibility. See [YouTube Data API
behavior](YOUTUBE_API.md).

Add `--transcript` (and optionally
`--transcript-query`) to attach the same per-video `transcript_search` object to
each video in human and raw mode: `query`, `status`, `match_count`, `matches`,
and available language/generation metadata. The transcript query is a
case-insensitive segment substring, not `NEAR/N`. One transcript failure does not
discard the YouTube discoveries; it produces a typed `transcript-search`
error, marks that video's nested result `failed`, and makes the command
`partial`.

When a search is unnecessary because the IDs are already known, use
`yt-video IDS... --raw`. It performs batched exact-ID `videos.list` reads and
returns one ordered, pipeline-compatible result with per-ID
`observed`/`not_returned`/`unprocessed` coverage.

For a curated research path, run `yt-playlists CHANNEL` with an exact channel
ID, handle, or canonical URL. Then pass one returned ID as the `PLAYLIST`
argument to `yt-playlist`. The latter returns ordered `playlist_items`, a
pipeline-safe `videos` projection enriched once per distinct ID, explicit page/result
budgets, API-call accounting, partial-failure state, and an opaque continuation
token. Resume only with the same identity and bounds. Do not infer deletion or
privacy from an ID-less or omitted item, and treat all returned YouTube
metadata as a 30-day observation. Playlist URL input is canonicalized before
it reaches result metadata, so unrelated query parameters do not become
research provenance. Python agents may use
`filmot.youtube_resources.get_playlist_detailed()` and
`list_channel_playlists_detailed()` for the same provider contracts.

### Pattern 3: Deep Discovery (Probe)
```bash
# Full pipeline: scout latest, search transcripts, auto-probe for connections
filmot research "TOPIC" --probe --depth 12 --dedupe

# Then explore what the probe found
filmot library compare "entity from probe" --topic TOPIC
filmot library search "new angle" --topic TOPIC
```

The `--probe` flag is a bounded discovery move. It mines eligible
selected/manual transcripts for entity relationships, auto-generates scoped
NEAR/N searches, and downloads up to three discoveries without recursively
using automatic scout/probe frontier records as new seeds.

### Pattern 3b: Channel Corpus Mining (Deep Knowledge Base)

Enumerate a channel's uploads playlist, download available transcripts, and
mine that local corpus offline. Private/deleted videos, unavailable captions,
malformed playlist rows, and later upstream changes can leave gaps.

```bash
# 1. Enumerate a bounded slice and download available transcripts
filmot channel-download @exact_handle --workers 4
# Equivalent exact forms: a 24-character UC... ID or canonical
# https://[www.]youtube.com/channel/UC... or
# https://[www.]youtube.com/@handle URL

# Tighten the default 10-page / 500-upload API budget
filmot channel-download @exact_handle --pages 2 --max-results 75 --workers 4

# If the result prints a continuation, follow its exact opaque token
filmot channel-download @exact_handle --page-token PAGE_TOKEN \
  --pages 2 --max-results 75 --workers 4

# 2. Check accumulated corpus status
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

`channel-download` does not resolve arbitrary display names. `/c/` and
`/user/` URLs and URLs with query/fragment components are rejected too. If you
only know a name, run `filmot channels "display name"`, inspect the returned
identity, and pass its exact `UC...` ID or `@handle`. The command records the
requested reference separately from the resolved canonical channel ID.

Enumeration uses the channel's uploads playlist, retains available playlist,
owner, publication, privacy, locale, statistics, and topic metadata, and
guards repeated page tokens. The default slice is at most 10 pages and 500
distinct uploads; `--pages` accepts 1–100 and `--max-results` accepts 1–5000.
`--limit` applies after that bounded enumeration and is not an API budget.

If a next page exists, the typed/logged outcome includes a continuation object
and human output prints a copyable `--page-token` command. A later-page failure
keeps prior rows, marks enumeration partial, continues to their transcript
downloads, and retains the retry token; the aggregate also accounts for
download failures/interruption. A first-page failure writes no new checkpoint or transcript;
an explicitly requested `--fresh` reset has already happened by then. Re-run a
slice to resume pending transcript items, or use its continuation to add the
next identities. `--fresh` cannot accompany `--page-token`.

The manifest's latest compact `filmot.youtube-upload-enumeration/v1` checkpoint records
the credential-free request, page/API/item and row-quality coverage, stopping
reason, next token, partial state, and 30-day `observed_at`/`expires_at` window.
It is an inspection/replay checkpoint, not an automatic expiry refresh/purge.
Library integrations can call `enumerate_uploads_detailed()` directly for the
same bounded envelope and a cooperative pre-page `cancel_check`.

**Proximity terms match whole words.** `"account"` will not match "accounts" or "accountability" — add explicit alternatives for inflections: `("account" | "accounts")`. Terms must be double-quoted; unquoted operands like `risk NEAR/10 position` are rejected with an error explaining the supported forms.

**When to use this over `filmot search`:**
- You want to mine a **specific channel's downloaded corpus broadly** (not just videos that match a query)
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

### Direct YouTube API failures and credentials

`yt-search`, `yt-video`, `yt-playlist`, `yt-playlists`, and `yt-data refresh`
default to a 5-second connect timeout, a 20-second read timeout, and two retries
for transient network/timeout/server/rate-limit failures. Use `--connect-timeout`,
`--read-timeout`, and `--retries` to make each request tighter. There is not yet
one wall-clock deadline across every page and detail batch. Authentication,
invalid-request, and ordinary quota errors fail without retrying. Search keeps
usable pages when a later page or optional enrichment fails; exact-ID metadata
keeps completed batches and leaves later IDs `unprocessed`.

Keep `YOUTUBE_API_KEY` in the process environment, per-user `config.env`, or
project `.env`, and restrict it in Google Cloud. Provider/transport errors,
typed outcomes, candidate extras, and session events scrub common credential
fields and URL parameters; native request/response objects that could retain a
key are not propagated. This boundary is defense in depth—never put secrets in
queries, titles, notes, or committed artifacts, and rotate a key if exposure is
suspected.

Run `filmot config` for a credential-safe preflight. It reports the Filmot and
YouTube API keys separately as `configured` or `not configured`; it never
prints either value or a fragment.

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
  segments exist. `yt-search` uses `videos` plus effective `request`,
  token/call `coverage`, and `enrichment`; later-page or optional-detail
  failures preserve video rows and make the top-level result `partial`. With
  `--transcript`, every video carries the shared `transcript_search` outcome
  and per-video failures are partial too. `transcript --grep --raw` returns stable
  seconds/timestamp/link/excerpt match rows, typed `empty` misses, and typed
  preflight parse failures. `yt-playlists` uses `channel`, `playlists`,
  `request`, `coverage`, `api_calls`, and `continuation`. `yt-playlist` adds
  `playlist`, ordered `playlist_items`, current `videos`, and per-ID outcomes;
  only that current-video projection can feed `download`. `library echoes`
  adds `clusters`, `method`, and
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
| **Preview initial research scope** | `filmot research "topic" --no-scout --depth 0` |
| **Deep discovery research** | `filmot research "topic" --probe --depth 12 --dedupe` |
| **Breaking news research** | `filmot research "topic" --scout-days 3 --depth 10` |
| **Search latest YouTube uploads** | `filmot yt-search "topic" --days 7 --pages 2 --max-results 75 --raw` |
| **Fetch exact YouTube metadata** | `filmot yt-video dQw4w9WgXcQ,aqz-KE-bpKQ --raw` |
| **List an exact channel's playlists** | `filmot yt-playlists @handle --pages 1 --max-results 25 --raw` |
| **Inspect/download a curated playlist** | `filmot yt-playlist PLAYLIST_ID --pages 1 --max-results 25 --raw` |
| **Audit saved YouTube expiry** | `filmot yt-data status --expired --raw` |
| **Refresh expired YouTube fields** | `filmot yt-data refresh --dry-run` then `filmot yt-data refresh` |
| **Purge expired YouTube fields** | `filmot yt-data purge --dry-run` then `filmot yt-data purge --yes` |
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
| **Search one transcript** | `filmot transcript VIDEO_ID --grep '"term A" NEAR/15 "term B"'` |
| **Save to library** | `filmot transcript VIDEO_ID --full --save-to TOPIC` |
| **Save with discovery metadata** | `filmot transcript VIDEO_ID --save-to TOPIC --discovery discovery.json` |
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
| **Download channel slice** | `filmot channel-download @exact_handle --pages 2 --max-results 75 --workers 4` |
| **Check channel status** | `filmot channel-status` |
| **Search channel corpus** | `filmot channel-search SLUG "query"` |
| **Proximity search (channel)** | `filmot channel-search SLUG '"A" NEAR/10 "B"'` |
| **Tilde proximity (channel)** | `filmot channel-search SLUG '"word1 word2"~5'` |

---

*This guide was written based on actual research sessions using Filmot CLI across topics including AI security, nuclear fusion, solid-state batteries, brain-computer interfaces, humanoid robotics, and multilingual AI discourse (Hebrew, Chinese, Hindi, Russian).*

> **Next**: Read **[AGENTS_RESEARCH_GUIDE.md](AGENTS_RESEARCH_GUIDE.md)** for the methodology behind evaluating sources, detecting AI misinformation, cross-referencing claims across languages, and reporting findings with appropriate confidence levels.
