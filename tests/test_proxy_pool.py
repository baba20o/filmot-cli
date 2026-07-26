"""Tests for filmot.proxy_pool."""

import json
import time
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import requests

import filmot.proxy_pool as pp_mod
from filmot.proxy_pool import (
    WebshareProxyPool,
    WebshareProxyError,
    WebshareSession,
    classify_transport_error,
    get_pool,
    reset_pool,
)


# ── helpers ────────────────────────────────────────────────────────

def _make_pool(tmp_path, sessions=None) -> WebshareProxyPool:
    """Construct a pool that won't try to talk to Webshare on its own."""
    pool = WebshareProxyPool.__new__(WebshareProxyPool)
    pool.token = "fake-token"
    pool.gateway_host = "p.webshare.io"
    pool.gateway_port = 80
    pool.refresh_hours = 24
    pool.max_sessions = 25
    pool.countries = []
    pool.request_timeout = 5
    pool.recent_success_hours = 24
    pool.state_path = tmp_path / "webshare_pool.json"
    pool._lock = threading.Lock()
    pool._sessions = list(sessions or [])
    pool._last_refresh = time.time()  # not stale
    pool._cursor = 0
    pool._file_backed = False
    pool._session_file_path = None
    return pool


def _api_payload(*ids_and_countries):
    return {
        "results": [
            {
                "id": id_,
                "username": f"user-{id_}",
                "password": f"pass-{id_}",
                "proxy_address": None,
                "port": 10000,
                "valid": True,
                "country_code": cc,
                "last_verification": "2026-05-01T00:00:00Z",
            }
            for id_, cc in ids_and_countries
        ],
        "count": len(ids_and_countries),
    }


# ── classify_transport_error ──────────────────────────────────────

class TestClassifyTransportError:
    def test_429(self):
        assert classify_transport_error(Exception("HTTP 429 Too Many Requests")) == "rate_limited"

    def test_blocked(self):
        assert classify_transport_error(Exception("IpBlocked: YouTube has blocked this IP")) == "blocked"

    def test_connection(self):
        assert classify_transport_error(Exception("Tunnel connection failed: 400")) == "connection"

    def test_ssl(self):
        assert classify_transport_error(Exception("SSL: UNEXPECTED_EOF_WHILE_READING")) == "connection"

    def test_common_connection_error_messages(self):
        assert classify_transport_error(
            requests.ConnectionError(
                "Failed to establish a new connection"
            )
        ) == "connection"
        assert classify_transport_error(
            Exception("Remote end closed connection without response")
        ) == "connection"

    def test_unknown(self):
        assert classify_transport_error(ValueError("unparseable")) == ""


# ── proxy_url ─────────────────────────────────────────────────────

class TestProxyUrl:
    def test_format(self, tmp_path):
        s = WebshareSession(id="b-US-1", username="user-x", password="secret-pw")
        pool = _make_pool(tmp_path)
        assert pool.proxy_url(s) == "http://user-x:secret-pw@p.webshare.io:80"


# ── refresh ───────────────────────────────────────────────────────

class TestRefresh:
    def test_refresh_populates_pool_and_persists(self, tmp_path):
        pool = _make_pool(tmp_path)
        pool._sessions = []
        pool._last_refresh = 0  # force stale

        with patch.object(pool, "_api_get", return_value=_api_payload(("b-US-1", "US"), ("b-CA-2", "CA"))):
            n = pool.refresh(force=True)

        assert n == 2
        assert {s.id for s in pool._sessions} == {"b-US-1", "b-CA-2"}
        # state persisted
        assert pool.state_path.exists()
        snap = json.loads(pool.state_path.read_text())
        assert {s["id"] for s in snap["sessions"]} == {"b-US-1", "b-CA-2"}

    def test_refresh_preserves_health_for_known_sessions(self, tmp_path):
        existing = WebshareSession(id="b-US-1", username="old-u", password="old-p", country_code="US")
        existing.success = 7
        existing.fail_429 = 2
        existing.cooldown_until = time.time() + 60

        pool = _make_pool(tmp_path, sessions=[existing])
        pool._last_refresh = 0  # force refresh

        with patch.object(pool, "_api_get", return_value=_api_payload(("b-US-1", "US"), ("b-DE-9", "DE"))):
            pool.refresh(force=True)

        kept = next(s for s in pool._sessions if s.id == "b-US-1")
        assert kept.success == 7
        assert kept.fail_429 == 2
        # creds were refreshed
        assert kept.username == "user-b-US-1"
        assert kept.password == "pass-b-US-1"

    def test_refresh_skips_when_fresh(self, tmp_path):
        pool = _make_pool(tmp_path, sessions=[
            WebshareSession(id="b-US-1", username="u", password="p")
        ])
        with patch.object(pool, "_api_get") as mock_get:
            n = pool.refresh()
        assert n == 1
        mock_get.assert_not_called()

    def test_refresh_passes_country_filter(self, tmp_path):
        pool = _make_pool(tmp_path)
        pool.countries = ["US", "CA"]
        pool._last_refresh = 0
        with patch.object(pool, "_api_get", return_value=_api_payload(("b-US-1", "US"))) as mock_get:
            pool.refresh(force=True)
        args, kwargs = mock_get.call_args
        assert args[0] == "/api/v2/proxy/list/"
        assert kwargs["params"]["country_code__in"] == "US,CA"
        assert kwargs["params"]["mode"] == "backbone"

    def test_refresh_401_raises(self, tmp_path):
        pool = _make_pool(tmp_path)
        pool._last_refresh = 0
        fake = MagicMock(status_code=401, text="bad token")
        with patch("filmot.proxy_pool.requests.get", return_value=fake):
            with pytest.raises(WebshareProxyError, match="401"):
                pool.refresh(force=True)


# ── pick / round-robin / cooldown ─────────────────────────────────

class TestPick:
    def test_round_robin(self, tmp_path):
        pool = _make_pool(tmp_path, sessions=[
            WebshareSession(id="b-US-1", username="u1", password="p1"),
            WebshareSession(id="b-US-2", username="u2", password="p2"),
            WebshareSession(id="b-US-3", username="u3", password="p3"),
        ])
        ids = [pool.pick().id for _ in range(6)]
        assert ids == ["b-US-1", "b-US-2", "b-US-3", "b-US-1", "b-US-2", "b-US-3"]

    def test_skips_cooldown(self, tmp_path):
        s1 = WebshareSession(id="b-US-1", username="u1", password="p1")
        s2 = WebshareSession(id="b-US-2", username="u2", password="p2")
        s1.cooldown_until = time.time() + 600
        pool = _make_pool(tmp_path, sessions=[s1, s2])
        picked = [pool.pick().id for _ in range(3)]
        assert picked == ["b-US-2", "b-US-2", "b-US-2"]

    def test_skips_retired(self, tmp_path):
        s1 = WebshareSession(id="b-US-1", username="u1", password="p1")
        s1.retired = True
        s2 = WebshareSession(id="b-US-2", username="u2", password="p2")
        pool = _make_pool(tmp_path, sessions=[s1, s2])
        assert pool.pick().id == "b-US-2"

    def test_returns_none_when_all_unavailable(self, tmp_path):
        s1 = WebshareSession(id="b-US-1", username="u", password="p")
        s1.cooldown_until = time.time() + 600
        pool = _make_pool(tmp_path, sessions=[s1])
        assert pool.pick() is None

    def test_empty_pool_attempts_refresh(self, tmp_path):
        pool = _make_pool(tmp_path, sessions=[])
        pool._last_refresh = 0
        with patch.object(pool, "_api_get", return_value=_api_payload(("b-US-1", "US"))):
            picked = pool.pick()
        assert picked is not None and picked.id == "b-US-1"

    def test_empty_pool_preserves_refresh_error(self, tmp_path):
        pool = _make_pool(tmp_path, sessions=[])
        pool._last_refresh = 0
        with patch.object(
            pool,
            "_api_get",
            side_effect=WebshareProxyError("Webshare rejected token (401)"),
        ):
            with pytest.raises(WebshareProxyError, match="401"):
                pool.pick()

    def test_can_exclude_already_attempted_sessions(self, tmp_path):
        pool = _make_pool(tmp_path, sessions=[
            WebshareSession(id="b-US-1", username="u1", password="p1"),
            WebshareSession(id="b-US-2", username="u2", password="p2"),
        ])

        first = pool.pick()
        second = pool.pick(exclude_ids={first.id})

        assert second is not None
        assert second.id != first.id
        assert pool.pick(exclude_ids={first.id, second.id}) is None

    def test_lease_excludes_in_flight_sessions_until_release(self, tmp_path):
        first = WebshareSession(id="b-US-1", username="u1", password="p1")
        second = WebshareSession(id="b-US-2", username="u2", password="p2")
        # _make_pool deliberately constructs via __new__ and does not initialize
        # lease state, exercising backwards-compatible lazy initialization.
        pool = _make_pool(tmp_path, sessions=[first, second])

        assert pool.pick(lease=True) is first
        assert pool.pick(lease=True) is second
        assert pool.pick() is None

        pool.release(first)
        pool.release(first)  # public release is idempotent
        assert pool.pick(lease=True) is first


# ── report_success / report_failure ───────────────────────────────

class TestReportOutcome:
    def test_success_clears_cooldown_and_consec(self, tmp_path):
        s = WebshareSession(id="b-US-1", username="u", password="p")
        s.cooldown_until = time.time() + 60
        s.consecutive_failures = 3
        pool = _make_pool(tmp_path, sessions=[s])
        pool.report_success(s)
        assert s.success == 1
        assert s.consecutive_failures == 0
        assert s.cooldown_until == 0.0
        assert s.last_success_at > 0

    def test_rate_limited_short_cooldown(self, tmp_path):
        s = WebshareSession(id="b-US-1", username="u", password="p")
        pool = _make_pool(tmp_path, sessions=[s])
        before = time.time()
        pool.report_failure(s, "rate_limited", summary="429")
        assert s.fail_429 == 1
        assert s.last_failure_at >= before
        assert s.last_failure_kind == "rate_limited"
        assert pp_mod.COOLDOWN_RATE_LIMITED - 5 < (s.cooldown_until - before) < pp_mod.COOLDOWN_RATE_LIMITED + 5

    def test_blocked_long_cooldown(self, tmp_path):
        s = WebshareSession(id="b-US-1", username="u", password="p")
        pool = _make_pool(tmp_path, sessions=[s])
        before = time.time()
        pool.report_failure(s, "blocked", summary="IpBlocked")
        assert s.fail_blocked == 1
        assert (s.cooldown_until - before) > 10 * 60

    def test_outcomes_release_leased_sessions(self, tmp_path):
        successful = WebshareSession(id="b-US-1", username="u1", password="p1")
        failed = WebshareSession(id="b-US-2", username="u2", password="p2")
        pool = _make_pool(tmp_path, sessions=[successful, failed])

        assert pool.pick(lease=True) is successful
        pool.report_success(successful)
        assert successful.id not in pool._in_flight_ids

        assert pool.pick(lease=True) is failed
        pool.report_failure(failed, "connection", summary="timed out")
        assert failed.id not in pool._in_flight_ids
        assert pool.status_snapshot()["in_flight"] == 0

    def test_failure_summary_redacts_proxy_credentials(self, tmp_path):
        s = WebshareSession(
            id="b-US-1",
            username="secret-user",
            password="secret-password",
        )
        pool = _make_pool(tmp_path, sessions=[s])

        pool.report_failure(
            s,
            "connection",
            summary=(
                "ProxyError http://secret-user:"
                "secret-password@proxy.example:80 failed"
            ),
        )

        assert "secret-user" not in s.last_error
        assert "secret-password" not in s.last_error
        assert "***:***@proxy.example" in s.last_error

    def test_retires_after_threshold(self, tmp_path):
        s = WebshareSession(id="b-US-1", username="u", password="p")
        pool = _make_pool(tmp_path, sessions=[s])
        for _ in range(pp_mod.RETIRE_AFTER_CONSECUTIVE_FAILURES):
            pool.report_failure(s, "connection", summary="boom")
        assert s.retired is True


# ── status snapshot ───────────────────────────────────────────────

class TestStatusSnapshot:
    def test_basic_shape(self, tmp_path):
        pool = _make_pool(tmp_path, sessions=[
            WebshareSession(id="b-US-1", username="u", password="p", country_code="US"),
        ])
        snap = pool.status_snapshot()
        assert snap["total"] == 1
        assert snap["available"] == 1
        assert snap["healthy"] == 0
        assert snap["recently_healthy"] == 0
        assert snap["untested"] == 1
        assert snap["gateway"] == "p.webshare.io:80"
        assert snap["sessions"][0]["id"] == "b-US-1"
        assert snap["sessions"][0]["available"] is True
        assert snap["sessions"][0]["state"] == "ready-untested"

    def test_availability_is_not_health(self, tmp_path):
        session = WebshareSession(id="b-US-1", username="u", password="p")
        pool = _make_pool(tmp_path, sessions=[session])

        assert pool.available_count() == 1
        assert pool.recently_healthy_count() == 0

        pool.report_success(session)
        assert pool.available_count() == 1
        assert pool.recently_healthy_count() == 1
        assert pool.status_snapshot()["sessions"][0]["state"] == "ready-tested"

    def test_in_flight_session_is_not_available_or_healthy(self, tmp_path):
        session = WebshareSession(
            id="b-US-1",
            username="u",
            password="p",
            success=1,
            last_success_at=time.time(),
        )
        pool = _make_pool(tmp_path, sessions=[session])

        assert pool.pick(lease=True) is session
        snap = pool.status_snapshot()

        assert pool.available_count() == 0
        assert pool.recently_healthy_count() == 0
        assert snap["available"] == 0
        assert snap["recently_healthy"] == 0
        assert snap["healthy"] == 0
        assert snap["in_flight"] == 1
        assert snap["sessions"][0]["in_flight"] is True
        assert snap["sessions"][0]["available"] is False
        assert snap["sessions"][0]["state"] == "in-flight"

    def test_expired_success_is_available_but_stale(self, tmp_path):
        session = WebshareSession(
            id="b-US-1",
            username="u",
            password="p",
            success=1,
            last_success_at=time.time() - (25 * 3600),
        )
        pool = _make_pool(tmp_path, sessions=[session])

        snap = pool.status_snapshot()
        assert snap["available"] == 1
        assert snap["recently_healthy"] == 0
        assert snap["sessions"][0]["state"] == "ready-stale"

    def test_newer_failure_is_not_recently_healthy_after_cooldown(self, tmp_path):
        session = WebshareSession(
            id="b-US-1",
            username="u",
            password="p",
            success=1,
            last_success_at=time.time() - 10,
            last_failure_at=time.time(),
            consecutive_failures=1,
            cooldown_until=time.time() - 1,
        )
        pool = _make_pool(tmp_path, sessions=[session])

        snap = pool.status_snapshot()

        assert snap["recently_healthy"] == 0
        assert snap["sessions"][0]["state"] == "ready-failing"

    def test_cooling_and_failing_are_reported_separately(self, tmp_path):
        session = WebshareSession(id="b-US-1", username="u", password="p")
        pool = _make_pool(tmp_path, sessions=[session])
        pool.report_failure(session, "connection", summary="timed out")

        snap = pool.status_snapshot()
        assert snap["available"] == 0
        assert snap["cooling"] == 1
        assert snap["failing"] == 1
        assert snap["sessions"][0]["state"] == "cooldown"


# ── module-level get_pool ─────────────────────────────────────────

class TestGetPool:
    def test_no_token_and_no_file_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.delenv("WEBSHARE_API_TOKEN", raising=False)
        monkeypatch.setenv("WEBSHARE_SESSION_FILE", str(tmp_path / "does-not-exist.txt"))
        reset_pool()
        assert get_pool() is None
        reset_pool()

    def test_with_token_returns_pool(self, monkeypatch):
        monkeypatch.setenv("WEBSHARE_API_TOKEN", "fake")
        reset_pool()
        pool = get_pool()
        assert pool is not None
        assert pool.token == "fake"
        reset_pool()

    def test_file_backed_pool_when_no_token(self, monkeypatch, tmp_path):
        # No API token, but a session list file exists → file-backed pool
        monkeypatch.delenv("WEBSHARE_API_TOKEN", raising=False)
        session_file = tmp_path / "webshare_info.txt"
        session_file.write_text(
            "\n".join(f"p.webshare.io:80:user-{i}:secretpw" for i in range(1, 21))
        )
        monkeypatch.setenv("WEBSHARE_SESSION_FILE", str(session_file))
        monkeypatch.setenv("FILMOT_PROXY_MAX_SESSIONS", "5")
        monkeypatch.chdir(tmp_path)  # keep the pool's state file inside tmp
        reset_pool()
        pool = get_pool()
        assert pool is not None
        assert pool._file_backed is True
        assert len(pool._sessions) == 5  # sampled down from 20
        assert pool.token is None
        # rotation: two consecutive picks differ
        a = pool.pick()
        b = pool.pick()
        assert a is not None and b is not None and a.id != b.id
        # refresh performs a local reload and must never hit the API
        assert pool.refresh() == 5
        reset_pool()

    def test_file_backed_reload_replaces_rows_and_preserves_health(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.delenv("WEBSHARE_API_TOKEN", raising=False)
        session_file = tmp_path / "sessions.txt"
        session_file.write_text(
            "old.gateway.test:9000:user-1:old-pass\n"
            "old.gateway.test:9000:user-2:pass-2\n"
        )
        monkeypatch.setenv("WEBSHARE_SESSION_FILE", str(session_file))
        monkeypatch.setenv("FILMOT_PROXY_MAX_SESSIONS", "10")
        monkeypatch.chdir(tmp_path)
        reset_pool()
        pool = get_pool()
        first = next(s for s in pool._sessions if s.id == "user-1")
        pool.report_success(first)

        session_file.write_text(
            "new.gateway.test:9100:user-1:new-pass\n"
            "new.gateway.test:9100:user-3:pass-3\n"
        )
        assert pool.reload_file() == 2

        assert {s.id for s in pool._sessions} == {"user-1", "user-3"}
        kept = next(s for s in pool._sessions if s.id == "user-1")
        assert kept.success == 1
        assert kept.password == "new-pass"
        assert kept.proxy_host == "new.gateway.test"
        assert kept.proxy_port == 9100
        assert pool.proxy_url(kept) == (
            "http://user-1:new-pass@new.gateway.test:9100"
        )
        snap = pool.status_snapshot()
        assert snap["source"] == "session-file"
        assert snap["session_file"] == str(session_file)
        reset_pool()

    def test_file_backed_reload_failure_preserves_existing_pool(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.delenv("WEBSHARE_API_TOKEN", raising=False)
        session_file = tmp_path / "sessions.txt"
        session_file.write_text("proxy.test:80:user-1:pass-1\n")
        monkeypatch.setenv("WEBSHARE_SESSION_FILE", str(session_file))
        monkeypatch.chdir(tmp_path)
        reset_pool()
        pool = get_pool()
        session_file.write_text("malformed\n")

        with pytest.raises(WebshareProxyError, match="no valid"):
            pool.refresh(force=True)
        assert [s.id for s in pool._sessions] == ["user-1"]
        reset_pool()

    def test_file_backed_reload_keeps_configured_capacity(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.delenv("WEBSHARE_API_TOKEN", raising=False)
        session_file = tmp_path / "sessions.txt"
        session_file.write_text("proxy.test:80:user-1:pass-1\n")
        monkeypatch.setenv("WEBSHARE_SESSION_FILE", str(session_file))
        monkeypatch.setenv("FILMOT_PROXY_MAX_SESSIONS", "3")
        monkeypatch.chdir(tmp_path)
        reset_pool()
        pool = get_pool()
        assert pool.max_sessions == 3

        session_file.write_text(
            "\n".join(
                f"proxy.test:80:user-{index}:pass-{index}"
                for index in range(1, 6)
            )
        )
        assert pool.reload_file() == 3
        reset_pool()

    def test_file_backed_full_refresh_is_rejected(self, monkeypatch, tmp_path):
        monkeypatch.delenv("WEBSHARE_API_TOKEN", raising=False)
        session_file = tmp_path / "sessions.txt"
        session_file.write_text("proxy.test:80:user-1:pass-1\n")
        monkeypatch.setenv("WEBSHARE_SESSION_FILE", str(session_file))
        monkeypatch.chdir(tmp_path)
        reset_pool()
        pool = get_pool()

        with pytest.raises(WebshareProxyError, match="file-backed"):
            pool.request_full_refresh()
        reset_pool()


# ── persistence round-trip ────────────────────────────────────────

class TestPersistence:
    def test_public_pool_rejects_non_sqlite_health_state(self, tmp_path):
        state_path = tmp_path / "credential-bearing-health.json"

        with pytest.raises(WebshareProxyError, match="SQLite"):
            WebshareProxyPool("test-token", state_path=state_path)

        assert not state_path.exists()

    def test_file_pool_rejects_non_sqlite_health_state(self, tmp_path):
        state_path = tmp_path / "credential-bearing-file-health.json"
        pool = WebshareProxyPool.__new__(WebshareProxyPool)

        with pytest.raises(WebshareProxyError, match="SQLite"):
            pool._init_file_backed(
                [
                    WebshareSession(
                        id="session-id",
                        username="secret-user",
                        password="secret-password",
                    )
                ],
                session_file_path=tmp_path / "sessions.txt",
                state_path=state_path,
            )

        assert not state_path.exists()

    def test_load_after_save(self, tmp_path):
        pool = _make_pool(tmp_path, sessions=[
            WebshareSession(id="b-US-1", username="u", password="p", country_code="US")
        ])
        pool._sessions[0].success = 5
        pool._save_state()

        # Reload into a new pool
        pool2 = _make_pool(tmp_path, sessions=[])
        pool2.state_path = pool.state_path
        pool2._load_state()
        assert len(pool2._sessions) == 1
        assert pool2._sessions[0].id == "b-US-1"
        assert pool2._sessions[0].success == 5
