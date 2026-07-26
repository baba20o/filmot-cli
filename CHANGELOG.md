# Changelog

All notable changes to Filmot CLI are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
