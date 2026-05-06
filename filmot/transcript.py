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
import os
from dotenv import load_dotenv

from .proxy_pool import (
    classify_transport_error,
    get_pool,
)

load_dotenv()

# Default number of pool sessions to try on transport-class failures.
_POOL_RETRY_LIMIT = int(os.getenv("FILMOT_PROXY_RETRY_LIMIT", "4"))

# Global API instance - holds the "primary" non-pool client (direct, legacy
# Webshare via env vars, or an operator-supplied proxy from configure_proxy()).
# The dynamic Webshare pool is layered on top by get_transcript().
_api = None
_proxy_configured = False
_initialized = False
_proxy_source = "direct"


def _build_direct_api() -> YouTubeTranscriptApi:
    """Build a direct YouTubeTranscriptApi client."""
    return YouTubeTranscriptApi()


def _build_webshare_api(proxy_username: str, proxy_password: str) -> YouTubeTranscriptApi:
    """Build a Webshare-backed YouTubeTranscriptApi client."""
    from youtube_transcript_api.proxies import WebshareProxyConfig

    return YouTubeTranscriptApi(
        proxy_config=WebshareProxyConfig(
            proxy_username=proxy_username,
            proxy_password=proxy_password,
        )
    )


def _build_generic_proxy_api(http_proxy: Optional[str], https_proxy: Optional[str]) -> YouTubeTranscriptApi:
    """Build a generic HTTP/HTTPS proxy-backed YouTubeTranscriptApi client."""
    from youtube_transcript_api.proxies import GenericProxyConfig

    return YouTubeTranscriptApi(
        proxy_config=GenericProxyConfig(
            http_url=http_proxy,
            https_url=https_proxy or http_proxy,
        )
    )


def _primary_from_environment() -> tuple[YouTubeTranscriptApi, str, bool]:
    """Build the "primary" non-pool client from environment config.

    Order of precedence (skipping the dynamic pool, which is layered separately):

    1. ``WEBSHARE_PROXY_USERNAME`` + ``WEBSHARE_PROXY_PASSWORD`` (legacy single
       rotating endpoint via ``WebshareProxyConfig``).
    2. ``HTTP_PROXY`` / ``HTTPS_PROXY``.
    3. Direct connection.

    Returns ``(api, label, is_proxy)``.
    """
    webshare_user = os.getenv("WEBSHARE_PROXY_USERNAME")
    webshare_pass = os.getenv("WEBSHARE_PROXY_PASSWORD")
    if webshare_user and webshare_pass:
        try:
            return (
                _build_webshare_api(webshare_user, webshare_pass),
                "legacy-webshare",
                True,
            )
        except ImportError:
            pass

    http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
    https_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    if http_proxy or https_proxy:
        try:
            return (
                _build_generic_proxy_api(http_proxy, https_proxy),
                "env-proxy",
                True,
            )
        except ImportError:
            pass

    return _build_direct_api(), "direct", False


def _init_api() -> None:
    """Initialise the "primary" client (the non-pool route)."""
    global _api, _proxy_configured, _initialized, _proxy_source

    if _initialized:
        return

    _api, _proxy_source, _proxy_configured = _primary_from_environment()
    _initialized = True


def _resolve_proxy_mode() -> str:
    """Decide the active routing mode.

    ``FILMOT_PROXY_MODE`` may be set to ``auto``, ``proxy-only``, or
    ``direct-only``. When unset, we default to ``proxy-only`` whenever a
    Webshare API token is configured (typical AWS-host case where direct
    fetches are routinely blocked) and ``auto`` otherwise.
    """
    mode = (os.getenv("FILMOT_PROXY_MODE") or "").strip().lower()
    if mode in {"auto", "proxy-only", "direct-only"}:
        return mode
    return "proxy-only" if os.getenv("WEBSHARE_API_TOKEN") else "auto"


def configure_proxy(
    webshare_username: Optional[str] = None,
    webshare_password: Optional[str] = None,
    http_proxy: Optional[str] = None,
    https_proxy: Optional[str] = None,
) -> None:
    """Configure the transcript API to use a proxy as the *primary* client.

    The dynamic Webshare pool (``WEBSHARE_API_TOKEN``) is unaffected by this
    call; it remains as the first leg of the route ladder when present.

    For Webshare (legacy single rotating endpoint):
        configure_proxy(webshare_username="user", webshare_password="pass")

    For generic HTTP/HTTPS proxy:
        configure_proxy(http_proxy="http://user:pass@host:port")
    """
    global _api, _proxy_configured, _initialized, _proxy_source

    if webshare_username and webshare_password:
        _api = _build_webshare_api(webshare_username, webshare_password)
        _proxy_source = "legacy-webshare"
    elif http_proxy:
        _api = _build_generic_proxy_api(http_proxy, https_proxy)
        _proxy_source = "explicit-proxy"
    else:
        raise ValueError("Must provide either Webshare credentials or proxy URL")

    _proxy_configured = True
    _initialized = True


def get_api() -> YouTubeTranscriptApi:
    """Get the configured API instance."""
    _init_api()
    return _api


def disable_proxy() -> None:
    """Disable all proxy routes and force direct connection only.

    Sets ``FILMOT_PROXY_MODE=direct-only`` for the rest of this process so the
    dynamic pool is also skipped, then resets the primary client.
    """
    global _api, _proxy_configured, _initialized, _proxy_source
    os.environ["FILMOT_PROXY_MODE"] = "direct-only"
    _api = _build_direct_api()
    _proxy_configured = False
    _proxy_source = "direct"
    _initialized = True


def is_proxy_configured() -> bool:
    """Check if a proxy is configured."""
    _init_api()
    return _proxy_configured


def reset_api() -> None:
    """Reset to default API without proxy."""
    global _initialized
    _initialized = False
    _init_api()


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
    
    attempts: list[str] = []
    last_error: Optional[Exception] = None

    for label, api, on_outcome in _iter_routes():
        attempts.append(label)
        try:
            result = _fetch_transcript_from_api(
                api,
                video_id,
                languages=languages,
                preserve_formatting=preserve_formatting,
            )
        except (TranscriptsDisabled, VideoUnavailable, NoTranscriptFound) as terminal:
            # Caption-side terminal failures — not a transport problem.
            # Don't penalise the route; surface the error immediately.
            if on_outcome is not None:
                on_outcome("success", None)
            return _terminal_error_result(terminal, video_id, label)
        except Exception as exc:  # noqa: BLE001 - we re-raise via result dict
            kind = classify_transport_error(exc)
            if on_outcome is not None:
                on_outcome("failure", (kind or "other", str(exc)))
            last_error = exc
            if not kind:
                # Unknown / non-transport error — stop retrying.
                break
            continue

        # Success.
        if on_outcome is not None:
            on_outcome("success", None)
        if isinstance(result, dict) and "error" not in result:
            result["route"] = label
        return result

    error_msg = str(last_error) if last_error else "all routes exhausted"
    return {
        "error": error_msg,
        "video_id": video_id,
        "routes_tried": attempts,
    }


def _terminal_error_result(exc: Exception, video_id: str, route: str) -> dict:
    if isinstance(exc, TranscriptsDisabled):
        msg = "Transcripts are disabled for this video"
    elif isinstance(exc, VideoUnavailable):
        msg = "Video is unavailable"
    else:
        msg = str(exc) or "No transcript available"
    return {"error": msg, "video_id": video_id, "route": route}


def _iter_routes():
    """Yield ``(label, api_client, on_outcome)`` tuples per the active mode.

    ``on_outcome`` is a callable invoked once per attempt with either
    ``("success", None)`` or ``("failure", (kind, summary))`` so the pool can
    update health stats. ``None`` for non-pool routes.
    """
    mode = _resolve_proxy_mode()
    pool = None if mode == "direct-only" else get_pool()

    # 1. Pool sessions (try up to N).
    if pool is not None and mode in {"auto", "proxy-only"}:
        for _ in range(_POOL_RETRY_LIMIT):
            session = pool.pick()
            if session is None:
                break
            url = pool.proxy_url(session)
            api = _build_generic_proxy_api(url, url)

            def _cb(outcome, info, _s=session, _p=pool):
                if outcome == "success":
                    _p.report_success(_s)
                else:
                    kind, summary = info
                    _p.report_failure(_s, kind or "other", summary=summary)

            yield (f"pool:{session.id}", api, _cb)

    # 2. Primary client (direct, env-proxy, legacy-webshare, or operator-supplied).
    if mode != "proxy-only" or pool is None:
        yield (_proxy_source, get_api(), None)


def _fetch_transcript_from_api(
    api: YouTubeTranscriptApi,
    video_id: str,
    languages: list[str],
    preserve_formatting: bool,
) -> dict:
    """Fetch transcript using a specific API client.

    Caption-side terminal failures (``TranscriptsDisabled``, ``VideoUnavailable``,
    ``NoTranscriptFound`` after translation attempts) propagate as exceptions so
    the route iterator can distinguish them from transport failures.
    """
    try:
        # Try the simple fetch first
        try:
            transcript = api.fetch(video_id, languages=languages, preserve_formatting=preserve_formatting)
        except NoTranscriptFound:
            # Try to list and translate
            transcript_list = api.list(video_id)
            translated = None
            for t in transcript_list:
                if t.is_translatable:
                    translated = t.translate('en').fetch(preserve_formatting=preserve_formatting)
                    break
            if translated:
                transcript = translated
            else:
                # Bubble up as a terminal NoTranscriptFound so the route iterator
                # treats it as caption-side, not transport-side.
                raise NoTranscriptFound(video_id, languages, transcript_list)
        
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
        
    except (TranscriptsDisabled, VideoUnavailable, NoTranscriptFound):
        # Re-raise terminal errors so the route iterator can short-circuit.
        raise


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


def get_transcript_with_fallback(
    video_id: str,
    languages: Optional[list[str]] = None,
    preserve_formatting: bool = False,
    use_aws_fallback: bool = True,
    aws_progress_callback=None,
) -> dict:
    """
    Fetch transcript with AWS Transcribe fallback.
    
    Attempts to get transcript using youtube-transcript-api first.
    If that fails (captions disabled, video unavailable, etc.) and
    use_aws_fallback is True, falls back to AWS Transcribe.
    
    AWS Transcribe flow:
    1. Download audio using yt-dlp
    2. Upload to S3
    3. Start transcription job with language auto-detection
    4. Poll for completion
    5. Fetch result and cleanup
    
    Requires:
    - AWS credentials configured (profile 'APIBoss')
    - yt-dlp installed
    - boto3 and requests packages
    
    Args:
        video_id: YouTube video ID or URL
        languages: Preferred languages for youtube-transcript-api
        preserve_formatting: Keep original line breaks
        use_aws_fallback: Enable AWS Transcribe fallback
        aws_progress_callback: Optional callback(stage, message) for AWS progress
    
    Returns:
        dict with:
            - video_id: The video ID
            - language: Language code
            - is_generated: Whether it's auto-generated (True for AWS)
            - full_text: Complete transcript
            - source: 'youtube' or 'aws_transcribe'
            - segments: List of segments (empty for AWS)
            - error: Error message if both methods fail
    """
    video_id = extract_video_id(video_id)
    
    # Try YouTube transcript API first
    result = get_transcript(video_id, languages, preserve_formatting)
    
    if 'error' not in result:
        result['source'] = 'youtube'
        return result
    
    youtube_error = result.get('error', 'Unknown error')
    
    # If fallback disabled, return the error
    if not use_aws_fallback:
        return result
    
    # Try AWS Transcribe fallback
    try:
        from .aws_transcribe import transcribe_video, check_dependencies, AWSTranscribeError
        
        # Check dependencies first
        deps_ok, deps_msg = check_dependencies()
        if not deps_ok:
            return {
                'error': f"YouTube error: {youtube_error}. AWS fallback unavailable: {deps_msg}",
                'video_id': video_id,
            }
        
        # Run transcription
        transcript_text, detected_language = transcribe_video(
            video_id,
            identify_language=True,
            cleanup=True,
            progress_callback=aws_progress_callback,
        )
        
        return {
            'video_id': video_id,
            'language': detected_language or 'unknown',
            'is_generated': True,
            'segments': [],  # AWS doesn't provide timestamps in simple mode
            'full_text': transcript_text,
            'duration_seconds': 0,
            'segment_count': 0,
            'source': 'aws_transcribe',
        }
        
    except Exception as e:
        return {
            'error': f"YouTube error: {youtube_error}. AWS fallback error: {str(e)}",
            'video_id': video_id,
        }
