"""Channel profiles, analytics, and indexed-video listings from Filmot.

``/getchannels`` and ``/getchannelvideos`` are direct-API endpoints keyed by UC
channel IDs. This module resolves the references people type (a UC ID, an
@handle, or a channel URL), maps CLI options to analytics groups, and pages
through channel listings.
"""

from __future__ import annotations

import unicodedata
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from .video_metadata import video_rows


PROFILE_CHANNEL_LIMIT = 20
LISTING_CHANNEL_LIMIT = 10
# /getchannelvideos: channels times countPerChannel on one page.
LISTING_PAGE_LIMIT = 200

# /getchannels columns for each option. History and video statistics cost 3
# credits; the profile, about text, growth, and word cloud cost 1.
CHANNEL_GROUPS: Dict[str, Sequence[str]] = {
    "about": ("description", "biography"),
    "growth": ("growth", "subscribergrowth"),
    "words": ("wordcloud",),
    "history": ("channelchanges",),
    "stats": ("videostats", "distributionstats", "languagestats"),
    "monthly": ("videostats", "monthlystats"),
}
THREE_CREDIT_GROUPS = frozenset({"history", "stats", "monthly", "all"})
# Groups a date range filters: video statistics and the subscriber series.
DATED_GROUPS = frozenset({"growth", "stats", "monthly", "all"})
VIDEO_STAT_GROUPS = frozenset({"stats", "monthly", "all"})


class ChannelReferenceError(ValueError):
    """A channel reference could not be resolved to a UC channel ID."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "ChannelReferenceError",
        payload: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.error_type = error_type
        self.payload = payload


def channel_columns(groups: Iterable[str]) -> Optional[str]:
    """The ``columns`` value for ``groups``, or None for the profile alone."""
    groups = list(groups)
    if "all" in groups:
        return "all"
    columns: List[str] = []
    for group in groups:
        for column in CHANNEL_GROUPS[group]:
            if column not in columns:
                columns.append(column)
    return ",".join(columns) or None


def channel_credits(groups: Iterable[str]) -> int:
    """Direct-API credits one ``/getchannels`` request costs for ``groups``."""
    return 3 if THREE_CREDIT_GROUPS.intersection(groups) else 1


def _handle_key(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    handle = unicodedata.normalize("NFC", value.strip())
    return (handle if handle.startswith("@") else "@" + handle).casefold()


def _channel_for_handle(handle: str, client: Any) -> str:
    result = client.search_channels(handle)
    if isinstance(result, dict) and result.get("error"):
        raise ChannelReferenceError(
            "Could not look up @{}: {}".format(handle, result["error"]),
            error_type=str(result.get("error_type") or "FilmotAPIError"),
            payload=result,
        )
    candidates = result if isinstance(result, list) else next(
        (
            result[key]
            for key in ("channels", "items", "result")
            if isinstance(result, dict) and isinstance(result.get(key), list)
        ),
        [],
    )
    wanted = _handle_key(handle)
    matches = {
        candidate["channelid"]
        for candidate in candidates
        if isinstance(candidate, dict)
        and isinstance(candidate.get("channelid"), str)
        and _handle_key(candidate.get("handle")) == wanted
    }
    if len(matches) != 1:
        raise ChannelReferenceError(
            "Filmot's channel search has no channel with the handle @{}; pass "
            "its UC channel ID instead.".format(handle),
            error_type="ChannelNotFound",
        )
    return matches.pop()


def resolve_channels(
    references: Sequence[str],
    client: Any,
    *,
    limit: int,
) -> List[Dict[str, str]]:
    """One ``{"reference", "channel_id", "resolved_by"}`` per distinct channel.

    UC IDs and ``/channel/`` URLs need no lookup. An @handle or ``/@handle``
    URL costs one Filmot channel search and must match a result's handle
    exactly, because the search itself is fuzzy; a handle given twice is
    looked up once. Order follows ``references``; a channel named twice is
    kept once. Once ``limit`` channels are resolved, a further new channel is
    refused, and an unresolved handle is refused before its lookup is paid for.
    """
    from .channel_dl import _parse_channel_reference

    def too_many() -> ChannelReferenceError:
        return ChannelReferenceError(
            "Filmot accepts at most {} channels per request.".format(limit),
            error_type="TooManyChannels",
        )

    resolved: List[Dict[str, str]] = []
    seen = set()
    handles: Dict[Optional[str], str] = {}
    for reference in references:
        try:
            kind, value = _parse_channel_reference(reference)
        except ValueError as error:
            raise ChannelReferenceError("{}: {}".format(reference, error)) from None
        if kind == "id":
            channel_id, resolved_by = value, "id"
        else:
            key = _handle_key(value)
            if key not in handles:
                if len(resolved) == limit:
                    raise too_many()
                handles[key] = _channel_for_handle(value, client)
            channel_id, resolved_by = handles[key], "handle"
        if channel_id in seen:
            continue
        if len(resolved) == limit:
            raise too_many()
        seen.add(channel_id)
        resolved.append({
            "reference": reference,
            "channel_id": channel_id,
            "resolved_by": resolved_by,
        })
    return resolved


def fetch_channel_videos(
    client: Any,
    channel_ids: Sequence[str],
    *,
    count: int,
    first_page: int = 1,
    pages: int = 1,
    only_with_subtitles: bool = False,
    columns: Any = None,
    on_page: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, Any]:
    """Up to ``pages`` consecutive listing pages from ``first_page``, newest first.

    Paging stops at the first empty page, which is the only end-of-listing
    signal Filmot documents; a short page may still be followed by more. Each
    page costs 3 credits. Returns ``videos`` (de-duplicated across pages),
    ``pages_fetched``, ``exhausted``, and ``next_page``. A failed page adds the
    API's ``error`` object, keeps earlier pages' videos, and makes
    ``next_page`` the page that failed.
    """
    videos: List[Dict[str, Any]] = []
    seen = set()
    page = first_page
    pages_fetched = 0
    exhausted = False
    error = None
    while pages_fetched < pages:
        response = client.get_channel_videos(
            channel_ids,
            count_per_channel=count,
            page=page,
            only_with_subtitles=only_with_subtitles,
            columns=columns,
        )
        if isinstance(response, dict) and response.get("error"):
            error = response
            break
        rows = video_rows(response)
        pages_fetched += 1
        if on_page is not None:
            on_page(page, len(rows))
        if not rows:
            exhausted = True
            break
        for row in rows:
            if row.get("id") not in seen:
                seen.add(row.get("id"))
                videos.append(row)
        page += 1
    result: Dict[str, Any] = {
        "videos": videos,
        "pages_fetched": pages_fetched,
        "exhausted": exhausted,
        "next_page": None if exhausted else page,
    }
    if error is not None:
        result["error"] = error
    return result
