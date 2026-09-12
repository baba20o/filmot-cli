# YouTube Data API behavior

Filmot uses the public, read-only YouTube Data API v3 to discover recent
videos, fetch exact-ID public video metadata, refresh API-owned fields in saved
records, and resolve/enumerate channel uploads. Transcript retrieval is a
separate capability; a YouTube Data API key does not grant access to caption
text.

## Configuration and attribution

Set `YOUTUBE_API_KEY` in the process environment, the per-user Filmot
`config.env`, or the current project's `.env`. Restrict the key to the YouTube
Data API and to the hosts or IPs that run Filmot. Filmot supplies it only to
Google's YouTube Data API transport/client, redacts credential-shaped fields,
URL parameters, and userinfo at provider, result, artifact, and ledger
boundaries, and never intentionally includes the key in discovery output.
Provider errors do not retain native request/response objects or full request
URIs that could carry the key. These controls are defense in depth: restrict
and rotate keys, and never place credentials in free-form queries or notes.

Results come from YouTube and retain canonical YouTube watch URLs. Use of this
integration is subject to the [YouTube Terms of Service](https://www.youtube.com/t/terms),
[YouTube API Services Terms](https://developers.google.com/youtube/terms/api-services-terms-of-service),
[Developer Policies](https://developers.google.com/youtube/terms/developer-policies),
and [Google Privacy Policy](https://policies.google.com/privacy).

## Scope, quota, and reproducibility

- `search.list` is token-paginated. Its reported total is approximate; Filmot
  relies on `nextPageToken`, records the actual UTC publication bounds, and
  reports why it stopped. Each requested page consumes another search call.
- As of September 2026, Google documents a default project allocation of 100
  `search.list` calls per day in a separate search bucket, with each call also
  described as costing one search-query unit. Other read methods used here,
  including `videos.list`, `channels.list`, and `playlistItems.list`, cost one
  regular quota unit per request. Check Google's current
  [quota calculator](https://developers.google.com/youtube/v3/determine_quota_cost)
  before planning a large crawl.
- A `videos.list` enrichment request can cover up to 50 IDs. Filmot preserves
  search rank and the original discovery rows when enrichment is partial or
  unavailable. Missing or hidden counters remain `null`; they are not rewritten
  as genuine zeroes.
- `search.list(order=date)` can lag or omit newly indexed uploads. For a known
  channel, the uploads playlist returned by `channels.list` and enumerated by
  `playlistItems.list` is YouTube's documented reliable recent-upload path.
- Retry behavior is bounded and limited to transient transport/server/rate
  failures. Authentication, invalid-request, and ordinary quota failures fail
  immediately so retries do not waste quota.

`filmot yt-search` defaults to the last 7 days, date order, one page, 25
distinct results, rich enrichment, a 5-second connect timeout, a 20-second read
timeout, and two transient retries. An explicit `--published-after` replaces
the relative lower bound. `--pages` (maximum 10) and `--max-results` (maximum 500) are independent
page/result budgets; `--page-token` accepts an opaque continuation. Raw output
records the effective credential-free request, API call/page/candidate counts,
deduplication/malformed counts, approximate total, next token, enrichment
state, and a stopping reason. Do not treat the approximate total as an
exhaustive result count.

A failure before the first usable page fails the command. A later-page failure
keeps prior pages and returns a partial result. Optional detail enrichment is
also best effort: missing IDs or a failed detail batch retain original search
rank/snippets, leave unobserved values null, and mark the aggregate partial.
There is currently no one wall-clock deadline across all pages and detail
batches; the limits apply to each request.

`filmot yt-video` calls `videos.list` directly for exact 11-character IDs or
supported public watch, `youtu.be`, Shorts, embed, and live URLs. Repeated and
comma-separated inputs are accepted, validated before quota is spent,
deduplicated in first-occurrence order, and batched by 50. Its raw
`filmot.result/v1` payload is also a pipeline-compatible candidate envelope.
It records a credential-free request, ordered returned resources, aggregate
coverage, observation/expiry times, and one outcome for every requested ID:

- `observed`: the completed response returned that video resource;
- `not_returned`: a completed response omitted it, with no availability reason
  inferred; or
- `unprocessed`: its batch did not complete, so no new observation or expiry
  exists.

A later-batch failure preserves earlier batch results and reports the remaining
IDs as unprocessed. Omitted IDs make coverage partial; if other rows were
returned the command is partial, while an all-omitted lookup is empty. Either
way omission is neutral: it is not proof that a video is deleted, private,
invalid, or unavailable.

Use RFC3339 `--published-after`/`--published-before` values to replay the exact
absolute UTC interval recorded in `request`. A date-only `--published-before`
means the end of that UTC calendar date and is sent as the next UTC midnight
because the API's upper bound is exclusive. Relative `--days` computes a new
interval on each invocation. Replay a continuation token with the exact emitted
bounds and filters; upstream ordering and content can still change, so this is
not a promise of byte-identical results.

## Discovery and library handoff

The unchanged versioned output from `yt-search --raw` or `yt-video --raw` can
feed `filmot download`. The provider-neutral boundary also accepts Filmot
candidates and bare arrays or `result`/`videos`/`items` envelopes. It validates
the complete candidate batch before transcript acquisition or storage,
preserves observed zero separately from missing values, and retains unknown
native fields only in a bounded credential-scrubbed mapping. Freshness,
disclosure, status, and topic provenance is copied before bulky native extras
can consume that budget.

For one manual save, pass the artifact explicitly:

```bash
filmot yt-search "fresh topic" --raw > discovery.json
filmot transcript VIDEO_ID --save-to TOPIC --discovery discovery.json
```

Filmot selects exactly one matching video ID and records a `sha256:` reference
to the supplied artifact bytes. It never infers discovery metadata from a
recent query/session neighbor. Pipeline download similarly hashes the exact
decoded stdin text and passes that content reference to every direct-YouTube
record it saves.

Provider-aware persistence keeps two update rules distinct. Filmot-provider
candidates use guarded atomic fill-only enrichment: missing/`Unknown` fields
may be filled while known values and observed zeroes remain. Direct-YouTube
candidates are registered under `metadata_lifecycle.youtube`. A later
observation first removes the previous YouTube-owned path set, then writes the
new returned set; this prevents omitted upstream fields from surviving as
stale API data. Paths not owned by the previous observation are preserved and
reported as conflicts. Both routes retain transcript text, segments,
`saved_at`, acquisition data, citations, and unrelated fields.

The manual `transcript ... --discovery ...` route still acquires captions
before its existing-record check. `yt-data refresh --video VIDEO_ID` is the
metadata-only path for saved YouTube records and does not acquire captions.

## Exact channel upload enumeration

`channel-download` accepts an exact 24-character `UC...` channel ID,
`@handle`, `https://[www.]youtube.com/channel/UC...`, or
`https://[www.]youtube.com/@handle`. It rejects
arbitrary display names, legacy `/c/` and `/user/` URLs, and URLs with a query
or fragment. Use `filmot channels "display name"` to discover an identity, then
pass the exact ID/handle. The requested reference and resolved canonical ID
are recorded separately.

Channel resolution reads public snippet/statistics/topic metadata and the
uploads playlist. Enumeration uses `playlistItems.list`, preserves usable
playlist/video ownership, position, publication, and privacy metadata, and
stops on a repeated page token. `channel-download` defaults to a bounded slice
of 10 pages and 500 distinct uploads. `--pages` accepts 1–100,
`--max-results` accepts 1–5000, and `--limit` separately caps transcript
selection after enumeration.

If another page exists, human output prints a copyable continuation command
containing the opaque `--page-token` and the same budgets. A later-page failure
preserves usable earlier rows, marks enumeration partial, continues to their
transcript downloads, and retains the failed-page token. The final aggregate
also accounts for transcript failures or interruption. A first-page failure
writes no new checkpoint or transcript; an explicitly requested `--fresh` reset has already occurred by
then. `--fresh` and `--page-token` are mutually exclusive. Re-running the same
slice resumes transcript work from the corpus manifest; following the
continuation adds newly enumerated identities to it.

The latest bounded observation is compactly checkpointed in the manifest under
`upload_enumeration` with schema `filmot.youtube-upload-enumeration/v1`,
provider, UTC `observed_at`/`expires_at`, effective request, and detailed
coverage. Channel-resolution and completed enumeration observations use a
30-day window. The checkpoint is not an automatic refresh scheduler.

New Python control-plane integrations should call
`filmot.channel_dl.enumerate_uploads_detailed(uploads_playlist_id,
max_pages=..., max_items=..., page_token=...)`. Page and unique-item budgets
are required. Its credential-free envelope contains normalized `videos`, UTC
observation/expiry times after any completed page, the effective request,
API/page/item accounting, approximate upstream total,
duplicate/malformed/ID-less counts, `next_page_token`, a stopping reason, and
warnings/errors. A failure before the first usable page raises; a later
failure preserves prior pages and reports `partial_failure`. A cooperative
`cancel_check` stops before the next request and leaves the current token for
resumption. Repeated tokens fail safely rather than looping.

The compatibility `list_all_video_ids()` API retains its historical unbounded,
fail-fast, list-returning behavior for older Python callers; the CLI uses the
bounded detailed API.

## Interpretation

Engagement counters are mutable observations, not credibility signals.
`paid_product_placement` records YouTube's `hasPaidProductPlacement` value when
that disclosure is observed. It, `made_for_kids`, and synthetic-media fields
are uploader/platform disclosures: a false or missing value is not proof that
no promotion, child-directed content, or synthetic media exists. Search
language is a relevance hint and does not guarantee that every result uses
that language.

## Storage and the 30-day rule

Public API responses are non-authorized API data. YouTube's Developer Policies
permit only limited temporary storage and require it to be deleted or refreshed
within 30 calendar days. Filmot therefore attaches `metadata_observed_at` and
`metadata_expires_at` to enriched records. Raw discovery output is not
automatically written as a candidate archive; the session ledger stores a
bounded request/coverage summary rather than the returned descriptions, tags,
or counters.

If you intentionally pipe API metadata into a durable transcript corpus or
save raw output elsewhere, refresh or delete those API-derived fields by their
expiry. Historical observations must be labeled with their observation time,
not presented as current values. Deleting a local Filmot record does not delete
the corresponding content from YouTube.

Saved transcript records can carry a `filmot.youtube-metadata/v1` lifecycle.
It identifies the YouTube video resource, records UTC observation/expiry
times, names every provider-owned field with an exact RFC 6901 JSON Pointer,
and retains at most 32 value-free audit events. An optional `sha256:`
`request_ref` identifies either the supplied discovery artifact/input or the
credential-free effective refresh request. The ownership set is what makes a
safe provider-only refresh or purge possible; Filmot does not guess that an
unowned field belongs to YouTube.

Use the explicit maintenance commands:

```bash
# Offline inventory; no key, network request, write, or ledger event
filmot yt-data status
filmot yt-data status --expired --raw

# Default scope is expired records; preview makes no call or write
filmot yt-data refresh --dry-run
filmot yt-data refresh

# Exact IDs select every saved topic copy; --topic can narrow that set
filmot yt-data refresh --video VIDEO_ID --max-videos 500

# Purge defaults to expired records and requires confirmation
filmot yt-data purge --dry-run
filmot yt-data purge --yes
```

`refresh --all` explicitly adds current and unmanaged records; it cannot be
combined with `--video`. Each unique ID is requested once even if saved under
several topics, and `--max-videos` bounds that unique-ID/API work. A completed
batch that omits an ID transitions its copies to the neutral `not_returned`
state and removes the prior owned values. An unprocessed ID is not mutated and
its old expiry is not advanced. Legacy records are adoptable only when they
carry explicit YouTube discovery/enrichment provenance. Refresh and purge use
guarded atomic replacement per record, not one cross-record transaction.

`purge` is offline, removes only the owned paths, and retains transcript text,
segments, citations, acquisition details, other-provider/manual metadata, and
the lifecycle audit. `--all` includes current records, `--max-records` bounds
the mutation set, and `--yes` bypasses the prompt. A truly unmanaged record is
a no-op because its field ownership is unknown.

Python callers can use the same primitives on `TranscriptLibrary`:
`youtube_metadata_inventory()`, `replace_youtube_metadata()`, and
`purge_youtube_metadata()`. Passing `candidate=None` to replacement is the
explicit neutral-not-returned operation, not a deletion assertion. Supplied
timestamps must be timezone-aware and the expiry cannot exceed 30 days after
observation.

There is no automatic background refresh, hide, or purge. Exported raw files
and channel manifests are not managed by `yt-data`, and an expired library
observation remains present until an operator runs refresh or purge. Operators
must schedule those actions as appropriate and separately maintain or delete
raw artifacts and channel-manifest API data.

This document summarizes product behavior and important policy constraints; it
is not legal advice. Operators remain responsible for the policies applicable
to their deployment and use case.
