"""Tests for filmot.transcript module."""

import pytest
from unittest.mock import patch, MagicMock

from filmot.transcript import (
    extract_video_id,
    format_timestamp,
    get_transcript,
    get_transcript_with_fallback,
)


# ── extract_video_id ──────────────────────────────────────────────

class TestExtractVideoId:
    """Tests for extract_video_id()."""

    def test_plain_id(self):
        assert extract_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_id_with_hyphens_underscores(self):
        assert extract_video_id("-O1bjFPgRQM") == "-O1bjFPgRQM"
        assert extract_video_id("a_b-c_d-e_f") == "a_b-c_d-e_f"

    def test_standard_url(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_short_url(self):
        url = "https://youtu.be/dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_url_with_extra_params(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=120&list=PLxyz"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_embed_url(self):
        url = "https://www.youtube.com/embed/dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_old_v_url(self):
        url = "https://www.youtube.com/v/dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_no_scheme(self):
        url = "youtube.com/watch?v=dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_garbage_passthrough(self):
        """Non-matching input is returned as-is."""
        assert extract_video_id("not-a-video") == "not-a-video"

    def test_empty_string(self):
        assert extract_video_id("") == ""


# ── format_timestamp ──────────────────────────────────────────────

class TestFormatTimestamp:
    """Tests for format_timestamp()."""

    def test_zero(self):
        assert format_timestamp(0) == "0:00"

    def test_seconds_only(self):
        assert format_timestamp(45) == "0:45"

    def test_minutes_and_seconds(self):
        assert format_timestamp(125) == "2:05"

    def test_hours(self):
        assert format_timestamp(3661) == "1:01:01"

    def test_float_input(self):
        assert format_timestamp(90.7) == "1:30"


# ── get_transcript (mocked) ──────────────────────────────────────

class TestGetTranscript:
    """Tests for get_transcript() with mocked API."""

    @patch("filmot.transcript.get_api")
    def test_success(self, mock_get_api):
        """Successful transcript fetch returns expected structure."""
        mock_api = MagicMock()
        mock_get_api.return_value = mock_api

        # Build a mock transcript object that is iterable
        seg1 = MagicMock(text="Hello", start=0.0, duration=1.5)
        seg2 = MagicMock(text="world", start=1.5, duration=1.0)
        mock_transcript = MagicMock()
        mock_transcript.__iter__ = MagicMock(return_value=iter([seg1, seg2]))
        mock_transcript.video_id = "abc12345678"
        mock_transcript.language_code = "en"
        mock_transcript.is_generated = True
        mock_api.fetch.return_value = mock_transcript

        result = get_transcript("abc12345678")

        assert "error" not in result
        assert result["video_id"] == "abc12345678"
        assert result["language"] == "en"
        assert result["is_generated"] is True
        assert result["segment_count"] == 2
        assert "Hello" in result["full_text"]
        assert "world" in result["full_text"]

    @patch("filmot.transcript.get_api")
    def test_transcripts_disabled(self, mock_get_api):
        from youtube_transcript_api._errors import TranscriptsDisabled

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api
        mock_api.fetch.side_effect = TranscriptsDisabled("abc12345678")

        result = get_transcript("abc12345678")
        assert "error" in result
        assert "disabled" in result["error"].lower()

    @patch("filmot.transcript.get_api")
    def test_video_unavailable(self, mock_get_api):
        from youtube_transcript_api._errors import VideoUnavailable

        mock_api = MagicMock()
        mock_get_api.return_value = mock_api
        mock_api.fetch.side_effect = VideoUnavailable("abc12345678")

        result = get_transcript("abc12345678")
        assert "error" in result
        assert "unavailable" in result["error"].lower()

    @patch("filmot.transcript.get_api")
    def test_url_input_extracts_id(self, mock_get_api):
        """Passing a URL correctly extracts the video ID."""
        mock_api = MagicMock()
        mock_get_api.return_value = mock_api

        seg = MagicMock(text="Test", start=0.0, duration=1.0)
        mock_transcript = MagicMock()
        mock_transcript.__iter__ = MagicMock(return_value=iter([seg]))
        mock_transcript.video_id = "dQw4w9WgXcQ"
        mock_transcript.language_code = "en"
        mock_transcript.is_generated = False
        mock_api.fetch.return_value = mock_transcript

        result = get_transcript("https://youtu.be/dQw4w9WgXcQ")
        assert result["video_id"] == "dQw4w9WgXcQ"


# ── get_transcript_with_fallback ─────────────────────────────────

class TestGetTranscriptWithFallback:
    """Tests for get_transcript_with_fallback()."""

    @patch("filmot.transcript.get_transcript")
    def test_youtube_success_no_fallback_needed(self, mock_gt):
        """When YouTube succeeds, AWS is never called."""
        mock_gt.return_value = {
            "video_id": "abc12345678",
            "language": "en",
            "is_generated": True,
            "segments": [],
            "full_text": "Hello world",
            "duration_seconds": 10,
            "segment_count": 1,
        }
        result = get_transcript_with_fallback("abc12345678")
        assert result["source"] == "youtube"
        assert result["full_text"] == "Hello world"

    @patch("filmot.transcript.get_transcript")
    def test_youtube_fails_fallback_disabled(self, mock_gt):
        """When YouTube fails and fallback is disabled, error is returned."""
        mock_gt.return_value = {"error": "Transcripts are disabled", "video_id": "abc12345678"}
        result = get_transcript_with_fallback("abc12345678", use_aws_fallback=False)
        assert "error" in result

    @patch("filmot.transcript.get_transcript")
    @patch("filmot.aws_transcribe.check_dependencies")
    @patch("filmot.aws_transcribe.transcribe_video")
    def test_youtube_fails_aws_succeeds(self, mock_transcribe, mock_deps, mock_gt):
        """When YouTube fails, AWS Transcribe fallback kicks in."""
        mock_gt.return_value = {"error": "Transcripts are disabled", "video_id": "abc12345678"}
        mock_deps.return_value = (True, "")
        mock_transcribe.return_value = ("AWS transcript text here", "en-US")

        result = get_transcript_with_fallback("abc12345678", use_aws_fallback=True)
        assert result["source"] == "aws_transcribe"
        assert result["full_text"] == "AWS transcript text here"
        assert result["language"] == "en-US"

    @patch("filmot.transcript.get_transcript")
    @patch("filmot.aws_transcribe.check_dependencies")
    def test_aws_deps_missing(self, mock_deps, mock_gt):
        """When AWS deps are missing, returns combined error."""
        mock_gt.return_value = {"error": "Transcripts are disabled", "video_id": "abc12345678"}
        mock_deps.return_value = (False, "boto3 not installed")

        result = get_transcript_with_fallback("abc12345678", use_aws_fallback=True)
        assert "error" in result
        assert "boto3" in result["error"]
