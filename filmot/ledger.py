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
from typing import Any, Dict, List, Mapping, Optional, Union

from .library import _legacy_normalize_topic, normalize_topic_name
from .paths import project_data_dir
from .schemas import (
    CommandResult,
    ErrorDetail,
    EventRecord,
    ResultStatus,
    normalize_event_dict,
)


def _reject_json_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number is not allowed: {}".format(value))


def _normalize(name: str) -> str:
    """Filesystem-safe slug for a topic/session name."""
    return normalize_topic_name(name, fallback="session")


def _sessions_dir(
    data_dir: Optional[Union[str, Path]] = None,
) -> Path:
    return project_data_dir(data_dir) / "sessions"


def _sessions_dir_ready(
    path: Path,
    diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """Return whether session storage is readable as a directory."""
    try:
        if not path.exists():
            return False
        if path.is_dir():
            return True
        if diagnostics is not None:
            diagnostics.append({
                "type": "InvalidSessionStorage",
                "message": "Session storage path is not a directory",
                "path": str(path),
            })
    except OSError as error:
        if diagnostics is not None:
            diagnostics.append({
                "type": type(error).__name__,
                "message": str(error),
                "path": str(path),
            })
    return False


def _read_path(
    path: Path,
    diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> list:
    """Read valid events and optionally report every unreadable record."""
    if not path.exists():
        return []
    events = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line_number, line in enumerate(f, 1):
                line = line.strip()
                if line:
                    try:
                        event = json.loads(
                            line,
                            parse_constant=_reject_json_constant,
                        )
                        if isinstance(event, dict):
                            events.append(normalize_event_dict(event))
                        elif diagnostics is not None:
                            diagnostics.append({
                                "type": "InvalidLedgerRecord",
                                "message": "Ledger line is not a JSON object",
                                "path": str(path),
                                "line": line_number,
                            })
                    except (json.JSONDecodeError, ValueError) as error:
                        if diagnostics is not None:
                            diagnostics.append({
                                "type": type(error).__name__,
                                "message": str(error),
                                "path": str(path),
                                "line": line_number,
                            })
    except (OSError, UnicodeError) as error:
        if diagnostics is not None:
            diagnostics.append({
                "type": type(error).__name__,
                "message": str(error),
                "path": str(path),
            })
    return events


def _event_matches_topic(event: dict, topic_slug: str) -> bool:
    """Whether an old event can be safely attributed to a Unicode topic."""
    data = event.get("data")
    if not isinstance(data, dict):
        data = event
    for field in ("topic", "research_topic", "query"):
        value = event.get(field)
        if value is None:
            value = data.get(field)
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


def migrate_legacy_session(
    name: str,
    data_dir: Optional[Union[str, Path]] = None,
) -> int:
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
            event = normalize_event_dict(event)
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
                json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n"
                for event in matching
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


_STATUS_ALIASES = {
    "completed_with_failures": ResultStatus.PARTIAL.value,
    "failed_closed": ResultStatus.FAILED.value,
    "error": ResultStatus.FAILED.value,
    "failure": ResultStatus.FAILED.value,
    "accepted_explicit": ResultStatus.COMPLETED.value,
    "added": ResultStatus.COMPLETED.value,
    "fetched": ResultStatus.COMPLETED.value,
    "found": ResultStatus.COMPLETED.value,
    "passed": ResultStatus.COMPLETED.value,
    "ready": ResultStatus.COMPLETED.value,
    "rendered": ResultStatus.COMPLETED.value,
    "saved": ResultStatus.COMPLETED.value,
    "blocked": ResultStatus.SKIPPED.value,
    "disabled": ResultStatus.SKIPPED.value,
    "not_found": ResultStatus.SKIPPED.value,
}
_CANONICAL_STATUSES = {item.value for item in ResultStatus}


def _normalize_status(value: object) -> tuple[str, Optional[str]]:
    detail = str(value or ResultStatus.COMPLETED.value)
    if detail in _CANONICAL_STATUSES:
        return detail, None
    normalized = _STATUS_ALIASES.get(detail, ResultStatus.COMPLETED.value)
    return normalized, detail


def _append_record(
    record: EventRecord,
    data_dir: Optional[Union[str, Path]],
) -> None:
    sessions = _sessions_dir(data_dir)
    sessions.mkdir(parents=True, exist_ok=True)
    name = record.topic or datetime.now().strftime("%Y-%m-%d")
    with open(sessions / "{}.jsonl".format(name), "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                record.to_dict(),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        )


def log_result(
    kind: str,
    result: CommandResult,
    *,
    topic: Optional[str] = None,
    data_dir: Optional[Union[str, Path]] = None,
    data: Optional[Mapping[str, object]] = None,
) -> None:
    """Append a compact event derived from a typed command result.

    Like :func:`log_event`, this is deliberately best-effort.
    """
    try:
        topic_slug = _normalize(topic) if topic else None
        if topic:
            migrate_legacy_session(topic, data_dir)
        _append_record(
            result.to_event(
                kind,
                topic=topic_slug,
                data=data,
            ),
            data_dir,
        )
    except Exception:
        pass


def log_event(
    kind: str,
    topic: Optional[str] = None,
    data_dir: Optional[Union[str, Path]] = None,
    **fields
) -> None:
    """Append one versioned event to the ledger. Never raises.

    Args:
        kind: Event type ("search", "research", "channel-search", "transcript", ...).
        topic: If given, log to <topic>.jsonl; otherwise to today's date file.
        fields: Arbitrary JSON-serializable event data (query, results, saved, ...).
    """
    try:
        topic_slug = _normalize(topic) if topic else None
        cleaned = {
            str(key): value
            for key, value in fields.items()
            if value is not None and key != "status"
        }
        status, detail_status = _normalize_status(fields.get("status"))
        if detail_status is not None:
            cleaned["detail_status"] = detail_status

        errors = []
        if status == ResultStatus.FAILED.value and fields.get("error") is not None:
            errors.append(
                ErrorDetail(
                    type=str(fields.get("error_type") or "CommandError"),
                    message=str(fields["error"]),
                    stage=(
                        str(fields["failure_stage"])
                        if fields.get("failure_stage") is not None
                        else None
                    ),
                )
            )

        if topic:
            migration_name = topic
            for field in ("research_topic", "query"):
                value = fields.get(field)
                if isinstance(value, str) and _normalize(value) == topic_slug:
                    migration_name = value
                    break
            migrate_legacy_session(migration_name, data_dir)

        record = EventRecord(
            kind=kind,
            command=kind.replace("_", "-"),
            status=status,
            data=cleaned,
            errors=errors,
            topic=topic_slug,
            ts=datetime.now().isoformat(timespec="seconds"),
        )
        _append_record(record, data_dir)
    except Exception:
        pass  # ledger is best-effort; never break the caller


def read_events(
    name: str,
    data_dir: Optional[Union[str, Path]] = None,
    *,
    diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> list:
    """Read session events; optionally collect corruption/read diagnostics."""
    sessions = _sessions_dir(data_dir)
    if not _sessions_dir_ready(sessions, diagnostics):
        return []
    is_date = bool(re.match(r"^\d{4}-\d{2}-\d{2}$", name))
    slug = name if is_date else _normalize(name)
    events = _read_path(
        sessions / "{}.jsonl".format(slug),
        diagnostics,
    )
    if is_date:
        return events

    # Read old records conservatively even before the next write has a chance
    # to migrate them. Do not return unrelated records from a shared legacy
    # ``session.jsonl``/ASCII-stripped file.
    for legacy_slug in _legacy_session_slugs(name):
        if legacy_slug == slug:
            continue
        legacy_events = _read_path(
            sessions / "{}.jsonl".format(legacy_slug),
            diagnostics,
        )
        matching = [
            event for event in legacy_events
            if _event_matches_topic(event, slug)
        ]
        events = matching + events
    return events


def list_sessions(
    data_dir: Optional[Union[str, Path]] = None,
    *,
    diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> list:
    """List sessions and optionally report unreadable physical ledger rows."""
    sessions = _sessions_dir(data_dir)
    if not _sessions_dir_ready(sessions, diagnostics):
        return []
    out = []
    try:
        paths = sorted(sessions.glob("*.jsonl"))
    except OSError as error:
        if diagnostics is not None:
            diagnostics.append({
                "type": type(error).__name__,
                "message": str(error),
                "path": str(sessions),
            })
        return []
    for path in paths:
        # List physical files here. ``read_events`` also overlays attributable
        # legacy records, which would otherwise inflate counts or duplicate a
        # migrated topic in this inventory.
        events = _read_path(path, diagnostics)
        if not events:
            continue
        out.append({
            "name": path.stem,
            "events": len(events),
            "last_ts": events[-1].get("ts", ""),
        })
    out.sort(key=lambda s: s["last_ts"], reverse=True)
    return out


def summarize_events(name: str, events: list) -> Dict[str, Any]:
    """Fold a session replay into explicitly named research universes.

    Counts that answer different questions intentionally stay separate. For
    example, fetched candidates are event totals (and may include the same
    video more than once), while saved and failed-video counts are unique IDs.
    This prevents an attractive but invalid single "corpus size" number.
    """
    status_counts: Dict[str, int] = {}
    kind_counts: Dict[str, int] = {}
    search_queries = []
    search_query_seen = set()
    all_queries = []
    all_query_seen = set()
    queries_by_kind: Dict[str, list] = {}
    search_rows = []
    candidate_fetches = 0
    post_filter_results = 0
    research_search_rows: Dict[tuple, Dict[str, Any]] = {}
    saved_ids = set()
    skipped_ids = set()
    failed_ids = set()
    transcript_failures = 0
    comparisons = 0
    claim_mutations = 0
    claim_ids = set()
    research_runs: Dict[str, Dict[str, Any]] = {}

    def as_int(value: object) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    for event in events:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind") or "unknown")
        status = str(event.get("status") or "unknown")
        data = event.get("data")
        if not isinstance(data, dict):
            data = {}
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1

        query = data.get("query")
        if isinstance(query, str) and query:
            if query not in all_query_seen:
                all_query_seen.add(query)
                all_queries.append(query)
            kind_queries = queries_by_kind.setdefault(kind, [])
            if query not in kind_queries:
                kind_queries.append(query)

        if kind == "search":
            if isinstance(query, str) and query and query not in search_query_seen:
                search_query_seen.add(query)
                search_queries.append(query)
            fetched = as_int(
                data.get("candidates_fetched")
                or data.get("page_count")
                or 0
            )
            filtered = as_int(
                data.get("post_filter_count")
                if data.get("post_filter_count") is not None
                else data.get("results") or 0
            )
            candidate_fetches += fetched
            post_filter_results += filtered
            search_rows.append({
                "ts": event.get("ts"),
                "status": status,
                "query": str(query or ""),
                "api_total": as_int(data.get("api_total") or data.get("total")),
                "candidates_fetched": fetched,
                "post_filter_count": filtered,
                "partial": bool(data.get("partial")),
            })

        if kind == "research_checkpoint":
            phase = str(data.get("phase") or "")
            run_id = str(data.get("run_id") or "legacy")
            stage = str(data.get("stage") or "")
            if phase == "search" and stage:
                key = (run_id, stage)
                row = research_search_rows.setdefault(key, {
                    "run_id": run_id,
                    "stage": stage,
                    "query": "",
                    "api_total": 0,
                    "candidates_fetched": 0,
                    "post_filter_count": 0,
                    "pages_fetched": 0,
                    "partial": False,
                    "status": ResultStatus.STARTED.value,
                    "ts": event.get("ts"),
                    "filter_steps": [],
                })
                row["status"] = status
                row["ts"] = event.get("ts")
                if isinstance(data.get("query"), str):
                    row["query"] = data["query"]
                if data.get("api_total") is not None:
                    row["api_total"] = as_int(data.get("api_total"))
                if data.get("candidates") is not None:
                    fetched = as_int(data.get("candidates"))
                    row["candidates_fetched"] = fetched
                    row["post_filter_count"] = fetched
                if data.get("pages") is not None:
                    row["pages_fetched"] = as_int(data.get("pages"))
                row["partial"] = bool(data.get("partial"))
                if row["partial"] and status == ResultStatus.COMPLETED.value:
                    row["status"] = ResultStatus.PARTIAL.value
            elif phase in {"search_filter", "relationship_gate", "broad_gate"}:
                if not stage and phase == "broad_gate":
                    stage = "broad_loose"
                row = research_search_rows.get((run_id, stage))
                if row is not None:
                    before_value = (
                        data.get("candidates_before")
                        if data.get("candidates_before") is not None
                        else data.get("candidates")
                    )
                    before = (
                        as_int(before_value)
                        if before_value is not None
                        else int(row["post_filter_count"])
                    )
                    if data.get("eligible") is not None:
                        after = as_int(data.get("eligible"))
                    elif data.get("candidates_after") is not None:
                        after = as_int(data.get("candidates_after"))
                    elif data.get("detail_status") == "blocked":
                        after = 0
                    else:
                        after = int(row["post_filter_count"])
                    row["post_filter_count"] = after
                    if phase == "broad_gate" and data.get("detail_status") == "blocked":
                        row["status"] = ResultStatus.SKIPPED.value
                    row["filter_steps"].append({
                        "phase": phase,
                        "before": before,
                        "after": after,
                        "status": status,
                        "detail_status": data.get("detail_status"),
                    })

        video_id = data.get("video_id")
        if isinstance(video_id, str) and video_id:
            if kind == "transcript_save":
                if status == ResultStatus.COMPLETED.value:
                    saved_ids.add(video_id)
                elif status == ResultStatus.SKIPPED.value:
                    skipped_ids.add(video_id)
                elif status == ResultStatus.FAILED.value:
                    transcript_failures += 1
                    failed_ids.add(video_id)
            if kind == "transcript" and status == ResultStatus.FAILED.value:
                transcript_failures += 1
                failed_ids.add(video_id)
            if kind == "research_checkpoint" and data.get("phase") in {
                "download_item",
                "probe_download",
            }:
                detail_status = data.get("detail_status") or data.get("legacy_status")
                if status == ResultStatus.COMPLETED.value and detail_status == "saved":
                    saved_ids.add(video_id)
                elif status == ResultStatus.SKIPPED.value:
                    skipped_ids.add(video_id)
                elif status == ResultStatus.FAILED.value:
                    transcript_failures += 1
                    failed_ids.add(video_id)

        if kind == "library_compare":
            comparisons += 1
        if kind.startswith("claims_"):
            if status == ResultStatus.COMPLETED.value:
                claim_mutations += 1
            claim_id = data.get("claim_id")
            if isinstance(claim_id, str) and claim_id:
                claim_ids.add(claim_id)

        if kind in {"research", "research_end"}:
            run_id = str(data.get("run_id") or "legacy-{}".format(event.get("ts", "")))
            current = research_runs.get(run_id)
            # Prefer the final aggregate research result over research_end;
            # both may describe the same run and must not be added together.
            priority = 2 if kind == "research" else 1
            if current is None or priority >= int(current.get("_priority", 0)):
                research_runs[run_id] = {
                    "run_id": run_id,
                    "status": status,
                    "scout": as_int(data.get("scout")),
                    "filmot_total": as_int(data.get("filmot_total")),
                    "saved": as_int(data.get("saved")),
                    "skipped": as_int(data.get("skipped")),
                    "failed": as_int(data.get("failed")),
                    "deduped": as_int(data.get("deduped")),
                    "selected": as_int(data.get("selected")),
                    "probe_saved": as_int(
                        data.get("probe_saved")
                        if data.get("probe_saved") is not None
                        else data.get("probe")
                    ),
                    "probe_download_failed": as_int(
                        data.get("probe_download_failed")
                        if data.get("probe_download_failed") is not None
                        else data.get("probe_failed")
                    ),
                    "probe_query_failed": as_int(data.get("probe_query_failed")),
                    "chars": as_int(data.get("chars")),
                    "_priority": priority,
                }

    research_rows = []
    for row in research_runs.values():
        visible = dict(row)
        visible.pop("_priority", None)
        research_rows.append(visible)
    research_rows.sort(key=lambda item: item["run_id"])
    staged_search_rows = sorted(
        research_search_rows.values(),
        key=lambda item: (
            str(item.get("run_id", "")),
            str(item.get("ts", "")),
            str(item.get("stage", "")),
        ),
    )

    return {
        "name": name,
        "event_count": len(events),
        "events_by_kind": dict(sorted(kind_counts.items())),
        "statuses": dict(sorted(status_counts.items())),
        "query_inventory": {
            "all_unique": all_queries,
            "by_event_kind": dict(sorted(queries_by_kind.items())),
        },
        "searches": {
            "events": len(search_rows),
            "unique_queries": search_queries,
            "candidate_fetches": candidate_fetches,
            "post_filter_results": post_filter_results,
            "scope_rows": search_rows,
            "counting_note": (
                "candidate_fetches and post_filter_results are per-event totals; "
                "they may include the same video more than once"
            ),
        },
        "research_searches": {
            "stages": len(staged_search_rows),
            "candidate_fetches": sum(
                int(row["candidates_fetched"]) for row in staged_search_rows
            ),
            "post_filter_results": sum(
                int(row["post_filter_count"]) for row in staged_search_rows
            ),
            "scope_rows": staged_search_rows,
            "counting_note": (
                "research search stages are separate from standalone search events; "
                "their candidate totals may repeat videos across fallback stages"
            ),
        },
        "transcripts": {
            "saved_unique": len(saved_ids),
            "saved_video_ids": sorted(saved_ids),
            "skipped_unique": len(skipped_ids),
            "skipped_video_ids": sorted(skipped_ids),
            "failed_attempts": transcript_failures,
            "failed_unique": len(failed_ids),
            "failed_video_ids": sorted(failed_ids),
        },
        "research": {
            "runs": len(research_rows),
            "saved_reported": sum(int(row["saved"]) for row in research_rows),
            "skipped_reported": sum(int(row["skipped"]) for row in research_rows),
            "failed_reported": sum(int(row["failed"]) for row in research_rows),
            "deduped_reported": sum(int(row["deduped"]) for row in research_rows),
            "probe_saved_reported": sum(
                int(row["probe_saved"]) for row in research_rows
            ),
            "probe_download_failed_reported": sum(
                int(row["probe_download_failed"]) for row in research_rows
            ),
            "probe_query_failed_reported": sum(
                int(row["probe_query_failed"]) for row in research_rows
            ),
            "total_saved_reported": sum(
                int(row["saved"]) + int(row["probe_saved"])
                for row in research_rows
            ),
            "rows": research_rows,
            "counting_note": (
                "research totals preserve selected-download and probe outcomes "
                "separately and are distinct from unique transcript-save events"
            ),
        },
        "analysis": {"historical_comparisons_logged": comparisons},
        "claims": {
            "mutation_events": claim_mutations,
            "unique_claim_ids": sorted(claim_ids),
        },
    }
