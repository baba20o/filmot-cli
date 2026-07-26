"""Contract tests for shared command results and durable events."""

from dataclasses import dataclass

import pytest

from filmot.schemas import (
    EVENT_SCHEMA,
    RESULT_SCHEMA,
    CommandResult,
    ErrorDetail,
    ResultStatus,
    normalize_event_dict,
    result_from_dict,
)


@dataclass(frozen=True)
class ExamplePayload:
    value: str
    count: int


def test_command_result_serializes_typed_payload():
    result = CommandResult.completed(
        "example",
        ExamplePayload(value="人工知能", count=2),
        warnings=["inspect the source"],
    )

    assert result.to_dict() == {
        "schema": RESULT_SCHEMA,
        "command": "example",
        "status": "completed",
        "data": {"value": "人工知能", "count": 2},
        "errors": [],
        "warnings": ["inspect the source"],
    }


def test_raw_projection_preserves_domain_keys_and_round_trips():
    result = CommandResult(
        command="search",
        status=ResultStatus.PARTIAL,
        data={"result": [{"id": "abc"}], "scope": {"partial": True}},
        errors=[ErrorDetail("PageError", "page two failed", stage="pagination")],
    )

    raw = result.to_raw_dict()

    assert raw["result"] == [{"id": "abc"}]
    assert raw["_filmot"]["schema"] == RESULT_SCHEMA
    assert raw["_filmot"]["status"] == "partial"
    parsed = result_from_dict(raw)
    assert parsed.command == "search"
    assert parsed.status_value == "partial"
    assert parsed.data == {
        "result": [{"id": "abc"}],
        "scope": {"partial": True},
    }
    assert parsed.errors[0].stage == "pagination"


def test_result_to_event_keeps_shared_contract_fields():
    result = CommandResult.completed("library-search", {"rows": [{"id": "x"}]})

    event = result.to_event(
        "library_search",
        topic="topic",
        data={"query": "needle", "results": 1},
        timestamp="2026-07-25T12:00:00",
    ).to_dict()

    assert event == {
        "schema": EVENT_SCHEMA,
        "ts": "2026-07-25T12:00:00",
        "kind": "library_search",
        "command": "library-search",
        "status": "completed",
        "data": {"query": "needle", "results": 1},
        "errors": [],
        "warnings": [],
        "topic": "topic",
    }


def test_legacy_event_normalizes_without_rewriting_source():
    event = normalize_event_dict({
        "ts": "2026-07-25T12:00:00",
        "kind": "search",
        "status": "completed_with_failures",
        "query": "SQLite WAL",
        "results": 3,
    })

    assert event["schema"] == EVENT_SCHEMA
    assert event["command"] == "search"
    assert event["status"] == "completed"
    assert event["data"] == {
        "query": "SQLite WAL",
        "results": 3,
        "legacy_status": "completed_with_failures",
    }


def test_unknown_status_is_rejected_at_schema_boundary():
    with pytest.raises(ValueError, match="Unsupported result status"):
        CommandResult(command="x", status="maybe", data={})
