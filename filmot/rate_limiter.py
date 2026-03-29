"""Rate limiting for Filmot API requests.

Supports both in-process and cross-process rate limiting via SQLite.
"""

import logging
import os
import sqlite3
import time
import threading
from pathlib import Path
from typing import Optional
from collections import deque

logger = logging.getLogger(__name__)

# Shared lockfile location — all processes using the same API key coordinate here
_DEFAULT_SHARED_DB = Path(os.environ.get(
    "FILMOT_RATE_LIMIT_DB",
    Path.home() / ".filmot" / "rate_limit.db",
))


class RateLimiter:
    """Token bucket rate limiter with sliding window."""

    def __init__(self, requests_per_second: float = 1.0, burst_size: int = 5):
        self.requests_per_second = requests_per_second
        self.burst_size = burst_size
        self.min_interval = 1.0 / requests_per_second
        self.request_times: deque = deque(maxlen=burst_size)
        self.lock = threading.Lock()
        self.total_requests = 0
        self.total_waits = 0
        self.total_wait_time = 0.0

    def acquire(self) -> float:
        with self.lock:
            current_time = time.time()
            wait_time = 0.0
            window_start = current_time - 1.0
            while self.request_times and self.request_times[0] < window_start:
                self.request_times.popleft()
            if len(self.request_times) >= self.burst_size:
                oldest = self.request_times[0]
                wait_time = (oldest + 1.0) - current_time
                if wait_time > 0:
                    self.total_waits += 1
                    self.total_wait_time += wait_time
            if self.request_times:
                last_request = self.request_times[-1]
                interval_wait = (last_request + self.min_interval) - current_time
                if interval_wait > wait_time:
                    wait_time = interval_wait
        if wait_time > 0:
            time.sleep(wait_time)
        with self.lock:
            self.request_times.append(time.time())
            self.total_requests += 1
        return wait_time

    def stats(self) -> dict:
        with self.lock:
            return {
                "total_requests": self.total_requests,
                "total_waits": self.total_waits,
                "total_wait_time": round(self.total_wait_time, 2),
                "avg_wait_time": round(self.total_wait_time / max(self.total_waits, 1), 3),
                "requests_per_second": self.requests_per_second,
                "burst_size": self.burst_size,
            }

    def reset_stats(self):
        with self.lock:
            self.total_requests = 0
            self.total_waits = 0
            self.total_wait_time = 0.0

    def report_success(self):
        pass

    def report_rate_limit(self):
        pass


class AdaptiveRateLimiter(RateLimiter):
    """Rate limiter that adapts based on API response codes."""

    def __init__(self, requests_per_second: float = 1.0, burst_size: int = 5):
        super().__init__(requests_per_second, burst_size)
        self.consecutive_errors = 0
        self.backoff_factor = 1.0
        self.max_backoff = 10.0

    def report_success(self):
        with self.lock:
            self.consecutive_errors = 0
            self.backoff_factor = max(1.0, self.backoff_factor * 0.9)

    def report_rate_limit(self):
        with self.lock:
            self.consecutive_errors += 1
            self.backoff_factor = min(self.max_backoff, self.backoff_factor * 2)

    def acquire(self) -> float:
        base_wait = super().acquire()
        with self.lock:
            if self.backoff_factor > 1.0:
                extra_wait = (self.backoff_factor - 1.0) * self.min_interval
                if extra_wait > 0:
                    time.sleep(extra_wait)
                    return base_wait + extra_wait
        return base_wait


class SharedRateLimiter(RateLimiter):
    """Cross-process rate limiter backed by SQLite.

    Coordinates requests across multiple processes sharing the same API key.
    Falls back to in-memory limiting if the DB can't be created.
    """

    def __init__(self, requests_per_second: float = 1.0, burst_size: int = 5,
                 db_path: Optional[Path] = None):
        super().__init__(requests_per_second, burst_size)
        self.db_path = db_path or _DEFAULT_SHARED_DB
        self.consecutive_errors = 0
        self.backoff_factor = 1.0
        self.max_backoff = 10.0
        self._db_available = self._init_db()

    def _init_db(self) -> bool:
        """Create the shared DB and table if needed."""
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.db_path), timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS request_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    pid INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_request_timestamp
                ON request_log (timestamp)
            """)
            conn.commit()
            conn.close()
            logger.debug("Shared rate limiter DB ready: %s", self.db_path)
            return True
        except Exception as e:
            logger.warning("Shared rate limiter DB unavailable (%s) — falling back to in-memory", e)
            return False

    def _count_recent_requests(self, conn: sqlite3.Connection, window: float) -> int:
        """Count requests from all processes within the sliding window."""
        cutoff = time.time() - window
        row = conn.execute(
            "SELECT COUNT(*) FROM request_log WHERE timestamp > ?", (cutoff,)
        ).fetchone()
        return row[0] if row else 0

    def _get_oldest_in_window(self, conn: sqlite3.Connection, window: float) -> Optional[float]:
        """Get the oldest request timestamp within the window."""
        cutoff = time.time() - window
        row = conn.execute(
            "SELECT MIN(timestamp) FROM request_log WHERE timestamp > ?", (cutoff,)
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    def _record_request(self, conn: sqlite3.Connection):
        """Record a request from this process."""
        conn.execute(
            "INSERT INTO request_log (timestamp, pid) VALUES (?, ?)",
            (time.time(), os.getpid()),
        )
        conn.commit()

    def _cleanup_old(self, conn: sqlite3.Connection):
        """Remove entries older than 60 seconds to keep the table small."""
        cutoff = time.time() - 60.0
        conn.execute("DELETE FROM request_log WHERE timestamp < ?", (cutoff,))
        conn.commit()

    def acquire(self) -> float:
        if not self._db_available:
            return super().acquire()

        wait_time = 0.0
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=5)
            conn.execute("PRAGMA busy_timeout=5000")

            # Check how many requests are in the current 1-second window
            window = 1.0
            recent = self._count_recent_requests(conn, window)

            if recent >= self.burst_size:
                oldest = self._get_oldest_in_window(conn, window)
                if oldest:
                    wait_time = (oldest + window) - time.time()
                    if wait_time < 0:
                        wait_time = 0

            # Ensure minimum interval from most recent request (any process)
            row = conn.execute(
                "SELECT MAX(timestamp) FROM request_log"
            ).fetchone()
            if row and row[0] is not None:
                interval_wait = (row[0] + self.min_interval) - time.time()
                if interval_wait > wait_time:
                    wait_time = interval_wait

            # Apply backoff if we've been hitting 429s
            with self.lock:
                if self.backoff_factor > 1.0:
                    extra = (self.backoff_factor - 1.0) * self.min_interval
                    wait_time += extra

            if wait_time > 0:
                logger.debug("Shared rate limiter: waiting %.1fs (pid=%d)", wait_time, os.getpid())
                conn.close()
                time.sleep(wait_time)
                conn = sqlite3.connect(str(self.db_path), timeout=5)
                conn.execute("PRAGMA busy_timeout=5000")

            # Record this request
            self._record_request(conn)

            # Periodic cleanup
            with self.lock:
                self.total_requests += 1
                if self.total_requests % 20 == 0:
                    self._cleanup_old(conn)
                if wait_time > 0:
                    self.total_waits += 1
                    self.total_wait_time += wait_time

            conn.close()
        except Exception as e:
            logger.warning("Shared rate limiter error (%s) — falling back to in-memory", e)
            return super().acquire()

        return wait_time

    def report_success(self):
        with self.lock:
            self.consecutive_errors = 0
            self.backoff_factor = max(1.0, self.backoff_factor * 0.9)

    def report_rate_limit(self):
        with self.lock:
            self.consecutive_errors += 1
            self.backoff_factor = min(self.max_backoff, self.backoff_factor * 2)


# Global rate limiter instance
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter(requests_per_second: float = 2.0, burst_size: int = 5,
                     shared: bool = True) -> RateLimiter:
    """Get or create the global rate limiter instance.

    Args:
        requests_per_second: Max sustained request rate.
        burst_size: Max burst of requests allowed.
        shared: Use cross-process SQLite limiter (default True).
    """
    global _rate_limiter
    if _rate_limiter is None:
        if shared:
            _rate_limiter = SharedRateLimiter(requests_per_second, burst_size)
        else:
            _rate_limiter = AdaptiveRateLimiter(requests_per_second, burst_size)
    return _rate_limiter
