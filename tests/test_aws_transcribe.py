"""Tests for filmot.aws_transcribe module (fully mocked, no AWS calls)."""

import pytest
from unittest.mock import patch, MagicMock, call
import os
import json

from filmot.aws_transcribe import (
    check_dependencies,
    upload_to_s3,
    start_transcription_job,
    wait_for_transcription,
    fetch_transcript_text,
    cleanup_job,
    cleanup_s3_file,
    AWSTranscribeError,
)


# ── check_dependencies ───────────────────────────────────────────

class TestCheckDependencies:

    @patch("filmot.aws_transcribe.HAS_BOTO3", True)
    @patch("subprocess.run")
    def test_all_ok(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="2024.01.01")
        ok, msg = check_dependencies()
        assert ok is True
        assert msg == ""

    @patch("filmot.aws_transcribe.HAS_BOTO3", False)
    def test_missing_boto3(self):
        ok, msg = check_dependencies()
        assert ok is False
        assert "boto3" in msg

    @patch("filmot.aws_transcribe.HAS_BOTO3", True)
    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_missing_ytdlp(self, mock_run):
        ok, msg = check_dependencies()
        assert ok is False
        assert "yt-dlp" in msg


# ── upload_to_s3 ─────────────────────────────────────────────────

class TestUploadToS3:

    def test_returns_s3_uri(self, tmp_path):
        mock_s3 = MagicMock()
        test_file = tmp_path / "video.mp3"
        test_file.write_bytes(b"audio data")

        uri = upload_to_s3(str(test_file), mock_s3, "my-bucket")
        assert uri == "s3://my-bucket/video.mp3"
        mock_s3.upload_file.assert_called_once()


# ── start_transcription_job ──────────────────────────────────────

class TestStartTranscriptionJob:

    def test_auto_detect_language(self):
        mock_client = MagicMock()
        job_name = start_transcription_job(
            mock_client, "testvid", "s3://bucket/file.mp3", identify_language=True
        )
        assert "testvid" in job_name
        call_kwargs = mock_client.start_transcription_job.call_args[1]
        assert call_kwargs["IdentifyLanguage"] is True
        assert "LanguageCode" not in call_kwargs

    def test_fixed_language(self):
        mock_client = MagicMock()
        job_name = start_transcription_job(
            mock_client, "testvid", "s3://bucket/file.mp3", identify_language=False
        )
        call_kwargs = mock_client.start_transcription_job.call_args[1]
        assert call_kwargs["LanguageCode"] == "en-US"
        assert "IdentifyLanguage" not in call_kwargs


# ── wait_for_transcription ───────────────────────────────────────

class TestWaitForTranscription:

    def test_completes_after_polls(self):
        mock_client = MagicMock()
        # First call: IN_PROGRESS, second: COMPLETED
        mock_client.get_transcription_job.side_effect = [
            {"TranscriptionJob": {"TranscriptionJobStatus": "IN_PROGRESS"}},
            {
                "TranscriptionJob": {
                    "TranscriptionJobStatus": "COMPLETED",
                    "Transcript": {"TranscriptFileUri": "https://aws.example.com/transcript.json"},
                }
            },
        ]

        with patch("time.sleep"):  # skip real sleep
            uri = wait_for_transcription(mock_client, "job123", poll_interval=1)

        assert uri == "https://aws.example.com/transcript.json"
        assert mock_client.get_transcription_job.call_count == 2

    def test_raises_on_failure(self):
        mock_client = MagicMock()
        mock_client.get_transcription_job.return_value = {
            "TranscriptionJob": {
                "TranscriptionJobStatus": "FAILED",
                "FailureReason": "Bad audio",
            }
        }

        with pytest.raises(AWSTranscribeError, match="Bad audio"):
            wait_for_transcription(mock_client, "job123")

    def test_raises_on_timeout(self):
        mock_client = MagicMock()
        mock_client.get_transcription_job.return_value = {
            "TranscriptionJob": {"TranscriptionJobStatus": "IN_PROGRESS"}
        }

        with patch("time.sleep"):
            with patch("time.time", side_effect=[0, 0, 999]):
                with pytest.raises(AWSTranscribeError, match="timed out"):
                    wait_for_transcription(mock_client, "job123", timeout=10)


# ── fetch_transcript_text ────────────────────────────────────────

class TestFetchTranscriptText:

    @patch("filmot.aws_transcribe.requests.get")
    def test_parses_aws_json(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": {"transcripts": [{"transcript": "Hello from AWS"}]}
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        text = fetch_transcript_text("https://aws.example.com/t.json")
        assert text == "Hello from AWS"


# ── cleanup helpers ──────────────────────────────────────────────

class TestCleanup:

    def test_cleanup_job_ignores_errors(self):
        mock_client = MagicMock()
        mock_client.delete_transcription_job.side_effect = Exception("oops")
        # Should not raise
        cleanup_job(mock_client, "job123")

    def test_cleanup_s3_ignores_errors(self):
        mock_s3 = MagicMock()
        mock_s3.delete_object.side_effect = Exception("oops")
        # Should not raise
        cleanup_s3_file(mock_s3, "bucket", "file.mp3")
