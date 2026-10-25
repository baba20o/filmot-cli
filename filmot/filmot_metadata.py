"""Pure normalization of Filmot index observations, without API configuration."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from .api_usage import valid_timestamp


FILMOT_ENRICHMENT_SCHEMA = "filmot.index-metadata/v1"
FILMOT_METADATA_LOOKUP_PROFILE = "video-metadata"
FILMOT_FIELD_ALIASES = {
    "category": "category",
    "isunlisted": "is_unlisted",
    "islivestream": "is_livestream",
    "license": "creative_commons",
    "channelsubcount": "channel_subscribers",
    "channelcountryname": "channel_country",
    "ispartialdata": "partial_metadata",
    "lastfetchdate": "filmot_observed_at",
    "dislikecount": "dislikes",
    "duration": "video_duration_seconds",
}
FILMOT_RAW_METADATA_FIELDS = frozenset({
    *FILMOT_FIELD_ALIASES, "autosubs", "manualsubs",
})
FILMOT_CANONICAL_METADATA_FIELDS = frozenset({
    *FILMOT_FIELD_ALIASES.values(), "caption_languages",
})


def caption_languages(value: Any) -> List[str]:
    """Split whitespace-separated caption languages, retaining order and regions."""
    return list(dict.fromkeys(value.split())) if isinstance(value, str) else []


def creative_commons(value: Any) -> Optional[bool]:
    """Read current CC/blank and legacy integer license representations."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    if value in ("CC", 2, "2"):
        return True
    if value in ("", 1, "1"):
        return False
    return None


def _flag(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if not isinstance(value, (str, int)):
        return None
    if value in (1, "1"):
        return True
    if value in (0, "0"):
        return False
    return None


def _count(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _field(row: Dict[str, Any], raw: str, canonical: str) -> Any:
    """An explicit API field wins, even when null or invalid; otherwise use canonical."""
    return row[raw] if raw in row else row.get(canonical)


def _seconds(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _language_list(value: Any) -> Optional[List[str]]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or len(item.split()) != 1 for item in value
    ):
        return None
    return list(dict.fromkeys(item.strip() for item in value))


def filmot_metadata_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    """Canonical metadata from a Filmot row; unknown observations stay absent.

    API spellings take precedence over canonical aliases when explicitly present,
    including null or invalid values. Canonical aliases are validated equally.
    Caption text is intentionally outside this metadata vocabulary. Its original
    track/line structure belongs to raw exports or transcript acquisition.
    """
    observed = _text(_field(row, "lastfetchdate", "filmot_observed_at"))
    fields = {
        "title": _text(row.get("title")),
        "description": _text(row.get("description")),
        "channel_title": _text(row.get("channelname")),
        "channel_id": _text(row.get("channelid")),
        "published_at": _text(row.get("uploaddate")),
        "views": _count(row.get("viewcount")),
        "likes": _count(row.get("likecount")),
        "dislikes": _count(_field(row, "dislikecount", "dislikes")),
        "category": _text(row.get("category")),
        "is_unlisted": _flag(_field(row, "isunlisted", "is_unlisted")),
        "is_livestream": _flag(_field(row, "islivestream", "is_livestream")),
        "creative_commons": (
            creative_commons(row["license"]) if "license" in row
            else _flag(row.get("creative_commons"))
        ),
        "channel_subscribers": _count(_field(row, "channelsubcount", "channel_subscribers")),
        "channel_country": _text(_field(row, "channelcountryname", "channel_country")),
        "partial_metadata": _flag(_field(row, "ispartialdata", "partial_metadata")),
        "filmot_observed_at": observed if observed and valid_timestamp(observed) else None,
        "video_duration_seconds": _seconds(_field(row, "duration", "video_duration_seconds")),
    }
    canonical_languages = row.get("caption_languages")
    canonical_languages = canonical_languages if isinstance(canonical_languages, dict) else {}
    languages = {}
    for part, field in (("automatic", "autosubs"), ("manual", "manualsubs")):
        value = (
            caption_languages(row[field]) if isinstance(row.get(field), str)
            else None if field in row else _language_list(canonical_languages.get(part))
        )
        if value is not None:
            languages[part] = value
    if languages:
        fields["caption_languages"] = languages
    return {key: value for key, value in fields.items() if value is not None}
