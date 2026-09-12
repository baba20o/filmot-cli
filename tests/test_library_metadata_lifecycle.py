import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from filmot.library import (
    TranscriptLibrary,
    YOUTUBE_METADATA_AUDIT_LIMIT,
    YOUTUBE_METADATA_PROVIDER,
    YOUTUBE_METADATA_SCHEMA,
)


VIDEO_ID = "abc12345678"
SECOND_ID = "def12345678"
THIRD_ID = "ghi12345678"
FOURTH_ID = "jkl12345678"
FIFTH_ID = "mno12345678"
NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
OBSERVED = "2026-09-12T10:00:00Z"
EXPIRES = "2026-10-12T10:00:00Z"
REQUEST_REF = "sha256:" + "a" * 64


def _library_record(tmp_path, video_id=VIDEO_ID, metadata=None):
    library = TranscriptLibrary(data_dir=tmp_path)
    path = library.save(
        video_id,
        "topic",
        "A transcript body that must remain byte-for-byte meaningful.",
        metadata={
            "acquisition": {"route": "captions", "attempts": 1},
            **(metadata or {}),
        },
        segments=[{"text": "A transcript body", "start": 2.0}],
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["citations"] = [{"at": 2.0, "note": "keep"}]
    raw["unrelated"] = {"nested": [1, 2, 3]}
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return library, path, raw


def _candidate(**overrides):
    candidate = {
        "video_id": VIDEO_ID,
        "provider": "youtube-data-api-v3",
        "title": "Fresh title",
        "description": "Fresh description",
        "channel_title": "Fresh channel",
        "channel_id": "UCabcdefghijklmnopqrstuv",
        "published_at": "2026-09-01T00:00:00Z",
        "views": 0,
        "likes": 7,
        "comments": None,
        "duration": "PT2M",
        "paid_product_placement": False,
        "metadata_observed_at": OBSERVED,
        "metadata_expires_at": EXPIRES,
        "provider_fields": {
            "definition": "hd",
            "custom/path~key": {"kept": True},
        },
    }
    candidate.update(overrides)
    return candidate


def _raw(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_replace_creates_owned_lifecycle_and_value_free_audit(tmp_path):
    library, path, before = _library_record(tmp_path)

    result = library.replace_youtube_metadata(
        VIDEO_ID,
        "topic",
        _candidate(),
        request_ref=REQUEST_REF,
    )

    after = _raw(path)
    assert result["state"] == "current"
    assert after["saved_at"] == before["saved_at"]
    for field in ("transcript", "segments", "citations", "unrelated"):
        assert after[field] == before[field]
    assert after["metadata"]["acquisition"] == before["metadata"]["acquisition"]
    assert after["metadata"]["views"] == 0
    assert after["metadata"]["paid_product_placement"] is False
    assert after["metadata"]["youtube_duration"] == "PT2M"
    assert after["source"]["views"] == 0

    lifecycle = after["metadata_lifecycle"]["youtube"]
    assert lifecycle["schema"] == YOUTUBE_METADATA_SCHEMA
    assert lifecycle["provider"] == YOUTUBE_METADATA_PROVIDER
    assert lifecycle["resource"] == "video"
    assert lifecycle["id"] == VIDEO_ID
    assert lifecycle["observed_at"] == OBSERVED
    assert lifecycle["expires_at"] == EXPIRES
    assert lifecycle["state"] == "current"
    assert lifecycle["request_ref"] == REQUEST_REF
    assert "/metadata/views" in lifecycle["owned_paths"]
    assert "/source/views" in lifecycle["owned_paths"]
    assert (
        "/metadata/provider_fields/custom~1path~0key"
        in lifecycle["owned_paths"]
    )
    audit_text = json.dumps(lifecycle["audit"])
    assert "Fresh title" not in audit_text
    assert "Fresh description" not in audit_text
    assert set(lifecycle["audit"][0]) <= {
        "action",
        "at",
        "state",
        "request_ref",
        "changed_paths",
        "removed_paths",
        "conflict_paths",
    }


def test_refresh_replaces_values_and_clears_omitted_owned_fields(tmp_path):
    library, path, before = _library_record(tmp_path)
    first = _candidate(
        provider_fields={"definition": "hd", "obsolete": "old"}
    )
    library.replace_youtube_metadata(VIDEO_ID, "topic", first)

    second = _candidate(
        title="Changed title",
        description=None,
        likes=0,
        provider_fields={"definition": "sd"},
        metadata_observed_at="2026-09-13T10:00:00Z",
        metadata_expires_at="2026-10-13T10:00:00Z",
    )
    result = library.replace_youtube_metadata(VIDEO_ID, "topic", second)

    after = _raw(path)
    assert after["metadata"]["title"] == "Changed title"
    assert after["source"]["title"] == "Changed title"
    assert after["metadata"]["likes"] == 0
    assert "description" not in after["metadata"]
    assert after["metadata"]["provider_fields"]["definition"] == "sd"
    assert "obsolete" not in after["metadata"]["provider_fields"]
    assert "/metadata/description" in result["removed_paths"]
    assert (
        "/metadata/provider_fields/obsolete" in result["removed_paths"]
    )
    assert "/metadata/title" in result["changed_paths"]
    assert after["metadata_lifecycle"]["youtube"]["observed_at"] == (
        "2026-09-13T10:00:00Z"
    )
    assert after["saved_at"] == before["saved_at"]
    for field in ("transcript", "segments", "citations", "unrelated"):
        assert after[field] == before[field]


def test_replace_preserves_unowned_collision_and_does_not_claim_it(tmp_path):
    library, path, _ = _library_record(
        tmp_path,
        metadata={"title": "Operator title"},
    )

    result = library.replace_youtube_metadata(VIDEO_ID, "topic", _candidate())

    after = _raw(path)
    assert after["metadata"]["title"] == "Operator title"
    assert after["source"]["title"] == "Operator title"
    assert "/metadata/title" in result["conflict_paths"]
    assert "/source/title" in result["conflict_paths"]
    assert "/metadata/title" not in result["owned_paths"]


def test_replace_adopts_only_explicitly_youtube_legacy_fields(tmp_path):
    library, path, before = _library_record(
        tmp_path,
        metadata={
            "title": "Old API title",
            "channel": "Old API channel",
            "views": 99,
            "duration": "PT1M",
            "discovery_provider": "youtube",
            "discovery_provenance": {"query": "keep local provenance"},
            "provider_fields": {
                "definition": "sd",
                "obsolete": "remove",
            },
        },
    )

    result = library.replace_youtube_metadata(
        VIDEO_ID, "topic", _candidate(provider_fields={"definition": "hd"})
    )

    after = _raw(path)
    assert result["adopted_legacy"] is True
    assert after["metadata"]["title"] == "Fresh title"
    assert after["metadata"]["channel"] == "Fresh channel"
    assert after["metadata"]["views"] == 0
    assert "duration" not in after["metadata"]
    assert "obsolete" not in after["metadata"]["provider_fields"]
    assert after["metadata"]["discovery_provider"] == "youtube"
    assert after["metadata"]["discovery_provenance"] == {
        "query": "keep local provenance"
    }
    assert after["metadata"]["acquisition"] == before["metadata"]["acquisition"]
    assert after["metadata_lifecycle"]["youtube"]["audit"][0]["action"] == (
        "adopt"
    )


def test_missing_api_item_purges_values_and_records_neutral_state(tmp_path):
    library, path, before = _library_record(tmp_path)
    library.replace_youtube_metadata(VIDEO_ID, "topic", _candidate())

    result = library.replace_youtube_metadata(
        VIDEO_ID,
        "topic",
        None,
        observed_at="2026-09-14T10:00:00Z",
        expires_at="2026-10-14T10:00:00Z",
        request_ref=REQUEST_REF,
    )

    after = _raw(path)
    lifecycle = after["metadata_lifecycle"]["youtube"]
    assert result["state"] == "not_returned"
    assert lifecycle["state"] == "not_returned"
    assert lifecycle["owned_paths"] == []
    assert "title" not in after["metadata"]
    assert "views" not in after["metadata"]
    assert "title" not in after["source"]
    assert "deleted" not in json.dumps(lifecycle).lower()
    assert after["transcript"] == before["transcript"]
    assert after["metadata"]["acquisition"] == before["metadata"]["acquisition"]


def test_purge_removes_only_owned_paths_without_an_api(tmp_path):
    library, path, before = _library_record(tmp_path)
    library.replace_youtube_metadata(VIDEO_ID, "topic", _candidate())
    raw = _raw(path)
    raw["metadata"]["operator_note"] = "keep"
    raw["source"]["local_label"] = "keep"
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    result = library.purge_youtube_metadata(
        VIDEO_ID,
        "topic",
        reason="expired",
        now=NOW,
    )

    after = _raw(path)
    assert result["state"] == "purged"
    assert result["removed_paths"]
    assert after["metadata_lifecycle"]["youtube"]["state"] == "purged"
    assert after["metadata_lifecycle"]["youtube"]["owned_paths"] == []
    assert after["metadata"]["operator_note"] == "keep"
    assert after["source"]["local_label"] == "keep"
    assert after["metadata"]["acquisition"] == before["metadata"]["acquisition"]
    for field in ("transcript", "segments", "citations", "unrelated", "saved_at"):
        assert after[field] == before[field]


def test_purge_unmanaged_record_is_byte_for_byte_noop(tmp_path):
    library, path, _ = _library_record(tmp_path)
    before = path.read_bytes()

    result = library.purge_youtube_metadata(VIDEO_ID, "topic", now=NOW)

    assert result["status"] == "noop"
    assert result["state"] == "unmanaged"
    assert path.read_bytes() == before


def test_inventory_is_offline_and_classifies_current_expired_and_unmanaged(
    tmp_path,
):
    library = TranscriptLibrary(data_dir=tmp_path)
    for video_id in (VIDEO_ID, SECOND_ID, THIRD_ID, FOURTH_ID, FIFTH_ID):
        library.save(video_id, "topic", "Transcript", metadata={})
    library.replace_youtube_metadata(
        VIDEO_ID,
        "topic",
        _candidate(),
    )
    library.replace_youtube_metadata(
        SECOND_ID,
        "topic",
        _candidate(
            video_id=SECOND_ID,
            metadata_observed_at="2026-07-01T00:00:00Z",
            metadata_expires_at="2026-07-31T00:00:00Z",
        ),
    )
    third_path = library.transcripts_dir / "topic" / f"{THIRD_ID}.json"
    third = _raw(third_path)
    third["metadata"]["discovery_provider"] = "youtube-data-api-v3"
    third["metadata"]["metadata_observed_at"] = "2026-07-01T00:00:00Z"
    third["metadata"]["metadata_expires_at"] = "2026-07-31T00:00:00Z"
    third_path.write_text(json.dumps(third, indent=2) + "\n", encoding="utf-8")
    fifth_path = library.transcripts_dir / "topic" / f"{FIFTH_ID}.json"
    fifth = _raw(fifth_path)
    fifth["metadata"]["discovery_provider"] = "youtube"
    fifth["metadata"]["provider_fields"] = {
        "metadata_observed_at": OBSERVED,
        "metadata_expires_at": EXPIRES,
    }
    fifth_path.write_text(json.dumps(fifth, indent=2) + "\n", encoding="utf-8")

    rows = library.youtube_metadata_inventory(now=NOW)
    by_id = {row["video_id"]: row for row in rows}

    assert by_id[VIDEO_ID]["classification"] == "current"
    assert by_id[VIDEO_ID]["managed"] is True
    assert by_id[SECOND_ID]["classification"] == "expired"
    assert by_id[THIRD_ID]["classification"] == "expired"
    assert by_id[THIRD_ID]["legacy_adoptable"] is True
    assert by_id[THIRD_ID]["managed"] is False
    assert by_id[THIRD_ID]["expires_at"] == "2026-07-31T00:00:00Z"
    assert by_id[FOURTH_ID]["classification"] == "unmanaged"
    assert by_id[FOURTH_ID]["legacy_adoptable"] is False
    assert by_id[FIFTH_ID]["classification"] == "current"
    assert by_id[FIFTH_ID]["legacy_adoptable"] is True
    assert by_id[FIFTH_ID]["observed_at"] == OBSERVED
    assert by_id[FIFTH_ID]["expires_at"] == EXPIRES
    assert library.youtube_metadata_inventory(
        topic="topic", video_id=SECOND_ID, now=NOW
    )[0]["classification"] == "expired"


def test_malformed_lifecycle_blocks_mutation_and_inventory_marks_it_invalid(
    tmp_path,
):
    library, path, _ = _library_record(tmp_path)
    raw = _raw(path)
    raw["metadata_lifecycle"] = {
        "youtube": {
            "schema": YOUTUBE_METADATA_SCHEMA,
            "provider": YOUTUBE_METADATA_PROVIDER,
            "resource": "video",
            "id": VIDEO_ID,
            "observed_at": OBSERVED,
            "expires_at": EXPIRES,
            "state": "current",
            "owned_paths": ["/transcript"],
            "audit": [],
        }
    }
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(ValueError, match="unsupported provider-owned path"):
        library.replace_youtube_metadata(VIDEO_ID, "topic", _candidate())

    assert path.read_bytes() == before
    status = library.youtube_metadata_inventory(now=NOW)[0]
    assert status["classification"] == "unmanaged"
    assert status["state"] == "invalid"
    assert status["invalid_lifecycle"] is True


def test_lifecycle_audit_is_bounded_and_old_events_are_value_free(tmp_path):
    library, path, _ = _library_record(tmp_path)
    library.replace_youtube_metadata(VIDEO_ID, "topic", _candidate())
    raw = _raw(path)
    lifecycle = raw["metadata_lifecycle"]["youtube"]
    lifecycle["audit"] = [
        {
            "action": "refresh",
            "at": OBSERVED,
            "state": "current",
            "changed_paths": ["/metadata/title"],
            "secret_old_value": "must-not-survive",
        }
        for _ in range(YOUTUBE_METADATA_AUDIT_LIMIT + 8)
    ]
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    library.replace_youtube_metadata(
        VIDEO_ID,
        "topic",
        _candidate(
            metadata_observed_at="2026-09-13T10:00:00Z",
            metadata_expires_at="2026-10-13T10:00:00Z",
        ),
    )

    audit = _raw(path)["metadata_lifecycle"]["youtube"]["audit"]
    assert len(audit) == YOUTUBE_METADATA_AUDIT_LIMIT
    assert audit[-1]["action"] == "refresh"
    assert "must-not-survive" not in json.dumps(audit)


def test_replace_is_atomic_when_publish_fails(tmp_path, monkeypatch):
    library, path, _ = _library_record(tmp_path)
    before = path.read_bytes()

    def fail_replace(source, destination, attempts=6):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("filmot.library._replace_with_retry", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        library.replace_youtube_metadata(VIDEO_ID, "topic", _candidate())

    assert path.read_bytes() == before
    assert list(path.parent.glob("*.tmp")) == []


def test_concurrent_refreshes_serialize_complete_observations(tmp_path):
    library, path, _ = _library_record(tmp_path)

    candidates = [
        _candidate(
            title="First observation",
            metadata_observed_at="2026-09-12T10:00:00Z",
            metadata_expires_at="2026-10-12T10:00:00Z",
        ),
        _candidate(
            title="Second observation",
            metadata_observed_at="2026-09-13T10:00:00Z",
            metadata_expires_at="2026-10-13T10:00:00Z",
        ),
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda item: library.replace_youtube_metadata(
                    VIDEO_ID, "topic", item
                ),
                candidates,
            )
        )

    after = _raw(path)
    lifecycle = after["metadata_lifecycle"]["youtube"]
    assert all(result["status"] == "updated" for result in results)
    assert len(lifecycle["audit"]) == 2
    assert {event["at"] for event in lifecycle["audit"]} == {
        "2026-09-12T10:00:00Z",
        "2026-09-13T10:00:00Z",
    }
    assert after["metadata"]["title"] in {
        "First observation",
        "Second observation",
    }


@pytest.mark.parametrize(
    "observed_at,expires_at",
    [
        ("2026-09-12T10:00:00", EXPIRES),
        (OBSERVED, "2026-10-13T10:00:01Z"),
        (OBSERVED, OBSERVED),
    ],
)
def test_replace_rejects_unsafe_observation_windows_before_writing(
    tmp_path, observed_at, expires_at
):
    library, path, _ = _library_record(tmp_path)
    before = path.read_bytes()

    with pytest.raises(ValueError):
        library.replace_youtube_metadata(
            VIDEO_ID,
            "topic",
            _candidate(
                metadata_observed_at=observed_at,
                metadata_expires_at=expires_at,
            ),
        )

    assert path.read_bytes() == before
