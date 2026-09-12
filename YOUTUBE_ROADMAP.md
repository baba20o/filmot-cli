# YouTube API enhancement roadmap

Updated 2026-09-12. This is the durable queue for expanding Filmot's
public, read-only YouTube Data API surface. It records opportunities and
decision gates; it is not a promise to implement every endpoint in order. A
new research mission can reorder the queue without losing the audit.

## Product direction

Filmot should use YouTube API data when it removes mechanical work from
transcript research: resolving exact identities, exposing creator-curated
paths, retrieving current public context, and making scope and freshness
visible. New work should keep these invariants:

- API mechanics stay low-cognitive-load: small defaults, explicit global
  bounds, typed stopping reasons, safe continuations, and compact human output.
- A first request failure fails; a later failure preserves completed work as a
  visibly partial result.
- Public API data carries an observation time and a refresh-or-delete deadline
  no later than 30 days afterward.
- Mutable counters, ranks, comments, and curation signals are never presented
  as credibility, consensus, provenance, or a representative audience measure.
- API-key reads remain separate from OAuth owner/moderator capabilities and
  from transcript acquisition.
- Raw schemas either deliberately satisfy the video-candidate pipeline contract
  or deliberately avoid its `result`/`videos`/`items` keys.
- Transient user-data cursors keep response rows, identities, counts, coverage,
  availability, and continuation state out of durable session events; the
  ledger records only invocation mechanics and safe call/quota telemetry.
- Every slice needs deterministic contract/security tests, updated operator and
  agent documentation, and a minimal live quota-bounded field test.

## Implemented foundation

| Surface | Current Filmot capability |
|---|---|
| `search.list(type=video)` | Bounded recent-video discovery with filters, exact UTC windows, page continuations, and best-effort video enrichment. |
| `videos.list` | Exact-ID metadata, 50-ID batching, per-ID outcomes, current counters/status/disclosures, and managed saved-record refresh/purge. |
| `channels.list` + uploads `playlistItems.list` | Exact channel resolution and resumable upload-corpus enumeration. |
| `playlists.list` + `playlistItems.list` | Exact channel playlist shelves and curated playlist-to-transcript handoff. |
| `commentThreads.list` + `comments.list` | Separate bounded public thread/reply cursors, optional embedded preview, transient user-data handling, and no derived audience analytics. Deterministic verification, the bounded live thread/reply drive, and the final minimal-ledger replay are complete and recorded in `FIELD_TEST_LOG.md`. |
| Shared API layer | Credential-erasing transport plus reusable control, retry, error, pagination, continuation, coverage, and quota-attempt conventions. |

### Cross-cutting CLI rough edge

Raw-mode contracts currently begin after Click has parsed the command line.
Invalid root or option syntax such as `--pages 0` therefore still produces
Click's human stderr and exit code 2 instead of a versioned JSON failure. A
future interface slice should evaluate one consistent machine-readable parse
boundary across commands while preserving normal `--help`, shell completion,
and non-raw behavior.

## Ranked next opportunities

### 1. Human-readable catalogs and selected localization

Add bounded catalog reads for
[`videoCategories.list`](https://developers.google.com/youtube/v3/docs/videoCategories/list),
[`i18nRegions.list`](https://developers.google.com/youtube/v3/docs/i18nRegions/list),
and [`i18nLanguages.list`](https://developers.google.com/youtube/v3/docs/i18nLanguages/list).
Use them to replace unexplained numeric category IDs, validate region/language
inputs, and render a selected metadata language through `hl` on existing video
and channel requests.

Why it ranks first: it removes recurring lookup and validation work from every
multilingual search for only one quota unit per catalog request. Cache entries
must expire within 30 days. YouTube UI language and content region are hints,
not transcript language, video origin, author nationality, or audience
geography. Prefer one requested localization over fetching every localization.

### 2. Channel homepage sections

Use
[`channelSections.list`](https://developers.google.com/youtube/v3/docs/channelSections/list)
to expose a creator's ordered homepage organization: featured playlists,
channels, uploads, and other section types. Keep this alongside—not instead
of—the full playlist shelf.

Why it matters: a section map can reveal the creator's intended navigation and
reduce manual playlist triage. It costs one regular unit and channels have a
small section limit. A featured or automatic section is a mutable presentation
signal, not endorsement, importance, affiliation, or an exhaustive catalog.

### 3. Scalable statistics-only refresh

Evaluate the June 2026
[`videos.batchGetStats`](https://developers.google.com/youtube/v3/docs/videos/batchGetStats)
method for large saved libraries. It uses a separate granular quota bucket and
returns compact success/failure accounting, but provides substantially less
metadata than `videos.list`.

This should ship only if measured refresh volume justifies a second update
path. Before implementation, verify the current documented ID limit instead
of assuming the 50-ID `videos.list` cap, and design a field-owned stats refresh
that cannot erase richer observations. Do not derive engagement or credibility
scores from the returned counters.

### 4. Direct channel and playlist search

Consider explicit `search.list(type=channel)` and
`search.list(type=playlist)` discovery modes. They can help when an exact
identity is unknown, but each request consumes the scarce search quota and
introduces heterogeneous result schemas. Existing Filmot channel discovery and
exact playlist paths already cover much of the need, so this requires a
concrete workflow and separate raw contracts rather than a generic type switch.

### 5. Small provenance-oriented metadata gaps

Evaluate selected uploader-supplied fields such as recording date only when a
research case needs them. Sparse or uploader-entered metadata must remain
labeled assertions, never independent verification. Embed-player HTML,
branding assets, and deprecated recording geolocation do not currently justify
surface area.

## Conditional or deferred public reads

| Endpoint | Potential value | Why deferred |
|---|---|---|
| [`subscriptions.list(channelId=...)`](https://developers.google.com/youtube/v3/docs/subscriptions/list) | Publicly disclosed adjacent-channel discovery. | Hidden shelves are incomplete; network/affiliation inference creates substantial privacy and surveillance risk. No centrality, affiliation, or audience profiling should be built from it. |
| [`activities.list(channelId=...)`](https://developers.google.com/youtube/v3/docs/activities/list) | Chronological non-upload channel events. | Upload events duplicate the more reliable uploads playlist, the feed is not a complete archive, and no strong non-upload workflow exists yet. |
| [`videos.list(chart=mostPopular)`](https://developers.google.com/youtube/v3/docs/videos/list) | Regional/category music, movie, and gaming discovery. | Since July 2025 it is not a general YouTube trending surface. Any command would need that narrow label and protection against ranking-as-importance claims. |

Channel-wide comment aggregation remains deferred even though the endpoint is
public. It raises retention, identity, sampling, and derived-analysis risk and
does not fit the exact-video discussion workflow without a separate compliance
and research-utility case.

## OAuth and write boundary

Do not silently add these to the API-key client:

- caption listing/download, live broadcasts/streams/chat, membership data,
  comment moderation, private subscription/activity variants, and owner audit
  data require OAuth or owner permissions;
- captions obtained through the official owner API have very different cost
  and authorization semantics from Filmot's transcript routes;
- inserting, updating, moderating, rating, reporting, watermarking, and other
  writes require an explicit product mission, consent model, and destructive
  action safeguards.

If an OAuth product is requested, design it as a separate authorization and
data-lifecycle boundary rather than weakening the current API-key contract.

## Admission checklist for a future slice

Before code starts, record:

1. The exact research question and why the endpoint reduces tool mechanics.
2. Authentication class, current quota cost/bucket, official limits, and
   whether another implemented endpoint already provides the data.
3. Row identity, scope, missing/disabled/private meanings, pagination or batch
   semantics, and what the API cannot establish.
4. Human and raw contracts, pipeline eligibility, continuation behavior, and
   compact ledger fields.
5. The 30-day refresh/delete path and any user-data, deletion-request,
   surveillance, or derived-metric constraints.
6. Pre-quota validation, credential-erasing exception behavior, malformed and
   cross-scope response handling, partial preservation, and attempt accounting.
7. Synthetic tests plus the smallest live call budget capable of validating
   the real workflow without committing API response bodies or identities.

The authoritative behavior of shipped commands remains in `YOUTUBE_API.md`;
the near-term release state and restart instructions remain in
`NEXT-SLICE.md`.
