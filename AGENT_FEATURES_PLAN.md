# Filmot CLI — Agent Experience Feature Plan

## Tier 1: Fundamental Workflow Changes

### 1. `filmot research "topic"` — Compound Research Command

**What it does:** Single command that orchestrates: search → filter → bulk download → summary.

**Implementation:**
- New CLI command `research` in `cli.py`
- Options: `--depth N` (number of transcripts, default 10), `--min-views N`, `--lang`, `--fallback`
- Workflow:
  1. Search with `--title` matching the topic + content matching the topic
  2. Sort by views (most credible/popular first)
  3. Download top N transcripts to library under normalized topic name
  4. Print summary: X saved, Y skipped, Z failed, total chars, sources list
- Uses existing `FilmotClient.search_subtitles()` and `_bulk_download_transcripts()` logic

**Files modified:** `filmot/cli.py`

---

### 2. `--min-matches N` Filter on Search

**What it does:** Client-side filter that drops results with fewer than N subtitle matches. The API already returns hit counts per video.

**Implementation:**
- Add `--min-matches` option to `search` command
- After API returns results, filter `videos` list where `len(video.get("hits", [])) >= min_matches`
- Update total count display to reflect filtered count
- Also add to `search-all` command

**Files modified:** `filmot/cli.py`

---

### 3. Word-Boundary Library Search

**What it does:** Library search currently uses substring matching (`query in text`). This causes false positives: searching "ore" matches "more", "before", etc. Switch to word-boundary regex.

**Implementation:**
- Modify `TranscriptLibrary.search()` in `library.py`
- Change `query_lower in transcript.lower()` to `re.search(r'\b' + re.escape(query_lower) + r'\b', transcript.lower())`
- Same change in `_find_matches()` for finding match positions
- Add `--substring` flag to CLI `library search` for backwards-compatible substring matching

**Files modified:** `filmot/library.py`, `filmot/cli.py`

---

## Tier 2: Significant Time Savings

### 4. Deduplication (`--dedupe`)

**What it does:** Detect and skip duplicate/near-duplicate transcripts during bulk download. Many YouTube channels repackage the same content into compilation videos.

**Implementation:**
- Add `--dedupe` flag to `search` command (affects bulk download)
- In `_bulk_download_transcripts()`, after downloading transcript text:
  - Hash first 500 chars of transcript
  - If hash matches an already-downloaded transcript, skip it
  - Also check against existing library entries in the topic
- Report deduplicated count in summary

**Files modified:** `filmot/cli.py`

---

### 5. Relevance Density Scoring

**What it does:** Calculate and display `matches_per_minute` for each search result. A 5-minute video with 12 matches is more relevant than a 3-hour video with 4 matches.

**Implementation:**
- In `_display_subtitle_results()`, compute density: `len(hits) / (duration / 60)` where duration > 0
- Display as `Density: 2.4/min` after the matches count
- Add `--sort density` option (client-side sort after API returns)

**Files modified:** `filmot/cli.py`

---

### 6. `filmot library compare "claim"` — Cross-Source Verification

**What it does:** Search for a term across library transcripts and present results in a structured comparison format, showing each source's treatment of the claim.

**Implementation:**
- New subcommand under `library` group: `library compare QUERY`
- Options: `--topic` (limit to topic), `--context N` (chars around match, default 150)
- Output format per source:
  ```
  Source 1: "Video Title" (Channel)
    [3 mentions] "...context around first match..."
  Source 2: "Another Video" (Other Channel)
    [1 mention] "...context around match..."
  ```
- Sort by mention count (most mentions = most relevant to claim)

**Files modified:** `filmot/cli.py` (uses existing `TranscriptLibrary.search()`)

---

## Tier 3: Quality of Life

### 7. Pipeline/Stdin Mode

**What it does:** Accept search results from stdin to feed into bulk download, enabling: `filmot search ... --raw | filmot download --stdin --topic "mining"`

**Implementation:**
- New command `download` with `--stdin` flag
- Reads JSON from stdin, expects same format as search `--raw` output
- Passes to existing `_bulk_download_transcripts()` logic
- Options: `--topic` (required), `--count N`, `--fallback`, `--dedupe`

**Files modified:** `filmot/cli.py`

---

### 8. Structured Library Context (`--format`)

**What it does:** `filmot library context` currently dumps raw text. Add a `--format structured` option that outputs clean markdown with metadata headers.

**Implementation:**
- Add `--format` option to `library context` command: choices `text` (default), `structured`
- `structured` format outputs:
  ```markdown
  # Topic: deep-sea-mining
  ## Video 1: "The Truth about Deep Sea Mining" by Real Engineering
  - Video ID: 73mXXJpEjRI
  - Duration: 15m 32s | Views: 1,874,577
  - Saved: 2026-02-11

  [transcript text]

  ---
  ## Video 2: ...
  ```
- Add new method `get_context_structured()` to `TranscriptLibrary`

**Files modified:** `filmot/library.py`, `filmot/cli.py`

---

### 9. `--title` Operator Support (Test & Document)

**What it does:** Test whether the Filmot API already supports Manticore operators in the `--title` parameter. If yes, document it. If no, note the limitation.

**Implementation:**
- Test: `filmot search "mining" --title "deep sea AND (mining | extraction)"`
- If operators work: update README with examples
- If not: document that `--title` is literal-only
- No code changes needed if API-side

**Files modified:** Possibly just `README.md`

---

### 10. Transcript Speaker Labels (Best-Effort)

**What it does:** When manual subtitles include speaker labels (e.g., `[Speaker 1]: ...`), preserve them in transcript output.

**Implementation:**
- Check if `youtube-transcript-api` returns speaker info in manual subtitle tracks
- If available, include speaker field in segments
- Likely limited: auto-captions never have speaker labels, manual captions sometimes do
- Add `--manual-subs` note in transcript command help explaining this

**Files modified:** `filmot/transcript.py` (if data available), `README.md`

---

## Implementation Status

| # | Feature | Status | Notes |
|---|---------|--------|-------|
| 1 | `--min-matches` filter | DONE | Client-side filter on search results |
| 2 | Word-boundary library search | DONE | Default behavior, `--substring` for old behavior |
| 3 | Density scoring | DONE | Shows matches/min in search results |
| 4 | `library compare` | DONE | Cross-source comparison subcommand |
| 5 | `filmot research` | DONE | Single compound command for full workflow |
| 6 | Deduplication | DONE | `--dedupe` flag on search and research commands |
| 7 | Structured context | DONE | `--format structured` outputs markdown |
| 8 | Pipeline/stdin | DONE | `filmot download --stdin` reads from pipe |
| 9 | `--title` operators | DONE | Confirmed working, documented in README |
| 10 | Speaker labels | NOT POSSIBLE | youtube-transcript-api only provides text/start/duration |

All 77 existing tests pass after changes.

## Post-Review Fixes (from BCI research session)

| # | Fix | Status | Notes |
|---|-----|--------|-------|
| A | Backfill Unknown metadata | DONE | `_backfill_metadata()` uses `get_videos()` API when title/channel is "Unknown" |
| B | `--sort density` | DONE | Client-side sort by matches/min, excludes `density` from API sort param |
| C | `--min-matches` on `research` | DONE | Filter applied before download loop |
| D | `library compare` default context 300 chars | DONE | Was 150, now 300 for full-sentence context |

## Post-Review Fixes (from solid-state-battery session)

| # | Fix | Status | Notes |
|---|-----|--------|-------|
| E | `library compare` shows mentions/min density | DONE | Uses saved `duration_seconds` metadata per source |
| F | `--sort density` on `research` command | DONE | Skips API sort, does client-side matches/min sort |
| G | Structured context auto-saves to `{topic}-context.md` | DONE | No more dumping 100KB+ to stdout |

## Post-Review Fixes (from nuclear-fusion-energy session)

| # | Fix | Status | Notes |
|---|-----|--------|-------|
| H | Auto-fallback to substring on zero word-boundary results | DONE | Catches plurals/inflections (e.g., "patent" finds "patents") |
| I | Context overlap dedup in `_find_matches` | DONE | `min_gap` param skips matches within previous context window |
| J | `--sort density` on `library compare` | DONE | Sort by mentions/min instead of raw count |
