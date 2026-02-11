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

import json
import os
import re
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime


class TranscriptLibrary:
    """Manage a local library of YouTube transcripts organized by topic."""
    
    def __init__(self, data_dir: str = ".filmot_data"):
        """
        Initialize the library.
        
        Args:
            data_dir: Base directory for all filmot data
        """
        self.data_dir = Path(data_dir)
        self.transcripts_dir = self.data_dir / "transcripts"
        self.transcripts_dir.mkdir(parents=True, exist_ok=True)
    
    def _normalize_topic(self, topic: str) -> str:
        """
        Normalize topic name for filesystem.
        
        Converts to lowercase, replaces spaces/special chars with hyphens.
        """
        # Lowercase and replace spaces/underscores with hyphens
        normalized = topic.lower().strip()
        normalized = re.sub(r'[\s_]+', '-', normalized)
        # Remove any chars that aren't alphanumeric or hyphens
        normalized = re.sub(r'[^a-z0-9\-]', '', normalized)
        # Collapse multiple hyphens
        normalized = re.sub(r'-+', '-', normalized)
        # Strip leading/trailing hyphens
        normalized = normalized.strip('-')
        return normalized or "uncategorized"
    
    def _get_topic_dir(self, topic: str) -> Path:
        """Get the directory for a topic, creating if needed."""
        topic_normalized = self._normalize_topic(topic)
        topic_dir = self.transcripts_dir / topic_normalized
        topic_dir.mkdir(parents=True, exist_ok=True)
        return topic_dir
    
    def _sanitize_video_id(self, video_id: str) -> str:
        """
        Sanitize video ID for use as filename.
        
        YouTube IDs can start with - which is fine for filenames.
        """
        # Remove any path separators or dangerous chars
        return re.sub(r'[/\\:*?"<>|]', '_', video_id)
    
    def save(
        self,
        video_id: str,
        topic: str,
        transcript_text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """
        Save a transcript to the library.
        
        Args:
            video_id: YouTube video ID
            topic: Topic/keyword to organize under
            transcript_text: The full transcript text
            metadata: Optional metadata (title, channel, duration, etc.)
            
        Returns:
            Path to the saved file
        """
        topic_dir = self._get_topic_dir(topic)
        safe_id = self._sanitize_video_id(video_id)
        file_path = topic_dir / f"{safe_id}.json"
        
        data = {
            "video_id": video_id,
            "topic": self._normalize_topic(topic),
            "saved_at": datetime.now().isoformat(),
            "transcript": transcript_text,
            "metadata": metadata or {},
        }
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
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
            file_path = self._get_topic_dir(topic) / f"{safe_id}.json"
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return None
        
        # Search all topics
        for topic_dir in self.transcripts_dir.iterdir():
            if topic_dir.is_dir() and not topic_dir.name.startswith('_'):
                file_path = topic_dir / f"{safe_id}.json"
                if file_path.exists():
                    with open(file_path, 'r', encoding='utf-8') as f:
                        return json.load(f)
        
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
        topic_dir = self._get_topic_dir(topic)
        transcripts = []
        
        for file_path in sorted(topic_dir.glob("*.json")):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
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
        query_lower = query.lower()

        # Build regex pattern with word boundaries (default) or substring
        if substring:
            pattern = re.compile(re.escape(query_lower))
        else:
            pattern = re.compile(r'\b' + re.escape(query_lower) + r'\b')

        results = []

        # Determine which topics to search
        if topic:
            topics_to_search = [self._normalize_topic(topic)]
        else:
            topics_to_search = [d.name for d in self.transcripts_dir.iterdir()
                               if d.is_dir() and not d.name.startswith('_')]

        for topic_name in topics_to_search:
            topic_dir = self.transcripts_dir / topic_name
            if not topic_dir.exists():
                continue

            for file_path in topic_dir.glob("*.json"):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                    transcript = data.get("transcript", "")
                    if pattern.search(transcript.lower()):
                        # Find match positions and extract context
                        matches = self._find_matches(transcript, query_lower, pattern=pattern)
                        results.append({
                            "video_id": data.get("video_id"),
                            "topic": topic_name,
                            "title": data.get("metadata", {}).get("title", "Unknown"),
                            "channel": data.get("metadata", {}).get("channel", "Unknown"),
                            "match_count": len(matches),
                            "matches": matches[:5],  # First 5 matches with context
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
        matches = []
        text_lower = text.lower()

        if pattern is None:
            pattern = re.compile(r'\b' + re.escape(query) + r'\b')

        last_pos = -(min_gap + 1)  # Ensure first match is always included
        for m in pattern.finditer(text_lower):
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

            matches.append(context)

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
            file_path = self._get_topic_dir(topic) / f"{safe_id}.json"
            if file_path.exists():
                file_path.unlink()
                deleted = True
        else:
            # Delete from all topics
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
    """Get the default library instance."""
    global _library
    if _library is None:
        _library = TranscriptLibrary()
    return _library
