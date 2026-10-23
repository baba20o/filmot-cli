# Read saved transcript passages

Use `library read` to inspect a time window in an already saved transcript:

```bash
filmot library read dQw4w9WgXcQ --topic music --from 1:00 --to 2:00
filmot library read 'https://youtu.be/dQw4w9WgXcQ' --from 60 --to 120 --raw
filmot library read dQw4w9WgXcQ --topic music --from 1:00 --to 2:00 \
  --max-chars 3000 --max-segments 40 --raw
```

The command only reads the transcript library rooted at `FILMOT_DATA_DIR`, or
the current directory's `.filmot_data`. It never configures transcript routes,
contacts a provider, acquires missing captions, writes files, or appends session
events. No credentials are needed. Other transcript commands retain their
existing acquisition behavior. The library is trusted local data: like the
other library commands, the reader follows links inside it.

`VIDEO` accepts an exact 11-character YouTube ID or a supported public YouTube
URL. Both window endpoints are required. They accept nonnegative seconds,
`M:SS`, or `H:MM:SS`, including fractional seconds. The end must be later than
the start. A URL is convenient for video IDs that begin with `-`.

## Select the saved copy

Without `--topic`, exactly one topic must contain the requested ID. Multiple
copies fail with `AmbiguousSavedTranscript`, even when their text is identical
or their languages differ. The error lists candidate topic names and paths
(up to 25, with a total count and truncation indicator); use `library list` to
inspect further topics and rerun with `--topic`. The reader never guesses the
newest copy or a preferred language. Explicit topics use the library's normal
canonical topic mapping, without falling back to legacy topic directories.

A missing copy fails with `SavedTranscriptNotFound` and exit code 1. To acquire
a new copy, run a separate command:

```bash
filmot transcript dQw4w9WgXcQ --save-to music-new
```

Choose a new topic name for a fresh caption copy: an existing `--save-to` target
does **not** refresh its saved caption text. `library read` reports the saved
timestamp and acquisition source; it does not determine whether upstream
captions have changed. `yt-data refresh` refreshes API metadata, not captions.

## Timing and bounds

The window is half-open: `[from, to)`. A caption is selected when its stored
time interval overlaps that window. A caption ending exactly at `from`, or
starting exactly at `to`, is excluded. Selected captions keep their original
timing and may contain words spoken just outside the window. Missing or zero
durations are treated as point events at their start; missing durations are
reported explicitly, without inventing an end time.

The reader validates finite, nonnegative, ordered start times and available
durations. The caption texts must reproduce the saved full transcript, joined
with spaces (line breaks inside a caption become spaces) or, for saves that
preserved formatting, with line breaks. Malformed or inconsistent records,
including unreadable or non-strict JSON, fail locally with
`InvalidSavedTranscript`; no partial evidence is emitted. Text-only saves fail with
`UntimedSavedTranscript`. Their text remains accessible through
`library context TOPIC`; acquiring a timestamped copy is a separate action.

Defaults return at most **6,000 transcript characters** and **80 segments**.
`--max-chars` accepts 1–100,000 and `--max-segments` accepts 1–1,000. Both limits
apply identically to human and raw output. They bound transcript text, not JSON
syntax, provenance, timestamps, or terminal formatting. A character limit can
clip the final returned caption; `text_truncated` marks that row. Summary
counts report matching/returned characters, matching/returned/omitted segments,
and an overall `truncated` flag. Narrow the window or increase the bounds to
read omitted text. Reading beyond the saved timing produces a successful
`empty` result, which is distinct from a missing record.

## JSON contract

`--raw` emits one JSON value with the existing `_filmot` versioned result
metadata (`command: library-read`, status, errors, warnings). Runtime selection,
parse, and record failures produce a typed `failed` result and exit code 1.
Click option errors, such as a missing required endpoint or an out-of-range
limit, retain normal CLI usage errors.

- `saved_copy`: absolute path, save time, normalized source metadata, saved
  language, generated-caption flag, and acquisition route when available.
  Unknown language/route values remain null; the library's existing source
  alias normalization applies to legacy records.
- `window`: numeric endpoints and the `segment-overlap` selection rule.
- `rows`: original segment index, start/end seconds, readable timestamp,
  YouTube URL, bounded caption text, and `text_truncated`.
- `summary`: output bounds, counts, truncation, and missing-duration count.
- `local_only: true`: this invocation has no remote acquisition route.

Numeric saved seconds retain their precision. Readable timestamps show up to
six decimal places; YouTube links use the containing whole second. Source
metadata and caption text are saved observations, not independently verified
claims.
