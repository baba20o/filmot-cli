"""Regression tests for defects found in the claims/evidence-register review.

Each test pins a behavior that the existing suite did not cover:

* Documented v1 events may omit errors/warnings/command/status.
* One unreadable legacy session line must not block future ledger writes.
* ``library context`` must not build its default filename from a raw topic.
"""

import json

from filmot import ledger
from filmot.cli import cli
from filmot.schemas import EVENT_SCHEMA, normalize_event_dict

import pytest
from click.testing import CliRunner


@pytest.fixture
def runner():
    # Modern Click exposes stdout and stderr separately on Result while keeping
    # their interleaved form in ``output``. Older supported Click releases also
    # work with the argument-free runner; ``mix_stderr`` was removed in 8.5.
    return CliRunner()


def test_v1_event_defaults_are_restored():
    event = normalize_event_dict({
        "schema": EVENT_SCHEMA,
        "ts": "2026-01-01T00:00:00",
        "kind": "search",
        "data": {"query": "x"},
    })

    assert event["command"] == "search"
    assert event["status"] == "completed"
    assert event["errors"] == []
    assert event["warnings"] == []


def test_bad_legacy_line_does_not_block_future_ledger_writes(tmp_path):
    topic = "人工知能"
    sessions = ledger._sessions_dir(tmp_path)
    sessions.mkdir(parents=True, exist_ok=True)
    legacy = sessions / "session.jsonl"
    legacy.write_text(
        json.dumps({"schema": "filmot.event/v9", "ts": "t", "kind": "search"})
        + "\n"
        + json.dumps({
            "ts": "2026-01-01T00:00:00",
            "kind": "search",
            "topic": topic,
            "query": "x",
        })
        + "\n",
        encoding="utf-8",
    )

    ledger.log_event(
        "research", topic=topic, data_dir=tmp_path,
        status="completed", note="fresh-event",
    )

    canonical = sessions / "{}.jsonl".format(ledger._normalize(topic))
    assert canonical.exists(), "canonical session file was never written"
    canonical_text = canonical.read_text(encoding="utf-8")
    assert "fresh-event" in canonical_text
    assert '"query": "x"' in canonical_text  # migrated good line
    # The unreadable line stays where it was instead of being dropped.
    assert "filmot.event/v9" in legacy.read_text(encoding="utf-8")


def test_library_context_default_filename_uses_normalized_topic(runner, tmp_path):
    from filmot.library import get_library

    get_library().save(
        video_id="vid",
        topic="../../notes",
        transcript_text="context text",
        metadata={"title": "t"},
        segments=[],
    )

    result = runner.invoke(
        cli, ["library", "context", "../../notes", "--format", "structured"]
    )

    assert result.exit_code == 0, result.output
    outside = tmp_path.parent.parent / "notes-context.md"
    assert not outside.exists()
    written = list(tmp_path.glob("*-context.md"))
    assert written, "structured context was not written into the working tree"
