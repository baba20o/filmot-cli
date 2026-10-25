"""Runtime contracts for responses returned by the Filmot API.

The upstream API may add fields without notice, so these validators deliberately
check only structure the client depends on. Unknown fields are accepted. Missing
or type-changed required fields produce a compact diagnostic without echoing
response values, request headers, or credentials.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Callable, Optional, Tuple, Type, Union


ExpectedType = Union[Type[Any], Tuple[Type[Any], ...]]


class FilmotAPIContractError(ValueError):
    """Raised when an upstream response no longer has a supported structure."""

    def __init__(
        self,
        endpoint: str,
        path: str,
        expected: str,
        actual: str,
    ) -> None:
        self.endpoint = endpoint
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(
            "Filmot API contract violation for {} at {}: expected {}, got {}".format(
                endpoint,
                path,
                expected,
                actual,
            )
        )

    def as_response(self) -> dict:
        """Return a machine-readable API error without including response data."""
        return {
            "error": str(self),
            "error_type": type(self).__name__,
            "endpoint": self.endpoint,
            "contract_path": self.path,
        }


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    return type(value).__name__


def _fail(endpoint: str, path: str, expected: str, value: Any) -> None:
    raise FilmotAPIContractError(endpoint, path, expected, _type_name(value))


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def _expect_mapping(endpoint: str, path: str, value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(endpoint, path, "object", value)
    return value


def _expect_list(endpoint: str, path: str, value: Any) -> Sequence[Any]:
    if not _is_sequence(value):
        _fail(endpoint, path, "array", value)
    return value


def _expect_field(
    endpoint: str,
    path: str,
    value: Mapping[str, Any],
    field: str,
    expected_type: ExpectedType,
    expected_label: Optional[str] = None,
) -> Any:
    field_path = "{}.{}".format(path, field)
    if field not in value:
        raise FilmotAPIContractError(
            endpoint,
            field_path,
            expected_label or _expected_type_label(expected_type),
            "missing",
        )
    item = value[field]
    # bool is an int subclass, but an upstream true/false value is structural
    # drift for count, duration, and timestamp fields.
    if expected_type in (int, float) or (
        isinstance(expected_type, tuple)
        and int in expected_type
        and bool not in expected_type
    ):
        if isinstance(item, bool):
            _fail(
                endpoint,
                field_path,
                expected_label or _expected_type_label(expected_type),
                item,
            )
    if not isinstance(item, expected_type):
        _fail(
            endpoint,
            field_path,
            expected_label or _expected_type_label(expected_type),
            item,
        )
    return item


def _expected_type_label(expected_type: ExpectedType) -> str:
    if isinstance(expected_type, tuple):
        return " or ".join(item.__name__ for item in expected_type)
    return expected_type.__name__


def _validate_error_response(
    endpoint: str,
    value: Mapping[str, Any],
) -> bool:
    """Validate and recognize an API error envelope."""
    if "error" not in value:
        return False
    _expect_field(endpoint, "$", value, "error", str)
    if "status_code" in value:
        _expect_field(endpoint, "$", value, "status_code", int)
    return True


def _validate_search_hit(endpoint: str, value: Any, index_path: str) -> None:
    hit = _expect_mapping(endpoint, index_path, value)
    _expect_field(
        endpoint,
        index_path,
        hit,
        "start",
        (int, float),
        "number",
    )
    _expect_field(endpoint, index_path, hit, "token", str)

    if "lines" in hit:
        lines = _expect_list(endpoint, "{}.lines".format(index_path), hit["lines"])
        for line_index, line_value in enumerate(lines):
            line_path = "{}.lines[{}]".format(index_path, line_index)
            line = _expect_mapping(endpoint, line_path, line_value)
            _expect_field(
                endpoint,
                line_path,
                line,
                "start",
                (int, float),
                "number",
            )
            _expect_field(endpoint, line_path, line, "text", str)
        return

    # Context-format hits do not have ``lines``. Both surrounding text fields
    # are part of the shape consumed by the result renderer.
    _expect_field(endpoint, index_path, hit, "ctx_before", str)
    _expect_field(endpoint, index_path, hit, "ctx_after", str)


def _validate_search_video(endpoint: str, value: Any, index_path: str) -> None:
    video = _expect_mapping(endpoint, index_path, value)
    _expect_field(endpoint, index_path, video, "id", str)
    _expect_field(endpoint, index_path, video, "title", str)
    _expect_field(
        endpoint,
        index_path,
        video,
        "duration",
        (int, float),
        "number",
    )
    _expect_field(endpoint, index_path, video, "uploaddate", str)
    _expect_field(endpoint, index_path, video, "viewcount", int)
    _expect_field(endpoint, index_path, video, "likecount", int)
    _expect_field(endpoint, index_path, video, "channelid", str)
    _expect_field(endpoint, index_path, video, "channelname", str)
    hits = _expect_field(endpoint, index_path, video, "hits", list)
    for hit_index, hit in enumerate(hits):
        _validate_search_hit(
            endpoint,
            hit,
            "{}.hits[{}]".format(index_path, hit_index),
        )


def _validate_search_subtitles(endpoint: str, payload: Any) -> None:
    response = _expect_mapping(endpoint, "$", payload)
    if _validate_error_response(endpoint, response):
        return
    results = _expect_field(endpoint, "$", response, "result", list)
    _expect_field(endpoint, "$", response, "totalresultcount", int)
    for index, value in enumerate(results):
        _validate_search_video(endpoint, value, "$.result[{}]".format(index))


def _candidate_list(
    endpoint: str,
    payload: Any,
) -> Sequence[Any]:
    if _is_sequence(payload):
        return payload
    response = _expect_mapping(endpoint, "$", payload)
    if _validate_error_response(endpoint, response):
        return []
    for field in ("channels", "items", "result"):
        if field in response:
            return _expect_list(endpoint, "$.{}".format(field), response[field])
    raise FilmotAPIContractError(
        endpoint,
        "$",
        "array or object containing channels/items/result",
        "object without a candidate array",
    )


def _validate_search_channels(endpoint: str, payload: Any) -> None:
    candidates = _candidate_list(endpoint, payload)
    for index, value in enumerate(candidates):
        path = "$[{}]".format(index)
        candidate = _expect_mapping(endpoint, path, value)
        identifier = next(
            (
                candidate[field]
                for field in ("value", "channelid", "id")
                if field in candidate
            ),
            None,
        )
        if not isinstance(identifier, str) or not identifier:
            raise FilmotAPIContractError(
                endpoint,
                path,
                "non-empty string identifier in value/channelid/id",
                "missing or invalid identifier",
            )
        label = next(
            (
                candidate[field]
                for field in ("label", "name", "title")
                if field in candidate
            ),
            None,
        )
        if not isinstance(label, str):
            raise FilmotAPIContractError(
                endpoint,
                path,
                "string label in label/name/title",
                "missing or invalid label",
            )


# Optional /getvideos ``columns`` the CLI displays and saves. Absent or null is
# fine (``--basic``, partial index records); any other type is drift.
_OPTIONAL_VIDEO_FIELDS: dict[str, Tuple[ExpectedType, str]] = {
    "description": (str, "string"),
    "category": (str, "string"),
    "autosubs": (str, "string"),
    "manualsubs": (str, "string"),
    "lastfetchdate": (str, "string"),
    "channelcountryname": (str, "string"),
    # Current endpoints send "CC" or ""; legacy search encodes 2 or 1.
    "license": ((str, int), "string or int"),
    "viewcount": (int, "int"),
    "likecount": (int, "int"),
    "channelsubcount": (int, "int"),
    "isunlisted": ((int, bool), "int or bool"),
    "islivestream": ((int, bool), "int or bool"),
    "ispartialdata": ((int, bool), "int or bool"),
    # Track contents are checked separately after validating the container.
    "subtitles": (list, "array"),
}


def _validate_subtitle_tracks(endpoint: str, path: str, tracks: Any) -> None:
    for index, value in enumerate(_expect_list(endpoint, path, tracks)):
        track_path = "{}[{}]".format(path, index)
        track = _expect_mapping(endpoint, track_path, value)
        _expect_field(endpoint, track_path, track, "lang", str)
        automatic = _expect_field(endpoint, track_path, track, "isauto", int)
        if automatic not in (0, 1):
            _fail(endpoint, track_path + ".isauto", "0 or 1", automatic)
        lines = _expect_field(endpoint, track_path, track, "transcript", list)
        for line_index, line_value in enumerate(lines):
            line_path = "{}.transcript[{}]".format(track_path, line_index)
            line = _expect_mapping(endpoint, line_path, line_value)
            _expect_field(endpoint, line_path, line, "s", (int, float), "number")
            _expect_field(endpoint, line_path, line, "t", str)


def _validate_video_record(endpoint: str, value: Any, path: str) -> None:
    video = _expect_mapping(endpoint, path, value)
    _expect_field(endpoint, path, video, "id", str)
    # Partial index records (ispartialdata 1) carry these keys as null, and
    # basic lookups return them without the ispartialdata flag. A missing key
    # or another type is still drift.
    _expect_field(endpoint, path, video, "title", (str, type(None)), "string or null")
    _expect_field(
        endpoint,
        path,
        video,
        "duration",
        (int, float, type(None)),
        "number or null",
    )
    for field in ("uploaddate", "channelid", "channelname"):
        _expect_field(endpoint, path, video, field, (str, type(None)), "string or null")
    for field, (expected_type, label) in _OPTIONAL_VIDEO_FIELDS.items():
        if video.get(field) is not None:
            _expect_field(endpoint, path, video, field, expected_type, label)
    if video.get("subtitles") is not None:
        _validate_subtitle_tracks(endpoint, path + ".subtitles", video["subtitles"])


def _validate_metadata_search(endpoint: str, payload: Any) -> None:
    """MetadataExportVideo permits every field to be omitted by columns."""
    response = _expect_mapping(endpoint, "$", payload)
    if _validate_error_response(endpoint, response):
        return
    results = _expect_field(endpoint, "$", response, "result", list)
    fields = {
        **_OPTIONAL_VIDEO_FIELDS,
        **{field: (str, "string") for field in (
            "id", "title", "channelid", "channelname", "uploaddate",
        )},
        "duration": ((int, float), "number"),
        "dislikecount": (int, "int"),
    }
    for index, value in enumerate(results):
        path = "$.result[{}]".format(index)
        video = _expect_mapping(endpoint, path, value)
        _optional_fields(endpoint, path, video, fields)
        if video.get("subtitles") is not None:
            _validate_subtitle_tracks(endpoint, path + ".subtitles", video["subtitles"])


def _validate_get_videos(endpoint: str, payload: Any) -> None:
    if _is_sequence(payload):
        for index, value in enumerate(payload):
            _validate_video_record(endpoint, value, "$[{}]".format(index))
        return

    response = _expect_mapping(endpoint, "$", payload)
    if _validate_error_response(endpoint, response):
        return
    if "result" in response:
        results = _expect_list(endpoint, "$.result", response["result"])
        for index, value in enumerate(results):
            _validate_video_record(
                endpoint,
                value,
                "$.result[{}]".format(index),
            )
        return
    _validate_video_record(endpoint, response, "$")


_NUMBER: Tuple[ExpectedType, str] = ((int, float), "number")
_TEXT: Tuple[ExpectedType, str] = (str, "string")

# /getchannels fields and groups the CLI displays. Absent or null is fine (the
# API omits ``username`` without a legacy name, and groups appear only when
# requested); any other type is drift.
_OPTIONAL_CHANNEL_FIELDS: dict[str, Tuple[ExpectedType, str]] = {
    **{
        field: _TEXT
        for field in (
            "name", "username", "handle", "thumbnailurl", "country", "description",
            "biography", "joindate", "fetcheddate", "updatedate", "firstvideodate",
            "lastvideodate",
        )
    },
    "subcount": (int, "int"),
    "totalviewcount": (int, "int"),
    "isofficialartist": ((bool, int), "bool or int"),
    "isverified": ((bool, int), "bool or int"),
    "isjoinable": ((bool, int), "bool or int"),
    "counts": (Mapping, "object"),
    "growth": (Mapping, "object"),
    "channelchanges": (Mapping, "object"),
    "subscribergrowth": (list, "array"),
    "wordcloud": (list, "array"),
    "videostats": (Mapping, "object"),
}


def _optional_fields(
    endpoint: str,
    path: str,
    value: Mapping[str, Any],
    fields: Mapping[str, Tuple[ExpectedType, str]],
) -> None:
    """Type-check the listed fields that are present and not null."""
    for field, (expected_type, label) in fields.items():
        if value.get(field) is not None:
            _expect_field(endpoint, path, value, field, expected_type, label)


# Keys the CLI reads inside /getchannels groups. Other keys are additions and
# pass unchecked, like unknown fields anywhere else.
_RECENT_UPLOAD_WINDOWS = ("15d", "30d", "60d", "180d", "360d")
_GROWTH_METRICS = ("totalviewcount", "subcount")
_GROWTH_WINDOWS = ("28d", "90d", "180d", "365d")
_VIDEO_TYPES = ("regular", "shorts", "livestreams", "unknown")
_DISTRIBUTION_MEASURES = ("duration", "viewcount", "likecount")
_SUMMARY_FIELDS = ("n", "mean", "sd", "q1", "median", "q3", "min", "max")
_HISTORY_FIELDS = ("name", "country", "description", "handle")


def _numbers(endpoint: str, path: str, value: Any, fields: Sequence[str]) -> None:
    """An object whose listed fields, when present and not null, are numbers."""
    mapping = _expect_mapping(endpoint, path, value)
    _optional_fields(endpoint, path, mapping, {field: _NUMBER for field in fields})


def _validate_video_stats(endpoint: str, path: str, value: Any) -> None:
    """``videostats`` and each of its ``months`` share one shape."""
    stats = _expect_mapping(endpoint, path, value)
    _optional_fields(endpoint, path, stats, {
        "month": _TEXT,
        "startdate": _TEXT,
        "enddate": _TEXT,
        "analyzedvideos": _NUMBER,
        "unknowndurationcount": _NUMBER,
        "durationhistogram": (list, "array"),
        "durationhistogramedgessec": (list, "array"),
        "languages": (list, "array"),
        "months": (list, "array"),
    })
    for key in ("durationhistogram", "durationhistogramedgessec"):
        for index, item in enumerate(stats.get(key) or []):
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                _fail(endpoint, "{}.{}[{}]".format(path, key, index), "number", item)
    if stats.get("videotypes") is not None:
        _numbers(endpoint, path + ".videotypes", stats["videotypes"], _VIDEO_TYPES)
    if stats.get("distributions") is not None:
        distributions = _expect_mapping(endpoint, path + ".distributions", stats["distributions"])
        for measure in _DISTRIBUTION_MEASURES:
            if distributions.get(measure) is not None:
                _numbers(
                    endpoint, "{}.distributions.{}".format(path, measure),
                    distributions[measure], _SUMMARY_FIELDS,
                )
    for index, language in enumerate(stats.get("languages") or []):
        language_path = "{}.languages[{}]".format(path, index)
        entry = _expect_mapping(endpoint, language_path, language)
        _expect_field(endpoint, language_path, entry, "lang", str)
        _optional_fields(endpoint, language_path, entry, {
            "total": _NUMBER, "automatic": _NUMBER, "manual": _NUMBER,
        })
    for index, month in enumerate(stats.get("months") or []):
        _validate_video_stats(endpoint, "{}.months[{}]".format(path, index), month)


def _validate_channel_record(
    endpoint: str, value: Any, path: str, *, projected: bool = False,
) -> None:
    channel = _expect_mapping(endpoint, path, value)
    if not projected or "channelid" in channel:
        _expect_field(endpoint, path, channel, "channelid", str)
    _optional_fields(endpoint, path, channel, _OPTIONAL_CHANNEL_FIELDS)
    counts = channel.get("counts")
    if counts is not None:
        _optional_fields(endpoint, path + ".counts", counts, {
            "indexedvideos": _NUMBER, "partialdatavideos": _NUMBER,
        })
        if counts.get("recentuploads") is not None:
            _numbers(
                endpoint, path + ".counts.recentuploads", counts["recentuploads"],
                _RECENT_UPLOAD_WINDOWS,
            )
    growth = channel.get("growth") or {}
    for metric in _GROWTH_METRICS:
        if growth.get(metric) is None:
            continue
        metric_path = "{}.growth.{}".format(path, metric)
        windows = _expect_mapping(endpoint, metric_path, growth[metric])
        for window in _GROWTH_WINDOWS:
            change = windows.get(window)
            if change is None:
                continue
            window_path = "{}.{}".format(metric_path, window)
            _optional_fields(endpoint, window_path, _expect_mapping(endpoint, window_path, change), {
                "startdate": _TEXT,
                "enddate": _TEXT,
                "actualdays": _NUMBER,
                "startcount": _NUMBER,
                "endcount": _NUMBER,
                "delta": _NUMBER,
                "normalizeddelta": _NUMBER,
            })
    for index, point in enumerate(channel.get("subscribergrowth") or []):
        point_path = "{}.subscribergrowth[{}]".format(path, index)
        point = _expect_mapping(endpoint, point_path, point)
        _expect_field(endpoint, point_path, point, "d", str)
        _expect_field(endpoint, point_path, point, "s", *_NUMBER)
    for index, term in enumerate(channel.get("wordcloud") or []):
        term_path = "{}.wordcloud[{}]".format(path, index)
        term = _expect_mapping(endpoint, term_path, term)
        _expect_field(endpoint, term_path, term, "text", str)
        _expect_field(endpoint, term_path, term, "weight", *_NUMBER)
        if "lang" in term:
            _expect_field(endpoint, term_path, term, "lang", str)
    for field, changes in (channel.get("channelchanges") or {}).items():
        # The CLI lists every array here; a non-array addition is skipped.
        if changes is None or (field not in _HISTORY_FIELDS and not _is_sequence(changes)):
            continue
        field_path = "{}.channelchanges.{}".format(path, field)
        for index, change in enumerate(_expect_list(endpoint, field_path, changes)):
            change_path = "{}[{}]".format(field_path, index)
            _optional_fields(endpoint, change_path, _expect_mapping(endpoint, change_path, change), {
                "val": _TEXT, "dstart": _TEXT, "dend": _TEXT,
            })
    if channel.get("videostats") is not None:
        _validate_video_stats(endpoint, path + ".videostats", channel["videostats"])


def _validate_get_channels(endpoint: str, payload: Any) -> None:
    if not _is_sequence(payload):
        response = _expect_mapping(endpoint, "$", payload)
        if _validate_error_response(endpoint, response):
            return
        _fail(endpoint, "$", "array", payload)
    for index, value in enumerate(payload):
        _validate_channel_record(endpoint, value, "$[{}]".format(index))


# /getadvancedchannelsearch adds these to the /getchannels record fields.
_OPTIONAL_CHANNEL_FINDER_FIELDS: dict[str, Tuple[ExpectedType, str]] = {
    "videocount": _NUMBER,
    "avgdurationseconds": _NUMBER,
    "avgvideoviewcount": _NUMBER,
    "matchscore": _NUMBER,
    "autolanguages": _TEXT,
    "topiccloud": _TEXT,
}


def _validate_advanced_channel_search(endpoint: str, payload: Any) -> None:
    response = _expect_mapping(endpoint, "$", payload)
    if _validate_error_response(endpoint, response):
        return
    _optional_fields(endpoint, "$", response, {
        "page": _NUMBER, "perpagecount": _NUMBER, "maxresults": _NUMBER,
    })
    results = _expect_field(endpoint, "$", response, "result", list, "array")
    for index, value in enumerate(results):
        path = "$.result[{}]".format(index)
        _validate_channel_record(endpoint, value, path, projected=True)
        _optional_fields(endpoint, path, value, _OPTIONAL_CHANNEL_FINDER_FIELDS)


_VALIDATORS: dict[str, Callable[[str, Any], None]] = {
    "getsearchsubtitles": _validate_search_subtitles,
    "getsearchchannels": _validate_search_channels,
    "getvideos": _validate_get_videos,
    "getchannels": _validate_get_channels,
    # Listings carry Video records; metadata-search fields are selectable.
    "getchannelvideos": _validate_get_videos,
    "getmetadatasearch": _validate_metadata_search,
    "getadvancedchannelsearch": _validate_advanced_channel_search,
}


def validate_api_response(endpoint: str, payload: Any) -> Any:
    """Validate supported Filmot endpoint responses and return ``payload``.

    Unknown endpoints pass through untouched. This lets the low-level client
    add endpoints independently while still protecting the response shapes
    consumed by the current CLI.
    """
    normalized = endpoint.strip().split("?", 1)[0].strip("/").casefold()
    validator = _VALIDATORS.get(normalized)
    if validator is not None:
        validator("/{}".format(normalized), payload)
    return payload
