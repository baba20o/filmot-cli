"""
Webshare proxy pool — dynamic, health-tracked rotation for transcript fetches.

The Webshare residential plan exposes hundreds-of-thousands of "sessions"
(unique username/password tuples) that route through a fixed gateway
(``p.webshare.io:80``) and pin to one residential exit IP. This module:

1. Pulls a working subset of those sessions from the Webshare REST API
   (``GET /api/v2/proxy/list/?mode=backbone``) using ``WEBSHARE_API_TOKEN``.
2. Stores credentials in private per-user configuration and keeps health,
   rotation, and short-lived leases in a credential-free per-user SQLite DB.
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
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from hashlib import sha256
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional

import requests

# Importing config performs the repository's single explicit environment-load
# sequence (process environment, per-user config, then project .env).
from . import config as _config  # noqa: F401
from .paths import (
    default_proxy_session_file,
    ensure_private_dir,
    legacy_proxy_session_file,
    legacy_proxy_state_file,
    project_data_dir,
    proxy_health_db,
    proxy_inventory_file,
    restrict_private_file,
    secure_copy,
)


WEBSHARE_API_BASE = "https://proxy.webshare.io"
DEFAULT_GATEWAY_HOST = "p.webshare.io"
DEFAULT_GATEWAY_PORT = 80
DEFAULT_REFRESH_HOURS = 6
DEFAULT_MAX_SESSIONS = 50
DEFAULT_RECENT_SUCCESS_HOURS = 24
DEFAULT_LEASE_SECONDS = 120.0
SQLITE_STATE_SUFFIXES = frozenset({".sqlite", ".sqlite3", ".db"})

# Cooldown windows per failure class (seconds)
COOLDOWN_RATE_LIMITED = 90
COOLDOWN_BLOCKED = 30 * 60
COOLDOWN_CONNECTION = 60
COOLDOWN_OTHER = 5 * 60

# When a session accumulates this many *consecutive* failures it gets retired.
RETIRE_AFTER_CONSECUTIVE_FAILURES = 5


class WebshareProxyError(Exception):
    """Raised for failures talking to the Webshare REST API."""


def _require_sqlite_state_path(path: Path) -> Path:
    """Reject health stores that could mix credentials into legacy JSON."""
    if path.suffix.lower() not in SQLITE_STATE_SUFFIXES:
        raise WebshareProxyError(
            "Proxy state_path must be a SQLite database ending in "
            ".sqlite, .sqlite3, or .db"
        )
    return path


def redact_sensitive_text(value: object, secrets: Iterable[str] = ()) -> str:
    """Remove proxy URL userinfo and caller-known secrets from diagnostics."""
    text = str(value)
    text = re.sub(
        r"(?i)(https?://)[^/\s@]+@",
        r"\1***:***@",
        text,
    )
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "***")
    return text


def _legacy_timestamp(value: object) -> float:
    """Return a safe legacy timestamp, treating corrupt metadata as unset."""
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if math.isfinite(parsed) and parsed >= 0 else 0.0


def _legacy_cursor(value: object, session_count: int) -> int:
    """Return a bounded legacy cursor, treating corrupt metadata as unset."""
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = 0
    return parsed % max(session_count, 1)


@dataclass
class WebshareSession:
    """One residential session (username/password pinning to one exit IP)."""

    id: str
    username: str
    password: str
    country_code: Optional[str] = None
    last_verification: Optional[str] = None
    valid: bool = True
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None

    # health
    success: int = 0
    fail_429: int = 0
    fail_blocked: int = 0
    fail_other: int = 0
    consecutive_failures: int = 0
    cooldown_until: float = 0.0  # epoch seconds
    last_used_at: float = 0.0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    last_failure_kind: Optional[str] = None
    last_error: Optional[str] = None
    retired: bool = False

    def is_available(self, now: float) -> bool:
        return self.valid and not self.retired and self.cooldown_until <= now

    def was_recently_successful(self, now: float, window_seconds: float) -> bool:
        return (
            self.last_success_at > 0
            and now - self.last_success_at <= window_seconds
        )

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


_HEALTH_COLUMNS = (
    "success",
    "fail_429",
    "fail_blocked",
    "fail_other",
    "consecutive_failures",
    "cooldown_until",
    "last_used_at",
    "last_success_at",
    "last_failure_at",
    "last_failure_kind",
    "last_error",
    "retired",
)


def _session_health_key(session_id: str) -> str:
    """Return a non-reversible key so the health DB contains no credentials."""
    return sha256(str(session_id).encode("utf-8")).hexdigest()


class _ProxyHealthStore:
    """SQLite-backed cross-process proxy health, cursor, and lease storage."""

    def __init__(self, path: Path, source_key: str, lease_seconds: float) -> None:
        self.path = Path(path)
        self.source_key = source_key
        self.lease_seconds = max(float(lease_seconds), 1.0)
        self.owner = "{}-{}".format(os.getpid(), uuid.uuid4().hex)
        ensure_private_dir(self.path.parent)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pool_state (
                    source_key TEXT PRIMARY KEY,
                    last_refresh REAL NOT NULL DEFAULT 0,
                    cursor INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS session_health (
                    source_key TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    success INTEGER NOT NULL DEFAULT 0,
                    fail_429 INTEGER NOT NULL DEFAULT 0,
                    fail_blocked INTEGER NOT NULL DEFAULT 0,
                    fail_other INTEGER NOT NULL DEFAULT 0,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    cooldown_until REAL NOT NULL DEFAULT 0,
                    last_used_at REAL NOT NULL DEFAULT 0,
                    last_success_at REAL NOT NULL DEFAULT 0,
                    last_failure_at REAL NOT NULL DEFAULT 0,
                    last_failure_kind TEXT,
                    last_error TEXT,
                    retired INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_until REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (source_key, session_key)
                )
                """
            )
            connection.commit()
        finally:
            connection.close()
        restrict_private_file(self.path)

    def has_source(self) -> bool:
        connection = self._connect()
        try:
            return connection.execute(
                "SELECT 1 FROM pool_state WHERE source_key = ?",
                (self.source_key,),
            ).fetchone() is not None
        finally:
            connection.close()

    def _ensure_rows(
        self,
        connection: sqlite3.Connection,
        sessions: Iterable[WebshareSession],
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO pool_state (source_key, last_refresh, cursor)
            VALUES (?, 0, 0)
            """,
            (self.source_key,),
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO session_health (source_key, session_key)
            VALUES (?, ?)
            """,
            [
                (self.source_key, _session_health_key(session.id))
                for session in sessions
            ],
        )

    @staticmethod
    def _apply_row(session: WebshareSession, row: sqlite3.Row) -> None:
        for column in _HEALTH_COLUMNS:
            value = row[column]
            if column == "retired":
                value = bool(value)
            setattr(session, column, value)

    def sync(
        self,
        sessions: list[WebshareSession],
    ) -> tuple[float, int, set[str]]:
        """Refresh in-memory health and return meta plus active lease keys."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_rows(connection, sessions)
            meta = connection.execute(
                """
                SELECT last_refresh, cursor FROM pool_state
                WHERE source_key = ?
                """,
                (self.source_key,),
            ).fetchone()
            rows = {
                row["session_key"]: row
                for row in connection.execute(
                    """
                    SELECT * FROM session_health WHERE source_key = ?
                    """,
                    (self.source_key,),
                )
            }
            connection.commit()
        finally:
            connection.close()

        now = time.time()
        leased = set()
        for session in sessions:
            key = _session_health_key(session.id)
            row = rows.get(key)
            if row is None:
                continue
            self._apply_row(session, row)
            if float(row["lease_until"] or 0) > now:
                leased.add(key)
        return (
            float(meta["last_refresh"] if meta is not None else 0),
            int(meta["cursor"] if meta is not None else 0),
            leased,
        )

    def set_meta(self, *, last_refresh: float, cursor: int) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO pool_state (source_key, last_refresh, cursor)
                VALUES (?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    last_refresh = excluded.last_refresh,
                    cursor = excluded.cursor
                """,
                (self.source_key, float(last_refresh), int(cursor)),
            )
            connection.commit()
        finally:
            connection.close()

    def import_legacy(
        self,
        sessions: list[WebshareSession],
        *,
        last_refresh: float,
        cursor: int,
    ) -> None:
        """Import one legacy JSON snapshot only when this source is absent."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM pool_state WHERE source_key = ?",
                (self.source_key,),
            ).fetchone()
            if exists is not None:
                connection.rollback()
                return
            connection.execute(
                """
                INSERT INTO pool_state (source_key, last_refresh, cursor)
                VALUES (?, ?, ?)
                """,
                (self.source_key, float(last_refresh), int(cursor)),
            )
            for session in sessions:
                values = [getattr(session, column) for column in _HEALTH_COLUMNS]
                values[-1] = int(bool(values[-1]))
                connection.execute(
                    """
                    INSERT INTO session_health (
                        source_key, session_key,
                        success, fail_429, fail_blocked, fail_other,
                        consecutive_failures, cooldown_until, last_used_at,
                        last_success_at, last_failure_at, last_failure_kind,
                        last_error, retired
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.source_key,
                        _session_health_key(session.id),
                        *values,
                    ),
                )
            connection.commit()
        finally:
            connection.close()

    def pick(
        self,
        sessions: list[WebshareSession],
        *,
        exclude_ids: set[str],
        lease: bool,
        lease_seconds: Optional[float] = None,
    ) -> tuple[Optional[WebshareSession], int]:
        connection = self._connect()
        selected = None
        next_cursor = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_rows(connection, sessions)
            meta = connection.execute(
                "SELECT cursor FROM pool_state WHERE source_key = ?",
                (self.source_key,),
            ).fetchone()
            cursor = int(meta["cursor"] if meta is not None else 0)
            now = time.time()
            n = len(sessions)
            for offset in range(n):
                index = (cursor + offset) % n
                session = sessions[index]
                row = connection.execute(
                    """
                    SELECT * FROM session_health
                    WHERE source_key = ? AND session_key = ?
                    """,
                    (self.source_key, _session_health_key(session.id)),
                ).fetchone()
                if row is None:
                    continue
                self._apply_row(session, row)
                actively_leased = float(row["lease_until"] or 0) > now
                if (
                    session.id in exclude_ids
                    or actively_leased
                    or not session.is_available(now)
                ):
                    continue
                next_cursor = (index + 1) % n
                lease_owner = self.owner if lease else None
                effective_lease_seconds = max(
                    float(
                        self.lease_seconds
                        if lease_seconds is None
                        else lease_seconds
                    ),
                    1.0,
                )
                lease_until = (
                    now + effective_lease_seconds
                    if lease
                    else 0.0
                )
                connection.execute(
                    """
                    UPDATE session_health
                    SET last_used_at = ?, lease_owner = ?, lease_until = ?
                    WHERE source_key = ? AND session_key = ?
                    """,
                    (
                        now,
                        lease_owner,
                        lease_until,
                        self.source_key,
                        _session_health_key(session.id),
                    ),
                )
                connection.execute(
                    """
                    UPDATE pool_state SET cursor = ? WHERE source_key = ?
                    """,
                    (next_cursor, self.source_key),
                )
                session.last_used_at = now
                selected = session
                break
            connection.commit()
        finally:
            connection.close()
        return selected, next_cursor

    def release(self, session: WebshareSession) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                UPDATE session_health
                SET lease_owner = NULL, lease_until = 0
                WHERE source_key = ? AND session_key = ? AND lease_owner = ?
                """,
                (
                    self.source_key,
                    _session_health_key(session.id),
                    self.owner,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def report_success(self, session: WebshareSession) -> None:
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_rows(connection, [session])
            connection.execute(
                """
                UPDATE session_health SET
                    success = success + 1,
                    consecutive_failures = 0,
                    cooldown_until = 0,
                    last_success_at = ?,
                    last_error = NULL,
                    lease_owner = CASE
                        WHEN lease_owner = ? THEN NULL ELSE lease_owner END,
                    lease_until = CASE
                        WHEN lease_owner = ? THEN 0 ELSE lease_until END
                WHERE source_key = ? AND session_key = ?
                """,
                (
                    now,
                    self.owner,
                    self.owner,
                    self.source_key,
                    _session_health_key(session.id),
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM session_health
                WHERE source_key = ? AND session_key = ?
                """,
                (self.source_key, _session_health_key(session.id)),
            ).fetchone()
            connection.commit()
        finally:
            connection.close()
        if row is not None:
            self._apply_row(session, row)

    def report_failure(
        self,
        session: WebshareSession,
        *,
        kind: str,
        cooldown_seconds: float,
        summary: str,
    ) -> None:
        now = time.time()
        counter = (
            "fail_429"
            if kind == "rate_limited"
            else "fail_blocked"
            if kind == "blocked"
            else "fail_other"
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_rows(connection, [session])
            connection.execute(
                """
                UPDATE session_health SET
                    {counter} = {counter} + 1,
                    consecutive_failures = consecutive_failures + 1,
                    cooldown_until = ?,
                    last_failure_at = ?,
                    last_failure_kind = ?,
                    last_error = ?,
                    retired = CASE
                        WHEN consecutive_failures + 1 >= ? THEN 1
                        ELSE retired END,
                    lease_owner = CASE
                        WHEN lease_owner = ? THEN NULL ELSE lease_owner END,
                    lease_until = CASE
                        WHEN lease_owner = ? THEN 0 ELSE lease_until END
                WHERE source_key = ? AND session_key = ?
                """.format(counter=counter),
                (
                    now + cooldown_seconds,
                    now,
                    kind,
                    summary or None,
                    RETIRE_AFTER_CONSECUTIVE_FAILURES,
                    self.owner,
                    self.owner,
                    self.source_key,
                    _session_health_key(session.id),
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM session_health
                WHERE source_key = ? AND session_key = ?
                """,
                (self.source_key, _session_health_key(session.id)),
            ).fetchone()
            connection.commit()
        finally:
            connection.close()
        if row is not None:
            self._apply_row(session, row)


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
        inventory_path: Optional[Path] = None,
        request_timeout: float = 15.0,
        recent_success_hours: float = DEFAULT_RECENT_SUCCESS_HOURS,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> None:
        if not token:
            raise WebshareProxyError("WEBSHARE_API_TOKEN is required")
        self.token = token
        self.countries = [c.strip().upper() for c in (countries or []) if c.strip()]
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.refresh_hours = refresh_hours
        self.max_sessions = max_sessions
        token_fingerprint = sha256(token.encode("utf-8")).hexdigest()[:16]
        resolved_state_path = (
            Path(state_path).expanduser().resolve(strict=False)
            if state_path is not None
            else proxy_health_db()
        )
        self.state_path = _require_sqlite_state_path(resolved_state_path)
        default_inventory = proxy_inventory_file()
        self.inventory_path = (
            Path(inventory_path).expanduser().resolve(strict=False)
            if inventory_path is not None
            else default_inventory.with_name(
                "{}-{}{}".format(
                    default_inventory.stem,
                    token_fingerprint,
                    default_inventory.suffix,
                )
            )
        )
        self.request_timeout = request_timeout
        self.recent_success_hours = recent_success_hours
        self.lease_seconds = lease_seconds

        self._lock = threading.Lock()
        self._sessions: list[WebshareSession] = []
        self._last_refresh: float = 0.0
        self._cursor: int = 0  # round-robin pointer
        self._in_flight_ids: set[str] = set()
        self._file_backed: bool = False
        self._session_file_path: Optional[Path] = None
        self._source_key = "webshare-api:{}".format(token_fingerprint)
        self._health_store = (
            _ProxyHealthStore(
                self.state_path,
                self._source_key,
                self.lease_seconds,
            )
            if self.state_path.suffix in {".sqlite", ".sqlite3", ".db"}
            else None
        )
        self._legacy_state_path = legacy_proxy_state_file(file_backed=False)

        self._load_state()

    def _init_file_backed(
        self,
        sessions: list,
        *,
        session_file_path: Path,
        state_path: Optional[Path] = None,
        max_sessions: Optional[int] = None,
        recent_success_hours: float = DEFAULT_RECENT_SUCCESS_HOURS,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> None:
        """Initialize a token-less pool from a pre-exported session list.

        Never calls the Webshare API; rotates across the provided sessions and
        persists health to its own state file (merging prior health by id).
        """
        self.token = None
        self.countries = []
        self.gateway_host = DEFAULT_GATEWAY_HOST
        self.gateway_port = DEFAULT_GATEWAY_PORT
        self.refresh_hours = DEFAULT_REFRESH_HOURS
        self.max_sessions = max_sessions or len(sessions)
        resolved_state_path = (
            Path(state_path).expanduser().resolve(strict=False)
            if state_path is not None
            else proxy_health_db()
        )
        self.state_path = _require_sqlite_state_path(resolved_state_path)
        self.inventory_path = None
        self.request_timeout = 15.0
        self.recent_success_hours = recent_success_hours
        self.lease_seconds = lease_seconds
        self._lock = threading.Lock()
        self._sessions = sessions
        self._last_refresh = time.time()
        self._cursor = 0
        self._in_flight_ids = set()
        self._file_backed = True
        self._session_file_path = (
            Path(session_file_path).expanduser().resolve(strict=False)
        )
        self._source_key = "session-file:{}".format(
            sha256(str(self._session_file_path).encode("utf-8")).hexdigest()[:16]
        )
        self._health_store = (
            _ProxyHealthStore(
                self.state_path,
                self._source_key,
                self.lease_seconds,
            )
            if self.state_path.suffix in {".sqlite", ".sqlite3", ".db"}
            else None
        )
        self._legacy_state_path = legacy_proxy_state_file(file_backed=True)
        self._load_state()

    # ── persistence ────────────────────────────────────────────────────

    @staticmethod
    def _session_config(session: WebshareSession) -> dict:
        """Return credential/config fields only (no health information)."""
        return {
            "id": session.id,
            "username": session.username,
            "password": session.password,
            "country_code": session.country_code,
            "last_verification": session.last_verification,
            "valid": session.valid,
            "proxy_host": session.proxy_host,
            "proxy_port": session.proxy_port,
        }

    def _load_inventory(self) -> None:
        inventory_path = getattr(self, "inventory_path", None)
        if inventory_path is None or not inventory_path.is_file():
            return
        # An inventory may predate the private-storage layout or have been
        # created manually under a permissive umask. Tighten both boundaries
        # before reading credential-bearing content.
        ensure_private_dir(inventory_path.parent)
        restrict_private_file(inventory_path)
        try:
            payload = json.loads(inventory_path.read_text(encoding="utf-8"))
            rows = payload.get("sessions", [])
            if isinstance(rows, list):
                self._sessions = [
                    WebshareSession.from_dict(row)
                    for row in rows
                    if isinstance(row, dict)
                ]
                self._last_refresh = _legacy_timestamp(
                    payload.get("last_refresh", 0.0)
                )
        except (OSError, ValueError, TypeError):
            return

    def _save_inventory(self) -> None:
        inventory_path = getattr(self, "inventory_path", None)
        if inventory_path is None:
            return
        ensure_private_dir(inventory_path.parent)
        payload = {
            "version": 1,
            "last_refresh": self._last_refresh,
            "gateway_host": self.gateway_host,
            "gateway_port": self.gateway_port,
            "countries": self.countries,
            "sessions": [
                self._session_config(session) for session in self._sessions
            ],
        }
        temporary = inventory_path.with_name(
            ".{}.{}.{}.tmp".format(
                inventory_path.name,
                os.getpid(),
                uuid.uuid4().hex,
            )
        )
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            restrict_private_file(temporary)
            temporary.replace(inventory_path)
            restrict_private_file(inventory_path)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def _read_legacy_payload(self) -> Optional[dict]:
        path = getattr(self, "_legacy_state_path", None)
        if (
            path is None
            or path == self.state_path
            or not path.exists()
        ):
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _import_legacy_state(self, payload: Optional[dict]) -> None:
        health_store = getattr(self, "_health_store", None)
        if payload is None or health_store is None:
            return
        rows = payload.get("sessions", [])
        if not isinstance(rows, list):
            return
        legacy_sessions = [
            WebshareSession.from_dict(row)
            for row in rows
            if isinstance(row, dict) and row.get("id")
        ]
        for session in legacy_sessions:
            session.last_error = redact_sensitive_text(
                session.last_error or "",
                (session.id, session.username, session.password),
            ) or None
        health_store.import_legacy(
            legacy_sessions,
            last_refresh=_legacy_timestamp(payload.get("last_refresh", 0.0)),
            cursor=_legacy_cursor(
                payload.get("cursor", 0),
                len(legacy_sessions),
            ),
        )

    def _load_state(self) -> None:
        health_store = getattr(self, "_health_store", None)
        if health_store is not None:
            legacy_payload = self._read_legacy_payload()
            if not self._file_backed:
                self._load_inventory()
                if not self._sessions and legacy_payload is not None:
                    rows = legacy_payload.get("sessions", [])
                    if isinstance(rows, list):
                        self._sessions = [
                            WebshareSession.from_dict(row)
                            for row in rows
                            if isinstance(row, dict)
                        ]
                        if self._sessions:
                            self._last_refresh = _legacy_timestamp(
                                legacy_payload.get("last_refresh", 0.0)
                            )
                            self._save_inventory()
            self._import_legacy_state(legacy_payload)
            (
                stored_refresh,
                stored_cursor,
                leased_keys,
            ) = health_store.sync(self._sessions)
            self._last_refresh = max(self._last_refresh, stored_refresh)
            self._cursor = stored_cursor % max(len(self._sessions), 1)
            self._in_flight_ids = {
                session.id
                for session in self._sessions
                if _session_health_key(session.id) in leased_keys
            }
            return

        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._last_refresh = _legacy_timestamp(data.get("last_refresh", 0.0))
        if not self._file_backed:
            self._sessions = [
                WebshareSession.from_dict(s)
                for s in data.get("sessions", [])
                if isinstance(s, dict)
            ]
        else:
            prior = {
                row.get("id"): WebshareSession.from_dict(row)
                for row in data.get("sessions", [])
                if isinstance(row, dict) and row.get("id")
            }
            for session in self._sessions:
                old = prior.get(session.id)
                if old is not None:
                    for column in _HEALTH_COLUMNS:
                        setattr(session, column, getattr(old, column))
        for session in self._sessions:
            session.last_error = redact_sensitive_text(
                session.last_error or "",
                (session.username, session.password),
            ) or None
        self._cursor = _legacy_cursor(
            data.get("cursor", 0),
            len(self._sessions),
        )

    def _save_state(self) -> None:
        health_store = getattr(self, "_health_store", None)
        if health_store is not None:
            health_store.set_meta(
                last_refresh=self._last_refresh,
                cursor=self._cursor,
            )
            return

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
        restrict_private_file(tmp)
        tmp.replace(self.state_path)
        restrict_private_file(self.state_path)

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
            # A file-backed pool has no remote list to refresh, but the local
            # export may have changed since this process started. Re-read it so
            # ``proxy refresh`` performs real work instead of reporting a
            # misleading no-op.
            return self.reload_file()
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
            self._save_inventory()
            health_store = getattr(self, "_health_store", None)
            if health_store is not None:
                health_store.sync(self._sessions)
            self._save_state()
            return len(self._sessions)

    def request_full_refresh(self) -> None:
        """Ask Webshare to rotate the entire underlying proxy list (POST)."""
        if getattr(self, "_file_backed", False):
            raise WebshareProxyError(
                "A file-backed pool cannot request a remote IP refresh; "
                "replace the session file and reload it instead."
            )
        self._api_post("/api/v2/proxy/list/refresh/")

    def reload_file(self) -> int:
        """Reload a file-backed session list, preserving health by session id.

        Raises :class:`WebshareProxyError` for API-backed pools, missing files,
        and files with no valid session rows. The existing in-memory pool is
        left untouched on failure.
        """
        if not getattr(self, "_file_backed", False):
            raise WebshareProxyError("reload_file() is only valid for file-backed pools")
        path = getattr(self, "_session_file_path", None)
        if path is None:
            raise WebshareProxyError("File-backed pool has no source path")
        if not path.exists():
            raise WebshareProxyError(f"Session file no longer exists: {path}")

        fresh_sessions = _load_sessions_from_file(path, self.max_sessions)
        if not fresh_sessions:
            raise WebshareProxyError(
                f"Session file contains no valid host:port:user:password rows: {path}"
            )

        with self._lock:
            existing = {s.id: s for s in self._sessions}
            merged_sessions: list[WebshareSession] = []
            for fresh in fresh_sessions:
                prior = existing.get(fresh.id)
                if prior is None:
                    merged_sessions.append(fresh)
                    continue
                prior.username = fresh.username
                prior.password = fresh.password
                prior.proxy_host = fresh.proxy_host
                prior.proxy_port = fresh.proxy_port
                prior.valid = fresh.valid
                merged_sessions.append(prior)

            self._sessions = merged_sessions
            self._last_refresh = time.time()
            self._cursor %= max(len(self._sessions), 1)
            health_store = getattr(self, "_health_store", None)
            if health_store is not None:
                health_store.sync(self._sessions)
            self._save_state()
            return len(self._sessions)

    def proxy_url(self, session: WebshareSession) -> str:
        host = session.proxy_host or self.gateway_host
        port = session.proxy_port or self.gateway_port
        return (
            f"http://{session.username}:{session.password}"
            f"@{host}:{port}"
        )

    def redacted_session_id(self, session: WebshareSession) -> str:
        """Return a stable, non-credential identifier suitable for output."""
        digest = sha256(session.id.encode("utf-8")).hexdigest()[:8]
        return f"session-{digest}"

    @property
    def source(self) -> str:
        return "session-file" if getattr(self, "_file_backed", False) else "webshare-api"

    @property
    def session_file_path(self) -> Optional[Path]:
        return getattr(self, "_session_file_path", None)

    def pick(
        self,
        *,
        refresh: bool = True,
        exclude_ids: Optional[set[str]] = None,
        lease: bool = False,
        lease_seconds: Optional[float] = None,
    ) -> Optional[WebshareSession]:
        """Return the next available session, or None if pool is empty/exhausted.

        Refreshes from the API on first use or when stale. ``exclude_ids`` lets
        bounded diagnostic callers request distinct sessions without changing
        normal round-robin behavior. Sessions leased by another caller are
        always excluded. With ``lease=True``, the selected session remains
        unavailable until it is released or an outcome is reported.
        ``lease_seconds`` can extend that lease for a caller whose hard
        operation deadline exceeds the pool default.
        """
        if refresh and (not self._sessions or (
            time.time() - self._last_refresh > self.refresh_hours * 3600
        )):
            try:
                self.refresh()
            except WebshareProxyError:
                # A stale populated cache remains useful, but an empty pool
                # must preserve the management error so callers can explain
                # why no route exists.
                if not self._sessions:
                    raise

        health_store = getattr(self, "_health_store", None)
        if health_store is not None:
            with self._lock:
                selected, next_cursor = health_store.pick(
                    self._sessions,
                    exclude_ids=set(exclude_ids or ()),
                    lease=lease,
                    lease_seconds=lease_seconds,
                )
                if selected is not None:
                    self._cursor = next_cursor
                    if lease:
                        self._in_flight_ids.add(selected.id)
                return selected

        with self._lock:
            if not self._sessions:
                return None
            now = time.time()
            in_flight = getattr(self, "_in_flight_ids", None)
            if in_flight is None:
                # Older integrations and tests sometimes construct a pool via
                # ``__new__``. Lazily initialize transient lease state so those
                # callers retain backwards compatibility.
                in_flight = set()
                self._in_flight_ids = in_flight
            excluded = set(exclude_ids or ())
            excluded.update(in_flight)
            n = len(self._sessions)
            for offset in range(n):
                idx = (self._cursor + offset) % n
                s = self._sessions[idx]
                if s.id not in excluded and s.is_available(now):
                    self._cursor = (idx + 1) % n
                    s.last_used_at = now
                    if lease:
                        in_flight.add(s.id)
                    return s
            return None  # all in cooldown / retired

    def release(self, session: WebshareSession) -> None:
        """Release an in-flight session without recording a health outcome."""
        health_store = getattr(self, "_health_store", None)
        if health_store is not None:
            health_store.release(session)
            with self._lock:
                self._in_flight_ids.discard(session.id)
            return
        with self._lock:
            in_flight = getattr(self, "_in_flight_ids", None)
            if in_flight is None:
                self._in_flight_ids = set()
                return
            in_flight.discard(session.id)

    def report_success(self, session: WebshareSession) -> None:
        health_store = getattr(self, "_health_store", None)
        if health_store is not None:
            health_store.report_success(session)
            with self._lock:
                self._in_flight_ids.discard(session.id)
            return
        with self._lock:
            in_flight = getattr(self, "_in_flight_ids", None)
            if in_flight is None:
                in_flight = set()
                self._in_flight_ids = in_flight
            in_flight.discard(session.id)
            now = time.time()
            session.success += 1
            session.consecutive_failures = 0
            session.cooldown_until = 0.0
            session.last_success_at = now
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
        health_store = getattr(self, "_health_store", None)
        if health_store is not None:
            safe_summary = (
                redact_sensitive_text(
                    summary,
                    (session.id, session.username, session.password),
                )[:200]
                if summary
                else ""
            )
            health_store.report_failure(
                session,
                kind=kind,
                cooldown_seconds=cooldowns.get(kind, COOLDOWN_OTHER),
                summary=safe_summary,
            )
            with self._lock:
                self._in_flight_ids.discard(session.id)
            return
        with self._lock:
            in_flight = getattr(self, "_in_flight_ids", None)
            if in_flight is None:
                in_flight = set()
                self._in_flight_ids = in_flight
            in_flight.discard(session.id)
            now = time.time()
            if kind == "rate_limited":
                session.fail_429 += 1
            elif kind == "blocked":
                session.fail_blocked += 1
            else:
                session.fail_other += 1
            session.consecutive_failures += 1
            session.cooldown_until = now + cooldowns.get(kind, COOLDOWN_OTHER)
            session.last_failure_at = now
            session.last_failure_kind = kind
            if summary:
                session.last_error = redact_sensitive_text(
                    summary,
                    (session.username, session.password),
                )[:200]
            if session.consecutive_failures >= RETIRE_AFTER_CONSECUTIVE_FAILURES:
                session.retired = True
            self._save_state()

    def _sync_shared_state(self) -> None:
        health_store = getattr(self, "_health_store", None)
        if health_store is None:
            return
        with self._lock:
            last_refresh, cursor, leased_keys = health_store.sync(
                self._sessions
            )
            self._last_refresh = max(self._last_refresh, last_refresh)
            self._cursor = cursor % max(len(self._sessions), 1)
            self._in_flight_ids = {
                session.id
                for session in self._sessions
                if _session_health_key(session.id) in leased_keys
            }

    def available_count(self) -> int:
        """Return sessions eligible for selection right now.

        Availability is not a health assertion: an untested or historically
        failing session becomes available again after its cooldown.
        """
        self._sync_shared_state()
        with self._lock:
            now = time.time()
            in_flight = getattr(self, "_in_flight_ids", set())
            return sum(
                1
                for s in self._sessions
                if s.id not in in_flight and s.is_available(now)
            )

    def recently_healthy_count(self) -> int:
        """Return available sessions with a successful recent live fetch."""
        self._sync_shared_state()
        with self._lock:
            now = time.time()
            in_flight = getattr(self, "_in_flight_ids", set())
            window = (
                getattr(self, "recent_success_hours", DEFAULT_RECENT_SUCCESS_HOURS)
                * 3600
            )
            return sum(
                1
                for s in self._sessions
                if (
                    s.id not in in_flight
                    and s.is_available(now)
                    and s.consecutive_failures == 0
                    and s.last_success_at >= s.last_failure_at
                    and s.was_recently_successful(now, window)
                )
            )

    def healthy_count(self) -> int:
        """Compatibility alias for genuinely, recently healthy sessions."""
        return self.recently_healthy_count()

    def status_snapshot(self) -> dict:
        self._sync_shared_state()
        now = time.time()
        with self._lock:
            in_flight = set(getattr(self, "_in_flight_ids", set()))
        recent_window = (
            getattr(self, "recent_success_hours", DEFAULT_RECENT_SUCCESS_HOURS)
            * 3600
        )
        sessions = []
        for s in self._sessions:
            leased = s.id in in_flight
            recently_successful = (
                s.consecutive_failures == 0
                and s.last_success_at >= s.last_failure_at
                and s.was_recently_successful(now, recent_window)
            )
            available = not leased and s.is_available(now)
            if not s.valid:
                state = "invalid"
            elif s.retired:
                state = "retired"
            elif leased:
                state = "in-flight"
            elif not available:
                state = "cooldown"
            elif s.consecutive_failures:
                state = "ready-failing"
            elif recently_successful:
                state = "ready-tested"
            elif s.success:
                state = "ready-stale"
            else:
                state = "ready-untested"
            sessions.append(
                {
                    "id": s.id,
                    "username": s.username,
                    "country": s.country_code,
                    "valid": s.valid,
                    "retired": s.retired,
                    "in_flight": leased,
                    "available": available,
                    "recently_successful": recently_successful,
                    "state": state,
                    "cooldown_remaining_s": max(0, int(s.cooldown_until - now)),
                    "success": s.success,
                    "fail_429": s.fail_429,
                    "fail_blocked": s.fail_blocked,
                    "fail_other": s.fail_other,
                    "consecutive_failures": s.consecutive_failures,
                    "last_success_at": s.last_success_at,
                    "last_failure_at": s.last_failure_at,
                    "last_failure_kind": s.last_failure_kind,
                    "last_error": (
                        redact_sensitive_text(
                            s.last_error,
                            (s.username, s.password),
                        )
                        if s.last_error
                        else None
                    ),
                }
            )
        counts = {
            "available": sum(1 for s in sessions if s["available"]),
            "recently_healthy": sum(
                1
                for s in sessions
                if s["available"] and s["recently_successful"]
            ),
            "in_flight": len(in_flight),
            "untested": sum(1 for s in self._sessions if s.success == 0),
            "cooling": sum(1 for s in sessions if s["state"] == "cooldown"),
            "retired": sum(1 for s in sessions if s["state"] == "retired"),
            "invalid": sum(1 for s in sessions if s["state"] == "invalid"),
            "failing": sum(
                1 for s in self._sessions if s.consecutive_failures > 0
            ),
        }
        return {
            "source": self.source,
            "session_file": (
                str(self.session_file_path)
                if self.session_file_path is not None
                else None
            ),
            "gateway": f"{self.gateway_host}:{self.gateway_port}",
            "countries": self.countries,
            "total": len(self._sessions),
            # Keep ``healthy`` for callers on the older schema, but give it
            # the corrected meaning rather than silently equating it with
            # availability.
            "healthy": counts["recently_healthy"],
            **counts,
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
            try:
                parsed_port = int(port)
            except ValueError:
                continue
            parsed.append((host, parsed_port, username, password))
    if not parsed:
        return []
    if len(parsed) > limit:
        step = len(parsed) / limit
        parsed = [parsed[int(i * step)] for i in range(limit)]
    return [
        WebshareSession(
            id=username,
            username=username,
            password=password,
            proxy_host=host,
            proxy_port=port,
        )
        for host, port, username, password in parsed
    ]


def _resolve_session_file() -> Path:
    """Resolve/migrate the default credential inventory.

    A deliberately configured custom path remains authoritative. The old
    documented ``.filmot_data/webshare_info.txt`` location is copied
    non-destructively into the per-user config directory and then read there.
    """
    configured = (os.getenv("WEBSHARE_SESSION_FILE") or "").strip()
    legacy = legacy_proxy_session_file()
    target = default_proxy_session_file()
    if configured:
        candidate = Path(os.path.expandvars(configured)).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        candidate = candidate.resolve(strict=False)
        if candidate != legacy.resolve(strict=False):
            return candidate
    if not target.exists() and legacy.exists():
        try:
            secure_copy(legacy, target)
        except OSError:
            # Preserve existing users on read-only homes. Health still remains
            # machine-global; ``proxy status`` exposes the source path.
            return legacy
    if target.is_file():
        # Existing default inventories may have been created manually or by an
        # older release. Enforce the same owner-only boundary as a migration.
        ensure_private_dir(target.parent)
        restrict_private_file(target)
    return target


def get_pool(*, force_new: bool = False) -> Optional[WebshareProxyPool]:
    """Return the process-wide pool, or None if no proxy source is configured.

    Two sources, in order of preference:
      1. WEBSHARE_API_TOKEN  → live API, health-tracked, can self-refresh.
      2. A pre-exported session file (WEBSHARE_SESSION_FILE or the per-user
         config inventory) → rotate across many backbone sessions with no API
         token.
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
                    recent_success_hours=float(
                        os.getenv(
                            "FILMOT_PROXY_HEALTH_HOURS",
                            DEFAULT_RECENT_SUCCESS_HOURS,
                        )
                    ),
                    lease_seconds=float(
                        os.getenv(
                            "FILMOT_PROXY_LEASE_SECONDS",
                            DEFAULT_LEASE_SECONDS,
                        )
                    ),
                )
            except (WebshareProxyError, OSError, sqlite3.Error):
                _pool = None
            if _pool is not None:
                return _pool

        # Fall back to a file-backed pool (no API token needed).
        session_file = _resolve_session_file()
        if session_file.exists():
            sessions = _load_sessions_from_file(session_file, max_sessions)
            if sessions:
                _pool = WebshareProxyPool.__new__(WebshareProxyPool)
                _pool._init_file_backed(
                    sessions,
                    session_file_path=session_file,
                    max_sessions=max_sessions,
                    recent_success_hours=float(
                        os.getenv(
                            "FILMOT_PROXY_HEALTH_HOURS",
                            DEFAULT_RECENT_SUCCESS_HOURS,
                        )
                    ),
                    lease_seconds=float(
                        os.getenv(
                            "FILMOT_PROXY_LEASE_SECONDS",
                            DEFAULT_LEASE_SECONDS,
                        )
                    ),
                )
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
    if isinstance(
        error,
        (
            requests.ConnectionError,
            requests.Timeout,
            requests.exceptions.ProxyError,
            requests.exceptions.SSLError,
        ),
    ):
        return "connection"
    transport_needles = (
        "proxy",
        "tunnel connection failed",
        "max retries exceeded",
        "connection reset",
        "connection aborted",
        "failed to establish a new connection",
        "remote end closed connection",
        "connection refused",
        "connection broken",
        "network is unreachable",
        "name resolution",
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
