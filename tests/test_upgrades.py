"""Tests for the agent-research upgrade batch: deep links, echo detection,
freshness hint, transcript grep, and the session ledger."""

from datetime import date, timedelta

import filmot.ledger as ledger
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
