"""Tests for local channel corpus search and proximity parsing."""

from filmot.channel_dl import ChannelDownloader, _parse_proximity_query


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
