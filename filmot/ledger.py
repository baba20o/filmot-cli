"""Session ledger — append-only log of research activity.

Records every query, result count, and download to a JSONL file so a later
session (or a fresh agent instance) can resume an investigation instead of
re-deriving it from scratch. Logging is strictly best-effort: a ledger failure
must never break the command that triggered it.

Storage:
    .filmot_data/
        sessions/
            2026-06-10.jsonl          # date-scoped, for ad-hoc work
            fable-5-mythos.jsonl      # topic-scoped, for `research <topic>`
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional


def _normalize(name: str) -> str:
    """Filesystem-safe slug for a topic/session name."""
    slug = re.sub(r"[^a-z0-9-]+", "-", (name or "").lower()).strip("-")
    return slug or "session"


def _sessions_dir(data_dir: str = ".filmot_data") -> Path:
    return Path(data_dir) / "sessions"


def log_event(kind: str, topic: Optional[str] = None, data_dir: str = ".filmot_data", **fields) -> None:
    """Append one event to the ledger. Never raises.

    Args:
        kind: Event type ("search", "research", "channel-search", "transcript", ...).
        topic: If given, log to <topic>.jsonl; otherwise to today's date file.
        fields: Arbitrary JSON-serializable event data (query, results, saved, ...).
    """
    try:
        sessions = _sessions_dir(data_dir)
        sessions.mkdir(parents=True, exist_ok=True)
        name = _normalize(topic) if topic else datetime.now().strftime("%Y-%m-%d")
        record = {"ts": datetime.now().isoformat(timespec="seconds"), "kind": kind}
        for k, v in fields.items():
            if v is not None:
                record[k] = v
        with open(sessions / f"{name}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # ledger is best-effort; never break the caller


def read_events(name: str, data_dir: str = ".filmot_data") -> list:
    """Read all events from a session file (by topic slug or date). Empty on miss."""
    path = _sessions_dir(data_dir) / f"{_normalize(name) if not re.match(r'^\d{4}-\d{2}-\d{2}$', name) else name}.jsonl"
    if not path.exists():
        return []
    events = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []
    return events


def list_sessions(data_dir: str = ".filmot_data") -> list:
    """Return [{name, events, last_ts}] for every session file, newest activity first."""
    sessions = _sessions_dir(data_dir)
    if not sessions.exists():
        return []
    out = []
    for path in sessions.glob("*.jsonl"):
        events = read_events(path.stem, data_dir)
        if not events:
            continue
        out.append({
            "name": path.stem,
            "events": len(events),
            "last_ts": events[-1].get("ts", ""),
        })
    out.sort(key=lambda s: s["last_ts"], reverse=True)
    return out
