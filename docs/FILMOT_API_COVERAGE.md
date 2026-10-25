# Filmot API coverage and commit audit

Upstream documentation: [Filmot's official API](https://filmot.com/api-documentation).
The CLI calls this provider `direct`; RapidAPI is a legacy compatibility gateway.

Reference: the supplied `context/openapi.json`, Filmot API 1.0.0, reviewed on
2026-10-25. SHA-256:
`f9722985a352d06a87d1191ac79cf1b924c96c40ecc514792490ba53f5325982`.
The reference is a local, ignored file; runtime use and clean-clone tests do not
depend on it. This is a contract audit, not a live-provider compatibility claim.

The review covers the direct-API series through `42e3ce1` (PRs #8–#18), plus
its integration with the existing discovery and library workflows. All seven
schema endpoints and 100 query parameters were inspected. The legacy
`/getsearchsubtitles` endpoint remains supported outside this supplied schema.

## Endpoint coverage

| Endpoint | Python client | CLI and scope |
|---|---|---|
| `/getchannels` | `get_channels` | `channel`; all optional groups via flags or `--all`, dates and archive statistics |
| `/getvideos` | `get_videos` | `video` requests all non-subtitle metadata; `--basic` uses base fields; transcript index acquisition requests language-filtered subtitle tracks |
| `/getchannelvideos` | `get_channel_videos` | `channel-videos`; metadata by default, `--columns`/`--all`, `--lang`, subtitle-presence filter and bounded paging |
| `/getsubtitlesearch` | `export_subtitle_search` | `subtitle-export`; every search filter, selectable columns/all, server exports |
| `/getsearchchannels` | `search_channels` | `channels` and exact-handle resolution |
| `/getadvancedchannelsearch` | `search_advanced_channels`, `export_advanced_channel_search` | `find-channels`; text filters, columns/all, JSON or JSONL export |
| `/getmetadatasearch` | `search_metadata`, `export_metadata_search` | `titles`; all filters, enriched columns/all, subtitle-aware paging, server exports |

The JSON search methods deliberately force JSON and return parsed objects.
The corresponding export methods return `ExportBody` bytes and preserve format
parameters. They share provider routing, credential scrubbing, and ZIP inspection.
Export responses are never cached. Supplying `--output` changes CLI stdout into
a file summary when `--raw` is used. One-page files preserve provider bytes;
multi-page files aggregate records. Merged JSON retains `result` and, when
present, per-page envelope metadata in a `pages` array. A channel aggregate
retains each source page's `page`, `perpagecount` and `maxresults` there; it does
not claim to be a single API response page.

## Request parameter inventory

Names below are the schema's names, not CLI spellings. Dictionary-based methods
accept them directly; the [README](../README.md#discovery-searches) documents CLI
spellings. `exportInline=1` is fixed by the export CLI because it writes the
returned bytes to a local file. Named formats are used instead of numeric aliases.

| Endpoint | Parameters |
|---|---|
| `getchannels` (5) | `channelID`, `columns`, `startDate`, `endDate`, `includeArchive` |
| `getvideos` (3) | `id`, `columns`, `lang` |
| `getchannelvideos` (7) | `channelID`, `countPerChannel`, `page`, `onlyWithSubtitles`, `columns`, `lang`, `flags` |
| `getsubtitlesearch` (27) | `query`, `channelID`, `country`, `license`, `category`, `excludeCategory`, `minViews`, `maxViews`, `minLikes`, `maxLikes`, `lang`, `title`, `startDuration`, `endDuration`, `detectedLang`, `noGuessVideoLanguage`, `searchManualSubs`, `startDate`, `endDate`, `sortField`, `sortOrder`, `page`, `perPageCount`, `format`, `exportInline`, `columns`, `getSnippets` |
| `getsearchchannels` (1) | `term` |
| `getadvancedchannelsearch` (29) | `channelCreatedAfter`, `channelCreatedBefore`, `firstVideoAfter`, `firstVideoBefore`, `minSubscribers`, `maxSubscribers`, `minTotalViewCount`, `maxTotalViewCount`, `minVideos`, `maxVideos`, `minAvgVideoDuration`, `maxAvgVideoDuration`, `gainedViewCountPeriod`, `minGainedViewCount`, `maxGainedViewCount`, `gainedSubPeriod`, `minGainedSubCount`, `maxGainedSubCount`, `countries`, `autoLanguages`, `description`, `topics`, `searchMode`, `sortField`, `sortOrder`, `page`, `perPageCount`, `columns`, `format` |
| `getmetadatasearch` (28) | `titleQuery`, `channelID`, `country`, `captionLanguages`, `detectedLanguage`, `minViews`, `maxViews`, `minLikes`, `maxLikes`, `startDate`, `endDate`, `startDuration`, `endDuration`, `category`, `excludeCategory`, `license`, `unlisted`, `live`, `completeData`, `sortField`, `sortOrder`, `page`, `perPageCount`, `capLangExactMatch`, `qDetectedLangInCapLangs`, `format`, `exportInline`, `columns` |

Channel profile groups are `description`, `biography`, `channelchanges`,
`wordcloud`, `growth`, `subscribergrowth`, `videostats`, `monthlystats`,
`languagestats`, and `distributionstats`; `channel --all` requests all of them.
Dates filter video statistics and subscriber observations, not profile dates or
the fixed growth windows. `includeArchive` applies to video statistics.

The client retains legacy `flags` support for channel videos; `flags=1` asks
for descriptions. The CLI uses the clearer `columns` selector. `video` does
not duplicate the transcript command's language/track-selection interface;
Python callers can use `get_videos(columns=..., lang=...)` for arbitrary batches.

## Fields and enrichment

| Schema fields | Retention and meaning |
|---|---|
| Video base `id`, `title`, `channelid`, `channelname`, `uploaddate`, `duration` | Raw records preserve them; library metadata uses normalized identity/publication fields and `video_duration_seconds`. Video duration is distinct from transcript duration. |
| `description`, `category`, `viewcount`, `likecount` | Fill missing library metadata, preserving observed zeros and known conflicts. |
| `dislikecount` | Metadata-search-only field; retained in discovery and saved enrichment. It is not requested from `/getvideos`. |
| `autosubs`, `manualsubs` | Normalize whitespace-separated language codes into automatic/manual caption language lists; empty observed lists differ from unknown values. |
| `isunlisted`, `islivestream`, `ispartialdata` | Normalize provider flags without dropping known false values. |
| `license` | Filmot's `CC`/empty encoding becomes `creative_commons`; it must not become a YouTube API license value. |
| `channelsubcount`, `channelcountryname` | Preserve channel metadata independently of video counters. |
| `lastfetchdate` | Retain Filmot's observation time; it is not the local save or transcript acquisition time. |
| Video/metadata `subtitles` | Nested language/automatic tracks and timed `s`/`t` lines remain in explicit raw results or export files. Full tracks are not metadata enrichment. |
| Subtitle-export `lang`, `hits`, `subtitles` | Export bytes retain matched language, context/matches, and flat timed lines. The export `subtitles` shape differs from Video's track array. |
| Channel profiles and analytics | Raw profiles retain every returned field and nested group; human tables summarize them. No channel analytics are attached to unrelated video transcript records. |
| Advanced-channel fields | Explicit columns/all can retrieve descriptions, topic clouds, match scores, counts, growth, languages and all identity/statistic fields. |

Sparse metadata-search and advanced-channel projections are legal API responses.
Their validators check present fields without requiring unselected base fields.
CLI discovery selections retain identity so paging and downstream handoff remain
usable. Video responses retain the existing tolerance for real partial index
records with null base metadata, even where the supplied schema is stricter.
Unknown response fields remain available; validation is a compatibility check of
consumed fields, not an exhaustive JSON Schema interpreter.

Filmot discovery handoff and automatic `/getvideos` enrichment share normalization
and the fill-only ownership rules. Explicit discoveries retain known metadata
even when the later lookup is disabled or fails. YouTube-owned paths and markers
remain protected. Versioned enrichment records retain cumulative filled fields;
default `library enrich` selects missing/older schema observations and imports
without a completed full-column lookup. Completed lookups carry
`lookup_profile: video-metadata`, `lookup_status: returned|not_returned`, and a
separate lookup time. An absent row produces a negative receipt without inventing
metadata or a snapshot date. Current completed lookups, including nullable,
partial or absent observations, are skipped by default; provider failures stay
eligible. `--all` or `--video` explicitly rechecks them.

`lookup_status` describes the most recent complete metadata lookup, not every
later discovery import. `lookup_at` advances on completed rechecks even when
metadata values are unchanged. An invalid video ID that was never requested
cannot receive a completed lookup receipt. Canonical Filmot fields supplied in
`provider_fields` are validated at both discovery and library enrichment
boundaries; they cannot bypass raw-field validation. Raw API spellings take
precedence when both representations are present.

Bounded `provider_fields` is a metadata carrier, not a transcript archive.
Bulky subtitle payloads are omitted there with an explicit omission marker;
use the raw source artifact, an export, or transcript acquisition for full text.

## Limits and deliberate policies

| Operation | Contract and CLI behavior |
|---|---|
| Channel profiles | Up to 20 unique channels. |
| Video lookup | API permits 200 IDs for metadata, 10 with `subtitles`/`all`; metadata enrichment batches at 200, index acquisition uses a single video. |
| Channel video listing | Up to 10 channels; channels × count at most 200, or 10 with subtitles/all. Omitted count is reduced in subtitle mode; excessive explicit count is rejected. |
| Metadata search | Default CLI page size 50, maximum 200, pages through 500. Subtitles/all changes default/maximum to 10 and last page to 5000. |
| Advanced channel search | Default CLI page size 25, maximum 300; only first 10,000 results accessible. Growth windows are 28/90/180/365 days. |
| Subtitle export | Default CLI page size 100, maximum 500. A short page ends the search; ordinary metadata/channel paging stops at an empty page or the window limit. |
| Text bounds | Subtitle base language at most 20 characters; advanced description/topics at most 500; automatic languages at most 100 tokens of at most 20 characters each. |
| Export formats | Metadata/subtitle CLI offers JSON, JSONL, CSV and ZIP; channel search offers JSON/JSONL. CSV retains server-serialized fields; JSON/JSONL preserves structured subtitles and ZIP carries subtitle files. |

Compact CSV is deliberately not merged by the CLI: its rows inherit values from
earlier rows, so concatenating separately initialized pages would corrupt data.
The low-level byte-export methods can request a single compact CSV page. Numeric
format aliases and browser attachment behavior are also left to those methods.
The schema marks `searchMode=hybrid|knn` unavailable (400); CLI channel search
uses the functional text mode. These are explicit restrictions, not missing
enrichment fields.

## Gaps found in recent integration

| Area | Repair |
|---|---|
| Discovery searches (#14) | Expose columns/all and metadata/channel exports; obey subtitle-specific limits; accept legal sparse selections. |
| Subtitle export (#14) | Expose manual-language guessing and columns/all; preserve earlier paid pages and continuation when a later page is malformed. |
| Channel listing (#13) | Forward language and legacy flags in the client; expose raw subtitle selection with the smaller total cap. |
| Metadata persistence (#9 plus discovery integration) | Preserve duration/dislikes/description, flags and caption languages; retain Filmot ownership and snapshot dates on manual/bulk handoff. |
| Incremental enrichment (#9) | Retain cumulative provenance; select older schemas/discovery-only imports; record completed negative lookups to avoid repeated default charges. |
| Raw discovery pipeline (existing consumer exposed by #13/#14) | Reject failed/interrupted discovery; process partial candidates while preserving incomplete status, errors and continuation with a nonzero download exit. |
| Provider pinning (#8) | Do not let an invalid unused provider host prevent a pinned provider request. |

Regression coverage lives in the API contract/provider, discovery command/export,
channel, metadata, library and pipeline test modules. Tests use fixtures and
synthetic failures; no credential reads or paid requests are needed to exercise
these paths. Remaining research/tooling backlog is in
[NEXT-SLICE](NEXT-SLICE.md) and [the YouTube roadmap](YOUTUBE_ROADMAP.md).
