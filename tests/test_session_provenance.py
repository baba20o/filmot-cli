"""Focused contracts for bounded compound-research session provenance."""

import json
from unittest.mock import patch

from click.testing import CliRunner

from filmot.cli import cli
from filmot.commands.library import _scout_reproduction_argv
from filmot.ledger import SESSION_PROVENANCE_ROW_LIMIT, summarize_events


def _compound_events():
    return [
        {
            "ts": "2026-01-01T00:00:00",
            "kind": "research_checkpoint",
            "status": "started",
            "data": {
                "run_id": "run-1",
                "phase": "scout",
                "query": "alpha beta",
                "days": 7,
                "max_results": 10,
                "order": "relevance",
            },
        },
        {
            "ts": "2026-01-01T00:00:01",
            "kind": "research_checkpoint",
            "status": "completed",
            "data": {
                "run_id": "run-1",
                "phase": "scout",
                "results": 9,
            },
        },
        {
            "ts": "2026-01-01T00:00:02",
            "kind": "research_checkpoint",
            "status": "completed",
            "data": {
                "run_id": "run-1",
                "phase": "scout_gate",
                "candidates_before": 9,
                "candidates_after": 3,
            },
        },
        {
            "ts": "2026-01-01T00:00:03",
            "kind": "research_checkpoint",
            "status": "completed",
            "data": {
                "run_id": "run-1",
                "phase": "search",
                "stage": "exact_phrase",
                "query": '"alpha beta"',
                "candidates": 4,
            },
        },
        {
            "ts": "2026-01-01T00:00:04",
            "kind": "research_checkpoint",
            "status": "started",
            "data": {
                "run_id": "run-1",
                "phase": "download_item",
                "video_id": "selected-id",
                "title": "Selected source",
                "stage": "exact_phrase",
                "signals": {
                    "passage_coverage": 1.0,
                    "private_excerpt": "DO NOT COPY THIS",
                },
            },
        },
        {
            "ts": "2026-01-01T00:00:05",
            "kind": "research_checkpoint",
            "status": "completed",
            "data": {
                "run_id": "run-1",
                "phase": "download_item",
                "video_id": "selected-id",
                "channel": "Selected channel",
                "stage": "exact_phrase",
                "detail_status": "saved",
            },
        },
        {
            "ts": "2026-01-01T00:00:06",
            "kind": "research_checkpoint",
            "status": "started",
            "data": {
                "run_id": "run-1",
                "phase": "probe_search",
                "index": 2,
                "query": '"entity one" NEAR/15 "entity two"',
                "constraints": {"title": "alpha beta", "lang": "en"},
                "co_windows": 8,
                "source_support": 3,
            },
        },
        {
            "ts": "2026-01-01T00:00:07",
            "kind": "research_probe",
            "status": "completed",
            "data": {
                "run_id": "run-1",
                "query": '"entity one" NEAR/15 "entity two"',
                "api_total": 99,
                "returned": 10,
                "scoped": 2,
            },
        },
        {
            "ts": "2026-01-01T00:00:08",
            "kind": "research_checkpoint",
            "status": "started",
            "data": {
                "run_id": "run-1",
                "phase": "probe_download",
                "video_id": "probe-id",
                "title": "Probe source",
                "probe_query": '"entity one" NEAR/15 "entity two"',
                "probe_index": 2,
            },
        },
        {
            "ts": "2026-01-01T00:00:09",
            "kind": "research_checkpoint",
            "status": "completed",
            "data": {
                "run_id": "run-1",
                "phase": "probe_download",
                "video_id": "probe-id",
                "probe_query": '"entity one" NEAR/15 "entity two"',
                "probe_index": 2,
                "detail_status": "saved",
                "route_errors": ["must stay out of summary"],
            },
        },
        {
            "ts": "2026-01-01T00:00:10",
            "kind": "research",
            "status": "completed",
            "data": {
                "run_id": "run-1",
                "sources": [{
                    "video_id": "probe-id",
                    "title": "Probe source",
                    "channel": "Probe channel",
                    "path": "/private/research/path",
                    "transcript": "PRIVATE TRANSCRIPT TEXT",
                }],
            },
        },
        {
            "kind": "claims_add",
            "status": "completed",
            "data": {"claim_id": "c-one", "text": "PRIVATE CLAIM TEXT"},
        },
    ]


def test_summary_folds_scout_probe_and_saved_source_provenance():
    summary = summarize_events("topic", _compound_events())
    provenance = summary["research_provenance"]

    scout = provenance["scout_runs"]
    assert (scout["total"], scout["shown"], scout["omitted"]) == (1, 1, 0)
    assert scout["rows"][0] == {
        "run_id": "run-1",
        "query": "alpha beta",
        "days": 7,
        "max_results": 10,
        "order": "relevance",
        "channel_id": None,
        "request_channel_id": None,
        "candidates_found": 9,
        "gate_before": 9,
        "gate_after": 3,
        "status": "completed",
        "ts": "2026-01-01T00:00:02",
    }

    probes = provenance["probe_queries"]
    assert probes["total"] == 1  # checkpoint + research_probe are one query
    assert probes["rows"][0]["index"] == 2
    assert probes["rows"][0]["co_windows"] == 8
    assert probes["rows"][0]["source_support"] == 3
    assert probes["rows"][0]["api_total"] == 99
    assert probes["rows"][0]["scoped"] == 2

    sources = provenance["saved_sources"]
    assert sources["origins"] == {"exact_phrase": 1, "probe": 1}
    selected, probe = sources["rows"]
    assert selected["origin_query"] == '"alpha beta"'
    assert selected["selection_signals"] == {"passage_coverage": 1.0}
    assert probe["origin_stage"] == "probe"
    assert probe["origin_query"] == '"entity one" NEAR/15 "entity two"'
    assert probe["probe_index"] == 2
    assert probe["title"] == "Probe source"
    assert probe["channel"] == "Probe channel"
    assert probe["provenance_status"] == "recorded"

    encoded = json.dumps(provenance)
    for private_value in (
        "DO NOT COPY THIS",
        "PRIVATE TRANSCRIPT TEXT",
        "PRIVATE CLAIM TEXT",
        "/private/research/path",
        "must stay out of summary",
    ):
        assert private_value not in encoded


def test_summary_marks_legacy_probe_origin_and_bounds_recent_rows():
    events = []
    for index in range(SESSION_PROVENANCE_ROW_LIMIT + 3):
        query = "query-{}-{}".format(index, "x" * 300)
        video_id = "video-{:02d}".format(index)
        events.extend([
            {
                "kind": "research_checkpoint",
                "status": "completed",
                "data": {
                    "run_id": "run-bounded",
                    "phase": "probe_search",
                    "index": index,
                    "query": query,
                    "scoped": 1,
                },
            },
            {
                "kind": "research_checkpoint",
                "status": "started",
                "data": {
                    "run_id": "run-bounded",
                    "phase": "probe_download",
                    "video_id": video_id,
                    "title": "title-{}-{}".format(index, "y" * 300),
                },
            },
            {
                "kind": "research_checkpoint",
                "status": "completed",
                "data": {
                    "run_id": "run-bounded",
                    "phase": "probe_download",
                    "video_id": video_id,
                    "detail_status": "saved",
                },
            },
        ])

    provenance = summarize_events("topic", events)["research_provenance"]
    for section_name in ("probe_queries", "saved_sources"):
        section = provenance[section_name]
        assert section["total"] == SESSION_PROVENANCE_ROW_LIMIT + 3
        assert section["shown"] == SESSION_PROVENANCE_ROW_LIMIT
        assert section["omitted"] == 3
    assert provenance["probe_queries"]["rows"][0]["index"] == 3
    assert all(
        len(row["query"]) <= provenance["limits"]["query_chars"]
        for row in provenance["probe_queries"]["rows"]
    )
    assert all(
        len(row["title"]) <= provenance["limits"]["label_chars"]
        for row in provenance["saved_sources"]["rows"]
    )
    assert all(
        row["origin_query"] is None
        and row["provenance_status"] == "query_not_recorded"
        for row in provenance["saved_sources"]["rows"]
    )


def test_sessions_summary_renders_compact_provenance_tables():
    runner = CliRunner()
    with patch("filmot.ledger.read_events", return_value=_compound_events()):
        result = runner.invoke(cli, ["sessions", "topic", "--summary"])

    assert result.exit_code == 0, result.output
    assert "Scout provenance" in result.output
    assert "Probe query provenance" in result.output
    assert "Saved source provenance" in result.output
    assert "7d / relevance / n=10" in result.output
    assert "Inspect scout candidates:" in result.output
    assert "--order relevance" in result.output
    assert "Probe source" in result.output
    assert '"entity one" NEAR/15 "entity' in result.output
    assert 'two"' in result.output
    assert "PRIVATE CLAIM TEXT" not in result.output

    argv = _scout_reproduction_argv(
        summarize_events("topic", _compound_events())[
            "research_provenance"
        ]["scout_runs"]["rows"][0]
    )
    assert argv == [
        "filmot",
        "yt-search",
        "alpha beta",
        "--days",
        "7",
        "--max-results",
        "10",
        "--order",
        "relevance",
        "--show-description",
        "--raw",
    ]


def test_sessions_raw_summary_adds_provenance_without_replay_body():
    runner = CliRunner()
    with patch("filmot.ledger.read_events", return_value=_compound_events()):
        result = runner.invoke(
            cli, ["sessions", "topic", "--summary", "--raw"]
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["_filmot"]["schema"] == "filmot.result/v1"
    assert payload["_filmot"]["status"] == "completed"
    assert "events" not in payload
    assert payload["summary"]["research_provenance"]["saved_sources"][
        "origins"
    ] == {"exact_phrase": 1, "probe": 1}


def test_probe_provenance_preserves_deferred_and_sampled_states():
    events = [
        {
            "kind": "research_checkpoint",
            "status": "completed",
            "data": {
                "run_id": "run-states",
                "phase": "probe_search",
                "query": '"broad pair" NEAR/15 "topic phrase"',
                "detail_status": "broad_sampled",
                "api_total": 5000,
                "returned": 50,
                "scoped": 0,
            },
        },
        {
            "kind": "research_checkpoint",
            "status": "completed",
            "data": {
                "run_id": "run-states",
                "phase": "probe_search",
                "query": '"lower pair" NEAR/15 "topic phrase"',
                "detail_status": "deferred",
                "reason": "broad_sampled_tail_budget",
            },
        },
    ]

    rows = summarize_events("topic", events)["research_provenance"][
        "probe_queries"
    ]["rows"]
    assert [row["status"] for row in rows] == [
        "broad_sampled",
        "deferred",
    ]
    assert rows[0]["api_total"] == 5000
    assert rows[1]["api_total"] == 0


def test_legacy_title_transcript_stage_joins_recorded_search_query():
    events = [
        {
            "kind": "research_checkpoint",
            "status": "completed",
            "data": {
                "run_id": "run-title",
                "phase": "search",
                "stage": "title+transcript",
                "query": "alpha beta",
                "candidates": 1,
            },
        },
        {
            "kind": "research_checkpoint",
            "status": "completed",
            "data": {
                "run_id": "run-title",
                "phase": "download_item",
                "video_id": "selected-id",
                "title": "Selected source",
                "stage": "title_transcript",
                "detail_status": "saved",
            },
        },
    ]

    source = summarize_events("topic", events)["research_provenance"][
        "saved_sources"
    ]["rows"][0]
    assert source["origin_stage"] == "title+transcript"
    assert source["origin_query"] == "alpha beta"
    assert source["provenance_status"] == "recorded"


def test_manual_transcript_saves_are_deduped_with_research_precedence():
    events = [
        {
            "ts": "2026-01-01T00:00:00",
            "kind": "research_checkpoint",
            "status": "completed",
            "data": {
                "run_id": "run-selected",
                "phase": "search",
                "stage": "exact_phrase",
                "query": '"alpha beta"',
                "candidates": 1,
            },
        },
        {
            "ts": "2026-01-01T00:00:01",
            "kind": "transcript_save",
            "status": "completed",
            "data": {
                "video_id": "manual-id",
                "title": "Manual source",
                "channel": "Manual channel",
            },
        },
        {
            "ts": "2026-01-01T00:00:02",
            "kind": "transcript_save",
            "status": "completed",
            "data": {"video_id": "manual-id"},
        },
        {
            "ts": "2026-01-01T00:00:03",
            "kind": "transcript_save",
            "status": "completed",
            "data": {"video_id": "selected-id"},
        },
        {
            "ts": "2026-01-01T00:00:04",
            "kind": "research_checkpoint",
            "status": "completed",
            "data": {
                "run_id": "run-selected",
                "phase": "download_item",
                "stage": "exact_phrase",
                "video_id": "selected-id",
                "title": "Selected source",
                "detail_status": "saved",
            },
        },
        {
            "ts": "2026-01-01T00:00:05",
            "kind": "transcript_save",
            "status": "completed",
            "data": {"video_id": "selected-id"},
        },
        {
            "ts": "2026-01-01T00:00:06",
            "kind": "transcript_save",
            "status": "completed",
            "data": {"video_id": "probe-id"},
        },
        {
            "ts": "2026-01-01T00:00:07",
            "kind": "research_checkpoint",
            "status": "completed",
            "data": {
                "run_id": "run-selected",
                "phase": "probe_download",
                "video_id": "probe-id",
                "probe_query": '"alpha" NEAR/15 "beta"',
                "detail_status": "saved",
            },
        },
        {
            "kind": "transcript_save",
            "status": "failed",
            "data": {"video_id": "failed-id"},
        },
        {
            "kind": "transcript_save",
            "status": "skipped",
            "data": {"video_id": "skipped-id"},
        },
    ]

    sources = summarize_events("topic", events)["research_provenance"][
        "saved_sources"
    ]
    assert sources["total"] == 3
    assert sources["origins"] == {
        "exact_phrase": 1,
        "manual": 1,
        "probe": 1,
    }
    rows = {row["video_id"]: row for row in sources["rows"]}
    assert set(rows) == {"manual-id", "probe-id", "selected-id"}
    assert rows["manual-id"] == {
        "run_id": None,
        "video_id": "manual-id",
        "title": "Manual source",
        "channel": "Manual channel",
        "origin_stage": "manual",
        "saved_at": "2026-01-01T00:00:02",
        "origin_query": None,
        "provenance_status": "query_not_recorded",
    }
    assert rows["selected-id"]["origin_stage"] == "exact_phrase"
    assert rows["selected-id"]["origin_query"] == '"alpha beta"'
    assert rows["selected-id"]["provenance_status"] == "recorded"
    assert rows["probe-id"]["origin_stage"] == "probe"
    assert rows["probe-id"]["origin_query"] == '"alpha" NEAR/15 "beta"'
    assert rows["probe-id"]["provenance_status"] == "recorded"


def test_manual_saved_source_rows_remain_bounded():
    events = [
        {
            "ts": "2026-01-01T00:00:{:02d}".format(index),
            "kind": "transcript_save",
            "status": "completed",
            "data": {"video_id": "manual-{:02d}".format(index)},
        }
        for index in range(SESSION_PROVENANCE_ROW_LIMIT + 3)
    ]

    sources = summarize_events("topic", events)["research_provenance"][
        "saved_sources"
    ]
    assert sources["total"] == SESSION_PROVENANCE_ROW_LIMIT + 3
    assert sources["shown"] == SESSION_PROVENANCE_ROW_LIMIT
    assert sources["omitted"] == 3
    assert sources["origins"] == {
        "manual": SESSION_PROVENANCE_ROW_LIMIT + 3
    }
    assert sources["rows"][0]["video_id"] == "manual-03"


def test_probe_run_summary_preserves_terminal_empty_and_legacy_unknowns():
    events = [
        {
            "ts": "2026-01-01T00:00:00",
            "kind": "research_checkpoint",
            "status": "started",
            "data": {"run_id": "run-empty", "phase": "probe"},
        },
        {
            "ts": "2026-01-01T00:00:01",
            "kind": "research_checkpoint",
            "status": "empty",
            "data": {
                "run_id": "run-empty",
                "phase": "probe",
                "reason": "no_cross_source_pairs",
                "eligible_seeds": 8,
                "terms": 12,
                "queries": 0,
                "queries_planned": 0,
                "queries_deferred": 0,
                "query_failed": 1,
                "download_failed": 2,
                "saved": 0,
            },
        },
        {
            "ts": "2025-01-01T00:00:00",
            "kind": "research_checkpoint",
            "status": "skipped",
            "data": {
                "run_id": "legacy-skip",
                "phase": "probe",
                "reason": "insufficient_eligible_seeds",
                "eligible_seeds": 1,
            },
        },
    ]

    probe_runs = summarize_events("topic", events)["research_provenance"][
        "probe_runs"
    ]
    assert probe_runs["total"] == 2
    rows = {row["run_id"]: row for row in probe_runs["rows"]}
    assert rows["run-empty"] == {
        "run_id": "run-empty",
        "status": "empty",
        "reason": "no_cross_source_pairs",
        "eligible_seeds": 8,
        "terms": 12,
        "queries": 0,
        "planned": 0,
        "deferred": 0,
        "failures": 3,
        "saved": 0,
        "ts": "2026-01-01T00:00:01",
    }
    assert rows["legacy-skip"]["status"] == "skipped"
    assert rows["legacy-skip"]["eligible_seeds"] == 1
    for field in ("terms", "queries", "planned", "deferred", "failures", "saved"):
        assert rows["legacy-skip"][field] is None


def test_probe_run_rows_remain_bounded_and_render_terminal_reason():
    events = [
        {
            "ts": "2026-01-01T00:00:{:02d}".format(index),
            "kind": "research_checkpoint",
            "status": "completed",
            "data": {
                "run_id": "run-{:02d}".format(index),
                "phase": "probe",
            },
        }
        for index in range(SESSION_PROVENANCE_ROW_LIMIT + 2)
    ]
    terminal = {
        "ts": "2026-01-02T00:00:00",
        "kind": "research_checkpoint",
        "status": "empty",
        "data": {
            "run_id": "run-terminal",
            "phase": "probe",
            "reason": "no_cross_source_pairs",
            "eligible_seeds": 5,
            "terms": 9,
            "queries": 0,
            "queries_planned": 0,
            "queries_deferred": 0,
            "query_failed": 0,
            "download_failed": 0,
            "saved": 0,
        },
    }
    events.append(terminal)

    probe_runs = summarize_events("topic", events)["research_provenance"][
        "probe_runs"
    ]
    assert probe_runs["total"] == SESSION_PROVENANCE_ROW_LIMIT + 3
    assert probe_runs["shown"] == SESSION_PROVENANCE_ROW_LIMIT
    assert probe_runs["omitted"] == 3
    assert probe_runs["rows"][0]["run_id"] == "run-03"

    runner = CliRunner()
    with patch("filmot.ledger.read_events", return_value=[terminal]):
        result = runner.invoke(cli, ["sessions", "topic", "--summary"])

    assert result.exit_code == 0, result.output
    assert "Probe run outcomes" in result.output
    assert (
        "Probe outcome: run-terminal status=empty "
        "reason=no_cross_source_pairs"
    ) in result.output
