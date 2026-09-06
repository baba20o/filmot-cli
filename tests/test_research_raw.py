"""Research JSON must describe the same completed run as its durable ledger."""

import importlib
import json
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from filmot.api_contract import FilmotAPIContractError
from filmot.cli import cli
from filmot.ledger import read_events
from filmot.library import normalize_topic_name

research_module = importlib.import_module("filmot.commands.research")


def candidate(video_id):
    return {
        "id": video_id,
        "title": "Alpha beta field study",
        "channelname": "Research Channel",
        "duration": 600,
        "viewcount": 1000,
        "hits": [{"token": "alpha beta"}, {"token": "alpha beta"}],
    }


@pytest.fixture
def backend(monkeypatch, library):
    client = Mock()
    client.search_subtitles_all.return_value = {
        "result": [candidate("source00001"), candidate("source00002")],
        "totalresultcount": 2,
    }
    monkeypatch.setattr(research_module, "FilmotClient", lambda: client)
    monkeypatch.setattr("filmot.library.get_library", lambda: library)
    monkeypatch.setattr(
        research_module, "_backfill_metadata",
        lambda video_id, title, channel: (title, channel),
    )

    def transcript(video_id, **kwargs):
        # Some nested helpers print directly; their output cannot enter JSON.
        print("nested downloader diagnostic")
        return {
            "video_id": video_id,
            "full_text": "Alpha beta observations from the field.",
            "segments": [{"text": "Alpha beta observations.", "start": 0, "duration": 3}],
            "language": "en",
            "duration_seconds": 3,
        }

    monkeypatch.setattr("filmot.transcript.get_transcript", transcript)
    return client, library


def invoke(*args, scout=False):
    return CliRunner().invoke(
        cli,
        ["research", "alpha beta", "--depth", "2", "--raw",
         *([] if scout else ["--no-scout"]), *args],
    )


def assert_result_matches_ledger(result, *, status, exit_code, session="alpha-beta"):
    assert result.exit_code == exit_code, result.output
    # json.loads rejects a progress prefix, trailing rendering, or two results.
    payload = json.loads(result.stdout)
    metadata = payload.pop("_filmot")
    assert metadata["schema"] == "filmot.result/v1"
    assert metadata["command"] == "research"
    assert metadata["status"] == status
    events = read_events(session)
    outcomes = [event for event in events if event["kind"] == "research"]
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome["status"] == metadata["status"]
    assert outcome["errors"] == metadata["errors"]
    assert outcome["warnings"] == metadata["warnings"]
    assert outcome["data"] == payload
    assert events[-1]["kind"] == "research_end"
    assert events[-1]["status"] == status
    return payload, metadata


def test_raw_saved_research_keeps_diagnostics_off_stdout(backend):
    result = invoke()
    payload, metadata = assert_result_matches_ledger(result, status="completed", exit_code=0)
    assert payload["selected"] == payload["saved"] == 2
    assert len(payload["sources"]) == 2
    assert metadata["errors"] == []
    assert "Researching:" in result.stderr
    assert "nested downloader diagnostic" in result.stderr
    assert "Research complete:" not in result.stdout


@pytest.mark.parametrize("partial,expected", [(False, "empty"), (True, "partial")])
def test_raw_empty_search_distinguishes_incomplete_discovery(backend, partial, expected):
    client, _ = backend
    client.search_subtitles_all.return_value = {
        "result": [], "totalresultcount": 0,
        **({"partial": True, "page_error": "page 2 timed out"} if partial else {}),
    }
    payload, metadata = assert_result_matches_ledger(invoke(), status=expected, exit_code=0)
    assert payload["selected"] == payload["saved"] == 0
    assert bool(metadata["errors"]) == partial
    if partial:
        assert metadata["errors"][0]["type"] == "PartialSearch"


@pytest.mark.parametrize("fail_all,expected,code", [(False, "partial", 0), (True, "failed", 1)])
def test_raw_item_failures_preserve_accounting(backend, monkeypatch, fail_all, expected, code):
    def transcript(video_id, **kwargs):
        if fail_all or video_id == "source00002":
            return {"error": "captions unavailable", "error_type": "NoTranscript"}
        return {"full_text": "Alpha beta observations.", "segments": []}

    monkeypatch.setattr("filmot.transcript.get_transcript", transcript)
    payload, metadata = assert_result_matches_ledger(invoke(), status=expected, exit_code=code)
    assert payload["selected"] == 2
    assert payload["failed"] == (2 if fail_all else 1)
    assert payload["saved"] == (0 if fail_all else 1)
    assert metadata["errors"][0]["type"] == "ResearchItemFailure"


@pytest.mark.parametrize("error,expected_type,status", [
    (RuntimeError("transport down"), "RuntimeError", "failed"),
    (ValueError("missing configuration"), "ValueError", "failed"),
    (FilmotAPIContractError("search", "$.result", "array", "object"), "FilmotAPIContractError", "failed"),
    (KeyboardInterrupt(), "KeyboardInterrupt", "interrupted"),
])
def test_raw_search_exception_is_one_typed_failure(backend, error, expected_type, status):
    client, _ = backend
    client.search_subtitles_all.side_effect = error
    payload, metadata = assert_result_matches_ledger(invoke(), status=status, exit_code=1)
    assert payload["phase"] == "search"
    assert metadata["errors"][-1]["type"] == expected_type


def test_raw_library_initialization_failure_is_recorded(monkeypatch, backend):
    def unavailable():
        raise OSError("library unavailable")

    monkeypatch.setattr("filmot.library.get_library", unavailable)
    payload, metadata = assert_result_matches_ledger(invoke(), status="failed", exit_code=1)
    assert payload["phase"] == "initializing"
    assert payload["sources"] == []
    assert metadata["errors"][0]["type"] == "OSError"


def test_raw_search_error_response_is_typed_failure(backend):
    client, _ = backend
    client.search_subtitles_all.return_value = {"error": "search quota exceeded"}
    _, metadata = assert_result_matches_ledger(invoke(), status="failed", exit_code=1)
    assert metadata["errors"][0]["type"] == "ClickException"
    assert "search quota exceeded" in metadata["errors"][0]["message"]


@pytest.mark.parametrize("session", [None, "named investigation"])
def test_raw_serialization_failure_is_failed_in_ledger_too(backend, monkeypatch, session):
    _, library = backend
    monkeypatch.setattr(library, "list_transcripts", lambda topic: [{"duration": float("nan")}])
    _, metadata = assert_result_matches_ledger(
        invoke("--depth", "0", *(["--session", session] if session else [])),
        status="failed", exit_code=1,
        session=normalize_topic_name(session or "alpha beta", fallback="session"),
    )
    assert metadata["errors"][0]["type"] == "InvalidJSONValue"


def test_raw_scout_failure_marks_research_partial(backend, monkeypatch):
    monkeypatch.setattr("filmot.youtube_search.validate_youtube_api", lambda: None)
    monkeypatch.setattr(
        "filmot.youtube_search.search_recent",
        Mock(side_effect=RuntimeError("scout transport unavailable")),
    )
    payload, metadata = assert_result_matches_ledger(invoke(scout=True), status="partial", exit_code=0)
    assert payload["saved"] == 2
    assert metadata["errors"][0]["stage"] == "scout"


@pytest.mark.parametrize("session", ["named investigation", "!!!"])
def test_raw_research_session_routes_results_and_saves_without_changing_topic(backend, session):
    normalized = normalize_topic_name(session, fallback="session")
    result = invoke("--session", session)
    payload, _ = assert_result_matches_ledger(
        result, status="completed", exit_code=0, session=normalized,
    )
    assert payload["topic"] == "alpha-beta"
    assert payload["session"] == normalized
    assert len(backend[1].list_transcripts("alpha-beta")) == 2
    events = read_events(normalized)
    saves = [event for event in events
             if event["kind"] == "research_checkpoint"
             and event["data"].get("phase") == "download_item"
             and event["data"].get("detail_status") == "saved"]
    assert len(saves) == 2
    assert {path.stem for path in (backend[1].data_dir / "sessions").glob("*.jsonl")} == {
        normalized,
    }


def test_human_research_replay_hint_uses_selected_session(backend):
    result = CliRunner().invoke(cli, [
        "research", "alpha beta", "--no-scout", "--depth", "0", "--session", "named-investigation",
    ])
    assert result.exit_code == 0, result.output
    assert "filmot sessions named-investigation --summary" in result.stdout
    assert "filmot library echoes alpha-beta" in result.stdout
