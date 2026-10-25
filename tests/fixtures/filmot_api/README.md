# Filmot API contract fixtures

The current upstream contract is documented in
[Filmot's official API documentation](https://filmot.com/api-documentation).
Use `FILMOT_API_KEY` and `--provider direct` to record official API responses.
Some fixtures retain legacy RapidAPI response shapes; their provenance is
listed below, and refreshing them requires that provider's credentials.

The original fixtures, `search_subtitles_context.json`,
`search_subtitles_lines.json`, `get_videos.json`, and `search_channels.json`,
were recorded through the legacy RapidAPI gateway on 2026-07-25. All captures
are sanitized: they retain JSON container shapes, field names, and value types
while replacing video/channel identities, human-readable transcript content,
dates, and URLs. HTTP request/response headers and credentials are never recorded.

To refresh those four legacy fixtures after reviewing an upstream contract change:

```bash
python scripts/record_filmot_api_fixtures.py --provider rapidapi \
  --only search_subtitles_context --only search_subtitles_lines \
  --only get_videos --only search_channels --force
python -m pytest -q tests/test_api.py tests/test_api_contract.py
```

Select fixtures with `--only`; omit it only when intentionally refreshing the
whole set. Review provider shape changes before replacing a legacy fixture
with an official API response. For official API comparisons, use
`--provider direct --suffix _direct` to keep the legacy captures intact.
Each unscoped official subtitle-search request costs 5 credits.

`search_channels_direct.json` was recorded from the official Filmot API on
2026-10-22. That provider returns different channel-suggestion field names
than the legacy RapidAPI gateway; refresh it with:

```bash
python scripts/record_filmot_api_fixtures.py --provider direct \
  --only search_channels --suffix _direct --force
```

Each direct API request spends credits (`/getsearchchannels` costs 1).

`get_videos_columns.json` was recorded on 2026-10-22 through the legacy RapidAPI
gateway with every column `filmot video` requests. Its description and
snapshot date are replaced like the other human-readable content and dates.
Refresh it with:

```bash
python scripts/record_filmot_api_fixtures.py --provider rapidapi \
  --only get_videos_columns --force
```

The explicit legacy provider keeps the fixture's original source. To review
an official API response instead, use `--provider direct --suffix _direct`;
that request costs 2 credits. Under `auto`, RapidAPI is preferred only when
its key is configured.

`get_videos_columns_partial.json` is the same request for a video that Filmot
holds only as a partial record (`ispartialdata: 1`). Its title, description,
like count, and caption languages are null. It was recorded on 2026-10-22;
refresh the legacy fixture with:

```bash
python scripts/record_filmot_api_fixtures.py --provider rapidapi --only get_videos_columns \
  --video-id EXB8HokGVMI --suffix _partial --force
```

`get_videos_subtitles.json` is a `/getvideos` request for the `subtitles` and
`lastfetchdate` columns with `lang=en`, recorded on 2026-10-22 through the
legacy RapidAPI gateway. Caption text is replaced, and the fixture keeps at most two
tracks and three lines per track. Refresh it with:

```bash
python scripts/record_filmot_api_fixtures.py --provider rapidapi --only get_videos_subtitles \
  --video-id jNQXAC9IVRw --force
```

`get_channels.json` (the base profile, 1 credit), `get_channels_all.json`
(`columns=all` from `startDate=2026-04-01`, 3 credits), and
`get_channel_videos.json` (three rows with every `/getvideos` metadata column,
3 credits) were recorded from the official API on 2026-10-23. Profile text,
previous profile values, usernames, and caption terms are replaced. Word-cloud
and subscriber series keep three entries, profile-history fields two, and
monthly and language statistics two. Refresh them with:

```bash
python scripts/record_filmot_api_fixtures.py --provider direct --only get_channels \
  --only get_channels_all --only get_channel_videos --force
```

`metadata_search.json` (`/getmetadatasearch`), `advanced_channel_search.json`
(`/getadvancedchannelsearch`, topics `SQLite`), and `subtitle_export.json`
(`/getsubtitlesearch` with `format=json`) were recorded from the official API on
2026-10-23. The two video searches were scoped to one channel, so each request
cost 1 credit. Titles, channel names, descriptions, topic words, and
subtitle-hit context and match text are replaced. Each keeps three rows and two
hits per row. Refresh them with:

```bash
python scripts/record_filmot_api_fixtures.py --provider direct --only metadata_search \
  --only advanced_channel_search --only subtitle_export --force
```

The recorder validates both the live body and its sanitized form. Runtime
validation accepts additive fields but returns a machine-readable error when a
required container or consumed field changes incompatibly.
