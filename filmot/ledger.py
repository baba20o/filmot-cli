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

from .library import _legacy_normalize_topic, normalize_topic_name


def _normalize(name: str) -> str:
    """Filesystem-safe slug for a topic/session name."""
    return normalize_topic_name(name, fallback="session")


def _sessions_dir(data_dir: str = ".filmot_data") -> Path:
    return Path(data_dir) / "sessions"


def _read_path(path: Path) -> list:
    """Read valid JSON events from one physical ledger path."""
    if not path.exists():
        return []
    events = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        event = json.loads(line)
                        if isinstance(event, dict):
                            events.append(event)
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []
    return events


def _event_matches_topic(event: dict, topic_slug: str) -> bool:
    """Whether an old event can be safely attributed to a Unicode topic."""
    for field in ("topic", "research_topic", "query"):
        value = event.get(field)
        if isinstance(value, str) and _normalize(value) == topic_slug:
            return True
    return False


def _legacy_session_slugs(name: str) -> list:
    """Possible files produced before library/ledger normalization was shared."""
    slugs = [
        _legacy_normalize_topic(name, fallback="session"),
        # ``research`` normalized through TranscriptLibrary first, so a pure
        # non-Latin topic was often routed to uncategorized.jsonl rather than
        # directly to session.jsonl.
        _legacy_normalize_topic(name, fallback="uncategorized"),
    ]
    return list(dict.fromkeys(slugs))


def migrate_legacy_session(name: str, data_dir: str = ".filmot_data") -> int:
    """Move safely attributable events out of an old ASCII-only session file.

    The old normalizer collapsed pure non-Latin names into ``session.jsonl``.
    Only records whose topic/query identifies ``name`` are moved; ambiguous
    records stay in the legacy file. The function is idempotent and returns the
    number of migrated events.
    """
    topic_slug = _normalize(name)
    sessions = _sessions_dir(data_dir)
    migrated = 0

    for legacy_slug in _legacy_session_slugs(name):
        if topic_slug == legacy_slug:
            continue
        source = sessions / "{}.jsonl".format(legacy_slug)
        if not source.exists():
            continue

        try:
            original_lines = source.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue

        matching = []
        remaining_lines = []
        for line in original_lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                remaining_lines.append(line)
                continue
            if not isinstance(event, dict):
                remaining_lines.append(line)
                continue
            if _event_matches_topic(event, topic_slug):
                matching.append(event)
            else:
                remaining_lines.append(line)

        if not matching:
            continue

        try:
            sessions.mkdir(parents=True, exist_ok=True)
            destination = sessions / "{}.jsonl".format(topic_slug)
            existing = destination.read_text(encoding="utf-8") if destination.exists() else ""
            migrated_text = "".join(
                json.dumps(event, ensure_ascii=False) + "\n" for event in matching
            )
            # Legacy history normally predates the canonical file. Prepending
            # keeps the common migration case chronological.
            destination_tmp = destination.with_name(destination.name + ".tmp")
            destination_tmp.write_text(migrated_text + existing, encoding="utf-8")
            destination_tmp.replace(destination)

            if remaining_lines:
                source_tmp = source.with_name(source.name + ".tmp")
                source_tmp.write_text("\n".join(remaining_lines) + "\n", encoding="utf-8")
                source_tmp.replace(source)
            else:
                source.unlink()
            migrated += len(matching)
        except OSError:
            continue

    return migrated


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
        if topic:
            # Retaining the canonical topic in each event makes future
            # migrations and per-event attribution unambiguous.
            record["topic"] = name
        for k, v in fields.items():
            if v is not None:
                record[k] = v
        if topic:
            migration_name = topic
            for field in ("research_topic", "query"):
                value = fields.get(field)
                if isinstance(value, str) and _normalize(value) == name:
                    migration_name = value
                    break
            migrate_legacy_session(migration_name, data_dir)
        with open(sessions / f"{name}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # ledger is best-effort; never break the caller


def read_events(name: str, data_dir: str = ".filmot_data") -> list:
    """Read all events from a session file (by topic slug or date). Empty on miss."""
    is_date = bool(re.match(r"^\d{4}-\d{2}-\d{2}$", name))
    slug = name if is_date else _normalize(name)
    events = _read_path(_sessions_dir(data_dir) / "{}.jsonl".format(slug))
    if is_date:
        return events

    # Read old records conservatively even before the next write has a chance
    # to migrate them. Do not return unrelated records from a shared legacy
    # ``session.jsonl``/ASCII-stripped file.
    for legacy_slug in _legacy_session_slugs(name):
        if legacy_slug == slug:
            continue
        legacy_events = _read_path(
            _sessions_dir(data_dir) / "{}.jsonl".format(legacy_slug)
        )
        matching = [
            event for event in legacy_events
            if _event_matches_topic(event, slug)
        ]
        events = matching + events
    return events


def list_sessions(data_dir: str = ".filmot_data") -> list:
    """Return [{name, events, last_ts}] for every session file, newest activity first."""
    sessions = _sessions_dir(data_dir)
    if not sessions.exists():
        return []
    out = []
    for path in sessions.glob("*.jsonl"):
        # List physical files here. ``read_events`` also overlays attributable
        # legacy records, which would otherwise inflate counts or duplicate a
        # migrated topic in this inventory.
        events = _read_path(path)
        if not events:
            continue
        out.append({
            "name": path.stem,
            "events": len(events),
            "last_ts": events[-1].get("ts", ""),
        })
    out.sort(key=lambda s: s["last_ts"], reverse=True)
    return out
