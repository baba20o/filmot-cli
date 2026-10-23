"""Transcripts from Filmot's subtitle index, for when YouTube cannot deliver one.

Filmot keeps the caption tracks it indexed (the ``/getvideos`` ``subtitles``
column): per-line text and start times, without durations, as of its last
fetch. They survive IP blocks, disabled captions, and deleted videos, but can
predate a video's current captions, so every result says it came from the index.
"""

from __future__ import annotations

import math
import os
import threading
from contextvars import copy_context
from typing import Any, Callable, Dict, List, Optional, Sequence

from .api import Deadline, FilmotClient
from .api_providers import ProviderConfigurationError, configured_providers, configured_secrets
from .api_usage import valid_timestamp
from .redaction import redact_sensitive_text
from .video_metadata import video_rows


SOURCES = ("auto", "youtube", "filmot")
SOURCE_ENV = "FILMOT_TRANSCRIPT_SOURCE"
ROUTE = "filmot-index"
SOURCE = "filmot_index"
TIMEOUT_ERROR = "IndexTimeout"
DEFAULT_LANGUAGES = ("en", "en-US", "en-GB")
# Filmot omits when the last line ends; captions rarely stay up longer.
_MAX_LAST_LINE_SECONDS = 10.0
# Join in slices so Ctrl+C still interrupts the wait on Windows.
_JOIN_SLICE_SECONDS = 0.25


def transcript_source(value: Optional[str] = None) -> str:
    """The transcript source: ``value``, else ``FILMOT_TRANSCRIPT_SOURCE``, else ``auto``.

    ``auto`` tries YouTube and falls back to the index, ``youtube`` never uses
    the index, and ``filmot`` uses only the index.
    """
    chosen = value if value is not None else (os.environ.get(SOURCE_ENV) or "auto")
    chosen = chosen.strip().casefold()
    if chosen not in SOURCES:
        raise ValueError(
            "transcript source must be one of {}, not {!r}".format(", ".join(SOURCES), chosen)
        )
    return chosen


def index_available() -> bool:
    """Whether a Filmot API client could be built to query the index."""
    try:
        return bool(configured_providers(require=True))
    except ValueError:
        return False


def _base(language: str) -> str:
    return language.split("-")[0].casefold()


def _usable_tracks(tracks: Any) -> List[Dict[str, Any]]:
    """Tracks with at least one timed, non-blank line."""
    return [
        track for track in (tracks if isinstance(tracks, list) else [])
        if isinstance(track, dict)
        and isinstance(track.get("lang"), str)
        and isinstance(track.get("transcript"), list)
        and any(
            segment["text"].strip()
            for segment in _segments(track["transcript"], video_duration=None)
        )
    ]


def _choose_track(tracks: Any, languages: Sequence[str]) -> Optional[Dict[str, Any]]:
    """The first requested language Filmot has, manual tracks before automatic.

    Unusable tracks are skipped, so an empty manual track gives way to a usable
    automatic one.
    """
    usable = _usable_tracks(tracks)
    for wanted in languages:
        for matches in (
            [track for track in usable if track["lang"].casefold() == wanted.casefold()],
            [track for track in usable if _base(track["lang"]) == _base(wanted)],
        ):
            if matches:
                return sorted(matches, key=lambda track: track.get("isauto") in (1, True))[0]
    return None


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


def _segments(lines: List[Any], video_duration: Any) -> List[Dict[str, Any]]:
    """Index lines as ``{text, start, duration}``; each runs until the next starts."""
    parsed = []
    for line in lines:
        if isinstance(line, dict) and isinstance(line.get("t"), str):
            start = _number(line.get("s"))
            if start is not None:
                parsed.append((start, line["t"]))
    parsed.sort(key=lambda pair: pair[0])
    end = _number(video_duration)
    segments = []
    for index, (start, text) in enumerate(parsed):
        if index + 1 < len(parsed):
            duration = parsed[index + 1][0] - start
        else:
            duration = min(_MAX_LAST_LINE_SECONDS, max(0.0, (end or 0.0) - start))
        segments.append({"text": text, "start": start, "duration": duration})
    return segments


def index_provenance(result: Dict[str, Any]) -> Dict[str, Any]:
    """The ``transcript_index`` block a library save keeps, for index transcripts only."""
    index = result.get("transcript_index")
    return {"transcript_index": dict(index)} if isinstance(index, dict) else {}


def _failure(video_id: str, error_type: str, message: str) -> Dict[str, Any]:
    return {"error": message, "error_type": error_type, "video_id": video_id, "route": ROUTE}


class _LookupTimeout(Exception):
    """The index lookup did not finish within its deadline."""


def _within(deadline: Optional[Deadline], call: Callable[[], Any]) -> Any:
    """``call()``'s result, waiting for it until ``deadline`` at the latest.

    Socket timeouts cannot bound a whole request: a read timeout measures
    silence, not total time, and the rate limiter can block first. So the call
    runs in a daemon thread. When the wait ends early, at the deadline or on
    Ctrl+C, the deadline is cancelled and the thread abandoned; a client that
    shares the deadline then starts no further request work. The thread gets
    a copy of this context, which keeps an ``--api-provider`` choice.
    """
    if deadline is None:
        return call()
    outcome: Dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["value"] = call()
        except BaseException as error:  # re-raised in the waiting thread
            outcome["error"] = error

    worker = threading.Thread(
        target=copy_context().run, args=(run,), name="filmot-index-lookup", daemon=True,
    )
    try:
        # start() waits for the thread to run, so Ctrl+C can arrive here too.
        worker.start()
        while worker.is_alive():
            remaining = deadline.remaining()
            if remaining <= 0:
                raise _LookupTimeout()
            worker.join(min(remaining, _JOIN_SLICE_SECONDS))
    except BaseException:
        deadline.cancel()
        raise
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def fetch_index_transcript(
    video_id: str,
    languages: Optional[Sequence[str]] = None,
    *,
    preserve_formatting: bool = False,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """One video's transcript from Filmot's index, shaped like ``get_transcript``'s.

    ``timeout`` caps the wall-clock wait for the whole lookup, client setup,
    rate limiting, retries, and provider failover included. A lookup still
    running then is abandoned, starts no further request work, and is
    reported as ``IndexTimeout``. Never raises: a missing provider, a failed
    lookup, an unindexed video, or a missing language returns
    ``{"error", "error_type", "video_id", "route"}``.
    """
    languages = list(languages or DEFAULT_LANGUAGES)
    bases = {_base(language) for language in languages}
    deadline = Deadline(timeout) if timeout is not None else None

    def lookup():
        client = FilmotClient(deadline=deadline)
        return client, client.get_videos(
            video_id,
            columns=("subtitles", "lastfetchdate"),
            # One base language narrows the response; several need every track.
            lang=next(iter(bases)) if len(bases) == 1 else None,
        )

    try:
        client, response = _within(deadline, lookup)
    except _LookupTimeout:
        return _failure(
            video_id, TIMEOUT_ERROR,
            "Filmot's index did not answer within {:g}s.".format(timeout),
        )
    except ProviderConfigurationError as error:
        return _failure(video_id, "IndexUnavailable", str(error))
    except Exception as error:  # an index lookup must never break the caller
        return _failure(
            video_id,
            type(error).__name__,
            redact_sensitive_text("{}: {}".format(type(error).__name__, error), configured_secrets()),
        )
    if isinstance(response, dict) and response.get("error"):
        return _failure(
            video_id,
            str(response.get("error_type") or "FilmotAPIError"),
            redact_sensitive_text(str(response["error"]), configured_secrets()),
        )
    row = next((row for row in video_rows(response) if row.get("id") == video_id), None)
    if row is None:
        return _failure(video_id, "VideoNotIndexed", "Filmot's index has no record of this video.")
    track = _choose_track(row.get("subtitles"), languages)
    if track is None:
        available = sorted({track["lang"] for track in _usable_tracks(row.get("subtitles"))})
        return _failure(
            video_id,
            "NoIndexTranscript",
            "Filmot's index has no {} subtitles for this video{}.".format(
                "/".join(languages),
                " (it has: {})".format(", ".join(available)) if available else "",
            ),
        )
    segments = _segments(track["transcript"], row.get("duration"))
    if preserve_formatting:
        full_text = "\n".join(segment["text"] for segment in segments)
    else:
        full_text = " ".join(segment["text"].replace("\n", " ") for segment in segments)
    observed = row.get("lastfetchdate")
    provider = getattr(client, "last_provider", None)
    last = segments[-1]
    return {
        "video_id": video_id,
        "language": track["lang"],
        "is_generated": track.get("isauto") in (1, True),
        "segments": segments,
        "full_text": full_text,
        "duration_seconds": last["start"] + last["duration"],
        "segment_count": len(segments),
        "source": SOURCE,
        "route": ROUTE,
        "transcript_index": {
            "provider": "filmot",
            "language": track["lang"],
            "is_generated": track.get("isauto") in (1, True),
            "observed_at": observed if isinstance(observed, str) and valid_timestamp(observed) else None,
            "api_provider": provider if isinstance(provider, str) else None,
        },
    }
