"""Regression: YouTube Data API query-string credentials must never leak.

``youtube_search.py`` puts ``YOUTUBE_API_KEY`` in the request URL as a
``?key=...`` query parameter (this is how the YouTube Data API is
authenticated; there is no header-based alternative). ``requests`` embeds
the full request URL -- key included -- in ``raise_for_status()`` messages
and in connection/transport error text. That message flows, unmodified,
into both the ``yt-search`` command's emitted JSON/error output
(``commands/search.py`` forwards ``str(error)``) and the durable ledger
(``log_event``'s ``error=str(error)`` field), so an HTTP failure could have
recorded a real API key in plaintext.

These tests use only mocked HTTP failures with an unmistakable dummy
sentinel value. No live network calls or real credentials are used.
"""

import json

import click
import pytest
import requests
from click.testing import CliRunner
from unittest.mock import patch

from filmot.cli import cli
from filmot.proxy_pool import redact_sensitive_text
from filmot.schemas import ErrorDetail
from filmot import youtube_search


DUMMY_API_KEY = "DUMMY-SENTINEL-YOUTUBE-KEY-DO-NOT-USE-1234567890abcdef"


def _make_http_error_response(url: str, status_code: int = 403):
    """Build a mock ``requests`` response whose raise_for_status() mimics the
    real library: an HTTPError whose message embeds the full request URL."""
    response = requests.Response()
    response.status_code = status_code
    response.url = url
    response._content = b'{"error": {"message": "forbidden"}}'
    return response


class TestRedactSensitiveText:
    """Unit coverage for the shared sanitizer's new query-credential handling."""

    def test_redacts_youtube_style_key_query_param(self):
        text = (
            f"403 Client Error: Forbidden for url: "
            f"https://www.googleapis.com/youtube/v3/search?key={DUMMY_API_KEY}"
            f"&q=cats&part=snippet"
        )
        redacted = redact_sensitive_text(text)
        assert DUMMY_API_KEY not in redacted
        assert "key=***" in redacted
        # Useful diagnostic context (host, path, other params) is preserved.
        assert "googleapis.com/youtube/v3/search" in redacted
        assert "q=cats" in redacted
        assert "403 Client Error: Forbidden" in redacted

    def test_still_redacts_proxy_userinfo(self):
        # Existing behavior must be preserved alongside the new query-param
        # handling.
        text = "connect to http://realuser:realpass@p.webshare.io:80 failed"
        redacted = redact_sensitive_text(text)
        assert "realuser" not in redacted
        assert "realpass" not in redacted
        assert "***:***@p.webshare.io:80" in redacted

    def test_redacts_multiple_credential_param_names(self):
        text = (
            "https://example.com/x?token=SECRET1&access_token=SECRET2"
            "&other=keep-me"
        )
        redacted = redact_sensitive_text(text)
        assert "SECRET1" not in redacted
        assert "SECRET2" not in redacted
        assert "other=keep-me" in redacted

    def test_error_detail_redacts_message_and_nested_details(self):
        leaking = (
            "https://www.googleapis.com/youtube/v3/channels?part=snippet"
            f"&key={DUMMY_API_KEY}"
        )
        detail = ErrorDetail.from_exception(
            RuntimeError(leaking),
            stage="channel-info",
            details={"nested": [{"uri": leaking}]},
        )

        rendered = json.dumps(detail.to_dict())
        assert DUMMY_API_KEY not in rendered
        assert "key=***" in rendered

    def test_error_detail_redacts_values_under_credential_field_names(self):
        detail = ErrorDetail(
            type="TransportError",
            message="request failed",
            details={
                "api_key": DUMMY_API_KEY,
                "headers": {"Authorization": "Bearer " + DUMMY_API_KEY},
                "safe": "retained",
            },
        )

        assert detail.to_dict()["details"] == {
            "api_key": "***",
            "headers": {"Authorization": "***"},
            "safe": "retained",
        }

    def test_real_ledger_write_redacts_compact_data_and_errors(
        self, tmp_path
    ):
        from filmot.ledger import log_event, read_events

        leaking = (
            "403 forbidden for "
            "https://www.googleapis.com/youtube/v3/channels?"
            f"part=snippet&key={DUMMY_API_KEY}"
        )
        log_event(
            "yt-channel",
            data_dir=tmp_path,
            status="failed",
            failure_stage="request",
            error=leaking,
            nested={"transport": leaking},
        )

        events = read_events(
            __import__("datetime").date.today().isoformat(),
            data_dir=tmp_path,
        )
        rendered = json.dumps(events)
        assert DUMMY_API_KEY not in rendered
        assert "key=***" in rendered


class TestYoutubeSearchNativeBoundary:
    """youtube_search.py must sanitize before an exception can propagate."""

    def test_search_videos_http_error_is_redacted(self, monkeypatch):
        monkeypatch.setattr(youtube_search, "YOUTUBE_API_KEY", DUMMY_API_KEY)
        leaking_url = (
            f"https://www.googleapis.com/youtube/v3/search?key={DUMMY_API_KEY}"
            "&q=cats&part=snippet&type=video&maxResults=25&order=date"
        )
        mock_response = _make_http_error_response(leaking_url, 403)

        with patch("requests.get", return_value=mock_response):
            with pytest.raises(requests.exceptions.HTTPError) as excinfo:
                youtube_search.search_videos("cats")

        message = str(excinfo.value)
        assert DUMMY_API_KEY not in message
        # Status/URL diagnostics remain useful.
        assert "403" in message
        assert "googleapis.com/youtube/v3/search" in message

    def test_get_video_details_http_error_is_redacted(self, monkeypatch):
        monkeypatch.setattr(youtube_search, "YOUTUBE_API_KEY", DUMMY_API_KEY)
        leaking_url = (
            f"https://www.googleapis.com/youtube/v3/videos?key={DUMMY_API_KEY}"
            "&id=abc123&part=snippet%2Cstatistics%2CcontentDetails"
        )
        mock_response = _make_http_error_response(leaking_url, 429)

        with patch("requests.get", return_value=mock_response):
            with pytest.raises(requests.exceptions.HTTPError) as excinfo:
                youtube_search.get_video_details(["abc123"])

        message = str(excinfo.value)
        assert DUMMY_API_KEY not in message
        assert "429" in message


class TestYtSearchCommandRedaction:
    """End-to-end: emitted JSON and recorded ledger diagnostics stay clean."""

    def test_raw_output_and_ledger_event_never_contain_the_key(self, monkeypatch):
        monkeypatch.setattr(youtube_search, "YOUTUBE_API_KEY", DUMMY_API_KEY)
        leaking_url = (
            f"https://www.googleapis.com/youtube/v3/search?key={DUMMY_API_KEY}"
            "&q=cats&part=snippet"
        )
        mock_response = _make_http_error_response(leaking_url, 403)

        with (
            patch("requests.get", return_value=mock_response),
            patch("filmot.ledger.log_event") as log_event,
            patch("filmot.ledger.log_result") as log_result,
        ):
            result = CliRunner().invoke(cli, ["yt-search", "cats", "--raw"])

        assert result.exit_code != 0
        assert DUMMY_API_KEY not in result.stdout

        raw = json.loads(result.stdout)
        rendered = json.dumps(raw)
        assert DUMMY_API_KEY not in rendered
        assert "403" in rendered

        # The native command diagnostic recorded to the ledger must also be
        # credential-safe, not just the emitted JSON.
        assert log_event.called
        _, kwargs = log_event.call_args
        recorded_error = kwargs.get("error", "")
        assert DUMMY_API_KEY not in recorded_error
        assert "403" in recorded_error
