"""Filmot video metadata from ``/getvideos`` columns, for display and saved records.

Filmot reports a snapshot from its index (``lastfetchdate``), not a live
YouTube reading, so every candidate carries that observation date. Lookups are
best effort: a failed lookup is reported and never breaks a transcript save.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .api import VIDEO_BATCH_LIMIT, VIDEO_METADATA_COLUMNS
from .api_providers import ProviderConfigurationError, configured_secrets
from .filmot_metadata import (
    FILMOT_METADATA_LOOKUP_PROFILE,
    caption_languages,
    creative_commons,
    filmot_metadata_fields,
)
from .redaction import redact_sensitive_text


ENRICH_ON_SAVE_ENV = "FILMOT_ENRICH_ON_SAVE"
OUTCOME_STATUSES = ("updated", "unchanged", "not_returned", "failed")

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


def _redacted_error(error: BaseException) -> str:
    return redact_sensitive_text(
        "{}: {}".format(type(error).__name__, error), configured_secrets(),
    )


def video_rows(payload: Any) -> List[Dict[str, Any]]:
    """Return the video objects from a list, ``result`` envelope, or bare object."""
    if isinstance(payload, dict) and isinstance(payload.get("result"), list):
        payload = payload["result"]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict) and not payload.get("error") and any(
        key in payload for key in ("id", "videoid", "title")
    ):
        return [payload]
    return []


def video_candidate(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build a ``TranscriptLibrary.enrich_metadata`` candidate from one row."""
    video_id = row.get("id") or row.get("videoid")
    if not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id):
        return None
    return {
        "video_id": video_id,
        "provider": "filmot",
        **filmot_metadata_fields(row),
    }


def fetch_video_candidates(
    client: Any,
    video_ids: Iterable[str],
) -> Tuple[Dict[str, Tuple[Dict[str, Any], Optional[str]]], List[Dict[str, Any]]]:
    """Look up metadata in batches of up to 200 IDs without raising.

    Returns ``({video_id: (candidate, api_provider)}, lookup_errors)``; each
    error lists the ``video_ids`` of the batch it lost.
    """
    unique = [
        video_id for video_id in dict.fromkeys(video_ids)
        if isinstance(video_id, str) and _VIDEO_ID.fullmatch(video_id)
    ]
    candidates: Dict[str, Tuple[Dict[str, Any], Optional[str]]] = {}
    errors: List[Dict[str, Any]] = []
    for start in range(0, len(unique), VIDEO_BATCH_LIMIT):
        batch = unique[start:start + VIDEO_BATCH_LIMIT]
        try:
            response = client.get_videos(",".join(batch), columns=VIDEO_METADATA_COLUMNS)
        except Exception as error:  # a metadata lookup must never break a save
            errors.append({"video_ids": batch, "error": _redacted_error(error)})
            continue
        if isinstance(response, dict) and response.get("error"):
            errors.append({
                "video_ids": batch,
                "error": redact_sensitive_text(str(response["error"]), configured_secrets()),
                "error_type": str(response.get("error_type") or "FilmotAPIError"),
            })
            continue
        provider = getattr(client, "last_provider", None)
        provider = provider if isinstance(provider, str) else None
        wanted = set(batch)
        for row in video_rows(response):
            candidate = video_candidate(row)
            if candidate and candidate["video_id"] in wanted:
                candidates[candidate["video_id"]] = (candidate, provider)
    return candidates, errors


def _apply_candidates(
    library: Any,
    records: List[Tuple[str, str]],
    candidates: Dict[str, Tuple[Dict[str, Any], Optional[str]]],
    lookup_errors: List[Dict[str, Any]],
) -> Dict[str, Any]:
    lost = {
        video_id: error["error"]
        for error in lookup_errors
        for video_id in error.get("video_ids") or ()
    }
    enriched_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    outcomes: List[Dict[str, Any]] = []
    for topic, video_id in records:
        if not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id):
            outcomes.append({
                "topic": topic, "video_id": video_id, "status": "failed",
                "error": "InvalidVideoID: expected an 11-character YouTube video ID",
                "error_type": "InvalidVideoID",
            })
            continue
        found = candidates.get(video_id)
        if found is None:
            outcome = {"topic": topic, "video_id": video_id, "status": "not_returned"}
            if video_id in lost:
                outcome.update(status="failed", error=lost[video_id])
            else:
                # A completed lookup with no matching row is useful evidence:
                # do not repeatedly charge for it in the default selection.
                # It is not a metadata observation and supplies no field values.
                try:
                    library.enrich_metadata(
                        video_id, topic, {"video_id": video_id, "provider": "filmot"},
                        provenance={
                            "provider": "filmot",
                            "lookup_profile": FILMOT_METADATA_LOOKUP_PROFILE,
                            "lookup_status": "not_returned",
                            "lookup_at": enriched_at,
                        },
                        filmot_snapshot=True,
                    )
                except Exception as error:
                    outcome.update(status="failed", error=_redacted_error(error))
            outcomes.append(outcome)
            continue
        candidate, provider = found
        provenance = {
            "provider": "filmot", "enriched_at": enriched_at,
            "lookup_profile": FILMOT_METADATA_LOOKUP_PROFILE,
            "lookup_status": "returned", "lookup_at": enriched_at,
        }
        if candidate.get("filmot_observed_at"):
            provenance["observed_at"] = candidate["filmot_observed_at"]
        if provider:
            provenance["api_provider"] = provider
        try:
            result = library.enrich_metadata(
                video_id, topic, candidate,
                provenance=provenance, filmot_snapshot=True,
            )
        except Exception as error:  # one unwritable record must not stop the rest
            outcomes.append({
                "topic": topic, "video_id": video_id, "status": "failed",
                "error": _redacted_error(error),
            })
            continue
        outcomes.append({
            "topic": topic,
            "video_id": video_id,
            "status": "updated" if result["status"] == "updated" else "unchanged",
            "filled_fields": list(result.get("filled_fields") or []),
            "conflicts": len(result.get("conflicts") or []),
            "deferred_to_youtube": bool(result.get("deferred_to_youtube")),
        })
    counts = {status: 0 for status in OUTCOME_STATUSES}
    for outcome in outcomes:
        counts[outcome["status"]] += 1
    return {
        "records": len(records),
        **counts,
        "lookup_errors": lookup_errors,
        "outcomes": outcomes,
    }


def _lookup_failed(
    library: Any,
    records: List[Tuple[str, str]],
    error: BaseException,
) -> Dict[str, Any]:
    """Summarize records whose lookup could not run at all."""
    return _apply_candidates(library, records, {}, [{
        "video_ids": [video_id for _, video_id in records],
        "error": _redacted_error(error),
    }])


def enrich_saved_transcripts(
    library: Any,
    records: Iterable[Tuple[str, str]],
    client: Any,
) -> Dict[str, Any]:
    """Fill missing metadata on saved ``(topic, video_id)`` records from Filmot.

    Known values are never overwritten; differing observations are counted as
    conflicts by ``TranscriptLibrary.enrich_metadata``.
    """
    records = list(dict.fromkeys(records))
    candidates, errors = fetch_video_candidates(client, [video_id for _, video_id in records])
    return _apply_candidates(library, records, candidates, errors)


def enrichment_counts(summary: Dict[str, Any]) -> Dict[str, int]:
    """The ledger-sized part of an enrichment summary."""
    counts = {key: int(summary.get(key, 0)) for key in ("records", *OUTCOME_STATUSES)}
    counts["lookup_errors"] = len(summary.get("lookup_errors") or [])
    return counts


def enrichment_summary_line(summary: Dict[str, Any]) -> str:
    """One human line for an enrichment summary or its ``enrichment_counts``."""
    parts = ["{} updated".format(summary.get("updated", 0))]
    for key, label in (("unchanged", "already complete"), ("not_returned", "not in Filmot"),
                       ("failed", "failed")):
        if summary.get(key):
            parts.append("{} {}".format(summary[key], label))
    lookup_errors = summary.get("lookup_errors") or 0
    if not isinstance(lookup_errors, int):
        lookup_errors = len(lookup_errors)
    if lookup_errors:
        parts.append("{} lookup error{}".format(lookup_errors, "" if lookup_errors == 1 else "s"))
    return "Filmot metadata: " + ", ".join(parts)


def enrich_on_save_enabled() -> bool:
    """Save-time lookups run unless ``FILMOT_ENRICH_ON_SAVE`` is 0, false, no, or off."""
    value = os.environ.get(ENRICH_ON_SAVE_ENV, "")
    return value.strip().casefold() not in {"0", "false", "no", "off"}


def enrich_after_save(
    library: Any,
    records: Iterable[Tuple[str, str]],
    client_factory: Callable[[], Any],
    *,
    source: str,
) -> Optional[Dict[str, Any]]:
    """Best-effort Filmot enrichment of records a command has just saved.

    Returns ``None``, without a request, when there is nothing to enrich, the
    feature is turned off, or no Filmot API provider is configured. Never raises.
    """
    from .ledger import log_event

    records = list(dict.fromkeys(records))
    if not records or not enrich_on_save_enabled():
        return None
    try:
        client = client_factory()
    except ProviderConfigurationError:
        return None
    except Exception as error:  # e.g. an unusable cache directory
        summary = _lookup_failed(library, records, error)
    else:
        try:
            summary = enrich_saved_transcripts(library, records, client)
        except Exception as error:  # the transcripts are already saved
            summary = _lookup_failed(library, records, error)
    counts = enrichment_counts(summary)
    topics = {topic for topic, _ in records}
    failures = sorted({
        outcome["error"] for outcome in summary["outcomes"] if outcome.get("error")
    })
    log_event(
        "filmot_enrichment",
        topic=next(iter(topics)) if len(topics) == 1 else None,
        source=source,
        status=(
            "failed" if counts["failed"] == counts["records"]
            else "partial" if counts["failed"]
            else "completed"
        ),
        error="; ".join(failures) or None,
        error_type="FilmotEnrichmentError" if failures else None,
        **counts,
    )
    return summary
