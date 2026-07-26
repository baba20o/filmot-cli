"""Tests for filmot.transcript module."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import time
import filmot.transcript as transcript_module

from filmot._process import (
    IsolatedWorkerProtocolError,
    IsolatedWorkerTimeout,
)
from filmot.transcript import (
    TranscriptRouteTimeout,
    describe_routing_plan,
    extract_video_id,
    format_timestamp,
    get_transcript,
    get_transcript_with_fallback,
    probe_pool_session,
    routing_plan,
)


def _worker_success(result):
    return {
        "protocol_version": 1,
        "status": "success",
        "result": result,
    }


def _worker_error(status, error_type, message, failure_kind=None):
    error = {
        "error_type": error_type,
        "message": message,
    }
    if failure_kind is not None:
        error["failure_kind"] = failure_kind
    return {
        "protocol_version": 1,
        "status": status,
        "error": error,
    }


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

    @patch("filmot.transcript._call_with_route_timeout")
    def test_success(self, mock_route):
        """Successful transcript fetch returns expected structure."""
        mock_route.return_value = _worker_success({
            "video_id": "abc12345678",
            "language": "en",
            "is_generated": True,
            "segments": [
                {"text": "Hello", "start": 0.0, "duration": 1.5},
                {"text": "world", "start": 1.5, "duration": 1.0},
            ],
            "full_text": "Hello world",
            "duration_seconds": 2.5,
            "segment_count": 2,
        })

        result = get_transcript("abc12345678")

        assert "error" not in result
        assert result["video_id"] == "abc12345678"
        assert result["language"] == "en"
        assert result["is_generated"] is True
        assert result["segment_count"] == 2
        assert "Hello" in result["full_text"]
        assert "world" in result["full_text"]

    @patch("filmot.transcript._call_with_route_timeout")
    def test_transcripts_disabled(self, mock_route):
        mock_route.return_value = _worker_error(
            "terminal",
            "TranscriptsDisabled",
            "captions disabled",
        )

        result = get_transcript("abc12345678")
        assert "error" in result
        assert "disabled" in result["error"].lower()

    @patch("filmot.transcript._call_with_route_timeout")
    def test_video_unavailable(self, mock_route):
        mock_route.return_value = _worker_error(
            "terminal",
            "VideoUnavailable",
            "unavailable",
        )

        result = get_transcript("abc12345678")
        assert "error" in result
        assert "unavailable" in result["error"].lower()

    @patch("filmot.transcript._call_with_route_timeout")
    def test_url_input_extracts_id(self, mock_route):
        """Passing a URL correctly extracts the video ID."""
        mock_route.return_value = _worker_success({
            "video_id": "dQw4w9WgXcQ",
            "language": "en",
            "is_generated": False,
            "segments": [{"text": "Test", "start": 0.0, "duration": 1.0}],
            "full_text": "Test",
            "duration_seconds": 1.0,
            "segment_count": 1,
        })

        result = get_transcript("https://youtu.be/dQw4w9WgXcQ")
        assert result["video_id"] == "dQw4w9WgXcQ"
        assert mock_route.call_args.kwargs["video_id"] == "dQw4w9WgXcQ"

    @patch("filmot.transcript._call_with_route_timeout")
    def test_pool_session_retried_on_transport_error(self, mock_route):
        """A transport-class failure on one pool session triggers a retry on the next."""
        from filmot.proxy_pool import WebshareProxyPool, WebshareSession

        # Two fake sessions in the pool, no auto-refresh.
        pool = WebshareProxyPool.__new__(WebshareProxyPool)
        pool.token = "fake"
        pool.gateway_host = "p.webshare.io"
        pool.gateway_port = 80
        pool.refresh_hours = 24
        pool.max_sessions = 10
        pool.countries = []
        pool.request_timeout = 5
        pool.state_path = Path("/tmp/pp_test_state.json")
        import threading as _t
        pool._lock = _t.Lock()
        pool._sessions = [
            WebshareSession(id="b-US-1", username="u1", password="p1", country_code="US"),
            WebshareSession(id="b-US-2", username="u2", password="p2", country_code="US"),
        ]
        pool._last_refresh = 9e12  # far future, never refresh
        pool._cursor = 0
        pool._file_backed = False
        pool._health_store = None
        pool._in_flight_ids = set()

        mock_route.side_effect = [
            _worker_error(
                "transport_error",
                "ProxyError",
                "Tunnel connection failed: 400 Bad Request",
                "connection",
            ),
            _worker_success({
                "video_id": "abc12345678",
                "language": "en",
                "is_generated": True,
                "segments": [],
                "full_text": "Recovered via second session",
                "duration_seconds": 10,
                "segment_count": 1,
            }),
        ]

        with patch("filmot.transcript.get_pool", return_value=pool), \
             patch.dict("os.environ", {"FILMOT_PROXY_MODE": "proxy-only"}):
            result = get_transcript("abc12345678")

        assert result["full_text"] == "Recovered via second session"
        assert result["route"].startswith("pool:webshare-api:session-")
        assert "b-US-2" not in result["route"]
        assert mock_route.call_count == 2
        # First session should now be in cooldown after the connection error.
        assert pool._sessions[0].fail_other + pool._sessions[0].fail_429 + pool._sessions[0].fail_blocked == 1
        assert pool._sessions[0].cooldown_until > 0
        assert pool._sessions[1].success == 1

    @patch("filmot.transcript._call_with_route_timeout")
    @patch("filmot.transcript._iter_routes")
    def test_direct_success_retains_prior_pool_route_diagnostics(
        self, mock_routes, mock_route
    ):
        pool_outcome = MagicMock()
        pool_route = "pool:webshare-api:session-deadbeef"
        mock_routes.return_value = iter([
            (
                pool_route,
                {
                    "kind": "generic-proxy",
                    "http_proxy": "http://u:p@proxy.invalid",
                    "https_proxy": "http://u:p@proxy.invalid",
                },
                pool_outcome,
            ),
            ("direct", {"kind": "direct"}, None),
        ])
        mock_route.side_effect = [
            _worker_error(
                "transport_error",
                "ProxyError",
                "Tunnel connection failed: 400 Bad Request",
                "connection",
            ),
            _worker_success({
                "video_id": "abc12345678",
                "language": "en",
                "is_generated": True,
                "segments": [],
                "full_text": "Recovered directly",
                "duration_seconds": 1,
                "segment_count": 0,
            }),
        ]

        result = get_transcript("abc12345678")

        assert result["route"] == "direct"
        assert result["routes_tried"] == [pool_route, "direct"]
        assert len(result["route_errors"]) == 1
        assert result["route_errors"][0]["route"] == pool_route
        assert result["route_errors"][0]["kind"] == "connection"
        assert result["route_errors"][0]["error_type"] == "ProxyError"
        pool_outcome.assert_called_once_with(
            "failure",
            ("connection", "Tunnel connection failed: 400 Bad Request"),
        )

    @patch("filmot.transcript._call_with_route_timeout")
    @patch("filmot.transcript._iter_routes")
    def test_internal_worker_error_releases_route_without_penalizing_it(
        self, mock_routes, mock_route
    ):
        outcome = MagicMock()
        secret = "route-password"
        route_config = {
            "kind": "generic-proxy",
            "http_proxy": f"http://user:{secret}@proxy.invalid",
            "https_proxy": f"http://user:{secret}@proxy.invalid",
        }
        mock_routes.return_value = iter([
            ("pool:test:session-redacted", route_config, outcome),
        ])
        mock_route.return_value = _worker_error(
            "internal_error",
            "ValueError",
            f"bad worker setup for http://user:{secret}@proxy.invalid",
        )

        result = get_transcript("abc12345678")

        assert result["error_type"] == "ValueError"
        assert result["route_errors"][0]["kind"] == "internal"
        assert secret not in result["error"]
        outcome.assert_called_once_with("release", None)

    @patch("filmot.transcript._call_with_route_timeout")
    def test_internal_worker_error_leaves_actual_proxy_health_unchanged(
        self, mock_route, tmp_path
    ):
        from filmot.proxy_pool import WebshareSession
        from test_proxy_pool import _make_pool

        session = WebshareSession(
            id="credential",
            username="proxy-user",
            password="proxy-password",
        )
        pool = _make_pool(tmp_path, sessions=[session])
        pool._health_store = None
        pool._in_flight_ids = set()
        mock_route.return_value = _worker_error(
            "internal_error",
            "ValueError",
            "parser invariant failed",
        )

        with patch("filmot.transcript.get_pool", return_value=pool), \
             patch.dict("os.environ", {"FILMOT_PROXY_MODE": "proxy-only"}):
            result = get_transcript("abc12345678")

        assert result["error_type"] == "ValueError"
        assert session.fail_429 == 0
        assert session.fail_blocked == 0
        assert session.fail_other == 0
        assert session.consecutive_failures == 0
        assert session.id not in pool._in_flight_ids

    @patch("filmot.transcript._call_with_route_timeout")
    @patch("filmot.transcript._iter_routes")
    def test_protocol_error_releases_route_without_penalizing_it(
        self, mock_routes, mock_route
    ):
        outcome = MagicMock()
        mock_routes.return_value = iter([
            (
                "pool:test:session-redacted",
                {
                    "kind": "generic-proxy",
                    "http_proxy": "http://user:password@proxy.invalid",
                    "https_proxy": "http://user:password@proxy.invalid",
                },
                outcome,
            ),
        ])
        mock_route.side_effect = IsolatedWorkerProtocolError(
            "worker returned invalid JSON"
        )

        result = get_transcript("abc12345678")

        assert result["error_type"] == "IsolatedWorkerProtocolError"
        assert result["route_errors"][0]["kind"] == "internal"
        outcome.assert_called_once_with("release", None)

    @patch("filmot.transcript._call_with_route_timeout")
    @patch("filmot.transcript._iter_routes")
    def test_parent_interruption_releases_route_before_propagating(
        self, mock_routes, mock_route
    ):
        outcome = MagicMock()
        mock_routes.return_value = iter([
            (
                "pool:test:session-redacted",
                {
                    "kind": "generic-proxy",
                    "http_proxy": "http://user:password@proxy.invalid",
                    "https_proxy": "http://user:password@proxy.invalid",
                },
                outcome,
            ),
        ])
        mock_route.side_effect = KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            get_transcript("abc12345678")

        outcome.assert_called_once_with("release", None)

    @patch("filmot.transcript._call_with_route_timeout")
    def test_terminal_error_short_circuits_routes(self, mock_route):
        """TranscriptsDisabled on the first route stops route iteration."""
        from filmot.proxy_pool import WebshareProxyPool, WebshareSession
        import threading as _t

        pool = WebshareProxyPool.__new__(WebshareProxyPool)
        pool.token = "fake"
        pool.gateway_host = "p.webshare.io"
        pool.gateway_port = 80
        pool.refresh_hours = 24
        pool.max_sessions = 10
        pool.countries = []
        pool.request_timeout = 5
        pool.state_path = Path("/tmp/pp_test_state.json")
        pool._lock = _t.Lock()
        pool._sessions = [
            WebshareSession(id="b-US-1", username="u1", password="p1", country_code="US"),
            WebshareSession(id="b-US-2", username="u2", password="p2", country_code="US"),
        ]
        pool._last_refresh = 9e12
        pool._cursor = 0
        pool._file_backed = False
        pool._health_store = None
        pool._in_flight_ids = set()

        mock_route.return_value = _worker_error(
            "terminal",
            "TranscriptsDisabled",
            "captions disabled",
        )

        with patch("filmot.transcript.get_pool", return_value=pool), \
             patch.dict("os.environ", {"FILMOT_PROXY_MODE": "proxy-only"}):
            result = get_transcript("abc12345678")

        assert "disabled" in result["error"].lower()
        # Only one fetch attempt: terminal error short-circuits the route ladder.
        assert mock_route.call_count == 1
        assert pool._sessions[0].success == 1
        assert pool._sessions[0].consecutive_failures == 0

    @patch("filmot.transcript._call_with_route_timeout")
    @patch("filmot.transcript._iter_routes")
    def test_terminal_result_has_normalized_route_metadata(
        self, mock_routes, mock_route
    ):
        mock_routes.return_value = iter([
            ("direct", {"kind": "direct"}, None),
        ])
        mock_route.return_value = _worker_error(
            "terminal",
            "TranscriptsDisabled",
            "captions disabled",
        )

        result = get_transcript("abc12345678")

        assert result["error_type"] == "TranscriptsDisabled"
        assert result["route"] == "direct"
        assert result["routes_tried"] == ["direct"]
        assert result["route_errors"] == []

    @patch("filmot.transcript._call_with_route_timeout")
    def test_primary_attempts_are_always_process_isolated(
        self, mock_route, monkeypatch
    ):
        payload = {
            "video_id": "abc12345678",
            "language": "en",
            "is_generated": True,
            "segments": [],
            "full_text": "isolated",
            "duration_seconds": 1,
            "segment_count": 0,
        }
        mock_route.side_effect = [
            _worker_success(dict(payload)),
            _worker_success(dict(payload)),
        ]
        monkeypatch.setattr(transcript_module, "_initialized", True)
        monkeypatch.setattr(transcript_module, "_api", MagicMock())
        monkeypatch.setattr(transcript_module, "_proxy_source", "direct")
        monkeypatch.setenv("FILMOT_PROXY_MODE", "direct-only")

        isolated = get_transcript(
            "abc12345678",
            fresh_primary=True,
        )
        regular = get_transcript("abc12345678")

        assert isolated["route"] == "direct"
        assert regular["route"] == "direct"
        assert mock_route.call_count == 2
        assert mock_route.call_args_list[0].args[0] == {"kind": "direct"}
        assert mock_route.call_args_list[1].args[0] == {"kind": "direct"}

    @patch("filmot.transcript._call_with_route_timeout")
    def test_route_timeout_advances_to_next_pool_session(self, mock_route, tmp_path):
        """A killed route is marked failed and does not stop the ladder."""
        from filmot.proxy_pool import WebshareProxyPool, WebshareSession
        import threading as _t

        pool = WebshareProxyPool.__new__(WebshareProxyPool)
        pool.token = "fake"
        pool.gateway_host = "p.webshare.io"
        pool.gateway_port = 80
        pool.refresh_hours = 24
        pool.max_sessions = 10
        pool.countries = []
        pool.request_timeout = 5
        pool.recent_success_hours = 24
        pool.state_path = tmp_path / "pool.json"
        pool._lock = _t.Lock()
        pool._sessions = [
            WebshareSession(id="secret-user-1", username="u1", password="p1"),
            WebshareSession(id="secret-user-2", username="u2", password="p2"),
        ]
        pool._last_refresh = 9e12
        pool._cursor = 0
        pool._file_backed = False
        pool._health_store = None
        pool._in_flight_ids = set()
        events = []

        def route_side_effect(*args, **kwargs):
            if mock_route.call_count == 1:
                raise TranscriptRouteTimeout(kwargs["route"], 0.03)
            return _worker_success({
                "video_id": "abc12345678",
                "language": "en",
                "is_generated": True,
                "segments": [],
                "full_text": "second route",
                "duration_seconds": 1,
                "segment_count": 0,
            })

        mock_route.side_effect = route_side_effect
        started = time.monotonic()
        with patch("filmot.transcript.get_pool", return_value=pool), \
             patch.dict("os.environ", {"FILMOT_PROXY_MODE": "proxy-only"}):
            result = get_transcript(
                "abc12345678",
                route_timeout=0.03,
                progress_callback=events.append,
            )

        assert time.monotonic() - started < 0.8
        assert result["full_text"] == "second route"
        assert result["route_errors"][0]["worker_terminated"] is True
        assert pool._sessions[0].last_failure_kind == "connection"
        assert pool._sessions[1].success == 1
        assert [event["event"] for event in events] == [
            "pool_prepare",
            "route_start",
            "route_timeout",
            "route_start",
            "route_success",
        ]
        assert "secret-user" not in " ".join(
            str(event.get("route", "")) for event in events
        )

    @patch("filmot.transcript._call_with_route_timeout")
    def test_all_route_timeouts_return_structured_diagnostics(
        self, mock_route, tmp_path
    ):
        from filmot.proxy_pool import WebshareProxyPool, WebshareSession
        import threading as _t

        pool = WebshareProxyPool.__new__(WebshareProxyPool)
        pool.token = "fake"
        pool.gateway_host = "p.webshare.io"
        pool.gateway_port = 80
        pool.refresh_hours = 24
        pool.max_sessions = 1
        pool.countries = []
        pool.request_timeout = 5
        pool.recent_success_hours = 24
        pool.state_path = tmp_path / "pool.json"
        pool._lock = _t.Lock()
        pool._sessions = [
            WebshareSession(id="user", username="u", password="p"),
        ]
        pool._last_refresh = 9e12
        pool._cursor = 0
        pool._file_backed = False
        pool._health_store = None
        pool._in_flight_ids = set()
        mock_route.side_effect = lambda *a, **k: (
            _ for _ in ()
        ).throw(TranscriptRouteTimeout(k["route"], 0.01))

        with patch("filmot.transcript.get_pool", return_value=pool), \
             patch.dict("os.environ", {"FILMOT_PROXY_MODE": "proxy-only"}):
            result = get_transcript("abc12345678", route_timeout=0.01)

        assert result["error_type"] == "TranscriptRouteTimeout"
        assert result["route_errors"][0]["kind"] == "connection"
        assert result["route_errors"][0]["worker_terminated"] is True
        assert len(result["routes_tried"]) == 1

    @patch("filmot.transcript._call_with_route_timeout")
    def test_progress_callback_errors_do_not_break_fetch(self, mock_route):
        mock_route.return_value = _worker_success({
            "video_id": "abc12345678",
            "language": "en",
            "is_generated": True,
            "segments": [],
            "full_text": "",
            "duration_seconds": 0,
            "segment_count": 0,
        })
        def broken_callback(event):
            raise RuntimeError("renderer is broken")

        result = get_transcript(
            "abc12345678",
            progress_callback=broken_callback,
        )
        assert "error" not in result


class TestTransportConfiguration:
    def test_bounded_session_supplies_connect_and_read_timeout(self):
        session = transcript_module._BoundedSession(1.5, 4.0)
        with patch(
            "requests.Session.request",
            return_value=MagicMock(),
        ) as request:
            session.get("https://example.test")
        assert request.call_args.kwargs["timeout"] == (1.5, 4.0)

    def test_explicit_request_timeout_is_preserved(self):
        session = transcript_module._BoundedSession(1.5, 4.0)
        with patch(
            "requests.Session.request",
            return_value=MagicMock(),
        ) as request:
            session.get("https://example.test", timeout=9)
        assert request.call_args.kwargs["timeout"] == 9

    def test_timeout_error_is_clear(self):
        error = TranscriptRouteTimeout("pool:session-123", 2.5)
        assert error.route == "pool:session-123"
        assert "2.5 seconds" in str(error)
        assert error.worker_terminated is True

    @patch("filmot.transcript.run_json_worker")
    def test_process_timeout_maps_to_route_timeout(self, mock_worker):
        mock_worker.side_effect = IsolatedWorkerTimeout(2.5, 123, -9)

        with pytest.raises(TranscriptRouteTimeout) as captured:
            transcript_module._call_with_route_timeout(
                {"kind": "direct"},
                route="direct",
                timeout_seconds=2.5,
                video_id="abc12345678",
                languages=["en"],
                preserve_formatting=False,
            )

        assert captured.value.route == "direct"
        assert captured.value.worker_terminated is True


class TestRoutingPlan:
    def test_initializes_primary_before_reporting_label(self, monkeypatch):
        fake_api = MagicMock()
        monkeypatch.setattr(transcript_module, "_initialized", False)
        monkeypatch.setattr(transcript_module, "_api", None)
        monkeypatch.setattr(transcript_module, "_proxy_source", "direct")
        monkeypatch.setattr(
            transcript_module,
            "_primary_from_environment",
            lambda: (fake_api, "env-proxy", True),
        )
        monkeypatch.setattr(transcript_module, "get_pool", lambda: None)
        monkeypatch.setenv("FILMOT_PROXY_MODE", "auto")

        plan = routing_plan()

        assert plan["primary"] == "env-proxy"
        assert plan["routes"][-1]["label"] == "env-proxy"
        assert plan["has_direct_route"] is False
        assert describe_routing_plan(plan) == "env-proxy"

    def test_proxy_only_without_pool_does_not_claim_direct_fallback(
        self, monkeypatch
    ):
        monkeypatch.setattr(transcript_module, "_initialized", False)
        monkeypatch.setattr(transcript_module, "_api", None)
        monkeypatch.setattr(transcript_module, "get_pool", lambda: None)
        monkeypatch.setenv("FILMOT_PROXY_MODE", "proxy-only")

        plan = routing_plan()

        assert plan["routes"] == []
        assert plan["has_direct_route"] is False
        assert "no usable routes" in describe_routing_plan(plan)

    def test_direct_only_ignores_environment_proxy(self, monkeypatch):
        monkeypatch.setattr(transcript_module, "_initialized", False)
        monkeypatch.setattr(transcript_module, "_api", None)
        monkeypatch.setenv("FILMOT_PROXY_MODE", "direct-only")
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")

        plan = routing_plan()

        assert plan["primary"] == "direct"
        assert [route["label"] for route in plan["routes"]] == ["direct"]
        assert plan["has_direct_route"] is True
        assert transcript_module.get_api()._fetcher._http_client.trust_env is False

    def test_explicit_proxy_can_override_token_backed_proxy_only_default(
        self, monkeypatch
    ):
        monkeypatch.setenv("WEBSHARE_API_TOKEN", "configured-token")
        monkeypatch.delenv("FILMOT_PROXY_MODE", raising=False)
        monkeypatch.setattr(transcript_module, "_api", None)
        monkeypatch.setattr(transcript_module, "_initialized", False)
        monkeypatch.setattr(transcript_module, "_proxy_configured", False)
        monkeypatch.setattr(transcript_module, "_proxy_source", "direct")
        monkeypatch.setattr(
            transcript_module,
            "_build_generic_proxy_api",
            lambda *args: MagicMock(),
        )

        transcript_module.configure_proxy(
            http_proxy="http://explicit.invalid:8080",
            exclusive=True,
        )
        plan = routing_plan()

        assert plan["mode"] == "primary-only"
        assert [route["label"] for route in plan["routes"]] == [
            "explicit-proxy"
        ]
        assert plan["pool_configured"] is False

    def test_proxy_pool_refresh_failure_is_returned_clearly(
        self, monkeypatch
    ):
        from filmot.proxy_pool import WebshareProxyError

        pool = MagicMock()
        pool.source = "webshare-api"
        pool.available_count.return_value = 0
        pool.pick.side_effect = WebshareProxyError(
            "Webshare rejected the API token (401)"
        )
        monkeypatch.setenv("FILMOT_PROXY_MODE", "proxy-only")
        monkeypatch.setattr(transcript_module, "_initialized", True)
        monkeypatch.setattr(transcript_module, "_api", MagicMock())
        monkeypatch.setattr(transcript_module, "_proxy_source", "direct")
        monkeypatch.setattr(transcript_module, "_proxy_configured", False)
        monkeypatch.setattr(transcript_module, "get_pool", lambda: pool)
        events = []

        result = get_transcript(
            "abc12345678",
            progress_callback=events.append,
        )

        assert result["error_type"] == "WebshareProxyError"
        assert "401" in result["error"]
        assert result["route_errors"][0]["kind"] == "pool_refresh"
        assert [event["event"] for event in events] == [
            "pool_prepare",
            "pool_prepare_failed",
            "routes_exhausted",
        ]


class TestProbePoolSession:
    @patch("filmot.transcript._call_with_route_timeout")
    def test_probe_uses_shared_timeout_and_updates_health(
        self, mock_route, tmp_path
    ):
        from filmot.proxy_pool import WebshareSession
        from test_proxy_pool import _make_pool

        session = WebshareSession(id="credential", username="u", password="p")
        pool = _make_pool(tmp_path, sessions=[session])
        pool._health_store = None
        pool._in_flight_ids = set()
        mock_route.side_effect = TranscriptRouteTimeout(
            "pool:webshare-api:session-e265b6f5",
            0.01,
        )
        events = []

        result = probe_pool_session(
            pool,
            session,
            "abc12345678",
            route_timeout=0.01,
            progress_callback=events.append,
        )

        assert result["error_type"] == "TranscriptRouteTimeout"
        assert result["failure_kind"] == "connection"
        assert result["transport_ok"] is False
        assert result["worker_terminated"] is True
        assert session.consecutive_failures == 1
        assert [event["event"] for event in events] == [
            "route_start",
            "route_timeout",
        ]


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
        youtube_route_errors = [{
            "route": "pool:webshare-api:session-deadbeef",
            "kind": "connection",
            "error_type": "ProxyError",
            "error": "proxy failed",
            "elapsed_s": 0.5,
        }]
        mock_gt.return_value = {
            "error": "Transcripts are disabled",
            "error_type": "TranscriptsDisabled",
            "video_id": "abc12345678",
            "route": "direct",
            "routes_tried": [
                "pool:webshare-api:session-deadbeef",
                "direct",
            ],
            "route_errors": youtube_route_errors,
        }
        mock_deps.return_value = (True, "")
        mock_transcribe.return_value = ("AWS transcript text here", "en-US")

        result = get_transcript_with_fallback("abc12345678", use_aws_fallback=True)
        assert result["source"] == "aws_transcribe"
        assert result["full_text"] == "AWS transcript text here"
        assert result["language"] == "en-US"
        assert result["route"] == "aws-transcribe"
        assert result["youtube_error"] == "Transcripts are disabled"
        assert result["youtube_error_type"] == "TranscriptsDisabled"
        assert result["youtube_routes_tried"] == [
            "pool:webshare-api:session-deadbeef",
            "direct",
        ]
        assert result["routes_tried"] == [
            "pool:webshare-api:session-deadbeef",
            "direct",
            "aws-transcribe",
        ]
        assert result["route_errors"] == youtube_route_errors

    @patch("filmot.transcript.get_transcript")
    @patch("filmot.aws_transcribe.check_dependencies")
    @patch("filmot.aws_transcribe.transcribe_video")
    def test_aws_failure_retains_youtube_route_metadata(
        self, mock_transcribe, mock_deps, mock_gt
    ):
        youtube_route_errors = [{
            "route": "pool:webshare-api:session-deadbeef",
            "kind": "connection",
            "error_type": "ProxyError",
            "error": "proxy failed",
            "elapsed_s": 0.5,
        }]
        mock_gt.return_value = {
            "error": "all YouTube routes failed",
            "error_type": "ConnectionError",
            "video_id": "abc12345678",
            "routes_tried": [
                "pool:webshare-api:session-deadbeef",
                "direct",
            ],
            "route_errors": youtube_route_errors,
        }
        mock_deps.return_value = (True, "")
        mock_transcribe.side_effect = RuntimeError("AWS job failed")

        result = get_transcript_with_fallback(
            "abc12345678",
            use_aws_fallback=True,
        )

        assert result["error_type"] == "RuntimeError"
        assert result["route"] == "aws-transcribe"
        assert result["youtube_error"] == "all YouTube routes failed"
        assert result["youtube_error_type"] == "ConnectionError"
        assert result["youtube_routes_tried"] == [
            "pool:webshare-api:session-deadbeef",
            "direct",
        ]
        assert result["routes_tried"] == [
            "pool:webshare-api:session-deadbeef",
            "direct",
            "aws-transcribe",
        ]
        assert result["route_errors"] == youtube_route_errors

    @patch("filmot.transcript.get_transcript")
    @patch("filmot.aws_transcribe.check_dependencies")
    def test_aws_deps_missing(self, mock_deps, mock_gt):
        """When AWS deps are missing, returns combined error."""
        mock_gt.return_value = {"error": "Transcripts are disabled", "video_id": "abc12345678"}
        mock_deps.return_value = (False, "boto3 not installed")

        result = get_transcript_with_fallback("abc12345678", use_aws_fallback=True)
        assert "error" in result
        assert "boto3" in result["error"]
