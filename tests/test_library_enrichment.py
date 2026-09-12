import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from filmot.library import TranscriptLibrary


VIDEO_ID = "abc12345678"


def _saved_record(tmp_path):
    library = TranscriptLibrary(data_dir=tmp_path)
    path = library.save(
        VIDEO_ID,
        "topic",
        "A preserved transcript body.",
        metadata={
            "title": "Unknown",
            "channel": "Known Channel",
            "views": 0,
            "acquisition": {"route": "captions"},
        },
        segments=[{"text": "A preserved transcript body.", "start": 3.0}],
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["citations"] = [{"at": 3.0, "note": "keep"}]
    raw["unrelated"] = {"nested": [1, 2, 3]}
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return library, path, raw


def test_enrichment_fills_unknown_only_and_preserves_record_content(tmp_path):
    library, path, before = _saved_record(tmp_path)

    result = library.enrich_metadata(
        VIDEO_ID,
        "topic",
        {
            "video_id": VIDEO_ID,
            "provider": "youtube-data-api",
            "title": "Observed title",
            "channel_title": "Conflicting channel",
            "channel_id": "UC-observed",
            "views": 99,
            "likes": 0,
            "caption": True,
            "tags": ["research", "primary-source"],
            "metadata_observed_at": "2026-09-12T10:00:00Z",
            "metadata_expires_at": "2026-10-12T10:00:00Z",
        },
        provenance={"discovery_ref": "sha256:example"},
    )

    after = json.loads(path.read_text(encoding="utf-8"))
    assert result["status"] == "updated"
    assert after["saved_at"] == before["saved_at"]
    for field in ("transcript", "segments", "citations", "unrelated"):
        assert after[field] == before[field]
    assert after["metadata"]["acquisition"] == {"route": "captions"}
    assert after["metadata"]["title"] == "Observed title"
    assert after["metadata"]["channel"] == "Known Channel"
    assert after["metadata"]["views"] == 0
    assert after["metadata"]["likes"] == 0
    assert after["metadata"]["caption"] is True
    assert after["source"]["title"] == "Observed title"
    assert after["source"]["channel"] == "Known Channel"
    assert after["source"]["views"] == 0
    assert after["metadata_enrichment"]["provider"] == "youtube-data-api"
    assert after["metadata_enrichment"]["discovery_ref"] == "sha256:example"
    assert any(
        item["field"] == "channel" for item in result["conflicts"]
    )
    assert any(item["field"] == "views" for item in result["conflicts"])


def test_repeat_enrichment_is_a_byte_for_byte_noop(tmp_path):
    library, path, _ = _saved_record(tmp_path)
    candidate = {
        "video_id": VIDEO_ID,
        "title": "Observed title",
        "metadata_observed_at": "2026-09-12T10:00:00Z",
    }
    assert library.enrich_metadata(VIDEO_ID, "topic", candidate)["status"] == "updated"
    first = path.read_bytes()

    result = library.enrich_metadata(VIDEO_ID, "topic", candidate)

    assert result["status"] == "noop"
    assert path.read_bytes() == first


def test_enrichment_rejects_identity_mismatch_before_writing(tmp_path):
    library, path, _ = _saved_record(tmp_path)
    before = path.read_bytes()

    with pytest.raises(ValueError, match="does not match"):
        library.enrich_metadata(
            VIDEO_ID,
            "topic",
            {"video_id": "other123456", "title": "Wrong source"},
        )

    assert path.read_bytes() == before


def test_enrichment_atomic_replace_failure_keeps_original(tmp_path, monkeypatch):
    library, path, _ = _saved_record(tmp_path)
    before = path.read_bytes()

    def fail_replace(source, destination, attempts=6):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("filmot.library._replace_with_retry", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        library.enrich_metadata(
            VIDEO_ID,
            "topic",
            {"video_id": VIDEO_ID, "title": "Observed title"},
        )

    assert path.read_bytes() == before
    assert list(path.parent.glob("*.tmp")) == []


def test_concurrent_enrichments_do_not_lose_each_others_fields(tmp_path):
    library, path, _ = _saved_record(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                library.enrich_metadata,
                VIDEO_ID,
                "topic",
                {"video_id": VIDEO_ID, "title": "Observed title"},
            ),
            pool.submit(
                library.enrich_metadata,
                VIDEO_ID,
                "topic",
                {"video_id": VIDEO_ID, "channel_id": "UC-observed"},
            ),
        ]
        [future.result() for future in futures]

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["metadata"]["title"] == "Observed title"
    assert after["metadata"]["channel_id"] == "UC-observed"
    assert after["source"]["title"] == "Observed title"
    assert after["source"]["channel_id"] == "UC-observed"


def test_enrichment_promotes_and_retains_rich_provider_fields(tmp_path):
    library = TranscriptLibrary(data_dir=tmp_path)
    library.save("video-rich", "topic", "Transcript", metadata={})

    result = library.enrich_metadata(
        "video-rich",
        "topic",
        {
            "video_id": "video-rich",
            "provider": "youtube-data-api-v3",
            "provider_fields": {
                "definition": "hd",
                "paid_product_placement": False,
                "metadata_observed_at": "2026-09-12T10:00:00Z",
                "metadata_expires_at": "2026-10-12T10:00:00Z",
                "topic_ids": ["/m/example"],
            },
        },
    )

    assert result["status"] == "updated"
    stored = library.get("video-rich", "topic")
    assert stored["metadata"]["definition"] == "hd"
    assert stored["metadata"]["paid_product_placement"] is False
    assert stored["metadata"]["metadata_observed_at"] == (
        "2026-09-12T10:00:00Z"
    )
    assert stored["metadata"]["topic_ids"] == ["/m/example"]
    assert stored["metadata"]["provider_fields"] == {
        "definition": "hd",
        "paid_product_placement": False,
        "metadata_observed_at": "2026-09-12T10:00:00Z",
        "metadata_expires_at": "2026-10-12T10:00:00Z",
        "topic_ids": ["/m/example"],
    }
    assert stored["metadata_enrichment"]["observed_at"] == (
        "2026-09-12T10:00:00Z"
    )
