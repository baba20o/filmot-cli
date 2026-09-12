"""Provider-neutral normalization for discovered video candidates.

Discovery commands currently expose two families of records: Filmot's compact
search rows and YouTube Data API metadata.  Downstream download/enrichment
code should not have to remember each provider's spelling, nor should it
mutate storage before learning that a later row has an invalid identity.

The public interface is deliberately small:

``extract_candidates(artifact)``
    Read a bare list or a ``result``/``videos``/``items`` envelope.

``preflight_candidates(artifact, ...)``
    Normalize every row and return all validation issues.  If any row is
    invalid, ``candidates`` is empty; callers therefore cannot accidentally
    consume a partial batch.

``normalize_candidates(artifact, ...)``
    The convenient fail-closed form.  It returns fresh canonical dictionaries
    or raises :class:`DiscoveryValidationError` containing every issue.

``normalize_candidate(record, ...)``
    Normalize one already-selected record with the same rules.

Inputs are never modified.  Unknown provider data is retained in a bounded,
credential-scrubbed ``provider_fields`` mapping.  The canonical result keeps
``None`` for unobserved metadata, so an observed count of zero remains
distinguishable from a missing count.  Within the provider-field budget,
freshness, disclosure, status, and topic evidence is copied before bulky
presentation extras.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, TypedDict, Union
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


YOUTUBE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_ENVELOPE_FIELDS = ("result", "videos", "items")
_YOUTUBE_PROVIDER_NAMES = frozenset({
    "youtube",
    "youtube-api",
    "youtube-data-api",
    "youtube_data_api",
    "google-youtube",
})

_ID_FIELDS = ("video_id", "videoid", "videoId", "id")
_CANONICAL_ALIASES = frozenset({
    "video_id", "videoid", "videoId", "id",
    "title", "name",
    "description", "summary",
    "channel_title", "channelname", "channeltitle", "channel", "channelTitle",
    "channel_id", "channelid", "channelId",
    "published_at", "publishedAt", "published", "publishdate", "upload_date",
    "uploaddate",
    "views", "viewcount", "viewCount",
    "likes", "likecount", "likeCount",
    "comments", "commentcount", "commentCount",
    "duration", "duration_seconds",
    "url", "video_url", "webpage_url",
    "provider", "provenance", "provider_fields",
})
_CONTAINER_CANONICAL_FIELDS = {
    "id": frozenset({"videoId"}),
    "snippet": frozenset({
        "title", "description", "channelTitle", "channelId", "publishedAt",
    }),
    "statistics": frozenset({"viewCount", "likeCount", "commentCount"}),
    "contentDetails": frozenset({"duration"}),
}
_PROVENANCE_FIELDS = (
    "query",
    "filters",
    "order",
    "days",
    "max_results",
    "published_after",
    "published_before",
    "requested_at",
    "request_time",
    "scope",
)
_FILMOT_METADATA_FIELDS = (
    "schema",
    "command",
    "status",
    "version",
    "run_id",
    "created_at",
    "requested_at",
)

# These YouTube detail fields carry freshness, disclosure, publication-status,
# or topical provenance.  Rich provider rows commonly place bulky thumbnails,
# tags, and restrictions before them, so copying in input order can spend the
# global node budget before the evidence needed to interpret or expire the row.
# Priority affects retention only; the same depth, width, text, and global-node
# limits still apply.  Flattened fields precede their raw API containers because
# they are the stable boundary emitted by ``youtube_search._video_detail``.
_PROVIDER_FIELD_PRIORITY = (
    "metadata_observed_at",
    "metadata_expires_at",
    "observed_at",
    "expires_at",
    "metadata_status",
    "paid_product_placement",
    "paid_product_placement_disclosure",
    "made_for_kids",
    "self_declared_made_for_kids",
    "contains_synthetic_media",
    "upload_status",
    "failure_reason",
    "rejection_reason",
    "privacy_status",
    "scheduled_publish_at",
    "license",
    "embeddable",
    "public_stats_viewable",
    "topic_ids",
    "relevant_topic_ids",
    "topic_categories",
    "paidProductPlacementDetails",
    "status",
    "topicDetails",
)

_CREDENTIAL_NAMES = frozenset({
    "key",
    "apikey",
    "youtubeapikey",
    "developerkey",
    "privatekey",
    "secretkey",
    "xgoogapikey",
    "authorization",
    "auth",
    "accesstoken",
    "refreshtoken",
    "token",
    "secret",
    "password",
    "signature",
    "credential",
    "credentials",
    "cookie",
    "setcookie",
})
_CREDENTIAL_TEXT_RE = re.compile(
    r"(?i)\b(key|api[_-]?key|access[_-]?token|refresh[_-]?token|auth|token|"
    r"secret|password|signature|credential)\s*=\s*[^&#\s]+"
)
_URL_USERINFO_RE = re.compile(r"(?i)(https?://)[^/\s@]+@")


Count = Optional[int]
Duration = Optional[Union[str, int, float]]


class DiscoveryCandidate(TypedDict):
    """Stable candidate shape shared by discovery providers."""

    video_id: str
    title: Optional[str]
    description: Optional[str]
    channel_title: Optional[str]
    channel_id: Optional[str]
    published_at: Optional[str]
    views: Count
    likes: Count
    comments: Count
    duration: Duration
    url: Optional[str]
    provider: Optional[str]
    provenance: Dict[str, Any]
    provider_fields: Dict[str, Any]


@dataclass(frozen=True)
class ProviderFieldLimits:
    """Hard limits for copied provider-specific data.

    ``max_nodes`` is a global budget across the complete mapping, rather than
    a per-level limit, which prevents deeply branching input from growing the
    normalized record exponentially.
    """

    max_nodes: int = 128
    max_depth: int = 4
    max_items: int = 32
    max_string_length: int = 2048
    max_key_length: int = 128

    def __post_init__(self) -> None:
        for field_name in (
            "max_nodes",
            "max_depth",
            "max_items",
            "max_string_length",
            "max_key_length",
        ):
            if getattr(self, field_name) < 1:
                raise ValueError("{} must be at least 1".format(field_name))


DEFAULT_PROVIDER_FIELD_LIMITS = ProviderFieldLimits()


@dataclass(frozen=True)
class DiscoveryIssue:
    """One credential-safe artifact or candidate validation problem."""

    code: str
    message: str
    path: str
    index: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "path": self.path,
        }
        if self.index is not None:
            result["index"] = self.index
        return result


class DiscoveryError(ValueError):
    """Base class for discovery artifact and candidate failures."""


class DiscoveryArtifactError(DiscoveryError):
    """Raised when a value does not contain a supported candidate array."""

    def __init__(self, code: str, message: str, path: str = "$") -> None:
        self.issue = DiscoveryIssue(code=code, message=message, path=path)
        super().__init__(message)


class DiscoveryValidationError(DiscoveryError):
    """Raised for a batch with one or more invalid candidate identities."""

    def __init__(self, issues: Sequence[DiscoveryIssue]) -> None:
        self.issues = tuple(issues)
        summary = "; ".join(issue.message for issue in self.issues)
        super().__init__(
            "Discovery candidate validation failed ({} issue{}): {}".format(
                len(self.issues),
                "" if len(self.issues) == 1 else "s",
                summary,
            )
        )


@dataclass(frozen=True)
class DiscoveryPreflight:
    """All-or-nothing result from :func:`preflight_candidates`."""

    candidates: Tuple[DiscoveryCandidate, ...]
    errors: Tuple[DiscoveryIssue, ...]
    candidate_count: int

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if self.errors:
            raise DiscoveryValidationError(self.errors)


@dataclass
class _CopyBudget:
    remaining: int

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


_OMIT = object()


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _is_credential_key(value: Any) -> bool:
    normalized = _normalized_key(value)
    return normalized in _CREDENTIAL_NAMES or normalized.endswith(
        (
            "apikey",
            "accesstoken",
            "refreshtoken",
            "authtoken",
            "token",
            "secret",
            "password",
            "credential",
            "credentials",
            "signature",
        )
    )


def _safe_text(value: Any, *, maximum: Optional[int] = None) -> str:
    text = str(value)
    text = _URL_USERINFO_RE.sub(r"\1***:***@", text)
    text = _CREDENTIAL_TEXT_RE.sub(
        lambda match: "{}=***".format(match.group(1)),
        text,
    )
    if maximum is not None and len(text) > maximum:
        return text[:maximum]
    return text


def _bounded_copy(
    value: Any,
    *,
    limits: ProviderFieldLimits,
    budget: _CopyBudget,
    depth: int = 0,
    excluded_keys: frozenset = frozenset(),
    path: Tuple[str, ...] = (),
) -> Any:
    """Copy JSON-like input within one global node budget.

    Unsupported Python objects and non-finite floats are omitted instead of
    calling their potentially sensitive ``repr`` implementations.
    """
    if not budget.take():
        return _OMIT

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _OMIT
    if isinstance(value, str):
        return _safe_text(value, maximum=limits.max_string_length)
    if depth >= limits.max_depth:
        return _OMIT
    if isinstance(value, Mapping):
        copied: Dict[str, Any] = {}
        for key, item in value.items():
            if len(copied) >= limits.max_items:
                break
            key_text = str(key)
            # Filmot search hits call the matched transcript text ``token``.
            # It is evidence, not an authentication token.  Keep that one
            # documented provider field while treating ``token`` as a secret
            # everywhere else.
            filmot_hit_token = (
                _normalized_key(key_text) == "token"
                and bool(path)
                and path[0] == "hits"
            )
            if (
                len(key_text) > limits.max_key_length
                or key_text in excluded_keys
                or (_is_credential_key(key_text) and not filmot_hit_token)
            ):
                continue
            copied_item = _bounded_copy(
                item,
                limits=limits,
                budget=budget,
                depth=depth + 1,
                path=path + (key_text,),
            )
            if copied_item is not _OMIT:
                copied[key_text] = copied_item
        return copied
    if _is_sequence(value):
        copied_list: List[Any] = []
        for item in list(value)[:limits.max_items]:
            copied_item = _bounded_copy(
                item,
                limits=limits,
                budget=budget,
                depth=depth + 1,
                path=path,
            )
            if copied_item is not _OMIT:
                copied_list.append(copied_item)
        return copied_list
    return _OMIT


def _bounded_mapping(
    value: Any,
    *,
    limits: ProviderFieldLimits,
    excluded_keys: frozenset = frozenset(),
) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    copied = _bounded_copy(
        value,
        limits=limits,
        budget=_CopyBudget(limits.max_nodes),
        excluded_keys=excluded_keys,
    )
    return copied if isinstance(copied, dict) else {}


def extract_candidates(artifact: Any) -> Tuple[Any, ...]:
    """Extract candidate rows without mutating or normalizing them.

    Supported inputs are a bare sequence (excluding strings/bytes) and an
    object containing exactly one of ``result``, ``videos``, or ``items``.
    Multiple candidate arrays are rejected as ambiguous.
    """
    if _is_sequence(artifact):
        return tuple(artifact)
    if not isinstance(artifact, Mapping):
        raise DiscoveryArtifactError(
            "artifact_type",
            "Discovery artifact must be a candidate array or object envelope",
        )

    present = [field for field in _ENVELOPE_FIELDS if field in artifact]
    if not present:
        raise DiscoveryArtifactError(
            "candidate_array_missing",
            "Discovery artifact has no result, videos, or items candidate array",
        )
    if len(present) > 1:
        raise DiscoveryArtifactError(
            "candidate_array_ambiguous",
            "Discovery artifact contains multiple candidate arrays: {}".format(
                ", ".join(present)
            ),
        )
    field = present[0]
    rows = artifact[field]
    if not _is_sequence(rows):
        raise DiscoveryArtifactError(
            "candidate_array_type",
            "Discovery artifact field {!r} must be an array".format(field),
            "$.{}".format(field),
        )
    return tuple(rows)


def _artifact_provider(artifact: Any) -> Optional[str]:
    if not isinstance(artifact, Mapping):
        return None
    explicit = _normalize_provider(artifact.get("provider"))
    if explicit:
        return explicit
    metadata = artifact.get("_filmot")
    command = metadata.get("command") if isinstance(metadata, Mapping) else None
    if command in {"yt-search", "yt-video", "yt-playlist"}:
        return "youtube"
    if command in {"search", "search-all", "research"}:
        return "filmot"
    kind = artifact.get("kind")
    if isinstance(kind, str) and kind.startswith("youtube#"):
        return "youtube"
    return None


def _artifact_provenance(
    artifact: Any,
    *,
    limits: ProviderFieldLimits,
) -> Dict[str, Any]:
    if not isinstance(artifact, Mapping):
        return {}
    result: Dict[str, Any] = {}
    metadata = artifact.get("_filmot")
    if isinstance(metadata, Mapping):
        selected = {
            key: metadata[key]
            for key in _FILMOT_METADATA_FIELDS
            if key in metadata
        }
        copied = _bounded_mapping(selected, limits=limits)
        if copied:
            result["artifact"] = copied
    for key in _PROVENANCE_FIELDS:
        if key not in artifact or _is_credential_key(key):
            continue
        copied = _bounded_copy(
            artifact[key],
            limits=limits,
            budget=_CopyBudget(limits.max_nodes),
        )
        if copied is not _OMIT:
            result[key] = copied
    return _bounded_mapping(result, limits=limits)


def _normalize_provider(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    provider = value.strip().lower()
    if (
        provider in _YOUTUBE_PROVIDER_NAMES
        or _normalized_key(provider).startswith("youtube")
    ):
        return "youtube"
    if provider.startswith("filmot"):
        return "filmot"
    return _safe_text(provider, maximum=128)


def _mapping_at(record: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = record.get(field)
    return value if isinstance(value, Mapping) else {}


def _first_value(values: Sequence[Any], *, text: bool = False) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if text:
            if not isinstance(value, str):
                continue
            return _safe_text(value.strip())
        return value
    return None


def _normalize_count(value: Any) -> Count:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() else None
    if isinstance(value, str):
        compact = value.strip().replace(",", "")
        if re.fullmatch(r"[+-]?\d+", compact):
            try:
                return int(compact)
            except (ValueError, OverflowError):
                return None
    return None


def _normalize_duration(value: Any) -> Duration:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    if isinstance(value, str) and value.strip():
        return _safe_text(value.strip())
    return None


def _youtube_id_from_url(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    path_parts = [part for part in parsed.path.split("/") if part]
    if host in {"youtu.be", "www.youtu.be"} and path_parts:
        return path_parts[0]
    if host in {
        "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    }:
        if parsed.path == "/watch":
            for key, item in parse_qsl(parsed.query, keep_blank_values=True):
                if key == "v":
                    return item
        if len(path_parts) >= 2 and path_parts[0] in {"shorts", "embed", "live"}:
            return path_parts[1]
    return None


def youtube_video_id(value: Any) -> Optional[str]:
    """Return an exact YouTube ID from a bare ID or supported public URL.

    The parser accepts canonical watch, short-link, shorts, embed, and live
    URLs on known YouTube hosts.  Extracted path/query values still have to be
    exactly 11 URL-safe characters, so callers can reject malformed input
    before spending API quota.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if YOUTUBE_VIDEO_ID_RE.fullmatch(candidate):
        return candidate
    extracted = _youtube_id_from_url(candidate)
    if isinstance(extracted, str) and YOUTUBE_VIDEO_ID_RE.fullmatch(extracted):
        return extracted
    return None


def _safe_url(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = urlsplit(text)
        query = urlencode([
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not _is_credential_key(key)
        ])
        if parsed.scheme and parsed.netloc:
            host = parsed.hostname or ""
            if parsed.port:
                host = "{}:{}".format(host, parsed.port)
            return _safe_text(urlunsplit(
                (parsed.scheme, host, parsed.path, query, parsed.fragment)
            ))
    except (TypeError, ValueError):
        pass
    return _safe_text(text)


def _record_looks_filmot(record: Mapping[str, Any]) -> bool:
    return any(
        field in record
        for field in ("videoid", "channelname", "channelid", "uploaddate", "viewcount")
    ) or isinstance(record.get("id"), str)


def _record_looks_direct_youtube(record: Mapping[str, Any]) -> bool:
    kind = record.get("kind")
    if isinstance(kind, str) and kind.startswith("youtube#"):
        return True
    if isinstance(record.get("id"), Mapping):
        return True
    if any(field in record for field in ("videoId", "channelTitle", "publishedAt", "viewCount")):
        return True
    if any(field in record for field in ("snippet", "statistics", "contentDetails")):
        return True
    return _youtube_id_from_url(
        _first_value([record.get("url"), record.get("video_url"), record.get("webpage_url")])
    ) is not None


def _identity_values(record: Mapping[str, Any]) -> Tuple[List[Tuple[str, str]], List[str]]:
    values: List[Tuple[str, str]] = []
    malformed: List[str] = []
    for field in _ID_FIELDS:
        if field not in record:
            continue
        value = record[field]
        if field == "id" and isinstance(value, Mapping):
            if "videoId" not in value:
                continue
            nested = value["videoId"]
            if isinstance(nested, str) and nested.strip():
                values.append(("id.videoId", nested.strip()))
            else:
                malformed.append("id.videoId")
            continue
        if isinstance(value, str) and value.strip():
            values.append((field, value.strip()))
        else:
            malformed.append(field)

    raw_url = _first_value([
        record.get("url"), record.get("video_url"), record.get("webpage_url"),
    ])
    url_id = _youtube_id_from_url(raw_url)
    if url_id is not None:
        if url_id.strip():
            values.append(("url", url_id.strip()))
        else:
            malformed.append("url")
    return values, malformed


def _identity_issues(
    record: Mapping[str, Any],
    *,
    index: Optional[int],
    strict_youtube: bool,
) -> Tuple[Optional[str], List[DiscoveryIssue]]:
    base_path = "$" if index is None else "$[{}]".format(index)
    values, malformed = _identity_values(record)
    issues: List[DiscoveryIssue] = []
    if malformed:
        issues.append(DiscoveryIssue(
            code="malformed_video_id",
            message="Candidate {} has non-string or empty ID field(s): {}".format(
                base_path, ", ".join(sorted(malformed))
            ),
            path="{}.video_id".format(base_path),
            index=index,
        ))
    if not values:
        if not malformed:
            issues.append(DiscoveryIssue(
                code="missing_video_id",
                message="Candidate {} has no video ID".format(base_path),
                path="{}.video_id".format(base_path),
                index=index,
            ))
        return None, issues

    distinct = {value for _, value in values}
    if len(distinct) > 1:
        issues.append(DiscoveryIssue(
            code="conflicting_video_id",
            message="Candidate {} has conflicting video IDs in {}".format(
                base_path, ", ".join(field for field, _ in values)
            ),
            path="{}.video_id".format(base_path),
            index=index,
        ))
        return None, issues

    video_id = values[0][1]
    if strict_youtube and not YOUTUBE_VIDEO_ID_RE.fullmatch(video_id):
        issues.append(DiscoveryIssue(
            code="invalid_youtube_video_id",
            message=(
                "Candidate {} has an invalid YouTube video ID; expected "
                "11 URL-safe characters"
            ).format(base_path),
            path="{}.video_id".format(base_path),
            index=index,
        ))
        return None, issues
    return video_id, issues


def _merge_provenance(
    artifact_provenance: Mapping[str, Any],
    record_provenance: Any,
    supplied_provenance: Optional[Mapping[str, Any]],
    *,
    limits: ProviderFieldLimits,
) -> Dict[str, Any]:
    combined: Dict[str, Any] = dict(artifact_provenance)
    if isinstance(record_provenance, Mapping):
        combined.update(record_provenance)
    if supplied_provenance is not None:
        if not isinstance(supplied_provenance, Mapping):
            raise TypeError("provenance must be a mapping")
        combined.update(supplied_provenance)
    return _bounded_mapping(combined, limits=limits)


def _provider_fields(
    record: Mapping[str, Any],
    *,
    limits: ProviderFieldLimits,
) -> Dict[str, Any]:
    unbounded: Dict[str, Any] = {}
    existing = record.get("provider_fields")
    if isinstance(existing, Mapping):
        for key, value in existing.items():
            if key not in _CANONICAL_ALIASES and not _is_credential_key(key):
                unbounded[str(key)] = value

    for key, value in record.items():
        key_text = str(key)
        if key_text in _CONTAINER_CANONICAL_FIELDS and isinstance(value, Mapping):
            nested = {
                str(nested_key): nested_value
                for nested_key, nested_value in value.items()
                if nested_key not in _CONTAINER_CANONICAL_FIELDS[key_text]
                and not _is_credential_key(nested_key)
            }
            if nested:
                unbounded[key_text] = nested
            continue
        if key_text in _CANONICAL_ALIASES or _is_credential_key(key_text):
            continue
        unbounded[key_text] = value

    # Dict insertion order is observable to the global copy budget.  Reserve
    # the front of that budget for time-sensitive and interpretive YouTube
    # evidence, regardless of where the provider placed it in the source row.
    prioritized = {
        key: unbounded[key]
        for key in _PROVIDER_FIELD_PRIORITY
        if key in unbounded
    }
    prioritized.update({
        key: value
        for key, value in unbounded.items()
        if key not in prioritized
    })
    return _bounded_mapping(
        prioritized,
        limits=limits,
        excluded_keys=_CANONICAL_ALIASES,
    )


def _normalize_record(
    record: Mapping[str, Any],
    *,
    index: Optional[int],
    artifact_provider: Optional[str],
    artifact_provenance: Mapping[str, Any],
    provider: Optional[str],
    provenance: Optional[Mapping[str, Any]],
    limits: ProviderFieldLimits,
) -> Tuple[Optional[DiscoveryCandidate], List[DiscoveryIssue]]:
    direct_youtube = _record_looks_direct_youtube(record)
    record_provider = _normalize_provider(record.get("provider"))
    supplied_provider = _normalize_provider(provider)
    inferred_provider = (
        "youtube" if direct_youtube
        else "filmot" if _record_looks_filmot(record)
        else None
    )
    normalized_provider = (
        supplied_provider or record_provider or artifact_provider or inferred_provider
    )
    strict_youtube = (
        direct_youtube
        or artifact_provider == "youtube"
        or record_provider == "youtube"
        or supplied_provider == "youtube"
    )
    video_id, issues = _identity_issues(
        record,
        index=index,
        strict_youtube=strict_youtube,
    )
    if issues:
        return None, issues
    assert video_id is not None

    snippet = _mapping_at(record, "snippet")
    statistics = _mapping_at(record, "statistics")
    content_details = _mapping_at(record, "contentDetails")

    title = _first_value([
        record.get("title"), record.get("name"), snippet.get("title"),
    ], text=True)
    description = _first_value([
        record.get("description"), record.get("summary"), snippet.get("description"),
    ], text=True)
    channel_title = _first_value([
        record.get("channel_title"), record.get("channelname"),
        record.get("channeltitle"), record.get("channel"),
        record.get("channelTitle"), snippet.get("channelTitle"),
    ], text=True)
    channel_id = _first_value([
        record.get("channel_id"), record.get("channelid"),
        record.get("channelId"), snippet.get("channelId"),
    ], text=True)
    published_at = _first_value([
        record.get("published_at"), record.get("publishedAt"),
        record.get("published"), record.get("publishdate"),
        record.get("upload_date"), record.get("uploaddate"),
        snippet.get("publishedAt"),
    ], text=True)
    views = _normalize_count(_first_value([
        record.get("views"), record.get("viewcount"),
        record.get("viewCount"), statistics.get("viewCount"),
    ]))
    likes = _normalize_count(_first_value([
        record.get("likes"), record.get("likecount"),
        record.get("likeCount"), statistics.get("likeCount"),
    ]))
    comments = _normalize_count(_first_value([
        record.get("comments"), record.get("commentcount"),
        record.get("commentCount"), statistics.get("commentCount"),
    ]))
    duration = _normalize_duration(_first_value([
        record.get("duration"), record.get("duration_seconds"),
        content_details.get("duration"),
    ]))
    raw_url = _first_value([
        record.get("url"), record.get("video_url"), record.get("webpage_url"),
    ])
    if YOUTUBE_VIDEO_ID_RE.fullmatch(video_id) and (
        normalized_provider in {"youtube", "filmot"} or direct_youtube
    ):
        url = "https://www.youtube.com/watch?v={}".format(video_id)
    else:
        url = _safe_url(raw_url)

    normalized: DiscoveryCandidate = {
        "video_id": video_id,
        "title": title,
        "description": description,
        "channel_title": channel_title,
        "channel_id": channel_id,
        "published_at": published_at,
        "views": views,
        "likes": likes,
        "comments": comments,
        "duration": duration,
        "url": url,
        "provider": normalized_provider,
        "provenance": _merge_provenance(
            artifact_provenance,
            record.get("provenance"),
            provenance,
            limits=limits,
        ),
        "provider_fields": _provider_fields(record, limits=limits),
    }
    return normalized, []


def preflight_candidates(
    artifact: Any,
    *,
    provider: Optional[str] = None,
    provenance: Optional[Mapping[str, Any]] = None,
    limits: ProviderFieldLimits = DEFAULT_PROVIDER_FIELD_LIMITS,
) -> DiscoveryPreflight:
    """Validate and normalize a candidate artifact without partial output.

    Every row is inspected even after an error.  When ``errors`` is non-empty,
    ``candidates`` is guaranteed to be empty.  This lets a storage pipeline do
    one preflight and then apply the returned batch without rollback logic.
    """
    try:
        rows = extract_candidates(artifact)
    except DiscoveryArtifactError as error:
        return DiscoveryPreflight(
            candidates=(), errors=(error.issue,), candidate_count=0,
        )

    artifact_provider = _artifact_provider(artifact)
    artifact_provenance = _artifact_provenance(artifact, limits=limits)
    normalized: List[DiscoveryCandidate] = []
    issues: List[DiscoveryIssue] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            issues.append(DiscoveryIssue(
                code="candidate_type",
                message="Candidate $[{}] must be an object".format(index),
                path="$[{}]".format(index),
                index=index,
            ))
            continue
        candidate, candidate_issues = _normalize_record(
            row,
            index=index,
            artifact_provider=artifact_provider,
            artifact_provenance=artifact_provenance,
            provider=provider,
            provenance=provenance,
            limits=limits,
        )
        issues.extend(candidate_issues)
        if candidate is not None:
            normalized.append(candidate)

    return DiscoveryPreflight(
        candidates=tuple(normalized) if not issues else (),
        errors=tuple(issues),
        candidate_count=len(rows),
    )


def normalize_candidates(
    artifact: Any,
    *,
    provider: Optional[str] = None,
    provenance: Optional[Mapping[str, Any]] = None,
    limits: ProviderFieldLimits = DEFAULT_PROVIDER_FIELD_LIMITS,
) -> List[DiscoveryCandidate]:
    """Return fresh canonical rows, raising once with all validation issues."""
    result = preflight_candidates(
        artifact,
        provider=provider,
        provenance=provenance,
        limits=limits,
    )
    result.raise_for_errors()
    return list(result.candidates)


def normalize_candidate(
    record: Mapping[str, Any],
    *,
    provider: Optional[str] = None,
    provenance: Optional[Mapping[str, Any]] = None,
    limits: ProviderFieldLimits = DEFAULT_PROVIDER_FIELD_LIMITS,
) -> DiscoveryCandidate:
    """Normalize one selected record using the same fail-closed contract."""
    if not isinstance(record, Mapping):
        raise DiscoveryValidationError((DiscoveryIssue(
            code="candidate_type",
            message="Candidate $ must be an object",
            path="$",
        ),))
    artifact_provider = _normalize_provider(provider)
    candidate, issues = _normalize_record(
        record,
        index=None,
        artifact_provider=artifact_provider,
        artifact_provenance={},
        provider=provider,
        provenance=provenance,
        limits=limits,
    )
    if issues:
        raise DiscoveryValidationError(issues)
    assert candidate is not None
    return candidate


__all__ = [
    "DEFAULT_PROVIDER_FIELD_LIMITS",
    "DiscoveryArtifactError",
    "DiscoveryCandidate",
    "DiscoveryError",
    "DiscoveryIssue",
    "DiscoveryPreflight",
    "DiscoveryValidationError",
    "ProviderFieldLimits",
    "YOUTUBE_VIDEO_ID_RE",
    "extract_candidates",
    "normalize_candidate",
    "normalize_candidates",
    "preflight_candidates",
    "youtube_video_id",
]
