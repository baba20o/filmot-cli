"""Typed result, ledger, and renderer contracts for library commands."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from filmot.cli import cli


def _invoke_with_contract(library, arguments, renderer):
    with (
        patch("filmot.library.get_library", return_value=library),
        patch("filmot.ledger.log_result") as log_result,
        patch(
            "filmot.commands.library.{}".format(renderer)
        ) as render,
    ):
        result = CliRunner().invoke(cli, arguments)
    outcome = log_result.call_args.args[1]
    assert render.call_args.args[0] is outcome
    return result, outcome, log_result.call_args.kwargs["data"]


def test_library_search_logs_and_renders_one_typed_outcome():
    library = MagicMock()
    library.search.return_value = [{
        "video_id": "video-id",
        "topic": "science",
        "title": "A source",
        "channel": "Primary Lab",
        "match_count": 2,
        "matches": ["alpha one", "alpha two"],
    }]

    result, outcome, compact = _invoke_with_contract(
        library,
        ["library", "search", "alpha", "--topic", "science"],
        "_render_library_search",
    )

    assert result.exit_code == 0, result.output
    assert outcome.command == "library-search"
    assert outcome.status_value == "completed"
    assert outcome.data["rows"][0]["video_id"] == "video-id"
    assert outcome.data["summary"]["matches"] == 2
    assert compact == {
        "query": "alpha",
        "substring": False,
        "sources": 1,
        "matches": 2,
    }
    assert "rows" not in compact


def test_library_context_ledger_projection_omits_context_body():
    library = MagicMock()
    library.get_context.return_value = "full context body"

    result, outcome, compact = _invoke_with_contract(
        library,
        ["library", "context", "science"],
        "_render_library_context",
    )

    assert result.exit_code == 0, result.output
    assert outcome.command == "library-context"
    assert outcome.data["rows"] == [{"content": "full context body"}]
    assert outcome.data["summary"]["delivery"] == "rendered"
    assert compact["chars"] == len("full context body")
    assert compact["delivery"] == "rendered"
    assert "rows" not in compact
    assert "content" not in compact


@pytest.mark.parametrize(
    ("arguments", "method", "return_value", "renderer", "command"),
    [
        (
            ["library", "stats"],
            "stats",
            {
                "total_topics": 1,
                "total_transcripts": 2,
                "total_size_bytes": 10,
                "total_size_mb": 0.1,
                "topics": [{"topic": "science", "count": 2}],
            },
            "_render_library_stats",
            "library-stats",
        ),
        (
            ["library", "delete", "video-id", "--yes"],
            "delete",
            True,
            "_render_library_delete",
            "library-delete",
        ),
    ],
)
def test_library_stats_and_delete_share_outcome_with_renderer(
    arguments,
    method,
    return_value,
    renderer,
    command,
):
    library = MagicMock()
    getattr(library, method).return_value = return_value

    result, outcome, compact = _invoke_with_contract(
        library,
        arguments,
        renderer,
    )

    assert result.exit_code == 0, result.output
    assert outcome.command == command
    assert outcome.status_value == "completed"
    assert "rows" not in compact


def test_library_compare_precomputes_renderer_rows_but_logs_counts_only():
    library = MagicMock()
    library.search.return_value = [{
        "video_id": "video-id",
        "topic": "science",
        "title": "A source",
        "channel": "Primary Lab",
        "match_count": 2,
        "matches": ["alpha"],
    }]
    library.get.return_value = {
        "metadata": {"duration_seconds": 120},
        "transcript": "alpha and alpha",
    }
    library._find_matches.return_value = ["alpha and alpha"]

    result, outcome, compact = _invoke_with_contract(
        library,
        [
            "library",
            "compare",
            "alpha",
            "--topic",
            "science",
            "--sort",
            "density",
        ],
        "_render_library_compare",
    )

    assert result.exit_code == 0, result.output
    assert outcome.command == "library-compare"
    assert outcome.status_value == "completed"
    assert outcome.data["rows"][0]["density"] == 1.0
    assert outcome.data["rows"][0]["excerpts"] == ["alpha and alpha"]
    assert compact["sources"] == 1
    assert compact["matches"] == 2
    assert "rows" not in compact
    assert "excerpts" not in compact


@pytest.mark.parametrize(
    ("arguments", "method", "renderer", "stage"),
    [
        (
            ["library", "search", "alpha"],
            "search",
            "_render_library_search",
            "search",
        ),
        (
            ["library", "context", "science"],
            "get_context",
            "_render_library_context",
            "build-context",
        ),
        (
            ["library", "stats"],
            "stats",
            "_render_library_stats",
            "statistics",
        ),
        (
            ["library", "delete", "video-id", "--yes"],
            "delete",
            "_render_library_delete",
            "delete-transcript",
        ),
        (
            ["library", "compare", "alpha"],
            "search",
            "_render_library_compare",
            "search",
        ),
    ],
)
def test_library_operation_failures_are_structured_and_nonzero(
    arguments,
    method,
    renderer,
    stage,
):
    library = MagicMock()
    getattr(library, method).side_effect = OSError("synthetic disk failure")

    result, outcome, compact = _invoke_with_contract(
        library,
        arguments,
        renderer,
    )

    assert result.exit_code == 1
    assert outcome.status_value == "failed"
    assert outcome.errors[0].type == "OSError"
    assert outcome.errors[0].message == "synthetic disk failure"
    assert outcome.errors[0].stage == stage
    assert "rows" not in compact


def test_library_context_write_failure_has_structured_error():
    library = MagicMock()
    library.get_context.return_value = "context body"
    with patch("builtins.open", side_effect=OSError("disk full")):
        result, outcome, compact = _invoke_with_contract(
            library,
            [
                "library",
                "context",
                "science",
                "--output",
                "artifact.txt",
            ],
            "_render_library_context",
        )

    assert result.exit_code == 1
    assert outcome.status_value == "failed"
    assert outcome.errors[0].stage == "write-output"
    assert outcome.errors[0].details == {"output": "artifact.txt"}
    assert compact["delivery"] == "failed"
    assert compact["output"] == "artifact.txt"
    assert "context body" not in compact.values()


def _migration_library(tmp_path: Path, *, remove_source: bool):
    library = MagicMock()
    library._normalize_topic.return_value = "人工知能"
    library.transcripts_dir = tmp_path / "transcripts"
    source = library.transcripts_dir / "uncategorized"
    source.mkdir(parents=True)
    source_file = source / "legacy.json"
    source_file.write_text("{}", encoding="utf-8")

    def migrate(_topic):
        if remove_source:
            source_file.unlink()
        return int(remove_source)

    library.migrate_legacy_topic.side_effect = migrate
    return library


@pytest.mark.parametrize(
    ("remove_source", "expected_status", "expected_exit"),
    [
        (True, "completed", 0),
        (False, "partial", 2),
    ],
)
def test_library_migration_typed_completion_and_partial_contract(
    tmp_path,
    remove_source,
    expected_status,
    expected_exit,
):
    library = _migration_library(
        tmp_path,
        remove_source=remove_source,
    )

    result, outcome, compact = _invoke_with_contract(
        library,
        ["library", "migrate-topic", "人工知能", "--yes"],
        "_render_library_migrate_topic",
    )

    assert result.exit_code == expected_exit, result.output
    assert outcome.command == "library-migrate-topic"
    assert outcome.status_value == expected_status
    assert compact["source_files"] == 1
    assert compact["migrated"] == int(remove_source)
    assert compact["remaining"] == int(not remove_source)
    assert "rows" not in compact
    if remove_source:
        assert outcome.errors == []
    else:
        assert outcome.errors[0].type == "MigrationIncomplete"
        assert outcome.errors[0].stage == "move-files"
        assert outcome.errors[0].details["remaining"] == 1
