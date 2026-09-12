# YouTube Data API behavior

Filmot uses the public, read-only YouTube Data API v3 to discover recent
videos and enumerate channel uploads that may not yet exist in Filmot's
transcript index. Transcript retrieval is a separate capability; a YouTube
Data API key does not grant access to caption text.

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

Use RFC3339 `--published-after`/`--published-before` values to replay the exact
absolute UTC interval recorded in `request`. A date-only `--published-before`
means the end of that UTC calendar date and is sent as the next UTC midnight
because the API's upper bound is exclusive. Relative `--days` computes a new
interval on each invocation. Replay a continuation token with the exact emitted
bounds and filters; upstream ordering and content can still change, so this is
not a promise of byte-identical results.

## Discovery and library handoff

The unchanged versioned output from `yt-search --raw` can feed
`filmot download`. The provider-neutral boundary also accepts Filmot candidates and
bare arrays or `result`/`videos`/`items` envelopes. It validates the complete
candidate batch before transcript acquisition or storage, preserves observed
zero separately from missing values, and retains unknown native fields only in
a bounded credential-scrubbed mapping. Freshness, disclosure, status, and topic
provenance is copied before bulky native extras can consume that budget.

For one manual save, pass the artifact explicitly:

```bash
filmot yt-search "fresh topic" --raw > discovery.json
filmot transcript VIDEO_ID --save-to TOPIC --discovery discovery.json
```

Filmot selects exactly one matching video ID and records a `sha256:` reference
to the supplied artifact bytes. It never infers discovery metadata from a
recent query/session neighbor. Existing records use atomic fill-only
enrichment: missing/`Unknown` fields may be filled, known values and observed
zeroes are retained, conflicts are reported, and transcript text, segments,
`saved_at`, acquisition data, citations, and unrelated fields remain intact.
This command currently acquires the transcript before its existing-record
check; a standalone metadata-only refresh command remains future work.

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
stops on a repeated page token. `channel-download --limit` limits transcript
selection only after enumeration; it does not bound API pages. Channel
metadata receives UTC `observed_at`/`expires_at` values 30 days apart.

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

Filmot currently records expiry but does not automatically refresh, hide,
purge, or delete expired API-derived metadata in exported raw files, library
records, or channel manifests. There is also no persistent metadata cache that
checks freshness before reuse. The operator must implement the required
refresh/deletion lifecycle for their deployment.

This document summarizes product behavior and important policy constraints; it
is not legal advice. Operators remain responsible for the policies applicable
to their deployment and use case.
