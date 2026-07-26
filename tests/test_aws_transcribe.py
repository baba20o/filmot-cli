"""Tests for filmot.aws_transcribe module (fully mocked, no AWS calls)."""

import pytest
from unittest.mock import patch, MagicMock, call
import os
import json

from filmot.aws_transcribe import (
    check_dependencies,
    download_audio,
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


class TestDownloadAudio:

    @patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.example:8080"}, clear=False)
    @patch("subprocess.run")
    def test_prefers_direct_connection_before_env_proxy(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        (tmp_path / "abc12345678.mp3").write_bytes(b"audio")

        path = download_audio("abc12345678", output_dir=str(tmp_path))

        assert path.endswith("abc12345678.mp3")
        used_env = mock_run.call_args.kwargs["env"]
        assert "HTTPS_PROXY" not in used_env

    @patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.example:8080"}, clear=False)
    @patch("subprocess.run")
    def test_retries_with_env_proxy_after_direct_failure(self, mock_run, tmp_path):
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr="direct failed"),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        (tmp_path / "abc12345678.mp3").write_bytes(b"audio")

        path = download_audio("abc12345678", output_dir=str(tmp_path))

        assert path.endswith("abc12345678.mp3")
        assert mock_run.call_count == 2
        first_env = mock_run.call_args_list[0].kwargs["env"]
        second_env = mock_run.call_args_list[1].kwargs["env"]
        assert "HTTPS_PROXY" not in first_env
        assert second_env["HTTPS_PROXY"] == "http://proxy.example:8080"

    @patch.dict(os.environ, {"FILMOT_PROXY_RETRY_LIMIT": "2"}, clear=False)
    @patch("subprocess.run")
    def test_pool_attempts_are_sequential_with_deadline_sized_leases(
        self, mock_run, tmp_path
    ):
        from filmot.aws_transcribe import (
            YTDLP_DOWNLOAD_TIMEOUT_SECONDS,
            YTDLP_LEASE_MARGIN_SECONDS,
        )
        from filmot.proxy_pool import WebshareSession

        first = WebshareSession(
            id="raw-session-one",
            username="proxy-user-one",
            password="proxy-password-one",
        )
        second = WebshareSession(
            id="raw-session-two",
            username="proxy-user-two",
            password="proxy-password-two",
        )
        urls = {
            first.id: "http://proxy-user-one:proxy-password-one@proxy.test:80",
            second.id: "http://proxy-user-two:proxy-password-two@proxy.test:80",
        }
        pool = MagicMock()
        pool.source = "session-file"
        events = []
        remaining_sessions = iter((first, second))

        def pick_session(**_kwargs):
            session = next(remaining_sessions)
            events.append(f"pick:{session.id}")
            return session

        def run_attempt(_command, **kwargs):
            proxy_url = kwargs["env"]["HTTPS_PROXY"]
            session = first if proxy_url == urls[first.id] else second
            events.append(f"run:{session.id}")
            if session is first:
                return MagicMock(
                    returncode=1,
                    stdout="",
                    stderr="proxy connection failed",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        def report_failure(session, *_args, **_kwargs):
            events.append(f"failure:{session.id}")

        def report_success(session):
            events.append(f"success:{session.id}")

        pool.pick.side_effect = pick_session
        pool.proxy_url.side_effect = lambda session: urls[session.id]
        pool.redacted_session_id.side_effect = (
            lambda session: f"redacted-{session.id[-3:]}"
        )
        pool.report_failure.side_effect = report_failure
        pool.report_success.side_effect = report_success
        mock_run.side_effect = run_attempt
        (tmp_path / "abc12345678.mp3").write_bytes(b"audio")

        with (
            patch("filmot.proxy_pool.get_pool", return_value=pool),
            patch(
                "filmot.transcript._resolve_proxy_mode",
                return_value="proxy-only",
            ),
        ):
            path = download_audio("abc12345678", output_dir=str(tmp_path))

        assert path.endswith("abc12345678.mp3")
        assert pool.pick.call_count == 2
        assert all(
            call_item.kwargs["lease"] is True
            and call_item.kwargs["lease_seconds"]
            == (
                YTDLP_DOWNLOAD_TIMEOUT_SECONDS
                + YTDLP_LEASE_MARGIN_SECONDS
            )
            for call_item in pool.pick.call_args_list
        )
        assert events == [
            f"pick:{first.id}",
            f"run:{first.id}",
            f"failure:{first.id}",
            f"pick:{second.id}",
            f"run:{second.id}",
            f"success:{second.id}",
        ]
        command_text = " ".join(
            part
            for call_item in mock_run.call_args_list
            for part in call_item.args[0]
        )
        assert "--proxy" not in command_text
        for secret in (
            first.id,
            first.username,
            first.password,
            second.id,
            second.username,
            second.password,
            urls[first.id],
            urls[second.id],
        ):
            assert secret not in command_text
        assert (
            mock_run.call_args_list[0].kwargs["env"]["HTTPS_PROXY"]
            == urls[first.id]
        )
        assert (
            mock_run.call_args_list[1].kwargs["env"]["HTTPS_PROXY"]
            == urls[second.id]
        )
        pool.report_success.assert_called_once_with(second)
        pool.release.assert_not_called()

    @patch.dict(os.environ, {"FILMOT_PROXY_RETRY_LIMIT": "1"}, clear=False)
    @patch("subprocess.run")
    def test_pool_failure_redacts_all_credentials(
        self, mock_run, tmp_path
    ):
        from filmot.proxy_pool import WebshareSession

        session = WebshareSession(
            id="raw-session-id",
            username="private-proxy-user",
            password="private-proxy-password",
        )
        proxy_url = (
            "http://private-proxy-user:private-proxy-password@proxy.test:80"
        )
        diagnostic = (
            f"failed {session.id} {session.username} "
            f"{session.password} via {proxy_url}"
        )
        pool = MagicMock()
        pool.source = "session-file"
        pool.pick.return_value = session
        pool.proxy_url.return_value = proxy_url
        pool.redacted_session_id.return_value = "session-deadbeef"
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr=diagnostic,
        )

        with (
            patch("filmot.proxy_pool.get_pool", return_value=pool),
            patch(
                "filmot.transcript._resolve_proxy_mode",
                return_value="proxy-only",
            ),
            pytest.raises(AWSTranscribeError) as raised,
        ):
            download_audio("abc12345678", output_dir=str(tmp_path))

        message = str(raised.value)
        summary = pool.report_failure.call_args.kwargs["summary"]
        assert "session-deadbeef" in message
        for secret in (
            session.id,
            session.username,
            session.password,
            proxy_url,
        ):
            assert secret not in message
            assert secret not in summary
        command = mock_run.call_args.args[0]
        assert "--proxy" not in command
        assert proxy_url not in " ".join(command)


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
