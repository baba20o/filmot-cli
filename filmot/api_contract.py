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


def _validate_video_record(endpoint: str, value: Any, path: str) -> None:
    video = _expect_mapping(endpoint, path, value)
    _expect_field(endpoint, path, video, "id", str)
    _expect_field(endpoint, path, video, "title", str)
    _expect_field(
        endpoint,
        path,
        video,
        "duration",
        (int, float),
        "number",
    )
    _expect_field(endpoint, path, video, "uploaddate", str)
    _expect_field(endpoint, path, video, "channelid", str)
    _expect_field(endpoint, path, video, "channelname", str)


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


_VALIDATORS: dict[str, Callable[[str, Any], None]] = {
    "getsearchsubtitles": _validate_search_subtitles,
    "getsearchchannels": _validate_search_channels,
    "getvideos": _validate_get_videos,
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
