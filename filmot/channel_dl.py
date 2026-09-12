"""
Channel Transcript Downloader - Download all transcripts from a YouTube channel.

Downloads every transcript from a channel and stores them locally with a manifest
for resumable downloads. Designed to build local knowledge corpora that AI agents
can mine for insights.

Storage structure:
    .filmot_data/
        channels/
            chat-with-traders/
                manifest.json          # Download state, channel metadata, video index
                transcripts/
                    Co_GYku903k.json   # Individual transcript files
                    HSuE19H1-K0.json
                    ...
"""

import json
import os
import re
import time
import unicodedata
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Callable
from urllib.parse import unquote, urlsplit

# Importing config runs the explicit process/user/project environment sequence.
from . import config as _config  # noqa: F401
from .paths import project_data_dir
from .redaction import redact_sensitive_text


CHANNEL_METADATA_TTL_DAYS = 30
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_CHANNEL_HANDLE_SEPARATORS = frozenset("_-.·")
_YOUTUBE_CHANNEL_HOSTS = {"youtube.com", "www.youtube.com"}


def _valid_channel_handle(value: str) -> bool:
    """Validate the safe cross-script shape; YouTube owns final acceptance."""
    if not 1 <= len(value) <= 30:
        return False
    if not value[0].isalnum() or not value[-1].isalnum():
        return False
    return all(
        character.isalnum()
        or unicodedata.category(character).startswith("M")
        or character in _CHANNEL_HANDLE_SEPARATORS
        for character in value
    )


class YouTubeChannelAPIError(RuntimeError):
    """Credential-safe failure from a YouTube channel API operation.

    ``googleapiclient.errors.HttpError`` retains the HTTP response and the full
    request URI. YouTube puts API keys in that URI, so allowing the native
    exception to escape can leak a credential through logs, tracebacks, or
    callers inspecting the exception. This domain error deliberately retains
    only small, sanitized scalar diagnostics.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        status: Optional[int] = None,
        reason: str = "request failed",
    ) -> None:
        self.endpoint = str(endpoint)
        self.status = status
        self.reason = str(reason)
        details = [f"HTTP {status}" if status is not None else None, self.reason]
        rendered = "; ".join(detail for detail in details if detail)
        super().__init__(f"YouTube Data API {self.endpoint} failed ({rendered})")

    def to_dict(self) -> dict:
        """Return the small, credential-safe diagnostic used in result data."""
        return {
            "type": type(self).__name__,
            "endpoint": self.endpoint,
            "status": self.status,
            "reason": self.reason,
            "message": str(self),
        }


def _utc_now() -> datetime:
    """Return a timezone-aware UTC clock value (split out for deterministic tests)."""
    return datetime.now(timezone.utc)


def _isoformat_utc(value: datetime) -> str:
    """Render an aware datetime in the API's compact UTC spelling."""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _optional_int(value: object) -> Optional[int]:
    """Parse an API counter while preserving absent or malformed values as unknown."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return parsed if parsed >= 0 else None


def _safe_api_failure(
    error: Exception,
    endpoint: str,
    api_key: str,
) -> YouTubeChannelAPIError:
    """Copy useful diagnostics out of an API exception without retaining it."""
    status = getattr(error, "status_code", None)
    response = getattr(error, "resp", None)
    if status is None and response is not None:
        status = getattr(response, "status", None)
        if status is None and isinstance(response, dict):
            status = response.get("status")
    status = _optional_int(status)

    reason_code = ""
    message = ""
    content = getattr(error, "content", None)
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8", errors="replace")
        except Exception:
            content = None
    if isinstance(content, str):
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            body = payload.get("error")
            if isinstance(body, dict):
                raw_message = body.get("message")
                if isinstance(raw_message, str):
                    message = raw_message
                errors = body.get("errors")
                if isinstance(errors, list):
                    for detail in errors:
                        if isinstance(detail, dict) and isinstance(detail.get("reason"), str):
                            reason_code = detail["reason"]
                            break

    if not message:
        raw_reason = getattr(error, "reason", None)
        if isinstance(raw_reason, str):
            message = raw_reason
    if not message:
        message = str(error)

    if reason_code and reason_code.lower() not in message.lower():
        message = f"{reason_code}: {message}"
    safe_reason = redact_sensitive_text(message, (api_key,))
    safe_reason = " ".join(safe_reason.split())[:1000] or "request failed"
    return YouTubeChannelAPIError(endpoint, status=status, reason=safe_reason)


def _execute_youtube_request(request_factory, endpoint: str, api_key: str) -> dict:
    """Build and execute one API request behind a credential-erasing boundary."""
    request = None
    failure = None
    try:
        request = request_factory()
        result = request.execute()
        if not isinstance(result, dict):
            raise TypeError("malformed response: expected a JSON object")
        return result
    except Exception as error:
        failure = _safe_api_failure(error, endpoint, api_key)

    # Raise after leaving the except suite so the original exception is not
    # retained as ``__context__``. Clear objects whose internals can hold the
    # developer key before this frame becomes part of the safe traceback.
    request = None
    request_factory = None
    api_key = ""
    raise failure


def _build_youtube_client(api_key: str):
    """Create the discovery client while applying the same safe error boundary."""
    from googleapiclient.discovery import build

    failure = None
    try:
        return build("youtube", "v3", developerKey=api_key)
    except Exception as error:
        failure = _safe_api_failure(error, "client initialization", api_key)
    api_key = ""
    raise failure


def _parse_channel_reference(channel_reference: str) -> tuple[str, str]:
    """Return ``(channels.list filter, value)`` for an exact channel reference.

    Accepted forms are a canonical ``UC...`` identifier, ``@handle``, and the
    corresponding canonical YouTube channel or handle URL. Legacy custom-name
    and user URLs are intentionally rejected because resolving them would
    require fuzzy guessing.
    """
    if not isinstance(channel_reference, str) or not channel_reference.strip():
        raise ValueError("A YouTube channel ID, @handle, or canonical URL is required")

    value = channel_reference.strip()
    if _CHANNEL_ID_RE.fullmatch(value):
        return "id", value
    if value.startswith("@"):
        handle = unicodedata.normalize("NFC", value[1:])
        if _valid_channel_handle(handle):
            return "forHandle", handle
        raise ValueError("Invalid YouTube @handle")

    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower()
    try:
        has_port = parsed.port is not None
    except ValueError:
        has_port = True
    if (
        parsed.scheme != "https"
        or hostname not in _YOUTUBE_CHANNEL_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or has_port
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Unsupported YouTube channel reference; use a UC channel ID, "
            "@handle, /channel/UC... URL, or /@handle URL"
        )

    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) == 2 and parts[0] == "channel" and _CHANNEL_ID_RE.fullmatch(parts[1]):
        return "id", parts[1]
    if len(parts) == 1 and parts[0].startswith("@"):
        handle = unicodedata.normalize("NFC", parts[0][1:])
        if _valid_channel_handle(handle):
            return "forHandle", handle

    raise ValueError(
        "Unsupported YouTube channel URL; use /channel/UC... or /@handle"
    )


# ---------------------------------------------------------------------------
# Local query / proximity search helpers
# ---------------------------------------------------------------------------

def _tokenize_words(text: str) -> list[tuple[str, int]]:
    """Return list of (lowercase_word, char_offset) for every word in *text*."""
    return [(m.group().lower(), m.start()) for m in re.finditer(r"\S+", text)]


def _parse_proximity_query(query: str):
    """Parse a query string looking for proximity operators.

    Supported syntaxes (case-insensitive):
        "phrase1" NEAR/N "phrase2"                  – two phrases within N words
        ("alt1" | "alt2") NEAR/N "phrase2"         – OR group on left side
        "phrase1" NEAR/N ("alt1" | "alt2")         – OR group on right side
        ("alt1" | "alt2") NEAR/N ("alt3" | "alt4") – OR groups on both sides
        "word1 word2"~N                            – words in the phrase within N words of each other

    Returns one of:
        ('plain', query_str)
        ('near', left_terms, right_terms, distance)
        ('tilde', words_list, distance)
    """
    # --- NEAR/N between quoted phrases or parenthesized OR groups ---
    m = re.match(r'''^\s*(.+?)\s+NEAR\s*/\s*(\d+)\s+(.+?)\s*$''', query, re.IGNORECASE)
    if m:
        left_terms = _parse_near_operand(m.group(1))
        right_terms = _parse_near_operand(m.group(3))
        if left_terms and right_terms:
            return ('near', left_terms, right_terms, int(m.group(2)))

    # --- "words"~N  (tilde proximity) ---
    m = re.match(r'''^\s*"([^"]+)"~(\d+)\s*$''', query)
    if m:
        words = m.group(1).strip().split()
        if len(words) >= 2:
            return ('tilde', words, int(m.group(2)))

    return ('plain', query)


def _looks_like_proximity(query: str) -> bool:
    """True if *query* contains proximity operator syntax (NEAR/N or "..."~N)."""
    return bool(re.search(r'NEAR\s*/\s*\d+|"~\d+', query, re.IGNORECASE))


def _parse_near_operand(operand: str) -> Optional[list[str]]:
    """Parse one side of a NEAR/N query.

    Each operand can be either a single quoted phrase or a parenthesized OR
    group of quoted phrases.
    """
    operand = operand.strip()

    single = re.fullmatch(r'''\s*"([^"]+)"\s*''', operand)
    if single:
        return [single.group(1).strip()]

    grouped = re.fullmatch(
        r'''\(\s*"[^"]+"\s*(?:\|\s*"[^"]+"\s*)+\)''',
        operand,
    )
    if grouped:
        return [term.strip() for term in re.findall(r'''"([^"]+)"''', operand)]

    return None


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping or touching character spans."""
    if not spans:
        return []

    spans = sorted(spans)
    merged = [spans[0]]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _phrase_occurrences(text_lower: str, phrase: str) -> list[tuple[int, int]]:
    """Find (start_char, end_char) of whole-word occurrences of *phrase*.

    Words in the phrase may be separated by any whitespace run in the text
    (spaces, newlines). Boundaries prevent substring hits like "count"
    matching inside "accountability".
    """
    pattern = r'(?<!\w)' + r'\s+'.join(re.escape(w) for w in phrase.split()) + r'(?!\w)'
    return [(m.start(), m.end()) for m in re.finditer(pattern, text_lower)]


def _snap_word_index(token_starts: list[int], char_pos: int) -> int:
    """Binary search for the token containing (or nearest to) *char_pos*."""
    import bisect
    i = bisect.bisect_left(token_starts, char_pos)
    if i == 0:
        return 0
    if i >= len(token_starts):
        return len(token_starts) - 1
    before = token_starts[i - 1]
    after = token_starts[i]
    if char_pos - before <= after - char_pos:
        return i - 1
    return i


def _find_near_matches(text: str, phrase1: str, phrase2: str, distance: int):
    """Find positions where *phrase1* and *phrase2* appear within *distance* words.

    Distance is measured between the nearest edges of the two phrases, so a
    multi-word phrase isn't penalized by its own length.

    Returns list of (start_char, end_char) spans covering both matches.
    """
    text_lower = text.lower()
    p1 = phrase1.lower()
    p2 = phrase2.lower()

    occ1 = _phrase_occurrences(text_lower, p1)
    occ2 = _phrase_occurrences(text_lower, p2)

    if not occ1 or not occ2:
        return []

    tokens = _tokenize_words(text)
    token_starts = [coff for (_, coff) in tokens]

    n1 = len(p1.split())
    n2 = len(p2.split())

    matches = []
    for s1, e1 in occ1:
        w1_start = _snap_word_index(token_starts, s1)
        w1_end = w1_start + n1 - 1
        for s2, e2 in occ2:
            w2_start = _snap_word_index(token_starts, s2)
            w2_end = w2_start + n2 - 1
            if w2_start > w1_end:
                gap = w2_start - w1_end
            elif w1_start > w2_end:
                gap = w1_start - w2_end
            else:
                gap = 0  # overlapping spans
            if gap <= distance:
                matches.append((min(s1, s2), max(e1, e2)))

    return _merge_spans(matches)


def _find_grouped_near_matches(
    text: str,
    left_terms: list[str],
    right_terms: list[str],
    distance: int,
) -> list[tuple[int, int]]:
    """Find NEAR/N spans across every left/right term combination."""
    spans = []
    for left in left_terms:
        for right in right_terms:
            spans.extend(_find_near_matches(text, left, right, distance))
    return _merge_spans(spans)


def _find_tilde_matches(text: str, words: list[str], distance: int):
    """Find positions where all *words* appear within *distance* words of each other.

    Returns list of (start_char, end_char) spans.
    """
    text_lower = text.lower()
    words_lower = [w.lower() for w in words]

    word_occurrences = [_phrase_occurrences(text_lower, w) for w in words_lower]
    if any(len(p) == 0 for p in word_occurrences):
        return []

    tokens = _tokenize_words(text)
    token_starts = [coff for (_, coff) in tokens]

    # For 2 words, simple pair check. For N words, check all combos of positions.
    # Since most queries will be 2-3 words, brute-force is fine.
    from itertools import product

    matches = []
    for combo in product(*word_occurrences):
        word_indices = [_snap_word_index(token_starts, s) for s, _ in combo]
        # A repeated query word must match distinct occurrences in the text
        if len(set(word_indices)) < len(word_indices):
            continue
        span = max(word_indices) - min(word_indices)
        if span <= distance:
            span_start = min(s for s, _ in combo)
            span_end = max(e for _, e in combo)
            matches.append((span_start, span_end))

    return _merge_spans(matches)


def _slugify(name: str) -> str:
    """Convert channel name to filesystem-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'[^a-z0-9\-]', '', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-') or 'unknown-channel'


def get_channel_info(channel_id: str) -> dict:
    """
    Resolve an exact channel ID/handle/URL and fetch public channel metadata.

    ``channel_id`` retains its historic parameter name for keyword-call
    compatibility, but accepts a canonical ``UC...`` ID, ``@handle``,
    ``youtube.com/channel/UC...`` URL, or ``youtube.com/@handle`` URL.

    Returns:
        Channel identity, public snippet/statistics/topic metadata, uploads
        playlist ID, and 30-day observation freshness timestamps.
    """
    failure = None
    try:
        return _get_channel_info(channel_id)
    except YouTubeChannelAPIError as error:
        # The implementation necessarily owns the developer key and discovery
        # client while it talks to Google. Strip all of those implementation
        # frames before exposing the small domain error to callers.
        failure = error.with_traceback(None)
    except ValueError as error:
        # Configuration, exact-reference validation, and not-found conditions
        # intentionally remain ValueError for backward compatibility. Rebuild
        # the scalar exception so it cannot retain the sensitive helper frame.
        failure = ValueError(redact_sensitive_text(error))
    except Exception as error:
        # Unexpected implementation exceptions must not expose their text or
        # object graph: either may contain a discovery client or request URI.
        failure = YouTubeChannelAPIError(
            "channel lookup",
            reason="unexpected {}".format(type(error).__name__),
        )
    raise failure


def _get_channel_info(channel_id: str) -> dict:
    """Sensitive implementation for :func:`get_channel_info`."""
    api_key = os.getenv("YOUTUBE_API_KEY", "")
    if not api_key:
        raise ValueError("YOUTUBE_API_KEY not found in .env")

    filter_name, filter_value = _parse_channel_reference(channel_id)
    yt = _build_youtube_client(api_key)
    params = {
        "part": "snippet,contentDetails,statistics,topicDetails",
        filter_name: filter_value,
    }
    response = _execute_youtube_request(
        lambda: yt.channels().list(**params),
        "channels.list",
        api_key,
    )

    items = response.get("items")
    if not isinstance(items, list):
        raise YouTubeChannelAPIError(
            "channels.list",
            reason="malformed response: items is not an array",
        )
    if not items:
        safe_reference = redact_sensitive_text(channel_id, (api_key,))
        raise ValueError(f"Channel not found: {safe_reference}")
    ch = items[0]
    if not isinstance(ch, dict):
        raise YouTubeChannelAPIError(
            "channels.list",
            reason="malformed response: channel item is not an object",
        )

    resolved_channel_id = ch.get("id")
    if not isinstance(resolved_channel_id, str) or not resolved_channel_id:
        raise YouTubeChannelAPIError(
            "channels.list",
            reason="malformed response: channel ID is missing",
        )
    if filter_name == "id" and resolved_channel_id != filter_value:
        raise YouTubeChannelAPIError(
            "channels.list",
            reason="response channel ID did not match the requested ID",
        )

    snippet = ch.get("snippet") if isinstance(ch.get("snippet"), dict) else {}
    content_details = (
        ch.get("contentDetails") if isinstance(ch.get("contentDetails"), dict) else {}
    )
    related_playlists = content_details.get("relatedPlaylists")
    if not isinstance(related_playlists, dict):
        related_playlists = {}
    uploads_playlist_id = related_playlists.get("uploads")
    if not isinstance(uploads_playlist_id, str) or not uploads_playlist_id:
        raise YouTubeChannelAPIError(
            "channels.list",
            reason="malformed response: uploads playlist ID is missing",
        )

    statistics = ch.get("statistics") if isinstance(ch.get("statistics"), dict) else {}
    topic_details = ch.get("topicDetails") if isinstance(ch.get("topicDetails"), dict) else {}
    topic_ids = topic_details.get("topicIds")
    topic_categories = topic_details.get("topicCategories")
    observed = _utc_now()
    expires = observed + timedelta(days=CHANNEL_METADATA_TTL_DAYS)

    return {
        # Original public interface (with unknown counters now preserved as None).
        "channel_id": resolved_channel_id,
        "name": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "uploads_playlist_id": uploads_playlist_id,
        "subscriber_count": _optional_int(statistics.get("subscriberCount")),
        "video_count": _optional_int(statistics.get("videoCount")),
        # Additional public metadata useful for cataloging and cache freshness.
        "view_count": _optional_int(statistics.get("viewCount")),
        "hidden_subscriber_count": (
            statistics.get("hiddenSubscriberCount")
            if isinstance(statistics.get("hiddenSubscriberCount"), bool)
            else None
        ),
        "custom_url": snippet.get("customUrl"),
        "published_at": snippet.get("publishedAt"),
        "country": snippet.get("country"),
        "default_language": snippet.get("defaultLanguage"),
        "topic_ids": [item for item in topic_ids if isinstance(item, str)]
        if isinstance(topic_ids, list)
        else [],
        "topic_categories": [item for item in topic_categories if isinstance(item, str)]
        if isinstance(topic_categories, list)
        else [],
        "resolved_by": "channel_id" if filter_name == "id" else "handle",
        "observed_at": _isoformat_utc(observed),
        "expires_at": _isoformat_utc(expires),
    }


def enumerate_uploads_detailed(
    uploads_playlist_id: str,
    *,
    max_pages: int,
    max_items: int,
    page_token: Optional[str] = None,
    progress_callback: Optional[Callable] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> dict:
    """Return one bounded, resumable slice of an uploads playlist.

    ``max_pages`` bounds successful API pages and ``max_items`` bounds unique
    video rows. ``page_token`` resumes at an exact YouTube continuation token.
    The result contains ``videos``, the effective ``request``, detailed
    ``coverage``, and bounded ``warnings``/``errors`` lists. A failure before
    the first usable page raises :class:`YouTubeChannelAPIError`; a later
    failure is reported as partial while retaining earlier pages.

    ``cancel_check`` is called immediately before each page request. Returning
    true stops without making that request and leaves its token in coverage so
    the caller can resume. The hook is deliberately cooperative: it does not
    interrupt a request already executing.
    """
    failure = None
    try:
        return _enumerate_uploads_detailed_impl(
            uploads_playlist_id,
            max_pages=max_pages,
            max_items=max_items,
            page_token=page_token,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
            preserve_partial=True,
            allow_unbounded=False,
        )
    except YouTubeChannelAPIError as error:
        # Playlist pagination keeps one credential-bearing discovery client
        # alive across pages. Never retain that implementation frame on a
        # public exception traceback.
        failure = error.with_traceback(None)
    except ValueError as error:
        # Missing-key validation remains a ValueError, detached from the
        # sensitive paginator implementation.
        failure = ValueError(redact_sensitive_text(error))
    except Exception as error:
        failure = YouTubeChannelAPIError(
            "playlist enumeration",
            reason="unexpected {}".format(type(error).__name__),
        )
    # A caller callback can retain arbitrary state. Do not leave it attached
    # to the only provider frame exposed by the detached public traceback.
    progress_callback = None
    cancel_check = None
    page_token = None
    raise failure


def list_all_video_ids(
    uploads_playlist_id: str,
    progress_callback: Optional[Callable] = None,
) -> list[dict]:
    """Enumerate every upload, preserving the original list-returning API.

    New control-plane callers should prefer :func:`enumerate_uploads_detailed`.
    This compatibility wrapper intentionally retains the historical unbounded
    traversal and fail-fast behavior.
    """
    failure = None
    try:
        return _list_all_video_ids(uploads_playlist_id, progress_callback)
    except YouTubeChannelAPIError as error:
        failure = error.with_traceback(None)
    except ValueError as error:
        failure = ValueError(redact_sensitive_text(error))
    except Exception as error:
        failure = YouTubeChannelAPIError(
            "playlist enumeration",
            reason="unexpected {}".format(type(error).__name__),
        )
    progress_callback = None
    raise failure


def _validate_upload_enumeration_request(
    uploads_playlist_id: str,
    max_pages: Optional[int],
    max_items: Optional[int],
    page_token: Optional[str],
    *,
    allow_unbounded: bool,
) -> tuple[str, Optional[str]]:
    """Validate public pagination inputs before creating an API client."""
    if not isinstance(uploads_playlist_id, str) or not uploads_playlist_id.strip():
        raise ValueError("uploads_playlist_id must be a non-empty string")

    for name, value in (("max_pages", max_pages), ("max_items", max_items)):
        if value is None and allow_unbounded:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")

    if page_token is not None:
        if not isinstance(page_token, str) or not page_token.strip():
            raise ValueError("page_token must be a non-empty string when provided")
        page_token = page_token.strip()
    return uploads_playlist_id.strip(), page_token


def _upload_video_row(item: dict, uploads_playlist_id: str) -> Optional[dict]:
    """Normalize one playlistItems row, or return ``None`` when no ID exists."""
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    content_details = (
        item.get("contentDetails")
        if isinstance(item.get("contentDetails"), dict)
        else {}
    )
    status = item.get("status") if isinstance(item.get("status"), dict) else {}
    resource = (
        snippet.get("resourceId")
        if isinstance(snippet.get("resourceId"), dict)
        else {}
    )
    content_video_id = content_details.get("videoId")
    resource_video_id = resource.get("videoId")
    if isinstance(content_video_id, str) and content_video_id.strip():
        video_id = content_video_id.strip()
    elif isinstance(resource_video_id, str) and resource_video_id.strip():
        video_id = resource_video_id.strip()
    else:
        return None

    description = snippet.get("description", "")
    if not isinstance(description, str):
        description = ""
    return {
        # Original keys and semantics.
        "video_id": video_id,
        "title": snippet.get("title", "")
        if isinstance(snippet.get("title", ""), str)
        else "",
        "published_at": snippet.get("publishedAt", "")
        if isinstance(snippet.get("publishedAt", ""), str)
        else "",
        "description": description[:500],
        # Exact playlist provenance and channel ownership metadata.
        "playlist_item_id": item.get("id")
        if isinstance(item.get("id"), str)
        else None,
        "playlist_id": snippet.get("playlistId")
        if isinstance(snippet.get("playlistId"), str)
        and snippet.get("playlistId")
        else uploads_playlist_id,
        "position": _optional_int(snippet.get("position")),
        "channel_id": snippet.get("channelId")
        if isinstance(snippet.get("channelId"), str)
        else None,
        "channel_title": snippet.get("channelTitle")
        if isinstance(snippet.get("channelTitle"), str)
        else None,
        "video_owner_channel_id": snippet.get("videoOwnerChannelId")
        if isinstance(snippet.get("videoOwnerChannelId"), str)
        else None,
        "video_owner_channel_title": snippet.get("videoOwnerChannelTitle")
        if isinstance(snippet.get("videoOwnerChannelTitle"), str)
        else None,
        "video_published_at": content_details.get("videoPublishedAt")
        if isinstance(content_details.get("videoPublishedAt"), str)
        else None,
        "privacy_status": status.get("privacyStatus")
        if isinstance(status.get("privacyStatus"), str)
        else None,
    }


def _enumerate_uploads_detailed_impl(
    uploads_playlist_id: str,
    *,
    max_pages: Optional[int],
    max_items: Optional[int],
    page_token: Optional[str],
    progress_callback: Optional[Callable],
    cancel_check: Optional[Callable[[], bool]],
    preserve_partial: bool,
    allow_unbounded: bool,
) -> dict:
    """Sensitive paginator shared by the detailed and compatibility APIs."""
    uploads_playlist_id, page_token = _validate_upload_enumeration_request(
        uploads_playlist_id,
        max_pages,
        max_items,
        page_token,
        allow_unbounded=allow_unbounded,
    )
    if progress_callback is not None and not callable(progress_callback):
        raise ValueError("progress_callback must be callable when provided")
    if cancel_check is not None and not callable(cancel_check):
        raise ValueError("cancel_check must be callable when provided")

    api_key = os.getenv("YOUTUBE_API_KEY", "")
    if not api_key:
        raise ValueError("YOUTUBE_API_KEY not found in .env")

    yt = _build_youtube_client(api_key)
    observation_started = _utc_now()

    videos: list[dict] = []
    seen_video_ids: set[str] = set()
    current_page_token = page_token
    next_page_token = page_token
    seen_page_tokens: set[str] = set()
    pages_attempted = 0
    pages_fetched = 0
    items_seen = 0
    candidates_fetched = 0
    malformed_items = 0
    idless_items = 0
    duplicates = 0
    approximate_total = None
    results_per_page = None
    stopping_reason = "exhausted"
    partial = False
    warnings: list[str] = []
    errors: list[dict] = []

    while True:
        if cancel_check is not None:
            callback_failure = None
            try:
                cancelled = bool(cancel_check())
            except Exception as error:
                callback_failure = _safe_api_failure(
                    error,
                    "playlist cancellation callback",
                    api_key,
                )
                cancelled = False
            if callback_failure is not None:
                cancel_check = None
                raise callback_failure
            if cancelled:
                stopping_reason = "cancelled"
                partial = True
                warnings.append(
                    "Upload enumeration was cancelled before the next page request."
                )
                break

        pages_attempted += 1
        if current_page_token:
            seen_page_tokens.add(current_page_token)
        remaining = (
            50 if max_items is None else min(50, max_items - len(videos))
        )
        params = {
            "part": "snippet,contentDetails,status",
            "playlistId": uploads_playlist_id,
            "maxResults": remaining,
            "pageToken": current_page_token,
        }
        try:
            response = _execute_youtube_request(
                lambda: yt.playlistItems().list(**params),
                "playlistItems.list",
                api_key,
            )
        except YouTubeChannelAPIError as error:
            if pages_fetched == 0 or not preserve_partial:
                yt = None
                params = {}
                api_key = ""
                raise
            stopping_reason = "partial_failure"
            partial = True
            errors.append({
                "stage": "enumeration",
                "page": pages_attempted,
                **error.to_dict(),
            })
            warnings.append(
                "Upload enumeration stopped after a later page failed; "
                "earlier pages were preserved."
            )
            break

        items = response.get("items")
        if not isinstance(items, list):
            response_failure = YouTubeChannelAPIError(
                "playlistItems.list",
                reason="malformed response: items is not an array",
            )
            if pages_fetched == 0 or not preserve_partial:
                raise response_failure
            stopping_reason = "partial_failure"
            partial = True
            errors.append({
                "stage": "enumeration",
                "page": pages_attempted,
                **response_failure.to_dict(),
            })
            warnings.append(
                "Upload enumeration stopped after a malformed later page; "
                "earlier pages were preserved."
            )
            break

        pages_fetched += 1

        for item in items:
            items_seen += 1
            if not isinstance(item, dict):
                malformed_items += 1
                continue
            video = _upload_video_row(item, uploads_playlist_id)
            if video is None:
                idless_items += 1
                continue
            candidates_fetched += 1
            video_id = video["video_id"]
            if video_id in seen_video_ids:
                duplicates += 1
                continue
            seen_video_ids.add(video_id)
            videos.append(video)
            if max_items is not None and len(videos) >= max_items:
                break

        if progress_callback:
            page_info = response.get("pageInfo")
            if not isinstance(page_info, dict):
                page_info = {}
            total = _optional_int(page_info.get("totalResults"))
            if total is None:
                total = len(videos)
            callback_failure = None
            try:
                progress_callback(len(videos), total, pages_fetched)
            except Exception as error:
                callback_failure = _safe_api_failure(
                    error,
                    "playlist progress callback",
                    api_key,
                )
            if callback_failure is not None:
                progress_callback = None
                raise callback_failure

        page_info = response.get("pageInfo")
        if isinstance(page_info, dict):
            total = _optional_int(page_info.get("totalResults"))
            per_page = _optional_int(page_info.get("resultsPerPage"))
            if total is not None:
                approximate_total = total
            if per_page is not None:
                results_per_page = per_page

        token = response.get("nextPageToken")
        if token is None or token == "":
            next_page_token = None
            stopping_reason = "exhausted"
            break
        if not isinstance(token, str):
            token_failure = YouTubeChannelAPIError(
                "playlistItems.list",
                reason="malformed response: nextPageToken is not a string",
            )
            if not preserve_partial:
                raise token_failure
            next_page_token = None
            stopping_reason = "partial_failure"
            partial = True
            errors.append({
                "stage": "pagination",
                "page": pages_fetched,
                **token_failure.to_dict(),
            })
            warnings.append(
                "Upload enumeration stopped because pagination metadata was malformed."
            )
            break
        if token in seen_page_tokens:
            token_failure = YouTubeChannelAPIError(
                "playlistItems.list",
                reason="pagination returned a repeated nextPageToken",
            )
            if not preserve_partial:
                raise token_failure
            next_page_token = None
            stopping_reason = "partial_failure"
            partial = True
            errors.append({
                "stage": "pagination",
                "page": pages_fetched,
                **token_failure.to_dict(),
            })
            warnings.append(
                "Upload enumeration stopped because YouTube repeated a page token."
            )
            break

        next_page_token = token
        if max_items is not None and len(videos) >= max_items:
            stopping_reason = "item_budget"
            break
        if max_pages is not None and pages_fetched >= max_pages:
            stopping_reason = "page_budget"
            break
        current_page_token = token

    if malformed_items:
        warnings.append("Malformed non-object upload playlist items were skipped.")
    if idless_items:
        warnings.append("Upload playlist items without a usable video ID were skipped.")
    if duplicates:
        warnings.append("Duplicate upload video IDs were skipped.")

    observed_at = (
        _isoformat_utc(observation_started) if pages_fetched else None
    )
    expires_at = (
        _isoformat_utc(
            observation_started + timedelta(days=CHANNEL_METADATA_TTL_DAYS)
        )
        if pages_fetched
        else None
    )

    return {
        "provider": "youtube-data-api-v3",
        "videos": videos,
        "request": {
            "uploads_playlist_id": uploads_playlist_id,
            "max_pages": max_pages,
            "max_items": max_items,
            "page_token": page_token,
            "page_budget": max_pages,
            "item_budget": max_items,
            "initial_page_token": page_token,
        },
        "observed_at": observed_at,
        "expires_at": expires_at,
        "coverage": {
            "pages_attempted": pages_attempted,
            "pages_fetched": pages_fetched,
            "api_calls": pages_attempted,
            "items_seen": items_seen,
            "candidates_fetched": candidates_fetched,
            "unique_results": len(videos),
            "returned": len(videos),
            "duplicates_skipped": duplicates,
            "malformed_items_skipped": malformed_items,
            "idless_items_skipped": idless_items,
            "next_page_token": next_page_token,
            "page_info": {
                "approximate_total_results": approximate_total,
                "results_per_page": results_per_page,
            },
            "approximate_total": approximate_total,
            "stopping_reason": stopping_reason,
            "partial": partial,
        },
        "warnings": warnings,
        "errors": errors,
        "api_calls": {
            "playlist_items": pages_attempted,
            "total": pages_attempted,
        },
    }


def _list_all_video_ids(
    uploads_playlist_id: str,
    progress_callback: Optional[Callable] = None,
) -> list[dict]:
    """Sensitive implementation for :func:`list_all_video_ids`."""
    result = _enumerate_uploads_detailed_impl(
        uploads_playlist_id,
        max_pages=None,
        max_items=None,
        page_token=None,
        progress_callback=progress_callback,
        cancel_check=None,
        preserve_partial=False,
        allow_unbounded=True,
    )
    return result["videos"]


class ChannelDownloader:
    """Manages downloading and storing all transcripts for a YouTube channel."""
    
    def __init__(self, data_dir=None):
        self.data_dir = project_data_dir(data_dir)
        self.channels_dir = self.data_dir / "channels"
        self.channels_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_channel_dir(self, slug: str) -> Path:
        """Get or create directory for a channel."""
        channel_dir = self.channels_dir / slug
        channel_dir.mkdir(parents=True, exist_ok=True)
        (channel_dir / "transcripts").mkdir(exist_ok=True)
        return channel_dir
    
    def _load_manifest(self, channel_dir: Path) -> dict:
        """Load existing manifest or return empty structure."""
        manifest_path = channel_dir / "manifest.json"
        if manifest_path.exists():
            try:
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _resolve_channel_dir(self, channel_info: dict) -> tuple[str, Path]:
        """Resolve the storage slug and directory for a channel, keyed by channel ID.

        An existing corpus whose manifest carries this channel_id is reused even
        if the channel has since been renamed (resume survives renames). If a
        *different* channel's corpus already occupies the name slug, the slug is
        disambiguated with a channel-id suffix so corpora never merge.
        """
        channel_id = channel_info['channel_id']

        if self.channels_dir.exists():
            for d in sorted(self.channels_dir.iterdir()):
                if d.is_dir():
                    manifest = self._load_manifest(d)
                    if manifest.get('channel', {}).get('channel_id') == channel_id:
                        return d.name, self._get_channel_dir(d.name)

        slug = _slugify(channel_info['name'])
        candidate = self.channels_dir / slug
        if candidate.exists():
            existing_id = self._load_manifest(candidate).get('channel', {}).get('channel_id')
            if existing_id and existing_id != channel_id:
                slug = f"{slug}-{channel_id[-6:].lower()}"

        return slug, self._get_channel_dir(slug)
    
    def _save_manifest(self, channel_dir: Path, manifest: dict):
        """Save manifest atomically."""
        manifest_path = channel_dir / "manifest.json"
        tmp_path = channel_dir / "manifest.json.tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        tmp_path.replace(manifest_path)
    
    def _save_transcript(self, channel_dir: Path, video_id: str, data: dict):
        """Save individual transcript file atomically."""
        safe_id = re.sub(r'[/\\:*?"<>|]', '_', video_id)
        path = channel_dir / "transcripts" / f"{safe_id}.json"
        tmp_path = path.with_suffix(".json.tmp")
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp_path.replace(path)
        return path
    
    def get_downloaded_channels(self) -> list[dict]:
        """List all channels that have been downloaded."""
        channels = []
        if not self.channels_dir.exists():
            return channels
        for d in sorted(self.channels_dir.iterdir()):
            if d.is_dir():
                manifest = self._load_manifest(d)
                if manifest:
                    channels.append({
                        'slug': d.name,
                        'name': manifest.get('channel', {}).get('name', d.name),
                        'channel_id': manifest.get('channel', {}).get('channel_id', ''),
                        'total_videos': manifest.get('total_videos', 0),
                        'downloaded': manifest.get('downloaded_count', 0),
                        'failed': manifest.get('failed_count', 0),
                        'last_updated': manifest.get('last_updated', ''),
                    })
        return channels

    def download_channel(
        self,
        channel_id: str,
        delay: float = 1.0,
        lang: Optional[list[str]] = None,
        progress_callback: Optional[Callable] = None,
        log_callback: Optional[Callable] = None,
    ) -> dict:
        """
        Download all transcripts from a channel with resume support.
        
        Args:
            channel_id: YouTube channel ID
            delay: Seconds between transcript downloads (rate limiting)
            lang: Preferred languages (default: ['en'])
            progress_callback: Called with (current, total, video_id, status)
            log_callback: Called with (level, message) for logging
            
        Returns:
            dict with summary statistics
        """
        from .transcript import get_transcript
        
        if lang is None:
            lang = ['en', 'en-US', 'en-GB']
        
        def log(level: str, msg: str):
            if log_callback:
                log_callback(level, msg)
        
        # STEP 1: Get channel info
        log('info', f'Fetching channel info for {channel_id}...')
        channel_info = get_channel_info(channel_id)
        slug, channel_dir = self._resolve_channel_dir(channel_info)
        
        log('info', f"Channel: {channel_info['name']} ({channel_info['video_count']} videos)")
        log('info', f"Storage: {channel_dir}")
        
        # STEP 2: Load existing manifest (for resume)
        manifest = self._load_manifest(channel_dir)
        existing_videos = manifest.get('videos', {})
        
        # STEP 3: Enumerate all video IDs
        log('info', 'Enumerating all videos...')
        
        def enum_progress(current, total, page):
            log('info', f"  Listed {current}/{total} videos (page {page})")
        
        all_videos = list_all_video_ids(
            channel_info['uploads_playlist_id'],
            progress_callback=enum_progress
        )
        log('info', f"Found {len(all_videos)} videos total")
        
        # STEP 4: Determine delta (what's new/not yet downloaded)
        already_done = set()
        already_failed = set()
        for vid_id, vid_info in existing_videos.items():
            status = vid_info.get('status', '')
            if status == 'done':
                already_done.add(vid_id)
            elif status == 'failed':
                already_failed.add(vid_id)
        
        # Build download queue: new videos + previously failed (retry)
        to_download = []
        for v in all_videos:
            vid_id = v['video_id']
            if vid_id in already_done:
                continue
            to_download.append(v)
        
        new_count = len([v for v in to_download if v['video_id'] not in already_failed])
        retry_count = len([v for v in to_download if v['video_id'] in already_failed])
        
        log('info', f"Already downloaded: {len(already_done)}")
        if retry_count > 0:
            log('info', f"Retrying failed: {retry_count}")
        log('info', f"New to download: {new_count}")
        log('info', f"Total to process: {len(to_download)}")
        
        if not to_download:
            log('info', "Nothing new to download — channel is fully synced!")
            # Still update manifest with latest video list
            manifest.update({
                'channel': channel_info,
                'total_videos': len(all_videos),
                'downloaded_count': len(already_done),
                'failed_count': len(already_failed),
                'last_updated': datetime.now().isoformat(),
                'last_sync': datetime.now().isoformat(),
            })
            self._save_manifest(channel_dir, manifest)
            return {
                'channel': channel_info['name'],
                'slug': slug,
                'total': len(all_videos),
                'downloaded': len(already_done),
                'new': 0,
                'failed': 0,
                'skipped': 0,
            }
        
        # STEP 5: Download transcripts
        session_downloaded = 0
        session_failed = 0
        session_skipped = 0
        
        # Ensure videos dict exists in manifest
        if 'videos' not in manifest:
            manifest['videos'] = {}
        
        # Add all known videos to manifest (even if not downloaded yet)
        for v in all_videos:
            vid_id = v['video_id']
            if vid_id not in manifest['videos']:
                manifest['videos'][vid_id] = {
                    'title': v['title'],
                    'published_at': v['published_at'],
                    'status': 'pending',
                }
        
        for i, v in enumerate(to_download):
            vid_id = v['video_id']
            title = v['title']
            
            if progress_callback:
                progress_callback(i + 1, len(to_download), vid_id, 'downloading')
            
            log('debug', f"[{i+1}/{len(to_download)}] {title[:60]}...")
            
            try:
                result = get_transcript(vid_id, languages=lang)
                
                if 'error' in result:
                    log('warn', f"  ✗ {result['error']}")
                    manifest['videos'][vid_id].update({
                        'status': 'failed',
                        'error': result['error'],
                        'last_attempt': datetime.now().isoformat(),
                    })
                    session_failed += 1
                else:
                    # Save transcript file
                    transcript_data = {
                        'video_id': vid_id,
                        'title': title,
                        'published_at': v['published_at'],
                        'channel': channel_info['name'],
                        # Persist the canonical ID returned by channels.list;
                        # ``channel_id`` input may be an @handle or URL.
                        'channel_id': channel_info['channel_id'],
                        'language': result.get('language', ''),
                        'is_generated': result.get('is_generated', True),
                        'duration_seconds': result.get('duration_seconds', 0),
                        'segment_count': result.get('segment_count', 0),
                        'word_count': len(result.get('full_text', '').split()),
                        'full_text': result.get('full_text', ''),
                        'segments': result.get('segments', []),
                        'downloaded_at': datetime.now().isoformat(),
                    }
                    self._save_transcript(channel_dir, vid_id, transcript_data)
                    
                    manifest['videos'][vid_id].update({
                        'status': 'done',
                        'language': result.get('language', ''),
                        'is_generated': result.get('is_generated', True),
                        'duration_seconds': result.get('duration_seconds', 0),
                        'word_count': len(result.get('full_text', '').split()),
                        'downloaded_at': datetime.now().isoformat(),
                    })
                    session_downloaded += 1
                    log('debug', f"  ✓ {result.get('segment_count', 0)} segments, {len(result.get('full_text', '').split())} words")
                    
            except Exception as e:
                log('warn', f"  ✗ Exception: {e}")
                manifest['videos'][vid_id].update({
                    'status': 'failed',
                    'error': str(e),
                    'last_attempt': datetime.now().isoformat(),
                })
                session_failed += 1
            
            # Save manifest after EVERY video (crash-safe resume)
            done_total = sum(1 for v in manifest['videos'].values() if v.get('status') == 'done')
            failed_total = sum(1 for v in manifest['videos'].values() if v.get('status') == 'failed')
            
            manifest.update({
                'channel': channel_info,
                'total_videos': len(all_videos),
                'downloaded_count': done_total,
                'failed_count': failed_total,
                'last_updated': datetime.now().isoformat(),
            })
            self._save_manifest(channel_dir, manifest)
            
            if progress_callback:
                progress_callback(i + 1, len(to_download), vid_id, 'done')
            
            # Rate limiting
            if i < len(to_download) - 1:
                time.sleep(delay)
        
        # Final manifest update
        manifest['last_sync'] = datetime.now().isoformat()
        self._save_manifest(channel_dir, manifest)
        
        summary = {
            'channel': channel_info['name'],
            'slug': slug,
            'total': len(all_videos),
            'already_had': len(already_done),
            'downloaded': session_downloaded,
            'failed': session_failed,
            'skipped': session_skipped,
            'storage': str(channel_dir),
        }
        
        log('info', f"\nDone! Downloaded {session_downloaded}, failed {session_failed}")
        log('info', f"Total corpus: {len(already_done) + session_downloaded}/{len(all_videos)} videos")
        
        return summary

    def get_channel_stats(self, slug: str) -> Optional[dict]:
        """Get statistics for a downloaded channel."""
        channel_dir = self.channels_dir / slug
        manifest = self._load_manifest(channel_dir)
        if not manifest:
            return None
        
        videos = manifest.get('videos', {})
        done_videos = {k: v for k, v in videos.items() if v.get('status') == 'done'}
        failed_videos = {k: v for k, v in videos.items() if v.get('status') == 'failed'}
        pending_videos = {k: v for k, v in videos.items() if v.get('status') == 'pending'}
        
        total_words = sum(v.get('word_count', 0) for v in done_videos.values())
        total_duration = sum(v.get('duration_seconds', 0) for v in done_videos.values())
        
        return {
            'channel': manifest.get('channel', {}),
            'slug': slug,
            'total_videos': manifest.get('total_videos', 0),
            'downloaded': len(done_videos),
            'failed': len(failed_videos),
            'pending': len(pending_videos),
            'total_words': total_words,
            'total_duration_hours': round(total_duration / 3600, 1),
            'last_updated': manifest.get('last_updated', ''),
            'last_sync': manifest.get('last_sync', ''),
            'storage_path': str(channel_dir),
        }
    
    def search_corpus(self, slug: str, query: str, case_sensitive: bool = False) -> list[dict]:
        """
        Search across all downloaded transcripts for a channel.

        Supports:
          - Plain substring search:  "machine learning"
          - NEAR proximity:          "machine learning" NEAR/10 "neural network"
          - NEAR with OR groups:     ("risk" | "drawdown") NEAR/10 "position"
          - Tilde proximity:         "deep learning tensorflow"~5

        Returns list of matches with video_id, title, context snippets.
        """
        channel_dir = self.channels_dir / slug
        transcripts_dir = channel_dir / "transcripts"

        if not transcripts_dir.exists():
            return []

        parsed = _parse_proximity_query(query)
        if parsed[0] == 'plain' and _looks_like_proximity(query):
            raise ValueError(
                f"Query contains proximity operators but could not be parsed: {query}\n"
                'Supported forms: \'"phrase1" NEAR/N "phrase2"\', '
                '\'("alt1" | "alt2") NEAR/N "phrase"\', \'"word1 word2"~N\'. '
                "Terms must be double-quoted."
            )
        results = []

        for f in sorted(transcripts_dir.glob("*.json")):
            try:
                with open(f, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
            except (json.JSONDecodeError, OSError):
                continue  # skip corrupted/unreadable transcript files

            text = data.get('full_text', '')
            if not text:
                continue

            # --- Proximity search paths ---
            if parsed[0] == 'near':
                _, left_terms, right_terms, dist = parsed
                spans = _find_grouped_near_matches(text, left_terms, right_terms, dist)
                if not spans:
                    continue
                snippets = self._snippets_from_spans(text, spans)
                results.append({
                    'video_id': data.get('video_id', f.stem),
                    'title': data.get('title', ''),
                    'published_at': data.get('published_at', ''),
                    'match_count': len(spans),
                    'snippets': snippets,
                })

            elif parsed[0] == 'tilde':
                _, words, dist = parsed
                spans = _find_tilde_matches(text, words, dist)
                if not spans:
                    continue
                snippets = self._snippets_from_spans(text, spans)
                results.append({
                    'video_id': data.get('video_id', f.stem),
                    'title': data.get('title', ''),
                    'published_at': data.get('published_at', ''),
                    'match_count': len(spans),
                    'snippets': snippets,
                })

            else:
                # --- Plain substring search ---
                _, raw_query = parsed
                query_lower = raw_query if case_sensitive else raw_query.lower()
                search_text = text if case_sensitive else text.lower()

                if query_lower not in search_text:
                    continue

                snippets = []
                pos = 0
                while True:
                    idx = search_text.find(query_lower, pos)
                    if idx == -1:
                        break
                    start = max(0, idx - 200)
                    end = min(len(text), idx + len(query) + 200)
                    snippet = text[start:end].strip()
                    if start > 0:
                        snippet = '...' + snippet
                    if end < len(text):
                        snippet = snippet + '...'
                    snippets.append(snippet)
                    pos = idx + 1
                    if len(snippets) >= 5:
                        break

                results.append({
                    'video_id': data.get('video_id', f.stem),
                    'title': data.get('title', ''),
                    'published_at': data.get('published_at', ''),
                    'match_count': search_text.count(query_lower),
                    'snippets': snippets,
                })

        results.sort(key=lambda x: x['match_count'], reverse=True)
        return results

    @staticmethod
    def _snippets_from_spans(text: str, spans: list[tuple[int, int]], max_snippets: int = 5) -> list[str]:
        """Extract context-window snippets from character spans."""
        snippets = []
        for span_start, span_end in spans[:max_snippets]:
            ctx_start = max(0, span_start - 200)
            ctx_end = min(len(text), span_end + 200)
            snippet = text[ctx_start:ctx_end].strip()
            if ctx_start > 0:
                snippet = '...' + snippet
            if ctx_end < len(text):
                snippet = snippet + '...'
            snippets.append(snippet)
        return snippets
