"""Contract tests for sanitized, recorded Filmot API response shapes."""

import json
import re
import runpy
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from filmot.api import FilmotClient
from filmot.api_contract import (
    FilmotAPIContractError,
    validate_api_response,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "filmot_api"
FIXTURE_ENDPOINTS = {
    "search_subtitles_context.json": "/getsearchsubtitles",
    "search_subtitles_lines.json": "/getsearchsubtitles",
    "search_channels.json": "/getsearchchannels",
    "get_videos.json": "/getvideos",
}


def _fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name,endpoint", FIXTURE_ENDPOINTS.items())
def test_recorded_fixture_matches_runtime_contract(name, endpoint):
    payload = _fixture(name)

    assert validate_api_response(endpoint, payload) is payload


def test_search_contract_tolerates_additive_fields_at_every_level():
    payload = _fixture("search_subtitles_lines.json")
    payload["future_top_level"] = {"anything": True}
    payload["result"][0]["future_video_field"] = "new"
    payload["result"][0]["hits"][0]["future_hit_field"] = 1
    payload["result"][0]["hits"][0]["lines"][0]["future_line_field"] = []

    validate_api_response("/getsearchsubtitles", payload)


@pytest.mark.parametrize(
    "mutate,expected_path",
    [
        (
            lambda payload: payload.pop("result"),
            "$.result",
        ),
        (
            lambda payload: payload["result"][0].pop("id"),
            "$.result[0].id",
        ),
        (
            lambda payload: payload["result"][0]["hits"][0].pop("token"),
            "$.result[0].hits[0].token",
        ),
    ],
)
def test_search_contract_flags_missing_required_structure(mutate, expected_path):
    payload = _fixture("search_subtitles_context.json")
    mutate(payload)

    with pytest.raises(FilmotAPIContractError) as caught:
        validate_api_response("/getsearchsubtitles", payload)

    assert caught.value.path == expected_path
    assert "recorded phrase" not in str(caught.value)


def test_search_contract_flags_container_type_drift():
    payload = _fixture("search_subtitles_context.json")
    payload["result"] = {"unexpected": "object"}

    with pytest.raises(FilmotAPIContractError) as caught:
        validate_api_response("/getsearchsubtitles", payload)

    assert caught.value.path == "$.result"
    assert caught.value.actual == "dict"


def test_supported_error_envelope_remains_a_normal_api_failure():
    payload = {"error": "upstream unavailable", "status_code": 503}

    assert validate_api_response("/getsearchsubtitles", payload) is payload


def test_unknown_endpoint_is_not_constrained():
    payload = {"new": ["shape"]}

    assert validate_api_response("/future-endpoint", payload) is payload


@patch("filmot.api.get_headers", return_value={})
@patch("filmot.api.validate_config")
def test_client_returns_machine_readable_contract_error(
    mock_validate,
    mock_headers,
):
    client = FilmotClient(use_cache=False)
    response = client.session.request = MagicMock()
    response.return_value.raise_for_status.return_value = None
    response.return_value.json.return_value = {
        "result": "not-an-array",
        "totalresultcount": 1,
    }

    result = client.get("/getsearchsubtitles")

    assert result["error_type"] == "FilmotAPIContractError"
    assert result["endpoint"] == "/getsearchsubtitles"
    assert result["contract_path"] == "$.result"
    assert "not-an-array" not in result["error"]


@patch("filmot.api.get_headers", return_value={})
@patch("filmot.api.validate_config")
@patch("filmot.api.get_cache")
def test_cached_responses_are_validated_too(
    mock_get_cache,
    mock_validate,
    mock_headers,
):
    cache = mock_get_cache.return_value
    cache.get.return_value = {
        "result": "not-an-array",
        "totalresultcount": 1,
    }
    client = FilmotClient(use_cache=True)
    client.session.request = MagicMock()

    result = client.get("/getsearchsubtitles")

    assert result["error_type"] == "FilmotAPIContractError"
    assert client.last_cache_hit is False
    client.session.request.assert_not_called()


def test_fixtures_contain_no_headers_credentials_or_proxy_userinfo():
    forbidden_keys = re.compile(
        r"(?i)(authorization|credential|headers|password|rapidapi|secret|"
        r"access_token|api_key|api_token|refresh_token)"
    )
    proxy_userinfo = re.compile(r"(?i)https?://[^/\s@]+@")

    for path in FIXTURE_DIR.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert not forbidden_keys.search(text), path
        assert not proxy_userinfo.search(text), path
        # Every fixture must remain standalone JSON rather than an HTTP
        # recording containing headers plus a response body.
        assert isinstance(json.loads(text), (dict, list))


def test_recorder_sanitizer_drops_credentials_but_keeps_hit_token_shape():
    namespace = runpy.run_path(
        str(
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "record_filmot_api_fixtures.py"
        )
    )
    sanitized = namespace["sanitize_response"]({
        "headers": {"x-rapidapi-key": "never-write-me"},
        "api_token": "never-write-me-either",
        "hits": [{
            "token": "public spoken words",
            "proxy": "http://user:password@proxy.invalid:8080",
        }],
    })

    assert "headers" not in sanitized
    assert "api_token" not in sanitized
    assert sanitized["hits"][0]["token"] == "recorded phrase"
    assert "user" not in sanitized["hits"][0]["proxy"]
    assert "password" not in sanitized["hits"][0]["proxy"]
