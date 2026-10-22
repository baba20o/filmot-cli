"""Per-process Filmot API usage: requests, credits, quotas, and failovers.

Each CLI invocation is one process, so a process-wide record describes exactly
what the current command spent. Raw results, ledger events, and the human
stderr summary all read the same snapshot. Only response metadata is kept,
never request headers or credentials.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime
import re
import threading
from typing import Any, Dict, List, Mapping, Optional


_MAX_FAILOVERS = 20
_TIMESTAMP = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})"
    r"(?:[T ](?P<time>\d{2}:\d{2}(?::\d{2})?)(?:\.\d{1,9})?)?"
    r"(?P<zone>Z|[+-]\d{2}:?\d{2})?"
)


def valid_timestamp(value: str) -> bool:
    """True for an ISO-8601 date or date-time with real calendar and clock values."""
    match = _TIMESTAMP.fullmatch(value)
    if not match:
        return False
    try:
        datetime.date.fromisoformat(match.group("date"))
        if match.group("time"):
            datetime.time(*(int(part) for part in match.group("time").split(":")))
    except ValueError:
        return False
    zone = match.group("zone")
    if zone and zone != "Z":
        hours, minutes = int(zone[1:3]), int(zone[-2:])
        if hours > 23 or minutes > 59:
            return False
    return True
_COUNT_HEADERS = (
    ("x-api-credits-used", "credits_used"),
    ("x-api-credits-remaining", "credits_remaining"),
    ("x-ratelimit-requests-limit", "requests_limit"),
    ("x-ratelimit-requests-remaining", "requests_remaining"),
)

_lock = threading.Lock()
_providers: Dict[str, Dict[str, Any]] = {}
_failovers: List[Dict[str, str]] = []


def _entry(provider: str) -> Dict[str, Any]:
    return _providers.setdefault(provider, {"requests": 0, "cache_hits": 0})


def _count(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def record_response(provider: str, headers: Optional[Mapping[str, Any]]) -> None:
    """Count one HTTP attempt and keep the latest credit or plan counters."""
    try:
        items = dict(headers or {}).items()
    except (TypeError, ValueError):
        items = ()
    lowered = {str(key).casefold(): value for key, value in items}
    with _lock:
        entry = _entry(provider)
        entry["requests"] += 1
        cost = _count(lowered.get("x-api-credit-cost"))
        if cost is not None:
            entry["credits_charged"] = entry.get("credits_charged", 0) + cost
        for header, key in _COUNT_HEADERS:
            value = _count(lowered.get(header))
            if value is not None:
                entry[key] = value
        # Keep header text only when it is a timestamp, so no arbitrary value
        # (or echoed credential) can reach raw output, the ledger, or stderr.
        reset = str(lowered.get("x-api-credits-reset") or "").strip()
        if valid_timestamp(reset):
            entry["credits_reset"] = reset


def record_transport_error(provider: str) -> None:
    """Count an attempt that never received a response; no credits are inferred."""
    with _lock:
        entry = _entry(provider)
        entry["requests"] += 1
        entry["transport_errors"] = entry.get("transport_errors", 0) + 1


def record_cache_hit(provider: str) -> None:
    with _lock:
        _entry(provider)["cache_hits"] += 1


def record_failover(endpoint: str, source: str, target: str, reason: str) -> None:
    with _lock:
        if len(_failovers) < _MAX_FAILOVERS:
            _failovers.append(
                {"endpoint": endpoint, "from": source, "to": target, "reason": reason}
            )


def snapshot() -> Optional[Dict[str, Any]]:
    """Return a JSON-safe copy of this process's usage, or None before any use."""
    with _lock:
        if not _providers and not _failovers:
            return None
        usage: Dict[str, Any] = {"providers": copy.deepcopy(_providers)}
        if _failovers:
            usage["failovers"] = copy.deepcopy(_failovers)
        return usage


def reset() -> None:
    with _lock:
        _providers.clear()
        _failovers.clear()


def attach_usage(outcome):
    """Return ``outcome`` carrying the current snapshot unless it already has one."""
    if getattr(outcome, "api", None) is not None:
        return outcome
    usage = snapshot()
    if not usage:
        return outcome
    return dataclasses.replace(outcome, api=usage)


def summary_line(usage: Optional[Mapping[str, Any]] = None) -> Optional[str]:
    """One credential-free line describing requests, credits, and failovers."""
    usage = snapshot() if usage is None else usage
    if not usage:
        return None
    parts = []
    for name, entry in sorted((usage.get("providers") or {}).items()):
        requests = entry.get("requests", 0)
        detail = "{} {} request{}".format(name, requests, "" if requests == 1 else "s")
        if entry.get("transport_errors"):
            detail += ", {} without a response".format(entry["transport_errors"])
        if entry.get("cache_hits"):
            detail += ", {} cached".format(entry["cache_hits"])
        if "credits_charged" in entry:
            detail += ", {} credit{}".format(
                entry["credits_charged"], "" if entry["credits_charged"] == 1 else "s",
            )
        remaining = []
        if "credits_remaining" in entry:
            remaining.append("{:,} credits left".format(entry["credits_remaining"]))
            if entry.get("credits_reset"):
                remaining.append("resets {}".format(str(entry["credits_reset"])[:10]))
        elif "requests_remaining" in entry:
            limit = entry.get("requests_limit")
            remaining.append(
                "{:,}{} requests left".format(
                    entry["requests_remaining"],
                    " of {:,}".format(limit) if isinstance(limit, int) else "",
                )
            )
        if remaining:
            detail += " ({})".format("; ".join(remaining))
        parts.append(detail)
    for failover in usage.get("failovers") or []:
        parts.append(
            "failover {} -> {} on {} ({})".format(
                failover.get("from"), failover.get("to"),
                failover.get("endpoint"), failover.get("reason"),
            )
        )
    return "Filmot API: " + " | ".join(parts)
