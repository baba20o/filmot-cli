# Filmot: next implementation slice and follow-up backlog

Updated 2026-09-06. **Status: proposed, not implemented.** Baseline: `main` at `5d8742e11fafa8ae71d79200f448b5f6e233f900`.

Start with **preserving fresh discovery metadata through library persistence**. A researcher should find a newly uploaded video, save its transcript, and later recover its identity and discovery context without manually joining separate JSON files.

This document records outstanding work from the insight and Astra-reaction investigations. It supplements the historical [feature plan](/mnt/c/projects/Filmot/AGENT_FEATURES_PLAN.md) and [field-test log](/mnt/c/projects/Filmot/FIELD_TEST_LOG.md); it does not reopen their already resolved findings. N-series IDs below are independent of the older F-series findings. Priorities indicate implementation order, not security severity.

## Evidence behind the next slice

The live Astra investigation used direct `yt-search` successfully, including a video approximately 16 minutes old and a native `--transcript` query that retained metadata alongside timestamped matches. Freshness discovery itself works.

However, **four of five separately saved transcripts had `Unknown` title/channel metadata**, despite those fields being available in their discovery results. Manual save performs an independent Filmot metadata lookup and does not consume the known YouTube record. The observed symptom alone does not distinguish absent index metadata, a failed lookup, or an unsupported response envelope.

Read-only code inspection also found that the downloader accepts a top-level `videos` array but expects `id`/`videoid` inside it; direct YouTube results use `video_id`. Other field names differ too. The unchanged `yt-search --raw` payload therefore is not currently compatible with the apparent pipe-to-download workflow. This incompatibility was inspected in code, **not tested as a live failing pipe**.

Research's internal scout already maps those fields for selected candidates. Reuse that knowledge through a shared contract rather than creating another independent mapping.

## Slice 1: fresh discovery to durable evidence

Scope: **N01–N04 only**. Agree final option names during implementation; descriptions here are behavior contracts, not claims that proposed CLI flags already exist.

### N01 — Shared candidate normalization and compatible downloads · P1

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

### N02 — Manual save can consume known discovery metadata · P1

Allow an explicitly supplied discovery artifact/reference to accompany a manual transcript save. Select its candidate by exact video ID; never guess from the last query or adjacent session events. Supplied known metadata should not depend on a successful Filmot lookup.

When using the existing best-effort lookup, handle supported single-object, list, and result-envelope responses consistently. Report metadata acquisition problems distinctly from successful caption acquisition.

Acceptance: a save keeps known identity fields even when Filmot metadata is empty or unavailable. Mismatched artifacts fail clearly. A save without an explicit artifact retains its documented behavior. Discovery and caption acquisition remain separately attributable.

### N03 — Explicit metadata enrichment for existing records · P1

Provide a narrowly scoped operation to fill missing fields and recognized `Unknown` placeholders from an explicitly supplied, matching discovery record. Known conflicting values should remain unchanged and be reported. General replacement of known metadata is deferred.

Preserve transcript text, original caption segments/timing, original `saved_at`, acquisition details, citations, and unrelated fields. Keep the record's `source` and `metadata` views consistent. Record enrichment time and source separately. Existing `library.save` reconstructs records and timestamps, so do not assume it is a safe enrichment primitive.

Acceptance: repair an existing `Unknown` record without another caption download. Verify preserved text/segments and acquisition fields, conflict reporting, consistent metadata views, and a no-change outcome when repeating the same enrichment. A failed write must not leave that record half updated. Detect/reconcile stale concurrent updates or serialize writes so enrichment cannot discard another writer's newly known fields. Retain normal existing-record skip behavior unless enrichment is explicitly requested.

### N04 — Durable discovery references and accurate time scope · P1

Persist provider/command, original query, effective filters, order, result cap, UTC request time, actual absolute publication bounds, metadata observation time, and a stable discovery reference or record. Relative `--days` alone is insufficient: rerunning it tomorrow searches a different interval. Capture the bounds actually sent to YouTube instead of recomputing them later.

Link discovery, save method, and later enrichment as distinct events. A later artifact used to enrich an old record must not be relabeled as the original reason that record was saved. Legacy missing provenance stays unknown. Store enough durable information to survive deletion of a temporary input file; avoid dependencies on an untracked scratch path alone.

Acceptance: trace a selected video from discovery to save/enrichment in structured output where supported and the named session. Time-frozen fixtures verify recorded absolute bounds equal the request sent. Provide a replay path that accepts those exact UTC bounds: current date-only `--published-after`/`--published-before` inputs cannot represent the second-level cutoff produced by `--days`. This may use a discovery reference or explicit timestamp inputs; settle the interface during implementation. Reinspection preserves recorded parameters while acknowledging that upstream results can change. Old artifacts without exact bounds remain readable and clearly incomplete.

### Slice 1 completion checks

- Exercise the real Click command boundary with small recorded/synthetic provider fixtures, including Unicode fields, zero views, empty metadata, legacy envelopes, invalid IDs, and malformed input.
- Cover one unchanged `yt-search` payload through download and one explicit discovery-backed manual save. Verify both saved metadata views and source identity.
- Cover metadata enrichment separately: repeat/no-op, known-value conflict, write failure, and unchanged caption content/timestamps. Specify batch partial-success semantics rather than implying cross-record transactions.
- For raw-capable commands invoked in raw mode, verify one versioned JSON value on stdout and separate diagnostics. Across paths, verify accurate outcomes and matching session events. Existing `download` has no `--raw`; adding that capability is a separate explicit interface decision, not an assumed prerequisite.
- Preserve the already fixed separation of investigation sessions from library topics, existing legacy reads, and current scout admission/selection behavior.
- Update README/help with a working discovery-to-save example and the explicit enrichment behavior. Run focused regressions and the repository's current full suite; historical test counts are not acceptance criteria.
- Finish with a small live check of a recent upload, capture evidence, and obtain a review focused on provenance and preservation. Keep network-dependent checks separate from deterministic tests. No AWS fallback is needed for this slice.

## Follow-up backlog

All items are open. **Observed** means encountered in a live investigation; **code finding** means inspected but not necessarily reproduced live; **proposal** means an enhancement to assess before building.

| ID / priority | Item and evidence | Acceptance target |
|---|---|---|
| **N05 · P2** | **Fresh-search scope in session summaries.** Observed: three `yt-search` events existed, but the standalone Filmot-search table showed zero searches. Raw events retained the activity. | Add a separately labeled, bounded YouTube-search section with query, effective dates, order, cap, filters, returned counts, and outcome. Preserve provider-specific counts; do not add unlike search universes together. |
| **N06 · P1, after slice 1** | **Bound YouTube metadata requests.** Code finding: direct search/enrichment requests lack explicit timeout/retry handling. This does not negate the working caption-route recovery. | Explicit request/overall deadlines; bounded retries for suitable transient errors; clear quota/auth/timeout handling; preserve successfully discovered results if optional metadata enrichment fails. Test faults deterministically. |
| **N07 · P2** | **Optional fresh-search pagination and coverage.** Code finding: one request, maximum 50, no exposed continuation. | Opt-in bounded page/result budget; returned/fetched/deduplicated counts, stopping reason, and continuation information when available. Preserve current small default and avoid claiming exhaustive coverage. |
| **N08 · P2** | **Reviewable source selection.** Observed: a generic leadership clip was downloaded; a Bjork query ranked a musician's bass lesson highly. Text matched, topic judgment did not. | First assess existing depth-zero previews. Support inspect/select/exclude with recorded decisions and clear ranking signals where needed. Keep automatic research available; do not introduce a compulsory confirmation gate. |
| **N09 · P2** | **Scout metadata and ranking comparability.** Code finding: normalization loses available likes/duration while scoring considers engagement/density. Ranking impact was not measured live. | Preserve available measurements with units and observation time; represent unavailable signals as missing. Compare controlled fixtures before changing weights. Do not restore a hidden freshness quota or represent engagement as authority. |
| **N10 · P2** | **Caption language and matching clarity.** Observed: an English request failed; choosing the listed Hindi-tagged caption track succeeded. Code finding: `yt-search --lang` affects discovery relevance, not the caption helper, and matching is per-segment substring. | Distinguish discovery-language hints from caption-language preferences; expose available tracks and selected language. Document current grammar. If cross-segment matching is added, preserve source timestamps and test split phrases explicitly. |
| **N11 · P3** | **Caption-error assistance.** Observed: context suggests `insight` where automatic captions repeatedly say `inside`; names also drift. | Optional suggestions or a separate correction layer, with original segments preserved. Exact matches and approximate suggestions remain distinguishable; absence of a match is not absence of the concept. |
| **N12 · P2** | **Consistent event timestamps.** Observed: session records used naive local times; claim records used UTC `Z`. | New records use explicit offsets or UTC. Preserve legacy timestamps and mark their zone as unknown unless an explicit migration input establishes it. Never infer old timestamps from the machine's current zone. |
| **N13 · P3** | **Assessor identity for claims.** Observed: help describes human verdicts; agent assessments required explanatory notes. | Optional human/agent/unknown attribution and an appropriate actor label, with backward-compatible folding. Attribution records the declared actor; it is not verified identity or proof of a claim. |
| **N14 · P2** | **Recovery summaries distinguish attempts from final state.** Observed: a route timeout recovered; a failed language request later succeeded. Current failed-attempt history is useful. | Clearly separate recovered route errors, failed attempts, saved videos, and any unresolved final failures without erasing history or implying that zero final failures means every attempt succeeded. |
| **N15 · P2** | **Documentation claims and scope terminology.** Code/document finding: introductory wording overstates what title filtering and density establish. | Describe lexical relevance, heuristic rank, request completion, corpus coverage, caption matching, and index freshness precisely. Remove blanket authority/relevance guarantees; verify examples against actual contracts. |

## Deliberate scope boundaries

Slice 1 does not change relevance scoring, scout admission, probe behavior, pagination, transport policy, caption search grammar, or transcript content. It does not add automatic background metadata refresh, silently overwrite known values, or claim to reconstruct missing historical provenance. Those boundaries keep the first change reviewable and preserve the successful research loop.

No semantic verifier or credibility score is proposed as a substitute for source judgment. Saved reactions, source similarity, popularity, and claim assessments retain their existing evidence limits.

## Code starting points

| Area | Current owner |
|---|---|
| Direct YouTube discovery and absolute date calculation | [youtube_search.py](/mnt/c/projects/Filmot/filmot/youtube_search.py) |
| Bulk candidate consumption and `yt-search` command | [commands/search.py](/mnt/c/projects/Filmot/filmot/commands/search.py) |
| `download` command and manual transcript save | [commands/transcript.py](/mnt/c/projects/Filmot/filmot/commands/transcript.py) |
| Existing scout normalization and persistence | [commands/research.py](/mnt/c/projects/Filmot/filmot/commands/research.py) |
| Library normalization and record writes | [library.py](/mnt/c/projects/Filmot/filmot/library.py) |
| Event recording and session folding | [ledger.py](/mnt/c/projects/Filmot/filmot/ledger.py), [commands/library.py](/mnt/c/projects/Filmot/filmot/commands/library.py) |

## Local evidence and restart notes

These captures are ignored local research artifacts, not Git-tracked fixtures. This document includes the key findings so another checkout can understand the scope without them. Create minimal sanitized fixtures for implementation tests rather than committing entire research corpora.

- [Insight investigation: CLI experience](/mnt/c/projects/Filmot/Insights/astra-insight-understanding-2026-09-06/CLI-EXPERIENCE.md) and [manifest](/mnt/c/projects/Filmot/Insights/astra-insight-understanding-2026-09-06/MANIFEST.json).
- [Freshness investigation: observations](/mnt/c/projects/Filmot/Insights/astra-fresh-reactions-2026-09-06/FRESHNESS-AND-REACTIONS.md) and [manifest with exact video-ID metadata joins](/mnt/c/projects/Filmot/Insights/astra-fresh-reactions-2026-09-06/MANIFEST.json).
- Concrete metadata examples: `fEvXSrHPzb4`, `KM_AIwCT5Dc`, `Spuza-KwTJ4`, and `vm_R4uT8ntE` saved with unknown identity fields; direct discovery supplied their titles/channels. `GGzT7zVrRTU` retained metadata and is a useful contrasting case.
- Freshness session: `astra-fresh-reactions-2026-09-06`; insight session: `astra-insight-understanding-2026-09-06`. The live session summaries are preserved under each artifact folder's `runs/` directory.

At documentation time, 22 pre-existing tracked files were dirty, largely newline differences; the whitespace-insensitive diff was a pre-existing one-line `.gitignore` deletion. Preserve those edits when beginning implementation. This document adds planning only; no N-series item is marked fixed.

## Atlas integration follow-up — 2026-09-07

YouTube diagnostic redaction was repaired at both native request boundaries and in the shared text sanitizer. Six dummy-key tests were added; the integrated full suite passes 630 tests. A parent offline check through the native CLI and Atlas confirms the dummy key is absent from emitted diagnostics, recorded events, receipts and raw artifacts after repair, with artifact hashes checked. Copilot-hosted Astra found no blockers in the bounded candidate. No actual credential incident was established. Evidence: .local/atlas-native-repair-evidence/.

Nonblocking next coverage: mocked transport exceptions and successful search followed by enrichment failure through CLI/ledger/Atlas persistence. The sanitizer matches common credential parameter names; it is not universal secret detection. An analogous pre-signed-URL diagnostic path in optional AWS transcription remains a separate review item; the Atlas adapter does not enable paid AWS fallback.
