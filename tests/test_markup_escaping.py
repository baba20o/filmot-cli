"""Provider and user text prints literally (audit F05).

Rich reads "[music]" as a style tag and drops it, and a stray "[/bold]"
raises MarkupError, so captions and titles must never be parsed as markup.
"""

from unittest.mock import patch

from click.testing import CliRunner

from filmot.cli import cli
from filmot.library import TranscriptLibrary


TITLE = "Weird [/bold] title [music]"
CHANNEL = "Chan [red]nel"


def _video(*hits):
    return {
        "id": "abcdefghijk", "title": TITLE, "channelname": CHANNEL, "channelid": "UCx",
        "channelcountryname": "[i]land", "viewcount": 10, "likecount": 1, "duration": 600,
        "category": "Edu [b]", "uploaddate": "2024-01-01", "lang": "en", "hits": list(hits),
    }


def test_search_results_and_both_hit_formats_print_literally():
    response = {"result": [_video(
        {"start": 5, "ctx_before": "[inaudible] said", "token": "pandas", "ctx_after": "[/] then"},
        {"start": 9, "token": "pandas", "lines": [{"start": 9, "text": "[laughs] PANDAS [/i]"}]},
    )], "totalresultcount": 1}
    with patch("filmot.commands.search.FilmotClient") as client_type:
        client = client_type.return_value
        client.last_cache_hit = False
        client.last_query_rewrite = None
        client.search_subtitles.return_value = response
        result = CliRunner().invoke(cli, ["search", "pandas [music]"])

    assert result.exit_code == 0, result.output
    for text in (
        TITLE, CHANNEL, "[i]land", "Edu [b]", "pandas [music]",
        "[inaudible] said pandas [/] then", "[laughs] PANDAS [/i]",
    ):
        assert text in result.output


def test_library_search_compare_and_context_print_captions_literally(tmp_path, monkeypatch):
    library = TranscriptLibrary(tmp_path / ".filmot_data")
    text = "[music] the pandas [/i] arrived [applause]"
    library.save("abcdefghijk", "zoo", text, metadata={"title": TITLE, "channel": CHANNEL})
    monkeypatch.setattr("filmot.library.get_library", lambda: library)
    runner = CliRunner()

    searched = runner.invoke(cli, ["library", "search", "pandas"])
    compared = runner.invoke(cli, ["library", "compare", "pandas"])
    context = runner.invoke(cli, ["library", "context", "zoo"])

    for result in (searched, compared, context):
        assert result.exit_code == 0, result.output
    assert TITLE in searched.output and CHANNEL in searched.output
    assert "pandas [/i] arrived" in searched.output
    assert TITLE in compared.output and "pandas [/i] arrived" in compared.output
    assert text in context.output


def test_transcript_views_print_captions_literally():
    transcript = {
        "video_id": "abcdefghijk", "language": "en", "is_generated": False,
        "full_text": "[music] hello [/i] world", "duration_seconds": 4.0, "segment_count": 2,
        "segments": [
            {"start": 0.0, "duration": 2.0, "text": "[music] hello"},
            {"start": 2.0, "duration": 2.0, "text": "[/i] world"},
        ],
    }
    runner = CliRunner()
    with patch("filmot.transcript.get_transcript", return_value=transcript), patch(
        "filmot.commands.transcript.FilmotClient"
    ):
        summary = runner.invoke(cli, ["transcript", "abcdefghijk"])
        timed = runner.invoke(cli, ["transcript", "abcdefghijk", "--timestamps"])

    assert summary.exit_code == 0, summary.output
    assert "[music] hello [/i] world" in summary.output
    assert timed.exit_code == 0, timed.output
    assert "[music] hello" in timed.output and "[/i] world" in timed.output


def test_research_prints_candidate_titles_and_channels_literally(monkeypatch, library):
    from unittest.mock import Mock

    from filmot.commands import research as research_module

    title = "Alpha beta [/bold] field [music] study"
    client = Mock()
    client.search_subtitles_all.return_value = {"result": [
        {
            "id": video_id, "title": title, "channelname": CHANNEL, "duration": 600,
            "viewcount": 1000, "hits": [{"token": "alpha beta"}, {"token": "alpha beta"}],
        }
        for video_id in ("source00001", "source00002")
    ], "totalresultcount": 2}
    monkeypatch.setattr(research_module, "FilmotClient", lambda: client)
    monkeypatch.setattr("filmot.library.get_library", lambda: library)
    monkeypatch.setattr(research_module, "_backfill_metadata", lambda video_id, t, c: (t, c))
    monkeypatch.setattr("filmot.transcript.get_transcript", lambda video_id, **kwargs: {
        "video_id": video_id, "full_text": "Alpha beta observations from the field.",
        "segments": [{"text": "Alpha beta observations.", "start": 0, "duration": 3}],
        "language": "en", "duration_seconds": 3,
    })

    result = CliRunner().invoke(cli, ["research", "alpha beta", "--depth", "2", "--no-scout"])

    assert result.exit_code == 0, result.output
    assert title in result.output
    assert CHANNEL in result.output


def test_session_summary_titles_and_claims_print_literally():
    from filmot.ledger import log_event

    name = "Inv [/bold] [music]"
    log_event("search", topic=name, query="pandas", results=1, total=1)
    statement = "Pandas [/i] [music]"  # short enough not to wrap in the list
    runner = CliRunner()

    summary = runner.invoke(cli, ["sessions", name, "--summary"])
    added = runner.invoke(cli, ["claims", "add", "zoo", statement])
    claim_id = added.output.split("Created claim", 1)[-1].split()[0]
    cited = runner.invoke(cli, [
        "claims", "cite", "zoo", claim_id, "--source", "ref [/u] 7", "--relation", "supports",
        "--excerpt", "said [music] [/i]", "--note", "note [/b]",
    ])
    listed = runner.invoke(cli, ["claims", "show", "zoo"])
    shown = runner.invoke(cli, ["claims", "show", "zoo", claim_id])

    assert summary.exit_code == 0, summary.output
    assert "Session Summary: " + name in summary.output
    for result in (added, cited, listed, shown):
        assert result.exit_code == 0, result.output
    assert statement in listed.output
    for text in (statement, "ref [/u] 7", "said [music] [/i]", "Note: note [/b]"):
        assert text in shown.output


def test_watchlist_prints_titles_literally():
    from filmot.watchlist import get_watchlist

    title = "Owl [/b] [music]"
    get_watchlist().add_video({"id": "abcdefghijk", "title": title, "channelname": "C [i]"})

    result = CliRunner().invoke(cli, ["watchlist", "list"])

    assert result.exit_code == 0, result.output
    assert title in result.output and "C [i]" in result.output


def test_saving_under_a_bracketed_topic_reports_it_literally():
    transcript = {
        "video_id": "abcdefghijk", "language": "en", "full_text": "Plain words.",
        "segments": [{"start": 0.0, "duration": 2.0, "text": "Plain words."}],
        "duration_seconds": 2.0, "segment_count": 1,
    }
    runner = CliRunner()
    with patch("filmot.transcript.get_transcript", return_value=transcript), patch(
        "filmot.commands.transcript.FilmotClient"
    ) as client:
        client.return_value.get_videos.return_value = [{"title": "Plain"}]
        saved = runner.invoke(cli, ["transcript", "abcdefghijk", "--save-to", "zoo [/bold]"])
        again = runner.invoke(cli, ["transcript", "abcdefghijk", "--save-to", "zoo [/bold]"])

    assert saved.exit_code == 0, saved.output
    assert "Saved to library: zoo [/bold]/abcdefghijk" in saved.output
    assert again.exit_code == 0, again.output
    assert "Already in library: zoo [/bold]/abcdefghijk" in again.output


def test_proxy_status_prints_configured_countries_literally(tmp_path, monkeypatch):
    from filmot import proxy_pool

    sessions = tmp_path / "sessions.txt"
    sessions.write_text("gateway.test:9000:user-1:pass-1\n", encoding="utf-8")
    monkeypatch.setenv("WEBSHARE_SESSION_FILE", str(sessions))
    proxy_pool.reset_pool()
    proxy_pool.get_pool().countries = ["US", "[/bold]"]  # FILMOT_PROXY_COUNTRIES

    result = CliRunner().invoke(cli, ["proxy", "status"])

    assert result.exit_code == 0, result.output
    assert "US, [/bold]" in result.output


def test_a_failed_rotation_is_reported_literally_and_still_refreshes():
    from unittest.mock import MagicMock

    pool = MagicMock(source="webshare")
    pool.request_full_refresh.side_effect = RuntimeError("HTTP 500: [/bold] broke")
    pool.refresh.return_value = 3
    pool.available_count.return_value = 3
    pool.recently_healthy_count.return_value = 1
    with patch("filmot.commands.proxy._require_pool", return_value=pool):
        result = CliRunner().invoke(cli, ["proxy", "refresh", "--full"])

    assert "Remote IP rotation failed: HTTP 500: [/bold] broke" in result.output
    pool.refresh.assert_called_once_with(force=True)
    assert result.exit_code == 1  # the session list refreshed, the rotation did not


def test_proxy_test_prints_the_video_input_and_country_literally(tmp_path, monkeypatch):
    from filmot import proxy_pool

    sessions = tmp_path / "sessions.txt"
    sessions.write_text("gateway.test:9000:user-1:pass-1\n", encoding="utf-8")
    monkeypatch.setenv("WEBSHARE_SESSION_FILE", str(sessions))
    proxy_pool.reset_pool()
    proxy_pool.get_pool()._sessions[0].country_code = "[/dim]"  # as a provider could send
    probe = {"transport_ok": True, "segment_count": 3, "route_elapsed_seconds": 0.1}
    url = "https://youtu.be/abcdefghijk?label=[/bold]"

    with patch("filmot.transcript.probe_pool_session", return_value=probe):
        result = CliRunner().invoke(cli, ["proxy", "test", "--video-id", url, "--count", "1"])

    assert result.exit_code == 0, result.output
    assert url in result.output
    assert "([/dim])" in result.output


def test_hit_links_survive_a_bracketed_video_id():
    # Deep links embed the provider's ID; inside a [link=...] tag it was parsed.
    video = dict(_video(
        {"start": 5, "ctx_before": "before", "token": "pandas", "ctx_after": "after"},
        {"start": 9, "token": "pandas", "lines": [{"start": 9, "text": "pandas line"}]},
    ), id="abc[/bold]")
    with patch("filmot.commands.search.FilmotClient") as client_type:
        client = client_type.return_value
        client.last_cache_hit = False
        client.last_query_rewrite = None
        client.search_subtitles.return_value = {"result": [video], "totalresultcount": 1}
        result = CliRunner().invoke(cli, ["search", "pandas"])

    assert result.exit_code == 0, result.output
    assert "before pandas after" in result.output and "pandas line" in result.output


def test_link_renderers_print_bracketed_urls_literally(monkeypatch):
    import io

    from rich.console import Console

    from filmot.commands import search as search_module
    from filmot.schemas import CommandResult, ResultStatus

    output = io.StringIO()
    monkeypatch.setattr(search_module, "console", Console(file=output, width=200))
    url = "https://youtube.com/watch?v=abc[/bold]"
    search_module._render_yt_search_result(CommandResult(
        command="yt-search", status=ResultStatus.COMPLETED,
        data={"query": "q", "days": 7, "videos": [{"title": "T", "url": url}]},
    ))
    search_module._render_transcript_grep(CommandResult(
        command="transcript", status=ResultStatus.COMPLETED,
        data={"query": "q", "matches": [{"timestamp": "0:05", "deep_link": url + "&t=5", "excerpt": "said q"}]},
    ))

    assert url in output.getvalue()
    assert url + "&t=5 said q" in output.getvalue()


def test_library_locators_survive_a_bracketed_video_id(tmp_path, monkeypatch):
    library = TranscriptLibrary(tmp_path / ".filmot_data")
    video_id = "abc[x]defgh"  # a legal file name; its deep link holds the brackets
    library.save(video_id, "zoo", "the pandas arrived", segments=[
        {"start": 5.0, "duration": 2.0, "text": "the pandas arrived"},
    ])
    monkeypatch.setattr("filmot.library.get_library", lambda: library)
    runner = CliRunner()

    searched = runner.invoke(cli, ["library", "search", "pandas"])
    compared = runner.invoke(cli, ["library", "compare", "pandas"])

    for result in (searched, compared):
        assert result.exit_code == 0, result.output
        assert "pandas arrived" in result.output


def test_transcript_search_highlights_without_parsing_the_caption():
    found = {
        "video_id": "abcdefghijk", "query": "hello", "match_count": 1,
        "matches": [{"timestamp": "0:00", "start": 0.0, "context": "[music] Hello [/i] world"}],
    }
    with patch("filmot.transcript.search_in_transcript", return_value=found):
        result = CliRunner().invoke(cli, ["transcript-search", "abcdefghijk", "hello"])

    assert result.exit_code == 0, result.output
    assert "[music] Hello [/i] world" in result.output
