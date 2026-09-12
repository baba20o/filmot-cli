# Filmot CLI — Agent Experience Feature Plan

> **Status:** Historical implementation plan plus the current follow-on batch.
> The original work was later split across `filmot/commands/` modules; file
> lists below name the current owners rather than the former monolithic CLI.
> README command contracts and the test suite are authoritative. Do not use a
> hard-coded historical test count as a release signal.

## Tier 1: Fundamental Workflow Changes

### 1. `filmot research "topic"` — Compound Research Command

**What it does:** Single command that orchestrates: search → filter → bulk download → summary.

**Implementation:**
- Research command in `filmot/commands/research.py`
- Options: `--depth N` (maximum selected transcripts, default 10), `--min-views N`, `--lang`, `--fallback`
- Workflow:
  1. Run freshness scouting plus staged title+transcript, exact-phrase, and
     proximity searches, accumulating unique relationship-stage candidates
     until the maximum depth target is reached or the ladder is exhausted
  2. Apply field-aware lexical admission to scout rows, then rank every admitted
     candidate globally with separate relevance, density, echo-risk, and
     audience/engagement signals; none is a semantic or credibility score
  3. Preserve the strongest (earliest) stage provenance for duplicates and
     download at most N transcripts to the normalized topic library. A nonempty
     underfilled relationship pool skips loose fallback and reports a targeted
     exact/near-search next action; only an empty pool can enter loose fallback.
     Depth zero keeps the Filmot ladder at the title+transcript preview and
     downloads no selected candidates. An enabled scout may still join that
     preview, and an explicit probe may use existing eligible library seeds.
     At any depth, a legitimately empty current discovery records an empty
     selection; an explicit probe still continues from eligible preexisting
     topic-library seeds and emits terminal accounting. Fatal broad-scope
     safety gates continue to fail closed.
  4. Print summary: X saved, Y skipped, Z failed, total chars, sources list
- Uses existing `FilmotClient.search_subtitles()` and `_bulk_download_transcripts()` logic

**Current owner:** `filmot/commands/research.py`

---

### 2. `--min-matches N` Filter on Search

**What it does:** Client-side filter that drops results with fewer than N subtitle matches. The API already returns hit counts per video.

**Implementation:**
- Add `--min-matches` option to `search` command
- After API returns results, filter `videos` list where `len(video.get("hits", [])) >= min_matches`
- Update total count display to reflect filtered count
- Also add to `search-all` command

**Current owner:** `filmot/commands/search.py`

---

### 3. Word-Boundary Library Search

**What it does:** Library search currently uses substring matching (`query in text`). This causes false positives: searching "ore" matches "more", "before", etc. Switch to word-boundary regex.

**Implementation:**
- Modify `TranscriptLibrary.search()` in `library.py`
- Match the original transcript with an escaped Unicode `re.IGNORECASE` pattern
  and word-boundary lookarounds, preserving source-relative character offsets
- Use the same original-text pattern in excerpt/segment timestamp mapping;
  lowercasing first can expand Unicode characters and shift citations
- Add `--substring` flag to CLI `library search` for backwards-compatible substring matching

**Current owners:** `filmot/library.py`, `filmot/commands/library.py`

---

## Tier 2: Significant Time Savings

### 4. Deduplication (`--dedupe`)

**What it does:** Detect and skip transcripts with the same first-500-character
fingerprint during bulk download. This exact dedupe mechanism is distinct from
the full-transcript similarity analysis performed by `library echoes`.

**Implementation:**
- Add `--dedupe` flag to `search` command (affects bulk download)
- In `_bulk_download_transcripts()`, after downloading transcript text:
  - Hash first 500 chars of transcript
  - If hash matches an already-downloaded transcript, skip it
  - Also check against existing library entries in the topic
- Report deduplicated count in summary

**Current owners:** `filmot/commands/search.py`, `filmot/library.py`

---

### 5. Relevance Density Scoring

**What it does:** Calculate and display `matches_per_minute` for each search
result. A 5-minute video with 12 literal matches has greater lexical match
concentration than a 3-hour video with 4; that does not establish semantic
relevance, source credibility, or truth.

**Implementation:**
- In `_display_subtitle_results()`, compute density: `len(hits) / (duration / 60)` where duration > 0
- Display as `Density: 2.4/min` after the matches count
- Add `--sort density` option (client-side sort after API returns)

**Current owner:** `filmot/commands/search.py`

---

### 6. `filmot library compare "term"` — Cross-Source Concordance

**What it does:** Search for a term across library transcripts and present the
matching passages by source. This is lexical navigation: it does not infer
stance, agreement, contradiction, independence, credibility, or truth.

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
- Sort by mention count (most lexical occurrences of the query)

**Current owners:** `filmot/commands/library.py`, `filmot/library.py`

---

## Tier 3: Quality of Life

### 7. Pipeline/Stdin Mode

**What it does:** Accept discovery results from stdin to feed into bulk
download. This works for both indexed search and curated-playlist output.

```bash
filmot search ... --raw | filmot download --topic "mining"
filmot yt-playlist PLAYLIST --raw | filmot download --topic "curated"
```

**Implementation:**
- `download` reads a piped raw discovery value from stdin
- Reads JSON from stdin through the shared provider-neutral candidate boundary
- Passes to existing `_bulk_download_transcripts()` logic
- Options: `--topic` (required), `--count N`, `--fallback`, `--dedupe`

**Current owner:** `filmot/commands/transcript.py`

---

### 8. Structured Library Context (`--format`)

**What it does:** `filmot library context` outputs plain text or structured
markdown with metadata headers. File output supports nested destination paths.

**Implementation:**
- `--format` choices are `text` (default) and `structured`
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
- `get_context_structured()` on `TranscriptLibrary` builds the markdown
- File output creates missing parent directories inside the same typed write
  boundary as publication, so parent-creation failures are normal command
  failures rather than uncaught filesystem exceptions

**Current owners:** `filmot/library.py`, `filmot/commands/library.py`

---

### 9. `--title` Operator Support

**What it does:** Document the supported phrase, grouped OR, and implicit-AND
forms in the `--title` parameter.

**Implementation:**
- Test: `filmot search "mining" --title 'deep sea (mining | extraction)'`
- Document that spaces express implicit AND and the literal word `AND` should
  not be used as an operator.
- No code changes are needed for API-side syntax.

**Files modified:** Possibly just `README.md`

---

### 10. Transcript Speaker Labels (Best-Effort)

**What it does:** When manual subtitles include speaker labels (e.g., `[Speaker 1]: ...`), preserve them in transcript output.

**Implementation:**
- Check if `youtube-transcript-api` returns speaker info in manual subtitle tracks
- If available, include speaker field in segments
- Likely limited: auto-captions never have speaker labels, manual captions sometimes do
- Add `--manual-subs` note in transcript command help explaining this

**Current owner:** `filmot/transcript.py` (if upstream data becomes available)

---

## Current Follow-On Batch: Durable Provenance and Low-Load Research

### Bounded scout admission and probe expansion

- Freshness-scout admission requires one bounded ordered topic span in the
  title or two non-overlapping, deduplicated spans in description/hit evidence.
  This is an inspectable lexical safety gate, not a semantic relevance or truth
  judgment; its `scout-lexical-spans` counters appear only on scout rows.
- Scout does not own a reserved quota. Filmot and admitted scout candidates use
  one displayed global ranking, and `--depth` selects its highest rows.
- Automatic probes seed only from manual or staged-selected transcripts;
  scout/probe frontier records cannot recursively seed another probe run.
- Probe-pair priority uses language-neutral, source-normalized salience with
  cross-source support. API work stops after three discoveries, or after a
  high-cardinality returned sample contains no lexically coherent new
  candidate. The latter is recorded as `broad_sampled`, lower-ranked queries
  are `deferred`, and the output reports sample coverage rather than claiming a
  global zero.
- The manual broad-sample follow-up is shell-quoted from an argument vector and
  preserves the effective language, usable title constraint, and channel IDs.
- Relationship stages accumulate one unique pool until the maximum depth target
  is met or exhausted. Duplicates retain their strongest stage origin;
  nonempty underfill does not trigger loose fallback and records the exact
  target/qualified/remaining counts plus a targeted exact/near-search recovery.
  Depth zero keeps the Filmot ladder at the title+transcript preview and
  downloads no selected candidates; an enabled scout may still join that
  preview, and an explicit probe may use existing eligible library seeds.
- At any depth, a legitimately empty current discovery records an empty
  selection and an explicit probe continues from eligible preexisting
  topic-library seeds, including terminal zero-query accounting. Fatal
  broad-scope safety gates still fail closed rather than entering probe work.
- With adequate seeds but no usable terms or no supported cross-source pair,
  probe planning records an explicit zero-query terminal outcome instead of an
  ambiguous success.

**Current owner:** `filmot/commands/research.py`

### Reproducible freshness requests and raw transcript inspection

- Research checkpoints retain scout days, `order=relevance`, result limit, and
  the actual YouTube request-channel filter separately from later post-filter
  scope. Session summaries render a copyable `yt-search --raw` inspection
  command when those values are known and do not guess legacy defaults.
- `yt-search --raw` exposes the effective request and exact candidate metadata.
  `--transcript` is evaluated before either renderer: every video receives a
  typed `transcript_search` object, and item failures produce an aggregate
  `partial` result while preserving successful discovery metadata.

**Current owners:** `filmot/commands/search.py`, `filmot/commands/library.py`,
`filmot/ledger.py`

### Bounded curated-playlist discovery and handoff

- `yt-playlists` resolves one exact channel ID/handle/URL and enumerates a
  bounded public-playlist shelf; `yt-playlist` retains an ordered bounded item
  slice and enriches each distinct usable video ID once.
- Both commands default to one page/25 rows, accept independent page and row
  budgets plus opaque continuation tokens, expose actual API-call accounting,
  and return typed empty/partial/completed results. Human and raw continuation
  forms preserve the same controls.
- Only `yt-playlist --raw` is a download-candidate envelope. Its top-level
  `videos` contains resources returned by completed metadata calls, while
  `playlist_items` preserves repeated and ID-less curator evidence.
- A 2026-09-12 live active-inference run enumerated 20 channel playlists in two
  calls, read ten items/videos in three calls, and passed the unchanged raw
  result through `download`; saved sources retained content-addressed artifact
  and playlist-position provenance.
- `filmot config` now distinguishes Filmot and YouTube credential readiness
  without revealing either key.

**Current owners:** `filmot/commands/youtube.py`,
`filmot/youtube_resources.py`, `filmot/discovery.py`

### Bounded public comment and reply inspection

- `yt-comments VIDEO` reads one exact video's `commentThreads.list` cursor;
  `yt-replies TOP_LEVEL_COMMENT_ID [--video VIDEO]` reads the separate
  `comments.list(parentId=...)` cursor. The reply identity is the nested
  `top_level_comment.comment_id`, never the outer thread ID, and embedded
  replies remain an explicitly covered preview rather than an automatic
  fan-out.
- Both commands default to one page/25 rows and 5/20-second connect/read
  timeouts with two retries. They accept `--pages` 1–10,
  `--max-results`/`-n` 1–500, `--page-token`, positive finite timeouts,
  `--retries` 0–5, and `--raw`. Threads additionally accept
  `--order time|relevance`, 1–500-character `--search`/`--search-terms`, and
  `--replies none|preview`; replies accept optional exact `--video` context.
- Raw thread data exposes `provider`, `video_id`, `comment_threads`,
  `replies_mode`, `availability`, `request`, `coverage`, `api_calls`, `quota`,
  `continuation`, `observed_at`, and `expires_at`; replies substitute
  `parent_comment_id`, nullable `video_id`, and `replies`. `_filmot` carries the
  `filmot.result/v1` status/errors/warnings. Typed outcomes distinguish
  completed, empty, first-page disabled/skipped, later-page partial, and
  first-page failed retrieval. Continuations preserve the matching token kind
  and argument vector as well as the active named session; the human reply
  drill-down preserves it too.
- Every `commentThreads.list` or `comments.list` HTTP attempt is conservatively
  estimated at one quota unit, including retries. The output keeps actual
  endpoint attempt counts; it does not claim to know project quota balance.
- Discussion results deliberately lack pipeline candidate keys and never enter
  the transcript library or `yt-data` lifecycle. Raw exports carry a 30-day
  refresh/delete deadline. Compact session events retain invocation controls,
  presence booleans, coarse safe diagnostics, and call/quota telemetry, with
  `transient_result_persisted=false`; they omit all response rows, identities,
  counts, coverage, availability, continuation state, search text, and tokens.
- Comments are mutable, self-selected, untrusted discourse—not corroboration,
  consensus, authority, or a representative audience measure. The feature does
  not compute sentiment, profile authors or infer sensitive traits, or create
  derived engagement metrics. Human display neutralizes bidi/Unicode format
  and terminal controls, collapses metadata newlines, and contains text in
  padded blocks without altering raw rows.
- Generic bounded-control, credential-detached error, page-info, request-option,
  and next-token handling now lives in `filmot.youtube_api_support`; playlist
  and comment providers share it while retaining endpoint-specific parsing and
  policy decisions.

Deterministic provider/CLI implementation and a bounded live API-key thread,
reply, and emitted-continuation drive are complete. See the exact nested raw
schemas in
[AGENTS_README.md](AGENTS_README.md#transient-public-comment-and-reply-inspection).
Future endpoint priorities, OAuth-only surfaces, and policy gates are maintained
in [YOUTUBE_ROADMAP.md](YOUTUBE_ROADMAP.md), not duplicated here.

**Current owners:** `filmot/commands/youtube_comments.py`,
`filmot/youtube_comments.py`, `filmot/youtube_api_support.py`,
`filmot/youtube_resources.py`, `filmot/schemas.py`

### Named search routing and session summaries

- `filmot search --session NAME` routes the search activity event to a named
  session; Ctrl-C before the final outcome leaves one `interrupted` breadcrumb.
- Routing precedence is CLI `--session`, `FILMOT_SESSION`, bulk-download TOPIC,
  then the current date.
- `filmot sessions NAME --summary [--raw]` derives separate standalone-search
  and staged research-search universes, unique transcript saves/failures,
  selected-download and probe outcomes, compact claim mutations, bounded
  scout/probe provenance, and per-source discovery stage/query. Legacy probe
  links remain explicit unknowns. Operational probe states
  (`broad_sampled`, `deferred`, and `failed_closed`) survive canonical event
  normalization, and legacy selected-source stage `title_transcript` joins the
  canonical `title+transcript` search query. It does not collapse unlike counts
  into one source total.
- Bounded probe-run outcome rows make skipped, empty, partial, and completed
  runs resumable and count older omitted rows. Completed transcript saves that
  lack staged selection provenance appear as `manual` /
  `query_not_recorded`, without inferring a query; matching research or probe
  provenance takes precedence over that fallback. New successful manual-save
  events carry best-effort title/channel display metadata; legacy manual events
  without recorded metadata remain `Unknown` rather than being reconstructed.
- Session list, replay, and summary are read-only and never log themselves.
  Named replay/summary surfaces malformed lines as partial/failed read errors
  instead of silently undercounting them.

**Current owners:** `filmot/commands/search.py`, `filmot/commands/library.py`,
`filmot/ledger.py`, `filmot/schemas.py`

### Strict claims and evidence

- `claims add`, `cite`, `assess`, and `show` all support `--raw`.
- Evidence relations are `supports`, `contradicts`, `qualifies`, `context`,
  `origin`, and `mentions`.
- Video IDs and finite non-negative timestamps are valid only for evidence
  classified as source kind `video`; `--at` accepts numeric seconds and
  displayed `M:SS`/`H:MM:SS` forms before canonicalizing to numeric seconds.
  `--source` and `--video` are mutually
  exclusive and each evidence event names one source. `--video` requires an
  exact 11-character `[A-Za-z0-9_-]{11}` YouTube ID rather than a URL and is
  validated before persistence.
- Default IDs record `utf8-ascii-whitespace/v1`, preserving exact UTF-8
  code points/case and avoiding runtime Unicode-database drift.
- Evidence and assessment events record `canonical-json-array/v2`; evidence
  IDs cover every persisted identity/provenance input rather than an informal
  generic content hash, and reads verify both IDs and the assessment
  supersedes chain rather than accepting forks or dangling predecessors.
- Human verdicts are `open`, `supported`, `contradicted`, and `mixed`;
  confidence is `unknown`, `low`, `medium`, or `high`.
- Claim events are strict append-only JSON files under
  `.filmot_data/claims/TOPIC/`. Persistence failures are command failures;
  per-topic OS locks serialize complete read/check/append transactions, and
  events are schema-validated before publication.
- Successful publication normally removes the new event's private temporary
  source. An unlink failure reports the durable destination and exact retained
  temporary without implying rollback. Publication plus cleanup failure reports
  both errors, names the exact retained temporary, and says the destination was
  not confirmed.
- Claim operations do not scan or delete historical/unrelated temporaries.
  Exact mutation retries are content-idempotent; recovery inspects the reported
  durable/not-confirmed outcome rather than guessing.
- Newly written `filmot.claim/v2` events have contiguous per-topic sequences.
  Sequence-less `filmot.claim/v1` histories are replayed in memory without
  rewriting their files and remain appendable only through newly added v2
  events.
- Mutation logs contain compact identifiers/classifications, not claim text or
  source excerpts. `claims show` is read-only and does not log.

**Current owners:** `filmot/claims.py`, `filmot/commands/claims.py`,
`filmot/schemas.py`

### Segment-aware transcript records and local citations

- New library records preserve available timestamped source-caption segments
  alongside complete text and normalized source/acquisition metadata.
- Legacy records are normalized to the current shape in memory, with an empty
  segment list when none was stored; reads do not rewrite them.
- Saves validate records, write strict JSON to a same-directory temporary,
  flush and `fsync`, then atomically replace the destination; a pre-replace
  failure preserves the existing record.
- `library search --raw` and `library compare --raw` expose citation-ready
  excerpt details, including timestamp and deep link when stored segments make
  those values knowable.
- `transcript --grep` filters local copies by requested language and validated,
  monotonic segment/text alignment, collapses equivalent records, and reuses a
  single content variant before configuring external routes. Distinct variants
  or incompatible records retain normal fetch behavior with a diagnostic.
- Any supplied grep query, including blank text, is parsed before local lookup,
  proxy setup, or retrieval. Invalid proximity syntax returns a typed
  `InvalidGrepQuery` parse failure and never becomes a full transcript fetch.
- Human and raw grep share one evaluator. Human matches print literal
  timestamped YouTube URLs; raw rows expose seconds, display timestamp, deep
  link, and excerpt, with typed empty/failure states. A unique local hit routes
  its compact activity to the saved source topic.
- Presentation chunks from `transcript --chunk` remain distinct from stored
  source-caption segments. When combined with `--timestamps`, chunking takes
  precedence for terminal/plain-text presentation without replacing the
  segments retained in raw/JSON output or library storage.

**Current owners:** `filmot/library.py`, `filmot/commands/transcript.py`,
`filmot/commands/search.py`, `filmot/commands/research.py`

### Reproducible echo analysis and read-only inspection

- `library echoes TOPIC` compares full saved transcripts with a deterministic,
  Unicode-aware word n-gram Jaccard method (`--ngram 5`, `--threshold 0.5`).
  Method v2 pins extended Han, Kana, Bopomofo, and Hangul tokenization ranges
  and records the runtime Unicode database used for normalization/case folding.
- Clusters are advisory reuse/common-lineage candidates, not proof of copying,
  dependence, credibility, falsity, or truth.
- `--persist` writes a content-addressed, non-overwriting artifact at
  `.filmot_data/analysis/TOPIC/echoes-HASH.json`; the stored content is hashed
  canonically and existing artifacts are verified before reuse. Echo analysis
  never logs.
- Corpus loading for echo analysis is strict: one unreadable, malformed, or
  incomplete transcript record fails the analysis instead of shrinking it.
- Pair scoring derives union cardinality arithmetically and retains only the
  five smallest representative shingles, avoiding redundant large allocations.
- Human rendering caps the table at the 25 strongest matching pairs; raw JSON
  and persisted artifacts preserve the complete pair set.
- `library list`, `search`, `compare`, and `stats`, plus stdout-only `context`,
  are read-only and do not log. Context file writes log.
- `library list`, `search`, and `compare` support `--raw`; raw mode does not
  change persistence semantics.
- The shared raw boundary rejects non-standard numeric values and emits Unicode
  through ASCII JSON escapes; `main.py` propagates the returned CLI status via
  `SystemExit`.

**Current owners:** `filmot/analysis.py`, `filmot/commands/library.py`,
`filmot/library.py`, `filmot/schemas.py`

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
| 8 | Pipeline/stdin | DONE | `filmot download` reads a piped raw discovery value |
| 9 | `--title` operators | DONE | Confirmed working, documented in README |
| 10 | Speaker labels | NOT POSSIBLE | youtube-transcript-api only provides text/start/duration |
| 11 | Public comment/reply cursors | DONE (LIVE VERIFIED) | Bounded thread, reply, exact-session continuation, and minimal-ledger privacy paths passed live; the complete drive is recorded in `FIELD_TEST_LOG.md` |
| 12 | Shared YouTube API support | DONE | Playlist/comment controls, safe errors, page info, and tokens share one internal helper |

The suite has expanded substantially since this plan was written. Run the
current test suite rather than relying on a frozen test count. Verification
snapshot for this field-tested follow-on batch: all 584 tests passed on
2026-08-30; that number records the batch audit and is not a release gate.

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

## Channel Corpus Features (from corpus mining sessions)

| # | Feature | Status | Notes |
|---|---------|--------|-------|
| K | `channel-download` command | DONE | Download all transcripts from a YouTube channel. Parallel workers, resume/delta, proxy + `--no-proxy` bypass. |
| L | `channel-status` command | DONE | List all downloaded channels or detailed stats for one. |
| M | `channel-search` command | DONE | Search across local channel corpus. Plain text, NEAR/N proximity, tilde `~N` proximity. |
| N | Parallel download (`--workers`) | DONE | ThreadPoolExecutor with thread-safe manifest saves. 3x speedup at 4 workers. |
| O | `--no-proxy` flag | DONE | Bypass Webshare proxy for direct connections. |
| P | Proximity search in `channel-search` | DONE | `_parse_proximity_query()` parses NEAR/N and `"words"~N`. `_find_near_matches()` / `_find_tilde_matches()` do word-distance matching. |
| Q | `yt-playlists` command | DONE | Bounded exact-channel public shelf with API accounting and copyable continuation. |
| R | `yt-playlist` command | DONE | Ordered playlist evidence plus a pipeline-safe current-video projection. |
