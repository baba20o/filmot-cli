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
from datetime import datetime

from .paths import project_data_dir, publish_file_exclusive


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
