"""
Transcript Library - Persistent local storage for YouTube transcripts.

Organize transcripts by topic/keyword for building curated knowledge bases
that AI agents can reference.

Structure:
    .filmot_data/
        transcripts/
            prompt-injection/
                rAEqP9VEhe8.json
                -O1bjFPgRQM.json
            quantum-computing/
                ...
            _index.json  (optional: metadata about all transcripts)
"""

import hashlib
import json
import math
import os
import re
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, List, Union
from datetime import datetime, timedelta, timezone

from .paths import (
    exclusive_file_guard,
    project_data_dir,
    publish_file_exclusive,
)
from .redaction import redact_sensitive_value


_WINDOWS_RESERVED_NAMES = frozenset({
    "con", "prn", "aux", "nul", "clock$",
    "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
})
_MAX_TOPIC_SLUG_LENGTH = 120
_SOURCE_METADATA_ALIASES = {
    "title": ("title", "name"),
    "channel": ("channel", "channelname", "channeltitle"),
    "channel_id": ("channel_id", "channelid"),
    "published_at": (
        "published_at",
        "publishdate",
        "published",
        "upload_date",
        "uploaddate",
    ),
    "views": ("views", "viewcount"),
}
_UNKNOWN_METADATA_TEXT = frozenset({"", "unknown", "n/a", "not available"})

YOUTUBE_METADATA_SCHEMA = "filmot.youtube-metadata/v1"
YOUTUBE_METADATA_PROVIDER = "youtube-data-api-v3"
YOUTUBE_METADATA_AUDIT_LIMIT = 32
YOUTUBE_METADATA_TTL_DAYS = 30

# YouTube's public video resource is stored in the library's long-standing
# metadata vocabulary.  This table is also the authoritative ownership list
# for refresh and purge: transcript/acquisition fields are intentionally absent.
_YOUTUBE_CANDIDATE_METADATA_ALIASES = {
    "title": "title",
    "description": "description",
    "channel_title": "channel",
    "channel_id": "channel_id",
    "published_at": "published_at",
    "views": "views",
    "likes": "likes",
    "comments": "comments",
    "favorites": "favorites",
    "duration": "youtube_duration",
    "thumbnail": "thumbnail",
    "thumbnails": "thumbnails",
    "definition": "definition",
    "dimension": "dimension",
    "caption": "caption",
    "licensed_content": "licensed_content",
    "category_id": "category_id",
    "tags": "tags",
    "default_language": "default_language",
    "default_audio_language": "default_audio_language",
    "localized": "localized",
    "live_broadcast_content": "live_broadcast_content",
    "upload_status": "upload_status",
    "failure_reason": "failure_reason",
    "rejection_reason": "rejection_reason",
    "privacy_status": "privacy_status",
    "scheduled_publish_at": "scheduled_publish_at",
    "license": "youtube_license",
    "embeddable": "embeddable",
    "public_stats_viewable": "public_stats_viewable",
    "made_for_kids": "made_for_kids",
    "self_declared_made_for_kids": "self_declared_made_for_kids",
    "contains_synthetic_media": "contains_synthetic_media",
    "paid_product_placement": "paid_product_placement",
    "has_paid_product_placement": "paid_product_placement",
    "paid_product_placement_disclosure": (
        "paid_product_placement_disclosure"
    ),
    "region_restriction": "region_restriction",
    "content_rating": "content_rating",
    "projection": "projection",
    "actual_start_time": "actual_start_time",
    "actual_end_time": "actual_end_time",
    "scheduled_start_time": "scheduled_start_time",
    "scheduled_end_time": "scheduled_end_time",
    "concurrent_viewers": "concurrent_viewers",
    "active_live_chat_id": "active_live_chat_id",
    "topic_ids": "topic_ids",
    "relevant_topic_ids": "relevant_topic_ids",
    "topic_categories": "topic_categories",
}
_YOUTUBE_CANDIDATE_SOURCE_ALIASES = {
    "title": "title",
    "channel_title": "channel",
    "channel_id": "channel_id",
    "published_at": "published_at",
    "views": "views",
}

# Records written before the lifecycle contract copied these fields directly
# from a YouTube discovery candidate.  Adoption is allowed only when the record
# also carries explicit YouTube provider provenance.
_LEGACY_YOUTUBE_METADATA_KEYS = frozenset({
    *_YOUTUBE_CANDIDATE_METADATA_ALIASES.values(),
    # The original fill-only enrichment used ``duration`` while pipeline saves
    # used ``youtube_duration``.
    "duration",
    "metadata_observed_at",
    "metadata_expires_at",
    "views_observed_at",
})
_LEGACY_YOUTUBE_SOURCE_KEYS = frozenset({
    *_YOUTUBE_CANDIDATE_SOURCE_ALIASES.values(),
    "views_observed_at",
})

_YOUTUBE_LIFECYCLE_STATES = frozenset({"current", "not_returned", "purged"})
_SHA256_REFERENCE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_YOUTUBE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_PURGE_REASONS = frozenset({
    "operator",
    "expired",
    "refresh_failed",
    "policy",
})
_MISSING = object()


def _metadata_value_missing(value: Any) -> bool:
    """Return whether metadata is absent, preserving valid zero/False values."""
    return value is None or (
        isinstance(value, str)
        and value.strip().casefold() in _UNKNOWN_METADATA_TEXT
    )


def _reject_json_constant(value: str) -> None:
    """Reject the non-standard NaN/Infinity tokens accepted by ``json``."""
    raise ValueError("non-finite JSON constant: {}".format(value))


def _require_finite_json(value: Any, path: str = "$") -> None:
    """Reject finite-overflow numbers recursively at a strict read boundary."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number at {}".format(path))
    if isinstance(value, dict):
        for key, item in value.items():
            _require_finite_json(item, "{}.{}".format(path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _require_finite_json(item, "{}[{}]".format(path, index))


def _write_json_temporary(destination: Path, data: Dict[str, Any]) -> Path:
    """Write, validate, flush, and fsync a private same-directory JSON file."""
    temporary = destination.with_name(
        ".{}.{}.tmp".format(destination.name, uuid.uuid4().hex)
    )
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                data,
                handle,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return temporary
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        raise


def _short_hash(value: str) -> str:
    """Return a stable suffix for otherwise lossy filesystem names."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


def normalize_topic_name(name: str, fallback: str = "uncategorized") -> str:
    """Return a deterministic filesystem slug for the current Unicode runtime.

    Existing ASCII topic names keep their historical spelling. Unicode letters,
    combining marks, and numbers are retained instead of being discarded, so
    investigations in different scripts no longer collapse into one directory.
    NFKC normalization plus case-folding makes canonically equivalent spellings
    resolve to the same slug.

    This v1 routing deliberately follows Python's bundled Unicode database.
    Pin the Python minor version for a shared project: a Unicode database
    upgrade can change normalization or character categories for newly assigned
    code points. The behavior is retained here to avoid silently relocating
    existing topic directories.

    A non-empty name containing no letters or numbers receives a stable hashed
    slug rather than sharing the generic fallback. This matters for punctuation-
    or symbol-only topics, which were previously all stored together.
    """
    canonical = unicodedata.normalize("NFKC", str(name or "")).casefold().strip()
    if not canonical:
        return fallback

    if canonical.isascii():
        # Preserve the exact historical mapping for existing ASCII topics.
        # In particular, punctuation inside a token was removed rather than
        # turned into a separator: ``foo.bar`` has always lived at ``foobar``.
        slug = re.sub(r"[\s_]+", "-", canonical)
        slug = re.sub(r"[^a-z0-9\-]", "", slug)
        slug = re.sub(r"-+", "-", slug).strip("-")
    else:
        slug_chars = []
        separator_pending = False
        for char in canonical:
            category = unicodedata.category(char)
            is_word_char = category[0] in {"L", "N"} or (
                category[0] == "M" and bool(slug_chars)
            )
            if is_word_char:
                if separator_pending and slug_chars and slug_chars[-1] != "-":
                    slug_chars.append("-")
                slug_chars.append(char)
                separator_pending = False
            else:
                separator_pending = bool(slug_chars)
        slug = "".join(slug_chars).strip("-")
    if not slug:
        # Keep this independent of the caller's empty-name fallback so library
        # and ledger canonicalization remain identical for the same non-empty
        # punctuation/symbol-only topic.
        return "topic-{}".format(_short_hash(canonical))

    # Keep path components comfortably below Windows' filename limit and avoid
    # device names that cannot be created there.
    if len(slug) > _MAX_TOPIC_SLUG_LENGTH:
        suffix = _short_hash(canonical)
        slug = "{}-{}".format(
            slug[:_MAX_TOPIC_SLUG_LENGTH - len(suffix) - 1].rstrip("-"),
            suffix,
        )
    if slug in _WINDOWS_RESERVED_NAMES:
        slug = "{}-{}".format(slug, _short_hash(canonical))

    return slug


def _legacy_normalize_topic(name: str, fallback: str = "uncategorized") -> str:
    """Reproduce the pre-Unicode slug algorithm for compatibility/migration."""
    normalized = str(name or "").lower().strip()
    normalized = re.sub(r"[\s_]+", "-", normalized)
    normalized = re.sub(r"[^a-z0-9\-]", "", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    return normalized or fallback


def _replace_with_retry(source: Path, destination: Path, attempts: int = 6) -> None:
    """``os.replace`` with a short retry for Windows sharing violations.

    On Windows ``os.replace`` raises ``PermissionError`` while another process
    holds the destination open for reading. The previous in-place write
    succeeded in that situation, so retry briefly before surfacing the error.
    """
    delay = 0.05
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.5)


def _utc_timestamp(value: datetime) -> str:
    """Return one stable UTC spelling for lifecycle timestamps."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("metadata lifecycle timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_metadata_timestamp(value: Any, field_name: str) -> datetime:
    """Parse an RFC3339-like lifecycle timestamp as aware UTC."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be a timezone-aware timestamp".format(field_name))
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith("Z") else text
        )
    except ValueError as error:
        raise ValueError(
            "{} must be a timezone-aware timestamp".format(field_name)
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("{} must include a timezone".format(field_name))
    return parsed.astimezone(timezone.utc)


def _metadata_now(value: Optional[datetime] = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must include a timezone")
    return current.astimezone(timezone.utc)


def _validate_observation_window(
    observed_at: Any,
    expires_at: Any,
) -> tuple[str, str]:
    """Validate and normalize a non-authorized-data refresh window."""
    observed = _parse_metadata_timestamp(observed_at, "observed_at")
    expires = _parse_metadata_timestamp(expires_at, "expires_at")
    if expires <= observed:
        raise ValueError("expires_at must be later than observed_at")
    if expires > observed + timedelta(days=YOUTUBE_METADATA_TTL_DAYS):
        raise ValueError("expires_at cannot be more than 30 days after observed_at")
    return _utc_timestamp(observed), _utc_timestamp(expires)


def _validate_request_ref(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256_REFERENCE_RE.fullmatch(value):
        raise ValueError("request_ref must be a sha256: reference")
    return value


def _pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _pointer_unescape(value: str) -> str:
    decoded = value.replace("~1", "/").replace("~0", "~")
    if _pointer_escape(decoded) != value:
        raise ValueError("owned path contains invalid JSON Pointer escaping")
    return decoded


def _metadata_path(key: str) -> str:
    return "/metadata/{}".format(_pointer_escape(key))


def _source_path(key: str) -> str:
    return "/source/{}".format(_pointer_escape(key))


def _provider_field_path(key: str) -> str:
    return "/metadata/provider_fields/{}".format(_pointer_escape(key))


def _owned_path_parts(path: Any) -> tuple[str, str]:
    """Decode one supported provider-owned path into container/key."""
    if not isinstance(path, str):
        raise ValueError("owned paths must be strings")
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == "" and parts[1] in {"metadata", "source"}:
        key = _pointer_unescape(parts[2])
        if parts[1] == "metadata" and key == "provider_fields":
            raise ValueError("provider_fields ownership must name an exact child")
        return parts[1], key
    if (
        len(parts) == 4
        and parts[:3] == ["", "metadata", "provider_fields"]
    ):
        return "provider_fields", _pointer_unescape(parts[3])
    raise ValueError("unsupported provider-owned path: {!r}".format(path))


def _container_for_owned_path(
    raw: Dict[str, Any],
    container_name: str,
    *,
    create: bool,
) -> Optional[Dict[str, Any]]:
    outer_name = "metadata" if container_name == "provider_fields" else container_name
    outer = raw.get(outer_name)
    if outer is None:
        if not create:
            return None
        outer = {}
        raw[outer_name] = outer
    if not isinstance(outer, dict):
        raise ValueError("saved transcript {} must be an object".format(outer_name))
    if container_name != "provider_fields":
        return outer
    provider_fields = outer.get("provider_fields")
    if provider_fields is None:
        if not create:
            return None
        provider_fields = {}
        outer["provider_fields"] = provider_fields
    if not isinstance(provider_fields, dict):
        raise ValueError("saved transcript metadata.provider_fields must be an object")
    return provider_fields


def _owned_value(raw: Dict[str, Any], path: str) -> Any:
    container_name, key = _owned_path_parts(path)
    container = _container_for_owned_path(raw, container_name, create=False)
    if container is None:
        return _MISSING
    return container.get(key, _MISSING)


def _delete_owned_value(raw: Dict[str, Any], path: str) -> bool:
    container_name, key = _owned_path_parts(path)
    container = _container_for_owned_path(raw, container_name, create=False)
    if container is None or key not in container:
        return False
    del container[key]
    if container_name == "provider_fields" and not container:
        metadata = raw.get("metadata")
        if isinstance(metadata, dict):
            metadata.pop("provider_fields", None)
    return True


def _set_owned_value(raw: Dict[str, Any], path: str, value: Any) -> None:
    container_name, key = _owned_path_parts(path)
    container = _container_for_owned_path(raw, container_name, create=True)
    assert container is not None
    container[key] = value


def _provider_is_youtube(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return normalized.startswith("youtube")


def _legacy_youtube_provenance(raw: Dict[str, Any]) -> bool:
    metadata = raw.get("metadata")
    if isinstance(metadata, dict) and _provider_is_youtube(
        metadata.get("discovery_provider")
    ):
        return True
    enrichment = raw.get("metadata_enrichment")
    return isinstance(enrichment, dict) and _provider_is_youtube(
        enrichment.get("provider")
    )


def _legacy_youtube_owned_paths(raw: Dict[str, Any]) -> List[str]:
    """Infer only the documented pre-contract YouTube fields."""
    if not _legacy_youtube_provenance(raw):
        return []
    paths: List[str] = []
    metadata = raw.get("metadata")
    if isinstance(metadata, dict):
        for key in sorted(_LEGACY_YOUTUBE_METADATA_KEYS):
            if key in metadata:
                paths.append(_metadata_path(key))
        provider_fields = metadata.get("provider_fields")
        if isinstance(provider_fields, dict):
            paths.extend(
                _provider_field_path(str(key))
                for key in sorted(provider_fields, key=str)
            )
    source = raw.get("source")
    if isinstance(source, dict):
        for key in sorted(_LEGACY_YOUTUBE_SOURCE_KEYS):
            if key in source:
                paths.append(_source_path(key))
    return sorted(set(paths))


def _legacy_youtube_timestamps(
    raw: Dict[str, Any],
) -> tuple[Optional[str], Optional[str]]:
    """Return independently valid timestamps from an adoptable old record."""
    if not _legacy_youtube_provenance(raw):
        return None, None
    metadata = raw.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    provider_fields = metadata.get("provider_fields")
    provider_fields = provider_fields if isinstance(provider_fields, dict) else {}
    source = raw.get("source")
    source = source if isinstance(source, dict) else {}
    enrichment = raw.get("metadata_enrichment")
    enrichment = enrichment if isinstance(enrichment, dict) else {}
    observed_value = (
        metadata.get("metadata_observed_at")
        or provider_fields.get("metadata_observed_at")
        or provider_fields.get("observed_at")
        or metadata.get("views_observed_at")
        or source.get("views_observed_at")
        or enrichment.get("observed_at")
    )
    expires_value = (
        metadata.get("metadata_expires_at")
        or provider_fields.get("metadata_expires_at")
        or provider_fields.get("expires_at")
        or enrichment.get("expires_at")
    )
    observed = None
    expires = None
    try:
        if observed_value is not None:
            observed = _utc_timestamp(
                _parse_metadata_timestamp(observed_value, "observed_at")
            )
    except ValueError:
        pass
    try:
        if expires_value is not None:
            expires = _utc_timestamp(
                _parse_metadata_timestamp(expires_value, "expires_at")
            )
    except ValueError:
        pass
    return observed, expires


def _project_audit_events(value: Any) -> List[Dict[str, Any]]:
    """Keep only value-free lifecycle audit fields from prior events."""
    if not isinstance(value, list):
        return []
    output: List[Dict[str, Any]] = []
    path_fields = {"changed_paths", "removed_paths", "conflict_paths"}
    for item in value[-YOUTUBE_METADATA_AUDIT_LIMIT:]:
        if not isinstance(item, dict):
            continue
        action = item.get("action")
        state = item.get("state")
        at = item.get("at")
        if action not in {"enrich", "adopt", "refresh", "not_returned", "purge"}:
            continue
        if state not in _YOUTUBE_LIFECYCLE_STATES:
            continue
        try:
            at = _utc_timestamp(_parse_metadata_timestamp(at, "audit.at"))
        except ValueError:
            continue
        projected = {"action": action, "at": at, "state": state}
        request_ref = item.get("request_ref")
        try:
            request_ref = _validate_request_ref(request_ref)
        except ValueError:
            request_ref = None
        if request_ref is not None:
            projected["request_ref"] = request_ref
        reason = item.get("reason")
        if reason in _YOUTUBE_PURGE_REASONS:
            projected["reason"] = reason
        for key in path_fields:
            paths = item.get(key)
            if not isinstance(paths, list):
                continue
            valid_paths = []
            for path in paths:
                try:
                    _owned_path_parts(path)
                except ValueError:
                    continue
                valid_paths.append(path)
            projected[key] = sorted(set(valid_paths))
        if projected:
            output.append(projected)
    return output


def _read_youtube_lifecycle(
    raw: Dict[str, Any],
    video_id: str,
    *,
    strict: bool,
) -> Optional[Dict[str, Any]]:
    root = raw.get("metadata_lifecycle")
    if root is None:
        return None
    if not isinstance(root, dict):
        if strict:
            raise ValueError("saved transcript metadata_lifecycle must be an object")
        return None
    value = root.get("youtube")
    if value is None:
        return None
    try:
        if not isinstance(value, dict):
            raise ValueError("YouTube metadata lifecycle must be an object")
        if value.get("schema") != YOUTUBE_METADATA_SCHEMA:
            raise ValueError("unsupported YouTube metadata lifecycle schema")
        if value.get("provider") != YOUTUBE_METADATA_PROVIDER:
            raise ValueError("unsupported YouTube metadata lifecycle provider")
        if value.get("resource") != "video":
            raise ValueError("YouTube metadata lifecycle resource must be video")
        if value.get("id") != video_id:
            raise ValueError("YouTube metadata lifecycle ID does not match record")
        state = value.get("state")
        if state not in _YOUTUBE_LIFECYCLE_STATES:
            raise ValueError("unsupported YouTube metadata lifecycle state")
        owned_paths = value.get("owned_paths")
        if not isinstance(owned_paths, list):
            raise ValueError("YouTube metadata lifecycle owned_paths must be an array")
        if len(set(owned_paths)) != len(owned_paths):
            raise ValueError("YouTube metadata lifecycle owned_paths contains duplicates")
        for path in owned_paths:
            _owned_path_parts(path)
        request_ref = _validate_request_ref(value.get("request_ref"))
        observed_at = value.get("observed_at")
        expires_at = value.get("expires_at")
        if state in {"current", "not_returned"}:
            observed_at, expires_at = _validate_observation_window(
                observed_at, expires_at
            )
        elif observed_at is not None:
            observed_at = _utc_timestamp(
                _parse_metadata_timestamp(observed_at, "observed_at")
            )
        elif expires_at is not None:
            raise ValueError("purged lifecycle cannot have expires_at without observed_at")
        if state == "purged" and expires_at is not None:
            expires_at = _utc_timestamp(
                _parse_metadata_timestamp(expires_at, "expires_at")
            )
        projected = {
            "schema": YOUTUBE_METADATA_SCHEMA,
            "provider": YOUTUBE_METADATA_PROVIDER,
            "resource": "video",
            "id": video_id,
            "observed_at": observed_at,
            "expires_at": expires_at,
            "state": state,
            "owned_paths": sorted(owned_paths),
            "audit": _project_audit_events(value.get("audit")),
        }
        if request_ref is not None:
            projected["request_ref"] = request_ref
        return projected
    except (TypeError, ValueError):
        if strict:
            raise
        return None


def _candidate_youtube_assignments(
    candidate: Dict[str, Any],
    observed_at: str,
    expires_at: str,
) -> Dict[str, Any]:
    """Project one public video observation into owned library paths."""
    provider_fields = candidate.get("provider_fields")
    provider_fields = provider_fields if isinstance(provider_fields, dict) else {}
    observed = dict(provider_fields)
    observed.update(candidate)
    assignments: Dict[str, Any] = {}
    for candidate_key, metadata_key in _YOUTUBE_CANDIDATE_METADATA_ALIASES.items():
        value = observed.get(candidate_key, _MISSING)
        if value is _MISSING or value is None:
            continue
        assignments[_metadata_path(metadata_key)] = redact_sensitive_value(value)
    for candidate_key, source_key in _YOUTUBE_CANDIDATE_SOURCE_ALIASES.items():
        value = observed.get(candidate_key, _MISSING)
        if value is _MISSING or value is None:
            continue
        assignments[_source_path(source_key)] = redact_sensitive_value(value)

    # Retain the established observation aliases for compatible readers while
    # making them explicitly removable through ownership.
    assignments[_metadata_path("metadata_observed_at")] = observed_at
    assignments[_metadata_path("metadata_expires_at")] = expires_at
    assignments[_metadata_path("views_observed_at")] = observed_at
    if observed.get("views") is not None:
        assignments[_source_path("views_observed_at")] = observed_at

    for key, value in provider_fields.items():
        if value is None:
            continue
        if key in {"metadata_observed_at", "observed_at"}:
            value = observed_at
        elif key in {"metadata_expires_at", "expires_at"}:
            value = expires_at
        assignments[_provider_field_path(str(key))] = redact_sensitive_value(value)
    _require_finite_json(assignments)
    return assignments


class TranscriptLibrary:
    """Manage a local library of YouTube transcripts organized by topic."""
    
    def __init__(self, data_dir: Optional[Union[str, Path]] = None):
        """
        Initialize the library.
        
        Args:
            data_dir: Base directory for all filmot data
        """
        self.data_dir = project_data_dir(data_dir)
        self.transcripts_dir = self.data_dir / "transcripts"
    
    def _normalize_topic(self, topic: str) -> str:
        """
        Normalize topic name for filesystem.
        
        Uses the shared Unicode-safe topic canonicalizer.
        """
        return normalize_topic_name(topic)

    def _topic_dirs_for_read(self, topic: str) -> List[Path]:
        """Return only the canonical topic directory.

        Every legacy slug that differs from the canonical slug is potentially
        ambiguous. The old normalizer discarded Unicode, so topics such as
        ``AI 人工知能`` and ``AI 초전도체`` both became ``ai``; pure non-Latin
        topics likewise shared ``uncategorized``. Reading either directory as a
        compatibility fallback can silently cross-contaminate investigations.
        Assigning old data therefore always requires an explicit
        :meth:`migrate_legacy_topic` call.
        """
        current = self.transcripts_dir / self._normalize_topic(topic)
        return [current]
    
    def _get_topic_dir(self, topic: str) -> Path:
        """Get the directory for a topic, creating if needed."""
        topic_normalized = self._normalize_topic(topic)
        topic_dir = self.transcripts_dir / topic_normalized
        topic_dir.mkdir(parents=True, exist_ok=True)
        return topic_dir

    def migrate_legacy_topic(self, topic: str) -> int:
        """Move a pre-Unicode topic directory to its canonical location.

        Migration is explicit because old pure non-Latin topics all shared the
        same ``uncategorized`` directory, so Filmot cannot infer how that corpus
        should be split. Call this only after the user has identified which new
        topic owns the legacy directory. Existing destination files are never
        overwritten; conflicts remain in the legacy directory.

        Returns:
            Number of transcript files migrated.
        """
        canonical_slug = self._normalize_topic(topic)
        legacy_slug = _legacy_normalize_topic(topic)
        if canonical_slug == legacy_slug:
            return 0

        source = self.transcripts_dir / legacy_slug
        if not source.exists():
            return 0

        destination = self.transcripts_dir / canonical_slug
        destination.mkdir(parents=True, exist_ok=True)
        migrated = 0

        for source_path in sorted(source.glob("*.json")):
            destination_path = destination / source_path.name
            if destination_path.exists():
                continue
            temporary = None
            try:
                with open(source_path, "r", encoding="utf-8") as f:
                    data = json.load(f, parse_constant=_reject_json_constant)
                if not isinstance(data, dict):
                    continue
                _require_finite_json(data)
                data["topic"] = canonical_slug
                temporary = _write_json_temporary(destination_path, data)
                if publish_file_exclusive(temporary, destination_path):
                    source_path.unlink()
                    migrated += 1
            except (
                json.JSONDecodeError,
                IOError,
                OSError,
                TypeError,
                UnicodeError,
                ValueError,
            ):
                continue
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink()
                    except FileNotFoundError:
                        pass
                    except OSError:
                        pass

        try:
            source.rmdir()
        except OSError:
            pass
        return migrated
    
    def _sanitize_video_id(self, video_id: str) -> str:
        """
        Sanitize video ID for use as filename.
        
        YouTube IDs can start with - which is fine for filenames.
        """
        # Remove any path separators or dangerous chars
        return re.sub(r'[/\\:*?"<>|]', '_', video_id)

    @staticmethod
    def _canonical_transcript_source(value: Any, route: Any = None) -> str:
        """Return one stable label for the transcript acquisition source."""
        source = str(value or "").strip().casefold().replace("-", "_")
        if not source:
            route_name = str(route or "").strip().casefold().replace("_", "-")
            source = "aws_transcribe" if route_name == "aws-transcribe" else "youtube"
        if source in {"aws", "aws_transcribe"}:
            return "aws_transcribe"
        if source in {"youtube", "youtube_transcript_api"}:
            return "youtube"
        return source

    @classmethod
    def _normalize_metadata(
        cls,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Normalize metadata aliases while retaining caller-specific fields.

        Early library records sometimes placed source fields directly on the
        record, while current records keep them below ``metadata``. Supporting
        both shapes at this boundary avoids an eager on-disk migration.
        """
        raw_metadata = data.get("metadata")
        metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}

        for canonical, aliases in _SOURCE_METADATA_ALIASES.items():
            if metadata.get(canonical) not in (None, ""):
                continue
            for alias in aliases:
                value = metadata.get(alias)
                if value in (None, ""):
                    value = data.get(alias)
                if value not in (None, ""):
                    metadata[canonical] = value
                    break

        legacy_source = data.get("source")
        if isinstance(legacy_source, dict):
            legacy_source = legacy_source.get("transcript_source")
        metadata["source"] = cls._canonical_transcript_source(
            metadata.get("source")
            or data.get("transcript_source")
            or legacy_source,
            metadata.get("route") or data.get("route"),
        )
        return metadata

    @staticmethod
    def _normalize_source_metadata(
        video_id: str,
        metadata: Dict[str, Any],
        existing: Any = None,
    ) -> Dict[str, Any]:
        """Build stable, citation-ready metadata for a YouTube source."""
        source = dict(existing) if isinstance(existing, dict) else {}
        source["platform"] = str(
            source.get("platform") or metadata.get("platform") or "youtube"
        ).casefold()
        source["video_id"] = str(
            source.get("video_id") or source.get("id") or video_id
        )
        source["url"] = str(
            source.get("url")
            or metadata.get("source_url")
            or metadata.get("url")
            or f"https://www.youtube.com/watch?v={video_id}"
        )
        source["transcript_source"] = str(metadata.get("source") or "youtube")

        for key in (
            "title",
            "channel",
            "channel_id",
            "published_at",
            "views",
            "views_observed_at",
        ):
            value = source.get(key)
            if value in (None, ""):
                value = metadata.get(key)
            if value not in (None, ""):
                source[key] = value
        return source

    @classmethod
    def _normalize_record(cls, raw: Any) -> Optional[Dict[str, Any]]:
        """Return the current read shape for both new and legacy records."""
        if not isinstance(raw, dict):
            return None

        data = dict(raw)
        metadata = cls._normalize_metadata(data)
        data["metadata"] = metadata

        # Very early exports used ``full_text`` at the top level. The public
        # library API continues to expose the historical ``transcript`` key.
        if "transcript" not in data:
            data["transcript"] = data.get("full_text", "")
        if not isinstance(data.get("segments"), list):
            data["segments"] = []

        video_id = str(data.get("video_id") or "")
        data["source"] = cls._normalize_source_metadata(
            video_id,
            metadata,
            existing=data.get("source"),
        )
        return data

    @classmethod
    def _read_record(
        cls,
        file_path: Path,
        *,
        strict_json: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Read one library record without rewriting its on-disk shape."""
        with open(file_path, "r", encoding="utf-8") as f:
            raw = json.load(
                f,
                parse_constant=(
                    _reject_json_constant if strict_json else None
                ),
            )
        if strict_json:
            _require_finite_json(raw)
        return cls._normalize_record(raw)
    
    def save(
        self,
        video_id: str,
        topic: str,
        transcript_text: str,
        metadata: Optional[Dict[str, Any]] = None,
        segments: Optional[List[Dict[str, Any]]] = None,
    ) -> Path:
        """
        Save a transcript to the library.
        
        Args:
            video_id: YouTube video ID
            topic: Topic/keyword to organize under
            transcript_text: The full transcript text
            metadata: Optional metadata (title, channel, duration, etc.)
            segments: Optional timestamped transcript segments
            
        Returns:
            Path to the saved file
        """
        if not isinstance(video_id, str) or not video_id.strip():
            raise ValueError("video_id must be non-empty text")
        if not isinstance(transcript_text, str) or not transcript_text.strip():
            raise ValueError("transcript_text must be non-empty text")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be an object or null")
        if segments is not None and not isinstance(segments, list):
            raise ValueError("segments must be a list or null")
        segment_rows = []
        for index, segment in enumerate(segments or []):
            if not isinstance(segment, dict):
                raise ValueError("segment {} must be an object".format(index))
            if not isinstance(segment.get("text"), str):
                raise ValueError("segment {} text must be text".format(index))
            for field_name in ("start", "duration"):
                value = segment.get(field_name)
                if value is None:
                    continue
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or value < 0
                ):
                    raise ValueError(
                        "segment {} {} must be finite and non-negative".format(
                            index,
                            field_name,
                        )
                    )
            segment_rows.append(dict(segment))
        topic_dir = self._get_topic_dir(topic)
        safe_id = self._sanitize_video_id(video_id)
        file_path = topic_dir / f"{safe_id}.json"
        
        guard_path = topic_dir / ".{}.metadata.guard".format(safe_id)
        with exclusive_file_guard(guard_path):
            saved_at = datetime.now().isoformat()
            normalized_metadata = self._normalize_metadata({
                "metadata": metadata or {},
            })
            if normalized_metadata.get("segment_count") is None:
                normalized_metadata["segment_count"] = len(segment_rows)
            if (
                normalized_metadata.get("views") is not None
                and normalized_metadata.get("views_observed_at") is None
            ):
                normalized_metadata["views_observed_at"] = saved_at

            data = {
                "video_id": video_id,
                "topic": self._normalize_topic(topic),
                "saved_at": saved_at,
                "transcript": transcript_text,
                "segments": segment_rows,
                "source": self._normalize_source_metadata(
                    video_id,
                    normalized_metadata,
                ),
                "metadata": normalized_metadata,
            }

            temporary = _write_json_temporary(file_path, data)
            try:
                _replace_with_retry(temporary, file_path)
            finally:
                if temporary.exists():
                    try:
                        temporary.unlink()
                    except OSError:
                        pass

        return file_path

    def enrich_metadata(
        self,
        video_id: str,
        topic: str,
        candidate: Dict[str, Any],
        *,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Fill only missing source metadata on an existing transcript.

        The transcript, segments, acquisition details, citations, ``saved_at``,
        and every unrelated field are preserved verbatim. Known values are
        never overwritten; differing observations are reported as conflicts.
        Save and enrichment share the same per-record OS guard, preventing a
        read/merge/write race from losing a concurrent save.
        """
        if not isinstance(video_id, str) or not video_id.strip():
            raise ValueError("video_id must be non-empty text")
        if not isinstance(candidate, dict):
            raise ValueError("candidate must be an object")
        candidate_id = candidate.get("video_id")
        if candidate_id is not None and str(candidate_id) != video_id:
            raise ValueError(
                "candidate video_id {!r} does not match {!r}".format(
                    candidate_id, video_id
                )
            )
        if provenance is not None and not isinstance(provenance, dict):
            raise ValueError("provenance must be an object or null")

        safe_id = self._sanitize_video_id(video_id)
        topic_dir = self.transcripts_dir / self._normalize_topic(topic)
        file_path = topic_dir / "{}.json".format(safe_id)
        guard_path = topic_dir / ".{}.metadata.guard".format(safe_id)
        if not file_path.exists():
            raise FileNotFoundError(
                "Transcript is not saved at {}/{}".format(
                    self._normalize_topic(topic), video_id
                )
            )

        provider_fields = candidate.get("provider_fields")
        provider_fields = (
            dict(provider_fields) if isinstance(provider_fields, dict) else {}
        )
        # Provider-neutral discovery keeps service-specific observations under
        # ``provider_fields``. Promote known YouTube fields into the library's
        # metadata view while preserving that original provider payload.
        observed_candidate = dict(provider_fields)
        observed_candidate.update({
            key: value
            for key, value in candidate.items()
            if not _metadata_value_missing(value)
        })

        aliases = {
            "title": "title",
            "description": "description",
            "channel_title": "channel",
            "channel_id": "channel_id",
            "published_at": "published_at",
            "views": "views",
            "likes": "likes",
            "comments": "comments",
            "favorites": "favorites",
            "duration": "duration",
            "thumbnail": "thumbnail",
            "thumbnails": "thumbnails",
            "definition": "definition",
            "dimension": "dimension",
            "caption": "caption",
            "licensed_content": "licensed_content",
            "category_id": "category_id",
            "tags": "tags",
            "default_language": "default_language",
            "default_audio_language": "default_audio_language",
            "localized": "localized",
            "live_broadcast_content": "live_broadcast_content",
            "upload_status": "upload_status",
            "failure_reason": "failure_reason",
            "rejection_reason": "rejection_reason",
            "privacy_status": "privacy_status",
            "scheduled_publish_at": "scheduled_publish_at",
            "license": "youtube_license",
            "embeddable": "embeddable",
            "public_stats_viewable": "public_stats_viewable",
            "made_for_kids": "made_for_kids",
            "self_declared_made_for_kids": "self_declared_made_for_kids",
            "contains_synthetic_media": "contains_synthetic_media",
            "paid_product_placement": "paid_product_placement",
            "has_paid_product_placement": "paid_product_placement",
            "paid_product_placement_disclosure": (
                "paid_product_placement_disclosure"
            ),
            "region_restriction": "region_restriction",
            "content_rating": "content_rating",
            "projection": "projection",
            "actual_start_time": "actual_start_time",
            "actual_end_time": "actual_end_time",
            "scheduled_start_time": "scheduled_start_time",
            "scheduled_end_time": "scheduled_end_time",
            "concurrent_viewers": "concurrent_viewers",
            "active_live_chat_id": "active_live_chat_id",
            "topic_ids": "topic_ids",
            "relevant_topic_ids": "relevant_topic_ids",
            "topic_categories": "topic_categories",
            "metadata_observed_at": "metadata_observed_at",
            "metadata_expires_at": "metadata_expires_at",
        }

        with exclusive_file_guard(guard_path):
            with open(file_path, "r", encoding="utf-8") as handle:
                raw = json.load(handle, parse_constant=_reject_json_constant)
            if not isinstance(raw, dict):
                raise ValueError("saved transcript record must be an object")
            _require_finite_json(raw)
            if str(raw.get("video_id") or "") != video_id:
                raise ValueError("saved transcript video_id does not match its path")

            metadata = raw.get("metadata")
            metadata = dict(metadata) if isinstance(metadata, dict) else {}
            source = raw.get("source")
            source = dict(source) if isinstance(source, dict) else {}
            filled = []
            conflicts = []

            for candidate_key, metadata_key in aliases.items():
                incoming = observed_candidate.get(candidate_key)
                if _metadata_value_missing(incoming):
                    continue
                existing = metadata.get(metadata_key)
                if _metadata_value_missing(existing):
                    metadata[metadata_key] = incoming
                    filled.append(metadata_key)
                elif existing != incoming:
                    conflicts.append({
                        "field": metadata_key,
                        "existing": existing,
                        "observed": incoming,
                    })

            source_aliases = {
                "title": "title",
                "channel_title": "channel",
                "channel_id": "channel_id",
                "published_at": "published_at",
                "views": "views",
                "metadata_observed_at": "views_observed_at",
            }
            for candidate_key, source_key in source_aliases.items():
                incoming = observed_candidate.get(candidate_key)
                if _metadata_value_missing(incoming):
                    continue
                existing = source.get(source_key)
                if _metadata_value_missing(existing):
                    source[source_key] = incoming
                    filled.append("source.{}".format(source_key))
                elif existing != incoming:
                    conflict_name = "source.{}".format(source_key)
                    if not any(
                        item["field"] == conflict_name for item in conflicts
                    ):
                        conflicts.append({
                            "field": conflict_name,
                            "existing": existing,
                            "observed": incoming,
                        })

            existing_provider_fields = metadata.get("provider_fields")
            existing_provider_fields = (
                dict(existing_provider_fields)
                if isinstance(existing_provider_fields, dict)
                else {}
            )
            for key, incoming in provider_fields.items():
                if _metadata_value_missing(incoming):
                    continue
                existing = existing_provider_fields.get(key)
                field_name = "provider_fields.{}".format(key)
                if _metadata_value_missing(existing):
                    existing_provider_fields[key] = incoming
                    filled.append(field_name)
                elif existing != incoming:
                    conflicts.append({
                        "field": field_name,
                        "existing": existing,
                        "observed": incoming,
                    })
            if existing_provider_fields:
                metadata["provider_fields"] = existing_provider_fields

            if not filled:
                return {
                    "status": "noop",
                    "path": str(file_path),
                    "filled_fields": [],
                    "conflicts": conflicts,
                }

            raw["metadata"] = metadata
            raw["source"] = source
            observation = dict(provenance or {})
            observation.setdefault(
                "provider", str(candidate.get("provider") or "unknown")
            )
            if observed_candidate.get("metadata_observed_at") is not None:
                observation.setdefault(
                    "observed_at", observed_candidate["metadata_observed_at"]
                )
            if observed_candidate.get("metadata_expires_at") is not None:
                observation.setdefault(
                    "expires_at", observed_candidate["metadata_expires_at"]
                )
            observation["filled_fields"] = list(filled)
            raw["metadata_enrichment"] = observation

            temporary = _write_json_temporary(file_path, raw)
            try:
                _replace_with_retry(temporary, file_path)
            finally:
                if temporary.exists():
                    try:
                        temporary.unlink()
                    except OSError:
                        pass

        return {
            "status": "updated",
            "path": str(file_path),
            "filled_fields": filled,
            "conflicts": conflicts,
        }

    def youtube_metadata_inventory(
        self,
        *,
        topic: Optional[str] = None,
        video_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Inspect YouTube metadata freshness without network or mutation.

        ``classification`` is deliberately limited to ``current``, ``expired``,
        or ``unmanaged``.  ``state`` retains the more precise lifecycle state
        (including ``not_returned`` and ``purged``), while ``legacy_adoptable``
        identifies pre-contract records with explicit YouTube provenance.
        Malformed records remain visible as unmanaged inventory rows rather
        than silently shrinking a maintenance scope.
        """
        current = _metadata_now(now)
        if video_id is not None and (
            not isinstance(video_id, str) or not video_id.strip()
        ):
            raise ValueError("video_id must be non-empty text")

        if topic is not None:
            topic_dirs = self._topic_dirs_for_read(topic)
        elif self.transcripts_dir.exists():
            topic_dirs = [
                path
                for path in sorted(self.transcripts_dir.iterdir())
                if path.is_dir() and not path.name.startswith("_")
            ]
        else:
            topic_dirs = []

        safe_id = self._sanitize_video_id(video_id) if video_id else None
        rows: List[Dict[str, Any]] = []
        for topic_dir in topic_dirs:
            paths = (
                [topic_dir / "{}.json".format(safe_id)]
                if safe_id is not None
                else sorted(topic_dir.glob("*.json"))
            )
            for file_path in paths:
                if not file_path.exists():
                    continue
                base: Dict[str, Any] = {
                    "topic": topic_dir.name,
                    "video_id": video_id or file_path.stem,
                    "path": str(file_path),
                    "classification": "unmanaged",
                    "state": "unmanaged",
                    "managed": False,
                    "legacy_adoptable": False,
                    "owned_paths": [],
                    "owned_path_count": 0,
                    "observed_at": None,
                    "expires_at": None,
                    "request_ref": None,
                }
                try:
                    with open(file_path, "r", encoding="utf-8") as handle:
                        raw = json.load(handle, parse_constant=_reject_json_constant)
                    if not isinstance(raw, dict):
                        raise ValueError("saved transcript record must be an object")
                    _require_finite_json(raw)
                    record_id = raw.get("video_id")
                    if not isinstance(record_id, str) or not record_id.strip():
                        raise ValueError("saved transcript video_id is missing")
                    if video_id is not None and record_id != video_id:
                        raise ValueError("saved transcript video_id does not match its path")
                    base["video_id"] = record_id
                    lifecycle_root = raw.get("metadata_lifecycle")
                    has_youtube_lifecycle = (
                        lifecycle_root is not None
                        and (
                            not isinstance(lifecycle_root, dict)
                            or "youtube" in lifecycle_root
                        )
                    )
                    lifecycle = _read_youtube_lifecycle(
                        raw, record_id, strict=False
                    )
                    if lifecycle is None:
                        if has_youtube_lifecycle:
                            base["state"] = "invalid"
                            base["invalid_lifecycle"] = True
                        else:
                            legacy_adoptable = _legacy_youtube_provenance(raw)
                            base["legacy_adoptable"] = legacy_adoptable
                            if legacy_adoptable:
                                base["state"] = "legacy"
                                legacy_observed, legacy_expires = (
                                    _legacy_youtube_timestamps(raw)
                                )
                                base["observed_at"] = legacy_observed
                                base["expires_at"] = legacy_expires
                                if legacy_expires is not None:
                                    expires = _parse_metadata_timestamp(
                                        legacy_expires, "expires_at"
                                    )
                                    base["classification"] = (
                                        "expired"
                                        if expires <= current
                                        else "current"
                                    )
                        rows.append(base)
                        continue

                    base.update({
                        "state": lifecycle["state"],
                        "managed": True,
                        "owned_paths": list(lifecycle["owned_paths"]),
                        "owned_path_count": len(lifecycle["owned_paths"]),
                        "observed_at": lifecycle.get("observed_at"),
                        "expires_at": lifecycle.get("expires_at"),
                        "request_ref": lifecycle.get("request_ref"),
                    })
                    if lifecycle["state"] == "purged":
                        base["classification"] = "unmanaged"
                    else:
                        expires = _parse_metadata_timestamp(
                            lifecycle["expires_at"], "expires_at"
                        )
                        base["classification"] = (
                            "expired" if expires <= current else "current"
                        )
                    rows.append(base)
                except (
                    json.JSONDecodeError,
                    OSError,
                    TypeError,
                    UnicodeError,
                    ValueError,
                ) as error:
                    base["state"] = "invalid"
                    base["invalid_record"] = True
                    base["error_type"] = type(error).__name__
                    rows.append(base)
        return rows

    def replace_youtube_metadata(
        self,
        video_id: str,
        topic: str,
        candidate: Optional[Dict[str, Any]],
        *,
        observed_at: Optional[str] = None,
        expires_at: Optional[str] = None,
        request_ref: Optional[str] = None,
        adopt_legacy: bool = True,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Atomically replace one record's provider-owned YouTube metadata.

        ``candidate=None`` means a successful API response did not return the
        requested ID.  It removes prior provider-owned values and records the
        neutral ``not_returned`` state; it does not infer deletion or privacy.
        A refresh removes the previous ownership set before applying the new
        observation, so fields omitted upstream cannot survive as stale data.
        Existing values outside that ownership set are never overwritten.
        """
        if not isinstance(video_id, str) or not _YOUTUBE_VIDEO_ID_RE.fullmatch(
            video_id
        ):
            raise ValueError("video_id must be an 11-character YouTube ID")
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("topic must be non-empty text")
        if candidate is not None and not isinstance(candidate, dict):
            raise ValueError("candidate must be an object or null")
        if candidate is not None:
            candidate_id = candidate.get("video_id")
            if candidate_id != video_id:
                raise ValueError("candidate video_id does not match record")
            provider = candidate.get("provider")
            if provider is not None and not _provider_is_youtube(provider):
                raise ValueError("candidate provider is not YouTube")
        request_ref = _validate_request_ref(request_ref)

        provider_fields = (
            candidate.get("provider_fields")
            if isinstance(candidate, dict) else None
        )
        provider_fields = provider_fields if isinstance(provider_fields, dict) else {}
        observation_value = observed_at
        if observation_value is None and candidate is not None:
            observation_value = (
                candidate.get("metadata_observed_at")
                or candidate.get("observed_at")
                or provider_fields.get("metadata_observed_at")
                or provider_fields.get("observed_at")
            )
        if observation_value is None:
            observation_value = _utc_timestamp(_metadata_now(now))
        observed = _parse_metadata_timestamp(observation_value, "observed_at")

        expiry_value = expires_at
        if expiry_value is None and candidate is not None:
            expiry_value = (
                candidate.get("metadata_expires_at")
                or candidate.get("expires_at")
                or provider_fields.get("metadata_expires_at")
                or provider_fields.get("expires_at")
            )
        if expiry_value is None:
            expiry_value = _utc_timestamp(
                observed + timedelta(days=YOUTUBE_METADATA_TTL_DAYS)
            )
        observed_text, expires_text = _validate_observation_window(
            observation_value, expiry_value
        )
        assignments = (
            _candidate_youtube_assignments(
                candidate, observed_text, expires_text
            )
            if candidate is not None
            else {}
        )

        topic_dir = self.transcripts_dir / self._normalize_topic(topic)
        file_path = topic_dir / "{}.json".format(video_id)
        guard_path = topic_dir / ".{}.metadata.guard".format(video_id)
        if not file_path.exists():
            raise FileNotFoundError(
                "Transcript is not saved at {}/{}".format(
                    self._normalize_topic(topic), video_id
                )
            )

        with exclusive_file_guard(guard_path):
            with open(file_path, "r", encoding="utf-8") as handle:
                raw = json.load(handle, parse_constant=_reject_json_constant)
            if not isinstance(raw, dict):
                raise ValueError("saved transcript record must be an object")
            _require_finite_json(raw)
            if raw.get("video_id") != video_id:
                raise ValueError("saved transcript video_id does not match its path")

            previous = _read_youtube_lifecycle(raw, video_id, strict=True)
            legacy_paths: List[str] = []
            if previous is not None:
                old_owned = list(previous["owned_paths"])
            elif adopt_legacy:
                legacy_paths = _legacy_youtube_owned_paths(raw)
                old_owned = list(legacy_paths)
            else:
                if _legacy_youtube_provenance(raw):
                    raise ValueError(
                        "record has legacy YouTube metadata; enable adoption or "
                        "purge it before refresh"
                    )
                old_owned = []

            old_values = {
                path: _owned_value(raw, path)
                for path in old_owned
            }
            for path in old_owned:
                _delete_owned_value(raw, path)

            new_owned: List[str] = []
            conflict_paths: List[str] = []
            changed_paths: List[str] = []
            for path in sorted(assignments):
                incoming = assignments[path]
                # A path not owned by the prior YouTube observation belongs to
                # another source or to the operator.  Preserve it and report a
                # provenance conflict instead of silently claiming ownership.
                if _owned_value(raw, path) is not _MISSING:
                    conflict_paths.append(path)
                    continue
                _set_owned_value(raw, path, incoming)
                new_owned.append(path)
                if (
                    old_values.get(path, _MISSING) is _MISSING
                    or old_values[path] != incoming
                ):
                    changed_paths.append(path)

            removed_paths = sorted(
                path
                for path, value in old_values.items()
                if value is not _MISSING and path not in new_owned
            )
            state = "current" if candidate is not None else "not_returned"
            if previous is not None:
                audit = list(previous.get("audit") or [])
                action = "refresh" if candidate is not None else "not_returned"
            else:
                audit = []
                action = (
                    "adopt" if legacy_paths and candidate is not None
                    else "not_returned" if candidate is None
                    else "enrich"
                )
            audit_event: Dict[str, Any] = {
                "action": action,
                "at": observed_text,
                "state": state,
                "changed_paths": sorted(changed_paths),
                "removed_paths": removed_paths,
                "conflict_paths": sorted(conflict_paths),
            }
            if request_ref is not None:
                audit_event["request_ref"] = request_ref
            audit.append(audit_event)
            audit = audit[-YOUTUBE_METADATA_AUDIT_LIMIT:]

            lifecycle: Dict[str, Any] = {
                "schema": YOUTUBE_METADATA_SCHEMA,
                "provider": YOUTUBE_METADATA_PROVIDER,
                "resource": "video",
                "id": video_id,
                "observed_at": observed_text,
                "expires_at": expires_text,
                "state": state,
                "owned_paths": sorted(new_owned),
                "audit": audit,
            }
            if request_ref is not None:
                lifecycle["request_ref"] = request_ref
            lifecycle_root = raw.get("metadata_lifecycle")
            if lifecycle_root is None:
                lifecycle_root = {}
            elif not isinstance(lifecycle_root, dict):
                raise ValueError("saved transcript metadata_lifecycle must be an object")
            lifecycle_root = dict(lifecycle_root)
            lifecycle_root["youtube"] = lifecycle
            raw["metadata_lifecycle"] = lifecycle_root

            temporary = _write_json_temporary(file_path, raw)
            try:
                _replace_with_retry(temporary, file_path)
            finally:
                if temporary.exists():
                    try:
                        temporary.unlink()
                    except OSError:
                        pass

        return {
            "status": "updated",
            "state": state,
            "path": str(file_path),
            "adopted_legacy": bool(legacy_paths),
            "changed_paths": sorted(changed_paths),
            "removed_paths": removed_paths,
            "conflict_paths": sorted(conflict_paths),
            "owned_paths": sorted(new_owned),
            "observed_at": observed_text,
            "expires_at": expires_text,
            "request_ref": request_ref,
        }

    def purge_youtube_metadata(
        self,
        video_id: str,
        topic: str,
        *,
        reason: str = "operator",
        adopt_legacy: bool = True,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Remove exactly the provider-owned YouTube fields, without an API.

        A clearly YouTube-derived pre-contract record can be adopted directly
        into the purged state.  Unmanaged records are a no-op because deleting
        fields whose ownership is unknown could destroy locally sourced data.
        """
        if not isinstance(video_id, str) or not _YOUTUBE_VIDEO_ID_RE.fullmatch(
            video_id
        ):
            raise ValueError("video_id must be an 11-character YouTube ID")
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("topic must be non-empty text")
        if reason not in _YOUTUBE_PURGE_REASONS:
            raise ValueError(
                "reason must be one of {}".format(
                    ", ".join(sorted(_YOUTUBE_PURGE_REASONS))
                )
            )
        action_at = _utc_timestamp(_metadata_now(now))
        topic_dir = self.transcripts_dir / self._normalize_topic(topic)
        file_path = topic_dir / "{}.json".format(video_id)
        guard_path = topic_dir / ".{}.metadata.guard".format(video_id)
        if not file_path.exists():
            raise FileNotFoundError(
                "Transcript is not saved at {}/{}".format(
                    self._normalize_topic(topic), video_id
                )
            )

        with exclusive_file_guard(guard_path):
            with open(file_path, "r", encoding="utf-8") as handle:
                raw = json.load(handle, parse_constant=_reject_json_constant)
            if not isinstance(raw, dict):
                raise ValueError("saved transcript record must be an object")
            _require_finite_json(raw)
            if raw.get("video_id") != video_id:
                raise ValueError("saved transcript video_id does not match its path")

            previous = _read_youtube_lifecycle(raw, video_id, strict=True)
            legacy_paths: List[str] = []
            if previous is not None:
                old_owned = list(previous["owned_paths"])
            elif adopt_legacy:
                legacy_paths = _legacy_youtube_owned_paths(raw)
                old_owned = list(legacy_paths)
            else:
                old_owned = []
            if (
                previous is not None
                and previous["state"] == "purged"
                and not old_owned
            ):
                return {
                    "status": "noop",
                    "state": "purged",
                    "path": str(file_path),
                    "adopted_legacy": False,
                    "removed_paths": [],
                    "owned_paths": [],
                }
            if previous is None and not old_owned:
                return {
                    "status": "noop",
                    "state": "unmanaged",
                    "path": str(file_path),
                    "adopted_legacy": False,
                    "removed_paths": [],
                    "owned_paths": [],
                }

            removed_paths = sorted(
                path for path in old_owned if _delete_owned_value(raw, path)
            )
            audit = (
                list(previous.get("audit") or [])
                if previous is not None else []
            )
            audit.append({
                "action": "purge",
                "at": action_at,
                "state": "purged",
                "reason": reason,
                "changed_paths": [],
                "removed_paths": removed_paths,
                "conflict_paths": [],
            })
            audit = audit[-YOUTUBE_METADATA_AUDIT_LIMIT:]
            lifecycle: Dict[str, Any] = {
                "schema": YOUTUBE_METADATA_SCHEMA,
                "provider": YOUTUBE_METADATA_PROVIDER,
                "resource": "video",
                "id": video_id,
                "observed_at": (
                    previous.get("observed_at")
                    if previous is not None else action_at
                ),
                "expires_at": (
                    previous.get("expires_at")
                    if previous is not None else None
                ),
                "state": "purged",
                "owned_paths": [],
                "audit": audit,
            }
            if previous is not None and previous.get("request_ref") is not None:
                lifecycle["request_ref"] = previous["request_ref"]
            lifecycle_root = raw.get("metadata_lifecycle")
            if lifecycle_root is None:
                lifecycle_root = {}
            elif not isinstance(lifecycle_root, dict):
                raise ValueError("saved transcript metadata_lifecycle must be an object")
            lifecycle_root = dict(lifecycle_root)
            lifecycle_root["youtube"] = lifecycle
            raw["metadata_lifecycle"] = lifecycle_root

            temporary = _write_json_temporary(file_path, raw)
            try:
                _replace_with_retry(temporary, file_path)
            finally:
                if temporary.exists():
                    try:
                        temporary.unlink()
                    except OSError:
                        pass

        return {
            "status": "updated",
            "state": "purged",
            "path": str(file_path),
            "adopted_legacy": bool(legacy_paths),
            "removed_paths": removed_paths,
            "owned_paths": [],
        }
    
    def get(self, video_id: str, topic: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Retrieve a transcript from the library.
        
        Args:
            video_id: YouTube video ID
            topic: Topic to look in (if None, searches all topics)
            
        Returns:
            Transcript data if found, None otherwise
        """
        safe_id = self._sanitize_video_id(video_id)
        
        if topic:
            # Look in specific topic
            for topic_dir in self._topic_dirs_for_read(topic):
                file_path = topic_dir / f"{safe_id}.json"
                if file_path.exists():
                    return self._read_record(file_path)
            return None
        
        # Search all topics
        if not self.transcripts_dir.exists():
            return None
        for topic_dir in self.transcripts_dir.iterdir():
            if topic_dir.is_dir() and not topic_dir.name.startswith('_'):
                file_path = topic_dir / f"{safe_id}.json"
                if file_path.exists():
                    return self._read_record(file_path)
        
        return None
    
    def exists(self, video_id: str, topic: Optional[str] = None) -> bool:
        """Check if a transcript exists in the library."""
        return self.get(video_id, topic) is not None
    
    def list_topics(self) -> List[Dict[str, Any]]:
        """
        List all topics in the library.
        
        Returns:
            List of dicts with topic name and transcript count
        """
        topics = []
        if not self.transcripts_dir.exists():
            return topics
        for topic_dir in sorted(self.transcripts_dir.iterdir()):
            if topic_dir.is_dir() and not topic_dir.name.startswith('_'):
                count = len(list(topic_dir.glob("*.json")))
                if count > 0:
                    topics.append({
                        "topic": topic_dir.name,
                        "count": count,
                        "path": str(topic_dir),
                    })
        return topics
    
    def list_transcripts(self, topic: str) -> List[Dict[str, Any]]:
        """
        List all transcripts in a topic.
        
        Args:
            topic: Topic name
            
        Returns:
            List of transcript metadata
        """
        transcripts = []
        
        for topic_dir in self._topic_dirs_for_read(topic):
            for file_path in sorted(topic_dir.glob("*.json")):
                try:
                    data = self._read_record(file_path)
                    if data is None:
                        continue
                    transcripts.append({
                        "video_id": data.get("video_id"),
                        "saved_at": data.get("saved_at"),
                        "title": data.get("metadata", {}).get("title", "Unknown"),
                        "channel": data.get("metadata", {}).get("channel", "Unknown"),
                        "char_count": len(data.get("transcript", "")),
                        "path": str(file_path),
                    })
                except (json.JSONDecodeError, IOError):
                    continue
        
        return transcripts

    def read_topic_records(self, topic: str) -> List[Dict[str, Any]]:
        """Read the complete topic corpus or fail on any invalid record.

        Inventory and convenience searches retain their legacy best-effort
        behavior. Corpus-level analysis uses this strict boundary so unreadable,
        malformed, or incomplete JSON cannot silently shrink its source set.
        """
        records = []
        seen_ids = set()
        for topic_dir in self._topic_dirs_for_read(topic):
            for file_path in sorted(topic_dir.glob("*.json")):
                try:
                    data = self._read_record(file_path, strict_json=True)
                except (
                    json.JSONDecodeError,
                    OSError,
                    UnicodeError,
                    ValueError,
                ) as error:
                    raise ValueError(
                        "Unable to read transcript record '{}': {}".format(
                            file_path,
                            error,
                        )
                    ) from error
                if data is None:
                    raise ValueError(
                        "Transcript record must be a JSON object: {}".format(
                            file_path
                        )
                    )
                raw_video_id = data.get("video_id")
                if (
                    not isinstance(raw_video_id, str)
                    or not raw_video_id.strip()
                ):
                    raise ValueError(
                        "Transcript record has no valid video_id: {}".format(
                            file_path
                        )
                    )
                video_id = raw_video_id
                if video_id in seen_ids:
                    raise ValueError(
                        "Duplicate video_id '{}' in topic corpus".format(video_id)
                    )
                transcript = data.get("transcript")
                if not isinstance(transcript, str) or not transcript.strip():
                    raise ValueError(
                        "Transcript text is missing or empty: {}".format(file_path)
                    )
                seen_ids.add(video_id)
                records.append(data)
        return records
    
    def search(self, query: str, topic: Optional[str] = None, substring: bool = False) -> List[Dict[str, Any]]:
        """
        Search for text across saved transcripts.

        Uses word-boundary matching by default to avoid false positives
        (e.g., searching "ore" won't match "more" or "before").

        Args:
            query: Text to search for (case-insensitive)
            topic: Limit search to specific topic (optional)
            substring: If True, use substring matching instead of word boundaries

        Returns:
            List of matches with context
        """
        # Build regex pattern with word boundaries (default) or substring
        if substring:
            pattern = re.compile(re.escape(query), flags=re.IGNORECASE)
        else:
            # Lookarounds instead of \b so queries ending in non-word chars
            # (e.g. "c++", ".net") still match as whole words
            pattern = re.compile(
                r'(?<!\w)' + re.escape(query) + r'(?!\w)',
                flags=re.IGNORECASE,
            )

        results = []

        # Determine which topics to search
        if topic:
            topic_dirs = self._topic_dirs_for_read(topic)
        else:
            if not self.transcripts_dir.exists():
                return []
            topic_dirs = [d for d in self.transcripts_dir.iterdir()
                          if d.is_dir() and not d.name.startswith('_')]

        for topic_dir in topic_dirs:
            if not topic_dir.exists():
                continue
            topic_name = topic_dir.name

            for file_path in topic_dir.glob("*.json"):
                try:
                    data = self._read_record(file_path)
                    if data is None:
                        continue

                    transcript = data.get("transcript", "")
                    if pattern.search(transcript):
                        # Find match positions and extract context
                        match_details = self._find_match_details(
                            transcript,
                            query,
                            pattern=pattern,
                            segments=data.get("segments") or [],
                            video_id=str(data.get("video_id") or ""),
                        )
                        results.append({
                            "video_id": data.get("video_id"),
                            "topic": topic_name,
                            "title": data.get("metadata", {}).get("title", "Unknown"),
                            "channel": data.get("metadata", {}).get("channel", "Unknown"),
                            "match_count": len(match_details),
                            "matches": [
                                item["excerpt"] for item in match_details[:5]
                            ],
                            "match_details": match_details[:5],
                            "match_mode": "substring" if substring else "word",
                        })
                except (json.JSONDecodeError, IOError):
                    continue

        # Sort by match count descending
        results.sort(key=lambda x: x["match_count"], reverse=True)
        return results

    def _find_matches(self, text: str, query: str, context_chars: int = 100, pattern=None, min_gap: int = 0) -> List[str]:
        """Find all occurrences of query in text with surrounding context.

        Args:
            min_gap: Minimum character gap between displayed matches to avoid
                     overlapping context windows. When > 0, skips matches that
                     fall within min_gap chars of the previous kept match.
        """
        return [
            item["excerpt"]
            for item in self._find_match_details(
                text,
                query,
                context_chars=context_chars,
                pattern=pattern,
                min_gap=min_gap,
            )
        ]

    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        total = int(seconds)
        minutes, secs = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return "{}:{:02d}:{:02d}".format(hours, minutes, secs)
        return "{}:{:02d}".format(minutes, secs)

    @staticmethod
    def _time_at_offset(
        segments: List[Dict[str, Any]],
        char_offset: int,
    ) -> Optional[float]:
        """Map a flattened transcript offset back to its source segment."""
        cursor = 0
        for segment in segments:
            raw_text = segment.get("text")
            if not isinstance(raw_text, str):
                return None
            segment_text = raw_text.replace("\n", " ")
            segment_end = cursor + len(segment_text)
            if cursor <= char_offset < segment_end:
                raw_start = segment.get("start")
                if (
                    isinstance(raw_start, bool)
                    or not isinstance(raw_start, (int, float))
                ):
                    return None
                start_seconds = float(raw_start)
                if not math.isfinite(start_seconds) or start_seconds < 0:
                    return None
                return start_seconds
            cursor += len(segment_text) + 1
        # A partial/mismatched segment list cannot safely locate transcript tail
        # text. Returning the last known time here would invent a citation.
        return None

    def _find_match_details(
        self,
        text: str,
        query: str,
        context_chars: int = 100,
        pattern=None,
        min_gap: int = 0,
        segments: Optional[List[Dict[str, Any]]] = None,
        video_id: str = "",
    ) -> List[Dict[str, Any]]:
        """Return citation-ready excerpts with offsets and optional timestamps."""
        matches: List[Dict[str, Any]] = []
        aligned_segments = []
        candidate_segments = segments or []
        if all(
            isinstance(segment, dict)
            and isinstance(segment.get("text"), str)
            and (
                segment.get("start") is None
                or (
                    not isinstance(segment.get("start"), bool)
                    and isinstance(segment.get("start"), (int, float))
                    and math.isfinite(float(segment["start"]))
                    and segment["start"] >= 0
                )
            )
            for segment in candidate_segments
        ):
            aligned_segments = list(candidate_segments)
        if aligned_segments:
            flattened = " ".join(
                segment["text"].replace("\n", " ")
                for segment in aligned_segments
            )
            if flattened != text.replace("\n", " "):
                # Segment timing is only citation-safe when its reconstructed
                # text has the same character coordinate system as full text.
                aligned_segments = []
        if pattern is None:
            pattern = re.compile(
                r'\b' + re.escape(query) + r'\b',
                flags=re.IGNORECASE,
            )

        last_pos = -(min_gap + 1)  # Ensure first match is always included
        # Match against the original text. Lowercasing first can expand Unicode
        # characters (for example, ``İ`` becomes two code points) and would make
        # match offsets, excerpts, and segment timestamps point at the wrong
        # source location.
        for m in pattern.finditer(text):
            pos = m.start()

            # Skip matches whose context would overlap with the previous one
            if min_gap > 0 and (pos - last_pos) < min_gap:
                continue
            last_pos = pos

            query_len = m.end() - m.start()

            # Extract context around match
            start = max(0, pos - context_chars)
            end = min(len(text), pos + query_len + context_chars)

            context = text[start:end]
            if start > 0:
                context = "..." + context
            if end < len(text):
                context = context + "..."

            start_seconds = self._time_at_offset(aligned_segments, pos)
            timestamp = (
                self._format_timestamp(start_seconds)
                if start_seconds is not None
                else None
            )
            url = None
            if video_id:
                url = "https://youtube.com/watch?v={}".format(video_id)
                if start_seconds is not None:
                    url += "&t={}s".format(int(start_seconds))
            matches.append({
                "excerpt": context,
                "start_char": pos,
                "end_char": m.end(),
                "start_seconds": start_seconds,
                "timestamp": timestamp,
                "url": url,
            })

        return matches
    
    def get_context(self, topic: str, max_chars: Optional[int] = None) -> str:
        """
        Concatenate all transcripts in a topic for LLM context.
        
        Args:
            topic: Topic name
            max_chars: Maximum total characters (optional)
            
        Returns:
            Combined transcript text with headers
        """
        transcripts = self.list_transcripts(topic)
        
        parts = []
        total_chars = 0
        
        for t in transcripts:
            data = self.get(t["video_id"], topic)
            if not data:
                continue
            
            header = f"\n{'='*60}\n"
            header += f"VIDEO: {data.get('metadata', {}).get('title', t['video_id'])}\n"
            header += f"CHANNEL: {data.get('metadata', {}).get('channel', 'Unknown')}\n"
            header += f"ID: {t['video_id']}\n"
            header += f"{'='*60}\n\n"
            
            transcript = data.get("transcript", "")
            content = header + transcript
            
            if max_chars and total_chars + len(content) > max_chars:
                # Truncate this transcript to fit
                remaining = max_chars - total_chars
                if remaining > len(header) + 500:  # At least 500 chars of content
                    content = content[:remaining] + "\n\n[TRUNCATED]"
                    parts.append(content)
                break
            
            parts.append(content)
            total_chars += len(content)
        
        return "\n".join(parts)
    
    def delete(self, video_id: str, topic: Optional[str] = None) -> bool:
        """
        Delete a transcript from the library.
        
        Args:
            video_id: YouTube video ID
            topic: Topic to delete from (if None, deletes from all topics)
            
        Returns:
            True if deleted, False if not found
        """
        safe_id = self._sanitize_video_id(video_id)
        deleted = False
        
        if topic:
            for topic_dir in self._topic_dirs_for_read(topic):
                file_path = topic_dir / f"{safe_id}.json"
                if file_path.exists():
                    file_path.unlink()
                    deleted = True
                    break
        else:
            # Delete from all topics
            if not self.transcripts_dir.exists():
                return False
            for topic_dir in self.transcripts_dir.iterdir():
                if topic_dir.is_dir():
                    file_path = topic_dir / f"{safe_id}.json"
                    if file_path.exists():
                        file_path.unlink()
                        deleted = True
        
        return deleted
    
    def delete_topic(self, topic: str) -> int:
        """
        Delete an entire topic and all its transcripts.
        
        Args:
            topic: Topic name
            
        Returns:
            Number of transcripts deleted
        """
        topic_dir = self._get_topic_dir(topic)
        count = 0
        
        for file_path in topic_dir.glob("*.json"):
            file_path.unlink()
            count += 1
        
        # Remove empty directory
        try:
            topic_dir.rmdir()
        except OSError:
            pass  # Directory not empty or doesn't exist
        
        return count
    
    def stats(self) -> Dict[str, Any]:
        """
        Get statistics about the library.
        
        Returns:
            Dict with total counts, size, and per-topic breakdown
        """
        topics = self.list_topics()
        total_transcripts = sum(t["count"] for t in topics)
        total_size = 0
        
        if self.transcripts_dir.exists():
            for topic_dir in self.transcripts_dir.iterdir():
                if topic_dir.is_dir():
                    for file_path in topic_dir.glob("*.json"):
                        total_size += file_path.stat().st_size
        
        return {
            "total_topics": len(topics),
            "total_transcripts": total_transcripts,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "topics": topics,
        }


# Default library instance
_library: Optional[TranscriptLibrary] = None


def get_library() -> TranscriptLibrary:
    """Get the library for the active project data root."""
    global _library
    root = project_data_dir()
    if _library is None or _library.data_dir != root:
        _library = TranscriptLibrary(root)
    return _library
