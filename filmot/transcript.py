"""
YouTube transcript downloader for deep content analysis.

This module allows fetching full transcripts from YouTube videos,
enabling AI agents to go beyond search snippets and truly understand
video content.
"""

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
)
from typing import Optional
import re


# Create a single API instance
_api = YouTubeTranscriptApi()


def extract_video_id(video_input: str) -> str:
    """
    Extract video ID from various YouTube URL formats or return as-is if already an ID.
    
    Supports:
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://youtube.com/watch?v=VIDEO_ID&other_params
    - Just the VIDEO_ID itself
    """
    # Already a video ID (11 characters, alphanumeric with - and _)
    if re.match(r'^[a-zA-Z0-9_-]{11}$', video_input):
        return video_input
    
    # YouTube URL patterns
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/v/([a-zA-Z0-9_-]{11})',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, video_input)
        if match:
            return match.group(1)
    
    # If nothing matched, return as-is and let the API handle errors
    return video_input


def get_transcript(
    video_id: str,
    languages: Optional[list[str]] = None,
    preserve_formatting: bool = False,
) -> dict:
    """
    Fetch the full transcript for a YouTube video.
    
    Args:
        video_id: YouTube video ID or URL
        languages: Preferred languages in order (e.g., ['en', 'en-US'])
                   If None, tries to get any available transcript
        preserve_formatting: If True, keeps original line breaks
        
    Returns:
        dict with:
            - video_id: The video ID
            - language: Language code of the transcript
            - is_generated: Whether it's auto-generated
            - segments: List of {text, start, duration} dicts
            - full_text: Complete transcript as single string
            - duration_seconds: Total video duration
    """
    video_id = extract_video_id(video_id)
    
    if languages is None:
        languages = ['en', 'en-US', 'en-GB']
    
    try:
        # Try the simple fetch first
        try:
            transcript = _api.fetch(video_id, languages=languages, preserve_formatting=preserve_formatting)
        except NoTranscriptFound:
            # Try to list and translate
            transcript_list = _api.list(video_id)
            translated = None
            for t in transcript_list:
                if t.is_translatable:
                    translated = t.translate('en').fetch(preserve_formatting=preserve_formatting)
                    break
            if translated:
                transcript = translated
            else:
                return {
                    'error': 'No transcript available (tried translation)',
                    'video_id': video_id,
                }
        
        # Convert to dict format for consistency
        segments = [
            {
                'text': seg.text,
                'start': seg.start,
                'duration': getattr(seg, 'duration', 0),
            }
            for seg in transcript
        ]
        
        # Build full text
        if preserve_formatting:
            full_text = '\n'.join(seg['text'] for seg in segments)
        else:
            full_text = ' '.join(seg['text'].replace('\n', ' ') for seg in segments)
        
        # Calculate total duration
        if segments:
            last_seg = segments[-1]
            duration_seconds = last_seg['start'] + last_seg.get('duration', 0)
        else:
            duration_seconds = 0
        
        return {
            'video_id': transcript.video_id,
            'language': transcript.language_code,
            'is_generated': transcript.is_generated,
            'segments': segments,
            'full_text': full_text,
            'duration_seconds': duration_seconds,
            'segment_count': len(segments),
        }
        
    except TranscriptsDisabled:
        return {
            'error': 'Transcripts are disabled for this video',
            'video_id': video_id,
        }
    except VideoUnavailable:
        return {
            'error': 'Video is unavailable',
            'video_id': video_id,
        }
    except Exception as e:
        return {
            'error': str(e),
            'video_id': video_id,
        }


def get_transcript_with_timestamps(
    video_id: str,
    languages: Optional[list[str]] = None,
    chunk_minutes: float = 5.0,
) -> dict:
    """
    Fetch transcript with timestamps, optionally chunked into time segments.
    
    Useful for navigating long videos - groups transcript into chunks
    with their start times.
    
    Args:
        video_id: YouTube video ID or URL
        languages: Preferred languages
        chunk_minutes: Group segments into chunks of this duration
        
    Returns:
        dict with chunked transcript for easier navigation
    """
    result = get_transcript(video_id, languages, preserve_formatting=False)
    
    if 'error' in result:
        return result
    
    segments = result['segments']
    chunk_seconds = chunk_minutes * 60
    
    chunks = []
    current_chunk = {
        'start': 0,
        'start_formatted': '0:00',
        'texts': [],
    }
    
    for seg in segments:
        chunk_index = int(seg['start'] // chunk_seconds)
        expected_start = chunk_index * chunk_seconds
        
        if expected_start != current_chunk['start'] and current_chunk['texts']:
            # Save current chunk and start new one
            current_chunk['text'] = ' '.join(current_chunk['texts'])
            del current_chunk['texts']
            chunks.append(current_chunk)
            
            current_chunk = {
                'start': expected_start,
                'start_formatted': format_timestamp(expected_start),
                'texts': [],
            }
        
        current_chunk['texts'].append(seg['text'].replace('\n', ' '))
    
    # Don't forget the last chunk
    if current_chunk['texts']:
        current_chunk['text'] = ' '.join(current_chunk['texts'])
        del current_chunk['texts']
        chunks.append(current_chunk)
    
    result['chunks'] = chunks
    result['chunk_minutes'] = chunk_minutes
    
    return result


def format_timestamp(seconds: float) -> str:
    """Convert seconds to human-readable timestamp (H:MM:SS or M:SS)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


def search_in_transcript(
    video_id: str,
    query: str,
    context_segments: int = 2,
    languages: Optional[list[str]] = None,
) -> dict:
    """
    Search for specific terms within a video's transcript.
    
    Returns matching segments with surrounding context.
    
    Args:
        video_id: YouTube video ID or URL
        query: Search term (case-insensitive)
        context_segments: Number of segments before/after to include
        languages: Preferred languages
        
    Returns:
        dict with matching segments and their context
    """
    result = get_transcript(video_id, languages)
    
    if 'error' in result:
        return result
    
    segments = result['segments']
    query_lower = query.lower()
    matches = []
    
    for i, seg in enumerate(segments):
        if query_lower in seg['text'].lower():
            # Get context
            start_idx = max(0, i - context_segments)
            end_idx = min(len(segments), i + context_segments + 1)
            
            context_text = ' '.join(
                s['text'].replace('\n', ' ') 
                for s in segments[start_idx:end_idx]
            )
            
            matches.append({
                'timestamp': format_timestamp(seg['start']),
                'start_seconds': seg['start'],
                'matched_text': seg['text'],
                'context': context_text,
                'segment_index': i,
            })
    
    return {
        'video_id': result['video_id'],
        'query': query,
        'match_count': len(matches),
        'matches': matches,
        'language': result['language'],
        'is_generated': result['is_generated'],
    }
