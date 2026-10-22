"""Dual Filmot API providers: routing, failover, quotas, credits, and channel shapes."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests
from click.testing import CliRunner

import filmot.config as app_config
from filmot import api_usage
from filmot.api import (
    FilmotClient,
    normalize_channel_suggestion,
    normalize_channel_suggestions,
)
from filmot.api_providers import (
    NO_CREDENTIALS_MESSAGE,
    ProviderConfigurationError,
    configured_providers,
    describe_configuration,
    endpoint_name,
    providers_for,
)
from filmot.cache import Cache
from filmot.cli import cli
from filmot.ledger import read_events
from filmot.schemas import CommandResult, EventRecord, normalize_event_dict


FIXTURES = Path(__file__).parent / "fixtures" / "filmot_api"
DIRECT_HOST = "direct.filmot.invalid"
CREDITS = {
    "X-API-Credit-Cost": "1",
    "X-API-Credits-Used": "1",
    "X-API-Credits-Remaining": "7999",
    "X-API-Credits-Reset": "2026-11-19T00:00:00+00:00",
}
RAPID_PLAN = {
    "X-RateLimit-Requests-Limit": "50000",
    "X-RateLimit-Requests-Remaining": "49903",
}
DIRECT_CHANNEL = {
    "channelid": "UCdirect000000000000000a", "name": "Direct Lab", "subcount": 2100,
    "totalviewcount": 99000, "countryname": "Iceland",
    "thumbnailurl": "https://example.invalid/t.jpg", "handle": "@directlab",
}
RAPID_CHANNEL = {
    "r": 7, "value": "UCrapid0000000000000000b", "label": "Rapid Lab",
    "thumbnailurl": "https://example.invalid/t.jpg", "subcount": 1500,
    "subcountp": "1.5K", "viewcount": 42000, "viewcountp": "42K",
    "newshortname": "@rapidlab", "islist": False,
}


class FakeResponse:
    def __init__(self, status=200, body=None, text=None, headers=None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else json.dumps(body)
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                "{} error".format(self.status_code), response=self,
            )


@pytest.fixture
def transport():
    calls = []
    queues = {"rapidapi": [], "direct": []}

    def request(method, url, params=None, json=None, headers=None, timeout=None,
                allow_redirects=True):
        provider = "direct" if DIRECT_HOST in url else "rapidapi"
        calls.append({
            "provider": provider, "url": url, "allow_redirects": allow_redirects,
            "params": dict(params or {}), "headers": dict(headers or {}),
        })
        item = queues[provider].pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    return SimpleNamespace(calls=calls, queues=queues, request=request)


def make_client(transport, use_cache=False):
    client = FilmotClient(use_cache=use_cache)
    client.session.request = transport.request
    client.rate_limiter = MagicMock()
    return client


@pytest.fixture
def http(monkeypatch):
    """Route every requests.Session call (CLI paths) to queued fake responses."""
    calls = []
    queues = {"rapidapi": [], "direct": []}

    def request(self, method, url, params=None, json=None, headers=None, timeout=None, **_):
        provider = "direct" if DIRECT_HOST in url else "rapidapi"
        calls.append({"provider": provider, "url": url, "headers": dict(headers or {})})
        return queues[provider].pop(0)

    monkeypatch.setattr(requests.sessions.Session, "request", request)
    monkeypatch.setattr("filmot.api.get_rate_limiter", lambda *args, **kwargs: MagicMock())
    return SimpleNamespace(calls=calls, queues=queues)


def invoke(*args):
    # A wide terminal keeps Rich from wrapping channel IDs across table lines.
    return CliRunner().invoke(cli, list(args), env={"COLUMNS": "200"})


# ---------------------------------------------------------------- routing


def test_auto_policy_prefers_rapidapi_for_legacy_routes_and_direct_for_new_ones():
    assert [p.name for p in providers_for("/getsearchsubtitles")] == ["rapidapi", "direct"]
    assert [p.name for p in providers_for("/getvideos?id=x")] == ["rapidapi", "direct"]
    assert [p.name for p in providers_for("getsearchchannels")] == ["rapidapi", "direct"]
    for endpoint in ("/getchannels", "/getchannelvideos", "/getmetadatasearch",
                     "/getadvancedchannelsearch", "/getfutureendpoint"):
        assert [p.name for p in providers_for(endpoint)] == ["direct"]


def test_subtitle_export_never_routes_to_rapidapi_where_it_aliases_legacy_search():
    assert [p.name for p in providers_for("/getsubtitlesearch")] == ["direct"]


def test_single_configured_provider_serves_what_it_can(monkeypatch):
    monkeypatch.setattr(app_config, "RAPIDAPI_KEY", "")
    assert [p.name for p in providers_for("/getsearchsubtitles")] == ["direct"]

    monkeypatch.setattr(app_config, "RAPIDAPI_KEY", "test-rapidapi-key")
    monkeypatch.setattr(app_config, "DIRECT_API_KEY", "")
    assert [p.name for p in providers_for("/getsearchsubtitles")] == ["rapidapi"]
    with pytest.raises(ProviderConfigurationError, match="FILMOT_API_KEY"):
        providers_for("/getchannels")


@pytest.mark.parametrize("policy,legacy,direct_only", [
    ("direct", ["direct"], ["direct"]),
    ("rapidapi", ["rapidapi"], None),
])
def test_pinned_policies_use_one_provider_without_failover(monkeypatch, policy, legacy, direct_only):
    monkeypatch.setattr(app_config, "API_PROVIDER", policy)
    assert [p.name for p in providers_for("/getvideos")] == legacy
    if direct_only is None:
        with pytest.raises(ProviderConfigurationError, match="only available from the direct"):
            providers_for("/getchannels")
    else:
        assert [p.name for p in providers_for("/getchannels")] == direct_only


@pytest.mark.parametrize("policy,missing,message", [
    ("direct", "DIRECT_API_KEY", "FILMOT_API_KEY is not set"),
    ("rapidapi", "RAPIDAPI_KEY", "RAPIDAPI_KEY is not set"),
])
def test_pinned_policy_without_its_key_fails_before_any_request(monkeypatch, policy, missing, message):
    monkeypatch.setattr(app_config, "API_PROVIDER", policy)
    monkeypatch.setattr(app_config, missing, "")
    with pytest.raises(ProviderConfigurationError, match=message):
        FilmotClient(use_cache=False)


def test_missing_credentials_and_invalid_policy_are_actionable(monkeypatch):
    monkeypatch.setattr(app_config, "DIRECT_API_KEY", "")
    monkeypatch.setattr(app_config, "RAPIDAPI_KEY", "")
    with pytest.raises(ProviderConfigurationError) as caught:
        FilmotClient(use_cache=False)
    assert str(caught.value) == NO_CREDENTIALS_MESSAGE
    assert "https://filmot.com/api-keys" in NO_CREDENTIALS_MESSAGE

    monkeypatch.setattr(app_config, "DIRECT_API_KEY", "test-direct-key")
    monkeypatch.setattr(app_config, "API_PROVIDER", "cheapest")
    with pytest.raises(ProviderConfigurationError, match="auto, direct, or rapidapi"):
        providers_for("/getvideos")


def test_providers_build_their_own_authentication_and_base_urls(monkeypatch):
    monkeypatch.setattr(app_config, "DIRECT_BASE_URL", "https://staging.filmot.invalid/api/v1/")
    providers = configured_providers()
    direct, rapid = providers["direct"], providers["rapidapi"]
    assert direct.headers() == {"X-API-Key": "test-direct-key"}
    assert direct.url("/getvideos") == "https://staging.filmot.invalid/api/v1/getvideos"
    assert rapid.headers() == {
        "x-rapidapi-key": "test-rapidapi-key", "x-rapidapi-host": "rapidapi.filmot.invalid",
    }
    assert rapid.url("getvideos") == "https://rapidapi.filmot.invalid/getvideos"
    for provider in (direct, rapid):
        assert "test-direct-key" not in repr(provider)
        assert "test-rapidapi-key" not in repr(provider)


def test_endpoint_names_normalize_paths_queries_and_case():
    assert endpoint_name("/GetVideos?id=abc") == "getvideos"
    assert endpoint_name("getsearchsubtitles/") == "getsearchsubtitles"


# ---------------------------------------------------------------- transport


def test_requests_carry_only_the_selected_providers_credentials(transport):
    transport.queues["rapidapi"].append(FakeResponse(200, [], headers=RAPID_PLAN))
    transport.queues["direct"].append(FakeResponse(200, [DIRECT_CHANNEL | {"counts": {}}], headers=CREDITS))
    client = make_client(transport)

    client.get_videos("dQw4w9WgXcQ")
    client.get("/getchannels", params={"channelID": "UCdirect000000000000000a"})

    rapid_call, direct_call = transport.calls
    assert rapid_call["url"] == "https://rapidapi.filmot.invalid/getvideos"
    assert rapid_call["headers"] == {
        "x-rapidapi-key": "test-rapidapi-key", "x-rapidapi-host": "rapidapi.filmot.invalid",
    }
    assert direct_call["url"] == "https://direct.filmot.invalid/api/v1/getchannels"
    assert direct_call["headers"] == {"X-API-Key": "test-direct-key"}
    assert client.last_provider == "direct"


@pytest.mark.parametrize("failure,reason", [
    (FakeResponse(401, None, text="<!doctype html><html>login</html>"), "auth"),
    (FakeResponse(403, {"message": "You are not subscribed to this API."}), "auth"),
    (FakeResponse(404, {"message": "Endpoint does not exist"}), "unavailable"),
    (FakeResponse(429, {"message": "You have exceeded the MONTHLY quota for Requests"}), "quota"),
    (FakeResponse(503, {"message": "Service Unavailable"}), "server-error"),
    (FakeResponse(200, None, text="<html>maintenance</html>"), "invalid-response"),
    (FakeResponse(302, None, text="", headers={"Location": "https://elsewhere.invalid/"}), "invalid-response"),
    (requests.exceptions.ConnectTimeout("connect timed out"), "connection"),
])
def test_auto_policy_fails_over_to_the_direct_api(transport, monkeypatch, failure, reason):
    sleep = MagicMock()
    monkeypatch.setattr("filmot.api.time.sleep", sleep)
    transport.queues["rapidapi"].append(failure)
    transport.queues["direct"].append(FakeResponse(200, [], headers=CREDITS))
    client = make_client(transport)

    result = client.get_videos("dQw4w9WgXcQ")

    assert result == []
    assert [call["provider"] for call in transport.calls] == ["rapidapi", "direct"]
    assert client.last_provider == "direct"
    usage = api_usage.snapshot()
    assert usage["failovers"] == [{
        "endpoint": "/getvideos", "from": "rapidapi", "to": "direct", "reason": reason,
    }]
    assert usage["providers"]["direct"]["credits_charged"] == 1
    sleep.assert_not_called()


def test_request_errors_do_not_fail_over(transport):
    transport.queues["rapidapi"].append(FakeResponse(400, {"message": "Invalid channel ID"}))
    client = make_client(transport)

    result = client.get_videos("bad")

    assert result["error_type"] == "FilmotRequestError"
    assert result["provider"] == "rapidapi"
    assert result["status_code"] == 400
    assert "Invalid channel ID" in result["error"]
    assert [call["provider"] for call in transport.calls] == ["rapidapi"]
    assert "failovers" not in (api_usage.snapshot() or {})


def test_contract_violations_do_not_fail_over(transport):
    transport.queues["rapidapi"].append(FakeResponse(200, {"result": "not-a-list", "totalresultcount": 1}))
    client = make_client(transport)

    result = client.search_subtitles("alpha")

    assert result["error_type"] == "FilmotAPIContractError"
    assert len(transport.calls) == 1


def test_pinned_provider_reports_its_failure_without_failover(transport, monkeypatch):
    monkeypatch.setattr(app_config, "API_PROVIDER", "rapidapi")
    transport.queues["rapidapi"].append(FakeResponse(401, None, text="<html>nope</html>"))
    client = make_client(transport)

    result = client.get_videos("dQw4w9WgXcQ")

    assert result["error_type"] == "FilmotAuthenticationError"
    assert result["error"] == "The RapidAPI gateway rejected its credentials (HTTP 401). Check RAPIDAPI_KEY."
    assert "<html" not in json.dumps(result)
    assert len(transport.calls) == 1


def test_direct_quota_exhaustion_is_structured_and_never_retried(transport, monkeypatch):
    monkeypatch.setattr(app_config, "RAPIDAPI_KEY", "")
    sleep = MagicMock()
    monkeypatch.setattr("filmot.api.time.sleep", sleep)
    quota = {"error": "quota_exhausted", "cost": 5, "used": 8000, "limit": 8000,
             "remaining": 0, "reset_at": "2026-11-19T00:00:00+00:00"}
    transport.queues["direct"].append(FakeResponse(429, quota))
    client = make_client(transport)

    result = client.search_subtitles("alpha")

    assert result["error_type"] == "FilmotQuotaExhausted"
    assert result["status_code"] == 429
    assert result["quota"] == {k: quota[k] for k in ("cost", "used", "limit", "remaining", "reset_at")}
    assert "used 8000 of 8000" in result["error"]
    assert "resets 2026-11-19" in result["error"]
    assert len(transport.calls) == 1
    sleep.assert_not_called()


def test_rate_limiting_backs_off_then_fails_over(transport, monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr("filmot.api.time.sleep", sleep)
    transport.queues["rapidapi"].extend(
        FakeResponse(429, {"message": "Too many requests"}) for _ in range(5)
    )
    transport.queues["direct"].append(FakeResponse(200, [], headers=CREDITS))
    client = make_client(transport)

    assert client.get_videos("dQw4w9WgXcQ") == []
    assert [call.args[0] for call in sleep.call_args_list] == [5, 10, 20, 40]
    assert api_usage.snapshot()["failovers"][0]["reason"] == "rate-limited"


def test_credentials_never_reach_error_results(transport):
    leak = "https://direct.filmot.invalid/x?key=test-direct-key X-API-Key: test-rapidapi-key"
    transport.queues["rapidapi"].append(requests.exceptions.ConnectionError(leak))
    transport.queues["direct"].append(requests.exceptions.ConnectionError(leak))
    client = make_client(transport)

    result = client.get_videos("dQw4w9WgXcQ")

    rendered = json.dumps(result)
    assert result["error_type"] == "FilmotConnectionError"
    assert "test-direct-key" not in rendered
    assert "test-rapidapi-key" not in rendered


def test_credentials_echoed_in_response_bodies_never_reach_output_or_ledger(http):
    body = {"error": "Key rejected: test-rapidapi-key (also test-direct-key)"}
    http.queues["rapidapi"].append(FakeResponse(200, body))

    result = invoke("--session", "leak-check", "channels", "lab", "--raw")

    assert "test-rapidapi-key" not in result.output
    assert "test-direct-key" not in result.output
    assert "Key rejected: *** (also ***)" in result.output
    ledger = json.dumps(read_events("leak-check"))
    assert "test-rapidapi-key" not in ledger and "test-direct-key" not in ledger


def test_quota_details_are_scrubbed_too(transport, monkeypatch):
    monkeypatch.setattr(app_config, "API_PROVIDER", "rapidapi")
    transport.queues["rapidapi"].append(
        FakeResponse(429, {"message": "Monthly quota exceeded for key test-rapidapi-key"})
    )
    client = make_client(transport)

    result = client.get_videos("dQw4w9WgXcQ")

    assert result["error_type"] == "FilmotQuotaExhausted"
    assert result["quota"] == {"message": "Monthly quota exceeded for key ***"}
    assert "test-rapidapi-key" not in json.dumps(result)


def test_successful_payloads_and_cache_files_are_scrubbed_but_tokens_survive(transport):
    leaked = dict(DIRECT_CHANNEL, name="Lab test-direct-key", token="public spoken words")
    transport.queues["rapidapi"].append(FakeResponse(200, [leaked]))
    client = make_client(transport, use_cache=True)

    result = client.search_channels("lab")

    assert result[0]["name"] == "Lab ***"
    assert result[0]["token"] == "public spoken words"
    cached = "".join(
        path.read_text(encoding="utf-8") for path in Path(client.cache.cache_dir).glob("*.json")
    )
    assert cached and "test-direct-key" not in cached
    assert client.search_channels("lab")[0]["name"] == "Lab ***"


@pytest.mark.parametrize("reset,kept", [
    ("2026-11-19T00:00:00+00:00", True),
    ("2026-11-19", True),
    ("2026-11-19T00:00:00.123Z", True),
    ("test-direct-key", False),
    ("2026-11-19 test-direct-key", False),
    ("<script>", False),
])
def test_credit_reset_header_is_kept_only_as_a_timestamp(reset, kept):
    api_usage.record_response("direct", {**CREDITS, "X-API-Credits-Reset": reset})

    entry = api_usage.snapshot()["providers"]["direct"]
    assert ("credits_reset" in entry) is kept
    if kept:
        assert entry["credits_reset"] == reset
    assert "test-direct-key" not in json.dumps(api_usage.snapshot())
    assert "test-direct-key"[:10] not in api_usage.summary_line()


class RedirectingAdapter(requests.adapters.BaseAdapter):
    """In-memory transport that answers every request with a cross-origin 302."""

    def __init__(self):
        super().__init__()
        self.seen = []

    def send(self, request, **kwargs):
        self.seen.append((request.url, dict(request.headers)))
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 302
        response.headers["Location"] = "https://collector.example.test/steal"
        response._content = b""
        return response

    def close(self):
        pass


def test_authenticated_requests_never_follow_redirects(monkeypatch):
    # Hosts outside .filmot.invalid pass the conftest guard to the real
    # requests machinery, which then reaches only this in-memory adapter.
    monkeypatch.setattr(app_config, "API_PROVIDER", "direct")
    monkeypatch.setattr(app_config, "DIRECT_BASE_URL", "https://direct.example.test/api/v1")
    adapter = RedirectingAdapter()
    client = FilmotClient(use_cache=False)
    client.rate_limiter = MagicMock()
    client.session.mount("https://", adapter)

    result = client.get_videos("dQw4w9WgXcQ")

    assert [url for url, _ in adapter.seen] == [
        "https://direct.example.test/api/v1/getvideos?id=dQw4w9WgXcQ",
    ]
    assert result["error_type"] == "FilmotResponseError"
    assert "unexpected redirect (HTTP 302)" in result["error"]


def test_every_provider_call_disables_redirects(transport):
    transport.queues["rapidapi"].append(FakeResponse(200, []))
    make_client(transport).get_videos("dQw4w9WgXcQ")

    assert transport.calls[0]["allow_redirects"] is False


@pytest.mark.parametrize("direct_key,rapid_key", [
    ("shared-secret-0001", "prefix-shared-secret-0001-suffix"),
    ("prefix-shared-secret-0001-suffix", "shared-secret-0001"),
])
def test_overlapping_keys_are_masked_whole(transport, monkeypatch, direct_key, rapid_key):
    monkeypatch.setattr(app_config, "DIRECT_API_KEY", direct_key)
    monkeypatch.setattr(app_config, "RAPIDAPI_KEY", rapid_key)
    longer = max(direct_key, rapid_key, key=len)
    transport.queues["rapidapi"].append(
        FakeResponse(200, [dict(DIRECT_CHANNEL, name="echo " + longer)])
    )
    transport.queues["rapidapi"].append(requests.exceptions.ConnectionError("failed " + longer))
    transport.queues["direct"].append(requests.exceptions.ConnectionError("failed " + longer))
    client = make_client(transport)

    assert client.search_channels("lab")[0]["name"] == "echo ***"
    rendered = json.dumps(client.get_videos("dQw4w9WgXcQ"))
    assert "prefix" not in rendered and "suffix" not in rendered
    assert "failed ***" in rendered


def _fragments(key, size=6):
    return [key[i:i + size] for i in range(len(key) - size + 1)]


@pytest.mark.parametrize("status,body_key,path", [
    (400, "message", "error"),
    (429, "message", "quota"),
])
def test_keys_split_by_message_truncation_leave_no_fragment(transport, monkeypatch, status, body_key, path):
    monkeypatch.setattr(app_config, "API_PROVIDER", "rapidapi")
    key = "test-direct-key"
    # The key straddles the 300-character bound applied to provider messages.
    message = "quota " + "x" * 288 + key + " trailing text"
    transport.queues["rapidapi"].append(FakeResponse(status, {body_key: message}))
    client = make_client(transport)

    result = client.get_videos("dQw4w9WgXcQ")

    rendered = json.dumps(result)
    assert not [fragment for fragment in _fragments(key) if fragment in rendered]
    if path == "quota":
        assert result["error_type"] == "FilmotQuotaExhausted"
        # Redaction happened before the bound, so the mask itself survives.
        assert "***" in result["quota"]["message"]


@pytest.mark.parametrize("value,valid", [
    ("2026-11-19T00:00:00+00:00", True),
    ("2026-11-19T23:59:59Z", True),
    ("2026-11-19T12:30", True),
    ("2026-11-19", True),
    ("2026-99-99T99:99:99+99:99", False),
    ("2026-02-30", False),
    ("2026-11-19T24:00:00Z", False),
    ("2026-11-19T12:60", False),
    ("2026-11-19T00:00:00+24:00", False),
])
def test_timestamps_are_validated_against_real_calendar_and_clock_values(value, valid):
    assert api_usage.valid_timestamp(value) is valid


def test_quota_reset_at_is_dropped_unless_it_is_a_real_timestamp(transport, monkeypatch):
    monkeypatch.setattr(app_config, "RAPIDAPI_KEY", "")
    quota = {"error": "quota_exhausted", "cost": "5", "used": 8000, "limit": 8000,
             "remaining": 0, "reset_at": "2026-99-99T99:99:99+99:99"}
    transport.queues["direct"].append(FakeResponse(429, quota))
    client = make_client(transport)

    result = client.search_subtitles("alpha")

    assert result["quota"] == {"cost": 5, "used": 8000, "limit": 8000, "remaining": 0}
    assert "resets unknown" in result["error"]


def test_keys_of_unusable_providers_are_still_scrubbed(transport, monkeypatch):
    # A blank host makes RapidAPI unusable, but its key is still a secret.
    monkeypatch.setattr(app_config, "API_HOST", "")
    assert list(configured_providers()) == ["direct"]
    transport.queues["direct"].append(
        FakeResponse(200, [dict(DIRECT_CHANNEL, name="echo test-rapidapi-key")], headers=CREDITS)
    )
    client = make_client(transport)

    result = client.search_channels("lab")

    assert result[0]["name"] == "echo ***"


def test_transport_failures_still_count_as_usage(http, monkeypatch):
    monkeypatch.setattr(app_config, "RAPIDAPI_KEY", "")

    def unreachable(self, method, url, **kwargs):
        raise requests.exceptions.ConnectTimeout("connect timed out")

    monkeypatch.setattr(requests.sessions.Session, "request", unreachable)

    result = invoke("channels", "lab", "--raw")

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["_filmot"]["api"]["providers"]["direct"] == {
        "requests": 1, "cache_hits": 0, "transport_errors": 1,
    }
    assert api_usage.summary_line(payload["_filmot"]["api"]) == (
        "Filmot API: direct 1 request, 1 without a response"
    )


def test_parsed_results_keep_their_api_provenance():
    from filmot.schemas import result_from_dict

    usage = {"providers": {"direct": {"requests": 1, "cache_hits": 0}}}
    outcome = CommandResult(command="channels", status="completed", data={"query": "x"}, api=usage)

    assert result_from_dict(outcome.to_raw_dict()).api == usage
    assert result_from_dict(outcome.to_dict()).api == usage
    assert result_from_dict(CommandResult.completed("c", {}).to_raw_dict()).api is None


def test_cache_entries_are_isolated_per_provider(tmp_path):
    cache = Cache(cache_dir=tmp_path / "cache")
    cache.set("/getsearchchannels", {"term": "x"}, [DIRECT_CHANNEL], namespace="direct")

    assert cache.get("/getsearchchannels", {"term": "x"}, namespace="rapidapi") is None
    assert cache.get("/getsearchchannels", {"term": "x"}) is None
    assert cache.get("/getsearchchannels", {"term": "x"}, namespace="direct") == [DIRECT_CHANNEL]


def test_cache_hits_are_recorded_for_the_provider_that_stored_them(transport):
    transport.queues["rapidapi"].append(FakeResponse(200, [], headers=RAPID_PLAN))
    client = make_client(transport, use_cache=True)

    client.get_videos("dQw4w9WgXcQ")
    client.get_videos("dQw4w9WgXcQ")

    assert len(transport.calls) == 1
    assert client.last_cache_hit is True
    assert api_usage.snapshot()["providers"]["rapidapi"] == {
        "requests": 1, "cache_hits": 1, "requests_limit": 50000, "requests_remaining": 49903,
    }


# ---------------------------------------------------------------- usage provenance


def test_usage_summarizes_credits_plans_and_failovers():
    api_usage.record_response("rapidapi", RAPID_PLAN)
    api_usage.record_response("direct", CREDITS)
    api_usage.record_response("direct", {**CREDITS, "X-API-Credit-Cost": "5", "X-API-Credits-Remaining": "7994"})
    api_usage.record_failover("/getvideos", "rapidapi", "direct", "auth")

    usage = api_usage.snapshot()
    assert usage["providers"]["direct"]["credits_charged"] == 6
    assert usage["providers"]["direct"]["credits_remaining"] == 7994
    assert api_usage.summary_line(usage) == (
        "Filmot API: direct 2 requests, 6 credits (7,994 credits left; resets 2026-11-19)"
        " | rapidapi 1 request (49,903 of 50,000 requests left)"
        " | failover rapidapi -> direct on /getvideos (auth)"
    )


def test_results_and_events_carry_usage_only_when_the_api_was_used():
    outcome = CommandResult.completed("channels", {"query": "x"})
    assert api_usage.attach_usage(outcome) is outcome
    assert "api" not in outcome.to_raw_dict()["_filmot"]

    api_usage.record_response("direct", CREDITS)
    attached = api_usage.attach_usage(outcome)
    assert attached.to_raw_dict()["_filmot"]["api"]["providers"]["direct"]["credits_charged"] == 1
    assert api_usage.attach_usage(attached) is attached

    event = attached.to_event("channels").to_dict()
    assert event["api"] == attached.api
    assert normalize_event_dict(event)["api"] == attached.api
    assert "api" not in EventRecord(kind="k", command="c", status="completed", data={}, ts="t").to_dict()


def test_failure_events_carry_provider_provenance(http, monkeypatch):
    monkeypatch.setattr(app_config, "RAPIDAPI_KEY", "")
    quota = {"error": "quota_exhausted", "cost": 1, "used": 8000, "limit": 8000,
             "remaining": 0, "reset_at": "2026-11-19T00:00:00+00:00"}
    http.queues["direct"].append(FakeResponse(429, quota))

    result = invoke("--session", "quota-check", "channels", "lab")

    assert result.exit_code == 1
    assert "has no credits left (used 8000 of 8000; resets 2026-11-19" in result.output
    failed = [event for event in read_events("quota-check") if event["status"] == "failed"]
    assert failed[-1]["api"]["providers"]["direct"]["requests"] == 1
    assert len(http.calls) == 1


def test_events_without_api_use_carry_no_usage(tmp_path):
    from filmot.ledger import log_event

    log_event("library-list", topic="usage-check", data_dir=tmp_path, query="x")
    events = read_events("usage-check", tmp_path)
    assert events and "api" not in events[-1]


def test_main_prints_the_usage_summary_on_stderr(monkeypatch, capsys):
    from filmot import cli as cli_module

    api_usage.record_response("direct", CREDITS)
    monkeypatch.setattr(sys, "argv", ["filmot", "--version"])

    assert cli_module.main() == 0
    assert capsys.readouterr().err.strip() == (
        "Filmot API: direct 1 request, 1 credit (7,999 credits left; resets 2026-11-19)"
    )


def test_main_prints_nothing_when_the_api_was_not_used(monkeypatch, capsys):
    from filmot import cli as cli_module

    monkeypatch.setattr(sys, "argv", ["filmot", "--version"])
    assert cli_module.main() == 0
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------- channel shapes


def test_channel_normalization_maps_rapidapi_fields_and_keeps_originals():
    normalized = normalize_channel_suggestion(RAPID_CHANNEL)

    assert normalized["channelid"] == "UCrapid0000000000000000b"
    assert normalized["name"] == "Rapid Lab"
    assert normalized["handle"] == "@rapidlab"
    assert normalized["totalviewcount"] == 42000
    assert normalized["subcountp"] == "1.5K"
    assert normalize_channel_suggestion(normalized) == normalized
    assert normalize_channel_suggestion(DIRECT_CHANNEL) == DIRECT_CHANNEL


def test_channel_normalization_handles_envelopes_and_errors():
    envelope = {"channels": [RAPID_CHANNEL]}
    assert normalize_channel_suggestions(envelope)["channels"][0]["name"] == "Rapid Lab"
    assert envelope["channels"][0] is RAPID_CHANNEL
    error = {"error": "x"}
    assert normalize_channel_suggestions(error) is error


@pytest.mark.parametrize("fixture", ["search_channels.json", "search_channels_direct.json"])
def test_recorded_fixtures_normalize_to_the_same_fields(fixture):
    channels = normalize_channel_suggestions(json.loads((FIXTURES / fixture).read_text("utf-8")))
    for channel in channels:
        assert channel["channelid"].startswith("recorded-channel-")
        assert channel["name"].startswith("Recorded channel ")
        assert channel["handle"].startswith("@recorded-channel-")
        assert isinstance(channel["totalviewcount"], int)


def test_channels_command_renders_direct_shapes_with_country(http):
    http.queues["direct"].append(FakeResponse(200, [DIRECT_CHANNEL], headers=CREDITS))

    result = invoke("--api-provider", "direct", "channels", "lab")

    assert result.exit_code == 0, result.output
    assert "Direct Lab" in result.output
    assert "@directlab" in result.output
    assert "Iceland" in result.output
    assert "UCdirect000000000000000a" in result.output
    assert http.calls[0]["headers"] == {"X-API-Key": "test-direct-key"}


def test_channels_command_renders_rapidapi_shapes(http):
    http.queues["rapidapi"].append(FakeResponse(200, [RAPID_CHANNEL], headers=RAPID_PLAN))

    result = invoke("channels", "lab")

    assert result.exit_code == 0, result.output
    assert "Rapid Lab" in result.output
    assert "@rapidlab" in result.output
    assert "1.5K" in result.output
    assert "Country" not in result.output


def test_raw_channels_after_failover_match_the_ledger(http):
    http.queues["rapidapi"].append(FakeResponse(403, {"message": "You are not subscribed to this API."}))
    http.queues["direct"].append(FakeResponse(200, [DIRECT_CHANNEL], headers=CREDITS))

    result = invoke("--session", "provider-check", "channels", "lab", "--raw")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    channel = payload["channels"][0]
    assert (channel["channelid"], channel["name"], channel["handle"]) == (
        "UCdirect000000000000000a", "Direct Lab", "@directlab",
    )
    usage = payload["_filmot"]["api"]
    assert usage["failovers"] == [{
        "endpoint": "/getsearchchannels", "from": "rapidapi", "to": "direct", "reason": "auth",
    }]
    assert usage["providers"]["direct"]["credits_remaining"] == 7999
    logged = [event for event in read_events("provider-check") if event["kind"] == "channels"]
    assert logged and logged[-1]["api"] == usage


def test_channel_filter_resolution_matches_direct_handles(http, monkeypatch):
    from filmot.commands.search import _resolve_channel_filter

    monkeypatch.setattr(app_config, "API_PROVIDER", "direct")
    http.queues["direct"].append(FakeResponse(200, [DIRECT_CHANNEL, {**DIRECT_CHANNEL, "channelid": "UCother", "name": "Other", "handle": "@other"}], headers=CREDITS))
    client = FilmotClient(use_cache=False)
    client.rate_limiter = MagicMock()

    ids, resolved = _resolve_channel_filter(client, "@directlab", 5)

    assert ids == "UCdirect000000000000000a"
    assert resolved == [{"id": "UCdirect000000000000000a", "name": "Direct Lab"}]


def test_channel_export_maps_both_provider_shapes(tmp_path):
    from filmot.export import export_csv

    path = export_csv([RAPID_CHANNEL, DIRECT_CHANNEL], str(tmp_path / "channels.csv"), "channels")
    rows = Path(path).read_text(encoding="utf-8").splitlines()

    assert rows[1].startswith("UCrapid0000000000000000b,Rapid Lab,@rapidlab,1500,42000,")
    assert rows[2].startswith("UCdirect000000000000000a,Direct Lab,@directlab,2100,99000,")


# ---------------------------------------------------------------- configuration display


def test_config_rows_describe_routes_without_credentials(monkeypatch):
    rows = dict(describe_configuration())
    assert rows["API Provider"] == "auto (default)"
    assert rows["Legacy routes"] == "rapidapi -> direct"
    assert rows["Direct-only routes"] == "direct"

    monkeypatch.setattr(app_config, "DIRECT_API_KEY", "")
    rows = dict(describe_configuration())
    assert rows["Filmot API Key (direct)"] == "not configured"
    assert rows["Direct-only routes"].startswith("unavailable: /getchannels is only available")
    assert "test-rapidapi-key" not in json.dumps(rows)


def test_config_never_prints_userinfo_from_a_base_url_override(monkeypatch):
    monkeypatch.setattr(app_config, "DIRECT_BASE_URL", "https://user:hunter2@staging.filmot.invalid/api/v1")

    rows = dict(describe_configuration())

    assert rows["Direct API URL"] == "https://***:***@staging.filmot.invalid/api/v1"


def test_root_option_selects_the_provider_for_one_invocation():
    result = CliRunner().invoke(cli, ["--api-provider", "DIRECT", "config"])
    assert result.exit_code == 0, result.output
    assert "API Provider: direct (--api-provider)" in result.output
    assert "Legacy routes: direct" in result.output

    rejected = CliRunner().invoke(cli, ["--api-provider", "cheapest", "config"])
    assert rejected.exit_code == 2
