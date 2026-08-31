"""Typed result, ledger, and renderer contracts for library commands."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from filmot.cli import cli


def _invoke_with_contract(
    library,
    arguments,
    renderer,
    *,
    expect_log=True,
):
    with (
        patch("filmot.library.get_library", return_value=library),
        patch("filmot.ledger.log_result") as log_result,
        patch(
            "filmot.commands.library.{}".format(renderer)
        ) as render,
    ):
        result = CliRunner().invoke(cli, arguments)
    if expect_log:
        outcome = log_result.call_args.args[1]
        compact = log_result.call_args.kwargs["data"]
    else:
        assert not log_result.called
        outcome = render.call_args.args[0]
        compact = None
    assert render.call_args.args[0] is outcome
    return result, outcome, compact


def test_library_search_is_read_only_and_renders_one_typed_outcome():
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
        expect_log=False,
    )

    assert result.exit_code == 0, result.output
    assert outcome.command == "library-search"
    assert outcome.status_value == "completed"
    assert outcome.data["rows"][0]["video_id"] == "video-id"
    assert outcome.data["summary"]["matches"] == 2
    assert compact is None


def test_library_context_stdout_is_read_only():
    library = MagicMock()
    library.get_context.return_value = "full context body"

    result, outcome, compact = _invoke_with_contract(
        library,
        ["library", "context", "science"],
        "_render_library_context",
        expect_log=False,
    )

    assert result.exit_code == 0, result.output
    assert outcome.command == "library-context"
    assert outcome.data["rows"] == [{"content": "full context body"}]
    assert outcome.data["summary"]["delivery"] == "rendered"
    assert compact is None


def test_library_context_creates_missing_output_parents(tmp_path):
    library = MagicMock()
    library.get_context.return_value = "full context body"
    output = tmp_path / "nested" / "research" / "context.txt"

    result, outcome, compact = _invoke_with_contract(
        library,
        [
            "library",
            "context",
            "science",
            "--output",
            str(output),
        ],
        "_render_library_context",
    )

    assert result.exit_code == 0, result.output
    assert output.read_text(encoding="utf-8") == "full context body"
    assert outcome.status_value == "completed"
    assert outcome.data["output"] == str(output)
    assert outcome.data["summary"]["delivery"] == "saved"
    assert compact["output"] == str(output)
    assert compact["delivery"] == "saved"


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

    is_mutation = method == "delete"
    result, outcome, compact = _invoke_with_contract(
        library,
        arguments,
        renderer,
        expect_log=is_mutation,
    )

    assert result.exit_code == 0, result.output
    assert outcome.command == command
    assert outcome.status_value == "completed"
    if is_mutation:
        assert "rows" not in compact
    else:
        assert compact is None


def test_library_compare_precomputes_rows_without_logging():
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
    library._find_match_details.return_value = [{
        "excerpt": "alpha and alpha",
        "start_char": 0,
        "end_char": 5,
        "start_seconds": None,
        "timestamp": None,
        "url": "https://youtube.com/watch?v=video-id",
    }]

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
        expect_log=False,
    )

    assert result.exit_code == 0, result.output
    assert outcome.command == "library-compare"
    assert outcome.status_value == "completed"
    assert outcome.data["rows"][0]["density"] == 1.0
    assert outcome.data["rows"][0]["excerpts"] == ["alpha and alpha"]
    assert outcome.data["rows"][0]["excerpt_details"][0]["url"].endswith(
        "video-id"
    )
    assert compact is None


@pytest.mark.parametrize(
    ("arguments", "method", "return_value", "expected_command"),
    [
        (
            ["library", "list", "science", "--raw"],
            "list_transcripts",
            [{"video_id": "video-id", "title": "Source"}],
            "library-list",
        ),
        (
            ["library", "search", "alpha", "--topic", "science", "--raw"],
            "search",
            [{
                "video_id": "video-id",
                "topic": "science",
                "match_count": 1,
                "matches": ["alpha"],
                "match_details": [],
            }],
            "library-search",
        ),
    ],
)
def test_library_inspection_raw_is_one_result_and_does_not_log(
    arguments,
    method,
    return_value,
    expected_command,
):
    library = MagicMock()
    getattr(library, method).return_value = return_value
    with (
        patch("filmot.library.get_library", return_value=library),
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(cli, arguments)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["_filmot"]["command"] == expected_command
    assert payload["rows"]
    log_result.assert_not_called()


def test_library_compare_raw_includes_timestamp_locator_and_does_not_log():
    library = MagicMock()
    library.search.return_value = [{
        "video_id": "video-id",
        "topic": "science",
        "title": "A source",
        "channel": "Primary Lab",
        "match_count": 1,
    }]
    library.get.return_value = {
        "metadata": {"duration_seconds": 120},
        "transcript": "before alpha after",
        "segments": [{"text": "before alpha after", "start": 42}],
    }
    library._find_match_details.return_value = [{
        "excerpt": "before alpha after",
        "start_char": 7,
        "end_char": 12,
        "start_seconds": 42.0,
        "timestamp": "0:42",
        "url": "https://youtube.com/watch?v=video-id&t=42s",
    }]
    with (
        patch("filmot.library.get_library", return_value=library),
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(
            cli,
            ["library", "compare", "alpha", "--topic", "science", "--raw"],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    detail = payload["rows"][0]["excerpt_details"][0]
    assert detail["start_seconds"] == 42.0
    assert detail["url"].endswith("&t=42s")
    log_result.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        ["library", "search", "alpha", "--raw"],
        ["library", "compare", "alpha", "--raw"],
    ],
)
def test_library_raw_inspection_never_starts_terminal_status(arguments):
    library = MagicMock()
    library.search.return_value = []
    with (
        patch("filmot.library.get_library", return_value=library),
        patch("filmot.commands.library.console.status") as status,
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(cli, arguments)

    assert result.exit_code == 0, result.output
    json.loads(result.stdout)
    status.assert_not_called()
    log_result.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        ["library", "search", "alpha", "--raw"],
        ["library", "compare", "alpha", "--raw"],
    ],
)
def test_library_raw_open_failure_is_one_structured_result(arguments):
    with patch(
        "filmot.library.get_library",
        side_effect=OSError("cannot open library"),
    ):
        result = CliRunner().invoke(cli, arguments)

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["message"] == "cannot open library"
    assert payload["_filmot"]["errors"][0]["stage"] == "open-library"


def test_library_list_raw_failure_is_one_structured_result():
    library = MagicMock()
    library.list_transcripts.side_effect = OSError("synthetic disk failure")
    with (
        patch("filmot.library.get_library", return_value=library),
        patch("filmot.ledger.log_result") as log_result,
    ):
        result = CliRunner().invoke(
            cli,
            ["library", "list", "science", "--raw"],
        )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["_filmot"]["command"] == "library-list"
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["stage"] == "list"
    assert payload["rows"] == []
    log_result.assert_not_called()


def test_library_raw_serializer_rejects_non_finite_values():
    library = MagicMock()
    library.list_transcripts.return_value = [{
        "video_id": "video-id",
        "score": float("nan"),
    }]
    with patch("filmot.library.get_library", return_value=library):
        result = CliRunner().invoke(
            cli,
            ["library", "list", "science", "--raw"],
        )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["stage"] == "serialize-result"
    assert payload["_filmot"]["errors"][0]["type"] == "InvalidJSONValue"
    json.dumps(payload, allow_nan=False)


def test_library_raw_escapes_unpaired_surrogates_safely():
    library = MagicMock()
    library.list_transcripts.return_value = [{
        "video_id": "video-id",
        "title": "\ud800",
    }]
    with patch("filmot.library.get_library", return_value=library):
        result = CliRunner().invoke(
            cli,
            ["library", "list", "science", "--raw"],
        )

    assert result.exit_code == 0, result.output
    assert "\\ud800" in result.stdout
    assert json.loads(result.stdout)["rows"][0]["title"] == "\ud800"


@pytest.mark.parametrize("malformed", [None, [{}]])
def test_library_search_malformed_rows_are_one_typed_raw_failure(malformed):
    library = MagicMock()
    library.search.return_value = malformed
    with patch("filmot.library.get_library", return_value=library):
        result = CliRunner().invoke(
            cli,
            ["library", "search", "alpha", "--substring", "--raw"],
        )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["stage"] == "build-result"
    assert payload["rows"] == []


@pytest.mark.parametrize(
    ("arguments", "method", "renderer", "stage"),
    [
        (
            ["library", "list", "science"],
            "list_transcripts",
            "_render_library_list",
            "list",
        ),
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

    is_mutation = method == "delete"
    result, outcome, compact = _invoke_with_contract(
        library,
        arguments,
        renderer,
        expect_log=is_mutation,
    )

    assert result.exit_code == 1
    assert outcome.status_value == "failed"
    assert outcome.errors[0].type == "OSError"
    assert outcome.errors[0].message == "synthetic disk failure"
    assert outcome.errors[0].stage == stage
    if is_mutation:
        assert "rows" not in compact
    else:
        assert compact is None


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


def test_library_context_help_explains_nested_output_contract():
    result = CliRunner().invoke(cli, ["library", "context", "--help"])

    assert result.exit_code == 0, result.output
    compact_output = " ".join(result.output.split())
    assert "creates missing parent directories" in compact_output
    assert "typed write-output failures" in compact_output


def test_library_context_parent_creation_failure_uses_write_contract():
    library = MagicMock()
    library.get_context.return_value = "context body"
    with (
        patch.object(
            Path,
            "mkdir",
            side_effect=OSError("parent unavailable"),
        ),
        patch("builtins.open") as output_file,
    ):
        result, outcome, compact = _invoke_with_contract(
            library,
            [
                "library",
                "context",
                "science",
                "--output",
                "nested/artifact.txt",
            ],
            "_render_library_context",
        )

    assert result.exit_code == 1
    output_file.assert_not_called()
    assert outcome.status_value == "failed"
    assert outcome.errors[0].type == "OSError"
    assert outcome.errors[0].message == "parent unavailable"
    assert outcome.errors[0].stage == "write-output"
    assert outcome.errors[0].details == {
        "output": "nested/artifact.txt"
    }
    assert compact["delivery"] == "failed"
    assert compact["output"] == "nested/artifact.txt"
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
