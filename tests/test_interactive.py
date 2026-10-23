"""The interactive REPL searches, survives failures, and prints text literally."""

import io
from unittest.mock import MagicMock, create_autospec

import pytest
from rich.console import Console

from filmot import interactive
from filmot.api import FilmotClient


VIDEO = {
    "id": "abc123def45",
    "title": "Weird [/bold] title [music]",
    "channelname": "Chan [red]nel",
    "viewcount": 1200,
    "duration": 600,
    "uploaddate": "2024-01-02",
    "hits": [{"start": 65, "ctx_before": "[inaudible] said", "token": "pandas", "ctx_after": "[/]"}],
}


@pytest.fixture
def repl(monkeypatch):
    # autospec makes the fake client reject keywords the real one lacks.
    client = create_autospec(FilmotClient, instance=True)
    client.search_subtitles.return_value = {"result": [VIDEO], "totalresultcount": 1}
    monkeypatch.setattr(interactive, "FilmotClient", lambda: client)
    monkeypatch.setattr(interactive, "get_watchlist", MagicMock)
    output = io.StringIO()
    monkeypatch.setattr(interactive, "console", Console(file=output, width=200))
    shell = interactive.FilmotREPL()
    shell.output = output
    return shell


def test_search_flags_reach_the_client_under_its_own_names(repl):
    repl.onecmd(
        'search "machine learning" --lang en --sort viewcount --order desc '
        "--exclude Music --min-duration 60 --max-duration 900 --license 2 --manual-subs"
    )

    repl.client.search_subtitles.assert_called_once_with(
        query="machine learning", lang="en", sort_field="viewcount", sort_order="desc",
        exclude_category="Music", start_duration=60, end_duration=900, license_type=2,
        search_manual_subs=1,
    )
    assert "Found 1 results" in repl.output.getvalue()


def test_a_bare_query_searches_with_the_defaults_that_are_set(repl):
    repl.onecmd("defaults min-views 1000")
    repl.onecmd("defaults sort likecount")

    repl.onecmd("pandas")

    repl.client.search_subtitles.assert_called_once_with(
        query="pandas", min_views=1000, sort_field="likecount",
    )


@pytest.mark.parametrize("line,message", [
    ("search pandas --foo bar", "Unknown option --foo"),
    ("search pandas --lang", "--lang needs a value"),
    ("search pandas --min-views lots", "--min-views needs a whole number"),
    ("search pandas --min-views -5", "--min-views must be at least 0"),
    ("search pandas --page 0", "--page must be at least 1"),
    ("search pandas --max-query-time 2", "--max-query-time must be 4 to 15000"),
    ("search pandas --sort views", "--sort must be one of: viewcount"),
    ("search pandas --sort density", "density sorting is in filmot search only"),
    ("search pandas --order up", "--order must be one of: asc, desc"),
    ("search pandas --license 3", "--license must be one of: 1, 2"),
    ("defaults hit-format 2", "--hit-format must be one of: 0, 1"),
    ("search pandas --min-views 10 --max-views 5", "minimum views cannot exceed maximum views"),
])
def test_a_bad_flag_is_explained_without_spending_a_search(repl, line, message):
    assert not repl.onecmd(line)

    repl.client.search_subtitles.assert_not_called()
    assert message in repl.output.getvalue()


def test_a_failed_search_reports_and_keeps_the_session(repl):
    repl.client.search_subtitles.side_effect = RuntimeError("upstream [/x] down")

    assert not repl.onecmd("search pandas")

    assert "Search failed: RuntimeError: upstream [/x] down" in repl.output.getvalue()


def test_results_and_matches_print_provider_text_literally(repl):
    repl.onecmd("search pandas")
    repl.onecmd("show 1")
    repl.onecmd("save 1")

    output = repl.output.getvalue()
    assert "Weird [/bold] title [music]" in output
    assert "Chan [red]nel" in output
    assert "[inaudible] said pandas [/]" in output


def test_cache_stats_is_an_alias_for_cache(repl, monkeypatch):
    cache = MagicMock()
    cache.stats.return_value = {
        "total_entries": 3, "valid_entries": 2, "expired_entries": 1,
        "size_mb": 0.01, "ttl_seconds": 3600,
    }
    monkeypatch.setattr(interactive, "get_cache", lambda: cache)

    repl.onecmd("cache stats")

    assert "Total entries: 3" in repl.output.getvalue()
    assert "Unknown cache command" not in repl.output.getvalue()


def test_missing_credentials_end_the_repl_with_a_message(monkeypatch, capsys):
    def no_credentials():
        raise ValueError("No Filmot API credentials are configured.")

    monkeypatch.setattr(interactive, "FilmotClient", no_credentials)
    output = io.StringIO()
    monkeypatch.setattr(interactive, "console", Console(file=output, width=200))

    with pytest.raises(SystemExit) as stopped:
        interactive.start_repl()

    assert stopped.value.code == 1
    assert "Configuration error: No Filmot API credentials" in output.getvalue()
