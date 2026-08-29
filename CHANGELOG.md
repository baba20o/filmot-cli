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

### Fixed

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
- Search interruption before outcome persistence leaves one resumable event;
  session replay/summary reports malformed ledger records as partial or failed.
- Echo analysis fails closed on unreadable/incomplete corpus records, keeps the
  same analysis identity in inspect and persist modes, verifies existing
  content, supports filesystems without hard links, and avoids redundant
  pairwise union allocation/full intersection sorting on larger corpora.
- No-hardlink publication uses crash-releasing OS locks and atomically renames
  a complete same-directory temporary, so concurrent writers never expose a
  partial destination or strand a stale existence-based lock.
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
