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
    "search_channels_direct.json": "/getsearchchannels",
    "get_videos.json": "/getvideos",
    "get_videos_columns.json": "/getvideos",
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


@pytest.mark.parametrize("field,value", [
    ("viewcount", "many"),
    ("likecount", True),
    ("description", 5),
    ("license", True),
    ("isunlisted", "yes"),
    ("autosubs", ["en"]),
])
def test_video_contract_flags_drift_in_consumed_optional_columns(field, value):
    payload = _fixture("get_videos_columns.json")
    payload[0][field] = value

    with pytest.raises(FilmotAPIContractError) as caught:
        validate_api_response("/getvideos", payload)

    assert caught.value.path == "$[0].{}".format(field)


def test_video_contract_accepts_absent_null_and_legacy_optional_columns():
    payload = _fixture("get_videos_columns.json")
    payload[0].update(description=None, likecount=None, license=2, isunlisted=True)
    del payload[0]["autosubs"]

    assert validate_api_response("/getvideos", payload) is payload


def test_supported_error_envelope_remains_a_normal_api_failure():
    payload = {"error": "upstream unavailable", "status_code": 503}

    assert validate_api_response("/getsearchsubtitles", payload) is payload


def test_unknown_endpoint_is_not_constrained():
    payload = {"new": ["shape"]}

    assert validate_api_response("/future-endpoint", payload) is payload


def test_client_returns_machine_readable_contract_error():
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


@patch("filmot.api.get_cache")
def test_cached_responses_are_validated_too(mock_get_cache):
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


def _recorder():
    return runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts" / "record_filmot_api_fixtures.py")
    )


def test_recorder_sanitizer_drops_direct_api_key_fields():
    sanitized = _recorder()["sanitize_response"](
        {"X-API-Key": "never-write-me", "apikey": "nor-me", "name": "Some channel"}
    )

    assert sanitized == {"name": "Recorded channel 001"}


def test_recorder_refuses_to_write_a_fixture_containing_a_configured_key(
    monkeypatch, tmp_path,
):
    class EchoingClient:
        def __init__(self, use_cache=False):
            pass

        def get(self, endpoint, params=None):
            # An unknown field the sanitizer keeps verbatim.
            return [{"channelid": "UCx", "name": "x", "note": "test-direct-key"}]

    monkeypatch.setattr("filmot.api.FilmotClient", EchoingClient)
    monkeypatch.setattr(
        "sys.argv",
        ["record", "--only", "search_channels", "--output-dir", str(tmp_path)],
    )

    with pytest.raises(SystemExit, match="refusing to write a fixture containing a configured key"):
        _recorder()["main"]()
    assert not list(tmp_path.iterdir())
