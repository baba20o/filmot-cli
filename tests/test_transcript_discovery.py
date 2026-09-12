import json
from unittest.mock import patch

from click.testing import CliRunner

from filmot.cli import cli


VIDEO_ID = "abc12345678"


def _artifact(path, video_id=VIDEO_ID):
    payload = {
        "query": "fresh evidence",
        "videos": [{
            "video_id": video_id,
            "title": "Discovery title",
            "channel_title": "Discovery channel",
            "channel_id": "UC-discovery",
            "published_at": "2026-09-12T09:00:00Z",
            "views": 0,
            "metadata_observed_at": "2026-09-12T10:00:00Z",
            "metadata_expires_at": "2026-10-12T10:00:00Z",
        }],
        "_filmot": {
            "schema": "filmot.result/v1",
            "command": "yt-search",
            "status": "completed",
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _transcript():
    return {
        "video_id": VIDEO_ID,
        "language": "en",
        "full_text": "Transcript content from the selected video.",
        "segments": [{"text": "Transcript content", "start": 0.0, "duration": 1.0}],
        "source": "youtube",
        "route": "direct",
        "routes_tried": ["direct"],
    }


def test_manual_save_uses_exact_discovery_metadata_without_refetch(tmp_path):
    artifact = _artifact(tmp_path / "discovery.json")
    with (
        patch("filmot.transcript.get_transcript", return_value=_transcript()),
        patch("filmot.library.get_library") as get_library,
        patch("filmot.commands.transcript.FilmotClient") as filmot_client,
        patch("filmot.ledger.log_event") as log_event,
        patch("filmot.ledger.log_result") as log_result,
    ):
        library = get_library.return_value
        library.exists.return_value = False
        library.save.return_value = tmp_path / "saved.json"
        result = CliRunner().invoke(
            cli,
            [
                "transcript",
                VIDEO_ID,
                "--save-to",
                "topic",
                "--discovery",
                str(artifact),
                "--raw",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["_filmot"]["warnings"] == []
    filmot_client.assert_not_called()
    metadata = library.save.call_args.kwargs["metadata"]
    assert metadata["title"] == "Discovery title"
    assert metadata["channel"] == "Discovery channel"
    assert metadata["channel_id"] == "UC-discovery"
    assert metadata["views"] == 0
    assert metadata["discovery_provider"] == "youtube"
    assert metadata["discovery_provenance"]["discovery_ref"].startswith(
        "sha256:"
    )
    saved = next(
        call for call in log_event.call_args_list
        if call.args[0] == "transcript_save"
        and call.kwargs.get("status") == "saved"
    )
    assert saved.kwargs["discovery_ref"].startswith("sha256:")
    assert log_result.call_args.kwargs["data"]["discovery_ref"].startswith(
        "sha256:"
    )
    lifecycle = library.replace_youtube_metadata.call_args
    assert lifecycle.args[:2] == (VIDEO_ID, "topic")
    assert lifecycle.args[2]["provider"] == "youtube"
    assert lifecycle.kwargs["request_ref"].startswith("sha256:")


def test_discovery_mismatch_fails_before_transcript_or_library(tmp_path):
    artifact = _artifact(tmp_path / "wrong.json", "def12345678")
    with (
        patch("filmot.transcript.get_transcript") as get_transcript,
        patch("filmot.library.get_library") as get_library,
    ):
        result = CliRunner().invoke(
            cli,
            [
                "transcript",
                VIDEO_ID,
                "--save-to",
                "topic",
                "--discovery",
                str(artifact),
                "--raw",
            ],
        )

    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert payload["_filmot"]["errors"][0]["stage"] == "discovery"
    assert "no candidate matching" in payload["error"].lower()
    get_transcript.assert_not_called()
    get_library.assert_not_called()


def test_existing_record_metadata_enrichment_failure_is_nonfatal(tmp_path):
    artifact = _artifact(tmp_path / "discovery.json")
    with (
        patch("filmot.transcript.get_transcript", return_value=_transcript()),
        patch("filmot.library.get_library") as get_library,
        patch("filmot.ledger.log_event") as log_event,
        patch("filmot.ledger.log_result") as log_result,
    ):
        library = get_library.return_value
        library.exists.return_value = True
        library.replace_youtube_metadata.side_effect = OSError(
            "metadata store busy"
        )
        result = CliRunner().invoke(
            cli,
            [
                "transcript",
                VIDEO_ID,
                "--save-to",
                "topic",
                "--discovery",
                str(artifact),
                "--raw",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["_filmot"]["status"] == "completed"
    assert "metadata store busy" in payload["_filmot"]["warnings"][0]
    assert log_result.call_args.args[1].to_raw_dict() == payload
    failed = next(
        call for call in log_event.call_args_list
        if call.args[0] == "metadata_enrichment"
    )
    assert failed.kwargs["status"] == "failed"
    library.save.assert_not_called()


def test_filmot_result_envelope_backfills_missing_manual_metadata(tmp_path):
    with (
        patch("filmot.transcript.get_transcript", return_value=_transcript()),
        patch("filmot.library.get_library") as get_library,
        patch("filmot.commands.transcript.FilmotClient") as filmot_client,
        patch("filmot.ledger.log_event"),
        patch("filmot.ledger.log_result"),
    ):
        library = get_library.return_value
        library.exists.return_value = False
        library.save.return_value = tmp_path / "saved.json"
        filmot_client.return_value.get_videos.return_value = {
            "result": [{
                "id": VIDEO_ID,
                "title": "Filmot title",
                "channelname": "Filmot channel",
            }]
        }
        result = CliRunner().invoke(
            cli,
            ["transcript", VIDEO_ID, "--save-to", "topic", "--raw"],
        )

    assert result.exit_code == 0, result.output
    metadata = library.save.call_args.kwargs["metadata"]
    assert metadata["title"] == "Filmot title"
    assert metadata["channel"] == "Filmot channel"
