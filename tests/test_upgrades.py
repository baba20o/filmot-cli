"""Tests for the agent-research upgrade batch: deep links, echo detection,
freshness hint, transcript grep, and the session ledger."""

from datetime import date, timedelta

import filmot.ledger as ledger
from filmot.library import normalize_topic_name
from filmot.cli import (
    _deep_link,
    _hit_start,
    _detect_echo_clusters,
    _freshness_hint,
    _all_substring_positions,
)


# ---- deep links -----------------------------------------------------------

def test_deep_link_appends_timestamp():
    assert _deep_link("abc12345678", 312) == "https://youtube.com/watch?v=abc12345678&t=312s"
    assert _deep_link("abc12345678", 312.9).endswith("&t=312s")  # truncated to int


def test_hit_start_prefers_lines_then_start():
    assert _hit_start({"lines": [{"start": 90}], "start": 5}) == 90
    assert _hit_start({"start": 42}) == 42
    assert _hit_start({}) == 0


# ---- echo detection -------------------------------------------------------

def test_echo_detects_copied_phrasing():
    shared = "scientists discovered more than five thousand brand new species in a deep sea mining zone"
    videos = [
        {"hits": [{"ctx_before": "", "token": shared, "ctx_after": ""}]},
        {"hits": [{"ctx_before": "", "token": shared, "ctx_after": ""}]},
        {"hits": [{"ctx_before": "", "token": "a completely unrelated discussion about gardening tomatoes", "ctx_after": ""}]},
    ]
    clusters = _detect_echo_clusters(videos)
    assert 0 in clusters and 1 in clusters  # the two copies cluster together
    assert clusters[0] == clusters[1]
    assert 2 not in clusters  # the unrelated one is not flagged


def test_echo_leaves_convergent_sources_unflagged():
    # Same topic, different words → convergence, not echo
    videos = [
        {"hits": [{"ctx_before": "", "token": "the council voted to pause seabed extraction permits", "ctx_after": ""}]},
        {"hits": [{"ctx_before": "", "token": "regulators delayed approval for ocean floor mineral harvesting", "ctx_after": ""}]},
    ]
    assert _detect_echo_clusters(videos) == {}


# ---- freshness hint -------------------------------------------------------

def test_freshness_hint_fires_for_recent_start_date():
    recent = (date.today() - timedelta(days=2)).strftime("%Y-%m-%d")
    hint = _freshness_hint(recent, None, "fable 5")
    assert hint and "yt-search" in hint


def test_freshness_hint_silent_for_old_dates():
    assert _freshness_hint("2020-01-01", "2020-12-31", "history") is None


def test_freshness_hint_silent_when_no_dates():
    assert _freshness_hint(None, None, "anything") is None


# ---- substring positions (grep plain path) --------------------------------

def test_all_substring_positions():
    assert _all_substring_positions("a b a b a", "a") == [0, 4, 8]
    assert _all_substring_positions("xyz", "q") == []


# ---- session ledger -------------------------------------------------------

def test_ledger_roundtrip(tmp_path):
    d = str(tmp_path / ".filmot_data")
    ledger.log_event("search", data_dir=d, query="deep sea mining", results=12, total=266000)
    ledger.log_event("research", topic="Deep Sea Mining", data_dir=d, query="deep sea mining", saved=5)

    # topic file is normalized and readable
    research = ledger.read_events("deep-sea-mining", data_dir=d)
    assert len(research) == 1
    assert research[0]["saved"] == 5
    assert research[0]["kind"] == "research"

    sessions = ledger.list_sessions(data_dir=d)
    names = {s["name"] for s in sessions}
    assert "deep-sea-mining" in names


def test_ledger_never_raises_on_bad_dir(tmp_path):
    # Logging must be best-effort: a broken path should not raise
    bad = str(tmp_path / "file_not_dir")
    (tmp_path / "file_not_dir").write_text("x")
    ledger.log_event("search", data_dir=bad, query="q")  # should silently no-op


def test_ledger_skips_none_fields(tmp_path):
    d = str(tmp_path / ".filmot_data")
    ledger.log_event("search", data_dir=d, query="q", lang=None, results=3)
    ev = ledger.read_events(date.today().strftime("%Y-%m-%d"), data_dir=d)[0]
    assert "lang" not in ev
    assert ev["results"] == 3


def test_ledger_uses_same_unicode_slug_as_library(tmp_path):
    d = str(tmp_path / ".filmot_data")
    topics = ["人工知能", "초전도체", "искусственный интеллект", "الذكاء الاصطناعي"]

    for index, topic in enumerate(topics):
        ledger.log_event("research", topic=topic, data_dir=d, query=topic, saved=index)

    sessions = {session["name"] for session in ledger.list_sessions(data_dir=d)}
    expected = {normalize_topic_name(topic) for topic in topics}
    assert expected.issubset(sessions)
    assert len(expected) == len(topics)

    for index, topic in enumerate(topics):
        events = ledger.read_events(topic, data_dir=d)
        assert len(events) == 1
        assert events[0]["saved"] == index
        assert events[0]["topic"] == normalize_topic_name(topic)


def test_ledger_punctuation_topics_are_deterministic_and_distinct():
    assert ledger._normalize("!!!") == ledger._normalize("!!!")
    assert ledger._normalize("!!!") != ledger._normalize("???")
    assert ledger._normalize("!!!").startswith("topic-")
    assert ledger._normalize("!!!") == normalize_topic_name("!!!")


def test_ledger_reads_and_migrates_only_matching_legacy_events(tmp_path):
    d = tmp_path / ".filmot_data"
    sessions = d / "sessions"
    sessions.mkdir(parents=True)
    legacy_path = sessions / "session.jsonl"
    legacy_path.write_text(
        "\n".join([
            '{"ts":"2026-01-01T00:00:00","kind":"research","query":"人工知能","saved":1}',
            '{"ts":"2026-01-02T00:00:00","kind":"research","query":"초전도체","saved":2}',
        ]) + "\n",
        encoding="utf-8",
    )

    # Backward-compatible read does not expose the Korean event under Japanese.
    events = ledger.read_events("人工知能", data_dir=str(d))
    assert [event["saved"] for event in events] == [1]

    # The next write checkpoints the attributable old history into the new
    # Unicode-safe file and leaves unrelated legacy history untouched.
    ledger.log_event(
        "research", topic="人工知能", data_dir=str(d), query="人工知能", saved=3
    )
    assert [event["saved"] for event in ledger.read_events("人工知能", data_dir=str(d))] == [1, 3]
    assert [event["saved"] for event in ledger.read_events("초전도체", data_dir=str(d))] == [2]
    assert '"초전도체"' in legacy_path.read_text(encoding="utf-8")
    assert '"人工知能"' not in legacy_path.read_text(encoding="utf-8")


def test_ledger_migrates_research_events_from_old_uncategorized_file(tmp_path):
    d = tmp_path / ".filmot_data"
    sessions = d / "sessions"
    sessions.mkdir(parents=True)
    legacy_path = sessions / "uncategorized.jsonl"
    legacy_path.write_text(
        '{"ts":"2026-01-01T00:00:00","kind":"research","query":"人工知能","saved":1}\n',
        encoding="utf-8",
    )

    # Old research normalized through the library before reaching the ledger,
    # producing uncategorized.jsonl rather than session.jsonl.
    assert [event["saved"] for event in ledger.read_events("人工知能", data_dir=str(d))] == [1]
    ledger.log_event(
        "research", topic="人工知能", data_dir=str(d), query="人工知能", saved=2
    )

    assert [event["saved"] for event in ledger.read_events("人工知能", data_dir=str(d))] == [1, 2]
    assert not legacy_path.exists()


def test_library_compare_substring_fallback_renders_excerpt(
    library, tmp_path, monkeypatch
):
    """B17: counts and excerpts must use the same fallback match mode."""
    from click.testing import CliRunner
    from filmot.cli import cli as cli_group
    import filmot.library as library_module

    library.save(
        "abc12345678",
        "water",
        "Cooling consumed twelve liters before recycling two more liters.",
        metadata={"title": "Cooling design", "channel": "Engineering"},
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(library_module, "get_library", lambda: library)

    result = CliRunner().invoke(
        cli_group, ["library", "compare", "liter", "--topic", "water"]
    )

    assert result.exit_code == 0
    assert "2 times across 1 sources" in result.output
    assert "liters" in result.output


def test_yt_search_logs_to_ledger(tmp_path, monkeypatch):
    """yt-search must write a ledger event like search/transcript do."""
    from click.testing import CliRunner
    from filmot.cli import cli as cli_group
    import filmot.youtube_search as ys

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ys, "validate_youtube_api", lambda: None)
    monkeypatch.setattr(ys, "search_recent", lambda **kw: [
        {"video_id": "abc12345678", "title": "T", "channel_title": "C",
         "published_at": "2026-06-10T00:00:00Z", "views": 1, "duration": "PT1M"},
    ])

    result = CliRunner().invoke(cli_group, ["yt-search", "quantum", "--days", "3"])
    assert result.exit_code == 0

    events = ledger.read_events(date.today().strftime("%Y-%m-%d"), data_dir=str(tmp_path / ".filmot_data"))
    yt_events = [e for e in events if e["kind"] == "yt-search"]
    assert len(yt_events) == 1
    assert yt_events[0]["query"] == "quantum"
    assert yt_events[0]["results"] == 1
