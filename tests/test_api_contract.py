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
    "get_videos_columns_partial.json": "/getvideos",
    "get_videos_subtitles.json": "/getvideos",
    "get_channels.json": "/getchannels",
    "get_channels_all.json": "/getchannels",
    "get_channel_videos.json": "/getchannelvideos",
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
    ("subtitles", {"lang": "en"}),
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


def _set(payload, path, value):
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


@pytest.mark.parametrize("path,value,expected_path", [
    ((0, "isverified"), "yes", "$[0].isverified"),
    ((0, "counts", "indexedvideos"), "582", "$[0].counts.indexedvideos"),
    ((0, "counts", "recentuploads", "15d"), "3", "$[0].counts.recentuploads.15d"),
    ((0, "growth", "subcount", "28d", "delta"), "0", "$[0].growth.subcount.28d.delta"),
    ((0, "subscribergrowth", 0, "s"), None, "$[0].subscribergrowth[0].s"),
    ((0, "wordcloud", 0, "weight"), "heavy", "$[0].wordcloud[0].weight"),
    ((0, "channelchanges", "name"), {"val": "old"}, "$[0].channelchanges.name"),
    ((0, "channelchanges", "name", 0, "val"), {"text": "old"}, "$[0].channelchanges.name[0].val"),
    ((0, "growth", "subcount"), "rising", "$[0].growth.subcount"),
    ((0, "videostats", "videotypes", "shorts"), "34", "$[0].videostats.videotypes.shorts"),
    ((0, "videostats", "durationhistogram", 1), True, "$[0].videostats.durationhistogram[1]"),
    (
        (0, "videostats", "months", 0, "distributions", "viewcount", "median"), "1",
        "$[0].videostats.months[0].distributions.viewcount.median",
    ),
])
def test_channel_contract_flags_drift_in_every_displayed_group(path, value, expected_path):
    payload = _fixture("get_channels_all.json")
    _set(payload, path, value)

    with pytest.raises(FilmotAPIContractError) as caught:
        validate_api_response("/getchannels", payload)

    assert caught.value.path == expected_path


def test_channel_contract_requires_an_id_and_a_list():
    payload = _fixture("get_channels.json")
    del payload[0]["channelid"]

    with pytest.raises(FilmotAPIContractError) as missing:
        validate_api_response("/getchannels", payload)
    with pytest.raises(FilmotAPIContractError) as not_a_list:
        validate_api_response("/getchannels", {"channelid": "UC"})

    assert missing.value.path == "$[0].channelid"
    assert not_a_list.value.path == "$"


def test_channel_contract_accepts_absent_groups_null_windows_and_new_fields():
    payload = _fixture("get_channels_all.json")
    record = payload[0]
    record["growth"]["subcount"]["28d"] = None
    record["growth"]["totalviewcount"] = None
    record["username"] = None
    record["futuregroup"] = {"anything": [1, "two"]}
    # Additions inside the groups the CLI reads are ignored too.
    record["growth"]["source"] = "metadata"
    record["growth"]["subcount"]["7d"] = "pending"
    record["growth"]["subcount"]["90d"]["note"] = "estimated"
    record["counts"]["recentuploads"]["note"] = "approximate"
    record["videostats"]["videotypes"]["premieres"] = "n/a"
    record["videostats"]["distributions"]["duration"]["unit"] = "seconds"
    record["videostats"]["distributions"]["commentcount"] = "later"
    record["channelchanges"]["futurefield"] = {"note": "new"}
    record["channelchanges"]["country"] = None
    base = _fixture("get_channels.json")

    assert validate_api_response("/getchannels", payload) is payload
    assert validate_api_response("/getchannels", base) is base


def test_channel_listings_use_the_video_record_contract():
    payload = _fixture("get_channel_videos.json")
    payload[1]["title"] = 7

    with pytest.raises(FilmotAPIContractError) as caught:
        validate_api_response("/getchannelvideos", payload)

    assert caught.value.path == "$[1].title"


@pytest.mark.parametrize("field", ["title", "duration", "uploaddate", "channelid", "channelname"])
def test_video_contract_accepts_null_core_fields_of_partial_records(field):
    payload = _fixture("get_videos.json")
    payload[0][field] = None

    assert validate_api_response("/getvideos", payload) is payload


@pytest.mark.parametrize("mutate,expected_path", [
    (lambda video: video.pop("title"), "$[0].title"),
    (lambda video: video.update(title=5), "$[0].title"),
    (lambda video: video.update(duration=True), "$[0].duration"),
    (lambda video: video.update(id=None), "$[0].id"),
])
def test_video_contract_still_flags_missing_or_mistyped_core_fields(mutate, expected_path):
    payload = _fixture("get_videos.json")
    mutate(payload[0])

    with pytest.raises(FilmotAPIContractError) as caught:
        validate_api_response("/getvideos", payload)

    assert caught.value.path == expected_path


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


def test_recorder_replaces_channel_profile_text_and_shortens_series():
    recorder = _recorder()
    channel = {
        "channelid": "UCreal", "username": "realname", "biography": "A real person's story",
        "description": "Real description",
        "channelchanges": {"name": [{"val": "Old real name", "dstart": "2019-01-01"}] * 3},
        "wordcloud": [{"text": "realword", "weight": 9, "lang": "en"}] * 5,
        "subscribergrowth": [{"d": "2026-01-01", "s": 5}] * 5,
        "videostats": {"months": [{"month": "2026-01"}] * 4, "languages": [{"lang": "en"}] * 4},
    }

    compacted = recorder["compact_response"]("get_channels_all", [channel])
    sanitized = recorder["sanitize_response"](compacted)[0]

    rendered = json.dumps(sanitized)
    for private in ("UCreal", "realname", "real person", "Real description", "Old real name", "realword"):
        assert private not in rendered
    assert len(sanitized["channelchanges"]["name"]) == 2
    assert (len(sanitized["wordcloud"]), len(sanitized["subscribergrowth"])) == (3, 3)
    assert (len(sanitized["videostats"]["months"]), len(sanitized["videostats"]["languages"])) == (2, 2)


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
