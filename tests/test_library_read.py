"""Local evidence reads must never turn an unresolved selection into a fetch."""

import json
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from filmot.cli import cli
from filmot.passages import parse_passage_time


VIDEO = "abc12345678"


@pytest.fixture(autouse=True)
def no_acquisition_or_logging(monkeypatch):
    guards = []
    for target in (
        "filmot.transcript.get_transcript",
        "filmot.transcript.get_transcript_with_timestamps",
        "filmot.transcript.get_transcript_with_fallback",
        "filmot.transcript._init_api",
        "filmot.transcript.configure_proxy",
        "filmot.api.FilmotClient.__init__",
        "requests.sessions.Session.request",
        "socket.create_connection",
        "filmot.ledger.log_result",
        "filmot.ledger.log_event",
    ):
        guard = Mock(side_effect=AssertionError("unexpected side effect: " + target))
        monkeypatch.setattr(target, guard)
        guards.append(guard)
    yield
    for guard in guards:
        guard.assert_not_called()


@pytest.fixture
def invoke(library, monkeypatch):
    monkeypatch.setattr("filmot.library.get_library", lambda: library)

    def run(*arguments, raw=True, video=VIDEO):
        args = ["library", "read", video, "--from", "10", "--to", "20", *arguments]
        if raw:
            args.append("--raw")
        result = CliRunner().invoke(cli, args)
        return result, json.loads(result.output) if raw else result.output

    return run


def save(library, topic="science", *, segments=None, language="en"):
    if segments is None:
        segments = [
            {"start": 0, "duration": 10, "text": "before"},
            {"start": 9.25, "duration": 2, "text": "overlap"},
            {"start": 10, "duration": 5, "text": "alpha [red] literal"},
            {"start": 19.5, "duration": 4, "text": "beta\nline"},
            {"start": 20, "duration": 5, "text": "after"},
        ]
    return library.save(
        VIDEO, topic,
        " ".join(row["text"].replace("\n", " ") for row in segments),
        metadata={"language": language, "source": "youtube", "route": "direct",
                  "title": "Evidence source", "is_generated": False},
        segments=segments,
    )


def rewrite(path, mutate):
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_reads_exact_saved_window_with_provenance_without_writes(library, invoke):
    path = save(library)
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns)
              for p in library.data_dir.rglob("*") if p.is_file()}
    result, payload = invoke(video="https://youtu.be/" + VIDEO)
    assert result.exit_code == 0, result.output
    assert payload["_filmot"]["status"] == "completed"
    assert payload["_filmot"]["command"] == "library-read"
    assert payload["local_only"] is True
    assert payload["topic"] == "science"
    assert [r["segment_index"] for r in payload["rows"]] == [1, 2, 3]
    assert payload["rows"][0]["timestamp"] == "0:09.25"
    assert payload["rows"][0]["start_seconds"] == 9.25
    assert payload["rows"][0]["end_seconds"] == 11.25
    assert payload["rows"][-1]["end_seconds"] == 23.5
    assert payload["rows"][0]["url"].endswith("&t=9s")
    assert payload["saved_copy"]["path"] == str(path.resolve())
    assert payload["saved_copy"]["language"] == "en"
    assert payload["saved_copy"]["is_generated"] is False
    assert payload["saved_copy"]["source"]["transcript_source"] == "youtube"
    assert payload["saved_copy"]["saved_at"]
    assert payload["summary"]["truncated"] is False
    assert before == {str(p): (p.read_bytes(), p.stat().st_mtime_ns)
                      for p in library.data_dir.rglob("*") if p.is_file()}


def test_missing_copy_fails_without_creating_storage(library, invoke):
    result, payload = invoke()
    assert result.exit_code == 1
    assert payload["_filmot"]["errors"][0]["type"] == "SavedTranscriptNotFound"
    assert "NEW_TOPIC" in payload["acquisition_guidance"]
    assert not library.data_dir.exists()


@pytest.mark.parametrize("different_language", [False, True])
def test_ambiguity_requires_topic_even_for_identical_copies(library, invoke, different_language):
    save(library, "first")
    save(library, "second", language="fr" if different_language else "en")
    result, payload = invoke()
    assert result.exit_code == 1
    assert payload["_filmot"]["errors"][0]["type"] == "AmbiguousSavedTranscript"
    assert [c["topic"] for c in payload["candidates"]] == ["first", "second"]
    assert payload["rows"] == []
    selected, selected_payload = invoke("--topic", "second")
    assert selected.exit_code == 0
    assert selected_payload["saved_copy"]["language"] == ("fr" if different_language else "en")


def test_missing_topic_does_not_read_another_topic(library, invoke):
    save(library)
    result, payload = invoke("--topic", "missing")
    assert result.exit_code == 1
    assert payload["_filmot"]["errors"][0]["type"] == "SavedTranscriptNotFound"
    assert not (library.transcripts_dir / "missing").exists()


def test_character_and_segment_bounds_are_explicit_and_apply_to_raw(library, invoke):
    save(library)
    result, payload = invoke("--max-chars", "9", "--max-segments", "2")
    assert result.exit_code == 0
    assert [row["text"] for row in payload["rows"]] == ["overlap", "al"]
    assert payload["rows"][-1]["text_truncated"] is True
    assert payload["summary"]["returned_chars"] == 9
    assert payload["summary"]["returned_segments"] == 2
    assert payload["summary"]["omitted_segments"] == 1
    assert payload["summary"]["truncated"] is True
    assert "truncated" in payload["_filmot"]["warnings"][0]
    result, payload = invoke("--max-segments", "1")
    assert result.exit_code == 0
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["text_truncated"] is False
    assert payload["summary"]["truncated"] is True


def test_default_limits_bound_large_saved_passage(library, invoke):
    save(library, segments=[{"start": 10 + i / 100, "duration": 1, "text": "x" * 100}
                            for i in range(100)])
    result, payload = invoke()
    assert result.exit_code == 0
    assert payload["summary"]["matching_chars"] == 10000
    assert payload["summary"]["returned_chars"] == 6000
    assert payload["summary"]["truncated"] is True
    result, payload = invoke("--max-chars", "100000")
    assert result.exit_code == 0
    assert payload["summary"]["returned_segments"] == 80
    assert payload["summary"]["omitted_segments"] == 20


def test_ambiguity_inventory_is_bounded(library, invoke):
    for index in range(26):
        save(library, "topic-{:02d}".format(index))
    result, payload = invoke()
    assert result.exit_code == 1
    assert len(payload["candidates"]) == 25
    assert payload["candidate_count"] == 26
    assert payload["candidates_truncated"] is True


def test_empty_window_is_typed_empty_not_missing(library, invoke):
    save(library)
    result, payload = invoke("--from", "1:00", "--to", "1:05.5")
    assert result.exit_code == 0
    assert payload["_filmot"]["status"] == "empty"
    assert payload["window"]["to_seconds"] == 65.5
    assert payload["summary"]["matching_segments"] == 0
    assert payload["summary"]["truncated"] is False


def test_missing_duration_and_unknown_language_are_explicit(library, invoke):
    save(library, language=None, segments=[
        {"start": 9, "text": "unknown earlier extent"},
        {"start": 10, "text": "at start"},
        {"start": 19, "duration": 0, "text": "zero duration"},
        {"start": 20, "text": "at end"},
    ])
    result, payload = invoke()
    assert result.exit_code == 0
    assert [row["segment_index"] for row in payload["rows"]] == [1, 2]
    assert payload["saved_copy"]["language"] is None
    assert payload["rows"][0]["end_seconds"] is None
    assert payload["summary"]["segments_without_duration"] == 1
    assert "unknown" in payload["_filmot"]["warnings"][0]


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(video_id="wrong123456"),
    lambda d: d.update(transcript="does not match the caption segments"),
    lambda d: d["segments"][2].update(start=-1),
    lambda d: d["segments"][2].update(start=8),
    lambda d: d["segments"][2].update(start=True),
    lambda d: d["segments"][2].update(start="10"),
    lambda d: d["segments"][2].update(duration=-1),
    lambda d: d["segments"][2].update(duration=float("nan")),
    lambda d: d["segments"][2].update(text=None),
    lambda d: d["segments"].append("broken"),
    # Integers too large for a float once raised OverflowError.
    lambda d: d["segments"][2].update(start=10 ** 400),
    lambda d: d["segments"][2].update(duration=10 ** 400),
])
def test_malformed_saved_evidence_fails_locally(library, invoke, mutate):
    path = save(library)
    rewrite(path, mutate)
    result, payload = invoke()
    assert result.exit_code == 1
    assert payload["_filmot"]["status"] == "failed"
    assert payload["rows"] == []
    error = payload["_filmot"]["errors"][0]
    assert error["stage"] == "read-saved-copy"
    assert error["type"] == "InvalidSavedTranscript"


@pytest.mark.parametrize("stored", ["{bad json", "[]", "null", '{"video_id": "abc12345678", "x": NaN}'])
def test_invalid_json_shape_fails_locally(library, invoke, stored):
    path = save(library)
    path.write_text(stored, encoding="utf-8")
    result, payload = invoke()
    assert result.exit_code == 1
    assert payload["_filmot"]["status"] == "failed"
    assert payload["_filmot"]["errors"][0]["type"] == "InvalidSavedTranscript"


@pytest.mark.parametrize("damage", ["directory", "nested"])
def test_unreadable_and_deeply_nested_records_fail_as_invalid(library, invoke, damage):
    path = save(library)
    path.unlink()
    if damage == "directory":
        path.mkdir()  # opening it raises an OSError
    else:
        path.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")  # RecursionError
    result, payload = invoke()
    assert result.exit_code == 1
    assert payload["_filmot"]["errors"][0]["type"] == "InvalidSavedTranscript"


def test_a_formatting_preserving_save_is_read(library, invoke):
    # preserve_formatting=True joins caption texts with line breaks.
    segments = [
        {"start": 9, "duration": 3, "text": "first line"},
        {"start": 12, "duration": 3, "text": "second\nline"},
    ]
    library.save(VIDEO, "science", "\n".join(row["text"] for row in segments), segments=segments)

    result, payload = invoke()

    assert result.exit_code == 0, result.output
    assert [row["text"] for row in payload["rows"]] == ["first line", "second\nline"]


def test_unicode_topics_and_index_transcripts_are_read(library, invoke):
    segments = [{"start": 10.5, "duration": 2.0, "text": "索引の字幕"}]
    library.save(
        VIDEO, "人工知能", segments[0]["text"],
        metadata={"language": "ja", "source": "transcript", "route": "filmot_index",
                  "transcript_index": {"observed_at": "2026-10-01T00:00:00Z", "language": "ja"}},
        segments=segments,
    )

    result, payload = invoke("--topic", "人工知能")

    assert result.exit_code == 0, result.output
    assert payload["rows"][0]["text"] == "索引の字幕"
    assert payload["saved_copy"]["route"] == "filmot_index"
    assert payload["saved_copy"]["language"] == "ja"


def test_text_only_record_fails_with_untimed_guidance(library, invoke):
    library.save(VIDEO, "science", "A saved text-only transcript")
    result, payload = invoke()
    assert result.exit_code == 1
    error = payload["_filmot"]["errors"][0]
    assert error["type"] == "UntimedSavedTranscript"
    assert "library context" in error["message"]


@pytest.mark.parametrize("start,end", [("NaN", "20"), ("-1", "20"), ("1:99", "200"),
                                      ("10", "10"), ("20", "10"), ("1:60:00", "9000")])
def test_invalid_window_fails_before_library_lookup(invoke, monkeypatch, start, end):
    lookup = Mock(side_effect=AssertionError("must validate first"))
    monkeypatch.setattr("filmot.library.get_library", lookup)
    result, payload = invoke("--from", start, "--to", end)
    assert result.exit_code == 1
    assert payload["_filmot"]["errors"][0]["stage"] == "parse-selection"
    lookup.assert_not_called()


def test_human_passage_preserves_literal_text_and_reports_clipping(library, invoke):
    save(library)
    result, output = invoke("--max-chars", "27", raw=False)
    assert result.exit_code == 0, output
    assert "language: en" in output
    assert "source: youtube" in output
    assert "Evidence source" in output
    assert "[0:09.25 - 0:11.25]" in output
    assert "alpha [red] literal" in output
    assert "[text truncated]" in output
    assert "truncated: yes" in output


def test_human_missing_and_ambiguous_selection_show_actionable_guidance(library, invoke):
    result, output = invoke(raw=False)
    assert result.exit_code == 1
    assert "NEW_TOPIC" in output
    assert "No network request" in output
    save(library, "first")
    save(library, "second")
    result, output = invoke(raw=False)
    assert result.exit_code == 1
    assert "Topic: first" in output and "Topic: second" in output
    assert "--topic" in output


@pytest.mark.parametrize("value,expected", [("90", 90), ("1:30.25", 90.25), ("1:02:03", 3723)])
def test_time_formats(value, expected):
    assert parse_passage_time(value) == expected
