"""Versioned command-result and durable-event contracts.

The CLI has three consumers for the same operation outcome:

* ``--raw`` JSON written for another process,
* the session ledger written for a later Filmot invocation, and
* human renderers that choose which fields to display.

Keeping those paths on one typed envelope prevents them from drifting into
three subtly different accounts of what happened.  Domain modules may use a
``TypedDict`` (or another mapping type) for ``data`` while the envelope keeps
status, warnings, and errors uniform.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from enum import Enum
from typing import (
    Any,
    Dict,
    Generic,
    List,
    Mapping,
    Optional,
    TypedDict,
    TypeVar,
    Union,
)

from .redaction import redact_sensitive_text, redact_sensitive_value

RESULT_SCHEMA = "filmot.result/v1"
EVENT_SCHEMA = "filmot.event/v1"
LEGACY_CLAIM_SCHEMA = "filmot.claim/v1"
CLAIM_SCHEMA = "filmot.claim/v2"
ECHO_ANALYSIS_SCHEMA = "filmot.echo-analysis/v1"

JSONScalar = Union[str, int, float, bool, None]
JSONValue = Union[
    JSONScalar,
    List["JSONValue"],
    Dict[str, "JSONValue"],
]


class ResultStatus(str, Enum):
    """Stable status vocabulary shared by command results and ledger events."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    EMPTY = "empty"
    SKIPPED = "skipped"
    STARTED = "started"


class SearchScopeData(TypedDict, total=False):
    """Scope accounting attached to a search result."""

    api_total: int
    candidates_fetched: int
    pages_fetched: int
    page: int
    partial: bool
    post_filter_count: int
    output_count: int
    max_hits: Optional[int]


class SearchResultData(TypedDict, total=False):
    """Normalized payload emitted by the ``search`` command."""

    query: str
    effective_query: str
    result: List[Dict[str, Any]]
    scope: SearchScopeData
    effective_filters: Dict[str, Any]
    totalresultcount: int
    results_returned: int
    duplicates_skipped: int
    page_error: str


class YouTubeRequestData(TypedDict, total=False):
    """Credential-free effective request sent to YouTube discovery."""

    query: str
    requested_at: str
    published_after: Optional[str]
    published_before: Optional[str]
    days: int
    order: str
    max_results: int
    max_pages: int
    page_token: Optional[str]
    result_budget: int
    page_budget: int
    initial_page_token: Optional[str]
    timeout: Dict[str, float]
    retries: int
    filters: Dict[str, Any]


class YouTubeCoverageData(TypedDict, total=False):
    """Token-pagination and quota-call accounting for direct discovery."""

    pages_fetched: int
    candidates_fetched: int
    unique_candidates: int
    unique_results: int
    returned: int
    approximate_total: Optional[int]
    next_page_token: Optional[str]
    stopping_reason: str
    search_calls: int
    detail_calls: int
    api_calls: int
    duplicates_skipped: int
    malformed_items_skipped: int
    page_info: Dict[str, Optional[int]]
    partial: bool


class YouTubeEnrichmentData(TypedDict, total=False):
    """Best-effort videos.list metadata observation state."""

    status: str
    requested: int
    requested_count: int
    returned: int
    matched_count: int
    missing_count: int
    api_calls: int
    batches_completed: int
    missing_video_ids: List[str]
    unprocessed_video_ids: List[str]
    observed_at: Optional[str]
    expires_at: Optional[str]
    error: Optional[Dict[str, Any]]
    partial: bool


class YouTubeSearchResultData(TypedDict, total=False):
    """Direct YouTube discovery outcome shared by CLI and ledger."""

    query: str
    videos: List[Dict[str, Any]]
    days: int
    max_results: int
    order: str
    filters: Dict[str, Any]
    request: YouTubeRequestData
    coverage: YouTubeCoverageData
    enrichment: YouTubeEnrichmentData
    transcript: bool
    transcript_query: Optional[str]
    show_description: bool


class YouTubeVideoDetailsResultData(TypedDict, total=False):
    """Exact-ID public metadata returned by ``yt-video``."""

    videos: List[Dict[str, Any]]
    id_outcomes: List[Dict[str, Any]]
    request: Dict[str, Any]
    coverage: Dict[str, Any]
    observed_at: Optional[str]
    expires_at: Optional[str]
    show_description: bool


class YouTubePlaylistResultData(TypedDict, total=False):
    """Bounded public-playlist inspection returned by ``yt-playlist``."""

    provider: str
    playlist: Optional[Dict[str, Any]]
    playlist_items: List[Dict[str, Any]]
    videos: List[Dict[str, Any]]
    video_id_outcomes: List[Dict[str, Any]]
    request: Dict[str, Any]
    coverage: Dict[str, Any]
    api_calls: Dict[str, Any]
    continuation: Dict[str, Any]
    observed_at: Optional[str]
    expires_at: Optional[str]
    show_description: bool


class YouTubePlaylistShelfResultData(TypedDict, total=False):
    """Bounded channel playlist shelf returned by ``yt-playlists``."""

    provider: str
    channel: Optional[Dict[str, Any]]
    playlists: List[Dict[str, Any]]
    request: Dict[str, Any]
    coverage: Dict[str, Any]
    api_calls: Dict[str, Any]
    continuation: Dict[str, Any]
    observed_at: Optional[str]
    expires_at: Optional[str]
    show_description: bool


class TranscriptResultData(TypedDict, total=False):
    """Normalized transcript outcome used by raw and human renderers."""

    video_id: str
    language: str
    is_generated: bool
    segments: List[Dict[str, Any]]
    full_text: str
    duration_seconds: float
    segment_count: int
    source: str
    route: str
    routes_tried: List[str]
    route_errors: List[Dict[str, Any]]


class VideoResultData(TypedDict, total=False):
    """Normalized metadata payload emitted by the ``video`` command."""

    video_ids: str
    videos: List[Dict[str, Any]]


class ChannelResultData(TypedDict, total=False):
    """Normalized channel lookup payload emitted by ``channels``."""

    query: str
    channels: List[Dict[str, Any]]


class ProxyResultData(TypedDict, total=False):
    """Proxy status, refresh, or bounded probe result."""

    source: str
    summary: Dict[str, Any]
    sessions: List[Dict[str, Any]]
    attempts: List[Dict[str, Any]]


class LibraryResultData(TypedDict, total=False):
    """Library command result without constraining command-specific rows."""

    topic: str
    query: str
    rows: List[Dict[str, Any]]
    summary: Dict[str, Any]
    output: str


class SessionResultData(TypedDict, total=False):
    """Session inventory or typed ledger replay payload."""

    name: str
    rows: List[Dict[str, Any]]
    events: List[Dict[str, Any]]
    summary: Dict[str, Any]


class ClaimEvidenceData(TypedDict, total=False):
    """One human-authored relationship between a claim and a source."""

    claim_id: str
    evidence_id: str
    id_method: str
    relation: str
    source: str
    source_kind: str
    deep_link: str
    video_id: str
    start_seconds: float
    locator: str
    excerpt: str
    note: str
    primary: Optional[bool]
    independence: str
    lineage_group: str
    title: str
    channel: str
    research_run_id: str
    created_at: str


class ClaimData(TypedDict, total=False):
    """Folded current state of one durable research claim."""

    claim_id: str
    id_method: str
    text: str
    topic: str
    created_at: str
    verdict: str
    confidence: str
    assessment_note: str
    assessment_id: str
    assessed_at: str
    evidence: List[ClaimEvidenceData]
    summary: Dict[str, Any]


class ClaimResultData(TypedDict, total=False):
    """Typed payload emitted by the top-level ``claims`` commands."""

    claim_schema: str
    topic: str
    claim_id: str
    created: bool
    rows: List[ClaimData]
    summary: Dict[str, Any]


class EchoAnalysisResultData(TypedDict, total=False):
    """Deterministic full-transcript echo-analysis payload."""

    analysis_schema: str
    topic: str
    rows: List[Dict[str, Any]]
    clusters: List[Dict[str, Any]]
    summary: Dict[str, Any]
    method: Dict[str, Any]
    artifact: str
    artifact_hash: str


class ResearchResultData(TypedDict, total=False):
    """Final staged-research accounting used by ledger and renderer."""

    run_id: str
    session: str
    topic: str
    query: str
    phase: str
    scout: int
    filmot_total: int
    fallback_stage: Optional[str]
    selected: int
    saved: int
    skipped: int
    failed: int
    deduped: int
    probe_saved: int
    probe_download_failed: int
    probe_query_failed: int
    chars: int
    sources: List[Dict[str, Any]]
    routing_plan: Dict[str, Any]


PayloadT = TypeVar("PayloadT")


def _status_value(status: Union[ResultStatus, str]) -> str:
    value = status.value if isinstance(status, ResultStatus) else str(status)
    allowed = {item.value for item in ResultStatus}
    if value not in allowed:
        raise ValueError(
            "Unsupported result status {!r}; expected one of {}".format(
                value,
                ", ".join(sorted(allowed)),
            )
        )
    return value


def _json_value(value: Any) -> Any:
    """Return a JSON-compatible value while preserving Unicode text."""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            str(key): _json_value(item)
            for key, item in asdict(value).items()
        }
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if isinstance(value, Exception):
        return redact_sensitive_text(value)
    return value


@dataclass(frozen=True)
class ErrorDetail:
    """Structured failure detail safe for raw output and ledger persistence."""

    type: str
    message: str
    stage: Optional[str] = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "message", redact_sensitive_text(self.message))
        object.__setattr__(
            self,
            "details",
            redact_sensitive_value(dict(self.details)),
        )

    @classmethod
    def from_exception(
        cls,
        error: BaseException,
        *,
        stage: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> "ErrorDetail":
        return cls(
            type=type(error).__name__,
            message=redact_sensitive_text(error),
            stage=stage,
            details=dict(details or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "type": self.type,
            "message": self.message,
        }
        if self.stage:
            payload["stage"] = self.stage
        if self.details:
            payload["details"] = _json_value(self.details)
        return payload


@dataclass(frozen=True)
class CommandResult(Generic[PayloadT]):
    """One versioned command outcome consumed by every presentation path."""

    command: str
    status: Union[ResultStatus, str]
    data: PayloadT
    errors: List[ErrorDetail] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    schema: str = RESULT_SCHEMA

    def __post_init__(self) -> None:
        _status_value(self.status)
        if self.schema != RESULT_SCHEMA:
            raise ValueError("Unsupported command result schema: {}".format(self.schema))

    @property
    def status_value(self) -> str:
        return _status_value(self.status)

    @property
    def ok(self) -> bool:
        return self.status_value not in {
            ResultStatus.FAILED.value,
            ResultStatus.INTERRUPTED.value,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "command": self.command,
            "status": self.status_value,
            "data": _json_value(self.data),
            "errors": [error.to_dict() for error in self.errors],
            "warnings": list(self.warnings),
        }

    def to_raw_dict(self) -> Dict[str, Any]:
        """Project the outcome to the CLI's backward-compatible raw shape.

        Mapping payloads retain their domain keys (notably search's top-level
        ``result`` list) so existing pipes keep working.  Version/status/error
        metadata lives under ``_filmot`` to avoid colliding with fields owned
        by an upstream API.  Non-mapping payloads use the canonical envelope.
        """
        if not isinstance(self.data, Mapping):
            return self.to_dict()
        payload = {
            str(key): _json_value(value)
            for key, value in self.data.items()
        }
        payload["_filmot"] = {
            "schema": self.schema,
            "command": self.command,
            "status": self.status_value,
            "errors": [error.to_dict() for error in self.errors],
            "warnings": list(self.warnings),
        }
        return payload

    def to_event(
        self,
        kind: str,
        *,
        topic: Optional[str] = None,
        data: Optional[Mapping[str, Any]] = None,
        timestamp: Optional[str] = None,
    ) -> "EventRecord":
        """Create a compact durable event from this result.

        ``data`` may provide a ledger-sized summary instead of persisting a
        complete transcript or search response.  The surrounding contract is
        identical either way.
        """
        event_data: Any = self.data if data is None else dict(data)
        return EventRecord(
            kind=kind,
            command=self.command,
            status=self.status_value,
            data=event_data,
            errors=list(self.errors),
            warnings=list(self.warnings),
            topic=topic,
            ts=timestamp or datetime.now().isoformat(timespec="seconds"),
        )

    @classmethod
    def completed(
        cls,
        command: str,
        data: PayloadT,
        *,
        warnings: Optional[List[str]] = None,
    ) -> "CommandResult[PayloadT]":
        return cls(
            command=command,
            status=ResultStatus.COMPLETED,
            data=data,
            warnings=list(warnings or []),
        )

    @classmethod
    def failed(
        cls,
        command: str,
        data: PayloadT,
        error: ErrorDetail,
        *,
        warnings: Optional[List[str]] = None,
    ) -> "CommandResult[PayloadT]":
        return cls(
            command=command,
            status=ResultStatus.FAILED,
            data=data,
            errors=[error],
            warnings=list(warnings or []),
        )


@dataclass(frozen=True)
class EventRecord:
    """Versioned JSONL record sharing the command-result contract."""

    kind: str
    command: str
    status: Union[ResultStatus, str]
    data: Any
    ts: str
    errors: List[ErrorDetail] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    topic: Optional[str] = None
    schema: str = EVENT_SCHEMA

    def __post_init__(self) -> None:
        _status_value(self.status)
        if self.schema != EVENT_SCHEMA:
            raise ValueError("Unsupported event schema: {}".format(self.schema))

    @property
    def status_value(self) -> str:
        return _status_value(self.status)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "schema": self.schema,
            "ts": self.ts,
            "kind": self.kind,
            "command": self.command,
            "status": self.status_value,
            "data": _json_value(self.data),
            "errors": [error.to_dict() for error in self.errors],
            "warnings": list(self.warnings),
        }
        if self.topic:
            payload["topic"] = self.topic
        return payload


def result_from_dict(payload: Mapping[str, Any]) -> CommandResult[Dict[str, Any]]:
    """Parse a canonical or backward-compatible v1 raw result."""
    metadata = payload.get("_filmot")
    if isinstance(metadata, Mapping) and metadata.get("schema") == RESULT_SCHEMA:
        data = {
            str(key): value
            for key, value in payload.items()
            if key != "_filmot"
        }
        source = metadata
    elif payload.get("schema") == RESULT_SCHEMA:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("Result envelope data must be an object")
        source = payload
    else:
        raise ValueError("Input is not a {} result".format(RESULT_SCHEMA))

    errors = []
    for item in source.get("errors") or []:
        if not isinstance(item, Mapping):
            continue
        errors.append(
            ErrorDetail(
                type=str(item.get("type") or "Error"),
                message=str(item.get("message") or ""),
                stage=(
                    str(item["stage"])
                    if item.get("stage") is not None
                    else None
                ),
                details=(
                    dict(item.get("details") or {})
                    if isinstance(item.get("details"), Mapping)
                    else {}
                ),
            )
        )
    return CommandResult(
        command=str(source.get("command") or ""),
        status=str(source.get("status") or ResultStatus.FAILED.value),
        data=dict(data),
        errors=errors,
        warnings=[str(item) for item in source.get("warnings") or []],
    )


def normalize_event_dict(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize a v1 or pre-v1 ledger record to the event envelope.

    Legacy files remain readable without rewriting them on inspection.  New
    appends always use ``filmot.event/v1``.
    """
    if payload.get("schema") == EVENT_SCHEMA:
        # The documented v1 envelope allowed these fields to be omitted.
        # Restore the defaults before strict validation so historical files
        # stay readable instead of silently dropping out of replay.
        payload = dict(payload)
        payload.setdefault("errors", [])
        payload.setdefault("warnings", [])
        payload.setdefault("command", str(payload.get("kind") or "unknown"))
        payload.setdefault("status", ResultStatus.COMPLETED.value)
        required_text = ("ts", "kind", "command")
        for field_name in required_text:
            if not isinstance(payload.get(field_name), str) or not payload[field_name]:
                raise ValueError(
                    "Event envelope requires non-empty '{}'".format(field_name)
                )
        _status_value(payload.get("status"))
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise ValueError("Event envelope data must be an object")
        errors = payload.get("errors")
        if not isinstance(errors, list) or not all(
            isinstance(item, Mapping) for item in errors
        ):
            raise ValueError("Event envelope errors must be a list of objects")
        for error in errors:
            if not isinstance(error.get("type"), str) or not error["type"]:
                raise ValueError("Event error requires a non-empty type")
            if not isinstance(error.get("message"), str):
                raise ValueError("Event error requires a message string")
            if error.get("stage") is not None and not isinstance(
                error.get("stage"), str
            ):
                raise ValueError("Event error stage must be a string or null")
            if error.get("details") is not None and not isinstance(
                error.get("details"), Mapping
            ):
                raise ValueError("Event error details must be an object")
        warnings = payload.get("warnings")
        if not isinstance(warnings, list) or not all(
            isinstance(item, str) for item in warnings
        ):
            raise ValueError("Event envelope warnings must be a list of strings")
        topic = payload.get("topic")
        if topic is not None and not isinstance(topic, str):
            raise ValueError("Event envelope topic must be a string or null")
        normalized = dict(payload)
        normalized["data"] = dict(data)
        return normalized

    if payload.get("schema") is not None:
        raise ValueError(
            "Unsupported event schema: {}".format(payload.get("schema"))
        )

    ts = str(payload.get("ts") or "")
    kind = str(payload.get("kind") or "unknown")
    topic = payload.get("topic")
    status = str(payload.get("status") or ResultStatus.COMPLETED.value)
    if status not in {item.value for item in ResultStatus}:
        # Preserve historical detail without expanding the stable vocabulary.
        legacy_status = status
        status = (
            ResultStatus.FAILED.value
            if legacy_status in {"error", "failure"}
            else ResultStatus.COMPLETED.value
        )
    else:
        legacy_status = None

    reserved = {"ts", "kind", "topic", "status", "schema"}
    data = {
        str(key): _json_value(value)
        for key, value in payload.items()
        if key not in reserved
    }
    if legacy_status is not None:
        data["legacy_status"] = legacy_status

    return EventRecord(
        kind=kind,
        command=kind.replace("_", "-"),
        status=status,
        data=data,
        topic=str(topic) if topic is not None else None,
        ts=ts,
    ).to_dict()
