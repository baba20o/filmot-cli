# Changelog

All notable changes to Filmot CLI are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Named search-session routing through `--session` and `FILMOT_SESSION`, with
  bulk-download TOPIC inference and an explicit CLI → environment → bulk TOPIC
  → date precedence.
- `sessions NAME --summary` for separate standalone-search and staged
  research-search universes plus transcript, failure, final research, and
  claim-mutation totals.
- A strict append-only claim/evidence register under
  `.filmot_data/claims/TOPIC/*.json`, with `claims add`, `cite`, `assess`, and
  `show`; every subcommand supports versioned raw JSON.
- Runtime-independent derived claim IDs using exact UTF-8 code points with only
  ASCII whitespace folding; declarations record the ID method, while explicit
  IDs are marked separately.
- Unambiguous `canonical-json-array/v2` IDs for evidence and assessments. v2
  evidence identity covers every persisted identity/provenance input, with
  strict ID and linear supersedes-chain validation on read.
- `filmot.claim/v2` events with contiguous per-topic sequence numbers; valid
  sequence-less `filmot.claim/v1` histories remain readable and can be
  continued with newly appended v2 events without rewriting legacy files.
- Explicit evidence relations (`supports`, `contradicts`, `qualifies`,
  `context`, `origin`, `mentions`) and human verdict/confidence vocabularies.
- Raw JSON for `library list`, `library search`, and `library compare`, including
  citation-ready local excerpt details and timestamped deep links when saved
  caption segments are available.
- `yt-search --raw` for the effective discovery request and exact candidate
  metadata. With `--transcript`, each video carries a typed transcript-search
  outcome and item failures make the aggregate result partial.
- Bounded direct-YouTube pagination through `yt-search --pages`, a distinct
  `--max-results` budget, opaque `--page-token` continuation, exact RFC3339
  publication bounds, optional `--no-enrich`, paid-placement filtering, and
  configurable per-request timeouts/retries. Raw output now carries effective
  request, call/token coverage, stopping reason, and enrichment accounting.
- Rich `videos.list` observations for public snippet/statistics,
  content/status, live-stream, topic, regional/content-rating,
  made-for-kids, synthetic-media, and `paid_product_placement` metadata, with missing
  counters preserved as unknown and 30-day UTC observation/expiry timestamps.
- `yt-video` for ordered exact-ID/URL `videos.list` lookups without caption
  acquisition. It validates and deduplicates inputs before quota use, batches
  by 50, emits pipeline-compatible raw output, and distinguishes returned,
  neutrally omitted, and unprocessed IDs while preserving completed batches.
- `yt-data status`, `refresh`, and `purge` for explicit saved-YouTube metadata
  maintenance. Status and purge are offline; refresh defaults to expired
  records, fetches each unique ID once across topic copies, supports dry-run
  and quota budgets, and never acquires captions.
- A durable `filmot.youtube-metadata/v1` lifecycle with exact JSON Pointer
  ownership, 30-day observation windows, optional content-addressed request
  references, neutral `not_returned` state, bounded value-free audit history,
  explicit legacy adoption, and guarded atomic per-record replacement/purge.
- `filmot.channel_dl.enumerate_uploads_detailed()` for bounded, resumable
  uploads-playlist traversal with page/item budgets, cooperative cancellation,
  continuation and stopping state, API-call/row-quality coverage, and partial
  preservation after a later-page failure.
- `filmot.youtube_resources` provider APIs for bounded public-playlist and
  channel-playlist-shelf inspection. They retain playlist-item order and
  provenance, enrich each distinct returned video ID once, preserve usable
  earlier pages after later failures, expose opaque continuation tokens, and
  canonicalize supplied playlist URLs before recording request metadata.
- `yt-playlists` and `yt-playlist` for bounded channel-shelf and curated-playlist
  inspection. Both expose independent page/row budgets, actual API-call
  accounting, typed partial/empty states, and copyable continuations;
  `yt-playlist --raw` adds an ordered item ledger and a pipeline-compatible
  current-video projection.
- A provider-neutral discovery candidate contract for Filmot, direct YouTube,
  and bare/list/result/videos/items artifacts. It provides all-row preflight,
  strict YouTube identities, zero-vs-missing preservation, bounded sanitized
  native fields, and aggregate validation errors before pipeline mutation.
- `transcript --save-to TOPIC --discovery FILE` for selecting the exact
  matching discovery row, recording a content-addressed `sha256:` reference,
  and retaining supplied metadata without guessing from neighboring events.
- Atomic fill-only metadata enrichment for existing library records. It
  preserves known values and transcript/citation/acquisition content, reports
  conflicts, and makes repeated enrichment a no-op.
- A separately bounded direct-YouTube section in session summaries, including
  absolute UTC scope, filters, page/result counts, stopping/partial/enrichment
  state, and continuation availability without adding it to Filmot totals.
- `library echoes TOPIC` for deterministic, advisory full-transcript word
  n-gram Jaccard analysis. `--persist` writes and verifies a canonical,
  content-addressed artifact in `.filmot_data/analysis/TOPIC/`; method metadata
  records the runtime Unicode database version. Method v2 pins extended Han,
  Kana, Bopomofo, and Hangul tokenization ranges.
- Preservation of optional timestamped caption segments and normalized source
  metadata in newly saved transcript-library records.

### Changed

- Library list/search/compare/stats, stdout-only context, default echo
  inspection, claim display, and session inspection are read-only. Echo
  analysis never logs even when `--persist` writes its artifact; context file
  writes and compact claim mutations remain logged.
- `filmot config` labels Filmot and YouTube API credential readiness separately
  while still revealing only `configured` or `not configured`.
- `download --help` describes its input as general discovery results and
  includes a direct `yt-playlist --raw` pipeline example.
- Exact channel-handle validation accepts bounded international letters and
  combining marks plus a narrow separator set while rejecting query, fragment,
  shell-control, and other unsafe punctuation before API use.
- Claim persistence is strict: write failures fail the command. Session logging
  remains a compact, best-effort activity trail and omits claim text/excerpts.
- Claim mutations hold a per-topic, crash-releasing OS lock across the complete
  read/check/append/readback transaction and validate events before publishing,
  preventing concurrent assessment forks or self-poisoning writes.
- Transcript `--chunk` takes precedence over `--timestamps` for terminal and
  plain-text export presentation while original source segments remain in
  raw/JSON results and library records.
- Echo labels and clusters are described as possible reuse/common-lineage
  signals rather than proof of copying, dependence, credibility, falsity, or
  truth.
- Topic slug v1 remains compatible with existing NFKC/case-folded paths and is
  now explicitly scoped to Python's bundled Unicode database; shared projects
  should pin their Python minor version before reusing one data directory.
- Freshness scouting now exposes a field-aware lexical admission gate and uses
  the displayed global candidate rank without a reserved scout quota. Lexical
  admission is explicitly not a semantic relevance, credibility, or truth
  judgment, and scout-only span diagnostics no longer appear on Filmot rows.
- Automatic probes seed only from manual/staged-selected transcripts, rank
  relationships with source-normalized salience, and stop after three
  discoveries or a broad returned sample with no lexically coherent candidate.
  Broad sampled-zero output reports coverage, defers the lower-ranked tail,
  avoids a global-zero claim, and preserves language/title/channel scope in its
  exact manual follow-up command.
- Research relationship stages now accumulate one unique candidate pool until
  the maximum depth target is reached or the ladder is exhausted. Duplicates
  keep their strongest stage provenance; a nonempty underfilled pool skips
  loose fallback and reports targeted exact/near-search recovery. Depth zero
  keeps the Filmot ladder at the title+transcript preview and downloads no
  selected candidates; an enabled scout may still join that preview, and an
  explicit probe may use existing eligible library seeds.
- At any depth, a legitimately empty current discovery records an empty
  selection while an explicit probe continues from eligible preexisting topic
  library seeds and emits terminal accounting. Fatal broad-scope gates remain
  fail-closed and do not continue into probing.
- Probe planning records explicit zero-query terminal outcomes when eligible
  seeds yield no candidate terms or no supported cross-source pair. Session
  summaries expose bounded probe-run outcomes and count omitted older runs.
- Pipeline download now normalizes and validates the complete provider-neutral
  candidate batch before opening transcript routes or writing library data, so
  unchanged `yt-search --raw` output is accepted and an invalid later row
  cannot leave an unintentionally partial mutation. Its bounded native-field
  copy prioritizes freshness, disclosure, status, and topic provenance ahead
  of bulky provider extras.
- Pipeline download now hashes the exact decoded stdin text and registers the
  YouTube-owned metadata lifecycle for each saved direct-YouTube candidate.
  Lifecycle registration failures retain the transcript, surface a partial
  outcome, and leave explicit provenance for later adoption.
- Explicit discovery-backed saves now apply provider-aware ownership:
  Filmot-derived candidates retain fill-only enrichment, while a new YouTube
  observation replaces only the previous YouTube-owned path set and preserves
  conflicting manual or other-provider values.
- `channel-download` now accepts only exact 24-character `UC...` IDs,
  `@handles`, and canonical `https://[www.]youtube.com/channel/UC...` or
  `https://[www.]youtube.com/@handle` URLs instead of guessing arbitrary
  display names. It records the requested reference and
  resolved canonical identity separately, resolves the uploads playlist with
  `channels.list`, and retains richer channel/playlist-item provenance with
  30-day observation/expiry timestamps.
- `channel-download` now enumerates bounded, resumable upload slices instead of
  an unbounded pre-download crawl. It defaults to 10 pages/500 distinct
  uploads, exposes `--pages`, `--max-results`, and `--page-token`, prints a
  copyable continuation, preserves usable earlier pages on later failure, and
  checkpoints request/coverage/expiry state in the corpus manifest.

### Fixed

- Playlist command/provider boundaries reject API-key-shaped identities,
  detach unexpected transport exceptions without retaining caller state, and
  reject cross-resource rows before they can enter the transcript pipeline.
- `claims cite --at` accepts the timestamps Filmot displays (`M:SS` and
  `H:MM:SS`) as well as numeric seconds, eliminating manual time conversion
  while preserving canonical evidence identity and typed raw failures.
- `transcript --grep` filters and validates saved copies before ambiguity,
  collapses equivalent records, reuses a single compatible local content
  variant before external routing, and returns the same citation-ready match
  evaluation in human and raw modes. Distinct or incompatible variants fall
  back with an explanation.
- Blank and malformed `transcript --grep` expressions now fail through typed
  `InvalidGrepQuery` results before library lookup, proxy setup, or retrieval;
  an empty raw grep can no longer become an unintended full-transcript result.
- Raw `yt-search --transcript` no longer bypasses transcript evaluation. Its
  per-video status, match count, matches, and bounded failure details are shared
  with human mode instead of being hidden inside the human renderer.
- Direct YouTube discovery keeps all usable earlier-page rows when a later page
  fails and keeps search rows when optional metadata enrichment fails or omits
  an ID. These outcomes are typed partial instead of appearing empty or losing
  the original search rank/provenance.
- YouTube request and channel-client failures are categorized without retaining
  native request/response objects or credential-bearing URLs. Common API keys,
  tokens, signatures, authorization fields, URL userinfo, and credential query
  parameters are redacted again at typed result and ledger boundaries.
- Playlist command failures now pass through explicit credential-safe
  summarization before `log_event`, rather than relying only on the downstream
  ledger boundary. Rejected references are detached before errors propagate.
- Long playlist continuation commands are emitted with soft wrapping and are
  also retained as argument arrays in raw output, so terminal layout does not
  insert hard line breaks into the copyable command.
- A playlist-shelf page failure after successful channel resolution now keeps
  the channel and any completed rows as a typed partial result, including when
  the failed page returned no rows.
- Channel upload pagination detects repeated continuation tokens, unavailable
  playlist rows retain usable video identities, and absent public counters are
  no longer fabricated as zero.
- The Click regression-test runner no longer passes the constructor option
  removed in Click 8.5, so a fresh supported development install reaches the
  product regressions.
- Legacy transcript-library records are normalized to the current in-memory
  shape without an eager rewrite; absent caption segments remain an explicit
  empty list rather than receiving invented timestamps.
- Transcript-library saves validate inputs and strict JSON before publishing a
  flushed, `fsync`ed same-directory temporary through atomic replacement, so a
  failed save does not truncate an existing record.
- Case-insensitive local matching now runs against the original Unicode text,
  so expanding lowercase forms cannot shift citation offsets or timestamps;
  missing, invalid, incomplete, or text-misaligned segment timing no longer
  invents a locator.
- Session summaries include item-level bulk and research download failures and
  saves while keeping selected-download and probe outcomes separately visible.
- Session summaries now provide bounded scout/probe provenance and link saved
  sources to their discovery stage/query; missing links in older probe events
  are explicitly reported instead of inferred. Scout rows retain the exact
  request parameters and render a copyable raw inspection command when known.
- Session summaries preserve `broad_sampled`, `deferred`, and `failed_closed`
  probe states instead of normalizing them to completed work, and canonicalize
  legacy `title_transcript` source metadata to the recorded
  `title+transcript` search stage.
- Session provenance includes completed manual transcript saves as `manual` /
  `query_not_recorded` without inferring a query. Recorded research or probe
  selection provenance takes precedence when the same source has both. New
  successful manual-save events carry best-effort title/channel metadata;
  legacy manual events without it remain `Unknown` rather than inferred.
- Nested `library context --output` paths now create missing parent directories
  inside the typed write boundary, including typed failures when parent
  creation is impossible.
- Search interruption before outcome persistence leaves one resumable event;
  session replay/summary reports malformed ledger records as partial or failed.
- Echo analysis fails closed on unreadable/incomplete corpus records, keeps the
  same analysis identity in inspect and persist modes, verifies existing
  content, supports filesystems without hard links, and avoids redundant
  pairwise union allocation/full intersection sorting on larger corpora.
- No-hardlink publication uses crash-releasing OS locks and atomically renames
  a complete same-directory temporary, so concurrent writers never expose a
  partial destination or strand a stale existence-based lock.
- Successful claim publication normally removes its private same-directory
  temporary. Cleanup failure after durable publication reports the durable
  destination and exact retained temporary without implying rollback.
  Publication plus cleanup failure reports both errors, names the exact retained
  temporary, and says the destination was not confirmed. Claim operations do
  not scan/delete historical or unrelated temporaries. Exact mutation retries
  are content-idempotent; recovery follows the reported durable/not-confirmed
  outcome rather than guessing.
- Echo human output is capped at the 25 strongest matching pairs; raw output
  and artifacts remain complete.
- Non-finite echo thresholds and evidence timestamps are rejected before JSON
  output or strict persistence; evidence timestamps require a video ID, and
  video locators cannot be attached to a non-video source kind.
- `claims cite --video` now rejects URLs and malformed text before persistence;
  it accepts only exact 11-character YouTube IDs matching
  `[A-Za-z0-9_-]{11}`.
- Raw result serialization rejects non-finite or unsupported JSON values with a
  typed failure, and declared session/claim schemas are validated strictly on
  read instead of being silently treated as legacy data.
- Logged raw commands preflight serialization before their event is written;
  transcript and local-library response folding also fails through typed JSON
  boundaries instead of leaking post-fetch exceptions.
- Raw output rejects non-standard numeric values, uses ASCII JSON
  escaping for Unicode, and `main.py` now propagates the CLI return status via
  `SystemExit`.
- Documentation now distinguishes lexical concordance, bulk-download prefix
  deduplication, repeated search-hit collapse, and full-transcript echo
  similarity instead of treating them as equivalent verification mechanisms.

## [0.4.0] - 2026-07-25

### Added

- A staged research workflow with freshness scouting, explainable candidate
  ranking, relationship-preserving fallbacks, broad-query safety gates,
  cross-source probing, and resumable checkpoints.
- Structured session ledgers for searches, transcript downloads, research
  phases, library operations, and channel corpora. Machine-global proxy
  diagnostics remain outside project research history.
- Bounded transcript route plans with redacted progress, per-route provenance,
  proxy-pool leases, health classification, and live proxy tests.
- Unicode-safe library and session topic names with explicit-only migration for
  ambiguous legacy directories.
- Process-isolated transcript attempts so a timed-out worker can be terminated
  instead of remaining as a quarantined daemon thread.
- Sanitized recorded Filmot API contract fixtures and runtime structural
  validation to identify incompatible upstream response changes.
- Typed command outcomes and events shared by raw JSON, ledger persistence, and
  human rendering.

### Changed

- Split the CLI command implementation into search, research, transcript,
  library, and proxy modules.
- Raw commands emit exactly one processed `filmot.result/v1` JSON value and
  keep diagnostics off stdout. Mapping payloads retain their domain keys and
  expose contract metadata under `_filmot`; session replay carries
  `filmot.event/v1` records in its `events` field.
- Search pagination, filtering, ranking, result limits, deduplication, and
  partial-result scope are reported explicitly.
- Research data remains project-local while proxy configuration and health are
  stored in a machine-global location.
- Python 3.9 is the declared runtime floor and CI covers both the floor and the
  latest supported Python release.
- GitHub Actions runs pull requests once, runs pushes only on `main`, cancels
  superseded runs, and includes a Windows transcript-cancellation lane.

### Fixed

- Broken-pipe shutdown now exits successfully in normal shell pipelines.
- Channel-scoped search, research, scout, and probe paths fail closed when
  returned channel identities cannot be verified.
- Transcript failures retain normalized route and fallback provenance without
  exposing proxy credentials.
- Configuration inventory reports credential presence without printing key
  fragments. AWS fallback and proxy probes use redacted, deadline-sized,
  cross-process leases without placing proxy credentials in child argv.
- Existing proxy inventories are tightened to owner-only permissions, corrupt
  legacy metadata no longer blocks startup, and custom proxy health stores
  fail closed unless they use the credential-free SQLite backend.
- Research selection rejects incoherent loose matches and one-source probe
  relationships.
- Library concordance excerpts use the requested lexical match mode and no
  longer imply fact-checking or source agreement.

[0.4.0]: https://github.com/baba20o/filmot-cli/releases/tag/v0.4.0
