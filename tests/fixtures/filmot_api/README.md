# Filmot API contract fixtures

These fixtures were recorded from live Filmot API response bodies on
2026-07-25 and then sanitized. They intentionally retain JSON container shapes,
field names, and value types while replacing video/channel identities,
human-readable transcript content, dates, and URLs. HTTP request/response
headers and credentials are never recorded.

To refresh them after deliberately reviewing an upstream contract change:

```bash
python scripts/record_filmot_api_fixtures.py --force
python -m pytest -q tests/test_api.py tests/test_api_contract.py
```

`search_channels_direct.json` was recorded from the direct Filmot API on
2026-10-22. That provider returns different channel-suggestion field names
than the RapidAPI gateway; refresh it with:

```bash
python scripts/record_filmot_api_fixtures.py --provider direct \
  --only search_channels --suffix _direct --force
```

Each direct API request spends credits (`/getsearchchannels` costs 1).

`get_videos_columns.json` was recorded on 2026-10-22 through the RapidAPI
gateway with every column `filmot video` requests. Its description and
snapshot date are replaced like the other human-readable content and dates.
Refresh it with:

```bash
python scripts/record_filmot_api_fixtures.py --only get_videos_columns --force
```

Under the default `auto` policy that request uses RapidAPI. With
`--provider direct` it costs 2 credits.

`get_videos_columns_partial.json` is the same request for a video that Filmot
holds only as a partial record (`ispartialdata: 1`). Its title, description,
like count, and caption languages are null. It was recorded on 2026-10-22 with:

```bash
python scripts/record_filmot_api_fixtures.py --only get_videos_columns \
  --video-id EXB8HokGVMI --suffix _partial --force
```

The recorder validates both the live body and its sanitized form. Runtime
validation accepts additive fields but returns a machine-readable error when a
required container or consumed field changes incompatibly.
