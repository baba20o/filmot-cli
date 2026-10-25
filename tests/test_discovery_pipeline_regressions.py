"""Discovery snapshots and producer failures survive transcript handoff."""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from filmot.cli import cli


VIDEO = "abc12345678"
OBSERVED = "2026-10-01T12:00:00Z"
ROW = {
    "id": VIDEO, "title": "Index title", "channelname": "Index channel",
    "description": "Description from the supplied discovery page",
    "viewcount": 0, "likecount": 4, "duration": 240,
    "lastfetchdate": OBSERVED, "license": "CC",
    "autosubs": "en", "manualsubs": "fr",
    "channelsubcount": 123, "channelcountryname": "Canada",
}
TRANSCRIPT = {
    "video_id": VIDEO, "full_text": "Saved caption text.",
    "segments": [{"text": "Saved caption text.", "start": 0, "duration": 10}],
    "source": "youtube", "route": "direct", "routes_tried": ["direct"],
    "language": "en", "is_generated": False, "duration_seconds": 10,
    "segment_count": 1,
}


def artifact(status="completed", rows=None):
    return {
        "videos": [dict(ROW)] if rows is None else rows,
        "next_page": 2,
        "_filmot": {
            "schema": "filmot.result/v1", "command": "titles", "status": status,
            "errors": [] if status == "completed" else [{
                "type": "FilmotServiceError", "message": "Later discovery page failed", "stage": "api",
            }],
            "warnings": [],
        },
    }


@pytest.mark.parametrize("command", ["download", "transcript"])
def test_filmot_discovery_fields_are_saved_without_a_second_metadata_request(
    command, library, tmp_path, monkeypatch,
):
    monkeypatch.setattr("filmot.library.get_library", lambda: library)
    monkeypatch.setattr("filmot.transcript.get_transcript", lambda *args, **kwargs: dict(TRANSCRIPT))
    payload = artifact()
    path = tmp_path / "discovery.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    args = (["download", "--topic", "audit"] if command == "download" else
            ["transcript", VIDEO, "--save-to", "audit", "--discovery", str(path), "--raw"])
    with patch("filmot.ledger.log_result"), patch("filmot.ledger.log_event"), patch(
        "requests.sessions.Session.request", side_effect=AssertionError("unexpected network request")
    ):
        result = CliRunner().invoke(cli, args, input=json.dumps(payload))

    assert result.exit_code == 0, result.output
    record = library.get(VIDEO, "audit")
    metadata = record["metadata"]
    assert metadata["description"] == ROW["description"]
    assert metadata["views"] == 0
    assert metadata["views_observed_at"] == OBSERVED
    assert record["source"]["views_observed_at"] == OBSERVED
    assert metadata["filmot_observed_at"] == OBSERVED
    assert metadata["video_duration_seconds"] == 240
    assert metadata.get("youtube_duration") is None
    assert metadata.get("youtube_license") is None
    assert metadata["creative_commons"] is True
    assert metadata["channel_subscribers"] == 123
    assert metadata["caption_languages"] == {"automatic": ["en"], "manual": ["fr"]}
    assert record["filmot_enrichment"]["provider"] == "filmot"
    assert "metadata_enrichment" not in record


@pytest.mark.parametrize("command", ["download", "transcript"])
@pytest.mark.parametrize("duration", ["not a duration", "PT4M", -1, float("inf"), True],
                         ids=["arbitrary-text", "iso-string", "negative", "nonfinite", "boolean"])
def test_invalid_filmot_video_duration_never_enters_saved_metadata(
    command, duration, library, tmp_path, monkeypatch,
):
    monkeypatch.setattr("filmot.library.get_library", lambda: library)
    monkeypatch.setattr("filmot.transcript.get_transcript", lambda *args, **kwargs: dict(TRANSCRIPT))
    payload = artifact(rows=[dict(ROW, duration=duration)])
    # 1e999 is valid JSON numeric syntax but decodes to infinity, exercising
    # normalization through both commands instead of a JSON-constant error.
    encoded = json.dumps(payload).replace("Infinity", "1e999")
    path = tmp_path / "discovery.json"
    path.write_text(encoded, encoding="utf-8")
    args = (["download", "--topic", "audit"] if command == "download" else
            ["transcript", VIDEO, "--save-to", "audit", "--discovery", str(path), "--raw"])
    with patch("filmot.ledger.log_result"), patch("filmot.ledger.log_event"), patch(
        "requests.sessions.Session.request", side_effect=AssertionError("unexpected network request")
    ):
        result = CliRunner().invoke(cli, args, input=encoded)

    assert result.exit_code == 0, result.output
    record = library.get(VIDEO, "audit")
    assert record["metadata"].get("video_duration_seconds") is None
    assert record["metadata"].get("youtube_duration") is None
    assert record["metadata"]["duration_seconds"] == TRANSCRIPT["duration_seconds"]
    assert record["segments"] == TRANSCRIPT["segments"]


@pytest.mark.parametrize("command", ["download", "transcript"])
def test_youtube_discovery_keeps_iso_video_duration(command, library, tmp_path, monkeypatch):
    monkeypatch.setattr("filmot.library.get_library", lambda: library)
    monkeypatch.setattr("filmot.transcript.get_transcript", lambda *args, **kwargs: dict(TRANSCRIPT))
    payload = artifact(rows=[{
        "video_id": VIDEO, "provider": "youtube", "title": "YouTube title",
        "channel_title": "YouTube channel", "duration": "PT4M",
    }])
    path = tmp_path / "discovery.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    args = (["download", "--topic", "audit"] if command == "download" else
            ["transcript", VIDEO, "--save-to", "audit", "--discovery", str(path), "--raw"])
    with patch("filmot.ledger.log_result"), patch("filmot.ledger.log_event"), patch(
        "requests.sessions.Session.request", side_effect=AssertionError("unexpected network request")
    ):
        result = CliRunner().invoke(cli, args, input=json.dumps(payload))

    assert result.exit_code == 0, result.output
    metadata = library.get(VIDEO, "audit")["metadata"]
    assert metadata["youtube_duration"] == "PT4M"
    assert metadata.get("video_duration_seconds") is None
    assert metadata["duration_seconds"] == TRANSCRIPT["duration_seconds"]


@pytest.mark.parametrize("command", ["download", "transcript"])
@pytest.mark.parametrize("duration", [-42, "untrusted duration"])
def test_invalid_canonical_filmot_fields_are_removed_before_save(
    command, duration, library, tmp_path, monkeypatch,
):
    monkeypatch.setattr("filmot.library.get_library", lambda: library)
    monkeypatch.setattr("filmot.transcript.get_transcript", lambda *args, **kwargs: dict(TRANSCRIPT))
    invalid_fields = {
        "video_duration_seconds": duration,
        "filmot_observed_at": "not-a-snapshot-date",
        "creative_commons": "yes please",
        "channel_subscribers": -5,
        "dislikes": True,
        "caption_languages": {"automatic": None, "manual": "fr"},
        "is_unlisted": "maybe",
    }
    payload = artifact(rows=[{
        "video_id": VIDEO, "provider": "filmot", "title": "Canonical title",
        "channel_title": "Canonical channel", "views": 4,
        "provider_fields": invalid_fields,
    }])
    path = tmp_path / "canonical-discovery.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    args = (["download", "--topic", "audit"] if command == "download" else
            ["transcript", VIDEO, "--save-to", "audit", "--discovery", str(path), "--raw"])
    with patch("filmot.ledger.log_result"), patch("filmot.ledger.log_event"), patch(
        "requests.sessions.Session.request", side_effect=AssertionError("unexpected network request")
    ):
        result = CliRunner().invoke(cli, args, input=json.dumps(payload))

    assert result.exit_code == 0, result.output
    record = library.get(VIDEO, "audit")
    metadata = record["metadata"]
    for field in invalid_fields:
        assert metadata.get(field) is None, field
        assert field not in metadata.get("provider_fields", {}), field
    assert metadata.get("views_observed_at") is None
    assert record["source"].get("views_observed_at") is None
    assert record.get("filmot_enrichment", {}).get("observed_at") is None
    assert metadata["duration_seconds"] == TRANSCRIPT["duration_seconds"]
    assert record["segments"] == TRANSCRIPT["segments"]


@pytest.mark.parametrize("command", ["download", "transcript"])
def test_valid_canonical_filmot_fields_keep_snapshot_and_timing(
    command, library, tmp_path, monkeypatch,
):
    monkeypatch.setattr("filmot.library.get_library", lambda: library)
    monkeypatch.setattr("filmot.transcript.get_transcript", lambda *args, **kwargs: dict(TRANSCRIPT))
    fields = {
        "video_duration_seconds": 240,
        "filmot_observed_at": OBSERVED,
        "creative_commons": False,
        "channel_subscribers": 0,
        "dislikes": 0,
        "caption_languages": {"automatic": [], "manual": ["fr"]},
        "is_unlisted": False,
        "is_livestream": True,
        "partial_metadata": False,
        "channel_country": "Canada",
    }
    payload = artifact(rows=[{
        "video_id": VIDEO, "provider": "filmot", "title": "Canonical title",
        "channel_title": "Canonical channel", "views": 4,
        "provider_fields": fields,
    }])
    path = tmp_path / "canonical-discovery.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    args = (["download", "--topic", "audit"] if command == "download" else
            ["transcript", VIDEO, "--save-to", "audit", "--discovery", str(path), "--raw"])
    with patch("filmot.ledger.log_result"), patch("filmot.ledger.log_event"), patch(
        "requests.sessions.Session.request", side_effect=AssertionError("unexpected network request")
    ):
        result = CliRunner().invoke(cli, args, input=json.dumps(payload))

    assert result.exit_code == 0, result.output
    record = library.get(VIDEO, "audit")
    metadata = record["metadata"]
    for field, value in fields.items():
        assert metadata[field] == value, field
        assert metadata["provider_fields"][field] == value, field
    assert metadata["views_observed_at"] == OBSERVED
    assert record["source"]["views_observed_at"] == OBSERVED
    assert record["filmot_enrichment"]["observed_at"] == OBSERVED
    assert metadata["duration_seconds"] == TRANSCRIPT["duration_seconds"]
    assert record["segments"] == TRANSCRIPT["segments"]


@pytest.mark.parametrize("status", ["failed", "interrupted"])
@pytest.mark.parametrize("rows", [[], [ROW]])
def test_failed_producer_never_starts_download_or_reports_empty_success(status, rows):
    with patch("filmot.commands.transcript._bulk_download_transcripts") as bulk, patch(
        "filmot.ledger.log_result"
    ) as log_result:
        result = CliRunner().invoke(
            cli, ["download", "--topic", "audit"], input=json.dumps(artifact(status, rows)),
        )
    assert result.exit_code == 1
    bulk.assert_not_called()
    outcome = log_result.call_args.args[1]
    assert outcome.status_value == "failed"
    assert outcome.data["discovery"]["status"] == status
    assert outcome.errors[0].type == "FilmotServiceError"


@pytest.mark.parametrize("selected", [0, 1])
def test_partial_producer_processes_available_rows_and_preserves_continuation(selected):
    payload = artifact("partial", [ROW] if selected else [])
    summary = {"selected": selected, "saved": selected, "skipped": 0, "failed": 0}
    with patch("filmot.commands.transcript._bulk_download_transcripts", return_value=summary) as bulk, patch(
        "filmot.ledger.log_result"
    ) as log_result:
        result = CliRunner().invoke(cli, ["download", "--topic", "audit"], input=json.dumps(payload))
    assert result.exit_code == 1
    assert bulk.call_args.args[0] == payload
    outcome = log_result.call_args.args[1]
    assert outcome.status_value == "partial"
    assert outcome.data["saved"] == selected
    assert outcome.data["discovery"]["next_page"] == 2
    assert outcome.errors[0].type == "FilmotServiceError"
    assert "--page 2" in result.output


def test_bare_discovery_candidates_remain_supported():
    with patch("filmot.commands.transcript._bulk_download_transcripts", return_value={
        "selected": 1, "saved": 1, "skipped": 0, "failed": 0,
    }), patch("filmot.ledger.log_result") as log_result:
        result = CliRunner().invoke(cli, ["download", "--topic", "audit"], input=json.dumps([ROW]))
    assert result.exit_code == 0, result.output
    assert log_result.call_args.args[1].status_value == "completed"


def test_failed_discovery_file_is_rejected_before_transcript_acquisition(tmp_path):
    path = tmp_path / "failed.json"
    path.write_text(json.dumps(artifact("failed")), encoding="utf-8")
    with patch("filmot.transcript.get_transcript") as fetch, patch("filmot.ledger.log_event"):
        result = CliRunner().invoke(cli, [
            "transcript", VIDEO, "--save-to", "audit", "--discovery", str(path), "--raw",
        ])
    assert result.exit_code == 1
    fetch.assert_not_called()
    assert "Discovery artifact reports failed" in result.output
