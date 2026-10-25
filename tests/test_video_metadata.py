"""Filmot /getvideos columns: parsing, batched lookups, and fill-only library enrichment."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from filmot.api import VIDEO_METADATA_COLUMNS
from filmot.api_providers import ProviderConfigurationError
from filmot.library import TranscriptLibrary
from filmot.video_metadata import (
    caption_languages,
    creative_commons,
    enrich_after_save,
    enrich_saved_transcripts,
    enrichment_summary_line,
    fetch_video_candidates,
    video_candidate,
    video_rows,
)


FIXTURE = Path(__file__).parent / "fixtures" / "filmot_api" / "get_videos_columns.json"
VIDEO_ID = "abc12345678"
OTHER_ID = "def12345678"
OBSERVED = "2026-09-12T10:00:00Z"
EXPIRES = "2026-10-12T10:00:00Z"


def row(video_id=VIDEO_ID, **changes):
    """The recorded full-column shape, with a real 11-character video ID."""
    value = dict(json.loads(FIXTURE.read_text(encoding="utf-8"))[0], id=video_id)
    value.update(changes)
    return value


class FakeClient:
    def __init__(self, *responses, provider="rapidapi"):
        self.responses = list(responses)
        self.calls = []
        self.last_provider = provider

    def get_videos(self, video_ids, flags=None, columns=None, lang=None):
        self.calls.append({"ids": video_ids.split(","), "columns": columns})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response(video_ids) if callable(response) else response


def saved(tmp_path, video_id=VIDEO_ID, **metadata):
    library = TranscriptLibrary(data_dir=tmp_path)
    path = library.save(
        video_id, "topic", "A transcript body.",
        metadata={"title": "Unknown", "channel": "Unknown", **metadata},
    )
    return library, path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ parsing


def test_caption_languages_and_license_read_every_encoding():
    assert caption_languages(" de-DE en es-419 en ") == ["de-DE", "en", "es-419"]
    assert caption_languages(None) == []
    assert [
        creative_commons(value)
        for value in ("CC", "", 2, "2", 1, "1", True, None, "other")
    ] == [True, False, True, True, False, False, None, None, None]


def test_candidate_maps_every_recorded_column():
    assert video_candidate(row()) == {
        "video_id": VIDEO_ID,
        "provider": "filmot",
        "title": "Recorded video title 001",
        "description": "Recorded video description",
        "channel_title": "Recorded channel 001",
        "channel_id": "recorded-channel-001",
        "published_at": "2026-01-01",
        "views": 1763636326,
        "likes": 18945568,
        "video_duration_seconds": row()["duration"],
        "category": "Music",
        "is_unlisted": False,
        "is_livestream": False,
        "creative_commons": False,
        "channel_subscribers": 4550000,
        "channel_country": "Recorded country",
        "partial_metadata": False,
        "filmot_observed_at": "2026-01-02",
        "caption_languages": {
            "automatic": ["en"],
            "manual": ["de-DE", "en", "es-419", "ja", "pt-BR"],
        },
    }


def test_candidate_drops_values_it_cannot_trust():
    candidate = video_candidate(row(
        viewcount=True, likecount=-1, channelsubcount="2500",
        lastfetchdate="2026-02-30", description="   ", isunlisted="maybe",
    ))

    assert "views" not in candidate and "likes" not in candidate
    assert candidate["channel_subscribers"] == 2500
    assert "filmot_observed_at" not in candidate
    assert "description" not in candidate
    assert "is_unlisted" not in candidate
    basic = {key: value for key, value in row().items() if key not in ("autosubs", "manualsubs")}
    assert "caption_languages" not in video_candidate(basic)
    assert video_candidate(row(video_id="not a video id")) is None


def test_caption_languages_record_only_what_filmot_reports():
    assert video_candidate(row(autosubs=" ", manualsubs=None))["caption_languages"] == {
        "automatic": [],
    }
    assert "caption_languages" not in video_candidate(row(autosubs=None, manualsubs=None))


def test_partial_index_records_yield_only_what_they_carry(tmp_path):
    partial = dict(json.loads(
        (FIXTURE.parent / "get_videos_columns_partial.json").read_text(encoding="utf-8")
    )[0], id=VIDEO_ID)
    library, path = saved(tmp_path)

    candidate = video_candidate(partial)
    enrich_saved_transcripts(library, [("topic", VIDEO_ID)], FakeClient([partial]))

    assert candidate["partial_metadata"] is True
    assert not {"title", "description", "likes", "caption_languages"} & set(candidate)
    metadata = read(path)["metadata"]
    assert metadata["title"] == "Unknown"
    assert metadata["category"] == "Science & Technology"
    assert metadata["views"] == 104088
    assert metadata["views_observed_at"] == "2026-01-02"


def test_video_rows_accept_every_response_shape():
    assert video_rows([row(), "junk"]) == [row()]
    assert video_rows({"result": [row()]}) == [row()]
    assert video_rows(row()) == [row()]
    assert video_rows({"error": "quota_exhausted"}) == []
    assert video_rows(None) == []


# ------------------------------------------------------------------ lookups


def test_lookups_batch_200_ids_and_keep_only_requested_rows():
    ids = ["v{:010d}".format(number) for number in range(401)]

    def echo(joined):
        return [row(video_id) for video_id in joined.split(",")] + [row(OTHER_ID)]

    client = FakeClient(echo, echo, echo)
    candidates, errors = fetch_video_candidates(client, ids + ids[:5] + ["bad id", None])

    assert [len(call["ids"]) for call in client.calls] == [200, 200, 1]
    assert all(call["columns"] == VIDEO_METADATA_COLUMNS for call in client.calls)
    assert set(candidates) == set(ids)
    assert candidates[ids[0]][1] == "rapidapi"
    assert errors == []


def test_failed_lookups_are_redacted_and_never_raise():
    client = FakeClient(
        RuntimeError("upstream echoed test-direct-key"),
        {"error": "quota_exhausted", "error_type": "QuotaExhausted"},
    )
    with patch("filmot.video_metadata.VIDEO_BATCH_LIMIT", 1):
        candidates, errors = fetch_video_candidates(client, [VIDEO_ID, OTHER_ID])

    assert candidates == {}
    assert errors[0]["video_ids"] == [VIDEO_ID]
    assert errors[0]["error"].startswith("RuntimeError: upstream echoed")
    assert "test-direct-key" not in errors[0]["error"]
    assert errors[1] == {
        "video_ids": [OTHER_ID],
        "error": "quota_exhausted",
        "error_type": "QuotaExhausted",
    }


# --------------------------------------------------------------- enrichment


def test_enrichment_fills_unknown_metadata_with_snapshot_provenance(tmp_path):
    library, path = saved(tmp_path)

    summary = enrich_saved_transcripts(library, [("topic", VIDEO_ID)], FakeClient([row()]))

    record = read(path)
    metadata = record["metadata"]
    assert (summary["records"], summary["updated"]) == (1, 1)
    assert metadata["title"] == "Recorded video title 001"
    assert metadata["channel"] == "Recorded channel 001"
    assert metadata["description"] == "Recorded video description"
    assert metadata["views"] == 1763636326
    assert metadata["views_observed_at"] == "2026-01-02"
    assert record["source"]["views_observed_at"] == "2026-01-02"
    assert metadata["caption_languages"]["manual"][0] == "de-DE"
    assert metadata["creative_commons"] is False
    marker = record["filmot_enrichment"]
    assert marker["provider"] == "filmot"
    assert marker["api_provider"] == "rapidapi"
    assert marker["observed_at"] == "2026-01-02"
    assert "views" in marker["filled_fields"]
    assert "metadata_enrichment" not in record

    before = read(path)
    again = enrich_saved_transcripts(library, [("topic", VIDEO_ID)], FakeClient([row()]))
    assert again["unchanged"] == 1
    after = read(path)
    assert after["metadata"] == before["metadata"]
    assert after["filmot_enrichment"]["field_observations"] == before["filmot_enrichment"]["field_observations"]


def test_unknown_caption_parts_stay_fillable_by_later_snapshots(tmp_path):
    library, path = saved(tmp_path)
    records = [("topic", VIDEO_ID)]

    enrich_saved_transcripts(library, records, FakeClient([row(autosubs=None, manualsubs=None)]))
    assert "caption_languages" not in read(path)["metadata"]
    enrich_saved_transcripts(library, records, FakeClient([row(autosubs=None, manualsubs=" de en ")]))
    assert read(path)["metadata"]["caption_languages"] == {"manual": ["de", "en"]}
    summary = enrich_saved_transcripts(
        library, records, FakeClient([row(autosubs=" en ", manualsubs=" de fr ")]),
    )

    record = read(path)
    assert record["metadata"]["caption_languages"] == {"manual": ["de", "en"], "automatic": ["en"]}
    assert "caption_languages.automatic" in record["filmot_enrichment"]["filled_fields"]
    assert "caption_languages" in record["filmot_enrichment"]["filled_fields"]
    assert summary["outcomes"][0]["conflicts"] == 1


def test_existing_counts_keep_their_own_observation_date(tmp_path):
    library, path = saved(tmp_path, views=5)
    saved_date = read(path)["metadata"]["views_observed_at"]

    summary = enrich_saved_transcripts(library, [("topic", VIDEO_ID)], FakeClient([row()]))

    metadata = read(path)["metadata"]
    assert metadata["views"] == 5
    assert metadata["views_observed_at"] == saved_date
    assert summary["outcomes"][0]["conflicts"] >= 1


def test_snapshot_observations_keep_per_field_dates_across_fills(tmp_path):
    library, path = saved(tmp_path)
    records = [("topic", VIDEO_ID)]
    enrich_saved_transcripts(library, records, FakeClient([row(likecount=None)]))
    first = read(path)["filmot_enrichment"]
    enrich_saved_transcripts(library, records, FakeClient([row(
        likecount=0, lastfetchdate="2026-02-01",
    )]))
    after = read(path)
    marker = after["filmot_enrichment"]
    assert marker["schema"] == "filmot.index-metadata/v1"
    assert marker["field_observations"]["views"]["observed_at"] == "2026-01-02"
    assert marker["field_observations"]["likes"]["observed_at"] == "2026-02-01"
    assert set(first["filled_fields"]) < set(marker["filled_fields"])
    assert after["metadata"]["likes"] == 0
    assert after["metadata"]["views_observed_at"] == "2026-01-02"


def test_filmot_discovery_save_uses_index_date_and_distinct_duration(tmp_path):
    from filmot.discovery import normalize_candidate

    candidate = normalize_candidate(row(dislikecount=0, duration=0))
    library = TranscriptLibrary(data_dir=tmp_path)
    path = library.save(VIDEO_ID, "topic", "Transcript", metadata={
        "discovery_provider": "filmot", "provider_fields": candidate["provider_fields"],
        "views": candidate["views"], "duration_seconds": 20,
    })
    library.enrich_metadata(VIDEO_ID, "topic", candidate)
    record = read(path)
    assert record["metadata"]["views_observed_at"] == "2026-01-02"
    assert record["metadata"]["video_duration_seconds"] == 0
    assert record["metadata"]["duration_seconds"] == 20
    assert record["metadata"]["dislikes"] == 0
    assert record["metadata"]["creative_commons"] is False
    assert "youtube_license" not in record["metadata"]
    assert "metadata_enrichment" not in record
    assert record["filmot_enrichment"]["observed_at"] == "2026-01-02"


def test_filmot_discovery_infers_protection_of_youtube_owned_fields(tmp_path):
    from filmot.discovery import normalize_candidate

    library = TranscriptLibrary(data_dir=tmp_path)
    path = library.save(VIDEO_ID, "topic", "Transcript", metadata={})
    library.replace_youtube_metadata(VIDEO_ID, "topic", {
        "video_id": VIDEO_ID, "provider": "youtube", "title": "YouTube title",
        "metadata_observed_at": OBSERVED, "metadata_expires_at": EXPIRES,
    })
    before = read(path)
    result = library.enrich_metadata(VIDEO_ID, "topic", normalize_candidate(row()))
    after = read(path)
    assert result["deferred_to_youtube"] is True
    assert after["metadata"]["title"] == "YouTube title"
    assert "description" not in after["metadata"]
    assert after["metadata_lifecycle"] == before["metadata_lifecycle"]
    assert after["metadata"]["creative_commons"] is False
    assert "youtube_license" not in after["metadata"]


def test_unavailable_dislikes_and_unknown_snapshot_dates_stay_unknown(tmp_path):
    assert "dislikes" not in video_candidate(row(dislikecount=-1))
    library = TranscriptLibrary(data_dir=tmp_path)
    path = library.save(VIDEO_ID, "topic", "Transcript", metadata={
        "discovery_provider": "filmot", "views": 0,
    })
    assert read(path)["metadata"].get("views_observed_at") is None


def test_direct_filmot_enrichment_validates_canonical_extras_and_preserves_unknown_fields(tmp_path):
    library, path = saved(tmp_path)
    invalid = {
        "video_duration_seconds": "PT4M", "filmot_observed_at": "invalid",
        "creative_commons": "CC", "channel_subscribers": -5, "dislikes": True,
        "is_unlisted": "maybe", "caption_languages": {"automatic": [None]},
    }
    library.enrich_metadata(VIDEO_ID, "topic", {
        "video_id": VIDEO_ID, "provider": "filmot",
        "provider_fields": {**invalid, "custom": {"retain": True}},
    })
    record = read(path)
    assert not set(invalid).intersection(record["metadata"])
    assert record["metadata"]["provider_fields"] == {"custom": {"retain": True}}
    assert "observed_at" not in record["filmot_enrichment"]


def test_direct_filmot_raw_alias_precedence_includes_null_and_blank(tmp_path):
    library, path = saved(tmp_path)
    library.enrich_metadata(VIDEO_ID, "topic", {
        "video_id": VIDEO_ID, "provider": "filmot", "duration": None, "license": "",
        "lastfetchdate": None, "autosubs": None,
        "provider_fields": {
            "video_duration_seconds": 240, "creative_commons": True,
            "filmot_observed_at": "2026-01-02",
            "caption_languages": {"automatic": ["en"], "manual": []},
        },
    })
    metadata = read(path)["metadata"]
    assert "video_duration_seconds" not in metadata
    assert "filmot_observed_at" not in metadata
    assert metadata["creative_commons"] is False
    assert metadata["caption_languages"] == {"manual": []}
    assert metadata["provider_fields"] == {
        "creative_commons": False, "caption_languages": {"manual": []},
    }


def test_completed_unchanged_lookup_advances_only_receipt_time(tmp_path):
    library, path = saved(tmp_path)
    records = [("topic", VIDEO_ID)]
    with patch("filmot.video_metadata.datetime") as clock:
        clock.now.return_value = datetime(2026, 10, 1, tzinfo=timezone.utc)
        enrich_saved_transcripts(library, records, FakeClient([row()]))
        before = read(path)
        clock.now.return_value = datetime(2026, 10, 2, tzinfo=timezone.utc)
        result = enrich_saved_transcripts(library, records, FakeClient([row()]))
    after = read(path)
    assert result["unchanged"] == 1 and result["updated"] == 0
    assert after["filmot_enrichment"]["lookup_at"] == "2026-10-02T00:00:00Z"
    assert after["filmot_enrichment"]["lookup_status"] == "returned"
    for key in ("enriched_at", "observed_at", "filled_fields", "field_observations"):
        assert after["filmot_enrichment"][key] == before["filmot_enrichment"][key]
    for key in ("metadata", "source", "transcript", "segments", "saved_at"):
        assert after[key] == before[key]


def test_discovery_import_preserves_status_and_time_of_last_complete_lookup(tmp_path):
    library, path = saved(tmp_path)
    enrich_saved_transcripts(library, [("topic", VIDEO_ID)], FakeClient([]))
    receipt = read(path)["filmot_enrichment"]
    library.enrich_metadata(VIDEO_ID, "topic", video_candidate(row()))
    marker = read(path)["filmot_enrichment"]
    assert marker["lookup_status"] == "not_returned"
    assert marker["lookup_at"] == receipt["lookup_at"]
    assert marker["observed_at"] == "2026-01-02"


@pytest.mark.parametrize("invalid", ["invalid", "", "abc12345678\n"])
def test_invalid_ids_are_failed_without_requests_or_lookup_receipts(tmp_path, invalid):
    library = MagicMock()
    client = FakeClient()
    result = enrich_saved_transcripts(library, [("topic", invalid)], client)
    assert result["failed"] == 1 and result["not_returned"] == 0
    assert result["outcomes"][0]["error_type"] == "InvalidVideoID"
    assert client.calls == []
    library.enrich_metadata.assert_not_called()


def test_schema_upgrade_is_eligible_once_without_changing_existing_values(tmp_path):
    library, path = saved(tmp_path)
    enrich_saved_transcripts(library, [("topic", VIDEO_ID)], FakeClient([row()]))
    record = read(path)
    record["filmot_enrichment"].pop("schema")
    path.write_text(json.dumps(record), encoding="utf-8")
    assert library.filmot_enrichment_inventory()[0]["needs_enrichment"] is True
    result = enrich_saved_transcripts(library, [("topic", VIDEO_ID)], FakeClient([row()]))
    assert result["updated"] == 1
    assert library.filmot_enrichment_inventory()[0]["needs_enrichment"] is False
    assert read(path)["metadata"] == record["metadata"]


def test_imported_rows_need_one_complete_column_lookup_even_when_it_fills_nothing(tmp_path):
    library, path = saved(tmp_path)
    library.enrich_metadata(VIDEO_ID, "topic", video_candidate(row()))
    assert library.filmot_enrichment_inventory()[0]["needs_enrichment"] is True
    before = read(path)
    enriched = enrich_saved_transcripts(library, [("topic", VIDEO_ID)], FakeClient([row()]))
    assert enriched["updated"] == 1
    assert enriched["outcomes"][0]["filled_fields"] == []
    assert library.filmot_enrichment_inventory()[0]["needs_enrichment"] is False
    after = read(path)
    assert after["metadata"] == before["metadata"]
    assert after["filmot_enrichment"]["lookup_profile"] == "video-metadata"
    library.enrich_metadata(VIDEO_ID, "topic", video_candidate(row(dislikecount=0)))
    assert library.filmot_enrichment_inventory()[0]["needs_enrichment"] is False


@pytest.mark.parametrize("imported", [False, True])
def test_completed_missing_lookup_is_recorded_without_inventing_observations(tmp_path, imported):
    library, path = saved(tmp_path)
    if imported:
        library.enrich_metadata(VIDEO_ID, "topic", video_candidate(row(likecount=None)))
    before = read(path)
    assert library.filmot_enrichment_inventory()[0]["needs_enrichment"] is True

    result = enrich_saved_transcripts(library, [("topic", VIDEO_ID)], FakeClient([]))

    after = read(path)
    marker = after["filmot_enrichment"]
    assert (result["not_returned"], result["updated"], result["failed"]) == (1, 0, 0)
    assert marker["lookup_status"] == "not_returned"
    assert marker["lookup_profile"] == "video-metadata"
    assert marker["schema"] == "filmot.index-metadata/v1"
    assert marker["lookup_at"].endswith("Z")
    assert library.filmot_enrichment_inventory()[0]["needs_enrichment"] is False
    for field in ("metadata", "source", "transcript", "segments", "saved_at"):
        assert after[field] == before[field]
    if imported:
        for field in ("filled_fields", "observed_fields", "field_observations", "observed_at", "enriched_at"):
            assert marker[field] == before["filmot_enrichment"][field]
    else:
        assert "observed_at" not in marker
        assert "enriched_at" not in marker
        assert marker["filled_fields"] == []
        assert marker["field_observations"] == {}

    # An explicit successful recheck can fill metadata and replaces the
    # negative receipt while retaining older field dates and values.
    again = enrich_saved_transcripts(library, [("topic", VIDEO_ID)], FakeClient([
        row(likecount=0, lastfetchdate="2026-02-01"),
    ]))
    final = read(path)
    assert again["updated"] == 1
    assert final["filmot_enrichment"]["lookup_status"] == "returned"
    assert final["filmot_enrichment"]["observed_at"] == "2026-02-01"
    assert final["metadata"]["likes"] == 0
    if imported:
        assert final["metadata"]["views_observed_at"] == "2026-01-02"
        assert final["filmot_enrichment"]["field_observations"]["views"] == (
            before["filmot_enrichment"]["field_observations"]["views"]
        )


@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("response", [RuntimeError("offline"), {"error": "quota_exhausted"}])
def test_failed_lookup_does_not_record_completion_or_suppress_retries(tmp_path, imported, response):
    library, path = saved(tmp_path)
    if imported:
        library.enrich_metadata(VIDEO_ID, "topic", video_candidate(row()))
    before = path.read_bytes()

    result = enrich_saved_transcripts(library, [("topic", VIDEO_ID)], FakeClient(response))

    assert result["failed"] == 1
    assert result["not_returned"] == 0
    assert path.read_bytes() == before
    assert library.filmot_enrichment_inventory()[0]["needs_enrichment"] is True


def test_successful_recheck_clears_negative_receipt_even_without_field_changes(tmp_path):
    library, path = saved(tmp_path)
    records = [("topic", VIDEO_ID)]
    enrich_saved_transcripts(library, records, FakeClient([row()]))
    before = read(path)
    enrich_saved_transcripts(library, records, FakeClient([]))
    negative = read(path)
    assert negative["filmot_enrichment"]["lookup_status"] == "not_returned"
    assert negative["filmot_enrichment"]["api_provider"] == before["filmot_enrichment"]["api_provider"]

    result = enrich_saved_transcripts(library, records, FakeClient([row()]))

    after = read(path)
    assert result["updated"] == 1
    assert result["outcomes"][0]["filled_fields"] == []
    assert after["filmot_enrichment"]["lookup_status"] == "returned"
    assert after["filmot_enrichment"]["field_observations"] == before["filmot_enrichment"]["field_observations"]
    assert after["metadata"] == before["metadata"]


@pytest.mark.parametrize("fields,top_level,expected", [
    ({"filmot_observed_at": "not-a-date"}, {}, None),
    ({}, {"filmot_observed_at": "2026-99-99"}, None),
    ({"filmot_observed_at": "2026-01-02"}, {}, "2026-01-02"),
    ({"lastfetchdate": "2026-01-02", "filmot_observed_at": "bad"}, {}, "2026-01-02"),
])
def test_direct_save_derives_only_valid_filmot_snapshot_dates(tmp_path, fields, top_level, expected):
    library = TranscriptLibrary(data_dir=tmp_path)
    path = library.save(VIDEO_ID, "topic", "Transcript text.", metadata={
        "views": 3, "discovery_provider": "filmot", "provider_fields": fields, **top_level,
    })

    record = read(path)
    assert record["metadata"]["views_observed_at"] == expected
    assert record["source"].get("views_observed_at") == expected


def test_lookup_failures_are_failures_not_missing_videos(tmp_path):
    library, _ = saved(tmp_path)

    failed = enrich_saved_transcripts(
        library, [("topic", VIDEO_ID)], FakeClient(RuntimeError("offline")),
    )
    missing = enrich_saved_transcripts(library, [("topic", VIDEO_ID)], FakeClient([]))
    unsaved = enrich_saved_transcripts(
        TranscriptLibrary(data_dir=tmp_path / "empty"), [("topic", VIDEO_ID)],
        FakeClient([row()]),
    )

    assert (failed["failed"], failed["not_returned"]) == (1, 0)
    assert failed["outcomes"][0]["error"] == "RuntimeError: offline"
    assert (missing["failed"], missing["not_returned"]) == (0, 1)
    assert unsaved["failed"] == 1
    assert unsaved["outcomes"][0]["error"].startswith("FileNotFoundError")


def test_youtube_provenance_keeps_its_fields_for_the_lifecycle(tmp_path):
    library, path = saved(tmp_path, discovery_provider="youtube")

    summary = enrich_saved_transcripts(library, [("topic", VIDEO_ID)], FakeClient([row()]))

    record = read(path)
    metadata = record["metadata"]
    assert metadata["title"] == "Unknown"
    assert "description" not in metadata and "views" not in metadata
    assert metadata["category"] == "Music"
    assert metadata["caption_languages"]["automatic"] == ["en"]
    assert summary["outcomes"][0]["deferred_to_youtube"] is True
    assert "title" not in record["filmot_enrichment"]["filled_fields"]


def test_youtube_refresh_still_claims_fields_filmot_left_empty(tmp_path):
    library = TranscriptLibrary(data_dir=tmp_path)
    path = library.save(VIDEO_ID, "topic", "A transcript body.", metadata={})
    youtube = {
        "video_id": VIDEO_ID, "provider": "youtube", "title": "YouTube title",
        "channel_title": "YouTube channel", "metadata_observed_at": OBSERVED,
        "metadata_expires_at": EXPIRES,
    }
    library.replace_youtube_metadata(VIDEO_ID, "topic", youtube)

    enrich_saved_transcripts(library, [("topic", VIDEO_ID)], FakeClient([row()]))
    assert "description" not in read(path)["metadata"]
    refreshed = library.replace_youtube_metadata(
        VIDEO_ID, "topic", dict(youtube, description="Now published", likes=9),
    )

    metadata = read(path)["metadata"]
    assert refreshed["conflict_paths"] == []
    assert metadata["description"] == "Now published"
    assert metadata["likes"] == 9
    assert metadata["category"] == "Music"


def test_youtube_refresh_reports_earlier_filmot_values_as_conflicts(tmp_path):
    library, path = saved(tmp_path)
    enrich_saved_transcripts(library, [("topic", VIDEO_ID)], FakeClient([row()]))

    refreshed = library.replace_youtube_metadata(VIDEO_ID, "topic", {
        "video_id": VIDEO_ID, "provider": "youtube", "title": "YouTube title",
        "description": "YouTube description", "views": 9, "tags": ["music"],
        "metadata_observed_at": OBSERVED, "metadata_expires_at": EXPIRES,
    })

    metadata = read(path)["metadata"]
    assert {"/metadata/title", "/metadata/description", "/metadata/views"} <= set(
        refreshed["conflict_paths"]
    )
    assert metadata["title"] == "Recorded video title 001"
    assert metadata["views"] == 1763636326
    assert metadata["tags"] == ["music"]


def test_filmot_snapshot_never_replaces_the_youtube_provenance_marker(tmp_path):
    library = TranscriptLibrary(data_dir=tmp_path)
    path = library.save(VIDEO_ID, "topic", "A transcript body.", metadata={"title": "T"})
    library.enrich_metadata(
        VIDEO_ID, "topic", {"video_id": VIDEO_ID, "provider": "youtube-data-api", "likes": 3},
    )

    library.enrich_metadata(
        VIDEO_ID, "topic",
        {"video_id": VIDEO_ID, "provider": "filmot", "category": "Music"},
        provenance={"provider": "filmot"}, filmot_snapshot=True,
    )

    record = read(path)
    assert record["metadata_enrichment"]["provider"] == "youtube-data-api"
    assert record["filmot_enrichment"]["filled_fields"] == ["category"]
    assert library.youtube_metadata_inventory()[0]["legacy_adoptable"] is True


def test_enrichment_inventory_reports_markers_and_unreadable_records(tmp_path):
    library, path = saved(tmp_path)
    library.save(OTHER_ID, "topic", "Another body.", metadata={})
    enrich_saved_transcripts(library, [("topic", VIDEO_ID)], FakeClient([row()]))
    (path.parent / "zzz12345678.json").write_text("{not json", encoding="utf-8")

    rows = {item["video_id"]: item for item in library.filmot_enrichment_inventory()}

    assert rows[VIDEO_ID]["enriched_at"] is not None
    assert rows[OTHER_ID]["enriched_at"] is None
    assert rows["zzz12345678"]["error_type"] == "JSONDecodeError"
    assert [
        item["video_id"] for item in library.filmot_enrichment_inventory(video_id=OTHER_ID)
    ] == [OTHER_ID]
    assert library.filmot_enrichment_inventory(topic="missing") == []


# ---------------------------------------------------------------- save time


def test_save_time_enrichment_is_opt_out_and_silent_without_credentials(tmp_path, monkeypatch):
    library, _ = saved(tmp_path)
    never = MagicMock(side_effect=AssertionError("no client may be built"))

    monkeypatch.setenv("FILMOT_ENRICH_ON_SAVE", "off")
    assert enrich_after_save(library, [("topic", VIDEO_ID)], never, source="test") is None
    monkeypatch.setenv("FILMOT_ENRICH_ON_SAVE", "1")
    assert enrich_after_save(library, [], never, source="test") is None
    never.assert_not_called()
    unconfigured = MagicMock(side_effect=ProviderConfigurationError("no keys"))
    assert enrich_after_save(library, [("topic", VIDEO_ID)], unconfigured, source="test") is None


def test_save_time_enrichment_logs_one_event_and_survives_a_broken_client(tmp_path, monkeypatch):
    monkeypatch.setenv("FILMOT_ENRICH_ON_SAVE", "1")
    library, _ = saved(tmp_path)

    with patch("filmot.ledger.log_event") as log_event:
        summary = enrich_after_save(
            library, [("topic", VIDEO_ID), ("topic", VIDEO_ID)],
            lambda: FakeClient([row()]), source="test",
        )
    assert (summary["records"], summary["updated"]) == (1, 1)
    log_event.assert_called_once()
    assert log_event.call_args.args == ("filmot_enrichment",)
    assert log_event.call_args.kwargs["topic"] == "topic"
    assert log_event.call_args.kwargs["status"] == "completed"
    assert log_event.call_args.kwargs["updated"] == 1

    with patch("filmot.ledger.log_event") as log_event:
        summary = enrich_after_save(
            library, [("topic", VIDEO_ID)],
            MagicMock(side_effect=OSError("cache directory is read-only")), source="test",
        )
    assert summary["failed"] == 1
    assert log_event.call_args.kwargs["status"] == "failed"
    assert "cache directory is read-only" in log_event.call_args.kwargs["error"]


def test_save_time_enrichment_contains_unexpected_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("FILMOT_ENRICH_ON_SAVE", "1")
    library, _ = saved(tmp_path)

    class Unwritable:
        def enrich_metadata(self, *args, **kwargs):
            raise RuntimeError("disk vanished")

    with patch("filmot.ledger.log_event"):
        per_record = enrich_after_save(
            Unwritable(), [("topic", VIDEO_ID)], lambda: FakeClient([row()]), source="test",
        )
        with patch(
            "filmot.video_metadata.enrich_saved_transcripts", side_effect=KeyError("unexpected"),
        ):
            whole = enrich_after_save(
                library, [("topic", VIDEO_ID)], lambda: FakeClient(), source="test",
            )

    assert per_record["failed"] == 1
    assert per_record["outcomes"][0]["error"] == "RuntimeError: disk vanished"
    assert whole["failed"] == 1
    assert whole["outcomes"][0]["error"].startswith("KeyError")


def test_summary_line_reads_full_summaries_and_ledger_counts():
    full = {
        "updated": 2, "unchanged": 1, "not_returned": 0, "failed": 1,
        "lookup_errors": [{"error": "x"}],
    }
    expected = "Filmot metadata: 2 updated, 1 already complete, 1 failed, 1 lookup error"

    assert enrichment_summary_line(full) == expected
    assert enrichment_summary_line(dict(full, lookup_errors=1)) == expected
    assert enrichment_summary_line({"updated": 0}) == "Filmot metadata: 0 updated"
