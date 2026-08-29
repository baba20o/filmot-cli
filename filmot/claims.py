"""Strict, append-only storage for human-authored research claims.

The ordinary session ledger is intentionally best-effort: losing an activity
event must not break a search or transcript download. Claims and citations are
different. They are user-authored research data, so persistence failures are
surfaced and every update is retained as an immutable event.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, urlparse

from .library import normalize_topic_name
from .paths import (
    exclusive_file_guard,
    project_data_dir,
    publish_file_exclusive,
)
from .schemas import (
    CLAIM_SCHEMA,
    LEGACY_CLAIM_SCHEMA,
    ClaimData,
    ClaimEvidenceData,
)


CLAIM_RELATIONS = (
    "supports",
    "contradicts",
    "qualifies",
    "context",
    "origin",
    "mentions",
)
CLAIM_VERDICTS = ("open", "supported", "contradicted", "mixed")
CLAIM_CONFIDENCE = ("unknown", "low", "medium", "high")
SOURCE_INDEPENDENCE = ("unknown", "independent", "echo")
SOURCE_KINDS = ("video", "paper", "patent", "official", "web", "local", "other")
CLAIM_ID_METHOD = "utf8-ascii-whitespace/v1"
LEGACY_STRUCTURED_ID_METHOD = "canonical-json-array/v1"
STRUCTURED_ID_METHOD = "canonical-json-array/v2"

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")
_YOUTUBE_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
_ASCII_WHITESPACE_PATTERN = re.compile(r"[ \t\r\n\f\v]+")


class ClaimStoreError(RuntimeError):
    """Raised when durable claim data is invalid or cannot be persisted."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _display_text(value: str) -> str:
    """Fold only ASCII whitespace, preserving exact Unicode code points/case."""
    return _ASCII_WHITESPACE_PATTERN.sub(" ", str(value or "")).strip(" ")


def _canonical_text(value: str) -> str:
    return _display_text(value)


def _digest(prefix: str, *parts: object, length: int = 16) -> str:
    encoded = json.dumps(
        list(parts),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "{}-{}".format(prefix, hashlib.sha256(encoded).hexdigest()[:length])


def _legacy_digest(prefix: str, *parts: object, length: int = 16) -> str:
    """Reproduce v1's ambiguous delimiter digest for read compatibility only."""
    encoded = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return "{}-{}".format(prefix, hashlib.sha256(encoded).hexdigest()[:length])


def default_claim_id(text: str) -> str:
    """Return a runtime-independent ID for an exact claim statement."""
    normalized = _canonical_text(text)
    if not normalized:
        raise ValueError("Claim text cannot be empty")
    return "c-{}".format(
        hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    )


def validate_youtube_video_id(value: object) -> str:
    """Return an exact YouTube video ID or reject ambiguous locator text."""
    if not isinstance(value, str) or not _YOUTUBE_VIDEO_ID_PATTERN.fullmatch(value):
        raise ValueError(
            "Evidence video ID must be exactly 11 letters, numbers, underscores, "
            "or hyphens; pass the ID, not a YouTube URL"
        )
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError("Non-standard JSON constant is not allowed: {}".format(value))


def _source_matches_video(source: str, video_id: str) -> bool:
    """Whether a source value identifies the same YouTube video."""
    value = str(source or "").strip()
    if value == video_id:
        return True
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    if host in {"youtu.be", "www.youtu.be"}:
        return parsed.path.strip("/").split("/", 1)[0] == video_id
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        return False
    if parsed.path.rstrip("/") == "/watch":
        return parse_qs(parsed.query).get("v", [""])[0] == video_id
    parts = [part for part in parsed.path.split("/") if part]
    return len(parts) >= 2 and parts[0] in {"embed", "live", "shorts"} and (
        parts[1] == video_id
    )


def _video_deep_link(video_id: str, start_seconds: Optional[float]) -> str:
    link = "https://youtube.com/watch?v={}".format(video_id)
    if start_seconds is not None:
        link += "&t={}s".format(int(start_seconds))
    return link


def _evidence_id(
    data: Dict[str, Any],
    *,
    legacy: bool = False,
    include_provenance: bool = True,
) -> str:
    digest = _legacy_digest if legacy else _digest
    parts = [
        data.get("claim_id") or "",
        data.get("relation") or "",
        data.get("source") or "",
        data.get("source_kind") or "",
        data.get("video_id") or "",
        (
            ""
            if data.get("start_seconds") is None
            else float(data["start_seconds"])
        ),
        data.get("locator") or "",
        str(data.get("excerpt") or "").strip(),
        str(data.get("note") or "").strip(),
        data.get("primary"),
        data.get("independence") or "unknown",
        data.get("lineage_group") or "",
    ]
    if include_provenance:
        parts.extend([
            data.get("title") or "",
            data.get("channel") or "",
            data.get("research_run_id") or "",
        ])
    return digest("e", *parts)


class ClaimStore:
    """Read and append immutable claim, evidence, and assessment events."""

    def __init__(self, data_dir: Optional[Union[str, Path]] = None):
        self.data_dir = project_data_dir(data_dir)
        self.claims_dir = self.data_dir / "claims"

    def _topic_dir(self, topic: str, *, create: bool = False) -> Path:
        slug = normalize_topic_name(topic, fallback="claims")
        path = self.claims_dir / slug
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    @contextmanager
    def _mutation_guard(self, topic: str):
        """Serialize a topic's complete read-check-append transaction."""
        try:
            directory = self._topic_dir(topic, create=True)
            with exclusive_file_guard(directory / ".filmot-claims.guard"):
                yield
        except OSError as error:
            raise ClaimStoreError(
                "Cannot lock claim topic for mutation: {}".format(error)
            ) from error

    def _read_events(self, topic: str) -> List[Dict[str, Any]]:
        path = self._topic_dir(topic)
        if not path.exists():
            return []
        legacy_events: List[Dict[str, Any]] = []
        current_events: List[Dict[str, Any]] = []
        for event_path in sorted(path.glob("*.json")):
            try:
                with event_path.open("r", encoding="utf-8") as handle:
                    event = json.load(
                        handle,
                        parse_constant=_reject_json_constant,
                    )
            except (OSError, json.JSONDecodeError, ValueError) as error:
                raise ClaimStoreError(
                    "Cannot read claim event {}: {}".format(event_path, error)
                ) from error
            if not isinstance(event, dict) or event.get("schema") not in {
                LEGACY_CLAIM_SCHEMA,
                CLAIM_SCHEMA,
            }:
                raise ClaimStoreError(
                    "Unsupported claim event in {}".format(event_path)
                )
            legacy = event.get("schema") == LEGACY_CLAIM_SCHEMA
            try:
                self._validate_event(
                    event,
                    normalize_topic_name(topic, fallback="claims"),
                    legacy=legacy,
                )
            except (TypeError, ValueError) as error:
                raise ClaimStoreError(
                    "Invalid claim event {}: {}".format(event_path, error)
                ) from error
            (legacy_events if legacy else current_events).append(event)

        legacy_has_sequence = ["sequence" in item for item in legacy_events]
        if any(legacy_has_sequence) and not all(legacy_has_sequence):
            raise ClaimStoreError(
                "Legacy claim event sequence is only partly present"
            )
        if legacy_events and all(legacy_has_sequence):
            legacy_events.sort(
                key=lambda item: (
                    int(item["sequence"]),
                    str(item.get("event_id", "")),
                )
            )
            legacy_sequences = [int(item["sequence"]) for item in legacy_events]
            if legacy_sequences != list(range(1, len(legacy_events) + 1)):
                raise ClaimStoreError(
                    "Legacy claim event sequence is duplicated or non-contiguous"
                )
        else:
            legacy_events.sort(
                key=lambda item: (
                    str(item.get("ts", "")),
                    str(item.get("event_id", "")),
                )
            )
            for index, event in enumerate(legacy_events, 1):
                event["sequence"] = index

        current_events.sort(
            key=lambda item: (
                int(item["sequence"]),
                str(item.get("event_id", "")),
            )
        )
        current_sequences = [int(item["sequence"]) for item in current_events]
        expected_current = list(
            range(len(legacy_events) + 1, len(legacy_events) + len(current_events) + 1)
        )
        if current_sequences != expected_current:
            raise ClaimStoreError(
                "Claim event sequence is missing, duplicated, or non-contiguous"
            )
        return legacy_events + current_events

    @staticmethod
    def _validate_event(
        event: Dict[str, Any],
        expected_topic: str,
        *,
        legacy: bool = False,
    ) -> None:
        if not legacy:
            unknown_event_fields = set(event) - {
                "schema",
                "event_id",
                "event_type",
                "sequence",
                "ts",
                "topic",
                "data",
            }
            if unknown_event_fields:
                raise ValueError("Unknown claim event fields")
        for field_name in ("event_id", "event_type", "ts", "topic"):
            if not isinstance(event.get(field_name), str) or not event[field_name]:
                raise ValueError("Missing non-empty {}".format(field_name))
        sequence = event.get("sequence")
        if sequence is not None and (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 1
        ):
            raise ValueError("Claim event sequence must be a positive integer")
        if not legacy and sequence is None:
            raise ValueError("Claim event sequence must be a positive integer")
        if event["topic"] != expected_topic:
            raise ValueError("Event topic does not match its claim directory")
        event_type = event["event_type"]
        if event_type not in {"claim", "evidence", "assessment"}:
            raise ValueError("Unknown claim event type: {}".format(event_type))
        data = event.get("data")
        if not isinstance(data, dict):
            raise ValueError("Claim event data must be an object")
        claim_id = data.get("claim_id")
        if not isinstance(claim_id, str) or not _ID_PATTERN.match(claim_id):
            raise ValueError("Invalid or missing claim_id")

        if event_type == "claim":
            if not legacy and set(data) != {"claim_id", "id_method", "text"}:
                raise ValueError("Unknown claim declaration fields")
            text = data.get("text")
            if not isinstance(text, str) or not _display_text(text):
                raise ValueError("Claim declaration has no text")
            if text != _display_text(text):
                raise ValueError("Claim declaration text is not canonical")
            method = data.get("id_method")
            allowed_methods = {"explicit", CLAIM_ID_METHOD}
            if legacy:
                allowed_methods.add(None)
            if method not in allowed_methods:
                raise ValueError("Unsupported claim ID method: {}".format(method))
            if method == CLAIM_ID_METHOD and default_claim_id(text) != claim_id:
                raise ValueError("Derived claim ID does not match claim text")
            return

        if event_type == "assessment":
            method = data.get("id_method")
            if method != STRUCTURED_ID_METHOD and not (
                legacy
                and method in {None, LEGACY_STRUCTURED_ID_METHOD}
            ):
                raise ValueError("Unsupported assessment ID method")
            if not legacy and set(data) - {
                "claim_id",
                "assessment_id",
                "id_method",
                "verdict",
                "confidence",
                "note",
                "supersedes",
            }:
                raise ValueError("Unknown assessment fields")
            if data.get("verdict") not in CLAIM_VERDICTS:
                raise ValueError("Unsupported claim verdict")
            if data.get("confidence") not in CLAIM_CONFIDENCE:
                raise ValueError("Unsupported claim confidence")
            if not isinstance(data.get("assessment_id"), str):
                raise ValueError("Assessment has no assessment_id")
            note = data.get("note")
            if not isinstance(note, str):
                raise ValueError("Assessment note must be text")
            supersedes = data.get("supersedes")
            if supersedes is not None and not isinstance(supersedes, str):
                raise ValueError("Assessment supersedes must be text")
            digest = _legacy_digest if legacy and method is None else _digest
            expected_id = digest(
                "a",
                claim_id,
                data.get("supersedes") or "",
                data["verdict"],
                data["confidence"],
                note.strip(),
            )
            if data["assessment_id"] != expected_id:
                raise ValueError("Assessment ID does not match its content")
            return

        if data.get("relation") not in CLAIM_RELATIONS:
            raise ValueError("Unsupported evidence relation")
        method = data.get("id_method")
        if method != STRUCTURED_ID_METHOD and not (
            legacy
            and method in {None, LEGACY_STRUCTURED_ID_METHOD}
        ):
            raise ValueError("Unsupported evidence ID method")
        if not legacy and set(data) - {
            "claim_id",
            "evidence_id",
            "id_method",
            "relation",
            "source",
            "source_kind",
            "deep_link",
            "video_id",
            "start_seconds",
            "locator",
            "excerpt",
            "note",
            "primary",
            "independence",
            "lineage_group",
            "title",
            "channel",
            "research_run_id",
        }:
            raise ValueError("Unknown evidence fields")
        if data.get("source_kind") not in SOURCE_KINDS:
            raise ValueError("Unsupported evidence source kind")
        independence = data.get("independence")
        if independence not in SOURCE_INDEPENDENCE:
            raise ValueError("Unsupported evidence independence")
        for field_name in (
            "locator",
            "excerpt",
            "note",
            "lineage_group",
            "title",
            "channel",
            "research_run_id",
        ):
            value = data.get(field_name)
            if value is not None and not isinstance(value, str):
                raise ValueError(
                    "Evidence {} must be text or null".format(field_name)
                )
        if independence == "echo" and not str(data.get("lineage_group") or "").strip():
            raise ValueError("Echo evidence has no lineage group")
        source = data.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("Evidence has no source")
        if not isinstance(data.get("evidence_id"), str):
            raise ValueError("Evidence has no evidence_id")
        primary = data.get("primary")
        if primary is not None and not isinstance(primary, bool):
            raise ValueError("Evidence primary value must be boolean or null")

        video_id = data.get("video_id")
        if video_id is not None and (
            not isinstance(video_id, str) or not video_id
        ):
            raise ValueError("Evidence video_id must be non-empty text")
        if video_id is not None and not legacy:
            validate_youtube_video_id(video_id)
        start_seconds = data.get("start_seconds")
        if start_seconds is not None:
            if isinstance(start_seconds, bool) or not isinstance(
                start_seconds, (int, float)
            ):
                raise ValueError("Evidence timestamp must be numeric")
            if not math.isfinite(float(start_seconds)) or start_seconds < 0:
                raise ValueError("Evidence timestamp must be finite and non-negative")
            if (
                not legacy
                and start_seconds == 0
                and math.copysign(1.0, float(start_seconds)) < 0
            ):
                raise ValueError("Evidence timestamp uses non-canonical negative zero")
            if not video_id:
                raise ValueError("Evidence timestamp requires a video ID")
        if video_id:
            if data.get("source_kind") != "video":
                raise ValueError("Video evidence has a non-video source kind")
            if not _source_matches_video(source, video_id):
                raise ValueError("Evidence source does not match its video ID")
            if data.get("deep_link") != _video_deep_link(video_id, start_seconds):
                raise ValueError("Evidence deep link does not match its locator")
        elif data.get("deep_link") is not None:
            raise ValueError("Non-video evidence cannot have a deep link")
        if data["evidence_id"] != _evidence_id(
            data,
            legacy=legacy and method is None,
            include_provenance=method == STRUCTURED_ID_METHOD,
        ):
            raise ValueError("Evidence ID does not match its content")

    def _append(self, topic: str, event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Persist one event atomically and raise on any failure."""
        topic_slug = normalize_topic_name(topic, fallback="claims")
        sequence = len(self._read_events(topic)) + 1
        event_id = "ce-{}".format(uuid.uuid4().hex)
        timestamp = _now()
        event = {
            "schema": CLAIM_SCHEMA,
            "event_id": event_id,
            "event_type": event_type,
            "sequence": sequence,
            "ts": timestamp,
            "topic": topic_slug,
            "data": data,
        }
        try:
            self._validate_event(event, topic_slug)
        except (TypeError, ValueError) as error:
            raise ClaimStoreError(
                "Refusing to persist invalid claim event: {}".format(error)
            ) from error
        directory = self._topic_dir(topic, create=True)
        destination = directory / "{:020d}-{}.json".format(sequence, event_id)
        temporary = directory / ".{}.{}.tmp".format(event_id, uuid.uuid4().hex)
        try:
            descriptor = os.open(
                str(temporary),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(event, handle, indent=2, ensure_ascii=False, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            if not publish_file_exclusive(temporary, destination):
                raise ClaimStoreError(
                    "Claim event collision at {}".format(destination)
                )
        except Exception as error:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass
            if isinstance(error, ClaimStoreError):
                raise
            raise ClaimStoreError("Cannot persist claim event: {}".format(error)) from error
        return event

    def list_claims(self, topic: str) -> List[ClaimData]:
        """Fold immutable topic events into the current claim register."""
        claims: Dict[str, ClaimData] = {}

        for event in self._read_events(topic):
            event_type = str(event.get("event_type") or "")
            data = event.get("data")
            if not isinstance(data, dict):
                raise ClaimStoreError(
                    "Claim event {} has no object data".format(event.get("event_id"))
                )
            claim_id = str(data.get("claim_id") or "")
            if event_type == "claim":
                text = _display_text(str(data.get("text") or ""))
                if not claim_id or not text:
                    raise ClaimStoreError("Malformed claim declaration")
                existing = claims.get(claim_id)
                if existing and _canonical_text(existing["text"]) != _canonical_text(text):
                    raise ClaimStoreError(
                        "Claim ID {} refers to different statements".format(claim_id)
                    )
                if not existing:
                    claims[claim_id] = {
                        "claim_id": claim_id,
                        "id_method": str(
                            data.get("id_method") or "legacy/unspecified"
                        ),
                        "text": text,
                        "topic": str(event.get("topic") or ""),
                        "created_at": str(event.get("ts") or ""),
                        "verdict": "open",
                        "confidence": "unknown",
                        "assessment_note": "",
                        "evidence": [],
                        "summary": {},
                    }
                continue

            claim = claims.get(claim_id)
            if claim is None:
                raise ClaimStoreError(
                    "{} event references unknown claim {}".format(event_type, claim_id)
                )

            if event_type == "evidence":
                evidence = dict(data)
                evidence.setdefault("created_at", str(event.get("ts") or ""))
                known_ids = {
                    item.get("evidence_id") for item in claim.get("evidence", [])
                }
                if evidence.get("evidence_id") not in known_ids:
                    claim.setdefault("evidence", []).append(evidence)  # type: ignore[arg-type]
            elif event_type == "assessment":
                current_assessment = claim.get("assessment_id")
                supplied_supersedes = data.get("supersedes")
                if current_assessment:
                    if supplied_supersedes != current_assessment:
                        raise ClaimStoreError(
                            "Assessment {} does not supersede the current "
                            "assessment for {}".format(
                                data.get("assessment_id"),
                                claim_id,
                            )
                        )
                elif supplied_supersedes is not None:
                    raise ClaimStoreError(
                        "First assessment for {} cannot supersede another "
                        "assessment".format(claim_id)
                    )
                claim["verdict"] = str(data.get("verdict") or "open")
                claim["confidence"] = str(data.get("confidence") or "unknown")
                claim["assessment_note"] = str(data.get("note") or "")
                claim["assessment_id"] = str(data.get("assessment_id") or "")
                claim["assessed_at"] = str(event.get("ts") or "")
            else:
                raise ClaimStoreError("Unknown claim event type: {}".format(event_type))

        for claim in claims.values():
            evidence_rows = claim.get("evidence") or []
            relations: Dict[str, int] = {relation: 0 for relation in CLAIM_RELATIONS}
            source_keys = set()
            independent_groups = set()
            echo_groups = set()
            primary_sources = set()
            for evidence in evidence_rows:
                relation = str(evidence.get("relation") or "")
                if relation in relations:
                    relations[relation] += 1
                source_key = str(
                    evidence.get("video_id") or evidence.get("source") or ""
                )
                if source_key:
                    source_keys.add(source_key)
                if evidence.get("primary") is True and source_key:
                    primary_sources.add(source_key)
                independence = evidence.get("independence")
                lineage = str(evidence.get("lineage_group") or "")
                if independence == "independent":
                    independent_groups.add(lineage or source_key)
                elif independence == "echo" and lineage:
                    echo_groups.add(lineage)
            claim["summary"] = {
                "evidence": len(evidence_rows),
                "sources": len(source_keys),
                "primary_sources": len(primary_sources),
                "independent_groups": len(independent_groups),
                "echo_groups": len(echo_groups),
                "relations": relations,
            }

        return sorted(
            claims.values(),
            key=lambda item: (str(item.get("created_at", "")), str(item["claim_id"])),
        )

    def get_claim(self, topic: str, claim_id: str) -> Optional[ClaimData]:
        for claim in self.list_claims(topic):
            if claim["claim_id"] == claim_id:
                return claim
        return None

    def add_claim(
        self,
        topic: str,
        text: str,
        *,
        claim_id: Optional[str] = None,
    ) -> Tuple[ClaimData, bool]:
        clean_text = _display_text(text)
        if not clean_text:
            raise ValueError("Claim text cannot be empty")
        resolved_id = claim_id or default_claim_id(text)
        if not _ID_PATTERN.match(resolved_id):
            raise ValueError(
                "Claim ID must be 2-64 letters, numbers, dots, underscores, or hyphens"
            )
        with self._mutation_guard(topic):
            existing = self.get_claim(topic, resolved_id)
            if existing:
                if _canonical_text(existing["text"]) != _canonical_text(clean_text):
                    raise ValueError(
                        "Claim ID {} already names a different statement".format(
                            resolved_id
                        )
                    )
                return existing, False
            self._append(
                topic,
                "claim",
                {
                    "claim_id": resolved_id,
                    "id_method": "explicit" if claim_id else CLAIM_ID_METHOD,
                    "text": clean_text,
                },
            )
            claim = self.get_claim(topic, resolved_id)
            if claim is None:  # pragma: no cover - defensive filesystem boundary
                raise ClaimStoreError("Persisted claim could not be read back")
            return claim, True

    def add_evidence(
        self,
        topic: str,
        claim_id: str,
        *,
        relation: str,
        source: str,
        source_kind: str,
        video_id: Optional[str] = None,
        start_seconds: Optional[float] = None,
        locator: Optional[str] = None,
        excerpt: Optional[str] = None,
        note: Optional[str] = None,
        primary: Optional[bool] = None,
        independence: str = "unknown",
        lineage_group: Optional[str] = None,
        title: Optional[str] = None,
        channel: Optional[str] = None,
        research_run_id: Optional[str] = None,
    ) -> Tuple[ClaimData, ClaimEvidenceData, bool]:
        if relation not in CLAIM_RELATIONS:
            raise ValueError("Unsupported evidence relation: {}".format(relation))
        if source_kind not in SOURCE_KINDS:
            raise ValueError("Unsupported source kind: {}".format(source_kind))
        if independence not in SOURCE_INDEPENDENCE:
            raise ValueError("Unsupported independence value: {}".format(independence))
        if independence == "echo" and not str(lineage_group or "").strip():
            raise ValueError("Echo evidence requires --lineage-group")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("Evidence requires a source URL, ID, or local reference")
        if video_id is not None:
            validate_youtube_video_id(video_id)
        if primary is not None and not isinstance(primary, bool):
            raise ValueError("Evidence primary value must be boolean or null")
        for field_name, value in (
            ("locator", locator),
            ("excerpt", excerpt),
            ("note", note),
            ("lineage_group", lineage_group),
            ("title", title),
            ("channel", channel),
            ("research_run_id", research_run_id),
        ):
            if value is not None and not isinstance(value, str):
                raise ValueError(
                    "Evidence {} must be text or null".format(field_name)
                )
        if start_seconds is not None:
            if not video_id:
                raise ValueError("Evidence timestamp requires a video ID")
            if isinstance(start_seconds, bool):
                raise ValueError("Evidence timestamp must be a finite number")
            try:
                start_seconds = float(start_seconds)
            except (TypeError, ValueError) as error:
                raise ValueError("Evidence timestamp must be a finite number") from error
            if not math.isfinite(start_seconds) or start_seconds < 0:
                raise ValueError("Evidence timestamp must be a finite non-negative number")
            if start_seconds == 0:
                start_seconds = 0.0
        if video_id and source_kind != "video":
            raise ValueError(
                "A video ID can only be attached to source kind 'video'"
            )
        if video_id and not _source_matches_video(source, video_id):
            raise ValueError(
                "Video evidence source must identify the same YouTube video"
            )
        clean_excerpt = str(excerpt or "").strip()
        clean_note = str(note or "").strip()
        evidence_fields = {
            "claim_id": claim_id,
            "relation": relation,
            "source": source,
            "source_kind": source_kind,
            "video_id": video_id,
            "start_seconds": start_seconds,
            "locator": locator,
            "excerpt": clean_excerpt,
            "note": clean_note,
            "primary": primary,
            "independence": independence,
            "lineage_group": lineage_group,
            "title": title,
            "channel": channel,
            "research_run_id": research_run_id,
        }
        with self._mutation_guard(topic):
            claim = self.get_claim(topic, claim_id)
            if claim is None:
                raise ValueError("Unknown claim ID: {}".format(claim_id))
            evidence_id = _evidence_id(evidence_fields)
            for existing in claim.get("evidence") or []:
                if existing.get("evidence_id") == evidence_id:
                    return claim, existing, False

            data: ClaimEvidenceData = {
                "claim_id": claim_id,
                "evidence_id": evidence_id,
                "id_method": STRUCTURED_ID_METHOD,
                "relation": relation,
                "source": str(source),
                "source_kind": source_kind,
                "independence": independence,
            }
            if video_id:
                data["deep_link"] = _video_deep_link(video_id, start_seconds)
            optional_values = {
                "video_id": video_id,
                "start_seconds": start_seconds,
                "locator": locator,
                "excerpt": clean_excerpt or None,
                "note": clean_note or None,
                "primary": primary,
                "lineage_group": lineage_group,
                "title": title,
                "channel": channel,
                "research_run_id": research_run_id,
            }
            for key, value in optional_values.items():
                if value is not None:
                    data[key] = value  # type: ignore[literal-required]
            event = self._append(topic, "evidence", dict(data))
            data["created_at"] = str(event["ts"])
            updated = self.get_claim(topic, claim_id)
            if updated is None:  # pragma: no cover
                raise ClaimStoreError("Persisted evidence could not be read back")
            return updated, data, True

    def assess(
        self,
        topic: str,
        claim_id: str,
        *,
        verdict: str,
        confidence: str,
        note: str,
    ) -> Tuple[ClaimData, bool]:
        if verdict not in CLAIM_VERDICTS:
            raise ValueError("Unsupported claim verdict: {}".format(verdict))
        if confidence not in CLAIM_CONFIDENCE:
            raise ValueError("Unsupported claim confidence: {}".format(confidence))
        clean_note = str(note or "").strip()
        with self._mutation_guard(topic):
            claim = self.get_claim(topic, claim_id)
            if claim is None:
                raise ValueError("Unknown claim ID: {}".format(claim_id))
            if (
                claim.get("assessment_id")
                and claim.get("verdict") == verdict
                and claim.get("confidence") == confidence
                and claim.get("assessment_note", "") == clean_note
            ):
                return claim, False
            assessment_id = _digest(
                "a",
                claim_id,
                claim.get("assessment_id", ""),
                verdict,
                confidence,
                clean_note,
            )
            data = {
                "claim_id": claim_id,
                "assessment_id": assessment_id,
                "id_method": STRUCTURED_ID_METHOD,
                "verdict": verdict,
                "confidence": confidence,
                "note": clean_note,
            }
            if claim.get("assessment_id"):
                data["supersedes"] = str(claim["assessment_id"])
            self._append(topic, "assessment", data)
            updated = self.get_claim(topic, claim_id)
            if updated is None:  # pragma: no cover
                raise ClaimStoreError("Persisted assessment could not be read back")
            return updated, True


_store: Optional[ClaimStore] = None


def get_claim_store() -> ClaimStore:
    """Return a store rooted in the active project data directory."""
    global _store
    root = project_data_dir()
    if _store is None or _store.data_dir != root:
        _store = ClaimStore(root)
    return _store
