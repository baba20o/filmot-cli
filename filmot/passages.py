"""Bounded, local-only passage reads from an explicitly selected saved copy."""

import math
import re
from typing import Optional

from .library import TranscriptLibrary


class PassageReadError(ValueError):
    """An actionable saved-copy selection or validation failure."""

    def __init__(self, kind: str, message: str, **details):
        super().__init__(message)
        self.kind = kind
        self.details = details


def _finite_number(value) -> bool:
    """A real int or float within float range (an int too large overflows)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def parse_passage_time(value: str) -> float:
    """Accept nonnegative seconds, M:SS, or H:MM:SS, with fractional seconds."""
    parts = value.strip().split(":")
    if (
        not 1 <= len(parts) <= 3
        or not re.fullmatch(r"\d+(?:\.\d+)?", parts[-1])
        or any(not re.fullmatch(r"\d+", part) for part in parts[:-1])
        or (len(parts) > 1 and float(parts[-1]) >= 60)
        or (len(parts) == 3 and int(parts[1]) >= 60)
    ):
        raise ValueError("use nonnegative seconds, M:SS, or H:MM:SS")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    if not math.isfinite(seconds):
        raise ValueError("time must be finite")
    return seconds


def passage_timestamp(seconds: float) -> str:
    """Render saved seconds without silently dropping the fractional part."""
    hours, remainder = divmod(round(seconds, 6), 3600)
    minutes, secs = divmod(remainder, 60)
    second_text = ("{:09.6f}".format(secs)).rstrip("0").rstrip(".")
    if hours:
        return "{}:{:02d}:{}".format(int(hours), int(minutes), second_text)
    return "{}:{}".format(int(minutes), second_text)


def read_saved_passage(
    library: TranscriptLibrary,
    video_id: str,
    *,
    start: float,
    end: float,
    topic: Optional[str] = None,
    max_chars: int = 6000,
    max_segments: int = 80,
) -> dict:
    """Read one saved record without acquisition, logging, or filesystem writes.

    Select complete segments overlapping [start, end). Missing/zero durations
    are point events selected by their start. Text limits can clip the final
    returned segment; its original timing is retained and clipping is explicit.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("expected an 11-character YouTube video ID")
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
        raise ValueError("--to must be greater than --from; times must be finite")
    if not (1 <= max_chars <= 100000 and 1 <= max_segments <= 1000):
        raise ValueError("max_chars must be 1..100000; max_segments must be 1..1000")

    if topic is not None:
        directories = library._topic_dirs_for_read(topic)
    else:
        directories = (
            sorted(path for path in library.transcripts_dir.iterdir()
                   if path.is_dir() and not path.name.startswith("_"))
            if library.transcripts_dir.exists() else []
        )
    paths = [directory / (video_id + ".json") for directory in directories
             if (directory / (video_id + ".json")).exists()]
    if not paths:
        raise PassageReadError(
            "SavedTranscriptNotFound",
            "No saved transcript matches this selection. No network request was made.",
        )
    if len(paths) > 1:
        raise PassageReadError(
            "AmbiguousSavedTranscript",
            "Multiple saved copies exist; select one with --topic. "
            "No network request was made.",
            candidates=[{"topic": path.parent.name, "path": str(path.resolve())}
                        for path in paths[:25]],
            candidate_count=len(paths),
            candidates_truncated=len(paths) > 25,
        )

    path = paths[0]
    try:
        record = library._read_record(path, strict_json=True)
    except (OSError, ValueError, TypeError, AttributeError, RecursionError) as error:
        # Unreadable, not strict JSON (NaN, Infinity), or not a record shape.
        raise PassageReadError(
            "InvalidSavedTranscript",
            "Saved copy could not be read as a transcript record ({}).".format(type(error).__name__),
        ) from None
    if record is None:
        raise PassageReadError("InvalidSavedTranscript", "Saved copy is not a transcript record.")
    if record.get("video_id") != video_id:
        raise PassageReadError("InvalidSavedTranscript", "Saved video ID does not match the requested video.")
    text = record.get("transcript")
    segments = record.get("segments")
    if not isinstance(text, str) or not text.strip() or not segments:
        raise PassageReadError(
            "UntimedSavedTranscript",
            "Saved copy has no usable timestamped text. Use library context for "
            "untimed text or acquire a timestamped copy under a new topic.",
        )
    previous = -1.0
    for index, segment in enumerate(segments):
        valid = isinstance(segment, dict) and isinstance(segment.get("text"), str)
        if valid:
            timestamp = segment.get("start")
            duration = segment.get("duration")
            valid = (
                _finite_number(timestamp) and timestamp >= 0 and timestamp >= previous
                and (duration is None or (
                    _finite_number(duration) and duration >= 0
                    and _finite_number(timestamp + duration)
                ))
            )
        if not valid:
            raise PassageReadError(
                "InvalidSavedTranscript", "Invalid saved timing or text in segment {}.".format(index),
            )
        previous = timestamp
    # Saves join caption texts with spaces (line breaks inside a caption
    # become spaces), or with line breaks when formatting was preserved.
    if text not in (
        " ".join(segment["text"].replace("\n", " ") for segment in segments),
        "\n".join(segment["text"] for segment in segments),
    ):
        raise PassageReadError(
            "InvalidSavedTranscript", "Saved segments do not reproduce the stored transcript text.",
        )

    matched = []
    for index, segment in enumerate(segments):
        timestamp = segment["start"]
        duration = segment.get("duration")
        overlaps = (timestamp + duration > start if duration else timestamp >= start)
        if timestamp < end and overlaps:
            matched.append((index, segment))

    rows = []
    returned_chars = 0
    for index, segment in matched[:max_segments]:
        available = max_chars - returned_chars
        if available <= 0:
            break
        excerpt = segment["text"][:available]
        timestamp = segment["start"]
        duration = segment.get("duration")
        rows.append({
            "segment_index": index,
            "start_seconds": timestamp,
            "end_seconds": timestamp + duration if duration is not None else None,
            "timestamp": passage_timestamp(timestamp),
            "url": "https://www.youtube.com/watch?v={}&t={}s".format(video_id, int(timestamp)),
            "text": excerpt,
            "text_truncated": len(excerpt) < len(segment["text"]),
        })
        returned_chars += len(excerpt)

    metadata = record["metadata"]
    truncated = len(rows) < len(matched) or any(row["text_truncated"] for row in rows)
    return {
        "video_id": video_id,
        "topic": path.parent.name,
        "local_only": True,
        "saved_copy": {
            "path": str(path.resolve()), "saved_at": record.get("saved_at"),
            "source": record["source"], "language": metadata.get("language"),
            "is_generated": metadata.get("is_generated"), "route": metadata.get("route"),
        },
        "window": {"from_seconds": start, "to_seconds": end, "selection": "segment-overlap"},
        "rows": rows,
        "summary": {
            "matching_segments": len(matched), "returned_segments": len(rows),
            "omitted_segments": len(matched) - len(rows),
            "matching_chars": sum(len(segment["text"]) for _, segment in matched),
            "returned_chars": returned_chars, "truncated": truncated,
            "max_chars": max_chars, "max_segments": max_segments,
            "segments_without_duration": sum(segment.get("duration") is None for _, segment in matched),
        },
    }
