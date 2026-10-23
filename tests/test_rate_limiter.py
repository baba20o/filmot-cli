"""The cross-process rate limiter prunes its log and reserves slots atomically."""

import sqlite3
import threading
import time

import pytest

from filmot.rate_limiter import SharedRateLimiter


def rows(db_path):
    with sqlite3.connect(str(db_path)) as conn:
        return [row[0] for row in conn.execute("SELECT timestamp FROM request_log ORDER BY id")]


def test_old_requests_are_pruned_when_a_process_starts(tmp_path):
    db_path = tmp_path / "rate_limit.db"
    SharedRateLimiter(db_path=db_path)
    now = time.time()
    with sqlite3.connect(str(db_path)) as conn:
        conn.executemany(
            "INSERT INTO request_log (timestamp, pid) VALUES (?, ?)",
            [(now - 3600, 1), (now - 120, 1), (now - 5, 1)],
        )

    SharedRateLimiter(db_path=db_path)

    assert rows(db_path) == [pytest.approx(now - 5)]


def in_any_window(slots, window=1.0):
    """The most slots that fall in one half-open window (t - window, t]."""
    return max(sum(1 for other in slots if slot - window < other <= slot) for slot in slots)


# At 127.0, rounding once left the oldest slot inside the window it had just
# been moved past, and the reservation loop never ended.
@pytest.mark.parametrize("now", [100.0, 127.0, 2147483647.0])
def test_queued_reservations_never_exceed_the_burst_limit(tmp_path, monkeypatch, now):
    monkeypatch.setattr("filmot.rate_limiter.time.time", lambda: now)
    monkeypatch.setattr("filmot.rate_limiter.time.sleep", lambda seconds: None)
    limiter = SharedRateLimiter(requests_per_second=100.0, burst_size=2, db_path=tmp_path / "r.db")

    # A stalled loop fails the test instead of hanging the run.
    worker = threading.Thread(target=lambda: [limiter.acquire() for _ in range(6)], daemon=True)
    worker.start()
    worker.join(10)
    assert not worker.is_alive(), "the reservation loop stalled"

    slots = rows(tmp_path / "r.db")
    assert in_any_window(slots) <= 2
    assert slots == [pytest.approx(now + s, abs=1e-6) for s in (0, 0.01, 1, 1.01, 2, 2.01)]


def test_concurrent_reservations_keep_the_spacing_and_burst_limits(tmp_path, monkeypatch):
    monkeypatch.setattr("filmot.rate_limiter.time.sleep", lambda seconds: None)
    db_path = tmp_path / "rate_limit.db"
    limiters = [
        SharedRateLimiter(requests_per_second=50.0, burst_size=3, db_path=db_path) for _ in range(6)
    ]
    start = threading.Barrier(len(limiters))

    def run(limiter):
        start.wait()
        for _ in range(4):
            limiter.acquire()

    threads = [threading.Thread(target=run, args=(limiter,)) for limiter in limiters]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    slots = sorted(rows(db_path))
    assert len(slots) == 24
    # Doubles near current Unix time resolve about 2.4e-7 seconds.
    assert min(b - a for a, b in zip(slots, slots[1:])) >= 0.02 - 1e-6
    assert in_any_window(slots) <= 3


def test_concurrent_processes_wait_behind_each_others_reservations(tmp_path, monkeypatch):
    # Two limiters stand in for two processes. Sleeping is skipped, so only
    # the reservations, not elapsed time, can space the requests out.
    monkeypatch.setattr("filmot.rate_limiter.time.sleep", lambda seconds: None)
    db_path = tmp_path / "rate_limit.db"
    first = SharedRateLimiter(requests_per_second=2.0, burst_size=5, db_path=db_path)
    second = SharedRateLimiter(requests_per_second=2.0, burst_size=5, db_path=db_path)

    waits = [first.acquire(), second.acquire(), first.acquire(), second.acquire()]

    assert waits[0] == 0
    assert waits[1:] == [pytest.approx(0.5 * n, abs=0.05) for n in (1, 2, 3)]
    timestamps = rows(db_path)
    assert [b - a for a, b in zip(timestamps, timestamps[1:])] == [pytest.approx(0.5, abs=0.05)] * 3
