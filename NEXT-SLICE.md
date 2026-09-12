# Filmot: next implementation slice and follow-up backlog

Updated 2026-09-12. **Status: implemented but unreleased; live API and policy
operator checks remain before release.** The implementation was prepared on
`main` at
`aa42e54aba85abd9d5c210369a2aa4e89a146668`; none of the status statements in
this file should be read as a released-version claim.

The implementation direction remains **preserving fresh discovery metadata
through library persistence**, now accompanied by bounded YouTube transport,
coverage, and channel-upload improvements. A researcher should find a newly
uploaded video, save its transcript, and later recover its identity and
discovery context without manually joining separate JSON files.

This document records outstanding work from the insight and Astra-reaction
investigations. It supplements the historical [feature plan](AGENT_FEATURES_PLAN.md)
and [field-test log](FIELD_TEST_LOG.md); it does not reopen their already
resolved findings. N-series IDs below are independent of the older F-series
findings. Priorities indicate implementation order, not security severity.

## Evidence behind the next slice

The live Astra investigation used direct `yt-search` successfully, including a video approximately 16 minutes old and a native `--transcript` query that retained metadata alongside timestamped matches. Freshness discovery itself works.

At the time of the investigation, **four of five separately saved transcripts
had `Unknown` title/channel metadata**, despite those fields being available in
their discovery results. The unreleased implementation adds an explicit discovery
handoff; this paragraph records the historical evidence, not current expected
behavior.

Read-only inspection also found that the downloader accepted a top-level
`videos` array but expected `id`/`videoid` inside it, while direct YouTube used
`video_id`. The unreleased implementation replaces that split with shared
provider-neutral normalization and a real Click-boundary regression for an
unchanged `yt-search --raw` payload.

Research's internal scout already maps those fields for selected candidates. Reuse that knowledge through a shared contract rather than creating another independent mapping.

## Unreleased implementation status

| Item | Status | Remaining work |
|---|---|---|
| N01 shared normalization | Implemented | None in the deterministic contract; retain a live handoff smoke check. |
| N02 explicit manual handoff | Implemented | Live recent-upload check. |
| N03 fill-only enrichment | Partial | Atomic primitive and explicit existing-record path exist, but there is no metadata-only CLI; `transcript --discovery` still acquires captions before the existing-record check. |
| N04 durable scope/reference | Partial | Exact request bounds, typed raw/ledger scope, RFC3339 replay, and manual artifact hashes exist. Pipeline saves do not yet retain one content hash plus the complete request/coverage envelope as a single durable discovery object. |
| N05 direct-YouTube summary | Implemented | Inspect one live ledger rendering. |
| N06 bounded transport | Partial | Per-request connect/read timeouts and retries exist; one overall wall-clock operation deadline does not. |
| N07 pagination/coverage | Implemented | Live continuation check and quota-aware operational review. |

## Slice 1: fresh discovery to durable evidence

The original slice scoped N01–N04. The unreleased implementation also covers most
of N05–N07 because request provenance, failure semantics, and pagination share
one provider result contract. The descriptions below remain acceptance
contracts; the status table above records what is actually present.

### N01 — Shared candidate normalization and compatible downloads · P1 · implemented

Accept existing Filmot search records and unchanged versioned `yt-search --raw` output at the download boundary. Normalize at least:

| Meaning | Filmot-shaped field | Direct YouTube field |
|---|---|---|
| Video identity | `id` / `videoid` | `video_id` |
| Channel name | `channelname` | `channel_title` |
| Channel identity | `channelid` | `channel_id` |
| Publication timestamp | `uploaddate` | `published_at` |
| Views at observation | `viewcount` | `views` |
| Title | `title` | `title` |

Preserve original provider/provenance fields. Distinguish valid zero counts from missing values. Do not fabricate caption hits or pass incompatible duration formats into scoring. Keep ranking changes out of this slice.

Acceptance: an unmodified fresh-search result can feed `download`; selected IDs resolve and saved records retain title, channel, publication date, and view observation. Existing Filmot inputs and research-scout persistence continue to work. Missing or conflicting IDs produce clear diagnostics before acquisition or mutation for that record.

Implementation: `filmot.discovery` accepts bare arrays and
`result`/`videos`/`items` envelopes, normalizes both provider families,
preserves missing versus observed zero, bounds and scrubs provider fields, and
returns all identity errors with no partial candidate set. Pipeline download
preflights the complete batch before acquisition or storage.

### N02 — Manual save can consume known discovery metadata · P1 · implemented

Allow an explicitly supplied discovery artifact/reference to accompany a manual transcript save. Select its candidate by exact video ID; never guess from the last query or adjacent session events. Supplied known metadata should not depend on a successful Filmot lookup.

When using the existing best-effort lookup, handle supported single-object, list, and result-envelope responses consistently. Report metadata acquisition problems distinctly from successful caption acquisition.

Acceptance: a save keeps known identity fields even when Filmot metadata is empty or unavailable. Mismatched artifacts fail clearly. A save without an explicit artifact retains its documented behavior. Discovery and caption acquisition remain separately attributable.

Implementation: `transcript VIDEO_ID --save-to TOPIC --discovery FILE`
selects exactly one matching candidate and records a hash of the supplied
bytes. An invalid/mismatched artifact fails before transcript/library work; an
explicit candidate supplies known metadata before the optional Filmot
missing-field lookup.

### N03 — Explicit metadata enrichment for existing records · P1 · partially implemented

Provide a narrowly scoped operation to fill missing fields and recognized `Unknown` placeholders from an explicitly supplied, matching discovery record. Known conflicting values should remain unchanged and be reported. General replacement of known metadata is deferred.

Preserve transcript text, original caption segments/timing, original `saved_at`, acquisition details, citations, and unrelated fields. Keep the record's `source` and `metadata` views consistent. Record enrichment time and source separately. Existing `library.save` reconstructs records and timestamps, so do not assume it is a safe enrichment primitive.

Acceptance: repair an existing `Unknown` record without another caption download. Verify preserved text/segments and acquisition fields, conflict reporting, consistent metadata views, and a no-change outcome when repeating the same enrichment. A failed write must not leave that record half updated. Detect/reconcile stale concurrent updates or serialize writes so enrichment cannot discard another writer's newly known fields. Retain normal existing-record skip behavior unless enrichment is explicitly requested.

Implementation: the library primitive performs guarded reread,
fill-only/conflict accounting, strict temporary serialization, and atomic
replacement; tests cover preservation, repeat no-op, mismatch, write failure,
and concurrent independent fills. The explicit CLI path invokes it for an
existing record, but only after normal transcript acquisition, so the first
sentence of the acceptance target is not yet satisfied end to end.

### N04 — Durable discovery references and accurate time scope · P1 · partially implemented

Persist provider/command, original query, effective filters, order, result cap, UTC request time, actual absolute publication bounds, metadata observation time, and a stable discovery reference or record. Relative `--days` alone is insufficient: rerunning it tomorrow searches a different interval. Capture the bounds actually sent to YouTube instead of recomputing them later.

Link discovery, save method, and later enrichment as distinct events. A later artifact used to enrich an old record must not be relabeled as the original reason that record was saved. Legacy missing provenance stays unknown. Store enough durable information to survive deletion of a temporary input file; avoid dependencies on an untracked scratch path alone.

Acceptance: trace a selected video from discovery to save/enrichment in structured output where supported and the named session. Time-frozen fixtures verify recorded absolute bounds equal the request sent. The replay path accepts exact UTC bounds because date-only `--published-after`/`--published-before` inputs cannot represent the second-level cutoff produced by `--days`. Reinspection preserves recorded parameters while acknowledging that upstream results can change. Old artifacts without exact bounds remain readable and clearly incomplete.

Implementation: direct discovery records `requested_at`, normalized
second-level UTC bounds, filters, budgets, timeouts/retries, page/call coverage,
and stopping state in raw output and compact events. The CLI accepts RFC3339
bounds for exact replay. Manual handoff hashes the artifact and stores selected
candidate provenance. A complete content-addressed discovery envelope is not
yet persisted for stdin pipeline saves, and old records remain incomplete by
design.

### Slice 1 deterministic coverage

- Recorded/synthetic provider fixtures exercise the real Click boundary,
  including zero views, empty metadata, legacy envelopes, invalid IDs, and
  malformed input.
- Regressions cover one unchanged `yt-search` payload through download and an
  explicit discovery-backed manual save, including both metadata views and
  source identity.
- Enrichment coverage includes repeat/no-op, known-value conflict, write
  failure, concurrency, and unchanged captions/timestamps. Batch behavior does
  not imply a cross-record transaction.
- Raw-capable commands emit one versioned JSON value on stdout with diagnostics
  separated. Existing `download` has no `--raw`; adding that capability remains
  a separate interface decision, not an assumed prerequisite.
- Investigation sessions remain separate from library topics, and legacy reads
  plus current scout admission/selection behavior retain regression coverage.
- README/help includes a working discovery-to-save example and the explicit
  enrichment behavior. Focused and repository-wide deterministic suites were
  green during integration; historical test counts are not acceptance criteria.

The remaining release check is a small live recent-upload and page-continuation
run plus operator review of its provenance and preservation. Keep
network-dependent checks separate from deterministic tests. No AWS fallback is
needed for this slice.

## Follow-up backlog

N08–N15 remain open. N05 and N07 are implemented in the unreleased code;
N06 is partial because per-request bounds do not impose an overall operation
deadline. **Observed** means encountered in a live investigation; **code
finding** means inspected but not necessarily reproduced live; **proposal**
means an enhancement to assess before building.

| ID / priority | Item and evidence | Acceptance target |
|---|---|---|
| **N05 · P2 · implemented** | **Fresh-search scope in session summaries.** The bounded direct-YouTube section now records query, absolute dates, order, cap, filters, pages/fetched/returned counts, enrichment, stopping/partial state, and continuation availability. | Inspect one live ledger rendering. Keep this universe separate from Filmot totals. |
| **N06 · P1 · partial** | **Bound YouTube metadata requests.** Direct search/enrichment now have configurable per-request connect/read timeouts, bounded transient retries, safe quota/auth/timeout categories, and partial preservation. | Add an overall wall-clock/operation deadline across pages and detail batches; consider cancellation. |
| **N07 · P2 · implemented** | **Optional fresh-search pagination and coverage.** `--pages`, a distinct-result budget, `--page-token`, call/token counts, approximate total, deduplication, stopping reason, and continuation are exposed with a small one-page default. | Perform a live continuation/quota check; never claim exhaustive coverage. |
| **N08 · P2** | **Reviewable source selection.** Observed: a generic leadership clip was downloaded; a Bjork query ranked a musician's bass lesson highly. Text matched, topic judgment did not. | First assess existing depth-zero previews. Support inspect/select/exclude with recorded decisions and clear ranking signals where needed. Keep automatic research available; do not introduce a compulsory confirmation gate. |
| **N09 · P2** | **Scout metadata and ranking comparability.** Code finding: normalization loses available likes/duration while scoring considers engagement/density. Ranking impact was not measured live. | Preserve available measurements with units and observation time; represent unavailable signals as missing. Compare controlled fixtures before changing weights. Do not restore a hidden freshness quota or represent engagement as authority. |
| **N10 · P2** | **Caption language and matching clarity.** Observed: an English request failed; choosing the listed Hindi-tagged caption track succeeded. Code finding: `yt-search --lang` affects discovery relevance, not the caption helper, and matching is per-segment substring. | Distinguish discovery-language hints from caption-language preferences; expose available tracks and selected language. Document current grammar. If cross-segment matching is added, preserve source timestamps and test split phrases explicitly. |
| **N11 · P3** | **Caption-error assistance.** Observed: context suggests `insight` where automatic captions repeatedly say `inside`; names also drift. | Optional suggestions or a separate correction layer, with original segments preserved. Exact matches and approximate suggestions remain distinguishable; absence of a match is not absence of the concept. |
| **N12 · P2** | **Consistent event timestamps.** Observed: session records used naive local times; claim records used UTC `Z`. | New records use explicit offsets or UTC. Preserve legacy timestamps and mark their zone as unknown unless an explicit migration input establishes it. Never infer old timestamps from the machine's current zone. |
| **N13 · P3** | **Assessor identity for claims.** Observed: help describes human verdicts; agent assessments required explanatory notes. | Optional human/agent/unknown attribution and an appropriate actor label, with backward-compatible folding. Attribution records the declared actor; it is not verified identity or proof of a claim. |
| **N14 · P2** | **Recovery summaries distinguish attempts from final state.** Observed: a route timeout recovered; a failed language request later succeeded. Current failed-attempt history is useful. | Clearly separate recovered route errors, failed attempts, saved videos, and any unresolved final failures without erasing history or implying that zero final failures means every attempt succeeded. |
| **N15 · P2** | **Documentation claims and scope terminology.** Code/document finding: introductory wording overstates what title filtering and density establish. | Describe lexical relevance, heuristic rank, request completion, corpus coverage, caption matching, and index freshness precisely. Remove blanket authority/relevance guarantees; verify examples against actual contracts. |

## Deliberate scope boundaries

The implementation does not change relevance scoring, scout admission, probe
behavior, caption-search grammar, or transcript content. It does add bounded
direct-YouTube pagination and transport policy because accurate provenance and
partial preservation depend on them. It does not add automatic background
metadata refresh/deletion, silently overwrite known values, or claim to
reconstruct missing historical provenance. YouTube-derived public metadata is
timestamped with a 30-day expiry, but operators remain responsible for
refreshing or deleting persisted/raw API data by that expiry.

No semantic verifier or credibility score is proposed as a substitute for source judgment. Saved reactions, source similarity, popularity, and claim assessments retain their existing evidence limits.

## Code starting points

| Area | Current owner |
|---|---|
| Direct YouTube discovery, transport, enrichment, and absolute dates | [youtube_search.py](filmot/youtube_search.py) |
| Shared provider-neutral candidate boundary | [discovery.py](filmot/discovery.py) |
| Bulk candidate consumption and `yt-search` command | [commands/search.py](filmot/commands/search.py) |
| `download`, explicit discovery handoff, and manual transcript save | [commands/transcript.py](filmot/commands/transcript.py) |
| Existing scout normalization and persistence | [commands/research.py](filmot/commands/research.py) |
| Library normalization, guarded fill-only enrichment, and record writes | [library.py](filmot/library.py) |
| Exact channel resolution and uploads-playlist enumeration | [channel_dl.py](filmot/channel_dl.py) |
| Event recording and session folding | [ledger.py](filmot/ledger.py), [commands/library.py](filmot/commands/library.py) |
| Shared credential redaction and typed result boundaries | [redaction.py](filmot/redaction.py), [schemas.py](filmot/schemas.py) |

## Local evidence and restart notes

These captures are ignored local research artifacts, not Git-tracked fixtures. This document includes the key findings so another checkout can understand the scope without them. Create minimal sanitized fixtures for implementation tests rather than committing entire research corpora.

- [Insight investigation: CLI experience](Insights/astra-insight-understanding-2026-09-06/CLI-EXPERIENCE.md) and [manifest](Insights/astra-insight-understanding-2026-09-06/MANIFEST.json).
- [Freshness investigation: observations](Insights/astra-fresh-reactions-2026-09-06/FRESHNESS-AND-REACTIONS.md) and [manifest with exact video-ID metadata joins](Insights/astra-fresh-reactions-2026-09-06/MANIFEST.json).
- Concrete metadata examples: `fEvXSrHPzb4`, `KM_AIwCT5Dc`, `Spuza-KwTJ4`, and `vm_R4uT8ntE` saved with unknown identity fields; direct discovery supplied their titles/channels. `GGzT7zVrRTU` retained metadata and is a useful contrasting case.
- Freshness session: `astra-fresh-reactions-2026-09-06`; insight session: `astra-insight-understanding-2026-09-06`. The live session summaries are preserved under each artifact folder's `runs/` directory.

The September 6 worktree note about 22 dirty files described historical local
state and is not a current status report. Use `git status` and a
whitespace-insensitive diff before changing the present worktree.

## Atlas integration follow-up — 2026-09-07

YouTube diagnostic redaction was repaired at both native request boundaries and
in the shared text sanitizer. Six dummy-key tests were added; the historical
integrated suite at that point passed 630 tests. A parent offline check through
the native CLI and Atlas confirmed the dummy key was absent from emitted
diagnostics, recorded events, receipts and raw artifacts after repair, with
artifact hashes checked. Copilot-hosted Astra found no blockers in that bounded
candidate. No actual credential incident was established. Evidence:
`.local/atlas-native-repair-evidence/` (local, intentionally untracked).

Mocked transport exceptions and search-success/enrichment-failure coverage now
exist at provider and CLI boundaries. Remaining security follow-up includes an
analogous pre-signed-URL diagnostic review in optional AWS transcription. The
sanitizer recognizes common credential shapes; it is not universal secret
detection, and the Atlas adapter does not enable paid AWS fallback.

## Remaining YouTube/API work after this implementation

- Add a metadata-only refresh/enrichment CLI that does not acquire captions,
  plus explicit refresh/delete behavior for expired API-derived fields.
- Persist a single bounded, content-addressed discovery request/coverage object
  for stdin pipeline saves, not only candidate-level provenance or a manual
  artifact path/hash.
- Add an overall discovery wall-clock deadline/cancellation budget beyond
  per-request timeouts and retries.
- Give uploads-playlist enumeration a caller-controlled page/item budget and
  cancellation path, and report ID-less malformed rows instead of silently
  skipping them.
- Decide whether useful read-only endpoints such as video categories and
  supported i18n regions/languages belong in the CLI; keep OAuth/write APIs out
  unless a separately authorized use case requires them.
- Run a small live recent-upload and page-continuation check, inspect the
  resulting provenance, and complete the operator policy review before release.
