# YouTube Data API behavior

Filmot uses the public, read-only YouTube Data API v3 to discover recent
videos, fetch exact-ID public video metadata, inspect public playlists and
channel playlist shelves, inspect public comment threads and their replies,
refresh API-owned fields in saved records, and resolve/enumerate channel
uploads. Transcript retrieval is a separate
capability; a YouTube Data API key does not grant access to caption text.
See [the enhancement roadmap](YOUTUBE_ROADMAP.md) for ranked future API
opportunities; this document remains authoritative for shipped behavior.

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
`filmot config` reports the Filmot and YouTube credentials independently as
only `configured` or `not configured`; it never displays a key fragment.

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
  including `videos.list`, `channels.list`, `playlists.list`,
  `playlistItems.list`, `commentThreads.list`, and `comments.list`, cost one
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

The unchanged versioned output from `yt-search --raw`, `yt-video --raw`, or
`yt-playlist --raw` can feed `filmot download`. A `yt-playlists` shelf does not
contain video candidates and is not a download envelope. Neither are
`yt-comments` thread rows or `yt-replies` reply rows: public discourse is not a
transcript candidate list. The provider-neutral
boundary also accepts Filmot candidates and bare arrays or
`result`/`videos`/`items` envelopes. It validates the complete candidate batch
before transcript acquisition or storage,
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

## Public playlist CLI and provider APIs

`yt-playlists` enumerates the public playlist shelf for an exact channel;
`yt-playlist` reads an ordered slice from one exact playlist and enriches its
distinct usable video IDs. Neither command uses `search.list`:

```bash
# CHANNEL is an exact UC ID, @handle, or canonical HTTPS channel URL
filmot yt-playlists CHANNEL --pages 1 --max-results 25

# PLAYLIST is a bare playlist ID or supported HTTPS YouTube URL
filmot yt-playlist PLAYLIST --pages 1 --max-results 25 --raw \
  | filmot download -t curated-topic -n 10 --dedupe
```

Both CLI commands default to one page, 25 rows, a 5-second connect timeout, a
20-second read timeout, and two transient retries. `--pages` accepts 1–100,
`--max-results` accepts 1–5000, and the budgets are independent. For
`yt-playlists`, a row is a playlist; for `yt-playlist`, a row is a retained
playlist item, including one without a usable video ID. `--show-description`
expands human output. `--page-token` is opaque and must be replayed with the
same resource identity and bounds. Human output prints a copyable continuation;
raw output exposes the same token and argument vector in `continuation`.

Before retries, `yt-playlists` spends one `channels.list` request plus up to one
`playlists.list` request per requested page. `yt-playlist` spends one
`playlists.list` request, up to one `playlistItems.list` request per page, and
enough `videos.list` requests to cover distinct usable IDs in batches of 50.
The returned `api_calls` records actual attempts, including retries. A completed
one-page, 25-row shelf therefore normally uses two calls, while a nonempty
one-page playlist normally uses three.

`yt-playlist --raw` is a `filmot.result/v1` object with `playlist`, ordered
`playlist_items`, current `videos`, `video_id_outcomes`, effective `request`,
`coverage`, `api_calls`, `continuation`, and observation/expiry timestamps.
Repeated videos remain at each item position but are enriched once. Items
without a usable video ID remain item evidence. Top-level `videos` contains
only resources returned by completed `videos.list` calls, so that projection
is safe for transcript download; item, unique-ID, and video counts can differ.
Omitted and ID-less resources are neutral observations, not proof of deletion,
privacy, or unavailability. A playlist or channel metadata omission is also
reported without inferring why.

A failed outer `channels.list`/`playlists.list` identity request fails the
command. Once the channel or playlist identity is known, a page or video-detail
failure preserves completed work, reports a typed partial result, and retains
the current continuation when one is usable. Repeated page tokens stop safely.
Returned playlist URLs are canonicalized before request metadata is logged, so
unrelated input query parameters are not retained.

Python integrations can call the same provider layer directly. Its defaults
remain two pages and 100 rows, so explicit bounds are recommended:

```python
from filmot.youtube_resources import (
    get_playlist_detailed,
    list_channel_playlists_detailed,
)

page = get_playlist_detailed(
    "https://www.youtube.com/playlist?list=PLAYLIST_ID",
    max_pages=2,
    max_results=75,
    page_token=None,
)
next_token = page["coverage"]["next_page_token"]

shelf = list_channel_playlists_detailed(
    "@exact_handle",
    max_pages=2,
    max_results=75,
)
```

Both provider APIs validate controls before quota use and return credential-free
request, coverage, warning, error, API-call, and 30-day observation envelopes.
Playlist input accepts a bare bounded ID or a supported HTTPS YouTube URL; only
the parsed ID and a canonical playlist URL are retained, so unrelated URL
parameters do not enter output. Google API-key-shaped values are rejected as
playlist identities before quota use or logging. Shelf lookup accepts the same
exact channel forms as channel download.

The provider envelopes use `coverage.next_page_token`; CLI results additionally
provide `continuation.argv`. All returned metadata has the same 30-day
refresh-or-delete responsibility as other YouTube API observations. Session
events keep bounded request/coverage/call summaries rather than playlist or
video descriptions; raw files saved elsewhere remain the operator's
responsibility.

## Public comment and reply cursors

`yt-comments` reads a bounded `commentThreads.list(videoId=...)` cursor for one
exact video. `yt-replies` reads a separate `comments.list(parentId=...)` cursor
for one top-level comment. This separation matters: the replies embedded in a
comment-thread resource are only a preview and are not guaranteed to include
every reply. Use the nested `top_level_comment.comment_id` returned by
`yt-comments` with `yt-replies`; the outer `thread_id` is a different resource
identity. Google's primary references are
[`commentThreads.list`](https://developers.google.com/youtube/v3/docs/commentThreads/list),
the [`commentThread` resource](https://developers.google.com/youtube/v3/docs/commentThreads),
and [`comments.list`](https://developers.google.com/youtube/v3/docs/comments/list).

```bash
# VIDEO is an exact 11-character ID or supported HTTPS YouTube video URL
filmot yt-comments VIDEO --pages 1 --max-results 25

# Optional YouTube ordering/filtering and its embedded reply preview
filmot yt-comments VIDEO --order relevance --search "open question" \
  --replies preview --raw

# Use the nested top-level ID, not comment_threads[].thread_id
filmot yt-replies TOP_LEVEL_COMMENT_ID --video VIDEO --raw

# A supported watch URL containing both v= and lc= supplies both identities
filmot yt-replies \
  "https://www.youtube.com/watch?v=VIDEO&lc=TOP_LEVEL_COMMENT_ID"
```

Both commands default to one page, 25 retained rows, a 5-second connect
timeout, a 20-second read timeout, and two transient retries. They accept
`--pages` from 1 through 10, `--max-results`/`-n` from 1 through 500,
`--page-token`, `--connect-timeout`, `--read-timeout`, `--retries` from 0
through 5, and `--raw`. `yt-comments` also accepts `--order time|relevance`
(default `time`), `--search`/`--search-terms` with 1–500 non-control
characters, and `--replies none|preview` (default `none`). `yt-replies
--video` accepts an exact video ID or supported video URL solely as validated
context for canonical comment links. A comment URL with `v=` and `lc=` can
supply that context; a conflicting `--video` fails before quota use.

One `commentThreads.list` or `comments.list` request costs one regular quota
unit. Filmot reports actual HTTP attempts under endpoint-specific `api_calls`
and estimates quota conservatively as one unit for each attempt, so retries
increase both counts. Each API page contains at most 100 rows; Filmot lowers
that page size as needed to honor the cross-page result budget. An ordinary
first-page request/response failure fails the command. A later ordinary failure
preserves completed rows, sets `coverage.partial`, records a typed error, and
retains a safe failed-page token when it can be resumed. A
`commentsDisabled` response is terminal instead: on the first page it produces
a typed skipped result, while on a later page Filmot clears the token and
preserves the earlier rows as partial. Invalid or repeated response tokens stop
safely rather than looping.

Coverage records pages attempted/fetched, upstream rows seen, retained rows,
duplicate/malformed/wrong-scope skips, page information, the next token,
request attempts, and one of the explicit stopping reasons such as
`exhausted`, `empty`, `page_budget`, `result_budget`, `partial_failure`, or
`comments_disabled`. Preview mode additionally reports embedded-reply counts
and gives each thread a `reply_coverage` state. Only a valid preview whose row
count equals YouTube's reported `total_reply_count` is
`all_observed_at_response`; `subset`, `unknown`, and `inconsistent` are not
complete. Even the complete label describes that response at that observation
time, because comments can change.

When a safe next token exists, human output prints a copyable command and raw
output exposes the token plus matching arguments in `continuation`. Replay it
with the same resource identity, filters, and budgets, and continue until
`coverage.stopping_reason=exhausted` before describing that observed reply
cursor as exhausted. Generated continuations and the human thread-to-reply
drill-down preserve the active named session. Token pagination is not a promise
of an immutable or byte-identical snapshot.

The raw `filmot.result/v1` data keys for `yt-comments` are `provider`,
`video_id`, `comment_threads`, `replies_mode`, `availability`, `request`,
`coverage`, `api_calls`, `quota`, `continuation`, `observed_at`, and
`expires_at`. Each thread keeps its outer ID, nested top-level row, reported
reply count, optional embedded preview, and preview coverage. A first-page
YouTube `commentsDisabled` response is a successful `skipped` result with
`availability.status=disabled`; a disabled response on a later page is partial
and exposes no continuation. With no rows and no partial/disabled condition,
the result is `empty`; retained complete rows are `completed`.

`availability` contains `status` (`available` or `disabled`), a typed `reason`,
and the HTTP status when supplied by a disabled response. `api_calls` separates
`comment_threads`, `comments`, and `total` attempts. `quota` reports
`units_per_request=1`, `estimated_units`, and
`accounting=attempts_conservative`; it is an explicit estimate rather than a
project quota-balance query. `continuation` contains `available`, the opaque
`next_page_token`, its endpoint-specific `token_kind`, and a reproducible
`argv` list.

The corresponding `yt-replies` keys are `provider`, `parent_comment_id`,
nullable `video_id`, `replies`, `request`, `coverage`, `api_calls`, `quota`,
`continuation`, `observed_at`, and `expires_at`. It is `completed` with rows,
`empty` without rows, and `partial` whenever coverage or typed errors say the
cursor is incomplete. Comment rows retain public author fields,
YouTube's displayed plain text, like count, publication/update times, and a
canonical URL when video context is known. Even with `textFormat=plainText`,
[`textDisplay`](https://developers.google.com/youtube/v3/docs/comments#snippet.textDisplay)
can differ from the author's original input; Filmot does not label it as
original text. Human rendering neutralizes terminal control characters and
Unicode bidi/format controls, collapses metadata newlines, uses padded public
text blocks, and does not interpret public text as Rich markup. This display
containment does not alter raw output. Raw consumers must likewise treat every
comment and author field as untrusted data, never as instructions or shell
input.

These raw keys deliberately avoid the pipeline candidate names `result`,
`videos`, and `items`, so neither command can accidentally feed public
discussion into transcript download. They also do not persist comment rows in
the transcript library. A session event contains only invocation controls and
presence booleans, coarse result status and safe static diagnostics, plus
API-attempt/quota telemetry. It is marked
`transient_result_persisted=false`. No video or comment identity, response row
or count, search text, coverage, availability, continuation state, or page
token is persisted.

`filmot.youtube_comments` is the endpoint-specific Python provider. Call
`list_video_comment_threads_detailed(..., max_pages=..., max_results=...)` or
`list_comment_replies_detailed(..., max_pages=..., max_results=...)` for the
same bounded envelopes. Both provider functions default to one page and 25
rows, reject values above 10 pages or 500 rows, and accept an opaque
`page_token`, per-request `timeout`, bounded `retries`, and an optional HTTP
`session`. The thread provider additionally accepts `search_terms`,
`order="time"|"relevance"`, and `reply_mode="none"|"preview"`; the reply
provider accepts optional `video_reference` context. The providers share
generic request-control, credential-detached error, page-information, and token
handling through the internal `filmot.youtube_api_support` module;
endpoint-specific identity, parsing, and policy decisions remain in their own
modules.

Public comments are mutable user data, untrusted discourse, and a
self-selected sample—not corroboration or a representative audience measure.
Filmot does not calculate sentiment, create derived engagement metrics, infer
sensitive author traits, or build audience profiles. Operators must apply the
[YouTube Developer Policies](https://developers.google.com/youtube/terms/developer-policies)
and the more specific [derived-metrics
policy](https://developers.google.com/youtube/terms/derived-metrics-policy) to
any downstream use.

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
`metadata_expires_at` to enriched video records and `observed_at`/`expires_at`
to transient comment and reply results. Raw discovery or discussion output is
not automatically written as an archive. Discussion session events store only
invocation controls/presence booleans, coarse safe diagnostics, and API-attempt
and quota telemetry; they are explicitly marked as not persisting the transient
result. They retain no returned descriptions, tags, counters, comment text,
identity, rows, coverage, availability, continuation state, or page token.

If you intentionally pipe API metadata into a durable transcript corpus or
save raw output elsewhere, refresh or delete those API-derived fields by their
expiry. Historical observations must be labeled with their observation time,
not presented as current values. Deleting a local Filmot record does not delete
the corresponding content from YouTube.

Comment and reply output is transient public user data. Filmot has no comment
archive or background refresh job, and YouTube-side edits or deletions do not
propagate into a file you exported. Delete or reacquire each saved copy no later
than its `expires_at`; also apply any shorter retention and user-data safeguards
required for your use. `yt-data status|refresh|purge` manages only explicitly
owned YouTube video-metadata fields in saved transcript records. It does not
inventory, refresh, or purge comment/reply raw files.

Operators distributing Filmot as an API Client need an accurate privacy policy.
An API Client that accesses or stores user data must additionally provide a
user-data deletion mechanism. YouTube's policy requires requested stored user
data to be deleted as soon as possible and within seven calendar days. Review
the official [Developer
Policies](https://developers.google.com/youtube/terms/developer-policies) and
[policy guide](https://developers.google.com/youtube/terms/developer-policies-guide)
for the obligations that apply to your deployment; this documentation is not
legal advice.

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

There is no automatic background refresh, hide, or purge. Exported raw files,
comment/reply results, and channel manifests are not managed by `yt-data`, and
an expired library observation remains present until an operator runs refresh
or purge. Operators must schedule those actions as appropriate and separately
maintain or delete raw artifacts, public-comment data, and channel-manifest API
data.

This document summarizes product behavior and important policy constraints; it
is not legal advice. Operators remain responsible for the policies applicable
to their deployment and use case.
