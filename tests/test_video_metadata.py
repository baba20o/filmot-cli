"""Filmot /getvideos columns: parsing, batched lookups, and fill-only library enrichment."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    before = path.read_bytes()
    again = enrich_saved_transcripts(library, [("topic", VIDEO_ID)], FakeClient([row()]))
    assert again["unchanged"] == 1
    assert path.read_bytes() == before


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
    assert record["filmot_enrichment"]["filled_fields"] == ["caption_languages.automatic"]
    assert summary["outcomes"][0]["conflicts"] == 1


def test_existing_counts_keep_their_own_observation_date(tmp_path):
    library, path = saved(tmp_path, views=5)
    saved_date = read(path)["metadata"]["views_observed_at"]

    summary = enrich_saved_transcripts(library, [("topic", VIDEO_ID)], FakeClient([row()]))

    metadata = read(path)["metadata"]
    assert metadata["views"] == 5
    assert metadata["views_observed_at"] == saved_date
    assert summary["outcomes"][0]["conflicts"] >= 1


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
