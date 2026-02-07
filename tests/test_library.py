"""Tests for filmot.library module."""

import pytest
import json
from pathlib import Path

from filmot.library import TranscriptLibrary


# ── Topic normalization ──────────────────────────────────────────

class TestNormalizeTopic:
    """Tests for TranscriptLibrary._normalize_topic()."""

    def test_simple(self, library):
        assert library._normalize_topic("my-topic") == "my-topic"

    def test_spaces_to_hyphens(self, library):
        assert library._normalize_topic("my topic") == "my-topic"

    def test_uppercase(self, library):
        assert library._normalize_topic("My Topic") == "my-topic"

    def test_special_chars_removed(self, library):
        assert library._normalize_topic("topic!@#$%") == "topic"

    def test_underscores_to_hyphens(self, library):
        assert library._normalize_topic("my_topic_name") == "my-topic-name"

    def test_empty_returns_uncategorized(self, library):
        assert library._normalize_topic("") == "uncategorized"

    def test_only_special_chars(self, library):
        assert library._normalize_topic("!!!") == "uncategorized"

    def test_multiple_hyphens_collapsed(self, library):
        assert library._normalize_topic("a--b---c") == "a-b-c"


# ── Save / Get ───────────────────────────────────────────────────

class TestSaveAndGet:
    """Tests for save() and get()."""

    def test_save_creates_file(self, library):
        path = library.save("vid123456789", "ml", "Hello world transcript")
        assert Path(path).exists()
        with open(path, "r") as f:
            data = json.load(f)
        assert data["video_id"] == "vid123456789"
        assert data["transcript"] == "Hello world transcript"
        assert data["topic"] == "ml"

    def test_get_by_topic(self, library):
        library.save("vid123456789", "ml", "Some text")
        result = library.get("vid123456789", "ml")
        assert result is not None
        assert result["transcript"] == "Some text"

    def test_get_without_topic_searches_all(self, library):
        library.save("vid123456789", "ml", "Some text")
        result = library.get("vid123456789")
        assert result is not None

    def test_get_missing_returns_none(self, library):
        assert library.get("nonexistent") is None

    def test_save_with_metadata(self, library):
        library.save(
            "vid123456789",
            "ml",
            "text",
            metadata={"title": "Cool Video", "channel": "TestCh"},
        )
        result = library.get("vid123456789", "ml")
        assert result["metadata"]["title"] == "Cool Video"

    def test_overwrite(self, library):
        library.save("vid123456789", "ml", "first")
        library.save("vid123456789", "ml", "second")
        result = library.get("vid123456789", "ml")
        assert result["transcript"] == "second"


# ── Exists ────────────────────────────────────────────────────────

class TestExists:
    """Tests for exists()."""

    def test_exists_true(self, populated_library):
        assert populated_library.exists("abc12345678", "test-topic") is True

    def test_exists_false(self, populated_library):
        assert populated_library.exists("zzz12345678", "test-topic") is False

    def test_exists_wrong_topic(self, populated_library):
        assert populated_library.exists("abc12345678", "other-topic") is False


# ── List topics / transcripts ────────────────────────────────────

class TestListTopics:
    """Tests for list_topics()."""

    def test_lists_all_topics(self, populated_library):
        topics = populated_library.list_topics()
        names = [t["topic"] for t in topics]
        assert "test-topic" in names
        assert "other-topic" in names

    def test_counts_are_correct(self, populated_library):
        topics = populated_library.list_topics()
        counts = {t["topic"]: t["count"] for t in topics}
        assert counts["test-topic"] == 2
        assert counts["other-topic"] == 1

    def test_empty_library(self, library):
        assert library.list_topics() == []


class TestListTranscripts:
    """Tests for list_transcripts()."""

    def test_lists_transcripts_in_topic(self, populated_library):
        transcripts = populated_library.list_transcripts("test-topic")
        assert len(transcripts) == 2
        ids = {t["video_id"] for t in transcripts}
        assert ids == {"abc12345678", "def12345678"}

    def test_empty_topic(self, library):
        assert library.list_transcripts("nonexistent") == []


# ── Search ────────────────────────────────────────────────────────

class TestSearch:
    """Tests for search()."""

    def test_search_finds_match(self, populated_library):
        results = populated_library.search("machine learning")
        assert len(results) == 1
        assert results[0]["video_id"] == "abc12345678"

    def test_search_case_insensitive(self, populated_library):
        results = populated_library.search("MACHINE LEARNING")
        assert len(results) == 1

    def test_search_across_topics(self, populated_library):
        results = populated_library.search("transcript")
        # "transcript" appears in both topics' text
        assert len(results) >= 2

    def test_search_within_topic(self, populated_library):
        results = populated_library.search("transcript", topic="test-topic")
        assert all(r["topic"] == "test-topic" for r in results)

    def test_search_no_match(self, populated_library):
        results = populated_library.search("xyzzyplugh")
        assert len(results) == 0

    def test_search_returns_match_count(self, populated_library):
        results = populated_library.search("neural")
        assert results[0]["match_count"] >= 1


# ── get_context ───────────────────────────────────────────────────

class TestGetContext:
    """Tests for get_context()."""

    def test_concatenates_transcripts(self, populated_library):
        ctx = populated_library.get_context("test-topic")
        assert "machine learning" in ctx
        assert "neural networks" in ctx

    def test_max_chars(self, populated_library):
        ctx = populated_library.get_context("test-topic", max_chars=200)
        assert len(ctx) <= 200 + 50  # allow some slack for headers/truncation marker

    def test_empty_topic(self, library):
        ctx = library.get_context("nonexistent")
        assert ctx == ""


# ── Delete ────────────────────────────────────────────────────────

class TestDelete:
    """Tests for delete() and delete_topic()."""

    def test_delete_single(self, populated_library):
        assert populated_library.delete("abc12345678", "test-topic") is True
        assert populated_library.exists("abc12345678", "test-topic") is False
        # Other video still exists
        assert populated_library.exists("def12345678", "test-topic") is True

    def test_delete_not_found(self, populated_library):
        assert populated_library.delete("zzz12345678", "test-topic") is False

    def test_delete_from_all_topics(self, populated_library):
        # Save same video in two topics
        populated_library.save("abc12345678", "other-topic", "cross-posted")
        assert populated_library.delete("abc12345678") is True
        assert populated_library.get("abc12345678") is None

    def test_delete_topic(self, populated_library):
        count = populated_library.delete_topic("test-topic")
        assert count == 2
        assert populated_library.list_transcripts("test-topic") == []


# ── Stats ─────────────────────────────────────────────────────────

class TestStats:
    """Tests for stats()."""

    def test_stats_totals(self, populated_library):
        s = populated_library.stats()
        assert s["total_topics"] == 2
        assert s["total_transcripts"] == 3
        assert s["total_size_bytes"] > 0

    def test_stats_empty(self, library):
        s = library.stats()
        assert s["total_topics"] == 0
        assert s["total_transcripts"] == 0
