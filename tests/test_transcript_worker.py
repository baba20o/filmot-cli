"""Tests for the isolated transcript worker protocol."""

from unittest.mock import patch

import requests
from youtube_transcript_api._errors import TranscriptsDisabled

from filmot._process import run_json_worker
from filmot._transcript_worker import handle_request


def _request(route=None):
    return {
        "protocol_version": 1,
        "route": route or {"kind": "direct"},
        "video_id": "abc12345678",
        "languages": ["en"],
        "preserve_formatting": False,
        "connect_timeout": 2.5,
        "read_timeout": 7.5,
    }


@patch("filmot._transcript_worker._fetch_transcript_from_api")
@patch("filmot._transcript_worker._build_api")
def test_worker_success_uses_validated_request(mock_build, mock_fetch):
    expected = {
        "video_id": "abc12345678",
        "segments": [],
        "full_text": "人工知能",
    }
    mock_fetch.return_value = expected

    response = handle_request(_request())

    assert response == {
        "protocol_version": 1,
        "status": "success",
        "result": expected,
    }
    mock_fetch.assert_called_once_with(
        mock_build.return_value,
        "abc12345678",
        languages=["en"],
        preserve_formatting=False,
    )


@patch("filmot._transcript_worker._fetch_transcript_from_api")
@patch("filmot._transcript_worker._build_api")
def test_worker_classifies_terminal_caption_error(mock_build, mock_fetch):
    mock_fetch.side_effect = TranscriptsDisabled("abc12345678")

    response = handle_request(_request())

    assert response["status"] == "terminal"
    assert response["error"]["error_type"] == "TranscriptsDisabled"
    assert "failure_kind" not in response["error"]


@patch("filmot._transcript_worker._fetch_transcript_from_api")
@patch("filmot._transcript_worker._build_api")
def test_worker_classifies_and_redacts_transport_error(mock_build, mock_fetch):
    secret = "proxy-password"
    proxy_url = f"http://proxy-user:{secret}@proxy.invalid:8080"
    mock_fetch.side_effect = requests.exceptions.ProxyError(
        f"tunnel failed through {proxy_url}"
    )

    response = handle_request(_request({
        "kind": "generic-proxy",
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
    }))

    assert response["status"] == "transport_error"
    assert response["error"]["failure_kind"] == "connection"
    assert secret not in response["error"]["message"]
    assert "proxy-user" not in response["error"]["message"]


@patch("filmot._transcript_worker._fetch_transcript_from_api")
@patch("filmot._transcript_worker._build_api")
def test_worker_classifies_software_error_as_internal(mock_build, mock_fetch):
    mock_fetch.side_effect = ValueError("parser invariant failed")

    response = handle_request(_request())

    assert response["status"] == "internal_error"
    assert response["error"]["error_type"] == "ValueError"
    assert "failure_kind" not in response["error"]


def test_worker_rejects_invalid_request_without_echoing_payload():
    response = handle_request({
        "protocol_version": 1,
        "password": "must-not-be-echoed",
    })

    assert response["status"] == "internal_error"
    assert response["error"]["error_type"] == "WorkerRequestError"
    assert "must-not-be-echoed" not in response["error"]["message"]


@patch("filmot._transcript_worker._build_direct_api")
def test_worker_passes_explicit_http_timeouts(mock_build):
    request = _request()
    with patch(
        "filmot._transcript_worker._fetch_transcript_from_api",
        return_value={"video_id": "abc12345678"},
    ):
        response = handle_request(request)

    assert response["status"] == "success"
    mock_build.assert_called_once_with(
        connect_timeout=2.5,
        read_timeout=7.5,
    )


def test_worker_module_protocol_runs_in_a_real_subprocess():
    response = run_json_worker(
        "filmot._transcript_worker",
        {"protocol_version": 1},
        timeout_seconds=5,
    )

    assert response["status"] == "internal_error"
    assert response["error"]["error_type"] == "WorkerRequestError"
