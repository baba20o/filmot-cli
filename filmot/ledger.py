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
from .redaction import redact_sensitive_value
from .session_context import current_session
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
    # New events explicitly distinguish investigation identity from corpus
    # topic. Never migrate them based on a coincidentally matching query/topic.
    if isinstance(data.get("session"), str):
        return _normalize(data["session"]) == topic_slug
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
            try:
                event = normalize_event_dict(event)
            except ValueError:
                # Leave lines the current schema cannot validate where they
                # are. One bad legacy record must not block every future
                # ledger write for this topic.
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

# Session summaries are a resumption aid, not a second copy of the ledger.
# Keep provenance useful in human and raw output without allowing a long-running
# investigation (or an unexpectedly large title/query) to dominate the result.
SESSION_PROVENANCE_ROW_LIMIT = 25
SESSION_PROVENANCE_QUERY_CHARS = 180
SESSION_PROVENANCE_LABEL_CHARS = 120
SESSION_SEARCH_FILTER_CHARS = 180
SESSION_SEARCH_CHANNEL_LIMIT = 5
_SEARCH_SCOPE_FILTERS = (
    "title", "channel_id", "channel", "resolved_channels", "lang",
    "start_date", "end_date", "min_views", "max_views", "min_likes",
    "max_likes", "min_duration", "max_duration", "manual_subs",
    "category", "exclude_category", "country", "license", "min_matches",
    "sort", "order", "page", "pages", "candidate_pool", "channel_count",
)
_YOUTUBE_SEARCH_SCOPE_FILTERS = (
    "channel_id", "region", "lang", "safe_search", "caption", "category",
    "definition", "dimension", "duration", "embeddable", "license",
    "syndicated", "video_type", "event_type", "location",
    "location_radius", "topic_id", "paid_promotion",
)
_PROBE_PROVENANCE_DETAIL_STATUSES = {
    "broad_sampled",
    "deferred",
    "failed_closed",
}
_RESEARCH_STAGE_ALIASES = {
    "title_transcript": "title+transcript",
}


def _probe_provenance_status(status: str, data: Mapping[str, Any]) -> str:
    """Preserve bounded operational states hidden by event normalization."""
    detail = data.get("detail_status")
    if detail in _PROBE_PROVENANCE_DETAIL_STATUSES:
        return str(detail)
    return status


def _canonical_research_stage(value: object) -> str:
    stage = str(value or "")
    return _RESEARCH_STAGE_ALIASES.get(stage, stage)


def _compact_provenance_text(value: object, limit: int) -> str:
    """Return one bounded, single-line ledger label for a session summary."""
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _bounded_provenance_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Keep the most recent provenance rows and report what was omitted."""
    total = len(rows)
    visible = rows[-SESSION_PROVENANCE_ROW_LIMIT:]
    return {
        "total": total,
        "shown": len(visible),
        "omitted": total - len(visible),
        "rows": visible,
    }


def _compact_search_scope(data: Mapping[str, Any]) -> Dict[str, Any]:
    """Project recorded search scope without guessing absent legacy defaults.

    Flat ledger fields and typed result filters are both supported. Only known
    scope fields are copied; text and resolved-channel lists are bounded, with
    field names identifying any truncation so this is never an exact replay
    contract.
    """
    nested = data.get("effective_filters")
    source = dict(data)
    if isinstance(nested, dict):
        source.update(nested)
    filters: Dict[str, Any] = {}
    truncated = []

    def compact_text(value: str, field: str) -> str:
        if len(" ".join(value.split())) > SESSION_SEARCH_FILTER_CHARS:
            if field not in truncated:
                truncated.append(field)
        return _compact_provenance_text(value, SESSION_SEARCH_FILTER_CHARS)

    for field in _SEARCH_SCOPE_FILTERS:
        if field not in source:
            continue
        value = source[field]
        if field == "resolved_channels" and isinstance(value, list):
            channels = []
            for channel in value[:SESSION_SEARCH_CHANNEL_LIMIT]:
                if not isinstance(channel, dict):
                    continue
                item = {
                    key: compact_text(channel[key], field)
                    for key in ("id", "name")
                    if isinstance(channel.get(key), str)
                }
                if item:
                    channels.append(item)
            filters[field] = channels
            if len(value) > SESSION_SEARCH_CHANNEL_LIMIT and field not in truncated:
                truncated.append(field)
        elif value is None or isinstance(value, bool):
            filters[field] = value
        elif isinstance(value, str):
            filters[field] = compact_text(value, field)
        elif isinstance(value, int):
            # Real search limits are small integers; bound malformed legacy
            # values too, without converting a truncated number into a number.
            if value.bit_length() <= 64:
                filters[field] = value
            else:
                filters[field] = "[oversized integer omitted]"
                truncated.append(field)

    scope: Dict[str, Any] = {}
    if filters:
        scope["effective_filters"] = filters
    if truncated:
        scope["effective_filters_truncated"] = truncated
    effective_query = data.get("effective_query")
    if isinstance(effective_query, str):
        scope["effective_query"] = _compact_provenance_text(
            effective_query, SESSION_PROVENANCE_QUERY_CHARS
        )
        if len(" ".join(effective_query.split())) > SESSION_PROVENANCE_QUERY_CHARS:
            scope["effective_query_truncated"] = True
    return scope


def _compact_youtube_search_scope(data: Mapping[str, Any]) -> Dict[str, Any]:
    """Project a bounded direct-YouTube request without persisting secrets.

    New events carry ``request``/``coverage``/``enrichment`` objects while
    older ``yt-search`` events stored most fields flat. Supporting both keeps
    session replay useful across upgrades. The whitelist intentionally cannot
    include an API key.
    """
    request = data.get("request")
    if not isinstance(request, Mapping):
        request = {}
    filters = request.get("filters")
    if not isinstance(filters, Mapping):
        filters = data.get("filters")
    if not isinstance(filters, Mapping):
        filters = {}

    source = dict(data)
    source.update(filters)
    projected: Dict[str, Any] = {}
    truncated = []
    for field in _YOUTUBE_SEARCH_SCOPE_FILTERS:
        if field not in source:
            continue
        value = source[field]
        if value is None or isinstance(value, bool):
            projected[field] = value
        elif isinstance(value, str):
            compact = _compact_provenance_text(
                value, SESSION_SEARCH_FILTER_CHARS
            )
            projected[field] = compact
            if compact != " ".join(value.split()):
                truncated.append(field)
        elif isinstance(value, int) and value.bit_length() <= 64:
            projected[field] = value

    scope: Dict[str, Any] = {}
    if projected:
        scope["effective_filters"] = projected
    if truncated:
        scope["effective_filters_truncated"] = truncated
    return scope


def _optional_provenance_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _compact_provenance_constraints(value: object) -> Dict[str, Any]:
    """Retain only the documented probe scope fields, each size-bounded."""
    if not isinstance(value, dict):
        return {}
    compact: Dict[str, Any] = {}
    for key in ("title", "channel_id", "lang"):
        item = value.get(key)
        if item is None:
            compact[key] = None
        elif isinstance(item, str):
            compact[key] = _compact_provenance_text(
                item, SESSION_PROVENANCE_QUERY_CHARS
            )
    return compact


def _compact_selection_signals(value: object) -> Dict[str, Any]:
    """Keep the small ranking subset that explains a selected source."""
    if not isinstance(value, dict):
        return {}
    return {
        key: value[key]
        for key in (
            "token_coverage",
            "passage_coverage",
            "title_coverage",
            "density",
            "source_signal",
            "balanced_score",
        )
        if isinstance(value.get(key), (int, float))
        and not isinstance(value.get(key), bool)
    }


def _fold_research_provenance(
    events: list,
    research_search_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a bounded resumption view of compound-research provenance.

    Only known operational fields are copied. Transcript excerpts, errors,
    routes, paths, and claim payloads deliberately remain in their respective
    detailed stores rather than leaking into the compact session summary.
    """
    scout_runs: Dict[str, Dict[str, Any]] = {}
    probe_runs: Dict[str, Dict[str, Any]] = {}
    probe_queries: Dict[tuple, Dict[str, Any]] = {}
    source_rows: Dict[tuple, Dict[str, Any]] = {}
    manual_source_rows: Dict[str, Dict[str, Any]] = {}

    for order, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        kind = str(event.get("kind") or "")
        status = str(event.get("status") or "unknown")
        run_id = str(data.get("run_id") or "legacy")
        phase = str(data.get("phase") or "")

        if kind == "research_checkpoint" and phase in {"scout", "scout_gate"}:
            row = scout_runs.setdefault(run_id, {
                "run_id": _compact_provenance_text(
                    run_id, SESSION_PROVENANCE_LABEL_CHARS
                ),
                "query": "",
                "days": None,
                "max_results": None,
                "order": None,
                "channel_id": None,
                "request_channel_id": None,
                "candidates_found": 0,
                "gate_before": None,
                "gate_after": None,
                "status": ResultStatus.STARTED.value,
                "ts": event.get("ts"),
                "_order": order,
            })
            row["ts"] = event.get("ts") or row.get("ts")
            if phase == "scout":
                if isinstance(data.get("query"), str):
                    row["query"] = _compact_provenance_text(
                        data["query"], SESSION_PROVENANCE_QUERY_CHARS
                    )
                if data.get("days") is not None:
                    row["days"] = _optional_provenance_int(data.get("days"))
                if data.get("max_results") is not None:
                    row["max_results"] = _optional_provenance_int(
                        data.get("max_results")
                    )
                if isinstance(data.get("order"), str):
                    row["order"] = _compact_provenance_text(
                        data["order"], SESSION_PROVENANCE_LABEL_CHARS
                    )
                if isinstance(data.get("channel_id"), str):
                    row["channel_id"] = _compact_provenance_text(
                        data["channel_id"], SESSION_PROVENANCE_QUERY_CHARS
                    )
                if isinstance(data.get("request_channel_id"), str):
                    row["request_channel_id"] = _compact_provenance_text(
                        data["request_channel_id"],
                        SESSION_PROVENANCE_QUERY_CHARS,
                    )
                if data.get("results") is not None:
                    row["candidates_found"] = (
                        _optional_provenance_int(data.get("results")) or 0
                    )
                row["status"] = status
            else:
                row["gate_before"] = _optional_provenance_int(
                    data.get("candidates_before")
                )
                row["gate_after"] = _optional_provenance_int(
                    data.get("candidates_after")
                )

        if kind == "research_checkpoint" and phase == "probe":
            row = probe_runs.setdefault(run_id, {
                "run_id": _compact_provenance_text(
                    run_id, SESSION_PROVENANCE_LABEL_CHARS
                ),
                "status": ResultStatus.STARTED.value,
                "reason": None,
                "eligible_seeds": None,
                "terms": None,
                "queries": None,
                "planned": None,
                "deferred": None,
                "failures": None,
                "saved": None,
                "ts": event.get("ts"),
                "_order": order,
            })
            row["status"] = status
            row["ts"] = event.get("ts") or row.get("ts")
            row["_order"] = order
            if "reason" in data:
                reason = data.get("reason")
                row["reason"] = (
                    _compact_provenance_text(
                        reason, SESSION_PROVENANCE_LABEL_CHARS
                    )
                    if isinstance(reason, str) and reason
                    else None
                )
            for source_field, visible_field in (
                ("eligible_seeds", "eligible_seeds"),
                ("terms", "terms"),
                ("queries", "queries"),
                ("queries_planned", "planned"),
                ("queries_deferred", "deferred"),
                ("saved", "saved"),
            ):
                if source_field in data:
                    row[visible_field] = _optional_provenance_int(
                        data.get(source_field)
                    )
            if "failures" in data:
                row["failures"] = _optional_provenance_int(
                    data.get("failures")
                )
            elif "query_failed" in data and "download_failed" in data:
                query_failures = _optional_provenance_int(
                    data.get("query_failed")
                )
                download_failures = _optional_provenance_int(
                    data.get("download_failed")
                )
                row["failures"] = (
                    query_failures + download_failures
                    if query_failures is not None
                    and download_failures is not None
                    else None
                )

        query = data.get("query")
        is_probe_event = kind == "research_probe" or (
            kind == "research_checkpoint" and phase == "probe_search"
        )
        if is_probe_event and isinstance(query, str) and query:
            probe_status = _probe_provenance_status(status, data)
            probe_key = (run_id, query)
            row = probe_queries.setdefault(probe_key, {
                "run_id": _compact_provenance_text(
                    run_id, SESSION_PROVENANCE_LABEL_CHARS
                ),
                "index": None,
                "query": _compact_provenance_text(
                    query, SESSION_PROVENANCE_QUERY_CHARS
                ),
                "constraints": {},
                "co_windows": None,
                "source_support": None,
                "api_total": 0,
                "returned": 0,
                "scoped": 0,
                "status": probe_status,
                "ts": event.get("ts"),
                "_order": order,
            })
            row["status"] = probe_status
            row["ts"] = event.get("ts") or row.get("ts")
            for field in (
                "index",
                "co_windows",
                "source_support",
                "api_total",
                "returned",
                "scoped",
            ):
                if data.get(field) is not None:
                    row[field] = _optional_provenance_int(data.get(field))
            constraints = _compact_provenance_constraints(
                data.get("constraints")
            )
            if constraints:
                row["constraints"] = constraints

        if kind == "research_checkpoint" and phase in {
            "download_item",
            "probe_download",
        }:
            video_id = data.get("video_id")
            if not isinstance(video_id, str) or not video_id:
                continue
            source_key = (run_id, video_id)
            row = source_rows.setdefault(source_key, {
                "run_id": _compact_provenance_text(
                    run_id, SESSION_PROVENANCE_LABEL_CHARS
                ),
                "video_id": _compact_provenance_text(
                    video_id, SESSION_PROVENANCE_LABEL_CHARS
                ),
                "title": "",
                "channel": "",
                "origin_stage": "probe" if phase == "probe_download" else "",
                "probe_query": None,
                "probe_index": None,
                "selection_signals": {},
                "saved_at": None,
                "_saved": False,
                "_run_key": run_id,
                "_order": order,
            })
            for field in ("title", "channel"):
                if isinstance(data.get(field), str):
                    row[field] = _compact_provenance_text(
                        data[field], SESSION_PROVENANCE_LABEL_CHARS
                    )
            if isinstance(data.get("stage"), str):
                row["origin_stage"] = _compact_provenance_text(
                    _canonical_research_stage(data["stage"]),
                    SESSION_PROVENANCE_LABEL_CHARS,
                )
            probe_query = data.get("probe_query")
            if probe_query is None and phase == "probe_download":
                probe_query = data.get("query")
            if isinstance(probe_query, str) and probe_query:
                row["probe_query"] = _compact_provenance_text(
                    probe_query, SESSION_PROVENANCE_QUERY_CHARS
                )
            probe_index = data.get("probe_index")
            if probe_index is None and phase == "probe_download":
                probe_index = data.get("index")
            if probe_index is not None:
                row["probe_index"] = _optional_provenance_int(probe_index)
            signals = _compact_selection_signals(data.get("signals"))
            if signals:
                row["selection_signals"] = signals
            detail_status = (
                data.get("detail_status") or data.get("legacy_status")
            )
            if (
                status == ResultStatus.COMPLETED.value
                and detail_status == "saved"
            ):
                row["_saved"] = True
                row["saved_at"] = event.get("ts")

        if (
            kind == "transcript_save"
            and _STATUS_ALIASES.get(status, status)
            == ResultStatus.COMPLETED.value
        ):
            video_id = data.get("video_id")
            if isinstance(video_id, str) and video_id:
                row = manual_source_rows.setdefault(video_id, {
                    "run_id": None,
                    "video_id": _compact_provenance_text(
                        video_id, SESSION_PROVENANCE_LABEL_CHARS
                    ),
                    "title": "",
                    "channel": "",
                    "origin_stage": "manual",
                    "selection_signals": {},
                    "saved_at": event.get("ts"),
                    "_saved": True,
                    "_run_key": "",
                    "_order": order,
                })
                for field in ("title", "channel"):
                    if isinstance(data.get(field), str):
                        row[field] = _compact_provenance_text(
                            data[field], SESSION_PROVENANCE_LABEL_CHARS
                        )
                # One row per saved video. If legacy history contains more
                # than one successful save, the latest event is the most
                # useful bounded-resumption timestamp.
                row["saved_at"] = event.get("ts") or row.get("saved_at")
                row["_order"] = order

        if kind == "research" and isinstance(data.get("sources"), list):
            # Aggregate sources can backfill labels but cannot create rows:
            # that list may include transcripts saved by earlier runs.
            for source in data["sources"]:
                if not isinstance(source, dict):
                    continue
                video_id = source.get("video_id")
                if not isinstance(video_id, str):
                    continue
                row = source_rows.get((run_id, video_id))
                if row is None:
                    continue
                for field in ("title", "channel"):
                    if not row.get(field) and isinstance(source.get(field), str):
                        row[field] = _compact_provenance_text(
                            source[field], SESSION_PROVENANCE_LABEL_CHARS
                        )

    stage_queries = {
        (
            str(row.get("run_id") or "legacy"),
            _canonical_research_stage(row.get("stage")),
        ):
        str(row.get("query") or "")
        for row in research_search_rows
    }
    scout_rows = []
    for row in sorted(scout_runs.values(), key=lambda item: int(item["_order"])):
        visible = dict(row)
        visible.pop("_order", None)
        scout_rows.append(visible)
    probe_rows = []
    for row in sorted(probe_queries.values(), key=lambda item: int(item["_order"])):
        visible = dict(row)
        visible.pop("_order", None)
        probe_rows.append(visible)

    probe_run_rows = []
    for row in sorted(probe_runs.values(), key=lambda item: int(item["_order"])):
        visible = dict(row)
        visible.pop("_order", None)
        probe_run_rows.append(visible)

    saved_rows = []
    origins: Dict[str, int] = {}
    linked_video_ids = {
        video_id
        for (_, video_id), row in source_rows.items()
        if row.get("_saved")
    }
    provenance_sources = list(source_rows.values())
    provenance_sources.extend(
        row
        for video_id, row in manual_source_rows.items()
        if video_id not in linked_video_ids
    )
    for row in sorted(
        provenance_sources, key=lambda item: int(item["_order"])
    ):
        if not row.get("_saved"):
            continue
        visible = dict(row)
        visible.pop("_saved", None)
        visible.pop("_order", None)
        run_key = str(visible.pop("_run_key", "legacy"))
        stage = str(visible.get("origin_stage") or "unknown")
        origins[stage] = origins.get(stage, 0) + 1
        if stage == "probe":
            origin_query = visible.pop("probe_query", None)
        elif stage == "scout":
            origin_query = scout_runs.get(run_key, {}).get("query")
            visible.pop("probe_query", None)
            visible.pop("probe_index", None)
        elif stage == "manual":
            origin_query = None
            visible.pop("probe_query", None)
            visible.pop("probe_index", None)
        else:
            origin_query = stage_queries.get(
                (run_key, stage)
            )
            visible.pop("probe_query", None)
            visible.pop("probe_index", None)
        visible["origin_query"] = (
            _compact_provenance_text(
                origin_query, SESSION_PROVENANCE_QUERY_CHARS
            )
            if origin_query
            else None
        )
        visible["provenance_status"] = (
            "recorded"
            if origin_query
            else "query_not_recorded"
            if stage in {"probe", "manual"}
            else "origin_not_recorded"
        )
        if not visible.get("selection_signals"):
            visible.pop("selection_signals", None)
        saved_rows.append(visible)

    return {
        "scout_runs": _bounded_provenance_rows(scout_rows),
        "probe_runs": _bounded_provenance_rows(probe_run_rows),
        "probe_queries": _bounded_provenance_rows(probe_rows),
        "saved_sources": {
            **_bounded_provenance_rows(saved_rows),
            "origins": dict(sorted(origins.items())),
        },
        "limits": {
            "rows_per_section": SESSION_PROVENANCE_ROW_LIMIT,
            "query_chars": SESSION_PROVENANCE_QUERY_CHARS,
            "label_chars": SESSION_PROVENANCE_LABEL_CHARS,
        },
        "counting_note": (
            "rows are a bounded resumption view over research checkpoints; "
            "query_not_recorded marks probe or manual saves whose originating "
            "query was not persisted"
        ),
    }


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
    selected = current_session()
    name = (
        _normalize(selected) if selected
        else record.topic or datetime.now().strftime("%Y-%m-%d")
    )
    payload = redact_sensitive_value(record.to_dict())
    if selected:
        payload["data"]["session"] = name
    with open(sessions / "{}.jsonl".format(name), "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                payload,
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
        migration_name = current_session() or topic
        if migration_name:
            migrate_legacy_session(migration_name, data_dir)
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
        topic: Default ledger/topic identity. An invocation's explicit session
            overrides the ledger destination, preserving this topic metadata.
            Without either, use today's date file.
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

        migration_name = current_session() or topic
        if migration_name:
            if not current_session():
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
    youtube_search_rows = []
    youtube_query_seen = set()
    youtube_queries = []
    youtube_candidate_fetches = 0
    youtube_returned_results = 0
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

    def optional_int(value: object) -> Optional[int]:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return None

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
                **_compact_search_scope(data),
            })

        if kind == "yt-search":
            request = data.get("request")
            if not isinstance(request, dict):
                request = {}
            coverage = data.get("coverage")
            if not isinstance(coverage, dict):
                coverage = {}
            enrichment = data.get("enrichment")
            if not isinstance(enrichment, dict):
                enrichment = {}

            direct_query = request.get("query", query)
            if isinstance(direct_query, str) and direct_query:
                if direct_query not in youtube_query_seen:
                    youtube_query_seen.add(direct_query)
                    youtube_queries.append(direct_query)
            else:
                direct_query = ""

            fetched = as_int(
                coverage.get("candidates_fetched")
                if coverage.get("candidates_fetched") is not None
                else data.get("candidates_fetched")
                if data.get("candidates_fetched") is not None
                else data.get("results")
            )
            returned = as_int(
                coverage.get("returned")
                if coverage.get("returned") is not None
                else coverage.get("results_returned")
                if coverage.get("results_returned") is not None
                else data.get("results")
            )
            youtube_candidate_fetches += fetched
            youtube_returned_results += returned

            def request_value(name: str, fallback: object = None) -> object:
                value = request.get(name)
                return fallback if value is None else value

            row = {
                "ts": event.get("ts"),
                "status": status,
                "query": _compact_provenance_text(
                    direct_query, SESSION_PROVENANCE_QUERY_CHARS
                ),
                "requested_at": request_value(
                    "requested_at", data.get("requested_at")
                ),
                "published_after": request_value(
                    "published_after",
                    data.get("effective_published_after")
                    or data.get("published_after"),
                ),
                "published_before": request_value(
                    "published_before",
                    data.get("effective_published_before")
                    or data.get("published_before"),
                ),
                "days": request_value("days", data.get("days")),
                "order": request_value("order", data.get("order")),
                "max_results": request_value(
                    "max_results", data.get("max_results")
                ),
                "pages_fetched": as_int(
                    coverage.get("pages_fetched")
                    if coverage.get("pages_fetched") is not None
                    else data.get("pages_fetched") or 1
                ),
                "candidates_fetched": fetched,
                "returned": returned,
                # YouTube documents this total as approximate. Preserve an
                # unobserved value as ``None`` rather than inventing zero.
                "approximate_total": optional_int(
                    coverage.get("approximate_total")
                    if coverage.get("approximate_total") is not None
                    else data.get("approximate_total")
                ),
                "stopping_reason": coverage.get("stopping_reason")
                or data.get("stopping_reason"),
                "continuation_available": bool(
                    coverage.get("next_page_token")
                    or data.get("next_page_token")
                ),
                "partial": bool(
                    coverage.get("partial")
                    if coverage.get("partial") is not None
                    else data.get("partial")
                    or status == ResultStatus.PARTIAL.value
                ),
                "enrichment_status": enrichment.get("status")
                or data.get("enrichment_status"),
                **_compact_youtube_search_scope(data),
            }
            if (
                isinstance(direct_query, str)
                and row["query"] != " ".join(direct_query.split())
            ):
                row["query_truncated"] = True
            youtube_search_rows.append(row)

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
    research_provenance = _fold_research_provenance(
        events, staged_search_rows
    )
    visible_youtube_rows = youtube_search_rows[-SESSION_PROVENANCE_ROW_LIMIT:]

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
        "youtube_searches": {
            "events": len(youtube_search_rows),
            "shown": len(visible_youtube_rows),
            "omitted": len(youtube_search_rows) - len(visible_youtube_rows),
            "unique_queries": youtube_queries,
            "candidate_fetches": youtube_candidate_fetches,
            "returned_results": youtube_returned_results,
            "scope_rows": visible_youtube_rows,
            "counting_note": (
                "direct YouTube discovery is a separate search universe; "
                "candidate_fetches and returned_results are per-event totals "
                "and are never added to Filmot search totals"
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
        "research_provenance": research_provenance,
        "analysis": {"historical_comparisons_logged": comparisons},
        "claims": {
            "mutation_events": claim_mutations,
            "unique_claim_ids": sorted(claim_ids),
        },
    }
