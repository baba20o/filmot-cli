"""
Webshare proxy pool — dynamic, health-tracked rotation for transcript fetches.

The Webshare residential plan exposes hundreds-of-thousands of "sessions"
(unique username/password tuples) that route through a fixed gateway
(``p.webshare.io:80``) and pin to one residential exit IP. This module:

1. Pulls a working subset of those sessions from the Webshare REST API
   (``GET /api/v2/proxy/list/?mode=backbone``) using ``WEBSHARE_API_TOKEN``.
2. Caches them locally with health stats (success / 429 / blocked / cooldown).
3. Hands them out one at a time via :meth:`WebshareProxyPool.pick`, skipping
   sessions on cooldown or that have repeatedly failed.
4. Lets the caller report outcomes back so unhealthy IPs get sidelined.
5. Optionally calls Webshare's full-refresh endpoint when the pool degrades.

The aim is to make transcript retrieval keep working from hosts whose own IP
gets blocked (e.g. AWS), without forcing an operator to manually rotate
credentials in ``.env`` when individual residential IPs go stale.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Optional

import requests
from dotenv import load_dotenv

load_dotenv()


WEBSHARE_API_BASE = "https://proxy.webshare.io"
DEFAULT_GATEWAY_HOST = "p.webshare.io"
DEFAULT_GATEWAY_PORT = 80
DEFAULT_REFRESH_HOURS = 6
DEFAULT_MAX_SESSIONS = 50
DEFAULT_STATE_PATH = Path(".filmot_data") / "webshare_pool.json"
# A pre-exported list of backbone sessions ("host:port:username:password" per
# line). Lets the pool rotate across many residential exit IPs without a
# WEBSHARE_API_TOKEN — each numbered session pins to a distinct exit IP.
DEFAULT_SESSION_FILE = Path(".filmot_data") / "webshare_info.txt"

# Cooldown windows per failure class (seconds)
COOLDOWN_RATE_LIMITED = 90
COOLDOWN_BLOCKED = 30 * 60
COOLDOWN_CONNECTION = 60
COOLDOWN_OTHER = 5 * 60

# When a session accumulates this many *consecutive* failures it gets retired.
RETIRE_AFTER_CONSECUTIVE_FAILURES = 5


class WebshareProxyError(Exception):
    """Raised for failures talking to the Webshare REST API."""


@dataclass
class WebshareSession:
    """One residential session (username/password pinning to one exit IP)."""

    id: str
    username: str
    password: str
    country_code: Optional[str] = None
    last_verification: Optional[str] = None
    valid: bool = True

    # health
    success: int = 0
    fail_429: int = 0
    fail_blocked: int = 0
    fail_other: int = 0
    consecutive_failures: int = 0
    cooldown_until: float = 0.0  # epoch seconds
    last_used_at: float = 0.0
    last_error: Optional[str] = None
    retired: bool = False

    def is_available(self, now: float) -> bool:
        return self.valid and not self.retired and self.cooldown_until <= now

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WebshareSession":
        # Tolerate missing health fields when loading older snapshots.
        defaults = cls(id="", username="", password="").to_dict()
        defaults.update({k: v for k, v in data.items() if k in defaults})
        return cls(**defaults)

    @classmethod
    def from_api(cls, payload: dict) -> "WebshareSession":
        return cls(
            id=payload["id"],
            username=payload["username"],
            password=payload["password"],
            country_code=payload.get("country_code"),
            last_verification=payload.get("last_verification"),
            valid=bool(payload.get("valid", True)),
        )


class WebshareProxyPool:
    """Round-robin pool of Webshare sessions with per-session health tracking.

    Thread-safe (uses an internal lock around state mutation).
    """

    def __init__(
        self,
        token: str,
        *,
        countries: Optional[Iterable[str]] = None,
        gateway_host: str = DEFAULT_GATEWAY_HOST,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        refresh_hours: float = DEFAULT_REFRESH_HOURS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        state_path: Optional[Path] = None,
        request_timeout: float = 15.0,
    ) -> None:
        if not token:
            raise WebshareProxyError("WEBSHARE_API_TOKEN is required")
        self.token = token
        self.countries = [c.strip().upper() for c in (countries or []) if c.strip()]
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.refresh_hours = refresh_hours
        self.max_sessions = max_sessions
        self.state_path = Path(state_path) if state_path else DEFAULT_STATE_PATH
        self.request_timeout = request_timeout

        self._lock = threading.Lock()
        self._sessions: list[WebshareSession] = []
        self._last_refresh: float = 0.0
        self._cursor: int = 0  # round-robin pointer
        self._file_backed: bool = False

        self._load_state()

    def _init_file_backed(self, sessions: list, state_path: Optional[Path] = None) -> None:
        """Initialize a token-less pool from a pre-exported session list.

        Never calls the Webshare API; rotates across the provided sessions and
        persists health to its own state file (merging prior health by id).
        """
        self.token = None
        self.countries = []
        self.gateway_host = DEFAULT_GATEWAY_HOST
        self.gateway_port = DEFAULT_GATEWAY_PORT
        self.refresh_hours = DEFAULT_REFRESH_HOURS
        self.max_sessions = len(sessions)
        self.state_path = Path(state_path) if state_path else (Path(".filmot_data") / "webshare_pool_file.json")
        self.request_timeout = 15.0
        self._lock = threading.Lock()
        self._sessions = sessions
        self._last_refresh = time.time()
        self._cursor = 0
        self._file_backed = True
        # Merge prior health stats (cooldowns, retirement) for known sessions.
        try:
            if self.state_path.exists():
                data = json.loads(self.state_path.read_text())
                prior = {s["id"]: s for s in data.get("sessions", [])}
                for sess in self._sessions:
                    if sess.id in prior:
                        merged = WebshareSession.from_dict(prior[sess.id])
                        sess.success = merged.success
                        sess.fail_429 = merged.fail_429
                        sess.fail_blocked = merged.fail_blocked
                        sess.fail_other = merged.fail_other
                        sess.consecutive_failures = merged.consecutive_failures
                        sess.cooldown_until = merged.cooldown_until
                        sess.retired = merged.retired
                self._cursor = int(data.get("cursor", 0)) % max(len(self._sessions), 1)
        except (OSError, json.JSONDecodeError, KeyError):
            pass

    # ── persistence ────────────────────────────────────────────────────

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        self._last_refresh = float(data.get("last_refresh", 0.0))
        self._sessions = [
            WebshareSession.from_dict(s) for s in data.get("sessions", [])
        ]
        # Persisting the cursor matters for short-lived CLI processes: without
        # it, every invocation starts at index 0 and hammers the same session.
        self._cursor = int(data.get("cursor", 0))

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_refresh": self._last_refresh,
            "gateway_host": self.gateway_host,
            "gateway_port": self.gateway_port,
            "countries": self.countries,
            "cursor": self._cursor,
            "sessions": [s.to_dict() for s in self._sessions],
        }
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.state_path)

    # ── REST helpers ───────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {"Authorization": f"Token {self.token}"}

    def _api_get(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{WEBSHARE_API_BASE}{path}"
        try:
            r = requests.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=self.request_timeout,
            )
        except requests.RequestException as exc:
            raise WebshareProxyError(f"Webshare GET {path} failed: {exc}") from exc
        if r.status_code == 401:
            raise WebshareProxyError(
                "Webshare rejected the API token (401). "
                "Check WEBSHARE_API_TOKEN."
            )
        if r.status_code >= 400:
            raise WebshareProxyError(
                f"Webshare GET {path} returned {r.status_code}: {r.text[:200]}"
            )
        try:
            return r.json()
        except ValueError as exc:
            raise WebshareProxyError(f"Webshare GET {path} non-JSON body") from exc

    def _api_post(self, path: str, params: Optional[dict] = None) -> requests.Response:
        url = f"{WEBSHARE_API_BASE}{path}"
        try:
            r = requests.post(
                url,
                headers=self._headers(),
                params=params,
                timeout=self.request_timeout,
            )
        except requests.RequestException as exc:
            raise WebshareProxyError(f"Webshare POST {path} failed: {exc}") from exc
        if r.status_code == 401:
            raise WebshareProxyError("Webshare rejected the API token (401)")
        if r.status_code >= 400:
            raise WebshareProxyError(
                f"Webshare POST {path} returned {r.status_code}: {r.text[:200]}"
            )
        return r

    # ── public API ─────────────────────────────────────────────────────

    def refresh(self, *, force: bool = False) -> int:
        """Pull sessions from Webshare. Returns the number now in the pool."""
        if getattr(self, "_file_backed", False):
            # File-backed pools have a fixed session list; nothing to pull.
            return len(self._sessions)
        with self._lock:
            now = time.time()
            stale = (now - self._last_refresh) > self.refresh_hours * 3600
            if not force and not stale and self._sessions:
                return len(self._sessions)

            # Note: backbone mode does not support `ordering`, `valid`,
            # `proxy_address`, or text search filters. Only ``country_code__in``
            # and pagination are honored.
            params = {
                "mode": "backbone",
                "page_size": str(self.max_sessions),
            }
            if self.countries:
                params["country_code__in"] = ",".join(self.countries)

            data = self._api_get("/api/v2/proxy/list/", params=params)
            results = data.get("results", []) or []

            # Preserve health for sessions we already know about.
            existing_by_id = {s.id: s for s in self._sessions}
            new_sessions: list[WebshareSession] = []
            for row in results:
                fresh = WebshareSession.from_api(row)
                prior = existing_by_id.get(fresh.id)
                if prior is not None:
                    # keep counters/cooldown; refresh creds + verification
                    prior.username = fresh.username
                    prior.password = fresh.password
                    prior.country_code = fresh.country_code
                    prior.last_verification = fresh.last_verification
                    prior.valid = fresh.valid
                    new_sessions.append(prior)
                else:
                    new_sessions.append(fresh)

            self._sessions = new_sessions
            self._last_refresh = now
            self._cursor = 0
            self._save_state()
            return len(self._sessions)

    def request_full_refresh(self) -> None:
        """Ask Webshare to rotate the entire underlying proxy list (POST)."""
        self._api_post("/api/v2/proxy/list/refresh/")

    def proxy_url(self, session: WebshareSession) -> str:
        return (
            f"http://{session.username}:{session.password}"
            f"@{self.gateway_host}:{self.gateway_port}"
        )

    def pick(self) -> Optional[WebshareSession]:
        """Return the next available session, or None if pool is empty/exhausted.

        Refreshes from the API on first use or when stale.
        """
        if not self._sessions or (
            time.time() - self._last_refresh > self.refresh_hours * 3600
        ):
            try:
                self.refresh()
            except WebshareProxyError:
                # Use whatever is cached; caller will see error eventually.
                pass

        with self._lock:
            if not self._sessions:
                return None
            now = time.time()
            n = len(self._sessions)
            for offset in range(n):
                idx = (self._cursor + offset) % n
                s = self._sessions[idx]
                if s.is_available(now):
                    self._cursor = (idx + 1) % n
                    s.last_used_at = now
                    return s
            return None  # all in cooldown / retired

    def report_success(self, session: WebshareSession) -> None:
        with self._lock:
            session.success += 1
            session.consecutive_failures = 0
            session.cooldown_until = 0.0
            session.last_error = None
            self._save_state()

    def report_failure(
        self, session: WebshareSession, kind: str, *, summary: str = ""
    ) -> None:
        """Record a failure and apply per-class cooldown.

        ``kind`` is one of: ``rate_limited``, ``blocked``, ``connection``, ``other``.
        """
        cooldowns = {
            "rate_limited": COOLDOWN_RATE_LIMITED,
            "blocked": COOLDOWN_BLOCKED,
            "connection": COOLDOWN_CONNECTION,
            "other": COOLDOWN_OTHER,
        }
        with self._lock:
            now = time.time()
            if kind == "rate_limited":
                session.fail_429 += 1
            elif kind == "blocked":
                session.fail_blocked += 1
            else:
                session.fail_other += 1
            session.consecutive_failures += 1
            session.cooldown_until = now + cooldowns.get(kind, COOLDOWN_OTHER)
            if summary:
                session.last_error = summary[:200]
            if session.consecutive_failures >= RETIRE_AFTER_CONSECUTIVE_FAILURES:
                session.retired = True
            self._save_state()

    def healthy_count(self) -> int:
        now = time.time()
        return sum(1 for s in self._sessions if s.is_available(now))

    def status_snapshot(self) -> dict:
        now = time.time()
        sessions = []
        for s in self._sessions:
            sessions.append(
                {
                    "id": s.id,
                    "username": s.username,
                    "country": s.country_code,
                    "valid": s.valid,
                    "retired": s.retired,
                    "available": s.is_available(now),
                    "cooldown_remaining_s": max(0, int(s.cooldown_until - now)),
                    "success": s.success,
                    "fail_429": s.fail_429,
                    "fail_blocked": s.fail_blocked,
                    "fail_other": s.fail_other,
                    "consecutive_failures": s.consecutive_failures,
                    "last_error": s.last_error,
                }
            )
        return {
            "gateway": f"{self.gateway_host}:{self.gateway_port}",
            "countries": self.countries,
            "total": len(self._sessions),
            "healthy": self.healthy_count(),
            "last_refresh": self._last_refresh,
            "stale": (now - self._last_refresh) > self.refresh_hours * 3600,
            "sessions": sessions,
        }


# ── module-level singleton helper ─────────────────────────────────────

_pool: Optional[WebshareProxyPool] = None
_pool_lock = threading.Lock()


def _parse_countries(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [c.strip().upper() for c in raw.split(",") if c.strip()]


def _load_sessions_from_file(path: Path, limit: int) -> list[WebshareSession]:
    """Parse a 'host:port:username:password' session list into WebshareSessions.

    Sampled evenly across the file (not just the first N) so we spread across
    distinct exit IPs rather than clustering on the lowest-numbered sessions.
    """
    try:
        lines = [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]
    except OSError:
        return []
    parsed = []
    for ln in lines:
        parts = ln.split(":")
        if len(parts) >= 4:
            # host:port:username:password (password may itself contain ':')
            host, port, username = parts[0], parts[1], parts[2]
            password = ":".join(parts[3:])
            parsed.append((username, password))
    if not parsed:
        return []
    if len(parsed) > limit:
        step = len(parsed) / limit
        parsed = [parsed[int(i * step)] for i in range(limit)]
    return [WebshareSession(id=u, username=u, password=p) for u, p in parsed]


def get_pool(*, force_new: bool = False) -> Optional[WebshareProxyPool]:
    """Return the process-wide pool, or None if no proxy source is configured.

    Two sources, in order of preference:
      1. WEBSHARE_API_TOKEN  → live API, health-tracked, can self-refresh.
      2. A pre-exported session file (WEBSHARE_SESSION_FILE or webshare_info.txt)
         → rotate across many backbone sessions with no API token. This revives
         multi-IP rotation when only proxy credentials (not an API key) exist.
    """
    global _pool
    with _pool_lock:
        if _pool is not None and not force_new:
            return _pool
        max_sessions = int(os.getenv("FILMOT_PROXY_MAX_SESSIONS", DEFAULT_MAX_SESSIONS))
        token = os.getenv("WEBSHARE_API_TOKEN")
        if token:
            try:
                _pool = WebshareProxyPool(
                    token,
                    countries=_parse_countries(os.getenv("FILMOT_PROXY_COUNTRIES")),
                    refresh_hours=float(
                        os.getenv("FILMOT_PROXY_REFRESH_HOURS", DEFAULT_REFRESH_HOURS)
                    ),
                    max_sessions=max_sessions,
                )
            except WebshareProxyError:
                _pool = None
            if _pool is not None:
                return _pool

        # Fall back to a file-backed pool (no API token needed).
        session_file = Path(os.getenv("WEBSHARE_SESSION_FILE", str(DEFAULT_SESSION_FILE)))
        if session_file.exists():
            sessions = _load_sessions_from_file(session_file, max_sessions)
            if sessions:
                _pool = WebshareProxyPool.__new__(WebshareProxyPool)
                _pool._init_file_backed(sessions)
                return _pool

        _pool = None
        return _pool


def reset_pool() -> None:
    """Drop the cached pool (mostly used by tests)."""
    global _pool
    with _pool_lock:
        _pool = None


def classify_transport_error(error: BaseException) -> str:
    """Map a fetch exception to one of the cooldown classes used by the pool.

    Returns one of: ``rate_limited``, ``blocked``, ``connection``, ``other``,
    or the empty string when the error doesn't look transport-related.
    """
    msg = str(error).lower()
    if "429" in msg or "too many requests" in msg or "rate limit" in msg:
        return "rate_limited"
    if "ipblocked" in msg or "ip block" in msg or "blocked by youtube" in msg:
        return "blocked"
    transport_needles = (
        "proxy",
        "tunnel connection failed",
        "max retries exceeded",
        "connection reset",
        "connection aborted",
        "connect timeout",
        "read timeout",
        "timed out",
        "ssl",
        "certificate",
        "temporary failure",
    )
    if any(n in msg for n in transport_needles):
        return "connection"
    return ""
