"""Tests for local channel corpus search and proximity parsing."""

import pytest

from filmot.channel_dl import (
    ChannelDownloader,
    _find_near_matches,
    _find_tilde_matches,
    _parse_proximity_query,
)


def _save_channel_transcript(downloader, slug, video_id, title, full_text):
    """Persist a minimal transcript payload for corpus search tests."""
    channel_dir = downloader._get_channel_dir(slug)
    downloader._save_transcript(
        channel_dir,
        video_id,
        {
            "video_id": video_id,
            "title": title,
            "published_at": "2025-01-01T00:00:00Z",
            "full_text": full_text,
        },
    )


def test_parse_near_query_with_or_group_on_left():
    parsed = _parse_proximity_query('("risk" | "drawdown") NEAR/10 "position"')
    assert parsed == ("near", ["risk", "drawdown"], ["position"], 10)


def test_parse_near_query_with_or_groups_on_both_sides():
    parsed = _parse_proximity_query('("risk" | "drawdown") NEAR/10 ("position" | "sizing")')
    assert parsed == ("near", ["risk", "drawdown"], ["position", "sizing"], 10)


def test_search_corpus_supports_or_group_on_left(tmp_path):
    downloader = ChannelDownloader(data_dir=str(tmp_path / ".filmot_data"))
    slug = "test-channel"

    _save_channel_transcript(
        downloader,
        slug,
        "vid12345678",
        "Risk and Sizing",
        "Good risk control depends on position sizing and discipline.",
    )
    _save_channel_transcript(
        downloader,
        slug,
        "vid87654321",
        "Market Structure",
        "Macro trends and liquidity matter, but sizing is discussed elsewhere.",
    )

    results = downloader.search_corpus(slug, '("risk" | "drawdown") NEAR/10 "position"')

    assert len(results) == 1
    assert results[0]["video_id"] == "vid12345678"
    assert results[0]["match_count"] >= 1


def test_search_corpus_supports_or_group_on_right(tmp_path):
    downloader = ChannelDownloader(data_dir=str(tmp_path / ".filmot_data"))
    slug = "test-channel"

    _save_channel_transcript(
        downloader,
        slug,
        "vid12345678",
        "Risk and Sizing",
        "A trader should manage risk with careful position sizing every day.",
    )

    results = downloader.search_corpus(slug, '"risk" NEAR/10 ("position" | "sizing")')

    assert len(results) == 1
    assert results[0]["video_id"] == "vid12345678"
    assert results[0]["match_count"] >= 1


def test_search_corpus_supports_or_groups_on_both_sides(tmp_path):
    downloader = ChannelDownloader(data_dir=str(tmp_path / ".filmot_data"))
    slug = "test-channel"

    _save_channel_transcript(
        downloader,
        slug,
        "vid12345678",
        "Drawdown and Sizing",
        "Limiting drawdown starts with position sizing and risk control.",
    )

    results = downloader.search_corpus(
        slug,
        '("risk" | "drawdown") NEAR/10 ("position" | "sizing")',
    )

    assert len(results) == 1
    assert results[0]["video_id"] == "vid12345678"
    assert results[0]["match_count"] >= 1


def test_parse_basic_near_query():
    parsed = _parse_proximity_query('"machine learning" NEAR/15 "neural network"')
    assert parsed == ("near", ["machine learning"], ["neural network"], 15)


def test_parse_tilde_query():
    parsed = _parse_proximity_query('"deep learning tensorflow"~5')
    assert parsed == ("tilde", ["deep", "learning", "tensorflow"], 5)


def test_near_matching_requires_word_boundaries():
    # "count" must not match inside "accountability"
    assert _find_near_matches("Her accountability and balance grew", "count", "balance", 5) == []
    assert _find_near_matches("Take count of the balance daily", "count", "balance", 5)


def test_near_distance_measured_from_phrase_edges():
    # "risk" is adjacent to the end of the phrase — must match at NEAR/1
    assert _find_near_matches("position sizing risk", "position sizing", "risk", 1)
    # ...but not when separated beyond the limit
    assert _find_near_matches("position sizing always beats reckless risk", "position sizing", "risk", 1) == []


def test_near_matches_phrases_across_newlines():
    assert _find_near_matches("position\nsizing controls risk", "position sizing", "risk", 3)


def test_tilde_repeated_word_requires_distinct_occurrences():
    assert _find_tilde_matches("we manage risk daily", ["risk", "risk"], 5) == []
    assert _find_tilde_matches("risk begets more risk", ["risk", "risk"], 5)


def test_tilde_matching_requires_word_boundaries():
    assert _find_tilde_matches("accountability matters here", ["count", "matters"], 5) == []
    assert _find_tilde_matches("the count matters here", ["count", "matters"], 5)


def test_search_corpus_rejects_unparseable_proximity_query(tmp_path):
    downloader = ChannelDownloader(data_dir=str(tmp_path / ".filmot_data"))
    slug = "test-channel"
    _save_channel_transcript(downloader, slug, "vid12345678", "T", "risk near position")

    with pytest.raises(ValueError, match="proximity operators"):
        downloader.search_corpus(slug, 'risk NEAR/10 position')  # unquoted terms


def test_search_corpus_skips_corrupted_transcript_files(tmp_path):
    downloader = ChannelDownloader(data_dir=str(tmp_path / ".filmot_data"))
    slug = "test-channel"
    _save_channel_transcript(
        downloader, slug, "vid12345678", "Good", "manage risk with position sizing"
    )
    channel_dir = downloader._get_channel_dir(slug)
    (channel_dir / "transcripts" / "corrupt.json").write_text('{"truncated', encoding="utf-8")

    results = downloader.search_corpus(slug, '"risk" NEAR/5 "sizing"')
    assert len(results) == 1


def test_save_transcript_is_atomic(tmp_path):
    downloader = ChannelDownloader(data_dir=str(tmp_path / ".filmot_data"))
    channel_dir = downloader._get_channel_dir("test-channel")
    path = downloader._save_transcript(channel_dir, "vid12345678", {"full_text": "hello"})
    assert path.exists()
    assert not list((channel_dir / "transcripts").glob("*.tmp"))
