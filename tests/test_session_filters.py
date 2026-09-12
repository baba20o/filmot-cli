"""Recorded search restrictions remain visible in compact session summaries."""

import importlib
import io
import json
from unittest.mock import patch

from click.testing import CliRunner
from rich.console import Console

from filmot.cli import cli
from filmot.ledger import (
    SESSION_PROVENANCE_ROW_LIMIT,
    SESSION_SEARCH_CHANNEL_LIMIT,
    SESSION_SEARCH_FILTER_CHARS,
    summarize_events,
)
from filmot.schemas import CommandResult, ResultStatus


def search_event(**scope):
    return {
        "kind": "search",
        "status": "completed",
        "ts": "2026-09-06T12:00:00",
        "data": {
            "query": '"memory" NEAR/25 "dreaming"',
            "api_total": 20,
            "page_count": 10,
            "post_filter_count": 5,
            **scope,
        },
    }


def youtube_event(**overrides):
    data = {
        "query": "fresh battery research",
        "request": {
            "query": "fresh battery research",
            "requested_at": "2026-09-06T12:00:00Z",
            "published_after": "2026-09-01T12:00:00Z",
            "published_before": "2026-09-06T12:00:00Z",
            "days": 5,
            "order": "date",
            "max_results": 75,
            "filters": {"region": "US", "caption": "closedCaption"},
        },
        "coverage": {
            "pages_fetched": 2,
            "candidates_fetched": 75,
            "returned": 72,
            "approximate_total": 900,
            "next_page_token": "continuation-token",
            "stopping_reason": "result_limit",
        },
        "enrichment": {"status": "partial"},
        "results": 72,
    }
    data.update(overrides)
    return {
        "kind": "yt-search",
        "status": "partial",
        "ts": "2026-09-06T12:00:00",
        "data": data,
    }


def test_search_command_filters_survive_real_ledger_and_summary():
    runner = CliRunner()
    with patch("filmot.commands.search.FilmotClient") as factory:
        client = factory.return_value
        client.last_cache_hit = False
        client.last_query_rewrite = None
        client.search_subtitles.return_value = {"result": [], "totalresultcount": 0}
        for options in (["--title", "agent"], ["--channel-id", "UCexample"]):
            result = runner.invoke(cli, [
                "search", "memory", "--session", "real-scope", "--raw",
                "--lang", "en", "--min-views", "10", *options,
            ])
            assert result.exit_code == 0, result.output
            json.loads(result.stdout)

    result = runner.invoke(cli, ["sessions", "real-scope", "--summary", "--raw"])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)["summary"]["searches"]
    assert summary["events"] == 2
    scopes = [row["effective_filters"] for row in summary["scope_rows"]]
    assert scopes[0]["title"] == "agent"
    assert scopes[0]["channel_id"] is None
    assert scopes[1]["title"] is None
    assert scopes[1]["channel_id"] == "UCexample"
    assert all(scope["lang"] == "en" and scope["min_views"] == 10 for scope in scopes)


def test_identical_queries_keep_distinct_recorded_scopes_in_raw_summary(monkeypatch):
    events = [
        search_event(title=None, channel_id=None, lang="en"),
        search_event(title="agent", channel_id=None, lang="en"),
        search_event(title=None, channel_id="UCexample", lang="en"),
    ]
    monkeypatch.setattr("filmot.ledger.read_events", lambda *args, **kwargs: events)

    result = CliRunner().invoke(cli, ["sessions", "investigation", "--summary", "--raw"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["_filmot"]["status"] == "completed"
    searches = payload["summary"]["searches"]
    assert len(searches["unique_queries"]) == 1
    assert searches["events"] == 3
    scopes = [row["effective_filters"] for row in searches["scope_rows"]]
    assert scopes == [
        {"title": None, "channel_id": None, "lang": "en"},
        {"title": "agent", "channel_id": None, "lang": "en"},
        {"title": None, "channel_id": "UCexample", "lang": "en"},
    ]


def test_direct_youtube_searches_have_a_separate_bounded_universe():
    events = [search_event()]
    events.extend(
        youtube_event(query="fresh {}".format(index))
        for index in range(SESSION_PROVENANCE_ROW_LIMIT + 3)
    )

    summary = summarize_events("investigation", events)
    direct = summary["youtube_searches"]

    assert summary["searches"]["events"] == 1
    assert summary["searches"]["candidate_fetches"] == 10
    assert direct["events"] == SESSION_PROVENANCE_ROW_LIMIT + 3
    assert direct["shown"] == SESSION_PROVENANCE_ROW_LIMIT
    assert direct["omitted"] == 3
    assert direct["candidate_fetches"] == 75 * (
        SESSION_PROVENANCE_ROW_LIMIT + 3
    )
    assert direct["returned_results"] == 72 * (
        SESSION_PROVENANCE_ROW_LIMIT + 3
    )
    row = direct["scope_rows"][-1]
    assert row["published_after"] == "2026-09-01T12:00:00Z"
    assert row["published_before"] == "2026-09-06T12:00:00Z"
    assert row["pages_fetched"] == 2
    assert row["continuation_available"] is True
    assert row["enrichment_status"] == "partial"
    assert row["effective_filters"] == {
        "region": "US",
        "caption": "closedCaption",
    }
    assert "never added" in direct["counting_note"]


def test_legacy_youtube_event_is_summarized_without_inventing_bounds_or_secrets():
    event = youtube_event()
    event["data"] = {
        "query": "legacy",
        "days": 7,
        "order": "relevance",
        "max_results": 25,
        "results": 4,
        "region": "GB",
        "key": "must-not-enter-summary",
    }

    row = summarize_events("legacy", [event])["youtube_searches"][
        "scope_rows"
    ][0]

    assert row["query"] == "legacy"
    assert row["days"] == 7
    assert row["published_after"] is None
    assert row["approximate_total"] is None
    assert row["returned"] == 4
    assert row["effective_filters"] == {"region": "GB"}
    assert "must-not-enter-summary" not in json.dumps(row)


def test_legacy_rows_do_not_gain_invented_default_filters():
    row = summarize_events("legacy", [search_event()])["searches"]["scope_rows"][0]

    assert set(row) == {
        "ts", "status", "query", "api_total", "candidates_fetched",
        "post_filter_count", "partial",
    }


def test_summary_preserves_known_filters_and_typed_result_precedence():
    event = search_event(
        title="requested",
        channel_id="requested-channel",
        effective_query="memory dreaming",
        effective_filters={
            "title": "effective", "channel_id": "resolved-channel", "lang": "fr",
            "resolved_channels": [{"id": "resolved-channel", "name": "Research"}],
            "unrelated_blob": "do not project",
        },
        start_date="2025-01-01", end_date="2026-08-31",
        min_views=0, max_views=5000, min_likes=3, max_likes=50,
        min_duration=60, max_duration=3600, manual_subs=False,
        category="Education", exclude_category="Music", country="France",
        license="creativeCommon", min_matches=2, sort="uploaddate", order="desc",
        page=2, pages=3, candidate_pool=150, channel_count=5,
    )

    row = summarize_events("scope", [event])["searches"]["scope_rows"][0]

    filters = row["effective_filters"]
    assert row["effective_query"] == "memory dreaming"
    assert filters["title"] == "effective"
    assert filters["channel_id"] == "resolved-channel"
    assert filters["lang"] == "fr"
    assert filters["resolved_channels"] == [{"id": "resolved-channel", "name": "Research"}]
    for key in (
        "start_date", "end_date", "min_views", "max_views", "min_likes",
        "max_likes", "min_duration", "max_duration", "manual_subs", "category",
        "exclude_category", "country", "license", "min_matches", "sort", "order",
        "page", "pages", "candidate_pool", "channel_count",
    ):
        assert filters[key] == event["data"][key]
    assert "unrelated_blob" not in filters
    assert "effective_filters_truncated" not in row


def test_summary_bounds_filter_values_and_identifies_truncated_fields():
    long_value = "x" * (SESSION_SEARCH_FILTER_CHARS + 40)
    row = summarize_events("bounded", [search_event(
        title=long_value,
        effective_query=long_value,
        min_views=1 << 10000,
        resolved_channels=[
            {"id": "UC{}".format(index), "name": long_value, "extra": "discard"}
            for index in range(SESSION_SEARCH_CHANNEL_LIMIT + 5)
        ],
    )])["searches"]["scope_rows"][0]

    filters = row["effective_filters"]
    assert len(filters["title"]) == SESSION_SEARCH_FILTER_CHARS
    assert filters["title"].endswith("…")
    assert filters["min_views"] == "[oversized integer omitted]"
    assert len(filters["resolved_channels"]) == SESSION_SEARCH_CHANNEL_LIMIT
    assert all(
        len(channel["name"]) == SESSION_SEARCH_FILTER_CHARS and set(channel) == {"id", "name"}
        for channel in filters["resolved_channels"]
    )
    assert set(row["effective_filters_truncated"]) == {"title", "min_views", "resolved_channels"}
    assert row["effective_query_truncated"] is True
    # Compact data remains strict JSON even for malformed, oversized old values.
    json.dumps(row, allow_nan=False)


def test_human_summary_shows_filters_as_literal_text_and_reports_missing_scope(monkeypatch):
    module = importlib.import_module("filmot.commands.library")
    output = io.StringIO()
    monkeypatch.setattr(module, "console", Console(file=output, width=160, color_system=None))
    events = [
        search_event(query="[bold]query[/bold]", title="[red]agent[/red]", lang="en"),
        search_event(channel_id="UCexample", manual_subs=True),
        search_event(),
        search_event(title="x" * (SESSION_SEARCH_FILTER_CHARS + 1)),
    ]
    module._render_sessions(CommandResult(
        command="sessions", status=ResultStatus.COMPLETED,
        data={"name": "investigation", "summary": summarize_events("investigation", events)},
    ))

    text = output.getvalue()
    assert "[bold]query[/bold]" in text
    assert '1. Scope: title="[red]agent[/red]"; lang="en"' in text
    assert '2. Scope: channel_id="UCexample"; manual_subs=true' in text
    assert "3. Scope: Filters not recorded" in text
    assert "Truncated fields: title. Replay the session for full values." in text


def test_human_summary_labels_direct_youtube_scope_separately(monkeypatch):
    module = importlib.import_module("filmot.commands.library")
    output = io.StringIO()
    monkeypatch.setattr(
        module,
        "console",
        Console(file=output, width=220, color_system=None),
    )
    module._render_sessions(CommandResult(
        command="sessions",
        status=ResultStatus.COMPLETED,
        data={
            "name": "investigation",
            "summary": summarize_events(
                "investigation", [search_event(), youtube_event()]
            ),
        },
    ))

    text = output.getvalue()
    assert "Direct YouTube searches: 1 (1 unique queries)" in text
    assert "Direct YouTube search universes" in text
    assert "2026-09-01T12:00:00Z" in text
    assert "2026-09-06T12:00:00Z" in text
    assert "region=\"US\"" in text
    assert "Standalone search universes" in text


def test_filter_projection_ignores_unknown_and_non_scalar_payloads():
    row = summarize_events("legacy", [search_event(
        effective_filters=["invalid"], title={"wrong": "type"},
        min_views=float("nan"), resolved_channels=[None, "unexpected"],
        arbitrary={"transcript": "should not enter summary"},
    )])["searches"]["scope_rows"][0]

    assert row["effective_filters"] == {"resolved_channels": []}
    json.dumps(row, allow_nan=False)
